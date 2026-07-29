"""Fail-closed descriptor-based reads for immutable local inputs."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import unicodedata


_OPEN_SUPPORTS_DIRFD = hasattr(os, "supports_dir_fd") and os.open in os.supports_dir_fd
_PATH_TYPE = type(Path())


class PinnedFileError(ValueError):
    """Raised when a pinned local file cannot be read safely."""


@dataclass(frozen=True, slots=True)
class PinnedBytes:
    data: bytes
    sha256: str


def _require_posix_features() -> None:
    required_constants = ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC", "O_NONBLOCK")
    if os.name != "posix" or any(not hasattr(os, item) for item in required_constants):
        raise PinnedFileError("pinned-file loading is unsupported on this runtime")
    if not _OPEN_SUPPORTS_DIRFD:
        raise PinnedFileError("pinned-file loading is unsupported on this runtime")
    if not hasattr(fcntl, "flock"):
        raise PinnedFileError("pinned-file loading is unsupported on this runtime")


def _absolute_external_path(path: str | os.PathLike[str], repo_root: str | os.PathLike[str]) -> str:
    candidate = os.path.expanduser(os.fspath(path))
    repository = os.path.expanduser(os.fspath(repo_root))
    if not os.path.isabs(candidate) or not os.path.isabs(repository):
        raise PinnedFileError("pinned-file paths must be absolute")
    candidate = os.path.abspath(candidate)
    repository = os.path.abspath(repository)
    try:
        if os.path.commonpath((candidate, repository)) == repository:
            raise PinnedFileError("pinned files must be outside the repository")
    except ValueError as error:
        raise PinnedFileError("pinned-file paths are invalid") from error
    return candidate


def _reject_forbidden_root_overlap(
    path: str | os.PathLike[str],
    forbidden_root: str | os.PathLike[str],
) -> None:
    if type(path) is not _PATH_TYPE or type(forbidden_root) is not _PATH_TYPE:
        raise PinnedFileError("restricted pinned-file path is invalid")
    candidate = str(path)
    forbidden = str(forbidden_root)
    if (
        not os.path.isabs(candidate)
        or not os.path.isabs(forbidden)
        or os.path.normpath(candidate) != candidate
        or os.path.normpath(forbidden) != forbidden
        or ".." in path.parts
        or ".." in forbidden_root.parts
    ):
        raise PinnedFileError("restricted pinned-file path is invalid")
    candidate_parts = tuple(
        unicodedata.normalize("NFC", component).casefold()
        for component in path.parts
    )
    forbidden_parts = tuple(
        unicodedata.normalize("NFC", component).casefold()
        for component in forbidden_root.parts
    )
    if (
        candidate_parts[: len(forbidden_parts)] == forbidden_parts
        or forbidden_parts[: len(candidate_parts)] == candidate_parts
    ):
        raise PinnedFileError("pinned file overlaps forbidden root")


def _metadata(file_stat: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        file_stat.st_nlink,
        file_stat.st_uid,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _trusted_repo_identity(repo_root: str | os.PathLike[str]) -> tuple[int, int]:
    repository = os.path.expanduser(os.fspath(repo_root))
    if not os.path.isabs(repository):
        raise PinnedFileError("pinned-file paths must be absolute")
    descriptor = -1
    try:
        descriptor = os.open(
            repository, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        )
        repository_stat = os.fstat(descriptor)
        if not stat.S_ISDIR(repository_stat.st_mode):
            raise PinnedFileError("repository root must be a directory")
        return repository_stat.st_dev, repository_stat.st_ino
    except PinnedFileError:
        raise
    except OSError as error:
        raise PinnedFileError("repository root cannot be opened safely") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_bounded(file_descriptor: int, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining:
        chunk = os.read(file_descriptor, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > max_bytes:
        raise PinnedFileError("pinned file exceeds the configured byte limit")
    return content


def read_pinned_file(
    path: str | os.PathLike[str],
    *,
    expected_sha256: str | None,
    repo_root: str | os.PathLike[str],
    max_bytes: int,
    forbidden_root: str | os.PathLike[str] | None = None,
) -> PinnedBytes:
    """Read an external regular file once, while detecting substitution races."""
    _require_posix_features()
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise PinnedFileError("pinned-file byte limit is invalid")
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise PinnedFileError("expected pinned-file digest is invalid")
    if forbidden_root is not None:
        _reject_forbidden_root_overlap(path, forbidden_root)
    absolute_path = _absolute_external_path(path, repo_root)
    trusted_repo_identity = _trusted_repo_identity(repo_root)
    components = absolute_path.split(os.sep)[1:]
    if not components or any(component in ("", ".", "..") for component in components):
        raise PinnedFileError("pinned-file path is invalid")

    directory_descriptor = -1
    file_descriptor = -1
    try:
        directory_descriptor = os.open(
            os.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        )
        root_stat = os.fstat(directory_descriptor)
        if (root_stat.st_dev, root_stat.st_ino) == trusted_repo_identity:
            raise PinnedFileError("pinned files must be outside the repository")
        for component in components[:-1]:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
            directory_stat = os.fstat(directory_descriptor)
            if (directory_stat.st_dev, directory_stat.st_ino) == trusted_repo_identity:
                raise PinnedFileError("pinned files must be outside the repository")
        file_descriptor = os.open(
            components[-1],
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_descriptor,
        )
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PinnedFileError("pinned file must be a single-link regular file")
        fcntl.flock(file_descriptor, fcntl.LOCK_SH)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PinnedFileError("pinned file must be a single-link regular file")
        content = _read_bounded(file_descriptor, max_bytes)
        after = os.fstat(file_descriptor)
        if _metadata(before) != _metadata(after):
            raise PinnedFileError("pinned-file metadata changed while reading")
    except PinnedFileError:
        raise
    except OSError as error:
        raise PinnedFileError("pinned file cannot be opened safely") from error
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)

    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise PinnedFileError("pinned-file digest does not match")
    return PinnedBytes(data=content, sha256=digest)
