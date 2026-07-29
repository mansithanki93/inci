"""Deterministic package and session identifiers for Tennis v1."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import uuid


CODE_FINGERPRINT_DOMAIN = b"INCI-TENNIS-V1-CODE\0"


class FingerprintError(ValueError):
    """Raised when the package tree cannot be fingerprinted safely."""


def _frame(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


def _reject_symlinked_route(candidate: Path) -> None:
    expanded = candidate.expanduser()
    if expanded.is_absolute():
        current = Path(expanded.anchor)
        components = expanded.parts[1:]
    else:
        current = Path.cwd()
        components = expanded.parts
    for component in components:
        if component in ("", "."):
            continue
        if component == "..":
            current = current.parent
            continue
        current /= component
        if current.is_symlink():
            raise FingerprintError("symlinked_package_root_forbidden")


def code_sha256(package_root: str | Path) -> str:
    original_root = Path(package_root)
    _reject_symlinked_route(original_root)
    root = original_root
    if not root.is_absolute():
        root = root.resolve()
    if root.is_symlink() or not root.is_dir():
        raise FingerprintError("package_root_invalid")
    entries: list[tuple[str, bytes]] = []
    try:
        for directory, names, files in os.walk(root, topdown=True, followlinks=False):
            directory_path = Path(directory)
            kept_directories: list[str] = []
            for name in names:
                child = directory_path / name
                if child.is_symlink():
                    raise FingerprintError("symlinked_artifact_forbidden")
                if name == "__pycache__":
                    continue
                kept_directories.append(name)
            names[:] = sorted(kept_directories)
            for name in sorted(files):
                path = directory_path / name
                if path.is_symlink():
                    raise FingerprintError("symlinked_artifact_forbidden")
                relative = PurePosixPath(path.relative_to(root).as_posix())
                if name.endswith((".pyc", ".pyo")):
                    continue
                allowed = path.suffix == ".py" or (
                    path.suffix == ".json" and "schemas" in relative.parts[:-1]
                )
                if not allowed:
                    raise FingerprintError("unknown_package_artifact")
                descriptor = os.open(
                    path,
                    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
                try:
                    before = os.fstat(descriptor)
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                        raise FingerprintError("unsafe_package_artifact")
                    chunks: list[bytes] = []
                    while True:
                        chunk = os.read(descriptor, 1024 * 1024)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    after = os.fstat(descriptor)
                    if (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                        before.st_ctime_ns,
                    ) != (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    ):
                        raise FingerprintError("package_artifact_changed")
                finally:
                    os.close(descriptor)
                entries.append((relative.as_posix(), b"".join(chunks)))
    except OSError as error:
        raise FingerprintError("package_tree_unreadable") from error
    digest = hashlib.sha256(CODE_FINGERPRINT_DOMAIN)
    for relative, content in sorted(entries):
        digest.update(_frame(relative.encode("utf-8")))
        digest.update(_frame(content))
    return digest.hexdigest()


def new_session_id() -> str:
    return str(uuid.uuid4())
