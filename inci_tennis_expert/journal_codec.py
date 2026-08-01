"""Pure framing codec for the Inci expert companion journal."""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
import struct
from typing import Final
from uuid import UUID

from .contracts import (
    ExpertEventKindV1,
    ExpertEventPayloadV1,
    ExpertJournalCursorV1,
    ExpertJournalGroupV1,
    ExpertObservationIgnoredPayloadV1,
    ExpertObservationRejectedPayloadV1,
    ExpertSessionManifestV1,
    ExpertSessionTerminalV1,
    ExpertSynchronizationAppliedPayloadV1,
    _REGISTERED_DATACLASSES,
    _REGISTERED_ENUMS,
    canonical_expert_bytes,
    expert_event_schema_resource_sha256,
    expert_trace_seed_sha256,
)


EXPERT_FILE_MAGIC: Final[bytes] = b"INCIXJ01"
EXPERT_FILE_VERSION: Final[int] = 1
EXPERT_FILE_FLAGS: Final[int] = 0
_EXPERT_FILE_HEADER: Final[struct.Struct] = struct.Struct(">8sHHI")
EXPERT_FILE_HEADER_BYTES: Final[int] = _EXPERT_FILE_HEADER.size

EXPERT_FRAME_MAGIC: Final[bytes] = b"IXJF"
EXPERT_FRAME_VERSION: Final[int] = 1
EXPERT_FRAME_FLAGS: Final[int] = 0
EXPERT_FRAME_TRAILER_MAGIC: Final[bytes] = b"FJXI"
EXPERT_FRAME_KIND_MANIFEST: Final[int] = 1
EXPERT_FRAME_KIND_PARENT_GROUP: Final[int] = 2
EXPERT_FRAME_KIND_TERMINAL: Final[int] = 3
_EXPERT_FRAME_PREFIX: Final[struct.Struct] = struct.Struct(">4sBBHQQII")
_EXPERT_FRAME_TRAILER: Final[struct.Struct] = struct.Struct(">Q32s4s")
EXPERT_FRAME_PREFIX_BYTES: Final[int] = _EXPERT_FRAME_PREFIX.size
EXPERT_FRAME_TRAILER_BYTES: Final[int] = _EXPERT_FRAME_TRAILER.size
EXPERT_FRAME_FIXED_BYTES: Final[int] = (
    EXPERT_FRAME_PREFIX_BYTES + EXPERT_FRAME_TRAILER_BYTES
)

MAX_EXPERT_FRAME_BYTES: Final[int] = 16_777_216
MAX_EXPERT_MANIFEST_METADATA_BYTES: Final[int] = 1_048_576
MAX_EXPERT_TERMINAL_METADATA_BYTES: Final[int] = 1_048_576
MAX_EXPERT_TERMINAL_FRAME_BYTES: Final[int] = 1_048_652
MAX_EXPERT_GROUP_METADATA_BYTES: Final[int] = 8_388_532
MAX_EXPERT_GROUP_PAYLOAD_AREA_BYTES: Final[int] = 8_388_608
MAX_EXPERT_EVENT_PAYLOAD_BYTES: Final[int] = 131_064
MAX_EXPERT_OUTCOMES_PER_PARENT: Final[int] = 64
MAX_EXPERT_SCHEMA_PINS: Final[int] = 256
MAX_EXPERT_ARTIFACT_PINS: Final[int] = 256
EXPERT_MIN_FREE_BYTES: Final[int] = 67_108_864
EXPERT_EMERGENCY_RESERVE_BYTES: Final[int] = 17_825_868
EXPERT_FRAME_DIGEST_DOMAIN: Final[bytes] = (
    b"INCI-EXPERT-JOURNAL-FRAME-V1\0"
)


class ExpertJournalCodecError(ValueError):
    """Raised when companion journal bytes violate the wire contract."""


def encode_expert_file_header() -> bytes:
    return _EXPERT_FILE_HEADER.pack(
        EXPERT_FILE_MAGIC,
        EXPERT_FILE_VERSION,
        EXPERT_FILE_FLAGS,
        EXPERT_FILE_HEADER_BYTES,
    )


def decode_expert_file_header(content: bytes) -> None:
    if type(content) is not bytes:
        raise TypeError("content")
    if len(content) != EXPERT_FILE_HEADER_BYTES:
        raise ExpertJournalCodecError("expert_journal_header_invalid")
    try:
        magic, version, flags, header_bytes = _EXPERT_FILE_HEADER.unpack(content)
    except struct.error:
        raise ExpertJournalCodecError("expert_journal_header_invalid") from None
    if (
        magic != EXPERT_FILE_MAGIC
        or version != EXPERT_FILE_VERSION
        or flags != EXPERT_FILE_FLAGS
        or header_bytes != EXPERT_FILE_HEADER_BYTES
    ):
        raise ExpertJournalCodecError("expert_journal_header_invalid")


def decode_expert_frame_prefix(
    content: bytes,
) -> tuple[int, int, int, int, int]:
    if type(content) is not bytes:
        raise TypeError("content")
    if len(content) != EXPERT_FRAME_PREFIX_BYTES:
        raise ExpertJournalCodecError("expert_journal_frame_invalid")
    try:
        (
            magic,
            version,
            kind,
            flags,
            frame_sequence,
            total_frame_bytes,
            metadata_bytes,
            payload_area_bytes,
        ) = _EXPERT_FRAME_PREFIX.unpack(content)
    except struct.error:
        raise ExpertJournalCodecError("expert_journal_frame_invalid") from None
    if (
        magic != EXPERT_FRAME_MAGIC
        or version != EXPERT_FRAME_VERSION
        or flags != EXPERT_FRAME_FLAGS
        or kind
        not in {
            EXPERT_FRAME_KIND_MANIFEST,
            EXPERT_FRAME_KIND_PARENT_GROUP,
            EXPERT_FRAME_KIND_TERMINAL,
        }
        or total_frame_bytes
        != EXPERT_FRAME_FIXED_BYTES + metadata_bytes + payload_area_bytes
        or total_frame_bytes < EXPERT_FRAME_FIXED_BYTES
        or total_frame_bytes > MAX_EXPERT_FRAME_BYTES
    ):
        raise ExpertJournalCodecError("expert_journal_frame_invalid")
    if kind == EXPERT_FRAME_KIND_MANIFEST:
        valid_kind_shape = (
            frame_sequence == 0
            and payload_area_bytes == 0
            and metadata_bytes <= MAX_EXPERT_MANIFEST_METADATA_BYTES
        )
    elif kind == EXPERT_FRAME_KIND_PARENT_GROUP:
        valid_kind_shape = (
            frame_sequence >= 1
            and metadata_bytes <= MAX_EXPERT_GROUP_METADATA_BYTES
            and payload_area_bytes <= MAX_EXPERT_GROUP_PAYLOAD_AREA_BYTES
        )
    else:
        valid_kind_shape = (
            frame_sequence >= 1
            and payload_area_bytes == 0
            and metadata_bytes <= MAX_EXPERT_TERMINAL_METADATA_BYTES
            and total_frame_bytes <= MAX_EXPERT_TERMINAL_FRAME_BYTES
        )
    if not valid_kind_shape:
        raise ExpertJournalCodecError("expert_journal_frame_invalid")
    return (
        kind,
        frame_sequence,
        total_frame_bytes,
        metadata_bytes,
        payload_area_bytes,
    )


def validate_expert_frame_parts(
    prefix: bytes,
    metadata: bytes,
    payload_area: bytes,
    trailer: bytes,
) -> str:
    for name, value in (
        ("prefix", prefix),
        ("metadata", metadata),
        ("payload_area", payload_area),
        ("trailer", trailer),
    ):
        if type(value) is not bytes:
            raise TypeError(name)
    (
        _,
        _,
        total_frame_bytes,
        metadata_bytes,
        payload_area_bytes,
    ) = decode_expert_frame_prefix(prefix)
    if (
        len(metadata) != metadata_bytes
        or len(payload_area) != payload_area_bytes
        or len(trailer) != EXPERT_FRAME_TRAILER_BYTES
    ):
        raise ExpertJournalCodecError("expert_journal_frame_invalid")
    try:
        repeated_total, stored_digest, magic = _EXPERT_FRAME_TRAILER.unpack(
            trailer
        )
    except struct.error:
        raise ExpertJournalCodecError("expert_journal_frame_invalid") from None
    expected_digest = sha256(
        EXPERT_FRAME_DIGEST_DOMAIN + prefix + metadata + payload_area
    ).digest()
    if (
        repeated_total != total_frame_bytes
        or magic != EXPERT_FRAME_TRAILER_MAGIC
        or stored_digest != expected_digest
    ):
        raise ExpertJournalCodecError("expert_journal_frame_invalid")
    return stored_digest.hex()


def validate_expert_streamed_frame_trailer(
    prefix: bytes,
    *,
    payload_area_bytes: int,
    trailer: bytes,
    computed_digest: bytes,
) -> str:
    """Validate a frame whose payload area was hashed incrementally."""

    for name, value in (
        ("prefix", prefix),
        ("trailer", trailer),
        ("computed_digest", computed_digest),
    ):
        if type(value) is not bytes:
            raise TypeError(name)
    (
        _,
        _,
        total_frame_bytes,
        _,
        declared_payload_area_bytes,
    ) = decode_expert_frame_prefix(prefix)
    if (
        type(payload_area_bytes) is not int
        or payload_area_bytes != declared_payload_area_bytes
        or len(trailer) != EXPERT_FRAME_TRAILER_BYTES
        or len(computed_digest) != 32
    ):
        raise ExpertJournalCodecError("expert_journal_frame_invalid")
    try:
        repeated_total, stored_digest, magic = _EXPERT_FRAME_TRAILER.unpack(
            trailer
        )
    except struct.error:
        raise ExpertJournalCodecError("expert_journal_frame_invalid") from None
    if (
        repeated_total != total_frame_bytes
        or magic != EXPERT_FRAME_TRAILER_MAGIC
        or stored_digest != computed_digest
    ):
        raise ExpertJournalCodecError("expert_journal_frame_invalid")
    return stored_digest.hex()


def decode_expert_complete_frame(
    frame: bytes,
) -> tuple[int, int, bytes, bytes, str]:
    if type(frame) is not bytes:
        raise TypeError("frame")
    if len(frame) < EXPERT_FRAME_PREFIX_BYTES:
        raise ExpertJournalCodecError("expert_journal_frame_invalid")
    prefix = frame[:EXPERT_FRAME_PREFIX_BYTES]
    (
        kind,
        frame_sequence,
        total_frame_bytes,
        metadata_bytes,
        payload_area_bytes,
    ) = decode_expert_frame_prefix(prefix)
    if len(frame) != total_frame_bytes:
        raise ExpertJournalCodecError("expert_journal_frame_invalid")
    metadata_start = EXPERT_FRAME_PREFIX_BYTES
    payload_start = metadata_start + metadata_bytes
    trailer_start = payload_start + payload_area_bytes
    metadata = frame[metadata_start:payload_start]
    payload_area = frame[payload_start:trailer_start]
    trailer = frame[trailer_start:]
    frame_sha256 = validate_expert_frame_parts(
        prefix,
        metadata,
        payload_area,
        trailer,
    )
    return (
        kind,
        frame_sequence,
        metadata,
        payload_area,
        frame_sha256,
    )


class ExpertJournalOrderValidator:
    __slots__ = ("_manifest_seen", "_next_sequence", "_terminal_seen")

    def __init__(self) -> None:
        self._manifest_seen = False
        self._next_sequence = 0
        self._terminal_seen = False

    def accept(self, prefix: bytes) -> None:
        kind, frame_sequence, _, _, _ = decode_expert_frame_prefix(prefix)
        if self._terminal_seen:
            raise ExpertJournalCodecError("expert_journal_frame_order_invalid")
        if not self._manifest_seen:
            if (
                kind != EXPERT_FRAME_KIND_MANIFEST
                or frame_sequence != 0
            ):
                raise ExpertJournalCodecError(
                    "expert_journal_frame_order_invalid"
                )
            self._manifest_seen = True
            self._next_sequence = 1
            return
        if (
            kind == EXPERT_FRAME_KIND_MANIFEST
            or frame_sequence != self._next_sequence
        ):
            raise ExpertJournalCodecError("expert_journal_frame_order_invalid")
        if kind == EXPERT_FRAME_KIND_PARENT_GROUP:
            self._next_sequence += 1
            return
        if kind == EXPERT_FRAME_KIND_TERMINAL:
            self._terminal_seen = True
            return
        raise ExpertJournalCodecError("expert_journal_frame_order_invalid")

    def require_terminal(self) -> None:
        if not self._terminal_seen:
            raise ExpertJournalCodecError("expert_journal_terminal_missing")


def _require_payload_count(value: object) -> int:
    if (
        type(value) is not int
        or value < 1
        or value > MAX_EXPERT_OUTCOMES_PER_PARENT
    ):
        raise TypeError("expected_count")
    return value


def encode_expert_group_payload_area(payloads: tuple[bytes, ...]) -> bytes:
    if type(payloads) is not tuple:
        raise TypeError("payloads")
    if not 1 <= len(payloads) <= MAX_EXPERT_OUTCOMES_PER_PARENT:
        raise ExpertJournalCodecError("expert_group_payload_area_invalid")
    total = 0
    for payload in payloads:
        if type(payload) is not bytes:
            raise TypeError("payload")
        payload_length = len(payload)
        if payload_length > MAX_EXPERT_EVENT_PAYLOAD_BYTES:
            raise ExpertJournalCodecError("expert_group_payload_area_invalid")
        total += 8 + payload_length
        if total > MAX_EXPERT_GROUP_PAYLOAD_AREA_BYTES:
            raise ExpertJournalCodecError("expert_group_payload_area_invalid")
    parts: list[bytes] = []
    for payload in payloads:
        parts.append(struct.pack(">Q", len(payload)))
        parts.append(payload)
    encoded = b"".join(parts)
    if len(encoded) != total:
        raise ExpertJournalCodecError("expert_group_payload_area_invalid")
    return encoded


def decode_expert_group_payload_area(
    payload_area: bytes,
    *,
    expected_count: int,
) -> tuple[bytes, ...]:
    if type(payload_area) is not bytes:
        raise TypeError("payload_area")
    count = _require_payload_count(expected_count)
    if len(payload_area) > MAX_EXPERT_GROUP_PAYLOAD_AREA_BYTES:
        raise ExpertJournalCodecError("expert_group_payload_area_invalid")
    view = memoryview(payload_area)
    offset = 0
    bounds: list[tuple[int, int]] = []
    for _ in range(count):
        if len(view) - offset < 8:
            raise ExpertJournalCodecError("expert_group_payload_area_invalid")
        payload_length = struct.unpack_from(">Q", view, offset)[0]
        offset += 8
        if payload_length > MAX_EXPERT_EVENT_PAYLOAD_BYTES:
            raise ExpertJournalCodecError("expert_group_payload_area_invalid")
        end = offset + payload_length
        if end > len(view):
            raise ExpertJournalCodecError("expert_group_payload_area_invalid")
        bounds.append((offset, end))
        offset = end
    if offset != len(view):
        raise ExpertJournalCodecError("expert_group_payload_area_invalid")
    return tuple(bytes(view[start:end]) for start, end in bounds)


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ExpertJournalCodecError("expert_journal_metadata_invalid")
        result[key] = value
    return result


def _reject_json_number(_: str) -> object:
    raise ExpertJournalCodecError("expert_journal_metadata_invalid")


def _registered_enum(name: str) -> type[Enum]:
    matches = tuple(item for item in _REGISTERED_ENUMS if item.__name__ == name)
    if len(matches) != 1:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    return matches[0]


def _registered_contract(name: str) -> type[object]:
    matches = tuple(
        item for item in _REGISTERED_DATACLASSES if item.__name__ == name
    )
    if len(matches) != 1:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    return matches[0]


def _decode_projection(value: object, *, depth: int) -> object:
    if depth > 128:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is not dict:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    keys = set(value)
    if keys == {"$decimal"}:
        rendered = value["$decimal"]
        if type(rendered) is not str:
            raise ExpertJournalCodecError("expert_journal_metadata_invalid")
        try:
            return Decimal(rendered)
        except BaseException:
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            ) from None
    if keys == {"$enum", "value"}:
        enum_name = value["$enum"]
        enum_value = value["value"]
        if type(enum_name) is not str or type(enum_value) is not str:
            raise ExpertJournalCodecError("expert_journal_metadata_invalid")
        enum_type = _registered_enum(enum_name)
        try:
            return enum_type(enum_value)
        except (TypeError, ValueError):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            ) from None
    if keys in ({"$tuple"}, {"$list"}):
        wrapper = "$tuple" if "$tuple" in value else "$list"
        items = value[wrapper]
        if type(items) is not list:
            raise ExpertJournalCodecError("expert_journal_metadata_invalid")
        decoded = [
            _decode_projection(item, depth=depth + 1) for item in items
        ]
        return tuple(decoded) if wrapper == "$tuple" else decoded
    if keys == {"$dict"}:
        entries = value["$dict"]
        if type(entries) is not list:
            raise ExpertJournalCodecError("expert_journal_metadata_invalid")
        result: dict[str, object] = {}
        for entry in entries:
            if (
                type(entry) is not list
                or len(entry) != 2
                or type(entry[0]) is not str
                or entry[0] in result
            ):
                raise ExpertJournalCodecError(
                    "expert_journal_metadata_invalid"
                )
            result[entry[0]] = _decode_projection(
                entry[1],
                depth=depth + 1,
            )
        return result
    if keys != {"$contract", "$version", "fields"}:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    contract_name = value["$contract"]
    version = value["$version"]
    field_values = value["fields"]
    if (
        type(contract_name) is not str
        or type(version) is not int
        or version != 1
        or type(field_values) is not dict
    ):
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    contract_type = _registered_contract(contract_name)
    contract_fields = fields(contract_type)
    if set(field_values) != {item.name for item in contract_fields}:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    try:
        return contract_type(
            **{
                item.name: _decode_projection(
                    field_values[item.name],
                    depth=depth + 1,
                )
                for item in contract_fields
            }
        )
    except ExpertJournalCodecError:
        raise
    except BaseException:
        raise ExpertJournalCodecError(
            "expert_journal_metadata_invalid"
        ) from None


def _decode_contract_metadata(
    metadata: bytes,
    expected_type: type[object],
) -> object:
    if type(metadata) is not bytes:
        raise TypeError("metadata")
    if metadata.startswith(b"\xef\xbb\xbf"):
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    try:
        document = json.loads(
            metadata.decode("ascii"),
            object_pairs_hook=_strict_pairs,
            parse_float=_reject_json_number,
            parse_constant=_reject_json_number,
        )
    except ExpertJournalCodecError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise ExpertJournalCodecError(
            "expert_journal_metadata_invalid"
        ) from None
    if (
        type(document) is not dict
        or set(document) != {"canonical_version", "domain", "value"}
        or type(document["canonical_version"]) is not int
        or document["canonical_version"] != 1
        or document["domain"] != "inci-tennis-expert"
    ):
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    decoded = _decode_projection(document["value"], depth=0)
    if type(decoded) is not expected_type:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    try:
        canonical = canonical_expert_bytes(decoded)
    except BaseException:
        raise ExpertJournalCodecError(
            "expert_journal_metadata_invalid"
        ) from None
    if canonical != metadata:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    return decoded


def _validate_diagnostic_projection_shape(
    value: object,
    *,
    depth: int,
) -> None:
    """Validate canonical projection structure without constructing contracts."""

    if depth > 128:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is not dict:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    keys = set(value)
    if keys == {"$decimal"}:
        rendered = value["$decimal"]
        if type(rendered) is not str or not 1 <= len(rendered) <= 256:
            raise ExpertJournalCodecError("expert_journal_metadata_invalid")
        try:
            decimal = Decimal(rendered)
            projected = json.loads(
                canonical_expert_bytes(decimal).decode("ascii")
            )["value"]
        except (ArithmeticError, KeyError, TypeError, ValueError):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            ) from None
        if projected != value:
            raise ExpertJournalCodecError("expert_journal_metadata_invalid")
        return
    if keys == {"$enum", "value"}:
        enum_name = value["$enum"]
        enum_value = value["value"]
        if type(enum_name) is not str or type(enum_value) is not str:
            raise ExpertJournalCodecError("expert_journal_metadata_invalid")
        enum_type = _registered_enum(enum_name)
        try:
            enum_type(enum_value)
        except (TypeError, ValueError):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            ) from None
        return
    if keys in ({"$tuple"}, {"$list"}):
        wrapper = "$tuple" if "$tuple" in value else "$list"
        items = value[wrapper]
        if type(items) is not list:
            raise ExpertJournalCodecError("expert_journal_metadata_invalid")
        for item in items:
            _validate_diagnostic_projection_shape(
                item,
                depth=depth + 1,
            )
        return
    if keys == {"$dict"}:
        entries = value["$dict"]
        if type(entries) is not list:
            raise ExpertJournalCodecError("expert_journal_metadata_invalid")
        prior_key: str | None = None
        for entry in entries:
            if (
                type(entry) is not list
                or len(entry) != 2
                or type(entry[0]) is not str
                or (
                    prior_key is not None
                    and entry[0] <= prior_key
                )
            ):
                raise ExpertJournalCodecError(
                    "expert_journal_metadata_invalid"
                )
            prior_key = entry[0]
            _validate_diagnostic_projection_shape(
                entry[1],
                depth=depth + 1,
            )
        return
    if keys in (
        {"$phase1_session_manifest_sha256"},
        {"$phase1_record_sha256"},
        {"$phase1_replay_summary_sha256"},
    ):
        digest = next(iter(value.values()))
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in digest
            )
        ):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        return
    if keys != {"$contract", "$version", "fields"}:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    contract_name = value["$contract"]
    version = value["$version"]
    field_values = value["fields"]
    if (
        type(contract_name) is not str
        or type(version) is not int
        or version != 1
        or type(field_values) is not dict
    ):
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    contract_type = _registered_contract(contract_name)
    contract_fields = fields(contract_type)
    if set(field_values) != {item.name for item in contract_fields}:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    for item in contract_fields:
        _validate_diagnostic_projection_shape(
            field_values[item.name],
            depth=depth + 1,
        )


def validate_expert_group_metadata_diagnostic(
    metadata: bytes,
) -> tuple[int, int, tuple[tuple[int, str], ...]]:
    """Return group sequence and record count after metadata-only validation.

    This intentionally does not construct an ``ExpertJournalGroupV1`` and
    does not apply cursor, sequence, chain, or semantic replay checks.
    """

    if type(metadata) is not bytes:
        raise TypeError("metadata")
    if metadata.startswith(b"\xef\xbb\xbf"):
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    try:
        document = json.loads(
            metadata.decode("ascii"),
            object_pairs_hook=_strict_pairs,
            parse_float=_reject_json_number,
            parse_constant=_reject_json_number,
        )
    except ExpertJournalCodecError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise ExpertJournalCodecError(
            "expert_journal_metadata_invalid"
        ) from None
    if (
        type(document) is not dict
        or set(document) != {"canonical_version", "domain", "value"}
        or type(document["canonical_version"]) is not int
        or document["canonical_version"] != 1
        or document["domain"] != "inci-tennis-expert"
    ):
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    value = document["value"]
    _validate_diagnostic_projection_shape(value, depth=0)
    if (
        type(value) is not dict
        or value.get("$contract") != "ExpertJournalGroupV1"
        or type(value.get("fields")) is not dict
    ):
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    records = value["fields"].get("records")
    trace_steps = value["fields"].get("trace_steps")
    parent_output_count = value["fields"].get("parent_output_count")
    group_sequence = value["fields"].get("group_sequence")
    if (
        type(records) is not dict
        or set(records) != {"$tuple"}
        or type(records["$tuple"]) is not list
        or not 1 <= len(records["$tuple"]) <= MAX_EXPERT_OUTCOMES_PER_PARENT
        or type(trace_steps) is not dict
        or set(trace_steps) != {"$tuple"}
        or type(trace_steps["$tuple"]) is not list
        or len(trace_steps["$tuple"]) != len(records["$tuple"])
        or type(parent_output_count) is not int
        or parent_output_count != len(records["$tuple"])
        or type(group_sequence) is not int
        or group_sequence < 1
    ):
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")

    def contract_fields(
        projection: object,
        contract_name: str,
    ) -> dict[str, object]:
        if (
            type(projection) is not dict
            or projection.get("$contract") != contract_name
            or projection.get("$version") != 1
            or type(projection.get("fields")) is not dict
        ):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        return projection["fields"]

    def require_integer(
        fields_value: dict[str, object],
        name: str,
        *,
        positive: bool = False,
    ) -> int:
        candidate = fields_value.get(name)
        if (
            type(candidate) is not int
            or candidate < (1 if positive else 0)
            or abs(candidate) >= 10**256
        ):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        return candidate

    def require_string(
        fields_value: dict[str, object],
        name: str,
    ) -> str:
        candidate = fields_value.get(name)
        if type(candidate) is not str or not candidate:
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        return candidate

    def require_digest(
        fields_value: dict[str, object],
        name: str,
    ) -> str:
        candidate = fields_value.get(name)
        if (
            type(candidate) is not str
            or len(candidate) != 64
            or any(
                character not in "0123456789abcdef"
                for character in candidate
            )
        ):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        return candidate

    def require_safe_id(
        fields_value: dict[str, object],
        name: str,
    ) -> str:
        candidate = require_string(fields_value, name)
        if (
            not 1 <= len(candidate) <= 128
            or candidate[0] not in (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "abcdefghijklmnopqrstuvwxyz"
                "0123456789"
            )
            or any(
                character not in (
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    "abcdefghijklmnopqrstuvwxyz"
                    "0123456789._:-"
                )
                for character in candidate
            )
            or candidate.lower().startswith(("http:", "https:", "file:"))
        ):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        return candidate

    def require_session_id(
        fields_value: dict[str, object],
        name: str = "session_id",
    ) -> str:
        candidate = require_safe_id(fields_value, name)
        try:
            parsed = UUID(candidate)
        except ValueError:
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            ) from None
        if str(parsed) != candidate:
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        return candidate

    def raw_self_digest(
        fields_value: dict[str, object],
        *,
        contract_name: str,
        digest_field: str,
        domain: bytes,
    ) -> str:
        ordered = [
            fields_value[item.name]
            for item in fields(_registered_contract(contract_name))
            if item.name != digest_field
        ]
        try:
            canonical = json.dumps(
                {
                    "canonical_version": 1,
                    "domain": "inci-tennis-expert",
                    "value": {"$tuple": ordered},
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
        except (TypeError, ValueError):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            ) from None
        return sha256(domain + canonical).hexdigest()

    def validate_parent(projection: object) -> dict[str, object]:
        parent_fields = contract_fields(
            projection,
            "ExpertParentEvidenceV1",
        )
        require_session_id(parent_fields)
        require_integer(parent_fields, "ingest_seq", positive=True)
        require_digest(parent_fields, "record_sha256")
        require_safe_id(parent_fields, "event_type")
        require_integer(parent_fields, "event_version", positive=True)
        for name in (
            "local_wall_ns",
            "local_monotonic_ns",
            "clock_uncertainty_ns",
        ):
            require_integer(parent_fields, name)
        return parent_fields

    group_fields = value["fields"]
    require_integer(group_fields, "schema_version", positive=True)
    if group_fields["schema_version"] != 1:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    group_session_id = require_session_id(group_fields)
    require_digest(group_fields, "expert_manifest_sha256")
    require_integer(group_fields, "group_sequence", positive=True)
    validate_parent(group_fields["parent"])
    require_integer(group_fields, "parent_output_count", positive=True)
    require_integer(group_fields, "first_expert_seq", positive=True)
    for name in (
        "prior_expert_record_sha256",
        "prior_expert_state_sha256",
        "final_expert_record_sha256",
        "post_expert_state_sha256",
        "post_trace_sha256",
        "group_sha256",
    ):
        require_digest(group_fields, name)

    payload_descriptors: list[tuple[int, str]] = []
    record_field_values: list[dict[str, object]] = []
    for projection in records["$tuple"]:
        record_fields = contract_fields(
            projection,
            "ExpertJournalRecordV1",
        )
        require_integer(record_fields, "schema_version", positive=True)
        if record_fields["schema_version"] != 1:
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        require_session_id(record_fields)
        for name in (
            "expert_manifest_sha256",
            "provider_request_binding_sha256",
            "match_binding_universe_sha256",
            "retention_binding_sha256",
            "event_schema_sha256",
            "prior_expert_record_sha256",
            "prior_expert_state_sha256",
            "post_expert_state_sha256",
            "record_sha256",
        ):
            require_digest(record_fields, name)
        require_integer(record_fields, "expert_seq", positive=True)
        validate_parent(record_fields["parent"])
        require_integer(record_fields, "parent_output_index")
        require_integer(
            record_fields,
            "parent_output_count",
            positive=True,
        )
        event_kind = record_fields["event_kind"]
        if (
            type(event_kind) is not dict
            or event_kind.get("$enum") != "ExpertEventKindV1"
            or type(event_kind.get("value")) is not str
        ):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        if require_integer(
            record_fields,
            "event_version",
            positive=True,
        ) != 1:
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        try:
            event_kind_value = ExpertEventKindV1(event_kind["value"])
        except (TypeError, ValueError):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            ) from None
        if (
            record_fields["event_schema_sha256"]
            != expert_event_schema_resource_sha256(event_kind_value)
        ):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        payload_fields = contract_fields(
            record_fields["payload"],
            "ExpertPayloadDescriptorV1",
        )
        if require_integer(
            payload_fields,
            "schema_version",
            positive=True,
        ) != 1:
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        if (
            require_string(payload_fields, "content_type")
            != "application/vnd.inci.expert+json"
            or require_string(payload_fields, "payload_encoding")
            != "canonical-json-v1"
        ):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        expected_payload_contract = {
            ExpertEventKindV1.SYNCHRONIZATION_APPLIED: (
                "ExpertSynchronizationAppliedPayloadV1"
            ),
            ExpertEventKindV1.OBSERVATION_IGNORED: (
                "ExpertObservationIgnoredPayloadV1"
            ),
            ExpertEventKindV1.OBSERVATION_REJECTED: (
                "ExpertObservationRejectedPayloadV1"
            ),
        }[event_kind_value]
        if (
            require_string(payload_fields, "payload_contract_name")
            != expected_payload_contract
        ):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        payload_length = require_integer(payload_fields, "payload_length")
        if payload_length > MAX_EXPERT_EVENT_PAYLOAD_BYTES:
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        require_digest(payload_fields, "payload_sha256")
        if (
            record_fields["record_sha256"]
            != raw_self_digest(
                record_fields,
                contract_name="ExpertJournalRecordV1",
                digest_field="record_sha256",
                domain=b"INCI-EXPERT-JOURNAL-RECORD-V1\0",
            )
        ):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        record_field_values.append(record_fields)
        payload_descriptors.append(
            (
                payload_length,
                payload_fields["payload_sha256"],
            )
        )

    trace_field_values: list[dict[str, object]] = []
    for projection in trace_steps["$tuple"]:
        trace_fields = contract_fields(
            projection,
            "ExpertTraceStepV1",
        )
        require_integer(trace_fields, "schema_version", positive=True)
        if trace_fields["schema_version"] != 1:
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        require_integer(trace_fields, "expert_seq", positive=True)
        for name in (
            "prior_trace_sha256",
            "expert_record_sha256",
            "post_expert_state_sha256",
            "post_trace_sha256",
        ):
            require_digest(trace_fields, name)
        if (
            trace_fields["post_trace_sha256"]
            != raw_self_digest(
                trace_fields,
                contract_name="ExpertTraceStepV1",
                digest_field="post_trace_sha256",
                domain=b"INCI-EXPERT-TRACE-STEP-V1\0",
            )
        ):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        trace_field_values.append(trace_fields)

    if (
        group_fields["parent_output_count"] != len(record_field_values)
        or group_fields["parent_output_count"]
        > MAX_EXPERT_OUTCOMES_PER_PARENT
    ):
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    expected_record = group_fields["prior_expert_record_sha256"]
    expected_state = group_fields["prior_expert_state_sha256"]
    expected_trace = trace_field_values[0]["prior_trace_sha256"]
    for offset, (record_fields, trace_fields) in enumerate(
        zip(record_field_values, trace_field_values, strict=True)
    ):
        if (
            record_fields["session_id"] != group_session_id
            or record_fields["expert_manifest_sha256"]
            != group_fields["expert_manifest_sha256"]
            or record_fields["parent"] != group_fields["parent"]
            or record_fields["parent_output_count"]
            != group_fields["parent_output_count"]
            or record_fields["parent_output_index"] != offset
            or record_fields["parent_output_index"]
            >= record_fields["parent_output_count"]
            or record_fields["parent_output_count"]
            > MAX_EXPERT_OUTCOMES_PER_PARENT
            or record_fields["expert_seq"]
            != group_fields["first_expert_seq"] + offset
            or record_fields["prior_expert_record_sha256"]
            != expected_record
            or record_fields["prior_expert_state_sha256"] != expected_state
            or trace_fields["expert_seq"] != record_fields["expert_seq"]
            or trace_fields["prior_trace_sha256"] != expected_trace
            or trace_fields["expert_record_sha256"]
            != record_fields["record_sha256"]
            or trace_fields["post_expert_state_sha256"]
            != record_fields["post_expert_state_sha256"]
        ):
            raise ExpertJournalCodecError(
                "expert_journal_metadata_invalid"
            )
        expected_record = record_fields["record_sha256"]
        expected_state = record_fields["post_expert_state_sha256"]
        expected_trace = trace_fields["post_trace_sha256"]
    if (
        group_fields["final_expert_record_sha256"] != expected_record
        or group_fields["post_expert_state_sha256"] != expected_state
        or group_fields["post_trace_sha256"] != expected_trace
        or group_fields["group_sha256"]
        != raw_self_digest(
            group_fields,
            contract_name="ExpertJournalGroupV1",
            digest_field="group_sha256",
            domain=b"INCI-EXPERT-JOURNAL-GROUP-V1\0",
        )
    ):
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    try:
        canonical = json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError):
        raise ExpertJournalCodecError(
            "expert_journal_metadata_invalid"
        ) from None
    if canonical != metadata:
        raise ExpertJournalCodecError("expert_journal_metadata_invalid")
    return (
        group_sequence,
        len(records["$tuple"]),
        tuple(payload_descriptors),
    )


def decode_expert_event_payload(
    payload: bytes,
    *,
    event_kind: ExpertEventKindV1,
    event_version: int,
) -> ExpertEventPayloadV1:
    if type(payload) is not bytes:
        raise TypeError("payload")
    if type(event_kind) is not ExpertEventKindV1:
        raise TypeError("event_kind")
    if type(event_version) is not int:
        raise TypeError("event_version")
    if (
        event_version != 1
        or len(payload) > MAX_EXPERT_EVENT_PAYLOAD_BYTES
    ):
        raise ExpertJournalCodecError("expert_event_payload_invalid")
    expected_type = {
        ExpertEventKindV1.SYNCHRONIZATION_APPLIED: (
            ExpertSynchronizationAppliedPayloadV1
        ),
        ExpertEventKindV1.OBSERVATION_IGNORED: (
            ExpertObservationIgnoredPayloadV1
        ),
        ExpertEventKindV1.OBSERVATION_REJECTED: (
            ExpertObservationRejectedPayloadV1
        ),
    }[event_kind]
    try:
        decoded = _decode_contract_metadata(payload, expected_type)
    except ExpertJournalCodecError:
        raise ExpertJournalCodecError("expert_event_payload_invalid") from None
    if type(decoded) is not expected_type:
        raise ExpertJournalCodecError("expert_event_payload_invalid")
    return decoded  # type: ignore[return-value]


def _encode_expert_frame(
    *,
    kind: int,
    frame_sequence: int,
    metadata: bytes,
    payload_area: bytes,
) -> bytes:
    if type(kind) is not int or type(frame_sequence) is not int:
        raise TypeError("frame_header")
    if type(metadata) is not bytes or type(payload_area) is not bytes:
        raise TypeError("frame_body")
    total_frame_bytes = (
        EXPERT_FRAME_FIXED_BYTES + len(metadata) + len(payload_area)
    )
    try:
        prefix = _EXPERT_FRAME_PREFIX.pack(
            EXPERT_FRAME_MAGIC,
            EXPERT_FRAME_VERSION,
            kind,
            EXPERT_FRAME_FLAGS,
            frame_sequence,
            total_frame_bytes,
            len(metadata),
            len(payload_area),
        )
    except (OverflowError, struct.error):
        raise ExpertJournalCodecError("expert_journal_frame_invalid") from None
    decode_expert_frame_prefix(prefix)
    digest = sha256(
        EXPERT_FRAME_DIGEST_DOMAIN + prefix + metadata + payload_area
    ).digest()
    trailer = _EXPERT_FRAME_TRAILER.pack(
        total_frame_bytes,
        digest,
        EXPERT_FRAME_TRAILER_MAGIC,
    )
    validate_expert_frame_parts(prefix, metadata, payload_area, trailer)
    return prefix + metadata + payload_area + trailer


def _require_exact_contract(value: object, cls: type[object], name: str) -> None:
    if type(value) is not cls:
        raise TypeError(name)
    try:
        cls.__post_init__(value)  # type: ignore[attr-defined]
    except BaseException:
        raise ExpertJournalCodecError("expert_journal_contract_invalid") from None


def _validate_group_cursor(
    group: ExpertJournalGroupV1,
    prior_cursor: ExpertJournalCursorV1,
) -> None:
    _require_exact_contract(group, ExpertJournalGroupV1, "group")
    _require_exact_contract(
        prior_cursor,
        ExpertJournalCursorV1,
        "prior_cursor",
    )
    _validate_genesis_cursor_anchor(
        prior_cursor,
        expert_manifest_sha256=group.expert_manifest_sha256,
    )
    if (
        group.session_id != prior_cursor.session_id
        or group.group_sequence != prior_cursor.group_count + 1
        or group.parent.ingest_seq <= prior_cursor.last_parent_ingest_seq
        or group.first_expert_seq != prior_cursor.expert_seq + 1
        or group.prior_expert_record_sha256
        != prior_cursor.expert_record_sha256
        or group.prior_expert_state_sha256
        != prior_cursor.expert_state_sha256
        or group.trace_steps[0].prior_trace_sha256
        != prior_cursor.expert_trace_sha256
    ):
        raise ExpertJournalCodecError("expert_journal_group_cursor_mismatch")


def _validate_genesis_cursor_anchor(
    cursor: ExpertJournalCursorV1,
    *,
    expert_manifest_sha256: str,
) -> None:
    if cursor.group_count != 0:
        return
    if (
        cursor.record_count != 0
        or cursor.last_parent_ingest_seq != 0
        or cursor.expert_seq != 0
        or cursor.expert_record_sha256 != expert_manifest_sha256
        or cursor.expert_trace_sha256
        != expert_trace_seed_sha256(
            cursor.session_id,
            expert_manifest_sha256,
            cursor.expert_state_sha256,
        )
    ):
        raise ExpertJournalCodecError(
            "expert_journal_genesis_cursor_mismatch"
        )


def _validate_payload_descriptors(
    group: ExpertJournalGroupV1,
    payloads: tuple[bytes, ...],
) -> None:
    if len(payloads) != len(group.records):
        raise ExpertJournalCodecError("expert_journal_group_payload_mismatch")
    for record, payload in zip(group.records, payloads, strict=True):
        if (
            record.payload.payload_length != len(payload)
            or record.payload.payload_sha256 != sha256(payload).hexdigest()
        ):
            raise ExpertJournalCodecError(
                "expert_journal_group_payload_mismatch"
            )


def _validate_terminal_cursor(
    terminal: ExpertSessionTerminalV1,
    final_cursor: ExpertJournalCursorV1,
) -> None:
    _require_exact_contract(terminal, ExpertSessionTerminalV1, "terminal")
    _require_exact_contract(
        final_cursor,
        ExpertJournalCursorV1,
        "final_cursor",
    )
    _validate_genesis_cursor_anchor(
        final_cursor,
        expert_manifest_sha256=terminal.expert_manifest_sha256,
    )
    if (
        terminal.session_id != final_cursor.session_id
        or terminal.expert_group_count != final_cursor.group_count
        or terminal.expert_record_count != final_cursor.record_count
        or terminal.last_parent_ingest_seq
        != final_cursor.last_parent_ingest_seq
        or terminal.last_parent_record_sha256
        != final_cursor.last_parent_record_sha256
        or terminal.final_expert_seq != final_cursor.expert_seq
        or terminal.final_expert_record_sha256
        != final_cursor.expert_record_sha256
        or terminal.final_expert_state_sha256
        != final_cursor.expert_state_sha256
        or terminal.final_expert_trace_sha256
        != final_cursor.expert_trace_sha256
    ):
        raise ExpertJournalCodecError(
            "expert_journal_terminal_cursor_mismatch"
        )


def encode_expert_manifest_frame(
    manifest: ExpertSessionManifestV1,
) -> bytes:
    _require_exact_contract(
        manifest,
        ExpertSessionManifestV1,
        "manifest",
    )
    metadata = canonical_expert_bytes(manifest)
    if len(metadata) > MAX_EXPERT_MANIFEST_METADATA_BYTES:
        raise ExpertJournalCodecError("expert_journal_manifest_too_large")
    return _encode_expert_frame(
        kind=EXPERT_FRAME_KIND_MANIFEST,
        frame_sequence=0,
        metadata=metadata,
        payload_area=b"",
    )


def decode_expert_manifest_frame(
    frame: bytes,
) -> ExpertSessionManifestV1:
    kind, frame_sequence, metadata, payload_area, _ = (
        decode_expert_complete_frame(frame)
    )
    if (
        kind != EXPERT_FRAME_KIND_MANIFEST
        or frame_sequence != 0
        or payload_area
    ):
        raise ExpertJournalCodecError("expert_journal_manifest_frame_invalid")
    decoded = _decode_contract_metadata(metadata, ExpertSessionManifestV1)
    assert type(decoded) is ExpertSessionManifestV1
    return decoded


def encode_expert_group_frame(
    group: ExpertJournalGroupV1,
    payloads: tuple[bytes, ...],
    *,
    prior_cursor: ExpertJournalCursorV1,
) -> bytes:
    validate_expert_group_against_cursor(group, payloads, prior_cursor)
    payload_area = encode_expert_group_payload_area(payloads)
    metadata = canonical_expert_bytes(group)
    if len(metadata) > MAX_EXPERT_GROUP_METADATA_BYTES:
        raise ExpertJournalCodecError("expert_journal_group_too_large")
    return _encode_expert_frame(
        kind=EXPERT_FRAME_KIND_PARENT_GROUP,
        frame_sequence=group.group_sequence,
        metadata=metadata,
        payload_area=payload_area,
    )


def decode_expert_group_frame_structural(
    frame: bytes,
) -> tuple[ExpertJournalGroupV1, tuple[bytes, ...]]:
    kind, frame_sequence, metadata, payload_area, _ = (
        decode_expert_complete_frame(frame)
    )
    if kind != EXPERT_FRAME_KIND_PARENT_GROUP:
        raise ExpertJournalCodecError("expert_journal_group_frame_invalid")
    decoded = _decode_contract_metadata(metadata, ExpertJournalGroupV1)
    assert type(decoded) is ExpertJournalGroupV1
    if frame_sequence != decoded.group_sequence:
        raise ExpertJournalCodecError("expert_journal_group_frame_invalid")
    payloads = decode_expert_group_payload_area(
        payload_area,
        expected_count=len(decoded.records),
    )
    _validate_payload_descriptors(decoded, payloads)
    return decoded, payloads


def validate_expert_group_against_cursor(
    group: ExpertJournalGroupV1,
    payloads: tuple[bytes, ...],
    prior_cursor: ExpertJournalCursorV1,
) -> None:
    _validate_group_cursor(group, prior_cursor)
    encode_expert_group_payload_area(payloads)
    _validate_payload_descriptors(group, payloads)


def decode_expert_group_frame(
    frame: bytes,
    *,
    prior_cursor: ExpertJournalCursorV1,
) -> tuple[ExpertJournalGroupV1, tuple[bytes, ...]]:
    group, payloads = decode_expert_group_frame_structural(frame)
    validate_expert_group_against_cursor(group, payloads, prior_cursor)
    return group, payloads


def encode_expert_terminal_frame(
    terminal: ExpertSessionTerminalV1,
    *,
    final_cursor: ExpertJournalCursorV1,
) -> bytes:
    validate_expert_terminal_against_cursor(terminal, final_cursor)
    metadata = canonical_expert_bytes(terminal)
    if len(metadata) > MAX_EXPERT_TERMINAL_METADATA_BYTES:
        raise ExpertJournalCodecError("expert_journal_terminal_too_large")
    return _encode_expert_frame(
        kind=EXPERT_FRAME_KIND_TERMINAL,
        frame_sequence=final_cursor.group_count + 1,
        metadata=metadata,
        payload_area=b"",
    )


def decode_expert_terminal_frame_structural(
    frame: bytes,
) -> ExpertSessionTerminalV1:
    kind, frame_sequence, metadata, payload_area, _ = (
        decode_expert_complete_frame(frame)
    )
    if kind != EXPERT_FRAME_KIND_TERMINAL or payload_area:
        raise ExpertJournalCodecError("expert_journal_terminal_frame_invalid")
    decoded = _decode_contract_metadata(metadata, ExpertSessionTerminalV1)
    assert type(decoded) is ExpertSessionTerminalV1
    if (
        decoded.evidence_raw_count != decoded.expert_group_count
        or decoded.expert_record_count < decoded.expert_group_count
        or decoded.final_expert_seq != decoded.expert_record_count
        or (decoded.expert_group_count == 0)
        != (decoded.expert_record_count == 0)
        or (decoded.expert_group_count == 0)
        != (decoded.last_parent_ingest_seq == 0)
        or (decoded.expert_group_count == 0)
        != (decoded.final_expert_seq == 0)
        or frame_sequence != decoded.expert_group_count + 1
    ):
        raise ExpertJournalCodecError("expert_journal_terminal_frame_invalid")
    return decoded


def decode_expert_terminal_frame_replay_material(
    frame: bytes,
) -> ExpertSessionTerminalV1:
    """Decode terminal fields while deferring semantic mismatch checks.

    Replay must classify terminal reason/count/provenance/state/trace
    differences in its ruled order.  This decoder therefore validates the
    canonical frame and exact field projection, but deliberately does not
    run ``ExpertSessionTerminalV1.__post_init__`` or cursor/count equations.
    """

    kind, _, metadata, payload_area, _ = decode_expert_complete_frame(frame)
    if kind != EXPERT_FRAME_KIND_TERMINAL or payload_area:
        raise ExpertJournalCodecError("expert_journal_terminal_frame_invalid")
    try:
        document = json.loads(
            metadata.decode("ascii"),
            object_pairs_hook=_strict_pairs,
            parse_float=_reject_json_number,
            parse_constant=_reject_json_number,
        )
    except ExpertJournalCodecError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise ExpertJournalCodecError(
            "expert_journal_terminal_frame_invalid"
        ) from None
    if (
        type(document) is not dict
        or set(document) != {"canonical_version", "domain", "value"}
        or document["canonical_version"] != 1
        or document["domain"] != "inci-tennis-expert"
    ):
        raise ExpertJournalCodecError("expert_journal_terminal_frame_invalid")
    value = document["value"]
    _validate_diagnostic_projection_shape(value, depth=0)
    if (
        type(value) is not dict
        or value.get("$contract") != "ExpertSessionTerminalV1"
        or type(value.get("fields")) is not dict
    ):
        raise ExpertJournalCodecError("expert_journal_terminal_frame_invalid")
    projected_fields = value["fields"]
    expected_fields = fields(ExpertSessionTerminalV1)
    if set(projected_fields) != {item.name for item in expected_fields}:
        raise ExpertJournalCodecError("expert_journal_terminal_frame_invalid")
    try:
        canonical = json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError):
        raise ExpertJournalCodecError(
            "expert_journal_terminal_frame_invalid"
        ) from None
    if canonical != metadata:
        raise ExpertJournalCodecError("expert_journal_terminal_frame_invalid")

    integer_fields = {
        "schema_version",
        "evidence_terminal_ingest_seq",
        "evidence_raw_count",
        "evidence_derived_count",
        "expert_group_count",
        "expert_record_count",
        "last_parent_ingest_seq",
        "final_expert_seq",
    }
    boolean_fields = {
        "evidence_terminal_clean",
        "clean",
        "research_evaluable",
    }
    enum_projection = projected_fields["reason"]
    if (
        type(enum_projection) is not dict
        or enum_projection.get("$enum") != "ExpertTerminalReasonV1"
        or type(enum_projection.get("value")) is not str
    ):
        raise ExpertJournalCodecError("expert_journal_terminal_frame_invalid")
    try:
        reason = _registered_enum("ExpertTerminalReasonV1")(
            enum_projection["value"]
        )
    except (TypeError, ValueError):
        raise ExpertJournalCodecError(
            "expert_journal_terminal_frame_invalid"
        ) from None
    values: dict[str, object] = {}
    for item in expected_fields:
        name = item.name
        projected = projected_fields[name]
        if name == "reason":
            values[name] = reason
        elif name in integer_fields:
            if type(projected) is not int or projected < 0:
                raise ExpertJournalCodecError(
                    "expert_journal_terminal_frame_invalid"
                )
            values[name] = projected
        elif name in boolean_fields:
            if type(projected) is not bool:
                raise ExpertJournalCodecError(
                    "expert_journal_terminal_frame_invalid"
                )
            values[name] = projected
        else:
            if type(projected) is not str:
                raise ExpertJournalCodecError(
                    "expert_journal_terminal_frame_invalid"
                )
            values[name] = projected
    if values["schema_version"] != 1:
        raise ExpertJournalCodecError("expert_journal_terminal_frame_invalid")
    terminal = object.__new__(ExpertSessionTerminalV1)
    for item in expected_fields:
        object.__setattr__(terminal, item.name, values[item.name])
    return terminal


def validate_expert_terminal_against_cursor(
    terminal: ExpertSessionTerminalV1,
    final_cursor: ExpertJournalCursorV1,
) -> None:
    _validate_terminal_cursor(terminal, final_cursor)


def decode_expert_terminal_frame(
    frame: bytes,
    *,
    final_cursor: ExpertJournalCursorV1,
) -> ExpertSessionTerminalV1:
    terminal = decode_expert_terminal_frame_structural(frame)
    validate_expert_terminal_against_cursor(terminal, final_cursor)
    return terminal
