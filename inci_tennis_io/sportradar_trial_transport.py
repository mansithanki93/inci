"""Read-only Sportradar trial transport with durable request accounting."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import pwd
import re
import signal
import time
from typing import Callable, Protocol
import uuid

import requests


_ORIGIN = "https://api.sportradar.com"
_MATCH_ID = r"sr:sport_event:[1-9][0-9]*\Z"
_ROUTES = frozenset({"live_summaries", "summary", "timeline"})
_MAXIMUM_BODY_BYTES = 8_388_608
_MAXIMUM_USAGE_LEDGER_BYTES = 4_194_304
_MAXIMUM_AUDIT_LEDGER_BYTES = 16_777_216
_MINIMUM_REQUEST_INTERVAL_NS = 1_000_000_000
_TOTAL_DEADLINE_NS = 15_000_000_000
_DEFAULT_SESSION_ATTEMPT_LIMIT = 400
_DEFAULT_ACCESS_ATTEMPT_LIMIT = 1_000
_USAGE_KEYS = frozenset(
    {
        "schema",
        "kind",
        "session_id",
        "session_attempt",
        "access_attempt",
        "route",
        "started_wall_ns",
    }
)
_OBSERVATION_KEYS = frozenset(
    {
        "schema",
        "command",
        "session_id",
        "session_attempt",
        "access_attempt",
        "route",
        "provider_match_id",
        "generated_wall_ns",
        "captured_wall_ns",
        "status",
        "match_status",
        "payload_sha256",
        "raw_file",
        "progression",
        "last_event_id",
        "terminal_reason",
    }
)
_PARSER_FAILURE_KEYS = frozenset(
    {
        "schema",
        "command",
        "session_id",
        "session_attempt",
        "access_attempt",
        "route",
        "payload_sha256",
        "raw_file",
        "code",
    }
)
_RECOVERY_KEYS = frozenset(
    {
        "schema",
        "session_id",
        "session_attempt",
        "access_attempt",
        "route",
        "payload_sha256",
        "raw_file",
        "code",
    }
)
_TERMINAL_KEYS = frozenset(
    {
        "schema",
        "command",
        "session_id",
        "provider_match_id",
        "reason",
        "code",
        "ended_wall_ns",
        "session_attempts",
        "access_attempts",
    }
)
_OUTCOME_KEYS = frozenset(
    {
        "schema",
        "session_id",
        "session_attempt",
        "access_attempt",
        "route",
        "outcome",
        "code",
        "captured_wall_ns",
        "payload_sha256",
        "raw_file",
    }
)
_COMMANDS = frozenset({"list_live", "check", "observe", "shadow"})
_AUDIT_COMMANDS = _COMMANDS | {"recovery"}
_TERMINAL_REASONS = frozenset(
    {
        "list_complete",
        "check_complete",
        "duration_elapsed",
        "operator_interrupt",
        "closed",
        "cancelled",
        "abandoned",
        "halted",
        "recovered_unclean_session",
    }
)
_CAPTURE_OUTCOME_CODES = frozenset(
    {
        "sportradar_capture_persisted",
        "sportradar_capture_persisted_interrupted",
    }
)
_UNCERTAIN_OUTCOME_CODE = "sportradar_process_crash_unresolved"
_SHADOW_TASK_CANCELLED_CODE = "sportradar_shadow_task_cancelled"


class SportradarTrialObserverError(RuntimeError):
    """A fixed diagnostic code with no response body or credential text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise SportradarTrialObserverError(code)


def default_trial_state_root() -> Path:
    """Return an OS-account-derived state path, independent of repository config."""

    try:
        home = pwd.getpwuid(os.getuid()).pw_dir
    except (KeyError, OSError):
        _fail("sportradar_os_account_unavailable")
    if type(home) is not str or not home or not os.path.isabs(home):
        _fail("sportradar_os_account_unavailable")
    return Path(home) / ".local" / "state" / "inci" / "sportradar-trial"


def load_trial_api_key() -> str | None:
    """Read the trial key at the IO boundary without copying it into config."""

    return os.environ.get("SPORTRADAR_API_KEY")


def trial_monotonic_ns() -> int:
    return time.monotonic_ns()


def trial_sleep(seconds: float) -> None:
    time.sleep(seconds)


@dataclass(frozen=True, slots=True)
class TrialAttemptReservation:
    session_id: str
    session_attempt: int
    access_attempt: int
    route: str
    started_wall_ns: int


@dataclass(frozen=True, slots=True)
class TrialCapture:
    reservation: TrialAttemptReservation
    captured_wall_ns: int
    raw_path: Path
    payload: bytes


@dataclass(frozen=True, slots=True)
class TrialObservationRecord:
    command: str
    reservation: TrialAttemptReservation
    provider_match_id: str | None
    generated_wall_ns: int
    captured_wall_ns: int
    status: str
    match_status: str | None
    payload_sha256: str
    raw_path: Path
    progression: str
    last_event_id: int | None
    terminal_reason: str | None


class _Response(Protocol):
    status_code: int
    headers: object

    def iter_content(self, chunk_size: int) -> object: ...

    def close(self) -> None: ...


def _private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
    except OSError:
        _fail("sportradar_state_unavailable")
    if not path.is_dir() or path.is_symlink() or info.st_uid != os.getuid():
        _fail("sportradar_state_unsafe")
    if info.st_mode & 0o022:
        _fail("sportradar_state_unsafe")


def _fsync_directory(path: Path, code: str) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        _fail(code)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _secure_open(path: Path, flags: int) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags | nofollow, 0o600)
        info = os.fstat(descriptor)
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        _fail("sportradar_state_unavailable")
    if not os.path.isfile(path) or info.st_uid != os.getuid():
        os.close(descriptor)
        _fail("sportradar_state_unsafe")
    return descriptor


def _positive_integer(value: object) -> int:
    if type(value) is not int or value <= 0:
        _fail("sportradar_usage_ledger_corrupt")
    return value


def _ledger_pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            _fail("sportradar_usage_ledger_corrupt")
        result[key] = value
    return result


def _audit_pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            _fail("sportradar_audit_ledger_corrupt")
        result[key] = value
    return result


def _safe_code(value: object) -> bool:
    return (
        type(value) is str
        and re.fullmatch(r"sportradar_[a-z0-9_]{1,80}", value) is not None
    )


def _allowed_text(value: object, allowed: frozenset[str] | set[str]) -> bool:
    return type(value) is str and value in allowed


def _decoded_contains_secret(payload: bytes, secret: str) -> bool:
    try:
        value = json.loads(payload, object_pairs_hook=lambda pairs: pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return False
    pending = [value]
    visited = 0
    while pending:
        current = pending.pop()
        visited += 1
        if visited > 200_000:
            _fail("sportradar_response_body_invalid")
        if type(current) is str:
            if secret in current:
                return True
        elif type(current) in {list, tuple}:
            pending.extend(current)
    return False


def _escaped_contains_secret(payload: bytes, secret: str) -> bool:
    text = payload.decode("latin-1")
    escapes = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }

    def decode_escape(match: re.Match[str]) -> str:
        token = match.group(1)
        if token.startswith("u"):
            return chr(int(token[1:], 16))
        return escapes[token]

    normalized = re.sub(
        r'\\(u[0-9a-fA-F]{4}|["\\/bfnrt])',
        decode_escape,
        text,
    )
    return secret in normalized


class _HardDeadline:
    """Process-level wall deadline around blocking requests IO on POSIX."""

    def __init__(self, duration_ns: int | None = None) -> None:
        self._previous_handler: object | None = None
        self._duration_ns = (
            _TOTAL_DEADLINE_NS if duration_ns is None else duration_ns
        )

    @staticmethod
    def _expired(*_: object) -> None:
        _fail("sportradar_total_deadline")

    def __enter__(self) -> _HardDeadline:
        handler_installed = False
        try:
            if type(self._duration_ns) is not int or self._duration_ns <= 0:
                _fail("sportradar_total_deadline")
            current_timer = signal.getitimer(signal.ITIMER_REAL)
            if current_timer != (0.0, 0.0):
                _fail("sportradar_deadline_unavailable")
            self._previous_handler = signal.getsignal(signal.SIGALRM)
            signal.signal(signal.SIGALRM, self._expired)
            handler_installed = True
            signal.setitimer(
                signal.ITIMER_REAL,
                self._duration_ns / 1_000_000_000,
            )
        except SportradarTrialObserverError:
            try:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
            except (AttributeError, OSError, ValueError):
                pass
            if handler_installed and self._previous_handler is not None:
                signal.signal(signal.SIGALRM, self._previous_handler)
            raise
        except (AttributeError, OSError, ValueError):
            try:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
            except (AttributeError, OSError, ValueError):
                pass
            if handler_installed and self._previous_handler is not None:
                try:
                    signal.signal(signal.SIGALRM, self._previous_handler)
                except (AttributeError, OSError, ValueError):
                    pass
            _fail("sportradar_deadline_unavailable")
        return self

    def __exit__(self, *_: object) -> None:
        try:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            if self._previous_handler is not None:
                signal.signal(signal.SIGALRM, self._previous_handler)
        except (AttributeError, OSError, ValueError):
            _fail("sportradar_deadline_unavailable")


class TrialUsageLedger:
    """Single-owner, append-only budget for a finite trial credential."""

    def __init__(
        self,
        state_root: Path | None = None,
        *,
        session_attempt_limit: int = _DEFAULT_SESSION_ATTEMPT_LIMIT,
        access_attempt_limit: int = _DEFAULT_ACCESS_ATTEMPT_LIMIT,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        wall_ns: Callable[[], int] = time.time_ns,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            type(session_attempt_limit) is not int
            or type(access_attempt_limit) is not int
            or session_attempt_limit <= 0
            or access_attempt_limit <= 0
            or session_attempt_limit > access_attempt_limit
            or session_attempt_limit > _DEFAULT_SESSION_ATTEMPT_LIMIT
            or access_attempt_limit > _DEFAULT_ACCESS_ATTEMPT_LIMIT
        ):
            _fail("sportradar_attempt_limit_invalid")
        root = default_trial_state_root() if state_root is None else state_root
        if not isinstance(root, Path) or not root.is_absolute():
            _fail("sportradar_state_root_invalid")
        _private_directory(root)
        raw_root = root / "raw"
        _private_directory(raw_root)
        self.state_root = root
        self.raw_root = raw_root
        self.usage_path = root / "usage.jsonl"
        self.observations_path = root / "observations.jsonl"
        self.outcomes_path = root / "outcomes.jsonl"
        self._session_attempt_limit = session_attempt_limit
        self._access_attempt_limit = access_attempt_limit
        self._monotonic_ns = monotonic_ns
        self._wall_ns = wall_ns
        self._sleeper = sleeper
        self._session_id = str(uuid.uuid4())
        self._session_attempts = 0
        self._access_attempts = 0
        self._last_started_wall_ns: int | None = None
        self._last_started_monotonic_ns: int | None = None
        self._attempt_history: dict[
            int,
            tuple[str, int, str],
        ] = {}
        self._reservations: dict[int, TrialAttemptReservation] = {}
        self._captures: dict[int, tuple[Path, str, int]] = {}
        self._recorded_observations: set[int] = set()
        self._recorded_outcomes: set[int] = set()
        self._session_terminal_recorded = False
        self._recovered_uncertain_attempts = 0
        self._recovered_incomplete_captures = 0
        self._recovered_unclean_sessions = 0
        self._closed = False
        self._lock_fd = _secure_open(root / "usage.lock", os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(self._lock_fd)
            self._closed = True
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                _fail("sportradar_usage_ledger_locked")
            _fail("sportradar_state_unavailable")
        try:
            self._usage_fd = _secure_open(
                self.usage_path,
                os.O_RDWR | os.O_CREAT | os.O_APPEND,
            )
            self._observations_fd = _secure_open(
                self.observations_path,
                os.O_RDWR | os.O_CREAT | os.O_APPEND,
            )
            self._outcomes_fd = _secure_open(
                self.outcomes_path,
                os.O_RDWR | os.O_CREAT | os.O_APPEND,
            )
            _fsync_directory(root, "sportradar_state_unavailable")
            self._load()
            observation_rows = self._validate_audit_file(
                self._observations_fd,
                "observations",
            )
            outcome_rows = self._validate_audit_file(
                self._outcomes_fd,
                "outcomes",
            )
            self._validate_outcome_history(outcome_rows)
            raw_inventory = self._validate_raw_inventory(outcome_rows)
            observed, terminal_sessions = self._validate_observation_history(
                observation_rows,
                outcome_rows,
            )
            present = {row["access_attempt"] for row in outcome_rows}
            missing = sorted(set(self._attempt_history) - present)
            for access_attempt in missing:
                session_id, session_attempt, route = self._attempt_history[
                    access_attempt
                ]
                raw_file = f"{access_attempt:04d}_{route}.json"
                raw_digest = raw_inventory.get(raw_file)
                self._append_record(
                    self._outcomes_fd,
                    {
                        "schema": "inci-sportradar-trial-outcome-v1",
                        "session_id": session_id,
                        "session_attempt": session_attempt,
                        "access_attempt": access_attempt,
                        "route": route,
                        "outcome": "uncertain",
                        "code": _UNCERTAIN_OUTCOME_CODE,
                        "captured_wall_ns": None,
                        "payload_sha256": raw_digest,
                        "raw_file": raw_file if raw_digest is not None else None,
                    },
                    "sportradar_outcome_write_failed",
                )
            self._recovered_uncertain_attempts = len(missing)
            captured_without_disposition = [
                row
                for row in outcome_rows
                if row["outcome"] == "captured"
                and row["access_attempt"] not in observed
            ]
            for row in captured_without_disposition:
                self._append_record(
                    self._observations_fd,
                    {
                        "schema": "inci-sportradar-trial-recovery-v1",
                        "session_id": row["session_id"],
                        "session_attempt": row["session_attempt"],
                        "access_attempt": row["access_attempt"],
                        "route": row["route"],
                        "payload_sha256": row["payload_sha256"],
                        "raw_file": row["raw_file"],
                        "code": (
                            "sportradar_process_crash_before_disposition"
                        ),
                    },
                    "sportradar_observation_write_failed",
                )
            self._recovered_incomplete_captures = len(
                captured_without_disposition
            )
            usage_sessions = {
                session_id
                for session_id, _, _ in self._attempt_history.values()
            }
            unclean_sessions = sorted(usage_sessions - terminal_sessions)
            ended_wall_ns = self._wall_ns()
            if (
                unclean_sessions
                and (type(ended_wall_ns) is not int or ended_wall_ns <= 0)
            ):
                _fail("sportradar_clock_invalid")
            for session_id in unclean_sessions:
                coordinates = [
                    coordinate
                    for coordinate, history in self._attempt_history.items()
                    if history[0] == session_id
                ]
                self._append_record(
                    self._observations_fd,
                    {
                        "schema": "inci-sportradar-trial-terminal-v1",
                        "command": "recovery",
                        "session_id": session_id,
                        "provider_match_id": None,
                        "reason": "recovered_unclean_session",
                        "code": (
                            "sportradar_process_crash_unclosed_session"
                        ),
                        "ended_wall_ns": ended_wall_ns,
                        "session_attempts": len(coordinates),
                        "access_attempts": max(coordinates),
                    },
                    "sportradar_observation_write_failed",
                )
            self._recovered_unclean_sessions = len(unclean_sessions)
        except BaseException:
            self.close()
            raise

    def __repr__(self) -> str:
        return (
            "TrialUsageLedger(state_root="
            f"{self.state_root!r}, session_attempts={self._session_attempts}, "
            f"access_attempts={self._access_attempts})"
        )

    @property
    def session_attempts(self) -> int:
        return self._session_attempts

    @property
    def access_attempts(self) -> int:
        return self._access_attempts

    @property
    def remaining_session_attempts(self) -> int:
        return self._session_attempt_limit - self._session_attempts

    @property
    def remaining_access_attempts(self) -> int:
        return self._access_attempt_limit - self._access_attempts

    @property
    def recovered_uncertain_attempts(self) -> int:
        return self._recovered_uncertain_attempts

    @property
    def recovered_incomplete_captures(self) -> int:
        return self._recovered_incomplete_captures

    @property
    def recovered_unclean_sessions(self) -> int:
        return self._recovered_unclean_sessions

    def _load(self) -> None:
        try:
            if os.fstat(self._usage_fd).st_size > _MAXIMUM_USAGE_LEDGER_BYTES:
                _fail("sportradar_usage_ledger_corrupt")
            os.lseek(self._usage_fd, 0, os.SEEK_SET)
            with os.fdopen(os.dup(self._usage_fd), "rb") as stream:
                rows = stream.read().splitlines()
        except OSError:
            _fail("sportradar_state_unavailable")
        last_wall: int | None = None
        session_coordinates: dict[str, int] = {}
        for coordinate, raw in enumerate(rows, start=1):
            try:
                row = json.loads(raw, object_pairs_hook=_ledger_pairs)
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                ValueError,
                RecursionError,
            ):
                _fail("sportradar_usage_ledger_corrupt")
            if type(row) is not dict or set(row) != _USAGE_KEYS:
                _fail("sportradar_usage_ledger_corrupt")
            if row["schema"] != "inci-sportradar-trial-usage-v1" or row["kind"] != "attempt":
                _fail("sportradar_usage_ledger_corrupt")
            session_id = row["session_id"]
            if type(session_id) is not str:
                _fail("sportradar_usage_ledger_corrupt")
            try:
                uuid.UUID(session_id)
            except (ValueError, AttributeError):
                _fail("sportradar_usage_ledger_corrupt")
            session_attempt = _positive_integer(row["session_attempt"])
            if session_attempt > self._session_attempt_limit:
                _fail("sportradar_usage_ledger_corrupt")
            expected_session = session_coordinates.get(session_id, 0) + 1
            if session_attempt != expected_session:
                _fail("sportradar_usage_ledger_corrupt")
            session_coordinates[session_id] = session_attempt
            if _positive_integer(row["access_attempt"]) != coordinate:
                _fail("sportradar_usage_ledger_corrupt")
            if not _allowed_text(row["route"], _ROUTES):
                _fail("sportradar_usage_ledger_corrupt")
            started_wall_ns = _positive_integer(row["started_wall_ns"])
            if last_wall is not None and started_wall_ns < last_wall:
                _fail("sportradar_usage_ledger_corrupt")
            last_wall = started_wall_ns
            self._attempt_history[coordinate] = (
                session_id,
                session_attempt,
                row["route"],
            )
        if len(rows) > self._access_attempt_limit:
            _fail("sportradar_access_attempt_limit")
        self._access_attempts = len(rows)
        self._last_started_wall_ns = last_wall

    def _validate_audit_file(
        self,
        descriptor: int,
        kind: str,
    ) -> list[dict[str, object]]:
        try:
            size = os.fstat(descriptor).st_size
            if size > _MAXIMUM_AUDIT_LEDGER_BYTES:
                _fail("sportradar_audit_ledger_corrupt")
            os.lseek(descriptor, 0, os.SEEK_SET)
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                payload = stream.read()
        except OSError:
            _fail("sportradar_state_unavailable")
        if payload and not payload.endswith(b"\n"):
            _fail("sportradar_audit_ledger_corrupt")
        rows: list[dict[str, object]] = []
        for raw in payload.splitlines():
            try:
                row = json.loads(raw, object_pairs_hook=_audit_pairs)
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                ValueError,
                RecursionError,
            ):
                _fail("sportradar_audit_ledger_corrupt")
            if type(row) is not dict:
                _fail("sportradar_audit_ledger_corrupt")
            schema = row.get("schema")
            valid = False
            if kind == "observations":
                valid = (
                    schema == "inci-sportradar-trial-observation-v1"
                    and set(row) == _OBSERVATION_KEYS
                ) or (
                    schema == "inci-sportradar-trial-parser-failure-v1"
                    and set(row) == _PARSER_FAILURE_KEYS
                ) or (
                    schema == "inci-sportradar-trial-recovery-v1"
                    and set(row) == _RECOVERY_KEYS
                ) or (
                    schema == "inci-sportradar-trial-terminal-v1"
                    and set(row) == _TERMINAL_KEYS
                )
            elif kind == "outcomes":
                valid = (
                    schema == "inci-sportradar-trial-outcome-v1"
                    and set(row) == _OUTCOME_KEYS
                )
            if not valid:
                _fail("sportradar_audit_ledger_corrupt")
            rows.append(row)
        return rows

    @staticmethod
    def _valid_match_id(value: object) -> bool:
        return value is None or (
            type(value) is str
            and re.fullmatch(_MATCH_ID, value, flags=re.ASCII) is not None
        )

    @staticmethod
    def _valid_uuid(value: object) -> bool:
        if type(value) is not str:
            return False
        try:
            uuid.UUID(value)
        except (ValueError, AttributeError):
            return False
        return True

    def _history_matches(self, row: dict[str, object]) -> bool:
        coordinate = row.get("access_attempt")
        if type(coordinate) is not int:
            return False
        history = self._attempt_history.get(coordinate)
        return history == (
            row.get("session_id"),
            row.get("session_attempt"),
            row.get("route"),
        )

    def _validate_outcome_history(
        self,
        rows: list[dict[str, object]],
    ) -> None:
        seen: set[int] = set()
        for row in rows:
            coordinate = row["access_attempt"]
            outcome = row["outcome"]
            if (
                type(coordinate) is not int
                or coordinate <= 0
                or coordinate in seen
                or not self._history_matches(row)
                or not _allowed_text(
                    outcome,
                    {"captured", "failed", "uncertain"},
                )
                or not _safe_code(row["code"])
            ):
                _fail("sportradar_audit_ledger_corrupt")
            seen.add(coordinate)
            if outcome == "captured":
                expected_file = f"{coordinate:04d}_{row['route']}.json"
                if (
                    type(row["captured_wall_ns"]) is not int
                    or row["captured_wall_ns"] <= 0
                    or type(row["payload_sha256"]) is not str
                    or re.fullmatch(
                        r"[0-9a-f]{64}",
                        row["payload_sha256"],
                    )
                    is None
                    or row["raw_file"] != expected_file
                ):
                    _fail("sportradar_audit_ledger_corrupt")
                if self._raw_digest(expected_file) != row["payload_sha256"]:
                    _fail("sportradar_audit_ledger_corrupt")
                if row["code"] not in _CAPTURE_OUTCOME_CODES:
                    _fail("sportradar_audit_ledger_corrupt")
            elif outcome == "uncertain":
                expected_file = f"{coordinate:04d}_{row['route']}.json"
                has_raw = row["raw_file"] is not None
                if (
                    row["code"] != _UNCERTAIN_OUTCOME_CODE
                    or row["captured_wall_ns"] is not None
                    or has_raw != (row["payload_sha256"] is not None)
                    or (
                        has_raw
                        and (
                            row["raw_file"] != expected_file
                            or type(row["payload_sha256"]) is not str
                            or re.fullmatch(
                                r"[0-9a-f]{64}",
                                row["payload_sha256"],
                            )
                            is None
                            or self._raw_digest(expected_file)
                            != row["payload_sha256"]
                        )
                    )
                ):
                    _fail("sportradar_audit_ledger_corrupt")
            elif (
                row["code"] in _CAPTURE_OUTCOME_CODES
                or row["code"] == _UNCERTAIN_OUTCOME_CODE
                or any(
                    row[name] is not None
                    for name in (
                        "captured_wall_ns",
                        "payload_sha256",
                        "raw_file",
                    )
                )
            ):
                _fail("sportradar_audit_ledger_corrupt")

    def _raw_digest(self, name: str) -> str:
        path = self.raw_root / name
        descriptor = _secure_open(path, os.O_RDONLY)
        try:
            size = os.fstat(descriptor).st_size
            if size < 0 or size > _MAXIMUM_BODY_BYTES:
                _fail("sportradar_audit_ledger_corrupt")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                payload = stream.read(_MAXIMUM_BODY_BYTES + 1)
        except OSError:
            _fail("sportradar_audit_ledger_corrupt")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(payload) > _MAXIMUM_BODY_BYTES:
            _fail("sportradar_audit_ledger_corrupt")
        return sha256(payload).hexdigest()

    def _validate_raw_inventory(
        self,
        outcome_rows: list[dict[str, object]],
    ) -> dict[str, str]:
        try:
            with os.scandir(self.raw_root) as entries:
                names = {entry.name for entry in entries}
        except OSError:
            _fail("sportradar_state_unavailable")
        if any(
            re.fullmatch(
                r"[0-9]{4,}_(?:live_summaries|summary|timeline)\.json",
                name,
                flags=re.ASCII,
            )
            is None
            for name in names
        ):
            _fail("sportradar_audit_ledger_corrupt")
        referenced = {
            row["raw_file"]
            for row in outcome_rows
            if row["raw_file"] is not None
        }
        outcome_coordinates = {
            row["access_attempt"] for row in outcome_rows
        }
        recoverable = {
            f"{coordinate:04d}_{history[2]}.json"
            for coordinate, history in self._attempt_history.items()
            if coordinate not in outcome_coordinates
        }
        if not referenced.issubset(names) or not names.issubset(
            referenced | recoverable
        ):
            _fail("sportradar_audit_ledger_corrupt")
        return {name: self._raw_digest(name) for name in names}

    def _validate_observation_history(
        self,
        rows: list[dict[str, object]],
        outcome_rows: list[dict[str, object]],
    ) -> tuple[set[int], set[str]]:
        captured = {
            row["access_attempt"]: row
            for row in outcome_rows
            if row["outcome"] == "captured"
        }
        observed: set[int] = set()
        terminal_sessions: set[str] = set()
        commands_by_session: dict[str, set[str]] = {}
        providers_by_session: dict[str, set[str]] = {}
        dispositions_by_session: dict[str, list[dict[str, object]]] = {}
        shadow_discovery_sessions: set[str] = set()
        shadow_collection_sessions: set[str] = set()
        coordinates_by_session: dict[str, list[int]] = {}
        for coordinate, history in self._attempt_history.items():
            coordinates_by_session.setdefault(history[0], []).append(coordinate)
        for row in rows:
            schema = row["schema"]
            if schema == "inci-sportradar-trial-terminal-v1":
                session_id = row["session_id"]
                if (
                    not self._valid_uuid(session_id)
                    or session_id in terminal_sessions
                    or not _allowed_text(row["command"], _AUDIT_COMMANDS)
                    or not self._valid_match_id(row["provider_match_id"])
                    or not _allowed_text(row["reason"], _TERMINAL_REASONS)
                    or type(row["ended_wall_ns"]) is not int
                    or row["ended_wall_ns"] <= 0
                    or type(row["session_attempts"]) is not int
                    or row["session_attempts"] < 0
                    or type(row["access_attempts"]) is not int
                    or row["access_attempts"] < 0
                    or row["access_attempts"] > self._access_attempts
                ):
                    _fail("sportradar_audit_ledger_corrupt")
                coordinates = coordinates_by_session.get(session_id)
                if coordinates is not None and (
                    row["session_attempts"] != len(coordinates)
                    or row["access_attempts"] != max(coordinates)
                ):
                    _fail("sportradar_audit_ledger_corrupt")
                if row["reason"] == "halted":
                    valid_code = _safe_code(row["code"])
                elif row["reason"] == "operator_interrupt":
                    valid_code = (
                        row["code"] == "sportradar_operator_interrupt"
                    )
                elif row["reason"] == "cancelled":
                    valid_code = row["code"] in {
                        None,
                        _SHADOW_TASK_CANCELLED_CODE,
                    }
                elif row["reason"] == "recovered_unclean_session":
                    valid_code = (
                        row["command"] == "recovery"
                        and coordinates is not None
                        and row["code"]
                        == "sportradar_process_crash_unclosed_session"
                    )
                else:
                    valid_code = row["code"] is None
                if not valid_code:
                    _fail("sportradar_audit_ledger_corrupt")
                commands = commands_by_session.get(session_id, set())
                providers = providers_by_session.get(session_id, set())
                dispositions = dispositions_by_session.get(session_id, [])
                command = row["command"]
                reason = row["reason"]
                provider = row["provider_match_id"]
                if command == "recovery":
                    coherent = (
                        reason == "recovered_unclean_session"
                        and provider is None
                    )
                else:
                    unselected_shadow = (
                        command == "shadow"
                        and provider is None
                        and not providers
                    )
                    coherent = (
                        (not commands or commands == {command})
                        and (
                            (command == "list_live" and provider is None)
                            or unselected_shadow
                            or (
                                command in {"check", "observe", "shadow"}
                                and provider is not None
                            )
                        )
                        and (
                            not providers
                            or providers == {provider}
                        )
                    )
                    last_is_observation = bool(dispositions) and (
                        dispositions[-1].get("schema")
                        == "inci-sportradar-trial-observation-v1"
                    )
                    if coherent and reason == "list_complete":
                        coherent = (
                            (
                                command == "list_live"
                                and provider is None
                                or unselected_shadow
                            )
                            and last_is_observation
                            and dispositions[-1].get("route")
                            == "live_summaries"
                            and dispositions[-1].get("provider_match_id")
                            is None
                            and dispositions[-1].get("progression")
                            == "discovery"
                        )
                    elif coherent and reason == "check_complete":
                        coherent = (
                            command == "check"
                            and last_is_observation
                            and dispositions[-1].get("terminal_reason") is None
                        )
                    elif coherent and reason == "duration_elapsed":
                        coherent = (
                            command in {"observe", "shadow"}
                            and provider is not None
                            and last_is_observation
                        )
                    elif (
                        coherent
                        and reason == "cancelled"
                        and row["code"] == _SHADOW_TASK_CANCELLED_CODE
                    ):
                        coherent = command == "shadow"
                    elif coherent and reason in {
                        "closed",
                        "cancelled",
                        "abandoned",
                    }:
                        coherent = (
                            command in {"check", "observe", "shadow"}
                            and last_is_observation
                            and dispositions[-1].get("terminal_reason") == reason
                        )
                if not coherent:
                    _fail("sportradar_audit_ledger_corrupt")
                terminal_sessions.add(session_id)
                continue
            session_id = row["session_id"]
            coordinate = row["access_attempt"]
            if type(session_id) is not str or type(coordinate) is not int:
                _fail("sportradar_audit_ledger_corrupt")
            if (
                session_id in terminal_sessions
                and schema != "inci-sportradar-trial-recovery-v1"
            ):
                _fail("sportradar_audit_ledger_corrupt")
            outcome = captured.get(coordinate)
            if (
                type(coordinate) is not int
                or coordinate in observed
                or not self._history_matches(row)
                or outcome is None
                or row["payload_sha256"] != outcome["payload_sha256"]
                or row["raw_file"] != outcome["raw_file"]
            ):
                _fail("sportradar_audit_ledger_corrupt")
            observed.add(coordinate)
            if schema == "inci-sportradar-trial-recovery-v1":
                if (
                    row["code"]
                    != "sportradar_process_crash_before_disposition"
                ):
                    _fail("sportradar_audit_ledger_corrupt")
                dispositions_by_session.setdefault(
                    row["session_id"], []
                ).append(row)
                continue
            command = row["command"]
            expected_routes = {
                "list_live": {"live_summaries"},
                "check": {"summary"},
                "observe": {"summary", "timeline"},
                "shadow": {"live_summaries", "summary", "timeline"},
            }
            if not _allowed_text(command, _COMMANDS) or not _allowed_text(
                row["route"], expected_routes[command]
            ):
                _fail("sportradar_audit_ledger_corrupt")
            if command == "shadow":
                session = row["session_id"]
                if row["route"] == "live_summaries":
                    if (
                        session in shadow_discovery_sessions
                        or session in shadow_collection_sessions
                    ):
                        _fail("sportradar_audit_ledger_corrupt")
                    shadow_discovery_sessions.add(session)
                else:
                    shadow_collection_sessions.add(session)
            commands_by_session.setdefault(row["session_id"], set()).add(
                command
            )
            if schema == "inci-sportradar-trial-parser-failure-v1":
                if not _safe_code(row["code"]):
                    _fail("sportradar_audit_ledger_corrupt")
                dispositions_by_session.setdefault(
                    row["session_id"], []
                ).append(row)
                continue
            if (
                not self._valid_match_id(row["provider_match_id"])
                or type(row["generated_wall_ns"]) is not int
                or row["generated_wall_ns"] <= 0
                or row["captured_wall_ns"]
                != outcome["captured_wall_ns"]
                or type(row["status"]) is not str
                or not row["status"]
                or len(row["status"]) > 64
                or (
                    row["match_status"] is not None
                    and (
                        type(row["match_status"]) is not str
                        or not row["match_status"]
                        or len(row["match_status"]) > 64
                    )
                )
                or type(row["progression"]) is not str
                or re.fullmatch(r"[a-z0-9_]{1,64}", row["progression"])
                is None
                or (
                    row["last_event_id"] is not None
                    and (
                        type(row["last_event_id"]) is not int
                        or row["last_event_id"] <= 0
                    )
                )
                or (
                    row["terminal_reason"] is not None
                    and not _allowed_text(
                        row["terminal_reason"],
                        {"empty", "closed", "cancelled", "abandoned"},
                    )
                )
            ):
                _fail("sportradar_audit_ledger_corrupt")
            provider = row["provider_match_id"]
            if command == "shadow" and (
                row["route"] == "live_summaries"
                and (
                    provider is not None
                    or row["progression"] != "discovery"
                    or row["last_event_id"] is not None
                )
                or row["route"] in {"summary", "timeline"}
                and provider is None
            ):
                _fail("sportradar_audit_ledger_corrupt")
            if provider is not None:
                providers_by_session.setdefault(
                    row["session_id"], set()
                ).add(provider)
            dispositions_by_session.setdefault(row["session_id"], []).append(
                row
            )
        return observed, terminal_sessions

    @staticmethod
    def _append_record(descriptor: int, row: dict[str, object], code: str) -> None:
        payload = (
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        try:
            written = os.write(descriptor, payload)
            if written != len(payload):
                _fail(code)
            os.fsync(descriptor)
        except OSError:
            _fail(code)

    def reserve(self, route: str) -> TrialAttemptReservation:
        if self._closed:
            _fail("sportradar_usage_ledger_closed")
        if not _allowed_text(route, _ROUTES):
            _fail("sportradar_route_invalid")
        if self._session_terminal_recorded:
            _fail("sportradar_session_terminal")
        if self._session_attempts >= self._session_attempt_limit:
            _fail("sportradar_session_attempt_limit")
        if self._access_attempts >= self._access_attempt_limit:
            _fail("sportradar_access_attempt_limit")

        wall_now = self._wall_ns()
        monotonic_now = self._monotonic_ns()
        if type(wall_now) is not int or type(monotonic_now) is not int:
            _fail("sportradar_clock_invalid")
        waits = [0]
        if self._last_started_wall_ns is not None:
            if wall_now < self._last_started_wall_ns:
                _fail("sportradar_clock_invalid")
            waits.append(
                _MINIMUM_REQUEST_INTERVAL_NS
                - (wall_now - self._last_started_wall_ns)
            )
        if self._last_started_monotonic_ns is not None:
            if monotonic_now < self._last_started_monotonic_ns:
                _fail("sportradar_clock_invalid")
            waits.append(
                _MINIMUM_REQUEST_INTERVAL_NS
                - (monotonic_now - self._last_started_monotonic_ns)
            )
        wait_ns = max(waits)
        if wait_ns > 0:
            self._sleeper(wait_ns / 1_000_000_000)
            wall_now = self._wall_ns()
            monotonic_now = self._monotonic_ns()
        if (
            type(wall_now) is not int
            or type(monotonic_now) is not int
            or (
                self._last_started_wall_ns is not None
                and wall_now - self._last_started_wall_ns
                < _MINIMUM_REQUEST_INTERVAL_NS
            )
            or (
                self._last_started_monotonic_ns is not None
                and monotonic_now - self._last_started_monotonic_ns
                < _MINIMUM_REQUEST_INTERVAL_NS
            )
        ):
            _fail("sportradar_pacing_failed")

        session_attempt = self._session_attempts + 1
        access_attempt = self._access_attempts + 1
        row = {
            "schema": "inci-sportradar-trial-usage-v1",
            "kind": "attempt",
            "session_id": self._session_id,
            "session_attempt": session_attempt,
            "access_attempt": access_attempt,
            "route": route,
            "started_wall_ns": wall_now,
        }
        payload = (
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        try:
            written = os.write(self._usage_fd, payload)
            if written != len(payload):
                _fail("sportradar_usage_ledger_write_failed")
            os.fsync(self._usage_fd)
        except OSError:
            _fail("sportradar_usage_ledger_write_failed")
        self._session_attempts = session_attempt
        self._access_attempts = access_attempt
        self._last_started_wall_ns = wall_now
        self._last_started_monotonic_ns = monotonic_now
        reservation = TrialAttemptReservation(
            session_id=self._session_id,
            session_attempt=session_attempt,
            access_attempt=access_attempt,
            route=route,
            started_wall_ns=wall_now,
        )
        self._reservations[access_attempt] = reservation
        return reservation

    def persist_raw(
        self,
        reservation: TrialAttemptReservation,
        payload: bytes,
        *,
        captured_wall_ns: int | None = None,
    ) -> Path:
        if self._closed:
            _fail("sportradar_usage_ledger_closed")
        if (
            type(reservation) is not TrialAttemptReservation
            or self._reservations.get(reservation.access_attempt)
            != reservation
            or type(payload) is not bytes
            or len(payload) > _MAXIMUM_BODY_BYTES
            or type(captured_wall_ns) is not int
            or captured_wall_ns <= 0
        ):
            _fail("sportradar_capture_invalid")
        path = self.raw_root / (
            f"{reservation.access_attempt:04d}_{reservation.route}.json"
        )
        descriptor = _secure_open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        )
        try:
            written = os.write(descriptor, payload)
            if written != len(payload):
                _fail("sportradar_capture_write_failed")
            os.fsync(descriptor)
        except OSError:
            _fail("sportradar_capture_write_failed")
        finally:
            os.close(descriptor)
        _fsync_directory(self.raw_root, "sportradar_capture_write_failed")
        self._captures[reservation.access_attempt] = (
            path,
            sha256(payload).hexdigest(),
            captured_wall_ns,
        )
        return path

    def record_attempt_outcome(
        self,
        reservation: TrialAttemptReservation,
        *,
        outcome: str,
        code: str,
    ) -> None:
        if self._closed:
            _fail("sportradar_usage_ledger_closed")
        if (
            type(reservation) is not TrialAttemptReservation
            or self._reservations.get(reservation.access_attempt)
            != reservation
            or not _allowed_text(outcome, {"captured", "failed"})
            or not _safe_code(code)
            or (outcome == "captured" and code not in _CAPTURE_OUTCOME_CODES)
            or (
                outcome == "failed"
                and (
                    code in _CAPTURE_OUTCOME_CODES
                    or code == _UNCERTAIN_OUTCOME_CODE
                )
            )
            or self._session_terminal_recorded
            or reservation.access_attempt in self._recorded_outcomes
        ):
            _fail("sportradar_outcome_record_invalid")
        capture = (
            self._captures.get(reservation.access_attempt)
            if type(reservation) is TrialAttemptReservation
            else None
        )
        if (outcome == "captured") != (capture is not None):
            _fail("sportradar_outcome_record_invalid")
        raw_file: str | None = None
        digest: str | None = None
        captured_wall_ns: int | None = None
        if capture is not None:
            raw_path, digest, captured_wall_ns = capture
            raw_file = raw_path.name
        row = {
            "schema": "inci-sportradar-trial-outcome-v1",
            "session_id": reservation.session_id,
            "session_attempt": reservation.session_attempt,
            "access_attempt": reservation.access_attempt,
            "route": reservation.route,
            "outcome": outcome,
            "code": code,
            "captured_wall_ns": captured_wall_ns,
            "payload_sha256": digest,
            "raw_file": raw_file,
        }
        self._append_record(
            self._outcomes_fd,
            row,
            "sportradar_outcome_write_failed",
        )
        self._recorded_outcomes.add(reservation.access_attempt)

    def record_interrupted_outcome(
        self,
        reservation: TrialAttemptReservation,
    ) -> None:
        if (
            type(reservation) is not TrialAttemptReservation
            or self._reservations.get(reservation.access_attempt)
            != reservation
            or self._session_terminal_recorded
        ):
            _fail("sportradar_outcome_record_invalid")
        if reservation.access_attempt in self._recorded_outcomes:
            return
        rows = self._validate_audit_file(self._outcomes_fd, "outcomes")
        if any(
            row["access_attempt"] == reservation.access_attempt
            for row in rows
        ):
            self._validate_outcome_history(rows)
            self._recorded_outcomes.add(reservation.access_attempt)
            return
        captured = reservation.access_attempt in self._captures
        if captured:
            self.record_attempt_outcome(
                reservation,
                outcome="captured",
                code="sportradar_capture_persisted_interrupted",
            )
            return
        raw = self._unexpected_raw_capture(reservation)
        if raw is None:
            self.record_attempt_outcome(
                reservation,
                outcome="failed",
                code="sportradar_operator_interrupt_during_request",
            )
            return
        self._record_uncertain_raw(reservation, *raw)

    def record_failed_or_uncertain_outcome(
        self,
        reservation: TrialAttemptReservation,
        *,
        code: str,
    ) -> None:
        if (
            type(reservation) is not TrialAttemptReservation
            or self._reservations.get(reservation.access_attempt)
            != reservation
            or self._session_terminal_recorded
            or not _safe_code(code)
        ):
            _fail("sportradar_outcome_record_invalid")
        raw = self._unexpected_raw_capture(reservation)
        if raw is None:
            self.record_attempt_outcome(
                reservation,
                outcome="failed",
                code=code,
            )
            return
        self._record_uncertain_raw(reservation, *raw)

    def _unexpected_raw_capture(
        self,
        reservation: TrialAttemptReservation,
    ) -> tuple[str, str] | None:
        raw_file = f"{reservation.access_attempt:04d}_{reservation.route}.json"
        try:
            (self.raw_root / raw_file).lstat()
        except FileNotFoundError:
            return None
        except OSError:
            _fail("sportradar_state_unavailable")
        return raw_file, self._raw_digest(raw_file)

    def _record_uncertain_raw(
        self,
        reservation: TrialAttemptReservation,
        raw_file: str,
        digest: str,
    ) -> None:
        if (
            type(reservation) is not TrialAttemptReservation
            or self._reservations.get(reservation.access_attempt)
            != reservation
            or reservation.access_attempt in self._recorded_outcomes
            or self._session_terminal_recorded
            or raw_file
            != f"{reservation.access_attempt:04d}_{reservation.route}.json"
            or type(digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            _fail("sportradar_outcome_record_invalid")
        self._append_record(
            self._outcomes_fd,
            {
                "schema": "inci-sportradar-trial-outcome-v1",
                "session_id": reservation.session_id,
                "session_attempt": reservation.session_attempt,
                "access_attempt": reservation.access_attempt,
                "route": reservation.route,
                "outcome": "uncertain",
                "code": _UNCERTAIN_OUTCOME_CODE,
                "captured_wall_ns": None,
                "payload_sha256": digest,
                "raw_file": raw_file,
            },
            "sportradar_outcome_write_failed",
        )
        self._recorded_outcomes.add(reservation.access_attempt)

    def record_parser_failure(
        self,
        *,
        command: str,
        reservation: TrialAttemptReservation,
        code: str,
    ) -> None:
        if self._closed:
            _fail("sportradar_usage_ledger_closed")
        capture = (
            self._captures.get(reservation.access_attempt)
            if type(reservation) is TrialAttemptReservation
            else None
        )
        if (
            not _allowed_text(command, _COMMANDS)
            or type(reservation) is not TrialAttemptReservation
            or self._reservations.get(reservation.access_attempt)
            != reservation
            or capture is None
            or reservation.access_attempt not in self._recorded_outcomes
            or reservation.access_attempt in self._recorded_observations
            or self._session_terminal_recorded
            or not _safe_code(code)
        ):
            _fail("sportradar_parser_failure_record_invalid")
        raw_path, digest, _ = capture
        row = {
            "schema": "inci-sportradar-trial-parser-failure-v1",
            "command": command,
            "session_id": reservation.session_id,
            "session_attempt": reservation.session_attempt,
            "access_attempt": reservation.access_attempt,
            "route": reservation.route,
            "payload_sha256": digest,
            "raw_file": raw_path.name,
            "code": code,
        }
        self._append_record(
            self._observations_fd,
            row,
            "sportradar_observation_write_failed",
        )
        self._recorded_observations.add(reservation.access_attempt)

    def record_session_terminal(
        self,
        *,
        command: str,
        provider_match_id: str | None,
        reason: str,
        code: str | None = None,
    ) -> None:
        ended_wall_ns = self._wall_ns()
        if (
            self._closed
            or self._session_terminal_recorded
            or not _allowed_text(command, _COMMANDS)
            or not _allowed_text(reason, _TERMINAL_REASONS)
            or (
                reason == "halted"
                and not _safe_code(code)
            )
            or (
                reason == "operator_interrupt"
                and code != "sportradar_operator_interrupt"
            )
            or (
                reason == "cancelled"
                and code not in {None, _SHADOW_TASK_CANCELLED_CODE}
            )
            or (
                code == _SHADOW_TASK_CANCELLED_CODE
                and command != "shadow"
            )
            or (
                reason not in {"halted", "operator_interrupt", "cancelled"}
                and code is not None
            )
            or type(ended_wall_ns) is not int
            or ended_wall_ns <= 0
            or (
                provider_match_id is not None
                and (
                    type(provider_match_id) is not str
                    or re.fullmatch(
                        _MATCH_ID,
                        provider_match_id,
                        flags=re.ASCII,
                    )
                    is None
                )
            )
        ):
            _fail("sportradar_terminal_record_invalid")
        row = {
            "schema": "inci-sportradar-trial-terminal-v1",
            "command": command,
            "session_id": self._session_id,
            "provider_match_id": provider_match_id,
            "reason": reason,
            "code": code,
            "ended_wall_ns": ended_wall_ns,
            "session_attempts": self._session_attempts,
            "access_attempts": self._access_attempts,
        }
        self._append_record(
            self._observations_fd,
            row,
            "sportradar_observation_write_failed",
        )
        self._session_terminal_recorded = True

    def record_observation(self, record: TrialObservationRecord) -> None:
        if self._closed:
            _fail("sportradar_usage_ledger_closed")
        if (
            type(record) is not TrialObservationRecord
            or type(record.reservation) is not TrialAttemptReservation
            or not _allowed_text(record.command, _COMMANDS)
            or self._reservations.get(record.reservation.access_attempt)
            != record.reservation
            or (
                record.provider_match_id is not None
                and (
                    type(record.provider_match_id) is not str
                    or re.fullmatch(
                        _MATCH_ID,
                        record.provider_match_id,
                        flags=re.ASCII,
                    )
                    is None
                )
            )
            or type(record.generated_wall_ns) is not int
            or record.generated_wall_ns <= 0
            or type(record.captured_wall_ns) is not int
            or record.captured_wall_ns <= 0
            or type(record.status) is not str
            or not record.status
            or len(record.status) > 64
            or (
                record.match_status is not None
                and (
                    type(record.match_status) is not str
                    or not record.match_status
                    or len(record.match_status) > 64
                )
            )
            or type(record.payload_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", record.payload_sha256) is None
            or self._captures.get(record.reservation.access_attempt)
            != (
                record.raw_path,
                record.payload_sha256,
                record.captured_wall_ns,
            )
            or record.reservation.access_attempt
            not in self._recorded_outcomes
            or record.reservation.access_attempt
            in self._recorded_observations
            or self._session_terminal_recorded
            or type(record.progression) is not str
            or not record.progression
            or len(record.progression) > 64
            or (
                record.last_event_id is not None
                and (
                    type(record.last_event_id) is not int
                    or record.last_event_id <= 0
                )
            )
            or (
                record.terminal_reason is not None
                and (
                    type(record.terminal_reason) is not str
                    or not record.terminal_reason
                    or len(record.terminal_reason) > 64
                )
            )
        ):
            _fail("sportradar_observation_record_invalid")
        row = {
            "schema": "inci-sportradar-trial-observation-v1",
            "command": record.command,
            "session_id": record.reservation.session_id,
            "session_attempt": record.reservation.session_attempt,
            "access_attempt": record.reservation.access_attempt,
            "route": record.reservation.route,
            "provider_match_id": record.provider_match_id,
            "generated_wall_ns": record.generated_wall_ns,
            "captured_wall_ns": record.captured_wall_ns,
            "status": record.status,
            "match_status": record.match_status,
            "payload_sha256": record.payload_sha256,
            "raw_file": record.raw_path.name,
            "progression": record.progression,
            "last_event_id": record.last_event_id,
            "terminal_reason": record.terminal_reason,
        }
        self._append_record(
            self._observations_fd,
            row,
            "sportradar_observation_write_failed",
        )
        self._recorded_observations.add(record.reservation.access_attempt)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        usage_fd = getattr(self, "_usage_fd", None)
        if type(usage_fd) is int:
            os.close(usage_fd)
        observations_fd = getattr(self, "_observations_fd", None)
        if type(observations_fd) is int:
            os.close(observations_fd)
        outcomes_fd = getattr(self, "_outcomes_fd", None)
        if type(outcomes_fd) is int:
            os.close(outcomes_fd)
        lock_fd = getattr(self, "_lock_fd", None)
        if type(lock_fd) is int:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

    def __enter__(self) -> TrialUsageLedger:
        if self._closed:
            _fail("sportradar_usage_ledger_closed")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _header(headers: object, name: str) -> str | None:
    try:
        value = headers.get(name)  # type: ignore[union-attr]
        if value is None:
            value = headers.get(name.lower())  # type: ignore[union-attr]
    except (AttributeError, TypeError):
        _fail("sportradar_response_headers_invalid")
    if value is None:
        return None
    if type(value) is not str or len(value) > 512:
        _fail("sportradar_response_headers_invalid")
    return value


class SportradarTrialTransport:
    """GET-only transport for trial observation; it has no trading authority."""

    def __init__(
        self,
        *,
        api_key: str,
        ledger: TrialUsageLedger,
        wall_ns: Callable[[], int] = time.time_ns,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if (
            type(api_key) is not str
            or not api_key
            or len(api_key) > 512
            or api_key != api_key.strip()
            or any(
                ord(character) < 33 or ord(character) > 126
                for character in api_key
            )
        ):
            _fail("sportradar_api_key_invalid")
        if type(ledger) is not TrialUsageLedger:
            _fail("sportradar_usage_ledger_invalid")
        self._api_key = api_key
        self._ledger = ledger
        self._wall_ns = wall_ns
        self._monotonic_ns = monotonic_ns
        try:
            self._session = requests.Session()
            self._session.trust_env = False
        except Exception:
            _fail("sportradar_transport_unavailable")
        self._closed = False

    def __repr__(self) -> str:
        return (
            "SportradarTrialTransport("
            f"ledger={self._ledger!r}, api_key=<redacted>)"
        )

    def fetch_live_summaries(self) -> TrialCapture:
        return self._get(
            "live_summaries",
            "/tennis/trial/v3/en/schedules/live/summaries.json",
        )

    def fetch_summary(self, match_id: str) -> TrialCapture:
        match = self._match_id(match_id)
        return self._get(
            "summary",
            f"/tennis/trial/v3/en/sport_events/{match}/summary.json",
        )

    def fetch_timeline(self, match_id: str) -> TrialCapture:
        match = self._match_id(match_id)
        return self._get(
            "timeline",
            f"/tennis/trial/v3/en/sport_events/{match}/timeline.json",
        )

    @staticmethod
    def _match_id(value: str) -> str:
        if (
            type(value) is not str
            or re.fullmatch(_MATCH_ID, value, flags=re.ASCII) is None
        ):
            _fail("sportradar_match_identifier_invalid")
        return value

    def _get(self, route: str, path: str) -> TrialCapture:
        if self._closed:
            _fail("sportradar_transport_closed")
        reservation = self._ledger.reserve(route)
        started_monotonic_ns = self._monotonic_ns()
        response: _Response | None = None
        capture_persisted = False
        try:
            if type(started_monotonic_ns) is not int or started_monotonic_ns < 0:
                _fail("sportradar_clock_invalid")
            with _HardDeadline():
                response = self._session.get(
                    _ORIGIN + path,
                    headers={
                        "accept": "application/json",
                        "accept-encoding": "identity",
                        "x-api-key": self._api_key,
                    },
                    allow_redirects=False,
                    stream=True,
                    timeout=(3, 10),
                )
                try:
                    self._enforce_deadline(started_monotonic_ns)
                    status = response.status_code
                    if type(status) is not int or status != 200:
                        if type(status) is int and 100 <= status <= 599:
                            _fail(f"sportradar_http_status_{status}")
                        _fail("sportradar_http_status_invalid")
                    content_type = _header(response.headers, "Content-Type")
                    if (
                        content_type is None
                        or content_type.split(";", 1)[0].strip().lower()
                        != "application/json"
                    ):
                        _fail("sportradar_content_type_invalid")
                    content_encoding = _header(
                        response.headers,
                        "Content-Encoding",
                    )
                    if (
                        content_encoding is not None
                        and content_encoding.strip().lower()
                        not in {"", "identity"}
                    ):
                        _fail("sportradar_content_encoding_invalid")
                    content_length = _header(
                        response.headers,
                        "Content-Length",
                    )
                    if content_length is not None:
                        if (
                            not content_length.isascii()
                            or not content_length.isdecimal()
                        ):
                            _fail("sportradar_content_length_invalid")
                        if int(content_length) > _MAXIMUM_BODY_BYTES:
                            _fail("sportradar_body_too_large")
                    chunks: list[bytes] = []
                    size = 0
                    try:
                        iterable = response.iter_content(chunk_size=65_536)
                        for chunk in iterable:  # type: ignore[union-attr]
                            self._enforce_deadline(started_monotonic_ns)
                            if type(chunk) is not bytes:
                                _fail("sportradar_response_body_invalid")
                            size += len(chunk)
                            if size > _MAXIMUM_BODY_BYTES:
                                _fail("sportradar_body_too_large")
                            chunks.append(chunk)
                    except SportradarTrialObserverError:
                        raise
                    except Exception:
                        _fail("sportradar_response_body_unavailable")
                    payload = b"".join(chunks)
                    self._enforce_deadline(started_monotonic_ns)
                finally:
                    closing = response
                    response = None
                    try:
                        closing.close()
                    except SportradarTrialObserverError:
                        raise
                    except Exception:
                        pass
            if content_length is not None and len(payload) != int(content_length):
                _fail("sportradar_content_length_mismatch")
            persistence_start_ns = self._monotonic_ns()
            if (
                type(persistence_start_ns) is not int
                or persistence_start_ns < started_monotonic_ns
            ):
                _fail("sportradar_clock_invalid")
            elapsed_ns = persistence_start_ns - started_monotonic_ns
            if elapsed_ns >= _TOTAL_DEADLINE_NS:
                _fail("sportradar_total_deadline")
            if (
                self._api_key.encode("utf-8") in payload
                or _decoded_contains_secret(payload, self._api_key)
                or _escaped_contains_secret(payload, self._api_key)
            ):
                _fail("sportradar_credential_reflected")
            captured_wall_ns = self._wall_ns()
            if type(captured_wall_ns) is not int or captured_wall_ns <= 0:
                _fail("sportradar_clock_invalid")
            raw_path = self._ledger.persist_raw(
                reservation,
                payload,
                captured_wall_ns=captured_wall_ns,
            )
            capture_persisted = True
            self._ledger.record_attempt_outcome(
                reservation,
                outcome="captured",
                code="sportradar_capture_persisted",
            )
            self._enforce_deadline(started_monotonic_ns)
            return TrialCapture(
                reservation=reservation,
                captured_wall_ns=captured_wall_ns,
                raw_path=raw_path,
                payload=payload,
            )
        except KeyboardInterrupt:
            self._ledger.record_interrupted_outcome(reservation)
            raise
        except SportradarTrialObserverError as error:
            if not capture_persisted:
                self._ledger.record_failed_or_uncertain_outcome(
                    reservation,
                    code=error.code,
                )
            raise
        except Exception:
            if not capture_persisted:
                self._ledger.record_failed_or_uncertain_outcome(
                    reservation,
                    code="sportradar_transport_unavailable",
                )
            _fail("sportradar_transport_unavailable")
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def _enforce_deadline(self, started_monotonic_ns: int) -> None:
        current = self._monotonic_ns()
        if (
            type(current) is not int
            or current < started_monotonic_ns
            or current - started_monotonic_ns > _TOTAL_DEADLINE_NS
        ):
            _fail("sportradar_total_deadline")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._session.close()
        except Exception:
            pass

    def __enter__(self) -> SportradarTrialTransport:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = (
    "SportradarTrialObserverError",
    "SportradarTrialTransport",
    "TrialAttemptReservation",
    "TrialCapture",
    "TrialObservationRecord",
    "TrialUsageLedger",
    "default_trial_state_root",
    "load_trial_api_key",
    "trial_monotonic_ns",
    "trial_sleep",
)
