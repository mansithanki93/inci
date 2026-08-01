from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "inci_tennis_adapters"
    / "schemas"
    / "sportradar-tennis-qualification-output-v1.schema.json"
)
COORDINATE_FIELDS = (
    "first_correction_epoch",
    "first_revision",
    "last_correction_epoch",
    "last_revision",
)
CAPABILITIES = (
    ("correction_semantics", True),
    ("current_server", True),
    ("match_format", True),
    ("monotonic_sequence_or_revision", True),
    ("point_state", True),
    ("provider_generated_time", True),
    ("resync_snapshot", True),
    ("source_event_time", True),
    ("stable_match_ids", True),
    ("stable_player_ids", True),
)
REJECTION_REASONS = (
    "normalizer_schema_unknown",
    "normalizer_payload_invalid",
    "normalizer_contract_violation",
    "normalizer_exception",
)


def _schema_matches(
    instance: object,
    schema: dict[str, object],
    root: dict[str, object],
) -> bool:
    reference = schema.get("$ref")
    if reference is not None:
        if (
            type(reference) is not str
            or not reference.startswith("#/$defs/")
        ):
            return False
        target = root.get("$defs", {}).get(
            reference.removeprefix("#/$defs/")
        )
        return (
            type(target) is dict
            and _schema_matches(instance, target, root)
        )

    all_of = schema.get("allOf")
    if all_of is not None and (
        type(all_of) is not list
        or not all(
            type(item) is dict
            and _schema_matches(instance, item, root)
            for item in all_of
        )
    ):
        return False
    one_of = schema.get("oneOf")
    if one_of is not None and (
        type(one_of) is not list
        or sum(
            type(item) is dict
            and _schema_matches(instance, item, root)
            for item in one_of
        )
        != 1
    ):
        return False

    if "const" in schema and instance != schema["const"]:
        return False
    enum = schema.get("enum")
    if enum is not None and (
        type(enum) is not list or instance not in enum
    ):
        return False

    declared_type = schema.get("type")
    if declared_type is not None:
        type_matches = {
            "null": lambda: instance is None,
            "boolean": lambda: type(instance) is bool,
            "integer": lambda: type(instance) is int,
            "string": lambda: type(instance) is str,
            "array": lambda: type(instance) is list,
            "object": lambda: type(instance) is dict,
        }
        if (
            type(declared_type) is not str
            or declared_type not in type_matches
            or not type_matches[declared_type]()
        ):
            return False

    if type(instance) is str:
        pattern = schema.get("pattern")
        if pattern is not None and (
            type(pattern) is not str
            or re.search(pattern, instance) is None
        ):
            return False
    if type(instance) is int and type(instance) is not bool:
        if "minimum" in schema and instance < schema["minimum"]:
            return False
        if "maximum" in schema and instance > schema["maximum"]:
            return False
    if type(instance) is dict:
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if (
            type(required) is not list
            or type(properties) is not dict
            or any(name not in instance for name in required)
        ):
            return False
        if schema.get("additionalProperties") is False and any(
            name not in properties for name in instance
        ):
            return False
        if any(
            name in instance
            and (
                type(child) is not dict
                or not _schema_matches(instance[name], child, root)
            )
            for name, child in properties.items()
        ):
            return False
    if type(instance) is list:
        if "minItems" in schema and len(instance) < schema["minItems"]:
            return False
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            return False
        prefix_items = schema.get("prefixItems", [])
        if type(prefix_items) is not list:
            return False
        for index, child in enumerate(prefix_items[: len(instance)]):
            if (
                type(child) is not dict
                or not _schema_matches(instance[index], child, root)
            ):
                return False
        items = schema.get("items")
        if items is False and len(instance) > len(prefix_items):
            return False
        if type(items) is dict and any(
            not _schema_matches(item, items, root)
            for item in instance[len(prefix_items) :]
        ):
            return False
    return True


def _parser_result(
    *,
    outcome: str,
    reason: str | None,
    output_count: int,
    coordinates: int | None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": 1,
        "record_type": "parser_result",
        "session_id": "00000000-0000-0000-0000-000000000001",
        "record_index": 1,
        "recorded_wall_ns": 1,
        "retention_delete_by_ns": 2,
        "previous_record_sha256": "a" * 64,
        "capture_record_sha256": "b" * 64,
        "evidence_sha256": "c" * 64,
        "parser_outcome": outcome,
        "reason": reason,
        "output_contract_sha256s": ["d" * 64] * output_count,
        "capabilities": [list(item) for item in CAPABILITIES],
    }
    row.update(
        {field: coordinates for field in COORDINATE_FIELDS}
    )
    return row


class QualificationOutputSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_parser_result_lifecycle_vectors(self) -> None:
        valid = [
            (
                "accepted-single-output",
                _parser_result(
                    outcome="accepted",
                    reason=None,
                    output_count=1,
                    coordinates=0,
                ),
            ),
            (
                "accepted-maximum-outputs",
                _parser_result(
                    outcome="accepted",
                    reason=None,
                    output_count=64,
                    coordinates=1,
                ),
            ),
            (
                "ignored",
                _parser_result(
                    outcome="ignored",
                    reason="event_not_relevant",
                    output_count=0,
                    coordinates=None,
                ),
            ),
        ]
        valid.extend(
            (
                f"rejected-{reason}",
                _parser_result(
                    outcome="rejected",
                    reason=reason,
                    output_count=0,
                    coordinates=None,
                ),
            )
            for reason in REJECTION_REASONS
        )

        invalid = [
            (
                "accepted-without-output",
                _parser_result(
                    outcome="accepted",
                    reason=None,
                    output_count=0,
                    coordinates=0,
                ),
            ),
            (
                "accepted-with-rejection-reason",
                _parser_result(
                    outcome="accepted",
                    reason=REJECTION_REASONS[0],
                    output_count=1,
                    coordinates=0,
                ),
            ),
            (
                "accepted-with-ignored-reason",
                _parser_result(
                    outcome="accepted",
                    reason="event_not_relevant",
                    output_count=1,
                    coordinates=0,
                ),
            ),
            (
                "accepted-over-output-limit",
                _parser_result(
                    outcome="accepted",
                    reason=None,
                    output_count=65,
                    coordinates=0,
                ),
            ),
            (
                "ignored-with-output",
                _parser_result(
                    outcome="ignored",
                    reason="event_not_relevant",
                    output_count=1,
                    coordinates=None,
                ),
            ),
            (
                "ignored-with-rejection-reason",
                _parser_result(
                    outcome="ignored",
                    reason=REJECTION_REASONS[0],
                    output_count=0,
                    coordinates=None,
                ),
            ),
            (
                "ignored-with-null-reason",
                _parser_result(
                    outcome="ignored",
                    reason=None,
                    output_count=0,
                    coordinates=None,
                ),
            ),
            (
                "rejected-with-output",
                _parser_result(
                    outcome="rejected",
                    reason=REJECTION_REASONS[0],
                    output_count=1,
                    coordinates=None,
                ),
            ),
            (
                "rejected-with-ignored-reason",
                _parser_result(
                    outcome="rejected",
                    reason="event_not_relevant",
                    output_count=0,
                    coordinates=None,
                ),
            ),
            (
                "rejected-with-null-reason",
                _parser_result(
                    outcome="rejected",
                    reason=None,
                    output_count=0,
                    coordinates=None,
                ),
            ),
        ]
        for coordinate in COORDINATE_FIELDS:
            accepted = _parser_result(
                outcome="accepted",
                reason=None,
                output_count=1,
                coordinates=0,
            )
            accepted[coordinate] = None
            invalid.append(
                (f"accepted-null-{coordinate}", accepted)
            )

            ignored = _parser_result(
                outcome="ignored",
                reason="event_not_relevant",
                output_count=0,
                coordinates=None,
            )
            ignored[coordinate] = 0
            invalid.append(
                (f"ignored-nonnull-{coordinate}", ignored)
            )

            rejected = _parser_result(
                outcome="rejected",
                reason=REJECTION_REASONS[0],
                output_count=0,
                coordinates=None,
            )
            rejected[coordinate] = 0
            invalid.append(
                (f"rejected-nonnull-{coordinate}", rejected)
            )

        for name, row in valid:
            with self.subTest(vector=name):
                self.assertTrue(
                    _schema_matches(row, self.schema, self.schema)
                )
        for name, row in invalid:
            with self.subTest(vector=name):
                self.assertFalse(
                    _schema_matches(row, self.schema, self.schema)
                )


if __name__ == "__main__":
    unittest.main()
