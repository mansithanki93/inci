"""Strict canonical JSON serialization for immutable Tennis v1 records."""

from __future__ import annotations

import json
from collections.abc import Mapping


class CanonicalJsonError(ValueError):
    """Raised when a value cannot be represented by the canonical JSON subset."""


def _validate(value: object) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        raise CanonicalJsonError("floating-point values are not permitted")
    if isinstance(value, list):
        for item in value:
            _validate(item)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJsonError("JSON object keys must be strings")
            _validate(item)
        return
    raise CanonicalJsonError("unsupported canonical JSON value")


def canonical_json_bytes(value: object) -> bytes:
    """Return the unique UTF-8 JSON representation for a safe JSON value."""
    _validate(value)
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
