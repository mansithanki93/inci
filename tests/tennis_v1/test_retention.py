from __future__ import annotations

import ast
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import gc
import hashlib
import inspect
import json
import os
from pathlib import Path
import pickle
import stat
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
import uuid
import weakref
from unittest import mock

import tennis_v1.retention as retention_module
from tennis_v1.canonical import canonical_json_bytes
from tennis_v1.codec import encode_record
from tennis_v1.config import TennisV1Config
from tennis_v1.entitlements import (
    QualificationDecision,
    QualificationReason,
    QualifiedProviderBinding,
    provider_request_binding_sha256,
)
from tennis_v1.events import (
    PersistedEvent,
    ProvenanceState,
    RecordKind,
    SessionManifest,
    SourceKind,
)
from tennis_v1.retention import (
    ProviderWalReadCapability,
    ProviderWalWriteCapability,
    RetentionCoordinator,
    RetentionDueDeleteError,
    RetentionError,
    RetentionGlobalHalt,
)
from tennis_v1.session import (
    canonical_session_manifest_bytes,
    session_manifest_sha256,
)


PYTHON = sys.executable
FRAME_PREFIX = struct.Struct(">4sBBHQQII")
FRAME_TRAILER = struct.Struct(">Q4s")
FRAME_DOMAIN = b"INCI-FRAME-V1\0"
WAL_FILE_PREFIX = struct.Struct(">8sHHI")
WAL_FILE_PREFIX_BYTES = WAL_FILE_PREFIX.pack(
    b"INCIWAL\x00",
    1,
    0,
    WAL_FILE_PREFIX.size,
)
SESSION_ID = "12345678-1234-4234-8234-123456789abc"
SECOND_SESSION_ID = "87654321-4321-4321-8321-cba987654321"


class MutableClock:
    def __init__(self, now_ns: int):
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns


class CountingMutableClock(MutableClock):
    def __init__(self, now_ns: int):
        super().__init__(now_ns)
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        return super().__call__()


class StrictAuthorizer:
    def __init__(
        self,
        coordinator: RetentionCoordinator,
        manifest: SessionManifest,
        decision: QualificationDecision,
    ):
        self.coordinator = coordinator
        self.session_manifest = manifest
        self.bound_decision = decision
        self.raw_deadline = manifest.required_retention_until_ns + 1_000_000_000
        self.session_calls = 0
        self.raw_calls = 0
        self.analysis_calls = 0
        self.close_calls = 0

    def authorize_session(self) -> None:
        self.session_calls += 1

    def authorize_raw_persistence(self) -> int:
        self.raw_calls += 1
        return self.raw_deadline

    def authorize_analysis(self) -> QualificationDecision:
        self.analysis_calls += 1
        return self.bound_decision

    def authorize_close(self) -> None:
        self.close_calls += 1


def install_callback_swap(
    authorizer: StrictAuthorizer,
    callback_name: str,
    property_name: str,
    replacement_value: object,
) -> None:
    original = getattr(authorizer, callback_name)

    def swap_after_callback():
        result = original()
        setattr(authorizer, property_name, replacement_value)
        return result

    setattr(authorizer, callback_name, swap_after_callback)


def _ns(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = value - epoch
    return (
        delta.days * 86_400_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def make_manifest_decision(
    session_id: str = SESSION_ID,
    *,
    start: datetime | None = None,
) -> tuple[SessionManifest, QualificationDecision]:
    start = start or datetime(2030, 1, 1, tzinfo=timezone.utc)
    session_end = start + timedelta(hours=1)
    delete_by = start + timedelta(hours=2)
    access_expires = start + timedelta(hours=3)
    analysis_expires = start + timedelta(hours=4)
    binding = QualifiedProviderBinding(
        provider_id="provider",
        product_tier="trial",
        source_lineage_id="lineage",
        entitlement_id_sha256="1" * 64,
        manifest_file_sha256="2" * 64,
        manifest_canonical_sha256="3" * 64,
        qualification_artifact_sha256="4" * 64,
        permission_artifact_sha256="5" * 64,
        qualification_trace_sha256="6" * 64,
        adapter_code_sha256="7" * 64,
        auth_contract_sha256="8" * 64,
        quota_contract_sha256="9" * 64,
        session_end_utc=session_end,
        required_retention_until=delete_by,
        access_expires_at=access_expires,
        analysis_expires_at=analysis_expires,
        qualified_until=analysis_expires,
    )
    decision = QualificationDecision(
        eligible=True,
        reasons=(QualificationReason.ELIGIBLE,),
        export_allowed=False,
        manifest_file_sha256=binding.manifest_file_sha256,
        manifest_canonical_sha256=binding.manifest_canonical_sha256,
        request_sha256="a" * 64,
        provider_request_binding_sha256=None,
        binding=binding,
    )
    decision = replace(
        decision,
        provider_request_binding_sha256=provider_request_binding_sha256(decision),
    )
    manifest = SessionManifest(
        schema_version=1,
        session_id=session_id,
        created_wall_ns=_ns(start) - 1_000_000_000,
        config_file_sha256="b" * 64,
        config_canonical_sha256="c" * 64,
        code_sha256="d" * 64,
        research_request_sha256=decision.request_sha256,
        provider_id=binding.provider_id,
        product_tier=binding.product_tier,
        source_lineage_id=binding.source_lineage_id,
        provider_manifest_file_sha256=binding.manifest_file_sha256,
        provider_manifest_canonical_sha256=binding.manifest_canonical_sha256,
        entitlement_id_sha256=binding.entitlement_id_sha256,
        terms_version="terms-v1",
        permission_artifact_sha256=binding.permission_artifact_sha256,
        qualification_artifact_sha256=binding.qualification_artifact_sha256,
        qualification_trace_sha256=binding.qualification_trace_sha256,
        adapter_code_sha256=binding.adapter_code_sha256,
        auth_contract_sha256=binding.auth_contract_sha256,
        quota_contract_sha256=binding.quota_contract_sha256,
        session_end_ns=_ns(session_end),
        required_retention_until_ns=_ns(delete_by),
        access_expires_at_ns=_ns(access_expires),
        analysis_expires_at_ns=_ns(analysis_expires),
        research_evaluable=False,
    )
    return manifest, decision


def make_config(state_root: Path) -> TennisV1Config:
    return TennisV1Config(
        schema_version=1,
        state_root=state_root,
        provider_manifest_path=state_root.parent / "provider.json",
        provider_manifest_sha256="2" * 64,
        trusted_permission_reviewer_ids=("reviewer",),
        trusted_qualification_issuer_ids=("issuer",),
        observed_pool_limit=1,
        paper_position_limit=1,
        source_file_sha256="e" * 64,
        canonical_sha256="f" * 64,
    )


def terminal_frame(
    manifest: SessionManifest,
    *,
    clean: bool,
    reason: str | None = None,
    ingest_seq: int = 2,
    record_count_before_terminal: int = 1,
    raw_count: int = 0,
    derived_count: int = 0,
    last_applied_raw_seq: int = 0,
) -> bytes:
    payload = canonical_json_bytes(
        {
            "terminal_version": 1,
            "clean": clean,
            "reason": (
                reason
                if reason is not None
                else (
                    "operator_stop"
                    if clean
                    else "retention_global_halt"
                )
            ),
            "trace_sha256": "0" * 64,
            "final_state_sha256": "1" * 64,
            "record_count_before_terminal": record_count_before_terminal,
            "raw_count": raw_count,
            "derived_count": derived_count,
            "last_applied_raw_seq": last_applied_raw_seq,
            "config_file_sha256": manifest.config_file_sha256,
            "config_canonical_sha256": manifest.config_canonical_sha256,
            "code_sha256": manifest.code_sha256,
            "session_manifest_sha256": session_manifest_sha256(manifest),
            "provider_manifest_file_sha256": (
                manifest.provider_manifest_file_sha256
            ),
            "provider_manifest_canonical_sha256": (
                manifest.provider_manifest_canonical_sha256
            ),
            "entitlement_id_sha256": manifest.entitlement_id_sha256,
            "permission_artifact_sha256": (
                manifest.permission_artifact_sha256
            ),
            "qualification_artifact_sha256": (
                manifest.qualification_artifact_sha256
            ),
            "qualification_trace_sha256": (
                manifest.qualification_trace_sha256
            ),
            "adapter_code_sha256": manifest.adapter_code_sha256,
            "auth_contract_sha256": manifest.auth_contract_sha256,
            "quota_contract_sha256": manifest.quota_contract_sha256,
            "required_retention_until_ns": (
                manifest.required_retention_until_ns
            ),
            "research_evaluable": False,
        }
    )
    event = PersistedEvent(
        journal_version=1,
        record_kind=RecordKind.CONTROL,
        ingest_seq=ingest_seq,
        session_id=manifest.session_id,
        event_type="SESSION_HALT",
        event_version=1,
        source_kind=SourceKind.SYSTEM,
        source_id="tennis-v1",
        source_entity_id=manifest.session_id,
        endpoint_id=None,
        endpoint_state=ProvenanceState.ABSENT,
        channel_id="session-control",
        channel_state=ProvenanceState.SAFE_ORIGINAL,
        request_id=None,
        request_id_state=ProvenanceState.ABSENT,
        source_wall_ns=None,
        source_generated_ns=None,
        local_wall_ns=manifest.created_wall_ns,
        local_monotonic_ns=0,
        clock_uncertainty_ns=0,
        connection_epoch=0,
        provider_sequence=None,
        parent_ingest_seq=None,
        content_type="application/vnd.inci.session-terminal+json",
        payload_encoding="canonical-json-v1",
        payload_transform="identity-public-market-v1",
        retention_delete_by_ns=None,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload=payload,
    )
    metadata, payload = encode_record(event)
    total = 76 + len(metadata) + len(payload)
    prefix = FRAME_PREFIX.pack(
        b"EVT1", 1, 3, 0, ingest_seq, total, len(metadata), len(payload)
    )
    digest = hashlib.sha256(FRAME_DOMAIN + prefix + metadata + payload).digest()
    return prefix + metadata + payload + digest + FRAME_TRAILER.pack(total, b"1TVE")


def session_start_frame(manifest: SessionManifest) -> bytes:
    payload = canonical_session_manifest_bytes(manifest)
    event = PersistedEvent(
        journal_version=1,
        record_kind=RecordKind.CONTROL,
        ingest_seq=1,
        session_id=manifest.session_id,
        event_type="SESSION_START",
        event_version=1,
        source_kind=SourceKind.SYSTEM,
        source_id="tennis-v1",
        source_entity_id=manifest.session_id,
        endpoint_id=None,
        endpoint_state=ProvenanceState.ABSENT,
        channel_id="session-control",
        channel_state=ProvenanceState.SAFE_ORIGINAL,
        request_id=None,
        request_id_state=ProvenanceState.ABSENT,
        source_wall_ns=None,
        source_generated_ns=None,
        local_wall_ns=manifest.created_wall_ns,
        local_monotonic_ns=0,
        clock_uncertainty_ns=0,
        connection_epoch=0,
        provider_sequence=None,
        parent_ingest_seq=None,
        content_type="application/vnd.inci.session-manifest+json",
        payload_encoding="canonical-json-v1",
        payload_transform="identity-public-market-v1",
        retention_delete_by_ns=None,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload=payload,
    )
    metadata, payload = encode_record(event)
    total = 76 + len(metadata) + len(payload)
    prefix = FRAME_PREFIX.pack(
        b"EVT1", 1, 3, 0, 1, total, len(metadata), len(payload)
    )
    digest = hashlib.sha256(FRAME_DOMAIN + prefix + metadata + payload).digest()
    return prefix + metadata + payload + digest + FRAME_TRAILER.pack(total, b"1TVE")


def ordinary_frame(
    manifest: SessionManifest,
    *,
    ingest_seq: int = 2,
) -> bytes:
    payload = canonical_json_bytes({"event": "ordinary"})
    event = PersistedEvent(
        journal_version=1,
        record_kind=RecordKind.RAW,
        ingest_seq=ingest_seq,
        session_id=manifest.session_id,
        event_type="provider.test",
        event_version=1,
        source_kind=SourceKind.PROVIDER,
        source_id=manifest.provider_id,
        source_entity_id="match-1",
        endpoint_id="test",
        endpoint_state=ProvenanceState.SAFE_ORIGINAL,
        channel_id=None,
        channel_state=ProvenanceState.ABSENT,
        request_id=None,
        request_id_state=ProvenanceState.ABSENT,
        source_wall_ns=manifest.created_wall_ns,
        source_generated_ns=manifest.created_wall_ns,
        local_wall_ns=manifest.created_wall_ns,
        local_monotonic_ns=ingest_seq,
        clock_uncertainty_ns=0,
        connection_epoch=0,
        provider_sequence=f"test-{ingest_seq}",
        parent_ingest_seq=None,
        content_type="application/json",
        payload_encoding="json",
        payload_transform="identity-public-market-v1",
        retention_delete_by_ns=manifest.required_retention_until_ns,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload=payload,
    )
    metadata, payload = encode_record(event)
    total = 76 + len(metadata) + len(payload)
    prefix = FRAME_PREFIX.pack(
        b"EVT1", 1, 1, 0, ingest_seq, total, len(metadata), len(payload)
    )
    digest = hashlib.sha256(FRAME_DOMAIN + prefix + metadata + payload).digest()
    return prefix + metadata + payload + digest + FRAME_TRAILER.pack(total, b"1TVE")


def bootstrap_capability(
    capability: ProviderWalWriteCapability,
    manifest: SessionManifest,
) -> bytes:
    retention_module._claim_provider_wal_writer(
        write_capability=capability,
        session_manifest=manifest,
    )
    start = session_start_frame(manifest)
    capability.write_all(WAL_FILE_PREFIX_BYTES)
    capability.write_all(start)
    return WAL_FILE_PREFIX_BYTES + start


def mutate_terminal_frame(frame: bytes, mode: str) -> bytes:
    (
        _magic,
        _version,
        kind,
        _flags,
        ingest_seq,
        _total,
        metadata_length,
        payload_length,
    ) = FRAME_PREFIX.unpack(frame[: FRAME_PREFIX.size])
    metadata_start = FRAME_PREFIX.size
    payload_start = metadata_start + metadata_length
    digest_start = payload_start + payload_length
    metadata = frame[metadata_start:payload_start]
    payload = frame[payload_start:digest_start]
    if mode == "length":
        return frame[:-1]
    if mode == "digest":
        changed = bytearray(frame)
        changed[digest_start] ^= 1
        return bytes(changed)
    if mode == "trailer":
        return frame[:-4] + b"EVIL"
    if mode == "metadata_contract":
        raw_metadata = json.loads(metadata)
        raw_metadata["content_type"] = "application/json"
        metadata = canonical_json_bytes(raw_metadata)
    elif mode == "payload_keys":
        raw_payload = json.loads(payload)
        raw_payload.pop("trace_sha256")
        payload = canonical_json_bytes(raw_payload)
    elif mode == "session_binding":
        raw_metadata = json.loads(metadata)
        raw_metadata["session_id"] = SECOND_SESSION_ID
        metadata = canonical_json_bytes(raw_metadata)
    elif mode == "digest_binding":
        raw_payload = json.loads(payload)
        raw_payload["session_manifest_sha256"] = "f" * 64
        payload = canonical_json_bytes(raw_payload)
    else:
        raise ValueError(f"unknown terminal mutation: {mode}")
    total = 76 + len(metadata) + len(payload)
    prefix = FRAME_PREFIX.pack(
        b"EVT1",
        1,
        kind,
        0,
        ingest_seq,
        total,
        len(metadata),
        len(payload),
    )
    digest = hashlib.sha256(FRAME_DOMAIN + prefix + metadata + payload).digest()
    return prefix + metadata + payload + digest + FRAME_TRAILER.pack(total, b"1TVE")


def child(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-c", script, *arguments],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )


class RetentionTests(unittest.TestCase):
    def test_marker_post_init_rejects_subclasses_before_property_dispatch(self):
        calls: list[str] = []

        class HostileRetentionMarker(retention_module.RetentionMarker):
            def __getattribute__(self, name):
                calls.append(name)
                if name == "schema_version":
                    return 1
                return super().__getattribute__(name)

        hostile = object.__new__(HostileRetentionMarker)
        with self.assertRaisesRegex(
            TypeError,
            r"\Aexact RetentionMarker required\Z",
        ):
            retention_module.RetentionMarker.__post_init__(hostile)
        self.assertEqual(calls, [])

    def test_marker_projection_rejects_subclass_before_property_dispatch(self):
        calls: list[str] = []

        class HostileRetentionMarker(retention_module.RetentionMarker):
            def __getattribute__(self, name):
                calls.append(name)
                return super().__getattribute__(name)

        hostile = object.__new__(HostileRetentionMarker)
        with self.assertRaisesRegex(
            TypeError,
            r"\Aexact RetentionMarker required\Z",
        ):
            retention_module._marker_projection(hostile)
        self.assertEqual(calls, [])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.state_root = self.root / "state"
        self.config = make_config(self.state_root)
        self.manifest, self.decision = make_manifest_decision()
        self.clock = MutableClock(self.manifest.created_wall_ns)
        self.coordinators: list[RetentionCoordinator] = []

    def tearDown(self) -> None:
        for coordinator in reversed(self.coordinators):
            coordinator.close()
        self.temporary.cleanup()

    def acquire(self, *, recover: bool = True) -> RetentionCoordinator:
        coordinator = RetentionCoordinator.acquire(
            self.config, clock_ns=self.clock
        )
        self.coordinators.append(coordinator)
        if recover:
            self.assertEqual(
                coordinator.recover_and_purge(),
                retention_report(),
            )
        return coordinator

    def acquire_expert_grant(
        self,
        suffix: str,
        *,
        clock: MutableClock | None = None,
    ) -> tuple[RetentionCoordinator, object, object, object, tuple[int, ...]]:
        coordinator = RetentionCoordinator.acquire(
            make_config(self.root / f"expert-matrix-{suffix}"),
            clock_ns=clock or self.clock,
        )
        self.coordinators.append(coordinator)
        self.assertEqual(
            coordinator.recover_and_purge(),
            retention_report(),
        )
        request = (
            coordinator.issue_expert_state_root_account_lock_request()
        )
        grant = (
            retention_module._consume_expert_state_root_account_lock_request(
                request
            )
        )
        sampler = object.__getattribute__(grant, "_clock_capability")
        authority = coordinator._expert_clock_capabilities[sampler]
        duplicate_fds = tuple(
            object.__getattribute__(grant, name)
            for name in (
                "_state_fd",
                "_sessions_fd",
                "_markers_fd",
                "_lock_fd",
            )
        )
        return coordinator, grant, sampler, authority, duplicate_fds

    def acquire_prearm_expert_grant(
        self,
        suffix: str,
    ):
        coordinator = RetentionCoordinator.acquire(
            make_config(self.root / f"expert-prearm-{suffix}"),
            clock_ns=self.clock,
        )
        self.coordinators.append(coordinator)
        self.assertEqual(
            coordinator.recover_and_purge(),
            retention_report(),
        )
        authorizer = StrictAuthorizer(
            coordinator,
            self.manifest,
            self.decision,
        )
        request = (
            coordinator.issue_expert_state_root_account_lock_request()
        )
        grant = (
            retention_module._consume_expert_state_root_account_lock_request(
                request
            )
        )
        sampler = object.__getattribute__(grant, "_clock_capability")
        authority = coordinator._expert_clock_capabilities[sampler]
        duplicate_fds = tuple(
            object.__getattribute__(grant, name)
            for name in (
                "_state_fd",
                "_sessions_fd",
                "_markers_fd",
                "_lock_fd",
            )
        )
        return (
            coordinator,
            authorizer,
            sampler,
            authority,
            duplicate_fds,
        )

    def assert_expert_sample_rejects_and_revokes(
        self,
        coordinator: RetentionCoordinator,
        sampler: object,
        duplicate_fds: tuple[int, ...],
        *,
        clock: CountingMutableClock | None = None,
    ) -> None:
        prior_clock_calls = None if clock is None else clock.calls
        with self.assertRaises(RetentionError):
            retention_module.sample_expert_retention_wall_ns(sampler)
        if prior_clock_calls is not None:
            self.assertEqual(clock.calls, prior_clock_calls)
        self.assertEqual(coordinator._expert_root_grants, {})
        self.assertEqual(coordinator._expert_clock_capabilities, {})
        with self.assertRaisesRegex(
            RetentionError,
            r"\Aexpert_retention_clock_capability_stale\Z",
        ):
            retention_module.sample_expert_retention_wall_ns(sampler)
        for fd in duplicate_fds:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def assert_prearm_refresh_failed_closed(
        self,
        coordinator: RetentionCoordinator,
        sampler: object,
        authority: object,
        duplicate_fds: tuple[int, ...],
        *,
        sessions_before: object,
        markers_before: object,
    ) -> None:
        self.assertEqual(
            authority.sessions_identity,
            sessions_before,
        )
        self.assertEqual(
            authority.markers_identity,
            markers_before,
        )
        self.assert_expert_sample_rejects_and_revokes(
            coordinator,
            sampler,
            duplicate_fds,
        )

    def arm(
        self,
        coordinator: RetentionCoordinator,
        manifest: SessionManifest | None = None,
        decision: QualificationDecision | None = None,
    ) -> tuple[ProviderWalWriteCapability, StrictAuthorizer]:
        manifest = manifest or self.manifest
        decision = decision or self.decision
        authorizer = StrictAuthorizer(coordinator, manifest, decision)
        capability = coordinator.arm_before_wal(
            session_manifest=manifest,
            decision=decision,
            persistence_authorizer=authorizer,
        )
        return capability, authorizer

    def close_current(self) -> None:
        coordinator = self.coordinators.pop()
        coordinator.close()

    def _run_expert_global_halt_script(
        self,
        body: str,
        *,
        marker: str,
    ) -> None:
        code = f"""
import os
from pathlib import Path
import tempfile
from unittest import mock

from tennis_v1.retention import (
    RetentionCoordinator,
    RetentionError,
    RetentionGlobalHalt,
    _consume_expert_state_root_account_lock_request,
    _latch_global_halt,
    _revoke_expert_state_root_account_lock_grant,
    sample_expert_retention_wall_ns,
)
from tests.tennis_v1.test_retention import make_config

temporary = tempfile.TemporaryDirectory()
coordinator = RetentionCoordinator.acquire(
    make_config(Path(temporary.name).resolve() / "state"),
    clock_ns=lambda: 123,
)
try:
    coordinator.recover_and_purge()
{textwrap.indent(body.strip(), "    ")}
finally:
    coordinator.close()
    temporary.cleanup()
print({marker!r})
"""
        completed = subprocess.run(
            [
                "/Users/mthanki/.venvs/inci-expert-py314/bin/python",
                "-B",
                "-c",
                code,
            ],
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
        self.assertEqual(completed.stdout.strip(), marker)
        self.assertNotIn("secret", completed.stdout + completed.stderr)

    def _run_expert_close_race_script(self, mode: str) -> None:
        code = r"""
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest import mock

from tennis_v1.retention import (
    RetentionCoordinator,
    RetentionError,
    _consume_expert_state_root_account_lock_request,
    _revoke_expert_state_root_account_lock_grant,
    sample_expert_retention_wall_ns,
)
from tests.tennis_v1.test_retention import make_config

mode = sys.argv[1]
assert mode in {"issue", "consume", "revoke", "sample"}

with tempfile.TemporaryDirectory() as temporary:
    operation_entered = threading.Event()
    close_done = threading.Event()
    close_errors = []
    clock_blocks = False

    def clock():
        if clock_blocks:
            operation_entered.set()
            if not close_waiting.wait(5):
                raise AssertionError("close did not reach sample seam")
            if close_done.is_set():
                raise AssertionError("close completed with sample in flight")
        return 123

    coordinator = RetentionCoordinator.acquire(
        make_config(Path(temporary).resolve() / "state"),
        clock_ns=clock,
    )
    coordinator.recover_and_purge()
    original_condition_wait = coordinator._condition.wait
    close_waiting = threading.Event()
    close_wait_states = []

    def tracked_condition_wait(timeout=None):
        if threading.current_thread().name == "expert-close-race":
            close_wait_states.append(coordinator._closing)
            close_waiting.set()
        return original_condition_wait(timeout)

    coordinator._condition.wait = tracked_condition_wait
    request = None
    grant = None
    sampler = None
    duplicate_fds = ()
    if mode in {"consume", "revoke", "sample"}:
        request = coordinator.issue_expert_state_root_account_lock_request()
    if mode in {"revoke", "sample"}:
        grant = _consume_expert_state_root_account_lock_request(request)
        sampler = object.__getattribute__(grant, "_clock_capability")
        duplicate_fds = tuple(
            object.__getattribute__(grant, name)
            for name in (
                "_state_fd",
                "_sessions_fd",
                "_markers_fd",
                "_lock_fd",
            )
        )

    def close_coordinator():
        if not operation_entered.wait(5):
            close_errors.append(
                AssertionError("operation did not reach close seam")
            )
            close_done.set()
            return
        try:
            coordinator.close()
        except BaseException as error:
            close_errors.append(error)
        finally:
            close_done.set()

    thread = threading.Thread(
        target=close_coordinator,
        name="expert-close-race",
    )
    thread.start()

    operation_error = None
    if mode == "issue":
        original_validate = RetentionCoordinator._validate_roots_and_lock

        def blocking_validate(instance):
            assert instance is coordinator
            operation_entered.set()
            if not close_waiting.wait(5):
                raise AssertionError("close did not reach issue seam")
            if close_done.is_set():
                raise AssertionError("close completed with issue in flight")
            return original_validate(instance)

        with mock.patch.object(
            RetentionCoordinator,
            "_validate_roots_and_lock",
            autospec=True,
            side_effect=blocking_validate,
        ):
            try:
                request = (
                    coordinator.issue_expert_state_root_account_lock_request()
                )
            except RetentionError as error:
                operation_error = error
    elif mode == "consume":
        original_dup = os.dup
        duplicates = []

        def blocking_dup(fd):
            duplicate = original_dup(fd)
            duplicates.append(duplicate)
            if len(duplicates) == 1:
                operation_entered.set()
                if not close_waiting.wait(5):
                    raise AssertionError(
                        "close did not reach consume seam"
                    )
                if close_done.is_set():
                    raise AssertionError(
                        "close completed with consume in flight"
                    )
            return duplicate

        with mock.patch.object(os, "dup", side_effect=blocking_dup):
            try:
                grant = _consume_expert_state_root_account_lock_request(
                    request
                )
            except RetentionError as error:
                operation_error = error
        duplicate_fds = tuple(duplicates)
    elif mode == "revoke":
        original_close_duplicates = (
            RetentionCoordinator._close_expert_root_duplicate_fds
        )

        def blocking_close_duplicates(fds):
            operation_entered.set()
            if not close_waiting.wait(5):
                raise AssertionError("close did not reach revoke seam")
            if close_done.is_set():
                raise AssertionError("close completed with revoke in flight")
            return original_close_duplicates(fds)

        with mock.patch.object(
            RetentionCoordinator,
            "_close_expert_root_duplicate_fds",
            side_effect=blocking_close_duplicates,
        ):
            try:
                _revoke_expert_state_root_account_lock_grant(grant)
            except RetentionError as error:
                operation_error = error
    else:
        clock_blocks = True
        try:
            sample_expert_retention_wall_ns(sampler)
        except RetentionError as error:
            operation_error = error

    thread.join(timeout=5)
    coordinator._condition.wait = original_condition_wait
    assert close_done.is_set()
    assert not thread.is_alive()
    assert close_errors == []
    assert close_wait_states != []
    assert all(close_wait_states)
    assert coordinator._closed is True
    assert coordinator._expert_root_operations_inflight == 0
    assert coordinator._expert_root_requests == {}
    assert coordinator._expert_root_grants == {}
    assert coordinator._expert_clock_capabilities == {}

    if mode == "revoke":
        assert operation_error is None
    else:
        assert type(operation_error) is RetentionError
        expected_errors = {
            "issue": "retention_coordinator_closed",
            "consume": "expert_state_root_request_stale",
            "sample": "expert_state_root_grant_stale",
        }
        assert str(operation_error) == expected_errors[mode]
    if mode != "issue":
        for fd in duplicate_fds:
            try:
                os.fstat(fd)
            except OSError:
                pass
            else:
                raise AssertionError("close leaked expert descriptor")
print(f"expert-close-{mode}-race-ok")
"""
        try:
            completed = subprocess.run(
                [
                    "/Users/mthanki/.venvs/inci-expert-py314/bin/python",
                    "-B",
                    "-c",
                    code,
                    mode,
                ],
                cwd=Path(__file__).resolve().parents[2],
                text=True,
                capture_output=True,
                timeout=12,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or b""
            stderr = error.stderr or b""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            self.fail(stdout + stderr)
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        self.assertEqual(
            completed.stdout.strip(),
            f"expert-close-{mode}-race-ok",
        )

    def test_private_fsynced_marker_is_durable_before_wal_creation(self):
        descriptor_names: dict[int, str] = {}
        actions: list[tuple[str, str]] = []
        original_open = os.open
        original_fsync = os.fsync

        def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
            fd = original_open(path, flags, mode, dir_fd=dir_fd)
            descriptor_names[fd] = os.fspath(path)
            actions.append(("open", os.fspath(path)))
            return fd

        def tracked_fsync(fd):
            name = descriptor_names.get(fd, "?")
            actions.append(("fsync", name))
            if name.endswith(".marker.json") or name == "retention-markers":
                sessions = self.state_root / "sessions"
                self.assertEqual(list(sessions.iterdir()), [])
            return original_fsync(fd)

        with (
            mock.patch("tennis_v1.retention.os.open", side_effect=tracked_open),
            mock.patch("tennis_v1.retention.os.fsync", side_effect=tracked_fsync),
        ):
            coordinator = self.acquire()
            actions.clear()
            capability, _ = self.arm(coordinator)
            self.assertIsInstance(capability, ProviderWalWriteCapability)

        marker_sync = next(
            index
            for index, action in enumerate(actions)
            if action[0] == "fsync" and action[1].endswith(".marker.json")
        )
        marker_dir_sync = next(
            index
            for index, action in enumerate(actions)
            if action == ("fsync", "retention-markers")
        )
        wal_open = next(
            index
            for index, action in enumerate(actions)
            if action[0] == "open" and action[1].endswith(".wal")
        )
        self.assertLess(marker_sync, marker_dir_sync)
        self.assertLess(marker_dir_sync, wal_open)

    def test_marker_binds_exact_session_manifest_and_full_provider_request_sha(self):
        coordinator = self.acquire()
        self.arm(coordinator)
        marker_path = next((self.state_root / "retention-markers").iterdir())
        raw = json.loads(marker_path.read_bytes())
        self.assertEqual(raw["session_manifest_sha256"], session_manifest_sha256(self.manifest))
        self.assertEqual(
            raw["provider_request_binding_sha256"],
            self.decision.provider_request_binding_sha256,
        )
        self.assertEqual(raw["delete_by_ns"], self.manifest.required_retention_until_ns)
        self.assertEqual(raw["provider_manifest_file_sha256"], "2" * 64)
        self.assertEqual(raw["entitlement_id_sha256"], "1" * 64)
        schema = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "tennis_v1/schemas/retention-marker-v1.schema.json"
            ).read_bytes()
        )
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["required"]), set(raw))

    def test_arm_requires_same_prebuilt_manifest_bound_authorizer_and_decision(self):
        coordinator = self.acquire()
        equal_manifest = replace(self.manifest)
        equal_decision = replace(self.decision)
        cases = (
            (equal_manifest, self.decision, StrictAuthorizer(coordinator, self.manifest, self.decision)),
            (self.manifest, equal_decision, StrictAuthorizer(coordinator, self.manifest, self.decision)),
            (self.manifest, self.decision, StrictAuthorizer(coordinator, equal_manifest, self.decision)),
            (self.manifest, self.decision, StrictAuthorizer(coordinator, self.manifest, equal_decision)),
        )
        for manifest, decision, authorizer in cases:
            with self.subTest(manifest_identity=manifest is self.manifest, decision_identity=decision is self.decision):
                with self.assertRaises(RetentionError):
                    coordinator.arm_before_wal(
                        session_manifest=manifest,
                        decision=decision,
                        persistence_authorizer=authorizer,
                    )
                self.assertEqual(list((self.state_root / "sessions").iterdir()), [])
                self.assertEqual(list((self.state_root / "retention-markers").iterdir()), [])

    def test_authorizer_must_be_bound_to_the_same_coordinator_object(self):
        coordinator = self.acquire()
        other_root = self.root / "other"
        other_config = make_config(other_root)
        other = RetentionCoordinator.acquire(other_config, clock_ns=self.clock)
        self.coordinators.append(other)
        other.recover_and_purge()
        authorizer = StrictAuthorizer(other, self.manifest, self.decision)
        with self.assertRaises(RetentionError):
            coordinator.arm_before_wal(
                session_manifest=self.manifest,
                decision=self.decision,
                persistence_authorizer=authorizer,
            )
        with self.assertRaises(RetentionError):
            coordinator.issue_read_capability(persistence_authorizer=authorizer)

    def test_every_authorizer_callback_rechecks_every_binding_property(self):
        coordinator = self.acquire()
        other = RetentionCoordinator.acquire(
            make_config(self.root / "other-callback"),
            clock_ns=self.clock,
        )
        self.coordinators.append(other)
        other.recover_and_purge()

        def replacement_for(
            property_name: str,
            manifest: SessionManifest,
            decision: QualificationDecision,
        ) -> object:
            return {
                "coordinator": other,
                "session_manifest": replace(manifest),
                "bound_decision": replace(decision),
            }[property_name]

        for callback_name in (
            "authorize_session",
            "authorize_raw_persistence",
        ):
            for property_name in (
                "coordinator",
                "session_manifest",
                "bound_decision",
            ):
                with self.subTest(
                    public_path="arm",
                    callback=callback_name,
                    property=property_name,
                ):
                    manifest, decision = make_manifest_decision(str(uuid.uuid4()))
                    authorizer = StrictAuthorizer(
                        coordinator, manifest, decision
                    )
                    install_callback_swap(
                        authorizer,
                        callback_name,
                        property_name,
                        replacement_for(property_name, manifest, decision),
                    )
                    with self.assertRaises(RetentionError):
                        coordinator.arm_before_wal(
                            session_manifest=manifest,
                            decision=decision,
                            persistence_authorizer=authorizer,
                        )
                    self.assertFalse(
                        (
                            self.state_root
                            / "retention-markers"
                            / f"{manifest.session_id}.marker.json"
                        ).exists()
                    )
                    self.assertFalse(
                        (
                            self.state_root
                            / "sessions"
                            / f"{manifest.session_id}.wal"
                        ).exists()
                    )

        for public_path in ("write", "fsync"):
            for property_name in (
                "coordinator",
                "session_manifest",
                "bound_decision",
            ):
                with self.subTest(
                    public_path=public_path,
                    callback="authorize_raw_persistence",
                    property=property_name,
                ):
                    manifest, decision = make_manifest_decision(str(uuid.uuid4()))
                    capability, authorizer = self.arm(
                        coordinator, manifest, decision
                    )
                    bootstrap = bootstrap_capability(capability, manifest)
                    install_callback_swap(
                        authorizer,
                        "authorize_raw_persistence",
                        property_name,
                        replacement_for(property_name, manifest, decision),
                    )
                    wal = (
                        self.state_root
                        / "sessions"
                        / f"{manifest.session_id}.wal"
                    )
                    with self.assertRaises(RetentionError):
                        if public_path == "write":
                            capability.write_all(ordinary_frame(manifest))
                        else:
                            capability.fsync()
                    self.assertEqual(wal.read_bytes(), bootstrap)
                    capability.close()

        for public_path in ("issue_read", "pread"):
            for property_name in (
                "coordinator",
                "session_manifest",
                "bound_decision",
            ):
                with self.subTest(
                    public_path=public_path,
                    callback="authorize_analysis",
                    property=property_name,
                ):
                    manifest, decision = make_manifest_decision(str(uuid.uuid4()))
                    capability, authorizer = self.arm(
                        coordinator, manifest, decision
                    )
                    bootstrap_capability(capability, manifest)
                    capability.write_all(terminal_frame(manifest, clean=True))
                    if public_path == "issue_read":
                        before = len(coordinator._read_capabilities)
                        install_callback_swap(
                            authorizer,
                            "authorize_analysis",
                            property_name,
                            replacement_for(property_name, manifest, decision),
                        )
                        with self.assertRaises(RetentionError):
                            coordinator.issue_read_capability(
                                persistence_authorizer=authorizer
                            )
                        self.assertEqual(
                            len(coordinator._read_capabilities), before
                        )
                    else:
                        read_capability = coordinator.issue_read_capability(
                            persistence_authorizer=authorizer
                        )
                        install_callback_swap(
                            authorizer,
                            "authorize_analysis",
                            property_name,
                            replacement_for(property_name, manifest, decision),
                        )
                        with mock.patch(
                            "tennis_v1.retention.os.pread",
                            wraps=os.pread,
                        ) as pread:
                            with self.assertRaises(RetentionError):
                                read_capability.pread(offset=0, length=1)
                        pread.assert_not_called()
                        read_capability.close()
                    capability.close()

    def test_callback_identity_preserving_semantic_mutation_is_rejected(self):
        coordinator = self.acquire()
        for callback_name in (
            "authorize_session",
            "authorize_raw_persistence",
        ):
            with self.subTest(callback=callback_name):
                manifest, decision = make_manifest_decision(str(uuid.uuid4()))
                authorizer = StrictAuthorizer(coordinator, manifest, decision)
                original = getattr(authorizer, callback_name)

                def mutate_decision_after_callback(
                    original=original,
                    decision=decision,
                ):
                    result = original()
                    object.__setattr__(
                        decision,
                        "provider_request_binding_sha256",
                        "0" * 64,
                    )
                    return result

                setattr(
                    authorizer,
                    callback_name,
                    mutate_decision_after_callback,
                )
                with self.assertRaises(RetentionError):
                    coordinator.arm_before_wal(
                        session_manifest=manifest,
                        decision=decision,
                        persistence_authorizer=authorizer,
                    )
                self.assertFalse(
                    (
                        self.state_root
                        / "retention-markers"
                        / f"{manifest.session_id}.marker.json"
                    ).exists()
                )

        manifest, decision = make_manifest_decision(str(uuid.uuid4()))
        capability, authorizer = self.arm(coordinator, manifest, decision)
        bootstrap = bootstrap_capability(capability, manifest)
        original_raw = authorizer.authorize_raw_persistence

        def mutate_write_decision():
            result = original_raw()
            object.__setattr__(
                decision,
                "provider_request_binding_sha256",
                "0" * 64,
            )
            return result

        authorizer.authorize_raw_persistence = mutate_write_decision
        with self.assertRaises(RetentionError):
            capability.write_all(ordinary_frame(manifest))
        self.assertEqual(
            (
                self.state_root
                / "sessions"
                / f"{manifest.session_id}.wal"
            ).read_bytes(),
            bootstrap,
        )
        capability.close()

        for public_path in ("issue_read", "pread"):
            with self.subTest(public_path=public_path):
                manifest, decision = make_manifest_decision(str(uuid.uuid4()))
                capability, authorizer = self.arm(
                    coordinator,
                    manifest,
                    decision,
                )
                bootstrap_capability(capability, manifest)
                capability.write_all(terminal_frame(manifest, clean=True))
                read_capability = (
                    None
                    if public_path == "issue_read"
                    else coordinator.issue_read_capability(
                        persistence_authorizer=authorizer
                    )
                )
                original_analysis = authorizer.authorize_analysis

                def mutate_analysis_decision(
                    original_analysis=original_analysis,
                    decision=decision,
                ):
                    result = original_analysis()
                    object.__setattr__(
                        decision,
                        "provider_request_binding_sha256",
                        "0" * 64,
                    )
                    return result

                authorizer.authorize_analysis = mutate_analysis_decision
                with mock.patch(
                    "tennis_v1.retention.os.pread",
                    wraps=os.pread,
                ) as pread:
                    with self.assertRaises(RetentionError):
                        if read_capability is None:
                            coordinator.issue_read_capability(
                                persistence_authorizer=authorizer
                            )
                        else:
                            read_capability.pread(offset=0, length=1)
                pread.assert_not_called()
                if read_capability is not None:
                    read_capability.close()
                capability.close()

    def test_capability_authority_exists_only_in_private_registry_records(self):
        authoritative_names = (
            "_coordinator",
            "_token",
            "_session_id",
            "_manifest_sha256",
            "_binding_sha256",
            "_authorizer",
            "_owner_pid",
            "_owner_thread",
            "_generation",
            "_closed",
            "_halt_consumed",
        )
        for capability_type in (
            ProviderWalWriteCapability,
            ProviderWalReadCapability,
        ):
            with self.subTest(capability_type=capability_type.__name__):
                for name in authoritative_names:
                    self.assertNotIn(name, capability_type.__slots__)

        coordinator = self.acquire()
        write_manifest, write_decision = make_manifest_decision(
            str(uuid.uuid4())
        )
        write_capability, write_authorizer = self.arm(
            coordinator, write_manifest, write_decision
        )
        read_manifest, read_decision = make_manifest_decision(str(uuid.uuid4()))
        read_writer, read_authorizer = self.arm(
            coordinator, read_manifest, read_decision
        )
        bootstrap_capability(read_writer, read_manifest)
        read_writer.write_all(terminal_frame(read_manifest, clean=True))
        read_capability = coordinator.issue_read_capability(
            persistence_authorizer=read_authorizer
        )

        dependency_calls: list[str] = []

        def dependency(name: str):
            def tripwire(*_: object, **__: object):
                dependency_calls.append(name)
                raise AssertionError(f"{name} crossed")

            return tripwire

        errors: list[BaseException] = []

        def transfer_attack() -> None:
            for capability in (write_capability, read_capability):
                for name in authoritative_names:
                    try:
                        object.__setattr__(capability, name, object())
                    except AttributeError:
                        continue
                    errors.append(
                        AssertionError(f"authoritative slot exposed: {name}")
                    )
            _capture_exception(
                errors, lambda: write_capability.write_all(b"x")
            )
            _capture_exception(
                errors,
                lambda: read_capability.pread(offset=0, length=1),
            )

        original_clock = coordinator._clock_ns
        coordinator._clock_ns = dependency("clock")
        write_authorizer.authorize_raw_persistence = dependency("raw")
        read_authorizer.authorize_analysis = dependency("analysis")
        try:
            with (
                mock.patch(
                    "tennis_v1.retention.os.fstat",
                    side_effect=dependency("fstat"),
                ),
                mock.patch(
                    "tennis_v1.retention.os.stat",
                    side_effect=dependency("stat"),
                ),
                mock.patch(
                    "tennis_v1.retention._write_all",
                    side_effect=dependency("write"),
                ),
                mock.patch(
                    "tennis_v1.retention.os.pread",
                    side_effect=dependency("pread"),
                ),
            ):
                thread = threading.Thread(target=transfer_attack)
                thread.start()
                thread.join(2)
                self.assertFalse(thread.is_alive())
        finally:
            coordinator._clock_ns = original_clock
        self.assertEqual(dependency_calls, [])
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(isinstance(item, RetentionError) for item in errors))
        other = RetentionCoordinator.acquire(
            make_config(self.root / "dispatch-other"),
            clock_ns=dependency("other_clock"),
        )
        self.coordinators.append(other)
        other._ready = True
        object.__setattr__(write_capability, "_dispatch", other)
        try:
            with (
                mock.patch(
                    "tennis_v1.retention.os.fstat",
                    side_effect=dependency("dispatch_fstat"),
                ),
                mock.patch(
                    "tennis_v1.retention._write_all",
                    side_effect=dependency("dispatch_write"),
                ),
            ):
                with self.assertRaises(RetentionError):
                    write_capability.write_all(b"x")
        finally:
            object.__setattr__(write_capability, "_dispatch", coordinator)
        self.assertEqual(dependency_calls, [])
        read_capability.close()
        read_writer.close()
        write_capability.close()

    def test_writer_claim_returns_only_success_for_exact_capability_and_manifest(self):
        coordinator = self.acquire()
        capability, _ = self.arm(coordinator)

        result = retention_module._claim_provider_wal_writer(
            write_capability=capability,
            session_manifest=self.manifest,
        )

        self.assertIsNone(result)
        capability.write_all(WAL_FILE_PREFIX_BYTES)
        capability.close()

    def test_reader_claim_returns_only_success_for_exact_live_capability(self):
        coordinator = self.acquire()
        write_capability, authorizer = self.arm(coordinator)
        bootstrap_capability(write_capability, self.manifest)
        write_capability.write_all(terminal_frame(self.manifest, clean=True))
        read_capability = coordinator.issue_read_capability(
            persistence_authorizer=authorizer,
        )

        result = retention_module._claim_provider_wal_reader(
            read_capability=read_capability,
        )

        self.assertIsNone(result)
        self.assertTrue(
            coordinator._read_capabilities[read_capability].reader_claimed
        )
        read_capability.close()
        write_capability.close()

    def test_reader_claim_rejects_noncapability_without_crossing_dependencies(self):
        dependency_calls: list[str] = []

        def dependency(*_: object, **__: object):
            dependency_calls.append("crossed")
            raise AssertionError("dependency crossed")

        with (
            mock.patch(
                "tennis_v1.retention.os.fstat",
                side_effect=dependency,
            ),
            mock.patch(
                "tennis_v1.retention.os.stat",
                side_effect=dependency,
            ),
            mock.patch(
                "tennis_v1.retention.os.pread",
                side_effect=dependency,
            ),
        ):
            with self.assertRaises(RetentionError):
                retention_module._claim_provider_wal_reader(
                    read_capability=object(),
                )
        self.assertEqual(dependency_calls, [])

    def test_reader_claim_rejects_forged_wrong_stale_and_reused_capabilities(self):
        code = r"""
from pathlib import Path
import sys
from unittest import mock
import tennis_v1.retention as retention
from tests.tennis_v1.test_retention import (
    MutableClock,
    StrictAuthorizer,
    bootstrap_capability,
    make_config,
    make_manifest_decision,
    terminal_frame,
)

root = Path(sys.argv[1])
mode = sys.argv[2]
root.mkdir(parents=True)
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
coordinator = retention.RetentionCoordinator.acquire(
    make_config(root / "primary"), clock_ns=clock
)
coordinator.recover_and_purge()
authorizer = StrictAuthorizer(coordinator, manifest, decision)
write_capability = coordinator.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=authorizer,
)
bootstrap_capability(write_capability, manifest)
write_capability.write_all(terminal_frame(manifest, clean=True))
read_capability = coordinator.issue_read_capability(
    persistence_authorizer=authorizer
)
if not hasattr(retention, "_claim_provider_wal_reader"):
    read_capability.close()
    write_capability.close()
    coordinator.close()
    raise SystemExit("reader claim helper missing")
target = read_capability
other = None
if mode == "forged":
    target = object.__new__(retention.ProviderWalReadCapability)
    object.__setattr__(target, "_dispatch", coordinator)
elif mode == "wrong_coordinator":
    other = retention.RetentionCoordinator.acquire(
        make_config(root / "other"), clock_ns=clock
    )
    other.recover_and_purge()
    object.__setattr__(read_capability, "_dispatch", other)
elif mode == "stale":
    read_capability.close()
elif mode == "already_claimed":
    retention._claim_provider_wal_reader(
        read_capability=read_capability
    )
else:
    raise SystemExit("unknown mode")

try:
    with mock.patch(
        "tennis_v1.retention.os.pread",
        side_effect=AssertionError("reader claim read WAL bytes"),
    ):
        retention._claim_provider_wal_reader(read_capability=target)
except retention.RetentionError:
    pass
else:
    raise SystemExit("invalid reader claim was accepted")

if retention._global_halt() is None:
    raise SystemExit("invalid reader claim did not latch retention halt")
if mode in {"stale", "already_claimed"}:
    if read_capability in coordinator._read_capabilities:
        raise SystemExit("invalid reader claim retained live authority")
    if read_capability not in coordinator._read_tombstones:
        raise SystemExit("invalid reader claim was not tombstoned")
if other is not None:
    other.close()
coordinator.close()
print(mode)
"""
        for mode in (
            "forged",
            "wrong_coordinator",
            "stale",
            "already_claimed",
        ):
            with self.subTest(mode=mode):
                result = child(code, str(self.root / mode), mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), mode)

    def test_writer_claim_rejects_noncapability_without_crossing_dependencies(self):
        dependency_calls: list[str] = []

        def dependency(*_: object, **__: object):
            dependency_calls.append("crossed")
            raise AssertionError("dependency crossed")

        with (
            mock.patch(
                "tennis_v1.retention.os.fstat",
                side_effect=dependency,
            ),
            mock.patch(
                "tennis_v1.retention.os.stat",
                side_effect=dependency,
            ),
            mock.patch(
                "tennis_v1.retention.os.write",
                side_effect=dependency,
            ),
        ):
            with self.assertRaises(RetentionError):
                retention_module._claim_provider_wal_writer(
                    write_capability=object(),
                    session_manifest=self.manifest,
                )
        self.assertEqual(dependency_calls, [])

    def test_writer_claim_mismatch_consumes_without_bytes_and_latches_halt(self):
        code = r"""
from dataclasses import replace
from pathlib import Path
import os
import sys
import threading
from unittest import mock
import tennis_v1.retention as retention
from tests.tennis_v1.test_retention import (
    MutableClock,
    StrictAuthorizer,
    make_config,
    make_manifest_decision,
)

if not hasattr(retention, "_claim_provider_wal_writer"):
    raise SystemExit("claim helper missing")
root = Path(sys.argv[1])
mode = sys.argv[2]
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
coordinator = retention.RetentionCoordinator.acquire(
    make_config(root), clock_ns=clock
)
coordinator.recover_and_purge()
authorizer = StrictAuthorizer(coordinator, manifest, decision)
capability = coordinator.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=authorizer,
)
authority = coordinator._write_capabilities[capability]
state = coordinator._session_states[manifest.session_id]
supplied_manifest = manifest
if mode == "registry_capability":
    authority.capability = object()
elif mode == "registry_coordinator":
    authority.coordinator = object()
elif mode == "pid":
    authority.owner_pid += 1
elif mode == "thread":
    authority.owner_thread = threading.Thread()
elif mode == "generation":
    authority.generation += 1
elif mode == "manifest":
    supplied_manifest = replace(manifest)
elif mode == "sha":
    authority.manifest_sha256 = "0" * 64
elif mode == "marker":
    state.marker = replace(
        state.marker, session_manifest_sha256="0" * 64
    )
elif mode == "request":
    object.__setattr__(
        decision, "provider_request_binding_sha256", "0" * 64
    )
elif mode == "deadline":
    clock.now_ns = manifest.required_retention_until_ns
elif mode == "health":
    state.healthy = False
elif mode == "global_halt":
    retention._latch_global_halt(
        coordinator, session_id=None, ambiguous=True
    )
else:
    raise SystemExit("unknown mode")

wal = root / "sessions" / f"{manifest.session_id}.wal"
try:
    with mock.patch(
        "tennis_v1.retention.os.write",
        side_effect=AssertionError("claim attempted a WAL write"),
    ):
        retention._claim_provider_wal_writer(
            write_capability=capability,
            session_manifest=supplied_manifest,
        )
except retention.RetentionError:
    pass
else:
    raise SystemExit("claim mismatch was accepted")
if wal.exists() and wal.stat().st_size != 0:
    raise SystemExit("claim mismatch wrote WAL bytes")
if retention._global_halt() is None:
    raise SystemExit("claim mismatch did not latch retention halt")
if capability in coordinator._write_capabilities:
    raise SystemExit("claim mismatch did not consume capability")
try:
    capability.write_all(b"x")
except retention.RetentionError:
    pass
else:
    raise SystemExit("consumed capability remained writable")
coordinator.close()
print(mode)
"""
        cases = (
            "registry_capability",
            "registry_coordinator",
            "pid",
            "thread",
            "generation",
            "manifest",
            "sha",
            "marker",
            "request",
            "deadline",
            "health",
            "global_halt",
        )
        for mode in cases:
            with self.subTest(mode=mode):
                result = child(code, str(self.root / mode), mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), mode)

    def test_second_writer_claim_fails_before_io_and_consumes_capability(self):
        code = r"""
from pathlib import Path
import sys
from unittest import mock
import tennis_v1.retention as retention
from tests.tennis_v1.test_retention import (
    MutableClock,
    StrictAuthorizer,
    make_config,
    make_manifest_decision,
)

if not hasattr(retention, "_claim_provider_wal_writer"):
    raise SystemExit("claim helper missing")
root = Path(sys.argv[1])
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
coordinator = retention.RetentionCoordinator.acquire(
    make_config(root), clock_ns=clock
)
coordinator.recover_and_purge()
authorizer = StrictAuthorizer(coordinator, manifest, decision)
capability = coordinator.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=authorizer,
)
retention._claim_provider_wal_writer(
    write_capability=capability,
    session_manifest=manifest,
)
dependencies = []
def dependency(name):
    def fail(*args, **kwargs):
        dependencies.append(name)
        raise AssertionError(f"{name} crossed")
    return fail
try:
    with (
        mock.patch("tennis_v1.retention.os.fstat", side_effect=dependency("fstat")),
        mock.patch("tennis_v1.retention.os.stat", side_effect=dependency("stat")),
        mock.patch("tennis_v1.retention.os.write", side_effect=dependency("write")),
    ):
        retention._claim_provider_wal_writer(
            write_capability=capability,
            session_manifest=manifest,
        )
except retention.RetentionError:
    pass
else:
    raise SystemExit("second claim was accepted")
if dependencies:
    raise SystemExit(f"second claim crossed dependencies: {dependencies}")
if capability in coordinator._write_capabilities:
    raise SystemExit("second claim did not consume capability")
if retention._global_halt() is None:
    raise SystemExit("second claim did not latch retention halt")
coordinator.close()
print("second")
"""
        result = child(code, str(self.root / "second-claim"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "second")

    def test_bootstrap_capacity_fails_during_arm_before_capability_or_wal_start(self):
        coordinator = self.acquire()
        authorizer = StrictAuthorizer(
            coordinator,
            self.manifest,
            self.decision,
        )
        conservative_threshold = (
            retention_module.MIN_FREE_BYTES
            + retention_module.RESERVE_BYTES
            + retention_module.MAX_FRAME_BYTES
        )
        at_threshold = mock.Mock(
            f_bavail=conservative_threshold,
            f_frsize=1,
        )

        with mock.patch(
            "tennis_v1.retention.os.fstatvfs",
            return_value=at_threshold,
        ) as fstatvfs:
            with self.assertRaises(
                retention_module.RetentionPrewriteCapacityError
            ):
                coordinator.arm_before_wal(
                    session_manifest=self.manifest,
                    decision=self.decision,
                    persistence_authorizer=authorizer,
                )

        fstatvfs.assert_called_once_with(coordinator._sessions_fd)
        self.assertEqual(
            list((self.state_root / "sessions").iterdir()),
            [],
        )
        self.assertEqual(
            list((self.state_root / "retention-markers").iterdir()),
            [],
        )
        self.assertEqual(coordinator._write_capabilities, {})

    def test_exact_session_start_bypasses_later_frame_capacity_check(self):
        coordinator = self.acquire()
        conservative_threshold = (
            retention_module.MIN_FREE_BYTES
            + retention_module.RESERVE_BYTES
            + retention_module.MAX_FRAME_BYTES
        )
        above_threshold = mock.Mock(
            f_bavail=conservative_threshold + 1,
            f_frsize=1,
        )
        with mock.patch(
            "tennis_v1.retention.os.fstatvfs",
            return_value=above_threshold,
        ) as arm_capacity:
            capability, _ = self.arm(coordinator)
        arm_capacity.assert_called_once_with(coordinator._sessions_fd)

        frame = session_start_frame(self.manifest)
        retention_module._claim_provider_wal_writer(
            write_capability=capability,
            session_manifest=self.manifest,
        )
        capability.write_all(WAL_FILE_PREFIX_BYTES)
        with mock.patch(
            "tennis_v1.retention.os.fstatvfs",
            side_effect=AssertionError(
                "exact SESSION_START rechecked frame capacity"
            ),
        ):
            capability.write_all(frame)

        wal = (
            self.state_root
            / "sessions"
            / f"{self.manifest.session_id}.wal"
        )
        self.assertEqual(wal.read_bytes(), WAL_FILE_PREFIX_BYTES + frame)
        capability.close()

    def test_bootstrap_requires_claim_then_exact_prefix_then_exact_session_start(self):
        coordinator = self.acquire()
        capability, _ = self.arm(coordinator)
        state = coordinator._session_states[self.manifest.session_id]
        retention_module._claim_provider_wal_writer(
            write_capability=capability,
            session_manifest=self.manifest,
        )

        capability.write_all(WAL_FILE_PREFIX_BYTES)
        self.assertTrue(state.wal_prefix_durable)
        self.assertFalse(state.session_start_durable)

        start = session_start_frame(self.manifest)
        with mock.patch(
            "tennis_v1.retention.os.fstatvfs",
            side_effect=AssertionError(
                "ordered SESSION_START rechecked frame capacity"
            ),
        ):
            capability.write_all(start)

        self.assertTrue(state.session_start_durable)
        wal = (
            self.state_root
            / "sessions"
            / f"{self.manifest.session_id}.wal"
        )
        self.assertEqual(
            wal.read_bytes(),
            WAL_FILE_PREFIX_BYTES + start,
        )
        capability.close()

    def test_bootstrap_unclaimed_wrong_duplicate_and_out_of_order_writes_fail_closed(
        self,
    ):
        code = r"""
from pathlib import Path
import sys
import tennis_v1.retention as retention
from tests.tennis_v1.test_retention import (
    MutableClock,
    StrictAuthorizer,
    WAL_FILE_PREFIX_BYTES,
    SECOND_SESSION_ID,
    make_config,
    make_manifest_decision,
    mutate_terminal_frame,
    ordinary_frame,
    session_start_frame,
    terminal_frame,
)

mode = sys.argv[1]
root = Path(sys.argv[2])
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
coordinator = retention.RetentionCoordinator.acquire(
    make_config(root), clock_ns=clock
)
coordinator.recover_and_purge()
authorizer = StrictAuthorizer(coordinator, manifest, decision)
capability = coordinator.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=authorizer,
)
start = session_start_frame(manifest)
terminal = terminal_frame(
    manifest,
    clean=True,
    ingest_seq=2,
    record_count_before_terminal=1,
)
wal = root / "sessions" / f"{manifest.session_id}.wal"
expected = b""
try:
    if mode == "unclaimed_prefix":
        operation = lambda: capability.write_all(WAL_FILE_PREFIX_BYTES)
    elif mode == "unclaimed_start":
        operation = lambda: capability.write_all(start)
    elif mode == "unclaimed_arbitrary":
        operation = lambda: capability.write_all(b"not-a-wal-prefix")
    else:
        retention._claim_provider_wal_writer(
            write_capability=capability,
            session_manifest=manifest,
        )
        if mode == "prefixless_start":
            operation = lambda: capability.write_all(start)
        elif mode == "wrong_prefix":
            operation = lambda: capability.write_all(b"not-a-wal-prefix")
        elif mode == "duplicate_prefix":
            capability.write_all(WAL_FILE_PREFIX_BYTES)
            expected = WAL_FILE_PREFIX_BYTES
            operation = lambda: capability.write_all(WAL_FILE_PREFIX_BYTES)
        elif mode == "terminal_before_start":
            capability.write_all(WAL_FILE_PREFIX_BYTES)
            expected = WAL_FILE_PREFIX_BYTES
            operation = lambda: capability.write_all(terminal)
        elif mode == "wrong_start":
            capability.write_all(WAL_FILE_PREFIX_BYTES)
            expected = WAL_FILE_PREFIX_BYTES
            wrong_manifest, _ = make_manifest_decision(SECOND_SESSION_ID)
            operation = lambda: capability.write_all(
                session_start_frame(wrong_manifest)
            )
        elif mode in (
            "duplicate_start",
            "wrong_start_after_start",
            "malformed_start_length",
            "malformed_start_digest",
            "malformed_start_trailer",
            "malformed_start_metadata",
            "malformed_start_session",
            "prefix_after_start",
            "arbitrary_after_start",
        ):
            capability.write_all(WAL_FILE_PREFIX_BYTES)
            capability.write_all(start)
            expected = WAL_FILE_PREFIX_BYTES + start
            wrong_manifest, _ = make_manifest_decision(SECOND_SESSION_ID)
            operation = {
                "duplicate_start": lambda: capability.write_all(start),
                "wrong_start_after_start": lambda: capability.write_all(
                    session_start_frame(wrong_manifest)
                ),
                "malformed_start_length": lambda: capability.write_all(
                    mutate_terminal_frame(start, "length")
                ),
                "malformed_start_digest": lambda: capability.write_all(
                    mutate_terminal_frame(start, "digest")
                ),
                "malformed_start_trailer": lambda: capability.write_all(
                    mutate_terminal_frame(start, "trailer")
                ),
                "malformed_start_metadata": lambda: capability.write_all(
                    mutate_terminal_frame(start, "metadata_contract")
                ),
                "malformed_start_session": lambda: capability.write_all(
                    mutate_terminal_frame(start, "session_binding")
                ),
                "prefix_after_start": lambda: capability.write_all(
                    WAL_FILE_PREFIX_BYTES
                ),
                "arbitrary_after_start": lambda: capability.write_all(
                    b"not-a-frame"
                ),
            }[mode]
        else:
            raise SystemExit("unknown mode")
    try:
        operation()
    except retention.RetentionError:
        pass
    else:
        raise SystemExit("invalid bootstrap write was accepted")
    if wal.read_bytes() != expected:
        raise SystemExit("invalid bootstrap write changed WAL bytes")
    if retention._global_halt() is None:
        raise SystemExit("invalid bootstrap write did not globally halt")
    if capability in coordinator._write_capabilities:
        raise SystemExit("invalid bootstrap capability was not revoked")
    try:
        capability.write_all(ordinary_frame(manifest))
    except retention.RetentionError:
        pass
    else:
        raise SystemExit("invalid bootstrap capability accepted a later frame")
    if wal.read_bytes() != expected:
        raise SystemExit("revoked bootstrap capability appended later bytes")
    print(mode)
finally:
    coordinator.close()
"""
        cases = (
            "unclaimed_prefix",
            "unclaimed_start",
            "unclaimed_arbitrary",
            "prefixless_start",
            "wrong_prefix",
            "duplicate_prefix",
            "terminal_before_start",
            "wrong_start",
            "duplicate_start",
            "wrong_start_after_start",
            "malformed_start_length",
            "malformed_start_digest",
            "malformed_start_trailer",
            "malformed_start_metadata",
            "malformed_start_session",
            "prefix_after_start",
            "arbitrary_after_start",
        )
        for mode in cases:
            with self.subTest(mode=mode):
                result = child(
                    code,
                    mode,
                    str(self.root / f"bootstrap-{mode}"),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), mode)

    def test_prefix_durable_state_sets_only_after_complete_write_and_fsync(self):
        code = r"""
from pathlib import Path
import os
import sys
from unittest import mock
import tennis_v1.retention as retention
from tests.tennis_v1.test_retention import (
    MutableClock,
    StrictAuthorizer,
    WAL_FILE_PREFIX_BYTES,
    make_config,
    make_manifest_decision,
)

mode = sys.argv[1]
root = Path(sys.argv[2])
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
coordinator = retention.RetentionCoordinator.acquire(
    make_config(root), clock_ns=clock
)
coordinator.recover_and_purge()
authorizer = StrictAuthorizer(coordinator, manifest, decision)
capability = coordinator.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=authorizer,
)
retention._claim_provider_wal_writer(
    write_capability=capability,
    session_manifest=manifest,
)
state = coordinator._session_states[manifest.session_id]
wal_fd = state.wal_fd
original_fsync = os.fsync

def fail_fsync(fd):
    if fd == wal_fd:
        raise OSError("injected prefix fsync failure")
    return original_fsync(fd)

patcher = (
    mock.patch("tennis_v1.retention.os.write", return_value=0)
    if mode == "zero"
    else (
        mock.patch(
            "tennis_v1.retention.os.write",
            side_effect=(7, 0),
        )
        if mode == "partial"
        else mock.patch(
            "tennis_v1.retention.os.fsync",
            side_effect=fail_fsync,
        )
    )
)
try:
    with patcher:
        try:
            capability.write_all(WAL_FILE_PREFIX_BYTES)
        except retention.RetentionGlobalHalt:
            pass
        else:
            raise SystemExit("prefix I/O failure was accepted")
    if getattr(state, "wal_prefix_durable", False):
        raise SystemExit("failed prefix was marked durable")
    if capability in coordinator._write_capabilities:
        raise SystemExit("failed prefix capability was not revoked")
    print(mode)
finally:
    coordinator.close()
"""
        for mode in ("zero", "partial", "fsync"):
            with self.subTest(mode=mode):
                result = child(
                    code,
                    mode,
                    str(self.root / f"prefix-durable-{mode}"),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), mode)

    def test_actual_write_zero_partial_and_fsync_failures_revoke_without_retry_or_terminal(
        self,
    ):
        code = r"""
from pathlib import Path
import os
import sys
from unittest import mock
import tennis_v1.retention as retention
from tests.tennis_v1.test_retention import (
    MutableClock,
    StrictAuthorizer,
    WAL_FILE_PREFIX_BYTES,
    make_config,
    make_manifest_decision,
    session_start_frame,
    terminal_frame,
)

mode = sys.argv[1]
root = Path(sys.argv[2])
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
coordinator = retention.RetentionCoordinator.acquire(
    make_config(root), clock_ns=clock
)
coordinator.recover_and_purge()
authorizer = StrictAuthorizer(coordinator, manifest, decision)
capability = coordinator.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=authorizer,
)
retention._claim_provider_wal_writer(
    write_capability=capability,
    session_manifest=manifest,
)
capability.write_all(WAL_FILE_PREFIX_BYTES)
state = coordinator._session_states[manifest.session_id]
wal_fd = state.wal_fd
wal = root / "sessions" / f"{manifest.session_id}.wal"
frame = session_start_frame(manifest)
original_write = os.write
original_fsync = os.fsync
wal_write_calls = 0

def injected_write(fd, content):
    global wal_write_calls
    if fd != wal_fd:
        return original_write(fd, content)
    wal_write_calls += 1
    if mode == "zero":
        return 0
    if mode == "partial":
        if wal_write_calls == 1:
            return original_write(fd, content[:7])
        return 0
    return original_write(fd, content)

def injected_fsync(fd):
    if mode == "fsync" and fd == wal_fd:
        raise OSError("injected WAL fsync failure")
    return original_fsync(fd)

patcher = (
    mock.patch("tennis_v1.retention.os.fsync", side_effect=injected_fsync)
    if mode == "fsync"
    else mock.patch("tennis_v1.retention.os.write", side_effect=injected_write)
)
try:
    with patcher:
        try:
            capability.write_all(frame)
        except retention.RetentionGlobalHalt:
            pass
        else:
            raise SystemExit("actual WAL I/O failure was accepted")
    expected_size = len(WAL_FILE_PREFIX_BYTES) + {
        "zero": 0,
        "partial": 7,
        "fsync": len(frame),
    }[mode]
    if wal.stat().st_size != expected_size:
        raise SystemExit(
            f"unexpected durable prefix: {wal.stat().st_size} != {expected_size}"
        )
    if state.session_start_durable:
        raise SystemExit("failed SESSION_START was marked durable")
    if retention._global_halt() is None:
        raise SystemExit("actual WAL I/O failure did not globally halt")
    if capability in coordinator._write_capabilities:
        raise SystemExit("uncertain capability remained registered")
    terminal = terminal_frame(
        manifest,
        clean=False,
        ingest_seq=2,
        record_count_before_terminal=1,
    )
    with mock.patch(
        "tennis_v1.retention.os.write",
        side_effect=AssertionError("revoked capability retried WAL I/O"),
    ):
        for operation in (
            lambda: capability.write_all(frame),
            lambda: capability.write_halt_control(terminal),
            capability.close,
        ):
            try:
                operation()
            except retention.RetentionError:
                pass
            else:
                raise SystemExit("revoked capability remained usable")
    print(mode)
finally:
    coordinator.close()
"""
        for mode in ("zero", "partial", "fsync"):
            with self.subTest(mode=mode):
                result = child(
                    code,
                    mode,
                    str(self.root / f"actual-io-{mode}"),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), mode)

    def test_nonterminal_frame_capacity_is_strict_and_checked_before_every_write(self):
        frame = ordinary_frame(self.manifest)
        threshold = 64 * 1024 * 1024 + 1024 * 1024 + len(frame)
        statvfs_at_threshold = mock.Mock(f_bavail=threshold, f_frsize=1)
        statvfs_above_threshold = mock.Mock(
            f_bavail=threshold + 1,
            f_frsize=1,
        )
        coordinator = self.acquire()
        denied, _ = self.arm(coordinator)
        wal = (
            self.state_root
            / "sessions"
            / f"{self.manifest.session_id}.wal"
        )
        denied_bootstrap = bootstrap_capability(denied, self.manifest)

        with (
            mock.patch(
                "tennis_v1.retention.os.fstatvfs",
                return_value=statvfs_at_threshold,
            ) as fstatvfs,
            mock.patch(
                "tennis_v1.retention._write_all",
                wraps=retention_module._write_all,
            ) as write_all,
        ):
            with self.assertRaises(
                retention_module.RetentionPrewriteCapacityError
            ):
                denied.write_all(frame)
        fstatvfs.assert_called_once_with(
            coordinator._session_states[self.manifest.session_id].wal_fd
        )
        write_all.assert_not_called()
        self.assertEqual(wal.read_bytes(), denied_bootstrap)

        denied.write_halt_control(
            terminal_frame(self.manifest, clean=False)
        )
        denied.close()

        accepted_manifest, accepted_decision = make_manifest_decision(
            str(uuid.uuid4())
        )
        accepted, _ = self.arm(
            coordinator, accepted_manifest, accepted_decision
        )
        accepted_wal = (
            self.state_root
            / "sessions"
            / f"{accepted_manifest.session_id}.wal"
        )
        accepted_frame = ordinary_frame(accepted_manifest)
        accepted_threshold = (
            64 * 1024 * 1024 + 1024 * 1024 + len(accepted_frame)
        )
        capacity_results = (
            mock.Mock(f_bavail=accepted_threshold + 1, f_frsize=1),
            mock.Mock(f_bavail=accepted_threshold, f_frsize=1),
        )
        accepted_bootstrap = bootstrap_capability(
            accepted,
            accepted_manifest,
        )
        with mock.patch(
            "tennis_v1.retention.os.fstatvfs",
            side_effect=capacity_results,
        ) as fstatvfs:
            accepted.write_all(accepted_frame)
            with self.assertRaises(
                retention_module.RetentionPrewriteCapacityError
            ):
                accepted.write_all(accepted_frame)
        self.assertEqual(fstatvfs.call_count, 2)
        self.assertEqual(
            accepted_wal.read_bytes(),
            accepted_bootstrap + accepted_frame,
        )
        accepted.write_halt_control(
            terminal_frame(
                accepted_manifest,
                clean=False,
                ingest_seq=2,
                record_count_before_terminal=1,
            )
        )
        accepted.close()

    def test_prefix_and_terminal_writes_do_not_use_frame_capacity_check(self):
        coordinator = self.acquire()
        prefix_capability, _ = self.arm(coordinator)
        retention_module._claim_provider_wal_writer(
            write_capability=prefix_capability,
            session_manifest=self.manifest,
        )
        with mock.patch(
            "tennis_v1.retention.os.fstatvfs",
            side_effect=AssertionError("prefix checked capacity"),
        ):
            prefix_capability.write_all(WAL_FILE_PREFIX_BYTES)
        prefix_capability.close()

        terminal_manifest, terminal_decision = make_manifest_decision(
            str(uuid.uuid4())
        )
        terminal_capability, _ = self.arm(
            coordinator, terminal_manifest, terminal_decision
        )
        bootstrap_capability(terminal_capability, terminal_manifest)
        with mock.patch(
            "tennis_v1.retention.os.fstatvfs",
            side_effect=AssertionError("terminal checked capacity"),
        ):
            terminal_capability.write_all(
                terminal_frame(terminal_manifest, clean=True)
            )
        terminal_capability.close()

    def test_clean_terminal_uses_distinct_close_not_raw_authorization(self):
        coordinator = self.acquire()
        capability, authorizer = self.arm(coordinator)
        bootstrap_capability(capability, self.manifest)
        raw_calls_before_terminal = authorizer.raw_calls
        capability.write_all(terminal_frame(self.manifest, clean=True))
        self.assertEqual(authorizer.raw_calls, raw_calls_before_terminal)
        self.assertEqual(authorizer.close_calls, 1)
        capability.close()

    def test_write_and_read_capability_constructors_copy_pickle_and_reuse_fail(self):
        coordinator = self.acquire()
        write_capability, authorizer = self.arm(coordinator)
        self.assertNotIn("_fd", ProviderWalWriteCapability.__slots__)
        self.assertNotIn("_reserve_fd", ProviderWalWriteCapability.__slots__)
        self.assertNotIn("_fd", ProviderWalReadCapability.__slots__)
        write_thread_errors: list[BaseException] = []
        write_thread = threading.Thread(
            target=lambda: _capture_exception(
                write_thread_errors, write_capability.close
            )
        )
        write_thread.start()
        write_thread.join()
        self.assertIsInstance(write_thread_errors[0], RetentionError)
        bootstrap_capability(write_capability, self.manifest)
        write_capability.write_all(
            ordinary_frame(self.manifest, ingest_seq=42)
        )
        write_capability.write_all(
            terminal_frame(
                self.manifest,
                clean=True,
                ingest_seq=43,
                record_count_before_terminal=42,
                raw_count=14,
                derived_count=14,
                last_applied_raw_seq=40,
            )
        )
        read_capability = coordinator.issue_read_capability(
            persistence_authorizer=authorizer
        )
        read_thread_errors: list[BaseException] = []
        read_thread = threading.Thread(
            target=lambda: _capture_exception(
                read_thread_errors, read_capability.close
            )
        )
        read_thread.start()
        read_thread.join()
        self.assertIsInstance(read_thread_errors[0], RetentionError)
        original_manifest = authorizer.session_manifest
        original_decision = authorizer.bound_decision
        authorizer.session_manifest = replace(original_manifest)
        authorizer.bound_decision = replace(original_decision)
        with self.assertRaises(RetentionError):
            read_capability.pread(offset=0, length=1)
        authorizer.session_manifest = original_manifest
        authorizer.bound_decision = original_decision

        swap_manifest, swap_decision = make_manifest_decision(str(uuid.uuid4()))
        swap_capability, swap_authorizer = self.arm(
            coordinator, swap_manifest, swap_decision
        )
        bootstrap_capability(swap_capability, swap_manifest)
        swap_authorizer.session_manifest = replace(swap_manifest)
        swap_authorizer.bound_decision = replace(swap_decision)
        with self.assertRaises(RetentionError):
            swap_capability.write_all(ordinary_frame(swap_manifest))
        swap_capability.close()
        for capability_type in (ProviderWalWriteCapability, ProviderWalReadCapability):
            with self.assertRaises(TypeError):
                capability_type()
        for capability in (write_capability, read_capability):
            self.assertNotIn(self.manifest.session_id, repr(capability))
            self.assertNotIn(".wal", repr(capability))
            with self.assertRaises(TypeError):
                copy.copy(capability)
            with self.assertRaises(TypeError):
                copy.deepcopy(capability)
            for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
                with self.subTest(capability=type(capability).__name__, protocol=protocol):
                    with self.assertRaises((TypeError, pickle.PicklingError)):
                        pickle.dumps(capability, protocol=protocol)
            forged = object.__new__(type(capability))
            for slot in type(capability).__slots__:
                if slot != "__weakref__" and hasattr(capability, slot):
                    object.__setattr__(forged, slot, getattr(capability, slot))
            with self.assertRaises(RetentionError):
                forged.close()
        read_capability.close()
        write_capability.close()
        with self.assertRaises(RetentionError):
            read_capability.pread(offset=0, length=1)
        with self.assertRaises(RetentionError):
            write_capability.write_all(b"x")
        with self.assertRaises(RetentionError):
            write_capability.close()

    def test_closed_capability_authority_graphs_are_collectible_and_due_tombstones_are_weak(
        self,
    ):
        coordinator = self.acquire()
        writer, writer_authorizer = self.arm(coordinator)
        bootstrap_capability(writer, self.manifest)
        writer.write_all(terminal_frame(self.manifest, clean=True))
        held_read = coordinator.issue_read_capability(
            persistence_authorizer=writer_authorizer
        )
        held_read.close()
        writer.close()

        iterations = 48
        authorizer_refs: list[weakref.ReferenceType[StrictAuthorizer]] = []
        thread_refs: list[weakref.ReferenceType[threading.Thread]] = []
        manifest_probes: list[SessionManifest] = []
        decision_probes: list[QualificationDecision] = []
        errors: list[BaseException] = []

        def issue_and_close() -> None:
            try:
                manifest = replace(self.manifest)
                decision = replace(self.decision)
                authorizer = StrictAuthorizer(
                    coordinator, manifest, decision
                )
                manifest_probes.append(manifest)
                decision_probes.append(decision)
                authorizer_refs.append(weakref.ref(authorizer))
                capability = coordinator.issue_read_capability(
                    persistence_authorizer=authorizer
                )
                capability.close()
            except BaseException as error:
                errors.append(error)

        for _ in range(iterations):
            thread = threading.Thread(target=issue_and_close)
            thread.start()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            thread_refs.append(weakref.ref(thread))
        del thread

        self.assertEqual(errors, [])
        self.assertEqual(len(coordinator._read_capabilities), 0)
        self.assertEqual(len(coordinator._write_capabilities), 0)
        gc.collect()
        self.assertTrue(all(item() is None for item in authorizer_refs))
        self.assertTrue(all(item() is None for item in thread_refs))
        for item in manifest_probes:
            self.assertFalse(
                any(
                    isinstance(referrer, retention_module._ReadAuthority)
                    for referrer in gc.get_referrers(item)
                )
            )
        for item in decision_probes:
            self.assertFalse(
                any(
                    isinstance(referrer, retention_module._ReadAuthority)
                    for referrer in gc.get_referrers(item)
                )
            )
        manifest_probes.clear()
        decision_probes.clear()

        self.clock.now_ns = self.manifest.required_retention_until_ns
        coordinator.recover_and_purge()
        with self.assertRaises(RetentionDueDeleteError):
            writer.close()
        with self.assertRaises(RetentionDueDeleteError):
            held_read.close()
        writer_ref = weakref.ref(writer)
        held_read_ref = weakref.ref(held_read)
        del writer
        del held_read
        gc.collect()
        self.assertIsNone(writer_ref())
        self.assertIsNone(held_read_ref())

        due_start = datetime(2030, 1, 2, tzinfo=timezone.utc)
        due_manifest, due_decision = make_manifest_decision(
            str(uuid.uuid4()), start=due_start
        )
        self.clock.now_ns = due_manifest.created_wall_ns
        due_capability, _ = self.arm(
            coordinator, due_manifest, due_decision
        )
        self.clock.now_ns = due_manifest.required_retention_until_ns
        with self.assertRaises(RetentionDueDeleteError):
            due_capability.write_all(b"must-not-write")
        with self.assertRaises(RetentionDueDeleteError):
            due_capability.close()
        self.assertEqual(len(coordinator._write_tombstones), 1)
        self.assertEqual(len(coordinator._read_tombstones), 0)
        due_capability_ref = weakref.ref(due_capability)
        del due_capability
        gc.collect()
        self.assertIsNone(due_capability_ref())
        self.assertEqual(len(coordinator._write_tombstones), 0)
        self.assertEqual(len(coordinator._read_tombstones), 0)

    def test_forked_process_cannot_use_inherited_capabilities(self):
        code = """
from pathlib import Path
import os, sys, threading
import tennis_v1.retention as retention_module
from tests.tennis_v1.test_retention import (
    MutableClock, StrictAuthorizer, bootstrap_capability, make_config,
    make_manifest_decision, terminal_frame,
)
from tennis_v1.retention import RetentionCoordinator, RetentionError

root = Path(sys.argv[1])
manifest, decision = make_manifest_decision()
read_manifest, read_decision = make_manifest_decision(
    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
)
clock = MutableClock(manifest.created_wall_ns)
c = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
c.recover_and_purge()
a1 = StrictAuthorizer(c, manifest, decision)
a2 = StrictAuthorizer(c, read_manifest, read_decision)
w1 = c.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=a1,
)
w2 = c.arm_before_wal(
    session_manifest=read_manifest,
    decision=read_decision,
    persistence_authorizer=a2,
)
bootstrap_capability(w2, read_manifest)
w2.write_all(terminal_frame(read_manifest, clean=True))
r2 = c.issue_read_capability(persistence_authorizer=a2)
w2.close()

pid = os.fork()
if pid == 0:
    for capability in (w1, r2):
        for name, value in (
            ("_owner_pid", os.getpid()),
            ("_owner_thread", threading.current_thread()),
        ):
            try:
                object.__setattr__(capability, name, value)
            except AttributeError:
                pass
            else:
                os._exit(10)
    c._clock_ns = lambda: os._exit(11)
    a1.authorize_raw_persistence = lambda: os._exit(12)
    a2.authorize_analysis = lambda: os._exit(13)
    RetentionCoordinator._load_named_marker = lambda *_: os._exit(14)
    retention_module._write_all = lambda *_: os._exit(15)
    retention_module.os.pread = lambda *_: os._exit(16)
    operations = (
        lambda: w1.write_all(b"x"),
        w1.fsync,
        w1.close,
        lambda: r2.pread(offset=0, length=1),
        r2.close,
    )
    for operation in operations:
        try:
            operation()
        except RetentionError:
            continue
        except BaseException:
            os._exit(2)
        os._exit(3)
    os._exit(0)

_, status = os.waitpid(pid, 0)
try:
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
        raise SystemExit(f"forked capability use status={status}")
    print("fork-denied")
finally:
    r2.close()
    w1.close()
    c.close()
"""
        result = child(code, str(self.root / "fork-state"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "fork-denied")

    def test_storage_oserrors_latch_before_escape_and_remain_permanent(self):
        code = """
from pathlib import Path
import os, sys
from unittest import mock
from tests.tennis_v1.test_retention import (
    MutableClock, StrictAuthorizer, bootstrap_capability, make_config,
    make_manifest_decision, ordinary_frame, terminal_frame,
)
from tennis_v1.retention import RetentionCoordinator, RetentionGlobalHalt

mode = sys.argv[1]
root = Path(sys.argv[2])
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
c = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
c.recover_and_purge()
a = StrictAuthorizer(c, manifest, decision)
w = c.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=a,
)
r = None
if mode == "pread":
    bootstrap_capability(w, manifest)
    w.write_all(terminal_frame(manifest, clean=True))
    r = c.issue_read_capability(persistence_authorizer=a)
else:
    bootstrap_capability(w, manifest)

original_open = os.open
original_fstat = os.fstat
original_stat = os.stat
original_read = os.read
original_pread = os.pread

def fail_marker_open(path, flags, mode_bits=0o777, *, dir_fd=None):
    if os.fspath(path).endswith(".marker.json"):
        raise OSError("injected marker open")
    return original_open(path, flags, mode_bits, dir_fd=dir_fd)

def fail_fstat(fd):
    raise OSError("injected fstat")

def fail_stat(path, *, dir_fd=None, follow_symlinks=True):
    raise OSError("injected stat")

def fail_read(fd, length):
    raise OSError("injected marker read")

def fail_pread(fd, length, offset):
    raise OSError("injected pread")

patcher = {
    "open": mock.patch(
        "tennis_v1.retention.os.open", side_effect=fail_marker_open
    ),
    "fstat": mock.patch(
        "tennis_v1.retention.os.fstat", side_effect=fail_fstat
    ),
    "stat": mock.patch(
        "tennis_v1.retention.os.stat", side_effect=fail_stat
    ),
    "marker_read": mock.patch(
        "tennis_v1.retention.os.read", side_effect=fail_read
    ),
    "pread": mock.patch(
        "tennis_v1.retention.os.pread", side_effect=fail_pread
    ),
}[mode]

try:
    with patcher:
        try:
            (
                r.pread(offset=0, length=1)
                if r is not None
                else w.write_all(ordinary_frame(manifest))
            )
        except RetentionGlobalHalt:
            pass
        else:
            raise SystemExit("storage failure did not globally halt")
    for operation in (
        c.require_provider_operation,
        lambda: w.write_all(b"x"),
        lambda: c.mark_clean_terminal(session_id=manifest.session_id),
    ):
        try:
            operation()
        except RetentionGlobalHalt:
            pass
        else:
            raise SystemExit("global halt was not permanent")
    try:
        RetentionCoordinator.acquire(make_config(root / "again"), clock_ns=clock)
    except RetentionGlobalHalt:
        pass
    else:
        raise SystemExit("same-process acquisition survived latch")
    print(mode)
finally:
    c.close()
"""
        for mode in ("open", "fstat", "stat", "marker_read", "pread"):
            with self.subTest(mode=mode):
                result = child(code, mode, str(self.root / f"oserror-{mode}"))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), mode)

    def test_mark_clean_terminal_rechecks_exact_deadline_and_current_wal(self):
        code = """
from pathlib import Path
import os, sys
from tests.tennis_v1.test_retention import (
    MutableClock, StrictAuthorizer, bootstrap_capability, make_config,
    make_manifest_decision, terminal_frame,
)
from tennis_v1.retention import (
    RetentionCoordinator, RetentionDueDeleteError, RetentionGlobalHalt,
    _ack_provider_wal_clean_terminal,
)

mode = sys.argv[1]
root = Path(sys.argv[2])
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
c = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
c.recover_and_purge()
a = StrictAuthorizer(c, manifest, decision)
w = c.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=a,
)
bootstrap_capability(w, manifest)
w.write_all(terminal_frame(manifest, clean=True))
_ack_provider_wal_clean_terminal(write_capability=w)
wal = root / "sessions" / f"{manifest.session_id}.wal"
marker = root / "retention-markers" / f"{manifest.session_id}.marker.json"

try:
    if mode == "equality":
        clock.now_ns = manifest.required_retention_until_ns
        try:
            c.mark_clean_terminal(session_id=manifest.session_id)
        except RetentionDueDeleteError:
            pass
        else:
            raise SystemExit("clean terminal crossed exact expiry")
        if wal.exists() or marker.exists():
            raise SystemExit("exact-expiry purge did not remove tuple")
    elif mode == "replacement":
        wal.unlink()
        fd = os.open(wal, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            c.mark_clean_terminal(session_id=manifest.session_id)
        except RetentionGlobalHalt:
            pass
        else:
            raise SystemExit("replacement WAL was accepted as clean")
        try:
            c.require_provider_operation()
        except RetentionGlobalHalt:
            pass
        else:
            raise SystemExit("WAL substitution did not latch")
    else:
        raise SystemExit("unknown mode")
    print(mode)
finally:
    c.close()
"""
        for mode in ("equality", "replacement"):
            with self.subTest(mode=mode):
                result = child(
                    code,
                    mode,
                    str(self.root / f"clean-{mode}"),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), mode)

    def test_halt_control_is_one_shot_strictly_typed_and_denied_when_session_due(self):
        coordinator = self.acquire()
        invalid_capability, _ = self.arm(coordinator)
        bootstrap_capability(invalid_capability, self.manifest)
        with self.assertRaises((TypeError, RetentionError)):
            invalid_capability.write_halt_control(bytearray(b"x"))  # type: ignore[arg-type]
        with self.assertRaises(RetentionError):
            invalid_capability.write_halt_control(
                terminal_frame(self.manifest, clean=False)
            )

        bypass_manifest, bypass_decision = make_manifest_decision(str(uuid.uuid4()))
        bypass_capability, _ = self.arm(
            coordinator, bypass_manifest, bypass_decision
        )
        bootstrap_capability(bypass_capability, bypass_manifest)
        with self.assertRaises(RetentionError):
            bypass_capability.write_all(b"EVT1")
        with self.assertRaises(RetentionError):
            bypass_capability.write_all(
                terminal_frame(bypass_manifest, clean=False)
            )
        bypass_capability.close()

        count_manifest, count_decision = make_manifest_decision(str(uuid.uuid4()))
        count_capability, _ = self.arm(
            coordinator, count_manifest, count_decision
        )
        bootstrap_capability(count_capability, count_manifest)
        with self.assertRaises(RetentionError):
            count_capability.write_all(
                terminal_frame(
                    count_manifest,
                    clean=True,
                    ingest_seq=3,
                    record_count_before_terminal=2,
                    raw_count=2,
                )
            )
        count_capability.close()

        clean_manifest, clean_decision = make_manifest_decision(str(uuid.uuid4()))
        clean_capability, _ = self.arm(
            coordinator, clean_manifest, clean_decision
        )
        bootstrap_capability(clean_capability, clean_manifest)
        with self.assertRaises(RetentionError):
            clean_capability.write_halt_control(
                terminal_frame(clean_manifest, clean=True)
            )
        with self.assertRaises(RetentionError):
            clean_capability.write_halt_control(
                terminal_frame(clean_manifest, clean=False)
            )

        second_manifest, second_decision = make_manifest_decision(SECOND_SESSION_ID)
        valid_capability, _ = self.arm(coordinator, second_manifest, second_decision)
        bootstrap_capability(valid_capability, second_manifest)
        valid_capability.write_halt_control(
            terminal_frame(second_manifest, clean=False)
        )
        with self.assertRaises(RetentionError):
            valid_capability.write_halt_control(
                terminal_frame(second_manifest, clean=False)
            )
        with self.assertRaises(RetentionError):
            valid_capability.write_all(b"x")

        third_id = str(uuid.uuid4())
        third_manifest, third_decision = make_manifest_decision(third_id)
        due_capability, _ = self.arm(coordinator, third_manifest, third_decision)
        bootstrap_capability(due_capability, third_manifest)
        self.clock.now_ns = third_manifest.required_retention_until_ns
        with self.assertRaises(RetentionDueDeleteError):
            due_capability.write_halt_control(
                terminal_frame(third_manifest, clean=False)
            )

        advancing_manifest, advancing_decision = make_manifest_decision(
            str(uuid.uuid4())
        )
        self.clock.now_ns = advancing_manifest.created_wall_ns
        advancing_capability, advancing_authorizer = self.arm(
            coordinator, advancing_manifest, advancing_decision
        )
        bootstrap_capability(advancing_capability, advancing_manifest)

        def advance_during_authorization():
            self.clock.now_ns = advancing_manifest.required_retention_until_ns
            return advancing_authorizer.raw_deadline

        advancing_authorizer.authorize_raw_persistence = (
            advance_during_authorization
        )
        with self.assertRaises(RetentionDueDeleteError):
            advancing_capability.write_all(ordinary_frame(advancing_manifest))

        release_manifest, release_decision = make_manifest_decision(
            str(uuid.uuid4())
        )
        self.clock.now_ns = release_manifest.created_wall_ns
        release_capability, _ = self.arm(
            coordinator, release_manifest, release_decision
        )
        release_bootstrap = bootstrap_capability(
            release_capability,
            release_manifest,
        )
        wal_path = (
            self.state_root
            / "sessions"
            / f"{release_manifest.session_id}.wal"
        )
        probe_fd = os.open(wal_path, os.O_RDONLY)
        crossed_deadline = False
        original_fsync_directory = retention_module._fsync_directory

        def advance_during_reserve_directory_fsync(fd):
            nonlocal crossed_deadline
            result = original_fsync_directory(fd)
            if fd == coordinator._sessions_fd and not crossed_deadline:
                crossed_deadline = True
                self.clock.now_ns = (
                    release_manifest.required_retention_until_ns
                )
            return result

        try:
            with mock.patch(
                "tennis_v1.retention._fsync_directory",
                side_effect=advance_during_reserve_directory_fsync,
            ):
                with self.assertRaises(RetentionDueDeleteError):
                    release_capability.write_all(
                        terminal_frame(release_manifest, clean=True)
                    )
            self.assertTrue(crossed_deadline)
            self.assertEqual(os.fstat(probe_fd).st_size, len(release_bootstrap))
        finally:
            os.close(probe_fd)

    def test_ingress_halt_reasons_are_fixed_local_terminals_not_global_halts(self):
        coordinator = self.acquire()
        for reason in (
            "ingress_backpressure",
            "ingress_owner_unresponsive",
        ):
            with self.subTest(reason=reason):
                manifest, decision = make_manifest_decision(str(uuid.uuid4()))
                capability, _ = self.arm(
                    coordinator,
                    manifest,
                    decision,
                )
                bootstrap_capability(capability, manifest)
                capability.write_halt_control(
                    terminal_frame(
                        manifest,
                        clean=False,
                        reason=reason,
                    )
                )
                self.assertIsNone(coordinator.require_provider_operation())
                self.assertIsNone(retention_module._global_halt())

    def test_marker_binds_reserve_and_recovery_handles_every_reserve_crash_window(self):
        coordinator = self.acquire()
        capability, _ = self.arm(coordinator)
        marker_path = next((self.state_root / "retention-markers").iterdir())
        marker = json.loads(marker_path.read_bytes())
        self.assertEqual(marker["reserve_basename"], f"{SESSION_ID}.reserve")
        sessions = self.state_root / "sessions"
        reserve = sessions / marker["reserve_basename"]
        wal = sessions / marker["wal_basename"]
        self.assertEqual(reserve.stat().st_size, 1024 * 1024)
        self.assertGreaterEqual(reserve.stat().st_blocks * 512, 1024 * 1024)
        capability.close()
        self.close_current()

        coordinator = self.acquire()
        self.assertTrue(wal.exists())
        self.assertFalse(reserve.exists())
        self.assertEqual(coordinator.recover_and_purge(), retention_report())
        self.close_current()

        # A no-reserve replay-only WAL remains valid on another restart.
        coordinator = self.acquire()
        self.assertTrue(wal.exists())
        self.assertFalse(reserve.exists())

    def test_state_marker_lock_and_wal_modes_are_0700_and_0600(self):
        previous = os.umask(0o777)
        try:
            coordinator = self.acquire()
            capability, _ = self.arm(coordinator)
        finally:
            os.umask(previous)
        for directory in (
            self.state_root,
            self.state_root / "sessions",
            self.state_root / "retention-markers",
        ):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        files = [self.state_root / "retention.lock"]
        files.extend((self.state_root / "sessions").iterdir())
        files.extend((self.state_root / "retention-markers").iterdir())
        for path in files:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(path.stat().st_uid, os.geteuid())
        worker = coordinator._worker
        capability.close()
        self.close_current()
        self.assertFalse(worker.is_alive())

    def test_expiry_worker_wakes_for_earlier_deadline_at_equality_and_closes(self):
        base = datetime(2030, 1, 1, tzinfo=timezone.utc)
        earlier_manifest, earlier_decision = make_manifest_decision(
            "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            start=base,
        )
        later_manifest, later_decision = make_manifest_decision(
            "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            start=base + timedelta(minutes=20),
        )
        self.clock.now_ns = (
            earlier_manifest.required_retention_until_ns
            - 300_000_000_000
        )
        coordinator = self.acquire(recover=False)
        condition = coordinator._condition
        original_wait = condition.wait
        first_timed_wait = threading.Event()
        earlier_timed_wait = threading.Event()
        purged = threading.Event()
        waiting_after_purge = threading.Event()
        first_timeout: list[float] = []

        def tracked_wait(timeout=None):
            if timeout is not None:
                if not first_timeout:
                    first_timeout.append(timeout)
                    first_timed_wait.set()
                elif timeout < first_timeout[0]:
                    earlier_timed_wait.set()
                if purged.is_set():
                    waiting_after_purge.set()
            return original_wait(timeout)

        with condition:
            condition.wait = tracked_wait
        self.assertEqual(coordinator.recover_and_purge(), retention_report())
        original_recover = RetentionCoordinator._recover_locked

        def tracked_recover(self):
            report = original_recover(self)
            if (
                self is coordinator
                and earlier_manifest.session_id in report.deleted_sessions
            ):
                purged.set()
            return report

        with mock.patch.object(
            RetentionCoordinator,
            "_recover_locked",
            new=tracked_recover,
        ):
            later_capability, _ = self.arm(
                coordinator, later_manifest, later_decision
            )
            self.assertTrue(first_timed_wait.wait(2))
            earlier_capability, _ = self.arm(
                coordinator, earlier_manifest, earlier_decision
            )
            self.assertTrue(earlier_timed_wait.wait(2))
            self.clock.now_ns = (
                earlier_manifest.required_retention_until_ns
            )
            with condition:
                condition.notify_all()
            self.assertTrue(purged.wait(2))
            self.assertTrue(waiting_after_purge.wait(2))

        sessions = self.state_root / "sessions"
        markers = self.state_root / "retention-markers"
        self.assertFalse(
            (sessions / f"{earlier_manifest.session_id}.wal").exists()
        )
        self.assertFalse(
            (
                markers
                / f"{earlier_manifest.session_id}.marker.json"
            ).exists()
        )
        self.assertTrue(
            (sessions / f"{later_manifest.session_id}.wal").exists()
        )
        with self.assertRaises(
            (RetentionDueDeleteError, RetentionError)
        ):
            earlier_capability.write_all(b"x")
        worker = coordinator._worker
        later_capability.close()
        self.close_current()
        self.assertFalse(worker.is_alive())

    def test_expiry_worker_and_close_are_atomic_without_deadlock(self):
        coordinator = self.acquire()
        capability, _ = self.arm(coordinator)
        entered = threading.Event()
        release = threading.Event()
        closed = threading.Event()
        original_recover = RetentionCoordinator._recover_locked

        def blocked_recover(self):
            if self is coordinator and self._ready:
                entered.set()
                if not release.wait(2):
                    raise AssertionError("test did not release expiry worker")
            return original_recover(self)

        with mock.patch.object(
            RetentionCoordinator,
            "_recover_locked",
            new=blocked_recover,
        ):
            with coordinator._condition:
                self.clock.now_ns = (
                    self.manifest.required_retention_until_ns
                )
                coordinator._condition.notify_all()
            self.assertTrue(entered.wait(2))
            close_thread = threading.Thread(
                target=lambda: (coordinator.close(), closed.set())
            )
            close_thread.start()
            release.set()
            close_thread.join(2)
            self.assertFalse(close_thread.is_alive())
            self.assertTrue(closed.is_set())
        self.coordinators.remove(coordinator)
        self.assertFalse(coordinator._worker.is_alive())
        with self.assertRaises((RetentionError, RetentionDueDeleteError)):
            capability.write_all(b"x")

    def test_second_process_cannot_acquire_the_exclusive_retention_lock(self):
        coordinator = self.acquire(recover=False)
        with self.assertRaises(RetentionError):
            coordinator.require_provider_operation()
        code = """
from pathlib import Path
import sys
from tests.tennis_v1.test_retention import make_config, MutableClock
from tennis_v1.retention import RetentionCoordinator, RetentionError
try:
    RetentionCoordinator.acquire(make_config(Path(sys.argv[1])), clock_ns=MutableClock(1))
except RetentionError:
    print("locked")
else:
    raise SystemExit("unexpected acquisition")
"""
        result = child(code, str(self.state_root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "locked")
        coordinator.recover_and_purge()

    def test_symlink_hardlink_wrong_owner_mode_and_nonregular_entries_halt(self):
        cases = (
            "symlink",
            "hardlink",
            "wrong_owner",
            "wrong_mode",
            "fifo",
            "lock_wrong_mode",
        )
        code = """
from pathlib import Path
import os, sys
from unittest import mock
from tests.tennis_v1.test_retention import make_config, MutableClock
from tennis_v1.retention import RetentionCoordinator, RetentionError, RetentionGlobalHalt
root = Path(sys.argv[1])
try:
    c = RetentionCoordinator.acquire(make_config(root), clock_ns=MutableClock(1))
except RetentionError:
    print("halted")
    raise SystemExit(0)
context = mock.patch("tennis_v1.retention.os.geteuid", return_value=os.geteuid()+1) if sys.argv[2] == "wrong_owner" else mock.patch("builtins.id", wraps=id)
try:
    with context:
        c.recover_and_purge()
except RetentionGlobalHalt:
    try:
        c.require_provider_operation()
    except RetentionGlobalHalt:
        print("halted")
else:
    raise SystemExit("unsafe state accepted")
finally:
    c.close()
"""
        for case in cases:
            with self.subTest(case=case):
                root = self.root / case
                (root / "sessions").mkdir(parents=True, mode=0o700)
                (root / "retention-markers").mkdir(mode=0o700)
                os.chmod(root, 0o700)
                target = root / "sessions" / f"{SESSION_ID}.wal"
                if case == "lock_wrong_mode":
                    lock = root / "retention.lock"
                    lock.write_bytes(b"")
                    os.chmod(lock, 0o640)
                elif case == "symlink":
                    (root / "outside").write_bytes(b"x")
                    target.symlink_to(root / "outside")
                elif case == "hardlink":
                    source = root / "source"
                    source.write_bytes(b"x")
                    os.chmod(source, 0o600)
                    os.link(source, target)
                elif case == "wrong_mode":
                    target.write_bytes(b"x")
                    os.chmod(target, 0o640)
                elif case == "fifo":
                    os.mkfifo(target, 0o600)
                else:
                    target.write_bytes(b"x")
                    os.chmod(target, 0o600)
                result = child(code, str(root), case)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "halted")

    def test_bound_marker_wal_and_reserve_substitutions_halt_before_write(self):
        code = """
from pathlib import Path
import os, sys
from dataclasses import replace
from unittest import mock
import tennis_v1.retention as retention_module
from tests.tennis_v1.test_retention import (
    MutableClock, StrictAuthorizer, make_config, make_manifest_decision,
)
from tennis_v1.retention import RetentionCoordinator, RetentionGlobalHalt

entry, attack, root_text = sys.argv[1:4]
root = Path(root_text)
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
c = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
c.recover_and_purge()
a = StrictAuthorizer(c, manifest, decision)
w = c.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=a,
)
paths = {
    "marker": root / "retention-markers" / f"{manifest.session_id}.marker.json",
    "wal": root / "sessions" / f"{manifest.session_id}.wal",
    "reserve": root / "sessions" / f"{manifest.session_id}.reserve",
}
target = paths[entry]
original_bytes = target.read_bytes() if entry == "marker" else b""
outside = root / f"outside-{entry}-{attack}"
target_stat = target.stat()
target_identity = (target_stat.st_dev, target_stat.st_ino)
context = mock.patch("builtins.id", wraps=id)
if attack == "wrong_owner":
    wrong_owner = os.geteuid() + 1
    state = c._session_states[manifest.session_id]
    if entry == "wal":
        state.wal_identity = replace(
            state.wal_identity, owner=wrong_owner
        )
    elif entry == "reserve":
        state.reserve_identity = replace(
            state.reserve_identity, owner=wrong_owner
        )
elif attack == "wrong_mode":
    os.chmod(target, 0o640)
else:
    target.unlink()
    if attack == "symlink":
        outside.write_bytes(original_bytes)
        os.chmod(outside, 0o600)
        target.symlink_to(outside)
    elif attack == "hardlink":
        outside.write_bytes(original_bytes)
        os.chmod(outside, 0o600)
        os.link(outside, target)
    elif attack == "fifo":
        os.mkfifo(target, 0o600)
    else:
        raise SystemExit("unknown attack")

write_calls = 0
target_fstat_calls = 0
target_stat_calls = 0
marker_read_calls = 0
original_write_all = retention_module._write_all
original_fstat = retention_module.os.fstat
original_stat = retention_module.os.stat
original_read_marker = retention_module._read_marker

def with_wrong_owner(value):
    values = list(value)
    values[4] = wrong_owner
    extras = {
        name: getattr(value, name)
        for name in ("st_blocks", "st_blksize", "st_rdev")
        if hasattr(value, name)
    }
    return os.stat_result(values, extras)

def counted_write(fd, content):
    global write_calls
    write_calls += 1
    return original_write_all(fd, content)

def counted_fstat(fd):
    global target_fstat_calls
    value = original_fstat(fd)
    if (value.st_dev, value.st_ino) == target_identity:
        target_fstat_calls += 1
        if attack == "wrong_owner":
            return with_wrong_owner(value)
    return value

def counted_stat(*args, **kwargs):
    global target_stat_calls
    value = original_stat(*args, **kwargs)
    if (value.st_dev, value.st_ino) == target_identity:
        target_stat_calls += 1
        if attack == "wrong_owner":
            return with_wrong_owner(value)
    return value

def counted_read_marker(fd):
    global marker_read_calls
    marker_read_calls += 1
    return original_read_marker(fd)

try:
    with (
        context,
        mock.patch(
            "tennis_v1.retention.os.fstat",
            side_effect=counted_fstat,
        ),
        mock.patch(
            "tennis_v1.retention.os.stat",
            side_effect=counted_stat,
        ),
        mock.patch(
            "tennis_v1.retention._read_marker",
            side_effect=counted_read_marker,
        ),
        mock.patch(
            "tennis_v1.retention._write_all",
            side_effect=counted_write,
        ),
    ):
        try:
            w.write_all(b"must-not-write")
        except RetentionGlobalHalt:
            pass
        else:
            raise SystemExit("bound substitution was accepted")
    if write_calls:
        raise SystemExit("provider write crossed substitution")
    if attack == "wrong_owner" and target_fstat_calls == 0:
        raise SystemExit("wrong-owner attack missed target inode")
    if (
        attack == "wrong_owner"
        and entry in ("wal", "reserve")
        and marker_read_calls == 0
    ):
        raise SystemExit("marker validation did not precede target owner check")
    try:
        c.require_provider_operation()
    except RetentionGlobalHalt:
        pass
    else:
        raise SystemExit("substitution did not latch")
    print(f"{entry}:{attack}")
finally:
    c.close()
"""
        for entry in ("marker", "wal", "reserve"):
            for attack in (
                "symlink",
                "hardlink",
                "wrong_mode",
                "wrong_owner",
                "fifo",
            ):
                with self.subTest(entry=entry, attack=attack):
                    result = child(
                        code,
                        entry,
                        attack,
                        str(self.root / f"bound-{entry}-{attack}"),
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        result.stdout.strip(), f"{entry}:{attack}"
                    )

    def test_terminal_frame_mutation_matrix_rejects_without_appending(self):
        coordinator = self.acquire()
        for mode in (
            "length",
            "digest",
            "trailer",
            "metadata_contract",
            "payload_keys",
            "session_binding",
            "digest_binding",
        ):
            with self.subTest(mode=mode):
                manifest, decision = make_manifest_decision(str(uuid.uuid4()))
                capability, _ = self.arm(coordinator, manifest, decision)
                bootstrap = bootstrap_capability(capability, manifest)
                frame = mutate_terminal_frame(
                    terminal_frame(manifest, clean=True),
                    mode,
                )
                with self.assertRaises(RetentionError):
                    capability.write_all(frame)
                wal = (
                    self.state_root
                    / "sessions"
                    / f"{manifest.session_id}.wal"
                )
                self.assertEqual(wal.read_bytes(), bootstrap)
                capability.close()

    def test_marker_canonical_parser_and_inventory_alias_matrix_halts(self):
        code = """
from pathlib import Path
import os, sys
from tests.tennis_v1.test_retention import (
    MutableClock, StrictAuthorizer, make_config, make_manifest_decision,
)
from tennis_v1.retention import RetentionCoordinator, RetentionGlobalHalt

mode = sys.argv[1]
root = Path(sys.argv[2])
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
c = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
c.recover_and_purge()
a = StrictAuthorizer(c, manifest, decision)
w = c.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=a,
)
w.close()
c.close()
marker = root / "retention-markers" / f"{manifest.session_id}.marker.json"
content = marker.read_bytes()
if mode == "duplicate_key":
    content = content.replace(
        b'"schema_version":1',
        b'"schema_version":1,"schema_version":1',
        1,
    )
    marker.write_bytes(content)
elif mode == "bom":
    marker.write_bytes(b"\\xef\\xbb\\xbf" + content)
elif mode in ("float", "constant", "bool"):
    import json
    raw = json.loads(content)
    token = str(raw["delete_by_ns"]).encode()
    replacement = {
        "float": b"1.0",
        "constant": b"NaN",
        "bool": b"true",
    }[mode]
    marker.write_bytes(
        content.replace(b'"delete_by_ns":' + token, b'"delete_by_ns":' + replacement)
    )
elif mode == "oversize":
    marker.write_bytes(content + b" " * (65537 - len(content)))
elif mode == "duplicate_mapping":
    duplicate = (
        root
        / "retention-markers"
        / "87654321-4321-4321-8321-cba987654321.marker.json"
    )
    duplicate.write_bytes(content)
    os.chmod(duplicate, 0o600)
elif mode == "case_alias":
    marker.rename(marker.with_name(marker.name.upper()))
else:
    raise SystemExit("unknown mode")
os.chmod(marker if marker.exists() else marker.with_name(marker.name.upper()), 0o600)

c = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
try:
    try:
        c.recover_and_purge()
    except RetentionGlobalHalt:
        pass
    else:
        raise SystemExit("malformed marker inventory accepted")
    try:
        c.require_provider_operation()
    except RetentionGlobalHalt:
        pass
    else:
        raise SystemExit("malformed marker did not latch")
    print(mode)
finally:
    c.close()
"""
        for mode in (
            "duplicate_key",
            "bom",
            "float",
            "constant",
            "bool",
            "oversize",
            "duplicate_mapping",
            "case_alias",
        ):
            with self.subTest(mode=mode):
                result = child(
                    code,
                    mode,
                    str(self.root / f"marker-{mode}"),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), mode)

    def test_recovery_removes_armed_marker_when_wal_was_never_created(self):
        coordinator = self.acquire()
        capability, _ = self.arm(coordinator)
        marker = json.loads(next((self.state_root / "retention-markers").iterdir()).read_bytes())
        capability.close()
        self.close_current()
        (self.state_root / "sessions" / marker["wal_basename"]).unlink()
        coordinator = self.acquire(recover=False)
        self.assertEqual(
            coordinator.recover_and_purge(),
            retention_report(recovered=(SESSION_ID,)),
        )
        self.assertEqual(list((self.state_root / "sessions").iterdir()), [])
        self.assertEqual(list((self.state_root / "retention-markers").iterdir()), [])
        self.close_current()

        marker_path = (
            self.state_root
            / "retention-markers"
            / f"{SESSION_ID}.marker.json"
        )
        marker_path.write_bytes(
            canonical_json_bytes(marker_projection(self.manifest, self.decision))
        )
        os.chmod(marker_path, 0o600)
        coordinator = self.acquire(recover=False)
        actions: list[str] = []
        original_fsync = os.fsync
        original_unlink = os.unlink

        def tracked_fsync(fd):
            if fd == coordinator._sessions_fd:
                actions.append("fsync_sessions")
            elif fd == coordinator._markers_fd:
                actions.append("fsync_markers")
            return original_fsync(fd)

        def tracked_unlink(path, *, dir_fd=None):
            if os.fspath(path).endswith(".marker.json"):
                actions.append("unlink_marker")
            return original_unlink(path, dir_fd=dir_fd)

        with (
            mock.patch(
                "tennis_v1.retention.os.fsync", side_effect=tracked_fsync
            ),
            mock.patch(
                "tennis_v1.retention.os.unlink", side_effect=tracked_unlink
            ),
        ):
            self.assertEqual(
                coordinator.recover_and_purge(),
                retention_report(recovered=(SESSION_ID,)),
            )
        self.assertEqual(
            actions,
            ["fsync_sessions", "unlink_marker", "fsync_markers"],
        )

    def test_recovery_accepts_matching_not_due_marker_and_wal(self):
        coordinator = self.acquire()
        capability, authorizer = self.arm(coordinator)
        bootstrap = bootstrap_capability(capability, self.manifest)
        durable_frame = ordinary_frame(self.manifest)
        capability.write_all(durable_frame)
        capability.fsync()
        capability.close()
        self.close_current()
        coordinator = self.acquire()
        read_authorizer = StrictAuthorizer(coordinator, self.manifest, self.decision)
        read_capability = coordinator.issue_read_capability(
            persistence_authorizer=read_authorizer
        )
        self.assertEqual(
            read_capability.pread(offset=0, length=4096),
            bootstrap + durable_frame,
        )
        self.assertGreaterEqual(read_authorizer.analysis_calls, 2)
        original_analysis = read_authorizer.authorize_analysis

        def advance_then_analyze():
            self.clock.now_ns = self.manifest.required_retention_until_ns
            return original_analysis()

        read_authorizer.authorize_analysis = advance_then_analyze
        with self.assertRaises(RetentionDueDeleteError):
            read_capability.pread(offset=0, length=1)

    def test_wal_without_marker_or_marker_wal_mismatch_globally_halts(self):
        code = """
from pathlib import Path
import sys
from tests.tennis_v1.test_retention import make_config, MutableClock
from tennis_v1.retention import RetentionCoordinator, RetentionGlobalHalt
c = RetentionCoordinator.acquire(make_config(Path(sys.argv[1])), clock_ns=MutableClock(1))
try:
    c.recover_and_purge()
except RetentionGlobalHalt:
    print("halted")
else:
    raise SystemExit("unsafe inventory accepted")
finally:
    c.close()
"""
        for case in ("orphan", "mismatch", "noncanonical"):
            with self.subTest(case=case):
                root = self.root / case
                sessions = root / "sessions"
                markers = root / "retention-markers"
                sessions.mkdir(parents=True, mode=0o700)
                markers.mkdir(mode=0o700)
                os.chmod(root, 0o700)
                wal = sessions / f"{SESSION_ID}.wal"
                wal.write_bytes(b"x")
                os.chmod(wal, 0o600)
                if case in ("mismatch", "noncanonical"):
                    marker = marker_projection(self.manifest, self.decision)
                    if case == "mismatch":
                        marker["wal_basename"] = f"{SECOND_SESSION_ID}.wal"
                    marker_path = markers / f"{SESSION_ID}.marker.json"
                    marker_bytes = canonical_json_bytes(marker)
                    marker_path.write_bytes(
                        marker_bytes + (b"\n" if case == "noncanonical" else b"")
                    )
                    os.chmod(marker_path, 0o600)
                result = child(code, str(root))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "halted")

    def test_due_purge_unlinks_wal_then_fsyncs_before_marker_unlink(self):
        coordinator = self.acquire()
        capability, _ = self.arm(coordinator)
        capability.close()
        self.close_current()
        self.clock.now_ns = self.manifest.required_retention_until_ns
        coordinator = self.acquire(recover=False)
        actions: list[tuple[str, object]] = []
        original_unlink = os.unlink
        original_fsync = os.fsync

        def tracked_unlink(path, *, dir_fd=None):
            actions.append(("unlink", os.fspath(path)))
            return original_unlink(path, dir_fd=dir_fd)

        def tracked_fsync(fd):
            actions.append(("fsync", fd))
            return original_fsync(fd)

        with (
            mock.patch("tennis_v1.retention.os.unlink", side_effect=tracked_unlink),
            mock.patch("tennis_v1.retention.os.fsync", side_effect=tracked_fsync),
        ):
            report = coordinator.recover_and_purge()
        self.assertEqual(report, retention_report(deleted=(SESSION_ID,)))
        wal_index = actions.index(("unlink", f"{SESSION_ID}.wal"))
        reserve_index = actions.index(("unlink", f"{SESSION_ID}.reserve"))
        marker_index = actions.index(("unlink", f"{SESSION_ID}.marker.json"))
        fsync_indices = [index for index, item in enumerate(actions) if item[0] == "fsync"]
        self.assertLess(wal_index, reserve_index)
        self.assertLess(reserve_index, fsync_indices[0])
        self.assertLess(fsync_indices[0], marker_index)
        self.assertLess(marker_index, fsync_indices[-1])

    def test_due_unlink_or_fsync_failure_globally_halts_and_blocks_clean_terminal(self):
        coordinator = self.acquire()
        capability, _ = self.arm(coordinator)
        capability.close()
        self.close_current()
        self.clock.now_ns = self.manifest.required_retention_until_ns
        code = """
from pathlib import Path
import os, sys
from unittest import mock
from tests.tennis_v1.test_retention import make_config, MutableClock
from tennis_v1.retention import RetentionCoordinator, RetentionDueDeleteError, RetentionGlobalHalt
c = RetentionCoordinator.acquire(make_config(Path(sys.argv[1])), clock_ns=MutableClock(int(sys.argv[2])))
mode = sys.argv[4]
original_unlink = os.unlink
original_fsync = os.fsync
def fail_wal(path, *, dir_fd=None):
    if os.fspath(path).endswith(".wal"):
        raise OSError("injected")
    return original_unlink(path, dir_fd=dir_fd)
def fail_fsync(fd):
    raise OSError("injected")
try:
    patcher = (
        mock.patch("tennis_v1.retention.os.unlink", side_effect=fail_wal)
        if mode == "unlink"
        else mock.patch("tennis_v1.retention.os.fsync", side_effect=fail_fsync)
    )
    with patcher:
        c.recover_and_purge()
except RetentionDueDeleteError:
    for call in (c.require_provider_operation, lambda: c.mark_clean_terminal(session_id=sys.argv[3])):
        try:
            call()
        except RetentionGlobalHalt:
            pass
        else:
            raise SystemExit("halt did not revoke")
    print("halted")
else:
    raise SystemExit("due failure returned")
finally:
    c.close()
"""
        for mode in ("unlink", "fsync"):
            with self.subTest(mode=mode):
                if mode == "fsync":
                    # The unlink-failure subprocess did not mutate this state.
                    pass
                result = child(
                    code,
                    str(self.state_root),
                    str(self.clock.now_ns),
                    SESSION_ID,
                    mode,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "halted")

    def test_due_delete_failure_matrix_covers_close_recheck_unlink_and_fsync(self):
        code = """
from pathlib import Path
import os, sys
from unittest import mock
import tennis_v1.retention as retention_module
from tests.tennis_v1.test_retention import (
    MutableClock, StrictAuthorizer, bootstrap_capability, make_config,
    make_manifest_decision, terminal_frame,
)
from tennis_v1.retention import (
    RetentionCoordinator, RetentionDueDeleteError, RetentionGlobalHalt,
)

mode = sys.argv[1]
root = Path(sys.argv[2])
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
c = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
c.recover_and_purge()
a = StrictAuthorizer(c, manifest, decision)
w = c.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=a,
)

live_modes = {"close_wal", "close_reserve", "close_read"}
target_fd = -1
trigger = None
if mode == "close_read":
    bootstrap_capability(w, manifest)
    w.write_all(terminal_frame(manifest, clean=True))
    r = c.issue_read_capability(persistence_authorizer=a)
    target_fd = next(iter(c._read_capabilities.values())).fd
    trigger = lambda: c._delete_due_marker(
        c._session_states[manifest.session_id].marker,
        has_reserve=False,
    )
elif mode == "close_wal":
    target_fd = c._session_states[manifest.session_id].wal_fd
    trigger = lambda: c._delete_due_marker(
        c._session_states[manifest.session_id].marker,
        has_reserve=True,
    )
elif mode == "close_reserve":
    target_fd = c._session_states[manifest.session_id].reserve_fd
    trigger = lambda: c._delete_due_marker(
        c._session_states[manifest.session_id].marker,
        has_reserve=True,
    )
else:
    w.close()
    c.close()
    c = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
    trigger = c.recover_and_purge

if mode not in live_modes:
    clock.now_ns = manifest.required_retention_until_ns
original_close = os.close
original_unlink = os.unlink
original_fsync = os.fsync
original_validate = retention_module._validate_named_fd
validation_counts = {}

def fail_close(fd):
    if fd == target_fd:
        raise OSError("injected capability close")
    return original_close(fd)

def fail_unlink(path, *, dir_fd=None):
    suffix = {
        "wal_unlink": ".wal",
        "reserve_unlink": ".reserve",
        "marker_unlink": ".marker.json",
    }.get(mode)
    if suffix is not None and os.fspath(path).endswith(suffix):
        raise OSError("injected unlink")
    return original_unlink(path, dir_fd=dir_fd)

def fail_fsync(fd):
    if (
        mode == "sessions_fsync"
        and fd == c._sessions_fd
        or mode == "markers_fsync"
        and fd == c._markers_fd
    ):
        raise OSError("injected directory fsync")
    return original_fsync(fd)

def fail_recheck(fd, name, directory_fd, **kwargs):
    validation_counts[name] = validation_counts.get(name, 0) + 1
    threshold = {
        "wal_recheck": (f"{manifest.session_id}.wal", 3),
        "reserve_recheck": (f"{manifest.session_id}.reserve", 4),
        "marker_recheck": (f"{manifest.session_id}.marker.json", 3),
    }.get(mode)
    if (
        threshold is not None
        and name == threshold[0]
        and validation_counts[name] == threshold[1]
    ):
        raise retention_module.RetentionError("injected identity recheck")
    return original_validate(fd, name, directory_fd, **kwargs)

patchers = []
if mode in live_modes:
    patchers.append(mock.patch("tennis_v1.retention.os.close", side_effect=fail_close))
else:
    patchers.extend(
        (
            mock.patch(
                "tennis_v1.retention.os.unlink",
                side_effect=fail_unlink,
            ),
            mock.patch(
                "tennis_v1.retention.os.fsync",
                side_effect=fail_fsync,
            ),
            mock.patch(
                "tennis_v1.retention._validate_named_fd",
                side_effect=fail_recheck,
            ),
        )
    )

try:
    for patcher in patchers:
        patcher.start()
    try:
        trigger()
    except RetentionDueDeleteError:
        pass
    else:
        raise SystemExit("due failure did not return due error")
finally:
    for patcher in reversed(patchers):
        patcher.stop()

try:
    c.require_provider_operation()
except RetentionGlobalHalt:
    pass
else:
    raise SystemExit("due failure did not latch")
print(mode)
c.close()
"""
        modes = (
            "close_wal",
            "close_reserve",
            "close_read",
            "wal_recheck",
            "wal_unlink",
            "reserve_recheck",
            "reserve_unlink",
            "sessions_fsync",
            "marker_recheck",
            "marker_unlink",
            "markers_fsync",
        )
        for mode in modes:
            with self.subTest(mode=mode):
                result = child(
                    code,
                    mode,
                    str(self.root / f"due-{mode}"),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), mode)

    def test_due_crash_inventory_matrix_recovers_each_durable_boundary(self):
        for boundary in (
            "wal_unlinked",
            "reserve_unlinked",
            "marker_unlinked",
        ):
            with self.subTest(boundary=boundary):
                root = self.root / f"crash-{boundary}"
                manifest, decision = make_manifest_decision(str(uuid.uuid4()))
                config = make_config(root)
                clock = MutableClock(manifest.created_wall_ns)
                first = RetentionCoordinator.acquire(config, clock_ns=clock)
                first.recover_and_purge()
                authorizer = StrictAuthorizer(first, manifest, decision)
                capability = first.arm_before_wal(
                    session_manifest=manifest,
                    decision=decision,
                    persistence_authorizer=authorizer,
                )
                capability.close()
                first.close()
                sessions = root / "sessions"
                markers = root / "retention-markers"
                wal = sessions / f"{manifest.session_id}.wal"
                reserve = sessions / f"{manifest.session_id}.reserve"
                marker = markers / f"{manifest.session_id}.marker.json"
                wal.unlink()
                if boundary in ("reserve_unlinked", "marker_unlinked"):
                    reserve.unlink()
                if boundary == "marker_unlinked":
                    marker.unlink()
                clock.now_ns = manifest.required_retention_until_ns
                second = RetentionCoordinator.acquire(config, clock_ns=clock)
                try:
                    report = second.recover_and_purge()
                    if boundary == "marker_unlinked":
                        self.assertEqual(report, retention_report())
                    else:
                        self.assertEqual(
                            report,
                            retention_report(
                                recovered=(manifest.session_id,)
                            ),
                        )
                    self.assertEqual(list(sessions.iterdir()), [])
                    self.assertEqual(list(markers.iterdir()), [])
                finally:
                    second.close()

    def test_global_halt_revokes_all_live_read_and_write_capabilities(self):
        code = """
from pathlib import Path
import sys
from tests.tennis_v1.test_retention import (
    MutableClock, StrictAuthorizer, bootstrap_capability, make_config,
    make_manifest_decision, terminal_frame,
)
from tennis_v1.retention import RetentionCoordinator, RetentionGlobalHalt
root = Path(sys.argv[1])
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
c = RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
c.recover_and_purge()
a = StrictAuthorizer(c, manifest, decision)
w = c.arm_before_wal(session_manifest=manifest, decision=decision, persistence_authorizer=a)
bootstrap_capability(w, manifest)
w.write_all(terminal_frame(manifest, clean=True))
r = c.issue_read_capability(persistence_authorizer=a)
(root / "sessions" / f"{manifest.session_id}.wal").unlink()
try:
    c.recover_and_purge()
except RetentionGlobalHalt:
    for call in (
        lambda: w.write_all(b"x"), w.fsync,
        lambda: r.pread(offset=0, length=1), c.require_provider_operation,
        lambda: c.mark_clean_terminal(session_id=manifest.session_id),
    ):
        try:
            call()
        except RetentionGlobalHalt:
            pass
        else:
            raise SystemExit("live authority survived halt")
    c.close()
    try:
        RetentionCoordinator.acquire(make_config(root), clock_ns=clock)
    except RetentionGlobalHalt:
        print("revoked")
    else:
        raise SystemExit("latch cleared in process")
else:
    c.close()
    raise SystemExit("unexpected entry accepted")
"""
        result = child(code, str(self.state_root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "revoked")

    def test_process_halt_serializes_final_io_across_two_coordinators(self):
        code = """
from pathlib import Path
import os, sys, threading
from unittest import mock
import tennis_v1.retention as retention_module
from tests.tennis_v1.test_retention import (
    MutableClock, StrictAuthorizer, bootstrap_capability, make_config,
    make_manifest_decision, ordinary_frame, terminal_frame,
)
from tennis_v1.retention import RetentionCoordinator, RetentionGlobalHalt

mode = sys.argv[1]
root = Path(sys.argv[2])
manifest, decision = make_manifest_decision()
other_manifest, other_decision = make_manifest_decision(
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
)
clock = MutableClock(manifest.created_wall_ns)
c1 = RetentionCoordinator.acquire(make_config(root / "one"), clock_ns=clock)
c2 = RetentionCoordinator.acquire(make_config(root / "two"), clock_ns=clock)
c1.recover_and_purge()
c2.recover_and_purge()
a1 = StrictAuthorizer(c1, manifest, decision)
a2 = StrictAuthorizer(c2, other_manifest, other_decision)
w1 = c1.arm_before_wal(
    session_manifest=manifest,
    decision=decision,
    persistence_authorizer=a1,
)
w2 = c2.arm_before_wal(
    session_manifest=other_manifest,
    decision=other_decision,
    persistence_authorizer=a2,
)
wal = root / "one" / "sessions" / f"{manifest.session_id}.wal"
bootstrap = bootstrap_capability(w1, manifest)
provider_frame = ordinary_frame(manifest)

try:
    if mode in ("write", "fsync"):
        original_validate = RetentionCoordinator._validate_write_capability
        validation_calls = 0

        def inject_after_final_validation(self, capability, **kwargs):
            global validation_calls
            state = original_validate(self, capability, **kwargs)
            if self is c1:
                validation_calls += 1
                if validation_calls == 2:
                    retention_module._latch_global_halt(
                        c2,
                        session_id=other_manifest.session_id,
                        ambiguous=False,
                    )
            return state

        io_calls = 0
        original_fsync = os.fsync

        def counted_fsync(fd):
            global io_calls
            io_calls += 1
            return original_fsync(fd)

        fsync_context = (
            mock.patch(
                "tennis_v1.retention.os.fsync",
                side_effect=counted_fsync,
            )
            if mode == "fsync"
            else mock.patch("builtins.id", wraps=id)
        )
        with (
            mock.patch.object(
                RetentionCoordinator,
                "_validate_write_capability",
                new=inject_after_final_validation,
            ),
            fsync_context,
        ):
            try:
                (
                    w1.write_all(provider_frame)
                    if mode == "write"
                    else w1.fsync()
                )
            except RetentionGlobalHalt:
                pass
            else:
                raise SystemExit("provider I/O returned after global halt")
        if mode == "write" and wal.read_bytes() != bootstrap:
            raise SystemExit("bytes appended after global halt")
        if mode == "fsync" and io_calls != 0:
            raise SystemExit("fsync executed after global halt")
        if mode == "write":
            before_halt = wal.stat().st_size
            try:
                w1.write_halt_control(
                    terminal_frame(manifest, clean=False)
                )
            except RetentionGlobalHalt:
                pass
            else:
                raise SystemExit(
                    "unobserved in-capability halt race wrote control"
                )
            if wal.stat().st_size != before_halt:
                raise SystemExit(
                    "unobserved in-capability halt race changed WAL"
                )
            c1.require_control_halt_eligible(
                session_id=manifest.session_id
            )
            w1.write_halt_control(
                terminal_frame(manifest, clean=False)
            )
            if wal.stat().st_size == len(bootstrap):
                raise SystemExit("scoped halt terminal was not persisted")

    elif mode == "pread":
        w1.write_all(terminal_frame(manifest, clean=True))
        r1 = c1.issue_read_capability(persistence_authorizer=a1)
        w1.close()
        original_validate = RetentionCoordinator._validate_read_capability
        validation_calls = 0
        pread_calls = 0
        original_pread = os.pread

        def inject_after_final_validation(self, capability):
            global validation_calls
            result = original_validate(self, capability)
            if self is c1:
                validation_calls += 1
                if validation_calls == 2:
                    retention_module._latch_global_halt(
                        c2,
                        session_id=other_manifest.session_id,
                        ambiguous=False,
                    )
            return result

        def counted_pread(fd, length, offset):
            global pread_calls
            pread_calls += 1
            return original_pread(fd, length, offset)

        with (
            mock.patch.object(
                RetentionCoordinator,
                "_validate_read_capability",
                new=inject_after_final_validation,
            ),
            mock.patch(
                "tennis_v1.retention.os.pread",
                side_effect=counted_pread,
            ),
        ):
            try:
                r1.pread(offset=0, length=1)
            except RetentionGlobalHalt:
                pass
            else:
                raise SystemExit("provider pread returned after global halt")
        if pread_calls != 0:
            raise SystemExit("pread executed after global halt")

    elif mode == "ordered":
        entered_io = threading.Event()
        latch_started = threading.Event()
        latch_returned = threading.Event()
        latch_installing = threading.Event()
        original_write_all = retention_module._write_all
        original_halt_state = retention_module._GlobalHaltState

        def latch_from_other_coordinator():
            if not entered_io.wait(2):
                raise SystemExit("provider write never entered")
            latch_started.set()
            retention_module._latch_global_halt(
                c2,
                session_id=other_manifest.session_id,
                ambiguous=False,
            )
            latch_returned.set()

        def serialized_write(fd, content):
            entered_io.set()
            if not latch_started.wait(2):
                raise RuntimeError("latch did not start")
            if latch_installing.wait(0.25):
                raise RuntimeError("latch installed during serialized I/O")
            return original_write_all(fd, content)

        def tracked_halt_state(*args, **kwargs):
            latch_installing.set()
            return original_halt_state(*args, **kwargs)

        thread = threading.Thread(target=latch_from_other_coordinator)
        thread.start()
        with (
            mock.patch(
                "tennis_v1.retention._write_all",
                side_effect=serialized_write,
            ),
            mock.patch(
                "tennis_v1.retention._GlobalHaltState",
                side_effect=tracked_halt_state,
            ),
        ):
            w1.write_all(provider_frame)
        thread.join(2)
        if thread.is_alive() or not latch_returned.is_set():
            raise SystemExit("latch did not install after serialized write")
        if wal.read_bytes() != bootstrap + provider_frame:
            raise SystemExit("serialized write did not complete first")
    else:
        raise SystemExit("unknown mode")
    print(mode)
finally:
    c1.close()
    c2.close()
"""
        for mode in ("write", "fsync", "pread", "ordered"):
            with self.subTest(mode=mode):
                scenario_root = self.root / mode
                scenario_root.mkdir()
                result = child(code, mode, str(scenario_root))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), mode)

    def test_only_retention_opens_unlinks_or_purges_a_provider_wal(self):
        package = Path(__file__).resolve().parents[2] / "tennis_v1"
        permitted_read_only_os_open_counts = {
            ("adapter_contract.py", "_open_root"): 1,
            ("adapter_contract.py", "_read_component"): 2,
            ("adapter_contract.py", "_open_relative_directory"): 1,
            ("adapter_contract.py", "_recursive_python_files"): 1,
            ("fingerprints.py", "code_sha256"): 1,
            ("pinned_file.py", "_trusted_repo_identity"): 1,
            ("pinned_file.py", "read_pinned_file"): 3,
        }
        observed_read_only_os_open_counts = {
            name: 0 for name in permitted_read_only_os_open_counts
        }
        forbidden_attributes = {
            "open",
            "mmap",
            "pread",
            "pwrite",
            "read_bytes",
            "read_text",
            "remove",
            "unlink",
            "write_bytes",
            "write_text",
            "touch",
            "hardlink_to",
            "symlink_to",
        }
        forbidden_os_calls = {
            "io.open",
            "mmap.mmap",
            "os.chmod",
            "os.chown",
            "os.copy_file_range",
            "os.fdopen",
            "os.fchmod",
            "os.fchown",
            "os.fsync",
            "os.ftruncate",
            "os.lchown",
            "os.link",
            "os.makedirs",
            "os.mkdir",
            "os.mkfifo",
            "os.mknod",
            "os.mmap",
            "os.posix_fallocate",
            "os.pread",
            "os.pwrite",
            "os.removedirs",
            "os.remove",
            "os.rename",
            "os.replace",
            "os.rmdir",
            "os.sendfile",
            "os.symlink",
            "os.truncate",
            "os.unlink",
            "os.utime",
            "os.write",
            "os.writev",
            "shutil.copy",
            "shutil.copy2",
            "shutil.copyfile",
            "shutil.copymode",
            "shutil.copystat",
            "shutil.move",
            "shutil.rmtree",
        }
        forbidden_path_calls = {
            f"pathlib.Path.instance.{name}"
            for name in (
                "chmod",
                "hardlink_to",
                "open",
                "read_bytes",
                "read_text",
                "rename",
                "replace",
                "symlink_to",
                "touch",
                "unlink",
                "write_bytes",
                "write_text",
            )
        }
        permitted_open_flags = {
            "O_RDONLY",
            "O_DIRECTORY",
            "O_CLOEXEC",
            "O_NOFOLLOW",
            "O_NONBLOCK",
        }
        capability_calls = {"write_all", "write_halt_control", "fsync", "pread"}

        def scan_path(
            path: Path,
            observed_counts: dict[tuple[str, str], int],
        ) -> None:
            if path.name == "retention.py":
                return
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

            class AuthorityVisitor(ast.NodeVisitor):
                def __init__(self) -> None:
                    self.aliases = {
                        "builtins": "builtins",
                        "__builtins__": "builtins",
                        "importlib": "importlib",
                        "io": "io",
                        "mmap": "mmap",
                        "operator": "operator",
                        "os": "os",
                        "pathlib": "pathlib",
                        "shutil": "shutil",
                        "sys": "sys",
                    }
                    self.constant_strings: dict[str, str] = {}
                    self.local_call_returns: dict[str, str | None] = {}
                    self.unproven_roots: set[str] = set()
                    self.function_stack: list[str] = []
                    self.dangerous_modules = {
                        "builtins",
                        "importlib",
                        "io",
                        "mmap",
                        "os",
                        "pathlib",
                        "shutil",
                        "sys",
                    }
                    self.tracked_modules = self.dangerous_modules | {
                        "operator"
                    }
                    self.attribute_getters = {
                        "builtins.getattr",
                        "getattr",
                    }
                    self.low_level_attribute_getters = {
                        "builtins.object.__getattribute__",
                        "builtins.type.__getattribute__",
                        "object.__getattribute__",
                        "type.__getattribute__",
                    }
                    self.reflective_functions = {
                        "__import__",
                        "builtins.__import__",
                        "builtins.compile",
                        "builtins.eval",
                        "builtins.exec",
                        "builtins.globals",
                        "builtins.locals",
                        "builtins.vars",
                        "compile",
                        "eval",
                        "exec",
                        "globals",
                        "importlib.import_module",
                        "locals",
                        "operator.attrgetter",
                        "operator.methodcaller",
                        "vars",
                    }
                    self.reflective_callables = (
                        self.attribute_getters
                        | self.low_level_attribute_getters
                        | self.reflective_functions
                    )

                def visit_Import(self, node: ast.Import) -> None:
                    for item in node.names:
                        if item.name in self.tracked_modules:
                            target = item.asname or item.name
                            self.aliases[target] = item.name
                            self.constant_strings.pop(target, None)
                            self.unproven_roots.discard(target)

                def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                    if node.module in self.tracked_modules:
                        for item in node.names:
                            if item.name == "*":
                                self_test.fail(f"{path}:{node.lineno}")
                            target = item.asname or item.name
                            self.aliases[target] = f"{node.module}.{item.name}"
                            self.constant_strings.pop(target, None)
                            self.unproven_roots.discard(target)

                @staticmethod
                def _tail(qualified: str) -> str:
                    return qualified.rsplit(".", 1)[-1]

                def rooted_in_dangerous_module(
                    self, qualified: str
                ) -> bool:
                    return any(
                        qualified == root
                        or qualified.startswith(f"{root}.")
                        for root in self.dangerous_modules
                    )

                def forbidden_reference(self, qualified: str) -> bool:
                    if not qualified:
                        return False
                    forbidden_qualified = (
                        forbidden_os_calls
                        | forbidden_path_calls
                        | self.reflective_callables
                    )
                    return (
                        any(
                            qualified == reference
                            or qualified.startswith(f"{reference}.")
                            for reference in forbidden_qualified
                        )
                        or qualified in self.dangerous_modules
                        or any(
                            part in forbidden_attributes
                            for part in qualified.split(".")
                        )
                    )

                def value_has_forbidden_reference(
                    self, node: ast.expr
                ) -> bool:
                    if self.forbidden_reference(self.qualified(node)):
                        return True
                    if isinstance(node, ast.Call):
                        if (
                            self.qualified(node.func)
                            in ("builtins.hasattr", "hasattr")
                            or self.read_only_flag_names(node) is not None
                        ):
                            return False
                        return any(
                            self.value_has_forbidden_reference(argument)
                            for argument in node.args
                        ) or any(
                            self.value_has_forbidden_reference(item.value)
                            for item in node.keywords
                        )
                    if isinstance(node, ast.Starred):
                        return self.value_has_forbidden_reference(node.value)
                    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                        return any(
                            self.value_has_forbidden_reference(item)
                            for item in node.elts
                        )
                    if isinstance(node, ast.Dict):
                        return any(
                            item is not None
                            and self.value_has_forbidden_reference(item)
                            for item in (*node.keys, *node.values)
                        )
                    if isinstance(node, ast.IfExp):
                        return self.value_has_forbidden_reference(
                            node.body
                        ) or self.value_has_forbidden_reference(node.orelse)
                    if isinstance(node, ast.BinOp):
                        return self.value_has_forbidden_reference(
                            node.left
                        ) or self.value_has_forbidden_reference(node.right)
                    if isinstance(node, ast.BoolOp):
                        return any(
                            self.value_has_forbidden_reference(item)
                            for item in node.values
                        )
                    if isinstance(node, (ast.NamedExpr, ast.Lambda)):
                        return self.value_has_forbidden_reference(
                            node.value
                            if isinstance(node, ast.NamedExpr)
                            else node.body
                        )
                    if isinstance(node, (ast.Await, ast.Yield, ast.YieldFrom)):
                        return (
                            node.value is not None
                            and self.value_has_forbidden_reference(node.value)
                        )
                    if isinstance(
                        node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)
                    ):
                        yielded_value_is_forbidden = (
                            self.value_has_forbidden_reference(node.elt)
                        )
                        generators_are_forbidden = any(
                            self.value_has_forbidden_reference(
                                generator.iter
                            )
                            or any(
                                self.value_has_forbidden_reference(condition)
                                for condition in generator.ifs
                            )
                            for generator in node.generators
                        )
                        return (
                            yielded_value_is_forbidden
                            or generators_are_forbidden
                        )
                    if isinstance(node, ast.DictComp):
                        yielded_value_is_forbidden = (
                            self.value_has_forbidden_reference(node.key)
                            or self.value_has_forbidden_reference(node.value)
                        )
                        generators_are_forbidden = any(
                            self.value_has_forbidden_reference(
                                generator.iter
                            )
                            or any(
                                self.value_has_forbidden_reference(condition)
                                for condition in generator.ifs
                            )
                            for generator in node.generators
                        )
                        return (
                            yielded_value_is_forbidden
                            or generators_are_forbidden
                        )
                    return False

                def bind_alias(
                    self,
                    target: ast.expr,
                    qualified: str,
                    *,
                    lineno: int,
                ) -> None:
                    if not isinstance(target, ast.Name):
                        return
                    if not qualified:
                        self.aliases.pop(target.id, None)
                        return
                    if self.forbidden_reference(qualified):
                        self_test.fail(f"{path}:{lineno}")
                    self.aliases[target.id] = qualified

                def constant_string(self, node: ast.expr) -> str | None:
                    if (
                        isinstance(node, ast.Constant)
                        and type(node.value) is str
                    ):
                        return node.value
                    if isinstance(node, ast.Name):
                        return self.constant_strings.get(node.id)
                    return None

                def root_has_unproven_identifier(
                    self, node: ast.expr
                ) -> bool:
                    if isinstance(node, ast.Name):
                        return node.id in self.unproven_roots
                    if isinstance(node, (ast.Attribute, ast.Subscript)):
                        return self.root_has_unproven_identifier(node.value)
                    return False

                def bind_assignment(
                    self,
                    target: ast.expr,
                    value: ast.expr,
                    *,
                    lineno: int,
                ) -> None:
                    if self.value_has_forbidden_reference(value):
                        self_test.fail(f"{path}:{lineno}")
                    if isinstance(target, ast.Name):
                        constant = self.constant_string(value)
                        if constant is None:
                            self.constant_strings.pop(target.id, None)
                        else:
                            self.constant_strings[target.id] = constant
                        qualified = self.qualified(value)
                        if (
                            self.root_has_unproven_identifier(value)
                            or (
                                isinstance(value, ast.Call)
                                and self.qualified(value.func)
                                in self.local_call_returns
                                and not qualified
                            )
                        ):
                            self.unproven_roots.add(target.id)
                        else:
                            self.unproven_roots.discard(target.id)
                        self.bind_alias(
                            target,
                            qualified,
                            lineno=lineno,
                        )
                        return
                    if isinstance(target, (ast.Tuple, ast.List)):
                        if (
                            isinstance(value, (ast.Tuple, ast.List))
                            and len(target.elts) == len(value.elts)
                            and not any(
                                isinstance(item, ast.Starred)
                                for item in target.elts
                            )
                        ):
                            for nested_target, nested_value in zip(
                                target.elts, value.elts, strict=True
                            ):
                                self.bind_assignment(
                                    nested_target,
                                    nested_value,
                                    lineno=lineno,
                                )
                            return

                def visit_Assign(self, node: ast.Assign) -> None:
                    for target in node.targets:
                        self.bind_assignment(
                            target,
                            node.value,
                            lineno=node.lineno,
                        )
                    self.generic_visit(node)

                def visit_AugAssign(self, node: ast.AugAssign) -> None:
                    if self.value_has_forbidden_reference(node.value):
                        self_test.fail(f"{path}:{node.lineno}")
                    self.generic_visit(node)

                def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
                    if node.value is not None:
                        self.bind_assignment(
                            node.target,
                            node.value,
                            lineno=node.lineno,
                        )
                    self.generic_visit(node)

                def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
                    self.bind_assignment(
                        node.target,
                        node.value,
                        lineno=node.lineno,
                    )
                    self.generic_visit(node)

                def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                    direct_returns = [
                        statement.value
                        for statement in node.body
                        if isinstance(statement, ast.Return)
                        and statement.value is not None
                    ]
                    return_qualified = (
                        self.qualified(direct_returns[0])
                        if len(direct_returns) == 1
                        else ""
                    )
                    self.local_call_returns[node.name] = (
                        return_qualified
                        if self.rooted_in_dangerous_module(return_qualified)
                        or self.forbidden_reference(return_qualified)
                        else None
                    )
                    outer_aliases = self.aliases.copy()
                    outer_constant_strings = self.constant_strings.copy()
                    outer_unproven_roots = self.unproven_roots.copy()
                    self.function_stack.append(node.name)
                    try:
                        self.generic_visit(node)
                    finally:
                        self.function_stack.pop()
                        self.aliases = outer_aliases
                        self.constant_strings = outer_constant_strings
                        self.unproven_roots = outer_unproven_roots

                def visit_AsyncFunctionDef(
                    self, node: ast.AsyncFunctionDef
                ) -> None:
                    outer_aliases = self.aliases.copy()
                    outer_constant_strings = self.constant_strings.copy()
                    outer_unproven_roots = self.unproven_roots.copy()
                    self.function_stack.append(node.name)
                    try:
                        self.generic_visit(node)
                    finally:
                        self.function_stack.pop()
                        self.aliases = outer_aliases
                        self.constant_strings = outer_constant_strings
                        self.unproven_roots = outer_unproven_roots

                def qualified(self, node: ast.expr) -> str:
                    if isinstance(node, ast.Name):
                        return self.aliases.get(node.id, node.id)
                    if isinstance(node, ast.Attribute):
                        owner = self.qualified(node.value)
                        return (
                            f"{owner}.{node.attr}" if owner else node.attr
                        )
                    if isinstance(node, ast.Subscript):
                        owner = self.qualified(node.value)
                        if (
                            owner
                            and isinstance(node.slice, ast.Constant)
                            and type(node.slice.value) is str
                        ):
                            return f"{owner}.{node.slice.value}"
                        return ""
                    if isinstance(node, ast.Call):
                        function = self.qualified(node.func)
                        if (
                            function
                            in self.attribute_getters
                            | self.low_level_attribute_getters
                            and len(node.args) >= 2
                            and isinstance(node.args[1], ast.Constant)
                            and type(node.args[1].value) is str
                        ):
                            owner = self.qualified(node.args[0])
                            return (
                                f"{owner}.{node.args[1].value}"
                                if owner
                                else ""
                            )
                        if (
                            function
                            in (
                                "__import__",
                                "builtins.__import__",
                                "importlib.import_module",
                            )
                            and node.args
                            and isinstance(node.args[0], ast.Constant)
                            and node.args[0].value in self.dangerous_modules
                        ):
                            return node.args[0].value
                        if function == "pathlib.Path":
                            return "pathlib.Path.instance"
                        if function in self.local_call_returns:
                            return self.local_call_returns[function] or ""
                    return ""

                def expression_references_dangerous_value(
                    self, node: ast.expr
                ) -> bool:
                    qualified = self.qualified(node)
                    if isinstance(
                        node, (ast.Name, ast.Attribute, ast.Subscript)
                    ) and (
                        self.rooted_in_dangerous_module(qualified)
                        or self.forbidden_reference(qualified)
                    ):
                        return True
                    if isinstance(node, ast.Call):
                        function = self.qualified(node.func)
                        if function in self.reflective_functions:
                            return True
                        if function in (
                            self.attribute_getters
                            | self.low_level_attribute_getters
                        ):
                            return self.getter_access_is_dangerous(node)
                        if self.forbidden_reference(function):
                            return True
                        return any(
                            self.expression_references_dangerous_value(
                                argument
                            )
                            for argument in node.args
                        ) or any(
                            self.expression_references_dangerous_value(
                                item.value
                            )
                            for item in node.keywords
                        )
                    if isinstance(node, ast.Attribute):
                        return (
                            node.attr
                            in ("__dict__", "__getattribute__", "__getitem__")
                            or self.expression_references_dangerous_value(
                                node.value
                            )
                        )
                    if isinstance(node, ast.Subscript):
                        return (
                            self.expression_references_dangerous_value(
                                node.value
                            )
                            or self.expression_references_dangerous_value(
                                node.slice
                            )
                        )
                    if isinstance(node, ast.Starred):
                        return self.expression_references_dangerous_value(
                            node.value
                        )
                    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                        return any(
                            self.expression_references_dangerous_value(item)
                            for item in node.elts
                        )
                    if isinstance(node, ast.Dict):
                        return any(
                            item is not None
                            and self.expression_references_dangerous_value(
                                item
                            )
                            for item in (*node.keys, *node.values)
                        )
                    if isinstance(node, ast.IfExp):
                        return (
                            self.expression_references_dangerous_value(
                                node.body
                            )
                            or self.expression_references_dangerous_value(
                                node.orelse
                            )
                        )
                    if isinstance(node, ast.BinOp):
                        return (
                            self.expression_references_dangerous_value(
                                node.left
                            )
                            or self.expression_references_dangerous_value(
                                node.right
                            )
                        )
                    if isinstance(node, ast.BoolOp):
                        return any(
                            self.expression_references_dangerous_value(item)
                            for item in node.values
                        )
                    if isinstance(node, (ast.NamedExpr, ast.Lambda)):
                        return self.expression_references_dangerous_value(
                            node.value
                            if isinstance(node, ast.NamedExpr)
                            else node.body
                        )
                    return False

                def getter_access_is_dangerous(
                    self, node: ast.Call
                ) -> bool:
                    if not node.args:
                        return True
                    root = node.args[0]
                    root_qualified = self.qualified(root)
                    attribute = (
                        self.constant_string(node.args[1])
                        if len(node.args) >= 2
                        else None
                    )
                    return (
                        self.rooted_in_dangerous_module(root_qualified)
                        or self.expression_references_dangerous_value(root)
                        or self.root_has_unproven_identifier(root)
                        or attribute in forbidden_attributes
                        or attribute
                        in ("__dict__", "__getattribute__", "__getitem__")
                        or (not root_qualified and attribute is None)
                    )

                def expression_uses_forbidden_reflection(
                    self, node: ast.expr
                ) -> bool:
                    if isinstance(node, ast.Call):
                        function = self.qualified(node.func)
                        if function in self.reflective_functions:
                            return True
                        if function in (
                            self.attribute_getters
                            | self.low_level_attribute_getters
                        ) and self.getter_access_is_dangerous(node):
                            return True
                        return any(
                            self.expression_uses_forbidden_reflection(argument)
                            for argument in node.args
                        ) or any(
                            self.expression_uses_forbidden_reflection(
                                item.value
                            )
                            for item in node.keywords
                        )
                    if isinstance(node, ast.Attribute):
                        return (
                            node.attr
                            in ("__dict__", "__getattribute__", "__getitem__")
                            or self.expression_uses_forbidden_reflection(
                                node.value
                            )
                        )
                    if isinstance(node, ast.Subscript):
                        return self.expression_uses_forbidden_reflection(
                            node.value
                        )
                    if isinstance(node, ast.Starred):
                        return self.expression_uses_forbidden_reflection(
                            node.value
                        )
                    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                        return any(
                            self.expression_uses_forbidden_reflection(item)
                            for item in node.elts
                        )
                    if isinstance(node, ast.Dict):
                        return any(
                            item is not None
                            and self.expression_uses_forbidden_reflection(item)
                            for item in (*node.keys, *node.values)
                        )
                    if isinstance(node, ast.IfExp):
                        return (
                            self.expression_uses_forbidden_reflection(node.body)
                            or self.expression_uses_forbidden_reflection(
                                node.orelse
                            )
                        )
                    if isinstance(node, (ast.BinOp, ast.BoolOp)):
                        return any(
                            self.expression_uses_forbidden_reflection(item)
                            for item in (
                                (node.left, node.right)
                                if isinstance(node, ast.BinOp)
                                else node.values
                            )
                        )
                    if isinstance(node, (ast.NamedExpr, ast.Lambda)):
                        return self.expression_uses_forbidden_reflection(
                            node.value
                            if isinstance(node, ast.NamedExpr)
                            else node.body
                        )
                    return False

                def visit_Attribute(self, node: ast.Attribute) -> None:
                    owner = self.qualified(node.value)
                    if node.attr == "__dict__" or (
                        node.attr in ("__getattribute__", "__getitem__")
                        and (
                            self.rooted_in_dangerous_module(owner)
                            or self.expression_references_dangerous_value(
                                node.value
                            )
                        )
                    ):
                        self_test.fail(f"{path}:{node.lineno}")
                    self.generic_visit(node)

                def visit_Subscript(self, node: ast.Subscript) -> None:
                    owner = self.qualified(node.value)
                    key = (
                        node.slice.value
                        if isinstance(node.slice, ast.Constant)
                        and type(node.slice.value) is str
                        else None
                    )
                    if (
                        self.expression_uses_forbidden_reflection(node.value)
                        or self.value_has_forbidden_reference(node.value)
                        or owner == "sys.modules"
                        or owner.endswith(".__dict__")
                        or (
                            owner in self.dangerous_modules
                            and key is not None
                        )
                    ):
                        self_test.fail(f"{path}:{node.lineno}")
                    self.generic_visit(node)

                def read_only_flag_names(
                    self, node: ast.expr
                ) -> set[str] | None:
                    if isinstance(node, ast.BinOp) and isinstance(
                        node.op, ast.BitOr
                    ):
                        left = self.read_only_flag_names(node.left)
                        right = self.read_only_flag_names(node.right)
                        if left is None or right is None:
                            return None
                        return left | right
                    if isinstance(node, ast.Attribute):
                        if (
                            self.qualified(node.value) == "os"
                            and node.attr in permitted_open_flags
                        ):
                            return {node.attr}
                        return None
                    if (
                        isinstance(node, ast.Call)
                        and self.qualified(node.func)
                        in self.attribute_getters
                        and len(node.args) == 3
                        and self.qualified(node.args[0]) == "os"
                        and isinstance(node.args[1], ast.Constant)
                        and node.args[1].value in permitted_open_flags
                        and isinstance(node.args[2], ast.Constant)
                        and type(node.args[2].value) is int
                        and node.args[2].value == 0
                    ):
                        return {node.args[1].value}
                    if isinstance(node, ast.Constant) and node.value == 0:
                        return set()
                    return None

                def visit_Call(self, node: ast.Call) -> None:
                    qualified = self.qualified(node.func)
                    attribute = (
                        node.func.attr
                        if isinstance(node.func, ast.Attribute)
                        else qualified.rsplit(".", 1)[-1]
                    )
                    function_qualified = self.qualified(node.func)
                    permitted_capability_pread = (
                        path.name == "wal.py"
                        and attribute == "pread"
                        and isinstance(node.func, ast.Attribute)
                        and self.qualified(node.func.value)
                        in {"read_capability", "self._read_capability"}
                    )
                    if function_qualified in self.reflective_functions:
                        self_test.fail(f"{path}:{node.lineno}")
                    if (
                        function_qualified in self.attribute_getters
                        and self.getter_access_is_dangerous(node)
                        and self.read_only_flag_names(node) is None
                    ):
                        self_test.fail(f"{path}:{node.lineno}")
                    if (
                        function_qualified
                        in self.low_level_attribute_getters
                        and self.getter_access_is_dangerous(node)
                    ):
                        self_test.fail(f"{path}:{node.lineno}")
                    if qualified == "os.open":
                        function = (
                            self.function_stack[-1]
                            if self.function_stack
                            else "<module>"
                        )
                        key = (path.name, function)
                        self_test.assertIn(
                            key,
                            permitted_read_only_os_open_counts,
                            f"{path}:{node.lineno}",
                        )
                        observed_counts[key] += 1
                        flags = (
                            node.args[1]
                            if len(node.args) >= 2
                            else next(
                                (
                                    item.value
                                    for item in node.keywords
                                    if item.arg == "flags"
                                ),
                                None,
                            )
                        )
                        flag_names = (
                            None
                            if flags is None
                            else self.read_only_flag_names(flags)
                        )
                        self_test.assertIsNotNone(
                            flag_names, f"{path}:{node.lineno}"
                        )
                        self_test.assertIn(
                            "O_RDONLY",
                            flag_names,
                            f"{path}:{node.lineno}",
                        )
                    else:
                        if (
                            function_qualified
                            not in self.attribute_getters
                            | self.low_level_attribute_getters
                            and not permitted_capability_pread
                            and self.value_has_forbidden_reference(node.func)
                        ):
                            self_test.fail(f"{path}:{node.lineno}")
                        self_test.assertNotIn(
                            qualified,
                            forbidden_os_calls | forbidden_path_calls,
                            f"{path}:{node.lineno}",
                        )
                        if not permitted_capability_pread:
                            self_test.assertNotIn(
                                attribute,
                                forbidden_attributes,
                                f"{path}:{node.lineno}",
                            )
                        if attribute in capability_calls:
                            self_test.assertEqual(
                                path.name,
                                "wal.py",
                                f"{path}:{node.lineno}",
                            )
                            if attribute == "pread":
                                self_test.assertTrue(
                                    permitted_capability_pread,
                                    f"{path}:{node.lineno}",
                                )
                    self.generic_visit(node)

            self_test = self
            AuthorityVisitor().visit(tree)

        for path in package.rglob("*.py"):
            scan_path(path, observed_read_only_os_open_counts)
        self.assertEqual(
            observed_read_only_os_open_counts,
            permitted_read_only_os_open_counts,
        )

        fixtures = {
            "assigned_open.py": (
                "import os\n"
                "dangerous_open = os.open\n"
                "dangerous_open('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "assigned_open_chain.py": (
                "import os\n"
                "first = os.open\n"
                "second = first\n"
                "second('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "assigned_module.py": (
                "import os\n"
                "filesystem = os\n"
                "filesystem.open('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "tuple_assigned_open.py": (
                "import os\n"
                "(provider_open,) = (os.open,)\n"
                "provider_open('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "list_assigned_namespace_chain.py": (
                "import os\n"
                "[first, second] = [os.rename, os.replace]\n"
                "(move,) = (second,)\n"
                "move('source', 'provider.wal')\n"
            ),
            "container_subscript_open.py": (
                "import os\n"
                "operations = [os.open]\n"
                "operations[0]('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "list_constructor_open.py": (
                "import os\n"
                "\n"
                "operations = list((os.open,))\n"
                "operations[0](\n"
                '    "provider.wal", os.O_WRONLY | os.O_CREAT\n'
                ")\n"
            ),
            "tuple_constructor_open.py": (
                "import os\n"
                "operations = tuple((os.open,))\n"
                "operations[0]"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "set_constructor_open.py": (
                "import os\n"
                "operations = set((os.open,))\n"
                "operations.pop()"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "dict_constructor_open.py": (
                "import os\n"
                "operations = dict(provider_open=os.open)\n"
                "operations['provider_open']"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "reflected_open.py": (
                "import os\n"
                "getattr(os, 'open')('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "factory_module_dynamic_attribute_open.py": (
                "import os\n"
                "\n"
                "def factory():\n"
                "    return os\n"
                "\n"
                "module = factory()\n"
                'attribute = "open"\n'
                "getattr(module, attribute)(\n"
                '    "provider.wal", os.O_WRONLY | os.O_CREAT\n'
                ")\n"
            ),
            "factory_module_constant_attribute_open.py": (
                "import os\n"
                "def factory():\n"
                "    return os\n"
                "module = factory()\n"
                "getattr(module, 'open')"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "os_constant_variable_attribute_open.py": (
                "import os\n"
                "attribute = 'open'\n"
                "getattr(os, attribute)"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "builtins_getattr_open.py": (
                "import builtins\n"
                "import os\n"
                "builtins.getattr(os, 'open')"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "attribute_target_open.py": (
                "import os\n"
                "class Operations:\n"
                "    pass\n"
                "operations = Operations()\n"
                "operations.provider_open = os.open\n"
                "operations.provider_open"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "subscript_target_open.py": (
                "import os\n"
                "operations = [None]\n"
                "operations[0] = os.open\n"
                "operations[0]"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "operator_attrgetter_open.py": (
                "from operator import attrgetter\n"
                "import os\n"
                "attrgetter('open')(os)"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "builtins_getattr_alias_unlink.py": (
                "from builtins import getattr as reflect\n"
                "import os\n"
                "reflect(os, 'unlink')('provider.wal')\n"
            ),
            "builtins_vars_open.py": (
                "import builtins\n"
                "import os\n"
                "builtins.vars(os)['open']"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "builtins_globals_open.py": (
                "import builtins\n"
                "import os\n"
                "builtins.globals()['os'].open"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "nested_attribute_target_replace.py": (
                "import os\n"
                "class Operations:\n"
                "    pass\n"
                "operations = Operations()\n"
                "operations.nested = Operations()\n"
                "[operations.nested.move] = [os.replace]\n"
                "operations.nested.move('source', 'provider.wal')\n"
            ),
            "nested_subscript_target_link.py": (
                "import os\n"
                "operations = {'nested': [None]}\n"
                "(operations['nested'][0],) = (os.link,)\n"
                "operations['nested'][0]('source', 'provider.wal')\n"
            ),
            "starred_target_open.py": (
                "import os\n"
                "operations = [None]\n"
                "[*operations] = [os.open]\n"
                "operations[0]"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "annotated_attribute_target_open.py": (
                "import os\n"
                "class Operations:\n"
                "    pass\n"
                "operations = Operations()\n"
                "operations.provider_open: object = os.open\n"
                "operations.provider_open"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "augmented_list_target_open.py": (
                "import os\n"
                "operations = []\n"
                "operations += [os.open]\n"
                "operations[0]"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "binop_attribute_target_open.py": (
                "import os\n"
                "class Operations:\n"
                "    pass\n"
                "operations = Operations()\n"
                "operations.provider_calls = [os.open] + []\n"
                "operations.provider_calls[0]"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "binop_subscript_target_open.py": (
                "import os\n"
                "operations = [None]\n"
                "operations[0] = [os.open] + []\n"
                "operations[0][0]"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "comprehension_iterable_open.py": (
                "import os\n"
                "operations = [operation for operation in (os.open,)]\n"
                "operations[0]"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "comprehension_payload_open.py": (
                "import os\n"
                "operations = [os.open for _ in (0,)]\n"
                "operations[0]"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "conditional_object_getattribute_open.py": (
                "import os\n"
                "flag = True\n"
                "safe = object()\n"
                "object.__getattribute__(os if flag else safe, 'open')"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "operator_star_import_attrgetter_open.py": (
                "from operator import *\n"
                "import os\n"
                "attrgetter('open')(os)"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "operator_qualified_attrgetter_symlink.py": (
                "import operator\n"
                "import os\n"
                "operator.attrgetter('symlink')(os)"
                "('source', 'provider.wal')\n"
            ),
            "operator_aliased_attrgetter_open.py": (
                "import operator as op\n"
                "import os\n"
                "picker = op.attrgetter\n"
                "picker('open')(os)"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "operator_methodcaller_unlink.py": (
                "from operator import methodcaller\n"
                "import os\n"
                "methodcaller('unlink', 'provider.wal')(os)\n"
            ),
            "vars_subscript_open.py": (
                "import os\n"
                "vars(os)['open']('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "module_dict_unlink.py": (
                "import os\n"
                "os.__dict__['unlink']('provider.wal')\n"
            ),
            "globals_subscript_open.py": (
                "import os\n"
                "globals()['os'].__dict__['open']('provider.wal', 1)\n"
            ),
            "locals_subscript_replace.py": (
                "import os\n"
                "locals()['os'].__dict__['replace']('a', 'provider.wal')\n"
            ),
            "getattr_chain_open.py": (
                "import os\n"
                "getattr(getattr(os, 'open'), '__call__')"
                "('provider.wal', os.O_WRONLY | os.O_CREAT)\n"
            ),
            "object_getattribute_link.py": (
                "import os\n"
                "object.__getattribute__(os, 'link')('a', 'provider.wal')\n"
            ),
            "dynamic_import.py": (
                "__import__('os').open('provider.wal', 1)\n"
            ),
            "dynamic_import_reflected.py": (
                "vars(__import__('os'))['symlink']('a', 'provider.wal')\n"
            ),
            "importlib_reflection.py": (
                "import importlib\n"
                "importlib.import_module('os').replace('a', 'provider.wal')\n"
            ),
            "path_open.py": (
                "from pathlib import Path\n"
                "path = Path('provider.wal')\n"
                "path.open('rb')\n"
            ),
            "path_read.py": (
                "from pathlib import Path\n"
                "path = Path('provider.wal')\n"
                "path.read_bytes()\n"
            ),
            "mapped.py": (
                "import mmap\n"
                "mmap.mmap(-1, 1)\n"
            ),
            "path_unlink.py": (
                "from pathlib import Path\n"
                "path = Path('provider.wal')\n"
                "path.unlink()\n"
            ),
            "aliased_remove.py": (
                "import os as filesystem\n"
                "filesystem.remove('provider.wal')\n"
            ),
            "os_rename.py": (
                "import os\n"
                "os.rename('a', 'provider.wal')\n"
            ),
            "os_replace.py": (
                "import os\n"
                "os.replace('a', 'provider.wal')\n"
            ),
            "os_link.py": (
                "import os\n"
                "os.link('a', 'provider.wal')\n"
            ),
            "path_write_text.py": (
                "from pathlib import Path\n"
                "Path('provider.wal').write_text('provider bytes')\n"
            ),
            "pinned_file.py": (
                "import os\n"
                "def _trusted_repo_identity():\n"
                "    flags = os.O_RDWR\n"
                "    return os.open('provider.wal', flags)\n"
            ),
            "wal.py": (
                "class UnsafeReader:\n"
                "    def pread(self, *, offset, length):\n"
                "        return b''\n"
                "unsafe_reader = UnsafeReader()\n"
                "unsafe_reader.pread(offset=0, length=1)\n"
            ),
        }
        fixture_root = self.root / "authority-fixtures"
        fixture_root.mkdir()
        for name, source in fixtures.items():
            with self.subTest(fixture=name):
                path = fixture_root / name
                path.write_text(source, encoding="utf-8")
                with self.assertRaises(AssertionError):
                    scan_path(
                        path,
                        {
                            key: 0
                            for key in permitted_read_only_os_open_counts
                        },
                    )

        safe_fixtures = {
            "safe_factory_getattr.py": (
                "class Value:\n"
                "    field = 'safe'\n"
                "def factory():\n"
                "    return Value()\n"
                "value = getattr(factory(), 'field')\n"
                "assert value == 'safe'\n"
            ),
            "wal.py": (
                "def open_reader(read_capability):\n"
                "    return read_capability.pread(offset=0, length=0)\n"
                "class Reader:\n"
                "    def read(self):\n"
                "        return self._read_capability.pread(\n"
                "            offset=0, length=1\n"
                "        )\n"
            ),
        }
        for name, source in safe_fixtures.items():
            with self.subTest(fixture=name):
                path = fixture_root / name
                path.write_text(source, encoding="utf-8")
                scan_path(
                    path,
                    {
                        key: 0
                        for key in permitted_read_only_os_open_counts
                    },
                )

        retention_source = (package / "retention.py").read_text(encoding="utf-8")
        self.assertNotIn("tennis_v1.wal", retention_source)
        self.assertNotIn("from . import wal", retention_source)
        self.assertNotIn("from .wal", retention_source)

    def test_closed_exact_read_capability_can_latch_replay_manifest_rejection(self):
        code = r"""
from tennis_v1.retention import (
    RetentionCoordinator,
    RetentionError,
    RetentionGlobalHalt,
    _reject_replay_manifest,
)
from tennis_v1.sequencer import EventRuntime, bind_provider_persistence_authorizer
from tennis_v1.state import initial_state
from tennis_v1.wal import JournalWriter
from tests.tennis_v1.test_sequencer import concrete_environment

with concrete_environment() as (_, coordinator, gate, manifest):
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
    read_capability = coordinator.issue_read_capability(
        persistence_authorizer=authorizer,
    )
    read_capability.close()
    try:
        _reject_replay_manifest(
            read_capability=read_capability,
            persistence_authorizer=authorizer,
            coordinator=coordinator,
            session_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )
    except RetentionError:
        pass
    else:
        raise AssertionError("wrong session accepted")
    coordinator.require_provider_operation()
    result = _reject_replay_manifest(
        read_capability=read_capability,
        persistence_authorizer=authorizer,
        coordinator=coordinator,
        session_id=manifest.session_id,
    )
    assert result is None
    try:
        coordinator.require_provider_operation()
    except RetentionGlobalHalt:
        pass
    else:
        raise AssertionError("manifest rejection did not halt")
print("replay-manifest-rejection-ok")
"""
        completed = subprocess.run(
            [PYTHON, "-c", code],
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
        self.assertIn("replay-manifest-rejection-ok", completed.stdout)

    def test_expert_root_request_preissue_global_halt_rejects_without_state(
        self,
    ):
        self._run_expert_global_halt_script(
            """
_latch_global_halt(
    coordinator,
    session_id=None,
    ambiguous=True,
)
try:
    coordinator.issue_expert_state_root_account_lock_request()
except RetentionGlobalHalt as error:
    assert str(error) == "retention_global_halt"
else:
    raise AssertionError("preissue global halt was ignored")
assert coordinator._expert_root_issued is False
assert coordinator._expert_root_requests == {}
assert coordinator._expert_root_grants == {}
assert coordinator._expert_clock_capabilities == {}
""",
            marker="expert-root-preissue-halt-ok",
        )

    def test_expert_root_request_postissue_global_halt_consumes_request(
        self,
    ):
        self._run_expert_global_halt_script(
            """
request = coordinator.issue_expert_state_root_account_lock_request()
_latch_global_halt(
    coordinator,
    session_id=None,
    ambiguous=True,
)
with mock.patch.object(
    os,
    "dup",
    side_effect=AssertionError("secret-duplicate-after-halt"),
):
    try:
        _consume_expert_state_root_account_lock_request(request)
    except RetentionGlobalHalt as error:
        assert str(error) == "retention_global_halt"
    else:
        raise AssertionError("postissue global halt was ignored")
assert coordinator._expert_root_requests == {}
assert coordinator._expert_root_grants == {}
assert coordinator._expert_clock_capabilities == {}
try:
    _consume_expert_state_root_account_lock_request(request)
except RetentionError as error:
    assert str(error) == "expert_state_root_request_stale"
else:
    raise AssertionError("halted request remained reusable")
""",
            marker="expert-root-postissue-halt-ok",
        )

    def test_expert_root_request_halt_during_duplication_closes_partial_fds(
        self,
    ):
        self._run_expert_global_halt_script(
            """
request = coordinator.issue_expert_state_root_account_lock_request()
duplicates = []
original_dup = os.dup

def latch_after_first_dup(fd):
    duplicate = original_dup(fd)
    duplicates.append(duplicate)
    if len(duplicates) == 1:
        _latch_global_halt(
            coordinator,
            session_id=None,
            ambiguous=True,
        )
    return duplicate

with mock.patch.object(os, "dup", side_effect=latch_after_first_dup):
    try:
        _consume_expert_state_root_account_lock_request(request)
    except RetentionGlobalHalt as error:
        assert str(error) == "retention_global_halt"
    else:
        raise AssertionError("duplication race returned a grant")
assert len(duplicates) == 1
assert coordinator._expert_root_requests == {}
assert coordinator._expert_root_grants == {}
assert coordinator._expert_clock_capabilities == {}
for fd in duplicates:
    try:
        os.fstat(fd)
    except OSError:
        pass
    else:
        raise AssertionError("partial duplicate leaked")
""",
            marker="expert-root-partial-dup-halt-ok",
        )

    def test_expert_clock_postsample_global_halt_revokes_grant_and_fds(
        self,
    ):
        self._run_expert_global_halt_script(
            """
request = coordinator.issue_expert_state_root_account_lock_request()
grant = _consume_expert_state_root_account_lock_request(request)
sampler = object.__getattribute__(grant, "_clock_capability")
duplicate_fds = tuple(
    object.__getattribute__(grant, name)
    for name in (
        "_state_fd",
        "_sessions_fd",
        "_markers_fd",
        "_lock_fd",
    )
)
assert sample_expert_retention_wall_ns(sampler) == 123
_latch_global_halt(
    coordinator,
    session_id=None,
    ambiguous=True,
)
try:
    sample_expert_retention_wall_ns(sampler)
except RetentionGlobalHalt as error:
    assert str(error) == "retention_global_halt"
else:
    raise AssertionError("postsample global halt was ignored")
assert coordinator._expert_root_grants == {}
assert coordinator._expert_clock_capabilities == {}
for fd in duplicate_fds:
    try:
        os.fstat(fd)
    except OSError:
        pass
    else:
        raise AssertionError("halted grant descriptor leaked")
assert _revoke_expert_state_root_account_lock_grant(grant) is None
assert _revoke_expert_state_root_account_lock_grant(grant) is None
try:
    sample_expert_retention_wall_ns(sampler)
except RetentionError as error:
    assert str(error) == "expert_retention_clock_capability_stale"
else:
    raise AssertionError("halted sampler remained reusable")
""",
            marker="expert-clock-postsample-halt-ok",
        )

    def test_expert_clock_failure_has_no_global_halt_lock_inversion(self):
        code = r"""
from pathlib import Path
import tempfile
import threading

import tennis_v1.retention as retention_module
from tennis_v1.retention import (
    RetentionCoordinator,
    RetentionGlobalHalt,
    _consume_expert_state_root_account_lock_request,
    sample_expert_retention_wall_ns,
)
from tests.tennis_v1.test_retention import make_config

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary).resolve()
    clock_entered = threading.Event()
    contender_attempting = threading.Event()
    contender_done = threading.Event()
    broken = False

    def clock():
        if not broken:
            return 123
        clock_entered.set()
        if not contender_attempting.wait(5):
            raise AssertionError("contender did not reach lock seam")
        raise RuntimeError("clock failure")

    first = RetentionCoordinator.acquire(
        make_config(root / "first"),
        clock_ns=clock,
    )
    second = RetentionCoordinator.acquire(
        make_config(root / "second"),
        clock_ns=lambda: 123,
    )
    try:
        first.recover_and_purge()
        second.recover_and_purge()
        request = first.issue_expert_state_root_account_lock_request()
        grant = _consume_expert_state_root_account_lock_request(request)
        sampler = object.__getattribute__(grant, "_clock_capability")

        def contend():
            with second._condition:
                if not clock_entered.wait(5):
                    raise AssertionError("clock did not reach failure seam")
                contender_attempting.set()
                with retention_module._PROVIDER_IO_LOCK:
                    pass
            contender_done.set()

        thread = threading.Thread(target=contend)
        thread.start()
        broken = True
        try:
            sample_expert_retention_wall_ns(sampler)
        except RetentionGlobalHalt as error:
            assert str(error) == "retention_clock_failed"
        else:
            raise AssertionError("clock failure did not halt")
        thread.join(timeout=5)
        assert contender_done.is_set()
        assert not thread.is_alive()
    finally:
        first.close()
        second.close()
print("expert-clock-lock-order-ok")
"""
        completed = subprocess.run(
            [
                "/Users/mthanki/.venvs/inci-expert-py314/bin/python",
                "-B",
                "-c",
                code,
            ],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        self.assertEqual(
            completed.stdout.strip(),
            "expert-clock-lock-order-ok",
        )

    def test_expert_close_waits_for_root_request_issue(self):
        self._run_expert_close_race_script("issue")

    def test_expert_close_waits_for_root_request_consumption(self):
        self._run_expert_close_race_script("consume")

    def test_expert_close_waits_for_root_grant_revocation(self):
        self._run_expert_close_race_script("revoke")

    def test_expert_close_waits_for_clock_sample(self):
        self._run_expert_close_race_script("sample")

    def test_expert_close_has_one_winner_and_rejects_new_root_operation(
        self,
    ):
        code = r"""
from pathlib import Path
import tempfile
import threading
from unittest import mock

from tennis_v1.retention import RetentionCoordinator, RetentionError
from tests.tennis_v1.test_retention import make_config

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary).resolve()
    coordinator = RetentionCoordinator.acquire(
        make_config(root / "state"),
        clock_ns=lambda: 123,
    )
    coordinator.recover_and_purge()
    initial_generation = coordinator._generation
    operation_entered = threading.Event()
    both_closers_called = threading.Event()
    close_waiting = threading.Event()
    close_done = [threading.Event(), threading.Event()]
    close_errors = []
    close_calls = [0]
    close_calls_lock = threading.Lock()
    wait_states = []
    original_validate = RetentionCoordinator._validate_roots_and_lock
    original_wait = coordinator._condition.wait
    nested = False

    def tracked_wait(timeout=None):
        if threading.current_thread().name.startswith("expert-closer-"):
            wait_states.append(coordinator._closing)
            close_waiting.set()
        return original_wait(timeout)

    coordinator._condition.wait = tracked_wait

    def close_coordinator(index):
        if not operation_entered.wait(5):
            close_errors.append(
                AssertionError("issue did not reach close seam")
            )
            close_done[index].set()
            return
        with close_calls_lock:
            close_calls[0] += 1
            is_second_close = close_calls[0] == 2
        if is_second_close:
            both_closers_called.set()
        try:
            coordinator.close()
        except BaseException as error:
            close_errors.append(error)
        finally:
            close_done[index].set()

    threads = [
        threading.Thread(
            target=close_coordinator,
            args=(index,),
            name=f"expert-closer-{index}",
        )
        for index in range(2)
    ]
    for thread in threads:
        thread.start()

    def blocking_validate(instance):
        global nested
        assert instance is coordinator
        if nested:
            return original_validate(instance)
        operation_entered.set()
        if not both_closers_called.wait(5):
            raise AssertionError("both closers did not start")
        if not close_waiting.wait(5):
            raise AssertionError("winning close did not enter inflight wait")
        assert not any(event.is_set() for event in close_done)
        nested = True
        try:
            try:
                coordinator.issue_expert_state_root_account_lock_request()
            except RetentionError as error:
                assert str(error) == "retention_coordinator_closed"
            else:
                raise AssertionError(
                    "new root operation started after close claim"
                )
        finally:
            nested = False
        return original_validate(instance)

    with mock.patch.object(
        RetentionCoordinator,
        "_validate_roots_and_lock",
        autospec=True,
        side_effect=blocking_validate,
    ):
        try:
            coordinator.issue_expert_state_root_account_lock_request()
        except RetentionError as error:
            assert str(error) == "retention_coordinator_closed"
        else:
            raise AssertionError("inflight issue committed after close claim")

    for thread in threads:
        thread.join(timeout=5)
    coordinator._condition.wait = original_wait
    assert all(event.is_set() for event in close_done)
    assert all(not thread.is_alive() for thread in threads)
    assert close_errors == []
    assert wait_states != []
    assert all(wait_states)
    assert coordinator._generation == initial_generation + 1
    assert coordinator._expert_root_operations_inflight == 0
    assert coordinator._expert_root_requests == {}
    assert coordinator._closed is True
print("expert-close-one-winner-ok")
"""
        completed = subprocess.run(
            [
                "/Users/mthanki/.venvs/inci-expert-py314/bin/python",
                "-B",
                "-c",
                code,
            ],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            capture_output=True,
            timeout=12,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        self.assertEqual(
            completed.stdout.strip(),
            "expert-close-one-winner-ok",
        )

    def test_expert_consume_cleanup_close_failure_has_no_global_halt_lock_inversion(
        self,
    ):
        code = r"""
import os
from pathlib import Path
import tempfile
import threading
from unittest import mock

import tennis_v1.retention as retention_module
from tennis_v1.retention import (
    RetentionCoordinator,
    RetentionGlobalHalt,
    _consume_expert_state_root_account_lock_request,
)
from tests.tennis_v1.test_retention import make_config

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary).resolve()
    close_entered = threading.Event()
    contender_attempting = threading.Event()
    contender_done = threading.Event()
    duplicates = []
    original_dup = os.dup
    original_close = os.close
    duplicate_calls = 0

    first = RetentionCoordinator.acquire(
        make_config(root / "first"),
        clock_ns=lambda: 123,
    )
    second = RetentionCoordinator.acquire(
        make_config(root / "second"),
        clock_ns=lambda: 123,
    )
    try:
        first.recover_and_purge()
        second.recover_and_purge()
        request = first.issue_expert_state_root_account_lock_request()

        def partial_dup(fd):
            global duplicate_calls
            duplicate_calls += 1
            if duplicate_calls == 1:
                duplicate = original_dup(fd)
                duplicates.append(duplicate)
                return duplicate
            raise OSError("forced duplicate failure")

        def close_then_report_failure(fd):
            assert fd == duplicates[0]
            close_entered.set()
            if not contender_attempting.wait(5):
                raise AssertionError("contender did not reach lock seam")
            original_close(fd)
            raise OSError("forced close failure")

        def contend():
            with second._condition:
                if not close_entered.wait(5):
                    raise AssertionError("close did not reach failure seam")
                contender_attempting.set()
                with retention_module._PROVIDER_IO_LOCK:
                    pass
            contender_done.set()

        thread = threading.Thread(target=contend)
        thread.start()
        with (
            mock.patch.object(os, "dup", side_effect=partial_dup),
            mock.patch.object(
                os,
                "close",
                side_effect=close_then_report_failure,
            ),
        ):
            try:
                _consume_expert_state_root_account_lock_request(request)
            except RetentionGlobalHalt as error:
                assert str(error) == "expert_state_root_grant_close_failed"
            else:
                raise AssertionError("close failure did not halt")
        thread.join(timeout=5)
        assert contender_done.is_set()
        assert not thread.is_alive()
        assert first._expert_root_grants == {}
        assert first._expert_clock_capabilities == {}
        try:
            os.fstat(duplicates[0])
        except OSError:
            pass
        else:
            raise AssertionError("failed-close duplicate leaked")
    finally:
        first.close()
        second.close()
print("expert-close-lock-order-ok")
"""
        completed = subprocess.run(
            [
                "/Users/mthanki/.venvs/inci-expert-py314/bin/python",
                "-B",
                "-c",
                code,
            ],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        self.assertEqual(
            completed.stdout.strip(),
            "expert-close-lock-order-ok",
        )

    def test_expert_coordinator_close_failure_has_no_global_halt_lock_inversion(
        self,
    ):
        code = r"""
import os
from pathlib import Path
import tempfile
import threading
from unittest import mock

import tennis_v1.retention as retention_module
from tennis_v1.retention import RetentionCoordinator, RetentionGlobalHalt
from tests.tennis_v1.test_retention import make_config

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary).resolve()
    latch_started = threading.Event()
    contender_has_provider = threading.Event()
    contender_done = threading.Event()
    original_close = os.close
    original_latch = retention_module._latch_global_halt

    first = RetentionCoordinator.acquire(
        make_config(root / "first"),
        clock_ns=lambda: 123,
    )
    second = RetentionCoordinator.acquire(
        make_config(root / "second"),
        clock_ns=lambda: 123,
    )
    first.recover_and_purge()
    second.recover_and_purge()
    failing_fd = first._markers_fd

    def fail_one_descriptor(fd):
        if fd == failing_fd:
            raise OSError("forced coordinator close failure")
        return original_close(fd)

    def announced_latch(*args, **kwargs):
        latch_started.set()
        return original_latch(*args, **kwargs)

    def contend():
        with second._condition:
            with retention_module._PROVIDER_IO_LOCK:
                contender_has_provider.set()
                if not latch_started.wait(5):
                    raise AssertionError("close did not reach latch seam")
                with first._condition:
                    pass
        contender_done.set()

    thread = threading.Thread(target=contend)
    thread.start()
    assert contender_has_provider.wait(5)
    with (
        mock.patch.object(os, "close", side_effect=fail_one_descriptor),
        mock.patch.object(
            retention_module,
            "_latch_global_halt",
            side_effect=announced_latch,
        ),
    ):
        try:
            first.close()
        except RetentionGlobalHalt as error:
            assert str(error) == "retention_descriptor_close_failed"
        else:
            raise AssertionError("coordinator close failure did not halt")
    thread.join(timeout=5)
    assert contender_done.is_set()
    assert not thread.is_alive()
    second.close()
print("expert-coordinator-close-lock-order-ok")
"""
        completed = subprocess.run(
            [
                "/Users/mthanki/.venvs/inci-expert-py314/bin/python",
                "-B",
                "-c",
                code,
            ],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        self.assertEqual(
            completed.stdout.strip(),
            "expert-coordinator-close-lock-order-ok",
        )

    def test_expert_root_request_is_one_shot_and_transfers_same_clock(self):
        from tennis_v1.retention import (
            ExpertStateRootAccountLockRequestV1,
            _consume_expert_state_root_account_lock_request,
            _revoke_expert_state_root_account_lock_grant,
            sample_expert_retention_wall_ns,
        )

        coordinator = self.acquire()
        request = (
            coordinator.issue_expert_state_root_account_lock_request()
        )
        self.assertIs(type(request), ExpertStateRootAccountLockRequestV1)
        self.assertEqual(
            repr(request),
            "<ExpertStateRootAccountLockRequestV1 redacted>",
        )
        for forbidden in (
            "path",
            "state_root",
            "fd",
            "lock_fd",
            "clock",
            "callback",
        ):
            self.assertNotIn(forbidden, request.__slots__)

        original_identities = tuple(
            (value.st_dev, value.st_ino)
            for value in (
                os.fstat(coordinator._state_fd),
                os.fstat(coordinator._sessions_fd),
                os.fstat(coordinator._markers_fd),
                os.fstat(coordinator._lock_fd),
            )
        )
        with mock.patch.object(
            os,
            "open",
            side_effect=AssertionError("request consumption reopened a path"),
        ):
            grant = _consume_expert_state_root_account_lock_request(
                request
            )
        duplicate_identities = tuple(
            (value.st_dev, value.st_ino)
            for value in (
                os.fstat(object.__getattribute__(grant, "_state_fd")),
                os.fstat(object.__getattribute__(grant, "_sessions_fd")),
                os.fstat(object.__getattribute__(grant, "_markers_fd")),
                os.fstat(object.__getattribute__(grant, "_lock_fd")),
            )
        )
        self.assertEqual(duplicate_identities, original_identities)

        sampler = object.__getattribute__(grant, "_clock_capability")
        for now_ns in (
            self.manifest.required_retention_until_ns - 1,
            self.manifest.required_retention_until_ns,
            self.manifest.required_retention_until_ns + 1,
        ):
            self.clock.now_ns = now_ns
            self.assertEqual(
                sample_expert_retention_wall_ns(sampler),
                now_ns,
            )

        with self.assertRaisesRegex(
            RetentionError,
            r"\Aexpert_state_root_request_stale\Z",
        ):
            _consume_expert_state_root_account_lock_request(request)
        with self.assertRaisesRegex(
            RetentionError,
            r"\Aexpert_state_root_request_stale\Z",
        ):
            coordinator.issue_expert_state_root_account_lock_request()

        duplicate_fds = tuple(
            object.__getattribute__(grant, name)
            for name in (
                "_state_fd",
                "_sessions_fd",
                "_markers_fd",
                "_lock_fd",
            )
        )
        self.assertIsNone(
            _revoke_expert_state_root_account_lock_grant(grant)
        )
        self.assertIsNone(
            _revoke_expert_state_root_account_lock_grant(grant)
        )
        with self.assertRaisesRegex(
            RetentionError,
            r"\Aexpert_retention_clock_capability_stale\Z",
        ):
            sample_expert_retention_wall_ns(sampler)
        for fd in duplicate_fds:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_expert_clock_allows_descriptor_relative_state_child_bootstrap(
        self,
    ):
        from tennis_v1.retention import (
            _consume_expert_state_root_account_lock_request,
            _revoke_expert_state_root_account_lock_grant,
            sample_expert_retention_wall_ns,
        )

        coordinator = self.acquire()
        request = (
            coordinator.issue_expert_state_root_account_lock_request()
        )
        grant = _consume_expert_state_root_account_lock_request(request)
        state_fd = object.__getattribute__(grant, "_state_fd")
        sampler = object.__getattribute__(grant, "_clock_capability")
        before_links = os.fstat(state_fd).st_nlink
        os.mkdir("authorized-companion-v1", 0o700, dir_fd=state_fd)
        try:
            after_links = os.fstat(state_fd).st_nlink
            if after_links != before_links:
                self.assertEqual(after_links, before_links + 1)
            self.assertEqual(
                sample_expert_retention_wall_ns(sampler),
                self.clock.now_ns,
            )
            for offset in (1, 2):
                self.clock.now_ns += offset
                self.assertEqual(
                    sample_expert_retention_wall_ns(sampler),
                    self.clock.now_ns,
                )
        finally:
            try:
                os.rmdir("authorized-companion-v1", dir_fd=state_fd)
            except OSError:
                companion = self.state_root / "authorized-companion-v1"
                if companion.exists():
                    companion.rmdir()
            try:
                _revoke_expert_state_root_account_lock_grant(grant)
            except RetentionError:
                pass

    def test_expert_state_root_historical_identity_relaxes_only_link_count(
        self,
    ):
        for identity_name in (
            "state_identity",
            "sessions_identity",
            "markers_identity",
        ):
            with self.subTest(identity=identity_name):
                clock = CountingMutableClock(self.clock.now_ns)
                (
                    coordinator,
                    grant,
                    sampler,
                    authority,
                    duplicate_fds,
                ) = self.acquire_expert_grant(
                    f"historical-link-{identity_name}",
                    clock=clock,
                )
                identity = getattr(authority, identity_name)
                link_delta = (
                    17 if identity_name == "state_identity" else 1
                )
                setattr(
                    authority,
                    identity_name,
                    replace(identity, links=identity.links + link_delta),
                )
                prior_clock_calls = clock.calls
                self.assertEqual(
                    retention_module.sample_expert_retention_wall_ns(
                        sampler
                    ),
                    clock.now_ns,
                )
                clock.now_ns += 1
                self.assertEqual(
                    retention_module.sample_expert_retention_wall_ns(
                        sampler
                    ),
                    clock.now_ns,
                )
                self.assertEqual(clock.calls, prior_clock_calls + 2)
                self.assertIsNone(
                    retention_module._revoke_expert_state_root_account_lock_grant(
                        grant
                    )
                )
                self.assertEqual(coordinator._expert_root_grants, {})
                self.assertEqual(
                    coordinator._expert_clock_capabilities,
                    {},
                )
                for fd in duplicate_fds:
                    with self.assertRaises(OSError):
                        os.fstat(fd)

    def test_expert_state_root_historical_identity_field_matrix_revokes(
        self,
    ):
        mutations = {
            "device": lambda identity: identity.device + 1,
            "inode": lambda identity: identity.inode + 1,
            "mode": lambda identity: identity.mode ^ stat.S_IWGRP,
            "owner": lambda identity: identity.owner + 1,
        }
        for identity_name in (
            "state_identity",
            "sessions_identity",
            "markers_identity",
        ):
            for field_name, mutate in mutations.items():
                with self.subTest(
                    identity=identity_name,
                    field=field_name,
                ):
                    clock = CountingMutableClock(self.clock.now_ns)
                    (
                        coordinator,
                        _grant,
                        sampler,
                        authority,
                        duplicate_fds,
                    ) = self.acquire_expert_grant(
                        f"historical-{identity_name}-{field_name}",
                        clock=clock,
                    )
                    identity = getattr(authority, identity_name)
                    setattr(
                        authority,
                        identity_name,
                        replace(
                            identity,
                            **{field_name: mutate(identity)},
                        ),
                    )
                    self.assert_expert_sample_rejects_and_revokes(
                        coordinator,
                        sampler,
                        duplicate_fds,
                        clock=clock,
                    )
        for identity_name in (
            "sessions_identity",
            "markers_identity",
        ):
            with self.subTest(
                identity=identity_name,
                field="links_two_lower",
            ):
                clock = CountingMutableClock(self.clock.now_ns)
                (
                    coordinator,
                    _grant,
                    sampler,
                    authority,
                    duplicate_fds,
                ) = self.acquire_expert_grant(
                    f"historical-{identity_name}-links-two-lower",
                    clock=clock,
                )
                identity = getattr(authority, identity_name)
                setattr(
                    authority,
                    identity_name,
                    replace(identity, links=identity.links + 2),
                )
                self.assert_expert_sample_rejects_and_revokes(
                    coordinator,
                    sampler,
                    duplicate_fds,
                    clock=clock,
                )

    def test_expert_sessions_identity_refresh_accepts_only_zero_or_one_authorized_removal(
        self,
    ):
        def acquire_case(
            suffix: str,
            children: tuple[str, ...] = (),
        ):
            coordinator = RetentionCoordinator.acquire(
                make_config(self.root / f"sessions-refresh-{suffix}"),
                clock_ns=self.clock,
            )
            self.coordinators.append(coordinator)
            self.assertEqual(
                coordinator.recover_and_purge(),
                retention_report(),
            )
            for child in children:
                os.mkdir(child, 0o700, dir_fd=coordinator._sessions_fd)
            if children:
                os.fsync(coordinator._sessions_fd)
            request = (
                coordinator.issue_expert_state_root_account_lock_request()
            )
            grant = (
                retention_module._consume_expert_state_root_account_lock_request(
                    request
                )
            )
            sampler = object.__getattribute__(
                grant,
                "_clock_capability",
            )
            authority = coordinator._expert_clock_capabilities[sampler]
            duplicate_fds = tuple(
                object.__getattribute__(grant, name)
                for name in (
                    "_state_fd",
                    "_sessions_fd",
                    "_markers_fd",
                    "_lock_fd",
                )
            )
            return (
                coordinator,
                grant,
                sampler,
                authority,
                duplicate_fds,
            )

        for suffix, removed in (
            ("unchanged", ()),
            ("one-removal", ("authorized-child",)),
        ):
            with self.subTest(accepted=suffix):
                (
                    coordinator,
                    grant,
                    sampler,
                    authority,
                    duplicate_fds,
                ) = acquire_case(suffix, removed)
                with coordinator._condition:
                    transition = (
                        coordinator._prepare_expert_sessions_identity_refresh()
                    )
                for child in removed:
                    os.rmdir(child, dir_fd=coordinator._sessions_fd)
                os.fsync(coordinator._sessions_fd)
                with coordinator._condition:
                    coordinator._commit_expert_sessions_identity_refresh(
                        transition
                    )
                current = retention_module._file_identity(
                    os.fstat(coordinator._sessions_fd)
                )
                self.assertEqual(authority.sessions_identity, current)
                self.assertEqual(
                    retention_module._file_identity(
                        os.fstat(authority.sessions_fd)
                    ),
                    current,
                )
                self.assertEqual(
                    retention_module._file_identity(
                        os.stat(
                            "sessions",
                            dir_fd=coordinator._state_fd,
                            follow_symlinks=False,
                        )
                    ),
                    current,
                )
                self.assertEqual(
                    retention_module.sample_expert_retention_wall_ns(
                        sampler
                    ),
                    self.clock.now_ns,
                )
                self.assertIsNone(
                    retention_module._revoke_expert_state_root_account_lock_grant(
                        grant
                    )
                )
                for fd in duplicate_fds:
                    with self.assertRaises(OSError):
                        os.fstat(fd)

        for suffix, before_children, after_children in (
            ("addition", (), ("unexpected-child",)),
            (
                "two-removals",
                ("first-child", "second-child"),
                (),
            ),
        ):
            with self.subTest(rejected=suffix):
                (
                    coordinator,
                    _grant,
                    sampler,
                    _authority,
                    duplicate_fds,
                ) = acquire_case(suffix, before_children)
                with coordinator._condition:
                    transition = (
                        coordinator._prepare_expert_sessions_identity_refresh()
                    )
                for child in before_children:
                    os.rmdir(child, dir_fd=coordinator._sessions_fd)
                for child in after_children:
                    os.mkdir(child, 0o700, dir_fd=coordinator._sessions_fd)
                os.fsync(coordinator._sessions_fd)
                with coordinator._condition:
                    with self.assertRaises(RetentionError):
                        coordinator._commit_expert_sessions_identity_refresh(
                            transition
                        )
                self.assert_expert_sample_rejects_and_revokes(
                    coordinator,
                    sampler,
                    duplicate_fds,
                )
                for child in after_children:
                    os.rmdir(child, dir_fd=coordinator._sessions_fd)

    def _run_expert_arm_identity_refresh_failure(
        self,
        mode: str,
    ) -> None:
        code = r"""
import os
from pathlib import Path
import sys
import tempfile
from unittest import mock

from tennis_v1.retention import (
    RetentionCoordinator,
    RetentionError,
    RetentionGlobalHalt,
    _consume_expert_state_root_account_lock_request,
    sample_expert_retention_wall_ns,
)
from tests.tennis_v1.test_retention import (
    MutableClock,
    StrictAuthorizer,
    make_config,
    make_manifest_decision,
)

mode = sys.argv[1]
assert mode in {
    "wrong-link",
    "extra-entry",
    "replacement",
    "partial",
    "directory-time",
    "post-validation",
}
temporary = tempfile.TemporaryDirectory()
root = Path(temporary.name).resolve()
manifest, decision = make_manifest_decision()
clock = MutableClock(manifest.created_wall_ns)
coordinator = RetentionCoordinator.acquire(
    make_config(root / "state"),
    clock_ns=clock,
)
coordinator.recover_and_purge()
authorizer = StrictAuthorizer(coordinator, manifest, decision)
request = coordinator.issue_expert_state_root_account_lock_request()
grant = _consume_expert_state_root_account_lock_request(request)
sampler = object.__getattribute__(grant, "_clock_capability")
authority = coordinator._expert_clock_capabilities[sampler]
duplicate_fds = tuple(
    object.__getattribute__(grant, name)
    for name in (
        "_state_fd",
        "_sessions_fd",
        "_markers_fd",
        "_lock_fd",
    )
)
sessions_before = authority.sessions_identity
markers_before = authority.markers_identity
original_commit = (
    RetentionCoordinator._commit_expert_arm_identity_refresh
)

def require_halt():
    try:
        coordinator.arm_before_wal(
            session_manifest=manifest,
            decision=decision,
            persistence_authorizer=authorizer,
        )
    except RetentionGlobalHalt:
        return
    raise AssertionError("expert arm transition failure did not halt")

if mode == "wrong-link":
    original_fstat = os.fstat
    original_stat = os.stat
    sessions_fds = {
        coordinator._sessions_fd,
        authority.sessions_fd,
    }

    def changed_links(value):
        fields = list(value)
        fields[3] = sessions_before.links + 1
        return os.stat_result(fields)

    def wrong_fstat(fd):
        value = original_fstat(fd)
        return changed_links(value) if fd in sessions_fds else value

    def wrong_stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if (
            path == "sessions"
            and kwargs.get("dir_fd") == coordinator._state_fd
        ):
            return changed_links(value)
        return value

    def commit_with_wrong_delta(instance, transitions, **kwargs):
        with (
            mock.patch.object(
                os,
                "fstat",
                side_effect=wrong_fstat,
            ),
            mock.patch.object(
                os,
                "stat",
                side_effect=wrong_stat,
            ),
        ):
            return original_commit(instance, transitions, **kwargs)

    with mock.patch.object(
        RetentionCoordinator,
        "_commit_expert_arm_identity_refresh",
        new=commit_with_wrong_delta,
    ):
        require_halt()
elif mode == "extra-entry":
    def commit_with_extra_entry(instance, transitions, **kwargs):
        descriptor = os.open(
            "unexpected-entry",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=coordinator._sessions_fd,
        )
        os.close(descriptor)
        os.fsync(coordinator._sessions_fd)
        return original_commit(instance, transitions, **kwargs)

    with mock.patch.object(
        RetentionCoordinator,
        "_commit_expert_arm_identity_refresh",
        new=commit_with_extra_entry,
    ):
        require_halt()
elif mode == "replacement":
    foreign = root / "foreign-arm-sessions"
    foreign.mkdir(mode=0o700)
    foreign_fd = os.open(
        foreign,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )

    def commit_with_replacement(instance, transitions, **kwargs):
        os.dup2(foreign_fd, authority.sessions_fd)
        os.set_inheritable(authority.sessions_fd, False)
        return original_commit(instance, transitions, **kwargs)

    try:
        with mock.patch.object(
            RetentionCoordinator,
            "_commit_expert_arm_identity_refresh",
            new=commit_with_replacement,
        ):
            require_halt()
    finally:
        os.close(foreign_fd)
elif mode == "partial":
    original_create = RetentionCoordinator._create_file
    create_calls = 0

    def fail_reserve_creation(directory_fd, name, *, append=False):
        global create_calls
        create_calls += 1
        if create_calls == 3:
            raise OSError("forced-partial-arm")
        return original_create(
            directory_fd,
            name,
            append=append,
        )

    with mock.patch.object(
        RetentionCoordinator,
        "_create_file",
        new=staticmethod(fail_reserve_creation),
    ):
        require_halt()
    assert create_calls == 3
elif mode == "directory-time":
    def commit_with_time_regression(instance, transitions, **kwargs):
        value = os.fstat(coordinator._markers_fd)
        os.utime(
            coordinator._markers_fd,
            ns=(
                value.st_atime_ns,
                value.st_mtime_ns - 1_000_000_000,
            ),
        )
        return original_commit(instance, transitions, **kwargs)

    with mock.patch.object(
        RetentionCoordinator,
        "_commit_expert_arm_identity_refresh",
        new=commit_with_time_regression,
    ):
        require_halt()
else:
    original_validate = (
        RetentionCoordinator._validate_expert_root_binding
    )

    def fail_after_refresh(instance, candidate):
        original_validate(instance, candidate)
        if (
            instance is coordinator
            and candidate is authority
            and (
                candidate.sessions_identity != sessions_before
                or candidate.markers_identity != markers_before
            )
        ):
            raise RetentionError("forced-post-refresh-validation")

    with mock.patch.object(
        RetentionCoordinator,
        "_validate_expert_root_binding",
        new=fail_after_refresh,
    ):
        require_halt()

assert authority.sessions_identity == sessions_before
assert authority.markers_identity == markers_before
try:
    sample_expert_retention_wall_ns(sampler)
except RetentionError:
    pass
else:
    raise AssertionError("failed arm transition retained clock authority")
assert coordinator._expert_root_grants == {}
assert coordinator._expert_clock_capabilities == {}
try:
    sample_expert_retention_wall_ns(sampler)
except RetentionError as error:
    assert str(error) == "expert_retention_clock_capability_stale"
else:
    raise AssertionError("revoked expert clock capability was reusable")
for descriptor in duplicate_fds:
    try:
        os.fstat(descriptor)
    except OSError:
        pass
    else:
        raise AssertionError("failed arm transition retained root fd")
coordinator.close()
temporary.cleanup()
print(mode)
"""
        completed = subprocess.run(
            [
                "/Users/mthanki/.venvs/inci-expert-py314/bin/python",
                "-B",
                "-c",
                code,
                mode,
            ],
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
        self.assertEqual(completed.stdout.strip(), mode)

    def test_expert_arm_identity_refresh_rejects_wrong_link_delta(
        self,
    ):
        self._run_expert_arm_identity_refresh_failure("wrong-link")

    def test_expert_arm_identity_refresh_rejects_extra_entry(
        self,
    ):
        self._run_expert_arm_identity_refresh_failure("extra-entry")

    def test_expert_arm_identity_refresh_rejects_descriptor_replacement(
        self,
    ):
        self._run_expert_arm_identity_refresh_failure("replacement")

    def test_expert_arm_identity_refresh_rejects_partial_creation(
        self,
    ):
        self._run_expert_arm_identity_refresh_failure("partial")

    def test_expert_arm_identity_refresh_rejects_unrelated_directory_time_mutation(
        self,
    ):
        self._run_expert_arm_identity_refresh_failure("directory-time")

    def test_expert_arm_identity_refresh_rolls_back_post_validation_failure(
        self,
    ):
        self._run_expert_arm_identity_refresh_failure("post-validation")

    def test_expert_current_state_descriptor_parity_is_full_identity(
        self,
    ):
        clock = CountingMutableClock(self.clock.now_ns)
        (
            coordinator,
            _grant,
            sampler,
            authority,
            duplicate_fds,
        ) = self.acquire_expert_grant(
            "current-state-substitution",
            clock=clock,
        )
        foreign = self.root / "foreign-state-substitution"
        foreign.mkdir(mode=0o700)
        foreign_fd = os.open(
            foreign,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.dup2(foreign_fd, authority.state_fd)
            os.set_inheritable(authority.state_fd, False)
        finally:
            os.close(foreign_fd)
        self.assert_expert_sample_rejects_and_revokes(
            coordinator,
            sampler,
            duplicate_fds,
            clock=clock,
        )

        clock = CountingMutableClock(self.clock.now_ns)
        (
            coordinator,
            _grant,
            sampler,
            authority,
            duplicate_fds,
        ) = self.acquire_expert_grant(
            "current-state-link",
            clock=clock,
        )
        original_fstat = os.fstat

        def changed_duplicate_link(fd: int):
            value = original_fstat(fd)
            if fd != authority.state_fd:
                return value
            fields = list(value)
            fields[3] = value.st_nlink + 1
            return os.stat_result(fields)

        with mock.patch.object(
            os,
            "fstat",
            side_effect=changed_duplicate_link,
        ):
            self.assert_expert_sample_rejects_and_revokes(
                coordinator,
                sampler,
                duplicate_fds,
                clock=clock,
            )

    def test_expert_evidence_directories_retain_full_identity_and_name(
        self,
    ):
        evidence = (
            ("sessions", "sessions_fd", "_sessions_fd"),
            ("retention-markers", "markers_fd", "_markers_fd"),
        )
        for entry_name, authority_fd_name, coordinator_fd_name in evidence:
            with self.subTest(entry=entry_name, mutation="link"):
                clock = CountingMutableClock(self.clock.now_ns)
                (
                    coordinator,
                    _grant,
                    sampler,
                    authority,
                    duplicate_fds,
                ) = self.acquire_expert_grant(
                    f"evidence-{entry_name}-link",
                    clock=clock,
                )
                evidence_fd = getattr(authority, authority_fd_name)
                os.mkdir("matrix-child", 0o700, dir_fd=evidence_fd)
                try:
                    self.assert_expert_sample_rejects_and_revokes(
                        coordinator,
                        sampler,
                        duplicate_fds,
                        clock=clock,
                    )
                finally:
                    os.rmdir(
                        "matrix-child",
                        dir_fd=getattr(coordinator, coordinator_fd_name),
                    )

            with self.subTest(entry=entry_name, mutation="descriptor"):
                clock = CountingMutableClock(self.clock.now_ns)
                (
                    coordinator,
                    _grant,
                    sampler,
                    authority,
                    duplicate_fds,
                ) = self.acquire_expert_grant(
                    f"evidence-{entry_name}-descriptor",
                    clock=clock,
                )
                foreign = (
                    self.root / f"foreign-{entry_name}-descriptor"
                )
                foreign.mkdir(mode=0o700)
                foreign_fd = os.open(
                    foreign,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    target_fd = getattr(authority, authority_fd_name)
                    os.dup2(foreign_fd, target_fd)
                    os.set_inheritable(target_fd, False)
                finally:
                    os.close(foreign_fd)
                self.assert_expert_sample_rejects_and_revokes(
                    coordinator,
                    sampler,
                    duplicate_fds,
                    clock=clock,
                )

            with self.subTest(entry=entry_name, mutation="named-entry"):
                clock = CountingMutableClock(self.clock.now_ns)
                (
                    coordinator,
                    _grant,
                    sampler,
                    _authority,
                    duplicate_fds,
                ) = self.acquire_expert_grant(
                    f"evidence-{entry_name}-named",
                    clock=clock,
                )
                backup_name = entry_name + ".matrix-original"
                os.rename(
                    entry_name,
                    backup_name,
                    src_dir_fd=coordinator._state_fd,
                    dst_dir_fd=coordinator._state_fd,
                )
                os.mkdir(entry_name, 0o700, dir_fd=coordinator._state_fd)
                try:
                    self.assert_expert_sample_rejects_and_revokes(
                        coordinator,
                        sampler,
                        duplicate_fds,
                        clock=clock,
                    )
                finally:
                    os.rmdir(entry_name, dir_fd=coordinator._state_fd)
                    os.rename(
                        backup_name,
                        entry_name,
                        src_dir_fd=coordinator._state_fd,
                        dst_dir_fd=coordinator._state_fd,
                    )

    def test_expert_account_lock_retains_full_identity_and_single_link(
        self,
    ):
        with self.subTest(mutation="mode"):
            clock = CountingMutableClock(self.clock.now_ns)
            (
                coordinator,
                _grant,
                sampler,
                _authority,
                duplicate_fds,
            ) = self.acquire_expert_grant("lock-mode", clock=clock)
            os.fchmod(coordinator._lock_fd, 0o640)
            try:
                self.assert_expert_sample_rejects_and_revokes(
                    coordinator,
                    sampler,
                    duplicate_fds,
                    clock=clock,
                )
            finally:
                os.fchmod(coordinator._lock_fd, 0o600)

        with self.subTest(mutation="owner"):
            clock = CountingMutableClock(self.clock.now_ns)
            (
                coordinator,
                _grant,
                sampler,
                authority,
                duplicate_fds,
            ) = self.acquire_expert_grant("lock-owner", clock=clock)
            original_fstat = os.fstat
            lock_fds = {coordinator._lock_fd, authority.lock_fd}

            def changed_lock_owner(fd: int):
                value = original_fstat(fd)
                if fd not in lock_fds:
                    return value
                fields = list(value)
                fields[4] = value.st_uid + 1
                return os.stat_result(fields)

            with mock.patch.object(
                os,
                "fstat",
                side_effect=changed_lock_owner,
            ):
                self.assert_expert_sample_rejects_and_revokes(
                    coordinator,
                    sampler,
                    duplicate_fds,
                    clock=clock,
                )

        with self.subTest(mutation="hard-link"):
            clock = CountingMutableClock(self.clock.now_ns)
            (
                coordinator,
                _grant,
                sampler,
                _authority,
                duplicate_fds,
            ) = self.acquire_expert_grant("lock-link", clock=clock)
            os.link(
                "retention.lock",
                "retention.lock.matrix-link",
                src_dir_fd=coordinator._state_fd,
                dst_dir_fd=coordinator._state_fd,
                follow_symlinks=False,
            )
            try:
                self.assert_expert_sample_rejects_and_revokes(
                    coordinator,
                    sampler,
                    duplicate_fds,
                    clock=clock,
                )
            finally:
                os.unlink(
                    "retention.lock.matrix-link",
                    dir_fd=coordinator._state_fd,
                )

        with self.subTest(mutation="named-replacement"):
            clock = CountingMutableClock(self.clock.now_ns)
            (
                coordinator,
                _grant,
                sampler,
                _authority,
                duplicate_fds,
            ) = self.acquire_expert_grant(
                "lock-named-replacement",
                clock=clock,
            )
            state_fd = coordinator._state_fd
            os.rename(
                "retention.lock",
                "retention.lock.matrix-original",
                src_dir_fd=state_fd,
                dst_dir_fd=state_fd,
            )
            replacement_fd = os.open(
                "retention.lock",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=state_fd,
            )
            os.close(replacement_fd)
            try:
                self.assert_expert_sample_rejects_and_revokes(
                    coordinator,
                    sampler,
                    duplicate_fds,
                    clock=clock,
                )
            finally:
                os.unlink("retention.lock", dir_fd=state_fd)
                os.rename(
                    "retention.lock.matrix-original",
                    "retention.lock",
                    src_dir_fd=state_fd,
                    dst_dir_fd=state_fd,
                )

    def test_mutable_directory_binding_has_one_state_root_call_site(self):
        source_path = Path(retention_module.__file__).resolve()
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_same_mutable_directory_binding"
            )
        ]
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(len(call.args), 2)
        self.assertEqual(call.keywords, [])
        self.assertEqual(
            ast.unparse(call.args[0]),
            "original_identities[0]",
        )
        self.assertEqual(
            ast.unparse(call.args[1]),
            "authority.state_identity",
        )
        validators = [
            node
            for node in tree.body
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "RetentionCoordinator"
            )
            for child in node.body
            if (
                isinstance(child, ast.FunctionDef)
                and child.name == "_validate_expert_root_binding"
            )
        ]
        self.assertEqual(len(validators), 1)
        self.assertGreaterEqual(call.lineno, validators[0].lineno)
        self.assertLessEqual(call.end_lineno, validators[0].end_lineno)
        release = [
            child
            for node in tree.body
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "RetentionCoordinator"
            )
            for child in node.body
            if (
                isinstance(child, ast.FunctionDef)
                and child.name == "_release_reserve"
            )
        ]
        self.assertEqual(len(release), 1)
        for helper_name in (
            "_prepare_expert_sessions_identity_refresh",
            "_commit_expert_sessions_identity_refresh",
        ):
            helper_calls = [
                node
                for node in ast.walk(tree)
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == helper_name
                )
            ]
            self.assertEqual(len(helper_calls), 1)
            self.assertGreaterEqual(
                helper_calls[0].lineno,
                release[0].lineno,
            )
            self.assertLessEqual(
                helper_calls[0].end_lineno,
                release[0].end_lineno,
            )
        arm = [
            child
            for node in tree.body
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "RetentionCoordinator"
            )
            for child in node.body
            if (
                isinstance(child, ast.FunctionDef)
                and child.name == "arm_before_wal"
            )
        ]
        self.assertEqual(len(arm), 1)
        for helper_name in (
            "_prepare_expert_arm_identity_refresh",
            "_commit_expert_arm_identity_refresh",
        ):
            helper_calls = [
                node
                for node in ast.walk(tree)
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == helper_name
                )
            ]
            self.assertEqual(len(helper_calls), 1)
            self.assertGreaterEqual(
                helper_calls[0].lineno,
                arm[0].lineno,
            )
            self.assertLessEqual(
                helper_calls[0].end_lineno,
                arm[0].end_lineno,
            )

    def test_expert_root_failed_final_validation_closes_and_unpublishes_grant(
        self,
    ):
        from tennis_v1.retention import (
            _consume_expert_state_root_account_lock_request,
        )

        coordinator = self.acquire()
        request = (
            coordinator.issue_expert_state_root_account_lock_request()
        )
        duplicates: list[int] = []
        original_dup = os.dup

        def tracked_dup(fd: int) -> int:
            duplicate = original_dup(fd)
            duplicates.append(duplicate)
            return duplicate

        with (
            mock.patch.object(os, "dup", side_effect=tracked_dup),
            mock.patch.object(
                RetentionCoordinator,
                "_validate_expert_root_binding",
                side_effect=RetentionError("forced-final-validation"),
            ),
        ):
            with self.assertRaisesRegex(
                RetentionError,
                r"\Aforced-final-validation\Z",
            ):
                _consume_expert_state_root_account_lock_request(
                    request
                )
        self.assertEqual(len(duplicates), 4)
        self.assertEqual(coordinator._expert_root_grants, {})
        self.assertEqual(coordinator._expert_clock_capabilities, {})
        for fd in duplicates:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_expert_root_partial_duplicate_failure_closes_prior_duplicates(
        self,
    ):
        from tennis_v1.retention import (
            _consume_expert_state_root_account_lock_request,
        )

        coordinator = self.acquire()
        request = (
            coordinator.issue_expert_state_root_account_lock_request()
        )
        duplicate = os.dup(coordinator._state_fd)
        calls = 0

        def fail_after_first(_fd: int) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return duplicate
            raise OSError("secret-dup-failure")

        with mock.patch.object(os, "dup", side_effect=fail_after_first):
            with self.assertRaisesRegex(
                RetentionError,
                r"\Aexpert_state_root_request_stale\Z",
            ) as caught:
                _consume_expert_state_root_account_lock_request(
                    request
                )
        self.assertNotIn("secret-dup-failure", str(caught.exception))
        with self.assertRaises(OSError):
            os.fstat(duplicate)
        self.assertEqual(coordinator._expert_root_grants, {})
        self.assertEqual(coordinator._expert_clock_capabilities, {})

    def test_expert_clock_cross_thread_misuse_permanently_revokes_sampler(
        self,
    ):
        from tennis_v1.retention import (
            _consume_expert_state_root_account_lock_request,
            sample_expert_retention_wall_ns,
        )

        coordinator = self.acquire()
        request = (
            coordinator.issue_expert_state_root_account_lock_request()
        )
        grant = _consume_expert_state_root_account_lock_request(request)
        sampler = object.__getattribute__(grant, "_clock_capability")
        errors: list[BaseException] = []
        thread = threading.Thread(
            target=lambda: _capture_exception(
                errors,
                lambda: sample_expert_retention_wall_ns(sampler),
            )
        )
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIs(type(errors[0]), RetentionError)
        self.assertEqual(
            str(errors[0]),
            "expert_state_root_grant_stale",
        )
        with self.assertRaisesRegex(
            RetentionError,
            r"\Aexpert_retention_clock_capability_stale\Z",
        ):
            sample_expert_retention_wall_ns(sampler)

    def test_expert_root_request_rejects_forgery_copy_and_pickle(self):
        from tennis_v1.retention import (
            ExpertStateRootAccountLockRequestV1,
        )

        coordinator = self.acquire()
        request = (
            coordinator.issue_expert_state_root_account_lock_request()
        )
        with self.assertRaisesRegex(
            TypeError,
            r"\Aexpert state-root requests are coordinator-issued\Z",
        ):
            ExpertStateRootAccountLockRequestV1()
        for operation in (
            lambda: copy.copy(request),
            lambda: copy.deepcopy(request),
            lambda: pickle.dumps(request),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(TypeError):
                    operation()

    def test_expert_root_request_cross_thread_misuse_revokes_request(self):
        from tennis_v1.retention import (
            _consume_expert_state_root_account_lock_request,
        )

        coordinator = self.acquire()
        request = (
            coordinator.issue_expert_state_root_account_lock_request()
        )
        errors: list[BaseException] = []
        thread = threading.Thread(
            target=lambda: _capture_exception(
                errors,
                lambda: _consume_expert_state_root_account_lock_request(
                    request
                ),
            )
        )
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIs(type(errors[0]), RetentionError)
        self.assertEqual(
            str(errors[0]),
            "expert_state_root_request_stale",
        )
        with self.assertRaisesRegex(
            RetentionError,
            r"\Aexpert_state_root_request_stale\Z",
        ):
            _consume_expert_state_root_account_lock_request(request)

    @unittest.skipUnless(hasattr(os, "fork"), "requires fork")
    def test_expert_root_request_cannot_be_consumed_after_fork(self):
        from tennis_v1.retention import (
            _consume_expert_state_root_account_lock_request,
            _revoke_expert_state_root_account_lock_grant,
        )
        import warnings

        coordinator = self.acquire()
        request = (
            coordinator.issue_expert_state_root_account_lock_request()
        )
        read_fd, write_fd = os.pipe()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            child_pid = os.fork()
        if child_pid == 0:
            try:
                os.close(read_fd)
                try:
                    _consume_expert_state_root_account_lock_request(
                        request
                    )
                except RetentionError:
                    os.write(write_fd, b"R")
                else:
                    os.write(write_fd, b"A")
            finally:
                os._exit(0)
        os.close(write_fd)
        try:
            self.assertEqual(os.read(read_fd, 1), b"R")
        finally:
            os.close(read_fd)
        waited, status = os.waitpid(child_pid, 0)
        self.assertEqual(waited, child_pid)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(os.WEXITSTATUS(status), 0)

        grant = _consume_expert_state_root_account_lock_request(request)
        _revoke_expert_state_root_account_lock_grant(grant)

    def test_expert_root_request_is_stale_after_coordinator_close(self):
        from tennis_v1.retention import (
            _consume_expert_state_root_account_lock_request,
        )

        coordinator = self.acquire()
        request = (
            coordinator.issue_expert_state_root_account_lock_request()
        )
        self.close_current()
        with self.assertRaisesRegex(
            RetentionError,
            r"\Aexpert_state_root_request_stale\Z",
        ):
            _consume_expert_state_root_account_lock_request(request)

    def test_expert_clock_sampling_failure_latches_global_halt(self):
        code = r"""
from pathlib import Path
import tempfile

from tennis_v1.retention import (
    RetentionCoordinator,
    RetentionGlobalHalt,
    _consume_expert_state_root_account_lock_request,
    sample_expert_retention_wall_ns,
)
from tests.tennis_v1.test_retention import make_config

with tempfile.TemporaryDirectory() as temporary:
    broken = False

    def broken_clock():
        if broken:
            raise RuntimeError("secret-clock-error")
        return 1

    coordinator = RetentionCoordinator.acquire(
        make_config(Path(temporary).resolve() / "state"),
        clock_ns=broken_clock,
    )
    coordinator.recover_and_purge()
    request = (
        coordinator.issue_expert_state_root_account_lock_request()
    )
    grant = _consume_expert_state_root_account_lock_request(request)
    sampler = object.__getattribute__(grant, "_clock_capability")
    broken = True
    try:
        sample_expert_retention_wall_ns(sampler)
    except RetentionGlobalHalt as error:
        assert str(error) == "retention_clock_failed"
    else:
        raise AssertionError("clock failure did not halt")
    coordinator.close()
print("expert-clock-halt-ok")
"""
        completed = subprocess.run(
            [
                "/Users/mthanki/.venvs/inci-expert-py314/bin/python",
                "-B",
                "-c",
                code,
            ],
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
        self.assertEqual(
            completed.stdout.strip(),
            "expert-clock-halt-ok",
        )
        self.assertNotIn("secret-clock-error", completed.stderr)

    def test_expert_companion_creation_guard_is_read_only_and_live_only(self):
        from tennis_v1.sequencer import (
            EventRuntime,
            bind_provider_persistence_authorizer,
        )
        from tennis_v1.state import initial_state
        from tennis_v1.wal import JournalWriter
        from tests.tennis_v1.test_sequencer import concrete_environment

        with concrete_environment() as (
            fixture,
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
            runtime = EventRuntime(
                writer=writer,
                state=initial_state(manifest.session_id),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
            marker_path = (
                fixture.config.state_root
                / "retention-markers"
                / f"{manifest.session_id}.marker.json"
            )
            wal_path = (
                fixture.config.state_root
                / "sessions"
                / f"{manifest.session_id}.wal"
            )
            reserve_path = (
                fixture.config.state_root
                / "sessions"
                / f"{manifest.session_id}.reserve"
            )
            before = (
                marker_path.read_bytes(),
                wal_path.read_bytes(),
                reserve_path.stat().st_size,
                tuple(coordinator._deadlines.items()),
            )
            self.assertIsNone(
                coordinator.require_expert_companion_creation_live(
                    persistence_authorizer=authorizer
                )
            )
            after = (
                marker_path.read_bytes(),
                wal_path.read_bytes(),
                reserve_path.stat().st_size,
                tuple(coordinator._deadlines.items()),
            )
            self.assertEqual(after, before)
            runtime.close_clean("operator_stop")
            with self.assertRaisesRegex(
                RetentionError,
                r"\Aexpert_companion_creation_not_live\Z",
            ):
                coordinator.require_expert_companion_creation_live(
                    persistence_authorizer=authorizer
                )

    def test_expert_companion_creation_guard_has_exact_authorizer_signature(
        self,
    ):
        signature = inspect.signature(
            RetentionCoordinator.require_expert_companion_creation_live
        )
        self.assertEqual(
            tuple(signature.parameters),
            ("self", "persistence_authorizer"),
        )
        parameter = signature.parameters["persistence_authorizer"]
        self.assertIs(
            parameter.kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertEqual(
            parameter.annotation,
            "ProviderPersistenceAuthorizer",
        )
        self.assertEqual(signature.return_annotation, "None")

    def test_expert_companion_creation_guard_rejects_halted_and_wrong_authority(
        self,
    ):
        from tennis_v1.sequencer import (
            EventRuntime,
            bind_provider_persistence_authorizer,
        )
        from tennis_v1.state import initial_state
        from tennis_v1.wal import JournalWriter
        from tests.tennis_v1.test_sequencer import concrete_environment

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
            runtime = EventRuntime(
                writer=writer,
                state=initial_state(manifest.session_id),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
            forged = StrictAuthorizer(
                coordinator,
                manifest,
                authorizer.bound_decision,
            )
            with self.assertRaisesRegex(
                RetentionError,
                r"\Aexpert_companion_creation_not_live\Z",
            ):
                coordinator.require_expert_companion_creation_live(
                    persistence_authorizer=forged  # type: ignore[arg-type]
                )
            runtime.close_halted("operator_halt")
            with self.assertRaisesRegex(
                RetentionError,
                r"\Aexpert_companion_creation_not_live\Z",
            ):
                coordinator.require_expert_companion_creation_live(
                    persistence_authorizer=authorizer
                )

    def test_expert_companion_creation_guard_rejects_cross_thread(self):
        from tennis_v1.sequencer import (
            EventRuntime,
            bind_provider_persistence_authorizer,
        )
        from tennis_v1.state import initial_state
        from tennis_v1.wal import JournalWriter
        from tests.tennis_v1.test_sequencer import concrete_environment

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
            runtime = EventRuntime(
                writer=writer,
                state=initial_state(manifest.session_id),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
            errors: list[BaseException] = []
            thread = threading.Thread(
                target=lambda: _capture_exception(
                    errors,
                    lambda: (
                        coordinator.require_expert_companion_creation_live(
                            persistence_authorizer=authorizer
                        )
                    ),
                )
            )
            thread.start()
            thread.join()
            self.assertEqual(len(errors), 1)
            self.assertIs(type(errors[0]), RetentionError)
            self.assertEqual(
                str(errors[0]),
                "expert_companion_creation_not_live",
            )
            runtime.close_clean("operator_stop")

    def test_only_retention_defines_phase1_expert_transfer_helpers(self):
        self.assertFalse(
            hasattr(
                retention_module,
                "_sample_expert_replay_prepare_wall_ns",
            )
        )
        package = Path(retention_module.__file__).resolve().parent
        names = (
            "_consume_expert_state_root_account_lock_request",
            "_revoke_expert_state_root_account_lock_grant",
        )
        offenders: list[str] = []
        for path in sorted(package.glob("*.py")):
            if path.name == "retention.py":
                continue
            source = path.read_text(encoding="utf-8")
            if any(name in source for name in names):
                offenders.append(path.name)
        self.assertEqual(offenders, [])


def retention_report(
    *,
    deleted: tuple[str, ...] = (),
    recovered: tuple[str, ...] = (),
):
    from tennis_v1.retention import PurgeReport

    return PurgeReport(deleted_sessions=deleted, recovered_markers=recovered)


def _capture_exception(
    errors: list[BaseException], operation,
) -> None:
    try:
        operation()
    except BaseException as error:
        errors.append(error)


def marker_projection(
    manifest: SessionManifest,
    decision: QualificationDecision,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "session_id": manifest.session_id,
        "wal_basename": f"{manifest.session_id}.wal",
        "reserve_basename": f"{manifest.session_id}.reserve",
        "delete_by_ns": manifest.required_retention_until_ns,
        "session_manifest_sha256": session_manifest_sha256(manifest),
        "provider_request_binding_sha256": (
            decision.provider_request_binding_sha256
        ),
        "provider_manifest_file_sha256": (
            manifest.provider_manifest_file_sha256
        ),
        "entitlement_id_sha256": manifest.entitlement_id_sha256,
        "qualification_artifact_sha256": (
            manifest.qualification_artifact_sha256
        ),
        "created_at_ns": manifest.created_wall_ns,
    }


if __name__ == "__main__":
    unittest.main()
