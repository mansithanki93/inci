"""Strict read-only projection of current Sportradar Tennis v3 REST data."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, replace as dataclass_replace
from decimal import Decimal
from hashlib import sha256
import json
import re
from datetime import datetime, timezone


_MATCH_ID = r"sr:sport_event:[1-9][0-9]*\Z"
_COMPETITOR_ID = r"sr:competitor:[1-9][0-9]*\Z"
_STATUSES = frozenset(
    {
        "not_started",
        "match_about_to_start",
        "started",
        "postponed",
        "suspended",
        "cancelled",
        "delayed",
        "live",
        "interrupted",
        "ended",
        "closed",
        "abandoned",
    }
)
_MATCH_STATUSES = frozenset(
    {
        "not_started",
        "match_about_to_start",
        "live",
        "closed",
        "ended",
        "interrupted",
        "defaulted",
        "postponed",
        "cancelled",
        "walkover",
        "1st_set",
        "2nd_set",
        "3rd_set",
        "4th_set",
        "5th_set",
        "retired",
        "start_delayed",
        "suspended",
        "started",
        "abandoned",
        "delayed",
    }
)
_TOP_LEVEL_SUMMARY_KEYS = frozenset(
    {"generated_at", "sport_event", "sport_event_status", "statistics"}
)
_TOP_LEVEL_TIMELINE_KEYS = _TOP_LEVEL_SUMMARY_KEYS | {"timeline"}
_TOP_LEVEL_LIVE_SUMMARIES_KEYS = frozenset({"generated_at", "summaries"})
_LIVE_SUMMARY_KEYS = frozenset(
    {"sport_event", "sport_event_status", "statistics"}
)
_EVENT_TYPES = frozenset(
    {
        "match_started",
        "match_called",
        "deciding_team",
        "first_serve",
        "period_start",
        "point",
        "period_score",
        "match_ended",
        "match_suspended",
        "match_resumed",
        "tie_break_points",
        "service_fault",
        "ball_position",
    }
)
_EVENT_RESULTS = frozenset(
    {"ace", "double_fault", "server_won", "receiver_won", "unknown"}
)
_SUSPENDED_REASONS = frozenset(
    {"toilet_break", "bad_weather", "trainer_called"}
)
_MAXIMUM_PAYLOAD_BYTES = 8_388_608
_EVENT_KEYS = frozenset(
    {
        "away_score",
        "competitor",
        "first_serve_fault",
        "home_score",
        "id",
        "period",
        "period_name",
        "reason",
        "result",
        "server",
        "time",
        "type",
        "updated",
        "updated_time",
    }
)


class SportradarWireContractError(ValueError):
    """A response cannot be trusted as the expected Tennis v3 contract."""

    def __init__(self, code: str = "sportradar_wire_contract_invalid") -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str = "sportradar_wire_contract_invalid") -> None:
    raise SportradarWireContractError(code)


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            _fail("sportradar_duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_: str) -> object:
    _fail("sportradar_nonfinite_number")


def _decode(payload: bytes) -> dict[str, object]:
    if (
        type(payload) is not bytes
        or not payload
        or len(payload) > _MAXIMUM_PAYLOAD_BYTES
    ):
        _fail()
    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=Decimal,
            parse_constant=_reject_constant,
        )
    except SportradarWireContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail()
    if type(decoded) is not dict:
        _fail()
    return decoded


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        _fail()
    return value


def _array(value: object, *, maximum: int = 1_000) -> list[object]:
    if type(value) is not list or len(value) > maximum:
        _fail()
    return value


def _text(value: object, *, maximum: int = 256) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        _fail()
    return value


def _optional_text(value: object, *, maximum: int = 256) -> str | None:
    if value is None:
        return None
    return _text(value, maximum=maximum)


def _integer(value: object, *, maximum: int = 1_000_000) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        _fail()
    return value


def _optional_integer(
    value: object, *, maximum: int = 1_000_000
) -> int | None:
    if value is None:
        return None
    return _integer(value, maximum=maximum)


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        _fail()
    return value


def _utc_ns(value: object) -> int:
    text = _text(value, maximum=64)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        _fail("sportradar_timestamp_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("sportradar_timestamp_invalid")
    parsed = parsed.astimezone(timezone.utc)
    seconds = calendar.timegm(parsed.utctimetuple())
    return seconds * 1_000_000_000 + parsed.microsecond * 1_000


def _provider_id(value: object, pattern: str) -> str:
    text = _text(value, maximum=64)
    if re.fullmatch(pattern, text, flags=re.ASCII) is None:
        _fail("sportradar_identifier_invalid")
    return text


@dataclass(frozen=True, slots=True)
class SportradarScoreSnapshot:
    provider_match_id: str
    generated_wall_ns: int
    start_wall_ns: int
    best_of: int | None
    home_id: str
    home_name: str
    away_id: str
    away_name: str
    status: str
    match_status: str | None
    sets_home: int | None
    sets_away: int | None
    games_home: int | None
    games_away: int | None
    points_home: str
    points_away: str
    serving: str | None
    in_tiebreak: bool | None
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class SportradarTimelineEvent:
    event_id: int
    event_type: str
    event_wall_ns: int
    home_score: int | None
    away_score: int | None
    competitor: str | None
    server: str | None
    result: str | None
    first_serve_fault: bool | None
    period: int | None
    period_name: str | None
    reason: str | None
    updated: bool
    updated_wall_ns: int | None


@dataclass(frozen=True, slots=True)
class SportradarTimelineSnapshot:
    score: SportradarScoreSnapshot
    events: tuple[SportradarTimelineEvent, ...]
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class SportradarLiveSummariesSnapshot:
    generated_wall_ns: int
    snapshots: tuple[SportradarScoreSnapshot, ...]
    payload_sha256: str


def _competitors(
    sport_event: dict[str, object],
) -> tuple[str, str, str, str]:
    values = _array(sport_event.get("competitors"), maximum=2)
    if len(values) != 2:
        _fail("sportradar_competitors_invalid")
    selected: dict[str, tuple[str, str]] = {}
    for raw in values:
        competitor = _object(raw)
        qualifier = _text(competitor.get("qualifier"), maximum=8)
        if qualifier not in {"home", "away"} or qualifier in selected:
            _fail("sportradar_competitors_invalid")
        selected[qualifier] = (
            _provider_id(competitor.get("id"), _COMPETITOR_ID),
            _text(competitor.get("name")),
        )
    if set(selected) != {"home", "away"}:
        _fail("sportradar_competitors_invalid")
    return (*selected["home"], *selected["away"])


def _best_of(sport_event: dict[str, object]) -> int | None:
    raw_context = sport_event.get("sport_event_context")
    if raw_context is None:
        return None
    context = _object(raw_context)
    raw_mode = context.get("mode")
    if raw_mode is None:
        return None
    mode = _object(raw_mode)
    raw_value = mode.get("best_of")
    if raw_value is None:
        return None
    value = _integer(raw_value, maximum=5)
    if value not in {3, 5}:
        _fail("sportradar_match_format_unknown")
    return value


def _last_set(status: dict[str, object]) -> tuple[int | None, int | None]:
    raw_periods = status.get("period_scores", [])
    periods = _array(raw_periods, maximum=5)
    if not periods:
        return None, None
    expected_number = 1
    games_home: int | None = None
    games_away: int | None = None
    for raw in periods:
        period = _object(raw)
        raw_number = period.get("number")
        number = (
            expected_number
            if raw_number is None
            else _integer(raw_number, maximum=5)
        )
        raw_type = period.get("type")
        period_type = (
            None if raw_type is None else _text(raw_type, maximum=16)
        )
        numbered_types = {
            1: "1st_set",
            2: "2nd_set",
            3: "3rd_set",
            4: "4th_set",
            5: "5th_set",
        }
        if number != expected_number or period_type not in {
            None,
            "set",
            numbered_types[number],
            "interrupted",
            "suspended",
        }:
            _fail("sportradar_period_scores_invalid")
        games_home = _optional_integer(period.get("home_score"), maximum=99)
        games_away = _optional_integer(period.get("away_score"), maximum=99)
        expected_number += 1
    return games_home, games_away


def _game_state(
    status: dict[str, object],
) -> tuple[str, str, str | None, bool | None]:
    raw = status.get("game_state")
    if raw is None:
        return "--", "--", None, None
    state = _object(raw)
    raw_home = state.get("home_score")
    raw_away = state.get("away_score")
    home = "--" if raw_home is None else str(_integer(raw_home, maximum=999))
    away = "--" if raw_away is None else str(_integer(raw_away, maximum=999))
    serving = _optional_text(state.get("serving"), maximum=8)
    if serving not in {None, "home", "away"}:
        _fail("sportradar_server_invalid")
    raw_tie_break = state.get("tie_break")
    tie_break = None if raw_tie_break is None else _boolean(raw_tie_break)
    advantage = _optional_text(state.get("advantage"), maximum=8)
    if advantage not in {None, "home", "away"}:
        _fail("sportradar_point_state_invalid")
    if advantage == "home":
        home = "AD"
    elif advantage == "away":
        away = "AD"
    return home, away, serving, tie_break


def parse_sport_event_summary(
    payload: bytes,
    *,
    expected_match_id: str,
) -> SportradarScoreSnapshot:
    """Parse one official summary response into a display-only snapshot."""

    expected = _provider_id(expected_match_id, _MATCH_ID)
    document = _decode(payload)
    return _parse_summary_document(
        document,
        expected_match_id=expected,
        payload_sha256=sha256(payload).hexdigest(),
    )


def _parse_summary_document(
    document: dict[str, object],
    *,
    expected_match_id: str | None,
    payload_sha256: str,
) -> SportradarScoreSnapshot:
    if not set(document).issubset(_TOP_LEVEL_SUMMARY_KEYS):
        _fail("sportradar_summary_schema_unknown")
    if "generated_at" not in document:
        _fail("sportradar_generated_time_missing")
    sport_event = _object(document.get("sport_event"))
    match_id = _provider_id(sport_event.get("id"), _MATCH_ID)
    if expected_match_id is not None and match_id != expected_match_id:
        _fail("sportradar_match_identity_mismatch")
    generated_wall_ns = _utc_ns(document["generated_at"])
    start_wall_ns = _utc_ns(sport_event.get("start_time"))
    _boolean(sport_event.get("start_time_confirmed"))
    best_of = _best_of(sport_event)
    home_id, home_name, away_id, away_name = _competitors(sport_event)

    status = _object(document.get("sport_event_status"))
    status_value = _text(status.get("status"), maximum=32)
    if status_value not in _STATUSES:
        _fail("sportradar_status_unknown")
    match_status = _optional_text(status.get("match_status"), maximum=32)
    if match_status is not None and match_status not in _MATCH_STATUSES:
        _fail("sportradar_match_status_unknown")
    sets_home = _optional_integer(status.get("home_score"), maximum=5)
    sets_away = _optional_integer(status.get("away_score"), maximum=5)
    games_home, games_away = _last_set(status)
    points_home, points_away, serving, in_tiebreak = _game_state(status)

    return SportradarScoreSnapshot(
        provider_match_id=match_id,
        generated_wall_ns=generated_wall_ns,
        start_wall_ns=start_wall_ns,
        best_of=best_of,
        home_id=home_id,
        home_name=home_name,
        away_id=away_id,
        away_name=away_name,
        status=status_value,
        match_status=match_status,
        sets_home=sets_home,
        sets_away=sets_away,
        games_home=games_home,
        games_away=games_away,
        points_home=points_home,
        points_away=points_away,
        serving=serving,
        in_tiebreak=in_tiebreak,
        payload_sha256=payload_sha256,
    )


def parse_live_summaries_envelope(
    payload: bytes,
) -> SportradarLiveSummariesSnapshot:
    """Parse the live envelope while preserving its provider timestamp."""

    document = _decode(payload)
    if set(document) != _TOP_LEVEL_LIVE_SUMMARIES_KEYS:
        _fail("sportradar_live_summaries_schema_unknown")
    generated_at = document["generated_at"]
    generated_wall_ns = _utc_ns(generated_at)
    digest = sha256(payload).hexdigest()
    snapshots: list[SportradarScoreSnapshot] = []
    seen: set[str] = set()
    for raw in _array(document["summaries"], maximum=1_000):
        summary = _object(raw)
        if not set(summary).issubset(_LIVE_SUMMARY_KEYS):
            _fail("sportradar_live_summary_schema_unknown")
        projected = _parse_summary_document(
            {"generated_at": generated_at, **summary},
            expected_match_id=None,
            payload_sha256=digest,
        )
        if projected.generated_wall_ns != generated_wall_ns:
            _fail("sportradar_generated_time_mismatch")
        if projected.provider_match_id in seen:
            _fail("sportradar_duplicate_match")
        seen.add(projected.provider_match_id)
        snapshots.append(projected)
    return SportradarLiveSummariesSnapshot(
        generated_wall_ns=generated_wall_ns,
        snapshots=tuple(snapshots),
        payload_sha256=digest,
    )


def parse_live_summaries(payload: bytes) -> tuple[SportradarScoreSnapshot, ...]:
    """Parse the official live schedule envelope for match selection only."""

    return parse_live_summaries_envelope(payload).snapshots


def _timeline_event(raw: object) -> SportradarTimelineEvent:
    value = _object(raw)
    if not set(value).issubset(_EVENT_KEYS):
        _fail("sportradar_timeline_schema_unknown")
    event_id = _integer(value.get("id"), maximum=9_223_372_036_854_775_807)
    event_type = _text(value.get("type"), maximum=32)
    if event_type not in _EVENT_TYPES:
        _fail("sportradar_timeline_event_unknown")
    event_wall_ns = _utc_ns(value.get("time"))
    competitor = _optional_text(value.get("competitor"), maximum=8)
    if competitor not in {None, "home", "away"}:
        _fail("sportradar_timeline_competitor_invalid")
    server = _optional_text(value.get("server"), maximum=8)
    if server not in {None, "home", "away", "unknown"}:
        _fail("sportradar_timeline_server_invalid")
    result = _optional_text(value.get("result"), maximum=32)
    if result is not None and result not in _EVENT_RESULTS:
        _fail("sportradar_timeline_result_unknown")
    raw_first_serve_fault = value.get("first_serve_fault")
    first_serve_fault = (
        None
        if raw_first_serve_fault is None
        else _boolean(raw_first_serve_fault)
    )
    raw_period = value.get("period")
    if raw_period is None:
        period = None
    elif type(raw_period) is int:
        period = _integer(raw_period, maximum=5)
        if period == 0:
            _fail("sportradar_timeline_period_invalid")
    elif (
        type(raw_period) is str
        and re.fullmatch(r"[1-5]", raw_period, flags=re.ASCII) is not None
    ):
        period = int(raw_period)
    else:
        _fail("sportradar_timeline_period_invalid")
    period_name = _optional_text(value.get("period_name"), maximum=64)
    reason = _optional_text(value.get("reason"), maximum=32)
    if reason is not None and reason not in _SUSPENDED_REASONS:
        _fail("sportradar_timeline_reason_unknown")
    updated = value.get("updated", False)
    if type(updated) is not bool:
        _fail("sportradar_timeline_update_invalid")
    raw_updated_time = value.get("updated_time")
    updated_wall_ns = (
        None if raw_updated_time is None else _utc_ns(raw_updated_time)
    )
    if updated and updated_wall_ns is None:
        _fail("sportradar_timeline_update_invalid")
    return SportradarTimelineEvent(
        event_id=event_id,
        event_type=event_type,
        event_wall_ns=event_wall_ns,
        home_score=_optional_integer(value.get("home_score"), maximum=999),
        away_score=_optional_integer(value.get("away_score"), maximum=999),
        competitor=competitor,
        server=server,
        result=result,
        first_serve_fault=first_serve_fault,
        period=period,
        period_name=period_name,
        reason=reason,
        updated=updated,
        updated_wall_ns=updated_wall_ns,
    )


def parse_sport_event_timeline(
    payload: bytes,
    *,
    expected_match_id: str,
) -> SportradarTimelineSnapshot:
    """Parse a complete per-match timeline without inventing revisions."""

    document = _decode(payload)
    if not set(document).issubset(_TOP_LEVEL_TIMELINE_KEYS):
        _fail("sportradar_timeline_schema_unknown")
    if "timeline" not in document:
        _fail("sportradar_timeline_missing")
    summary_document: dict[str, object] = {
        name: document[name]
        for name in ("generated_at", "sport_event", "sport_event_status")
        if name in document
    }
    expected = _provider_id(expected_match_id, _MATCH_ID)
    digest = sha256(payload).hexdigest()
    score = _parse_summary_document(
        summary_document,
        expected_match_id=expected,
        payload_sha256=digest,
    )
    events = tuple(
        _timeline_event(item)
        for item in _array(document["timeline"], maximum=10_000)
    )
    previous_id = -1
    for event in events:
        if event.event_id <= previous_id:
            _fail("sportradar_timeline_order_invalid")
        previous_id = event.event_id
    return SportradarTimelineSnapshot(
        score=dataclass_replace(score, payload_sha256=digest),
        events=events,
        payload_sha256=digest,
    )


def validate_timeline_progression(
    previous: SportradarTimelineSnapshot,
    current: SportradarTimelineSnapshot,
) -> str:
    """Classify an append/correction or reject a missing/reordered event."""

    if (
        type(previous) is not SportradarTimelineSnapshot
        or type(current) is not SportradarTimelineSnapshot
        or previous.score.provider_match_id != current.score.provider_match_id
        or current.score.generated_wall_ns < previous.score.generated_wall_ns
        or len(current.events) < len(previous.events)
    ):
        _fail("sportradar_timeline_gap")
    corrected = False
    for old, new in zip(previous.events, current.events, strict=False):
        if old.event_id != new.event_id:
            _fail("sportradar_timeline_gap")
        if old == new:
            continue
        previous_revision_time = old.updated_wall_ns or old.event_wall_ns
        if (
            not new.updated
            or new.updated_wall_ns is None
            or new.updated_wall_ns <= previous_revision_time
        ):
            _fail("sportradar_timeline_unmarked_correction")
        corrected = True
    advanced = len(current.events) > len(previous.events)
    if advanced:
        if previous.events and (
            current.events[len(previous.events)].event_id
            <= previous.events[-1].event_id
        ):
            _fail("sportradar_timeline_gap")
    if corrected and advanced:
        return "corrected_and_advanced"
    if corrected:
        return "corrected"
    if advanced:
        return "advanced"
    previous_score = (
        previous.score.status,
        previous.score.match_status,
        previous.score.sets_home,
        previous.score.sets_away,
        previous.score.games_home,
        previous.score.games_away,
        previous.score.points_home,
        previous.score.points_away,
        previous.score.serving,
        previous.score.in_tiebreak,
    )
    current_score = (
        current.score.status,
        current.score.match_status,
        current.score.sets_home,
        current.score.sets_away,
        current.score.games_home,
        current.score.games_away,
        current.score.points_home,
        current.score.points_away,
        current.score.serving,
        current.score.in_tiebreak,
    )
    if current_score != previous_score:
        return "score_changed"
    return "unchanged"


def validate_timeline_after_summary(
    summary: SportradarScoreSnapshot,
    timeline: SportradarTimelineSnapshot,
) -> None:
    """Reject a first timeline older than the summary already displayed."""

    if (
        type(summary) is not SportradarScoreSnapshot
        or type(timeline) is not SportradarTimelineSnapshot
        or summary.provider_match_id != timeline.score.provider_match_id
    ):
        _fail("sportradar_match_identity_mismatch")
    if timeline.score.generated_wall_ns < summary.generated_wall_ns:
        _fail("sportradar_timeline_before_summary")


__all__ = (
    "SportradarScoreSnapshot",
    "SportradarLiveSummariesSnapshot",
    "SportradarTimelineEvent",
    "SportradarTimelineSnapshot",
    "SportradarWireContractError",
    "parse_sport_event_summary",
    "parse_sport_event_timeline",
    "parse_live_summaries",
    "parse_live_summaries_envelope",
    "validate_timeline_after_summary",
    "validate_timeline_progression",
)
