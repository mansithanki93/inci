"""Descriptor-safe synthetic candidate lock for the root-v6 account path."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import fcntl
from hashlib import sha256
import os
from pathlib import Path
import pwd
import stat
import threading
import weakref

from tennis_v1.canonical import canonical_json_bytes


_ENVIRONMENT = "production"
_MIN_SUBACCOUNT = 0
_MAX_SUBACCOUNT = 32
_DIRECTORY_COMPONENTS = (".local", "state", "inci", "production")
_LOCK_BASENAME = "inci.lock"
_HELD = "HELD"
_RELEASED = "RELEASED"
_RELEASE_UNCERTAIN = "RELEASE_UNCERTAIN"
_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_MKDIR_SUPPORTS_DIR_FD = os.mkdir in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd
_STAT_SUPPORTS_NOFOLLOW = os.stat in os.supports_follow_symlinks
_CHMOD_SUPPORTS_DIR_FD = os.chmod in os.supports_dir_fd
_CHMOD_SUPPORTS_NOFOLLOW = os.chmod in os.supports_follow_symlinks


class CandidateAccountLockError(RuntimeError):
    """A fixed, redacted candidate account-lock failure."""


@dataclass(frozen=True, slots=True)
class _Identity:
    device: int
    inode: int
    mode: int
    owner: int
    links: int


@dataclass(slots=True)
class _LockAuthority:
    home_path: str
    component_names: tuple[str, ...]
    directory_fds: tuple[int, ...]
    lock_fd: int
    observation: tuple[tuple[_Identity, ...], _Identity]
    owner_pid: int
    owner_thread: threading.Thread
    lifecycle: str = _HELD


class CandidateAccountLockV1:
    """Opaque same-owner release capability for one held account lock."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("candidate account locks are acquired")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("candidate account locks cannot be subclassed")

    def __repr__(self) -> str:
        return "<CandidateAccountLockV1 redacted>"

    def __copy__(self):
        raise TypeError("candidate account locks cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("candidate account locks cannot be copied")

    def __reduce__(self):
        raise TypeError("candidate account locks cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("candidate account locks cannot be pickled")

    def __getstate__(self):
        raise TypeError("candidate account locks cannot be pickled")

    def release(self) -> None:
        _release_candidate_account_lock(self)


def _make_authority_registry():
    entries: dict[
        int,
        tuple[
            weakref.ReferenceType[CandidateAccountLockV1],
            _LockAuthority,
        ],
    ] = {}
    guard = threading.RLock()

    def reset_guard_after_fork() -> None:
        nonlocal guard
        guard = threading.RLock()

    if hasattr(os, "register_at_fork"):
        os.register_at_fork(after_in_child=reset_guard_after_fork)

    def register(
        item: CandidateAccountLockV1,
        authority: _LockAuthority,
    ) -> None:
        key = id(item)

        def discard(
            reference: weakref.ReferenceType[CandidateAccountLockV1],
        ) -> None:
            with guard:
                current = entries.get(key)
                if current is not None and current[0] is reference:
                    entries.pop(key, None)

        reference = weakref.ref(item, discard)
        with guard:
            current = entries.get(key)
            if current is not None and current[0]() is not None:
                raise RuntimeError("candidate account lock registry collision")
            entries[key] = (reference, authority)

    def lookup(
        item: CandidateAccountLockV1,
    ) -> _LockAuthority | None:
        with guard:
            current = entries.get(id(item))
            if current is None or current[0]() is not item:
                return None
            return current[1]

    return register, lookup


_register_authority, _lookup_authority = _make_authority_registry()


def _error(code: str) -> CandidateAccountLockError:
    return CandidateAccountLockError(code)


def _mode(value: os.stat_result) -> int:
    return stat.S_IMODE(value.st_mode)


def _identity(value: os.stat_result) -> _Identity:
    return _Identity(
        device=value.st_dev,
        inode=value.st_ino,
        mode=value.st_mode,
        owner=value.st_uid,
        links=value.st_nlink,
    )


def _require_arguments(environment: object, subaccount: object) -> int:
    if type(environment) is not str or environment != _ENVIRONMENT:
        raise _error("candidate_account_lock_environment_invalid")
    if (
        type(subaccount) is not int
        or subaccount < _MIN_SUBACCOUNT
        or subaccount > _MAX_SUBACCOUNT
    ):
        raise _error("candidate_account_lock_subaccount_invalid")
    return subaccount


def _passwd_home() -> str:
    try:
        value = pwd.getpwuid(os.getuid()).pw_dir
    except Exception:
        raise _error("candidate_account_lock_passwd_home_invalid") from None
    if (
        type(value) is not str
        or value == ""
        or not os.path.isabs(value)
        or os.path.normpath(value) != value
    ):
        raise _error("candidate_account_lock_passwd_home_invalid")
    return value


def _path_inputs(
    *,
    environment: object,
    subaccount: object,
) -> tuple[str, tuple[str, ...], Path]:
    checked_subaccount = _require_arguments(environment, subaccount)
    home = _passwd_home()
    components = (
        *_DIRECTORY_COMPONENTS,
        f"subaccount-{checked_subaccount}",
    )
    path = Path(home).joinpath(*components, _LOCK_BASENAME)
    return home, components, path


def derive_candidate_account_lock_path(
    *,
    environment: str,
    subaccount: int,
) -> Path:
    """Derive the one root-v6-compatible production lock path."""

    _, _, path = _path_inputs(
        environment=environment,
        subaccount=subaccount,
    )
    return path


def _require_platform() -> None:
    required_os_flags = (
        "O_CLOEXEC",
        "O_DIRECTORY",
        "O_NOFOLLOW",
    )
    required_flock_flags = (
        "LOCK_EX",
        "LOCK_NB",
        "LOCK_UN",
    )
    required_os_callables = (
        "close",
        "fchmod",
        "fstat",
        "geteuid",
        "getpid",
        "getuid",
        "mkdir",
        "open",
        "stat",
    )
    if (
        any(
            type(getattr(os, name, None)) is not int
            or getattr(os, name) == 0
            for name in required_os_flags
        )
        or any(
            type(getattr(fcntl, name, None)) is not int
            or getattr(fcntl, name) == 0
            for name in required_flock_flags
        )
        or any(
            not callable(getattr(os, name, None))
            for name in required_os_callables
        )
        or not callable(getattr(fcntl, "flock", None))
        or not _OPEN_SUPPORTS_DIR_FD
        or not _MKDIR_SUPPORTS_DIR_FD
        or not _STAT_SUPPORTS_DIR_FD
        or not _STAT_SUPPORTS_NOFOLLOW
    ):
        raise _error("candidate_account_lock_platform_unsupported")


def _validate_directory_stat(value: os.stat_result) -> _Identity:
    if (
        not stat.S_ISDIR(value.st_mode)
        or value.st_uid != os.geteuid()
        or _mode(value) & 0o022
    ):
        raise _error("candidate_account_lock_directory_invalid")
    return _identity(value)


def _validate_file_stat(value: os.stat_result) -> _Identity:
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.geteuid()
        or value.st_nlink != 1
        or _mode(value) & 0o111
        or _mode(value) & 0o022
    ):
        raise _error("candidate_account_lock_file_invalid")
    return _identity(value)


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_CLOEXEC
        | os.O_NOFOLLOW
    )


def _lock_flags() -> int:
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    return flags


def _close_failed_open(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        raise _error("candidate_account_lock_acquire_uncertain") from None


def _open_home(home: str) -> int:
    try:
        descriptor = os.open(home, _directory_flags())
        _validate_directory_stat(os.fstat(descriptor))
        return descriptor
    except CandidateAccountLockError:
        if "descriptor" in locals():
            _close_failed_open(descriptor)
        raise
    except OSError:
        if "descriptor" in locals():
            _close_failed_open(descriptor)
        raise _error("candidate_account_lock_directory_invalid") from None


def _open_directory(parent_fd: int, name: str) -> int:
    created = False
    try:
        try:
            descriptor = os.open(
                name,
                _directory_flags(),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                created = True
            except FileExistsError:
                pass
            descriptor = os.open(
                name,
                _directory_flags(),
                dir_fd=parent_fd,
            )
        if created:
            os.fchmod(descriptor, 0o700)
        value = os.fstat(descriptor)
        _validate_directory_stat(value)
        if created and _mode(value) != 0o700:
            raise _error("candidate_account_lock_directory_invalid")
        return descriptor
    except CandidateAccountLockError:
        if "descriptor" in locals():
            _close_failed_open(descriptor)
        raise
    except OSError:
        if "descriptor" in locals():
            _close_failed_open(descriptor)
        raise _error("candidate_account_lock_directory_invalid") from None


def _open_lock(directory_fd: int) -> tuple[int, bool]:
    flags = _lock_flags()
    created = False
    try:
        try:
            descriptor = os.open(
                _LOCK_BASENAME,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=directory_fd,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(
                _LOCK_BASENAME,
                flags,
                dir_fd=directory_fd,
            )
        if created:
            os.fchmod(descriptor, 0o600)
        value = os.fstat(descriptor)
        _validate_file_stat(value)
        if created and (_mode(value) != 0o600 or value.st_size != 0):
            raise _error("candidate_account_lock_file_invalid")
        return descriptor, created
    except CandidateAccountLockError:
        if "descriptor" in locals():
            _close_failed_open(descriptor)
        raise
    except OSError:
        if "descriptor" in locals():
            _close_failed_open(descriptor)
        raise _error("candidate_account_lock_file_invalid") from None


def _observe_bindings(
    *,
    home_path: str,
    component_names: tuple[str, ...],
    directory_fds: tuple[int, ...],
    lock_fd: int,
    normalize_drift: bool,
) -> tuple[tuple[_Identity, ...], _Identity]:
    try:
        before_directories = tuple(
            _validate_directory_stat(os.fstat(fd))
            for fd in directory_fds
        )
        named_home = _validate_directory_stat(
            os.stat(home_path, follow_symlinks=False)
        )
        if named_home != before_directories[0]:
            raise _error("candidate_account_lock_path_replaced")
        for index, name in enumerate(component_names):
            parent_fd = directory_fds[index]
            child_identity = before_directories[index + 1]
            named = _validate_directory_stat(
                os.stat(
                    name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            )
            if named != child_identity:
                raise _error("candidate_account_lock_path_replaced")
        before_lock = _validate_file_stat(os.fstat(lock_fd))
        named_lock = _validate_file_stat(
            os.stat(
                _LOCK_BASENAME,
                dir_fd=directory_fds[-1],
                follow_symlinks=False,
            )
        )
        if named_lock != before_lock:
            raise _error("candidate_account_lock_path_replaced")
        after_directories = tuple(
            _validate_directory_stat(os.fstat(fd))
            for fd in directory_fds
        )
        after_lock = _validate_file_stat(os.fstat(lock_fd))
        if (
            after_directories != before_directories
            or after_lock != before_lock
        ):
            raise _error("candidate_account_lock_path_replaced")
        return after_directories, after_lock
    except CandidateAccountLockError:
        if normalize_drift:
            raise _error("candidate_account_lock_path_replaced") from None
        raise
    except OSError:
        raise _error("candidate_account_lock_path_replaced") from None


def _cleanup_descriptors(
    *,
    lock_fd: int,
    directory_fds: tuple[int, ...],
    unlock: bool,
) -> bool:
    uncertain = False
    if lock_fd >= 0:
        if unlock:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                uncertain = True
        try:
            os.close(lock_fd)
        except OSError:
            uncertain = True
    for descriptor in reversed(directory_fds):
        try:
            os.close(descriptor)
        except OSError:
            uncertain = True
    return uncertain


def _build_lock(authority: _LockAuthority) -> CandidateAccountLockV1:
    item = object.__new__(CandidateAccountLockV1)
    _register_authority(item, authority)
    return item


def acquire_candidate_account_lock(
    *,
    environment: str,
    subaccount: int,
) -> CandidateAccountLockV1:
    """Acquire the canonical production/subaccount lock without blocking."""

    home, components, _ = _path_inputs(
        environment=environment,
        subaccount=subaccount,
    )
    _require_platform()
    directory_descriptors: list[int] = []
    lock_fd = -1
    flock_held = False
    try:
        home_fd = _open_home(home)
        directory_descriptors.append(home_fd)
        for component in components:
            child = _open_directory(
                directory_descriptors[-1],
                component,
            )
            directory_descriptors.append(child)
        lock_fd, _ = _open_lock(directory_descriptors[-1])
        directory_tuple = tuple(directory_descriptors)
        before = _observe_bindings(
            home_path=home,
            component_names=components,
            directory_fds=directory_tuple,
            lock_fd=lock_fd,
            normalize_drift=False,
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            flock_held = True
        except BlockingIOError:
            raise _error("candidate_account_lock_contended") from None
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise _error("candidate_account_lock_contended") from None
            raise _error("candidate_account_lock_acquire_failed") from None
        after = _observe_bindings(
            home_path=home,
            component_names=components,
            directory_fds=directory_tuple,
            lock_fd=lock_fd,
            normalize_drift=True,
        )
        if after != before:
            raise _error("candidate_account_lock_path_replaced")
        authority = _LockAuthority(
            home_path=home,
            component_names=components,
            directory_fds=directory_tuple,
            lock_fd=lock_fd,
            observation=after,
            owner_pid=os.getpid(),
            owner_thread=threading.current_thread(),
        )
        return _build_lock(authority)
    except BaseException as failure:
        uncertain = _cleanup_descriptors(
            lock_fd=lock_fd,
            directory_fds=tuple(directory_descriptors),
            unlock=flock_held,
        )
        if uncertain:
            raise _error(
                "candidate_account_lock_acquire_uncertain"
            ) from None
        if isinstance(failure, CandidateAccountLockError):
            raise failure from None
        if not isinstance(failure, Exception):
            raise
        raise _error("candidate_account_lock_acquire_failed") from None


def _release_candidate_account_lock(item: CandidateAccountLockV1) -> None:
    if type(item) is not CandidateAccountLockV1:
        raise _error("candidate_account_lock_stale")
    authority = _lookup_authority(item)
    if type(authority) is not _LockAuthority:
        raise _error("candidate_account_lock_stale")
    if os.getpid() != authority.owner_pid:
        raise _error("candidate_account_lock_fork_invalid")
    if threading.current_thread() is not authority.owner_thread:
        raise _error("candidate_account_lock_thread_invalid")
    if authority.lifecycle == _RELEASED:
        return
    if authority.lifecycle == _RELEASE_UNCERTAIN:
        raise _error("candidate_account_lock_release_uncertain")
    if authority.lifecycle != _HELD:
        raise _error("candidate_account_lock_stale")

    release_error: CandidateAccountLockError | None = None
    try:
        current = _observe_bindings(
            home_path=authority.home_path,
            component_names=authority.component_names,
            directory_fds=authority.directory_fds,
            lock_fd=authority.lock_fd,
            normalize_drift=True,
        )
        if current != authority.observation:
            release_error = _error(
                "candidate_account_lock_path_replaced"
            )
    except CandidateAccountLockError as error:
        release_error = error

    uncertain = _cleanup_descriptors(
        lock_fd=authority.lock_fd,
        directory_fds=authority.directory_fds,
        unlock=True,
    )
    if uncertain:
        authority.lifecycle = _RELEASE_UNCERTAIN
        authority.lock_fd = -1
        authority.directory_fds = ()
        raise _error("candidate_account_lock_release_uncertain")

    authority.lifecycle = _RELEASED
    authority.lock_fd = -1
    authority.directory_fds = ()
    if release_error is not None:
        raise release_error from None


# Production account-lock protocol.  This is deliberately independent from the
# candidate capability above: neither capability type is accepted by the other
# protocol, even though both protocols bind the same root-v6 lock path.

_PRODUCTION_GRANT_FRESH = "FRESH_HELD"
_PRODUCTION_GRANT_CONSUMED = "CONSUMED_TO_LEASE"
_PRODUCTION_GRANT_REVOKED = "REVOKED"
_PRODUCTION_LEASE_HELD = "HELD"
_PRODUCTION_LEASE_RELEASED = "RELEASED"
_PRODUCTION_LEASE_RELEASE_UNCERTAIN = "RELEASE_UNCERTAIN"
_PRODUCTION_ROOTS_DOMAIN = b"INCI-LOCKED-PRODUCTION-STATE-ROOTS-V1\0"


class ProductionAccountLockError(RuntimeError):
    """A fixed, redacted production account-lock failure."""


class _ProductionOpaqueCapability:
    __slots__ = ("__weakref__",)

    _label = "production account-lock capability"

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError(f"{self._label} is privately issued")

    def __init_subclass__(cls, **kwargs: object) -> None:
        if cls.__module__ != __name__:
            raise TypeError(
                "production account-lock capabilities cannot be subclassed"
            )
        super().__init_subclass__(**kwargs)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} redacted>"

    def __copy__(self):
        raise TypeError("production account-lock capabilities cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("production account-lock capabilities cannot be copied")

    def __reduce__(self):
        raise TypeError("production account-lock capabilities cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("production account-lock capabilities cannot be pickled")

    def __getstate__(self):
        raise TypeError("production account-lock capabilities cannot be pickled")


class ProductionAccountLockGrantV1(_ProductionOpaqueCapability):
    """Opaque, same-process/thread authority to consume or revoke a lock."""

    __slots__ = ()
    _label = "production account-lock grant"

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("production account-lock grants cannot be subclassed")


class ProductionAccountLockLeaseV1(_ProductionOpaqueCapability):
    """Opaque, same-process/thread authority for one held production lock."""

    __slots__ = ()
    _label = "production account-lock lease"

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("production account-lock leases cannot be subclassed")


@dataclass(frozen=True, slots=True, init=False, weakref_slot=True)
class LockedProductionStateRootsV1:
    """State roots issued only while the matching production lease is held."""

    environment: str
    subaccount: int
    phase1_state_root: Path
    expert_state_root: Path
    roots_sha256: str

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("locked production state roots are privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("locked production state roots cannot be subclassed")

    def __copy__(self):
        raise TypeError("locked production state roots cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("locked production state roots cannot be copied")

    def __reduce__(self):
        raise TypeError("locked production state roots cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("locked production state roots cannot be pickled")

    def __getstate__(self):
        raise TypeError("locked production state roots cannot be pickled")


@dataclass(slots=True)
class _ProductionLockResource:
    environment: str
    subaccount: int
    home_path: str
    component_names: tuple[str, ...]
    directory_fds: tuple[int, ...]
    lock_fd: int
    observation: tuple[tuple[_Identity, ...], _Identity]
    owner_pid: int
    owner_thread: threading.Thread


@dataclass(slots=True)
class _ProductionGrantAuthority:
    resource: _ProductionLockResource
    lifecycle: str = _PRODUCTION_GRANT_FRESH


@dataclass(slots=True)
class _ProductionLeaseAuthority:
    resource: _ProductionLockResource
    lifecycle: str = _PRODUCTION_LEASE_HELD
    roots: LockedProductionStateRootsV1 | None = None


@dataclass(frozen=True, slots=True)
class _ProductionRootsAuthority:
    lease_reference: weakref.ReferenceType[ProductionAccountLockLeaseV1]
    lease_authority: _ProductionLeaseAuthority


_production_grant_entries: dict[
    int,
    tuple[
        weakref.ReferenceType[ProductionAccountLockGrantV1],
        _ProductionGrantAuthority,
    ],
] = {}
_production_lease_entries: dict[
    int,
    tuple[
        weakref.ReferenceType[ProductionAccountLockLeaseV1],
        _ProductionLeaseAuthority,
    ],
] = {}
_production_roots_entries: dict[
    int,
    tuple[
        weakref.ReferenceType[LockedProductionStateRootsV1],
        _ProductionRootsAuthority,
    ],
] = {}
_production_registry_guard = threading.RLock()
_production_acquisition_halted = False


def _reset_production_registry_guard_after_fork() -> None:
    global _production_registry_guard
    _production_registry_guard = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_production_registry_guard_after_fork)


def _production_error(code: str) -> ProductionAccountLockError:
    return ProductionAccountLockError(code)


def _production_mark_halted() -> None:
    global _production_acquisition_halted
    with _production_registry_guard:
        _production_acquisition_halted = True


def _production_require_not_halted() -> None:
    with _production_registry_guard:
        if _production_acquisition_halted:
            raise _production_error("production_account_lock_release_uncertain")


def _production_register(
    entries: dict[int, tuple[weakref.ReferenceType[object], object]],
    item: object,
    authority: object,
) -> None:
    key = id(item)

    def discard(reference: weakref.ReferenceType[object]) -> None:
        with _production_registry_guard:
            current = entries.get(key)
            if current is not None and current[0] is reference:
                entries.pop(key, None)

    reference = weakref.ref(item, discard)
    current = entries.get(key)
    if current is not None and current[0]() is not None:
        raise RuntimeError("production account-lock registry collision")
    entries[key] = (reference, authority)


def _production_lookup(
    entries: dict[int, tuple[weakref.ReferenceType[object], object]],
    item: object,
) -> object | None:
    current = entries.get(id(item))
    if current is None or current[0]() is not item:
        return None
    return current[1]


def _production_require_arguments(environment: object, subaccount: object) -> int:
    if type(environment) is not str or environment != _ENVIRONMENT:
        raise _production_error("production_account_lock_environment_invalid")
    if (
        type(subaccount) is not int
        or subaccount < _MIN_SUBACCOUNT
        or subaccount > _MAX_SUBACCOUNT
    ):
        raise _production_error("production_account_lock_subaccount_invalid")
    return subaccount


def _production_passwd_home() -> str:
    try:
        value = pwd.getpwuid(os.getuid()).pw_dir
    except Exception:
        raise _production_error(
            "production_account_lock_passwd_home_invalid"
        ) from None
    if (
        type(value) is not str
        or value == ""
        or not os.path.isabs(value)
        or os.path.normpath(value) != value
    ):
        raise _production_error("production_account_lock_passwd_home_invalid")
    return value


def _production_path_from_inputs(
    *,
    environment: object,
    subaccount: object,
) -> tuple[str, tuple[str, ...], Path]:
    checked_subaccount = _production_require_arguments(environment, subaccount)
    home = _production_passwd_home()
    components = (
        *_DIRECTORY_COMPONENTS,
        f"subaccount-{checked_subaccount}",
    )
    return home, components, Path(home).joinpath(*components, _LOCK_BASENAME)


def derive_production_account_lock_path(
    *,
    environment: str,
    subaccount: int,
) -> Path:
    """Derive the canonical root-v6 production account-lock path."""

    _, _, path = _production_path_from_inputs(
        environment=environment,
        subaccount=subaccount,
    )
    return path


def _production_require_platform() -> None:
    required_os_flags = ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW")
    required_flock_flags = ("LOCK_EX", "LOCK_NB", "LOCK_UN")
    required_os_callables = (
        "chmod",
        "close",
        "fchmod",
        "fstat",
        "fsync",
        "geteuid",
        "getpid",
        "getuid",
        "mkdir",
        "open",
        "stat",
    )
    if (
        any(
            type(getattr(os, name, None)) is not int
            or getattr(os, name) == 0
            for name in required_os_flags
        )
        or any(
            type(getattr(fcntl, name, None)) is not int
            or getattr(fcntl, name) == 0
            for name in required_flock_flags
        )
        or any(not callable(getattr(os, name, None)) for name in required_os_callables)
        or not callable(getattr(fcntl, "flock", None))
        or not _OPEN_SUPPORTS_DIR_FD
        or not _MKDIR_SUPPORTS_DIR_FD
        or not _STAT_SUPPORTS_DIR_FD
        or not _STAT_SUPPORTS_NOFOLLOW
        or not _CHMOD_SUPPORTS_DIR_FD
        or not _CHMOD_SUPPORTS_NOFOLLOW
    ):
        raise _production_error("production_account_lock_platform_unsupported")


def _production_validate_directory_stat(value: os.stat_result) -> _Identity:
    if (
        not stat.S_ISDIR(value.st_mode)
        or value.st_uid != os.geteuid()
        or _mode(value) & 0o022
    ):
        raise _production_error("production_account_lock_directory_invalid")
    return _identity(value)


def _production_validate_file_stat(value: os.stat_result) -> _Identity:
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.geteuid()
        or value.st_nlink != 1
        or _mode(value) not in (0o600, 0o644)
    ):
        raise _production_error("production_account_lock_file_invalid")
    return _identity(value)


def _production_close_failed_open(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except Exception:
        _production_mark_halted()
        raise _production_error("production_account_lock_release_uncertain") from None


def _production_open_home(home: str) -> int:
    try:
        descriptor = os.open(home, _directory_flags())
        _production_validate_directory_stat(os.fstat(descriptor))
        return descriptor
    except ProductionAccountLockError:
        if "descriptor" in locals():
            _production_close_failed_open(descriptor)
        raise
    except Exception:
        if "descriptor" in locals():
            _production_close_failed_open(descriptor)
        raise _production_error("production_account_lock_directory_invalid") from None


def _production_open_directory(parent_fd: int, name: str) -> int:
    created = False
    try:
        try:
            descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
        except FileNotFoundError:
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                created = True
            except FileExistsError:
                pass
            if created:
                os.chmod(
                    name,
                    0o700,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
        if created:
            os.fchmod(descriptor, 0o700)
        value = os.fstat(descriptor)
        _production_validate_directory_stat(value)
        if created and _mode(value) != 0o700:
            raise _production_error("production_account_lock_directory_invalid")
        return descriptor
    except ProductionAccountLockError:
        if "descriptor" in locals():
            _production_close_failed_open(descriptor)
        raise
    except Exception:
        if "descriptor" in locals():
            _production_close_failed_open(descriptor)
        raise _production_error("production_account_lock_directory_invalid") from None


def _production_open_lock(directory_fd: int) -> tuple[int, bool]:
    flags = _lock_flags()
    created = False
    try:
        try:
            descriptor = os.open(
                _LOCK_BASENAME,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=directory_fd,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(_LOCK_BASENAME, flags, dir_fd=directory_fd)
        if created:
            os.fchmod(descriptor, 0o600)
        value = os.fstat(descriptor)
        _production_validate_file_stat(value)
        if created and (_mode(value) != 0o600 or value.st_size != 0):
            raise _production_error("production_account_lock_file_invalid")
        if created:
            try:
                os.fsync(descriptor)
                os.fsync(directory_fd)
            except Exception:
                _production_mark_halted()
                raise _production_error(
                    "production_account_lock_release_uncertain"
                ) from None
        return descriptor, created
    except ProductionAccountLockError:
        if "descriptor" in locals():
            _production_close_failed_open(descriptor)
        raise
    except Exception:
        if "descriptor" in locals():
            _production_close_failed_open(descriptor)
        raise _production_error("production_account_lock_file_invalid") from None


def _production_observe_bindings(
    *,
    home_path: str,
    component_names: tuple[str, ...],
    directory_fds: tuple[int, ...],
    lock_fd: int,
    normalize_drift: bool,
) -> tuple[tuple[_Identity, ...], _Identity]:
    try:
        before_directories = tuple(
            _production_validate_directory_stat(os.fstat(fd))
            for fd in directory_fds
        )
        named_home = _production_validate_directory_stat(
            os.stat(home_path, follow_symlinks=False)
        )
        if named_home != before_directories[0]:
            raise _production_error("production_account_lock_path_replaced")
        for index, name in enumerate(component_names):
            named = _production_validate_directory_stat(
                os.stat(
                    name,
                    dir_fd=directory_fds[index],
                    follow_symlinks=False,
                )
            )
            if named != before_directories[index + 1]:
                raise _production_error("production_account_lock_path_replaced")
        before_lock = _production_validate_file_stat(os.fstat(lock_fd))
        named_lock = _production_validate_file_stat(
            os.stat(
                _LOCK_BASENAME,
                dir_fd=directory_fds[-1],
                follow_symlinks=False,
            )
        )
        if named_lock != before_lock:
            raise _production_error("production_account_lock_path_replaced")
        after_directories = tuple(
            _production_validate_directory_stat(os.fstat(fd))
            for fd in directory_fds
        )
        after_lock = _production_validate_file_stat(os.fstat(lock_fd))
        if after_directories != before_directories or after_lock != before_lock:
            raise _production_error("production_account_lock_path_replaced")
        return after_directories, after_lock
    except ProductionAccountLockError:
        if normalize_drift:
            raise _production_error("production_account_lock_path_replaced") from None
        raise
    except Exception:
        raise _production_error("production_account_lock_path_replaced") from None


def _production_cleanup_descriptors(
    *,
    lock_fd: int,
    directory_fds: tuple[int, ...],
    unlock: bool,
) -> bool:
    uncertain = False
    if lock_fd >= 0:
        if unlock:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except Exception:
                uncertain = True
        try:
            os.close(lock_fd)
        except Exception:
            uncertain = True
    for descriptor in reversed(directory_fds):
        try:
            os.close(descriptor)
        except Exception:
            uncertain = True
    return uncertain


def _production_build_grant(
    authority: _ProductionGrantAuthority,
) -> ProductionAccountLockGrantV1:
    item = object.__new__(ProductionAccountLockGrantV1)
    _production_register(_production_grant_entries, item, authority)  # type: ignore[arg-type]
    return item


def _production_build_lease(
    authority: _ProductionLeaseAuthority,
) -> ProductionAccountLockLeaseV1:
    item = object.__new__(ProductionAccountLockLeaseV1)
    _production_register(_production_lease_entries, item, authority)  # type: ignore[arg-type]
    return item


def acquire_production_account_lock(
    *,
    environment: str,
    subaccount: int,
) -> ProductionAccountLockGrantV1:
    """Acquire and privately grant the canonical production account lock."""

    checked_subaccount = _production_require_arguments(environment, subaccount)
    _production_require_not_halted()
    home = _production_passwd_home()
    components = (
        *_DIRECTORY_COMPONENTS,
        f"subaccount-{checked_subaccount}",
    )
    _production_require_platform()
    directory_descriptors: list[int] = []
    lock_fd = -1
    flock_held = False
    force_uncertain = False
    try:
        directory_descriptors.append(_production_open_home(home))
        for component in components:
            directory_descriptors.append(
                _production_open_directory(directory_descriptors[-1], component)
            )
        lock_fd, _ = _production_open_lock(directory_descriptors[-1])
        directory_tuple = tuple(directory_descriptors)
        before = _production_observe_bindings(
            home_path=home,
            component_names=components,
            directory_fds=directory_tuple,
            lock_fd=lock_fd,
            normalize_drift=False,
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            flock_held = True
        except BlockingIOError:
            raise _production_error("production_account_lock_contended") from None
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise _production_error("production_account_lock_contended") from None
            flock_held = True
            force_uncertain = True
            raise _production_error(
                "production_account_lock_release_uncertain"
            ) from None
        except Exception:
            flock_held = True
            force_uncertain = True
            raise _production_error(
                "production_account_lock_release_uncertain"
            ) from None
        after = _production_observe_bindings(
            home_path=home,
            component_names=components,
            directory_fds=directory_tuple,
            lock_fd=lock_fd,
            normalize_drift=True,
        )
        if after != before:
            raise _production_error("production_account_lock_path_replaced")
        resource = _ProductionLockResource(
            environment=_ENVIRONMENT,
            subaccount=checked_subaccount,
            home_path=home,
            component_names=components,
            directory_fds=directory_tuple,
            lock_fd=lock_fd,
            observation=after,
            owner_pid=os.getpid(),
            owner_thread=threading.current_thread(),
        )
        with _production_registry_guard:
            if _production_acquisition_halted:
                raise _production_error(
                    "production_account_lock_release_uncertain"
                )
            return _production_build_grant(_ProductionGrantAuthority(resource))
    except BaseException as failure:
        uncertain = _production_cleanup_descriptors(
            lock_fd=lock_fd,
            directory_fds=tuple(directory_descriptors),
            unlock=flock_held,
        )
        if uncertain or force_uncertain:
            _production_mark_halted()
            raise _production_error("production_account_lock_release_uncertain") from None
        if isinstance(failure, ProductionAccountLockError):
            raise failure from None
        if not isinstance(failure, Exception):
            raise
        _production_mark_halted()
        raise _production_error("production_account_lock_release_uncertain") from None


def _production_validate_grant(
    grant: object,
) -> _ProductionGrantAuthority:
    if type(grant) is not ProductionAccountLockGrantV1:
        raise _production_error("production_account_lock_grant_invalid")
    authority = _production_lookup(_production_grant_entries, grant)
    if type(authority) is not _ProductionGrantAuthority:
        raise _production_error("production_account_lock_grant_invalid")
    resource = authority.resource
    if (
        os.getpid() != resource.owner_pid
        or threading.current_thread() is not resource.owner_thread
    ):
        raise _production_error("production_account_lock_grant_invalid")
    return authority


def _production_validate_lease(
    lease: object,
) -> _ProductionLeaseAuthority:
    if type(lease) is not ProductionAccountLockLeaseV1:
        raise _production_error("production_account_lock_lease_invalid")
    authority = _production_lookup(_production_lease_entries, lease)
    if type(authority) is not _ProductionLeaseAuthority:
        raise _production_error("production_account_lock_lease_invalid")
    resource = authority.resource
    if (
        os.getpid() != resource.owner_pid
        or threading.current_thread() is not resource.owner_thread
    ):
        raise _production_error("production_account_lock_lease_invalid")
    return authority


def consume_production_account_lock(
    grant: ProductionAccountLockGrantV1,
) -> ProductionAccountLockLeaseV1:
    """Atomically consume a fresh held grant into exactly one lease."""

    with _production_registry_guard:
        authority = _production_validate_grant(grant)
        if authority.lifecycle == _PRODUCTION_GRANT_CONSUMED:
            raise _production_error("production_account_lock_grant_consumed")
        if authority.lifecycle == _PRODUCTION_GRANT_REVOKED:
            raise _production_error("production_account_lock_grant_revoked")
        if authority.lifecycle != _PRODUCTION_GRANT_FRESH:
            raise _production_error("production_account_lock_grant_invalid")
        lease_authority = _ProductionLeaseAuthority(authority.resource)
        lease = _production_build_lease(lease_authority)
        authority.lifecycle = _PRODUCTION_GRANT_CONSUMED
        return lease


def _production_release_resource(
    resource: _ProductionLockResource,
) -> ProductionAccountLockError | None:
    release_error: ProductionAccountLockError | None = None
    try:
        _production_observe_bindings(
            home_path=resource.home_path,
            component_names=resource.component_names,
            directory_fds=resource.directory_fds,
            lock_fd=resource.lock_fd,
            normalize_drift=True,
        )
    except ProductionAccountLockError as error:
        release_error = error

    uncertain = _production_cleanup_descriptors(
        lock_fd=resource.lock_fd,
        directory_fds=resource.directory_fds,
        unlock=True,
    )
    resource.lock_fd = -1
    resource.directory_fds = ()
    if uncertain:
        _production_mark_halted()
        return _production_error("production_account_lock_release_uncertain")
    return release_error


def revoke_production_account_lock_grant(
    grant: ProductionAccountLockGrantV1,
) -> None:
    """Revoke a fresh grant and release its lock exactly once."""

    with _production_registry_guard:
        authority = _production_validate_grant(grant)
        if authority.lifecycle == _PRODUCTION_GRANT_CONSUMED:
            raise _production_error("production_account_lock_grant_consumed")
        if authority.lifecycle == _PRODUCTION_GRANT_REVOKED:
            raise _production_error("production_account_lock_grant_revoked")
        if authority.lifecycle != _PRODUCTION_GRANT_FRESH:
            raise _production_error("production_account_lock_grant_invalid")
        authority.lifecycle = _PRODUCTION_GRANT_REVOKED
        failure = _production_release_resource(authority.resource)
        if failure is not None:
            raise failure from None


def release_production_account_lock(
    lease: ProductionAccountLockLeaseV1,
) -> None:
    """Release one live production lease exactly once."""

    with _production_registry_guard:
        authority = _production_validate_lease(lease)
        if authority.lifecycle == _PRODUCTION_LEASE_RELEASE_UNCERTAIN:
            raise _production_error("production_account_lock_release_uncertain")
        if authority.lifecycle == _PRODUCTION_LEASE_RELEASED:
            raise _production_error("production_account_lock_lease_invalid")
        if authority.lifecycle != _PRODUCTION_LEASE_HELD:
            raise _production_error("production_account_lock_lease_invalid")
        authority.lifecycle = _PRODUCTION_LEASE_RELEASED
        failure = _production_release_resource(authority.resource)
        if (
            failure is not None
            and str(failure) == "production_account_lock_release_uncertain"
        ):
            authority.lifecycle = _PRODUCTION_LEASE_RELEASE_UNCERTAIN
        if failure is not None:
            raise failure from None


def _production_roots_values(
    resource: _ProductionLockResource,
) -> tuple[str, int, Path, Path, str]:
    phase1 = Path(resource.home_path).joinpath(
        *resource.component_names,
        "tennis-v1",
    )
    expert = phase1 / "expert-v1"
    projection = {
        "environment": resource.environment,
        "subaccount": resource.subaccount,
        "phase1_state_root": str(phase1),
        "expert_state_root": str(expert),
    }
    digest = sha256(
        _PRODUCTION_ROOTS_DOMAIN + canonical_json_bytes(projection)
    ).hexdigest()
    return resource.environment, resource.subaccount, phase1, expert, digest


def _production_build_roots(
    lease: ProductionAccountLockLeaseV1,
    authority: _ProductionLeaseAuthority,
) -> LockedProductionStateRootsV1:
    values = _production_roots_values(authority.resource)
    roots = object.__new__(LockedProductionStateRootsV1)
    for name, value in zip(
        (
            "environment",
            "subaccount",
            "phase1_state_root",
            "expert_state_root",
            "roots_sha256",
        ),
        values,
        strict=True,
    ):
        object.__setattr__(roots, name, value)
    roots_authority = _ProductionRootsAuthority(weakref.ref(lease), authority)
    _production_register(_production_roots_entries, roots, roots_authority)  # type: ignore[arg-type]
    return roots


def derive_locked_production_state_roots_v1(
    lease: ProductionAccountLockLeaseV1,
) -> LockedProductionStateRootsV1:
    """Derive immutable roots bound to one currently held lease."""

    with _production_registry_guard:
        authority = _production_validate_lease(lease)
        if authority.lifecycle != _PRODUCTION_LEASE_HELD:
            raise _production_error("production_account_lock_lease_invalid")
        _production_observe_bindings(
            home_path=authority.resource.home_path,
            component_names=authority.resource.component_names,
            directory_fds=authority.resource.directory_fds,
            lock_fd=authority.resource.lock_fd,
            normalize_drift=True,
        )
        if authority.roots is None:
            authority.roots = _production_build_roots(lease, authority)
        return authority.roots


def _require_locked_production_state_roots_v1(
    roots: LockedProductionStateRootsV1,
    lease: ProductionAccountLockLeaseV1,
) -> LockedProductionStateRootsV1:
    """Validate exact root/lease identity and a still-held lock."""

    with _production_registry_guard:
        if type(roots) is not LockedProductionStateRootsV1:
            raise _production_error("production_account_roots_invalid")
        if type(lease) is not ProductionAccountLockLeaseV1:
            raise _production_error("production_account_roots_invalid")
        roots_authority = _production_lookup(_production_roots_entries, roots)
        lease_authority = _production_lookup(_production_lease_entries, lease)
        if (
            type(roots_authority) is not _ProductionRootsAuthority
            or type(lease_authority) is not _ProductionLeaseAuthority
            or roots_authority.lease_reference() is not lease
            or roots_authority.lease_authority is not lease_authority
            or lease_authority.roots is not roots
            or lease_authority.lifecycle != _PRODUCTION_LEASE_HELD
        ):
            raise _production_error("production_account_roots_invalid")
        resource = lease_authority.resource
        if (
            os.getpid() != resource.owner_pid
            or threading.current_thread() is not resource.owner_thread
        ):
            raise _production_error("production_account_roots_invalid")
        try:
            actual = (
                roots.environment,
                roots.subaccount,
                roots.phase1_state_root,
                roots.expert_state_root,
                roots.roots_sha256,
            )
        except Exception:
            raise _production_error("production_account_roots_invalid") from None
        if actual != _production_roots_values(resource):
            raise _production_error("production_account_roots_invalid")
        try:
            _production_observe_bindings(
                home_path=resource.home_path,
                component_names=resource.component_names,
                directory_fds=resource.directory_fds,
                lock_fd=resource.lock_fd,
                normalize_drift=True,
            )
        except ProductionAccountLockError:
            raise _production_error("production_account_roots_invalid") from None
        return roots
