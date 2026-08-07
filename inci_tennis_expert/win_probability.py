from __future__ import annotations

from decimal import Decimal, localcontext
from functools import lru_cache
from typing import Final

from .contracts import (
    DecisionReason,
    FairValueEstimate,
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ScoreValue,
    TennisState,
    TennisStateInvariantError,
    _exact,
    expert_contract_sha256,
)
from .prematch_model import PrematchPrior
from .tennis_score import validate_tennis_state


LIVE_WIN_PROBABILITY_MODEL_SHA256: Final[str] = expert_contract_sha256(
    {
        "schema": "live_win_probability_model_v1",
        "method": "exact_recursive_advantage_tb7_all_sets",
    }
)
_MINIMUM_LIVE_EFFECTIVE_SAMPLE_SIZE: Final[Decimal] = Decimal("12")
_DECIMAL_PLACES: Final[Decimal] = Decimal("0.000000000001")
_SUPPORTED_FORMATS: Final[frozenset[MatchFormat]] = frozenset(
    {
        MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS,
    }
)


def _quantize(value: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return value.quantize(_DECIMAL_PLACES)


def _clamp_probability(value: Decimal) -> Decimal:
    if value < Decimal("0"):
        return Decimal("0")
    if value > Decimal("1"):
        return Decimal("1")
    return _quantize(value)


def _opposite(side: PlayerSide) -> PlayerSide:
    if side is PlayerSide.HOME:
        return PlayerSide.AWAY
    return PlayerSide.HOME


def _sets_required(match_format: MatchFormat) -> int:
    if match_format is MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS:
        return 2
    return 3


def _completed_set_counts(state: TennisState) -> tuple[int, int]:
    home = 0
    away = 0
    for set_score in state.completed_sets:
        if set_score.games_home > set_score.games_away:
            home += 1
        else:
            away += 1
    return home, away


def _score_points(value: ScoreValue) -> int:
    if value is ScoreValue.LOVE:
        return 0
    if value is ScoreValue.FIFTEEN:
        return 1
    if value is ScoreValue.THIRTY:
        return 2
    if value is ScoreValue.FORTY:
        return 3
    return 4


def _normal_set_won(home: int, away: int) -> bool:
    return (
        home >= 6
        and home - away >= 2
        or away >= 6
        and away - home >= 2
    )


def _tiebreak_won(home: int, away: int) -> bool:
    return (
        home >= 7
        and home - away >= 2
        or away >= 7
        and away - home >= 2
    )


def _tiebreak_next_server(
    first_server: PlayerSide,
    completed_points: int,
) -> PlayerSide:
    if completed_points == 0:
        return first_server
    if ((completed_points - 1) // 2) % 2 == 0:
        return _opposite(first_server)
    return first_server


def _tail_next_server(
    first_server: PlayerSide,
    phase: int,
) -> PlayerSide:
    if phase in (0, 3):
        return first_server
    return _opposite(first_server)


def _home_point_probability(
    server: PlayerSide,
    home_serve_probability: Decimal,
    away_serve_probability: Decimal,
) -> Decimal:
    if server is PlayerSide.HOME:
        return home_serve_probability
    return Decimal("1") - away_serve_probability


@lru_cache(maxsize=None)
def _game_probability(
    points_home: int,
    points_away: int,
    server: PlayerSide,
    home_serve_probability: Decimal,
    away_serve_probability: Decimal,
) -> Decimal:
    q = _home_point_probability(
        server,
        home_serve_probability,
        away_serve_probability,
    )
    if points_home >= 4 and points_home - points_away >= 2:
        return Decimal("1")
    if points_away >= 4 and points_away - points_home >= 2:
        return Decimal("0")
    if points_home >= 3 and points_away >= 3:
        q_loss = Decimal("1") - q
        with localcontext() as context:
            context.prec = 50
            deuce = (q * q) / (q * q + q_loss * q_loss)
        if points_home == points_away:
            return _clamp_probability(deuce)
        if points_home > points_away:
            return _clamp_probability(q + q_loss * deuce)
        return _clamp_probability(q * deuce)
    return _clamp_probability(
        q
        * _game_probability(
            points_home + 1,
            points_away,
            server,
            home_serve_probability,
            away_serve_probability,
        )
        + (Decimal("1") - q)
        * _game_probability(
            points_home,
            points_away + 1,
            server,
            home_serve_probability,
            away_serve_probability,
        )
    )


def _tail_states() -> tuple[tuple[int, int], ...]:
    return (
        (0, 0),
        (0, 2),
        (1, 1),
        (1, 3),
        (-1, 1),
        (-1, 3),
    )


def _solve_linear_system(
    matrix: list[list[Decimal]],
    vector: list[Decimal],
) -> tuple[Decimal, ...]:
    size = len(vector)
    with localcontext() as context:
        context.prec = 60
        for pivot_index in range(size):
            pivot_row = pivot_index
            while (
                pivot_row < size
                and matrix[pivot_row][pivot_index] == Decimal("0")
            ):
                pivot_row += 1
            if pivot_row == size:
                raise ArithmeticError("singular_tiebreak_tail")
            if pivot_row != pivot_index:
                matrix[pivot_index], matrix[pivot_row] = (
                    matrix[pivot_row],
                    matrix[pivot_index],
                )
                vector[pivot_index], vector[pivot_row] = (
                    vector[pivot_row],
                    vector[pivot_index],
                )
            pivot = matrix[pivot_index][pivot_index]
            for column in range(pivot_index, size):
                matrix[pivot_index][column] /= pivot
            vector[pivot_index] /= pivot
            for row in range(size):
                if row == pivot_index:
                    continue
                factor = matrix[row][pivot_index]
                if factor == Decimal("0"):
                    continue
                for column in range(pivot_index, size):
                    matrix[row][column] -= factor * matrix[pivot_index][column]
                vector[row] -= factor * vector[pivot_index]
    return tuple(_clamp_probability(value) for value in vector)


@lru_cache(maxsize=None)
def _tiebreak_tail_probability(
    diff: int,
    phase: int,
    first_server: PlayerSide,
    home_serve_probability: Decimal,
    away_serve_probability: Decimal,
) -> Decimal:
    states = _tail_states()
    indexes = {state: index for index, state in enumerate(states)}
    size = len(states)
    matrix = [
        [Decimal("0") for _ in range(size)]
        for _ in range(size)
    ]
    vector = [Decimal("0") for _ in range(size)]
    for state, row in indexes.items():
        state_diff, state_phase = state
        matrix[row][row] = Decimal("1")
        server = _tail_next_server(first_server, state_phase)
        q = _home_point_probability(
            server,
            home_serve_probability,
            away_serve_probability,
        )
        next_phase = (state_phase + 1) % 4
        for probability, next_diff in (
            (q, state_diff + 1),
            (Decimal("1") - q, state_diff - 1),
        ):
            if next_diff >= 2:
                vector[row] += probability
            elif next_diff <= -2:
                continue
            else:
                matrix[row][indexes[(next_diff, next_phase)]] -= probability
    solution = _solve_linear_system(matrix, vector)
    return solution[indexes[(diff, phase)]]


@lru_cache(maxsize=None)
def _tiebreak_probability(
    points_home: int,
    points_away: int,
    first_server: PlayerSide,
    home_serve_probability: Decimal,
    away_serve_probability: Decimal,
) -> Decimal:
    if _tiebreak_won(points_home, points_away):
        if points_home > points_away:
            return Decimal("1")
        return Decimal("0")
    if points_home >= 6 and points_away >= 6:
        return _tiebreak_tail_probability(
            points_home - points_away,
            (points_home + points_away) % 4,
            first_server,
            home_serve_probability,
            away_serve_probability,
        )
    server = _tiebreak_next_server(
        first_server,
        points_home + points_away,
    )
    q = _home_point_probability(
        server,
        home_serve_probability,
        away_serve_probability,
    )
    return _clamp_probability(
        q
        * _tiebreak_probability(
            points_home + 1,
            points_away,
            first_server,
            home_serve_probability,
            away_serve_probability,
        )
        + (Decimal("1") - q)
        * _tiebreak_probability(
            points_home,
            points_away + 1,
            first_server,
            home_serve_probability,
            away_serve_probability,
        )
    )


@lru_cache(maxsize=None)
def _match_probability(
    sets_home: int,
    sets_away: int,
    games_home: int,
    games_away: int,
    points_home: int,
    points_away: int,
    in_tiebreak: bool,
    tiebreak_points_home: int,
    tiebreak_points_away: int,
    tiebreak_first_server: PlayerSide | None,
    server: PlayerSide,
    sets_required: int,
    home_serve_probability: Decimal,
    away_serve_probability: Decimal,
) -> Decimal:
    if sets_home >= sets_required:
        return Decimal("1")
    if sets_away >= sets_required:
        return Decimal("0")
    if in_tiebreak:
        assert tiebreak_first_server is not None
        tiebreak = _tiebreak_probability(
            tiebreak_points_home,
            tiebreak_points_away,
            tiebreak_first_server,
            home_serve_probability,
            away_serve_probability,
        )
        next_server = _opposite(tiebreak_first_server)
        return _clamp_probability(
            tiebreak
            * _match_probability(
                sets_home + 1,
                sets_away,
                0,
                0,
                0,
                0,
                False,
                0,
                0,
                None,
                next_server,
                sets_required,
                home_serve_probability,
                away_serve_probability,
            )
            + (Decimal("1") - tiebreak)
            * _match_probability(
                sets_home,
                sets_away + 1,
                0,
                0,
                0,
                0,
                False,
                0,
                0,
                None,
                next_server,
                sets_required,
                home_serve_probability,
                away_serve_probability,
            )
        )

    game = _game_probability(
        points_home,
        points_away,
        server,
        home_serve_probability,
        away_serve_probability,
    )
    next_server = _opposite(server)
    home_games_after = games_home + 1
    away_games_after = games_away + 1
    if _normal_set_won(home_games_after, games_away):
        home_after = _match_probability(
            sets_home + 1,
            sets_away,
            0,
            0,
            0,
            0,
            False,
            0,
            0,
            None,
            next_server,
            sets_required,
            home_serve_probability,
            away_serve_probability,
        )
    elif home_games_after == 6 and games_away == 6:
        home_after = _match_probability(
            sets_home,
            sets_away,
            home_games_after,
            games_away,
            0,
            0,
            True,
            0,
            0,
            next_server,
            next_server,
            sets_required,
            home_serve_probability,
            away_serve_probability,
        )
    else:
        home_after = _match_probability(
            sets_home,
            sets_away,
            home_games_after,
            games_away,
            0,
            0,
            False,
            0,
            0,
            None,
            next_server,
            sets_required,
            home_serve_probability,
            away_serve_probability,
        )
    if _normal_set_won(games_home, away_games_after):
        away_after = _match_probability(
            sets_home,
            sets_away + 1,
            0,
            0,
            0,
            0,
            False,
            0,
            0,
            None,
            next_server,
            sets_required,
            home_serve_probability,
            away_serve_probability,
        )
    elif games_home == 6 and away_games_after == 6:
        away_after = _match_probability(
            sets_home,
            sets_away,
            games_home,
            away_games_after,
            0,
            0,
            True,
            0,
            0,
            next_server,
            next_server,
            sets_required,
            home_serve_probability,
            away_serve_probability,
        )
    else:
        away_after = _match_probability(
            sets_home,
            sets_away,
            games_home,
            away_games_after,
            0,
            0,
            False,
            0,
            0,
            None,
            next_server,
            sets_required,
            home_serve_probability,
            away_serve_probability,
        )
    return _clamp_probability(game * home_after + (Decimal("1") - game) * away_after)


def _feature_vector_sha256(state: TennisState, prior: PrematchPrior) -> str:
    return expert_contract_sha256(
        {
            "schema": "live_win_probability_features_v1",
            "provider_match_id": state.provider_match_id,
            "revision": state.revision,
            "score": {
                "completed_sets": state.completed_sets,
                "games_home": state.games_home,
                "games_away": state.games_away,
                "points_home": state.points_home,
                "points_away": state.points_away,
                "in_tiebreak": state.in_tiebreak,
                "tiebreak_points_home": state.tiebreak_points_home,
                "tiebreak_points_away": state.tiebreak_points_away,
                "server_for_next_point": state.server_for_next_point,
            },
            "prematch_feature_vector_sha256": prior.feature_vector_sha256,
        }
    )


def _stratum(state: TennisState, prior: PrematchPrior) -> str:
    prefix = "bo3"
    if state.match_format is MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS:
        prefix = "bo5"
    return f"{prefix}-{prior.surface}-live"


def _unsupported_estimate(
    state: TennisState,
    prior: PrematchPrior,
    reason: DecisionReason,
    *,
    side: PlayerSide = PlayerSide.HOME,
) -> FairValueEstimate:
    return FairValueEstimate(
        player_side=side,
        fair_probability=Decimal("0.5"),
        lower_probability=Decimal("0"),
        upper_probability=Decimal("1"),
        supported=False,
        stratum=_stratum(state, prior),
        model_sha256=LIVE_WIN_PROBABILITY_MODEL_SHA256,
        prematch_artifact_sha256=prior.prematch_artifact_sha256,
        feature_definition_sha256=prior.feature_definition_sha256,
        feature_vector_sha256=_feature_vector_sha256(state, prior),
        calibration_artifact_sha256=None,
        abstention_reason=reason,
    )


def _home_probability(
    state: TennisState,
    home_serve_probability: Decimal,
    away_serve_probability: Decimal,
) -> Decimal:
    if state.status is MatchStatus.ENDED and state.winner is not None:
        if state.winner is PlayerSide.HOME:
            return Decimal("1")
        return Decimal("0")
    assert state.server_for_next_point is not None
    sets_home, sets_away = _completed_set_counts(state)
    return _match_probability(
        sets_home,
        sets_away,
        state.games_home,
        state.games_away,
        _score_points(state.points_home),
        _score_points(state.points_away),
        state.in_tiebreak,
        state.tiebreak_points_home,
        state.tiebreak_points_away,
        state.tiebreak_first_server,
        state.server_for_next_point,
        _sets_required(state.match_format),
        home_serve_probability,
        away_serve_probability,
    )


def _estimate_for_side(
    *,
    state: TennisState,
    prior: PrematchPrior,
    side: PlayerSide,
    center_home: Decimal,
    lower_home: Decimal,
    upper_home: Decimal,
) -> FairValueEstimate:
    if side is PlayerSide.HOME:
        center = center_home
        lower = lower_home
        upper = upper_home
    else:
        center = Decimal("1") - center_home
        lower = Decimal("1") - upper_home
        upper = Decimal("1") - lower_home
    return FairValueEstimate(
        player_side=side,
        fair_probability=_clamp_probability(center),
        lower_probability=_clamp_probability(lower),
        upper_probability=_clamp_probability(upper),
        supported=True,
        stratum=_stratum(state, prior),
        model_sha256=LIVE_WIN_PROBABILITY_MODEL_SHA256,
        prematch_artifact_sha256=prior.prematch_artifact_sha256,
        feature_definition_sha256=prior.feature_definition_sha256,
        feature_vector_sha256=_feature_vector_sha256(state, prior),
        calibration_artifact_sha256=None,
        abstention_reason=None,
    )


def live_fair_value_for_side(
    state: TennisState,
    prior: PrematchPrior,
    side: PlayerSide,
) -> FairValueEstimate:
    if type(state) is not TennisState:
        raise TypeError("state")
    if type(prior) is not PrematchPrior:
        raise TypeError("prior")
    _exact(side, PlayerSide, "side")
    if state.match_format not in _SUPPORTED_FORMATS:
        return _unsupported_estimate(
            state,
            prior,
            DecisionReason.MODEL_UNSUPPORTED,
            side=side,
        )
    try:
        validate_tennis_state(state)
    except TennisStateInvariantError:
        return _unsupported_estimate(
            state,
            prior,
            DecisionReason.MODEL_OUT_OF_DISTRIBUTION,
            side=side,
        )
    if (
        state.home_player_id != prior.player_home_id
        or state.away_player_id != prior.player_away_id
        or state.scheduled_start_wall_ns != prior.scheduled_start_wall_ns
    ):
        return _unsupported_estimate(
            state,
            prior,
            DecisionReason.MODEL_OUT_OF_DISTRIBUTION,
            side=side,
        )
    if state.status is not MatchStatus.ENDED:
        if state.server_for_next_point is None:
            return _unsupported_estimate(
                state,
                prior,
                DecisionReason.MODEL_UNSUPPORTED,
                side=side,
            )
        if (
            not prior.supported
            or prior.home_effective_sample_size
            < _MINIMUM_LIVE_EFFECTIVE_SAMPLE_SIZE
            or prior.away_effective_sample_size
            < _MINIMUM_LIVE_EFFECTIVE_SAMPLE_SIZE
        ):
            return _unsupported_estimate(
                state,
                prior,
                DecisionReason.MODEL_UNCERTAIN,
                side=side,
            )

    center_home = _home_probability(
        state,
        prior.home_serve_point_probability,
        prior.away_serve_point_probability,
    )
    lower_home = _home_probability(
        state,
        prior.home_serve_point_lower,
        prior.away_serve_point_upper,
    )
    upper_home = _home_probability(
        state,
        prior.home_serve_point_upper,
        prior.away_serve_point_lower,
    )
    if lower_home > upper_home:
        lower_home, upper_home = upper_home, lower_home
    return _estimate_for_side(
        state=state,
        prior=prior,
        side=side,
        center_home=center_home,
        lower_home=lower_home,
        upper_home=upper_home,
    )


def live_fair_value(
    state: TennisState,
    prior: PrematchPrior,
) -> FairValueEstimate:
    return live_fair_value_for_side(state, prior, PlayerSide.HOME)


__all__ = (
    "LIVE_WIN_PROBABILITY_MODEL_SHA256",
    "live_fair_value",
    "live_fair_value_for_side",
)
