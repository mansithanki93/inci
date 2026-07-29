from __future__ import annotations

from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import timedelta
import ast
import gc
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tracemalloc
import unittest
from unittest import mock

from tennis_v1 import adapter_contract
from tennis_v1.canonical import canonical_json_bytes
from tennis_v1.config import canonical_config_sha256
from tennis_v1.entitlements import (
    PermissionBasis,
    PermissionOperation,
    ProviderGate,
    ProviderGateError,
    QualificationReason,
    evaluate_provider,
)
from tennis_v1.events import PersistedEvent, RecordKind
from tennis_v1.reducer import initial_trace
from tennis_v1.replay_core import (
    ReplayMismatch,
    ReplayResult,
    _compare_derived_sequences,
    _derived_signature,
    replay_exact,
    scan_diagnostic_prefix,
)
from tennis_v1.retention import (
    ProviderWalReadCapability,
    RetentionCoordinator,
    RetentionError,
    RetentionGlobalHalt,
)
from tennis_v1.sequencer import (
    EventRuntime,
    ProviderPersistenceAuthorizer,
    bind_provider_persistence_authorizer,
)
from tennis_v1.session import build_session_manifest, session_manifest_sha256
from tennis_v1.state import canonical_state_bytes, initial_state
from tennis_v1.wal import (
    FILE_FLAGS,
    FILE_MAGIC,
    FILE_PREFIX,
    FILE_VERSION,
    FRAME_TRAILER,
    JournalCorruptionError,
    JournalReader,
    JournalWriter,
    ScanIssue,
    ScanSummary,
    _encode_frame,
)
from tests.tennis_v1.test_sequencer import captured, concrete_environment
from tests.tennis_v1 import test_events as event_test_module
from tests.tennis_v1.test_entitlements import utc


PYTHON = "/private/tmp/inci-tennis-v1.uDa8Ve/venv/bin/python"


@contextmanager
def replay_session(
    *,
    raw_count: int = 1,
    clean: bool = True,
    halt_reason: str = "operator_halt",
):
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
        for _ in range(raw_count):
            runtime.ingest(captured(authorizer))
        if clean:
            runtime.close_clean("operator_stop")
        elif halt_reason == "operator_halt":
            runtime.close_halted(halt_reason)
        else:
            method_name = {
                "ingress_backpressure": "close_ingress_backpressure",
                "ingress_owner_unresponsive": (
                    "close_ingress_owner_unresponsive"
                ),
            }[halt_reason]
            getattr(runtime, method_name)()
        yield fixture, coordinator, authorizer, writer, runtime


def expected_digest(authorizer: ProviderPersistenceAuthorizer) -> str:
    return session_manifest_sha256(authorizer.session_manifest)


def replay(
    coordinator: RetentionCoordinator,
    authorizer: ProviderPersistenceAuthorizer,
) -> ReplayResult:
    return replay_exact(
        expected_session_manifest_sha256=expected_digest(authorizer),
        persistence_authorizer=authorizer,
        coordinator=coordinator,
    )


def records_for(
    coordinator: RetentionCoordinator,
    authorizer: ProviderPersistenceAuthorizer,
) -> tuple[PersistedEvent, ...]:
    capability = coordinator.issue_read_capability(
        persistence_authorizer=authorizer
    )
    with JournalReader.open(read_capability=capability) as reader:
        return tuple(reader.iter_records(diagnostic_prefix=True))


def wal_path(fixture, authorizer: ProviderPersistenceAuthorizer) -> Path:
    return (
        fixture.config.state_root
        / "sessions"
        / f"{authorizer.session_manifest.session_id}.wal"
    )


def rewrite_records(path: Path, records: tuple[PersistedEvent, ...]) -> None:
    prefix = FILE_PREFIX.pack(
        FILE_MAGIC,
        FILE_VERSION,
        FILE_FLAGS,
        FILE_PREFIX.size,
    )
    path.write_bytes(prefix + b"".join(_encode_frame(item) for item in records))


def changed_terminal(
    terminal: PersistedEvent,
    **changes: object,
) -> PersistedEvent:
    payload = json.loads(terminal.payload)
    payload.update(changes)
    encoded = canonical_json_bytes(payload)
    return replace(
        terminal,
        payload=encoded,
        payload_sha256=hashlib.sha256(encoded).hexdigest(),
    )


class ReplayCoreTests(unittest.TestCase):
    def test_replay_uses_raw_records_only_and_matches_every_derived_witness(self):
        with replay_session(raw_count=2) as (
            _,
            coordinator,
            authorizer,
            _,
            runtime,
        ):
            expected_state = runtime.state
            with mock.patch.object(
                JournalWriter,
                "append_derived",
                side_effect=AssertionError("writer helper used by replay"),
            ):
                result = replay(coordinator, authorizer)
            self.assertEqual(result.state, expected_state)
            self.assertEqual(result.raw_count, 2)
            self.assertEqual(result.derived_count, 2)
            self.assertTrue(result.wal_valid)
            self.assertTrue(result.exact_replay)
            self.assertIsNone(result.scan_issue)
            self.assertIsNone(result.replay_mismatch)

    def test_replay_twice_produces_identical_state_outputs_and_trace(self):
        with replay_session(raw_count=3) as (
            _,
            coordinator,
            authorizer,
            _,
            _,
        ):
            first = replay(coordinator, authorizer)
            second = replay(coordinator, authorizer)
            self.assertEqual(first, second)
            self.assertIsNotNone(first.state)
            self.assertIsNotNone(first.trace_sha256)

    def test_mutated_derived_payload_fails_at_parent_sequence(self):
        with replay_session() as (
            fixture,
            coordinator,
            authorizer,
            _,
            _,
        ):
            records = records_for(coordinator, authorizer)
            derived = records[2]
            payload = canonical_json_bytes(
                {
                    "input_event_type": "forged",
                    "input_payload_sha256": "0" * 64,
                    "parent_ingest_seq": derived.parent_ingest_seq,
                    "source_id": derived.source_id,
                }
            )
            forged = replace(
                derived,
                payload=payload,
                payload_sha256=hashlib.sha256(payload).hexdigest(),
            )
            rewrite_records(
                wal_path(fixture, authorizer),
                (records[0], records[1], forged, records[3]),
            )
            result = replay(coordinator, authorizer)
            self.assertEqual(
                result.replay_mismatch,
                ReplayMismatch.DERIVED_RECORD,
            )
            self.assertTrue(result.wal_valid)
            self.assertFalse(result.exact_replay)

    def test_pure_multi_output_comparator_types_missing_extra_order_and_record(self):
        with replay_session() as (
            _,
            coordinator,
            authorizer,
            _,
            _,
        ):
            records = records_for(coordinator, authorizer)
            one = records[2]
            payload = canonical_json_bytes({"secondary": True})
            two = replace(
                one,
                ingest_seq=one.ingest_seq + 1,
                event_type="raw_secondary",
                payload=payload,
                payload_sha256=hashlib.sha256(payload).hexdigest(),
            )
            permuted = (
                replace(two, ingest_seq=one.ingest_seq),
                replace(one, ingest_seq=two.ingest_seq),
            )
            changed = replace(
                one,
                event_type="raw_changed",
            )
            self.assertEqual(
                _compare_derived_sequences((one, two), (one,)),
                ReplayMismatch.DERIVED_MISSING,
            )
            self.assertEqual(
                _compare_derived_sequences((one,), (one, two)),
                ReplayMismatch.DERIVED_EXTRA,
            )
            self.assertEqual(
                _compare_derived_sequences((one, two), permuted),
                ReplayMismatch.DERIVED_ORDER,
            )
            self.assertEqual(
                _compare_derived_sequences((one,), (changed,)),
                ReplayMismatch.DERIVED_RECORD,
            )
            self.assertIsNone(
                _compare_derived_sequences((one, two), (one, two))
            )

    def test_terminal_witness_mismatches_are_typed_independently(self):
        cases = (
            (
                {"raw_count": 99},
                ReplayMismatch.TERMINAL_COUNTS,
            ),
            (
                {"config_file_sha256": "0" * 64},
                ReplayMismatch.TERMINAL_PROVENANCE,
            ),
            (
                {"final_state_sha256": "0" * 64},
                ReplayMismatch.STATE,
            ),
            (
                {"trace_sha256": "0" * 64},
                ReplayMismatch.TRACE,
            ),
            (
                {"reason": "canonical_but_not_clean"},
                ReplayMismatch.TERMINAL_REASON,
            ),
        )
        for changes, expected in cases:
            with self.subTest(expected=expected), replay_session() as (
                fixture,
                coordinator,
                authorizer,
                _,
                _,
            ):
                records = records_for(coordinator, authorizer)
                terminal = changed_terminal(records[-1], **changes)
                rewrite_records(
                    wal_path(fixture, authorizer),
                    records[:-1] + (terminal,),
                )
                result = replay(coordinator, authorizer)
                self.assertEqual(result.replay_mismatch, expected)
                self.assertTrue(result.wal_valid)
                self.assertFalse(result.exact_replay)

    def test_halted_missing_and_torn_terminal_never_pass_exact_replay(self):
        with replay_session(clean=False) as (
            _,
            coordinator,
            authorizer,
            _,
            _,
        ):
            halted = replay(coordinator, authorizer)
            self.assertEqual(halted.scan_issue, ScanIssue.HALTED_TERMINAL)
            self.assertEqual(
                halted.replay_mismatch,
                ReplayMismatch.TERMINAL_REASON,
            )
            self.assertFalse(halted.exact_replay)

        for reason in (
            "ingress_backpressure",
            "ingress_owner_unresponsive",
        ):
            with self.subTest(reason=reason), replay_session(
                clean=False,
                halt_reason=reason,
            ) as (
                _,
                coordinator,
                authorizer,
                _,
                _,
            ):
                halted = replay(coordinator, authorizer)
                self.assertEqual(
                    halted.scan_issue,
                    ScanIssue.HALTED_TERMINAL,
                )
                self.assertEqual(
                    halted.replay_mismatch,
                    ReplayMismatch.TERMINAL_REASON,
                )
                self.assertFalse(halted.exact_replay)
                self.assertIs(halted.research_evaluable, False)

        with replay_session() as (
            fixture,
            coordinator,
            authorizer,
            _,
            _,
        ):
            records = records_for(coordinator, authorizer)
            rewrite_records(wal_path(fixture, authorizer), records[:-1])
            missing = replay(coordinator, authorizer)
            self.assertEqual(missing.scan_issue, ScanIssue.MISSING_TERMINAL)
            self.assertFalse(missing.exact_replay)

            content = wal_path(fixture, authorizer).read_bytes()
            wal_path(fixture, authorizer).write_bytes(content + b"EVT")
            torn = replay(coordinator, authorizer)
            self.assertEqual(torn.scan_issue, ScanIssue.TORN_TAIL)
            self.assertFalse(torn.wal_valid)
            self.assertFalse(torn.exact_replay)

    def test_raw_reduction_failure_is_typed_and_does_not_fabricate_state(self):
        with replay_session(raw_count=2) as (
            fixture,
            coordinator,
            authorizer,
            _,
            _,
        ):
            records = records_for(coordinator, authorizer)
            second_raw = records[3]
            regressed = replace(
                second_raw,
                connection_epoch=second_raw.connection_epoch - 1,
            )
            rewrite_records(
                wal_path(fixture, authorizer),
                records[:3] + (regressed,) + records[4:],
            )
            result = replay(coordinator, authorizer)
            self.assertEqual(
                result.replay_mismatch,
                ReplayMismatch.RAW_REDUCTION,
            )
            self.assertIsNone(result.state)
            self.assertIsNone(result.trace_sha256)

    def test_raw_fsync_prefix_reports_tail_and_semantic_facts_together(self):
        with replay_session() as (
            fixture,
            coordinator,
            authorizer,
            _,
            _,
        ):
            records = records_for(coordinator, authorizer)
            rewrite_records(
                wal_path(fixture, authorizer),
                (records[0], records[1]),
            )
            result = scan_diagnostic_prefix(
                expected_session_manifest_sha256=expected_digest(authorizer),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
            self.assertEqual(result.scan_issue, ScanIssue.MISSING_TERMINAL)
            self.assertEqual(
                result.replay_mismatch,
                ReplayMismatch.DERIVED_MISSING,
            )
            self.assertIsNotNone(result.state)
            self.assertEqual(result.state.raw_count, 1)
            self.assertFalse(result.exact_replay)

    def test_header_only_wal_never_fabricates_state_or_trace(self):
        with replay_session() as (
            fixture,
            coordinator,
            authorizer,
            _,
            _,
        ):
            prefix = FILE_PREFIX.pack(
                FILE_MAGIC,
                FILE_VERSION,
                FILE_FLAGS,
                FILE_PREFIX.size,
            )
            wal_path(fixture, authorizer).write_bytes(prefix)
            result = scan_diagnostic_prefix(
                expected_session_manifest_sha256=expected_digest(authorizer),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
            self.assertIsNone(result.state)
            self.assertIsNone(result.trace_sha256)
            self.assertEqual(result.scan_issue, ScanIssue.MISSING_TERMINAL)
            self.assertFalse(result.exact_replay)

    def test_corrupt_terminal_tail_is_typed_and_never_exact(self):
        with replay_session() as (
            fixture,
            coordinator,
            authorizer,
            _,
            _,
        ):
            path = wal_path(fixture, authorizer)
            corrupted = bytearray(path.read_bytes())
            corrupted[-FRAME_TRAILER.size - 1] ^= 1
            path.write_bytes(bytes(corrupted))
            result = scan_diagnostic_prefix(
                expected_session_manifest_sha256=expected_digest(authorizer),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
            self.assertIs(result.scan_issue, ScanIssue.CORRUPT_TAIL)
            self.assertFalse(result.wal_valid)
            self.assertFalse(result.exact_replay)

    def test_analysis_denial_opens_and_reads_zero_wal_bytes(self):
        with replay_session() as (
            _,
            coordinator,
            authorizer,
            _,
            _,
        ):
            with mock.patch.object(
                ProviderPersistenceAuthorizer,
                "authorize_analysis",
                autospec=True,
                side_effect=ProviderGateError(
                    QualificationReason.ANALYSIS_EXPIRED
                ),
            ), mock.patch.object(
                RetentionCoordinator,
                "issue_read_capability",
                side_effect=AssertionError("read capability issued"),
            ):
                with self.assertRaises(ProviderGateError):
                    replay(coordinator, authorizer)

    def test_real_written_permission_controls_post_access_expiry_replay(self):
        fixture = event_test_module.SessionContractTests(
            "test_session_manifest_requires_verified_eligible_matching_inputs"
        )
        fixture.setUp()
        coordinator = None
        try:
            access_end = fixture.provider_manifest.access_expires_at
            analysis_end = access_end + timedelta(days=2)

            def permission_change(raw):
                raw["basis"] = "written_permission"
                raw["permitted_operations"].append(
                    "post_expiry_analysis"
                )
                raw["analysis_expires_at"] = utc(analysis_end)
                raw["raw_retention_until"] = utc(analysis_end)

            def manifest_change(raw):
                raw["analysis_expires_at"] = utc(analysis_end)
                raw["raw_retention_until"] = utc(analysis_end)
                window = analysis_end - fixture.provider_manifest.access_starts_at
                raw["max_raw_retention_seconds"] = (
                    window.days * 86_400 + window.seconds
                )

            bundle = fixture.builder.build(
                permission_change=permission_change,
                manifest_change=manifest_change,
                qualification_change=lambda raw: raw.__setitem__(
                    "qualified_until",
                    utc(analysis_end),
                ),
            )
            provider_manifest = fixture.builder.load(bundle)
            provisional = replace(
                fixture.config,
                provider_manifest_sha256=(
                    provider_manifest.source_file_sha256
                ),
                canonical_sha256="",
            )
            config = replace(
                provisional,
                canonical_sha256=canonical_config_sha256(provisional),
            )
            gate_now = [fixture.now]
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
                decision = evaluate_provider(
                    config,
                    provider_manifest,
                    fixture.request,
                    environ={"SYNTHETIC_API_KEY": "fixture-secret"},
                )
                decision.require_eligible()
                manifest = build_session_manifest(
                    config=config,
                    provider_manifest=provider_manifest,
                    qualification=decision,
                    session_id=(
                        "12345678-1234-4234-8234-123456789abc"
                    ),
                    created_wall_ns=fixture.ns(fixture.now),
                    code_sha256="f" * 64,
                )
                coordinator = RetentionCoordinator.acquire(
                    config,
                    clock_ns=lambda: manifest.created_wall_ns,
                )
                coordinator.recover_and_purge()
                gate = ProviderGate(
                    config,
                    provider_manifest,
                    fixture.request,
                    environ={"SYNTHETIC_API_KEY": "fixture-secret"},
                    clock=lambda: gate_now[0],
                )
                authorizer = bind_provider_persistence_authorizer(
                    gate=gate,
                    coordinator=coordinator,
                    session_manifest=manifest,
                )
                write_capability = coordinator.arm_before_wal(
                    session_manifest=manifest,
                    decision=authorizer.bound_decision,
                    persistence_authorizer=authorizer,
                )
                writer = JournalWriter.create(
                    write_capability=write_capability,
                    session_manifest=manifest,
                )
                runtime = EventRuntime(
                    writer=writer,
                    state=initial_state(manifest.session_id),
                    persistence_authorizer=authorizer,
                    coordinator=coordinator,
                )
                runtime.ingest(captured(authorizer))
                runtime.close_clean("operator_stop")

                self.assertIs(
                    provider_manifest.permission.basis,
                    PermissionBasis.WRITTEN_PERMISSION,
                )
                self.assertIn(
                    PermissionOperation.POST_EXPIRY_ANALYSIS,
                    provider_manifest.permission.permitted_operations,
                )
                gate_now[0] = access_end + timedelta(hours=1)
                allowed = replay(coordinator, authorizer)
                self.assertTrue(allowed.exact_replay)

                issue_calls = 0
                pread_calls = 0

                def issue(*args, **kwargs):
                    nonlocal issue_calls
                    issue_calls += 1
                    raise AssertionError("read capability issued")

                def pread(*args, **kwargs):
                    nonlocal pread_calls
                    pread_calls += 1
                    raise AssertionError("WAL byte read")

                gate_now[0] = analysis_end
                with (
                    mock.patch.object(
                        RetentionCoordinator,
                        "issue_read_capability",
                        issue,
                    ),
                    mock.patch.object(
                        ProviderWalReadCapability,
                        "pread",
                        pread,
                    ),
                ):
                    with self.assertRaises(ProviderGateError) as denied:
                        replay(coordinator, authorizer)
                self.assertIs(
                    denied.exception.reason,
                    QualificationReason.ANALYSIS_EXPIRED,
                )
                self.assertEqual(issue_calls, 0)
                self.assertEqual(pread_calls, 0)
        finally:
            if coordinator is not None:
                coordinator.close()
            fixture.tearDown()

    def test_reader_creation_failure_closes_issued_read_capability(self):
        with replay_session() as (
            _,
            coordinator,
            authorizer,
            _,
            _,
        ):
            closed: list[ProviderWalReadCapability] = []
            original_close = ProviderWalReadCapability.close

            def close(capability):
                closed.append(capability)
                return original_close(capability)

            with mock.patch.object(
                ProviderWalReadCapability,
                "pread",
                side_effect=JournalCorruptionError("reader creation failed"),
            ), mock.patch.object(
                ProviderWalReadCapability,
                "close",
                close,
            ):
                with self.assertRaises(JournalCorruptionError):
                    replay(coordinator, authorizer)
            self.assertEqual(len(closed), 1)

    def test_purge_finishes_before_read_capability_or_wal_byte(self):
        with replay_session() as (
            _,
            coordinator,
            authorizer,
            _,
            _,
        ):
            calls: list[str] = []
            original_recover = RetentionCoordinator.recover_and_purge
            original_issue = RetentionCoordinator.issue_read_capability
            original_pread = ProviderWalReadCapability.pread

            def recover(instance):
                calls.append("purge")
                return original_recover(instance)

            def issue(instance, *, persistence_authorizer):
                calls.append("issue")
                return original_issue(
                    instance,
                    persistence_authorizer=persistence_authorizer,
                )

            def pread(instance, *, offset, length):
                calls.append("pread")
                return original_pread(
                    instance,
                    offset=offset,
                    length=length,
                )

            with mock.patch.object(
                RetentionCoordinator,
                "recover_and_purge",
                recover,
            ), mock.patch.object(
                RetentionCoordinator,
                "issue_read_capability",
                issue,
            ), mock.patch.object(
                ProviderWalReadCapability,
                "pread",
                pread,
            ):
                result = replay(coordinator, authorizer)
            self.assertTrue(result.exact_replay)
            self.assertLess(calls.index("purge"), calls.index("issue"))
            self.assertLess(calls.index("issue"), calls.index("pread"))

    def test_expected_digest_grammar_and_exact_production_types_fail_preopen(self):
        with replay_session() as (
            _,
            coordinator,
            authorizer,
            _,
            _,
        ):
            with mock.patch.object(
                RetentionCoordinator,
                "issue_read_capability",
                side_effect=AssertionError("read capability issued"),
            ):
                with self.assertRaises((TypeError, ValueError)):
                    replay_exact(
                        expected_session_manifest_sha256="0" * 63,
                        persistence_authorizer=authorizer,
                        coordinator=coordinator,
                    )
            for changed in (
                {"persistence_authorizer": object()},
                {"coordinator": object()},
            ):
                values = {
                    "expected_session_manifest_sha256": (
                        expected_digest(authorizer)
                    ),
                    "persistence_authorizer": authorizer,
                    "coordinator": coordinator,
                }
                values.update(changed)
                with self.assertRaises(TypeError):
                    replay_exact(**values)

    def test_clean_exact_phase_one_replay_is_literal_not_research_evaluable(self):
        with replay_session() as (
            _,
            coordinator,
            authorizer,
            _,
            _,
        ):
            result = replay(coordinator, authorizer)
            self.assertTrue(result.exact_replay)
            self.assertIs(result.research_evaluable, False)
            self.assertFalse(
                any(item.name == "research_evaluable" and item.init
                    for item in fields(ReplayResult))
            )

    def test_replay_static_boundary_and_memory_shape(self):
        source = inspect.getsource(
            sys.modules["tennis_v1.replay_core"]
        )
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
        self.assertNotIn("open", names)
        self.assertNotIn("open", attrs)
        for forbidden in (
            "os",
            "Path",
            "JournalWriter",
            "append_raw",
            "append_derived",
            "_walk",
        ):
            self.assertNotIn(forbidden, names | attrs)
        self.assertIn("iter_replay_records", attrs)
        self.assertIn("recover_and_purge", attrs)
        self.assertIn("issue_read_capability", attrs)
        self.assertIn("create", attrs)

    def test_replay_memory_tracks_sources_not_record_history(self):
        with replay_session(raw_count=64) as (
            _,
            coordinator,
            authorizer,
            _,
            _,
        ):
            result = replay(coordinator, authorizer)
            self.assertTrue(result.exact_replay)
            self.assertEqual(result.raw_count, 64)
            self.assertEqual(len(result.state.source_epochs), 1)

    def test_corrupt_extra_derived_stream_retains_only_bounded_candidates(self):
        with replay_session() as (
            _,
            coordinator,
            authorizer,
            _,
            _,
        ):
            start, raw, derived, terminal = records_for(
                coordinator,
                authorizer,
            )

            def peak_for(derived_total: int) -> int:
                def streamed(_reader):
                    yield start
                    yield raw
                    for index in range(derived_total):
                        yield replace(
                            derived,
                            ingest_seq=raw.ingest_seq + index + 1,
                        )
                    terminal_seq = raw.ingest_seq + derived_total + 1
                    yield replace(terminal, ingest_seq=terminal_seq)
                    return ScanSummary(
                        file_size=terminal_seq,
                        last_good_offset=terminal_seq,
                        last_good_ingest_seq=terminal_seq,
                        record_count=derived_total + 3,
                        raw_count=1,
                        derived_count=derived_total,
                        terminal_clean=True,
                        issue=None,
                        wal_valid=True,
                    )

                gc.collect()
                tracemalloc.start()
                with mock.patch.object(
                    JournalReader,
                    "iter_replay_records",
                    streamed,
                ):
                    result = replay(coordinator, authorizer)
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                self.assertIs(
                    result.replay_mismatch,
                    ReplayMismatch.DERIVED_EXTRA,
                )
                return peak

            small_peak = peak_for(16)
            large_peak = peak_for(4096)
            self.assertLessEqual(large_peak, small_peak + 512 * 1024)


CRASH_RECOVERY_SCRIPT = r"""
import json
import os
import signal
import sys
from unittest import mock

from tennis_v1 import adapter_contract
from tennis_v1.entitlements import ProviderGate
import tennis_v1.retention as retention_module
import tennis_v1.sequencer as sequencer_module
from tennis_v1.replay_core import ReplayMismatch, scan_diagnostic_prefix
from tennis_v1.retention import RetentionCoordinator, RetentionError
from tennis_v1.sequencer import EventRuntime, bind_provider_persistence_authorizer
from tennis_v1.session import session_manifest_sha256
from tennis_v1.state import initial_state
from tennis_v1.wal import JournalWriter, ScanIssue
from tests.tennis_v1 import test_events as event_test_module
from tests.tennis_v1.test_sequencer import captured

kind = sys.argv[1]
assert kind in {"raw", "torn"}
for attempt in range(3):
    fixture = event_test_module.SessionContractTests(
        "test_session_manifest_requires_verified_eligible_matching_inputs")
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
                        environ={"SYNTHETIC_API_KEY": "fixture-secret"},
                        clock=lambda: fixture.now,
                    )
                    child_authorizer = bind_provider_persistence_authorizer(
                        gate=child_gate,
                        coordinator=child_coordinator,
                        session_manifest=manifest,
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
                    candidate = captured(child_authorizer)
                    if kind == "raw":
                        def blocked_reducer(*_):
                            os.write(signal_write, b"R")
                            os.read(block_read, 1)
                            raise AssertionError("parent did not kill child")
                        with mock.patch.object(
                            sequencer_module,
                            "reduce_event",
                            blocked_reducer,
                        ):
                            runtime.ingest(candidate)
                    else:
                        def controlled_partial(fd, content):
                            target = max(1, len(content) // 2)
                            written = 0
                            while written < target:
                                count = os.write(
                                    fd,
                                    content[written:target],
                                )
                                if count <= 0:
                                    raise AssertionError(
                                        "controlled partial write stalled")
                                written += count
                            os.fsync(fd)
                            os.write(signal_write, b"T")
                            os.read(block_read, 1)
                            raise AssertionError("parent did not kill child")
                        with mock.patch.object(
                            retention_module,
                            "_write_all",
                            controlled_partial,
                        ):
                            runtime.ingest(candidate)
                finally:
                    os._exit(91)

            os.close(signal_write)
            os.close(block_read)
            observed = os.read(signal_read, 1)
            assert observed == (b"R" if kind == "raw" else b"T"), observed
            os.kill(child_pid, signal.SIGKILL)
            waited, status = os.waitpid(child_pid, 0)
            assert waited == child_pid and os.WIFSIGNALED(status)
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
                assert str(error) == "retention_session_already_armed"
            else:
                raise AssertionError("crashed session rearmed/resumed")
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
            assert result.exact_replay is False
            if kind == "raw":
                assert result.scan_issue is ScanIssue.MISSING_TERMINAL
                assert (
                    result.replay_mismatch
                    is ReplayMismatch.DERIVED_MISSING
                )
                assert result.raw_count == 1
                assert result.state is not None
                assert result.state.raw_count == 1
            else:
                assert result.scan_issue is ScanIssue.TORN_TAIL
                assert result.wal_valid is False
                assert result.raw_count == 0
                assert result.state is not None
                assert result.state.raw_count == 0
    finally:
        if coordinator is not None:
            coordinator.close()
        fixture.tearDown()
print(json.dumps(
    {"kind": kind, "attempts": 3, "rearm_rejections": 3},
    sort_keys=True,
))
"""


MANIFEST_REJECTION_SCRIPT = r"""
from unittest import mock
import json
from tennis_v1 import adapter_contract
from tennis_v1.canonical import canonical_json_bytes
from tennis_v1.replay_core import ReplayMismatch, replay_exact
from tennis_v1.retention import RetentionCoordinator, RetentionGlobalHalt
from tennis_v1.sequencer import EventRuntime, bind_provider_persistence_authorizer
from tennis_v1.session import session_manifest_sha256
from tennis_v1.state import initial_state
from tennis_v1.wal import FILE_FLAGS, FILE_MAGIC, FILE_PREFIX, FILE_VERSION, JournalWriter, _encode_frame
from tests.tennis_v1.test_sequencer import captured, concrete_environment

with concrete_environment() as (fixture, coordinator, gate, manifest):
    authorizer = bind_provider_persistence_authorizer(
        gate=gate, coordinator=coordinator, session_manifest=manifest)
    capability = coordinator.arm_before_wal(
        session_manifest=manifest,
        decision=authorizer.bound_decision,
        persistence_authorizer=authorizer)
    writer = JournalWriter.create(
        write_capability=capability, session_manifest=manifest)
    runtime = EventRuntime(
        writer=writer,
        state=initial_state(manifest.session_id),
        persistence_authorizer=authorizer,
        coordinator=coordinator)
    runtime.close_clean("operator_stop")
    read_capability = coordinator.issue_read_capability(
        persistence_authorizer=authorizer)
    from tennis_v1.wal import JournalReader
    with JournalReader.open(read_capability=read_capability) as reader:
        records = tuple(reader.iter_records())
    raw = json.loads(records[0].payload)
    raw["code_sha256"] = "0" * 64
    payload = canonical_json_bytes(raw)
    from dataclasses import replace
    import hashlib
    start = replace(
        records[0],
        payload=payload,
        payload_sha256=hashlib.sha256(payload).hexdigest())
    path = fixture.config.state_root / "sessions" / f"{manifest.session_id}.wal"
    prefix = FILE_PREFIX.pack(
        FILE_MAGIC, FILE_VERSION, FILE_FLAGS, FILE_PREFIX.size)
    path.write_bytes(prefix + b"".join(
        _encode_frame(item) for item in (start,) + records[1:]))
    closed = []
    rejected = []
    from tennis_v1.retention import ProviderWalReadCapability
    original_close = ProviderWalReadCapability.close
    def close(cap):
        closed.append(True)
        return original_close(cap)
    with mock.patch.object(ProviderWalReadCapability, "close", close):
        result = replay_exact(
            expected_session_manifest_sha256=session_manifest_sha256(manifest),
            persistence_authorizer=authorizer,
            coordinator=coordinator)
    assert result.replay_mismatch is ReplayMismatch.SESSION_MANIFEST
    assert closed
    try:
        coordinator.require_provider_operation()
    except RetentionGlobalHalt:
        rejected.append(True)
    assert rejected
print("manifest-rejection-ok")
"""


EXPECTED_MANIFEST_REJECTION_SCRIPT = r"""
from contextlib import ExitStack
import threading
from unittest import mock

import tennis_v1.retention as retention_module
from tennis_v1.replay_core import replay_exact
from tennis_v1.retention import (
    ProviderWalReadCapability,
    RetentionCoordinator,
    RetentionError,
    RetentionGlobalHalt,
    _reject_expected_replay_manifest,
)
from tennis_v1.sequencer import (
    EventRuntime,
    bind_provider_persistence_authorizer,
)
from tennis_v1.session import session_manifest_sha256
from tennis_v1.state import initial_state
from tennis_v1.wal import JournalReader, JournalWriter
from tests.tennis_v1.test_sequencer import concrete_environment

with concrete_environment() as (fixture, coordinator, gate, manifest):
    authorizer = bind_provider_persistence_authorizer(
        gate=gate,
        coordinator=coordinator,
        session_manifest=manifest,
    )
    write_capability = coordinator.arm_before_wal(
        session_manifest=manifest,
        decision=authorizer.bound_decision,
        persistence_authorizer=authorizer,
    )
    writer = JournalWriter.create(
        write_capability=write_capability,
        session_manifest=manifest,
    )
    runtime = EventRuntime(
        writer=writer,
        state=initial_state(manifest.session_id),
        persistence_authorizer=authorizer,
        coordinator=coordinator,
    )
    runtime.close_clean("operator_stop")
    actual_digest = session_manifest_sha256(manifest)
    wrong_digest = "0" * 64 if actual_digest != "0" * 64 else "1" * 64

    def forbidden(name):
        def fail(*args, **kwargs):
            raise AssertionError(name)
        return fail

    def no_replay_io():
        stack = ExitStack()
        for target, name, label in (
            (
                RetentionCoordinator,
                "recover_and_purge",
                "recover_and_purge",
            ),
            (
                RetentionCoordinator,
                "issue_read_capability",
                "issue_read_capability",
            ),
            (ProviderWalReadCapability, "pread", "capability.pread"),
            (JournalReader, "create", "JournalReader.create"),
            (
                retention_module,
                "_open_existing_file",
                "_open_existing_file",
            ),
            (retention_module, "_read_marker", "_read_marker"),
            (retention_module.os, "pread", "os.pread"),
        ):
            stack.enter_context(
                mock.patch.object(target, name, forbidden(label))
            )
        return stack

    class HostileAuthorizer:
        touches = 0

        @property
        def coordinator(self):
            self.touches += 1
            raise AssertionError("hostile coordinator property read")

        @property
        def session_manifest(self):
            self.touches += 1
            raise AssertionError("hostile manifest property read")

        @property
        def bound_decision(self):
            self.touches += 1
            raise AssertionError("hostile decision property read")

    hostile = HostileAuthorizer()
    with mock.patch.object(
        RetentionCoordinator,
        "_reject_expected_replay_manifest",
        forbidden("coordinator rejection dispatch"),
    ):
        try:
            _reject_expected_replay_manifest(
                expected_session_manifest_sha256=wrong_digest,
                persistence_authorizer=hostile,
                coordinator=coordinator,
            )
        except RetentionError as error:
            assert str(error) == (
                "retention_expected_replay_rejection_invalid"
            )
        else:
            raise AssertionError("hostile authorizer accepted")
    assert hostile.touches == 0
    coordinator.require_provider_operation()

    wrong_thread_errors = []
    def reject_from_wrong_thread():
        try:
            _reject_expected_replay_manifest(
                expected_session_manifest_sha256=wrong_digest,
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
        except RetentionError as error:
            wrong_thread_errors.append(str(error))

    wrong_thread = threading.Thread(target=reject_from_wrong_thread)
    wrong_thread.start()
    wrong_thread.join()
    assert wrong_thread_errors == [
        "retention_expected_replay_rejection_invalid"
    ]
    coordinator.require_provider_operation()

    for invalid in (None, "0" * 63, "A" * 64):
        with no_replay_io():
            try:
                replay_exact(
                    expected_session_manifest_sha256=invalid,
                    persistence_authorizer=authorizer,
                    coordinator=coordinator,
                )
            except ValueError as error:
                assert str(error) == (
                    "expected_session_manifest_sha256_invalid"
                )
            else:
                raise AssertionError("malformed expected digest accepted")
        coordinator.require_provider_operation()

    with no_replay_io():
        try:
            replay_exact(
                expected_session_manifest_sha256=wrong_digest,
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
        except ValueError as error:
            assert str(error) == "expected_session_manifest_mismatch"
        else:
            raise AssertionError("wrong expected digest accepted")

    try:
        coordinator.require_provider_operation()
    except RetentionGlobalHalt:
        pass
    else:
        raise AssertionError("wrong expected digest did not latch halt")
print("expected-manifest-preopen-rejection-ok")
"""


class ReplayIsolationTests(unittest.TestCase):
    def test_derived_signature_rejects_subclass_before_any_field_getter(self):
        class HostileEvent(PersistedEvent):
            touches = 0

            def __getattribute__(self, name):
                if name in PersistedEvent.__dataclass_fields__:
                    type(self).touches += 1
                    raise AssertionError("hostile event getter executed")
                return super().__getattribute__(name)

        hostile = object.__new__(HostileEvent)
        with self.assertRaisesRegex(
            TypeError,
            r"\Aexact PersistedEvent required\Z",
        ):
            _derived_signature(hostile)
        self.assertEqual(HostileEvent.touches, 0)

    def test_crash_after_raw_fsync_before_reduce_is_replayed_three_times(self):
        completed = subprocess.run(
            [PYTHON, "-c", CRASH_RECOVERY_SCRIPT, "raw"],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        self.assertIn('"attempts": 3', completed.stdout)
        self.assertIn('"kind": "raw"', completed.stdout)
        self.assertIn('"rearm_rejections": 3', completed.stdout)

    def test_controlled_torn_frame_preserves_verified_prefix_three_times(self):
        completed = subprocess.run(
            [PYTHON, "-c", CRASH_RECOVERY_SCRIPT, "torn"],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        self.assertIn('"attempts": 3', completed.stdout)
        self.assertIn('"kind": "torn"', completed.stdout)
        self.assertIn('"rearm_rejections": 3', completed.stdout)

    def test_first_session_start_mismatch_closes_capability_then_latches_halt(self):
        completed = subprocess.run(
            [PYTHON, "-c", MANIFEST_REJECTION_SCRIPT],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        self.assertIn("manifest-rejection-ok", completed.stdout)

    def test_wrong_expected_manifest_digest_latches_before_any_replay_io(self):
        completed = subprocess.run(
            [PYTHON, "-c", EXPECTED_MANIFEST_REJECTION_SCRIPT],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        self.assertIn(
            "expected-manifest-preopen-rejection-ok",
            completed.stdout,
        )


if __name__ == "__main__":
    unittest.main()
