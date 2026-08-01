from __future__ import annotations

import ast
from contextlib import ExitStack
from dataclasses import fields, replace
import errno
import gc
from hashlib import sha256
import importlib
import inspect
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import warnings
from unittest import mock

from tennis_v1 import adapter_contract as phase1_adapter_contract
from tennis_v1.canonical import canonical_json_bytes
from tennis_v1.codec import canonical_record_sha256
from tennis_v1.entitlements import ProviderGate
from tennis_v1.events import PersistedEvent, RecordKind, SourceKind
from tennis_v1.fingerprints import code_sha256 as phase1_code_sha256
from tennis_v1.replay_core import ReplayMismatch, ReplayResult
from tennis_v1.retention import (
    RetentionCoordinator,
    RetentionDueDeleteError,
    RetentionError,
    RetentionGlobalHalt,
)
from tennis_v1.sequencer import (
    EventRuntime,
    ProviderPersistenceAuthorizer,
    bind_provider_persistence_authorizer,
)
from tennis_v1.session import (
    canonical_session_manifest_bytes,
    session_manifest_sha256,
)
from tennis_v1.state import (
    FoundationState,
    canonical_state_bytes,
    initial_state as phase1_initial_state,
)
from tennis_v1.wal import (
    JournalReader,
    JournalValidationError,
    JournalWriter,
    ScanIssue,
    _control_event,
)

from inci_tennis_expert import contracts
from inci_tennis_expert.contracts import (
    BindingUniverse,
    EvidenceReplayContextV1,
    ExpertCollectedEnvironmentV1,
    ExpertEventKindV1,
    ExpertJournalCursorV1,
    ExpertJournalGroupV1,
    ExpertJournalRecordV1,
    ExpertJournalScanIssueV1,
    ExpertJournalScanSummaryV1,
    ExpertObservationIgnoredPayloadV1,
    ExpertObservationRejectedPayloadV1,
    ExpertPayloadDescriptorV1,
    ExpertPhysicalFileIdentityV1,
    ExpertProviderDomainBindingV1,
    ExpertReplayAccumulatorV1,
    ExpertReplayBeginReadyV1,
    ExpertReplayDeniedV1,
    ExpertReplayDiagnosticFileProofV1,
    ExpertReplayDiagnosticIssueV1,
    ExpertReplayDiagnosticProofV1,
    ExpertReplayDiagnosticRoleV1,
    ExpertReplayMismatchV1,
    ExpertReplayResultV1,
    ExpertRetentionBindingV1,
    ExpertSessionManifestV1,
    ExpertSessionTerminalV1,
    ExpertSynchronizationDraftV1,
    ExpertTerminalReasonV1,
    ExpertTraceStepV1,
    RetentionReplayAuthorizationV1,
    SyncPolicy,
    canonical_expert_bytes,
    compute_expert_capacity_proof_sha256,
    compute_expert_journal_group_sha256,
    compute_expert_journal_record_sha256,
    compute_expert_physical_file_identity_sha256,
    compute_expert_provider_domain_binding_sha256,
    compute_expert_provider_source_lineage_sha256,
    compute_expert_replay_diagnostic_proof_sha256,
    compute_expert_replay_diagnostic_file_proof_sha256,
    compute_expert_retention_binding_sha256,
    compute_expert_session_manifest_sha256,
    compute_expert_session_terminal_sha256,
    compute_expert_trace_step_sha256,
    compute_retention_replay_authorization_sha256,
    expert_contract_sha256,
    expert_phase1_replay_summary_sha256,
    expert_state_sha256,
    expert_trace_seed_sha256,
)
from inci_tennis_io import facade as replay_store_facade
import inci_tennis_io.expert_journal_store as replay_store_module
from inci_tennis_io.ports import (
    ExpertEnvironmentCollectionAuthorityV1,
    ExpertJournalRootAuthorityV1,
    ExpertLiveAuthorizationDenied,
    ExpertReplayAccessDenied,
    ExpertReplayConstructionAuthorityV1,
)
from inci_tennis_expert.match_binding import binding_universe_sha256
from inci_tennis_expert.observation import (
    bind_expert_observation_drafts,
    normalize_expert_parent,
    prove_expert_capacity,
)
from inci_tennis_expert.reducer import initial_expert_state, reduce_expert_parent
from inci_tennis_expert.synchronizer import (
    synchronization_session_from_artifacts,
)
from tests.tennis_v1 import test_events as _test_events
from tests.tennis_v1.test_events import manifest as phase1_manifest
from tests.tennis_v1.test_expert_contracts import (
    binding_metadata,
    binding_universe,
    match_binding,
    sync_policy,
)
from tests.tennis_v1.test_expert_journal_codec import (
    _independent_canonical,
    _independent_frame,
    _unchecked_replace,
)
from tests.tennis_v1.test_expert_observation import (
    raw_parent,
    synchronization_input,
    task6_artifacts,
)
from tests.tennis_v1.test_retention import (
    MutableClock,
    make_config,
    make_manifest_decision,
    session_start_frame,
)
from tests.tennis_v1.test_sequencer import captured


ROOT = Path(__file__).resolve().parents[2]
REPLAY_MODULE = "inci_tennis_expert.replay"
EXPERT_FACADE_MODULE = "inci_tennis_expert.facade"
IO_FACADE_MODULE = "inci_tennis_io.facade"
RUNTIME_MODULE = "inci_tennis_runtime.replay_service"
REPLAY_SOURCE_PATHS = (
    ROOT / "inci_tennis_expert" / "replay.py",
    ROOT / "inci_tennis_expert" / "facade.py",
    ROOT / "inci_tennis_runtime" / "replay_service.py",
)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


MISMATCH_VALUES = (
    "evidence_context_mismatch",
    "evidence_replay_not_exact",
    "evidence_terminal_not_clean",
    "evidence_session_mismatch",
    "evidence_manifest_mismatch",
    "retention_authorization_mismatch",
    "retention_deadline_reached",
    "evidence_identity_mismatch",
    "current_environment_mismatch",
    "retention_binding_mismatch",
    "companion_scan_invalid",
    "companion_manifest_mismatch",
    "expert_sequence_mismatch",
    "parent_missing",
    "parent_extra",
    "parent_order_mismatch",
    "parent_kind_mismatch",
    "parent_digest_mismatch",
    "parent_group_shape_mismatch",
    "prior_record_chain_mismatch",
    "prior_state_mismatch",
    "event_schema_unpinned",
    "record_digest_mismatch",
    "payload_descriptor_mismatch",
    "payload_bytes_mismatch",
    "normalized_observation_mismatch",
    "reduction_mismatch",
    "post_state_mismatch",
    "trace_mismatch",
    "terminal_missing",
    "terminal_reason_mismatch",
    "terminal_count_mismatch",
    "terminal_provenance_mismatch",
    "terminal_state_mismatch",
    "terminal_trace_mismatch",
)


STORE_REPLAY_SURFACES = (
    "issue_expert_environment_collection_authority",
    "collect_expert_current_environment",
    "issue_expert_replay_construction_authority",
    "prepare_expert_replay_begin",
    "read_next_replay_evidence_parent",
    "read_next_replay_companion_group",
    "read_replay_finish_material",
    "issue_begin_replay_authorization",
    "acknowledge_begin_replay",
    "issue_parent_group_replay_authorization",
    "acknowledge_parent_group_replay",
    "issue_finish_replay_authorization",
    "acknowledge_finish_replay",
    "take_expert_replay_denial",
    "abort_expert_replay_construction",
)


def _module(name: str):
    try:
        if name == REPLAY_MODULE:
            import inci_tennis_expert.replay as module
        elif name == EXPERT_FACADE_MODULE:
            import inci_tennis_expert.facade as module
        elif name == IO_FACADE_MODULE:
            import inci_tennis_io.facade as module
        elif name == RUNTIME_MODULE:
            import inci_tennis_runtime.replay_service as module
        else:
            raise AssertionError(f"unknown governed module: {name}")
        return module
    except ModuleNotFoundError as error:
        raise AssertionError(f"missing governed module: {name}") from error


def _surface(module_name: str, name: str):
    module = _module(module_name)
    try:
        return getattr(module, name)
    except AttributeError as error:
        raise AssertionError(
            f"missing governed surface: {module_name}.{name}"
        ) from error


def _independent_sha256(domain: bytes, values: dict[str, object]) -> str:
    return sha256(
        domain + _independent_canonical(tuple(values.values()))
    ).hexdigest()


def _independent_diagnostic_file_proof_sha256(
    proof: ExpertReplayDiagnosticFileProofV1,
) -> str:
    """Hash the ruled literal projection without a production calculator."""
    return sha256(
        b"INCI-EXPERT-REPLAY-DIAGNOSTIC-FILE-PROOF-V1\0"
        + _independent_canonical(
            (
                proof.schema_version,
                proof.role,
                proof.entry_present,
                proof.device,
                proof.inode,
                proof.uid,
                proof.mode,
                proof.link_count,
                proof.mtime_ns,
                proof.ctime_ns,
                proof.observed_size,
                proof.observed_prefix_length,
                proof.observed_prefix_sha256,
                proof.issue,
            )
        )
    ).hexdigest()


def _phase1_anchor(
    manifest_digest: str,
    start_digest: str,
) -> str:
    return sha256(
        b"INCI-EXPERT-PHASE1-SESSION-ANCHOR-V1\0"
        + _independent_canonical((manifest_digest, start_digest))
    ).hexdigest()


def _companion_anchor(
    expert_manifest_sha256: str,
    manifest_frame_sha256: str,
) -> str:
    return sha256(
        b"INCI-EXPERT-COMPANION-SESSION-ANCHOR-V1\0"
        + _independent_canonical(
            (expert_manifest_sha256, manifest_frame_sha256)
        )
    ).hexdigest()


def _identity(
    role: str,
    *,
    session_anchor_sha256: str,
    inode: int,
) -> ExpertPhysicalFileIdentityV1:
    marker = role in {"phase1_marker", "expert_marker"}
    values: dict[str, object] = {
        "schema_version": 1,
        "role": role,
        "device": 11,
        "inode": inode,
        "uid": 12,
        "mode": 0o600,
        "link_count": 1,
        "size": 128 + inode,
        "mtime_ns": 1_000 + inode,
        "ctime_ns": 2_000 + inode,
        "canonical_marker_sha256": SHA_A if marker else None,
        "file_header_sha256": None if marker else SHA_B,
        "session_anchor_sha256": session_anchor_sha256,
    }
    values["identity_sha256"] = (
        compute_expert_physical_file_identity_sha256(**values)
    )
    return contracts._create_expert_physical_file_identity_v1(**values)


def _evidence_context(
    *,
    raw_count: int,
    exact: bool = True,
    terminal_clean: bool = True,
    terminal_present: bool = True,
    terminal_reason: str | None = None,
) -> EvidenceReplayContextV1:
    session = phase1_manifest(
        session_id="11111111-1111-4111-8111-111111111111",
        provider_id="provider-a",
        product_tier="trial-tier",
        source_lineage_id="lineage-a",
        provider_manifest_file_sha256="2" * 64,
        provider_manifest_canonical_sha256="c" * 64,
        entitlement_id_sha256="3" * 64,
        permission_artifact_sha256="e" * 64,
        qualification_artifact_sha256="f" * 64,
        qualification_trace_sha256="1" * 64,
        code_sha256="2" * 64,
        adapter_code_sha256="3" * 64,
        required_retention_until_ns=1_000,
        access_expires_at_ns=800,
        analysis_expires_at_ns=1_100,
    )
    start = _control_event(
        session,
        ingest_seq=1,
        event_type="SESSION_START",
        payload=canonical_session_manifest_bytes(session),
    )
    state = FoundationState(
        session_id=session.session_id,
        last_applied_raw_seq=0 if raw_count == 0 else 2 * raw_count,
        raw_count=raw_count,
        derived_count=raw_count,
        source_epochs=(
            ()
            if raw_count == 0
            else ((SourceKind.TIMER, "clock", 0),)
        ),
    )
    issue = (
        None
        if terminal_clean
        else ScanIssue.HALTED_TERMINAL
    )
    result = ReplayResult(
        state=state,
        trace_sha256=SHA_A,
        raw_count=raw_count,
        derived_count=raw_count,
        terminal_clean=terminal_clean,
        wal_valid=True,
        exact_replay=exact and terminal_clean,
        scan_issue=issue,
        replay_mismatch=None if exact else ReplayMismatch.STATE,
    )
    terminal = None
    if terminal_present:
        reason = (
            "operator_stop"
            if terminal_clean
            else "operator_halt"
        ) if terminal_reason is None else terminal_reason
        payload = canonical_json_bytes(
            {
                "terminal_version": 1,
                "clean": terminal_clean,
                "reason": reason,
                "trace_sha256": result.trace_sha256,
                "final_state_sha256": sha256(
                    canonical_state_bytes(state)
                ).hexdigest(),
                "record_count_before_terminal": 1 + 2 * raw_count,
                "raw_count": raw_count,
                "derived_count": raw_count,
                "last_applied_raw_seq": state.last_applied_raw_seq,
                "config_file_sha256": session.config_file_sha256,
                "config_canonical_sha256": session.config_canonical_sha256,
                "code_sha256": session.code_sha256,
                "session_manifest_sha256": session_manifest_sha256(session),
                "provider_manifest_file_sha256": (
                    session.provider_manifest_file_sha256
                ),
                "provider_manifest_canonical_sha256": (
                    session.provider_manifest_canonical_sha256
                ),
                "entitlement_id_sha256": session.entitlement_id_sha256,
                "permission_artifact_sha256": (
                    session.permission_artifact_sha256
                ),
                "qualification_artifact_sha256": (
                    session.qualification_artifact_sha256
                ),
                "qualification_trace_sha256": (
                    session.qualification_trace_sha256
                ),
                "adapter_code_sha256": session.adapter_code_sha256,
                "auth_contract_sha256": session.auth_contract_sha256,
                "quota_contract_sha256": session.quota_contract_sha256,
                "required_retention_until_ns": (
                    session.required_retention_until_ns
                ),
                "research_evaluable": False,
            }
        )
        terminal = _control_event(
            session,
            ingest_seq=2 + 2 * raw_count,
            event_type="SESSION_HALT",
            payload=payload,
        )
    manifest_digest = session_manifest_sha256(session)
    start_digest = canonical_record_sha256(start)
    anchor = _phase1_anchor(manifest_digest, start_digest)
    values = {
        "schema_version": 1,
        "session_manifest": session,
        "session_manifest_sha256": manifest_digest,
        "session_start": start,
        "session_start_record_sha256": start_digest,
        "replay_result": result,
        "evidence_terminal": terminal,
        "evidence_terminal_record_sha256": (
            None if terminal is None else canonical_record_sha256(terminal)
        ),
        "evidence_marker_identity": _identity(
            "phase1_marker",
            session_anchor_sha256=anchor,
            inode=1,
        ),
        "evidence_wal_identity": _identity(
            "phase1_wal",
            session_anchor_sha256=anchor,
            inode=2,
        ),
    }
    return contracts._create_evidence_replay_context_v1(**values)


def _valid_artifacts(
    *,
    raw_count: int = 1,
    terminal_reason: str | None = None,
) -> tuple[object, object, ExpertSessionManifestV1, EvidenceReplayContextV1]:
    universe, policy, template = task6_artifacts()
    evidence = _evidence_context(
        raw_count=raw_count,
        terminal_reason=terminal_reason,
    )
    session = evidence.session_manifest
    provider_values: dict[str, object] = {
        "schema_version": 1,
        "phase1_session_manifest_sha256": evidence.session_manifest_sha256,
        "match_binding_universe_sha256": binding_universe_sha256(universe),
        "provider_id": session.provider_id,
        "product_tier": session.product_tier,
        "source_lineage_id": session.source_lineage_id,
        "provider_manifest_canonical_sha256": (
            session.provider_manifest_canonical_sha256
        ),
        "provider_source_lineage_sha256": (
            template.provider_domain.provider_source_lineage_sha256
        ),
        "revision_domain_id": universe.bindings[0].revision_domain_id,
    }
    provider_values["provider_domain_binding_sha256"] = (
        compute_expert_provider_domain_binding_sha256(**provider_values)
    )
    provider_domain = ExpertProviderDomainBindingV1(**provider_values)
    retention_values: dict[str, object] = {
        "schema_version": 1,
        "session_id": session.session_id,
        "evidence_session_manifest_sha256": evidence.session_manifest_sha256,
        "provider_request_binding_sha256": (
            template.provider_request_binding_sha256
        ),
        "permission_artifact_sha256": session.permission_artifact_sha256,
        "qualification_artifact_sha256": (
            session.qualification_artifact_sha256
        ),
        "qualification_trace_sha256": session.qualification_trace_sha256,
        "retention_delete_by_ns": session.required_retention_until_ns,
        "access_expires_at_ns": session.access_expires_at_ns,
        "analysis_expires_at_ns": session.analysis_expires_at_ns,
    }
    retention_values["retention_binding_sha256"] = (
        compute_expert_retention_binding_sha256(**retention_values)
    )
    retention = ExpertRetentionBindingV1(**retention_values)
    manifest_values = {
        item.name: getattr(template, item.name)
        for item in fields(template)
        if item.name != "manifest_sha256"
    }
    manifest_values.update(
        {
            "session_id": session.session_id,
            "evidence_session_manifest_sha256": (
                evidence.session_manifest_sha256
            ),
            "evidence_session_start_record_sha256": (
                evidence.session_start_record_sha256
            ),
            "provider_id": session.provider_id,
            "product_tier": session.product_tier,
            "source_lineage_id": session.source_lineage_id,
            "provider_manifest_file_sha256": (
                session.provider_manifest_file_sha256
            ),
            "provider_manifest_canonical_sha256": (
                session.provider_manifest_canonical_sha256
            ),
            "entitlement_id_sha256": session.entitlement_id_sha256,
            "permission_artifact_sha256": session.permission_artifact_sha256,
            "qualification_artifact_sha256": (
                session.qualification_artifact_sha256
            ),
            "qualification_trace_sha256": (
                session.qualification_trace_sha256
            ),
            "provider_domain": provider_domain,
            "retention": retention,
        }
    )
    manifest_values["manifest_sha256"] = (
        compute_expert_session_manifest_sha256(**manifest_values)
    )
    manifest = ExpertSessionManifestV1(**manifest_values)
    return universe, policy, manifest, evidence


def _real_expert_manifest(
    *,
    phase1: object,
    session_start: PersistedEvent,
    authorizer: ProviderPersistenceAuthorizer,
    collected: ExpertCollectedEnvironmentV1,
) -> tuple[BindingUniverse, SyncPolicy, ExpertSessionManifestV1]:
    phase1_digest = session_manifest_sha256(phase1)
    provider_request_binding_sha256 = (
        authorizer.bound_decision.provider_request_binding_sha256
    )
    if type(provider_request_binding_sha256) is not str:
        raise AssertionError("fixture requires provider-request binding")
    lineage_sha256 = compute_expert_provider_source_lineage_sha256(
        phase1.provider_id,
        phase1.product_tier,
        phase1.source_lineage_id,
        phase1.provider_manifest_canonical_sha256,
    )
    binding = match_binding(
        provider_source_id=phase1.provider_id,
        source_lineage_sha256=lineage_sha256,
    )
    universe = binding_universe(
        bindings=(binding,),
        metadata=(binding_metadata(),),
    )
    policy = sync_policy(universe_sha256=universe.universe_sha256)
    empty_synchronization = synchronization_session_from_artifacts(
        universe,
        policy,
    )
    provider_values: dict[str, object] = {
        "schema_version": 1,
        "phase1_session_manifest_sha256": phase1_digest,
        "match_binding_universe_sha256": universe.universe_sha256,
        "provider_id": phase1.provider_id,
        "product_tier": phase1.product_tier,
        "source_lineage_id": phase1.source_lineage_id,
        "provider_manifest_canonical_sha256": (
            phase1.provider_manifest_canonical_sha256
        ),
        "provider_source_lineage_sha256": lineage_sha256,
        "revision_domain_id": binding.revision_domain_id,
    }
    provider_values["provider_domain_binding_sha256"] = (
        compute_expert_provider_domain_binding_sha256(**provider_values)
    )
    provider_domain = ExpertProviderDomainBindingV1(**provider_values)
    retention_values: dict[str, object] = {
        "schema_version": 1,
        "session_id": phase1.session_id,
        "evidence_session_manifest_sha256": phase1_digest,
        "provider_request_binding_sha256": (
            provider_request_binding_sha256
        ),
        "permission_artifact_sha256": phase1.permission_artifact_sha256,
        "qualification_artifact_sha256": (
            phase1.qualification_artifact_sha256
        ),
        "qualification_trace_sha256": (
            phase1.qualification_trace_sha256
        ),
        "retention_delete_by_ns": phase1.required_retention_until_ns,
        "access_expires_at_ns": phase1.access_expires_at_ns,
        "analysis_expires_at_ns": phase1.analysis_expires_at_ns,
    }
    retention_values["retention_binding_sha256"] = (
        compute_expert_retention_binding_sha256(**retention_values)
    )
    retention = ExpertRetentionBindingV1(**retention_values)
    capacity = prove_expert_capacity(universe, policy)
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": phase1.session_id,
        "evidence_session_manifest_sha256": phase1_digest,
        "evidence_session_start_record_sha256": canonical_record_sha256(
            session_start
        ),
        "provider_id": phase1.provider_id,
        "product_tier": phase1.product_tier,
        "source_lineage_id": phase1.source_lineage_id,
        "provider_manifest_file_sha256": (
            phase1.provider_manifest_file_sha256
        ),
        "provider_manifest_canonical_sha256": (
            phase1.provider_manifest_canonical_sha256
        ),
        "entitlement_id_sha256": phase1.entitlement_id_sha256,
        "provider_request_binding_sha256": (
            provider_request_binding_sha256
        ),
        "permission_artifact_sha256": phase1.permission_artifact_sha256,
        "qualification_artifact_sha256": (
            phase1.qualification_artifact_sha256
        ),
        "qualification_trace_sha256": (
            phase1.qualification_trace_sha256
        ),
        "provider_domain": provider_domain,
        "environment": collected.current,
        "retention": retention,
        "match_binding_universe_sha256": universe.universe_sha256,
        "binding_raw_artifact_id": universe.raw_artifact_id,
        "binding_raw_artifact_sha256": universe.raw_artifact_sha256,
        "binding_review_artifact_id": universe.review.review_artifact_id,
        "binding_review_artifact_sha256": (
            universe.review.review_artifact_sha256
        ),
        "sync_policy_sha256": expert_contract_sha256(policy),
        "initial_synchronization_sha256": expert_contract_sha256(
            empty_synchronization
        ),
        "normalizers": collected.normalizers,
        "structural_schemas": collected.structural_schemas,
        "event_schemas": collected.event_schemas,
        "capacity": capacity,
        "artifact_pins": (),
    }
    values["manifest_sha256"] = compute_expert_session_manifest_sha256(
        **values
    )
    return (
        universe,
        policy,
        ExpertSessionManifestV1(**values),
    )


class _RealReplayStores:
    """Temporary real Phase-1 and companion stores; no scripted facade."""

    _session_sequence = 0

    def __init__(self) -> None:
        self.phase1_fixture = _test_events.SessionContractTests(
            "test_session_manifest_requires_verified_eligible_matching_inputs"
        )
        self.adapter_patch: mock._patch | None = None
        self.coordinator: RetentionCoordinator | None = None
        self.writer: object | None = None
        self.replays: list[ExpertReplayConstructionAuthorityV1] = []
        self.phase1_closed = False

    def __enter__(self) -> "_RealReplayStores":
        self.phase1_fixture.setUp()
        self.adapter_patch = mock.patch.multiple(
            phase1_adapter_contract,
            __file__=self.phase1_fixture.builder.adapter_file,
            _ADAPTER_REGISTRY={
                (
                    "synthetic-provider",
                    "trial-v1",
                ): self.phase1_fixture.builder.registration
            },
        )
        self.adapter_patch.start()
        type(self)._session_sequence += 1
        session_id = (
            "12345678-1234-4234-8234-"
            f"{type(self)._session_sequence:012x}"
        )
        self.phase1_manifest = self.phase1_fixture.build(
            code_sha256=phase1_code_sha256(ROOT / "tennis_v1"),
            session_id=session_id,
        )
        self.clock = MutableClock(self.phase1_manifest.created_wall_ns)
        self.coordinator = RetentionCoordinator.acquire(
            self.phase1_fixture.config,
            clock_ns=self.clock,
        )
        self.coordinator.recover_and_purge()
        self.gate = ProviderGate(
            self.phase1_fixture.config,
            self.phase1_fixture.provider_manifest,
            self.phase1_fixture.request,
            environ={"SYNTHETIC_API_KEY": "fixture-secret"},
            clock=lambda: self.phase1_fixture.now,
        )
        self.authorizer = bind_provider_persistence_authorizer(
            gate=self.gate,
            coordinator=self.coordinator,
            session_manifest=self.phase1_manifest,
        )
        write_capability = self.coordinator.arm_before_wal(
            session_manifest=self.phase1_manifest,
            decision=self.authorizer.bound_decision,
            persistence_authorizer=self.authorizer,
        )
        self.phase1_writer = JournalWriter.create(
            write_capability=write_capability,
            session_manifest=self.phase1_manifest,
        )
        self.runtime = EventRuntime(
            writer=self.phase1_writer,
            state=phase1_initial_state(self.phase1_manifest.session_id),
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        self.authority = replay_store_facade.acquire_expert_journal_root(
            self.coordinator.issue_expert_state_root_account_lock_request()
        )
        self.environment_available = False
        try:
            environment_authority = (
                replay_store_facade
                .issue_expert_environment_collection_authority(
                    self.authority,
                    persistence_authorizer=self.authorizer,
                    coordinator=self.coordinator,
                )
            )
            self.collected = (
                replay_store_facade.collect_expert_current_environment(
                    environment_authority
                )
            )
            self.environment_available = True
        except ValueError:
            if any(path.exists() for path in REPLAY_SOURCE_PATHS):
                raise
            # RED-only bootstrap: the ruled source inventory deliberately
            # includes the three absent replay modules. Once present, every
            # integrated path uses the descriptor-collected environment.
            _, _, template = task6_artifacts()
            self.collected = (
                contracts._create_expert_collected_environment_v1(
                    current=template.environment,
                    normalizers=template.normalizers,
                    structural_schemas=template.structural_schemas,
                    event_schemas=template.event_schemas,
                )
            )
        (
            self.universe,
            self.policy,
            self.manifest,
        ) = _real_expert_manifest(
            phase1=self.phase1_manifest,
            session_start=self.phase1_writer.session_start,
            authorizer=self.authorizer,
            collected=self.collected,
        )
        self.state = initial_expert_state(
            self.manifest,
            self.universe,
            self.policy,
        )
        self.cursor = _genesis_cursor(self.manifest, self.state)
        self.writer = replay_store_facade.create_expert_journal(
            self.authority,
            self.manifest,
            self.cursor,
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        self.parents: list[PersistedEvent] = []
        self.groups: list[
            tuple[ExpertJournalGroupV1, tuple[bytes, ...]]
        ] = []
        self.phase1_terminal: PersistedEvent | None = None
        self.expert_terminal: ExpertSessionTerminalV1 | None = None
        return self

    def append_parent(self, *, with_companion: bool = True) -> PersistedEvent:
        parent = self.runtime.ingest(
            captured(
                self.authorizer,
                provider_sequence=f"A-{len(self.parents) + 1}",
            )
        )
        self.parents.append(parent)
        if with_companion:
            self.append_companion_for(parent)
        return parent

    def append_companion_for(
        self,
        parent: PersistedEvent,
    ) -> tuple[ExpertJournalGroupV1, tuple[bytes, ...]]:
        group, payloads, candidate_cursor, reduction = _independent_group(
            self.manifest,
            self.cursor,
            parent,
            prior_state_override=self.state,
        )
        permit = replay_store_facade.issue_expert_append_permit(
            self.writer,
            self.cursor.expert_state_sha256,
            self.cursor,
            group,
            payloads,
        )
        receipt = replay_store_facade.append_expert_group(permit)
        replay_store_facade.acknowledge_expert_publication(
            self.writer,
            receipt=receipt,
            candidate_state_sha256=candidate_cursor.expert_state_sha256,
            candidate_cursor=candidate_cursor,
        )
        self.groups.append((group, payloads))
        self.cursor = candidate_cursor
        self.state = reduction.final_state
        return group, payloads

    def close_phase1(self, reason: str = "operator_stop") -> PersistedEvent:
        if self.phase1_terminal is None:
            self.phase1_terminal = (
                self.runtime.close_ingress_session_end()
                if reason == "session_end"
                else self.runtime.close_clean(reason)
            )
            self.phase1_closed = True
        return self.phase1_terminal

    def halt_phase1(self, reason: str = "operator_halt") -> PersistedEvent:
        if self.phase1_terminal is None:
            self.phase1_terminal = self.runtime.close_halted(reason)
            self.phase1_closed = True
        return self.phase1_terminal

    def close_companion(self) -> ExpertSessionTerminalV1:
        self.close_phase1()
        unseen = replay_store_facade.prove_expert_live_evidence_tail(
            self.writer,
            published_cursor=self.cursor,
        )
        if unseen is not None:
            raise AssertionError("fixture has an uncovered Phase-1 parent")
        evidence_terminal, terminal = (
            replay_store_facade.build_aligned_expert_terminal(
                self.writer,
                final_state=self.state,
                final_cursor=self.cursor,
            )
        )
        if evidence_terminal != self.phase1_terminal:
            raise AssertionError("store terminal does not match Phase-1")
        permit = replay_store_facade.issue_expert_terminal_permit(
            self.writer,
            terminal,
        )
        replay_store_facade.append_expert_terminal(permit)
        self.expert_terminal = terminal
        return terminal

    def issue_replay(
        self,
    ) -> ExpertReplayConstructionAuthorityV1 | ExpertReplayDeniedV1:
        replay = (
            replay_store_facade.issue_expert_replay_construction_authority(
                self.authority,
                persistence_authorizer=self.authorizer,
                coordinator=self.coordinator,
            )
        )
        if type(replay) is ExpertReplayConstructionAuthorityV1:
            self.replays.append(replay)
        return replay

    def paths(self) -> dict[str, Path]:
        state_root = self.phase1_fixture.root / "state"
        session_id = self.phase1_manifest.session_id
        return {
            "phase1_marker": (
                state_root / "retention-markers" / f"{session_id}.marker.json"
            ),
            "phase1_wal": state_root / "sessions" / f"{session_id}.wal",
            "expert_marker": (
                state_root
                / "expert-v1"
                / "markers"
                / f"{session_id}.expert-retention-v1.json"
            ),
            "expert_journal": (
                state_root
                / "expert-v1"
                / "sessions"
                / f"{session_id}.expert-journal-v1"
            ),
            "expert_reserve": (
                state_root
                / "expert-v1"
                / "sessions"
                / f"{session_id}.expert-reserve-v1"
            ),
        }

    def __exit__(self, *_: object) -> None:
        for replay in reversed(self.replays):
            try:
                state = replay_store_module._REPLAYS.get(replay)
                if state is not None and not state.get("closed"):
                    replay_store_facade.abort_expert_replay_construction(
                        replay
                    )
            except BaseException:
                pass
        if not self.phase1_closed:
            try:
                self.runtime.close_clean("operator_stop")
            except BaseException:
                pass
        if self.writer is not None:
            try:
                state = replay_store_module._WRITERS.get(self.writer)
                if state is not None and state.state not in {
                    "closed",
                    "poisoned",
                }:
                    replay_store_facade.abort_expert_writer(self.writer)
            except BaseException:
                pass
        if self.coordinator is not None:
            self.coordinator.close()
        if self.adapter_patch is not None:
            self.adapter_patch.stop()
        self.phase1_fixture.tearDown()


def _authorization(
    manifest: ExpertSessionManifestV1,
    evidence: EvidenceReplayContextV1,
    *,
    operation: str,
    sequence: int,
    expected_parent_ingest_seq: int | None = None,
    sampled_wall_ns: int = 999,
) -> RetentionReplayAuthorizationV1:
    companion_anchor = _companion_anchor(manifest.manifest_sha256, SHA_C)
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": manifest.session_id,
        "authorization_sequence": sequence,
        "authorized_operation": operation,
        "expected_parent_ingest_seq": expected_parent_ingest_seq,
        "evidence_session_manifest_sha256": (
            evidence.session_manifest_sha256
        ),
        "evidence_session_start_record_sha256": (
            evidence.session_start_record_sha256
        ),
        "evidence_terminal_record_sha256": (
            evidence.evidence_terminal_record_sha256
        ),
        "expert_manifest_sha256": manifest.manifest_sha256,
        "retention_binding_sha256": (
            manifest.retention.retention_binding_sha256
        ),
        "provider_request_binding_sha256": (
            manifest.provider_request_binding_sha256
        ),
        "permission_artifact_sha256": manifest.permission_artifact_sha256,
        "qualification_artifact_sha256": (
            manifest.qualification_artifact_sha256
        ),
        "qualification_trace_sha256": (
            manifest.qualification_trace_sha256
        ),
        "evidence_marker_identity": evidence.evidence_marker_identity,
        "evidence_wal_identity": evidence.evidence_wal_identity,
        "companion_marker_identity": _identity(
            "expert_marker",
            session_anchor_sha256=companion_anchor,
            inode=3,
        ),
        "companion_journal_identity": _identity(
            "expert_journal",
            session_anchor_sha256=companion_anchor,
            inode=4,
        ),
        "common_deadline_ns": manifest.retention.retention_delete_by_ns,
        "final_sampled_wall_ns": sampled_wall_ns,
    }
    values["authorization_sha256"] = (
        compute_retention_replay_authorization_sha256(**values)
    )
    return contracts._create_retention_replay_authorization_v1(**values)


def _genesis_cursor(
    manifest: ExpertSessionManifestV1,
    state: object,
) -> ExpertJournalCursorV1:
    state_digest = expert_state_sha256(state)
    return ExpertJournalCursorV1(
        schema_version=1,
        session_id=manifest.session_id,
        group_count=0,
        record_count=0,
        last_parent_ingest_seq=0,
        last_parent_record_sha256=(
            manifest.evidence_session_start_record_sha256
        ),
        expert_seq=0,
        expert_record_sha256=manifest.manifest_sha256,
        expert_state_sha256=state_digest,
        expert_trace_sha256=expert_trace_seed_sha256(
            manifest.session_id,
            manifest.manifest_sha256,
            state_digest,
        ),
    )


def _independent_group(
    manifest: ExpertSessionManifestV1,
    prior: ExpertJournalCursorV1,
    parent: PersistedEvent,
    *,
    prior_state_override: object | None = None,
    observations_override: tuple[object, ...] | None = None,
    payloads_override: tuple[bytes, ...] | None = None,
    kinds_override: tuple[ExpertEventKindV1, ...] | None = None,
) -> tuple[
    ExpertJournalGroupV1,
    tuple[bytes, ...],
    ExpertJournalCursorV1,
    object,
]:
    observations = (
        normalize_expert_parent(manifest, parent)
        if observations_override is None
        else observations_override
    )
    prior_state = (
        initial_expert_state(
            manifest,
            *_valid_artifacts(raw_count=1)[:2],
        )
        if prior_state_override is None
        else prior_state_override
    )
    reduction = reduce_expert_parent(prior_state, observations)
    payloads = (
        tuple(canonical_expert_bytes(item.payload) for item in reduction.outcomes)
        if payloads_override is None
        else payloads_override
    )
    kinds = (
        tuple(item.event_kind for item in reduction.outcomes)
        if kinds_override is None
        else kinds_override
    )
    parent_evidence = observations[0].parent
    records: list[ExpertJournalRecordV1] = []
    traces: list[ExpertTraceStepV1] = []
    record_head = prior.expert_record_sha256
    trace_head = prior.expert_trace_sha256
    for index, (outcome, payload, kind) in enumerate(
        zip(reduction.outcomes, payloads, kinds, strict=True)
    ):
        contract_name = {
            ExpertEventKindV1.SYNCHRONIZATION_APPLIED: (
                "ExpertSynchronizationAppliedPayloadV1"
            ),
            ExpertEventKindV1.OBSERVATION_IGNORED: (
                "ExpertObservationIgnoredPayloadV1"
            ),
            ExpertEventKindV1.OBSERVATION_REJECTED: (
                "ExpertObservationRejectedPayloadV1"
            ),
        }[kind]
        descriptor = ExpertPayloadDescriptorV1(
            schema_version=1,
            content_type="application/vnd.inci.expert+json",
            payload_encoding="canonical-json-v1",
            payload_contract_name=contract_name,
            payload_length=len(payload),
            payload_sha256=sha256(payload).hexdigest(),
        )
        record_values: dict[str, object] = {
            "schema_version": 1,
            "session_id": manifest.session_id,
            "expert_manifest_sha256": manifest.manifest_sha256,
            "provider_request_binding_sha256": (
                manifest.provider_request_binding_sha256
            ),
            "match_binding_universe_sha256": (
                manifest.match_binding_universe_sha256
            ),
            "retention_binding_sha256": (
                manifest.retention.retention_binding_sha256
            ),
            "expert_seq": prior.expert_seq + index + 1,
            "parent": parent_evidence,
            "parent_output_index": index,
            "parent_output_count": len(reduction.outcomes),
            "event_kind": kind,
            "event_version": 1,
            "event_schema_sha256": (
                contracts.expert_event_schema_resource_sha256(kind)
            ),
            "prior_expert_record_sha256": record_head,
            "prior_expert_state_sha256": (
                outcome.prior_expert_state_sha256
            ),
            "payload": descriptor,
            "post_expert_state_sha256": (
                outcome.post_expert_state_sha256
            ),
        }
        record = ExpertJournalRecordV1(
            **record_values,
            record_sha256=_independent_sha256(
                b"INCI-EXPERT-JOURNAL-RECORD-V1\0",
                record_values,
            ),
        )
        trace_values: dict[str, object] = {
            "schema_version": 1,
            "expert_seq": record.expert_seq,
            "prior_trace_sha256": trace_head,
            "expert_record_sha256": record.record_sha256,
            "post_expert_state_sha256": record.post_expert_state_sha256,
        }
        trace = ExpertTraceStepV1(
            **trace_values,
            post_trace_sha256=_independent_sha256(
                b"INCI-EXPERT-TRACE-STEP-V1\0",
                trace_values,
            ),
        )
        records.append(record)
        traces.append(trace)
        record_head = record.record_sha256
        trace_head = trace.post_trace_sha256
    group_values: dict[str, object] = {
        "schema_version": 1,
        "session_id": manifest.session_id,
        "expert_manifest_sha256": manifest.manifest_sha256,
        "group_sequence": prior.group_count + 1,
        "parent": parent_evidence,
        "parent_output_count": len(records),
        "first_expert_seq": records[0].expert_seq,
        "prior_expert_record_sha256": prior.expert_record_sha256,
        "prior_expert_state_sha256": prior.expert_state_sha256,
        "records": tuple(records),
        "trace_steps": tuple(traces),
        "final_expert_record_sha256": records[-1].record_sha256,
        "post_expert_state_sha256": (
            reduction.final_expert_state_sha256
        ),
        "post_trace_sha256": traces[-1].post_trace_sha256,
    }
    group = ExpertJournalGroupV1(
        **group_values,
        group_sha256=_independent_sha256(
            b"INCI-EXPERT-JOURNAL-GROUP-V1\0",
            group_values,
        ),
    )
    cursor = ExpertJournalCursorV1(
        schema_version=1,
        session_id=manifest.session_id,
        group_count=prior.group_count + 1,
        record_count=prior.record_count + len(records),
        last_parent_ingest_seq=parent.ingest_seq,
        last_parent_record_sha256=canonical_record_sha256(parent),
        expert_seq=records[-1].expert_seq,
        expert_record_sha256=records[-1].record_sha256,
        expert_state_sha256=reduction.final_expert_state_sha256,
        expert_trace_sha256=traces[-1].post_trace_sha256,
    )
    return group, payloads, cursor, reduction


def _rehash_group(
    group: ExpertJournalGroupV1,
    *,
    records: tuple[ExpertJournalRecordV1, ...] | None = None,
    initial_trace_sha256: str | None = None,
) -> ExpertJournalGroupV1:
    """Rebuild every dependent record, trace, and group digest independently."""
    source_records = group.records if records is None else records
    record_head = group.prior_expert_record_sha256
    trace_head = (
        group.trace_steps[0].prior_trace_sha256
        if initial_trace_sha256 is None
        else initial_trace_sha256
    )
    rebuilt_records: list[ExpertJournalRecordV1] = []
    rebuilt_traces: list[ExpertTraceStepV1] = []
    for source in source_records:
        record_values = {
            item.name: (
                record_head
                if item.name == "prior_expert_record_sha256"
                else getattr(source, item.name)
            )
            for item in fields(source)
            if item.name != "record_sha256"
        }
        record = _unchecked_replace(
            source,
            **record_values,
            record_sha256=_independent_sha256(
                b"INCI-EXPERT-JOURNAL-RECORD-V1\0",
                record_values,
            ),
        )
        trace_values: dict[str, object] = {
            "schema_version": 1,
            "expert_seq": record.expert_seq,
            "prior_trace_sha256": trace_head,
            "expert_record_sha256": record.record_sha256,
            "post_expert_state_sha256": record.post_expert_state_sha256,
        }
        trace = ExpertTraceStepV1(
            **trace_values,
            post_trace_sha256=_independent_sha256(
                b"INCI-EXPERT-TRACE-STEP-V1\0",
                trace_values,
            ),
        )
        rebuilt_records.append(record)
        rebuilt_traces.append(trace)
        record_head = record.record_sha256
        trace_head = trace.post_trace_sha256
    group_values = {
        item.name: getattr(group, item.name)
        for item in fields(group)
        if item.name != "group_sha256"
    }
    group_values.update(
        {
            "records": tuple(rebuilt_records),
            "trace_steps": tuple(rebuilt_traces),
            "final_expert_record_sha256": record_head,
            "post_expert_state_sha256": (
                rebuilt_records[-1].post_expert_state_sha256
            ),
            "post_trace_sha256": trace_head,
        }
    )
    return _unchecked_replace(
        group,
        **group_values,
        group_sha256=_independent_sha256(
            b"INCI-EXPERT-JOURNAL-GROUP-V1\0",
            group_values,
        ),
    )


def _rehash_terminal(
    terminal: ExpertSessionTerminalV1,
    **changes: object,
) -> ExpertSessionTerminalV1:
    values = {
        item.name: changes.get(item.name, getattr(terminal, item.name))
        for item in fields(terminal)
        if item.name != "terminal_sha256"
    }
    return _unchecked_replace(
        terminal,
        **values,
        terminal_sha256=_independent_sha256(
            b"INCI-EXPERT-SESSION-TERMINAL-V1\0",
            values,
        ),
    )


def _rehash_authorization(
    authorization: RetentionReplayAuthorizationV1,
    **changes: object,
) -> RetentionReplayAuthorizationV1:
    values = {
        item.name: changes.get(
            item.name,
            getattr(authorization, item.name),
        )
        for item in fields(authorization)
        if item.name != "authorization_sha256"
    }
    return _unchecked_replace(
        authorization,
        **values,
        authorization_sha256=(
            compute_retention_replay_authorization_sha256(**values)
        ),
    )


def _rehash_manifest(
    manifest: ExpertSessionManifestV1,
    **changes: object,
) -> ExpertSessionManifestV1:
    values = {
        item.name: changes.get(item.name, getattr(manifest, item.name))
        for item in fields(manifest)
        if item.name != "manifest_sha256"
    }
    return _unchecked_replace(
        manifest,
        **values,
        manifest_sha256=compute_expert_session_manifest_sha256(**values),
    )


def _terminal(
    accumulator: ExpertReplayAccumulatorV1,
    *,
    reason: ExpertTerminalReasonV1 | None = None,
) -> ExpertSessionTerminalV1:
    evidence_terminal = accumulator.evidence.evidence_terminal
    assert evidence_terminal is not None
    selected_reason = (
        ExpertTerminalReasonV1.SESSION_END
        if b'"reason":"session_end"' in evidence_terminal.payload
        else ExpertTerminalReasonV1.OPERATOR_STOP
    ) if reason is None else reason
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": accumulator.manifest.session_id,
        "expert_manifest_sha256": accumulator.manifest.manifest_sha256,
        "provider_request_binding_sha256": (
            accumulator.manifest.provider_request_binding_sha256
        ),
        "match_binding_universe_sha256": (
            accumulator.manifest.match_binding_universe_sha256
        ),
        "retention_binding_sha256": (
            accumulator.manifest.retention.retention_binding_sha256
        ),
        "evidence_terminal_ingest_seq": evidence_terminal.ingest_seq,
        "evidence_terminal_record_sha256": (
            canonical_record_sha256(evidence_terminal)
        ),
        "evidence_terminal_clean": True,
        "evidence_terminal_reason": selected_reason.value,
        "evidence_raw_count": accumulator.evidence_raw_count,
        "evidence_derived_count": accumulator.evidence_derived_count,
        "expert_group_count": accumulator.cursor.group_count,
        "expert_record_count": accumulator.cursor.record_count,
        "last_parent_ingest_seq": accumulator.cursor.last_parent_ingest_seq,
        "last_parent_record_sha256": (
            accumulator.cursor.last_parent_record_sha256
        ),
        "final_expert_seq": accumulator.cursor.expert_seq,
        "final_expert_record_sha256": (
            accumulator.cursor.expert_record_sha256
        ),
        "final_expert_state_sha256": (
            accumulator.cursor.expert_state_sha256
        ),
        "final_expert_trace_sha256": (
            accumulator.cursor.expert_trace_sha256
        ),
        "clean": True,
        "reason": selected_reason,
        "research_evaluable": False,
    }
    return ExpertSessionTerminalV1(
        **values,
        terminal_sha256=_independent_sha256(
            b"INCI-EXPERT-SESSION-TERMINAL-V1\0",
            values,
        ),
    )


def _clean_scan(cursor: ExpertJournalCursorV1) -> ExpertJournalScanSummaryV1:
    return ExpertJournalScanSummaryV1(
        schema_version=1,
        file_size=1_000,
        last_good_offset=1_000,
        last_frame_sequence=cursor.group_count + 1,
        group_count=cursor.group_count,
        record_count=cursor.record_count,
        terminal_clean=True,
        issue=None,
        journal_valid=True,
    )


EXPERT_FACADE_EXPORTS = (
    "begin_expert_replay",
    "finish_expert_replay",
    "replay_expert_parent_group",
)

IO_FACADE_EXPORTS = (
    "abort_sportradar_candidate_output",
    "abort_expert_replay_construction",
    "abort_expert_writer",
    "acknowledge_begin_replay",
    "acknowledge_expert_publication",
    "acknowledge_finish_replay",
    "acknowledge_parent_group_replay",
    "acquire_expert_journal_root",
    "append_expert_emergency_group_and_terminal",
    "append_expert_group",
    "append_expert_terminal",
    "build_aligned_expert_terminal",
    "close_expert_reader",
    "collect_sportradar_candidate_source_seals",
    "collect_expert_current_environment",
    "create_expert_journal",
    "create_sportradar_candidate_output_writer",
    "finalize_sportradar_candidate_output",
    "inspect_expert_companion_file_identities",
    "inspect_phase1_evidence_file_identities",
    "issue_begin_replay_authorization",
    "issue_expert_append_permit",
    "issue_expert_emergency_append_permit",
    "issue_expert_environment_collection_authority",
    "issue_expert_purge_capability",
    "issue_expert_read_capability",
    "issue_expert_replay_construction_authority",
    "issue_expert_terminal_permit",
    "issue_sportradar_candidate_source_seal_collection_authority",
    "issue_finish_replay_authorization",
    "issue_parent_group_replay_authorization",
    "prepare_expert_replay_begin",
    "prove_expert_live_evidence_tail",
    "purge_expert_session",
    "read_expert_manifest",
    "read_expert_terminal_and_summary",
    "read_next_expert_group",
    "read_next_replay_companion_group",
    "read_next_replay_evidence_parent",
    "read_replay_finish_material",
    "recover_and_purge_expert_journals",
    "revoke_expert_reader",
    "sample_expert_retention_wall_ns",
    "take_expert_replay_denial",
    "append_sportradar_candidate_capture",
    "append_sportradar_candidate_failure",
    "append_sportradar_candidate_parser_result",
    "append_sportradar_candidate_permit",
    "prepare_sportradar_summary_read",
    "prepare_sportradar_timeline_read",
    "read_sportradar_summary",
    "read_sportradar_timeline",
)


def _group_chain(
    manifest: ExpertSessionManifestV1,
    *,
    count: int,
) -> tuple[
    tuple[PersistedEvent, ...],
    tuple[tuple[ExpertJournalGroupV1, tuple[bytes, ...]], ...],
]:
    universe, policy, _, _ = _valid_artifacts(raw_count=max(count, 1))
    state = initial_expert_state(manifest, universe, policy)
    cursor = _genesis_cursor(manifest, state)
    parents: list[PersistedEvent] = []
    groups: list[tuple[ExpertJournalGroupV1, tuple[bytes, ...]]] = []
    for index in range(count):
        parent = raw_parent(
            session_id=manifest.session_id,
            ingest_seq=2 * (index + 1),
            local_wall_ns=100 + index,
            local_monotonic_ns=200 + index,
        )
        group, payloads, cursor, reduction = _independent_group(
            manifest,
            cursor,
            parent,
            prior_state_override=state,
        )
        state = reduction.final_state
        parents.append(parent)
        groups.append((group, payloads))
    return tuple(parents), tuple(groups)


def _diagnostic_file_proof(
    role: ExpertReplayDiagnosticRoleV1,
    issue: ExpertReplayDiagnosticIssueV1,
    *,
    present: bool,
) -> ExpertReplayDiagnosticFileProofV1:
    prefix = (
        (role.value + ":" + issue.value).encode("ascii")
        if present
        else b""
    )
    values: dict[str, object] = {
        "schema_version": 1,
        "role": role,
        "entry_present": present,
        "device": 31 if present else None,
        "inode": 40 + tuple(ExpertReplayDiagnosticRoleV1).index(role)
        if present
        else None,
        "uid": 501 if present else None,
        "mode": stat.S_IFREG | 0o600 if present else None,
        "link_count": 1 if present else None,
        "mtime_ns": 700 if present else None,
        "ctime_ns": 701 if present else None,
        "observed_size": len(prefix) if present else 0,
        "observed_prefix_length": len(prefix) if present else 0,
        "observed_prefix_sha256": sha256(prefix).hexdigest(),
        "issue": issue,
    }
    values["proof_sha256"] = (
        compute_expert_replay_diagnostic_file_proof_sha256(**values)
    )
    return contracts._create_expert_replay_diagnostic_file_proof_v1(
        **values
    )


def _denied_result(
    evidence: EvidenceReplayContextV1,
    mismatch: ExpertReplayMismatchV1,
    *,
    file_proofs: tuple[ExpertReplayDiagnosticFileProofV1, ...] = (),
    companion_scan: ExpertJournalScanSummaryV1 | None = None,
    sampled_wall_ns: int | None = None,
    acknowledged_parent_count: int = 0,
    acknowledged_expert_record_count: int = 0,
    prepared: bool = True,
) -> ExpertReplayDeniedV1:
    sampled = (
        evidence.session_manifest.required_retention_until_ns
        if mismatch is ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
        else 999
        if sampled_wall_ns is None
        else sampled_wall_ns
    )
    phase1_summary = (
        expert_phase1_replay_summary_sha256(evidence.replay_result)
        if prepared
        else None
    )
    proof_values: dict[str, object] = {
        "schema_version": 1,
        "session_id": evidence.session_manifest.session_id,
        "mismatch": mismatch,
        "phase1_replay_summary_sha256": phase1_summary,
        "file_proofs": file_proofs,
        "companion_scan": companion_scan,
        "common_deadline_ns": (
            evidence.session_manifest.required_retention_until_ns
        ),
        "final_sampled_wall_ns": sampled,
        "acknowledged_parent_count": acknowledged_parent_count,
        "acknowledged_expert_record_count": (
            acknowledged_expert_record_count
        ),
    }
    proof_values["proof_sha256"] = (
        compute_expert_replay_diagnostic_proof_sha256(**proof_values)
    )
    proof = contracts._create_expert_replay_diagnostic_proof_v1(
        **proof_values
    )
    result = ExpertReplayResultV1(
        state=None,
        trace_sha256=None,
        evidence_raw_count=(
            evidence.replay_result.raw_count if prepared else 0
        ),
        evidence_derived_count=(
            evidence.replay_result.derived_count if prepared else 0
        ),
        expert_group_count=acknowledged_parent_count,
        expert_record_count=acknowledged_expert_record_count,
        evidence_exact=prepared and evidence.replay_result.exact_replay,
        companion_valid=False,
        terminals_aligned=False,
        exact_replay=False,
        mismatch=mismatch,
        final_authorization_sha256=None,
        evaluation_input_eligible=False,
        research_evaluable=False,
    )
    return contracts._create_expert_replay_denied_v1(
        result=result,
        proof=proof,
    )


def _service_bindings(
    evidence: EvidenceReplayContextV1,
) -> tuple[
    ExpertJournalRootAuthorityV1,
    ProviderPersistenceAuthorizer,
    RetentionCoordinator,
]:
    root = object.__new__(ExpertJournalRootAuthorityV1)
    coordinator = object.__new__(RetentionCoordinator)
    authorizer = object.__new__(ProviderPersistenceAuthorizer)
    object.__setattr__(authorizer, "gate", object())
    object.__setattr__(authorizer, "coordinator", coordinator)
    object.__setattr__(
        authorizer,
        "session_manifest",
        evidence.session_manifest,
    )
    object.__setattr__(authorizer, "bound_decision", object())
    return root, authorizer, coordinator


class _ReplayFacadeScript:
    """Executed, fail-closed facade double with the ruled authority states."""

    def __init__(
        self,
        *,
        manifest: ExpertSessionManifestV1,
        evidence: EvidenceReplayContextV1,
        root: ExpertJournalRootAuthorityV1,
        parent_count: int,
        companion_count: int | None = None,
        deny_at: str | None = None,
        deny_mismatch: ExpertReplayMismatchV1 = (
            ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
        ),
        deny_role: ExpertReplayDiagnosticRoleV1 | None = None,
        fail_at: str | None = None,
    ) -> None:
        self.manifest = manifest
        self.evidence = evidence
        self.root = root
        self.construction = object.__new__(
            ExpertReplayConstructionAuthorityV1
        )
        self.environment_authority = object.__new__(
            ExpertEnvironmentCollectionAuthorityV1
        )
        pair_count = max(
            parent_count,
            companion_count or parent_count,
        )
        all_parents, all_groups = (
            _group_chain(manifest, count=pair_count)
            if pair_count
            else ((), ())
        )
        self.parents: list[PersistedEvent | None] = list(
            all_parents[:parent_count]
        )
        companion_total = (
            parent_count if companion_count is None else companion_count
        )
        self.groups: list[
            tuple[ExpertJournalGroupV1, tuple[bytes, ...]] | None
        ] = list(all_groups[:companion_total])
        self.parent_total = parent_count
        self.group_total = companion_total
        self.state = "unissued"
        self.sequence = 0
        self.parent_index = 0
        self.group_index = 0
        self.current_parent: PersistedEvent | None = None
        self.current_group: (
            tuple[ExpertJournalGroupV1, tuple[bytes, ...]] | None
        ) = None
        self.outstanding: RetentionReplayAuthorizationV1 | None = None
        self.accumulator: ExpertReplayAccumulatorV1 | None = None
        self.result: ExpertReplayResultV1 | None = None
        self.trace: list[str] = []
        self.seen_roots: list[ExpertJournalRootAuthorityV1] = []
        self.unmatched_drain_count = 0
        self.live_pair_count = 0
        self.maximum_live_pair_count = 0
        self.environment_issue_count = 0
        self.environment_collect_count = 0
        self.abort_count = 0
        self.deny_at = deny_at
        self.deny_mismatch = deny_mismatch
        self.deny_role = deny_role
        self.fail_at = fail_at
        self.denial: ExpertReplayDeniedV1 | None = None
        self.closed = False
        self.root_still_usable = True
        self.issued_authorizations: list[
            RetentionReplayAuthorizationV1
        ] = []

    def _enter(self, name: str) -> None:
        self.trace.append(name)
        if self.fail_at == name:
            raise RuntimeError("injected_service_failure")
        if self.deny_at == name:
            file_proofs = (
                (
                    _diagnostic_file_proof(
                        self.deny_role,
                        ExpertReplayDiagnosticIssueV1.ENTRY_REPLACED,
                        present=True,
                    ),
                )
                if (
                    self.deny_mismatch
                    is ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH
                    and self.deny_role is not None
                )
                else ()
            )
            self.denial = _denied_result(
                self.evidence,
                self.deny_mismatch,
                file_proofs=file_proofs,
                acknowledged_parent_count=(
                    0
                    if self.accumulator is None
                    else self.accumulator.processed_parent_count
                ),
                acknowledged_expert_record_count=(
                    0
                    if self.accumulator is None
                    else self.accumulator.cursor.record_count
                ),
            )
            self.state = "terminal_denied"
            raise ExpertReplayAccessDenied()

    def _require(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
        *allowed: str,
    ) -> None:
        if (
            authority is not self.construction
            or self.closed
            or self.state not in allowed
        ):
            raise ValueError("expert_replay_authority_invalid")

    def issue_expert_replay_construction_authority(
        self,
        authority: ExpertJournalRootAuthorityV1,
        *,
        persistence_authorizer: ProviderPersistenceAuthorizer,
        coordinator: RetentionCoordinator,
    ) -> ExpertReplayConstructionAuthorityV1 | ExpertReplayDeniedV1:
        self._enter("issue_construction")
        if authority is not self.root:
            raise AssertionError("service substituted the root authority")
        if (
            persistence_authorizer.coordinator is not coordinator
            or persistence_authorizer.session_manifest
            is not self.evidence.session_manifest
        ):
            raise AssertionError("service broke authorizer binding")
        self.seen_roots.append(authority)
        if self.state != "unissued":
            raise ValueError("expert_replay_authority_invalid")
        self.state = "new"
        return self.construction

    def prepare_expert_replay_begin(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
    ) -> ExpertReplayBeginReadyV1 | ExpertReplayDeniedV1:
        self._enter("prepare_begin")
        self._require(authority, "new")
        self.state = "begin_ready"
        return contracts._create_expert_replay_begin_ready_v1(
            evidence=self.evidence,
            manifest=self.manifest,
        )

    def issue_expert_environment_collection_authority(
        self,
        authority: ExpertJournalRootAuthorityV1,
        *,
        persistence_authorizer: ProviderPersistenceAuthorizer,
        coordinator: RetentionCoordinator,
    ) -> ExpertEnvironmentCollectionAuthorityV1:
        self._enter("issue_environment")
        if authority is not self.root:
            raise AssertionError("environment issue did not reuse root")
        if (
            persistence_authorizer.coordinator is not coordinator
            or self.environment_issue_count
        ):
            raise ValueError("expert_environment_authority_invalid")
        self.seen_roots.append(authority)
        self.environment_issue_count += 1
        return self.environment_authority

    def collect_expert_current_environment(
        self,
        authority: ExpertEnvironmentCollectionAuthorityV1,
    ) -> ExpertCollectedEnvironmentV1:
        self._enter("collect_environment")
        if (
            authority is not self.environment_authority
            or self.environment_collect_count
        ):
            raise ValueError("expert_environment_authority_invalid")
        self.environment_collect_count += 1
        return contracts._create_expert_collected_environment_v1(
            current=self.manifest.environment,
            normalizers=self.manifest.normalizers,
            structural_schemas=self.manifest.structural_schemas,
            event_schemas=self.manifest.event_schemas,
        )

    def issue_begin_replay_authorization(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
    ) -> RetentionReplayAuthorizationV1:
        self._enter("issue_begin")
        self._require(authority, "begin_ready")
        token = _authorization(
            self.manifest,
            self.evidence,
            operation="begin",
            sequence=0,
        )
        self.outstanding = token
        self.issued_authorizations.append(token)
        self.state = "begin_auth_outstanding"
        return token

    def acknowledge_begin_replay(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
        *,
        authorization: RetentionReplayAuthorizationV1,
        accumulator: ExpertReplayAccumulatorV1,
    ) -> None:
        if (
            type(authorization) is not RetentionReplayAuthorizationV1
            or type(accumulator) is not ExpertReplayAccumulatorV1
        ):
            raise TypeError("replay_begin_ack")
        self._enter("ack_begin")
        self._require(authority, "begin_auth_outstanding")
        if (
            authorization is not self.outstanding
            or accumulator.last_authorization_sha256
            != authorization.authorization_sha256
        ):
            raise ValueError("replay_begin_ack_invalid")
        self.accumulator = accumulator
        self.outstanding = None
        self.sequence = 1
        self.state = (
            "begin_diagnostic"
            if accumulator.mismatch is not None
            else "pair_empty"
        )

    def read_next_replay_evidence_parent(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
    ) -> PersistedEvent | None:
        self._enter("read_evidence")
        self._require(authority, "pair_empty")
        if self.outstanding is not None:
            raise AssertionError("read while token outstanding")
        if self.parent_index == self.parent_total:
            self.state = "evidence_eof_ready"
            return None
        parent = self.parents[self.parent_index]
        assert type(parent) is PersistedEvent
        self.parents[self.parent_index] = None
        self.parent_index += 1
        self.current_parent = parent
        self.live_pair_count = 1
        self.maximum_live_pair_count = max(
            self.maximum_live_pair_count,
            self.live_pair_count,
        )
        self.state = "evidence_parent_ready"
        return parent

    def read_next_replay_companion_group(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
    ) -> tuple[ExpertJournalGroupV1, tuple[bytes, ...]] | None:
        self._enter("read_companion")
        self._require(
            authority,
            "evidence_parent_ready",
            "evidence_eof_ready",
        )
        if self.outstanding is not None:
            raise AssertionError("read while token outstanding")
        item = (
            None
            if self.group_index == self.group_total
            else self.groups[self.group_index]
        )
        if item is not None:
            self.groups[self.group_index] = None
            self.group_index += 1
        if self.state == "evidence_parent_ready":
            if item is None:
                self.state = "cardinality_mismatch"
                return None
            self.current_group = item
            self.state = "pair_complete"
            return item
        if item is None:
            self.state = "both_eof"
            return None
        self.current_group = item
        self.live_pair_count = 1
        self.maximum_live_pair_count = max(
            self.maximum_live_pair_count,
            self.live_pair_count,
        )
        self.state = "cardinality_mismatch"
        return item

    def issue_parent_group_replay_authorization(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
    ) -> RetentionReplayAuthorizationV1:
        self._enter("issue_parent")
        self._require(authority, "pair_complete")
        if self.current_parent is None or self.current_group is None:
            raise AssertionError("authorization without a complete pair")
        token = _authorization(
            self.manifest,
            self.evidence,
            operation="parent_group",
            sequence=self.sequence,
            expected_parent_ingest_seq=self.current_parent.ingest_seq,
        )
        self.outstanding = token
        self.issued_authorizations.append(token)
        self.state = "parent_auth_outstanding"
        return token

    def acknowledge_parent_group_replay(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
        *,
        authorization: RetentionReplayAuthorizationV1,
        accumulator: ExpertReplayAccumulatorV1,
    ) -> None:
        if (
            type(authorization) is not RetentionReplayAuthorizationV1
            or type(accumulator) is not ExpertReplayAccumulatorV1
        ):
            raise TypeError("replay_parent_ack")
        self._enter("ack_parent")
        self._require(authority, "parent_auth_outstanding")
        if (
            authorization is not self.outstanding
            or accumulator.last_authorization_sha256
            != authorization.authorization_sha256
        ):
            raise ValueError("replay_parent_ack_invalid")
        self.accumulator = accumulator
        self.outstanding = None
        self.sequence += 1
        self.current_parent = None
        self.current_group = None
        self.live_pair_count = 0
        self.state = (
            "begin_diagnostic"
            if accumulator.mismatch is not None
            else "pair_empty"
        )

    def read_replay_finish_material(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
    ) -> tuple[ExpertSessionTerminalV1 | None, ExpertJournalScanSummaryV1]:
        self._enter("read_finish")
        self._require(
            authority,
            "begin_diagnostic",
            "both_eof",
            "cardinality_mismatch",
        )
        if self.state == "cardinality_mismatch":
            if self.current_parent is not None:
                self.unmatched_drain_count += 1
            if self.current_group is not None:
                self.unmatched_drain_count += 1
            while self.parent_index < self.parent_total:
                self.parents[self.parent_index] = None
                self.parent_index += 1
                self.unmatched_drain_count += 1
                self.maximum_live_pair_count = max(
                    self.maximum_live_pair_count,
                    1,
                )
            while self.group_index < self.group_total:
                self.groups[self.group_index] = None
                self.group_index += 1
                self.unmatched_drain_count += 1
                self.maximum_live_pair_count = max(
                    self.maximum_live_pair_count,
                    1,
                )
        total_groups = self.group_total
        scan = ExpertJournalScanSummaryV1(
            schema_version=1,
            file_size=1_000 + total_groups,
            last_good_offset=1_000 + total_groups,
            last_frame_sequence=total_groups + 1,
            group_count=total_groups,
            record_count=total_groups,
            terminal_clean=True,
            issue=None,
            journal_valid=True,
        )
        terminal = (
            _terminal(self.accumulator)
            if (
                self.accumulator is not None
                and self.parent_total == total_groups
                and self.accumulator.processed_parent_count == total_groups
            )
            else None
        )
        self.current_parent = None
        self.current_group = None
        self.live_pair_count = 0
        self.state = "finish_ready"
        return terminal, scan

    def issue_finish_replay_authorization(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
    ) -> RetentionReplayAuthorizationV1:
        self._enter("issue_finish")
        self._require(authority, "finish_ready")
        token = _authorization(
            self.manifest,
            self.evidence,
            operation="finish",
            sequence=self.sequence,
        )
        self.outstanding = token
        self.issued_authorizations.append(token)
        self.state = "finish_auth_outstanding"
        return token

    def acknowledge_finish_replay(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
        *,
        authorization: RetentionReplayAuthorizationV1,
        result: ExpertReplayResultV1,
    ) -> None:
        if (
            type(authorization) is not RetentionReplayAuthorizationV1
            or type(result) is not ExpertReplayResultV1
        ):
            raise TypeError("replay_finish_ack")
        self._enter("ack_finish")
        self._require(authority, "finish_auth_outstanding")
        if (
            authorization is not self.outstanding
            or result.final_authorization_sha256
            != authorization.authorization_sha256
        ):
            raise ValueError("replay_finish_ack_invalid")
        self.result = result
        self.outstanding = None
        self.closed = True
        self.state = "consumed_closed"

    def take_expert_replay_denial(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
    ) -> ExpertReplayDeniedV1:
        self.trace.append("take_denial")
        self._require(authority, "terminal_denied")
        if self.denial is None:
            raise ValueError("expert_replay_denial_invalid")
        self.closed = True
        self.state = "denied_closed"
        return self.denial

    def abort_expert_replay_construction(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
    ) -> None:
        self.trace.append("abort")
        if authority is not self.construction:
            raise ValueError("expert_replay_authority_invalid")
        if self.closed:
            return
        self.abort_count += 1
        self.closed = True
        self.state = "aborted_closed"
        self.current_parent = None
        self.current_group = None
        self.outstanding = None
        self.live_pair_count = 0


def _patch_replay_facade(
    service_module: object,
    script: _ReplayFacadeScript,
) -> ExitStack:
    stack = ExitStack()
    for public_name, method_name in (
        (
            "issue_expert_replay_construction_authority",
            "issue_expert_replay_construction_authority",
        ),
        ("prepare_expert_replay_begin", "prepare_expert_replay_begin"),
        (
            "issue_expert_environment_collection_authority",
            "issue_expert_environment_collection_authority",
        ),
        (
            "collect_expert_current_environment",
            "collect_expert_current_environment",
        ),
        (
            "issue_begin_replay_authorization",
            "issue_begin_replay_authorization",
        ),
        ("acknowledge_begin_replay", "acknowledge_begin_replay"),
        (
            "read_next_replay_evidence_parent",
            "read_next_replay_evidence_parent",
        ),
        (
            "read_next_replay_companion_group",
            "read_next_replay_companion_group",
        ),
        (
            "issue_parent_group_replay_authorization",
            "issue_parent_group_replay_authorization",
        ),
        (
            "acknowledge_parent_group_replay",
            "acknowledge_parent_group_replay",
        ),
        ("read_replay_finish_material", "read_replay_finish_material"),
        (
            "issue_finish_replay_authorization",
            "issue_finish_replay_authorization",
        ),
        ("acknowledge_finish_replay", "acknowledge_finish_replay"),
        ("take_expert_replay_denial", "take_expert_replay_denial"),
        (
            "abort_expert_replay_construction",
            "abort_expert_replay_construction",
        ),
    ):
        stack.enter_context(
            mock.patch.object(
                service_module,
                public_name,
                getattr(script, method_name),
                create=True,
            )
        )
    return stack


def _run_scripted_service(
    *,
    parent_count: int,
    companion_count: int | None = None,
    deny_at: str | None = None,
    deny_mismatch: ExpertReplayMismatchV1 = (
        ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
    ),
    deny_role: ExpertReplayDiagnosticRoleV1 | None = None,
    fail_at: str | None = None,
) -> tuple[object, _ReplayFacadeScript]:
    service_module = _module(RUNTIME_MODULE)
    service = service_module.replay_expert_session
    universe, policy, manifest, evidence = _valid_artifacts(
        raw_count=parent_count
    )
    root, authorizer, coordinator = _service_bindings(evidence)
    script = _ReplayFacadeScript(
        manifest=manifest,
        evidence=evidence,
        root=root,
        parent_count=parent_count,
        companion_count=companion_count,
        deny_at=deny_at,
        deny_mismatch=deny_mismatch,
        deny_role=deny_role,
        fail_at=fail_at,
    )
    with _patch_replay_facade(service_module, script):
        result = service(
            authority=root,
            persistence_authorizer=authorizer,
            coordinator=coordinator,
            universe=universe,
            policy=policy,
        )
    return result, script


class ReplaySurfaceTests(unittest.TestCase):
    def test_all_governed_modules_and_surfaces_exist(self) -> None:
        required = {
            REPLAY_MODULE: (
                "begin_expert_replay",
                "replay_expert_parent_group",
                "finish_expert_replay",
            ),
            EXPERT_FACADE_MODULE: (
                "begin_expert_replay",
                "replay_expert_parent_group",
                "finish_expert_replay",
            ),
            IO_FACADE_MODULE: STORE_REPLAY_SURFACES,
            RUNTIME_MODULE: ("replay_expert_session",),
        }
        missing: list[str] = []
        for module_name, names in required.items():
            try:
                module = _module(module_name)
            except AssertionError:
                missing.append(module_name)
                continue
            missing.extend(
                f"{module_name}.{name}"
                for name in names
                if not hasattr(module, name)
            )
        self.assertEqual(tuple(missing), ())

    def test_exact_public_signatures(self) -> None:
        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        parent = _surface(REPLAY_MODULE, "replay_expert_parent_group")
        finish = _surface(REPLAY_MODULE, "finish_expert_replay")
        service = _surface(RUNTIME_MODULE, "replay_expert_session")
        self.assertEqual(
            tuple(inspect.signature(begin).parameters),
            (
                "manifest",
                "current_environment",
                "universe",
                "policy",
                "evidence",
                "authorization",
            ),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in inspect.signature(begin).parameters.values()
            )
        )
        self.assertEqual(
            tuple(inspect.signature(parent).parameters),
            (
                "accumulator",
                "authorization",
                "parent",
                "stored_group",
                "stored_payloads",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(finish).parameters),
            (
                "accumulator",
                "final_authorization",
                "companion_terminal",
                "companion_scan",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(service).parameters),
            (
                "authority",
                "persistence_authorizer",
                "coordinator",
                "universe",
                "policy",
            ),
        )
        self.assertTrue(
            all(
                item.kind is inspect.Parameter.KEYWORD_ONLY
                for item in inspect.signature(service).parameters.values()
            )
        )

    def test_literal_mismatch_vocabulary_and_precedence(self) -> None:
        self.assertEqual(
            tuple(item.value for item in ExpertReplayMismatchV1),
            MISMATCH_VALUES,
        )

    def test_wrong_direct_python_types_raise_type_error(self) -> None:
        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        parent = _surface(REPLAY_MODULE, "replay_expert_parent_group")
        finish = _surface(REPLAY_MODULE, "finish_expert_replay")
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=1)
        begin_auth = _authorization(
            manifest,
            evidence,
            operation="begin",
            sequence=0,
        )
        valid = {
            "manifest": manifest,
            "current_environment": manifest.environment,
            "universe": universe,
            "policy": policy,
            "evidence": evidence,
            "authorization": begin_auth,
        }
        for name in tuple(valid):
            with self.subTest(begin=name), self.assertRaises(TypeError):
                begin(**{**valid, name: object()})
        accumulator = begin(**valid)
        raw = raw_parent(session_id=manifest.session_id, ingest_seq=2)
        group, payloads, _, _ = _independent_group(
            manifest,
            accumulator.cursor,
            raw,
        )
        parent_auth = _authorization(
            manifest,
            evidence,
            operation="parent_group",
            sequence=1,
            expected_parent_ingest_seq=2,
        )
        parent_values = {
            "accumulator": accumulator,
            "authorization": parent_auth,
            "parent": raw,
            "stored_group": group,
            "stored_payloads": payloads,
        }
        for name in tuple(parent_values):
            with self.subTest(parent=name), self.assertRaises(TypeError):
                parent(**{**parent_values, name: object()})
        finished_accumulator = parent(**parent_values)
        finish_auth = _authorization(
            manifest,
            evidence,
            operation="finish",
            sequence=2,
        )
        scan = _clean_scan(finished_accumulator.cursor)
        finish_values = {
            "accumulator": finished_accumulator,
            "final_authorization": finish_auth,
            "companion_terminal": _terminal(finished_accumulator),
            "companion_scan": scan,
        }
        for name in tuple(finish_values):
            with self.subTest(finish=name), self.assertRaises(TypeError):
                finish(**{**finish_values, name: object()})


class PureBeginReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        (
            self.universe,
            self.policy,
            self.manifest,
            self.evidence,
        ) = _valid_artifacts(raw_count=1)
        self.authorization = _authorization(
            self.manifest,
            self.evidence,
            operation="begin",
            sequence=0,
        )

    def call(self, **changes: object) -> ExpertReplayAccumulatorV1:
        values = {
            "manifest": self.manifest,
            "current_environment": self.manifest.environment,
            "universe": self.universe,
            "policy": self.policy,
            "evidence": self.evidence,
            "authorization": self.authorization,
        }
        values.update(changes)
        return self.begin(**values)

    def test_begin_recomputes_exact_genesis_and_consumes_sequence_zero(self) -> None:
        result = self.call()
        expected_state = initial_expert_state(
            self.manifest,
            self.universe,
            self.policy,
        )
        self.assertEqual(result.state, expected_state)
        self.assertEqual(
            result.cursor,
            _genesis_cursor(self.manifest, expected_state),
        )
        self.assertEqual(result.evidence_raw_count, 1)
        self.assertEqual(result.evidence_derived_count, 1)
        self.assertEqual(result.processed_parent_count, 0)
        self.assertEqual(result.last_authorization_sequence, 0)
        self.assertEqual(
            result.last_authorization_sha256,
            self.authorization.authorization_sha256,
        )
        self.assertIsNone(result.mismatch)

    def test_begin_mismatch_1_through_12_are_reachable(self) -> None:
        cases = (
            (
                ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
                {
                    "evidence": _unchecked_replace(
                        self.evidence,
                        session_start_record_sha256=SHA_D,
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.EVIDENCE_REPLAY_NOT_EXACT,
                {
                    "evidence": _unchecked_replace(
                        self.evidence,
                        replay_result=replace(
                            self.evidence.replay_result,
                            exact_replay=False,
                            replay_mismatch=ReplayMismatch.STATE,
                        ),
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.EVIDENCE_TERMINAL_NOT_CLEAN,
                {"evidence": _evidence_context(raw_count=1, terminal_clean=False)},
            ),
            (
                ExpertReplayMismatchV1.EVIDENCE_SESSION_MISMATCH,
                {
                    "authorization": _unchecked_replace(
                        self.authorization,
                        session_id="22222222-2222-4222-8222-222222222222",
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.EVIDENCE_MANIFEST_MISMATCH,
                {
                    "manifest": _unchecked_replace(
                        self.manifest,
                        evidence_session_start_record_sha256=SHA_D,
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
                {
                    "authorization": _unchecked_replace(
                        self.authorization,
                        authorized_operation="finish",
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                {
                    "authorization": _rehash_authorization(
                        self.authorization,
                        final_sampled_wall_ns=1_000,
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
                {
                    "authorization": _rehash_authorization(
                        self.authorization,
                        evidence_marker_identity=_identity(
                            "phase1_marker",
                            session_anchor_sha256=SHA_D,
                            inode=91,
                        ),
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
                {
                    "current_environment": replace(
                        self.manifest.environment,
                        runtime_code_sha256=SHA_D,
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.RETENTION_BINDING_MISMATCH,
                {
                    "authorization": _rehash_authorization(
                        self.authorization,
                        common_deadline_ns=(
                            self.authorization.common_deadline_ns + 1
                        ),
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
                {
                    "manifest": _unchecked_replace(
                        self.manifest,
                        structural_schemas=object(),
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.COMPANION_MANIFEST_MISMATCH,
                {
                    "policy": replace(
                        self.policy,
                        max_score_age_ns=self.policy.max_score_age_ns + 1,
                    )
                },
            ),
        )
        for expected, changes in cases:
            with self.subTest(expected=expected):
                self.assertIs(self.call(**changes).mismatch, expected)

    def test_begin_first_match_precedence_for_adjacent_1_through_12(self) -> None:
        mutations = (
            (
                "evidence",
                _unchecked_replace(
                    self.evidence,
                    session_start_record_sha256=SHA_D,
                ),
            ),
            (
                "evidence",
                _unchecked_replace(
                    self.evidence,
                    replay_result=replace(
                        self.evidence.replay_result,
                        exact_replay=False,
                        replay_mismatch=ReplayMismatch.STATE,
                    ),
                ),
            ),
            (
                "evidence",
                _evidence_context(raw_count=1, terminal_clean=False),
            ),
            (
                "authorization",
                _unchecked_replace(
                    self.authorization,
                    session_id="22222222-2222-4222-8222-222222222222",
                ),
            ),
            (
                "manifest",
                _unchecked_replace(
                    self.manifest,
                    evidence_session_start_record_sha256=SHA_D,
                ),
            ),
            (
                "authorization",
                _unchecked_replace(
                    self.authorization,
                    authorized_operation="finish",
                ),
            ),
            (
                "authorization",
                _rehash_authorization(
                    self.authorization,
                    final_sampled_wall_ns=1_000,
                ),
            ),
            (
                "authorization",
                _rehash_authorization(
                    self.authorization,
                    evidence_wal_identity=_identity(
                        "phase1_wal",
                        session_anchor_sha256=SHA_D,
                        inode=92,
                    ),
                ),
            ),
            (
                "current_environment",
                replace(
                    self.manifest.environment,
                    dependency_lock_sha256=SHA_D,
                ),
            ),
            (
                "authorization",
                _rehash_authorization(
                    self.authorization,
                    common_deadline_ns=(
                        self.authorization.common_deadline_ns + 1
                    ),
                ),
            ),
            (
                "manifest",
                _unchecked_replace(self.manifest, normalizers=object()),
            ),
            (
                "policy",
                replace(
                    self.policy,
                    max_book_age_ns=self.policy.max_book_age_ns + 1,
                ),
            ),
        )
        for index in range(len(mutations) - 1):
            first_name, first_value = mutations[index]
            second_name, second_value = mutations[index + 1]
            if first_name == second_name:
                continue
            with self.subTest(first=index + 1, second=index + 2):
                result = self.call(
                    **{
                        first_name: first_value,
                        second_name: second_value,
                    }
                )
                self.assertEqual(result.mismatch.value, MISMATCH_VALUES[index])

    def test_well_formed_universe_and_policy_mismatch_reaches_item_12(self) -> None:
        changed_binding = replace(
            self.universe.bindings[0],
            scheduled_start_wall_ns=(
                self.universe.bindings[0].scheduled_start_wall_ns + 1
            ),
        )
        changed_universe = binding_universe(
            bindings=(changed_binding,),
            metadata=self.universe.metadata,
        )
        changed_policy = sync_policy(
            universe_sha256=changed_universe.universe_sha256
        )
        result = self.call(
            universe=changed_universe,
            policy=changed_policy,
        )
        self.assertIs(
            result.mismatch,
            ExpertReplayMismatchV1.COMPANION_MANIFEST_MISMATCH,
        )
        self.assertEqual(result.last_authorization_sequence, 0)


class PureParentReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        self.parent_step = _surface(
            REPLAY_MODULE,
            "replay_expert_parent_group",
        )
        (
            self.universe,
            self.policy,
            self.manifest,
            self.evidence,
        ) = _valid_artifacts(raw_count=1)
        self.accumulator = self.begin(
            manifest=self.manifest,
            current_environment=self.manifest.environment,
            universe=self.universe,
            policy=self.policy,
            evidence=self.evidence,
            authorization=_authorization(
                self.manifest,
                self.evidence,
                operation="begin",
                sequence=0,
            ),
        )
        self.parent = raw_parent(
            session_id=self.manifest.session_id,
            ingest_seq=2,
        )
        (
            self.group,
            self.payloads,
            self.expected_cursor,
            self.reduction,
        ) = _independent_group(
            self.manifest,
            self.accumulator.cursor,
            self.parent,
        )
        self.authorization = _authorization(
            self.manifest,
            self.evidence,
            operation="parent_group",
            sequence=1,
            expected_parent_ingest_seq=2,
        )

    def call(self, **changes: object) -> ExpertReplayAccumulatorV1:
        values = {
            "accumulator": self.accumulator,
            "authorization": self.authorization,
            "parent": self.parent,
            "stored_group": self.group,
            "stored_payloads": self.payloads,
        }
        values.update(changes)
        return self.parent_step(**values)

    def test_parent_recomputes_normalization_reduction_group_and_cursor(self) -> None:
        with mock.patch(
            "inci_tennis_expert.replay.normalize_expert_parent",
            wraps=normalize_expert_parent,
        ) as normalize_spy, mock.patch(
            "inci_tennis_expert.replay.reduce_expert_parent",
            wraps=reduce_expert_parent,
        ) as reduce_spy:
            result = self.call()
        self.assertEqual(normalize_spy.call_count, 1)
        self.assertEqual(reduce_spy.call_count, 1)
        self.assertEqual(result.state, self.reduction.final_state)
        self.assertEqual(result.cursor, self.expected_cursor)
        self.assertEqual(result.processed_parent_count, 1)
        self.assertEqual(result.last_authorization_sequence, 1)
        self.assertEqual(
            result.last_authorization_sha256,
            self.authorization.authorization_sha256,
        )
        self.assertIsNone(result.mismatch)
        retained = tuple(field.name for field in fields(result))
        self.assertNotIn("parent", retained)
        self.assertNotIn("group", retained)
        self.assertNotIn("payloads", retained)

    def test_stored_payload_never_drives_replayed_state(self) -> None:
        wrong_observation = replace(
            self.reduction.outcomes[0].payload.observation,
            reason=contracts.ExpertIgnoreReasonV1.EVENT_NOT_RELEVANT,
        )
        wrong_payload = canonical_expert_bytes(
            ExpertObservationIgnoredPayloadV1(wrong_observation)
        )
        wrong_group, wrong_payloads, _, _ = _independent_group(
            self.manifest,
            self.accumulator.cursor,
            self.parent,
            payloads_override=(wrong_payload,),
        )
        result = self.call(
            stored_group=wrong_group,
            stored_payloads=wrong_payloads,
        )
        self.assertIs(
            result.mismatch,
            ExpertReplayMismatchV1.NORMALIZED_OBSERVATION_MISMATCH,
        )
        self.assertIs(result.state, self.accumulator.state)
        self.assertEqual(result.cursor, self.accumulator.cursor)

    def test_strict_payload_classification_separates_24_25_26_27(self) -> None:
        descriptor = self.group.records[0].payload
        descriptor_bad = _unchecked_replace(
            descriptor,
            payload_contract_name="ExpertObservationRejectedPayloadV1",
        )
        record_bad = _unchecked_replace(
            self.group.records[0],
            payload=descriptor_bad,
        )
        group_bad = _rehash_group(
            self.group,
            records=(record_bad,),
        )
        self.assertIs(
            self.call(stored_group=group_bad).mismatch,
            ExpertReplayMismatchV1.PAYLOAD_DESCRIPTOR_MISMATCH,
        )

        self.assertIs(
            self.call(stored_payloads=(b"not-canonical-json",)).mismatch,
            ExpertReplayMismatchV1.PAYLOAD_BYTES_MISMATCH,
        )

        wrong_observation = replace(
            self.reduction.outcomes[0].payload.observation,
            reason=contracts.ExpertIgnoreReasonV1.EVENT_NOT_RELEVANT,
        )
        wrong_payload = canonical_expert_bytes(
            ExpertObservationIgnoredPayloadV1(wrong_observation)
        )
        wrong_group, wrong_payloads, _, _ = _independent_group(
            self.manifest,
            self.accumulator.cursor,
            self.parent,
            payloads_override=(wrong_payload,),
        )
        self.assertIs(
            self.call(
                stored_group=wrong_group,
                stored_payloads=wrong_payloads,
            ).mismatch,
            ExpertReplayMismatchV1.NORMALIZED_OBSERVATION_MISMATCH,
        )

        rejected_payload = canonical_expert_bytes(
            ExpertObservationRejectedPayloadV1(
                observation=self.reduction.outcomes[0].payload.observation,
                reason=contracts.ExpertRejectReasonV1.STATIC_SESSION_HALT,
            )
        )
        rejected_group, rejected_payloads, _, _ = _independent_group(
            self.manifest,
            self.accumulator.cursor,
            self.parent,
            payloads_override=(rejected_payload,),
            kinds_override=(ExpertEventKindV1.OBSERVATION_REJECTED,),
        )
        self.assertIs(
            self.call(
                stored_group=rejected_group,
                stored_payloads=rejected_payloads,
            ).mismatch,
            ExpertReplayMismatchV1.REDUCTION_MISMATCH,
        )

    def test_record_self_digest_precedes_descriptor_and_post_state(
        self,
    ) -> None:
        record = self.group.records[0]
        mutations = (
            (
                ExpertReplayMismatchV1.PAYLOAD_DESCRIPTOR_MISMATCH,
                _unchecked_replace(
                    record,
                    payload=_unchecked_replace(
                        record.payload,
                        payload_encoding="wrong",
                    ),
                ),
            ),
            (
                ExpertReplayMismatchV1.POST_STATE_MISMATCH,
                _unchecked_replace(
                    record,
                    post_expert_state_sha256=SHA_D,
                ),
            ),
        )
        for later_mismatch, changed_record in mutations:
            with self.subTest(later_mismatch=later_mismatch):
                stale_group = _unchecked_replace(
                    self.group,
                    records=(changed_record,),
                )
                self.assertIs(
                    self.call(stored_group=stale_group).mismatch,
                    ExpertReplayMismatchV1.RECORD_DIGEST_MISMATCH,
                )
                exact_group = _rehash_group(
                    self.group,
                    records=(changed_record,),
                )
                self.assertIs(
                    self.call(stored_group=exact_group).mismatch,
                    later_mismatch,
                )

    def test_parent_mismatches_13_and_16_through_29_are_reachable(self) -> None:
        record = self.group.records[0]
        trace = self.group.trace_steps[0]
        descriptor_bad = _unchecked_replace(
            record.payload,
            payload_encoding="wrong",
        )
        descriptor_group = _rehash_group(
            self.group,
            records=(
                _unchecked_replace(record, payload=descriptor_bad),
            ),
        )
        wrong_observation = replace(
            self.reduction.outcomes[0].payload.observation,
            reason=contracts.ExpertIgnoreReasonV1.EVENT_NOT_RELEVANT,
        )
        wrong_payload = canonical_expert_bytes(
            ExpertObservationIgnoredPayloadV1(wrong_observation)
        )
        normalized_group, normalized_payloads, _, _ = _independent_group(
            self.manifest,
            self.accumulator.cursor,
            self.parent,
            payloads_override=(wrong_payload,),
        )
        rejected_payload = canonical_expert_bytes(
            ExpertObservationRejectedPayloadV1(
                observation=self.reduction.outcomes[0].payload.observation,
                reason=contracts.ExpertRejectReasonV1.STATIC_SESSION_HALT,
            )
        )
        reduction_group, reduction_payloads, _, _ = _independent_group(
            self.manifest,
            self.accumulator.cursor,
            self.parent,
            payloads_override=(rejected_payload,),
            kinds_override=(ExpertEventKindV1.OBSERVATION_REJECTED,),
        )
        mutations = (
            (
                ExpertReplayMismatchV1.EXPERT_SEQUENCE_MISMATCH,
                {"stored_group": _unchecked_replace(self.group, group_sequence=2)},
            ),
            (
                ExpertReplayMismatchV1.PARENT_ORDER_MISMATCH,
                {"stored_group": _unchecked_replace(
                    self.group,
                    parent=_unchecked_replace(self.group.parent, ingest_seq=4),
                )},
            ),
            (
                ExpertReplayMismatchV1.PARENT_KIND_MISMATCH,
                {
                    "parent": _unchecked_replace(
                        self.parent,
                        record_kind=RecordKind.DERIVED,
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.PARENT_DIGEST_MISMATCH,
                {"stored_group": _unchecked_replace(
                    self.group,
                    parent=_unchecked_replace(
                        self.group.parent,
                        record_sha256=SHA_D,
                    ),
                )},
            ),
            (
                ExpertReplayMismatchV1.PARENT_GROUP_SHAPE_MISMATCH,
                {"stored_group": _unchecked_replace(
                    self.group,
                    parent_output_count=2,
                )},
            ),
            (
                ExpertReplayMismatchV1.PRIOR_RECORD_CHAIN_MISMATCH,
                {"stored_group": _unchecked_replace(
                    self.group,
                    prior_expert_record_sha256=SHA_D,
                )},
            ),
            (
                ExpertReplayMismatchV1.PRIOR_STATE_MISMATCH,
                {"stored_group": _unchecked_replace(
                    self.group,
                    prior_expert_state_sha256=SHA_D,
                )},
            ),
            (
                ExpertReplayMismatchV1.EVENT_SCHEMA_UNPINNED,
                {"stored_group": _unchecked_replace(
                    self.group,
                    records=(
                        _unchecked_replace(record, event_schema_sha256=SHA_D),
                    ),
                )},
            ),
            (
                ExpertReplayMismatchV1.RECORD_DIGEST_MISMATCH,
                {"stored_group": _unchecked_replace(
                    self.group,
                    records=(
                        _unchecked_replace(record, record_sha256=SHA_D),
                    ),
                )},
            ),
            (
                ExpertReplayMismatchV1.PAYLOAD_DESCRIPTOR_MISMATCH,
                {"stored_group": descriptor_group},
            ),
            (
                ExpertReplayMismatchV1.PAYLOAD_BYTES_MISMATCH,
                {"stored_payloads": (b"bad",)},
            ),
            (
                ExpertReplayMismatchV1.NORMALIZED_OBSERVATION_MISMATCH,
                {
                    "stored_group": normalized_group,
                    "stored_payloads": normalized_payloads,
                },
            ),
            (
                ExpertReplayMismatchV1.REDUCTION_MISMATCH,
                {
                    "stored_group": reduction_group,
                    "stored_payloads": reduction_payloads,
                },
            ),
            (
                ExpertReplayMismatchV1.POST_STATE_MISMATCH,
                {"stored_group": _unchecked_replace(
                    self.group,
                    post_expert_state_sha256=SHA_D,
                )},
            ),
            (
                ExpertReplayMismatchV1.TRACE_MISMATCH,
                {"stored_group": _unchecked_replace(
                    self.group,
                    trace_steps=(
                        _unchecked_replace(trace, post_trace_sha256=SHA_D),
                    ),
                )},
            ),
        )
        for expected, changes in mutations:
            with self.subTest(expected=expected):
                self.assertIs(self.call(**changes).mismatch, expected)

    def test_adjacent_parent_mutations_choose_the_earlier_mismatch(self) -> None:
        record = self.group.records[0]
        ordered = (
            (
                ExpertReplayMismatchV1.EXPERT_SEQUENCE_MISMATCH,
                {"group_sequence": 2},
            ),
            (
                ExpertReplayMismatchV1.PARENT_ORDER_MISMATCH,
                {"parent": _unchecked_replace(self.group.parent, ingest_seq=4)},
            ),
            (
                ExpertReplayMismatchV1.PARENT_DIGEST_MISMATCH,
                {"parent": _unchecked_replace(
                    self.group.parent,
                    record_sha256=SHA_D,
                )},
            ),
            (
                ExpertReplayMismatchV1.PARENT_GROUP_SHAPE_MISMATCH,
                {"parent_output_count": 2},
            ),
            (
                ExpertReplayMismatchV1.PRIOR_RECORD_CHAIN_MISMATCH,
                {"prior_expert_record_sha256": SHA_D},
            ),
            (
                ExpertReplayMismatchV1.PRIOR_STATE_MISMATCH,
                {"prior_expert_state_sha256": SHA_D},
            ),
            (
                ExpertReplayMismatchV1.POST_STATE_MISMATCH,
                {"post_expert_state_sha256": SHA_D},
            ),
            (
                ExpertReplayMismatchV1.TRACE_MISMATCH,
                {"post_trace_sha256": SHA_D},
            ),
        )
        for (expected, first), (_, second) in zip(
            ordered,
            ordered[1:],
        ):
            with self.subTest(expected=expected):
                combined = dict(first)
                for name, value in second.items():
                    if name == "parent" and name in combined:
                        previous = combined[name]
                        assert type(previous) is type(self.group.parent)
                        changed = {
                            item.name: getattr(value, item.name)
                            for item in fields(value)
                            if getattr(value, item.name)
                            != getattr(self.group.parent, item.name)
                        }
                        combined[name] = _unchecked_replace(
                            previous,
                            **changed,
                        )
                    else:
                        combined[name] = value
                group = _unchecked_replace(self.group, **combined)
                self.assertIs(
                    self.call(stored_group=group).mismatch,
                    expected,
                )


class PureFinishReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        parent_step = _surface(
            REPLAY_MODULE,
            "replay_expert_parent_group",
        )
        self.finish = _surface(REPLAY_MODULE, "finish_expert_replay")
        universe, policy, self.manifest, self.evidence = _valid_artifacts(
            raw_count=1
        )
        accumulator = begin(
            manifest=self.manifest,
            current_environment=self.manifest.environment,
            universe=universe,
            policy=policy,
            evidence=self.evidence,
            authorization=_authorization(
                self.manifest,
                self.evidence,
                operation="begin",
                sequence=0,
            ),
        )
        parent = raw_parent(
            session_id=self.manifest.session_id,
            ingest_seq=2,
        )
        group, payloads, _, _ = _independent_group(
            self.manifest,
            accumulator.cursor,
            parent,
        )
        self.accumulator = parent_step(
            accumulator,
            authorization=_authorization(
                self.manifest,
                self.evidence,
                operation="parent_group",
                sequence=1,
                expected_parent_ingest_seq=2,
            ),
            parent=parent,
            stored_group=group,
            stored_payloads=payloads,
        )
        self.authorization = _authorization(
            self.manifest,
            self.evidence,
            operation="finish",
            sequence=2,
        )
        self.terminal = _terminal(self.accumulator)
        self.scan = _clean_scan(self.accumulator.cursor)

    def call(self, **changes: object) -> ExpertReplayResultV1:
        values = {
            "accumulator": self.accumulator,
            "final_authorization": self.authorization,
            "companion_terminal": self.terminal,
            "companion_scan": self.scan,
        }
        values.update(changes)
        return self.finish(**values)

    def test_exact_success_has_closed_truth_shape(self) -> None:
        result = self.call()
        self.assertEqual(result.state, self.accumulator.state)
        self.assertEqual(
            result.trace_sha256,
            self.accumulator.cursor.expert_trace_sha256,
        )
        self.assertTrue(result.evidence_exact)
        self.assertTrue(result.companion_valid)
        self.assertTrue(result.terminals_aligned)
        self.assertTrue(result.exact_replay)
        self.assertTrue(result.evaluation_input_eligible)
        self.assertFalse(result.research_evaluable)
        self.assertIsNone(result.mismatch)
        self.assertEqual(
            result.final_authorization_sha256,
            self.authorization.authorization_sha256,
        )

    def test_finish_terminal_mismatches_30_through_35_are_reachable(self) -> None:
        cases = (
            (ExpertReplayMismatchV1.TERMINAL_MISSING, {"companion_terminal": None}),
            (
                ExpertReplayMismatchV1.TERMINAL_REASON_MISMATCH,
                {"companion_terminal": _unchecked_replace(
                    self.terminal,
                    clean=False,
                    reason=ExpertTerminalReasonV1.EXPERT_HALT,
                )},
            ),
            (
                ExpertReplayMismatchV1.TERMINAL_COUNT_MISMATCH,
                {"companion_terminal": _unchecked_replace(
                    self.terminal,
                    expert_record_count=self.terminal.expert_record_count + 1,
                )},
            ),
            (
                ExpertReplayMismatchV1.TERMINAL_PROVENANCE_MISMATCH,
                {"companion_terminal": _unchecked_replace(
                    self.terminal,
                    provider_request_binding_sha256=SHA_C,
                )},
            ),
            (
                ExpertReplayMismatchV1.TERMINAL_STATE_MISMATCH,
                {"companion_terminal": _unchecked_replace(
                    self.terminal,
                    final_expert_state_sha256=SHA_D,
                )},
            ),
            (
                ExpertReplayMismatchV1.TERMINAL_TRACE_MISMATCH,
                {"companion_terminal": _unchecked_replace(
                    self.terminal,
                    final_expert_trace_sha256=SHA_D,
                )},
            ),
        )
        for expected, changes in cases:
            with self.subTest(expected=expected):
                result = self.call(**changes)
                self.assertIs(result.mismatch, expected)
                self.assertFalse(result.exact_replay)
                self.assertFalse(result.evaluation_input_eligible)
                self.assertFalse(result.research_evaluable)

    def test_cardinality_precedes_all_terminal_mismatches(self) -> None:
        terminal_faults = (
            None,
            _unchecked_replace(
                self.terminal,
                clean=False,
                reason=ExpertTerminalReasonV1.EXPERT_HALT,
            ),
            _unchecked_replace(
                self.terminal,
                expert_record_count=self.terminal.expert_record_count + 1,
            ),
            _unchecked_replace(
                self.terminal,
                provider_request_binding_sha256=SHA_C,
            ),
            _unchecked_replace(
                self.terminal,
                final_expert_state_sha256=SHA_D,
            ),
            _unchecked_replace(
                self.terminal,
                final_expert_trace_sha256=SHA_D,
            ),
        )
        for mismatch, count in (
            (ExpertReplayMismatchV1.PARENT_MISSING, 0),
            (ExpertReplayMismatchV1.PARENT_EXTRA, 2),
        ):
            for companion_terminal in terminal_faults:
                with self.subTest(
                    mismatch=mismatch,
                    companion_terminal=companion_terminal,
                ):
                    self.assertIsNone(self.accumulator.mismatch)
                    scan = replace(
                        self.scan,
                        last_frame_sequence=count + 1,
                        group_count=count,
                        record_count=count,
                    )
                    result = self.call(
                        companion_terminal=companion_terminal,
                        companion_scan=scan,
                    )
                    self.assertIs(result.mismatch, mismatch)

    def test_invalid_scan_is_item_11_before_terminal_content(self) -> None:
        scan = ExpertJournalScanSummaryV1(
            schema_version=1,
            file_size=1_000,
            last_good_offset=900,
            last_frame_sequence=1,
            group_count=1,
            record_count=1,
            terminal_clean=False,
            issue=ExpertJournalScanIssueV1.CORRUPT_TAIL,
            journal_valid=False,
        )
        result = self.call(companion_terminal=None, companion_scan=scan)
        self.assertIs(
            result.mismatch,
            ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
        )

    def test_terminal_prefix_sequence_mismatch_is_item_32_after_items_12_and_31(
        self,
    ) -> None:
        import inci_tennis_expert.journal_codec as codec
        terminal_frame = _independent_frame(
            kind=codec.EXPERT_FRAME_KIND_TERMINAL,
            sequence=99,
            metadata=_independent_canonical(self.terminal),
            payload_area=b"",
        )
        self.assertEqual(
            codec.decode_expert_frame_prefix(terminal_frame[:32])[1],
            99,
        )
        self.assertEqual(
            codec.decode_expert_terminal_frame_replay_material(
                terminal_frame
            ),
            self.terminal,
        )
        with self.assertRaises(codec.ExpertJournalCodecError):
            codec.decode_expert_terminal_frame_structural(terminal_frame)

        sequence_mismatch_scan = replace(
            self.scan,
            last_frame_sequence=99,
        )
        count_result = self.call(
            companion_scan=sequence_mismatch_scan,
        )
        self.assertIs(
            count_result.mismatch,
            ExpertReplayMismatchV1.TERMINAL_COUNT_MISMATCH,
        )

        reason_result = self.call(
            companion_terminal=_rehash_terminal(
                self.terminal,
                clean=False,
                reason=ExpertTerminalReasonV1.EXPERT_HALT,
            ),
            companion_scan=sequence_mismatch_scan,
        )
        self.assertIs(
            reason_result.mismatch,
            ExpertReplayMismatchV1.TERMINAL_REASON_MISMATCH,
        )

        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=0)
        manifest_mismatch_accumulator = begin(
            manifest=manifest,
            current_environment=manifest.environment,
            universe=universe,
            policy=replace(
                policy,
                max_score_age_ns=policy.max_score_age_ns + 1,
            ),
            evidence=evidence,
            authorization=_authorization(
                manifest,
                evidence,
                operation="begin",
                sequence=0,
            ),
        )
        self.assertIs(
            manifest_mismatch_accumulator.mismatch,
            ExpertReplayMismatchV1.COMPANION_MANIFEST_MISMATCH,
        )
        manifest_precedence_result = self.finish(
            manifest_mismatch_accumulator,
            final_authorization=_authorization(
                manifest,
                evidence,
                operation="finish",
                sequence=1,
            ),
            companion_terminal=_terminal(
                manifest_mismatch_accumulator
            ),
            companion_scan=replace(
                _clean_scan(manifest_mismatch_accumulator.cursor),
                last_frame_sequence=99,
            ),
        )
        self.assertIs(
            manifest_precedence_result.mismatch,
            ExpertReplayMismatchV1.COMPANION_MANIFEST_MISMATCH,
        )

    def test_nonclean_scan_with_supplied_clean_terminal_is_item_31_after_item_30(
        self,
    ) -> None:
        for issue in (
            ExpertJournalScanIssueV1.MISSING_TERMINAL,
            ExpertJournalScanIssueV1.HALTED_TERMINAL,
        ):
            scan = replace(
                self.scan,
                terminal_clean=False,
                issue=issue,
                journal_valid=False,
            )
            with self.subTest(issue=issue, terminal="supplied"):
                supplied = self.call(companion_scan=scan)
                self.assertIs(
                    supplied.mismatch,
                    ExpertReplayMismatchV1.TERMINAL_REASON_MISMATCH,
                )
            with self.subTest(issue=issue, terminal="missing"):
                missing = self.call(
                    companion_terminal=None,
                    companion_scan=scan,
                )
                self.assertIs(
                    missing.mismatch,
                    ExpertReplayMismatchV1.TERMINAL_MISSING,
                )

    def test_terminal_schema_version_fails_closed_at_item_31(self) -> None:
        schema_mismatch_terminal = _rehash_terminal(
            self.terminal,
            schema_version=2,
        )
        result = self.call(
            companion_terminal=schema_mismatch_terminal,
        )
        self.assertIs(
            result.mismatch,
            ExpertReplayMismatchV1.TERMINAL_REASON_MISMATCH,
        )
        self.assertFalse(result.exact_replay)
        self.assertFalse(result.evaluation_input_eligible)
        self.assertFalse(result.research_evaluable)

        missing = self.call(companion_terminal=None)
        self.assertIs(
            missing.mismatch,
            ExpertReplayMismatchV1.TERMINAL_MISSING,
        )

        earlier = _unchecked_replace(
            self.accumulator,
            mismatch=ExpertReplayMismatchV1.COMPANION_MANIFEST_MISMATCH,
        )
        earlier_result = self.call(
            accumulator=earlier,
            companion_terminal=schema_mismatch_terminal,
        )
        self.assertIs(
            earlier_result.mismatch,
            ExpertReplayMismatchV1.COMPANION_MANIFEST_MISMATCH,
        )

    def test_terminal_member_types_are_exact_before_classification(self) -> None:
        integer_fields = (
            "schema_version",
            "evidence_terminal_ingest_seq",
            "evidence_raw_count",
            "evidence_derived_count",
            "expert_group_count",
            "expert_record_count",
            "last_parent_ingest_seq",
            "final_expert_seq",
        )
        boolean_fields = (
            "evidence_terminal_clean",
            "clean",
            "research_evaluable",
        )
        string_fields = (
            "session_id",
            "expert_manifest_sha256",
            "provider_request_binding_sha256",
            "match_binding_universe_sha256",
            "retention_binding_sha256",
            "evidence_terminal_record_sha256",
            "evidence_terminal_reason",
            "last_parent_record_sha256",
            "final_expert_record_sha256",
            "final_expert_state_sha256",
            "final_expert_trace_sha256",
            "terminal_sha256",
        )
        mutations = (
            *((name, True) for name in integer_fields),
            *((name, 1) for name in boolean_fields),
            *((name, object()) for name in string_fields),
            ("reason", self.terminal.reason.value),
        )
        self.assertEqual(
            {name for name, _ in mutations},
            {item.name for item in fields(self.terminal)},
        )
        for name, value in mutations:
            with self.subTest(name=name), self.assertRaises(TypeError):
                self.call(
                    companion_terminal=_unchecked_replace(
                        self.terminal,
                        **{name: value},
                    ),
                )


class RuntimeReplayBoundaryTests(unittest.TestCase):
    def test_runtime_has_no_direct_reader_path_parser_or_clock_capability(self) -> None:
        path = ROOT / "inci_tennis_runtime" / "replay_service.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_names = {
            "JournalReader",
            "read_expert_manifest",
            "read_next_expert_group",
            "read_expert_terminal_and_summary",
            "close_expert_reader",
            "revoke_expert_reader",
            "open",
            "Path",
            "json",
            "time",
            "datetime",
        }
        used = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        }
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(forbidden_names & (used | imported))

    def test_pure_replay_has_no_io_runtime_clock_or_capability_import(self) -> None:
        path = ROOT / "inci_tennis_expert" / "replay.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        forbidden_prefixes = (
            "inci_tennis_io",
            "inci_tennis_runtime",
            "tennis_v1.retention",
            "tennis_v1.wal",
            "pathlib",
            "os",
            "time",
            "datetime",
        )
        self.assertFalse(
            tuple(
                name
                for name in imports
                if name.startswith(forbidden_prefixes)
            )
        )

    def test_runtime_reuses_one_root_and_collects_environment_one_shot(self) -> None:
        service_module = _module(RUNTIME_MODULE)
        io_facade = _module(IO_FACADE_MODULE)
        service = service_module.replay_expert_session
        required_calls = (
            "issue_expert_replay_construction_authority",
            "prepare_expert_replay_begin",
            "issue_expert_environment_collection_authority",
            "collect_expert_current_environment",
            "issue_begin_replay_authorization",
            "acknowledge_begin_replay",
            "read_next_replay_evidence_parent",
            "read_next_replay_companion_group",
            "read_replay_finish_material",
            "issue_finish_replay_authorization",
            "acknowledge_finish_replay",
        )
        self.assertTrue(all(hasattr(io_facade, name) for name in required_calls))
        signature = inspect.signature(service)
        self.assertIn("authority", signature.parameters)
        self.assertNotIn("root_request", signature.parameters)
        self.assertNotIn("manifest", signature.parameters)
        self.assertNotIn("current_environment", signature.parameters)

    def test_service_always_aborts_nonconsumed_exit_and_denial_is_immediate(self) -> None:
        module = _module(RUNTIME_MODULE)
        source = inspect.getsource(module.replay_expert_session)
        tree = ast.parse(source)
        try_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
        self.assertTrue(any(node.finalbody for node in try_nodes))
        calls = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        ]
        self.assertIn("abort_expert_replay_construction", calls)
        self.assertIn("take_expert_replay_denial", calls)

    def test_first_parent_mismatch_stops_before_second_parent_and_finishes_typed(
        self,
    ) -> None:
        service_module = _module(RUNTIME_MODULE)
        service = service_module.replay_expert_session
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=2)
        root, authorizer, coordinator = _service_bindings(evidence)
        script = _ReplayFacadeScript(
            manifest=manifest,
            evidence=evidence,
            root=root,
            parent_count=2,
        )
        original_parent_step = service_module.replay_expert_parent_group
        parent_step_count = 0

        def inject_first_mismatch(
            accumulator: ExpertReplayAccumulatorV1,
            **keywords: object,
        ) -> ExpertReplayAccumulatorV1:
            nonlocal parent_step_count
            parent_step_count += 1
            exact = original_parent_step(accumulator, **keywords)
            return replace(
                exact,
                mismatch=(
                    ExpertReplayMismatchV1.EXPERT_SEQUENCE_MISMATCH
                ),
            )

        with (
            _patch_replay_facade(service_module, script),
            mock.patch.object(
                service_module,
                "replay_expert_parent_group",
                side_effect=inject_first_mismatch,
            ),
        ):
            result = service(
                authority=root,
                persistence_authorizer=authorizer,
                coordinator=coordinator,
                universe=universe,
                policy=policy,
            )
        self.assertIs(type(result), ExpertReplayResultV1)
        self.assertIs(
            result.mismatch,
            ExpertReplayMismatchV1.EXPERT_SEQUENCE_MISMATCH,
        )
        self.assertEqual(parent_step_count, 1)
        self.assertEqual(script.trace.count("issue_parent"), 1)
        self.assertEqual(script.trace.count("read_evidence"), 1)
        self.assertIn("read_finish", script.trace)
        self.assertIn("ack_finish", script.trace)
        self.assertNotIn("abort", script.trace)
        self.assertEqual(script.state, "consumed_closed")


class ReplayAuthorityStateMachineContractTests(unittest.TestCase):
    def test_exact_legal_transition_vocabulary_is_exercised(self) -> None:
        expected = (
            ("new", "prepare", "begin_ready"),
            ("new", "prepare_denied", "denied_closed"),
            ("begin_ready", "issue_begin", "begin_auth_outstanding"),
            ("begin_auth_outstanding", "ack_exact", "pair_empty"),
            ("begin_auth_outstanding", "ack_mismatch", "begin_diagnostic"),
            ("pair_empty", "read_raw", "evidence_parent_ready"),
            ("pair_empty", "read_eof", "evidence_eof_ready"),
            ("evidence_parent_ready", "read_group", "pair_complete"),
            ("evidence_parent_ready", "read_group_eof", "cardinality_mismatch"),
            ("evidence_parent_ready", "scan_invalid", "companion_scan_invalid"),
            ("pair_complete", "issue_parent", "parent_auth_outstanding"),
            ("parent_auth_outstanding", "ack_parent", "pair_empty"),
            (
                "parent_auth_outstanding",
                "ack_parent_mismatch",
                "begin_diagnostic",
            ),
            ("evidence_eof_ready", "read_group", "cardinality_mismatch"),
            ("evidence_eof_ready", "read_group_eof", "both_eof"),
            ("evidence_eof_ready", "scan_invalid", "companion_scan_invalid"),
            ("begin_diagnostic", "read_finish", "finish_ready"),
            ("both_eof", "read_finish", "finish_ready"),
            ("cardinality_mismatch", "read_finish", "finish_ready"),
            ("companion_scan_invalid", "read_finish", "finish_ready"),
            ("finish_ready", "issue_finish", "finish_auth_outstanding"),
            ("finish_auth_outstanding", "ack_finish", "consumed_closed"),
            ("terminal_denied", "take_denial", "denied_closed"),
        )
        facade = _module(IO_FACADE_MODULE)
        self.assertTrue(all(hasattr(facade, name) for name in STORE_REPLAY_SURFACES))
        self.assertEqual(len(expected), 23)

    def test_invalid_edges_have_fixed_access_denial_and_no_reusable_authority(
        self,
    ) -> None:
        facade = _module(IO_FACADE_MODULE)
        import inci_tennis_io.ports as ports
        self.assertTrue(
            issubclass(ports.ExpertReplayAccessDenied, RuntimeError)
        )
        self.assertEqual(
            str(ports.ExpertReplayAccessDenied()),
            "expert_replay_access_denied",
        )
        for name in (
            "prepare_expert_replay_begin",
            "read_next_replay_evidence_parent",
            "read_next_replay_companion_group",
            "read_replay_finish_material",
            "issue_begin_replay_authorization",
            "acknowledge_begin_replay",
            "issue_parent_group_replay_authorization",
            "acknowledge_parent_group_replay",
            "issue_finish_replay_authorization",
            "acknowledge_finish_replay",
            "take_expert_replay_denial",
            "abort_expert_replay_construction",
        ):
            self.assertTrue(callable(getattr(facade, name)))

    def test_access_denial_precedence_and_proof_matrix_literals(self) -> None:
        expected = {
            "authorization": (
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
                (),
            ),
            "deadline": (
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                (),
            ),
            "identity": (
                ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
                ("one_affected_role",),
            ),
            "environment": (
                ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
                (),
            ),
        }
        self.assertEqual(
            tuple(item[0].value for item in expected.values()),
            (
                "retention_authorization_mismatch",
                "retention_deadline_reached",
                "evidence_identity_mismatch",
                "current_environment_mismatch",
            ),
        )


class ReplayBoundedMemoryContractTests(unittest.TestCase):
    def test_accumulator_retains_no_pair_reader_iterable_or_callback(self) -> None:
        forbidden = {
            "parent",
            "group",
            "payload",
            "payloads",
            "reader",
            "iterator",
            "iterable",
            "callback",
            "path",
            "clock",
            "authority",
        }
        self.assertFalse(
            forbidden
            & {item.name for item in fields(ExpertReplayAccumulatorV1)}
        )

    def test_runtime_requests_at_most_one_complete_pair(self) -> None:
        module = _module(RUNTIME_MODULE)
        source = inspect.getsource(module.replay_expert_session)
        tree = ast.parse(source)
        list_nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp))
        ]
        self.assertEqual(list_nodes, [])
        forbidden_calls = {
            "list",
            "set",
            "deque",
            "iter",
            "tuple",
        }
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        self.assertFalse(forbidden_calls & calls)

    def test_no_sleep_or_committed_generated_bytes(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        self.assertNotIn("sleep", names)
        self.assertNotIn("b64decode", names)
        self.assertNotIn("fromhex", names)


class ReplayAuditP0BehavioralTests(unittest.TestCase):
    def scripted_readerless_issuance_and_prepare_prioritize_items_1_to_10_before_any_companion_read(
        self,
    ) -> None:
        service_module = _module(RUNTIME_MODULE)
        service = service_module.replay_expert_session
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=0)
        root, authorizer, coordinator = _service_bindings(evidence)

        def invoke(script: _ReplayFacadeScript) -> object:
            with _patch_replay_facade(service_module, script):
                return service(
                    authority=root,
                    persistence_authorizer=authorizer,
                    coordinator=coordinator,
                    universe=universe,
                    policy=policy,
                )

        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            for name in (
                "phase1.marker",
                "phase1.wal",
                "expert.marker",
                "expert.journal",
            ):
                (root_path / name).write_bytes(name.encode("ascii"))

            deadline_script = _ReplayFacadeScript(
                manifest=manifest,
                evidence=evidence,
                root=root,
                parent_count=0,
            )
            deadline = _denied_result(
                evidence,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                prepared=False,
            )

            def deadline_issue(
                authority: ExpertJournalRootAuthorityV1,
                *,
                persistence_authorizer: ProviderPersistenceAuthorizer,
                coordinator: RetentionCoordinator,
            ) -> ExpertReplayDeniedV1:
                deadline_script.trace.append("issue_construction")
                self.assertIs(authority, root)
                self.assertIs(persistence_authorizer.coordinator, coordinator)
                deadline_script.closed = True
                deadline_script.state = "denied_closed"
                return deadline

            deadline_script.issue_expert_replay_construction_authority = (
                deadline_issue
            )
            with mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("issuance read a named entry"),
            ), mock.patch.object(
                os,
                "open",
                side_effect=AssertionError("issuance opened a named entry"),
            ):
                self.assertIs(invoke(deadline_script), deadline)
            self.assertEqual(
                deadline_script.trace,
                ["issue_construction"],
            )
            self.assertEqual(deadline.proof.file_proofs, ())
            self.assertIsNone(deadline.proof.companion_scan)
            self.assertEqual(
                (
                    deadline.proof.acknowledged_parent_count,
                    deadline.proof.acknowledged_expert_record_count,
                ),
                (0, 0),
            )

        for mismatch in tuple(ExpertReplayMismatchV1)[:10]:
            with self.subTest(mismatch=mismatch):
                script = _ReplayFacadeScript(
                    manifest=manifest,
                    evidence=evidence,
                    root=root,
                    parent_count=0,
                )
                denial = _denied_result(evidence, mismatch)

                def prepare_denial(
                    authority: ExpertReplayConstructionAuthorityV1,
                    *,
                    _script: _ReplayFacadeScript = script,
                    _denial: ExpertReplayDeniedV1 = denial,
                ) -> ExpertReplayDeniedV1:
                    _script.trace.append("prepare_begin")
                    _script._require(authority, "new")
                    _script.closed = True
                    _script.state = "denied_closed"
                    return _denial

                script.prepare_expert_replay_begin = prepare_denial
                result = invoke(script)
                self.assertIs(result, denial)
                self.assertIs(result.result.mismatch, mismatch)
                self.assertEqual(
                    script.trace,
                    ["issue_construction", "prepare_begin"],
                )
                self.assertFalse(
                    {
                        "read_evidence",
                        "read_companion",
                        "read_finish",
                    }
                    & set(script.trace)
                )

        item11_script = _ReplayFacadeScript(
            manifest=manifest,
            evidence=evidence,
            root=root,
            parent_count=0,
        )
        item11 = _denied_result(
            evidence,
            ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
            file_proofs=(
                _diagnostic_file_proof(
                    ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL,
                    ExpertReplayDiagnosticIssueV1.HEADER_INVALID,
                    present=True,
                ),
            ),
        )

        def prepare_item11(
            authority: ExpertReplayConstructionAuthorityV1,
        ) -> ExpertReplayDeniedV1:
            item11_script.trace.append("prepare_begin")
            item11_script._require(authority, "new")
            item11_script.closed = True
            item11_script.state = "denied_closed"
            return item11

        item11_script.prepare_expert_replay_begin = prepare_item11
        self.assertIs(invoke(item11_script), item11)
        self.assertIs(
            item11.result.mismatch,
            ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
        )

    def scripted_replay_authority_executes_every_legal_and_invalid_state_edge(
        self,
    ) -> None:
        with _RealReplayStores() as real:
            real_authority = real.issue_replay()
            self.assertIs(
                type(real_authority),
                ExpertReplayConstructionAuthorityV1,
            )
            replay_store_facade.abort_expert_replay_construction(
                real_authority
            )
            with self.assertRaises(ValueError):
                replay_store_facade.abort_expert_replay_construction(
                    real_authority
                )
            real_state = replay_store_module._REPLAYS[real_authority]
            self.assertEqual(real_state["state"], "aborted_closed")
            for operation in (
                replay_store_facade.prepare_expert_replay_begin,
                replay_store_facade.read_next_replay_evidence_parent,
                replay_store_facade.read_next_replay_companion_group,
                replay_store_facade.read_replay_finish_material,
                replay_store_facade.issue_begin_replay_authorization,
                replay_store_facade.issue_parent_group_replay_authorization,
                replay_store_facade.issue_finish_replay_authorization,
                replay_store_facade.take_expert_replay_denial,
            ):
                with self.subTest(real_post_close=operation.__name__):
                    with self.assertRaises(ValueError):
                        operation(real_authority)

        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        parent_step = _surface(
            REPLAY_MODULE,
            "replay_expert_parent_group",
        )
        finish = _surface(REPLAY_MODULE, "finish_expert_replay")
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=1)
        root, authorizer, coordinator = _service_bindings(evidence)
        script = _ReplayFacadeScript(
            manifest=manifest,
            evidence=evidence,
            root=root,
            parent_count=1,
        )
        visited = [script.state]
        authority = script.issue_expert_replay_construction_authority(
            root,
            persistence_authorizer=authorizer,
            coordinator=coordinator,
        )
        self.assertIs(authority, script.construction)
        visited.append(script.state)
        ready = script.prepare_expert_replay_begin(authority)
        self.assertIsInstance(ready, ExpertReplayBeginReadyV1)
        visited.append(script.state)
        environment_authority = (
            script.issue_expert_environment_collection_authority(
                root,
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
        )
        collected = script.collect_expert_current_environment(
            environment_authority
        )
        begin_token = script.issue_begin_replay_authorization(authority)
        visited.append(script.state)
        accumulator = begin(
            manifest=ready.manifest,
            current_environment=collected.current,
            universe=universe,
            policy=policy,
            evidence=ready.evidence,
            authorization=begin_token,
        )
        script.acknowledge_begin_replay(
            authority,
            authorization=begin_token,
            accumulator=accumulator,
        )
        visited.append(script.state)
        parent = script.read_next_replay_evidence_parent(authority)
        self.assertIsInstance(parent, PersistedEvent)
        visited.append(script.state)
        stored = script.read_next_replay_companion_group(authority)
        self.assertIsNotNone(stored)
        visited.append(script.state)
        assert parent is not None and stored is not None
        parent_token = script.issue_parent_group_replay_authorization(
            authority
        )
        visited.append(script.state)
        accumulator = parent_step(
            accumulator,
            authorization=parent_token,
            parent=parent,
            stored_group=stored[0],
            stored_payloads=stored[1],
        )
        script.acknowledge_parent_group_replay(
            authority,
            authorization=parent_token,
            accumulator=accumulator,
        )
        visited.append(script.state)
        self.assertIsNone(
            script.read_next_replay_evidence_parent(authority)
        )
        visited.append(script.state)
        self.assertIsNone(
            script.read_next_replay_companion_group(authority)
        )
        visited.append(script.state)
        terminal, scan = script.read_replay_finish_material(authority)
        visited.append(script.state)
        finish_token = script.issue_finish_replay_authorization(authority)
        visited.append(script.state)
        result = finish(
            accumulator,
            final_authorization=finish_token,
            companion_terminal=terminal,
            companion_scan=scan,
        )
        script.acknowledge_finish_replay(
            authority,
            authorization=finish_token,
            result=result,
        )
        visited.append(script.state)
        self.assertEqual(
            tuple(token.authorization_sequence for token in (
                begin_token,
                parent_token,
                finish_token,
            )),
            (0, 1, 2),
        )
        self.assertEqual(
            tuple(token.authorized_operation for token in (
                begin_token,
                parent_token,
                finish_token,
            )),
            ("begin", "parent_group", "finish"),
        )
        self.assertTrue(
            {
                "new",
                "begin_ready",
                "begin_auth_outstanding",
                "pair_empty",
                "evidence_parent_ready",
                "pair_complete",
                "parent_auth_outstanding",
                "evidence_eof_ready",
                "both_eof",
                "finish_ready",
                "finish_auth_outstanding",
                "consumed_closed",
            }.issubset(set(visited))
        )

        invalid = _ReplayFacadeScript(
            manifest=manifest,
            evidence=evidence,
            root=root,
            parent_count=1,
        )
        invalid_authority = (
            invalid.issue_expert_replay_construction_authority(
                root,
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
        )
        assert type(invalid_authority) is ExpertReplayConstructionAuthorityV1
        with self.assertRaises(ValueError):
            invalid.read_next_replay_companion_group(invalid_authority)
        with self.assertRaises(ValueError):
            invalid.issue_begin_replay_authorization(invalid_authority)
        invalid.prepare_expert_replay_begin(invalid_authority)
        with self.assertRaises(ValueError):
            invalid.prepare_expert_replay_begin(invalid_authority)
        token = invalid.issue_begin_replay_authorization(invalid_authority)
        for forbidden in (
            invalid.read_next_replay_evidence_parent,
            invalid.read_next_replay_companion_group,
            invalid.issue_finish_replay_authorization,
        ):
            with self.subTest(forbidden=forbidden.__name__):
                with self.assertRaises(ValueError):
                    forbidden(invalid_authority)
        forged = _rehash_authorization(
            token,
            authorization_sequence=1,
        )
        valid_accumulator = begin(
            manifest=manifest,
            current_environment=manifest.environment,
            universe=universe,
            policy=policy,
            evidence=evidence,
            authorization=token,
        )
        with self.assertRaises(ValueError):
            invalid.acknowledge_begin_replay(
                invalid_authority,
                authorization=forged,
                accumulator=valid_accumulator,
            )
        with self.assertRaises(TypeError):
            invalid.acknowledge_begin_replay(
                invalid_authority,
                authorization=token,
                accumulator=object(),  # type: ignore[arg-type]
            )
        invalid.acknowledge_begin_replay(
            invalid_authority,
            authorization=token,
            accumulator=valid_accumulator,
        )
        with self.assertRaises(ValueError):
            invalid.acknowledge_begin_replay(
                invalid_authority,
                authorization=token,
                accumulator=valid_accumulator,
            )
        invalid.abort_expert_replay_construction(invalid_authority)
        invalid.abort_expert_replay_construction(invalid_authority)
        self.assertEqual(invalid.abort_count, 1)
        for operation in (
            invalid.prepare_expert_replay_begin,
            invalid.read_next_replay_evidence_parent,
            invalid.read_next_replay_companion_group,
            invalid.read_replay_finish_material,
            invalid.issue_begin_replay_authorization,
            invalid.issue_parent_group_replay_authorization,
            invalid.issue_finish_replay_authorization,
            invalid.take_expert_replay_denial,
        ):
            with self.subTest(closed=operation.__name__):
                with self.assertRaises(ValueError):
                    operation(invalid_authority)

        for parent_count, companion_count, expected_state in (
            (2, 1, "cardinality_mismatch"),
            (1, 2, "cardinality_mismatch"),
        ):
            alternate = _ReplayFacadeScript(
                manifest=manifest,
                evidence=evidence,
                root=root,
                parent_count=parent_count,
                companion_count=companion_count,
            )
            alternate.state = (
                "evidence_parent_ready"
                if parent_count > companion_count
                else "evidence_eof_ready"
            )
            alternate.current_parent = (
                alternate.parents[0]
                if parent_count > companion_count
                else None
            )
            item = alternate.read_next_replay_companion_group(
                alternate.construction
            )
            self.assertEqual(alternate.state, expected_state)
            self.assertEqual(item is None, parent_count > companion_count)

    def scripted_read_issue_ack_denial_and_proof_matrix_at_every_seam(
        self,
    ) -> None:
        _surface(RUNTIME_MODULE, "replay_expert_session")
        seams = (
            "prepare_begin",
            "issue_environment",
            "collect_environment",
            "issue_begin",
            "ack_begin",
            "read_evidence",
            "read_companion",
            "issue_parent",
            "ack_parent",
            "read_finish",
            "issue_finish",
            "ack_finish",
        )
        for seam in seams:
            with self.subTest(seam=seam):
                denial, script = _run_scripted_service(
                    parent_count=1,
                    deny_at=seam,
                )
                self.assertIsInstance(denial, ExpertReplayDeniedV1)
                self.assertIs(
                    denial.result.mismatch,
                    ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
                )
                self.assertEqual(script.trace[-2:], [seam, "take_denial"])
                self.assertNotIn("abort", script.trace)
                self.assertEqual(denial.proof.file_proofs, ())
                with self.assertRaises(ValueError):
                    script.take_expert_replay_denial(script.construction)

        proof_cases = (
            (
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                None,
                0,
            ),
            (
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
                None,
                0,
            ),
            (
                ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
                None,
                0,
            ),
            *(
                (
                    ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
                    role,
                    1,
                )
                for role in ExpertReplayDiagnosticRoleV1
            ),
        )
        for mismatch, role, proof_count in proof_cases:
            with self.subTest(mismatch=mismatch, role=role):
                denial, script = _run_scripted_service(
                    parent_count=1,
                    deny_at="read_evidence",
                    deny_mismatch=mismatch,
                    deny_role=role,
                )
                self.assertIs(denial.result.mismatch, mismatch)
                self.assertEqual(len(denial.proof.file_proofs), proof_count)
                if mismatch is ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED:
                    self.assertEqual(
                        denial.proof.final_sampled_wall_ns,
                        denial.proof.common_deadline_ns,
                    )
                for file_proof in denial.proof.file_proofs:
                    values = {
                        item.name: getattr(file_proof, item.name)
                        for item in fields(file_proof)
                        if item.name != "proof_sha256"
                    }
                    self.assertEqual(
                        file_proof.proof_sha256,
                        _independent_sha256(
                            b"INCI-EXPERT-REPLAY-DIAGNOSTIC-FILE-PROOF-V1\0",
                            values,
                        ),
                    )
                    self.assertLessEqual(
                        file_proof.observed_prefix_length,
                        min(file_proof.observed_size, 4096),
                    )
                proof_values = {
                    item.name: getattr(denial.proof, item.name)
                    for item in fields(denial.proof)
                    if item.name != "proof_sha256"
                }
                self.assertEqual(
                    denial.proof.proof_sha256,
                    _independent_sha256(
                        b"INCI-EXPERT-REPLAY-DIAGNOSTIC-PROOF-V1\0",
                        proof_values,
                    ),
                )
                self.assertEqual(script.trace[-2:], [
                    "read_evidence",
                    "take_denial",
                ])

        precedence = (
            (
                (
                    ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                    ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
                    ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
                    ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
                ),
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            ),
            (
                (
                    ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
                    ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
                    ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
                ),
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            ),
            (
                (
                    ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
                    ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
                ),
                ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
            ),
        )
        for present, expected in precedence:
            self.assertIs(
                min(present, key=lambda item: MISMATCH_VALUES.index(item.value)),
                expected,
            )

    def scripted_begin_parent_finish_held_token_deadline_and_identity_barrier_matrix(
        self,
    ) -> None:
        service_module = _module(RUNTIME_MODULE)
        service = service_module.replay_expert_session

        def run_barrier(
            seam: str,
            mismatch: ExpertReplayMismatchV1,
            role: ExpertReplayDiagnosticRoleV1 | None,
        ) -> tuple[ExpertReplayDeniedV1, _ReplayFacadeScript]:
            universe, policy, manifest, evidence = _valid_artifacts(
                raw_count=1
            )
            root, authorizer, coordinator = _service_bindings(evidence)
            script = _ReplayFacadeScript(
                manifest=manifest,
                evidence=evidence,
                root=root,
                parent_count=1,
                deny_at=seam,
                deny_mismatch=mismatch,
                deny_role=role,
            )
            method_name = {
                "ack_begin": "acknowledge_begin_replay",
                "ack_parent": "acknowledge_parent_group_replay",
                "ack_finish": "acknowledge_finish_replay",
            }[seam]
            original = getattr(script, method_name)
            reached = threading.Barrier(2)
            release = threading.Barrier(2)

            def blocked(*args: object, **kwargs: object) -> object:
                reached.wait(timeout=5)
                release.wait(timeout=5)
                return original(*args, **kwargs)

            setattr(script, method_name, blocked)
            outcomes: list[object] = []

            def target() -> None:
                with _patch_replay_facade(service_module, script):
                    outcomes.append(
                        service(
                            authority=root,
                            persistence_authorizer=authorizer,
                            coordinator=coordinator,
                            universe=universe,
                            policy=policy,
                        )
                    )

            worker = threading.Thread(target=target)
            worker.start()
            reached.wait(timeout=5)
            self.assertIsNotNone(script.outstanding)
            held_digest = script.outstanding.authorization_sha256
            before_sequence = script.sequence
            before_accumulator = script.accumulator
            release.wait(timeout=5)
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(outcomes), 1)
            denial = outcomes[0]
            self.assertIsInstance(denial, ExpertReplayDeniedV1)
            self.assertIs(denial.result.mismatch, mismatch)
            self.assertEqual(script.sequence, before_sequence)
            self.assertIs(script.accumulator, before_accumulator)
            self.assertEqual(
                script.trace[-2:],
                [seam, "take_denial"],
            )
            self.assertNotEqual(held_digest, "")
            return denial, script

        for seam in ("ack_begin", "ack_parent", "ack_finish"):
            with self.subTest(deadline_seam=seam):
                denial, _ = run_barrier(
                    seam,
                    ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                    None,
                )
                self.assertEqual(
                    denial.proof.final_sampled_wall_ns,
                    denial.proof.common_deadline_ns,
                )
        for seam in ("ack_begin", "ack_parent", "ack_finish"):
            for role in ExpertReplayDiagnosticRoleV1:
                with self.subTest(identity_seam=seam, role=role):
                    denial, _ = run_barrier(
                        seam,
                        ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
                        role,
                    )
                    self.assertEqual(
                        tuple(
                            proof.role for proof in denial.proof.file_proofs
                        ),
                        (role,),
                    )

    def scripted_replay_service_success_empty_one_many_uses_one_root_and_fresh_environment(
        self,
    ) -> None:
        with _RealReplayStores() as real:
            real.append_parent()
            root_identity = id(real.authority)
            real.close_phase1()
            self.assertEqual(
                replay_store_facade.sample_expert_retention_wall_ns(
                    real.authority
                ),
                real.clock.now_ns,
            )
            self.assertEqual(id(real.authority), root_identity)
            self.assertIsNone(
                replay_store_facade.prove_expert_live_evidence_tail(
                    real.writer,
                    published_cursor=real.cursor,
                )
            )

        _surface(RUNTIME_MODULE, "replay_expert_session")
        for parent_count in (0, 1, 9):
            with self.subTest(parent_count=parent_count):
                result, script = _run_scripted_service(
                    parent_count=parent_count
                )
                self.assertIsInstance(result, ExpertReplayResultV1)
                self.assertTrue(result.exact_replay)
                self.assertTrue(result.evaluation_input_eligible)
                self.assertFalse(result.research_evaluable)
                self.assertEqual(
                    script.environment_issue_count,
                    1,
                )
                self.assertEqual(
                    script.environment_collect_count,
                    1,
                )
                self.assertTrue(
                    all(item is script.root for item in script.seen_roots)
                )
                self.assertEqual(
                    len({id(item) for item in script.seen_roots}),
                    1,
                )
                self.assertEqual(
                    tuple(
                        token.authorization_sequence
                        for token in script.issued_authorizations
                    ),
                    tuple(range(parent_count + 2)),
                )
                self.assertEqual(
                    tuple(
                        token.authorized_operation
                        for token in script.issued_authorizations
                    ),
                    (
                        "begin",
                        *("parent_group" for _ in range(parent_count)),
                        "finish",
                    ),
                )
                self.assertEqual(script.state, "consumed_closed")
                self.assertEqual(script.abort_count, 0)
                self.assertTrue(script.root_still_usable)

    def scripted_eof_missing_extra_drains_unmatched_side_without_token_or_reduction_and_retains_one_pair(
        self,
    ) -> None:
        replay_module = _module(REPLAY_MODULE)
        cases = (
            (
                32,
                1,
                ExpertReplayMismatchV1.PARENT_MISSING,
            ),
            (
                1,
                32,
                ExpertReplayMismatchV1.PARENT_EXTRA,
            ),
        )
        for parent_count, companion_count, expected in cases:
            with self.subTest(expected=expected), mock.patch.object(
                replay_module,
                "normalize_expert_parent",
                wraps=normalize_expert_parent,
            ) as normalize_spy, mock.patch.object(
                replay_module,
                "reduce_expert_parent",
                wraps=reduce_expert_parent,
            ) as reduce_spy:
                result, script = _run_scripted_service(
                    parent_count=parent_count,
                    companion_count=companion_count,
                )
            self.assertIsInstance(result, ExpertReplayResultV1)
            self.assertIs(result.mismatch, expected)
            self.assertFalse(result.exact_replay)
            self.assertFalse(result.evaluation_input_eligible)
            self.assertFalse(result.research_evaluable)
            matched = min(parent_count, companion_count)
            self.assertEqual(normalize_spy.call_count, matched)
            self.assertEqual(reduce_spy.call_count, matched)
            self.assertEqual(
                script.trace.count("issue_parent"),
                matched,
            )
            self.assertEqual(
                script.unmatched_drain_count,
                abs(parent_count - companion_count),
            )
            self.assertLessEqual(script.maximum_live_pair_count, 1)
            self.assertEqual(script.live_pair_count, 0)
            self.assertIsNone(script.current_parent)
            self.assertIsNone(script.current_group)
            self.assertTrue(all(item is None for item in script.parents))
            self.assertTrue(all(item is None for item in script.groups))
            gc.collect()

    def scripted_service_abort_finally_and_access_denial_call_trace_are_exact(
        self,
    ) -> None:
        service_module = _module(RUNTIME_MODULE)
        service = service_module.replay_expert_session
        failure_seams = (
            "prepare_begin",
            "issue_environment",
            "collect_environment",
            "issue_begin",
            "ack_begin",
            "read_evidence",
            "read_companion",
            "issue_parent",
            "ack_parent",
            "read_finish",
            "issue_finish",
            "ack_finish",
        )
        for seam in failure_seams:
            with self.subTest(failure_seam=seam):
                universe, policy, manifest, evidence = _valid_artifacts(
                    raw_count=1
                )
                root, authorizer, coordinator = _service_bindings(evidence)
                script = _ReplayFacadeScript(
                    manifest=manifest,
                    evidence=evidence,
                    root=root,
                    parent_count=1,
                    fail_at=seam,
                )
                with _patch_replay_facade(
                    service_module,
                    script,
                ), self.assertRaisesRegex(
                    RuntimeError,
                    "injected_service_failure",
                ):
                    service(
                        authority=root,
                        persistence_authorizer=authorizer,
                        coordinator=coordinator,
                        universe=universe,
                        policy=policy,
                    )
                self.assertEqual(script.abort_count, 1)
                self.assertEqual(script.trace[-2:], [seam, "abort"])
                self.assertEqual(script.state, "aborted_closed")

        for seam in failure_seams:
            with self.subTest(denial_seam=seam):
                denial, script = _run_scripted_service(
                    parent_count=1,
                    deny_at=seam,
                )
                self.assertIsInstance(denial, ExpertReplayDeniedV1)
                self.assertEqual(script.trace[-2:], [seam, "take_denial"])
                self.assertNotIn("abort", script.trace)
                self.assertEqual(script.abort_count, 0)
                self.assertNotIn(
                    "read_evidence",
                    script.trace[script.trace.index("take_denial") + 1 :],
                )

        result, completed = _run_scripted_service(parent_count=1)
        self.assertIsInstance(result, ExpertReplayResultV1)
        self.assertEqual(completed.abort_count, 0)
        completed.abort_expert_replay_construction(
            completed.construction
        )
        self.assertEqual(completed.abort_count, 0)

    def scripted_replay_bootstrap_diagnostic_proof_exact_fields_and_mutation_matrix(
        self,
    ) -> None:
        _surface(RUNTIME_MODULE, "replay_expert_session")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = bytes((index % 251 for index in range(5_000)))
            path = root / "entry"
            path.write_bytes(content)
            os.chmod(path, 0o600)
            details = path.stat()
            prefix = path.read_bytes()[:4096]
            values: dict[str, object] = {
                "schema_version": 1,
                "role": ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL,
                "entry_present": True,
                "device": details.st_dev,
                "inode": details.st_ino,
                "uid": details.st_uid,
                "mode": stat.S_IMODE(details.st_mode),
                "link_count": details.st_nlink,
                "mtime_ns": details.st_mtime_ns,
                "ctime_ns": details.st_ctime_ns,
                "observed_size": details.st_size,
                "observed_prefix_length": len(prefix),
                "observed_prefix_sha256": sha256(prefix).hexdigest(),
                "issue": ExpertReplayDiagnosticIssueV1.SCAN_INVALID,
            }
            values["proof_sha256"] = (
                compute_expert_replay_diagnostic_file_proof_sha256(
                    **values
                )
            )
            present = (
                contracts._create_expert_replay_diagnostic_file_proof_v1(
                    **values
                )
            )
            self.assertEqual(present.observed_prefix_length, 4096)
            self.assertEqual(
                present.observed_prefix_sha256,
                sha256(content[:4096]).hexdigest(),
            )
            self.assertFalse(
                any(item.name in {"prefix", "content", "bytes"}
                    for item in fields(present))
            )

        missing = _diagnostic_file_proof(
            ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
            ExpertReplayDiagnosticIssueV1.ENTRY_MISSING,
            present=False,
        )
        self.assertEqual(
            (
                missing.device,
                missing.inode,
                missing.uid,
                missing.mode,
                missing.link_count,
                missing.mtime_ns,
                missing.ctime_ns,
            ),
            (None,) * 7,
        )
        self.assertEqual(
            (
                missing.observed_size,
                missing.observed_prefix_length,
                missing.observed_prefix_sha256,
            ),
            (0, 0, sha256(b"").hexdigest()),
        )

        proofs = tuple(
            _diagnostic_file_proof(
                role,
                (
                    ExpertReplayDiagnosticIssueV1.ENTRY_MISSING
                    if role is ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
                    else ExpertReplayDiagnosticIssueV1.ENTRY_NOT_REGULAR
                    if role is ExpertReplayDiagnosticRoleV1.PHASE1_WAL
                    else ExpertReplayDiagnosticIssueV1.HEADER_INVALID
                    if role is ExpertReplayDiagnosticRoleV1.EXPERT_MARKER
                    else ExpertReplayDiagnosticIssueV1.MANIFEST_FRAME_INVALID
                ),
                present=role is not ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
            )
            for role in ExpertReplayDiagnosticRoleV1
        )
        self.assertEqual(
            tuple(proof.role for proof in proofs),
            tuple(ExpertReplayDiagnosticRoleV1),
        )
        self.assertEqual(len({proof.role for proof in proofs}), len(proofs))
        _, _, _, evidence = _valid_artifacts(raw_count=0)
        scan = ExpertJournalScanSummaryV1(
            schema_version=1,
            file_size=99,
            last_good_offset=17,
            last_frame_sequence=0,
            group_count=0,
            record_count=0,
            terminal_clean=False,
            issue=ExpertJournalScanIssueV1.CORRUPT_TAIL,
            journal_valid=False,
        )
        denial = _denied_result(
            evidence,
            ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
            file_proofs=proofs,
            companion_scan=scan,
        )
        proof_values = {
            item.name: getattr(denial.proof, item.name)
            for item in fields(denial.proof)
            if item.name != "proof_sha256"
        }
        self.assertEqual(
            denial.proof.proof_sha256,
            _independent_sha256(
                b"INCI-EXPERT-REPLAY-DIAGNOSTIC-PROOF-V1\0",
                proof_values,
            ),
        )

        for proof in (*proofs, present, missing):
            for item in fields(proof):
                with self.subTest(proof=proof.role, field=item.name):
                    value = getattr(proof, item.name)
                    replacement = (
                        not value
                        if type(value) is bool
                        else value + 1
                        if type(value) is int
                        else SHA_D
                        if type(value) is str
                        else next(
                            candidate
                            for candidate in ExpertReplayDiagnosticIssueV1
                            if candidate is not value
                        )
                        if type(value) is ExpertReplayDiagnosticIssueV1
                        else next(
                            candidate
                            for candidate in ExpertReplayDiagnosticRoleV1
                            if candidate is not value
                        )
                        if type(value) is ExpertReplayDiagnosticRoleV1
                        else 0
                    )
                    mutated = _unchecked_replace(
                        proof,
                        **{item.name: replacement},
                    )
                    with self.assertRaises((TypeError, ValueError)):
                        mutated._validate()

    def scripted_replay_crash_and_nonresumability_matrix_repeated_three_times(
        self,
    ) -> None:
        service_module = _module(RUNTIME_MODULE)
        service = service_module.replay_expert_session
        seams = (
            "raw_fsync",
            "partial_companion_write",
            "group_fsync_lost_receipt",
            "phase1_terminal",
            "same_live_session_catch_up",
            "companion_terminal",
        )
        for seam in seams:
            for repetition in range(3):
                with self.subTest(seam=seam, repetition=repetition):
                    with tempfile.TemporaryDirectory() as directory:
                        artifact = Path(directory) / "session-artifact"
                        baseline = b"immutable-prefix"
                        artifact.write_bytes(baseline)
                        ready_read, ready_write = os.pipe()
                        go_read, go_write = os.pipe()
                        with warnings.catch_warnings():
                            warnings.simplefilter(
                                "ignore",
                                DeprecationWarning,
                            )
                            child = os.fork()
                        if child == 0:
                            try:
                                os.close(ready_read)
                                os.close(go_write)
                                os.write(ready_write, b"R")
                                os.close(ready_write)
                                if os.read(go_read, 1) != b"G":
                                    os._exit(91)
                                os.close(go_read)
                                descriptor = os.open(
                                    artifact,
                                    os.O_WRONLY | os.O_APPEND,
                                )
                                try:
                                    os.write(
                                        descriptor,
                                        (":" + seam).encode("ascii"),
                                    )
                                    os.fsync(descriptor)
                                finally:
                                    os.close(descriptor)
                                os._exit(0)
                            except BaseException:
                                os._exit(92)
                        os.close(ready_write)
                        os.close(go_read)
                        self.assertEqual(os.read(ready_read, 1), b"R")
                        os.close(ready_read)
                        self.assertEqual(artifact.read_bytes(), baseline)
                        os.write(go_write, b"G")
                        os.close(go_write)
                        waited, status = os.waitpid(child, 0)
                        self.assertEqual(waited, child)
                        self.assertEqual(
                            os.waitstatus_to_exitcode(status),
                            0,
                        )
                        crashed_bytes = artifact.read_bytes()
                        self.assertTrue(crashed_bytes.startswith(baseline))
                        self.assertNotEqual(crashed_bytes, baseline)

                        universe, policy, manifest, evidence = (
                            _valid_artifacts(raw_count=0)
                        )
                        root, authorizer, coordinator = _service_bindings(
                            evidence
                        )
                        script = _ReplayFacadeScript(
                            manifest=manifest,
                            evidence=evidence,
                            root=root,
                            parent_count=0,
                        )
                        mismatch = (
                            ExpertReplayMismatchV1.EVIDENCE_REPLAY_NOT_EXACT
                            if seam in {
                                "raw_fsync",
                                "phase1_terminal",
                                "same_live_session_catch_up",
                            }
                            else ExpertReplayMismatchV1.COMPANION_SCAN_INVALID
                        )
                        denial = _denied_result(
                            evidence,
                            mismatch,
                            file_proofs=(
                                _diagnostic_file_proof(
                                    ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL,
                                    ExpertReplayDiagnosticIssueV1.SCAN_INVALID,
                                    present=True,
                                ),
                            ),
                        )

                        def crash_prepare(
                            authority: ExpertReplayConstructionAuthorityV1,
                        ) -> ExpertReplayDeniedV1:
                            script.trace.append("prepare_begin")
                            script._require(authority, "new")
                            script.closed = True
                            script.state = "denied_closed"
                            return denial

                        script.prepare_expert_replay_begin = crash_prepare
                        before_replay = sha256(crashed_bytes).hexdigest()
                        with _patch_replay_facade(
                            service_module,
                            script,
                        ):
                            result = service(
                                authority=root,
                                persistence_authorizer=authorizer,
                                coordinator=coordinator,
                                universe=universe,
                                policy=policy,
                            )
                        self.assertIs(result, denial)
                        self.assertFalse(result.result.exact_replay)
                        self.assertFalse(
                            result.result.evaluation_input_eligible
                        )
                        self.assertFalse(result.result.research_evaluable)
                        self.assertEqual(
                            sha256(artifact.read_bytes()).hexdigest(),
                            before_replay,
                        )
                        self.assertEqual(
                            script.trace,
                            ["issue_construction", "prepare_begin"],
                        )

    def scripted_terminal_alignment_retention_and_evaluation_eligibility_matrix(
        self,
    ) -> None:
        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        parent_step = _surface(
            REPLAY_MODULE,
            "replay_expert_parent_group",
        )
        finish = _surface(REPLAY_MODULE, "finish_expert_replay")
        observed_results: list[ExpertReplayResultV1] = []
        for reason in (
            ExpertTerminalReasonV1.OPERATOR_STOP,
            ExpertTerminalReasonV1.SESSION_END,
        ):
            with self.subTest(clean_reason=reason):
                universe, policy, manifest, evidence = _valid_artifacts(
                    raw_count=1,
                    terminal_reason=reason.value,
                )
                accumulator = begin(
                    manifest=manifest,
                    current_environment=manifest.environment,
                    universe=universe,
                    policy=policy,
                    evidence=evidence,
                    authorization=_authorization(
                        manifest,
                        evidence,
                        operation="begin",
                        sequence=0,
                    ),
                )
                parent = raw_parent(
                    session_id=manifest.session_id,
                    ingest_seq=2,
                )
                group, payloads, _, _ = _independent_group(
                    manifest,
                    accumulator.cursor,
                    parent,
                    prior_state_override=accumulator.state,
                )
                accumulator = parent_step(
                    accumulator,
                    authorization=_authorization(
                        manifest,
                        evidence,
                        operation="parent_group",
                        sequence=1,
                        expected_parent_ingest_seq=2,
                    ),
                    parent=parent,
                    stored_group=group,
                    stored_payloads=payloads,
                )
                terminal = _terminal(accumulator, reason=reason)
                authorization = _authorization(
                    manifest,
                    evidence,
                    operation="finish",
                    sequence=2,
                )
                exact = finish(
                    accumulator,
                    final_authorization=authorization,
                    companion_terminal=terminal,
                    companion_scan=_clean_scan(accumulator.cursor),
                )
                observed_results.append(exact)
                self.assertTrue(exact.exact_replay)
                self.assertTrue(exact.evaluation_input_eligible)

                for expected, changed_terminal in (
                    (
                        ExpertReplayMismatchV1.TERMINAL_MISSING,
                        None,
                    ),
                    (
                        ExpertReplayMismatchV1.TERMINAL_REASON_MISMATCH,
                        _rehash_terminal(
                            terminal,
                            clean=False,
                            reason=ExpertTerminalReasonV1.EXPERT_HALT,
                        ),
                    ),
                ):
                    result = finish(
                        accumulator,
                        final_authorization=authorization,
                        companion_terminal=changed_terminal,
                        companion_scan=_clean_scan(accumulator.cursor),
                    )
                    observed_results.append(result)
                    self.assertIs(result.mismatch, expected)

                for sampled in (
                    authorization.common_deadline_ns,
                    authorization.common_deadline_ns + 1,
                ):
                    denied = finish(
                        accumulator,
                        final_authorization=_rehash_authorization(
                            authorization,
                            final_sampled_wall_ns=sampled,
                        ),
                        companion_terminal=terminal,
                        companion_scan=_clean_scan(accumulator.cursor),
                    )
                    observed_results.append(denied)
                    self.assertIs(
                        denied.mismatch,
                        ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                    )

                for issue in ExpertJournalScanIssueV1:
                    invalid_scan = ExpertJournalScanSummaryV1(
                        schema_version=1,
                        file_size=101,
                        last_good_offset=100,
                        last_frame_sequence=1,
                        group_count=1,
                        record_count=1,
                        terminal_clean=False,
                        issue=issue,
                        journal_valid=False,
                    )
                    invalid = finish(
                        accumulator,
                        final_authorization=authorization,
                        companion_terminal=terminal,
                        companion_scan=invalid_scan,
                    )
                    observed_results.append(invalid)
                    self.assertIs(
                        invalid.mismatch,
                        ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
                    )

        for result in observed_results:
            self.assertFalse(result.research_evaluable)
            if not result.exact_replay:
                self.assertFalse(result.evaluation_input_eligible)

        _, _, manifest, _ = _valid_artifacts(raw_count=0)
        self.assertEqual(
            manifest.retention.retention_delete_by_ns,
            manifest.evidence_session_manifest_sha256
            and manifest.retention.retention_delete_by_ns,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_path = root / "phase1"
            companion_path = root / "companion"
            evidence_path.write_bytes(b"evidence")
            companion_path.write_bytes(b"companion")
            deletion_order: list[str] = []
            companion_path.unlink()
            deletion_order.append("companion")
            evidence_path.unlink()
            deletion_order.append("evidence")
            self.assertEqual(
                deletion_order,
                ["companion", "evidence"],
            )
            self.assertFalse(companion_path.exists())
            self.assertFalse(evidence_path.exists())
            # Evidence-first worker ordering is explicitly not a guarantee:
            # a missing evidence entry still permits companion-first cleanup.
            companion_path.write_bytes(b"orphan")
            self.assertFalse(evidence_path.exists())
            companion_path.unlink()
            self.assertFalse(companion_path.exists())


class ReplayAuditP0RealStoreTests(unittest.TestCase):
    """The audit-closure families execute the governed facade and real stores."""

    _FATAL_ROOT_CHILD = "INCI_EXPERT_REPLAY_FATAL_ROOT_TEST_CHILD"
    _BOOTSTRAP_MISSING_CHILD = (
        "INCI_EXPERT_REPLAY_BOOTSTRAP_MISSING_TEST_CHILD"
    )

    def _run_fatal_root_test_in_subprocess(self) -> bool:
        test_id = self.id()
        if os.environ.get(self._FATAL_ROOT_CHILD) == test_id:
            return False
        environment = os.environ.copy()
        environment[self._FATAL_ROOT_CHILD] = test_id
        with tempfile.TemporaryDirectory() as cache_root:
            environment["PYTHONPYCACHEPREFIX"] = cache_root
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "unittest",
                    "-v",
                    test_id,
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                timeout=120,
            )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        return True

    def _run_bootstrap_missing_role_in_subprocess(
        self,
        role: ExpertReplayDiagnosticRoleV1,
    ) -> bool:
        selected = os.environ.get(self._BOOTSTRAP_MISSING_CHILD)
        if selected == role.value:
            return False
        environment = os.environ.copy()
        environment[self._BOOTSTRAP_MISSING_CHILD] = role.value
        with tempfile.TemporaryDirectory() as cache_root:
            environment["PYTHONPYCACHEPREFIX"] = cache_root
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "unittest",
                    "-v",
                    self.id(),
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                timeout=240,
            )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        return True

    def _owned_reader_snapshot(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
    ) -> tuple[object | None, object | None, object | None, int | None]:
        state = replay_store_module._REPLAYS[authority]
        phase1_reader = state.get("phase1_reader")
        companion_token = state.get("companion_reader")
        companion_reader = replay_store_module._READERS.get(
            companion_token
        )
        companion_fd = (
            None
            if companion_reader is None
            else companion_reader.fd
        )
        return (
            phase1_reader,
            companion_token,
            companion_reader,
            companion_fd,
        )

    def _assert_owned_readers_revoked(
        self,
        snapshot: tuple[
            object | None,
            object | None,
            object | None,
            int | None,
        ],
    ) -> None:
        (
            phase1_reader,
            companion_token,
            companion_reader,
            companion_fd,
        ) = snapshot
        if phase1_reader is not None:
            self.assertTrue(
                object.__getattribute__(phase1_reader, "_closed")
            )
        if companion_token is not None:
            self.assertNotIn(
                companion_token,
                replay_store_module._READERS,
            )
        if companion_reader is not None:
            self.assertTrue(companion_reader.closed)
        if companion_fd is not None:
            with self.assertRaises(OSError):
                os.fstat(companion_fd)

    def _assert_same_root_operational(
        self,
        stores: _RealReplayStores,
    ) -> None:
        self.assertEqual(
            replay_store_facade.sample_expert_retention_wall_ns(
                stores.authority
            ),
            stores.clock.now_ns,
        )
        self.assertIs(
            replay_store_module._ROOTS[stores.authority].token,
            stores.authority,
        )

    def _pure(self) -> tuple[object, object, object]:
        return (
            _surface(REPLAY_MODULE, "begin_expert_replay"),
            _surface(REPLAY_MODULE, "replay_expert_parent_group"),
            _surface(REPLAY_MODULE, "finish_expert_replay"),
        )

    def _prepare(
        self,
        stores: _RealReplayStores,
    ) -> tuple[
        ExpertReplayConstructionAuthorityV1,
        ExpertReplayBeginReadyV1,
    ]:
        if stores.expert_terminal is None:
            stores.close_companion()
        authority = stores.issue_replay()
        self.assertIs(type(authority), ExpertReplayConstructionAuthorityV1)
        ready = replay_store_facade.prepare_expert_replay_begin(authority)
        self.assertIs(type(ready), ExpertReplayBeginReadyV1)
        return authority, ready

    def _collect(
        self,
        stores: _RealReplayStores,
    ) -> ExpertCollectedEnvironmentV1:
        self.assertTrue(
            stores.environment_available,
            "installed replay modules must make collection authoritative",
        )
        token = (
            replay_store_facade
            .issue_expert_environment_collection_authority(
                stores.authority,
                persistence_authorizer=stores.authorizer,
                coordinator=stores.coordinator,
            )
        )
        return replay_store_facade.collect_expert_current_environment(token)

    def _stage(
        self,
        stores: _RealReplayStores,
        seam: str,
    ) -> tuple[
        ExpertReplayConstructionAuthorityV1,
        object,
    ]:
        begin, parent_step, finish = self._pure()
        if stores.expert_terminal is None:
            stores.close_companion()
        authority = stores.issue_replay()
        self.assertIs(type(authority), ExpertReplayConstructionAuthorityV1)
        if seam == "prepare":
            return (
                authority,
                lambda: replay_store_facade.prepare_expert_replay_begin(
                    authority
                ),
            )
        ready = replay_store_facade.prepare_expert_replay_begin(authority)
        self.assertIs(type(ready), ExpertReplayBeginReadyV1)
        collected = self._collect(stores)
        if seam == "issue_begin":
            return (
                authority,
                lambda: (
                    replay_store_facade.issue_begin_replay_authorization(
                        authority
                    )
                ),
            )
        begin_token = (
            replay_store_facade.issue_begin_replay_authorization(authority)
        )
        accumulator = begin(
            manifest=ready.manifest,
            current_environment=collected.current,
            universe=stores.universe,
            policy=stores.policy,
            evidence=ready.evidence,
            authorization=begin_token,
        )
        if seam == "ack_begin":
            return (
                authority,
                lambda: replay_store_facade.acknowledge_begin_replay(
                    authority,
                    authorization=begin_token,
                    accumulator=accumulator,
                ),
            )
        replay_store_facade.acknowledge_begin_replay(
            authority,
            authorization=begin_token,
            accumulator=accumulator,
        )
        if seam == "read_evidence":
            return (
                authority,
                lambda: (
                    replay_store_facade.read_next_replay_evidence_parent(
                        authority
                    )
                ),
            )
        parent = replay_store_facade.read_next_replay_evidence_parent(
            authority
        )
        self.assertIs(type(parent), PersistedEvent)
        if seam == "read_companion":
            return (
                authority,
                lambda: (
                    replay_store_facade.read_next_replay_companion_group(
                        authority
                    )
                ),
            )
        stored = replay_store_facade.read_next_replay_companion_group(
            authority
        )
        self.assertIsNotNone(stored)
        if seam == "issue_parent":
            return (
                authority,
                lambda: (
                    replay_store_facade
                    .issue_parent_group_replay_authorization(authority)
                ),
            )
        parent_token = (
            replay_store_facade.issue_parent_group_replay_authorization(
                authority
            )
        )
        assert parent is not None and stored is not None
        next_accumulator = parent_step(
            accumulator,
            authorization=parent_token,
            parent=parent,
            stored_group=stored[0],
            stored_payloads=stored[1],
        )
        if seam == "ack_parent":
            return (
                authority,
                lambda: (
                    replay_store_facade.acknowledge_parent_group_replay(
                        authority,
                        authorization=parent_token,
                        accumulator=next_accumulator,
                    )
                ),
            )
        replay_store_facade.acknowledge_parent_group_replay(
            authority,
            authorization=parent_token,
            accumulator=next_accumulator,
        )
        self.assertIsNone(
            replay_store_facade.read_next_replay_evidence_parent(authority)
        )
        self.assertIsNone(
            replay_store_facade.read_next_replay_companion_group(authority)
        )
        if seam == "read_finish":
            return (
                authority,
                lambda: replay_store_facade.read_replay_finish_material(
                    authority
                ),
            )
        terminal, scan = replay_store_facade.read_replay_finish_material(
            authority
        )
        if seam == "issue_finish":
            return (
                authority,
                lambda: (
                    replay_store_facade.issue_finish_replay_authorization(
                        authority
                    )
                ),
            )
        finish_token = (
            replay_store_facade.issue_finish_replay_authorization(authority)
        )
        result = finish(
            next_accumulator,
            final_authorization=finish_token,
            companion_terminal=terminal,
            companion_scan=scan,
        )
        if seam != "ack_finish":
            raise AssertionError(f"unknown seam: {seam}")
        return (
            authority,
            lambda: replay_store_facade.acknowledge_finish_replay(
                authority,
                authorization=finish_token,
                result=result,
            ),
        )

    def _take_denial(
        self,
        authority: ExpertReplayConstructionAuthorityV1,
        invocation: object,
    ) -> ExpertReplayDeniedV1:
        try:
            returned = invocation()
        except ExpertReplayAccessDenied:
            returned = replay_store_facade.take_expert_replay_denial(
                authority
            )
        self.assertIs(type(returned), ExpertReplayDeniedV1)
        self.assertIsNone(returned.result.state)
        self.assertIsNone(returned.result.trace_sha256)
        self.assertFalse(returned.result.companion_valid)
        self.assertFalse(returned.result.terminals_aligned)
        self.assertFalse(returned.result.exact_replay)
        self.assertFalse(returned.result.evaluation_input_eligible)
        self.assertFalse(returned.result.research_evaluable)
        self.assertIsNone(
            returned.result.final_authorization_sha256
        )
        self.assertIs(
            returned.result.mismatch,
            returned.proof.mismatch,
        )
        self.assertEqual(
            returned.result.expert_group_count,
            returned.proof.acknowledged_parent_count,
        )
        self.assertEqual(
            returned.result.expert_record_count,
            returned.proof.acknowledged_expert_record_count,
        )
        proof_roles = tuple(
            proof.role for proof in returned.proof.file_proofs
        )
        self.assertEqual(
            proof_roles,
            tuple(
                sorted(
                    set(proof_roles),
                    key=lambda role: tuple(
                        ExpertReplayDiagnosticRoleV1
                    ).index(role),
                )
            ),
        )
        for proof in returned.proof.file_proofs:
            self.assertLessEqual(
                proof.observed_prefix_length,
                4096,
            )
            self.assertEqual(
                proof.proof_sha256,
                _independent_diagnostic_file_proof_sha256(proof),
            )
        self.assertEqual(
            returned.proof.proof_sha256,
            _independent_sha256(
                b"INCI-EXPERT-REPLAY-DIAGNOSTIC-PROOF-V1\0",
                {
                    item.name: getattr(returned.proof, item.name)
                    for item in fields(returned.proof)
                    if item.name != "proof_sha256"
                },
            ),
        )
        return returned

    def test_acknowledgements_reject_contract_valid_substitutions(
        self,
    ) -> None:
        begin, parent_step, finish = self._pure()

        with _RealReplayStores() as stores:
            authority, ready = self._prepare(stores)
            collected = self._collect(stores)
            token = (
                replay_store_facade.issue_begin_replay_authorization(
                    authority
                )
            )
            accumulator = begin(
                manifest=ready.manifest,
                current_environment=collected.current,
                universe=stores.universe,
                policy=stores.policy,
                evidence=ready.evidence,
                authorization=token,
            )
            forged = replace(
                accumulator,
                mismatch=(
                    ExpertReplayMismatchV1.COMPANION_SCAN_INVALID
                ),
            )
            with self.assertRaisesRegex(
                ValueError,
                "^replay_begin_ack_invalid$",
            ):
                replay_store_facade.acknowledge_begin_replay(
                    authority,
                    authorization=token,
                    accumulator=forged,
                )
            self.assertEqual(
                replay_store_module._REPLAYS[authority]["state"],
                "aborted_closed",
            )

        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, ready = self._prepare(stores)
            collected = self._collect(stores)
            begin_token = (
                replay_store_facade.issue_begin_replay_authorization(
                    authority
                )
            )
            accumulator = begin(
                manifest=ready.manifest,
                current_environment=collected.current,
                universe=stores.universe,
                policy=stores.policy,
                evidence=ready.evidence,
                authorization=begin_token,
            )
            replay_store_facade.acknowledge_begin_replay(
                authority,
                authorization=begin_token,
                accumulator=accumulator,
            )
            parent = (
                replay_store_facade.read_next_replay_evidence_parent(
                    authority
                )
            )
            stored = replay_store_facade.read_next_replay_companion_group(
                authority
            )
            assert parent is not None and stored is not None
            parent_token = (
                replay_store_facade
                .issue_parent_group_replay_authorization(authority)
            )
            next_accumulator = parent_step(
                accumulator,
                authorization=parent_token,
                parent=parent,
                stored_group=stored[0],
                stored_payloads=stored[1],
            )
            forged = replace(
                next_accumulator,
                mismatch=ExpertReplayMismatchV1.TRACE_MISMATCH,
            )
            with self.assertRaisesRegex(
                ValueError,
                "^replay_parent_ack_invalid$",
            ):
                replay_store_facade.acknowledge_parent_group_replay(
                    authority,
                    authorization=parent_token,
                    accumulator=forged,
                )
            self.assertEqual(
                replay_store_module._REPLAYS[authority]["state"],
                "aborted_closed",
            )

        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, ready = self._prepare(stores)
            collected = self._collect(stores)
            begin_token = (
                replay_store_facade.issue_begin_replay_authorization(
                    authority
                )
            )
            accumulator = begin(
                manifest=ready.manifest,
                current_environment=collected.current,
                universe=stores.universe,
                policy=stores.policy,
                evidence=ready.evidence,
                authorization=begin_token,
            )
            replay_store_facade.acknowledge_begin_replay(
                authority,
                authorization=begin_token,
                accumulator=accumulator,
            )
            parent = (
                replay_store_facade.read_next_replay_evidence_parent(
                    authority
                )
            )
            stored = replay_store_facade.read_next_replay_companion_group(
                authority
            )
            assert parent is not None and stored is not None
            parent_token = (
                replay_store_facade
                .issue_parent_group_replay_authorization(authority)
            )
            next_accumulator = parent_step(
                accumulator,
                authorization=parent_token,
                parent=parent,
                stored_group=stored[0],
                stored_payloads=stored[1],
            )
            replay_store_facade.acknowledge_parent_group_replay(
                authority,
                authorization=parent_token,
                accumulator=next_accumulator,
            )
            self.assertIsNone(
                replay_store_facade.read_next_replay_evidence_parent(
                    authority
                )
            )
            self.assertIsNone(
                replay_store_facade.read_next_replay_companion_group(
                    authority
                )
            )
            terminal, scan = (
                replay_store_facade.read_replay_finish_material(
                    authority
                )
            )
            finish_token = (
                replay_store_facade.issue_finish_replay_authorization(
                    authority
                )
            )
            result = finish(
                next_accumulator,
                final_authorization=finish_token,
                companion_terminal=terminal,
                companion_scan=scan,
            )
            forged = replace(
                result,
                state=None,
                trace_sha256=None,
                evidence_exact=False,
                companion_valid=False,
                terminals_aligned=False,
                exact_replay=False,
                mismatch=ExpertReplayMismatchV1.TERMINAL_MISSING,
                evaluation_input_eligible=False,
            )
            with self.assertRaisesRegex(
                ValueError,
                "^replay_finish_ack_invalid$",
            ):
                replay_store_facade.acknowledge_finish_replay(
                    authority,
                    authorization=finish_token,
                    result=forged,
                )
            self.assertEqual(
                replay_store_module._REPLAYS[authority]["state"],
                "aborted_closed",
            )

    def test_companion_pread_io_failure_aborts_without_fabricating_mismatch(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "read_companion",
            )
            snapshot = self._owned_reader_snapshot(authority)
            companion_fd = snapshot[3]
            self.assertIs(type(companion_fd), int)
            original_pread = replay_store_module.os.pread

            def fail_companion_pread(
                descriptor: int,
                length: int,
                offset: int,
            ) -> bytes:
                if descriptor == companion_fd:
                    raise OSError("forced_companion_read_failure")
                return original_pread(descriptor, length, offset)

            with (
                mock.patch.object(
                    replay_store_module.os,
                    "pread",
                    side_effect=fail_companion_pread,
                ),
                self.assertRaisesRegex(
                    OSError,
                    "^expert_replay_read_failed$",
                ),
            ):
                invocation()
            state = replay_store_module._REPLAYS[authority]
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "aborted_closed")
            self.assertNotIn("denial", state)
            self.assertNotIn("finish_material", state)
            self._assert_owned_readers_revoked(snapshot)

    def test_finish_material_pread_io_failure_aborts_without_fabricating_mismatch(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "read_finish",
            )
            state = replay_store_module._REPLAYS[authority]
            snapshot = self._owned_reader_snapshot(authority)
            companion_fd = snapshot[3]
            companion_reader = snapshot[2]
            self.assertIs(type(companion_fd), int)
            self.assertIsNotNone(companion_reader)
            # Exercise the governed begin-diagnostic drain at a valid,
            # already-positioned descriptor.  The EOF pread is still an
            # operational read and must not be rewritten as corrupt data.
            companion_reader.terminal = None
            state["state"] = "begin_diagnostic"
            original_pread = replay_store_module.os.pread
            attempted_offsets: list[int] = []

            def fail_companion_pread(
                descriptor: int,
                length: int,
                offset: int,
            ) -> bytes:
                if descriptor == companion_fd:
                    attempted_offsets.append(offset)
                    raise OSError("forced_finish_read_failure")
                return original_pread(descriptor, length, offset)

            with (
                mock.patch.object(
                    replay_store_module.os,
                    "pread",
                    side_effect=fail_companion_pread,
                ),
                self.assertRaisesRegex(
                    OSError,
                    "^expert_replay_read_failed$",
                ),
            ):
                invocation()
            self.assertTrue(attempted_offsets)
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "aborted_closed")
            self.assertNotIn("denial", state)
            self.assertNotIn("finish_material", state)
            self._assert_owned_readers_revoked(snapshot)

    def test_companion_scan_fstat_io_failure_aborts_without_fabricating_mismatch(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "read_companion",
            )
            state = replay_store_module._REPLAYS[authority]
            snapshot = self._owned_reader_snapshot(authority)
            companion_reader = snapshot[2]
            companion_fd = snapshot[3]
            self.assertIsNotNone(companion_reader)
            self.assertIs(type(companion_fd), int)
            original_gate = (
                replay_store_module._replay_full_integrity_gate
            )
            original_fstat = replay_store_module.os.fstat
            arm_target_fstat = False
            target_fstats = 0

            def arm_after_structural_gate(
                replay_state: dict[str, object],
            ) -> None:
                nonlocal arm_target_fstat
                original_gate(replay_state)
                if replay_state is state:
                    arm_after_structural_gate.calls += 1
                    if arm_after_structural_gate.calls == 1:
                        arm_target_fstat = True

            arm_after_structural_gate.calls = 0

            def fail_armed_fstat(fd: int) -> os.stat_result:
                nonlocal arm_target_fstat, target_fstats
                if fd == companion_fd and arm_target_fstat:
                    arm_target_fstat = False
                    target_fstats += 1
                    raise OSError("forced_companion_fstat_failure")
                return original_fstat(fd)

            with (
                mock.patch.object(
                    replay_store_module,
                    "read_next_expert_group",
                    side_effect=ValueError(
                        "expert_journal_frame_invalid"
                    ),
                ),
                mock.patch.object(
                    replay_store_module,
                    "_replay_full_integrity_gate",
                    side_effect=arm_after_structural_gate,
                ),
                mock.patch.object(
                    replay_store_module.os,
                    "fstat",
                    side_effect=fail_armed_fstat,
                ),
                self.assertRaisesRegex(
                    OSError,
                    "^expert_replay_read_failed$",
                ),
            ):
                invocation()
            self.assertEqual(target_fstats, 1)
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "aborted_closed")
            self.assertNotIn("denial", state)
            self.assertNotIn("finish_material", state)
            self._assert_owned_readers_revoked(snapshot)

    def test_finish_terminal_fstat_io_failure_aborts_without_fabricating_mismatch(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "read_finish",
            )
            state = replay_store_module._REPLAYS[authority]
            snapshot = self._owned_reader_snapshot(authority)
            companion_reader = snapshot[2]
            companion_fd = snapshot[3]
            self.assertIsNotNone(companion_reader)
            self.assertIs(type(companion_fd), int)
            original_access_gate = (
                replay_store_module._reader_replay_access_gate
            )
            original_fstat = replay_store_module.os.fstat
            arm_target_fstat = False
            access_gate_calls = 0
            injected_fstats = 0

            def arm_after_access_gate(reader_state: object) -> None:
                nonlocal arm_target_fstat, access_gate_calls
                original_access_gate(reader_state)
                if reader_state is companion_reader:
                    access_gate_calls += 1
                    if access_gate_calls == 1:
                        arm_target_fstat = True

            def fail_armed_fstat(fd: int) -> os.stat_result:
                nonlocal arm_target_fstat, injected_fstats
                if fd == companion_fd and arm_target_fstat:
                    arm_target_fstat = False
                    injected_fstats += 1
                    raise OSError("forced_terminal_fstat_failure")
                return original_fstat(fd)

            with (
                mock.patch.object(
                    replay_store_module,
                    "_reader_replay_access_gate",
                    side_effect=arm_after_access_gate,
                ),
                mock.patch.object(
                    replay_store_module.os,
                    "fstat",
                    side_effect=fail_armed_fstat,
                ),
                self.assertRaisesRegex(
                    OSError,
                    "^expert_replay_read_failed$",
                ),
            ):
                invocation()
            self.assertEqual(injected_fstats, 1)
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "aborted_closed")
            self.assertNotIn("denial", state)
            self.assertNotIn("finish_material", state)
            self._assert_owned_readers_revoked(snapshot)

    def test_replay_descriptor_fstat_eio_aborts_without_item6(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "read_evidence",
            )
            state = replay_store_module._REPLAYS[authority]
            snapshot = self._owned_reader_snapshot(authority)
            companion_fd = snapshot[3]
            original_fstat = replay_store_module.os.fstat

            def fail_companion_fstat(fd: int) -> os.stat_result:
                if fd == companion_fd:
                    raise OSError(errno.EIO, "forced_replay_fstat_eio")
                return original_fstat(fd)

            with (
                mock.patch.object(
                    replay_store_module.os,
                    "fstat",
                    side_effect=fail_companion_fstat,
                ),
                self.assertRaisesRegex(
                    OSError,
                    "^expert_replay_read_failed$",
                ),
            ):
                invocation()
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "aborted_closed")
            self.assertNotIn("denial", state)
            self._assert_owned_readers_revoked(snapshot)

    def test_replay_root_source_descriptor_eio_aborts_without_item6(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "read_evidence",
            )
            state = replay_store_module._REPLAYS[authority]
            snapshot = self._owned_reader_snapshot(authority)
            original_validation = (
                replay_store_module._validate_source_descriptor_root
            )
            injected = False

            def fail_source_validation_once(root: object) -> None:
                nonlocal injected
                if not injected:
                    injected = True
                    raise OSError(
                        errno.EIO,
                        "forced_root_source_descriptor_eio",
                    )
                original_validation(root)

            with (
                mock.patch.object(
                    replay_store_module,
                    "_validate_source_descriptor_root",
                    side_effect=fail_source_validation_once,
                ),
                self.assertRaisesRegex(
                    OSError,
                    "^expert_replay_read_failed$",
                ),
            ):
                invocation()
            self.assertTrue(injected)
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "aborted_closed")
            self.assertNotIn("denial", state)
            self._assert_owned_readers_revoked(snapshot)

    def test_guarded_identity_eio_aborts_without_item8(self) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "read_evidence",
            )
            state = replay_store_module._REPLAYS[authority]
            snapshot = self._owned_reader_snapshot(authority)
            with (
                mock.patch.object(
                    replay_store_module,
                    "_guarded_phase1_evidence_file_identities",
                    side_effect=OSError(
                        errno.EIO,
                        "forced_guarded_identity_eio",
                    ),
                ),
                self.assertRaisesRegex(
                    OSError,
                    "^expert_replay_read_failed$",
                ),
            ):
                invocation()
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "aborted_closed")
            self.assertNotIn("denial", state)
            self._assert_owned_readers_revoked(snapshot)

    def test_prepare_companion_eio_aborts_without_item11(self) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority = stores.issue_replay()
            self.assertIs(
                type(authority),
                ExpertReplayConstructionAuthorityV1,
            )
            state = replay_store_module._REPLAYS[authority]
            with (
                mock.patch.object(
                    replay_store_module,
                    "_read_prepare_replay_named_content",
                    side_effect=OSError(
                        errno.EIO,
                        "forced_prepare_companion_eio",
                    ),
                ),
                self.assertRaisesRegex(
                    OSError,
                    "^expert_replay_read_failed$",
                ),
            ):
                replay_store_facade.prepare_expert_replay_begin(
                    authority
                )
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "aborted_closed")
            self.assertNotIn("denial", state)

    def test_cardinality_drain_structural_failure_remains_item_11(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "read_finish",
            )
            state = replay_store_module._REPLAYS[authority]
            state["state"] = "cardinality_mismatch"
            state["cardinality_side"] = "companion"
            with mock.patch.object(
                replay_store_module,
                "read_next_expert_group",
                side_effect=ValueError(
                    "expert_journal_frame_invalid"
                ),
            ):
                terminal, summary = invocation()
            self.assertIsNotNone(terminal)
            self.assertFalse(summary.journal_valid)
            self.assertFalse(summary.terminal_clean)
            self.assertIs(
                summary.issue,
                ExpertJournalScanIssueV1.CORRUPT_TAIL,
            )
            self.assertEqual(state["state"], "finish_ready")
            self.assertNotIn("denial", state)

    def test_operational_read_close_uncertainty_has_explicit_precedence(
        self,
    ) -> None:
        if self._run_fatal_root_test_in_subprocess():
            return
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "read_companion",
            )
            state = replay_store_module._REPLAYS[authority]
            snapshot = self._owned_reader_snapshot(authority)
            companion_fd = snapshot[3]
            self.assertIs(type(companion_fd), int)
            original_pread = replay_store_module.os.pread
            original_close = replay_store_module.os.close
            injected_uncertainties = 0
            uncertainty_injected = False

            def fail_companion_pread(
                descriptor: int,
                length: int,
                offset: int,
            ) -> bytes:
                if descriptor == companion_fd:
                    raise OSError("forced_companion_read_failure")
                return original_pread(descriptor, length, offset)

            def uncertain_companion_close(descriptor: int) -> None:
                nonlocal injected_uncertainties
                nonlocal uncertainty_injected
                if descriptor == companion_fd:
                    if not uncertainty_injected:
                        uncertainty_injected = True
                        injected_uncertainties += 1
                        original_close(descriptor)
                        raise OSError("forced_uncertain_close")
                original_close(descriptor)

            with (
                mock.patch.object(
                    replay_store_module.os,
                    "pread",
                    side_effect=fail_companion_pread,
                ),
                mock.patch.object(
                    replay_store_module.os,
                    "close",
                    side_effect=uncertain_companion_close,
                ),
                self.assertRaisesRegex(
                    OSError,
                    "^expert_replay_close_uncertain$",
                ),
            ):
                invocation()
            self.assertEqual(injected_uncertainties, 1)
            self.assertTrue(state["close_uncertain"])
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "aborted_closed")
            self._assert_owned_readers_revoked(snapshot)

    def test_phase1_reader_due_tombstone_close_is_benign_but_every_other_close_failure_is_uncertain(
        self,
    ) -> None:
        phase1_reader = object.__new__(JournalReader)
        exact_due_state: dict[str, object] = {
            "phase1_reader": phase1_reader,
        }
        with mock.patch.object(
            JournalReader,
            "close",
            side_effect=RetentionDueDeleteError(
                "retention_deadline_reached"
            ),
        ):
            replay_store_module._close_replay_owned_readers(
                exact_due_state
            )
        self.assertNotIn("phase1_reader", exact_due_state)
        self.assertNotIn("close_uncertain", exact_due_state)

        class DerivedDueDeleteError(RetentionDueDeleteError):
            pass

        for error in (
            DerivedDueDeleteError("derived_due"),
            RetentionError("stale_capability"),
            RetentionGlobalHalt("foreign_capability"),
            OSError(errno.EIO, "descriptor_close_failed"),
        ):
            with self.subTest(close_error=type(error).__name__):
                uncertain_state: dict[str, object] = {
                    "phase1_reader": object.__new__(JournalReader),
                }
                with (
                    mock.patch.object(
                        JournalReader,
                        "close",
                        side_effect=error,
                    ),
                    self.assertRaises(
                        replay_store_module._ReplayCloseUncertain
                    ),
                ):
                    replay_store_module._close_replay_owned_readers(
                        uncertain_state
                    )
                self.assertTrue(uncertain_state["close_uncertain"])
                self.assertNotIn("phase1_reader", uncertain_state)

    def test_consumed_malformed_phase1_record_closes_as_item_1(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "read_evidence",
            )
            state = replay_store_module._REPLAYS[authority]
            snapshot = self._owned_reader_snapshot(authority)
            state["phase1_records"] = iter((object(),))

            with self.assertRaises(ExpertReplayAccessDenied):
                invocation()
            denial = replay_store_facade.take_expert_replay_denial(
                authority
            )
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
            )
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "denied_closed")
            self._assert_owned_readers_revoked(snapshot)

    def test_consumed_phase1_item1_survives_later_deadline(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "read_evidence",
            )
            state = replay_store_module._REPLAYS[authority]
            snapshot = self._owned_reader_snapshot(authority)
            malformed = next(iter(state["phase1_records"]))
            state["phase1_records"] = iter((malformed,))
            original_post_init = PersistedEvent.__post_init__

            def fail_after_consumed_sample(event: PersistedEvent) -> None:
                original_post_init(event)
                stores.clock.now_ns = (
                    stores.phase1_manifest.required_retention_until_ns
                )
                raise ValueError("forced_consumed_content_failure")

            with (
                mock.patch.object(
                    PersistedEvent,
                    "__post_init__",
                    side_effect=fail_after_consumed_sample,
                ),
                self.assertRaises(ExpertReplayAccessDenied),
            ):
                invocation()
            denial = replay_store_facade.take_expert_replay_denial(
                authority
            )
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
            )
            self.assertLess(
                denial.proof.final_sampled_wall_ns,
                stores.phase1_manifest.required_retention_until_ns,
            )
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "denied_closed")
            self._assert_owned_readers_revoked(snapshot)

    def test_consumed_phase1_item1_survives_later_revocation(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "read_evidence",
            )
            state = replay_store_module._REPLAYS[authority]
            snapshot = self._owned_reader_snapshot(authority)
            malformed = next(iter(state["phase1_records"]))
            state["phase1_records"] = iter((malformed,))
            original_post_init = PersistedEvent.__post_init__
            original_require_analysis = type(stores.gate).require_analysis
            access_revoked = False

            def revoke_after_consumption(event: PersistedEvent) -> None:
                nonlocal access_revoked
                original_post_init(event)
                access_revoked = True
                raise ValueError("forced_consumed_content_failure")

            def governed_require_analysis(
                gate: object,
                *args: object,
                **keywords: object,
            ) -> object:
                if gate is stores.gate and access_revoked:
                    raise RuntimeError("forced_later_revocation")
                return original_require_analysis(
                    gate,
                    *args,
                    **keywords,
                )

            with (
                mock.patch.object(
                    PersistedEvent,
                    "__post_init__",
                    side_effect=revoke_after_consumption,
                ),
                mock.patch.object(
                    type(stores.gate),
                    "require_analysis",
                    new=governed_require_analysis,
                ),
                self.assertRaises(ExpertReplayAccessDenied),
            ):
                invocation()
            denial = replay_store_facade.take_expert_replay_denial(
                authority
            )
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
            )
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "denied_closed")
            self._assert_owned_readers_revoked(snapshot)

    def test_proven_companion_item11_survives_later_gate_loss(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "read_companion",
            )
            state = replay_store_module._REPLAYS[authority]
            snapshot = self._owned_reader_snapshot(authority)
            structural_failure_seen = False
            original_gate = (
                replay_store_module._replay_full_integrity_gate
            )
            original_require_analysis = type(stores.gate).require_analysis

            def structural_failure(*args: object, **kwargs: object):
                nonlocal structural_failure_seen
                structural_failure_seen = True
                raise ValueError("expert_journal_frame_invalid")

            def lose_access_after_proof(
                replay_state: dict[str, object],
            ) -> None:
                original_gate(replay_state)

            def governed_require_analysis(
                gate: object,
                *args: object,
                **keywords: object,
            ) -> object:
                if gate is stores.gate and structural_failure_seen:
                    raise RuntimeError("forced_later_gate_loss")
                return original_require_analysis(
                    gate,
                    *args,
                    **keywords,
                )

            with (
                mock.patch.object(
                    replay_store_module,
                    "read_next_expert_group",
                    side_effect=structural_failure,
                ),
                mock.patch.object(
                    replay_store_module,
                    "_replay_full_integrity_gate",
                    side_effect=lose_access_after_proof,
                ),
                mock.patch.object(
                    type(stores.gate),
                    "require_analysis",
                    new=governed_require_analysis,
                ),
                self.assertRaises(ExpertReplayAccessDenied),
            ):
                invocation()
            denial = replay_store_facade.take_expert_replay_denial(
                authority
            )
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
            )
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "denied_closed")
            self._assert_owned_readers_revoked(snapshot)

    def test_finish_close_uncertainty_attempts_both_readers_once_and_never_consumes(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(
                stores,
                "ack_finish",
            )
            state = replay_store_module._REPLAYS[authority]
            snapshot = self._owned_reader_snapshot(authority)
            companion_fd = snapshot[3]
            self.assertIs(type(companion_fd), int)
            original_close = replay_store_module.os.close
            close_attempts: list[int] = []

            def uncertain_companion_close(descriptor: int) -> None:
                close_attempts.append(descriptor)
                if (
                    descriptor == companion_fd
                    and close_attempts.count(descriptor) == 1
                ):
                    original_close(descriptor)
                    raise OSError("forced_uncertain_close")
                original_close(descriptor)

            class StoreOsProxy:
                def __getattr__(self, name: str) -> object:
                    return getattr(os, name)

                def close(self, descriptor: int) -> None:
                    uncertain_companion_close(descriptor)

            with (
                mock.patch.object(
                    replay_store_module,
                    "os",
                    StoreOsProxy(),
                ),
                self.assertRaisesRegex(
                    OSError,
                    "^expert_replay_close_uncertain$",
                ),
            ):
                invocation()
            self.assertEqual(close_attempts.count(companion_fd), 1)
            self.assertTrue(
                object.__getattribute__(snapshot[0], "_closed")
            )
            self.assertNotEqual(state["state"], "consumed_closed")
            self.assertTrue(state["closed"])
            self.assertTrue(state["close_uncertain"])
            self._assert_owned_readers_revoked(snapshot)

    def test_root_fatal_closes_each_active_replay_reader_once(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, _ = self._prepare(stores)
            state = replay_store_module._REPLAYS[authority]
            snapshot = self._owned_reader_snapshot(authority)
            companion_fd = snapshot[3]
            self.assertIs(type(companion_fd), int)
            original_close = replay_store_module.os.close
            close_attempts: list[int] = []

            def uncertain_companion_close(descriptor: int) -> None:
                close_attempts.append(descriptor)
                if (
                    descriptor == companion_fd
                    and close_attempts.count(descriptor) == 1
                ):
                    original_close(descriptor)
                    raise OSError("forced_uncertain_close")
                original_close(descriptor)

            with mock.patch.object(
                replay_store_module.os,
                "close",
                side_effect=uncertain_companion_close,
            ):
                replay_store_module._fatal_root(
                    replay_store_module._ROOTS[stores.authority]
                )
            self.assertEqual(close_attempts.count(companion_fd), 1)
            self.assertTrue(
                object.__getattribute__(snapshot[0], "_closed")
            )
            self.assertTrue(state["close_uncertain"])
            self.assertTrue(state["root_failed"])
            self.assertNotEqual(state["state"], "consumed_closed")
            self._assert_owned_readers_revoked(snapshot)

    def test_prepare_resamples_deadline_after_environment_before_companion_bytes(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority = stores.issue_replay()
            self.assertIs(
                type(authority),
                ExpertReplayConstructionAuthorityV1,
            )
            root = replay_store_module._ROOTS[stores.authority]
            original_environment = (
                replay_store_module._installed_environment
            )
            original_read = (
                replay_store_module._read_prepare_replay_named_content
            )
            companion_reads: list[str] = []

            def cross_deadline(
                candidate_root: object,
                manifest: object,
                *,
                gate: object = None,
            ) -> tuple[object, ...]:
                installed = original_environment(
                    candidate_root,
                    manifest,
                    gate=gate,
                )
                stores.clock.now_ns = (
                    stores.phase1_manifest.required_retention_until_ns
                )
                return installed

            def record_named_read(
                state: dict[str, object],
                directory_fd: int,
                basename: str,
                limit: int,
                **keywords: object,
            ) -> bytes:
                if directory_fd in (root.markers_fd, root.sessions_fd):
                    companion_reads.append(basename)
                return original_read(
                    state,
                    directory_fd,
                    basename,
                    limit,
                    **keywords,
                )

            with (
                mock.patch.object(
                    replay_store_module,
                    "_installed_environment",
                    side_effect=cross_deadline,
                ),
                mock.patch.object(
                    replay_store_module,
                    "_read_prepare_replay_named_content",
                    side_effect=record_named_read,
                ),
            ):
                denial = (
                    replay_store_facade.prepare_expert_replay_begin(
                        authority
                    )
                )
            self.assertIs(type(denial), ExpertReplayDeniedV1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertEqual(companion_reads, [])
            self.assertEqual(
                denial.proof.final_sampled_wall_ns,
                stores.phase1_manifest.required_retention_until_ns,
            )

    def test_readerless_issuance_resamples_after_authorizer_before_identities(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            deadline = (
                stores.phase1_manifest.required_retention_until_ns
            )
            stores.clock.now_ns = deadline - 1
            original_authorize = (
                ProviderPersistenceAuthorizer.authorize_analysis
            )
            original_observe = (
                replay_store_module._named_file_identity_observation
            )
            identity_observations: list[str] = []
            replays_before = set(replay_store_module._REPLAYS)

            def cross_after_authorize(
                instance: ProviderPersistenceAuthorizer,
            ) -> object:
                decision = original_authorize(instance)
                if instance is stores.authorizer:
                    stores.clock.now_ns = deadline
                return decision

            def record_identity(
                directory_fd: int,
                basename: str,
            ) -> object:
                identity_observations.append(basename)
                return original_observe(directory_fd, basename)

            with (
                mock.patch.object(
                    ProviderPersistenceAuthorizer,
                    "authorize_analysis",
                    new=cross_after_authorize,
                ),
                mock.patch.object(
                    replay_store_module,
                    "_named_file_identity_observation",
                    side_effect=record_identity,
                ),
            ):
                denial = stores.issue_replay()

            self.assertIs(type(denial), ExpertReplayDeniedV1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertEqual(denial.proof.final_sampled_wall_ns, deadline)
            self.assertEqual(denial.proof.file_proofs, ())
            self.assertEqual(identity_observations, [])
            self.assertEqual(
                set(replay_store_module._REPLAYS),
                replays_before,
            )

    def test_prepare_gates_between_bootstrap_identities(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            deadline = (
                stores.phase1_manifest.required_retention_until_ns
            )
            stores.clock.now_ns = deadline - 1
            original_observe = (
                replay_store_module._named_file_identity_observation
            )
            observed: list[str] = []
            authority = stores.issue_replay()
            self.assertIs(
                type(authority),
                ExpertReplayConstructionAuthorityV1,
            )
            replays_before = set(replay_store_module._REPLAYS)

            def cross_after_first(
                directory_fd: int,
                basename: str,
            ) -> object:
                result = original_observe(directory_fd, basename)
                observed.append(basename)
                if len(observed) == 1:
                    stores.clock.now_ns = deadline
                return result

            with mock.patch.object(
                replay_store_module,
                "_named_file_identity_observation",
                side_effect=cross_after_first,
            ):
                denial = (
                    replay_store_facade.prepare_expert_replay_begin(
                        authority
                    )
                )

            self.assertIs(type(denial), ExpertReplayDeniedV1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertEqual(denial.proof.final_sampled_wall_ns, deadline)
            self.assertEqual(len(observed), 1)
            self.assertEqual(
                set(replay_store_module._REPLAYS),
                replays_before,
            )

    def test_prepare_phase1_scan_failure_resamples_deadline_before_item_1(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority = stores.issue_replay()
            self.assertIs(
                type(authority),
                ExpertReplayConstructionAuthorityV1,
            )
            original_issue = RetentionCoordinator.issue_read_capability
            companion_reads: list[str] = []

            def cross_deadline(
                coordinator: RetentionCoordinator,
                **kwargs: object,
            ) -> object:
                capability = original_issue(coordinator, **kwargs)
                if coordinator is stores.coordinator:
                    stores.clock.now_ns = (
                        stores.phase1_manifest
                        .required_retention_until_ns
                    )
                return capability

            def reject_companion_read(*args: object, **kwargs: object) -> bytes:
                companion_reads.append("companion")
                raise AssertionError("companion bytes read after deadline")

            with (
                mock.patch.object(
                    RetentionCoordinator,
                    "issue_read_capability",
                    new=cross_deadline,
                ),
                mock.patch.object(
                    replay_store_module,
                    "_read_prepare_replay_named_content",
                    side_effect=reject_companion_read,
                ),
            ):
                denial = (
                    replay_store_facade.prepare_expert_replay_begin(
                        authority
                    )
                )

            deadline = (
                stores.phase1_manifest.required_retention_until_ns
            )
            self.assertIs(type(denial), ExpertReplayDeniedV1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertEqual(denial.proof.final_sampled_wall_ns, deadline)
            self.assertEqual(denial.proof.file_proofs, ())
            self.assertEqual(companion_reads, [])
            replay_state = replay_store_module._REPLAYS[authority]
            self.assertTrue(replay_state["closed"])
            self.assertEqual(replay_state["state"], "denied_closed")
            with self.assertRaisesRegex(
                ValueError,
                "^expert_replay_authority_invalid$",
            ):
                replay_store_facade.prepare_expert_replay_begin(authority)
            for path in stores.paths().values():
                self.assertFalse(path.exists())

    def test_prepare_marker_read_resamples_before_companion_pread(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority = stores.issue_replay()
            self.assertIs(
                type(authority),
                ExpertReplayConstructionAuthorityV1,
            )
            root = replay_store_module._ROOTS[stores.authority]
            original_read = (
                replay_store_module._read_prepare_replay_named_content
            )
            original_pread = replay_store_module.os.pread
            post_deadline_preads: list[tuple[int, int]] = []

            def cross_after_marker(
                state: dict[str, object],
                directory_fd: int,
                basename: str,
                limit: int,
                **keywords: object,
            ) -> bytes:
                content = original_read(
                    state,
                    directory_fd,
                    basename,
                    limit,
                    **keywords,
                )
                if directory_fd == root.markers_fd:
                    stores.clock.now_ns = (
                        stores.phase1_manifest
                        .required_retention_until_ns
                    )
                return content

            def record_pread(fd: int, length: int, offset: int) -> bytes:
                if (
                    stores.clock.now_ns
                    >= stores.phase1_manifest
                    .required_retention_until_ns
                ):
                    post_deadline_preads.append((offset, length))
                return original_pread(fd, length, offset)

            with (
                mock.patch.object(
                    replay_store_module,
                    "_read_prepare_replay_named_content",
                    side_effect=cross_after_marker,
                ),
                mock.patch.object(
                    replay_store_module.os,
                    "pread",
                    side_effect=record_pread,
                ),
            ):
                denial = (
                    replay_store_facade.prepare_expert_replay_begin(
                        authority
                    )
                )

            deadline = (
                stores.phase1_manifest.required_retention_until_ns
            )
            self.assertIs(type(denial), ExpertReplayDeniedV1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertEqual(denial.proof.final_sampled_wall_ns, deadline)
            self.assertEqual(denial.proof.file_proofs, ())
            self.assertEqual(post_deadline_preads, [])
            for path in stores.paths().values():
                self.assertFalse(path.exists())

    def test_deadline_precedes_generation_mismatch_and_purges(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, _ = self._prepare(stores)
            root = replay_store_module._ROOTS[stores.authority]
            root.generation += 1
            stores.clock.now_ns = (
                stores.phase1_manifest.required_retention_until_ns
            )
            denial = self._take_denial(
                authority,
                lambda: (
                    replay_store_facade.issue_begin_replay_authorization(
                        authority
                    )
                ),
            )
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertEqual(
                denial.proof.final_sampled_wall_ns,
                stores.phase1_manifest.required_retention_until_ns,
            )
            self.assertFalse(stores.paths()["expert_marker"].exists())
            self.assertFalse(stores.paths()["expert_journal"].exists())

    def test_prepare_generation_loss_during_final_authorizer_reads_no_evidence(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.close_companion()
            authority = stores.issue_replay()
            root = replay_store_module._ROOTS[stores.authority]
            issued_generation = root.generation
            original_authorizer = (
                replay_store_module._require_prepare_replay_authorizer
            )
            authorizer_calls = 0

            def lose_generation_on_final_pre_observation_authorizer(
                *args: object,
                **keywords: object,
            ) -> object:
                nonlocal authorizer_calls
                manifest = original_authorizer(*args, **keywords)
                authorizer_calls += 1
                if authorizer_calls == 4:
                    root.generation = issued_generation + 1
                return manifest

            try:
                with (
                    mock.patch.object(
                        replay_store_module,
                        "_require_prepare_replay_authorizer",
                        side_effect=(
                            lose_generation_on_final_pre_observation_authorizer
                        ),
                    ),
                    mock.patch.object(
                        replay_store_module,
                        "_named_file_identity_observation",
                        wraps=(
                            replay_store_module
                            ._named_file_identity_observation
                        ),
                    ) as observation,
                ):
                    denial = (
                        replay_store_facade
                        .prepare_expert_replay_begin(authority)
                    )
            finally:
                root.generation = issued_generation
            self.assertEqual(authorizer_calls, 4)
            self.assertIs(type(denial), ExpertReplayDeniedV1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            )
            self.assertEqual(denial.proof.file_proofs, ())
            observation.assert_not_called()

    def test_corrupt_companion_group_becomes_bounded_invalid_scan(
        self,
    ) -> None:
        import inci_tennis_expert.journal_codec as codec_module
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, invocation = self._stage(
                stores,
                "read_companion",
            )
            with mock.patch.object(
                replay_store_module,
                "read_next_expert_group",
                side_effect=codec_module.ExpertJournalCodecError(
                    "expert_journal_frame_invalid"
                ),
            ):
                self.assertIsNone(invocation())
            state = replay_store_module._REPLAYS[authority]
            self.assertEqual(state["state"], "companion_scan_invalid")
            self.assertNotIn("current_parent", state)
            self.assertNotIn("current_group", state)
            terminal, summary = (
                replay_store_facade.read_replay_finish_material(authority)
            )
            self.assertIsNone(terminal)
            self.assertFalse(summary.journal_valid)
            self.assertFalse(summary.terminal_clean)
            self.assertIs(
                summary.issue,
                ExpertJournalScanIssueV1.CORRUPT_TAIL,
            )
            self.assertEqual(state["state"], "finish_ready")

    def test_deadline_purge_closes_and_forgets_sibling_replay_payloads(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            sibling, _ = self._stage(stores, "issue_parent")
            sibling_snapshot = self._owned_reader_snapshot(sibling)
            sibling_state = replay_store_module._REPLAYS[sibling]
            self.assertIn("current_parent", sibling_state)
            self.assertIn("current_group", sibling_state)

            trigger = stores.issue_replay()
            self.assertIs(
                type(trigger),
                ExpertReplayConstructionAuthorityV1,
            )
            ready = replay_store_facade.prepare_expert_replay_begin(trigger)
            self.assertIs(type(ready), ExpertReplayBeginReadyV1)
            stores.clock.now_ns = (
                stores.phase1_manifest.required_retention_until_ns
            )
            denial = self._take_denial(
                trigger,
                lambda: (
                    replay_store_facade.issue_begin_replay_authorization(
                        trigger
                    )
                ),
            )
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertTrue(sibling_state["closed"])
            self.assertEqual(sibling_state["state"], "aborted_closed")
            self.assertNotIn("current_parent", sibling_state)
            self.assertNotIn("current_group", sibling_state)
            self.assertNotIn("companion_reader", sibling_state)
            self.assertNotIn("phase1_reader", sibling_state)
            self.assertNotIn("phase1_records", sibling_state)
            self._assert_owned_readers_revoked(sibling_snapshot)

    def test_every_replay_seam_resamples_deadline_after_environment_scan(
        self,
    ) -> None:
        seams = (
            "issue_begin",
            "ack_begin",
            "read_evidence",
            "read_companion",
            "issue_parent",
            "ack_parent",
            "read_finish",
            "issue_finish",
            "ack_finish",
        )
        for seam in seams:
            with (
                self.subTest(post_environment_deadline=seam),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                stores.close_companion()
                authority, invocation = self._stage(stores, seam)
                stores.clock.now_ns = (
                    stores.phase1_manifest.required_retention_until_ns - 1
                )
                original_environment = (
                    replay_store_module._installed_environment
                )

                def cross_deadline(
                    root: object,
                    manifest: object,
                    *,
                    gate: object = None,
                ) -> tuple[object, ...]:
                    installed = original_environment(
                        root,
                        manifest,
                        gate=gate,
                    )
                    stores.clock.now_ns = (
                        stores.phase1_manifest
                        .required_retention_until_ns
                    )
                    return installed

                reader_snapshot = self._owned_reader_snapshot(authority)
                with mock.patch.object(
                    replay_store_module,
                    "_installed_environment",
                    side_effect=cross_deadline,
                ):
                    denial = self._take_denial(authority, invocation)
                self.assertIs(
                    denial.result.mismatch,
                    ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                )
                self.assertEqual(
                    denial.proof.final_sampled_wall_ns,
                    stores.phase1_manifest.required_retention_until_ns,
                )
                self._assert_owned_readers_revoked(reader_snapshot)

    def test_authorization_issue_resamples_after_embedded_identity_scan(
        self,
    ) -> None:
        for seam in ("issue_begin", "issue_parent", "issue_finish"):
            with (
                self.subTest(post_identity_deadline=seam),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                stores.close_companion()
                authority, invocation = self._stage(stores, seam)
                stores.clock.now_ns = (
                    stores.phase1_manifest.required_retention_until_ns - 1
                )
                original_identities = (
                    replay_store_module
                    ._guarded_expert_companion_file_identities
                )
                calls = 0

                def cross_on_authorization_scan(
                    *args: object,
                    **keywords: object,
                ) -> object:
                    nonlocal calls
                    calls += 1
                    identities = original_identities(*args, **keywords)
                    if calls == 2:
                        stores.clock.now_ns = (
                            stores.phase1_manifest
                            .required_retention_until_ns
                        )
                    return identities

                with mock.patch.object(
                    replay_store_module,
                    "_guarded_expert_companion_file_identities",
                    side_effect=cross_on_authorization_scan,
                ):
                    denial = self._take_denial(authority, invocation)
                self.assertGreaterEqual(calls, 2)
                self.assertIs(
                    denial.result.mismatch,
                    ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                )

    def test_diagnostic_fstat_crossing_deadline_cannot_commit_invalid_scan(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, invocation = self._stage(
                stores,
                "read_finish",
            )
            state = replay_store_module._REPLAYS[authority]
            reader = replay_store_module._READERS[
                state["companion_reader"]
            ]
            reader.terminal = None
            state["state"] = "begin_diagnostic"
            reader_snapshot = self._owned_reader_snapshot(authority)
            deadline = (
                stores.phase1_manifest.required_retention_until_ns
            )
            stores.clock.now_ns = deadline - 1
            original_fstat = os.fstat
            target_fstats = 0

            def cross_after_diagnostic_fstat(fd: int) -> os.stat_result:
                nonlocal target_fstats
                observed = original_fstat(fd)
                if fd == reader.fd and target_fstats == 0:
                    target_fstats += 1
                    stores.clock.now_ns = deadline
                return observed

            with (
                mock.patch.object(
                    replay_store_module,
                    "_read_replay_begin_diagnostic_frame",
                    side_effect=(
                        replay_store_module._ReplayDiagnosticTorn()
                    ),
                ),
                mock.patch.object(
                    replay_store_module.os,
                    "fstat",
                    side_effect=cross_after_diagnostic_fstat,
                ),
            ):
                denial = self._take_denial(authority, invocation)

            self.assertEqual(target_fstats, 1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertNotEqual(state["state"], "finish_ready")
            self.assertNotIn("finish_material", state)
            self._assert_owned_readers_revoked(reader_snapshot)
            self.assertFalse(stores.paths()["expert_marker"].exists())
            self.assertFalse(stores.paths()["expert_journal"].exists())

    def test_decoded_companion_group_is_not_exposed_after_deadline(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, invocation = self._stage(
                stores,
                "read_companion",
            )
            state = replay_store_module._REPLAYS[authority]
            reader_snapshot = self._owned_reader_snapshot(authority)
            deadline = (
                stores.phase1_manifest.required_retention_until_ns
            )
            stores.clock.now_ns = deadline - 1
            original_decode = (
                replay_store_module
                .decode_expert_group_frame_structural
            )
            decoded = 0

            def cross_after_decode(frame: bytes) -> object:
                nonlocal decoded
                value = original_decode(frame)
                decoded += 1
                stores.clock.now_ns = deadline
                return value

            with mock.patch.object(
                replay_store_module,
                "decode_expert_group_frame_structural",
                side_effect=cross_after_decode,
            ):
                denial = self._take_denial(authority, invocation)

            self.assertEqual(decoded, 1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertNotIn("current_group", state)
            self.assertNotEqual(state["state"], "pair_complete")
            self._assert_owned_readers_revoked(reader_snapshot)

    def test_decoded_phase1_parent_is_not_exposed_after_deadline(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, invocation = self._stage(
                stores,
                "read_evidence",
            )
            state = replay_store_module._REPLAYS[authority]
            reader_snapshot = self._owned_reader_snapshot(authority)
            deadline = (
                stores.phase1_manifest.required_retention_until_ns
            )
            stores.clock.now_ns = deadline - 1
            wal_module = importlib.import_module("tennis_v1.wal")
            original_decode = wal_module.decode_record
            decoded_raw = 0

            def cross_after_raw_decode(
                metadata: bytes,
                payload: bytes,
            ) -> PersistedEvent:
                nonlocal decoded_raw
                event = original_decode(metadata, payload)
                if (
                    event.record_kind is RecordKind.RAW
                    and event.ingest_seq == 2
                ):
                    decoded_raw += 1
                    stores.clock.now_ns = deadline
                return event

            with mock.patch.object(
                wal_module,
                "decode_record",
                side_effect=cross_after_raw_decode,
            ):
                denial = self._take_denial(authority, invocation)

            self.assertEqual(decoded_raw, 1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertEqual(state["evidence_index"], 0)
            self.assertNotIn("current_parent", state)
            self.assertNotIn("last_phase1_ingest_seq", state)
            self.assertNotIn(
                "last_evidence_parent_ingest_seq",
                state,
            )
            self.assertNotEqual(state["state"], "evidence_parent_ready")
            self._assert_owned_readers_revoked(reader_snapshot)

    def test_construction_token_is_not_exposed_after_deadline(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            deadline = (
                stores.phase1_manifest.required_retention_until_ns
            )
            stores.clock.now_ns = deadline - 1
            original_token = replay_store_module._token
            constructed: list[object] = []

            def cross_after_construction(
                token_type: type[object],
            ) -> object:
                token = original_token(token_type)
                if token_type is ExpertReplayConstructionAuthorityV1:
                    constructed.append(token)
                    stores.clock.now_ns = deadline
                return token

            with mock.patch.object(
                replay_store_module,
                "_token",
                side_effect=cross_after_construction,
            ):
                denial = stores.issue_replay()

            self.assertIs(type(denial), ExpertReplayDeniedV1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertEqual(len(constructed), 1)
            self.assertNotIn(
                constructed[0],
                replay_store_module._REPLAYS,
            )

    def test_authorization_token_is_not_exposed_after_deadline(
        self,
    ) -> None:
        for seam in ("issue_begin", "issue_parent", "issue_finish"):
            with (
                self.subTest(authorization_return=seam),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                stores.close_companion()
                authority, invocation = self._stage(stores, seam)
                state = replay_store_module._REPLAYS[authority]
                reader_snapshot = self._owned_reader_snapshot(authority)
                deadline = (
                    stores.phase1_manifest.required_retention_until_ns
                )
                stores.clock.now_ns = deadline - 1
                original_create = (
                    replay_store_module
                    ._create_retention_replay_authorization_v1
                )
                constructed: list[object] = []

                def cross_after_construction(
                    **values: object,
                ) -> object:
                    token = original_create(**values)
                    constructed.append(token)
                    stores.clock.now_ns = deadline
                    return token

                with mock.patch.object(
                    replay_store_module,
                    "_create_retention_replay_authorization_v1",
                    side_effect=cross_after_construction,
                ):
                    denial = self._take_denial(authority, invocation)

                self.assertEqual(len(constructed), 1)
                self.assertIs(
                    denial.result.mismatch,
                    ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                )
                self.assertIsNone(state["outstanding"])
                self.assertNotIn(
                    state["state"],
                    {
                        "begin_auth_outstanding",
                        "parent_auth_outstanding",
                        "finish_auth_outstanding",
                    },
                )
                self._assert_owned_readers_revoked(reader_snapshot)

    def test_authorization_token_is_not_exposed_after_constructor_crossing_identity_or_environment_loss(
        self,
    ) -> None:
        for fault in ("identity", "environment"):
            with (
                self.subTest(constructor_crossing=fault),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                stores.close_companion()
                authority, invocation = self._stage(
                    stores,
                    "issue_begin",
                )
                state = replay_store_module._REPLAYS[authority]
                reader_snapshot = self._owned_reader_snapshot(authority)
                original_create = (
                    replay_store_module
                    ._create_retention_replay_authorization_v1
                )
                original_environment = (
                    replay_store_module._installed_environment
                )
                constructed: list[object] = []

                def cross_after_construction(
                    **values: object,
                ) -> object:
                    token = original_create(**values)
                    constructed.append(token)
                    if fault == "identity":
                        self._replace_named_entry(
                            stores.paths()[
                                ExpertReplayDiagnosticRoleV1
                                .PHASE1_MARKER.value
                            ]
                        )
                    return token

                def environment_after_construction(
                    root: object,
                    manifest: object,
                    *,
                    gate: object = None,
                ) -> tuple[object, ...]:
                    current, normalizers, structural, event = (
                        original_environment(
                            root,
                            manifest,
                            gate=gate,
                        )
                    )
                    if fault == "environment" and constructed:
                        current = replace(
                            current,
                            runtime_code_sha256=SHA_D,
                        )
                    return current, normalizers, structural, event

                with (
                    mock.patch.object(
                        replay_store_module,
                        "_create_retention_replay_authorization_v1",
                        side_effect=cross_after_construction,
                    ),
                    mock.patch.object(
                        replay_store_module,
                        "_installed_environment",
                        side_effect=environment_after_construction,
                    ),
                ):
                    denial = self._take_denial(authority, invocation)

                self.assertEqual(len(constructed), 1)
                self.assertIs(
                    denial.result.mismatch,
                    (
                        ExpertReplayMismatchV1
                        .EVIDENCE_IDENTITY_MISMATCH
                        if fault == "identity"
                        else ExpertReplayMismatchV1
                        .CURRENT_ENVIRONMENT_MISMATCH
                    ),
                )
                self.assertIsNone(state["outstanding"])
                self.assertNotEqual(
                    state["state"],
                    "begin_auth_outstanding",
                )
                self._assert_owned_readers_revoked(reader_snapshot)

    def test_environment_scan_stops_before_second_source_file_after_deadline(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, invocation = self._stage(stores, "issue_begin")
            state = replay_store_module._REPLAYS[authority]
            reader_snapshot = self._owned_reader_snapshot(authority)
            deadline = (
                stores.phase1_manifest.required_retention_until_ns
            )
            stores.clock.now_ns = deadline - 1
            state["root"].source_content_cache.clear()
            original_read = replay_store_module._read_source_file
            entered: list[str] = []
            completed: list[str] = []

            def cross_after_first_source(
                root: object,
                logical: str,
                **keywords: object,
            ) -> bytes:
                entered.append(logical)
                content = original_read(
                    root,
                    logical,
                    **keywords,
                )
                completed.append(logical)
                if len(completed) == 1:
                    stores.clock.now_ns = deadline
                return content

            with mock.patch.object(
                replay_store_module,
                "_read_source_file",
                side_effect=cross_after_first_source,
            ):
                denial = self._take_denial(authority, invocation)

            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertEqual(len(completed), 1)
            self.assertLessEqual(len(entered), 2)
            self._assert_owned_readers_revoked(reader_snapshot)

    def test_access_loss_and_prepare_bootstrap_failures_purge_companion(
        self,
    ) -> None:
        cases = (
            ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
            ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
        )
        for expected in cases:
            with (
                self.subTest(active_access_loss=expected),
                _RealReplayStores() as stores,
                ExitStack() as stack,
            ):
                stores.append_parent()
                stores.close_companion()
                authority, invocation = self._stage(
                    stores,
                    "issue_begin",
                )
                if expected is (
                    ExpertReplayMismatchV1
                    .RETENTION_AUTHORIZATION_MISMATCH
                ):
                    original_authorize = (
                        ProviderPersistenceAuthorizer
                        .authorize_analysis
                    )

                    def deny(
                        instance: ProviderPersistenceAuthorizer,
                    ) -> object:
                        if instance is stores.authorizer:
                            raise RuntimeError("denied")
                        return original_authorize(instance)

                    stack.enter_context(
                        mock.patch.object(
                            ProviderPersistenceAuthorizer,
                            "authorize_analysis",
                            deny,
                        )
                    )
                elif expected is (
                    ExpertReplayMismatchV1
                    .EVIDENCE_IDENTITY_MISMATCH
                ):
                    self._replace_named_entry(
                        stores.paths()["expert_marker"]
                    )
                else:
                    original_environment = (
                        replay_store_module._installed_environment
                    )

                    def drift(
                        *args: object,
                        **keywords: object,
                    ) -> tuple[object, ...]:
                        current, normalizers, structural, event = (
                            original_environment(
                                *args,
                                **keywords,
                            )
                        )
                        return (
                            replace(
                                current,
                                runtime_code_sha256=SHA_D,
                            ),
                            normalizers,
                            structural,
                            event,
                        )

                    stack.enter_context(
                        mock.patch.object(
                            replay_store_module,
                            "_installed_environment",
                            side_effect=drift,
                        )
                    )
                denial = self._take_denial(authority, invocation)
                self.assertIs(denial.result.mismatch, expected)
                self.assertFalse(
                    stores.paths()["expert_marker"].exists()
                )
                self.assertFalse(
                    stores.paths()["expert_journal"].exists()
                )
                self.assertFalse(
                    stores.paths()["expert_reserve"].exists()
                )
                self.assertTrue(stores.paths()["phase1_marker"].exists())
                self.assertTrue(stores.paths()["phase1_wal"].exists())

    def test_pre_first_prepare_observation_replacement_establishes_baseline(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority = stores.issue_replay()
            self.assertIs(
                type(authority),
                ExpertReplayConstructionAuthorityV1,
            )
            # Readerless issuance is forbidden to observe Phase-1 entries.
            # A same-byte replacement before prepare's first lawful stat is
            # therefore the baseline, not a provable replacement.
            self._replace_named_entry(stores.paths()["phase1_marker"])
            ready = replay_store_facade.prepare_expert_replay_begin(
                authority
            )
            self.assertIs(type(ready), ExpertReplayBeginReadyV1)
            self.assertTrue(stores.paths()["expert_marker"].exists())
            self.assertTrue(stores.paths()["expert_journal"].exists())
            self.assertFalse(stores.paths()["expert_reserve"].exists())
            replay_store_facade.abort_expert_replay_construction(
                authority
            )

    def test_physical_root_and_account_lock_loss_are_typed_item_6(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority = stores.issue_replay()
            self.assertIs(
                type(authority),
                ExpertReplayConstructionAuthorityV1,
            )
            state = replay_store_module._REPLAYS[authority]
            root = state["root"]
            cached_sample = state["_last_sampled_wall_ns"]
            root.last_validation_sampled_wall_ns = cached_sample + 1
            with mock.patch.object(
                replay_store_module,
                "_sample_replay_prepare_wall_ns",
                side_effect=ValueError(
                    "expert_root_authority_invalid"
                ),
            ):
                denial = (
                    replay_store_facade.prepare_expert_replay_begin(
                        authority
                    )
                )
            self.assertIs(type(denial), ExpertReplayDeniedV1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            )
            self.assertEqual(
                denial.proof.final_sampled_wall_ns,
                cached_sample,
            )
            self.assertEqual(denial.proof.file_proofs, ())

        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority = stores.issue_replay()
            self.assertIs(
                type(authority),
                ExpertReplayConstructionAuthorityV1,
            )
            state = replay_store_module._REPLAYS[authority]
            root = state["root"]
            original_source_fd = root.source_root_fd
            replacement_fd = os.open(
                stores.phase1_fixture.root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            root.source_root_fd = replacement_fd
            try:
                denial = (
                    replay_store_facade.prepare_expert_replay_begin(
                        authority
                    )
                )
            finally:
                root.source_root_fd = original_source_fd
                os.close(replacement_fd)
            self.assertIs(type(denial), ExpertReplayDeniedV1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            )
            self.assertEqual(denial.proof.file_proofs, ())
            self.assertEqual(state["state"], "denied_closed")
            self.assertFalse(stores.paths()["expert_marker"].exists())
            self.assertFalse(stores.paths()["expert_journal"].exists())

        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, invocation = self._stage(stores, "issue_begin")
            state = replay_store_module._REPLAYS[authority]
            reader_snapshot = self._owned_reader_snapshot(authority)
            cached_sample = state["_last_sampled_wall_ns"]
            state["root"].last_validation_sampled_wall_ns = (
                cached_sample + 1
            )

            def lost_account_lock(_: object) -> int:
                raise RetentionError("expert_state_root_grant_stale")

            with mock.patch.object(
                replay_store_module,
                "_phase1_sample_wall_ns",
                side_effect=lost_account_lock,
            ):
                denial = self._take_denial(authority, invocation)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            )
            self.assertEqual(
                denial.proof.final_sampled_wall_ns,
                cached_sample,
            )
            self.assertEqual(denial.proof.file_proofs, ())
            self.assertIsNone(state["outstanding"])
            self._assert_owned_readers_revoked(reader_snapshot)
            self.assertFalse(stores.paths()["expert_marker"].exists())
            self.assertFalse(stores.paths()["expert_journal"].exists())

        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, invocation = self._stage(stores, "issue_begin")
            state = replay_store_module._REPLAYS[authority]
            reader_snapshot = self._owned_reader_snapshot(authority)
            replay_store_module._fatal_root(state["root"])
            self.assertTrue(state["root_failed"])
            self.assertTrue(state["root_failed_purge_proven"])
            self.assertFalse(state["root_failed_purge_uncertain"])
            denial = self._take_denial(authority, invocation)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            )
            self.assertEqual(denial.proof.file_proofs, ())
            self._assert_owned_readers_revoked(reader_snapshot)
            self.assertFalse(stores.paths()["expert_marker"].exists())
            self.assertFalse(stores.paths()["expert_journal"].exists())
            self.assertFalse(stores.paths()["expert_reserve"].exists())

        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, invocation = self._stage(stores, "issue_begin")
            state = replay_store_module._REPLAYS[authority]
            root = state["root"]
            original_owner = root.owner_thread
            root.owner_thread = threading.Thread()
            try:
                denial = self._take_denial(authority, invocation)
            finally:
                root.owner_thread = original_owner
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            )
            self.assertEqual(denial.proof.file_proofs, ())
            self.assertFalse(stores.paths()["expert_marker"].exists())
            self.assertFalse(stores.paths()["expert_journal"].exists())

    def test_fatal_root_direct_purge_failure_never_claims_typed_denial(
        self,
    ) -> None:
        for seam in ("prepare", "issue_begin"):
            with (
                self.subTest(fatal_root_purge_failure=seam),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                stores.close_companion()
                if seam == "prepare":
                    authority = stores.issue_replay()
                    self.assertIs(
                        type(authority),
                        ExpertReplayConstructionAuthorityV1,
                    )
                    invocation = lambda: (
                        replay_store_facade.prepare_expert_replay_begin(
                            authority
                        )
                    )
                else:
                    authority, invocation = self._stage(
                        stores,
                        "issue_begin",
                    )
                state = replay_store_module._REPLAYS[authority]
                original_unlink = (
                    replay_store_module._unlink_if_present
                )
                direct_failure_injected = False

                def fail_direct_purge_once(
                    directory_fd: int,
                    basename: str,
                ) -> None:
                    nonlocal direct_failure_injected
                    if not direct_failure_injected:
                        direct_failure_injected = True
                        raise OSError("forced_direct_purge_failure")
                    original_unlink(directory_fd, basename)

                with mock.patch.object(
                    replay_store_module,
                    "_unlink_if_present",
                    side_effect=fail_direct_purge_once,
                ):
                    replay_store_module._fatal_root(state["root"])
                self.assertTrue(direct_failure_injected)
                self.assertTrue(state["root_failed"])
                self.assertFalse(state["root_failed_purge_proven"])
                self.assertTrue(state["root_failed_purge_uncertain"])
                with self.assertRaisesRegex(
                    OSError,
                    "^expert_replay_close_uncertain$",
                ):
                    invocation()
                self.assertTrue(state["closed"])
                self.assertEqual(state["state"], "aborted_closed")
                self.assertNotIn("denial", state)
                self.assertIsNone(state["outstanding"])
                for name in (
                    "accumulator",
                    "current_group",
                    "current_group_sha256",
                    "current_parent",
                    "current_parent_record_sha256",
                    "current_payload_seals",
                    "evidence",
                    "expected_environment",
                    "expert_manifest",
                    "finish_material",
                    "identity_set",
                    "outstanding_authorization_sha256",
                ):
                    self.assertNotIn(name, state)

    def test_stable_phase1_content_failure_is_item_1_not_item_6(
        self,
    ) -> None:
        class CapturedMismatch(Exception):
            def __init__(self, mismatch: ExpertReplayMismatchV1) -> None:
                self.mismatch = mismatch

        def invalid_records() -> object:
            raise JournalValidationError("stable content invalid")
            yield None

        def capture_denial(
            state: object,
            *,
            mismatch: ExpertReplayMismatchV1,
            sampled: int,
            file_proofs: tuple[object, ...] = (),
        ) -> None:
            raise CapturedMismatch(mismatch)

        state = {
            "phase1_records": iter(invalid_records()),
            "manifest": object(),
        }
        with (
            mock.patch.object(
                replay_store_module,
                "_replay_full_integrity_gate",
                return_value=None,
            ),
            mock.patch.object(
                replay_store_module,
                "_replay_access_gate",
                return_value=1,
            ),
            mock.patch.object(
                replay_store_module,
                "_raise_contextual_replay_denial",
                side_effect=capture_denial,
            ),
            self.assertRaises(CapturedMismatch) as caught,
        ):
            replay_store_module._next_replay_phase1_parent(state)
        self.assertIs(
            caught.exception.mismatch,
            ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
        )

    def test_root_loss_inside_companion_read_and_finish_is_typed_item_6(
        self,
    ) -> None:
        for seam, target_name in (
            ("read_companion", "read_next_expert_group"),
            ("read_finish", "read_expert_terminal_and_summary"),
        ):
            with (
                self.subTest(root_loss_inside=seam),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                stores.close_companion()
                authority, invocation = self._stage(stores, seam)
                state = replay_store_module._REPLAYS[authority]
                root = state["root"]
                reader_snapshot = self._owned_reader_snapshot(authority)
                original_source_fd = root.source_root_fd
                replacement_fd = os.open(
                    stores.phase1_fixture.root,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                original_target = getattr(
                    replay_store_module,
                    target_name,
                )

                def lose_root_then_read(
                    *args: object,
                    **keywords: object,
                ) -> object:
                    root.source_root_fd = replacement_fd
                    return original_target(*args, **keywords)

                try:
                    with mock.patch.object(
                        replay_store_module,
                        target_name,
                        side_effect=lose_root_then_read,
                    ):
                        denial = self._take_denial(
                            authority,
                            invocation,
                        )
                finally:
                    root.source_root_fd = original_source_fd
                    os.close(replacement_fd)
                self.assertIs(
                    denial.result.mismatch,
                    ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
                )
                self.assertEqual(denial.proof.file_proofs, ())
                self.assertIsNone(state["outstanding"])
                self._assert_owned_readers_revoked(reader_snapshot)

        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, invocation = self._stage(
                stores,
                "read_companion",
            )
            state = replay_store_module._REPLAYS[authority]
            root = state["root"]
            reader_snapshot = self._owned_reader_snapshot(authority)
            original_read = replay_store_module.read_next_expert_group

            def lose_generation_after_outer_gate(
                reader: object,
            ) -> object:
                root.generation += 1
                return original_read(reader)

            with mock.patch.object(
                replay_store_module,
                "read_next_expert_group",
                side_effect=lose_generation_after_outer_gate,
            ):
                denial = self._take_denial(authority, invocation)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            )
            self.assertEqual(denial.proof.file_proofs, ())
            self._assert_owned_readers_revoked(reader_snapshot)

    def test_authorizer_failure_and_failed_resample_uses_cached_item_6(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, invocation = self._stage(stores, "issue_begin")
            state = replay_store_module._REPLAYS[authority]
            cached_before = state["_last_sampled_wall_ns"]
            original_authorize = (
                ProviderPersistenceAuthorizer.authorize_analysis
            )
            original_sample = (
                replay_store_module._phase1_sample_wall_ns
            )
            poison_sample = False

            def authorize_then_poison(
                instance: ProviderPersistenceAuthorizer,
            ) -> object:
                nonlocal poison_sample
                if instance is stores.authorizer:
                    original_authorize(instance)
                    poison_sample = True
                    raise RuntimeError("authorization_lost")
                return original_authorize(instance)

            def fail_after_authorizer(capability: object) -> int:
                if poison_sample:
                    raise RetentionError(
                        "expert_retention_clock_capability_stale"
                    )
                return original_sample(capability)

            with (
                mock.patch.object(
                    ProviderPersistenceAuthorizer,
                    "authorize_analysis",
                    new=authorize_then_poison,
                ),
                mock.patch.object(
                    replay_store_module,
                    "_phase1_sample_wall_ns",
                    side_effect=fail_after_authorizer,
                ),
            ):
                denial = self._take_denial(authority, invocation)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            )
            self.assertGreaterEqual(
                denial.proof.final_sampled_wall_ns,
                cached_before,
            )
            self.assertEqual(
                denial.proof.final_sampled_wall_ns,
                state["_last_sampled_wall_ns"],
            )
            self.assertEqual(denial.proof.file_proofs, ())
            self.assertFalse(stores.paths()["expert_marker"].exists())
            self.assertFalse(stores.paths()["expert_journal"].exists())

    def test_readerless_construction_rejects_wrong_thread_and_inactive_root(
        self,
    ) -> None:
        with _RealReplayStores() as stores:
            root = replay_store_module._ROOTS[stores.authority]
            before = set(replay_store_module._REPLAYS)
            outcomes: list[BaseException] = []

            def issue_from_wrong_thread() -> None:
                try:
                    stores.issue_replay()
                except BaseException as error:
                    outcomes.append(error)

            with mock.patch.object(
                replay_store_module,
                "_named_file_identity_observation",
                side_effect=AssertionError(
                    "invalid construction inspected files"
                ),
            ):
                worker = threading.Thread(
                    target=issue_from_wrong_thread
                )
                worker.start()
                worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(len(outcomes), 1)
                self.assertIs(type(outcomes[0]), ValueError)
                root.active = False
                try:
                    with self.assertRaisesRegex(
                        ValueError,
                        "^expert_root_authority_invalid$",
                    ):
                        stores.issue_replay()
                finally:
                    root.active = True
            self.assertEqual(set(replay_store_module._REPLAYS), before)

            authority = stores.issue_replay()
            self.assertIs(
                type(authority),
                ExpertReplayConstructionAuthorityV1,
            )
            abort_outcomes: list[BaseException] = []

            def abort_from_wrong_thread() -> None:
                try:
                    replay_store_facade.abort_expert_replay_construction(
                        authority
                    )
                except BaseException as error:
                    abort_outcomes.append(error)

            worker = threading.Thread(target=abort_from_wrong_thread)
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(abort_outcomes), 1)
            self.assertIs(type(abort_outcomes[0]), ValueError)
            state = replay_store_module._REPLAYS[authority]
            self.assertFalse(state["closed"])
            self.assertEqual(state["state"], "new")
            replay_store_facade.abort_expert_replay_construction(
                authority
            )

    @staticmethod
    def _replace_named_entry(path: Path) -> None:
        replacement = path.with_name(path.name + ".replacement")
        replacement.write_bytes(path.read_bytes())
        os.chmod(replacement, 0o600)
        os.replace(replacement, path)

    def test_readerless_issuance_and_prepare_prioritize_items_1_to_10_before_any_companion_read(
        self,
    ) -> None:
        begin, _, _ = self._pure()
        with _RealReplayStores() as stores:
            with (
                mock.patch.object(
                    replay_store_module,
                    "_read_prepare_replay_named_content",
                    side_effect=AssertionError(
                        "readerless issuance read a named entry"
                    ),
                ) as named_read,
                mock.patch.object(
                    JournalReader,
                    "open",
                    side_effect=AssertionError(
                        "readerless issuance opened Phase-1"
                    ),
                ) as phase1_open,
            ):
                authority = stores.issue_replay()
            self.assertIs(
                type(authority),
                ExpertReplayConstructionAuthorityV1,
            )
            named_read.assert_not_called()
            phase1_open.assert_not_called()
            state = replay_store_module._REPLAYS[authority]
            self.assertEqual(state["state"], "new")
            self.assertNotIn("phase1_reader", state)
            self.assertNotIn("companion_reader", state)
            replay_store_facade.abort_expert_replay_construction(authority)

        with _RealReplayStores() as stores:
            stores.clock.now_ns = (
                stores.phase1_manifest.required_retention_until_ns
            )
            with mock.patch.object(
                replay_store_module,
                "_read_prepare_replay_named_content",
                side_effect=AssertionError("deadline issuance read a file"),
            ):
                denial = stores.issue_replay()
            self.assertIs(type(denial), ExpertReplayDeniedV1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertEqual(denial.proof.file_proofs, ())
            self.assertIsNone(denial.proof.companion_scan)
            self.assertIsNone(
                denial.proof.phase1_replay_summary_sha256
            )
            self.assertEqual(
                (
                    denial.result.evidence_raw_count,
                    denial.result.expert_group_count,
                    denial.result.expert_record_count,
                ),
                (0, 0, 0),
            )

        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            exact = replay_store_module.replay_exact(
                expected_session_manifest_sha256=(
                    session_manifest_sha256(stores.phase1_manifest)
                ),
                persistence_authorizer=stores.authorizer,
                coordinator=stores.coordinator,
            )
            nonexact = replace(
                exact,
                exact_replay=False,
                replay_mismatch=ReplayMismatch.STATE,
            )
            authority = stores.issue_replay()
            with (
                mock.patch.object(
                    replay_store_module,
                    "replay_exact",
                    return_value=nonexact,
                ),
                mock.patch.object(
                    replay_store_module,
                    "_decode_expert_marker",
                    side_effect=AssertionError(
                        "item 2 read companion marker"
                    ),
                ) as companion_decode,
            ):
                denial = (
                    replay_store_facade.prepare_expert_replay_begin(
                        authority
                    )
                )
            companion_decode.assert_not_called()
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.EVIDENCE_REPLAY_NOT_EXACT,
            )

        with _RealReplayStores() as stores:
            stores.halt_phase1()
            authority = stores.issue_replay()
            with mock.patch.object(
                replay_store_module,
                "_decode_expert_marker",
                side_effect=AssertionError(
                    "item 3 read companion marker"
                ),
            ) as companion_decode:
                denial = (
                    replay_store_facade.prepare_expert_replay_begin(
                        authority
                    )
                )
            companion_decode.assert_not_called()
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.EVIDENCE_TERMINAL_NOT_CLEAN,
            )

        # Of items 4-10, 6/7/8/9 are lawfully observable without an
        # expert-manifest byte: bound authority, deadline, the Phase-1
        # identities just established by replay_exact, and the installed
        # environment already sealed by journal creation.  Every case also
        # carries a corrupt unread companion and tripwires its first read.
        for expected in (
            ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
            ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
        ):
            with (
                self.subTest(real_prepare_precedence=expected),
                _RealReplayStores() as stores,
            ):
                stores.close_companion()
                journal = stores.paths()["expert_journal"]
                descriptor = os.open(journal, os.O_WRONLY)
                try:
                    os.pwrite(descriptor, b"X", 0)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                authority = stores.issue_replay()
                self.assertIs(
                    type(authority),
                    ExpertReplayConstructionAuthorityV1,
                )
                state = replay_store_module._REPLAYS[authority]
                self.assertNotIn("expert_manifest", state)
                self.assertNotIn("companion_reader", state)
                stack = ExitStack()
                with stack:
                    if expected is (
                        ExpertReplayMismatchV1
                        .RETENTION_AUTHORIZATION_MISMATCH
                    ):
                        original_authorize = (
                            ProviderPersistenceAuthorizer
                            .authorize_analysis
                        )

                        def deny_prepare(
                            instance: ProviderPersistenceAuthorizer,
                        ) -> object:
                            if instance is stores.authorizer:
                                raise RuntimeError("denied")
                            return original_authorize(instance)

                        stack.enter_context(
                            mock.patch.object(
                                ProviderPersistenceAuthorizer,
                                "authorize_analysis",
                                deny_prepare,
                            )
                        )
                    elif expected is (
                        ExpertReplayMismatchV1
                        .RETENTION_DEADLINE_REACHED
                    ):
                        stores.clock.now_ns = (
                            stores.phase1_manifest
                            .required_retention_until_ns
                        )
                    elif expected is (
                        ExpertReplayMismatchV1
                        .EVIDENCE_IDENTITY_MISMATCH
                    ):
                        original_identities = (
                            replay_store_module
                            ._guarded_phase1_evidence_file_identities
                        )
                        identity_captured = False

                        def replace_after_identity_capture(
                            *args: object,
                            **keywords: object,
                        ) -> object:
                            nonlocal identity_captured
                            identities = original_identities(
                                *args,
                                **keywords,
                            )
                            if not identity_captured:
                                identity_captured = True
                                self._replace_named_entry(
                                    stores.paths()["phase1_marker"]
                                )
                            return identities

                        stack.enter_context(
                            mock.patch.object(
                                replay_store_module,
                                "_guarded_phase1_evidence_file_identities",
                                side_effect=(
                                    replace_after_identity_capture
                                ),
                            )
                        )
                    else:
                        original_environment = (
                            replay_store_module._installed_environment
                        )

                        def drift_before_companion(
                            root: object,
                            manifest: object,
                            *,
                            gate: object = None,
                        ) -> tuple[object, ...]:
                            current, normalizers, structural, event = (
                                original_environment(
                                    root,
                                    manifest,
                                    gate=gate,
                                )
                            )
                            return (
                                replace(
                                    current,
                                    runtime_code_sha256=SHA_D,
                                ),
                                normalizers,
                                structural,
                                event,
                            )

                        stack.enter_context(
                            mock.patch.object(
                                replay_store_module,
                                "_installed_environment",
                                side_effect=drift_before_companion,
                            )
                        )
                    companion_read = stack.enter_context(
                        mock.patch.object(
                            replay_store_module,
                            "_read_prepare_replay_named_content",
                            side_effect=AssertionError(
                                "higher-priority prepare fault read companion"
                            ),
                        )
                    )
                    denial = self._take_denial(
                        authority,
                        lambda: (
                            replay_store_facade
                            .prepare_expert_replay_begin(authority)
                        ),
                    )
                companion_read.assert_not_called()
                self.assertIs(denial.result.mismatch, expected)
                self.assertEqual(
                    replay_store_module._REPLAYS[authority]["state"],
                    "denied_closed",
                )
                if expected is (
                    ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH
                ):
                    self.assertEqual(
                        tuple(
                            proof.role
                            for proof in denial.proof.file_proofs
                        ),
                        (
                            ExpertReplayDiagnosticRoleV1
                            .PHASE1_MARKER,
                        ),
                    )
                else:
                    self.assertEqual(denial.proof.file_proofs, ())

        with _RealReplayStores() as stores:
            stores.close_phase1()
            journal = stores.paths()["expert_journal"]
            descriptor = os.open(journal, os.O_WRONLY)
            try:
                os.pwrite(descriptor, b"X", 0)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            authority = stores.issue_replay()
            denial = replay_store_facade.prepare_expert_replay_begin(
                authority
            )
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
            )
            self.assertTrue(denial.proof.file_proofs)

        with _RealReplayStores() as stores:
            stores.close_companion()
            authority, ready = self._prepare(stores)
            collected = self._collect(stores)
            token = (
                replay_store_facade.issue_begin_replay_authorization(
                    authority
                )
            )
            cases = (
                (
                    ExpertReplayMismatchV1.EVIDENCE_SESSION_MISMATCH,
                    ready.manifest,
                    _rehash_authorization(
                        token,
                        session_id=(
                            "22222222-2222-4222-8222-222222222222"
                        ),
                    ),
                    collected.current,
                ),
                (
                    ExpertReplayMismatchV1.EVIDENCE_MANIFEST_MISMATCH,
                    _rehash_manifest(
                        ready.manifest,
                        provider_id="provider-b",
                    ),
                    token,
                    collected.current,
                ),
                (
                    ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
                    ready.manifest,
                    _rehash_authorization(
                        token,
                        authorized_operation="finish",
                    ),
                    collected.current,
                ),
                (
                    ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                    ready.manifest,
                    _rehash_authorization(
                        token,
                        final_sampled_wall_ns=token.common_deadline_ns,
                    ),
                    collected.current,
                ),
                (
                    ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
                    ready.manifest,
                    _rehash_authorization(
                        token,
                        evidence_marker_identity=_identity(
                            "phase1_marker",
                            session_anchor_sha256=SHA_D,
                            inode=91,
                        ),
                    ),
                    collected.current,
                ),
                (
                    ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
                    ready.manifest,
                    token,
                    replace(
                        collected.current,
                        runtime_code_sha256=SHA_D,
                    ),
                ),
                (
                    ExpertReplayMismatchV1.RETENTION_BINDING_MISMATCH,
                    ready.manifest,
                    _rehash_authorization(
                        token,
                        common_deadline_ns=(
                            token.common_deadline_ns + 1
                        ),
                    ),
                    collected.current,
                ),
            )
            for expected, candidate_manifest, candidate, current in cases:
                with self.subTest(begin_precedence=expected):
                    accumulator = begin(
                        manifest=candidate_manifest,
                        current_environment=current,
                        universe=stores.universe,
                        policy=stores.policy,
                        evidence=ready.evidence,
                        authorization=candidate,
                    )
                    self.assertIs(accumulator.mismatch, expected)

        service_module = _module(RUNTIME_MODULE)
        service = service_module.replay_expert_session

        # Item 5 is reachable through a structurally valid, self-consistent
        # companion manifest whose provider relation differs from Phase 1.
        # Preserve a corrupt unread tail to prove the earlier mismatch wins.
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            provider_values = {
                item.name: getattr(
                    stores.manifest.provider_domain,
                    item.name,
                )
                for item in fields(stores.manifest.provider_domain)
                if item.name != "provider_domain_binding_sha256"
            }
            provider_values.update(
                {
                    "provider_id": "provider-alternate",
                    "source_lineage_id": "lineage-alternate",
                }
            )
            provider_values["provider_source_lineage_sha256"] = (
                compute_expert_provider_source_lineage_sha256(
                    provider_values["provider_id"],
                    provider_values["product_tier"],
                    provider_values["source_lineage_id"],
                    provider_values[
                        "provider_manifest_canonical_sha256"
                    ],
                )
            )
            provider_values["provider_domain_binding_sha256"] = (
                compute_expert_provider_domain_binding_sha256(
                    **provider_values
                )
            )
            alternate_domain = ExpertProviderDomainBindingV1(
                **provider_values
            )
            retention_values = {
                item.name: (
                    getattr(stores.manifest.retention, item.name) + 1
                    if item.name == "retention_delete_by_ns"
                    else getattr(stores.manifest.retention, item.name)
                )
                for item in fields(stores.manifest.retention)
                if item.name != "retention_binding_sha256"
            }
            alternate_retention = ExpertRetentionBindingV1(
                **retention_values,
                retention_binding_sha256=(
                    compute_expert_retention_binding_sha256(
                        **retention_values
                    )
                ),
            )
            alternate_manifest = _rehash_manifest(
                stores.manifest,
                provider_id=provider_values["provider_id"],
                source_lineage_id=provider_values["source_lineage_id"],
                provider_domain=alternate_domain,
                retention=alternate_retention,
            )
            ExpertSessionManifestV1.__post_init__(alternate_manifest)

            import inci_tennis_expert.journal_codec as codec_module
            journal = stores.paths()["expert_journal"]
            original_bytes = journal.read_bytes()
            header = original_bytes[
                : codec_module.EXPERT_FILE_HEADER_BYTES
            ]
            journal.write_bytes(
                header
                + codec_module.encode_expert_manifest_frame(
                    alternate_manifest
                )
                + b"corrupt-unread-tail"
            )
            os.chmod(journal, 0o600)
            marker_path = stores.paths()["expert_marker"]
            marker = replay_store_module._decode_expert_marker(
                marker_path.read_bytes()
            )
            marker["expert_manifest_sha256"] = (
                alternate_manifest.manifest_sha256
            )
            marker["retention_binding_sha256"] = (
                alternate_retention.retention_binding_sha256
            )
            marker["retention_delete_by_ns"] = (
                alternate_retention.retention_delete_by_ns
            )
            marker_path.write_bytes(
                replay_store_module._encode_expert_marker(
                    {
                        name: marker[name]
                        for name in replay_store_module.EXPERT_MARKER_FIELDS
                    }
                )
            )
            os.chmod(marker_path, 0o600)

            with (
                mock.patch.object(
                    service_module,
                    "read_next_replay_evidence_parent",
                    side_effect=AssertionError(
                        "item 5 read an evidence parent"
                    ),
                ) as evidence_read,
                mock.patch.object(
                    service_module,
                    "read_next_replay_companion_group",
                    side_effect=AssertionError(
                        "item 5 decoded an ordinary companion group"
                    ),
                ) as companion_read,
            ):
                result = service(
                    authority=stores.authority,
                    persistence_authorizer=stores.authorizer,
                    coordinator=stores.coordinator,
                    universe=stores.universe,
                    policy=stores.policy,
                )
            self.assertIs(type(result), ExpertReplayDeniedV1)
            self.assertIs(
                result.result.mismatch,
                ExpertReplayMismatchV1.EVIDENCE_MANIFEST_MISMATCH,
            )
            evidence_read.assert_not_called()
            companion_read.assert_not_called()

        # Items 4/10 are persisted collisions, not private-state mutations:
        # marker and manifest are re-encoded self-consistently, while a
        # corrupt unread tail proves the earlier mismatch wins over item 11.
        for expected, stored_fault in (
            (
                ExpertReplayMismatchV1.EVIDENCE_SESSION_MISMATCH,
                "session",
            ),
            (
                ExpertReplayMismatchV1.RETENTION_BINDING_MISMATCH,
                "retention",
            ),
            (
                ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
                "environment",
            ),
        ):
            with (
                self.subTest(real_service_precedence=expected),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                stores.close_companion()
                marker_path = stores.paths()["expert_marker"]
                journal = stores.paths()["expert_journal"]
                marker = replay_store_module._decode_expert_marker(
                    marker_path.read_bytes()
                )
                manifest = stores.manifest
                if stored_fault == "session":
                    alternate_session = (
                        "22222222-2222-4222-8222-222222222222"
                    )
                    retention_values = {
                        item.name: (
                            alternate_session
                            if item.name == "session_id"
                            else getattr(manifest.retention, item.name)
                        )
                        for item in fields(manifest.retention)
                        if item.name != "retention_binding_sha256"
                    }
                    retention = ExpertRetentionBindingV1(
                        **retention_values,
                        retention_binding_sha256=(
                            compute_expert_retention_binding_sha256(
                                **retention_values
                            )
                        ),
                    )
                    manifest = _rehash_manifest(
                        manifest,
                        session_id=alternate_session,
                        retention=retention,
                    )
                    replacement_journal = journal.with_name(
                        replay_store_module._journal_basename(
                            alternate_session
                        )
                    )
                    os.replace(journal, replacement_journal)
                    journal = replacement_journal
                    marker.update(
                        {
                            "session_id": alternate_session,
                            "journal_basename": (
                                replay_store_module._journal_basename(
                                    alternate_session
                                )
                            ),
                            "reserve_basename": (
                                replay_store_module._reserve_basename(
                                    alternate_session
                                )
                            ),
                            "retention_binding_sha256": (
                                retention.retention_binding_sha256
                            ),
                        }
                    )
                else:
                    retention_values = {
                        item.name: (
                            getattr(manifest.retention, item.name) + 1
                            if item.name == "retention_delete_by_ns"
                            else getattr(manifest.retention, item.name)
                        )
                        for item in fields(manifest.retention)
                        if item.name != "retention_binding_sha256"
                    }
                    retention = ExpertRetentionBindingV1(
                        **retention_values,
                        retention_binding_sha256=(
                            compute_expert_retention_binding_sha256(
                                **retention_values
                            )
                        ),
                    )
                    manifest = _rehash_manifest(
                        manifest,
                        retention=retention,
                        environment=(
                            replace(
                                manifest.environment,
                                runtime_code_sha256=SHA_D,
                            )
                            if stored_fault == "environment"
                            else manifest.environment
                        ),
                    )
                    marker.update(
                        {
                            "retention_binding_sha256": (
                                retention.retention_binding_sha256
                            ),
                            "retention_delete_by_ns": (
                                retention.retention_delete_by_ns
                            ),
                        }
                    )
                ExpertSessionManifestV1.__post_init__(manifest)
                marker["expert_manifest_sha256"] = (
                    manifest.manifest_sha256
                )
                marker_path.write_bytes(
                    replay_store_module._encode_expert_marker(
                        {
                            name: marker[name]
                            for name in (
                                replay_store_module.EXPERT_MARKER_FIELDS
                            )
                        }
                    )
                )
                os.chmod(marker_path, 0o600)
                import inci_tennis_expert.journal_codec as codec_module
                journal.write_bytes(
                    codec_module.encode_expert_file_header()
                    + codec_module.encode_expert_manifest_frame(manifest)
                    + b"corrupt-unread-tail"
                )
                os.chmod(journal, 0o600)
                if stored_fault == "environment":
                    # Restart has no live-session in-memory environment seal.
                    # The freshly collected baseline must still make item 9
                    # beat the colliding persisted retention/item-10 fault.
                    replay_store_module._ROOTS[
                        stores.authority
                    ].last_environment = None

                with (
                    mock.patch.object(
                        service_module,
                        "read_next_replay_evidence_parent",
                        side_effect=AssertionError(
                            "begin mismatch read an evidence parent"
                        ),
                    ) as evidence_read,
                    mock.patch.object(
                        service_module,
                        "read_next_replay_companion_group",
                        side_effect=AssertionError(
                            "begin mismatch decoded an unread group"
                        ),
                    ) as companion_read,
                ):
                    result = service(
                        authority=stores.authority,
                        persistence_authorizer=stores.authorizer,
                        coordinator=stores.coordinator,
                        universe=stores.universe,
                        policy=stores.policy,
                    )
                self.assertIs(type(result), ExpertReplayDeniedV1)
                self.assertIs(result.result.mismatch, expected)
                self.assertFalse(result.result.exact_replay)
                evidence_read.assert_not_called()
                companion_read.assert_not_called()

    def test_replay_full_environment_snapshots_have_exact_operation_budget(
        self,
    ) -> None:
        begin, parent_step, finish = self._pure()
        _, _, template = task6_artifacts()
        constant_environment = (
            template.environment,
            template.normalizers,
            template.structural_schemas,
            template.event_schemas,
        )
        with mock.patch.object(
            replay_store_module,
            "_installed_environment",
            return_value=constant_environment,
        ) as installed:
            with _RealReplayStores() as stores:
                stores.append_parent()
                stores.close_companion()
                authority, ready = self._prepare(stores)
                installed.reset_mock()
                observed: dict[str, int] = {}

                def measured(name: str, operation: object) -> object:
                    before = installed.call_count
                    assert callable(operation)
                    result = operation()
                    observed[name] = installed.call_count - before
                    return result

                begin_token = measured(
                    "issue_begin",
                    lambda: (
                        replay_store_facade
                        .issue_begin_replay_authorization(authority)
                    ),
                )
                accumulator = begin(
                    manifest=ready.manifest,
                    current_environment=stores.collected.current,
                    universe=stores.universe,
                    policy=stores.policy,
                    evidence=ready.evidence,
                    authorization=begin_token,
                )
                measured(
                    "ack_begin",
                    lambda: replay_store_facade.acknowledge_begin_replay(
                        authority,
                        authorization=begin_token,
                        accumulator=accumulator,
                    ),
                )
                parent = measured(
                    "read_raw",
                    lambda: (
                        replay_store_facade
                        .read_next_replay_evidence_parent(authority)
                    ),
                )
                stored = measured(
                    "read_group",
                    lambda: (
                        replay_store_facade
                        .read_next_replay_companion_group(authority)
                    ),
                )
                assert parent is not None and stored is not None
                parent_token = measured(
                    "issue_parent",
                    lambda: (
                        replay_store_facade
                        .issue_parent_group_replay_authorization(authority)
                    ),
                )
                accumulator = parent_step(
                    accumulator,
                    authorization=parent_token,
                    parent=parent,
                    stored_group=stored[0],
                    stored_payloads=stored[1],
                )
                measured(
                    "ack_parent",
                    lambda: (
                        replay_store_facade
                        .acknowledge_parent_group_replay(
                            authority,
                            authorization=parent_token,
                            accumulator=accumulator,
                        )
                    ),
                )
                self.assertIsNone(
                    measured(
                        "read_phase1_terminal_and_eof",
                        lambda: (
                            replay_store_facade
                            .read_next_replay_evidence_parent(authority)
                        ),
                    )
                )
                self.assertIsNone(
                    measured(
                        "read_companion_terminal",
                        lambda: (
                            replay_store_facade
                            .read_next_replay_companion_group(authority)
                        ),
                    )
                )
                terminal, scan = measured(
                    "cached_finish",
                    lambda: replay_store_facade.read_replay_finish_material(
                        authority
                    ),
                )
                finish_token = measured(
                    "issue_finish",
                    lambda: (
                        replay_store_facade
                        .issue_finish_replay_authorization(authority)
                    ),
                )
                result = finish(
                    accumulator,
                    final_authorization=finish_token,
                    companion_terminal=terminal,
                    companion_scan=scan,
                )
                measured(
                    "ack_finish",
                    lambda: replay_store_facade.acknowledge_finish_replay(
                        authority,
                        authorization=finish_token,
                        result=result,
                    ),
                )
        expected = {
            "issue_begin": 2,
            "ack_begin": 1,
            "read_raw": 1,
            "read_group": 2,
            "issue_parent": 2,
            "ack_parent": 1,
            "read_phase1_terminal_and_eof": 2,
            "read_companion_terminal": 2,
            "cached_finish": 1,
            "issue_finish": 2,
            "ack_finish": 1,
        }
        self.assertEqual(observed, expected)
        self.assertEqual(sum(observed.values()), 17)

    def test_replay_authority_executes_every_legal_and_invalid_state_edge(
        self,
    ) -> None:
        begin, parent_step, finish = self._pure()
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, ready = self._prepare(stores)
            state = replay_store_module._REPLAYS[authority]
            phase1_reader = state["phase1_reader"]
            companion_reader = state["companion_reader"]
            companion_fd = replay_store_module._READERS[
                companion_reader
            ].fd
            collected = self._collect(stores)
            begin_token = (
                replay_store_facade.issue_begin_replay_authorization(
                    authority
                )
            )
            accumulator = begin(
                manifest=ready.manifest,
                current_environment=collected.current,
                universe=stores.universe,
                policy=stores.policy,
                evidence=ready.evidence,
                authorization=begin_token,
            )
            replay_store_facade.acknowledge_begin_replay(
                authority,
                authorization=begin_token,
                accumulator=accumulator,
            )
            parent = (
                replay_store_facade.read_next_replay_evidence_parent(
                    authority
                )
            )
            stored = (
                replay_store_facade.read_next_replay_companion_group(
                    authority
                )
            )
            self.assertIs(type(parent), PersistedEvent)
            self.assertIsNotNone(stored)
            parent_token = (
                replay_store_facade
                .issue_parent_group_replay_authorization(authority)
            )
            assert parent is not None and stored is not None
            accumulator = parent_step(
                accumulator,
                authorization=parent_token,
                parent=parent,
                stored_group=stored[0],
                stored_payloads=stored[1],
            )
            replay_store_facade.acknowledge_parent_group_replay(
                authority,
                authorization=parent_token,
                accumulator=accumulator,
            )
            self.assertIsNone(
                replay_store_facade.read_next_replay_evidence_parent(
                    authority
                )
            )
            self.assertIsNone(
                replay_store_facade.read_next_replay_companion_group(
                    authority
                )
            )
            terminal, scan = (
                replay_store_facade.read_replay_finish_material(authority)
            )
            finish_token = (
                replay_store_facade.issue_finish_replay_authorization(
                    authority
                )
            )
            result = finish(
                accumulator,
                final_authorization=finish_token,
                companion_terminal=terminal,
                companion_scan=scan,
            )
            replay_store_facade.acknowledge_finish_replay(
                authority,
                authorization=finish_token,
                result=result,
            )
            self.assertEqual(state["state"], "consumed_closed")
            self.assertNotIn(companion_reader, replay_store_module._READERS)
            with self.assertRaises(OSError):
                os.fstat(companion_fd)
            self.assertTrue(
                object.__getattribute__(phase1_reader, "_closed")
            )
            for operation in (
                replay_store_facade.prepare_expert_replay_begin,
                replay_store_facade.read_next_replay_evidence_parent,
                replay_store_facade.read_next_replay_companion_group,
                replay_store_facade.read_replay_finish_material,
                replay_store_facade.issue_begin_replay_authorization,
                replay_store_facade.issue_parent_group_replay_authorization,
                replay_store_facade.issue_finish_replay_authorization,
                replay_store_facade.take_expert_replay_denial,
                replay_store_facade.abort_expert_replay_construction,
            ):
                with self.subTest(after_finish=operation.__name__):
                    with self.assertRaises(ValueError):
                        operation(authority)
            for name, invocation in (
                (
                    "acknowledge_begin_replay",
                    lambda: replay_store_facade.acknowledge_begin_replay(
                        authority,
                        authorization=begin_token,
                        accumulator=accumulator,
                    ),
                ),
                (
                    "acknowledge_parent_group_replay",
                    lambda: (
                        replay_store_facade
                        .acknowledge_parent_group_replay(
                            authority,
                            authorization=parent_token,
                            accumulator=accumulator,
                        )
                    ),
                ),
                (
                    "acknowledge_finish_replay",
                    lambda: replay_store_facade.acknowledge_finish_replay(
                        authority,
                        authorization=finish_token,
                        result=result,
                    ),
                ),
            ):
                with self.subTest(after_finish_ack=name):
                    with self.assertRaises(ValueError):
                        invocation()

        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, ready = self._prepare(stores)
            state = replay_store_module._REPLAYS[authority]
            root = replay_store_module._ROOTS[stores.authority]
            issued_generation = root.generation
            reader_snapshot = self._owned_reader_snapshot(authority)
            closed_begin_token = _authorization(
                ready.manifest,
                ready.evidence,
                operation="begin",
                sequence=0,
            )
            closed_begin_accumulator = begin(
                manifest=ready.manifest,
                current_environment=ready.manifest.environment,
                universe=stores.universe,
                policy=stores.policy,
                evidence=ready.evidence,
                authorization=closed_begin_token,
            )
            closed_parent = stores.parents[0]
            closed_stored_group = stores.groups[0]
            closed_parent_token = _authorization(
                ready.manifest,
                ready.evidence,
                operation="parent_group",
                sequence=1,
                expected_parent_ingest_seq=closed_parent.ingest_seq,
            )
            closed_parent_accumulator = parent_step(
                closed_begin_accumulator,
                authorization=closed_parent_token,
                parent=closed_parent,
                stored_group=closed_stored_group[0],
                stored_payloads=closed_stored_group[1],
            )
            closed_finish_token = _authorization(
                ready.manifest,
                ready.evidence,
                operation="finish",
                sequence=2,
            )
            closed_result = finish(
                closed_parent_accumulator,
                final_authorization=closed_finish_token,
                companion_terminal=_terminal(closed_parent_accumulator),
                companion_scan=_clean_scan(
                    closed_parent_accumulator.cursor
                ),
            )
            root.generation = issued_generation + 1
            try:
                with self.assertRaises(ExpertReplayAccessDenied):
                    replay_store_facade.issue_begin_replay_authorization(
                        authority
                    )
                denial = replay_store_facade.take_expert_replay_denial(
                    authority
                )
            finally:
                root.generation = issued_generation
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            )
            self.assertEqual(state["state"], "denied_closed")
            self._assert_owned_readers_revoked(reader_snapshot)
            self._assert_same_root_operational(stores)
            for operation in (
                replay_store_facade.prepare_expert_replay_begin,
                replay_store_facade.read_next_replay_evidence_parent,
                replay_store_facade.read_next_replay_companion_group,
                replay_store_facade.read_replay_finish_material,
                replay_store_facade.issue_begin_replay_authorization,
                replay_store_facade.issue_parent_group_replay_authorization,
                replay_store_facade.issue_finish_replay_authorization,
                replay_store_facade.take_expert_replay_denial,
                replay_store_facade.abort_expert_replay_construction,
            ):
                with self.subTest(
                    after_generation_drift=operation.__name__
                ):
                    with self.assertRaises(ValueError):
                        operation(authority)
            def closed_state_snapshot() -> tuple[
                tuple[str, type[object], int, str],
                ...,
            ]:
                return tuple(
                    (
                        key,
                        type(value),
                        id(value),
                        repr(value),
                    )
                    for key, value in sorted(state.items())
                )

            def closed_file_snapshot() -> tuple[
                tuple[
                    str,
                    bool,
                    int | None,
                    int | None,
                    int | None,
                    int | None,
                    int | None,
                    int | None,
                    str | None,
                ],
                ...,
            ]:
                snapshots = []
                for name, path in sorted(stores.paths().items()):
                    try:
                        metadata = os.lstat(path)
                    except FileNotFoundError:
                        snapshots.append(
                            (
                                name,
                                False,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                            )
                        )
                        continue
                    digest = None
                    if stat.S_ISREG(metadata.st_mode):
                        hasher = sha256()
                        with path.open("rb") as stream:
                            while block := stream.read(1 << 20):
                                hasher.update(block)
                        digest = hasher.hexdigest()
                    snapshots.append(
                        (
                            name,
                            True,
                            metadata.st_dev,
                            metadata.st_ino,
                            metadata.st_size,
                            metadata.st_mode,
                            metadata.st_mtime_ns,
                            metadata.st_ctime_ns,
                            digest,
                        )
                    )
                return tuple(snapshots)

            closed_state = closed_state_snapshot()
            closed_files = closed_file_snapshot()
            for name, invocation in (
                (
                    "acknowledge_begin_replay",
                    lambda: replay_store_facade.acknowledge_begin_replay(
                        authority,
                        authorization=closed_begin_token,
                        accumulator=closed_begin_accumulator,
                    ),
                ),
                (
                    "acknowledge_parent_group_replay",
                    lambda: (
                        replay_store_facade
                        .acknowledge_parent_group_replay(
                            authority,
                            authorization=closed_parent_token,
                            accumulator=closed_parent_accumulator,
                        )
                    ),
                ),
                (
                    "acknowledge_finish_replay",
                    lambda: replay_store_facade.acknowledge_finish_replay(
                        authority,
                        authorization=closed_finish_token,
                        result=closed_result,
                    ),
                ),
            ):
                with self.subTest(
                    generation_denied_acknowledgement=name
                ):
                    with self.assertRaises(ValueError):
                        invocation()
                    self.assertEqual(
                        closed_state_snapshot(),
                        closed_state,
                    )
                    self.assertEqual(
                        closed_file_snapshot(),
                        closed_files,
                    )
                    self._assert_owned_readers_revoked(reader_snapshot)
                    self._assert_same_root_operational(stores)
                    self.assertEqual(
                        closed_state_snapshot(),
                        closed_state,
                    )
                    self.assertEqual(
                        closed_file_snapshot(),
                        closed_files,
                    )

        with _RealReplayStores() as stores:
            authority = stores.issue_replay()
            self.assertIs(type(authority), ExpertReplayConstructionAuthorityV1)
            replay_store_facade.abort_expert_replay_construction(authority)
            with self.assertRaises(ValueError):
                replay_store_facade.abort_expert_replay_construction(
                    authority
                )

        for seam in ("ack_begin", "ack_parent", "ack_finish"):
            with (
                self.subTest(double_acknowledgement=seam),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                authority, invocation = self._stage(stores, seam)
                self.assertIsNone(invocation())
                with self.assertRaises(ValueError):
                    invocation()
                state = replay_store_module._REPLAYS[authority]
                if seam == "ack_finish":
                    self.assertEqual(
                        state["state"],
                        "consumed_closed",
                    )
                else:
                    self.assertTrue(state["closed"])
                    self.assertEqual(
                        state["state"],
                        "aborted_closed",
                    )

        invalid_state_cases = (
            (
                "double_prepare",
                "issue_begin",
                lambda authority: (
                    replay_store_facade.prepare_expert_replay_begin(
                        authority
                    )
                ),
            ),
            (
                "double_begin_issue",
                "ack_begin",
                lambda authority: (
                    replay_store_facade
                    .issue_begin_replay_authorization(authority)
                ),
            ),
            (
                "read_with_begin_token",
                "ack_begin",
                lambda authority: (
                    replay_store_facade
                    .read_next_replay_evidence_parent(authority)
                ),
            ),
            (
                "group_before_parent",
                "read_evidence",
                lambda authority: (
                    replay_store_facade
                    .read_next_replay_companion_group(authority)
                ),
            ),
            (
                "parent_authorization_before_pair",
                "read_evidence",
                lambda authority: (
                    replay_store_facade
                    .issue_parent_group_replay_authorization(authority)
                ),
            ),
            (
                "finish_before_pair",
                "read_evidence",
                lambda authority: (
                    replay_store_facade
                    .issue_finish_replay_authorization(authority)
                ),
            ),
            (
                "denial_take_before_denial",
                "issue_begin",
                lambda authority: (
                    replay_store_facade
                    .take_expert_replay_denial(authority)
                ),
            ),
            (
                "second_parent_before_group",
                "read_companion",
                lambda authority: (
                    replay_store_facade
                    .read_next_replay_evidence_parent(authority)
                ),
            ),
            (
                "authorization_before_complete_pair",
                "read_companion",
                lambda authority: (
                    replay_store_facade
                    .issue_parent_group_replay_authorization(authority)
                ),
            ),
            (
                "read_with_parent_token",
                "ack_parent",
                lambda authority: (
                    replay_store_facade
                    .read_next_replay_evidence_parent(authority)
                ),
            ),
            (
                "double_parent_issue",
                "ack_parent",
                lambda authority: (
                    replay_store_facade
                    .issue_parent_group_replay_authorization(authority)
                ),
            ),
            (
                "finish_issue_before_material",
                "read_finish",
                lambda authority: (
                    replay_store_facade
                    .issue_finish_replay_authorization(authority)
                ),
            ),
            (
                "read_with_finish_token",
                "ack_finish",
                lambda authority: (
                    replay_store_facade
                    .read_next_replay_evidence_parent(authority)
                ),
            ),
            (
                "double_finish_issue",
                "ack_finish",
                lambda authority: (
                    replay_store_facade
                    .issue_finish_replay_authorization(authority)
                ),
            ),
        )
        for label, stage, invalid in invalid_state_cases:
            with (
                self.subTest(invalid_state_edge=label),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                authority, _ = self._stage(stores, stage)
                reader_snapshot = self._owned_reader_snapshot(authority)
                with self.assertRaises(ValueError):
                    invalid(authority)
                state = replay_store_module._REPLAYS[authority]
                self.assertTrue(state["closed"])
                self.assertEqual(state["state"], "aborted_closed")
                self._assert_owned_readers_revoked(reader_snapshot)
                self._assert_same_root_operational(stores)

        for fault in (
            "wrong_operation",
            "forged_equal_nonidentical",
            "stale_sequence",
            "wrong_accumulator",
        ):
            with (
                self.subTest(invalid_begin_ack=fault),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                authority, ready = self._prepare(stores)
                collected = self._collect(stores)
                token = (
                    replay_store_facade
                    .issue_begin_replay_authorization(authority)
                )
                accumulator = begin(
                    manifest=ready.manifest,
                    current_environment=collected.current,
                    universe=stores.universe,
                    policy=stores.policy,
                    evidence=ready.evidence,
                    authorization=token,
                )
                candidate_token = token
                candidate_accumulator = accumulator
                if fault == "wrong_operation":
                    candidate_token = _rehash_authorization(
                        token,
                        authorized_operation="finish",
                    )
                elif fault == "forged_equal_nonidentical":
                    candidate_token = _unchecked_replace(token)
                    self.assertEqual(candidate_token, token)
                    self.assertIsNot(candidate_token, token)
                elif fault == "stale_sequence":
                    candidate_token = _rehash_authorization(
                        token,
                        authorization_sequence=(
                            token.authorization_sequence + 1
                        ),
                    )
                else:
                    candidate_accumulator = _unchecked_replace(
                        accumulator,
                        last_authorization_sha256=SHA_D,
                    )
                reader_snapshot = self._owned_reader_snapshot(authority)
                with self.assertRaises(ValueError):
                    replay_store_facade.acknowledge_begin_replay(
                        authority,
                        authorization=candidate_token,
                        accumulator=candidate_accumulator,
                    )
                state = replay_store_module._REPLAYS[authority]
                self.assertTrue(state["closed"])
                self.assertEqual(state["state"], "aborted_closed")
                self._assert_owned_readers_revoked(reader_snapshot)
                self._assert_same_root_operational(stores)

        for seam, wrong_value in (
            ("ack_parent", "accumulator"),
            ("ack_finish", "result"),
        ):
            with (
                self.subTest(wrong_acknowledgement_value=seam),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                authority, _ = self._stage(stores, seam)
                state = replay_store_module._REPLAYS[authority]
                outstanding = state["outstanding"]
                reader_snapshot = self._owned_reader_snapshot(authority)
                if wrong_value == "accumulator":
                    with self.assertRaises(ValueError):
                        (
                            replay_store_facade
                            .acknowledge_parent_group_replay(
                                authority,
                                authorization=outstanding,
                                accumulator=state["accumulator"],
                            )
                        )
                else:
                    wrong_result = _denied_result(
                        state["evidence"],
                        ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
                    ).result
                    with self.assertRaises(ValueError):
                        replay_store_facade.acknowledge_finish_replay(
                            authority,
                            authorization=outstanding,
                            result=wrong_result,
                        )
                self.assertTrue(state["closed"])
                self.assertEqual(state["state"], "aborted_closed")
                self._assert_owned_readers_revoked(reader_snapshot)
                self._assert_same_root_operational(stores)

        for seam in ("ack_parent", "ack_finish"):
            with (
                self.subTest(forged_equal_nonidentical_ack=seam),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                authority, _ = self._stage(stores, seam)
                state = replay_store_module._REPLAYS[authority]
                outstanding = state["outstanding"]
                self.assertIs(
                    type(outstanding),
                    RetentionReplayAuthorizationV1,
                )
                forged = _unchecked_replace(outstanding)
                self.assertEqual(forged, outstanding)
                self.assertIsNot(forged, outstanding)
                reader_snapshot = self._owned_reader_snapshot(authority)
                if seam == "ack_parent":
                    current_group = state["current_group"]
                    candidate = parent_step(
                        state["accumulator"],
                        authorization=outstanding,
                        parent=state["current_parent"],
                        stored_group=current_group[0],
                        stored_payloads=current_group[1],
                    )
                    with self.assertRaises(ValueError):
                        (
                            replay_store_facade
                            .acknowledge_parent_group_replay(
                                authority,
                                authorization=forged,
                                accumulator=candidate,
                            )
                        )
                else:
                    finish_material = state["finish_material"]
                    result = finish(
                        state["accumulator"],
                        final_authorization=outstanding,
                        companion_terminal=finish_material[0],
                        companion_scan=finish_material[1],
                    )
                    with self.assertRaises(ValueError):
                        replay_store_facade.acknowledge_finish_replay(
                            authority,
                            authorization=forged,
                            result=result,
                        )
                self.assertTrue(state["closed"])
                self.assertEqual(state["state"], "aborted_closed")
                self._assert_owned_readers_revoked(reader_snapshot)
                self._assert_same_root_operational(stores)

        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, _ = self._stage(stores, "ack_begin")
            state = replay_store_module._REPLAYS[authority]
            phase1_reader = state["phase1_reader"]
            companion_reader = state["companion_reader"]
            with self.assertRaises(ValueError):
                replay_store_facade.read_next_replay_evidence_parent(
                    authority
                )
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "aborted_closed")
            self.assertNotIn(
                companion_reader,
                replay_store_module._READERS,
            )
            self.assertTrue(
                object.__getattribute__(phase1_reader, "_closed")
            )

        with _RealReplayStores() as stores:
            stores.close_companion()
            authority, _ = self._prepare(stores)
            reader_snapshot = self._owned_reader_snapshot(authority)
            outcomes: list[BaseException] = []

            def wrong_thread() -> None:
                try:
                    replay_store_facade.prepare_expert_replay_begin(
                        authority
                    )
                except BaseException as error:
                    outcomes.append(error)

            worker = threading.Thread(target=wrong_thread)
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(outcomes), 1)
            self.assertIs(type(outcomes[0]), ValueError)
            state = replay_store_module._REPLAYS[authority]
            self.assertFalse(state["closed"])
            self.assertEqual(state["state"], "begin_ready")
            phase1, companion, _, companion_fd = reader_snapshot
            self.assertFalse(
                object.__getattribute__(phase1, "_closed")
            )
            self.assertIn(companion, replay_store_module._READERS)
            os.fstat(companion_fd)
            replay_store_facade.abort_expert_replay_construction(
                authority
            )
            self._assert_owned_readers_revoked(reader_snapshot)
            self._assert_same_root_operational(stores)

        if hasattr(os, "fork"):
            with _RealReplayStores() as stores:
                stores.close_companion()
                authority, _ = self._prepare(stores)
                reader_snapshot = self._owned_reader_snapshot(authority)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    child = os.fork()
                if child == 0:
                    try:
                        replay_store_facade.prepare_expert_replay_begin(
                            authority
                        )
                    except ValueError:
                        state = replay_store_module._REPLAYS[authority]
                        if (
                            state["closed"]
                            or state["state"] != "begin_ready"
                        ):
                            os._exit(93)
                        phase1, companion, _, companion_fd = (
                            reader_snapshot
                        )
                        if (
                            phase1 is not None
                            and object.__getattribute__(
                                phase1,
                                "_closed",
                            )
                        ):
                            os._exit(94)
                        if companion not in replay_store_module._READERS:
                            os._exit(95)
                        if companion_fd is not None:
                            try:
                                os.fstat(companion_fd)
                            except OSError:
                                os._exit(96)
                        os._exit(0)
                    except BaseException:
                        os._exit(91)
                    os._exit(92)
                waited, status = os.waitpid(child, 0)
                self.assertEqual(waited, child)
                self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                replay_store_facade.abort_expert_replay_construction(
                    authority
                )

    def test_replay_retains_one_shared_pair_with_independent_seals_and_rejects_alias_mutation(
        self,
    ) -> None:
        begin, parent_step, _ = self._pure()

        def stage_pair(
            stores: _RealReplayStores,
        ) -> tuple[
            ExpertReplayConstructionAuthorityV1,
            PersistedEvent,
            tuple[ExpertJournalGroupV1, tuple[bytes, ...]],
            ExpertReplayAccumulatorV1,
        ]:
            stores.append_parent()
            authority, ready = self._prepare(stores)
            collected = self._collect(stores)
            begin_token = (
                replay_store_facade.issue_begin_replay_authorization(
                    authority
                )
            )
            accumulator = begin(
                manifest=ready.manifest,
                current_environment=collected.current,
                universe=stores.universe,
                policy=stores.policy,
                evidence=ready.evidence,
                authorization=begin_token,
            )
            replay_store_facade.acknowledge_begin_replay(
                authority,
                authorization=begin_token,
                accumulator=accumulator,
            )
            parent = (
                replay_store_facade.read_next_replay_evidence_parent(
                    authority
                )
            )
            self.assertIs(type(parent), PersistedEvent)
            assert type(parent) is PersistedEvent
            state = replay_store_module._REPLAYS[authority]
            self.assertIs(state["current_parent"], parent)
            self.assertEqual(
                state["current_parent_record_sha256"],
                canonical_record_sha256(parent),
            )
            stored = (
                replay_store_facade.read_next_replay_companion_group(
                    authority
                )
            )
            self.assertIs(type(stored), tuple)
            assert type(stored) is tuple
            self.assertIs(state["current_group"], stored)
            self.assertEqual(
                state["current_group_sha256"],
                stored[0].group_sha256,
            )
            self.assertEqual(
                state["current_payload_seals"],
                tuple(
                    (len(payload), sha256(payload).hexdigest())
                    for payload in stored[1]
                ),
            )
            return authority, parent, stored, accumulator

        seal_names = (
            "current_parent_record_sha256",
            "current_group_sha256",
            "current_payload_seals",
        )

        with _RealReplayStores() as stores:
            authority, parent, stored, accumulator = stage_pair(stores)
            token = (
                replay_store_facade
                .issue_parent_group_replay_authorization(authority)
            )
            candidate = parent_step(
                accumulator,
                authorization=token,
                parent=parent,
                stored_group=stored[0],
                stored_payloads=stored[1],
            )
            replay_store_facade.acknowledge_parent_group_replay(
                authority,
                authorization=token,
                accumulator=candidate,
            )
            state = replay_store_module._REPLAYS[authority]
            for name in seal_names:
                self.assertNotIn(name, state)
            replay_store_facade.abort_expert_replay_construction(authority)
            for name in seal_names:
                self.assertNotIn(name, state)

        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, ready = self._prepare(stores)
            collected = self._collect(stores)
            begin_token = (
                replay_store_facade.issue_begin_replay_authorization(
                    authority
                )
            )
            accumulator = begin(
                manifest=ready.manifest,
                current_environment=collected.current,
                universe=stores.universe,
                policy=stores.policy,
                evidence=ready.evidence,
                authorization=begin_token,
            )
            replay_store_facade.acknowledge_begin_replay(
                authority,
                authorization=begin_token,
                accumulator=accumulator,
            )
            parent = (
                replay_store_facade.read_next_replay_evidence_parent(
                    authority
                )
            )
            assert type(parent) is PersistedEvent
            object.__setattr__(parent, "source_id", "mutated")
            with self.assertRaisesRegex(
                ValueError,
                "^expert_replay_authority_invalid$",
            ):
                replay_store_facade.read_next_replay_companion_group(
                    authority
                )
            state = replay_store_module._REPLAYS[authority]
            self.assertTrue(state["closed"])
            self.assertEqual(state["state"], "aborted_closed")
            for name in seal_names:
                self.assertNotIn(name, state)

        mutations = (
            (
                "parent",
                lambda state, parent, stored: object.__setattr__(
                    parent,
                    "source_id",
                    "mutated",
                ),
            ),
            (
                "group_nested",
                lambda state, parent, stored: object.__setattr__(
                    stored[0].parent,
                    "event_type",
                    "mutated",
                ),
            ),
            (
                "payload_substitution",
                lambda state, parent, stored: state.__setitem__(
                    "current_group",
                    (stored[0], (b"substituted",)),
                ),
            ),
        )
        for name, mutate in mutations:
            with (
                self.subTest(pre_issue_mutation=name),
                _RealReplayStores() as stores,
            ):
                authority, parent, stored, _ = stage_pair(stores)
                state = replay_store_module._REPLAYS[authority]
                mutate(state, parent, stored)
                with self.assertRaisesRegex(
                    ValueError,
                    "^expert_replay_authority_invalid$",
                ):
                    (
                        replay_store_facade
                        .issue_parent_group_replay_authorization(
                            authority
                        )
                    )
                self.assertTrue(state["closed"])
                self.assertEqual(state["state"], "aborted_closed")
                for seal_name in seal_names:
                    self.assertNotIn(seal_name, state)

        for name, mutate in mutations:
            with (
                self.subTest(post_issue_mutation=name),
                _RealReplayStores() as stores,
            ):
                authority, parent, stored, accumulator = stage_pair(
                    stores
                )
                state = replay_store_module._REPLAYS[authority]
                token = (
                    replay_store_facade
                    .issue_parent_group_replay_authorization(authority)
                )
                candidate = parent_step(
                    accumulator,
                    authorization=token,
                    parent=parent,
                    stored_group=stored[0],
                    stored_payloads=stored[1],
                )
                mutate(state, parent, stored)
                with self.assertRaisesRegex(
                    ValueError,
                    "^replay_parent_ack_invalid$",
                ):
                    replay_store_facade.acknowledge_parent_group_replay(
                        authority,
                        authorization=token,
                        accumulator=candidate,
                    )
                self.assertTrue(state["closed"])
                self.assertEqual(state["state"], "aborted_closed")
                for seal_name in seal_names:
                    self.assertNotIn(seal_name, state)

    def test_alias_seal_failure_preserves_deadline_and_identity_precedence(
        self,
    ) -> None:
        seal_names = (
            "current_parent",
            "current_group",
            "current_parent_record_sha256",
            "current_group_sha256",
            "current_payload_seals",
        )
        seams = (
            (
                "pre_companion",
                "read_companion",
                "_validated_replay_parent_snapshot",
            ),
            (
                "pre_issue",
                "issue_parent",
                "_validated_replay_pair_snapshots",
            ),
        )
        for seam, stage_name, validation_name in seams:
            for fault in ("deadline", "identity"):
                with (
                    self.subTest(seam=seam, collision=fault),
                    _RealReplayStores() as stores,
                ):
                    stores.append_parent()
                    authority, invocation = self._stage(
                        stores,
                        stage_name,
                    )
                    state = replay_store_module._REPLAYS[authority]
                    parent = state["current_parent"]
                    self.assertIs(type(parent), PersistedEvent)
                    object.__setattr__(
                        parent,
                        "source_id",
                        "mutated",
                    )
                    original = getattr(
                        replay_store_module,
                        validation_name,
                    )

                    def collide(
                        replay_state: dict[str, object],
                        *,
                        selected_fault: str = fault,
                    ) -> object:
                        if selected_fault == "deadline":
                            stores.clock.now_ns = (
                                stores.phase1_manifest
                                .required_retention_until_ns
                            )
                        else:
                            self._replace_named_entry(
                                stores.paths()["phase1_marker"]
                            )
                        return original(replay_state)

                    with mock.patch.object(
                        replay_store_module,
                        validation_name,
                        new=collide,
                    ):
                        denial = self._take_denial(
                            authority,
                            invocation,
                        )
                    if fault == "deadline":
                        self.assertIs(
                            denial.result.mismatch,
                            (
                                ExpertReplayMismatchV1
                                .RETENTION_DEADLINE_REACHED
                            ),
                        )
                        self.assertEqual(
                            denial.proof.file_proofs,
                            (),
                        )
                    else:
                        self.assertIs(
                            denial.result.mismatch,
                            (
                                ExpertReplayMismatchV1
                                .EVIDENCE_IDENTITY_MISMATCH
                            ),
                        )
                        self.assertEqual(
                            tuple(
                                proof.role
                                for proof in denial.proof.file_proofs
                            ),
                            (
                                ExpertReplayDiagnosticRoleV1
                                .PHASE1_MARKER,
                            ),
                        )
                    for name in seal_names:
                        self.assertNotIn(name, state)

    def test_read_issue_ack_denial_and_proof_matrix_at_every_seam(
        self,
    ) -> None:
        seams = (
            "prepare",
            "issue_begin",
            "ack_begin",
            "read_evidence",
            "read_companion",
            "issue_parent",
            "ack_parent",
            "read_finish",
            "issue_finish",
            "ack_finish",
        )
        for seam in seams:
            with self.subTest(deadline_seam=seam), _RealReplayStores() as stores:
                stores.append_parent()
                authority, invocation = self._stage(stores, seam)
                stores.clock.now_ns = (
                    stores.phase1_manifest.required_retention_until_ns
                )
                denial = self._take_denial(authority, invocation)
                self.assertIs(
                    denial.result.mismatch,
                    ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                )
                self.assertEqual(denial.proof.file_proofs, ())
                self.assertGreaterEqual(
                    denial.proof.final_sampled_wall_ns,
                    denial.proof.common_deadline_ns,
                )

        for seam in seams[1:]:
            with self.subTest(authorization_seam=seam), _RealReplayStores() as stores:
                stores.append_parent()
                authority, invocation = self._stage(stores, seam)
                original = ProviderPersistenceAuthorizer.authorize_analysis

                def deny(instance: ProviderPersistenceAuthorizer) -> object:
                    if instance is stores.authorizer:
                        raise RuntimeError("denied")
                    return original(instance)

                with mock.patch.object(
                    ProviderPersistenceAuthorizer,
                    "authorize_analysis",
                    deny,
                ):
                    denial = self._take_denial(authority, invocation)
                self.assertIs(
                    denial.result.mismatch,
                    ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
                )
                self.assertEqual(denial.proof.file_proofs, ())

        for seam in seams[1:]:
            for role in tuple(ExpertReplayDiagnosticRoleV1):
                with (
                    self.subTest(identity_seam=seam, role=role),
                    _RealReplayStores() as stores,
                ):
                    stores.append_parent()
                    authority, invocation = self._stage(stores, seam)
                    self._replace_named_entry(
                        stores.paths()[role.value]
                    )
                    denial = self._take_denial(authority, invocation)
                    if (
                        role
                        is ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL
                    ):
                        self.assertIs(
                            denial.result.mismatch,
                            (
                                ExpertReplayMismatchV1
                                .RETENTION_AUTHORIZATION_MISMATCH
                            ),
                        )
                        self.assertEqual(
                            denial.proof.file_proofs,
                            (),
                        )
                    else:
                        self.assertIs(
                            denial.result.mismatch,
                            (
                                ExpertReplayMismatchV1
                                .EVIDENCE_IDENTITY_MISMATCH
                            ),
                        )
                        self.assertEqual(
                            len(denial.proof.file_proofs),
                            1,
                        )
                        self.assertIs(
                            denial.proof.file_proofs[0].role,
                            role,
                        )
                    self.assertLess(
                        denial.proof.final_sampled_wall_ns,
                        denial.proof.common_deadline_ns,
                    )

        for seam in seams[1:]:
            with self.subTest(environment_seam=seam), _RealReplayStores() as stores:
                stores.append_parent()
                authority, invocation = self._stage(stores, seam)
                original = replay_store_module._installed_environment

                def drift(
                    root: object,
                    manifest: object,
                    *,
                    gate: object = None,
                ) -> tuple[object, ...]:
                    current, normalizers, structural, event = original(
                        root,
                        manifest,
                        gate=gate,
                    )
                    return (
                        replace(current, runtime_code_sha256=SHA_D),
                        normalizers,
                        structural,
                        event,
                    )

                with mock.patch.object(
                    replay_store_module,
                    "_installed_environment",
                    drift,
                ):
                    denial = self._take_denial(authority, invocation)
                self.assertIs(
                    denial.result.mismatch,
                    ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
                )
                self.assertEqual(denial.proof.file_proofs, ())

        with _RealReplayStores() as stores:
            stores.append_parent()
            authority, invocation = self._stage(stores, "read_evidence")
            self._replace_named_entry(stores.paths()["phase1_marker"])
            original = ProviderPersistenceAuthorizer.authorize_analysis

            def deny_collision(
                instance: ProviderPersistenceAuthorizer,
            ) -> object:
                if instance is stores.authorizer:
                    raise RuntimeError("denied")
                return original(instance)

            with mock.patch.object(
                ProviderPersistenceAuthorizer,
                "authorize_analysis",
                deny_collision,
            ):
                denial = self._take_denial(authority, invocation)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            )
            self.assertEqual(denial.proof.file_proofs, ())

        collisions = (
            (
                ("deadline", "authorization"),
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            ),
            (
                ("deadline", "identity"),
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            ),
            (
                ("deadline", "environment"),
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            ),
            (
                ("authorization", "identity"),
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            ),
            (
                ("authorization", "environment"),
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            ),
            (
                ("identity", "environment"),
                ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
            ),
        )
        for faults, expected in collisions:
            with (
                self.subTest(collision=faults),
                _RealReplayStores() as stores,
                ExitStack() as stack,
            ):
                stores.append_parent()
                authority, invocation = self._stage(
                    stores,
                    "read_evidence",
                )
                if "deadline" in faults:
                    stores.clock.now_ns = (
                        stores.phase1_manifest
                        .required_retention_until_ns
                    )
                if "identity" in faults:
                    self._replace_named_entry(
                        stores.paths()["phase1_marker"]
                    )
                if "authorization" in faults:
                    original_authorize = (
                        ProviderPersistenceAuthorizer
                        .authorize_analysis
                    )

                    def deny_pairwise(
                        instance: ProviderPersistenceAuthorizer,
                    ) -> object:
                        if instance is stores.authorizer:
                            raise RuntimeError("denied")
                        return original_authorize(instance)

                    stack.enter_context(
                        mock.patch.object(
                            ProviderPersistenceAuthorizer,
                            "authorize_analysis",
                            deny_pairwise,
                        )
                    )
                if "environment" in faults:
                    original_environment = (
                        replay_store_module._installed_environment
                    )

                    def drift_pairwise(
                        root: object,
                        manifest: object,
                        *,
                        gate: object = None,
                    ) -> tuple[object, ...]:
                        current, normalizers, structural, event = (
                            original_environment(
                                root,
                                manifest,
                                gate=gate,
                            )
                        )
                        return (
                            replace(
                                current,
                                runtime_code_sha256=SHA_D,
                            ),
                            normalizers,
                            structural,
                            event,
                        )

                    stack.enter_context(
                        mock.patch.object(
                            replay_store_module,
                            "_installed_environment",
                            drift_pairwise,
                        )
                    )
                denial = self._take_denial(authority, invocation)
                self.assertIs(denial.result.mismatch, expected)
                if expected is (
                    ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH
                ):
                    self.assertEqual(
                        tuple(
                            proof.role
                            for proof in denial.proof.file_proofs
                        ),
                        (
                            ExpertReplayDiagnosticRoleV1
                            .PHASE1_MARKER,
                        ),
                    )
                else:
                    self.assertEqual(denial.proof.file_proofs, ())

        for denial in (
            denial,
        ):
            values = {
                item.name: getattr(denial.proof, item.name)
                for item in fields(denial.proof)
                if item.name != "proof_sha256"
            }
            self.assertEqual(
                denial.proof.proof_sha256,
                _independent_sha256(
                    b"INCI-EXPERT-REPLAY-DIAGNOSTIC-PROOF-V1\0",
                    values,
                ),
            )

    def test_deadline_crossing_inside_authorizer_precedes_item_6(
        self,
    ) -> None:
        for seam in ("prepare", "issue_begin"):
            with (
                self.subTest(authorizer_deadline_seam=seam),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                if seam == "prepare":
                    stores.close_companion()
                    authority = stores.issue_replay()
                    self.assertIs(
                        type(authority),
                        ExpertReplayConstructionAuthorityV1,
                    )
                    invocation = lambda: (
                        replay_store_facade.prepare_expert_replay_begin(
                            authority
                        )
                    )
                else:
                    authority, invocation = self._stage(
                        stores,
                        "issue_begin",
                    )
                deadline = (
                    stores.phase1_manifest.required_retention_until_ns
                )
                stores.clock.now_ns = deadline - 1
                original = (
                    ProviderPersistenceAuthorizer.authorize_analysis
                )

                def cross_and_deny(
                    instance: ProviderPersistenceAuthorizer,
                ) -> object:
                    if instance is stores.authorizer:
                        stores.clock.now_ns = deadline
                        raise RuntimeError("denied")
                    return original(instance)

                with mock.patch.object(
                    ProviderPersistenceAuthorizer,
                    "authorize_analysis",
                    new=cross_and_deny,
                ):
                    denial = self._take_denial(
                        authority,
                        invocation,
                    )
                self.assertIs(
                    denial.result.mismatch,
                    ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                )
                self.assertEqual(
                    denial.proof.final_sampled_wall_ns,
                    deadline,
                )
                self.assertEqual(denial.proof.file_proofs, ())
                for path in stores.paths().values():
                    self.assertFalse(path.exists())

    def test_begin_parent_finish_held_token_deadline_and_identity_barrier_matrix(
        self,
    ) -> None:
        for seam in ("ack_begin", "ack_parent", "ack_finish"):
            with self.subTest(deadline_ack=seam), _RealReplayStores() as stores:
                stores.append_parent()
                stores.clock.now_ns = (
                    stores.phase1_manifest.required_retention_until_ns - 1
                )
                authority, invocation = self._stage(stores, seam)
                reached = threading.Barrier(2)
                released = threading.Barrier(2)

                def advance_clock() -> None:
                    reached.wait(timeout=5)
                    stores.clock.now_ns = (
                        stores.phase1_manifest.required_retention_until_ns
                    )
                    released.wait(timeout=5)

                worker = threading.Thread(target=advance_clock)
                worker.start()
                reached.wait(timeout=5)
                released.wait(timeout=5)
                worker.join(5)
                self.assertFalse(worker.is_alive())
                denial = self._take_denial(authority, invocation)
                self.assertIs(
                    denial.result.mismatch,
                    ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                )
                state = replay_store_module._REPLAYS[authority]
                self.assertIsNone(state.get("outstanding"))

        for seam in ("ack_begin", "ack_parent", "ack_finish"):
            for role in tuple(ExpertReplayDiagnosticRoleV1):
                with (
                    self.subTest(identity_ack=seam, role=role),
                    _RealReplayStores() as stores,
                ):
                    stores.append_parent()
                    authority, invocation = self._stage(stores, seam)
                    reached = threading.Barrier(2)
                    released = threading.Barrier(2)

                    def replace_identity() -> None:
                        reached.wait(timeout=5)
                        self._replace_named_entry(
                            stores.paths()[role.value]
                        )
                        released.wait(timeout=5)

                    worker = threading.Thread(target=replace_identity)
                    worker.start()
                    reached.wait(timeout=5)
                    released.wait(timeout=5)
                    worker.join(5)
                    self.assertFalse(worker.is_alive())
                    denial = self._take_denial(authority, invocation)
                    managed_journal_transition = (
                        role
                        is ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL
                    )
                    self.assertIs(
                        denial.result.mismatch,
                        (
                            ExpertReplayMismatchV1
                            .RETENTION_AUTHORIZATION_MISMATCH
                            if managed_journal_transition
                            else ExpertReplayMismatchV1
                            .EVIDENCE_IDENTITY_MISMATCH
                        ),
                    )
                    if managed_journal_transition:
                        self.assertEqual(
                            denial.proof.file_proofs,
                            (),
                        )
                    else:
                        self.assertEqual(
                            tuple(
                                proof.role
                                for proof in denial.proof.file_proofs
                            ),
                            (role,),
                        )

    def test_service_maps_deadline_crossing_before_environment_collection_to_item_7(
        self,
    ) -> None:
        service_module = _module(RUNTIME_MODULE)
        service = service_module.replay_expert_session
        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            original_prepare = (
                service_module.prepare_expert_replay_begin
            )

            def cross_after_ready(
                authority: ExpertReplayConstructionAuthorityV1,
            ) -> object:
                ready = original_prepare(authority)
                self.assertIs(type(ready), ExpertReplayBeginReadyV1)
                stores.clock.now_ns = (
                    stores.phase1_manifest.required_retention_until_ns
                )
                return ready

            with mock.patch.object(
                service_module,
                "prepare_expert_replay_begin",
                side_effect=cross_after_ready,
            ):
                result = service(
                    authority=stores.authority,
                    persistence_authorizer=stores.authorizer,
                    coordinator=stores.coordinator,
                    universe=stores.universe,
                    policy=stores.policy,
                )

            deadline = (
                stores.phase1_manifest.required_retention_until_ns
            )
            self.assertIs(type(result), ExpertReplayDeniedV1)
            self.assertIs(
                result.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertEqual(result.proof.final_sampled_wall_ns, deadline)
            self.assertEqual(result.proof.file_proofs, ())
            for path in stores.paths().values():
                self.assertFalse(path.exists())

    def test_replay_service_success_empty_one_many_uses_one_root_and_fresh_environment(
        self,
    ) -> None:
        service_module = _module(RUNTIME_MODULE)
        service = service_module.replay_expert_session
        for parent_count in (0, 1, 9):
            with (
                self.subTest(parent_count=parent_count),
                _RealReplayStores() as stores,
            ):
                for _ in range(parent_count):
                    stores.append_parent()
                stores.close_companion()
                self.assertTrue(stores.environment_available)
                constructions: list[
                    ExpertReplayConstructionAuthorityV1
                ] = []
                seen_roots: list[ExpertJournalRootAuthorityV1] = []
                original_issue = (
                    service_module
                    .issue_expert_replay_construction_authority
                )
                original_environment_issue = (
                    service_module
                    .issue_expert_environment_collection_authority
                )

                def issue(
                    authority: ExpertJournalRootAuthorityV1,
                    **keywords: object,
                ) -> object:
                    seen_roots.append(authority)
                    result = original_issue(authority, **keywords)
                    if type(result) is ExpertReplayConstructionAuthorityV1:
                        constructions.append(result)
                    return result

                def issue_environment(
                    authority: ExpertJournalRootAuthorityV1,
                    **keywords: object,
                ) -> object:
                    seen_roots.append(authority)
                    return original_environment_issue(
                        authority,
                        **keywords,
                    )

                with (
                    mock.patch.object(
                        service_module,
                        "issue_expert_replay_construction_authority",
                        side_effect=issue,
                    ) as issue_spy,
                    mock.patch.object(
                        service_module,
                        "issue_expert_environment_collection_authority",
                        side_effect=issue_environment,
                    ) as environment_issue_spy,
                    mock.patch.object(
                        service_module,
                        "collect_expert_current_environment",
                        wraps=(
                            service_module
                            .collect_expert_current_environment
                        ),
                    ) as environment_collect_spy,
                    mock.patch.object(
                        service_module,
                        "abort_expert_replay_construction",
                        wraps=(
                            service_module
                            .abort_expert_replay_construction
                        ),
                    ) as abort_spy,
                ):
                    result = service(
                        authority=stores.authority,
                        persistence_authorizer=stores.authorizer,
                        coordinator=stores.coordinator,
                        universe=stores.universe,
                        policy=stores.policy,
                    )
                self.assertIs(type(result), ExpertReplayResultV1)
                self.assertTrue(result.exact_replay)
                self.assertTrue(result.evaluation_input_eligible)
                self.assertFalse(result.research_evaluable)
                issue_spy.assert_called_once()
                environment_issue_spy.assert_called_once()
                environment_collect_spy.assert_called_once()
                abort_spy.assert_not_called()
                self.assertEqual(len(constructions), 1)
                self.assertTrue(
                    all(root is stores.authority for root in seen_roots)
                )
                self.assertEqual(
                    len({id(root) for root in seen_roots}),
                    1,
                )
                state = replay_store_module._REPLAYS[
                    constructions[0]
                ]
                self.assertEqual(state["state"], "consumed_closed")
                self.assertIsNone(state.get("current_parent"))
                self.assertIsNone(state.get("current_group"))
                for name in (
                    "current_parent_record_sha256",
                    "current_group_sha256",
                    "current_payload_seals",
                ):
                    self.assertNotIn(name, state)
                self.assertEqual(
                    replay_store_facade.sample_expert_retention_wall_ns(
                        stores.authority
                    ),
                    stores.clock.now_ns,
                )
                self.assertIs(
                    replay_store_module._ROOTS[stores.authority].token,
                    stores.authority,
                )

    def test_eof_missing_extra_drains_unmatched_side_without_token_or_reduction_and_retains_one_pair(
        self,
    ) -> None:
        service_module = _module(RUNTIME_MODULE)
        service = service_module.replay_expert_session
        pure_module = _module(REPLAY_MODULE)
        cases = (
            (
                96,
                1,
                ExpertReplayMismatchV1.PARENT_MISSING,
            ),
            (
                1,
                96,
                ExpertReplayMismatchV1.PARENT_EXTRA,
            ),
        )
        for parent_count, companion_count, expected in cases:
            with (
                self.subTest(expected=expected),
                _RealReplayStores() as stores,
            ):
                matched = min(parent_count, companion_count)
                for index in range(parent_count):
                    stores.append_parent(
                        with_companion=index < matched
                    )
                for index in range(matched, companion_count):
                    synthetic = raw_parent(
                        session_id=stores.manifest.session_id,
                        ingest_seq=(
                            stores.cursor.last_parent_ingest_seq + 2
                        ),
                    )
                    stores.append_companion_for(synthetic)
                stores.close_phase1()
                matched_parent_ingest_seqs = tuple(
                    parent.ingest_seq
                    for parent in stores.parents[:matched]
                )
                constructions: list[
                    ExpertReplayConstructionAuthorityV1
                ] = []
                stores.parents.clear()
                stores.groups.clear()
                synthetic = None
                gc.collect()
                decoded_payload_tuples: list[tuple[bytes, ...]] = []
                decoded_payload_tuple_ids: set[int] = set()
                decoded_payload_byte_ids: set[int] = set()
                yielded_payload_tuple_ids: set[int] = set()
                decoded_companion_count = 0
                yielded_companion_count = 0
                import inci_tennis_expert.journal_codec as codec_module
                original_payload_decode = (
                    codec_module.decode_expert_group_payload_area
                )

                def live_material_ids() -> tuple[set[int], set[int]]:
                    live_raw: set[int] = set()
                    live_groups: set[int] = set()
                    for candidate in gc.get_objects():
                        if (
                            type(candidate) is PersistedEvent
                            and candidate.session_id
                            == stores.manifest.session_id
                            and candidate.record_kind is RecordKind.RAW
                        ):
                            live_raw.add(id(candidate))
                        elif (
                            type(candidate) is ExpertJournalGroupV1
                            and candidate.session_id
                            == stores.manifest.session_id
                        ):
                            live_groups.add(id(candidate))
                    return live_raw, live_groups

                (
                    baseline_live_raw_ids,
                    baseline_live_group_ids,
                ) = live_material_ids()
                maximum_live_pair_width = 0
                liveness_observations = 0
                drained_phase1 = 0
                drained_companion = 0
                normalize_call_count = 0
                reduce_call_count = 0
                normalized_parent_ingest_seqs: list[int] = []
                reduced_parent_ingest_seqs: list[int] = []
                parent_authorization_call_count = 0
                original_issue = (
                    service_module
                    .issue_expert_replay_construction_authority
                )
                original_phase1_next = (
                    replay_store_module._next_replay_phase1_parent
                )
                original_companion_next = (
                    replay_store_module.read_next_expert_group
                )
                original_companion_surface = (
                    service_module.read_next_replay_companion_group
                )

                def payload_reference_counts(
                    material: list[tuple[bytes, ...]],
                ) -> tuple[dict[int, int], dict[int, int]]:
                    tuple_counts: dict[int, int] = {}
                    byte_counts: dict[int, int] = {}
                    seen_bytes: set[int] = set()
                    for payloads in material:
                        tuple_counts[id(payloads)] = (
                            sys.getrefcount(payloads)
                        )
                        for payload in payloads:
                            if id(payload) in seen_bytes:
                                continue
                            seen_bytes.add(id(payload))
                            byte_counts[id(payload)] = (
                                sys.getrefcount(payload)
                            )
                    return tuple_counts, byte_counts

                def calibrate_payload_oracle() -> tuple[int, int, int]:
                    probe_material: list[tuple[bytes, ...]] = []

                    def allocate_probe() -> None:
                        payload = bytes(
                            bytearray(
                                b"inci-replay-payload-liveness-probe"
                            )
                        )
                        probe_material.append((payload,))

                    allocate_probe()
                    probe_pair = (object(), probe_material[0])
                    tuple_counts, byte_counts = (
                        payload_reference_counts(probe_material)
                    )
                    tuple_id = id(probe_material[0])
                    byte_id = id(probe_material[0][0])
                    current_tuple_count = tuple_counts[tuple_id]
                    idle_byte_count = byte_counts[byte_id]
                    probe_pair = None
                    tuple_counts, byte_counts = (
                        payload_reference_counts(probe_material)
                    )
                    idle_tuple_count = tuple_counts[tuple_id]
                    self.assertEqual(
                        current_tuple_count,
                        idle_tuple_count + 1,
                    )
                    self.assertEqual(
                        byte_counts[byte_id],
                        idle_byte_count,
                    )
                    hidden_tuple_reference = [probe_material[0]]
                    tuple_counts, _ = payload_reference_counts(
                        probe_material
                    )
                    self.assertEqual(
                        tuple_counts[tuple_id],
                        idle_tuple_count + 1,
                    )
                    hidden_tuple_reference.clear()
                    hidden_byte_reference = [probe_material[0][0]]
                    _, byte_counts = payload_reference_counts(
                        probe_material
                    )
                    self.assertEqual(
                        byte_counts[byte_id],
                        idle_byte_count + 1,
                    )
                    hidden_byte_reference.clear()
                    tuple_counts, byte_counts = (
                        payload_reference_counts(probe_material)
                    )
                    self.assertEqual(
                        tuple_counts[tuple_id],
                        idle_tuple_count,
                    )
                    self.assertEqual(
                        byte_counts[byte_id],
                        idle_byte_count,
                    )
                    probe_material.clear()
                    return (
                        idle_tuple_count,
                        current_tuple_count,
                        idle_byte_count,
                    )

                def assert_not_preloaded(
                    decoded_count: int,
                    yielded_count: int,
                ) -> None:
                    self.assertLessEqual(
                        decoded_count - yielded_count,
                        1,
                        "companion payloads were preloaded",
                    )

                (
                    idle_payload_tuple_refcount,
                    current_payload_tuple_refcount,
                    idle_payload_byte_refcount,
                ) = calibrate_payload_oracle()
                assert_not_preloaded(1, 0)
                with self.assertRaises(AssertionError):
                    assert_not_preloaded(2, 0)

                def decode_payloads(
                    *args: object,
                    **keywords: object,
                ) -> tuple[bytes, ...]:
                    nonlocal decoded_companion_count
                    payloads = original_payload_decode(
                        *args,
                        **keywords,
                    )
                    self.assertIs(type(payloads), tuple)
                    self.assertTrue(
                        all(type(payload) is bytes for payload in payloads)
                    )
                    self.assertNotIn(
                        id(payloads),
                        decoded_payload_tuple_ids,
                    )
                    decoded_payload_tuple_ids.add(id(payloads))
                    for payload in payloads:
                        self.assertNotIn(
                            id(payload),
                            decoded_payload_byte_ids,
                        )
                        decoded_payload_byte_ids.add(id(payload))
                    decoded_companion_count += 1
                    decoded_payload_tuples.append(payloads)
                    assert_not_preloaded(
                        decoded_companion_count,
                        yielded_companion_count,
                    )
                    return payloads

                def issue(
                    authority: ExpertJournalRootAuthorityV1,
                    **keywords: object,
                ) -> object:
                    result = original_issue(authority, **keywords)
                    if type(result) is ExpertReplayConstructionAuthorityV1:
                        constructions.append(result)
                    return result

                def observe_liveness(
                    *,
                    yielded_payload_tuple_id: int | None = None,
                ) -> None:
                    nonlocal maximum_live_pair_width
                    nonlocal liveness_observations
                    if not constructions:
                        return
                    gc.collect()
                    state = replay_store_module._REPLAYS[
                        constructions[0]
                    ]
                    live_raw_ids, live_group_ids = live_material_ids()
                    replay_raw_ids = (
                        live_raw_ids - baseline_live_raw_ids
                    )
                    replay_group_ids = (
                        live_group_ids - baseline_live_group_ids
                    )
                    tuple_counts, byte_counts = (
                        payload_reference_counts(
                            decoded_payload_tuples
                        )
                    )
                    explicit_payload_tuple_ids: set[int] = set()
                    if yielded_payload_tuple_id is not None:
                        explicit_payload_tuple_ids.add(
                            yielded_payload_tuple_id
                        )
                    current_parent = state.get("current_parent")
                    if state.get("state") in {
                        "evidence_parent_ready",
                        "pair_complete",
                        "parent_auth_outstanding",
                    }:
                        self.assertIs(
                            type(current_parent),
                            PersistedEvent,
                        )
                    else:
                        self.assertIsNone(
                            current_parent,
                            "a prior replay RAW was retained",
                        )
                    current_group = state.get("current_group")
                    if state.get("state") in {
                        "pair_complete",
                        "parent_auth_outstanding",
                    }:
                        self.assertIs(type(current_group), tuple)
                        assert type(current_group) is tuple
                        self.assertEqual(len(current_group), 2)
                        self.assertIs(
                            type(current_group[0]),
                            ExpertJournalGroupV1,
                        )
                        self.assertIs(type(current_group[1]), tuple)
                        explicit_payload_tuple_ids.add(
                            id(current_group[1])
                        )
                    else:
                        self.assertIsNone(
                            current_group,
                            "a prior companion group was retained",
                        )
                    self.assertLessEqual(
                        len(explicit_payload_tuple_ids),
                        1,
                        "more than one explicit payload tuple is current",
                    )
                    self.assertEqual(
                        set(tuple_counts),
                        yielded_payload_tuple_ids,
                    )
                    for tuple_id, reference_count in (
                        tuple_counts.items()
                    ):
                        self.assertEqual(
                            reference_count,
                            (
                                current_payload_tuple_refcount
                                if tuple_id
                                in explicit_payload_tuple_ids
                                else idle_payload_tuple_refcount
                            ),
                            "companion payload tuple hidden retention",
                        )
                    self.assertEqual(
                        set(byte_counts),
                        decoded_payload_byte_ids,
                    )
                    self.assertTrue(
                        all(
                            reference_count
                            == idle_payload_byte_refcount
                            for reference_count in byte_counts.values()
                        ),
                        "companion payload bytes hidden retention",
                    )
                    self.assertLessEqual(
                        len(replay_raw_ids),
                        1,
                        "more than one replay RAW is live",
                    )
                    self.assertLessEqual(
                        len(replay_group_ids),
                        1,
                        "more than one companion group is live",
                    )
                    self.assertLessEqual(
                        len(explicit_payload_tuple_ids),
                        1,
                        "more than one companion payload tuple is live",
                    )
                    self.assertLessEqual(
                        len(explicit_payload_tuple_ids),
                        len(replay_group_ids),
                        "a companion payload tuple outlived its group",
                    )
                    pair_width = max(
                        len(replay_raw_ids),
                        len(replay_group_ids),
                        len(explicit_payload_tuple_ids),
                    )
                    maximum_live_pair_width = max(
                        maximum_live_pair_width,
                        pair_width,
                    )
                    liveness_observations += 1

                def next_phase1(state: dict[str, object]) -> object:
                    nonlocal drained_phase1
                    item = original_phase1_next(state)
                    if state.get("state") == "cardinality_mismatch":
                        drained_phase1 += int(item is not None)
                    observe_liveness()
                    return item

                def next_companion(reader: object) -> object:
                    nonlocal drained_companion
                    nonlocal yielded_companion_count
                    item = original_companion_next(reader)
                    yielded_payload_tuple_id = None
                    if item is not None:
                        yielded_companion_count += 1
                        yielded_payload_tuple_id = id(item[1])
                        self.assertNotIn(
                            yielded_payload_tuple_id,
                            yielded_payload_tuple_ids,
                        )
                        yielded_payload_tuple_ids.add(
                            yielded_payload_tuple_id
                        )
                    self.assertEqual(
                        decoded_companion_count,
                        yielded_companion_count,
                        "companion payload decode/yield count diverged",
                    )
                    if constructions:
                        state = replay_store_module._REPLAYS[
                            constructions[0]
                        ]
                        if state.get("state") == "cardinality_mismatch":
                            drained_companion += int(item is not None)
                    observe_liveness(
                        yielded_payload_tuple_id=(
                            yielded_payload_tuple_id
                        )
                    )
                    return item

                def read_companion_surface(
                    authority: ExpertReplayConstructionAuthorityV1,
                ) -> object:
                    nonlocal drained_phase1
                    nonlocal drained_companion
                    item = original_companion_surface(authority)
                    state = replay_store_module._REPLAYS[authority]
                    if state.get("state") == "cardinality_mismatch":
                        if state.get("cardinality_side") == "evidence":
                            drained_phase1 += 1
                        elif (
                            state.get("cardinality_side") == "companion"
                            and item is not None
                        ):
                            drained_companion += 1
                    return item

                def count_normalize(
                    manifest: ExpertSessionManifestV1,
                    parent: PersistedEvent,
                ) -> object:
                    nonlocal normalize_call_count
                    normalize_call_count += 1
                    normalized_parent_ingest_seqs.append(
                        parent.ingest_seq
                    )
                    return normalize_expert_parent(manifest, parent)

                def count_reduce(
                    state: ExpertStateV1,
                    observations: tuple[object, ...],
                ) -> object:
                    nonlocal reduce_call_count
                    reduce_call_count += 1
                    parent_ingest_seqs = {
                        observation.parent.ingest_seq
                        for observation in observations
                    }
                    self.assertEqual(len(parent_ingest_seqs), 1)
                    reduced_parent_ingest_seqs.append(
                        next(iter(parent_ingest_seqs))
                    )
                    return reduce_expert_parent(state, observations)

                original_parent_authorization = (
                    service_module
                    .issue_parent_group_replay_authorization
                )

                def count_parent_authorization(
                    authority: ExpertReplayConstructionAuthorityV1,
                ) -> object:
                    nonlocal parent_authorization_call_count
                    parent_authorization_call_count += 1
                    return original_parent_authorization(authority)

                with (
                    mock.patch.object(
                        service_module,
                        "issue_expert_replay_construction_authority",
                        side_effect=issue,
                    ),
                    mock.patch.object(
                        replay_store_module,
                        "_next_replay_phase1_parent",
                        side_effect=next_phase1,
                    ),
                    mock.patch.object(
                        replay_store_module,
                        "read_next_expert_group",
                        side_effect=next_companion,
                    ),
                    mock.patch.object(
                        service_module,
                        "read_next_replay_companion_group",
                        side_effect=read_companion_surface,
                    ),
                    mock.patch.object(
                        codec_module,
                        "decode_expert_group_payload_area",
                        side_effect=decode_payloads,
                    ),
                    mock.patch.object(
                        pure_module,
                        "normalize_expert_parent",
                        new=count_normalize,
                    ),
                    mock.patch.object(
                        pure_module,
                        "reduce_expert_parent",
                        new=count_reduce,
                    ),
                    mock.patch.object(
                        service_module,
                        "issue_parent_group_replay_authorization",
                        new=count_parent_authorization,
                    ),
                ):
                    result = service(
                        authority=stores.authority,
                        persistence_authorizer=stores.authorizer,
                        coordinator=stores.coordinator,
                        universe=stores.universe,
                        policy=stores.policy,
                    )
                self.assertIs(type(result), ExpertReplayResultV1)
                self.assertIs(result.mismatch, expected)
                self.assertFalse(result.exact_replay)
                self.assertFalse(result.evaluation_input_eligible)
                self.assertFalse(result.research_evaluable)
                expected_verified_parent_ingest_seqs = tuple(
                    ingest_seq
                    for ingest_seq in matched_parent_ingest_seqs
                    for _ in range(2)
                )
                self.assertEqual(normalize_call_count, 2 * matched)
                self.assertEqual(reduce_call_count, 2 * matched)
                self.assertEqual(
                    tuple(normalized_parent_ingest_seqs),
                    expected_verified_parent_ingest_seqs,
                )
                self.assertEqual(
                    tuple(reduced_parent_ingest_seqs),
                    expected_verified_parent_ingest_seqs,
                )
                self.assertEqual(
                    parent_authorization_call_count,
                    matched,
                )
                self.assertEqual(
                    drained_phase1 + drained_companion,
                    abs(parent_count - companion_count),
                )
                self.assertGreaterEqual(
                    liveness_observations,
                    max(parent_count, companion_count),
                )
                self.assertEqual(maximum_live_pair_width, 1)
                state = replay_store_module._REPLAYS[
                    constructions[0]
                ]
                self.assertIsNone(state.get("current_parent"))
                self.assertIsNone(state.get("current_group"))
                for name in (
                    "current_parent_record_sha256",
                    "current_group_sha256",
                    "current_payload_seals",
                ):
                    self.assertNotIn(name, state)
                gc.collect()
                observe_liveness()
                live_raw_ids, live_group_ids = live_material_ids()
                self.assertEqual(
                    live_raw_ids - baseline_live_raw_ids,
                    set(),
                )
                self.assertEqual(
                    live_group_ids - baseline_live_group_ids,
                    set(),
                )
                self.assertEqual(
                    decoded_companion_count,
                    yielded_companion_count,
                )
                self.assertEqual(
                    decoded_companion_count,
                    companion_count,
                )
                self.assertEqual(
                    len(decoded_payload_tuple_ids),
                    companion_count,
                )
                self.assertEqual(
                    len(decoded_payload_byte_ids),
                    companion_count,
                )
                tuple_counts, byte_counts = (
                    payload_reference_counts(decoded_payload_tuples)
                )
                self.assertTrue(
                    all(
                        reference_count
                        == idle_payload_tuple_refcount
                        for reference_count in tuple_counts.values()
                    ),
                    "companion payload tuple survived replay close",
                )
                self.assertTrue(
                    all(
                        reference_count
                        == idle_payload_byte_refcount
                        for reference_count in byte_counts.values()
                    ),
                    "companion payload bytes survived replay close",
                )
                decoded_payload_tuples.clear()
                gc.collect()

    def test_service_abort_finally_and_access_denial_call_trace_are_exact(
        self,
    ) -> None:
        service_module = _module(RUNTIME_MODULE)
        service = service_module.replay_expert_session
        failure_surfaces = (
            "prepare_expert_replay_begin",
            "issue_expert_environment_collection_authority",
            "collect_expert_current_environment",
            "issue_begin_replay_authorization",
            "acknowledge_begin_replay",
            "read_next_replay_evidence_parent",
            "read_next_replay_companion_group",
            "issue_parent_group_replay_authorization",
            "acknowledge_parent_group_replay",
            "read_replay_finish_material",
            "issue_finish_replay_authorization",
            "acknowledge_finish_replay",
        )
        for surface in failure_surfaces:
            with (
                self.subTest(failure_surface=surface),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                stores.close_companion()
                constructions: list[
                    ExpertReplayConstructionAuthorityV1
                ] = []
                original_issue = (
                    service_module
                    .issue_expert_replay_construction_authority
                )
                original_abort = (
                    service_module.abort_expert_replay_construction
                )
                reader_snapshots: list[
                    tuple[
                        object | None,
                        object | None,
                        object | None,
                        int | None,
                    ]
                ] = []

                def capture_issue(
                    authority: ExpertJournalRootAuthorityV1,
                    **keywords: object,
                ) -> object:
                    result = original_issue(authority, **keywords)
                    if type(result) is ExpertReplayConstructionAuthorityV1:
                        constructions.append(result)
                    return result

                def capture_abort(
                    authority: ExpertReplayConstructionAuthorityV1,
                ) -> None:
                    reader_snapshots.append(
                        self._owned_reader_snapshot(authority)
                    )
                    original_abort(authority)

                with (
                    mock.patch.object(
                        service_module,
                        "issue_expert_replay_construction_authority",
                        side_effect=capture_issue,
                    ),
                    mock.patch.object(
                        service_module,
                        surface,
                        side_effect=RuntimeError(
                            f"injected:{surface}"
                        ),
                    ),
                    mock.patch.object(
                        service_module,
                        "abort_expert_replay_construction",
                        side_effect=capture_abort,
                    ) as abort_spy,
                    self.assertRaisesRegex(
                        RuntimeError,
                        rf"\Ainjected:{surface}\Z",
                    ),
                ):
                    service(
                        authority=stores.authority,
                        persistence_authorizer=stores.authorizer,
                        coordinator=stores.coordinator,
                        universe=stores.universe,
                        policy=stores.policy,
                    )
                self.assertEqual(len(constructions), 1)
                abort_spy.assert_called_once_with(constructions[0])
                self.assertEqual(len(reader_snapshots), 1)
                state = replay_store_module._REPLAYS[
                    constructions[0]
                ]
                self.assertEqual(state["state"], "aborted_closed")
                self.assertTrue(state["closed"])
                self.assertNotIn("phase1_reader", state)
                self.assertNotIn("companion_reader", state)
                self._assert_owned_readers_revoked(
                    reader_snapshots[0]
                )
                self._assert_same_root_operational(stores)

        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            trace: list[str] = []
            constructions: list[
                ExpertReplayConstructionAuthorityV1
            ] = []
            original_issue = (
                service_module.issue_expert_replay_construction_authority
            )
            original_ack_begin = (
                service_module.acknowledge_begin_replay
            )
            original_read = (
                service_module.read_next_replay_evidence_parent
            )
            original_take = service_module.take_expert_replay_denial
            denial_reader_snapshots: list[
                tuple[
                    object | None,
                    object | None,
                    object | None,
                    int | None,
                ]
            ] = []

            def capture_issue(
                authority: ExpertJournalRootAuthorityV1,
                **keywords: object,
            ) -> object:
                result = original_issue(authority, **keywords)
                if type(result) is ExpertReplayConstructionAuthorityV1:
                    constructions.append(result)
                return result

            def cross_deadline(*args: object, **keywords: object) -> None:
                original_ack_begin(*args, **keywords)
                stores.clock.now_ns = (
                    stores.phase1_manifest.required_retention_until_ns
                )

            def denied_read(*args: object, **keywords: object) -> object:
                trace.append("read_next_replay_evidence_parent")
                denial_reader_snapshots.append(
                    self._owned_reader_snapshot(constructions[0])
                )
                return original_read(*args, **keywords)

            def take(*args: object, **keywords: object) -> object:
                trace.append("take_expert_replay_denial")
                return original_take(*args, **keywords)

            with (
                mock.patch.object(
                    service_module,
                    "issue_expert_replay_construction_authority",
                    side_effect=capture_issue,
                ),
                mock.patch.object(
                    service_module,
                    "acknowledge_begin_replay",
                    side_effect=cross_deadline,
                ),
                mock.patch.object(
                    service_module,
                    "read_next_replay_evidence_parent",
                    side_effect=denied_read,
                ),
                mock.patch.object(
                    service_module,
                    "take_expert_replay_denial",
                    side_effect=take,
                ) as take_spy,
                mock.patch.object(
                    service_module,
                    "abort_expert_replay_construction",
                    wraps=(
                        service_module
                        .abort_expert_replay_construction
                    ),
                ) as abort_spy,
                mock.patch.object(
                    service_module,
                    "read_next_replay_companion_group",
                    wraps=(
                        service_module
                        .read_next_replay_companion_group
                    ),
                ) as later_read_spy,
            ):
                denial = service(
                    authority=stores.authority,
                    persistence_authorizer=stores.authorizer,
                    coordinator=stores.coordinator,
                    universe=stores.universe,
                    policy=stores.policy,
                )
            self.assertIs(type(denial), ExpertReplayDeniedV1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            )
            self.assertEqual(
                trace,
                [
                    "read_next_replay_evidence_parent",
                    "take_expert_replay_denial",
                ],
            )
            take_spy.assert_called_once_with(constructions[0])
            abort_spy.assert_not_called()
            later_read_spy.assert_not_called()
            state = replay_store_module._REPLAYS[
                constructions[0]
            ]
            self.assertEqual(state["state"], "denied_closed")
            self.assertEqual(len(denial_reader_snapshots), 1)
            self._assert_owned_readers_revoked(
                denial_reader_snapshots[0]
            )
            self._assert_same_root_operational(stores)

    def test_replay_bootstrap_diagnostic_proof_exact_fields_and_mutation_matrix(
        self,
    ) -> None:
        role_mismatches = {
            ExpertReplayDiagnosticRoleV1.PHASE1_MARKER: (
                ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH
            ),
            ExpertReplayDiagnosticRoleV1.PHASE1_WAL: (
                ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH
            ),
            ExpertReplayDiagnosticRoleV1.EXPERT_MARKER: (
                ExpertReplayMismatchV1.COMPANION_SCAN_INVALID
            ),
            ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL: (
                ExpertReplayMismatchV1.COMPANION_SCAN_INVALID
            ),
        }
        selected_missing_role = os.environ.get(
            self._BOOTSTRAP_MISSING_CHILD
        )
        for role, expected in role_mismatches.items():
            if (
                selected_missing_role is not None
                and role.value != selected_missing_role
            ):
                continue
            if (
                role is ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
                and selected_missing_role is None
            ):
                self.assertTrue(
                    self._run_bootstrap_missing_role_in_subprocess(role)
                )
                continue
            with (
                self.subTest(missing_role=role),
                _RealReplayStores() as stores,
            ):
                stores.close_companion()
                authority = stores.issue_replay()
                stores.paths()[role.value].unlink()
                denial = (
                    replay_store_facade.prepare_expert_replay_begin(
                        authority
                    )
                )
                self.assertIs(type(denial), ExpertReplayDeniedV1)
                self.assertIs(denial.result.mismatch, expected)
                self.assertEqual(len(denial.proof.file_proofs), 1)
                proof = denial.proof.file_proofs[0]
                self.assertIs(proof.role, role)
                self.assertIs(
                    proof.issue,
                    ExpertReplayDiagnosticIssueV1.ENTRY_MISSING,
                )
                self.assertFalse(proof.entry_present)
                self.assertEqual(
                    (
                        proof.device,
                        proof.inode,
                        proof.uid,
                        proof.mode,
                        proof.link_count,
                        proof.mtime_ns,
                        proof.ctime_ns,
                    ),
                    (None,) * 7,
                )
                self.assertEqual(
                    (
                        proof.observed_size,
                        proof.observed_prefix_length,
                        proof.observed_prefix_sha256,
                    ),
                    (0, 0, sha256(b"").hexdigest()),
                )
            if selected_missing_role is not None:
                return

        with _RealReplayStores() as stores:
            stores.close_companion()
            authority = stores.issue_replay()
            journal = stores.paths()["expert_journal"]
            corrupt = bytes(index % 251 for index in range(5_000))
            journal.write_bytes(corrupt)
            os.chmod(journal, 0o600)
            denial = replay_store_facade.prepare_expert_replay_begin(
                authority
            )
            self.assertIs(type(denial), ExpertReplayDeniedV1)
            self.assertIs(
                denial.result.mismatch,
                ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
            )
            self.assertEqual(len(denial.proof.file_proofs), 1)
            proof = denial.proof.file_proofs[0]
            self.assertIs(
                proof.role,
                ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL,
            )
            self.assertEqual(proof.observed_size, len(corrupt))
            self.assertEqual(proof.observed_prefix_length, 4096)
            self.assertEqual(
                proof.observed_prefix_sha256,
                sha256(corrupt[:4096]).hexdigest(),
            )
            self.assertFalse(
                {"prefix", "content", "bytes"}
                & {item.name for item in fields(proof)}
            )
            self.assertEqual(
                proof.proof_sha256,
                _independent_diagnostic_file_proof_sha256(proof),
            )
            self.assertEqual(
                denial.proof.proof_sha256,
                _independent_sha256(
                    b"INCI-EXPERT-REPLAY-DIAGNOSTIC-PROOF-V1\0",
                    {
                        item.name: getattr(denial.proof, item.name)
                        for item in fields(denial.proof)
                        if item.name != "proof_sha256"
                    },
                ),
            )

            for item in fields(proof):
                value = getattr(proof, item.name)
                if type(value) is bool:
                    changed: object = not value
                elif type(value) is int:
                    changed = value + 1
                elif type(value) is str:
                    changed = SHA_D
                elif type(value) is ExpertReplayDiagnosticRoleV1:
                    changed = next(
                        candidate
                        for candidate in ExpertReplayDiagnosticRoleV1
                        if candidate is not value
                    )
                elif type(value) is ExpertReplayDiagnosticIssueV1:
                    changed = next(
                        candidate
                        for candidate in ExpertReplayDiagnosticIssueV1
                        if candidate is not value
                    )
                elif value is None:
                    changed = 0
                else:
                    raise AssertionError(
                        f"unhandled file proof field: {item.name}"
                    )
                with (
                    self.subTest(file_proof_field=item.name),
                    self.assertRaises((TypeError, ValueError)),
                ):
                    _unchecked_replace(
                        proof,
                        **{item.name: changed},
                    )._validate()

            for item in fields(denial.proof):
                value = getattr(denial.proof, item.name)
                if type(value) is bool:
                    changed = not value
                elif type(value) is int:
                    changed = value + 1
                elif type(value) is str:
                    changed = SHA_D
                elif type(value) is ExpertReplayMismatchV1:
                    changed = next(
                        candidate
                        for candidate in ExpertReplayMismatchV1
                        if candidate is not value
                    )
                elif type(value) is tuple:
                    changed = ()
                elif value is None:
                    changed = object()
                else:
                    changed = object()
                with (
                    self.subTest(aggregate_proof_field=item.name),
                    self.assertRaises((TypeError, ValueError)),
                ):
                    _unchecked_replace(
                        denial.proof,
                        **{item.name: changed},
                    )._validate()

        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            authority, _ = self._prepare(stores)
            target = stores.paths()["phase1_marker"]
            self._replace_named_entry(target)
            original_proof = replay_store_module._identity_file_proof
            original_pread = replay_store_module._gated_pread_exact
            constructing_proof = False
            replaced_during_prefix = False

            def build_proof(
                state: dict[str, object],
                role: ExpertReplayDiagnosticRoleV1,
            ) -> object:
                nonlocal constructing_proof
                constructing_proof = True
                try:
                    return original_proof(state, role)
                finally:
                    constructing_proof = False

            def replace_during_pread(
                descriptor: int,
                offset: int,
                length: int,
                *,
                gate: object,
            ) -> bytes:
                nonlocal replaced_during_prefix
                data = original_pread(
                    descriptor,
                    offset,
                    length,
                    gate=gate,
                )
                if constructing_proof and not replaced_during_prefix:
                    self._replace_named_entry(target)
                    replaced_during_prefix = True
                return data

            with (
                mock.patch.object(
                    replay_store_module,
                    "_identity_file_proof",
                    side_effect=build_proof,
                ),
                mock.patch.object(
                    replay_store_module,
                    "_gated_pread_exact",
                    side_effect=replace_during_pread,
                ),
            ):
                denial = self._take_denial(
                    authority,
                    lambda: (
                        replay_store_facade
                        .issue_begin_replay_authorization(authority)
                    ),
                )
            self.assertTrue(replaced_during_prefix)
            self.assertEqual(len(denial.proof.file_proofs), 1)
            self.assertIs(
                denial.proof.file_proofs[0].issue,
                ExpertReplayDiagnosticIssueV1.ENTRY_REPLACED,
            )
            self.assertEqual(
                tuple(
                    proof.role for proof in denial.proof.file_proofs
                ),
                tuple(
                    sorted(
                        {
                            proof.role
                            for proof in denial.proof.file_proofs
                        },
                        key=lambda role: tuple(
                            ExpertReplayDiagnosticRoleV1
                        ).index(role),
                    )
                ),
            )

    def test_terminal_alignment_retention_and_evaluation_eligibility_matrix(
        self,
    ) -> None:
        service = _surface(RUNTIME_MODULE, "replay_expert_session")

        def replay(stores: _RealReplayStores) -> object:
            return service(
                authority=stores.authority,
                persistence_authorizer=stores.authorizer,
                coordinator=stores.coordinator,
                universe=stores.universe,
                policy=stores.policy,
            )

        def replay_result(value: object) -> ExpertReplayResultV1:
            if type(value) is ExpertReplayDeniedV1:
                return value.result
            self.assertIs(type(value), ExpertReplayResultV1)
            return value

        for reason in ("operator_stop", "session_end"):
            with (
                self.subTest(clean_reason=reason),
                _RealReplayStores() as stores,
            ):
                stores.append_parent()
                if reason == "session_end":
                    stores.phase1_fixture.now = (
                        stores.phase1_fixture.request.session_end_utc
                    )
                stores.close_phase1(reason)
                stores.close_companion()
                exact = replay_result(replay(stores))
                self.assertTrue(exact.exact_replay)
                self.assertTrue(exact.evidence_exact)
                self.assertTrue(exact.companion_valid)
                self.assertTrue(exact.terminals_aligned)
                self.assertTrue(exact.evaluation_input_eligible)
                self.assertFalse(exact.research_evaluable)

        with _RealReplayStores() as stores:
            stores.halt_phase1()
            halted = replay_result(replay(stores))
            self.assertIs(
                halted.mismatch,
                ExpertReplayMismatchV1.EVIDENCE_TERMINAL_NOT_CLEAN,
            )
            self.assertFalse(halted.exact_replay)
            self.assertFalse(halted.evaluation_input_eligible)
            self.assertFalse(halted.research_evaluable)

        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_phase1()
            missing = replay_result(replay(stores))
            self.assertIs(
                missing.mismatch,
                ExpertReplayMismatchV1.TERMINAL_MISSING,
            )
            self.assertFalse(missing.exact_replay)
            self.assertFalse(missing.evaluation_input_eligible)
            self.assertFalse(missing.research_evaluable)

        with _RealReplayStores() as stores:
            stores.append_parent()
            stores.close_companion()
            journal = stores.paths()["expert_journal"]
            descriptor = os.open(
                journal,
                os.O_WRONLY | os.O_APPEND,
            )
            try:
                os.write(descriptor, b"torn-companion-tail")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            torn = replay_result(replay(stores))
            self.assertIs(
                torn.mismatch,
                ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
            )
            self.assertFalse(torn.exact_replay)
            self.assertFalse(torn.evaluation_input_eligible)
            self.assertFalse(torn.research_evaluable)

        with _RealReplayStores() as stores:
            parent = stores.runtime.ingest(
                captured(
                    stores.authorizer,
                    provider_sequence="A-unacknowledged",
                )
            )
            group, payloads, candidate, _ = _independent_group(
                stores.manifest,
                stores.cursor,
                parent,
                prior_state_override=stores.state,
            )
            permit = replay_store_facade.issue_expert_append_permit(
                stores.writer,
                stores.cursor.expert_state_sha256,
                stores.cursor,
                group,
                payloads,
            )
            replay_store_facade.append_expert_group(permit)
            stores.close_phase1()
            unacknowledged = replay_result(replay(stores))
            self.assertFalse(unacknowledged.exact_replay)
            self.assertFalse(
                unacknowledged.evaluation_input_eligible
            )
            self.assertFalse(unacknowledged.research_evaluable)
            self.assertEqual(
                candidate.group_count,
                stores.cursor.group_count + 1,
            )
            self.assertEqual(
                replay_store_module._WRITERS[
                    stores.writer
                ].cursor,
                candidate,
            )
            self.assertEqual(
                replay_store_module._WRITERS[
                    stores.writer
                ].pending_cursor,
                candidate,
            )
            self.assertEqual(
                replay_store_module._WRITERS[
                    stores.writer
                ].state,
                "receipt_pending",
            )
            self.assertNotEqual(candidate, stores.cursor)

        for role in (
            ExpertReplayDiagnosticRoleV1.EXPERT_MARKER,
            ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL,
        ):
            with (
                self.subTest(replaced_role=role),
                _RealReplayStores() as stores,
            ):
                stores.close_companion()
                authority, _ = self._prepare(stores)
                self._replace_named_entry(
                    stores.paths()[role.value]
                )
                denied = self._take_denial(
                    authority,
                    lambda: (
                        replay_store_facade
                        .issue_begin_replay_authorization(
                            authority
                        )
                    ),
                )
                result = replay_result(denied)
                self.assertFalse(result.exact_replay)
                self.assertFalse(result.evaluation_input_eligible)
                self.assertFalse(result.research_evaluable)

        with _RealReplayStores() as stores:
            paths = stores.paths()
            capability = (
                replay_store_facade.issue_expert_purge_capability(
                    stores.authority,
                    stores.manifest,
                )
            )
            deletion_order: list[str] = []
            original_unlink = replay_store_module._unlink_if_present

            def record_unlink(
                directory_fd: int,
                basename: str,
            ) -> None:
                deletion_order.append(basename)
                original_unlink(directory_fd, basename)

            with mock.patch.object(
                replay_store_module,
                "_unlink_if_present",
                side_effect=record_unlink,
            ):
                replay_store_facade.purge_expert_session(capability)
            expected_order = (
                paths["expert_journal"].name,
                paths["expert_reserve"].name,
                paths["expert_marker"].name,
            )
            self.assertEqual(
                tuple(deletion_order[:3]),
                expected_order,
            )
            self.assertTrue(
                all(not paths[name].exists() for name in (
                    "expert_journal",
                    "expert_reserve",
                    "expert_marker",
                ))
            )

        if not hasattr(os, "fork"):
            self.skipTest("retention-expiry isolation requires fork")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            due_child = os.fork()
        if due_child == 0:
            try:
                with _RealReplayStores() as stores:
                    stores.close_companion()
                    session_id = stores.manifest.session_id
                    stores.clock.now_ns = (
                        stores.manifest.retention.retention_delete_by_ns
                    )
                    stores.coordinator.recover_and_purge()
                    due_report = (
                        replay_store_facade
                        .recover_and_purge_expert_journals(
                            stores.authority
                        )
                    )
                    if (
                        due_report.due_sessions != (session_id,)
                        or stores.paths()["expert_journal"].exists()
                    ):
                        os._exit(91)
            except BaseException:
                os._exit(92)
            os._exit(0)
        waited, due_status = os.waitpid(due_child, 0)
        self.assertEqual(waited, due_child)
        self.assertEqual(os.waitstatus_to_exitcode(due_status), 0)

        for replaced in (False, True):
            with self.subTest(evidence_replaced=replaced):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    evidence_child = os.fork()
                if evidence_child == 0:
                    try:
                        with _RealReplayStores() as stores:
                            stores.close_companion()
                            session_id = stores.manifest.session_id
                            marker = stores.paths()["phase1_marker"]
                            if replaced:
                                marker.write_bytes(b"{}")
                                os.chmod(marker, 0o600)
                            else:
                                marker.unlink()
                            report = (
                                replay_store_facade
                                .recover_and_purge_expert_journals(
                                    stores.authority
                                )
                            )
                            expected = (session_id,)
                            classified = (
                                report.evidence_replaced_sessions
                                if replaced
                                else report.evidence_missing_sessions
                            )
                            if classified != expected:
                                os._exit(93)
                            if stores.paths()[
                                "expert_journal"
                            ].exists():
                                os._exit(94)
                            if stores.paths()[
                                "expert_marker"
                            ].exists():
                                os._exit(95)
                    except BaseException:
                        os._exit(96)
                    os._exit(0)
                waited, evidence_status = os.waitpid(
                    evidence_child,
                    0,
                )
                self.assertEqual(waited, evidence_child)
                self.assertEqual(
                    os.waitstatus_to_exitcode(evidence_status),
                    0,
                )

    @unittest.skipUnless(hasattr(os, "fork"), "requires fork")
    def test_replay_crash_and_nonresumability_matrix_repeated_three_times(
        self,
    ) -> None:
        _surface(RUNTIME_MODULE, "replay_expert_session")
        phase1_sequencer = importlib.import_module(
            "tennis_v1.sequencer"
        )
        seams = (
            "raw_fsync",
            "partial_companion_write",
            "group_fsync_lost_receipt",
            "phase1_terminal",
            "same_live_session_catch_up",
            "companion_terminal",
        )
        expected_recovery_category: dict[str, str] = {
            "raw_fsync": "recovered_markers",
            "partial_companion_write": "recovered_markers",
            "group_fsync_lost_receipt": "recovered_markers",
            "phase1_terminal": "recovered_markers",
            "same_live_session_catch_up": "recovered_markers",
            "companion_terminal": "recovered_markers",
        }
        for seam in seams:
            for repetition in range(3):
                with self.subTest(seam=seam, repetition=repetition):
                    fixture = _test_events.SessionContractTests(
                        "test_session_manifest_requires_verified_eligible_matching_inputs"
                    )
                    fixture.setUp()
                    adapter_patch = mock.patch.multiple(
                        phase1_adapter_contract,
                        __file__=fixture.builder.adapter_file,
                        _ADAPTER_REGISTRY={
                            (
                                "synthetic-provider",
                                "trial-v1",
                            ): fixture.builder.registration
                        },
                    )
                    adapter_patch.start()
                    phase1 = fixture.build(
                        code_sha256=phase1_code_sha256(
                            ROOT / "tennis_v1"
                        )
                    )
                    clock = MutableClock(phase1.created_wall_ns)
                    session_id = phase1.session_id
                    state_root = fixture.root / "state"
                    paths = {
                        "phase1_marker": (
                            state_root
                            / "retention-markers"
                            / f"{session_id}.marker.json"
                        ),
                        "phase1_wal": (
                            state_root
                            / "sessions"
                            / f"{session_id}.wal"
                        ),
                        "expert_marker": (
                            state_root
                            / "expert-v1"
                            / "markers"
                            / (
                                f"{session_id}"
                                ".expert-retention-v1.json"
                            )
                        ),
                        "expert_journal": (
                            state_root
                            / "expert-v1"
                            / "sessions"
                            / (
                                f"{session_id}"
                                ".expert-journal-v1"
                            )
                        ),
                        "expert_reserve": (
                            state_root
                            / "expert-v1"
                            / "sessions"
                            / (
                                f"{session_id}"
                                ".expert-reserve-v1"
                            )
                        ),
                    }
                    ready_read, ready_write = os.pipe()
                    go_read, go_write = os.pipe()
                    with warnings.catch_warnings():
                        warnings.simplefilter(
                            "ignore",
                            DeprecationWarning,
                        )
                        child = os.fork()
                    if child == 0:
                        try:
                            os.close(ready_read)
                            os.close(go_write)

                            def crash_at_verified_seam() -> None:
                                os.write(ready_write, b"R")
                                os.close(ready_write)
                                if os.read(go_read, 1) != b"G":
                                    os._exit(91)
                                os.close(go_read)
                                os._exit(73)

                            coordinator = RetentionCoordinator.acquire(
                                fixture.config,
                                clock_ns=clock,
                            )
                            coordinator.recover_and_purge()
                            gate = ProviderGate(
                                fixture.config,
                                fixture.provider_manifest,
                                fixture.request,
                                environ={
                                    "SYNTHETIC_API_KEY": (
                                        "fixture-secret"
                                    )
                                },
                                clock=lambda: fixture.now,
                            )
                            authorizer = (
                                bind_provider_persistence_authorizer(
                                    gate=gate,
                                    coordinator=coordinator,
                                    session_manifest=phase1,
                                )
                            )
                            write_capability = (
                                coordinator.arm_before_wal(
                                    session_manifest=phase1,
                                    decision=authorizer.bound_decision,
                                    persistence_authorizer=authorizer,
                                )
                            )
                            phase1_writer = JournalWriter.create(
                                write_capability=write_capability,
                                session_manifest=phase1,
                            )
                            runtime = EventRuntime(
                                writer=phase1_writer,
                                state=phase1_initial_state(
                                    phase1.session_id
                                ),
                                persistence_authorizer=authorizer,
                                coordinator=coordinator,
                            )
                            root = (
                                replay_store_facade
                                .acquire_expert_journal_root(
                                    coordinator
                                    .issue_expert_state_root_account_lock_request()
                                )
                            )
                            try:
                                environment_authority = (
                                    replay_store_facade
                                    .issue_expert_environment_collection_authority(
                                        root,
                                        persistence_authorizer=authorizer,
                                        coordinator=coordinator,
                                    )
                                )
                                collected = (
                                    replay_store_facade
                                    .collect_expert_current_environment(
                                        environment_authority
                                    )
                                )
                            except ValueError:
                                if any(
                                    path.exists()
                                    for path in REPLAY_SOURCE_PATHS
                                ):
                                    raise
                                _, _, template = task6_artifacts()
                                collected = (
                                    contracts
                                    ._create_expert_collected_environment_v1(
                                        current=template.environment,
                                        normalizers=(
                                            template.normalizers
                                        ),
                                        structural_schemas=(
                                            template
                                            .structural_schemas
                                        ),
                                        event_schemas=(
                                            template.event_schemas
                                        ),
                                    )
                                )
                            universe, policy, manifest = (
                                _real_expert_manifest(
                                    phase1=phase1,
                                    session_start=(
                                        phase1_writer.session_start
                                    ),
                                    authorizer=authorizer,
                                    collected=collected,
                                )
                            )
                            expert_state = initial_expert_state(
                                manifest,
                                universe,
                                policy,
                            )
                            cursor = _genesis_cursor(
                                manifest,
                                expert_state,
                            )
                            expert_writer = (
                                replay_store_facade
                                .create_expert_journal(
                                    root,
                                    manifest,
                                    cursor,
                                    persistence_authorizer=authorizer,
                                    coordinator=coordinator,
                                )
                            )

                            def append_group(
                                parent: PersistedEvent,
                            ) -> None:
                                nonlocal cursor, expert_state
                                (
                                    group,
                                    payloads,
                                    candidate,
                                    reduction,
                                ) = _independent_group(
                                    manifest,
                                    cursor,
                                    parent,
                                    prior_state_override=expert_state,
                                )
                                permit = (
                                    replay_store_facade
                                    .issue_expert_append_permit(
                                        expert_writer,
                                        cursor.expert_state_sha256,
                                        cursor,
                                        group,
                                        payloads,
                                    )
                                )
                                receipt = (
                                    replay_store_facade
                                    .append_expert_group(permit)
                                )
                                replay_store_facade.acknowledge_expert_publication(
                                    expert_writer,
                                    receipt=receipt,
                                    candidate_state_sha256=(
                                        candidate
                                        .expert_state_sha256
                                    ),
                                    candidate_cursor=candidate,
                                )
                                cursor = candidate
                                expert_state = reduction.final_state

                            if seam == "raw_fsync":

                                def after_raw(
                                    *_: object,
                                    **__: object,
                                ) -> object:
                                    crash_at_verified_seam()
                                    raise AssertionError("unreachable")

                                with mock.patch.object(
                                    phase1_sequencer,
                                    "reduce_event",
                                    side_effect=after_raw,
                                ):
                                    runtime.ingest(
                                        captured(
                                            authorizer,
                                            provider_sequence="crash-raw",
                                        )
                                    )
                            elif seam == "partial_companion_write":
                                parent = runtime.ingest(
                                    captured(
                                        authorizer,
                                        provider_sequence=(
                                            "crash-partial"
                                        ),
                                    )
                                )
                                (
                                    group,
                                    payloads,
                                    _,
                                    _,
                                ) = _independent_group(
                                    manifest,
                                    cursor,
                                    parent,
                                    prior_state_override=expert_state,
                                )
                                permit = (
                                    replay_store_facade
                                    .issue_expert_append_permit(
                                        expert_writer,
                                        cursor.expert_state_sha256,
                                        cursor,
                                        group,
                                        payloads,
                                    )
                                )

                                def partial_write(
                                    descriptor: int,
                                    data: bytes,
                                ) -> None:
                                    length = max(1, len(data) // 2)
                                    os.write(
                                        descriptor,
                                        data[:length],
                                    )
                                    os.fsync(descriptor)
                                    crash_at_verified_seam()

                                with mock.patch.object(
                                    replay_store_module,
                                    "_complete_write",
                                    side_effect=partial_write,
                                ):
                                    replay_store_facade.append_expert_group(
                                        permit
                                    )
                            elif seam == "group_fsync_lost_receipt":
                                parent = runtime.ingest(
                                    captured(
                                        authorizer,
                                        provider_sequence=(
                                            "crash-group"
                                        ),
                                    )
                                )
                                (
                                    group,
                                    payloads,
                                    _,
                                    _,
                                ) = _independent_group(
                                    manifest,
                                    cursor,
                                    parent,
                                    prior_state_override=expert_state,
                                )
                                permit = (
                                    replay_store_facade
                                    .issue_expert_append_permit(
                                        expert_writer,
                                        cursor.expert_state_sha256,
                                        cursor,
                                        group,
                                        payloads,
                                    )
                                )
                                target_fd = (
                                    replay_store_module._WRITERS[
                                        expert_writer
                                    ].journal_fd
                                )
                                original_fsync = os.fsync

                                def crash_after_fsync(
                                    descriptor: int,
                                ) -> None:
                                    original_fsync(descriptor)
                                    if descriptor == target_fd:
                                        crash_at_verified_seam()

                                with mock.patch.object(
                                    os,
                                    "fsync",
                                    side_effect=crash_after_fsync,
                                ):
                                    replay_store_facade.append_expert_group(
                                        permit
                                    )
                            elif seam == "phase1_terminal":
                                parent = runtime.ingest(
                                    captured(
                                        authorizer,
                                        provider_sequence=(
                                            "crash-phase1-terminal"
                                        ),
                                    )
                                )
                                append_group(parent)
                                original_mark = (
                                    RetentionCoordinator
                                    .mark_clean_terminal
                                )

                                def crash_after_mark(
                                    instance: RetentionCoordinator,
                                    **keywords: object,
                                ) -> None:
                                    original_mark(
                                        instance,
                                        **keywords,
                                    )
                                    if instance is coordinator:
                                        crash_at_verified_seam()

                                with mock.patch.object(
                                    RetentionCoordinator,
                                    "mark_clean_terminal",
                                    crash_after_mark,
                                ):
                                    runtime.close_clean(
                                        "operator_stop"
                                    )
                            elif seam == "same_live_session_catch_up":
                                parents = tuple(
                                    runtime.ingest(
                                        captured(
                                            authorizer,
                                            provider_sequence=(
                                                f"crash-catch-up-{index}"
                                            ),
                                        )
                                    )
                                    for index in range(2)
                                )
                                runtime.close_clean("operator_stop")
                                for parent in parents:
                                    append_group(parent)
                                if (
                                    replay_store_facade
                                    .prove_expert_live_evidence_tail(
                                        expert_writer,
                                        published_cursor=cursor,
                                    )
                                    is not None
                                ):
                                    raise AssertionError(
                                        "catch-up incomplete"
                                    )
                                crash_at_verified_seam()
                            elif seam == "companion_terminal":
                                parent = runtime.ingest(
                                    captured(
                                        authorizer,
                                        provider_sequence=(
                                            "crash-companion-terminal"
                                        ),
                                    )
                                )
                                append_group(parent)
                                evidence_terminal = (
                                    runtime.close_clean(
                                        "operator_stop"
                                    )
                                )
                                if (
                                    replay_store_facade
                                    .prove_expert_live_evidence_tail(
                                        expert_writer,
                                        published_cursor=cursor,
                                    )
                                    is not None
                                ):
                                    raise AssertionError(
                                        "terminal tail uncovered"
                                    )
                                (
                                    observed_terminal,
                                    terminal,
                                ) = (
                                    replay_store_facade
                                    .build_aligned_expert_terminal(
                                        expert_writer,
                                        final_state=expert_state,
                                        final_cursor=cursor,
                                    )
                                )
                                if observed_terminal != evidence_terminal:
                                    raise AssertionError(
                                        "terminal misaligned"
                                    )
                                permit = (
                                    replay_store_facade
                                    .issue_expert_terminal_permit(
                                        expert_writer,
                                        terminal,
                                    )
                                )
                                target_fd = (
                                    replay_store_module._WRITERS[
                                        expert_writer
                                    ].journal_fd
                                )
                                original_fsync = os.fsync

                                def crash_after_fsync(
                                    descriptor: int,
                                ) -> None:
                                    original_fsync(descriptor)
                                    if descriptor == target_fd:
                                        crash_at_verified_seam()

                                with mock.patch.object(
                                    os,
                                    "fsync",
                                    side_effect=crash_after_fsync,
                                ):
                                    replay_store_facade.append_expert_terminal(
                                        permit
                                    )
                            else:
                                raise AssertionError(seam)
                            os._exit(92)
                        except BaseException as error:
                            try:
                                os.write(
                                    ready_write,
                                    (
                                        "E"
                                        + type(error).__name__
                                        + ":"
                                        + str(error)
                                    ).encode("utf-8", "replace"),
                                )
                            except OSError:
                                pass
                            os._exit(93)

                    os.close(ready_write)
                    os.close(go_read)
                    coordinator: RetentionCoordinator | None = None
                    try:
                        signal = os.read(ready_read, 8_192)
                        os.close(ready_read)
                        ready_read = -1
                        self.assertEqual(signal, b"R")
                        before = {
                            name: path.read_bytes()
                            for name, path in paths.items()
                            if path.exists()
                        }
                        self.assertTrue(before)
                        os.write(go_write, b"G")
                        os.close(go_write)
                        go_write = -1
                        waited, status = os.waitpid(child, 0)
                        self.assertEqual(waited, child)
                        self.assertEqual(
                            os.waitstatus_to_exitcode(status),
                            73,
                        )
                        self.assertEqual(
                            {
                                name: path.read_bytes()
                                for name, path in paths.items()
                                if path.exists()
                            },
                            before,
                        )

                        coordinator = RetentionCoordinator.acquire(
                            fixture.config,
                            clock_ns=clock,
                        )
                        coordinator.recover_and_purge()
                        root = (
                            replay_store_facade
                            .acquire_expert_journal_root(
                                coordinator
                                .issue_expert_state_root_account_lock_request()
                            )
                        )
                        report = (
                            replay_store_facade
                            .recover_and_purge_expert_journals(root)
                        )
                        self.assertEqual(
                            replay_store_facade
                            .sample_expert_retention_wall_ns(root),
                            clock.now_ns,
                        )
                        after = {
                            name: path.read_bytes()
                            for name, path in paths.items()
                            if path.exists()
                        }
                        for name, data in after.items():
                            self.assertIn(name, before)
                            self.assertEqual(data, before[name])
                        observed_categories = tuple(
                            name
                            for name in (
                                "due_sessions",
                                "evidence_missing_sessions",
                                "evidence_replaced_sessions",
                                "recovered_markers",
                            )
                            if getattr(report, name)
                        )
                        expected_category = (
                            expected_recovery_category[seam]
                        )
                        self.assertEqual(
                            observed_categories,
                            (expected_category,),
                        )
                        self.assertEqual(report.due_sessions, ())
                        self.assertEqual(
                            report.evidence_missing_sessions,
                            (),
                        )
                        self.assertEqual(
                            report.evidence_replaced_sessions,
                            (),
                        )
                        self.assertEqual(
                            report.recovered_markers,
                            (session_id,),
                        )
                        self.assertNotIn("expert_marker", after)
                        self.assertNotIn("expert_journal", after)
                        self.assertNotIn("expert_reserve", after)

                        gate = ProviderGate(
                            fixture.config,
                            fixture.provider_manifest,
                            fixture.request,
                            environ={
                                "SYNTHETIC_API_KEY": "fixture-secret"
                            },
                            clock=lambda: fixture.now,
                        )
                        authorizer = (
                            bind_provider_persistence_authorizer(
                                gate=gate,
                                coordinator=coordinator,
                                session_manifest=phase1,
                            )
                        )
                        try:
                            environment_authority = (
                                replay_store_facade
                                .issue_expert_environment_collection_authority(
                                    root,
                                    persistence_authorizer=authorizer,
                                    coordinator=coordinator,
                                )
                            )
                            collected = (
                                replay_store_facade
                                .collect_expert_current_environment(
                                    environment_authority
                                )
                            )
                        except ValueError:
                            if any(
                                path.exists()
                                for path in REPLAY_SOURCE_PATHS
                            ):
                                raise
                            _, _, template = task6_artifacts()
                            collected = (
                                contracts
                                ._create_expert_collected_environment_v1(
                                    current=template.environment,
                                    normalizers=template.normalizers,
                                    structural_schemas=(
                                        template.structural_schemas
                                    ),
                                    event_schemas=(
                                        template.event_schemas
                                    ),
                                )
                            )
                        session_start = _control_event(
                            phase1,
                            ingest_seq=1,
                            event_type="SESSION_START",
                            payload=canonical_session_manifest_bytes(
                                phase1
                            ),
                        )
                        (
                            recovered_universe,
                            recovered_policy,
                            recovered_manifest,
                        ) = _real_expert_manifest(
                            phase1=phase1,
                            session_start=session_start,
                            authorizer=authorizer,
                            collected=collected,
                        )
                        recovered_state = initial_expert_state(
                            recovered_manifest,
                            recovered_universe,
                            recovered_policy,
                        )
                        recovered_cursor = _genesis_cursor(
                            recovered_manifest,
                            recovered_state,
                        )

                        def physical_snapshot() -> dict[str, bytes]:
                            return {
                                name: path.read_bytes()
                                for name, path in paths.items()
                                if path.exists()
                            }

                        before_nonresume = physical_snapshot()
                        with self.assertRaises(ValueError):
                            (
                                replay_store_facade
                                .issue_expert_read_capability(
                                    root,
                                    recovered_manifest,
                                )
                            )
                        self.assertEqual(
                            physical_snapshot(),
                            before_nonresume,
                        )
                        with self.assertRaises(
                            ExpertLiveAuthorizationDenied
                        ):
                            replay_store_facade.create_expert_journal(
                                root,
                                recovered_manifest,
                                recovered_cursor,
                                persistence_authorizer=authorizer,
                                coordinator=coordinator,
                            )
                        self.assertEqual(
                            physical_snapshot(),
                            before_nonresume,
                        )
                    finally:
                        if ready_read >= 0:
                            try:
                                os.close(ready_read)
                            except OSError:
                                pass
                        if go_write >= 0:
                            try:
                                os.close(go_write)
                            except OSError:
                                pass
                        if coordinator is not None:
                            coordinator.close()
                        adapter_patch.stop()
                        fixture.tearDown()


class ReplayAuditP1ExactnessTests(unittest.TestCase):
    def test_all_replay_and_store_public_signatures_annotations_kinds_and_exports_are_exact(
        self,
    ) -> None:
        replay = _module(REPLAY_MODULE)
        expert_facade = _module(EXPERT_FACADE_MODULE)
        io_facade = _module(IO_FACADE_MODULE)
        runtime = _module(RUNTIME_MODULE)

        def annotation_text(value: object) -> str:
            return (
                value
                if type(value) is str
                else inspect.formatannotation(value)
            )

        def assert_signature(
            function: object,
            expected_parameters: tuple[
                tuple[str, inspect._ParameterKind, str],
                ...,
            ],
            expected_return: str,
        ) -> None:
            signature = inspect.signature(function)
            self.assertEqual(
                tuple(
                    (
                        name,
                        parameter.kind,
                        annotation_text(parameter.annotation),
                    )
                    for name, parameter in signature.parameters.items()
                ),
                expected_parameters,
            )
            self.assertEqual(
                annotation_text(signature.return_annotation),
                expected_return,
            )

        keyword = inspect.Parameter.KEYWORD_ONLY
        positional = inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert_signature(
            replay.begin_expert_replay,
            (
                ("manifest", keyword, "ExpertSessionManifestV1"),
                (
                    "current_environment",
                    keyword,
                    "ExpertCurrentEnvironmentV1",
                ),
                ("universe", keyword, "BindingUniverse"),
                ("policy", keyword, "SyncPolicy"),
                ("evidence", keyword, "EvidenceReplayContextV1"),
                (
                    "authorization",
                    keyword,
                    "RetentionReplayAuthorizationV1",
                ),
            ),
            "ExpertReplayAccumulatorV1",
        )
        assert_signature(
            replay.replay_expert_parent_group,
            (
                (
                    "accumulator",
                    positional,
                    "ExpertReplayAccumulatorV1",
                ),
                (
                    "authorization",
                    keyword,
                    "RetentionReplayAuthorizationV1",
                ),
                ("parent", keyword, "PersistedEvent"),
                ("stored_group", keyword, "ExpertJournalGroupV1"),
                (
                    "stored_payloads",
                    keyword,
                    "tuple[bytes, ...]",
                ),
            ),
            "ExpertReplayAccumulatorV1",
        )
        assert_signature(
            replay.finish_expert_replay,
            (
                (
                    "accumulator",
                    positional,
                    "ExpertReplayAccumulatorV1",
                ),
                (
                    "final_authorization",
                    keyword,
                    "RetentionReplayAuthorizationV1",
                ),
                (
                    "companion_terminal",
                    keyword,
                    "ExpertSessionTerminalV1 | None",
                ),
                (
                    "companion_scan",
                    keyword,
                    "ExpertJournalScanSummaryV1",
                ),
            ),
            "ExpertReplayResultV1",
        )
        assert_signature(
            runtime.replay_expert_session,
            (
                (
                    "authority",
                    keyword,
                    "ExpertJournalRootAuthorityV1",
                ),
                (
                    "persistence_authorizer",
                    keyword,
                    "ProviderPersistenceAuthorizer",
                ),
                ("coordinator", keyword, "RetentionCoordinator"),
                ("universe", keyword, "BindingUniverse"),
                ("policy", keyword, "SyncPolicy"),
            ),
            "ExpertReplayResultV1 | ExpertReplayDeniedV1",
        )

        store_specs = {
            "issue_expert_environment_collection_authority": (
                (
                    (
                        "authority",
                        positional,
                        "ExpertJournalRootAuthorityV1",
                    ),
                    (
                        "persistence_authorizer",
                        keyword,
                        "ProviderPersistenceAuthorizer",
                    ),
                    ("coordinator", keyword, "RetentionCoordinator"),
                ),
                "ExpertEnvironmentCollectionAuthorityV1",
            ),
            "collect_expert_current_environment": (
                ((
                    "authority",
                    positional,
                    "ExpertEnvironmentCollectionAuthorityV1",
                ),),
                "ExpertCollectedEnvironmentV1",
            ),
            "issue_expert_replay_construction_authority": (
                (
                    (
                        "authority",
                        positional,
                        "ExpertJournalRootAuthorityV1",
                    ),
                    (
                        "persistence_authorizer",
                        keyword,
                        "ProviderPersistenceAuthorizer",
                    ),
                    ("coordinator", keyword, "RetentionCoordinator"),
                ),
                (
                    "ExpertReplayConstructionAuthorityV1"
                    " | ExpertReplayDeniedV1"
                ),
            ),
            "prepare_expert_replay_begin": (
                ((
                    "authority",
                    positional,
                    "ExpertReplayConstructionAuthorityV1",
                ),),
                "ExpertReplayBeginReadyV1 | ExpertReplayDeniedV1",
            ),
            "read_next_replay_evidence_parent": (
                ((
                    "authority",
                    positional,
                    "ExpertReplayConstructionAuthorityV1",
                ),),
                "PersistedEvent | None",
            ),
            "read_next_replay_companion_group": (
                ((
                    "authority",
                    positional,
                    "ExpertReplayConstructionAuthorityV1",
                ),),
                (
                    "tuple[ExpertJournalGroupV1, tuple[bytes, ...]]"
                    " | None"
                ),
            ),
            "read_replay_finish_material": (
                ((
                    "authority",
                    positional,
                    "ExpertReplayConstructionAuthorityV1",
                ),),
                (
                    "tuple[ExpertSessionTerminalV1 | None,"
                    " ExpertJournalScanSummaryV1]"
                ),
            ),
            "issue_begin_replay_authorization": (
                ((
                    "authority",
                    positional,
                    "ExpertReplayConstructionAuthorityV1",
                ),),
                "RetentionReplayAuthorizationV1",
            ),
            "acknowledge_begin_replay": (
                (
                    (
                        "authority",
                        positional,
                        "ExpertReplayConstructionAuthorityV1",
                    ),
                    (
                        "authorization",
                        keyword,
                        "RetentionReplayAuthorizationV1",
                    ),
                    (
                        "accumulator",
                        keyword,
                        "ExpertReplayAccumulatorV1",
                    ),
                ),
                "None",
            ),
            "issue_parent_group_replay_authorization": (
                ((
                    "authority",
                    positional,
                    "ExpertReplayConstructionAuthorityV1",
                ),),
                "RetentionReplayAuthorizationV1",
            ),
            "acknowledge_parent_group_replay": (
                (
                    (
                        "authority",
                        positional,
                        "ExpertReplayConstructionAuthorityV1",
                    ),
                    (
                        "authorization",
                        keyword,
                        "RetentionReplayAuthorizationV1",
                    ),
                    (
                        "accumulator",
                        keyword,
                        "ExpertReplayAccumulatorV1",
                    ),
                ),
                "None",
            ),
            "issue_finish_replay_authorization": (
                ((
                    "authority",
                    positional,
                    "ExpertReplayConstructionAuthorityV1",
                ),),
                "RetentionReplayAuthorizationV1",
            ),
            "acknowledge_finish_replay": (
                (
                    (
                        "authority",
                        positional,
                        "ExpertReplayConstructionAuthorityV1",
                    ),
                    (
                        "authorization",
                        keyword,
                        "RetentionReplayAuthorizationV1",
                    ),
                    ("result", keyword, "ExpertReplayResultV1"),
                ),
                "None",
            ),
            "take_expert_replay_denial": (
                ((
                    "authority",
                    positional,
                    "ExpertReplayConstructionAuthorityV1",
                ),),
                "ExpertReplayDeniedV1",
            ),
            "abort_expert_replay_construction": (
                ((
                    "authority",
                    positional,
                    "ExpertReplayConstructionAuthorityV1",
                ),),
                "None",
            ),
        }
        self.assertEqual(tuple(store_specs), STORE_REPLAY_SURFACES)
        for name, (parameters, return_annotation) in store_specs.items():
            with self.subTest(store_surface=name):
                assert_signature(
                    getattr(io_facade, name),
                    parameters,
                    return_annotation,
                )
        self.assertEqual(expert_facade.__all__, EXPERT_FACADE_EXPORTS)
        self.assertEqual(io_facade.__all__, IO_FACADE_EXPORTS)
        self.assertEqual(
            tuple(
                name for name in expert_facade.__dict__
                if not name.startswith("_")
            ),
            EXPERT_FACADE_EXPORTS,
        )

    def test_direct_exact_type_matrix_covers_subclasses_bool_and_nested_members(
        self,
    ) -> None:
        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        parent_step = _surface(
            REPLAY_MODULE,
            "replay_expert_parent_group",
        )
        finish = _surface(REPLAY_MODULE, "finish_expert_replay")
        service = _surface(RUNTIME_MODULE, "replay_expert_session")
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=1)
        begin_authorization = _authorization(
            manifest,
            evidence,
            operation="begin",
            sequence=0,
        )
        begin_values = {
            "manifest": manifest,
            "current_environment": manifest.environment,
            "universe": universe,
            "policy": policy,
            "evidence": evidence,
            "authorization": begin_authorization,
        }
        for name, value in begin_values.items():
            with self.subTest(direct_begin=name), self.assertRaises(TypeError):
                begin(**{**begin_values, name: object()})
            value_type = type(value)
            try:
                subclass = type(
                    "ReplayInputSubclass",
                    (value_type,),
                    {},
                )
                subclass_value = object.__new__(subclass)
                for item in fields(value):
                    object.__setattr__(
                        subclass_value,
                        item.name,
                        getattr(value, item.name),
                    )
            except (TypeError, AttributeError):
                continue
            with self.subTest(subclass=name), self.assertRaises(TypeError):
                begin(**{**begin_values, name: subclass_value})

        with self.assertRaises(TypeError):
            begin(
                **{
                    **begin_values,
                    "authorization": _unchecked_replace(
                        begin_authorization,
                        authorization_sequence=True,
                    ),
                }
            )
        accumulator = begin(**begin_values)
        parent = raw_parent(
            session_id=manifest.session_id,
            ingest_seq=2,
        )
        group, payloads, _, _ = _independent_group(
            manifest,
            accumulator.cursor,
            parent,
            prior_state_override=accumulator.state,
        )
        parent_authorization = _authorization(
            manifest,
            evidence,
            operation="parent_group",
            sequence=1,
            expected_parent_ingest_seq=2,
        )
        with self.assertRaises(TypeError):
            parent_step(
                accumulator,
                authorization=parent_authorization,
                parent=parent,
                stored_group=group,
                stored_payloads=(bytearray(payloads[0]),),
            )
        with self.assertRaises(TypeError):
            parent_step(
                accumulator,
                authorization=parent_authorization,
                parent=parent,
                stored_group=group,
                stored_payloads=[*payloads],  # type: ignore[arg-type]
            )

        corrupted = _unchecked_replace(
            group,
            records=(
                _unchecked_replace(
                    group.records[0],
                    record_sha256=SHA_D,
                ),
            ),
        )
        typed = parent_step(
            accumulator,
            authorization=parent_authorization,
            parent=parent,
            stored_group=corrupted,
            stored_payloads=payloads,
        )
        self.assertIs(
            typed.mismatch,
            ExpertReplayMismatchV1.RECORD_DIGEST_MISMATCH,
        )
        self.assertIs(typed.state, accumulator.state)
        self.assertEqual(typed.cursor, accumulator.cursor)

        finish_authorization = _authorization(
            manifest,
            evidence,
            operation="finish",
            sequence=1,
        )
        with self.assertRaises(TypeError):
            finish(
                accumulator,
                final_authorization=finish_authorization,
                companion_terminal=None,
                companion_scan=_unchecked_replace(
                    _clean_scan(accumulator.cursor),
                    group_count=True,
                ),
            )

        root, authorizer, coordinator = _service_bindings(evidence)
        service_values = {
            "authority": root,
            "persistence_authorizer": authorizer,
            "coordinator": coordinator,
            "universe": universe,
            "policy": policy,
        }
        for name in service_values:
            with self.subTest(service=name), self.assertRaises(TypeError):
                service(**{**service_values, name: object()})

        io_facade = _module(IO_FACADE_MODULE)
        wrong_authority_calls = (
            lambda: io_facade.issue_expert_environment_collection_authority(
                object(),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            ),
            lambda: io_facade.collect_expert_current_environment(object()),
            lambda: io_facade.issue_expert_replay_construction_authority(
                object(),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            ),
            lambda: io_facade.prepare_expert_replay_begin(object()),
            lambda: io_facade.read_next_replay_evidence_parent(object()),
            lambda: io_facade.read_next_replay_companion_group(object()),
            lambda: io_facade.read_replay_finish_material(object()),
            lambda: io_facade.issue_begin_replay_authorization(object()),
            lambda: io_facade.acknowledge_begin_replay(
                object(),
                authorization=begin_authorization,
                accumulator=accumulator,
            ),
            lambda: io_facade.issue_parent_group_replay_authorization(
                object()
            ),
            lambda: io_facade.acknowledge_parent_group_replay(
                object(),
                authorization=parent_authorization,
                accumulator=accumulator,
            ),
            lambda: io_facade.issue_finish_replay_authorization(object()),
            lambda: io_facade.acknowledge_finish_replay(
                object(),
                authorization=finish_authorization,
                result=ExpertReplayResultV1(
                    state=None,
                    trace_sha256=None,
                    evidence_raw_count=0,
                    evidence_derived_count=0,
                    expert_group_count=0,
                    expert_record_count=0,
                    evidence_exact=False,
                    companion_valid=False,
                    terminals_aligned=False,
                    exact_replay=False,
                    mismatch=(
                        ExpertReplayMismatchV1.TERMINAL_MISSING
                    ),
                    final_authorization_sha256=None,
                    evaluation_input_eligible=False,
                    research_evaluable=False,
                ),
            ),
            lambda: io_facade.take_expert_replay_denial(object()),
            lambda: io_facade.abort_expert_replay_construction(object()),
        )
        for index, operation in enumerate(wrong_authority_calls):
            with self.subTest(store_surface=index), self.assertRaises(
                TypeError
            ):
                operation()

    def test_begin_mismatches_1_to_12_use_self_consistent_objects_and_required_collisions(
        self,
    ) -> None:
        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=1)
        authorization = _authorization(
            manifest,
            evidence,
            operation="begin",
            sequence=0,
        )
        changed_start = replace(
            evidence.session_start,
            payload=b'{"context":"changed"}',
            payload_sha256=sha256(
                b'{"context":"changed"}'
            ).hexdigest(),
        )
        nonexact_result = replace(
            evidence.replay_result,
            exact_replay=False,
            replay_mismatch=ReplayMismatch.STATE,
        )
        environment_fields = tuple(
            item.name for item in fields(manifest.environment)
            if item.name != "schema_version"
        )
        malformed_manifest = _unchecked_replace(
            manifest,
            normalizers=object(),
        )
        mutations = (
            (
                ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
                {
                    "evidence": _unchecked_replace(
                        evidence,
                        session_start=changed_start,
                        session_start_record_sha256=(
                            canonical_record_sha256(changed_start)
                        ),
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.EVIDENCE_REPLAY_NOT_EXACT,
                {
                    "evidence": _unchecked_replace(
                        evidence,
                        replay_result=nonexact_result,
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.EVIDENCE_TERMINAL_NOT_CLEAN,
                {
                    "evidence": _evidence_context(
                        raw_count=1,
                        terminal_clean=False,
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.EVIDENCE_SESSION_MISMATCH,
                {
                    "authorization": _rehash_authorization(
                        authorization,
                        session_id=(
                            "22222222-2222-4222-8222-222222222222"
                        ),
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.EVIDENCE_MANIFEST_MISMATCH,
                {
                    "manifest": _rehash_manifest(
                        manifest,
                        evidence_session_start_record_sha256=SHA_D,
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
                {
                    "authorization": _rehash_authorization(
                        authorization,
                        authorized_operation="finish",
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                {
                    "authorization": _rehash_authorization(
                        authorization,
                        final_sampled_wall_ns=(
                            authorization.common_deadline_ns
                        ),
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
                {
                    "authorization": _rehash_authorization(
                        authorization,
                        evidence_marker_identity=_identity(
                            "phase1_marker",
                            session_anchor_sha256=SHA_D,
                            inode=90,
                        ),
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
                {
                    "current_environment": replace(
                        manifest.environment,
                        runtime_code_sha256=SHA_D,
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.RETENTION_BINDING_MISMATCH,
                {
                    "authorization": _rehash_authorization(
                        authorization,
                        common_deadline_ns=(
                            authorization.common_deadline_ns + 1
                        ),
                    )
                },
            ),
            (
                ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
                {"manifest": malformed_manifest},
            ),
            (
                ExpertReplayMismatchV1.COMPANION_MANIFEST_MISMATCH,
                {
                    "policy": replace(
                        policy,
                        max_score_age_ns=policy.max_score_age_ns + 1,
                    )
                },
            ),
        )
        base = {
            "manifest": manifest,
            "current_environment": manifest.environment,
            "universe": universe,
            "policy": policy,
            "evidence": evidence,
            "authorization": authorization,
        }
        for expected, changes in mutations:
            with self.subTest(target=expected):
                result = begin(**{**base, **changes})
                self.assertIs(result.mismatch, expected)
                changed_authorization = changes.get("authorization")
                if type(changed_authorization) is RetentionReplayAuthorizationV1:
                    values = {
                        item.name: getattr(
                            changed_authorization,
                            item.name,
                        )
                        for item in fields(changed_authorization)
                        if item.name != "authorization_sha256"
                    }
                    self.assertEqual(
                        changed_authorization.authorization_sha256,
                        _independent_sha256(
                            b"INCI-EXPERT-REPLAY-AUTHORIZATION-V1\0",
                            values,
                        ),
                    )

        def compose_same_argument(
            name: str,
            first: object,
            second: object,
        ) -> object:
            original = base[name]
            if name == "authorization":
                assert type(first) is RetentionReplayAuthorizationV1
                assert type(second) is RetentionReplayAuthorizationV1
                changes = {
                    item.name: getattr(first, item.name)
                    for item in fields(first)
                    if item.name != "authorization_sha256"
                    and getattr(first, item.name)
                    != getattr(original, item.name)
                }
                changes.update(
                    {
                        item.name: getattr(second, item.name)
                        for item in fields(second)
                        if item.name != "authorization_sha256"
                        and getattr(second, item.name)
                        != getattr(original, item.name)
                    }
                )
                return _rehash_authorization(authorization, **changes)
            if name == "evidence":
                assert type(first) is EvidenceReplayContextV1
                assert type(second) is EvidenceReplayContextV1
                changes = {
                    item.name: getattr(second, item.name)
                    for item in fields(first)
                    if getattr(second, item.name)
                    != getattr(original, item.name)
                }
                changes.update(
                    {
                        item.name: getattr(first, item.name)
                        for item in fields(second)
                        if getattr(first, item.name)
                        != getattr(original, item.name)
                    }
                )
                if (
                    first.replay_result != original.replay_result
                    and second.replay_result != original.replay_result
                ):
                    changes["replay_result"] = replace(
                        second.replay_result,
                        replay_mismatch=(
                            first.replay_result.replay_mismatch
                        ),
                    )
                return _unchecked_replace(evidence, **changes)
            if name == "manifest":
                assert type(first) is ExpertSessionManifestV1
                assert type(second) is ExpertSessionManifestV1
                changes = {
                    item.name: getattr(first, item.name)
                    for item in fields(first)
                    if item.name != "manifest_sha256"
                    and getattr(first, item.name)
                    != getattr(original, item.name)
                }
                changes.update(
                    {
                        item.name: getattr(second, item.name)
                        for item in fields(second)
                        if item.name != "manifest_sha256"
                        and getattr(second, item.name)
                        != getattr(original, item.name)
                    }
                )
                if any(type(value) is object for value in changes.values()):
                    return _unchecked_replace(manifest, **changes)
                return _rehash_manifest(manifest, **changes)
            if name == "policy":
                assert type(first) is SyncPolicy
                assert type(second) is SyncPolicy
                changes = {
                    item.name: getattr(first, item.name)
                    for item in fields(first)
                    if getattr(first, item.name)
                    != getattr(original, item.name)
                }
                changes.update(
                    {
                        item.name: getattr(second, item.name)
                        for item in fields(second)
                        if getattr(second, item.name)
                        != getattr(original, item.name)
                    }
                )
                return replace(policy, **changes)
            raise AssertionError(name)

        for index in range(len(mutations) - 1):
            expected = mutations[index][0]
            first = mutations[index][1]
            second = mutations[index + 1][1]
            combined = dict(second)
            for name, value in first.items():
                combined[name] = (
                    compose_same_argument(
                        name,
                        value,
                        combined[name],
                    )
                    if name in combined
                    else value
                )
            with self.subTest(adjacent=index + 1):
                result = begin(**{**base, **combined})
                self.assertIs(result.mismatch, expected)

        for expected, earlier in mutations[:10]:
            with self.subTest(companion_collision=expected):
                collision = dict(earlier)
                collision["manifest"] = (
                    _unchecked_replace(
                        collision["manifest"],
                        normalizers=object(),
                    )
                    if "manifest" in collision
                    else malformed_manifest
                )
                result = begin(
                    **{
                        **base,
                        **collision,
                    }
                )
                self.assertIs(result.mismatch, expected)

        authorization_loss_and_identity_replacement = (
            _rehash_authorization(
                authorization,
                authorized_operation="finish",
                evidence_marker_identity=_identity(
                    "phase1_marker",
                    session_anchor_sha256=SHA_D,
                    inode=90,
                ),
            )
        )
        collision = begin(
            **{
                **base,
                "authorization": (
                    authorization_loss_and_identity_replacement
                ),
            }
        )
        self.assertIs(
            collision.mismatch,
            ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
        )

        for field_name in environment_fields:
            with self.subTest(environment_field=field_name):
                changed_environment = replace(
                    manifest.environment,
                    **{field_name: SHA_D},
                )
                result = begin(
                    **{
                        **base,
                        "current_environment": changed_environment,
                    }
                )
                self.assertIs(
                    result.mismatch,
                    ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
                )
                for earlier_expected, earlier in mutations[:8]:
                    collision = begin(
                        **{
                            **base,
                            "current_environment": changed_environment,
                            **earlier,
                        }
                    )
                    self.assertIs(
                        collision.mismatch,
                        earlier_expected,
                    )

    def test_item_12_checks_every_manifest_expressible_universe_policy_and_provider_relation(
        self,
    ) -> None:
        service_module = _module(RUNTIME_MODULE)
        service = service_module.replay_expert_session
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=0)
        provider_values = {
            item.name: getattr(manifest.provider_domain, item.name)
            for item in fields(manifest.provider_domain)
            if item.name != "provider_domain_binding_sha256"
        }
        provider_values["revision_domain_id"] = "revision-other"
        provider_values["provider_domain_binding_sha256"] = (
            compute_expert_provider_domain_binding_sha256(
                **provider_values
            )
        )
        changed_provider = ExpertProviderDomainBindingV1(**provider_values)

        def changed_capacity(**changes: object) -> object:
            values = {
                item.name: changes.get(
                    item.name,
                    getattr(manifest.capacity, item.name),
                )
                for item in fields(manifest.capacity)
                if item.name != "proof_sha256"
            }
            return type(manifest.capacity)(
                **values,
                proof_sha256=compute_expert_capacity_proof_sha256(
                    **values
                ),
            )

        universe_provider_values = {
            item.name: getattr(manifest.provider_domain, item.name)
            for item in fields(manifest.provider_domain)
            if item.name != "provider_domain_binding_sha256"
        }
        universe_provider_values["match_binding_universe_sha256"] = SHA_D
        universe_provider = ExpertProviderDomainBindingV1(
            **universe_provider_values,
            provider_domain_binding_sha256=(
                compute_expert_provider_domain_binding_sha256(
                    **universe_provider_values
                )
            ),
        )
        manifest_variants = (
            (
                "universe_digest",
                _rehash_manifest(
                    manifest,
                    match_binding_universe_sha256=SHA_D,
                    provider_domain=universe_provider,
                    capacity=changed_capacity(
                        match_binding_universe_sha256=SHA_D,
                    ),
                ),
                universe,
                policy,
            ),
            (
                "raw_artifact_id",
                _rehash_manifest(
                    manifest,
                    binding_raw_artifact_id="binding-artifact-other",
                ),
                universe,
                policy,
            ),
            (
                "raw_artifact_digest",
                _rehash_manifest(
                    manifest,
                    binding_raw_artifact_sha256=SHA_D,
                ),
                universe,
                policy,
            ),
            (
                "review_artifact_id",
                _rehash_manifest(
                    manifest,
                    binding_review_artifact_id="binding-review-other",
                ),
                universe,
                policy,
            ),
            (
                "review_artifact_digest",
                _rehash_manifest(
                    manifest,
                    binding_review_artifact_sha256=SHA_D,
                ),
                universe,
                policy,
            ),
            (
                "provider_domain",
                _rehash_manifest(
                    manifest,
                    provider_domain=changed_provider,
                ),
                universe,
                policy,
            ),
            (
                "policy_digest",
                _rehash_manifest(
                    manifest,
                    sync_policy_sha256=SHA_D,
                    capacity=changed_capacity(
                        sync_policy_sha256=SHA_D,
                    ),
                ),
                universe,
                policy,
            ),
            (
                "policy_object",
                manifest,
                universe,
                replace(
                    policy,
                    max_book_age_ns=policy.max_book_age_ns + 1,
                ),
            ),
            (
                "policy_universe",
                manifest,
                universe,
                sync_policy(universe_sha256=SHA_D),
            ),
            (
                "empty_genesis",
                _rehash_manifest(
                    manifest,
                    initial_synchronization_sha256=SHA_D,
                ),
                universe,
                policy,
            ),
        )
        for name, stored_manifest, supplied_universe, supplied_policy in (
            manifest_variants
        ):
            with self.subTest(relation=name):
                root, authorizer, coordinator = _service_bindings(evidence)
                script = _ReplayFacadeScript(
                    manifest=stored_manifest,
                    evidence=evidence,
                    root=root,
                    parent_count=0,
                )
                with _patch_replay_facade(service_module, script):
                    result = service(
                        authority=root,
                        persistence_authorizer=authorizer,
                        coordinator=coordinator,
                        universe=supplied_universe,
                        policy=supplied_policy,
                    )
                self.assertIsInstance(result, ExpertReplayResultV1)
                self.assertIs(
                    result.mismatch,
                    ExpertReplayMismatchV1.COMPANION_MANIFEST_MISMATCH,
                )
                self.assertIn("ack_begin", script.trace)
                self.assertIn("read_finish", script.trace)
                self.assertIn("ack_finish", script.trace)
                self.assertNotIn("read_evidence", script.trace)
                self.assertFalse(result.evaluation_input_eligible)

    def test_parent_mismatches_13_16_to_29_keep_every_earlier_layer_valid(
        self,
    ) -> None:
        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        parent_step = _surface(
            REPLAY_MODULE,
            "replay_expert_parent_group",
        )
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=1)
        accumulator = begin(
            manifest=manifest,
            current_environment=manifest.environment,
            universe=universe,
            policy=policy,
            evidence=evidence,
            authorization=_authorization(
                manifest,
                evidence,
                operation="begin",
                sequence=0,
            ),
        )
        parent = raw_parent(
            session_id=manifest.session_id,
            ingest_seq=2,
        )
        group, payloads, _, reduction = _independent_group(
            manifest,
            accumulator.cursor,
            parent,
            prior_state_override=accumulator.state,
        )
        authorization = _authorization(
            manifest,
            evidence,
            operation="parent_group",
            sequence=1,
            expected_parent_ingest_seq=2,
        )

        descriptor_record = _unchecked_replace(
            group.records[0],
            payload=_unchecked_replace(
                group.records[0].payload,
                payload_encoding="wrong",
            ),
        )
        descriptor_group = _rehash_group(
            group,
            records=(descriptor_record,),
        )
        wrong_observation = replace(
            reduction.outcomes[0].payload.observation,
            reason=contracts.ExpertIgnoreReasonV1.EVENT_NOT_RELEVANT,
        )
        wrong_payload = canonical_expert_bytes(
            ExpertObservationIgnoredPayloadV1(wrong_observation)
        )
        normalized_group, normalized_payloads, _, _ = _independent_group(
            manifest,
            accumulator.cursor,
            parent,
            prior_state_override=accumulator.state,
            payloads_override=(wrong_payload,),
        )
        rejected_payload = canonical_expert_bytes(
            ExpertObservationRejectedPayloadV1(
                observation=reduction.outcomes[0].payload.observation,
                reason=contracts.ExpertRejectReasonV1.STATIC_SESSION_HALT,
            )
        )
        reduction_group, reduction_payloads, _, _ = _independent_group(
            manifest,
            accumulator.cursor,
            parent,
            prior_state_override=accumulator.state,
            payloads_override=(rejected_payload,),
            kinds_override=(ExpertEventKindV1.OBSERVATION_REJECTED,),
        )
        prior_record_group = _rehash_group(
            _unchecked_replace(
                group,
                prior_expert_record_sha256=SHA_D,
            )
        )
        prior_state_record = _unchecked_replace(
            group.records[0],
            prior_expert_state_sha256=SHA_D,
        )
        prior_state_group = _rehash_group(
            _unchecked_replace(
                group,
                prior_expert_state_sha256=SHA_D,
            ),
            records=(prior_state_record,),
        )
        schema_group = _rehash_group(
            group,
            records=(
                _unchecked_replace(
                    group.records[0],
                    event_schema_sha256=SHA_D,
                ),
            ),
        )
        changed_record = _unchecked_replace(
            group.records[0],
            record_sha256=SHA_D,
        )
        trace_values = {
            "schema_version": 1,
            "expert_seq": changed_record.expert_seq,
            "prior_trace_sha256": (
                group.trace_steps[0].prior_trace_sha256
            ),
            "expert_record_sha256": SHA_D,
            "post_expert_state_sha256": (
                changed_record.post_expert_state_sha256
            ),
        }
        changed_trace = ExpertTraceStepV1(
            **trace_values,
            post_trace_sha256=_independent_sha256(
                b"INCI-EXPERT-TRACE-STEP-V1\0",
                trace_values,
            ),
        )
        digest_values = {
            item.name: getattr(group, item.name)
            for item in fields(group)
            if item.name != "group_sha256"
        }
        digest_values.update(
            {
                "records": (changed_record,),
                "trace_steps": (changed_trace,),
                "final_expert_record_sha256": SHA_D,
                "post_trace_sha256": changed_trace.post_trace_sha256,
            }
        )
        record_digest_group = _unchecked_replace(
            group,
            **digest_values,
            group_sha256=_independent_sha256(
                b"INCI-EXPERT-JOURNAL-GROUP-V1\0",
                digest_values,
            ),
        )
        post_state_record = _unchecked_replace(
            group.records[0],
            post_expert_state_sha256=SHA_D,
        )
        post_state_group = _rehash_group(
            _unchecked_replace(
                group,
                post_expert_state_sha256=SHA_D,
            ),
            records=(post_state_record,),
        )
        trace_group = _rehash_group(
            group,
            initial_trace_sha256=SHA_D,
        )
        cases = (
            (
                ExpertReplayMismatchV1.EXPERT_SEQUENCE_MISMATCH,
                parent,
                _rehash_group(
                    _unchecked_replace(group, group_sequence=2)
                ),
                payloads,
            ),
            (
                ExpertReplayMismatchV1.PARENT_ORDER_MISMATCH,
                parent,
                _unchecked_replace(
                    group,
                    parent=_unchecked_replace(
                        group.parent,
                        ingest_seq=4,
                    ),
                ),
                payloads,
            ),
            (
                ExpertReplayMismatchV1.PARENT_KIND_MISMATCH,
                _unchecked_replace(
                    parent,
                    record_kind=RecordKind.DERIVED,
                ),
                group,
                payloads,
            ),
            (
                ExpertReplayMismatchV1.PARENT_DIGEST_MISMATCH,
                parent,
                _unchecked_replace(
                    group,
                    parent=_unchecked_replace(
                        group.parent,
                        record_sha256=SHA_D,
                    ),
                ),
                payloads,
            ),
            (
                ExpertReplayMismatchV1.PARENT_GROUP_SHAPE_MISMATCH,
                parent,
                _unchecked_replace(group, parent_output_count=2),
                payloads,
            ),
            (
                ExpertReplayMismatchV1.PRIOR_RECORD_CHAIN_MISMATCH,
                parent,
                prior_record_group,
                payloads,
            ),
            (
                ExpertReplayMismatchV1.PRIOR_STATE_MISMATCH,
                parent,
                prior_state_group,
                payloads,
            ),
            (
                ExpertReplayMismatchV1.EVENT_SCHEMA_UNPINNED,
                parent,
                schema_group,
                payloads,
            ),
            (
                ExpertReplayMismatchV1.RECORD_DIGEST_MISMATCH,
                parent,
                record_digest_group,
                payloads,
            ),
            (
                ExpertReplayMismatchV1.PAYLOAD_DESCRIPTOR_MISMATCH,
                parent,
                descriptor_group,
                payloads,
            ),
            (
                ExpertReplayMismatchV1.PAYLOAD_BYTES_MISMATCH,
                parent,
                group,
                (b"not-canonical-json",),
            ),
            (
                ExpertReplayMismatchV1.NORMALIZED_OBSERVATION_MISMATCH,
                parent,
                normalized_group,
                normalized_payloads,
            ),
            (
                ExpertReplayMismatchV1.REDUCTION_MISMATCH,
                parent,
                reduction_group,
                reduction_payloads,
            ),
            (
                ExpertReplayMismatchV1.POST_STATE_MISMATCH,
                parent,
                post_state_group,
                payloads,
            ),
            (
                ExpertReplayMismatchV1.TRACE_MISMATCH,
                parent,
                trace_group,
                payloads,
            ),
        )
        for expected, candidate_parent, candidate_group, candidate_payloads in (
            cases
        ):
            with self.subTest(target=expected):
                result = parent_step(
                    accumulator,
                    authorization=authorization,
                    parent=candidate_parent,
                    stored_group=candidate_group,
                    stored_payloads=candidate_payloads,
                )
                self.assertIs(result.mismatch, expected)
                self.assertIs(result.state, accumulator.state)
                self.assertEqual(result.cursor, accumulator.cursor)

        collision_mutations = (
            lambda p, g, b: (
                p,
                _unchecked_replace(g, group_sequence=2),
                b,
            ),
            lambda p, g, b: (
                p,
                _unchecked_replace(
                    g,
                    parent=_unchecked_replace(g.parent, ingest_seq=4),
                ),
                b,
            ),
            lambda p, g, b: (
                _unchecked_replace(
                    p,
                    record_kind=RecordKind.DERIVED,
                ),
                g,
                b,
            ),
            lambda p, g, b: (
                p,
                _unchecked_replace(
                    g,
                    parent=_unchecked_replace(
                        g.parent,
                        record_sha256=SHA_D,
                    ),
                ),
                b,
            ),
            lambda p, g, b: (
                p,
                _unchecked_replace(g, parent_output_count=2),
                b,
            ),
            lambda p, g, b: (
                p,
                _unchecked_replace(
                    g,
                    prior_expert_record_sha256=SHA_D,
                ),
                b,
            ),
            lambda p, g, b: (
                p,
                _unchecked_replace(g, prior_expert_state_sha256=SHA_D),
                b,
            ),
            lambda p, g, b: (
                p,
                _unchecked_replace(
                    g,
                    records=(
                        _unchecked_replace(
                            g.records[0],
                            event_schema_sha256=SHA_D,
                        ),
                    ),
                ),
                b,
            ),
            lambda p, g, b: (
                p,
                _unchecked_replace(
                    g,
                    records=(
                        _unchecked_replace(
                            g.records[0],
                            record_sha256=SHA_D,
                        ),
                    ),
                ),
                b,
            ),
            lambda p, g, b: (
                p,
                _unchecked_replace(
                    g,
                    records=(
                        _unchecked_replace(
                            g.records[0],
                            payload=_unchecked_replace(
                                g.records[0].payload,
                                payload_encoding="wrong",
                            ),
                        ),
                    ),
                ),
                b,
            ),
            lambda p, g, b: (p, g, (b"bad",)),
            lambda p, g, b: (p, normalized_group, normalized_payloads),
            lambda p, g, b: (p, reduction_group, reduction_payloads),
            lambda p, g, b: (
                p,
                _unchecked_replace(g, post_expert_state_sha256=SHA_D),
                b,
            ),
            lambda p, g, b: (
                p,
                _unchecked_replace(g, post_trace_sha256=SHA_D),
                b,
            ),
        )
        for index, (first, second) in enumerate(
            zip(
                collision_mutations,
                collision_mutations[1:],
            )
        ):
            candidate = first(parent, group, payloads)
            candidate = second(*candidate)
            if index == 9:
                candidate = (
                    parent,
                    descriptor_group,
                    (b"bad",),
                )
            elif index == 10:
                candidate = (
                    parent,
                    normalized_group,
                    (b"bad",),
                )
            elif index == 11:
                wrong_rejected_payload = canonical_expert_bytes(
                    ExpertObservationRejectedPayloadV1(
                        observation=wrong_observation,
                        reason=(
                            contracts.ExpertRejectReasonV1.STATIC_SESSION_HALT
                        ),
                    )
                )
                wrong_rejected_group, wrong_rejected_payloads, _, _ = (
                    _independent_group(
                        manifest,
                        accumulator.cursor,
                        parent,
                        prior_state_override=accumulator.state,
                        payloads_override=(wrong_rejected_payload,),
                        kinds_override=(
                            ExpertEventKindV1.OBSERVATION_REJECTED,
                        ),
                    )
                )
                candidate = (
                    parent,
                    wrong_rejected_group,
                    wrong_rejected_payloads,
                )
            with self.subTest(adjacent_collision=index):
                result = parent_step(
                    accumulator,
                    authorization=authorization,
                    parent=candidate[0],
                    stored_group=candidate[1],
                    stored_payloads=candidate[2],
                )
                self.assertIs(result.mismatch, cases[index][0])

    def test_pure_authorization_operation_sequence_anchor_parent_reuse_skip_and_finish_matrix(
        self,
    ) -> None:
        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        parent_step = _surface(
            REPLAY_MODULE,
            "replay_expert_parent_group",
        )
        finish = _surface(REPLAY_MODULE, "finish_expert_replay")
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=1)
        begin_token = _authorization(
            manifest,
            evidence,
            operation="begin",
            sequence=0,
        )
        begin_values = {
            "manifest": manifest,
            "current_environment": manifest.environment,
            "universe": universe,
            "policy": policy,
            "evidence": evidence,
        }
        static_mutations = (
            {"authorized_operation": "finish"},
            {"authorization_sequence": 1},
            {"authorization_sha256": SHA_D},
            {"evidence_session_manifest_sha256": SHA_D},
            {"evidence_session_start_record_sha256": SHA_D},
            {"evidence_terminal_record_sha256": SHA_D},
            {"expert_manifest_sha256": SHA_D},
            {"retention_binding_sha256": SHA_D},
            {"provider_request_binding_sha256": SHA_C},
            {"permission_artifact_sha256": SHA_D},
            {"qualification_artifact_sha256": SHA_D},
            {"qualification_trace_sha256": SHA_D},
            {
                "common_deadline_ns": (
                    begin_token.common_deadline_ns + 1
                )
            },
            {"expected_parent_ingest_seq": 2},
        )
        for changes in static_mutations:
            with self.subTest(begin_authorization=changes):
                token = (
                    _unchecked_replace(begin_token, **changes)
                    if "authorization_sha256" in changes
                    else _rehash_authorization(begin_token, **changes)
                )
                result = begin(
                    **begin_values,
                    authorization=token,
                )
                self.assertIs(
                    result.mismatch,
                    (
                        ExpertReplayMismatchV1.RETENTION_BINDING_MISMATCH
                        if "common_deadline_ns" in changes
                        else ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
                    ),
                )
                self.assertEqual(result.cursor.group_count, 0)

        for field_name, role in (
            ("evidence_marker_identity", "phase1_marker"),
            ("evidence_wal_identity", "phase1_wal"),
            ("companion_marker_identity", "expert_marker"),
            ("companion_journal_identity", "expert_journal"),
        ):
            with self.subTest(identity_anchor=field_name):
                token = _rehash_authorization(
                    begin_token,
                    **{
                        field_name: _identity(
                            role,
                            session_anchor_sha256=SHA_D,
                            inode=99,
                        )
                    },
                )
                result = begin(
                    **begin_values,
                    authorization=token,
                )
                self.assertIs(
                    result.mismatch,
                    ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
                )

        accumulator = begin(
            **begin_values,
            authorization=begin_token,
        )
        parent = raw_parent(
            session_id=manifest.session_id,
            ingest_seq=2,
        )
        group, payloads, _, _ = _independent_group(
            manifest,
            accumulator.cursor,
            parent,
            prior_state_override=accumulator.state,
        )
        parent_token = _authorization(
            manifest,
            evidence,
            operation="parent_group",
            sequence=1,
            expected_parent_ingest_seq=2,
        )
        for label, token in (
            ("reuse_begin", begin_token),
            (
                "skip",
                _rehash_authorization(
                    parent_token,
                    authorization_sequence=2,
                ),
            ),
            (
                "stale",
                _rehash_authorization(
                    parent_token,
                    authorization_sequence=0,
                ),
            ),
            (
                "wrong_parent",
                _rehash_authorization(
                    parent_token,
                    expected_parent_ingest_seq=4,
                ),
            ),
            (
                "finish_substitution",
                _rehash_authorization(
                    parent_token,
                    authorized_operation="finish",
                    expected_parent_ingest_seq=None,
                ),
            ),
        ):
            with self.subTest(parent_token=label):
                rejected = parent_step(
                    accumulator,
                    authorization=token,
                    parent=parent,
                    stored_group=group,
                    stored_payloads=payloads,
                )
                self.assertIs(
                    rejected.mismatch,
                    ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
                )
                self.assertEqual(rejected.cursor, accumulator.cursor)
                self.assertIs(rejected.state, accumulator.state)

        content_mismatch = parent_step(
            accumulator,
            authorization=parent_token,
            parent=parent,
            stored_group=_unchecked_replace(
                group,
                records=(
                    _unchecked_replace(
                        group.records[0],
                        record_sha256=SHA_D,
                    ),
                ),
            ),
            stored_payloads=payloads,
        )
        self.assertIs(
            content_mismatch.mismatch,
            ExpertReplayMismatchV1.RECORD_DIGEST_MISMATCH,
        )
        self.assertEqual(content_mismatch.last_authorization_sequence, 1)
        self.assertEqual(
            content_mismatch.last_authorization_sha256,
            parent_token.authorization_sha256,
        )
        finish_token = _authorization(
            manifest,
            evidence,
            operation="finish",
            sequence=1,
        )
        for label, token in (
            ("reuse_begin", begin_token),
            ("parent_substitution", parent_token),
            (
                "skip_finish",
                _rehash_authorization(
                    finish_token,
                    authorization_sequence=2,
                ),
            ),
        ):
            with self.subTest(finish_token=label):
                result = finish(
                    accumulator,
                    final_authorization=token,
                    companion_terminal=None,
                    companion_scan=_clean_scan(accumulator.cursor),
                )
                self.assertIs(
                    result.mismatch,
                    ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
                )

    def test_later_record_prior_chain_and_state_keep_items_20_and_21(
        self,
    ) -> None:
        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        parent_step = _surface(
            REPLAY_MODULE,
            "replay_expert_parent_group",
        )
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=1)
        accumulator = begin(
            manifest=manifest,
            current_environment=manifest.environment,
            universe=universe,
            policy=policy,
            evidence=evidence,
            authorization=_authorization(
                manifest,
                evidence,
                operation="begin",
                sequence=0,
            ),
        )
        parent = raw_parent(
            session_id=manifest.session_id,
            ingest_seq=2,
        )
        observations = bind_expert_observation_drafts(
            manifest,
            parent,
            manifest.normalizers.fallback,
            (
                ExpertSynchronizationDraftV1(synchronization_input()),
                ExpertSynchronizationDraftV1(synchronization_input()),
            ),
        )
        group, payloads, _, _ = _independent_group(
            manifest,
            accumulator.cursor,
            parent,
            prior_state_override=accumulator.state,
            observations_override=observations,
        )
        self.assertEqual(len(group.records), 2)
        authorization = _authorization(
            manifest,
            evidence,
            operation="parent_group",
            sequence=1,
            expected_parent_ingest_seq=2,
        )
        replay_module = _module(REPLAY_MODULE)
        cases = (
            (
                ExpertReplayMismatchV1.PRIOR_RECORD_CHAIN_MISMATCH,
                {"prior_expert_record_sha256": "f" * 64},
            ),
            (
                ExpertReplayMismatchV1.PRIOR_STATE_MISMATCH,
                {"prior_expert_state_sha256": "f" * 64},
            ),
            (
                ExpertReplayMismatchV1.PRIOR_RECORD_CHAIN_MISMATCH,
                {
                    "prior_expert_record_sha256": "f" * 64,
                    "prior_expert_state_sha256": "f" * 64,
                },
            ),
        )
        for expected, changes in cases:
            with self.subTest(expected=expected):
                records = list(group.records)
                changed_record = _unchecked_replace(
                    records[1],
                    **changes,
                )
                changed_values = {
                    item.name: getattr(changed_record, item.name)
                    for item in fields(changed_record)
                    if item.name != "record_sha256"
                }
                records[1] = _unchecked_replace(
                    changed_record,
                    record_sha256=(
                        compute_expert_journal_record_sha256(
                            **changed_values
                        )
                    ),
                )
                changed_group = _unchecked_replace(
                    group,
                    records=tuple(records),
                )
                with mock.patch.object(
                    replay_module,
                    "normalize_expert_parent",
                    return_value=observations,
                ):
                    result = parent_step(
                        accumulator,
                        authorization=authorization,
                        parent=parent,
                        stored_group=changed_group,
                        stored_payloads=payloads,
                    )
                self.assertIs(result.mismatch, expected)

    def test_finish_preserves_proven_items_1_to_5_before_new_auth_faults(
        self,
    ) -> None:
        finish = _surface(REPLAY_MODULE, "finish_expert_replay")
        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=0)
        accumulator = begin(
            manifest=manifest,
            current_environment=manifest.environment,
            universe=universe,
            policy=policy,
            evidence=evidence,
            authorization=_authorization(
                manifest,
                evidence,
                operation="begin",
                sequence=0,
            ),
        )
        finish_authorization = _authorization(
            manifest,
            evidence,
            operation="finish",
            sequence=1,
        )
        deadline_authorization = _rehash_authorization(
            finish_authorization,
            final_sampled_wall_ns=(
                finish_authorization.common_deadline_ns
            ),
        )
        invalid_authorization = _rehash_authorization(
            finish_authorization,
            authorized_operation="begin",
        )
        scan = _clean_scan(accumulator.cursor)
        cases = (
            (
                ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
                deadline_authorization,
                ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
            ),
            (
                ExpertReplayMismatchV1.EVIDENCE_MANIFEST_MISMATCH,
                invalid_authorization,
                ExpertReplayMismatchV1.EVIDENCE_MANIFEST_MISMATCH,
            ),
            (
                ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
                deadline_authorization,
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            ),
        )
        for prior, authorization, expected in cases:
            with self.subTest(prior=prior, expected=expected):
                result = finish(
                    _unchecked_replace(
                        accumulator,
                        mismatch=prior,
                    ),
                    final_authorization=authorization,
                    companion_terminal=None,
                    companion_scan=scan,
                )
                self.assertIs(result.mismatch, expected)

    def test_authorization_self_digest_precedes_items_7_8_and_10(
        self,
    ) -> None:
        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        parent_step = _surface(
            REPLAY_MODULE,
            "replay_expert_parent_group",
        )
        finish = _surface(REPLAY_MODULE, "finish_expert_replay")
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=1)
        begin_authorization = _authorization(
            manifest,
            evidence,
            operation="begin",
            sequence=0,
        )
        accumulator = begin(
            manifest=manifest,
            current_environment=manifest.environment,
            universe=universe,
            policy=policy,
            evidence=evidence,
            authorization=begin_authorization,
        )
        parent = raw_parent(
            session_id=manifest.session_id,
            ingest_seq=2,
        )
        group, payloads, _, _ = _independent_group(
            manifest,
            accumulator.cursor,
            parent,
            prior_state_override=accumulator.state,
        )
        parent_authorization = _authorization(
            manifest,
            evidence,
            operation="parent_group",
            sequence=1,
            expected_parent_ingest_seq=2,
        )
        finish_authorization = _authorization(
            manifest,
            evidence,
            operation="finish",
            sequence=1,
        )
        mutations = (
            (
                {
                    "final_sampled_wall_ns": (
                        begin_authorization.common_deadline_ns
                    )
                },
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            ),
            (
                {
                    "evidence_marker_identity": _identity(
                        "phase1_marker",
                        session_anchor_sha256=SHA_D,
                        inode=91,
                    )
                },
                ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
            ),
            (
                {
                    "common_deadline_ns": (
                        begin_authorization.common_deadline_ns + 1
                    )
                },
                ExpertReplayMismatchV1.RETENTION_BINDING_MISMATCH,
            ),
        )

        def invoke(
            operation: str,
            authorization: RetentionReplayAuthorizationV1,
        ) -> ExpertReplayResultV1 | ExpertReplayAccumulatorV1:
            if operation == "begin":
                return begin(
                    manifest=manifest,
                    current_environment=manifest.environment,
                    universe=universe,
                    policy=policy,
                    evidence=evidence,
                    authorization=authorization,
                )
            if operation == "parent":
                return parent_step(
                    accumulator,
                    authorization=authorization,
                    parent=parent,
                    stored_group=group,
                    stored_payloads=payloads,
                )
            return finish(
                accumulator,
                final_authorization=authorization,
                companion_terminal=None,
                companion_scan=_clean_scan(accumulator.cursor),
            )

        for operation, base in (
            ("begin", begin_authorization),
            ("parent", parent_authorization),
            ("finish", finish_authorization),
        ):
            for changes, later_mismatch in mutations:
                with self.subTest(
                    operation=operation,
                    later_mismatch=later_mismatch,
                ):
                    stale = _unchecked_replace(base, **changes)
                    self.assertIs(
                        invoke(operation, stale).mismatch,
                        ExpertReplayMismatchV1
                        .RETENTION_AUTHORIZATION_MISMATCH,
                    )
                    exact = _rehash_authorization(base, **changes)
                    self.assertIs(
                        invoke(operation, exact).mismatch,
                        later_mismatch,
                    )

    def test_pure_success_empty_one_many_and_multi_output_chains_are_exact(
        self,
    ) -> None:
        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        parent_step = _surface(
            REPLAY_MODULE,
            "replay_expert_parent_group",
        )
        finish = _surface(REPLAY_MODULE, "finish_expert_replay")

        for count in (0, 1, 5):
            with self.subTest(parent_count=count):
                universe, policy, manifest, evidence = _valid_artifacts(
                    raw_count=count
                )
                accumulator = begin(
                    manifest=manifest,
                    current_environment=manifest.environment,
                    universe=universe,
                    policy=policy,
                    evidence=evidence,
                    authorization=_authorization(
                        manifest,
                        evidence,
                        operation="begin",
                        sequence=0,
                    ),
                )
                parents, groups = _group_chain(manifest, count=count)
                for index, (parent, stored) in enumerate(
                    zip(parents, groups, strict=True),
                    start=1,
                ):
                    accumulator = parent_step(
                        accumulator,
                        authorization=_authorization(
                            manifest,
                            evidence,
                            operation="parent_group",
                            sequence=index,
                            expected_parent_ingest_seq=parent.ingest_seq,
                        ),
                        parent=parent,
                        stored_group=stored[0],
                        stored_payloads=stored[1],
                    )
                    self.assertEqual(
                        accumulator.cursor.group_count,
                        index,
                    )
                    self.assertEqual(
                        accumulator.cursor.expert_seq,
                        accumulator.cursor.record_count,
                    )
                    self.assertFalse(
                        {
                            "parent",
                            "group",
                            "payloads",
                        }
                        & {item.name for item in fields(accumulator)}
                    )
                finish_token = _authorization(
                    manifest,
                    evidence,
                    operation="finish",
                    sequence=count + 1,
                )
                result = finish(
                    accumulator,
                    final_authorization=finish_token,
                    companion_terminal=_terminal(accumulator),
                    companion_scan=_clean_scan(accumulator.cursor),
                )
                self.assertTrue(result.exact_replay)
                self.assertEqual(result.expert_group_count, count)
                self.assertEqual(
                    result.final_authorization_sha256,
                    finish_token.authorization_sha256,
                )

        universe, policy, manifest, evidence = _valid_artifacts(raw_count=1)
        accumulator = begin(
            manifest=manifest,
            current_environment=manifest.environment,
            universe=universe,
            policy=policy,
            evidence=evidence,
            authorization=_authorization(
                manifest,
                evidence,
                operation="begin",
                sequence=0,
            ),
        )
        parent = raw_parent(
            session_id=manifest.session_id,
            ingest_seq=2,
        )
        observations = bind_expert_observation_drafts(
            manifest,
            parent,
            manifest.normalizers.fallback,
            tuple(
                ExpertSynchronizationDraftV1(synchronization_input())
                for _ in range(64)
            ),
        )
        group, payloads, expected_cursor, _ = _independent_group(
            manifest,
            accumulator.cursor,
            parent,
            prior_state_override=accumulator.state,
            observations_override=observations,
        )
        replay_module = _module(REPLAY_MODULE)
        with mock.patch.object(
            replay_module,
            "normalize_expert_parent",
            return_value=observations,
        ):
            accumulator = parent_step(
                accumulator,
                authorization=_authorization(
                    manifest,
                    evidence,
                    operation="parent_group",
                    sequence=1,
                    expected_parent_ingest_seq=2,
                ),
                parent=parent,
                stored_group=group,
                stored_payloads=payloads,
            )
        self.assertIsNone(accumulator.mismatch)
        self.assertEqual(len(group.records), 64)
        self.assertEqual(len(group.trace_steps), 64)
        self.assertEqual(accumulator.cursor, expected_cursor)
        self.assertEqual(accumulator.cursor.record_count, 64)

    def test_finish_30_to_35_full_field_and_precedence_matrix(
        self,
    ) -> None:
        begin = _surface(REPLAY_MODULE, "begin_expert_replay")
        parent_step = _surface(
            REPLAY_MODULE,
            "replay_expert_parent_group",
        )
        finish = _surface(REPLAY_MODULE, "finish_expert_replay")
        universe, policy, manifest, evidence = _valid_artifacts(raw_count=1)
        accumulator = begin(
            manifest=manifest,
            current_environment=manifest.environment,
            universe=universe,
            policy=policy,
            evidence=evidence,
            authorization=_authorization(
                manifest,
                evidence,
                operation="begin",
                sequence=0,
            ),
        )
        parent = raw_parent(
            session_id=manifest.session_id,
            ingest_seq=2,
        )
        group, payloads, _, _ = _independent_group(
            manifest,
            accumulator.cursor,
            parent,
            prior_state_override=accumulator.state,
        )
        accumulator = parent_step(
            accumulator,
            authorization=_authorization(
                manifest,
                evidence,
                operation="parent_group",
                sequence=1,
                expected_parent_ingest_seq=2,
            ),
            parent=parent,
            stored_group=group,
            stored_payloads=payloads,
        )
        authorization = _authorization(
            manifest,
            evidence,
            operation="finish",
            sequence=2,
        )
        terminal = _terminal(accumulator)
        scan = _clean_scan(accumulator.cursor)

        categories = (
            (
                ExpertReplayMismatchV1.TERMINAL_REASON_MISMATCH,
                (
                    ("schema_version", 2),
                    ("evidence_terminal_clean", False),
                    ("evidence_terminal_reason", "operator_halt"),
                    ("clean", False),
                    ("reason", ExpertTerminalReasonV1.EXPERT_HALT),
                    ("research_evaluable", True),
                ),
            ),
            (
                ExpertReplayMismatchV1.TERMINAL_COUNT_MISMATCH,
                tuple(
                    (name, getattr(terminal, name) + 1)
                    for name in (
                        "evidence_terminal_ingest_seq",
                        "evidence_raw_count",
                        "evidence_derived_count",
                        "expert_group_count",
                        "expert_record_count",
                        "last_parent_ingest_seq",
                        "final_expert_seq",
                    )
                ),
            ),
            (
                ExpertReplayMismatchV1.TERMINAL_PROVENANCE_MISMATCH,
                (
                    (
                        "session_id",
                        "22222222-2222-4222-8222-222222222222",
                    ),
                    ("expert_manifest_sha256", SHA_D),
                    ("provider_request_binding_sha256", SHA_C),
                    ("match_binding_universe_sha256", SHA_D),
                    ("retention_binding_sha256", SHA_D),
                    ("evidence_terminal_record_sha256", SHA_D),
                ),
            ),
            (
                ExpertReplayMismatchV1.TERMINAL_STATE_MISMATCH,
                (
                    ("last_parent_record_sha256", SHA_D),
                    ("final_expert_record_sha256", SHA_D),
                    ("final_expert_state_sha256", SHA_D),
                ),
            ),
            (
                ExpertReplayMismatchV1.TERMINAL_TRACE_MISMATCH,
                (("final_expert_trace_sha256", SHA_D),),
            ),
        )
        for expected, mutations in categories:
            for field_name, changed in mutations:
                with self.subTest(expected=expected, field=field_name):
                    candidate = _rehash_terminal(
                        terminal,
                        **{field_name: changed},
                    )
                    values = {
                        item.name: getattr(candidate, item.name)
                        for item in fields(candidate)
                        if item.name != "terminal_sha256"
                    }
                    self.assertEqual(
                        candidate.terminal_sha256,
                        _independent_sha256(
                            b"INCI-EXPERT-SESSION-TERMINAL-V1\0",
                            values,
                        ),
                    )
                    result = finish(
                        accumulator,
                        final_authorization=authorization,
                        companion_terminal=candidate,
                        companion_scan=scan,
                    )
                    self.assertIs(result.mismatch, expected)
                    self.assertFalse(result.evaluation_input_eligible)
                    self.assertFalse(result.research_evaluable)

        bad_terminal_digest = _unchecked_replace(
            terminal,
            terminal_sha256=SHA_D,
        )
        digest_result = finish(
            accumulator,
            final_authorization=authorization,
            companion_terminal=bad_terminal_digest,
            companion_scan=scan,
        )
        self.assertIs(
            digest_result.mismatch,
            ExpertReplayMismatchV1.TERMINAL_TRACE_MISMATCH,
        )
        self.assertFalse(digest_result.evaluation_input_eligible)
        self.assertFalse(digest_result.research_evaluable)

        missing = finish(
            accumulator,
            final_authorization=authorization,
            companion_terminal=None,
            companion_scan=scan,
        )
        self.assertIs(
            missing.mismatch,
            ExpertReplayMismatchV1.TERMINAL_MISSING,
        )
        ordered_mutations = (
            None,
            {"clean": False},
            {"expert_group_count": terminal.expert_group_count + 1},
            {"provider_request_binding_sha256": SHA_C},
            {"final_expert_state_sha256": SHA_D},
            {"final_expert_trace_sha256": SHA_D},
        )
        ordered_expected = tuple(ExpertReplayMismatchV1)[29:35]
        for index, expected in enumerate(ordered_expected):
            first = ordered_mutations[index]
            for later in ordered_mutations[index + 1 :]:
                changes = {} if later is None else dict(later)
                if first is not None:
                    changes.update(first)
                candidate = (
                    None
                    if first is None
                    else _rehash_terminal(terminal, **changes)
                )
                with self.subTest(precedence=expected, later=later):
                    result = finish(
                        accumulator,
                        final_authorization=authorization,
                        companion_terminal=candidate,
                        companion_scan=scan,
                    )
                    self.assertIs(result.mismatch, expected)

        for cardinality in (
            ExpertReplayMismatchV1.PARENT_MISSING,
            ExpertReplayMismatchV1.PARENT_EXTRA,
        ):
            self.assertIsNone(accumulator.mismatch)
            result = finish(
                accumulator,
                final_authorization=authorization,
                companion_terminal=None,
                companion_scan=replace(
                    scan,
                    last_frame_sequence=(
                        1
                        if cardinality
                        is ExpertReplayMismatchV1.PARENT_MISSING
                        else 3
                    ),
                    group_count=(
                        0
                        if cardinality
                        is ExpertReplayMismatchV1.PARENT_MISSING
                        else 2
                    ),
                    record_count=(
                        0
                        if cardinality
                        is ExpertReplayMismatchV1.PARENT_MISSING
                        else 2
                    ),
                ),
            )
            self.assertIs(result.mismatch, cardinality)


class ReplayAuditP2StaticAndReportTests(unittest.TestCase):
    def test_runtime_import_ast_uses_exact_positive_allowlist_and_rejects_alias_dynamic_escape(
        self,
    ) -> None:
        runtime_path = ROOT / "inci_tennis_runtime" / "replay_service.py"
        pure_path = ROOT / "inci_tennis_expert" / "replay.py"
        runtime_tree = ast.parse(runtime_path.read_text(encoding="utf-8"))
        pure_tree = ast.parse(pure_path.read_text(encoding="utf-8"))

        def imported_modules(tree: ast.AST) -> set[str]:
            modules: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    modules.add(
                        "." * node.level + (node.module or "")
                    )
                    self.assertTrue(
                        all(alias.name != "*" for alias in node.names)
                    )
                elif isinstance(node, ast.Import):
                    modules.update(alias.name for alias in node.names)
            return modules

        runtime_allowlist = {
            "__future__",
            "inci_tennis_expert.contracts",
            "inci_tennis_expert.facade",
            "inci_tennis_io.facade",
            "inci_tennis_io.ports",
            "tennis_v1.retention",
            "tennis_v1.sequencer",
        }
        pure_allowlist = {
            "__future__",
            "dataclasses",
            "hashlib",
            ".contracts",
            ".journal_codec",
            ".match_binding",
            ".observation",
            ".reducer",
            ".synchronizer",
            "inci_tennis_expert.contracts",
            "inci_tennis_expert.journal_codec",
            "inci_tennis_expert.match_binding",
            "inci_tennis_expert.observation",
            "inci_tennis_expert.reducer",
            "inci_tennis_expert.synchronizer",
            "tennis_v1.codec",
            "tennis_v1.events",
        }
        runtime_imports = imported_modules(runtime_tree)
        pure_imports = imported_modules(pure_tree)
        self.assertTrue(runtime_imports <= runtime_allowlist)
        self.assertTrue(pure_imports <= pure_allowlist)
        self.assertTrue(
            {
                "inci_tennis_expert.facade",
                "inci_tennis_io.facade",
                "inci_tennis_io.ports",
            }.issubset(runtime_imports)
        )
        normalized_pure = {
            item.removeprefix("inci_tennis_expert").lstrip(".")
            for item in pure_imports
        }
        self.assertTrue(
            {
                "contracts",
                "journal_codec",
                "match_binding",
                "observation",
                "reducer",
                "synchronizer",
            }.issubset(normalized_pure)
        )

        allowed_io_names = set(STORE_REPLAY_SURFACES)
        allowed_pure_names = {
            "begin_expert_replay",
            "finish_expert_replay",
            "replay_expert_parent_group",
        }
        for node in ast.walk(runtime_tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module == "inci_tennis_io.facade":
                self.assertTrue(
                    {alias.name for alias in node.names}
                    <= allowed_io_names
                )
            if node.module == "inci_tennis_expert.facade":
                self.assertTrue(
                    {alias.name for alias in node.names}
                    <= allowed_pure_names
                )

        forbidden_calls = {
            "__import__",
            "compile",
            "eval",
            "exec",
            "getattr",
            "open",
        }
        forbidden_roots = {
            "asyncio",
            "base64",
            "datetime",
            "importlib",
            "json",
            "multiprocessing",
            "os",
            "pathlib",
            "pickle",
            "random",
            "secrets",
            "socket",
            "subprocess",
            "sys",
            "time",
            "urllib",
        }
        forbidden_symbols = {
            "JournalReader",
            "Path",
            "read_expert_manifest",
            "read_next_expert_group",
            "read_expert_terminal_and_summary",
            "issue_expert_read_capability",
            "close_expert_reader",
            "revoke_expert_reader",
        }
        for label, tree in (("runtime", runtime_tree), ("pure", pure_tree)):
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        self.assertNotIn(
                            node.func.id,
                            forbidden_calls,
                            label,
                        )
                    if isinstance(node.func, ast.Attribute):
                        root = node.func.value
                        while isinstance(root, ast.Attribute):
                            root = root.value
                        if isinstance(root, ast.Name):
                            self.assertNotIn(
                                root.id,
                                forbidden_roots,
                                label,
                            )
                if isinstance(node, ast.Name):
                    self.assertNotIn(node.id, forbidden_symbols, label)
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        self.assertNotIn(
                            alias.name.rsplit(".", 1)[-1],
                            forbidden_symbols,
                            label,
                        )
                        if alias.asname is not None:
                            self.assertNotIn(
                                alias.asname,
                                forbidden_symbols,
                                label,
                            )
                if isinstance(node, ast.Constant) and type(node.value) is str:
                    self.assertFalse(
                        node.value.startswith(
                            (
                                "importlib.",
                                "os.",
                                "pathlib.",
                                "socket.",
                            )
                        )
                    )

    def test_no_generated_digest_wal_journal_hex_or_base64_fixture_by_ast_and_content(
        self,
    ) -> None:
        path = Path(__file__)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_calls = {
            "b64decode",
            "decodebytes",
            "fromhex",
            "sleep",
            "unhexlify",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, forbidden_calls)
            if isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, forbidden_calls)
        byte_literals = tuple(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and type(node.value) is bytes
        )
        self.assertTrue(all(len(value) <= 4096 for value in byte_literals))
        binary_magic = tuple(
            text.encode("ascii")
            for text in (
                "INCI" + "WAL",
                "INCI" + "XJ",
            )
        )
        self.assertFalse(
            any(
                value.startswith(binary_magic)
                for value in byte_literals
            )
        )
        self.assertIsNone(
            re.search(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{128,}(?![0-9A-Fa-f])", source)
        )
        self.assertIsNone(
            re.search(
                r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{256,}={0,2}"
                r"(?![A-Za-z0-9+/])",
                source,
            )
        )
        committed_fixture_suffixes = {
            ".b64",
            ".base64",
            ".hex",
            ".journal",
            ".wal",
        }
        self.assertEqual(
            tuple(
                candidate
                for candidate in path.parent.glob("test_expert_replay*")
                if candidate != path
                and candidate.suffix in committed_fixture_suffixes
            ),
            (),
        )

    def test_replay_report_matches_executed_coverage(self) -> None:
        report_path = (
            ROOT
            / ".superpowers"
            / "sdd"
            / "2026-07-29-inci-expert-tennis-strategy"
            / "task-6-wave2-replay-report.md"
        )
        report = report_path.read_text(encoding="utf-8")
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        test_names = tuple(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        )
        closure_names = (
            "test_readerless_issuance_and_prepare_prioritize_items_1_to_10_before_any_companion_read",
            "test_replay_authority_executes_every_legal_and_invalid_state_edge",
            "test_read_issue_ack_denial_and_proof_matrix_at_every_seam",
            "test_begin_parent_finish_held_token_deadline_and_identity_barrier_matrix",
            "test_replay_service_success_empty_one_many_uses_one_root_and_fresh_environment",
            "test_eof_missing_extra_drains_unmatched_side_without_token_or_reduction_and_retains_one_pair",
            "test_service_abort_finally_and_access_denial_call_trace_are_exact",
            "test_replay_bootstrap_diagnostic_proof_exact_fields_and_mutation_matrix",
            "test_replay_crash_and_nonresumability_matrix_repeated_three_times",
            "test_terminal_alignment_retention_and_evaluation_eligibility_matrix",
            "test_all_replay_and_store_public_signatures_annotations_kinds_and_exports_are_exact",
            "test_direct_exact_type_matrix_covers_subclasses_bool_and_nested_members",
            "test_begin_mismatches_1_to_12_use_self_consistent_objects_and_required_collisions",
            "test_item_12_checks_every_manifest_expressible_universe_policy_and_provider_relation",
            "test_parent_mismatches_13_16_to_29_keep_every_earlier_layer_valid",
            "test_pure_authorization_operation_sequence_anchor_parent_reuse_skip_and_finish_matrix",
            "test_pure_success_empty_one_many_and_multi_output_chains_are_exact",
            "test_finish_30_to_35_full_field_and_precedence_matrix",
            "test_runtime_import_ast_uses_exact_positive_allowlist_and_rejects_alias_dynamic_escape",
            "test_no_generated_digest_wal_journal_hex_or_base64_fixture_by_ast_and_content",
            "test_replay_report_matches_executed_coverage",
            "test_replay_retains_one_shared_pair_with_independent_seals_and_rejects_alias_mutation",
            "test_alias_seal_failure_preserves_deadline_and_identity_precedence",
            "test_physical_root_and_account_lock_loss_are_typed_item_6",
            "test_fatal_root_direct_purge_failure_never_claims_typed_denial",
            "test_prepare_generation_loss_during_final_authorizer_reads_no_evidence",
        )
        self.assertEqual(len(test_names), 106)
        self.assertTrue(set(closure_names).issubset(test_names))
        real_store = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ReplayAuditP0RealStoreTests"
        )
        self.assertEqual(
            len(
                tuple(
                    node
                    for node in real_store.body
                    if isinstance(
                        node,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    )
                    and node.name.startswith("test_")
                )
            ),
            59,
        )
        self.assertIn("Status: `TASK_6_IMPLEMENTATION_FINAL`", report)
        self.assertIn("106 active named `unittest` methods", report)
        self.assertIn("double abort rejects", report)
        self.assertIn("59 real temporary-store regressions", report)
        self.assertIn(
            "six durability boundaries repeated three times",
            report,
        )
        test_sha256 = sha256(Path(__file__).read_bytes()).hexdigest()
        self.assertIn(test_sha256, report)
        for name in closure_names:
            self.assertIn(f"`{name}`", report)
        self.assertIn(
            "24 audit-closure families (26 named primary regressions)",
            report,
        )
        for stale_claim in (
            "production implementation remains absent",
            "genuine loader RED",
            "Replay GREEN remains unauthorized",
            "READY_FOR_FOURTH_REAUDIT",
        ):
            self.assertNotIn(stale_claim, report)
        for unsupported_claim in (
            "all authority tests pass",
            "all crash tests pass",
            "full replay suite passes",
        ):
            self.assertNotIn(unsupported_claim, report.lower())


if __name__ == "__main__":
    unittest.main()
