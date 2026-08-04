"""Descriptor-relative durable store for companion paper clip journals."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from inci_tennis_expert.clip_journal import (
    ClipJournalRecordV1,
    deserialize_clip_journal_document,
    encode_clip_journal_records,
    serialize_clip_journal_document,
)
from inci_tennis_expert.contracts import ExpertContractError


def _as_path(path: Path | str) -> Path:
    if isinstance(path, Path):
        return path
    if type(path) is str:
        return Path(path)
    raise TypeError("path")


def write_clip_journal_document(
    path: Path | str,
    records: tuple[ClipJournalRecordV1, ...],
) -> str:
    """Atomically write a sealed companion clip journal document.

    Returns the integrity digest for the written records.
    """
    target = _as_path(path)
    if type(records) is not tuple:
        raise TypeError("records")
    document = serialize_clip_journal_document(records)
    integrity = encode_clip_journal_records(records).strip().decode("ascii")
    parent = target.parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_bytes(document)
        os.replace(temporary, target)
    except Exception:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        raise
    return integrity


def read_clip_journal_document(
    path: Path | str,
) -> tuple[ClipJournalRecordV1, ...]:
    """Load and verify a companion clip journal document from disk."""
    target = _as_path(path)
    try:
        document = target.read_bytes()
    except OSError as exc:
        raise ExpertContractError("clip_journal_missing") from exc
    return deserialize_clip_journal_document(document)


__all__: Final[tuple[str, ...]] = (
    "read_clip_journal_document",
    "write_clip_journal_document",
)
