from __future__ import annotations

import copy
from dataclasses import replace
import json
import unittest


def _context() -> object:
    from inci_tennis_adapters.live_score_candidates import LiveScoreCaptureContext
    from inci_tennis_expert.contracts import MatchFormat

    return LiveScoreCaptureContext(
        provider_source_id="candidate-provider",
        revision_domain_id="candidate-revisions",
        source_lineage_sha256="a" * 64,
        provider_match_id="17",
        home_player_id="101",
        away_player_id="102",
        scheduled_start_wall_ns=1_894_726_800_000_000_000,
        match_format=MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        local_capture_wall_ns=1_894_730_000_000_000_000,
        local_capture_monotonic_ns=800,
        local_clock_uncertainty_ns=2,
        raw_capture_id="raw-17",
        lineage_independence_proven=None,
    )


API_TENNIS_LIVE = {
    "success": 1,
    "result": [{
        "event_key": "17",
        "first_player_key": "101",
        "second_player_key": "102",
        "event_status": "Set 2",
        "event_type_type": "ITF Men Singles",
        "event_serve": "First Player",
        "event_game_result": "30 - 15",
        "event_winner": None,
        "pointbypoint": [{
            "set_number": "Set 1",
            "number_game": "1",
            "player_served": "First Player",
            "serve_winner": "First Player",
            "serve_lost": None,
            "score": "1 - 0",
            "points": [{
                "number_point": "1", "score": "15 - 0",
                "break_point": None, "set_point": None, "match_point": None,
            }],
        }],
        "scores": [
            {"score_first": "6", "score_second": "3", "score_set": "1"},
            {"score_first": "4", "score_second": "2", "score_set": "2"},
        ],
    }],
}

GOALSERVE_LIVE = b"""<?xml version="1.0" encoding="UTF-8"?>
<scores><tournament name="ATP Sample"><matches><match status="2nd Set" id="17">
<player name="A. Home" serve="True" game_score="30" sets_won="1" set1="6" set2="4" set3="0" set4="0" set5="0" winner="False" id="101"/>
<player name="B. Away" serve="False" game_score="15" sets_won="0" set1="3" set2="2" set3="0" set4="0" set5="0" winner="False" id="102"/>
</match></matches></tournament></scores>"""

LIVE_TENNIS_API_LIVE = {
    "id": 17,
    "status": "live",
    "format": "BO3",
    "is_doubles": False,
    "players": {"p1": {"id": 101}, "p2": {"id": 102}},
    "score": {
        "sets": [1, 0],
        "games": [[6, 4], [3, 2]],
        "points": ["30", "15"],
        "server": 1,
        "is_tiebreak": False,
        "timestamp": "2030-01-01T12:00:00Z",
    },
    "winner": None,
}


def _payload(fixture: object) -> bytes:
    if type(fixture) is bytes:
        return fixture
    return json.dumps(fixture, separators=(",", ":")).encode("utf-8")


class LiveScoreCandidateTests(unittest.TestCase):
    """Sanitized fixtures mirror the reviewed official wire layouts."""

    def _parse(self, provider: str, fixture: object) -> object:
        from inci_tennis_adapters.live_score_candidates import parse_live_score

        return parse_live_score(provider, _payload(fixture), _context())

    def test_official_shaped_live_fixtures_preserve_facts_and_abstain(self) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason
        from inci_tennis_expert.contracts import MatchStatus, PlayerSide, ScoreValue

        for provider, fixture in {
            "api_tennis": API_TENNIS_LIVE,
            "goalserve": GOALSERVE_LIVE,
            "live_tennis_api": LIVE_TENNIS_API_LIVE,
        }.items():
            with self.subTest(provider=provider):
                result = self._parse(provider, fixture)
                self.assertIsNone(result.snapshot)
                self.assertEqual(result.abstention, AbstentionReason.MISSING_PROVIDER_REVISION)
                self.assertEqual(result.facts.status, MatchStatus.LIVE)
                self.assertEqual((result.facts.games_home, result.facts.games_away), (4, 2))
                self.assertEqual((result.facts.points_home, result.facts.points_away), (ScoreValue.THIRTY, ScoreValue.FIFTEEN))
                self.assertEqual(result.facts.server_for_next_point, PlayerSide.HOME)
                self.assertEqual(len(result.raw_sha256), 64)
                self.assertIsNone(result.lineage_independence_proven)
        api_facts = self._parse("api_tennis", API_TENNIS_LIVE).facts
        self.assertEqual(len(api_facts.point_by_point), 1)
        self.assertEqual(api_facts.point_by_point[0].server, PlayerSide.HOME)
        self.assertEqual(len(api_facts.point_by_point[0].points), 1)
        self.assertIsNone(self._parse("api_tennis", API_TENNIS_LIVE).facts.source_generated_wall_ns)
        self.assertIsNotNone(self._parse("live_tennis_api", LIVE_TENNIS_API_LIVE).facts.source_generated_wall_ns)

    def test_api_tennis_selects_exact_context_match_from_multi_event_result(self) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason

        before = copy.deepcopy(API_TENNIS_LIVE["result"][0])
        before["event_key"] = "16"
        after = copy.deepcopy(before)
        after["event_key"] = "18"
        fixture = {"success": 1, "result": [before, API_TENNIS_LIVE["result"][0], after]}
        self.assertEqual(self._parse("api_tennis", fixture).facts.home_player_id, "101")

        wrong_orientation = copy.deepcopy(API_TENNIS_LIVE["result"][0])
        wrong_orientation["first_player_key"] = "999"
        for result, expected in (
            ([before, after], AbstentionReason.MATCH_NOT_FOUND),
            ([before, API_TENNIS_LIVE["result"][0], copy.deepcopy(API_TENNIS_LIVE["result"][0]), after], AbstentionReason.DUPLICATE_MATCH),
            ([before, wrong_orientation, after], AbstentionReason.IDENTITY_MISMATCH),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(self._parse("api_tennis", {"success": 1, "result": result}).abstention, expected)

    def test_goalserve_selects_exact_context_match_from_multi_match_feed(self) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason

        target = GOALSERVE_LIVE.replace(b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<scores><tournament name=\"ATP Sample\"><matches>", b"").replace(b"</matches></tournament></scores>", b"")
        before = target.replace(b'id="17"', b'id="16"')
        after = target.replace(b'id="17"', b'id="18"')
        def feed(matches: bytes) -> bytes:
            return b'<scores><tournament name="first"><matches>' + matches + b'</matches></tournament></scores>'

        self.assertEqual(self._parse("goalserve", feed(before + target + after)).facts.away_player_id, "102")
        wrong_orientation = target.replace(b'id="102"', b'id="999"')
        for matches, expected in (
            (before + after, AbstentionReason.MATCH_NOT_FOUND),
            (before + target + target + after, AbstentionReason.DUPLICATE_MATCH),
            (before + wrong_orientation + after, AbstentionReason.IDENTITY_MISMATCH),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(self._parse("goalserve", feed(matches)).abstention, expected)

    def test_standard_bo3_live_positions_are_supported(self) -> None:
        from inci_tennis_expert.contracts import MatchStatus

        first = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        first["score"].update({"sets": [0, 0], "games": [[4], [2]]})
        third = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        third["score"].update({"sets": [1, 1], "games": [[6, 4, 2], [3, 6, 1]]})
        for fixture, completed_count, games in ((first, 0, (4, 2)), (LIVE_TENNIS_API_LIVE, 1, (4, 2)), (third, 2, (2, 1))):
            with self.subTest(completed_count=completed_count):
                result = self._parse("live_tennis_api", fixture)
                self.assertEqual(result.facts.status, MatchStatus.LIVE)
                self.assertEqual(len(result.facts.completed_sets), completed_count)
                self.assertEqual((result.facts.games_home, result.facts.games_away), games)

    def test_completed_two_set_and_three_set_bo3_are_supported(self) -> None:
        from inci_tennis_expert.contracts import MatchStatus, PlayerSide

        two = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        two.update({"status": "completed", "winner": 1})
        two["score"].update({"sets": [2, 0], "games": [[6, 6], [3, 4]], "points": [None, None], "server": None})
        three = copy.deepcopy(two)
        three["score"].update({"sets": [2, 1], "games": [[6, 4, 6], [3, 6, 2]]})
        for fixture, count in ((two, 2), (three, 3)):
            with self.subTest(count=count):
                result = self._parse("live_tennis_api", fixture)
                self.assertEqual(result.facts.status, MatchStatus.ENDED)
                self.assertEqual(result.facts.winner, PlayerSide.HOME)
                self.assertEqual(len(result.facts.completed_sets), count)

    def test_goalserve_fin_and_susp_statuses_use_player_attributes(self) -> None:
        from inci_tennis_expert.contracts import MatchStatus, PlayerSide

        finished = GOALSERVE_LIVE.replace(b'status="2nd Set"', b'status="Fin."').replace(b'sets_won="1" set1="6" set2="4"', b'sets_won="2" set1="6" set2="6"').replace(b'sets_won="0" set1="3" set2="2"', b'sets_won="0" set1="3" set2="4"').replace(b'winner="False" id="101"', b'winner="True" id="101"')
        suspended = GOALSERVE_LIVE.replace(b'status="2nd Set"', b'status="Susp."')
        result = self._parse("goalserve", finished)
        self.assertEqual(result.facts.status, MatchStatus.ENDED)
        self.assertEqual(result.facts.winner, PlayerSide.HOME)
        self.assertEqual(len(result.facts.completed_sets), 2)
        self.assertEqual(self._parse("goalserve", suspended).facts.status, MatchStatus.SUSPENDED)

    def test_completed_official_shapes_cover_each_provider(self) -> None:
        from inci_tennis_expert.contracts import MatchStatus, PlayerSide

        api = copy.deepcopy(API_TENNIS_LIVE)
        api["result"][0].update({"event_status": "Finished", "event_winner": "First Player", "event_game_result": "-", "scores": [{"score_first": "6", "score_second": "3", "score_set": "1"}, {"score_first": "6", "score_second": "4", "score_set": "2"}]})
        goal = GOALSERVE_LIVE.replace(b'status="2nd Set"', b'status="Fin."').replace(b'sets_won="1" set1="6" set2="4"', b'sets_won="2" set1="6" set2="6"').replace(b'sets_won="0" set1="3" set2="2"', b'sets_won="0" set1="3" set2="4"').replace(b'winner="False" id="101"', b'winner="True" id="101"')
        live = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        live.update({"status": "completed", "winner": 1})
        live["score"].update({"sets": [2, 0], "games": [[6, 6], [3, 4]], "points": [None, None], "server": None})
        for provider, fixture in {"api_tennis": api, "goalserve": goal, "live_tennis_api": live}.items():
            with self.subTest(provider=provider):
                result = self._parse(provider, fixture)
                self.assertEqual(result.facts.status, MatchStatus.ENDED)
                self.assertEqual(result.facts.winner, PlayerSide.HOME)
                self.assertEqual(len(result.facts.completed_sets), 2)

    def test_missing_server_keeps_useful_facts_for_each_provider(self) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason

        api = copy.deepcopy(API_TENNIS_LIVE)
        api["result"][0]["event_serve"] = None
        goal = GOALSERVE_LIVE.replace(b'serve="True"', b'serve="False"')
        live = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        live["score"]["server"] = None
        for provider, fixture in {"api_tennis": api, "goalserve": goal, "live_tennis_api": live}.items():
            with self.subTest(provider=provider):
                result = self._parse(provider, fixture)
                self.assertIsNotNone(result.facts)
                self.assertEqual(result.abstention, AbstentionReason.MISSING_SERVER)

    def test_documented_doubles_and_tiebreak_forms_are_explicitly_abstained(self) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason

        api = copy.deepcopy(API_TENNIS_LIVE)
        api["result"][0]["event_type_type"] = "ITF Men Doubles"
        goal_doubles = GOALSERVE_LIVE.replace(b'id="101"', b'id1="101" id2="103"')
        goal_tiebreak = GOALSERVE_LIVE.replace(b'set2="4"', b'set2="6.5"')
        live = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        live["is_doubles"] = True
        cases = (("api_tennis", api, AbstentionReason.DOUBLES), ("goalserve", goal_doubles, AbstentionReason.DOUBLES), ("goalserve", goal_tiebreak, AbstentionReason.UNSUPPORTED_TIEBREAK), ("live_tennis_api", live, AbstentionReason.DOUBLES))
        for provider, fixture, expected in cases:
            with self.subTest(provider=provider, expected=expected):
                self.assertEqual(self._parse(provider, fixture).abstention, expected)

    def test_status_schema_access_and_identity_matrix_is_explicit(self) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason

        api_suspended = copy.deepcopy(API_TENNIS_LIVE)
        api_suspended["result"][0]["event_status"] = "Suspended"
        goal_suspended = GOALSERVE_LIVE.replace(b'status="2nd Set"', b'status="Susp."')
        lta_unknown_status = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        lta_unknown_status["status"] = "suspended"
        cases = (
            ("api_tennis", api_suspended, None),
            ("goalserve", goal_suspended, None),
            ("live_tennis_api", lta_unknown_status, AbstentionReason.UNKNOWN_STATUS),
            ("api_tennis", {"success": 1, "result": []}, AbstentionReason.MATCH_NOT_FOUND),
            ("goalserve", b"<not_scores/>", AbstentionReason.UNKNOWN_SCHEMA),
            ("live_tennis_api", [], AbstentionReason.UNKNOWN_SCHEMA),
            ("api_tennis", {"success": 0, "error": "access denied"}, AbstentionReason.ACCESS_DENIED),
            ("goalserve", b'<scores error="access denied"/>', AbstentionReason.ACCESS_DENIED),
            ("live_tennis_api", {"detail": "access denied"}, AbstentionReason.ACCESS_DENIED),
        )
        for provider, fixture, expected in cases:
            with self.subTest(provider=provider, expected=expected):
                result = self._parse(provider, fixture)
                if expected is None:
                    self.assertEqual(result.facts.status.value, "suspended")
                else:
                    self.assertEqual(result.abstention, expected)

        api = copy.deepcopy(API_TENNIS_LIVE)
        api["result"][0]["first_player_key"] = "999"
        goal = GOALSERVE_LIVE.replace(b'id="102"', b'id="999"')
        live = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        live["players"]["p2"]["id"] = 999
        for provider, fixture in {"api_tennis": api, "goalserve": goal, "live_tennis_api": live}.items():
            with self.subTest(provider=provider, expected="identity"):
                self.assertEqual(self._parse(provider, fixture).abstention, AbstentionReason.IDENTITY_MISMATCH)

    def test_ambiguous_and_tiebreak_matrix_is_explicit(self) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason

        api = copy.deepcopy(API_TENNIS_LIVE)
        api["result"][0]["scores"].append({"score_first": "0", "score_second": "0", "score_set": "3"})
        goal = GOALSERVE_LIVE.replace(b'set2="4"', b'set2="6.5"')
        live = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        live["score"]["is_tiebreak"] = True
        for provider, fixture, expected in (("api_tennis", api, AbstentionReason.AMBIGUOUS_CURRENT_SET), ("goalserve", goal, AbstentionReason.UNSUPPORTED_TIEBREAK), ("live_tennis_api", live, AbstentionReason.UNSUPPORTED_TIEBREAK)):
            with self.subTest(provider=provider):
                self.assertEqual(self._parse(provider, fixture).abstention, expected)

    def test_live_tennis_openapi_format_status_arrays_timestamp_and_additive_fields(self) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason, LiveScoreParseError

        bo5 = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        bo5["format"] = "BO5"
        self.assertEqual(self._parse("live_tennis_api", bo5).abstention, AbstentionReason.BO5)
        completed = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        completed["status"] = "completed"
        completed["winner"] = 1
        completed["score"].update({"sets": [2, 0], "games": [[6, 6], [3, 4]], "points": [None, None], "server": None})
        completed["extra_provider_field"] = {"allowed": [1, 2]}
        self.assertEqual(self._parse("live_tennis_api", completed).facts.status.value, "ended")
        bad_time = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        bad_time["score"]["timestamp"] = 1_700_000_000
        with self.assertRaisesRegex(LiveScoreParseError, r"\Aimpossible_score\Z"):
            self._parse("live_tennis_api", bad_time)
        offset_time = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        offset_time["score"]["timestamp"] = "2030-01-01T12:00:00+01:00"
        fractional_overflow = copy.deepcopy(LIVE_TENNIS_API_LIVE)
        fractional_overflow["score"]["timestamp"] = "2030-01-01T12:00:00.1234567890Z"
        for fixture in (offset_time, fractional_overflow):
            with self.subTest(timestamp=fixture["score"]["timestamp"]):
                with self.assertRaisesRegex(LiveScoreParseError, r"\Aimpossible_score\Z"):
                    self._parse("live_tennis_api", fixture)

    def test_malformed_trees_numeric_forms_and_errors_are_bounded_and_sanitized(self) -> None:
        from inci_tennis_adapters.live_score_candidates import LiveScoreParseError

        deep_json = (b'{"x":' * 80) + b"0" + (b"}" * 80)
        deep_xml = (b"<x>" * 80) + (b"</x>" * 80)
        cases = (
            ("api_tennis", b'{"success":1,"success":1,"result":[]}', "duplicate_json_key"),
            ("api_tennis", b'{"success":NaN,"result":[]}', "non_finite_number"),
            ("api_tennis", b'{"success":1e309,"result":[]}', "non_finite_number"),
            (
                "api_tennis",
                b'{"success":' + (b"1" * 5_000) + b',"result":[]}',
                "malformed_payload",
            ),
            ("api_tennis", deep_json, "malformed_payload"),
            ("goalserve", deep_xml, "malformed_payload"),
            ("goalserve", b"<scores><token>secret-value</token></scores>", "secret_material"),
            ("api_tennis", b"\xff", "malformed_payload"),
        )
        for provider, payload, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(LiveScoreParseError, rf"\A{code}\Z") as raised:
                    self._parse(provider, payload)
                self.assertIsNone(raised.exception.__cause__)
                self.assertNotIn("secret-value", repr(raised.exception))

    def test_capture_context_rejects_invalid_identity_and_clocks(self) -> None:
        for changes in (
            {"away_player_id": "101"},
            {"scheduled_start_wall_ns": 0},
            {"local_capture_wall_ns": 0},
            {"local_capture_monotonic_ns": -1},
            {"local_clock_uncertainty_ns": -1},
            {"lineage_independence_proven": 0},
            {"lineage_independence_proven": 1},
        ):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, r"\Ainvalid_capture_context\Z"):
                    replace(_context(), **changes)

    def test_point_tape_payload_text_is_not_exposed_by_repr(self) -> None:
        fixture = copy.deepcopy(API_TENNIS_LIVE)
        fixture["result"][0]["pointbypoint"][0]["points"][0][
            "score"
        ] = "Bearer supersecret"

        result = self._parse("api_tennis", fixture)

        self.assertNotIn("supersecret", repr(result))
        self.assertNotIn("supersecret", repr(result.facts))
        self.assertNotIn("supersecret", repr(result.facts.point_by_point))


if __name__ == "__main__":
    unittest.main()
