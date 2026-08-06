"""Live Tennis API client for ITF (and optional Challenger) scoreboards.

Maps free-tier live/upcoming match payloads onto the same ``EspnMatch`` shape
used by ``EspnProbGate``, so ITF Kalshi markets can bind when ESPN has no card.

Auth: ``Authorization: Bearer <key>`` or ``X-API-Key``. Resolve the key from
``Config.live_tennis_api_key`` or env ``LIVETENNISAPI_KEY`` /
``LIVETENNIS_API_KEY``.

Free tier is ~30 req/min and ~100/day. The gate only polls this feed when an
ITF-marked ticker fails ESPN bind; keep ``live_tennis_cache_s`` high enough for
your quota (default 120s).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from espn_tennis import EspnCompetitor, EspnMatch

BASE_URL = "https://api.livetennisapi.com/api/public/v1"
DEFAULT_TOURS = ("itf",)
_STATUS_MAP = {
    "upcoming": "pre",
    "live": "in",
    "completed": "post",
}


def resolve_api_key(config=None) -> str:
    if config is not None:
        configured = str(getattr(config, "live_tennis_api_key", "") or "").strip()
        if configured:
            return configured
    return (
        os.environ.get("LIVETENNISAPI_KEY")
        or os.environ.get("LIVETENNIS_API_KEY")
        or ""
    ).strip()


def _http_get_json(url: str, *, api_key: str, timeout_s: float = 10.0) -> dict:
    if not api_key:
        raise ValueError("live tennis API key is required")
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-API-Key": api_key,
            "User-Agent": "inci-research/live-tennis",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _player_name(player) -> str:
    if not isinstance(player, dict):
        return ""
    return str(player.get("name") or "").strip()


def _best_of(fmt) -> int:
    text = str(fmt or "").upper()
    if text == "BO5":
        return 5
    return 3


def _sets_from_score(score: dict | None, side: int) -> tuple[int, ...]:
    """Per-set game counts for player side (1 or 2)."""
    if not isinstance(score, dict):
        return ()
    games = score.get("games")
    if not isinstance(games, list) or len(games) < side:
        return ()
    row = games[side - 1]
    if not isinstance(row, list):
        return ()
    out = []
    for value in row:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            return ()
    return tuple(out)


def _sets_won_from_score(score: dict | None, side: int) -> int:
    if not isinstance(score, dict):
        return 0
    sets = score.get("sets")
    if not isinstance(sets, list) or len(sets) < side:
        return 0
    try:
        return int(sets[side - 1])
    except (TypeError, ValueError):
        return 0


def parse_match(row: dict) -> EspnMatch | None:
    """Convert one Live Tennis match object into ``EspnMatch``."""
    if not isinstance(row, dict):
        return None
    if row.get("is_doubles") is True:
        return None
    status = str(row.get("status") or "")
    state = _STATUS_MAP.get(status)
    if state is None:
        return None
    players = row.get("players") or {}
    if not isinstance(players, dict):
        return None
    p1 = players.get("p1") or {}
    p2 = players.get("p2") or {}
    name1 = _player_name(p1)
    name2 = _player_name(p2)
    if not name1 or not name2:
        return None
    score = row.get("score")
    if score is not None and not isinstance(score, dict):
        score = None
    sets1 = _sets_from_score(score, 1)
    sets2 = _sets_from_score(score, 2)
    # If games arrays are empty but sets-won are present (pre/live sparse),
    # leave per-set tuples empty; gate treats that as 0-0 current.
    server = (score or {}).get("server") if score else None
    winner = row.get("winner")
    tour = str(row.get("tour") or "itf")
    detail_parts = []
    if row.get("tournament"):
        detail_parts.append(str(row["tournament"]))
    if row.get("round"):
        detail_parts.append(str(row["round"]))
    detail_parts.append(status)
    games = None
    if sets1 and sets2 and len(sets1) == len(sets2):
        games = (int(sets1[-1]), int(sets2[-1]))
    left = EspnCompetitor(
        athlete_id=str((p1 or {}).get("id") or ""),
        display_name=name1,
        short_name=name1,
        home_away="home",
        sets=sets1,
        sets_won=_sets_won_from_score(score, 1),
        serving=(server == 1),
        winner=(True if winner == 1 else False if winner == 2 else None),
    )
    right = EspnCompetitor(
        athlete_id=str((p2 or {}).get("id") or ""),
        display_name=name2,
        short_name=name2,
        home_away="away",
        sets=sets2,
        sets_won=_sets_won_from_score(score, 2),
        serving=(server == 2),
        winner=(True if winner == 2 else False if winner == 1 else None),
    )
    return EspnMatch(
        competition_id=f"lt:{row.get('id')}",
        league=tour,
        state=state,
        detail=" | ".join(detail_parts),
        best_of=_best_of(row.get("format")),
        competitors=(left, right),
        note=str(row.get("tournament") or ""),
        games=games,
    )


def fetch_matches(
        *,
        api_key: str,
        tours=DEFAULT_TOURS,
        statuses: tuple[str, ...] = ("live",),
        get_json: Callable | None = None,
        limit: int = 100,
) -> tuple[EspnMatch, ...]:
    """Fetch and parse matches for each tour × status (free-tier safe)."""
    getter = get_json
    if getter is None:
        def getter(url, **_kwargs):
            return _http_get_json(url, api_key=api_key)

    out: list[EspnMatch] = []
    seen: set[str] = set()
    for tour in tours:
        for status in statuses:
            params = urllib.parse.urlencode({
                "status": status,
                "tour": tour,
                "limit": int(limit),
            })
            url = f"{BASE_URL}/matches?{params}"
            try:
                payload = getter(url, api_key=api_key)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                    ValueError, KeyError):
                continue
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                match = parse_match(row)
                if match is None or match.competition_id in seen:
                    continue
                seen.add(match.competition_id)
                out.append(match)
    return tuple(out)


class LiveTennisCache:
    """TTL cache for Live Tennis ITF/challenger scoreboards."""

    def __init__(
            self,
            *,
            api_key: str,
            tours=DEFAULT_TOURS,
            ttl_s: float = 120.0,
            include_upcoming: bool = False,
            clock=time.time,
            fetch=None,
    ):
        self.api_key = str(api_key or "").strip()
        self.tours = tuple(tours)
        self.ttl_s = float(ttl_s)
        self.include_upcoming = bool(include_upcoming)
        self.clock = clock
        self.fetch = fetch or fetch_matches
        self._fetched_at = 0.0
        self._matches: tuple[EspnMatch, ...] = ()
        self._error: str | None = None

    def matches(self, *, force: bool = False) -> tuple[EspnMatch, ...]:
        if not self.api_key:
            raise ValueError("live tennis API key is missing")
        now = self.clock()
        if (not force and self._matches
                and now - self._fetched_at < self.ttl_s):
            return self._matches
        statuses = ("live", "upcoming") if self.include_upcoming else ("live",)
        try:
            self._matches = self.fetch(
                api_key=self.api_key,
                tours=self.tours,
                statuses=statuses,
            )
            self._fetched_at = now
            self._error = None
        except Exception as error:  # noqa: BLE001 — keep last good
            self._error = str(error)
            if not self._matches:
                raise
        return self._matches

    @property
    def last_error(self):
        return self._error
