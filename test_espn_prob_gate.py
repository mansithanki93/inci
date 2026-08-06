"""Focused tests for ESPN scoreboard parse, win-prob, and entry gate."""
from decimal import Decimal

from espn_tennis import parse_competition
from espn_prob_gate import (
    EspnProbGate, names_match, normalize_name, ticker_wants_live_tennis,
)
from live_tennis import parse_match
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


def test_parse_live_tennis_match_itf():
    row = {
        "id": 4242,
        "tournament": "ITF M15 Antalya",
        "tour": "itf",
        "format": "BO3",
        "round": "R16",
        "status": "live",
        "is_doubles": False,
        "players": {
            "p1": {"id": 1, "name": "Adrian Oetzbach"},
            "p2": {"id": 2, "name": "Jonas Fix"},
        },
        "score": {
            "sets": [0, 0],
            "games": [[3], [2]],
            "points": ["30", "15"],
            "server": 1,
        },
        "winner": None,
    }
    match = parse_match(row)
    assert match is not None
    assert match.competition_id == "lt:4242"
    assert match.league == "itf"
    assert match.state == "in"
    assert match.best_of == 3
    assert match.competitors[0].display_name == "Adrian Oetzbach"
    assert match.competitors[0].sets == (3,)
    assert match.competitors[1].sets == (2,)
    assert match.games == (3, 2)
    assert parse_match({**row, "is_doubles": True}) is None
    assert parse_match({**row, "status": "cancelled"}) is None
    print("PASS Live Tennis ITF match parse")


def test_display_player_name_from_kalshi_title():
    from espn_prob_gate import display_player_name
    assert display_player_name(
        "Will Massimo Giunta win the Tabacco vs Giunta: M25 Fano match?"
    ) == "Massimo Giunta"
    assert display_player_name("Adrian Oetzbach") == "Adrian Oetzbach"
    print("PASS Kalshi title player extraction")


def test_rank_contracts_prefer_bind_tiers():
    from decimal import Decimal
    from sports_discovery import (
        ContractProvenance, SelectedContract, rank_contracts,
        rank_contracts_prefer_bind,
    )

    def contract(ticker, *, bid_size, ask_size=None, start=10):
        ask_size = bid_size if ask_size is None else ask_size
        return SelectedContract(
            ticker=ticker, title=ticker, game_title="game",
            bid=Decimal(50), ask=Decimal(51),
            bid_size=Decimal(bid_size), ask_size=Decimal(ask_size),
            provenance=ContractProvenance(
                sport="Tennis", league=None, series_ticker="KXITF",
                milestone_id="m-" + ticker, event_ticker="e-" + ticker,
                scheduled_start_ts=start))

    deep_unbound = contract("deep-unbound", bid_size=100)
    shallow_bound = contract("shallow-bound", bid_size=5)
    mid_bound = contract("mid-bound", bid_size=20)
    ranked = rank_contracts_prefer_bind(
        (deep_unbound, shallow_bound, mid_bound), Decimal(10),
        {"shallow-bound", "mid-bound"})
    assert tuple(c.ticker for c in ranked) == (
        "mid-bound", "shallow-bound", "deep-unbound")
    # Without prefer-bind, depth wins.
    plain = rank_contracts(
        (deep_unbound, shallow_bound, mid_bound), Decimal(10))
    assert plain[0].ticker == "deep-unbound"
    print("PASS bind-prefer ranking tiers")


def test_gate_binds_itf_via_live_tennis_secondary():
    cfg = Config(
        espn_gate_enabled=True,
        espn_min_model_prob=0.35,
        espn_min_edge=0.03,
        live_tennis_enabled=True,
        live_tennis_ticker_substrings=("ITF",),
    )

    class EmptyEspn:
        def matches(self, force=False):
            return ()

    class FakeLiveTennis:
        def matches(self, force=False):
            return (parse_match({
                "id": 99,
                "tournament": "ITF M15",
                "tour": "itf",
                "format": "BO3",
                "status": "live",
                "is_doubles": False,
                "players": {
                    "p1": {"id": 1, "name": "Adrian Oetzbach"},
                    "p2": {"id": 2, "name": "Jonas Fix"},
                },
                "score": {"sets": [0, 0], "games": [[2], [1]], "server": 1},
            }),)

    assert ticker_wants_live_tennis("KXITFMATCH-FOO", "Oetzbach vs Fix")
    assert not ticker_wants_live_tennis("KXATPMATCH-FOO", "Fils vs Navone")

    gate = EspnProbGate(
        cfg, cache=EmptyEspn(), live_tennis_cache=FakeLiveTennis())
    # Non-ITF ticker must not consult Live Tennis → still unbound.
    atp_block = gate.decide(
        ticker="KXATPMATCH-X", player_name="Adrian Oetzbach",
        event_title="Oetzbach vs Fix", ask_cents=40)
    assert not atp_block.allow
    assert "no_espn_bind" in atp_block.reason

    ok = gate.decide(
        ticker="KXITFMATCH-X", player_name="Adrian Oetzbach",
        event_title="Oetzbach vs Fix", ask_cents=40)
    assert ok.allow, ok.reason
    assert ok.espn_match_id == "lt:99"
    assert "live_tennis_ok" in ok.reason
    print("PASS Live Tennis secondary ITF bind")


if __name__ == "__main__":
    test_names_match_surname()
    test_neutral_model_rejects_match_already_lost()
    test_parse_espn_competition_live()
    test_gate_blocks_unbound_and_allows_edge()
    test_parse_live_tennis_match_itf()
    test_display_player_name_from_kalshi_title()
    test_rank_contracts_prefer_bind_tiers()
    test_gate_binds_itf_via_live_tennis_secondary()
    print("\nALL ESPN GATE TESTS PASS")
