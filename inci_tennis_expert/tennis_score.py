from __future__ import annotations

from dataclasses import fields
from hashlib import sha256
from typing import Final

from .contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ProviderLifecycle,
    ProviderLifecycleKind,
    ProviderPoint,
    ProviderSnapshot,
    ScoreValue,
    SetScore,
    TennisState,
    TennisStateInvariantError,
    TennisTransitionError,
    TennisTransitionReason,
    TennisTransitionResult,
    TerminationKind,
    TransitionDisposition,
    canonical_expert_bytes,
)


_EVENT_DOMAIN: Final[bytes] = (
    b"inci-tennis-provider-event-semantic-v1\x00"
)
_INITIAL_LINEAGE_DOMAIN: Final[bytes] = (
    b"inci-tennis-initial-lineage-v1\x00"
)
_CORRECTION_LINEAGE_DOMAIN: Final[bytes] = (
    b"inci-tennis-correction-lineage-v1\x00"
)
_SUPPORTED_FORMATS: Final[frozenset[MatchFormat]] = frozenset(
    {
        MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS,
    }
)


def _opposite(side: PlayerSide) -> PlayerSide:
    if side is PlayerSide.HOME:
        return PlayerSide.AWAY
    return PlayerSide.HOME


def _sets_required(match_format: MatchFormat) -> int:
    if match_format is MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS:
        return 2
    return 3


def _event_semantic(
    event: ProviderSnapshot | ProviderPoint | ProviderLifecycle,
) -> tuple[bytes, str]:
    if type(event) is ProviderSnapshot:
        event_kind = "snapshot"
    elif type(event) is ProviderPoint:
        event_kind = "point"
    else:
        event_kind = "lifecycle"
    event_fields: dict[str, object] = {}
    for field in fields(event):
        if field.name != "received_monotonic_ns":
            event_fields[field.name] = getattr(event, field.name)
    semantic_value = {
        "schema": "provider_event_semantic_v1",
        "event_kind": event_kind,
        "event": event_fields,
    }
    semantic_bytes = canonical_expert_bytes(semantic_value)
    digest = sha256(_EVENT_DOMAIN + semantic_bytes).hexdigest()
    return semantic_bytes, digest


def _completed_set_winner(set_score: SetScore) -> PlayerSide | None:
    home = set_score.games_home
    away = set_score.games_away
    home_tiebreak = set_score.tiebreak_points_home
    away_tiebreak = set_score.tiebreak_points_away
    if home_tiebreak is None:
        if (home == 6 and 0 <= away <= 4) or (home == 7 and away == 5):
            return PlayerSide.HOME
        if (away == 6 and 0 <= home <= 4) or (away == 7 and home == 5):
            return PlayerSide.AWAY
        return None
    if away_tiebreak is None:
        return None
    if (home, away) == (7, 6):
        if home_tiebreak >= 7 and home_tiebreak - away_tiebreak >= 2:
            return PlayerSide.HOME
        return None
    if (home, away) == (6, 7):
        if away_tiebreak >= 7 and away_tiebreak - home_tiebreak >= 2:
            return PlayerSide.AWAY
        return None
    return None


def _tiebreak_won(home: int, away: int) -> bool:
    return (
        home >= 7
        and home - away >= 2
        or away >= 7
        and away - home >= 2
    )


def _normal_set_won(home: int, away: int) -> bool:
    return (
        home >= 6
        and home - away >= 2
        or away >= 6
        and away - home >= 2
    )


def _tiebreak_server(
    first: PlayerSide,
    completed_points: int,
) -> PlayerSide:
    if completed_points == 0:
        return first
    if ((completed_points - 1) // 2) % 2 == 0:
        return _opposite(first)
    return first


def _score_is_zero(value: ProviderSnapshot | TennisState) -> bool:
    return (
        value.completed_sets == ()
        and value.games_home == 0
        and value.games_away == 0
        and value.points_home is ScoreValue.LOVE
        and value.points_away is ScoreValue.LOVE
        and value.in_tiebreak is False
        and value.tiebreak_points_home == 0
        and value.tiebreak_points_away == 0
        and value.tiebreak_first_server is None
    )


def _completed_prefix(
    value: ProviderSnapshot | TennisState,
) -> tuple[bool, PlayerSide | None]:
    required = _sets_required(value.match_format)
    home_wins = 0
    away_wins = 0
    clinching_side: PlayerSide | None = None
    for index, set_score in enumerate(value.completed_sets):
        winner = _completed_set_winner(set_score)
        if winner is None or clinching_side is not None:
            return False, None
        if winner is PlayerSide.HOME:
            home_wins += 1
            if home_wins == required:
                clinching_side = PlayerSide.HOME
        else:
            away_wins += 1
            if away_wins == required:
                clinching_side = PlayerSide.AWAY
        if clinching_side is not None and index != len(value.completed_sets) - 1:
            return False, None
    return True, clinching_side


def _incomplete_score_is_reachable(
    value: ProviderSnapshot | TennisState,
    *,
    active: bool,
) -> bool:
    prefix_valid, clinching_side = _completed_prefix(value)
    if not prefix_valid or clinching_side is not None:
        return False
    if value.in_tiebreak:
        if (
            value.games_home != 6
            or value.games_away != 6
            or value.points_home is not ScoreValue.LOVE
            or value.points_away is not ScoreValue.LOVE
            or value.tiebreak_first_server is None
            or _tiebreak_won(
                value.tiebreak_points_home,
                value.tiebreak_points_away,
            )
        ):
            return False
        if active:
            completed_points = (
                value.tiebreak_points_home + value.tiebreak_points_away
            )
            if value.server_for_next_point is not _tiebreak_server(
                value.tiebreak_first_server,
                completed_points,
            ):
                return False
        elif value.server_for_next_point is not None:
            return False
        return True
    if (
        value.games_home > 6
        or value.games_away > 6
        or (value.games_home == 6 and value.games_away == 6)
        or _normal_set_won(value.games_home, value.games_away)
    ):
        return False
    if active:
        return value.server_for_next_point is not None
    return value.server_for_next_point is None


def _reachable(value: ProviderSnapshot | TennisState) -> bool:
    if value.match_format not in _SUPPORTED_FORMATS:
        return False
    if value.status is MatchStatus.SCHEDULED:
        return (
            value.termination_kind is TerminationKind.NONE
            and value.winner is None
            and value.retired_side is None
            and value.server_for_next_point is None
            and _score_is_zero(value)
        )
    if value.status in {MatchStatus.LIVE, MatchStatus.SUSPENDED}:
        return (
            value.termination_kind is TerminationKind.NONE
            and value.winner is None
            and value.retired_side is None
            and _incomplete_score_is_reachable(value, active=True)
        )
    if value.status is MatchStatus.CANCELLED:
        return (
            value.termination_kind is TerminationKind.CANCELLATION
            and value.winner is None
            and value.retired_side is None
            and _incomplete_score_is_reachable(value, active=False)
        )
    if value.status is not MatchStatus.ENDED:
        return False
    if value.termination_kind is TerminationKind.WALKOVER:
        return (
            value.winner is not None
            and value.retired_side is None
            and value.server_for_next_point is None
            and _score_is_zero(value)
        )
    if value.termination_kind is TerminationKind.RETIREMENT:
        return (
            value.winner is not None
            and value.retired_side is not None
            and value.winner is not value.retired_side
            and _incomplete_score_is_reachable(value, active=False)
        )
    if value.termination_kind is not TerminationKind.NATURAL:
        return False
    prefix_valid, clinching_side = _completed_prefix(value)
    return (
        prefix_valid
        and clinching_side is not None
        and value.winner is clinching_side
        and value.retired_side is None
        and value.server_for_next_point is None
        and value.games_home == 0
        and value.games_away == 0
        and value.points_home is ScoreValue.LOVE
        and value.points_away is ScoreValue.LOVE
        and value.in_tiebreak is False
        and value.tiebreak_points_home == 0
        and value.tiebreak_points_away == 0
        and value.tiebreak_first_server is None
    )


def validate_tennis_state(state: TennisState) -> None:
    if type(state) is not TennisState:
        raise TypeError("state")
    if not _reachable(state):
        raise TennisStateInvariantError()


def _state_values(state: TennisState) -> dict[str, object]:
    return {
        field.name: getattr(state, field.name)
        for field in fields(state)
    }


def _copy_state(
    state: TennisState,
    **changes: object,
) -> TennisState:
    values = _state_values(state)
    values.update(changes)
    return TennisState(**values)  # type: ignore[arg-type]


def _state_from_snapshot(
    snapshot: ProviderSnapshot,
    *,
    event_digest: str,
    lineage_digest: str,
) -> TennisState:
    return TennisState(
        provider_source_id=snapshot.provider_source_id,
        revision_domain_id=snapshot.revision_domain_id,
        source_lineage_sha256=snapshot.source_lineage_sha256,
        provider_match_id=snapshot.provider_match_id,
        home_player_id=snapshot.home_player_id,
        away_player_id=snapshot.away_player_id,
        scheduled_start_wall_ns=snapshot.scheduled_start_wall_ns,
        match_format=snapshot.match_format,
        status=snapshot.status,
        termination_kind=snapshot.termination_kind,
        winner=snapshot.winner,
        retired_side=snapshot.retired_side,
        completed_sets=snapshot.completed_sets,
        games_home=snapshot.games_home,
        games_away=snapshot.games_away,
        points_home=snapshot.points_home,
        points_away=snapshot.points_away,
        in_tiebreak=snapshot.in_tiebreak,
        tiebreak_points_home=snapshot.tiebreak_points_home,
        tiebreak_points_away=snapshot.tiebreak_points_away,
        tiebreak_first_server=snapshot.tiebreak_first_server,
        server_for_next_point=snapshot.server_for_next_point,
        correction_epoch=snapshot.correction_epoch,
        revision=snapshot.revision,
        snapshot_complete=True,
        last_provider_event_id=snapshot.provider_event_id,
        last_event_semantic_sha256=event_digest,
        correction_lineage_sha256=lineage_digest,
        last_source_wall_ns=snapshot.source_wall_ns,
        last_source_generated_wall_ns=snapshot.source_generated_wall_ns,
        last_received_monotonic_ns=snapshot.received_monotonic_ns,
        last_clock_uncertainty_ns=snapshot.clock_uncertainty_ns,
        block_reason=None,
        expected_revision=None,
        observed_revision=None,
        blocked_event_semantic_sha256=None,
        blocked_received_monotonic_ns=None,
    )


def state_from_snapshot(snapshot: ProviderSnapshot) -> TennisState:
    if type(snapshot) is not ProviderSnapshot:
        raise TypeError("snapshot")
    semantic_bytes, event_digest = _event_semantic(snapshot)
    if snapshot.match_format is MatchFormat.UNSUPPORTED:
        raise TennisTransitionError(
            TennisTransitionReason.UNSUPPORTED_FORMAT
        )
    if snapshot.snapshot_complete is not True or not _reachable(snapshot):
        raise TennisTransitionError(TennisTransitionReason.SNAPSHOT_INVALID)
    lineage_digest = sha256(
        _INITIAL_LINEAGE_DOMAIN + semantic_bytes
    ).hexdigest()
    return _state_from_snapshot(
        snapshot,
        event_digest=event_digest,
        lineage_digest=lineage_digest,
    )


def _result(
    state: TennisState,
    disposition: TransitionDisposition,
    reason: TennisTransitionReason,
    event_digest: str,
) -> TennisTransitionResult:
    return TennisTransitionResult(
        state=state,
        disposition=disposition,
        reason=reason,
        event_semantic_sha256=event_digest,
    )


def _block_state(
    state: TennisState,
    *,
    reason: TennisTransitionReason,
    event_digest: str,
    received_monotonic_ns: int,
    expected_revision: int | None = None,
    observed_revision: int | None = None,
) -> TennisState:
    return _copy_state(
        state,
        block_reason=reason,
        expected_revision=expected_revision,
        observed_revision=observed_revision,
        blocked_event_semantic_sha256=event_digest,
        blocked_received_monotonic_ns=received_monotonic_ns,
    )


def _blocked(
    state: TennisState,
    event: ProviderPoint | ProviderLifecycle,
    event_digest: str,
    reason: TennisTransitionReason,
    *,
    expected_revision: int | None = None,
    observed_revision: int | None = None,
) -> TennisTransitionResult:
    blocked_state = _block_state(
        state,
        reason=reason,
        event_digest=event_digest,
        received_monotonic_ns=event.received_monotonic_ns,
        expected_revision=expected_revision,
        observed_revision=observed_revision,
    )
    return _result(
        blocked_state,
        TransitionDisposition.BLOCKED,
        reason,
        event_digest,
    )


def _incremental_precheck(
    state: TennisState,
    event: ProviderPoint | ProviderLifecycle,
    event_digest: str,
) -> TennisTransitionResult | None:
    if state.block_reason is not None:
        return _result(
            state,
            TransitionDisposition.BLOCKED,
            TennisTransitionReason.CORRECTION_REQUIRED,
            event_digest,
        )
    if (
        event.provider_match_id != state.provider_match_id
        or event.home_player_id != state.home_player_id
        or event.away_player_id != state.away_player_id
        or event.scheduled_start_wall_ns != state.scheduled_start_wall_ns
    ):
        return _blocked(
            state,
            event,
            event_digest,
            TennisTransitionReason.IDENTITY_MISMATCH,
        )
    if (
        event.provider_source_id != state.provider_source_id
        or event.source_lineage_sha256 != state.source_lineage_sha256
    ):
        return _blocked(
            state,
            event,
            event_digest,
            TennisTransitionReason.SOURCE_LINEAGE_MISMATCH,
        )
    if event.revision_domain_id != state.revision_domain_id:
        return _blocked(
            state,
            event,
            event_digest,
            TennisTransitionReason.REVISION_DOMAIN_MISMATCH,
        )
    if event.match_format is not state.match_format:
        return _blocked(
            state,
            event,
            event_digest,
            TennisTransitionReason.FORMAT_MISMATCH,
        )
    if event.received_monotonic_ns < state.last_received_monotonic_ns:
        return _blocked(
            state,
            event,
            event_digest,
            TennisTransitionReason.RECEIVE_TIME_REGRESSION,
        )
    if event.correction_epoch < state.correction_epoch:
        return _blocked(
            state,
            event,
            event_digest,
            TennisTransitionReason.CORRECTION_EPOCH_STALE,
        )
    if event.correction_epoch > state.correction_epoch:
        return _blocked(
            state,
            event,
            event_digest,
            TennisTransitionReason.CORRECTION_EPOCH_AHEAD,
        )
    if event.revision == state.revision:
        if event_digest == state.last_event_semantic_sha256:
            return _result(
                state,
                TransitionDisposition.DUPLICATE,
                TennisTransitionReason.EXACT_DUPLICATE,
                event_digest,
            )
        return _blocked(
            state,
            event,
            event_digest,
            TennisTransitionReason.PROVIDER_EVENT_CONFLICT,
        )
    if event.revision < state.revision:
        return _blocked(
            state,
            event,
            event_digest,
            TennisTransitionReason.PROVIDER_EVENT_STALE,
        )
    if event.revision > state.revision + 1:
        return _blocked(
            state,
            event,
            event_digest,
            TennisTransitionReason.PROVIDER_EVENT_GAP,
            expected_revision=state.revision + 1,
            observed_revision=event.revision,
        )
    return None


def _applied_state(
    state: TennisState,
    event: ProviderPoint | ProviderLifecycle,
    event_digest: str,
    **changes: object,
) -> TennisState:
    changes.update(
        {
            "revision": event.revision,
            "last_provider_event_id": event.provider_event_id,
            "last_event_semantic_sha256": event_digest,
            "last_source_wall_ns": event.source_wall_ns,
            "last_source_generated_wall_ns": (
                event.source_generated_wall_ns
            ),
            "last_received_monotonic_ns": event.received_monotonic_ns,
            "last_clock_uncertainty_ns": event.clock_uncertainty_ns,
            "block_reason": None,
            "expected_revision": None,
            "observed_revision": None,
            "blocked_event_semantic_sha256": None,
            "blocked_received_monotonic_ns": None,
        }
    )
    return _copy_state(state, **changes)


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


def _completed_set_changes(
    state: TennisState,
    set_score: SetScore,
    next_server: PlayerSide,
) -> dict[str, object]:
    completed_sets = (*state.completed_sets, set_score)
    home_wins, away_wins = _set_wins(completed_sets)
    required = _sets_required(state.match_format)
    winner: PlayerSide | None = None
    if home_wins == required:
        winner = PlayerSide.HOME
    elif away_wins == required:
        winner = PlayerSide.AWAY
    terminal = winner is not None
    return {
        "status": MatchStatus.ENDED if terminal else MatchStatus.LIVE,
        "termination_kind": (
            TerminationKind.NATURAL if terminal else TerminationKind.NONE
        ),
        "winner": winner,
        "retired_side": None,
        "completed_sets": completed_sets,
        "games_home": 0,
        "games_away": 0,
        "points_home": ScoreValue.LOVE,
        "points_away": ScoreValue.LOVE,
        "in_tiebreak": False,
        "tiebreak_points_home": 0,
        "tiebreak_points_away": 0,
        "tiebreak_first_server": None,
        "server_for_next_point": None if terminal else next_server,
    }


def _advance_score(
    state: TennisState,
    winner: PlayerSide,
) -> dict[str, object]:
    if state.in_tiebreak:
        home = state.tiebreak_points_home
        away = state.tiebreak_points_away
        if winner is PlayerSide.HOME:
            home += 1
        else:
            away += 1
        if _tiebreak_won(home, away):
            assert state.tiebreak_first_server is not None
            set_score = SetScore(
                7 if winner is PlayerSide.HOME else 6,
                6 if winner is PlayerSide.HOME else 7,
                home,
                away,
            )
            return _completed_set_changes(
                state,
                set_score,
                _opposite(state.tiebreak_first_server),
            )
        assert state.tiebreak_first_server is not None
        return {
            "tiebreak_points_home": home,
            "tiebreak_points_away": away,
            "server_for_next_point": _tiebreak_server(
                state.tiebreak_first_server,
                home + away,
            ),
        }

    home_score = state.points_home
    away_score = state.points_away
    own = home_score if winner is PlayerSide.HOME else away_score
    opponent = away_score if winner is PlayerSide.HOME else home_score
    game_won = False
    if own is ScoreValue.ADVANTAGE:
        game_won = True
    elif opponent is ScoreValue.ADVANTAGE:
        own = ScoreValue.FORTY
        opponent = ScoreValue.FORTY
    elif own is ScoreValue.FORTY:
        if opponent is ScoreValue.FORTY:
            own = ScoreValue.ADVANTAGE
        else:
            game_won = True
    elif own is ScoreValue.LOVE:
        own = ScoreValue.FIFTEEN
    elif own is ScoreValue.FIFTEEN:
        own = ScoreValue.THIRTY
    else:
        own = ScoreValue.FORTY

    if not game_won:
        if winner is PlayerSide.HOME:
            return {"points_home": own, "points_away": opponent}
        return {"points_home": opponent, "points_away": own}

    games_home = state.games_home
    games_away = state.games_away
    if winner is PlayerSide.HOME:
        games_home += 1
    else:
        games_away += 1
    assert state.server_for_next_point is not None
    next_server = _opposite(state.server_for_next_point)
    if _normal_set_won(games_home, games_away):
        return _completed_set_changes(
            state,
            SetScore(games_home, games_away, None, None),
            next_server,
        )
    if games_home == 6 and games_away == 6:
        return {
            "games_home": games_home,
            "games_away": games_away,
            "points_home": ScoreValue.LOVE,
            "points_away": ScoreValue.LOVE,
            "in_tiebreak": True,
            "tiebreak_points_home": 0,
            "tiebreak_points_away": 0,
            "tiebreak_first_server": next_server,
            "server_for_next_point": next_server,
        }
    return {
        "games_home": games_home,
        "games_away": games_away,
        "points_home": ScoreValue.LOVE,
        "points_away": ScoreValue.LOVE,
        "server_for_next_point": next_server,
    }


def apply_point(
    state: TennisState,
    point: ProviderPoint,
) -> TennisTransitionResult:
    if type(state) is not TennisState:
        raise TypeError("state")
    if type(point) is not ProviderPoint:
        raise TypeError("point")
    validate_tennis_state(state)
    _, event_digest = _event_semantic(point)
    prechecked = _incremental_precheck(state, point, event_digest)
    if prechecked is not None:
        return prechecked
    if state.status in {MatchStatus.ENDED, MatchStatus.CANCELLED}:
        return _blocked(
            state,
            point,
            event_digest,
            TennisTransitionReason.TERMINAL_ABSORBING,
        )
    if state.status is not MatchStatus.LIVE:
        return _blocked(
            state,
            point,
            event_digest,
            TennisTransitionReason.POINT_WHILE_NOT_LIVE,
        )
    if point.server_before_point is not state.server_for_next_point:
        return _blocked(
            state,
            point,
            event_digest,
            TennisTransitionReason.SERVER_MISMATCH,
        )
    changes = _advance_score(state, point.point_winner)
    applied = _applied_state(
        state,
        point,
        event_digest,
        **changes,
    )
    return _result(
        applied,
        TransitionDisposition.APPLIED,
        TennisTransitionReason.POINT_APPLIED,
        event_digest,
    )


def _lifecycle_payload_valid(
    event: ProviderLifecycle,
    *,
    winner_required: bool = False,
    retired_required: bool = False,
    server_required: bool = False,
) -> bool:
    if winner_required != (event.winner is not None):
        return False
    if retired_required != (event.retired_side is not None):
        return False
    if server_required != (event.server_for_next_point is not None):
        return False
    if (
        winner_required
        and retired_required
        and event.winner is event.retired_side
    ):
        return False
    return True


def apply_lifecycle(
    state: TennisState,
    event: ProviderLifecycle,
) -> TennisTransitionResult:
    if type(state) is not TennisState:
        raise TypeError("state")
    if type(event) is not ProviderLifecycle:
        raise TypeError("event")
    validate_tennis_state(state)
    _, event_digest = _event_semantic(event)
    prechecked = _incremental_precheck(state, event, event_digest)
    if prechecked is not None:
        return prechecked

    terminal = state.status in {MatchStatus.ENDED, MatchStatus.CANCELLED}
    natural_confirmation = (
        state.status is MatchStatus.ENDED
        and state.termination_kind is TerminationKind.NATURAL
        and event.kind
        is ProviderLifecycleKind.NATURAL_END_CONFIRMATION
    )
    if terminal and not natural_confirmation:
        return _blocked(
            state,
            event,
            event_digest,
            TennisTransitionReason.TERMINAL_ABSORBING,
        )
    if natural_confirmation:
        if not (
            event.winner is state.winner
            and event.retired_side is None
            and event.server_for_next_point is None
        ):
            return _blocked(
                state,
                event,
                event_digest,
                TennisTransitionReason.ILLEGAL_LIFECYCLE_TRANSITION,
            )
        applied = _applied_state(state, event, event_digest)
        return _result(
            applied,
            TransitionDisposition.APPLIED,
            TennisTransitionReason.NATURAL_END_CONFIRMED,
            event_digest,
        )

    changes: dict[str, object] | None = None
    compare_server = False
    if (
        state.status is MatchStatus.SCHEDULED
        and event.kind is ProviderLifecycleKind.START
        and _lifecycle_payload_valid(event, server_required=True)
    ):
        changes = {
            "status": MatchStatus.LIVE,
            "server_for_next_point": event.server_for_next_point,
        }
    elif (
        state.status is MatchStatus.LIVE
        and event.kind is ProviderLifecycleKind.SUSPEND
        and _lifecycle_payload_valid(event, server_required=True)
    ):
        compare_server = True
        changes = {"status": MatchStatus.SUSPENDED}
    elif (
        state.status is MatchStatus.SUSPENDED
        and event.kind is ProviderLifecycleKind.RESUME
        and _lifecycle_payload_valid(event, server_required=True)
    ):
        compare_server = True
        changes = {"status": MatchStatus.LIVE}
    elif (
        state.status is MatchStatus.SCHEDULED
        and event.kind is ProviderLifecycleKind.WALKOVER
        and _lifecycle_payload_valid(event, winner_required=True)
    ):
        changes = {
            "status": MatchStatus.ENDED,
            "termination_kind": TerminationKind.WALKOVER,
            "winner": event.winner,
            "retired_side": None,
            "server_for_next_point": None,
        }
    elif (
        state.status in {MatchStatus.LIVE, MatchStatus.SUSPENDED}
        and event.kind is ProviderLifecycleKind.RETIREMENT
        and _lifecycle_payload_valid(
            event,
            winner_required=True,
            retired_required=True,
        )
    ):
        changes = {
            "status": MatchStatus.ENDED,
            "termination_kind": TerminationKind.RETIREMENT,
            "winner": event.winner,
            "retired_side": event.retired_side,
            "server_for_next_point": None,
        }
    elif (
        state.status
        in {
            MatchStatus.SCHEDULED,
            MatchStatus.LIVE,
            MatchStatus.SUSPENDED,
        }
        and event.kind is ProviderLifecycleKind.CANCEL
        and _lifecycle_payload_valid(event)
    ):
        changes = {
            "status": MatchStatus.CANCELLED,
            "termination_kind": TerminationKind.CANCELLATION,
            "winner": None,
            "retired_side": None,
            "server_for_next_point": None,
        }
    if changes is None:
        return _blocked(
            state,
            event,
            event_digest,
            TennisTransitionReason.ILLEGAL_LIFECYCLE_TRANSITION,
        )
    if (
        compare_server
        and event.server_for_next_point is not state.server_for_next_point
    ):
        return _blocked(
            state,
            event,
            event_digest,
            TennisTransitionReason.SERVER_MISMATCH,
        )
    applied = _applied_state(
        state,
        event,
        event_digest,
        **changes,
    )
    return _result(
        applied,
        TransitionDisposition.APPLIED,
        TennisTransitionReason.LIFECYCLE_APPLIED,
        event_digest,
    )


def _correction_rejected(
    state: TennisState,
    replacement: ProviderSnapshot,
    event_digest: str,
    reason: TennisTransitionReason,
) -> TennisTransitionResult:
    if state.block_reason is not None:
        rejected_state = state
    else:
        rejected_state = _block_state(
            state,
            reason=reason,
            event_digest=event_digest,
            received_monotonic_ns=replacement.received_monotonic_ns,
        )
    return _result(
        rejected_state,
        TransitionDisposition.BLOCKED,
        reason,
        event_digest,
    )


def apply_correction(
    state: TennisState,
    replacement: ProviderSnapshot,
) -> TennisTransitionResult:
    if type(state) is not TennisState:
        raise TypeError("state")
    if type(replacement) is not ProviderSnapshot:
        raise TypeError("replacement")
    validate_tennis_state(state)
    semantic_bytes, event_digest = _event_semantic(replacement)
    if (
        replacement.provider_match_id != state.provider_match_id
        or replacement.home_player_id != state.home_player_id
        or replacement.away_player_id != state.away_player_id
        or replacement.scheduled_start_wall_ns
        != state.scheduled_start_wall_ns
    ):
        return _correction_rejected(
            state,
            replacement,
            event_digest,
            TennisTransitionReason.IDENTITY_MISMATCH,
        )
    if (
        replacement.provider_source_id != state.provider_source_id
        or replacement.source_lineage_sha256
        != state.source_lineage_sha256
    ):
        return _correction_rejected(
            state,
            replacement,
            event_digest,
            TennisTransitionReason.SOURCE_LINEAGE_MISMATCH,
        )
    if replacement.revision_domain_id != state.revision_domain_id:
        return _correction_rejected(
            state,
            replacement,
            event_digest,
            TennisTransitionReason.REVISION_DOMAIN_MISMATCH,
        )
    if replacement.match_format is MatchFormat.UNSUPPORTED:
        return _correction_rejected(
            state,
            replacement,
            event_digest,
            TennisTransitionReason.UNSUPPORTED_FORMAT,
        )
    if replacement.match_format is not state.match_format:
        return _correction_rejected(
            state,
            replacement,
            event_digest,
            TennisTransitionReason.FORMAT_MISMATCH,
        )
    minimum_receipt = state.last_received_monotonic_ns
    if (
        state.blocked_received_monotonic_ns is not None
        and state.blocked_received_monotonic_ns > minimum_receipt
    ):
        minimum_receipt = state.blocked_received_monotonic_ns
    if replacement.received_monotonic_ns < minimum_receipt:
        return _correction_rejected(
            state,
            replacement,
            event_digest,
            TennisTransitionReason.RECEIVE_TIME_REGRESSION,
        )
    if (
        replacement.correction_epoch == state.correction_epoch
        and replacement.revision == state.revision
        and event_digest == state.last_event_semantic_sha256
    ):
        return _result(
            state,
            TransitionDisposition.DUPLICATE,
            TennisTransitionReason.EXACT_DUPLICATE,
            event_digest,
        )
    if replacement.correction_epoch <= state.correction_epoch:
        return _correction_rejected(
            state,
            replacement,
            event_digest,
            TennisTransitionReason.CORRECTION_EPOCH_NOT_NEWER,
        )
    if (
        replacement.snapshot_complete is not True
        or not _reachable(replacement)
    ):
        return _correction_rejected(
            state,
            replacement,
            event_digest,
            TennisTransitionReason.CORRECTION_SNAPSHOT_INVALID,
        )
    lineage_digest = sha256(
        _CORRECTION_LINEAGE_DOMAIN
        + bytes.fromhex(state.correction_lineage_sha256)
        + semantic_bytes
    ).hexdigest()
    applied = _state_from_snapshot(
        replacement,
        event_digest=event_digest,
        lineage_digest=lineage_digest,
    )
    return _result(
        applied,
        TransitionDisposition.APPLIED,
        TennisTransitionReason.CORRECTION_APPLIED,
        event_digest,
    )
