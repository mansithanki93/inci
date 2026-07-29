"""Safe, policy-enforcing capture factories for Tennis v1."""

from __future__ import annotations

import json
import re
import hashlib
from urllib.parse import urlsplit

from .canonical import canonical_json_bytes
from .events import (
    CaptureAuthority,
    CapturedInput,
    PersistedEvent,
    ProvenanceEvidence,
    ProvenanceState,
    RecordKind,
    SessionCaptureAuthorizer,
    SessionManifest,
    SourceKind,
    _exact_nonnegative_integer,
    _safe_identifier,
    _validate_content_type,
    _validate_provenance,
)


MAX_CAPTURE_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 250_000
MAX_CONTENT_TYPES = 32
SECRET_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "setcookie",
        "apikey",
        "apitoken",
        "authtoken",
        "accesstoken",
        "refreshtoken",
        "bearer",
        "token",
        "secret",
        "secretkey",
        "password",
        "passwd",
        "privatekey",
        "signature",
        "credential",
        "clientsecret",
        "kalshiaccesskey",
        "kalshiaccesssignature",
    }
)
TRANSPORT_CONTENT_TYPE = "application/vnd.inci.transport-error+json"
_FIXED_NONPROVIDER_SOURCE_IDS = {
    SourceKind.KALSHI: "kalshi",
    SourceKind.TIMER: "timer",
    SourceKind.SYSTEM: "tennis-v1",
}
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{1,127}\Z")
_CAPTURE_CONSTRUCTION_SENTINEL = object()


class CaptureValidationError(ValueError):
    """A stable, non-secret-bearing capture rejection."""


def _build_provenance(
    value: str | None, state: ProvenanceState, sentinel: object
) -> ProvenanceEvidence:
    if sentinel is not _CAPTURE_CONSTRUCTION_SENTINEL:
        raise TypeError("private capture constructor")
    instance = object.__new__(ProvenanceEvidence)
    object.__setattr__(instance, "value", value)
    object.__setattr__(instance, "state", state)
    return instance


def absent_provenance() -> ProvenanceEvidence:
    return _build_provenance(
        None, ProvenanceState.ABSENT, _CAPTURE_CONSTRUCTION_SENTINEL
    )


def safe_provenance(value: str) -> ProvenanceEvidence:
    try:
        safe = _safe_identifier(value, "provenance")
    except (TypeError, ValueError):
        raise CaptureValidationError("invalid_safe_provenance") from None
    return _build_provenance(
        safe, ProvenanceState.SAFE_ORIGINAL, _CAPTURE_CONSTRUCTION_SENTINEL
    )


def redacted_provenance() -> ProvenanceEvidence:
    return _build_provenance(
        "<redacted>", ProvenanceState.REDACTED, _CAPTURE_CONSTRUCTION_SENTINEL
    )


def _require_provenance(
    evidence: object, field_name: str
) -> ProvenanceEvidence:
    if type(evidence) is not ProvenanceEvidence:
        raise TypeError(f"{field_name}: ProvenanceEvidence required")
    try:
        _validate_provenance(evidence.value, evidence.state, field_name)
    except (TypeError, ValueError):
        raise CaptureValidationError(f"{field_name}_provenance_invalid") from None
    return evidence


def _build_capture_authority(
    values: dict[str, object], sentinel: object
) -> CaptureAuthority:
    if sentinel is not _CAPTURE_CONSTRUCTION_SENTINEL:
        raise TypeError("private capture constructor")
    instance = object.__new__(CaptureAuthority)
    for name, value in values.items():
        object.__setattr__(instance, name, value)
    return instance


def issue_capture_authority(
    *,
    session_authorizer: SessionCaptureAuthorizer,
    source_kind: SourceKind,
    source_id: str,
    source_entity_id: str,
    endpoint: ProvenanceEvidence,
    channel: ProvenanceEvidence,
    connection_epoch: int,
    allowed_content_types: tuple[str, ...],
    wall_clock_ns,
    monotonic_clock_ns,
    clock_uncertainty_ns,
) -> CaptureAuthority:
    try:
        session_manifest = session_authorizer.session_manifest
    except Exception:
        raise TypeError("session_authorizer: bound manifest required") from None
    if type(session_manifest) is not SessionManifest or not callable(
        getattr(session_authorizer, "authorize_capture", None)
    ):
        raise TypeError("session_authorizer: exact session binding required")
    if type(source_kind) is not SourceKind:
        raise TypeError("source_kind: exact SourceKind required")
    try:
        checked_source_id = _safe_identifier(source_id, "source_id")
        checked_entity = _safe_identifier(source_entity_id, "source_entity_id")
        checked_epoch = _exact_nonnegative_integer(
            connection_epoch, "connection_epoch"
        )
    except (TypeError, ValueError) as error:
        raise CaptureValidationError("authority_identity_invalid") from error
    if source_kind is SourceKind.PROVIDER:
        if checked_source_id != session_manifest.provider_id:
            raise CaptureValidationError("provider_session_binding_mismatch")
    elif checked_source_id != _FIXED_NONPROVIDER_SOURCE_IDS[source_kind]:
        raise CaptureValidationError("nonprovider_source_id_invalid")
    checked_endpoint = _require_provenance(endpoint, "endpoint")
    checked_channel = _require_provenance(channel, "channel")
    if type(allowed_content_types) is not tuple or not (
        1 <= len(allowed_content_types) <= MAX_CONTENT_TYPES
    ):
        raise CaptureValidationError("content_type_allowlist_invalid")
    normalized: list[str] = []
    for content_type in allowed_content_types:
        try:
            normalized.append(_validate_content_type(content_type))
        except (TypeError, ValueError):
            raise CaptureValidationError("content_type_allowlist_invalid") from None
    if len(set(normalized)) != len(normalized):
        raise CaptureValidationError("content_type_allowlist_invalid")
    for clock in (wall_clock_ns, monotonic_clock_ns, clock_uncertainty_ns):
        if not callable(clock):
            raise TypeError("capture clocks must be callable")
    return _build_capture_authority(
        {
            "session_id": session_manifest.session_id,
            "source_kind": source_kind,
            "source_id": checked_source_id,
            "source_entity_id": checked_entity,
            "endpoint_id": checked_endpoint.value,
            "endpoint_state": checked_endpoint.state,
            "channel_id": checked_channel.value,
            "channel_state": checked_channel.state,
            "connection_epoch": checked_epoch,
            "_session_authorizer": session_authorizer,
            "_wall_clock_ns": wall_clock_ns,
            "_monotonic_clock_ns": monotonic_clock_ns,
            "_clock_uncertainty_ns": clock_uncertainty_ns,
            "_allowed_content_types": tuple(normalized),
        },
        _CAPTURE_CONSTRUCTION_SENTINEL,
    )


def _build_captured_input(
    values: dict[str, object], sentinel: object
) -> CapturedInput:
    if sentinel is not _CAPTURE_CONSTRUCTION_SENTINEL:
        raise TypeError("private capture constructor")
    instance = object.__new__(CapturedInput)
    for name, value in values.items():
        object.__setattr__(instance, name, value)
    return instance


def _prescan_depth(content: bytes) -> None:
    depth = 0
    in_string = False
    escaped = False
    for byte in content:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise CaptureValidationError("json_depth_exceeded")
        elif byte in (0x7D, 0x5D):
            depth -= 1
            if depth < 0:
                raise CaptureValidationError("invalid_json")


def _reject_float(_: str) -> object:
    raise CaptureValidationError("json_float_forbidden")


def _reject_constant(_: str) -> object:
    raise CaptureValidationError("json_constant_forbidden")


def _duplicate_free_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CaptureValidationError("duplicate_json_key")
        result[key] = value
    return result


def _parse_json(content: bytes) -> object:
    if type(content) is not bytes:
        raise TypeError("raw_json: exact bytes required")
    if len(content) > MAX_CAPTURE_BYTES:
        raise CaptureValidationError("capture_bytes_exceeded")
    if content.startswith(b"\xef\xbb\xbf"):
        raise CaptureValidationError("utf8_bom_forbidden")
    _prescan_depth(content)
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError:
        raise CaptureValidationError("invalid_utf8") from None
    try:
        return json.loads(
            decoded,
            object_pairs_hook=_duplicate_free_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except CaptureValidationError:
        raise
    except (json.JSONDecodeError, ValueError):
        raise CaptureValidationError("invalid_json") from None


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _secret_shaped_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return any(normalized.endswith(suffix) for suffix in SECRET_KEYS)


def _unsafe_string(value: str) -> bool:
    upper = value.upper()
    if "-----BEGIN " in upper or "-----END " in upper:
        return True
    for match in re.finditer(r"""https?://[^\s"'<>]+""", value, re.IGNORECASE):
        try:
            parsed = urlsplit(match.group(0))
        except ValueError:
            return True
        if (
            parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
        ):
            return True
    return False


def _validate_and_redact_json(value: object, *, redact: bool) -> None:
    count = 0
    stack: list[tuple[object, str | None]] = [(value, None)]
    while stack:
        current, parent_key = stack.pop()
        count += 1
        if count > MAX_JSON_NODES:
            raise CaptureValidationError("json_nodes_exceeded")
        if current is None or type(current) in (str, int, bool):
            if type(current) is str and _unsafe_string(current):
                raise CaptureValidationError("unsafe_structured_content")
            continue
        if type(current) is list:
            stack.extend((item, parent_key) for item in current)
            continue
        if type(current) is not dict:
            raise CaptureValidationError("unsupported_json_value")
        normalized_parent = _normalized_key(parent_key) if parent_key else ""
        if normalized_parent in {"headers", "header", "request", "httprequest"}:
            raise CaptureValidationError("forbidden_structured_object")
        if normalized_parent in {"environment", "environ", "env"} and any(
            type(key) is str and _ENV_NAME.fullmatch(key) is not None
            for key in current
        ):
            raise CaptureValidationError("credential_environment_forbidden")
        for key, item in current.items():
            count += 1
            if count > MAX_JSON_NODES:
                raise CaptureValidationError("json_nodes_exceeded")
            if type(key) is not str:
                raise CaptureValidationError("json_key_invalid")
            normalized = _normalized_key(key)
            if normalized in {"headers", "header", "request", "httprequest"}:
                raise CaptureValidationError("forbidden_structured_object")
            if _secret_shaped_key(key):
                if not redact:
                    raise CaptureValidationError("secret_shaped_content")
                current[key] = "<redacted>"
                continue
            stack.append((item, key))


def _validated_content_type(
    authority: CaptureAuthority, content_type: object
) -> str:
    try:
        checked = _validate_content_type(content_type)
    except (TypeError, ValueError):
        raise CaptureValidationError("content_type_not_allowed") from None
    if checked not in authority._allowed_content_types:
        raise CaptureValidationError("content_type_not_allowed")
    return checked


def _capture_common(
    *,
    authority: CaptureAuthority,
    request_id: ProvenanceEvidence,
    event_type: str,
    event_version: int,
    source_wall_ns: int | None,
    source_generated_ns: int | None,
    provider_sequence: str | None,
    content_type: str,
    payload_encoding: str,
    payload_transform: str,
    payload: bytes,
) -> CapturedInput:
    if type(authority) is not CaptureAuthority:
        raise TypeError("authority: issued CaptureAuthority required")
    request = _require_provenance(request_id, "request_id")
    try:
        checked_event = _safe_identifier(event_type, "event_type")
        if type(event_version) is not int or event_version < 1:
            raise ValueError
        if source_wall_ns is not None:
            _exact_nonnegative_integer(source_wall_ns, "source_wall_ns")
        if source_generated_ns is not None:
            _exact_nonnegative_integer(
                source_generated_ns, "source_generated_ns"
            )
        if provider_sequence is not None:
            _safe_identifier(provider_sequence, "provider_sequence")
        local_wall_ns = _exact_nonnegative_integer(
            authority._wall_clock_ns(), "local_wall_ns"
        )
        local_monotonic_ns = _exact_nonnegative_integer(
            authority._monotonic_clock_ns(), "local_monotonic_ns"
        )
        uncertainty_ns = _exact_nonnegative_integer(
            authority._clock_uncertainty_ns(), "clock_uncertainty_ns"
        )
    except (TypeError, ValueError):
        raise CaptureValidationError("capture_envelope_invalid") from None
    if type(payload) is not bytes or len(payload) > MAX_CAPTURE_BYTES:
        raise CaptureValidationError("capture_payload_invalid")
    performing_authorizer = authority._session_authorizer
    manifest = performing_authorizer.session_manifest
    if (
        type(manifest) is not SessionManifest
        or manifest.session_id != authority.session_id
    ):
        raise CaptureValidationError("authority_session_binding_invalid")
    delete_by = (
        manifest.required_retention_until_ns
        if authority.source_kind is SourceKind.PROVIDER
        else None
    )
    candidate = _build_captured_input(
        {
            "session_id": authority.session_id,
            "event_type": checked_event,
            "event_version": event_version,
            "source_kind": authority.source_kind,
            "source_id": authority.source_id,
            "source_entity_id": authority.source_entity_id,
            "endpoint_id": authority.endpoint_id,
            "endpoint_state": authority.endpoint_state,
            "channel_id": authority.channel_id,
            "channel_state": authority.channel_state,
            "request_id": request.value,
            "request_id_state": request.state,
            "source_wall_ns": source_wall_ns,
            "source_generated_ns": source_generated_ns,
            "local_wall_ns": local_wall_ns,
            "local_monotonic_ns": local_monotonic_ns,
            "clock_uncertainty_ns": uncertainty_ns,
            "connection_epoch": authority.connection_epoch,
            "provider_sequence": provider_sequence,
            "content_type": content_type,
            "payload_encoding": payload_encoding,
            "payload_transform": payload_transform,
            "retention_delete_by_ns": delete_by,
            "payload": payload,
        },
        _CAPTURE_CONSTRUCTION_SENTINEL,
    )
    validate_capture_against_authority(
        authority,
        candidate,
        manifest,
        performing_authorizer=performing_authorizer,
    )
    performing_authorizer.authorize_capture(authority, candidate)
    validate_capture_against_authority(
        authority,
        candidate,
        manifest,
        performing_authorizer=performing_authorizer,
    )
    return candidate


def _capture_error(reason: str) -> CaptureValidationError:
    return CaptureValidationError(reason)


def _validate_transport_payload(captured: CapturedInput) -> None:
    try:
        value = _parse_json(captured.payload)
        if type(value) is not dict or set(value) != {
            "exception_type",
            "status_code",
            "error_code",
            "request_id",
        }:
            raise _capture_error("capture_payload_invalid")
        request = value["request_id"]
        if type(request) is not dict or set(request) != {"value", "state"}:
            raise _capture_error("capture_payload_invalid")
        if (
            request["value"] != captured.request_id
            or request["state"] != captured.request_id_state.value
        ):
            raise _capture_error("capture_payload_invalid")
        _safe_identifier(value["exception_type"], "exception_type")
        status = value["status_code"]
        if status is not None and (
            type(status) is not int or not 100 <= status <= 599
        ):
            raise _capture_error("capture_payload_invalid")
        error_code = value["error_code"]
        if error_code is not None:
            _safe_identifier(error_code, "error_code")
        if canonical_json_bytes(value) != captured.payload:
            raise _capture_error("capture_payload_invalid")
    except CaptureValidationError:
        raise
    except (TypeError, ValueError, KeyError, AttributeError):
        raise _capture_error("capture_payload_invalid") from None


def _validate_capture_payload(captured: CapturedInput) -> None:
    transform = captured.payload_transform
    try:
        if transform == "identity-public-market-v1":
            if captured.payload_encoding != "json":
                raise _capture_error("capture_transform_invalid")
            value = _parse_json(captured.payload)
            _validate_and_redact_json(value, redact=False)
        elif transform == "json-secret-redaction-v1":
            if captured.payload_encoding != "canonical-json-v1":
                raise _capture_error("capture_transform_invalid")
            value = _parse_json(captured.payload)
            _validate_and_redact_json(value, redact=True)
            if canonical_json_bytes(value) != captured.payload:
                raise _capture_error("capture_payload_invalid")
        elif transform == "sanitized-transport-error-v1":
            if (
                captured.payload_encoding != "canonical-json-v1"
                or captured.content_type != TRANSPORT_CONTENT_TYPE
            ):
                raise _capture_error("capture_transform_invalid")
            _validate_transport_payload(captured)
        else:
            raise _capture_error("capture_transform_invalid")
    except CaptureValidationError as error:
        if str(error) in {
            "capture_transform_invalid",
            "capture_payload_invalid",
        }:
            raise
        raise _capture_error("capture_payload_invalid") from None
    except (TypeError, ValueError, AttributeError):
        raise _capture_error("capture_payload_invalid") from None


def validate_captured_input(
    captured: CapturedInput,
    session_manifest: SessionManifest,
) -> None:
    """Revalidate an admitted envelope without trusting its constructor path."""
    if type(captured) is not CapturedInput:
        raise TypeError("exact CapturedInput required")
    if type(session_manifest) is not SessionManifest:
        raise TypeError("exact SessionManifest required")
    try:
        if captured.session_id != session_manifest.session_id:
            raise _capture_error("capture_session_binding_invalid")
        if captured.payload_transform not in {
            "identity-public-market-v1",
            "json-secret-redaction-v1",
            "sanitized-transport-error-v1",
        }:
            raise _capture_error("capture_transform_invalid")
        _validate_capture_payload(captured)
        # PersistedEvent owns the complete scalar, enum, provenance, timestamp,
        # content, transform-name, payload and retention-shape validation.
        PersistedEvent(
            journal_version=1,
            record_kind=RecordKind.RAW,
            ingest_seq=1,
            session_id=captured.session_id,
            event_type=captured.event_type,
            event_version=captured.event_version,
            source_kind=captured.source_kind,
            source_id=captured.source_id,
            source_entity_id=captured.source_entity_id,
            endpoint_id=captured.endpoint_id,
            endpoint_state=captured.endpoint_state,
            channel_id=captured.channel_id,
            channel_state=captured.channel_state,
            request_id=captured.request_id,
            request_id_state=captured.request_id_state,
            source_wall_ns=captured.source_wall_ns,
            source_generated_ns=captured.source_generated_ns,
            local_wall_ns=captured.local_wall_ns,
            local_monotonic_ns=captured.local_monotonic_ns,
            clock_uncertainty_ns=captured.clock_uncertainty_ns,
            connection_epoch=captured.connection_epoch,
            provider_sequence=captured.provider_sequence,
            parent_ingest_seq=None,
            content_type=captured.content_type,
            payload_encoding=captured.payload_encoding,
            payload_transform=captured.payload_transform,
            retention_delete_by_ns=captured.retention_delete_by_ns,
            payload_sha256=hashlib.sha256(captured.payload).hexdigest(),
            payload=captured.payload,
        )
        if captured.source_kind is SourceKind.PROVIDER:
            if (
                captured.source_id != session_manifest.provider_id
                or captured.retention_delete_by_ns
                != session_manifest.required_retention_until_ns
            ):
                raise _capture_error("capture_provider_binding_invalid")
        elif (
            captured.source_id
            != _FIXED_NONPROVIDER_SOURCE_IDS[captured.source_kind]
            or captured.retention_delete_by_ns is not None
        ):
            raise _capture_error("capture_nonprovider_binding_invalid")
    except CaptureValidationError:
        raise
    except (TypeError, ValueError, AttributeError, KeyError):
        raise _capture_error("capture_envelope_invalid") from None


def validate_capture_against_authority(
    authority: CaptureAuthority,
    captured: CapturedInput,
    session_manifest: SessionManifest,
    *,
    performing_authorizer: SessionCaptureAuthorizer,
) -> None:
    """Bind a returned envelope to the exact authority that admitted it."""
    if type(authority) is not CaptureAuthority:
        raise TypeError("exact CaptureAuthority required")
    if type(captured) is not CapturedInput:
        raise TypeError("exact CapturedInput required")
    if type(session_manifest) is not SessionManifest:
        raise TypeError("exact SessionManifest required")
    try:
        owner = object.__getattribute__(authority, "_session_authorizer")
        if owner is not performing_authorizer:
            raise _capture_error("capture_authorizer_identity_invalid")
        if (
            owner.session_manifest is not session_manifest
            or authority.session_id != session_manifest.session_id
            or not callable(getattr(owner, "authorize_capture", None))
            or not callable(authority._wall_clock_ns)
            or not callable(authority._monotonic_clock_ns)
            or not callable(authority._clock_uncertainty_ns)
        ):
            raise _capture_error("capture_authority_binding_invalid")
        allowed = authority._allowed_content_types
        if type(allowed) is not tuple or not (
            1 <= len(allowed) <= MAX_CONTENT_TYPES
        ):
            raise _capture_error("capture_authority_invalid")
        normalized = tuple(_validate_content_type(item) for item in allowed)
        if len(set(normalized)) != len(normalized) or normalized != allowed:
            raise _capture_error("capture_authority_invalid")
        if (
            captured.content_type != TRANSPORT_CONTENT_TYPE
            and captured.content_type not in normalized
        ):
            raise _capture_error("content_type_not_allowed")
        authority_projection = (
            authority.session_id,
            authority.source_kind,
            authority.source_id,
            authority.source_entity_id,
            authority.endpoint_id,
            authority.endpoint_state,
            authority.channel_id,
            authority.channel_state,
            authority.connection_epoch,
        )
        captured_projection = (
            captured.session_id,
            captured.source_kind,
            captured.source_id,
            captured.source_entity_id,
            captured.endpoint_id,
            captured.endpoint_state,
            captured.channel_id,
            captured.channel_state,
            captured.connection_epoch,
        )
        if authority_projection != captured_projection:
            raise _capture_error("capture_authority_envelope_mismatch")
        validate_captured_input(captured, session_manifest)
    except CaptureValidationError:
        raise
    except (TypeError, ValueError, AttributeError, KeyError):
        raise _capture_error("capture_authority_invalid") from None


def capture_public_json(
    raw_json: bytes,
    *,
    authority: CaptureAuthority,
    content_type: str,
    request_id: ProvenanceEvidence,
    event_type: str,
    event_version: int,
    source_wall_ns: int | None,
    source_generated_ns: int | None,
    provider_sequence: str | None,
) -> CapturedInput:
    if type(authority) is not CaptureAuthority:
        raise TypeError("authority: issued CaptureAuthority required")
    checked_content_type = _validated_content_type(authority, content_type)
    value = _parse_json(raw_json)
    _validate_and_redact_json(value, redact=False)
    return _capture_common(
        authority=authority,
        request_id=request_id,
        event_type=event_type,
        event_version=event_version,
        source_wall_ns=source_wall_ns,
        source_generated_ns=source_generated_ns,
        provider_sequence=provider_sequence,
        content_type=checked_content_type,
        payload_encoding="json",
        payload_transform="identity-public-market-v1",
        payload=raw_json,
    )


def capture_redacted_json(
    raw_json: bytes,
    *,
    authority: CaptureAuthority,
    content_type: str,
    request_id: ProvenanceEvidence,
    event_type: str,
    event_version: int,
    source_wall_ns: int | None,
    source_generated_ns: int | None,
    provider_sequence: str | None,
) -> CapturedInput:
    if type(authority) is not CaptureAuthority:
        raise TypeError("authority: issued CaptureAuthority required")
    checked_content_type = _validated_content_type(authority, content_type)
    value = _parse_json(raw_json)
    _validate_and_redact_json(value, redact=True)
    payload = canonical_json_bytes(value)
    return _capture_common(
        authority=authority,
        request_id=request_id,
        event_type=event_type,
        event_version=event_version,
        source_wall_ns=source_wall_ns,
        source_generated_ns=source_generated_ns,
        provider_sequence=provider_sequence,
        content_type=checked_content_type,
        payload_encoding="canonical-json-v1",
        payload_transform="json-secret-redaction-v1",
        payload=payload,
    )


def capture_transport_error(
    *,
    exception_type: str,
    status_code: int | None,
    error_code: str | None,
    request_id: ProvenanceEvidence,
    authority: CaptureAuthority,
    event_type: str,
    event_version: int,
) -> CapturedInput:
    if type(authority) is not CaptureAuthority:
        raise TypeError("authority: issued CaptureAuthority required")
    request = _require_provenance(request_id, "request_id")
    try:
        exception_name = _safe_identifier(exception_type, "exception_type")
        if status_code is not None and (
            type(status_code) is not int or not 100 <= status_code <= 599
        ):
            raise ValueError
        checked_error = (
            None
            if error_code is None
            else _safe_identifier(error_code, "error_code")
        )
    except (TypeError, ValueError):
        raise CaptureValidationError("transport_error_field_invalid") from None
    payload = canonical_json_bytes(
        {
            "exception_type": exception_name,
            "status_code": status_code,
            "error_code": checked_error,
            "request_id": {
                "value": request.value,
                "state": request.state.value,
            },
        }
    )
    return _capture_common(
        authority=authority,
        request_id=request,
        event_type=event_type,
        event_version=event_version,
        source_wall_ns=None,
        source_generated_ns=None,
        provider_sequence=None,
        content_type=TRANSPORT_CONTENT_TYPE,
        payload_encoding="canonical-json-v1",
        payload_transform="sanitized-transport-error-v1",
        payload=payload,
    )
