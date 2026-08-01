from __future__ import annotations

import ast
import copy
from dataclasses import fields, replace
import gc
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import tracemalloc
import unittest
import uuid
from unittest import mock

from tennis_v1.canonical import canonical_json_bytes
from tennis_v1.capture import (
    MAX_CAPTURE_BYTES,
    absent_provenance,
    capture_public_json,
    capture_redacted_json,
    issue_capture_authority,
    safe_provenance,
)
from tennis_v1.codec import decode_record, encode_record
from tennis_v1.events import (
    CapturedInput,
    DerivedDraft,
    PersistedEvent,
    ProvenanceState,
    RecordKind,
    SessionManifest,
    SourceKind,
)
from tennis_v1.session import (
    canonical_session_manifest_bytes,
    session_manifest_sha256,
)
from tennis_v1.wal import (
    DISK_HALT_RESERVE_BYTES,
    FILE_FLAGS,
    FILE_MAGIC,
    FILE_PREFIX,
    FILE_VERSION,
    FRAME_DIGEST_DOMAIN,
    FRAME_FLAGS,
    FRAME_KIND,
    FRAME_MAGIC,
    FRAME_PREFIX,
    FRAME_TRAILER,
    FRAME_VERSION,
    MAX_FRAME_BYTES,
    MIN_FREE_BYTES,
    TRAILER_MAGIC,
    DiskLowError,
    JournalCorruptionError,
    JournalDurabilityError,
    JournalReader,
    JournalValidationError,
    JournalWriter,
    ScanIssue,
)
from tennis_v1.retention import (
    ProviderWalReadCapability,
    RetentionCoordinator,
    RetentionPrewriteCapacityError,
)
from tests.tennis_v1.test_retention import (
    PYTHON,
    MutableClock,
    StrictAuthorizer,
    make_config,
    make_manifest_decision,
)


class WalAuthorizer(StrictAuthorizer):
    def authorize_capture(self, authority, captured) -> None:
        if (
            authority._session_authorizer is not self
            or authority.session_id != self.session_manifest.session_id
            or captured.session_id != self.session_manifest.session_id
            or captured.source_id != self.session_manifest.provider_id
            or captured.retention_delete_by_ns
            != self.session_manifest.required_retention_until_ns
        ):
            raise ValueError("capture_contract_violation")


def encode_frame(event: PersistedEvent) -> bytes:
    metadata, payload = encode_record(event)
    total = 76 + len(metadata) + len(payload)
    prefix = FRAME_PREFIX.pack(
        FRAME_MAGIC,
        FRAME_VERSION,
        FRAME_KIND[event.record_kind],
        FRAME_FLAGS,
        event.ingest_seq,
        total,
        len(metadata),
        len(payload),
    )
    digest = hashlib.sha256(
        FRAME_DIGEST_DOMAIN + prefix + metadata + payload
    ).digest()
    return (
        prefix
        + metadata
        + payload
        + digest
        + FRAME_TRAILER.pack(total, TRAILER_MAGIC)
    )


def frame_ranges(content: bytes) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    offset = FILE_PREFIX.size
    while offset < len(content):
        prefix = content[offset : offset + FRAME_PREFIX.size]
        if len(prefix) != FRAME_PREFIX.size:
            raise AssertionError("fixture contains a partial frame prefix")
        total = FRAME_PREFIX.unpack(prefix)[5]
        ranges.append((offset, offset + total))
        offset += total
    if offset != len(content):
        raise AssertionError("fixture frame lengths do not cover the WAL")
    return ranges


def rewrite_frame(content: bytes, index: int, replacement: bytes) -> bytes:
    ranges = frame_ranges(content)
    start, end = ranges[index]
    return content[:start] + replacement + content[end:]


def child(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-c", script, *arguments],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


class WalTests(unittest.TestCase):
    def test_clean_terminal_two_phase_ack_survives_capability_close(self):
        writer, authorizer = self.writer_for()
        raw = writer.append_raw(self.capture(authorizer))
        terminal = writer.close_clean(
            reason="operator_stop",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=raw.ingest_seq,
        )
        self.assertEqual(terminal.event_type, "SESSION_HALT")
        self.coordinator.mark_clean_terminal(
            session_id=self.manifest.session_id
        )
        self.coordinator.mark_clean_terminal(
            session_id=self.manifest.session_id
        )

    def test_wal_revalidates_shared_capture_transform_contract_before_write(self):
        writer, authorizer = self.writer_for()
        candidate = self.capture(authorizer)
        object.__setattr__(
            candidate,
            "payload_transform",
            "forged-transform-v1",
        )
        before = self.wal_path().read_bytes()
        with self.assertRaisesRegex(
            JournalValidationError,
            "captured_input_invalid",
        ):
            writer.append_raw(candidate)
        self.assertEqual(self.wal_path().read_bytes(), before)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.manifest, self.decision = make_manifest_decision()
        self.clock = MutableClock(self.manifest.created_wall_ns)
        self.coordinator = RetentionCoordinator.acquire(
            make_config(self.root / "state"),
            clock_ns=self.clock,
        )
        self.coordinator.recover_and_purge()

    def tearDown(self) -> None:
        self.coordinator.close()
        self.temporary.cleanup()

    def writer_for(
        self,
        manifest: SessionManifest | None = None,
        decision=None,
    ) -> tuple[JournalWriter, WalAuthorizer]:
        manifest = manifest or self.manifest
        decision = decision or self.decision
        authorizer = WalAuthorizer(self.coordinator, manifest, decision)
        capability = self.coordinator.arm_before_wal(
            session_manifest=manifest,
            decision=decision,
            persistence_authorizer=authorizer,
        )
        writer = JournalWriter.create(
            write_capability=capability,
            session_manifest=manifest,
        )
        return writer, authorizer

    def new_writer(self) -> tuple[JournalWriter, WalAuthorizer]:
        manifest, decision = make_manifest_decision(str(uuid.uuid4()))
        return self.writer_for(manifest, decision)

    def capture(
        self,
        authorizer: WalAuthorizer,
        *,
        raw: bytes = b'{"score":"15-0"}',
        provider_sequence: str = "A-1",
        redacted: bool = False,
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
            wall_clock_ns=lambda: authorizer.session_manifest.created_wall_ns,
            monotonic_clock_ns=lambda: 90,
            clock_uncertainty_ns=lambda: 3,
        )
        factory = capture_redacted_json if redacted else capture_public_json
        return factory(
            raw,
            authority=authority,
            content_type="application/json",
            request_id=safe_provenance("request-1"),
            event_type="provider.point",
            event_version=1,
            source_wall_ns=10,
            source_generated_ns=11,
            provider_sequence=provider_sequence,
        )

    def wal_path(self, manifest: SessionManifest | None = None) -> Path:
        manifest = manifest or self.manifest
        return (
            self.root
            / "state"
            / "sessions"
            / f"{manifest.session_id}.wal"
        )

    def close_clean_with_one_raw(
        self,
    ) -> tuple[WalAuthorizer, PersistedEvent, bytes]:
        writer, authorizer = self.writer_for()
        raw = writer.append_raw(self.capture(authorizer))
        writer.close_clean(
            reason="operator_stop",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=raw.ingest_seq,
        )
        return authorizer, raw, self.wal_path().read_bytes()

    def open_reader(self, authorizer: WalAuthorizer) -> JournalReader:
        capability = self.coordinator.issue_read_capability(
            persistence_authorizer=authorizer,
        )
        return JournalReader.open(read_capability=capability)

    def test_writer_requires_coordinator_issued_capability_and_exact_armed_marker(self):
        with self.assertRaises(TypeError):
            JournalWriter.create(
                write_capability=object(),  # type: ignore[arg-type]
                session_manifest=self.manifest,
            )
        self.assertEqual(
            list((self.root / "state" / "sessions").iterdir()),
            [],
        )

    def test_writer_create_accepts_only_opaque_capability_and_exact_manifest(self):
        writer, _ = self.writer_for()
        self.assertIs(writer.session_manifest, self.manifest)
        self.assertEqual(writer.session_start.ingest_seq, 1)
        self.assertNotIn("_fd", type(writer).__slots__)
        self.assertNotIn("_path", type(writer).__slots__)
        with self.assertRaises(TypeError):
            JournalWriter.create(  # type: ignore[call-arg]
                path=self.wal_path(),
                session_manifest=self.manifest,
            )

    def test_writer_requires_research_evaluable_is_literal_false(self):
        code = """
from pathlib import Path
import sys
from tests.tennis_v1.test_retention import (
    MutableClock, StrictAuthorizer, make_config, make_manifest_decision,
)
from tennis_v1.retention import RetentionCoordinator
from tennis_v1.wal import JournalWriter
root = Path(sys.argv[1])
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
coordinator = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
coordinator.recover_and_purge()
authorizer = StrictAuthorizer(coordinator, manifest, decision)
capability = coordinator.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=authorizer,
)
object.__setattr__(manifest, "research_evaluable", 0)
try:
    try:
        JournalWriter.create(
            write_capability=capability,
            session_manifest=manifest,
        )
    except Exception:
        wal = root / "sessions" / f"{manifest.session_id}.wal"
        if wal.stat().st_size != 0:
            raise SystemExit("invalid manifest wrote bytes")
        print("rejected")
    else:
        raise SystemExit("falsey non-boolean manifest accepted")
finally:
    coordinator.close()
"""
        result = child(code, str(self.root / "literal-false"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "rejected")

    def test_every_supplied_manifest_validation_failure_claims_consumes_and_halts_before_bytes(self):
        code = r"""
from pathlib import Path
import sys
from tests.tennis_v1.test_retention import (
    MutableClock, StrictAuthorizer, make_config, make_manifest_decision,
)
from tennis_v1.retention import (
    RetentionCoordinator,
    RetentionError,
    RetentionGlobalHalt,
)
from tennis_v1.wal import JournalWriter

root = Path(sys.argv[1])
mode = sys.argv[2]
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
coordinator = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
coordinator.recover_and_purge()
authorizer = StrictAuthorizer(coordinator, manifest, decision)
capability = coordinator.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=authorizer,
)
wal = root / "sessions" / f"{manifest.session_id}.wal"
supplied = manifest
if mode == "wrong_type":
    supplied = object()
elif mode == "research":
    object.__setattr__(manifest, "research_evaluable", 0)
elif mode == "session_id":
    object.__setattr__(manifest, "session_id", "not-a-uuid")
elif mode == "created_wall":
    object.__setattr__(manifest, "created_wall_ns", True)
elif mode == "canonical_value":
    object.__setattr__(manifest, "provider_id", object())
else:
    raise SystemExit("unknown mode")
try:
    JournalWriter.create(
        write_capability=capability,
        session_manifest=supplied,
    )
except Exception:
    pass
else:
    raise SystemExit("invalid supplied manifest accepted")
errors = []
if wal.stat().st_size != 0:
    errors.append("invalid supplied manifest wrote WAL bytes")
if capability in coordinator._write_capabilities:
    errors.append("invalid supplied manifest did not consume capability")
try:
    coordinator.require_provider_operation()
except RetentionGlobalHalt:
    pass
else:
    errors.append("invalid supplied manifest did not latch halt")
try:
    capability.close()
except RetentionError:
    pass
coordinator.close()
if errors:
    raise SystemExit("; ".join(errors))
print(mode)
"""
        for mode in (
            "wrong_type",
            "research",
            "session_id",
            "created_wall",
            "canonical_value",
        ):
            with self.subTest(mode=mode):
                result = child(
                    code,
                    str(self.root / f"claim-first-{mode}"),
                    mode,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), mode)

    def test_capability_manifest_marker_session_and_binding_mismatch_fail_prewrite(self):
        code = """
from dataclasses import replace
from pathlib import Path
import sys
from tests.tennis_v1.test_retention import (
    MutableClock, StrictAuthorizer, make_config, make_manifest_decision,
)
from tennis_v1.retention import RetentionCoordinator, RetentionGlobalHalt
from tennis_v1.wal import JournalWriter
root = Path(sys.argv[1])
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
coordinator = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
coordinator.recover_and_purge()
authorizer = StrictAuthorizer(coordinator, manifest, decision)
capability = coordinator.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=authorizer,
)
try:
    try:
        JournalWriter.create(
            write_capability=capability,
            session_manifest=replace(manifest),
        )
    except Exception:
        wal = root / "sessions" / f"{manifest.session_id}.wal"
        if wal.stat().st_size != 0:
            raise SystemExit("mismatch wrote bytes")
        try:
            coordinator.require_provider_operation()
        except RetentionGlobalHalt:
            print("halted")
        else:
            raise SystemExit("mismatch did not halt")
    else:
        raise SystemExit("equal but unbound manifest accepted")
finally:
    coordinator.close()
"""
        result = child(code, str(self.root / "manifest-mismatch"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "halted")

    def test_session_start_is_sequence_one_durable_and_matches_manifest(self):
        writer, authorizer = self.writer_for()
        content = self.wal_path().read_bytes()
        self.assertEqual(
            content[: FILE_PREFIX.size],
            FILE_PREFIX.pack(
                FILE_MAGIC,
                FILE_VERSION,
                FILE_FLAGS,
                FILE_PREFIX.size,
            ),
        )
        self.assertGreater(len(content), FILE_PREFIX.size)
        self.assertEqual(
            json.loads(writer.session_start.payload),
            json.loads(canonical_session_manifest_bytes(self.manifest)),
        )
        writer.close_halted(
            reason="operator_halt",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=0,
        )
        with self.open_reader(authorizer) as reader:
            self.assertEqual(reader.read_session_manifest(), self.manifest)
            summary = reader.scan()
            self.assertEqual(summary.issue, ScanIssue.HALTED_TERMINAL)
            self.assertTrue(summary.wal_valid)
            self.assertFalse(summary.terminal_clean)
            with self.assertRaises(JournalCorruptionError):
                reader.scan(require_clean=True)
            with self.assertRaises(JournalCorruptionError):
                tuple(reader.iter_records())
            self.assertEqual(
                len(tuple(reader.iter_records(diagnostic_prefix=True))),
                2,
            )

    def test_every_append_completes_write_loop_and_fsync_before_return(self):
        writer, authorizer = self.writer_for()
        actions: list[str] = []
        original_write = RetentionCoordinator._write_capability_bytes

        def tracked_write(coordinator, capability, frame):
            if coordinator is not self.coordinator:
                return original_write(coordinator, capability, frame)
            actions.append("write_enter")
            result = original_write(coordinator, capability, frame)
            actions.append("fsync_returned")
            return result

        with mock.patch.object(
            RetentionCoordinator,
            "_write_capability_bytes",
            new=tracked_write,
        ):
            raw = writer.append_raw(self.capture(authorizer))
            actions.append(f"returned_{raw.ingest_seq}")
        self.assertEqual(
            actions,
            ["write_enter", "fsync_returned", "returned_2"],
        )

    def test_frame_round_trip_preserves_exact_validated_json_payload_bytes(self):
        writer, authorizer = self.writer_for()
        captured = self.capture(authorizer)
        arbitrary = copy.copy(captured)
        payload = b'{"line":"one\\ntwo","nul":"\\u0000","utf8":"\\u00ff"}'
        object.__setattr__(arbitrary, "payload", payload)
        raw = writer.append_raw(arbitrary)
        writer.close_clean(
            reason="operator_stop",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=raw.ingest_seq,
        )
        with self.open_reader(authorizer) as reader:
            records = tuple(reader.iter_records())
        self.assertEqual(records[1].payload, payload)
        self.assertEqual(records[1], raw)

    def test_unknown_versions_flags_kinds_lengths_and_oversize_fail(self):
        authorizer, _, original = self.close_clean_with_one_raw()
        path = self.wal_path()
        ranges = frame_ranges(original)
        raw_start, raw_end = ranges[1]
        raw_frame = original[raw_start:raw_end]
        unpacked = list(FRAME_PREFIX.unpack(raw_frame[: FRAME_PREFIX.size]))
        mutations: list[bytes] = []
        for field_index, value in (
            (1, 2),
            (2, 99),
            (3, 1),
            (5, MAX_FRAME_BYTES + 1),
        ):
            changed = list(unpacked)
            changed[field_index] = value
            mutations.append(
                FRAME_PREFIX.pack(*changed) + raw_frame[FRAME_PREFIX.size :]
            )
        header_mutations = (
            FILE_PREFIX.pack(b"BADWAL!\0", 1, 0, FILE_PREFIX.size),
            FILE_PREFIX.pack(FILE_MAGIC, 2, 0, FILE_PREFIX.size),
            FILE_PREFIX.pack(FILE_MAGIC, 1, 1, FILE_PREFIX.size),
            FILE_PREFIX.pack(FILE_MAGIC, 1, 0, FILE_PREFIX.size + 1),
        )
        with self.open_reader(authorizer) as reader:
            for header in header_mutations:
                with self.subTest(header=header):
                    path.write_bytes(header + original[FILE_PREFIX.size :])
                    with self.assertRaises(JournalCorruptionError):
                        reader.scan()
            for frame in mutations:
                with self.subTest(prefix=frame[: FRAME_PREFIX.size]):
                    path.write_bytes(rewrite_frame(original, 1, frame))
                    with self.assertRaises(JournalCorruptionError):
                        reader.scan()
        path.write_bytes(original)

    def test_metadata_payload_digest_and_trailer_corruption_fail(self):
        authorizer, _, original = self.close_clean_with_one_raw()
        path = self.wal_path()
        ranges = frame_ranges(original)
        raw_start, raw_end = ranges[1]
        terminal_start, terminal_end = ranges[-1]
        raw_frame = bytearray(original[raw_start:raw_end])
        _, _, _, _, _, _, metadata_length, payload_length = FRAME_PREFIX.unpack(
            raw_frame[: FRAME_PREFIX.size]
        )
        payload_start = FRAME_PREFIX.size + metadata_length
        digest_start = payload_start + payload_length
        mutations: list[bytes] = []
        for index in (FRAME_PREFIX.size, payload_start, digest_start, len(raw_frame) - 1):
            changed = bytearray(raw_frame)
            changed[index] ^= 1
            mutations.append(bytes(changed))
        with self.open_reader(authorizer) as reader:
            for changed in mutations:
                with self.subTest(index=mutations.index(changed)):
                    path.write_bytes(rewrite_frame(original, 1, changed))
                    with self.assertRaises(JournalCorruptionError):
                        reader.scan()
            terminal = bytearray(original[terminal_start:terminal_end])
            terminal[-FRAME_TRAILER.size - 1] ^= 1
            path.write_bytes(rewrite_frame(original, -1, bytes(terminal)))
            summary = reader.scan()
            self.assertEqual(summary.issue, ScanIssue.CORRUPT_TAIL)
            self.assertFalse(summary.wal_valid)
        path.write_bytes(original)

    def test_sequence_duplicate_regression_gap_and_forward_parent_fail(self):
        writer, authorizer = self.writer_for()
        raw = writer.append_raw(self.capture(authorizer))
        derived = writer.append_derived(
            raw,
            DerivedDraft("raw_accepted", 1, "canonical-json-v1", b"{}"),
        )
        writer.close_clean(
            reason="operator_stop",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=raw.ingest_seq,
        )
        original = self.wal_path().read_bytes()
        path = self.wal_path()
        invalid_frames = (
            (1, encode_frame(replace(raw, ingest_seq=1))),
            (1, encode_frame(replace(raw, ingest_seq=3))),
            (2, encode_frame(replace(derived, parent_ingest_seq=1))),
        )
        with self.open_reader(authorizer) as reader:
            for index, changed in invalid_frames:
                with self.subTest(index=index, seq=FRAME_PREFIX.unpack(changed[:32])[4]):
                    path.write_bytes(rewrite_frame(original, index, changed))
                    with self.assertRaises(JournalCorruptionError):
                        reader.scan()
        path.write_bytes(original)

    def test_duplicate_or_nonfinal_terminal_fails(self):
        authorizer, _, original = self.close_clean_with_one_raw()
        terminal_start, terminal_end = frame_ranges(original)[-1]
        duplicate = original[terminal_start:terminal_end]
        with self.open_reader(authorizer) as reader:
            for suffix in (b"x", duplicate):
                with self.subTest(suffix_length=len(suffix)):
                    self.wal_path().write_bytes(original + suffix)
                    with self.assertRaises(JournalCorruptionError):
                        reader.scan()

    def test_terminal_payload_counts_hashes_and_reason_are_exact(self):
        writer, authorizer = self.writer_for()
        raw = writer.append_raw(self.capture(authorizer))
        writer.append_derived(
            raw,
            DerivedDraft("raw_accepted", 1, "canonical-json-v1", b"{}"),
        )
        terminal = writer.close_clean(
            reason="operator_stop",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=raw.ingest_seq,
        )
        payload = json.loads(terminal.payload)
        self.assertEqual(
            set(payload),
            {
                "terminal_version",
                "clean",
                "reason",
                "trace_sha256",
                "final_state_sha256",
                "record_count_before_terminal",
                "raw_count",
                "derived_count",
                "last_applied_raw_seq",
                "config_file_sha256",
                "config_canonical_sha256",
                "code_sha256",
                "session_manifest_sha256",
                "provider_manifest_file_sha256",
                "provider_manifest_canonical_sha256",
                "entitlement_id_sha256",
                "permission_artifact_sha256",
                "qualification_artifact_sha256",
                "qualification_trace_sha256",
                "adapter_code_sha256",
                "auth_contract_sha256",
                "quota_contract_sha256",
                "required_retention_until_ns",
                "research_evaluable",
            },
        )
        self.assertEqual(
            (
                payload["clean"],
                payload["reason"],
                payload["record_count_before_terminal"],
                payload["raw_count"],
                payload["derived_count"],
                payload["last_applied_raw_seq"],
                payload["session_manifest_sha256"],
                payload["research_evaluable"],
            ),
            (
                True,
                "operator_stop",
                3,
                1,
                1,
                raw.ingest_seq,
                session_manifest_sha256(self.manifest),
                False,
            ),
        )
        original = self.wal_path().read_bytes()
        with self.open_reader(authorizer) as reader:
            self.assertIsNone(reader.scan(require_clean=True).issue)
        forged_payload = dict(payload)
        forged_payload["raw_count"] = 2
        forged_bytes = canonical_json_bytes(forged_payload)
        forged_terminal = replace(
            terminal,
            payload=forged_bytes,
            payload_sha256=hashlib.sha256(forged_bytes).hexdigest(),
        )
        self.wal_path().write_bytes(
            rewrite_frame(original, -1, encode_frame(forged_terminal))
        )
        with self.open_reader(authorizer) as reader:
            with self.assertRaises(JournalCorruptionError):
                reader.scan()
        self.wal_path().write_bytes(original)

    def test_terminal_reason_uses_closed_sanitized_vocabulary(self):
        empty_writer, _ = self.new_writer()
        with self.assertRaises(JournalValidationError):
            empty_writer.close_halted(
                reason="",
                trace_sha256="0" * 64,
                final_state_sha256="1" * 64,
                last_applied_raw_seq=0,
            )
        arbitrary_writer, _ = self.new_writer()
        with self.assertRaises(JournalValidationError):
            arbitrary_writer.close_halted(
                reason="operator stop: requested",
                trace_sha256="0" * 64,
                final_state_sha256="1" * 64,
                last_applied_raw_seq=0,
            )
        writer, _ = self.new_writer()
        terminal = writer.close_halted(
            reason="operator_halt",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=0,
        )
        self.assertEqual(
            json.loads(terminal.payload)["reason"],
            "operator_halt",
        )
        for reason in (
            "ingress_backpressure",
            "ingress_owner_unresponsive",
        ):
            with self.subTest(reason=reason):
                ingress_writer, ingress_authorizer = self.new_writer()
                ingress_terminal = ingress_writer.close_halted(
                    reason=reason,
                    trace_sha256="0" * 64,
                    final_state_sha256="1" * 64,
                    last_applied_raw_seq=0,
                )
                self.assertEqual(
                    json.loads(ingress_terminal.payload)["reason"],
                    reason,
                )
                with self.open_reader(ingress_authorizer) as reader:
                    summary = reader.scan()
                    records = tuple(
                        reader.iter_records(diagnostic_prefix=True)
                    )
                self.assertEqual(summary.issue, ScanIssue.HALTED_TERMINAL)
                self.assertTrue(summary.wal_valid)
                self.assertEqual(
                    json.loads(records[-1].payload)["reason"],
                    reason,
                )

    def test_writer_never_appends_after_terminal(self):
        writer, authorizer = self.writer_for()
        raw = writer.append_raw(self.capture(authorizer))
        writer.close_clean(
            reason="operator_stop",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=raw.ingest_seq,
        )
        size = self.wal_path().stat().st_size
        operations = (
            lambda: writer.append_raw(self.capture(authorizer)),
            lambda: writer.append_derived(
                raw,
                DerivedDraft("raw_accepted", 1, "canonical-json-v1", b"{}"),
            ),
            lambda: writer.close_halted(
                reason="late",
                trace_sha256="0" * 64,
                final_state_sha256="1" * 64,
                last_applied_raw_seq=raw.ingest_seq,
            ),
        )
        for operation in operations:
            with self.assertRaises(JournalValidationError):
                operation()
        self.assertEqual(self.wal_path().stat().st_size, size)

    def test_forged_capture_validation_error_writes_no_raw_and_leaves_writer_healthy_for_halt(self):
        writer, authorizer = self.writer_for()
        forged = copy.copy(self.capture(authorizer))
        object.__setattr__(forged, "retention_delete_by_ns", 1)
        before = self.wal_path().stat().st_size
        with self.assertRaises(JournalValidationError):
            writer.append_raw(forged)
        self.assertFalse(writer.poisoned)
        self.assertEqual(self.wal_path().stat().st_size, before)
        terminal = writer.close_halted(
            reason="capture_contract_violation",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=0,
        )
        self.assertEqual(terminal.ingest_seq, 2)

    def test_forged_oversized_capture_is_rejected_before_any_write(self):
        writer, authorizer = self.writer_for()
        forged = copy.copy(self.capture(authorizer))
        object.__setattr__(forged, "payload", b"x" * (MAX_CAPTURE_BYTES + 1))
        before = self.wal_path().stat().st_size
        with self.assertRaises(JournalValidationError):
            writer.append_raw(forged)
        self.assertFalse(writer.poisoned)
        self.assertEqual(self.wal_path().stat().st_size, before)

    def test_writer_retained_memory_is_independent_of_raw_record_count(self):
        writer, authorizer = self.writer_for()
        template = self.capture(authorizer)
        payload_size = 256 * 1024
        gc.collect()
        tracemalloc.start()
        before, _ = tracemalloc.get_traced_memory()
        for index in range(24):
            captured = copy.copy(template)
            object.__setattr__(
                captured,
                "provider_sequence",
                f"MEM-{index}",
            )
            object.__setattr__(
                captured,
                "payload",
                b'{"padding":"'
                + bytes([65 + (index % 26)]) * (payload_size - 14)
                + b'"}',
            )
            writer.append_raw(captured)
            del captured
        gc.collect()
        after, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertLess(after - before, payload_size * 3)
        writer.close_halted(
            reason="operator_halt",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=0,
        )

    def test_derived_parent_must_be_latest_raw_but_multiple_outputs_may_share_it(self):
        writer, authorizer = self.writer_for()
        first = writer.append_raw(
            self.capture(authorizer, provider_sequence="P-1")
        )
        latest = writer.append_raw(
            self.capture(authorizer, provider_sequence="P-2")
        )
        before = self.wal_path().stat().st_size
        draft = DerivedDraft(
            "raw_accepted",
            1,
            "canonical-json-v1",
            b"{}",
        )
        with self.assertRaises(JournalValidationError):
            writer.append_derived(first, draft)
        self.assertEqual(self.wal_path().stat().st_size, before)
        self.assertFalse(writer.poisoned)
        one = writer.append_derived(latest, draft)
        two = writer.append_derived(latest, draft)
        self.assertEqual(
            (one.parent_ingest_seq, two.parent_ingest_seq),
            (latest.ingest_seq, latest.ingest_seq),
        )
        writer.close_clean(
            reason="operator_stop",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=latest.ingest_seq,
        )

    def test_reader_rejects_derived_parent_that_is_not_latest_raw(self):
        writer, authorizer = self.writer_for()
        first = writer.append_raw(
            self.capture(authorizer, provider_sequence="P-1")
        )
        latest = writer.append_raw(
            self.capture(authorizer, provider_sequence="P-2")
        )
        derived = writer.append_derived(
            latest,
            DerivedDraft("raw_accepted", 1, "canonical-json-v1", b"{}"),
        )
        writer.close_clean(
            reason="operator_stop",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=latest.ingest_seq,
        )
        original = self.wal_path().read_bytes()
        forged = replace(derived, parent_ingest_seq=first.ingest_seq)
        self.wal_path().write_bytes(
            rewrite_frame(original, 3, encode_frame(forged))
        )
        with self.open_reader(authorizer) as reader:
            with self.assertRaises(JournalCorruptionError):
                reader.scan()

    def test_halted_last_applied_witness_accepts_zero_or_sequence_at_most_latest_raw(self):
        writer, authorizer = self.writer_for()
        first = writer.append_raw(
            self.capture(authorizer, provider_sequence="P-1")
        )
        derived = writer.append_derived(
            first,
            DerivedDraft("raw_accepted", 1, "canonical-json-v1", b"{}"),
        )
        latest = writer.append_raw(
            self.capture(authorizer, provider_sequence="P-2")
        )
        terminal = writer.close_halted(
            reason="operator_halt",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=derived.ingest_seq,
        )
        self.assertLess(
            json.loads(terminal.payload)["last_applied_raw_seq"],
            latest.ingest_seq,
        )
        with self.open_reader(authorizer) as reader:
            summary = reader.scan()
        self.assertEqual(summary.issue, ScanIssue.HALTED_TERMINAL)
        self.assertTrue(summary.wal_valid)

    def test_zero_byte_partial_write_and_fsync_failures_poison_writer(self):
        for mode in ("zero", "partial", "fsync"):
            with self.subTest(mode=mode):
                writer, authorizer = self.new_writer()
                original_write = RetentionCoordinator._write_capability_bytes

                def fail_write(coordinator, capability, frame):
                    if coordinator is self.coordinator:
                        raise OSError(mode)
                    return original_write(coordinator, capability, frame)

                with mock.patch.object(
                    RetentionCoordinator,
                    "_write_capability_bytes",
                    new=fail_write,
                ):
                    with self.assertRaises(JournalDurabilityError):
                        writer.append_raw(self.capture(authorizer))
                self.assertTrue(writer.poisoned)

    def test_poisoned_writer_cannot_append_or_write_any_terminal(self):
        writer, authorizer = self.writer_for()
        original_write = RetentionCoordinator._write_capability_bytes

        def fail_write(coordinator, capability, frame):
            if coordinator is self.coordinator:
                raise OSError("injected")
            return original_write(coordinator, capability, frame)

        with mock.patch.object(
            RetentionCoordinator,
            "_write_capability_bytes",
            new=fail_write,
        ):
            with self.assertRaises(JournalDurabilityError):
                writer.append_raw(self.capture(authorizer))
        operations = (
            lambda: writer.append_raw(self.capture(authorizer)),
            lambda: writer.close_clean(
                reason="operator_stop",
                trace_sha256="0" * 64,
                final_state_sha256="1" * 64,
                last_applied_raw_seq=0,
            ),
            lambda: writer.close_halted(
                reason="halted",
                trace_sha256="0" * 64,
                final_state_sha256="1" * 64,
                last_applied_raw_seq=0,
            ),
        )
        for operation in operations:
            with self.assertRaises(JournalDurabilityError):
                operation()

    def test_every_healthy_writer_halt_requires_state_trace_and_last_applied_witnesses(self):
        for field_name, value in (
            ("trace_sha256", None),
            ("final_state_sha256", None),
            ("last_applied_raw_seq", None),
            ("trace_sha256", "not-a-digest"),
            ("last_applied_raw_seq", True),
        ):
            with self.subTest(field=field_name, value=value):
                writer, _ = self.new_writer()
                values = {
                    "reason": "operator_halt",
                    "trace_sha256": "0" * 64,
                    "final_state_sha256": "1" * 64,
                    "last_applied_raw_seq": 0,
                }
                values[field_name] = value
                with self.assertRaises(JournalValidationError):
                    writer.close_halted(**values)  # type: ignore[arg-type]
                self.assertFalse(writer.poisoned)

    def test_disk_low_halts_with_reserved_terminal_space_before_next_raw(self):
        writer, authorizer = self.writer_for()
        original = RetentionCoordinator._write_capability_bytes

        def capacity_denial(coordinator, capability, content):
            if coordinator is not self.coordinator:
                return original(coordinator, capability, content)
            if content.startswith(FRAME_MAGIC):
                event = decode_record(
                    content[FRAME_PREFIX.size : FRAME_PREFIX.size + FRAME_PREFIX.unpack(content[:32])[6]],
                    content[
                        FRAME_PREFIX.size + FRAME_PREFIX.unpack(content[:32])[6] :
                        FRAME_PREFIX.size
                        + FRAME_PREFIX.unpack(content[:32])[6]
                        + FRAME_PREFIX.unpack(content[:32])[7]
                    ],
                )
                if event.record_kind is RecordKind.RAW:
                    raise RetentionPrewriteCapacityError(
                        "retention_prewrite_capacity_low"
                    )
            return original(coordinator, capability, content)

        with mock.patch.object(
            RetentionCoordinator,
            "_write_capability_bytes",
            new=capacity_denial,
        ):
            with self.assertRaises(DiskLowError):
                writer.append_raw(self.capture(authorizer))
        self.assertFalse(writer.poisoned)
        terminal = writer.close_halted(
            reason="disk_low",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=0,
        )
        self.assertEqual(terminal.ingest_seq, 2)

    def test_halted_terminal_uses_only_one_shot_halt_control_capability(self):
        writer, _ = self.writer_for()
        calls = {"halt": 0, "ordinary": 0}
        original_halt = RetentionCoordinator._write_halt_control
        original_write = RetentionCoordinator._write_capability_bytes

        def tracked_halt(coordinator, capability, frame):
            if coordinator is self.coordinator:
                calls["halt"] += 1
            return original_halt(coordinator, capability, frame)

        def tracked_write(coordinator, capability, frame):
            if coordinator is self.coordinator:
                calls["ordinary"] += 1
            return original_write(coordinator, capability, frame)

        with (
            mock.patch.object(
                RetentionCoordinator,
                "_write_halt_control",
                new=tracked_halt,
            ),
            mock.patch.object(
                RetentionCoordinator,
                "_write_capability_bytes",
                new=tracked_write,
            ),
        ):
            terminal = writer.close_halted(
                reason="operator_halt",
                trace_sha256="0" * 64,
                final_state_sha256="1" * 64,
                last_applied_raw_seq=0,
            )
        self.assertFalse(json.loads(terminal.payload)["clean"])
        self.assertEqual(calls["halt"], 1)
        self.assertEqual(calls["ordinary"], 0)

    def test_global_halt_for_other_session_can_write_control_but_due_session_cannot(self):
        code = """
from pathlib import Path
import sys
import tennis_v1.retention as retention_module
from tests.tennis_v1.test_retention import (
    MutableClock, StrictAuthorizer, make_config, make_manifest_decision,
)
from tennis_v1.retention import (
    RetentionCoordinator, RetentionDueDeleteError, RetentionGlobalHalt,
)
from tennis_v1.wal import JournalWriter
root = Path(sys.argv[1])
manifest, decision = make_manifest_decision()
other_manifest, _ = make_manifest_decision(
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
)
clock = MutableClock(manifest.created_wall_ns)
coordinator = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
coordinator.recover_and_purge()
authorizer = StrictAuthorizer(coordinator, manifest, decision)
capability = coordinator.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=authorizer,
)
writer = JournalWriter.create(
    write_capability=capability,
    session_manifest=manifest,
)
try:
    retention_module._latch_global_halt(
        None,
        session_id=other_manifest.session_id,
        ambiguous=False,
    )
    coordinator.require_control_halt_eligible(
        session_id=manifest.session_id
    )
    writer.close_halted(
        reason="retention_global_halt",
        trace_sha256="0" * 64,
        final_state_sha256="1" * 64,
        last_applied_raw_seq=0,
    )
    if not (root / "sessions" / f"{manifest.session_id}.wal").stat().st_size:
        raise SystemExit("halt terminal not written")
    print("scoped-control")
finally:
    coordinator.close()
"""
        result = child(code, str(self.root / "other-global-halt"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "scoped-control")

        due_code = """
from pathlib import Path
import sys
from tests.tennis_v1.test_retention import (
    MutableClock, StrictAuthorizer, make_config, make_manifest_decision,
)
from tennis_v1.retention import RetentionCoordinator
from tennis_v1.wal import JournalWriter
root = Path(sys.argv[1])
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
coordinator = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
coordinator.recover_and_purge()
authorizer = StrictAuthorizer(coordinator, manifest, decision)
capability = coordinator.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=authorizer,
)
writer = JournalWriter.create(
    write_capability=capability,
    session_manifest=manifest,
)
wal = root / "sessions" / f"{manifest.session_id}.wal"
before = wal.stat().st_size
clock.now_ns = manifest.required_retention_until_ns
try:
    writer.close_halted(
        reason="operator_halt",
        trace_sha256="0" * 64,
        final_state_sha256="1" * 64,
        last_applied_raw_seq=0,
    )
except Exception:
    if wal.exists() and wal.stat().st_size != before:
        raise SystemExit("due session appended a terminal")
    print("due-denied")
else:
    raise SystemExit("due session wrote a terminal")
finally:
    coordinator.close()
"""
        due_result = child(due_code, str(self.root / "due-halt"))
        self.assertEqual(due_result.returncode, 0, due_result.stderr)
        self.assertEqual(due_result.stdout.strip(), "due-denied")

    def test_reserve_is_marker_bound_and_recovered_or_purged_after_crash(self):
        writer, _ = self.writer_for()
        marker = json.loads(
            (
                self.root
                / "state"
                / "retention-markers"
                / f"{self.manifest.session_id}.marker.json"
            ).read_bytes()
        )
        reserve = self.root / "state" / "sessions" / marker["reserve_basename"]
        self.assertEqual(reserve.stat().st_size, DISK_HALT_RESERVE_BYTES)
        writer._write_capability.close()
        self.coordinator.close()
        self.coordinator = RetentionCoordinator.acquire(
            make_config(self.root / "state"),
            clock_ns=self.clock,
        )
        report = self.coordinator.recover_and_purge()
        self.assertEqual(report.deleted_sessions, ())
        self.assertFalse(reserve.exists())

    def test_scan_summary_is_bounded_and_iter_records_streams_large_session(self):
        writer, authorizer = self.writer_for()
        raw_events: list[PersistedEvent] = []
        for index in range(4):
            raw_events.append(
                writer.append_raw(
                    self.capture(
                        authorizer,
                        provider_sequence=f"S-{index}",
                    )
                )
            )
        writer.close_clean(
            reason="operator_stop",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=raw_events[-1].ingest_seq,
        )
        with self.open_reader(authorizer) as reader:
            summary = reader.scan()
            iterator = reader.iter_records()
            self.assertNotIsInstance(iterator, (tuple, list))
            records = tuple(iterator)
            self.assertEqual(len(records), 6)
        self.assertEqual(summary.raw_count, 4)
        self.assertFalse(
            any(
                isinstance(getattr(summary, item.name), (list, tuple, dict))
                for item in fields(summary)
            )
        )

        start = records[0]
        raw_template = records[1]
        terminal_template = records[-1]

        def wal_bytes(raw_total: int) -> bytes:
            raw_frames = [
                encode_frame(
                    replace(
                        raw_template,
                        ingest_seq=index + 2,
                        provider_sequence=f"S-{index}",
                    )
                )
                for index in range(raw_total)
            ]
            terminal_payload = json.loads(terminal_template.payload)
            terminal_payload.update(
                {
                    "record_count_before_terminal": raw_total + 1,
                    "raw_count": raw_total,
                    "derived_count": 0,
                    "last_applied_raw_seq": raw_total + 1,
                }
            )
            payload = canonical_json_bytes(terminal_payload)
            terminal = replace(
                terminal_template,
                ingest_seq=raw_total + 2,
                payload=payload,
                payload_sha256=hashlib.sha256(payload).hexdigest(),
            )
            return (
                FILE_PREFIX.pack(
                    FILE_MAGIC,
                    FILE_VERSION,
                    FILE_FLAGS,
                    FILE_PREFIX.size,
                )
                + encode_frame(start)
                + b"".join(raw_frames)
                + encode_frame(terminal)
            )

        memory_content = [wal_bytes(16)]
        capability = self.coordinator.issue_read_capability(
            persistence_authorizer=authorizer,
        )

        def memory_pread(_capability, *, offset, length):
            return memory_content[0][offset : offset + length]

        with (
            mock.patch.object(
                ProviderWalReadCapability,
                "pread",
                new=memory_pread,
            ),
            JournalReader.open(read_capability=capability) as reader,
        ):
            peaks = []
            for raw_total in (16, 4096):
                memory_content[0] = wal_bytes(raw_total)
                gc.collect()
                tracemalloc.start()
                reader.scan(require_clean=True)
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                peaks.append(peak)
        self.assertLessEqual(peaks[1], peaks[0] + 64 * 1024)

    def test_secret_sentinel_is_absent_from_every_wal_byte_repr_and_error(self):
        writer, authorizer = self.writer_for()
        captured = self.capture(
            authorizer,
            raw=b'{"password":"NEVER_STORE_THIS"}',
            redacted=True,
        )
        raw = writer.append_raw(captured)
        writer.close_clean(
            reason="operator_stop",
            trace_sha256="0" * 64,
            final_state_sha256="1" * 64,
            last_applied_raw_seq=raw.ingest_seq,
        )
        self.assertNotIn(b"NEVER_STORE_THIS", self.wal_path().read_bytes())
        self.assertNotIn("NEVER_STORE_THIS", repr(raw))
        with self.assertRaises(JournalValidationError) as caught:
            writer.append_raw(captured)
        self.assertNotIn("NEVER_STORE_THIS", str(caught.exception))

    def test_reader_requires_coordinator_issued_read_capability(self):
        code = r"""
from tennis_v1.retention import ProviderWalReadCapability, RetentionError
from tennis_v1.wal import JournalReader
from tests.tennis_v1.test_sequencer import concrete_environment

with concrete_environment() as (_, coordinator, _, _):
    forged = object.__new__(ProviderWalReadCapability)
    object.__setattr__(forged, "_dispatch", coordinator)
    try:
        JournalReader.open(read_capability=forged)
    except RetentionError:
        pass
    else:
        raise SystemExit("forged read capability was accepted")
print("forged-reader-rejected")
"""
        result = subprocess.run(
            [PYTHON, "-c", code],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "forged-reader-rejected")

    def test_reader_create_claims_the_exact_read_capability_once(self):
        authorizer, _, _ = self.close_clean_with_one_raw()
        capability = self.coordinator.issue_read_capability(
            persistence_authorizer=authorizer,
        )
        authority = self.coordinator._read_capabilities[capability]
        self.assertFalse(authority.reader_claimed)

        with JournalReader.create(read_capability=capability) as reader:
            self.assertTrue(authority.reader_claimed)
            self.assertEqual(reader.scan(require_clean=True).raw_count, 1)

    def test_reader_creation_failure_closes_and_tombstones_claimed_authority(self):
        authorizer, _, _ = self.close_clean_with_one_raw()
        capability = self.coordinator.issue_read_capability(
            persistence_authorizer=authorizer,
        )
        closed: list[ProviderWalReadCapability] = []
        original_close = ProviderWalReadCapability.close

        def close(item):
            closed.append(item)
            return original_close(item)

        with (
            mock.patch.object(
                ProviderWalReadCapability,
                "pread",
                return_value=b"invalid-zero-length-probe",
            ),
            mock.patch.object(
                ProviderWalReadCapability,
                "close",
                close,
            ),
        ):
            with self.assertRaises(JournalCorruptionError):
                JournalReader.create(read_capability=capability)

        self.assertEqual(closed, [capability])
        self.assertNotIn(capability, self.coordinator._read_capabilities)
        self.assertIn(capability, self.coordinator._read_tombstones)

    def test_reader_open_is_only_a_compatibility_delegate_to_one_shot_create(self):
        capability = object()
        expected = object()
        with mock.patch.object(
            JournalReader,
            "create",
            return_value=expected,
        ) as create:
            observed = JournalReader.open(  # type: ignore[arg-type]
                read_capability=capability,
            )
        self.assertIs(observed, expected)
        create.assert_called_once_with(read_capability=capability)

    def test_reader_open_accepts_only_opaque_capability_not_path_fd_or_callback(self):
        with self.assertRaises(TypeError):
            JournalReader.open(read_capability=self.wal_path())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            JournalReader.open(read_capability=3)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            JournalReader.open(read_capability=lambda *_: b"")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            JournalReader.open(  # type: ignore[call-arg]
                path=self.wal_path(),
            )

    def test_every_header_metadata_payload_digest_and_trailer_read_uses_coordinator(self):
        authorizer, _, original = self.close_clean_with_one_raw()
        calls: list[tuple[int, int]] = []
        original_pread = RetentionCoordinator._pread_capability

        def tracked(coordinator, capability, *, offset, length):
            if coordinator is not self.coordinator:
                return original_pread(
                    coordinator,
                    capability,
                    offset=offset,
                    length=length,
                )
            calls.append((offset, length))
            return original_pread(
                coordinator,
                capability,
                offset=offset,
                length=length,
            )

        with mock.patch.object(
            RetentionCoordinator,
            "_pread_capability",
            new=tracked,
        ):
            with self.open_reader(authorizer) as reader:
                summary = reader.scan()
        expected = [(0, 0), (0, FILE_PREFIX.size)]
        for start, _ in frame_ranges(original):
            prefix = original[start : start + FRAME_PREFIX.size]
            metadata_length = FRAME_PREFIX.unpack(prefix)[6]
            payload_length = FRAME_PREFIX.unpack(prefix)[7]
            metadata_offset = start + FRAME_PREFIX.size
            payload_offset = metadata_offset + metadata_length
            digest_offset = payload_offset + payload_length
            trailer_offset = digest_offset + 32
            expected.extend(
                (
                    (start, FRAME_PREFIX.size),
                    (metadata_offset, metadata_length),
                    (payload_offset, payload_length),
                    (digest_offset, 32),
                    (trailer_offset, FRAME_TRAILER.size),
                )
            )
        expected.append((len(original), FRAME_PREFIX.size))
        self.assertEqual(calls, expected)
        self.assertEqual(summary.file_size, len(original))

    def test_reader_rechecks_capability_before_each_bounded_range_and_never_read_aheads(self):
        authorizer, _, _ = self.close_clean_with_one_raw()
        before = authorizer.analysis_calls
        lengths: list[int] = []
        original_pread = RetentionCoordinator._pread_capability

        def tracked(coordinator, capability, *, offset, length):
            if coordinator is not self.coordinator:
                return original_pread(
                    coordinator,
                    capability,
                    offset=offset,
                    length=length,
                )
            lengths.append(length)
            return original_pread(
                coordinator,
                capability,
                offset=offset,
                length=length,
            )

        with mock.patch.object(
            RetentionCoordinator,
            "_pread_capability",
            new=tracked,
        ):
            with self.open_reader(authorizer) as reader:
                reader.scan()
        self.assertGreaterEqual(
            authorizer.analysis_calls - before,
            len(lengths),
        )
        self.assertTrue(all(length <= MAX_FRAME_BYTES for length in lengths))
        self.assertEqual(lengths[:2], [0, FILE_PREFIX.size])

    def test_every_terminal_tail_cut_is_torn_and_missing_boundary_is_distinct(self):
        authorizer, raw, original = self.close_clean_with_one_raw()
        terminal_start, _ = frame_ranges(original)[-1]
        content = [original]
        capability = self.coordinator.issue_read_capability(
            persistence_authorizer=authorizer,
        )

        def memory_pread(_capability, *, offset, length):
            return content[0][offset : offset + length]

        with (
            mock.patch.object(
                ProviderWalReadCapability,
                "pread",
                new=memory_pread,
            ),
            JournalReader.open(read_capability=capability) as reader,
        ):
            content[0] = original[:terminal_start]
            missing = reader.scan()
            self.assertEqual(missing.issue, ScanIssue.MISSING_TERMINAL)
            self.assertTrue(missing.wal_valid)
            self.assertEqual(missing.last_good_ingest_seq, raw.ingest_seq)
            for cut in range(terminal_start + 1, len(original)):
                with self.subTest(cut=cut):
                    content[0] = original[:cut]
                    summary = reader.scan()
                    self.assertEqual(summary.issue, ScanIssue.TORN_TAIL)
                    self.assertEqual(
                        summary.last_good_ingest_seq,
                        raw.ingest_seq,
                    )
                    self.assertFalse(summary.wal_valid)

    def test_append_derived_copies_exact_parent_provenance_time_and_retention_without_clock(self):
        writer, authorizer = self.writer_for()
        raw = writer.append_raw(self.capture(authorizer))
        draft = DerivedDraft(
            "raw_accepted",
            3,
            "canonical-json-v1",
            b'{"accepted":true}',
        )
        with mock.patch(
            "time.time_ns",
            side_effect=AssertionError("derived path sampled a clock"),
        ):
            derived = writer.append_derived(raw, draft)
        copied = (
            "session_id",
            "source_kind",
            "source_id",
            "source_entity_id",
            "endpoint_id",
            "endpoint_state",
            "channel_id",
            "channel_state",
            "request_id",
            "request_id_state",
            "source_wall_ns",
            "source_generated_ns",
            "local_wall_ns",
            "local_monotonic_ns",
            "clock_uncertainty_ns",
            "connection_epoch",
            "provider_sequence",
            "retention_delete_by_ns",
        )
        self.assertEqual(
            tuple(getattr(derived, name) for name in copied),
            tuple(getattr(raw, name) for name in copied),
        )
        self.assertEqual(
            (
                derived.record_kind,
                derived.event_type,
                derived.event_version,
                derived.parent_ingest_seq,
                derived.content_type,
                derived.payload_encoding,
                derived.payload_transform,
                derived.payload,
            ),
            (
                RecordKind.DERIVED,
                draft.event_type,
                draft.event_version,
                raw.ingest_seq,
                "application/vnd.inci.derived+json",
                draft.payload_encoding,
                "derived-canonical-v1",
                draft.payload,
            ),
        )
        source = ast.parse(
            (
                Path(__file__).resolve().parents[2]
                / "tennis_v1"
                / "wal.py"
            ).read_text(encoding="utf-8")
        )
        imported_roots = {
            item.name.split(".", 1)[0]
            for node in ast.walk(source)
            if isinstance(node, ast.Import)
            for item in node.names
        } | {
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(source)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertNotIn("time", imported_roots)

    def test_replay_iterator_yields_false_terminal_then_returns_mechanical_summary(self):
        authorizer, _, original = self.close_clean_with_one_raw()
        with self.open_reader(authorizer) as reader:
            records = tuple(reader.iter_records())
        terminal_payload = json.loads(records[-1].payload)
        terminal_payload["raw_count"] += 1
        encoded = canonical_json_bytes(terminal_payload)
        false_terminal = replace(
            records[-1],
            payload=encoded,
            payload_sha256=hashlib.sha256(encoded).hexdigest(),
        )
        self.wal_path().write_bytes(
            FILE_PREFIX.pack(
                FILE_MAGIC,
                FILE_VERSION,
                FILE_FLAGS,
                FILE_PREFIX.size,
            )
            + b"".join(
                encode_frame(item)
                for item in records[:-1] + (false_terminal,)
            )
        )
        with self.open_reader(authorizer) as reader:
            with self.assertRaisesRegex(
                JournalCorruptionError,
                "journal_terminal_binding_invalid",
            ):
                reader.scan()
        with self.open_reader(authorizer) as reader:
            iterator = reader.iter_replay_records()
            yielded: list[PersistedEvent] = []
            while True:
                try:
                    yielded.append(next(iterator))
                except StopIteration as stopped:
                    summary = stopped.value
                    break
        self.assertEqual(yielded[-1], false_terminal)
        self.assertEqual(summary.file_size, self.wal_path().stat().st_size)
        self.assertTrue(summary.wal_valid)
        self.assertTrue(summary.terminal_clean)
        self.assertIsNone(summary.issue)
        self.assertNotEqual(original, self.wal_path().read_bytes())

    def test_replay_iterator_returns_torn_summary_after_verified_prefix(self):
        authorizer, raw, original = self.close_clean_with_one_raw()
        terminal_start, _ = frame_ranges(original)[-1]
        self.wal_path().write_bytes(original[: terminal_start + 3])
        with self.open_reader(authorizer) as reader:
            iterator = reader.iter_replay_records()
            yielded: list[PersistedEvent] = []
            while True:
                try:
                    yielded.append(next(iterator))
                except StopIteration as stopped:
                    summary = stopped.value
                    break
        self.assertEqual(yielded[-1], raw)
        self.assertEqual(summary.issue, ScanIssue.TORN_TAIL)
        self.assertFalse(summary.wal_valid)
        self.assertEqual(summary.last_good_ingest_seq, raw.ingest_seq)

    def test_published_constants_match_frozen_binary_and_capacity_contract(self):
        self.assertEqual(FILE_PREFIX.format, ">8sHHI")
        self.assertEqual(FILE_PREFIX.size, 16)
        self.assertEqual(FRAME_PREFIX.format, ">4sBBHQQII")
        self.assertEqual(FRAME_PREFIX.size, 32)
        self.assertEqual(FRAME_TRAILER.format, ">Q4s")
        self.assertEqual(FRAME_TRAILER.size, 12)
        self.assertEqual(MAX_FRAME_BYTES, 16 * 1024 * 1024)
        self.assertEqual(MIN_FREE_BYTES, 64 * 1024 * 1024)
        self.assertEqual(DISK_HALT_RESERVE_BYTES, 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
