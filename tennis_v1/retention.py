"""Descriptor-owned physical retention authority for provider WAL bytes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, fields
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import threading
from typing import Protocol
import uuid
import weakref

from .canonical import CanonicalJsonError, canonical_json_bytes
from .codec import RecordCodecError, decode_record
from .config import TennisV1Config
from .entitlements import (
    QualificationDecision,
    QualifiedProviderBinding,
    provider_request_binding_sha256,
)
from .events import RecordKind, SessionManifest
from .session import (
    canonical_session_manifest_bytes,
    require_decision_matches_session,
    session_manifest_sha256,
)


RESERVE_BYTES = 1024 * 1024
MIN_FREE_BYTES = 64 * 1024 * 1024
MARKER_MAX_BYTES = 64 * 1024
WAL_FILE_PREFIX = struct.Struct(">8sHHI")
WAL_FILE_PREFIX_BYTES = WAL_FILE_PREFIX.pack(
    b"INCIWAL\x00",
    1,
    0,
    WAL_FILE_PREFIX.size,
)
FRAME_PREFIX = struct.Struct(">4sBBHQQII")
FRAME_TRAILER = struct.Struct(">Q4s")
FRAME_MAGIC = b"EVT1"
FRAME_VERSION = 1
FRAME_FLAGS = 0
FRAME_CONTROL_KIND = 3
TRAILER_MAGIC = b"1TVE"
MAX_FRAME_BYTES = 16 * 1024 * 1024
FRAME_DIGEST_DOMAIN = b"INCI-FRAME-V1\0"
_FSTORE = struct.Struct("@Iiqqq")
_F_PREALLOCATE = 42
_F_ALLOCATEALL = 0x4
_F_ALLOCATEPERSIST = 0x8
_F_PEOFPOSMODE = 3
_SESSION_START_FRAME = object()
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MARKER_NAME = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"\.marker\.json\Z"
)
_WAL_NAME = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"\.wal\Z"
)
_RESERVE_NAME = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"\.reserve\Z"
)
_TERMINAL_KEYS = frozenset(
    {
        "terminal_version",
        "clean",
        "reason",
        "trace_sha256",
        "final_state_sha256",
        "record_count_before_terminal",
        "raw_count",
        "derived_count",
        "last_applied_raw_seq",
        "config_file_sha256",
        "config_canonical_sha256",
        "code_sha256",
        "session_manifest_sha256",
        "provider_manifest_file_sha256",
        "provider_manifest_canonical_sha256",
        "entitlement_id_sha256",
        "permission_artifact_sha256",
        "qualification_artifact_sha256",
        "qualification_trace_sha256",
        "adapter_code_sha256",
        "auth_contract_sha256",
        "quota_contract_sha256",
        "required_retention_until_ns",
        "research_evaluable",
    }
)
_CLEAN_TERMINAL_REASONS = frozenset({"operator_stop", "session_end"})
_HALTED_TERMINAL_REASONS = frozenset(
    {
        "operator_halt",
        "initialization_failure",
        "capture_contract_violation",
        "provider_gate_denied",
        "retention_global_halt",
        "disk_low",
        "reducer_exception",
        "derived_validation_failure",
        "trace_exception",
        "ingress_backpressure",
        "ingress_owner_unresponsive",
    }
)
_OPEN_SUPPORTS_DIRFD = os.open in os.supports_dir_fd
_UNLINK_SUPPORTS_DIRFD = os.unlink in os.supports_dir_fd
_STAT_SUPPORTS_DIRFD = os.stat in os.supports_dir_fd


class RetentionError(RuntimeError):
    """Raised when provider storage authority cannot be established safely."""


class RetentionDueDeleteError(RetentionError):
    """Raised to the operation that encounters an expiry/delete failure."""


class RetentionGlobalHalt(RetentionError):
    """Raised after the process-wide retention halt latch is set."""


class RetentionPrewriteCapacityError(RetentionError):
    """Raised when a WAL frame cannot begin without consuming halt reserve."""


@dataclass(frozen=True, slots=True)
class RetentionMarker:
    schema_version: int
    session_id: str
    wal_basename: str
    reserve_basename: str
    delete_by_ns: int
    session_manifest_sha256: str
    provider_request_binding_sha256: str
    provider_manifest_file_sha256: str
    entitlement_id_sha256: str
    qualification_artifact_sha256: str
    created_at_ns: int

    def __post_init__(self) -> None:
        if type(self) is not RetentionMarker:
            raise TypeError("exact RetentionMarker required")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise RetentionError("retention_marker_schema_invalid")
        _require_session_id(self.session_id)
        if self.wal_basename != _wal_basename(self.session_id):
            raise RetentionError("retention_marker_wal_binding_invalid")
        if self.reserve_basename != _reserve_basename(self.session_id):
            raise RetentionError("retention_marker_reserve_binding_invalid")
        for name in ("delete_by_ns", "created_at_ns"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise RetentionError("retention_marker_timestamp_invalid")
        for name in (
            "session_manifest_sha256",
            "provider_request_binding_sha256",
            "provider_manifest_file_sha256",
            "entitlement_id_sha256",
            "qualification_artifact_sha256",
        ):
            value = getattr(self, name)
            if type(value) is not str or _SHA256.fullmatch(value) is None:
                raise RetentionError("retention_marker_digest_invalid")


@dataclass(frozen=True, slots=True)
class PurgeReport:
    deleted_sessions: tuple[str, ...]
    recovered_markers: tuple[str, ...]


class RetentionSessionAuthorizer(Protocol):
    @property
    def coordinator(self) -> "RetentionCoordinator": ...

    @property
    def session_manifest(self) -> SessionManifest: ...

    @property
    def bound_decision(self) -> QualificationDecision: ...

    def authorize_session(self) -> None: ...

    def authorize_raw_persistence(self) -> int: ...

    def authorize_analysis(self) -> QualificationDecision: ...

    def authorize_close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    links: int
    owner: int


@dataclass(slots=True)
class _CapabilityLifecycle:
    due: bool = False


@dataclass(
    frozen=True, slots=True, weakref_slot=True, init=False, eq=False
)
class ExpertStateRootAccountLockRequestV1:
    """Opaque one-shot request for the already validated state-root lock."""

    _dispatch: "RetentionCoordinator" = field(repr=False, compare=False)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("expert state-root requests are coordinator-issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("expert state-root requests cannot be subclassed")

    def __repr__(self) -> str:
        return "<ExpertStateRootAccountLockRequestV1 redacted>"

    def __copy__(self):
        raise TypeError("expert state-root requests cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("expert state-root requests cannot be copied")

    def __reduce__(self):
        raise TypeError("expert state-root requests cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("expert state-root requests cannot be pickled")

    def __getstate__(self):
        raise TypeError("expert state-root requests cannot be pickled")


@dataclass(
    frozen=True, slots=True, weakref_slot=True, init=False, eq=False
)
class ExpertRetentionClockSampleCapabilityV1:
    """Opaque reusable sampler bound to one live expert root grant."""

    _dispatch: "RetentionCoordinator" = field(repr=False, compare=False)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError(
            "expert retention-clock capabilities are coordinator-issued"
        )

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError(
            "expert retention-clock capabilities cannot be subclassed"
        )

    def __repr__(self) -> str:
        return "<ExpertRetentionClockSampleCapabilityV1 redacted>"

    def __copy__(self):
        raise TypeError(
            "expert retention-clock capabilities cannot be copied"
        )

    def __deepcopy__(self, _: object):
        raise TypeError(
            "expert retention-clock capabilities cannot be copied"
        )

    def __reduce__(self):
        raise TypeError(
            "expert retention-clock capabilities cannot be pickled"
        )

    def __reduce_ex__(self, _: int):
        raise TypeError(
            "expert retention-clock capabilities cannot be pickled"
        )

    def __getstate__(self):
        raise TypeError(
            "expert retention-clock capabilities cannot be pickled"
        )


@dataclass(
    frozen=True, slots=True, weakref_slot=True, init=False, eq=False
)
class _ExpertStateRootAccountLockGrantV1:
    _dispatch: "RetentionCoordinator" = field(repr=False, compare=False)
    _state_fd: int = field(repr=False, compare=False)
    _sessions_fd: int = field(repr=False, compare=False)
    _markers_fd: int = field(repr=False, compare=False)
    _lock_fd: int = field(repr=False, compare=False)
    _clock_capability: ExpertRetentionClockSampleCapabilityV1 = field(
        repr=False,
        compare=False,
    )

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("expert state-root grants are coordinator-issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("expert state-root grants cannot be subclassed")

    def __repr__(self) -> str:
        return "<_ExpertStateRootAccountLockGrantV1 redacted>"

    def __copy__(self):
        raise TypeError("expert state-root grants cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("expert state-root grants cannot be copied")

    def __reduce__(self):
        raise TypeError("expert state-root grants cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("expert state-root grants cannot be pickled")

    def __getstate__(self):
        raise TypeError("expert state-root grants cannot be pickled")


@dataclass(slots=True)
class _ExpertRootRequestAuthority:
    request: ExpertStateRootAccountLockRequestV1
    coordinator: "RetentionCoordinator"
    owner_pid: int
    owner_thread: threading.Thread
    generation: int


@dataclass(slots=True)
class _ExpertRootGrantAuthority:
    grant: _ExpertStateRootAccountLockGrantV1
    coordinator: "RetentionCoordinator"
    owner_pid: int
    owner_thread: threading.Thread
    generation: int
    state_fd: int
    sessions_fd: int
    markers_fd: int
    lock_fd: int
    state_identity: _FileIdentity
    sessions_identity: _FileIdentity
    markers_identity: _FileIdentity
    lock_identity: _FileIdentity
    clock_capability: ExpertRetentionClockSampleCapabilityV1


@dataclass(frozen=True, slots=True)
class _DirectoryTransitionObservation:
    identity: _FileIdentity
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True, slots=True)
class _ExpertArmRootIdentityTransition:
    authority: _ExpertRootGrantAuthority
    sessions: _DirectoryTransitionObservation
    markers: _DirectoryTransitionObservation
    session_entries: tuple[str, ...]
    marker_entries: tuple[str, ...]


@dataclass(slots=True)
class _SessionState:
    marker: RetentionMarker
    wal_identity: _FileIdentity
    reserve_identity: _FileIdentity | None
    capability_lifecycle: _CapabilityLifecycle = field(
        default_factory=_CapabilityLifecycle
    )
    wal_fd: int = -1
    reserve_fd: int = -1
    manifest: SessionManifest | None = None
    decision: QualificationDecision | None = None
    authorizer: RetentionSessionAuthorizer | None = None
    write_token: object | None = None
    terminal_clean_durable: bool = False
    terminal_written: bool = False
    wal_prefix_durable: bool = False
    session_start_durable: bool = False
    healthy: bool = True
    clean_preclose_ack: "_CleanTerminalAck | None" = None
    clean_terminal_marked: bool = False


@dataclass(slots=True)
class _WriteAuthority:
    capability: "ProviderWalWriteCapability"
    coordinator: "RetentionCoordinator"
    token: object
    session_id: str
    manifest_sha256: str
    binding_sha256: str
    manifest: SessionManifest
    decision: QualificationDecision
    authorizer: RetentionSessionAuthorizer
    owner_pid: int
    owner_thread: threading.Thread
    generation: int
    lifecycle: _CapabilityLifecycle
    halt_consumed: bool = False
    writer_claimed: bool = False
    runtime_claimed: bool = False
    preobserved_global_halt: bool = False


@dataclass(slots=True)
class _ReadAuthority:
    capability: "ProviderWalReadCapability"
    coordinator: "RetentionCoordinator"
    token: object
    session_id: str
    manifest_sha256: str
    binding_sha256: str
    manifest: SessionManifest
    decision: QualificationDecision
    authorizer: RetentionSessionAuthorizer
    owner_pid: int
    owner_thread: threading.Thread
    generation: int
    fd: int
    wal_identity: _FileIdentity
    lifecycle: _CapabilityLifecycle
    reader_claimed: bool = False


@dataclass(slots=True)
class _ReadTombstone:
    coordinator: "RetentionCoordinator"
    session_id: str
    manifest_sha256: str
    binding_sha256: str
    manifest: SessionManifest
    decision: QualificationDecision
    authorizer: RetentionSessionAuthorizer
    owner_pid: int
    owner_thread: threading.Thread
    generation: int
    lifecycle: _CapabilityLifecycle


@dataclass(frozen=True, slots=True)
class _GlobalHaltState:
    source: weakref.ReferenceType["RetentionCoordinator"] | None
    scoped_session_id: str | None
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class _CleanTerminalAck:
    marker: RetentionMarker
    wal_identity: _FileIdentity
    wal_size: int
    manifest_sha256: str
    binding_sha256: str


_PROVIDER_IO_LOCK = threading.RLock()
_GLOBAL_HALT_LOCK = threading.Lock()
_PROCESS_HALT: _GlobalHaltState | None = None
_ACTIVE_COORDINATORS: weakref.WeakSet["RetentionCoordinator"] = weakref.WeakSet()


def _global_halt() -> _GlobalHaltState | None:
    with _GLOBAL_HALT_LOCK:
        return _PROCESS_HALT


def _raise_if_global_halt() -> None:
    if _global_halt() is not None:
        raise RetentionGlobalHalt("retention_global_halt")


def _latch_global_halt(
    coordinator: "RetentionCoordinator | None",
    *,
    session_id: str | None,
    ambiguous: bool,
) -> None:
    global _PROCESS_HALT
    active: tuple[RetentionCoordinator, ...] = ()
    with _PROVIDER_IO_LOCK:
        with _GLOBAL_HALT_LOCK:
            if _PROCESS_HALT is None:
                _PROCESS_HALT = _GlobalHaltState(
                    None if coordinator is None else weakref.ref(coordinator),
                    session_id,
                    ambiguous,
                )
                active = tuple(_ACTIVE_COORDINATORS)
    for item in active:
        item._revoke_reads_for_global_halt()


def _require_posix_features() -> None:
    required = ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC")
    if (
        os.name != "posix"
        or any(not hasattr(os, name) for name in required)
        or not hasattr(os, "pread")
        or not hasattr(os, "geteuid")
        or not hasattr(fcntl, "flock")
        or not _OPEN_SUPPORTS_DIRFD
        or not _UNLINK_SUPPORTS_DIRFD
        or not _STAT_SUPPORTS_DIRFD
    ):
        raise RetentionError("retention_platform_unsupported")


def _require_session_id(value: object) -> str:
    if type(value) is not str:
        raise RetentionError("retention_session_id_invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise RetentionError("retention_session_id_invalid") from None
    if str(parsed) != value:
        raise RetentionError("retention_session_id_invalid")
    return value


def _wal_basename(session_id: str) -> str:
    return f"{_require_session_id(session_id)}.wal"


def _reserve_basename(session_id: str) -> str:
    return f"{_require_session_id(session_id)}.reserve"


def _marker_basename(session_id: str) -> str:
    return f"{_require_session_id(session_id)}.marker.json"


def _mode(value: os.stat_result) -> int:
    return stat.S_IMODE(value.st_mode)


def _validate_directory(fd: int) -> None:
    try:
        value = os.fstat(fd)
    except OSError as error:
        raise RetentionError("retention_directory_stat_failed") from error
    _validate_directory_stat(value)


def _validate_directory_stat(value: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(value.st_mode)
        or value.st_uid != os.geteuid()
        or _mode(value) != 0o700
    ):
        raise RetentionError("retention_private_directory_invalid")


def _file_identity(value: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
    )


def _same_mutable_directory_binding(
    current: _FileIdentity,
    expected: _FileIdentity,
) -> bool:
    return (
        current.device == expected.device
        and current.inode == expected.inode
        and current.mode == expected.mode
        and current.owner == expected.owner
    )


def _validate_file_stat(
    value: os.stat_result,
    *,
    exact_size: int | None = None,
    physical: bool = False,
) -> _FileIdentity:
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_nlink != 1
        or value.st_uid != os.geteuid()
        or _mode(value) != 0o600
    ):
        raise RetentionError("retention_private_file_invalid")
    if exact_size is not None and value.st_size != exact_size:
        raise RetentionError("retention_private_file_size_invalid")
    if physical and (
        not hasattr(value, "st_blocks")
        or value.st_blocks * 512 < RESERVE_BYTES
    ):
        raise RetentionError("retention_reserve_not_physical")
    return _file_identity(value)


def _validate_named_fd(
    fd: int,
    name: str,
    directory_fd: int,
    *,
    exact_size: int | None = None,
    physical: bool = False,
) -> _FileIdentity:
    try:
        descriptor = os.fstat(fd)
        expected = _validate_file_stat(
            descriptor, exact_size=exact_size, physical=physical
        )
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as error:
        raise RetentionError("retention_tuple_stat_failed") from error
    actual = _validate_file_stat(
        named, exact_size=exact_size, physical=physical
    )
    if actual != expected:
        raise RetentionError("retention_tuple_substituted")
    return expected


def _open_existing_file(
    directory_fd: int,
    name: str,
    *,
    writable: bool,
    append: bool = False,
) -> tuple[int, _FileIdentity]:
    flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_CLOEXEC | os.O_NOFOLLOW
    if append:
        flags |= os.O_APPEND
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(name, flags, dir_fd=directory_fd)
        return fd, _validate_named_fd(fd, name, directory_fd)
    except RetentionError:
        if "fd" in locals():
            try:
                os.close(fd)
            except OSError:
                pass
        raise
    except OSError as error:
        if "fd" in locals():
            try:
                os.close(fd)
            except OSError:
                pass
        raise RetentionError("retention_file_open_failed") from error
    except Exception:
        if "fd" in locals():
            try:
                os.close(fd)
            except OSError:
                pass
        raise


def _write_all(fd: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "zero-byte write")
        view = view[written:]


def _close_fd(fd: int) -> None:
    try:
        os.close(fd)
    except OSError as error:
        raise RetentionError("retention_descriptor_close_failed") from error


def _fsync_directory(fd: int) -> None:
    os.fsync(fd)


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RetentionError("retention_json_duplicate_key")
        result[key] = value
    return result


def _reject_float(_: str) -> object:
    raise RetentionError("retention_json_float_forbidden")


def _reject_constant(_: str) -> object:
    raise RetentionError("retention_json_constant_forbidden")


def _strict_json(content: bytes, expected: frozenset[str]) -> dict[str, object]:
    if type(content) is not bytes or content.startswith(b"\xef\xbb\xbf"):
        raise RetentionError("retention_json_invalid")
    try:
        text = content.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_strict_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except RetentionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise RetentionError("retention_json_invalid") from None
    if type(value) is not dict or set(value) != expected:
        raise RetentionError("retention_json_keys_invalid")
    try:
        canonical = canonical_json_bytes(value)
    except CanonicalJsonError as error:
        raise RetentionError("retention_json_value_invalid") from error
    if canonical != content:
        raise RetentionError("retention_json_noncanonical")
    return value


def _marker_projection(marker: RetentionMarker) -> dict[str, object]:
    if type(marker) is not RetentionMarker:
        raise TypeError("exact RetentionMarker required")
    return {
        item.name: getattr(marker, item.name)
        for item in fields(RetentionMarker)
    }


_MARKER_KEYS = frozenset(item.name for item in fields(RetentionMarker))


def _marker_bytes(marker: RetentionMarker) -> bytes:
    return canonical_json_bytes(_marker_projection(marker))


def _read_marker(fd: int) -> tuple[RetentionMarker, bytes]:
    try:
        before = os.fstat(fd)
        _validate_file_stat(before)
        chunks: list[bytes] = []
        remaining = MARKER_MAX_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(16384, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(fd)
    except OSError as error:
        raise RetentionError("retention_marker_read_failed") from error
    if len(content) > MARKER_MAX_BYTES:
        raise RetentionError("retention_marker_too_large")
    if (
        _file_identity(before) != _file_identity(after)
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    ):
        raise RetentionError("retention_marker_changed")
    raw = _strict_json(content, _MARKER_KEYS)
    try:
        marker = RetentionMarker(**raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise RetentionError("retention_marker_contract_invalid") from error
    if _marker_bytes(marker) != content:
        raise RetentionError("retention_marker_noncanonical")
    return marker, content


def _allocate_reserve(fd: int) -> None:
    if hasattr(os, "posix_fallocate"):
        os.posix_fallocate(fd, 0, RESERVE_BYTES)
    elif sys.platform == "darwin" and _FSTORE.size == 32:
        request = _FSTORE.pack(
            _F_ALLOCATEALL | _F_ALLOCATEPERSIST,
            _F_PEOFPOSMODE,
            0,
            RESERVE_BYTES,
            0,
        )
        returned = fcntl.fcntl(fd, _F_PREALLOCATE, request)
        if (
            type(returned) is not bytes
            or len(returned) != _FSTORE.size
            or _FSTORE.unpack(returned)[4] != RESERVE_BYTES
        ):
            raise RetentionError("retention_reserve_allocation_incomplete")
    else:
        raise RetentionError("retention_reserve_allocation_unsupported")
    os.ftruncate(fd, RESERVE_BYTES)
    value = os.fstat(fd)
    _validate_file_stat(value, exact_size=RESERVE_BYTES, physical=True)


def _manifest_matches_marker(
    manifest: SessionManifest,
    decision: QualificationDecision,
    marker: RetentionMarker,
) -> None:
    require_decision_matches_session(decision, manifest)
    if (
        session_manifest_sha256(manifest) != marker.session_manifest_sha256
        or decision.provider_request_binding_sha256
        != marker.provider_request_binding_sha256
        or provider_request_binding_sha256(decision)
        != marker.provider_request_binding_sha256
        or manifest.provider_manifest_file_sha256
        != marker.provider_manifest_file_sha256
        or manifest.entitlement_id_sha256 != marker.entitlement_id_sha256
        or manifest.qualification_artifact_sha256
        != marker.qualification_artifact_sha256
        or manifest.required_retention_until_ns != marker.delete_by_ns
    ):
        raise RetentionError("retention_session_marker_binding_invalid")


@dataclass(
    frozen=True, slots=True, weakref_slot=True, init=False, eq=False
)
class ProviderWalWriteCapability:
    # Dispatch is deliberately non-authoritative. The receiving coordinator
    # accepts this object only when its private registry contains the exact
    # object identity and all private ownership/binding fields still validate.
    _dispatch: "RetentionCoordinator" = field(repr=False, compare=False)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("provider WAL write capabilities are coordinator-issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("provider WAL write capabilities cannot be subclassed")

    def __repr__(self) -> str:
        return "<ProviderWalWriteCapability redacted>"

    def __copy__(self):
        raise TypeError("provider WAL capabilities cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("provider WAL capabilities cannot be copied")

    def __reduce__(self):
        raise TypeError("provider WAL capabilities cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("provider WAL capabilities cannot be pickled")

    def __getstate__(self):
        raise TypeError("provider WAL capabilities cannot be pickled")

    def write_all(self, frame: bytes) -> None:
        self._dispatch._write_capability_bytes(self, frame)

    def write_halt_control(self, frame: bytes) -> None:
        self._dispatch._write_halt_control(self, frame)

    def fsync(self) -> None:
        self._dispatch._fsync_write_capability(self)

    def close(self) -> None:
        self._dispatch._close_write_capability(self)


@dataclass(
    frozen=True, slots=True, weakref_slot=True, init=False, eq=False
)
class ProviderWalReadCapability:
    _dispatch: "RetentionCoordinator" = field(repr=False, compare=False)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("provider WAL read capabilities are coordinator-issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("provider WAL read capabilities cannot be subclassed")

    def __repr__(self) -> str:
        return "<ProviderWalReadCapability redacted>"

    def __copy__(self):
        raise TypeError("provider WAL capabilities cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("provider WAL capabilities cannot be copied")

    def __reduce__(self):
        raise TypeError("provider WAL capabilities cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("provider WAL capabilities cannot be pickled")

    def __getstate__(self):
        raise TypeError("provider WAL capabilities cannot be pickled")

    def pread(self, *, offset: int, length: int) -> bytes:
        return self._dispatch._pread_capability(self, offset=offset, length=length)

    def close(self) -> None:
        self._dispatch._close_read_capability(self)


def _build_write_capability(
    coordinator: "RetentionCoordinator",
) -> ProviderWalWriteCapability:
    item = object.__new__(ProviderWalWriteCapability)
    object.__setattr__(item, "_dispatch", coordinator)
    return item


def _build_read_capability(
    coordinator: "RetentionCoordinator",
) -> ProviderWalReadCapability:
    item = object.__new__(ProviderWalReadCapability)
    object.__setattr__(item, "_dispatch", coordinator)
    return item


def _build_expert_state_root_request(
    coordinator: "RetentionCoordinator",
) -> ExpertStateRootAccountLockRequestV1:
    request = object.__new__(ExpertStateRootAccountLockRequestV1)
    object.__setattr__(request, "_dispatch", coordinator)
    return request


def _build_expert_retention_clock_capability(
    coordinator: "RetentionCoordinator",
) -> ExpertRetentionClockSampleCapabilityV1:
    capability = object.__new__(ExpertRetentionClockSampleCapabilityV1)
    object.__setattr__(capability, "_dispatch", coordinator)
    return capability


def _build_expert_state_root_grant(
    coordinator: "RetentionCoordinator",
    *,
    state_fd: int,
    sessions_fd: int,
    markers_fd: int,
    lock_fd: int,
    clock_capability: ExpertRetentionClockSampleCapabilityV1,
) -> _ExpertStateRootAccountLockGrantV1:
    grant = object.__new__(_ExpertStateRootAccountLockGrantV1)
    object.__setattr__(grant, "_dispatch", coordinator)
    object.__setattr__(grant, "_state_fd", state_fd)
    object.__setattr__(grant, "_sessions_fd", sessions_fd)
    object.__setattr__(grant, "_markers_fd", markers_fd)
    object.__setattr__(grant, "_lock_fd", lock_fd)
    object.__setattr__(grant, "_clock_capability", clock_capability)
    return grant


def _consume_expert_state_root_account_lock_request(
    request: ExpertStateRootAccountLockRequestV1,
) -> _ExpertStateRootAccountLockGrantV1:
    if type(request) is not ExpertStateRootAccountLockRequestV1:
        raise RetentionError("expert_state_root_request_stale")
    try:
        coordinator = object.__getattribute__(request, "_dispatch")
    except AttributeError:
        raise RetentionError("expert_state_root_request_stale") from None
    if type(coordinator) is not RetentionCoordinator:
        raise RetentionError("expert_state_root_request_stale")
    return coordinator._consume_expert_state_root_account_lock_request(
        request
    )


def _revoke_expert_state_root_account_lock_grant(
    grant: _ExpertStateRootAccountLockGrantV1,
) -> None:
    if type(grant) is not _ExpertStateRootAccountLockGrantV1:
        raise RetentionError("expert_state_root_grant_stale")
    try:
        coordinator = object.__getattribute__(grant, "_dispatch")
    except AttributeError:
        raise RetentionError("expert_state_root_grant_stale") from None
    if type(coordinator) is not RetentionCoordinator:
        raise RetentionError("expert_state_root_grant_stale")
    coordinator._revoke_expert_state_root_account_lock_grant(grant)


def sample_expert_retention_wall_ns(
    capability: ExpertRetentionClockSampleCapabilityV1,
) -> int:
    if type(capability) is not ExpertRetentionClockSampleCapabilityV1:
        raise RetentionError("expert_retention_clock_capability_stale")
    try:
        coordinator = object.__getattribute__(capability, "_dispatch")
    except AttributeError:
        raise RetentionError(
            "expert_retention_clock_capability_stale"
        ) from None
    if type(coordinator) is not RetentionCoordinator:
        raise RetentionError("expert_retention_clock_capability_stale")
    return coordinator._sample_expert_retention_wall_ns(capability)


def _claim_provider_wal_writer(
    *,
    write_capability: ProviderWalWriteCapability,
    session_manifest: SessionManifest,
) -> None:
    if type(write_capability) is not ProviderWalWriteCapability:
        raise RetentionError("retention_write_capability_invalid")
    try:
        coordinator = object.__getattribute__(write_capability, "_dispatch")
    except AttributeError:
        raise RetentionError("retention_write_capability_invalid") from None
    if type(coordinator) is not RetentionCoordinator:
        raise RetentionError("retention_write_capability_invalid")
    coordinator._claim_provider_wal_writer(
        write_capability=write_capability,
        session_manifest=session_manifest,
    )


def _claim_provider_wal_reader(
    *,
    read_capability: ProviderWalReadCapability,
) -> None:
    if type(read_capability) is not ProviderWalReadCapability:
        raise RetentionError("retention_read_capability_invalid")
    try:
        coordinator = object.__getattribute__(read_capability, "_dispatch")
    except AttributeError:
        raise RetentionError("retention_read_capability_invalid") from None
    if type(coordinator) is not RetentionCoordinator:
        raise RetentionError("retention_read_capability_invalid")
    coordinator._claim_provider_wal_reader(
        read_capability=read_capability,
    )


def _claim_provider_wal_runtime(
    *,
    write_capability: ProviderWalWriteCapability,
    persistence_authorizer: RetentionSessionAuthorizer,
    coordinator: "RetentionCoordinator",
    session_manifest: SessionManifest,
) -> None:
    if (
        type(write_capability) is not ProviderWalWriteCapability
        or type(coordinator) is not RetentionCoordinator
    ):
        raise RetentionError("retention_runtime_claim_invalid")
    try:
        dispatch = object.__getattribute__(write_capability, "_dispatch")
    except AttributeError:
        raise RetentionError("retention_runtime_claim_invalid") from None
    if dispatch is not coordinator:
        raise RetentionError("retention_runtime_claim_invalid")
    coordinator._claim_provider_wal_runtime(
        write_capability=write_capability,
        persistence_authorizer=persistence_authorizer,
        session_manifest=session_manifest,
    )


def _ack_provider_wal_clean_terminal(
    *,
    write_capability: ProviderWalWriteCapability,
) -> None:
    if type(write_capability) is not ProviderWalWriteCapability:
        raise RetentionError("retention_clean_ack_invalid")
    try:
        coordinator = object.__getattribute__(write_capability, "_dispatch")
    except AttributeError:
        raise RetentionError("retention_clean_ack_invalid") from None
    if type(coordinator) is not RetentionCoordinator:
        raise RetentionError("retention_clean_ack_invalid")
    coordinator._ack_provider_wal_clean_terminal(
        write_capability=write_capability,
    )


def _reject_replay_manifest(
    *,
    read_capability: ProviderWalReadCapability,
    persistence_authorizer: RetentionSessionAuthorizer,
    coordinator: "RetentionCoordinator",
    session_id: str,
) -> None:
    if (
        type(read_capability) is not ProviderWalReadCapability
        or type(coordinator) is not RetentionCoordinator
    ):
        raise RetentionError("retention_replay_rejection_invalid")
    try:
        dispatch = object.__getattribute__(read_capability, "_dispatch")
    except AttributeError:
        raise RetentionError("retention_replay_rejection_invalid") from None
    if dispatch is not coordinator:
        raise RetentionError("retention_replay_rejection_invalid")
    coordinator._reject_replay_manifest(
        read_capability=read_capability,
        persistence_authorizer=persistence_authorizer,
        session_id=session_id,
    )


def _reject_expected_replay_manifest(
    *,
    expected_session_manifest_sha256: str,
    persistence_authorizer: RetentionSessionAuthorizer,
    coordinator: "RetentionCoordinator",
) -> None:
    from .sequencer import ProviderPersistenceAuthorizer

    if (
        type(coordinator) is not RetentionCoordinator
        or type(persistence_authorizer)
        is not ProviderPersistenceAuthorizer
    ):
        raise RetentionError(
            "retention_expected_replay_rejection_invalid"
        )
    coordinator._reject_expected_replay_manifest(
        expected_session_manifest_sha256=(
            expected_session_manifest_sha256
        ),
        persistence_authorizer=persistence_authorizer,
    )


class RetentionCoordinator:
    __slots__ = (
        "__weakref__",
        "_config",
        "_clock_ns",
        "_lock",
        "_condition",
        "_state_fd",
        "_sessions_fd",
        "_markers_fd",
        "_lock_fd",
        "_lock_identity",
        "_ready",
        "_closing",
        "_closed",
        "_close_failed",
        "_owner_pid",
        "_owner_thread",
        "_generation",
        "_worker",
        "_write_capabilities",
        "_read_capabilities",
        "_write_tombstones",
        "_read_tombstones",
        "_session_states",
        "_deadlines",
        "_ambiguous_halt",
        "_expert_root_issued",
        "_expert_root_requests",
        "_expert_root_grants",
        "_expert_revoked_root_grants",
        "_expert_clock_capabilities",
        "_expert_root_operations_inflight",
    )

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("use RetentionCoordinator.acquire")

    @classmethod
    def acquire(
        cls,
        config: TennisV1Config,
        *,
        clock_ns: Callable[[], int],
    ) -> "RetentionCoordinator":
        _raise_if_global_halt()
        _require_posix_features()
        if type(config) is not TennisV1Config or not callable(clock_ns):
            raise TypeError("exact TennisV1Config and callable clock required")
        state_fd = sessions_fd = markers_fd = lock_fd = -1
        try:
            state_fd = cls._open_state_root(config.state_root)
            sessions_fd = cls._open_managed_directory(state_fd, "sessions")
            markers_fd = cls._open_managed_directory(
                state_fd, "retention-markers"
            )
            lock_fd, lock_identity = cls._open_lock(state_fd)
            instance = object.__new__(cls)
            instance._config = config
            instance._clock_ns = clock_ns
            instance._lock = threading.RLock()
            instance._condition = threading.Condition(instance._lock)
            instance._state_fd = state_fd
            instance._sessions_fd = sessions_fd
            instance._markers_fd = markers_fd
            instance._lock_fd = lock_fd
            instance._lock_identity = lock_identity
            instance._ready = False
            instance._closing = False
            instance._closed = False
            instance._close_failed = False
            instance._owner_pid = os.getpid()
            instance._owner_thread = threading.current_thread()
            instance._generation = 1
            instance._write_capabilities = {}
            instance._read_capabilities = {}
            instance._write_tombstones = weakref.WeakKeyDictionary()
            instance._read_tombstones = weakref.WeakKeyDictionary()
            instance._session_states = {}
            instance._deadlines = {}
            instance._ambiguous_halt = False
            instance._expert_root_issued = False
            instance._expert_root_requests = {}
            instance._expert_root_grants = {}
            instance._expert_revoked_root_grants = weakref.WeakSet()
            instance._expert_clock_capabilities = {}
            instance._expert_root_operations_inflight = 0
            instance._worker = threading.Thread(
                target=instance._expiry_worker,
                name="tennis-v1-retention-expiry",
                daemon=False,
            )
            with _GLOBAL_HALT_LOCK:
                if _PROCESS_HALT is not None:
                    raise RetentionGlobalHalt("retention_global_halt")
                _ACTIVE_COORDINATORS.add(instance)
            instance._worker.start()
            return instance
        except Exception as error:
            for fd in (lock_fd, markers_fd, sessions_fd, state_fd):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            if isinstance(error, RetentionError):
                raise
            raise RetentionError("retention_acquire_failed") from error

    @staticmethod
    def _open_state_root(path: Path) -> int:
        if not isinstance(path, Path) or not path.is_absolute():
            raise RetentionError("retention_state_root_invalid")
        components = path.parts[1:]
        if not components or any(item in ("", ".", "..") for item in components):
            raise RetentionError("retention_state_root_invalid")
        current = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for component in components[:-1]:
                next_fd = os.open(
                    component,
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | os.O_CLOEXEC
                    | os.O_NOFOLLOW,
                    dir_fd=current,
                )
                os.close(current)
                current = next_fd
            created = False
            try:
                state_fd = os.open(
                    components[-1],
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | os.O_CLOEXEC
                    | os.O_NOFOLLOW,
                    dir_fd=current,
                )
            except FileNotFoundError:
                os.mkdir(components[-1], 0o700, dir_fd=current)
                created = True
                os.chmod(
                    components[-1],
                    0o700,
                    dir_fd=current,
                    follow_symlinks=False,
                )
                state_fd = os.open(
                    components[-1],
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | os.O_CLOEXEC
                    | os.O_NOFOLLOW,
                    dir_fd=current,
                )
            if created:
                os.fchmod(state_fd, 0o700)
                _fsync_directory(current)
            _validate_directory(state_fd)
            return state_fd
        finally:
            os.close(current)

    @staticmethod
    def _open_managed_directory(parent_fd: int, name: str) -> int:
        created = False
        try:
            fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
            os.chmod(name, 0o700, dir_fd=parent_fd, follow_symlinks=False)
            fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        try:
            if created:
                os.fchmod(fd, 0o700)
                _fsync_directory(parent_fd)
            _validate_directory(fd)
            return fd
        except Exception:
            os.close(fd)
            raise

    @staticmethod
    def _open_lock(state_fd: int) -> tuple[int, _FileIdentity]:
        flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        try:
            fd = os.open(
                "retention.lock",
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=state_fd,
            )
            created = True
        except FileExistsError:
            fd = os.open("retention.lock", flags, dir_fd=state_fd)
            created = False
        try:
            if created:
                os.fchmod(fd, 0o600)
            identity = _validate_file_stat(os.fstat(fd))
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RetentionError("retention_lock_unavailable") from None
            named = os.stat(
                "retention.lock", dir_fd=state_fd, follow_symlinks=False
            )
            if _validate_file_stat(named) != identity:
                raise RetentionError("retention_lock_substituted")
            if created:
                os.fsync(fd)
                _fsync_directory(state_fd)
            return fd, identity
        except Exception:
            os.close(fd)
            raise

    def _sample_clock(self) -> int:
        try:
            now = self._clock_ns()
        except Exception as error:
            _latch_global_halt(self, session_id=None, ambiguous=True)
            raise RetentionGlobalHalt("retention_clock_failed") from error
        if type(now) is not int or now < 0:
            _latch_global_halt(self, session_id=None, ambiguous=True)
            raise RetentionGlobalHalt("retention_clock_invalid")
        return now

    def _require_open(self, *, ready: bool = True) -> None:
        _raise_if_global_halt()
        if self._closed or self._closing:
            raise RetentionError("retention_coordinator_closed")
        if ready and not self._ready:
            raise RetentionError("retention_recovery_required")

    def _require_healthy_inventory(self) -> None:
        try:
            self._inventory()
        except (RetentionError, OSError):
            self._halt(ambiguous=True)

    def _validate_roots_and_lock(self) -> None:
        _validate_directory(self._state_fd)
        _validate_directory(self._sessions_fd)
        _validate_directory(self._markers_fd)
        if (
            _validate_named_fd(
                self._lock_fd, "retention.lock", self._state_fd
            )
            != self._lock_identity
        ):
            raise RetentionError("retention_lock_substituted")

    def _halt(self, *, session_id: str | None = None, ambiguous: bool = True) -> None:
        self._ambiguous_halt = self._ambiguous_halt or ambiguous
        _latch_global_halt(
            self, session_id=session_id, ambiguous=ambiguous
        )
        raise RetentionGlobalHalt("retention_global_halt")

    def recover_and_purge(self) -> PurgeReport:
        with self._condition:
            self._require_open(ready=False)
            try:
                report = self._recover_locked()
            except RetentionDueDeleteError:
                raise
            except RetentionGlobalHalt:
                raise
            except Exception as error:
                _latch_global_halt(self, session_id=None, ambiguous=True)
                self._ambiguous_halt = True
                raise RetentionGlobalHalt("retention_recovery_failed") from error
            self._ready = True
            self._condition.notify_all()
            return report

    def _inventory(
        self,
    ) -> tuple[dict[str, RetentionMarker], set[str], set[str]]:
        self._validate_roots_and_lock()
        try:
            marker_names = sorted(os.listdir(self._markers_fd))
            session_names = sorted(os.listdir(self._sessions_fd))
        except OSError as error:
            raise RetentionError("retention_inventory_failed") from error
        markers: dict[str, RetentionMarker] = {}
        expected_wals: set[str] = set()
        expected_reserves: set[str] = set()
        for name in marker_names:
            match = _MARKER_NAME.fullmatch(name)
            if match is None:
                raise RetentionError("retention_unexpected_marker_entry")
            try:
                fd, _ = _open_existing_file(
                    self._markers_fd, name, writable=False
                )
                marker, _ = _read_marker(fd)
            finally:
                if "fd" in locals():
                    _close_fd(fd)
                    del fd
            if (
                name != _marker_basename(marker.session_id)
                or match.group(1) != marker.session_id
                or marker.session_id in markers
                or marker.wal_basename in expected_wals
                or marker.reserve_basename in expected_reserves
            ):
                raise RetentionError("retention_marker_inventory_mismatch")
            markers[marker.session_id] = marker
            expected_wals.add(marker.wal_basename)
            expected_reserves.add(marker.reserve_basename)
        present_wals: set[str] = set()
        present_reserves: set[str] = set()
        for name in session_names:
            wal_match = _WAL_NAME.fullmatch(name)
            reserve_match = _RESERVE_NAME.fullmatch(name)
            if wal_match is None and reserve_match is None:
                raise RetentionError("retention_unexpected_session_entry")
            if wal_match is not None:
                if name not in expected_wals:
                    raise RetentionError("retention_unbound_wal")
                fd, _ = _open_existing_file(
                    self._sessions_fd, name, writable=False
                )
                _close_fd(fd)
                present_wals.add(name)
            else:
                assert reserve_match is not None
                if name not in expected_reserves:
                    raise RetentionError("retention_unbound_reserve")
                fd, _ = _open_existing_file(
                    self._sessions_fd, name, writable=False
                )
                try:
                    _validate_named_fd(
                        fd,
                        name,
                        self._sessions_fd,
                        exact_size=RESERVE_BYTES,
                        physical=True,
                    )
                finally:
                    _close_fd(fd)
                present_reserves.add(name)
        return markers, present_wals, present_reserves

    def _recover_locked(self) -> PurgeReport:
        try:
            markers, present_wals, present_reserves = self._inventory()
        except (RetentionError, OSError):
            self._halt(ambiguous=True)
        live_sessions = {
            authority.session_id
            for authority in self._write_capabilities.values()
        } | {
            authority.session_id
            for authority in self._read_capabilities.values()
        }
        if not live_sessions.issubset(markers):
            self._halt(ambiguous=True)
        deleted: list[str] = []
        recovered: list[str] = []
        retained_deadlines: dict[str, int] = {}
        now = self._sample_clock()
        for session_id in sorted(markers):
            marker = markers[session_id]
            has_wal = marker.wal_basename in present_wals
            has_reserve = marker.reserve_basename in present_reserves
            due = now >= marker.delete_by_ns
            live = self._session_states.get(session_id)
            if session_id in live_sessions and (
                not has_wal
                or (
                    live is not None
                    and live.reserve_identity is not None
                    and not has_reserve
                )
            ):
                self._halt(session_id=session_id, ambiguous=False)
            if not has_wal:
                self._cleanup_orphan_marker(
                    marker, has_reserve=has_reserve, due=due
                )
                recovered.append(session_id)
            elif due:
                self._delete_due_marker(marker, has_reserve=has_reserve)
                deleted.append(session_id)
            else:
                if has_reserve and not (
                    live is not None
                    and live.write_token is not None
                    and live.healthy
                ):
                    self._unlink_validated_reserve(marker.reserve_basename)
                    _fsync_directory(self._sessions_fd)
                retained_deadlines[session_id] = marker.delete_by_ns
        self._deadlines = retained_deadlines
        return PurgeReport(
            deleted_sessions=tuple(sorted(deleted)),
            recovered_markers=tuple(sorted(recovered)),
        )

    def _cleanup_orphan_marker(
        self,
        marker: RetentionMarker,
        *,
        has_reserve: bool,
        due: bool,
    ) -> None:
        try:
            if has_reserve:
                self._unlink_validated_reserve(marker.reserve_basename)
            _fsync_directory(self._sessions_fd)
            self._unlink_validated_file(
                self._markers_fd, marker.session_id, _marker_basename(marker.session_id)
            )
            _fsync_directory(self._markers_fd)
        except Exception as error:
            _latch_global_halt(
                self, session_id=marker.session_id, ambiguous=False
            )
            if due:
                raise RetentionDueDeleteError(
                    "retention_due_delete_failed"
                ) from error
            raise RetentionGlobalHalt("retention_recovery_cleanup_failed") from error

    def _unlink_validated_reserve(self, name: str) -> None:
        fd, _ = _open_existing_file(self._sessions_fd, name, writable=False)
        try:
            _validate_named_fd(
                fd,
                name,
                self._sessions_fd,
                exact_size=RESERVE_BYTES,
                physical=True,
            )
        finally:
            _close_fd(fd)
        os.unlink(name, dir_fd=self._sessions_fd)

    def _unlink_validated_file(
        self, directory_fd: int, session_id: str, name: str
    ) -> None:
        fd, _ = _open_existing_file(directory_fd, name, writable=False)
        try:
            _validate_named_fd(fd, name, directory_fd)
        finally:
            _close_fd(fd)
        os.unlink(name, dir_fd=directory_fd)

    def _delete_due_marker(
        self, marker: RetentionMarker, *, has_reserve: bool
    ) -> None:
        try:
            state = self._session_states.get(marker.session_id)
            if state is not None:
                state.capability_lifecycle.due = True
            self._revoke_session_locked(marker.session_id, due=True)
            self._unlink_validated_file(
                self._sessions_fd, marker.session_id, marker.wal_basename
            )
            if has_reserve:
                self._unlink_validated_reserve(marker.reserve_basename)
            _fsync_directory(self._sessions_fd)
            self._unlink_validated_file(
                self._markers_fd,
                marker.session_id,
                _marker_basename(marker.session_id),
            )
            _fsync_directory(self._markers_fd)
            self._session_states.pop(marker.session_id, None)
            self._deadlines.pop(marker.session_id, None)
        except Exception as error:
            _latch_global_halt(
                self, session_id=marker.session_id, ambiguous=False
            )
            raise RetentionDueDeleteError("retention_due_delete_failed") from error

    def _require_authorizer_binding(
        self,
        authorizer: RetentionSessionAuthorizer,
        manifest: SessionManifest,
        decision: QualificationDecision,
        *,
        marker: RetentionMarker | None = None,
    ) -> None:
        try:
            if (
                type(manifest) is not SessionManifest
                or type(decision) is not QualificationDecision
                or authorizer.coordinator is not self
                or authorizer.session_manifest is not manifest
                or authorizer.bound_decision is not decision
            ):
                raise RetentionError("retention_authorizer_identity_invalid")
            require_decision_matches_session(decision, manifest)
            if marker is not None:
                _manifest_matches_marker(manifest, decision, marker)
        except RetentionError:
            raise
        except Exception as error:
            raise RetentionError("retention_authorizer_binding_failed") from error

    @staticmethod
    def _directory_transition_observation(
        value: os.stat_result,
    ) -> _DirectoryTransitionObservation:
        _validate_directory_stat(value)
        return _DirectoryTransitionObservation(
            identity=_file_identity(value),
            size=value.st_size,
            mtime_ns=value.st_mtime_ns,
            ctime_ns=value.st_ctime_ns,
        )

    def _observe_expert_arm_directory(
        self,
        *,
        original_fd: int,
        duplicate_fd: int,
        basename: str,
    ) -> _DirectoryTransitionObservation:
        original = self._directory_transition_observation(
            os.fstat(original_fd)
        )
        duplicate = self._directory_transition_observation(
            os.fstat(duplicate_fd)
        )
        named = self._directory_transition_observation(
            os.stat(
                basename,
                dir_fd=self._state_fd,
                follow_symlinks=False,
            )
        )
        if original != duplicate or original != named:
            raise RetentionError(
                "expert_arm_directory_transition_invalid"
            )
        return original

    def _prepare_expert_arm_identity_refresh(
        self,
        marker: RetentionMarker,
    ) -> tuple[_ExpertArmRootIdentityTransition, ...]:
        transitions: list[_ExpertArmRootIdentityTransition] = []
        for authority in tuple(self._expert_root_grants.values()):
            try:
                self._validate_expert_root_authority_locked(
                    authority,
                    authority.clock_capability,
                )
                with _PROVIDER_IO_LOCK:
                    self._validate_expert_root_binding(authority)
                    sessions = self._observe_expert_arm_directory(
                        original_fd=self._sessions_fd,
                        duplicate_fd=authority.sessions_fd,
                        basename="sessions",
                    )
                    markers = self._observe_expert_arm_directory(
                        original_fd=self._markers_fd,
                        duplicate_fd=authority.markers_fd,
                        basename="retention-markers",
                    )
                    session_entries = tuple(
                        sorted(os.listdir(self._sessions_fd))
                    )
                    marker_entries = tuple(
                        sorted(os.listdir(self._markers_fd))
                    )
                if (
                    marker.wal_basename in session_entries
                    or marker.reserve_basename in session_entries
                    or _marker_basename(marker.session_id)
                    in marker_entries
                ):
                    raise RetentionError(
                        "expert_arm_directory_transition_invalid"
                    )
                transitions.append(
                    _ExpertArmRootIdentityTransition(
                        authority=authority,
                        sessions=sessions,
                        markers=markers,
                        session_entries=session_entries,
                        marker_entries=marker_entries,
                    )
                )
            except BaseException as error:
                self._raise_after_expert_root_failure(authority, error)
        return tuple(transitions)

    @staticmethod
    def _require_arm_directory_transition(
        before: _DirectoryTransitionObservation,
        after: _DirectoryTransitionObservation,
        *,
        created_entries: int,
    ) -> None:
        if (
            (
                after.identity.device,
                after.identity.inode,
                after.identity.mode,
                after.identity.owner,
            )
            != (
                before.identity.device,
                before.identity.inode,
                before.identity.mode,
                before.identity.owner,
            )
            or after.identity.links
            not in (
                before.identity.links,
                before.identity.links + created_entries,
            )
            or after.mtime_ns < before.mtime_ns
            or after.ctime_ns < before.ctime_ns
        ):
            raise RetentionError(
                "expert_arm_directory_transition_invalid"
            )

    def _commit_expert_arm_identity_refresh(
        self,
        transitions: tuple[_ExpertArmRootIdentityTransition, ...],
        *,
        marker: RetentionMarker,
        wal_fd: int,
        reserve_fd: int,
        wal_identity: _FileIdentity,
        reserve_identity: _FileIdentity,
    ) -> None:
        validated: list[
            tuple[
                _ExpertArmRootIdentityTransition,
                _DirectoryTransitionObservation,
                _DirectoryTransitionObservation,
            ]
        ] = []
        failed_authority: _ExpertRootGrantAuthority | None = None
        try:
            for transition in transitions:
                authority = transition.authority
                failed_authority = authority
                self._validate_expert_root_authority_locked(
                    authority,
                    authority.clock_capability,
                )
                if (
                    authority.sessions_identity
                    != transition.sessions.identity
                    or authority.markers_identity
                    != transition.markers.identity
                ):
                    raise RetentionError(
                        "expert_arm_directory_transition_invalid"
                    )
            with _PROVIDER_IO_LOCK:
                for transition in transitions:
                    authority = transition.authority
                    failed_authority = authority
                    sessions = self._observe_expert_arm_directory(
                        original_fd=self._sessions_fd,
                        duplicate_fd=authority.sessions_fd,
                        basename="sessions",
                    )
                    markers = self._observe_expert_arm_directory(
                        original_fd=self._markers_fd,
                        duplicate_fd=authority.markers_fd,
                        basename="retention-markers",
                    )
                    session_entries = tuple(
                        sorted(os.listdir(self._sessions_fd))
                    )
                    marker_entries = tuple(
                        sorted(os.listdir(self._markers_fd))
                    )
                    if (
                        session_entries
                        != tuple(
                            sorted(
                                (
                                    *transition.session_entries,
                                    marker.wal_basename,
                                    marker.reserve_basename,
                                )
                            )
                        )
                        or marker_entries
                        != tuple(
                            sorted(
                                (
                                    *transition.marker_entries,
                                    _marker_basename(marker.session_id),
                                )
                            )
                        )
                        or _validate_named_fd(
                            wal_fd,
                            marker.wal_basename,
                            self._sessions_fd,
                        )
                        != wal_identity
                        or _validate_named_fd(
                            reserve_fd,
                            marker.reserve_basename,
                            self._sessions_fd,
                            exact_size=RESERVE_BYTES,
                            physical=True,
                        )
                        != reserve_identity
                        or self._load_named_marker(marker.session_id)
                        != marker
                    ):
                        raise RetentionError(
                            "expert_arm_directory_transition_invalid"
                        )
                    self._require_arm_directory_transition(
                        transition.sessions,
                        sessions,
                        created_entries=2,
                    )
                    self._require_arm_directory_transition(
                        transition.markers,
                        markers,
                        created_entries=1,
                    )
                    validated.append(
                        (transition, sessions, markers)
                    )
                try:
                    for transition, sessions, markers in validated:
                        transition.authority.sessions_identity = (
                            sessions.identity
                        )
                        transition.authority.markers_identity = (
                            markers.identity
                        )
                    for transition, _sessions, _markers in validated:
                        failed_authority = transition.authority
                        self._validate_expert_root_binding(
                            transition.authority
                        )
                except BaseException:
                    for transition, _sessions, _markers in validated:
                        transition.authority.sessions_identity = (
                            transition.sessions.identity
                        )
                        transition.authority.markers_identity = (
                            transition.markers.identity
                        )
                    raise
        except BaseException as error:
            if failed_authority is None:
                raise
            self._raise_after_expert_root_failure(
                failed_authority,
                error,
            )

    def arm_before_wal(
        self,
        *,
        session_manifest: SessionManifest,
        decision: QualificationDecision,
        persistence_authorizer: RetentionSessionAuthorizer,
    ) -> ProviderWalWriteCapability:
        with self._condition:
            self._require_open()
            self._require_healthy_inventory()
            if (
                type(session_manifest) is not SessionManifest
                or type(decision) is not QualificationDecision
            ):
                raise TypeError("exact manifest and decision required")
            try:
                self._require_authorizer_binding(
                    persistence_authorizer,
                    session_manifest,
                    decision,
                )
                persistence_authorizer.authorize_session()
                self._require_authorizer_binding(
                    persistence_authorizer,
                    session_manifest,
                    decision,
                )
                upper = persistence_authorizer.authorize_raw_persistence()
                self._require_authorizer_binding(
                    persistence_authorizer,
                    session_manifest,
                    decision,
                )
            except RetentionError:
                raise
            except Exception as error:
                raise RetentionError("retention_authorization_failed") from error
            if (
                type(upper) is not int
                or upper < session_manifest.required_retention_until_ns
            ):
                raise RetentionError("retention_authorized_deadline_inadequate")
            now = self._sample_clock()
            if now >= session_manifest.required_retention_until_ns:
                raise RetentionDueDeleteError("retention_deadline_reached")
            self._require_authorizer_binding(
                persistence_authorizer,
                session_manifest,
                decision,
            )
            marker = RetentionMarker(
                schema_version=1,
                session_id=session_manifest.session_id,
                wal_basename=_wal_basename(session_manifest.session_id),
                reserve_basename=_reserve_basename(session_manifest.session_id),
                delete_by_ns=session_manifest.required_retention_until_ns,
                session_manifest_sha256=session_manifest_sha256(session_manifest),
                provider_request_binding_sha256=(
                    decision.provider_request_binding_sha256 or ""
                ),
                provider_manifest_file_sha256=(
                    session_manifest.provider_manifest_file_sha256
                ),
                entitlement_id_sha256=session_manifest.entitlement_id_sha256,
                qualification_artifact_sha256=(
                    session_manifest.qualification_artifact_sha256
                ),
                created_at_ns=now,
            )
            if session_manifest.session_id in self._deadlines:
                raise RetentionError("retention_session_already_armed")
            try:
                filesystem = os.fstatvfs(self._sessions_fd)
                available = filesystem.f_bavail * filesystem.f_frsize
            except OSError as error:
                _latch_global_halt(
                    self,
                    session_id=marker.session_id,
                    ambiguous=False,
                )
                raise RetentionGlobalHalt(
                    "retention_capacity_stat_failed"
                ) from error
            if (
                available
                <= MIN_FREE_BYTES + RESERVE_BYTES + MAX_FRAME_BYTES
            ):
                raise RetentionPrewriteCapacityError(
                    "retention_bootstrap_capacity_low"
                )
            marker_fd = wal_fd = reserve_fd = -1
            try:
                expert_arm_transitions = (
                    self._prepare_expert_arm_identity_refresh(marker)
                )
                marker_fd = self._create_file(
                    self._markers_fd, _marker_basename(marker.session_id)
                )
                _write_all(marker_fd, _marker_bytes(marker))
                os.fsync(marker_fd)
                os.close(marker_fd)
                marker_fd = -1
                _fsync_directory(self._markers_fd)

                wal_fd = self._create_file(
                    self._sessions_fd, marker.wal_basename, append=True
                )
                reserve_fd = self._create_file(
                    self._sessions_fd, marker.reserve_basename
                )
                _allocate_reserve(reserve_fd)
                wal_identity = _validate_named_fd(
                    wal_fd, marker.wal_basename, self._sessions_fd
                )
                reserve_identity = _validate_named_fd(
                    reserve_fd,
                    marker.reserve_basename,
                    self._sessions_fd,
                    exact_size=RESERVE_BYTES,
                    physical=True,
                )
                os.fsync(wal_fd)
                os.fsync(reserve_fd)
                _fsync_directory(self._sessions_fd)
                self._commit_expert_arm_identity_refresh(
                    expert_arm_transitions,
                    marker=marker,
                    wal_fd=wal_fd,
                    reserve_fd=reserve_fd,
                    wal_identity=wal_identity,
                    reserve_identity=reserve_identity,
                )
            except Exception as error:
                for fd in (marker_fd, wal_fd, reserve_fd):
                    if fd >= 0:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                _latch_global_halt(
                    self, session_id=marker.session_id, ambiguous=False
                )
                raise RetentionGlobalHalt("retention_arm_durability_failed") from error
            token = object()
            capability = _build_write_capability(self)
            lifecycle = _CapabilityLifecycle()
            self._write_capabilities[capability] = _WriteAuthority(
                capability=capability,
                coordinator=self,
                token=token,
                session_id=marker.session_id,
                manifest_sha256=marker.session_manifest_sha256,
                binding_sha256=marker.provider_request_binding_sha256,
                manifest=session_manifest,
                decision=decision,
                authorizer=persistence_authorizer,
                owner_pid=os.getpid(),
                owner_thread=threading.current_thread(),
                generation=self._generation,
                lifecycle=lifecycle,
            )
            self._session_states[marker.session_id] = _SessionState(
                marker=marker,
                wal_identity=wal_identity,
                reserve_identity=reserve_identity,
                capability_lifecycle=lifecycle,
                wal_fd=wal_fd,
                reserve_fd=reserve_fd,
                manifest=session_manifest,
                decision=decision,
                authorizer=persistence_authorizer,
                write_token=token,
            )
            self._deadlines[marker.session_id] = marker.delete_by_ns
            self._condition.notify_all()
            return capability

    @staticmethod
    def _create_file(
        directory_fd: int, name: str, *, append: bool = False
    ) -> int:
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | os.O_NOFOLLOW
        )
        if append:
            flags |= os.O_APPEND
        fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
        try:
            os.fchmod(fd, 0o600)
            _validate_named_fd(fd, name, directory_fd)
            return fd
        except Exception:
            os.close(fd)
            raise

    def _load_named_marker(self, session_id: str) -> RetentionMarker:
        name = _marker_basename(session_id)
        fd, _ = _open_existing_file(self._markers_fd, name, writable=False)
        try:
            marker, _ = _read_marker(fd)
        finally:
            _close_fd(fd)
        if marker.session_id != session_id:
            raise RetentionError("retention_marker_session_mismatch")
        return marker

    def issue_read_capability(
        self,
        *,
        persistence_authorizer: RetentionSessionAuthorizer,
    ) -> ProviderWalReadCapability:
        with self._condition:
            self._require_open()
            self._require_healthy_inventory()
            try:
                manifest = persistence_authorizer.session_manifest
                bound = persistence_authorizer.bound_decision
                self._require_authorizer_binding(
                    persistence_authorizer,
                    manifest,
                    bound,
                )
                decision = persistence_authorizer.authorize_analysis()
                if decision is not bound:
                    raise RetentionError("retention_analysis_decision_changed")
                self._require_authorizer_binding(
                    persistence_authorizer,
                    manifest,
                    decision,
                )
            except RetentionError:
                raise
            except Exception as error:
                raise RetentionError("retention_analysis_authorization_failed") from error
            try:
                marker = self._load_named_marker(manifest.session_id)
                self._require_authorizer_binding(
                    persistence_authorizer,
                    manifest,
                    decision,
                    marker=marker,
                )
            except RetentionError:
                state = self._session_states.get(manifest.session_id)
                if state is not None:
                    state.healthy = False
                self._halt(session_id=manifest.session_id, ambiguous=False)
            now = self._sample_clock()
            if now >= marker.delete_by_ns:
                try:
                    has_reserve = self._entry_exists(marker.reserve_basename)
                except RetentionError:
                    state = self._session_states.get(marker.session_id)
                    if state is not None:
                        state.healthy = False
                    self._halt(session_id=marker.session_id, ambiguous=False)
                self._delete_due_marker(marker, has_reserve=has_reserve)
                raise RetentionDueDeleteError("retention_deadline_reached")
            try:
                has_reserve = self._entry_exists(marker.reserve_basename)
            except RetentionError:
                state = self._session_states.get(marker.session_id)
                if state is not None:
                    state.healthy = False
                self._halt(session_id=marker.session_id, ambiguous=False)
            if has_reserve:
                try:
                    reserve_fd, _ = _open_existing_file(
                        self._sessions_fd,
                        marker.reserve_basename,
                        writable=False,
                    )
                    _validate_named_fd(
                        reserve_fd,
                        marker.reserve_basename,
                        self._sessions_fd,
                        exact_size=RESERVE_BYTES,
                        physical=True,
                    )
                except RetentionError:
                    state = self._session_states.get(marker.session_id)
                    if state is not None:
                        state.healthy = False
                    self._halt(session_id=marker.session_id, ambiguous=False)
                finally:
                    if "reserve_fd" in locals():
                        try:
                            _close_fd(reserve_fd)
                        except RetentionError:
                            state = self._session_states.get(marker.session_id)
                            if state is not None:
                                state.healthy = False
                            self._halt(
                                session_id=marker.session_id,
                                ambiguous=False,
                            )
                        del reserve_fd
                state = self._session_states.get(marker.session_id)
                if state is None or not state.terminal_written:
                    raise RetentionError("retention_wal_not_replay_ready")
            self._require_authorizer_binding(
                persistence_authorizer,
                manifest,
                decision,
                marker=marker,
            )
            try:
                fd, identity = _open_existing_file(
                    self._sessions_fd, marker.wal_basename, writable=False
                )
            except RetentionError:
                state = self._session_states.get(marker.session_id)
                if state is not None:
                    state.healthy = False
                self._halt(session_id=marker.session_id, ambiguous=False)
            token = object()
            capability = _build_read_capability(self)
            state = self._session_states.setdefault(
                marker.session_id,
                _SessionState(
                    marker=marker,
                    wal_identity=identity,
                    reserve_identity=None,
                ),
            )
            self._read_capabilities[capability] = _ReadAuthority(
                capability=capability,
                coordinator=self,
                token=token,
                session_id=marker.session_id,
                manifest_sha256=marker.session_manifest_sha256,
                binding_sha256=marker.provider_request_binding_sha256,
                manifest=manifest,
                decision=decision,
                authorizer=persistence_authorizer,
                owner_pid=os.getpid(),
                owner_thread=threading.current_thread(),
                generation=self._generation,
                fd=fd,
                wal_identity=identity,
                lifecycle=state.capability_lifecycle,
            )
            return capability

    def _entry_exists(self, name: str) -> bool:
        try:
            os.stat(name, dir_fd=self._sessions_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as error:
            raise RetentionError("retention_entry_stat_failed") from error
        return True

    def _validate_expert_root_binding(
        self,
        authority: _ExpertRootGrantAuthority,
        *,
        mutable_evidence_links: bool = False,
    ) -> None:
        _raise_if_global_halt()
        if (
            type(authority) is not _ExpertRootGrantAuthority
            or authority.coordinator is not self
            or authority.owner_pid != os.getpid()
            or authority.owner_thread is not threading.current_thread()
        ):
            raise RetentionError("expert_state_root_grant_stale")
        self._validate_roots_and_lock()
        try:
            original_values = (
                os.fstat(self._state_fd),
                os.fstat(self._sessions_fd),
                os.fstat(self._markers_fd),
                os.fstat(self._lock_fd),
            )
            duplicate_values = (
                os.fstat(authority.state_fd),
                os.fstat(authority.sessions_fd),
                os.fstat(authority.markers_fd),
                os.fstat(authority.lock_fd),
            )
            named_sessions = os.stat(
                "sessions",
                dir_fd=self._state_fd,
                follow_symlinks=False,
            )
            named_markers = os.stat(
                "retention-markers",
                dir_fd=self._state_fd,
                follow_symlinks=False,
            )
        except OSError as error:
            raise RetentionError("expert_state_root_grant_stale") from error
        for value in original_values[:3] + duplicate_values[:3]:
            _validate_directory_stat(value)
        original_identities = tuple(
            _file_identity(value) for value in original_values
        )
        duplicate_identities = tuple(
            _file_identity(value) for value in duplicate_values
        )
        evidence_history_valid = (
            (
                original_identities[1].device,
                original_identities[1].inode,
                original_identities[1].mode,
                original_identities[1].owner,
            )
            == (
                authority.sessions_identity.device,
                authority.sessions_identity.inode,
                authority.sessions_identity.mode,
                authority.sessions_identity.owner,
            )
            and authority.sessions_identity.links
            - original_identities[1].links
            in (0, 1)
            and (
                original_identities[2].device,
                original_identities[2].inode,
                original_identities[2].mode,
                original_identities[2].owner,
            )
            == (
                authority.markers_identity.device,
                authority.markers_identity.inode,
                authority.markers_identity.mode,
                authority.markers_identity.owner,
            )
            and authority.markers_identity.links
            - original_identities[2].links
            in (0, 1)
            if mutable_evidence_links
            else original_identities[1]
            == authority.sessions_identity
            and original_identities[2]
            == authority.markers_identity
        )
        if (
            not _same_mutable_directory_binding(
                original_identities[0],
                authority.state_identity,
            )
            or not evidence_history_valid
            or original_identities[3] != authority.lock_identity
            or duplicate_identities != original_identities
            or _file_identity(named_sessions)
            != original_identities[1]
            or _file_identity(named_markers)
            != original_identities[2]
            or _validate_file_stat(original_values[3])
            != self._lock_identity
            or _validate_file_stat(duplicate_values[3])
            != authority.lock_identity
            or object.__getattribute__(authority.grant, "_dispatch")
            is not self
            or object.__getattribute__(authority.grant, "_state_fd")
            != authority.state_fd
            or object.__getattribute__(authority.grant, "_sessions_fd")
            != authority.sessions_fd
            or object.__getattribute__(authority.grant, "_markers_fd")
            != authority.markers_fd
            or object.__getattribute__(authority.grant, "_lock_fd")
            != authority.lock_fd
            or object.__getattribute__(
                authority.grant,
                "_clock_capability",
            )
            is not authority.clock_capability
        ):
            raise RetentionError("expert_state_root_grant_stale")

    def _validate_expert_root_authority_locked(
        self,
        authority: _ExpertRootGrantAuthority,
        capability: ExpertRetentionClockSampleCapabilityV1,
    ) -> None:
        _raise_if_global_halt()
        if (
            type(authority) is not _ExpertRootGrantAuthority
            or authority.coordinator is not self
            or authority.owner_pid != os.getpid()
            or authority.owner_thread is not threading.current_thread()
            or authority.generation != self._generation
            or self._closed
            or self._closing
            or not self._ready
            or authority.clock_capability is not capability
            or self._expert_root_grants.get(authority.grant) is not authority
            or self._expert_clock_capabilities.get(capability) is not authority
            or object.__getattribute__(capability, "_dispatch") is not self
        ):
            raise RetentionError("expert_state_root_grant_stale")

    @staticmethod
    def _close_expert_root_duplicate_fds(
        fds: tuple[int, ...],
    ) -> bool:
        failed = False
        for fd in reversed(fds):
            if fd < 0:
                continue
            try:
                os.close(fd)
            except OSError:
                failed = True
        return failed

    def _begin_expert_root_operation_locked(self) -> int:
        self._expert_root_operations_inflight += 1
        return self._generation

    def _end_expert_root_operation(self) -> None:
        with self._condition:
            if self._expert_root_operations_inflight <= 0:
                raise RetentionError("expert_state_root_operation_stale")
            self._expert_root_operations_inflight -= 1
            if self._closing:
                self._condition.notify_all()

    def _invalidate_expert_root_grant_locked(
        self,
        authority: _ExpertRootGrantAuthority,
    ) -> tuple[int, ...]:
        if self._expert_root_grants.get(authority.grant) is not authority:
            return ()
        self._expert_root_grants.pop(authority.grant, None)
        self._expert_clock_capabilities.pop(
            authority.clock_capability,
            None,
        )
        self._expert_revoked_root_grants.add(authority.grant)
        return (
            authority.state_fd,
            authority.sessions_fd,
            authority.markers_fd,
            authority.lock_fd,
        )

    def _raise_after_expert_root_failure(
        self,
        authority: _ExpertRootGrantAuthority,
        error: BaseException,
    ) -> None:
        with self._condition:
            duplicate_fds = self._invalidate_expert_root_grant_locked(
                authority
            )
        close_failed = False
        if duplicate_fds:
            with _PROVIDER_IO_LOCK:
                close_failed = self._close_expert_root_duplicate_fds(
                    duplicate_fds
                )
        if close_failed:
            _latch_global_halt(
                self,
                session_id=None,
                ambiguous=True,
            )
            raise RetentionGlobalHalt(
                "expert_state_root_grant_close_failed"
            ) from error
        raise error

    def issue_expert_state_root_account_lock_request(
        self,
    ) -> ExpertStateRootAccountLockRequestV1:
        with self._condition:
            self._require_open()
            if (
                os.getpid() != self._owner_pid
                or threading.current_thread() is not self._owner_thread
                or self._expert_root_issued
            ):
                raise RetentionError("expert_state_root_request_stale")
            generation = self._begin_expert_root_operation_locked()
        try:
            try:
                with _PROVIDER_IO_LOCK:
                    _raise_if_global_halt()
                    self._validate_roots_and_lock()
                    _raise_if_global_halt()
            except RetentionGlobalHalt:
                raise
            except Exception as error:
                raise RetentionError(
                    "expert_state_root_request_stale"
                ) from error
            with self._condition:
                self._require_open()
                if (
                    os.getpid() != self._owner_pid
                    or threading.current_thread() is not self._owner_thread
                    or self._generation != generation
                    or self._expert_root_issued
                ):
                    raise RetentionError("expert_state_root_request_stale")
                _raise_if_global_halt()
                request = _build_expert_state_root_request(self)
                self._expert_root_issued = True
                self._expert_root_requests[request] = (
                    _ExpertRootRequestAuthority(
                        request=request,
                        coordinator=self,
                        owner_pid=os.getpid(),
                        owner_thread=threading.current_thread(),
                        generation=self._generation,
                    )
                )
                return request
        finally:
            self._end_expert_root_operation()

    def _consume_expert_state_root_account_lock_request(
        self,
        request: ExpertStateRootAccountLockRequestV1,
    ) -> _ExpertStateRootAccountLockGrantV1:
        with self._condition:
            request_authority = self._expert_root_requests.pop(request, None)
            if (
                request_authority is None
                or type(request_authority) is not _ExpertRootRequestAuthority
                or request_authority.request is not request
                or request_authority.coordinator is not self
                or request_authority.owner_pid != os.getpid()
                or request_authority.owner_thread
                is not threading.current_thread()
                or request_authority.generation != self._generation
                or self._closed
                or self._closing
                or not self._ready
            ):
                raise RetentionError("expert_state_root_request_stale")
            _raise_if_global_halt()
            source_fds = (
                self._state_fd,
                self._sessions_fd,
                self._markers_fd,
                self._lock_fd,
            )
            generation = self._begin_expert_root_operation_locked()
        try:
            return self._complete_expert_root_request_consumption(
                request_authority,
                source_fds=source_fds,
                generation=generation,
            )
        finally:
            self._end_expert_root_operation()

    def _complete_expert_root_request_consumption(
        self,
        request_authority: _ExpertRootRequestAuthority,
        *,
        source_fds: tuple[int, int, int, int],
        generation: int,
    ) -> _ExpertStateRootAccountLockGrantV1:
        duplicates: list[int] = []
        grant_authority: _ExpertRootGrantAuthority | None = None
        operation_error: Exception | None = None
        close_failed = False
        with _PROVIDER_IO_LOCK:
            try:
                _raise_if_global_halt()
                self._validate_roots_and_lock()
                source_values = tuple(os.fstat(fd) for fd in source_fds)
                for value in source_values[:3]:
                    _validate_directory_stat(value)
                source_identities = tuple(
                    _file_identity(value) for value in source_values
                )
                if (
                    _validate_file_stat(source_values[3])
                    != self._lock_identity
                ):
                    raise RetentionError(
                        "expert_state_root_request_stale"
                    )
                for fd in source_fds:
                    _raise_if_global_halt()
                    duplicate = os.dup(fd)
                    duplicates.append(duplicate)
                    os.set_inheritable(duplicate, False)
                    _raise_if_global_halt()
                duplicate_values = tuple(
                    os.fstat(fd) for fd in duplicates
                )
                for value in duplicate_values[:3]:
                    _validate_directory_stat(value)
                if (
                    tuple(
                        _file_identity(value)
                        for value in duplicate_values
                    )
                    != source_identities
                    or _validate_file_stat(duplicate_values[3])
                    != self._lock_identity
                ):
                    raise RetentionError(
                        "expert_state_root_request_stale"
                    )
                sampler = _build_expert_retention_clock_capability(self)
                grant = _build_expert_state_root_grant(
                    self,
                    state_fd=duplicates[0],
                    sessions_fd=duplicates[1],
                    markers_fd=duplicates[2],
                    lock_fd=duplicates[3],
                    clock_capability=sampler,
                )
                grant_authority = _ExpertRootGrantAuthority(
                    grant=grant,
                    coordinator=self,
                    owner_pid=os.getpid(),
                    owner_thread=threading.current_thread(),
                    generation=generation,
                    state_fd=duplicates[0],
                    sessions_fd=duplicates[1],
                    markers_fd=duplicates[2],
                    lock_fd=duplicates[3],
                    state_identity=source_identities[0],
                    sessions_identity=source_identities[1],
                    markers_identity=source_identities[2],
                    lock_identity=source_identities[3],
                    clock_capability=sampler,
                )
                self._validate_expert_root_binding(grant_authority)
            except Exception as error:
                operation_error = error
                close_failed = self._close_expert_root_duplicate_fds(
                    tuple(duplicates)
                )

        if operation_error is not None:
            if close_failed:
                _latch_global_halt(
                    self,
                    session_id=None,
                    ambiguous=True,
                )
                raise RetentionGlobalHalt(
                    "expert_state_root_grant_close_failed"
                ) from operation_error
            if isinstance(operation_error, RetentionError):
                raise operation_error
            raise RetentionError(
                "expert_state_root_request_stale"
            ) from operation_error

        assert grant_authority is not None
        commit_error: RetentionError | None = None
        with self._condition:
            try:
                if (
                    request_authority.generation != generation
                    or self._generation != generation
                    or self._closed
                    or self._closing
                    or not self._ready
                    or os.getpid() != request_authority.owner_pid
                    or threading.current_thread()
                    is not request_authority.owner_thread
                ):
                    raise RetentionError("expert_state_root_request_stale")
                _raise_if_global_halt()
            except RetentionError as error:
                commit_error = error
            else:
                self._expert_root_grants[grant_authority.grant] = (
                    grant_authority
                )
                self._expert_clock_capabilities[
                    grant_authority.clock_capability
                ] = grant_authority

        if commit_error is not None:
            with _PROVIDER_IO_LOCK:
                close_failed = self._close_expert_root_duplicate_fds(
                    (
                        grant_authority.state_fd,
                        grant_authority.sessions_fd,
                        grant_authority.markers_fd,
                        grant_authority.lock_fd,
                    )
                )
            if close_failed:
                _latch_global_halt(
                    self,
                    session_id=None,
                    ambiguous=True,
                )
                raise RetentionGlobalHalt(
                    "expert_state_root_grant_close_failed"
                ) from commit_error
            raise commit_error
        return grant_authority.grant

    def _revoke_expert_state_root_account_lock_grant(
        self,
        grant: _ExpertStateRootAccountLockGrantV1,
    ) -> None:
        with self._condition:
            authority = self._expert_root_grants.get(grant)
            if authority is None:
                if grant in self._expert_revoked_root_grants:
                    return
                raise RetentionError("expert_state_root_grant_stale")
            if (
                authority.owner_pid != os.getpid()
                or authority.owner_thread is not threading.current_thread()
                or authority.generation != self._generation
                or self._closed
                or self._closing
            ):
                raise RetentionError("expert_state_root_grant_stale")
            self._begin_expert_root_operation_locked()
            duplicate_fds = self._invalidate_expert_root_grant_locked(
                authority
            )
        try:
            with _PROVIDER_IO_LOCK:
                close_failed = self._close_expert_root_duplicate_fds(
                    duplicate_fds
                )
        finally:
            self._end_expert_root_operation()
        if close_failed:
            _latch_global_halt(
                self,
                session_id=None,
                ambiguous=True,
            )
            raise RetentionGlobalHalt(
                "expert_state_root_grant_close_failed"
            )

    def _sample_expert_retention_wall_ns(
        self,
        capability: ExpertRetentionClockSampleCapabilityV1,
    ) -> int:
        initial_error: BaseException | None = None
        with self._condition:
            authority = self._expert_clock_capabilities.get(capability)
            if authority is None:
                raise RetentionError(
                    "expert_retention_clock_capability_stale"
                )
            try:
                self._validate_expert_root_authority_locked(
                    authority,
                    capability,
                )
            except BaseException as error:
                initial_error = error
            self._begin_expert_root_operation_locked()
        try:
            if initial_error is not None:
                self._raise_after_expert_root_failure(
                    authority,
                    initial_error,
                )
            try:
                with _PROVIDER_IO_LOCK:
                    self._validate_expert_root_binding(
                        authority,
                        mutable_evidence_links=True,
                    )
            except BaseException as error:
                self._raise_after_expert_root_failure(authority, error)
            try:
                with self._condition:
                    self._validate_expert_root_authority_locked(
                        authority,
                        capability,
                    )
            except BaseException as error:
                self._raise_after_expert_root_failure(authority, error)

            try:
                sampled_wall_ns = self._sample_clock()
            except BaseException as error:
                self._raise_after_expert_root_failure(authority, error)

            try:
                with self._condition:
                    self._validate_expert_root_authority_locked(
                        authority,
                        capability,
                    )
            except BaseException as error:
                self._raise_after_expert_root_failure(authority, error)
            try:
                with _PROVIDER_IO_LOCK:
                    self._validate_expert_root_binding(
                        authority,
                        mutable_evidence_links=True,
                    )
            except BaseException as error:
                self._raise_after_expert_root_failure(authority, error)
            try:
                with self._condition:
                    self._validate_expert_root_authority_locked(
                        authority,
                        capability,
                    )
            except BaseException as error:
                self._raise_after_expert_root_failure(authority, error)
            return sampled_wall_ns
        finally:
            self._end_expert_root_operation()

    def require_expert_companion_creation_live(
        self,
        *,
        persistence_authorizer: ProviderPersistenceAuthorizer,
    ) -> None:
        from .sequencer import ProviderPersistenceAuthorizer

        with self._condition:
            try:
                if (
                    type(persistence_authorizer)
                    is not ProviderPersistenceAuthorizer
                    or persistence_authorizer.coordinator is not self
                    or os.getpid() != self._owner_pid
                    or threading.current_thread() is not self._owner_thread
                    or self._closed
                    or self._closing
                    or not self._ready
                    or _global_halt() is not None
                ):
                    raise RetentionError(
                        "expert_companion_creation_not_live"
                    )
                manifest = persistence_authorizer.session_manifest
                decision = persistence_authorizer.bound_decision
                if (
                    type(manifest) is not SessionManifest
                    or type(decision) is not QualificationDecision
                ):
                    raise RetentionError(
                        "expert_companion_creation_not_live"
                    )
                state = self._session_states.get(manifest.session_id)
                if state is None:
                    raise RetentionError(
                        "expert_companion_creation_not_live"
                    )
                authority = next(
                    (
                        item
                        for item in self._write_capabilities.values()
                        if item.session_id == manifest.session_id
                        and item.token is state.write_token
                    ),
                    None,
                )
                if (
                    authority is None
                    or authority.authorizer is not persistence_authorizer
                    or authority.manifest is not manifest
                    or authority.decision is not decision
                    or authority.owner_pid != os.getpid()
                    or authority.owner_thread
                    is not threading.current_thread()
                    or authority.generation != self._generation
                    or not authority.writer_claimed
                    or not authority.runtime_claimed
                    or authority.halt_consumed
                    or authority.lifecycle.due
                    or state.authorizer is not persistence_authorizer
                    or state.manifest is not manifest
                    or state.decision is not decision
                    or state.write_token is not authority.token
                    or not state.healthy
                    or not state.wal_prefix_durable
                    or not state.session_start_durable
                    or state.terminal_written
                    or state.terminal_clean_durable
                    or state.clean_preclose_ack is not None
                    or state.clean_terminal_marked
                    or state.wal_fd < 0
                    or state.reserve_fd < 0
                    or state.reserve_identity is None
                ):
                    raise RetentionError(
                        "expert_companion_creation_not_live"
                    )
                self._validate_roots_and_lock()
                marker = self._load_named_marker(manifest.session_id)
                self._require_authorizer_binding(
                    persistence_authorizer,
                    manifest,
                    decision,
                    marker=marker,
                )
                wal_identity = _validate_named_fd(
                    state.wal_fd,
                    marker.wal_basename,
                    self._sessions_fd,
                )
                reserve_identity = _validate_named_fd(
                    state.reserve_fd,
                    marker.reserve_basename,
                    self._sessions_fd,
                    exact_size=RESERVE_BYTES,
                    physical=True,
                )
                if (
                    marker != state.marker
                    or wal_identity != state.wal_identity
                    or reserve_identity != state.reserve_identity
                    or os.fstat(state.wal_fd).st_size
                    <= len(WAL_FILE_PREFIX_BYTES)
                ):
                    raise RetentionError(
                        "expert_companion_creation_not_live"
                    )
            except Exception:
                raise RetentionError(
                    "expert_companion_creation_not_live"
                ) from None

    def require_provider_operation(self) -> None:
        with self._condition:
            self._require_open()
            self._require_healthy_inventory()

    def require_control_halt_eligible(self, *, session_id: str) -> None:
        """Authorize only a halt already observed at a coordinator pre-check."""
        with self._condition:
            checked_session = _require_session_id(session_id)
            halt = _global_halt()
            state = self._session_states.get(checked_session)
            if halt is None or state is None:
                raise RetentionError("retention_control_halt_not_eligible")
            if (
                halt.ambiguous
                or halt.scoped_session_id == checked_session
                or self._ambiguous_halt
                or not state.healthy
                or state.terminal_written
                or state.wal_fd < 0
            ):
                raise RetentionGlobalHalt("retention_global_halt")
            try:
                marker = self._load_named_marker(checked_session)
                if marker != state.marker:
                    raise RetentionError("retention_marker_changed")
                now = self._sample_clock()
                if now >= marker.delete_by_ns:
                    raise RetentionDueDeleteError(
                        "retention_deadline_reached"
                    )
                identity = _validate_named_fd(
                    state.wal_fd,
                    marker.wal_basename,
                    self._sessions_fd,
                )
                if (
                    state.reserve_identity is None
                    or state.reserve_fd < 0
                ):
                    raise RetentionError("retention_reserve_missing")
                reserve_identity = _validate_named_fd(
                    state.reserve_fd,
                    marker.reserve_basename,
                    self._sessions_fd,
                    exact_size=RESERVE_BYTES,
                    physical=True,
                )
                if (
                    identity != state.wal_identity
                    or reserve_identity != state.reserve_identity
                    or state.authorizer is None
                    or state.manifest is None
                    or state.decision is None
                ):
                    raise RetentionError("retention_wal_substituted")
                self._require_authorizer_binding(
                    state.authorizer,
                    state.manifest,
                    state.decision,
                    marker=marker,
                )
                authority = next(
                    (
                        item
                        for item in self._write_capabilities.values()
                        if item.session_id == checked_session
                        and item.token is state.write_token
                    ),
                    None,
                )
                if authority is None:
                    raise RetentionError(
                        "retention_control_halt_not_eligible"
                    )
                self._require_write_authority(authority.capability)
                authority.preobserved_global_halt = True
            except (RetentionDueDeleteError, RetentionGlobalHalt):
                raise
            except RetentionError:
                state.healthy = False
                raise RetentionGlobalHalt("retention_global_halt") from None

    def mark_clean_terminal(self, *, session_id: str) -> None:
        with self._condition:
            self._require_open()
            _require_session_id(session_id)
            state = self._session_states.get(session_id)
            if (
                state is None
                or not state.terminal_clean_durable
                or not state.healthy
                or state.clean_preclose_ack is None
            ):
                raise RetentionError("retention_clean_terminal_not_authoritative")
            try:
                marker = self._load_named_marker(session_id)
                if marker != state.marker:
                    raise RetentionError("retention_marker_changed")
                now = self._sample_clock()
                has_reserve = self._entry_exists(marker.reserve_basename)
                if now >= marker.delete_by_ns:
                    self._delete_due_marker(
                        marker,
                        has_reserve=has_reserve,
                    )
                    raise RetentionDueDeleteError(
                        "retention_deadline_reached"
                    )
                if has_reserve:
                    raise RetentionError(
                        "retention_reserve_still_present"
                    )
                fd = -1
                try:
                    fd, identity = _open_existing_file(
                        self._sessions_fd,
                        marker.wal_basename,
                        writable=False,
                    )
                    checked_identity = _validate_named_fd(
                        fd,
                        marker.wal_basename,
                        self._sessions_fd,
                    )
                    size = os.fstat(fd).st_size
                finally:
                    if fd >= 0:
                        _close_fd(fd)
                ack = state.clean_preclose_ack
                if (
                    identity != checked_identity
                    or identity != state.wal_identity
                    or identity != ack.wal_identity
                    or size != ack.wal_size
                    or ack.marker != marker
                    or ack.manifest_sha256
                    != marker.session_manifest_sha256
                    or ack.binding_sha256
                    != marker.provider_request_binding_sha256
                ):
                    raise RetentionError("retention_wal_substituted")
                state.clean_terminal_marked = True
            except RetentionDueDeleteError:
                raise
            except RetentionGlobalHalt:
                raise
            except RetentionError:
                state.healthy = False
                self._halt(session_id=session_id, ambiguous=False)

    def _require_write_authority(
        self,
        capability: ProviderWalWriteCapability,
    ) -> _WriteAuthority:
        if type(capability) is not ProviderWalWriteCapability:
            raise RetentionError("retention_write_capability_invalid")
        authority = self._write_capabilities.get(capability)
        if authority is None:
            lifecycle = self._write_tombstones.get(capability)
            if lifecycle is not None and lifecycle.due:
                raise RetentionDueDeleteError("retention_deadline_reached")
            raise RetentionError("retention_write_capability_stale")
        if (
            authority.capability is not capability
            or authority.coordinator is not self
            or authority.owner_pid != os.getpid()
            or authority.owner_thread is not threading.current_thread()
            or authority.generation != self._generation
        ):
            raise RetentionError("retention_write_capability_stale")
        return authority

    def _claim_provider_wal_writer(
        self,
        *,
        write_capability: ProviderWalWriteCapability,
        session_manifest: SessionManifest,
    ) -> None:
        with self._condition:
            authority = self._write_capabilities.get(write_capability)
            session_id = None if authority is None else authority.session_id
            try:
                authority = self._require_write_authority(write_capability)
                session_id = authority.session_id
                if authority.writer_claimed:
                    raise RetentionError("retention_writer_already_claimed")
                if (
                    type(session_manifest) is not SessionManifest
                    or authority.manifest is not session_manifest
                ):
                    raise RetentionError("retention_writer_manifest_invalid")
                state = self._validate_write_capability(write_capability)
                _manifest_matches_marker(
                    session_manifest,
                    authority.decision,
                    state.marker,
                )
                if (
                    session_manifest_sha256(session_manifest)
                    != authority.manifest_sha256
                    or authority.manifest_sha256
                    != state.marker.session_manifest_sha256
                    or authority.binding_sha256
                    != state.marker.provider_request_binding_sha256
                ):
                    raise RetentionError("retention_writer_binding_invalid")
                authority.writer_claimed = True
            except Exception as error:
                state = (
                    None
                    if session_id is None
                    else self._session_states.get(session_id)
                )
                if state is not None:
                    state.healthy = False
                self._close_write_locked(write_capability, strict=False)
                _latch_global_halt(
                    self,
                    session_id=session_id,
                    ambiguous=session_id is None,
                )
                if isinstance(error, RetentionGlobalHalt):
                    raise
                raise RetentionGlobalHalt(
                    "retention_writer_claim_failed"
                ) from error

    def _claim_provider_wal_reader(
        self,
        *,
        read_capability: ProviderWalReadCapability,
    ) -> None:
        with self._condition:
            authority = self._read_capabilities.get(read_capability)
            tombstone = self._read_tombstones.get(read_capability)
            session_id = (
                authority.session_id
                if authority is not None
                else None if tombstone is None else tombstone.session_id
            )
            try:
                authority = self._require_read_authority(read_capability)
                session_id = authority.session_id
                if authority.reader_claimed:
                    raise RetentionError("retention_reader_already_claimed")
                state, marker, authority = self._validate_read_capability(
                    read_capability
                )
                self._require_authorizer_binding(
                    authority.authorizer,
                    authority.manifest,
                    authority.decision,
                    marker=marker,
                )
                decision = authority.authorizer.authorize_analysis()
                if decision is not authority.decision:
                    raise RetentionError(
                        "retention_analysis_decision_changed"
                    )
                self._require_authorizer_binding(
                    authority.authorizer,
                    authority.manifest,
                    decision,
                    marker=marker,
                )
                state, marker, authority = self._validate_read_capability(
                    read_capability
                )
                self._require_authorizer_binding(
                    authority.authorizer,
                    authority.manifest,
                    authority.decision,
                    marker=marker,
                )
                authority.reader_claimed = True
            except RetentionDueDeleteError:
                raise
            except Exception as error:
                active = self._read_capabilities.get(read_capability)
                if active is not None:
                    state = self._session_states.get(active.session_id)
                    if state is not None:
                        state.healthy = False
                    self._close_read_locked(
                        read_capability,
                        strict=False,
                    )
                _latch_global_halt(
                    self,
                    session_id=session_id,
                    ambiguous=session_id is None,
                )
                if isinstance(error, RetentionGlobalHalt):
                    raise
                raise RetentionGlobalHalt(
                    "retention_reader_claim_failed"
                ) from error

    def _claim_provider_wal_runtime(
        self,
        *,
        write_capability: ProviderWalWriteCapability,
        persistence_authorizer: RetentionSessionAuthorizer,
        session_manifest: SessionManifest,
    ) -> None:
        with self._condition:
            authority = self._write_capabilities.get(write_capability)
            session_id = None if authority is None else authority.session_id
            try:
                from .sequencer import ProviderPersistenceAuthorizer

                authority = self._require_write_authority(write_capability)
                session_id = authority.session_id
                if authority.runtime_claimed:
                    raise RetentionError("retention_runtime_already_claimed")
                if (
                    type(persistence_authorizer)
                    is not ProviderPersistenceAuthorizer
                    or persistence_authorizer is not authority.authorizer
                    or persistence_authorizer.coordinator is not self
                    or persistence_authorizer.session_manifest
                    is not session_manifest
                    or authority.manifest is not session_manifest
                ):
                    raise RetentionError("retention_runtime_binding_invalid")
                state = self._validate_write_capability(write_capability)
                if (
                    not authority.writer_claimed
                    or not state.wal_prefix_durable
                    or not state.session_start_durable
                    or state.terminal_written
                    or state.clean_preclose_ack is not None
                ):
                    raise RetentionError("retention_runtime_bootstrap_invalid")
                self._require_authorizer_binding(
                    persistence_authorizer,
                    session_manifest,
                    authority.decision,
                    marker=state.marker,
                )
                if (
                    persistence_authorizer.bound_decision
                    is not authority.decision
                    or authority.manifest_sha256
                    != session_manifest_sha256(session_manifest)
                    or authority.binding_sha256
                    != state.marker.provider_request_binding_sha256
                ):
                    raise RetentionError("retention_runtime_binding_invalid")
                authority.runtime_claimed = True
            except Exception as error:
                state = (
                    None
                    if session_id is None
                    else self._session_states.get(session_id)
                )
                if state is not None:
                    state.healthy = False
                self._close_write_locked(write_capability, strict=False)
                _latch_global_halt(
                    self,
                    session_id=session_id,
                    ambiguous=session_id is None,
                )
                if isinstance(error, RetentionGlobalHalt):
                    raise
                raise RetentionGlobalHalt(
                    "retention_runtime_claim_failed"
                ) from error

    def _ack_provider_wal_clean_terminal(
        self,
        *,
        write_capability: ProviderWalWriteCapability,
    ) -> None:
        with self._condition:
            authority = self._require_write_authority(write_capability)
            state = self._session_states.get(authority.session_id)
            try:
                _raise_if_global_halt()
                if (
                    self._closed
                    or self._closing
                    or not self._ready
                    or state is None
                    or state.write_token is not authority.token
                    or not authority.writer_claimed
                    or not state.healthy
                    or not state.terminal_written
                    or not state.terminal_clean_durable
                    or state.reserve_identity is not None
                    or state.reserve_fd != -1
                    or state.clean_preclose_ack is not None
                ):
                    raise RetentionError("retention_clean_ack_invalid")
                marker = self._load_named_marker(authority.session_id)
                if marker != state.marker:
                    raise RetentionError("retention_marker_changed")
                now = self._sample_clock()
                if now >= marker.delete_by_ns:
                    raise RetentionDueDeleteError(
                        "retention_deadline_reached"
                    )
                identity = _validate_named_fd(
                    state.wal_fd,
                    marker.wal_basename,
                    self._sessions_fd,
                )
                if identity != state.wal_identity:
                    raise RetentionError("retention_wal_substituted")
                size = os.fstat(state.wal_fd).st_size
                if size <= len(WAL_FILE_PREFIX_BYTES):
                    raise RetentionError("retention_terminal_not_durable")
                self._require_authorizer_binding(
                    authority.authorizer,
                    authority.manifest,
                    authority.decision,
                    marker=marker,
                )
                state.clean_preclose_ack = _CleanTerminalAck(
                    marker=marker,
                    wal_identity=identity,
                    wal_size=size,
                    manifest_sha256=authority.manifest_sha256,
                    binding_sha256=authority.binding_sha256,
                )
            except RetentionDueDeleteError:
                raise
            except RetentionGlobalHalt:
                raise
            except Exception as error:
                if state is not None:
                    state.healthy = False
                _latch_global_halt(
                    self,
                    session_id=authority.session_id,
                    ambiguous=False,
                )
                raise RetentionGlobalHalt(
                    "retention_clean_ack_failed"
                ) from error

    def _validate_write_capability(
        self,
        capability: ProviderWalWriteCapability,
        *,
        allow_global_halt: bool = False,
    ) -> _SessionState:
        authority = self._require_write_authority(capability)
        halt = _global_halt()
        if halt is not None and not allow_global_halt:
            raise RetentionGlobalHalt("retention_global_halt")
        if halt is not None and allow_global_halt:
            source = None if halt.source is None else halt.source()
            if (
                halt.ambiguous and source is self
                or halt.scoped_session_id == authority.session_id
                or self._ambiguous_halt
            ):
                raise RetentionGlobalHalt("retention_global_halt")
        if self._closed or self._closing or not self._ready:
            raise RetentionError("retention_coordinator_unavailable")
        state = self._session_states.get(authority.session_id)
        if (
            state is None
            or state.write_token is not authority.token
            or state.manifest is not authority.manifest
            or state.decision is not authority.decision
            or state.authorizer is not authority.authorizer
            or not state.healthy
            or state.terminal_written
        ):
            raise RetentionError("retention_write_session_invalid")
        if (
            authority.manifest_sha256
            != state.marker.session_manifest_sha256
            or authority.binding_sha256
            != state.marker.provider_request_binding_sha256
        ):
            raise RetentionError("retention_write_binding_invalid")
        try:
            marker = self._load_named_marker(authority.session_id)
            if marker != state.marker:
                raise RetentionError("retention_marker_changed")
            now = self._sample_clock()
            if now >= marker.delete_by_ns:
                has_reserve = self._entry_exists(marker.reserve_basename)
                self._delete_due_marker(
                    marker,
                    has_reserve=has_reserve,
                )
                raise RetentionDueDeleteError("retention_deadline_reached")
            wal_identity = _validate_named_fd(
                state.wal_fd,
                marker.wal_basename,
                self._sessions_fd,
            )
            reserve_identity = (
                None
                if state.reserve_identity is None
                else _validate_named_fd(
                    state.reserve_fd,
                    marker.reserve_basename,
                    self._sessions_fd,
                    exact_size=RESERVE_BYTES,
                    physical=True,
                )
            )
            if (
                wal_identity != state.wal_identity
                or reserve_identity != state.reserve_identity
            ):
                raise RetentionError("retention_tuple_substituted")
        except RetentionDueDeleteError:
            raise
        except RetentionGlobalHalt:
            raise
        except RetentionError:
            state.healthy = False
            self._halt(session_id=authority.session_id, ambiguous=False)
        return state

    def _authorize_write(self, state: _SessionState) -> None:
        authorizer = state.authorizer
        assert authorizer is not None
        decision = state.decision
        assert decision is not None
        manifest = state.manifest
        assert manifest is not None
        try:
            self._require_authorizer_binding(
                authorizer,
                manifest,
                decision,
                marker=state.marker,
            )
            upper = authorizer.authorize_raw_persistence()
            self._require_authorizer_binding(
                authorizer,
                manifest,
                decision,
                marker=state.marker,
            )
        except RetentionError:
            raise
        except Exception as error:
            raise RetentionError("retention_raw_authorization_failed") from error
        if type(upper) is not int or upper < state.marker.delete_by_ns:
            raise RetentionError("retention_authorized_deadline_inadequate")

    def _authorize_clean_close(self, state: _SessionState) -> None:
        authorizer = state.authorizer
        assert authorizer is not None
        decision = state.decision
        assert decision is not None
        manifest = state.manifest
        assert manifest is not None
        try:
            self._require_authorizer_binding(
                authorizer,
                manifest,
                decision,
                marker=state.marker,
            )
            authorizer.authorize_close()
            self._require_authorizer_binding(
                authorizer,
                manifest,
                decision,
                marker=state.marker,
            )
        except RetentionError:
            raise
        except Exception as error:
            raise RetentionError(
                "retention_close_authorization_failed"
            ) from error

    def _write_capability_bytes(
        self, capability: ProviderWalWriteCapability, frame: bytes
    ) -> None:
        if type(frame) is not bytes:
            raise TypeError("provider WAL writes require exact bytes")
        with self._condition:
            state = self._validate_write_capability(capability)
            authority = self._require_write_authority(capability)
            exact_file_prefix = frame == WAL_FILE_PREFIX_BYTES
            if not authority.writer_claimed:
                self._reject_bootstrap_write(
                    capability,
                    state,
                    "retention_writer_not_claimed",
                )
            if not state.wal_prefix_durable:
                if not exact_file_prefix:
                    self._reject_bootstrap_write(
                        capability,
                        state,
                        "retention_wal_prefix_required",
                    )
                clean_terminal: bool | object | None = None
            else:
                if exact_file_prefix or frame[:4] != FRAME_MAGIC:
                    self._reject_bootstrap_write(
                        capability,
                        state,
                        "retention_wal_prefix_invalid",
                    )
                try:
                    clean_terminal = _terminal_frame_clean(frame, state)
                except RetentionError as error:
                    if (
                        not state.session_start_durable
                        or _session_start_like_frame(frame)
                    ):
                        self._reject_bootstrap_write(
                            capability,
                            state,
                            (
                                "retention_session_start_required"
                                if not state.session_start_durable
                                else "retention_session_start_invalid"
                            ),
                            cause=error,
                        )
                    raise
                if not state.session_start_durable:
                    if clean_terminal is not _SESSION_START_FRAME:
                        self._reject_bootstrap_write(
                            capability,
                            state,
                            "retention_session_start_required",
                        )
                elif clean_terminal is _SESSION_START_FRAME:
                    self._reject_bootstrap_write(
                        capability,
                        state,
                        "retention_session_start_duplicate",
                    )
                if clean_terminal is False:
                    raise RetentionError(
                        "halted_terminal_requires_halt_capability"
                    )
            nonterminal_frame = (
                frame[:4] == FRAME_MAGIC
                and clean_terminal is not True
                and clean_terminal is not False
            )
            bootstrap_frame = (
                clean_terminal is _SESSION_START_FRAME
                and state.wal_prefix_durable
                and not state.session_start_durable
            )
            if clean_terminal is True:
                self._authorize_clean_close(state)
            else:
                self._authorize_write(state)
            state = self._validate_write_capability(capability)
            if clean_terminal is True:
                self._release_reserve(capability, state)
                state = self._validate_write_capability(capability)
            assert state.authorizer is not None
            assert state.manifest is not None
            assert state.decision is not None
            self._require_authorizer_binding(
                state.authorizer,
                state.manifest,
                state.decision,
                marker=state.marker,
            )
            io_error: Exception | None = None
            capacity_error: RetentionPrewriteCapacityError | None = None
            with _PROVIDER_IO_LOCK:
                _raise_if_global_halt()
                try:
                    if nonterminal_frame and not bootstrap_frame:
                        filesystem = os.fstatvfs(state.wal_fd)
                        available = filesystem.f_bavail * filesystem.f_frsize
                        required = MIN_FREE_BYTES + RESERVE_BYTES + len(frame)
                        if available <= required:
                            raise RetentionPrewriteCapacityError(
                                "retention_prewrite_capacity_low"
                            )
                    _write_all(state.wal_fd, frame)
                    os.fsync(state.wal_fd)
                except RetentionPrewriteCapacityError as error:
                    capacity_error = error
                except Exception as error:
                    io_error = error
            if capacity_error is not None:
                raise capacity_error
            if io_error is not None:
                state.healthy = False
                _latch_global_halt(
                    self, session_id=state.marker.session_id, ambiguous=False
                )
                self._close_write_locked(capability, strict=False)
                raise RetentionGlobalHalt(
                    "retention_wal_write_failed"
                ) from io_error
            if exact_file_prefix:
                state.wal_prefix_durable = True
            elif clean_terminal is _SESSION_START_FRAME:
                state.session_start_durable = True
            if clean_terminal is True:
                state.terminal_written = True
                state.terminal_clean_durable = True

    def _reject_bootstrap_write(
        self,
        capability: ProviderWalWriteCapability,
        state: _SessionState,
        message: str,
        *,
        cause: Exception | None = None,
    ) -> None:
        state.healthy = False
        self._close_write_locked(capability, strict=False)
        _latch_global_halt(
            self,
            session_id=state.marker.session_id,
            ambiguous=False,
        )
        error = RetentionGlobalHalt(message)
        if cause is None:
            raise error
        raise error from cause

    def _fsync_write_capability(
        self, capability: ProviderWalWriteCapability
    ) -> None:
        with self._condition:
            state = self._validate_write_capability(capability)
            self._authorize_write(state)
            state = self._validate_write_capability(capability)
            assert state.authorizer is not None
            assert state.manifest is not None
            assert state.decision is not None
            self._require_authorizer_binding(
                state.authorizer,
                state.manifest,
                state.decision,
                marker=state.marker,
            )
            io_error: OSError | None = None
            with _PROVIDER_IO_LOCK:
                _raise_if_global_halt()
                try:
                    os.fsync(state.wal_fd)
                except OSError as error:
                    io_error = error
            if io_error is not None:
                state.healthy = False
                _latch_global_halt(
                    self, session_id=state.marker.session_id, ambiguous=False
                )
                raise RetentionGlobalHalt(
                    "retention_wal_fsync_failed"
                ) from io_error

    def _write_halt_control(
        self, capability: ProviderWalWriteCapability, frame: bytes
    ) -> None:
        if type(frame) is not bytes:
            # A real invocation still consumes its one-shot permit.
            with self._condition:
                self._consume_halt_permit(capability)
            raise TypeError("halt control requires exact bytes")
        with self._condition:
            state = self._validate_write_capability(
                capability, allow_global_halt=True
            )
            authority = self._require_write_authority(capability)
            if (
                _global_halt() is not None
                and not authority.preobserved_global_halt
            ):
                raise RetentionGlobalHalt("retention_global_halt")
            if (
                not authority.writer_claimed
                or not state.wal_prefix_durable
                or not state.session_start_durable
            ):
                self._reject_bootstrap_write(
                    capability,
                    state,
                    "retention_bootstrap_incomplete",
                )
            state = self._consume_halt_permit(capability)
            clean = _terminal_frame_clean(frame, state)
            if clean is not False:
                raise RetentionError("halt control requires clean false")
            state = self._validate_write_capability(
                capability, allow_global_halt=True
            )
            self._release_reserve(capability, state)
            # Recheck immediately before append.
            if _global_halt() is None:
                state = self._validate_write_capability(capability)
            else:
                state = self._validate_write_capability(
                    capability, allow_global_halt=True
                )
            io_error: Exception | None = None
            with _PROVIDER_IO_LOCK:
                halt = _global_halt()
                if halt is not None:
                    if not authority.preobserved_global_halt:
                        raise RetentionGlobalHalt(
                            "retention_global_halt"
                        )
                    source = None if halt.source is None else halt.source()
                    if (
                        halt.ambiguous and source is self
                        or halt.scoped_session_id == state.marker.session_id
                        or self._ambiguous_halt
                    ):
                        raise RetentionGlobalHalt("retention_global_halt")
                try:
                    _write_all(state.wal_fd, frame)
                    os.fsync(state.wal_fd)
                except Exception as error:
                    io_error = error
            if io_error is not None:
                state.healthy = False
                _latch_global_halt(
                    self, session_id=state.marker.session_id, ambiguous=False
                )
                raise RetentionGlobalHalt(
                    "retention_halt_write_failed"
                ) from io_error
            state.terminal_written = True
            state.terminal_clean_durable = False

    def _consume_halt_permit(
        self, capability: ProviderWalWriteCapability
    ) -> _SessionState:
        state = self._validate_write_capability(
            capability, allow_global_halt=True
        )
        authority = self._require_write_authority(capability)
        if authority.halt_consumed:
            raise RetentionError("retention_halt_permit_consumed")
        authority.halt_consumed = True
        return state

    def _prepare_expert_sessions_identity_refresh(
        self,
    ) -> tuple[tuple[_ExpertRootGrantAuthority, _FileIdentity], ...]:
        transitions: list[
            tuple[_ExpertRootGrantAuthority, _FileIdentity]
        ] = []
        for authority in tuple(self._expert_root_grants.values()):
            try:
                self._validate_expert_root_authority_locked(
                    authority,
                    authority.clock_capability,
                )
                with _PROVIDER_IO_LOCK:
                    self._validate_expert_root_binding(authority)
            except BaseException as error:
                self._raise_after_expert_root_failure(authority, error)
            transitions.append(
                (authority, authority.sessions_identity)
            )
        return tuple(transitions)

    def _commit_expert_sessions_identity_refresh(
        self,
        transitions: tuple[
            tuple[_ExpertRootGrantAuthority, _FileIdentity],
            ...,
        ],
    ) -> None:
        for authority, before in transitions:
            try:
                self._validate_expert_root_authority_locked(
                    authority,
                    authority.clock_capability,
                )
                if authority.sessions_identity != before:
                    raise RetentionError(
                        "expert_sessions_identity_transition_invalid"
                    )
                with _PROVIDER_IO_LOCK:
                    original_value = os.fstat(self._sessions_fd)
                    duplicate_value = os.fstat(authority.sessions_fd)
                    named_value = os.stat(
                        "sessions",
                        dir_fd=self._state_fd,
                        follow_symlinks=False,
                    )
                    for value in (
                        original_value,
                        duplicate_value,
                        named_value,
                    ):
                        _validate_directory_stat(value)
                    current = _file_identity(original_value)
                    if (
                        _file_identity(duplicate_value) != current
                        or _file_identity(named_value) != current
                        or (
                            current.device,
                            current.inode,
                            current.mode,
                            current.owner,
                        )
                        != (
                            before.device,
                            before.inode,
                            before.mode,
                            before.owner,
                        )
                        or current.links
                        not in (before.links, before.links - 1)
                    ):
                        raise RetentionError(
                            "expert_sessions_identity_transition_invalid"
                        )
                    authority.sessions_identity = current
                    self._validate_expert_root_binding(authority)
            except BaseException as error:
                self._raise_after_expert_root_failure(authority, error)

    def _release_reserve(
        self, capability: ProviderWalWriteCapability, state: _SessionState
    ) -> None:
        if state.reserve_identity is None:
            raise RetentionError("retention_reserve_already_released")
        try:
            _validate_named_fd(
                state.reserve_fd,
                state.marker.reserve_basename,
                self._sessions_fd,
                exact_size=RESERVE_BYTES,
                physical=True,
            )
            expert_sessions_transitions = (
                self._prepare_expert_sessions_identity_refresh()
            )
        except RetentionError:
            state.healthy = False
            _latch_global_halt(
                self, session_id=state.marker.session_id, ambiguous=False
            )
            raise RetentionGlobalHalt(
                "retention_reserve_validation_failed"
            )
        try:
            os.close(state.reserve_fd)
        except OSError as error:
            state.healthy = False
            _latch_global_halt(
                self, session_id=state.marker.session_id, ambiguous=False
            )
            raise RetentionGlobalHalt("retention_reserve_close_failed") from error
        state.reserve_fd = -1
        try:
            self._unlink_validated_reserve(state.marker.reserve_basename)
            _fsync_directory(self._sessions_fd)
            self._commit_expert_sessions_identity_refresh(
                expert_sessions_transitions
            )
        except Exception as error:
            state.healthy = False
            _latch_global_halt(
                self, session_id=state.marker.session_id, ambiguous=False
            )
            raise RetentionGlobalHalt("retention_reserve_release_failed") from error
        state.reserve_identity = None

    def _require_read_authority(
        self,
        capability: ProviderWalReadCapability,
    ) -> _ReadAuthority:
        if type(capability) is not ProviderWalReadCapability:
            raise RetentionError("retention_read_capability_invalid")
        authority = self._read_capabilities.get(capability)
        if authority is None:
            tombstone = self._read_tombstones.get(capability)
            if tombstone is not None and tombstone.lifecycle.due:
                raise RetentionDueDeleteError("retention_deadline_reached")
            raise RetentionError("retention_read_capability_stale")
        if (
            authority.capability is not capability
            or authority.coordinator is not self
            or authority.owner_pid != os.getpid()
            or authority.owner_thread is not threading.current_thread()
            or authority.generation != self._generation
        ):
            raise RetentionError("retention_read_capability_stale")
        return authority

    def _reject_replay_manifest(
        self,
        *,
        read_capability: ProviderWalReadCapability,
        persistence_authorizer: RetentionSessionAuthorizer,
        session_id: str,
    ) -> None:
        with self._condition:
            self._require_open()
            checked_session = _require_session_id(session_id)
            tombstone = self._read_tombstones.get(read_capability)
            try:
                from .sequencer import ProviderPersistenceAuthorizer

                if (
                    read_capability in self._read_capabilities
                    or type(persistence_authorizer)
                    is not ProviderPersistenceAuthorizer
                    or tombstone is None
                    or tombstone.coordinator is not self
                    or tombstone.session_id != checked_session
                    or tombstone.authorizer is not persistence_authorizer
                    or tombstone.manifest
                    is not persistence_authorizer.session_manifest
                    or tombstone.decision
                    is not persistence_authorizer.bound_decision
                    or tombstone.manifest_sha256
                    != session_manifest_sha256(tombstone.manifest)
                    or tombstone.binding_sha256
                    != tombstone.decision.provider_request_binding_sha256
                    or tombstone.owner_pid != os.getpid()
                    or tombstone.owner_thread
                    is not threading.current_thread()
                    or tombstone.generation != self._generation
                    or tombstone.lifecycle.due
                ):
                    raise RetentionError(
                        "retention_replay_rejection_invalid"
                    )
                self._require_authorizer_binding(
                    persistence_authorizer,
                    tombstone.manifest,
                    tombstone.decision,
                )
            except RetentionError:
                raise
            except Exception as error:
                raise RetentionError(
                    "retention_replay_rejection_invalid"
                ) from error
            _latch_global_halt(
                self,
                session_id=checked_session,
                ambiguous=False,
            )

    def _reject_expected_replay_manifest(
        self,
        *,
        expected_session_manifest_sha256: str,
        persistence_authorizer: RetentionSessionAuthorizer,
    ) -> None:
        with self._condition:
            self._require_open(ready=False)
            try:
                from .sequencer import ProviderPersistenceAuthorizer

                if (
                    type(persistence_authorizer)
                    is not ProviderPersistenceAuthorizer
                    or type(expected_session_manifest_sha256) is not str
                    or _SHA256.fullmatch(
                        expected_session_manifest_sha256
                    )
                    is None
                    or self._owner_pid != os.getpid()
                    or self._owner_thread
                    is not threading.current_thread()
                ):
                    raise RetentionError(
                        "retention_expected_replay_rejection_invalid"
                    )
                manifest = persistence_authorizer.session_manifest
                decision = persistence_authorizer.bound_decision
                if (
                    persistence_authorizer.coordinator is not self
                    or type(manifest) is not SessionManifest
                    or type(decision) is not QualificationDecision
                ):
                    raise RetentionError(
                        "retention_expected_replay_rejection_invalid"
                    )
                actual_digest = session_manifest_sha256(manifest)
                if expected_session_manifest_sha256 == actual_digest:
                    raise RetentionError(
                        "retention_expected_replay_rejection_invalid"
                    )
                self._require_authorizer_binding(
                    persistence_authorizer,
                    manifest,
                    decision,
                )
            except RetentionError:
                raise
            except Exception as error:
                raise RetentionError(
                    "retention_expected_replay_rejection_invalid"
                ) from error
            _latch_global_halt(
                self,
                session_id=manifest.session_id,
                ambiguous=False,
            )

    def _validate_read_capability(
        self, capability: ProviderWalReadCapability
    ) -> tuple[_SessionState, RetentionMarker, _ReadAuthority]:
        _raise_if_global_halt()
        authority = self._require_read_authority(capability)
        if (
            self._closed
            or self._closing
            or not self._ready
        ):
            raise RetentionError("retention_read_capability_stale")
        state = self._session_states.get(authority.session_id)
        if state is None or not state.healthy:
            raise RetentionError("retention_read_session_invalid")
        try:
            marker = self._load_named_marker(authority.session_id)
            if (
                marker != state.marker
                or marker.session_manifest_sha256
                != authority.manifest_sha256
                or marker.provider_request_binding_sha256
                != authority.binding_sha256
            ):
                raise RetentionError("retention_read_binding_changed")
            if self._entry_exists(marker.reserve_basename):
                raise RetentionError("retention_reserve_still_present")
            now = self._sample_clock()
            if now >= marker.delete_by_ns:
                has_reserve = self._entry_exists(marker.reserve_basename)
                self._delete_due_marker(
                    marker,
                    has_reserve=has_reserve,
                )
                raise RetentionDueDeleteError("retention_deadline_reached")
            identity = _validate_named_fd(
                authority.fd,
                marker.wal_basename,
                self._sessions_fd,
            )
            if (
                identity != authority.wal_identity
                or identity != state.wal_identity
            ):
                raise RetentionError("retention_wal_substituted")
        except RetentionDueDeleteError:
            raise
        except RetentionGlobalHalt:
            raise
        except RetentionError:
            state.healthy = False
            self._halt(session_id=authority.session_id, ambiguous=False)
        return state, marker, authority

    def _pread_capability(
        self,
        capability: ProviderWalReadCapability,
        *,
        offset: int,
        length: int,
    ) -> bytes:
        if (
            type(offset) is not int
            or type(length) is not int
            or offset < 0
            or length < 0
        ):
            raise TypeError("pread offset and length must be nonnegative integers")
        with self._condition:
            state, marker, authority = self._validate_read_capability(capability)
            authorizer = authority.authorizer
            try:
                self._require_authorizer_binding(
                    authorizer,
                    authority.manifest,
                    authority.decision,
                    marker=marker,
                )
                decision = authorizer.authorize_analysis()
                if decision is not authority.decision:
                    raise RetentionError("retention_analysis_decision_changed")
                self._require_authorizer_binding(
                    authorizer,
                    authority.manifest,
                    decision,
                    marker=marker,
                )
            except RetentionError:
                raise
            except Exception as error:
                raise RetentionError("retention_analysis_authorization_failed") from error
            state, marker, authority = self._validate_read_capability(capability)
            self._require_authorizer_binding(
                authority.authorizer,
                authority.manifest,
                authority.decision,
                marker=marker,
            )
            content: bytes | None = None
            io_error: OSError | None = None
            with _PROVIDER_IO_LOCK:
                _raise_if_global_halt()
                try:
                    content = os.pread(authority.fd, length, offset)
                except OSError as error:
                    io_error = error
            if io_error is not None:
                state.healthy = False
                _latch_global_halt(
                    self,
                    session_id=authority.session_id,
                    ambiguous=False,
                )
                raise RetentionGlobalHalt(
                    "retention_wal_pread_failed"
                ) from io_error
            assert content is not None
            return content

    def _close_write_capability(
        self, capability: ProviderWalWriteCapability
    ) -> None:
        with self._condition:
            authority = self._require_write_authority(capability)
            try:
                self._close_write_locked(capability, strict=True)
            except RetentionError as error:
                _latch_global_halt(
                    self, session_id=authority.session_id, ambiguous=False
                )
                raise RetentionGlobalHalt(
                    "retention_write_capability_close_failed"
                ) from error

    def _close_write_locked(
        self,
        capability: ProviderWalWriteCapability,
        *,
        strict: bool,
        due: bool = False,
    ) -> None:
        authority = self._write_capabilities.pop(capability, None)
        if authority is None:
            return
        if due:
            authority.lifecycle.due = True
        self._write_tombstones[capability] = authority.lifecycle
        state = self._session_states.get(authority.session_id)
        if state is not None:
            if state.write_token is authority.token:
                state.write_token = None
            failure: OSError | None = None
            for name in ("wal_fd", "reserve_fd"):
                fd = getattr(state, name)
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError as error:
                        failure = failure or error
                    setattr(state, name, -1)
            if failure is not None:
                state.healthy = False
                if strict:
                    raise RetentionError(
                        "retention_capability_descriptor_close_failed"
                    ) from failure

    def _close_read_capability(
        self, capability: ProviderWalReadCapability
    ) -> None:
        with self._condition:
            authority = self._require_read_authority(capability)
            try:
                self._close_read_locked(capability, strict=True)
            except RetentionError as error:
                _latch_global_halt(
                    self, session_id=authority.session_id, ambiguous=False
                )
                raise RetentionGlobalHalt(
                    "retention_read_capability_close_failed"
                ) from error

    def _close_read_locked(
        self,
        capability: ProviderWalReadCapability,
        *,
        strict: bool,
        due: bool = False,
    ) -> None:
        authority = self._read_capabilities.pop(capability, None)
        if authority is None:
            return
        if due:
            authority.lifecycle.due = True
        if authority.fd >= 0:
            try:
                os.close(authority.fd)
            except OSError as error:
                authority.fd = -1
                if strict:
                    raise RetentionError(
                        "retention_capability_descriptor_close_failed"
                    ) from error
            authority.fd = -1
        self._read_tombstones[capability] = _ReadTombstone(
            coordinator=self,
            session_id=authority.session_id,
            manifest_sha256=authority.manifest_sha256,
            binding_sha256=authority.binding_sha256,
            manifest=authority.manifest,
            decision=authority.decision,
            authorizer=authority.authorizer,
            owner_pid=authority.owner_pid,
            owner_thread=authority.owner_thread,
            generation=authority.generation,
            lifecycle=authority.lifecycle,
        )

    def _revoke_session_locked(
        self, session_id: str, *, due: bool = False
    ) -> None:
        for authority in tuple(self._write_capabilities.values()):
            if authority.session_id == session_id:
                self._close_write_locked(
                    authority.capability,
                    strict=True,
                    due=due,
                )
        for authority in tuple(self._read_capabilities.values()):
            if authority.session_id == session_id:
                self._close_read_locked(
                    authority.capability,
                    strict=True,
                    due=due,
                )

    def _revoke_reads_for_global_halt(self) -> None:
        if not hasattr(self, "_lock"):
            return
        with self._lock:
            for authority in tuple(self._read_capabilities.values()):
                self._close_read_locked(
                    authority.capability,
                    strict=False,
                )

    def _expiry_worker(self) -> None:
        try:
            with self._condition:
                while not self._closing:
                    if not self._ready or not self._deadlines:
                        self._condition.wait()
                        continue
                    deadline = min(self._deadlines.values())
                    now = self._sample_clock()
                    if now >= deadline:
                        try:
                            self._recover_locked()
                        except RetentionDueDeleteError:
                            # The failing delete already installed the scoped
                            # process-wide latch and preserved its immediate
                            # exception semantics.
                            return
                        except Exception:
                            _latch_global_halt(
                                self, session_id=None, ambiguous=True
                            )
                            self._ambiguous_halt = True
                            return
                        continue
                    timeout = min((deadline - now) / 1_000_000_000, 3600.0)
                    self._condition.wait(timeout=timeout)
        except Exception:
            _latch_global_halt(self, session_id=None, ambiguous=True)
            self._ambiguous_halt = True

    def close(self) -> None:
        if not hasattr(self, "_lock"):
            return
        expert_duplicate_fds: list[int] = []
        deferred_close_halt = False
        with self._condition:
            if self._closed:
                return
            if self._closing:
                while not self._closed and not self._close_failed:
                    self._condition.wait()
                if self._closed:
                    return
                raise RetentionGlobalHalt(
                    "retention_descriptor_close_failed"
                )
            self._closing = True
            self._ready = False
            self._generation += 1
            self._condition.notify_all()
            while self._expert_root_operations_inflight:
                self._condition.wait()
            close_failed = False
            self._expert_root_requests.clear()
            for expert_authority in tuple(
                self._expert_root_grants.values()
            ):
                expert_duplicate_fds.extend(
                    self._invalidate_expert_root_grant_locked(
                        expert_authority
                    )
                )
            self._expert_clock_capabilities.clear()
            for authority in tuple(self._read_capabilities.values()):
                try:
                    self._close_read_locked(
                        authority.capability, strict=True
                    )
                except RetentionError:
                    close_failed = True
            for item in tuple(self._write_capabilities.values()):
                try:
                    self._close_write_locked(
                        item.capability,
                        strict=True,
                    )
                except RetentionError:
                    close_failed = True
            if close_failed:
                self._close_failed = True
                deferred_close_halt = True
            self._condition.notify_all()
            worker = self._worker
        if expert_duplicate_fds:
            with _PROVIDER_IO_LOCK:
                expert_close_failed = self._close_expert_root_duplicate_fds(
                    tuple(expert_duplicate_fds)
                )
            if expert_close_failed:
                with self._condition:
                    self._close_failed = True
                    self._condition.notify_all()
                deferred_close_halt = True
        if deferred_close_halt:
            _latch_global_halt(
                self,
                session_id=None,
                ambiguous=True,
            )
        if worker is not threading.current_thread():
            worker.join(timeout=5.0)
            if worker.is_alive():
                with self._condition:
                    self._close_failed = True
                    self._condition.notify_all()
                _latch_global_halt(self, session_id=None, ambiguous=True)
                raise RetentionGlobalHalt("retention_worker_did_not_stop")
        descriptor_error: OSError | None = None
        descriptor_failure = "retention_descriptor_close_failed"
        with self._lock:
            if self._closed:
                return
            if self._close_failed:
                raise RetentionGlobalHalt("retention_descriptor_close_failed")
            for fd_name in (
                "_markers_fd",
                "_sessions_fd",
                "_state_fd",
            ):
                fd = getattr(self, fd_name)
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError as error:
                        self._close_failed = True
                        descriptor_error = error
                        break
                    setattr(self, fd_name, -1)
            if descriptor_error is None:
                try:
                    os.close(self._lock_fd)
                except OSError as error:
                    self._close_failed = True
                    descriptor_error = error
                    descriptor_failure = "retention_lock_close_failed"
                else:
                    self._lock_fd = -1
                    self._closed = True
            self._condition.notify_all()
        if descriptor_error is not None:
            _latch_global_halt(
                self,
                session_id=None,
                ambiguous=True,
            )
            raise RetentionGlobalHalt(
                descriptor_failure
            ) from descriptor_error
        with _GLOBAL_HALT_LOCK:
            _ACTIVE_COORDINATORS.discard(self)


def _terminal_frame_clean(
    content: bytes, state: _SessionState
) -> bool | object | None:
    if content[:4] != FRAME_MAGIC:
        return None
    if len(content) < FRAME_PREFIX.size:
        raise RetentionError("retention_terminal_frame_invalid")
    try:
        (
            magic,
            version,
            kind,
            flags,
            ingest_seq,
            total,
            metadata_length,
            payload_length,
        ) = FRAME_PREFIX.unpack(content[: FRAME_PREFIX.size])
    except struct.error as error:
        raise RetentionError("retention_terminal_frame_invalid") from error
    if (
        magic != FRAME_MAGIC
        or version != FRAME_VERSION
        or flags != FRAME_FLAGS
        or total != 76 + metadata_length + payload_length
        or total != len(content)
        or total > MAX_FRAME_BYTES
        or total < 76
    ):
        raise RetentionError("retention_terminal_frame_invalid")
    metadata_start = FRAME_PREFIX.size
    payload_start = metadata_start + metadata_length
    digest_start = payload_start + payload_length
    trailer_start = digest_start + 32
    if trailer_start + FRAME_TRAILER.size != len(content):
        raise RetentionError("retention_terminal_frame_invalid")
    prefix = content[:metadata_start]
    metadata = content[metadata_start:payload_start]
    payload = content[payload_start:digest_start]
    digest = content[digest_start:trailer_start]
    try:
        repeated, trailer_magic = FRAME_TRAILER.unpack(content[trailer_start:])
    except struct.error as error:
        raise RetentionError("retention_terminal_frame_invalid") from error
    if (
        repeated != total
        or trailer_magic != TRAILER_MAGIC
        or digest
        != hashlib.sha256(
            FRAME_DIGEST_DOMAIN + prefix + metadata + payload
        ).digest()
    ):
        raise RetentionError("retention_terminal_frame_invalid")
    try:
        event = decode_record(metadata, payload)
    except (TypeError, ValueError, RecordCodecError) as error:
        raise RetentionError("retention_terminal_record_invalid") from error
    expected_kind = {
        RecordKind.RAW: 1,
        RecordKind.DERIVED: 2,
        RecordKind.CONTROL: FRAME_CONTROL_KIND,
    }[event.record_kind]
    if kind != expected_kind or event.ingest_seq != ingest_seq:
        raise RetentionError("retention_terminal_record_invalid")
    if event.event_type == "SESSION_START":
        manifest = state.manifest
        if (
            manifest is None
            or event.record_kind is not RecordKind.CONTROL
            or event.ingest_seq != 1
            or event.session_id != state.marker.session_id
            or event.local_wall_ns != manifest.created_wall_ns
            or event.local_monotonic_ns != 0
            or payload != canonical_session_manifest_bytes(manifest)
        ):
            raise RetentionError("retention_session_start_binding_invalid")
        return _SESSION_START_FRAME
    if event.event_type != "SESSION_HALT":
        return None
    if (
        event.record_kind is not RecordKind.CONTROL
        or event.session_id != state.marker.session_id
    ):
        raise RetentionError("retention_terminal_binding_invalid")
    raw = _strict_json(payload, _TERMINAL_KEYS)
    if (
        raw["terminal_version"] != 1
        or type(raw["terminal_version"]) is not int
        or type(raw["clean"]) is not bool
        or type(raw["reason"]) is not str
        or not raw["reason"]
        or raw["research_evaluable"] is not False
    ):
        raise RetentionError("retention_terminal_payload_invalid")
    allowed_reasons = (
        _CLEAN_TERMINAL_REASONS
        if raw["clean"]
        else _HALTED_TERMINAL_REASONS
    )
    if raw["reason"] not in allowed_reasons:
        raise RetentionError("retention_terminal_reason_invalid")
    for name in (
        "trace_sha256",
        "final_state_sha256",
        "config_file_sha256",
        "config_canonical_sha256",
        "code_sha256",
        "session_manifest_sha256",
        "provider_manifest_file_sha256",
        "provider_manifest_canonical_sha256",
        "entitlement_id_sha256",
        "permission_artifact_sha256",
        "qualification_artifact_sha256",
        "qualification_trace_sha256",
        "adapter_code_sha256",
        "auth_contract_sha256",
        "quota_contract_sha256",
    ):
        if type(raw[name]) is not str or _SHA256.fullmatch(raw[name]) is None:
            raise RetentionError("retention_terminal_digest_invalid")
    for name in (
        "record_count_before_terminal",
        "raw_count",
        "derived_count",
        "last_applied_raw_seq",
        "required_retention_until_ns",
    ):
        if type(raw[name]) is not int or raw[name] < 0:
            raise RetentionError("retention_terminal_count_invalid")
    manifest = state.manifest
    if manifest is None:
        raise RetentionError("retention_terminal_manifest_missing")
    expected = {
        "config_file_sha256": manifest.config_file_sha256,
        "config_canonical_sha256": manifest.config_canonical_sha256,
        "code_sha256": manifest.code_sha256,
        "session_manifest_sha256": state.marker.session_manifest_sha256,
        "provider_manifest_file_sha256": manifest.provider_manifest_file_sha256,
        "provider_manifest_canonical_sha256": (
            manifest.provider_manifest_canonical_sha256
        ),
        "entitlement_id_sha256": manifest.entitlement_id_sha256,
        "permission_artifact_sha256": manifest.permission_artifact_sha256,
        "qualification_artifact_sha256": (
            manifest.qualification_artifact_sha256
        ),
        "qualification_trace_sha256": manifest.qualification_trace_sha256,
        "adapter_code_sha256": manifest.adapter_code_sha256,
        "auth_contract_sha256": manifest.auth_contract_sha256,
        "quota_contract_sha256": manifest.quota_contract_sha256,
        "required_retention_until_ns": state.marker.delete_by_ns,
    }
    if any(raw[name] != value for name, value in expected.items()):
        raise RetentionError("retention_terminal_binding_invalid")
    if (
        raw["record_count_before_terminal"] != ingest_seq - 1
        or raw["raw_count"] + raw["derived_count"] + 1
        > raw["record_count_before_terminal"]
        or raw["last_applied_raw_seq"] >= ingest_seq
    ):
        raise RetentionError("retention_terminal_count_invalid")
    return raw["clean"]  # type: ignore[return-value]


def _session_start_like_frame(content: bytes) -> bool:
    if content[:4] != FRAME_MAGIC or len(content) < FRAME_PREFIX.size:
        return False
    try:
        (
            _magic,
            _version,
            _kind,
            _flags,
            ingest_seq,
            _total,
            metadata_length,
            _payload_length,
        ) = FRAME_PREFIX.unpack(content[: FRAME_PREFIX.size])
    except struct.error:
        return False
    if ingest_seq == 1:
        return True
    metadata_start = FRAME_PREFIX.size
    metadata_end = metadata_start + metadata_length
    if metadata_end > len(content):
        return False
    metadata = content[metadata_start:metadata_end]
    try:
        value = json.loads(
            metadata.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (
        RetentionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        return b'"event_type":"SESSION_START"' in metadata
    return (
        type(value) is dict
        and value.get("event_type") == "SESSION_START"
    )
