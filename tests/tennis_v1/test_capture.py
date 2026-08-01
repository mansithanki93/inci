from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import unittest

from tennis_v1.capture import (
    MAX_CAPTURE_BYTES,
    MAX_JSON_DEPTH,
    absent_provenance,
    capture_public_json,
    capture_redacted_json,
    capture_transport_error,
    issue_capture_authority,
    redacted_provenance,
    safe_provenance,
    validate_capture_against_authority,
    validate_captured_input,
)
from tennis_v1.events import (
    CaptureAuthority,
    CapturedInput,
    ProvenanceEvidence,
    ProvenanceState,
    SourceKind,
)
from tests.tennis_v1.test_events import manifest


class FakeAuthorizer:
    def __init__(self):
        self._manifest = manifest(provider_id="provider")
        self.calls: list[tuple[CaptureAuthority, CapturedInput]] = []
        self.error: Exception | None = None

    @property
    def session_manifest(self):
        return self._manifest

    def authorize_capture(self, authority, captured):
        self.calls.append((authority, captured))
        if self.error is not None:
            raise self.error
        if captured.session_id != self._manifest.session_id:
            raise ValueError("capture_contract_violation")
        if captured.source_id != authority.source_id or captured.payload != bytes(captured.payload):
            raise ValueError("capture_contract_violation")


class StrictEnvelopeAuthorizer(FakeAuthorizer):
    def __init__(self, expected: dict[str, object]):
        super().__init__()
        self.expected = expected

    def authorize_capture(self, authority, captured):
        actual = {
            "session_id": captured.session_id,
            "event_type": captured.event_type,
            "event_version": captured.event_version,
            "source_kind": captured.source_kind,
            "source_id": captured.source_id,
            "source_entity_id": captured.source_entity_id,
            "endpoint_id": captured.endpoint_id,
            "endpoint_state": captured.endpoint_state,
            "channel_id": captured.channel_id,
            "channel_state": captured.channel_state,
            "request_id": captured.request_id,
            "request_id_state": captured.request_id_state,
            "source_wall_ns": captured.source_wall_ns,
            "source_generated_ns": captured.source_generated_ns,
            "local_wall_ns": captured.local_wall_ns,
            "local_monotonic_ns": captured.local_monotonic_ns,
            "clock_uncertainty_ns": captured.clock_uncertainty_ns,
            "connection_epoch": captured.connection_epoch,
            "provider_sequence": captured.provider_sequence,
            "content_type": captured.content_type,
            "payload_encoding": captured.payload_encoding,
            "payload_transform": captured.payload_transform,
            "retention_delete_by_ns": captured.retention_delete_by_ns,
            "payload": captured.payload,
        }
        if actual != self.expected:
            raise AssertionError((actual, self.expected))
        self.calls.append((authority, captured))


def authority(
    authorizer: FakeAuthorizer | None = None,
    *,
    endpoint=None,
    channel=None,
    source_kind=SourceKind.PROVIDER,
    source_id="provider",
):
    owner = authorizer or FakeAuthorizer()
    return issue_capture_authority(
        session_authorizer=owner,
        source_kind=source_kind,
        source_id=source_id,
        source_entity_id="match-1",
        endpoint=endpoint or safe_provenance("live"),
        channel=channel or absent_provenance(),
        connection_epoch=7,
        allowed_content_types=("application/json",),
        wall_clock_ns=lambda: 100,
        monotonic_clock_ns=lambda: 90,
        clock_uncertainty_ns=lambda: 3,
    ), owner


def capture(raw: bytes = b'{"score":"15-0"}', **changes):
    issued, owner = authority()
    values = {
        "authority": issued,
        "content_type": "application/json",
        "request_id": safe_provenance("req-1"),
        "event_type": "provider.point",
        "event_version": 1,
        "source_wall_ns": 10,
        "source_generated_ns": 11,
        "provider_sequence": "A-1",
    }
    values.update(changes)
    return capture_public_json(raw, **values), owner


class CaptureTests(unittest.TestCase):
    def test_capture_validator_requires_exact_performing_authorizer_identity(self):
        issued, owner = authority()
        captured = capture_public_json(
            b'{"score":"15-0"}',
            authority=issued,
            content_type="application/json",
            request_id=safe_provenance("req-1"),
            event_type="provider.point",
            event_version=1,
            source_wall_ns=None,
            source_generated_ns=None,
            provider_sequence=None,
        )
        validate_capture_against_authority(
            issued,
            captured,
            owner.session_manifest,
            performing_authorizer=owner,
        )
        same_manifest_attacker = FakeAuthorizer()
        same_manifest_attacker._manifest = owner.session_manifest
        with self.assertRaisesRegex(
            ValueError,
            "capture_authorizer_identity_invalid",
        ):
            validate_capture_against_authority(
                issued,
                captured,
                owner.session_manifest,
                performing_authorizer=same_manifest_attacker,
            )

    def test_shared_capture_validators_reject_forged_envelope_and_transform(self):
        issued, owner = authority()
        captured = capture_public_json(
            b'{"score":"15-0"}',
            authority=issued,
            content_type="application/json",
            request_id=safe_provenance("req-1"),
            event_type="provider.point",
            event_version=1,
            source_wall_ns=10,
            source_generated_ns=11,
            provider_sequence="A-1",
        )
        validate_capture_against_authority(
            issued,
            captured,
            owner.session_manifest,
            performing_authorizer=owner,
        )
        validate_captured_input(captured, owner.session_manifest)
        object.__setattr__(captured, "payload_transform", "forged-transform-v1")
        with self.assertRaisesRegex(ValueError, "capture_transform_invalid"):
            validate_capture_against_authority(
                issued,
                captured,
                owner.session_manifest,
                performing_authorizer=owner,
            )
        with self.assertRaisesRegex(ValueError, "capture_transform_invalid"):
            validate_captured_input(captured, owner.session_manifest)

    def test_shared_validator_reparses_public_redacted_and_transport_payloads(self):
        issued, owner = authority()
        public = capture_public_json(
            b'{"score":"15-0"}',
            authority=issued,
            content_type="application/json",
            request_id=safe_provenance("req-1"),
            event_type="provider.point",
            event_version=1,
            source_wall_ns=None,
            source_generated_ns=None,
            provider_sequence=None,
        )
        object.__setattr__(public, "payload", b'{"token":"SECRET"}')
        with self.assertRaisesRegex(ValueError, "capture_payload_invalid"):
            validate_captured_input(public, owner.session_manifest)

        redacted = capture_redacted_json(
            b'{"token":"SECRET"}',
            authority=issued,
            content_type="application/json",
            request_id=redacted_provenance(),
            event_type="provider.point",
            event_version=1,
            source_wall_ns=None,
            source_generated_ns=None,
            provider_sequence=None,
        )
        object.__setattr__(
            redacted,
            "payload",
            b'{"token":"NOT_REDACTED"}',
        )
        with self.assertRaisesRegex(ValueError, "capture_payload_invalid"):
            validate_captured_input(redacted, owner.session_manifest)

        transport = capture_transport_error(
            exception_type="TimeoutError",
            status_code=504,
            error_code="timeout",
            request_id=safe_provenance("req-1"),
            authority=issued,
            event_type="provider.transport",
            event_version=1,
        )
        object.__setattr__(
            transport,
            "payload",
            b'{"exception_type":"TimeoutError"}',
        )
        with self.assertRaisesRegex(ValueError, "capture_payload_invalid"):
            validate_captured_input(transport, owner.session_manifest)

    def test_direct_capture_and_authority_construction_raise_explicitly(self):
        with self.assertRaisesRegex(TypeError, "safe capture factory"):
            CapturedInput()
        with self.assertRaisesRegex(TypeError, "session runtime"):
            CaptureAuthority()

    def test_provenance_objects_require_safe_factories_and_never_store_redacted_input(self):
        with self.assertRaisesRegex(TypeError, "safe provenance factory"):
            ProvenanceEvidence()
        self.assertEqual(absent_provenance().state, ProvenanceState.ABSENT)
        self.assertEqual(safe_provenance("request-1").value, "request-1")
        self.assertEqual(redacted_provenance().value, "<redacted>")
        with self.assertRaises(TypeError):
            redacted_provenance("secret")  # type: ignore[call-arg]
        with self.assertRaises(ValueError):
            safe_provenance("https://host.invalid/?token=x")

    def test_authority_requires_exact_session_bound_provider_authorizer(self):
        with self.assertRaises(TypeError):
            issue_capture_authority(
                session_authorizer=object(),  # type: ignore[arg-type]
                source_kind=SourceKind.PROVIDER,
                source_id="provider",
                source_entity_id="match",
                endpoint=absent_provenance(),
                channel=absent_provenance(),
                connection_epoch=0,
                allowed_content_types=("application/json",),
                wall_clock_ns=lambda: 1,
                monotonic_clock_ns=lambda: 1,
                clock_uncertainty_ns=lambda: 0,
            )
        mismatched = FakeAuthorizer()
        mismatched._manifest = manifest(provider_id="other")
        with self.assertRaises(ValueError):
            authority(mismatched)

    def test_authority_fixes_source_entity_endpoint_channel_epoch_and_clock_fields(self):
        captured, owner = capture()
        self.assertEqual(
            (
                captured.session_id,
                captured.source_entity_id,
                captured.endpoint_id,
                captured.channel_id,
                captured.connection_epoch,
                captured.local_wall_ns,
                captured.local_monotonic_ns,
                captured.clock_uncertainty_ns,
                captured.retention_delete_by_ns,
            ),
            (
                owner.session_manifest.session_id,
                "match-1",
                "live",
                None,
                7,
                100,
                90,
                3,
                owner.session_manifest.required_retention_until_ns,
            ),
        )

    def test_endpoint_and_channel_are_distinct_bounded_provenance_fields(self):
        issued, _ = authority(
            endpoint=safe_provenance("endpoint-1"),
            channel=safe_provenance("channel-1"),
        )
        self.assertEqual(issued.endpoint_id, "endpoint-1")
        self.assertEqual(issued.channel_id, "channel-1")

    def test_endpoint_channel_and_request_each_preserve_absent_safe_or_redacted_state(self):
        cases = (absent_provenance(), safe_provenance("safe"), redacted_provenance())
        for endpoint in cases:
            for channel in cases:
                issued, _ = authority(endpoint=endpoint, channel=channel)
                captured = capture_public_json(
                    b"{}",
                    authority=issued,
                    content_type="application/json",
                    request_id=endpoint,
                    event_type="provider.point",
                    event_version=1,
                    source_wall_ns=None,
                    source_generated_ns=None,
                    provider_sequence=None,
                )
                self.assertEqual((captured.endpoint_id, captured.endpoint_state), (endpoint.value, endpoint.state))
                self.assertEqual((captured.channel_id, captured.channel_state), (channel.value, channel.state))
                self.assertEqual((captured.request_id, captured.request_id_state), (endpoint.value, endpoint.state))

    def test_content_type_and_typed_endpoint_channel_request_are_captured(self):
        captured, _ = capture()
        self.assertEqual(captured.content_type, "application/json")
        self.assertEqual(captured.payload_encoding, "json")
        self.assertEqual(captured.payload_transform, "identity-public-market-v1")

    def test_public_json_rejects_duplicate_keys_secrets_headers_and_url_queries(self):
        invalid = (
            b'{"x":1,"x":2}',
            b'{"Authorization":"Bearer abc"}',
            b'{"headers":{"x":"y"}}',
            b'{"url":"https://example.invalid/path?q=secret"}',
            b'{"pem":"-----BEGIN PRIVATE KEY-----"}',
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    capture(raw)

    def test_public_json_rejects_secret_key_variants_without_leaking_values(self):
        sentinel = "PUBLIC_SECRET_MUST_NOT_LEAK"
        variants = (
            "x-api-key",
            "xapikey",
            "api_token",
            "authToken",
            "Secret-Key",
            "clientSecret",
            "REFRESH_TOKEN",
            "access-token",
            "KalshiAccessKey",
            "KALSHI_ACCESS_SIGNATURE",
            "providerAuthToken",
            "vendor_api_key",
            "oauthAccessToken",
            "sessionRefreshToken",
            "signingPrivateKey",
            "webhookSignature",
            "appClientSecret",
            "tenantCredential",
        )
        for key in variants:
            with self.subTest(key=key):
                issued, owner = authority()
                raw = json.dumps(
                    {"nested": [{key: sentinel}]},
                    separators=(",", ":"),
                ).encode("utf-8")
                with self.assertRaisesRegex(
                    ValueError,
                    r"\Asecret_shaped_content\Z",
                ) as caught:
                    capture_public_json(
                        raw,
                        authority=issued,
                        content_type="application/json",
                        request_id=absent_provenance(),
                        event_type="provider.point",
                        event_version=1,
                        source_wall_ns=None,
                        source_generated_ns=None,
                        provider_sequence=None,
                    )
                self.assertNotIn(sentinel, str(caught.exception))
                self.assertNotIn(sentinel, repr(caught.exception))
                self.assertEqual(owner.calls, [])

    def test_redacted_json_canonicalizes_secret_key_variants_without_leaks(self):
        sentinel = "REDACTED_SECRET_MUST_NOT_LEAK"
        raw = (
            b'{"safe":"kept","nested":['
            b'{"x-api-key":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"xapikey":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"api_token":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"authToken":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"Secret-Key":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"clientSecret":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"REFRESH_TOKEN":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"access-token":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"KalshiAccessKey":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"KALSHI_ACCESS_SIGNATURE":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"providerAuthToken":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"vendor_api_key":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"oauthAccessToken":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"sessionRefreshToken":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"signingPrivateKey":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"webhookSignature":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"appClientSecret":"REDACTED_SECRET_MUST_NOT_LEAK"},'
            b'{"tenantCredential":"REDACTED_SECRET_MUST_NOT_LEAK"}'
            b"]}"
        )
        issued, _ = authority()
        captured = capture_redacted_json(
            raw,
            authority=issued,
            content_type="application/json",
            request_id=redacted_provenance(),
            event_type="provider.point",
            event_version=1,
            source_wall_ns=None,
            source_generated_ns=None,
            provider_sequence=None,
        )
        self.assertEqual(
            captured.payload,
            (
                b'{"nested":['
                b'{"x-api-key":"<redacted>"},'
                b'{"xapikey":"<redacted>"},'
                b'{"api_token":"<redacted>"},'
                b'{"authToken":"<redacted>"},'
                b'{"Secret-Key":"<redacted>"},'
                b'{"clientSecret":"<redacted>"},'
                b'{"REFRESH_TOKEN":"<redacted>"},'
                b'{"access-token":"<redacted>"},'
                b'{"KalshiAccessKey":"<redacted>"},'
                b'{"KALSHI_ACCESS_SIGNATURE":"<redacted>"},'
                b'{"providerAuthToken":"<redacted>"},'
                b'{"vendor_api_key":"<redacted>"},'
                b'{"oauthAccessToken":"<redacted>"},'
                b'{"sessionRefreshToken":"<redacted>"},'
                b'{"signingPrivateKey":"<redacted>"},'
                b'{"webhookSignature":"<redacted>"},'
                b'{"appClientSecret":"<redacted>"},'
                b'{"tenantCredential":"<redacted>"}'
                b'],"safe":"kept"}'
            ),
        )
        self.assertNotIn(sentinel, repr(captured))
        self.assertNotIn(sentinel.encode("ascii"), captured.payload)

    def test_secret_key_detection_allows_safe_ordinary_key_fragments(self):
        raw = (
            b'{"authorizationPolicy":"public","credentialedUser":true,'
            b'"hockeyScore":"15-0","monkeyBusiness":false,'
            b'"passwordlessMode":true,"secretaryName":"Ada",'
            b'"signatureAlgorithm":"ed25519","tokenizerVersion":"v1"}'
        )
        captured, _ = capture(raw)
        self.assertEqual(captured.payload, raw)

    def test_public_and_redacted_json_reject_urls_with_s_before_unsafe_components(self):
        unsafe_values = (
            (
                b'{"url":"https://service-user:pass@x.invalid/path"}',
                b"service-user:pass",
            ),
            (
                b'{"url":"https://service.invalid/path?q=api_key%3Dsecret"}',
                b"api_key%3Dsecret",
            ),
        )
        for factory in (capture_public_json, capture_redacted_json):
            for raw, sentinel in unsafe_values:
                with self.subTest(factory=factory.__name__, sentinel=sentinel):
                    issued, owner = authority()
                    with self.assertRaises(ValueError) as caught:
                        factory(
                            raw,
                            authority=issued,
                            content_type="application/json",
                            request_id=absent_provenance(),
                            event_type="provider.point",
                            event_version=1,
                            source_wall_ns=None,
                            source_generated_ns=None,
                            provider_sequence=None,
                        )
                    self.assertNotIn(
                        sentinel.decode("ascii"), str(caught.exception)
                    )
                    self.assertEqual(owner.calls, [])

    def test_redacted_json_removes_nested_case_variant_secrets_deterministically(self):
        issued, _ = authority()
        raw = b'{"nested":{"Api-Key":"TOPSECRET","safe":1},"TOKEN":"ALSOSECRET"}'
        captured = capture_redacted_json(
            raw,
            authority=issued,
            content_type="application/json",
            request_id=redacted_provenance(),
            event_type="provider.point",
            event_version=1,
            source_wall_ns=None,
            source_generated_ns=None,
            provider_sequence=None,
        )
        self.assertEqual(
            captured.payload,
            b'{"TOKEN":"<redacted>","nested":{"Api-Key":"<redacted>","safe":1}}',
        )
        self.assertNotIn(b"TOPSECRET", captured.payload)
        self.assertNotIn(b"ALSOSECRET", captured.payload)

    def test_transport_error_accepts_only_allowlisted_typed_fields(self):
        issued, _ = authority()
        captured = capture_transport_error(
            exception_type="TimeoutError",
            status_code=504,
            error_code="upstream_timeout",
            request_id=absent_provenance(),
            authority=issued,
            event_type="provider.transport_error",
            event_version=1,
        )
        self.assertEqual(captured.content_type, "application/vnd.inci.transport-error+json")
        self.assertEqual(
            set(json.loads(captured.payload)),
            {"exception_type", "status_code", "error_code", "request_id"},
        )
        with self.assertRaises(ValueError):
            capture_transport_error(
                exception_type="TimeoutError: https://host.invalid/?token=x",
                status_code=504,
                error_code=None,
                request_id=absent_provenance(),
                authority=issued,
                event_type="provider.transport_error",
                event_version=1,
            )

    def test_capture_rejects_oversize_or_overdeep_json_before_decode(self):
        with self.assertRaises(ValueError):
            capture(b" " * (MAX_CAPTURE_BYTES + 1))
        with self.assertRaises(ValueError):
            capture((b"[" * (MAX_JSON_DEPTH + 1)) + (b"]" * (MAX_JSON_DEPTH + 1)))

    def test_session_authorizer_revalidates_every_envelope_field_and_payload_byte(self):
        expected = {
            "session_id": "12345678-1234-4234-8234-123456789abc",
            "event_type": "provider.point",
            "event_version": 3,
            "source_kind": SourceKind.PROVIDER,
            "source_id": "provider",
            "source_entity_id": "match-1",
            "endpoint_id": "live",
            "endpoint_state": ProvenanceState.SAFE_ORIGINAL,
            "channel_id": "scores",
            "channel_state": ProvenanceState.SAFE_ORIGINAL,
            "request_id": "req-9",
            "request_id_state": ProvenanceState.SAFE_ORIGINAL,
            "source_wall_ns": 12,
            "source_generated_ns": 13,
            "local_wall_ns": 100,
            "local_monotonic_ns": 90,
            "clock_uncertainty_ns": 3,
            "connection_epoch": 7,
            "provider_sequence": "SEQ-9",
            "content_type": "application/json",
            "payload_encoding": "json",
            "payload_transform": "identity-public-market-v1",
            "retention_delete_by_ns": 300,
            "payload": b'{"exact":"durable bytes"}',
        }
        owner = StrictEnvelopeAuthorizer(expected)
        issued, _ = authority(owner, channel=safe_provenance("scores"))
        captured = capture_public_json(
            b'{"exact":"durable bytes"}',
            authority=issued,
            content_type="application/json",
            request_id=safe_provenance("req-9"),
            event_type="provider.point",
            event_version=3,
            source_wall_ns=12,
            source_generated_ns=13,
            provider_sequence="SEQ-9",
        )
        self.assertEqual(owner.calls, [(issued, captured)])

    def test_capture_checks_coordinator_halt_before_returning_an_envelope(self):
        issued, owner = authority()
        owner.error = RuntimeError("retention_global_halt")
        with self.assertRaisesRegex(RuntimeError, "retention_global_halt"):
            capture_public_json(
                b"{}",
                authority=issued,
                content_type="application/json",
                request_id=absent_provenance(),
                event_type="provider.point",
                event_version=1,
                source_wall_ns=None,
                source_generated_ns=None,
                provider_sequence=None,
            )

    def test_secret_sentinel_absent_from_capture_repr_errors_and_encoded_bytes(self):
        issued, _ = authority()
        captured = capture_redacted_json(
            b'{"password":"NEVER_STORE_THIS"}',
            authority=issued,
            content_type="application/json",
            request_id=redacted_provenance(),
            event_type="provider.point",
            event_version=1,
            source_wall_ns=None,
            source_generated_ns=None,
            provider_sequence=None,
        )
        self.assertNotIn("NEVER_STORE_THIS", repr(captured))
        self.assertNotIn(b"NEVER_STORE_THIS", captured.payload)

    def test_captured_values_are_frozen(self):
        captured, _ = capture()
        with self.assertRaises(FrozenInstanceError):
            captured.payload = b"changed"  # type: ignore[misc]

    def test_only_capture_module_may_use_private_sentinel_constructor(self):
        package_root = Path(__file__).resolve().parents[2] / "tennis_v1"
        private_forbidden = {
            "_CAPTURE_CONSTRUCTION_SENTINEL",
            "_build_capture_authority",
            "_build_captured_input",
        }
        for path in package_root.glob("*.py"):
            if path.name == "capture.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {
                node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
            }
            attrs = {
                node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            }
            self.assertFalse((names | attrs) & private_forbidden, path.name)
            if path.name not in {
                "session.py",
                "runtime.py",
                "bootstrap.py",
                "sequencer.py",
            }:
                self.assertNotIn("issue_capture_authority", names | attrs, path.name)
