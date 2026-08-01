"""Pure, unregistered Sportradar Tennis v3 candidate normalization.

The public binder in this module is ``pure_test_only``.  It does not confer
provider qualification, durability authority, or production registration.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Final

from inci_tennis_adapters.candidate_contracts import (
    CandidateParserEvidenceV1,
    CandidateProviderBindingV1,
    REQUIRED_CANDIDATE_CAPABILITIES,
    candidate_binding_projection,
)
from inci_tennis_expert.contracts import (
    BindingUniverse,
    ExpertContractError,
    ExpertIgnoreReasonV1,
    ExpertRejectReasonV1,
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ProviderLifecycle,
    ProviderLifecycleKind,
    ProviderPoint,
    ProviderSnapshot,
    ScoreValue,
    SetScore,
    TennisState,
    TerminationKind,
    canonical_expert_bytes,
    compute_expert_provider_source_lineage_sha256,
    expert_contract_sha256,
)
from tennis_v1.canonical import canonical_json_bytes
from tennis_v1.codec import canonical_record_sha256
from tennis_v1.entitlements import QualifiedProviderBinding
from tennis_v1.events import (
    CapturedInput,
    PersistedEvent,
    ProvenanceState,
    RecordKind,
    SourceKind,
)


_ERRORS: Final[frozenset[str]] = frozenset(
    {
        "candidate_binding_invalid",
        "candidate_captured_parent_mismatch",
        "candidate_payload_invalid",
        "candidate_prior_mismatch",
        "candidate_received_time_mismatch",
        "candidate_route_unknown",
        "candidate_schema_unknown",
        "candidate_secret_material",
    }
)
_MAX_CAPTURE_BYTES: Final[int] = 8 * 1024 * 1024
_MAX_JSON_DEPTH: Final[int] = 64
_MAX_JSON_NODES: Final[int] = 250_000
_MAX_SIGNED_64: Final[int] = 9_223_372_036_854_775_807
_SAFE_ID_PATTERN: Final[str] = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z"
_SHA256_PATTERN: Final[str] = r"[0-9a-f]{64}\Z"
_UTC_PATTERN: Final[str] = (
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]{1,9}))?Z\Z"
)
_URL_PATTERN: Final[str] = r"""https?://[^\s"'<>]+"""
_SUMMARY_EVENT_TYPE: Final[str] = "sportradar_tennis_summary_v3"
_TIMELINE_EVENT_TYPE: Final[str] = "sportradar_tennis_timeline_v3"
_TRANSPORT_ERROR_EVENT_TYPE: Final[str] = (
    "sportradar_tennis_transport_error_v1"
)
_SUMMARY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "event_id",
        "generated_at",
        "revision",
        "correction_epoch",
        "snapshot_complete",
        "source_event_time",
        "sport_event",
        "sport_event_status",
        "coverage",
    }
)
_TIMELINE_KEYS: Final[frozenset[str]] = frozenset(
    {"generated_at", "sport_event", "timeline"}
)
_SPORT_EVENT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "scheduled",
        "type",
        "best_of",
        "match_format",
        "competitors",
    }
)
_COMPETITOR_KEYS: Final[frozenset[str]] = frozenset(
    {"id", "qualifier"}
)
_STATUS_KEYS: Final[frozenset[str]] = frozenset(
    {
        "status",
        "termination",
        "winner_id",
        "retired_id",
        "completed_sets",
        "games_home",
        "games_away",
        "points_home",
        "points_away",
        "in_tiebreak",
        "tiebreak_points_home",
        "tiebreak_points_away",
        "tiebreak_first_server_id",
        "server_id",
    }
)
_SET_SCORE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "games_home",
        "games_away",
        "tiebreak_points_home",
        "tiebreak_points_away",
    }
)
_TIMELINE_BASE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "type",
        "revision",
        "correction_epoch",
        "event_time",
        "generated_at",
    }
)
_POINT_KEYS: Final[frozenset[str]] = (
    _TIMELINE_BASE_KEYS | {"point_winner_id", "server_id"}
)
_LIFECYCLE_KEYS: Final[frozenset[str]] = (
    _TIMELINE_BASE_KEYS
    | {"lifecycle", "winner_id", "retired_id", "server_id"}
)
_SNAPSHOT_KEYS: Final[frozenset[str]] = (
    _TIMELINE_BASE_KEYS | _STATUS_KEYS | {"snapshot_complete"}
)
_TRANSPORT_ERROR_KEYS: Final[frozenset[str]] = frozenset(
    {"exception_type", "status_code", "error_code", "request_id"}
)
_REQUEST_ID_KEYS: Final[frozenset[str]] = frozenset(
    {"state", "value"}
)
_SECRET_SUFFIXES: Final[tuple[str, ...]] = (
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
)
_SUPPORTED_FORMATS: Final[frozenset[MatchFormat]] = frozenset(
    {
        MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS,
    }
)
_CONTEXT_CONSTRUCTION_SENTINEL: Final[object] = object()
_ADAPTER_CONSTRUCTION_SENTINEL: Final[object] = object()


class SportradarTennisV3CandidateError(ValueError):
    """Fixed, non-secret-bearing candidate parser failure."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _ERRORS:
            raise TypeError("exact candidate error code required")
        super().__init__(code)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class _ProviderNormalizationContextV1:
    provider_source_id: str
    revision_domain_id: str
    source_lineage_sha256: str
    match_binding_universe_sha256: str
    canonical_match_id: str
    provider_match_id: str
    home_player_id: str
    away_player_id: str
    scheduled_start_wall_ns: int
    match_format: MatchFormat
    raw_event_type: str
    raw_event_version: int
    raw_ingest_seq: int
    raw_record_sha256: str
    captured_local_wall_ns: int
    captured_local_monotonic_ns: int
    captured_clock_uncertainty_ns: int
    captured_source_wall_ns: int | None
    captured_source_generated_ns: int | None
    captured_provider_sequence: str | None
    captured_payload_sha256: str
    captured_payload: bytes

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private normalization context constructor")

    def __repr__(self) -> str:
        return "<_ProviderNormalizationContextV1 redacted>"

    def __copy__(self) -> object:
        raise TypeError("normalization context is non-copyable")

    def __deepcopy__(self, _: object) -> object:
        raise TypeError("normalization context is non-copyable")

    def __reduce__(self) -> object:
        raise TypeError("normalization context is non-pickleable")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("normalization context is non-subclassable")


@dataclass(frozen=True, slots=True)
class _TimelineParseV1:
    events: tuple[
        ProviderSnapshot | ProviderPoint | ProviderLifecycle,
        ...,
    ]
    capabilities: tuple[tuple[str, bool], ...]


def _build_context(
    values: dict[str, object],
    sentinel: object,
) -> _ProviderNormalizationContextV1:
    if (
        sentinel is not _CONTEXT_CONSTRUCTION_SENTINEL
        or type(values) is not dict
        or set(values)
        != {
            item.name
            for item in fields(_ProviderNormalizationContextV1)
        }
    ):
        raise TypeError("private normalization context constructor")
    instance = object.__new__(_ProviderNormalizationContextV1)
    for item in fields(_ProviderNormalizationContextV1):
        object.__setattr__(instance, item.name, values[item.name])
    _validate_context(instance)
    return instance


def _build_adapter(
    context: _ProviderNormalizationContextV1,
    sentinel: object,
) -> SportradarTennisV3Adapter:
    if (
        sentinel is not _ADAPTER_CONSTRUCTION_SENTINEL
        or type(context) is not _ProviderNormalizationContextV1
    ):
        raise TypeError("private pure_test_only adapter constructor")
    instance = object.__new__(SportradarTennisV3Adapter)
    object.__setattr__(instance, "_context", context)
    return instance


@dataclass(frozen=True, slots=True, init=False)
class SportradarTennisV3Adapter:
    """Immutable adapter bound to one durable RAW event (pure_test_only)."""

    _context: _ProviderNormalizationContextV1

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private pure_test_only adapter constructor")

    def __repr__(self) -> str:
        return "<SportradarTennisV3Adapter pure_test_only redacted>"

    def __copy__(self) -> object:
        raise TypeError("adapter is non-copyable")

    def __deepcopy__(self, _: object) -> object:
        raise TypeError("adapter is non-copyable")

    def __reduce__(self) -> object:
        raise TypeError("adapter is non-pickleable")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("adapter is non-subclassable")

    def normalize_summary(
        self,
        payload: bytes,
        *,
        received_monotonic_ns: int,
    ) -> ProviderSnapshot:
        context = _require_adapter_call(
            self,
            payload=payload,
            received_monotonic_ns=received_monotonic_ns,
            required_event_type=_SUMMARY_EVENT_TYPE,
        )
        return _parse_summary(context)

    def normalize_timeline(
        self,
        payload: bytes,
        *,
        prior: TennisState | None,
        received_monotonic_ns: int,
    ) -> tuple[
        ProviderSnapshot | ProviderPoint | ProviderLifecycle,
        ...,
    ]:
        context = _require_adapter_call(
            self,
            payload=payload,
            received_monotonic_ns=received_monotonic_ns,
            required_event_type=_TIMELINE_EVENT_TYPE,
        )
        _validate_prior(prior, context)
        return _parse_timeline(context, prior=prior).events


def _candidate_error(code: str) -> SportradarTennisV3CandidateError:
    return SportradarTennisV3CandidateError(code)


def _safe_id(value: object) -> str:
    if (
        type(value) is not str
        or re.fullmatch(_SAFE_ID_PATTERN, value, flags=re.ASCII) is None
    ):
        raise _candidate_error("candidate_payload_invalid")
    return value


def _binding_safe_id(value: object) -> str:
    try:
        return _safe_id(value)
    except SportradarTennisV3CandidateError:
        raise _candidate_error("candidate_binding_invalid") from None


def _digest(value: object, *, binding: bool = False) -> str:
    if (
        type(value) is not str
        or re.fullmatch(_SHA256_PATTERN, value, flags=re.ASCII) is None
    ):
        code = (
            "candidate_binding_invalid"
            if binding
            else "candidate_payload_invalid"
        )
        raise _candidate_error(code)
    return value


def _integer(
    value: object,
    *,
    minimum: int = 0,
    maximum: int = _MAX_SIGNED_64,
) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise _candidate_error("candidate_payload_invalid")
    return value


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise _candidate_error("candidate_payload_invalid")
    return value


def _string(value: object) -> str:
    if type(value) is not str:
        raise _candidate_error("candidate_payload_invalid")
    return value


def _object(
    value: object,
    *,
    keys: frozenset[str],
) -> dict[str, object]:
    if type(value) is not dict:
        raise _candidate_error("candidate_payload_invalid")
    actual = set(value)
    if not keys.issubset(actual):
        raise _candidate_error("candidate_payload_invalid")
    if actual != keys:
        raise _candidate_error("candidate_schema_unknown")
    return value


def _array(
    value: object,
    *,
    minimum: int,
    maximum: int,
) -> list[object]:
    if (
        type(value) is not list
        or len(value) < minimum
        or len(value) > maximum
    ):
        raise _candidate_error("candidate_payload_invalid")
    return value


def _prescan_depth(content: bytes) -> None:
    depth = 0
    in_string = False
    escaped = False
    for byte in content:
        if in_string:
            if escaped:
                if byte not in (0x22, 0x5C):
                    raise _candidate_error(
                        "candidate_payload_invalid"
                    )
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):
            depth += 1
            if depth > _MAX_JSON_DEPTH:
                raise _candidate_error("candidate_payload_invalid")
        elif byte in (0x7D, 0x5D):
            depth -= 1
            if depth < 0:
                raise _candidate_error("candidate_payload_invalid")
    if in_string or depth != 0:
        raise _candidate_error("candidate_payload_invalid")


def _reject_float(_: str) -> object:
    raise _candidate_error("candidate_payload_invalid")


def _reject_constant(_: str) -> object:
    raise _candidate_error("candidate_payload_invalid")


def _parse_integer(value: str) -> int:
    if value == "-0":
        raise _candidate_error("candidate_payload_invalid")
    return int(value)


def _duplicate_free_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _candidate_error("candidate_payload_invalid")
        result[key] = value
    return result


def _normalized_key(value: str) -> str:
    return "".join(
        character
        for character in value.casefold()
        if character.isalnum()
    )


def _unsafe_url(value: str) -> bool:
    for match in re.finditer(
        _URL_PATTERN,
        value,
        flags=re.ASCII | re.IGNORECASE,
    ):
        url = match.group(0)
        remainder = url.split("://", 1)[1]
        authority = remainder
        for delimiter in ("/", "?", "#"):
            authority = authority.split(delimiter, 1)[0]
        if (
            not authority
            or "@" in authority
            or "?" in remainder
            or "#" in remainder
        ):
            return True
    return False


def _canonical_string(value: str) -> None:
    for character in value:
        codepoint = ord(character)
        if codepoint < 0x20 or 0xD800 <= codepoint <= 0xDFFF:
            raise _candidate_error("candidate_payload_invalid")
    if _unsafe_url(value):
        raise _candidate_error("candidate_secret_material")


def _validate_safe_json(value: object) -> None:
    count = 0
    stack: list[tuple[object, str | None]] = [(value, None)]
    while stack:
        current, parent_key = stack.pop()
        count += 1
        if count > _MAX_JSON_NODES:
            raise _candidate_error("candidate_payload_invalid")
        if current is None or type(current) in (bool, int, str):
            if type(current) is str:
                _canonical_string(current)
            continue
        if type(current) is list:
            stack.extend((item, parent_key) for item in current)
            continue
        if type(current) is not dict:
            raise _candidate_error("candidate_payload_invalid")
        normalized_parent = (
            _normalized_key(parent_key) if parent_key is not None else ""
        )
        if normalized_parent in {
            "headers",
            "header",
            "request",
            "httprequest",
            "environment",
            "environ",
            "env",
        }:
            raise _candidate_error("candidate_secret_material")
        for key, item in current.items():
            count += 1
            if count > _MAX_JSON_NODES or type(key) is not str:
                raise _candidate_error("candidate_payload_invalid")
            _canonical_string(key)
            normalized = _normalized_key(key)
            if normalized in {
                "headers",
                "header",
                "request",
                "httprequest",
            } or any(
                normalized.endswith(suffix)
                for suffix in _SECRET_SUFFIXES
            ):
                raise _candidate_error("candidate_secret_material")
            stack.append((item, key))


def _strict_json(payload: bytes) -> object:
    if (
        type(payload) is not bytes
        or len(payload) == 0
        or len(payload) > _MAX_CAPTURE_BYTES
        or payload.startswith(b"\xef\xbb\xbf")
    ):
        raise _candidate_error("candidate_payload_invalid")
    _prescan_depth(payload)
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise _candidate_error("candidate_payload_invalid") from None
    try:
        value = json.loads(
            decoded,
            object_pairs_hook=_duplicate_free_object,
            parse_float=_reject_float,
            parse_int=_parse_integer,
            parse_constant=_reject_constant,
        )
    except SportradarTennisV3CandidateError:
        raise
    except (json.JSONDecodeError, UnicodeError, ValueError):
        raise _candidate_error("candidate_payload_invalid") from None
    _validate_safe_json(value)
    return value


def _is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _utc_ns(value: object) -> int:
    text = _string(value)
    match = re.fullmatch(_UTC_PATTERN, text, flags=re.ASCII)
    if match is None:
        raise _candidate_error("candidate_payload_invalid")
    year, month, day, hour, minute, second = (
        int(match.group(index)) for index in range(1, 7)
    )
    fraction = match.group(7)
    month_lengths = (
        31,
        29 if _is_leap_year(year) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    )
    if (
        year < 1
        or month < 1
        or month > 12
        or day < 1
        or day > month_lengths[month - 1]
        or hour > 23
        or minute > 59
        or second > 59
    ):
        raise _candidate_error("candidate_payload_invalid")
    prior_year = year - 1
    days = (
        365 * prior_year
        + prior_year // 4
        - prior_year // 100
        + prior_year // 400
        - 719_162
    )
    days += sum(month_lengths[: month - 1]) + day - 1
    whole_seconds = ((days * 24 + hour) * 60 + minute) * 60 + second
    nanoseconds = whole_seconds * 1_000_000_000
    if fraction is not None:
        nanoseconds += int(fraction.ljust(9, "0"))
    if nanoseconds < 0 or nanoseconds > _MAX_SIGNED_64:
        raise _candidate_error("candidate_payload_invalid")
    return nanoseconds


def _coordinate(correction_epoch: int, revision: int) -> str:
    return f"c{correction_epoch}.r{revision}"


def _validate_context(
    context: _ProviderNormalizationContextV1,
) -> None:
    try:
        if type(context) is not _ProviderNormalizationContextV1:
            raise _candidate_error("candidate_binding_invalid")
        for value in (
            context.provider_source_id,
            context.revision_domain_id,
            context.canonical_match_id,
            context.provider_match_id,
            context.home_player_id,
            context.away_player_id,
            context.raw_event_type,
        ):
            _binding_safe_id(value)
        if context.provider_source_id != "sportradar":
            raise _candidate_error("candidate_binding_invalid")
        _digest(context.source_lineage_sha256, binding=True)
        _digest(context.match_binding_universe_sha256, binding=True)
        _digest(context.raw_record_sha256, binding=True)
        _digest(context.captured_payload_sha256, binding=True)
        if (
            context.home_player_id == context.away_player_id
            or type(context.scheduled_start_wall_ns) is not int
            or context.scheduled_start_wall_ns < 0
            or type(context.match_format) is not MatchFormat
            or context.match_format not in _SUPPORTED_FORMATS
            or type(context.raw_event_version) is not int
            or context.raw_event_version != 1
            or type(context.raw_ingest_seq) is not int
            or context.raw_ingest_seq < 0
            or type(context.captured_local_wall_ns) is not int
            or context.captured_local_wall_ns < 0
            or type(context.captured_local_monotonic_ns) is not int
            or context.captured_local_monotonic_ns < 0
            or type(context.captured_clock_uncertainty_ns) is not int
            or context.captured_clock_uncertainty_ns < 0
            or type(context.captured_payload) is not bytes
            or sha256(context.captured_payload).hexdigest()
            != context.captured_payload_sha256
        ):
            raise _candidate_error("candidate_binding_invalid")
        for optional_time in (
            context.captured_source_wall_ns,
            context.captured_source_generated_ns,
        ):
            if (
                optional_time is not None
                and (
                    type(optional_time) is not int
                    or optional_time < 0
                    or optional_time > _MAX_SIGNED_64
                )
            ):
                raise _candidate_error("candidate_binding_invalid")
        if context.captured_provider_sequence is not None:
            _binding_safe_id(context.captured_provider_sequence)
    except SportradarTennisV3CandidateError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise _candidate_error("candidate_binding_invalid") from None


def _require_adapter_call(
    adapter: SportradarTennisV3Adapter,
    *,
    payload: bytes,
    received_monotonic_ns: int,
    required_event_type: str,
) -> _ProviderNormalizationContextV1:
    if type(adapter) is not SportradarTennisV3Adapter:
        raise TypeError("exact SportradarTennisV3Adapter required")
    try:
        context = adapter._context
    except AttributeError:
        raise _candidate_error("candidate_binding_invalid") from None
    _validate_context(context)
    if context.raw_event_type != required_event_type:
        raise _candidate_error("candidate_route_unknown")
    if (
        type(payload) is not bytes
        or payload != context.captured_payload
    ):
        raise _candidate_error("candidate_captured_parent_mismatch")
    if (
        type(received_monotonic_ns) is not int
        or received_monotonic_ns
        != context.captured_local_monotonic_ns
    ):
        raise _candidate_error("candidate_received_time_mismatch")
    return context


def _validate_prior(
    prior: TennisState | None,
    context: _ProviderNormalizationContextV1,
) -> None:
    if prior is None:
        return
    if type(prior) is not TennisState:
        raise _candidate_error("candidate_prior_mismatch")
    try:
        TennisState.__post_init__(prior)
        if (
            prior.provider_source_id != context.provider_source_id
            or prior.revision_domain_id != context.revision_domain_id
            or prior.source_lineage_sha256
            != context.source_lineage_sha256
            or prior.provider_match_id != context.provider_match_id
            or prior.home_player_id != context.home_player_id
            or prior.away_player_id != context.away_player_id
            or prior.scheduled_start_wall_ns
            != context.scheduled_start_wall_ns
            or prior.match_format is not context.match_format
        ):
            raise _candidate_error("candidate_prior_mismatch")
    except SportradarTennisV3CandidateError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise _candidate_error("candidate_prior_mismatch") from None


def _validate_qualified_binding(
    provider_binding: QualifiedProviderBinding,
) -> None:
    try:
        safe_fields = (
            provider_binding.provider_id,
            provider_binding.product_tier,
            provider_binding.source_lineage_id,
        )
        for value in safe_fields:
            _binding_safe_id(value)
        if provider_binding.provider_id != "sportradar":
            raise _candidate_error("candidate_binding_invalid")
        for value in (
            provider_binding.entitlement_id_sha256,
            provider_binding.manifest_file_sha256,
            provider_binding.manifest_canonical_sha256,
            provider_binding.qualification_artifact_sha256,
            provider_binding.permission_artifact_sha256,
            provider_binding.qualification_trace_sha256,
            provider_binding.adapter_code_sha256,
            provider_binding.auth_contract_sha256,
            provider_binding.quota_contract_sha256,
        ):
            _digest(value, binding=True)
        times = (
            provider_binding.session_end_utc,
            provider_binding.required_retention_until,
            provider_binding.access_expires_at,
            provider_binding.analysis_expires_at,
            provider_binding.qualified_until,
        )
        if any(
            type(value) is not datetime
            or value.tzinfo is not timezone.utc
            or value.fold != 0
            for value in times
        ):
            raise _candidate_error("candidate_binding_invalid")
        if not (
            provider_binding.session_end_utc
            <= provider_binding.required_retention_until
            <= provider_binding.analysis_expires_at
            and provider_binding.session_end_utc
            <= provider_binding.access_expires_at
            <= provider_binding.analysis_expires_at
            and provider_binding.qualified_until
            <= provider_binding.analysis_expires_at
        ):
            raise _candidate_error("candidate_binding_invalid")
    except SportradarTennisV3CandidateError:
        raise
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise _candidate_error("candidate_binding_invalid") from None


def _validate_universe(universe: BindingUniverse) -> None:
    try:
        BindingUniverse.__post_init__(universe)
    except (AttributeError, TypeError, ValueError):
        raise _candidate_error("candidate_binding_invalid") from None


def _selected_binding_values(
    *,
    universe: BindingUniverse,
    captured: CapturedInput,
    provider_source_id: str,
    source_lineage_sha256: str,
) -> tuple[str, str, str, str, int, MatchFormat, str]:
    try:
        revision_domains = {
            binding.revision_domain_id for binding in universe.bindings
        }
        if len(revision_domains) != 1:
            raise _candidate_error("candidate_binding_invalid")
        revision_domain_id = next(iter(revision_domains))
        for binding in universe.bindings:
            if (
                binding.provider_source_id != provider_source_id
                or binding.source_lineage_sha256
                != source_lineage_sha256
                or binding.revision_domain_id != revision_domain_id
            ):
                raise _candidate_error("candidate_binding_invalid")
            for value in (
                binding.provider_match_id,
                binding.canonical_match_id,
                binding.provider_home_player_id,
                binding.provider_away_player_id,
                binding.revision_domain_id,
            ):
                _binding_safe_id(value)
        matches = tuple(
            binding
            for binding in universe.bindings
            if (
                binding.provider_source_id == provider_source_id
                and binding.source_lineage_sha256
                == source_lineage_sha256
                and binding.revision_domain_id == revision_domain_id
                and binding.provider_match_id
                == captured.source_entity_id
            )
        )
        if len(matches) != 1:
            raise _candidate_error("candidate_binding_invalid")
        selected = matches[0]
        return (
            revision_domain_id,
            selected.canonical_match_id,
            selected.provider_match_id,
            selected.provider_home_player_id,
            selected.scheduled_start_wall_ns,
            selected.match_format,
            selected.provider_away_player_id,
        )
    except SportradarTennisV3CandidateError:
        raise
    except (AttributeError, TypeError, ValueError, StopIteration):
        raise _candidate_error("candidate_binding_invalid") from None


def _captured_parent_fields_equal(
    captured: CapturedInput,
    durable_raw: PersistedEvent,
) -> bool:
    return (
        captured.session_id == durable_raw.session_id
        and captured.event_type == durable_raw.event_type
        and captured.event_version == durable_raw.event_version
        and captured.source_kind is durable_raw.source_kind
        and captured.source_id == durable_raw.source_id
        and captured.source_entity_id == durable_raw.source_entity_id
        and captured.endpoint_id == durable_raw.endpoint_id
        and captured.endpoint_state is durable_raw.endpoint_state
        and captured.channel_id == durable_raw.channel_id
        and captured.channel_state is durable_raw.channel_state
        and captured.request_id == durable_raw.request_id
        and captured.request_id_state is durable_raw.request_id_state
        and captured.source_wall_ns == durable_raw.source_wall_ns
        and captured.source_generated_ns
        == durable_raw.source_generated_ns
        and captured.local_wall_ns == durable_raw.local_wall_ns
        and captured.local_monotonic_ns
        == durable_raw.local_monotonic_ns
        and captured.clock_uncertainty_ns
        == durable_raw.clock_uncertainty_ns
        and captured.connection_epoch == durable_raw.connection_epoch
        and captured.provider_sequence == durable_raw.provider_sequence
        and captured.content_type == durable_raw.content_type
        and captured.payload_encoding == durable_raw.payload_encoding
        and captured.payload_transform == durable_raw.payload_transform
        and captured.retention_delete_by_ns
        == durable_raw.retention_delete_by_ns
        and captured.payload == durable_raw.payload
        and sha256(captured.payload).hexdigest()
        == durable_raw.payload_sha256
    )


def _validate_route_envelope(captured: CapturedInput) -> None:
    try:
        if (
            captured.source_kind is not SourceKind.PROVIDER
            or captured.source_id != "sportradar"
            or type(captured.event_version) is not int
            or captured.event_version != 1
            or captured.endpoint_id != "sportradar-api"
            or captured.endpoint_state is not ProvenanceState.SAFE_ORIGINAL
            or captured.channel_id != "sportradar-rest"
            or captured.channel_state is not ProvenanceState.SAFE_ORIGINAL
            or captured.request_id != "<redacted>"
            or captured.request_id_state is not ProvenanceState.REDACTED
            or type(captured.connection_epoch) is not int
            or captured.connection_epoch != 1
        ):
            raise _candidate_error("candidate_route_unknown")
        _binding_safe_id(captured.source_entity_id)
        if captured.event_type in {
            _SUMMARY_EVENT_TYPE,
            _TIMELINE_EVENT_TYPE,
        }:
            if (
                captured.content_type != "application/json"
                or (
                    captured.payload_transform
                    == "identity-public-market-v1"
                    and captured.payload_encoding != "json"
                )
                or (
                    captured.payload_transform
                    == "json-secret-redaction-v1"
                    and captured.payload_encoding
                    != "canonical-json-v1"
                )
                or captured.payload_transform
                not in {
                    "identity-public-market-v1",
                    "json-secret-redaction-v1",
                }
                or captured.source_wall_ns is None
                or captured.source_generated_ns is None
                or captured.provider_sequence is None
            ):
                raise _candidate_error(
                    "candidate_captured_parent_mismatch"
                )
            _binding_safe_id(captured.provider_sequence)
            return
        if captured.event_type == _TRANSPORT_ERROR_EVENT_TYPE:
            if (
                captured.content_type
                != "application/vnd.inci.transport-error+json"
                or captured.payload_encoding != "canonical-json-v1"
                or captured.payload_transform
                != "sanitized-transport-error-v1"
                or captured.source_wall_ns is not None
                or captured.source_generated_ns is not None
                or captured.provider_sequence is not None
            ):
                raise _candidate_error(
                    "candidate_captured_parent_mismatch"
                )
            return
        raise _candidate_error("candidate_route_unknown")
    except SportradarTennisV3CandidateError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise _candidate_error("candidate_route_unknown") from None


def _base_context_values(
    *,
    universe: BindingUniverse,
    captured: CapturedInput,
    provider_source_id: str,
    source_lineage_sha256: str,
    raw_ingest_seq: int,
    raw_record_sha256: str,
) -> dict[str, object]:
    (
        revision_domain_id,
        canonical_match_id,
        provider_match_id,
        home_player_id,
        scheduled_start_wall_ns,
        match_format,
        away_player_id,
    ) = _selected_binding_values(
        universe=universe,
        captured=captured,
        provider_source_id=provider_source_id,
        source_lineage_sha256=source_lineage_sha256,
    )
    return {
        "provider_source_id": provider_source_id,
        "revision_domain_id": revision_domain_id,
        "source_lineage_sha256": source_lineage_sha256,
        "match_binding_universe_sha256": universe.universe_sha256,
        "canonical_match_id": canonical_match_id,
        "provider_match_id": provider_match_id,
        "home_player_id": home_player_id,
        "away_player_id": away_player_id,
        "scheduled_start_wall_ns": scheduled_start_wall_ns,
        "match_format": match_format,
        "raw_event_type": captured.event_type,
        "raw_event_version": captured.event_version,
        "raw_ingest_seq": raw_ingest_seq,
        "raw_record_sha256": raw_record_sha256,
        "captured_local_wall_ns": captured.local_wall_ns,
        "captured_local_monotonic_ns": captured.local_monotonic_ns,
        "captured_clock_uncertainty_ns": (
            captured.clock_uncertainty_ns
        ),
        "captured_source_wall_ns": captured.source_wall_ns,
        "captured_source_generated_ns": captured.source_generated_ns,
        "captured_provider_sequence": captured.provider_sequence,
        "captured_payload_sha256": sha256(
            captured.payload
        ).hexdigest(),
        "captured_payload": captured.payload,
    }


def _qualified_context(
    *,
    provider_binding: QualifiedProviderBinding,
    universe: BindingUniverse,
    captured: CapturedInput,
    durable_raw: PersistedEvent,
) -> _ProviderNormalizationContextV1:
    if type(provider_binding) is not QualifiedProviderBinding:
        raise TypeError("exact QualifiedProviderBinding required")
    if type(universe) is not BindingUniverse:
        raise TypeError("exact BindingUniverse required")
    if type(captured) is not CapturedInput:
        raise TypeError("exact CapturedInput required")
    if type(durable_raw) is not PersistedEvent:
        raise TypeError("exact PersistedEvent required")
    _validate_qualified_binding(provider_binding)
    _validate_universe(universe)
    try:
        if (
            durable_raw.record_kind is not RecordKind.RAW
            or durable_raw.journal_version != 1
            or durable_raw.parent_ingest_seq is not None
            or durable_raw.source_kind is not SourceKind.PROVIDER
        ):
            raise _candidate_error(
                "candidate_captured_parent_mismatch"
            )
        PersistedEvent.__post_init__(durable_raw)
        if not _captured_parent_fields_equal(captured, durable_raw):
            raise _candidate_error(
                "candidate_captured_parent_mismatch"
            )
    except SportradarTennisV3CandidateError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise _candidate_error(
            "candidate_captured_parent_mismatch"
        ) from None
    _validate_route_envelope(captured)
    try:
        source_lineage_sha256 = (
            compute_expert_provider_source_lineage_sha256(
                provider_binding.provider_id,
                provider_binding.product_tier,
                provider_binding.source_lineage_id,
                provider_binding.manifest_canonical_sha256,
            )
        )
    except (AttributeError, TypeError, ValueError):
        raise _candidate_error("candidate_binding_invalid") from None
    values = _base_context_values(
        universe=universe,
        captured=captured,
        provider_source_id=provider_binding.provider_id,
        source_lineage_sha256=source_lineage_sha256,
        raw_ingest_seq=durable_raw.ingest_seq,
        raw_record_sha256=canonical_record_sha256(durable_raw),
    )
    return _build_context(values, _CONTEXT_CONSTRUCTION_SENTINEL)


def _capture_envelope_sha256(captured: CapturedInput) -> str:
    if type(captured) is not CapturedInput:
        raise TypeError("exact CapturedInput required")
    try:
        projection = (
            ("session_id", captured.session_id),
            ("event_type", captured.event_type),
            ("event_version", captured.event_version),
            ("source_kind", captured.source_kind.value),
            ("source_id", captured.source_id),
            ("source_entity_id", captured.source_entity_id),
            ("endpoint_id", captured.endpoint_id),
            ("endpoint_state", captured.endpoint_state.value),
            ("channel_id", captured.channel_id),
            ("channel_state", captured.channel_state.value),
            ("request_id", captured.request_id),
            ("request_id_state", captured.request_id_state.value),
            ("source_wall_ns", captured.source_wall_ns),
            ("source_generated_ns", captured.source_generated_ns),
            ("local_wall_ns", captured.local_wall_ns),
            ("local_monotonic_ns", captured.local_monotonic_ns),
            ("clock_uncertainty_ns", captured.clock_uncertainty_ns),
            ("connection_epoch", captured.connection_epoch),
            ("provider_sequence", captured.provider_sequence),
            ("content_type", captured.content_type),
            ("payload_encoding", captured.payload_encoding),
            ("payload_transform", captured.payload_transform),
            ("retention_delete_by_ns", captured.retention_delete_by_ns),
            ("payload_sha256", sha256(captured.payload).hexdigest()),
        )
        return sha256(
            b"INCI-SPORTRADAR-CANDIDATE-CAPTURE-ENVELOPE-V1\0"
            + canonical_expert_bytes(projection)
        ).hexdigest()
    except (AttributeError, TypeError, ValueError):
        raise _candidate_error(
            "candidate_captured_parent_mismatch"
        ) from None


def _candidate_context(
    *,
    binding: CandidateProviderBindingV1,
    universe: BindingUniverse,
    captured: CapturedInput,
) -> _ProviderNormalizationContextV1:
    if type(binding) is not CandidateProviderBindingV1:
        raise TypeError("exact CandidateProviderBindingV1 required")
    if type(universe) is not BindingUniverse:
        raise TypeError("exact BindingUniverse required")
    if type(captured) is not CapturedInput:
        raise TypeError("exact CapturedInput required")
    try:
        projection = candidate_binding_projection(binding)
        expected_binding_sha256 = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-BINDING-V1\0"
            + canonical_expert_bytes(projection)
        ).hexdigest()
        if (
            binding.binding_sha256 != expected_binding_sha256
            or binding.match_binding_universe_sha256
            != universe.universe_sha256
            or binding.binding_raw_artifact_sha256
            != universe.raw_artifact_sha256
            or binding.binding_review_artifact_sha256
            != universe.review.review_artifact_sha256
            or captured.session_id != binding.session_id
            or captured.retention_delete_by_ns
            != binding.retention_delete_by_ns
            or captured.local_wall_ns < binding.session_start_wall_ns
            or captured.local_wall_ns >= binding.session_end_wall_ns
        ):
            raise _candidate_error("candidate_binding_invalid")
    except SportradarTennisV3CandidateError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise _candidate_error("candidate_binding_invalid") from None
    _validate_universe(universe)
    _validate_route_envelope(captured)
    try:
        source_lineage_sha256 = (
            compute_expert_provider_source_lineage_sha256(
                binding.provider_id,
                binding.product_tier,
                binding.source_lineage_id,
                binding.provider_manifest_canonical_sha256,
            )
        )
        if (
            source_lineage_sha256
            != binding.provider_source_lineage_sha256
        ):
            raise _candidate_error("candidate_binding_invalid")
    except SportradarTennisV3CandidateError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise _candidate_error("candidate_binding_invalid") from None
    capture_digest = _capture_envelope_sha256(captured)
    values = _base_context_values(
        universe=universe,
        captured=captured,
        provider_source_id=binding.provider_id,
        source_lineage_sha256=source_lineage_sha256,
        raw_ingest_seq=0,
        raw_record_sha256=capture_digest,
    )
    return _build_context(values, _CONTEXT_CONSTRUCTION_SENTINEL)


def _match_format(value: object, best_of: int) -> MatchFormat:
    text = _string(value)
    if text == "standard_advantage_bo3_tb7_all_sets":
        match_format = MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS
        expected_best_of = 3
    elif text == "standard_advantage_bo5_tb7_all_sets":
        match_format = MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS
        expected_best_of = 5
    else:
        raise _candidate_error("candidate_payload_invalid")
    if best_of != expected_best_of:
        raise _candidate_error("candidate_payload_invalid")
    return match_format


def _parse_sport_event(
    value: object,
    context: _ProviderNormalizationContextV1,
) -> None:
    sport_event = _object(value, keys=_SPORT_EVENT_KEYS)
    if _string(sport_event["type"]) != "SINGLES":
        raise _candidate_error("candidate_schema_unknown")
    best_of = _integer(sport_event["best_of"], minimum=3, maximum=5)
    match_format = _match_format(
        sport_event["match_format"],
        best_of,
    )
    competitors = _array(
        sport_event["competitors"],
        minimum=2,
        maximum=2,
    )
    home = _object(competitors[0], keys=_COMPETITOR_KEYS)
    away = _object(competitors[1], keys=_COMPETITOR_KEYS)
    if (
        _string(home["qualifier"]) != "HOME"
        or _string(away["qualifier"]) != "AWAY"
    ):
        raise _candidate_error("candidate_schema_unknown")
    provider_match_id = _safe_id(sport_event["id"])
    home_player_id = _safe_id(home["id"])
    away_player_id = _safe_id(away["id"])
    scheduled_start_wall_ns = _utc_ns(sport_event["scheduled"])
    if (
        provider_match_id != context.provider_match_id
        or home_player_id != context.home_player_id
        or away_player_id != context.away_player_id
        or home_player_id == away_player_id
        or scheduled_start_wall_ns
        != context.scheduled_start_wall_ns
        or match_format is not context.match_format
    ):
        raise _candidate_error("candidate_captured_parent_mismatch")


def _side_from_id(
    value: object,
    context: _ProviderNormalizationContextV1,
    *,
    optional: bool,
) -> PlayerSide | None:
    if value is None and optional:
        return None
    identifier = _safe_id(value)
    if identifier == context.home_player_id:
        return PlayerSide.HOME
    if identifier == context.away_player_id:
        return PlayerSide.AWAY
    raise _candidate_error("candidate_payload_invalid")


def _status(value: object) -> MatchStatus:
    text = _string(value)
    if text == "SCHEDULED":
        return MatchStatus.SCHEDULED
    if text == "LIVE":
        return MatchStatus.LIVE
    if text == "SUSPENDED":
        return MatchStatus.SUSPENDED
    if text == "ENDED":
        return MatchStatus.ENDED
    if text == "CANCELLED":
        return MatchStatus.CANCELLED
    raise _candidate_error("candidate_schema_unknown")


def _termination(value: object) -> TerminationKind:
    text = _string(value)
    if text == "NONE":
        return TerminationKind.NONE
    if text == "NATURAL":
        return TerminationKind.NATURAL
    if text == "WALKOVER":
        return TerminationKind.WALKOVER
    if text == "RETIREMENT":
        return TerminationKind.RETIREMENT
    if text == "CANCELLATION":
        return TerminationKind.CANCELLATION
    raise _candidate_error("candidate_schema_unknown")


def _score_value(value: object) -> ScoreValue:
    text = _string(value)
    if text == "LOVE":
        return ScoreValue.LOVE
    if text == "FIFTEEN":
        return ScoreValue.FIFTEEN
    if text == "THIRTY":
        return ScoreValue.THIRTY
    if text == "FORTY":
        return ScoreValue.FORTY
    if text == "ADVANTAGE":
        return ScoreValue.ADVANTAGE
    raise _candidate_error("candidate_schema_unknown")


def _optional_integer(value: object) -> int | None:
    if value is None:
        return None
    return _integer(value)


def _set_score(value: object) -> SetScore:
    item = _object(value, keys=_SET_SCORE_KEYS)
    try:
        return SetScore(
            games_home=_integer(item["games_home"]),
            games_away=_integer(item["games_away"]),
            tiebreak_points_home=_optional_integer(
                item["tiebreak_points_home"]
            ),
            tiebreak_points_away=_optional_integer(
                item["tiebreak_points_away"]
            ),
        )
    except (ExpertContractError, TypeError, ValueError):
        raise _candidate_error("candidate_payload_invalid") from None


def _completed_set_winner(score: SetScore) -> PlayerSide | None:
    home = score.games_home
    away = score.games_away
    home_tiebreak = score.tiebreak_points_home
    away_tiebreak = score.tiebreak_points_away
    if home_tiebreak is None:
        if (home == 6 and 0 <= away <= 4) or (home == 7 and away == 5):
            return PlayerSide.HOME
        if (away == 6 and 0 <= home <= 4) or (away == 7 and home == 5):
            return PlayerSide.AWAY
        return None
    if away_tiebreak is None:
        return None
    if (
        (home, away) == (7, 6)
        and home_tiebreak >= 7
        and home_tiebreak - away_tiebreak >= 2
    ):
        return PlayerSide.HOME
    if (
        (home, away) == (6, 7)
        and away_tiebreak >= 7
        and away_tiebreak - home_tiebreak >= 2
    ):
        return PlayerSide.AWAY
    return None


def _tiebreak_won(home: int, away: int) -> bool:
    return (
        home >= 7
        and home - away >= 2
        or away >= 7
        and away - home >= 2
    )


def _normal_set_won(home: int, away: int) -> bool:
    return (
        home >= 6
        and home - away >= 2
        or away >= 6
        and away - home >= 2
    )


def _opposite(side: PlayerSide) -> PlayerSide:
    if side is PlayerSide.HOME:
        return PlayerSide.AWAY
    return PlayerSide.HOME


def _tiebreak_server(
    first: PlayerSide,
    completed_points: int,
) -> PlayerSide:
    if completed_points == 0:
        return first
    if ((completed_points - 1) // 2) % 2 == 0:
        return _opposite(first)
    return first


def _snapshot_reachable(snapshot: ProviderSnapshot) -> bool:
    required_sets = (
        2
        if snapshot.match_format
        is MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS
        else 3
    )
    home_sets = 0
    away_sets = 0
    clinching_side: PlayerSide | None = None
    prefix_valid = True
    for index, set_score in enumerate(snapshot.completed_sets):
        winner = _completed_set_winner(set_score)
        if winner is None or clinching_side is not None:
            prefix_valid = False
            break
        if winner is PlayerSide.HOME:
            home_sets += 1
            if home_sets == required_sets:
                clinching_side = PlayerSide.HOME
        else:
            away_sets += 1
            if away_sets == required_sets:
                clinching_side = PlayerSide.AWAY
        if (
            clinching_side is not None
            and index != len(snapshot.completed_sets) - 1
        ):
            prefix_valid = False
            break
    zero_score = (
        snapshot.completed_sets == ()
        and snapshot.games_home == 0
        and snapshot.games_away == 0
        and snapshot.points_home is ScoreValue.LOVE
        and snapshot.points_away is ScoreValue.LOVE
        and snapshot.in_tiebreak is False
        and snapshot.tiebreak_points_home == 0
        and snapshot.tiebreak_points_away == 0
        and snapshot.tiebreak_first_server is None
    )
    active = snapshot.status in {
        MatchStatus.LIVE,
        MatchStatus.SUSPENDED,
    }
    if snapshot.in_tiebreak:
        incomplete_reachable = (
            prefix_valid
            and clinching_side is None
            and snapshot.games_home == 6
            and snapshot.games_away == 6
            and snapshot.points_home is ScoreValue.LOVE
            and snapshot.points_away is ScoreValue.LOVE
            and snapshot.tiebreak_first_server is not None
            and not _tiebreak_won(
                snapshot.tiebreak_points_home,
                snapshot.tiebreak_points_away,
            )
            and (
                (
                    active
                    and snapshot.server_for_next_point
                    is _tiebreak_server(
                        snapshot.tiebreak_first_server,
                        snapshot.tiebreak_points_home
                        + snapshot.tiebreak_points_away,
                    )
                )
                or (
                    not active
                    and snapshot.server_for_next_point is None
                )
            )
        )
    else:
        incomplete_reachable = (
            prefix_valid
            and clinching_side is None
            and snapshot.games_home <= 6
            and snapshot.games_away <= 6
            and (snapshot.games_home, snapshot.games_away) != (6, 6)
            and not _normal_set_won(
                snapshot.games_home,
                snapshot.games_away,
            )
            and (
                (active and snapshot.server_for_next_point is not None)
                or (
                    not active
                    and snapshot.server_for_next_point is None
                )
            )
        )
    if snapshot.status is MatchStatus.SCHEDULED:
        return zero_score and snapshot.server_for_next_point is None
    if active:
        return incomplete_reachable
    if snapshot.status is MatchStatus.CANCELLED:
        return incomplete_reachable
    if snapshot.status is not MatchStatus.ENDED:
        return False
    if snapshot.termination_kind is TerminationKind.WALKOVER:
        return zero_score and snapshot.server_for_next_point is None
    if snapshot.termination_kind is TerminationKind.RETIREMENT:
        return incomplete_reachable
    if snapshot.termination_kind is not TerminationKind.NATURAL:
        return False
    return (
        prefix_valid
        and clinching_side is not None
        and snapshot.winner is clinching_side
        and snapshot.games_home == 0
        and snapshot.games_away == 0
        and snapshot.points_home is ScoreValue.LOVE
        and snapshot.points_away is ScoreValue.LOVE
        and snapshot.in_tiebreak is False
        and snapshot.tiebreak_points_home == 0
        and snapshot.tiebreak_points_away == 0
        and snapshot.tiebreak_first_server is None
        and snapshot.server_for_next_point is None
    )


def _snapshot_from_fields(
    *,
    context: _ProviderNormalizationContextV1,
    provider_event_id: object,
    correction_epoch: int,
    revision: int,
    source_wall_ns: int,
    source_generated_wall_ns: int,
    snapshot_complete: object,
    status_fields: dict[str, object],
) -> ProviderSnapshot:
    if _boolean(snapshot_complete) is not True:
        raise _candidate_error("candidate_payload_invalid")
    status = _status(status_fields["status"])
    termination = _termination(status_fields["termination"])
    winner = _side_from_id(
        status_fields["winner_id"],
        context,
        optional=True,
    )
    retired = _side_from_id(
        status_fields["retired_id"],
        context,
        optional=True,
    )
    server = _side_from_id(
        status_fields["server_id"],
        context,
        optional=True,
    )
    first_server = _side_from_id(
        status_fields["tiebreak_first_server_id"],
        context,
        optional=True,
    )
    completed_raw = _array(
        status_fields["completed_sets"],
        minimum=0,
        maximum=5,
    )
    completed_sets = tuple(_set_score(item) for item in completed_raw)
    try:
        snapshot = ProviderSnapshot(
            provider_source_id=context.provider_source_id,
            revision_domain_id=context.revision_domain_id,
            source_lineage_sha256=context.source_lineage_sha256,
            provider_event_id=_safe_id(provider_event_id),
            provider_match_id=context.provider_match_id,
            home_player_id=context.home_player_id,
            away_player_id=context.away_player_id,
            scheduled_start_wall_ns=context.scheduled_start_wall_ns,
            match_format=context.match_format,
            status=status,
            termination_kind=termination,
            winner=winner,
            retired_side=retired,
            completed_sets=completed_sets,
            games_home=_integer(status_fields["games_home"]),
            games_away=_integer(status_fields["games_away"]),
            points_home=_score_value(status_fields["points_home"]),
            points_away=_score_value(status_fields["points_away"]),
            in_tiebreak=_boolean(status_fields["in_tiebreak"]),
            tiebreak_points_home=_integer(
                status_fields["tiebreak_points_home"]
            ),
            tiebreak_points_away=_integer(
                status_fields["tiebreak_points_away"]
            ),
            tiebreak_first_server=first_server,
            server_for_next_point=server,
            correction_epoch=correction_epoch,
            revision=revision,
            source_wall_ns=source_wall_ns,
            source_generated_wall_ns=source_generated_wall_ns,
            received_monotonic_ns=(
                context.captured_local_monotonic_ns
            ),
            clock_uncertainty_ns=(
                context.captured_clock_uncertainty_ns
            ),
            snapshot_complete=True,
        )
    except SportradarTennisV3CandidateError:
        raise
    except (ExpertContractError, TypeError, ValueError):
        raise _candidate_error("candidate_payload_invalid") from None
    if not _snapshot_reachable(snapshot):
        raise _candidate_error("candidate_payload_invalid")
    return snapshot


def _all_capabilities(value: bool) -> tuple[tuple[str, bool], ...]:
    return tuple(
        (capability, value)
        for capability in REQUIRED_CANDIDATE_CAPABILITIES
    )


def _parse_summary(
    context: _ProviderNormalizationContextV1,
) -> ProviderSnapshot:
    if context.raw_event_type != _SUMMARY_EVENT_TYPE:
        raise _candidate_error("candidate_route_unknown")
    if context.raw_ingest_seq < 0:
        raise _candidate_error("candidate_binding_invalid")
    if (
        context.captured_payload.startswith(b"\xef\xbb\xbf")
        or context.captured_payload == b""
    ):
        raise _candidate_error("candidate_payload_invalid")
    decoded = _strict_json(context.captured_payload)
    summary = _object(decoded, keys=_SUMMARY_KEYS)
    _parse_sport_event(summary["sport_event"], context)
    coverage = _object(
        summary["coverage"],
        keys=frozenset(REQUIRED_CANDIDATE_CAPABILITIES),
    )
    for capability in REQUIRED_CANDIDATE_CAPABILITIES:
        if _boolean(coverage[capability]) is not True:
            raise _candidate_error("candidate_payload_invalid")
    correction_epoch = _integer(summary["correction_epoch"])
    revision = _integer(summary["revision"])
    source_wall_ns = _utc_ns(summary["source_event_time"])
    source_generated_wall_ns = _utc_ns(summary["generated_at"])
    if (
        context.captured_source_wall_ns != source_wall_ns
        or context.captured_source_generated_ns
        != source_generated_wall_ns
        or _coordinate(correction_epoch, revision)
        != _captured_provider_sequence(context)
    ):
        raise _candidate_error("candidate_captured_parent_mismatch")
    status_fields = _object(
        summary["sport_event_status"],
        keys=_STATUS_KEYS,
    )
    return _snapshot_from_fields(
        context=context,
        provider_event_id=summary["event_id"],
        correction_epoch=correction_epoch,
        revision=revision,
        source_wall_ns=source_wall_ns,
        source_generated_wall_ns=source_generated_wall_ns,
        snapshot_complete=summary["snapshot_complete"],
        status_fields=status_fields,
    )


def _captured_provider_sequence(
    context: _ProviderNormalizationContextV1,
) -> str | None:
    return context.captured_provider_sequence


def _point_from_entry(
    entry: dict[str, object],
    *,
    context: _ProviderNormalizationContextV1,
    correction_epoch: int,
    revision: int,
    source_wall_ns: int,
    source_generated_wall_ns: int,
) -> ProviderPoint:
    try:
        return ProviderPoint(
            provider_source_id=context.provider_source_id,
            revision_domain_id=context.revision_domain_id,
            source_lineage_sha256=context.source_lineage_sha256,
            provider_event_id=_safe_id(entry["id"]),
            provider_match_id=context.provider_match_id,
            home_player_id=context.home_player_id,
            away_player_id=context.away_player_id,
            scheduled_start_wall_ns=context.scheduled_start_wall_ns,
            match_format=context.match_format,
            correction_epoch=correction_epoch,
            revision=revision,
            point_winner=_side_from_id(
                entry["point_winner_id"],
                context,
                optional=False,
            ),
            server_before_point=_side_from_id(
                entry["server_id"],
                context,
                optional=False,
            ),
            source_wall_ns=source_wall_ns,
            source_generated_wall_ns=source_generated_wall_ns,
            received_monotonic_ns=(
                context.captured_local_monotonic_ns
            ),
            clock_uncertainty_ns=(
                context.captured_clock_uncertainty_ns
            ),
        )
    except SportradarTennisV3CandidateError:
        raise
    except (ExpertContractError, TypeError, ValueError):
        raise _candidate_error("candidate_payload_invalid") from None


def _lifecycle_kind(value: object) -> ProviderLifecycleKind:
    text = _string(value)
    if text == "START":
        return ProviderLifecycleKind.START
    if text == "SUSPEND":
        return ProviderLifecycleKind.SUSPEND
    if text == "RESUME":
        return ProviderLifecycleKind.RESUME
    if text == "WALKOVER":
        return ProviderLifecycleKind.WALKOVER
    if text == "RETIREMENT":
        return ProviderLifecycleKind.RETIREMENT
    if text == "CANCEL":
        return ProviderLifecycleKind.CANCEL
    if text == "NATURAL_END_CONFIRMATION":
        return ProviderLifecycleKind.NATURAL_END_CONFIRMATION
    raise _candidate_error("candidate_schema_unknown")


def _lifecycle_from_entry(
    entry: dict[str, object],
    *,
    context: _ProviderNormalizationContextV1,
    correction_epoch: int,
    revision: int,
    source_wall_ns: int,
    source_generated_wall_ns: int,
) -> ProviderLifecycle:
    kind = _lifecycle_kind(entry["lifecycle"])
    winner = _side_from_id(
        entry["winner_id"],
        context,
        optional=True,
    )
    retired = _side_from_id(
        entry["retired_id"],
        context,
        optional=True,
    )
    server = _side_from_id(
        entry["server_id"],
        context,
        optional=True,
    )
    if kind in {
        ProviderLifecycleKind.START,
        ProviderLifecycleKind.SUSPEND,
        ProviderLifecycleKind.RESUME,
    }:
        valid_payload = (
            winner is None and retired is None and server is not None
        )
    elif kind in {
        ProviderLifecycleKind.WALKOVER,
        ProviderLifecycleKind.NATURAL_END_CONFIRMATION,
    }:
        valid_payload = (
            winner is not None and retired is None and server is None
        )
    elif kind is ProviderLifecycleKind.RETIREMENT:
        valid_payload = (
            winner is not None
            and retired is not None
            and winner is not retired
            and server is None
        )
    else:
        valid_payload = (
            winner is None and retired is None and server is None
        )
    if not valid_payload:
        raise _candidate_error("candidate_payload_invalid")
    try:
        return ProviderLifecycle(
            provider_source_id=context.provider_source_id,
            revision_domain_id=context.revision_domain_id,
            source_lineage_sha256=context.source_lineage_sha256,
            provider_event_id=_safe_id(entry["id"]),
            provider_match_id=context.provider_match_id,
            home_player_id=context.home_player_id,
            away_player_id=context.away_player_id,
            scheduled_start_wall_ns=context.scheduled_start_wall_ns,
            match_format=context.match_format,
            correction_epoch=correction_epoch,
            revision=revision,
            kind=kind,
            winner=winner,
            retired_side=retired,
            server_for_next_point=server,
            source_wall_ns=source_wall_ns,
            source_generated_wall_ns=source_generated_wall_ns,
            received_monotonic_ns=(
                context.captured_local_monotonic_ns
            ),
            clock_uncertainty_ns=(
                context.captured_clock_uncertainty_ns
            ),
        )
    except (ExpertContractError, TypeError, ValueError):
        raise _candidate_error("candidate_payload_invalid") from None


def _timeline_capabilities(
    events: tuple[
        ProviderSnapshot | ProviderPoint | ProviderLifecycle,
        ...,
    ],
) -> tuple[tuple[str, bool], ...]:
    has_snapshot = any(type(event) is ProviderSnapshot for event in events)
    has_point_state = any(
        type(event) in {ProviderSnapshot, ProviderPoint}
        for event in events
    )
    has_server = any(
        (
            type(event) is ProviderSnapshot
            and event.server_for_next_point is not None
        )
        or type(event) is ProviderPoint
        or (
            type(event) is ProviderLifecycle
            and event.server_for_next_point is not None
        )
        for event in events
    )
    values = (
        ("correction_semantics", True),
        ("current_server", has_server),
        ("match_format", True),
        ("monotonic_sequence_or_revision", True),
        ("point_state", has_point_state),
        ("provider_generated_time", True),
        ("resync_snapshot", has_snapshot),
        ("source_event_time", True),
        ("stable_match_ids", True),
        ("stable_player_ids", True),
    )
    if tuple(name for name, _ in values) != (
        REQUIRED_CANDIDATE_CAPABILITIES
    ):
        raise _candidate_error("candidate_binding_invalid")
    return values


def _parse_timeline(
    context: _ProviderNormalizationContextV1,
    *,
    prior: TennisState | None,
) -> _TimelineParseV1:
    if context.raw_event_type != _TIMELINE_EVENT_TYPE:
        raise _candidate_error("candidate_route_unknown")
    decoded = _strict_json(context.captured_payload)
    document = _object(decoded, keys=_TIMELINE_KEYS)
    _parse_sport_event(document["sport_event"], context)
    generated_text = _string(document["generated_at"])
    generated_ns = _utc_ns(generated_text)
    timeline = _array(document["timeline"], minimum=1, maximum=64)
    parsed: list[
        ProviderSnapshot | ProviderPoint | ProviderLifecycle
    ] = []
    previous_coordinate: tuple[int, int] | None = None
    final_event_time_ns: int | None = None
    final_generated_text: str | None = None
    final_generated_ns: int | None = None
    for raw_entry in timeline:
        if type(raw_entry) is not dict:
            raise _candidate_error("candidate_payload_invalid")
        if "type" not in raw_entry:
            raise _candidate_error("candidate_payload_invalid")
        entry_type = _string(raw_entry["type"])
        if entry_type == "SNAPSHOT":
            expected_keys = _SNAPSHOT_KEYS
        elif entry_type == "POINT":
            expected_keys = _POINT_KEYS
        elif entry_type == "LIFECYCLE":
            expected_keys = _LIFECYCLE_KEYS
        else:
            raise _candidate_error("candidate_schema_unknown")
        entry = _object(raw_entry, keys=expected_keys)
        correction_epoch = _integer(entry["correction_epoch"])
        revision = _integer(entry["revision"], minimum=1)
        coordinate = (correction_epoch, revision)
        if (
            previous_coordinate is not None
            and coordinate <= previous_coordinate
        ):
            raise _candidate_error("candidate_payload_invalid")
        epoch_advanced = (
            previous_coordinate is not None
            and correction_epoch > previous_coordinate[0]
        )
        if (
            previous_coordinate is None
            and prior is not None
            and correction_epoch > prior.correction_epoch
        ):
            epoch_advanced = True
        if epoch_advanced and entry_type != "SNAPSHOT":
            raise _candidate_error("candidate_payload_invalid")
        source_wall_ns = _utc_ns(entry["event_time"])
        source_generated_wall_ns = _utc_ns(entry["generated_at"])
        _safe_id(entry["id"])
        if entry_type == "SNAPSHOT":
            status_fields = {
                key: entry[key] for key in _STATUS_KEYS
            }
            event = _snapshot_from_fields(
                context=context,
                provider_event_id=entry["id"],
                correction_epoch=correction_epoch,
                revision=revision,
                source_wall_ns=source_wall_ns,
                source_generated_wall_ns=source_generated_wall_ns,
                snapshot_complete=entry["snapshot_complete"],
                status_fields=status_fields,
            )
        elif entry_type == "POINT":
            event = _point_from_entry(
                entry,
                context=context,
                correction_epoch=correction_epoch,
                revision=revision,
                source_wall_ns=source_wall_ns,
                source_generated_wall_ns=source_generated_wall_ns,
            )
        else:
            event = _lifecycle_from_entry(
                entry,
                context=context,
                correction_epoch=correction_epoch,
                revision=revision,
                source_wall_ns=source_wall_ns,
                source_generated_wall_ns=source_generated_wall_ns,
            )
        parsed.append(event)
        previous_coordinate = coordinate
        final_event_time_ns = source_wall_ns
        final_generated_text = _string(entry["generated_at"])
        final_generated_ns = source_generated_wall_ns
    if (
        final_event_time_ns is None
        or final_generated_ns is None
        or final_generated_text != generated_text
        or final_generated_ns != generated_ns
        or context.captured_source_wall_ns != final_event_time_ns
        or context.captured_source_generated_ns != generated_ns
        or previous_coordinate is None
        or _coordinate(*previous_coordinate)
        != _captured_provider_sequence(context)
    ):
        raise _candidate_error("candidate_captured_parent_mismatch")
    events = tuple(parsed)
    return _TimelineParseV1(
        events=events,
        capabilities=_timeline_capabilities(events),
    )


def _validate_transport_error_context(
    context: _ProviderNormalizationContextV1,
) -> None:
    if context.raw_event_type != _TRANSPORT_ERROR_EVENT_TYPE:
        raise _candidate_error("candidate_route_unknown")
    decoded = _strict_json(context.captured_payload)
    document = _object(decoded, keys=_TRANSPORT_ERROR_KEYS)
    request = _object(document["request_id"], keys=_REQUEST_ID_KEYS)
    exception_type = _string(document["exception_type"])
    error_code = _string(document["error_code"])
    status_code = document["status_code"]
    allowed_exceptions = {
        "connect_error",
        "timeout_error",
        "tls_error",
        "http_status_error",
        "redirect_error",
        "body_limit_error",
        "transport_contract_error",
    }
    allowed_codes = {
        "connect_failed",
        "timeout",
        "tls_failed",
        "http_4xx",
        "http_5xx",
        "http_other",
        "redirect_denied",
        "body_too_large",
        "transport_contract_invalid",
    }
    if (
        exception_type not in allowed_exceptions
        or error_code not in allowed_codes
        or request != {"state": "redacted", "value": "<redacted>"}
    ):
        raise _candidate_error("candidate_schema_unknown")
    if status_code is not None:
        _integer(status_code, minimum=100, maximum=599)
    if exception_type == "http_status_error":
        if status_code is None:
            raise _candidate_error("candidate_payload_invalid")
        if 400 <= status_code <= 499:
            expected_code = "http_4xx"
        elif 500 <= status_code <= 599:
            expected_code = "http_5xx"
        else:
            expected_code = "http_other"
        if error_code != expected_code:
            raise _candidate_error("candidate_payload_invalid")
    elif exception_type == "redirect_error":
        if (
            status_code is None
            or status_code < 300
            or status_code > 399
            or error_code != "redirect_denied"
        ):
            raise _candidate_error("candidate_payload_invalid")
    else:
        expected_pairs = {
            "connect_error": "connect_failed",
            "timeout_error": "timeout",
            "tls_error": "tls_failed",
            "body_limit_error": "body_too_large",
            "transport_contract_error": "transport_contract_invalid",
        }
        if (
            status_code is not None
            or expected_pairs.get(exception_type) != error_code
        ):
            raise _candidate_error("candidate_payload_invalid")
    try:
        if canonical_json_bytes(document) != context.captured_payload:
            raise _candidate_error("candidate_payload_invalid")
    except SportradarTennisV3CandidateError:
        raise
    except (TypeError, ValueError):
        raise _candidate_error("candidate_payload_invalid") from None


def bind_sportradar_tennis_v3_event(
    *,
    provider_binding: QualifiedProviderBinding,
    universe: BindingUniverse,
    captured: CapturedInput,
    durable_raw: PersistedEvent,
) -> SportradarTennisV3Adapter:
    """Bind one exact durable candidate event for pure contract tests only."""
    context = _qualified_context(
        provider_binding=provider_binding,
        universe=universe,
        captured=captured,
        durable_raw=durable_raw,
    )
    return _build_adapter(context, _ADAPTER_CONSTRUCTION_SENTINEL)


def validate_sportradar_tennis_v3_prior(
    *,
    adapter: SportradarTennisV3Adapter,
    prior: TennisState | None,
) -> None:
    """Validate prior state against a bound event before payload parsing."""
    if type(adapter) is not SportradarTennisV3Adapter:
        raise TypeError("exact SportradarTennisV3Adapter required")
    try:
        context = adapter._context
    except AttributeError:
        raise _candidate_error("candidate_binding_invalid") from None
    _validate_context(context)
    _validate_prior(prior, context)


def validate_sportradar_tennis_v3_transport_error(
    *,
    provider_binding: QualifiedProviderBinding,
    universe: BindingUniverse,
    captured: CapturedInput,
    durable_raw: PersistedEvent,
) -> None:
    """Strictly validate one sanitized transport-error RAW event."""
    context = _qualified_context(
        provider_binding=provider_binding,
        universe=universe,
        captured=captured,
        durable_raw=durable_raw,
    )
    _validate_transport_error_context(context)


def _reject_reason_for_error(
    error: SportradarTennisV3CandidateError,
) -> str:
    code = str(error)
    if code in {
        "candidate_payload_invalid",
        "candidate_secret_material",
    }:
        return ExpertRejectReasonV1.NORMALIZER_PAYLOAD_INVALID.value
    if code in {
        "candidate_route_unknown",
        "candidate_schema_unknown",
    }:
        return ExpertRejectReasonV1.NORMALIZER_SCHEMA_UNKNOWN.value
    return ExpertRejectReasonV1.NORMALIZER_CONTRACT_VIOLATION.value


def _output_contract_sha256(
    value: ProviderSnapshot | ProviderPoint | ProviderLifecycle,
) -> str:
    if type(value) not in {
        ProviderSnapshot,
        ProviderPoint,
        ProviderLifecycle,
    }:
        raise _candidate_error("candidate_binding_invalid")
    return expert_contract_sha256(value)


def _parser_evidence(
    *,
    captured: CapturedInput,
    capture_envelope_sha256: str,
    parser_outcome: str,
    reason: str | None,
    events: tuple[
        ProviderSnapshot | ProviderPoint | ProviderLifecycle,
        ...,
    ],
    capabilities: tuple[tuple[str, bool], ...],
) -> CandidateParserEvidenceV1:
    if events:
        first_correction_epoch = events[0].correction_epoch
        first_revision = events[0].revision
        last_correction_epoch = events[-1].correction_epoch
        last_revision = events[-1].revision
    else:
        first_correction_epoch = None
        first_revision = None
        last_correction_epoch = None
        last_revision = None
    try:
        return CandidateParserEvidenceV1._create(
            schema_version=1,
            event_type=captured.event_type,
            event_version=captured.event_version,
            payload_sha256=sha256(captured.payload).hexdigest(),
            capture_envelope_sha256=capture_envelope_sha256,
            parser_outcome=parser_outcome,
            reason=reason,
            output_contract_sha256s=tuple(
                _output_contract_sha256(event) for event in events
            ),
            capabilities=capabilities,
            first_correction_epoch=first_correction_epoch,
            first_revision=first_revision,
            last_correction_epoch=last_correction_epoch,
            last_revision=last_revision,
        )
    except SportradarTennisV3CandidateError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise _candidate_error("candidate_binding_invalid") from None


def inspect_sportradar_candidate_capture(
    *,
    binding: CandidateProviderBindingV1,
    universe: BindingUniverse,
    captured: CapturedInput,
    prior: TennisState | None,
) -> CandidateParserEvidenceV1:
    """Return redacted parser evidence for one durable candidate capture."""
    if type(binding) is not CandidateProviderBindingV1:
        raise TypeError("exact CandidateProviderBindingV1 required")
    if type(universe) is not BindingUniverse:
        raise TypeError("exact BindingUniverse required")
    if type(captured) is not CapturedInput:
        raise TypeError("exact CapturedInput required")
    if captured.event_type not in {
        _SUMMARY_EVENT_TYPE,
        _TIMELINE_EVENT_TYPE,
        _TRANSPORT_ERROR_EVENT_TYPE,
    } or type(captured.event_version) is not int:
        raise _candidate_error("candidate_route_unknown")
    capture_digest = _capture_envelope_sha256(captured)
    try:
        context = _candidate_context(
            binding=binding,
            universe=universe,
            captured=captured,
        )
        if captured.event_type == _SUMMARY_EVENT_TYPE:
            if prior is not None:
                _validate_prior(prior, context)
            events: tuple[
                ProviderSnapshot | ProviderPoint | ProviderLifecycle,
                ...,
            ] = (_parse_summary(context),)
            capabilities = _all_capabilities(True)
            outcome = "accepted"
            reason = None
        elif captured.event_type == _TIMELINE_EVENT_TYPE:
            _validate_prior(prior, context)
            timeline = _parse_timeline(context, prior=prior)
            events = timeline.events
            capabilities = timeline.capabilities
            outcome = "accepted"
            reason = None
        else:
            _validate_prior(prior, context)
            _validate_transport_error_context(context)
            events = ()
            capabilities = _all_capabilities(False)
            outcome = "ignored"
            reason = ExpertIgnoreReasonV1.EVENT_NOT_RELEVANT.value
    except SportradarTennisV3CandidateError as error:
        events = ()
        capabilities = _all_capabilities(False)
        outcome = "rejected"
        reason = _reject_reason_for_error(error)
    except Exception:
        events = ()
        capabilities = _all_capabilities(False)
        outcome = "rejected"
        reason = ExpertRejectReasonV1.NORMALIZER_EXCEPTION.value
    return _parser_evidence(
        captured=captured,
        capture_envelope_sha256=capture_digest,
        parser_outcome=outcome,
        reason=reason,
        events=events,
        capabilities=capabilities,
    )
