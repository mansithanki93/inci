from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, InvalidOperation, localcontext
from functools import lru_cache

from .contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ScoreValue,
    SetScore,
    TennisState,
)


_CONTEXT = Context(prec=50)


class WinProbabilityError(ValueError):
    pass


def _probability(value: object, name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise WinProbabilityError(name)
    if not Decimal("0") < value < Decimal("1"):
        raise WinProbabilityError(name)
    return value


def _point_score(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 4:
        raise WinProbabilityError(name)
    return value


def service_game_win_probability(
    server_point_probability: Decimal,
    *,
    server_points: int,
    receiver_points: int,
) -> Decimal:
    p = _probability(server_point_probability, "server_point_probability")
    server = _point_score(server_points, "server_points")
    receiver = _point_score(receiver_points, "receiver_points")
    if server == 4 and receiver != 3:
        raise WinProbabilityError("server_points")
    if receiver == 4 and server != 3:
        raise WinProbabilityError("receiver_points")
    if server == receiver == 4:
        raise WinProbabilityError("point_score")

    with localcontext(_CONTEXT):
        q = Decimal("1") - p
        deuce = p * p / (p * p + q * q)

        @lru_cache(maxsize=None)
        def solve(server_score: int, receiver_score: int) -> Decimal:
            if server_score >= 4 and server_score - receiver_score >= 2:
                return Decimal("1")
            if receiver_score >= 4 and receiver_score - server_score >= 2:
                return Decimal("0")
            if server_score >= 3 and receiver_score >= 3:
                if server_score == receiver_score:
                    return deuce
                if server_score > receiver_score:
                    return p + q * deuce
                return p * deuce
            return (
                p * solve(server_score + 1, receiver_score)
                + q * solve(server_score, receiver_score + 1)
            )

        try:
            return +solve(server, receiver)
        except InvalidOperation as error:
            raise WinProbabilityError("probability_arithmetic") from error


def _opposite(side: PlayerSide) -> PlayerSide:
    if type(side) is not PlayerSide:
        raise WinProbabilityError("player_side")
    if side is PlayerSide.HOME:
        return PlayerSide.AWAY
    return PlayerSide.HOME


def _tiebreak_server(first_server: PlayerSide, completed_points: int) -> PlayerSide:
    if completed_points % 4 in (0, 3):
        return first_server
    return _opposite(first_server)


def _solve_linear_system(
    matrix: list[list[Decimal]],
    values: list[Decimal],
) -> tuple[Decimal, ...]:
    size = len(values)
    augmented = [matrix[row][:] + [values[row]] for row in range(size)]
    for column in range(size):
        pivot = next(
            (
                row
                for row in range(column, size)
                if augmented[row][column] != 0
            ),
            None,
        )
        if pivot is None:
            raise WinProbabilityError("singular_probability_system")
        if pivot != column:
            augmented[column], augmented[pivot] = (
                augmented[pivot],
                augmented[column],
            )
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            multiplier = augmented[row][column]
            if multiplier == 0:
                continue
            augmented[row] = [
                current - multiplier * pivot_value
                for current, pivot_value in zip(
                    augmented[row],
                    augmented[column],
                    strict=True,
                )
            ]
    return tuple(row[-1] for row in augmented)


def _tiebreak_tail_probabilities(
    home_serve_point_probability: Decimal,
    away_serve_point_probability: Decimal,
    first_server: PlayerSide,
) -> dict[tuple[int, int], Decimal]:
    states = tuple(
        (difference, phase)
        for difference in (-1, 0, 1)
        for phase in range(4)
    )
    index = {state: position for position, state in enumerate(states)}
    size = len(states)
    matrix = [
        [Decimal("1") if row == column else Decimal("0") for column in range(size)]
        for row in range(size)
    ]
    values = [Decimal("0") for _ in range(size)]

    for state, row in index.items():
        difference, phase = state
        server = _tiebreak_server(first_server, phase)
        home_point = (
            home_serve_point_probability
            if server is PlayerSide.HOME
            else Decimal("1") - away_serve_point_probability
        )
        next_phase = (phase + 1) % 4
        home_difference = difference + 1
        away_difference = difference - 1
        if home_difference == 2:
            values[row] += home_point
        else:
            matrix[row][index[(home_difference, next_phase)]] -= home_point
        away_point = Decimal("1") - home_point
        if away_difference != -2:
            matrix[row][index[(away_difference, next_phase)]] -= away_point

    solved = _solve_linear_system(matrix, values)
    return {state: solved[position] for state, position in index.items()}


def tiebreak_home_win_probability(
    home_serve_point_probability: Decimal,
    away_serve_point_probability: Decimal,
    *,
    points_home: int,
    points_away: int,
    first_server: PlayerSide,
) -> Decimal:
    home_serve = _probability(
        home_serve_point_probability,
        "home_serve_point_probability",
    )
    away_serve = _probability(
        away_serve_point_probability,
        "away_serve_point_probability",
    )
    if type(points_home) is not int or not 0 <= points_home <= 10_000:
        raise WinProbabilityError("points_home")
    if type(points_away) is not int or not 0 <= points_away <= 10_000:
        raise WinProbabilityError("points_away")
    if type(first_server) is not PlayerSide:
        raise WinProbabilityError("first_server")
    if points_home >= 7 and points_home - points_away >= 2:
        return Decimal("1")
    if points_away >= 7 and points_away - points_home >= 2:
        return Decimal("0")

    with localcontext(_CONTEXT):
        tail = _tiebreak_tail_probabilities(home_serve, away_serve, first_server)

        @lru_cache(maxsize=None)
        def solve(home: int, away: int) -> Decimal:
            if home >= 7 and home - away >= 2:
                return Decimal("1")
            if away >= 7 and away - home >= 2:
                return Decimal("0")
            if home >= 6 and away >= 6:
                return tail[(home - away, (home + away) % 4)]
            server = _tiebreak_server(first_server, home + away)
            home_point = (
                home_serve
                if server is PlayerSide.HOME
                else Decimal("1") - away_serve
            )
            return (
                home_point * solve(home + 1, away)
                + (Decimal("1") - home_point) * solve(home, away + 1)
            )

        return +solve(points_home, points_away)


@dataclass(frozen=True, slots=True)
class PrematchWinProbabilities:
    home_match_probability: Decimal
    home_set1_probability: Decimal
    home_set2_probability: Decimal
    deciding_set_reach_probability: Decimal
    home_set3_probability_given_reached: Decimal

    def __post_init__(self) -> None:
        for name in (
            "home_match_probability",
            "home_set1_probability",
            "home_set2_probability",
            "deciding_set_reach_probability",
            "home_set3_probability_given_reached",
        ):
            value = getattr(self, name)
            if (
                type(value) is not Decimal
                or not value.is_finite()
                or not Decimal("0") <= value <= Decimal("1")
            ):
                raise WinProbabilityError(name)


@dataclass(frozen=True, slots=True)
class LiveWinProbabilities:
    home_match_probability: Decimal
    home_current_set_probability: Decimal

    def __post_init__(self) -> None:
        for name in (
            "home_match_probability",
            "home_current_set_probability",
        ):
            value = getattr(self, name)
            if (
                type(value) is not Decimal
                or not value.is_finite()
                or not Decimal("0") <= value <= Decimal("1")
            ):
                raise WinProbabilityError(name)


def _set_outcomes_from_boundary(
    *,
    games_home: int,
    games_away: int,
    next_server: PlayerSide,
    home_serve_point_probability: Decimal,
    away_serve_point_probability: Decimal,
) -> dict[tuple[PlayerSide, PlayerSide], Decimal]:
    home_hold = service_game_win_probability(
        home_serve_point_probability,
        server_points=0,
        receiver_points=0,
    )
    away_hold = service_game_win_probability(
        away_serve_point_probability,
        server_points=0,
        receiver_points=0,
    )

    @lru_cache(maxsize=None)
    def solve(
        home_games: int,
        away_games: int,
        server: PlayerSide,
    ) -> tuple[tuple[PlayerSide, PlayerSide, Decimal], ...]:
        if home_games >= 6 and home_games - away_games >= 2:
            return ((PlayerSide.HOME, server, Decimal("1")),)
        if away_games >= 6 and away_games - home_games >= 2:
            return ((PlayerSide.AWAY, server, Decimal("1")),)
        if home_games == away_games == 6:
            home_tiebreak = tiebreak_home_win_probability(
                home_serve_point_probability,
                away_serve_point_probability,
                points_home=0,
                points_away=0,
                first_server=server,
            )
            following_server = _opposite(server)
            return (
                (PlayerSide.HOME, following_server, home_tiebreak),
                (
                    PlayerSide.AWAY,
                    following_server,
                    Decimal("1") - home_tiebreak,
                ),
            )

        home_game = home_hold if server is PlayerSide.HOME else Decimal("1") - away_hold
        following_server = _opposite(server)
        combined: dict[tuple[PlayerSide, PlayerSide], Decimal] = {}
        for winner, after_set_server, probability in solve(
            home_games + 1,
            away_games,
            following_server,
        ):
            key = (winner, after_set_server)
            combined[key] = combined.get(key, Decimal("0")) + home_game * probability
        for winner, after_set_server, probability in solve(
            home_games,
            away_games + 1,
            following_server,
        ):
            key = (winner, after_set_server)
            combined[key] = (
                combined.get(key, Decimal("0"))
                + (Decimal("1") - home_game) * probability
            )
        return tuple(
            (winner, after_set_server, probability)
            for (winner, after_set_server), probability in sorted(
                combined.items(),
                key=lambda item: (item[0][0].value, item[0][1].value),
            )
        )

    return {
        (winner, following_server): probability
        for winner, following_server, probability in solve(
            games_home,
            games_away,
            next_server,
        )
    }


def standard_bo3_prematch_probabilities(
    home_serve_point_probability: Decimal,
    away_serve_point_probability: Decimal,
    *,
    first_server: PlayerSide | None,
) -> PrematchWinProbabilities:
    home_serve = _probability(
        home_serve_point_probability,
        "home_serve_point_probability",
    )
    away_serve = _probability(
        away_serve_point_probability,
        "away_serve_point_probability",
    )
    if first_server is not None and type(first_server) is not PlayerSide:
        raise WinProbabilityError("first_server")

    with localcontext(_CONTEXT):
        starts = (
            ((first_server, Decimal("1")),)
            if first_server is not None
            else (
                (PlayerSide.HOME, Decimal("0.5")),
                (PlayerSide.AWAY, Decimal("0.5")),
            )
        )
        set1_home = Decimal("0")
        set2_home = Decimal("0")
        deciding_reach = Decimal("0")
        set3_home_joint = Decimal("0")
        match_home = Decimal("0")

        for start_server, start_probability in starts:
            assert type(start_server) is PlayerSide
            set1 = _set_outcomes_from_boundary(
                games_home=0,
                games_away=0,
                next_server=start_server,
                home_serve_point_probability=home_serve,
                away_serve_point_probability=away_serve,
            )
            for (winner1, next_server2), probability1 in set1.items():
                path1 = start_probability * probability1
                if winner1 is PlayerSide.HOME:
                    set1_home += path1
                set2 = _set_outcomes_from_boundary(
                    games_home=0,
                    games_away=0,
                    next_server=next_server2,
                    home_serve_point_probability=home_serve,
                    away_serve_point_probability=away_serve,
                )
                for (winner2, next_server3), probability2 in set2.items():
                    path2 = path1 * probability2
                    if winner2 is PlayerSide.HOME:
                        set2_home += path2
                    if winner1 is winner2:
                        if winner1 is PlayerSide.HOME:
                            match_home += path2
                        continue
                    deciding_reach += path2
                    set3 = _set_outcomes_from_boundary(
                        games_home=0,
                        games_away=0,
                        next_server=next_server3,
                        home_serve_point_probability=home_serve,
                        away_serve_point_probability=away_serve,
                    )
                    for (winner3, _), probability3 in set3.items():
                        path3 = path2 * probability3
                        if winner3 is PlayerSide.HOME:
                            set3_home_joint += path3
                            match_home += path3

        if deciding_reach == 0:
            raise WinProbabilityError("deciding_set_reach_probability")
        return PrematchWinProbabilities(
            home_match_probability=+match_home,
            home_set1_probability=+set1_home,
            home_set2_probability=+set2_home,
            deciding_set_reach_probability=+deciding_reach,
            home_set3_probability_given_reached=+(
                set3_home_joint / deciding_reach
            ),
        )


_POINT_RANK = {
    ScoreValue.LOVE: 0,
    ScoreValue.FIFTEEN: 1,
    ScoreValue.THIRTY: 2,
    ScoreValue.FORTY: 3,
    ScoreValue.ADVANTAGE: 4,
}


def _completed_set_winner(score: SetScore) -> PlayerSide:
    if score.games_home > score.games_away:
        return PlayerSide.HOME
    return PlayerSide.AWAY


def _live_set_outcomes(
    state: TennisState,
    *,
    home_serve_point_probability: Decimal,
    away_serve_point_probability: Decimal,
) -> dict[tuple[PlayerSide, PlayerSide], Decimal]:
    server = state.server_for_next_point
    if server is None:
        raise WinProbabilityError("server_for_next_point")
    if state.in_tiebreak:
        first_server = state.tiebreak_first_server
        if first_server is None:
            raise WinProbabilityError("tiebreak_first_server")
        home_tiebreak = tiebreak_home_win_probability(
            home_serve_point_probability,
            away_serve_point_probability,
            points_home=state.tiebreak_points_home,
            points_away=state.tiebreak_points_away,
            first_server=first_server,
        )
        next_set_server = _opposite(first_server)
        return {
            (PlayerSide.HOME, next_set_server): home_tiebreak,
            (PlayerSide.AWAY, next_set_server): Decimal("1") - home_tiebreak,
        }

    home_points = _POINT_RANK[state.points_home]
    away_points = _POINT_RANK[state.points_away]
    if server is PlayerSide.HOME:
        home_game = service_game_win_probability(
            home_serve_point_probability,
            server_points=home_points,
            receiver_points=away_points,
        )
    else:
        away_game = service_game_win_probability(
            away_serve_point_probability,
            server_points=away_points,
            receiver_points=home_points,
        )
        home_game = Decimal("1") - away_game
    next_server = _opposite(server)
    home_branch = _set_outcomes_from_boundary(
        games_home=state.games_home + 1,
        games_away=state.games_away,
        next_server=next_server,
        home_serve_point_probability=home_serve_point_probability,
        away_serve_point_probability=away_serve_point_probability,
    )
    away_branch = _set_outcomes_from_boundary(
        games_home=state.games_home,
        games_away=state.games_away + 1,
        next_server=next_server,
        home_serve_point_probability=home_serve_point_probability,
        away_serve_point_probability=away_serve_point_probability,
    )
    combined: dict[tuple[PlayerSide, PlayerSide], Decimal] = {}
    for key, probability in home_branch.items():
        combined[key] = combined.get(key, Decimal("0")) + home_game * probability
    for key, probability in away_branch.items():
        combined[key] = (
            combined.get(key, Decimal("0"))
            + (Decimal("1") - home_game) * probability
        )
    return combined


def standard_bo3_live_probabilities(
    state: TennisState,
    home_serve_point_probability: Decimal,
    away_serve_point_probability: Decimal,
) -> LiveWinProbabilities:
    if type(state) is not TennisState:
        raise WinProbabilityError("state")
    if state.match_format is not MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS:
        raise WinProbabilityError("match_format")
    if (
        state.status is not MatchStatus.LIVE
        or state.block_reason is not None
        or not state.snapshot_complete
    ):
        raise WinProbabilityError("state_untrusted")
    home_serve = _probability(
        home_serve_point_probability,
        "home_serve_point_probability",
    )
    away_serve = _probability(
        away_serve_point_probability,
        "away_serve_point_probability",
    )
    home_sets = sum(
        1
        for score in state.completed_sets
        if _completed_set_winner(score) is PlayerSide.HOME
    )
    away_sets = len(state.completed_sets) - home_sets

    with localcontext(_CONTEXT):
        @lru_cache(maxsize=None)
        def match_from_boundary(
            home_wins: int,
            away_wins: int,
            next_server: PlayerSide,
        ) -> Decimal:
            if home_wins == 2:
                return Decimal("1")
            if away_wins == 2:
                return Decimal("0")
            total = Decimal("0")
            outcomes = _set_outcomes_from_boundary(
                games_home=0,
                games_away=0,
                next_server=next_server,
                home_serve_point_probability=home_serve,
                away_serve_point_probability=away_serve,
            )
            for (winner, following_server), probability in outcomes.items():
                total += probability * match_from_boundary(
                    home_wins + (winner is PlayerSide.HOME),
                    away_wins + (winner is PlayerSide.AWAY),
                    following_server,
                )
            return total

        current_outcomes = _live_set_outcomes(
            state,
            home_serve_point_probability=home_serve,
            away_serve_point_probability=away_serve,
        )
        current_set_home = Decimal("0")
        match_home = Decimal("0")
        for (winner, following_server), probability in current_outcomes.items():
            if winner is PlayerSide.HOME:
                current_set_home += probability
            match_home += probability * match_from_boundary(
                home_sets + (winner is PlayerSide.HOME),
                away_sets + (winner is PlayerSide.AWAY),
                following_server,
            )
        return LiveWinProbabilities(
            home_match_probability=+match_home,
            home_current_set_probability=+current_set_home,
        )
