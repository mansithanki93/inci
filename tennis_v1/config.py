"""Immutable, fail-closed Tennis v1 research configuration."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from .canonical import canonical_json_bytes
from .pinned_file import PinnedFileError, read_pinned_file


CONFIG_MAX_BYTES = 64 * 1024
EXPECTED_KEYS = frozenset(
    {
        "schema_version",
        "state_root",
        "provider_manifest_path",
        "provider_manifest_sha256",
        "trusted_permission_reviewer_ids",
        "trusted_qualification_issuer_ids",
        "observed_pool_limit",
        "paper_position_limit",
    }
)
SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")


class ConfigError(ValueError):
    """Raised when immutable Tennis v1 configuration is invalid."""


@dataclass(frozen=True, slots=True)
class TennisV1Config:
    schema_version: int
    state_root: Path
    provider_manifest_path: Path
    provider_manifest_sha256: str
    trusted_permission_reviewer_ids: tuple[str, ...]
    trusted_qualification_issuer_ids: tuple[str, ...]
    observed_pool_limit: int
    paper_position_limit: int
    source_file_sha256: str
    canonical_sha256: str


def _reject_float(_: str) -> object:
    raise ConfigError("configuration floats are not permitted")


def _reject_constant(_: str) -> object:
    raise ConfigError("configuration constants are not permitted")


def _duplicate_free_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("configuration contains duplicate JSON keys")
        result[key] = value
    return result


def _parse_config_bytes(content: bytes) -> dict[str, object]:
    if content.startswith(b"\xef\xbb\xbf"):
        raise ConfigError("configuration must not contain a UTF-8 BOM")
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConfigError("configuration is not valid UTF-8") from error
    try:
        raw = json.loads(
            decoded,
            object_pairs_hook=_duplicate_free_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except ConfigError:
        raise
    except (json.JSONDecodeError, ValueError) as error:
        raise ConfigError("configuration is not valid strict JSON") from error
    if not isinstance(raw, dict) or set(raw) != EXPECTED_KEYS:
        raise ConfigError("configuration keys do not match schema v1")
    return raw


def _safe_external_path(value: object, repo_root: str | Path) -> Path:
    if not isinstance(value, str):
        raise ConfigError("configured paths must be strings")
    candidate = Path(value).expanduser()
    repository = Path(repo_root).expanduser()
    if not candidate.is_absolute() or not repository.is_absolute():
        raise ConfigError("configured paths must be absolute")
    candidate = Path(os.path.abspath(candidate))
    repository = Path(os.path.abspath(repository))
    current = Path(candidate.anchor)
    for component in candidate.parts[1:]:
        current /= component
        if current.is_symlink():
            raise ConfigError("configured paths must not traverse symlinks")
    normalized = candidate.resolve(strict=False)
    normalized_repository = repository.resolve(strict=False)
    try:
        normalized.relative_to(normalized_repository)
    except ValueError:
        return normalized
    raise ConfigError("configured paths must be outside the repository")


def _trusted_identifiers(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{field} must be a nonempty list")
    if len(value) > 64 or any(not isinstance(item, str) or not SAFE_IDENTIFIER.fullmatch(item) for item in value):
        raise ConfigError(f"{field} contains an invalid identifier")
    identifiers = tuple(value)
    if len(set(identifiers)) != len(identifiers) or identifiers != tuple(sorted(identifiers)):
        raise ConfigError(f"{field} must be unique and sorted")
    return identifiers


def _bounded_integer(value: object, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ConfigError(f"{field} is outside its permitted range")
    return value


def _canonical_projection(config: TennisV1Config) -> dict[str, object]:
    return {
        "schema_version": config.schema_version,
        "state_root": str(config.state_root),
        "provider_manifest_path": str(config.provider_manifest_path),
        "provider_manifest_sha256": config.provider_manifest_sha256,
        "trusted_permission_reviewer_ids": list(config.trusted_permission_reviewer_ids),
        "trusted_qualification_issuer_ids": list(config.trusted_qualification_issuer_ids),
        "observed_pool_limit": config.observed_pool_limit,
        "paper_position_limit": config.paper_position_limit,
    }


def canonical_config_sha256(config: TennisV1Config) -> str:
    """Hash the semantic configuration, excluding its derived provenance hashes."""
    if not isinstance(config, TennisV1Config):
        raise ConfigError("canonical configuration requires TennisV1Config")
    return hashlib.sha256(canonical_json_bytes(_canonical_projection(config))).hexdigest()


def load_config(path: str | Path, *, repo_root: str | Path) -> TennisV1Config:
    """Load a single external config file through the shared pinned-file boundary."""
    try:
        pinned = read_pinned_file(
            path,
            expected_sha256=None,
            repo_root=repo_root,
            max_bytes=CONFIG_MAX_BYTES,
        )
    except PinnedFileError as error:
        raise ConfigError("configuration file failed immutable loading") from error
    raw = _parse_config_bytes(pinned.data)
    if raw["schema_version"] != 1 or isinstance(raw["schema_version"], bool):
        raise ConfigError("unsupported configuration schema_version")
    provider_manifest_sha256 = raw["provider_manifest_sha256"]
    if not isinstance(provider_manifest_sha256, str) or not SHA256_HEX.fullmatch(provider_manifest_sha256):
        raise ConfigError("provider_manifest_sha256 must be lowercase SHA-256")

    provisional = TennisV1Config(
        schema_version=1,
        state_root=_safe_external_path(raw["state_root"], repo_root),
        provider_manifest_path=_safe_external_path(raw["provider_manifest_path"], repo_root),
        provider_manifest_sha256=provider_manifest_sha256,
        trusted_permission_reviewer_ids=_trusted_identifiers(
            raw["trusted_permission_reviewer_ids"], "trusted_permission_reviewer_ids"
        ),
        trusted_qualification_issuer_ids=_trusted_identifiers(
            raw["trusted_qualification_issuer_ids"], "trusted_qualification_issuer_ids"
        ),
        observed_pool_limit=_bounded_integer(raw["observed_pool_limit"], "observed_pool_limit", 10),
        paper_position_limit=_bounded_integer(raw["paper_position_limit"], "paper_position_limit", 3),
        source_file_sha256=pinned.sha256,
        canonical_sha256="",
    )
    return replace(provisional, canonical_sha256=canonical_config_sha256(provisional))


def session_wal_path(config: TennisV1Config, session_id: str) -> Path:
    """Return the one confined WAL location for a canonical UUID session ID."""
    if not isinstance(config, TennisV1Config) or not isinstance(session_id, str):
        raise ConfigError("session configuration or identifier is invalid")
    try:
        parsed = uuid.UUID(session_id)
    except (ValueError, AttributeError) as error:
        raise ConfigError("session identifier must be a canonical UUID") from error
    if str(parsed) != session_id:
        raise ConfigError("session identifier must be a lowercase canonical UUID")
    return config.state_root / "sessions" / f"{session_id}.wal"
