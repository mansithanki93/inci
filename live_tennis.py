"""Live Tennis API client for ITF (and optional Challenger) scoreboards.

Maps free-tier live/upcoming match payloads onto the same ``EspnMatch`` shape
used by ``EspnProbGate``, so ITF Kalshi markets can bind when ESPN has no card.

Auth: ``Authorization: Bearer <key>`` or ``X-API-Key``. Resolve the key from
``Config.live_tennis_api_key`` or env ``LIVETENNISAPI_KEY`` /
``LIVETENNIS_API_KEY``.

The gate only polls this feed when an
ITF-marked ticker fails ESPN bind; keep ``live_tennis_cache_s`` high enough for
your account's quota (default 120s).
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from espn_tennis import (
    EspnCompetitor,
    EspnMatch,
    _strict_game_value,
    parse_provider_timestamp,
    reconcile_scorecard,
)

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


def _sets_from_score(score: dict | None, side: int) -> tuple[int, ...] | None:
    """Per-set game counts for player side (1 or 2)."""
    if not isinstance(score, dict):
        return ()
    games = score.get("games")
    if games is None:
        return ()
    if not isinstance(games, (list, tuple)) or len(games) != 2:
        return None
    row = games[side - 1]
    if not isinstance(row, (list, tuple)):
        return None
    out = []
    for value in row:
        parsed = _strict_game_value(value)
        if parsed is None:
            return None
        out.append(parsed)
    return tuple(out)


def _sets_won_from_score(score: dict | None, side: int) -> int | None:
    if not isinstance(score, dict):
        return 0
    sets = score.get("sets")
    if sets is None:
        return 0
    if not isinstance(sets, (list, tuple)) or len(sets) != 2:
        return None
    value = sets[side - 1]
    return _strict_game_value(value)


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
    raw_id = row.get("id")
    if (raw_id is None or isinstance(raw_id, bool)
            or not str(raw_id).strip()):
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
        return None
    if state == "in" and score is None:
        return None
    sets1 = _sets_from_score(score, 1)
    sets2 = _sets_from_score(score, 2)
    if sets1 is None or sets2 is None or len(sets1) != len(sets2):
        return None
    best_of = _best_of(row.get("format"))
    sets_won1 = _sets_won_from_score(score, 1)
    sets_won2 = _sets_won_from_score(score, 2)
    sets_needed = best_of // 2 + 1
    if (sets_won1 is None or sets_won2 is None
            or sets_won1 < 0 or sets_won2 < 0
            or sets_won1 > sets_needed or sets_won2 > sets_needed
            or sets_won1 + sets_won2 > best_of
            or (sets_won1 == sets_needed and sets_won2 == sets_needed)
            or (state == "pre" and (sets_won1 or sets_won2))
            or (state == "in" and max(sets_won1, sets_won2) >= sets_needed)):
        return None
    score_observed = bool(
        isinstance(score, dict)
        and ("sets" in score or bool(sets1) or bool(sets2)))
    if state == "in" and not score_observed:
        return None
    # If games arrays are empty but sets-won are present (a sparse live card),
    # retain the authoritative set score for the gate to consume.
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
        sets_won=sets_won1,
        serving=(server == 1),
        winner=(True if winner == 1 else False if winner == 2 else None),
    )
    right = EspnCompetitor(
        athlete_id=str((p2 or {}).get("id") or ""),
        display_name=name2,
        short_name=name2,
        home_away="away",
        sets=sets2,
        sets_won=sets_won2,
        serving=(server == 2),
        winner=(True if winner == 2 else False if winner == 1 else None),
    )
    return EspnMatch(
        competition_id=f"lt:{raw_id}",
        league=tour,
        state=state,
        detail=" | ".join(detail_parts),
        best_of=best_of,
        competitors=(left, right),
        note=str(row.get("tournament") or ""),
        games=games,
        score_timestamp=next((parsed for parsed in (
            parse_provider_timestamp(row.get("timestamp")),
            parse_provider_timestamp(row.get("updated_at")),
            parse_provider_timestamp(row.get("last_updated")),
            parse_provider_timestamp(
                score.get("timestamp") if isinstance(score, dict) else None),
        ) if parsed is not None), None),
        score_observed=score_observed,
    )


def fetch_matches(
        *,
        api_key: str,
        tours=DEFAULT_TOURS,
        statuses: tuple[str, ...] = ("live",),
        get_json: Callable | None = None,
        limit: int = 100,
        max_pages: int = 3,
) -> tuple[EspnMatch, ...]:
    """Fetch bounded pages for each tour × status."""
    getter = get_json
    if getter is None:
        def getter(url, **_kwargs):
            return _http_get_json(url, api_key=api_key)

    out: list[EspnMatch] = []
    index_by_id: dict[str, int] = {}
    try:
        page_limit = max(1, min(int(limit), 200))
        page_cap = max(1, min(int(max_pages), 10))
    except (TypeError, ValueError) as error:
        raise ValueError("limit and max_pages must be integers") from error
    successful_requests = 0
    failures: list[Exception] = []
    for tour in tours:
        for status in statuses:
            offset = 0
            for _page in range(page_cap):
                params = urllib.parse.urlencode({
                    "status": status,
                    "tour": tour,
                    "limit": page_limit,
                    "offset": offset,
                })
                url = f"{BASE_URL}/matches?{params}"
                try:
                    payload = getter(url, api_key=api_key)
                except (urllib.error.URLError, TimeoutError,
                        json.JSONDecodeError, ValueError, KeyError) as error:
                    failures.append(error)
                    break
                successful_requests += 1
                rows = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(rows, list):
                    failures.append(ValueError(
                        "Live Tennis response data must be a list"))
                    break
                for row in rows:
                    match = parse_match(row)
                    if match is None:
                        continue
                    existing_index = index_by_id.get(match.competition_id)
                    if existing_index is not None:
                        out[existing_index] = reconcile_scorecard(
                            out[existing_index], match)
                        continue
                    index_by_id[match.competition_id] = len(out)
                    out.append(match)
                meta = payload.get("meta") if isinstance(payload, dict) else {}
                if not isinstance(meta, dict):
                    meta = {}
                if meta.get("has_more") is not True or not rows:
                    break
                offset += page_limit
    if not successful_requests and failures:
        raise RuntimeError(
            f"all Live Tennis requests failed ({failures[-1]})") \
            from failures[-1]
    return tuple(out)


class LiveTennisCache:
    """TTL cache for Live Tennis ITF/challenger scoreboards."""

    def __init__(
            self,
            *,
            api_key: str,
            tours=DEFAULT_TOURS,
            ttl_s: float = 120.0,
            max_stale_s: float | None = None,
            include_upcoming: bool = False,
            clock=time.time,
            fetch=None,
    ):
        self.api_key = str(api_key or "").strip()
        self.tours = tuple(tours)
        self.ttl_s = float(ttl_s)
        self.max_stale_s = float(
            max_stale_s if max_stale_s is not None else max(360.0, ttl_s * 3))
        self.include_upcoming = bool(include_upcoming)
        self.clock = clock
        self.fetch = fetch or fetch_matches
        self._fetched_at = 0.0
        self._matches: tuple[EspnMatch, ...] = ()
        self._has_snapshot = False
        self._error: str | None = None

    def matches(self, *, force: bool = False) -> tuple[EspnMatch, ...]:
        if not self.api_key:
            raise ValueError("live tennis API key is missing")
        now = self.clock()
        if (not force and self._has_snapshot
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
            self._has_snapshot = True
            self._error = None
        except Exception as error:  # noqa: BLE001 — keep last good
            self._error = str(error)
            age = now - self._fetched_at
            if not self._has_snapshot:
                raise
            if age > self.max_stale_s:
                raise RuntimeError(
                    f"Live Tennis snapshot stale ({age:.1f}s > "
                    f"{self.max_stale_s:.1f}s): {error}") from error
        return self._matches

    @property
    def last_error(self):
        return self._error
