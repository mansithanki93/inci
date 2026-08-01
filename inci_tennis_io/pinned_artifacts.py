from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from inci_tennis_expert.contracts import ArtifactPin
from tennis_v1.pinned_file import (
    PinnedBytes,
    PinnedFileError,
    read_pinned_file,
)


MATCH_BINDING_ARTIFACT_MAX_BYTES = 1_048_576
BINDING_REVIEW_ARTIFACT_MAX_BYTES = 16_384
_PATH_TYPE = type(Path())


class PinnedArtifactError(ValueError):
    pass


def _exact_path(value: object, name: str) -> Path:
    if type(value) is not _PATH_TYPE:
        raise PinnedArtifactError(name)
    if not value.is_absolute() or ".." in value.parts:
        raise PinnedArtifactError(name)
    return value


@dataclass(frozen=True, slots=True, repr=False)
class PinnedArtifactReadRequest:
    artifact_pin: ArtifactPin
    path: Path
    repo_root: Path
    forbidden_root: Path
    max_bytes: int

    def __post_init__(self) -> None:
        if type(self) is not PinnedArtifactReadRequest:
            raise TypeError("exact PinnedArtifactReadRequest required")
        if type(self.artifact_pin) is not ArtifactPin:
            raise TypeError("artifact_pin")
        _exact_path(self.path, "pinned_artifact_path")
        _exact_path(self.repo_root, "pinned_artifact_repo_root")
        _exact_path(
            self.forbidden_root,
            "pinned_artifact_forbidden_root",
        )
        if (
            type(self.max_bytes) is not int
            or self.max_bytes < 1
            or self.max_bytes > MATCH_BINDING_ARTIFACT_MAX_BYTES
        ):
            raise PinnedArtifactError("pinned_artifact_max_bytes")


@dataclass(frozen=True, slots=True, repr=False)
class PinnedArtifactBytes:
    artifact_pin: ArtifactPin
    data: bytes

    def __post_init__(self) -> None:
        if type(self) is not PinnedArtifactBytes:
            raise TypeError("exact PinnedArtifactBytes required")
        if type(self.artifact_pin) is not ArtifactPin:
            raise TypeError("artifact_pin")
        if (
            type(self.data) is not bytes
            or len(self.data) > MATCH_BINDING_ARTIFACT_MAX_BYTES
        ):
            raise PinnedArtifactError("pinned_artifact_data")
        if sha256(self.data).hexdigest() != self.artifact_pin.artifact_sha256:
            raise PinnedArtifactError("pinned_artifact_digest")


def read_pinned_artifact(
    request: PinnedArtifactReadRequest,
) -> PinnedArtifactBytes:
    if type(request) is not PinnedArtifactReadRequest:
        raise TypeError("request")
    try:
        result = read_pinned_file(
            request.path,
            expected_sha256=request.artifact_pin.artifact_sha256,
            repo_root=request.repo_root,
            max_bytes=request.max_bytes,
            forbidden_root=request.forbidden_root,
        )
    except PinnedFileError:
        raise PinnedArtifactError("pinned_artifact_read") from None
    if type(result) is not PinnedBytes:
        raise PinnedArtifactError("pinned_artifact_result")
    if (
        type(result.data) is not bytes
        or type(result.sha256) is not str
    ):
        raise PinnedArtifactError("pinned_artifact_result")
    if (
        len(result.sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in result.sha256
        )
        or len(result.data) > request.max_bytes
        or len(result.data) > MATCH_BINDING_ARTIFACT_MAX_BYTES
        or result.sha256 != request.artifact_pin.artifact_sha256
        or sha256(result.data).hexdigest()
        != request.artifact_pin.artifact_sha256
    ):
        raise PinnedArtifactError("pinned_artifact_result")
    try:
        return PinnedArtifactBytes(
            artifact_pin=request.artifact_pin,
            data=result.data,
        )
    except PinnedArtifactError:
        raise PinnedArtifactError("pinned_artifact_result") from None
