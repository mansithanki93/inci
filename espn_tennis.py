"""Free ESPN tennis scoreboard client (unofficial site API).

Fetches ATP + WTA scoreboards without credentials. Intended as a research
score feed for entry gating — not an entitled production provider.

Coverage note: lower-tier ITF cards often do not appear here. Unbound Kalshi
ITF markets must fail closed rather than trade on price dips alone.
"""
from __future__ import annotations

import json
import math
import re
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable


# site.api.espn.com is often 403 from datacenter IPs; site.web.api works with a
# normal browser UA (still unauthenticated / free).
ESPN_SCOREBOARD = (
    "https://site.web.api.espn.com/apis/site/v2/sports/tennis/{league}/scoreboard"
)
DEFAULT_LEAGUES = ("atp", "wta")
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class EspnCompetitor:
    athlete_id: str
    display_name: str
    short_name: str
    home_away: str
    sets: tuple[int, ...]
    sets_won: int
    serving: bool
    winner: bool | None


@dataclass(frozen=True)
class EspnMatch:
    competition_id: str
    league: str
    state: str          # pre | in | post
    detail: str
    best_of: int
    competitors: tuple[EspnCompetitor, ...]
    note: str
    # Current-set games inferred from linescores / note when available.
    games: tuple[int, int] | None
    # Provider's score-update time, when the feed supplies one (Unix seconds).
    score_timestamp: float | None = None
    # True only when the provider supplied an explicit, validated score.  This
    # distinguishes a real live 0-0 from a live card with no score payload.
    score_observed: bool = False


def _http_get_json(url: str, timeout_s: float = 10.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.espn.com/tennis/scoreboard",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _strict_game_value(value) -> int | None:
    """Parse one provider game count without truncation or bool coercion."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return (int(value) if math.isfinite(value) and value.is_integer()
                and value >= 0 else None)
    if isinstance(value, str):
        if value != value.strip() or not value:
            return None
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            return None
        if (not parsed.is_finite() or parsed < 0
                or parsed != parsed.to_integral_value()):
            return None
        return int(parsed)
    return None


def _sets_from_linescores(
        linescores) -> tuple[tuple[int, ...], int, bool] | None:
    values = []
    won = 0
    if linescores is None:
        return (), 0, False
    if not isinstance(linescores, (list, tuple)):
        return None
    for row in linescores:
        if not isinstance(row, dict):
            return None
        value = _strict_game_value(row.get("value"))
        if value is None:
            return None
        winner = row.get("winner")
        if winner is not None and type(winner) is not bool:
            return None
        values.append(value)
        if winner is True:
            won += 1
    return tuple(values), won, bool(linescores)


_NOTE_SCORE = re.compile(
    r"(\d+)\s*-\s*(\d+)(?:\s*\([^)]*\))?(?:\s+(\d+)\s*-\s*(\d+))*"
)


def _set_complete(a: int, b: int) -> bool:
    high, low = max(a, b), min(a, b)
    return (high >= 6 and high - low >= 2) or (high == 7 and low == 6)


def _games_from_note_and_lines(note: str, left: EspnCompetitor,
                               right: EspnCompetitor,
                               *, state: str) -> tuple[int, int] | None:
    """Best-effort current-set games from ESPN note / open set linescores."""
    if state != "in":
        return None
    # Prefer only an unfinished paired linescore. A completed final line is a
    # completed set, not the score of the next set while ESPN is between sets.
    if left.sets and right.sets and len(left.sets) == len(right.sets):
        last_i = len(left.sets) - 1
        a, b = int(left.sets[last_i]), int(right.sets[last_i])
        if not _set_complete(a, b):
            return a, b
        return None
    if not note:
        return None
    matches = list(re.finditer(r"(\d+)\s*-\s*(\d+)", note))
    if not matches:
        return None
    a, b = matches[-1].group(1), matches[-1].group(2)
    games = int(a), int(b)
    if _set_complete(*games):
        return None

    def normalized(value: str) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = "".join(
            character for character in text
            if not unicodedata.combining(character))
        return re.sub(r"\s+", " ", re.sub(
            r"[^a-z0-9\s]", " ", text.lower())).strip()

    normalized_note = " " + normalized(note) + " "

    def identity_position(competitor: EspnCompetitor) -> int | None:
        positions = []
        for candidate in (competitor.display_name, competitor.short_name):
            phrase = normalized(candidate)
            if len(phrase.split()) < 2:
                continue
            position = normalized_note.find(" " + phrase + " ")
            if position >= 0:
                positions.append(position)
        return min(positions) if positions else None

    left_position = identity_position(left)
    right_position = identity_position(right)
    if (left_position is None or right_position is None
            or left_position == right_position):
        return None
    # ESPN note score pairs follow the first named competitor, not necessarily
    # the provider's competitors-array order.
    return games if left_position < right_position else (games[1], games[0])


_MAJORS = (
    "australian open", "french open", "roland garros",
    "wimbledon", "us open", "u s open",
)


def _infer_best_of(*, event_name: str, grouping_name: str,
                   competition: dict) -> int:
    """Infer BO5 only from explicit major + men's-singles metadata.

    ESPN's ``format.regulation.periods`` is route-dependent rather than a
    trustworthy match format field, so ordinary cards always default BO3.
    """
    extra = competition.get("type") or {}
    if isinstance(extra, dict):
        extra = " ".join(str(extra.get(key) or "") for key in (
            "name", "displayName", "shortName", "slug"))
    text = " ".join((str(event_name or ""), str(grouping_name or ""),
                     str(extra or ""))).lower().replace("’", "'")
    major = any(name in text for name in _MAJORS)
    qualifying = bool(re.search(r"\bqualif", text))
    mens_singles = bool(re.search(r"\bmen(?:'s|s)?\s+singles\b", text))
    return 5 if major and mens_singles and not qualifying else 3


def parse_provider_timestamp(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        if parsed > 10_000_000_000:
            parsed /= 1000.0
        return parsed if math.isfinite(parsed) and parsed >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            timestamp = parsed.timestamp()
            return timestamp if math.isfinite(timestamp) else None
        except ValueError:
            return None
    if parsed > 10_000_000_000:
        parsed /= 1000.0
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _score_timestamp(competition: dict, fallback=None) -> float | None:
    status = competition.get("status") or {}
    candidates = (
        competition.get("timestamp"), competition.get("lastUpdated"),
        competition.get("last_updated"), competition.get("updated_at"),
        status.get("timestamp") if isinstance(status, dict) else None,
        status.get("lastUpdated") if isinstance(status, dict) else None,
        fallback,
    )
    for candidate in candidates:
        parsed = parse_provider_timestamp(candidate)
        if parsed is not None:
            return parsed
    return None


def parse_competition(
        competition: dict,
        league: str,
        *,
        event_name: str = "",
        grouping_name: str = "",
        provider_timestamp=None,
) -> EspnMatch | None:
    if not isinstance(competition, dict):
        return None
    raw_competition_id = competition.get("id")
    if (isinstance(raw_competition_id, bool)
            or raw_competition_id is None):
        return None
    competition_id = str(raw_competition_id)
    if not competition_id or competition_id != competition_id.strip():
        return None
    status = (competition.get("status") or {}).get("type") or {}
    state = str(status.get("state") or "")
    if state not in ("pre", "in", "post"):
        return None
    comps_raw = competition.get("competitors") or []
    if len(comps_raw) != 2:
        return None
    parsed = []
    for row in comps_raw:
        athlete = row.get("athlete") or {}
        display = (athlete.get("displayName") or athlete.get("fullName")
                   or athlete.get("shortName") or "")
        if not display:
            return None
        parsed_lines = _sets_from_linescores(row.get("linescores"))
        if parsed_lines is None:
            return None
        sets, sets_won, _observed = parsed_lines
        # ESPN sometimes only marks set winners; also count by comparing pairs.
        parsed.append(EspnCompetitor(
            athlete_id=str(row.get("id") or athlete.get("id") or ""),
            display_name=str(display),
            short_name=str(athlete.get("shortName") or display),
            home_away=str(row.get("homeAway") or ""),
            sets=sets,
            sets_won=int(sets_won),
            serving=bool(row.get("possession")),
            winner=(True if row.get("winner") is True
                    else False if row.get("winner") is False else None),
        ))
    left, right = parsed[0], parsed[1]
    if len(left.sets) != len(right.sets):
        return None
    # Recompute sets_won from paired linescores when winner flags are sparse.
    if left.sets and right.sets and len(left.sets) == len(right.sets):
        lw = rw = 0
        for i, (a, b) in enumerate(zip(left.sets, right.sets)):
            # Skip an unfinished current set, including 6-5 and 6-6.
            if state == "in" and i == len(left.sets) - 1 \
                    and not _set_complete(a, b):
                continue
            if _set_complete(a, b) and a > b:
                lw += 1
            elif _set_complete(a, b) and b > a:
                rw += 1
        left = EspnCompetitor(**{**left.__dict__, "sets_won": lw})
        right = EspnCompetitor(**{**right.__dict__, "sets_won": rw})
    notes = competition.get("notes") or []
    note = ""
    for item in notes:
        if isinstance(item, dict) and item.get("text"):
            note = str(item["text"])
            break
    best_of = _infer_best_of(
        event_name=event_name, grouping_name=grouping_name,
        competition=competition)
    games = _games_from_note_and_lines(note, left, right, state=state)
    score_observed = bool(left.sets) or games is not None
    if state == "in" and not score_observed:
        return None
    return EspnMatch(
        competition_id=competition_id,
        league=league,
        state=state,
        detail=str(status.get("detail") or status.get("shortDetail") or ""),
        best_of=best_of,
        competitors=(left, right),
        note=note,
        games=games,
        score_timestamp=_score_timestamp(competition, provider_timestamp),
        score_observed=score_observed,
    )


def _scorecard_identity(match: EspnMatch) -> tuple:
    return (
        int(match.best_of),
        tuple((str(competitor.athlete_id or ""),
               str(competitor.display_name), str(competitor.short_name))
              for competitor in match.competitors),
    )


def _scorecard_state(match: EspnMatch) -> tuple:
    return (
        match.state, match.games, bool(match.score_observed),
        tuple((competitor.sets, int(competitor.sets_won),
               bool(competitor.serving), competitor.winner)
              for competitor in match.competitors),
    )


def reconcile_scorecard(existing: EspnMatch, incoming: EspnMatch) -> EspnMatch:
    """Reconcile one duplicate provider card without route-order lookahead."""
    if _scorecard_identity(existing) != _scorecard_identity(incoming):
        raise ValueError(
            "duplicate competition identity has conflicting competitors/format")
    old_time = existing.score_timestamp
    new_time = incoming.score_timestamp
    if old_time is not None and new_time is not None:
        if new_time > old_time:
            return incoming
        if new_time < old_time:
            return existing
    elif old_time is None and new_time is not None:
        if _scorecard_state(existing) != _scorecard_state(incoming):
            raise ValueError(
                "duplicate competition score conflict lacks comparable timestamps")
        return incoming
    elif old_time is not None and new_time is None:
        if _scorecard_state(existing) != _scorecard_state(incoming):
            raise ValueError(
                "duplicate competition score conflict lacks comparable timestamps")
        return existing
    if _scorecard_state(existing) != _scorecard_state(incoming):
        raise ValueError(
            "duplicate competition has conflicting score at the same timestamp")
    return existing


def fetch_scoreboard(league: str, *, get_json: Callable = _http_get_json,
                     dates: str | None = None) -> tuple[EspnMatch, ...]:
    url = ESPN_SCOREBOARD.format(league=league)
    if dates:
        url = f"{url}?dates={dates}"
    payload = get_json(url)
    out = []
    if not isinstance(payload, dict):
        raise ValueError("ESPN scoreboard payload must be an object")
    payload_timestamp = (payload.get("timestamp") or payload.get("lastUpdated")
                         or payload.get("last_updated"))
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_name = str(event.get("name") or event.get("displayName") or "")
        event_timestamp = (event.get("timestamp") or event.get("lastUpdated")
                           or payload_timestamp)
        for grouping in event.get("groupings") or []:
            if not isinstance(grouping, dict):
                continue
            grouping_meta = grouping.get("grouping")
            if not isinstance(grouping_meta, dict):
                grouping_meta = {}
            grouping_name = str(
                grouping.get("displayName") or grouping.get("name")
                or grouping_meta.get("displayName")
                or grouping_meta.get("name") or "")
            for competition in grouping.get("competitions") or []:
                match = parse_competition(
                    competition, league, event_name=event_name,
                    grouping_name=grouping_name,
                    provider_timestamp=event_timestamp)
                if match is not None:
                    out.append(match)
    return tuple(out)


def fetch_live_matches(leagues=DEFAULT_LEAGUES, *,
                       get_json: Callable = _http_get_json) -> tuple[EspnMatch, ...]:
    matches: list[EspnMatch] = []
    index_by_id: dict[str, int] = {}
    failures: list[Exception] = []
    successful_routes = 0
    for league in leagues:
        try:
            route_matches = fetch_scoreboard(league, get_json=get_json)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                ValueError, KeyError) as error:
            failures.append(error)
            continue
        successful_routes += 1
        for match in route_matches:
            identity = str(match.competition_id or "")
            if identity:
                existing_index = index_by_id.get(identity)
                if existing_index is not None:
                    matches[existing_index] = reconcile_scorecard(
                        matches[existing_index], match)
                    continue
                index_by_id[identity] = len(matches)
            matches.append(match)
    if not successful_routes and failures:
        raise RuntimeError(
            f"all ESPN scoreboard routes failed ({failures[-1]})") \
            from failures[-1]
    return tuple(matches)


class EspnScoreboardCache:
    """Poll ESPN on a cadence; return the last successful snapshot."""

    def __init__(self, leagues=DEFAULT_LEAGUES, ttl_s: float = 15.0,
                 max_stale_s: float | None = None,
                 clock=time.time, fetch=fetch_live_matches):
        self.leagues = tuple(leagues)
        self.ttl_s = float(ttl_s)
        self.max_stale_s = float(
            max_stale_s if max_stale_s is not None else max(45.0, ttl_s * 3))
        self.clock = clock
        self.fetch = fetch
        self._fetched_at = 0.0
        self._matches: tuple[EspnMatch, ...] = ()
        self._has_snapshot = False
        self._error: str | None = None

    def matches(self, *, force: bool = False) -> tuple[EspnMatch, ...]:
        now = self.clock()
        if (not force and self._has_snapshot
                and now - self._fetched_at < self.ttl_s):
            return self._matches
        try:
            self._matches = self.fetch(self.leagues)
            self._fetched_at = now
            self._has_snapshot = True
            self._error = None
        except Exception as error:  # noqa: BLE001 — cache keeps last good
            self._error = str(error)
            age = now - self._fetched_at
            if not self._has_snapshot:
                raise
            if age > self.max_stale_s:
                raise RuntimeError(
                    f"ESPN scoreboard snapshot stale ({age:.1f}s > "
                    f"{self.max_stale_s:.1f}s): {error}") from error
        return self._matches

    @property
    def last_error(self):
        return self._error
