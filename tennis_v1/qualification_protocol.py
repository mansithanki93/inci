"""Code-owned Tennis v1 provider qualification protocol."""

from __future__ import annotations

import hashlib

from .canonical import canonical_json_bytes


QUALIFICATION_PROTOCOL_V1 = {
    "schema_version": 1,
    "interval_semantics": "half_open_end_before_start",
    "summary_authority": "digest_pinned_trace",
    "maximum_validity_days": 30,
    "required_capabilities": [
        "correction_semantics",
        "current_server",
        "match_format",
        "monotonic_sequence_or_revision",
        "point_state",
        "provider_generated_time",
        "resync_snapshot",
        "source_event_time",
        "stable_match_ids",
        "stable_player_ids",
    ],
}


def qualification_protocol_sha256() -> str:
    return hashlib.sha256(
        b"INCI-QUALIFICATION-PROTOCOL-V1\0"
        + canonical_json_bytes(QUALIFICATION_PROTOCOL_V1)
    ).hexdigest()
