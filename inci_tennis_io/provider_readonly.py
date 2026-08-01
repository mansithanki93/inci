"""Offline-only Sportradar candidate artifact and route contracts."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from inci_tennis_adapters.candidate_contracts import (
    CandidateProviderBindingV1,
    CandidateQualificationDecisionV1,
    CandidateQuotaClosureV1,
)
from inci_tennis_expert.contracts import (
    ArtifactPin,
    BindingUniverse,
    MatchFormat,
    canonical_expert_bytes,
    compute_expert_provider_source_lineage_sha256,
)
from inci_tennis_expert.match_binding import decode_binding_universe
from inci_tennis_io.ports import (
    CandidateObservationStartupAuthorityV1,
    CandidateQualificationAppendReceiptV1,
    CandidateQualificationOutputWriterV1,
    CandidateSourceSealsV1,
    SportradarCandidatePreparedReadV1,
)
from tennis_v1.adapter_contract import AdapterUsagePlan, ProviderQuotas
from tennis_v1.canonical import canonical_json_bytes
from tennis_v1.capture import (
    issue_capture_authority,
    safe_provenance,
)
from tennis_v1.entitlements import (
    CoverageStratum,
    QualificationReason,
    REQUIRED_QUALIFICATION_CAPABILITIES,
    RequestedStratum,
)
from tennis_v1.events import (
    CaptureAuthority,
    CapturedInput,
    SessionCaptureAuthorizer,
    SessionManifest,
    SourceKind,
)
from tennis_v1.session import session_manifest_sha256


_MAX_SIGNED_64 = 9_223_372_036_854_775_807
_DAY_NS = 86_400_000_000_000
_MINUTE_NS = 60_000_000_000
_HOUR_NS = 3_600_000_000_000
_MAX_MANIFEST_BYTES = 65_536
_MAX_AUTHORIZATION_BYTES = 32_768
_MAX_BINDING_BYTES = 1_048_576
_MAX_BINDING_REVIEW_BYTES = 16_384
_MAX_JSON_DEPTH = 16
_MAX_JSON_NODES = 10_000
_MAXIMUM_TRACE_BYTES = 268_435_456
_TRACE_BYTES_PER_ATTEMPT = 4_096 + (2 * 1_048_576 + 4_096) + 8_192
_TRACE_TERMINAL_BYTES = 4_096
_SAFE_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z"
_SHA256_PATTERN = r"[0-9a-f]{64}\Z"
_REQUIRED_CAPABILITIES = tuple(sorted(REQUIRED_QUALIFICATION_CAPABILITIES))
_SUPPORTED_FORMATS = (
    MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS.value,
    MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS.value,
)
_SESSION_NAMESPACE = uuid.UUID("8f4c1777-5fea-521a-aaab-60afdc79e328")
_QUOTA_FIELDS = (
    "requests_per_rolling_60_seconds",
    "requests_per_utc_calendar_day",
    "requests_per_rolling_second",
    "max_connections",
    "max_subscriptions",
    "resync_requests_per_rolling_hour",
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "artifact_id",
        "artifact_created_wall_ns",
        "provider_id",
        "product_tier",
        "source_lineage_id",
        "terms_version",
        "permission_artifact_sha256",
        "authorization_artifact_sha256",
        "credential_env_names",
        "declared_quotas",
        "session_start_wall_ns",
        "session_end_wall_ns",
        "required_retention_until_ns",
        "access_expires_at_ns",
        "analysis_expires_at_ns",
        "requested_provider_match_ids",
        "required_candidate_capabilities",
        "required_strata",
        "binding_manifest_path",
        "binding_manifest_artifact_id",
        "binding_manifest_sha256",
        "binding_review_path",
        "binding_review_artifact_id",
        "binding_review_sha256",
        "manifest_core_sha256",
    }
)
_AUTHORIZATION_KEYS = frozenset(
    {
        "schema_version",
        "artifact_id",
        "artifact_created_wall_ns",
        "candidate_manifest_core_sha256",
        "decision",
        "reviewer_id",
        "reviewed_wall_ns",
        "allowed_provider_id",
        "allowed_product_tier",
        "allowed_duration_seconds",
        "allowed_match_ids",
        "required_candidate_capabilities",
        "allowed_strata_sha256",
        "publication_allowed",
        "authorization_evidence_sha256",
    }
)
_STRATUM_KEYS = frozenset(
    {
        "sport",
        "tour",
        "competition_tier",
        "match_format",
        "round_code",
        "matches",
    }
)

SPORTRADAR_CANDIDATE_USAGE = AdapterUsagePlan(
    startup_requests_fixed=0,
    startup_requests_per_match=1,
    steady_requests_per_minute_fixed=0,
    steady_requests_per_minute_per_match=6,
    resync_requests_per_match=1,
    max_resyncs_per_match_per_hour=2,
    max_connections=1,
    subscriptions_per_match=1,
)
SPORTRADAR_CANDIDATE_ORIGIN = "https://api.sportradar.com"
SPORTRADAR_CANDIDATE_SUMMARY_PATH = (
    "/tennis/{product_tier}/v3/en/sport_events/"
    "{provider_match_id}/summary.json"
)
SPORTRADAR_CANDIDATE_TIMELINE_PATH = (
    "/tennis/{product_tier}/v3/en/sport_events/"
    "{provider_match_id}/timeline.json"
)
SPORTRADAR_CANDIDATE_METHOD = "GET"
SPORTRADAR_CANDIDATE_CONNECT_TIMEOUT_SECONDS = 3
SPORTRADAR_CANDIDATE_READ_TIMEOUT_SECONDS = 10
SPORTRADAR_CANDIDATE_TOTAL_DEADLINE_SECONDS = 15
SPORTRADAR_CANDIDATE_MAXIMUM_BODY_BYTES = 1_048_576
SPORTRADAR_CANDIDATE_MAXIMUM_ACTIVE_CONNECTIONS = 1
SPORTRADAR_CANDIDATE_CREDENTIAL_ENV_NAMES = ("SPORTRADAR_API_KEY",)
SPORTRADAR_CANDIDATE_ACCEPTED_STATUS = 200
SPORTRADAR_CANDIDATE_ACCEPTED_MEDIA_TYPE = "application/json"
SPORTRADAR_CANDIDATE_AUTOMATIC_RETRIES = 0
SPORTRADAR_CANDIDATE_ACCEPTED_REDIRECTS = 0
SPORTRADAR_CANDIDATE_CONNECTION_EPOCH = 1
SPORTRADAR_CANDIDATE_POLLING_INTERVAL_SECONDS = 10


class CandidateOfflineValidationError(ValueError):
    """A fixed, non-secret-bearing offline rejection."""

    def __init__(self) -> None:
        super().__init__("candidate_offline_validation_failed")


class CandidateObservationUnavailable(RuntimeError):
    """The Task-7 build has no observation startup authority issuer."""

    def __init__(self) -> None:
        super().__init__("candidate_observation_unavailable")


class _DuplicateKey(ValueError):
    pass


class _JsonNumber(ValueError):
    pass


def _fail() -> None:
    raise CandidateOfflineValidationError()


def _object_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _parse_integer(token: str) -> int:
    if len(token.removeprefix("-")) > 19:
        raise _JsonNumber
    try:
        value = int(token)
    except (OverflowError, ValueError):
        raise _JsonNumber from None
    if value < 0 or value > _MAX_SIGNED_64:
        raise _JsonNumber
    return value


def _reject_number(_: str) -> object:
    raise _JsonNumber


def _validate_json_tree(value: object, *, depth: int = 0) -> int:
    if depth > _MAX_JSON_DEPTH:
        _fail()
    if value is None or type(value) in (str, bool, int):
        return 1
    if type(value) is list:
        count = 1
        for item in value:
            count += _validate_json_tree(item, depth=depth + 1)
            if count > _MAX_JSON_NODES:
                _fail()
        return count
    if type(value) is dict:
        count = 1
        for key, item in value.items():
            if type(key) is not str:
                _fail()
            count += 1 + _validate_json_tree(item, depth=depth + 1)
            if count > _MAX_JSON_NODES:
                _fail()
        return count
    _fail()
    raise AssertionError


def _decode_canonical_json(payload: bytes) -> dict[str, object]:
    if type(payload) is not bytes or payload.startswith(b"\xef\xbb\xbf"):
        _fail()
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_int=_parse_integer,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKey,
        _JsonNumber,
        RecursionError,
        OverflowError,
        ValueError,
    ):
        _fail()
    _validate_json_tree(value)
    if type(value) is not dict:
        _fail()
    try:
        canonical = canonical_json_bytes(value)
    except (TypeError, ValueError):
        _fail()
    if payload != canonical:
        _fail()
    return value


def _shape(
    value: object,
    keys: frozenset[str],
) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != keys:
        _fail()
    return value


def _integer(
    value: object,
    *,
    minimum: int = 0,
    maximum: int = _MAX_SIGNED_64,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()
    return value


def _text(value: object) -> str:
    if type(value) is not str:
        _fail()
    return value


def _safe_id(value: object) -> str:
    text = _text(value)
    if re.fullmatch(_SAFE_ID_PATTERN, text, flags=re.ASCII) is None:
        _fail()
    return text


def _digest(value: object) -> str:
    text = _text(value)
    if re.fullmatch(_SHA256_PATTERN, text, flags=re.ASCII) is None:
        _fail()
    return text


def _exact_external_path(value: object) -> str:
    text = _text(value)
    if (
        not text
        or len(text.encode("utf-8")) > 4_096
        or "\x00" in text
        or not os.path.isabs(text)
        or text == os.path.sep
        or os.path.normpath(text) != text
        or ".." in Path(text).parts
    ):
        _fail()
    return text


_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
)


def _reject_git_marker(directory_descriptor: int) -> None:
    try:
        os.stat(
            ".git",
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    except OSError:
        _fail()
    _fail()


def _require_directory_parent(
    *,
    parent_descriptor: int,
    child_descriptor: int,
) -> None:
    backlink_descriptor = -1
    try:
        backlink_descriptor = os.open(
            "..",
            _DIRECTORY_OPEN_FLAGS,
            dir_fd=child_descriptor,
        )
        parent = os.fstat(parent_descriptor)
        backlink = os.fstat(backlink_descriptor)
        if (parent.st_dev, parent.st_ino) != (
            backlink.st_dev,
            backlink.st_ino,
        ):
            _fail()
    finally:
        if backlink_descriptor >= 0:
            try:
                os.close(backlink_descriptor)
            except OSError:
                pass


def _open_external_parent(path: str) -> tuple[int, str]:
    checked = _exact_external_path(path)
    components = Path(checked).parts
    if len(components) < 2 or components[0] != os.path.sep:
        _fail()
    descriptor = -1
    try:
        descriptor = os.open(os.path.sep, _DIRECTORY_OPEN_FLAGS)
        _reject_git_marker(descriptor)
        for component in components[1:-1]:
            before = os.stat(
                component,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if (
                stat.S_ISLNK(before.st_mode)
                or not stat.S_ISDIR(before.st_mode)
            ):
                _fail()
            child = os.open(
                component,
                _DIRECTORY_OPEN_FLAGS,
                dir_fd=descriptor,
            )
            after = os.fstat(child)
            if (before.st_dev, before.st_ino) != (
                after.st_dev,
                after.st_ino,
            ):
                os.close(child)
                _fail()
            _require_directory_parent(
                parent_descriptor=descriptor,
                child_descriptor=child,
            )
            _reject_git_marker(child)
            os.close(descriptor)
            descriptor = child
        if components[-1] == ".git":
            _fail()
        return descriptor, components[-1]
    except CandidateOfflineValidationError:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
    except (OSError, OverflowError, ValueError):
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        _fail()
    raise AssertionError


def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_external_file(path: str, *, maximum_bytes: int) -> bytes:
    checked = _exact_external_path(path)
    parent_descriptor = -1
    descriptor = -1
    try:
        parent_descriptor, basename = _open_external_parent(checked)
        before_path = os.stat(
            basename,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if stat.S_ISLNK(before_path.st_mode):
            _fail()
        descriptor = os.open(
            basename,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum_bytes
            or (before.st_dev, before.st_ino)
            != (before_path.st_dev, before_path.st_ino)
        ):
            _fail()
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        after_path = os.stat(
            basename,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            len(payload) < 1
            or len(payload) > maximum_bytes
            or len(payload) != before.st_size
            or _stat_identity(before) != _stat_identity(after)
            or (after.st_dev, after.st_ino)
            != (after_path.st_dev, after_path.st_ino)
            or stat.S_ISLNK(after_path.st_mode)
        ):
            _fail()
        return payload
    except CandidateOfflineValidationError:
        raise
    except (OSError, OverflowError, ValueError):
        _fail()
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if parent_descriptor >= 0:
            try:
                os.close(parent_descriptor)
            except OSError:
                pass
    raise AssertionError


def _validate_output_directory(path: str) -> None:
    checked = _exact_external_path(path)
    parent_descriptor = -1
    descriptor = -1
    try:
        parent_descriptor, basename = _open_external_parent(checked)
        before = os.stat(
            basename,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISDIR(before.st_mode)
        ):
            _fail()
        descriptor = os.open(
            basename,
            _DIRECTORY_OPEN_FLAGS,
            dir_fd=parent_descriptor,
        )
        value = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (
            value.st_dev,
            value.st_ino,
        ):
            _fail()
        _require_directory_parent(
            parent_descriptor=parent_descriptor,
            child_descriptor=descriptor,
        )
        _reject_git_marker(descriptor)
    except CandidateOfflineValidationError:
        raise
    except OSError:
        _fail()
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if parent_descriptor >= 0:
            try:
                os.close(parent_descriptor)
            except OSError:
                pass
    if value.st_uid != os.geteuid() or stat.S_IMODE(value.st_mode) != 0o700:
        _fail()


def _candidate_digest(domain: bytes, projection: object) -> str:
    try:
        return sha256(domain + canonical_expert_bytes(projection)).hexdigest()
    except (TypeError, ValueError):
        _fail()
    raise AssertionError


def _parse_quotas(value: object) -> ProviderQuotas:
    root = _shape(value, frozenset(_QUOTA_FIELDS))
    values = {
        name: _integer(root[name])
        for name in _QUOTA_FIELDS
    }
    return ProviderQuotas(**values)


def _quota_projection(
    value: ProviderQuotas,
) -> tuple[tuple[str, int], ...]:
    if type(value) is not ProviderQuotas:
        _fail()
    return tuple(
        (name, _integer(getattr(value, name)))
        for name in _QUOTA_FIELDS
    )


def _usage_projection(
    value: AdapterUsagePlan,
) -> tuple[tuple[str, int], ...]:
    if type(value) is not AdapterUsagePlan:
        _fail()
    return (
        ("startup_requests_fixed", value.startup_requests_fixed),
        ("startup_requests_per_match", value.startup_requests_per_match),
        (
            "steady_requests_per_minute_fixed",
            value.steady_requests_per_minute_fixed,
        ),
        (
            "steady_requests_per_minute_per_match",
            value.steady_requests_per_minute_per_match,
        ),
        ("resync_requests_per_match", value.resync_requests_per_match),
        (
            "max_resyncs_per_match_per_hour",
            value.max_resyncs_per_match_per_hour,
        ),
        ("max_connections", value.max_connections),
        ("subscriptions_per_match", value.subscriptions_per_match),
    )


def _parse_capabilities(value: object) -> tuple[str, ...]:
    if type(value) is not list or len(value) > len(
        _REQUIRED_CAPABILITIES
    ):
        _fail()
    result = tuple(_text(item) for item in value)
    if (
        result != tuple(sorted(set(result)))
        or not set(result).issubset(_REQUIRED_CAPABILITIES)
    ):
        _fail()
    return result


def _stratum_projection(
    value: RequestedStratum,
) -> tuple[tuple[str, object], ...]:
    if type(value) is not RequestedStratum:
        _fail()
    if type(value.stratum) is not CoverageStratum:
        _fail()
    return (
        ("sport", value.stratum.sport),
        ("tour", value.stratum.tour),
        ("competition_tier", value.stratum.competition_tier),
        ("match_format", value.stratum.match_format),
        ("round_code", value.stratum.round_code),
        ("matches", value.matches),
    )


def _parse_strata(value: object) -> tuple[RequestedStratum, ...]:
    if type(value) is not list or not 1 <= len(value) <= 10:
        _fail()
    strata: list[RequestedStratum] = []
    previous: bytes | None = None
    for item in value:
        root = _shape(item, _STRATUM_KEYS)
        sport = _safe_id(root["sport"])
        tour = _safe_id(root["tour"])
        tier = _safe_id(root["competition_tier"])
        match_format = _safe_id(root["match_format"])
        round_code = _safe_id(root["round_code"])
        matches = _integer(root["matches"], minimum=1, maximum=10)
        if sport != "tennis":
            _fail()
        candidate = RequestedStratum(
            stratum=CoverageStratum(
                sport=sport,
                tour=tour,
                competition_tier=tier,
                match_format=match_format,
                round_code=round_code,
            ),
            matches=matches,
        )
        encoded = canonical_expert_bytes(_stratum_projection(candidate))
        if previous is not None and encoded <= previous:
            _fail()
        previous = encoded
        strata.append(candidate)
    return tuple(strata)


def _parse_match_ids(value: object) -> tuple[str, ...]:
    if type(value) is not list or not 1 <= len(value) <= 10:
        _fail()
    result = tuple(_safe_id(item) for item in value)
    if result != tuple(sorted(set(result))):
        _fail()
    return result


def candidate_quota_demand(
    *,
    requested_matches: int,
    session_start_wall_ns: int,
    session_end_wall_ns: int,
) -> ProviderQuotas:
    matches = _integer(requested_matches, minimum=1, maximum=10)
    start = _integer(session_start_wall_ns)
    end = _integer(session_end_wall_ns)
    if start >= end:
        _fail()
    startup = matches
    steady_minute = 6 * matches
    resync_hour = 2 * matches
    worst_cluster = startup + steady_minute + resync_hour
    first_day = start // _DAY_NS
    final_day = (end - 1) // _DAY_NS
    day_demands: list[int] = []
    for day_index in range(first_day, final_day + 1):
        day_start = day_index * _DAY_NS
        day_end = day_start + _DAY_NS
        overlap = min(end, day_end) - max(start, day_start)
        if overlap <= 0:
            continue
        day_demands.append(
            (startup if day_start <= start < day_end else 0)
            + ((overlap + _MINUTE_NS - 1) // _MINUTE_NS)
            * steady_minute
            + ((overlap + _HOUR_NS - 1) // _HOUR_NS)
            * resync_hour
        )
    if not day_demands:
        _fail()
    return ProviderQuotas(
        requests_per_rolling_60_seconds=worst_cluster,
        requests_per_utc_calendar_day=max(day_demands),
        requests_per_rolling_second=worst_cluster,
        max_connections=1,
        max_subscriptions=matches,
        resync_requests_per_rolling_hour=resync_hour,
    )


def candidate_maximum_trace_bytes(
    *,
    requested_matches: int,
    duration_seconds: int,
) -> int:
    matches = _integer(requested_matches, minimum=1, maximum=10)
    duration = _integer(duration_seconds, minimum=1, maximum=3_600)
    polls = (duration - 1) // 10
    resyncs = min(2, polls)
    attempts = matches * (1 + polls + resyncs)
    return attempts * _TRACE_BYTES_PER_ATTEMPT + _TRACE_TERMINAL_BYTES


def _quota_covers(
    declared: ProviderQuotas,
    demand: ProviderQuotas,
) -> bool:
    return all(
        getattr(declared, name) >= getattr(demand, name)
        for name in _QUOTA_FIELDS
    )


@dataclass(frozen=True, slots=True, repr=False)
class ValidatedCandidateOfflineArtifactsV1:
    schema_version: int
    artifact_created_wall_ns: int
    provider_id: str
    product_tier: str
    source_lineage_id: str
    terms_version: str
    permission_artifact_sha256: str
    candidate_manifest_sha256: str
    manifest_core_sha256: str
    candidate_authorization_sha256: str
    authorization_evidence_sha256: str
    binding_manifest_sha256: str
    binding_review_sha256: str
    provider_source_lineage_sha256: str
    declared_quotas: ProviderQuotas
    demand_quotas: ProviderQuotas
    requested_provider_match_ids: tuple[str, ...]
    required_candidate_capabilities: tuple[str, ...]
    required_strata: tuple[RequestedStratum, ...]
    session_start_wall_ns: int
    session_end_wall_ns: int
    required_retention_until_ns: int
    access_expires_at_ns: int
    analysis_expires_at_ns: int
    duration_seconds: int
    maximum_candidate_trace_bytes: int
    auth_contract_sha256: str
    allowed_strata_sha256: str
    universe: BindingUniverse

    def __post_init__(self) -> None:
        if type(self) is not ValidatedCandidateOfflineArtifactsV1:
            raise TypeError("exact offline candidate artifacts required")
        _integer(self.schema_version, minimum=1, maximum=1)
        _integer(self.artifact_created_wall_ns)
        if self.provider_id != "sportradar":
            _fail()
        _safe_id(self.product_tier)
        _safe_id(self.source_lineage_id)
        _safe_id(self.terms_version)
        for value in (
            self.permission_artifact_sha256,
            self.candidate_manifest_sha256,
            self.manifest_core_sha256,
            self.candidate_authorization_sha256,
            self.authorization_evidence_sha256,
            self.binding_manifest_sha256,
            self.binding_review_sha256,
            self.provider_source_lineage_sha256,
            self.auth_contract_sha256,
            self.allowed_strata_sha256,
        ):
            _digest(value)
        _quota_projection(self.declared_quotas)
        _quota_projection(self.demand_quotas)
        if (
            self.requested_provider_match_ids
            != tuple(sorted(set(self.requested_provider_match_ids)))
            or not 1 <= len(self.requested_provider_match_ids) <= 10
        ):
            _fail()
        for item in self.requested_provider_match_ids:
            _safe_id(item)
        if (
            type(self.required_candidate_capabilities) is not tuple
            or self.required_candidate_capabilities
            != tuple(
                sorted(set(self.required_candidate_capabilities))
            )
            or not set(
                self.required_candidate_capabilities
            ).issubset(_REQUIRED_CAPABILITIES)
        ):
            _fail()
        for item in self.required_candidate_capabilities:
            _text(item)
        if (
            type(self.required_strata) is not tuple
            or not 1 <= len(self.required_strata) <= 10
            or sum(item.matches for item in self.required_strata)
            != len(self.requested_provider_match_ids)
        ):
            _fail()
        prior_stratum: bytes | None = None
        for item in self.required_strata:
            projection = _stratum_projection(item)
            if item.stratum.sport != "tennis":
                _fail()
            for component in (
                item.stratum.tour,
                item.stratum.competition_tier,
                item.stratum.match_format,
                item.stratum.round_code,
            ):
                _safe_id(component)
            _integer(item.matches, minimum=1, maximum=10)
            encoded = canonical_expert_bytes(projection)
            if prior_stratum is not None and encoded <= prior_stratum:
                _fail()
            prior_stratum = encoded
        start = _integer(self.session_start_wall_ns)
        end = _integer(self.session_end_wall_ns)
        retention = _integer(self.required_retention_until_ns)
        access = _integer(self.access_expires_at_ns)
        analysis = _integer(self.analysis_expires_at_ns)
        duration = _integer(
            self.duration_seconds,
            minimum=1,
            maximum=3_600,
        )
        if not (
            self.artifact_created_wall_ns <= start
            < end
            <= access
            <= analysis
            and end < retention <= analysis
            and end - start == duration * 1_000_000_000
            and (retention - start) % 1_000_000_000 == 0
        ):
            _fail()
        if self.maximum_candidate_trace_bytes != candidate_maximum_trace_bytes(
            requested_matches=len(self.requested_provider_match_ids),
            duration_seconds=duration,
        ):
            _fail()
        expected_demand = candidate_quota_demand(
            requested_matches=len(self.requested_provider_match_ids),
            session_start_wall_ns=start,
            session_end_wall_ns=end,
        )
        if _quota_projection(
            self.demand_quotas
        ) != _quota_projection(expected_demand):
            _fail()
        if type(self.universe) is not BindingUniverse:
            _fail()
        BindingUniverse.__post_init__(self.universe)

    def __repr__(self) -> str:
        return "<ValidatedCandidateOfflineArtifactsV1 redacted>"


def _validate_binding_universe(
    *,
    universe: BindingUniverse,
    requested_provider_match_ids: tuple[str, ...],
    required_strata: tuple[RequestedStratum, ...],
    provider_source_lineage_sha256: str,
) -> None:
    if type(universe) is not BindingUniverse:
        _fail()
    BindingUniverse.__post_init__(universe)
    if len(universe.bindings) != len(requested_provider_match_ids):
        _fail()
    seen_matches: set[str] = set()
    observed_strata: dict[
        tuple[str, str, str, str, str],
        int,
    ] = {}
    revision_domains: set[str] = set()
    for binding, metadata in zip(
        universe.bindings,
        universe.metadata,
        strict=True,
    ):
        for identifier in (
            binding.provider_match_id,
            binding.canonical_match_id,
            binding.provider_home_player_id,
            binding.provider_away_player_id,
            metadata.canonical_home_player_id,
            metadata.canonical_away_player_id,
        ):
            _safe_id(identifier)
        if (
            binding.provider_source_id != "sportradar"
            or binding.source_lineage_sha256
            != provider_source_lineage_sha256
            or binding.provider_match_id not in requested_provider_match_ids
            or binding.provider_match_id in seen_matches
            or binding.match_format.value not in _SUPPORTED_FORMATS
        ):
            _fail()
        seen_matches.add(binding.provider_match_id)
        revision_domains.add(binding.revision_domain_id)
        key = (
            "tennis",
            metadata.tour_id,
            metadata.tier_id,
            binding.match_format.value,
            metadata.round_id,
        )
        observed_strata[key] = observed_strata.get(key, 0) + 1
    expected_strata = {
        (
            item.stratum.sport,
            item.stratum.tour,
            item.stratum.competition_tier,
            item.stratum.match_format,
            item.stratum.round_code,
        ): item.matches
        for item in required_strata
    }
    formats_supported = all(
        item.stratum.match_format in _SUPPORTED_FORMATS
        for item in required_strata
    )
    reduced_observed: dict[tuple[str, str, str, str], int] = {}
    for key, matches in observed_strata.items():
        reduced_key = (key[0], key[1], key[2], key[4])
        reduced_observed[reduced_key] = (
            reduced_observed.get(reduced_key, 0) + matches
        )
    reduced_expected: dict[tuple[str, str, str, str], int] = {}
    for item in required_strata:
        reduced_key = (
            item.stratum.sport,
            item.stratum.tour,
            item.stratum.competition_tier,
            item.stratum.round_code,
        )
        reduced_expected[reduced_key] = (
            reduced_expected.get(reduced_key, 0) + item.matches
        )
    if (
        seen_matches != set(requested_provider_match_ids)
        or len(revision_domains) != 1
        or (
            observed_strata != expected_strata
            if formats_supported
            else reduced_observed != reduced_expected
        )
    ):
        _fail()


def validate_sportradar_candidate_offline_artifacts(
    *,
    manifest_path: str,
    binding_path: str,
    duration_seconds: int,
    output_dir: str,
) -> ValidatedCandidateOfflineArtifactsV1:
    """Validate bounded external inputs without creating output or authority."""

    try:
        duration = _integer(
            duration_seconds,
            minimum=1,
            maximum=3_600,
        )
        manifest_input = _exact_external_path(manifest_path)
        authorization_input = _exact_external_path(binding_path)
        output = _exact_external_path(output_dir)
        if manifest_input == authorization_input:
            _fail()

        manifest_payload = _read_external_file(
            manifest_input,
            maximum_bytes=_MAX_MANIFEST_BYTES,
        )
        manifest = _shape(
            _decode_canonical_json(manifest_payload),
            _MANIFEST_KEYS,
        )
        _integer(manifest["schema_version"], minimum=1, maximum=1)
        _safe_id(manifest["artifact_id"])
        artifact_created = _integer(
            manifest["artifact_created_wall_ns"]
        )
        if manifest["provider_id"] != "sportradar":
            _fail()
        product_tier = _safe_id(manifest["product_tier"])
        source_lineage_id = _safe_id(manifest["source_lineage_id"])
        terms_version = _safe_id(manifest["terms_version"])
        permission_sha = _digest(
            manifest["permission_artifact_sha256"]
        )
        expected_authorization_sha = _digest(
            manifest["authorization_artifact_sha256"]
        )
        if manifest["credential_env_names"] != ["SPORTRADAR_API_KEY"]:
            _fail()
        declared = _parse_quotas(manifest["declared_quotas"])
        start = _integer(manifest["session_start_wall_ns"])
        end = _integer(manifest["session_end_wall_ns"])
        retention = _integer(
            manifest["required_retention_until_ns"]
        )
        access = _integer(manifest["access_expires_at_ns"])
        analysis = _integer(manifest["analysis_expires_at_ns"])
        if not (
            artifact_created <= start < end <= access <= analysis
            and end < retention <= analysis
            and end - start == duration * 1_000_000_000
            and (retention - start) % 1_000_000_000 == 0
        ):
            _fail()
        requested_match_ids = _parse_match_ids(
            manifest["requested_provider_match_ids"]
        )
        required_capabilities = _parse_capabilities(
            manifest["required_candidate_capabilities"]
        )
        required_strata = _parse_strata(manifest["required_strata"])
        if sum(item.matches for item in required_strata) != len(
            requested_match_ids
        ):
            _fail()
        binding_manifest_path = _exact_external_path(
            manifest["binding_manifest_path"]
        )
        binding_manifest_artifact_id = _safe_id(
            manifest["binding_manifest_artifact_id"]
        )
        binding_manifest_sha = _digest(
            manifest["binding_manifest_sha256"]
        )
        binding_review_path = _exact_external_path(
            manifest["binding_review_path"]
        )
        binding_review_artifact_id = _safe_id(
            manifest["binding_review_artifact_id"]
        )
        binding_review_sha = _digest(
            manifest["binding_review_sha256"]
        )
        input_paths = (
            manifest_input,
            authorization_input,
            binding_manifest_path,
            binding_review_path,
        )
        if len(set(input_paths)) != len(input_paths):
            _fail()
        core_value = {
            key: value
            for key, value in manifest.items()
            if key
            not in {
                "authorization_artifact_sha256",
                "binding_manifest_path",
                "binding_manifest_artifact_id",
                "binding_manifest_sha256",
                "binding_review_path",
                "binding_review_artifact_id",
                "binding_review_sha256",
                "manifest_core_sha256",
            }
        }
        manifest_core_sha = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-MANIFEST-CORE-V1\0"
            + canonical_json_bytes(core_value)
        ).hexdigest()
        if manifest["manifest_core_sha256"] != manifest_core_sha:
            _fail()
        manifest_sha = sha256(manifest_payload).hexdigest()

        authorization_payload = _read_external_file(
            authorization_input,
            maximum_bytes=_MAX_AUTHORIZATION_BYTES,
        )
        authorization = _shape(
            _decode_canonical_json(authorization_payload),
            _AUTHORIZATION_KEYS,
        )
        authorization_sha = sha256(authorization_payload).hexdigest()
        if authorization_sha != expected_authorization_sha:
            _fail()
        _integer(authorization["schema_version"], minimum=1, maximum=1)
        _safe_id(authorization["artifact_id"])
        authorization_created = _integer(
            authorization["artifact_created_wall_ns"]
        )
        allowed_duration = _integer(
            authorization["allowed_duration_seconds"],
            minimum=1,
            maximum=3_600,
        )
        reviewer_id = _safe_id(authorization["reviewer_id"])
        reviewed = _integer(authorization["reviewed_wall_ns"])
        if (
            authorization["candidate_manifest_core_sha256"]
            != manifest_core_sha
            or authorization["decision"]
            != "approved_for_candidate_read_only_observation"
            or authorization["allowed_provider_id"] != "sportradar"
            or authorization["allowed_product_tier"] != product_tier
            or allowed_duration != duration
            or authorization["allowed_match_ids"]
            != list(requested_match_ids)
            or _parse_capabilities(
                authorization["required_candidate_capabilities"]
            )
            != required_capabilities
            or authorization["publication_allowed"] is not False
            or reviewed > authorization_created
            or reviewed < artifact_created
            or authorization_created > start
            or not reviewer_id
        ):
            _fail()
        strata_projection = tuple(
            _stratum_projection(item)
            for item in required_strata
        )
        allowed_strata_sha = _candidate_digest(
            b"INCI-SPORTRADAR-CANDIDATE-REQUIRED-STRATA-V1\0",
            strata_projection,
        )
        if authorization["allowed_strata_sha256"] != allowed_strata_sha:
            _fail()
        authorization_evidence_sha = _digest(
            authorization["authorization_evidence_sha256"]
        )

        demand = candidate_quota_demand(
            requested_matches=len(requested_match_ids),
            session_start_wall_ns=start,
            session_end_wall_ns=end,
        )
        maximum_trace_bytes = candidate_maximum_trace_bytes(
            requested_matches=len(requested_match_ids),
            duration_seconds=duration,
        )

        binding_manifest_payload = _read_external_file(
            binding_manifest_path,
            maximum_bytes=_MAX_BINDING_BYTES,
        )
        binding_review_payload = _read_external_file(
            binding_review_path,
            maximum_bytes=_MAX_BINDING_REVIEW_BYTES,
        )
        if (
            sha256(binding_manifest_payload).hexdigest()
            != binding_manifest_sha
            or sha256(binding_review_payload).hexdigest()
            != binding_review_sha
        ):
            _fail()
        universe = decode_binding_universe(
            binding_manifest_payload,
            binding_review_payload,
            manifest_pin=ArtifactPin(
                binding_manifest_artifact_id,
                binding_manifest_sha,
            ),
            review_pin=ArtifactPin(
                binding_review_artifact_id,
                binding_review_sha,
            ),
        )
        provider_lineage_sha = (
            compute_expert_provider_source_lineage_sha256(
                "sportradar",
                product_tier,
                source_lineage_id,
                manifest_core_sha,
            )
        )
        _validate_binding_universe(
            universe=universe,
            requested_provider_match_ids=requested_match_ids,
            required_strata=required_strata,
            provider_source_lineage_sha256=provider_lineage_sha,
        )

        output_node = Path(output)
        if output in input_paths or any(
            Path(input_path).is_relative_to(output_node)
            or output_node.is_relative_to(Path(input_path))
            for input_path in input_paths
        ):
            _fail()
        _validate_output_directory(output)
        auth_contract_sha = sha256(
            b"INCI-AUTH-CONTRACT-V1\0"
            + canonical_json_bytes(
                {
                    "credential_env_names": ["SPORTRADAR_API_KEY"],
                    "mode": "api_key",
                }
            )
        ).hexdigest()
        return ValidatedCandidateOfflineArtifactsV1(
            schema_version=1,
            artifact_created_wall_ns=artifact_created,
            provider_id="sportradar",
            product_tier=product_tier,
            source_lineage_id=source_lineage_id,
            terms_version=terms_version,
            permission_artifact_sha256=permission_sha,
            candidate_manifest_sha256=manifest_sha,
            manifest_core_sha256=manifest_core_sha,
            candidate_authorization_sha256=authorization_sha,
            authorization_evidence_sha256=authorization_evidence_sha,
            binding_manifest_sha256=binding_manifest_sha,
            binding_review_sha256=binding_review_sha,
            provider_source_lineage_sha256=provider_lineage_sha,
            declared_quotas=declared,
            demand_quotas=demand,
            requested_provider_match_ids=requested_match_ids,
            required_candidate_capabilities=required_capabilities,
            required_strata=required_strata,
            session_start_wall_ns=start,
            session_end_wall_ns=end,
            required_retention_until_ns=retention,
            access_expires_at_ns=access,
            analysis_expires_at_ns=analysis,
            duration_seconds=duration,
            maximum_candidate_trace_bytes=maximum_trace_bytes,
            auth_contract_sha256=auth_contract_sha,
            allowed_strata_sha256=allowed_strata_sha,
            universe=universe,
        )
    except CandidateOfflineValidationError:
        raise
    except BaseException:
        raise CandidateOfflineValidationError() from None


def _make_sportradar_candidate_qualification_decision(
    *,
    eligible_for_candidate_observation: bool,
    reasons: tuple[QualificationReason, ...],
    binding: CandidateProviderBindingV1 | None,
    quota: CandidateQuotaClosureV1 | None,
) -> CandidateQualificationDecisionV1:
    """Construct a decision from exact closed-vocabulary reason values."""

    try:
        if (
            type(eligible_for_candidate_observation) is not bool
            or type(reasons) is not tuple
            or not reasons
            or any(
                type(item) is not QualificationReason
                for item in reasons
            )
        ):
            _fail()
        decision = CandidateQualificationDecisionV1._create(
            schema_version=1,
            eligible_for_candidate_observation=(
                eligible_for_candidate_observation
            ),
            reasons=reasons,
            binding=binding,
            quota=quota,
        )
        decision._validate()
        return decision
    except CandidateOfflineValidationError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise CandidateOfflineValidationError() from None


def _candidate_offline_qualification_reasons(
    artifacts: ValidatedCandidateOfflineArtifactsV1,
) -> tuple[QualificationReason, ...]:
    if type(artifacts) is not ValidatedCandidateOfflineArtifactsV1:
        raise TypeError("exact validated candidate artifacts required")
    try:
        ValidatedCandidateOfflineArtifactsV1.__post_init__(artifacts)
    except (TypeError, ValueError):
        raise CandidateOfflineValidationError() from None
    reasons: set[QualificationReason] = set()
    if artifacts.required_candidate_capabilities != _REQUIRED_CAPABILITIES:
        reasons.add(QualificationReason.CAPABILITY_MISSING)
    if any(
        item.stratum.match_format not in _SUPPORTED_FORMATS
        for item in artifacts.required_strata
    ):
        reasons.add(QualificationReason.FORMAT_UNSUPPORTED)
    if not _quota_covers(
        artifacts.declared_quotas,
        artifacts.demand_quotas,
    ):
        reasons.add(QualificationReason.QUOTA_INADEQUATE)
    if artifacts.maximum_candidate_trace_bytes > _MAXIMUM_TRACE_BYTES:
        reasons.add(
            QualificationReason.QUALIFICATION_CAPACITY_INADEQUATE
        )
    return tuple(sorted(reasons, key=lambda item: item.value))


def sportradar_candidate_offline_is_eligible(
    artifacts: ValidatedCandidateOfflineArtifactsV1,
) -> bool:
    """Return whether structurally valid artifacts clear offline policy."""

    return not _candidate_offline_qualification_reasons(artifacts)


def make_sportradar_candidate_offline_denial(
    *,
    artifacts: ValidatedCandidateOfflineArtifactsV1,
) -> CandidateQualificationDecisionV1 | None:
    """Build the exact candidate-only denial for offline policy failures."""

    reasons = _candidate_offline_qualification_reasons(artifacts)
    if not reasons:
        return None
    return _make_sportradar_candidate_qualification_decision(
        eligible_for_candidate_observation=False,
        reasons=reasons,
        binding=None,
        quota=None,
    )


def make_sportradar_candidate_evidence_mismatch_decision(
) -> CandidateQualificationDecisionV1:
    """Build the fixed candidate-only evidence-mismatch decision."""

    return _make_sportradar_candidate_qualification_decision(
        eligible_for_candidate_observation=False,
        reasons=(
            QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
        ),
        binding=None,
        quota=None,
    )


def make_sportradar_candidate_eligible_decision(
    *,
    binding: CandidateProviderBindingV1,
    quota: CandidateQuotaClosureV1,
) -> CandidateQualificationDecisionV1:
    """Build the fixed eligible candidate-only decision."""

    return _make_sportradar_candidate_qualification_decision(
        eligible_for_candidate_observation=True,
        reasons=(QualificationReason.ELIGIBLE,),
        binding=binding,
        quota=quota,
    )


def _candidate_session_derivations(
    *,
    artifacts: ValidatedCandidateOfflineArtifactsV1,
    source_seals: CandidateSourceSealsV1,
    quota: CandidateQuotaClosureV1,
) -> tuple[str, str, str, str, str]:
    strata = tuple(
        _stratum_projection(item)
        for item in artifacts.required_strata
    )
    authorization_evidence_sha256 = _candidate_digest(
        b"INCI-SPORTRADAR-CANDIDATE-AUTHORIZATION-EVIDENCE-V1\0",
        (
            (
                "candidate_source_seals_sha256",
                source_seals.candidate_source_seals_sha256,
            ),
            (
                "permission_artifact_sha256",
                artifacts.permission_artifact_sha256,
            ),
            ("manifest_core_sha256", artifacts.manifest_core_sha256),
            (
                "binding_manifest_sha256",
                artifacts.binding_manifest_sha256,
            ),
            (
                "binding_review_sha256",
                artifacts.binding_review_sha256,
            ),
            (
                "requested_provider_match_ids",
                artifacts.requested_provider_match_ids,
            ),
            (
                "required_candidate_capabilities",
                artifacts.required_candidate_capabilities,
            ),
            ("required_strata", strata),
            ("duration_seconds", artifacts.duration_seconds),
            ("usage", _usage_projection(SPORTRADAR_CANDIDATE_USAGE)),
            (
                "declared_quotas",
                _quota_projection(artifacts.declared_quotas),
            ),
        ),
    )
    research_request_sha256 = _candidate_digest(
        b"INCI-SPORTRADAR-CANDIDATE-RESEARCH-REQUEST-V1\0",
        (
            ("intended_use", "private_paper_evaluation"),
            ("session_start_wall_ns", artifacts.session_start_wall_ns),
            ("session_end_wall_ns", artifacts.session_end_wall_ns),
            (
                "required_retention_until_ns",
                artifacts.required_retention_until_ns,
            ),
            ("expiry_safety_margin_seconds", 60),
            (
                "required_raw_retention_seconds",
                (
                    artifacts.required_retention_until_ns
                    - artifacts.session_start_wall_ns
                )
                // 1_000_000_000,
            ),
            (
                "requested_matches",
                len(artifacts.requested_provider_match_ids),
            ),
            (
                "required_candidate_capabilities",
                artifacts.required_candidate_capabilities,
            ),
            ("required_strata", strata),
        ),
    )
    permission_scope_sha256 = _candidate_digest(
        b"INCI-SPORTRADAR-CANDIDATE-PERMISSION-SCOPE-V1\0",
        (
            ("provider_id", "sportradar"),
            ("product_tier", artifacts.product_tier),
            ("terms_version", artifacts.terms_version),
            (
                "permission_artifact_sha256",
                artifacts.permission_artifact_sha256,
            ),
            ("intended_use", "private_paper_evaluation"),
            ("publication_allowed", False),
        ),
    )
    session_name_sha256 = _candidate_digest(
        b"INCI-SPORTRADAR-CANDIDATE-SESSION-NAME-V1\0",
        (
            (
                "candidate_manifest_sha256",
                artifacts.candidate_manifest_sha256,
            ),
            ("manifest_core_sha256", artifacts.manifest_core_sha256),
            (
                "candidate_authorization_sha256",
                artifacts.candidate_authorization_sha256,
            ),
            (
                "candidate_source_seals_sha256",
                source_seals.candidate_source_seals_sha256,
            ),
            ("quota_closure_sha256", quota.quota_closure_sha256),
            (
                "match_binding_universe_sha256",
                artifacts.universe.universe_sha256,
            ),
            (
                "requested_provider_match_ids",
                artifacts.requested_provider_match_ids,
            ),
            ("session_start_wall_ns", artifacts.session_start_wall_ns),
            ("session_end_wall_ns", artifacts.session_end_wall_ns),
            (
                "retention_delete_by_ns",
                artifacts.required_retention_until_ns,
            ),
        ),
    )
    session_id = str(
        uuid.uuid5(_SESSION_NAMESPACE, session_name_sha256)
    )
    preobservation_trace_sha256 = _candidate_digest(
        b"INCI-SPORTRADAR-CANDIDATE-PREOBSERVATION-TRACE-V1\0",
        (
            ("trace_state", "empty_pre_observation"),
            ("session_id", session_id),
            (
                "candidate_manifest_sha256",
                artifacts.candidate_manifest_sha256,
            ),
            ("manifest_core_sha256", artifacts.manifest_core_sha256),
            (
                "candidate_authorization_sha256",
                artifacts.candidate_authorization_sha256,
            ),
            (
                "candidate_source_seals_sha256",
                source_seals.candidate_source_seals_sha256,
            ),
            ("quota_closure_sha256", quota.quota_closure_sha256),
            (
                "match_binding_universe_sha256",
                artifacts.universe.universe_sha256,
            ),
            (
                "requested_provider_match_ids",
                artifacts.requested_provider_match_ids,
            ),
            (
                "retention_delete_by_ns",
                artifacts.required_retention_until_ns,
            ),
        ),
    )
    return (
        authorization_evidence_sha256,
        research_request_sha256,
        permission_scope_sha256,
        session_id,
        preobservation_trace_sha256,
    )


def _require_candidate_session_inputs(
    *,
    artifacts: ValidatedCandidateOfflineArtifactsV1,
    source_seals: CandidateSourceSealsV1,
    decision: CandidateQualificationDecisionV1,
) -> tuple[CandidateProviderBindingV1, CandidateQuotaClosureV1]:
    if type(artifacts) is not ValidatedCandidateOfflineArtifactsV1:
        raise TypeError("exact validated candidate artifacts required")
    if type(source_seals) is not CandidateSourceSealsV1:
        raise TypeError("exact candidate source seals required")
    if type(decision) is not CandidateQualificationDecisionV1:
        raise TypeError("exact candidate decision required")
    try:
        ValidatedCandidateOfflineArtifactsV1.__post_init__(artifacts)
        source_seals._validate()
        decision._validate()
        if _candidate_offline_qualification_reasons(artifacts):
            _fail()
        if (
            decision.eligible_for_candidate_observation is not True
            or decision.reasons != (QualificationReason.ELIGIBLE,)
            or type(decision.binding) is not CandidateProviderBindingV1
            or type(decision.quota) is not CandidateQuotaClosureV1
        ):
            _fail()
        binding = decision.binding
        quota = decision.quota
        binding._validate()
        quota._validate()
        (
            authorization_evidence_sha256,
            research_request_sha256,
            permission_scope_sha256,
            session_id,
            preobservation_trace_sha256,
        ) = _candidate_session_derivations(
            artifacts=artifacts,
            source_seals=source_seals,
            quota=quota,
        )
        expected_lineage_sha256 = (
            compute_expert_provider_source_lineage_sha256(
                "sportradar",
                artifacts.product_tier,
                artifacts.source_lineage_id,
                artifacts.manifest_core_sha256,
            )
        )
        expected_auth_contract_sha256 = sha256(
            b"INCI-AUTH-CONTRACT-V1\0"
            + canonical_json_bytes(
                {
                    "credential_env_names": ["SPORTRADAR_API_KEY"],
                    "mode": "api_key",
                }
            )
        ).hexdigest()
        expected_allowed_strata_sha256 = _candidate_digest(
            b"INCI-SPORTRADAR-CANDIDATE-REQUIRED-STRATA-V1\0",
            tuple(
                _stratum_projection(item)
                for item in artifacts.required_strata
            ),
        )
        if (
            authorization_evidence_sha256
            != artifacts.authorization_evidence_sha256
            or expected_lineage_sha256
            != artifacts.provider_source_lineage_sha256
            or expected_auth_contract_sha256
            != artifacts.auth_contract_sha256
            or expected_allowed_strata_sha256
            != artifacts.allowed_strata_sha256
            or binding.provider_id != "sportradar"
            or binding.product_tier != artifacts.product_tier
            or binding.source_lineage_id != artifacts.source_lineage_id
            or binding.candidate_manifest_sha256
            != artifacts.candidate_manifest_sha256
            or binding.provider_manifest_canonical_sha256
            != artifacts.manifest_core_sha256
            or binding.provider_source_lineage_sha256
            != artifacts.provider_source_lineage_sha256
            or binding.candidate_authorization_sha256
            != artifacts.candidate_authorization_sha256
            or binding.permission_artifact_sha256
            != artifacts.permission_artifact_sha256
            or binding.match_binding_universe_sha256
            != artifacts.universe.universe_sha256
            or binding.binding_raw_artifact_sha256
            != artifacts.binding_manifest_sha256
            or binding.binding_review_artifact_sha256
            != artifacts.binding_review_sha256
            or binding.candidate_source_seals_sha256
            != source_seals.candidate_source_seals_sha256
            or binding.auth_contract_sha256
            != artifacts.auth_contract_sha256
            or binding.quota_closure_sha256
            != quota.quota_closure_sha256
            or binding.candidate_research_request_sha256
            != research_request_sha256
            or binding.session_id != session_id
            or binding.candidate_permission_scope_sha256
            != permission_scope_sha256
            or binding.candidate_preobservation_trace_sha256
            != preobservation_trace_sha256
            or binding.session_start_wall_ns
            != artifacts.session_start_wall_ns
            or binding.session_end_wall_ns
            != artifacts.session_end_wall_ns
            or binding.retention_delete_by_ns
            != artifacts.required_retention_until_ns
            or binding.access_expires_at_ns
            != artifacts.access_expires_at_ns
            or binding.analysis_expires_at_ns
            != artifacts.analysis_expires_at_ns
            or quota.usage != SPORTRADAR_CANDIDATE_USAGE
            or quota.declared != artifacts.declared_quotas
            or quota.demand != artifacts.demand_quotas
            or quota.requested_matches
            != len(artifacts.requested_provider_match_ids)
            or quota.duration_seconds != artifacts.duration_seconds
            or quota.polling_interval_seconds
            != SPORTRADAR_CANDIDATE_POLLING_INTERVAL_SECONDS
            or quota.maximum_candidate_trace_bytes
            != artifacts.maximum_candidate_trace_bytes
        ):
            _fail()
        return binding, quota
    except CandidateOfflineValidationError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise CandidateOfflineValidationError() from None


def build_sportradar_candidate_session_manifest(
    *,
    artifacts: ValidatedCandidateOfflineArtifactsV1,
    source_seals: CandidateSourceSealsV1,
    decision: CandidateQualificationDecisionV1,
) -> SessionManifest:
    """Construct the exact non-evaluable candidate capture manifest."""

    binding, quota = _require_candidate_session_inputs(
        artifacts=artifacts,
        source_seals=source_seals,
        decision=decision,
    )
    try:
        manifest = SessionManifest(
            schema_version=1,
            session_id=binding.session_id,
            created_wall_ns=artifacts.artifact_created_wall_ns,
            config_file_sha256=binding.candidate_manifest_sha256,
            config_canonical_sha256=(
                binding.provider_manifest_canonical_sha256
            ),
            code_sha256=binding.candidate_source_seals_sha256,
            research_request_sha256=(
                binding.candidate_research_request_sha256
            ),
            provider_id="sportradar",
            product_tier=binding.product_tier,
            source_lineage_id=binding.source_lineage_id,
            provider_manifest_file_sha256=(
                binding.candidate_manifest_sha256
            ),
            provider_manifest_canonical_sha256=(
                binding.provider_manifest_canonical_sha256
            ),
            entitlement_id_sha256=(
                binding.candidate_permission_scope_sha256
            ),
            terms_version=artifacts.terms_version,
            permission_artifact_sha256=(
                binding.permission_artifact_sha256
            ),
            qualification_artifact_sha256=(
                binding.candidate_authorization_sha256
            ),
            qualification_trace_sha256=(
                binding.candidate_preobservation_trace_sha256
            ),
            adapter_code_sha256=(
                source_seals.candidate_adapter_inventory_sha256
            ),
            auth_contract_sha256=binding.auth_contract_sha256,
            quota_contract_sha256=quota.quota_closure_sha256,
            session_end_ns=binding.session_end_wall_ns,
            required_retention_until_ns=(
                binding.retention_delete_by_ns
            ),
            access_expires_at_ns=binding.access_expires_at_ns,
            analysis_expires_at_ns=binding.analysis_expires_at_ns,
            research_evaluable=False,
        )
        SessionManifest.__post_init__(manifest)
        _digest(session_manifest_sha256(manifest))
        return manifest
    except CandidateOfflineValidationError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise CandidateOfflineValidationError() from None


_CANDIDATE_CAPTURE_AUTHORIZER_SENTINEL = object()


class _CandidateCaptureAuthorizerV1:
    __slots__ = (
        "_artifacts",
        "_source_seals",
        "_decision",
        "_session_manifest",
        "_session_manifest_sha256",
        "_owner_pid",
        "_owner_thread",
    )

    def __init__(
        self,
        *_: object,
        **__: object,
    ) -> None:
        raise TypeError("private candidate capture authorizer")

    @classmethod
    def _create(
        cls,
        *,
        artifacts: ValidatedCandidateOfflineArtifactsV1,
        source_seals: CandidateSourceSealsV1,
        decision: CandidateQualificationDecisionV1,
        session_manifest: SessionManifest,
        sentinel: object,
    ) -> _CandidateCaptureAuthorizerV1:
        if (
            cls is not _CandidateCaptureAuthorizerV1
            or sentinel is not _CANDIDATE_CAPTURE_AUTHORIZER_SENTINEL
            or type(session_manifest) is not SessionManifest
        ):
            raise TypeError("private candidate capture authorizer")
        expected = build_sportradar_candidate_session_manifest(
            artifacts=artifacts,
            source_seals=source_seals,
            decision=decision,
        )
        if session_manifest != expected:
            _fail()
        instance = object.__new__(cls)
        object.__setattr__(instance, "_artifacts", artifacts)
        object.__setattr__(instance, "_source_seals", source_seals)
        object.__setattr__(instance, "_decision", decision)
        object.__setattr__(
            instance,
            "_session_manifest",
            session_manifest,
        )
        object.__setattr__(
            instance,
            "_session_manifest_sha256",
            session_manifest_sha256(session_manifest),
        )
        object.__setattr__(instance, "_owner_pid", os.getpid())
        object.__setattr__(
            instance,
            "_owner_thread",
            threading.current_thread(),
        )
        return instance

    @property
    def session_manifest(self) -> SessionManifest:
        return self._session_manifest

    def authorize_capture(
        self,
        authority: CaptureAuthority,
        captured: CapturedInput,
    ) -> None:
        try:
            if (
                os.getpid() != self._owner_pid
                or threading.current_thread() is not self._owner_thread
                or type(authority) is not CaptureAuthority
                or type(captured) is not CapturedInput
            ):
                raise CandidateObservationUnavailable()
            binding, _ = _require_candidate_session_inputs(
                artifacts=self._artifacts,
                source_seals=self._source_seals,
                decision=self._decision,
            )
            if (
                self._session_manifest_sha256
                != session_manifest_sha256(self._session_manifest)
                or captured.session_id != binding.session_id
                or captured.source_kind is not SourceKind.PROVIDER
                or captured.source_id != "sportradar"
                or captured.source_entity_id
                not in self._artifacts.requested_provider_match_ids
                or captured.endpoint_id != "sportradar-api"
                or captured.endpoint_state.value != "safe_original"
                or captured.channel_id != "sportradar-rest"
                or captured.channel_state.value != "safe_original"
                or captured.request_id != "<redacted>"
                or captured.request_id_state.value != "redacted"
                or captured.connection_epoch
                != SPORTRADAR_CANDIDATE_CONNECTION_EPOCH
                or captured.event_version != 1
                or captured.event_type
                not in {
                    "sportradar_tennis_summary_v3",
                    "sportradar_tennis_timeline_v3",
                    "sportradar_tennis_transport_error_v1",
                }
                or captured.retention_delete_by_ns
                != binding.retention_delete_by_ns
                or not (
                    binding.session_start_wall_ns
                    <= captured.local_wall_ns
                    < binding.session_end_wall_ns
                )
            ):
                raise CandidateObservationUnavailable()
            transport_error = (
                captured.event_type
                == "sportradar_tennis_transport_error_v1"
            )
            if transport_error != (
                captured.source_wall_ns is None
                and captured.source_generated_ns is None
                and captured.provider_sequence is None
            ):
                raise CandidateObservationUnavailable()
        except CandidateObservationUnavailable:
            raise
        except BaseException:
            raise CandidateObservationUnavailable() from None

    def __repr__(self) -> str:
        return "<CandidateCaptureAuthorizerV1 redacted>"

    def __setattr__(self, _: str, __: object) -> None:
        raise TypeError("candidate capture authorizer is immutable")

    def __delattr__(self, _: str) -> None:
        raise TypeError("candidate capture authorizer is immutable")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("candidate capture authorizer cannot be subclassed")

    def __copy__(self):
        raise TypeError("candidate capture authorizer cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("candidate capture authorizer cannot be copied")

    def __reduce__(self):
        raise TypeError("candidate capture authorizer cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("candidate capture authorizer cannot be pickled")

    def __getstate__(self):
        raise TypeError("candidate capture authorizer cannot be pickled")


def _candidate_observation_clock_unavailable() -> int:
    raise CandidateObservationUnavailable()


def issue_sportradar_candidate_capture_authorities(
    *,
    artifacts: ValidatedCandidateOfflineArtifactsV1,
    source_seals: CandidateSourceSealsV1,
    decision: CandidateQualificationDecisionV1,
    session_manifest: SessionManifest,
) -> tuple[CaptureAuthority, ...]:
    """Issue inert real capture authorities without sampling a clock."""

    authorizer: SessionCaptureAuthorizer = (
        _CandidateCaptureAuthorizerV1._create(
            artifacts=artifacts,
            source_seals=source_seals,
            decision=decision,
            session_manifest=session_manifest,
            sentinel=_CANDIDATE_CAPTURE_AUTHORIZER_SENTINEL,
        )
    )
    try:
        authorities = tuple(
            issue_capture_authority(
                session_authorizer=authorizer,
                source_kind=SourceKind.PROVIDER,
                source_id="sportradar",
                source_entity_id=provider_match_id,
                endpoint=safe_provenance("sportradar-api"),
                channel=safe_provenance("sportradar-rest"),
                connection_epoch=(
                    SPORTRADAR_CANDIDATE_CONNECTION_EPOCH
                ),
                allowed_content_types=(
                    SPORTRADAR_CANDIDATE_ACCEPTED_MEDIA_TYPE,
                ),
                wall_clock_ns=_candidate_observation_clock_unavailable,
                monotonic_clock_ns=(
                    _candidate_observation_clock_unavailable
                ),
                clock_uncertainty_ns=(
                    _candidate_observation_clock_unavailable
                ),
            )
            for provider_match_id in (
                artifacts.requested_provider_match_ids
            )
        )
        if (
            len(authorities)
            != len(artifacts.requested_provider_match_ids)
            or any(type(item) is not CaptureAuthority for item in authorities)
        ):
            _fail()
        return authorities
    except CandidateOfflineValidationError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise CandidateOfflineValidationError() from None


class SportradarCandidateReadOnlyTransportV1:
    """Opaque unreachable transport placeholder for a later checkpoint."""

    __slots__ = ()

    def __init__(self, *_: object, **__: object) -> None:
        raise CandidateObservationUnavailable()

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("candidate transport cannot be subclassed")

    def __repr__(self) -> str:
        return "<SportradarCandidateReadOnlyTransportV1 redacted>"

    def __copy__(self):
        raise TypeError("candidate transport cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("candidate transport cannot be copied")

    def __reduce__(self):
        raise TypeError("candidate transport cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("candidate transport cannot be pickled")

    def __getstate__(self):
        raise TypeError("candidate transport cannot be pickled")


_ISSUED_CANDIDATE_STARTUP_AUTHORITY_IDS: frozenset[int] = frozenset()


def _open_sportradar_candidate_read_only_transport(
    *,
    startup_authority: CandidateObservationStartupAuthorityV1,
    decision: CandidateQualificationDecisionV1,
    binding: CandidateProviderBindingV1,
    quota: CandidateQuotaClosureV1,
) -> SportradarCandidateReadOnlyTransportV1:
    """Future composition seam; Task 7 deliberately has no valid issuer."""

    if type(startup_authority) is not CandidateObservationStartupAuthorityV1:
        raise TypeError("exact candidate startup authority required")
    if id(startup_authority) not in (
        _ISSUED_CANDIDATE_STARTUP_AUTHORITY_IDS
    ):
        raise CandidateObservationUnavailable()
    if type(decision) is not CandidateQualificationDecisionV1:
        raise TypeError("exact candidate decision required")
    if type(binding) is not CandidateProviderBindingV1:
        raise TypeError("exact candidate binding required")
    if type(quota) is not CandidateQuotaClosureV1:
        raise TypeError("exact candidate quota closure required")
    raise CandidateObservationUnavailable()


def prepare_sportradar_summary_read(
    transport: SportradarCandidateReadOnlyTransportV1,
    writer: CandidateQualificationOutputWriterV1,
    *,
    prior_receipt: CandidateQualificationAppendReceiptV1 | None,
    provider_match_id: str,
    resync: bool,
) -> tuple[
    SportradarCandidatePreparedReadV1,
    CandidateQualificationAppendReceiptV1,
]:
    if type(transport) is not SportradarCandidateReadOnlyTransportV1:
        raise TypeError("exact candidate transport required")
    if type(writer) is not CandidateQualificationOutputWriterV1:
        raise TypeError("exact candidate output writer required")
    if prior_receipt is not None and type(
        prior_receipt
    ) is not CandidateQualificationAppendReceiptV1:
        raise TypeError("exact candidate append receipt required")
    _safe_id(provider_match_id)
    if type(resync) is not bool:
        raise TypeError("exact resync flag required")
    raise CandidateObservationUnavailable()


def prepare_sportradar_timeline_read(
    transport: SportradarCandidateReadOnlyTransportV1,
    writer: CandidateQualificationOutputWriterV1,
    *,
    prior_receipt: CandidateQualificationAppendReceiptV1 | None,
    provider_match_id: str,
) -> tuple[
    SportradarCandidatePreparedReadV1,
    CandidateQualificationAppendReceiptV1,
]:
    if type(transport) is not SportradarCandidateReadOnlyTransportV1:
        raise TypeError("exact candidate transport required")
    if type(writer) is not CandidateQualificationOutputWriterV1:
        raise TypeError("exact candidate output writer required")
    if prior_receipt is not None and type(
        prior_receipt
    ) is not CandidateQualificationAppendReceiptV1:
        raise TypeError("exact candidate append receipt required")
    _safe_id(provider_match_id)
    raise CandidateObservationUnavailable()


def read_sportradar_summary(
    transport: SportradarCandidateReadOnlyTransportV1,
    *,
    prepared: SportradarCandidatePreparedReadV1,
) -> CapturedInput:
    if type(transport) is not SportradarCandidateReadOnlyTransportV1:
        raise TypeError("exact candidate transport required")
    if type(prepared) is not SportradarCandidatePreparedReadV1:
        raise TypeError("exact prepared read required")
    raise CandidateObservationUnavailable()


def read_sportradar_timeline(
    transport: SportradarCandidateReadOnlyTransportV1,
    *,
    prepared: SportradarCandidatePreparedReadV1,
) -> CapturedInput:
    if type(transport) is not SportradarCandidateReadOnlyTransportV1:
        raise TypeError("exact candidate transport required")
    if type(prepared) is not SportradarCandidatePreparedReadV1:
        raise TypeError("exact prepared read required")
    raise CandidateObservationUnavailable()


__all__ = (
    "CandidateObservationUnavailable",
    "CandidateOfflineValidationError",
    "SPORTRADAR_CANDIDATE_ACCEPTED_MEDIA_TYPE",
    "SPORTRADAR_CANDIDATE_ACCEPTED_REDIRECTS",
    "SPORTRADAR_CANDIDATE_ACCEPTED_STATUS",
    "SPORTRADAR_CANDIDATE_AUTOMATIC_RETRIES",
    "SPORTRADAR_CANDIDATE_CONNECT_TIMEOUT_SECONDS",
    "SPORTRADAR_CANDIDATE_CONNECTION_EPOCH",
    "SPORTRADAR_CANDIDATE_CREDENTIAL_ENV_NAMES",
    "SPORTRADAR_CANDIDATE_MAXIMUM_ACTIVE_CONNECTIONS",
    "SPORTRADAR_CANDIDATE_MAXIMUM_BODY_BYTES",
    "SPORTRADAR_CANDIDATE_METHOD",
    "SPORTRADAR_CANDIDATE_ORIGIN",
    "SPORTRADAR_CANDIDATE_POLLING_INTERVAL_SECONDS",
    "SPORTRADAR_CANDIDATE_READ_TIMEOUT_SECONDS",
    "SPORTRADAR_CANDIDATE_SUMMARY_PATH",
    "SPORTRADAR_CANDIDATE_TIMELINE_PATH",
    "SPORTRADAR_CANDIDATE_TOTAL_DEADLINE_SECONDS",
    "SPORTRADAR_CANDIDATE_USAGE",
    "SportradarCandidateReadOnlyTransportV1",
    "ValidatedCandidateOfflineArtifactsV1",
    "build_sportradar_candidate_session_manifest",
    "candidate_maximum_trace_bytes",
    "candidate_quota_demand",
    "issue_sportradar_candidate_capture_authorities",
    "make_sportradar_candidate_evidence_mismatch_decision",
    "make_sportradar_candidate_eligible_decision",
    "make_sportradar_candidate_offline_denial",
    "prepare_sportradar_summary_read",
    "prepare_sportradar_timeline_read",
    "read_sportradar_summary",
    "read_sportradar_timeline",
    "sportradar_candidate_offline_is_eligible",
    "validate_sportradar_candidate_offline_artifacts",
)
