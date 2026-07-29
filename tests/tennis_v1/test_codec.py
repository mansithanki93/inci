from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import unittest

from tennis_v1.codec import (
    CANONICAL_RECORD_DOMAIN,
    canonical_metadata,
    canonical_record_sha256,
    decode_record,
    encode_record,
)
from tennis_v1.events import PersistedEvent, ProvenanceState, RecordKind, SourceKind
from tests.tennis_v1.test_events import event_with


class CodecTests(unittest.TestCase):
    def test_canonical_metadata_has_exact_sorted_schema_and_payload_digest(self):
        event = event_with(b"\x00\xff")
        metadata = canonical_metadata(event)
        parsed = json.loads(metadata)
        self.assertEqual(metadata, json.dumps(
            parsed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8"))
        self.assertNotIn("payload", parsed)
        self.assertEqual(parsed["payload_sha256"], hashlib.sha256(b"\x00\xff").hexdigest())
        expected_preimage = (
            CANONICAL_RECORD_DOMAIN
            + len(metadata).to_bytes(8, "big")
            + metadata
            + (2).to_bytes(8, "big")
            + b"\x00\xff"
        )
        self.assertEqual(
            canonical_record_sha256(event),
            hashlib.sha256(expected_preimage).hexdigest(),
        )

    def test_canonical_records_reject_floats_unknown_keys_and_unknown_enums(self):
        metadata, payload = encode_record(event_with())
        parsed = json.loads(metadata)
        bad_values = []
        with_unknown = dict(parsed)
        with_unknown["unknown"] = 1
        bad_values.append(with_unknown)
        with_float = dict(parsed)
        with_float["local_wall_ns"] = 1.5
        bad_values.append(with_float)
        with_enum = dict(parsed)
        with_enum["source_kind"] = "unknown"
        bad_values.append(with_enum)
        for value in bad_values:
            with self.subTest(value=value):
                encoded = json.dumps(value, separators=(",", ":")).encode()
                with self.assertRaises(ValueError):
                    decode_record(encoded, payload)

    def test_decoder_rejects_duplicate_keys_bom_noncanonical_and_payload_mismatch(self):
        metadata, payload = encode_record(event_with())
        with self.assertRaises(ValueError):
            decode_record(b"\xef\xbb\xbf" + metadata, payload)
        duplicate = metadata[:-1] + b',"journal_version":1}'
        with self.assertRaises(ValueError):
            decode_record(duplicate, payload)
        with self.assertRaises(ValueError):
            decode_record(metadata + b" ", payload)
        with self.assertRaises(ValueError):
            decode_record(metadata, payload + b"x")

    def test_control_record_contract_cannot_masquerade(self):
        control = event_with(
            record_kind=RecordKind.CONTROL,
            source_kind=SourceKind.SYSTEM,
            source_id="tennis-v1",
            source_entity_id="12345678-1234-4234-8234-123456789abc",
            event_type="SESSION_START",
            source_wall_ns=None,
            source_generated_ns=None,
            local_wall_ns=0,
            local_monotonic_ns=0,
            clock_uncertainty_ns=0,
            connection_epoch=0,
            provider_sequence=None,
            endpoint_id=None,
            endpoint_state=ProvenanceState.ABSENT,
            channel_id="session-control",
            channel_state=ProvenanceState.SAFE_ORIGINAL,
            retention_delete_by_ns=None,
            content_type="application/vnd.inci.session-manifest+json",
            payload_encoding="canonical-json-v1",
            payload_transform="identity-public-market-v1",
        )
        metadata, payload = encode_record(control)
        parsed = json.loads(metadata)
        parsed["content_type"] = "application/json"
        with self.assertRaises(ValueError):
            decode_record(json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode(), payload)
