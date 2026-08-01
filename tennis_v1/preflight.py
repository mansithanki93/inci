"""Read-only, redacted provider-entitlement diagnostics for Tennis v1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import unicodedata

from .config import TennisV1Config, canonical_config_sha256
from .entitlements import (
    CoverageStratum,
    IntendedUse,
    PermissionArtifact,
    PermissionOperation,
    ProviderManifest,
    QualificationDecision,
    QualificationReason,
    QualificationStatus,
    QualifiedProviderBinding,
    ResearchRequest,
    RequestedStratum,
    _evaluate_provider_as_of,
    _load_provider_manifest_restricted,
    _request_sha256,
    _snapshot_environment,
    canonical_manifest_sha256,
    opaque_id_sha256,
    provider_request_binding_sha256,
)


_PATH_TYPE = type(Path())
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")
_PUBLIC_REASON_VALUES = frozenset(item.value for item in QualificationReason)
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _capture_repository_root() -> Path | None:
    try:
        code_file = Path(__file__).resolve(strict=True)
        repository_root = code_file.parents[1]
        if (
            code_file.name != "preflight.py"
            or code_file.parent.name != "tennis_v1"
            or code_file != repository_root / "tennis_v1" / "preflight.py"
        ):
            return None
        return repository_root
    except Exception:
        return None


_REPOSITORY_ROOT = _capture_repository_root()


class EntitlementPreflightError(RuntimeError):
    """One stable, non-data-bearing failure for unverified preflight input."""

    def __init__(self) -> None:
        super().__init__("entitlement_preflight_failed")


def _safe_identifier(value: object) -> bool:
    return type(value) is str and _SAFE_IDENTIFIER.fullmatch(value) is not None


def _safe_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_HEX.fullmatch(value) is not None


def _exact_utc(value: object) -> bool:
    return type(value) is datetime and value.tzinfo is timezone.utc


@dataclass(frozen=True, slots=True)
class EntitlementPreflight:
    provider_id: str
    product_tier: str
    entitlement_id_sha256: str
    permission_artifact_sha256: str
    qualification_artifact_sha256: str
    qualification_trace_sha256: str
    access_expires_at: datetime
    analysis_expires_at: datetime
    raw_retention_until: datetime
    qualified_until: datetime | None
    planned_session_delete_by_ns: int
    requested_matches: int
    eligible: bool
    reasons: tuple[str, ...]
    export_allowed: bool

    def __post_init__(self) -> None:
        if type(self) is not EntitlementPreflight:
            raise TypeError("exact EntitlementPreflight required")
        if not _safe_identifier(self.provider_id) or not _safe_identifier(
            self.product_tier
        ):
            raise ValueError("preflight identifier invalid")
        for digest in (
            self.entitlement_id_sha256,
            self.permission_artifact_sha256,
            self.qualification_artifact_sha256,
            self.qualification_trace_sha256,
        ):
            if not _safe_sha256(digest):
                raise ValueError("preflight digest invalid")
        for value in (
            self.access_expires_at,
            self.analysis_expires_at,
            self.raw_retention_until,
        ):
            if not _exact_utc(value):
                raise ValueError("preflight datetime invalid")
        if self.qualified_until is not None and not _exact_utc(
            self.qualified_until
        ):
            raise ValueError("preflight qualification datetime invalid")
        if (
            type(self.planned_session_delete_by_ns) is not int
            or type(self.requested_matches) is not int
            or type(self.eligible) is not bool
            or type(self.export_allowed) is not bool
        ):
            raise ValueError("preflight primitive invalid")
        if (
            type(self.reasons) is not tuple
            or not self.reasons
            or any(
                type(reason) is not str
                or reason not in _PUBLIC_REASON_VALUES
                for reason in self.reasons
            )
            or self.reasons != tuple(sorted(set(self.reasons)))
        ):
            raise ValueError("preflight reasons invalid")
        if self.eligible:
            if (
                self.reasons != (QualificationReason.ELIGIBLE.value,)
                or self.qualified_until is None
            ):
                raise ValueError("eligible preflight inconsistent")
        elif (
            QualificationReason.ELIGIBLE.value in self.reasons
            or self.export_allowed
        ):
            raise ValueError("ineligible preflight inconsistent")
        if self.export_allowed and not self.eligible:
            raise ValueError("preflight export inconsistent")


def _lexically_normal_absolute_path(value: object) -> bool:
    if type(value) is not _PATH_TYPE:
        return False
    rendered = str(value)
    return (
        value.is_absolute()
        and ".." not in value.parts
        and os.path.normpath(rendered) == rendered
    )


def _paths_overlap(left: Path, right: Path) -> bool:
    left_parts = tuple(
        unicodedata.normalize("NFC", component).casefold()
        for component in left.parts
    )
    right_parts = tuple(
        unicodedata.normalize("NFC", component).casefold()
        for component in right.parts
    )
    return (
        left_parts[: len(right_parts)] == right_parts
        or right_parts[: len(left_parts)] == left_parts
    )


def _validate_config(config: object, repository_root: Path) -> TennisV1Config:
    if type(config) is not TennisV1Config:
        raise TypeError("config type")
    if type(config.schema_version) is not int or config.schema_version != 1:
        raise TypeError("config schema")
    if not _lexically_normal_absolute_path(
        config.state_root
    ) or not _lexically_normal_absolute_path(config.provider_manifest_path):
        raise TypeError("config path")
    for digest in (
        config.provider_manifest_sha256,
        config.source_file_sha256,
        config.canonical_sha256,
    ):
        if not _safe_sha256(digest):
            raise TypeError("config digest")
    for identifiers in (
        config.trusted_permission_reviewer_ids,
        config.trusted_qualification_issuer_ids,
    ):
        if (
            type(identifiers) is not tuple
            or not 1 <= len(identifiers) <= 64
            or any(not _safe_identifier(item) for item in identifiers)
            or identifiers != tuple(sorted(set(identifiers)))
        ):
            raise TypeError("config trust anchors")
    if (
        type(config.observed_pool_limit) is not int
        or not 1 <= config.observed_pool_limit <= 10
        or type(config.paper_position_limit) is not int
        or not 1 <= config.paper_position_limit <= 3
    ):
        raise TypeError("config limits")
    if canonical_config_sha256(config) != config.canonical_sha256:
        raise TypeError("config canonical digest")
    if _paths_overlap(config.state_root, repository_root) or _paths_overlap(
        config.state_root,
        config.provider_manifest_path,
    ):
        raise TypeError("state root overlap")
    return config


def _validate_request(request: object) -> ResearchRequest:
    if type(request) is not ResearchRequest:
        raise TypeError("request type")
    if type(request.intended_use) is not IntendedUse:
        raise TypeError("request intended use")
    for value in (
        request.now_utc,
        request.session_end_utc,
        request.required_retention_until,
    ):
        if not _exact_utc(value):
            raise TypeError("request datetime")
    for value in (
        request.expiry_safety_margin_seconds,
        request.required_raw_retention_seconds,
        request.requested_matches,
    ):
        if type(value) is not int:
            raise TypeError("request integer")
    if type(request.required_strata) is not tuple:
        raise TypeError("request strata")
    for item in request.required_strata:
        if (
            type(item) is not RequestedStratum
            or type(item.matches) is not int
            or type(item.stratum) is not CoverageStratum
        ):
            raise TypeError("request stratum")
        stratum = item.stratum
        if any(
            type(value) is not str
            for value in (
                stratum.sport,
                stratum.tour,
                stratum.competition_tier,
                stratum.match_format,
                stratum.round_code,
            )
        ):
            raise TypeError("request stratum primitive")
    return request


def _validate_binding_fields(binding: QualifiedProviderBinding) -> None:
    if type(binding) is not QualifiedProviderBinding:
        raise TypeError("decision binding")
    for value in (
        binding.provider_id,
        binding.product_tier,
        binding.source_lineage_id,
    ):
        if not _safe_identifier(value):
            raise TypeError("binding identifier")
    for value in (
        binding.entitlement_id_sha256,
        binding.manifest_file_sha256,
        binding.manifest_canonical_sha256,
        binding.qualification_artifact_sha256,
        binding.permission_artifact_sha256,
        binding.qualification_trace_sha256,
        binding.adapter_code_sha256,
        binding.auth_contract_sha256,
        binding.quota_contract_sha256,
    ):
        if not _safe_sha256(value):
            raise TypeError("binding digest")
    for value in (
        binding.session_end_utc,
        binding.required_retention_until,
        binding.access_expires_at,
        binding.analysis_expires_at,
        binding.qualified_until,
    ):
        if not _exact_utc(value):
            raise TypeError("binding datetime")


def _expected_binding(
    manifest: ProviderManifest,
    request: ResearchRequest,
) -> QualifiedProviderBinding:
    qualification = manifest.qualification
    qualified_until = qualification.qualified_until
    if type(qualified_until) is not datetime or qualified_until.tzinfo is not timezone.utc:
        raise TypeError("eligible qualification deadline")
    return QualifiedProviderBinding(
        provider_id=manifest.provider_id,
        product_tier=manifest.product_tier,
        source_lineage_id=manifest.source_lineage_id,
        entitlement_id_sha256=opaque_id_sha256(manifest.entitlement_id),
        manifest_file_sha256=manifest.source_file_sha256,
        manifest_canonical_sha256=manifest.canonical_sha256,
        qualification_artifact_sha256=manifest.qualification_artifact_sha256,
        permission_artifact_sha256=manifest.permission_artifact_sha256,
        qualification_trace_sha256=manifest.qualification_trace_sha256,
        adapter_code_sha256=qualification.adapter_code_sha256,
        auth_contract_sha256=qualification.auth_contract_sha256,
        quota_contract_sha256=qualification.quota_contract_sha256,
        session_end_utc=request.session_end_utc,
        required_retention_until=request.required_retention_until,
        access_expires_at=manifest.access_expires_at,
        analysis_expires_at=manifest.analysis_expires_at,
        qualified_until=qualified_until,
    )


def _validate_decision(
    config: TennisV1Config,
    manifest: ProviderManifest,
    request: ResearchRequest,
    decision: object,
) -> QualificationDecision:
    if type(manifest) is not ProviderManifest or type(decision) is not QualificationDecision:
        raise TypeError("preflight evaluation type")
    if (
        type(manifest.permission) is not PermissionArtifact
        or type(manifest.permission.permitted_operations) is not tuple
        or any(
            type(operation) is not PermissionOperation
            for operation in manifest.permission.permitted_operations
        )
    ):
        raise TypeError("permission projection")
    if (
        type(decision.eligible) is not bool
        or type(decision.export_allowed) is not bool
        or type(decision.reasons) is not tuple
        or not decision.reasons
        or any(type(reason) is not QualificationReason for reason in decision.reasons)
        or decision.reasons
        != tuple(sorted(set(decision.reasons), key=lambda item: item.value))
    ):
        raise TypeError("decision shape")
    if any(
        reason
        in {
            QualificationReason.ADAPTER_MISMATCH,
            QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
        }
        for reason in decision.reasons
    ):
        raise TypeError("decision verification failure")
    for digest in (
        manifest.source_file_sha256,
        manifest.canonical_sha256,
        decision.manifest_file_sha256,
        decision.manifest_canonical_sha256,
        decision.request_sha256,
    ):
        if not _safe_sha256(digest):
            raise TypeError("decision digest")
    expected_request_sha256 = _request_sha256(request)
    expected_manifest_sha256 = canonical_manifest_sha256(manifest)
    if (
        config.provider_manifest_sha256 != manifest.source_file_sha256
        or manifest.canonical_sha256 != expected_manifest_sha256
        or decision.manifest_file_sha256 != manifest.source_file_sha256
        or decision.manifest_canonical_sha256 != expected_manifest_sha256
        or decision.request_sha256 != expected_request_sha256
    ):
        raise TypeError("decision source binding")
    if decision.eligible:
        if (
            decision.reasons != (QualificationReason.ELIGIBLE,)
            or decision.provider_request_binding_sha256 is None
            or not _safe_sha256(decision.provider_request_binding_sha256)
            or type(decision.binding) is not QualifiedProviderBinding
        ):
            raise TypeError("eligible decision shape")
        _validate_binding_fields(decision.binding)
        expected_binding = _expected_binding(manifest, request)
        if decision.binding != expected_binding:
            raise TypeError("eligible decision binding")
        if (
            provider_request_binding_sha256(decision)
            != decision.provider_request_binding_sha256
        ):
            raise TypeError("eligible decision digest")
    elif (
        QualificationReason.ELIGIBLE in decision.reasons
        or decision.provider_request_binding_sha256 is not None
        or decision.binding is not None
        or decision.export_allowed
    ):
        raise TypeError("ineligible decision shape")
    expected_export = (
        decision.eligible
        and PermissionOperation.PUBLICATION
        in manifest.permission.permitted_operations
    )
    if decision.export_allowed is not expected_export:
        raise TypeError("decision export")
    return decision


def _planned_delete_by_ns(value: datetime) -> int:
    delta = value - _EPOCH_UTC
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000
        + delta.microseconds
    ) * 1_000


def _run_preflight(
    config: object,
    request: object,
    environ: object,
) -> EntitlementPreflight:
    repository_root = _REPOSITORY_ROOT
    if type(repository_root) is not _PATH_TYPE:
        raise TypeError("repository root")
    checked_config = _validate_config(config, repository_root)
    checked_request = _validate_request(request)
    snapshot = _snapshot_environment(environ)
    manifest = _load_provider_manifest_restricted(
        checked_config.provider_manifest_path,
        expected_sha256=checked_config.provider_manifest_sha256,
        repo_root=repository_root,
        forbidden_root=checked_config.state_root,
    )
    decision = _evaluate_provider_as_of(
        checked_config,
        manifest,
        checked_request,
        environ=snapshot,
        as_of=checked_request.now_utc,
    )
    checked_decision = _validate_decision(
        checked_config,
        manifest,
        checked_request,
        decision,
    )
    qualification = manifest.qualification
    if type(qualification.status) is not QualificationStatus:
        raise TypeError("qualification status")
    qualified_until = qualification.qualified_until
    if qualification.status is QualificationStatus.PASSED:
        if not _exact_utc(qualified_until):
            raise TypeError("qualification deadline")
    elif qualified_until is not None:
        raise TypeError("non-passed qualification deadline")
    return EntitlementPreflight(
        provider_id=manifest.provider_id,
        product_tier=manifest.product_tier,
        entitlement_id_sha256=opaque_id_sha256(manifest.entitlement_id),
        permission_artifact_sha256=manifest.permission_artifact_sha256,
        qualification_artifact_sha256=manifest.qualification_artifact_sha256,
        qualification_trace_sha256=manifest.qualification_trace_sha256,
        access_expires_at=manifest.access_expires_at,
        analysis_expires_at=manifest.analysis_expires_at,
        raw_retention_until=manifest.raw_retention_until,
        qualified_until=qualified_until,
        planned_session_delete_by_ns=_planned_delete_by_ns(
            checked_request.required_retention_until
        ),
        requested_matches=checked_request.requested_matches,
        eligible=checked_decision.eligible,
        reasons=tuple(reason.value for reason in checked_decision.reasons),
        export_allowed=checked_decision.export_allowed,
    )


def run_entitlement_preflight(
    config: TennisV1Config,
    request: ResearchRequest,
    *,
    environ: dict[str, str],
) -> EntitlementPreflight:
    failed = False
    result: EntitlementPreflight | None = None
    try:
        result = _run_preflight(config, request, environ)
    except Exception:
        failed = True
    if failed or type(result) is not EntitlementPreflight:
        raise EntitlementPreflightError() from None
    return result
