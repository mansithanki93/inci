"""Closed, code-owned adapter and quota contracts for Tennis v1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from enum import Enum
import hashlib
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat

from .canonical import canonical_json_bytes


ADAPTER_CLOSURE_DOMAIN = b"INCI-ADAPTER-CLOSURE-V1\0"
AUTH_CONTRACT_DOMAIN = b"INCI-AUTH-CONTRACT-V1\0"
QUOTA_CONTRACT_DOMAIN = b"INCI-QUOTA-CONTRACT-V1\0"
MICROSECONDS_PER_MINUTE = 60_000_000
MICROSECONDS_PER_HOUR = 3_600_000_000
SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")
SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")
ALLOWED_FORMATS = frozenset({"rest_json", "websocket_json", "ndjson"})
_FileIdentity = tuple[int, int, int, int, int, int, int, int]


class AdapterContractError(ValueError):
    """Raised when the code-owned adapter contract cannot be proven."""


class AuthMode(str, Enum):
    PUBLIC = "public"
    API_KEY = "api_key"
    OAUTH_CLIENT = "oauth_client"


@dataclass(frozen=True, slots=True)
class ProviderQuotas:
    requests_per_rolling_60_seconds: int
    requests_per_utc_calendar_day: int
    requests_per_rolling_second: int
    max_connections: int
    max_subscriptions: int
    resync_requests_per_rolling_hour: int


@dataclass(frozen=True, slots=True)
class AuthContract:
    mode: AuthMode
    credential_env_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdapterUsagePlan:
    startup_requests_fixed: int
    startup_requests_per_match: int
    steady_requests_per_minute_fixed: int
    steady_requests_per_minute_per_match: int
    resync_requests_per_match: int
    max_resyncs_per_match_per_hour: int
    max_connections: int
    subscriptions_per_match: int


@dataclass(frozen=True, slots=True)
class AdapterContract:
    provider_id: str
    product_tier: str
    adapter_id: str
    adapter_code_sha256: str
    auth: AuthContract
    usage: AdapterUsagePlan
    formats: tuple[str, ...]
    auth_contract_sha256: str
    quota_contract_sha256: str


@dataclass(frozen=True, slots=True)
class _AdapterFilePin:
    path: str
    length: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _AdapterContractSpec:
    provider_id: str
    product_tier: str
    adapter_id: str
    auth: AuthContract
    usage: AdapterUsagePlan
    formats: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _AdapterRegistration:
    module_paths: tuple[str, ...]
    spec: _AdapterContractSpec
    expected_entries: tuple[_AdapterFilePin, ...] = ()


# Phase 1 deliberately has no production provider adapter.
_ADAPTER_REGISTRY: dict[tuple[str, str], _AdapterRegistration] = {}


def _safe_id(value: object, field: str) -> str:
    if type(value) is not str or SAFE_IDENTIFIER.fullmatch(value) is None:
        raise AdapterContractError(f"{field}: invalid_identifier")
    return value


def _validate_paths(paths: object) -> tuple[str, ...]:
    if type(paths) is not tuple or not paths:
        raise AdapterContractError("adapter closure is empty")
    normalized: list[str] = []
    for value in paths:
        if type(value) is not str:
            raise AdapterContractError("adapter closure path is invalid")
        pure = PurePosixPath(value)
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in ("", ".", "..") for part in pure.parts)
            or pure.suffix != ".py"
            or pure.as_posix() != value
        ):
            raise AdapterContractError("adapter closure path is invalid")
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise AdapterContractError("adapter closure contains duplicate paths")
    if tuple(sorted(normalized)) != tuple(normalized):
        raise AdapterContractError("adapter closure paths must be sorted")
    return tuple(normalized)


def _open_root(root: Path) -> int:
    if os.name != "posix" or not all(
        hasattr(os, name) for name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW")
    ):
        raise AdapterContractError("adapter closure is unsupported on this runtime")
    try:
        descriptor = os.open(
            os.fspath(root), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        )
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise AdapterContractError("adapter package root is invalid")
        return descriptor
    except AdapterContractError:
        raise
    except OSError as error:
        raise AdapterContractError("adapter package root cannot be opened safely") from error


def _file_identity(value: os.stat_result) -> _FileIdentity:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_component(
    root_descriptor: int, relative_path: str
) -> tuple[bytes, _FileIdentity]:
    directory_descriptor = os.dup(root_descriptor)
    file_descriptor = -1
    try:
        parts = PurePosixPath(relative_path).parts
        for part in parts[:-1]:
            next_descriptor = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
            if not stat.S_ISDIR(os.fstat(directory_descriptor).st_mode):
                raise AdapterContractError("adapter closure directory is invalid")
        file_descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_descriptor,
        )
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise AdapterContractError(
                "adapter closure component must be a single-link regular file"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > 4 * 1024 * 1024:
                raise AdapterContractError("adapter closure component is oversized")
        after = os.fstat(file_descriptor)
        if _file_identity(before) != _file_identity(after):
            raise AdapterContractError("adapter closure component changed while reading")
        return b"".join(chunks), _file_identity(before)
    except AdapterContractError:
        raise
    except OSError as error:
        raise AdapterContractError(
            "adapter closure component cannot be opened safely"
        ) from error
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        os.close(directory_descriptor)


def _open_relative_directory(
    root_descriptor: int, directory: PurePosixPath
) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for part in directory.parts:
            if part == ".":
                continue
            next_descriptor = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError as error:
        os.close(descriptor)
        raise AdapterContractError("adapter directory cannot be inspected safely") from error


def _directory_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _recursive_python_files(
    directory_descriptor: int, relative_directory: PurePosixPath
) -> dict[str, _FileIdentity]:
    before = os.fstat(directory_descriptor)
    if not stat.S_ISDIR(before.st_mode):
        raise AdapterContractError("adapter closure directory is invalid")
    discovered: dict[str, _FileIdentity] = {}
    try:
        for name in sorted(os.listdir(directory_descriptor)):
            if type(name) is not str or name in ("", ".", ".."):
                raise AdapterContractError("adapter directory entry is invalid")
            value = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
            if stat.S_ISLNK(value.st_mode):
                raise AdapterContractError("adapter directory contains a symlink")
            child = (
                PurePosixPath(name)
                if relative_directory == PurePosixPath(".")
                else relative_directory / name
            )
            if stat.S_ISDIR(value.st_mode):
                child_descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=directory_descriptor,
                )
                try:
                    discovered.update(_recursive_python_files(child_descriptor, child))
                finally:
                    os.close(child_descriptor)
            elif name.endswith(".py"):
                if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
                    raise AdapterContractError(
                        "adapter Python file must be a single-link regular file"
                    )
                discovered[child.as_posix()] = _file_identity(value)
        after = os.fstat(directory_descriptor)
        if _directory_identity(before) != _directory_identity(after):
            raise AdapterContractError("adapter directory changed while inspecting")
        return discovered
    except OSError as error:
        raise AdapterContractError("adapter directory cannot be inspected safely") from error


def _closure_scan_directory(paths: tuple[str, ...]) -> PurePosixPath:
    parents = [PurePosixPath(value).parent.as_posix() for value in paths]
    common = posixpath.commonpath(parents)
    return PurePosixPath(".") if common in ("", ".") else PurePosixPath(common)


def _load_closure(module_paths: object) -> tuple[_AdapterFilePin, ...]:
    paths = _validate_paths(module_paths)
    try:
        root = Path(__file__).resolve(strict=True).parent
    except OSError as error:
        raise AdapterContractError("adapter package root cannot be resolved") from error
    root_descriptor = _open_root(root)
    scan_directory = _closure_scan_directory(paths)
    scan_descriptor = -1
    try:
        scan_descriptor = _open_relative_directory(root_descriptor, scan_directory)
        expected = set(paths)
        if set(_recursive_python_files(scan_descriptor, scan_directory)) != expected:
            raise AdapterContractError("adapter directory contains an unexpected Python file")
        entries: list[_AdapterFilePin] = []
        identities: set[_FileIdentity] = set()
        for relative_path in paths:
            content, identity = _read_component(root_descriptor, relative_path)
            if identity in identities:
                raise AdapterContractError("adapter closure aliases one file twice")
            identities.add(identity)
            entries.append(
                _AdapterFilePin(
                    path=relative_path,
                    length=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
        if set(_recursive_python_files(scan_descriptor, scan_directory)) != expected:
            raise AdapterContractError("adapter directory contains an unexpected Python file")
        verified_entries: list[_AdapterFilePin] = []
        verified_identities: dict[str, _FileIdentity] = {}
        for relative_path in paths:
            content, identity = _read_component(root_descriptor, relative_path)
            if identity in verified_identities.values():
                raise AdapterContractError("adapter closure aliases one file twice")
            verified_identities[relative_path] = identity
            verified_entries.append(
                _AdapterFilePin(
                    path=relative_path,
                    length=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
        if tuple(verified_entries) != tuple(entries):
            raise AdapterContractError("adapter closure components changed while reading")
        final_files = _recursive_python_files(scan_descriptor, scan_directory)
        if set(final_files) != expected:
            raise AdapterContractError("adapter directory contains an unexpected Python file")
        if any(final_files[path] != verified_identities[path] for path in paths):
            raise AdapterContractError("adapter closure components changed while reading")
        return tuple(verified_entries)
    except AdapterContractError:
        raise
    except OSError as error:
        raise AdapterContractError("adapter closure cannot be inspected safely") from error
    finally:
        if scan_descriptor >= 0:
            os.close(scan_descriptor)
        os.close(root_descriptor)


def _closure_sha256(entries: tuple[_AdapterFilePin, ...]) -> str:
    projection = [
        {"path": item.path, "length": item.length, "sha256": item.sha256}
        for item in entries
    ]
    return hashlib.sha256(
        ADAPTER_CLOSURE_DOMAIN + canonical_json_bytes(projection)
    ).hexdigest()


def _validate_usage(usage: object) -> AdapterUsagePlan:
    if type(usage) is not AdapterUsagePlan:
        raise AdapterContractError("adapter usage contract is invalid")
    for name in (
        "startup_requests_fixed",
        "startup_requests_per_match",
        "steady_requests_per_minute_fixed",
        "steady_requests_per_minute_per_match",
        "resync_requests_per_match",
        "max_resyncs_per_match_per_hour",
    ):
        if type(getattr(usage, name)) is not int or getattr(usage, name) < 0:
            raise AdapterContractError(f"{name}: invalid_nonnegative_integer")
    for name in ("max_connections", "subscriptions_per_match"):
        if type(getattr(usage, name)) is not int or getattr(usage, name) <= 0:
            raise AdapterContractError(f"{name}: invalid_positive_integer")
    return usage


def _validate_auth(auth: object) -> AuthContract:
    if type(auth) is not AuthContract or type(auth.mode) is not AuthMode:
        raise AdapterContractError("adapter auth contract is invalid")
    names = auth.credential_env_names
    if (
        type(names) is not tuple
        or any(type(name) is not str or ENV_NAME.fullmatch(name) is None for name in names)
        or len(set(names)) != len(names)
        or tuple(sorted(names)) != names
    ):
        raise AdapterContractError("adapter credential environment names are invalid")
    if auth.mode is AuthMode.PUBLIC and names:
        raise AdapterContractError("public adapter cannot require credentials")
    if auth.mode is not AuthMode.PUBLIC and not names:
        raise AdapterContractError("authenticated adapter must name credential variables")
    return auth


def _validate_contract_spec(value: object) -> _AdapterContractSpec:
    if type(value) is not _AdapterContractSpec:
        raise AdapterContractError("adapter contract declaration is invalid")
    provider_id = _safe_id(value.provider_id, "provider_id")
    product_tier = _safe_id(value.product_tier, "product_tier")
    adapter_id = _safe_id(value.adapter_id, "adapter_id")
    auth = _validate_auth(value.auth)
    usage = _validate_usage(value.usage)
    formats = value.formats
    if (
        type(formats) is not tuple
        or not formats
        or any(type(item) is not str or item not in ALLOWED_FORMATS for item in formats)
        or len(set(formats)) != len(formats)
        or tuple(sorted(formats)) != formats
    ):
        raise AdapterContractError("adapter formats are invalid")
    return _AdapterContractSpec(
        provider_id=provider_id,
        product_tier=product_tier,
        adapter_id=adapter_id,
        auth=auth,
        usage=usage,
        formats=formats,
    )


def _capture_adapter_registration(
    *,
    module_paths: tuple[str, ...],
    spec: _AdapterContractSpec,
) -> _AdapterRegistration:
    """Capture immutable code and declaration pins without executing an adapter."""
    paths = _validate_paths(module_paths)
    declaration = _validate_contract_spec(spec)
    entries = _load_closure(paths)
    return _AdapterRegistration(
        module_paths=paths,
        spec=declaration,
        expected_entries=entries,
    )


def _usage_projection(usage: AdapterUsagePlan) -> dict[str, int]:
    if type(usage) is not AdapterUsagePlan:
        raise AdapterContractError("adapter usage contract is invalid")
    return {name: getattr(usage, name) for name in AdapterUsagePlan.__dataclass_fields__}


def _validate_expected_entries(
    value: object,
    module_paths: tuple[str, ...],
) -> tuple[_AdapterFilePin, ...]:
    if type(value) is not tuple or not value:
        raise AdapterContractError("adapter registration pins are invalid")
    for entry, path in zip(value, module_paths, strict=False):
        if (
            type(entry) is not _AdapterFilePin
            or type(entry.path) is not str
            or entry.path != path
            or type(entry.length) is not int
            or entry.length < 0
            or type(entry.sha256) is not str
            or SHA256_HEX.fullmatch(entry.sha256) is None
        ):
            raise AdapterContractError("adapter registration pins are invalid")
    if len(value) != len(module_paths):
        raise AdapterContractError("adapter registration pins are invalid")
    return value


def _validated_registry_snapshot(
    value: object,
) -> dict[tuple[str, str], _AdapterRegistration]:
    if type(value) is not dict:
        raise AdapterContractError("adapter registry is invalid")
    validated_entries: list[
        tuple[tuple[str, str], _AdapterRegistration]
    ] = []
    for key, registration in tuple(dict.items(value)):
        if (
            type(key) is not tuple
            or len(key) != 2
            or type(key[0]) is not str
            or type(key[1]) is not str
            or type(registration) is not _AdapterRegistration
        ):
            raise AdapterContractError("adapter registry is invalid")
        provider = _safe_id(key[0], "provider_id")
        tier = _safe_id(key[1], "product_tier")
        validated_entries.append(((provider, tier), registration))
    return dict(validated_entries)


def load_active_adapter_contract(
    *, provider_id: str, product_tier: str
) -> AdapterContract:
    """Resolve a code-owned declaration bound to an exact source-byte closure."""
    provider = _safe_id(provider_id, "provider_id")
    tier = _safe_id(product_tier, "product_tier")
    registry = _validated_registry_snapshot(_ADAPTER_REGISTRY)
    registration = registry.get((provider, tier))
    if type(registration) is not _AdapterRegistration:
        raise AdapterContractError("no active adapter is registered")
    paths = _validate_paths(registration.module_paths)
    expected_entries = _validate_expected_entries(
        registration.expected_entries,
        paths,
    )
    entries = _load_closure(paths)
    if entries != expected_entries:
        raise AdapterContractError(
            "active adapter files differ from code-owned registration pins"
        )
    spec = _validate_contract_spec(registration.spec)
    if spec.provider_id != provider or spec.product_tier != tier:
        raise AdapterContractError("adapter contract identity mismatch")
    auth_projection = {
        "mode": spec.auth.mode.value,
        "credential_env_names": list(spec.auth.credential_env_names),
    }
    auth_sha = hashlib.sha256(
        AUTH_CONTRACT_DOMAIN + canonical_json_bytes(auth_projection)
    ).hexdigest()
    quota_sha = hashlib.sha256(
        QUOTA_CONTRACT_DOMAIN + canonical_json_bytes(_usage_projection(spec.usage))
    ).hexdigest()
    return AdapterContract(
        provider_id=spec.provider_id,
        product_tier=spec.product_tier,
        adapter_id=spec.adapter_id,
        adapter_code_sha256=_closure_sha256(entries),
        auth=spec.auth,
        usage=spec.usage,
        formats=spec.formats,
        auth_contract_sha256=auth_sha,
        quota_contract_sha256=quota_sha,
    )


def ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def timedelta_microseconds(delta: timedelta) -> int:
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000
        + delta.microseconds
    )


def _utc_datetime(value: object, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise AdapterContractError(f"{field}: invalid_utc_datetime")
    return value


def derive_quota_demand(
    adapter: AdapterContract, request: object
) -> ProviderQuotas:
    """Derive the exact conservative integer quota demand for one request."""
    if type(adapter) is not AdapterContract:
        raise AdapterContractError("adapter contract is invalid")
    usage = _validate_usage(adapter.usage)
    matches = getattr(request, "requested_matches", None)
    if type(matches) is not int or matches <= 0:
        raise AdapterContractError("requested_matches: invalid_positive_integer")
    start = _utc_datetime(getattr(request, "now_utc", None), "now_utc")
    end = _utc_datetime(getattr(request, "session_end_utc", None), "session_end_utc")
    if start >= end:
        raise AdapterContractError("research session interval is invalid")
    startup = usage.startup_requests_fixed + matches * usage.startup_requests_per_match
    steady_minute = (
        usage.steady_requests_per_minute_fixed
        + matches * usage.steady_requests_per_minute_per_match
    )
    resync_hour = (
        matches
        * usage.resync_requests_per_match
        * usage.max_resyncs_per_match_per_hour
    )
    worst_cluster = startup + steady_minute + resync_hour
    day_demands: list[int] = []
    current_day = start.date()
    final_day = (end - timedelta(microseconds=1)).date()
    while current_day <= final_day:
        day_start = datetime.combine(current_day, time.min, tzinfo=timezone.utc)
        try:
            day_end = day_start + timedelta(days=1)
        except OverflowError:
            day_end = datetime.max.replace(tzinfo=timezone.utc)
        overlap_us = timedelta_microseconds(
            min(end, day_end) - max(start, day_start)
        )
        if overlap_us > 0:
            day_demands.append(
                (startup if start.date() == current_day else 0)
                + ceil_div(overlap_us, MICROSECONDS_PER_MINUTE) * steady_minute
                + ceil_div(overlap_us, MICROSECONDS_PER_HOUR) * resync_hour
            )
        if current_day == final_day:
            break
        current_day += timedelta(days=1)
    return ProviderQuotas(
        requests_per_rolling_60_seconds=worst_cluster,
        requests_per_utc_calendar_day=max(day_demands),
        requests_per_rolling_second=worst_cluster,
        max_connections=usage.max_connections,
        max_subscriptions=matches * usage.subscriptions_per_match,
        resync_requests_per_rolling_hour=resync_hour,
    )
