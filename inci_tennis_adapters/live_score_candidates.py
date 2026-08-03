"""Pure candidate parsing for documented tennis live-score responses."""

from __future__ import annotations

from dataclasses import dataclass
import calendar
from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
import math
from re import ASCII, compile as pattern_compile, split as pattern_split
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


PARSER_VERSION: Final[str] = "live-score-candidates-v3"
MAX_PAYLOAD_BYTES: Final[int] = 1_048_576
_MAX_TREE_DEPTH: Final[int] = 64
_MAX_TREE_NODES: Final[int] = 20_000
_MAX_TEXT_BYTES: Final[int] = 4_096
_SAFE_ID = pattern_compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", ASCII)
_SHA256 = pattern_compile(r"[0-9a-f]{64}\Z", ASCII)
_DECIMAL_SET = pattern_compile(r"[0-9]+\.[0-9]+\Z", ASCII)
_RFC3339_UTC = pattern_compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"
    r"(?:\.([0-9]{1,9}))?Z\Z",
    ASCII,
)
_SECRET_SUFFIXES: Final[tuple[str, ...]] = (
    "authorization", "cookie", "apikey", "apitoken", "accesstoken",
    "refreshtoken", "token", "secret", "password", "credential",
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
    MATCH_NOT_FOUND = "match_not_found"
    DUPLICATE_MATCH = "duplicate_match"


class LiveScoreParseError(ValueError):
    _CODES = frozenset({
        "malformed_payload", "duplicate_json_key", "non_finite_number",
        "payload_too_large", "secret_material", "impossible_score",
    })

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in self._CODES:
            raise TypeError("exact parse error code required")
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class LiveScoreCaptureContext:
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
            "provider_source_id", "revision_domain_id", "provider_match_id",
            "home_player_id", "away_player_id", "raw_capture_id",
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
            "scheduled_start_wall_ns", "local_capture_wall_ns",
            "local_capture_monotonic_ns", "local_clock_uncertainty_ns",
        ):
            if type(getattr(self, name)) is not int:
                raise ValueError("invalid_capture_context")
        if (
            self.home_player_id == self.away_player_id
            or self.scheduled_start_wall_ns <= 0
            or self.local_capture_wall_ns <= 0
            or self.local_capture_monotonic_ns < 0
            or self.local_clock_uncertainty_ns < 0
        ):
            raise ValueError("invalid_capture_context")
        if (
            self.lineage_independence_proven is not None
            and type(self.lineage_independence_proven) is not bool
        ):
            raise ValueError("invalid_capture_context")


@dataclass(frozen=True, slots=True, repr=False)
class PointByPointPoint:
    number: str
    score: str


@dataclass(frozen=True, slots=True, repr=False)
class PointByPointGame:
    set_number: str
    game_number: str
    server: PlayerSide
    score: str
    points: tuple[PointByPointPoint, ...]


@dataclass(frozen=True, slots=True, repr=False)
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
    point_by_point: tuple[PointByPointGame, ...]


@dataclass(frozen=True, slots=True, repr=False)
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
    lineage_independence_proven: bool | None


@dataclass(frozen=True, slots=True)
class _WireFacts:
    match_id: str
    home_id: str
    away_id: str
    status: MatchStatus
    termination_kind: TerminationKind
    winner: PlayerSide | None
    set_rows: tuple[tuple[int, int], ...]
    declared_set_wins: tuple[int, int] | None
    points: tuple[ScoreValue, ScoreValue] | None
    server: PlayerSide | None
    in_tiebreak: bool
    source_generated_wall_ns: int | None
    point_by_point: tuple[PointByPointGame, ...]


def _parse_slot(value: object) -> ProviderSlot:
    if type(value) is not str:
        raise ValueError("provider_slot")
    try:
        return ProviderSlot(value)
    except ValueError:
        raise ValueError("provider_slot") from None


def _raw(payload: object) -> tuple[bytes, str]:
    if type(payload) is not bytes:
        raise LiveScoreParseError("malformed_payload")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise LiveScoreParseError("payload_too_large")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        raise LiveScoreParseError("malformed_payload") from None
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
        document = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except LiveScoreParseError:
        raise
    except (UnicodeDecodeError, RecursionError, ValueError):
        raise LiveScoreParseError("malformed_payload") from None
    _validate_json_tree(document)
    return document


def _secret_name(value: str) -> bool:
    normalized = "".join(
        character
        for character in value.lower()
        if character not in "_-"
    )
    return any(normalized.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def _validate_json_tree(root: object) -> None:
    stack: list[tuple[object, int]] = [(root, 0)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_TREE_NODES or depth > _MAX_TREE_DEPTH:
            raise LiveScoreParseError("malformed_payload")
        if type(value) is str:
            if len(value.encode("utf-8")) > _MAX_TEXT_BYTES:
                raise LiveScoreParseError("malformed_payload")
        elif type(value) is float and not math.isfinite(value):
            raise LiveScoreParseError("non_finite_number")
        elif type(value) is dict:
            for key, item in value.items():
                if type(key) is not str or _secret_name(key):
                    raise LiveScoreParseError("secret_material")
                if len(key.encode("utf-8")) > _MAX_TEXT_BYTES:
                    raise LiveScoreParseError("malformed_payload")
                stack.append((item, depth + 1))
        elif type(value) is list:
            stack.extend((item, depth + 1) for item in value)


def _xml_document(payload: bytes) -> ElementTree.Element:
    try:
        root = ElementTree.fromstring(payload)
    except (ElementTree.ParseError, UnicodeDecodeError, RecursionError):
        raise LiveScoreParseError("malformed_payload") from None
    stack: list[tuple[ElementTree.Element, int]] = [(root, 0)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        tag = node.tag if type(node.tag) is str else ""
        if nodes > _MAX_TREE_NODES or depth > _MAX_TREE_DEPTH:
            raise LiveScoreParseError("malformed_payload")
        if _secret_name(tag) or len(tag.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise LiveScoreParseError("secret_material")
        for key, value in node.attrib.items():
            if _secret_name(key):
                raise LiveScoreParseError("secret_material")
            if len(key.encode("utf-8")) > _MAX_TEXT_BYTES or len(value.encode("utf-8")) > _MAX_TEXT_BYTES:
                raise LiveScoreParseError("malformed_payload")
        if node.text is not None and len(node.text.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise LiveScoreParseError("malformed_payload")
        stack.extend((child, depth + 1) for child in node)
    return root


def _id_text(value: object) -> str:
    if type(value) is int and value >= 0:
        return str(value)
    if type(value) is str and _SAFE_ID.fullmatch(value) is not None:
        return value
    raise LiveScoreParseError("impossible_score")


def _matches_bound_id(value: object, expected: str) -> bool:
    return (
        type(value) is str and value == expected
    ) or (
        type(value) is int and str(value) == expected
    )


def _integer(value: object, *, maximum: int = 99) -> int:
    if type(value) is int:
        result = value
    elif type(value) is str and value.isdecimal():
        result = int(value)
    else:
        raise LiveScoreParseError("impossible_score")
    if not 0 <= result <= maximum:
        raise LiveScoreParseError("impossible_score")
    return result


def _pair(value: object) -> tuple[int, int]:
    if type(value) not in (list, tuple) or len(value) != 2:
        raise LiveScoreParseError("impossible_score")
    return _integer(value[0]), _integer(value[1])


def _pair_text(value: object) -> tuple[int, int]:
    if type(value) is not str:
        raise LiveScoreParseError("impossible_score")
    parts = pattern_split(r"\s*-\s*", value.strip())
    if len(parts) != 2:
        raise LiveScoreParseError("impossible_score")
    return _integer(parts[0]), _integer(parts[1])


def _point(value: object, *, nullable: bool = False) -> ScoreValue | None:
    if value is None and nullable:
        return None
    if type(value) is int and value in (0, 15, 30, 40):
        value = str(value)
    if type(value) is not str:
        raise LiveScoreParseError("impossible_score")
    mapping = {
        "0": ScoreValue.LOVE, "00": ScoreValue.LOVE, "love": ScoreValue.LOVE,
        "15": ScoreValue.FIFTEEN, "30": ScoreValue.THIRTY,
        "40": ScoreValue.FORTY, "ad": ScoreValue.ADVANTAGE,
    }
    parsed = mapping.get(value.lower())
    if parsed is None:
        raise LiveScoreParseError("impossible_score")
    return parsed


def _points_pair(value: object, *, nullable: bool = False) -> tuple[ScoreValue, ScoreValue] | None:
    if type(value) not in (list, tuple) or len(value) != 2:
        raise LiveScoreParseError("impossible_score")
    home, away = _point(value[0], nullable=nullable), _point(value[1], nullable=nullable)
    if home is None and away is None:
        return None
    if home is None or away is None:
        raise LiveScoreParseError("impossible_score")
    if (home is ScoreValue.ADVANTAGE and away is not ScoreValue.FORTY) or (away is ScoreValue.ADVANTAGE and home is not ScoreValue.FORTY) or (home is ScoreValue.ADVANTAGE and away is ScoreValue.ADVANTAGE):
        raise LiveScoreParseError("impossible_score")
    return home, away


def _side(value: object, *, allow_none: bool = False) -> PlayerSide | None:
    if value is None and allow_none:
        return None
    if type(value) is int:
        value = str(value)
    if type(value) is not str:
        raise LiveScoreParseError("impossible_score")
    normalized = value.lower().strip()
    if normalized in {"1", "p1", "home", "first", "first player"}:
        return PlayerSide.HOME
    if normalized in {"2", "p2", "away", "second", "second player"}:
        return PlayerSide.AWAY
    raise LiveScoreParseError("impossible_score")


def _status(value: object, *, provider: ProviderSlot) -> tuple[MatchStatus, TerminationKind]:
    if type(value) is not str:
        raise ValueError("unknown_status")
    text = value.lower().strip()
    if provider is ProviderSlot.GOALSERVE:
        if text == "fin.":
            return MatchStatus.ENDED, TerminationKind.NATURAL
        if text == "susp.":
            return MatchStatus.SUSPENDED, TerminationKind.NONE
    if provider is ProviderSlot.LIVE_TENNIS_API:
        mapping = {
            "upcoming": (MatchStatus.SCHEDULED, TerminationKind.NONE),
            "live": (MatchStatus.LIVE, TerminationKind.NONE),
            "completed": (MatchStatus.ENDED, TerminationKind.NATURAL),
            "cancelled": (MatchStatus.CANCELLED, TerminationKind.CANCELLATION),
        }
        if text in mapping:
            return mapping[text]
        raise ValueError("unknown_status")
    if text in {"finished", "ended", "complete", "completed"}:
        return MatchStatus.ENDED, TerminationKind.NATURAL
    if "suspend" in text:
        return MatchStatus.SUSPENDED, TerminationKind.NONE
    if "set" in text or text in {"live", "in progress"}:
        return MatchStatus.LIVE, TerminationKind.NONE
    raise ValueError("unknown_status")


def _utc_ns(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not str:
        raise LiveScoreParseError("impossible_score")
    matched = _RFC3339_UTC.fullmatch(value)
    if matched is None:
        raise LiveScoreParseError("impossible_score")
    try:
        parsed = datetime.strptime(matched.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise LiveScoreParseError("impossible_score") from None
    fraction = matched.group(2) or ""
    return (
        calendar.timegm(parsed.utctimetuple())
        * 1_000_000_000
        + int(fraction.ljust(9, "0") or "0")
    )


def _api_point_tape(value: object) -> tuple[PointByPointGame, ...]:
    if type(value) is not list:
        raise LiveScoreParseError("impossible_score")
    games: list[PointByPointGame] = []
    for item in value:
        if type(item) is not dict or type(item.get("points")) is not list:
            raise LiveScoreParseError("impossible_score")
        points: list[PointByPointPoint] = []
        for point in item["points"]:
            if type(point) is not dict:
                raise LiveScoreParseError("impossible_score")
            number, score = point.get("number_point"), point.get("score")
            if type(number) is not str or type(score) is not str:
                raise LiveScoreParseError("impossible_score")
            points.append(PointByPointPoint(number=number, score=score))
        set_number, game_number, score = item.get("set_number"), item.get("number_game"), item.get("score")
        if type(set_number) is not str or type(game_number) is not str or type(score) is not str:
            raise LiveScoreParseError("impossible_score")
        server = _side(item.get("player_served"))
        if server is None:
            raise LiveScoreParseError("impossible_score")
        games.append(PointByPointGame(set_number, game_number, server, score, tuple(points)))
    return tuple(games)


def _api_tennis(
    document: object,
    context: LiveScoreCaptureContext,
) -> _WireFacts | AbstentionReason:
    if type(document) is not dict:
        return AbstentionReason.UNKNOWN_SCHEMA
    if document.get("success") in (0, False):
        return AbstentionReason.ACCESS_DENIED
    matches = document.get("result")
    if document.get("success") not in (1, True) or type(matches) is not list:
        return AbstentionReason.UNKNOWN_SCHEMA
    selected = [
        match
        for match in matches
        if (
            type(match) is dict
            and _matches_bound_id(
                match.get("event_key"),
                context.provider_match_id,
            )
        )
    ]
    if not selected:
        return AbstentionReason.MATCH_NOT_FOUND
    if len(selected) != 1:
        return AbstentionReason.DUPLICATE_MATCH
    match = selected[0]
    kind = match.get("event_type_type")
    if type(kind) is not str:
        return AbstentionReason.UNKNOWN_SCHEMA
    if "doubles" in kind.lower():
        return AbstentionReason.DOUBLES
    if "singles" not in kind.lower():
        return AbstentionReason.NONSTANDARD_FORMAT
    try:
        status, termination = _status(match.get("event_status"), provider=ProviderSlot.API_TENNIS)
    except ValueError:
        return AbstentionReason.UNKNOWN_STATUS
    try:
        raw_scores = match.get("scores")
        if type(raw_scores) is not list or not raw_scores or len(raw_scores) > 3:
            return AbstentionReason.UNKNOWN_SCHEMA
        rows: list[tuple[int, int]] = []
        for index, row in enumerate(raw_scores, 1):
            if type(row) is not dict or _integer(row.get("score_set"), maximum=3) != index:
                return AbstentionReason.UNKNOWN_SCHEMA
            rows.append((_integer(row.get("score_first"), maximum=7), _integer(row.get("score_second"), maximum=7)))
        return _WireFacts(
            match_id=_id_text(match.get("event_key")), home_id=_id_text(match.get("first_player_key")), away_id=_id_text(match.get("second_player_key")),
            status=status, termination_kind=termination, winner=_side(match.get("event_winner"), allow_none=True),
            set_rows=tuple(rows), declared_set_wins=None, points=_points_pair(_pair_text(match.get("event_game_result"))) if status is not MatchStatus.ENDED else None,
            server=_side(match.get("event_serve"), allow_none=True) if status is not MatchStatus.ENDED else None,
            in_tiebreak=False, source_generated_wall_ns=None, point_by_point=_api_point_tape(match.get("pointbypoint")),
        )
    except LiveScoreParseError:
        raise


def _goalserve(
    root: ElementTree.Element,
    context: LiveScoreCaptureContext,
) -> _WireFacts | AbstentionReason:
    if root.tag != "scores":
        return AbstentionReason.UNKNOWN_SCHEMA
    if "access" in " ".join(root.attrib.values()).lower():
        return AbstentionReason.ACCESS_DENIED
    matches = root.findall("./tournament/matches/match")
    selected = [
        match
        for match in matches
        if _matches_bound_id(
            match.attrib.get("id"),
            context.provider_match_id,
        )
    ]
    if not selected:
        return AbstentionReason.MATCH_NOT_FOUND
    if len(selected) != 1:
        return AbstentionReason.DUPLICATE_MATCH
    match = selected[0]
    try:
        status, termination = _status(match.attrib.get("status"), provider=ProviderSlot.GOALSERVE)
    except ValueError:
        return AbstentionReason.UNKNOWN_STATUS
    players = match.findall("player")
    if len(players) != 2:
        return AbstentionReason.UNKNOWN_SCHEMA
    if any("id1" in player.attrib or "id2" in player.attrib for player in players):
        return AbstentionReason.DOUBLES
    try:
        wins = (_integer(players[0].attrib.get("sets_won"), maximum=3), _integer(players[1].attrib.get("sets_won"), maximum=3))
        limit = sum(wins) if status is MatchStatus.ENDED else sum(wins) + 1
        if not 1 <= limit <= 3:
            return AbstentionReason.AMBIGUOUS_CURRENT_SET
        rows: list[tuple[int, int]] = []
        for index in range(1, limit + 1):
            home, away = players[0].attrib.get(f"set{index}"), players[1].attrib.get(f"set{index}")
            if type(home) is not str or type(away) is not str:
                return AbstentionReason.UNKNOWN_SCHEMA
            if _DECIMAL_SET.fullmatch(home) or _DECIMAL_SET.fullmatch(away):
                return AbstentionReason.UNSUPPORTED_TIEBREAK
            rows.append((_integer(home, maximum=7), _integer(away, maximum=7)))
        served = tuple(player.attrib.get("serve") == "True" for player in players)
        server = PlayerSide.HOME if served == (True, False) else PlayerSide.AWAY if served == (False, True) else None
        won = tuple(player.attrib.get("winner") == "True" for player in players)
        winner = PlayerSide.HOME if won == (True, False) else PlayerSide.AWAY if won == (False, True) else None
        return _WireFacts(
            match_id=_id_text(match.attrib.get("id")), home_id=_id_text(players[0].attrib.get("id")), away_id=_id_text(players[1].attrib.get("id")),
            status=status, termination_kind=termination, winner=winner, set_rows=tuple(rows), declared_set_wins=wins,
            points=(_point(players[0].attrib.get("game_score")), _point(players[1].attrib.get("game_score"))) if status is not MatchStatus.ENDED else None,
            server=server if status is not MatchStatus.ENDED else None, in_tiebreak=False, source_generated_wall_ns=None, point_by_point=(),
        )
    except LiveScoreParseError:
        raise


def _live_tennis_api(document: object) -> _WireFacts | AbstentionReason:
    if type(document) is not dict:
        return AbstentionReason.UNKNOWN_SCHEMA
    message = " ".join(str(document.get(name, "")) for name in ("error", "message", "detail")).lower()
    if "access" in message or "denied" in message:
        return AbstentionReason.ACCESS_DENIED
    if document.get("is_doubles") is True:
        return AbstentionReason.DOUBLES
    if document.get("format") == "BO5":
        return AbstentionReason.BO5
    if document.get("format") != "BO3":
        return AbstentionReason.NONSTANDARD_FORMAT
    try:
        status, termination = _status(document.get("status"), provider=ProviderSlot.LIVE_TENNIS_API)
    except ValueError:
        return AbstentionReason.UNKNOWN_STATUS
    players, score = document.get("players"), document.get("score")
    if type(players) is not dict or type(score) is not dict or type(players.get("p1")) is not dict or type(players.get("p2")) is not dict:
        return AbstentionReason.UNKNOWN_SCHEMA
    if score.get("is_tiebreak") is True:
        return AbstentionReason.UNSUPPORTED_TIEBREAK
    try:
        declared = _pair(score.get("sets"))
        raw_games = score.get("games")
        if type(raw_games) is not list or len(raw_games) != 2 or type(raw_games[0]) is not list or type(raw_games[1]) is not list or len(raw_games[0]) != len(raw_games[1]) or not 1 <= len(raw_games[0]) <= 3:
            return AbstentionReason.UNKNOWN_SCHEMA
        rows = tuple((_integer(home, maximum=7), _integer(away, maximum=7)) for home, away in zip(raw_games[0], raw_games[1], strict=True))
        return _WireFacts(
            match_id=_id_text(document.get("id")), home_id=_id_text(players["p1"].get("id")), away_id=_id_text(players["p2"].get("id")),
            status=status, termination_kind=termination, winner=_side(document.get("winner"), allow_none=True), set_rows=rows, declared_set_wins=declared,
            points=_points_pair(score.get("points"), nullable=status is not MatchStatus.LIVE), server=_side(score.get("server"), allow_none=True),
            in_tiebreak=False, source_generated_wall_ns=_utc_ns(score.get("timestamp")), point_by_point=(),
        )
    except LiveScoreParseError:
        raise


def _complete_set(row: tuple[int, int]) -> bool:
    home, away = row
    return (home == 6 and 0 <= away <= 4) or (away == 6 and 0 <= home <= 4) or (home, away) in {(7, 5), (5, 7)}


def _wins(rows: tuple[tuple[int, int], ...]) -> tuple[int, int]:
    home = sum(1 for first, second in rows if first > second)
    return home, len(rows) - home


def _facts(context: LiveScoreCaptureContext, wire: _WireFacts) -> tuple[LiveScoreFacts | None, AbstentionReason | None]:
    if (wire.match_id, wire.home_id, wire.away_id) != (context.provider_match_id, context.home_player_id, context.away_player_id):
        return None, AbstentionReason.IDENTITY_MISMATCH
    if context.match_format is MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS:
        return None, AbstentionReason.BO5
    if context.match_format is not MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS:
        return None, AbstentionReason.UNSUPPORTED_FORMAT
    if wire.in_tiebreak or any(row in {(7, 6), (6, 7)} for row in wire.set_rows):
        return None, AbstentionReason.UNSUPPORTED_TIEBREAK
    if wire.status is MatchStatus.ENDED:
        if len(wire.set_rows) not in (2, 3) or not all(_complete_set(row) for row in wire.set_rows) or wire.winner is None:
            return None, AbstentionReason.AMBIGUOUS_CURRENT_SET
        wins = _wins(wire.set_rows)
        if max(wins) != 2 or (wire.winner is PlayerSide.HOME) != (wins[0] == 2) or (wire.declared_set_wins is not None and wire.declared_set_wins != wins):
            return None, AbstentionReason.AMBIGUOUS_CURRENT_SET
        return LiveScoreFacts(context.provider_match_id, context.home_player_id, context.away_player_id, wire.status, wire.termination_kind, wire.winner, tuple(SetScore(a, b, None, None) for a, b in wire.set_rows), 0, 0, ScoreValue.LOVE, ScoreValue.LOVE, False, None, wire.source_generated_wall_ns, wire.point_by_point), None
    if wire.status not in {MatchStatus.LIVE, MatchStatus.SUSPENDED} or not 1 <= len(wire.set_rows) <= 3:
        return None, AbstentionReason.AMBIGUOUS_CURRENT_SET
    completed, current = wire.set_rows[:-1], wire.set_rows[-1]
    if any(not _complete_set(row) for row in completed) or _complete_set(current) or current[0] > 6 or current[1] > 6 or current == (6, 6):
        return None, AbstentionReason.AMBIGUOUS_CURRENT_SET
    wins = _wins(completed)
    if max(wins) > 1 or (wire.declared_set_wins is not None and wire.declared_set_wins != wins):
        return None, AbstentionReason.AMBIGUOUS_CURRENT_SET
    if wire.points is None:
        return None, AbstentionReason.AMBIGUOUS_CURRENT_SET
    return LiveScoreFacts(context.provider_match_id, context.home_player_id, context.away_player_id, wire.status, wire.termination_kind, None, tuple(SetScore(a, b, None, None) for a, b in completed), current[0], current[1], wire.points[0], wire.points[1], False, wire.server, wire.source_generated_wall_ns, wire.point_by_point), AbstentionReason.MISSING_SERVER if wire.server is None else None


def _result(slot: ProviderSlot, context: LiveScoreCaptureContext, raw_sha256: str, facts: LiveScoreFacts | None, abstention: AbstentionReason | None) -> NormalizedLiveScore:
    diagnostics: list[AbstentionReason] = []
    if abstention is not None:
        diagnostics.append(abstention)
    if facts is not None:
        diagnostics.extend((AbstentionReason.MISSING_PROVIDER_REVISION, AbstentionReason.MISSING_CORRECTION_SEMANTICS, AbstentionReason.MISSING_SOURCE_EVENT_ID))
        if facts.source_generated_wall_ns is None:
            diagnostics.append(AbstentionReason.MISSING_SOURCE_GENERATED_TIME)
        if abstention is None:
            abstention = AbstentionReason.MISSING_PROVIDER_REVISION
    return NormalizedLiveScore(slot, context.provider_source_id, context.source_lineage_sha256, context.raw_capture_id, raw_sha256, PARSER_VERSION, facts, None, abstention, tuple(diagnostics), context.lineage_independence_proven)


def parse_live_score(provider_slot: str, payload: bytes, context: LiveScoreCaptureContext) -> NormalizedLiveScore:
    """Normalize one candidate payload without transport or trust promotion."""
    if type(context) is not LiveScoreCaptureContext:
        raise ValueError("capture_context")
    slot = _parse_slot(provider_slot)
    raw, raw_sha256 = _raw(payload)
    if slot is ProviderSlot.GOALSERVE:
        parsed = _goalserve(_xml_document(raw), context)
    else:
        document = _json_document(raw)
        parsed = (
            _api_tennis(document, context)
            if slot is ProviderSlot.API_TENNIS
            else _live_tennis_api(document)
        )
    if type(parsed) is AbstentionReason:
        return _result(slot, context, raw_sha256, None, parsed)
    facts, abstention = _facts(context, parsed)
    return _result(slot, context, raw_sha256, facts, abstention)
