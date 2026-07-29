from __future__ import annotations

import ast
import copy
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
from pathlib import Path
import unittest

from tennis_v1.codec import CANONICAL_RECORD_DOMAIN, encode_record
from tennis_v1.events import (
    DerivedDraft,
    PersistedEvent,
    ProvenanceState,
    RecordKind,
    SourceKind,
)
from tennis_v1.reducer import (
    Reduction,
    initial_trace,
    next_trace,
    reduce_event,
)
from tennis_v1.session import canonical_session_manifest_bytes
from tennis_v1.state import (
    FoundationState,
    canonical_state_bytes,
    initial_state,
)
from tests.tennis_v1.test_events import event_with, manifest


SESSION_ID = "12345678-1234-4234-8234-123456789abc"
OTHER_SESSION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def raw_event(payload: bytes = b'{"point":"15-0"}', **changes: object) -> PersistedEvent:
    values: dict[str, object] = {
        "ingest_seq": 2,
        "event_type": "provider_frame",
        "source_id": "provider-a",
        "source_entity_id": "match-1",
        "connection_epoch": 7,
        "provider_sequence": "P-1",
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    values.update(changes)
    return event_with(**values)


def derived_event(
    raw: PersistedEvent,
    *,
    ingest_seq: int | None = None,
    payload: bytes = b"{}",
    **changes: object,
) -> PersistedEvent:
    values: dict[str, object] = {
        "journal_version": 1,
        "record_kind": RecordKind.DERIVED,
        "ingest_seq": raw.ingest_seq + 1 if ingest_seq is None else ingest_seq,
        "session_id": raw.session_id,
        "event_type": "raw_accepted",
        "event_version": 1,
        "source_kind": raw.source_kind,
        "source_id": raw.source_id,
        "source_entity_id": raw.source_entity_id,
        "endpoint_id": raw.endpoint_id,
        "endpoint_state": raw.endpoint_state,
        "channel_id": raw.channel_id,
        "channel_state": raw.channel_state,
        "request_id": raw.request_id,
        "request_id_state": raw.request_id_state,
        "source_wall_ns": raw.source_wall_ns,
        "source_generated_ns": raw.source_generated_ns,
        "local_wall_ns": raw.local_wall_ns,
        "local_monotonic_ns": raw.local_monotonic_ns,
        "clock_uncertainty_ns": raw.clock_uncertainty_ns,
        "connection_epoch": raw.connection_epoch,
        "provider_sequence": raw.provider_sequence,
        "parent_ingest_seq": raw.ingest_seq,
        "content_type": "application/vnd.inci.derived+json",
        "payload_encoding": "canonical-json-v1",
        "payload_transform": "derived-canonical-v1",
        "retention_delete_by_ns": raw.retention_delete_by_ns,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload": payload,
    }
    values.update(changes)
    if "payload" in changes and "payload_sha256" not in changes:
        changed_payload = changes["payload"]
        if type(changed_payload) is bytes:
            values["payload_sha256"] = hashlib.sha256(changed_payload).hexdigest()
    return PersistedEvent(**values)  # type: ignore[arg-type]


def session_start(**changes: object) -> PersistedEvent:
    session_manifest = manifest()
    payload = canonical_session_manifest_bytes(session_manifest)
    values: dict[str, object] = {
        "record_kind": RecordKind.CONTROL,
        "ingest_seq": 1,
        "session_id": session_manifest.session_id,
        "event_type": "SESSION_START",
        "source_kind": SourceKind.SYSTEM,
        "source_id": "tennis-v1",
        "source_entity_id": session_manifest.session_id,
        "endpoint_id": None,
        "endpoint_state": ProvenanceState.ABSENT,
        "channel_id": "session-control",
        "channel_state": ProvenanceState.SAFE_ORIGINAL,
        "request_id": None,
        "request_id_state": ProvenanceState.ABSENT,
        "source_wall_ns": None,
        "source_generated_ns": None,
        "local_wall_ns": session_manifest.created_wall_ns,
        "local_monotonic_ns": 0,
        "clock_uncertainty_ns": 0,
        "connection_epoch": 0,
        "provider_sequence": None,
        "parent_ingest_seq": None,
        "content_type": "application/vnd.inci.session-manifest+json",
        "payload_encoding": "canonical-json-v1",
        "payload_transform": "identity-public-market-v1",
        "retention_delete_by_ns": None,
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    values.update(changes)
    if "payload" in changes and "payload_sha256" not in changes:
        changed_payload = changes["payload"]
        if type(changed_payload) is bytes:
            values["payload_sha256"] = hashlib.sha256(changed_payload).hexdigest()
    return event_with(**values)


def reduction_for(raw: PersistedEvent) -> Reduction:
    return reduce_event(initial_state(raw.session_id), raw)


def trace_for(raw: PersistedEvent) -> bytes:
    reduction = reduction_for(raw)
    stored = derived_event(
        raw,
        payload=reduction.outputs[0].payload,
    )
    return next_trace(
        b"\x11" * 32,
        raw,
        (stored,),
        reduction.state,
    )


class FoundationStateTests(unittest.TestCase):
    def test_initial_state_is_exact_frozen_empty_state(self):
        state = initial_state(SESSION_ID)
        self.assertEqual(
            state,
            FoundationState(
                session_id=SESSION_ID,
                last_applied_raw_seq=0,
                raw_count=0,
                derived_count=0,
                source_epochs=(),
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            state.raw_count = 1  # type: ignore[misc]
        with self.assertRaises((TypeError, ValueError)):
            initial_state("not-a-session")

    def test_canonical_state_has_exact_projection_and_golden_bytes(self):
        state = FoundationState(
            session_id=SESSION_ID,
            last_applied_raw_seq=2,
            raw_count=1,
            derived_count=1,
            source_epochs=((SourceKind.PROVIDER, "provider-a", 7),),
        )
        expected = (
            b'{"derived_count":1,"last_applied_raw_seq":2,"raw_count":1,'
            b'"session_id":"12345678-1234-4234-8234-123456789abc",'
            b'"source_epochs":[{"connection_epoch":7,'
            b'"source_id":"provider-a","source_kind":"provider"}],'
            b'"state_version":1}'
        )
        self.assertEqual(canonical_state_bytes(state), expected)

    def test_state_rejects_nonexact_malformed_duplicate_and_unsorted_epochs(self):
        class IntegerSubclass(int):
            pass

        common = {
            "session_id": SESSION_ID,
            "last_applied_raw_seq": 2,
            "raw_count": 1,
            "derived_count": 1,
            "source_epochs": ((SourceKind.PROVIDER, "provider-a", 7),),
        }
        invalid = (
            {"last_applied_raw_seq": True},
            {"raw_count": IntegerSubclass(1)},
            {"derived_count": -1},
            {"source_epochs": [(SourceKind.PROVIDER, "provider-a", 7)]},
            {"source_epochs": ((SourceKind.PROVIDER, "provider-a", 7, 8),)},
            {"source_epochs": (("provider", "provider-a", 7),)},
            {"source_epochs": ((SourceKind.PROVIDER, "bad source", 7),)},
            {"source_epochs": ((SourceKind.PROVIDER, "provider-a", True),)},
            {
                "raw_count": 2,
                "derived_count": 2,
                "source_epochs": (
                    (SourceKind.PROVIDER, "provider-a", 7),
                    (SourceKind.PROVIDER, "provider-a", 8),
                ),
            },
            {
                "raw_count": 2,
                "derived_count": 2,
                "source_epochs": (
                    (SourceKind.PROVIDER, "provider-z", 7),
                    (SourceKind.KALSHI, "kalshi", 1),
                ),
            },
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises((TypeError, ValueError)):
                    FoundationState(**{**common, **changes})  # type: ignore[arg-type]

    def test_state_rejects_count_and_sequence_invariant_violations(self):
        common = {
            "session_id": SESSION_ID,
            "last_applied_raw_seq": 2,
            "raw_count": 1,
            "derived_count": 1,
            "source_epochs": ((SourceKind.PROVIDER, "provider-a", 7),),
        }
        invalid = (
            {"raw_count": 0, "derived_count": 0},
            {"last_applied_raw_seq": 0},
            {"raw_count": 2, "derived_count": 1},
            {"last_applied_raw_seq": 1, "raw_count": 2, "derived_count": 2},
            {"raw_count": 0, "derived_count": 0, "source_epochs": common["source_epochs"]},
            {"source_epochs": ()},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    FoundationState(**{**common, **changes})  # type: ignore[arg-type]

    def test_canonical_state_revalidates_a_post_construction_forgery(self):
        state = initial_state(SESSION_ID)
        object.__setattr__(state, "raw_count", True)
        with self.assertRaises((TypeError, ValueError)):
            canonical_state_bytes(state)


class ReducerTests(unittest.TestCase):
    def test_reducer_accepts_only_raw_records_for_its_session(self):
        state = initial_state(SESSION_ID)
        raw = raw_event()
        self.assertEqual(reduce_event(state, raw).state.last_applied_raw_seq, 2)
        with self.assertRaises(ValueError):
            reduce_event(
                state,
                derived_event(raw),
            )
        with self.assertRaises(ValueError):
            reduce_event(state, session_start())
        with self.assertRaises(ValueError):
            reduce_event(
                state,
                raw_event(session_id=OTHER_SESSION_ID),
            )
        with self.assertRaises(TypeError):
            reduce_event(state, object())  # type: ignore[arg-type]

    def test_reducer_rejects_duplicate_or_regressed_raw_but_accepts_gaps(self):
        first = reduce_event(initial_state(SESSION_ID), raw_event()).state
        for ingest_seq in (1, 2):
            with self.subTest(ingest_seq=ingest_seq):
                with self.assertRaises(ValueError):
                    reduce_event(first, raw_event(ingest_seq=ingest_seq))
        gapped = reduce_event(
            first,
            raw_event(ingest_seq=10, provider_sequence="P-10"),
        )
        self.assertEqual(gapped.state.last_applied_raw_seq, 10)
        self.assertEqual(gapped.state.raw_count, 2)

    def test_source_epoch_never_regresses_within_exact_kind_and_source(self):
        first = reduce_event(initial_state(SESSION_ID), raw_event()).state
        equal = reduce_event(
            first,
            raw_event(ingest_seq=4, connection_epoch=7),
        ).state
        increased = reduce_event(
            equal,
            raw_event(ingest_seq=6, connection_epoch=8),
        ).state
        self.assertEqual(
            increased.source_epochs,
            ((SourceKind.PROVIDER, "provider-a", 8),),
        )
        with self.assertRaises(ValueError):
            reduce_event(
                increased,
                raw_event(ingest_seq=8, connection_epoch=7),
            )
        distinct = reduce_event(
            increased,
            raw_event(
                ingest_seq=8,
                source_id="provider-b",
                connection_epoch=0,
            ),
        ).state
        self.assertEqual(len(distinct.source_epochs), 2)

    def test_same_state_and_event_produce_byte_identical_reduction(self):
        state = initial_state(SESSION_ID)
        raw = raw_event()
        first = reduce_event(state, raw)
        second = reduce_event(state, raw)
        self.assertEqual(first, second)
        self.assertEqual(
            first.outputs,
            (
                DerivedDraft(
                    event_type="raw_accepted",
                    event_version=1,
                    payload_encoding="canonical-json-v1",
                    payload=(
                        b'{"input_event_type":"provider_frame",'
                        b'"input_payload_sha256":"'
                        + raw.payload_sha256.encode("ascii")
                        + b'","parent_ingest_seq":2,'
                        b'"source_id":"provider-a"}'
                    ),
                ),
            ),
        )
        self.assertEqual(first.state.raw_count, 1)
        self.assertEqual(first.state.derived_count, 1)

    def test_reduction_has_one_state_authority_and_no_independent_state_bytes(self):
        reduction = reduction_for(raw_event())
        self.assertEqual(
            tuple(item.name for item in fields(Reduction)),
            ("state", "outputs"),
        )
        with self.assertRaises(FrozenInstanceError):
            reduction.outputs = ()  # type: ignore[misc]
        with self.assertRaises(TypeError):
            Reduction(reduction.state, [reduction.outputs[0]])  # type: ignore[arg-type]

    def test_sorted_frozen_state_is_independent_of_source_arrival_order(self):
        provider = raw_event(
            source_kind=SourceKind.PROVIDER,
            source_id="provider-z",
            connection_epoch=3,
        )
        kalshi = raw_event(
            source_kind=SourceKind.KALSHI,
            source_id="kalshi",
            connection_epoch=1,
            retention_delete_by_ns=None,
        )
        left = reduce_event(initial_state(SESSION_ID), provider).state
        left = reduce_event(
            left,
            replace(kalshi, ingest_seq=4),
        ).state
        right = reduce_event(initial_state(SESSION_ID), kalshi).state
        right = reduce_event(
            right,
            replace(provider, ingest_seq=4),
        ).state
        self.assertEqual(left, right)
        self.assertEqual(
            left.source_epochs,
            (
                (SourceKind.KALSHI, "kalshi", 1),
                (SourceKind.PROVIDER, "provider-z", 3),
            ),
        )

    def test_reducer_has_no_clock_random_filesystem_network_or_legacy_dependency(self):
        package_root = Path(__file__).resolve().parents[2] / "tennis_v1"
        forbidden_imports = {
            "aiohttp",
            "bot",
            "engine",
            "executor",
            "http",
            "httpx",
            "kalshi_client",
            "market_data",
            "os",
            "pathlib",
            "random",
            "replay",
            "requests",
            "research_log",
            "safety",
            "socket",
            "time",
            "urllib",
            "websockets",
        }
        for filename in ("state.py", "reducer.py"):
            tree = ast.parse((package_root / filename).read_text(encoding="utf-8"))
            imported = {
                item.name.split(".", 1)[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for item in node.names
            } | {
                (node.module or "").split(".", 1)[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.level == 0
            }
            self.assertTrue(
                imported.isdisjoint(forbidden_imports),
                (filename, imported & forbidden_imports),
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(
                        node.func.id,
                        {"__import__", "eval", "exec", "open"},
                    )


class TraceTests(unittest.TestCase):
    def test_trace_seed_binds_complete_session_start_record_metadata_and_payload(self):
        start = session_start()
        metadata, payload = encode_record(start)
        record_digest = hashlib.sha256(
            CANONICAL_RECORD_DOMAIN
            + len(metadata).to_bytes(8, "big")
            + metadata
            + len(payload).to_bytes(8, "big")
            + payload
        ).digest()
        expected = hashlib.sha256(
            b"INCI-TRACE-V1\0" + record_digest
        ).digest()
        self.assertEqual(initial_trace(start), expected)
        wall_manifest = replace(manifest(), created_wall_ns=101)
        wall_payload = canonical_session_manifest_bytes(wall_manifest)
        metadata_changed = replace(
            start,
            local_wall_ns=wall_manifest.created_wall_ns,
            payload=wall_payload,
            payload_sha256=hashlib.sha256(wall_payload).hexdigest(),
        )
        changed_manifest = replace(manifest(), terms_version="terms-v2")
        changed_payload = canonical_session_manifest_bytes(changed_manifest)
        payload_changed = replace(
            start,
            payload=changed_payload,
            payload_sha256=hashlib.sha256(changed_payload).hexdigest(),
        )
        self.assertNotEqual(initial_trace(metadata_changed), expected)
        self.assertNotEqual(initial_trace(payload_changed), expected)

    def test_initial_trace_rejects_manifest_record_binding_mismatches(self):
        start = session_start()
        different_session_manifest = replace(
            manifest(),
            session_id=OTHER_SESSION_ID,
        )
        different_session_payload = canonical_session_manifest_bytes(
            different_session_manifest
        )
        mismatched_session = replace(
            start,
            payload=different_session_payload,
            payload_sha256=hashlib.sha256(
                different_session_payload
            ).hexdigest(),
        )
        for invalid in (
            mismatched_session,
            replace(start, local_wall_ns=start.local_wall_ns + 1),
            replace(start, local_monotonic_ns=1),
        ):
            with self.subTest(
                session_id=invalid.session_id,
                local_wall_ns=invalid.local_wall_ns,
                local_monotonic_ns=invalid.local_monotonic_ns,
            ):
                with self.assertRaises(ValueError):
                    initial_trace(invalid)

    def test_initial_trace_rejects_noncanonical_extra_and_malformed_manifest_payload(self):
        start = session_start()
        invalid_payloads = (
            start.payload + b" ",
            start.payload[:-1] + b',"unexpected":1}',
            b"{",
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload[-24:]):
                candidate = replace(
                    start,
                    payload=payload,
                    payload_sha256=hashlib.sha256(payload).hexdigest(),
                )
                with self.assertRaises(ValueError):
                    initial_trace(candidate)

    def test_initial_trace_rechecks_every_exact_control_metadata_sentinel(self):
        start = session_start()
        mutations = (
            ("journal_version", 2),
            ("record_kind", RecordKind.RAW),
            ("event_version", 2),
            ("source_kind", SourceKind.KALSHI),
            ("source_id", "other"),
            ("source_entity_id", OTHER_SESSION_ID),
            ("endpoint_id", "endpoint"),
            ("channel_id", "other"),
            ("request_id", "request"),
            ("source_wall_ns", 1),
            ("source_generated_ns", 1),
            ("clock_uncertainty_ns", 1),
            ("connection_epoch", 1),
            ("provider_sequence", "P-1"),
            ("parent_ingest_seq", 1),
            ("content_type", "application/json"),
            ("payload_encoding", "json"),
            ("payload_transform", "json-secret-redaction-v1"),
            ("retention_delete_by_ns", 300),
        )
        for field_name, value in mutations:
            with self.subTest(field=field_name):
                forged = copy.copy(start)
                object.__setattr__(forged, field_name, value)
                with self.assertRaises(ValueError):
                    initial_trace(forged)

    def test_initial_trace_accepts_only_valid_sequence_one_session_start(self):
        start = session_start()
        invalid_payload = replace(
            start,
            payload=b"{}",
            payload_sha256=hashlib.sha256(b"{}").hexdigest(),
        )
        halt = replace(
            start,
            event_type="SESSION_HALT",
            content_type="application/vnd.inci.session-terminal+json",
        )
        for invalid in (
            replace(start, ingest_seq=2),
            raw_event(),
            halt,
            invalid_payload,
        ):
            with self.subTest(event_type=invalid.event_type):
                with self.assertRaises(ValueError):
                    initial_trace(invalid)
        with self.assertRaises(TypeError):
            initial_trace(object())  # type: ignore[arg-type]

    def test_next_trace_matches_independently_computed_step(self):
        raw = raw_event()
        reduction = reduction_for(raw)
        stored = derived_event(raw, payload=reduction.outputs[0].payload)
        prior = b"\x22" * 32
        raw_metadata, raw_payload = encode_record(raw)
        raw_digest = hashlib.sha256(
            CANONICAL_RECORD_DOMAIN
            + len(raw_metadata).to_bytes(8, "big")
            + raw_metadata
            + len(raw_payload).to_bytes(8, "big")
            + raw_payload
        ).hexdigest()
        output_metadata, output_payload = encode_record(stored)
        output_digest = hashlib.sha256(
            CANONICAL_RECORD_DOMAIN
            + len(output_metadata).to_bytes(8, "big")
            + output_metadata
            + len(output_payload).to_bytes(8, "big")
            + output_payload
        ).hexdigest()
        state_digest = hashlib.sha256(
            canonical_state_bytes(reduction.state)
        ).hexdigest()
        entry = (
            b'{"outputs":[{"record_sha256":"'
            + output_digest.encode("ascii")
            + b'"}],"raw_record_sha256":"'
            + raw_digest.encode("ascii")
            + b'","state_sha256":"'
            + state_digest.encode("ascii")
            + b'","v":1}'
        )
        expected = hashlib.sha256(
            b"INCI-TRACE-STEP-V1\0"
            + prior
            + len(entry).to_bytes(8, "big")
            + entry
        ).digest()
        self.assertEqual(
            next_trace(prior, raw, (stored,), reduction.state),
            expected,
        )

    def test_trace_changes_for_payload_output_order_and_valid_state_change(self):
        raw = raw_event()
        reduction = reduction_for(raw)
        first = derived_event(
            raw,
            ingest_seq=3,
            payload=reduction.outputs[0].payload,
        )
        second = derived_event(
            raw,
            ingest_seq=4,
            event_type="raw_observed",
            payload=b'{"second":true}',
        )
        prior = b"\x33" * 32
        ordered = next_trace(
            prior,
            raw,
            (first, second),
            reduction.state,
        )
        self.assertNotEqual(
            ordered,
            next_trace(prior, raw, (second, first), reduction.state),
        )
        changed_payload = derived_event(
            raw,
            payload=b'{"different":true}',
        )
        self.assertNotEqual(
            next_trace(prior, raw, (first,), reduction.state),
            next_trace(prior, raw, (changed_payload,), reduction.state),
        )
        changed_state = replace(
            reduction.state,
            source_epochs=((SourceKind.PROVIDER, "provider-a", 8),),
        )
        self.assertNotEqual(
            next_trace(prior, raw, (first,), reduction.state),
            next_trace(prior, raw, (first,), changed_state),
        )

    def test_trace_changes_when_each_valid_raw_metadata_dimension_changes(self):
        base = raw_event()
        baseline = trace_for(base)
        alternatives = (
            replace(base, ingest_seq=4),
            replace(base, session_id=OTHER_SESSION_ID),
            replace(base, event_type="provider_update"),
            replace(base, event_version=2),
            replace(
                base,
                source_kind=SourceKind.KALSHI,
                source_id="kalshi",
                retention_delete_by_ns=None,
            ),
            replace(base, source_id="provider-b"),
            replace(base, source_entity_id="match-2"),
            replace(base, endpoint_id="backup"),
            replace(
                base,
                endpoint_id=None,
                endpoint_state=ProvenanceState.ABSENT,
            ),
            replace(
                base,
                channel_id="scores",
                channel_state=ProvenanceState.SAFE_ORIGINAL,
            ),
            replace(
                base,
                request_id="request-2",
                request_id_state=ProvenanceState.SAFE_ORIGINAL,
            ),
            replace(base, source_wall_ns=12),
            replace(base, source_generated_ns=13),
            replace(base, local_wall_ns=101),
            replace(base, local_monotonic_ns=51),
            replace(base, clock_uncertainty_ns=3),
            replace(base, connection_epoch=8),
            replace(base, provider_sequence="P-2"),
            replace(base, content_type="application/vnd.test+json"),
            replace(base, payload_encoding="canonical-json-v1"),
            replace(base, payload_transform="json-secret-redaction-v1"),
            replace(base, retention_delete_by_ns=301),
            raw_event(payload=b'{"point":"30-0"}'),
        )
        for alternative in alternatives:
            with self.subTest(field=alternative):
                self.assertNotEqual(trace_for(alternative), baseline)

    def test_trace_changes_when_each_valid_derived_metadata_dimension_changes(self):
        raw = raw_event()
        state = reduction_for(raw).state
        base = derived_event(raw)
        prior = b"\x44" * 32
        baseline = next_trace(prior, raw, (base,), state)
        alternatives = (
            replace(base, ingest_seq=4),
            replace(base, event_type="raw_observed"),
            replace(base, event_version=2),
            replace(
                base,
                source_kind=SourceKind.KALSHI,
                source_id="kalshi",
                retention_delete_by_ns=None,
            ),
            replace(base, source_id="provider-b"),
            replace(base, source_entity_id="match-2"),
            replace(base, endpoint_id="backup"),
            replace(
                base,
                endpoint_id=None,
                endpoint_state=ProvenanceState.ABSENT,
            ),
            replace(
                base,
                channel_id="scores",
                channel_state=ProvenanceState.SAFE_ORIGINAL,
            ),
            replace(
                base,
                request_id="request-2",
                request_id_state=ProvenanceState.SAFE_ORIGINAL,
            ),
            replace(base, source_wall_ns=12),
            replace(base, source_generated_ns=13),
            replace(base, local_wall_ns=101),
            replace(base, local_monotonic_ns=51),
            replace(base, clock_uncertainty_ns=3),
            replace(base, connection_epoch=8),
            replace(base, provider_sequence="P-2"),
            replace(base, payload_encoding="json"),
            replace(base, retention_delete_by_ns=301),
            derived_event(raw, payload=b'{"different":true}'),
        )
        for alternative in alternatives:
            with self.subTest(field=alternative):
                self.assertNotEqual(
                    next_trace(prior, raw, (alternative,), state),
                    baseline,
                )

    def test_fixed_record_contract_fields_fail_closed_instead_of_being_forged(self):
        raw = raw_event()
        with self.assertRaises(ValueError):
            replace(raw, journal_version=2)
        with self.assertRaises(ValueError):
            replace(raw, parent_ingest_seq=1)
        with self.assertRaises(ValueError):
            replace(raw, payload_sha256="0" * 64)
        derived = derived_event(raw)
        with self.assertRaises(ValueError):
            replace(derived, content_type="application/json")
        with self.assertRaises(ValueError):
            replace(derived, payload_transform="identity-public-market-v1")

    def test_next_trace_rejects_invalid_digest_parent_session_state_and_container(self):
        raw = raw_event()
        state = reduction_for(raw).state
        derived = derived_event(raw)
        invalid_parent = replace(derived, parent_ingest_seq=1)
        other_session = replace(
            derived,
            session_id=OTHER_SESSION_ID,
        )
        for prior in (b"", b"x" * 31, b"x" * 33, bytearray(b"x" * 32)):
            with self.subTest(prior_type=type(prior), prior_length=len(prior)):
                with self.assertRaises((TypeError, ValueError)):
                    next_trace(prior, raw, (derived,), state)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            next_trace(b"x" * 32, raw, [derived], state)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            next_trace(b"x" * 32, derived, (derived,), state)
        with self.assertRaises(ValueError):
            next_trace(b"x" * 32, raw, (invalid_parent,), state)
        with self.assertRaises(ValueError):
            next_trace(b"x" * 32, raw, (other_session,), state)
        with self.assertRaises(ValueError):
            next_trace(
                b"x" * 32,
                raw,
                (derived,),
                replace(state, session_id=OTHER_SESSION_ID),
            )
        with self.assertRaises(ValueError):
            next_trace(
                b"x" * 32,
                raw,
                (derived,),
                replace(
                    state,
                    last_applied_raw_seq=4,
                ),
            )


if __name__ == "__main__":
    unittest.main()
