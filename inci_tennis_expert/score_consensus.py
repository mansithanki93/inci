from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from inci_tennis_expert.contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ScoreValue,
    SetScore,
    TennisState,
    TennisStateInvariantError,
    TerminationKind,
)
from inci_tennis_expert.tennis_score import validate_tennis_state


def _nonempty_string(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(name)
    if not value:
        raise ValueError(name)
    return value


def _sha256(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(name)
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(name)
    return value


def _nonnegative_integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(name)
    if value < 0:
        raise ValueError(name)
    return value


class ConsensusReason(str, Enum):
    ACCEPTED = "accepted"
    INSUFFICIENT_DISTINCT_LINEAGES = "insufficient_distinct_lineages"
    PRIMARY_BLOCKED = "primary_blocked"
    PRIMARY_INCOMPLETE = "primary_incomplete"
    PRIMARY_MISSING = "primary_missing"
    PRIMARY_QUARANTINED = "primary_quarantined"
    PRIMARY_STALE = "primary_stale"
    SOURCE_CONFIGURATION_MISMATCH = "source_configuration_mismatch"
    STATE_MISMATCH = "state_mismatch"
    TRANSITION_REGRESSION = "transition_regression"
    WITNESS_BLOCKED = "witness_blocked"
    WITNESS_INCOMPLETE = "witness_incomplete"
    WITNESS_MISSING = "witness_missing"
    WITNESS_STALE = "witness_stale"


@dataclass(frozen=True, slots=True)
class ScoreSourceConfig:
    source_id: str
    source_lineage_sha256: str
    max_age_ns: int
    independence_lineage_id: str | None = None
    provider_match_id: str | None = None
    provider_home_player_id: str | None = None
    provider_away_player_id: str | None = None
    canonical_home_player_id: str | None = None
    canonical_away_player_id: str | None = None

    def __post_init__(self) -> None:
        _nonempty_string(self.source_id, "source_id")
        _sha256(self.source_lineage_sha256, "source_lineage_sha256")
        _nonnegative_integer(self.max_age_ns, "max_age_ns")
        if self.independence_lineage_id is not None:
            _nonempty_string(
                self.independence_lineage_id,
                "independence_lineage_id",
            )
        identity = (
            self.provider_match_id,
            self.provider_home_player_id,
            self.provider_away_player_id,
            self.canonical_home_player_id,
            self.canonical_away_player_id,
        )
        if any(value is None for value in identity):
            raise ValueError("provider_identity")
        _nonempty_string(self.provider_match_id, "provider_match_id")
        _nonempty_string(
            self.provider_home_player_id,
            "provider_home_player_id",
        )
        _nonempty_string(
            self.provider_away_player_id,
            "provider_away_player_id",
        )
        _nonempty_string(
            self.canonical_home_player_id,
            "canonical_home_player_id",
        )
        _nonempty_string(
            self.canonical_away_player_id,
            "canonical_away_player_id",
        )
        if self.provider_home_player_id == self.provider_away_player_id:
            raise ValueError("provider_player_identity")
        if self.canonical_home_player_id == self.canonical_away_player_id:
            raise ValueError("canonical_player_identity")


@dataclass(frozen=True, slots=True)
class ScoreConsensusPolicy:
    primary_source_id: str
    sources: tuple[ScoreSourceConfig, ...]

    def __post_init__(self) -> None:
        _nonempty_string(self.primary_source_id, "primary_source_id")
        if type(self.sources) is not tuple:
            raise TypeError("sources")
        if any(type(source) is not ScoreSourceConfig for source in self.sources):
            raise TypeError("sources")
        primary_count = sum(
            source.source_id == self.primary_source_id
            for source in self.sources
        )
        if primary_count != 1:
            raise ValueError("primary_source_id")
        source_ids = tuple(source.source_id for source in self.sources)
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source_id")
        independence_by_source_lineage: dict[str, str | None] = {}
        for source in self.sources:
            lineage = source.source_lineage_sha256
            independence = source.independence_lineage_id
            if (
                lineage in independence_by_source_lineage
                and independence_by_source_lineage[lineage] != independence
            ):
                raise ValueError("source_lineage_independence")
            independence_by_source_lineage[lineage] = independence
        canonical_orientations = {
            (
                source.canonical_home_player_id,
                source.canonical_away_player_id,
            )
            for source in self.sources
        }
        if len(canonical_orientations) != 1:
            raise ValueError("canonical_player_identity")


@dataclass(frozen=True, slots=True)
class ScoreConsensusResult:
    accepted_state: TennisState | None
    supporting_source_ids: tuple[str, ...]
    supporting_lineages: tuple[str, ...]
    reason: ConsensusReason


@dataclass(frozen=True, slots=True)
class ScoreConsensusState:
    accepted_state: TennisState | None
    quarantined: bool
    consensus_epoch: int
    quarantine_barrier_monotonic_ns: int | None

    def __post_init__(self) -> None:
        if self.accepted_state is not None and type(
            self.accepted_state
        ) is not TennisState:
            raise TypeError("accepted_state")
        if type(self.quarantined) is not bool:
            raise TypeError("quarantined")
        _nonnegative_integer(self.consensus_epoch, "consensus_epoch")
        if self.quarantine_barrier_monotonic_ns is not None:
            _nonnegative_integer(
                self.quarantine_barrier_monotonic_ns,
                "quarantine_barrier_monotonic_ns",
            )
        if self.quarantined != (
            self.quarantine_barrier_monotonic_ns is not None
        ):
            raise ValueError("quarantine_barrier_monotonic_ns")


def initial_score_consensus_state() -> ScoreConsensusState:
    return ScoreConsensusState(
        accepted_state=None,
        quarantined=False,
        consensus_epoch=0,
        quarantine_barrier_monotonic_ns=None,
    )


def _abstain(reason: ConsensusReason) -> ScoreConsensusResult:
    return ScoreConsensusResult(None, (), (), reason)


def _independence_lineage(source: ScoreSourceConfig) -> str | None:
    return source.independence_lineage_id


def _state_matches_config(
    state: TennisState,
    source: ScoreSourceConfig,
) -> bool:
    return (
        state.provider_source_id == source.source_id
        and state.source_lineage_sha256 == source.source_lineage_sha256
        and (
            source.provider_match_id is None
            or state.provider_match_id == source.provider_match_id
        )
        and (
            source.provider_home_player_id is None
            or state.home_player_id == source.provider_home_player_id
        )
        and (
            source.provider_away_player_id is None
            or state.away_player_id == source.provider_away_player_id
        )
    )


def _consensus_coordinates(
    state: TennisState,
    source: ScoreSourceConfig,
) -> tuple[object, ...]:
    return (
        source.canonical_home_player_id,
        source.canonical_away_player_id,
        state.scheduled_start_wall_ns,
        state.match_format,
        state.status,
        state.termination_kind,
        state.winner,
        state.retired_side,
        state.completed_sets,
        state.games_home,
        state.games_away,
        state.points_home,
        state.points_away,
        state.in_tiebreak,
        state.tiebreak_points_home,
        state.tiebreak_points_away,
        state.tiebreak_first_server,
        state.server_for_next_point,
    )


def reduce_score_consensus(
    policy: ScoreConsensusPolicy,
    observations: Mapping[str, TennisState | None],
    *,
    now_monotonic_ns: int,
) -> ScoreConsensusResult:
    """Return batch diagnostics; this stateless result is not authoritative."""
    if type(policy) is not ScoreConsensusPolicy:
        raise TypeError("policy")
    if not isinstance(observations, Mapping):
        raise TypeError("observations")
    _nonnegative_integer(now_monotonic_ns, "now_monotonic_ns")
    configured_source_ids = {source.source_id for source in policy.sources}
    for source_id, state in observations.items():
        if type(source_id) is not str:
            raise TypeError("observations")
        if source_id not in configured_source_ids:
            raise ValueError("observations")
        if state is not None and type(state) is not TennisState:
            raise TypeError("observations")
    if policy.primary_source_id not in observations:
        return _abstain(ConsensusReason.PRIMARY_MISSING)
    primary = observations[policy.primary_source_id]
    if primary is None:
        return _abstain(ConsensusReason.PRIMARY_INCOMPLETE)

    configs = {source.source_id: source for source in policy.sources}
    primary_config = configs[policy.primary_source_id]
    if not _state_matches_config(primary, primary_config):
        return _abstain(ConsensusReason.SOURCE_CONFIGURATION_MISMATCH)
    if primary.block_reason is not None:
        return _abstain(ConsensusReason.PRIMARY_BLOCKED)
    primary_age_ns = now_monotonic_ns - primary.last_received_monotonic_ns
    if not 0 <= primary_age_ns <= primary_config.max_age_ns:
        return _abstain(ConsensusReason.PRIMARY_STALE)
    primary_coordinates = _consensus_coordinates(primary, primary_config)
    supporters = [primary_config]
    blocked_witness_found = False
    configuration_mismatch_found = False
    dissenting_lineages: dict[tuple[object, ...], set[str]] = {}
    incomplete_witness_found = False
    missing_witness_found = False
    stale_witness_found = False
    for source in policy.sources:
        if source.source_id == policy.primary_source_id:
            continue
        if source.source_id not in observations:
            missing_witness_found = True
            continue
        state = observations[source.source_id]
        if state is None:
            incomplete_witness_found = True
            continue
        if not _state_matches_config(state, source):
            configuration_mismatch_found = True
            continue
        if state.block_reason is not None:
            blocked_witness_found = True
            continue
        age_ns = now_monotonic_ns - state.last_received_monotonic_ns
        if not 0 <= age_ns <= source.max_age_ns:
            stale_witness_found = True
            continue
        coordinates = _consensus_coordinates(state, source)
        if coordinates == primary_coordinates:
            supporters.append(source)
        else:
            lineages = dissenting_lineages.setdefault(coordinates, set())
            lineage = _independence_lineage(source)
            if lineage is not None:
                lineages.add(lineage)

    supporting_lineages = {
        lineage
        for source in supporters
        if (lineage := _independence_lineage(source)) is not None
    }
    primary_lineage = _independence_lineage(primary_config)
    has_independent_witness = primary_lineage is not None and any(
        lineage != primary_lineage for lineage in supporting_lineages
    )
    if not has_independent_witness and any(
        len(lineages) >= 2 for lineages in dissenting_lineages.values()
    ):
        return _abstain(ConsensusReason.PRIMARY_QUARANTINED)
    if not has_independent_witness and dissenting_lineages:
        return _abstain(ConsensusReason.STATE_MISMATCH)
    if not has_independent_witness and configuration_mismatch_found:
        return _abstain(ConsensusReason.SOURCE_CONFIGURATION_MISMATCH)
    if not has_independent_witness and blocked_witness_found:
        return _abstain(ConsensusReason.WITNESS_BLOCKED)
    if not has_independent_witness and stale_witness_found:
        return _abstain(ConsensusReason.WITNESS_STALE)
    if not has_independent_witness and incomplete_witness_found:
        return _abstain(ConsensusReason.WITNESS_INCOMPLETE)
    if not has_independent_witness and missing_witness_found:
        return _abstain(ConsensusReason.WITNESS_MISSING)
    if not has_independent_witness:
        return _abstain(ConsensusReason.INSUFFICIENT_DISTINCT_LINEAGES)
    return ScoreConsensusResult(
        accepted_state=primary,
        supporting_source_ids=tuple(
            sorted(source.source_id for source in supporters)
        ),
        supporting_lineages=tuple(sorted(supporting_lineages)),
        reason=ConsensusReason.ACCEPTED,
    )


def _same_score_coordinates(left: TennisState, right: TennisState) -> bool:
    return (
        left.completed_sets,
        left.games_home,
        left.games_away,
        left.points_home,
        left.points_away,
        left.in_tiebreak,
        left.tiebreak_points_home,
        left.tiebreak_points_away,
        left.tiebreak_first_server,
        left.server_for_next_point,
        left.status,
        left.termination_kind,
        left.winner,
        left.retired_side,
    ) == (
        right.completed_sets,
        right.games_home,
        right.games_away,
        right.points_home,
        right.points_away,
        right.in_tiebreak,
        right.tiebreak_points_home,
        right.tiebreak_points_away,
        right.tiebreak_first_server,
        right.server_for_next_point,
        right.status,
        right.termination_kind,
        right.winner,
        right.retired_side,
    )


_NEXT_POINT_SCORE = {
    ScoreValue.LOVE: ScoreValue.FIFTEEN,
    ScoreValue.FIFTEEN: ScoreValue.THIRTY,
    ScoreValue.THIRTY: ScoreValue.FORTY,
}


def _normal_point_successors(
    home: ScoreValue,
    away: ScoreValue,
) -> tuple[tuple[ScoreValue, ScoreValue], ...]:
    if home is ScoreValue.ADVANTAGE:
        return ((ScoreValue.FORTY, ScoreValue.FORTY),)
    if away is ScoreValue.ADVANTAGE:
        return ((ScoreValue.FORTY, ScoreValue.FORTY),)
    if home is ScoreValue.FORTY and away is ScoreValue.FORTY:
        return (
            (ScoreValue.ADVANTAGE, ScoreValue.FORTY),
            (ScoreValue.FORTY, ScoreValue.ADVANTAGE),
        )
    successors: list[tuple[ScoreValue, ScoreValue]] = []
    if home is not ScoreValue.FORTY:
        successors.append((_NEXT_POINT_SCORE[home], away))
    if away is not ScoreValue.FORTY:
        successors.append((home, _NEXT_POINT_SCORE[away]))
    return tuple(successors)


def _normal_point_reachable(
    prior: tuple[ScoreValue, ScoreValue],
    candidate: tuple[ScoreValue, ScoreValue],
) -> bool:
    pending = [prior]
    seen: set[tuple[ScoreValue, ScoreValue]] = set()
    while pending:
        current = pending.pop()
        if current == candidate:
            return True
        if current in seen:
            continue
        seen.add(current)
        pending.extend(
            successor
            for successor in _normal_point_successors(*current)
            if successor not in seen
        )
    return False


def _opposite(side: PlayerSide) -> PlayerSide:
    if side is PlayerSide.HOME:
        return PlayerSide.AWAY
    return PlayerSide.HOME


def _same_authority_identity(
    prior: TennisState,
    candidate: TennisState,
) -> bool:
    return (
        candidate.provider_source_id == prior.provider_source_id
        and candidate.revision_domain_id == prior.revision_domain_id
        and candidate.source_lineage_sha256
        == prior.source_lineage_sha256
        and candidate.provider_match_id == prior.provider_match_id
        and candidate.home_player_id == prior.home_player_id
        and candidate.away_player_id == prior.away_player_id
        and candidate.scheduled_start_wall_ns
        == prior.scheduled_start_wall_ns
        and candidate.match_format is prior.match_format
    )


def _state_is_reachable(state: TennisState) -> bool:
    if (
        state.match_format
        is not MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS
    ):
        return False
    if not _completed_sets_are_exact(state.completed_sets):
        return False
    try:
        validate_tennis_state(state)
    except TennisStateInvariantError:
        return False
    return True


def _same_play_coordinates(
    prior: TennisState,
    candidate: TennisState,
) -> bool:
    return (
        prior.completed_sets,
        prior.games_home,
        prior.games_away,
        prior.points_home,
        prior.points_away,
        prior.in_tiebreak,
        prior.tiebreak_points_home,
        prior.tiebreak_points_away,
        prior.tiebreak_first_server,
    ) == (
        candidate.completed_sets,
        candidate.games_home,
        candidate.games_away,
        candidate.points_home,
        candidate.points_away,
        candidate.in_tiebreak,
        candidate.tiebreak_points_home,
        candidate.tiebreak_points_away,
        candidate.tiebreak_first_server,
    )


def _live_nonterminal(state: TennisState) -> bool:
    return (
        state.status is MatchStatus.LIVE
        and state.termination_kind is TerminationKind.NONE
        and state.winner is None
        and state.retired_side is None
    )


def _legal_lifecycle_transition(
    prior: TennisState,
    candidate: TennisState,
) -> bool:
    if not _same_play_coordinates(prior, candidate):
        return False
    if _same_score_coordinates(prior, candidate):
        return True
    if (
        prior.status is MatchStatus.SCHEDULED
        and candidate.status is MatchStatus.LIVE
    ):
        return candidate.server_for_next_point is not None
    if (
        (prior.status, candidate.status)
        in {
            (MatchStatus.LIVE, MatchStatus.SUSPENDED),
            (MatchStatus.SUSPENDED, MatchStatus.LIVE),
        }
    ):
        return (
            candidate.server_for_next_point
            is prior.server_for_next_point
        )
    if (
        prior.status is MatchStatus.SCHEDULED
        and candidate.status is MatchStatus.ENDED
        and candidate.termination_kind is TerminationKind.WALKOVER
    ):
        return True
    if (
        prior.status in {MatchStatus.LIVE, MatchStatus.SUSPENDED}
        and candidate.status is MatchStatus.ENDED
        and candidate.termination_kind is TerminationKind.RETIREMENT
    ):
        return True
    return (
        prior.status
        in {
            MatchStatus.SCHEDULED,
            MatchStatus.LIVE,
            MatchStatus.SUSPENDED,
        }
        and candidate.status is MatchStatus.CANCELLED
        and candidate.termination_kind is TerminationKind.CANCELLATION
    )


def _tiebreak_won(home: int, away: int) -> bool:
    return (
        home >= 7
        and home - away >= 2
        or away >= 7
        and away - home >= 2
    )


def _tiebreak_server(
    first_server: PlayerSide,
    completed_points: int,
) -> PlayerSide:
    if completed_points == 0:
        return first_server
    if ((completed_points - 1) // 2) % 2 == 0:
        return _opposite(first_server)
    return first_server


def _tiebreak_progress_reachable(
    prior_home: int,
    prior_away: int,
    candidate: TennisState,
    *,
    first_server: PlayerSide,
) -> bool:
    home = candidate.tiebreak_points_home
    away = candidate.tiebreak_points_away
    return (
        candidate.in_tiebreak
        and candidate.tiebreak_first_server is first_server
        and home >= prior_home
        and away >= prior_away
        and not _tiebreak_won(home, away)
        and candidate.server_for_next_point
        is _tiebreak_server(first_server, home + away)
        and _live_nonterminal(candidate)
    )


def _valid_tiebreak_final(home: int, away: int) -> PlayerSide | None:
    if home == 7 and 0 <= away <= 5:
        return PlayerSide.HOME
    if away == 7 and 0 <= home <= 5:
        return PlayerSide.AWAY
    if home >= 8 and home == away + 2:
        return PlayerSide.HOME
    if away >= 8 and away == home + 2:
        return PlayerSide.AWAY
    return None


def _completed_sets_are_exact(
    completed_sets: tuple[SetScore, ...],
) -> bool:
    home_wins = 0
    away_wins = 0
    for index, set_score in enumerate(completed_sets):
        if set_score.tiebreak_points_home is None:
            winner = _normal_set_winner(
                set_score.games_home,
                set_score.games_away,
            )
        else:
            if set_score.tiebreak_points_away is None:
                return False
            winner = _valid_tiebreak_final(
                set_score.tiebreak_points_home,
                set_score.tiebreak_points_away,
            )
            if winner is None:
                return False
            if winner is PlayerSide.HOME:
                expected_games = (7, 6)
            else:
                expected_games = (6, 7)
            if (
                set_score.games_home,
                set_score.games_away,
            ) != expected_games:
                return False
        if winner is None:
            return False
        if winner is PlayerSide.HOME:
            home_wins += 1
        else:
            away_wins += 1
        if (
            (home_wins == 2 or away_wins == 2)
            and index != len(completed_sets) - 1
        ):
            return False
    return True


def _set_wins(
    completed_sets: tuple[SetScore, ...],
) -> tuple[int, int]:
    home = 0
    away = 0
    for set_score in completed_sets:
        if set_score.games_home > set_score.games_away:
            home += 1
        else:
            away += 1
    return home, away


def _candidate_after_completed_set(
    prior: TennisState,
    candidate: TennisState,
    completed_set: SetScore,
    *,
    next_server: PlayerSide,
) -> bool:
    completed_sets = (*prior.completed_sets, completed_set)
    if (
        candidate.completed_sets != completed_sets
        or candidate.games_home != 0
        or candidate.games_away != 0
        or candidate.in_tiebreak
    ):
        return False
    home_wins, away_wins = _set_wins(completed_sets)
    if home_wins == 2 or away_wins == 2:
        winner = (
            PlayerSide.HOME if home_wins == 2 else PlayerSide.AWAY
        )
        return (
            candidate.status is MatchStatus.ENDED
            and candidate.termination_kind is TerminationKind.NATURAL
            and candidate.winner is winner
            and candidate.retired_side is None
            and candidate.points_home is ScoreValue.LOVE
            and candidate.points_away is ScoreValue.LOVE
            and candidate.server_for_next_point is None
        )
    return (
        _live_nonterminal(candidate)
        and candidate.server_for_next_point is next_server
        and _normal_point_reachable(
            (ScoreValue.LOVE, ScoreValue.LOVE),
            (candidate.points_home, candidate.points_away),
        )
    )


def _normal_set_winner(home: int, away: int) -> PlayerSide | None:
    if (home == 6 and 0 <= away <= 4) or (home == 7 and away == 5):
        return PlayerSide.HOME
    if (away == 6 and 0 <= home <= 4) or (away == 7 and home == 5):
        return PlayerSide.AWAY
    return None


def _normal_game_transition_reachable(
    prior: TennisState,
    candidate: TennisState,
) -> bool:
    if prior.server_for_next_point is None:
        return False
    next_server = _opposite(prior.server_for_next_point)
    for game_winner in (PlayerSide.HOME, PlayerSide.AWAY):
        games_home = prior.games_home + (
            1 if game_winner is PlayerSide.HOME else 0
        )
        games_away = prior.games_away + (
            1 if game_winner is PlayerSide.AWAY else 0
        )
        set_winner = _normal_set_winner(games_home, games_away)
        if set_winner is not None:
            if _candidate_after_completed_set(
                prior,
                candidate,
                SetScore(games_home, games_away, None, None),
                next_server=next_server,
            ):
                return True
            continue
        if games_home == 6 and games_away == 6:
            if (
                candidate.completed_sets == prior.completed_sets
                and candidate.games_home == 6
                and candidate.games_away == 6
                and _tiebreak_progress_reachable(
                    0,
                    0,
                    candidate,
                    first_server=next_server,
                )
            ):
                return True
            continue
        if (
            candidate.completed_sets == prior.completed_sets
            and candidate.games_home == games_home
            and candidate.games_away == games_away
            and not candidate.in_tiebreak
            and candidate.server_for_next_point is next_server
            and _live_nonterminal(candidate)
            and _normal_point_reachable(
                (ScoreValue.LOVE, ScoreValue.LOVE),
                (candidate.points_home, candidate.points_away),
            )
        ):
            return True
    return False


def _tiebreak_completion_reachable(
    prior: TennisState,
    candidate: TennisState,
) -> bool:
    if len(candidate.completed_sets) != len(prior.completed_sets) + 1:
        return False
    completed_set = candidate.completed_sets[-1]
    home = completed_set.tiebreak_points_home
    away = completed_set.tiebreak_points_away
    if home is None or away is None:
        return False
    winner = _valid_tiebreak_final(home, away)
    if winner is None:
        return False
    if winner is PlayerSide.HOME:
        expected_games = (7, 6)
        preterminal = (home - 1, away)
    else:
        expected_games = (6, 7)
        preterminal = (home, away - 1)
    if (completed_set.games_home, completed_set.games_away) != expected_games:
        return False
    if (
        prior.tiebreak_points_home > preterminal[0]
        or prior.tiebreak_points_away > preterminal[1]
    ):
        return False
    if prior.tiebreak_first_server is None:
        return False
    return _candidate_after_completed_set(
        prior,
        candidate,
        completed_set,
        next_server=_opposite(prior.tiebreak_first_server),
    )


def _ordinary_score_transition_reachable(
    prior: TennisState,
    candidate: TennisState,
) -> bool:
    if _same_play_coordinates(prior, candidate):
        return _legal_lifecycle_transition(prior, candidate)
    if not _live_nonterminal(prior):
        return False
    if prior.in_tiebreak:
        if prior.tiebreak_first_server is None:
            return False
        if (
            candidate.completed_sets == prior.completed_sets
            and candidate.games_home == 6
            and candidate.games_away == 6
            and _tiebreak_progress_reachable(
                prior.tiebreak_points_home,
                prior.tiebreak_points_away,
                candidate,
                first_server=prior.tiebreak_first_server,
            )
        ):
            return True
        return _tiebreak_completion_reachable(prior, candidate)
    if (
        candidate.completed_sets == prior.completed_sets
        and candidate.games_home == prior.games_home
        and candidate.games_away == prior.games_away
        and not candidate.in_tiebreak
        and candidate.server_for_next_point
        is prior.server_for_next_point
        and _live_nonterminal(candidate)
        and _normal_point_reachable(
            (prior.points_home, prior.points_away),
            (candidate.points_home, candidate.points_away),
        )
    ):
        return True
    return _normal_game_transition_reachable(prior, candidate)


def _transition_is_legal(
    prior: TennisState | None,
    candidate: TennisState,
) -> bool:
    if not _state_is_reachable(candidate):
        return False
    if prior is None:
        return True
    if not _state_is_reachable(prior):
        return False
    if not _same_authority_identity(prior, candidate):
        return False
    if candidate.last_received_monotonic_ns < prior.last_received_monotonic_ns:
        return False
    if candidate.correction_epoch < prior.correction_epoch:
        return False
    if candidate.correction_epoch > prior.correction_epoch:
        return True
    if (
        candidate.correction_lineage_sha256
        != prior.correction_lineage_sha256
        or candidate.revision < prior.revision
    ):
        return False
    if candidate.revision == prior.revision:
        return (
            candidate.last_event_semantic_sha256
            == prior.last_event_semantic_sha256
            and candidate.last_provider_event_id
            == prior.last_provider_event_id
            and _same_score_coordinates(prior, candidate)
        )
    return _ordinary_score_transition_reachable(prior, candidate)


_QUARANTINE_REASONS = frozenset(
    {
        ConsensusReason.PRIMARY_BLOCKED,
        ConsensusReason.PRIMARY_QUARANTINED,
        ConsensusReason.SOURCE_CONFIGURATION_MISMATCH,
        ConsensusReason.STATE_MISMATCH,
    }
)


def _observation_barrier_monotonic_ns(
    observations: Mapping[str, TennisState | None],
) -> int:
    clocks = tuple(
        max(
            observation.last_received_monotonic_ns,
            (
                observation.blocked_received_monotonic_ns
                if observation.blocked_received_monotonic_ns is not None
                else observation.last_received_monotonic_ns
            ),
        )
        for observation in observations.values()
        if observation is not None
    )
    if not clocks:
        raise ValueError("quarantine_barrier_monotonic_ns")
    return max(clocks)


def _quarantine_barrier_for_abstention(
    state: ScoreConsensusState,
    reason: ConsensusReason,
    observations: Mapping[str, TennisState | None],
) -> int | None:
    if not state.quarantined and reason not in _QUARANTINE_REASONS:
        return state.quarantine_barrier_monotonic_ns
    if not any(
        observation is not None for observation in observations.values()
    ):
        if state.quarantine_barrier_monotonic_ns is None:
            raise ValueError("quarantine_barrier_monotonic_ns")
        return state.quarantine_barrier_monotonic_ns
    observed_barrier = _observation_barrier_monotonic_ns(observations)
    prior_barrier = state.quarantine_barrier_monotonic_ns
    if prior_barrier is None:
        return observed_barrier
    return max(prior_barrier, observed_barrier)


def _accepted_support_is_after_barrier(
    result: ScoreConsensusResult,
    observations: Mapping[str, TennisState | None],
    barrier_monotonic_ns: int,
) -> bool:
    if not result.supporting_source_ids:
        return False
    for source_id in result.supporting_source_ids:
        observation = observations.get(source_id)
        if (
            observation is None
            or observation.last_received_monotonic_ns
            <= barrier_monotonic_ns
        ):
            return False
    return True


def apply_score_consensus(
    state: ScoreConsensusState,
    policy: ScoreConsensusPolicy,
    observations: Mapping[str, TennisState | None],
    *,
    now_monotonic_ns: int,
) -> tuple[ScoreConsensusState, ScoreConsensusResult]:
    """Advance the sole authoritative consensus state through a legal barrier."""
    if type(state) is not ScoreConsensusState:
        raise TypeError("state")
    result = reduce_score_consensus(
        policy,
        observations,
        now_monotonic_ns=now_monotonic_ns,
    )
    candidate = result.accepted_state
    if candidate is None:
        quarantined = (
            state.quarantined or result.reason in _QUARANTINE_REASONS
        )
        return (
            ScoreConsensusState(
                accepted_state=state.accepted_state,
                quarantined=quarantined,
                consensus_epoch=state.consensus_epoch,
                quarantine_barrier_monotonic_ns=(
                    _quarantine_barrier_for_abstention(
                        state,
                        result.reason,
                        observations,
                    )
                    if quarantined
                    else None
                ),
            ),
            result,
        )
    prior = state.accepted_state
    if (
        state.quarantine_barrier_monotonic_ns is not None
        and not _accepted_support_is_after_barrier(
            result,
            observations,
            state.quarantine_barrier_monotonic_ns,
        )
    ):
        return (
            ScoreConsensusState(
                accepted_state=state.accepted_state,
                quarantined=True,
                consensus_epoch=state.consensus_epoch,
                quarantine_barrier_monotonic_ns=(
                    _quarantine_barrier_for_abstention(
                        state,
                        ConsensusReason.PRIMARY_QUARANTINED,
                        observations,
                    )
                ),
            ),
            _abstain(ConsensusReason.PRIMARY_QUARANTINED),
        )
    if not _transition_is_legal(prior, candidate):
        observed_barrier = _observation_barrier_monotonic_ns(observations)
        prior_barrier = state.quarantine_barrier_monotonic_ns
        return (
            ScoreConsensusState(
                accepted_state=prior,
                quarantined=True,
                consensus_epoch=state.consensus_epoch,
                quarantine_barrier_monotonic_ns=(
                    observed_barrier
                    if prior_barrier is None
                    else max(prior_barrier, observed_barrier)
                ),
            ),
            _abstain(ConsensusReason.TRANSITION_REGRESSION),
        )
    starts_new_epoch = state.quarantined or (
        prior is not None
        and candidate.correction_epoch > prior.correction_epoch
    )
    return (
        ScoreConsensusState(
            accepted_state=candidate,
            quarantined=False,
            consensus_epoch=(
                state.consensus_epoch + (1 if starts_new_epoch else 0)
            ),
            quarantine_barrier_monotonic_ns=None,
        ),
        result,
    )
