"""Score-based tennis match-win probability (research model).

The neutral iid point model (default p=1/2) maps an observed score to a
collapse guard without claiming player skill. A genuine upstream prematch
match probability can also be calibrated to the model's effective point
parameter, then updated consistently as the score changes.
"""
from __future__ import annotations

from functools import lru_cache


def game_win_prob(p_point: float = 0.5) -> float:
    """P(win a game) under iid points with advantage scoring."""
    p = float(p_point)
    q = 1.0 - p
    # From deuce, P(win) = p^2 / (p^2 + q^2).
    deuce = (p * p) / (p * p + q * q) if (p * p + q * q) else 0.5

    @lru_cache(None)
    def w(a: int, b: int) -> float:
        if a >= 4 and a - b >= 2:
            return 1.0
        if b >= 4 and b - a >= 2:
            return 0.0
        if a >= 3 and b >= 3:
            if a == b:
                return deuce
            if a == b + 1:
                return p * 1.0 + q * deuce
            if b == a + 1:
                return p * deuce + q * 0.0
        return p * w(a + 1, b) + q * w(a, b + 1)

    return w(0, 0)


def set_win_prob(games_for: int, games_against: int, p_game: float) -> float:
    """P(win set) from current games; tiebreak approximated at 6-6."""
    p = float(p_game)
    q = 1.0 - p
    gf = max(0, min(int(games_for), 7))
    ga = max(0, min(int(games_against), 7))

    @lru_cache(None)
    def w(a: int, b: int) -> float:
        if a >= 6 and a - b >= 2:
            return 1.0
        if b >= 6 and b - a >= 2:
            return 0.0
        if a == 7 and b == 6:
            return 1.0
        if b == 7 and a == 6:
            return 0.0
        if a == 6 and b == 6:
            # Approximate tiebreak win rate with p_game.
            return p
        return p * w(a + 1, b) + q * w(a, b + 1)

    return w(gf, ga)


def match_win_from_sets(sets_for: int, sets_against: int, p_set: float,
                        sets_needed: int) -> float:
    p = float(p_set)
    q = 1.0 - p

    @lru_cache(None)
    def w(a: int, b: int) -> float:
        if a >= sets_needed:
            return 1.0
        if b >= sets_needed:
            return 0.0
        return p * w(a + 1, b) + q * w(a, b + 1)

    return w(int(sets_for), int(sets_against))


def match_win_probability(
        sets_for: int,
        sets_against: int,
        games_for: int = 0,
        games_against: int = 0,
        *,
        best_of: int = 3,
        p_point: float = 0.5,
) -> float:
    """P(this player wins the match) from set/game score."""
    if best_of not in (3, 5):
        raise ValueError("best_of must be 3 or 5")
    sets_needed = best_of // 2 + 1
    if sets_for >= sets_needed:
        return 1.0
    if sets_against >= sets_needed:
        return 0.0
    p_game = game_win_prob(p_point)
    p_set_now = set_win_prob(games_for, games_against, p_game)
    p_set_fresh = set_win_prob(0, 0, p_game)
    win_if_take = match_win_from_sets(
        sets_for + 1, sets_against, p_set_fresh, sets_needed)
    win_if_lose = match_win_from_sets(
        sets_for, sets_against + 1, p_set_fresh, sets_needed)
    return p_set_now * win_if_take + (1.0 - p_set_now) * win_if_lose


@lru_cache(maxsize=4096)
def effective_point_probability(prematch_probability: float, *,
                                best_of: int = 3) -> float:
    """Invert the neutral-score model to calibrate a prematch match prior.

    The result is an effective iid point probability, not a claim that real
    points are iid.  It is simply the monotone latent parameter that lets the
    same score-state transition update a genuine upstream match probability.
    """
    target = float(prematch_probability)
    if not 0.0 <= target <= 1.0:
        raise ValueError("prematch_probability must be between 0 and 1")
    if best_of not in (3, 5):
        raise ValueError("best_of must be 3 or 5")
    if target in (0.0, 1.0):
        return target
    low, high = 0.0, 1.0
    for _ in range(60):
        middle = (low + high) / 2.0
        value = match_win_probability(
            0, 0, 0, 0, best_of=best_of, p_point=middle)
        if value < target:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def match_win_probability_from_prematch(
        prematch_probability: float,
        sets_for: int,
        sets_against: int,
        games_for: int = 0,
        games_against: int = 0,
        *,
        best_of: int = 3,
) -> float:
    """Update a genuine prematch match probability from observed score."""
    p_point = effective_point_probability(
        prematch_probability, best_of=best_of)
    return match_win_probability(
        sets_for, sets_against, games_for, games_against,
        best_of=best_of, p_point=p_point)


def set_complete(a: int, b: int) -> bool:
    hi, lo = max(a, b), min(a, b)
    return (hi >= 6 and hi - lo >= 2) or (hi == 7 and lo == 6)


def completed_sets_won(sets_a: tuple[int, ...], sets_b: tuple[int, ...],
                       *, live: bool) -> tuple[int, int]:
    n = min(len(sets_a), len(sets_b))
    aw = bw = 0
    for i in range(n):
        a, b = int(sets_a[i]), int(sets_b[i])
        if live and i == n - 1 and not set_complete(a, b):
            continue
        if set_complete(a, b):
            if a > b:
                aw += 1
            elif b > a:
                bw += 1
    return aw, bw


def current_games(sets_a: tuple[int, ...], sets_b: tuple[int, ...],
                  *, live: bool) -> tuple[int, int]:
    if not sets_a or not sets_b or len(sets_a) != len(sets_b):
        return 0, 0
    a, b = int(sets_a[-1]), int(sets_b[-1])
    if live and not set_complete(a, b):
        return a, b
    return 0, 0


def score_transition_advances(previous, current) -> bool:
    """Validate one provider score transition for a fixed player orientation.

    Values are ``(provider_timestamp, lifecycle, sets_for, sets_against,
    games_for, games_against)``. Completed-set counts never decrease. Current
    games never decrease while the completed-set score is unchanged; a new
    completed set is the only legitimate reset. Equal provider timestamps
    must describe exactly the same lifecycle and score.
    """
    if previous is None:
        return True
    if not isinstance(previous, tuple) or not isinstance(current, tuple):
        return False
    if len(previous) != 6 or len(current) != 6:
        return False
    prior_ts, prior_state, prior_sf, prior_sa, prior_gf, prior_ga = previous
    next_ts, next_state, next_sf, next_sa, next_gf, next_ga = current
    ranks = {"pre": 0, "in": 1, "post": 2}
    if prior_state not in ranks or next_state not in ranks:
        return False
    if ranks[next_state] < ranks[prior_state]:
        return False
    if prior_ts is not None:
        if next_ts is None or next_ts < prior_ts:
            return False
        if next_ts == prior_ts and current[1:] != previous[1:]:
            return False
    if next_sf < prior_sf or next_sa < prior_sa:
        return False
    if (next_sf, next_sa) == (prior_sf, prior_sa) and (
            next_gf < prior_gf or next_ga < prior_ga):
        return False
    return True
