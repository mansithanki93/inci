"""Session-manifest construction and exhaustive qualification binding."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone
import hashlib

from .canonical import canonical_json_bytes
from .config import TennisV1Config, canonical_config_sha256
from .entitlements import (
    ProviderGateError,
    ProviderManifest,
    QualificationDecision,
    QualificationReason,
    QualifiedProviderBinding,
    canonical_manifest_sha256,
    opaque_id_sha256,
    provider_request_binding_sha256,
)
from .events import SessionManifest


class SessionBindingError(ValueError):
    """Raised when immutable qualification evidence does not bind a session."""


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _datetime_ns(value: datetime, field_name: str) -> int:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise SessionBindingError(f"{field_name}: aware_utc_required")
    delta = value - _EPOCH
    return (
        delta.days * 86_400_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def _projection(manifest: SessionManifest) -> dict[str, object]:
    if type(manifest) is not SessionManifest:
        raise TypeError("session manifest must be SessionManifest")
    return {
        field.name: getattr(manifest, field.name)
        for field in fields(SessionManifest)
    }


def canonical_session_manifest_bytes(manifest: SessionManifest) -> bytes:
    if type(manifest) is not SessionManifest:
        raise TypeError("session manifest must be SessionManifest")
    return canonical_json_bytes(_projection(manifest))


def session_manifest_sha256(manifest: SessionManifest) -> str:
    return hashlib.sha256(canonical_session_manifest_bytes(manifest)).hexdigest()


def _binding_error() -> SessionBindingError:
    return SessionBindingError("qualification_decision_session_binding_mismatch")


def _require_literal_eligibility(decision: QualificationDecision) -> None:
    if decision.eligible is not True:
        raise SessionBindingError(
            "qualification_decision_eligibility_invalid"
        )


def require_decision_matches_session(
    decision: QualificationDecision,
    manifest: SessionManifest,
) -> None:
    if type(decision) is not QualificationDecision or type(manifest) is not SessionManifest:
        raise TypeError("exact qualification decision and session manifest required")
    _require_literal_eligibility(decision)
    try:
        decision.require_eligible()
    except ProviderGateError as error:
        raise SessionBindingError("qualification_decision_ineligible") from error
    binding = decision.binding
    if type(binding) is not QualifiedProviderBinding:
        raise SessionBindingError("qualification_decision_binding_missing")
    try:
        computed_binding_sha = provider_request_binding_sha256(decision)
    except ProviderGateError as error:
        raise SessionBindingError("qualification_decision_binding_invalid") from error
    expected = (
        decision.reasons == (QualificationReason.ELIGIBLE,)
        and decision.provider_request_binding_sha256 == computed_binding_sha
        and decision.manifest_file_sha256
        == manifest.provider_manifest_file_sha256
        and decision.manifest_canonical_sha256
        == manifest.provider_manifest_canonical_sha256
        and decision.request_sha256 == manifest.research_request_sha256
        and binding.provider_id == manifest.provider_id
        and binding.product_tier == manifest.product_tier
        and binding.source_lineage_id == manifest.source_lineage_id
        and binding.entitlement_id_sha256 == manifest.entitlement_id_sha256
        and binding.manifest_file_sha256
        == manifest.provider_manifest_file_sha256
        and binding.manifest_canonical_sha256
        == manifest.provider_manifest_canonical_sha256
        and binding.qualification_artifact_sha256
        == manifest.qualification_artifact_sha256
        and binding.permission_artifact_sha256
        == manifest.permission_artifact_sha256
        and binding.qualification_trace_sha256
        == manifest.qualification_trace_sha256
        and binding.adapter_code_sha256 == manifest.adapter_code_sha256
        and binding.auth_contract_sha256 == manifest.auth_contract_sha256
        and binding.quota_contract_sha256 == manifest.quota_contract_sha256
        and _datetime_ns(binding.session_end_utc, "session_end_utc")
        == manifest.session_end_ns
        and _datetime_ns(
            binding.required_retention_until, "required_retention_until"
        )
        == manifest.required_retention_until_ns
        and _datetime_ns(binding.access_expires_at, "access_expires_at")
        == manifest.access_expires_at_ns
        and _datetime_ns(binding.analysis_expires_at, "analysis_expires_at")
        == manifest.analysis_expires_at_ns
        and manifest.research_evaluable is False
    )
    if not expected:
        raise _binding_error()


def build_session_manifest(
    *,
    config: TennisV1Config,
    provider_manifest: ProviderManifest,
    qualification: QualificationDecision,
    session_id: str,
    created_wall_ns: int,
    code_sha256: str,
) -> SessionManifest:
    if type(config) is not TennisV1Config:
        raise TypeError("config must be TennisV1Config")
    if type(provider_manifest) is not ProviderManifest:
        raise TypeError("provider_manifest must be ProviderManifest")
    if type(qualification) is not QualificationDecision:
        raise TypeError("qualification must be QualificationDecision")
    _require_literal_eligibility(qualification)
    if (
        config.provider_manifest_sha256 != provider_manifest.source_file_sha256
        or config.canonical_sha256 != canonical_config_sha256(config)
        or provider_manifest.canonical_sha256
        != canonical_manifest_sha256(provider_manifest)
    ):
        raise SessionBindingError("verified_input_digest_mismatch")
    try:
        qualification.require_eligible()
    except ProviderGateError as error:
        raise SessionBindingError("qualification_decision_ineligible") from error
    binding = qualification.binding
    if type(binding) is not QualifiedProviderBinding:
        raise SessionBindingError("qualification_decision_binding_missing")
    candidate = SessionManifest(
        schema_version=1,
        session_id=session_id,
        created_wall_ns=created_wall_ns,
        config_file_sha256=config.source_file_sha256,
        config_canonical_sha256=config.canonical_sha256,
        code_sha256=code_sha256,
        research_request_sha256=qualification.request_sha256,
        provider_id=provider_manifest.provider_id,
        product_tier=provider_manifest.product_tier,
        source_lineage_id=provider_manifest.source_lineage_id,
        provider_manifest_file_sha256=provider_manifest.source_file_sha256,
        provider_manifest_canonical_sha256=provider_manifest.canonical_sha256,
        entitlement_id_sha256=opaque_id_sha256(
            provider_manifest.entitlement_id
        ),
        terms_version=provider_manifest.terms_version,
        permission_artifact_sha256=provider_manifest.permission_artifact_sha256,
        qualification_artifact_sha256=(
            provider_manifest.qualification_artifact_sha256
        ),
        qualification_trace_sha256=provider_manifest.qualification_trace_sha256,
        adapter_code_sha256=binding.adapter_code_sha256,
        auth_contract_sha256=binding.auth_contract_sha256,
        quota_contract_sha256=binding.quota_contract_sha256,
        session_end_ns=_datetime_ns(binding.session_end_utc, "session_end_utc"),
        required_retention_until_ns=_datetime_ns(
            binding.required_retention_until, "required_retention_until"
        ),
        access_expires_at_ns=_datetime_ns(
            binding.access_expires_at, "access_expires_at"
        ),
        analysis_expires_at_ns=_datetime_ns(
            binding.analysis_expires_at, "analysis_expires_at"
        ),
        research_evaluable=False,
    )
    require_decision_matches_session(qualification, candidate)
    return candidate
