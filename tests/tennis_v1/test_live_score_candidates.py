from __future__ import annotations

import json
import unittest


def _context() -> object:
    from inci_tennis_adapters.live_score_candidates import (
        LiveScoreCaptureContext,
    )
    from inci_tennis_expert.contracts import MatchFormat

    return LiveScoreCaptureContext(
        provider_source_id="candidate-provider",
        revision_domain_id="candidate-revisions",
        source_lineage_sha256="a" * 64,
        provider_match_id="match-17",
        home_player_id="player-1",
        away_player_id="player-2",
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
    "result": [
        {
            "event_key": "match-17",
            "first_player_key": "player-1",
            "second_player_key": "player-2",
            "event_status": "Set 2",
            "event_serving_player": "1",
            "event_game_result": "30 - 15",
            "pointbypoint": [],
            "sets": [["6", "3"], ["4", "2"]],
        }
    ],
}
GOALSERVE_LIVE = b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<scores><tournament><matches><match id=\"match-17\" status=\"2nd Set\" serve=\"1\" game_score=\"30-15\" sets_won=\"1-0\"><player id=\"player-1\" set1=\"6\" set2=\"4\"/><player id=\"player-2\" set1=\"3\" set2=\"2\"/></match></matches></tournament></scores>"""
LIVE_TENNIS_API_LIVE = {
    "id": "match-17",
    "status": "live",
    "format": "best_of_3",
    "is_doubles": False,
    "players": {"p1": {"id": "player-1"}, "p2": {"id": "player-2"}},
    "score": {
        "sets": [{"p1": 6, "p2": 3}],
        "games": {"p1": 4, "p2": 2},
        "points": {"p1": "30", "p2": "15"},
        "server": "p1",
        "is_tiebreak": False,
        "timestamp": "2030-01-01T12:00:00Z",
    },
    "winner": None,
}


def _payload(provider: str, fixture: object) -> bytes:
    if type(fixture) is bytes:
        assert type(fixture) is bytes
        return fixture
    return json.dumps(fixture, separators=(",", ":")).encode("utf-8")


class LiveScoreCandidateTests(unittest.TestCase):
    """Every fixture is public-shaped and deliberately lacks trust provenance."""

    def _parse(self, provider: str, payload: bytes) -> object:
        from inci_tennis_adapters.live_score_candidates import parse_live_score

        return parse_live_score(provider, payload, _context())

    def test_normal_live_fixtures_preserve_facts_and_abstain_without_provenance(
        self,
    ) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason
        from inci_tennis_expert.contracts import MatchStatus, PlayerSide, ScoreValue

        fixtures = {
            "api_tennis": API_TENNIS_LIVE,
            "goalserve": GOALSERVE_LIVE,
            "live_tennis_api": LIVE_TENNIS_API_LIVE,
        }
        for provider, fixture in fixtures.items():
            with self.subTest(provider=provider):
                result = self._parse(provider, _payload(provider, fixture))
                self.assertIsNone(result.snapshot)
                self.assertEqual(result.facts.status, MatchStatus.LIVE)
                self.assertEqual(result.facts.games_home, 4)
                self.assertEqual(result.facts.games_away, 2)
                self.assertEqual(result.facts.points_home, ScoreValue.THIRTY)
                self.assertEqual(result.facts.points_away, ScoreValue.FIFTEEN)
                self.assertEqual(result.facts.server_for_next_point, PlayerSide.HOME)
                self.assertEqual(
                    result.abstention,
                    AbstentionReason.MISSING_PROVIDER_REVISION,
                )
                self.assertEqual(len(result.raw_sha256), 64)
                self.assertFalse(result.lineage_independence_proven)
                self.assertIn(
                    AbstentionReason.MISSING_CORRECTION_SEMANTICS,
                    result.diagnostics,
                )
                self.assertIn(
                    AbstentionReason.MISSING_SOURCE_EVENT_ID,
                    result.diagnostics,
                )
                if provider == "live_tennis_api":
                    self.assertIsNotNone(result.facts.source_generated_wall_ns)
                else:
                    self.assertIsNone(result.facts.source_generated_wall_ns)
                    self.assertIn(
                        AbstentionReason.MISSING_SOURCE_GENERATED_TIME,
                        result.diagnostics,
                    )

    def test_completed_fixtures_preserve_terminal_result_and_abstain(
        self,
    ) -> None:
        from inci_tennis_expert.contracts import (
            MatchStatus,
            PlayerSide,
            TerminationKind,
        )

        api = json.loads(json.dumps(API_TENNIS_LIVE))
        api["result"][0].update(
            {"event_status": "Finished", "event_winner": "1", "sets": [["6", "3"], ["6", "4"]]}
        )
        goalserve = GOALSERVE_LIVE.replace(
            b'status="2nd Set" serve="1" game_score="30-15" sets_won="1-0"',
            b'status="Finished" sets_won="2-0" winner="1"',
        ).replace(b'set2="4"', b'set2="6"').replace(b'set2="2"', b'set2="4"')
        live_tennis = json.loads(json.dumps(LIVE_TENNIS_API_LIVE))
        live_tennis.update({"status": "finished", "winner": "p1"})
        live_tennis["score"] = {
            "sets": [{"p1": 6, "p2": 3}, {"p1": 6, "p2": 4}],
            "games": {"p1": 0, "p2": 0},
            "points": {"p1": "0", "p2": "0"},
            "server": None,
            "is_tiebreak": False,
            "timestamp": "2030-01-01T12:00:00Z",
        }
        for provider, fixture in {
            "api_tennis": api,
            "goalserve": goalserve,
            "live_tennis_api": live_tennis,
        }.items():
            with self.subTest(provider=provider):
                result = self._parse(provider, _payload(provider, fixture))
                self.assertEqual(result.facts.status, MatchStatus.ENDED)
                self.assertEqual(result.facts.termination_kind, TerminationKind.NATURAL)
                self.assertEqual(result.facts.winner, PlayerSide.HOME)
                self.assertEqual(len(result.facts.completed_sets), 2)
                self.assertIsNone(result.snapshot)

    def test_suspended_fixtures_are_not_trusted_snapshots(self) -> None:
        from inci_tennis_expert.contracts import MatchStatus

        api = json.loads(json.dumps(API_TENNIS_LIVE))
        api["result"][0]["event_status"] = "Suspended"
        goalserve = GOALSERVE_LIVE.replace(b'2nd Set', b'Suspended')
        live_tennis = json.loads(json.dumps(LIVE_TENNIS_API_LIVE))
        live_tennis["status"] = "suspended"
        for provider, fixture in {
            "api_tennis": api,
            "goalserve": goalserve,
            "live_tennis_api": live_tennis,
        }.items():
            with self.subTest(provider=provider):
                result = self._parse(provider, _payload(provider, fixture))
                self.assertEqual(result.facts.status, MatchStatus.SUSPENDED)
                self.assertIsNone(result.snapshot)

    def test_missing_server_has_stable_abstention(self) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason

        api = json.loads(json.dumps(API_TENNIS_LIVE))
        del api["result"][0]["event_serving_player"]
        goalserve = GOALSERVE_LIVE.replace(b' serve="1"', b"")
        live_tennis = json.loads(json.dumps(LIVE_TENNIS_API_LIVE))
        live_tennis["score"]["server"] = None
        for provider, fixture in {
            "api_tennis": api,
            "goalserve": goalserve,
            "live_tennis_api": live_tennis,
        }.items():
            with self.subTest(provider=provider):
                result = self._parse(provider, _payload(provider, fixture))
                self.assertIsNone(result.snapshot)
                self.assertIsNotNone(result.facts)
                self.assertEqual(result.abstention, AbstentionReason.MISSING_SERVER)

    def test_unsupported_format_and_schema_have_stable_abstentions(self) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason

        doubles = json.loads(json.dumps(LIVE_TENNIS_API_LIVE))
        doubles["is_doubles"] = True
        fixtures = {
            "api_tennis": b'{"success":1,"result":[]}',
            "goalserve": b"<not_scores/>",
            "live_tennis_api": doubles,
        }
        expected = {
            "api_tennis": AbstentionReason.UNKNOWN_SCHEMA,
            "goalserve": AbstentionReason.UNKNOWN_SCHEMA,
            "live_tennis_api": AbstentionReason.DOUBLES,
        }
        for provider, fixture in fixtures.items():
            with self.subTest(provider=provider):
                result = self._parse(provider, _payload(provider, fixture))
                self.assertEqual(result.abstention, expected[provider])

    def test_api_tennis_requires_documented_point_by_point_shape(self) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason

        fixture = json.loads(json.dumps(API_TENNIS_LIVE))
        fixture["result"][0]["pointbypoint"] = {"unexpected": "object"}
        result = self._parse("api_tennis", _payload("api_tennis", fixture))
        self.assertEqual(result.abstention, AbstentionReason.UNKNOWN_SCHEMA)

    def test_access_denied_has_stable_abstention(self) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason

        fixtures = {
            "api_tennis": b'{"success":0,"error":"access denied"}',
            "goalserve": b'<scores error="access denied"/>',
            "live_tennis_api": b'{"detail":"access denied"}',
        }
        for provider, fixture in fixtures.items():
            with self.subTest(provider=provider):
                result = self._parse(provider, _payload(provider, fixture))
                self.assertIsNone(result.facts)
                self.assertEqual(result.abstention, AbstentionReason.ACCESS_DENIED)

    def test_identity_mismatch_has_stable_abstention(self) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason

        api = json.loads(json.dumps(API_TENNIS_LIVE))
        api["result"][0]["first_player_key"] = "other-player"
        goalserve = GOALSERVE_LIVE.replace(b'id="player-2"', b'id="other-player"')
        live_tennis = json.loads(json.dumps(LIVE_TENNIS_API_LIVE))
        live_tennis["players"]["p2"]["id"] = "other-player"
        for provider, fixture in {
            "api_tennis": api,
            "goalserve": goalserve,
            "live_tennis_api": live_tennis,
        }.items():
            with self.subTest(provider=provider):
                result = self._parse(provider, _payload(provider, fixture))
                self.assertIsNone(result.facts)
                self.assertEqual(result.abstention, AbstentionReason.IDENTITY_MISMATCH)

    def test_tiebreak_and_ambiguous_current_set_are_explicitly_abstained(
        self,
    ) -> None:
        from inci_tennis_adapters.live_score_candidates import AbstentionReason

        tiebreak = json.loads(json.dumps(LIVE_TENNIS_API_LIVE))
        tiebreak["score"].update(
            {
                "games": {"p1": 6, "p2": 6},
                "points": {"p1": "0", "p2": "0"},
                "is_tiebreak": True,
            }
        )
        ambiguous = json.loads(json.dumps(API_TENNIS_LIVE))
        ambiguous["result"][0]["sets"] = [["6", "3"], ["4", "2"], ["0", "0"]]
        goalserve = GOALSERVE_LIVE.replace(
            b'set2="4"', b'set2="4" set3="0"'
        ).replace(b'set2="2"', b'set2="2" set3="0"')
        expected = {
            "api_tennis": AbstentionReason.AMBIGUOUS_CURRENT_SET,
            "goalserve": AbstentionReason.AMBIGUOUS_CURRENT_SET,
            "live_tennis_api": AbstentionReason.UNSUPPORTED_TIEBREAK,
        }
        for provider, fixture in {
            "api_tennis": ambiguous,
            "goalserve": goalserve,
            "live_tennis_api": tiebreak,
        }.items():
            with self.subTest(provider=provider):
                self.assertEqual(
                    self._parse(provider, _payload(provider, fixture)).abstention,
                    expected[provider],
                )

    def test_malformed_payloads_are_rejected_without_echoing_them(self) -> None:
        from inci_tennis_adapters.live_score_candidates import LiveScoreParseError

        cases = (
            ("api_tennis", b'{"success":1,"success":1,"result":[]}', "duplicate_json_key"),
            ("api_tennis", b'{"success":NaN,"result":[]}', "non_finite_number"),
            ("goalserve", b'<scores><match></scores>', "malformed_payload"),
            ("api_tennis", b'{"token":"secret-value","success":1,"result":[]}', "secret_material"),
            ("goalserve", b"<scores><token>secret-value</token></scores>", "secret_material"),
            ("api_tennis", b"\xff", "malformed_payload"),
        )
        for provider, payload, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(LiveScoreParseError, rf"\A{code}\Z") as raised:
                    self._parse(provider, payload)
                self.assertNotIn("secret-value", repr(raised.exception))

    def test_oversized_payload_is_rejected(self) -> None:
        from inci_tennis_adapters.live_score_candidates import LiveScoreParseError

        with self.assertRaisesRegex(LiveScoreParseError, r"\Apayload_too_large\Z"):
            self._parse("api_tennis", b" " * (1_048_577))


if __name__ == "__main__":
    unittest.main()
