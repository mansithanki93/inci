"""Pure normalization of the public ESPN tennis scoreboard document.

The ESPN scoreboard is a free, unauthenticated, undocumented public feed. It
reports completed games per set, tiebreak points, match status, and which
player holds serve, but it never reports the point score inside the game in
progress. Every snapshot produced here therefore declares ``love-love`` for
the current game: the normalized state is the state at the start of the
current game, which is exactly the resolution the source supports.

Nothing in this module performs IO. Callers supply the fetched document and
the paired observation clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from re import ASCII as RE_ASCII, Pattern, compile as pattern_compile
from typing import Final

from inci_tennis_expert.contracts import (
    ExpertContractError,
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ProviderSnapshot,
    ScoreValue,
    SetScore,
    TerminationKind,
    compute_expert_provider_source_lineage_sha256,
)


ESPN_PROVIDER_ID: Final[str] = "espn"
ESPN_PRODUCT_TIER: Final[str] = "public-scoreboard"
ESPN_SOURCE_LINEAGE_ID: Final[str] = "espn-site-api-v2"
ESPN_REVISION_DOMAIN_ID: Final[str] = "espn-site-api-v2"

SINGLES_GROUPING_SLUGS: Final[frozenset[str]] = frozenset(
    {"mens-singles", "womens-singles"}
)

_MAX_COMPETITIONS: Final[int] = 4096
_MAX_SETS: Final[int] = 5
_GAMES_REVISION_SCALE: Final[int] = 100
_PLACEHOLDER_ID_RE: Final[Pattern[str]] = pattern_compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",
    RE_ASCII,
)

_LIVE_STATUSES: Final[frozenset[str]] = frozenset({"STATUS_IN_PROGRESS"})
_SUSPENDED_STATUSES: Final[frozenset[str]] = frozenset(
    {"STATUS_SUSPENDED", "STATUS_DELAYED", "STATUS_RAIN_DELAY"}
)
_SCHEDULED_STATUSES: Final[frozenset[str]] = frozenset(
    {"STATUS_SCHEDULED", "STATUS_PRE"}
)
_CANCELLED_STATUSES: Final[frozenset[str]] = frozenset(
    {"STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_POSTPONED"}
)
_STATUS_RANK: Final[dict[MatchStatus, int]] = {
    MatchStatus.SCHEDULED: 0,
    MatchStatus.LIVE: 1,
    MatchStatus.SUSPENDED: 2,
    MatchStatus.CANCELLED: 3,
    MatchStatus.ENDED: 4,
}


class EspnTennisError(ValueError):
    """Raised when an ESPN document cannot be normalized safely."""


@dataclass(frozen=True, slots=True)
class EspnSkippedCompetition:
    """A competition deliberately excluded from normalization."""

    competition_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class EspnScoreboardNormalization:
    """Snapshots for every supported singles match plus explicit exclusions."""

    snapshots: tuple[ProviderSnapshot, ...]
    skipped: tuple[EspnSkippedCompetition, ...]

    def snapshot_for(self, competition_id: str) -> ProviderSnapshot:
        """Return one snapshot, failing closed when it was not normalized."""
        for snapshot in self.snapshots:
            if snapshot.provider_match_id == competition_id:
                return snapshot
        for entry in self.skipped:
            if entry.competition_id == competition_id:
                raise EspnTennisError(f"espn_competition_skipped:{entry.reason}")
        raise EspnTennisError("espn_competition_absent")


def espn_source_lineage_sha256(provider_manifest_sha256: str) -> str:
    return compute_expert_provider_source_lineage_sha256(
        ESPN_PROVIDER_ID,
        ESPN_PRODUCT_TIER,
        ESPN_SOURCE_LINEAGE_ID,
        provider_manifest_sha256,
    )


def _mapping(value: object, name: str) -> dict:
    if type(value) is not dict:
        raise EspnTennisError(name)
    return value


def _sequence(value: object, name: str) -> list:
    if type(value) is not list:
        raise EspnTennisError(name)
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise EspnTennisError(name)
    return value


def _wall_ns_from_iso(value: str) -> int:
    if not value.endswith("Z"):
        raise EspnTennisError("espn_date_invalid")
    offset_form = value[:-1] + "+0000"
    for layout in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z"):
        try:
            parsed = datetime.strptime(offset_form, layout)
        except ValueError:
            continue
        return int(parsed.timestamp()) * 1_000_000_000
    raise EspnTennisError("espn_date_invalid")


def _games_value(entry: dict) -> int:
    raw = entry.get("value")
    if type(raw) is int:
        return raw
    if type(raw) is float and raw.is_integer():
        return int(raw)
    raise EspnTennisError("espn_linescore_value")


def _tiebreak_value(entry: dict) -> int | None:
    raw = entry.get("tiebreak")
    if raw is None:
        return None
    if type(raw) is int:
        return raw
    if type(raw) is float and raw.is_integer():
        return int(raw)
    raise EspnTennisError("espn_linescore_tiebreak")


def _status_of(competition: dict) -> str:
    status = _mapping(competition.get("status"), "espn_status")
    kind = _mapping(status.get("type"), "espn_status_type")
    return _text(kind.get("name"), "espn_status_name")


def _sides(competition: dict) -> tuple[dict, dict]:
    competitors = _sequence(competition.get("competitors"), "espn_competitors")
    if len(competitors) != 2:
        raise EspnTennisError("espn_competitor_count")
    home: dict | None = None
    away: dict | None = None
    for raw in competitors:
        competitor = _mapping(raw, "espn_competitor")
        placement = competitor.get("homeAway")
        if placement == "home":
            home = competitor
        elif placement == "away":
            away = competitor
    if home is None or away is None:
        raise EspnTennisError("espn_home_away")
    return home, away


def _player_id(competitor: dict) -> str:
    """Return a real athlete id, rejecting unfilled draw placeholders.

    ESPN fills undecided draw slots with negative ids such as ``-3`` and a
    ``TBD`` display name. Those are not players and must never reach a
    snapshot identity.
    """
    identifier = _text(competitor.get("id"), "espn_athlete_id")
    if _PLACEHOLDER_ID_RE.fullmatch(identifier) is None:
        raise EspnTennisError("espn_player_placeholder")
    return identifier


def _match_format(
    grouping_slug: str,
    *,
    major: bool,
) -> MatchFormat:
    if grouping_slug == "mens-singles" and major:
        return MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS
    return MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS


def _score_state(
    home: dict,
    away: dict,
) -> tuple[tuple[SetScore, ...], int, int, bool, int, int]:
    home_lines = _sequence(home.get("linescores", []), "espn_linescores")
    away_lines = _sequence(away.get("linescores", []), "espn_linescores")
    if len(home_lines) != len(away_lines):
        raise EspnTennisError("espn_linescore_length")
    if len(home_lines) > _MAX_SETS:
        raise EspnTennisError("espn_linescore_count")

    completed: list[SetScore] = []
    games_home = 0
    games_away = 0
    in_tiebreak = False
    tiebreak_home = 0
    tiebreak_away = 0

    for index, raw_home in enumerate(home_lines):
        entry_home = _mapping(raw_home, "espn_linescore")
        entry_away = _mapping(away_lines[index], "espn_linescore")
        set_home = _games_value(entry_home)
        set_away = _games_value(entry_away)
        break_home = _tiebreak_value(entry_home)
        break_away = _tiebreak_value(entry_away)
        decided = "winner" in entry_home or "winner" in entry_away
        if decided:
            if (break_home is None) != (break_away is None):
                raise EspnTennisError("espn_tiebreak_pairing")
            completed.append(
                SetScore(
                    games_home=set_home,
                    games_away=set_away,
                    tiebreak_points_home=break_home,
                    tiebreak_points_away=break_away,
                )
            )
            continue
        games_home = set_home
        games_away = set_away
        if set_home == 6 and set_away == 6:
            in_tiebreak = True
            tiebreak_home = 0 if break_home is None else break_home
            tiebreak_away = 0 if break_away is None else break_away

    return (
        tuple(completed),
        games_home,
        games_away,
        in_tiebreak,
        tiebreak_home,
        tiebreak_away,
    )


def _server(home: dict, away: dict) -> PlayerSide | None:
    if home.get("possession") is True:
        return PlayerSide.HOME
    if away.get("possession") is True:
        return PlayerSide.AWAY
    return None


def _outcome(
    espn_status: str,
    home: dict,
    away: dict,
) -> tuple[MatchStatus, TerminationKind, PlayerSide | None, PlayerSide | None]:
    if espn_status in _LIVE_STATUSES:
        return MatchStatus.LIVE, TerminationKind.NONE, None, None
    if espn_status in _SUSPENDED_STATUSES:
        return MatchStatus.SUSPENDED, TerminationKind.NONE, None, None
    if espn_status in _SCHEDULED_STATUSES:
        return MatchStatus.SCHEDULED, TerminationKind.NONE, None, None
    if espn_status in _CANCELLED_STATUSES:
        return (
            MatchStatus.CANCELLED,
            TerminationKind.CANCELLATION,
            None,
            None,
        )

    if home.get("winner") is True:
        winner = PlayerSide.HOME
        loser = PlayerSide.AWAY
    elif away.get("winner") is True:
        winner = PlayerSide.AWAY
        loser = PlayerSide.HOME
    else:
        raise EspnTennisError("espn_winner_absent")

    if espn_status == "STATUS_FINAL":
        return MatchStatus.ENDED, TerminationKind.NATURAL, winner, None
    if espn_status == "STATUS_WALKOVER":
        return MatchStatus.ENDED, TerminationKind.WALKOVER, winner, None
    if espn_status == "STATUS_RETIRED":
        return MatchStatus.ENDED, TerminationKind.RETIREMENT, winner, loser
    raise EspnTennisError("espn_status_unsupported")


def _revision(
    status: MatchStatus,
    completed_sets: tuple[SetScore, ...],
    games_home: int,
    games_away: int,
    tiebreak_home: int,
    tiebreak_away: int,
) -> int:
    total_games = games_home + games_away
    for entry in completed_sets:
        total_games += entry.games_home + entry.games_away
    total_tiebreak = tiebreak_home + tiebreak_away
    return (
        total_games * _GAMES_REVISION_SCALE
        + total_tiebreak
        + _STATUS_RANK[status]
    )


def _snapshot(
    competition: dict,
    *,
    grouping_slug: str,
    major: bool,
    source_lineage_sha256: str,
    source_wall_ns: int,
    received_monotonic_ns: int,
    clock_uncertainty_ns: int,
) -> ProviderSnapshot:
    competition_id = _text(competition.get("id"), "espn_competition_id")
    espn_status = _status_of(competition)
    home, away = _sides(competition)
    status, termination, winner, retired = _outcome(espn_status, home, away)
    (
        completed_sets,
        games_home,
        games_away,
        in_tiebreak,
        tiebreak_home,
        tiebreak_away,
    ) = _score_state(home, away)

    server = (
        _server(home, away)
        if status in (MatchStatus.LIVE, MatchStatus.SUSPENDED)
        else None
    )
    revision = _revision(
        status,
        completed_sets,
        games_home,
        games_away,
        tiebreak_home,
        tiebreak_away,
    )
    scheduled_start = _wall_ns_from_iso(
        _text(
            competition.get("startDate") or competition.get("date"),
            "espn_start_date",
        )
    )
    return ProviderSnapshot(
        provider_source_id=ESPN_PROVIDER_ID,
        revision_domain_id=ESPN_REVISION_DOMAIN_ID,
        source_lineage_sha256=source_lineage_sha256,
        provider_event_id=f"{competition_id}-r{revision}",
        provider_match_id=competition_id,
        home_player_id=_player_id(home),
        away_player_id=_player_id(away),
        scheduled_start_wall_ns=scheduled_start,
        match_format=_match_format(grouping_slug, major=major),
        status=status,
        termination_kind=termination,
        winner=winner,
        retired_side=retired,
        completed_sets=completed_sets,
        games_home=games_home,
        games_away=games_away,
        points_home=ScoreValue.LOVE,
        points_away=ScoreValue.LOVE,
        in_tiebreak=in_tiebreak,
        tiebreak_points_home=tiebreak_home,
        tiebreak_points_away=tiebreak_away,
        tiebreak_first_server=None,
        server_for_next_point=server,
        correction_epoch=0,
        revision=revision,
        source_wall_ns=source_wall_ns,
        source_generated_wall_ns=source_wall_ns,
        received_monotonic_ns=received_monotonic_ns,
        clock_uncertainty_ns=clock_uncertainty_ns,
        snapshot_complete=True,
    )


def normalize_espn_scoreboard(
    document: object,
    *,
    source_lineage_sha256: str,
    source_wall_ns: int,
    received_monotonic_ns: int,
    clock_uncertainty_ns: int,
) -> EspnScoreboardNormalization:
    """Convert one ESPN scoreboard document into singles provider snapshots."""
    payload = _mapping(document, "espn_document")
    events = _sequence(payload.get("events", []), "espn_events")
    snapshots: list[ProviderSnapshot] = []
    skipped: list[EspnSkippedCompetition] = []
    seen: set[str] = set()
    total = 0

    for raw_event in events:
        event = _mapping(raw_event, "espn_event")
        major = event.get("major") is True
        for raw_grouping in _sequence(
            event.get("groupings", []),
            "espn_groupings",
        ):
            grouping = _mapping(raw_grouping, "espn_grouping")
            descriptor = _mapping(grouping.get("grouping"), "espn_grouping_id")
            slug = _text(descriptor.get("slug"), "espn_grouping_slug")
            for raw_competition in _sequence(
                grouping.get("competitions", []),
                "espn_competitions",
            ):
                total += 1
                if total > _MAX_COMPETITIONS:
                    raise EspnTennisError("espn_competition_capacity")
                competition = _mapping(raw_competition, "espn_competition")
                competition_id = _text(
                    competition.get("id"),
                    "espn_competition_id",
                )
                if competition_id in seen:
                    continue
                seen.add(competition_id)
                if slug not in SINGLES_GROUPING_SLUGS:
                    skipped.append(
                        EspnSkippedCompetition(competition_id, "not_singles")
                    )
                    continue
                try:
                    snapshots.append(
                        _snapshot(
                            competition,
                            grouping_slug=slug,
                            major=major,
                            source_lineage_sha256=source_lineage_sha256,
                            source_wall_ns=source_wall_ns,
                            received_monotonic_ns=received_monotonic_ns,
                            clock_uncertainty_ns=clock_uncertainty_ns,
                        )
                    )
                except EspnTennisError as error:
                    skipped.append(
                        EspnSkippedCompetition(competition_id, str(error))
                    )
                except ExpertContractError as error:
                    # One malformed competition must never discard the rest of
                    # a poll that may contain a match with an open position.
                    skipped.append(
                        EspnSkippedCompetition(
                            competition_id,
                            f"contract_rejected:{error}",
                        )
                    )

    return EspnScoreboardNormalization(
        snapshots=tuple(snapshots),
        skipped=tuple(skipped),
    )


def live_snapshots(
    normalization: EspnScoreboardNormalization,
) -> tuple[ProviderSnapshot, ...]:
    if type(normalization) is not EspnScoreboardNormalization:
        raise TypeError("normalization")
    return tuple(
        snapshot
        for snapshot in normalization.snapshots
        if snapshot.status is MatchStatus.LIVE
    )


__all__: Final[tuple[str, ...]] = (
    "ESPN_PRODUCT_TIER",
    "ESPN_PROVIDER_ID",
    "ESPN_REVISION_DOMAIN_ID",
    "ESPN_SOURCE_LINEAGE_ID",
    "EspnScoreboardNormalization",
    "EspnSkippedCompetition",
    "EspnTennisError",
    "SINGLES_GROUPING_SLUGS",
    "espn_source_lineage_sha256",
    "live_snapshots",
    "normalize_espn_scoreboard",
)
