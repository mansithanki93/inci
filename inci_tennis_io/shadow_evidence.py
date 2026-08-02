"""Private durable storage for read-only unqualified tennis shadow evidence."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import errno
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import pwd
from re import ASCII, compile as pattern_compile
import stat
import time
from typing import Any
import uuid


_MAXIMUM_KALSHI_FRAME_BYTES = 1_048_576
_MAXIMUM_SPORTRADAR_CAPTURE_BYTES = 8_388_608
_ZERO_DIGEST = "0" * 64
_COMMIT_WATERMARK_SCHEMA = "inci-tennis-shadow-commit-watermark-v1"
_PROTOCOL_EPOCH_SCHEMA = "inci-tennis-shadow-protocol-epoch-v1"
_DIGEST_PATTERN = pattern_compile(r"[0-9a-f]{64}\Z", flags=ASCII)
_TICKER_PATTERN = pattern_compile(
    r"[A-Z0-9][A-Z0-9._-]{0,127}\Z", flags=ASCII
)
_MATCH_PATTERN = pattern_compile(
    r"sr:sport_event:[1-9][0-9]*\Z", flags=ASCII
)
_SAFE_CODE_PATTERN = pattern_compile(
    r"(?:shadow|kalshi|sportradar)_[a-z0-9_]{1,96}\Z", flags=ASCII
)
_DECIMAL_PATTERN = pattern_compile(
    r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z", flags=ASCII
)
_OBSERVATION_REASONS = frozenset(
    {
        "provider_summary_captured",
        "provider_timeline_captured",
        "candidate_snapshot_applied",
        "candidate_delta_applied",
        "candidate_book_incomplete",
        "candidate_book_ready",
        "empty_book",
        "candidate_message_ignored",
        "kalshi_sequence_gap",
        "kalshi_sequence_duplicate",
        "kalshi_sequence_out_of_order",
        "kalshi_stream_disconnected",
        "kalshi_parser_error",
        "kalshi_resnapshot_requested",
        "kalshi_reconnected",
    }
)
_KALSHI_STATUSES = frozenset(
    {
        "waiting",
        "candidate",
        "gap",
        "duplicate",
        "out_of_order",
        "disconnected",
        "error",
        "ignored",
        "incomplete",
        "snapshot_required",
    }
)
_PROGRESSIONS = frozenset(
    {
        "initial",
        "initial_timeline",
        "advanced",
        "corrected",
        "corrected_and_advanced",
        "score_changed",
        "unchanged",
    }
)
_TERMINAL_REASONS = frozenset(
    {
        "duration_elapsed",
        "operator_interrupt",
        "closed",
        "cancelled",
        "abandoned",
        "halted",
    }
)
_PRICE_ONLY_OBSERVATION_REASONS = frozenset(
    {
        "candidate_snapshot_applied",
        "candidate_delta_applied",
        "candidate_book_incomplete",
        "candidate_book_ready",
        "empty_book",
        "candidate_message_ignored",
        "kalshi_sequence_gap",
        "kalshi_sequence_duplicate",
        "kalshi_sequence_out_of_order",
        "kalshi_stream_disconnected",
        "kalshi_parser_error",
        "kalshi_resnapshot_requested",
        "kalshi_reconnected",
    }
)
_INITIAL_BOOK_STATES = frozenset({"empty", "one_sided", "two_sided"})
_PRICE_ONLY_TERMINAL_REASONS = frozenset(
    {"duration_elapsed", "operator_interrupt", "cancelled", "halted"}
)
_CHAIN_FIELDS = frozenset(
    {
        "session_id",
        "row_number",
        "previous_row_sha256",
        "row_sha256",
    }
)
_KALSHI_CAPTURE_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "trust",
        "reason",
        "raw_path",
        "raw_sha256",
        "captured_wall_ns",
        "captured_monotonic_ns",
        "clock_uncertainty_ns",
        "physical_connection_generation",
    }
)
_OBSERVATION_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "trust",
        "reason",
        "observed_wall_ns",
        "observed_monotonic_ns",
        "clock_uncertainty_ns",
        "provider_match_id",
        "market_tickers",
        "provider_generated_wall_ns",
        "provider_captured_wall_ns",
        "provider_request_started_wall_ns",
        "provider_request_started_monotonic_ns",
        "provider_request_completed_wall_ns",
        "provider_request_completed_monotonic_ns",
        "provider_clock_uncertainty_ns",
        "provider_raw_path",
        "provider_raw_sha256",
        "home_player_name",
        "away_player_name",
        "match_status",
        "sets",
        "games",
        "points",
        "server",
        "sportradar_age_ns",
        "progression",
        "last_event_id",
        "last_event_type",
        "last_event_result",
        "kalshi_raw_path",
        "kalshi_raw_sha256",
        "kalshi_captured_wall_ns",
        "kalshi_captured_monotonic_ns",
        "kalshi_generation",
        "kalshi_sequence",
        "kalshi_age_ns",
        "kalshi_status",
        "home_ticker",
        "home_yes_bid",
        "home_yes_ask",
        "home_bid_depth",
        "home_ask_depth",
        "away_ticker",
        "away_yes_bid",
        "away_yes_ask",
        "away_bid_depth",
        "away_ask_depth",
        "sportradar_captures",
        "kalshi_frames",
    }
)
_TERMINAL_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "trust",
        "reason",
        "code",
        "ended_wall_ns",
        "ended_monotonic_ns",
        "provider_match_id",
        "market_tickers",
        "sportradar_captures",
        "kalshi_frames",
    }
)
_RESOLUTION_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "trust",
        "reason",
        "selected_wall_ns",
        "provider_match_id",
        "provider_start_wall_ns",
        "event_ticker",
        "home_player_name",
        "away_player_name",
        "market_tickers",
        "provider_discovery_raw_path",
        "provider_discovery_raw_sha256",
        "kalshi_catalog_sha256",
        "resolver_snapshot_sha256",
        "resolver_rule_version",
    }
)
_AUTO_TERMINAL_FIELDS = _TERMINAL_FIELDS | frozenset(
    {"mapping_mode", "resolution_row_sha256"}
)
_PRICE_ONLY_SESSION_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "trust",
        "reason",
        "selected_wall_ns",
        "selected_monotonic_ns",
        "event_ticker",
        "player_a_name",
        "player_b_name",
        "market_tickers",
        "scheduled_start_wall_ns",
        "catalog_sport",
        "catalog_scope",
        "catalog_queried_competitions",
        "catalog_series_ticker",
        "catalog_milestone_id",
        "catalog_milestone_league",
        "initial_book_state",
        "initial_market_a_ticker",
        "initial_market_a_yes_bid",
        "initial_market_a_yes_ask",
        "initial_market_a_bid_depth",
        "initial_market_a_ask_depth",
        "initial_market_b_ticker",
        "initial_market_b_yes_bid",
        "initial_market_b_yes_ask",
        "initial_market_b_bid_depth",
        "initial_market_b_ask_depth",
        "provider_discovery_state",
        "provider_discovery_reason",
        "provider_discovery_raw_path",
        "provider_discovery_raw_sha256",
        "kalshi_catalog_sha256",
        "resolver_snapshot_sha256",
        "resolver_version",
        "registry_digest",
        "authority_scope",
        "execution_authorized",
        "score_feed",
        "predecessor_session_id",
        "predecessor_terminal_row_sha256",
    }
)
_PRICE_ONLY_CAPTURE_FIELDS = _KALSHI_CAPTURE_FIELDS
_PRICE_ONLY_OBSERVATION_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "trust",
        "reason",
        "observed_wall_ns",
        "observed_monotonic_ns",
        "clock_uncertainty_ns",
        "event_ticker",
        "market_tickers",
        "kalshi_raw_path",
        "kalshi_raw_sha256",
        "kalshi_captured_wall_ns",
        "kalshi_captured_monotonic_ns",
        "kalshi_generation",
        "kalshi_sequence",
        "kalshi_age_ns",
        "kalshi_status",
        "market_a_ticker",
        "market_a_yes_bid",
        "market_a_yes_ask",
        "market_a_bid_depth",
        "market_a_ask_depth",
        "market_b_ticker",
        "market_b_yes_bid",
        "market_b_yes_ask",
        "market_b_bid_depth",
        "market_b_ask_depth",
        "kalshi_frames",
    }
)
_PRICE_ONLY_TERMINAL_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "trust",
        "reason",
        "code",
        "ended_wall_ns",
        "ended_monotonic_ns",
        "event_ticker",
        "market_tickers",
        "kalshi_frames",
        "mapping_mode",
        "session_row_sha256",
    }
)
_FIELDS_BY_KIND = {
    "resolution": _RESOLUTION_FIELDS | _CHAIN_FIELDS,
    "kalshi_capture": _KALSHI_CAPTURE_FIELDS | _CHAIN_FIELDS,
    "observation": _OBSERVATION_FIELDS | _CHAIN_FIELDS,
    "terminal": _TERMINAL_FIELDS | _CHAIN_FIELDS,
    "auto_terminal": _AUTO_TERMINAL_FIELDS | _CHAIN_FIELDS,
    "price_only_session": _PRICE_ONLY_SESSION_FIELDS | _CHAIN_FIELDS,
    "price_only_kalshi_capture": _PRICE_ONLY_CAPTURE_FIELDS | _CHAIN_FIELDS,
    "price_only_observation": (
        _PRICE_ONLY_OBSERVATION_FIELDS | _CHAIN_FIELDS
    ),
    "price_only_terminal": _PRICE_ONLY_TERMINAL_FIELDS | _CHAIN_FIELDS,
}
_SCHEMA_BY_KIND = {
    "resolution": "inci-tennis-unqualified-shadow-resolution-v1",
    "observation": "inci-tennis-unqualified-shadow-observation-v1",
    "kalshi_capture": "inci-tennis-unqualified-shadow-kalshi-capture-v1",
    "terminal": "inci-tennis-unqualified-shadow-terminal-v1",
    "auto_terminal": (
        "inci-tennis-unqualified-shadow-auto-terminal-v1"
    ),
    "price_only_session": "inci-tennis-price-only-session-v1",
    "price_only_kalshi_capture": "inci-tennis-price-only-kalshi-capture-v1",
    "price_only_observation": "inci-tennis-price-only-observation-v1",
    "price_only_terminal": "inci-tennis-price-only-terminal-v1",
}
_TRUST_BY_KIND = {
    "resolution": "unqualified_shadow",
    "observation": "unqualified_shadow",
    "kalshi_capture": "unqualified_shadow",
    "terminal": "unqualified_shadow",
    "auto_terminal": "unqualified_shadow",
    "price_only_session": "PRICE_ONLY",
    "price_only_kalshi_capture": "PRICE_ONLY",
    "price_only_observation": "PRICE_ONLY",
    "price_only_terminal": "PRICE_ONLY",
}


class ShadowEvidenceError(RuntimeError):
    """A fixed diagnostic code without payload or credential text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ShadowEvidenceError(code)


def default_shadow_state_root() -> Path:
    """Derive shadow state from the OS account, never repository config."""

    try:
        home = pwd.getpwuid(os.getuid()).pw_dir
    except (KeyError, OSError):
        _fail("shadow_evidence_os_account_unavailable")
    if type(home) is not str or not home or not os.path.isabs(home):
        _fail("shadow_evidence_os_account_unavailable")
    return Path(home) / ".local" / "state" / "inci" / "tennis-shadow"


def shadow_wall_ns() -> int:
    return time.time_ns()


def shadow_monotonic_ns() -> int:
    return time.monotonic_ns()


async def shadow_pause(seconds: float) -> None:
    await asyncio.sleep(seconds)


def shadow_kalshi_clock_observation() -> object:
    """Return one IO-owned paired clock sample for the Kalshi transport."""

    from .kalshi_readonly import KalshiClockObservation

    before = time.monotonic_ns()
    wall = time.time_ns()
    after = time.monotonic_ns()
    if (
        type(before) is not int
        or before < 0
        or type(wall) is not int
        or wall <= 0
        or type(after) is not int
        or after < before
    ):
        _fail("shadow_clock_invalid")
    return KalshiClockObservation(
        wall_ns=wall,
        monotonic_ns=before + (after - before) // 2,
        uncertainty_ns=after - before,
    )


@dataclass(frozen=True, slots=True)
class ShadowCredentialMaterial:
    sportradar_api_key: str
    kalshi_api_key_id: str
    kalshi_private_key_path: Path

    def __repr__(self) -> str:
        return "<ShadowCredentialMaterial redacted>"


def load_shadow_credential_material(
    environ: Mapping[str, str] | None = None,
) -> ShadowCredentialMaterial:
    """Validate required environment values at the IO boundary."""

    source = os.environ if environ is None else environ
    if not isinstance(source, Mapping):
        _fail("shadow_credentials_invalid")
    values = []
    for name in (
        "SPORTRADAR_API_KEY",
        "KALSHI_API_KEY_ID",
        "KALSHI_PRIVATE_KEY_PATH",
    ):
        value = source.get(name)
        if (
            type(value) is not str
            or not value
            or value != value.strip()
            or len(value) > 4_096
            or any(ord(character) < 32 for character in value)
        ):
            _fail("shadow_credentials_missing")
        values.append(value)
    key_path = Path(values[2])
    if not key_path.is_absolute():
        _fail("shadow_private_key_path_invalid")
    _validate_existing_regular_file(
        key_path,
        expected_mode=0o600,
        code="shadow_private_key_path_invalid",
        read_payload=False,
    )
    return ShadowCredentialMaterial(values[0], values[1], key_path)


@dataclass(frozen=True, slots=True)
class KalshiOnlyCredentialMaterial:
    kalshi_api_key_id: str
    kalshi_private_key_path: Path

    def __repr__(self) -> str:
        return "<KalshiOnlyCredentialMaterial redacted>"


def load_kalshi_only_credential_material(
    environ: Mapping[str, str] | None = None,
) -> KalshiOnlyCredentialMaterial:
    """Validate only the credentials required for Kalshi price observation."""

    source = os.environ if environ is None else environ
    if not isinstance(source, Mapping):
        _fail("shadow_credentials_invalid")
    values: list[str] = []
    for name in ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH"):
        value = source.get(name)
        if (
            type(value) is not str
            or not value
            or value != value.strip()
            or len(value) > 4_096
            or any(ord(character) < 32 for character in value)
        ):
            _fail("shadow_credentials_missing")
        values.append(value)
    key_path = Path(values[1])
    if not key_path.is_absolute():
        _fail("shadow_private_key_path_invalid")
    _validate_existing_regular_file(
        key_path,
        expected_mode=0o600,
        code="shadow_private_key_path_invalid",
        read_payload=False,
    )
    return KalshiOnlyCredentialMaterial(values[0], key_path)


@dataclass(frozen=True, slots=True)
class PersistedKalshiFrame:
    raw_path: str
    raw_sha256: str
    captured_wall_ns: int
    captured_monotonic_ns: int
    clock_uncertainty_ns: int
    physical_connection_generation: int


@dataclass(frozen=True, slots=True)
class ShadowMarketCandidate:
    ticker: str
    yes_bid: str | None
    yes_ask: str | None
    bid_depth: str | None
    ask_depth: str | None


@dataclass(frozen=True, slots=True)
class ShadowEvidenceObservation:
    observed_wall_ns: int
    observed_monotonic_ns: int
    clock_uncertainty_ns: int
    provider_match_id: str
    market_tickers: tuple[str, str]
    provider_generated_wall_ns: int
    provider_captured_wall_ns: int
    provider_request_started_wall_ns: int
    provider_request_started_monotonic_ns: int
    provider_request_completed_wall_ns: int
    provider_request_completed_monotonic_ns: int
    provider_clock_uncertainty_ns: int
    provider_raw_path: str
    provider_raw_sha256: str
    home_player_name: str
    away_player_name: str
    match_status: str | None
    sets: tuple[int | None, int | None]
    games: tuple[int | None, int | None]
    points: tuple[str, str]
    server: str | None
    sportradar_age_ns: int | None
    progression: str
    last_event_id: int | None
    last_event_type: str | None
    last_event_result: str | None
    kalshi_raw_path: str | None
    kalshi_raw_sha256: str | None
    kalshi_captured_wall_ns: int | None
    kalshi_captured_monotonic_ns: int | None
    kalshi_generation: int | None
    kalshi_sequence: int | None
    kalshi_age_ns: int | None
    kalshi_status: str
    home_market: ShadowMarketCandidate
    away_market: ShadowMarketCandidate
    reason: str
    sportradar_captures: int
    kalshi_frames: int


@dataclass(frozen=True, slots=True)
class ShadowResolutionEvidence:
    selected_wall_ns: int
    provider_match_id: str
    provider_start_wall_ns: int
    event_ticker: str
    home_player_name: str
    away_player_name: str
    market_tickers: tuple[str, str]
    provider_discovery_raw_path: str
    provider_discovery_raw_sha256: str
    kalshi_catalog_sha256: str
    resolver_snapshot_sha256: str
    resolver_rule_version: str


@dataclass(frozen=True, slots=True)
class PriceOnlySessionEvidence:
    selected_wall_ns: int
    selected_monotonic_ns: int
    event_ticker: str
    player_a_name: str
    player_b_name: str
    market_tickers: tuple[str, str]
    scheduled_start_wall_ns: int
    catalog_sport: str
    catalog_scope: str
    catalog_queried_competitions: tuple[str, ...]
    catalog_series_ticker: str
    catalog_milestone_id: str
    catalog_milestone_league: str | None
    initial_book_state: str
    initial_market_a: ShadowMarketCandidate
    initial_market_b: ShadowMarketCandidate
    provider_discovery_state: str
    provider_discovery_reason: str
    provider_discovery_raw_path: str | None
    provider_discovery_raw_sha256: str | None
    kalshi_catalog_sha256: str
    resolver_snapshot_sha256: str
    resolver_version: str
    registry_digest: str
    predecessor_session_id: str | None = None
    predecessor_terminal_row_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class PriceOnlyEvidenceObservation:
    observed_wall_ns: int
    observed_monotonic_ns: int
    clock_uncertainty_ns: int
    event_ticker: str
    market_tickers: tuple[str, str]
    kalshi_raw_path: str | None
    kalshi_raw_sha256: str | None
    kalshi_captured_wall_ns: int | None
    kalshi_captured_monotonic_ns: int | None
    kalshi_generation: int | None
    kalshi_sequence: int | None
    kalshi_age_ns: int | None
    kalshi_status: str
    market_a: ShadowMarketCandidate
    market_b: ShadowMarketCandidate
    reason: str
    kalshi_frames: int


@dataclass(frozen=True, slots=True)
class AuditedShadowSettlementSource:
    """An immutable settlement identity derived from a fully audited ledger."""

    session_path: Path
    ledger_sha256: str
    session_id: str
    mode: str
    event_ticker: str
    market_tickers: tuple[str, str]
    player_names: tuple[str, str]
    first_row_sha256: str
    terminal_row_sha256: str


class _ShadowSettlementSourceAuditLease:
    """Keep the source root shared-locked while its audit result is used."""

    def __init__(
        self, source: AuditedShadowSettlementSource, lock_fd: int
    ) -> None:
        self.source = source
        self._lock_fd: int | None = lock_fd

    def close(self) -> None:
        """Release the audit lock exactly once."""

        descriptor = self._lock_fd
        if descriptor is None:
            return
        self._lock_fd = None
        primary: BaseException | None = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except BaseException as error:
            primary = error
        try:
            os.close(descriptor)
        except BaseException as error:
            if primary is None:
                primary = error
        if primary is not None:
            raise primary

    def __enter__(self) -> AuditedShadowSettlementSource:
        if self._lock_fd is None:
            _fail("shadow_evidence_closed")
        return self.source

    def __exit__(self, exc_type: object, *_: object) -> None:
        if exc_type is None:
            self.close()
            return
        try:
            self.close()
        except BaseException:
            pass


def _private_directory(path: Path) -> None:
    if not isinstance(path, Path) or not path.is_absolute():
        _fail("shadow_evidence_state_invalid")
    existed = False
    try:
        path.lstat()
        existed = True
    except FileNotFoundError:
        pass
    except OSError:
        _fail("shadow_evidence_state_unavailable")
    if existed and path.is_symlink():
        _fail("shadow_evidence_state_unsafe")
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not existed:
            path.chmod(0o700)
        info = path.lstat()
    except OSError:
        _fail("shadow_evidence_state_unavailable")
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        _fail("shadow_evidence_state_unsafe")


def _validate_existing_private_directory(path: Path, *, code: str) -> None:
    try:
        info = path.lstat()
    except OSError:
        _fail(code)
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        _fail(code)


def _open_private_file(
    path: Path,
    *,
    create: bool,
    append: bool = False,
    exclusive: bool = False,
) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    flags = os.O_RDWR | nofollow | cloexec
    if append:
        flags |= os.O_APPEND
    created = False
    if create:
        try:
            descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            if exclusive:
                _fail("shadow_evidence_file_collision")
            try:
                descriptor = os.open(path, flags)
            except OSError:
                _fail("shadow_evidence_state_unsafe")
        except OSError:
            _fail("shadow_evidence_state_unavailable")
    else:
        try:
            descriptor = os.open(path, flags)
        except OSError:
            _fail("shadow_evidence_state_unsafe")
    try:
        if created:
            os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            _fail("shadow_evidence_state_unsafe")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _open_existing_private_readonly_file(path: Path, *, code: str) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow | cloexec)
    except OSError:
        _fail(code)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            _fail(code)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _write_all(descriptor: int, payload: bytes, code: str) -> None:
    offset = 0
    try:
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                _fail(code)
            offset += written
        os.fsync(descriptor)
    except ShadowEvidenceError:
        raise
    except OSError:
        _fail(code)


def _fsync_directory(path: Path) -> None:
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(path, os.O_RDONLY)
            os.fsync(descriptor)
        except OSError:
            _fail("shadow_evidence_write_failed")
        try:
            os.close(descriptor)
        except OSError:
            descriptor = None
            _fail("shadow_evidence_write_failed")
        descriptor = None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _validate_existing_regular_file(
    path: Path,
    *,
    expected_mode: int,
    code: str,
    read_payload: bool,
    maximum_bytes: int = _MAXIMUM_KALSHI_FRAME_BYTES,
) -> bytes | None:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow | cloexec)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != expected_mode
        ):
            _fail(code)
        if not read_payload:
            return None
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            return stream.read(maximum_bytes + 1)
    except ShadowEvidenceError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return None


def _positive_or_none(value: object) -> bool:
    return value is None or (type(value) is int and value >= 0)


def _decimal_text(value: object, *, price: bool) -> bool:
    if (
        type(value) is not str
        or _DECIMAL_PATTERN.fullmatch(value) is None
    ):
        return False
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return False
    return (
        Decimal("0") <= parsed <= Decimal("1")
        if price
        else parsed > Decimal("0")
    )


def _valid_market(value: object, ticker: str) -> bool:
    if type(value) is not ShadowMarketCandidate or value.ticker != ticker:
        return False
    if value.yes_bid is not None and not _decimal_text(value.yes_bid, price=True):
        return False
    if value.yes_ask is not None and not _decimal_text(value.yes_ask, price=True):
        return False
    if value.bid_depth is not None and not _decimal_text(value.bid_depth, price=False):
        return False
    if value.ask_depth is not None and not _decimal_text(value.ask_depth, price=False):
        return False
    return (value.yes_bid is None) == (value.bid_depth is None) and (
        value.yes_ask is None
    ) == (value.ask_depth is None)


def _valid_identity_text(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and len(value) <= 256
        and value == value.strip()
        and not any(ord(character) < 32 for character in value)
    )


def _validate_resolution(value: object) -> ShadowResolutionEvidence:
    if (
        type(value) is not ShadowResolutionEvidence
        or type(value.selected_wall_ns) is not int
        or value.selected_wall_ns <= 0
        or type(value.provider_match_id) is not str
        or _MATCH_PATTERN.fullmatch(value.provider_match_id) is None
        or type(value.provider_start_wall_ns) is not int
        or value.provider_start_wall_ns <= 0
        or type(value.event_ticker) is not str
        or _TICKER_PATTERN.fullmatch(value.event_ticker) is None
        or not _valid_identity_text(value.home_player_name)
        or not _valid_identity_text(value.away_player_name)
        or value.home_player_name == value.away_player_name
        or type(value.market_tickers) is not tuple
        or len(value.market_tickers) != 2
        or value.market_tickers[0] == value.market_tickers[1]
        or any(
            type(item) is not str
            or _TICKER_PATTERN.fullmatch(item) is None
            for item in value.market_tickers
        )
        or type(value.provider_discovery_raw_path) is not str
        or not os.path.isabs(value.provider_discovery_raw_path)
        or type(value.provider_discovery_raw_sha256) is not str
        or _DIGEST_PATTERN.fullmatch(
            value.provider_discovery_raw_sha256
        )
        is None
        or type(value.kalshi_catalog_sha256) is not str
        or _DIGEST_PATTERN.fullmatch(value.kalshi_catalog_sha256) is None
        or type(value.resolver_snapshot_sha256) is not str
        or _DIGEST_PATTERN.fullmatch(value.resolver_snapshot_sha256) is None
        or value.resolver_rule_version != "strict-name-start-v1"
    ):
        _fail("shadow_evidence_resolution_invalid")
    return value


def _validate_observation(value: object) -> ShadowEvidenceObservation:
    if type(value) is not ShadowEvidenceObservation:
        _fail("shadow_evidence_row_invalid")
    if (
        type(value.home_market) is not ShadowMarketCandidate
        or type(value.away_market) is not ShadowMarketCandidate
    ):
        _fail("shadow_evidence_row_invalid")
    integer_values = (
        value.observed_wall_ns,
        value.observed_monotonic_ns,
        value.clock_uncertainty_ns,
        value.provider_generated_wall_ns,
        value.provider_captured_wall_ns,
        value.provider_request_started_wall_ns,
        value.provider_request_started_monotonic_ns,
        value.provider_request_completed_wall_ns,
        value.provider_request_completed_monotonic_ns,
        value.provider_clock_uncertainty_ns,
        value.sportradar_captures,
        value.kalshi_frames,
    )
    kalshi_reference = (
        value.kalshi_raw_path,
        value.kalshi_raw_sha256,
        value.kalshi_captured_wall_ns,
        value.kalshi_captured_monotonic_ns,
    )
    book_values = (
        value.home_market.yes_bid,
        value.home_market.yes_ask,
        value.home_market.bid_depth,
        value.home_market.ask_depth,
        value.away_market.yes_bid,
        value.away_market.yes_ask,
        value.away_market.bid_depth,
        value.away_market.ask_depth,
    )
    if (
        any(type(item) is not int or item < 0 for item in integer_values)
        or value.observed_wall_ns <= 0
        or value.provider_generated_wall_ns <= 0
        or value.provider_captured_wall_ns <= 0
        or value.provider_request_started_wall_ns <= 0
        or value.provider_request_completed_wall_ns <= 0
        or value.provider_request_completed_monotonic_ns
        < value.provider_request_started_monotonic_ns
        or value.provider_clock_uncertainty_ns
        < (
            value.provider_request_completed_monotonic_ns
            - value.provider_request_started_monotonic_ns
        )
        or type(value.provider_raw_path) is not str
        or not os.path.isabs(value.provider_raw_path)
        or type(value.provider_raw_sha256) is not str
        or _DIGEST_PATTERN.fullmatch(value.provider_raw_sha256) is None
        or type(value.provider_match_id) is not str
        or _MATCH_PATTERN.fullmatch(value.provider_match_id) is None
        or type(value.market_tickers) is not tuple
        or len(value.market_tickers) != 2
        or value.market_tickers[0] == value.market_tickers[1]
        or any(
            type(item) is not str
            or _TICKER_PATTERN.fullmatch(item) is None
            for item in value.market_tickers
        )
        or any(
            type(item) is not str or not item or len(item) > 256
            for item in (value.home_player_name, value.away_player_name)
        )
        or value.match_status is not None
        and (type(value.match_status) is not str or not value.match_status)
        or type(value.sets) is not tuple
        or len(value.sets) != 2
        or type(value.games) is not tuple
        or len(value.games) != 2
        or any(not _positive_or_none(item) for item in (*value.sets, *value.games))
        or type(value.points) is not tuple
        or len(value.points) != 2
        or any(type(item) is not str or not item for item in value.points)
        or value.server not in {None, "home", "away"}
        or not _positive_or_none(value.sportradar_age_ns)
        or value.progression not in _PROGRESSIONS
        or not _positive_or_none(value.last_event_id)
        or value.last_event_type is not None
        and (type(value.last_event_type) is not str or not value.last_event_type)
        or value.last_event_result is not None
        and (type(value.last_event_result) is not str or not value.last_event_result)
        or not _positive_or_none(value.kalshi_captured_wall_ns)
        or not _positive_or_none(value.kalshi_captured_monotonic_ns)
        or not _positive_or_none(value.kalshi_generation)
        or not _positive_or_none(value.kalshi_sequence)
        or not _positive_or_none(value.kalshi_age_ns)
        or value.kalshi_status not in _KALSHI_STATUSES
        or not _valid_market(value.home_market, value.market_tickers[0])
        or not _valid_market(value.away_market, value.market_tickers[1])
        or value.reason not in _OBSERVATION_REASONS
        or value.sportradar_captures < 1
        or (
            not all(item is None for item in kalshi_reference)
            and not all(item is not None for item in kalshi_reference)
        )
        or all(item is None for item in kalshi_reference)
        and value.kalshi_frames != 0
        or all(item is not None for item in kalshi_reference)
        and value.kalshi_frames < 1
        or value.kalshi_captured_wall_ns is not None
        and value.kalshi_captured_wall_ns <= 0
        or value.kalshi_status == "candidate"
        and all(item is None for item in kalshi_reference)
        or value.kalshi_status == "candidate"
        and any(item is None for item in book_values)
        or value.kalshi_status != "candidate"
        and any(item is not None for item in book_values)
    ):
        _fail("shadow_evidence_row_invalid")
    return value


def _valid_price_only_market_tickers(value: object) -> bool:
    return (
        type(value) is tuple
        and len(value) == 2
        and value[0] != value[1]
        and all(
            type(item) is str and _TICKER_PATTERN.fullmatch(item) is not None
            for item in value
        )
    )


def _valid_digest(value: object) -> bool:
    return type(value) is str and _DIGEST_PATTERN.fullmatch(value) is not None


def _empty_market(value: ShadowMarketCandidate) -> bool:
    return (
        value.yes_bid is None
        and value.yes_ask is None
        and value.bid_depth is None
        and value.ask_depth is None
    )


def _two_sided_market(value: ShadowMarketCandidate) -> bool:
    return (
        value.yes_bid is not None
        and value.yes_ask is not None
        and value.bid_depth is not None
        and value.ask_depth is not None
    )


def _valid_price_only_initial_books(value: PriceOnlySessionEvidence) -> bool:
    market_a = value.initial_market_a
    market_b = value.initial_market_b
    if not (
        _valid_market(market_a, value.market_tickers[0])
        and _valid_market(market_b, value.market_tickers[1])
    ):
        return False
    if value.initial_book_state == "empty":
        return _empty_market(market_a) and _empty_market(market_b)
    if value.initial_book_state == "two_sided":
        return _two_sided_market(market_a) and _two_sided_market(market_b)
    return not (_empty_market(market_a) and _empty_market(market_b)) and not (
        _two_sided_market(market_a) and _two_sided_market(market_b)
    )


def _validate_price_only_session(value: object) -> PriceOnlySessionEvidence:
    if type(value) is not PriceOnlySessionEvidence:
        _fail("shadow_evidence_row_invalid")
    raw_reference = (
        value.provider_discovery_raw_path,
        value.provider_discovery_raw_sha256,
    )
    predecessor = (
        value.predecessor_session_id,
        value.predecessor_terminal_row_sha256,
    )
    if (
        type(value.selected_wall_ns) is not int
        or value.selected_wall_ns <= 0
        or type(value.selected_monotonic_ns) is not int
        or value.selected_monotonic_ns < 0
        or type(value.event_ticker) is not str
        or _TICKER_PATTERN.fullmatch(value.event_ticker) is None
        or not _valid_identity_text(value.player_a_name)
        or not _valid_identity_text(value.player_b_name)
        or value.player_a_name == value.player_b_name
        or not _valid_price_only_market_tickers(value.market_tickers)
        or type(value.scheduled_start_wall_ns) is not int
        or value.scheduled_start_wall_ns <= 0
        or any(
            not _valid_identity_text(item)
            for item in (
                value.catalog_sport,
                value.catalog_scope,
                value.catalog_series_ticker,
                value.catalog_milestone_id,
                value.provider_discovery_state,
                value.provider_discovery_reason,
                value.resolver_version,
            )
        )
        or type(value.catalog_queried_competitions) is not tuple
        or not value.catalog_queried_competitions
        or any(
            not _valid_identity_text(item)
            for item in value.catalog_queried_competitions
        )
        or value.catalog_queried_competitions
        != tuple(sorted(set(value.catalog_queried_competitions)))
        or value.catalog_milestone_league is not None
        and not _valid_identity_text(value.catalog_milestone_league)
        or value.initial_book_state not in _INITIAL_BOOK_STATES
        or not _valid_price_only_initial_books(value)
        or (
            not all(item is None for item in raw_reference)
            and not all(item is not None for item in raw_reference)
        )
        or value.provider_discovery_raw_path is not None
        and (
            type(value.provider_discovery_raw_path) is not str
            or not os.path.isabs(value.provider_discovery_raw_path)
        )
        or value.provider_discovery_raw_sha256 is not None
        and not _valid_digest(value.provider_discovery_raw_sha256)
        or any(
            not _valid_digest(item)
            for item in (
                value.kalshi_catalog_sha256,
                value.resolver_snapshot_sha256,
                value.registry_digest,
            )
        )
        or (
            not all(item is None for item in predecessor)
            and not all(item is not None for item in predecessor)
        )
        or value.predecessor_session_id is not None
        and _canonical_session_id(value.predecessor_session_id) is None
        or value.predecessor_terminal_row_sha256 is not None
        and not _valid_digest(value.predecessor_terminal_row_sha256)
    ):
        _fail("shadow_evidence_row_invalid")
    return value


def _validate_price_only_observation(
    value: object,
) -> PriceOnlyEvidenceObservation:
    if type(value) is not PriceOnlyEvidenceObservation:
        _fail("shadow_evidence_row_invalid")
    reference = (
        value.kalshi_raw_path,
        value.kalshi_raw_sha256,
        value.kalshi_captured_wall_ns,
        value.kalshi_captured_monotonic_ns,
        value.kalshi_generation,
        value.kalshi_sequence,
        value.kalshi_age_ns,
    )
    book_values = (
        value.market_a.yes_bid,
        value.market_a.yes_ask,
        value.market_a.bid_depth,
        value.market_a.ask_depth,
        value.market_b.yes_bid,
        value.market_b.yes_ask,
        value.market_b.bid_depth,
        value.market_b.ask_depth,
    ) if (
        type(value.market_a) is ShadowMarketCandidate
        and type(value.market_b) is ShadowMarketCandidate
    ) else (object(),)
    if (
        type(value.observed_wall_ns) is not int
        or value.observed_wall_ns <= 0
        or type(value.observed_monotonic_ns) is not int
        or value.observed_monotonic_ns < 0
        or type(value.clock_uncertainty_ns) is not int
        or value.clock_uncertainty_ns < 0
        or type(value.event_ticker) is not str
        or _TICKER_PATTERN.fullmatch(value.event_ticker) is None
        or not _valid_price_only_market_tickers(value.market_tickers)
        or (
            not all(item is None for item in reference)
            and not all(item is not None for item in reference)
        )
        or value.kalshi_raw_path is not None
        and (
            type(value.kalshi_raw_path) is not str
            or not os.path.isabs(value.kalshi_raw_path)
        )
        or value.kalshi_raw_sha256 is not None
        and not _valid_digest(value.kalshi_raw_sha256)
        or value.kalshi_captured_wall_ns is not None
        and (
            type(value.kalshi_captured_wall_ns) is not int
            or value.kalshi_captured_wall_ns <= 0
        )
        or value.kalshi_captured_monotonic_ns is not None
        and (
            type(value.kalshi_captured_monotonic_ns) is not int
            or value.kalshi_captured_monotonic_ns < 0
        )
        or value.kalshi_generation is not None
        and (
            type(value.kalshi_generation) is not int
            or value.kalshi_generation <= 0
        )
        or value.kalshi_sequence is not None
        and (
            type(value.kalshi_sequence) is not int
            or value.kalshi_sequence < 0
        )
        or value.kalshi_age_ns is not None
        and (type(value.kalshi_age_ns) is not int or value.kalshi_age_ns < 0)
        or value.kalshi_status not in _KALSHI_STATUSES
        or not _valid_market(value.market_a, value.market_tickers[0])
        or not _valid_market(value.market_b, value.market_tickers[1])
        or value.reason not in _PRICE_ONLY_OBSERVATION_REASONS
        or type(value.kalshi_frames) is not int
        or value.kalshi_frames < 0
        or all(item is None for item in reference)
        and value.kalshi_frames != 0
        or all(item is None for item in reference)
        and value.kalshi_status == "candidate"
        or value.kalshi_status == "candidate"
        and any(item is None for item in book_values)
        or value.kalshi_status != "candidate"
        and any(item is not None for item in book_values)
    ):
        _fail("shadow_evidence_row_invalid")
    return value


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("shadow_evidence_row_invalid")


def _row_digest(row_without_digest: dict[str, object]) -> str:
    return sha256(_canonical_json(row_without_digest)).hexdigest()


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("shadow_evidence_prior_corrupt")
        result[key] = value
    return result


def _canonical_session_id(value: object) -> str | None:
    if type(value) is not str:
        return None
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        return None
    return value if value == str(parsed) else None


def _stored_observation(row: dict[str, object]) -> ShadowEvidenceObservation:
    if (
        type(row.get("market_tickers")) is not list
        or type(row.get("sets")) is not list
        or type(row.get("games")) is not list
        or type(row.get("points")) is not list
    ):
        _fail("shadow_evidence_prior_corrupt")
    try:
        record = ShadowEvidenceObservation(
            observed_wall_ns=row["observed_wall_ns"],
            observed_monotonic_ns=row["observed_monotonic_ns"],
            clock_uncertainty_ns=row["clock_uncertainty_ns"],
            provider_match_id=row["provider_match_id"],
            market_tickers=tuple(row["market_tickers"]),
            provider_generated_wall_ns=row["provider_generated_wall_ns"],
            provider_captured_wall_ns=row["provider_captured_wall_ns"],
            provider_request_started_wall_ns=(
                row["provider_request_started_wall_ns"]
            ),
            provider_request_started_monotonic_ns=(
                row["provider_request_started_monotonic_ns"]
            ),
            provider_request_completed_wall_ns=(
                row["provider_request_completed_wall_ns"]
            ),
            provider_request_completed_monotonic_ns=(
                row["provider_request_completed_monotonic_ns"]
            ),
            provider_clock_uncertainty_ns=row["provider_clock_uncertainty_ns"],
            provider_raw_path=row["provider_raw_path"],
            provider_raw_sha256=row["provider_raw_sha256"],
            home_player_name=row["home_player_name"],
            away_player_name=row["away_player_name"],
            match_status=row["match_status"],
            sets=tuple(row["sets"]),
            games=tuple(row["games"]),
            points=tuple(row["points"]),
            server=row["server"],
            sportradar_age_ns=row["sportradar_age_ns"],
            progression=row["progression"],
            last_event_id=row["last_event_id"],
            last_event_type=row["last_event_type"],
            last_event_result=row["last_event_result"],
            kalshi_raw_path=row["kalshi_raw_path"],
            kalshi_raw_sha256=row["kalshi_raw_sha256"],
            kalshi_captured_wall_ns=row["kalshi_captured_wall_ns"],
            kalshi_captured_monotonic_ns=row[
                "kalshi_captured_monotonic_ns"
            ],
            kalshi_generation=row["kalshi_generation"],
            kalshi_sequence=row["kalshi_sequence"],
            kalshi_age_ns=row["kalshi_age_ns"],
            kalshi_status=row["kalshi_status"],
            home_market=ShadowMarketCandidate(
                row["home_ticker"],
                row["home_yes_bid"],
                row["home_yes_ask"],
                row["home_bid_depth"],
                row["home_ask_depth"],
            ),
            away_market=ShadowMarketCandidate(
                row["away_ticker"],
                row["away_yes_bid"],
                row["away_yes_ask"],
                row["away_bid_depth"],
                row["away_ask_depth"],
            ),
            reason=row["reason"],
            sportradar_captures=row["sportradar_captures"],
            kalshi_frames=row["kalshi_frames"],
        )
        return _validate_observation(record)
    except (KeyError, TypeError, ShadowEvidenceError):
        _fail("shadow_evidence_prior_corrupt")


def _stored_price_only_session(
    row: dict[str, object],
) -> PriceOnlySessionEvidence:
    if (
        type(row.get("market_tickers")) is not list
        or type(row.get("catalog_queried_competitions")) is not list
    ):
        _fail("shadow_evidence_prior_corrupt")
    try:
        return _validate_price_only_session(
            PriceOnlySessionEvidence(
                selected_wall_ns=row["selected_wall_ns"],
                selected_monotonic_ns=row["selected_monotonic_ns"],
                event_ticker=row["event_ticker"],
                player_a_name=row["player_a_name"],
                player_b_name=row["player_b_name"],
                market_tickers=tuple(row["market_tickers"]),
                scheduled_start_wall_ns=row["scheduled_start_wall_ns"],
                catalog_sport=row["catalog_sport"],
                catalog_scope=row["catalog_scope"],
                catalog_queried_competitions=tuple(
                    row["catalog_queried_competitions"]
                ),
                catalog_series_ticker=row["catalog_series_ticker"],
                catalog_milestone_id=row["catalog_milestone_id"],
                catalog_milestone_league=row["catalog_milestone_league"],
                initial_book_state=row["initial_book_state"],
                initial_market_a=ShadowMarketCandidate(
                    row["initial_market_a_ticker"],
                    row["initial_market_a_yes_bid"],
                    row["initial_market_a_yes_ask"],
                    row["initial_market_a_bid_depth"],
                    row["initial_market_a_ask_depth"],
                ),
                initial_market_b=ShadowMarketCandidate(
                    row["initial_market_b_ticker"],
                    row["initial_market_b_yes_bid"],
                    row["initial_market_b_yes_ask"],
                    row["initial_market_b_bid_depth"],
                    row["initial_market_b_ask_depth"],
                ),
                provider_discovery_state=row["provider_discovery_state"],
                provider_discovery_reason=row["provider_discovery_reason"],
                provider_discovery_raw_path=row[
                    "provider_discovery_raw_path"
                ],
                provider_discovery_raw_sha256=row[
                    "provider_discovery_raw_sha256"
                ],
                kalshi_catalog_sha256=row["kalshi_catalog_sha256"],
                resolver_snapshot_sha256=row["resolver_snapshot_sha256"],
                resolver_version=row["resolver_version"],
                registry_digest=row["registry_digest"],
                predecessor_session_id=row["predecessor_session_id"],
                predecessor_terminal_row_sha256=row[
                    "predecessor_terminal_row_sha256"
                ],
            )
        )
    except (KeyError, TypeError, ShadowEvidenceError):
        _fail("shadow_evidence_prior_corrupt")


def _stored_price_only_observation(
    row: dict[str, object],
) -> PriceOnlyEvidenceObservation:
    if type(row.get("market_tickers")) is not list:
        _fail("shadow_evidence_prior_corrupt")
    try:
        return _validate_price_only_observation(
            PriceOnlyEvidenceObservation(
                observed_wall_ns=row["observed_wall_ns"],
                observed_monotonic_ns=row["observed_monotonic_ns"],
                clock_uncertainty_ns=row["clock_uncertainty_ns"],
                event_ticker=row["event_ticker"],
                market_tickers=tuple(row["market_tickers"]),
                kalshi_raw_path=row["kalshi_raw_path"],
                kalshi_raw_sha256=row["kalshi_raw_sha256"],
                kalshi_captured_wall_ns=row["kalshi_captured_wall_ns"],
                kalshi_captured_monotonic_ns=row[
                    "kalshi_captured_monotonic_ns"
                ],
                kalshi_generation=row["kalshi_generation"],
                kalshi_sequence=row["kalshi_sequence"],
                kalshi_age_ns=row["kalshi_age_ns"],
                kalshi_status=row["kalshi_status"],
                market_a=ShadowMarketCandidate(
                    row["market_a_ticker"],
                    row["market_a_yes_bid"],
                    row["market_a_yes_ask"],
                    row["market_a_bid_depth"],
                    row["market_a_ask_depth"],
                ),
                market_b=ShadowMarketCandidate(
                    row["market_b_ticker"],
                    row["market_b_yes_bid"],
                    row["market_b_yes_ask"],
                    row["market_b_bid_depth"],
                    row["market_b_ask_depth"],
                ),
                reason=row["reason"],
                kalshi_frames=row["kalshi_frames"],
            )
        )
    except (KeyError, TypeError, ShadowEvidenceError):
        _fail("shadow_evidence_prior_corrupt")


def _stored_resolution(row: dict[str, object]) -> ShadowResolutionEvidence:
    if type(row.get("market_tickers")) is not list:
        _fail("shadow_evidence_prior_corrupt")
    try:
        return _validate_resolution(
            ShadowResolutionEvidence(
                selected_wall_ns=row["selected_wall_ns"],
                provider_match_id=row["provider_match_id"],
                provider_start_wall_ns=row["provider_start_wall_ns"],
                event_ticker=row["event_ticker"],
                home_player_name=row["home_player_name"],
                away_player_name=row["away_player_name"],
                market_tickers=tuple(row["market_tickers"]),
                provider_discovery_raw_path=row[
                    "provider_discovery_raw_path"
                ],
                provider_discovery_raw_sha256=row[
                    "provider_discovery_raw_sha256"
                ],
                kalshi_catalog_sha256=row["kalshi_catalog_sha256"],
                resolver_snapshot_sha256=row["resolver_snapshot_sha256"],
                resolver_rule_version=row["resolver_rule_version"],
            )
        )
    except (KeyError, TypeError, ShadowEvidenceError):
        _fail("shadow_evidence_prior_corrupt")


def _resolution_identity(
    value: ShadowResolutionEvidence,
) -> tuple[str, tuple[str, str], str, str]:
    return (
        value.provider_match_id,
        value.market_tickers,
        value.home_player_name,
        value.away_player_name,
    )


def _valid_terminal_row(row: dict[str, object]) -> bool:
    reason = row.get("reason")
    code = row.get("code")
    tickers = row.get("market_tickers")
    return (
        reason in _TERMINAL_REASONS
        and (reason == "halted")
        == (
            type(code) is str
            and _SAFE_CODE_PATTERN.fullmatch(code) is not None
        )
        and type(row.get("ended_wall_ns")) is int
        and row["ended_wall_ns"] > 0
        and type(row.get("ended_monotonic_ns")) is int
        and row["ended_monotonic_ns"] >= 0
        and type(row.get("provider_match_id")) is str
        and _MATCH_PATTERN.fullmatch(row["provider_match_id"]) is not None
        and type(tickers) is list
        and len(tickers) == 2
        and tickers[0] != tickers[1]
        and all(
            type(item) is str and _TICKER_PATTERN.fullmatch(item) is not None
            for item in tickers
        )
        and type(row.get("sportradar_captures")) is int
        and row["sportradar_captures"] >= 0
        and type(row.get("kalshi_frames")) is int
        and row["kalshi_frames"] >= 0
    )


def _valid_price_only_terminal_row(row: dict[str, object]) -> bool:
    tickers = row.get("market_tickers")
    reason = row.get("reason")
    code = row.get("code")
    return (
        reason in _PRICE_ONLY_TERMINAL_REASONS
        and (
            (
                reason == "halted"
                and type(code) is str
                and _SAFE_CODE_PATTERN.fullmatch(code) is not None
            )
            or (reason != "halted" and code is None)
        )
        and type(row.get("ended_wall_ns")) is int
        and row["ended_wall_ns"] > 0
        and type(row.get("ended_monotonic_ns")) is int
        and row["ended_monotonic_ns"] >= 0
        and type(row.get("event_ticker")) is str
        and _TICKER_PATTERN.fullmatch(row["event_ticker"]) is not None
        and type(tickers) is list
        and len(tickers) == 2
        and tickers[0] != tickers[1]
        and all(
            type(item) is str and _TICKER_PATTERN.fullmatch(item) is not None
            for item in tickers
        )
        and type(row.get("kalshi_frames")) is int
        and row["kalshi_frames"] >= 0
        and row.get("mapping_mode") == "price_only"
        and _valid_digest(row.get("session_row_sha256"))
    )


class ShadowEvidenceStore:
    """One-owner append-only ledger with immutable raw Kalshi captures.

    A local chain cannot detect rollback or deletion of the entire state root;
    promotion therefore requires immutable external archival of these files.
    """

    def __init__(self, state_root: Path | None = None) -> None:
        root = default_shadow_state_root() if state_root is None else state_root
        if not isinstance(root, Path) or not root.is_absolute():
            _fail("shadow_evidence_state_invalid")
        _private_directory(root)
        raw_root = root / "raw"
        _private_directory(raw_root)
        self.state_root = root
        self.raw_root = raw_root
        self.session_id = str(uuid.uuid4())
        self.ledger_path = root / f"session-{self.session_id}.jsonl"
        self._closed = False
        self._row_number = 0
        self._raw_number = 0
        self._previous_row_sha256 = _ZERO_DIGEST
        self._kalshi_receipts: dict[str, str] = {}
        self._eligible_predecessor_terminals: dict[str, tuple[str, int]] = {}
        self._audited_price_predecessors: list[tuple[str, str, int]] = []
        self._audited_settlement_sources: dict[
            str, AuditedShadowSettlementSource
        ] = {}
        self._mode: str | None = None
        self._resolution_identity: (
            tuple[str, tuple[str, str], str, str] | None
        ) = None
        self._resolution_row_sha256: str | None = None
        self._price_only_session: PriceOnlySessionEvidence | None = None
        self._price_only_session_row_sha256: str | None = None
        self._terminal_recorded = False
        self._terminal_row_sha256: str | None = None
        self._poisoned = False
        self._protocol_epoch_persisted = False
        self._lock_fd = _open_private_file(root / "shadow.lock", create=True)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(self._lock_fd)
            self._closed = True
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                _fail("shadow_evidence_locked")
            _fail("shadow_evidence_state_unavailable")
        try:
            self._audit_prior_sessions()
            self._ledger_fd = _open_private_file(
                self.ledger_path,
                create=True,
                append=True,
                exclusive=True,
            )
            _fsync_directory(root)
        except BaseException:
            self.close()
            raise

    def __repr__(self) -> str:
        return f"ShadowEvidenceStore(state_root={self.state_root!r})"

    @property
    def terminal_row_sha256(self) -> str | None:
        """Expose only a successfully fsynced terminal row digest."""

        return self._terminal_row_sha256

    def _pending_marker_path(self, session_id: str | None = None) -> Path:
        value = self.session_id if session_id is None else session_id
        return self.state_root / f"session-{value}.pending"

    def _commit_marker_path(self, session_id: str | None = None) -> Path:
        value = self.session_id if session_id is None else session_id
        return self.state_root / f"session-{value}.commit"

    def _protocol_epoch_path(self, session_id: str | None = None) -> Path:
        value = self.session_id if session_id is None else session_id
        return self.state_root / f"session-{value}.epoch"

    def _watermark_payload(self, row_number: int, row_sha256: str) -> bytes:
        return _canonical_json(
            {
                "row_number": row_number,
                "row_sha256": row_sha256,
                "schema": _COMMIT_WATERMARK_SCHEMA,
                "session_id": self.session_id,
            }
        ) + b"\n"

    def _protocol_epoch_payload(self) -> bytes:
        return _canonical_json(
            {
                "commit_watermark_schema": _COMMIT_WATERMARK_SCHEMA,
                "schema": _PROTOCOL_EPOCH_SCHEMA,
                "session_id": self.session_id,
            }
        ) + b"\n"

    def _persist_new_marker(self, path: Path, payload: bytes) -> None:
        descriptor: int | None = None
        try:
            descriptor = _open_private_file(path, create=True, exclusive=True)
            try:
                _write_all(
                    descriptor, payload, "shadow_evidence_write_failed"
                )
            except ShadowEvidenceError:
                self._poisoned = True
                try:
                    os.close(descriptor)
                except OSError:
                    self._poisoned = True
                descriptor = None
                raise
            try:
                os.close(descriptor)
            except OSError:
                descriptor = None
                self._poisoned = True
                _fail("shadow_evidence_write_failed")
            descriptor = None
            _fsync_directory(self.state_root)
        except ShadowEvidenceError:
            self._poisoned = True
            raise
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    self._poisoned = True

    def _create_pending_marker(
        self, row_number: int, row_sha256: str
    ) -> tuple[Path, bytes]:
        path = self._pending_marker_path()
        payload = self._watermark_payload(row_number, row_sha256)
        self._persist_new_marker(path, payload)
        return path, payload

    def _ensure_protocol_epoch(self) -> None:
        if self._protocol_epoch_persisted:
            return
        self._persist_new_marker(
            self._protocol_epoch_path(), self._protocol_epoch_payload()
        )
        self._protocol_epoch_persisted = True

    def _commit_pending_marker(self, path: Path) -> None:
        try:
            os.replace(path, self._commit_marker_path())
        except OSError:
            self._poisoned = True
            _fail("shadow_evidence_write_failed")
        try:
            _fsync_directory(self.state_root)
        except ShadowEvidenceError:
            self._poisoned = True
            raise

    def _audit_watermark(
        self, path: Path, session_id: str
    ) -> tuple[int, str]:
        try:
            info = path.lstat()
        except OSError:
            _fail("shadow_evidence_prior_corrupt")
        if stat.S_ISLNK(info.st_mode):
            _fail("shadow_evidence_prior_corrupt")
        payload = _validate_existing_regular_file(
            path,
            expected_mode=0o600,
            code="shadow_evidence_prior_corrupt",
            read_payload=True,
            maximum_bytes=512,
        )
        if payload is None or not payload.endswith(b"\n"):
            _fail("shadow_evidence_prior_corrupt")
        try:
            marker = json.loads(
                payload,
                object_pairs_hook=_strict_json_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            _fail("shadow_evidence_prior_corrupt")
        if (
            type(marker) is not dict
            or frozenset(marker)
            != {
                "row_number",
                "row_sha256",
                "schema",
                "session_id",
            }
            or marker.get("schema") != _COMMIT_WATERMARK_SCHEMA
            or marker.get("session_id") != session_id
            or type(marker.get("row_number")) is not int
            or marker["row_number"] < 1
            or not _valid_digest(marker.get("row_sha256"))
            or _canonical_json(marker) + b"\n" != payload
        ):
            _fail("shadow_evidence_prior_corrupt")
        return marker["row_number"], marker["row_sha256"]

    def _audit_protocol_epoch(self, path: Path, session_id: str) -> None:
        try:
            info = path.lstat()
        except OSError:
            _fail("shadow_evidence_prior_corrupt")
        if stat.S_ISLNK(info.st_mode):
            _fail("shadow_evidence_prior_corrupt")
        payload = _validate_existing_regular_file(
            path,
            expected_mode=0o600,
            code="shadow_evidence_prior_corrupt",
            read_payload=True,
            maximum_bytes=512,
        )
        if payload is None or not payload.endswith(b"\n"):
            _fail("shadow_evidence_prior_corrupt")
        try:
            marker = json.loads(
                payload,
                object_pairs_hook=_strict_json_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            _fail("shadow_evidence_prior_corrupt")
        if (
            type(marker) is not dict
            or frozenset(marker)
            != {
                "commit_watermark_schema",
                "schema",
                "session_id",
            }
            or marker.get("commit_watermark_schema")
            != _COMMIT_WATERMARK_SCHEMA
            or marker.get("schema") != _PROTOCOL_EPOCH_SCHEMA
            or marker.get("session_id") != session_id
            or _canonical_json(marker) + b"\n" != payload
        ):
            _fail("shadow_evidence_prior_corrupt")

    def _audit_marker_inventory(
        self,
    ) -> tuple[dict[str, tuple[int, str]], set[str]]:
        commits: dict[str, tuple[int, str]] = {}
        protocol_epochs: set[str] = set()
        try:
            entries = sorted(self.state_root.iterdir(), key=lambda path: path.name)
        except OSError:
            _fail("shadow_evidence_state_unavailable")
        for path in entries:
            name = path.name
            if name in {"raw", "shadow.lock"}:
                continue
            if name.startswith("session-") and name.endswith(".jsonl"):
                continue
            if name.startswith("session-") and name.endswith(".pending"):
                session_id = name[len("session-") : -len(".pending")]
                if (
                    name != f"session-{session_id}.pending"
                    or _canonical_session_id(session_id) is None
                ):
                    _fail("shadow_evidence_prior_corrupt")
                self._audit_watermark(path, session_id)
                _fail("shadow_evidence_unclean_session")
            if name.startswith("session-") and name.endswith(".commit"):
                session_id = name[len("session-") : -len(".commit")]
                if (
                    name != f"session-{session_id}.commit"
                    or _canonical_session_id(session_id) is None
                    or session_id in commits
                ):
                    _fail("shadow_evidence_prior_corrupt")
                commits[session_id] = self._audit_watermark(path, session_id)
                continue
            if name.startswith("session-") and name.endswith(".epoch"):
                session_id = name[len("session-") : -len(".epoch")]
                if (
                    name != f"session-{session_id}.epoch"
                    or _canonical_session_id(session_id) is None
                    or session_id in protocol_epochs
                ):
                    _fail("shadow_evidence_prior_corrupt")
                self._audit_protocol_epoch(path, session_id)
                protocol_epochs.add(session_id)
                continue
            if (
                name.endswith(".pending")
                or name.endswith(".commit")
                or name.endswith(".epoch")
                or name.endswith(".poisoned")
            ):
                _fail("shadow_evidence_prior_corrupt")
            _fail("shadow_evidence_prior_corrupt")
        return commits, protocol_epochs

    def _audit_prior_sessions(self) -> None:
        receipts: dict[str, str] = {}
        commits, protocol_epochs = self._audit_marker_inventory()
        audited_tails: dict[str, tuple[int, str]] = {}
        price_only_sessions: set[str] = set()
        try:
            ledgers = sorted(self.state_root.glob("session-*.jsonl"))
        except OSError:
            _fail("shadow_evidence_state_unavailable")
        for path in ledgers:
            name = path.name
            session_id = name[len("session-") : -len(".jsonl")]
            if (
                name != f"session-{session_id}.jsonl"
                or _canonical_session_id(session_id) is None
            ):
                _fail("shadow_evidence_prior_corrupt")
            payload = _validate_existing_regular_file(
                path,
                expected_mode=0o600,
                code="shadow_evidence_prior_corrupt",
                read_payload=True,
                maximum_bytes=67_108_864,
            )
            if payload is None or not payload or not payload.endswith(b"\n"):
                _fail("shadow_evidence_unclean_session")
            rows: list[dict[str, Any]] = []
            try:
                for line in payload.splitlines():
                    row = json.loads(
                        line,
                        object_pairs_hook=_strict_json_object,
                    )
                    if type(row) is not dict:
                        _fail("shadow_evidence_prior_corrupt")
                    rows.append(row)
            except ShadowEvidenceError:
                raise
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                _fail("shadow_evidence_prior_corrupt")
            if rows and rows[0].get("kind") == "price_only_session":
                price_only_sessions.add(session_id)
                audited_tails[session_id] = (
                    len(rows),
                    self._audit_price_only_rows(
                        rows,
                        session_id,
                        receipts,
                        path=path,
                        ledger_sha256=sha256(payload).hexdigest(),
                    ),
                )
                continue
            terminal_indexes = [
                index
                for index, row in enumerate(rows)
                if row.get("kind") in {"terminal", "auto_terminal"}
            ]
            if terminal_indexes != [len(rows) - 1]:
                _fail("shadow_evidence_unclean_session")
            previous_digest = _ZERO_DIGEST
            session_receipts: dict[str, str] = {}
            capture_number = 0
            prior_sportradar_captures = 0
            resolution: ShadowResolutionEvidence | None = None
            resolution_row_sha256: str | None = None
            for row_number, row in enumerate(rows, start=1):
                kind = row.get("kind")
                expected_fields = _FIELDS_BY_KIND.get(kind)
                claimed_digest = row.get("row_sha256")
                if (
                    expected_fields is None
                    or frozenset(row) != expected_fields
                    or row.get("schema") != _SCHEMA_BY_KIND.get(kind)
                    or row.get("trust") != _TRUST_BY_KIND.get(kind)
                    or type(row.get("reason")) is not str
                    or not row["reason"]
                    or row.get("session_id") != session_id
                    or row.get("row_number") != row_number
                    or row.get("previous_row_sha256") != previous_digest
                    or type(claimed_digest) is not str
                    or _DIGEST_PATTERN.fullmatch(claimed_digest) is None
                ):
                    _fail("shadow_evidence_prior_corrupt")
                unhashed = dict(row)
                unhashed.pop("row_sha256")
                if _row_digest(unhashed) != claimed_digest:
                    _fail("shadow_evidence_prior_corrupt")
                previous_digest = claimed_digest
                if kind == "resolution":
                    if row_number != 1 or resolution is not None:
                        _fail("shadow_evidence_prior_corrupt")
                    resolution = self._audit_resolution_row(row)
                    resolution_row_sha256 = claimed_digest
                elif kind == "kalshi_capture":
                    capture_number += 1
                    self._audit_capture_row(
                        row,
                        session_id=session_id,
                        capture_number=capture_number,
                        session_receipts=session_receipts,
                        all_receipts=receipts,
                    )
                elif kind == "observation":
                    self._audit_observation_row(row, session_receipts)
                    if resolution is not None and (
                        row["provider_match_id"]
                        != resolution.provider_match_id
                        or tuple(row["market_tickers"])
                        != resolution.market_tickers
                        or row["home_player_name"]
                        != resolution.home_player_name
                        or row["away_player_name"]
                        != resolution.away_player_name
                    ):
                        _fail("shadow_evidence_prior_corrupt")
                    if (
                        row["kalshi_frames"] != capture_number
                        or row["sportradar_captures"]
                        < prior_sportradar_captures
                    ):
                        _fail("shadow_evidence_prior_corrupt")
                    prior_sportradar_captures = row[
                        "sportradar_captures"
                    ]
                else:
                    if (
                        not _valid_terminal_row(row)
                        or row["kalshi_frames"] != capture_number
                        or row["sportradar_captures"]
                        < prior_sportradar_captures
                        or resolution is not None
                        and (
                            row["provider_match_id"]
                            != resolution.provider_match_id
                            or tuple(row["market_tickers"])
                            != resolution.market_tickers
                        )
                        or kind == "auto_terminal"
                        and (
                            resolution is None
                            or row.get("mapping_mode") != "auto_matched"
                            or row.get("resolution_row_sha256")
                            != resolution_row_sha256
                        )
                        or kind == "terminal" and resolution is not None
                    ):
                        _fail("shadow_evidence_prior_corrupt")
            if rows[-1].get("kind") == "auto_terminal":
                self._eligible_predecessor_terminals[session_id] = (
                    previous_digest,
                    rows[-1]["ended_wall_ns"],
                )
                if resolution is None or resolution_row_sha256 is None:
                    _fail("shadow_evidence_prior_corrupt")
                self._audited_settlement_sources[session_id] = (
                    AuditedShadowSettlementSource(
                        session_path=path,
                        ledger_sha256=sha256(payload).hexdigest(),
                        session_id=session_id,
                        mode="VERIFIED",
                        event_ticker=resolution.event_ticker,
                        market_tickers=resolution.market_tickers,
                        player_names=(
                            resolution.home_player_name,
                            resolution.away_player_name,
                        ),
                        first_row_sha256=resolution_row_sha256,
                        terminal_row_sha256=previous_digest,
                    )
                )
            audited_tails[session_id] = (len(rows), previous_digest)
        current_protocol_sessions = (
            price_only_sessions | set(commits) | protocol_epochs
        )
        if not current_protocol_sessions.issubset(audited_tails):
            _fail("shadow_evidence_prior_corrupt")
        for session_id in current_protocol_sessions:
            if (
                session_id not in protocol_epochs
                or commits.get(session_id) != audited_tails[session_id]
            ):
                _fail("shadow_evidence_prior_corrupt")
        for (
            predecessor_session_id,
            predecessor_terminal_digest,
            selected_wall_ns,
        ) in (
            self._audited_price_predecessors
        ):
            predecessor = self._eligible_predecessor_terminals.get(
                predecessor_session_id
            )
            if predecessor is None or (
                predecessor[0] != predecessor_terminal_digest
                or predecessor[1] >= selected_wall_ns
            ):
                _fail("shadow_evidence_prior_corrupt")
        try:
            raw_paths = sorted(self.raw_root.iterdir())
        except OSError:
            _fail("shadow_evidence_state_unavailable")
        if {str(path) for path in raw_paths} != set(receipts):
            _fail("shadow_evidence_unclean_session")
        for raw_path, digest in receipts.items():
            payload = _validate_existing_regular_file(
                Path(raw_path),
                expected_mode=0o600,
                code="shadow_evidence_prior_corrupt",
                read_payload=True,
                maximum_bytes=_MAXIMUM_KALSHI_FRAME_BYTES,
            )
            if (
                payload is None
                or len(payload) > _MAXIMUM_KALSHI_FRAME_BYTES
                or sha256(payload).hexdigest() != digest
            ):
                _fail("shadow_evidence_prior_corrupt")

    def _audit_price_only_rows(
        self,
        rows: list[dict[str, Any]],
        session_id: str,
        all_receipts: dict[str, str],
        *,
        path: Path,
        ledger_sha256: str,
    ) -> str:
        terminal_indexes = [
            index
            for index, row in enumerate(rows)
            if row.get("kind") == "price_only_terminal"
        ]
        if terminal_indexes != [len(rows) - 1]:
            _fail("shadow_evidence_unclean_session")
        previous_digest = _ZERO_DIGEST
        session_receipts: dict[str, str] = {}
        capture_number = 0
        session: PriceOnlySessionEvidence | None = None
        session_row_sha256: str | None = None
        for row_number, row in enumerate(rows, start=1):
            kind = row.get("kind")
            expected_fields = _FIELDS_BY_KIND.get(kind)
            claimed_digest = row.get("row_sha256")
            if (
                expected_fields is None
                or frozenset(row) != expected_fields
                or row.get("schema") != _SCHEMA_BY_KIND.get(kind)
                or row.get("trust") != _TRUST_BY_KIND.get(kind)
                or type(row.get("reason")) is not str
                or not row["reason"]
                or row.get("session_id") != session_id
                or row.get("row_number") != row_number
                or row.get("previous_row_sha256") != previous_digest
                or not _valid_digest(claimed_digest)
            ):
                _fail("shadow_evidence_prior_corrupt")
            unhashed = dict(row)
            unhashed.pop("row_sha256")
            if _row_digest(unhashed) != claimed_digest:
                _fail("shadow_evidence_prior_corrupt")
            previous_digest = claimed_digest
            if kind == "price_only_session":
                if (
                    row_number != 1
                    or row.get("reason") != "price_only_selected"
                    or row.get("authority_scope") != "observation_only"
                    or row.get("execution_authorized") is not False
                    or row.get("score_feed") != "none"
                ):
                    _fail("shadow_evidence_prior_corrupt")
                session = _stored_price_only_session(row)
                session_row_sha256 = claimed_digest
                if session.provider_discovery_raw_path is not None:
                    self._audit_raw_reference(
                        session.provider_discovery_raw_path,
                        session.provider_discovery_raw_sha256,
                        kalshi=False,
                    )
                if session.predecessor_session_id is not None:
                    self._audited_price_predecessors.append(
                        (
                            session.predecessor_session_id,
                            session.predecessor_terminal_row_sha256,
                            session.selected_wall_ns,
                        )
                    )
            elif kind == "price_only_kalshi_capture":
                if session is None:
                    _fail("shadow_evidence_prior_corrupt")
                capture_number += 1
                self._audit_price_only_capture_row(
                    row,
                    session_id=session_id,
                    capture_number=capture_number,
                    session_receipts=session_receipts,
                    all_receipts=all_receipts,
                )
            elif kind == "price_only_observation":
                if session is None:
                    _fail("shadow_evidence_prior_corrupt")
                record = _stored_price_only_observation(row)
                if (
                    record.event_ticker != session.event_ticker
                    or record.market_tickers != session.market_tickers
                    or record.kalshi_frames != capture_number
                ):
                    _fail("shadow_evidence_prior_corrupt")
                if record.kalshi_raw_path is not None:
                    if (
                        session_receipts.get(record.kalshi_raw_path)
                        != record.kalshi_raw_sha256
                    ):
                        _fail("shadow_evidence_prior_corrupt")
                    self._audit_raw_reference(
                        record.kalshi_raw_path,
                        record.kalshi_raw_sha256,
                        kalshi=True,
                    )
            elif kind == "price_only_terminal":
                if (
                    session is None
                    or not _valid_price_only_terminal_row(row)
                    or row["event_ticker"] != session.event_ticker
                    or tuple(row["market_tickers"]) != session.market_tickers
                    or row["kalshi_frames"] != capture_number
                    or row["session_row_sha256"] != session_row_sha256
                ):
                    _fail("shadow_evidence_prior_corrupt")
                if session_row_sha256 is None:
                    _fail("shadow_evidence_prior_corrupt")
                self._audited_settlement_sources[session_id] = (
                    AuditedShadowSettlementSource(
                        session_path=path,
                        ledger_sha256=ledger_sha256,
                        session_id=session_id,
                        mode="PRICE_ONLY",
                        event_ticker=session.event_ticker,
                        market_tickers=session.market_tickers,
                        player_names=(
                            session.player_a_name,
                            session.player_b_name,
                        ),
                        first_row_sha256=session_row_sha256,
                        terminal_row_sha256=claimed_digest,
                    )
                )
            else:
                _fail("shadow_evidence_prior_corrupt")
        return previous_digest

    def _audit_price_only_capture_row(
        self,
        row: dict[str, object],
        *,
        session_id: str,
        capture_number: int,
        session_receipts: dict[str, str],
        all_receipts: dict[str, str],
    ) -> None:
        raw_path = row.get("raw_path")
        digest = row.get("raw_sha256")
        if (
            row.get("schema")
            != "inci-tennis-price-only-kalshi-capture-v1"
            or row.get("reason") != "kalshi_raw_capture_persisted"
            or type(raw_path) is not str
            or raw_path
            != str(
                self.raw_root
                / f"{session_id}-{capture_number:08d}-kalshi.bin"
            )
            or not _valid_digest(digest)
            or raw_path in session_receipts
            or raw_path in all_receipts
            or type(row.get("captured_wall_ns")) is not int
            or row["captured_wall_ns"] <= 0
            or type(row.get("captured_monotonic_ns")) is not int
            or row["captured_monotonic_ns"] < 0
            or type(row.get("clock_uncertainty_ns")) is not int
            or row["clock_uncertainty_ns"] < 0
            or type(row.get("physical_connection_generation")) is not int
            or row["physical_connection_generation"] <= 0
        ):
            _fail("shadow_evidence_prior_corrupt")
        self._audit_raw_reference(raw_path, digest, kalshi=True)
        session_receipts[raw_path] = digest
        all_receipts[raw_path] = digest

    def _audit_raw_reference(
        self,
        raw_path: object,
        raw_digest: object,
        *,
        kalshi: bool,
    ) -> None:
        if (
            type(raw_path) is not str
            or not os.path.isabs(raw_path)
            or type(raw_digest) is not str
            or _DIGEST_PATTERN.fullmatch(raw_digest) is None
        ):
            _fail("shadow_evidence_prior_corrupt")
        path = Path(raw_path)
        if kalshi and path.parent != self.raw_root:
            _fail("shadow_evidence_prior_corrupt")
        maximum = (
            _MAXIMUM_KALSHI_FRAME_BYTES
            if kalshi
            else _MAXIMUM_SPORTRADAR_CAPTURE_BYTES
        )
        payload = _validate_existing_regular_file(
            path,
            expected_mode=0o600,
            code="shadow_evidence_prior_corrupt",
            read_payload=True,
            maximum_bytes=maximum,
        )
        if (
            payload is None
            or not payload
            or len(payload) > maximum
            or sha256(payload).hexdigest() != raw_digest
        ):
            _fail("shadow_evidence_prior_corrupt")

    def _audit_resolution_row(
        self, row: dict[str, object]
    ) -> ShadowResolutionEvidence:
        if (
            row.get("schema")
            != "inci-tennis-unqualified-shadow-resolution-v1"
            or row.get("reason") != "strict_name_start_selected"
        ):
            _fail("shadow_evidence_prior_corrupt")
        value = _stored_resolution(row)
        self._audit_raw_reference(
            value.provider_discovery_raw_path,
            value.provider_discovery_raw_sha256,
            kalshi=False,
        )
        return value

    def _audit_capture_row(
        self,
        row: dict[str, object],
        *,
        session_id: str,
        capture_number: int,
        session_receipts: dict[str, str],
        all_receipts: dict[str, str],
    ) -> None:
        raw_path = row.get("raw_path")
        digest = row.get("raw_sha256")
        if (
            row.get("schema")
            != "inci-tennis-unqualified-shadow-kalshi-capture-v1"
            or row.get("reason") != "kalshi_raw_capture_persisted"
            or type(raw_path) is not str
            or raw_path
            != str(
                self.raw_root
                / f"{session_id}-{capture_number:08d}-kalshi.bin"
            )
            or type(digest) is not str
            or _DIGEST_PATTERN.fullmatch(digest) is None
            or raw_path in session_receipts
            or raw_path in all_receipts
            or type(row.get("captured_wall_ns")) is not int
            or row["captured_wall_ns"] <= 0
            or type(row.get("captured_monotonic_ns")) is not int
            or row["captured_monotonic_ns"] < 0
            or type(row.get("clock_uncertainty_ns")) is not int
            or row["clock_uncertainty_ns"] < 0
            or type(row.get("physical_connection_generation")) is not int
            or row["physical_connection_generation"] <= 0
        ):
            _fail("shadow_evidence_prior_corrupt")
        self._audit_raw_reference(raw_path, digest, kalshi=True)
        session_receipts[raw_path] = digest
        all_receipts[raw_path] = digest

    def _audit_observation_row(
        self,
        row: dict[str, object],
        session_receipts: dict[str, str],
    ) -> None:
        if (
            row.get("schema")
            != "inci-tennis-unqualified-shadow-observation-v1"
        ):
            _fail("shadow_evidence_prior_corrupt")
        record = _stored_observation(row)
        self._audit_raw_reference(
            record.provider_raw_path,
            record.provider_raw_sha256,
            kalshi=False,
        )
        if record.kalshi_raw_path is None:
            if record.kalshi_raw_sha256 is not None:
                _fail("shadow_evidence_prior_corrupt")
            return
        if (
            session_receipts.get(record.kalshi_raw_path)
            != record.kalshi_raw_sha256
        ):
            _fail("shadow_evidence_prior_corrupt")
        self._audit_raw_reference(
            record.kalshi_raw_path,
            record.kalshi_raw_sha256,
            kalshi=True,
        )

    def persist_kalshi_frame(self, frame: object) -> PersistedKalshiFrame:
        if self._closed or self._poisoned:
            _fail("shadow_evidence_closed")
        try:
            payload = frame.payload
            captured_wall_ns = frame.captured_wall_ns
            captured_monotonic_ns = frame.captured_monotonic_ns
            clock_uncertainty_ns = frame.clock_uncertainty_ns
            generation = frame.physical_connection_generation
            claimed_digest = frame.raw_sha256
        except AttributeError:
            _fail("shadow_evidence_frame_invalid")
        digest = sha256(payload).hexdigest() if type(payload) is bytes else None
        if (
            type(payload) is not bytes
            or not payload
            or len(payload) > _MAXIMUM_KALSHI_FRAME_BYTES
            or type(captured_wall_ns) is not int
            or captured_wall_ns <= 0
            or type(captured_monotonic_ns) is not int
            or captured_monotonic_ns < 0
            or type(clock_uncertainty_ns) is not int
            or clock_uncertainty_ns < 0
            or type(generation) is not int
            or generation <= 0
            or claimed_digest != digest
        ):
            _fail("shadow_evidence_frame_invalid")
        raw_number = self._raw_number + 1
        path = self.raw_root / (
            f"{self.session_id}-{raw_number:08d}-kalshi.bin"
        )
        try:
            descriptor = _open_private_file(path, create=True, exclusive=True)
        except ShadowEvidenceError:
            self._poisoned = True
            raise
        try:
            _write_all(descriptor, payload, "shadow_evidence_raw_write_failed")
        except ShadowEvidenceError:
            self._poisoned = True
            try:
                os.close(descriptor)
            except OSError:
                self._poisoned = True
            raise
        try:
            os.close(descriptor)
        except OSError:
            self._poisoned = True
            _fail("shadow_evidence_raw_write_failed")
        try:
            _fsync_directory(self.raw_root)
        except ShadowEvidenceError:
            self._poisoned = True
            raise
        reference = PersistedKalshiFrame(
            raw_path=str(path),
            raw_sha256=digest,
            captured_wall_ns=captured_wall_ns,
            captured_monotonic_ns=captured_monotonic_ns,
            clock_uncertainty_ns=clock_uncertainty_ns,
            physical_connection_generation=generation,
        )
        try:
            self._append_record(
                {
                    "schema": (
                        "inci-tennis-price-only-kalshi-capture-v1"
                        if self._mode == "price_only"
                        else "inci-tennis-unqualified-shadow-kalshi-capture-v1"
                    ),
                    "kind": (
                        "price_only_kalshi_capture"
                        if self._mode == "price_only"
                        else "kalshi_capture"
                    ),
                    "trust": (
                        "PRICE_ONLY"
                        if self._mode == "price_only"
                        else "unqualified_shadow"
                    ),
                    "reason": "kalshi_raw_capture_persisted",
                    "raw_path": reference.raw_path,
                    "raw_sha256": reference.raw_sha256,
                    "captured_wall_ns": reference.captured_wall_ns,
                    "captured_monotonic_ns": reference.captured_monotonic_ns,
                    "clock_uncertainty_ns": reference.clock_uncertainty_ns,
                    "physical_connection_generation": (
                        reference.physical_connection_generation
                    ),
                }
            )
        except ShadowEvidenceError:
            self._poisoned = True
            raise
        self._raw_number = raw_number
        self._kalshi_receipts[reference.raw_path] = reference.raw_sha256
        return reference

    def append_resolution(self, record: ShadowResolutionEvidence) -> None:
        if (
            self._mode == "price_only"
            or self._row_number != 0
            or self._resolution_identity is not None
        ):
            _fail("shadow_evidence_resolution_invalid")
        value = _validate_resolution(record)
        self._validate_reference(
            value.provider_discovery_raw_path,
            value.provider_discovery_raw_sha256,
            kalshi=False,
        )
        self._append_record(
            {
                "schema": "inci-tennis-unqualified-shadow-resolution-v1",
                "kind": "resolution",
                "trust": "unqualified_shadow",
                "reason": "strict_name_start_selected",
                "selected_wall_ns": value.selected_wall_ns,
                "provider_match_id": value.provider_match_id,
                "provider_start_wall_ns": value.provider_start_wall_ns,
                "event_ticker": value.event_ticker,
                "home_player_name": value.home_player_name,
                "away_player_name": value.away_player_name,
                "market_tickers": list(value.market_tickers),
                "provider_discovery_raw_path": (
                    value.provider_discovery_raw_path
                ),
                "provider_discovery_raw_sha256": (
                    value.provider_discovery_raw_sha256
                ),
                "kalshi_catalog_sha256": value.kalshi_catalog_sha256,
                "resolver_snapshot_sha256": (
                    value.resolver_snapshot_sha256
                ),
                "resolver_rule_version": value.resolver_rule_version,
            }
        )
        self._resolution_identity = _resolution_identity(value)
        self._resolution_row_sha256 = self._previous_row_sha256

    def _validate_reference(
        self,
        raw_path: object,
        raw_digest: object,
        *,
        kalshi: bool,
    ) -> None:
        if raw_path is None and raw_digest is None:
            return
        if (
            type(raw_path) is not str
            or not os.path.isabs(raw_path)
            or type(raw_digest) is not str
            or _DIGEST_PATTERN.fullmatch(raw_digest) is None
        ):
            _fail("shadow_evidence_reference_invalid")
        path = Path(raw_path)
        if kalshi and path.parent != self.raw_root:
            _fail("shadow_evidence_reference_invalid")
        payload = _validate_existing_regular_file(
            path,
            expected_mode=0o600,
            code="shadow_evidence_reference_invalid",
            read_payload=True,
            maximum_bytes=(
                _MAXIMUM_KALSHI_FRAME_BYTES
                if kalshi
                else _MAXIMUM_SPORTRADAR_CAPTURE_BYTES
            ),
        )
        maximum = (
            _MAXIMUM_KALSHI_FRAME_BYTES
            if kalshi
            else _MAXIMUM_SPORTRADAR_CAPTURE_BYTES
        )
        if payload is None or not payload or len(payload) > maximum:
            _fail("shadow_evidence_reference_invalid")
        if sha256(payload).hexdigest() != raw_digest:
            _fail("shadow_evidence_reference_invalid")

    def _append_record(self, row: dict[str, object]) -> None:
        if self._closed or self._poisoned:
            _fail("shadow_evidence_closed")
        if type(row) is not dict:
            _fail("shadow_evidence_row_invalid")
        schema = row.get("schema")
        kind = row.get("kind")
        if (
            type(kind) is not str
            or _SCHEMA_BY_KIND.get(kind) != schema
            or row.get("trust") != _TRUST_BY_KIND.get(kind)
            or type(row.get("reason")) is not str
            or not row["reason"]
            or self._terminal_recorded
        ):
            _fail("shadow_evidence_row_invalid")
        if any(
            "key" in str(name).casefold() or "payload" in str(name).casefold()
            for name in row
        ) or _CHAIN_FIELDS.intersection(row):
            _fail("shadow_evidence_row_invalid")
        row_number = self._row_number + 1
        persisted = {
            **row,
            "session_id": self.session_id,
            "row_number": row_number,
            "previous_row_sha256": self._previous_row_sha256,
        }
        current_digest = _row_digest(persisted)
        persisted["row_sha256"] = current_digest
        payload = _canonical_json(persisted) + b"\n"
        self._ensure_protocol_epoch()
        pending_path, _ = self._create_pending_marker(row_number, current_digest)
        try:
            _write_all(
                self._ledger_fd,
                payload,
                "shadow_evidence_write_failed",
            )
        except ShadowEvidenceError:
            self._poisoned = True
            raise
        self._commit_pending_marker(pending_path)
        self._row_number = row_number
        self._previous_row_sha256 = current_digest
        if kind in {"terminal", "auto_terminal", "price_only_terminal"}:
            self._terminal_recorded = True
            self._terminal_row_sha256 = current_digest

    def append_observation(self, record: ShadowEvidenceObservation) -> None:
        if self._mode == "price_only":
            _fail("shadow_evidence_row_invalid")
        value = _validate_observation(record)
        if self._resolution_identity is not None and (
            value.provider_match_id,
            value.market_tickers,
            value.home_player_name,
            value.away_player_name,
        ) != self._resolution_identity:
            _fail("shadow_evidence_resolution_invalid")
        self._validate_reference(
            value.provider_raw_path,
            value.provider_raw_sha256,
            kalshi=False,
        )
        self._validate_reference(
            value.kalshi_raw_path,
            value.kalshi_raw_sha256,
            kalshi=True,
        )
        if value.kalshi_raw_path is not None and (
            self._kalshi_receipts.get(value.kalshi_raw_path)
            != value.kalshi_raw_sha256
        ):
            _fail("shadow_evidence_reference_invalid")
        self._append_record(
            {
                "schema": "inci-tennis-unqualified-shadow-observation-v1",
                "kind": "observation",
                "trust": "unqualified_shadow",
                "observed_wall_ns": value.observed_wall_ns,
                "observed_monotonic_ns": value.observed_monotonic_ns,
                "clock_uncertainty_ns": value.clock_uncertainty_ns,
                "provider_match_id": value.provider_match_id,
                "market_tickers": list(value.market_tickers),
                "provider_generated_wall_ns": value.provider_generated_wall_ns,
                "provider_captured_wall_ns": value.provider_captured_wall_ns,
                "provider_request_started_wall_ns": (
                    value.provider_request_started_wall_ns
                ),
                "provider_request_started_monotonic_ns": (
                    value.provider_request_started_monotonic_ns
                ),
                "provider_request_completed_wall_ns": (
                    value.provider_request_completed_wall_ns
                ),
                "provider_request_completed_monotonic_ns": (
                    value.provider_request_completed_monotonic_ns
                ),
                "provider_clock_uncertainty_ns": (
                    value.provider_clock_uncertainty_ns
                ),
                "provider_raw_path": value.provider_raw_path,
                "provider_raw_sha256": value.provider_raw_sha256,
                "home_player_name": value.home_player_name,
                "away_player_name": value.away_player_name,
                "match_status": value.match_status,
                "sets": list(value.sets),
                "games": list(value.games),
                "points": list(value.points),
                "server": value.server,
                "sportradar_age_ns": value.sportradar_age_ns,
                "progression": value.progression,
                "last_event_id": value.last_event_id,
                "last_event_type": value.last_event_type,
                "last_event_result": value.last_event_result,
                "kalshi_raw_path": value.kalshi_raw_path,
                "kalshi_raw_sha256": value.kalshi_raw_sha256,
                "kalshi_captured_wall_ns": value.kalshi_captured_wall_ns,
                "kalshi_captured_monotonic_ns": value.kalshi_captured_monotonic_ns,
                "kalshi_generation": value.kalshi_generation,
                "kalshi_sequence": value.kalshi_sequence,
                "kalshi_age_ns": value.kalshi_age_ns,
                "kalshi_status": value.kalshi_status,
                "home_ticker": value.home_market.ticker,
                "home_yes_bid": value.home_market.yes_bid,
                "home_yes_ask": value.home_market.yes_ask,
                "home_bid_depth": value.home_market.bid_depth,
                "home_ask_depth": value.home_market.ask_depth,
                "away_ticker": value.away_market.ticker,
                "away_yes_bid": value.away_market.yes_bid,
                "away_yes_ask": value.away_market.yes_ask,
                "away_bid_depth": value.away_market.bid_depth,
                "away_ask_depth": value.away_market.ask_depth,
                "reason": value.reason,
                "sportradar_captures": value.sportradar_captures,
                "kalshi_frames": value.kalshi_frames,
            }
        )

    def append_price_only_session(
        self, record: PriceOnlySessionEvidence
    ) -> None:
        """Fix a ledger into the independent Kalshi-only evidence grammar."""

        if (
            self._closed
            or self._poisoned
            or self._row_number != 0
            or self._mode is not None
            or self._price_only_session is not None
        ):
            _fail("shadow_evidence_row_invalid")
        value = _validate_price_only_session(record)
        if value.provider_discovery_raw_path is not None:
            self._validate_reference(
                value.provider_discovery_raw_path,
                value.provider_discovery_raw_sha256,
                kalshi=False,
            )
        if value.predecessor_session_id is not None:
            predecessor = self._eligible_predecessor_terminals.get(
                value.predecessor_session_id
            )
            if predecessor is None or (
                predecessor[0] != value.predecessor_terminal_row_sha256
                or predecessor[1] >= value.selected_wall_ns
            ):
                _fail("shadow_evidence_reference_invalid")
        self._append_record(
            {
                "schema": "inci-tennis-price-only-session-v1",
                "kind": "price_only_session",
                "trust": "PRICE_ONLY",
                "reason": "price_only_selected",
                "selected_wall_ns": value.selected_wall_ns,
                "selected_monotonic_ns": value.selected_monotonic_ns,
                "event_ticker": value.event_ticker,
                "player_a_name": value.player_a_name,
                "player_b_name": value.player_b_name,
                "market_tickers": list(value.market_tickers),
                "scheduled_start_wall_ns": value.scheduled_start_wall_ns,
                "catalog_sport": value.catalog_sport,
                "catalog_scope": value.catalog_scope,
                "catalog_queried_competitions": list(
                    value.catalog_queried_competitions
                ),
                "catalog_series_ticker": value.catalog_series_ticker,
                "catalog_milestone_id": value.catalog_milestone_id,
                "catalog_milestone_league": value.catalog_milestone_league,
                "initial_book_state": value.initial_book_state,
                "initial_market_a_ticker": value.initial_market_a.ticker,
                "initial_market_a_yes_bid": value.initial_market_a.yes_bid,
                "initial_market_a_yes_ask": value.initial_market_a.yes_ask,
                "initial_market_a_bid_depth": value.initial_market_a.bid_depth,
                "initial_market_a_ask_depth": value.initial_market_a.ask_depth,
                "initial_market_b_ticker": value.initial_market_b.ticker,
                "initial_market_b_yes_bid": value.initial_market_b.yes_bid,
                "initial_market_b_yes_ask": value.initial_market_b.yes_ask,
                "initial_market_b_bid_depth": value.initial_market_b.bid_depth,
                "initial_market_b_ask_depth": value.initial_market_b.ask_depth,
                "provider_discovery_state": value.provider_discovery_state,
                "provider_discovery_reason": value.provider_discovery_reason,
                "provider_discovery_raw_path": (
                    value.provider_discovery_raw_path
                ),
                "provider_discovery_raw_sha256": (
                    value.provider_discovery_raw_sha256
                ),
                "kalshi_catalog_sha256": value.kalshi_catalog_sha256,
                "resolver_snapshot_sha256": value.resolver_snapshot_sha256,
                "resolver_version": value.resolver_version,
                "registry_digest": value.registry_digest,
                "authority_scope": "observation_only",
                "execution_authorized": False,
                "score_feed": "none",
                "predecessor_session_id": value.predecessor_session_id,
                "predecessor_terminal_row_sha256": (
                    value.predecessor_terminal_row_sha256
                ),
            }
        )
        self._mode = "price_only"
        self._price_only_session = value
        self._price_only_session_row_sha256 = self._previous_row_sha256

    def append_price_only_observation(
        self, record: PriceOnlyEvidenceObservation
    ) -> None:
        if (
            self._mode != "price_only"
            or self._price_only_session is None
            or self._terminal_recorded
        ):
            _fail("shadow_evidence_row_invalid")
        value = _validate_price_only_observation(record)
        if (
            value.event_ticker != self._price_only_session.event_ticker
            or value.market_tickers != self._price_only_session.market_tickers
            or value.kalshi_frames != self._raw_number
        ):
            _fail("shadow_evidence_reference_invalid")
        self._validate_reference(
            value.kalshi_raw_path,
            value.kalshi_raw_sha256,
            kalshi=True,
        )
        if value.kalshi_raw_path is not None and (
            self._kalshi_receipts.get(value.kalshi_raw_path)
            != value.kalshi_raw_sha256
        ):
            _fail("shadow_evidence_reference_invalid")
        self._append_record(
            {
                "schema": "inci-tennis-price-only-observation-v1",
                "kind": "price_only_observation",
                "trust": "PRICE_ONLY",
                "reason": value.reason,
                "observed_wall_ns": value.observed_wall_ns,
                "observed_monotonic_ns": value.observed_monotonic_ns,
                "clock_uncertainty_ns": value.clock_uncertainty_ns,
                "event_ticker": value.event_ticker,
                "market_tickers": list(value.market_tickers),
                "kalshi_raw_path": value.kalshi_raw_path,
                "kalshi_raw_sha256": value.kalshi_raw_sha256,
                "kalshi_captured_wall_ns": value.kalshi_captured_wall_ns,
                "kalshi_captured_monotonic_ns": (
                    value.kalshi_captured_monotonic_ns
                ),
                "kalshi_generation": value.kalshi_generation,
                "kalshi_sequence": value.kalshi_sequence,
                "kalshi_age_ns": value.kalshi_age_ns,
                "kalshi_status": value.kalshi_status,
                "market_a_ticker": value.market_a.ticker,
                "market_a_yes_bid": value.market_a.yes_bid,
                "market_a_yes_ask": value.market_a.yes_ask,
                "market_a_bid_depth": value.market_a.bid_depth,
                "market_a_ask_depth": value.market_a.ask_depth,
                "market_b_ticker": value.market_b.ticker,
                "market_b_yes_bid": value.market_b.yes_bid,
                "market_b_yes_ask": value.market_b.yes_ask,
                "market_b_bid_depth": value.market_b.bid_depth,
                "market_b_ask_depth": value.market_b.ask_depth,
                "kalshi_frames": value.kalshi_frames,
            }
        )

    def append_price_only_terminal(
        self,
        *,
        reason: str,
        code: str | None,
        ended_wall_ns: int,
        ended_monotonic_ns: int,
        event_ticker: str,
        market_tickers: tuple[str, str],
        kalshi_frames: int,
    ) -> None:
        session = self._price_only_session
        if (
            self._mode != "price_only"
            or session is None
            or reason not in _PRICE_ONLY_TERMINAL_REASONS
            or reason == "halted"
            and (
                type(code) is not str
                or _SAFE_CODE_PATTERN.fullmatch(code) is None
            )
            or reason != "halted"
            and code is not None
            or type(ended_wall_ns) is not int
            or ended_wall_ns <= 0
            or type(ended_monotonic_ns) is not int
            or ended_monotonic_ns < 0
            or type(event_ticker) is not str
            or _TICKER_PATTERN.fullmatch(event_ticker) is None
            or not _valid_price_only_market_tickers(market_tickers)
            or type(kalshi_frames) is not int
            or kalshi_frames < 0
            or kalshi_frames != self._raw_number
            or event_ticker != session.event_ticker
            or market_tickers != session.market_tickers
            or not _valid_digest(self._price_only_session_row_sha256)
        ):
            _fail("shadow_evidence_terminal_invalid")
        self._append_record(
            {
                "schema": "inci-tennis-price-only-terminal-v1",
                "kind": "price_only_terminal",
                "trust": "PRICE_ONLY",
                "reason": reason,
                "code": code,
                "ended_wall_ns": ended_wall_ns,
                "ended_monotonic_ns": ended_monotonic_ns,
                "event_ticker": event_ticker,
                "market_tickers": list(market_tickers),
                "kalshi_frames": kalshi_frames,
                "mapping_mode": "price_only",
                "session_row_sha256": self._price_only_session_row_sha256,
            }
        )

    def ensure_price_only_halted_terminal(self, *, code: str) -> None:
        """Close a constructed price-only session after an IO-owned failure."""

        if self._terminal_recorded:
            return
        session = self._price_only_session
        if session is None:
            _fail("shadow_evidence_terminal_invalid")
        self.append_price_only_terminal(
            reason="halted",
            code=code,
            ended_wall_ns=time.time_ns(),
            ended_monotonic_ns=time.monotonic_ns(),
            event_ticker=session.event_ticker,
            market_tickers=session.market_tickers,
            kalshi_frames=self._raw_number,
        )

    def append_terminal(
        self,
        *,
        reason: str,
        code: str | None,
        ended_wall_ns: int,
        ended_monotonic_ns: int,
        provider_match_id: str,
        market_tickers: tuple[str, str],
        sportradar_captures: int,
        kalshi_frames: int,
    ) -> None:
        if (
            self._mode == "price_only"
            or self._closed
            or self._poisoned
            or self._terminal_recorded
            or reason not in _TERMINAL_REASONS
            or (reason == "halted")
            != (
                type(code) is str
                and _SAFE_CODE_PATTERN.fullmatch(code) is not None
            )
            or type(ended_wall_ns) is not int
            or ended_wall_ns <= 0
            or type(ended_monotonic_ns) is not int
            or ended_monotonic_ns < 0
            or type(provider_match_id) is not str
            or _MATCH_PATTERN.fullmatch(provider_match_id) is None
            or type(market_tickers) is not tuple
            or len(market_tickers) != 2
            or market_tickers[0] == market_tickers[1]
            or any(
                type(item) is not str
                or _TICKER_PATTERN.fullmatch(item) is None
                for item in market_tickers
            )
            or type(sportradar_captures) is not int
            or sportradar_captures < 0
            or type(kalshi_frames) is not int
            or kalshi_frames < 0
            or kalshi_frames != self._raw_number
            or self._resolution_identity is not None
            and (
                provider_match_id,
                market_tickers,
            )
            != self._resolution_identity[:2]
            or self._resolution_identity is not None
            and (
                type(self._resolution_row_sha256) is not str
                or _DIGEST_PATTERN.fullmatch(
                    self._resolution_row_sha256
                )
                is None
            )
        ):
            _fail("shadow_evidence_terminal_invalid")
        row: dict[str, object] = {
            "schema": "inci-tennis-unqualified-shadow-terminal-v1",
            "kind": "terminal",
            "trust": "unqualified_shadow",
            "reason": reason,
            "code": code,
            "ended_wall_ns": ended_wall_ns,
            "ended_monotonic_ns": ended_monotonic_ns,
            "provider_match_id": provider_match_id,
            "market_tickers": list(market_tickers),
            "sportradar_captures": sportradar_captures,
            "kalshi_frames": kalshi_frames,
        }
        if self._resolution_identity is not None:
            row.update(
                {
                    "schema": (
                        "inci-tennis-unqualified-shadow-auto-terminal-v1"
                    ),
                    "kind": "auto_terminal",
                    "mapping_mode": "auto_matched",
                    "resolution_row_sha256": self._resolution_row_sha256,
                }
            )
        self._append_record(row)

    def ensure_halted_terminal(
        self,
        *,
        code: str,
        provider_match_id: str,
        market_tickers: tuple[str, str],
        sportradar_captures: int,
        kalshi_frames: int,
    ) -> None:
        """Durably close a post-construction failure using IO-owned clocks."""

        if self._terminal_recorded:
            return
        self.append_terminal(
            reason="halted",
            code=code,
            ended_wall_ns=time.time_ns(),
            ended_monotonic_ns=time.monotonic_ns(),
            provider_match_id=provider_match_id,
            market_tickers=market_tickers,
            sportradar_captures=sportradar_captures,
            kalshi_frames=kalshi_frames,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        ledger_fd = getattr(self, "_ledger_fd", None)
        if type(ledger_fd) is int:
            os.close(ledger_fd)
        lock_fd = getattr(self, "_lock_fd", None)
        if type(lock_fd) is int:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

    def __enter__(self) -> ShadowEvidenceStore:
        if self._closed:
            _fail("shadow_evidence_closed")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def audit_shadow_settlement_source(
    session_path: Path,
) -> _ShadowSettlementSourceAuditLease:
    """Audit one eligible terminal ledger without creating or changing state."""

    if not isinstance(session_path, Path) or not session_path.is_absolute():
        _fail("shadow_evidence_state_invalid")
    try:
        canonical_path = session_path.resolve(strict=True)
    except OSError:
        _fail("shadow_evidence_state_unsafe")
    if canonical_path != session_path:
        _fail("shadow_evidence_state_unsafe")
    name = canonical_path.name
    if not name.startswith("session-") or not name.endswith(".jsonl"):
        _fail("shadow_evidence_state_invalid")
    session_id = name[len("session-") : -len(".jsonl")]
    if (
        name != f"session-{session_id}.jsonl"
        or _canonical_session_id(session_id) is None
    ):
        _fail("shadow_evidence_state_invalid")
    state_root = canonical_path.parent
    raw_root = state_root / "raw"
    _validate_existing_private_directory(
        state_root, code="shadow_evidence_state_unsafe"
    )
    _validate_existing_private_directory(
        raw_root, code="shadow_evidence_state_unsafe"
    )
    _validate_existing_regular_file(
        canonical_path,
        expected_mode=0o600,
        code="shadow_evidence_state_unsafe",
        read_payload=False,
    )
    lock_fd = _open_existing_private_readonly_file(
        state_root / "shadow.lock", code="shadow_evidence_state_unsafe"
    )
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                _fail("shadow_evidence_locked")
            _fail("shadow_evidence_state_unavailable")
        adapter = object.__new__(ShadowEvidenceStore)
        adapter.state_root = state_root
        adapter.raw_root = raw_root
        adapter._eligible_predecessor_terminals = {}
        adapter._audited_price_predecessors = []
        adapter._audited_settlement_sources = {}
        adapter._audit_prior_sessions()
        source = adapter._audited_settlement_sources.get(session_id)
        if source is None or source.session_path != canonical_path:
            _fail("shadow_evidence_state_invalid")
        return _ShadowSettlementSourceAuditLease(source, lock_fd)
    except BaseException:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except BaseException:
            pass
        try:
            os.close(lock_fd)
        except BaseException:
            pass
        raise


__all__ = (
    "AuditedShadowSettlementSource",
    "KalshiOnlyCredentialMaterial",
    "PersistedKalshiFrame",
    "PriceOnlyEvidenceObservation",
    "PriceOnlySessionEvidence",
    "ShadowCredentialMaterial",
    "ShadowEvidenceObservation",
    "ShadowEvidenceError",
    "ShadowEvidenceStore",
    "ShadowMarketCandidate",
    "ShadowResolutionEvidence",
    "audit_shadow_settlement_source",
    "default_shadow_state_root",
    "load_kalshi_only_credential_material",
    "load_shadow_credential_material",
    "shadow_monotonic_ns",
    "shadow_kalshi_clock_observation",
    "shadow_pause",
    "shadow_wall_ns",
)
