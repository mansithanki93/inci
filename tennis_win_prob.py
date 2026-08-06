"""Score-based tennis match-win probability (research model).

Neutral iid point model (default p=1/2) maps an observed score to
P(player wins the match). Enough to reject collapsing sides without claiming
ranking skill. Simpler than sealed Tennis v1 FairValueEstimate; usable as a
v6 paper entry gate until Phase B lands.
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
