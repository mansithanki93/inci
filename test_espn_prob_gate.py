"""Focused tests for ESPN scoreboard parse, win-prob, and entry gate."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from espn_tennis import (
    EspnScoreboardCache, fetch_live_matches, parse_competition,
    parse_provider_timestamp,
)
from espn_prob_gate import (
    EspnProbGate, names_match, normalize_name, ticker_wants_live_tennis,
)
from live_tennis import LiveTennisCache, fetch_matches, parse_match
from tennis_win_prob import game_win_prob, match_win_probability
from config import Config


_PRIOR_TEST_NOW = datetime(
    2026, 8, 6, 12, 0, 30, tzinfo=timezone.utc).timestamp()


def _strict_prior(identity, first, second):
    return SimpleNamespace(
        **identity,
        model_as_of=datetime(2026, 8, 6, 11, 59, tzinfo=timezone.utc),
        match_start=datetime(2026, 8, 6, 12, 1, tzinfo=timezone.utc),
        model_1_probability=Decimal(str(first)),
        model_2_probability=Decimal(str(second)),
        provenance=SimpleNamespace(
            source_sha256="a" * 64,
            generated_at=datetime(
                2026, 8, 6, 12, 0, tzinfo=timezone.utc),
            model_1_id="static-v1", model_2_id="dynamic-v1"))


def _prior_provider(first, second):
    return lambda **identity: _strict_prior(identity, first, second)


def test_names_match_surname():
    assert names_match("Adrian Oetzbach", "A. Oetzbach")
    assert names_match("Arthur Fils", "Arthur Fils")
    assert not names_match("Arthur Fils", "Mariano Navone")
    assert not names_match("Venus Williams", "Serena Williams")
    assert not names_match("V Williams", "Vera Williams")
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


def test_espn_competition_requires_nonempty_identity():
    row = {
        "id": "",
        "status": {"type": {"state": "pre", "detail": "Scheduled"}},
        "competitors": [
            {"id": "a", "athlete": {"displayName": "Ada Ace"}},
            {"id": "b", "athlete": {"displayName": "Bea Break"}},
        ],
    }
    assert parse_competition(row, "wta") is None
    assert parse_competition({**row, "id": "  "}, "wta") is None
    print("PASS ESPN competition requires nonempty identity")


def test_espn_ignores_route_periods_and_deduplicates_competition_ids():
    """Regression: route-level periods=5 must not turn ordinary ATP into BO5."""
    competition = {
        "id": "same-competition",
        "status": {"type": {"state": "in", "detail": "2nd Set"}},
        # Captured ESPN shape reports this as 5 on the ATP route and 3 on WTA.
        "format": {"regulation": {"periods": 5}},
        "competitors": [
            {"id": "a", "linescores": [{"value": 6}, {"value": 1}],
             "athlete": {"displayName": "Nicolas Mejia"}},
            {"id": "b", "linescores": [{"value": 4}, {"value": 2}],
             "athlete": {"displayName": "Marco Trungelliti"}},
        ],
    }

    def get_json(url):
        route_periods = 5 if "/atp/" in url else 3
        row = {**competition, "format": {"regulation": {
            "periods": route_periods}}}
        return {"events": [{
            "name": "ATP Challenger Barranquilla",
            "groupings": [{"displayName": "Men's Singles",
                           "competitions": [row]}],
        }]}

    matches = fetch_live_matches(("atp", "wta"), get_json=get_json)
    assert len(matches) == 1
    assert matches[0].competition_id == "same-competition"
    assert matches[0].best_of == 3

    major = parse_competition(
        competition, "atp", event_name="Wimbledon",
        grouping_name="Men's Singles")
    assert major is not None and major.best_of == 5
    women = parse_competition(
        competition, "wta", event_name="Wimbledon",
        grouping_name="Women's Singles")
    assert women is not None and women.best_of == 3
    nested = fetch_live_matches(("atp",), get_json=lambda _url: {
        "events": [{
            "name": "Wimbledon",
            "groupings": [{
                "grouping": {"displayName": "Men's Singles"},
                "competitions": [competition],
            }],
        }],
    })
    assert len(nested) == 1 and nested[0].best_of == 5
    print("PASS ESPN route periods ignored, IDs deduped, BO5 inferred safely")


def test_major_qualifying_is_not_inferred_as_best_of_five():
    competition = {
        "id": "wimbledon-qualifying",
        "status": {"type": {"state": "pre", "detail": "Scheduled"}},
        "competitors": [
            {"id": "a", "athlete": {"displayName": "Ada Ace"}},
            {"id": "b", "athlete": {"displayName": "Ben Break"}},
        ],
    }
    match = parse_competition(
        competition, "atp", event_name="Wimbledon Qualifying",
        grouping_name="Men's Singles Qualifying")
    assert match is not None
    assert match.best_of == 3
    print("PASS major qualifying remains best-of-three")


def test_completed_set_is_not_reused_as_current_games():
    """Regression: a completed 6-4 set counts once, never as current games."""
    competition = {
        "id": "between-sets",
        "timestamp": _PRIOR_TEST_NOW,
        "status": {"type": {"state": "in", "detail": "2nd Set"}},
        "notes": [{"text": "Ada Ace leads Ben Break 6-4"}],
        "competitors": [
            {"id": "a", "linescores": [{"value": 6, "winner": True}],
             "athlete": {"displayName": "Ada Ace"}},
            {"id": "b", "linescores": [{"value": 4, "winner": False}],
             "athlete": {"displayName": "Ben Break"}},
        ],
    }
    match = parse_competition(competition, "atp")
    assert match is not None
    assert match.games is None

    class Cache:
        def matches(self, force=False):
            return (match,)

    cfg = Config(espn_gate_enabled=True, espn_min_model_prob=0.35,
                 espn_min_edge=0.03)
    gate = EspnProbGate(
        cfg, cache=Cache(), clock=lambda: _PRIOR_TEST_NOW,
        prematch_prior_provider=_prior_provider("0.5", "0.5"))
    decision = gate.decide(
        ticker="T-ATP", player_name="Ada Ace",
        event_title="Ada Ace vs Ben Break", ask_cents=70)
    assert decision.allow, decision.reason
    assert decision.model_prob is not None
    assert Decimal("0.74") < decision.model_prob < Decimal("0.76")
    print("PASS completed set is not double-counted")


def test_gate_blocks_unbound_and_allows_edge():
    cfg = Config(espn_gate_enabled=True, espn_min_model_prob=0.35,
                 espn_min_edge=0.03)

    class FakeCache:
        def matches(self, force=False):
            competition = {
                "id": "1",
                "timestamp": _PRIOR_TEST_NOW,
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

    gate = EspnProbGate(
        cfg, cache=FakeCache(), clock=lambda: _PRIOR_TEST_NOW,
        prematch_prior_provider=_prior_provider("0.60", "0.65"))
    blocked = gate.decide(
        ticker="T-ITF", player_name="Adrian Oetzbach",
        event_title="Adrian Oetzbach vs Jonas Fix", ask_cents=40)
    assert not blocked.allow
    assert "no_espn_bind" in blocked.reason

    # Early first set, roughly fair: model ~0.5, ask 40c → edge ~0.10.
    ok = gate.decide(
        ticker="T-ATP", player_name="Ada Ace",
        event_title="Ada Ace vs Ben Break", ask_cents=40)
    assert ok.allow, ok.reason
    assert ok.model_prob is not None and ok.model_prob > Decimal("0.35")

    # Same score but ask already 70c → no edge.
    no_edge = gate.decide(
        ticker="T-ATP", player_name="Ada Ace",
        event_title="Ada Ace vs Ben Break", ask_cents=70)
    assert not no_edge.allow
    assert "edge" in no_edge.reason

    # With no genuine prematch models, neutral 0.5 is only a collapse guard;
    # it must not invent a +30-point edge against a 20-cent market.
    guard = EspnProbGate(
        cfg, cache=FakeCache(), clock=lambda: _PRIOR_TEST_NOW)
    guard_only = guard.decide(
        ticker="T-ATP", player_name="Ada Ace",
        event_title="Ada Ace vs Ben Break", ask_cents=70)
    assert guard_only.allow, guard_only.reason
    assert "score_guard_only" in guard_only.reason
    assert guard_only.edge is None
    assert guard.model_edge_score(
        ticker="T-ATP", player_name="Ada Ace",
        event_title="Ada Ace vs Ben Break", ask_cents=20) == (1, Decimal(0))
    print("PASS ESPN prob gate bind/edge rules")


def test_two_model_prior_is_updated_from_score_conservatively():
    import tennis_win_prob
    assert hasattr(tennis_win_prob, "match_win_probability_from_prematch")
    update = tennis_win_prob.match_win_probability_from_prematch
    assert abs(update(
        0.62, 0, 0, 0, 0, best_of=3) - 0.62) < 1e-6
    ahead = update(
        0.62, 1, 0, 2, 1, best_of=3)
    behind = update(
        0.62, 0, 1, 1, 5, best_of=3)
    assert ahead > 0.62
    assert behind < 0.62
    print("PASS prematch prior is calibrated then updated by live score")


def test_configured_prior_provider_is_strict_and_accepts_store_object():
    class Cache:
        def matches(self, force=False):
            return (parse_competition({
                "id": "prior-card",
                "status": {"type": {"state": "pre", "detail": "Scheduled"}},
                "competitors": [
                    {"id": "ada-id", "athlete": {"displayName": "Ada Ace"}},
                    {"id": "ben-id", "athlete": {"displayName": "Ben Break"}},
                ],
            }, "atp"),)

    cfg = Config(espn_min_edge=0.03)
    unavailable = EspnProbGate(
        cfg, cache=Cache(), prematch_prior_provider=lambda **_: None)
    blocked = unavailable.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Ben Break", ask_cents=50)
    assert not blocked.allow
    assert "prematch_prior_unavailable" in blocked.reason
    assert unavailable.model_edge_score(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Ben Break", ask_cents=50) == (0, Decimal(0))

    lookups = []

    def provider(**identity):
        lookups.append(identity)
        return _strict_prior(identity, "0.64", "0.60")

    gate = EspnProbGate(
        cfg, cache=Cache(), clock=lambda: _PRIOR_TEST_NOW,
        prematch_prior_provider=provider)
    allowed = gate.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Ben Break", ask_cents=55)
    assert allowed.allow, allowed.reason
    assert allowed.model_prob == Decimal("0.6")
    assert allowed.model_1_prob == Decimal("0.64")
    assert allowed.model_2_prob == Decimal("0.6")
    assert allowed.prior_source_sha256 == "a" * 64
    assert allowed.prior_generated_at == "2026-08-06T12:00:00Z"
    assert allowed.prior_model_1_id == "static-v1"
    assert allowed.prior_model_2_id == "dynamic-v1"
    assert allowed.edge == Decimal("0.05")
    assert lookups == [{
        "competition_id": "espn:prior-card",
        "athlete_id": "espn:athlete:ada-id",
        "opponent_athlete_id": "espn:athlete:ben-id",
        "player_name": "Ada Ace",
        "opponent_name": "Ben Break",
    }]

    lt_identity = []
    lt_match = parse_match({
        "id": 9, "tour": "itf", "format": "BO3", "status": "live",
        "is_doubles": False, "timestamp": _PRIOR_TEST_NOW,
        "players": {"p1": {"id": 11, "name": "Ada Ace"},
                    "p2": {"id": 12, "name": "Ben Break"}},
        "score": {"sets": [0, 0], "games": [[1], [0]]},
    })

    class LiveCache:
        def matches(self, force=False):
            return (lt_match,)

    def live_provider(**identity):
        lt_identity.append(identity)
        return _strict_prior(identity, "0.6", "0.6")

    lt_gate = EspnProbGate(
        cfg, cache=LiveCache(), clock=lambda: _PRIOR_TEST_NOW,
        prematch_prior_provider=live_provider)
    assert lt_gate.decide(
        ticker="ITF-T", player_name="Ada Ace",
        event_title="Ada Ace vs Ben Break", ask_cents=55).allow
    assert lt_identity == [{
        "competition_id": "lt:9", "athlete_id": "lt:athlete:11",
        "opponent_athlete_id": "lt:athlete:12",
        "player_name": "Ada Ace",
        "opponent_name": "Ben Break",
    }]
    print("PASS configured prior is strict and store object is supported")


def test_active_match_pins_prematch_baseline_and_emits_replay_state():
    """A live file rewrite must not become a second 'prematch' baseline."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    card = parse_competition({
        "id": "pinned-card", "timestamp": _PRIOR_TEST_NOW,
        "status": {"type": {"state": "in", "detail": "1st Set"}},
        "competitors": [
            {"id": "ada", "linescores": [{"value": 0}],
             "athlete": {"displayName": "Ada Ace"}},
            {"id": "bea", "linescores": [{"value": 0}],
             "athlete": {"displayName": "Bea Break"}},
        ],
    }, "atp")

    class Cache:
        def matches(self, force=False):
            return (card,)

    values = [Decimal("0.64"), Decimal("0.20")]
    calls = []

    def provider(**identity):
        calls.append(identity)
        probability = values.pop(0)
        return SimpleNamespace(
            competition_id="espn:pinned-card",
            athlete_id="espn:athlete:ada",
            opponent_athlete_id="espn:athlete:bea",
            player_name="Ada Ace", opponent_name="Bea Break",
            model_as_of=datetime(
                2026, 8, 6, 11, 59, tzinfo=timezone.utc),
            match_start=datetime(
                2026, 8, 6, 12, 0, tzinfo=timezone.utc),
            model_1_probability=probability,
            model_2_probability=probability,
            provenance=SimpleNamespace(
                source_sha256=("a" if probability > Decimal("0.5") else "b") * 64,
                generated_at=datetime(
                    2026, 8, 6, 11, 59, 30, tzinfo=timezone.utc),
                model_1_id="static-v1", model_2_id="dynamic-v1"))

    gate = EspnProbGate(
        Config(), cache=Cache(), clock=lambda: _PRIOR_TEST_NOW,
        prematch_prior_provider=provider)
    first = gate.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Bea Break", ask_cents=40,
        scheduled_start_ts=1786017600)
    second = gate.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Bea Break", ask_cents=40,
        scheduled_start_ts=1786017600)

    assert first.allow and second.allow
    assert first.prematch_model_1_prob == Decimal("0.64")
    assert second.prematch_model_1_prob == Decimal("0.64")
    assert first.prior_source_sha256 == second.prior_source_sha256 == "a" * 64
    assert len(calls) == 1
    assert calls[0] == {
        "competition_id": "espn:pinned-card",
        "athlete_id": "espn:athlete:ada",
        "opponent_athlete_id": "espn:athlete:bea",
        "player_name": "Ada Ace",
        "opponent_name": "Bea Break",
    }
    assert first.score_source == "espn"
    assert first.score_match_id == "espn:pinned-card"
    assert first.score_athlete_id == "espn:athlete:ada"
    assert first.score_opponent_id == "espn:athlete:bea"
    assert first.score_timestamp == Decimal(str(_PRIOR_TEST_NOW))
    assert first.score_lifecycle_state == "in"
    assert first.score_observed is True
    assert first.score_best_of == 3
    assert (first.score_sets_for, first.score_sets_against,
            first.score_games_for, first.score_games_against) == (0, 0, 0, 0)
    assert first.prior_model_as_of == "2026-08-06T11:59:00Z"
    assert first.prior_match_start == "2026-08-06T12:00:00Z"
    print("PASS active match pins prematch prior and exposes replay state")


def test_pinned_prematch_prior_still_expires():
    """Pinning freezes the baseline, not its configured freshness window."""
    generated = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    match_start = datetime(2026, 8, 6, 12, 30, tzinfo=timezone.utc)
    now = [generated.timestamp() + 1]

    def card():
        return parse_competition({
            "id": "aging-prior", "timestamp": now[0],
            "status": {"type": {"state": "in", "detail": "1st Set"}},
            "competitors": [
                {"id": "ada", "linescores": [{"value": 0}],
                 "athlete": {"displayName": "Ada Ace"}},
                {"id": "bea", "linescores": [{"value": 0}],
                 "athlete": {"displayName": "Bea Break"}},
            ],
        }, "wta")

    class Cache:
        def matches(self, force=False):
            return (card(),)

    calls = []

    def provider(**identity):
        calls.append(identity)
        return SimpleNamespace(
            **identity,
            model_as_of=generated,
            match_start=match_start,
            model_1_probability=Decimal("0.80"),
            model_2_probability=Decimal("0.80"),
            provenance=SimpleNamespace(
                source_sha256="a" * 64,
                generated_at=generated,
                model_1_id="static-v1", model_2_id="dynamic-v1"))

    gate = EspnProbGate(
        Config(two_model_prior_max_age_s=5), cache=Cache(),
        clock=lambda: now[0], prematch_prior_provider=provider)
    first = gate.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Bea Break", ask_cents=50,
        scheduled_start_ts=match_start.timestamp())
    assert first.allow, first.reason

    now[0] = generated.timestamp() + 6
    expired = gate.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Bea Break", ask_cents=50,
        scheduled_start_ts=match_start.timestamp())
    assert not expired.allow
    assert "prematch_prior_unavailable" in expired.reason
    assert len(calls) == 1
    print("PASS pinned prematch prior still expires")


def test_sibling_score_prioritizes_entry_eligibility_before_raw_edge():
    card = parse_competition({
        "id": "eligibility-card",
        "status": {"type": {"state": "pre", "detail": "Scheduled"}},
        "competitors": [
            {"id": "low", "athlete": {"displayName": "Low Player"}},
            {"id": "high", "athlete": {"displayName": "High Player"}},
        ],
    }, "atp")

    class Cache:
        def matches(self, force=False):
            return (card,)

    def provider(**identity):
        probability = 0.25 if identity["athlete_id"].endswith(":low") else 0.75
        return _strict_prior(identity, probability, probability)

    gate = EspnProbGate(
        Config(espn_min_model_prob=0.35, espn_min_edge=0.03),
        cache=Cache(), clock=lambda: _PRIOR_TEST_NOW,
        prematch_prior_provider=provider)
    # Low has a larger raw edge (+15 points) but fails the 35% probability
    # floor. High has only +5 points but is an actually eligible entry.
    low = gate.model_edge_score(
        ticker="LOW", player_name="Low Player",
        event_title="Low Player vs High Player", ask_cents=10)
    high = gate.model_edge_score(
        ticker="HIGH", player_name="High Player",
        event_title="Low Player vs High Player", ask_cents=70)
    assert low[0] == 0
    assert high[0] == 1
    assert high > low
    print("PASS sibling score ranks eligible sides before raw edge")


def test_binding_requires_identity_opponent_and_unique_match():
    def match(match_id, me, opp):
        return parse_competition({
            "id": match_id,
            "timestamp": 100.0,
            "status": {"type": {"state": "in", "detail": "1st Set"}},
            "competitors": [
                {"id": match_id + "a", "linescores": [{"value": 1}],
                 "athlete": {"displayName": me}},
                {"id": match_id + "b", "linescores": [{"value": 1}],
                 "athlete": {"displayName": opp}},
            ],
        }, "atp")

    class Cache:
        def __init__(self, values):
            self.values = values

        def matches(self, force=False):
            return self.values

    cfg = Config()
    venus = match("venus", "Venus Williams", "Anna Smith")
    serena = match("serena", "Serena Williams", "Bella Jones")
    gate = EspnProbGate(
        cfg, cache=Cache((venus, serena)), clock=lambda: 100.0)
    assert gate.find_bind(
        ticker="T", player_name="Venus Williams",
        event_title="Venus Williams vs Anna Smith") is not None
    assert gate.binding_identity(
        ticker="T",
        player_name="Will Venus Williams win the Williams vs Smith match?",
        event_title="Venus Williams vs Anna Smith") == (
            "espn:venus", "espn:athlete:venusa",
            "espn:athlete:venusb")
    assert gate.find_bind(
        ticker="T", player_name="Venus Williams",
        event_title="Williams winner") is None
    assert gate.find_bind(
        ticker="T", player_name="V Williams",
        event_title="Serena Williams vs Bella Jones") is None

    duplicate_card = match("venus-2", "Venus Williams", "Anna Smith")
    ambiguous = EspnProbGate(
        cfg, cache=Cache((venus, duplicate_card)), clock=lambda: 100.0)
    assert ambiguous.find_bind(
        ticker="T", player_name="Venus Williams",
        event_title="Venus Williams vs Anna Smith") is None
    print("PASS binding requires target identity, opponent, and uniqueness")


def test_binding_requires_opponent_first_identity_not_just_surname():
    card = parse_competition({
        "id": "venus-anna",
        "status": {"type": {"state": "pre", "detail": "Scheduled"}},
        "competitors": [
            {"id": "venus", "athlete": {"displayName": "Venus Williams"}},
            {"id": "anna", "athlete": {"displayName": "Anna Smith"}},
        ],
    }, "wta")

    class Cache:
        def matches(self, force=False):
            return (card,)

    gate = EspnProbGate(Config(), cache=Cache())
    assert gate.find_bind(
        ticker="T", player_name="Venus Williams",
        event_title="Venus Williams vs Anna Smith") is not None
    # Conflicting given name for the opponent surname must fail closed.
    assert gate.find_bind(
        ticker="T", player_name="Venus Williams",
        event_title="Venus Williams vs Alice Smith") is None
    # Kalshi ITF-style surname-only titles are allowed when both surnames
    # appear and no conflicting given name is asserted.
    assert gate.find_bind(
        ticker="T", player_name="Venus Williams",
        event_title="Williams vs Smith") is not None
    print("PASS binding verifies opponent identity without blocking "
          "surname-only titles")


def test_binding_accepts_kalshi_itf_surname_only_event_titles():
    """Live Tennis cards use full names; Kalshi ITF events use surnames."""
    card = parse_match({
        "id": 172107,
        "tournament": "W15 Tianjin",
        "tour": "itf",
        "format": "BO3",
        "status": "live",
        "is_doubles": False,
        "timestamp": 100.0,
        "players": {
            "p1": {"id": 1072, "name": "Chengyiyi Yuan"},
            "p2": {"id": 11402, "name": "Yu Jun Lin"},
        },
        "score": {"sets": [0, 0], "games": [[4], [3]], "server": 1},
    })
    assert card is not None

    class EspnEmpty:
        def matches(self, force=False):
            return ()

    class LiveCache:
        def matches(self, force=False):
            return (card,)

    gate = EspnProbGate(
        Config(), cache=EspnEmpty(), live_tennis_cache=LiveCache(),
        clock=lambda: 100.0)
    title = ("Will Yu Jun Lin win the Yuan vs Lin: "
             "W15 Tianjin Quarterfinal match?")
    assert gate.find_bind(
        ticker="KXITFWMATCH-26AUG06YUALIN-LIN",
        player_name=title,
        event_title="Yuan vs Lin") is not None
    assert gate.binding_provenance(
        ticker="KXITFWMATCH-26AUG06YUALIN-LIN",
        player_name=title,
        event_title="Yuan vs Lin") == (
            "lt:172107", "lt:athlete:11402", "lt:athlete:1072",
            "Yu Jun Lin", "Chengyiyi Yuan")
    # Opposite YES must reverse cleanly for sibling packaging.
    opp_title = ("Will Chengyiyi Yuan win the Yuan vs Lin: "
                 "W15 Tianjin Quarterfinal match?")
    assert gate.binding_provenance(
        ticker="KXITFWMATCH-26AUG06YUALIN-YUA",
        player_name=opp_title,
        event_title="Yuan vs Lin") == (
            "lt:172107", "lt:athlete:1072", "lt:athlete:11402",
            "Chengyiyi Yuan", "Yu Jun Lin")
    print("PASS Kalshi ITF surname-only titles bind to Live Tennis cards")


def test_match_winner_binding_rejects_reversed_player_set_prop():
    """A player orientation does not turn a set prop into match-winner proof."""
    card = parse_competition({
        "id": "semantic-card",
        "status": {"type": {"state": "pre", "detail": "Scheduled"}},
        "competitors": [
            {"id": "ada", "athlete": {"displayName": "Ada Ace"}},
            {"id": "bea", "athlete": {"displayName": "Bea Break"}},
        ],
    }, "wta")

    class Cache:
        def matches(self, force=False):
            return (card,)

    gate = EspnProbGate(Config(), cache=Cache())
    assert gate.binding_identity(
        ticker="MATCH", player_name="Will Ada Ace win the Ace vs Break match?",
        event_title="Ada Ace vs Bea Break") == (
            "espn:semantic-card", "espn:athlete:ada", "espn:athlete:bea")
    assert gate.binding_identity(
        ticker="SET", player_name="Will Bea Break win the first set?",
        event_title="Ada Ace vs Bea Break") is None
    blocked = gate.decide(
        ticker="SET", player_name="Will Bea Break win the first set?",
        event_title="Ada Ace vs Bea Break", ask_cents=40)
    assert not blocked.allow
    assert "non_match_winner" in blocked.reason
    print("PASS only match-winner semantics can bind score orientation")


def test_scoreboard_caches_empty_and_bounds_stale_fallback():
    now = [100.0]
    calls = []

    def empty_fetch(_leagues):
        calls.append(now[0])
        return ()

    empty = EspnScoreboardCache(
        ttl_s=10, max_stale_s=30, clock=lambda: now[0], fetch=empty_fetch)
    assert empty.matches() == ()
    now[0] = 105
    assert empty.matches() == ()
    assert calls == [100.0]

    sample = parse_competition({
        "id": "cached", "status": {"type": {"state": "in"}},
        "competitors": [
            {"id": "a", "linescores": [{"value": 1}],
             "athlete": {"displayName": "Ada Ace"}},
            {"id": "b", "linescores": [{"value": 0}],
             "athlete": {"displayName": "Ben Break"}},
        ],
    }, "atp")
    results = [(sample,), RuntimeError("provider down"),
               RuntimeError("provider still down")]

    def flaky(_leagues):
        value = results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    stale = EspnScoreboardCache(
        ttl_s=10, max_stale_s=30, clock=lambda: now[0], fetch=flaky)
    now[0] = 200
    assert stale.matches() == (sample,)
    now[0] = 215
    assert stale.matches() == (sample,)
    now[0] = 231
    try:
        stale.matches()
    except RuntimeError as error:
        assert "stale" in str(error)
    else:
        raise AssertionError("stale scoreboard snapshot did not fail closed")
    print("PASS ESPN cache stores empty snapshots and bounds stale fallback")


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
        "timestamp": "2026-08-06T12:34:56Z",
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
    assert match.score_timestamp == 1786019696.0
    assert parse_match({**row, "is_doubles": True}) is None
    assert parse_match({**row, "status": "cancelled"}) is None
    print("PASS Live Tennis ITF match parse")


def test_live_cards_require_explicit_score_but_real_zero_zero_is_valid():
    """Missing score and an observed 0-0 are distinct provider states."""
    espn_base = {
        "id": "score-presence", "timestamp": 100.0,
        "status": {"type": {"state": "in", "detail": "1st Set"}},
        "competitors": [
            {"id": "ada", "athlete": {"displayName": "Ada Ace"}},
            {"id": "bea", "athlete": {"displayName": "Bea Break"}},
        ],
    }
    assert parse_competition(espn_base, "atp") is None
    observed = {
        **espn_base,
        "competitors": [
            {"id": "ada", "linescores": [{"value": 0}],
             "athlete": {"displayName": "Ada Ace"}},
            {"id": "bea", "linescores": [{"value": 0}],
             "athlete": {"displayName": "Bea Break"}},
        ],
    }
    espn_match = parse_competition(observed, "atp")
    assert espn_match is not None and espn_match.score_observed is True
    assert espn_match.games == (0, 0)

    live_base = {
        "id": 700, "tour": "itf", "format": "BO3", "status": "live",
        "is_doubles": False, "timestamp": _PRIOR_TEST_NOW,
        "players": {"p1": {"id": 1, "name": "Ada Ace"},
                    "p2": {"id": 2, "name": "Bea Break"}},
    }
    assert parse_match(live_base) is None
    live_match = parse_match({
        **live_base,
        "score": {"sets": [0, 0], "games": [[0], [0]]},
    })
    assert live_match is not None and live_match.score_observed is True
    assert live_match.games == (0, 0)
    print("PASS missing live scores fail closed while observed 0-0 remains valid")


def test_provider_games_are_strict_paired_nonnegative_integers():
    """Coercion or dropped rows must never move a score to another set."""
    espn = {
        "id": "strict-games", "timestamp": 100.0,
        "status": {"type": {"state": "in"}},
        "competitors": [
            {"id": "a", "athlete": {"displayName": "Ada Ace"},
             "linescores": [{"value": 6}, {"value": 1}]},
            {"id": "b", "athlete": {"displayName": "Bea Break"},
             "linescores": [{"value": "bad"}, {"value": 2}]},
        ],
    }
    assert parse_competition(espn, "atp") is None
    assert parse_competition({
        **espn,
        "competitors": [
            {**espn["competitors"][0], "linescores": [{"value": 1.5}]},
            {**espn["competitors"][1], "linescores": [{"value": 2}]},
        ],
    }, "atp") is None

    live = {
        "id": 701, "tour": "itf", "format": "BO3", "status": "live",
        "is_doubles": False, "timestamp": _PRIOR_TEST_NOW,
        "players": {"p1": {"id": 1, "name": "Ada Ace"},
                    "p2": {"id": 2, "name": "Bea Break"}},
        "score": {"sets": [0, 0], "games": [[1.5], [2]]},
    }
    assert parse_match(live) is None
    assert parse_match({
        **live, "score": {"sets": [0, 0], "games": [[True], [2]]},
    }) is None
    assert parse_match({
        **live, "score": {"sets": [0, 0], "games": [[-1], [2]]},
    }) is None
    assert parse_match({
        **live, "score": {"sets": [0, 0], "games": [[1, 2], [2]]},
    }) is None
    print("PASS score arrays reject coercion, negatives, and misalignment")


def test_duplicate_scorecards_reconcile_by_identity_and_newest_timestamp():
    """Route order must not retain an older conflicting scorecard."""
    def espn_payload(url):
        newer = "/wta/" in url
        return {"events": [{"groupings": [{"competitions": [{
            "id": "duplicate", "timestamp": 110.0 if newer else 100.0,
            "status": {"type": {"state": "in"}},
            "competitors": [
                {"id": "a", "athlete": {"displayName": "Ada Ace"},
                 "linescores": [{"value": 5 if newer else 1}]},
                {"id": "b", "athlete": {"displayName": "Bea Break"},
                 "linescores": [{"value": 0}]},
            ],
        }]}]}]}

    matches = fetch_live_matches(("atp", "wta"), get_json=espn_payload)
    assert len(matches) == 1
    assert matches[0].competitors[0].sets == (5,)
    assert matches[0].score_timestamp == 110.0

    def conflicting_identity(url):
        payload = espn_payload(url)
        if "/wta/" in url:
            payload["events"][0]["groupings"][0]["competitions"][0][
                "competitors"][0]["id"] = "other"
        return payload

    try:
        fetch_live_matches(("atp", "wta"), get_json=conflicting_identity)
    except ValueError as error:
        assert "duplicate competition identity" in str(error)
    else:
        raise AssertionError("conflicting duplicate ESPN identity was accepted")
    print("PASS duplicate scorecards select newest only after identity convergence")


def test_note_only_score_is_oriented_by_named_player_order():
    """ESPN note scores are ordered by the note's first named competitor."""
    match = parse_competition({
        "id": "note-order", "timestamp": 100.0,
        "status": {"type": {"state": "in"}},
        "notes": [{"text": "Bea Break leads Ada Ace 4-1"}],
        "competitors": [
            {"id": "ada", "athlete": {"displayName": "Ada Ace"}},
            {"id": "bea", "athlete": {"displayName": "Bea Break"}},
        ],
    }, "atp")
    assert match is not None
    assert match.games == (1, 4)

    class Cache:
        def matches(self, force=False):
            return (match,)

    decision = EspnProbGate(
        Config(), cache=Cache(), clock=lambda: 100.0).decide(
            ticker="T", player_name="Ada Ace",
            event_title="Ada Ace vs Bea Break", ask_cents=40)
    assert not decision.allow
    assert decision.model_prob is not None
    assert decision.model_prob < Decimal("0.35")
    print("PASS note-only score follows proven player ordering")


def test_sparse_live_tennis_score_uses_validated_sets_won():
    row = {
        "id": 5000, "tour": "itf", "format": "BO3", "status": "live",
        "is_doubles": False, "timestamp": _PRIOR_TEST_NOW,
        "players": {
            "p1": {"id": 1, "name": "Ada Ace"},
            "p2": {"id": 2, "name": "Ben Break"},
        },
        # Some provider cards publish the authoritative set score before the
        # per-set games arrays arrive.
        "score": {"sets": [1, 0], "games": [[], []]},
    }
    match = parse_match(row)
    assert match is not None

    class Cache:
        def matches(self, force=False):
            return (match,)

    gate = EspnProbGate(
        Config(), cache=Cache(), clock=lambda: _PRIOR_TEST_NOW,
        prematch_prior_provider=_prior_provider("0.5", "0.5"))
    decision = gate.decide(
        ticker="ITF-T", player_name="Ada Ace",
        event_title="Ada Ace vs Ben Break", ask_cents=50)
    assert decision.allow, decision.reason
    assert decision.model_prob is not None
    assert decision.model_prob > Decimal("0.70")

    assert parse_match({
        **row, "id": 5001,
        "score": {"sets": [-1, 0], "games": [[], []]},
    }) is None
    assert parse_match({
        **row, "id": 5002,
        "score": {"sets": [2, 2], "games": [[], []]},
    }) is None
    assert parse_match({
        **row, "id": 5003,
        "score": {"sets": [1.5, 0], "games": [[], []]},
    }) is None
    # A live BO3 card at two sets won is already terminal; stale provider
    # lifecycle must not leave it eligible for an entry.
    assert parse_match({
        **row, "id": 5004,
        "score": {"sets": [2, 0], "games": [[], []]},
    }) is None

    inconsistent = parse_match({
        **row, "id": 5005,
        # The linescore says Ada won the completed set, while the provider's
        # authoritative set score assigns it to Ben.
        "score": {"sets": [0, 1], "games": [[6], [4]]},
    })
    assert inconsistent is not None

    class InconsistentCache:
        def matches(self, force=False):
            return (inconsistent,)

    rejected = EspnProbGate(
        Config(), cache=InconsistentCache(),
        clock=lambda: _PRIOR_TEST_NOW).decide(
            ticker="ITF-T", player_name="Ada Ace",
            event_title="Ada Ace vs Ben Break", ask_cents=40)
    assert not rejected.allow
    assert "invalid_score" in rejected.reason
    print("PASS sparse authoritative sets-won is used and validated")


def test_live_tennis_paginates_with_bounds_and_caches_empty():
    urls = []

    def getter(url, **_kwargs):
        urls.append(url)
        query = parse_qs(urlparse(url).query)
        offset = int(query.get("offset", ["0"])[0])
        row = {
            "id": offset + 1, "tour": "itf", "format": "BO3",
            "status": "live", "is_doubles": False,
            "players": {"p1": {"name": f"Ada {offset}"},
                        "p2": {"name": f"Ben {offset}"}},
            "score": {"sets": [0, 0], "games": [[1], [0]]},
        }
        return {"data": [row], "meta": {"has_more": True}}

    matches = fetch_matches(
        api_key="key", tours=("itf",), statuses=("live",),
        get_json=getter, limit=500, max_pages=2)
    assert len(matches) == 2
    assert len(urls) == 2
    queries = [parse_qs(urlparse(url).query) for url in urls]
    assert [q["limit"] for q in queries] == [["200"], ["200"]]
    assert [q["offset"] for q in queries] == [["0"], ["200"]]

    now = [10.0]
    calls = []

    def empty_fetch(**_kwargs):
        calls.append(now[0])
        return ()

    cache = LiveTennisCache(
        api_key="key", ttl_s=120, max_stale_s=360,
        clock=lambda: now[0], fetch=empty_fetch)
    assert cache.matches() == ()
    now[0] = 100
    assert cache.matches() == ()
    assert calls == [10.0]
    print("PASS Live Tennis pagination is bounded and empty snapshots cache")


def test_gate_rejects_stale_provider_score_timestamp():
    match = parse_match({
        "id": 8, "tour": "itf", "format": "BO3", "status": "live",
        "is_doubles": False, "timestamp": 100.0,
        "players": {"p1": {"id": 1, "name": "Ada Ace"},
                    "p2": {"id": 2, "name": "Ben Break"}},
        "score": {"sets": [0, 0], "games": [[1], [0]]},
    })

    class Cache:
        def matches(self, force=False):
            return (match,)

    gate = EspnProbGate(
        Config(), cache=Cache(), clock=lambda: 200.0, max_score_age_s=30.0)
    decision = gate.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Ben Break", ask_cents=40)
    assert not decision.allow
    assert "stale_score" in decision.reason
    print("PASS provider timestamp freshness fails closed")


def test_live_gate_requires_finite_nonfuture_provider_timestamp():
    assert parse_provider_timestamp(float("nan")) is None
    assert parse_provider_timestamp(float("inf")) is None
    assert parse_provider_timestamp(float("-inf")) is None

    def card(timestamp_marker):
        row = {
            "id": 81, "tour": "itf", "format": "BO3", "status": "live",
            "is_doubles": False,
            "players": {"p1": {"id": 1, "name": "Ada Ace"},
                        "p2": {"id": 2, "name": "Ben Break"}},
            "score": {"sets": [0, 0], "games": [[1], [0]]},
        }
        if timestamp_marker is not None:
            row["timestamp"] = timestamp_marker
        return parse_match(row)

    for match in (card(None), card(201.0), card(float("nan"))):
        class Cache:
            def matches(self, force=False):
                return (match,)

        gate = EspnProbGate(
            Config(), cache=Cache(), clock=lambda: 200.0,
            max_score_age_s=30.0)
        decision = gate.decide(
            ticker="T", player_name="Ada Ace",
            event_title="Ada Ace vs Ben Break", ask_cents=40)
        assert not decision.allow
        assert "stale_score" in decision.reason
    print("PASS live scores require finite nonfuture provider timestamps")


def test_score_orientation_is_correct_when_player_ids_are_absent():
    match = parse_competition({
        "id": "no-athlete-ids", "timestamp": 100.0,
        "status": {"type": {"state": "in", "detail": "1st Set"}},
        "notes": [{"text": "Ada Ace trails Ben Break 1-4"}],
        "competitors": [
            {"athlete": {"displayName": "Ada Ace"}},
            {"athlete": {"displayName": "Ben Break"}},
        ],
    }, "atp")
    assert match is not None and match.games == (1, 4)

    class Cache:
        def matches(self, force=False):
            return (match,)

    gate = EspnProbGate(
        Config(), cache=Cache(), clock=lambda: 100.0)
    decision = gate.decide(
        ticker="T", player_name="Ben Break",
        event_title="Ada Ace vs Ben Break", ask_cents=40)
    assert not decision.allow
    assert "athlete_identity" in decision.reason
    # Parsing/orientation remains correct, but a trading decision requires
    # stable provider IDs so its score binding can be replayed exactly.
    assert gate._score_state(
        match, match.competitors[1], match.competitors[0]) == (0, 0, 4, 1)
    print("PASS score orientation is correct but missing IDs fail closed")


def test_prematch_state_expires_at_start_and_lifecycle_cannot_rewind():
    def card(state, timestamp=None):
        row = {
            "id": "lifecycle-card",
            "status": {"type": {"state": state, "detail": state}},
            "competitors": [
                {"id": "ada", "athlete": {"displayName": "Ada Ace"}},
                {"id": "bea", "athlete": {"displayName": "Bea Break"}},
            ],
        }
        if timestamp is not None:
            row["timestamp"] = timestamp
        if state == "in":
            for competitor in row["competitors"]:
                competitor["linescores"] = [{"value": 0}]
        return parse_competition(row, "wta")

    now = [100.0]

    class Cache:
        value = card("pre")

        def matches(self, force=False):
            return (self.value,)

    cache = Cache()
    expired = EspnProbGate(
        Config(), cache=cache, clock=lambda: now[0]).decide(
            ticker="T", player_name="Ada Ace",
            event_title="Ada Ace vs Bea Break", ask_cents=40,
            scheduled_start_ts=100)
    assert not expired.allow
    assert "scheduled_start" in expired.reason

    gate = EspnProbGate(Config(), cache=cache, clock=lambda: now[0])
    before = gate.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Bea Break", ask_cents=40,
        scheduled_start_ts=200)
    assert before.allow
    now[0] = 110
    cache.value = card("in", timestamp=110)
    live = gate.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Bea Break", ask_cents=40,
        scheduled_start_ts=200)
    assert live.allow
    now[0] = 120
    cache.value = card("pre")
    rewound = gate.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Bea Break", ask_cents=40,
        scheduled_start_ts=200)
    assert not rewound.allow
    assert "lifecycle_rewind" in rewound.reason
    print("PASS scheduled start expires pre state and lifecycle cannot rewind")


def test_terminal_scorecard_cannot_resurrect_as_live():
    now = [100.0]

    def card(state):
        completed = state == "post"
        return parse_competition({
            "id": "terminal-card", "timestamp": now[0],
            "status": {"type": {"state": state, "detail": state}},
            "competitors": [
                {"id": "ada", "linescores": (
                    [{"value": 6}, {"value": 6}]
                    if completed else [{"value": 0}]),
                 "athlete": {"displayName": "Ada Ace"}},
                {"id": "bea", "linescores": (
                    [{"value": 4}, {"value": 4}]
                    if completed else [{"value": 0}]),
                 "athlete": {"displayName": "Bea Break"}},
            ],
        }, "wta")

    class Cache:
        value = card("post")

        def matches(self, force=False):
            return (self.value,)

    cache = Cache()
    gate = EspnProbGate(Config(), cache=cache, clock=lambda: now[0])
    terminal = gate.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Bea Break", ask_cents=40)
    assert not terminal.allow
    assert "match_over" in terminal.reason

    now[0] = 101.0
    cache.value = card("in")
    resurrected = gate.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Bea Break", ask_cents=40)
    assert not resurrected.allow
    assert "lifecycle_rewind" in resurrected.reason
    print("PASS terminal scorecard cannot resurrect as live")


def test_live_score_progress_cannot_rewind():
    now = [100.0]

    def card(a_sets, b_sets):
        return parse_competition({
            "id": "score-rewind", "timestamp": now[0],
            "status": {"type": {"state": "in", "detail": "live"}},
            "competitors": [
                {"id": "ada", "linescores": [
                    {"value": value} for value in a_sets],
                 "athlete": {"displayName": "Ada Ace"}},
                {"id": "bea", "linescores": [
                    {"value": value} for value in b_sets],
                 "athlete": {"displayName": "Bea Break"}},
            ],
        }, "wta")

    class Cache:
        value = card((6, 5), (4, 0))

        def matches(self, force=False):
            return (self.value,)

    cache = Cache()
    gate = EspnProbGate(Config(), cache=cache, clock=lambda: now[0])
    first = gate.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Bea Break", ask_cents=40)
    assert first.allow, first.reason
    assert (first.score_sets_for, first.score_games_for) == (1, 5)

    now[0] = 101.0
    cache.value = card((0,), (0,))
    rewound = gate.decide(
        ticker="T", player_name="Ada Ace",
        event_title="Ada Ace vs Bea Break", ask_cents=40)
    assert not rewound.allow
    assert "score_progress_rewind" in rewound.reason
    print("PASS live score progress cannot rewind")


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


def test_mid_rise_in_lookback_and_sibling_spike_block():
    from decimal import Decimal
    from types import SimpleNamespace
    from market_data import PriceFeed
    from engine import Context, _sibling_spike_block
    from sports_discovery import ContractProvenance, SelectedContract, DiscoveryResult

    def contract(ticker, event):
        return SelectedContract(
            ticker=ticker, title=ticker, game_title="game",
            bid=Decimal(50), ask=Decimal(51),
            bid_size=Decimal(10), ask_size=Decimal(10),
            provenance=ContractProvenance(
                sport="Tennis", league=None, series_ticker="KXITF",
                milestone_id="m", event_ticker=event,
                scheduled_start_ts=1.0))

    discovery = DiscoveryResult(
        contracts=(contract("FAV", "E1"),),
        selected_sports=("Tennis",),
        local_timezone="UTC",
        session_start_local="1970-01-01T00:00:00+00:00",
        session_end_local="1970-01-02T00:00:00+00:00",
        session_start_utc=0.0, session_end_utc=86400.0,
        stats={"selected": 1},
        watch_contracts=(contract("DOG", "E1"),),
    )
    feed = PriceFeed(Config(), client=object())
    feed.install_discovery(discovery)
    feed.history["DOG"].extend((
        (100.0, Decimal("5")),
        (110.0, Decimal("20")),
        (120.0, Decimal("40")),
    ))
    assert feed.mid_rise_in_lookback("DOG", 120.0, 45.0) == Decimal("35")
    assert feed.sibling_tickers("FAV") == ("DOG",)

    cfg = Config(sibling_spike_enabled=True, sibling_spike_cents=15,
                 sibling_spike_lookback_s=45)
    ctx = Context(cfg, feed, strategy=SimpleNamespace(positions={}),
                  executor=SimpleNamespace(pending_paper=[]),
                  log=None, safety=None)
    reason = _sibling_spike_block(ctx, "FAV", 120.0)
    assert reason is not None and "sibling_spike" in reason and "DOG" in reason
    assert "+35" in reason or "+35.0" in reason

    cfg_off = Config(sibling_spike_enabled=False, sibling_spike_cents=15)
    ctx_off = Context(cfg_off, feed, strategy=SimpleNamespace(positions={}),
                      executor=SimpleNamespace(pending_paper=[]),
                      log=None, safety=None)
    assert _sibling_spike_block(ctx_off, "FAV", 120.0) is None
    print("PASS sibling mid-rise spike block")


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
                "timestamp": 100.0,
                "players": {
                    "p1": {"id": 1, "name": "Adrian Oetzbach"},
                    "p2": {"id": 2, "name": "Jonas Fix"},
                },
                "score": {"sets": [0, 0], "games": [[2], [1]], "server": 1},
            }),)

    assert ticker_wants_live_tennis("KXITFMATCH-FOO", "Oetzbach vs Fix")
    assert not ticker_wants_live_tennis("KXATPMATCH-FOO", "Fils vs Navone")

    gate = EspnProbGate(
        cfg, cache=EmptyEspn(), live_tennis_cache=FakeLiveTennis(),
        clock=lambda: 100.0)
    # Non-ITF ticker must not consult Live Tennis → still unbound.
    atp_block = gate.decide(
        ticker="KXATPMATCH-X", player_name="Adrian Oetzbach",
        event_title="Adrian Oetzbach vs Jonas Fix", ask_cents=40)
    assert not atp_block.allow
    assert "no_espn_bind" in atp_block.reason

    ok = gate.decide(
        ticker="KXITFMATCH-X", player_name="Adrian Oetzbach",
        event_title="Adrian Oetzbach vs Jonas Fix", ask_cents=40)
    assert ok.allow, ok.reason
    assert ok.espn_match_id == "lt:99"
    assert "live_tennis_score_guard_only" in ok.reason
    print("PASS Live Tennis secondary ITF bind")


if __name__ == "__main__":
    test_names_match_surname()
    test_neutral_model_rejects_match_already_lost()
    test_parse_espn_competition_live()
    test_espn_competition_requires_nonempty_identity()
    test_espn_ignores_route_periods_and_deduplicates_competition_ids()
    test_major_qualifying_is_not_inferred_as_best_of_five()
    test_completed_set_is_not_reused_as_current_games()
    test_gate_blocks_unbound_and_allows_edge()
    test_two_model_prior_is_updated_from_score_conservatively()
    test_configured_prior_provider_is_strict_and_accepts_store_object()
    test_active_match_pins_prematch_baseline_and_emits_replay_state()
    test_pinned_prematch_prior_still_expires()
    test_sibling_score_prioritizes_entry_eligibility_before_raw_edge()
    test_binding_requires_identity_opponent_and_unique_match()
    test_binding_requires_opponent_first_identity_not_just_surname()
    test_binding_accepts_kalshi_itf_surname_only_event_titles()
    test_match_winner_binding_rejects_reversed_player_set_prop()
    test_scoreboard_caches_empty_and_bounds_stale_fallback()
    test_parse_live_tennis_match_itf()
    test_live_cards_require_explicit_score_but_real_zero_zero_is_valid()
    test_provider_games_are_strict_paired_nonnegative_integers()
    test_duplicate_scorecards_reconcile_by_identity_and_newest_timestamp()
    test_note_only_score_is_oriented_by_named_player_order()
    test_sparse_live_tennis_score_uses_validated_sets_won()
    test_live_tennis_paginates_with_bounds_and_caches_empty()
    test_gate_rejects_stale_provider_score_timestamp()
    test_live_gate_requires_finite_nonfuture_provider_timestamp()
    test_score_orientation_is_correct_when_player_ids_are_absent()
    test_prematch_state_expires_at_start_and_lifecycle_cannot_rewind()
    test_terminal_scorecard_cannot_resurrect_as_live()
    test_live_score_progress_cannot_rewind()
    test_display_player_name_from_kalshi_title()
    test_rank_contracts_prefer_bind_tiers()
    test_mid_rise_in_lookback_and_sibling_spike_block()
    test_gate_binds_itf_via_live_tennis_secondary()
    print("\nALL ESPN GATE TESTS PASS")
