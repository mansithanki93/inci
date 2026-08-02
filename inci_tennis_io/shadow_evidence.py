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
_FIELDS_BY_KIND = {
    "kalshi_capture": _KALSHI_CAPTURE_FIELDS | _CHAIN_FIELDS,
    "observation": _OBSERVATION_FIELDS | _CHAIN_FIELDS,
    "terminal": _TERMINAL_FIELDS | _CHAIN_FIELDS,
}
_SCHEMA_BY_KIND = {
    "observation": "inci-tennis-unqualified-shadow-observation-v1",
    "kalshi_capture": "inci-tennis-unqualified-shadow-kalshi-capture-v1",
    "terminal": "inci-tennis-unqualified-shadow-terminal-v1",
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
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        _fail("shadow_evidence_write_failed")
    finally:
        if descriptor is not None:
            os.close(descriptor)


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


class ShadowEvidenceStore:
    """One-owner append-only ledger with immutable raw Kalshi captures."""

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
        self._terminal_recorded = False
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

    def _audit_prior_sessions(self) -> None:
        receipts: dict[str, str] = {}
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
            terminal_indexes = [
                index for index, row in enumerate(rows) if row.get("kind") == "terminal"
            ]
            if terminal_indexes != [len(rows) - 1]:
                _fail("shadow_evidence_unclean_session")
            previous_digest = _ZERO_DIGEST
            session_receipts: dict[str, str] = {}
            capture_number = 0
            prior_sportradar_captures = 0
            for row_number, row in enumerate(rows, start=1):
                kind = row.get("kind")
                expected_fields = _FIELDS_BY_KIND.get(kind)
                claimed_digest = row.get("row_sha256")
                if (
                    expected_fields is None
                    or frozenset(row) != expected_fields
                    or row.get("schema") != _SCHEMA_BY_KIND.get(kind)
                    or row.get("trust") != "unqualified_shadow"
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
                if kind == "kalshi_capture":
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
                    if (
                        row["kalshi_frames"] != capture_number
                        or row["sportradar_captures"]
                        < prior_sportradar_captures
                    ):
                        _fail("shadow_evidence_prior_corrupt")
                    prior_sportradar_captures = row[
                        "sportradar_captures"
                    ]
                elif (
                    not _valid_terminal_row(row)
                    or row["kalshi_frames"] != capture_number
                    or row["sportradar_captures"]
                    < prior_sportradar_captures
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
        if self._closed:
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
        self._raw_number += 1
        path = self.raw_root / (
            f"{self.session_id}-{self._raw_number:08d}-kalshi.bin"
        )
        descriptor = _open_private_file(path, create=True, exclusive=True)
        try:
            _write_all(descriptor, payload, "shadow_evidence_raw_write_failed")
        finally:
            os.close(descriptor)
        _fsync_directory(self.raw_root)
        reference = PersistedKalshiFrame(
            raw_path=str(path),
            raw_sha256=digest,
            captured_wall_ns=captured_wall_ns,
            captured_monotonic_ns=captured_monotonic_ns,
            clock_uncertainty_ns=clock_uncertainty_ns,
            physical_connection_generation=generation,
        )
        self._append_record(
            {
                "schema": "inci-tennis-unqualified-shadow-kalshi-capture-v1",
                "kind": "kalshi_capture",
                "trust": "unqualified_shadow",
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
        self._kalshi_receipts[reference.raw_path] = reference.raw_sha256
        return reference

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
        if payload is None or len(payload) > maximum:
            _fail("shadow_evidence_reference_invalid")
        if sha256(payload).hexdigest() != raw_digest:
            _fail("shadow_evidence_reference_invalid")

    def _append_record(self, row: dict[str, object]) -> None:
        if self._closed:
            _fail("shadow_evidence_closed")
        if type(row) is not dict or row.get("trust") != "unqualified_shadow":
            _fail("shadow_evidence_row_invalid")
        schema = row.get("schema")
        kind = row.get("kind")
        if (
            type(kind) is not str
            or _SCHEMA_BY_KIND.get(kind) != schema
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
        self._row_number += 1
        persisted = {
            **row,
            "session_id": self.session_id,
            "row_number": self._row_number,
            "previous_row_sha256": self._previous_row_sha256,
        }
        current_digest = _row_digest(persisted)
        persisted["row_sha256"] = current_digest
        payload = _canonical_json(persisted) + b"\n"
        _write_all(
            self._ledger_fd,
            payload,
            "shadow_evidence_write_failed",
        )
        self._previous_row_sha256 = current_digest
        if kind == "terminal":
            self._terminal_recorded = True

    def append_observation(self, record: ShadowEvidenceObservation) -> None:
        value = _validate_observation(record)
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
            reason not in _TERMINAL_REASONS
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
        ):
            _fail("shadow_evidence_terminal_invalid")
        self._append_record(
            {
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
        )

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


__all__ = (
    "PersistedKalshiFrame",
    "ShadowCredentialMaterial",
    "ShadowEvidenceObservation",
    "ShadowEvidenceError",
    "ShadowEvidenceStore",
    "ShadowMarketCandidate",
    "default_shadow_state_root",
    "load_shadow_credential_material",
    "shadow_monotonic_ns",
    "shadow_kalshi_clock_observation",
    "shadow_pause",
    "shadow_wall_ns",
)
