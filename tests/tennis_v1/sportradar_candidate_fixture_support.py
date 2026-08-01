"""Synthetic-only safe-capture support for pure Task-7 tests."""

from __future__ import annotations

from tennis_v1.capture import (
    capture_public_json,
    capture_redacted_json,
    capture_transport_error,
    issue_capture_authority,
    redacted_provenance,
    safe_provenance,
)
from tennis_v1.events import (
    CaptureAuthority,
    CapturedInput,
    SessionManifest,
    SourceKind,
)


_SUMMARY_EVENT_TYPE = "sportradar_tennis_summary_v3"
_TIMELINE_EVENT_TYPE = "sportradar_tennis_timeline_v3"
_TRANSPORT_EVENT_TYPE = "sportradar_tennis_transport_error_v1"
_TRANSPORT_CONTENT_TYPE = (
    "application/vnd.inci.transport-error+json"
)


class InjectedFixtureCaptureAuthorizerV1:
    """Test-only owner for one synthetic safe-capture authority."""

    __slots__ = (
        "session_manifest",
        "_source_entity_id",
        "_session_start_wall_ns",
        "_authorized",
    )

    def __init__(
        self,
        *,
        session_manifest: SessionManifest,
        source_entity_id: str,
        session_start_wall_ns: int,
    ) -> None:
        if (
            type(session_manifest) is not SessionManifest
            or session_manifest.provider_id != "sportradar"
            or session_manifest.research_evaluable is not False
            or type(source_entity_id) is not str
            or not source_entity_id.startswith("synthetic-")
            or type(session_start_wall_ns) is not int
            or not (
                session_manifest.created_wall_ns
                <= session_start_wall_ns
                < session_manifest.session_end_ns
            )
        ):
            raise TypeError("synthetic fixture capture binding required")
        self.session_manifest = session_manifest
        self._source_entity_id = source_entity_id
        self._session_start_wall_ns = session_start_wall_ns
        self._authorized: CapturedInput | None = None

    def authorize_capture(
        self,
        authority: CaptureAuthority,
        captured: CapturedInput,
    ) -> None:
        manifest = self.session_manifest
        if (
            type(authority) is not CaptureAuthority
            or type(captured) is not CapturedInput
            or authority.session_id != manifest.session_id
            or captured.session_id != manifest.session_id
            or captured.source_kind is not SourceKind.PROVIDER
            or captured.source_id != "sportradar"
            or captured.source_entity_id != self._source_entity_id
            or captured.endpoint_id != "sportradar-api"
            or captured.channel_id != "sportradar-rest"
            or captured.request_id != "<redacted>"
            or captured.connection_epoch != 1
            or captured.retention_delete_by_ns
            != manifest.required_retention_until_ns
            or not (
                self._session_start_wall_ns
                <= captured.local_wall_ns
                < manifest.session_end_ns
            )
        ):
            raise ValueError("synthetic fixture capture denied")
        self._authorized = captured

    def require_returned_identity(
        self,
        captured: CapturedInput,
    ) -> None:
        if self._authorized is not captured:
            raise ValueError("synthetic fixture capture identity mismatch")


def _authority(
    *,
    manifest: SessionManifest,
    source_entity_id: str,
    session_start_wall_ns: int,
    local_wall_ns: int,
    local_monotonic_ns: int,
    clock_uncertainty_ns: int,
    allowed_content_types: tuple[str, ...],
) -> tuple[
    InjectedFixtureCaptureAuthorizerV1,
    CaptureAuthority,
]:
    for value in (
        local_wall_ns,
        local_monotonic_ns,
        clock_uncertainty_ns,
    ):
        if type(value) is not int or value < 0:
            raise TypeError("exact nonnegative fixture clock required")
    if not session_start_wall_ns <= local_wall_ns < manifest.session_end_ns:
        raise ValueError("synthetic fixture clock outside session")
    authorizer = InjectedFixtureCaptureAuthorizerV1(
        session_manifest=manifest,
        source_entity_id=source_entity_id,
        session_start_wall_ns=session_start_wall_ns,
    )
    authority = issue_capture_authority(
        session_authorizer=authorizer,
        source_kind=SourceKind.PROVIDER,
        source_id="sportradar",
        source_entity_id=source_entity_id,
        endpoint=safe_provenance("sportradar-api"),
        channel=safe_provenance("sportradar-rest"),
        connection_epoch=1,
        allowed_content_types=allowed_content_types,
        wall_clock_ns=lambda: local_wall_ns,
        monotonic_clock_ns=lambda: local_monotonic_ns,
        clock_uncertainty_ns=lambda: clock_uncertainty_ns,
    )
    return authorizer, authority


def capture_public_candidate_fixture(
    payload: bytes,
    *,
    manifest: SessionManifest,
    source_entity_id: str,
    session_start_wall_ns: int,
    local_wall_ns: int,
    local_monotonic_ns: int,
    clock_uncertainty_ns: int,
    event_type: str,
    source_wall_ns: int,
    source_generated_ns: int,
    provider_sequence: str,
) -> CapturedInput:
    if event_type not in {_SUMMARY_EVENT_TYPE, _TIMELINE_EVENT_TYPE}:
        raise ValueError("synthetic fixture route denied")
    authorizer, authority = _authority(
        manifest=manifest,
        source_entity_id=source_entity_id,
        session_start_wall_ns=session_start_wall_ns,
        local_wall_ns=local_wall_ns,
        local_monotonic_ns=local_monotonic_ns,
        clock_uncertainty_ns=clock_uncertainty_ns,
        allowed_content_types=("application/json",),
    )
    captured = capture_public_json(
        payload,
        authority=authority,
        content_type="application/json",
        request_id=redacted_provenance(),
        event_type=event_type,
        event_version=1,
        source_wall_ns=source_wall_ns,
        source_generated_ns=source_generated_ns,
        provider_sequence=provider_sequence,
    )
    authorizer.require_returned_identity(captured)
    return captured


def capture_transport_candidate_fixture(
    *,
    manifest: SessionManifest,
    source_entity_id: str,
    session_start_wall_ns: int,
    local_wall_ns: int,
    local_monotonic_ns: int,
    clock_uncertainty_ns: int,
    exception_type: str,
    status_code: int | None,
    error_code: str,
) -> CapturedInput:
    authorizer, authority = _authority(
        manifest=manifest,
        source_entity_id=source_entity_id,
        session_start_wall_ns=session_start_wall_ns,
        local_wall_ns=local_wall_ns,
        local_monotonic_ns=local_monotonic_ns,
        clock_uncertainty_ns=clock_uncertainty_ns,
        allowed_content_types=(_TRANSPORT_CONTENT_TYPE,),
    )
    captured = capture_transport_error(
        exception_type=exception_type,
        status_code=status_code,
        error_code=error_code,
        request_id=redacted_provenance(),
        authority=authority,
        event_type=_TRANSPORT_EVENT_TYPE,
        event_version=1,
    )
    authorizer.require_returned_identity(captured)
    return captured


def capture_redacted_candidate_fixture(
    payload: bytes,
    *,
    manifest: SessionManifest,
    source_entity_id: str,
    session_start_wall_ns: int,
    local_wall_ns: int,
    local_monotonic_ns: int,
    clock_uncertainty_ns: int,
    event_type: str,
    source_wall_ns: int,
    source_generated_ns: int,
    provider_sequence: str,
) -> CapturedInput:
    if event_type not in {_SUMMARY_EVENT_TYPE, _TIMELINE_EVENT_TYPE}:
        raise ValueError("synthetic fixture route denied")
    authorizer, authority = _authority(
        manifest=manifest,
        source_entity_id=source_entity_id,
        session_start_wall_ns=session_start_wall_ns,
        local_wall_ns=local_wall_ns,
        local_monotonic_ns=local_monotonic_ns,
        clock_uncertainty_ns=clock_uncertainty_ns,
        allowed_content_types=("application/json",),
    )
    captured = capture_redacted_json(
        payload,
        authority=authority,
        content_type="application/json",
        request_id=redacted_provenance(),
        event_type=event_type,
        event_version=1,
        source_wall_ns=source_wall_ns,
        source_generated_ns=source_generated_ns,
        provider_sequence=provider_sequence,
    )
    authorizer.require_returned_identity(captured)
    return captured


__all__ = (
    "InjectedFixtureCaptureAuthorizerV1",
    "capture_public_candidate_fixture",
    "capture_redacted_candidate_fixture",
    "capture_transport_candidate_fixture",
)
