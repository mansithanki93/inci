"""Pure candidate parsing for documented public tennis live-score payloads.

The module deliberately does not activate a provider or register a route.  A
public response can describe a useful score state while still lacking the
revision and correction provenance required by the expert snapshot contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from datetime import datetime, timezone
from typing import Final
from xml.etree import ElementTree

from inci_tennis_expert.contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ProviderSnapshot,
    ScoreValue,
    SetScore,
    TerminationKind,
)


PARSER_VERSION: Final[str] = "live-score-candidates-v1"
MAX_PAYLOAD_BYTES: Final[int] = 1_048_576
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SECRET_SUFFIXES: Final[tuple[str, ...]] = (
    "authorization",
    "cookie",
    "apikey",
    "apitoken",
    "accesstoken",
    "refreshtoken",
    "token",
    "secret",
    "password",
    "credential",
    "signature",
)


class ProviderSlot(str, Enum):
    API_TENNIS = "api_tennis"
    GOALSERVE = "goalserve"
    LIVE_TENNIS_API = "live_tennis_api"


class AbstentionReason(str, Enum):
    ACCESS_DENIED = "access_denied"
    UNKNOWN_SCHEMA = "unknown_schema"
    UNKNOWN_STATUS = "unknown_status"
    UNSUPPORTED_FORMAT = "unsupported_format"
    DOUBLES = "doubles"
    BO5 = "bo5"
    NONSTANDARD_FORMAT = "nonstandard_format"
    MISSING_SERVER = "missing_server"
    AMBIGUOUS_CURRENT_SET = "ambiguous_current_set"
    UNSUPPORTED_TIEBREAK = "unsupported_tiebreak"
    IDENTITY_MISMATCH = "identity_mismatch"
    MISSING_PROVIDER_REVISION = "missing_provider_revision"
    MISSING_CORRECTION_SEMANTICS = "missing_correction_semantics"
    MISSING_SOURCE_EVENT_ID = "missing_source_event_id"
    MISSING_SOURCE_GENERATED_TIME = "missing_source_generated_time"


class LiveScoreParseError(ValueError):
    """Stable non-secret-bearing rejection for malformed raw input."""

    _CODES = frozenset(
        {
            "malformed_payload",
            "duplicate_json_key",
            "non_finite_number",
            "payload_too_large",
            "secret_material",
            "impossible_score",
        }
    )

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in self._CODES:
            raise TypeError("exact parse error code required")
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class LiveScoreCaptureContext:
    """Caller-bound identity and local capture facts; never wire provenance."""

    provider_source_id: str
    revision_domain_id: str
    source_lineage_sha256: str
    provider_match_id: str
    home_player_id: str
    away_player_id: str
    scheduled_start_wall_ns: int
    match_format: MatchFormat
    local_capture_wall_ns: int
    local_capture_monotonic_ns: int
    local_clock_uncertainty_ns: int
    raw_capture_id: str
    lineage_independence_proven: bool | None

    def __post_init__(self) -> None:
        for name in (
            "provider_source_id",
            "revision_domain_id",
            "provider_match_id",
            "home_player_id",
            "away_player_id",
            "raw_capture_id",
        ):
            value = getattr(self, name)
            if type(value) is not str or _SAFE_ID.fullmatch(value) is None:
                raise ValueError("invalid_capture_context")
        if (
            type(self.source_lineage_sha256) is not str
            or _SHA256.fullmatch(self.source_lineage_sha256) is None
            or type(self.match_format) is not MatchFormat
        ):
            raise ValueError("invalid_capture_context")
        for name in (
            "scheduled_start_wall_ns",
            "local_capture_wall_ns",
            "local_capture_monotonic_ns",
            "local_clock_uncertainty_ns",
        ):
            if type(getattr(self, name)) is not int:
                raise ValueError("invalid_capture_context")
        if self.lineage_independence_proven not in (None, True, False):
            raise ValueError("invalid_capture_context")


@dataclass(frozen=True, slots=True)
class LiveScoreFacts:
    provider_match_id: str
    home_player_id: str
    away_player_id: str
    status: MatchStatus
    termination_kind: TerminationKind
    winner: PlayerSide | None
    completed_sets: tuple[SetScore, ...]
    games_home: int
    games_away: int
    points_home: ScoreValue
    points_away: ScoreValue
    in_tiebreak: bool
    server_for_next_point: PlayerSide | None
    source_generated_wall_ns: int | None


@dataclass(frozen=True, slots=True)
class NormalizedLiveScore:
    provider_slot: ProviderSlot
    provider_source_id: str
    source_lineage_sha256: str
    raw_capture_id: str
    raw_sha256: str
    parser_version: str
    facts: LiveScoreFacts | None
    snapshot: ProviderSnapshot | None
    abstention: AbstentionReason | None
    diagnostics: tuple[AbstentionReason, ...]
    lineage_independence_proven: bool


@dataclass(frozen=True, slots=True)
class _WireFacts:
    match_id: str
    home_id: str
    away_id: str
    status: MatchStatus
    termination_kind: TerminationKind
    winner: PlayerSide | None
    sets: tuple[tuple[int, int], ...]
    games: tuple[int, int]
    points: tuple[ScoreValue, ScoreValue]
    server: PlayerSide | None
    in_tiebreak: bool
    source_generated_wall_ns: int | None


def _parse_slot(value: object) -> ProviderSlot:
    if type(value) is not str:
        raise ValueError("provider_slot")
    try:
        return ProviderSlot(value)
    except ValueError as error:
        raise ValueError("provider_slot") from error


def _raw(payload: object) -> tuple[bytes, str]:
    if type(payload) is not bytes:
        raise LiveScoreParseError("malformed_payload")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise LiveScoreParseError("payload_too_large")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LiveScoreParseError("malformed_payload") from error
    return payload, sha256(payload).hexdigest()


def _reject_constant(_: str) -> object:
    raise LiveScoreParseError("non_finite_number")


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LiveScoreParseError("duplicate_json_key")
        result[key] = value
    return result


def _json_document(payload: bytes) -> object:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except LiveScoreParseError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise LiveScoreParseError("malformed_payload") from error


def _has_secret(value: object) -> bool:
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                return True
            lowered = key.lower().replace("_", "").replace("-", "")
            if any(lowered.endswith(suffix) for suffix in _SECRET_SUFFIXES):
                return True
            if _has_secret(item):
                return True
    elif type(value) is list:
        return any(_has_secret(item) for item in value)
    return False


def _xml_document(payload: bytes) -> ElementTree.Element:
    try:
        root = ElementTree.fromstring(payload)
    except (ElementTree.ParseError, UnicodeDecodeError) as error:
        raise LiveScoreParseError("malformed_payload") from error
    for node in root.iter():
        tag = node.tag if type(node.tag) is str else ""
        if _has_secret({tag: dict(node.attrib)}):
            raise LiveScoreParseError("secret_material")
    return root


def _text(value: object) -> str:
    if type(value) is not str or not value:
        raise LiveScoreParseError("impossible_score")
    return value


def _number(value: object) -> int:
    if type(value) is bool:
        raise LiveScoreParseError("impossible_score")
    if type(value) is int:
        result = value
    elif type(value) is str and value.isdecimal():
        result = int(value)
    else:
        raise LiveScoreParseError("impossible_score")
    if not 0 <= result <= 99:
        raise LiveScoreParseError("impossible_score")
    return result


def _pair(value: object) -> tuple[int, int]:
    if type(value) not in (list, tuple) or len(value) != 2:
        raise LiveScoreParseError("impossible_score")
    return _number(value[0]), _number(value[1])


def _game_pair(value: object) -> tuple[int, int]:
    if type(value) is not str:
        raise LiveScoreParseError("impossible_score")
    parts = re.split(r"\s*[-:]\s*", value.strip())
    if len(parts) != 2:
        raise LiveScoreParseError("impossible_score")
    return _number(parts[0]), _number(parts[1])


def _points(value: object) -> tuple[ScoreValue, ScoreValue]:
    if type(value) not in (list, tuple) or len(value) != 2:
        raise LiveScoreParseError("impossible_score")
    mapping = {
        "0": ScoreValue.LOVE,
        "love": ScoreValue.LOVE,
        "15": ScoreValue.FIFTEEN,
        "fifteen": ScoreValue.FIFTEEN,
        "30": ScoreValue.THIRTY,
        "thirty": ScoreValue.THIRTY,
        "40": ScoreValue.FORTY,
        "forty": ScoreValue.FORTY,
        "ad": ScoreValue.ADVANTAGE,
        "a": ScoreValue.ADVANTAGE,
        "advantage": ScoreValue.ADVANTAGE,
    }
    parsed: list[ScoreValue] = []
    for item in value:
        if type(item) is int and item in (0, 15, 30, 40):
            item = str(item)
        if type(item) is not str or item.lower() not in mapping:
            raise LiveScoreParseError("impossible_score")
        parsed.append(mapping[item.lower()])
    if (
        parsed[0] is ScoreValue.ADVANTAGE
        and parsed[1] is not ScoreValue.FORTY
        or parsed[1] is ScoreValue.ADVANTAGE
        and parsed[0] is not ScoreValue.FORTY
        or parsed[0] is ScoreValue.ADVANTAGE
        and parsed[1] is ScoreValue.ADVANTAGE
    ):
        raise LiveScoreParseError("impossible_score")
    return parsed[0], parsed[1]


def _side(value: object, *, allow_none: bool = False) -> PlayerSide | None:
    if value is None and allow_none:
        return None
    if type(value) is int:
        value = str(value)
    if type(value) is not str:
        raise LiveScoreParseError("impossible_score")
    normalized = value.lower().strip()
    if normalized in {"1", "p1", "home", "first", "yes"}:
        return PlayerSide.HOME
    if normalized in {"2", "p2", "away", "second", "no"}:
        return PlayerSide.AWAY
    raise LiveScoreParseError("impossible_score")


def _status(value: object) -> tuple[MatchStatus, TerminationKind]:
    if type(value) is not str:
        raise ValueError("unknown_status")
    normalized = value.lower().strip()
    if normalized in {"finished", "ended", "complete", "completed"}:
        return MatchStatus.ENDED, TerminationKind.NATURAL
    if "suspend" in normalized or normalized in {"delay", "interrupted"}:
        return MatchStatus.SUSPENDED, TerminationKind.NONE
    if normalized in {"live", "in progress", "in_play"} or "set" in normalized:
        return MatchStatus.LIVE, TerminationKind.NONE
    raise ValueError("unknown_status")


def _utc_ns(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is int and 0 <= value <= 9_223_372_036_854_775_807:
        return value * (1_000_000_000 if value < 10_000_000_000_000 else 1)
    if type(value) is not str:
        raise LiveScoreParseError("impossible_score")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise LiveScoreParseError("impossible_score") from error
    if parsed.tzinfo is None:
        raise LiveScoreParseError("impossible_score")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _api_tennis(document: object) -> _WireFacts | AbstentionReason:
    if type(document) is not dict:
        return AbstentionReason.UNKNOWN_SCHEMA
    if document.get("success") in (0, False):
        return AbstentionReason.ACCESS_DENIED
    matches = document.get("result")
    if document.get("success") not in (1, True) or type(matches) is not list or len(matches) != 1:
        return AbstentionReason.UNKNOWN_SCHEMA
    match = matches[0]
    if type(match) is not dict:
        return AbstentionReason.UNKNOWN_SCHEMA
    if type(match.get("pointbypoint")) is not list:
        return AbstentionReason.UNKNOWN_SCHEMA
    try:
        status, termination = _status(match.get("event_status"))
    except ValueError:
        return AbstentionReason.UNKNOWN_STATUS
    try:
        raw_sets = match.get("sets")
        if type(raw_sets) is not list or not raw_sets:
            return AbstentionReason.UNKNOWN_SCHEMA
        sets = tuple(_pair(item) for item in raw_sets)
        games = sets[-1]
        point_text = match.get("event_game_result")
        points = _points(_game_pair(point_text)) if point_text is not None else (ScoreValue.LOVE, ScoreValue.LOVE)
        server = _side(match.get("event_serving_player"), allow_none=True)
        winner = _side(match.get("event_winner"), allow_none=True)
        return _WireFacts(
            match_id=_text(match.get("event_key")),
            home_id=_text(match.get("first_player_key")),
            away_id=_text(match.get("second_player_key")),
            status=status,
            termination_kind=termination,
            winner=winner,
            sets=sets,
            games=games if status is not MatchStatus.ENDED else (0, 0),
            points=points if status is not MatchStatus.ENDED else (ScoreValue.LOVE, ScoreValue.LOVE),
            server=server if status is not MatchStatus.ENDED else None,
            in_tiebreak=False,
            source_generated_wall_ns=None,
        )
    except LiveScoreParseError:
        raise


def _goalserve(root: ElementTree.Element) -> _WireFacts | AbstentionReason:
    if root.tag != "scores":
        return AbstentionReason.UNKNOWN_SCHEMA
    error_text = " ".join(root.attrib.values()).lower()
    if "access" in error_text or "denied" in error_text:
        return AbstentionReason.ACCESS_DENIED
    matches = root.findall("./tournament/matches/match")
    if len(matches) != 1:
        return AbstentionReason.UNKNOWN_SCHEMA
    match = matches[0]
    try:
        status, termination = _status(match.attrib.get("status"))
    except ValueError:
        return AbstentionReason.UNKNOWN_STATUS
    players = match.findall("player")
    if len(players) != 2:
        return AbstentionReason.UNKNOWN_SCHEMA
    try:
        rows: list[tuple[int, int]] = []
        for index in range(1, 6):
            home = players[0].attrib.get(f"set{index}")
            away = players[1].attrib.get(f"set{index}")
            if home is None and away is None:
                break
            if home is None or away is None:
                raise LiveScoreParseError("impossible_score")
            rows.append((_number(home), _number(away)))
        if not rows:
            return AbstentionReason.UNKNOWN_SCHEMA
        game_text = match.attrib.get("game_score")
        points = _points(_game_pair(game_text)) if game_text is not None else (ScoreValue.LOVE, ScoreValue.LOVE)
        winner = _side(match.attrib.get("winner"), allow_none=True)
        return _WireFacts(
            match_id=_text(match.attrib.get("id")),
            home_id=_text(players[0].attrib.get("id")),
            away_id=_text(players[1].attrib.get("id")),
            status=status,
            termination_kind=termination,
            winner=winner,
            sets=tuple(rows),
            games=rows[-1] if status is not MatchStatus.ENDED else (0, 0),
            points=points if status is not MatchStatus.ENDED else (ScoreValue.LOVE, ScoreValue.LOVE),
            server=_side(match.attrib.get("serve"), allow_none=True) if status is not MatchStatus.ENDED else None,
            in_tiebreak=False,
            source_generated_wall_ns=None,
        )
    except LiveScoreParseError:
        raise


def _live_tennis_api(document: object) -> _WireFacts | AbstentionReason:
    if type(document) is not dict:
        return AbstentionReason.UNKNOWN_SCHEMA
    message = " ".join(str(document.get(key, "")) for key in ("error", "message", "detail")).lower()
    if "access" in message or "denied" in message:
        return AbstentionReason.ACCESS_DENIED
    if document.get("is_doubles") is True:
        return AbstentionReason.DOUBLES
    format_text = document.get("format")
    if type(format_text) is not str:
        return AbstentionReason.UNKNOWN_SCHEMA
    normalized_format = format_text.lower().replace("-", "_")
    if "5" in normalized_format:
        return AbstentionReason.BO5
    if normalized_format not in {"best_of_3", "bo3", "bestof3"}:
        return AbstentionReason.NONSTANDARD_FORMAT
    try:
        status, termination = _status(document.get("status"))
    except ValueError:
        return AbstentionReason.UNKNOWN_STATUS
    players = document.get("players")
    score = document.get("score")
    if type(players) is not dict or type(score) is not dict:
        return AbstentionReason.UNKNOWN_SCHEMA
    p1, p2 = players.get("p1"), players.get("p2")
    if type(p1) is not dict or type(p2) is not dict:
        return AbstentionReason.UNKNOWN_SCHEMA
    raw_sets = score.get("sets")
    games_value = score.get("games")
    points_value = score.get("points")
    if type(raw_sets) is not list or type(games_value) is not dict or type(points_value) is not dict:
        return AbstentionReason.UNKNOWN_SCHEMA
    try:
        completed_sets = tuple(
            _pair((item.get("p1"), item.get("p2")))
            if type(item) is dict
            else _pair(item)
            for item in raw_sets
        )
        games = _pair((games_value.get("p1"), games_value.get("p2")))
        points = _points((points_value.get("p1"), points_value.get("p2")))
        winner = _side(document.get("winner"), allow_none=True)
        return _WireFacts(
            match_id=_text(document.get("id")),
            home_id=_text(p1.get("id")),
            away_id=_text(p2.get("id")),
            status=status,
            termination_kind=termination,
            winner=winner,
            sets=(
                completed_sets
                if status is MatchStatus.ENDED
                else completed_sets + (games,)
            ),
            games=games if status is not MatchStatus.ENDED else (0, 0),
            points=points if status is not MatchStatus.ENDED else (ScoreValue.LOVE, ScoreValue.LOVE),
            server=_side(score.get("server"), allow_none=True) if status is not MatchStatus.ENDED else None,
            in_tiebreak=score.get("is_tiebreak") is True,
            source_generated_wall_ns=_utc_ns(score.get("timestamp")),
        )
    except LiveScoreParseError:
        raise


def _complete_set(pair: tuple[int, int]) -> bool:
    home, away = pair
    return (home == 6 and 0 <= away <= 4) or (away == 6 and 0 <= home <= 4) or (home, away) in {(7, 5), (5, 7), (7, 6), (6, 7)}


def _facts(
    context: LiveScoreCaptureContext,
    wire: _WireFacts,
) -> tuple[LiveScoreFacts | None, AbstentionReason | None]:
    if (
        wire.match_id != context.provider_match_id
        or wire.home_id != context.home_player_id
        or wire.away_id != context.away_player_id
    ):
        return None, AbstentionReason.IDENTITY_MISMATCH
    if context.match_format is MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS:
        return None, AbstentionReason.BO5
    if context.match_format is not MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS:
        return None, AbstentionReason.UNSUPPORTED_FORMAT
    if wire.in_tiebreak:
        return None, AbstentionReason.UNSUPPORTED_TIEBREAK
    if wire.status is MatchStatus.ENDED:
        if len(wire.sets) != 2 or not all(_complete_set(item) for item in wire.sets) or wire.winner is None:
            return None, AbstentionReason.AMBIGUOUS_CURRENT_SET
        completed = tuple(SetScore(home, away, None, None) for home, away in wire.sets)
        return LiveScoreFacts(
            provider_match_id=context.provider_match_id,
            home_player_id=context.home_player_id,
            away_player_id=context.away_player_id,
            status=wire.status,
            termination_kind=wire.termination_kind,
            winner=wire.winner,
            completed_sets=completed,
            games_home=0,
            games_away=0,
            points_home=ScoreValue.LOVE,
            points_away=ScoreValue.LOVE,
            in_tiebreak=False,
            server_for_next_point=None,
            source_generated_wall_ns=wire.source_generated_wall_ns,
        ), None
    if len(wire.sets) != 2 or not _complete_set(wire.sets[0]) or _complete_set(wire.sets[1]):
        return None, AbstentionReason.AMBIGUOUS_CURRENT_SET
    if wire.games != wire.sets[1] or wire.games[0] > 6 or wire.games[1] > 6 or wire.games == (6, 6):
        raise LiveScoreParseError("impossible_score")
    return LiveScoreFacts(
        provider_match_id=context.provider_match_id,
        home_player_id=context.home_player_id,
        away_player_id=context.away_player_id,
        status=wire.status,
        termination_kind=wire.termination_kind,
        winner=None,
        completed_sets=(SetScore(wire.sets[0][0], wire.sets[0][1], None, None),),
        games_home=wire.games[0],
        games_away=wire.games[1],
        points_home=wire.points[0],
        points_away=wire.points[1],
        in_tiebreak=False,
        server_for_next_point=wire.server,
        source_generated_wall_ns=wire.source_generated_wall_ns,
    ), (
        AbstentionReason.MISSING_SERVER
        if wire.server is None
        else None
    )


def _result(
    slot: ProviderSlot,
    context: LiveScoreCaptureContext,
    raw_sha256: str,
    facts: LiveScoreFacts | None,
    abstention: AbstentionReason | None,
) -> NormalizedLiveScore:
    diagnostics: list[AbstentionReason] = []
    if abstention is not None:
        diagnostics.append(abstention)
    if facts is not None:
        # The three documented public shapes carry no revision/correction event
        # contract, so none can become an expert snapshot by inference.
        diagnostics.extend(
            (
                AbstentionReason.MISSING_PROVIDER_REVISION,
                AbstentionReason.MISSING_CORRECTION_SEMANTICS,
                AbstentionReason.MISSING_SOURCE_EVENT_ID,
            )
        )
        if facts.source_generated_wall_ns is None:
            diagnostics.append(AbstentionReason.MISSING_SOURCE_GENERATED_TIME)
        if abstention is None:
            abstention = AbstentionReason.MISSING_PROVIDER_REVISION
    return NormalizedLiveScore(
        provider_slot=slot,
        provider_source_id=context.provider_source_id,
        source_lineage_sha256=context.source_lineage_sha256,
        raw_capture_id=context.raw_capture_id,
        raw_sha256=raw_sha256,
        parser_version=PARSER_VERSION,
        facts=facts,
        snapshot=None,
        abstention=abstention,
        diagnostics=tuple(diagnostics),
        lineage_independence_proven=context.lineage_independence_proven is True,
    )


def parse_live_score(
    provider_slot: str,
    payload: bytes,
    context: LiveScoreCaptureContext,
) -> NormalizedLiveScore:
    """Normalize one raw provider response without I/O or trust promotion."""
    if type(context) is not LiveScoreCaptureContext:
        raise ValueError("capture_context")
    slot = _parse_slot(provider_slot)
    raw, raw_sha256 = _raw(payload)
    if slot is ProviderSlot.GOALSERVE:
        parsed: _WireFacts | AbstentionReason = _goalserve(_xml_document(raw))
    else:
        document = _json_document(raw)
        if _has_secret(document):
            raise LiveScoreParseError("secret_material")
        parsed = _api_tennis(document) if slot is ProviderSlot.API_TENNIS else _live_tennis_api(document)
    if type(parsed) is AbstentionReason:
        return _result(slot, context, raw_sha256, None, parsed)
    facts, abstention = _facts(context, parsed)
    return _result(slot, context, raw_sha256, facts, abstention)
