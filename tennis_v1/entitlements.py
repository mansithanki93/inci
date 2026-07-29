"""Strict, pinned provider entitlement artifacts for Tennis v1."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import hmac
import hashlib
import json
from pathlib import Path
import re
from typing import Callable
from urllib.parse import urlsplit

from .adapter_contract import (
    AdapterContract,
    AdapterContractError,
    AdapterUsagePlan,
    AuthContract,
    AuthMode,
    ProviderQuotas,
    derive_quota_demand,
    load_active_adapter_contract,
)
from .canonical import canonical_json_bytes
from .config import TennisV1Config
from .pinned_file import PinnedFileError, read_pinned_file
from .qualification_protocol import (
    QUALIFICATION_PROTOCOL_V1,
    qualification_protocol_sha256,
)


JSON_MAX_BYTES = 64 * 1024
PERMISSION_EVIDENCE_MAX_BYTES = 4 * 1024 * 1024
QUALIFICATION_TRACE_MAX_BYTES = 64 * 1024 * 1024
FULL_UTC_RE = re.compile(
    r"(?:[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z|"
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z)\Z"
)
SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")
SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")
TOUR_COMPONENT = re.compile(r"[A-Z][A-Z0-9-]{0,31}\Z")
FORMAT_COMPONENT = re.compile(r"[A-Z][A-Z0-9_]{0,31}\Z")
SECRET_KEY = re.compile(
    r"(?:authorization|cookie|token|secret|password|privatekey|signature|credential|apikey)"
)
ALLOWED_FORMATS = frozenset({"rest_json", "websocket_json", "ndjson"})
TERMS_URL_PATTERN = (
    r"^https://(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:/(?:[A-Za-z0-9._~!$&'()*+,;=:@/-]|%[0-9A-Fa-f]{2})*)?$"
)
TERMS_URL_RE = re.compile(TERMS_URL_PATTERN)

MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "provider_id",
        "product_tier",
        "entitlement_id",
        "source_lineage_id",
        "terms_url",
        "terms_version",
        "permission",
        "billing_mode",
        "auto_renew",
        "access_starts_at",
        "access_expires_at",
        "analysis_expires_at",
        "raw_retention_until",
        "max_raw_retention_seconds",
        "credential_env_names",
        "quotas",
        "capabilities",
        "qualification",
    }
)
PERMISSION_REFERENCE_KEYS = frozenset(
    {"artifact_path", "artifact_sha256", "evidence_path", "evidence_sha256"}
)
QUALIFICATION_REFERENCE_KEYS = frozenset(
    {
        "artifact_path",
        "artifact_sha256",
        "evidence_trace_path",
        "evidence_trace_sha256",
    }
)
QUOTA_KEYS = frozenset(ProviderQuotas.__dataclass_fields__)
CAPABILITY_KEYS = frozenset(
    {
        "stable_match_ids",
        "stable_player_ids",
        "point_state",
        "current_server",
        "match_format",
        "source_event_time",
        "provider_generated_time",
        "monotonic_sequence_or_revision",
        "correction_semantics",
        "resync_snapshot",
        "supported_formats",
        "declared_strata",
    }
)
BOOLEAN_CAPABILITY_KEYS = frozenset(
    CAPABILITY_KEYS - {"supported_formats", "declared_strata"}
)
REQUIRED_QUALIFICATION_CAPABILITIES = frozenset(
    QUALIFICATION_PROTOCOL_V1["required_capabilities"]
)
STRATUM_KEYS = frozenset(
    {"sport", "tour", "competition_tier", "match_format", "round_code"}
)
PERMISSION_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "provider_id",
        "product_tier",
        "entitlement_id_sha256",
        "terms_version",
        "basis",
        "intended_use",
        "permitted_operations",
        "access_starts_at",
        "access_expires_at",
        "analysis_expires_at",
        "raw_retention_until",
        "reviewed_at",
        "reviewer_id",
        "approval_id",
        "evidence_document_sha256",
    }
)
QUALIFICATION_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "provider_id",
        "product_tier",
        "source_lineage_id",
        "adapter_code_sha256",
        "auth_contract_sha256",
        "quota_contract_sha256",
        "qualification_protocol_sha256",
        "evidence_trace_sha256",
        "issuer_id",
        "approval_id",
        "issued_at",
        "status",
        "qualified_at",
        "qualified_until",
        "observed_matches",
        "simultaneous_matches_tested",
        "strata",
    }
)
QUALIFIED_STRATUM_KEYS = frozenset(
    {
        "stratum",
        "observed_matches",
        "simultaneous_matches_tested",
        "tested_formats",
        "tested_capabilities",
    }
)
QUALIFICATION_TRACE_KEYS = frozenset(
    {
        "schema_version",
        "provider_id",
        "product_tier",
        "source_lineage_id",
        "adapter_code_sha256",
        "auth_contract_sha256",
        "quota_contract_sha256",
        "qualification_protocol_sha256",
        "started_at",
        "completed_at",
        "matches",
        "clean_terminal",
    }
)
QUALIFICATION_TRACE_MATCH_KEYS = frozenset(
    {
        "match_id_sha256",
        "stratum",
        "tested_format",
        "started_at",
        "ended_at",
        "tested_capabilities",
    }
)


class ManifestError(ValueError):
    """Raised when a provider artifact cannot satisfy the strict v1 contract."""


class IntendedUse(str, Enum):
    PRIVATE_PAPER_EVALUATION = "private_paper_evaluation"


class PermissionBasis(str, Enum):
    TRIAL_TERMS = "trial_terms"
    WRITTEN_PERMISSION = "written_permission"


class PermissionOperation(str, Enum):
    PROVIDER_INGEST = "provider_ingest"
    RAW_RETENTION = "raw_retention"
    DERIVED_SIGNALS = "derived_signals"
    POST_EXPIRY_ANALYSIS = "post_expiry_analysis"
    PUBLICATION = "publication"


class BillingMode(str, Enum):
    TRIAL = "trial"
    PAID = "paid"


class QualificationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNTESTED = "untested"


@dataclass(frozen=True, slots=True)
class CoverageStratum:
    sport: str
    tour: str
    competition_tier: str
    match_format: str
    round_code: str


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    stable_match_ids: bool
    stable_player_ids: bool
    point_state: bool
    current_server: bool
    match_format: bool
    source_event_time: bool
    provider_generated_time: bool
    monotonic_sequence_or_revision: bool
    correction_semantics: bool
    resync_snapshot: bool
    supported_formats: tuple[str, ...]
    declared_strata: tuple[CoverageStratum, ...]


@dataclass(frozen=True, slots=True)
class QualifiedStratumEvidence:
    stratum: CoverageStratum
    observed_matches: int
    simultaneous_matches_tested: int
    tested_formats: tuple[str, ...]
    tested_capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualificationArtifact:
    schema_version: int
    provider_id: str
    product_tier: str
    source_lineage_id: str
    adapter_code_sha256: str
    auth_contract_sha256: str
    quota_contract_sha256: str
    qualification_protocol_sha256: str
    evidence_trace_sha256: str
    issuer_id: str
    approval_id: str
    issued_at: datetime
    status: QualificationStatus
    qualified_at: datetime | None
    qualified_until: datetime | None
    observed_matches: int
    simultaneous_matches_tested: int
    strata: tuple[QualifiedStratumEvidence, ...]


@dataclass(frozen=True, slots=True)
class PermissionArtifact:
    schema_version: int
    provider_id: str
    product_tier: str
    entitlement_id_sha256: str
    terms_version: str
    basis: PermissionBasis
    intended_use: IntendedUse
    permitted_operations: tuple[PermissionOperation, ...]
    access_starts_at: datetime
    access_expires_at: datetime
    analysis_expires_at: datetime
    raw_retention_until: datetime
    reviewed_at: datetime
    reviewer_id: str
    approval_id: str
    evidence_document_sha256: str


@dataclass(frozen=True, slots=True)
class QualificationTraceMatch:
    match_id_sha256: str
    stratum: CoverageStratum
    tested_format: str
    started_at: datetime
    ended_at: datetime
    tested_capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualificationTrace:
    schema_version: int
    provider_id: str
    product_tier: str
    source_lineage_id: str
    adapter_code_sha256: str
    auth_contract_sha256: str
    quota_contract_sha256: str
    qualification_protocol_sha256: str
    started_at: datetime
    completed_at: datetime
    matches: tuple[QualificationTraceMatch, ...]
    clean_terminal: bool
    source_file_sha256: str


@dataclass(frozen=True, slots=True)
class ProviderManifest:
    schema_version: int
    provider_id: str
    product_tier: str
    entitlement_id: str = field(repr=False)
    source_lineage_id: str
    terms_url: str
    terms_version: str
    permission_artifact_path: Path = field(repr=False)
    permission_artifact_sha256: str
    permission_evidence_path: Path = field(repr=False)
    permission_evidence_sha256: str
    permission: PermissionArtifact
    billing_mode: BillingMode
    auto_renew: bool
    access_starts_at: datetime
    access_expires_at: datetime
    analysis_expires_at: datetime
    raw_retention_until: datetime
    max_raw_retention_seconds: int
    credential_env_names: tuple[str, ...]
    quotas: ProviderQuotas
    capabilities: ProviderCapabilities
    qualification_artifact_path: Path = field(repr=False)
    qualification_artifact_sha256: str
    qualification_trace_path: Path = field(repr=False)
    qualification_trace_sha256: str
    qualification: QualificationArtifact
    source_file_sha256: str
    canonical_sha256: str


def format_utc(value: datetime) -> str:
    if value.microsecond:
        return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or FULL_UTC_RE.fullmatch(value) is None:
        raise ManifestError(f"{field_name}: invalid_utc_timestamp")
    try:
        fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in value else "%Y-%m-%dT%H:%M:%SZ"
        parsed = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
    except ValueError:
        raise ManifestError(f"{field_name}: invalid_utc_timestamp") from None
    if format_utc(parsed) != value:
        raise ManifestError(f"{field_name}: noncanonical_utc_timestamp")
    return parsed


def _optional_utc(value: object, field_name: str) -> datetime | None:
    return None if value is None else _utc(value, field_name)


def _safe_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ManifestError(f"{field_name}: invalid_identifier")
    return value


def _bounded_string(
    value: object, field_name: str, *, maximum: int = 256
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ManifestError(f"{field_name}: invalid_string")
    return value


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or SHA256_HEX.fullmatch(value) is None:
        raise ManifestError(f"{field_name}: invalid_sha256")
    return value


def _safe_sha256_equal(left: object, right: object) -> bool:
    if type(left) is not str or type(right) is not str:
        return False
    try:
        return (
            SHA256_HEX.fullmatch(left) is not None
            and SHA256_HEX.fullmatch(right) is not None
            and hmac.compare_digest(left, right)
        )
    except Exception:
        return False


def _integer(
    value: object, field_name: str, *, positive: bool
) -> int:
    minimum = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        kind = "positive" if positive else "nonnegative"
        raise ManifestError(f"{field_name}: invalid_{kind}_integer")
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ManifestError(f"{field_name}: invalid_boolean")
    return value


def _exact_object(
    value: object, expected: frozenset[str], field_name: str
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ManifestError(f"{field_name}: schema_keys_mismatch")
    return value


def _enum(value: object, enum_type: type[Enum], field_name: str):
    if not isinstance(value, str):
        raise ManifestError(f"{field_name}: invalid_enum")
    try:
        return enum_type(value)
    except ValueError:
        raise ManifestError(f"{field_name}: invalid_enum") from None


def _reject_float(_: str) -> object:
    raise ManifestError("artifact: floating_point_not_permitted")


def _reject_constant(_: str) -> object:
    raise ManifestError("artifact: nonstandard_constant_not_permitted")


def _duplicate_free_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("artifact: duplicate_json_key")
        result[key] = value
    return result


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _reject_secret_shaped_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                key != "credential_env_names"
                and SECRET_KEY.search(_normalized_key(key)) is not None
            ):
                raise ManifestError("artifact: forbidden_secret_shaped_key")
            _reject_secret_shaped_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_secret_shaped_keys(item)


def _parse_json(content: bytes, artifact_name: str) -> dict[str, object]:
    if content.startswith(b"\xef\xbb\xbf"):
        raise ManifestError(f"{artifact_name}: utf8_bom_not_permitted")
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ManifestError(f"{artifact_name}: invalid_utf8") from None
    try:
        value = json.loads(
            decoded,
            object_pairs_hook=_duplicate_free_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except ManifestError:
        raise
    except (json.JSONDecodeError, ValueError):
        raise ManifestError(f"{artifact_name}: invalid_strict_json") from None
    if not isinstance(value, dict):
        raise ManifestError(f"{artifact_name}: top_level_object_required")
    _reject_secret_shaped_keys(value)
    return value


def _read_json(
    path: str | Path,
    *,
    expected_sha256: str,
    repo_root: str | Path,
    max_bytes: int,
    artifact_name: str,
    forbidden_root: Path | None = None,
) -> tuple[dict[str, object], str]:
    _sha256(expected_sha256, f"{artifact_name}_expected_sha256")
    try:
        pinned = read_pinned_file(
            path,
            expected_sha256=expected_sha256,
            repo_root=repo_root,
            max_bytes=max_bytes,
            forbidden_root=forbidden_root,
        )
    except PinnedFileError:
        raise ManifestError(f"{artifact_name}: immutable_load_failed") from None
    return _parse_json(pinned.data, artifact_name), pinned.sha256


def _read_evidence(
    path: str | Path,
    *,
    expected_sha256: str,
    repo_root: str | Path,
    forbidden_root: Path | None = None,
) -> str:
    _sha256(expected_sha256, "permission_evidence_expected_sha256")
    try:
        pinned = read_pinned_file(
            path,
            expected_sha256=expected_sha256,
            repo_root=repo_root,
            max_bytes=PERMISSION_EVIDENCE_MAX_BYTES,
            forbidden_root=forbidden_root,
        )
    except PinnedFileError:
        raise ManifestError("permission_evidence: immutable_load_failed") from None
    return pinned.sha256


def _stratum(value: object, field_name: str) -> CoverageStratum:
    raw = _exact_object(value, STRATUM_KEYS, field_name)
    sport = raw["sport"]
    tour = raw["tour"]
    tier = raw["competition_tier"]
    match_format = raw["match_format"]
    round_code = raw["round_code"]
    if sport != "tennis":
        raise ManifestError(f"{field_name}: invalid_sport")
    if not isinstance(tour, str) or TOUR_COMPONENT.fullmatch(tour) is None:
        raise ManifestError(f"{field_name}: invalid_tour")
    if not isinstance(tier, str) or TOUR_COMPONENT.fullmatch(tier) is None:
        raise ManifestError(f"{field_name}: invalid_competition_tier")
    if (
        not isinstance(match_format, str)
        or FORMAT_COMPONENT.fullmatch(match_format) is None
    ):
        raise ManifestError(f"{field_name}: invalid_match_format")
    if not isinstance(round_code, str) or FORMAT_COMPONENT.fullmatch(round_code) is None:
        raise ManifestError(f"{field_name}: invalid_round_code")
    if any(
        component.lower() == "unknown"
        for component in (tour, tier, match_format, round_code)
    ):
        raise ManifestError(f"{field_name}: unknown_component_not_permitted")
    return CoverageStratum(
        sport="tennis",
        tour=tour,
        competition_tier=tier,
        match_format=match_format,
        round_code=round_code,
    )


def _stratum_key(value: CoverageStratum) -> tuple[str, str, str, str, str]:
    return (
        value.sport,
        value.tour,
        value.competition_tier,
        value.match_format,
        value.round_code,
    )


def _unique_strings(
    value: object,
    field_name: str,
    *,
    allowed: frozenset[str],
    nonempty: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ManifestError(f"{field_name}: invalid_list")
    if any(not isinstance(item, str) or item not in allowed for item in value):
        raise ManifestError(f"{field_name}: invalid_value")
    if len(set(value)) != len(value):
        raise ManifestError(f"{field_name}: duplicate_value")
    return tuple(sorted(value))


def _parse_capabilities(value: object) -> ProviderCapabilities:
    raw = _exact_object(value, CAPABILITY_KEYS, "capabilities")
    booleans = {
        name: _boolean(raw[name], f"capabilities.{name}")
        for name in BOOLEAN_CAPABILITY_KEYS
    }
    formats = _unique_strings(
        raw["supported_formats"],
        "capabilities.supported_formats",
        allowed=ALLOWED_FORMATS,
    )
    strata_raw = raw["declared_strata"]
    if not isinstance(strata_raw, list) or not strata_raw:
        raise ManifestError("capabilities.declared_strata: invalid_list")
    strata = tuple(
        sorted(
            (
                _stratum(item, f"capabilities.declared_strata[{index}]")
                for index, item in enumerate(strata_raw)
            ),
            key=_stratum_key,
        )
    )
    if len(set(strata)) != len(strata):
        raise ManifestError("capabilities.declared_strata: duplicate_value")
    return ProviderCapabilities(
        **booleans,
        supported_formats=formats,
        declared_strata=strata,
    )


def _parse_quotas(value: object) -> ProviderQuotas:
    raw = _exact_object(value, QUOTA_KEYS, "quotas")
    return ProviderQuotas(
        **{
            name: _integer(raw[name], f"quotas.{name}", positive=True)
            for name in ProviderQuotas.__dataclass_fields__
        }
    )


def _parse_permission(raw_value: object) -> PermissionArtifact:
    raw = _exact_object(
        raw_value, PERMISSION_ARTIFACT_KEYS, "permission_artifact"
    )
    if raw["schema_version"] != 1 or isinstance(raw["schema_version"], bool):
        raise ManifestError("permission_artifact: unsupported_schema_version")
    basis = _enum(raw["basis"], PermissionBasis, "permission_artifact.basis")
    intended_use = _enum(
        raw["intended_use"], IntendedUse, "permission_artifact.intended_use"
    )
    operations = _unique_strings(
        raw["permitted_operations"],
        "permission_artifact.permitted_operations",
        allowed=frozenset(member.value for member in PermissionOperation),
    )
    operation_enums = tuple(PermissionOperation(value) for value in operations)
    base = {
        PermissionOperation.PROVIDER_INGEST,
        PermissionOperation.RAW_RETENTION,
        PermissionOperation.DERIVED_SIGNALS,
    }
    operation_set = set(operation_enums)
    if basis is PermissionBasis.TRIAL_TERMS and operation_set != base:
        raise ManifestError("permission_artifact: invalid_trial_operations")
    if basis is PermissionBasis.WRITTEN_PERMISSION and (
        not base.issubset(operation_set)
        or not operation_set.issubset(set(PermissionOperation))
    ):
        raise ManifestError("permission_artifact: invalid_written_operations")
    access_start = _utc(
        raw["access_starts_at"], "permission_artifact.access_starts_at"
    )
    access_end = _utc(
        raw["access_expires_at"], "permission_artifact.access_expires_at"
    )
    analysis_end = _utc(
        raw["analysis_expires_at"], "permission_artifact.analysis_expires_at"
    )
    retention_end = _utc(
        raw["raw_retention_until"], "permission_artifact.raw_retention_until"
    )
    if not access_start < access_end <= analysis_end <= retention_end:
        raise ManifestError("permission_artifact: invalid_time_window")
    has_post_expiry = PermissionOperation.POST_EXPIRY_ANALYSIS in operation_set
    if basis is PermissionBasis.TRIAL_TERMS and analysis_end != access_end:
        raise ManifestError("permission_artifact: trial_analysis_window_mismatch")
    if (analysis_end > access_end) != has_post_expiry:
        raise ManifestError("permission_artifact: post_expiry_grant_mismatch")
    return PermissionArtifact(
        schema_version=1,
        provider_id=_safe_id(raw["provider_id"], "permission_artifact.provider_id"),
        product_tier=_safe_id(
            raw["product_tier"], "permission_artifact.product_tier"
        ),
        entitlement_id_sha256=_sha256(
            raw["entitlement_id_sha256"],
            "permission_artifact.entitlement_id_sha256",
        ),
        terms_version=_safe_id(
            raw["terms_version"], "permission_artifact.terms_version"
        ),
        basis=basis,
        intended_use=intended_use,
        permitted_operations=operation_enums,
        access_starts_at=access_start,
        access_expires_at=access_end,
        analysis_expires_at=analysis_end,
        raw_retention_until=retention_end,
        reviewed_at=_utc(raw["reviewed_at"], "permission_artifact.reviewed_at"),
        reviewer_id=_safe_id(
            raw["reviewer_id"], "permission_artifact.reviewer_id"
        ),
        approval_id=_safe_id(
            raw["approval_id"], "permission_artifact.approval_id"
        ),
        evidence_document_sha256=_sha256(
            raw["evidence_document_sha256"],
            "permission_artifact.evidence_document_sha256",
        ),
    )


def load_permission_artifact(
    path: str | Path,
    *,
    expected_sha256: str,
    evidence_path: str | Path,
    expected_evidence_sha256: str,
    repo_root: str | Path,
    _forbidden_root: Path | None = None,
) -> PermissionArtifact:
    raw, _ = _read_json(
        path,
        expected_sha256=expected_sha256,
        repo_root=repo_root,
        max_bytes=JSON_MAX_BYTES,
        artifact_name="permission_artifact",
        forbidden_root=_forbidden_root,
    )
    evidence_sha = _read_evidence(
        evidence_path,
        expected_sha256=expected_evidence_sha256,
        repo_root=repo_root,
        forbidden_root=_forbidden_root,
    )
    artifact = _parse_permission(raw)
    if artifact.evidence_document_sha256 != evidence_sha:
        raise ManifestError("permission_artifact: evidence_digest_mismatch")
    return artifact


def _parse_trace_match(value: object, index: int) -> QualificationTraceMatch:
    field_name = f"qualification_trace.matches[{index}]"
    raw = _exact_object(value, QUALIFICATION_TRACE_MATCH_KEYS, field_name)
    started = _utc(raw["started_at"], f"{field_name}.started_at")
    ended = _utc(raw["ended_at"], f"{field_name}.ended_at")
    if started >= ended:
        raise ManifestError(f"{field_name}: invalid_interval")
    return QualificationTraceMatch(
        match_id_sha256=_sha256(
            raw["match_id_sha256"], f"{field_name}.match_id_sha256"
        ),
        stratum=_stratum(raw["stratum"], f"{field_name}.stratum"),
        tested_format=_unique_strings(
            [raw["tested_format"]],
            f"{field_name}.tested_format",
            allowed=ALLOWED_FORMATS,
        )[0],
        started_at=started,
        ended_at=ended,
        tested_capabilities=_unique_strings(
            raw["tested_capabilities"],
            f"{field_name}.tested_capabilities",
            allowed=BOOLEAN_CAPABILITY_KEYS,
        ),
    )


def _parse_trace(raw_value: object, source_sha256: str) -> QualificationTrace:
    raw = _exact_object(
        raw_value, QUALIFICATION_TRACE_KEYS, "qualification_trace"
    )
    if raw["schema_version"] != 1 or isinstance(raw["schema_version"], bool):
        raise ManifestError("qualification_trace: unsupported_schema_version")
    started = _utc(raw["started_at"], "qualification_trace.started_at")
    completed = _utc(raw["completed_at"], "qualification_trace.completed_at")
    if started >= completed:
        raise ManifestError("qualification_trace: invalid_interval")
    raw_matches = raw["matches"]
    if not isinstance(raw_matches, list):
        raise ManifestError("qualification_trace.matches: invalid_list")
    matches = tuple(
        sorted(
            (
                _parse_trace_match(item, index)
                for index, item in enumerate(raw_matches)
            ),
            key=lambda item: item.match_id_sha256,
        )
    )
    if len({item.match_id_sha256 for item in matches}) != len(matches):
        raise ManifestError("qualification_trace.matches: duplicate_match_hash")
    if any(
        item.started_at < started or item.ended_at > completed for item in matches
    ):
        raise ManifestError("qualification_trace.matches: outside_trace_window")
    if not _boolean(raw["clean_terminal"], "qualification_trace.clean_terminal"):
        raise ManifestError("qualification_trace: unclean_terminal")
    return QualificationTrace(
        schema_version=1,
        provider_id=_safe_id(raw["provider_id"], "qualification_trace.provider_id"),
        product_tier=_safe_id(
            raw["product_tier"], "qualification_trace.product_tier"
        ),
        source_lineage_id=_safe_id(
            raw["source_lineage_id"], "qualification_trace.source_lineage_id"
        ),
        adapter_code_sha256=_sha256(
            raw["adapter_code_sha256"],
            "qualification_trace.adapter_code_sha256",
        ),
        auth_contract_sha256=_sha256(
            raw["auth_contract_sha256"],
            "qualification_trace.auth_contract_sha256",
        ),
        quota_contract_sha256=_sha256(
            raw["quota_contract_sha256"],
            "qualification_trace.quota_contract_sha256",
        ),
        qualification_protocol_sha256=_sha256(
            raw["qualification_protocol_sha256"],
            "qualification_trace.qualification_protocol_sha256",
        ),
        started_at=started,
        completed_at=completed,
        matches=matches,
        clean_terminal=True,
        source_file_sha256=source_sha256,
    )


def load_qualification_trace(
    path: str | Path,
    *,
    expected_sha256: str,
    repo_root: str | Path,
    _forbidden_root: Path | None = None,
) -> QualificationTrace:
    raw, source_sha = _read_json(
        path,
        expected_sha256=expected_sha256,
        repo_root=repo_root,
        max_bytes=QUALIFICATION_TRACE_MAX_BYTES,
        artifact_name="qualification_trace",
        forbidden_root=_forbidden_root,
    )
    trace = _parse_trace(raw, source_sha)
    if trace.qualification_protocol_sha256 != qualification_protocol_sha256():
        raise ManifestError("qualification_trace: protocol_binding_mismatch")
    try:
        adapter = load_active_adapter_contract(
            provider_id=trace.provider_id,
            product_tier=trace.product_tier,
        )
    except AdapterContractError:
        raise ManifestError("qualification_trace: active_adapter_invalid") from None
    if (
        trace.adapter_code_sha256 != adapter.adapter_code_sha256
        or trace.auth_contract_sha256 != adapter.auth_contract_sha256
        or trace.quota_contract_sha256 != adapter.quota_contract_sha256
    ):
        raise ManifestError("qualification_trace: adapter_binding_mismatch")
    return trace


def _parse_qualified_stratum(
    value: object, index: int
) -> QualifiedStratumEvidence:
    field_name = f"qualification_artifact.strata[{index}]"
    raw = _exact_object(value, QUALIFIED_STRATUM_KEYS, field_name)
    return QualifiedStratumEvidence(
        stratum=_stratum(raw["stratum"], f"{field_name}.stratum"),
        observed_matches=_integer(
            raw["observed_matches"], f"{field_name}.observed_matches", positive=True
        ),
        simultaneous_matches_tested=_integer(
            raw["simultaneous_matches_tested"],
            f"{field_name}.simultaneous_matches_tested",
            positive=True,
        ),
        tested_formats=_unique_strings(
            raw["tested_formats"],
            f"{field_name}.tested_formats",
            allowed=ALLOWED_FORMATS,
        ),
        tested_capabilities=_unique_strings(
            raw["tested_capabilities"],
            f"{field_name}.tested_capabilities",
            allowed=BOOLEAN_CAPABILITY_KEYS,
        ),
    )


def _parse_qualification(raw_value: object) -> QualificationArtifact:
    raw = _exact_object(
        raw_value, QUALIFICATION_ARTIFACT_KEYS, "qualification_artifact"
    )
    if raw["schema_version"] != 1 or isinstance(raw["schema_version"], bool):
        raise ManifestError("qualification_artifact: unsupported_schema_version")
    status = _enum(
        raw["status"], QualificationStatus, "qualification_artifact.status"
    )
    qualified_at = _optional_utc(
        raw["qualified_at"], "qualification_artifact.qualified_at"
    )
    qualified_until = _optional_utc(
        raw["qualified_until"], "qualification_artifact.qualified_until"
    )
    observed = _integer(
        raw["observed_matches"],
        "qualification_artifact.observed_matches",
        positive=False,
    )
    capacity = _integer(
        raw["simultaneous_matches_tested"],
        "qualification_artifact.simultaneous_matches_tested",
        positive=False,
    )
    raw_strata = raw["strata"]
    if not isinstance(raw_strata, list):
        raise ManifestError("qualification_artifact.strata: invalid_list")
    strata = tuple(
        sorted(
            (
                _parse_qualified_stratum(item, index)
                for index, item in enumerate(raw_strata)
            ),
            key=lambda item: _stratum_key(item.stratum),
        )
    )
    if len({item.stratum for item in strata}) != len(strata):
        raise ManifestError("qualification_artifact.strata: duplicate_stratum")
    issued_at = _utc(raw["issued_at"], "qualification_artifact.issued_at")
    if status is QualificationStatus.PASSED:
        if (
            qualified_at is None
            or qualified_until is None
            or observed <= 0
            or capacity <= 0
            or not strata
            or not qualified_at <= issued_at < qualified_until
            or qualified_until > qualified_at + timedelta(days=30)
            or any(
                set(item.tested_capabilities)
                != REQUIRED_QUALIFICATION_CAPABILITIES
                for item in strata
            )
        ):
            raise ManifestError("qualification_artifact: invalid_passed_summary")
    elif (
        qualified_at is not None
        or qualified_until is not None
        or observed != 0
        or capacity != 0
        or strata
    ):
        raise ManifestError("qualification_artifact: invalid_nonpassed_summary")
    return QualificationArtifact(
        schema_version=1,
        provider_id=_safe_id(
            raw["provider_id"], "qualification_artifact.provider_id"
        ),
        product_tier=_safe_id(
            raw["product_tier"], "qualification_artifact.product_tier"
        ),
        source_lineage_id=_safe_id(
            raw["source_lineage_id"],
            "qualification_artifact.source_lineage_id",
        ),
        adapter_code_sha256=_sha256(
            raw["adapter_code_sha256"],
            "qualification_artifact.adapter_code_sha256",
        ),
        auth_contract_sha256=_sha256(
            raw["auth_contract_sha256"],
            "qualification_artifact.auth_contract_sha256",
        ),
        quota_contract_sha256=_sha256(
            raw["quota_contract_sha256"],
            "qualification_artifact.quota_contract_sha256",
        ),
        qualification_protocol_sha256=_sha256(
            raw["qualification_protocol_sha256"],
            "qualification_artifact.qualification_protocol_sha256",
        ),
        evidence_trace_sha256=_sha256(
            raw["evidence_trace_sha256"],
            "qualification_artifact.evidence_trace_sha256",
        ),
        issuer_id=_safe_id(
            raw["issuer_id"], "qualification_artifact.issuer_id"
        ),
        approval_id=_safe_id(
            raw["approval_id"], "qualification_artifact.approval_id"
        ),
        issued_at=issued_at,
        status=status,
        qualified_at=qualified_at,
        qualified_until=qualified_until,
        observed_matches=observed,
        simultaneous_matches_tested=capacity,
        strata=strata,
    )


def load_qualification_artifact(
    path: str | Path,
    *,
    expected_sha256: str,
    repo_root: str | Path,
    _forbidden_root: Path | None = None,
) -> QualificationArtifact:
    raw, _ = _read_json(
        path,
        expected_sha256=expected_sha256,
        repo_root=repo_root,
        max_bytes=JSON_MAX_BYTES,
        artifact_name="qualification_artifact",
        forbidden_root=_forbidden_root,
    )
    artifact = _parse_qualification(raw)
    if artifact.qualification_protocol_sha256 != qualification_protocol_sha256():
        raise ManifestError("qualification_artifact: protocol_binding_mismatch")
    try:
        adapter = load_active_adapter_contract(
            provider_id=artifact.provider_id,
            product_tier=artifact.product_tier,
        )
    except AdapterContractError:
        raise ManifestError("qualification_artifact: active_adapter_invalid") from None
    if (
        artifact.adapter_code_sha256 != adapter.adapter_code_sha256
        or artifact.auth_contract_sha256 != adapter.auth_contract_sha256
        or artifact.quota_contract_sha256 != adapter.quota_contract_sha256
    ):
        raise ManifestError("qualification_artifact: adapter_binding_mismatch")
    return artifact


def _maximum_overlap(matches: tuple[QualificationTraceMatch, ...]) -> int:
    events: list[tuple[datetime, int]] = []
    for item in matches:
        events.append((item.started_at, 1))
        events.append((item.ended_at, -1))
    current = 0
    maximum = 0
    for _, change in sorted(events, key=lambda item: (item[0], item[1])):
        current += change
        maximum = max(maximum, current)
    return maximum


def _derived_trace_summary(
    trace: QualificationTrace,
) -> tuple[int, int, tuple[QualifiedStratumEvidence, ...]]:
    grouped: dict[CoverageStratum, list[QualificationTraceMatch]] = defaultdict(list)
    for item in trace.matches:
        grouped[item.stratum].append(item)
    strata = tuple(
        QualifiedStratumEvidence(
            stratum=stratum,
            observed_matches=len(matches),
            simultaneous_matches_tested=_maximum_overlap(tuple(matches)),
            tested_formats=tuple(sorted({item.tested_format for item in matches})),
            tested_capabilities=tuple(
                sorted(
                    {
                        capability
                        for item in matches
                        for capability in item.tested_capabilities
                    }
                )
            ),
        )
        for stratum, matches in sorted(
            grouped.items(), key=lambda item: _stratum_key(item[0])
        )
    )
    return len(trace.matches), _maximum_overlap(trace.matches), strata


def opaque_id_sha256(value: str) -> str:
    opaque = _bounded_string(value, "opaque_identifier")
    return hashlib.sha256(
        b"INCI-OPAQUE-ID-V1\0" + opaque.encode("utf-8")
    ).hexdigest()


def _terms_url(value: object) -> str:
    url = _bounded_string(value, "terms_url", maximum=2048)
    if TERMS_URL_RE.fullmatch(url) is None:
        raise ManifestError("terms_url: invalid_public_https_url")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ManifestError("terms_url: invalid_url") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ManifestError("terms_url: invalid_public_https_url")
    return url


def _environment_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ManifestError("credential_env_names: invalid_list")
    if any(
        not isinstance(item, str) or ENV_NAME.fullmatch(item) is None
        for item in value
    ):
        raise ManifestError("credential_env_names: invalid_name")
    if len(set(value)) != len(value):
        raise ManifestError("credential_env_names: duplicate_name")
    return tuple(sorted(value))


def _permission_projection(value: PermissionArtifact) -> dict[str, object]:
    if type(value) is not PermissionArtifact:
        raise ManifestError("canonical_manifest: invalid_permission_type")
    return {
        "schema_version": value.schema_version,
        "provider_id": value.provider_id,
        "product_tier": value.product_tier,
        "entitlement_id_sha256": value.entitlement_id_sha256,
        "terms_version": value.terms_version,
        "basis": value.basis.value,
        "intended_use": value.intended_use.value,
        "permitted_operations": [item.value for item in value.permitted_operations],
        "access_starts_at": format_utc(value.access_starts_at),
        "access_expires_at": format_utc(value.access_expires_at),
        "analysis_expires_at": format_utc(value.analysis_expires_at),
        "raw_retention_until": format_utc(value.raw_retention_until),
        "reviewed_at": format_utc(value.reviewed_at),
        "reviewer_id": value.reviewer_id,
        "approval_id": value.approval_id,
        "evidence_document_sha256": value.evidence_document_sha256,
    }


def _stratum_projection(value: CoverageStratum) -> dict[str, str]:
    if type(value) is not CoverageStratum:
        raise ManifestError("canonical_manifest: invalid_stratum_type")
    return {
        "sport": value.sport,
        "tour": value.tour,
        "competition_tier": value.competition_tier,
        "match_format": value.match_format,
        "round_code": value.round_code,
    }


def _qualification_projection(value: QualificationArtifact) -> dict[str, object]:
    if type(value) is not QualificationArtifact:
        raise ManifestError("canonical_manifest: invalid_qualification_type")
    if type(value.strata) is not tuple or any(
        type(item) is not QualifiedStratumEvidence
        or type(item.stratum) is not CoverageStratum
        for item in value.strata
    ):
        raise ManifestError("canonical_manifest: invalid_qualification_strata")
    return {
        "schema_version": value.schema_version,
        "provider_id": value.provider_id,
        "product_tier": value.product_tier,
        "source_lineage_id": value.source_lineage_id,
        "adapter_code_sha256": value.adapter_code_sha256,
        "auth_contract_sha256": value.auth_contract_sha256,
        "quota_contract_sha256": value.quota_contract_sha256,
        "qualification_protocol_sha256": value.qualification_protocol_sha256,
        "evidence_trace_sha256": value.evidence_trace_sha256,
        "issuer_id": value.issuer_id,
        "approval_id": value.approval_id,
        "issued_at": format_utc(value.issued_at),
        "status": value.status.value,
        "qualified_at": (
            format_utc(value.qualified_at) if value.qualified_at is not None else None
        ),
        "qualified_until": (
            format_utc(value.qualified_until)
            if value.qualified_until is not None
            else None
        ),
        "observed_matches": value.observed_matches,
        "simultaneous_matches_tested": value.simultaneous_matches_tested,
        "strata": [
            {
                "stratum": _stratum_projection(item.stratum),
                "observed_matches": item.observed_matches,
                "simultaneous_matches_tested": item.simultaneous_matches_tested,
                "tested_formats": list(item.tested_formats),
                "tested_capabilities": list(item.tested_capabilities),
            }
            for item in value.strata
        ],
    }


def _canonical_projection(manifest: ProviderManifest) -> dict[str, object]:
    if type(manifest) is not ProviderManifest:
        raise ManifestError("canonical_manifest: invalid_type")
    if type(manifest.permission) is not PermissionArtifact:
        raise ManifestError("canonical_manifest: invalid_permission_type")
    if type(manifest.quotas) is not ProviderQuotas:
        raise ManifestError("canonical_manifest: invalid_quotas_type")
    if type(manifest.capabilities) is not ProviderCapabilities:
        raise ManifestError("canonical_manifest: invalid_capabilities_type")
    if type(manifest.capabilities.declared_strata) is not tuple or any(
        type(item) is not CoverageStratum
        for item in manifest.capabilities.declared_strata
    ):
        raise ManifestError("canonical_manifest: invalid_capability_strata")
    if type(manifest.qualification) is not QualificationArtifact:
        raise ManifestError("canonical_manifest: invalid_qualification_type")
    return {
        "schema_version": manifest.schema_version,
        "provider_id": manifest.provider_id,
        "product_tier": manifest.product_tier,
        "entitlement_id_sha256": opaque_id_sha256(manifest.entitlement_id),
        "source_lineage_id": manifest.source_lineage_id,
        "terms_url": manifest.terms_url,
        "terms_version": manifest.terms_version,
        "permission_artifact_sha256": manifest.permission_artifact_sha256,
        "permission_evidence_sha256": manifest.permission_evidence_sha256,
        "permission": _permission_projection(manifest.permission),
        "billing_mode": manifest.billing_mode.value,
        "auto_renew": manifest.auto_renew,
        "access_starts_at": format_utc(manifest.access_starts_at),
        "access_expires_at": format_utc(manifest.access_expires_at),
        "analysis_expires_at": format_utc(manifest.analysis_expires_at),
        "raw_retention_until": format_utc(manifest.raw_retention_until),
        "max_raw_retention_seconds": manifest.max_raw_retention_seconds,
        "credential_env_names": list(manifest.credential_env_names),
        "quotas": {
            name: getattr(manifest.quotas, name)
            for name in ProviderQuotas.__dataclass_fields__
        },
        "capabilities": {
            **{
                name: getattr(manifest.capabilities, name)
                for name in BOOLEAN_CAPABILITY_KEYS
            },
            "supported_formats": list(manifest.capabilities.supported_formats),
            "declared_strata": [
                _stratum_projection(item)
                for item in manifest.capabilities.declared_strata
            ],
        },
        "qualification_artifact_sha256": manifest.qualification_artifact_sha256,
        "qualification_trace_sha256": manifest.qualification_trace_sha256,
        "qualification": _qualification_projection(manifest.qualification),
    }


def canonical_manifest_sha256(manifest: ProviderManifest) -> str:
    if type(manifest) is not ProviderManifest:
        raise ManifestError("canonical_manifest: invalid_type")
    return hashlib.sha256(canonical_json_bytes(_canonical_projection(manifest))).hexdigest()


def _bind_permission(
    permission: PermissionArtifact,
    *,
    provider_id: str,
    product_tier: str,
    entitlement_id: str,
    terms_version: str,
    billing_mode: BillingMode,
    access_starts_at: datetime,
    access_expires_at: datetime,
    analysis_expires_at: datetime,
    raw_retention_until: datetime,
) -> None:
    if (
        permission.provider_id != provider_id
        or permission.product_tier != product_tier
        or permission.entitlement_id_sha256 != opaque_id_sha256(entitlement_id)
        or permission.terms_version != terms_version
        or permission.intended_use is not IntendedUse.PRIVATE_PAPER_EVALUATION
        or permission.access_starts_at != access_starts_at
        or permission.access_expires_at != access_expires_at
        or permission.analysis_expires_at != analysis_expires_at
        or permission.raw_retention_until != raw_retention_until
    ):
        raise ManifestError("permission_artifact: manifest_binding_mismatch")
    if billing_mode is not BillingMode.TRIAL:
        raise ManifestError("permission_artifact: paid_access_disabled")


def _bind_qualification(
    qualification: QualificationArtifact,
    trace: QualificationTrace,
    adapter: AdapterContract,
    *,
    provider_id: str,
    product_tier: str,
    source_lineage_id: str,
    trace_sha256: str,
    analysis_expires_at: datetime,
    capabilities: ProviderCapabilities,
) -> None:
    if type(capabilities) is not ProviderCapabilities:
        raise ManifestError("qualification_artifact: invalid_capabilities_type")
    protocol_sha = qualification_protocol_sha256()
    shared = (
        provider_id,
        product_tier,
        source_lineage_id,
        adapter.adapter_code_sha256,
        adapter.auth_contract_sha256,
        adapter.quota_contract_sha256,
        protocol_sha,
    )
    if (
        (
            trace.provider_id,
            trace.product_tier,
            trace.source_lineage_id,
            trace.adapter_code_sha256,
            trace.auth_contract_sha256,
            trace.quota_contract_sha256,
            trace.qualification_protocol_sha256,
        )
        != shared
        or (
            qualification.provider_id,
            qualification.product_tier,
            qualification.source_lineage_id,
            qualification.adapter_code_sha256,
            qualification.auth_contract_sha256,
            qualification.quota_contract_sha256,
            qualification.qualification_protocol_sha256,
        )
        != shared
        or qualification.evidence_trace_sha256 != trace_sha256
        or trace.source_file_sha256 != trace_sha256
    ):
        raise ManifestError("qualification_artifact: verified_binding_mismatch")
    if any(item.tested_format not in adapter.formats for item in trace.matches):
        raise ManifestError("qualification_trace: unsupported_adapter_format")
    observed, capacity, strata = _derived_trace_summary(trace)
    if (
        qualification.observed_matches != observed
        or qualification.simultaneous_matches_tested != capacity
        or qualification.strata != strata
    ):
        raise ManifestError("qualification_artifact: trace_summary_mismatch")
    if qualification.status is QualificationStatus.PASSED:
        if (
            qualification.qualified_at != trace.completed_at
            or qualification.qualified_until is None
            or qualification.qualified_at is None
            or qualification.qualified_until > analysis_expires_at
            or qualification.qualified_until
            > qualification.qualified_at + timedelta(days=30)
            or any(
                not getattr(capabilities, name)
                for name in BOOLEAN_CAPABILITY_KEYS
            )
            or not {item.stratum for item in strata}.issubset(
                set(capabilities.declared_strata)
            )
        ):
            raise ManifestError("qualification_artifact: invalid_passed_binding")


def _load_provider_manifest(
    path: str | Path,
    *,
    expected_sha256: str,
    repo_root: str | Path,
    forbidden_root: Path | None,
) -> ProviderManifest:
    raw_value, source_sha = _read_json(
        path,
        expected_sha256=expected_sha256,
        repo_root=repo_root,
        max_bytes=JSON_MAX_BYTES,
        artifact_name="provider_manifest",
        forbidden_root=forbidden_root,
    )
    raw = _exact_object(raw_value, MANIFEST_KEYS, "provider_manifest")
    if raw["schema_version"] != 1 or isinstance(raw["schema_version"], bool):
        raise ManifestError("provider_manifest: unsupported_schema_version")
    provider_id = _safe_id(raw["provider_id"], "provider_id")
    product_tier = _safe_id(raw["product_tier"], "product_tier")
    entitlement_id = _bounded_string(raw["entitlement_id"], "entitlement_id")
    source_lineage_id = _safe_id(raw["source_lineage_id"], "source_lineage_id")
    terms_version = _safe_id(raw["terms_version"], "terms_version")
    permission_reference = _exact_object(
        raw["permission"], PERMISSION_REFERENCE_KEYS, "permission"
    )
    qualification_reference = _exact_object(
        raw["qualification"], QUALIFICATION_REFERENCE_KEYS, "qualification"
    )
    access_starts_at = _utc(raw["access_starts_at"], "access_starts_at")
    access_expires_at = _utc(raw["access_expires_at"], "access_expires_at")
    analysis_expires_at = _utc(raw["analysis_expires_at"], "analysis_expires_at")
    raw_retention_until = _utc(
        raw["raw_retention_until"], "raw_retention_until"
    )
    if not access_starts_at < access_expires_at <= analysis_expires_at:
        raise ManifestError("provider_manifest: invalid_access_window")
    if raw_retention_until < analysis_expires_at:
        raise ManifestError("provider_manifest: invalid_retention_window")
    max_raw_retention_seconds = _integer(
        raw["max_raw_retention_seconds"],
        "max_raw_retention_seconds",
        positive=True,
    )
    if (
        raw_retention_until - access_starts_at
        > timedelta(seconds=max_raw_retention_seconds)
    ):
        raise ManifestError("provider_manifest: retention_exceeds_declared_maximum")
    billing_mode = _enum(raw["billing_mode"], BillingMode, "billing_mode")
    credentials = _environment_names(raw["credential_env_names"])
    quotas = _parse_quotas(raw["quotas"])
    capabilities = _parse_capabilities(raw["capabilities"])

    permission_path = Path(
        _bounded_string(permission_reference["artifact_path"], "permission.artifact_path")
    )
    permission_sha = _sha256(
        permission_reference["artifact_sha256"], "permission.artifact_sha256"
    )
    evidence_path = Path(
        _bounded_string(permission_reference["evidence_path"], "permission.evidence_path")
    )
    evidence_sha = _sha256(
        permission_reference["evidence_sha256"], "permission.evidence_sha256"
    )
    permission = load_permission_artifact(
        permission_path,
        expected_sha256=permission_sha,
        evidence_path=evidence_path,
        expected_evidence_sha256=evidence_sha,
        repo_root=repo_root,
        _forbidden_root=forbidden_root,
    )
    _bind_permission(
        permission,
        provider_id=provider_id,
        product_tier=product_tier,
        entitlement_id=entitlement_id,
        terms_version=terms_version,
        billing_mode=billing_mode,
        access_starts_at=access_starts_at,
        access_expires_at=access_expires_at,
        analysis_expires_at=analysis_expires_at,
        raw_retention_until=raw_retention_until,
    )

    try:
        adapter = load_active_adapter_contract(
            provider_id=provider_id, product_tier=product_tier
        )
    except AdapterContractError:
        raise ManifestError("provider_manifest: active_adapter_invalid") from None
    if (
        adapter.auth.credential_env_names != credentials
        or adapter.formats != capabilities.supported_formats
    ):
        raise ManifestError("provider_manifest: adapter_contract_mismatch")

    qualification_path = Path(
        _bounded_string(
            qualification_reference["artifact_path"],
            "qualification.artifact_path",
        )
    )
    qualification_sha = _sha256(
        qualification_reference["artifact_sha256"],
        "qualification.artifact_sha256",
    )
    trace_path = Path(
        _bounded_string(
            qualification_reference["evidence_trace_path"],
            "qualification.evidence_trace_path",
        )
    )
    trace_sha = _sha256(
        qualification_reference["evidence_trace_sha256"],
        "qualification.evidence_trace_sha256",
    )
    trace = load_qualification_trace(
        trace_path,
        expected_sha256=trace_sha,
        repo_root=repo_root,
        _forbidden_root=forbidden_root,
    )
    qualification = load_qualification_artifact(
        qualification_path,
        expected_sha256=qualification_sha,
        repo_root=repo_root,
        _forbidden_root=forbidden_root,
    )
    _bind_qualification(
        qualification,
        trace,
        adapter,
        provider_id=provider_id,
        product_tier=product_tier,
        source_lineage_id=source_lineage_id,
        trace_sha256=trace_sha,
        analysis_expires_at=analysis_expires_at,
        capabilities=capabilities,
    )
    provisional = ProviderManifest(
        schema_version=1,
        provider_id=provider_id,
        product_tier=product_tier,
        entitlement_id=entitlement_id,
        source_lineage_id=source_lineage_id,
        terms_url=_terms_url(raw["terms_url"]),
        terms_version=terms_version,
        permission_artifact_path=permission_path,
        permission_artifact_sha256=permission_sha,
        permission_evidence_path=evidence_path,
        permission_evidence_sha256=evidence_sha,
        permission=permission,
        billing_mode=billing_mode,
        auto_renew=_boolean(raw["auto_renew"], "auto_renew"),
        access_starts_at=access_starts_at,
        access_expires_at=access_expires_at,
        analysis_expires_at=analysis_expires_at,
        raw_retention_until=raw_retention_until,
        max_raw_retention_seconds=max_raw_retention_seconds,
        credential_env_names=credentials,
        quotas=quotas,
        capabilities=capabilities,
        qualification_artifact_path=qualification_path,
        qualification_artifact_sha256=qualification_sha,
        qualification_trace_path=trace_path,
        qualification_trace_sha256=trace_sha,
        qualification=qualification,
        source_file_sha256=source_sha,
        canonical_sha256="",
    )
    return replace(
        provisional, canonical_sha256=canonical_manifest_sha256(provisional)
    )


def load_provider_manifest(
    path: str | Path,
    *,
    expected_sha256: str,
    repo_root: str | Path,
) -> ProviderManifest:
    return _load_provider_manifest(
        path,
        expected_sha256=expected_sha256,
        repo_root=repo_root,
        forbidden_root=None,
    )


def _load_provider_manifest_restricted(
    path: Path,
    *,
    expected_sha256: str,
    repo_root: Path,
    forbidden_root: Path,
) -> ProviderManifest:
    return _load_provider_manifest(
        path,
        expected_sha256=expected_sha256,
        repo_root=repo_root,
        forbidden_root=forbidden_root,
    )


class QualificationReason(str, Enum):
    ELIGIBLE = "eligible"
    PAID_ACCESS_DISABLED = "paid_access_disabled"
    AUTO_RENEW_FORBIDDEN = "auto_renew_forbidden"
    ACCESS_NOT_STARTED = "access_not_started"
    ACCESS_EXPIRED = "access_expired"
    CLOCK_ROLLBACK = "clock_rollback"
    SESSION_WINDOW_EXCEEDS_ACCESS = "session_window_exceeds_access"
    ANALYSIS_EXPIRED = "analysis_expired"
    ANALYSIS_WINDOW_INADEQUATE = "analysis_window_inadequate"
    RETENTION_TOO_SHORT = "retention_too_short"
    MANDATORY_PERMISSION_MISSING = "mandatory_permission_missing"
    CREDENTIAL_MISSING = "credential_missing"
    QUOTA_INADEQUATE = "quota_inadequate"
    CAPABILITY_MISSING = "capability_missing"
    FORMAT_UNSUPPORTED = "format_unsupported"
    ADAPTER_MISMATCH = "adapter_mismatch"
    QUALIFICATION_EVIDENCE_MISMATCH = "qualification_evidence_mismatch"
    QUALIFICATION_NOT_PASSED = "qualification_not_passed"
    QUALIFICATION_CAPACITY_INADEQUATE = "qualification_capacity_inadequate"
    STRATUM_NOT_QUALIFIED = "stratum_not_qualified"


@dataclass(frozen=True, slots=True)
class RequestedStratum:
    stratum: CoverageStratum
    matches: int


@dataclass(frozen=True, slots=True)
class ResearchRequest:
    intended_use: IntendedUse
    now_utc: datetime
    session_end_utc: datetime
    required_retention_until: datetime
    expiry_safety_margin_seconds: int
    required_raw_retention_seconds: int
    requested_matches: int
    required_strata: tuple[RequestedStratum, ...]


@dataclass(frozen=True, slots=True)
class QualifiedProviderBinding:
    provider_id: str
    product_tier: str
    source_lineage_id: str
    entitlement_id_sha256: str
    manifest_file_sha256: str
    manifest_canonical_sha256: str
    qualification_artifact_sha256: str
    permission_artifact_sha256: str
    qualification_trace_sha256: str
    adapter_code_sha256: str
    auth_contract_sha256: str
    quota_contract_sha256: str
    session_end_utc: datetime
    required_retention_until: datetime
    access_expires_at: datetime
    analysis_expires_at: datetime
    qualified_until: datetime


@dataclass(frozen=True, slots=True)
class QualificationDecision:
    eligible: bool
    reasons: tuple[QualificationReason, ...]
    export_allowed: bool
    manifest_file_sha256: str
    manifest_canonical_sha256: str
    request_sha256: str
    provider_request_binding_sha256: str | None
    binding: QualifiedProviderBinding | None

    def require_eligible(self) -> None:
        if type(self) is not QualificationDecision:
            raise TypeError("exact QualificationDecision required")
        if self.eligible:
            return
        reason = (
            self.reasons[0]
            if self.reasons
            else QualificationReason.QUALIFICATION_NOT_PASSED
        )
        summary = ",".join(item.value for item in self.reasons)
        raise ProviderGateError(reason, f"provider qualification denied: {summary}")


class ProviderGateError(RuntimeError):
    reason: QualificationReason

    def __init__(self, reason: QualificationReason, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or f"provider qualification denied: {reason.value}")


REQUEST_HASH_DOMAIN = b"INCI-RESEARCH-REQUEST-V1\0"
PROVIDER_REQUEST_BINDING_DOMAIN = b"INCI-PROVIDER-REQUEST-BINDING-V1\0"
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _is_aware_utc(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )


def _normalized_datetime(value: object) -> str:
    return format_utc(value) if _is_aware_utc(value) else "<invalid-utc>"


def _normalized_requested_stratum(value: object) -> dict[str, object]:
    if type(value) is not RequestedStratum:
        return {"stratum": "<invalid-stratum>", "matches": "<invalid-integer>"}
    stratum = value.stratum
    return {
        "stratum": (
            _stratum_projection(stratum)
            if type(stratum) is CoverageStratum
            else "<invalid-stratum>"
        ),
        "matches": (
            value.matches
            if type(value.matches) is int
            else "<invalid-integer>"
        ),
    }


def _request_projection(request: ResearchRequest) -> dict[str, object]:
    if type(request) is not ResearchRequest:
        raise TypeError("exact ResearchRequest required")
    intended_use = (
        request.intended_use.value
        if isinstance(request.intended_use, IntendedUse)
        else "<invalid-intended-use>"
    )
    strata = (
        sorted(
            (
                _normalized_requested_stratum(item)
                for item in request.required_strata
            ),
            key=canonical_json_bytes,
        )
        if type(request.required_strata) is tuple
        else ["<invalid-required-strata>"]
    )
    return {
        "intended_use": intended_use,
        "now_utc": _normalized_datetime(request.now_utc),
        "session_end_utc": _normalized_datetime(request.session_end_utc),
        "required_retention_until": _normalized_datetime(
            request.required_retention_until
        ),
        "expiry_safety_margin_seconds": (
            request.expiry_safety_margin_seconds
            if type(request.expiry_safety_margin_seconds) is int
            else "<invalid-integer>"
        ),
        "required_raw_retention_seconds": (
            request.required_raw_retention_seconds
            if type(request.required_raw_retention_seconds) is int
            else "<invalid-integer>"
        ),
        "requested_matches": (
            request.requested_matches
            if type(request.requested_matches) is int
            else "<invalid-integer>"
        ),
        "required_strata": strata,
    }


def _request_sha256(request: ResearchRequest) -> str:
    if type(request) is not ResearchRequest:
        raise TypeError("exact ResearchRequest required")
    return hashlib.sha256(
        REQUEST_HASH_DOMAIN + canonical_json_bytes(_request_projection(request))
    ).hexdigest()


def _binding_projection(value: QualifiedProviderBinding) -> dict[str, object]:
    if type(value) is not QualifiedProviderBinding:
        raise TypeError("exact QualifiedProviderBinding required")
    return {
        "provider_id": value.provider_id,
        "product_tier": value.product_tier,
        "source_lineage_id": value.source_lineage_id,
        "entitlement_id_sha256": value.entitlement_id_sha256,
        "manifest_file_sha256": value.manifest_file_sha256,
        "manifest_canonical_sha256": value.manifest_canonical_sha256,
        "qualification_artifact_sha256": value.qualification_artifact_sha256,
        "permission_artifact_sha256": value.permission_artifact_sha256,
        "qualification_trace_sha256": value.qualification_trace_sha256,
        "adapter_code_sha256": value.adapter_code_sha256,
        "auth_contract_sha256": value.auth_contract_sha256,
        "quota_contract_sha256": value.quota_contract_sha256,
        "session_end_utc": format_utc(value.session_end_utc),
        "required_retention_until": format_utc(value.required_retention_until),
        "access_expires_at": format_utc(value.access_expires_at),
        "analysis_expires_at": format_utc(value.analysis_expires_at),
        "qualified_until": format_utc(value.qualified_until),
    }


def provider_request_binding_sha256(decision: QualificationDecision) -> str:
    if (
        type(decision) is not QualificationDecision
        or not decision.eligible
        or type(decision.binding) is not QualifiedProviderBinding
    ):
        raise ProviderGateError(
            QualificationReason.QUALIFICATION_NOT_PASSED,
            "provider request binding unavailable",
        )
    return hashlib.sha256(
        PROVIDER_REQUEST_BINDING_DOMAIN
        + canonical_json_bytes(
            {
                "request_sha256": decision.request_sha256,
                "binding": _binding_projection(decision.binding),
            }
        )
    ).hexdigest()


def _positive_nonboolean_integer(value: object) -> bool:
    return type(value) is int and value > 0


def _snapshot_environment(environ: object) -> dict[str, str]:
    if type(environ) is not dict:
        raise TypeError("environ: exact_dict_of_exact_str_required")
    snapshot = dict.copy(environ)
    for name, value in dict.items(snapshot):
        if type(name) is not str or type(value) is not str:
            raise TypeError("environ: exact_dict_of_exact_str_required")
    return snapshot


def _sorted_reasons(
    reasons: set[QualificationReason],
) -> tuple[QualificationReason, ...]:
    return tuple(sorted(reasons, key=lambda item: item.value))


def _evaluate_provider_as_of(
    config: TennisV1Config,
    manifest: ProviderManifest,
    request: ResearchRequest,
    *,
    environ: dict[str, str],
    as_of: datetime,
) -> QualificationDecision:
    if type(config) is not TennisV1Config:
        raise TypeError("config must be TennisV1Config")
    if type(manifest) is not ProviderManifest:
        raise TypeError("manifest must be ProviderManifest")
    if type(request) is not ResearchRequest:
        raise TypeError("request must be ResearchRequest")
    if type(environ) is not dict:
        raise TypeError("environ: private_exact_dict_required")
    _canonical_projection(manifest)

    reasons: set[QualificationReason] = set()
    try:
        recomputed_manifest_canonical_sha256 = canonical_manifest_sha256(
            manifest
        )
    except Exception:
        reasons.add(QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH)
    else:
        if not _safe_sha256_equal(
            recomputed_manifest_canonical_sha256,
            manifest.canonical_sha256,
        ):
            reasons.add(QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH)

    request_sha = _request_sha256(request)
    if not _safe_sha256_equal(
        config.provider_manifest_sha256,
        manifest.source_file_sha256,
    ):
        reasons.add(QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH)
    request_now_valid = _is_aware_utc(request.now_utc)
    session_end_valid = _is_aware_utc(request.session_end_utc)
    retention_until_valid = _is_aware_utc(request.required_retention_until)
    as_of_valid = _is_aware_utc(as_of)

    if manifest.billing_mode is not BillingMode.TRIAL:
        reasons.add(QualificationReason.PAID_ACCESS_DISABLED)
    if manifest.auto_renew is not False:
        reasons.add(QualificationReason.AUTO_RENEW_FORBIDDEN)

    if not (
        request_now_valid
        and session_end_valid
        and request.now_utc < request.session_end_utc
    ):
        reasons.add(QualificationReason.SESSION_WINDOW_EXCEEDS_ACCESS)
    if not (
        session_end_valid
        and retention_until_valid
        and request.session_end_utc <= request.required_retention_until
    ):
        reasons.add(QualificationReason.RETENTION_TOO_SHORT)

    if as_of_valid:
        if as_of < manifest.access_starts_at:
            reasons.add(QualificationReason.ACCESS_NOT_STARTED)
        if as_of >= manifest.access_expires_at:
            reasons.add(QualificationReason.ACCESS_EXPIRED)
        if as_of >= manifest.analysis_expires_at:
            reasons.add(QualificationReason.ANALYSIS_EXPIRED)
    else:
        reasons.add(QualificationReason.SESSION_WINDOW_EXCEEDS_ACCESS)

    margin_valid = _positive_nonboolean_integer(
        request.expiry_safety_margin_seconds
    )
    if not margin_valid:
        reasons.add(QualificationReason.SESSION_WINDOW_EXCEEDS_ACCESS)
    elif session_end_valid:
        try:
            safe_session_end = request.session_end_utc + timedelta(
                seconds=request.expiry_safety_margin_seconds
            )
        except OverflowError:
            reasons.add(QualificationReason.SESSION_WINDOW_EXCEEDS_ACCESS)
        else:
            if safe_session_end >= manifest.access_expires_at:
                reasons.add(QualificationReason.SESSION_WINDOW_EXCEEDS_ACCESS)

    raw_seconds_valid = _positive_nonboolean_integer(
        request.required_raw_retention_seconds
    )
    if not raw_seconds_valid:
        reasons.add(QualificationReason.RETENTION_TOO_SHORT)
    else:
        if request.required_raw_retention_seconds > manifest.max_raw_retention_seconds:
            reasons.add(QualificationReason.RETENTION_TOO_SHORT)
        if session_end_valid and retention_until_valid:
            try:
                exact_retention_until = request.session_end_utc + timedelta(
                    seconds=request.required_raw_retention_seconds
                )
            except OverflowError:
                reasons.add(QualificationReason.RETENTION_TOO_SHORT)
            else:
                if request.required_retention_until != exact_retention_until:
                    reasons.add(QualificationReason.RETENTION_TOO_SHORT)

    if retention_until_valid:
        if request.required_retention_until > manifest.analysis_expires_at:
            reasons.add(QualificationReason.ANALYSIS_WINDOW_INADEQUATE)
        if request.required_retention_until > manifest.raw_retention_until:
            reasons.add(QualificationReason.RETENTION_TOO_SHORT)
        if as_of_valid:
            try:
                earliest_capture_limit = min(
                    manifest.raw_retention_until,
                    as_of
                    + timedelta(seconds=manifest.max_raw_retention_seconds),
                )
            except OverflowError:
                earliest_capture_limit = manifest.raw_retention_until
            if earliest_capture_limit < request.required_retention_until:
                reasons.add(QualificationReason.RETENTION_TOO_SHORT)

    permission = manifest.permission
    mandatory = {
        PermissionOperation.PROVIDER_INGEST,
        PermissionOperation.RAW_RETENTION,
        PermissionOperation.DERIVED_SIGNALS,
    }
    permission_operations = set(permission.permitted_operations)
    permission_windows_valid = (
        permission.access_starts_at
        < permission.access_expires_at
        <= permission.analysis_expires_at
        <= permission.raw_retention_until
    )
    has_post_expiry = (
        PermissionOperation.POST_EXPIRY_ANALYSIS in permission_operations
    )
    permission_basis_valid = (
        (
            permission.basis is PermissionBasis.TRIAL_TERMS
            and permission_operations == mandatory
            and permission.analysis_expires_at == permission.access_expires_at
        )
        or (
            permission.basis is PermissionBasis.WRITTEN_PERMISSION
            and mandatory.issubset(permission_operations)
            and (
                permission.analysis_expires_at
                > permission.access_expires_at
            )
            == has_post_expiry
        )
    )
    if (
        request.intended_use is not IntendedUse.PRIVATE_PAPER_EVALUATION
        or permission.intended_use is not request.intended_use
        or not mandatory.issubset(permission_operations)
        or not permission_windows_valid
        or not permission_basis_valid
        or permission.reviewer_id not in config.trusted_permission_reviewer_ids
        or not as_of_valid
        or permission.reviewed_at > as_of
        or permission.provider_id != manifest.provider_id
        or permission.product_tier != manifest.product_tier
        or permission.entitlement_id_sha256
        != opaque_id_sha256(manifest.entitlement_id)
        or permission.terms_version != manifest.terms_version
        or permission.access_starts_at != manifest.access_starts_at
        or permission.access_expires_at != manifest.access_expires_at
        or permission.analysis_expires_at != manifest.analysis_expires_at
        or permission.raw_retention_until != manifest.raw_retention_until
    ):
        reasons.add(QualificationReason.MANDATORY_PERMISSION_MISSING)

    adapter: AdapterContract | None
    try:
        adapter = load_active_adapter_contract(
            provider_id=manifest.provider_id,
            product_tier=manifest.product_tier,
        )
    except AdapterContractError:
        adapter = None
        reasons.add(QualificationReason.ADAPTER_MISMATCH)

    credential_names: tuple[str, ...] = ()
    if adapter is not None:
        credential_names = adapter.auth.credential_env_names
        auth_matches = (
            manifest.credential_env_names == credential_names
            and (
                (
                    adapter.auth.mode is AuthMode.PUBLIC
                    and credential_names == ()
                )
                or (
                    adapter.auth.mode is not AuthMode.PUBLIC
                    and bool(credential_names)
                )
            )
        )
        if (
            not auth_matches
            or manifest.capabilities.supported_formats != adapter.formats
        ):
            reasons.add(QualificationReason.ADAPTER_MISMATCH)
        qualification = manifest.qualification
        if (
            qualification.provider_id != manifest.provider_id
            or qualification.product_tier != manifest.product_tier
            or qualification.source_lineage_id != manifest.source_lineage_id
            or qualification.adapter_code_sha256 != adapter.adapter_code_sha256
            or qualification.auth_contract_sha256
            != adapter.auth_contract_sha256
            or qualification.quota_contract_sha256
            != adapter.quota_contract_sha256
            or qualification.qualification_protocol_sha256
            != qualification_protocol_sha256()
            or qualification.evidence_trace_sha256
            != manifest.qualification_trace_sha256
        ):
            reasons.add(QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH)

    for name in credential_names:
        value = environ.get(name)
        if type(value) is not str or not value.strip():
            reasons.add(QualificationReason.CREDENTIAL_MISSING)

    matches_valid = (
        type(request.requested_matches) is int
        and 1 <= request.requested_matches <= 10
        and request.requested_matches <= config.observed_pool_limit
    )
    strata_valid = type(request.required_strata) is tuple and bool(
        request.required_strata
    )
    requested_by_stratum: dict[CoverageStratum, int] = {}
    if strata_valid:
        for item in request.required_strata:
            if (
                not isinstance(item, RequestedStratum)
                or not isinstance(item.stratum, CoverageStratum)
                or not _positive_nonboolean_integer(item.matches)
                or item.stratum in requested_by_stratum
            ):
                strata_valid = False
                break
            requested_by_stratum[item.stratum] = item.matches
    if (
        not matches_valid
        or not strata_valid
        or sum(requested_by_stratum.values()) != request.requested_matches
    ):
        reasons.add(QualificationReason.QUALIFICATION_CAPACITY_INADEQUATE)
        reasons.add(QualificationReason.STRATUM_NOT_QUALIFIED)

    if adapter is not None and matches_valid and request_now_valid and session_end_valid:
        try:
            demand = derive_quota_demand(adapter, request)
        except AdapterContractError:
            reasons.add(QualificationReason.QUOTA_INADEQUATE)
        else:
            if type(demand) is not ProviderQuotas:
                raise TypeError("exact ProviderQuotas required")
            if any(
                getattr(demand, name) > getattr(manifest.quotas, name)
                for name in ProviderQuotas.__dataclass_fields__
            ):
                reasons.add(QualificationReason.QUOTA_INADEQUATE)

    if any(
        not getattr(manifest.capabilities, name)
        for name in REQUIRED_QUALIFICATION_CAPABILITIES
    ):
        reasons.add(QualificationReason.CAPABILITY_MISSING)

    qualification = manifest.qualification
    qualified_at = qualification.qualified_at
    qualified_until = qualification.qualified_until
    qualification_current = (
        qualification.status is QualificationStatus.PASSED
        and qualification.issuer_id
        in config.trusted_qualification_issuer_ids
        and isinstance(qualified_at, datetime)
        and isinstance(qualified_until, datetime)
        and _is_aware_utc(qualified_at)
        and _is_aware_utc(qualification.issued_at)
        and _is_aware_utc(qualified_until)
        and as_of_valid
        and qualified_at <= qualification.issued_at <= as_of < qualified_until
        and qualified_until <= manifest.analysis_expires_at
        and qualified_until <= qualified_at + timedelta(days=30)
    )
    if not qualification_current:
        reasons.add(QualificationReason.QUALIFICATION_NOT_PASSED)

    if matches_valid and (
        qualification.observed_matches < request.requested_matches
        or qualification.simultaneous_matches_tested
        < request.requested_matches
    ):
        reasons.add(QualificationReason.QUALIFICATION_CAPACITY_INADEQUATE)

    declared = set(manifest.capabilities.declared_strata)
    evidence_by_stratum = {item.stratum: item for item in qualification.strata}
    for stratum, matches in requested_by_stratum.items():
        evidence = evidence_by_stratum.get(stratum)
        if (
            stratum not in declared
            or evidence is None
            or evidence.observed_matches < matches
            or evidence.simultaneous_matches_tested < matches
        ):
            reasons.add(QualificationReason.STRATUM_NOT_QUALIFIED)
            continue
        if adapter is None or not set(evidence.tested_formats).intersection(
            adapter.formats
        ):
            reasons.add(QualificationReason.FORMAT_UNSUPPORTED)
        if not REQUIRED_QUALIFICATION_CAPABILITIES.issubset(
            set(evidence.tested_capabilities)
        ):
            reasons.add(QualificationReason.CAPABILITY_MISSING)

    eligible = not reasons
    binding: QualifiedProviderBinding | None = None
    if eligible:
        reasons.add(QualificationReason.ELIGIBLE)
        assert adapter is not None
        assert qualified_until is not None
        binding = QualifiedProviderBinding(
            provider_id=manifest.provider_id,
            product_tier=manifest.product_tier,
            source_lineage_id=manifest.source_lineage_id,
            entitlement_id_sha256=opaque_id_sha256(manifest.entitlement_id),
            manifest_file_sha256=manifest.source_file_sha256,
            manifest_canonical_sha256=manifest.canonical_sha256,
            qualification_artifact_sha256=manifest.qualification_artifact_sha256,
            permission_artifact_sha256=manifest.permission_artifact_sha256,
            qualification_trace_sha256=manifest.qualification_trace_sha256,
            adapter_code_sha256=adapter.adapter_code_sha256,
            auth_contract_sha256=adapter.auth_contract_sha256,
            quota_contract_sha256=adapter.quota_contract_sha256,
            session_end_utc=request.session_end_utc,
            required_retention_until=request.required_retention_until,
            access_expires_at=manifest.access_expires_at,
            analysis_expires_at=manifest.analysis_expires_at,
            qualified_until=qualified_until,
        )
    ordered_reasons = _sorted_reasons(reasons)
    decision = QualificationDecision(
        eligible=eligible,
        reasons=ordered_reasons,
        export_allowed=eligible
        and PermissionOperation.PUBLICATION in permission.permitted_operations,
        manifest_file_sha256=manifest.source_file_sha256,
        manifest_canonical_sha256=manifest.canonical_sha256,
        request_sha256=request_sha,
        provider_request_binding_sha256=None,
        binding=binding,
    )
    if eligible:
        decision = replace(
            decision,
            provider_request_binding_sha256=provider_request_binding_sha256(
                decision
            ),
        )
    return decision


def evaluate_provider(
    config: TennisV1Config,
    manifest: ProviderManifest,
    request: ResearchRequest,
    *,
    environ: dict[str, str],
) -> QualificationDecision:
    if type(config) is not TennisV1Config:
        raise TypeError("config must be TennisV1Config")
    if type(manifest) is not ProviderManifest:
        raise TypeError("manifest must be ProviderManifest")
    if type(request) is not ResearchRequest:
        raise TypeError("request must be ResearchRequest")
    snapshot = _snapshot_environment(environ)
    return _evaluate_provider_as_of(
        config,
        manifest,
        request,
        environ=snapshot,
        as_of=request.now_utc,
    )


def _decision_with_reasons(
    decision: QualificationDecision,
    reasons: set[QualificationReason],
) -> QualificationDecision:
    ordered = _sorted_reasons(reasons)
    return replace(
        decision,
        eligible=False,
        reasons=ordered,
        export_allowed=False,
        provider_request_binding_sha256=None,
        binding=None,
    )


@dataclass(frozen=True, slots=True)
class ProviderSessionPoll:
    decision: QualificationDecision
    session_ended: bool

    def __post_init__(self) -> None:
        if type(self.decision) is not QualificationDecision:
            raise TypeError("exact QualificationDecision required")
        if type(self.session_ended) is not bool:
            raise TypeError("session_ended: exact_bool_required")


class ProviderGate:
    def __init__(
        self,
        config: TennisV1Config,
        manifest: ProviderManifest,
        request: ResearchRequest,
        *,
        environ: dict[str, str],
        clock: Callable[[], datetime],
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(config) is not TennisV1Config:
            raise TypeError("config must be TennisV1Config")
        if type(manifest) is not ProviderManifest:
            raise TypeError("manifest must be ProviderManifest")
        if type(request) is not ResearchRequest:
            raise TypeError("request must be ResearchRequest")
        snapshot = _snapshot_environment(environ)
        self._config = config
        self._manifest = manifest
        self._request = request
        self._environ = environ
        self._clock = clock
        self._initial_decision = _evaluate_provider_as_of(
            config,
            manifest,
            request,
            environ=snapshot,
            as_of=request.now_utc,
        )
        self._latest_now = (
            request.now_utc if _is_aware_utc(request.now_utc) else None
        )

    def _sample_clock(self) -> datetime:
        now = self._clock()
        if not _is_aware_utc(now):
            raise ProviderGateError(
                QualificationReason.CLOCK_ROLLBACK,
                "provider operation denied: invalid authoritative clock",
            )
        if self._latest_now is not None and now < self._latest_now:
            raise ProviderGateError(
                QualificationReason.CLOCK_ROLLBACK,
                "provider operation denied: clock_rollback",
            )
        self._latest_now = now
        return now

    def _runtime_decision(self, now: datetime) -> QualificationDecision:
        self._initial_decision.require_eligible()
        try:
            current_manifest_canonical_sha256 = canonical_manifest_sha256(
                self._manifest
            )
            current_request_sha256 = _request_sha256(self._request)
        except Exception:
            raise ProviderGateError(
                QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
                "provider operation denied: qualification_evidence_mismatch",
            ) from None
        if not (
            _safe_sha256_equal(
                current_manifest_canonical_sha256,
                self._initial_decision.manifest_canonical_sha256,
            )
            and _safe_sha256_equal(
                self._manifest.source_file_sha256,
                self._initial_decision.manifest_file_sha256,
            )
            and _safe_sha256_equal(
                self._manifest.canonical_sha256,
                self._initial_decision.manifest_canonical_sha256,
            )
            and _safe_sha256_equal(
                current_request_sha256,
                self._initial_decision.request_sha256,
            )
        ):
            raise ProviderGateError(
                QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
                "provider operation denied: qualification_evidence_mismatch",
            )
        snapshot = _snapshot_environment(self._environ)
        current = _evaluate_provider_as_of(
            self._config,
            self._manifest,
            self._request,
            environ=snapshot,
            as_of=now,
        )
        if current.eligible and not _safe_sha256_equal(
            current.provider_request_binding_sha256,
            self._initial_decision.provider_request_binding_sha256,
        ):
            return _decision_with_reasons(
                current,
                {QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH},
            )
        return current

    def _require_access_operation(
        self,
        *,
        enforce_session_window: bool = True,
    ) -> QualificationDecision:
        now = self._sample_clock()
        decision = self._runtime_decision(now)
        reasons = set(
            () if decision.eligible else decision.reasons
        )
        if enforce_session_window and now >= self._request.session_end_utc:
            reasons.add(QualificationReason.SESSION_WINDOW_EXCEEDS_ACCESS)
        try:
            safe_now = now + timedelta(
                seconds=self._request.expiry_safety_margin_seconds
            )
        except (OverflowError, TypeError):
            safe_now = self._manifest.access_expires_at
        if safe_now >= self._manifest.access_expires_at:
            reasons.add(QualificationReason.SESSION_WINDOW_EXCEEDS_ACCESS)
        if reasons:
            _decision_with_reasons(decision, reasons).require_eligible()
        return self._initial_decision

    def require_start(self) -> QualificationDecision:
        return self._require_access_operation()

    def require_ingest(self) -> QualificationDecision:
        return self._require_access_operation()

    def require_resync(self) -> QualificationDecision:
        return self._require_access_operation()

    def require_transform(self) -> QualificationDecision:
        return self._require_access_operation()

    def require_derived_persist(self) -> QualificationDecision:
        return self._require_access_operation()

    def require_raw_persist(self) -> int:
        self._require_access_operation()
        now = self._latest_now
        assert now is not None
        try:
            upper = min(
                self._manifest.raw_retention_until,
                now
                + timedelta(seconds=self._manifest.max_raw_retention_seconds),
            )
        except OverflowError:
            upper = self._manifest.raw_retention_until
        if (
            upper <= now
            or upper < self._request.required_retention_until
        ):
            raise ProviderGateError(
                QualificationReason.RETENTION_TOO_SHORT,
                "provider operation denied: retention_too_short",
            )
        delta = upper - _EPOCH_UTC
        microseconds = (
            (delta.days * 86_400 + delta.seconds) * 1_000_000
            + delta.microseconds
        )
        return microseconds * 1_000

    def require_analysis(self) -> QualificationDecision:
        now = self._sample_clock()
        decision = self._runtime_decision(now)
        reasons = set(() if decision.eligible else decision.reasons)
        if now >= self._manifest.access_expires_at:
            reasons.discard(QualificationReason.ACCESS_EXPIRED)
            if (
                PermissionOperation.POST_EXPIRY_ANALYSIS
                not in self._manifest.permission.permitted_operations
            ):
                reasons.add(QualificationReason.MANDATORY_PERMISSION_MISSING)
        if now >= self._manifest.analysis_expires_at:
            reasons.add(QualificationReason.ANALYSIS_EXPIRED)
        if now >= self._manifest.raw_retention_until:
            reasons.add(QualificationReason.RETENTION_TOO_SHORT)
        if reasons:
            _decision_with_reasons(decision, reasons).require_eligible()
        return self._initial_decision

    def require_close(self) -> QualificationDecision:
        return self._require_access_operation(enforce_session_window=False)

    def poll_session(self) -> ProviderSessionPoll:
        decision = self._require_access_operation(enforce_session_window=False)
        now = self._latest_now
        assert now is not None
        return ProviderSessionPoll(
            decision=decision,
            session_ended=now >= self._request.session_end_utc,
        )

    def require_export(self) -> QualificationDecision:
        decision = self.require_analysis()
        if not decision.export_allowed:
            raise ProviderGateError(
                QualificationReason.MANDATORY_PERMISSION_MISSING,
                "provider operation denied: publication permission missing",
            )
        return decision

    def seconds_until_access_expiry(self) -> float:
        now = self._sample_clock()
        decision = self._runtime_decision(now)
        decision.require_eligible()
        delta = self._manifest.access_expires_at - now
        microseconds = (
            (delta.days * 86_400 + delta.seconds) * 1_000_000
            + delta.microseconds
        )
        if microseconds <= 0:
            raise ProviderGateError(
                QualificationReason.ACCESS_EXPIRED,
                "provider operation denied: access_expired",
            )
        return microseconds / 1_000_000
