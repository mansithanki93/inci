from __future__ import annotations

import ast
from contextlib import ExitStack, contextmanager
import copy
from dataclasses import fields, replace
from enum import Enum
import gc
from hashlib import sha256
import inspect
import os
from pathlib import Path
import pickle
import signal
import sys
import threading
import types
from typing import get_args, get_type_hints
import unittest
from unittest import mock
import weakref

import tennis_v1
import inci_tennis_runtime.expert_controller as controller_module
from inci_tennis_expert.contracts import (
    DurableExpertAppendReceiptV1,
    DurableExpertEmergencyReceiptV1,
    DurableExpertTerminalReceiptV1,
    ExpertCollectedEnvironmentV1,
    ExpertCurrentEnvironmentV1,
    ExpertEventKindV1,
    ExpertIgnoreReasonV1,
    ExpertJournalCursorV1,
    ExpertJournalGroupV1,
    ExpertJournalRecordV1,
    ExpertObservationIgnoredPayloadV1,
    ExpertPayloadDescriptorV1,
    ExpertProviderDomainBindingV1,
    ExpertRejectReasonV1,
    ExpertRetentionBindingV1,
    ExpertSessionManifestV1,
    ExpertSessionTerminalV1,
    ExpertStateV1,
    ExpertSynchronizationDraftV1,
    ExpertTerminalReasonV1,
    ExpertTraceStepV1,
    canonical_expert_bytes,
    compute_expert_provider_domain_binding_sha256,
    compute_expert_provider_source_lineage_sha256,
    compute_expert_retention_binding_sha256,
    compute_expert_session_manifest_sha256,
    compute_expert_session_terminal_sha256,
    expert_contract_sha256,
    expert_state_sha256,
    expert_trace_seed_sha256,
)
from inci_tennis_expert.journal_codec import (
    EXPERT_EMERGENCY_RESERVE_BYTES,
    EXPERT_FILE_HEADER_BYTES,
    decode_expert_event_payload,
    encode_expert_group_frame,
    encode_expert_manifest_frame,
    encode_expert_terminal_frame,
    validate_expert_group_against_cursor,
)
from inci_tennis_expert.observation import (
    bind_expert_observation_drafts,
    prove_expert_capacity,
)
from inci_tennis_expert.state import initial_expert_state
from inci_tennis_expert.synchronizer import (
    synchronization_session_from_artifacts,
)
from inci_tennis_io import facade
import inci_tennis_io.expert_journal_store as store_module
from inci_tennis_io.ports import (
    ExpertEmergencyAppendPermitV1,
    ExpertEnvironmentCollectionAuthorityV1,
    ExpertJournalAppendPermitV1,
    ExpertJournalRootAuthorityV1,
    ExpertJournalTerminalPermitV1,
    ExpertJournalWriteCapabilityV1,
    ExpertLiveAuthorizationDenied,
    ExpertPrewriteCapacityError,
)
from inci_tennis_runtime.expert_controller import (
    ExpertControllerV1,
    create_expert_controller,
)
from tennis_v1.codec import canonical_record_sha256
from tennis_v1 import adapter_contract
from tennis_v1.entitlements import ProviderGate
from tennis_v1.events import PersistedEvent, RecordKind
from tennis_v1.fingerprints import code_sha256
from tennis_v1.ingress import (
    BoundedIngress,
    DurableEvidenceTerminalV1,
    DurableIngressParentV1,
    IngressClosed,
    IngressItem,
)
import tennis_v1.ingress as ingress_module
from tennis_v1.retention import RetentionCoordinator
import tennis_v1.retention as retention_module
from tennis_v1.sequencer import (
    EventRuntime,
    ProviderPersistenceAuthorizer,
    WrongOwnerThread,
    bind_provider_persistence_authorizer,
)
from tennis_v1.session import session_manifest_sha256
from tennis_v1.state import initial_state
from tennis_v1.wal import JournalReader, JournalWriter
import tennis_v1.wal as wal_module
from tests.tennis_v1.test_expert_contracts import (
    binding_metadata,
    binding_universe,
    match_binding,
    synchronization_input,
    sync_policy,
)
from tests.tennis_v1.test_sequencer import captured, concrete_environment
from tests.tennis_v1 import test_events as phase1_event_tests


_REQUIRED_REPLAY_INVENTORY = (
    "inci_tennis_expert/replay.py",
    "inci_tennis_expert/facade.py",
    "inci_tennis_runtime/replay_service.py",
)


def _missing_replay_inventory() -> tuple[str, ...]:
    source_root = Path(controller_module.__file__).resolve().parent.parent
    return tuple(
        logical
        for logical in _REQUIRED_REPLAY_INVENTORY
        if not (source_root / logical).is_file()
    )


def _live_phase1_code_sha256() -> str:
    package_file = tennis_v1.__file__
    if type(package_file) is not str:
        raise AssertionError("fixture requires a filesystem tennis_v1 package")
    return code_sha256(Path(package_file).resolve().parent)


@contextmanager
def _concrete_environment_with_live_phase1_fingerprint():
    """Adapt the shared Phase-1 fixture to its installed source bytes."""
    environment = concrete_environment()
    original_build = phase1_event_tests.SessionContractTests.build
    live_code_sha256 = _live_phase1_code_sha256()

    def build(instance, **changes):
        changes.setdefault("code_sha256", live_code_sha256)
        return original_build(instance, **changes)

    with mock.patch.object(
        phase1_event_tests.SessionContractTests,
        "build",
        build,
    ):
        values = environment.__enter__()
    try:
        yield values
    finally:
        environment.__exit__(None, None, None)


def _controller_private_exact(
    controller: ExpertControllerV1,
    expected_type: type[object],
) -> tuple[str, object]:
    """Return the single controller-owned exact-type slot for test probes."""
    matches: list[tuple[str, object]] = []
    for name in dir(controller):
        try:
            value = object.__getattribute__(controller, name)
        except BaseException:
            continue
        if type(value) is expected_type:
            matches.append((name, value))
    if len(matches) != 1:
        raise AssertionError(
            f"expected one private {expected_type.__name__}, got {matches!r}"
        )
    return matches[0]


def _replace_controller_private_exact(
    controller: ExpertControllerV1,
    value: object,
) -> None:
    """Mutate the actual private publication slot for a stale-race oracle."""
    name, _ = _controller_private_exact(controller, type(value))
    object.__setattr__(controller, name, value)


def _controller_private_publication(
    controller: ExpertControllerV1,
) -> tuple[ExpertStateV1, ExpertJournalCursorV1]:
    """Observe controller assignments without invoking the owner-only API."""
    state = _controller_private_exact(controller, ExpertStateV1)[1]
    cursor = _controller_private_exact(
        controller,
        ExpertJournalCursorV1,
    )[1]
    return state, cursor  # type: ignore[return-value]


def _controller_private_writer(
    controller: ExpertControllerV1,
) -> ExpertJournalWriteCapabilityV1:
    return _controller_private_exact(
        controller,
        ExpertJournalWriteCapabilityV1,
    )[1]  # type: ignore[return-value]


def _manifest_values(value: ExpertSessionManifestV1) -> dict[str, object]:
    return {
        item.name: getattr(value, item.name)
        for item in fields(ExpertSessionManifestV1)
        if item.name != "manifest_sha256"
    }


def _forged_replace(value: object, **changes: object):
    forged = object.__new__(type(value))
    for item in fields(type(value)):
        object.__setattr__(
            forged,
            item.name,
            changes.get(item.name, getattr(value, item.name)),
        )
    return forged


def _rebuild_manifest(
    value: ExpertSessionManifestV1,
    **changes: object,
) -> ExpertSessionManifestV1:
    values = _manifest_values(value)
    values.update(changes)
    values["manifest_sha256"] = compute_expert_session_manifest_sha256(
        **values
    )
    return ExpertSessionManifestV1(**values)  # type: ignore[arg-type]


def _expert_manifest_for(
    *,
    phase1_manifest,
    session_start: PersistedEvent,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    collected: ExpertCollectedEnvironmentV1,
):
    evidence_manifest_sha256 = session_manifest_sha256(phase1_manifest)
    provider_request_binding_sha256 = (
        persistence_authorizer.bound_decision.provider_request_binding_sha256
    )
    if type(provider_request_binding_sha256) is not str:
        raise AssertionError("fixture requires a provider-request binding")
    lineage_sha256 = compute_expert_provider_source_lineage_sha256(
        phase1_manifest.provider_id,
        phase1_manifest.product_tier,
        phase1_manifest.source_lineage_id,
        phase1_manifest.provider_manifest_canonical_sha256,
    )
    binding = match_binding(
        provider_source_id=phase1_manifest.provider_id,
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

    provider_domain_values: dict[str, object] = {
        "schema_version": 1,
        "phase1_session_manifest_sha256": evidence_manifest_sha256,
        "match_binding_universe_sha256": universe.universe_sha256,
        "provider_id": phase1_manifest.provider_id,
        "product_tier": phase1_manifest.product_tier,
        "source_lineage_id": phase1_manifest.source_lineage_id,
        "provider_manifest_canonical_sha256": (
            phase1_manifest.provider_manifest_canonical_sha256
        ),
        "provider_source_lineage_sha256": lineage_sha256,
        "revision_domain_id": binding.revision_domain_id,
    }
    provider_domain_values["provider_domain_binding_sha256"] = (
        compute_expert_provider_domain_binding_sha256(
            **provider_domain_values
        )
    )
    provider_domain = ExpertProviderDomainBindingV1(
        **provider_domain_values  # type: ignore[arg-type]
    )

    retention_values: dict[str, object] = {
        "schema_version": 1,
        "session_id": phase1_manifest.session_id,
        "evidence_session_manifest_sha256": evidence_manifest_sha256,
        "provider_request_binding_sha256": provider_request_binding_sha256,
        "permission_artifact_sha256": (
            phase1_manifest.permission_artifact_sha256
        ),
        "qualification_artifact_sha256": (
            phase1_manifest.qualification_artifact_sha256
        ),
        "qualification_trace_sha256": (
            phase1_manifest.qualification_trace_sha256
        ),
        "retention_delete_by_ns": (
            phase1_manifest.required_retention_until_ns
        ),
        "access_expires_at_ns": phase1_manifest.access_expires_at_ns,
        "analysis_expires_at_ns": phase1_manifest.analysis_expires_at_ns,
    }
    retention_values["retention_binding_sha256"] = (
        compute_expert_retention_binding_sha256(**retention_values)
    )
    retention = ExpertRetentionBindingV1(
        **retention_values  # type: ignore[arg-type]
    )
    capacity = prove_expert_capacity(universe, policy)

    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": phase1_manifest.session_id,
        "evidence_session_manifest_sha256": evidence_manifest_sha256,
        "evidence_session_start_record_sha256": canonical_record_sha256(
            session_start
        ),
        "provider_id": phase1_manifest.provider_id,
        "product_tier": phase1_manifest.product_tier,
        "source_lineage_id": phase1_manifest.source_lineage_id,
        "provider_manifest_file_sha256": (
            phase1_manifest.provider_manifest_file_sha256
        ),
        "provider_manifest_canonical_sha256": (
            phase1_manifest.provider_manifest_canonical_sha256
        ),
        "entitlement_id_sha256": phase1_manifest.entitlement_id_sha256,
        "provider_request_binding_sha256": provider_request_binding_sha256,
        "permission_artifact_sha256": (
            phase1_manifest.permission_artifact_sha256
        ),
        "qualification_artifact_sha256": (
            phase1_manifest.qualification_artifact_sha256
        ),
        "qualification_trace_sha256": (
            phase1_manifest.qualification_trace_sha256
        ),
        "provider_domain": provider_domain,
        "environment": collected.current,
        "retention": retention,
        "match_binding_universe_sha256": universe.universe_sha256,
        "binding_raw_artifact_id": universe.raw_artifact_id,
        "binding_raw_artifact_sha256": universe.raw_artifact_sha256,
        "binding_review_artifact_id": (
            universe.review.review_artifact_id
        ),
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
    manifest = ExpertSessionManifestV1(
        **values  # type: ignore[arg-type]
    )
    return universe, policy, manifest


def _expected_genesis(
    manifest: ExpertSessionManifestV1,
    universe,
    policy,
) -> tuple[object, ExpertJournalCursorV1]:
    state = initial_expert_state(manifest, universe, policy)
    state_sha256 = expert_state_sha256(state)
    cursor = ExpertJournalCursorV1(
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
        expert_state_sha256=state_sha256,
        expert_trace_sha256=expert_trace_seed_sha256(
            manifest.session_id,
            manifest.manifest_sha256,
            state_sha256,
        ),
    )
    return state, cursor


def _terminal_for(
    manifest: ExpertSessionManifestV1,
    cursor: ExpertJournalCursorV1,
    evidence_terminal: PersistedEvent,
    *,
    clean: bool,
    evidence_reason: str,
) -> ExpertSessionTerminalV1:
    values: dict[str, object] = {
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
        "evidence_terminal_ingest_seq": evidence_terminal.ingest_seq,
        "evidence_terminal_record_sha256": canonical_record_sha256(
            evidence_terminal
        ),
        "evidence_terminal_clean": clean,
        "evidence_terminal_reason": evidence_reason,
        "evidence_raw_count": cursor.group_count,
        "evidence_derived_count": cursor.group_count,
        "expert_group_count": cursor.group_count,
        "expert_record_count": cursor.record_count,
        "last_parent_ingest_seq": cursor.last_parent_ingest_seq,
        "last_parent_record_sha256": cursor.last_parent_record_sha256,
        "final_expert_seq": cursor.expert_seq,
        "final_expert_record_sha256": cursor.expert_record_sha256,
        "final_expert_state_sha256": cursor.expert_state_sha256,
        "final_expert_trace_sha256": cursor.expert_trace_sha256,
        "clean": clean,
        "reason": (
            (
                ExpertTerminalReasonV1.SESSION_END
                if evidence_reason == "session_end"
                else ExpertTerminalReasonV1.OPERATOR_STOP
            )
            if clean
            else ExpertTerminalReasonV1.EXPERT_HALT
        ),
        "research_evaluable": False,
    }
    values["terminal_sha256"] = compute_expert_session_terminal_sha256(
        **values
    )
    return ExpertSessionTerminalV1(**values)  # type: ignore[arg-type]


def _append_receipt_for(
    group: ExpertJournalGroupV1,
    *,
    durable_end_offset: int,
) -> DurableExpertAppendReceiptV1:
    record = group.records[-1]
    return DurableExpertAppendReceiptV1(
        session_id=group.session_id,
        group_sequence=group.group_sequence,
        group_sha256=group.group_sha256,
        last_parent_record_sha256=group.parent.record_sha256,
        last_expert_seq=record.expert_seq,
        final_expert_record_sha256=group.final_expert_record_sha256,
        post_expert_state_sha256=group.post_expert_state_sha256,
        post_expert_trace_sha256=group.post_trace_sha256,
        durable_end_offset=durable_end_offset,
    )


class _RuledStoreProbe:
    """Controller-facing double for the frozen I/O facade only."""

    def __init__(
        self,
        collected: ExpertCollectedEnvironmentV1,
    ) -> None:
        self.collected = collected
        self.writer = object.__new__(ExpertJournalWriteCapabilityV1)
        self.environment_authority = object.__new__(
            ExpertEnvironmentCollectionAuthorityV1
        )
        self.append_permit = object.__new__(ExpertJournalAppendPermitV1)
        self.terminal_permit = object.__new__(
            ExpertJournalTerminalPermitV1
        )
        self.emergency_permit = object.__new__(
            ExpertEmergencyAppendPermitV1
        )
        self.calls: list[str] = []
        self.manifest: ExpertSessionManifestV1 | None = None
        self.persistence_authorizer: (
            ProviderPersistenceAuthorizer | None
        ) = None
        self.coordinator: RetentionCoordinator | None = None
        self.initial_cursor: ExpertJournalCursorV1 | None = None
        self.current_cursor: ExpertJournalCursorV1 | None = None
        self.emergency_cursor: ExpertJournalCursorV1 | None = None
        self.durable_end_offset: int | None = None
        self.group: ExpertJournalGroupV1 | None = None
        self.payloads: tuple[bytes, ...] | None = None
        self.terminal: ExpertSessionTerminalV1 | None = None
        self.evidence_terminal: PersistedEvent | None = None
        self.receipt_pending = False
        self.pending_receipt: DurableExpertAppendReceiptV1 | None = None
        self.append_permit_attempts = 0
        self.cas_count = 0
        self.ordinary_cas_count = 0
        self.emergency_cas_count = 0
        self.append_count = 0
        self.ack_count = 0
        self.terminal_permit_count = 0
        self.terminal_append_count = 0
        self.emergency_permit_count = 0
        self.emergency_append_count = 0
        self.abort_count = 0
        self.tail_count = 0
        self.build_terminal_count = 0
        self.append_pause: object | None = None
        self.prewrite_capacity_pause: object | None = None
        self.ack_pause: object | None = None
        self.terminal_pause: object | None = None
        self.emergency_pause: object | None = None
        self.receipt_mutator = None
        self.terminal_receipt_mutator = None
        self.emergency_receipt_mutator = None
        self.append_failure: BaseException | None = None
        self.ack_failure: BaseException | None = None
        self.terminal_failure: BaseException | None = None
        self.emergency_failure: BaseException | None = None
        self.prewrite_capacity = False
        self.prewrite_capacity_error: ExpertPrewriteCapacityError | None = None
        self.tail_result: PersistedEvent | None = None
        self.tail_failure: BaseException | None = None
        self.last_append_receipt: DurableExpertAppendReceiptV1 | None = None
        self.last_terminal_receipt: (
            DurableExpertTerminalReceiptV1 | None
        ) = None
        self.last_emergency_receipt: (
            DurableExpertEmergencyReceiptV1 | None
        ) = None
        self.built_terminal_pair: (
            tuple[PersistedEvent, ExpertSessionTerminalV1] | None
        ) = None

    def issue_environment(self, *args, **kwargs):
        self.calls.append("issue_environment")
        return self.environment_authority

    def collect_environment(self, authority):
        self.calls.append("collect_environment")
        if authority is not self.environment_authority:
            raise ValueError("wrong_environment_authority")
        return self.collected

    def create_journal(
        self,
        authority,
        manifest,
        initial_cursor,
        *,
        persistence_authorizer,
        coordinator,
    ):
        self.calls.append("create_journal")
        self.manifest = manifest
        self.persistence_authorizer = persistence_authorizer
        self.coordinator = coordinator
        self.initial_cursor = initial_cursor
        self.current_cursor = initial_cursor
        self.durable_end_offset = (
            EXPERT_FILE_HEADER_BYTES
            + len(encode_expert_manifest_frame(manifest))
        )
        return self.writer

    def _require_live_gate(self) -> None:
        authorizer = self.persistence_authorizer
        coordinator = self.coordinator
        if (
            type(authorizer) is not ProviderPersistenceAuthorizer
            or type(coordinator) is not RetentionCoordinator
        ):
            raise ValueError("probe_writer_not_bound")
        try:
            coordinator.require_provider_operation()
            decision = authorizer.authorize_analysis()
            if decision is not authorizer.bound_decision:
                raise ValueError
            if authorizer.poll_session() is not False:
                raise ValueError
        except Exception:
            raise ExpertLiveAuthorizationDenied() from None

    @staticmethod
    def _cursor_after_group(
        prior: ExpertJournalCursorV1,
        group: ExpertJournalGroupV1,
    ) -> ExpertJournalCursorV1:
        return ExpertJournalCursorV1(
            schema_version=1,
            session_id=group.session_id,
            group_count=group.group_sequence,
            record_count=prior.record_count + len(group.records),
            last_parent_ingest_seq=group.parent.ingest_seq,
            last_parent_record_sha256=group.parent.record_sha256,
            expert_seq=group.records[-1].expert_seq,
            expert_record_sha256=group.final_expert_record_sha256,
            expert_state_sha256=group.post_expert_state_sha256,
            expert_trace_sha256=group.post_trace_sha256,
        )

    def issue_append(
        self,
        writer,
        expected_state_sha256,
        expected_cursor,
        group,
        payloads,
    ):
        self.calls.append("issue_append")
        self.append_permit_attempts += 1
        if writer is not self.writer:
            raise ValueError("wrong_writer")
        if self.receipt_pending:
            raise ValueError("receipt_pending")
        self._require_live_gate()
        if self.prewrite_capacity or self.prewrite_capacity_error is not None:
            if callable(self.prewrite_capacity_pause):
                self.prewrite_capacity_pause()
            if self.prewrite_capacity_error is not None:
                raise self.prewrite_capacity_error
            raise ExpertPrewriteCapacityError()
        self.cas_count += 1
        self.ordinary_cas_count += 1
        self.group = group
        self.payloads = payloads
        return self.append_permit

    def append_group(self, permit):
        self.calls.append("append_group")
        if (
            permit is not self.append_permit
            or self.group is None
            or self.payloads is None
            or self.current_cursor is None
            or self.durable_end_offset is None
        ):
            raise ValueError("wrong_append_permit")
        self._require_live_gate()
        self.append_count += 1
        self.receipt_pending = True
        if callable(self.append_pause):
            self.append_pause()
        if self.append_failure is not None:
            raise self.append_failure
        self.durable_end_offset += len(
            encode_expert_group_frame(
                self.group,
                self.payloads,
                prior_cursor=self.current_cursor,
            )
        )
        receipt = _append_receipt_for(
            self.group,
            durable_end_offset=self.durable_end_offset,
        )
        self.pending_receipt = receipt
        if callable(self.receipt_mutator):
            receipt = self.receipt_mutator(receipt)
        self.last_append_receipt = receipt
        return receipt

    def acknowledge(
        self,
        writer,
        *,
        receipt,
        candidate_state_sha256,
        candidate_cursor,
    ) -> None:
        self.calls.append("acknowledge")
        self.ack_count += 1
        if writer is not self.writer:
            raise ValueError("wrong_writer")
        if not self.receipt_pending:
            raise ValueError("receipt_not_pending")
        self._require_live_gate()
        if receipt != self.pending_receipt:
            raise ValueError("receipt_not_exact")
        if callable(self.ack_pause):
            self.ack_pause()
        if self.ack_failure is not None:
            raise self.ack_failure
        self.current_cursor = candidate_cursor
        self.receipt_pending = False
        self.pending_receipt = None

    def issue_terminal(self, writer, terminal):
        self.calls.append("issue_terminal")
        self.terminal_permit_count += 1
        if self.receipt_pending:
            raise ValueError("receipt_pending")
        self.terminal = terminal
        return self.terminal_permit

    def append_terminal(self, permit):
        self.calls.append("append_terminal")
        self.terminal_append_count += 1
        if (
            permit is not self.terminal_permit
            or self.terminal is None
            or self.current_cursor is None
            or self.durable_end_offset is None
        ):
            raise ValueError("wrong_terminal_permit")
        if callable(self.terminal_pause):
            self.terminal_pause()
        if self.terminal_failure is not None:
            raise self.terminal_failure
        self.durable_end_offset += len(
            encode_expert_terminal_frame(
                self.terminal,
                final_cursor=self.current_cursor,
            )
        )
        receipt = DurableExpertTerminalReceiptV1(
            session_id=self.terminal.session_id,
            terminal_sha256=self.terminal.terminal_sha256,
            terminal_frame_sequence=self.terminal.expert_group_count + 1,
            durable_end_offset=self.durable_end_offset,
            reserve_already_consumed=True,
        )
        if callable(self.terminal_receipt_mutator):
            receipt = self.terminal_receipt_mutator(receipt)
        self.last_terminal_receipt = receipt
        return receipt

    def issue_emergency(
        self,
        writer,
        *,
        expected_state_sha256,
        expected_cursor,
        evidence_terminal,
        group,
        payloads,
        terminal,
    ):
        self.calls.append("issue_emergency")
        self.emergency_permit_count += 1
        if self.receipt_pending:
            raise ValueError("receipt_pending")
        self.cas_count += 1
        self.emergency_cas_count += 1
        self.group = group
        self.payloads = payloads
        self.evidence_terminal = evidence_terminal
        self.terminal = terminal
        if self.current_cursor is None:
            raise ValueError("probe_cursor_not_bound")
        self.emergency_cursor = self._cursor_after_group(
            self.current_cursor,
            group,
        )
        return self.emergency_permit

    def append_emergency(self, permit):
        self.calls.append("append_emergency")
        self.emergency_append_count += 1
        if permit is not self.emergency_permit:
            raise ValueError("wrong_emergency_permit")
        if callable(self.emergency_pause):
            self.emergency_pause()
        if self.emergency_failure is not None:
            raise self.emergency_failure
        if (
            self.group is None
            or self.payloads is None
            or self.terminal is None
            or self.current_cursor is None
            or self.emergency_cursor is None
            or self.durable_end_offset is None
        ):
            raise ValueError("emergency_material_missing")
        self.durable_end_offset += len(
            encode_expert_group_frame(
                self.group,
                self.payloads,
                prior_cursor=self.current_cursor,
            )
        )
        group_receipt = _append_receipt_for(
            self.group,
            durable_end_offset=self.durable_end_offset,
        )
        self.durable_end_offset += len(
            encode_expert_terminal_frame(
                self.terminal,
                final_cursor=self.emergency_cursor,
            )
        )
        terminal_receipt = DurableExpertTerminalReceiptV1(
            session_id=self.terminal.session_id,
            terminal_sha256=self.terminal.terminal_sha256,
            terminal_frame_sequence=self.terminal.expert_group_count + 1,
            durable_end_offset=self.durable_end_offset,
            reserve_already_consumed=True,
        )
        receipt = DurableExpertEmergencyReceiptV1(
            session_id=self.terminal.session_id,
            group_receipt=group_receipt,
            terminal_receipt=terminal_receipt,
            reserve_already_consumed=True,
        )
        if callable(self.emergency_receipt_mutator):
            receipt = self.emergency_receipt_mutator(receipt)
        self.last_emergency_receipt = receipt
        return receipt

    def prove_tail(self, writer, *, published_cursor):
        self.calls.append("prove_tail")
        self.tail_count += 1
        if self.tail_failure is not None:
            raise self.tail_failure
        return self.tail_result

    def build_terminal(self, writer, *, final_state, final_cursor):
        self.calls.append("build_terminal")
        self.build_terminal_count += 1
        if self.built_terminal_pair is None:
            raise ValueError("terminal_pair_not_configured")
        return self.built_terminal_pair

    def abort(self, writer) -> None:
        self.calls.append("abort")
        self.abort_count += 1

    def patches(self) -> dict[str, object]:
        return {
            "issue_expert_environment_collection_authority": (
                self.issue_environment
            ),
            "collect_expert_current_environment": self.collect_environment,
            "create_expert_journal": self.create_journal,
            "issue_expert_append_permit": self.issue_append,
            "append_expert_group": self.append_group,
            "acknowledge_expert_publication": self.acknowledge,
            "issue_expert_terminal_permit": self.issue_terminal,
            "append_expert_terminal": self.append_terminal,
            "issue_expert_emergency_append_permit": self.issue_emergency,
            "append_expert_emergency_group_and_terminal": (
                self.append_emergency
            ),
            "prove_expert_live_evidence_tail": self.prove_tail,
            "build_aligned_expert_terminal": self.build_terminal,
            "abort_expert_writer": self.abort,
        }


class ExpertControllerSurfaceTests(unittest.TestCase):
    def test_exact_private_controller_and_factory_signatures(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(ExpertControllerV1).parameters),
            ("_", "__"),
        )
        process = inspect.signature(ExpertControllerV1.process_one)
        self.assertEqual(tuple(process.parameters), ("self", "timeout_seconds"))
        self.assertIs(
            process.parameters["timeout_seconds"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertEqual(
            tuple(inspect.signature(ExpertControllerV1.snapshot).parameters),
            ("self",),
        )
        self.assertEqual(
            tuple(inspect.signature(ExpertControllerV1.close).parameters),
            ("self",),
        )
        factory = inspect.signature(create_expert_controller)
        self.assertEqual(
            tuple(factory.parameters),
            (
                "authority",
                "manifest",
                "universe",
                "policy",
                "ingress",
                "runtime",
                "persistence_authorizer",
                "coordinator",
            ),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in factory.parameters.values()
            )
        )

    def test_controller_is_opaque_owner_object(self) -> None:
        with self.assertRaises(TypeError):
            ExpertControllerV1()
        with self.assertRaises(TypeError):
            type("HostileController", (ExpertControllerV1,), {})
        forged = object.__new__(ExpertControllerV1)
        self.assertIn("redacted", repr(forged))
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(TypeError):
                    operation(forged)
        public_names = {
            name for name in dir(forged) if not name.startswith("_")
        }
        self.assertEqual(
            public_names,
            {
                "abort_pending_durable_companion_emergency_v1",
                "close",
                "complete_pending_emergency",
                "process_durable_parent",
                "process_evidence_terminal",
                "process_one",
                "snapshot",
            },
        )

    def test_public_surface_never_accepts_or_returns_live_material(self) -> None:
        forbidden = {
            "PersistedEvent",
            "ExpertJournalWriteCapabilityV1",
            "ExpertJournalAppendPermitV1",
            "DurableExpertAppendReceiptV1",
            "callback",
            "path",
            "candidate",
            "writer",
            "receipt",
            "permit",
        }
        for callable_value in (
            ExpertControllerV1.process_durable_parent,
            ExpertControllerV1.process_evidence_terminal,
            ExpertControllerV1.complete_pending_emergency,
            ExpertControllerV1.abort_pending_durable_companion_emergency_v1,
            ExpertControllerV1.process_one,
            ExpertControllerV1.snapshot,
            ExpertControllerV1.close,
            create_expert_controller,
        ):
            with self.subTest(callable=callable_value.__name__):
                signature = str(inspect.signature(callable_value))
                for name in forbidden:
                    self.assertNotIn(name, signature)

    def test_controller_import_boundary_is_exact_and_parser_free(self) -> None:
        source_path = Path(controller_module.__file__)
        tree = ast.parse(source_path.read_text("utf-8"))
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_modules = {
            (node.module or "")
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        forbidden_roots = {
            "json",
            "pathlib",
            "time",
            "datetime",
            "socket",
            "requests",
            "urllib",
            "subprocess",
            "asyncio",
            "inci_tennis_adapters",
            "inci_tennis_strategy",
            "inci_tennis_orders",
        }
        self.assertTrue(imported_roots.isdisjoint(forbidden_roots))
        self.assertTrue(
            all(
                not module.endswith("expert_journal_store")
                and "JournalReader" not in module
                for module in imported_modules
            )
        )
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        self.assertTrue(
            called_names.isdisjoint(
                {"open", "eval", "exec", "__import__"}
            )
        )
        package_root = source_path.with_name("__init__.py")
        self.assertEqual(package_root.read_bytes(), b"\n")

    def test_controller_ast_rejects_dynamic_import_process_clock_and_sleep_escape(
        self,
    ) -> None:
        tree = ast.parse(
            Path(controller_module.__file__).read_text("utf-8")
        )
        forbidden_modules = {
            "asyncio",
            "builtins",
            "csv",
            "datetime",
            "fcntl",
            "glob",
            "importlib",
            "io",
            "json",
            "mmap",
            "multiprocessing",
            "pathlib",
            "pickle",
            "shutil",
            "socket",
            "subprocess",
            "tempfile",
            "time",
            "urllib",
        }
        os_aliases = {"os"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    self.assertNotIn(root, forbidden_modules)
                    if root == "os":
                        os_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                self.assertNotIn(root, forbidden_modules)
                if root == "os":
                    self.assertEqual(
                        {(alias.name, alias.asname) for alias in node.names},
                        {("getpid", None)},
                    )
        forbidden_calls = {
            "__import__",
            "compile",
            "eval",
            "exec",
            "getattr",
            "globals",
            "locals",
            "open",
            "sleep",
            "vars",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, forbidden_calls)
            elif isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, forbidden_calls)
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id in os_aliases
                ):
                    self.assertIn(node.func.attr, {"getpid"})

        def attribute_parts(node: ast.AST) -> tuple[str, ...]:
            parts: list[str] = []
            current = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return tuple(reversed(parts))

        private_dependency_accesses: set[str] = set()
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Attribute)
                or not node.attr.startswith("_")
                or node.attr
                in {"__name__", "__new__", "__post_init__"}
            ):
                continue
            if (
                isinstance(node.value, ast.Name)
                and node.value.id in {"controller", "self"}
            ):
                continue
            private_dependency_accesses.add(
                ".".join(attribute_parts(node))
            )
        self.assertEqual(
            private_dependency_accesses,
            {
                "__init__",
                "authority.controller._controller_identity",
                "authority.controller._manifest",
                "authority.controller._publication_lock",
                "authority.publication_lock._is_owned",
                "controller._publication_lock._is_owned",
                "ingress._causal_subject_lock",
                "ingress._causal_subject_lock._is_owned",
                "object.__setattr__",
                "publication_lock._is_owned",
                "store_error.__cause__",
                "store_error.__context__",
                "store_error.__traceback__",
            },
        )

        source = Path(__file__).read_text("utf-8")
        test_tree = ast.parse(source)
        self.assertFalse(
            any(
                isinstance(node, ast.Call)
                and (
                    (
                        isinstance(node.func, ast.Name)
                        and node.func.id == "sleep"
                    )
                    or (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr == "sleep"
                    )
                )
                for node in ast.walk(test_tree)
            )
        )

    def test_surface_annotations_and_runtime_results_do_not_expose_root_or_terminal_material(
        self,
    ) -> None:
        allowed_leaf_types = {
            type(None),
            float,
            ExpertControllerV1,
            ExpertJournalCursorV1,
            ExpertSessionTerminalV1,
            getattr(
                controller_module,
                "DurableCompanionEmergencyPublicationProofV1",
            ),
            getattr(
                controller_module,
                "DurableCompanionPublicationAckV1",
            ),
            getattr(
                controller_module,
                "PendingDurableCompanionEmergencyAbortReceiptV1",
            ),
            getattr(
                controller_module,
                "PendingDurableCompanionEmergencyV1",
            ),
        }
        forbidden_names = {
            "ExpertEnvironmentCollectionAuthorityV1",
            "ExpertJournalAppendPermitV1",
            "ExpertJournalRootAuthorityV1",
            "ExpertJournalTerminalPermitV1",
            "ExpertJournalWriteCapabilityV1",
            "DurableExpertAppendReceiptV1",
            "DurableExpertEmergencyReceiptV1",
            "DurableExpertTerminalReceiptV1",
            "PersistedEvent",
        }

        def annotation_leaves(value: object) -> set[object]:
            arguments = get_args(value)
            if not arguments:
                return {value}
            leaves: set[object] = set()
            for argument in arguments:
                leaves.update(annotation_leaves(argument))
            return leaves

        for callable_value in (
            ExpertControllerV1.process_durable_parent,
            ExpertControllerV1.process_evidence_terminal,
            ExpertControllerV1.complete_pending_emergency,
            ExpertControllerV1.abort_pending_durable_companion_emergency_v1,
            ExpertControllerV1.process_one,
            ExpertControllerV1.snapshot,
            ExpertControllerV1.close,
            create_expert_controller,
        ):
            hints = get_type_hints(callable_value)
            rendered = repr(hints["return"])
            for forbidden in forbidden_names:
                self.assertNotIn(forbidden, rendered)
            if callable_value is not create_expert_controller:
                leaves = annotation_leaves(hints["return"])
                self.assertTrue(
                    leaves.issubset(
                        allowed_leaf_types
                        | {
                            ExpertStateV1,
                        }
                    )
                )
        factory_hints = get_type_hints(create_expert_controller)
        self.assertIs(factory_hints["return"], ExpertControllerV1)

        def assert_ruled_result(value: object) -> None:
            self.assertIs(type(value), tuple)
            self.assertEqual(len(value), 3)  # type: ignore[arg-type]
            state, cursor, terminal = value  # type: ignore[misc]
            self.assertIs(type(state), ExpertStateV1)
            self.assertIs(type(cursor), ExpertJournalCursorV1)
            self.assertTrue(
                terminal is None
                or type(terminal) is ExpertSessionTerminalV1
            )

        case = ExpertControllerIntegrationTests(
            "test_timeout_returns_the_unchanged_published_snapshot"
        )
        case.setUp()
        try:
            probe = _RuledStoreProbe(case.collected)
            returned_terminal: list[PersistedEvent] = []
            original_drain = BoundedIngress.drain_one_parent

            def drain(instance, runtime, *, timeout_seconds):
                value = original_drain(
                    instance,
                    runtime,
                    timeout_seconds=timeout_seconds,
                )
                if type(value) is DurableEvidenceTerminalV1:
                    returned_terminal.append(value.terminal)
                return value

            def build(writer, *, final_state, final_cursor):
                evidence = returned_terminal[-1]
                return (
                    evidence,
                    _terminal_for(
                        case.manifest,
                        final_cursor,
                        evidence,
                        clean=True,
                        evidence_reason="operator_stop",
                    ),
                )

            probe.build_terminal = build  # type: ignore[method-assign]
            with (
                case.mocked_facade(probe),
                mock.patch.object(
                    BoundedIngress,
                    "drain_one_parent",
                    drain,
                ),
            ):
                controller = case.create_controller()
                self.assertIs(type(controller), ExpertControllerV1)
                initial = controller.snapshot()
                producer, _ = case.enqueue_one()
                processed = controller.process_one(timeout_seconds=1.0)
                producer.join(5)
                closed = controller.close()
            for value in (initial, processed, closed):
                assert_ruled_result(value)
            self.assertIsNone(initial[2])
            self.assertIsNone(processed[2])
            self.assertIs(type(closed[2]), ExpertSessionTerminalV1)
        finally:
            case.tearDown()


class ExpertControllerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        missing_replay = _missing_replay_inventory()
        if missing_replay:
            raise ModuleNotFoundError(
                "missing required Task-6 replay inventory: "
                + ", ".join(missing_replay)
            )
        self._environment = (
            _concrete_environment_with_live_phase1_fingerprint()
        )
        (
            self.phase1_fixture,
            self.coordinator,
            self.gate,
            self.phase1_manifest,
        ) = self._environment.__enter__()
        self.assertEqual(
            self.phase1_manifest.code_sha256,
            _live_phase1_code_sha256(),
        )
        self.controller: ExpertControllerV1 | None = None
        self._threads: list[threading.Thread] = []

        self.persistence_authorizer = bind_provider_persistence_authorizer(
            gate=self.gate,
            coordinator=self.coordinator,
            session_manifest=self.phase1_manifest,
        )
        request = (
            self.coordinator.issue_expert_state_root_account_lock_request()
        )
        self.authority = facade.acquire_expert_journal_root(request)
        capability = self.coordinator.arm_before_wal(
            session_manifest=self.phase1_manifest,
            decision=self.persistence_authorizer.bound_decision,
            persistence_authorizer=self.persistence_authorizer,
        )
        self.phase1_writer = JournalWriter.create(
            write_capability=capability,
            session_manifest=self.phase1_manifest,
        )
        self.runtime = EventRuntime(
            writer=self.phase1_writer,
            state=initial_state(self.phase1_manifest.session_id),
            persistence_authorizer=self.persistence_authorizer,
            coordinator=self.coordinator,
        )
        self.ingress = BoundedIngress(
            capacity=4,
            producer_timeout_seconds=2.0,
            receipt_timeout_seconds=5.0,
        )
        proposed_authority = (
            facade.issue_expert_environment_collection_authority(
                self.authority,
                persistence_authorizer=self.persistence_authorizer,
                coordinator=self.coordinator,
            )
        )
        self.collected = facade.collect_expert_current_environment(
            proposed_authority
        )
        (
            self.universe,
            self.policy,
            self.manifest,
        ) = _expert_manifest_for(
            phase1_manifest=self.phase1_manifest,
            session_start=self.phase1_writer.session_start,
            persistence_authorizer=self.persistence_authorizer,
            collected=self.collected,
        )

    def tearDown(self) -> None:
        for thread in self._threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        if self.controller is not None:
            try:
                self.controller.close()
            except BaseException:
                pass
        self._environment.__exit__(None, None, None)

    def create_controller(
        self,
        **changes: object,
    ) -> ExpertControllerV1:
        values: dict[str, object] = {
            "authority": self.authority,
            "manifest": self.manifest,
            "universe": self.universe,
            "policy": self.policy,
            "ingress": self.ingress,
            "runtime": self.runtime,
            "persistence_authorizer": self.persistence_authorizer,
            "coordinator": self.coordinator,
        }
        values.update(changes)
        controller = create_expert_controller(
            **values  # type: ignore[arg-type]
        )
        self.controller = controller
        return controller

    @contextmanager
    def mocked_facade(self, probe: _RuledStoreProbe):
        with ExitStack() as stack:
            for name, replacement in probe.patches().items():
                stack.enter_context(
                    mock.patch.object(
                        controller_module,
                        name,
                        replacement,
                    )
                )
            yield probe

    @contextmanager
    def fresh_case(self):
        case = type(self)(self._testMethodName)
        case.setUp()
        try:
            yield case
        finally:
            case.tearDown()

    def enqueue_one(self, sequence: int = 1):
        entered_queue = threading.Event()
        result: list[object] = []
        original_put = self.ingress._queue.put  # type: ignore[attr-defined]

        def instrumented_put(node, *args, **kwargs):
            output = original_put(node, *args, **kwargs)
            entered_queue.set()
            return output

        self.ingress._queue.put = instrumented_put  # type: ignore[attr-defined]

        def produce() -> None:
            try:
                result.append(
                    self.ingress.enqueue(
                        IngressItem(
                            producer_id="expert-controller-test",
                            producer_sequence=sequence,
                            captured=captured(self.persistence_authorizer),
                        )
                    )
                )
            except BaseException as error:
                result.append(error)

        thread = threading.Thread(target=produce)
        self._threads.append(thread)
        thread.start()
        self.assertTrue(entered_queue.wait(5))
        return thread, result

    def read_companion(self):
        reader = facade.issue_expert_read_capability(
            self.authority,
            self.manifest,
        )
        try:
            manifest = facade.read_expert_manifest(reader)
            groups = []
            while True:
                item = facade.read_next_expert_group(reader)
                if item is None:
                    break
                groups.append(item)
            terminal, summary = facade.read_expert_terminal_and_summary(
                reader
            )
            return manifest, tuple(groups), terminal, summary
        finally:
            facade.close_expert_reader(reader)

    def read_phase1_diagnostic_prefix(
        self,
    ) -> tuple[PersistedEvent, ...]:
        capability = self.coordinator.issue_read_capability(
            persistence_authorizer=self.persistence_authorizer,
        )
        reader = JournalReader.open(read_capability=capability)
        try:
            return tuple(reader.iter_records(diagnostic_prefix=True))
        finally:
            reader.close()

    def rewrite_phase1_records(
        self,
        records: tuple[PersistedEvent, ...],
        *,
        diagnostic_suffix: bytes = b"",
    ) -> None:
        prefix = wal_module.FILE_PREFIX.pack(
            wal_module.FILE_MAGIC,
            wal_module.FILE_VERSION,
            wal_module.FILE_FLAGS,
            wal_module.FILE_PREFIX.size,
        )
        content = (
            prefix
            + b"".join(wal_module._encode_frame(item) for item in records)
            + diagnostic_suffix
        )
        basename = f"{self.phase1_manifest.session_id}.wal"
        sessions_fd = self.coordinator._sessions_fd  # type: ignore[attr-defined]
        descriptor = os.open(
            basename,
            os.O_WRONLY | os.O_TRUNC,
            dir_fd=sessions_fd,
        )
        try:
            offset = 0
            while offset != len(content):
                written = os.write(descriptor, content[offset:])
                if written < 1:
                    raise OSError("phase1_test_rewrite_short")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def test_factory_recollects_environment_and_creates_exact_genesis(
        self,
    ) -> None:
        calls: list[str] = []
        original_issue = (
            controller_module.issue_expert_environment_collection_authority
        )
        original_collect = (
            controller_module.collect_expert_current_environment
        )
        original_create = controller_module.create_expert_journal

        def issue(*args, **kwargs):
            calls.append("issue_environment")
            return original_issue(*args, **kwargs)

        def collect(*args, **kwargs):
            calls.append("collect_environment")
            return original_collect(*args, **kwargs)

        def create(*args, **kwargs):
            calls.append("create_journal")
            return original_create(*args, **kwargs)

        runtime_authorizer = self.runtime._persistence_authorizer
        runtime_coordinator = self.runtime._coordinator
        self.runtime._persistence_authorizer = object()
        self.runtime._coordinator = object()
        try:
            with (
                mock.patch.object(
                    controller_module,
                    "issue_expert_environment_collection_authority",
                    issue,
                ),
                mock.patch.object(
                    controller_module,
                    "collect_expert_current_environment",
                    collect,
                ),
                mock.patch.object(
                    controller_module,
                    "create_expert_journal",
                    create,
                ),
            ):
                controller = self.create_controller()
        finally:
            self.runtime._persistence_authorizer = runtime_authorizer
            self.runtime._coordinator = runtime_coordinator
        self.assertEqual(
            calls,
            ["issue_environment", "collect_environment", "create_journal"],
        )
        expected = _expected_genesis(
            self.manifest,
            self.universe,
            self.policy,
        )
        self.assertEqual(controller.snapshot(), (*expected, None))

    def test_factory_rejects_every_cross_binding_before_drain_or_create(
        self,
    ) -> None:
        mutations = (
            ("universe", binding_universe()),
            (
                "policy",
                sync_policy(
                    universe_sha256=self.universe.universe_sha256,
                    max_score_age_ns=21,
                ),
            ),
            (
                "manifest",
                _rebuild_manifest(
                    self.manifest,
                    evidence_session_start_record_sha256="f" * 64,
                ),
            ),
        )
        for name, value in mutations:
            with self.subTest(name=name):
                calls: list[str] = []
                with (
                    mock.patch.object(
                        BoundedIngress,
                        "drain_one_parent",
                        side_effect=AssertionError("drain must not run"),
                    ),
                    mock.patch.object(
                        controller_module,
                        "create_expert_journal",
                        side_effect=lambda *a, **k: calls.append("create"),
                    ),
                ):
                    with self.assertRaises((TypeError, ValueError)):
                        self.create_controller(**{name: value})
                self.assertEqual(calls, [])

    def test_factory_full_binding_and_recollection_failure_matrix(
        self,
    ) -> None:
        exact_arguments = {
            "authority": self.authority,
            "manifest": self.manifest,
            "universe": self.universe,
            "policy": self.policy,
            "ingress": self.ingress,
            "runtime": self.runtime,
            "persistence_authorizer": self.persistence_authorizer,
            "coordinator": self.coordinator,
        }
        for argument in exact_arguments:
            with self.subTest(wrong_type=argument):
                values = dict(exact_arguments)
                values[argument] = object()
                probe = _RuledStoreProbe(self.collected)
                with self.mocked_facade(probe):
                    with self.assertRaises(TypeError):
                        create_expert_controller(
                            **values  # type: ignore[arg-type]
                        )
                self.assertEqual(probe.calls, [])

        environment_mutations = {
            "current": _forged_replace(
                self.collected,
                current=replace(
                    self.collected.current,
                    runtime_code_sha256="0" * 64,
                ),
            ),
            "normalizers": _forged_replace(
                self.collected,
                normalizers=_forged_replace(
                    self.collected.normalizers,
                    registry_sha256="1" * 64,
                ),
            ),
            "structural_schemas": _forged_replace(
                self.collected,
                structural_schemas=_forged_replace(
                    self.collected.structural_schemas,
                    bundle_sha256="2" * 64,
                ),
            ),
            "event_schemas": _forged_replace(
                self.collected,
                event_schemas=_forged_replace(
                    self.collected.event_schemas,
                    bundle_sha256="3" * 64,
                ),
            ),
        }
        for name, changed_collected in environment_mutations.items():
            with self.subTest(recollection=name):
                probe = _RuledStoreProbe(changed_collected)
                with self.mocked_facade(probe):
                    with self.assertRaises((TypeError, ValueError)):
                        self.create_controller()
                self.assertEqual(
                    probe.calls[:2],
                    ["issue_environment", "collect_environment"],
                )
                self.assertNotIn("create_journal", probe.calls)
                self.assertEqual(probe.abort_count, 0)
                self.assertEqual(probe.append_count, 0)
                self.assertEqual(probe.terminal_append_count, 0)
                self.assertEqual(probe.emergency_append_count, 0)

        equation_mutations = {
            "capacity": _forged_replace(
                self.manifest,
                capacity=_forged_replace(
                    self.manifest.capacity,
                    match_binding_universe_sha256="4" * 64,
                ),
            ),
            "provider_domain": _forged_replace(
                self.manifest,
                provider_domain=_forged_replace(
                    self.manifest.provider_domain,
                    provider_id="other-provider",
                ),
            ),
            "retention": _forged_replace(
                self.manifest,
                retention=_forged_replace(
                    self.manifest.retention,
                    analysis_expires_at_ns=(
                        self.manifest.retention.analysis_expires_at_ns - 1
                    ),
                ),
            ),
            "carried_synchronization": _forged_replace(
                self.manifest,
                initial_synchronization_sha256="5" * 64,
            ),
        }
        for name, changed_manifest in equation_mutations.items():
            with self.subTest(equation=name):
                probe = _RuledStoreProbe(self.collected)
                with self.mocked_facade(probe):
                    with self.assertRaises((TypeError, ValueError)):
                        self.create_controller(manifest=changed_manifest)
                self.assertEqual(
                    probe.calls,
                    ["issue_environment", "collect_environment"],
                )
                self.assertEqual(probe.append_count, 0)
                self.assertEqual(probe.terminal_append_count, 0)
                self.assertEqual(probe.emergency_append_count, 0)

        owner_outcomes: list[BaseException] = []
        probe = _RuledStoreProbe(self.collected)

        def wrong_owner_factory() -> None:
            try:
                with self.mocked_facade(probe):
                    self.create_controller()
            except BaseException as error:
                owner_outcomes.append(error)

        thread = threading.Thread(target=wrong_owner_factory)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(owner_outcomes), 1)
        self.assertIsInstance(owner_outcomes[0], WrongOwnerThread)
        self.assertNotIn("create_journal", probe.calls)

        for writer_state in ("existing_terminal", "closed", "poisoned"):
            with self.subTest(writer_state=writer_state):
                probe = _RuledStoreProbe(self.collected)

                def reject_create(*args, _state=writer_state, **kwargs):
                    probe.calls.append("create_journal")
                    raise ValueError(f"expert_writer_{_state}")

                probe.create_journal = reject_create  # type: ignore[method-assign]
                with self.mocked_facade(probe):
                    with self.assertRaises(ValueError):
                        self.create_controller()
                self.assertEqual(
                    probe.calls,
                    [
                        "issue_environment",
                        "collect_environment",
                        "create_journal",
                    ],
                )
                self.assertNotIn("abort", probe.calls)
                self.assertEqual(probe.append_count, 0)
                self.assertEqual(probe.terminal_append_count, 0)
                self.assertEqual(probe.emergency_append_count, 0)

    def test_factory_environment_authority_exit_and_postcreate_abort_matrix(
        self,
    ) -> None:
        exact_arguments = {
            "authority": self.authority,
            "manifest": self.manifest,
            "universe": self.universe,
            "policy": self.policy,
            "ingress": self.ingress,
            "runtime": self.runtime,
            "persistence_authorizer": self.persistence_authorizer,
            "coordinator": self.coordinator,
        }
        original_issue = (
            controller_module.issue_expert_environment_collection_authority
        )
        original_collect = (
            controller_module.collect_expert_current_environment
        )
        for failure in ("collection", "comparison"):
            with self.subTest(failure=failure):
                issued: list[ExpertEnvironmentCollectionAuthorityV1] = []
                collected_calls = 0

                def issue(*args, **kwargs):
                    authority = original_issue(*args, **kwargs)
                    issued.append(authority)
                    return authority

                def collect(authority):
                    nonlocal collected_calls
                    collected_calls += 1
                    current = original_collect(authority)
                    if failure == "collection":
                        raise ValueError("collection_after_authority_consumed")
                    return _forged_replace(
                        current,
                        current=replace(
                            current.current,
                            runtime_code_sha256="0" * 64,
                        ),
                    )

                with (
                    mock.patch.object(
                        controller_module,
                        "issue_expert_environment_collection_authority",
                        issue,
                    ),
                    mock.patch.object(
                        controller_module,
                        "collect_expert_current_environment",
                        collect,
                    ),
                    mock.patch.object(
                        controller_module,
                        "create_expert_journal",
                        side_effect=AssertionError(
                            "failed collection/comparison cannot create"
                        ),
                    ),
                ):
                    with self.assertRaises(ValueError):
                        create_expert_controller(**exact_arguments)
                self.assertEqual(collected_calls, 1)
                self.assertEqual(len(issued), 1)
                with self.assertRaises(ValueError):
                    facade.collect_expert_current_environment(issued[0])

        issued: list[ExpertEnvironmentCollectionAuthorityV1] = []
        created: list[ExpertJournalWriteCapabilityV1] = []
        aborts: list[ExpertJournalWriteCapabilityV1] = []
        writer_created = False
        original_create = controller_module.create_expert_journal
        original_abort = controller_module.abort_expert_writer
        original_setattr = ExpertControllerV1.__setattr__

        def issue(*args, **kwargs):
            authority = original_issue(*args, **kwargs)
            issued.append(authority)
            return authority

        def create(*args, **kwargs):
            nonlocal writer_created
            writer = original_create(*args, **kwargs)
            created.append(writer)
            writer_created = True
            return writer

        def fail_after_writer(self, name, value):
            if (
                writer_created
                and type(value) is ExpertJournalWriteCapabilityV1
            ):
                raise ValueError("postcreate_controller_assignment_failed")
            return original_setattr(self, name, value)

        def abort(writer):
            aborts.append(writer)
            return original_abort(writer)

        with (
            mock.patch.object(
                controller_module,
                "issue_expert_environment_collection_authority",
                issue,
            ),
            mock.patch.object(
                controller_module,
                "create_expert_journal",
                create,
            ),
            mock.patch.object(
                controller_module,
                "abort_expert_writer",
                abort,
            ),
            mock.patch.object(
                ExpertControllerV1,
                "__setattr__",
                fail_after_writer,
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "^postcreate_controller_assignment_failed$",
            ):
                create_expert_controller(**exact_arguments)
        self.assertEqual(len(issued), 1)
        with self.assertRaises(ValueError):
            facade.collect_expert_current_environment(issued[0])
        self.assertEqual(len(created), 1)
        self.assertEqual(aborts, created)
        with self.assertRaises(ValueError):
            facade.issue_expert_terminal_permit(
                created[0],
                object(),  # type: ignore[arg-type]
            )
        purge_report = facade.recover_and_purge_expert_journals(
            self.authority
        )
        self.assertEqual(
            tuple(
                getattr(purge_report, item.name)
                for item in fields(type(purge_report))
            ),
            ((), (), (), ()),
        )
        with self.assertRaisesRegex(
            ValueError,
            "^expert_reader_manifest_invalid$",
        ):
            facade.issue_expert_read_capability(
                self.authority,
                self.manifest,
            )

    @unittest.skipUnless(hasattr(os, "fork"), "requires process fork")
    def test_postfork_factory_rejects_before_collection_or_create(self) -> None:
        read_fd, write_fd = os.pipe()
        calls: list[str] = []
        original_issue = (
            controller_module.issue_expert_environment_collection_authority
        )
        original_collect = (
            controller_module.collect_expert_current_environment
        )
        original_create = controller_module.create_expert_journal

        def issue(*args, **kwargs):
            calls.append("issue")
            return original_issue(*args, **kwargs)

        def collect(*args, **kwargs):
            calls.append("collect")
            return original_collect(*args, **kwargs)

        def create(*args, **kwargs):
            calls.append("create")
            return original_create(*args, **kwargs)

        with (
            mock.patch.object(
                controller_module,
                "issue_expert_environment_collection_authority",
                issue,
            ),
            mock.patch.object(
                controller_module,
                "collect_expert_current_environment",
                collect,
            ),
            mock.patch.object(
                controller_module,
                "create_expert_journal",
                create,
            ),
        ):
            child = os.fork()
            if child == 0:
                os.close(read_fd)
                try:
                    create_expert_controller(
                        authority=self.authority,
                        manifest=self.manifest,
                        universe=self.universe,
                        policy=self.policy,
                        ingress=self.ingress,
                        runtime=self.runtime,
                        persistence_authorizer=self.persistence_authorizer,
                        coordinator=self.coordinator,
                    )
                    outcome = "NO_ERROR"
                except BaseException as error:
                    outcome = type(error).__name__
                os.write(
                    write_fd,
                    f"{outcome}:{','.join(calls)}".encode("ascii"),
                )
                os.close(write_fd)
                os._exit(0)
            os.close(write_fd)
            payload = os.read(read_fd, 4096)
            os.close(read_fd)
            waited, status = os.waitpid(child, 0)
        self.assertEqual(waited, child)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        self.assertEqual(payload, b"WrongOwnerThread:")

    def test_timeout_returns_the_unchanged_published_snapshot(self) -> None:
        controller = self.create_controller()
        before = controller.snapshot()
        self.assertEqual(
            controller.process_one(timeout_seconds=0.001),
            before,
        )
        self.assertEqual(controller.snapshot(), before)
        _, groups, terminal, _ = self.read_companion()
        self.assertEqual(groups, ())
        self.assertIsNone(terminal)

    def test_real_raw_pipeline_orders_authority_pure_reduce_and_publication(
        self,
    ) -> None:
        controller = self.create_controller()
        thread, producer_result = self.enqueue_one()
        order: list[str] = []
        original_drain = BoundedIngress.drain_one_parent
        original_require = RetentionCoordinator.require_provider_operation
        original_transform = (
            ProviderPersistenceAuthorizer.authorize_transform
        )
        original_analysis = ProviderPersistenceAuthorizer.authorize_analysis
        original_poll = ProviderPersistenceAuthorizer.poll_session
        original_normalize = controller_module.normalize_expert_parent
        original_reduce = controller_module.reduce_expert_parent
        original_issue = controller_module.issue_expert_append_permit
        original_append = controller_module.append_expert_group
        original_ack = controller_module.acknowledge_expert_publication
        controller_authorization_window = False
        dependency_internal_calls = {
            "provider_operation": 0,
            "transform": 0,
            "analysis": 0,
            "poll": 0,
        }

        def drain(instance, runtime, *, timeout_seconds):
            nonlocal controller_authorization_window
            result = original_drain(
                instance,
                runtime,
                timeout_seconds=timeout_seconds,
            )
            order.append("drain_return")
            controller_authorization_window = True
            return result

        def require(instance):
            result = original_require(instance)
            if controller_authorization_window:
                order.append("provider_operation")
            else:
                dependency_internal_calls["provider_operation"] += 1
            return result

        def transform(instance, raw):
            result = original_transform(instance, raw)
            if controller_authorization_window:
                order.append("transform")
            else:
                dependency_internal_calls["transform"] += 1
            return result

        def analysis(instance):
            result = original_analysis(instance)
            if controller_authorization_window:
                order.append("analysis")
            else:
                dependency_internal_calls["analysis"] += 1
            return result

        def poll(instance):
            result = original_poll(instance)
            if controller_authorization_window:
                order.append("poll")
            else:
                dependency_internal_calls["poll"] += 1
            return result

        def normalize(*args, **kwargs):
            nonlocal controller_authorization_window
            order.append("normalize")
            controller_authorization_window = False
            return original_normalize(*args, **kwargs)

        def reduce(*args, **kwargs):
            order.append("reduce")
            return original_reduce(*args, **kwargs)

        def issue(*args, **kwargs):
            order.append("permit")
            return original_issue(*args, **kwargs)

        def append(*args, **kwargs):
            receipt = original_append(*args, **kwargs)
            order.append("durable_receipt")
            return receipt

        def acknowledge(*args, **kwargs):
            result = original_ack(*args, **kwargs)
            order.append("acknowledge")
            return result

        with (
            mock.patch.object(BoundedIngress, "drain_one_parent", drain),
            mock.patch.object(
                RetentionCoordinator,
                "require_provider_operation",
                require,
            ),
            mock.patch.object(
                ProviderPersistenceAuthorizer,
                "authorize_transform",
                transform,
            ),
            mock.patch.object(
                ProviderPersistenceAuthorizer,
                "authorize_analysis",
                analysis,
            ),
            mock.patch.object(
                ProviderPersistenceAuthorizer,
                "poll_session",
                poll,
            ),
            mock.patch.object(
                controller_module,
                "normalize_expert_parent",
                normalize,
            ),
            mock.patch.object(
                controller_module,
                "reduce_expert_parent",
                reduce,
            ),
            mock.patch.object(
                controller_module,
                "issue_expert_append_permit",
                issue,
            ),
            mock.patch.object(
                controller_module,
                "append_expert_group",
                append,
            ),
            mock.patch.object(
                controller_module,
                "acknowledge_expert_publication",
                acknowledge,
            ),
        ):
            state, cursor, terminal = controller.process_one(
                timeout_seconds=1.0
            )
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertNotIsInstance(producer_result[0], BaseException)
        self.assertIsNone(terminal)
        self.assertEqual(cursor.group_count, 1)
        self.assertEqual(cursor.record_count, 1)
        self.assertEqual(cursor.expert_state_sha256, expert_state_sha256(state))
        self.assertEqual(
            order,
            [
                "drain_return",
                "provider_operation",
                "transform",
                "analysis",
                "poll",
                "normalize",
                "reduce",
                "permit",
                "durable_receipt",
                "acknowledge",
            ],
        )
        self.assertGreaterEqual(
            dependency_internal_calls["transform"],
            1,
        )
        self.assertGreaterEqual(
            dependency_internal_calls["analysis"],
            3,
        )
        self.assertGreaterEqual(
            dependency_internal_calls["poll"],
            4,
        )

    def test_raw_fsync_and_io_verification_precede_authorization_and_normalization(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        entered_raw_write = threading.Event()
        release_raw_write = threading.Event()
        observations: list[tuple[int, int, int, int, int, int, int]] = []
        drain_returns: list[
            DurableIngressParentV1 | DurableEvidenceTerminalV1 | None
        ] = []
        normalize_calls: list[PersistedEvent] = []
        reduce_calls: list[object] = []
        authorization_calls: list[str] = []
        original_fsync = retention_module.os.fsync
        original_drain = BoundedIngress.drain_one_parent
        original_require = RetentionCoordinator.require_provider_operation
        original_transform = (
            ProviderPersistenceAuthorizer.authorize_transform
        )
        original_analysis = (
            ProviderPersistenceAuthorizer.authorize_analysis
        )
        original_poll = ProviderPersistenceAuthorizer.poll_session
        original_normalize = controller_module.normalize_expert_parent
        original_reduce = controller_module.reduce_expert_parent
        controller_authorization_window = False
        dependency_internal_calls = {
            "coordinator": 0,
            "transform": 0,
            "analysis": 0,
            "poll": 0,
        }
        private_writer_accesses: list[str] = []
        runtime_writer = self.runtime._writer

        class _PrivateWriterTripwire:
            @property
            def latest_raw(self):
                private_writer_accesses.append("latest_raw")
                raise AssertionError(
                    "controller must use the RAW returned by drain_one_parent"
                )

        private_writer_tripwire = _PrivateWriterTripwire()

        def gated_fsync(descriptor):
            if not entered_raw_write.is_set():
                entered_raw_write.set()
                if not release_raw_write.wait(5):
                    raise AssertionError("RAW durability barrier not released")
            return original_fsync(descriptor)

        def drain(instance, runtime, *, timeout_seconds):
            nonlocal controller_authorization_window
            result = original_drain(
                instance,
                runtime,
                timeout_seconds=timeout_seconds,
            )
            drain_returns.append(result)
            runtime._writer = private_writer_tripwire
            controller_authorization_window = True
            return result

        def require(instance):
            if controller_authorization_window:
                authorization_calls.append("coordinator")
            else:
                dependency_internal_calls["coordinator"] += 1
            return original_require(instance)

        def transform(instance, raw):
            if controller_authorization_window:
                authorization_calls.append("transform")
            else:
                dependency_internal_calls["transform"] += 1
            return original_transform(instance, raw)

        def analysis(instance):
            if controller_authorization_window:
                authorization_calls.append("analysis")
            else:
                dependency_internal_calls["analysis"] += 1
            return original_analysis(instance)

        def poll(instance):
            if controller_authorization_window:
                authorization_calls.append("poll")
            else:
                dependency_internal_calls["poll"] += 1
            return original_poll(instance)

        def normalize(manifest, parent):
            nonlocal controller_authorization_window
            normalize_calls.append(parent)
            controller_authorization_window = False
            return original_normalize(manifest, parent)

        def reduce(state, observations):
            reduce_calls.append((state, observations))
            return original_reduce(state, observations)

        def observe_before_fsync() -> None:
            self.assertTrue(entered_raw_write.wait(5))
            observations.append(
                (
                    len(drain_returns),
                    len(authorization_calls),
                    len(normalize_calls),
                    len(reduce_calls),
                    probe.append_permit_attempts,
                    probe.append_count,
                    probe.ack_count,
                )
            )
            release_raw_write.set()

        with (
            self.mocked_facade(probe),
            mock.patch.object(
                retention_module.os,
                "fsync",
                gated_fsync,
            ),
            mock.patch.object(BoundedIngress, "drain_one_parent", drain),
            mock.patch.object(
                RetentionCoordinator,
                "require_provider_operation",
                require,
            ),
            mock.patch.object(
                ProviderPersistenceAuthorizer,
                "authorize_transform",
                transform,
            ),
            mock.patch.object(
                ProviderPersistenceAuthorizer,
                "authorize_analysis",
                analysis,
            ),
            mock.patch.object(
                ProviderPersistenceAuthorizer,
                "poll_session",
                poll,
            ),
            mock.patch.object(
                controller_module,
                "normalize_expert_parent",
                normalize,
            ),
            mock.patch.object(
                controller_module,
                "reduce_expert_parent",
                reduce,
            ),
        ):
            controller = self.create_controller()
            producer, _ = self.enqueue_one()
            observer = threading.Thread(target=observe_before_fsync)
            observer.start()
            try:
                state, cursor, terminal = controller.process_one(
                    timeout_seconds=1.0
                )
            finally:
                self.runtime._writer = runtime_writer
            observer.join(5)
        producer.join(5)
        self.assertFalse(observer.is_alive())
        self.assertEqual(private_writer_accesses, [])
        self.assertEqual(observations, [(0, 0, 0, 0, 0, 0, 0)])
        self.assertEqual(len(drain_returns), 1)
        self.assertEqual(
            authorization_calls[:4],
            ["coordinator", "transform", "analysis", "poll"],
        )
        self.assertEqual(len(authorization_calls), 4)
        self.assertGreaterEqual(
            dependency_internal_calls["transform"],
            1,
        )
        self.assertGreaterEqual(
            dependency_internal_calls["poll"],
            4,
        )
        self.assertEqual(probe.tail_count, 0)
        self.assertEqual(len(normalize_calls), 1)
        self.assertEqual(len(reduce_calls), 1)
        parent = normalize_calls[0]
        self.assertIs(parent, self.phase1_writer.latest_raw)
        self.assertIs(parent.record_kind, RecordKind.RAW)
        self.assertEqual(parent.session_id, self.manifest.session_id)
        self.assertEqual(
            probe.group.parent.record_sha256,
            canonical_record_sha256(parent),
        )
        self.assertEqual(cursor.last_parent_record_sha256, probe.group.parent.record_sha256)
        self.assertEqual(cursor.expert_state_sha256, expert_state_sha256(state))
        self.assertIsNone(terminal)

    def test_group_payload_descriptor_and_cursor_are_exact(self) -> None:
        controller = self.create_controller()
        thread, _ = self.enqueue_one()
        captured_candidate: list[tuple[object, ...]] = []
        original_issue = controller_module.issue_expert_append_permit

        def issue(writer, expected_state, expected_cursor, group, payloads):
            captured_candidate.append(
                (
                    expected_state,
                    expected_cursor,
                    group,
                    payloads,
                )
            )
            return original_issue(
                writer,
                expected_state,
                expected_cursor,
                group,
                payloads,
            )

        with mock.patch.object(
            controller_module,
            "issue_expert_append_permit",
            issue,
        ):
            state, cursor, _ = controller.process_one(timeout_seconds=1.0)
        thread.join(5)
        self.assertEqual(len(captured_candidate), 1)
        expected_state, prior_cursor, group, payloads = captured_candidate[0]
        self.assertIs(type(prior_cursor), ExpertJournalCursorV1)
        self.assertIs(type(group), ExpertJournalGroupV1)
        self.assertEqual(expected_state, prior_cursor.expert_state_sha256)
        validate_expert_group_against_cursor(
            group,
            payloads,
            prior_cursor,
        )
        self.assertEqual(group.group_sequence, 1)
        self.assertEqual(group.first_expert_seq, 1)
        self.assertEqual(group.parent_output_count, len(group.records))
        self.assertEqual(len(group.records), len(payloads))
        self.assertEqual(cursor.group_count, prior_cursor.group_count + 1)
        self.assertEqual(
            cursor.record_count,
            prior_cursor.record_count + len(group.records),
        )
        self.assertEqual(
            cursor.last_parent_record_sha256,
            group.parent.record_sha256,
        )
        self.assertEqual(cursor.expert_state_sha256, expert_state_sha256(state))
        for record, payload in zip(group.records, payloads, strict=True):
            self.assertIs(type(record.payload), ExpertPayloadDescriptorV1)
            self.assertEqual(
                record.payload.content_type,
                "application/vnd.inci.expert+json",
            )
            self.assertEqual(
                record.payload.payload_encoding,
                "canonical-json-v1",
            )
            self.assertEqual(record.payload.payload_length, len(payload))
            decoded = decode_expert_event_payload(
                payload,
                event_kind=record.event_kind,
                event_version=record.event_version,
            )
            self.assertEqual(payload, canonical_expert_bytes(decoded))
            self.assertIs(type(decoded), ExpertObservationIgnoredPayloadV1)
            self.assertIs(
                decoded.observation.reason,
                ExpertIgnoreReasonV1.NORMALIZER_NOT_REGISTERED,
            )
            self.assertEqual(decoded.observation.parent, group.parent)
            self.assertIs(
                record.event_kind,
                ExpertEventKindV1.OBSERVATION_IGNORED,
            )

    def test_raw_only_parent_and_full_group_cursor_mutation_matrix(
        self,
    ) -> None:
        parent_mutations = (
            ("derived", {"record_kind": RecordKind.DERIVED}),
            ("control", {"record_kind": RecordKind.CONTROL}),
            (
                "wrong_session",
                {"session_id": "22222222-2222-4222-8222-222222222222"},
            ),
            ("wrong_chain_sequence", {"ingest_seq": 4}),
            ("payload_digest_mismatch", {"payload_sha256": "0" * 64}),
        )
        for name, changes in parent_mutations:
            with self.subTest(parent=name), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    durable_raw = case.runtime.ingest(
                        captured(case.persistence_authorizer)
                    )
                    forged = _forged_replace(durable_raw, **changes)
                    with (
                        mock.patch.object(
                            BoundedIngress,
                            "drain_one_parent",
                            return_value=forged,
                        ),
                        mock.patch.object(
                            controller_module,
                            "normalize_expert_parent",
                            side_effect=AssertionError(
                                "nonexact RAW must not normalize"
                            ),
                        ),
                    ):
                        with self.assertRaises((TypeError, ValueError)):
                            controller.process_one(timeout_seconds=1.0)
                    self.assertEqual(controller.snapshot(), before)
                self.assertEqual(probe.append_permit_attempts, 0)
                self.assertEqual(probe.append_count, 0)
                self.assertEqual(probe.terminal_append_count, 0)

        projection_mutations = (
            (
                "session_id",
                "22222222-2222-4222-8222-222222222222",
            ),
            ("ingest_seq", 4),
            ("record_sha256", "0" * 64),
            ("event_type", "forged-event"),
            ("event_version", 2),
            ("local_wall_ns", 999),
            ("local_monotonic_ns", 999),
            ("clock_uncertainty_ns", 999),
        )
        for field_name, changed_value in projection_mutations:
            with (
                self.subTest(projection=field_name),
                self.fresh_case() as case,
            ):
                probe = _RuledStoreProbe(case.collected)
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    durable_raw = case.runtime.ingest(
                        captured(case.persistence_authorizer)
                    )
                    observations = controller_module.normalize_expert_parent(
                        case.manifest,
                        durable_raw,
                    )
                    forged_parent = _forged_replace(
                        observations[0].parent,
                        **{field_name: changed_value},
                    )
                    forged_observation = _forged_replace(
                        observations[0],
                        parent=forged_parent,
                    )
                    with (
                        mock.patch.object(
                            BoundedIngress,
                            "drain_one_parent",
                            return_value=durable_raw,
                        ),
                        mock.patch.object(
                            controller_module,
                            "normalize_expert_parent",
                            return_value=(forged_observation,),
                        ),
                        mock.patch.object(
                            controller_module,
                            "reduce_expert_parent",
                            side_effect=AssertionError(
                                "forged parent projection must not reduce"
                            ),
                        ),
                    ):
                        with self.assertRaises((TypeError, ValueError)):
                            controller.process_one(timeout_seconds=1.0)
                    self.assertEqual(controller.snapshot(), before)
                self.assertEqual(probe.append_permit_attempts, 0)
                self.assertEqual(probe.append_count, 0)
                self.assertEqual(probe.terminal_append_count, 0)

        with self.fresh_case() as case:
            probe = _RuledStoreProbe(case.collected)

            def normalize_two(manifest, parent):
                return bind_expert_observation_drafts(
                    manifest,
                    parent,
                    manifest.normalizers.fallback,
                    (
                        ExpertSynchronizationDraftV1(
                            synchronization_input()
                        ),
                        ExpertSynchronizationDraftV1(
                            synchronization_input()
                        ),
                    ),
                )

            with (
                case.mocked_facade(probe),
                mock.patch.object(
                    controller_module,
                    "normalize_expert_parent",
                    normalize_two,
                ),
            ):
                controller = case.create_controller()
                producer, _ = case.enqueue_one()
                result = controller.process_one(timeout_seconds=1.0)
                producer.join(5)
            self.assertIsNotNone(probe.group)
            self.assertIsNotNone(probe.payloads)
            self.assertIsNotNone(probe.initial_cursor)
            group = probe.group
            payloads = probe.payloads
            prior_cursor = probe.initial_cursor
            self.assertEqual(group.parent_output_count, 2)
            self.assertEqual(len(group.records), 2)
            self.assertEqual(len(group.trace_steps), 2)
            self.assertEqual(len(payloads), 2)
            validate_expert_group_against_cursor(
                group,
                payloads,
                prior_cursor,
            )
            self.assertEqual(
                [item.parent_output_index for item in group.records],
                [0, 1],
            )
            self.assertEqual(
                [item.parent_output_count for item in group.records],
                [2, 2],
            )
            self.assertEqual(
                [item.expert_seq for item in group.records],
                [prior_cursor.expert_seq + 1, prior_cursor.expert_seq + 2],
            )
            decoded = tuple(
                decode_expert_event_payload(
                    payload,
                    event_kind=record.event_kind,
                    event_version=record.event_version,
                )
                for record, payload in zip(
                    group.records,
                    payloads,
                    strict=True,
                )
            )
            self.assertEqual(
                [
                    item.observation.parent_output_index
                    for item in decoded
                ],
                [0, 1],
            )
            self.assertEqual(
                group.records[1].prior_expert_record_sha256,
                group.records[0].record_sha256,
            )
            self.assertEqual(
                group.records[1].prior_expert_state_sha256,
                group.records[0].post_expert_state_sha256,
            )
            self.assertEqual(
                group.trace_steps[1].prior_trace_sha256,
                group.trace_steps[0].post_trace_sha256,
            )
            for record, trace in zip(
                group.records,
                group.trace_steps,
                strict=True,
            ):
                self.assertEqual(trace.expert_seq, record.expert_seq)
                self.assertEqual(
                    trace.expert_record_sha256,
                    record.record_sha256,
                )
                self.assertEqual(
                    trace.post_expert_state_sha256,
                    record.post_expert_state_sha256,
                )
            self.assertEqual(
                group.final_expert_record_sha256,
                group.records[-1].record_sha256,
            )
            self.assertEqual(
                group.post_expert_state_sha256,
                group.records[-1].post_expert_state_sha256,
            )
            self.assertEqual(
                group.post_trace_sha256,
                group.trace_steps[-1].post_trace_sha256,
            )
            self.assertEqual(result[1].record_count, 2)
            self.assertEqual(probe.ordinary_cas_count, 1)
            self.assertEqual(probe.append_count, 1)
            self.assertEqual(probe.ack_count, 1)

        mutation_matrix = (
            *(
                ("descriptor", item.name)
                for item in fields(ExpertPayloadDescriptorV1)
            ),
            *(
                ("record", item.name)
                for item in fields(ExpertJournalRecordV1)
            ),
            *(
                ("trace", item.name)
                for item in fields(ExpertTraceStepV1)
            ),
            *(
                ("group", item.name)
                for item in fields(ExpertJournalGroupV1)
            ),
            *(
                ("cursor", item.name)
                for item in fields(ExpertJournalCursorV1)
            ),
        )

        def different_value(field_name: str, current: object) -> object:
            if type(current) is bool:
                return not current
            if type(current) is int:
                return current + 1
            if type(current) is str:
                if field_name == "session_id":
                    return "22222222-2222-4222-8222-222222222222"
                if field_name.endswith("sha256"):
                    return (
                        "1" * 64
                        if current == "0" * 64
                        else "0" * 64
                    )
                return "mutated"
            if type(current) is tuple:
                return ()
            if hasattr(type(current), "__members__"):
                return next(
                    value
                    for value in type(current)
                    if value is not current
                )
            return object()

        for scope, field_name in mutation_matrix:
            label = f"{scope}.{field_name}"
            with (
                self.subTest(scope=scope, field=field_name),
                self.fresh_case() as case,
            ):
                probe = _RuledStoreProbe(case.collected)
                original_validate = (
                    controller_module.validate_expert_group_against_cursor
                )
                original_cursor_init = ExpertJournalCursorV1.__init__
                validation_calls: list[str] = []
                constructed_candidates: list[ExpertJournalCursorV1] = []

                def mutate_actual_group(group, payloads, prior_cursor):
                    original_validate(group, payloads, prior_cursor)
                    record = group.records[0]
                    descriptor = record.payload
                    trace = group.trace_steps[0]
                    if scope == "descriptor":
                        target = descriptor
                    elif scope == "record":
                        target = record
                    elif scope == "trace":
                        target = trace
                    elif scope == "group":
                        target = group
                    else:
                        raise AssertionError(scope)
                    object.__setattr__(
                        target,
                        field_name,
                        different_value(
                            field_name,
                            getattr(target, field_name),
                        )
                    )
                    validation_calls.append(label)
                    return original_validate(group, payloads, prior_cursor)

                def mutate_actual_candidate(self, *args, **kwargs):
                    original_cursor_init(self, *args, **kwargs)
                    constructed_candidates.append(self)
                    object.__setattr__(
                        self,
                        field_name,
                        different_value(
                            field_name,
                            getattr(self, field_name),
                        ),
                    )

                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    before_private = _controller_private_publication(
                        controller
                    )
                    with (
                        mock.patch.object(
                            controller_module,
                            "validate_expert_group_against_cursor",
                            (
                                mutate_actual_group
                                if scope != "cursor"
                                else original_validate
                            ),
                        ),
                        mock.patch.object(
                            ExpertJournalCursorV1,
                            "__init__",
                            (
                                mutate_actual_candidate
                                if scope == "cursor"
                                else original_cursor_init
                            ),
                        ),
                    ):
                        producer, _ = case.enqueue_one()
                        with self.assertRaises((TypeError, ValueError)):
                            controller.process_one(timeout_seconds=1.0)
                    producer.join(5)
                    self.assertEqual(controller.snapshot(), before)
                    after_private = _controller_private_publication(
                        controller
                    )
                if scope == "cursor":
                    self.assertEqual(len(constructed_candidates), 1)
                    self.assertIs(
                        type(constructed_candidates[0]),
                        ExpertJournalCursorV1,
                    )
                    self.assertEqual(validation_calls, [])
                else:
                    self.assertEqual(validation_calls, [label])
                self.assertEqual(probe.append_permit_attempts, 0)
                self.assertEqual(probe.append_count, 0)
                self.assertEqual(probe.ack_count, 0)
                self.assertEqual(probe.terminal_append_count, 0)
                self.assertIs(after_private[0], before_private[0])
                self.assertIs(after_private[1], before_private[1])

    def test_authorization_denial_writes_no_group_or_terminal(self) -> None:
        controller = self.create_controller()
        before = controller.snapshot()
        thread, _ = self.enqueue_one()
        normalize_calls: list[object] = []
        controller_authorization_window = False
        original_drain = BoundedIngress.drain_one_parent
        original_transform = (
            ProviderPersistenceAuthorizer.authorize_transform
        )

        def drain(instance, runtime, *, timeout_seconds):
            nonlocal controller_authorization_window
            result = original_drain(
                instance,
                runtime,
                timeout_seconds=timeout_seconds,
            )
            controller_authorization_window = True
            return result

        def deny_controller_transform(instance, raw):
            if controller_authorization_window:
                raise ExpertLiveAuthorizationDenied()
            return original_transform(instance, raw)

        with (
            mock.patch.object(BoundedIngress, "drain_one_parent", drain),
            mock.patch.object(
                ProviderPersistenceAuthorizer,
                "authorize_transform",
                deny_controller_transform,
            ),
            mock.patch.object(
                controller_module,
                "normalize_expert_parent",
                side_effect=lambda *args: normalize_calls.append(args),
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^expert_consumed_parent_processing_failed$",
            ) as caught:
                controller.process_one(timeout_seconds=1.0)
        self.assertIsNone(caught.exception.__cause__)
        thread.join(5)
        self.assertEqual(normalize_calls, [])
        self.assertEqual(controller.snapshot(), before)
        purge_report = facade.recover_and_purge_expert_journals(
            self.authority
        )
        self.assertEqual(
            tuple(
                getattr(purge_report, item.name)
                for item in fields(type(purge_report))
            ),
            ((), (), (), ()),
        )
        with self.assertRaisesRegex(
            ValueError,
            "^expert_reader_manifest_invalid$",
        ):
            facade.issue_expert_read_capability(
                self.authority,
                self.manifest,
            )
        with self.assertRaisesRegex(
            RuntimeError,
            "^expert_controller_unavailable$",
        ):
            controller.process_one(timeout_seconds=0.001)

    def test_authorization_denial_matrix_before_and_between_group_seams(
        self,
    ) -> None:
        cases = (
            ("coordinator_operation", "coordinator", "raise"),
            ("transform", "transform", "raise"),
            ("analysis", "analysis", "raise"),
            ("malformed_rebound", "analysis", "malformed"),
            ("poll_not_false", "poll", "true"),
            ("analysis_after_permit", "analysis_ordinal", 2),
            ("analysis_before_append", "analysis_ordinal", 3),
            ("analysis_before_ack", "analysis_ordinal", 4),
            ("poll_after_permit", "poll_ordinal", 2),
            ("poll_before_append", "poll_ordinal", 3),
            ("poll_before_ack", "poll_ordinal", 4),
            ("permit_identity_loss", "permit", "raise"),
            ("ack_deadline_equality", "ack", "raise"),
        )
        for name, seam, behavior in cases:
            with self.subTest(name=name), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    producer, _ = case.enqueue_one()
                    normalize_calls: list[object] = []
                    original_normalize = (
                        controller_module.normalize_expert_parent
                    )
                    original_analysis = (
                        ProviderPersistenceAuthorizer.authorize_analysis
                    )
                    original_poll = (
                        ProviderPersistenceAuthorizer.poll_session
                    )
                    original_drain = BoundedIngress.drain_one_parent
                    original_require = (
                        RetentionCoordinator.require_provider_operation
                    )
                    original_transform = (
                        ProviderPersistenceAuthorizer.authorize_transform
                    )
                    authorization_call_count = 0
                    controller_authorization_window = False

                    def normalize(*args, **kwargs):
                        normalize_calls.append(args)
                        return original_normalize(*args, **kwargs)

                    def drain(instance, runtime, *, timeout_seconds):
                        nonlocal controller_authorization_window
                        result = original_drain(
                            instance,
                            runtime,
                            timeout_seconds=timeout_seconds,
                        )
                        controller_authorization_window = True
                        return result

                    def fail_coordinator(instance):
                        if controller_authorization_window:
                            raise ExpertLiveAuthorizationDenied()
                        return original_require(instance)

                    def fail_transform(instance, raw):
                        if controller_authorization_window:
                            raise ExpertLiveAuthorizationDenied()
                        return original_transform(instance, raw)

                    def fail_analysis(instance):
                        if not controller_authorization_window:
                            return original_analysis(instance)
                        if behavior == "raise":
                            raise ExpertLiveAuthorizationDenied()
                        return object()

                    def fail_poll(instance):
                        if controller_authorization_window:
                            return True
                        return original_poll(instance)

                    def fail_analysis_ordinal(instance):
                        nonlocal authorization_call_count
                        if not controller_authorization_window:
                            return original_analysis(instance)
                        authorization_call_count += 1
                        if authorization_call_count == behavior:
                            raise ExpertLiveAuthorizationDenied()
                        return original_analysis(instance)

                    def fail_poll_ordinal(instance):
                        nonlocal authorization_call_count
                        if not controller_authorization_window:
                            return original_poll(instance)
                        authorization_call_count += 1
                        if authorization_call_count == behavior:
                            return True
                        return original_poll(instance)

                    with ExitStack() as stack:
                        stack.enter_context(
                            mock.patch.object(
                                controller_module,
                                "normalize_expert_parent",
                                normalize,
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                BoundedIngress,
                                "drain_one_parent",
                                drain,
                            )
                        )
                        if seam == "coordinator":
                            stack.enter_context(
                                mock.patch.object(
                                    RetentionCoordinator,
                                    "require_provider_operation",
                                    fail_coordinator,
                                )
                            )
                        elif seam == "transform":
                            stack.enter_context(
                                mock.patch.object(
                                    ProviderPersistenceAuthorizer,
                                    "authorize_transform",
                                    fail_transform,
                                )
                            )
                        elif seam == "analysis":
                            stack.enter_context(
                                mock.patch.object(
                                    ProviderPersistenceAuthorizer,
                                    "authorize_analysis",
                                    fail_analysis,
                                )
                            )
                        elif seam == "poll":
                            stack.enter_context(
                                mock.patch.object(
                                    ProviderPersistenceAuthorizer,
                                    "poll_session",
                                    fail_poll,
                                )
                            )
                        elif seam == "analysis_ordinal":
                            stack.enter_context(
                                mock.patch.object(
                                    ProviderPersistenceAuthorizer,
                                    "authorize_analysis",
                                    fail_analysis_ordinal,
                                )
                            )
                        elif seam == "poll_ordinal":
                            stack.enter_context(
                                mock.patch.object(
                                    ProviderPersistenceAuthorizer,
                                    "poll_session",
                                    fail_poll_ordinal,
                                )
                            )
                        elif seam == "permit":
                            probe.issue_append = mock.Mock(
                                side_effect=ExpertLiveAuthorizationDenied()
                            )
                            stack.enter_context(
                                mock.patch.object(
                                    controller_module,
                                    "issue_expert_append_permit",
                                    probe.issue_append,
                                )
                            )
                        elif seam == "ack":
                            probe.ack_failure = (
                                ExpertLiveAuthorizationDenied()
                            )
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "^expert_consumed_parent_processing_failed$",
                        ) as caught:
                            controller.process_one(timeout_seconds=1.0)
                        self.assertIsNone(caught.exception.__cause__)
                    producer.join(5)
                    self.assertEqual(controller.snapshot(), before)
                if name in {
                    "coordinator_operation",
                    "transform",
                    "analysis",
                    "malformed_rebound",
                    "poll_not_false",
                }:
                    self.assertEqual(normalize_calls, [])
                else:
                    self.assertEqual(len(normalize_calls), 1)
                if seam in {
                    "coordinator",
                    "transform",
                    "analysis",
                    "poll",
                }:
                    expected_append_count = 0
                elif name in {
                    "analysis_before_ack",
                    "poll_before_ack",
                    "ack_deadline_equality",
                }:
                    expected_append_count = 1
                else:
                    expected_append_count = 0
                self.assertEqual(
                    probe.append_count,
                    expected_append_count,
                )
                self.assertEqual(probe.terminal_permit_count, 0)
                self.assertEqual(probe.terminal_append_count, 0)
                self.assertEqual(probe.abort_count, 1)

    def test_receipt_mismatch_never_publishes(self) -> None:
        controller = self.create_controller()
        before = controller.snapshot()
        thread, _ = self.enqueue_one()
        original = controller_module.append_expert_group

        def bad_receipt(permit):
            receipt = original(permit)
            return replace(receipt, group_sha256="f" * 64)

        with mock.patch.object(
            controller_module,
            "append_expert_group",
            bad_receipt,
        ):
            with self.assertRaises(ValueError):
                controller.process_one(timeout_seconds=1.0)
        thread.join(5)
        self.assertEqual(controller.snapshot(), before)

    def test_append_receipt_field_mutation_matrix_never_acknowledges_or_publishes(
        self,
    ) -> None:
        mutations = {
            "session_id": (
                lambda receipt: "22222222-2222-4222-8222-222222222222"
            ),
            "group_sequence": (
                lambda receipt: receipt.group_sequence + 1
            ),
            "group_sha256": lambda receipt: "0" * 64,
            "last_parent_record_sha256": lambda receipt: "1" * 64,
            "last_expert_seq": lambda receipt: receipt.last_expert_seq + 1,
            "final_expert_record_sha256": lambda receipt: "2" * 64,
            "post_expert_state_sha256": lambda receipt: "3" * 64,
            "post_expert_trace_sha256": lambda receipt: "4" * 64,
            "durable_end_offset": (
                lambda receipt: receipt.durable_end_offset + 1
            ),
        }
        for field_name, replacement in mutations.items():
            with self.subTest(field=field_name), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    producer, _ = case.enqueue_one()

                    def mutate(receipt, *, _field=field_name, _value=replacement):
                        return replace(
                            receipt,
                            **{_field: _value(receipt)},
                        )

                    probe.receipt_mutator = mutate
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "^expert_consumed_parent_processing_failed$",
                    ) as raised:
                        controller.process_one(timeout_seconds=1.0)
                    self.assertIsNone(raised.exception.__cause__)
                    producer.join(5)
                    self.assertEqual(controller.snapshot(), before)
                    cleanup = controller.close()
                    self.assertEqual(cleanup[:2], before[:2])
                    self.assertIsNone(cleanup[2])
                self.assertEqual(probe.append_count, 1)
                self.assertEqual(probe.ack_count, 0)
                self.assertEqual(probe.abort_count, 1)
                self.assertEqual(probe.terminal_permit_count, 0)
                self.assertEqual(probe.terminal_append_count, 0)

    def test_acknowledgement_failure_never_publishes(self) -> None:
        controller = self.create_controller()
        before = controller.snapshot()
        thread, _ = self.enqueue_one()
        with mock.patch.object(
            controller_module,
            "acknowledge_expert_publication",
            side_effect=ValueError("publication_denied"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "^expert_consumed_parent_processing_failed$",
            ) as raised:
                controller.process_one(timeout_seconds=1.0)
        self.assertIsNone(raised.exception.__cause__)
        thread.join(5)
        self.assertEqual(controller.snapshot(), before)

    def test_receipt_pending_rejects_second_ordinary_emergency_and_terminal_permits(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        rejected: list[str] = []

        def attempt_second_permits() -> None:
            self.assertIsNotNone(probe.group)
            self.assertIsNotNone(probe.payloads)
            self.assertIsNotNone(probe.initial_cursor)
            group = probe.group
            payloads = probe.payloads
            initial_cursor = probe.initial_cursor
            operations = (
                (
                    "ordinary",
                    lambda: controller_module.issue_expert_append_permit(
                        probe.writer,
                        initial_cursor.expert_state_sha256,
                        initial_cursor,
                        group,
                        payloads,
                    ),
                ),
                (
                    "emergency",
                    lambda: controller_module.issue_expert_emergency_append_permit(
                        probe.writer,
                        expected_state_sha256=(
                            initial_cursor.expert_state_sha256
                        ),
                        expected_cursor=initial_cursor,
                        evidence_terminal=object(),
                        group=group,
                        payloads=payloads,
                        terminal=object(),
                    ),
                ),
                (
                    "terminal",
                    lambda: controller_module.issue_expert_terminal_permit(
                        probe.writer,
                        object(),
                    ),
                ),
            )
            for name, operation in operations:
                with self.assertRaises(ValueError):
                    operation()
                rejected.append(name)

        probe.append_pause = attempt_second_permits
        with self.mocked_facade(probe):
            controller = self.create_controller()
            producer, _ = self.enqueue_one()
            result = controller.process_one(timeout_seconds=1.0)
        producer.join(5)
        self.assertEqual(rejected, ["ordinary", "emergency", "terminal"])
        self.assertEqual(probe.append_permit_attempts, 2)
        self.assertEqual(probe.cas_count, 1)
        self.assertEqual(probe.ordinary_cas_count, 1)
        self.assertEqual(probe.emergency_cas_count, 0)
        self.assertEqual(probe.emergency_permit_count, 1)
        self.assertEqual(probe.terminal_permit_count, 1)
        self.assertEqual(probe.append_count, 1)
        self.assertEqual(probe.ack_count, 1)
        self.assertFalse(probe.receipt_pending)
        self.assertIsNone(probe.pending_receipt)
        self.assertEqual(controller.snapshot(), result)

    def test_two_contenders_share_snapshot_winner_acknowledges_loser_writes_nothing(
        self,
    ) -> None:
        for stale_field in ("state", "cursor", "writer_generation_head"):
            with self.subTest(stale_field=stale_field), self.fresh_case() as case:
                controller = case.create_controller()
                before = controller.snapshot()
                writer = _controller_private_writer(controller)
                writer_state = store_module._WRITERS[writer]
                before_generation = writer_state.generation
                durable_raw = case.runtime.ingest(
                    captured(case.persistence_authorizer)
                )
                outer_ready = threading.Event()
                winner_ready = threading.Event()
                release_winner = threading.Event()
                candidates: list[
                    tuple[
                        ExpertJournalGroupV1,
                        tuple[bytes, ...],
                        ExpertJournalCursorV1,
                    ]
                ] = []
                winner_mailbox: list[
                    tuple[
                        ExpertStateV1,
                        ExpertJournalCursorV1,
                        ExpertSessionTerminalV1 | None,
                    ]
                ] = []
                loser_publication: list[
                    tuple[
                        ExpertStateV1,
                        ExpertJournalCursorV1,
                        ExpertSessionTerminalV1 | None,
                    ]
                ] = []
                issue_attempts = 0
                issued_permits = 0
                append_count = 0
                acknowledgement_count = 0
                abort_count = 0
                terminal_count = 0
                in_winner = False
                original_validate = (
                    controller_module.validate_expert_group_against_cursor
                )
                original_issue = controller_module.issue_expert_append_permit
                original_append = controller_module.append_expert_group
                original_ack = (
                    controller_module.acknowledge_expert_publication
                )
                original_abort = controller_module.abort_expert_writer
                original_issue_terminal = (
                    controller_module.issue_expert_terminal_permit
                )
                original_append_terminal = (
                    controller_module.append_expert_terminal
                )

                def observe_both_candidates() -> None:
                    self.assertTrue(outer_ready.wait(5))
                    self.assertTrue(winner_ready.wait(5))
                    self.assertEqual(len(candidates), 2)
                    self.assertIs(candidates[0][2], before[1])
                    self.assertIs(candidates[1][2], before[1])
                    self.assertIsNot(candidates[0][0], candidates[1][0])
                    release_winner.set()

                observer = threading.Thread(target=observe_both_candidates)
                observer.start()

                def validate(group, payloads, prior_cursor):
                    nonlocal in_winner
                    original_validate(group, payloads, prior_cursor)
                    candidates.append((group, payloads, prior_cursor))
                    if in_winner:
                        winner_ready.set()
                        self.assertTrue(release_winner.wait(5))
                        return None
                    outer_ready.set()
                    in_winner = True
                    try:
                        winner_mailbox.append(
                            controller.process_one(timeout_seconds=1.0)
                        )
                    finally:
                        in_winner = False
                    winner_state, winner_cursor, _ = winner_mailbox[-1]
                    if stale_field == "state":
                        _replace_controller_private_exact(
                            controller,
                            winner_state,
                        )
                        _replace_controller_private_exact(
                            controller,
                            before[1],
                        )
                        writer_state.cursor = before[1]
                        writer_state.generation = before_generation
                    elif stale_field == "cursor":
                        _replace_controller_private_exact(
                            controller,
                            before[0],
                        )
                        _replace_controller_private_exact(
                            controller,
                            winner_cursor,
                        )
                        writer_state.cursor = before[1]
                        writer_state.generation = before_generation
                    else:
                        _replace_controller_private_exact(
                            controller,
                            before[0],
                        )
                        _replace_controller_private_exact(
                            controller,
                            before[1],
                        )
                        writer_state.cursor = winner_cursor
                        writer_state.generation = before_generation + 1
                    published_state, published_cursor = (
                        _controller_private_publication(controller)
                    )
                    loser_publication.append(
                        (published_state, published_cursor, None)
                    )
                    return None

                def issue(*args, **kwargs):
                    nonlocal issue_attempts, issued_permits
                    issue_attempts += 1
                    permit = original_issue(*args, **kwargs)
                    issued_permits += 1
                    return permit

                def append(*args, **kwargs):
                    nonlocal append_count
                    receipt = original_append(*args, **kwargs)
                    append_count += 1
                    return receipt

                def acknowledge(*args, **kwargs):
                    nonlocal acknowledgement_count
                    result = original_ack(*args, **kwargs)
                    acknowledgement_count += 1
                    return result

                def abort(*args, **kwargs):
                    nonlocal abort_count
                    abort_count += 1
                    return original_abort(*args, **kwargs)

                def issue_terminal(*args, **kwargs):
                    nonlocal terminal_count
                    terminal_count += 1
                    return original_issue_terminal(*args, **kwargs)

                def append_terminal(*args, **kwargs):
                    nonlocal terminal_count
                    terminal_count += 1
                    return original_append_terminal(*args, **kwargs)

                with (
                    mock.patch.object(
                        BoundedIngress,
                        "drain_one_parent",
                        side_effect=(durable_raw, durable_raw),
                    ),
                    mock.patch.object(
                        controller_module,
                        "validate_expert_group_against_cursor",
                        validate,
                    ),
                    mock.patch.object(
                        controller_module,
                        "issue_expert_append_permit",
                        issue,
                    ),
                    mock.patch.object(
                        controller_module,
                        "append_expert_group",
                        append,
                    ),
                    mock.patch.object(
                        controller_module,
                        "acknowledge_expert_publication",
                        acknowledge,
                    ),
                    mock.patch.object(
                        controller_module,
                        "abort_expert_writer",
                        abort,
                    ),
                    mock.patch.object(
                        controller_module,
                        "issue_expert_terminal_permit",
                        issue_terminal,
                    ),
                    mock.patch.object(
                        controller_module,
                        "append_expert_terminal",
                        append_terminal,
                    ),
                ):
                    with self.assertRaises(ValueError):
                        controller.process_one(timeout_seconds=1.0)
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "^expert_controller_unavailable$",
                    ):
                        controller.process_one(timeout_seconds=0.001)
                observer.join(5)
                self.assertFalse(observer.is_alive())
                self.assertEqual(len(candidates), 2)
                self.assertEqual(len(winner_mailbox), 1)
                self.assertEqual(controller.snapshot(), loser_publication[-1])
                self.assertEqual(issued_permits, 1)
                self.assertEqual(append_count, 1)
                self.assertEqual(acknowledgement_count, 1)
                self.assertEqual(
                    issue_attempts,
                    2 if stale_field == "writer_generation_head" else 1,
                )
                self.assertEqual(terminal_count, 0)
                self.assertEqual(abort_count, 1)

    def test_acknowledgement_is_inside_uninterrupted_publication_lock(
        self,
    ) -> None:
        controller = self.create_controller()
        before = controller.snapshot()
        thread, _ = self.enqueue_one()
        entered = threading.Event()
        release = threading.Event()
        mailbox: list[object] = [before]
        original = controller_module.acknowledge_expert_publication

        def gated_ack(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(*args, **kwargs)

        observations: list[object] = []

        def observe_mailbox() -> None:
            self.assertTrue(entered.wait(5))
            observations.append(tuple(mailbox))
            release.set()

        with mock.patch.object(
            controller_module,
            "acknowledge_expert_publication",
            gated_ack,
        ):
            observer = threading.Thread(target=observe_mailbox)
            observer.start()
            mailbox.append(
                controller.process_one(timeout_seconds=1.0)
            )
            observer.join(5)
        self.assertFalse(observer.is_alive())
        self.assertEqual(observations, [(before,)])
        self.assertEqual(len(mailbox), 2)

    def test_paused_transition_mailbox_assignment_matrix(self) -> None:
        ordinary_seams = ("append", "receipt_return", "acknowledgement")
        for seam in ordinary_seams:
            with self.subTest(seam=seam), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                entered = threading.Event()
                release = threading.Event()
                mailbox: list[object] = []
                observed: list[
                    tuple[
                        tuple[object, ...],
                        int,
                        bool,
                        int,
                        ExpertStateV1,
                        ExpertJournalCursorV1,
                    ]
                ] = []

                def pause() -> None:
                    entered.set()
                    self.assertTrue(release.wait(5))

                if seam == "append":
                    probe.append_pause = pause
                elif seam == "receipt_return":
                    def pause_receipt(receipt):
                        pause()
                        return receipt

                    probe.receipt_mutator = pause_receipt
                else:
                    probe.ack_pause = pause

                def observe() -> None:
                    self.assertTrue(entered.wait(5))
                    observed.append(
                        (
                            tuple(mailbox),
                            probe.ack_count,
                            probe.receipt_pending,
                            probe.append_count,
                            *_controller_private_publication(controller),
                        )
                    )
                    release.set()

                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    before_private = _controller_private_publication(
                        controller
                    )
                    mailbox.append(before)
                    producer, _ = case.enqueue_one()
                    observer = threading.Thread(target=observe)
                    observer.start()
                    mailbox.append(
                        controller.process_one(timeout_seconds=1.0)
                    )
                    observer.join(5)
                    producer.join(5)
                self.assertFalse(observer.is_alive())
                self.assertEqual(
                    observed,
                    [
                        (
                            (before,),
                            1 if seam == "acknowledgement" else 0,
                            True,
                            1,
                            *before_private,
                        )
                    ],
                )
                self.assertIs(observed[0][-2], before_private[0])
                self.assertIs(observed[0][-1], before_private[1])
                self.assertEqual(len(mailbox), 2)
                self.assertEqual(controller.snapshot(), mailbox[-1])

        with self.fresh_case() as case:
            probe = _RuledStoreProbe(case.collected)
            entered = threading.Event()
            release = threading.Event()
            mailbox: list[object] = []
            observed: list[
                tuple[
                    tuple[object, ...],
                    int,
                    int,
                    DurableExpertTerminalReceiptV1 | None,
                    ExpertStateV1,
                    ExpertJournalCursorV1,
                ]
            ] = []
            returned_terminal: list[PersistedEvent] = []
            original_drain = BoundedIngress.drain_one_parent

            def drain(instance, runtime, *, timeout_seconds):
                envelope = original_drain(
                    instance,
                    runtime,
                    timeout_seconds=timeout_seconds,
                )
                if type(envelope) is DurableEvidenceTerminalV1:
                    returned_terminal.append(envelope.terminal)
                return envelope

            def build(writer, *, final_state, final_cursor):
                evidence = returned_terminal[-1]
                return (
                    evidence,
                    _terminal_for(
                        case.manifest,
                        final_cursor,
                        evidence,
                        clean=True,
                        evidence_reason="session_end",
                    ),
                )

            def pause_terminal() -> None:
                entered.set()
                self.assertTrue(release.wait(5))

            def observe_terminal() -> None:
                self.assertTrue(entered.wait(5))
                observed.append(
                    (
                        tuple(mailbox),
                        probe.terminal_permit_count,
                        probe.terminal_append_count,
                        probe.last_terminal_receipt,
                        *_controller_private_publication(controller),
                    )
                )
                release.set()

            probe.build_terminal = build  # type: ignore[method-assign]
            probe.terminal_pause = pause_terminal
            with (
                case.mocked_facade(probe),
                mock.patch.object(BoundedIngress, "drain_one_parent", drain),
            ):
                controller = case.create_controller()
                before = controller.snapshot()
                before_private = _controller_private_publication(controller)
                mailbox.append(before)
                case.ingress.close_inputs()
                observer = threading.Thread(target=observe_terminal)
                observer.start()
                mailbox.append(
                    controller.process_one(timeout_seconds=1.0)
                )
                observer.join(5)
            self.assertFalse(observer.is_alive())
            self.assertEqual(
                observed,
                [((before,), 1, 1, None, *before_private)],
            )
            self.assertIs(observed[0][-2], before_private[0])
            self.assertIs(observed[0][-1], before_private[1])
            self.assertIs(type(mailbox[-1][2]), ExpertSessionTerminalV1)

        with self.fresh_case() as case:
            probe = _RuledStoreProbe(case.collected)
            probe.prewrite_capacity_error = ExpertPrewriteCapacityError(
                requested_bytes=4096,
                available_bytes=1024,
                emergency_reserve_bytes=EXPERT_EMERGENCY_RESERVE_BYTES,
            )
            entered = threading.Event()
            release = threading.Event()
            mailbox: list[object] = []
            observed: list[
                tuple[
                    tuple[object, ...],
                    int,
                    int,
                    DurableExpertEmergencyReceiptV1 | None,
                    ExpertStateV1,
                    ExpertJournalCursorV1,
                ]
            ] = []
            pending_values: list[object] = []
            abort_reasons: list[object] = []
            original_abort_pending = (
                ExpertControllerV1
                .abort_pending_durable_companion_emergency_v1
            )

            def pause_before_abort(
                instance,
                pending,
                *,
                reason,
            ):
                pending_values.append(pending)
                abort_reasons.append(reason)
                entered.set()
                self.assertTrue(release.wait(5))
                return original_abort_pending(
                    instance,
                    pending,
                    reason=reason,
                )

            def observe_capacity_pending() -> None:
                self.assertTrue(entered.wait(5))
                observed.append(
                    (
                        tuple(mailbox),
                        probe.emergency_permit_count,
                        probe.emergency_append_count,
                        probe.last_emergency_receipt,
                        *_controller_private_publication(controller),
                    )
                )
                release.set()

            with (
                case.mocked_facade(probe),
                mock.patch.object(
                    ExpertControllerV1,
                    "abort_pending_durable_companion_emergency_v1",
                    new=pause_before_abort,
                ),
                mock.patch.object(
                    BoundedIngress,
                    "close_external_halt",
                    side_effect=AssertionError(
                        "legacy capacity denial must not bridge ingress"
                    ),
                ),
            ):
                controller = case.create_controller()
                before = controller.snapshot()
                before_private = _controller_private_publication(controller)
                mailbox.append(before)
                producer, producer_result = case.enqueue_one()
                observer = threading.Thread(target=observe_capacity_pending)
                observer.start()
                error_type = getattr(
                    controller_module,
                    "ExpertCapacityExceeded",
                )
                with self.assertRaises(error_type) as raised:
                    controller.process_one(timeout_seconds=1.0)
                observer.join(5)
                producer.join(5)
            self.assertFalse(observer.is_alive())
            self.assertFalse(producer.is_alive())
            self.assertNotIsInstance(producer_result[0], BaseException)
            self.assertEqual(
                raised.exception.args,
                ("expert_legacy_process_one_capacity_denied",),
            )
            self.assertIsNone(raised.exception.__cause__)
            self.assertEqual(
                observed,
                [((before,), 0, 0, None, *before_private)],
            )
            self.assertEqual(len(pending_values), 1)
            self.assertIs(
                abort_reasons[0],
                getattr(
                    controller_module,
                    "PendingEmergencyAbortReasonV1",
                ).LEGACY_PROCESS_ONE_CAPACITY_DENIAL,
            )
            self.assertEqual(controller.snapshot(), before)
            self.assertEqual(probe.append_count, 0)
            self.assertEqual(probe.emergency_append_count, 0)
            self.assertEqual(probe.abort_count, 1)
    def test_clean_close_drains_admitted_work_and_is_idempotent(self) -> None:
        controller = self.create_controller()
        thread, _ = self.enqueue_one()
        first = controller.close()
        thread.join(5)
        self.assertIs(type(first[2]), ExpertSessionTerminalV1)
        self.assertTrue(first[2].clean)
        self.assertEqual(first[1].group_count, 1)
        self.assertEqual(controller.close(), first)
        self.assertEqual(controller.snapshot(), first)
        self.assertIs(
            facade.sample_expert_retention_wall_ns(self.authority).__class__,
            int,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "^expert_controller_unavailable$",
        ):
            controller.process_one(timeout_seconds=0.001)

    def test_close_operator_stop_terminal_root_lifetime_and_poisoned_cleanup(
        self,
    ) -> None:
        with self.fresh_case() as case:
            probe = _RuledStoreProbe(case.collected)
            returned_terminal: list[PersistedEvent] = []
            original_drain = BoundedIngress.drain_one_parent

            def drain(instance, runtime, *, timeout_seconds):
                envelope = original_drain(
                    instance,
                    runtime,
                    timeout_seconds=timeout_seconds,
                )
                if type(envelope) is DurableEvidenceTerminalV1:
                    returned_terminal.append(envelope.terminal)
                return envelope

            def build(writer, *, final_state, final_cursor):
                evidence = returned_terminal[-1]
                return (
                    evidence,
                    _terminal_for(
                        case.manifest,
                        final_cursor,
                        evidence,
                        clean=True,
                        evidence_reason="operator_stop",
                    ),
                )

            def tail(writer, *, published_cursor):
                probe.calls.append("prove_tail")
                probe.tail_count += 1
                self.assertIs(published_cursor, probe.current_cursor)
                self.assertEqual(published_cursor.group_count, 1)
                return None

            probe.build_terminal = build  # type: ignore[method-assign]
            probe.prove_tail = tail  # type: ignore[method-assign]
            with (
                case.mocked_facade(probe),
                mock.patch.object(BoundedIngress, "drain_one_parent", drain),
            ):
                controller = case.create_controller()
                producer, _ = case.enqueue_one()
                closed = controller.close()
                producer.join(5)
                repeated = controller.close()
            self.assertEqual(repeated, closed)
            self.assertIs(type(closed[2]), ExpertSessionTerminalV1)
            self.assertTrue(closed[2].clean)
            self.assertIs(
                closed[2].reason,
                ExpertTerminalReasonV1.OPERATOR_STOP,
            )
            self.assertEqual(closed[1].group_count, 1)
            self.assertEqual(probe.tail_count, 1)
            self.assertEqual(probe.append_count, 1)
            self.assertEqual(probe.ack_count, 1)
            self.assertEqual(probe.terminal_permit_count, 1)
            self.assertEqual(probe.terminal_append_count, 1)
            self.assertIsNotNone(probe.last_terminal_receipt)
            self.assertEqual(
                probe.last_terminal_receipt.session_id,
                case.manifest.session_id,
            )
            self.assertEqual(
                probe.last_terminal_receipt.terminal_sha256,
                closed[2].terminal_sha256,
            )
            self.assertEqual(
                probe.last_terminal_receipt.terminal_frame_sequence,
                closed[1].group_count + 1,
            )
            self.assertIs(
                probe.last_terminal_receipt.reserve_already_consumed,
                True,
            )
            self.assertEqual(probe.abort_count, 0)
            sampled = facade.sample_expert_retention_wall_ns(case.authority)
            self.assertIs(type(sampled), int)

        with self.fresh_case() as case:
            probe = _RuledStoreProbe(case.collected)
            returned_terminal: list[PersistedEvent] = []
            original_drain = BoundedIngress.drain_one_parent

            def drain(instance, runtime, *, timeout_seconds):
                envelope = original_drain(
                    instance,
                    runtime,
                    timeout_seconds=timeout_seconds,
                )
                if type(envelope) is DurableEvidenceTerminalV1:
                    returned_terminal.append(envelope.terminal)
                return envelope

            def build(writer, *, final_state, final_cursor):
                evidence = returned_terminal[-1]
                return (
                    evidence,
                    _terminal_for(
                        case.manifest,
                        final_cursor,
                        evidence,
                        clean=True,
                        evidence_reason="operator_stop",
                    ),
                )

            probe.build_terminal = build  # type: ignore[method-assign]
            probe.terminal_failure = ValueError("terminal_close_uncertain")
            with (
                case.mocked_facade(probe),
                mock.patch.object(BoundedIngress, "drain_one_parent", drain),
            ):
                controller = case.create_controller()
                before = controller.snapshot()
                with self.assertRaisesRegex(
                    RuntimeError,
                    "^expert_consumed_terminal_processing_failed$",
                ):
                    controller.close()
                first_attempts = probe.terminal_append_count
                cleanup = controller.close()
            self.assertEqual(cleanup[:2], before[:2])
            self.assertIsNone(cleanup[2])
            self.assertEqual(first_attempts, 1)
            self.assertEqual(probe.terminal_append_count, 1)
            self.assertEqual(probe.abort_count, 1)

        with self.fresh_case() as case:
            probe = _RuledStoreProbe(case.collected)
            original_drain = BoundedIngress.drain_one_parent
            original_transform = (
                ProviderPersistenceAuthorizer.authorize_transform
            )
            controller_authorization_window = False

            def drain_then_enter_controller(
                instance,
                runtime,
                *,
                timeout_seconds,
            ):
                nonlocal controller_authorization_window
                result = original_drain(
                    instance,
                    runtime,
                    timeout_seconds=timeout_seconds,
                )
                controller_authorization_window = True
                return result

            def deny_controller_transform(instance, raw):
                if controller_authorization_window:
                    raise ExpertLiveAuthorizationDenied()
                return original_transform(instance, raw)

            with case.mocked_facade(probe):
                controller = case.create_controller()
                before = controller.snapshot()
                producer, _ = case.enqueue_one()
                with (
                    mock.patch.object(
                        BoundedIngress,
                        "drain_one_parent",
                        drain_then_enter_controller,
                    ),
                    mock.patch.object(
                        ProviderPersistenceAuthorizer,
                        "authorize_transform",
                        deny_controller_transform,
                    ),
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "^expert_consumed_parent_processing_failed$",
                    ):
                        controller.process_one(timeout_seconds=1.0)
                producer.join(5)
                cleanup = controller.close()
            self.assertEqual(cleanup[:2], before[:2])
            self.assertIsNone(cleanup[2])
            self.assertEqual(probe.terminal_permit_count, 0)
            self.assertEqual(probe.terminal_append_count, 0)
            self.assertEqual(probe.abort_count, 1)

    def test_returned_terminal_uses_one_tail_proof_and_aligned_builder(
        self,
    ) -> None:
        controller = self.create_controller()
        self.ingress.close_inputs()
        calls: list[str] = []
        original_tail = controller_module.prove_expert_live_evidence_tail
        original_build = controller_module.build_aligned_expert_terminal

        def tail(*args, **kwargs):
            calls.append("tail")
            return original_tail(*args, **kwargs)

        def build(*args, **kwargs):
            calls.append("build")
            return original_build(*args, **kwargs)

        with (
            mock.patch.object(
                controller_module,
                "prove_expert_live_evidence_tail",
                tail,
            ),
            mock.patch.object(
                controller_module,
                "build_aligned_expert_terminal",
                build,
            ),
        ):
            state, cursor, terminal = controller.process_one(
                timeout_seconds=1.0
            )
        self.assertEqual(calls, ["tail", "build"])
        self.assertIs(type(terminal), ExpertSessionTerminalV1)
        self.assertTrue(terminal.clean)
        self.assertEqual(terminal.expert_group_count, 0)
        self.assertEqual(terminal.final_expert_state_sha256, expert_state_sha256(state))
        self.assertEqual(terminal.final_expert_trace_sha256, cursor.expert_trace_sha256)

    def test_live_tail_zero_one_two_hidden_raw_and_prior_group_halted_matrix(
        self,
    ) -> None:
        success_cases = (
            ("zero_empty_prefix", 0, False),
            ("zero_nonempty_covered_prefix", 0, True),
            ("one_unseen_raw", 1, False),
        )
        for name, unseen_count, covered_prefix in success_cases:
            with self.subTest(success=name), self.fresh_case() as case:
                controller = case.create_controller()
                writer = _controller_private_writer(controller)
                if covered_prefix:
                    producer, _ = case.enqueue_one()
                    covered = controller.process_one(timeout_seconds=1.0)
                    producer.join(5)
                    self.assertEqual(covered[1].group_count, 1)
                elif unseen_count == 1:
                    case.runtime.ingest(
                        captured(case.persistence_authorizer)
                    )
                before = controller.snapshot()
                evidence_terminal = case.runtime.close_halted(
                    "operator_halt"
                )
                tail_calls = 0
                permit_calls = 0
                append_calls = 0
                acknowledgement_calls = 0
                terminal_calls = 0
                original_tail = (
                    controller_module.prove_expert_live_evidence_tail
                )
                original_issue = (
                    controller_module.issue_expert_append_permit
                )
                original_append = controller_module.append_expert_group
                original_ack = (
                    controller_module.acknowledge_expert_publication
                )
                original_issue_terminal = (
                    controller_module.issue_expert_terminal_permit
                )
                original_append_terminal = (
                    controller_module.append_expert_terminal
                )

                def tail(actual_writer, *, published_cursor):
                    nonlocal tail_calls
                    tail_calls += 1
                    self.assertIs(actual_writer, writer)
                    self.assertIs(published_cursor, before[1])
                    return original_tail(
                        actual_writer,
                        published_cursor=published_cursor,
                    )

                def issue(*args, **kwargs):
                    nonlocal permit_calls
                    permit_calls += 1
                    return original_issue(*args, **kwargs)

                def append(*args, **kwargs):
                    nonlocal append_calls
                    append_calls += 1
                    return original_append(*args, **kwargs)

                def acknowledge(*args, **kwargs):
                    nonlocal acknowledgement_calls
                    acknowledgement_calls += 1
                    return original_ack(*args, **kwargs)

                def issue_terminal(*args, **kwargs):
                    nonlocal terminal_calls
                    terminal_calls += 1
                    return original_issue_terminal(*args, **kwargs)

                def append_terminal(*args, **kwargs):
                    nonlocal terminal_calls
                    terminal_calls += 1
                    return original_append_terminal(*args, **kwargs)

                with (
                    mock.patch.object(
                        BoundedIngress,
                        "drain_one_parent",
                        return_value=evidence_terminal,
                    ),
                    mock.patch.object(
                        BoundedIngress,
                        "close_external_halt",
                        side_effect=AssertionError(
                            "pre-existing terminal forbids bridge"
                        ),
                    ),
                    mock.patch.object(
                        controller_module,
                        "prove_expert_live_evidence_tail",
                        tail,
                    ),
                    mock.patch.object(
                        controller_module,
                        "issue_expert_append_permit",
                        issue,
                    ),
                    mock.patch.object(
                        controller_module,
                        "append_expert_group",
                        append,
                    ),
                    mock.patch.object(
                        controller_module,
                        "acknowledge_expert_publication",
                        acknowledge,
                    ),
                    mock.patch.object(
                        controller_module,
                        "issue_expert_terminal_permit",
                        issue_terminal,
                    ),
                    mock.patch.object(
                        controller_module,
                        "append_expert_terminal",
                        append_terminal,
                    ),
                ):
                    result = controller.process_one(timeout_seconds=1.0)
                self.assertEqual(tail_calls, 1)
                self.assertEqual(permit_calls, unseen_count)
                self.assertEqual(append_calls, unseen_count)
                self.assertEqual(acknowledgement_calls, unseen_count)
                self.assertEqual(terminal_calls, 2)
                self.assertEqual(
                    result[1].group_count,
                    before[1].group_count + unseen_count,
                )
                self.assertIs(type(result[2]), ExpertSessionTerminalV1)

        invalid_tail_cases = (
            "two_unseen_raw",
            "wrong_prior_digest",
            "wrong_parent_order",
            "wrong_parent_time",
            "expert_terminal_already_bound",
            "writer_poisoned",
            "writer_closed",
            "new_process_restart",
            "changed_generation",
            "changed_session",
            "diagnostic_prefix",
        )
        for failure in invalid_tail_cases:
            with self.subTest(failure=failure), self.fresh_case() as case:
                controller = case.create_controller()
                writer = _controller_private_writer(controller)
                writer_state = store_module._WRITERS[writer]
                if failure in {
                    "wrong_prior_digest",
                    "wrong_parent_order",
                    "wrong_parent_time",
                }:
                    producer, _ = case.enqueue_one()
                    controller.process_one(timeout_seconds=1.0)
                    producer.join(5)
                    evidence_terminal = case.runtime.close_halted(
                        "operator_halt"
                    )
                    records = list(
                        case.read_phase1_diagnostic_prefix()
                    )
                    raw_index = next(
                        index
                        for index, item in enumerate(records)
                        if item.record_kind is RecordKind.RAW
                    )
                    raw = records[raw_index]
                    if failure == "wrong_prior_digest":
                        changed_raw = _forged_replace(
                            raw,
                            source_entity_id=(
                                "physically-different-source-entity"
                            ),
                        )
                    elif failure == "wrong_parent_order":
                        changed_raw = _forged_replace(
                            raw,
                            ingest_seq=raw.ingest_seq + 2,
                        )
                    else:
                        changed_raw = _forged_replace(
                            raw,
                            local_wall_ns=raw.local_wall_ns + 1,
                        )
                    records[raw_index] = changed_raw
                    case.rewrite_phase1_records(tuple(records))
                elif failure == "two_unseen_raw":
                    case.runtime.ingest(
                        captured(case.persistence_authorizer)
                    )
                    case.runtime.ingest(
                        captured(case.persistence_authorizer)
                    )
                    evidence_terminal = case.runtime.close_halted(
                        "operator_halt"
                    )
                else:
                    evidence_terminal = case.runtime.close_halted(
                        "operator_halt"
                    )
                    if failure == "expert_terminal_already_bound":
                        unseen = facade.prove_expert_live_evidence_tail(
                            writer,
                            published_cursor=controller.snapshot()[1],
                        )
                        self.assertIsNone(unseen)
                        evidence, terminal = (
                            facade.build_aligned_expert_terminal(
                                writer,
                                final_state=controller.snapshot()[0],
                                final_cursor=controller.snapshot()[1],
                            )
                        )
                        self.assertEqual(evidence, evidence_terminal)
                        facade.issue_expert_terminal_permit(
                            writer,
                            terminal,
                        )
                    elif failure == "writer_poisoned":
                        facade.abort_expert_writer(writer)
                    elif failure == "writer_closed":
                        facade.prove_expert_live_evidence_tail(
                            writer,
                            published_cursor=controller.snapshot()[1],
                        )
                        _, terminal = facade.build_aligned_expert_terminal(
                            writer,
                            final_state=controller.snapshot()[0],
                            final_cursor=controller.snapshot()[1],
                        )
                        permit = facade.issue_expert_terminal_permit(
                            writer,
                            terminal,
                        )
                        facade.append_expert_terminal(permit)
                    elif failure == "changed_generation":
                        writer_state.generation += 1
                    elif failure == "changed_session":
                        writer_state.manifest = _forged_replace(
                            writer_state.manifest,
                            session_id=(
                                "22222222-2222-4222-8222-222222222222"
                            ),
                        )
                    elif failure == "new_process_restart":
                        inherited_cursor = controller.snapshot()[1]
                        read_fd, write_fd = os.pipe()
                        child = os.fork()
                        if child == 0:
                            os.close(read_fd)
                            try:
                                facade.prove_expert_live_evidence_tail(
                                    writer,
                                    published_cursor=inherited_cursor,
                                )
                                result_name = "NO_ERROR"
                            except BaseException as error:
                                result_name = type(error).__name__
                            os.write(write_fd, result_name.encode("ascii"))
                            os.close(write_fd)
                            os._exit(0)
                        os.close(write_fd)
                        child_result = os.read(read_fd, 4096)
                        os.close(read_fd)
                        waited, status = os.waitpid(child, 0)
                        self.assertEqual(waited, child)
                        self.assertEqual(
                            os.waitstatus_to_exitcode(status),
                            0,
                        )
                        self.assertEqual(child_result, b"ValueError")
                        writer_state.owner_pid = child
                    elif failure == "diagnostic_prefix":
                        records = case.read_phase1_diagnostic_prefix()
                        case.rewrite_phase1_records(
                            records,
                            diagnostic_suffix=b"\x00diagnostic-torn-tail",
                        )
                before = controller.snapshot()
                tail_calls = 0
                later_calls: list[str] = []
                abort_calls = 0
                original_tail = (
                    controller_module.prove_expert_live_evidence_tail
                )
                original_abort = controller_module.abort_expert_writer

                def tail(actual_writer, *, published_cursor):
                    nonlocal tail_calls
                    tail_calls += 1
                    self.assertIs(actual_writer, writer)
                    self.assertIs(published_cursor, before[1])
                    return original_tail(
                        actual_writer,
                        published_cursor=published_cursor,
                    )

                def forbidden(name):
                    def call(*args, **kwargs):
                        later_calls.append(name)
                        raise AssertionError(
                            f"tail rejection reached {name}"
                        )

                    return call

                def abort(*args, **kwargs):
                    nonlocal abort_calls
                    abort_calls += 1
                    return original_abort(*args, **kwargs)

                with (
                    mock.patch.object(
                        BoundedIngress,
                        "drain_one_parent",
                        return_value=evidence_terminal,
                    ),
                    mock.patch.object(
                        BoundedIngress,
                        "close_external_halt",
                        side_effect=AssertionError(
                            "pre-existing terminal forbids bridge"
                        ),
                    ),
                    mock.patch.object(
                        controller_module,
                        "prove_expert_live_evidence_tail",
                        tail,
                    ),
                    mock.patch.object(
                        controller_module,
                        "issue_expert_append_permit",
                        forbidden("permit"),
                    ),
                    mock.patch.object(
                        controller_module,
                        "append_expert_group",
                        forbidden("append"),
                    ),
                    mock.patch.object(
                        controller_module,
                        "acknowledge_expert_publication",
                        forbidden("acknowledgement"),
                    ),
                    mock.patch.object(
                        controller_module,
                        "issue_expert_terminal_permit",
                        forbidden("terminal_permit"),
                    ),
                    mock.patch.object(
                        controller_module,
                        "append_expert_terminal",
                        forbidden("terminal_append"),
                    ),
                    mock.patch.object(
                        controller_module,
                        "abort_expert_writer",
                        abort,
                    ),
                ):
                    with self.assertRaises(
                        (ValueError, ExpertLiveAuthorizationDenied)
                    ):
                        controller.process_one(timeout_seconds=1.0)
                self.assertEqual(tail_calls, 1)
                self.assertEqual(later_calls, [])
                self.assertEqual(abort_calls, 1)
                self.assertEqual(controller.snapshot(), before)

        with self.fresh_case() as case:
            prior_halted = replace(
                _expected_genesis(
                    case.manifest,
                    case.universe,
                    case.policy,
                )[0],
                rejected_parent_count=1,
                halted=True,
                halt_reason=ExpertRejectReasonV1.REDUCER_EXCEPTION,
            )
            probe = _RuledStoreProbe(case.collected)

            def build(writer, *, final_state, final_cursor):
                return (
                    evidence_terminal,
                    _terminal_for(
                        case.manifest,
                        final_cursor,
                        evidence_terminal,
                        clean=False,
                        evidence_reason="operator_halt",
                    ),
                )

            probe.build_terminal = build  # type: ignore[method-assign]
            with (
                case.mocked_facade(probe),
                mock.patch.object(
                    controller_module,
                    "initial_expert_state",
                    return_value=prior_halted,
                ),
            ):
                controller = case.create_controller()
                hidden_raw = case.runtime.ingest(
                    captured(case.persistence_authorizer)
                )
                evidence_terminal = case.runtime.close_halted(
                    "operator_halt"
                )
                probe.tail_result = hidden_raw
                with (
                    mock.patch.object(
                        BoundedIngress,
                        "drain_one_parent",
                        return_value=evidence_terminal,
                    ),
                    mock.patch.object(
                        BoundedIngress,
                        "close_external_halt",
                        side_effect=AssertionError(
                            "prior halt catch-up must not reopen admission"
                        ),
                    ),
                ):
                    result = controller.process_one(
                        timeout_seconds=1.0
                    )
            self.assertEqual(probe.tail_count, 1)
            self.assertEqual(probe.append_count, 1)
            self.assertEqual(probe.ack_count, 1)
            self.assertEqual(probe.terminal_append_count, 1)
            self.assertEqual(result[1].group_count, 1)
            self.assertTrue(result[0].halted)
            self.assertIs(
                result[0].halt_reason,
                ExpertRejectReasonV1.REDUCER_EXCEPTION,
            )
            self.assertIsNotNone(probe.group)
            self.assertIsNotNone(probe.payloads)
            rejected = decode_expert_event_payload(
                probe.payloads[0],  # type: ignore[index]
                event_kind=probe.group.records[0].event_kind,  # type: ignore[union-attr]
                event_version=probe.group.records[0].event_version,  # type: ignore[union-attr]
            )
            self.assertIs(
                rejected.reason,
                ExpertRejectReasonV1.PRIOR_GROUP_HALTED,
            )
            self.assertEqual(len(probe.group.records), 1)  # type: ignore[union-attr]

    def test_returned_terminal_tail_and_terminal_receipt_field_matrix(
        self,
    ) -> None:
        terminal_fields = tuple(
            item.name for item in fields(ExpertSessionTerminalV1)
        )
        for field_name in terminal_fields:
            with self.subTest(terminal_field=field_name), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                returned_terminal: list[PersistedEvent] = []
                original_drain = BoundedIngress.drain_one_parent

                def drain(instance, runtime, *, timeout_seconds):
                    envelope = original_drain(
                        instance,
                        runtime,
                        timeout_seconds=timeout_seconds,
                    )
                    if type(envelope) is DurableEvidenceTerminalV1:
                        returned_terminal.append(envelope.terminal)
                    return envelope

                def build(writer, *, final_state, final_cursor):
                    evidence = returned_terminal[-1]
                    terminal = _terminal_for(
                        case.manifest,
                        final_cursor,
                        evidence,
                        clean=True,
                        evidence_reason="session_end",
                    )
                    current = getattr(terminal, field_name)
                    if type(current) is bool:
                        replacement = not current
                    elif type(current) is int:
                        replacement = current + 1
                    elif type(current) is str:
                        replacement = (
                            "22222222-2222-4222-8222-222222222222"
                            if field_name == "session_id"
                            else (
                                "operator_stop"
                                if field_name == "evidence_terminal_reason"
                                else "d" * 64
                            )
                        )
                    elif type(current) is ExpertTerminalReasonV1:
                        replacement = ExpertTerminalReasonV1.OPERATOR_STOP
                    else:
                        raise AssertionError(field_name)
                    return (
                        evidence,
                        _forged_replace(
                            terminal,
                            **{field_name: replacement},
                        ),
                    )

                probe.build_terminal = build  # type: ignore[method-assign]
                with (
                    case.mocked_facade(probe),
                    mock.patch.object(
                        BoundedIngress,
                        "drain_one_parent",
                        drain,
                    ),
                ):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    case.ingress.close_inputs()
                    with self.assertRaises((TypeError, ValueError)):
                        controller.process_one(timeout_seconds=1.0)
                    published = controller.snapshot()
                self.assertEqual(published[:2], before[:2])
                self.assertIsNone(published[2])
                self.assertEqual(probe.terminal_permit_count, 0)
                self.assertEqual(probe.terminal_append_count, 0)
                self.assertEqual(probe.abort_count, 1)

        receipt_fields = tuple(
            item.name for item in fields(DurableExpertTerminalReceiptV1)
        )
        for field_name in receipt_fields:
            with self.subTest(receipt_field=field_name), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                returned_terminal: list[PersistedEvent] = []
                original_drain = BoundedIngress.drain_one_parent

                def drain(instance, runtime, *, timeout_seconds):
                    envelope = original_drain(
                        instance,
                        runtime,
                        timeout_seconds=timeout_seconds,
                    )
                    if type(envelope) is DurableEvidenceTerminalV1:
                        returned_terminal.append(envelope.terminal)
                    return envelope

                def build(writer, *, final_state, final_cursor):
                    evidence = returned_terminal[-1]
                    return (
                        evidence,
                        _terminal_for(
                            case.manifest,
                            final_cursor,
                            evidence,
                            clean=True,
                            evidence_reason="session_end",
                        ),
                    )

                def mutate(receipt, *, _field=field_name):
                    current = getattr(receipt, _field)
                    if type(current) is bool:
                        value = False
                        return _forged_replace(
                            receipt,
                            **{_field: value},
                        )
                    if type(current) is int:
                        value = current + 1
                    elif _field == "session_id":
                        value = "22222222-2222-4222-8222-222222222222"
                    else:
                        value = "e" * 64
                    return replace(receipt, **{_field: value})

                probe.build_terminal = build  # type: ignore[method-assign]
                probe.terminal_receipt_mutator = mutate
                with (
                    case.mocked_facade(probe),
                    mock.patch.object(
                        BoundedIngress,
                        "drain_one_parent",
                        drain,
                    ),
                ):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    case.ingress.close_inputs()
                    with self.assertRaises(ValueError):
                        controller.process_one(timeout_seconds=1.0)
                    published = controller.snapshot()
                self.assertEqual(published[:2], before[:2])
                self.assertIsNone(published[2])
                self.assertEqual(probe.terminal_permit_count, 1)
                self.assertEqual(probe.terminal_append_count, 1)
                self.assertEqual(probe.abort_count, 1)

        with self.fresh_case() as case:
            probe = _RuledStoreProbe(case.collected)
            returned_terminal: list[PersistedEvent] = []
            original_drain = BoundedIngress.drain_one_parent

            def drain(instance, runtime, *, timeout_seconds):
                envelope = original_drain(
                    instance,
                    runtime,
                    timeout_seconds=timeout_seconds,
                )
                if type(envelope) is DurableEvidenceTerminalV1:
                    returned_terminal.append(envelope.terminal)
                return envelope

            def build(writer, *, final_state, final_cursor):
                probe.calls.append("build_terminal")
                probe.build_terminal_count += 1
                evidence = returned_terminal[-1]
                return (
                    evidence,
                    _terminal_for(
                        case.manifest,
                        final_cursor,
                        evidence,
                        clean=True,
                        evidence_reason="session_end",
                    ),
                )

            probe.build_terminal = build  # type: ignore[method-assign]
            with (
                case.mocked_facade(probe),
                mock.patch.object(BoundedIngress, "drain_one_parent", drain),
                mock.patch.object(
                    BoundedIngress,
                    "close_external_halt",
                    side_effect=AssertionError(
                        "pre-existing Phase-1 terminal forbids bridge"
                    ),
                ),
            ):
                controller = case.create_controller()
                case.ingress.close_inputs()
                result = controller.process_one(timeout_seconds=1.0)
            self.assertIs(type(result[2]), ExpertSessionTerminalV1)
            self.assertTrue(result[2].clean)
            self.assertIs(
                result[2].reason,
                ExpertTerminalReasonV1.SESSION_END,
            )
            self.assertIs(result[2], probe.terminal)
            self.assertEqual(probe.tail_count, 1)
            self.assertEqual(probe.build_terminal_count, 1)
            self.assertEqual(probe.terminal_permit_count, 1)
            self.assertEqual(probe.terminal_append_count, 1)
            self.assertIsNotNone(probe.last_terminal_receipt)
            terminal_receipt = probe.last_terminal_receipt
            self.assertEqual(
                terminal_receipt.session_id,
                case.manifest.session_id,
            )
            self.assertEqual(
                terminal_receipt.terminal_sha256,
                result[2].terminal_sha256,
            )
            self.assertEqual(
                terminal_receipt.terminal_frame_sequence,
                result[1].group_count + 1,
            )
            self.assertEqual(
                terminal_receipt.durable_end_offset,
                EXPERT_FILE_HEADER_BYTES
                + len(encode_expert_manifest_frame(case.manifest))
                + len(
                    encode_expert_terminal_frame(
                        result[2],
                        final_cursor=result[1],
                    )
                ),
            )
            self.assertIs(
                terminal_receipt.reserve_already_consumed,
                True,
            )

    def test_prewrite_capacity_uses_only_combined_emergency_path(self) -> None:
        controller = self.create_controller()
        before = controller.snapshot()
        writer = _controller_private_writer(controller)
        writer_state = store_module._WRITERS[writer]
        journal_fd = writer_state.journal_fd
        producer, producer_result = self.enqueue_one()
        expert_capacity_probes = 0
        pending_values: list[object] = []
        abort_reasons: list[object] = []
        original_fstatvfs = store_module.os.fstatvfs
        original_abort_pending = (
            ExpertControllerV1
            .abort_pending_durable_companion_emergency_v1
        )

        def controlled_fstatvfs(descriptor):
            nonlocal expert_capacity_probes
            if descriptor != journal_fd:
                return original_fstatvfs(descriptor)
            expert_capacity_probes += 1
            if expert_capacity_probes == 1:
                return mock.Mock(f_bavail=0, f_frsize=1)
            return original_fstatvfs(descriptor)

        def record_abort(instance, pending, *, reason):
            pending_values.append(pending)
            abort_reasons.append(reason)
            return original_abort_pending(
                instance,
                pending,
                reason=reason,
            )

        error_type = getattr(controller_module, "ExpertCapacityExceeded")
        with (
            mock.patch.object(
                store_module.os,
                "fstatvfs",
                controlled_fstatvfs,
            ),
            mock.patch.object(
                ExpertControllerV1,
                "abort_pending_durable_companion_emergency_v1",
                new=record_abort,
            ),
            mock.patch.object(
                BoundedIngress,
                "close_external_halt",
                side_effect=AssertionError(
                    "legacy capacity denial must not bridge ingress"
                ),
            ),
            mock.patch.object(
                controller_module,
                "issue_expert_terminal_permit",
                side_effect=AssertionError(
                    "legacy capacity denial must not issue terminal"
                ),
            ),
            mock.patch.object(
                controller_module,
                "append_expert_group",
                side_effect=AssertionError(
                    "capacity-denied ordinary group must not append"
                ),
            ),
            mock.patch.object(
                controller_module,
                "issue_expert_emergency_append_permit",
                side_effect=AssertionError(
                    "legacy process_one must not issue emergency permit"
                ),
            ),
            mock.patch.object(
                controller_module,
                "append_expert_emergency_group_and_terminal",
                side_effect=AssertionError(
                    "legacy process_one must not append emergency frames"
                ),
            ),
            self.assertRaises(error_type) as raised,
        ):
            controller.process_one(timeout_seconds=1.0)
        producer.join(5)
        self.assertFalse(producer.is_alive())
        self.assertNotIsInstance(producer_result[0], BaseException)
        self.assertEqual(
            raised.exception.args,
            ("expert_legacy_process_one_capacity_denied",),
        )
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(expert_capacity_probes, 1)
        self.assertIs(writer_state.prewrite_capacity_denied, True)
        self.assertEqual(len(pending_values), 1)
        reason = getattr(
            controller_module,
            "PendingEmergencyAbortReasonV1",
        ).LEGACY_PROCESS_ONE_CAPACITY_DENIAL
        self.assertEqual(abort_reasons, [reason])
        tombstone = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        ).lookup(pending_values[0])
        self.assertEqual(tombstone.lifecycle, "ABORTED_NONPUBLICATION")
        self.assertIs(tombstone.retained_receipt.abort_reason, reason)
        self.assertEqual(controller.snapshot(), before)
        with self.assertRaisesRegex(
            RuntimeError,
            "^expert_controller_unavailable$",
        ):
            controller.process_one(timeout_seconds=0.001)
    def test_halt_bridge_detaches_queued_producers_without_extra_raw_or_lock_inversion(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        probe.prewrite_capacity_error = ExpertPrewriteCapacityError(
            requested_bytes=4096,
            available_bytes=1024,
            emergency_reserve_bytes=EXPERT_EMERGENCY_RESERVE_BYTES,
        )
        with (
            self.mocked_facade(probe),
            mock.patch.object(
                BoundedIngress,
                "close_external_halt",
                side_effect=AssertionError(
                    "legacy capacity denial must not bridge ingress"
                ),
            ),
        ):
            controller = self.create_controller()
            before = controller.snapshot()
            first, first_result = self.enqueue_one(1)
            second, second_result = self.enqueue_one(2)
            queued_nodes = tuple(self.ingress._queue.queue)
            self.assertEqual(len(queued_nodes), 2)
            second_node = queued_nodes[1]
            node_lock_held = threading.Event()
            release_node_lock = threading.Event()
            detached_nodes_ready = threading.Event()
            runtime_failure_released_condition = threading.Event()
            settled_nodes: list[object] = []
            holder_errors: list[BaseException] = []
            observer_errors: list[BaseException] = []
            original_settle_failed_node = (
                BoundedIngress._settle_failed_node
            )

            def hold_timeout_completion_lock() -> None:
                try:
                    with second_node.completion_lock:
                        node_lock_held.set()
                        if not release_node_lock.wait(5):
                            raise AssertionError(
                                "observer did not release node lock"
                            )
                except BaseException as error:
                    holder_errors.append(error)

            def observe_detachment_without_lock_inversion() -> None:
                try:
                    if not node_lock_held.wait(5):
                        raise AssertionError(
                            "node completion lock was not held"
                        )
                    if not detached_nodes_ready.wait(5):
                        raise AssertionError(
                            "runtime failure did not detach queued node"
                        )
                    with self.ingress._condition:
                        self.assertEqual(self.ingress._queue.qsize(), 0)
                        self.assertTrue(self.ingress._runtime_failed)
                        runtime_failure_released_condition.set()
                except BaseException as error:
                    observer_errors.append(error)
                finally:
                    release_node_lock.set()

            def settle_failed_node(instance, node) -> None:
                settled_nodes.append(node)
                if node is second_node:
                    detached_nodes_ready.set()
                return original_settle_failed_node(instance, node)

            holder = threading.Thread(target=hold_timeout_completion_lock)
            observer = threading.Thread(
                target=observe_detachment_without_lock_inversion
            )
            holder.start()
            self.assertTrue(node_lock_held.wait(5))
            observer.start()
            error_type = getattr(
                controller_module,
                "ExpertCapacityExceeded",
            )
            with mock.patch.object(
                BoundedIngress,
                "_settle_failed_node",
                settle_failed_node,
            ):
                with self.assertRaises(error_type) as raised:
                    controller.process_one(timeout_seconds=1.0)
                active_node = self.ingress._active_node
                self.assertIsNone(active_node)
                self.assertEqual(self.ingress._queue.qsize(), 1)
                self.ingress._runtime_failure(active_node)
            holder.join(5)
            observer.join(5)
        first.join(5)
        second.join(5)
        self.assertEqual(
            raised.exception.args,
            ("expert_legacy_process_one_capacity_denied",),
        )
        self.assertIsNone(raised.exception.__cause__)
        self.assertFalse(holder.is_alive())
        self.assertFalse(observer.is_alive())
        self.assertEqual(holder_errors, [])
        self.assertEqual(observer_errors, [])
        self.assertEqual(settled_nodes, [second_node])
        self.assertTrue(runtime_failure_released_condition.is_set())
        self.assertNotIsInstance(first_result[0], BaseException)
        self.assertIsInstance(second_result[0], IngressClosed)
        self.assertEqual(probe.append_permit_attempts, 1)
        self.assertEqual(probe.ordinary_cas_count, 0)
        self.assertEqual(probe.emergency_cas_count, 0)
        self.assertEqual(probe.cas_count, 0)
        self.assertEqual(probe.append_count, 0)
        self.assertEqual(probe.emergency_append_count, 0)
        self.assertEqual(probe.abort_count, 1)
        self.assertEqual(controller.snapshot(), before)
        self.assertEqual(
            self.phase1_writer.latest_raw.ingest_seq // 2,
            1,
        )
        for operation in (
            lambda: self.ingress.enqueue(
                IngressItem(
                    producer_id="late",
                    producer_sequence=3,
                    captured=captured(self.persistence_authorizer),
                )
            ),
            lambda: self.ingress.drain_one(
                self.runtime,
                timeout_seconds=0.001,
            ),
            lambda: self.ingress.close_external_halt(self.runtime),
        ):
            with self.assertRaises(IngressClosed):
                operation()
        self.assertIsNone(self.ingress.close_inputs())
        self.assertEqual(controller.close(), before)
    def test_prewrite_capacity_emergency_proves_no_ordinary_cas_and_validates_nested_receipts(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        probe.prewrite_capacity_error = ExpertPrewriteCapacityError(
            requested_bytes=4096,
            available_bytes=1024,
            emergency_reserve_bytes=EXPERT_EMERGENCY_RESERVE_BYTES,
        )
        prewrite_observations: list[
            tuple[int, int, int, ExpertJournalCursorV1 | None]
        ] = []
        pending_values: list[object] = []
        abort_reasons: list[object] = []
        original_abort_pending = (
            ExpertControllerV1
            .abort_pending_durable_companion_emergency_v1
        )

        def record_abort(instance, pending, *, reason):
            pending_values.append(pending)
            abort_reasons.append(reason)
            return original_abort_pending(
                instance,
                pending,
                reason=reason,
            )

        probe.prewrite_capacity_pause = lambda: prewrite_observations.append(
            (
                probe.ordinary_cas_count,
                probe.append_count,
                probe.ack_count,
                probe.initial_cursor,
            )
        )
        error_type = getattr(controller_module, "ExpertCapacityExceeded")
        with (
            self.mocked_facade(probe),
            mock.patch.object(
                ExpertControllerV1,
                "abort_pending_durable_companion_emergency_v1",
                new=record_abort,
            ),
            mock.patch.object(
                BoundedIngress,
                "close_external_halt",
                side_effect=AssertionError(
                    "legacy capacity denial must not bridge ingress"
                ),
            ),
            mock.patch.object(
                controller_module,
                "issue_expert_emergency_append_permit",
                side_effect=AssertionError(
                    "legacy process_one must not issue emergency permit"
                ),
            ),
            mock.patch.object(
                controller_module,
                "append_expert_emergency_group_and_terminal",
                side_effect=AssertionError(
                    "legacy process_one must not append emergency receipt"
                ),
            ),
        ):
            controller = self.create_controller()
            before = controller.snapshot()
            producer, producer_result = self.enqueue_one()
            with self.assertRaises(error_type) as raised:
                controller.process_one(timeout_seconds=1.0)
        producer.join(5)
        self.assertFalse(producer.is_alive())
        self.assertNotIsInstance(producer_result[0], BaseException)
        self.assertEqual(
            raised.exception.args,
            ("expert_legacy_process_one_capacity_denied",),
        )
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(probe.append_permit_attempts, 1)
        self.assertEqual(
            prewrite_observations,
            [(0, 0, 0, before[1])],
        )
        self.assertEqual(probe.ordinary_cas_count, 0)
        self.assertEqual(probe.emergency_cas_count, 0)
        self.assertEqual(probe.cas_count, 0)
        self.assertEqual(probe.append_count, 0)
        self.assertEqual(probe.ack_count, 0)
        self.assertEqual(probe.emergency_permit_count, 0)
        self.assertEqual(probe.emergency_append_count, 0)
        self.assertEqual(probe.tail_count, 0)
        self.assertEqual(probe.build_terminal_count, 0)
        self.assertIsNone(probe.last_emergency_receipt)
        self.assertEqual(len(pending_values), 1)
        reason = getattr(
            controller_module,
            "PendingEmergencyAbortReasonV1",
        ).LEGACY_PROCESS_ONE_CAPACITY_DENIAL
        self.assertEqual(abort_reasons, [reason])
        tombstone = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        ).lookup(pending_values[0])
        self.assertEqual(tombstone.lifecycle, "ABORTED_NONPUBLICATION")
        self.assertIs(tombstone.retained_receipt.abort_reason, reason)
        self.assertEqual(controller.snapshot(), before)
        self.assertEqual(probe.abort_count, 1)
    def test_emergency_group_fsync_terminal_seam_failure_matrix(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        probe.prewrite_capacity_error = ExpertPrewriteCapacityError(
            requested_bytes=4096,
            available_bytes=1024,
            emergency_reserve_bytes=EXPERT_EMERGENCY_RESERVE_BYTES,
        )
        forbidden_calls: list[str] = []
        pending_values: list[object] = []
        abort_reasons: list[object] = []
        original_abort_pending = (
            ExpertControllerV1
            .abort_pending_durable_companion_emergency_v1
        )

        def forbidden(name):
            def call(*_args, **_kwargs):
                forbidden_calls.append(name)
                raise AssertionError(
                    f"legacy capacity denial reached {name}"
                )

            return call

        def record_abort(instance, pending, *, reason):
            pending_values.append(pending)
            abort_reasons.append(reason)
            return original_abort_pending(
                instance,
                pending,
                reason=reason,
            )

        error_type = getattr(controller_module, "ExpertCapacityExceeded")
        with self.mocked_facade(probe):
            controller = self.create_controller()
            before = controller.snapshot()
            producer, producer_result = self.enqueue_one()
            with (
                mock.patch.object(
                    ExpertControllerV1,
                    "abort_pending_durable_companion_emergency_v1",
                    new=record_abort,
                ),
                mock.patch.object(
                    BoundedIngress,
                    "close_external_halt",
                    new=forbidden("halt_bridge"),
                ),
                mock.patch.object(
                    controller_module,
                    "issue_expert_emergency_append_permit",
                    new=forbidden("emergency_permit"),
                ),
                mock.patch.object(
                    controller_module,
                    "append_expert_emergency_group_and_terminal",
                    new=forbidden("emergency_append"),
                ),
                mock.patch.object(
                    controller_module,
                    "issue_expert_terminal_permit",
                    new=forbidden("terminal_permit"),
                ),
                mock.patch.object(
                    controller_module,
                    "append_expert_terminal",
                    new=forbidden("terminal_append"),
                ),
                mock.patch.object(
                    store_module,
                    "_release_reserve",
                    new=forbidden("reserve_release"),
                ),
                mock.patch.object(
                    store_module,
                    "_complete_write",
                    new=forbidden("physical_write"),
                ),
                self.assertRaises(error_type) as raised,
            ):
                controller.process_one(timeout_seconds=1.0)
        producer.join(5)
        self.assertFalse(producer.is_alive())
        self.assertNotIsInstance(producer_result[0], BaseException)
        self.assertEqual(
            raised.exception.args,
            ("expert_legacy_process_one_capacity_denied",),
        )
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(forbidden_calls, [])
        self.assertEqual(len(pending_values), 1)
        reason = getattr(
            controller_module,
            "PendingEmergencyAbortReasonV1",
        ).LEGACY_PROCESS_ONE_CAPACITY_DENIAL
        self.assertEqual(abort_reasons, [reason])
        tombstone = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        ).lookup(pending_values[0])
        self.assertEqual(tombstone.lifecycle, "ABORTED_NONPUBLICATION")
        self.assertIs(tombstone.retained_receipt.abort_reason, reason)
        self.assertEqual(controller.snapshot(), before)
        self.assertEqual(probe.append_permit_attempts, 1)
        self.assertEqual(probe.append_count, 0)
        self.assertEqual(probe.emergency_append_count, 0)
        self.assertEqual(probe.terminal_append_count, 0)
        self.assertEqual(probe.abort_count, 1)
    def test_wrong_thread_rejects_snapshot_process_and_close(self) -> None:
        controller = self.create_controller()
        outcomes: list[BaseException] = []

        def misuse() -> None:
            for operation in (
                controller.snapshot,
                lambda: controller.process_one(timeout_seconds=0.001),
                controller.close,
            ):
                try:
                    operation()
                except BaseException as error:
                    outcomes.append(error)

        thread = threading.Thread(target=misuse)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 3)
        self.assertTrue(
            all(type(error) is WrongOwnerThread for error in outcomes)
        )
        self.assertEqual(
            controller.snapshot(),
            (*_expected_genesis(self.manifest, self.universe, self.policy), None),
        )

    @unittest.skipUnless(hasattr(os, "fork"), "requires process fork")
    def test_postfork_snapshot_process_and_close_reject_before_io(self) -> None:
        controller = self.create_controller()
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            names = []
            for operation in (
                controller.snapshot,
                lambda: controller.process_one(timeout_seconds=0.001),
                controller.close,
            ):
                try:
                    operation()
                    names.append("NO_ERROR")
                except BaseException as error:
                    names.append(type(error).__name__)
            os.write(write_fd, ",".join(names).encode("ascii"))
            os.close(write_fd)
            os._exit(0)
        os.close(write_fd)
        result = os.read(read_fd, 4096)
        os.close(read_fd)
        _, status = os.waitpid(child, 0)
        self.assertEqual(status, 0)
        self.assertEqual(
            result.decode("ascii"),
            "WrongOwnerThread,WrongOwnerThread,WrongOwnerThread",
        )
        self.assertEqual(
            controller.snapshot(),
            (*_expected_genesis(self.manifest, self.universe, self.policy), None),
        )

    @unittest.skipUnless(hasattr(os, "fork"), "requires process fork")
    def test_crash_after_raw_or_group_fsync_is_diagnostic_nonresumable_repeated_three_times(
        self,
    ) -> None:
        seams = (
            "raw_fsync",
            "group_fsync_before_receipt",
            "receipt_before_ack",
        )
        observed: list[tuple[str, int, str, int]] = []

        def read_exact(descriptor: int, length: int) -> bytes:
            content = bytearray()
            while len(content) != length:
                chunk = os.read(descriptor, length - len(content))
                if not chunk:
                    raise AssertionError("crash metadata pipe closed")
                content.extend(chunk)
            return bytes(content)

        for seam in seams:
            for repetition in range(3):
                with self.subTest(seam=seam, repetition=repetition):
                    fixture = phase1_event_tests.SessionContractTests(
                        "test_session_manifest_requires_verified_eligible_matching_inputs"
                    )
                    fixture.setUp()
                    restart_coordinator = None
                    try:
                        phase1_manifest = fixture.build(
                            code_sha256=_live_phase1_code_sha256()
                        )
                        self.assertEqual(
                            phase1_manifest.code_sha256,
                            _live_phase1_code_sha256(),
                        )
                        state_root = str(fixture.config.state_root.resolve())
                        ready_read, ready_write = os.pipe()
                        release_read, release_write = os.pipe()
                        metadata_read, metadata_write = os.pipe()
                        child = os.fork()
                        if child == 0:
                            os.close(ready_read)
                            os.close(release_write)
                            os.close(metadata_read)
                            try:
                                coordinator = RetentionCoordinator.acquire(
                                    fixture.config,
                                    clock_ns=lambda: (
                                        phase1_manifest.created_wall_ns
                                    ),
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
                                            session_manifest=phase1_manifest,
                                        )
                                    )
                                    root_request = (
                                        coordinator
                                        .issue_expert_state_root_account_lock_request()
                                    )
                                    authority = (
                                        facade.acquire_expert_journal_root(
                                            root_request
                                        )
                                    )
                                    write_capability = (
                                        coordinator.arm_before_wal(
                                            session_manifest=phase1_manifest,
                                            decision=(
                                                authorizer.bound_decision
                                            ),
                                            persistence_authorizer=authorizer,
                                        )
                                    )
                                    phase1_writer = JournalWriter.create(
                                        write_capability=write_capability,
                                        session_manifest=phase1_manifest,
                                    )
                                    runtime = EventRuntime(
                                        writer=phase1_writer,
                                        state=initial_state(
                                            phase1_manifest.session_id
                                        ),
                                        persistence_authorizer=authorizer,
                                        coordinator=coordinator,
                                    )
                                    ingress = BoundedIngress(
                                        capacity=4,
                                        producer_timeout_seconds=2.0,
                                        receipt_timeout_seconds=5.0,
                                    )
                                    environment_authority = (
                                        facade
                                        .issue_expert_environment_collection_authority(
                                            authority,
                                            persistence_authorizer=authorizer,
                                            coordinator=coordinator,
                                        )
                                    )
                                    collected = (
                                        facade.collect_expert_current_environment(
                                            environment_authority
                                        )
                                    )
                                    universe, policy, manifest = (
                                        _expert_manifest_for(
                                            phase1_manifest=phase1_manifest,
                                            session_start=(
                                                phase1_writer.session_start
                                            ),
                                            persistence_authorizer=authorizer,
                                            collected=collected,
                                        )
                                    )
                                    controller = create_expert_controller(
                                        authority=authority,
                                        manifest=manifest,
                                        universe=universe,
                                        policy=policy,
                                        ingress=ingress,
                                        runtime=runtime,
                                        persistence_authorizer=authorizer,
                                        coordinator=coordinator,
                                    )
                                    writer = _controller_private_writer(
                                        controller
                                    )
                                    writer_state = (
                                        store_module._WRITERS[writer]
                                    )
                                    initial_cursor = controller.snapshot()[1]
                                    metadata = pickle.dumps(
                                        (
                                            manifest,
                                            initial_cursor,
                                            os.fstat(
                                                writer_state.journal_fd
                                            ).st_size,
                                        ),
                                        protocol=5,
                                    )
                                    os.write(
                                        metadata_write,
                                        len(metadata).to_bytes(8, "big"),
                                    )
                                    offset = 0
                                    while offset != len(metadata):
                                        written = os.write(
                                            metadata_write,
                                            metadata[offset:],
                                        )
                                        if written < 1:
                                            raise OSError(
                                                "metadata_write_short"
                                            )
                                        offset += written
                                    os.close(metadata_write)

                                    def crash_barrier() -> None:
                                        os.write(ready_write, b"R")
                                        os.read(release_read, 1)

                                    original_fsync = (
                                        retention_module.os.fsync
                                    )
                                    original_append = (
                                        controller_module.append_expert_group
                                    )
                                    wal_fd = (
                                        coordinator._session_states[  # type: ignore[attr-defined]
                                            phase1_manifest.session_id
                                        ].wal_fd
                                    )
                                    expert_fd = writer_state.journal_fd
                                    raw_fired = False
                                    group_fired = False

                                    def gated_fsync(descriptor):
                                        nonlocal raw_fired, group_fired
                                        result = original_fsync(descriptor)
                                        if (
                                            seam == "raw_fsync"
                                            and descriptor == wal_fd
                                            and not raw_fired
                                        ):
                                            raw_fired = True
                                            crash_barrier()
                                        if (
                                            seam
                                            == "group_fsync_before_receipt"
                                            and descriptor == expert_fd
                                            and not group_fired
                                        ):
                                            group_fired = True
                                            crash_barrier()
                                        return result

                                    def receipt_barrier(*args, **kwargs):
                                        receipt = original_append(
                                            *args,
                                            **kwargs,
                                        )
                                        if seam == "receipt_before_ack":
                                            crash_barrier()
                                        return receipt

                                    entered_queue = threading.Event()
                                    original_put = ingress._queue.put  # type: ignore[attr-defined]

                                    def instrumented_put(
                                        node,
                                        *args,
                                        **kwargs,
                                    ):
                                        result = original_put(
                                            node,
                                            *args,
                                            **kwargs,
                                        )
                                        entered_queue.set()
                                        return result

                                    ingress._queue.put = instrumented_put  # type: ignore[attr-defined]

                                    def produce() -> None:
                                        ingress.enqueue(
                                            IngressItem(
                                                producer_id="crash-test",
                                                producer_sequence=1,
                                                captured=captured(authorizer),
                                            )
                                        )

                                    producer = threading.Thread(target=produce)
                                    producer.start()
                                    if not entered_queue.wait(5):
                                        raise AssertionError(
                                            "producer did not queue"
                                        )
                                    with (
                                        mock.patch.object(
                                            retention_module.os,
                                            "fsync",
                                            gated_fsync,
                                        ),
                                        mock.patch.object(
                                            controller_module,
                                            "append_expert_group",
                                            receipt_barrier,
                                        ),
                                    ):
                                        controller.process_one(
                                            timeout_seconds=1.0
                                        )
                                    raise AssertionError(
                                        "crash barrier returned"
                                    )
                            except BaseException:
                                try:
                                    os.write(ready_write, b"E")
                                except BaseException:
                                    pass
                            finally:
                                for descriptor in (
                                    metadata_write,
                                    ready_write,
                                    release_read,
                                ):
                                    try:
                                        os.close(descriptor)
                                    except OSError:
                                        pass
                            os._exit(2)

                        os.close(ready_write)
                        os.close(release_read)
                        os.close(metadata_write)
                        metadata_length = int.from_bytes(
                            read_exact(metadata_read, 8),
                            "big",
                        )
                        (
                            expert_manifest,
                            initial_cursor,
                            initial_expert_size,
                        ) = pickle.loads(
                            read_exact(metadata_read, metadata_length)
                        )
                        os.close(metadata_read)
                        marker = os.read(ready_read, 1)
                        os.close(ready_read)
                        self.assertEqual(
                            marker,
                            b"R",
                            (
                                f"{seam} repetition {repetition} "
                                "missed completed durability barrier"
                            ),
                        )
                        os.kill(child, signal.SIGKILL)
                        waited, status = os.waitpid(child, 0)
                        os.close(release_write)
                        self.assertEqual(waited, child)
                        self.assertTrue(os.WIFSIGNALED(status))
                        self.assertEqual(
                            os.WTERMSIG(status),
                            signal.SIGKILL,
                        )

                        journal_path = (
                            Path(state_root)
                            / "expert-v1"
                            / "sessions"
                            / store_module._journal_basename(
                                phase1_manifest.session_id
                            )
                        )
                        self.assertTrue(journal_path.is_file())
                        crashed_size = journal_path.stat().st_size
                        if seam == "raw_fsync":
                            self.assertEqual(
                                crashed_size,
                                initial_expert_size,
                            )
                        else:
                            self.assertGreater(
                                crashed_size,
                                initial_expert_size,
                            )

                        restart_coordinator = RetentionCoordinator.acquire(
                            fixture.config,
                            clock_ns=lambda: (
                                phase1_manifest.created_wall_ns
                            ),
                        )
                        restart_coordinator.recover_and_purge()
                        root_request = (
                            restart_coordinator
                            .issue_expert_state_root_account_lock_request()
                        )
                        restart_authority = (
                            facade.acquire_expert_journal_root(root_request)
                        )
                        reader = facade.issue_expert_read_capability(
                            restart_authority,
                            expert_manifest,
                        )
                        try:
                            self.assertEqual(
                                facade.read_expert_manifest(reader),
                                expert_manifest,
                            )
                            groups = []
                            while True:
                                group = facade.read_next_expert_group(reader)
                                if group is None:
                                    break
                                groups.append(group)
                            terminal, _ = (
                                facade.read_expert_terminal_and_summary(
                                    reader
                                )
                            )
                        finally:
                            facade.close_expert_reader(reader)
                        self.assertEqual(
                            len(groups),
                            0 if seam == "raw_fsync" else 1,
                        )
                        self.assertIsNone(terminal)

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
                            restart_gate = ProviderGate(
                                fixture.config,
                                fixture.provider_manifest,
                                fixture.request,
                                environ={
                                    "SYNTHETIC_API_KEY": "fixture-secret"
                                },
                                clock=lambda: fixture.now,
                            )
                            restart_authorizer = (
                                bind_provider_persistence_authorizer(
                                    gate=restart_gate,
                                    coordinator=restart_coordinator,
                                    session_manifest=phase1_manifest,
                                )
                            )
                            with self.assertRaises(
                                (ValueError, ExpertLiveAuthorizationDenied)
                            ):
                                facade.create_expert_journal(
                                    restart_authority,
                                    expert_manifest,
                                    initial_cursor,
                                    persistence_authorizer=(
                                        restart_authorizer
                                    ),
                                    coordinator=restart_coordinator,
                                )

                        report = (
                            facade.recover_and_purge_expert_journals(
                                restart_authority
                            )
                        )
                        classified = tuple(
                            item
                            for field in fields(type(report))
                            for item in (
                                getattr(report, field.name)
                                if type(getattr(report, field.name)) is tuple
                                else ()
                            )
                            if type(item) is str
                        )
                        self.assertIn(
                            phase1_manifest.session_id,
                            classified,
                        )
                        marker_basename = store_module._marker_basename(
                            phase1_manifest.session_id
                        )
                        marker_path = (
                            Path(state_root)
                            / "expert-v1"
                            / "markers"
                            / marker_basename
                        )
                        self.assertFalse(marker_path.exists())
                        with self.assertRaisesRegex(
                            ValueError,
                            "^expert_reader_manifest_invalid$",
                        ):
                            facade.issue_expert_read_capability(
                                restart_authority,
                                expert_manifest,
                            )
                        observed.append(
                            (seam, repetition, state_root, status)
                        )
                    finally:
                        if restart_coordinator is not None:
                            restart_coordinator.close()
                        fixture.tearDown()

        self.assertEqual(len(observed), 9)
        self.assertEqual(
            {(seam, repetition) for seam, repetition, _, _ in observed},
            {
                (seam, repetition)
                for seam in seams
                for repetition in range(3)
            },
        )
        self.assertTrue(callable(facade.recover_and_purge_expert_journals))
        self.assertTrue(callable(facade.purge_expert_session))
        self.assertEqual(
            [
                name
                for name in dir(controller_module)
                if any(
                    fragment in name
                    for fragment in (
                        "adopt",
                        "diagnostic_scan",
                        "purge",
                        "recover",
                        "resume",
                    )
                )
            ],
            [],
        )
        self.assertTrue(
            {
                "writer",
                "cursor",
                "replay",
                "diagnostic_prefix",
            }.isdisjoint(
                inspect.signature(create_expert_controller).parameters
            )
        )


class Round19ControllerContractRedTests(unittest.TestCase):
    """Fast structural RED gate for the Round-19 controller contract."""

    _PUBLIC_SLOTS = {
        "ExpertControllerIdentityV1": (
            "__weakref__",
            "schema_version",
            "session_id",
            "expert_manifest_sha256",
            "owner_pid",
            "allocation_coordinate",
            "controller_identity_sha256",
        ),
        "DurableCompanionCapacityDenialObservationV1": (
            "__weakref__",
            "schema_version",
            "session_id",
            "durable_parent_envelope_sha256",
            "candidate_state_sha256",
            "candidate_cursor_sha256",
            "requested_bytes",
            "available_bytes",
            "emergency_reserve_bytes",
            "publication_epoch",
            "observation_sha256",
        ),
        "DurableCompanionPublicationAckV1": (
            "__weakref__",
            "schema_version",
            "session_id",
            "durable_parent_envelope_sha256",
            "append_receipt_sha256",
            "candidate_state_sha256",
            "candidate_cursor_sha256",
            "publication_epoch",
            "ack_sha256",
        ),
        "PendingDurableCompanionEmergencyV1": (
            "__weakref__",
            "schema_version",
            "session_id",
            "durable_parent_envelope_sha256",
            "candidate_state_sha256",
            "candidate_cursor_sha256",
            "capacity_denial_sha256",
            "publication_epoch",
            "pending_sha256",
        ),
        "DurableCompanionEmergencyPublicationProofV1": (
            "__weakref__",
            "schema_version",
            "session_id",
            "durable_parent_envelope_sha256",
            "group_receipt_sha256",
            "terminal_receipt_sha256",
            "group_sha256",
            "terminal_sha256",
            "candidate_state_sha256",
            "candidate_cursor_sha256",
            "publication_epoch",
            "proof_sha256",
        ),
        "PendingDurableCompanionEmergencyAbortReceiptV1": (
            "__weakref__",
            "schema_version",
            "session_id",
            "controller_identity_sha256",
            "pending_sha256",
            "capacity_denial_sha256",
            "abort_reason",
            "publication_epoch",
            "receipt_sha256",
        ),
    }

    def symbol(self, name: str) -> object:
        value = getattr(controller_module, name, None)
        self.assertIsNotNone(value, f"R19 missing controller symbol: {name}")
        return value

    def test_r19_c01_c02_public_identity_and_six_opaque_values(self) -> None:
        for name, expected_slots in self._PUBLIC_SLOTS.items():
            with self.subTest(name=name):
                value_type = self.symbol(name)
                self.assertIs(type(value_type), type)
                self.assertEqual(value_type.__slots__, expected_slots)
                with self.assertRaises(TypeError):
                    value_type()
                with self.assertRaises(TypeError):
                    type(f"Hostile{name}", (value_type,), {})
                forged = object.__new__(value_type)
                self.assertIn("redacted", repr(forged))
                for operation in (copy.copy, copy.deepcopy, pickle.dumps):
                    with self.assertRaises(TypeError):
                        operation(forged)

    def test_r19_c03_c04_epoch_and_controller_surface(self) -> None:
        expected = {
            "process_durable_parent": ("self", "durable_parent"),
            "process_evidence_terminal": ("self", "terminal"),
            "complete_pending_emergency": (
                "self",
                "pending",
                "terminal",
                "source_close_claim",
            ),
            "process_one": ("self", "timeout_seconds"),
            "snapshot": ("self",),
            "close": ("self",),
            "abort_pending_durable_companion_emergency_v1": (
                "self",
                "pending",
                "reason",
            ),
        }
        for name, parameters in expected.items():
            with self.subTest(name=name):
                member = getattr(ExpertControllerV1, name, None)
                self.assertIsNotNone(member, f"R19 missing method: {name}")
                self.assertEqual(tuple(inspect.signature(member).parameters), parameters)
        self.assertIn("_publication_epoch", ExpertControllerV1.__slots__)
        self.assertIn("_controller_identity", ExpertControllerV1.__slots__)
        self.assertIn("_lifecycle", ExpertControllerV1.__slots__)

    def test_r19_c05_c06_capacity_and_candidate_contract_records(self) -> None:
        expected_slots = {
            "_DurableCompanionCapacityDenialObservationAuthorityV1": (
                "controller", "controller_identity", "issuance_snapshot",
                "parent", "store_error", "prior_state", "prior_cursor",
                "denied_candidate_state", "denied_candidate_cursor",
                "denied_group", "denied_payloads", "emergency_candidate_state",
                "emergency_candidate_cursor", "emergency_group",
                "emergency_payloads", "requested_bytes", "available_bytes",
                "emergency_reserve_bytes", "publication_epoch", "owner_pid",
                "owner_thread", "lifecycle",
            ),
            "_PendingDurableCompanionEmergencyAuthorityV1": (
                "controller", "controller_identity", "pending", "observation",
                "parent", "ingress", "runtime", "session_id", "prior_state",
                "prior_cursor", "denied_candidate_state", "denied_candidate_cursor",
                "denied_group", "denied_payloads", "emergency_candidate_state",
                "emergency_candidate_cursor", "emergency_group",
                "emergency_payloads", "publication_epoch", "publication_lock",
                "owner_pid", "owner_thread", "lifecycle",
                "active_completion_scope", "retry_subject", "reserved_claim",
                "reserved_subject", "reserved_terminal", "reserved_causal_proof",
                "reserved_completion_scope", "prepared_abort_reason",
                "prepared_abort_receipt", "abort_reason", "abort_receipt",
            ),
        }
        for name, slots in expected_slots.items():
            with self.subTest(name=name):
                value_type = self.symbol(name)
                self.assertEqual(tuple(field.name for field in fields(value_type)), slots)

    def test_r19_c07_c08_capacity_issuance_api_and_exact_errors(self) -> None:
        issue = self.symbol("_issue_pending_durable_companion_emergency_v1")
        signature = inspect.signature(issue)
        self.assertEqual(
            tuple(signature.parameters),
            ("observation", "candidate_state", "candidate_cursor"),
        )
        observation_type = self.symbol(
            "DurableCompanionCapacityDenialObservationV1"
        )
        with self.assertRaisesRegex(
            TypeError,
            "^exact DurableCompanionCapacityDenialObservationV1 required$",
        ):
            issue(
                object(),
                candidate_state=object(),
                candidate_cursor=object(),
            )
        self.assertIsNotNone(observation_type)

    def test_r19_c09_c10_digest_domains_and_terminal_api(self) -> None:
        source = inspect.getsource(controller_module)
        for domain in (
            "INCI-EXPERT-CONTROLLER-IDENTITY-V1",
            "INCI-DURABLE-COMPANION-CAPACITY-DENIAL-OBSERVATION-V1",
            "INCI-DURABLE-COMPANION-PUBLICATION-ACK-V1",
            "INCI-PENDING-DURABLE-COMPANION-EMERGENCY-V1",
            "INCI-DURABLE-COMPANION-EMERGENCY-PUBLICATION-V1",
            "INCI-PENDING-DURABLE-COMPANION-EMERGENCY-ABORT-RECEIPT-V1",
            "INCI-DURABLE-EXPERT-APPEND-RECEIPT-V1",
            "INCI-DURABLE-EXPERT-TERMINAL-RECEIPT-V1",
            "INCI-EXPERT-JOURNAL-CURSOR-V1",
        ):
            with self.subTest(domain=domain):
                self.assertIn(domain, source)
        self.assertIn("process_evidence_terminal", source)
        self.assertNotIn("process_evidence_terminal_or_none", source)

    def test_r19_c11_c12_terminal_receipt_handoffs(self) -> None:
        for name, parameters in {
            "_claim_ordinary_durable_expert_terminal_receipt_for_alignment_v1": (
                "controller", "terminal"
            ),
            "_claim_emergency_durable_expert_terminal_receipt_for_alignment_v1": (
                "controller", "proof"
            ),
        }.items():
            with self.subTest(name=name):
                helper = self.symbol(name)
                self.assertEqual(tuple(inspect.signature(helper).parameters), parameters)
        handoff = self.symbol("_DurableExpertTerminalReceiptHandoffV1")
        self.assertEqual(
            tuple(field.name for field in fields(handoff)),
            (
                "controller", "controller_identity", "terminal_issuance_snapshot",
                "terminal_receipt", "lane", "publication_epoch", "owner_pid",
                "owner_thread", "lifecycle",
            ),
        )

    def test_r19_c13_c14_scope_and_ingress_resolvers(self) -> None:
        scope = self.symbol("_DeferredEmergencyCompletionScopeV1")
        self.assertEqual(
            tuple(field.name for field in fields(scope)),
            (
                "controller", "pending", "pending_authority", "terminal",
                "publication_lock", "publication_epoch", "owner_pid",
                "owner_thread", "lifecycle", "reservation_committed",
                "source_close_claim", "subject", "causal_proof",
            ),
        )
        for name, parameters in {
            "_resolve_deferred_emergency_subject_inputs_v1": (
                "controller", "pending"
            ),
            "_resolve_deferred_emergency_pending_for_ingress_commit_v1": (
                "controller", "pending", "subject"
            ),
        }.items():
            helper = self.symbol(name)
            self.assertEqual(tuple(inspect.signature(helper).parameters), parameters)

    def test_r19_c15_c16_two_phase_ingress_usage_and_failure_close(self) -> None:
        tree = ast.parse(inspect.getsource(controller_module))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        required = {
            "_prepare_durable_causal_precedes_proof_commit_v1",
            "_commit_prepared_durable_causal_precedes_proof_v1",
            "_close_durable_causal_precedes_proof_after_deferred_append_failure_v1",
        }
        self.assertTrue(required.issubset(called), required - called)
        self.assertNotIn(
            "_consume_durable_causal_precedes_proof_after_deferred_append_v1",
            called,
        )

    def test_r19_c17_c18_ack_independent_bits_and_legacy_discard(self) -> None:
        authority = self.symbol("_DurableCompanionPublicationAckAuthorityV1")
        self.assertEqual(
            tuple(field.name for field in fields(authority)),
            (
                "controller", "controller_identity", "issuance_snapshot", "parent",
                "append_receipt", "candidate_state", "candidate_cursor",
                "publication_epoch", "publication_lock", "owner_pid", "owner_thread",
                "lifecycle", "cursor_issued", "facts_claimed",
            ),
        )
        for name in (
            "_resolve_durable_companion_publication_ack_for_wave_c_v1",
            "_claim_durable_companion_publication_ack_cursor_v1",
            "_claim_durable_companion_publication_ack_facts_v1",
            "_discard_legacy_companion_publication_ack_v1",
        ):
            self.symbol(name)

    def test_r19_c19_emergency_proof_single_projection_contract(self) -> None:
        authority = self.symbol(
            "_DurableCompanionEmergencyPublicationProofAuthorityV1"
        )
        self.assertEqual(
            tuple(field.name for field in fields(authority)),
            (
                "controller", "controller_identity", "issuance_snapshot", "pending",
                "parent", "source_close_claim", "subject", "causal_proof",
                "terminal_envelope", "emergency_receipt", "group_receipt",
                "terminal_receipt", "candidate_state", "candidate_cursor",
                "expert_terminal", "publication_epoch", "publication_lock",
                "owner_pid", "owner_thread", "lifecycle",
            ),
        )
        self.symbol("_resolve_durable_companion_emergency_proof_for_wave_c_v1")
        self.symbol("_claim_durable_companion_emergency_proof_projection_v1")

    def test_r19_c20_c22_abort_enum_receipt_and_capacity_exception(self) -> None:
        reason = self.symbol("PendingEmergencyAbortReasonV1")
        self.assertTrue(issubclass(reason, str))
        self.assertTrue(issubclass(reason, Enum))
        self.assertEqual(
            tuple(member.value for member in reason),
            (
                "legacy_process_one_capacity_denial",
                "recorded_capacity_contract_violation",
                "caller_close_with_pending",
            ),
        )
        error_type = self.symbol("ExpertCapacityExceeded")
        error = error_type()
        self.assertIs(type(error), error_type)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(error.args, ("expert_legacy_process_one_capacity_denied",))
        with self.assertRaises(TypeError):
            error_type("caller text")
        with self.assertRaises(TypeError):
            error_type(detail="caller text")

    def test_r19_c21_c23_c24_abort_contract_and_idempotence_surface(self) -> None:
        method = getattr(
            ExpertControllerV1,
            "abort_pending_durable_companion_emergency_v1",
            None,
        )
        self.assertIsNotNone(
            method,
            "R19 missing method: abort_pending_durable_companion_emergency_v1",
        )
        self.assertIs(
            method,
            ExpertControllerV1.abort_pending_durable_companion_emergency_v1,
        )
        source = inspect.getsource(controller_module)
        self.assertIn("durable_companion_emergency_pending_abort_uncertain", source)
        self.assertIn("CALLER_CLOSE_WITH_PENDING", source)
        self.assertIn("LEGACY_PROCESS_ONE_CAPACITY_DENIAL", source)
        self.assertIn("RECORDED_CAPACITY_CONTRACT_VIOLATION", source)

    def test_r19_c25_c26_weak_registries_and_retry_slots(self) -> None:
        source = inspect.getsource(controller_module)
        self.assertIn("weakref", source)
        self.assertNotIn("WeakKeyDictionary", source)
        self.assertIn("retry_subject", source)
        self.assertIn("active_completion_scope", source)
        self.assertNotIn("(pending, subject)", source)

    def test_r19_c27_c31_lifecycle_tokens_and_lock_order_sources(self) -> None:
        source = inspect.getsource(controller_module)
        for token in (
            "ACTIVE", "EMERGENCY_PENDING", "HALTED_UNCLEAN",
            "DURABILITY_UNCERTAIN_HALTED", "TERMINAL", "COMMIT_RESERVED",
            "PUBLISHED", "PUBLICATION_FAILED_CLOSED", "ABORTED_NONPUBLICATION",
            "ABORT_FAILED_CLOSED", "RESERVATION_COMMITTED", "PUBLISHED_CLOSED",
            "APPEND_FAILED_CLOSED", "DISCARDED_LEGACY_CLOSED",
            "BOTH_CLAIMED_CLOSED", "PROJECTION_CONSUMED",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertIn("with self._publication_lock", source)

    def test_r19_c32_dependency_and_authority_boundary(self) -> None:
        source_path = Path(controller_module.__file__)
        source = source_path.read_text("utf-8")
        tree = ast.parse(source)
        forbidden = {
            "statvfs", "fstatvfs", "requests", "socket", "subprocess",
            "urllib", "credential", "portfolio", "order", "execution",
        }
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        self.assertTrue(forbidden.isdisjoint(imported | called))
        self.assertNotIn("inci_tennis_runtime.shadow_sources", "\n".join(
            line for line in source.splitlines() if line.startswith("from ")
        ))


    def test_r19_controller_has_one_function_local_a5_claim_bridge(self) -> None:
        source_path = Path(controller_module.__file__)
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        bridges = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name
            == "_consume_deferred_emergency_source_close_claim_v1"
        ]
        self.assertEqual(len(bridges), 1)
        bridge = bridges[0]
        a5_imports = [
            node
            for node in ast.walk(bridge)
            if isinstance(node, ast.ImportFrom)
            and node.module == "inci_tennis_runtime.shadow_sources"
        ]
        self.assertEqual(len(a5_imports), 1)
        self.assertEqual(
            tuple(alias.name for alias in a5_imports[0].names),
            (
                "DeferredEmergencySourceCloseClaimV1",
                "consume_deferred_emergency_source_close_before_terminal_v1",
            ),
        )
        helper_calls = [
            node
            for node in ast.walk(bridge)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id
            == "consume_deferred_emergency_source_close_before_terminal_v1"
        ]
        self.assertEqual(len(helper_calls), 1)
        self.assertEqual(len(helper_calls[0].args), 2)
        self.assertEqual(helper_calls[0].keywords, [])
        self.assertEqual(
            tuple(
                argument.id
                for argument in helper_calls[0].args
                if isinstance(argument, ast.Name)
            ),
            ("claim", "subject"),
        )
        direct_ingress_issue_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id
            == "_issue_durable_causal_precedes_for_deferred_commit_v1"
        ]
        self.assertEqual(direct_ingress_issue_calls, [])
        module_level_a5_imports = [
            node
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module == "inci_tennis_runtime.shadow_sources"
        ]
        self.assertEqual(module_level_a5_imports, [])


class Round19ControllerDynamicTests(unittest.TestCase):
    """Executable Round-19 controller transactions over real ingress values."""

    setUp = ExpertControllerIntegrationTests.setUp
    tearDown = ExpertControllerIntegrationTests.tearDown
    create_controller = ExpertControllerIntegrationTests.create_controller
    mocked_facade = ExpertControllerIntegrationTests.mocked_facade
    fresh_case = ExpertControllerIntegrationTests.fresh_case
    enqueue_one = ExpertControllerIntegrationTests.enqueue_one

    @contextmanager
    def _patched_ingress_bridge(self, name: str, replacement: object):
        """Patch both the current bound seam and ruled function-local import."""
        with ExitStack() as stack:
            patched = False
            if hasattr(ingress_module, name):
                stack.enter_context(
                    mock.patch.object(
                        ingress_module,
                        name,
                        new=replacement,
                    )
                )
                patched = True
            if hasattr(controller_module, name):
                stack.enter_context(
                    mock.patch.object(
                        controller_module,
                        name,
                        new=replacement,
                    )
                )
                patched = True
            if not patched:
                raise AttributeError(name)
            yield

    @contextmanager
    def _governed_source_close_claim(self, label: str):
        """Supply the missing Wave-C owner with a real ordered coordinate."""
        class DeferredEmergencySourceCloseClaimV1:
            pass

        class _DeferredEmergencySourceCloseClaimAuthorityV1:
            def __init__(self) -> None:
                self.lifecycle = "CLAIMED"

        claim = DeferredEmergencySourceCloseClaimV1()
        authority = _DeferredEmergencySourceCloseClaimAuthorityV1()
        before_coordinate = ingress_module._issue_coordinate_v1(
            self.ingress,
            self.runtime,
            session_id=self.manifest.session_id,
            stage="SOURCE_CLOSE_COMPLETE",
            subject=claim,
            subject_sha256=sha256(label.encode("ascii")).hexdigest(),
        )
        owner_module = types.ModuleType(
            "inci_tennis_runtime.shadow_sources"
        )
        owner_module.DeferredEmergencySourceCloseClaimV1 = (
            DeferredEmergencySourceCloseClaimV1
        )
        owner_module._DeferredEmergencySourceCloseClaimAuthorityV1 = (
            _DeferredEmergencySourceCloseClaimAuthorityV1
        )
        owner_module._DEFERRED_EMERGENCY_CLAIM_COMMIT_LOCK_V1 = (
            threading.RLock()
        )

        def resolve(value):
            if value is not claim:
                raise ValueError("wrong_test_source_close_claim")
            return authority, before_coordinate

        owner_module._resolve_deferred_emergency_source_close_claim_for_ingress_commit_v1 = resolve

        def consume(value, subject):
            if type(value) is not DeferredEmergencySourceCloseClaimV1:
                raise TypeError(
                    "exact DeferredEmergencySourceCloseClaimV1 required"
                )
            proof = (
                ingress_module._issue_durable_causal_precedes_for_deferred_commit_v1(
                    claim=value,
                    subject=subject,
                )
            )
            return value, subject, proof

        owner_module.consume_deferred_emergency_source_close_before_terminal_v1 = consume
        with mock.patch.dict(
            sys.modules,
            {"inci_tennis_runtime.shadow_sources": owner_module},
        ):
            yield claim

    def _drain_durable_parent(
        self,
        *,
        sequence: int = 1,
    ) -> DurableIngressParentV1:
        producer, result = self.enqueue_one(sequence)
        envelope = self.ingress.drain_one_parent(
            self.runtime,
            timeout_seconds=1.0,
        )
        producer.join(5)
        self.assertFalse(producer.is_alive())
        self.assertEqual(len(result), 1)
        self.assertIs(type(envelope), DurableIngressParentV1)
        return envelope

    def _drain_durable_terminal(self) -> DurableEvidenceTerminalV1:
        self.ingress.close_inputs()
        envelope = self.ingress.drain_one_parent(
            self.runtime,
            timeout_seconds=1.0,
        )
        self.assertIs(type(envelope), DurableEvidenceTerminalV1)
        return envelope

    def test_r19_unavailable_a5_claim_bridge_consumes_nothing(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        captured_subjects: list[object] = []
        real_issue_subject = (
            ingress_module._issue_deferred_emergency_commit_subject_v1
        )

        def capture_subject(*, controller, pending, terminal):
            subject = real_issue_subject(
                controller=controller,
                pending=pending,
                terminal=terminal,
            )
            captured_subjects.append(subject)
            return subject

        with self.mocked_facade(probe):
            controller = self.create_controller()
            before, pending = self._capacity_pending(probe, controller)
            authority, terminal, _ = self._emergency_terminal_material(
                probe,
                pending,
            )
            terminal_authority = ingress_module._lookup_envelope_authority(
                terminal
            )
            for _ in range(2):
                with (
                    self._patched_ingress_bridge(
                        "_issue_deferred_emergency_commit_subject_v1",
                        capture_subject,
                    ),
                    self.assertRaisesRegex(
                        ValueError,
                        "^durable_companion_emergency_source_close_claim_unavailable$",
                    ) as raised,
                ):
                    controller.complete_pending_emergency(
                        pending,
                        terminal,
                        object(),
                    )
                self.assertIsNone(raised.exception.__cause__)
                self.assertTrue(raised.exception.__suppress_context__)
                self.assertEqual(authority.lifecycle, "FRESH")
                self.assertEqual(
                    authority.active_completion_scope.lifecycle,
                    "CLEARED",
                )
                self.assertEqual(
                    terminal_authority.lifecycle,
                    "ISSUED",
                )
                self.assertEqual(probe.emergency_append_count, 0)
                self.assertEqual(controller.snapshot(), before)
                self.assertEqual(
                    object.__getattribute__(controller, "_lifecycle"),
                    "EMERGENCY_PENDING",
                )
            self.assertEqual(len(captured_subjects), 2)
            self.assertIs(captured_subjects[0], captured_subjects[1])
            subject_authority = ingress_module._lookup_subject_authority(
                captured_subjects[0]
            )
            self.assertEqual(subject_authority.lifecycle, "FRESH")

    @staticmethod
    def _capacity_error() -> ExpertPrewriteCapacityError:
        return ExpertPrewriteCapacityError(
            requested_bytes=4096,
            available_bytes=1024,
            emergency_reserve_bytes=EXPERT_EMERGENCY_RESERVE_BYTES,
        )

    @staticmethod
    def _identity_digest(identity: object) -> str:
        projection = {
            "schema_version": identity.schema_version,
            "session_id": identity.session_id,
            "expert_manifest_sha256": identity.expert_manifest_sha256,
            "owner_pid": identity.owner_pid,
            "allocation_coordinate": identity.allocation_coordinate,
        }
        return sha256(
            b"INCI-EXPERT-CONTROLLER-IDENTITY-V1\0"
            + canonical_expert_bytes(projection)
        ).hexdigest()

    @staticmethod
    def _observation_digest(observation: object) -> str:
        projection = {
            "schema_version": observation.schema_version,
            "session_id": observation.session_id,
            "durable_parent_envelope_sha256": (
                observation.durable_parent_envelope_sha256
            ),
            "candidate_state_sha256": observation.candidate_state_sha256,
            "candidate_cursor_sha256": observation.candidate_cursor_sha256,
            "requested_bytes": observation.requested_bytes,
            "available_bytes": observation.available_bytes,
            "emergency_reserve_bytes": observation.emergency_reserve_bytes,
            "publication_epoch": observation.publication_epoch,
        }
        return sha256(
            b"INCI-DURABLE-COMPANION-CAPACITY-DENIAL-OBSERVATION-V1\0"
            + canonical_expert_bytes(projection)
        ).hexdigest()

    def _capacity_pending(
        self,
        probe: _RuledStoreProbe,
        controller: ExpertControllerV1,
    ) -> tuple[
        tuple[ExpertStateV1, ExpertJournalCursorV1, None],
        object,
    ]:
        before = controller.snapshot()
        probe.prewrite_capacity_error = self._capacity_error()
        parent = self._drain_durable_parent()
        result = controller.process_durable_parent(parent)
        pending_type = getattr(
            controller_module,
            "PendingDurableCompanionEmergencyV1",
        )
        self.assertIs(type(result[2]), pending_type)
        self.assertIs(result[0], before[0])
        self.assertIs(result[1], before[1])
        self.assertIsNone(before[2])
        return before, result[2]

    def _emergency_terminal_material(
        self,
        probe: _RuledStoreProbe,
        pending: object,
    ) -> tuple[object, DurableEvidenceTerminalV1, ExpertSessionTerminalV1]:
        authority = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        ).lookup(pending)
        terminal = self.ingress.close_external_halt_terminal(self.runtime)
        self.assertIs(type(terminal), DurableEvidenceTerminalV1)
        expert_terminal = _terminal_for(
            self.manifest,
            authority.emergency_candidate_cursor,
            terminal.terminal,
            clean=False,
            evidence_reason="operator_halt",
        )
        probe.tail_result = authority.parent.parent
        probe.built_terminal_pair = (terminal.terminal, expert_terminal)
        return authority, terminal, expert_terminal

    @contextmanager
    def _synthetic_causal_seam(self, pending: object):
        """Stand in only for the not-yet-delivered Wave-C claim issuer."""
        subject = object()
        causal_proof = object()
        prepared = object()
        calls = {"commit": 0, "failure_close": 0}

        def issue_subject(*, controller, pending, terminal):
            return subject

        def reserve(claim, subject):
            authority = getattr(
                controller_module,
                "_PENDING_EMERGENCY_AUTHORITIES_V1",
            ).lookup(pending)
            scope = authority.active_completion_scope
            authority.lifecycle = "COMMIT_RESERVED"
            authority.reserved_claim = claim
            authority.reserved_subject = subject
            authority.reserved_terminal = scope.terminal
            authority.reserved_causal_proof = causal_proof
            authority.reserved_completion_scope = scope
            scope.lifecycle = "RESERVATION_COMMITTED"
            scope.reservation_committed = True
            scope.causal_proof = causal_proof
            return causal_proof

        def prepare(*_args, **_kwargs):
            return prepared

        def commit(value):
            self.assertIs(value, prepared)
            calls["commit"] += 1

        def failure_close(*_args, **_kwargs):
            calls["failure_close"] += 1

        with (
            self._patched_ingress_bridge(
                "_issue_deferred_emergency_commit_subject_v1",
                issue_subject,
            ),
            self._patched_ingress_bridge(
                "_consume_deferred_emergency_source_close_claim_v1",
                reserve,
            ),
            self._patched_ingress_bridge(
                "_prepare_durable_causal_precedes_proof_commit_v1",
                prepare,
            ),
            self._patched_ingress_bridge(
                "_commit_prepared_durable_causal_precedes_proof_v1",
                commit,
            ),
            self._patched_ingress_bridge(
                "_close_durable_causal_precedes_proof_after_deferred_append_failure_v1",
                failure_close,
            ),
        ):
            yield calls

    def test_r19_dynamic_identity_allocation_burns_failed_coordinate(self) -> None:
        identity_type = getattr(controller_module, "ExpertControllerIdentityV1")
        with self.fresh_case() as genesis_case:
            genesis_probe = _RuledStoreProbe(genesis_case.collected)
            with (
                mock.patch.object(
                    controller_module,
                    "_CONTROLLER_IDENTITY_ALLOCATION_COORDINATE_V1",
                    0,
                ),
                genesis_case.mocked_facade(genesis_probe),
            ):
                genesis_controller = genesis_case.create_controller()
                genesis_identity = object.__getattribute__(
                    genesis_controller,
                    "_controller_identity",
                )
                self.assertEqual(genesis_identity.allocation_coordinate, 1)

        with self.mocked_facade(_RuledStoreProbe(self.collected)):
            first = self.create_controller()
        first_identity = object.__getattribute__(
            first,
            "_controller_identity",
        )
        self.assertIs(type(first_identity), identity_type)
        self.assertEqual(first_identity.schema_version, 1)
        self.assertEqual(first_identity.session_id, self.manifest.session_id)
        self.assertEqual(
            first_identity.expert_manifest_sha256,
            self.manifest.manifest_sha256,
        )
        self.assertEqual(first_identity.owner_pid, os.getpid())
        self.assertGreater(first_identity.allocation_coordinate, 0)
        self.assertLessEqual(
            first_identity.allocation_coordinate,
            9_223_372_036_854_775_807,
        )
        self.assertEqual(
            first_identity.controller_identity_sha256,
            self._identity_digest(first_identity),
        )

        registry_type = getattr(controller_module, "_WeakIdentityRegistryV1")
        controller_registry = getattr(
            controller_module,
            "_CONTROLLER_AUTHORITIES_V1",
        )
        original_register = registry_type.register

        def fail_controller_registration(registry, key, value):
            if registry is controller_registry:
                raise RuntimeError("injected_controller_registration_failure")
            return original_register(registry, key, value)

        with self.fresh_case() as failed_case:
            failed_probe = _RuledStoreProbe(failed_case.collected)
            with (
                failed_case.mocked_facade(failed_probe),
                mock.patch.object(
                    registry_type,
                    "register",
                    fail_controller_registration,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "^injected_controller_registration_failure$",
                ),
            ):
                failed_case.create_controller()
            self.assertEqual(failed_probe.abort_count, 1)

        with self.fresh_case() as next_case:
            with next_case.mocked_facade(_RuledStoreProbe(next_case.collected)):
                next_controller = next_case.create_controller()
            next_identity = object.__getattribute__(
                next_controller,
                "_controller_identity",
            )
            self.assertEqual(
                next_identity.allocation_coordinate,
                first_identity.allocation_coordinate + 2,
            )

    def test_r19_dynamic_ordinary_ack_commits_epoch_once(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            before = controller.snapshot()
            self.assertEqual(
                object.__getattribute__(controller, "_publication_epoch"),
                0,
            )
            parent = self._drain_durable_parent()
            result = controller.process_durable_parent(parent)
            ack_type = getattr(
                controller_module,
                "DurableCompanionPublicationAckV1",
            )
            self.assertIs(type(result[2]), ack_type)
            self.assertIs(result[0], controller.snapshot()[0])
            self.assertIs(result[1], controller.snapshot()[1])
            self.assertIsNot(result[0], before[0])
            self.assertIsNot(result[1], before[1])
            self.assertEqual(result[2].publication_epoch, 1)
            self.assertEqual(
                object.__getattribute__(controller, "_publication_epoch"),
                1,
            )
            self.assertEqual(probe.append_count, 1)
            self.assertEqual(probe.ack_count, 1)
            with self.assertRaises(ValueError):
                controller.process_durable_parent(parent)
            self.assertEqual(
                object.__getattribute__(controller, "_publication_epoch"),
                1,
            )
            self.assertEqual(probe.append_count, 1)

    def test_r19_dynamic_ack_unavailable_keeps_commit_and_halts(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        registry_type = getattr(controller_module, "_WeakIdentityRegistryV1")
        ack_registry = getattr(
            controller_module,
            "_PUBLICATION_ACK_AUTHORITIES_V1",
        )
        original_register = registry_type.register

        def fail_ack_registration(registry, key, value):
            if registry is ack_registry:
                raise RuntimeError("injected_ack_registration_failure")
            return original_register(registry, key, value)

        with self.mocked_facade(probe):
            controller = self.create_controller()
            before = controller.snapshot()
            parent = self._drain_durable_parent()
            with (
                mock.patch.object(
                    registry_type,
                    "register",
                    fail_ack_registration,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "^durable_companion_publication_ack_unavailable$",
                ) as raised,
            ):
                controller.process_durable_parent(parent)
            self.assertIsNone(raised.exception.__cause__)
            after = controller.snapshot()
            self.assertIsNot(after[0], before[0])
            self.assertIsNot(after[1], before[1])
            self.assertEqual(
                object.__getattribute__(controller, "_publication_epoch"),
                1,
            )
            self.assertEqual(
                object.__getattribute__(controller, "_lifecycle"),
                "HALTED_UNCLEAN",
            )
            self.assertEqual(probe.append_count, 1)
            self.assertEqual(probe.ack_count, 1)
            self.assertEqual(probe.abort_count, 1)

    def test_r19_dynamic_valid_capacity_facts_issue_pending_without_append(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            routed: dict[str, object] = {}
            original_issue_append = controller_module.issue_expert_append_permit

            def capture_denied_route(*args, **kwargs):
                routed["group"] = args[3]
                routed["payloads"] = args[4]
                return original_issue_append(*args, **kwargs)

            with mock.patch.object(
                controller_module,
                "issue_expert_append_permit",
                new=capture_denied_route,
            ):
                before, pending = self._capacity_pending(probe, controller)
            authority = getattr(
                controller_module,
                "_PENDING_EMERGENCY_AUTHORITIES_V1",
            ).lookup(pending)
            self.assertIsNotNone(authority)
            observation = authority.observation
            observation_type = getattr(
                controller_module,
                "DurableCompanionCapacityDenialObservationV1",
            )
            self.assertIs(type(observation), observation_type)
            self.assertEqual(observation.requested_bytes, 4096)
            self.assertEqual(observation.available_bytes, 1024)
            self.assertEqual(
                observation.emergency_reserve_bytes,
                EXPERT_EMERGENCY_RESERVE_BYTES,
            )
            self.assertEqual(observation.publication_epoch, 1)
            self.assertEqual(
                observation.observation_sha256,
                self._observation_digest(observation),
            )
            self.assertEqual(pending.publication_epoch, 1)
            self.assertEqual(
                pending.capacity_denial_sha256,
                observation.observation_sha256,
            )
            self.assertIs(authority.prior_state, before[0])
            self.assertIs(authority.prior_cursor, before[1])
            self.assertIs(authority.observation, observation)
            self.assertIsNot(
                authority.denied_candidate_state,
                authority.emergency_candidate_state,
            )
            self.assertIsNot(
                authority.denied_candidate_cursor,
                authority.emergency_candidate_cursor,
            )
            denied_state_sha256 = expert_state_sha256(
                authority.denied_candidate_state
            )
            emergency_state_sha256 = expert_state_sha256(
                authority.emergency_candidate_state
            )
            denied_cursor_sha256 = (
                controller_module._expert_cursor_sha256_v1(
                    authority.denied_candidate_cursor
                )
            )
            emergency_cursor_sha256 = (
                controller_module._expert_cursor_sha256_v1(
                    authority.emergency_candidate_cursor
                )
            )
            self.assertNotEqual(
                denied_state_sha256,
                emergency_state_sha256,
            )
            self.assertNotEqual(
                denied_cursor_sha256,
                emergency_cursor_sha256,
            )
            self.assertEqual(
                observation.candidate_state_sha256,
                denied_state_sha256,
            )
            self.assertEqual(
                observation.candidate_cursor_sha256,
                denied_cursor_sha256,
            )
            self.assertEqual(pending.candidate_state_sha256, denied_state_sha256)
            self.assertEqual(
                pending.candidate_cursor_sha256,
                denied_cursor_sha256,
            )
            self.assertNotEqual(
                pending.candidate_state_sha256,
                emergency_state_sha256,
            )
            self.assertNotEqual(
                pending.candidate_cursor_sha256,
                emergency_cursor_sha256,
            )
            self.assertIs(routed["group"], authority.denied_group)
            self.assertIs(routed["payloads"], authority.denied_payloads)
            self.assertIsNot(routed["group"], authority.emergency_group)
            self.assertIsNot(routed["payloads"], authority.emergency_payloads)
            self.assertEqual(probe.append_count, 0)
            self.assertEqual(probe.ack_count, 0)
            self.assertEqual(probe.emergency_append_count, 0)
            self.assertEqual(
                object.__getattribute__(controller, "_publication_epoch"),
                1,
            )
            self.assertEqual(
                object.__getattribute__(controller, "_lifecycle"),
                "EMERGENCY_PENDING",
            )

    def test_r19_dynamic_malformed_capacity_facts_halt_without_epoch_or_append(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        probe.prewrite_capacity = True
        with self.mocked_facade(probe):
            controller = self.create_controller()
            before = controller.snapshot()
            parent = self._drain_durable_parent()
            with self.assertRaisesRegex(
                RuntimeError,
                "^expert_capacity_observation_invalid$",
            ) as raised:
                controller.process_durable_parent(parent)
            self.assertIsNone(raised.exception.__cause__)
            after = controller.snapshot()
            self.assertIs(after[0], before[0])
            self.assertIs(after[1], before[1])
            self.assertEqual(
                object.__getattribute__(controller, "_publication_epoch"),
                0,
            )
            self.assertEqual(
                object.__getattribute__(controller, "_lifecycle"),
                "HALTED_UNCLEAN",
            )
            self.assertEqual(probe.append_count, 0)
            self.assertEqual(probe.ack_count, 0)
            self.assertEqual(probe.emergency_append_count, 0)
            self.assertEqual(probe.abort_count, 1)

    def test_r19_dynamic_terminal_returns_nonnull_and_handoff_is_one_shot(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            terminal_envelope = self._drain_durable_terminal()
            before = controller.snapshot()
            expected_terminal = _terminal_for(
                self.manifest,
                before[1],
                terminal_envelope.terminal,
                clean=True,
                evidence_reason="operator_stop",
            )
            probe.built_terminal_pair = (
                terminal_envelope.terminal,
                expected_terminal,
            )
            result = controller.process_evidence_terminal(terminal_envelope)
            self.assertIs(result[0], before[0])
            self.assertIs(result[1], before[1])
            self.assertIs(result[2], expected_terminal)
            self.assertIsNotNone(result[2])
            claim = getattr(
                controller_module,
                "_claim_ordinary_durable_expert_terminal_receipt_for_alignment_v1",
            )
            receipt = claim(controller, result[2])
            self.assertIs(receipt, probe.last_terminal_receipt)
            with self.assertRaisesRegex(
                ValueError,
                "^durable_companion_ordinary_terminal_receipt_consumed$",
            ):
                claim(controller, result[2])

    def test_r19_dynamic_ack_claim_bits_are_independent(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            ack = controller.process_durable_parent(
                self._drain_durable_parent()
            )[2]
            resolve = getattr(
                controller_module,
                "_resolve_durable_companion_publication_ack_for_wave_c_v1",
            )
            claim_cursor = getattr(
                controller_module,
                "_claim_durable_companion_publication_ack_cursor_v1",
            )
            claim_facts = getattr(
                controller_module,
                "_claim_durable_companion_publication_ack_facts_v1",
            )
            binding = resolve(ack)
            with binding.publication_lock:
                self.assertIsNone(claim_facts(ack, binding))
                self.assertFalse(binding.authority.cursor_issued)
                self.assertTrue(binding.authority.facts_claimed)
                self.assertEqual(binding.authority.lifecycle, "ACTIVE")
                with self.assertRaisesRegex(
                    ValueError,
                    "^durable_companion_publication_ack_facts_consumed$",
                ):
                    claim_facts(ack, binding)
                self.assertIsNone(claim_cursor(ack, binding))
                self.assertTrue(binding.authority.cursor_issued)
                self.assertTrue(binding.authority.facts_claimed)
                self.assertEqual(
                    binding.authority.lifecycle,
                    "BOTH_CLAIMED_CLOSED",
                )

    def test_r19_dynamic_legacy_ack_discard_is_idempotent(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            ack = controller.process_durable_parent(
                self._drain_durable_parent()
            )[2]
            resolve = getattr(
                controller_module,
                "_resolve_durable_companion_publication_ack_for_wave_c_v1",
            )
            discard = getattr(
                controller_module,
                "_discard_legacy_companion_publication_ack_v1",
            )
            binding = resolve(ack)
            self.assertIsNone(discard(controller, ack))
            self.assertEqual(
                binding.authority.lifecycle,
                "DISCARDED_LEGACY_CLOSED",
            )
            self.assertIsNone(discard(controller, ack))
            claim_cursor = getattr(
                controller_module,
                "_claim_durable_companion_publication_ack_cursor_v1",
            )
            with binding.publication_lock:
                with self.assertRaisesRegex(
                    ValueError,
                    "^durable_companion_publication_ack_discarded$",
                ):
                    claim_cursor(ack, binding)

    def test_r19_dynamic_pending_abort_and_close_are_exactly_idempotent(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            before, pending = self._capacity_pending(probe, controller)
            reason = getattr(
                controller_module,
                "PendingEmergencyAbortReasonV1",
            ).CALLER_CLOSE_WITH_PENDING
            receipt = controller.abort_pending_durable_companion_emergency_v1(
                pending,
                reason=reason,
            )
            repeat = controller.abort_pending_durable_companion_emergency_v1(
                pending,
                reason=reason,
            )
            self.assertIs(repeat, receipt)
            self.assertIs(receipt.abort_reason, reason)
            self.assertEqual(receipt.publication_epoch, 1)
            self.assertEqual(probe.abort_count, 1)
            closed = controller.close()
            self.assertIs(closed[0], before[0])
            self.assertIs(closed[1], before[1])
            self.assertIsNone(closed[2])
            again = controller.close()
            self.assertIs(again[0], closed[0])
            self.assertIs(again[1], closed[1])
            self.assertIs(again[2], closed[2])
            self.assertEqual(probe.abort_count, 1)
            self.assertEqual(probe.append_count, 0)

    def test_r19_dynamic_abort_reason_digests_and_value_graph_gc(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        pending_registry = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        )
        reason_type = getattr(
            controller_module,
            "PendingEmergencyAbortReasonV1",
        )
        issue_receipt = getattr(
            controller_module,
            "_issue_pending_abort_receipt_v1",
        )
        with self.mocked_facade(probe):
            controller = self.create_controller()
            _, pending = self._capacity_pending(probe, controller)
            authority = pending_registry.lookup(pending)
            observation = authority.observation
            reason_receipts = tuple(
                issue_receipt(authority, reason)
                for reason in reason_type
            )
            self.assertEqual(
                tuple(receipt.abort_reason for receipt in reason_receipts),
                tuple(reason_type),
            )
            self.assertEqual(
                len(
                    {
                        receipt.receipt_sha256
                        for receipt in reason_receipts
                    }
                ),
                3,
            )
            receipt = (
                controller.abort_pending_durable_companion_emergency_v1(
                    pending,
                    reason=reason_type.RECORDED_CAPACITY_CONTRACT_VIOLATION,
                )
            )
            self.assertEqual(
                receipt.receipt_sha256,
                reason_receipts[1].receipt_sha256,
            )
            graph_references = {
                "observation": weakref.ref(observation),
                "abort_receipt": weakref.ref(receipt),
                "pending": weakref.ref(pending),
            }
            del reason_receipts
            del observation
            del receipt
            del authority
            del pending
        gc.collect()
        self.assertEqual(
            {
                name: reference()
                for name, reference in graph_references.items()
                if reference() is not None
            },
            {},
        )

    def test_r19_dynamic_close_fresh_pending_uses_caller_close_reason(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            before, pending = self._capacity_pending(probe, controller)
            result = controller.close()
            self.assertIs(result[0], before[0])
            self.assertIs(result[1], before[1])
            self.assertIsNone(result[2])
            reason = getattr(
                controller_module,
                "PendingEmergencyAbortReasonV1",
            ).CALLER_CLOSE_WITH_PENDING
            receipt = controller.abort_pending_durable_companion_emergency_v1(
                pending,
                reason=reason,
            )
            self.assertIs(receipt.abort_reason, reason)
            self.assertIs(
                controller.abort_pending_durable_companion_emergency_v1(
                    pending,
                    reason=reason,
                ),
                receipt,
            )
            self.assertEqual(probe.abort_count, 1)
            self.assertEqual(probe.append_count, 0)

    def test_r19_dynamic_legacy_capacity_aborts_before_exported_exception(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        probe.prewrite_capacity_error = self._capacity_error()
        with self.mocked_facade(probe):
            controller = self.create_controller()
            producer, _ = self.enqueue_one()
            error_type = getattr(controller_module, "ExpertCapacityExceeded")
            with self.assertRaises(error_type) as raised:
                controller.process_one(timeout_seconds=1.0)
            producer.join(5)
            self.assertFalse(producer.is_alive())
            self.assertEqual(
                raised.exception.args,
                ("expert_legacy_process_one_capacity_denied",),
            )
            self.assertIsNone(raised.exception.__cause__)
            self.assertEqual(probe.append_count, 0)
            self.assertEqual(probe.emergency_append_count, 0)
            self.assertEqual(probe.abort_count, 1)

    def test_r19_dynamic_parent_token_collision_is_mapped_and_halts(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            before = controller.snapshot()
            parent = self._drain_durable_parent()
            with (
                mock.patch.object(
                    ExpertControllerV1,
                    "_prepare_parent",
                    side_effect=RuntimeError(
                        "durable_ingress_parent_consumed"
                    ),
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "^expert_consumed_parent_processing_failed$",
                ) as raised,
            ):
                controller.process_durable_parent(parent)
            self.assertIsNone(raised.exception.__cause__)
            self.assertIs(controller.snapshot()[0], before[0])
            self.assertIs(controller.snapshot()[1], before[1])
            self.assertIsNone(controller.snapshot()[2])
            self.assertEqual(
                object.__getattribute__(controller, "_lifecycle"),
                "HALTED_UNCLEAN",
            )
            self.assertEqual(probe.append_count, 0)
            self.assertEqual(probe.abort_count, 1)

    def test_r19_dynamic_handoff_unavailable_halts_with_null_snapshot_terminal(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        registry_type = getattr(controller_module, "_WeakIdentityRegistryV1")
        handoff_registry = getattr(
            controller_module,
            "_ORDINARY_TERMINAL_HANDOFFS_V1",
        )
        original_register = registry_type.register

        def fail_handoff_registration(registry, key, value):
            if registry is handoff_registry:
                raise RuntimeError("injected_handoff_registration_failure")
            return original_register(registry, key, value)

        with self.mocked_facade(probe):
            controller = self.create_controller()
            terminal_envelope = self._drain_durable_terminal()
            before = controller.snapshot()
            probe.built_terminal_pair = (
                terminal_envelope.terminal,
                _terminal_for(
                    self.manifest,
                    before[1],
                    terminal_envelope.terminal,
                    clean=True,
                    evidence_reason="operator_stop",
                ),
            )
            with (
                mock.patch.object(
                    registry_type,
                    "register",
                    fail_handoff_registration,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "^durable_companion_terminal_receipt_unavailable$",
                ),
            ):
                controller.process_evidence_terminal(terminal_envelope)
            after = controller.snapshot()
            self.assertIs(after[0], before[0])
            self.assertIs(after[1], before[1])
            self.assertIsNone(after[2])
            self.assertEqual(
                object.__getattribute__(controller, "_lifecycle"),
                "HALTED_UNCLEAN",
            )
            self.assertEqual(probe.terminal_append_count, 1)
            self.assertEqual(probe.abort_count, 1)

    def test_r19_dynamic_ack_closed_duplicates_are_per_bit_in_both_orders(
        self,
    ) -> None:
        orders = (("cursor", "facts"), ("facts", "cursor"))
        for order in orders:
            with self.subTest(order=order), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    ack = controller.process_durable_parent(
                        case._drain_durable_parent()
                    )[2]
                    resolve = getattr(
                        controller_module,
                        "_resolve_durable_companion_publication_ack_for_wave_c_v1",
                    )
                    claims = {
                        "cursor": getattr(
                            controller_module,
                            "_claim_durable_companion_publication_ack_cursor_v1",
                        ),
                        "facts": getattr(
                            controller_module,
                            "_claim_durable_companion_publication_ack_facts_v1",
                        ),
                    }
                    tokens = {
                        "cursor": (
                            "durable_companion_publication_ack_cursor_consumed"
                        ),
                        "facts": (
                            "durable_companion_publication_ack_facts_consumed"
                        ),
                    }
                    binding = resolve(ack)
                    with binding.publication_lock:
                        for name in order:
                            self.assertIsNone(claims[name](ack, binding))
                        for name in ("cursor", "facts"):
                            with self.assertRaisesRegex(
                                ValueError,
                                f"^{tokens[name]}$",
                            ):
                                claims[name](ack, binding)

    def test_r19_dynamic_abort_failure_tombstones_pending_and_releases_for_gc(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            def abort_and_release_pending():
                _, pending = self._capacity_pending(probe, controller)
                pending_reference = weakref.ref(pending)
                reason = getattr(
                    controller_module,
                    "PendingEmergencyAbortReasonV1",
                ).CALLER_CLOSE_WITH_PENDING

                def abort_failure(*_args, **_kwargs):
                    raise RuntimeError(
                        "durable_causal_subject_abort_uncertain"
                    )

                with self._patched_ingress_bridge(
                    "_abort_deferred_emergency_commit_subject_v1",
                    abort_failure,
                ):
                    try:
                        controller.abort_pending_durable_companion_emergency_v1(
                            pending,
                            reason=reason,
                        )
                    except RuntimeError as error:
                        self.assertEqual(
                            str(error),
                            "durable_companion_emergency_pending_abort_uncertain",
                        )
                        self.assertIsNone(error.__cause__)
                    else:
                        self.fail("pending abort uncertainty did not raise")
                tombstone = getattr(
                    controller_module,
                    "_PENDING_EMERGENCY_AUTHORITIES_V1",
                ).lookup(pending)
                self.assertEqual(tombstone.lifecycle, "ABORT_FAILED_CLOSED")
                self.assertIsNone(tombstone.retained_receipt)
                return pending_reference

            pending_reference = abort_and_release_pending()
            self.assertEqual(
                object.__getattribute__(controller, "_lifecycle"),
                "DURABILITY_UNCERTAIN_HALTED",
            )
            self.assertIsNone(controller.snapshot()[2])
            self.assertEqual(probe.abort_count, 1)
            gc.collect()
            self.assertIsNone(pending_reference())

    def test_r19_dynamic_register_then_raise_rolls_back_every_issuer(self) -> None:
        registry_type = getattr(controller_module, "_WeakIdentityRegistryV1")

        def exercise(case, lane: str) -> None:
            probe = _RuledStoreProbe(case.collected)
            target_names = {
                "identity": "_CONTROLLER_IDENTITY_AUTHORITIES_V1",
                "observation": "_CAPACITY_OBSERVATION_AUTHORITIES_V1",
                "pending": "_PENDING_EMERGENCY_AUTHORITIES_V1",
                "ack": "_PUBLICATION_ACK_AUTHORITIES_V1",
                "proof": "_EMERGENCY_PROOF_AUTHORITIES_V1",
                "handoff": "_ORDINARY_TERMINAL_HANDOFFS_V1",
            }
            target = getattr(controller_module, target_names[lane])
            original_register = registry_type.register
            captured: list[tuple[object, object]] = []

            def register_then_raise(registry, key, value):
                result = original_register(registry, key, value)
                if registry is target:
                    captured.append((key, value))
                    raise RuntimeError(f"injected_{lane}_register_after_write")
                return result

            with case.mocked_facade(probe), mock.patch.object(
                registry_type,
                "register",
                new=register_then_raise,
            ):
                if lane == "identity":
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "^injected_identity_register_after_write$",
                    ):
                        case.create_controller()
                else:
                    controller = case.create_controller()
                    if lane in ("observation", "pending"):
                        probe.prewrite_capacity_error = case._capacity_error()
                        parent = case._drain_durable_parent()
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "^durable_companion_capacity_pending_unavailable$",
                        ):
                            controller.process_durable_parent(parent)
                    elif lane == "ack":
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "^durable_companion_publication_ack_unavailable$",
                        ):
                            controller.process_durable_parent(
                                case._drain_durable_parent()
                            )
                    elif lane == "handoff":
                        terminal = case._drain_durable_terminal()
                        before = controller.snapshot()
                        probe.built_terminal_pair = (
                            terminal.terminal,
                            _terminal_for(
                                case.manifest,
                                before[1],
                                terminal.terminal,
                                clean=True,
                                evidence_reason="operator_stop",
                            ),
                        )
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "^durable_companion_terminal_receipt_unavailable$",
                        ):
                            controller.process_evidence_terminal(terminal)
                    else:
                        _, pending = case._capacity_pending(probe, controller)
                        authority, terminal, _ = (
                            case._emergency_terminal_material(probe, pending)
                        )
                        with case._synthetic_causal_seam(pending):
                            with self.assertRaisesRegex(
                                RuntimeError,
                                "^durable_companion_emergency_publication_ack_unavailable$",
                            ):
                                controller.complete_pending_emergency(
                                    pending,
                                    terminal,
                                    object(),
                                )
                        self.assertIsNotNone(authority)
            self.assertEqual(len(captured), 1)
            key, _ = captured[0]
            self.assertIsNone(
                target.lookup(key),
                f"{lane} register-then-raise leaked authority",
            )

        for lane in (
            "identity",
            "observation",
            "pending",
            "ack",
            "proof",
            "handoff",
        ):
            with self.subTest(lane=lane), self.fresh_case() as case:
                exercise(case, lane)

    def test_r19_dynamic_abort_replace_uncertainty_is_atomic_and_sanitized(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        registry_type = getattr(controller_module, "_WeakIdentityRegistryV1")
        pending_registry = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        )
        original_replace = registry_type.replace
        original_finalizer = getattr(
            ingress_module,
            "_abort_deferred_emergency_commit_subject_v1",
        )
        finalizer_calls = 0

        def finalizer(*args, **kwargs):
            nonlocal finalizer_calls
            finalizer_calls += 1
            return original_finalizer(*args, **kwargs)

        def replace_then_raise(registry, key, prior, value):
            result = original_replace(registry, key, prior, value)
            if registry is pending_registry:
                raise RuntimeError("injected_abort_replace_after_write")
            return result

        with self.mocked_facade(probe):
            controller = self.create_controller()
            _, pending = self._capacity_pending(probe, controller)
            reason = getattr(
                controller_module,
                "PendingEmergencyAbortReasonV1",
            ).CALLER_CLOSE_WITH_PENDING
            with (
                self._patched_ingress_bridge(
                    "_abort_deferred_emergency_commit_subject_v1",
                    finalizer,
                ),
                mock.patch.object(
                    registry_type,
                    "replace",
                    new=replace_then_raise,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "^durable_companion_emergency_pending_abort_uncertain$",
                ) as raised,
            ):
                controller.abort_pending_durable_companion_emergency_v1(
                    pending,
                    reason=reason,
                )
            self.assertIsNone(raised.exception.__cause__)
            self.assertEqual(finalizer_calls, 1)
            tombstone = pending_registry.lookup(pending)
            self.assertEqual(tombstone.lifecycle, "ABORT_FAILED_CLOSED")
            self.assertIsNone(tombstone.retained_receipt)
            self.assertEqual(
                object.__getattribute__(controller, "_lifecycle"),
                "DURABILITY_UNCERTAIN_HALTED",
            )

    def test_r19_dynamic_corrupted_ordinary_terminal_receipt_is_rejected(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            terminal_envelope = self._drain_durable_terminal()
            before = controller.snapshot()
            expert_terminal = _terminal_for(
                self.manifest,
                before[1],
                terminal_envelope.terminal,
                clean=True,
                evidence_reason="operator_stop",
            )
            probe.built_terminal_pair = (
                terminal_envelope.terminal,
                expert_terminal,
            )
            controller.process_evidence_terminal(terminal_envelope)
            receipt = probe.last_terminal_receipt
            self.assertIsNotNone(receipt)
            object.__setattr__(
                receipt,
                "durable_end_offset",
                receipt.durable_end_offset + 1,
            )
            claim = getattr(
                controller_module,
                "_claim_ordinary_durable_expert_terminal_receipt_for_alignment_v1",
            )
            with self.assertRaisesRegex(
                ValueError,
                "^durable_companion_ordinary_terminal_receipt_invalid$",
            ):
                claim(controller, expert_terminal)

    def test_r19_dynamic_ordinary_terminal_handoff_rejects_cross_controller(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            terminal_envelope = self._drain_durable_terminal()
            before = controller.snapshot()
            expert_terminal = _terminal_for(
                self.manifest,
                before[1],
                terminal_envelope.terminal,
                clean=True,
                evidence_reason="operator_stop",
            )
            probe.built_terminal_pair = (
                terminal_envelope.terminal,
                expert_terminal,
            )
            controller.process_evidence_terminal(terminal_envelope)
        claim = getattr(
            controller_module,
            "_claim_ordinary_durable_expert_terminal_receipt_for_alignment_v1",
        )
        with self.fresh_case() as other_case:
            with other_case.mocked_facade(
                _RuledStoreProbe(other_case.collected)
            ):
                other_controller = other_case.create_controller()
                with self.assertRaisesRegex(
                    ValueError,
                    "^durable_companion_ordinary_terminal_receipt_invalid$",
                ):
                    claim(other_controller, expert_terminal)

    def test_r19_dynamic_corrupted_emergency_proof_is_rejected(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            _, pending = self._capacity_pending(probe, controller)
            _, terminal, _ = self._emergency_terminal_material(
                probe,
                pending,
            )
            with self._synthetic_causal_seam(pending):
                proof = controller.complete_pending_emergency(
                    pending,
                    terminal,
                    object(),
                )[3]
            object.__setattr__(proof, "group_receipt_sha256", "0" * 64)
            resolve = getattr(
                controller_module,
                "_resolve_durable_companion_emergency_proof_for_wave_c_v1",
            )
            with self.assertRaisesRegex(
                ValueError,
                "^durable_companion_publication_proof_invalid$",
            ):
                resolve(proof)

    def test_r19_dynamic_post_causal_commit_failure_is_ack_unavailable_only(
        self,
    ) -> None:
        module_tree = ast.parse(
            Path(controller_module.__file__).read_text(encoding="utf-8")
        )
        completion = next(
            node
            for node in ast.walk(module_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "complete_pending_emergency"
        )
        commit_statement = next(
            statement
            for statement in ast.walk(completion)
            if isinstance(statement, ast.Expr)
            and any(
                isinstance(call.func, ast.Name)
                and call.func.id
                == "_commit_prepared_durable_causal_precedes_proof_v1"
                for call in ast.walk(statement)
                if isinstance(call, ast.Call)
            )
        )
        commit_barrier = next(
            node
            for node in ast.walk(completion)
            if isinstance(node, ast.With)
            and commit_statement in node.body
        )
        commit_index = next(
            index for index, statement in enumerate(commit_barrier.body)
            if statement is commit_statement
        )
        forbidden_calls = [
            call
            for statement in commit_barrier.body[commit_index + 1 :]
            for call in ast.walk(statement)
            if isinstance(call, ast.Call)
        ]
        self.assertEqual(
            forbidden_calls,
            [],
            "post-causal scalar commit kernel must contain no calls",
        )
        self.assertFalse(
            any(
                isinstance(node, ast.Try)
                and node.handlers
                and node.lineno
                <= commit_statement.lineno
                <= node.end_lineno
                for node in ast.walk(completion)
            ),
            "causal commit must not have an enclosing exception handler",
        )

        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            _, pending = self._capacity_pending(probe, controller)
            _, terminal, _ = self._emergency_terminal_material(
                probe,
                pending,
            )

            with self._synthetic_causal_seam(pending) as calls:
                with (
                    mock.patch.object(
                        controller_module,
                        "_issue_emergency_publication_proof_and_handoff_v1",
                        side_effect=RuntimeError(
                            "injected_post_causal_exposure_failure"
                        ),
                    ),
                    self.assertRaisesRegex(
                        RuntimeError,
                        "^durable_companion_emergency_publication_ack_unavailable$",
                    ) as raised,
                ):
                    controller.complete_pending_emergency(
                        pending,
                        terminal,
                        object(),
                    )
            self.assertIsNone(raised.exception.__cause__)
            self.assertEqual(calls["commit"], 1)
            self.assertEqual(calls["failure_close"], 0)
            self.assertEqual(
                object.__getattribute__(controller, "_lifecycle"),
                "DURABILITY_UNCERTAIN_HALTED",
            )
            state, cursor, expert_terminal = controller.snapshot()
            self.assertIsNotNone(expert_terminal)
            self.assertEqual(state, controller.snapshot()[0])
            self.assertEqual(cursor, controller.snapshot()[1])

    def test_r19_dynamic_ordinary_reconciliation_retains_exact_durable_facts(
        self,
    ) -> None:
        for cut in ("store_ack", "ack_issue"):
            with self.subTest(cut=cut), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    parent = case._drain_durable_parent()
                    if cut == "store_ack":
                        probe.ack_failure = RuntimeError(
                            "injected_store_ack_failure"
                        )
                        expected_token = (
                            "expert_consumed_parent_processing_failed"
                        )
                        patcher = ExitStack()
                    else:
                        expected_token = (
                            "durable_companion_publication_ack_unavailable"
                        )
                        patcher = mock.patch.object(
                            controller_module,
                            "_issue_publication_ack_v1",
                            side_effect=RuntimeError(
                                "injected_ack_issuance_failure"
                            ),
                        )
                    with patcher, self.assertRaisesRegex(
                        RuntimeError,
                        f"^{expected_token}$",
                    ) as raised:
                        controller.process_durable_parent(parent)
                self.assertIsNone(raised.exception.__cause__)
                reconciliation = object.__getattribute__(
                    controller,
                    "_ordinary_reconciliation",
                )
                self.assertIsNotNone(reconciliation)
                self.assertEqual(reconciliation.lane, "PARENT")
                self.assertIs(reconciliation.receipt, probe.last_append_receipt)
                self.assertEqual(
                    reconciliation.durable_end_offset,
                    probe.last_append_receipt.durable_end_offset,
                )
                self.assertEqual(reconciliation.publication_epoch, 1)
                self.assertIs(
                    reconciliation.store_acknowledged,
                    cut == "ack_issue",
                )
                after = controller.snapshot()
                if cut == "store_ack":
                    self.assertIs(after[0], before[0])
                    self.assertIs(after[1], before[1])
                else:
                    self.assertIs(after[0], reconciliation.candidate_state)
                    self.assertIs(after[1], reconciliation.candidate_cursor)
                self.assertIsNone(after[2])
                self.assertEqual(
                    object.__getattribute__(controller, "_lifecycle"),
                    (
                        "DURABILITY_UNCERTAIN_HALTED"
                        if cut == "store_ack"
                        else "HALTED_UNCLEAN"
                    ),
                )

    def test_r19_dynamic_terminal_reconciliation_survives_handoff_cuts(
        self,
    ) -> None:
        registry_type = getattr(controller_module, "_WeakIdentityRegistryV1")
        handoff_registry = getattr(
            controller_module,
            "_ORDINARY_TERMINAL_HANDOFFS_V1",
        )
        original_register = registry_type.register
        for timing in ("before_write", "after_write"):
            with self.subTest(timing=timing), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    terminal = case._drain_durable_terminal()
                    expert_terminal = _terminal_for(
                        case.manifest,
                        before[1],
                        terminal.terminal,
                        clean=True,
                        evidence_reason="operator_stop",
                    )
                    probe.built_terminal_pair = (
                        terminal.terminal,
                        expert_terminal,
                    )

                    def fail_handoff_register(registry, key, value):
                        if registry is not handoff_registry:
                            return original_register(registry, key, value)
                        if timing == "after_write":
                            original_register(registry, key, value)
                        raise RuntimeError(
                            f"injected_terminal_handoff_{timing}"
                        )

                    with (
                        mock.patch.object(
                            registry_type,
                            "register",
                            new=fail_handoff_register,
                        ),
                        self.assertRaisesRegex(
                            RuntimeError,
                            "^durable_companion_terminal_receipt_unavailable$",
                        ) as raised,
                    ):
                        controller.process_evidence_terminal(terminal)
                self.assertIsNone(raised.exception.__cause__)
                reconciliation = object.__getattribute__(
                    controller,
                    "_ordinary_reconciliation",
                )
                self.assertEqual(reconciliation.lane, "TERMINAL")
                self.assertIs(reconciliation.receipt, probe.last_terminal_receipt)
                self.assertIs(reconciliation.candidate_state, before[0])
                self.assertIs(reconciliation.candidate_cursor, before[1])
                self.assertIs(reconciliation.expert_terminal, expert_terminal)
                self.assertIsNone(handoff_registry.lookup(terminal))
                self.assertEqual(controller.snapshot(), before)
                self.assertEqual(
                    object.__getattribute__(controller, "_lifecycle"),
                    "HALTED_UNCLEAN",
                )

    def test_r19_dynamic_precommit_failure_close_replace_converges(
        self,
    ) -> None:
        registry_type = getattr(controller_module, "_WeakIdentityRegistryV1")
        pending_registry = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        )
        original_replace = registry_type.replace
        for timing in ("before_write", "after_write"):
            with self.subTest(timing=timing), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before, pending = case._capacity_pending(probe, controller)
                    authority, terminal, _ = case._emergency_terminal_material(
                        probe,
                        pending,
                    )
                    probe.emergency_failure = RuntimeError(
                        "injected_precommit_append_failure"
                    )
                    injected = False

                    def fail_close_replace(registry, key, prior, value):
                        nonlocal injected
                        if (
                            registry is pending_registry
                            and key is pending
                            and not injected
                            and getattr(value, "lifecycle", None)
                            == "PUBLICATION_FAILED_CLOSED"
                        ):
                            injected = True
                            if timing == "after_write":
                                original_replace(registry, key, prior, value)
                            raise RuntimeError(
                                f"injected_failure_close_replace_{timing}"
                            )
                        return original_replace(registry, key, prior, value)

                    with case._synthetic_causal_seam(pending) as calls:
                        with (
                            mock.patch.object(
                                registry_type,
                                "replace",
                                new=fail_close_replace,
                            ),
                            self.assertRaisesRegex(
                                RuntimeError,
                                "^durable_companion_emergency_publication_uncertain$",
                            ) as raised,
                        ):
                            controller.complete_pending_emergency(
                                pending,
                                terminal,
                                object(),
                            )
                self.assertIsNone(raised.exception.__cause__)
                self.assertTrue(injected)
                self.assertEqual(calls, {"commit": 0, "failure_close": 1})
                tombstone = pending_registry.lookup(pending)
                self.assertEqual(
                    tombstone.lifecycle,
                    "PUBLICATION_FAILED_CLOSED",
                )
                self.assertIsNone(tombstone.retained_receipt)
                self.assertIsNone(authority.active_completion_scope)
                self.assertIsNone(authority.retry_subject)
                self.assertEqual(controller.snapshot(), before)
                self.assertEqual(
                    object.__getattribute__(controller, "_lifecycle"),
                    "DURABILITY_UNCERTAIN_HALTED",
                )

    def test_r19_dynamic_postcommit_replace_and_exposure_cuts_converge(
        self,
    ) -> None:
        registry_type = getattr(controller_module, "_WeakIdentityRegistryV1")
        pending_registry = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        )
        proof_registry = getattr(
            controller_module,
            "_EMERGENCY_PROOF_AUTHORITIES_V1",
        )
        handoff_registry = getattr(
            controller_module,
            "_EMERGENCY_TERMINAL_HANDOFFS_V1",
        )
        original_replace = registry_type.replace
        original_register = registry_type.register
        cuts = (
            "published_replace_before_write",
            "published_replace_after_write",
            "proof_register_before_write",
            "proof_register_after_write",
            "handoff_register_before_write",
            "handoff_register_after_write",
        )
        for cut in cuts:
            with self.subTest(cut=cut), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                captured: list[object] = []
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    _, pending = case._capacity_pending(probe, controller)
                    authority, terminal, expert_terminal = (
                        case._emergency_terminal_material(probe, pending)
                    )
                    injected = False

                    def fail_replace(registry, key, prior, value):
                        nonlocal injected
                        if (
                            cut.startswith("published_replace")
                            and registry is pending_registry
                            and key is pending
                            and not injected
                            and getattr(value, "lifecycle", None) == "PUBLISHED"
                        ):
                            injected = True
                            if cut.endswith("after_write"):
                                original_replace(registry, key, prior, value)
                            raise RuntimeError(f"injected_{cut}")
                        return original_replace(registry, key, prior, value)

                    def fail_register(registry, key, value):
                        nonlocal injected
                        target = (
                            proof_registry
                            if cut.startswith("proof_register")
                            else handoff_registry
                        )
                        if (
                            not cut.startswith("published_replace")
                            and registry is target
                            and not injected
                        ):
                            injected = True
                            captured.append(key)
                            if cut.endswith("after_write"):
                                original_register(registry, key, value)
                            raise RuntimeError(f"injected_{cut}")
                        return original_register(registry, key, value)

                    with case._synthetic_causal_seam(pending) as calls:
                        with (
                            mock.patch.object(
                                registry_type,
                                "replace",
                                new=fail_replace,
                            ),
                            mock.patch.object(
                                registry_type,
                                "register",
                                new=fail_register,
                            ),
                            self.assertRaisesRegex(
                                RuntimeError,
                                "^durable_companion_emergency_publication_ack_unavailable$",
                            ) as raised,
                        ):
                            controller.complete_pending_emergency(
                                pending,
                                terminal,
                                object(),
                            )
                self.assertIsNone(raised.exception.__cause__)
                self.assertTrue(injected)
                self.assertEqual(calls, {"commit": 1, "failure_close": 0})
                tombstone = pending_registry.lookup(pending)
                self.assertEqual(
                    tombstone.lifecycle,
                    "PUBLISHED_PROOF_UNAVAILABLE_CLOSED",
                )
                self.assertIsNone(authority.active_completion_scope)
                self.assertIsNone(authority.retry_subject)
                state, cursor, terminal_snapshot = controller.snapshot()
                self.assertIs(state, authority.emergency_candidate_state)
                self.assertIs(cursor, authority.emergency_candidate_cursor)
                self.assertIs(terminal_snapshot, expert_terminal)
                self.assertEqual(
                    object.__getattribute__(controller, "_lifecycle"),
                    "DURABILITY_UNCERTAIN_HALTED",
                )
                for proof in captured:
                    self.assertIsNone(proof_registry.lookup(proof))
                    self.assertIsNone(handoff_registry.lookup(proof))

    def test_r19_dynamic_normal_abort_replace_converges_before_or_after_write(
        self,
    ) -> None:
        registry_type = getattr(controller_module, "_WeakIdentityRegistryV1")
        pending_registry = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        )
        original_replace = registry_type.replace
        reason = getattr(
            controller_module,
            "PendingEmergencyAbortReasonV1",
        ).CALLER_CLOSE_WITH_PENDING
        for timing in ("before_write", "after_write"):
            with self.subTest(timing=timing), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    _, pending = case._capacity_pending(probe, controller)
                    injected = False

                    def fail_success_replace(registry, key, prior, value):
                        nonlocal injected
                        if (
                            registry is pending_registry
                            and key is pending
                            and not injected
                            and getattr(value, "lifecycle", None)
                            == "ABORTED_NONPUBLICATION"
                        ):
                            injected = True
                            if timing == "after_write":
                                original_replace(registry, key, prior, value)
                            raise RuntimeError(
                                f"injected_abort_success_replace_{timing}"
                            )
                        return original_replace(registry, key, prior, value)

                    with (
                        mock.patch.object(
                            registry_type,
                            "replace",
                            new=fail_success_replace,
                        ),
                        self.assertRaisesRegex(
                            RuntimeError,
                            "^durable_companion_emergency_pending_abort_uncertain$",
                        ) as raised,
                    ):
                        controller.abort_pending_durable_companion_emergency_v1(
                            pending,
                            reason=reason,
                        )
                self.assertIsNone(raised.exception.__cause__)
                self.assertTrue(injected)
                tombstone = pending_registry.lookup(pending)
                self.assertEqual(tombstone.lifecycle, "ABORT_FAILED_CLOSED")
                self.assertIsNone(tombstone.retained_receipt)
                self.assertEqual(
                    object.__getattribute__(controller, "_lifecycle"),
                    "DURABILITY_UNCERTAIN_HALTED",
                )
                self.assertEqual(probe.abort_count, 1)

    def test_r19_dynamic_observation_to_pending_boundary_rolls_back_atomically(
        self,
    ) -> None:
        registry_type = getattr(controller_module, "_WeakIdentityRegistryV1")
        observation_registry = getattr(
            controller_module,
            "_CAPACITY_OBSERVATION_AUTHORITIES_V1",
        )
        pending_registry = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        )
        original_register = registry_type.register
        original_replace = registry_type.replace
        cuts = (
            "pending_register_before_write",
            "pending_register_after_write",
            "observation_replace_before_write",
            "observation_replace_after_write",
        )
        for cut in cuts:
            with self.subTest(cut=cut), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                probe.prewrite_capacity_error = case._capacity_error()
                observations: list[object] = []
                pendings: list[object] = []
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    parent = case._drain_durable_parent()

                    def cut_register(registry, key, value):
                        if registry is observation_registry:
                            observations.append(key)
                        if registry is pending_registry:
                            pendings.append(key)
                            if cut.startswith("pending_register"):
                                if cut.endswith("after_write"):
                                    original_register(registry, key, value)
                                raise RuntimeError(f"injected_{cut}")
                        return original_register(registry, key, value)

                    def cut_replace(registry, key, prior, value):
                        if (
                            registry is observation_registry
                            and cut.startswith("observation_replace")
                        ):
                            if cut.endswith("after_write"):
                                original_replace(registry, key, prior, value)
                            raise RuntimeError(f"injected_{cut}")
                        return original_replace(registry, key, prior, value)

                    with (
                        mock.patch.object(
                            registry_type,
                            "register",
                            new=cut_register,
                        ),
                        mock.patch.object(
                            registry_type,
                            "replace",
                            new=cut_replace,
                        ),
                        self.assertRaisesRegex(
                            RuntimeError,
                            "^durable_companion_capacity_pending_unavailable$",
                        ) as raised,
                    ):
                        controller.process_durable_parent(parent)
                self.assertIsNone(raised.exception.__cause__)
                self.assertEqual(controller.snapshot(), before)
                self.assertEqual(
                    object.__getattribute__(controller, "_publication_epoch"),
                    0,
                )
                self.assertIsNone(
                    object.__getattribute__(controller, "_active_pending")
                )
                for observation in observations:
                    self.assertIsNone(observation_registry.lookup(observation))
                for pending in pendings:
                    self.assertIsNone(pending_registry.lookup(pending))

        tree = ast.parse(
            Path(controller_module.__file__).read_text(encoding="utf-8")
        )
        issuer = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_issue_pending_durable_companion_emergency_v1"
        )
        issuance_try = next(
            node
            for node in ast.walk(issuer)
            if isinstance(node, ast.Try)
            and any(
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "replace"
                for statement in node.body
                for call in ast.walk(statement)
                if isinstance(call, ast.Call)
            )
        )
        replace_index = next(
            index
            for index, statement in enumerate(issuance_try.body)
            if any(
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "replace"
                for call in ast.walk(statement)
                if isinstance(call, ast.Call)
            )
        )
        scalar_calls = [
            call
            for statement in issuance_try.body[replace_index + 1 :]
            for call in ast.walk(statement)
            if isinstance(call, ast.Call)
        ]
        self.assertEqual(
            scalar_calls,
            [],
            "Observation-to-Pending scalar commit kernel must contain no calls",
        )

    def test_r19_dynamic_consumed_observation_precedes_candidate_validation(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            _, pending = self._capacity_pending(probe, controller)
            authority = getattr(
                controller_module,
                "_PENDING_EMERGENCY_AUTHORITIES_V1",
            ).lookup(pending)
            issue_pending = getattr(
                controller_module,
                "_issue_pending_durable_companion_emergency_v1",
            )
            for malformed_state, malformed_cursor in (
                (object(), authority.denied_candidate_cursor),
                (authority.denied_candidate_state, object()),
                (object(), object()),
            ):
                with self.subTest(
                    state=type(malformed_state).__name__,
                    cursor=type(malformed_cursor).__name__,
                ), self.assertRaisesRegex(
                    ValueError,
                    "^durable_companion_capacity_denial_observation_consumed$",
                ):
                    issue_pending(
                        authority.observation,
                        candidate_state=malformed_state,
                        candidate_cursor=malformed_cursor,
                    )

    def test_r19_dynamic_resolver_and_lock_barrier_are_structurally_complete(
        self,
    ) -> None:
        tree = ast.parse(
            Path(controller_module.__file__).read_text(encoding="utf-8")
        )
        completion = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "complete_pending_emergency"
        )
        resolver_lines = [
            call.lineno
            for call in ast.walk(completion)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_resolve_deferred_emergency_subject_inputs_v1"
        ]
        outer_with = next(
            node
            for node in ast.walk(completion)
            if isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Name)
                and item.context_expr.id == "publication_lock"
                for item in node.items
            )
        )
        inner_with = next(
            node
            for node in ast.walk(outer_with)
            if node is not outer_with
            and isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Attribute)
                and item.context_expr.attr == "_causal_subject_lock"
                for item in node.items
            )
        )
        guarded_names = {
            (
                call.func.id
                if isinstance(call.func, ast.Name)
                else call.func.attr
            )
            for call in ast.walk(inner_with)
            if isinstance(call, ast.Call)
            and isinstance(call.func, (ast.Name, ast.Attribute))
        }
        self.assertIn(
            "_prepare_reserved_emergency_before_causal_commit_v1",
            guarded_names,
        )
        self.assertIn(
            "_commit_prepared_durable_causal_precedes_proof_v1",
            guarded_names,
        )
        self.assertLess(
            min(resolver_lines),
            outer_with.lineno,
        )
        commit_statement = next(
            statement
            for statement in inner_with.body
            if any(
                isinstance(call.func, ast.Name)
                and call.func.id
                == "_commit_prepared_durable_causal_precedes_proof_v1"
                for call in ast.walk(statement)
                if isinstance(call, ast.Call)
            )
        )
        self.assertFalse(
            any(
                isinstance(node, ast.Try)
                and node.handlers
                and node.lineno
                <= commit_statement.lineno
                <= node.end_lineno
                for node in ast.walk(completion)
            )
        )
        exposure = next(
            call
            for call in ast.walk(completion)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id
            == "_issue_emergency_publication_proof_and_handoff_v1"
        )
        self.assertGreater(exposure.lineno, inner_with.end_lineno)

        prepare_helper = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name
            == "_prepare_reserved_emergency_before_causal_commit_v1"
        )
        prepare_calls = {
            (
                call.func.id
                if isinstance(call.func, ast.Name)
                else call.func.attr
            )
            for call in ast.walk(prepare_helper)
            if isinstance(call, ast.Call)
            and isinstance(call.func, (ast.Name, ast.Attribute))
        }
        self.assertTrue(
            {
                "_resolve_deferred_emergency_subject_inputs_v1",
                "_consume_deferred_emergency_source_close_claim_v1",
                "_prepare_durable_causal_precedes_proof_commit_v1",
                "append_expert_emergency_group_and_terminal",
                "_converge_reserved_emergency_failure_close_v1",
            }.issubset(prepare_calls)
        )

    def test_r19_dynamic_identity_and_epoch_exhaustion_are_fail_closed(
        self,
    ) -> None:
        identity_registry = getattr(
            controller_module,
            "_CONTROLLER_IDENTITY_AUTHORITIES_V1",
        )
        with self.mocked_facade(_RuledStoreProbe(self.collected)):
            controller = self.create_controller()
        identity = object.__getattribute__(controller, "_controller_identity")
        self.assertIsNotNone(identity_registry.lookup(identity))
        rebuilt = object.__new__(type(identity))
        for slot in type(identity).__slots__:
            if slot != "__weakref__":
                object.__setattr__(rebuilt, slot, getattr(identity, slot))
        validate_identity = getattr(
            controller_module,
            "_controller_identity_public_fields_valid_v1",
        )
        self.assertTrue(validate_identity(identity))
        self.assertFalse(validate_identity(rebuilt))

        coordinate_name = "_CONTROLLER_IDENTITY_ALLOCATION_COORDINATE_V1"
        signed_max = getattr(controller_module, "_SIGNED_63_MAX")
        with self.fresh_case() as exhausted_case:
            exhausted_probe = _RuledStoreProbe(exhausted_case.collected)
            with exhausted_case.mocked_facade(exhausted_probe), mock.patch.object(
                controller_module,
                coordinate_name,
                signed_max,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "^expert_controller_identity_exhausted$",
                ) as raised:
                    exhausted_case.create_controller()
                self.assertIsNone(raised.exception.__cause__)
                self.assertEqual(exhausted_probe.abort_count, 1)
                self.assertEqual(
                    getattr(controller_module, coordinate_name),
                    signed_max,
                )

        with self.fresh_case() as epoch_case:
            epoch_probe = _RuledStoreProbe(epoch_case.collected)
            with epoch_case.mocked_facade(epoch_probe):
                epoch_controller = epoch_case.create_controller()
                before = epoch_controller.snapshot()
                parent = epoch_case._drain_durable_parent()
                object.__setattr__(
                    epoch_controller,
                    "_publication_epoch",
                    signed_max,
                )
                with self.assertRaisesRegex(
                    RuntimeError,
                    "^durable_companion_publication_epoch_exhausted$",
                ) as raised:
                    epoch_controller.process_durable_parent(parent)
                self.assertIsNone(raised.exception.__cause__)
                getattr(
                    ingress_module,
                    "_validate_durable_ingress_parent_for_consumer_v1",
                )(parent, epoch_controller)
            self.assertEqual(epoch_controller.snapshot(), before)
            self.assertEqual(epoch_probe.append_count, 0)
            self.assertEqual(epoch_probe.abort_count, 1)

    def test_r19_dynamic_capacity_fact_rejection_matrix_is_sanitized(
        self,
    ) -> None:
        cases = (
            ("partial", (None, 1, EXPERT_EMERGENCY_RESERVE_BYTES)),
            ("bool", (True, 1, EXPERT_EMERGENCY_RESERVE_BYTES)),
            ("negative", (-1, 1, EXPERT_EMERGENCY_RESERVE_BYTES)),
            (
                "overflow",
                (9_223_372_036_854_775_808, 1, EXPERT_EMERGENCY_RESERVE_BYTES),
            ),
            ("reserve", (4096, 1024, EXPERT_EMERGENCY_RESERVE_BYTES - 1)),
        )
        for name, values in cases:
            with self.subTest(name=name), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                error = ExpertPrewriteCapacityError(
                    requested_bytes=4096,
                    available_bytes=1024,
                    emergency_reserve_bytes=EXPERT_EMERGENCY_RESERVE_BYTES,
                )
                for field_name, value in zip(
                    (
                        "requested_bytes",
                        "available_bytes",
                        "emergency_reserve_bytes",
                    ),
                    values,
                    strict=True,
                ):
                    object.__setattr__(error, field_name, value)
                probe.prewrite_capacity_error = error
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "^expert_capacity_observation_invalid$",
                    ) as raised:
                        controller.process_durable_parent(
                            case._drain_durable_parent()
                        )
                self.assertIsNone(raised.exception.__cause__)
                self.assertEqual(controller.snapshot(), before)
                self.assertEqual(
                    object.__getattribute__(controller, "_publication_epoch"),
                    0,
                )
                self.assertEqual(probe.append_count, 0)
                self.assertEqual(probe.emergency_append_count, 0)
                self.assertEqual(probe.abort_count, 1)
                self.assertIsNone(error.__traceback__)
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)

    def test_r19_dynamic_unseen_tail_consumes_terminal_and_never_catches_up(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            before = controller.snapshot()
            terminal = self._drain_durable_terminal()
            probe.tail_result = object()
            with self.assertRaisesRegex(
                ValueError,
                "^expert_unacknowledged_evidence_tail$",
            ) as raised:
                controller.process_evidence_terminal(terminal)
            self.assertIsNone(raised.exception.__cause__)
            with self.assertRaisesRegex(
                ValueError,
                "^durable_evidence_terminal_consumed$",
            ):
                getattr(
                    ingress_module,
                    "_validate_durable_evidence_terminal_for_consumer_v1",
                )(terminal, controller)
        self.assertEqual(controller.snapshot(), before)
        self.assertEqual(probe.append_count, 0)
        self.assertEqual(probe.terminal_append_count, 0)
        self.assertEqual(probe.abort_count, 1)
        self.assertEqual(
            object.__getattribute__(controller, "_lifecycle"),
            "HALTED_UNCLEAN",
        )

    def test_r19_dynamic_ordinary_terminal_claim_negative_and_one_shot_matrix(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            terminal = self._drain_durable_terminal()
            before = controller.snapshot()
            expert_terminal = _terminal_for(
                self.manifest,
                before[1],
                terminal.terminal,
                clean=True,
                evidence_reason="operator_stop",
            )
            probe.built_terminal_pair = (terminal.terminal, expert_terminal)
            published = controller.process_evidence_terminal(terminal)[2]
            claim = getattr(
                controller_module,
                "_claim_ordinary_durable_expert_terminal_receipt_for_alignment_v1",
            )
            with self.assertRaisesRegex(
                TypeError,
                "^exact ExpertControllerV1 required$",
            ):
                claim(object(), published)
            with self.assertRaisesRegex(
                TypeError,
                "^exact ExpertSessionTerminalV1 required$",
            ):
                claim(controller, object())
            rebuilt = replace(published)
            with self.assertRaisesRegex(
                ValueError,
                "^durable_companion_ordinary_terminal_receipt_invalid$",
            ):
                claim(controller, rebuilt)
            receipt = claim(controller, published)
            self.assertIs(receipt, probe.last_terminal_receipt)
            with self.assertRaisesRegex(
                ValueError,
                "^durable_companion_ordinary_terminal_receipt_consumed$",
            ):
                claim(controller, published)

    def test_r19_dynamic_ack_claim_discard_and_gc_matrix(self) -> None:
        resolve = getattr(
            controller_module,
            "_resolve_durable_companion_publication_ack_for_wave_c_v1",
        )
        claim_cursor = getattr(
            controller_module,
            "_claim_durable_companion_publication_ack_cursor_v1",
        )
        claim_facts = getattr(
            controller_module,
            "_claim_durable_companion_publication_ack_facts_v1",
        )
        discard = getattr(
            controller_module,
            "_discard_legacy_companion_publication_ack_v1",
        )
        registry = getattr(controller_module, "_PUBLICATION_ACK_AUTHORITIES_V1")

        with self.fresh_case() as discard_case:
            probe = _RuledStoreProbe(discard_case.collected)
            with discard_case.mocked_facade(probe):
                controller = discard_case.create_controller()
                ack = controller.process_durable_parent(
                    discard_case._drain_durable_parent()
                )[2]
                discard(controller, ack)
                discard(controller, ack)
                with self.assertRaisesRegex(
                    ValueError,
                    "^durable_companion_publication_ack_discarded$",
                ):
                    resolve(ack)
                discarded_ack_ref = weakref.ref(ack)
                del ack
            gc.collect()
            self.assertIsNone(discarded_ack_ref())

        with self.fresh_case() as facts_only_case:
            probe = _RuledStoreProbe(facts_only_case.collected)
            with facts_only_case.mocked_facade(probe):
                controller = facts_only_case.create_controller()
                ack = controller.process_durable_parent(
                    facts_only_case._drain_durable_parent()
                )[2]
                binding = resolve(ack)
                with binding.publication_lock:
                    claim_facts(ack, binding)
                self.assertFalse(binding.authority.cursor_issued)
                self.assertTrue(binding.authority.facts_claimed)
                self.assertEqual(binding.authority.lifecycle, "ACTIVE")
                with self.assertRaisesRegex(
                    ValueError,
                    "^durable_companion_publication_ack_invalid$",
                ):
                    discard(controller, ack)
                self.assertFalse(binding.authority.cursor_issued)
                self.assertTrue(binding.authority.facts_claimed)
                self.assertEqual(binding.authority.lifecycle, "ACTIVE")

        with self.fresh_case() as claimed_case:
            probe = _RuledStoreProbe(claimed_case.collected)
            with claimed_case.mocked_facade(probe):
                controller = claimed_case.create_controller()
                ack = controller.process_durable_parent(
                    claimed_case._drain_durable_parent()
                )[2]
                binding = resolve(ack)
                with binding.publication_lock:
                    claim_cursor(ack, binding)
                    with self.assertRaisesRegex(
                        ValueError,
                        "^durable_companion_publication_ack_invalid$",
                    ):
                        discard(controller, ack)
                    claim_facts(ack, binding)
                tombstone = registry.lookup(ack)
                self.assertEqual(tombstone.lifecycle, "BOTH_CLAIMED_CLOSED")
                ack_ref = weakref.ref(ack)
                del binding
                del ack
            gc.collect()
            self.assertIsNone(ack_ref())

    def test_r19_dynamic_emergency_proof_projection_handoff_and_gc_matrix(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        proof_registry = getattr(
            controller_module,
            "_EMERGENCY_PROOF_AUTHORITIES_V1",
        )
        handoff_registry = getattr(
            controller_module,
            "_EMERGENCY_TERMINAL_HANDOFFS_V1",
        )
        resolve = getattr(
            controller_module,
            "_resolve_durable_companion_emergency_proof_for_wave_c_v1",
        )
        consume_projection = getattr(
            controller_module,
            "_claim_durable_companion_emergency_proof_projection_v1",
        )
        claim_terminal = getattr(
            controller_module,
            "_claim_emergency_durable_expert_terminal_receipt_for_alignment_v1",
        )
        with self.mocked_facade(probe):
            controller = self.create_controller()
            _, pending = self._capacity_pending(probe, controller)
            authority, terminal, expected_terminal = (
                self._emergency_terminal_material(probe, pending)
            )
            denied_state_sha256 = expert_state_sha256(
                authority.denied_candidate_state
            )
            denied_cursor_sha256 = (
                controller_module._expert_cursor_sha256_v1(
                    authority.denied_candidate_cursor
                )
            )
            rejection_state_sha256 = expert_state_sha256(
                authority.emergency_candidate_state
            )
            rejection_cursor_sha256 = (
                controller_module._expert_cursor_sha256_v1(
                    authority.emergency_candidate_cursor
                )
            )
            with self._synthetic_causal_seam(pending):
                result = controller.complete_pending_emergency(
                    pending,
                    terminal,
                    object(),
                )
            returned_state, returned_cursor, returned_terminal, proof = result
            self.assertIs(returned_state, authority.emergency_candidate_state)
            self.assertIs(returned_cursor, authority.emergency_candidate_cursor)
            self.assertIs(returned_terminal, expected_terminal)
            self.assertIsNot(returned_state, authority.denied_candidate_state)
            self.assertIsNot(returned_cursor, authority.denied_candidate_cursor)
            self.assertEqual(
                proof.candidate_state_sha256,
                rejection_state_sha256,
            )
            self.assertEqual(
                proof.candidate_cursor_sha256,
                rejection_cursor_sha256,
            )
            self.assertNotEqual(
                proof.candidate_state_sha256,
                denied_state_sha256,
            )
            self.assertNotEqual(
                proof.candidate_cursor_sha256,
                denied_cursor_sha256,
            )
            self.assertEqual(
                returned_terminal.final_expert_state_sha256,
                rejection_state_sha256,
            )
            self.assertNotEqual(
                returned_terminal.final_expert_state_sha256,
                denied_state_sha256,
            )
            self.assertEqual(
                (
                    returned_terminal.expert_group_count,
                    returned_terminal.expert_record_count,
                    returned_terminal.last_parent_ingest_seq,
                    returned_terminal.last_parent_record_sha256,
                    returned_terminal.final_expert_seq,
                    returned_terminal.final_expert_record_sha256,
                    returned_terminal.final_expert_state_sha256,
                    returned_terminal.final_expert_trace_sha256,
                ),
                (
                    returned_cursor.group_count,
                    returned_cursor.record_count,
                    returned_cursor.last_parent_ingest_seq,
                    returned_cursor.last_parent_record_sha256,
                    returned_cursor.expert_seq,
                    returned_cursor.expert_record_sha256,
                    returned_cursor.expert_state_sha256,
                    returned_cursor.expert_trace_sha256,
                ),
            )
            emergency_receipt = probe.last_emergency_receipt
            self.assertIsNotNone(emergency_receipt)
            terminal_receipt = emergency_receipt.terminal_receipt
            self.assertEqual(
                proof.terminal_receipt_sha256,
                controller_module._durable_expert_terminal_receipt_sha256_v1(
                    terminal_receipt
                ),
            )
            self.assertEqual(
                proof.terminal_sha256,
                terminal_receipt.terminal_sha256,
            )
            self.assertEqual(
                terminal_receipt.terminal_sha256,
                returned_terminal.terminal_sha256,
            )
            binding = resolve(proof)
            with binding.publication_lock:
                consume_projection(proof, binding)
            proof_tombstone = proof_registry.lookup(proof)
            self.assertEqual(
                proof_tombstone.lifecycle,
                "PROJECTION_CONSUMED",
            )
            receipt = claim_terminal(controller, proof)
            self.assertIs(receipt, probe.last_emergency_receipt.terminal_receipt)
            with self.assertRaisesRegex(
                ValueError,
                "^durable_companion_emergency_terminal_receipt_consumed$",
            ):
                claim_terminal(controller, proof)
            self.assertEqual(
                handoff_registry.lookup(proof).lifecycle,
                "CLAIMED_BY_WAVE_C",
            )
            with self.assertRaisesRegex(
                ValueError,
                "^durable_companion_emergency_proof_consumed$",
            ):
                resolve(proof)
            proof_ref = weakref.ref(proof)
            del binding
            del proof
            del result
        gc.collect()
        self.assertIsNone(proof_ref())

    def test_r19_dynamic_retry_subject_and_optional_abort_pair_matrix(self) -> None:
        probe = _RuledStoreProbe(self.collected)
        reason = getattr(
            controller_module,
            "PendingEmergencyAbortReasonV1",
        ).RECORDED_CAPACITY_CONTRACT_VIOLATION
        retained_subject = object()
        issued_subjects: list[object] = []
        abort_pairs: list[tuple[object | None, object | None]] = []
        with self.mocked_facade(probe):
            controller = self.create_controller()
            _, pending = self._capacity_pending(probe, controller)
            authority, terminal, _ = self._emergency_terminal_material(
                probe,
                pending,
            )

            def issue_subject(*, controller, pending, terminal):
                subject = authority.retry_subject or retained_subject
                issued_subjects.append(subject)
                return subject

            def fail_before_reservation(claim, subject):
                self.assertIs(subject, retained_subject)
                raise RuntimeError("injected_pre_reservation_failure")

            for attempt in range(2):
                with (
                    self._patched_ingress_bridge(
                        "_issue_deferred_emergency_commit_subject_v1",
                        issue_subject,
                    ),
                    self._patched_ingress_bridge(
                        "_consume_deferred_emergency_source_close_claim_v1",
                        fail_before_reservation,
                    ),
                    self.assertRaisesRegex(
                        RuntimeError,
                        "^injected_pre_reservation_failure$",
                    ),
                ):
                    controller.complete_pending_emergency(
                        pending,
                        terminal,
                        object(),
                    )
                self.assertEqual(
                    authority.active_completion_scope.lifecycle,
                    "CLEARED",
                )
                self.assertIs(authority.retry_subject, retained_subject)
                self.assertEqual(len(issued_subjects), attempt + 1)

            def abort_subject(subject, target, value, evidence_terminal):
                self.assertIs(target, controller)
                self.assertIs(value, pending)
                abort_pairs.append((subject, evidence_terminal))

            with self._patched_ingress_bridge(
                "_abort_deferred_emergency_commit_subject_v1",
                abort_subject,
            ):
                receipt = (
                    controller.abort_pending_durable_companion_emergency_v1(
                        pending,
                        reason=reason,
                    )
                )
            self.assertIs(receipt.abort_reason, reason)
            self.assertEqual(abort_pairs, [(retained_subject, terminal)])
            self.assertEqual(issued_subjects, [retained_subject, retained_subject])
            self.assertIsNone(authority.retry_subject)

    def test_r19_dynamic_parent_postconsume_failure_matrix(self) -> None:
        cuts = (
            "authorize",
            "normalize",
            "reduce",
            "build",
            "encode",
            "reconciliation",
            "permit",
            "append",
            "receipt_validation",
            "acknowledge",
            "ack_issue",
        )
        for cut in cuts:
            with self.subTest(cut=cut), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    parent = case._drain_durable_parent()
                    stack = ExitStack()
                    if cut == "authorize":
                        stack.enter_context(
                            mock.patch.object(
                                ExpertControllerV1,
                                "_authorize_parent",
                                side_effect=RuntimeError(
                                    "injected_parent_authorize_failure"
                                ),
                            )
                        )
                    elif cut == "normalize":
                        stack.enter_context(
                            mock.patch.object(
                                controller_module,
                                "normalize_expert_parent",
                                side_effect=RuntimeError(
                                    "injected_parent_normalize_failure"
                                ),
                            )
                        )
                    elif cut == "reduce":
                        stack.enter_context(
                            mock.patch.object(
                                controller_module,
                                "reduce_expert_parent",
                                side_effect=RuntimeError(
                                    "injected_parent_reduce_failure"
                                ),
                            )
                        )
                    elif cut == "build":
                        stack.enter_context(
                            mock.patch.object(
                                controller_module,
                                "_build_group",
                                side_effect=RuntimeError(
                                    "injected_parent_build_failure"
                                ),
                            )
                        )
                    elif cut == "encode":
                        stack.enter_context(
                            mock.patch.object(
                                controller_module,
                                "encode_expert_group_frame",
                                side_effect=RuntimeError(
                                    "injected_parent_encode_failure"
                                ),
                            )
                        )
                    elif cut == "reconciliation":
                        stack.enter_context(
                            mock.patch.object(
                                controller_module,
                                "_DefinitelyDurableOrdinaryReconciliationV1",
                                side_effect=MemoryError(
                                    "injected_parent_reconciliation_failure"
                                ),
                            )
                        )
                    elif cut == "permit":
                        stack.enter_context(
                            mock.patch.object(
                                controller_module,
                                "issue_expert_append_permit",
                                side_effect=RuntimeError(
                                    "injected_parent_permit_failure"
                                ),
                            )
                        )
                    elif cut == "append":
                        probe.append_failure = RuntimeError(
                            "injected_parent_append_ambiguity"
                        )
                    elif cut == "receipt_validation":
                        stack.enter_context(
                            mock.patch.object(
                                controller_module,
                                "_validate_append_receipt",
                                side_effect=RuntimeError(
                                    "injected_parent_receipt_validation_failure"
                                ),
                            )
                        )
                    elif cut == "acknowledge":
                        probe.ack_failure = RuntimeError(
                            "injected_parent_acknowledge_failure"
                        )
                    else:
                        stack.enter_context(
                            mock.patch.object(
                                controller_module,
                                "_issue_publication_ack_v1",
                                side_effect=RuntimeError(
                                    "injected_parent_ack_issue_failure"
                                ),
                            )
                        )
                    expected_token = (
                        "durable_companion_publication_ack_unavailable"
                        if cut == "ack_issue"
                        else "expert_consumed_parent_processing_failed"
                    )
                    with stack, self.assertRaisesRegex(
                        RuntimeError,
                        f"^{expected_token}$",
                    ) as raised:
                        controller.process_durable_parent(parent)
                self.assertIsNone(raised.exception.__cause__)
                self.assertEqual(raised.exception.args, (expected_token,))
                self.assertNotIn("injected_parent_", str(raised.exception))
                with self.assertRaisesRegex(
                    ValueError,
                    "^durable_ingress_parent_consumed$",
                ):
                    getattr(
                        ingress_module,
                        "_validate_durable_ingress_parent_for_consumer_v1",
                    )(parent, controller)
                counts_before_retry = (
                    probe.append_count,
                    probe.ack_count,
                    probe.abort_count,
                )
                with self.assertRaisesRegex(
                    RuntimeError,
                    "^expert_controller_unavailable$",
                ):
                    controller.process_durable_parent(parent)
                self.assertEqual(
                    (
                        probe.append_count,
                        probe.ack_count,
                        probe.abort_count,
                    ),
                    counts_before_retry,
                )
                after = controller.snapshot()
                if cut == "ack_issue":
                    reconciliation = object.__getattribute__(
                        controller,
                        "_ordinary_reconciliation",
                    )
                    self.assertIs(after[0], reconciliation.candidate_state)
                    self.assertIs(after[1], reconciliation.candidate_cursor)
                    self.assertIs(after[1], probe.current_cursor)
                    self.assertIsNot(after[0], before[0])
                    self.assertIsNot(after[1], before[1])
                    self.assertIsNone(after[2])
                else:
                    self.assertIs(after[0], before[0])
                    self.assertIs(after[1], before[1])
                    self.assertIsNone(after[2])
                self.assertEqual(
                    object.__getattribute__(controller, "_lifecycle"),
                    (
                        "DURABILITY_UNCERTAIN_HALTED"
                        if cut
                        in ("append", "receipt_validation", "acknowledge")
                        else "HALTED_UNCLEAN"
                    ),
                )
                self.assertEqual(
                    object.__getattribute__(
                        controller,
                        "_controller_lifecycle",
                    ),
                    (
                        "DURABILITY_UNCERTAIN_HALTED"
                        if cut
                        in ("append", "receipt_validation", "acknowledge")
                        else "HALTED_UNCLEAN"
                    ),
                )
                self.assertEqual(
                    object.__getattribute__(controller, "_publication_epoch"),
                    int(cut == "ack_issue"),
                )
                self.assertEqual(
                    probe.append_count,
                    int(
                        cut
                        in (
                            "append",
                            "receipt_validation",
                            "acknowledge",
                            "ack_issue",
                        )
                    ),
                )
                self.assertEqual(
                    probe.ack_count,
                    int(cut in ("acknowledge", "ack_issue")),
                )
                self.assertEqual(probe.abort_count, 1)

    def test_r19_dynamic_terminal_postconsume_failure_matrix(self) -> None:
        cuts = (
            "tail",
            "tail_unseen",
            "build",
            "material_validation",
            "encode",
            "permit",
            "reconciliation",
            "append",
            "receipt_validation",
            "handoff_constructor",
            "handoff_register",
        )
        registry_type = getattr(controller_module, "_WeakIdentityRegistryV1")
        handoff_registry = getattr(
            controller_module,
            "_ORDINARY_TERMINAL_HANDOFFS_V1",
        )
        original_register = registry_type.register
        for cut in cuts:
            with self.subTest(cut=cut), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    terminal = case._drain_durable_terminal()
                    expert_terminal = _terminal_for(
                        case.manifest,
                        before[1],
                        terminal.terminal,
                        clean=True,
                        evidence_reason="operator_stop",
                    )
                    stack = ExitStack()
                    if cut == "tail":
                        probe.tail_failure = RuntimeError(
                            "injected_terminal_tail_failure"
                        )
                    elif cut == "tail_unseen":
                        probe.tail_result = object()
                    elif cut == "build":
                        pass
                    else:
                        probe.built_terminal_pair = (
                            terminal.terminal,
                            expert_terminal,
                        )
                        if cut == "material_validation":
                            stack.enter_context(
                                mock.patch.object(
                                    controller_module,
                                    "_validate_terminal_material",
                                    side_effect=RuntimeError(
                                        "injected_terminal_material_failure"
                                    ),
                                )
                            )
                        elif cut == "encode":
                            stack.enter_context(
                                mock.patch.object(
                                    controller_module,
                                    "encode_expert_terminal_frame",
                                    side_effect=RuntimeError(
                                        "injected_terminal_encode_failure"
                                    ),
                                )
                            )
                        elif cut == "permit":
                            stack.enter_context(
                                mock.patch.object(
                                    controller_module,
                                    "issue_expert_terminal_permit",
                                    side_effect=RuntimeError(
                                        "injected_terminal_permit_failure"
                                    ),
                                )
                            )
                        elif cut == "reconciliation":
                            stack.enter_context(
                                mock.patch.object(
                                    controller_module,
                                    "_DefinitelyDurableOrdinaryReconciliationV1",
                                    side_effect=MemoryError(
                                        "injected_terminal_reconciliation_failure"
                                    ),
                                )
                            )
                        elif cut == "append":
                            probe.terminal_failure = RuntimeError(
                                "injected_terminal_append_ambiguity"
                            )
                        elif cut == "receipt_validation":
                            stack.enter_context(
                                mock.patch.object(
                                    controller_module,
                                    "_validate_terminal_receipt",
                                    side_effect=RuntimeError(
                                        "injected_terminal_receipt_failure"
                                    ),
                                )
                            )
                        elif cut == "handoff_constructor":
                            stack.enter_context(
                                mock.patch.object(
                                    controller_module,
                                    "_DurableExpertTerminalReceiptHandoffV1",
                                    side_effect=MemoryError(
                                        "injected_terminal_handoff_constructor_failure"
                                    ),
                                )
                            )
                        elif cut == "handoff_register":
                            def fail_handoff_register(
                                registry,
                                key,
                                value,
                            ):
                                if registry is handoff_registry:
                                    raise RuntimeError(
                                        "injected_terminal_handoff_register_failure"
                                    )
                                return original_register(
                                    registry,
                                    key,
                                    value,
                                )

                            stack.enter_context(
                                mock.patch.object(
                                    registry_type,
                                    "register",
                                    new=fail_handoff_register,
                                )
                            )
                    expected_type = (
                        ValueError if cut == "tail_unseen" else RuntimeError
                    )
                    expected_token = (
                        "expert_unacknowledged_evidence_tail"
                        if cut == "tail_unseen"
                        else "durable_companion_terminal_receipt_unavailable"
                        if cut
                        in ("handoff_constructor", "handoff_register")
                        else "expert_consumed_terminal_processing_failed"
                    )
                    with stack, self.assertRaisesRegex(
                        expected_type,
                        f"^{expected_token}$",
                    ) as raised:
                        controller.process_evidence_terminal(terminal)
                self.assertIsNone(raised.exception.__cause__)
                with self.assertRaisesRegex(
                    ValueError,
                    "^durable_evidence_terminal_consumed$",
                ):
                    getattr(
                        ingress_module,
                        "_validate_durable_evidence_terminal_for_consumer_v1",
                    )(terminal, controller)
                self.assertEqual(controller.snapshot(), before)
                self.assertEqual(
                    object.__getattribute__(controller, "_lifecycle"),
                    (
                        "DURABILITY_UNCERTAIN_HALTED"
                        if cut in ("append", "receipt_validation")
                        else "HALTED_UNCLEAN"
                    ),
                )
                self.assertEqual(
                    probe.terminal_append_count,
                    int(
                        cut
                        in (
                            "append",
                            "receipt_validation",
                            "handoff_constructor",
                            "handoff_register",
                        )
                    ),
                )
                self.assertEqual(probe.abort_count, 1)

    def test_r19_dynamic_snapshot_and_close_lifecycle_matrix(self) -> None:
        with self.fresh_case() as pending_case:
            probe = _RuledStoreProbe(pending_case.collected)
            with pending_case.mocked_facade(probe):
                controller = pending_case.create_controller()
                before, pending = pending_case._capacity_pending(probe, controller)
                self.assertEqual(controller.snapshot(), before)
                closed = controller.close()
                self.assertEqual(closed, before)
                self.assertEqual(controller.close(), before)
                self.assertEqual(
                    object.__getattribute__(controller, "_lifecycle"),
                    "HALTED_UNCLEAN",
                )
                self.assertEqual(
                    getattr(
                        controller_module,
                        "_PENDING_EMERGENCY_AUTHORITIES_V1",
                    ).lookup(pending).lifecycle,
                    "ABORTED_NONPUBLICATION",
                )

        with self.fresh_case() as terminal_case:
            probe = _RuledStoreProbe(terminal_case.collected)
            with terminal_case.mocked_facade(probe):
                controller = terminal_case.create_controller()
                before = controller.snapshot()
                terminal = terminal_case._drain_durable_terminal()
                expert_terminal = _terminal_for(
                    terminal_case.manifest,
                    before[1],
                    terminal.terminal,
                    clean=True,
                    evidence_reason="operator_stop",
                )
                probe.built_terminal_pair = (
                    terminal.terminal,
                    expert_terminal,
                )
                published = controller.process_evidence_terminal(terminal)
                self.assertIs(published[2], expert_terminal)
                self.assertEqual(controller.snapshot(), published)
                self.assertEqual(controller.close(), published)

        with self.fresh_case() as uncertain_case:
            probe = _RuledStoreProbe(uncertain_case.collected)
            with uncertain_case.mocked_facade(probe):
                controller = uncertain_case.create_controller()
                before = controller.snapshot()
                probe.ack_failure = RuntimeError("injected_ack_ambiguity")
                with self.assertRaisesRegex(
                    RuntimeError,
                    "^expert_consumed_parent_processing_failed$",
                ):
                    controller.process_durable_parent(
                        uncertain_case._drain_durable_parent()
                    )
                self.assertEqual(controller.snapshot(), before)
                self.assertEqual(controller.close(), before)
                self.assertEqual(
                    object.__getattribute__(controller, "_lifecycle"),
                    "DURABILITY_UNCERTAIN_HALTED",
                )

    def test_r19_c32_strong_ast_and_dependency_boundary(self) -> None:
        source = Path(controller_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_import_roots = {
            "requests",
            "socket",
            "subprocess",
            "urllib",
            "http",
            "asyncio",
        }
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertTrue(forbidden_import_roots.isdisjoint(imported_roots))
        public_authorities = {
            "ExpertControllerIdentityV1",
            "DurableCompanionCapacityDenialObservationV1",
            "DurableCompanionPublicationAckV1",
            "PendingDurableCompanionEmergencyV1",
            "DurableCompanionEmergencyPublicationProofV1",
            "PendingDurableCompanionEmergencyAbortReceiptV1",
        }
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(public_authorities.isdisjoint(called_names))
        forbidden_calls = {
            "statvfs",
            "fstatvfs",
            "getenv",
            "open",
            "urlopen",
            "request",
        }
        self.assertTrue(forbidden_calls.isdisjoint(called_names))
        identifiers = {
            node.id.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        } | {
            node.attr.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        forbidden_fragments = (
            "credential",
            "portfolio",
            "trading",
            "order_",
            "network",
            "callback",
            "capacity_probe",
        )
        self.assertFalse(
            sorted(
                identifier
                for identifier in identifiers
                if any(fragment in identifier for fragment in forbidden_fragments)
            )
        )
        eager_a5 = {
            "DeferredEmergencyCommitSubjectV1",
            "DurableCausalPrecedesProofV1",
            "DeferredEmergencySourceCloseClaimV1",
            "_abort_deferred_emergency_commit_subject_v1",
            "_close_durable_causal_precedes_proof_after_deferred_append_failure_v1",
            "_commit_prepared_durable_causal_precedes_proof_v1",
            "_issue_durable_causal_precedes_for_deferred_commit_v1",
            "_issue_deferred_emergency_commit_subject_v1",
            "_PreparedDeferredEmergencyCausalProofCommitV1",
            "_prepare_durable_causal_precedes_proof_commit_v1",
        }
        module_level_ingress_imports = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module == "tennis_v1.ingress"
            for alias in node.names
        }
        self.assertTrue(
            eager_a5.isdisjoint(module_level_ingress_imports),
            eager_a5 & module_level_ingress_imports,
        )
        self.assertFalse(
            any(
                isinstance(node, ast.ImportFrom)
                and node.module == "inci_tennis_runtime.shadow_sources"
                for node in tree.body
            )
        )

    def test_r19_ast_causal_commit_and_step9_are_unhandled_scalar_kernels(
        self,
    ) -> None:
        ingress_tree = ast.parse(
            Path(ingress_module.__file__).read_text(encoding="utf-8")
        )
        commit = next(
            node
            for node in ast.walk(ingress_tree)
            if isinstance(node, ast.FunctionDef)
            and node.name
            == "_commit_prepared_durable_causal_precedes_proof_v1"
        )
        final_guard_index = max(
            index
            for index, statement in enumerate(commit.body)
            if isinstance(statement, ast.If)
        )
        ingress_scalar_tail = commit.body[final_guard_index + 1 :]
        ingress_calls = [
            node
            for statement in ingress_scalar_tail
            for node in ast.walk(statement)
            if isinstance(node, ast.Call)
        ]
        allowed_ingress_calls: list[str] = []
        forbidden_ingress_calls: list[str] = []
        for call in ingress_calls:
            is_prevalidated_proof_clear = (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "object"
                and call.func.attr == "__setattr__"
                and len(call.args) == 3
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == "proof"
                and isinstance(call.args[1], ast.Constant)
                and call.args[1].value
                in ("_proof_authority", "_prepared_commit")
                and isinstance(call.args[2], ast.Constant)
                and call.args[2].value is None
                and not call.keywords
            )
            rendered = ast.unparse(call)
            if is_prevalidated_proof_clear:
                allowed_ingress_calls.append(rendered)
            else:
                forbidden_ingress_calls.append(rendered)
        ingress_forbidden = [
            type(node).__name__
            for statement in ingress_scalar_tail
            for node in ast.walk(statement)
            if isinstance(
                node,
                (
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

        controller_tree = ast.parse(
            Path(controller_module.__file__).read_text(encoding="utf-8")
        )
        completion = next(
            node
            for node in ast.walk(controller_tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "complete_pending_emergency"
        )
        commit_statement = next(
            statement
            for statement in ast.walk(completion)
            if isinstance(statement, ast.Expr)
            and any(
                isinstance(call.func, ast.Name)
                and call.func.id
                == "_commit_prepared_durable_causal_precedes_proof_v1"
                for call in ast.walk(statement)
                if isinstance(call, ast.Call)
            )
        )
        enclosing_handlers = [
            node
            for node in ast.walk(completion)
            if isinstance(node, ast.Try)
            and node.lineno <= commit_statement.lineno <= node.end_lineno
            and node.handlers
        ]
        step9_try = min(
            enclosing_handlers,
            key=lambda node: node.end_lineno - node.lineno,
            default=None,
        )
        controller_scalar_calls: list[str] = []
        if step9_try is not None:
            commit_index = next(
                index
                for index, statement in enumerate(step9_try.body)
                if statement is commit_statement
            )
            controller_scalar_calls = [
                (
                    call.func.id
                    if isinstance(call.func, ast.Name)
                    else call.func.attr
                    if isinstance(call.func, ast.Attribute)
                    else type(call.func).__name__
                )
                for statement in step9_try.body[commit_index + 1 :]
                for call in ast.walk(statement)
                if isinstance(call, ast.Call)
            ]
        exposure = next(
            call
            for call in ast.walk(completion)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id
            == "_issue_emergency_publication_proof_and_handoff_v1"
        )
        problems = {
            "ingress_scalar_allowed_calls": allowed_ingress_calls,
            "ingress_scalar_forbidden_calls": forbidden_ingress_calls,
            "ingress_scalar_forbidden": ingress_forbidden,
            "controller_step9_enclosing_handlers": len(enclosing_handlers),
            "controller_step9_calls": controller_scalar_calls,
            "exposure_not_later": exposure.lineno <= commit_statement.lineno,
        }
        self.assertEqual(
            problems,
            {
                "ingress_scalar_allowed_calls": [
                    "object.__setattr__(proof, '_proof_authority', None)",
                    "object.__setattr__(proof, '_prepared_commit', None)",
                ],
                "ingress_scalar_forbidden_calls": [],
                "ingress_scalar_forbidden": [],
                "controller_step9_enclosing_handlers": 0,
                "controller_step9_calls": [],
                "exposure_not_later": False,
            },
        )

    def test_r19_dynamic_real_ingress_failure_close_converges_and_releases(
        self,
    ) -> None:
        original_prepare = getattr(
            ingress_module,
            "_prepare_durable_causal_precedes_proof_commit_v1",
        )
        original_close = getattr(
            ingress_module,
            "_close_durable_causal_precedes_proof_after_deferred_append_failure_v1",
        )
        prepared_registry = getattr(
            ingress_module,
            "_PREPARED_DEFERRED_COMMIT_ENTRIES_V1",
        )
        pending_registry = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        )
        pending_tombstone_type = getattr(
            controller_module,
            "_ControllerTerminalTombstoneV1",
        )

        for timing in ("before_original", "after_original"):
            with self.subTest(timing=timing), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                captured: dict[str, object] = {}
                close_calls = 0

                class DeferredEmergencySourceCloseClaimV1:
                    pass

                class _DeferredEmergencySourceCloseClaimAuthorityV1:
                    def __init__(self) -> None:
                        self.lifecycle = "CLAIMED"

                claim = DeferredEmergencySourceCloseClaimV1()
                claim_authority = (
                    _DeferredEmergencySourceCloseClaimAuthorityV1()
                )
                source_module = types.ModuleType(
                    "inci_tennis_runtime.shadow_sources"
                )
                source_module.DeferredEmergencySourceCloseClaimV1 = (
                    DeferredEmergencySourceCloseClaimV1
                )
                source_module._DeferredEmergencySourceCloseClaimAuthorityV1 = (
                    _DeferredEmergencySourceCloseClaimAuthorityV1
                )
                source_module._DEFERRED_EMERGENCY_CLAIM_COMMIT_LOCK_V1 = (
                    threading.RLock()
                )

                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before, pending = case._capacity_pending(
                        probe,
                        controller,
                    )
                    before_coordinate = ingress_module._issue_coordinate_v1(
                        case.ingress,
                        case.runtime,
                        session_id=case.manifest.session_id,
                        stage="SOURCE_CLOSE_COMPLETE",
                        subject=claim,
                        subject_sha256=sha256(
                            (
                                "r19-failure-close-" + timing
                            ).encode("ascii")
                        ).hexdigest(),
                    )

                    def resolve_claim(value):
                        if value is not claim:
                            raise ValueError("wrong_test_source_close_claim")
                        return claim_authority, before_coordinate

                    source_module._resolve_deferred_emergency_source_close_claim_for_ingress_commit_v1 = resolve_claim

                    def consume_claim(value, subject):
                        if type(value) is not DeferredEmergencySourceCloseClaimV1:
                            raise TypeError(
                                "exact DeferredEmergencySourceCloseClaimV1 required"
                            )
                        proof = (
                            ingress_module._issue_durable_causal_precedes_for_deferred_commit_v1(
                                claim=value,
                                subject=subject,
                            )
                        )
                        return value, subject, proof

                    source_module.consume_deferred_emergency_source_close_before_terminal_v1 = consume_claim
                    authority, terminal, _ = (
                        case._emergency_terminal_material(probe, pending)
                    )
                    append_failure = RuntimeError(
                        "injected_real_ingress_emergency_append_failure"
                    )
                    probe.emergency_failure = append_failure

                    def capture_prepare(proof, **kwargs):
                        prepared = original_prepare(proof, **kwargs)
                        captured["proof"] = proof
                        captured["prepared"] = prepared
                        captured["subject"] = kwargs["subject"]
                        captured["proof_authority"] = (
                            ingress_module._lookup_proof_authority(proof)
                        )
                        captured["registry_cell"] = prepared_registry[
                            id(prepared)
                        ]
                        captured["scope"] = prepared.completion_scope
                        return prepared

                    def fault_close(proof, **kwargs):
                        nonlocal close_calls
                        close_calls += 1
                        if close_calls != 1:
                            return original_close(proof, **kwargs)
                        if timing == "after_original":
                            original_close(proof, **kwargs)
                        raise RuntimeError(
                            f"injected_failure_close_{timing}"
                        )

                    with (
                        mock.patch.dict(
                            sys.modules,
                            {
                                "inci_tennis_runtime.shadow_sources": (
                                    source_module
                                )
                            },
                        ),
                        case._patched_ingress_bridge(
                            "_prepare_durable_causal_precedes_proof_commit_v1",
                            capture_prepare,
                        ),
                        case._patched_ingress_bridge(
                            "_close_durable_causal_precedes_proof_after_deferred_append_failure_v1",
                            fault_close,
                        ),
                        self.assertRaisesRegex(
                            RuntimeError,
                            "^durable_companion_emergency_publication_uncertain$",
                        ) as raised,
                    ):
                        controller.complete_pending_emergency(
                            pending,
                            terminal,
                            claim,
                        )

                self.assertIs(type(raised.exception), RuntimeError)
                self.assertEqual(
                    raised.exception.args,
                    ("durable_companion_emergency_publication_uncertain",),
                )
                self.assertIsNone(raised.exception.__cause__)
                self.assertTrue(raised.exception.__suppress_context__)
                self.assertNotIn("injected_", str(raised.exception))
                self.assertEqual(close_calls, 1)
                self.assertEqual(probe.emergency_append_count, 1)
                self.assertEqual(probe.abort_count, 1)

                proof = captured["proof"]
                prepared = captured["prepared"]
                subject = captured["subject"]
                proof_authority = captured["proof_authority"]
                registry_cell = captured["registry_cell"]
                scope = captured["scope"]
                self.assertEqual(
                    proof_authority.lifecycle,
                    "APPEND_FAILED_CLOSED",
                )
                self.assertEqual(prepared.lifecycle, "FAILED_CLOSED")
                self.assertEqual(registry_cell.lifecycle, "FAILED_CLOSED")
                self.assertEqual(
                    authority.lifecycle,
                    "PUBLICATION_FAILED_CLOSED",
                )
                self.assertEqual(scope.lifecycle, "APPEND_FAILED_CLOSED")
                self.assertIsNone(
                    object.__getattribute__(proof, "_proof_authority")
                )
                self.assertIsNone(
                    object.__getattribute__(proof, "_prepared_commit")
                )
                for field_name in (
                    "before",
                    "after",
                    "subject",
                    "claim",
                    "pending",
                    "terminal",
                    "owner_thread",
                    "issuance",
                    "proof_reference",
                    "prepared_commit",
                ):
                    self.assertIsNone(getattr(proof_authority, field_name))
                for field_name in (
                    "ingress",
                    "controller",
                    "pending_authority",
                    "completion_scope",
                    "proof_authority",
                    "subject_authority",
                    "terminal_authority",
                    "publication_lock",
                    "ingress_lock",
                    "owner_thread",
                ):
                    self.assertIsNone(getattr(prepared, field_name))
                tombstone = pending_registry.lookup(pending)
                self.assertIs(type(tombstone), pending_tombstone_type)
                self.assertEqual(
                    tombstone.lifecycle,
                    "PUBLICATION_FAILED_CLOSED",
                )
                self.assertIsNone(authority.active_completion_scope)
                self.assertIsNone(authority.retry_subject)
                self.assertIsNone(authority.reserved_claim)
                self.assertIsNone(authority.reserved_subject)
                self.assertIsNone(authority.reserved_terminal)
                self.assertIsNone(authority.reserved_causal_proof)
                self.assertIsNone(authority.reserved_completion_scope)
                self.assertEqual(controller.snapshot(), before)
                self.assertIsNone(
                    object.__getattribute__(controller, "_active_pending")
                )
                self.assertEqual(
                    object.__getattribute__(controller, "_lifecycle"),
                    "DURABILITY_UNCERTAIN_HALTED",
                )

                prepared_key = id(prepared)
                graph_references = {
                    "claim": weakref.ref(claim),
                    "before_coordinate": weakref.ref(before_coordinate),
                    "pending": weakref.ref(pending),
                    "terminal": weakref.ref(terminal),
                    "subject": weakref.ref(subject),
                    "proof": weakref.ref(proof),
                    "prepared": weakref.ref(prepared),
                }
                append_failure.__traceback__ = None
                append_failure.__cause__ = None
                append_failure.__context__ = None
                probe.emergency_failure = None
                captured.clear()
                del raised
                del proof
                del prepared
                del subject
                del proof_authority
                del registry_cell
                del scope
                del tombstone
                del authority
                del terminal
                del pending
                del before_coordinate
                del claim
                gc.collect()
                self.assertEqual(
                    {
                        name: reference()
                        for name, reference in graph_references.items()
                        if reference() is not None
                    },
                    {},
                )
                self.assertNotIn(prepared_key, prepared_registry)

    def test_r19_dynamic_real_ingress_postreservation_cut_matrix(
        self,
    ) -> None:
        original_issue = getattr(
            ingress_module,
            "_issue_durable_causal_precedes_for_deferred_commit_v1",
        )
        original_prepare = getattr(
            ingress_module,
            "_prepare_durable_causal_precedes_proof_commit_v1",
        )
        original_consume_terminal = getattr(
            ingress_module,
            "_consume_durable_evidence_terminal_v1",
        )
        lookup_prepared = getattr(
            ingress_module,
            "_lookup_prepared_deferred_emergency_causal_proof_commit_for_proof_v1",
        )
        prepared_registry = getattr(
            ingress_module,
            "_PREPARED_DEFERRED_COMMIT_ENTRIES_V1",
        )
        pending_registry = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        )
        cuts = (
            "terminal_consume",
            "tail",
            "candidate_group",
            "terminal_build",
            "permit",
            "success_arming",
            "append",
            "receipt_validation",
        )
        for cut in cuts:
            with self.subTest(cut=cut), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                captured: dict[str, object] = {}

                def capture_issue(*, claim, subject):
                    proof = original_issue(claim=claim, subject=subject)
                    prepared = lookup_prepared(proof)
                    self.assertIsNotNone(prepared)
                    captured["proof"] = proof
                    captured["proof_authority"] = (
                        ingress_module._lookup_proof_authority(proof)
                    )
                    captured["prepared"] = prepared
                    captured["registry_cell"] = prepared_registry[
                        id(prepared)
                    ]
                    captured["scope"] = prepared.completion_scope
                    return proof

                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    before, pending = case._capacity_pending(
                        probe,
                        controller,
                    )
                    with case._governed_source_close_claim(
                        "r19-postreservation-" + cut
                    ) as claim:
                        authority, terminal, _ = (
                            case._emergency_terminal_material(
                                probe,
                                pending,
                            )
                        )
                        with ExitStack() as stack:
                            stack.enter_context(
                                case._patched_ingress_bridge(
                                    "_issue_durable_causal_precedes_for_deferred_commit_v1",
                                    capture_issue,
                                )
                            )
                            if cut == "terminal_consume":
                                def consume_then_fail(*args, **kwargs):
                                    original_consume_terminal(*args, **kwargs)
                                    raise RuntimeError(
                                        "injected_terminal_consume_cut"
                                    )

                                stack.enter_context(
                                    case._patched_ingress_bridge(
                                        "_consume_durable_evidence_terminal_v1",
                                        consume_then_fail,
                                    )
                                )
                            elif cut == "tail":
                                probe.tail_failure = RuntimeError(
                                    "injected_emergency_tail_cut"
                                )
                            elif cut == "candidate_group":
                                stack.enter_context(
                                    mock.patch.object(
                                        controller_module,
                                        "validate_expert_group_against_cursor",
                                        side_effect=RuntimeError(
                                            "injected_candidate_group_cut"
                                        ),
                                    )
                                )
                            elif cut == "terminal_build":
                                stack.enter_context(
                                    mock.patch.object(
                                        controller_module,
                                        "build_aligned_expert_terminal",
                                        side_effect=RuntimeError(
                                            "injected_terminal_build_cut"
                                        ),
                                    )
                                )
                            elif cut == "permit":
                                stack.enter_context(
                                    mock.patch.object(
                                        controller_module,
                                        "issue_expert_emergency_append_permit",
                                        side_effect=RuntimeError(
                                            "injected_emergency_permit_cut"
                                        ),
                                    )
                                )
                            elif cut == "success_arming":
                                def arm_then_fail(*args, **kwargs):
                                    original_prepare(*args, **kwargs)
                                    raise RuntimeError(
                                        "injected_success_arming_cut"
                                    )

                                stack.enter_context(
                                    case._patched_ingress_bridge(
                                        "_prepare_durable_causal_precedes_proof_commit_v1",
                                        arm_then_fail,
                                    )
                                )
                            elif cut == "append":
                                probe.emergency_failure = RuntimeError(
                                    "injected_emergency_append_cut"
                                )
                            else:
                                stack.enter_context(
                                    mock.patch.object(
                                        controller_module,
                                        "_validate_emergency_receipt",
                                        side_effect=RuntimeError(
                                            "injected_emergency_receipt_cut"
                                        ),
                                    )
                                )
                            with self.assertRaisesRegex(
                                RuntimeError,
                                "^durable_companion_emergency_publication_uncertain$",
                            ) as raised:
                                controller.complete_pending_emergency(
                                    pending,
                                    terminal,
                                    claim,
                                )

                self.assertIsNone(raised.exception.__cause__)
                self.assertNotIn("injected_", str(raised.exception))
                proof = captured["proof"]
                proof_authority = captured["proof_authority"]
                prepared = captured["prepared"]
                registry_cell = captured["registry_cell"]
                scope = captured["scope"]
                self.assertEqual(
                    proof_authority.lifecycle,
                    "APPEND_FAILED_CLOSED",
                )
                self.assertEqual(prepared.lifecycle, "FAILED_CLOSED")
                self.assertEqual(registry_cell.lifecycle, "FAILED_CLOSED")
                self.assertEqual(
                    authority.lifecycle,
                    "PUBLICATION_FAILED_CLOSED",
                )
                self.assertEqual(scope.lifecycle, "APPEND_FAILED_CLOSED")
                self.assertIsNone(
                    object.__getattribute__(proof, "_proof_authority")
                )
                self.assertIsNone(
                    object.__getattribute__(proof, "_prepared_commit")
                )
                self.assertIsNone(authority.active_completion_scope)
                self.assertEqual(
                    pending_registry.lookup(pending).lifecycle,
                    "PUBLICATION_FAILED_CLOSED",
                )
                self.assertEqual(controller.snapshot(), before)
                self.assertEqual(
                    object.__getattribute__(controller, "_lifecycle"),
                    "DURABILITY_UNCERTAIN_HALTED",
                )
                self.assertEqual(
                    prepared.success_armed,
                    cut in ("success_arming", "append", "receipt_validation"),
                )
                self.assertEqual(
                    probe.emergency_append_count,
                    int(cut in ("append", "receipt_validation")),
                )
                if probe.emergency_permit_count:
                    self.assertIs(probe.group, authority.emergency_group)
                    self.assertIsNot(probe.group, authority.denied_group)
                self.assertEqual(probe.abort_count, 1)
                captured.clear()
                del raised

    def test_r19_dynamic_reconciliation_constructor_failure_is_preappend(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        with self.mocked_facade(probe):
            controller = self.create_controller()
            before = controller.snapshot()
            parent = self._drain_durable_parent()
            with (
                mock.patch.object(
                    controller_module,
                    "_DefinitelyDurableOrdinaryReconciliationV1",
                    side_effect=MemoryError(
                        "injected_reconciliation_constructor_failure"
                    ),
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "^expert_consumed_parent_processing_failed$",
                ) as raised,
            ):
                controller.process_durable_parent(parent)
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(controller.snapshot(), before)
        self.assertIsNone(
            object.__getattribute__(controller, "_ordinary_reconciliation")
        )
        self.assertEqual(
            object.__getattribute__(controller, "_lifecycle"),
            "HALTED_UNCLEAN",
        )
        self.assertEqual(probe.append_count, 0)
        self.assertEqual(probe.ack_count, 0)
        self.assertEqual(probe.abort_count, 1)

    def test_r19_dynamic_capacity_error_frame_graph_is_collectable(self) -> None:
        class FrameGraph:
            pass

        for path in ("success", "failure"):
            with self.subTest(path=path), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                error = ExpertPrewriteCapacityError(
                    requested_bytes=4096,
                    available_bytes=1024,
                    emergency_reserve_bytes=EXPERT_EMERGENCY_RESERVE_BYTES,
                )
                if path == "failure":
                    object.__setattr__(error, "requested_bytes", True)
                frame_graph = FrameGraph()
                frame_graph_ref = weakref.ref(frame_graph)

                def raise_with_retained_frame(*_args, **_kwargs):
                    retained_frame_graph = frame_graph
                    self.assertIs(retained_frame_graph, frame_graph)
                    raise error

                with (
                    case.mocked_facade(probe),
                    mock.patch.object(
                        controller_module,
                        "issue_expert_append_permit",
                        new=raise_with_retained_frame,
                    ),
                ):
                    controller = case.create_controller()
                    before = controller.snapshot()
                    parent = case._drain_durable_parent()
                    if path == "success":
                        pending = controller.process_durable_parent(parent)[2]
                        authority = getattr(
                            controller_module,
                            "_PENDING_EMERGENCY_AUTHORITIES_V1",
                        ).lookup(pending)
                        self.assertIsNone(authority.store_error if hasattr(authority, "store_error") else None)
                    else:
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "^expert_capacity_observation_invalid$",
                        ) as raised:
                            controller.process_durable_parent(parent)
                        self.assertIsNone(raised.exception.__cause__)
                        self.assertEqual(controller.snapshot(), before)
                self.assertIsNone(error.__traceback__)
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)
                del raise_with_retained_frame
                del frame_graph
                gc.collect()
                self.assertIsNone(
                    frame_graph_ref(),
                    f"caught capacity frame graph retained on {path} path",
                )

    def test_r19_dynamic_capacity_error_subclass_is_rejected_exactly(
        self,
    ) -> None:
        class DerivedCapacityError(ExpertPrewriteCapacityError):
            pass

        probe = _RuledStoreProbe(self.collected)
        probe.prewrite_capacity_error = DerivedCapacityError(
            requested_bytes=4096,
            available_bytes=1024,
            emergency_reserve_bytes=EXPERT_EMERGENCY_RESERVE_BYTES,
        )
        with self.mocked_facade(probe):
            controller = self.create_controller()
            before = controller.snapshot()
            parent = self._drain_durable_parent()
            with self.assertRaisesRegex(
                RuntimeError,
                "^expert_capacity_observation_invalid$",
            ) as raised:
                controller.process_durable_parent(parent)
        self.assertIsNone(raised.exception.__cause__)
        after = controller.snapshot()
        self.assertIs(after[0], before[0])
        self.assertIs(after[1], before[1])
        self.assertIsNone(after[2])
        self.assertEqual(
            object.__getattribute__(controller, "_publication_epoch"),
            0,
        )
        self.assertEqual(
            object.__getattribute__(controller, "_lifecycle"),
            "HALTED_UNCLEAN",
        )
        self.assertEqual(probe.append_count, 0)
        self.assertEqual(probe.emergency_append_count, 0)
        self.assertEqual(probe.abort_count, 1)

    def test_r19_dynamic_emergency_publication_lock_barrier_is_continuous(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        contender_started = threading.Event()
        contender_acquired = threading.Event()
        contender_threads: list[threading.Thread] = []
        with self.mocked_facade(probe):
            controller = self.create_controller()
            _, pending = self._capacity_pending(probe, controller)
            _, terminal, _ = self._emergency_terminal_material(
                probe,
                pending,
            )
            publication_lock = object.__getattribute__(
                controller,
                "_publication_lock",
            )
            causal_lock = self.ingress._causal_subject_lock

            def contend_for_outer_lock() -> None:
                contender_started.set()
                with publication_lock:
                    contender_acquired.set()

            def pause_during_append() -> None:
                self.assertTrue(publication_lock._is_owned())
                self.assertTrue(causal_lock._is_owned())
                contender = threading.Thread(target=contend_for_outer_lock)
                contender_threads.append(contender)
                self._threads.append(contender)
                contender.start()
                self.assertTrue(contender_started.wait(5))
                self.assertFalse(contender_acquired.wait(0.1))

            probe.emergency_pause = pause_during_append
            with self._synthetic_causal_seam(pending) as calls:
                synthetic_commit = getattr(
                    ingress_module,
                    "_commit_prepared_durable_causal_precedes_proof_v1",
                )

                def commit_under_barrier(prepared) -> None:
                    self.assertTrue(publication_lock._is_owned())
                    self.assertTrue(causal_lock._is_owned())
                    self.assertTrue(contender_started.is_set())
                    self.assertFalse(contender_acquired.is_set())
                    synthetic_commit(prepared)

                with self._patched_ingress_bridge(
                    "_commit_prepared_durable_causal_precedes_proof_v1",
                    commit_under_barrier,
                ):
                    result = controller.complete_pending_emergency(
                        pending,
                        terminal,
                        object(),
                    )
        self.assertIs(type(result[2]), ExpertSessionTerminalV1)
        self.assertEqual(calls, {"commit": 1, "failure_close": 0})
        self.assertEqual(len(contender_threads), 1)
        contender_threads[0].join(5)
        self.assertFalse(contender_threads[0].is_alive())
        self.assertTrue(contender_acquired.is_set())

    def test_r19_dynamic_tombstone_allocation_failure_after_reservation_closes(
        self,
    ) -> None:
        probe = _RuledStoreProbe(self.collected)
        pending_registry = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        )
        allocation_calls = 0
        with self.mocked_facade(probe):
            controller = self.create_controller()
            before, pending = self._capacity_pending(probe, controller)
            authority, terminal, _ = self._emergency_terminal_material(
                probe,
                pending,
            )
            publication_lock = object.__getattribute__(
                controller,
                "_publication_lock",
            )
            causal_lock = self.ingress._causal_subject_lock

            def fail_tombstone_allocation(*_args, **_kwargs):
                nonlocal allocation_calls
                allocation_calls += 1
                self.assertEqual(authority.lifecycle, "FRESH")
                self.assertFalse(
                    authority.active_completion_scope.reservation_committed
                )
                self.assertFalse(publication_lock._is_owned())
                self.assertFalse(causal_lock._is_owned())
                raise MemoryError("injected_tombstone_allocation_failure")

            with self._synthetic_causal_seam(pending) as calls:
                with (
                    mock.patch.object(
                        controller_module,
                        "_pending_terminal_tombstone_v1",
                        new=fail_tombstone_allocation,
                    ),
                    self.assertRaisesRegex(
                        MemoryError,
                        "^injected_tombstone_allocation_failure$",
                    ) as raised,
                ):
                    controller.complete_pending_emergency(
                        pending,
                        terminal,
                        object(),
                    )
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(allocation_calls, 1)
        self.assertEqual(calls, {"commit": 0, "failure_close": 0})
        self.assertIs(pending_registry.lookup(pending), authority)
        self.assertEqual(authority.lifecycle, "FRESH")
        self.assertEqual(authority.active_completion_scope.lifecycle, "CLEARED")
        self.assertFalse(authority.active_completion_scope.reservation_committed)
        self.assertIsNone(authority.retry_subject)
        self.assertIs(
            object.__getattribute__(controller, "_active_pending"),
            pending,
        )
        self.assertEqual(controller.snapshot(), before)
        ingress_module._validate_durable_evidence_terminal_for_consumer_v1(
            terminal,
            controller,
        )
        self.assertEqual(
            object.__getattribute__(controller, "_lifecycle"),
            "EMERGENCY_PENDING",
        )
        self.assertEqual(probe.emergency_append_count, 0)
        self.assertEqual(probe.abort_count, 0)

    def test_r19_dynamic_abort_uncertainty_converges_replace_before_or_after_write(
        self,
    ) -> None:
        registry_type = getattr(controller_module, "_WeakIdentityRegistryV1")
        pending_registry = getattr(
            controller_module,
            "_PENDING_EMERGENCY_AUTHORITIES_V1",
        )
        original_replace = registry_type.replace
        reason_type = getattr(
            controller_module,
            "PendingEmergencyAbortReasonV1",
        )
        for timing in ("before_write", "after_write"):
            with self.subTest(timing=timing), self.fresh_case() as case:
                probe = _RuledStoreProbe(case.collected)
                with case.mocked_facade(probe):
                    controller = case.create_controller()
                    _, pending = case._capacity_pending(probe, controller)

                    def abort_uncertain(*_args, **_kwargs):
                        raise RuntimeError(
                            "durable_causal_subject_abort_uncertain"
                        )

                    def uncertain_replace(registry, key, prior, value):
                        if registry is not pending_registry:
                            return original_replace(
                                registry,
                                key,
                                prior,
                                value,
                            )
                        if timing == "after_write":
                            original_replace(registry, key, prior, value)
                        raise RuntimeError(
                            f"injected_abort_replace_{timing}"
                        )

                    with (
                        self._patched_ingress_bridge(
                            "_abort_deferred_emergency_commit_subject_v1",
                            abort_uncertain,
                        ),
                        mock.patch.object(
                            registry_type,
                            "replace",
                            new=uncertain_replace,
                        ),
                        self.assertRaisesRegex(
                            RuntimeError,
                            "^durable_companion_emergency_pending_abort_uncertain$",
                        ) as raised,
                    ):
                        controller.abort_pending_durable_companion_emergency_v1(
                            pending,
                            reason=(
                                reason_type.CALLER_CLOSE_WITH_PENDING
                            ),
                        )
                self.assertIsNone(raised.exception.__cause__)
                tombstone = pending_registry.lookup(pending)
                self.assertEqual(tombstone.lifecycle, "ABORT_FAILED_CLOSED")
                self.assertIsNone(tombstone.retained_receipt)
                self.assertEqual(
                    object.__getattribute__(controller, "_lifecycle"),
                    "DURABILITY_UNCERTAIN_HALTED",
                )
                self.assertEqual(probe.abort_count, 1)
                with self.assertRaisesRegex(
                    ValueError,
                    "^durable_companion_emergency_pending_abort_consumed$",
                ):
                    controller.abort_pending_durable_companion_emergency_v1(
                        pending,
                        reason=reason_type.CALLER_CLOSE_WITH_PENDING,
                    )


if __name__ == "__main__":
    unittest.main()
