"""Focused tests for ESPN scoreboard parse, win-prob, and entry gate."""
from decimal import Decimal

from espn_tennis import parse_competition
from espn_prob_gate import EspnProbGate, names_match, normalize_name
from tennis_win_prob import match_win_probability, game_win_prob
from config import Config


def test_names_match_surname():
    assert names_match("Adrian Oetzbach", "A. Oetzbach")
    assert names_match("Arthur Fils", "Arthur Fils")
    assert not names_match("Arthur Fils", "Mariano Navone")
    assert normalize_name("José") == "jose"
    print("PASS name normalization/match")


def test_neutral_model_rejects_match_already_lost():
    assert match_win_probability(0, 2, 0, 0, best_of=3) == 0.0
    assert match_win_probability(2, 0, 0, 0, best_of=3) == 1.0
    # Down a set and down big in the current set is clearly < 0.35.
    p = match_win_probability(0, 1, 1, 5, best_of=3)
    assert p < 0.35
    assert abs(game_win_prob(0.5) - 0.5) < 1e-9
    print("PASS score model rejects collapsed match states")


def test_parse_espn_competition_live():
    competition = {
        "id": "181730",
        "status": {"type": {"state": "in", "detail": "2nd Set",
                            "shortDetail": "2nd"}},
        "format": {"regulation": {"periods": 3}},
        "notes": [{"text": "Mariano Navone (ARG) leads Arthur Fils (FRA) 3-6 1-1",
                   "type": "event"}],
        "competitors": [
            {
                "id": "1", "homeAway": "away", "possession": False,
                "linescores": [{"value": 6, "winner": True}, {"value": 1}],
                "athlete": {"displayName": "Arthur Fils", "shortName": "A. Fils"},
            },
            {
                "id": "2", "homeAway": "home", "possession": True,
                "linescores": [{"value": 3, "winner": False}, {"value": 1}],
                "athlete": {"displayName": "Mariano Navone",
                            "shortName": "M. Navone"},
            },
        ],
    }
    match = parse_competition(competition, "atp")
    assert match is not None
    assert match.state == "in"
    assert match.best_of == 3
    assert {c.display_name for c in match.competitors} == {
        "Arthur Fils", "Mariano Navone"}
    print("PASS ESPN competition parse")


def test_gate_blocks_unbound_and_allows_edge():
    cfg = Config(espn_gate_enabled=True, espn_min_model_prob=0.35,
                 espn_min_edge=0.03)

    class FakeCache:
        def matches(self, force=False):
            competition = {
                "id": "1",
                "status": {"type": {"state": "in", "detail": "1st Set"}},
                "format": {"regulation": {"periods": 3}},
                "notes": [],
                "competitors": [
                    {"id": "a", "homeAway": "home", "possession": True,
                     "linescores": [{"value": 3}],
                     "athlete": {"displayName": "Ada Ace"}},
                    {"id": "b", "homeAway": "away", "possession": False,
                     "linescores": [{"value": 2}],
                     "athlete": {"displayName": "Ben Break"}},
                ],
            }
            from espn_tennis import parse_competition
            return (parse_competition(competition, "atp"),)

    gate = EspnProbGate(cfg, cache=FakeCache())
    blocked = gate.decide(
        ticker="T-ITF", player_name="Adrian Oetzbach",
        event_title="Oetzbach vs Fix", ask_cents=40)
    assert not blocked.allow
    assert "no_espn_bind" in blocked.reason

    # Early first set, roughly fair: model ~0.5, ask 40c → edge ~0.10.
    ok = gate.decide(
        ticker="T-ATP", player_name="Ada Ace",
        event_title="Ace vs Break", ask_cents=40)
    assert ok.allow, ok.reason
    assert ok.model_prob is not None and ok.model_prob > Decimal("0.35")

    # Same score but ask already 70c → no edge.
    no_edge = gate.decide(
        ticker="T-ATP", player_name="Ada Ace",
        event_title="Ace vs Break", ask_cents=70)
    assert not no_edge.allow
    assert "edge" in no_edge.reason
    print("PASS ESPN prob gate bind/edge rules")


if __name__ == "__main__":
    test_names_match_surname()
    test_neutral_model_rejects_match_already_lost()
    test_parse_espn_competition_live()
    test_gate_blocks_unbound_and_allows_edge()
    print("\nALL ESPN GATE TESTS PASS")
