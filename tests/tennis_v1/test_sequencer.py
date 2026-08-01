from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import ast
from pathlib import Path
import threading
import unittest
from unittest import mock

from tennis_v1.capture import (
    CaptureValidationError,
    capture_public_json,
    issue_capture_authority,
    safe_provenance,
)
from tennis_v1 import adapter_contract
from tennis_v1.entitlements import (
    ProviderGate,
    ProviderGateError,
    ProviderSessionPoll,
    QualificationReason,
)
from tennis_v1.events import (
    CapturedInput,
    DerivedDraft,
    PersistedEvent,
    SourceKind,
)
from tennis_v1.reducer import reduce_event
from tennis_v1.retention import (
    RetentionCoordinator,
    RetentionDueDeleteError,
    RetentionError,
    RetentionGlobalHalt,
)
from tennis_v1.sequencer import (
    EventRuntime,
    ProviderPersistenceAuthorizer,
    RuntimePoisoned,
    WrongOwnerThread,
    bind_provider_persistence_authorizer,
)
from tennis_v1.state import initial_state
from tennis_v1.wal import (
    DiskLowError,
    JournalDurabilityError,
    JournalReader,
    JournalValidationError,
    JournalWriter,
)
from tests.tennis_v1 import test_events as event_test_module
from tests.tennis_v1.test_events import event_with


def captured(
    authorizer: ProviderPersistenceAuthorizer,
    *,
    source_wall_ns: int | None = 10,
    source_generated_ns: int | None = 11,
    provider_sequence: str | None = "A-1",
) -> CapturedInput:
    authority = issue_capture_authority(
        session_authorizer=authorizer,
        source_kind=SourceKind.PROVIDER,
        source_id=authorizer.session_manifest.provider_id,
        source_entity_id="match-1",
        endpoint=safe_provenance("live"),
        channel=safe_provenance("scores"),
        connection_epoch=7,
        allowed_content_types=("application/json",),
        wall_clock_ns=lambda: 100,
        monotonic_clock_ns=lambda: 90,
        clock_uncertainty_ns=lambda: 3,
    )
    return capture_public_json(
        b'{"score":"15-0"}',
        authority=authority,
        content_type="application/json",
        request_id=safe_provenance("request-1"),
        event_type="provider.point",
        event_version=1,
        source_wall_ns=source_wall_ns,
        source_generated_ns=source_generated_ns,
        provider_sequence=provider_sequence,
    )


@contextmanager
def concrete_environment():
    fixture = event_test_module.SessionContractTests(
        "test_session_manifest_requires_verified_eligible_matching_inputs"
    )
    fixture.setUp()
    coordinator = None
    try:
        session_manifest = fixture.build()
        coordinator = RetentionCoordinator.acquire(
            fixture.config,
            clock_ns=lambda: session_manifest.created_wall_ns,
        )
        coordinator.recover_and_purge()
        with mock.patch.multiple(
            adapter_contract,
            __file__=fixture.builder.adapter_file,
            _ADAPTER_REGISTRY={
                (
                    "synthetic-provider",
                    "trial-v1",
                ): fixture.builder.registration
            },
        ):
            gate = ProviderGate(
                fixture.config,
                fixture.provider_manifest,
                fixture.request,
                environ={"SYNTHETIC_API_KEY": "fixture-secret"},
                clock=lambda: fixture.now,
            )
            yield fixture, coordinator, gate, session_manifest
    finally:
        if coordinator is not None:
            coordinator.close()
        fixture.tearDown()


class RuntimeFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._environment = None
        self._patchers: list[mock._patch] = []
        self._install_real_stack()
        self._close_reasons: list[tuple[bool, str]] = []
        self._attempted_sequences: list[int] = []
        self._durable_sequences: set[int] = {1}
        self._authorizer_calls: list[str] = []
        self._authorizer_fail: dict[str, BaseException] = {}
        self._coordinator_calls: list[str] = []
        self._coordinator_fail_at: int | None = None
        self._coordinator_failure: BaseException = RetentionGlobalHalt("halt")
        self._control_eligible = True
        self._clean_marks = 0
        self._writer_fail_raw: BaseException | None = None
        self._writer_fail_derived: BaseException | None = None
        self._install_real_method_seams()

    def tearDown(self) -> None:
        for patcher in reversed(self._patchers):
            patcher.stop()
        self._patchers.clear()
        if self._environment is not None:
            self._environment.__exit__(None, None, None)
            self._environment = None

    def _install_real_stack(self) -> None:
        self._environment = concrete_environment()
        (
            self.fixture,
            self.coordinator,
            self.gate,
            self.session_manifest,
        ) = self._environment.__enter__()
        self.authorizer = bind_provider_persistence_authorizer(
            gate=self.gate,
            coordinator=self.coordinator,
            session_manifest=self.session_manifest,
        )
        capability = self.coordinator.arm_before_wal(
            session_manifest=self.session_manifest,
            decision=self.authorizer.bound_decision,
            persistence_authorizer=self.authorizer,
        )
        self.writer = JournalWriter.create(
            write_capability=capability,
            session_manifest=self.session_manifest,
        )

    def reset_stack(self) -> None:
        if self._environment is not None:
            self._environment.__exit__(None, None, None)
        self._install_real_stack()
        self._close_reasons.clear()
        self._attempted_sequences.clear()
        self._durable_sequences = {1}
        self._authorizer_calls.clear()
        self._authorizer_fail.clear()
        self._coordinator_calls.clear()
        self._coordinator_fail_at = None
        self._coordinator_failure = RetentionGlobalHalt("halt")
        self._control_eligible = True
        self._clean_marks = 0
        self._writer_fail_raw = None
        self._writer_fail_derived = None

    def _patch_method(self, target, name: str, replacement) -> None:
        patcher = mock.patch.object(target, name, new=replacement)
        patcher.start()
        self._patchers.append(patcher)

    def _install_real_method_seams(self) -> None:
        original_append_raw = JournalWriter.append_raw
        original_append_derived = JournalWriter.append_derived
        original_close_clean = JournalWriter.close_clean
        original_close_halted = JournalWriter.close_halted

        def append_raw(instance, candidate):
            if instance is self.writer:
                sequence = object.__getattribute__(instance, "_next_seq")
                self._attempted_sequences.append(sequence)
                if self._writer_fail_raw is not None:
                    raise self._writer_fail_raw
            stored = original_append_raw(instance, candidate)
            if instance is self.writer:
                self._durable_sequences.add(stored.ingest_seq)
            return stored

        def append_derived(instance, raw, draft):
            if instance is self.writer:
                sequence = object.__getattribute__(instance, "_next_seq")
                self._attempted_sequences.append(sequence)
                if self._writer_fail_derived is not None:
                    raise self._writer_fail_derived
            stored = original_append_derived(instance, raw, draft)
            if instance is self.writer:
                self._durable_sequences.add(stored.ingest_seq)
            return stored

        def close_clean(instance, *, reason, **witnesses):
            stored = original_close_clean(
                instance,
                reason=reason,
                **witnesses,
            )
            if instance is self.writer:
                self._close_reasons.append((True, reason))
                self._durable_sequences.add(stored.ingest_seq)
            return stored

        def close_halted(instance, *, reason, **witnesses):
            stored = original_close_halted(
                instance,
                reason=reason,
                **witnesses,
            )
            if instance is self.writer:
                self._close_reasons.append((False, reason))
                self._durable_sequences.add(stored.ingest_seq)
            return stored

        self._patch_method(JournalWriter, "append_raw", append_raw)
        self._patch_method(JournalWriter, "append_derived", append_derived)
        self._patch_method(JournalWriter, "close_clean", close_clean)
        self._patch_method(JournalWriter, "close_halted", close_halted)

        authorizer_methods = {
            "authorize_session": "session",
            "authorize_capture": "capture",
            "authorize_ingest": "ingest",
            "authorize_raw_persistence": "raw_persist",
            "authorize_persist": "persist",
            "authorize_transform": "transform",
            "authorize_derived_persist": "derived",
            "authorize_analysis": "analysis",
            "authorize_close": "close",
            "poll_session": "poll",
        }
        for method_name, call_name in authorizer_methods.items():
            original = getattr(ProviderPersistenceAuthorizer, method_name)

            def authorizer_method(
                instance,
                *args,
                _original=original,
                _call_name=call_name,
                **kwargs,
            ):
                if instance is self.authorizer:
                    self._authorizer_calls.append(_call_name)
                    error = self._authorizer_fail.get(_call_name)
                    if error is not None:
                        raise error
                return _original(instance, *args, **kwargs)

            self._patch_method(
                ProviderPersistenceAuthorizer,
                method_name,
                authorizer_method,
            )

        original_require = RetentionCoordinator.require_provider_operation
        original_control = (
            RetentionCoordinator.require_control_halt_eligible
        )
        original_mark = RetentionCoordinator.mark_clean_terminal

        def require_provider_operation(instance):
            if instance is self.coordinator:
                self._coordinator_calls.append("coordinator")
                if self._coordinator_fail_at == len(self._coordinator_calls):
                    raise self._coordinator_failure
            return original_require(instance)

        def require_control_halt_eligible(instance, *, session_id):
            if instance is self.coordinator:
                self._coordinator_calls.append("control_eligible")
                if not self._control_eligible:
                    raise RetentionDueDeleteError("due")
            return original_control(instance, session_id=session_id)

        def mark_clean_terminal(instance, *, session_id):
            result = original_mark(instance, session_id=session_id)
            if instance is self.coordinator:
                self._coordinator_calls.append("mark_clean")
                self._clean_marks += 1
            return result

        self._patch_method(
            RetentionCoordinator,
            "require_provider_operation",
            require_provider_operation,
        )
        self._patch_method(
            RetentionCoordinator,
            "require_control_halt_eligible",
            require_control_halt_eligible,
        )
        self._patch_method(
            RetentionCoordinator,
            "mark_clean_terminal",
            mark_clean_terminal,
        )

    def runtime(self) -> EventRuntime:
        return EventRuntime(
            writer=self.writer,
            state=initial_state(self.writer.session_manifest.session_id),
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )


class DurableOrderingTests(RuntimeFixture):
    def test_raw_fsync_finishes_before_reducer_is_called(self):
        runtime = self.runtime()
        original = reduce_event

        def checked(state, raw):
            self.assertIn(raw.ingest_seq, self._durable_sequences)
            return original(state, raw)

        with mock.patch("tennis_v1.sequencer.reduce_event", side_effect=checked):
            raw = runtime.ingest(captured(self.authorizer))
        self.assertIn(raw.ingest_seq, self._durable_sequences)
        self.assertEqual(runtime.state.raw_count, 1)

    def test_write_or_fsync_failure_causes_zero_reducer_calls(self):
        runtime = self.runtime()
        self._writer_fail_raw = JournalDurabilityError("uncertain")
        with mock.patch("tennis_v1.sequencer.reduce_event") as reducer:
            with self.assertRaises(JournalDurabilityError):
                runtime.ingest(captured(self.authorizer))
        reducer.assert_not_called()

    def test_zero_byte_partial_write_and_fsync_failure_poison_runtime(self):
        for label in ("zero", "partial", "fsync"):
            with self.subTest(label=label):
                self.reset_stack()
                runtime = self.runtime()
                self._writer_fail_raw = JournalDurabilityError(label)
                with self.assertRaises(JournalDurabilityError):
                    runtime.ingest(captured(self.authorizer))
                with self.assertRaises(RuntimePoisoned):
                    runtime.poll_entitlement()
                self.assertEqual(self._close_reasons, [])

    def test_reducer_failure_after_raw_fsync_writes_sanitized_halt_when_writer_healthy(self):
        runtime = self.runtime()
        with mock.patch(
            "tennis_v1.sequencer.reduce_event",
            side_effect=RuntimeError("SECRET reducer text"),
        ):
            with self.assertRaises(RuntimeError):
                runtime.ingest(captured(self.authorizer))
        self.assertEqual(self._close_reasons, [(False, "reducer_exception")])

    def test_derived_validation_failure_halts_but_derived_durability_failure_poisons(self):
        runtime = self.runtime()
        self._writer_fail_derived = JournalValidationError("bad")
        with self.assertRaises(JournalValidationError):
            runtime.ingest(captured(self.authorizer))
        self.assertEqual(
            self._close_reasons,
            [(False, "derived_validation_failure")],
        )

        self.reset_stack()
        runtime = self.runtime()
        self._writer_fail_derived = JournalDurabilityError("uncertain")
        with self.assertRaises(JournalDurabilityError):
            runtime.ingest(captured(self.authorizer))
        self.assertEqual(self._close_reasons, [])

    def test_safe_factory_rejection_is_retryable_before_envelope_admission(self):
        runtime = self.runtime()
        with self.assertRaises(CaptureValidationError):
            capture_public_json(
                b'{"token":"secret"}',
                authority=issue_capture_authority(
                    session_authorizer=self.authorizer,
                    source_kind=SourceKind.PROVIDER,
                    source_id="provider",
                    source_entity_id="match-1",
                    endpoint=safe_provenance("live"),
                    channel=safe_provenance("scores"),
                    connection_epoch=0,
                    allowed_content_types=("application/json",),
                    wall_clock_ns=lambda: 100,
                    monotonic_clock_ns=lambda: 90,
                    clock_uncertainty_ns=lambda: 1,
                ),
                content_type="application/json",
                request_id=safe_provenance("r"),
                event_type="provider.point",
                event_version=1,
                source_wall_ns=None,
                source_generated_ns=None,
                provider_sequence=None,
            )
        runtime.ingest(captured(self.authorizer))
        self.assertEqual(runtime.state.raw_count, 1)

    def test_forged_returned_capture_is_fatal_halt_not_retryable_validation(self):
        runtime = self.runtime()
        candidate = captured(self.authorizer)
        object.__setattr__(candidate, "retention_delete_by_ns", 301)
        with self.assertRaises(CaptureValidationError):
            runtime.ingest(candidate)
        self.assertEqual(
            self._close_reasons,
            [(False, "capture_contract_violation")],
        )

    def test_durability_failure_never_reuses_uncertain_sequence(self):
        runtime = self.runtime()
        self._writer_fail_raw = JournalDurabilityError("uncertain")
        with self.assertRaises(JournalDurabilityError):
            runtime.ingest(captured(self.authorizer))
        attempted = tuple(self._attempted_sequences)
        with self.assertRaises(RuntimePoisoned):
            runtime.ingest(captured(self.authorizer))
        self.assertEqual(tuple(self._attempted_sequences), attempted)


class OwnershipAndTerminalTests(RuntimeFixture):
    def test_require_owner_returns_no_data_and_mutates_nothing(self):
        runtime = self.runtime()
        state_before = runtime.state
        trace_before = runtime.trace_sha256
        wal_path = (
            self.fixture.config.state_root
            / "sessions"
            / f"{self.session_manifest.session_id}.wal"
        )
        wal_before = wal_path.read_bytes()
        authorizer_calls_before = tuple(self._authorizer_calls)
        coordinator_calls_before = tuple(self._coordinator_calls)
        self.assertIsNone(runtime.require_owner())
        self.assertIs(runtime.state, state_before)
        self.assertEqual(runtime.trace_sha256, trace_before)
        self.assertEqual(wal_path.read_bytes(), wal_before)
        self.assertEqual(tuple(self._authorizer_calls), authorizer_calls_before)
        self.assertEqual(tuple(self._coordinator_calls), coordinator_calls_before)

    def test_require_owner_checks_thread_before_any_runtime_work(self):
        runtime = self.runtime()
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                runtime.require_owner()
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertIsInstance(errors[0], WrongOwnerThread)
        self.assertEqual(self._attempted_sequences, [])
        self.assertEqual(self._close_reasons, [])

    def test_nonowner_thread_cannot_ingest_or_close(self):
        runtime = self.runtime()
        errors: list[type[BaseException]] = []

        def worker() -> None:
            for operation in (
                lambda: runtime.ingest(captured(self.authorizer)),
                lambda: runtime.close_clean("operator_stop"),
            ):
                try:
                    operation()
                except BaseException as error:
                    errors.append(type(error))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(errors, [WrongOwnerThread, WrongOwnerThread])

    def test_clean_terminal_is_unique_fsynced_and_final(self):
        runtime = self.runtime()
        terminal = runtime.close_clean("operator_stop")
        self.assertIn(terminal.ingest_seq, self._durable_sequences)
        self.assertEqual(self._clean_marks, 1)
        with self.assertRaises(RuntimePoisoned):
            runtime.close_clean("operator_stop")

    def test_halted_terminal_never_passes_exact_replay(self):
        runtime = self.runtime()
        terminal = runtime.close_halted("operator_halt")
        self.assertEqual(terminal.event_type, "SESSION_HALT")
        self.assertEqual(self._close_reasons, [(False, "operator_halt")])
        self.assertEqual(self._clean_marks, 0)

    def test_fixed_ingress_terminal_operations_use_only_closed_reasons(self):
        for method_name, reason in (
            ("close_ingress_backpressure", "ingress_backpressure"),
            (
                "close_ingress_owner_unresponsive",
                "ingress_owner_unresponsive",
            ),
        ):
            with self.subTest(method_name=method_name):
                self.reset_stack()
                runtime = self.runtime()
                terminal = getattr(runtime, method_name)()
                self.assertEqual(terminal.event_type, "SESSION_HALT")
                self.assertEqual(self._close_reasons, [(False, reason)])
                self.assertEqual(self._clean_marks, 0)
                with self.assertRaises(TypeError):
                    getattr(runtime, method_name)("producer-controlled")
                with self.assertRaises(RuntimePoisoned):
                    runtime.require_owner()

    def test_ingress_session_end_check_is_nonwriting_and_close_rechecks(self):
        runtime = self.runtime()
        wal_path = (
            self.fixture.config.state_root
            / "sessions"
            / f"{self.session_manifest.session_id}.wal"
        )
        before = wal_path.read_bytes()
        self.assertIs(runtime.check_ingress_session_end(), False)
        self.assertEqual(wal_path.read_bytes(), before)
        self.fixture.now = self.fixture.request.session_end_utc
        self.assertIs(runtime.check_ingress_session_end(), True)
        self.assertEqual(wal_path.read_bytes(), before)
        terminal = runtime.close_ingress_session_end()
        self.assertEqual(
            __import__("json").loads(terminal.payload)["reason"],
            "session_end",
        )
        self.assertEqual(self._close_reasons, [(True, "session_end")])

    def test_ingress_session_end_close_fails_closed_when_no_longer_due(self):
        runtime = self.runtime()
        with self.assertRaises(RuntimePoisoned):
            runtime.close_ingress_session_end()
        self.assertEqual(self._close_reasons, [])
        with self.assertRaises(RuntimePoisoned):
            runtime.require_owner()


class GateAndBoundaryTests(RuntimeFixture):
    def test_provider_gate_runs_before_append_and_sets_fixed_session_delete_by(self):
        runtime = self.runtime()
        candidate = captured(self.authorizer)
        runtime.ingest(candidate)
        self.assertEqual(
            candidate.retention_delete_by_ns,
            self.writer.session_manifest.required_retention_until_ns,
        )
        self.assertEqual(
            [
                call
                for call in self._authorizer_calls
                if call in {"session", "capture", "ingest", "persist"}
            ][:4],
            ["session", "capture", "ingest", "persist"],
        )
        self.assertEqual(self._attempted_sequences[0], 2)

    def test_provider_denial_causes_zero_raw_writes_zero_reducer_calls_and_one_halt(self):
        runtime = self.runtime()
        candidate = captured(self.authorizer)
        self._authorizer_fail["ingest"] = ProviderGateError(
            QualificationReason.ACCESS_EXPIRED
        )
        with mock.patch("tennis_v1.sequencer.reduce_event") as reducer:
            with self.assertRaises(ProviderGateError):
                runtime.ingest(candidate)
        reducer.assert_not_called()
        self.assertEqual(self._attempted_sequences, [])
        self.assertEqual(self._close_reasons, [(False, "provider_gate_denied")])

    def test_expiry_on_ingest_persist_or_clean_close_forces_halted_terminal(self):
        for boundary in ("ingest", "persist", "close"):
            with self.subTest(boundary=boundary):
                self.reset_stack()
                runtime = self.runtime()
                candidate = captured(self.authorizer)
                self._authorizer_fail[boundary] = ProviderGateError(
                    QualificationReason.ACCESS_EXPIRED
                )
                if boundary == "close":
                    operation = lambda: runtime.close_clean("operator_stop")
                else:
                    operation = lambda: runtime.ingest(candidate)
                with self.assertRaises(ProviderGateError):
                    operation()
                self.assertEqual(
                    self._close_reasons,
                    [(False, "provider_gate_denied")],
                )

    def test_expiry_after_raw_before_transform_or_derived_write_forces_halt(self):
        for boundary in ("transform", "derived"):
            with self.subTest(boundary=boundary):
                self.reset_stack()
                runtime = self.runtime()
                candidate = captured(self.authorizer)
                self._authorizer_fail[boundary] = ProviderGateError(
                    QualificationReason.ACCESS_EXPIRED
                )
                with self.assertRaises(ProviderGateError):
                    runtime.ingest(candidate)
                self.assertEqual(
                    self._close_reasons,
                    [(False, "provider_gate_denied")],
                )

    def test_idle_expiry_poll_forces_halted_terminal_before_more_work(self):
        runtime = self.runtime()
        self._authorizer_fail["poll"] = ProviderGateError(
            QualificationReason.ACCESS_EXPIRED
        )
        with self.assertRaises(ProviderGateError):
            runtime.poll_entitlement()
        self.assertEqual(self._close_reasons, [(False, "provider_gate_denied")])

    def test_runtime_uses_writer_manifest_as_single_session_authority(self):
        wrong = initial_state("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        with self.assertRaises((TypeError, ValueError)):
            EventRuntime(
                writer=self.writer,
                state=wrong,
                persistence_authorizer=self.authorizer,
                coordinator=self.coordinator,
            )
        progressed = replace(
            initial_state(self.writer.session_manifest.session_id),
            last_applied_raw_seq=2,
            raw_count=1,
            derived_count=1,
            source_epochs=((SourceKind.PROVIDER, "provider", 0),),
        )
        with self.assertRaises((TypeError, ValueError)):
            EventRuntime(
                writer=self.writer,
                state=progressed,
                persistence_authorizer=self.authorizer,
                coordinator=self.coordinator,
            )

    def test_provider_authorizer_maps_every_runtime_boundary_to_exact_gate_method(self):
        runtime = self.runtime()
        runtime.ingest(captured(self.authorizer))
        runtime.poll_entitlement()
        runtime.close_clean("operator_stop")
        for name in (
            "session",
            "ingest",
            "persist",
            "transform",
            "derived",
            "poll",
            "close",
        ):
            self.assertIn(name, self._authorizer_calls)

    def test_authorizer_rejects_gate_for_different_session_provider_or_request(self):
        with concrete_environment() as (
            _,
            coordinator,
            gate,
            session_manifest,
        ):
            for changed in (
                replace(session_manifest, provider_id="different-provider"),
                replace(
                    session_manifest,
                    research_request_sha256="0" * 64,
                ),
                replace(
                    session_manifest,
                    session_end_ns=session_manifest.session_end_ns + 1,
                ),
            ):
                with self.subTest(changed=changed):
                    with self.assertRaises(ValueError):
                        bind_provider_persistence_authorizer(
                            gate=gate,
                            coordinator=coordinator,
                            session_manifest=changed,
                        )

    def test_runtime_rejects_coordinator_different_from_authorizer_and_writer_capability(self):
        with self.assertRaises((TypeError, ValueError)):
            EventRuntime(
                writer=self.writer,
                state=initial_state(self.writer.session_manifest.session_id),
                persistence_authorizer=self.authorizer,
                coordinator=object(),
            )

    def test_direct_provider_authorizer_construction_is_unavailable(self):
        with self.assertRaises(TypeError):
            ProviderPersistenceAuthorizer()

    def test_every_gate_decision_is_rebound_to_complete_session_manifest(self):
        with concrete_environment() as (
            _,
            coordinator,
            gate,
            session_manifest,
        ):
            authorizer = bind_provider_persistence_authorizer(
                gate=gate,
                coordinator=coordinator,
                session_manifest=session_manifest,
            )
            forged = replace(
                authorizer.bound_decision,
                request_sha256="0" * 64,
            )
            candidate = captured(authorizer)
            raw = event_with(
                session_id=session_manifest.session_id,
                source_id=session_manifest.provider_id,
                retention_delete_by_ns=(
                    session_manifest.required_retention_until_ns
                ),
            )
            draft = DerivedDraft(
                "raw_accepted",
                1,
                "canonical-json-v1",
                b"{}",
            )
            for method_name, invocation in (
                ("require_start", authorizer.authorize_session),
                (
                    "require_ingest",
                    lambda: authorizer.authorize_ingest(candidate),
                ),
                (
                    "require_transform",
                    lambda: authorizer.authorize_transform(raw),
                ),
                (
                    "require_derived_persist",
                    lambda: authorizer.authorize_derived_persist(
                        raw,
                        draft,
                    ),
                ),
                ("require_analysis", authorizer.authorize_analysis),
                ("require_close", authorizer.authorize_close),
            ):
                with self.subTest(method=method_name), mock.patch.object(
                    ProviderGate,
                    method_name,
                    return_value=forged,
                ):
                    with self.assertRaises(ValueError):
                        invocation()
            with mock.patch.object(
                ProviderGate,
                "poll_session",
                return_value=ProviderSessionPoll(forged, False),
            ):
                with self.assertRaises(ValueError):
                    authorizer.poll_session()

    def test_capture_and_persist_return_same_homogeneous_session_deadline(self):
        candidate = captured(self.authorizer)
        self.assertEqual(
            self.authorizer.authorize_persist(candidate),
            candidate.retention_delete_by_ns,
        )

    def test_require_raw_persist_is_called_with_no_observation_argument(self):
        runtime = self.runtime()
        runtime.ingest(captured(self.authorizer))
        self.assertIn("persist", self._authorizer_calls)


class HaltBoundaryTests(RuntimeFixture):
    def test_global_halt_checked_at_capture_append_transform_each_derived_close_and_idle(self):
        runtime = self.runtime()
        runtime.ingest(captured(self.authorizer))
        runtime.poll_entitlement()
        runtime.close_clean("operator_stop")
        self.assertGreaterEqual(self._coordinator_calls.count("coordinator"), 7)

    def test_coordinator_halt_at_every_boundary_writes_one_witnessed_halt_if_writer_healthy(self):
        for boundary in (1, 2, 3, 4, 5):
            with self.subTest(boundary=boundary):
                self.reset_stack()
                runtime = self.runtime()
                candidate = captured(self.authorizer)
                self._coordinator_fail_at = (
                    len(self._coordinator_calls) + boundary
                )
                with self.assertRaises(RetentionGlobalHalt):
                    runtime.ingest(candidate)
                self.assertLessEqual(len(self._close_reasons), 1)
                if self._close_reasons:
                    self.assertEqual(
                        self._close_reasons,
                        [(False, "retention_global_halt")],
                    )

    def test_current_session_due_or_ambiguous_halt_leaves_unclean_without_further_byte(self):
        runtime = self.runtime()
        candidate = captured(self.authorizer)
        self._coordinator_fail_at = len(self._coordinator_calls) + 1
        self._control_eligible = False
        with self.assertRaises(RetentionGlobalHalt):
            runtime.ingest(candidate)
        self.assertEqual(self._close_reasons, [])

    def test_healthy_reducer_trace_or_derived_validation_failure_writes_one_halt(self):
        cases = (
            ("reducer_exception", "reduce_event"),
            ("trace_exception", "next_trace"),
        )
        for reason, target in cases:
            with self.subTest(reason=reason):
                self.reset_stack()
                runtime = self.runtime()
                with mock.patch(
                    f"tennis_v1.sequencer.{target}",
                    side_effect=RuntimeError("SECRET"),
                ):
                    with self.assertRaises(RuntimeError):
                        runtime.ingest(captured(self.authorizer))
                self.assertEqual(self._close_reasons, [(False, reason)])

    def test_disk_low_prewrite_check_halts_before_raw_or_reducer(self):
        runtime = self.runtime()
        candidate = captured(self.authorizer)
        self._writer_fail_raw = DiskLowError("disk")
        with mock.patch("tennis_v1.sequencer.reduce_event") as reducer:
            with self.assertRaises(DiskLowError):
                runtime.ingest(candidate)
        reducer.assert_not_called()
        self.assertEqual(self._close_reasons, [(False, "disk_low")])


class RealRuntimeIntegrationTests(unittest.TestCase):
    def test_runtime_rejects_wrappers_noop_claim_and_multiple_runtime_bypass(self):
        with concrete_environment() as (
            _,
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

            class NoopClaimWriter:
                session_manifest = writer.session_manifest
                session_start = writer.session_start
                poisoned = False

                def claim_runtime(self, **_):
                    return None

            class CoordinatorWrapper:
                def require_provider_operation(self):
                    return coordinator.require_provider_operation()

            class AuthorizerWrapper:
                def __init__(self):
                    self.session_manifest = authorizer.session_manifest
                    self.coordinator = coordinator
                    self.bound_decision = authorizer.bound_decision

                def __getattr__(self, name):
                    return getattr(authorizer, name)

            state = initial_state(session_manifest.session_id)
            for values in (
                {
                    "writer": NoopClaimWriter(),
                    "persistence_authorizer": authorizer,
                    "coordinator": coordinator,
                },
                {
                    "writer": writer,
                    "persistence_authorizer": AuthorizerWrapper(),
                    "coordinator": coordinator,
                },
                {
                    "writer": writer,
                    "persistence_authorizer": authorizer,
                    "coordinator": CoordinatorWrapper(),
                },
            ):
                with self.subTest(values=tuple(values)):
                    with self.assertRaises(TypeError):
                        EventRuntime(state=state, **values)
            with self.assertRaises(TypeError):
                EventRuntime(
                    writer=NoopClaimWriter(),
                    state=state,
                    persistence_authorizer=authorizer,
                    coordinator=coordinator,
                )
            with self.assertRaises(TypeError):
                EventRuntime(
                    writer=NoopClaimWriter(),
                    state=state,
                    persistence_authorizer=authorizer,
                    coordinator=coordinator,
                )

    def test_capture_rejects_distinct_concrete_authorizer_same_manifest(self):
        with concrete_environment() as (
            fixture,
            coordinator,
            gate,
            session_manifest,
        ):
            first = bind_provider_persistence_authorizer(
                gate=gate,
                coordinator=coordinator,
                session_manifest=session_manifest,
            )
            second_gate = ProviderGate(
                fixture.config,
                fixture.provider_manifest,
                fixture.request,
                environ={"SYNTHETIC_API_KEY": "fixture-secret"},
                clock=lambda: fixture.now,
            )
            second = bind_provider_persistence_authorizer(
                gate=second_gate,
                coordinator=coordinator,
                session_manifest=session_manifest,
            )
            authority = issue_capture_authority(
                session_authorizer=first,
                source_kind=SourceKind.PROVIDER,
                source_id=session_manifest.provider_id,
                source_entity_id="match-1",
                endpoint=safe_provenance("live"),
                channel=safe_provenance("scores"),
                connection_epoch=0,
                allowed_content_types=("application/json",),
                wall_clock_ns=lambda: session_manifest.created_wall_ns,
                monotonic_clock_ns=lambda: 1,
                clock_uncertainty_ns=lambda: 0,
            )
            candidate = capture_public_json(
                b'{"score":"15-0"}',
                authority=authority,
                content_type="application/json",
                request_id=safe_provenance("request-1"),
                event_type="provider.point",
                event_version=1,
                source_wall_ns=None,
                source_generated_ns=None,
                provider_sequence="A-1",
            )
            with self.assertRaisesRegex(
                CaptureValidationError,
                "capture_authorizer_identity_invalid",
            ):
                second.authorize_capture(authority, candidate)

    def test_exact_session_end_poll_durably_clean_closes_real_stack(self):
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
            fixture.now = fixture.request.session_end_utc
            terminal = runtime.poll_entitlement()
            self.assertIsInstance(terminal, PersistedEvent)
            self.assertFalse(writer.poisoned)
            read_capability = coordinator.issue_read_capability(
                persistence_authorizer=authorizer
            )
            with JournalReader.open(read_capability=read_capability) as reader:
                records = tuple(reader.iter_records())
                summary = reader.scan(require_clean=True)
            self.assertTrue(summary.terminal_clean)
            self.assertEqual(
                __import__("json").loads(records[-1].payload)["reason"],
                "session_end",
            )

    def test_retention_close_reauthorization_failure_never_writes_false_clean(self):
        with concrete_environment() as (
            _,
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
            with mock.patch.object(
                ProviderPersistenceAuthorizer,
                "authorize_close",
                autospec=True,
                side_effect=(
                    None,
                    ProviderGateError(QualificationReason.ACCESS_EXPIRED),
                ),
            ):
                with self.assertRaises(JournalDurabilityError):
                    runtime.close_clean("operator_stop")
            self.assertTrue(writer.poisoned)
            with self.assertRaisesRegex(
                RetentionError,
                "retention_wal_not_replay_ready",
            ):
                coordinator.issue_read_capability(
                    persistence_authorizer=authorizer
                )

    def test_private_authorizer_and_retention_claim_dependencies_are_narrow(self):
        package = Path(__file__).resolve().parents[2] / "tennis_v1"
        private_authorizer_names = {
            "_AUTHORIZER_SENTINEL",
            "_build_provider_persistence_authorizer",
        }
        retention_helpers = {
            "_claim_provider_wal_runtime",
            "_ack_provider_wal_clean_terminal",
            "_claim_provider_wal_reader",
        }
        replay_retention_helpers = {
            "_reject_expected_replay_manifest",
            "_reject_replay_manifest",
        }
        for path in package.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
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
            observed = names | attrs
            if path.name != "sequencer.py":
                self.assertFalse(
                    observed & private_authorizer_names,
                    path.name,
                )
            if observed & retention_helpers:
                self.assertIn(path.name, {"retention.py", "wal.py"})
            if observed & replay_retention_helpers:
                self.assertIn(
                    path.name,
                    {"retention.py", "replay_core.py"},
                )
            if "claim_runtime" in attrs:
                self.assertEqual(path.name, "sequencer.py")
        source = (package / "sequencer.py").read_text(encoding="utf-8")
        replay_source = (package / "replay_core.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_reject_expected_replay_manifest", replay_source)
        retention_tree = ast.parse(
            (package / "retention.py").read_text(encoding="utf-8")
        )
        expected_rejection_helpers = [
            node
            for node in retention_tree.body
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_reject_expected_replay_manifest"
            )
        ]
        self.assertEqual(len(expected_rejection_helpers), 1)
        helper = expected_rejection_helpers[0]
        self.assertEqual(helper.args.args, [])
        self.assertEqual(
            [item.arg for item in helper.args.kwonlyargs],
            [
                "expected_session_manifest_sha256",
                "persistence_authorizer",
                "coordinator",
            ],
        )
        self.assertNotIn("session_id", replay_source.split(
            "_reject_expected_replay_manifest(", 1
        )[1].split(")", 1)[0])
        self.assertNotIn("reason", replay_source.split(
            "_reject_expected_replay_manifest(", 1
        )[1].split(")", 1)[0])
        for forbidden in (
            "requests",
            "httpx",
            "kalshi_client",
            "market_data",
            "executor",
            "create_order",
            "cancel_order",
        ):
            self.assertNotIn(forbidden, source)

    def test_concrete_authorizer_runtime_claim_and_post_close_clean_ack(self):
        fixture = event_test_module.SessionContractTests(
            "test_session_manifest_requires_verified_eligible_matching_inputs"
        )
        fixture.setUp()
        coordinator = None
        try:
            session_manifest = fixture.build()
            coordinator = RetentionCoordinator.acquire(
                fixture.config,
                clock_ns=lambda: session_manifest.created_wall_ns,
            )
            coordinator.recover_and_purge()
            with mock.patch.multiple(
                adapter_contract,
                __file__=fixture.builder.adapter_file,
                _ADAPTER_REGISTRY={
                    (
                        "synthetic-provider",
                        "trial-v1",
                    ): fixture.builder.registration
                },
            ):
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
                with self.assertRaises(JournalValidationError):
                    writer.claim_runtime(
                        persistence_authorizer=authorizer,
                        coordinator=coordinator,
                    )
                authority = issue_capture_authority(
                    session_authorizer=authorizer,
                    source_kind=SourceKind.PROVIDER,
                    source_id=session_manifest.provider_id,
                    source_entity_id="match-1",
                    endpoint=safe_provenance("live"),
                    channel=safe_provenance("scores"),
                    connection_epoch=0,
                    allowed_content_types=("application/json",),
                    wall_clock_ns=lambda: session_manifest.created_wall_ns,
                    monotonic_clock_ns=lambda: 1,
                    clock_uncertainty_ns=lambda: 0,
                )
                candidate = capture_public_json(
                    b'{"score":"15-0"}',
                    authority=authority,
                    content_type="application/json",
                    request_id=safe_provenance("request-1"),
                    event_type="provider.point",
                    event_version=1,
                    source_wall_ns=None,
                    source_generated_ns=None,
                    provider_sequence="A-1",
                )
                runtime.ingest(candidate)
                runtime.close_clean("operator_stop")
                read_capability = coordinator.issue_read_capability(
                    persistence_authorizer=authorizer
                )
                with JournalReader.open(
                    read_capability=read_capability
                ) as reader:
                    summary = reader.scan(require_clean=True)
                self.assertTrue(summary.terminal_clean)
        finally:
            if coordinator is not None:
                coordinator.close()
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
