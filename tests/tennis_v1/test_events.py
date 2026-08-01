from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import tennis_v1.adapter_contract as adapter_contract
import tennis_v1.session as session_module
from tennis_v1.canonical import canonical_json_bytes
from tennis_v1.codec import decode_record, encode_record
from tennis_v1.config import TennisV1Config, canonical_config_sha256
from tennis_v1.entitlements import (
    IntendedUse,
    QualificationDecision,
    ResearchRequest,
    RequestedStratum,
    evaluate_provider,
)
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
    SessionBindingError,
    build_session_manifest,
    canonical_session_manifest_bytes,
    require_decision_matches_session,
    session_manifest_sha256,
)
from tests.tennis_v1.test_entitlements import FixtureBuilder


def manifest(**changes: object) -> SessionManifest:
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": "12345678-1234-4234-8234-123456789abc",
        "created_wall_ns": 100,
        "config_file_sha256": "1" * 64,
        "config_canonical_sha256": "2" * 64,
        "code_sha256": "3" * 64,
        "research_request_sha256": "4" * 64,
        "provider_id": "provider",
        "product_tier": "trial",
        "source_lineage_id": "lineage",
        "provider_manifest_file_sha256": "5" * 64,
        "provider_manifest_canonical_sha256": "6" * 64,
        "entitlement_id_sha256": "7" * 64,
        "terms_version": "terms-v1",
        "permission_artifact_sha256": "8" * 64,
        "qualification_artifact_sha256": "9" * 64,
        "qualification_trace_sha256": "a" * 64,
        "adapter_code_sha256": "b" * 64,
        "auth_contract_sha256": "c" * 64,
        "quota_contract_sha256": "d" * 64,
        "session_end_ns": 200,
        "required_retention_until_ns": 300,
        "access_expires_at_ns": 400,
        "analysis_expires_at_ns": 500,
        "research_evaluable": False,
    }
    values.update(changes)
    return SessionManifest(**values)  # type: ignore[arg-type]


def event_with(payload: bytes = b"{}", **changes: object) -> PersistedEvent:
    values: dict[str, object] = {
        "journal_version": 1,
        "record_kind": RecordKind.RAW,
        "ingest_seq": 1,
        "session_id": "12345678-1234-4234-8234-123456789abc",
        "event_type": "provider.point",
        "event_version": 1,
        "source_kind": SourceKind.PROVIDER,
        "source_id": "provider",
        "source_entity_id": "match-1",
        "endpoint_id": "live",
        "endpoint_state": ProvenanceState.SAFE_ORIGINAL,
        "channel_id": None,
        "channel_state": ProvenanceState.ABSENT,
        "request_id": None,
        "request_id_state": ProvenanceState.ABSENT,
        "source_wall_ns": 10,
        "source_generated_ns": 11,
        "local_wall_ns": 100,
        "local_monotonic_ns": 50,
        "clock_uncertainty_ns": 2,
        "connection_epoch": 0,
        "provider_sequence": "0001-A",
        "parent_ingest_seq": None,
        "content_type": "application/json",
        "payload_encoding": "json",
        "payload_transform": "identity-public-market-v1",
        "retention_delete_by_ns": 300,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload": payload,
    }
    values.update(changes)
    return PersistedEvent(**values)  # type: ignore[arg-type]


class EventContractTests(unittest.TestCase):
    def test_event_post_init_rejects_subclasses_before_property_dispatch(self):
        calls: list[str] = []

        class HostilePersistedEvent(PersistedEvent):
            def __getattribute__(self, name):
                calls.append(name)
                if name == "journal_version":
                    return 1
                return super().__getattribute__(name)

        hostile = object.__new__(HostilePersistedEvent)
        with self.assertRaisesRegex(
            TypeError,
            r"\Aexact PersistedEvent required\Z",
        ):
            PersistedEvent.__post_init__(hostile)
        self.assertEqual(calls, [])

    def test_captured_input_requires_nonnegative_integer_times_and_epoch(self):
        for field in (
            "local_wall_ns",
            "local_monotonic_ns",
            "clock_uncertainty_ns",
            "connection_epoch",
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    event_with(**{field: -1})
                with self.assertRaises(TypeError):
                    event_with(**{field: True})

    def test_source_and_event_identifiers_are_nonempty_safe_strings(self):
        for field in ("session_id", "event_type", "source_id", "source_entity_id"):
            for value in ("", "bad value", "https://secret.invalid/?q=x", "a/b", "x@y"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        event_with(**{field: value})

    def test_provider_sequence_is_an_opaque_string_not_an_integer_clock(self):
        self.assertEqual(event_with(provider_sequence="0001-A").provider_sequence, "0001-A")
        with self.assertRaises(TypeError):
            event_with(provider_sequence=1)

    def test_payload_is_bytes_and_sha256_matches_exact_durable_bytes(self):
        with self.assertRaises(TypeError):
            event_with(payload=bytearray(b"{}"))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            event_with(payload_sha256="0" * 64)

    def test_derived_parent_must_precede_derived_ingest_sequence(self):
        derived = event_with(
            record_kind=RecordKind.DERIVED,
            ingest_seq=3,
            parent_ingest_seq=2,
            content_type="application/vnd.inci.derived+json",
            payload_transform="derived-canonical-v1",
        )
        self.assertEqual(derived.parent_ingest_seq, 2)
        for parent in (None, 0, 3, 4):
            with self.subTest(parent=parent):
                with self.assertRaises(ValueError):
                    event_with(
                        record_kind=RecordKind.DERIVED,
                        ingest_seq=3,
                        parent_ingest_seq=parent,
                        content_type="application/vnd.inci.derived+json",
                        payload_transform="derived-canonical-v1",
                    )

    def test_provider_raw_requires_future_retention_deadline(self):
        for deadline in (None, 99, 100):
            with self.subTest(deadline=deadline):
                with self.assertRaises(ValueError):
                    event_with(retention_delete_by_ns=deadline)

    def test_nonprovider_raw_rejects_provider_retention_deadline(self):
        with self.assertRaises(ValueError):
            event_with(
                source_kind=SourceKind.KALSHI,
                source_id="kalshi",
                retention_delete_by_ns=300,
            )

    def test_event_values_are_frozen_and_payload_hidden_from_repr(self):
        event = event_with(b"DO_NOT_RENDER")
        self.assertNotIn("DO_NOT_RENDER", repr(event))
        with self.assertRaises(FrozenInstanceError):
            event.event_type = "changed"  # type: ignore[misc]
        self.assertEqual(DerivedDraft("derived.point", 1, "json", b"{}").payload, b"{}")

    def test_session_manifest_is_phase_one_nonresearch_evaluable(self):
        self.assertIs(manifest().research_evaluable, False)
        for invalid in (0, None, True):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    manifest(research_evaluable=invalid)

    def test_decoder_requires_research_evaluable_is_literal_false(self):
        payload = b"{}"
        metadata, durable = encode_record(event_with(payload))
        self.assertEqual(decode_record(metadata, durable), event_with(payload))

    def test_round_trip_preserves_utf8_newline_nul_and_arbitrary_bytes(self):
        payload = b'{"line":"one\\ntwo","nul":"\\u0000"}\x00\xff'
        expected = event_with(payload)
        metadata, durable_payload = encode_record(expected)
        self.assertEqual(durable_payload, payload)
        self.assertEqual(decode_record(metadata, durable_payload), expected)


class SessionContractTests(unittest.TestCase):
    def test_manifest_post_init_rejects_subclasses_before_property_dispatch(self):
        calls: list[str] = []

        class HostileSessionManifest(SessionManifest):
            def __getattribute__(self, name):
                calls.append(name)
                if name == "schema_version":
                    return 1
                return super().__getattribute__(name)

        hostile = object.__new__(HostileSessionManifest)
        with self.assertRaisesRegex(
            TypeError,
            r"\Aexact SessionManifest required\Z",
        ):
            SessionManifest.__post_init__(hostile)
        self.assertEqual(calls, [])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.builder = FixtureBuilder(self.root, self.repo)
        self.provider_manifest = self.builder.load(self.builder.build())
        provisional = TennisV1Config(
            schema_version=1,
            state_root=self.root / "state",
            provider_manifest_path=self.root / "provider.json",
            provider_manifest_sha256=self.provider_manifest.source_file_sha256,
            trusted_permission_reviewer_ids=("reviewer-test",),
            trusted_qualification_issuer_ids=("issuer-test",),
            observed_pool_limit=10,
            paper_position_limit=3,
            source_file_sha256="e" * 64,
            canonical_sha256="",
        )
        self.config = replace(
            provisional, canonical_sha256=canonical_config_sha256(provisional)
        )
        self.now = datetime(2026, 7, 27, 13, 0, tzinfo=timezone.utc)
        stratum = self.provider_manifest.capabilities.declared_strata[0]
        self.request = ResearchRequest(
            intended_use=IntendedUse.PRIVATE_PAPER_EVALUATION,
            now_utc=self.now,
            session_end_utc=self.now + timedelta(hours=1),
            required_retention_until=self.now + timedelta(hours=2),
            expiry_safety_margin_seconds=60,
            required_raw_retention_seconds=3600,
            requested_matches=2,
            required_strata=(RequestedStratum(stratum, 2),),
        )
        with mock.patch.multiple(
            adapter_contract,
            __file__=self.builder.adapter_file,
            _ADAPTER_REGISTRY={
                ("synthetic-provider", "trial-v1"): self.builder.registration
            },
        ):
            self.decision = evaluate_provider(
                self.config,
                self.provider_manifest,
                self.request,
                environ={"SYNTHETIC_API_KEY": "fixture-secret"},
            )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def ns(value: datetime) -> int:
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = value - epoch
        return (
            delta.days * 86_400_000_000_000
            + delta.seconds * 1_000_000_000
            + delta.microseconds * 1_000
        )

    def build(self, **changes):
        values = {
            "config": self.config,
            "provider_manifest": self.provider_manifest,
            "qualification": self.decision,
            "session_id": "12345678-1234-4234-8234-123456789abc",
            "created_wall_ns": self.ns(self.now),
            "code_sha256": "f" * 64,
        }
        values.update(changes)
        return build_session_manifest(**values)

    def test_session_manifest_requires_verified_eligible_matching_inputs(self):
        built = self.build()
        require_decision_matches_session(self.decision, built)
        with self.assertRaises(ValueError):
            self.build(
                config=replace(
                    self.config, provider_manifest_sha256="0" * 64
                )
            )
        with self.assertRaises(ValueError):
            self.build(
                qualification=replace(
                    self.decision, eligible=False, binding=None
                )
            )
        with self.assertRaises(ValueError):
            require_decision_matches_session(
                replace(
                    self.decision,
                    provider_request_binding_sha256="0" * 64,
                ),
                built,
            )

    def test_session_authorization_requires_eligible_is_literal_true(self):
        built = self.build()
        for invalid in (1, "true", None, False):
            with self.subTest(invalid=invalid):
                forged = replace(self.decision, eligible=invalid)
                with self.assertRaisesRegex(
                    SessionBindingError,
                    "qualification_decision_eligibility_invalid",
                ):
                    require_decision_matches_session(forged, built)

    def test_session_manifest_copies_every_binding_field_represented_in_manifest(self):
        built = self.build()
        binding = self.decision.binding
        self.assertIsNotNone(binding)
        self.assertEqual(
            {
                "provider_id": built.provider_id,
                "product_tier": built.product_tier,
                "source_lineage_id": built.source_lineage_id,
                "entitlement_id_sha256": built.entitlement_id_sha256,
                "manifest_file_sha256": built.provider_manifest_file_sha256,
                "manifest_canonical_sha256": built.provider_manifest_canonical_sha256,
                "qualification_artifact_sha256": built.qualification_artifact_sha256,
                "permission_artifact_sha256": built.permission_artifact_sha256,
                "qualification_trace_sha256": built.qualification_trace_sha256,
                "adapter_code_sha256": built.adapter_code_sha256,
                "auth_contract_sha256": built.auth_contract_sha256,
                "quota_contract_sha256": built.quota_contract_sha256,
                "session_end_ns": built.session_end_ns,
                "required_retention_until_ns": built.required_retention_until_ns,
                "access_expires_at_ns": built.access_expires_at_ns,
                "analysis_expires_at_ns": built.analysis_expires_at_ns,
            },
            {
                "provider_id": binding.provider_id,
                "product_tier": binding.product_tier,
                "source_lineage_id": binding.source_lineage_id,
                "entitlement_id_sha256": binding.entitlement_id_sha256,
                "manifest_file_sha256": binding.manifest_file_sha256,
                "manifest_canonical_sha256": binding.manifest_canonical_sha256,
                "qualification_artifact_sha256": binding.qualification_artifact_sha256,
                "permission_artifact_sha256": binding.permission_artifact_sha256,
                "qualification_trace_sha256": binding.qualification_trace_sha256,
                "adapter_code_sha256": binding.adapter_code_sha256,
                "auth_contract_sha256": binding.auth_contract_sha256,
                "quota_contract_sha256": binding.quota_contract_sha256,
                "session_end_ns": self.ns(binding.session_end_utc),
                "required_retention_until_ns": self.ns(
                    binding.required_retention_until
                ),
                "access_expires_at_ns": self.ns(binding.access_expires_at),
                "analysis_expires_at_ns": self.ns(binding.analysis_expires_at),
            },
        )

    def test_session_manifest_fixes_one_provider_delete_by_for_whole_session(self):
        built = self.build()
        self.assertEqual(
            built.required_retention_until_ns,
            self.ns(self.request.required_retention_until),
        )
        self.assertEqual(
            session_manifest_sha256(built),
            hashlib.sha256(canonical_session_manifest_bytes(built)).hexdigest(),
        )
        self.assertEqual(
            set(json.loads(canonical_session_manifest_bytes(built))),
            {field.name for field in fields(SessionManifest)},
        )

    def test_session_projection_rejects_subclass_before_any_field_getter(self):
        class HostileManifest(SessionManifest):
            touches = 0

            def __getattribute__(self, name):
                if name in SessionManifest.__dataclass_fields__:
                    type(self).touches += 1
                    raise AssertionError("hostile manifest getter executed")
                return super().__getattribute__(name)

        hostile = object.__new__(HostileManifest)
        with self.assertRaisesRegex(
            TypeError,
            r"\Asession manifest must be SessionManifest\Z",
        ):
            session_module._projection(hostile)
        self.assertEqual(HostileManifest.touches, 0)

    def test_decoder_requires_research_evaluable_is_literal_false_in_session_start(self):
        built = self.build()
        good_payload = canonical_session_manifest_bytes(built)
        control = event_with(
            good_payload,
            record_kind=RecordKind.CONTROL,
            source_kind=SourceKind.SYSTEM,
            source_id="tennis-v1",
            source_entity_id=built.session_id,
            session_id=built.session_id,
            event_type="SESSION_START",
            source_wall_ns=None,
            source_generated_ns=None,
            local_wall_ns=built.created_wall_ns,
            local_monotonic_ns=0,
            clock_uncertainty_ns=0,
            connection_epoch=0,
            provider_sequence=None,
            endpoint_id=None,
            endpoint_state=ProvenanceState.ABSENT,
            channel_id="session-control",
            channel_state=ProvenanceState.SAFE_ORIGINAL,
            request_id=None,
            request_id_state=ProvenanceState.ABSENT,
            retention_delete_by_ns=None,
            content_type="application/vnd.inci.session-manifest+json",
            payload_encoding="canonical-json-v1",
            payload_transform="identity-public-market-v1",
        )
        metadata, payload = encode_record(control)
        self.assertEqual(decode_record(metadata, payload), control)
        invalid = json.loads(good_payload)
        invalid["research_evaluable"] = 0
        invalid_payload = canonical_json_bytes(invalid)
        invalid_control = replace(
            control,
            payload=invalid_payload,
            payload_sha256=hashlib.sha256(invalid_payload).hexdigest(),
        )
        invalid_metadata, invalid_durable = encode_record(invalid_control)
        with self.assertRaises(ValueError):
            decode_record(invalid_metadata, invalid_durable)
