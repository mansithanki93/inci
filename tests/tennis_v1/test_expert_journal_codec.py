from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from enum import Enum
from hashlib import sha256
import inspect
import json
import struct
import unittest
from unittest import mock

import inci_tennis_expert.journal_codec as journal_codec_module
from inci_tennis_expert.contracts import (
    ExpertCapacityProofV1,
    ExpertCurrentEnvironmentV1,
    ExpertEventKindV1,
    ExpertObservationIgnoredPayloadV1,
    ExpertObservationRejectedPayloadV1,
    ExpertRejectReasonV1,
    ExpertRejectedDraftV1,
    ExpertSynchronizationAppliedPayloadV1,
    ExpertSynchronizationDraftV1,
    ExpertEventSchemaBundleV1,
    ExpertEventSchemaPinV1,
    ExpertJournalCursorV1,
    ExpertJournalGroupV1,
    ExpertJournalRecordV1,
    ExpertNormalizerPinV1,
    ExpertNormalizerRegistryV1,
    ExpertParentEvidenceV1,
    ExpertPayloadDescriptorV1,
    ExpertProviderDomainBindingV1,
    ExpertRetentionBindingV1,
    ExpertSchemaPinV1,
    ExpertSessionManifestV1,
    ExpertSessionTerminalV1,
    ExpertStructuralSchemaBundleV1,
    ExpertTerminalReasonV1,
    ExpertTraceStepV1,
    compute_expert_capacity_proof_sha256,
    compute_expert_journal_group_sha256,
    compute_expert_journal_record_sha256,
    compute_expert_provider_domain_binding_sha256,
    compute_expert_provider_source_lineage_sha256,
    compute_expert_retention_binding_sha256,
    compute_expert_session_manifest_sha256,
    compute_expert_session_terminal_sha256,
    compute_expert_trace_step_sha256,
    canonical_expert_bytes,
    expert_event_schema_bundle_sha256,
    expert_event_schema_resource_sha256,
    expert_normalizer_registry_sha256,
    expert_structural_schema_bundle_sha256,
)
from inci_tennis_expert.observation import (
    bind_expert_observation_drafts,
    normalize_expert_parent,
)
from inci_tennis_expert.reducer import (
    initial_expert_state,
    reduce_expert_parent,
)
from tests.tennis_v1.test_expert_contracts import synchronization_input
from tests.tennis_v1.test_expert_observation import raw_parent, task6_artifacts
from inci_tennis_expert.journal_codec import (
    EXPERT_EMERGENCY_RESERVE_BYTES,
    EXPERT_FILE_FLAGS,
    EXPERT_FILE_HEADER_BYTES,
    EXPERT_FILE_MAGIC,
    EXPERT_FILE_VERSION,
    EXPERT_FRAME_FIXED_BYTES,
    EXPERT_FRAME_FLAGS,
    EXPERT_FRAME_KIND_MANIFEST,
    EXPERT_FRAME_KIND_PARENT_GROUP,
    EXPERT_FRAME_KIND_TERMINAL,
    EXPERT_FRAME_MAGIC,
    EXPERT_FRAME_PREFIX_BYTES,
    EXPERT_FRAME_TRAILER_BYTES,
    EXPERT_FRAME_TRAILER_MAGIC,
    EXPERT_FRAME_VERSION,
    MAX_EXPERT_EVENT_PAYLOAD_BYTES,
    MAX_EXPERT_FRAME_BYTES,
    MAX_EXPERT_GROUP_METADATA_BYTES,
    MAX_EXPERT_GROUP_PAYLOAD_AREA_BYTES,
    MAX_EXPERT_MANIFEST_METADATA_BYTES,
    MAX_EXPERT_OUTCOMES_PER_PARENT,
    MAX_EXPERT_TERMINAL_FRAME_BYTES,
    MAX_EXPERT_TERMINAL_METADATA_BYTES,
    ExpertJournalCodecError,
    ExpertJournalOrderValidator,
    decode_expert_complete_frame,
    decode_expert_file_header,
    decode_expert_frame_prefix,
    decode_expert_group_frame,
    decode_expert_group_frame_structural,
    decode_expert_group_payload_area,
    decode_expert_manifest_frame,
    decode_expert_terminal_frame,
    decode_expert_terminal_frame_structural,
    encode_expert_group_frame,
    encode_expert_file_header,
    encode_expert_group_payload_area,
    encode_expert_manifest_frame,
    encode_expert_terminal_frame,
    expert_trace_seed_sha256,
    validate_expert_group_against_cursor,
    validate_expert_frame_parts,
    validate_expert_terminal_against_cursor,
)


def _independent_frame(
    *,
    kind: int,
    sequence: int,
    metadata: bytes,
    payload_area: bytes,
) -> bytes:
    total = 76 + len(metadata) + len(payload_area)
    prefix = struct.pack(
        ">4sBBHQQII",
        b"IXJF",
        1,
        kind,
        0,
        sequence,
        total,
        len(metadata),
        len(payload_area),
    )
    digest = sha256(
        b"INCI-EXPERT-JOURNAL-FRAME-V1\0"
        + prefix
        + metadata
        + payload_area
    ).digest()
    return (
        prefix
        + metadata
        + payload_area
        + struct.pack(">Q32s4s", total, digest, b"FJXI")
    )


def _independent_project(value: object) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if isinstance(value, Enum):
        return {"$enum": type(value).__name__, "value": value.value}
    if type(value) is tuple:
        return {"$tuple": [_independent_project(item) for item in value]}
    if type(value) is list:
        return {"$list": [_independent_project(item) for item in value]}
    if type(value) is dict:
        return {
            "$dict": [
                [key, _independent_project(value[key])]
                for key in sorted(value)
            ]
        }
    if is_dataclass(value):
        return {
            "$contract": type(value).__name__,
            "$version": 1,
            "fields": {
                item.name: _independent_project(getattr(value, item.name))
                for item in fields(value)
            },
        }
    raise AssertionError(f"unsupported test projection: {type(value)!r}")


def _independent_canonical(value: object) -> bytes:
    return json.dumps(
        {
            "canonical_version": 1,
            "domain": "inci-tennis-expert",
            "value": _independent_project(value),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _unchecked_replace(value: object, **changes: object) -> object:
    replacement = object.__new__(type(value))
    for item in fields(value):
        object.__setattr__(
            replacement,
            item.name,
            changes.get(item.name, getattr(value, item.name)),
        )
    return replacement


_SESSION_ID = "11111111-1111-4111-8111-111111111111"
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_SHA_D = "d" * 64
_SHA_E = "e" * 64
_SHA_F = "f" * 64
_SHA_1 = "1" * 64
_SHA_2 = "2" * 64
_SHA_3 = "3" * 64
_SHA_4 = "4" * 64
_SHA_5 = "5" * 64


def _manifest_fixture() -> ExpertSessionManifestV1:
    structural_pins = (
        ExpertSchemaPinV1(
            "session_manifest",
            "ExpertSessionManifestV1",
            "expert-session-manifest-v1.schema.json",
            _SHA_A,
        ),
        ExpertSchemaPinV1(
            "journal_record",
            "ExpertJournalRecordV1",
            "expert-journal-record-v1.schema.json",
            _SHA_B,
        ),
        ExpertSchemaPinV1(
            "parent_group",
            "ExpertJournalGroupV1",
            "expert-journal-group-v1.schema.json",
            _SHA_C,
        ),
        ExpertSchemaPinV1(
            "session_terminal",
            "ExpertSessionTerminalV1",
            "expert-session-terminal-v1.schema.json",
            _SHA_D,
        ),
    )
    structural_values: dict[str, object] = {
        "schema_version": 1,
        "pins": structural_pins,
    }
    structural = ExpertStructuralSchemaBundleV1(
        **structural_values,
        bundle_sha256=expert_structural_schema_bundle_sha256(
            **structural_values  # type: ignore[arg-type]
        ),
    )
    event_pins = (
        ExpertEventSchemaPinV1(
            ExpertEventKindV1.SYNCHRONIZATION_APPLIED,
            1,
            "ExpertSynchronizationAppliedPayloadV1",
            "expert-synchronization-applied-v1.schema.json",
            expert_event_schema_resource_sha256(
                ExpertEventKindV1.SYNCHRONIZATION_APPLIED
            ),
        ),
        ExpertEventSchemaPinV1(
            ExpertEventKindV1.OBSERVATION_IGNORED,
            1,
            "ExpertObservationIgnoredPayloadV1",
            "expert-observation-ignored-v1.schema.json",
            expert_event_schema_resource_sha256(
                ExpertEventKindV1.OBSERVATION_IGNORED
            ),
        ),
        ExpertEventSchemaPinV1(
            ExpertEventKindV1.OBSERVATION_REJECTED,
            1,
            "ExpertObservationRejectedPayloadV1",
            "expert-observation-rejected-v1.schema.json",
            expert_event_schema_resource_sha256(
                ExpertEventKindV1.OBSERVATION_REJECTED
            ),
        ),
    )
    event_values: dict[str, object] = {
        "schema_version": 1,
        "pins": event_pins,
    }
    event_schemas = ExpertEventSchemaBundleV1(
        **event_values,
        bundle_sha256=expert_event_schema_bundle_sha256(
            **event_values  # type: ignore[arg-type]
        ),
    )
    fallback = ExpertNormalizerPinV1(
        "task6-fallback-v1",
        "fallback",
        "task6",
        "unregistered",
        1,
        _SHA_D,
        _SHA_E,
    )
    normalizer_values: dict[str, object] = {
        "schema_version": 1,
        "fallback": fallback,
        "entries": (),
    }
    normalizers = ExpertNormalizerRegistryV1(
        **normalizer_values,
        registry_sha256=expert_normalizer_registry_sha256(
            **normalizer_values  # type: ignore[arg-type]
        ),
    )
    provider_lineage = compute_expert_provider_source_lineage_sha256(
        "provider-a",
        "free-trial",
        "lineage-a",
        _SHA_D,
    )
    provider_domain_values: dict[str, object] = {
        "schema_version": 1,
        "phase1_session_manifest_sha256": _SHA_A,
        "match_binding_universe_sha256": _SHA_B,
        "provider_id": "provider-a",
        "product_tier": "free-trial",
        "source_lineage_id": "lineage-a",
        "provider_manifest_canonical_sha256": _SHA_D,
        "provider_source_lineage_sha256": provider_lineage,
        "revision_domain_id": "revision-a",
    }
    provider_domain = ExpertProviderDomainBindingV1(
        **provider_domain_values,
        provider_domain_binding_sha256=(
            compute_expert_provider_domain_binding_sha256(
                **provider_domain_values
            )
        ),
    )
    retention_values: dict[str, object] = {
        "schema_version": 1,
        "session_id": _SESSION_ID,
        "evidence_session_manifest_sha256": _SHA_A,
        "provider_request_binding_sha256": _SHA_C,
        "permission_artifact_sha256": _SHA_E,
        "qualification_artifact_sha256": _SHA_F,
        "qualification_trace_sha256": _SHA_1,
        "retention_delete_by_ns": 10_000,
        "access_expires_at_ns": 1_000,
        "analysis_expires_at_ns": 2_000,
    }
    retention = ExpertRetentionBindingV1(
        **retention_values,
        retention_binding_sha256=compute_expert_retention_binding_sha256(
            **retention_values
        ),
    )
    capacity_values: dict[str, object] = {
        "schema_version": 1,
        "match_binding_universe_sha256": _SHA_B,
        "sync_policy_sha256": _SHA_2,
        "maximum_output_count": 64,
        "maximum_synchronization_state_bytes": 131_064,
        "maximum_transition_payload_bytes": 131_064,
        "maximum_rejected_payload_bytes": 131_064,
        "maximum_event_payload_bytes": 131_064,
        "maximum_group_payload_area_bytes": 8_388_608,
        "maximum_group_metadata_bytes": 8_388_532,
        "maximum_group_frame_bytes": 16_777_216,
        "maximum_terminal_metadata_bytes": 1_048_576,
        "maximum_terminal_frame_bytes": 1_048_652,
        "emergency_reserve_bytes": 17_825_868,
    }
    capacity = ExpertCapacityProofV1(
        **capacity_values,
        proof_sha256=compute_expert_capacity_proof_sha256(
            **capacity_values
        ),
    )
    environment = ExpertCurrentEnvironmentV1(
        1,
        _SHA_A,
        _SHA_B,
        _SHA_C,
        _SHA_D,
        _SHA_E,
        _SHA_F,
        _SHA_1,
        _SHA_2,
        normalizers.registry_sha256,
        structural.bundle_sha256,
        event_schemas.bundle_sha256,
    )
    manifest_values: dict[str, object] = {
        "schema_version": 1,
        "session_id": _SESSION_ID,
        "evidence_session_manifest_sha256": _SHA_A,
        "evidence_session_start_record_sha256": _SHA_B,
        "provider_id": "provider-a",
        "product_tier": "free-trial",
        "source_lineage_id": "lineage-a",
        "provider_manifest_file_sha256": _SHA_C,
        "provider_manifest_canonical_sha256": _SHA_D,
        "entitlement_id_sha256": _SHA_4,
        "provider_request_binding_sha256": _SHA_C,
        "permission_artifact_sha256": _SHA_E,
        "qualification_artifact_sha256": _SHA_F,
        "qualification_trace_sha256": _SHA_1,
        "provider_domain": provider_domain,
        "environment": environment,
        "retention": retention,
        "match_binding_universe_sha256": _SHA_B,
        "binding_raw_artifact_id": "binding-raw",
        "binding_raw_artifact_sha256": _SHA_3,
        "binding_review_artifact_id": "binding-review",
        "binding_review_artifact_sha256": _SHA_4,
        "sync_policy_sha256": _SHA_2,
        "initial_synchronization_sha256": _SHA_5,
        "normalizers": normalizers,
        "structural_schemas": structural,
        "event_schemas": event_schemas,
        "capacity": capacity,
        "artifact_pins": (),
    }
    return ExpertSessionManifestV1(
        **manifest_values,
        manifest_sha256=compute_expert_session_manifest_sha256(
            **manifest_values
        ),
    )


def _genesis_cursor(
    manifest: ExpertSessionManifestV1,
) -> ExpertJournalCursorV1:
    initial_state_sha256 = _SHA_3
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
        expert_state_sha256=initial_state_sha256,
        expert_trace_sha256=expert_trace_seed_sha256(
            manifest.session_id,
            manifest.manifest_sha256,
            initial_state_sha256,
        ),
    )


def _group_fixture(
    manifest: ExpertSessionManifestV1,
    prior: ExpertJournalCursorV1,
) -> tuple[ExpertJournalGroupV1, tuple[bytes, ...], ExpertJournalCursorV1]:
    parent = ExpertParentEvidenceV1(
        session_id=manifest.session_id,
        ingest_seq=2 * (prior.group_count + 1),
        record_sha256=_SHA_4,
        event_type="provider_snapshot",
        event_version=1,
        local_wall_ns=100,
        local_monotonic_ns=90,
        clock_uncertainty_ns=2,
    )
    payloads = (b'{"ignored":true}',)
    descriptor = ExpertPayloadDescriptorV1(
        schema_version=1,
        content_type="application/vnd.inci.expert+json",
        payload_encoding="canonical-json-v1",
        payload_contract_name="ExpertObservationIgnoredPayloadV1",
        payload_length=len(payloads[0]),
        payload_sha256=sha256(payloads[0]).hexdigest(),
    )
    post_state_sha256 = _SHA_5
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
        "expert_seq": prior.expert_seq + 1,
        "parent": parent,
        "parent_output_index": 0,
        "parent_output_count": 1,
        "event_kind": ExpertEventKindV1.OBSERVATION_IGNORED,
        "event_version": 1,
        "event_schema_sha256": expert_event_schema_resource_sha256(
            ExpertEventKindV1.OBSERVATION_IGNORED
        ),
        "prior_expert_record_sha256": prior.expert_record_sha256,
        "prior_expert_state_sha256": prior.expert_state_sha256,
        "payload": descriptor,
        "post_expert_state_sha256": post_state_sha256,
    }
    record = ExpertJournalRecordV1(
        **record_values,
        record_sha256=compute_expert_journal_record_sha256(
            **record_values
        ),
    )
    trace_values: dict[str, object] = {
        "schema_version": 1,
        "expert_seq": record.expert_seq,
        "prior_trace_sha256": prior.expert_trace_sha256,
        "expert_record_sha256": record.record_sha256,
        "post_expert_state_sha256": post_state_sha256,
    }
    trace = ExpertTraceStepV1(
        **trace_values,
        post_trace_sha256=compute_expert_trace_step_sha256(
            **trace_values
        ),
    )
    group_values: dict[str, object] = {
        "schema_version": 1,
        "session_id": manifest.session_id,
        "expert_manifest_sha256": manifest.manifest_sha256,
        "group_sequence": prior.group_count + 1,
        "parent": parent,
        "parent_output_count": 1,
        "first_expert_seq": record.expert_seq,
        "prior_expert_record_sha256": prior.expert_record_sha256,
        "prior_expert_state_sha256": prior.expert_state_sha256,
        "records": (record,),
        "trace_steps": (trace,),
        "final_expert_record_sha256": record.record_sha256,
        "post_expert_state_sha256": post_state_sha256,
        "post_trace_sha256": trace.post_trace_sha256,
    }
    group = ExpertJournalGroupV1(
        **group_values,
        group_sha256=compute_expert_journal_group_sha256(
            **group_values
        ),
    )
    cursor = ExpertJournalCursorV1(
        schema_version=1,
        session_id=manifest.session_id,
        group_count=prior.group_count + 1,
        record_count=prior.record_count + 1,
        last_parent_ingest_seq=parent.ingest_seq,
        last_parent_record_sha256=parent.record_sha256,
        expert_seq=record.expert_seq,
        expert_record_sha256=record.record_sha256,
        expert_state_sha256=post_state_sha256,
        expert_trace_sha256=trace.post_trace_sha256,
    )
    return group, payloads, cursor


def _terminal_fixture(
    manifest: ExpertSessionManifestV1,
    cursor: ExpertJournalCursorV1,
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
        "evidence_terminal_ingest_seq": (
            2 + cursor.group_count + cursor.group_count
        ),
        "evidence_terminal_record_sha256": _SHA_E,
        "evidence_terminal_clean": True,
        "evidence_terminal_reason": "operator_stop",
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
        "clean": True,
        "reason": ExpertTerminalReasonV1.OPERATOR_STOP,
        "research_evaluable": False,
    }
    return ExpertSessionTerminalV1(
        **values,
        terminal_sha256=compute_expert_session_terminal_sha256(**values),
    )


class ExpertJournalHeaderTests(unittest.TestCase):
    def test_header_is_the_exact_ruled_sixteen_bytes(self) -> None:
        expected = struct.pack(">8sHHI", b"INCIXJ01", 1, 0, 16)

        encoded = encode_expert_file_header()

        self.assertEqual(encoded, expected)
        self.assertEqual(len(encoded), 16)
        self.assertEqual(EXPERT_FILE_MAGIC, b"INCIXJ01")
        self.assertEqual(EXPERT_FILE_VERSION, 1)
        self.assertEqual(EXPERT_FILE_FLAGS, 0)
        self.assertEqual(EXPERT_FILE_HEADER_BYTES, 16)
        self.assertIsNone(decode_expert_file_header(encoded))

    def test_header_rejects_every_field_mutation_and_wrong_length(self) -> None:
        valid = bytearray(encode_expert_file_header())
        candidates = [
            bytes(valid[:15]),
            bytes(valid) + b"\x00",
            struct.pack(">8sHHI", b"WRONGMAG", 1, 0, 16),
            struct.pack(">8sHHI", b"INCIXJ01", 2, 0, 16),
            struct.pack(">8sHHI", b"INCIXJ01", 1, 1, 16),
            struct.pack(">8sHHI", b"INCIXJ01", 1, 0, 15),
        ]
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(
                    ExpertJournalCodecError,
                    r"\Aexpert_journal_header_invalid\Z",
                ):
                    decode_expert_file_header(candidate)

    def test_header_requires_exact_bytes(self) -> None:
        with self.assertRaisesRegex(TypeError, r"\Acontent\Z"):
            decode_expert_file_header(  # type: ignore[arg-type]
                bytearray(encode_expert_file_header())
            )


class ExpertJournalCeilingTests(unittest.TestCase):
    def test_frame_sizes_and_capacity_derivations_are_exact(self) -> None:
        self.assertEqual(EXPERT_FRAME_PREFIX_BYTES, 32)
        self.assertEqual(EXPERT_FRAME_TRAILER_BYTES, 44)
        self.assertEqual(EXPERT_FRAME_FIXED_BYTES, 76)
        self.assertEqual(MAX_EXPERT_GROUP_METADATA_BYTES, 8_388_532)
        self.assertEqual(MAX_EXPERT_GROUP_PAYLOAD_AREA_BYTES, 8_388_608)
        self.assertEqual(MAX_EXPERT_FRAME_BYTES, 16_777_216)
        self.assertEqual(MAX_EXPERT_TERMINAL_METADATA_BYTES, 1_048_576)
        self.assertEqual(MAX_EXPERT_TERMINAL_FRAME_BYTES, 1_048_652)
        self.assertEqual(EXPERT_EMERGENCY_RESERVE_BYTES, 17_825_868)
        self.assertEqual(MAX_EXPERT_EVENT_PAYLOAD_BYTES, 131_064)
        self.assertEqual(MAX_EXPERT_OUTCOMES_PER_PARENT, 64)
        self.assertEqual(
            MAX_EXPERT_GROUP_METADATA_BYTES
            + MAX_EXPERT_GROUP_PAYLOAD_AREA_BYTES
            + EXPERT_FRAME_FIXED_BYTES,
            MAX_EXPERT_FRAME_BYTES,
        )
        self.assertEqual(
            MAX_EXPERT_TERMINAL_METADATA_BYTES + EXPERT_FRAME_FIXED_BYTES,
            MAX_EXPERT_TERMINAL_FRAME_BYTES,
        )
        self.assertEqual(
            MAX_EXPERT_FRAME_BYTES + MAX_EXPERT_TERMINAL_FRAME_BYTES,
            EXPERT_EMERGENCY_RESERVE_BYTES,
        )
        self.assertEqual(
            MAX_EXPERT_OUTCOMES_PER_PARENT
            * (MAX_EXPERT_EVENT_PAYLOAD_BYTES + 8),
            MAX_EXPERT_GROUP_PAYLOAD_AREA_BYTES,
        )


class ExpertFrameWireTests(unittest.TestCase):
    def test_prefix_trailer_digest_and_fixed_overhead_are_exact(self) -> None:
        metadata = b"canonical-metadata"
        payload_area = struct.pack(">Q", 3) + b"abc"
        frame = _independent_frame(
            kind=EXPERT_FRAME_KIND_PARENT_GROUP,
            sequence=1,
            metadata=metadata,
            payload_area=payload_area,
        )
        prefix = frame[:EXPERT_FRAME_PREFIX_BYTES]
        trailer = frame[-EXPERT_FRAME_TRAILER_BYTES:]

        decoded_prefix = decode_expert_frame_prefix(prefix)
        frame_sha256 = validate_expert_frame_parts(
            prefix,
            metadata,
            payload_area,
            trailer,
        )
        decoded = decode_expert_complete_frame(frame)

        self.assertEqual(EXPERT_FRAME_MAGIC, b"IXJF")
        self.assertEqual(EXPERT_FRAME_VERSION, 1)
        self.assertEqual(EXPERT_FRAME_FLAGS, 0)
        self.assertEqual(EXPERT_FRAME_TRAILER_MAGIC, b"FJXI")
        self.assertEqual(
            decoded_prefix,
            (
                EXPERT_FRAME_KIND_PARENT_GROUP,
                1,
                len(frame),
                len(metadata),
                len(payload_area),
            ),
        )
        self.assertEqual(
            decoded,
            (
                EXPERT_FRAME_KIND_PARENT_GROUP,
                1,
                metadata,
                payload_area,
                frame_sha256,
            ),
        )
        self.assertEqual(frame_sha256, frame[-36:-4].hex())
        self.assertEqual(
            len(frame) - len(metadata) - len(payload_area),
            EXPERT_FRAME_FIXED_BYTES,
        )

    def test_complete_frame_rejects_every_cut_and_bytes_after_frame(self) -> None:
        frame = _independent_frame(
            kind=EXPERT_FRAME_KIND_PARENT_GROUP,
            sequence=1,
            metadata=b"metadata",
            payload_area=struct.pack(">Q", 1) + b"x",
        )
        for cut in range(len(frame)):
            with self.subTest(cut=cut):
                with self.assertRaises(ExpertJournalCodecError):
                    decode_expert_complete_frame(frame[:cut])
        with self.assertRaises(ExpertJournalCodecError):
            decode_expert_complete_frame(frame + b"x")

    def test_complete_frame_rejects_corruption_in_every_region(self) -> None:
        frame = _independent_frame(
            kind=EXPERT_FRAME_KIND_PARENT_GROUP,
            sequence=1,
            metadata=b"metadata",
            payload_area=struct.pack(">Q", 1) + b"x",
        )
        offsets = (
            0,
            4,
            5,
            6,
            8,
            16,
            24,
            28,
            EXPERT_FRAME_PREFIX_BYTES,
            EXPERT_FRAME_PREFIX_BYTES + len(b"metadata"),
            len(frame) - EXPERT_FRAME_TRAILER_BYTES,
            len(frame) - 36,
            len(frame) - 1,
        )
        for offset in offsets:
            candidate = bytearray(frame)
            candidate[offset] ^= 1
            with self.subTest(offset=offset):
                with self.assertRaises(ExpertJournalCodecError):
                    decode_expert_complete_frame(bytes(candidate))

    def test_prefix_rejects_unknown_contract_and_oversize_before_body(self) -> None:
        invalid_prefixes = (
            struct.pack(">4sBBHQQII", b"NOPE", 1, 2, 0, 1, 76, 0, 0),
            struct.pack(">4sBBHQQII", b"IXJF", 2, 2, 0, 1, 76, 0, 0),
            struct.pack(">4sBBHQQII", b"IXJF", 1, 0, 0, 1, 76, 0, 0),
            struct.pack(">4sBBHQQII", b"IXJF", 1, 4, 0, 1, 76, 0, 0),
            struct.pack(">4sBBHQQII", b"IXJF", 1, 2, 1, 1, 76, 0, 0),
            struct.pack(
                ">4sBBHQQII",
                b"IXJF",
                1,
                2,
                0,
                1,
                MAX_EXPERT_FRAME_BYTES + 1,
                MAX_EXPERT_GROUP_METADATA_BYTES,
                MAX_EXPERT_GROUP_PAYLOAD_AREA_BYTES + 1,
            ),
            struct.pack(
                ">4sBBHQQII",
                b"IXJF",
                1,
                1,
                0,
                0,
                77 + MAX_EXPERT_MANIFEST_METADATA_BYTES,
                MAX_EXPERT_MANIFEST_METADATA_BYTES + 1,
                0,
            ),
            struct.pack(
                ">4sBBHQQII",
                b"IXJF",
                1,
                3,
                0,
                1,
                MAX_EXPERT_TERMINAL_FRAME_BYTES + 1,
                MAX_EXPERT_TERMINAL_METADATA_BYTES + 1,
                0,
            ),
        )
        for prefix in invalid_prefixes:
            with self.subTest(prefix=prefix):
                with self.assertRaises(ExpertJournalCodecError):
                    decode_expert_frame_prefix(prefix)

    def test_validate_parts_rejects_wrong_lengths_digest_and_trailer(self) -> None:
        frame = _independent_frame(
            kind=EXPERT_FRAME_KIND_PARENT_GROUP,
            sequence=1,
            metadata=b"m",
            payload_area=struct.pack(">Q", 0),
        )
        prefix = frame[:32]
        metadata = frame[32:33]
        payload_area = frame[33:-44]
        trailer = frame[-44:]
        candidates = (
            (prefix, metadata + b"x", payload_area, trailer),
            (prefix, metadata, payload_area + b"x", trailer),
            (prefix, metadata, payload_area, trailer[:-1]),
            (
                prefix,
                metadata,
                payload_area,
                struct.pack(
                    ">Q32s4s",
                    len(frame) + 1,
                    bytes.fromhex("00" * 32),
                    b"FJXI",
                ),
            ),
            (
                prefix,
                metadata,
                payload_area,
                trailer[:-4] + b"NOPE",
            ),
        )
        for parts in candidates:
            with self.subTest(parts=tuple(map(len, parts))):
                with self.assertRaises(ExpertJournalCodecError):
                    validate_expert_frame_parts(*parts)

    def test_wire_decoder_requires_exact_bytes(self) -> None:
        frame = _independent_frame(
            kind=EXPERT_FRAME_KIND_TERMINAL,
            sequence=1,
            metadata=b"terminal",
            payload_area=b"",
        )
        with self.assertRaises(TypeError):
            decode_expert_complete_frame(bytearray(frame))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            decode_expert_frame_prefix(bytearray(frame[:32]))  # type: ignore[arg-type]


class ExpertTypedFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = _manifest_fixture()
        self.genesis = _genesis_cursor(self.manifest)
        self.group, self.payloads, self.after_group = _group_fixture(
            self.manifest,
            self.genesis,
        )
        self.terminal = _terminal_fixture(
            self.manifest,
            self.after_group,
        )

    def test_manifest_frame_is_exact_canonical_metadata_at_sequence_zero(
        self,
    ) -> None:
        expected = _independent_frame(
            kind=EXPERT_FRAME_KIND_MANIFEST,
            sequence=0,
            metadata=_independent_canonical(self.manifest),
            payload_area=b"",
        )

        encoded = encode_expert_manifest_frame(self.manifest)

        self.assertEqual(encoded, expected)
        self.assertEqual(decode_expert_manifest_frame(encoded), self.manifest)

    def test_group_frame_binds_prior_cursor_records_payloads_and_trace(
        self,
    ) -> None:
        payload_area = b"".join(
            struct.pack(">Q", len(payload)) + payload
            for payload in self.payloads
        )
        expected = _independent_frame(
            kind=EXPERT_FRAME_KIND_PARENT_GROUP,
            sequence=1,
            metadata=_independent_canonical(self.group),
            payload_area=payload_area,
        )

        encoded = encode_expert_group_frame(
            self.group,
            self.payloads,
            prior_cursor=self.genesis,
        )

        self.assertEqual(encoded, expected)
        self.assertEqual(
            decode_expert_group_frame(
                encoded,
                prior_cursor=self.genesis,
            ),
            (self.group, self.payloads),
        )

    def test_structural_group_decode_defers_only_external_cursor_facts(
        self,
    ) -> None:
        frame = encode_expert_group_frame(
            self.group,
            self.payloads,
            prior_cursor=self.genesis,
        )
        self.assertEqual(
            decode_expert_group_frame_structural(frame),
            (self.group, self.payloads),
        )
        self.assertIsNone(
            validate_expert_group_against_cursor(
                self.group,
                self.payloads,
                self.genesis,
            )
        )

        group_values = {
            item.name: getattr(self.group, item.name)
            for item in fields(self.group)
            if item.name != "group_sha256"
        }
        group_values["group_sequence"] = 2
        sequence_two_group = ExpertJournalGroupV1(
            **group_values,
            group_sha256=compute_expert_journal_group_sha256(
                **group_values
            ),
        )
        structurally_valid = _independent_frame(
            kind=EXPERT_FRAME_KIND_PARENT_GROUP,
            sequence=2,
            metadata=_independent_canonical(sequence_two_group),
            payload_area=(
                struct.pack(">Q", len(self.payloads[0]))
                + self.payloads[0]
            ),
        )
        decoded_group, decoded_payloads = (
            decode_expert_group_frame_structural(structurally_valid)
        )
        self.assertEqual(decoded_group, sequence_two_group)
        with self.assertRaises(ExpertJournalCodecError):
            validate_expert_group_against_cursor(
                decoded_group,
                decoded_payloads,
                self.genesis,
            )

        wrong_physical_sequence = _independent_frame(
            kind=EXPERT_FRAME_KIND_PARENT_GROUP,
            sequence=2,
            metadata=_independent_canonical(self.group),
            payload_area=(
                struct.pack(">Q", len(self.payloads[0]))
                + self.payloads[0]
            ),
        )
        with self.assertRaises(ExpertJournalCodecError):
            decode_expert_group_frame_structural(wrong_physical_sequence)

    def test_terminal_frame_is_group_count_plus_one_and_cursor_aligned(
        self,
    ) -> None:
        expected = _independent_frame(
            kind=EXPERT_FRAME_KIND_TERMINAL,
            sequence=self.after_group.group_count + 1,
            metadata=_independent_canonical(self.terminal),
            payload_area=b"",
        )

        encoded = encode_expert_terminal_frame(
            self.terminal,
            final_cursor=self.after_group,
        )

        self.assertEqual(encoded, expected)
        self.assertEqual(
            decode_expert_terminal_frame(
                encoded,
                final_cursor=self.after_group,
            ),
            self.terminal,
        )

    def test_structural_terminal_decode_defers_only_external_cursor_facts(
        self,
    ) -> None:
        frame = encode_expert_terminal_frame(
            self.terminal,
            final_cursor=self.after_group,
        )
        self.assertEqual(
            decode_expert_terminal_frame_structural(frame),
            self.terminal,
        )
        self.assertIsNone(
            validate_expert_terminal_against_cursor(
                self.terminal,
                self.after_group,
            )
        )
        with self.assertRaises(ExpertJournalCodecError):
            validate_expert_terminal_against_cursor(
                self.terminal,
                self.genesis,
            )

        wrong_physical_sequence = _independent_frame(
            kind=EXPERT_FRAME_KIND_TERMINAL,
            sequence=self.terminal.expert_group_count + 2,
            metadata=_independent_canonical(self.terminal),
            payload_area=b"",
        )
        with self.assertRaises(ExpertJournalCodecError):
            decode_expert_terminal_frame_structural(wrong_physical_sequence)

    def test_structural_terminal_rejects_count_and_zero_shape_independently(
        self,
    ) -> None:
        cases = (
            {"evidence_raw_count": self.terminal.expert_group_count + 1},
            {
                "expert_group_count": 0,
                "evidence_raw_count": 0,
                "expert_record_count": 1,
                "last_parent_ingest_seq": 0,
                "final_expert_seq": 1,
            },
            {
                "expert_group_count": 0,
                "evidence_raw_count": 0,
                "expert_record_count": 0,
                "last_parent_ingest_seq": 1,
                "final_expert_seq": 0,
            },
            {
                "expert_group_count": 1,
                "evidence_raw_count": 1,
                "expert_record_count": 0,
                "last_parent_ingest_seq": 1,
                "final_expert_seq": 0,
            },
            {
                "expert_group_count": 1,
                "evidence_raw_count": 1,
                "expert_record_count": 1,
                "last_parent_ingest_seq": 0,
                "final_expert_seq": 1,
            },
        )
        for changes in cases:
            with self.subTest(changes=changes):
                forged = _unchecked_replace(
                    self.terminal,
                    **changes,
                )
                frame = _independent_frame(
                    kind=EXPERT_FRAME_KIND_TERMINAL,
                    sequence=forged.expert_group_count + 1,
                    metadata=_independent_canonical(forged),
                    payload_area=b"",
                )
                with mock.patch.object(
                    journal_codec_module,
                    "_decode_contract_metadata",
                    return_value=forged,
                ):
                    with self.assertRaises(ExpertJournalCodecError):
                        decode_expert_terminal_frame_structural(frame)

    def test_empty_session_terminal_is_frame_one(self) -> None:
        terminal = _terminal_fixture(self.manifest, self.genesis)

        encoded = encode_expert_terminal_frame(
            terminal,
            final_cursor=self.genesis,
        )

        kind, sequence, _, payload_area, _ = decode_expert_complete_frame(
            encoded
        )
        self.assertEqual(kind, EXPERT_FRAME_KIND_TERMINAL)
        self.assertEqual(sequence, 1)
        self.assertEqual(payload_area, b"")
        self.assertEqual(
            decode_expert_terminal_frame(
                encoded,
                final_cursor=self.genesis,
            ),
            terminal,
        )

    def test_group_rejects_stale_cursor_and_wrong_first_expert_sequence(
        self,
    ) -> None:
        stale = replace(
            self.genesis,
            expert_record_sha256=_SHA_F,
        )
        with self.assertRaises(ExpertJournalCodecError):
            encode_expert_group_frame(
                self.group,
                self.payloads,
                prior_cursor=stale,
            )

        sequence_two_group, sequence_two_payloads, _ = _group_fixture(
            self.manifest,
            self.after_group,
        )
        wrong_first_sequence_cursor = replace(
            self.after_group,
            record_count=self.after_group.record_count + 1,
            expert_seq=self.after_group.expert_seq + 1,
        )
        with self.assertRaises(ExpertJournalCodecError):
            encode_expert_group_frame(
                sequence_two_group,
                sequence_two_payloads,
                prior_cursor=wrong_first_sequence_cursor,
            )

    def test_genesis_cursor_must_be_anchored_to_manifest_and_trace_seed(
        self,
    ) -> None:
        forged_record = replace(
            self.genesis,
            expert_record_sha256=_SHA_F,
        )
        forged_record_group, forged_payloads, _ = _group_fixture(
            self.manifest,
            forged_record,
        )
        with self.assertRaises(ExpertJournalCodecError):
            encode_expert_group_frame(
                forged_record_group,
                forged_payloads,
                prior_cursor=forged_record,
            )

        forged_trace = replace(
            self.genesis,
            expert_trace_sha256=_SHA_F,
        )
        forged_trace_group, forged_payloads, _ = _group_fixture(
            self.manifest,
            forged_trace,
        )
        with self.assertRaises(ExpertJournalCodecError):
            encode_expert_group_frame(
                forged_trace_group,
                forged_payloads,
                prior_cursor=forged_trace,
            )

        forged_terminal = _terminal_fixture(self.manifest, forged_trace)
        with self.assertRaises(ExpertJournalCodecError):
            encode_expert_terminal_frame(
                forged_terminal,
                final_cursor=forged_trace,
            )

    def test_genesis_cursor_requires_exact_zero_numeric_shape(self) -> None:
        for field_name in (
            "record_count",
            "last_parent_ingest_seq",
            "expert_seq",
        ):
            with self.subTest(field_name=field_name):
                forged = _unchecked_replace(
                    self.genesis,
                    **{field_name: 1},
                )
                with self.assertRaisesRegex(
                    ExpertJournalCodecError,
                    r"\Aexpert_journal_genesis_cursor_mismatch\Z",
                ):
                    journal_codec_module._validate_genesis_cursor_anchor(
                        forged,
                        expert_manifest_sha256=(
                            self.manifest.manifest_sha256
                        ),
                    )

    def test_terminal_rejects_a_different_final_cursor(self) -> None:
        with self.assertRaises(ExpertJournalCodecError):
            encode_expert_terminal_frame(
                self.terminal,
                final_cursor=self.genesis,
            )
        encoded = _independent_frame(
            kind=EXPERT_FRAME_KIND_TERMINAL,
            sequence=2,
            metadata=_independent_canonical(self.terminal),
            payload_area=b"",
        )
        with self.assertRaises(ExpertJournalCodecError):
            decode_expert_terminal_frame(
                encoded,
                final_cursor=self.genesis,
            )

    def test_reframed_payload_mutation_fails_descriptor_validation(
        self,
    ) -> None:
        valid = encode_expert_group_frame(
            self.group,
            self.payloads,
            prior_cursor=self.genesis,
        )
        _, _, metadata, payload_area, _ = decode_expert_complete_frame(valid)
        mutated = bytearray(payload_area)
        mutated[-1] ^= 1
        reframed = _independent_frame(
            kind=EXPERT_FRAME_KIND_PARENT_GROUP,
            sequence=1,
            metadata=metadata,
            payload_area=bytes(mutated),
        )

        with self.assertRaises(ExpertJournalCodecError):
            decode_expert_group_frame(
                reframed,
                prior_cursor=self.genesis,
            )

    def test_reframed_record_group_trace_and_state_mutations_fail(self) -> None:
        metadata = json.loads(_independent_canonical(self.group))
        fields_document = metadata["value"]["fields"]
        mutation_paths = (
            ("group_sha256",),
            ("records", "$tuple", 0, "fields", "record_sha256"),
            ("records", "$tuple", 0, "fields", "post_expert_state_sha256"),
            ("trace_steps", "$tuple", 0, "fields", "post_trace_sha256"),
            (
                "records",
                "$tuple",
                0,
                "fields",
                "payload",
                "fields",
                "payload_sha256",
            ),
        )
        for path in mutation_paths:
            candidate = json.loads(json.dumps(metadata))
            target = candidate["value"]["fields"]
            for component in path[:-1]:
                target = target[component]
            target[path[-1]] = _SHA_F
            candidate_metadata = json.dumps(
                candidate,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
            reframed = _independent_frame(
                kind=EXPERT_FRAME_KIND_PARENT_GROUP,
                sequence=1,
                metadata=candidate_metadata,
                payload_area=(
                    struct.pack(">Q", len(self.payloads[0]))
                    + self.payloads[0]
                ),
            )
            with self.subTest(path=path):
                with self.assertRaises(ExpertJournalCodecError):
                    decode_expert_group_frame(
                        reframed,
                        prior_cursor=self.genesis,
                    )

    def test_diagnostic_group_metadata_matches_normal_structural_validation(
        self,
    ) -> None:
        valid = _independent_canonical(self.group)
        self.assertEqual(
            journal_codec_module.validate_expert_group_metadata_diagnostic(
                valid
            ),
            (
                self.group.group_sequence,
                len(self.group.records),
                tuple(
                    (
                        record.payload.payload_length,
                        record.payload.payload_sha256,
                    )
                    for record in self.group.records
                ),
            ),
        )
        with (
            mock.patch.object(
                journal_codec_module,
                "_decode_contract_metadata",
                side_effect=AssertionError("normal metadata decoder invoked"),
            ),
            mock.patch.object(
                journal_codec_module,
                "decode_expert_event_payload",
                side_effect=AssertionError("payload decoder invoked"),
            ),
            mock.patch.object(
                ExpertJournalGroupV1,
                "__init__",
                side_effect=AssertionError("group contract constructed"),
            ),
        ):
            self.assertEqual(
                (
                    journal_codec_module
                    .validate_expert_group_metadata_diagnostic(valid)
                )[0:2],
                (self.group.group_sequence, len(self.group.records)),
            )
        mutations = (
            (
                ("value", "fields", "session_id"),
                "/",
            ),
            (
                (
                    "value",
                    "fields",
                    "records",
                    "$tuple",
                    0,
                    "fields",
                    "payload",
                    "fields",
                    "content_type",
                ),
                "bad",
            ),
            (
                (
                    "value",
                    "fields",
                    "records",
                    "$tuple",
                    0,
                    "fields",
                    "payload",
                    "fields",
                    "payload_encoding",
                ),
                "bad",
            ),
            (
                (
                    "value",
                    "fields",
                    "records",
                    "$tuple",
                    0,
                    "fields",
                    "payload",
                    "fields",
                    "payload_contract_name",
                ),
                "bad",
            ),
            (
                (
                    "value",
                    "fields",
                    "records",
                    "$tuple",
                    0,
                    "fields",
                    "payload",
                    "fields",
                    "payload_length",
                ),
                131_065,
            ),
            (
                (
                    "value",
                    "fields",
                    "records",
                    "$tuple",
                    0,
                    "fields",
                    "event_version",
                ),
                2,
            ),
            (
                (
                    "value",
                    "fields",
                    "records",
                    "$tuple",
                    0,
                    "fields",
                    "parent_output_index",
                ),
                9,
            ),
            (
                (
                    "value",
                    "fields",
                    "records",
                    "$tuple",
                    0,
                    "fields",
                    "record_sha256",
                ),
                "0" * 64,
            ),
            (
                (
                    "value",
                    "fields",
                    "trace_steps",
                    "$tuple",
                    0,
                    "fields",
                    "expert_seq",
                ),
                99,
            ),
            (
                ("value", "fields", "group_sha256"),
                "0" * 64,
            ),
            (
                ("value", "fields", "records", "$tuple"),
                [1],
            ),
        )
        for path, replacement in mutations:
            candidate = json.loads(valid)
            target = candidate
            for component in path[:-1]:
                target = target[component]
            target[path[-1]] = replacement
            candidate_metadata = json.dumps(
                candidate,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
            with self.subTest(path=path):
                with self.assertRaises(ExpertJournalCodecError):
                    journal_codec_module._decode_contract_metadata(
                        candidate_metadata,
                        ExpertJournalGroupV1,
                    )
                with self.assertRaises(ExpertJournalCodecError):
                    (
                        journal_codec_module
                        .validate_expert_group_metadata_diagnostic(
                            candidate_metadata
                        )
                    )

    def test_canonical_decoder_rejects_noncanonical_duplicate_unknown_and_schema(
        self,
    ) -> None:
        valid = _independent_canonical(self.manifest)
        unknown = json.loads(valid)
        unknown["value"]["$contract"] = "UnknownContractV1"
        schema = json.loads(valid)
        schema["value"]["fields"]["schema_version"] = 2
        candidates = (
            b" " + valid,
            b'{"canonical_version":1,' + valid[1:],
            json.dumps(
                unknown,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii"),
            json.dumps(
                schema,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii"),
        )
        for metadata in candidates:
            frame = _independent_frame(
                kind=EXPERT_FRAME_KIND_MANIFEST,
                sequence=0,
                metadata=metadata,
                payload_area=b"",
            )
            with self.subTest(metadata=metadata[:48]):
                with self.assertRaises(ExpertJournalCodecError):
                    decode_expert_manifest_frame(frame)


class ExpertJournalOrderTests(unittest.TestCase):
    @staticmethod
    def _prefix(kind: int, sequence: int) -> bytes:
        metadata_bytes = 1
        payload_area_bytes = (
            8 if kind == EXPERT_FRAME_KIND_PARENT_GROUP else 0
        )
        total = 76 + metadata_bytes + payload_area_bytes
        return struct.pack(
            ">4sBBHQQII",
            b"IXJF",
            1,
            kind,
            0,
            sequence,
            total,
            metadata_bytes,
            payload_area_bytes,
        )

    def _accept(self, *entries: tuple[int, int]) -> None:
        validator = ExpertJournalOrderValidator()
        for kind, sequence in entries:
            validator.accept(self._prefix(kind, sequence))
        validator.require_terminal()

    def test_empty_one_group_and_multi_group_sequences_are_exact(self) -> None:
        self._accept(
            (EXPERT_FRAME_KIND_MANIFEST, 0),
            (EXPERT_FRAME_KIND_TERMINAL, 1),
        )
        self._accept(
            (EXPERT_FRAME_KIND_MANIFEST, 0),
            (EXPERT_FRAME_KIND_PARENT_GROUP, 1),
            (EXPERT_FRAME_KIND_TERMINAL, 2),
        )
        self._accept(
            (EXPERT_FRAME_KIND_MANIFEST, 0),
            (EXPERT_FRAME_KIND_PARENT_GROUP, 1),
            (EXPERT_FRAME_KIND_PARENT_GROUP, 2),
            (EXPERT_FRAME_KIND_PARENT_GROUP, 3),
            (EXPERT_FRAME_KIND_TERMINAL, 4),
        )

    def test_order_rejects_gap_duplicate_zero_and_wrong_terminal_sequence(
        self,
    ) -> None:
        invalid_sequences = (
            ((EXPERT_FRAME_KIND_PARENT_GROUP, 1),),
            ((EXPERT_FRAME_KIND_MANIFEST, 1),),
            (
                (EXPERT_FRAME_KIND_MANIFEST, 0),
                (EXPERT_FRAME_KIND_PARENT_GROUP, 0),
            ),
            (
                (EXPERT_FRAME_KIND_MANIFEST, 0),
                (EXPERT_FRAME_KIND_PARENT_GROUP, 2),
            ),
            (
                (EXPERT_FRAME_KIND_MANIFEST, 0),
                (EXPERT_FRAME_KIND_PARENT_GROUP, 1),
                (EXPERT_FRAME_KIND_PARENT_GROUP, 1),
            ),
            (
                (EXPERT_FRAME_KIND_MANIFEST, 0),
                (EXPERT_FRAME_KIND_PARENT_GROUP, 1),
                (EXPERT_FRAME_KIND_TERMINAL, 3),
            ),
        )
        for entries in invalid_sequences:
            validator = ExpertJournalOrderValidator()
            with self.subTest(entries=entries):
                with self.assertRaises(ExpertJournalCodecError):
                    for kind, sequence in entries:
                        validator.accept(self._prefix(kind, sequence))

    def test_order_rejects_duplicate_manifest_and_bytes_after_terminal(
        self,
    ) -> None:
        validator = ExpertJournalOrderValidator()
        validator.accept(self._prefix(EXPERT_FRAME_KIND_MANIFEST, 0))
        with self.assertRaises(ExpertJournalCodecError):
            validator.accept(self._prefix(EXPERT_FRAME_KIND_MANIFEST, 0))

        validator = ExpertJournalOrderValidator()
        validator.accept(self._prefix(EXPERT_FRAME_KIND_MANIFEST, 0))
        validator.accept(self._prefix(EXPERT_FRAME_KIND_TERMINAL, 1))
        with self.assertRaises(ExpertJournalCodecError):
            validator.accept(self._prefix(EXPERT_FRAME_KIND_PARENT_GROUP, 2))

    def test_order_requires_a_terminal(self) -> None:
        validator = ExpertJournalOrderValidator()
        validator.accept(self._prefix(EXPERT_FRAME_KIND_MANIFEST, 0))
        with self.assertRaises(ExpertJournalCodecError):
            validator.require_terminal()


class ExpertGroupPayloadAreaTests(unittest.TestCase):
    def test_payload_area_uses_one_big_endian_length_per_payload(self) -> None:
        payloads = (b"alpha", b"", b"\x00\xff")
        expected = (
            struct.pack(">Q", 5)
            + b"alpha"
            + struct.pack(">Q", 0)
            + struct.pack(">Q", 2)
            + b"\x00\xff"
        )

        encoded = encode_expert_group_payload_area(payloads)

        self.assertEqual(encoded, expected)
        self.assertEqual(
            decode_expert_group_payload_area(encoded, expected_count=3),
            payloads,
        )

    def test_payload_area_accepts_the_exact_aggregate_boundary(self) -> None:
        payload = b"x" * MAX_EXPERT_EVENT_PAYLOAD_BYTES
        payloads = (payload,) * MAX_EXPERT_OUTCOMES_PER_PARENT

        encoded = encode_expert_group_payload_area(payloads)

        self.assertEqual(len(encoded), MAX_EXPERT_GROUP_PAYLOAD_AREA_BYTES)
        decoded = decode_expert_group_payload_area(
            encoded,
            expected_count=MAX_EXPERT_OUTCOMES_PER_PARENT,
        )
        self.assertEqual(len(decoded), MAX_EXPERT_OUTCOMES_PER_PARENT)
        self.assertTrue(all(item == payload for item in decoded))

    def test_payload_area_rejects_count_item_and_aggregate_overflow(self) -> None:
        invalid = (
            (),
            (b"",) * (MAX_EXPERT_OUTCOMES_PER_PARENT + 1),
            (b"x" * (MAX_EXPERT_EVENT_PAYLOAD_BYTES + 1),),
        )
        for payloads in invalid:
            with self.subTest(count=len(payloads)):
                with self.assertRaises(ExpertJournalCodecError):
                    encode_expert_group_payload_area(payloads)

    def test_payload_area_rejects_truncation_trailing_and_oversized_length(
        self,
    ) -> None:
        valid = encode_expert_group_payload_area((b"abc",))
        invalid = (
            b"",
            valid[:-1],
            valid + b"x",
            struct.pack(">Q", MAX_EXPERT_EVENT_PAYLOAD_BYTES + 1),
            struct.pack(">Q", (1 << 64) - 1),
        )
        for payload_area in invalid:
            with self.subTest(payload_area_length=len(payload_area)):
                with self.assertRaises(ExpertJournalCodecError):
                    decode_expert_group_payload_area(
                        payload_area,
                        expected_count=1,
                    )

    def test_payload_area_requires_exact_bytes_and_count(self) -> None:
        with self.assertRaises(TypeError):
            encode_expert_group_payload_area([b"x"])  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            encode_expert_group_payload_area(  # type: ignore[arg-type]
                (bytearray(b"x"),)
            )
        with self.assertRaises(TypeError):
            decode_expert_group_payload_area(
                bytearray(struct.pack(">Q", 0)),  # type: ignore[arg-type]
                expected_count=1,
            )
        with self.assertRaises(TypeError):
            decode_expert_group_payload_area(
                struct.pack(">Q", 0),
                expected_count=True,  # type: ignore[arg-type]
            )


class ExpertEventPayloadDecoderTests(unittest.TestCase):
    def _payloads_by_kind(self) -> dict[ExpertEventKindV1, object]:
        universe, policy, manifest = task6_artifacts()
        state = initial_expert_state(manifest, universe, policy)
        ignored = reduce_expert_parent(
            state,
            normalize_expert_parent(manifest, raw_parent()),
        ).outcomes[0].payload
        rejected = reduce_expert_parent(
            state,
            bind_expert_observation_drafts(
                manifest,
                raw_parent(),
                manifest.normalizers.fallback,
                (
                    ExpertRejectedDraftV1(
                        ExpertRejectReasonV1.PARENT_CONTRACT_INVALID
                    ),
                ),
            ),
        ).outcomes[0].payload
        synchronization = reduce_expert_parent(
            state,
            bind_expert_observation_drafts(
                manifest,
                raw_parent(),
                manifest.normalizers.fallback,
                (
                    ExpertSynchronizationDraftV1(synchronization_input()),
                ),
            ),
        ).outcomes[0].payload
        return {
            ExpertEventKindV1.SYNCHRONIZATION_APPLIED: synchronization,
            ExpertEventKindV1.OBSERVATION_IGNORED: ignored,
            ExpertEventKindV1.OBSERVATION_REJECTED: rejected,
        }

    def _decode(self, payload: bytes, kind: ExpertEventKindV1) -> object:
        return journal_codec_module.decode_expert_event_payload(
            payload,
            event_kind=kind,
            event_version=1,
        )

    def _assert_invalid(self, payload: bytes, kind: ExpertEventKindV1) -> None:
        with self.assertRaisesRegex(
            ExpertJournalCodecError,
            "^expert_event_payload_invalid$",
        ):
            self._decode(payload, kind)

    def test_decoder_surface_requires_exact_types_and_version_one(self) -> None:
        decoder = journal_codec_module.decode_expert_event_payload
        signature = inspect.signature(decoder)
        self.assertEqual(tuple(signature.parameters), (
            "payload",
            "event_kind",
            "event_version",
        ))
        self.assertEqual(
            signature.parameters["payload"].kind,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        self.assertEqual(
            signature.parameters["event_kind"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertEqual(
            signature.parameters["event_version"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        payload = canonical_expert_bytes(
            self._payloads_by_kind()[ExpertEventKindV1.OBSERVATION_IGNORED]
        )
        for wrong_payload, wrong_kind, wrong_version in (
            (bytearray(payload), ExpertEventKindV1.OBSERVATION_IGNORED, 1),
            (payload, "observation_ignored", 1),
            (payload, ExpertEventKindV1.OBSERVATION_IGNORED, "1"),
            (payload, ExpertEventKindV1.OBSERVATION_IGNORED, True),
        ):
            with self.subTest(
                payload_type=type(wrong_payload),
                kind_type=type(wrong_kind),
                version_type=type(wrong_version),
            ):
                with self.assertRaises(TypeError):
                    decoder(  # type: ignore[arg-type]
                        wrong_payload,
                        event_kind=wrong_kind,
                        event_version=wrong_version,
                    )
        with self.assertRaisesRegex(
            ExpertJournalCodecError,
            "^expert_event_payload_invalid$",
        ):
            decoder(
                payload,
                event_kind=ExpertEventKindV1.OBSERVATION_IGNORED,
                event_version=2,
            )

    def test_decoder_round_trips_only_the_selected_payload_type(self) -> None:
        expected_types = {
            ExpertEventKindV1.SYNCHRONIZATION_APPLIED: (
                ExpertSynchronizationAppliedPayloadV1
            ),
            ExpertEventKindV1.OBSERVATION_IGNORED: (
                ExpertObservationIgnoredPayloadV1
            ),
            ExpertEventKindV1.OBSERVATION_REJECTED: (
                ExpertObservationRejectedPayloadV1
            ),
        }
        for kind, payload in self._payloads_by_kind().items():
            with self.subTest(kind=kind):
                encoded = canonical_expert_bytes(payload)
                decoded = self._decode(encoded, kind)
                self.assertIs(type(decoded), expected_types[kind])
                self.assertEqual(decoded, payload)

    def test_decoder_rejects_all_invalid_bytes_with_one_fixed_error(self) -> None:
        valid = canonical_expert_bytes(
            self._payloads_by_kind()[ExpertEventKindV1.OBSERVATION_IGNORED]
        )
        over_depth = (
            b'{"canonical_version":1,"domain":"inci-tennis-expert","value":'
            + b'{"$tuple":[' * 130
            + b"null"
            + b"]}" * 130
            + b"}"
        )
        invalid = (
            b"x" * MAX_EXPERT_EVENT_PAYLOAD_BYTES,
            b"x" * (MAX_EXPERT_EVENT_PAYLOAD_BYTES + 1),
            valid.replace(
                b'"domain":"inci-tennis-expert"',
                b'"domain":"inci-tennis-expert","domain":"inci-tennis-expert"',
                1,
            ),
            valid[:-1] + b',"unknown":true}',
            b"\xef\xbb\xbf" + valid,
            valid + b"\x80",
            valid.replace(b'"canonical_version":1,', b'"canonical_version":1.0,', 1),
            valid.replace(
                b"ExpertObservationIgnoredPayloadV1",
                b"UnregisteredPayloadV1",
                1,
            ),
            over_depth,
            valid.replace(b",\"value\":", b", \"value\":", 1),
        )
        for payload in invalid:
            with self.subTest(payload_length=len(payload)):
                self._assert_invalid(
                    payload,
                    ExpertEventKindV1.OBSERVATION_IGNORED,
                )
        self._assert_invalid(
            valid,
            ExpertEventKindV1.OBSERVATION_REJECTED,
        )


class ExpertTraceSeedTests(unittest.TestCase):
    def test_trace_seed_uses_only_the_exact_three_field_projection(self) -> None:
        projection = {
            "canonical_version": 1,
            "domain": "inci-tennis-expert",
            "value": {
                "$tuple": [
                    "11111111-1111-4111-8111-111111111111",
                    "a" * 64,
                    "b" * 64,
                ]
            },
        }
        expected = sha256(
            b"INCI-EXPERT-TRACE-SEED-V1\0"
            + json.dumps(
                projection,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
        ).hexdigest()

        actual = expert_trace_seed_sha256(
            "11111111-1111-4111-8111-111111111111",
            "a" * 64,
            "b" * 64,
        )

        self.assertEqual(actual, expected)
        self.assertNotEqual(
            actual,
            expert_trace_seed_sha256(
                "11111111-1111-4111-8111-111111111111",
                "a" * 64,
                "c" * 64,
            ),
        )


if __name__ == "__main__":
    unittest.main()
