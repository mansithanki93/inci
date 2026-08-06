"""Free ESPN tennis scoreboard client (unofficial site API).

Fetches ATP + WTA scoreboards without credentials. Intended as a research
score feed for entry gating — not an entitled production provider.

Coverage note: lower-tier ITF cards often do not appear here. Unbound Kalshi
ITF markets must fail closed rather than trade on price dips alone.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable


ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/tennis/{league}/scoreboard"
)
DEFAULT_LEAGUES = ("atp", "wta")
_USER_AGENT = "inci-paper-research/espn-scoreboard"


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


def _http_get_json(url: str, timeout_s: float = 10.0) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _sets_from_linescores(linescores) -> tuple[tuple[int, ...], int]:
    values = []
    won = 0
    for row in linescores or []:
        try:
            value = int(float(row.get("value")))
        except (TypeError, ValueError):
            continue
        values.append(value)
        if row.get("winner") is True:
            won += 1
    return tuple(values), won


_NOTE_SCORE = re.compile(
    r"(\d+)\s*-\s*(\d+)(?:\s*\([^)]*\))?(?:\s+(\d+)\s*-\s*(\d+))*"
)


def _games_from_note_and_lines(note: str, left: EspnCompetitor,
                               right: EspnCompetitor) -> tuple[int, int] | None:
    """Best-effort current-set games from ESPN note / open set linescores."""
    # Prefer unfinished set linescores (no winner flag on that set).
    if left.sets and right.sets and len(left.sets) == len(right.sets):
        last_i = len(left.sets) - 1
        # If match still in progress, last set may be current.
        return int(left.sets[last_i]), int(right.sets[last_i])
    if not note:
        return None
    matches = list(re.finditer(r"(\d+)\s*-\s*(\d+)", note))
    if not matches:
        return None
    a, b = matches[-1].group(1), matches[-1].group(2)
    return int(a), int(b)


def parse_competition(competition: dict, league: str) -> EspnMatch | None:
    if not isinstance(competition, dict):
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
        sets, sets_won = _sets_from_linescores(row.get("linescores"))
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
    # Recompute sets_won from paired linescores when winner flags are sparse.
    if left.sets and right.sets and len(left.sets) == len(right.sets):
        lw = rw = 0
        for i, (a, b) in enumerate(zip(left.sets, right.sets)):
            # Skip likely-current set while match is live if neither won flag.
            if state == "in" and i == len(left.sets) - 1 and a < 6 and b < 6:
                continue
            if a > b:
                lw += 1
            elif b > a:
                rw += 1
        left = EspnCompetitor(**{**left.__dict__, "sets_won": lw})
        right = EspnCompetitor(**{**right.__dict__, "sets_won": rw})
    notes = competition.get("notes") or []
    note = ""
    for item in notes:
        if isinstance(item, dict) and item.get("text"):
            note = str(item["text"])
            break
    periods = (((competition.get("format") or {}).get("regulation") or {})
               .get("periods"))
    try:
        best_of = int(periods) if periods else 3
    except (TypeError, ValueError):
        best_of = 3
    if best_of not in (3, 5):
        best_of = 3
    games = _games_from_note_and_lines(note, left, right)
    return EspnMatch(
        competition_id=str(competition.get("id") or ""),
        league=league,
        state=state,
        detail=str(status.get("detail") or status.get("shortDetail") or ""),
        best_of=best_of,
        competitors=(left, right),
        note=note,
        games=games,
    )


def fetch_scoreboard(league: str, *, get_json: Callable = _http_get_json,
                     dates: str | None = None) -> tuple[EspnMatch, ...]:
    url = ESPN_SCOREBOARD.format(league=league)
    if dates:
        url = f"{url}?dates={dates}"
    payload = get_json(url)
    out = []
    for event in payload.get("events") or []:
        for grouping in event.get("groupings") or []:
            for competition in grouping.get("competitions") or []:
                match = parse_competition(competition, league)
                if match is not None:
                    out.append(match)
    return tuple(out)


def fetch_live_matches(leagues=DEFAULT_LEAGUES, *,
                       get_json: Callable = _http_get_json) -> tuple[EspnMatch, ...]:
    matches = []
    for league in leagues:
        try:
            matches.extend(fetch_scoreboard(league, get_json=get_json))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                ValueError, KeyError):
            continue
    return tuple(matches)


class EspnScoreboardCache:
    """Poll ESPN on a cadence; return the last successful snapshot."""

    def __init__(self, leagues=DEFAULT_LEAGUES, ttl_s: float = 15.0,
                 clock=time.time, fetch=fetch_live_matches):
        self.leagues = tuple(leagues)
        self.ttl_s = float(ttl_s)
        self.clock = clock
        self.fetch = fetch
        self._fetched_at = 0.0
        self._matches: tuple[EspnMatch, ...] = ()
        self._error: str | None = None

    def matches(self, *, force: bool = False) -> tuple[EspnMatch, ...]:
        now = self.clock()
        if (not force and self._matches
                and now - self._fetched_at < self.ttl_s):
            return self._matches
        try:
            self._matches = self.fetch(self.leagues)
            self._fetched_at = now
            self._error = None
        except Exception as error:  # noqa: BLE001 — cache keeps last good
            self._error = str(error)
            if not self._matches:
                raise
        return self._matches

    @property
    def last_error(self):
        return self._error
