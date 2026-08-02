from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch


def _summary_payload() -> bytes:
    return json.dumps(
        {
            "generated_at": "2026-08-01T18:00:02+00:00",
            "sport_event": {
                "id": "sr:sport_event:123456",
                "start_time": "2026-08-01T18:00:00+00:00",
                "start_time_confirmed": True,
                "competitors": [
                    {
                        "id": "sr:competitor:101",
                        "name": "Player Home",
                        "qualifier": "home",
                    },
                    {
                        "id": "sr:competitor:202",
                        "name": "Player Away",
                        "qualifier": "away",
                    },
                ],
                "sport_event_context": {"mode": {"best_of": 3}},
            },
            "sport_event_status": {
                "status": "live",
                "match_status": "1st_set",
                "home_score": 0,
                "away_score": 0,
                "period_scores": [
                    {
                        "number": 1,
                        "type": "set",
                        "home_score": 3,
                        "away_score": 2,
                    }
                ],
                "game_state": {
                    "home_score": 30,
                    "away_score": 15,
                    "serving": "home",
                    "tie_break": False,
                },
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _timeline_payload(*, include_second: bool = True) -> bytes:
    document = json.loads(_summary_payload())
    events = [
        {
            "id": 9001,
            "type": "match_started",
            "time": "2026-08-01T18:00:00+00:00",
        },
        {
            "id": 9002,
            "type": "point",
            "time": "2026-08-01T18:00:01+00:00",
            "home_score": 15,
            "away_score": 0,
            "competitor": "home",
            "server": "home",
            "result": "server_won",
        },
    ]
    if include_second:
        events.append(
            {
                "id": 9003,
                "type": "point",
                "time": "2026-08-01T18:00:02+00:00",
                "home_score": 30,
                "away_score": 0,
                "competitor": "home",
                "server": "home",
                "result": "ace",
            }
        )
    document["timeline"] = events
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def _live_summaries_payload() -> bytes:
    first = json.loads(_summary_payload())
    second = json.loads(_summary_payload())
    second["sport_event"]["id"] = "sr:sport_event:654321"
    second["sport_event"]["competitors"][0].update(
        {"id": "sr:competitor:303", "name": "Second Home"}
    )
    second["sport_event"]["competitors"][1].update(
        {"id": "sr:competitor:404", "name": "Second Away"}
    )
    second["sport_event_status"]["status"] = "not_started"
    second["sport_event_status"]["match_status"] = "match_about_to_start"
    return json.dumps(
        {
            "generated_at": first["generated_at"],
            "summaries": [
                {
                    "sport_event": first["sport_event"],
                    "sport_event_status": first["sport_event_status"],
                },
                {
                    "sport_event": second["sport_event"],
                    "sport_event_status": second["sport_event_status"],
                },
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")


class SportradarWireContractTests(unittest.TestCase):
    def test_wire_contract_module_exists(self) -> None:
        self.assertIsNotNone(
            importlib.util.find_spec(
                "inci_tennis_adapters.sportradar_trial_v3"
            )
        )

    def test_official_summary_shape_projects_score_and_server(self) -> None:
        from inci_tennis_adapters.sportradar_trial_v3 import (
            parse_sport_event_summary,
        )

        snapshot = parse_sport_event_summary(
            _summary_payload(),
            expected_match_id="sr:sport_event:123456",
        )

        self.assertEqual(
            (
                snapshot.provider_match_id,
                snapshot.generated_wall_ns,
                snapshot.start_wall_ns,
                snapshot.best_of,
                snapshot.home_id,
                snapshot.home_name,
                snapshot.away_id,
                snapshot.away_name,
                snapshot.status,
                snapshot.match_status,
                snapshot.sets_home,
                snapshot.sets_away,
                snapshot.games_home,
                snapshot.games_away,
                snapshot.points_home,
                snapshot.points_away,
                snapshot.serving,
                snapshot.in_tiebreak,
            ),
            (
                "sr:sport_event:123456",
                1_785_607_202_000_000_000,
                1_785_607_200_000_000_000,
                3,
                "sr:competitor:101",
                "Player Home",
                "sr:competitor:202",
                "Player Away",
                "live",
                "1st_set",
                0,
                0,
                3,
                2,
                "30",
                "15",
                "home",
                False,
            ),
        )

    def test_summary_rejects_wrong_match_and_unknown_status(self) -> None:
        from inci_tennis_adapters.sportradar_trial_v3 import (
            SportradarWireContractError,
            parse_sport_event_summary,
        )

        with self.assertRaises(SportradarWireContractError):
            parse_sport_event_summary(
                _summary_payload(),
                expected_match_id="sr:sport_event:999999",
            )

        document = json.loads(_summary_payload())
        document["sport_event_status"]["status"] = "mystery"
        with self.assertRaises(SportradarWireContractError):
            parse_sport_event_summary(
                json.dumps(document).encode("utf-8"),
                expected_match_id="sr:sport_event:123456",
            )

    def test_official_lifecycle_status_values_are_accepted(self) -> None:
        from inci_tennis_adapters.sportradar_trial_v3 import (
            parse_sport_event_summary,
        )

        vectors = (
            ("match_about_to_start", "match_about_to_start"),
            ("live", "live"),
            ("closed", "closed"),
        )
        for status, match_status in vectors:
            with self.subTest(status=status, match_status=match_status):
                document = json.loads(_summary_payload())
                document["sport_event_status"].update(
                    {"status": status, "match_status": match_status}
                )
                snapshot = parse_sport_event_summary(
                    json.dumps(document).encode("utf-8"),
                    expected_match_id="sr:sport_event:123456",
                )
                self.assertEqual(
                    (snapshot.status, snapshot.match_status),
                    (status, match_status),
                )

    def test_documented_optional_score_fields_are_not_invented(self) -> None:
        from inci_tennis_adapters.sportradar_trial_v3 import (
            parse_sport_event_summary,
        )

        document = json.loads(_summary_payload())
        del document["sport_event"]["sport_event_context"]["mode"]
        period = document["sport_event_status"]["period_scores"][0]
        del period["number"]
        del period["type"]
        game = document["sport_event_status"]["game_state"]
        del game["home_score"]
        del game["away_score"]
        del game["tie_break"]
        snapshot = parse_sport_event_summary(
            json.dumps(document).encode("utf-8"),
            expected_match_id="sr:sport_event:123456",
        )
        self.assertEqual(
            (
                snapshot.best_of,
                snapshot.games_home,
                snapshot.games_away,
                snapshot.points_home,
                snapshot.points_away,
                snapshot.serving,
                snapshot.in_tiebreak,
            ),
            (None, 3, 2, "--", "--", "home", None),
        )

        del document["sport_event_status"]["home_score"]
        del document["sport_event_status"]["away_score"]
        del document["sport_event_status"]["period_scores"]
        snapshot = parse_sport_event_summary(
            json.dumps(document).encode("utf-8"),
            expected_match_id="sr:sport_event:123456",
        )
        self.assertEqual(
            (
                snapshot.sets_home,
                snapshot.sets_away,
                snapshot.games_home,
                snapshot.games_away,
            ),
            (None, None, None, None),
        )

    def test_timeline_progression_detects_append_correction_and_gap(self) -> None:
        from inci_tennis_adapters import sportradar_trial_v3 as wire

        self.assertTrue(hasattr(wire, "parse_sport_event_timeline"))
        self.assertTrue(hasattr(wire, "validate_timeline_progression"))
        SportradarWireContractError = wire.SportradarWireContractError
        parse_sport_event_timeline = wire.parse_sport_event_timeline
        validate_timeline_progression = wire.validate_timeline_progression

        first = parse_sport_event_timeline(
            _timeline_payload(include_second=False),
            expected_match_id="sr:sport_event:123456",
        )
        second = parse_sport_event_timeline(
            _timeline_payload(),
            expected_match_id="sr:sport_event:123456",
        )
        self.assertEqual(
            (
                first.events[-1].event_id,
                second.events[-1].event_id,
                second.events[-1].event_type,
                second.events[-1].result,
                validate_timeline_progression(first, second),
            ),
            (9002, 9003, "point", "ace", "advanced"),
        )

        corrected_document = json.loads(_timeline_payload())
        corrected_document["generated_at"] = "2026-08-01T18:00:03+00:00"
        corrected_document["timeline"][1]["away_score"] = 15
        corrected_document["timeline"][1]["updated"] = True
        corrected_document["timeline"][1]["updated_time"] = (
            "2026-08-01T18:00:03+00:00"
        )
        corrected = parse_sport_event_timeline(
            json.dumps(corrected_document).encode("utf-8"),
            expected_match_id="sr:sport_event:123456",
        )
        self.assertEqual(
            validate_timeline_progression(second, corrected), "corrected"
        )

        corrected_and_advanced_document = json.loads(_timeline_payload())
        corrected_and_advanced_document["generated_at"] = (
            "2026-08-01T18:00:04+00:00"
        )
        corrected_and_advanced_document["timeline"][1].update(
            {
                "first_serve_fault": True,
                "updated": True,
                "updated_time": "2026-08-01T18:00:04+00:00",
            }
        )
        corrected_and_advanced_document["timeline"].append(
            {
                "id": 9004,
                "type": "period_score",
                "time": "2026-08-01T18:00:04+00:00",
                "competitor": "away",
                "period": 1,
                "period_name": "1st_set",
                "reason": "trainer_called",
            }
        )
        corrected_and_advanced = parse_sport_event_timeline(
            json.dumps(corrected_and_advanced_document).encode("utf-8"),
            expected_match_id="sr:sport_event:123456",
        )
        self.assertTrue(
            corrected_and_advanced.events[1].first_serve_fault
        )
        self.assertEqual(corrected_and_advanced.events[-1].period, 1)
        self.assertEqual(
            validate_timeline_progression(second, corrected_and_advanced),
            "corrected_and_advanced",
        )

        legacy_string_period = json.loads(
            json.dumps(corrected_and_advanced_document)
        )
        legacy_string_period["timeline"][-1]["period"] = "1"
        parsed_legacy = parse_sport_event_timeline(
            json.dumps(legacy_string_period).encode("utf-8"),
            expected_match_id="sr:sport_event:123456",
        )
        self.assertEqual(parsed_legacy.events[-1].period, 1)

        with self.assertRaises(SportradarWireContractError):
            validate_timeline_progression(second, first)

    def test_live_summaries_projects_all_matches_and_rejects_duplicates(self) -> None:
        from inci_tennis_adapters import sportradar_trial_v3 as wire

        self.assertTrue(hasattr(wire, "parse_live_summaries"))
        snapshots = wire.parse_live_summaries(_live_summaries_payload())
        self.assertEqual(
            [
                (
                    item.provider_match_id,
                    item.home_name,
                    item.away_name,
                    item.status,
                )
                for item in snapshots
            ],
            [
                (
                    "sr:sport_event:123456",
                    "Player Home",
                    "Player Away",
                    "live",
                ),
                (
                    "sr:sport_event:654321",
                    "Second Home",
                    "Second Away",
                    "not_started",
                ),
            ],
        )

        duplicate = json.loads(_live_summaries_payload())
        duplicate["summaries"].append(duplicate["summaries"][0])
        with self.assertRaises(wire.SportradarWireContractError):
            wire.parse_live_summaries(json.dumps(duplicate).encode("utf-8"))


class _FakeClock:
    def __init__(self) -> None:
        self.monotonic_ns = 10_000_000_000
        self.wall_ns = 1_785_607_200_000_000_000
        self.sleeps: list[float] = []

    def monotonic(self) -> int:
        return self.monotonic_ns

    def wall(self) -> int:
        return self.wall_ns

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        delta = round(seconds * 1_000_000_000)
        self.monotonic_ns += delta
        self.wall_ns += delta


class _FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(payload)),
        }
        self.closed = False

    def iter_content(self, chunk_size: int) -> list[bytes]:
        return [
            self.payload[offset : offset + chunk_size]
            for offset in range(0, len(self.payload), chunk_size)
        ]

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if callable(self.outcome):
            value = self.outcome()
        else:
            value = self.outcome
        if isinstance(value, BaseException):
            raise value
        if not isinstance(value, _FakeResponse):
            raise AssertionError("fake outcome is not a response")
        return value

    def close(self) -> None:
        return None


class SportradarTrialLedgerTests(unittest.TestCase):
    def test_shadow_task_cancellation_is_restartable_before_observation(self) -> None:
        """Catches a clean task cancellation poisoning every later session."""

        from inci_tennis_io.sportradar_trial_transport import TrialUsageLedger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TrialUsageLedger(root) as ledger:
                reservation = ledger.reserve("summary")
                ledger.record_interrupted_outcome(reservation)
                ledger.record_session_terminal(
                    command="shadow",
                    provider_match_id="sr:sport_event:123456",
                    reason="cancelled",
                    code="sportradar_shadow_task_cancelled",
                )

            with TrialUsageLedger(root) as reopened:
                self.assertEqual(reopened.recovered_unclean_sessions, 0)

    def test_shadow_observation_and_terminal_are_restartable(self) -> None:
        from hashlib import sha256
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialObservationRecord,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b'{"shadow":"observation"}'
            captured_wall_ns = 1_785_607_200_000_000_000
            with TrialUsageLedger(root) as ledger:
                reservation = ledger.reserve("summary")
                raw_path = ledger.persist_raw(
                    reservation,
                    payload,
                    captured_wall_ns=captured_wall_ns,
                )
                ledger.record_attempt_outcome(
                    reservation,
                    outcome="captured",
                    code="sportradar_capture_persisted",
                )
                try:
                    ledger.record_observation(
                        TrialObservationRecord(
                            command="shadow",
                            reservation=reservation,
                            provider_match_id="sr:sport_event:123456",
                            generated_wall_ns=captured_wall_ns,
                            captured_wall_ns=captured_wall_ns,
                            status="live",
                            match_status="1st_set",
                            payload_sha256=sha256(payload).hexdigest(),
                            raw_path=raw_path,
                            progression="initial",
                            last_event_id=None,
                            terminal_reason=None,
                        )
                    )
                    ledger.record_session_terminal(
                        command="shadow",
                        provider_match_id="sr:sport_event:123456",
                        reason="duration_elapsed",
                    )
                except SportradarTrialObserverError as error:
                    self.fail(f"shadow audit command rejected: {error}")

            with TrialUsageLedger(root) as reopened:
                self.assertEqual(reopened.recovered_uncertain_attempts, 0)
                self.assertEqual(reopened.recovered_incomplete_captures, 0)
                self.assertEqual(reopened.recovered_unclean_sessions, 0)

            rows = [
                json.loads(value)
                for value in (root / "observations.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                [row["command"] for row in rows],
                ["shadow", "shadow"],
            )

    def test_shadow_parser_failure_and_terminal_are_restartable(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            captured_wall_ns = 1_785_607_200_000_000_000
            with TrialUsageLedger(root) as ledger:
                reservation = ledger.reserve("timeline")
                ledger.persist_raw(
                    reservation,
                    b"{}",
                    captured_wall_ns=captured_wall_ns,
                )
                ledger.record_attempt_outcome(
                    reservation,
                    outcome="captured",
                    code="sportradar_capture_persisted",
                )
                try:
                    ledger.record_parser_failure(
                        command="shadow",
                        reservation=reservation,
                        code="sportradar_timeline_schema_unknown",
                    )
                    ledger.record_session_terminal(
                        command="shadow",
                        provider_match_id="sr:sport_event:123456",
                        reason="halted",
                        code="sportradar_timeline_schema_unknown",
                    )
                except SportradarTrialObserverError as error:
                    self.fail(f"shadow audit command rejected: {error}")

            with TrialUsageLedger(root) as reopened:
                self.assertEqual(reopened.recovered_uncertain_attempts, 0)
                self.assertEqual(reopened.recovered_incomplete_captures, 0)
                self.assertEqual(reopened.recovered_unclean_sessions, 0)

            rows = [
                json.loads(value)
                for value in (root / "observations.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                [row["command"] for row in rows],
                ["shadow", "shadow"],
            )

    def test_shadow_discovery_quit_and_post_discovery_halt_are_restartable(self) -> None:
        """Catches chooser exits poisoning the next trial-ledger startup."""

        from hashlib import sha256
        from inci_tennis_io.sportradar_trial_transport import (
            TrialObservationRecord,
            TrialUsageLedger,
        )

        captured = 1_785_607_205_000_000_000
        for label, reason, code in (
            ("quit", "list_complete", None),
            (
                "catalog_halt",
                "halted",
                "sportradar_shadow_discovery_halted",
            ),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                payload = _live_summaries_payload()
                with TrialUsageLedger(root) as ledger:
                    reservation = ledger.reserve("live_summaries")
                    raw_path = ledger.persist_raw(
                        reservation, payload, captured_wall_ns=captured
                    )
                    ledger.record_attempt_outcome(
                        reservation,
                        outcome="captured",
                        code="sportradar_capture_persisted",
                    )
                    ledger.record_observation(
                        TrialObservationRecord(
                            command="shadow",
                            reservation=reservation,
                            provider_match_id=None,
                            generated_wall_ns=1_785_607_202_000_000_000,
                            captured_wall_ns=captured,
                            status="listed",
                            match_status=None,
                            payload_sha256=sha256(payload).hexdigest(),
                            raw_path=raw_path,
                            progression="discovery",
                            last_event_id=None,
                            terminal_reason=None,
                        )
                    )
                    values: dict[str, object] = {
                        "command": "shadow",
                        "provider_match_id": None,
                        "reason": reason,
                    }
                    if code is not None:
                        values["code"] = code
                    ledger.record_session_terminal(**values)

                with TrialUsageLedger(root) as reopened:
                    self.assertEqual(reopened.recovered_unclean_sessions, 0)

    def test_shadow_live_discovery_parser_failure_with_no_selection_restarts(self) -> None:
        """Catches a malformed chooser response leaving an unauditable terminal."""

        from inci_tennis_io.sportradar_trial_transport import TrialUsageLedger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TrialUsageLedger(root) as ledger:
                reservation = ledger.reserve("live_summaries")
                ledger.persist_raw(
                    reservation,
                    b"{}",
                    captured_wall_ns=1_785_607_205_000_000_000,
                )
                ledger.record_attempt_outcome(
                    reservation,
                    outcome="captured",
                    code="sportradar_capture_persisted",
                )
                ledger.record_parser_failure(
                    command="shadow",
                    reservation=reservation,
                    code="sportradar_live_summaries_schema_unknown",
                )
                ledger.record_session_terminal(
                    command="shadow",
                    provider_match_id=None,
                    reason="halted",
                    code="sportradar_live_summaries_schema_unknown",
                )

            with TrialUsageLedger(root) as reopened:
                self.assertEqual(reopened.recovered_unclean_sessions, 0)

    def test_selected_shadow_session_restarts_with_discovery_and_collection_routes(self) -> None:
        """Catches audit rejection of the chooser's one shared provider session."""

        from hashlib import sha256
        from inci_tennis_io.sportradar_trial_transport import (
            TrialObservationRecord,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            captured = 1_785_607_205_000_000_000
            rows = (
                (
                    "live_summaries",
                    _live_summaries_payload(),
                    None,
                    "listed",
                    None,
                    "discovery",
                ),
                (
                    "summary",
                    _summary_payload(),
                    "sr:sport_event:123456",
                    "live",
                    "1st_set",
                    "initial",
                ),
                (
                    "timeline",
                    _timeline_payload(),
                    "sr:sport_event:123456",
                    "live",
                    "1st_set",
                    "initial_timeline",
                ),
            )
            with TrialUsageLedger(root) as ledger:
                for route, payload, provider_id, status, match_status, progression in rows:
                    reservation = ledger.reserve(route)
                    raw_path = ledger.persist_raw(
                        reservation, payload, captured_wall_ns=captured
                    )
                    ledger.record_attempt_outcome(
                        reservation,
                        outcome="captured",
                        code="sportradar_capture_persisted",
                    )
                    ledger.record_observation(
                        TrialObservationRecord(
                            command="shadow",
                            reservation=reservation,
                            provider_match_id=provider_id,
                            generated_wall_ns=1_785_607_202_000_000_000,
                            captured_wall_ns=captured,
                            status=status,
                            match_status=match_status,
                            payload_sha256=sha256(payload).hexdigest(),
                            raw_path=raw_path,
                            progression=progression,
                            last_event_id=None,
                            terminal_reason=None,
                        )
                    )
                ledger.record_session_terminal(
                    command="shadow",
                    provider_match_id="sr:sport_event:123456",
                    reason="duration_elapsed",
                )

            with TrialUsageLedger(root) as reopened:
                self.assertEqual(reopened.recovered_unclean_sessions, 0)

    def test_existing_check_audit_still_reopens(self) -> None:
        from hashlib import sha256
        from inci_tennis_io.sportradar_trial_transport import (
            TrialObservationRecord,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b'{"existing":"check"}'
            captured_wall_ns = 1_785_607_200_000_000_000
            with TrialUsageLedger(root) as ledger:
                reservation = ledger.reserve("summary")
                raw_path = ledger.persist_raw(
                    reservation,
                    payload,
                    captured_wall_ns=captured_wall_ns,
                )
                ledger.record_attempt_outcome(
                    reservation,
                    outcome="captured",
                    code="sportradar_capture_persisted",
                )
                ledger.record_observation(
                    TrialObservationRecord(
                        command="check",
                        reservation=reservation,
                        provider_match_id="sr:sport_event:123456",
                        generated_wall_ns=captured_wall_ns,
                        captured_wall_ns=captured_wall_ns,
                        status="live",
                        match_status="1st_set",
                        payload_sha256=sha256(payload).hexdigest(),
                        raw_path=raw_path,
                        progression="initial",
                        last_event_id=None,
                        terminal_reason=None,
                    )
                )
                ledger.record_session_terminal(
                    command="check",
                    provider_match_id="sr:sport_event:123456",
                    reason="check_complete",
                )

            with TrialUsageLedger(root) as reopened:
                self.assertEqual(reopened.recovered_unclean_sessions, 0)

    def test_unknown_audit_commands_remain_rejected(self) -> None:
        from hashlib import sha256
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialObservationRecord,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b'{"unknown":"command"}'
            captured_wall_ns = 1_785_607_200_000_000_000
            with TrialUsageLedger(root) as ledger:
                reservation = ledger.reserve("summary")
                raw_path = ledger.persist_raw(
                    reservation,
                    payload,
                    captured_wall_ns=captured_wall_ns,
                )
                ledger.record_attempt_outcome(
                    reservation,
                    outcome="captured",
                    code="sportradar_capture_persisted",
                )
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_observation_record_invalid",
                ):
                    ledger.record_observation(
                        TrialObservationRecord(
                            command="unknown",
                            reservation=reservation,
                            provider_match_id="sr:sport_event:123456",
                            generated_wall_ns=captured_wall_ns,
                            captured_wall_ns=captured_wall_ns,
                            status="live",
                            match_status="1st_set",
                            payload_sha256=sha256(payload).hexdigest(),
                            raw_path=raw_path,
                            progression="initial",
                            last_event_id=None,
                            terminal_reason=None,
                        )
                    )
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_parser_failure_record_invalid",
                ):
                    ledger.record_parser_failure(
                        command="unknown",
                        reservation=reservation,
                        code="sportradar_summary_schema_unknown",
                    )
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_terminal_record_invalid",
                ):
                    ledger.record_session_terminal(
                        command="unknown",
                        provider_match_id="sr:sport_event:123456",
                        reason="halted",
                        code="sportradar_summary_schema_unknown",
                    )
                ledger.record_parser_failure(
                    command="check",
                    reservation=reservation,
                    code="sportradar_summary_schema_unknown",
                )
                ledger.record_session_terminal(
                    command="check",
                    provider_match_id="sr:sport_event:123456",
                    reason="halted",
                    code="sportradar_summary_schema_unknown",
                )

    def test_transport_uses_proxy_independent_owned_session(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        class DirectSession:
            def __init__(self) -> None:
                self.trust_env = True
                self.trust_values: list[bool] = []
                self.closed = False

            def get(self, *_: object, **__: object) -> _FakeResponse:
                self.trust_values.append(self.trust_env)
                return _FakeResponse(_summary_payload())

            def close(self) -> None:
                self.closed = True

        session = DirectSession()
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "inci_tennis_io.sportradar_trial_transport.requests.Session",
                    return_value=session,
                ),
                patch(
                    "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                    return_value=_FakeResponse(_summary_payload()),
                ),
                TrialUsageLedger(Path(directory)) as ledger,
            ):
                transport = SportradarTrialTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                )
                transport.fetch_summary("sr:sport_event:123456")
                transport.close()
        self.assertEqual(session.trust_values, [False])
        self.assertTrue(session.closed)

    def test_reservation_is_durable_before_network_and_response_before_parse(self) -> None:
        from inci_tennis_adapters.sportradar_trial_v3 import (
            SportradarWireContractError,
            parse_sport_event_summary,
        )
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = _FakeClock()

            def outcome() -> _FakeResponse:
                rows = (root / "usage.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
                self.assertEqual(len(rows), 1)
                self.assertEqual(json.loads(rows[0])["session_attempt"], 1)
                return _FakeResponse(b'{"malformed_for_parser":true}')

            session = _FakeSession(outcome)
            with patch(
                "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                side_effect=session.get,
            ):
                with TrialUsageLedger(
                    root,
                    monotonic_ns=clock.monotonic,
                    wall_ns=clock.wall,
                    sleeper=clock.sleep,
                ) as ledger:
                    transport = SportradarTrialTransport(
                        api_key="trial-secret-value",
                        ledger=ledger,
                        wall_ns=clock.wall,
                    )
                    capture = transport.fetch_summary(
                        "sr:sport_event:123456"
                    )

            self.assertEqual(capture.payload, b'{"malformed_for_parser":true}')
            self.assertEqual(capture.raw_path.read_bytes(), capture.payload)
            with self.assertRaises(SportradarWireContractError):
                parse_sport_event_summary(
                    capture.payload,
                    expected_match_id="sr:sport_event:123456",
                )
            self.assertEqual(len(session.calls), 1)
            call = session.calls[0]
            self.assertEqual(
                call["url"],
                "https://api.sportradar.com/tennis/trial/v3/en/"
                "sport_events/sr:sport_event:123456/summary.json",
            )
            self.assertEqual(
                call["headers"],
                {
                    "accept": "application/json",
                    "accept-encoding": "identity",
                    "x-api-key": "trial-secret-value",
                },
            )
            self.assertNotIn("params", call)
            self.assertNotIn("trial-secret-value", str(call["url"]))
            self.assertEqual(call["allow_redirects"], False)
            self.assertEqual(call["stream"], True)
            self.assertEqual(call["timeout"], (3, 10))

    def test_ledger_enforces_pacing_session_total_and_single_owner(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = _FakeClock()
            with TrialUsageLedger(
                root,
                session_attempt_limit=2,
                access_attempt_limit=3,
                monotonic_ns=clock.monotonic,
                wall_ns=clock.wall,
                sleeper=clock.sleep,
            ) as first:
                one = first.reserve("summary")
                two = first.reserve("timeline")
                first.record_attempt_outcome(
                    one,
                    outcome="failed",
                    code="sportradar_transport_unavailable",
                )
                first.record_attempt_outcome(
                    two,
                    outcome="failed",
                    code="sportradar_transport_unavailable",
                )
                self.assertEqual(
                    (one.session_attempt, two.session_attempt), (1, 2)
                )
                self.assertEqual(clock.sleeps, [1.0])
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_session_attempt_limit",
                ):
                    first.reserve("timeline")
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_usage_ledger_locked",
                ):
                    TrialUsageLedger(root)

            with TrialUsageLedger(
                root,
                session_attempt_limit=2,
                access_attempt_limit=3,
                monotonic_ns=clock.monotonic,
                wall_ns=clock.wall,
                sleeper=clock.sleep,
            ) as second:
                third = second.reserve("summary")
                self.assertEqual(third.access_attempt, 3)
                second.record_attempt_outcome(
                    third,
                    outcome="failed",
                    code="sportradar_transport_unavailable",
                )
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_access_attempt_limit",
                ):
                    second.reserve("timeline")

            for session_limit, access_limit in ((401, 1_000), (400, 1_001)):
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_attempt_limit_invalid",
                ):
                    TrialUsageLedger(
                        root,
                        session_attempt_limit=session_limit,
                        access_attempt_limit=access_limit,
                    )

    def test_crashed_attempt_recovers_uncertain_without_refunding_quota(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import TrialUsageLedger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TrialUsageLedger(root) as ledger:
                ledger.reserve("summary")
            with TrialUsageLedger(root) as recovered:
                self.assertEqual(recovered.recovered_uncertain_attempts, 1)
                self.assertEqual(recovered.access_attempts, 1)
                second = recovered.reserve("summary")
                self.assertEqual(second.access_attempt, 2)
                recovered.record_attempt_outcome(
                    second,
                    outcome="failed",
                    code="sportradar_transport_unavailable",
                )
            outcomes = [
                json.loads(value)
                for value in (root / "outcomes.jsonl").read_text().splitlines()
            ]
            self.assertEqual(outcomes[0]["outcome"], "uncertain")
            self.assertEqual(
                outcomes[0]["code"],
                "sportradar_process_crash_unresolved",
            )

    def test_transport_rejects_unsafe_responses_without_leaking_key(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        cases = (
            _FakeResponse(b"forbidden", status_code=403),
            _FakeResponse(
                b"{}", headers={"Content-Type": "text/html"}
            ),
            _FakeResponse(
                b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Content-Encoding": "gzip",
                },
            ),
            RuntimeError("server reflected trial-secret-value"),
        )
        for index, outcome in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                key = "trial-secret-value"
                session = _FakeSession(outcome)
                with patch(
                    "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                    side_effect=session.get,
                ):
                    with TrialUsageLedger(Path(directory)) as ledger:
                        transport = SportradarTrialTransport(
                            api_key=key,
                            ledger=ledger,
                        )
                        with self.assertRaises(SportradarTrialObserverError) as caught:
                            transport.fetch_live_summaries()
                        self.assertNotIn(key, str(caught.exception))
                        self.assertNotIn(key, repr(transport))
                self.assertEqual(len(session.calls), 1)
                self.assertEqual(
                    list((Path(directory) / "raw").glob("*.json")), []
                )

    def test_failed_http_attempt_has_one_durable_redacted_outcome(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                return_value=_FakeResponse(b"secret body", status_code=403),
            ):
                with TrialUsageLedger(root) as ledger:
                    with self.assertRaisesRegex(
                        SportradarTrialObserverError,
                        "sportradar_http_status_403",
                    ):
                        SportradarTrialTransport(
                            api_key="safe-trial-key",
                            ledger=ledger,
                        ).fetch_live_summaries()
            usage = (root / "usage.jsonl").read_text().splitlines()
            outcomes = (root / "outcomes.jsonl").read_text().splitlines()
            self.assertEqual((len(usage), len(outcomes)), (1, 1))
            outcome = json.loads(outcomes[0])
            self.assertEqual(
                (outcome["outcome"], outcome["code"], outcome["raw_file"]),
                ("failed", "sportradar_http_status_403", None),
            )
            self.assertNotIn("secret body", outcomes[0])
            self.assertNotIn("safe-trial-key", outcomes[0])

    def test_interrupt_during_http_is_durable_and_restartable(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                side_effect=KeyboardInterrupt,
            ):
                with TrialUsageLedger(root) as ledger:
                    with self.assertRaises(KeyboardInterrupt):
                        SportradarTrialTransport(
                            api_key="safe-trial-key",
                            ledger=ledger,
                        ).fetch_live_summaries()
            outcome = json.loads(
                (root / "outcomes.jsonl").read_text().splitlines()[0]
            )
            self.assertEqual(
                (outcome["outcome"], outcome["code"]),
                (
                    "failed",
                    "sportradar_operator_interrupt_during_request",
                ),
            )
            with TrialUsageLedger(root) as recovered:
                self.assertEqual(recovered.recovered_uncertain_attempts, 0)
                self.assertEqual(recovered.recovered_unclean_sessions, 1)

    def test_encoded_json_secret_reflection_is_rejected_before_raw(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        payloads = (
            b'{"echo":"\\u0073afe-trial-key"}',
            b'{"echo":"\\u0073afe-trial-key"',
            b'{"echo":"\\u0073\\u0061fe-trial-key"',
        )
        for payload in payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with (
                    patch(
                        "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                        return_value=_FakeResponse(payload),
                    ),
                    TrialUsageLedger(root) as ledger,
                ):
                    with self.assertRaisesRegex(
                        SportradarTrialObserverError,
                        "sportradar_credential_reflected",
                    ):
                        SportradarTrialTransport(
                            api_key="safe-trial-key",
                            ledger=ledger,
                        ).fetch_live_summaries()
                self.assertEqual(list((root / "raw").glob("*.json")), [])
                outcome = json.loads(
                    (root / "outcomes.jsonl").read_text().splitlines()[0]
                )
                self.assertEqual(outcome["outcome"], "failed")

    def test_ledger_rejects_forged_reservations_and_duplicate_json_keys(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialAttemptReservation,
            TrialObservationRecord,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TrialUsageLedger(root) as ledger:
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_parser_failure_record_invalid",
                ):
                    ledger.record_parser_failure(
                        command="check",
                        reservation=object(),  # type: ignore[arg-type]
                        code="sportradar_summary_schema_unknown",
                    )
                real = ledger.reserve("summary")
                forged = TrialAttemptReservation(
                    session_id=real.session_id,
                    session_attempt=real.session_attempt,
                    access_attempt=real.access_attempt,
                    route="timeline",
                    started_wall_ns=real.started_wall_ns,
                )
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_capture_invalid",
                ):
                    ledger.persist_raw(
                        forged,
                        b"{}",
                        captured_wall_ns=1_785_607_200_000_000_000,
                    )

                raw_path = ledger.persist_raw(
                    real,
                    b"{}",
                    captured_wall_ns=1_785_607_200_000_000_000,
                )
                ledger.record_attempt_outcome(
                    real,
                    outcome="captured",
                    code="sportradar_capture_persisted",
                )
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_observation_record_invalid",
                ):
                    ledger.record_observation(
                        TrialObservationRecord(
                            command="check",
                            reservation=forged,
                            provider_match_id="sr:sport_event:123456",
                            generated_wall_ns=1_785_607_200_000_000_000,
                            captured_wall_ns=1_785_607_200_000_000_000,
                            status="live",
                            match_status="1st_set",
                            payload_sha256=(
                                "44136fa355b3678a1146ad16f7e8649e"
                                "94fb4fc21fe77e8310c060f61caaff8a"
                            ),
                            raw_path=raw_path,
                            progression="initial",
                            last_event_id=None,
                            terminal_reason=None,
                        )
                    )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            duplicate = (
                '{"schema":"wrong","schema":"inci-sportradar-trial-usage-v1",'
                '"kind":"attempt","session_id":'
                '"eaf9bd2c-1f67-4c40-ab31-da6781d7d6d5",'
                '"session_attempt":1,"access_attempt":1,"route":"summary",'
                '"started_wall_ns":1785607200000000000}\n'
            )
            (root / "usage.jsonl").write_text(duplicate, encoding="utf-8")
            with self.assertRaisesRegex(
                SportradarTrialObserverError,
                "sportradar_usage_ledger_corrupt",
            ):
                TrialUsageLedger(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TrialUsageLedger(root):
                pass
            (root / "observations.jsonl").write_bytes(b'{"partial":true}')
            with self.assertRaisesRegex(
                SportradarTrialObserverError,
                "sportradar_audit_ledger_corrupt",
            ):
                TrialUsageLedger(root)

    def test_restart_rejects_semantically_tampered_audit_rows(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TrialUsageLedger(root) as ledger:
                reservation = ledger.reserve("summary")
                ledger.record_attempt_outcome(
                    reservation,
                    outcome="failed",
                    code="sportradar_transport_unavailable",
                )
            row = json.loads((root / "outcomes.jsonl").read_text())
            row.update(
                {
                    "session_id": "not-a-uuid",
                    "route": "mutating-route",
                    "outcome": "invented",
                    "code": "unsafe",
                    "captured_wall_ns": -1,
                    "payload_sha256": "bad",
                    "raw_file": "../../other-file",
                }
            )
            (root / "outcomes.jsonl").write_text(
                json.dumps(row) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SportradarTrialObserverError,
                "sportradar_audit_ledger_corrupt",
            ):
                TrialUsageLedger(root)

    def test_container_and_recursive_ledger_tampering_has_fixed_diagnostic(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TrialUsageLedger(root) as ledger:
                reservation = ledger.reserve("summary")
                ledger.record_attempt_outcome(
                    reservation,
                    outcome="failed",
                    code="sportradar_transport_unavailable",
                )
            outcome = json.loads((root / "outcomes.jsonl").read_text())
            outcome["outcome"] = []
            (root / "outcomes.jsonl").write_text(
                json.dumps(outcome) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SportradarTrialObserverError,
                "sportradar_audit_ledger_corrupt",
            ):
                TrialUsageLedger(root)

        for field in ("session_id", "access_attempt"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with TrialUsageLedger(root) as ledger:
                    reservation = ledger.reserve("summary")
                    ledger.persist_raw(
                        reservation,
                        b"{}",
                        captured_wall_ns=1_785_607_200_000_000_000,
                    )
                    ledger.record_attempt_outcome(
                        reservation,
                        outcome="captured",
                        code="sportradar_capture_persisted",
                    )
                    ledger.record_parser_failure(
                        command="check",
                        reservation=reservation,
                        code="sportradar_summary_schema_unknown",
                    )
                    ledger.record_session_terminal(
                        command="check",
                        provider_match_id="sr:sport_event:123456",
                        reason="halted",
                        code="sportradar_summary_schema_unknown",
                    )
                rows = [
                    json.loads(value)
                    for value in (root / "observations.jsonl")
                    .read_text()
                    .splitlines()
                ]
                rows[0][field] = []
                (root / "observations.jsonl").write_text(
                    "".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_audit_ledger_corrupt",
                ):
                    TrialUsageLedger(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.mkdir(exist_ok=True)
            (root / "usage.jsonl").write_text(
                "[" * 2_000 + "]" * 2_000 + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SportradarTrialObserverError,
                "sportradar_usage_ledger_corrupt",
            ):
                TrialUsageLedger(root)

    def test_restart_binds_outcome_codes_to_their_semantics(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch(
                    "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                    return_value=_FakeResponse(_summary_payload()),
                ),
                TrialUsageLedger(root) as ledger,
            ):
                SportradarTrialTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                ).fetch_summary("sr:sport_event:123456")
            row = json.loads((root / "outcomes.jsonl").read_text())
            row["code"] = "sportradar_http_status_500"
            (root / "outcomes.jsonl").write_text(
                json.dumps(row) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SportradarTrialObserverError,
                "sportradar_audit_ledger_corrupt",
            ):
                TrialUsageLedger(root)

    def test_restart_accounts_for_raw_capture_left_before_outcome(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import TrialUsageLedger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b'{"durable":"capture"}'
            with TrialUsageLedger(root) as ledger:
                reservation = ledger.reserve("summary")
                raw_path = ledger.persist_raw(
                    reservation,
                    payload,
                    captured_wall_ns=1_785_607_200_000_000_000,
                )
            with TrialUsageLedger(root) as recovered:
                self.assertEqual(recovered.recovered_uncertain_attempts, 1)
            outcome = json.loads((root / "outcomes.jsonl").read_text())
            self.assertEqual(
                (
                    outcome["outcome"],
                    outcome["code"],
                    outcome["captured_wall_ns"],
                    outcome["raw_file"],
                    outcome["payload_sha256"],
                ),
                (
                    "uncertain",
                    "sportradar_process_crash_unresolved",
                    None,
                    raw_path.name,
                    "d27a5e11a94f1269818d17bb745fd457"
                    "fad8c656f620ebd3ca2436fb5a6ea6b8",
                ),
            )
            with TrialUsageLedger(root) as clean:
                self.assertEqual(clean.recovered_uncertain_attempts, 0)

    def test_raw_persistence_failure_is_recorded_uncertain_and_restartable(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TrialUsageLedger(root) as ledger:
                with (
                    patch(
                        "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                        return_value=_FakeResponse(_summary_payload()),
                    ),
                    patch(
                        "inci_tennis_io.sportradar_trial_transport._fsync_directory",
                        side_effect=SportradarTrialObserverError(
                            "sportradar_capture_write_failed"
                        ),
                    ),
                ):
                    with self.assertRaisesRegex(
                        SportradarTrialObserverError,
                        "sportradar_capture_write_failed",
                    ):
                        SportradarTrialTransport(
                            api_key="safe-trial-key",
                            ledger=ledger,
                        ).fetch_summary("sr:sport_event:123456")
            outcome = json.loads((root / "outcomes.jsonl").read_text())
            self.assertEqual(
                (
                    outcome["outcome"],
                    outcome["code"],
                    outcome["raw_file"],
                ),
                (
                    "uncertain",
                    "sportradar_process_crash_unresolved",
                    "0001_summary.json",
                ),
            )
            with TrialUsageLedger(root) as recovered:
                self.assertEqual(recovered.recovered_uncertain_attempts, 0)

    def test_restart_rejects_unaccounted_raw_files(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TrialUsageLedger(root):
                pass
            (root / "raw" / "9999_summary.json").write_bytes(b"{}")
            with self.assertRaisesRegex(
                SportradarTrialObserverError,
                "sportradar_audit_ledger_corrupt",
            ):
                TrialUsageLedger(root)

    def test_restart_binds_terminal_to_session_observations(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            SportradarTrialTransport,
            TrialUsageLedger,
        )
        from inci_tennis_runtime.sportradar_trial_cli import (
            TrialCliDependencies,
            run_cli,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = _FakeClock()
            ledger = TrialUsageLedger(
                root,
                monotonic_ns=clock.monotonic,
                wall_ns=clock.wall,
                sleeper=clock.sleep,
            )
            dependencies = TrialCliDependencies(
                ledger_factory=lambda: ledger,
                transport_factory=lambda **values: SportradarTrialTransport(
                    **values,
                    wall_ns=clock.wall,
                    monotonic_ns=clock.monotonic,
                ),
                sample_counter=clock.monotonic,
                sleeper=clock.sleep,
            )
            with patch(
                "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                return_value=_FakeResponse(_summary_payload()),
            ):
                self.assertEqual(
                    run_cli(
                        [
                            "--check",
                            "--match-id",
                            "sr:sport_event:123456",
                        ],
                        environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                        stdout=io.StringIO(),
                        stderr=io.StringIO(),
                        dependencies=dependencies,
                    ),
                    0,
                )
            rows = [
                json.loads(value)
                for value in (root / "observations.jsonl")
                .read_text()
                .splitlines()
            ]
            rows[-1].update(
                {"command": "observe", "reason": "duration_elapsed"}
            )
            (root / "observations.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SportradarTrialObserverError,
                "sportradar_audit_ledger_corrupt",
            ):
                TrialUsageLedger(root)

    def test_terminal_record_blocks_later_dispositions(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TrialUsageLedger(root) as ledger:
                reservation = ledger.reserve("summary")
                ledger.persist_raw(
                    reservation,
                    b"{}",
                    captured_wall_ns=1_785_607_200_000_000_000,
                )
                ledger.record_attempt_outcome(
                    reservation,
                    outcome="captured",
                    code="sportradar_capture_persisted",
                )
                pending = ledger.reserve("timeline")
                ledger.record_session_terminal(
                    command="check",
                    provider_match_id="sr:sport_event:123456",
                    reason="halted",
                    code="sportradar_output_unavailable",
                )
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_parser_failure_record_invalid",
                ):
                    ledger.record_parser_failure(
                        command="check",
                        reservation=reservation,
                        code="sportradar_summary_schema_unknown",
                    )
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_outcome_record_invalid",
                ):
                    ledger.record_attempt_outcome(
                        pending,
                        outcome="failed",
                        code="sportradar_transport_unavailable",
                    )

    def test_restart_rejects_disposition_after_terminal(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TrialUsageLedger(root) as ledger:
                reservation = ledger.reserve("summary")
                ledger.persist_raw(
                    reservation,
                    b"{}",
                    captured_wall_ns=1_785_607_200_000_000_000,
                )
                ledger.record_attempt_outcome(
                    reservation,
                    outcome="captured",
                    code="sportradar_capture_persisted",
                )
                ledger.record_parser_failure(
                    command="check",
                    reservation=reservation,
                    code="sportradar_summary_schema_unknown",
                )
                ledger.record_session_terminal(
                    command="check",
                    provider_match_id="sr:sport_event:123456",
                    reason="halted",
                    code="sportradar_summary_schema_unknown",
                )
            rows = (root / "observations.jsonl").read_text().splitlines()
            (root / "observations.jsonl").write_text(
                rows[1] + "\n" + rows[0] + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SportradarTrialObserverError,
                "sportradar_audit_ledger_corrupt",
            ):
                TrialUsageLedger(root)

    def test_restart_marks_captured_without_disposition_and_unclean_session(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch(
                    "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                    return_value=_FakeResponse(_summary_payload()),
                ),
                TrialUsageLedger(root) as ledger,
            ):
                SportradarTrialTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                ).fetch_summary("sr:sport_event:123456")
            with TrialUsageLedger(root) as recovered:
                self.assertEqual(recovered.recovered_incomplete_captures, 1)
                self.assertEqual(recovered.recovered_unclean_sessions, 1)
            rows = [
                json.loads(value)
                for value in (root / "observations.jsonl")
                .read_text()
                .splitlines()
            ]
            self.assertEqual(
                [row["schema"] for row in rows],
                [
                    "inci-sportradar-trial-recovery-v1",
                    "inci-sportradar-trial-terminal-v1",
                ],
            )
            with TrialUsageLedger(root) as clean:
                self.assertEqual(clean.recovered_incomplete_captures, 0)
                self.assertEqual(clean.recovered_unclean_sessions, 0)

    def test_recovered_terminal_allows_prior_observations_in_crashed_session(self) -> None:
        from hashlib import sha256
        from inci_tennis_io.sportradar_trial_transport import (
            TrialObservationRecord,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b'{"first":"observation"}'
            with TrialUsageLedger(root) as ledger:
                first = ledger.reserve("summary")
                raw_path = ledger.persist_raw(
                    first,
                    payload,
                    captured_wall_ns=1_785_607_200_000_000_000,
                )
                ledger.record_attempt_outcome(
                    first,
                    outcome="captured",
                    code="sportradar_capture_persisted",
                )
                ledger.record_observation(
                    TrialObservationRecord(
                        command="observe",
                        reservation=first,
                        provider_match_id="sr:sport_event:123456",
                        generated_wall_ns=1_785_607_200_000_000_000,
                        captured_wall_ns=1_785_607_200_000_000_000,
                        status="live",
                        match_status="1st_set",
                        payload_sha256=sha256(payload).hexdigest(),
                        raw_path=raw_path,
                        progression="initial",
                        last_event_id=None,
                        terminal_reason=None,
                    )
                )
                second = ledger.reserve("timeline")
                ledger.record_attempt_outcome(
                    second,
                    outcome="failed",
                    code="sportradar_transport_unavailable",
                )
            with TrialUsageLedger(root) as recovered:
                self.assertEqual(recovered.recovered_unclean_sessions, 1)
            with TrialUsageLedger(root) as clean:
                self.assertEqual(clean.recovered_unclean_sessions, 0)

    def test_recovery_disposition_after_halt_terminal_remains_restartable(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import TrialUsageLedger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TrialUsageLedger(root) as ledger:
                reservation = ledger.reserve("summary")
                ledger.persist_raw(
                    reservation,
                    b"{}",
                    captured_wall_ns=1_785_607_200_000_000_000,
                )
                ledger.record_attempt_outcome(
                    reservation,
                    outcome="captured",
                    code="sportradar_capture_persisted",
                )
                ledger.record_session_terminal(
                    command="check",
                    provider_match_id="sr:sport_event:123456",
                    reason="halted",
                    code="sportradar_total_deadline",
                )
            with TrialUsageLedger(root) as recovered:
                self.assertEqual(recovered.recovered_incomplete_captures, 1)
            with TrialUsageLedger(root) as clean:
                self.assertEqual(clean.recovered_incomplete_captures, 0)

    def test_transport_enforces_total_deadline_and_closes_response(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = _FakeClock()
            response = _FakeResponse(b"{}")

            def delayed_get(*_: object, **__: object) -> _FakeResponse:
                clock.sleep(16.0)
                return response

            with patch(
                "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                side_effect=delayed_get,
            ):
                with TrialUsageLedger(
                    root,
                    monotonic_ns=clock.monotonic,
                    wall_ns=clock.wall,
                    sleeper=clock.sleep,
                ) as ledger:
                    transport = SportradarTrialTransport(
                        api_key="safe-trial-key",
                        ledger=ledger,
                        monotonic_ns=clock.monotonic,
                    )
                    with self.assertRaisesRegex(
                        SportradarTrialObserverError,
                        "sportradar_total_deadline",
                    ):
                        transport.fetch_live_summaries()
            self.assertTrue(response.closed)
            self.assertEqual(list((root / "raw").glob("*.json")), [])

    def test_hard_deadline_interrupts_a_blocking_http_call(self) -> None:
        from inci_tennis_io import sportradar_trial_transport as transport_module
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        def blocked_get(*_: object, **__: object) -> object:
            time.sleep(0.25)
            return _FakeResponse(b"{}")

        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            with (
                patch.object(transport_module, "_TOTAL_DEADLINE_NS", 50_000_000),
                patch(
                    "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                    side_effect=blocked_get,
                ),
                TrialUsageLedger(Path(directory)) as ledger,
            ):
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_total_deadline",
                ):
                    SportradarTrialTransport(
                        api_key="safe-trial-key",
                        ledger=ledger,
                    ).fetch_live_summaries()
            self.assertLess(time.monotonic() - started, 0.2)

    def test_hard_deadline_does_not_retry_blocking_response_close(self) -> None:
        from inci_tennis_io import sportradar_trial_transport as transport_module
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        class BlockingCloseResponse(_FakeResponse):
            def __init__(self) -> None:
                super().__init__(b"forbidden", status_code=403)
                self.close_calls = 0

            def close(self) -> None:
                self.close_calls += 1
                time.sleep(0.25)

        response = BlockingCloseResponse()
        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            with (
                patch.object(transport_module, "_TOTAL_DEADLINE_NS", 50_000_000),
                patch(
                    "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                    return_value=response,
                ),
                TrialUsageLedger(Path(directory)) as ledger,
            ):
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_total_deadline",
                ):
                    SportradarTrialTransport(
                        api_key="safe-trial-key",
                        ledger=ledger,
                    ).fetch_live_summaries()
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertEqual(response.close_calls, 1)

    def test_transport_stream_cap_length_mismatch_and_key_reflection(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        class ChunkedResponse(_FakeResponse):
            def __init__(
                self,
                chunks: list[bytes],
                headers: dict[str, str],
            ) -> None:
                super().__init__(b"", headers=headers)
                self.chunks = chunks

            def iter_content(self, chunk_size: int) -> list[bytes]:
                return self.chunks

        cases = (
            (
                ChunkedResponse(
                    [b"x" * 8_388_608, b"y"],
                    {"Content-Type": "application/json"},
                ),
                "sportradar_body_too_large",
            ),
            (
                ChunkedResponse(
                    [b"{}"],
                    {
                        "Content-Type": "application/json",
                        "Content-Length": "3",
                    },
                ),
                "sportradar_content_length_mismatch",
            ),
            (
                ChunkedResponse(
                    [b'{"echo":"safe-trial-key"}'],
                    {"Content-Type": "application/json"},
                ),
                "sportradar_credential_reflected",
            ),
        )
        for index, (response, code) in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch(
                    "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                    return_value=response,
                ):
                    with TrialUsageLedger(root) as ledger:
                        transport = SportradarTrialTransport(
                            api_key="safe-trial-key",
                            ledger=ledger,
                        )
                        with self.assertRaisesRegex(
                            SportradarTrialObserverError,
                            code,
                        ):
                            transport.fetch_live_summaries()
                self.assertTrue(response.closed)
                self.assertEqual(list((root / "raw").glob("*.json")), [])


class _FakeLedger:
    def __init__(self) -> None:
        self.session_attempts = 0
        self.remaining_session_attempts = 400
        self.remaining_access_attempts = 1_000
        self.records: list[object] = []
        self.parser_failures: list[dict[str, object]] = []
        self.terminals: list[dict[str, object]] = []

    def __enter__(self) -> _FakeLedger:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def record_observation(self, record: object) -> None:
        self.records.append(record)

    def record_parser_failure(self, **values: object) -> None:
        self.parser_failures.append(values)

    def record_session_terminal(self, **values: object) -> None:
        self.terminals.append(values)


class _FakeTrialTransport:
    def __init__(self, captures: list[bytes], ledger: _FakeLedger) -> None:
        self.captures = captures
        self.ledger = ledger
        self.routes: list[str] = []

    def __enter__(self) -> _FakeTrialTransport:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def _capture(self, route: str) -> object:
        from inci_tennis_io.sportradar_trial_transport import (
            TrialAttemptReservation,
            TrialCapture,
        )

        self.routes.append(route)
        self.ledger.session_attempts += 1
        self.ledger.remaining_session_attempts -= 1
        self.ledger.remaining_access_attempts -= 1
        payload = self.captures.pop(0)
        captured_wall_ns = (
            1_785_607_202_000_000_000
            + (self.ledger.session_attempts - 1) * 10_000_000_000
        )
        return TrialCapture(
            reservation=TrialAttemptReservation(
                session_id="eaf9bd2c-1f67-4c40-ab31-da6781d7d6d5",
                session_attempt=self.ledger.session_attempts,
                access_attempt=self.ledger.session_attempts,
                route=route,
                started_wall_ns=1_785_607_200_000_000_000,
            ),
            captured_wall_ns=captured_wall_ns,
            raw_path=Path(f"/{route}.json"),
            payload=payload,
        )

    def fetch_live_summaries(self) -> object:
        return self._capture("live_summaries")

    def fetch_summary(self, _: str) -> object:
        return self._capture("summary")

    def fetch_timeline(self, _: str) -> object:
        return self._capture("timeline")


class SportradarTrialCliTests(unittest.TestCase):
    def _dependencies(
        self, captures: list[bytes]
    ) -> tuple[object, _FakeLedger, _FakeTrialTransport, _FakeClock]:
        from inci_tennis_runtime.sportradar_trial_cli import TrialCliDependencies

        ledger = _FakeLedger()
        transport = _FakeTrialTransport(captures, ledger)
        clock = _FakeClock()
        dependencies = TrialCliDependencies(
            ledger_factory=lambda: ledger,
            transport_factory=lambda **_: transport,
            sample_counter=clock.monotonic,
            sleeper=clock.sleep,
        )
        return dependencies, ledger, transport, clock

    def test_missing_key_fails_before_state_or_network(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import (
            TrialCliDependencies,
            run_cli,
        )

        touched: list[str] = []
        dependencies = TrialCliDependencies(
            ledger_factory=lambda: touched.append("ledger"),
            transport_factory=lambda **_: touched.append("transport"),
            sample_counter=lambda: 0,
            sleeper=lambda _: None,
        )
        stderr = io.StringIO()
        result = run_cli(
            ["--list-live"],
            environ={},
            stdout=io.StringIO(),
            stderr=stderr,
            dependencies=dependencies,
        )
        self.assertEqual(result, 2)
        self.assertEqual(touched, [])
        self.assertIn("SPORTRADAR_API_KEY", stderr.getvalue())

        closed_errors = io.StringIO()
        closed_errors.close()
        self.assertEqual(
            run_cli(
                ["--list-live"],
                environ={},
                stdout=io.StringIO(),
                stderr=closed_errors,
                dependencies=dependencies,
            ),
            2,
        )

    def test_usage_errors_do_not_echo_untrusted_arguments(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import run_cli

        secret = "super-secret-trial-key"
        stderr = io.StringIO()
        self.assertEqual(
            run_cli(
                ["--list-live", "--api-key", secret],
                environ={},
                stdout=io.StringIO(),
                stderr=stderr,
            ),
            2,
        )
        self.assertNotIn(secret, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "ERROR: invalid command arguments\n")

    def test_list_live_and_check_are_one_call_read_only_commands(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import run_cli

        dependencies, ledger, transport, _ = self._dependencies(
            [_live_summaries_payload()]
        )
        stdout = io.StringIO()
        self.assertEqual(
            run_cli(
                ["--list-live"],
                environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                stdout=stdout,
                stderr=io.StringIO(),
                dependencies=dependencies,
            ),
            0,
        )
        self.assertEqual(transport.routes, ["live_summaries"])
        self.assertIn("sr:sport_event:123456", stdout.getvalue())
        self.assertIn("Player Home vs Player Away", stdout.getvalue())
        self.assertEqual(len(ledger.records), 1)

        dependencies, ledger, transport, _ = self._dependencies(
            [_summary_payload()]
        )
        stdout = io.StringIO()
        self.assertEqual(
            run_cli(
                ["--check", "--match-id", "sr:sport_event:123456"],
                environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                stdout=stdout,
                stderr=io.StringIO(),
                dependencies=dependencies,
            ),
            0,
        )
        self.assertEqual(transport.routes, ["summary"])
        self.assertIn("READ ONLY", stdout.getvalue())
        self.assertIn("30 - 15", stdout.getvalue())
        self.assertEqual(len(ledger.records), 1)

    def test_observe_polls_every_ten_seconds_and_stops_when_match_closes(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import run_cli

        first_timeline = _timeline_payload(include_second=False)
        closed = json.loads(_timeline_payload())
        closed["generated_at"] = "2026-08-01T18:00:12+00:00"
        closed["sport_event_status"].update(
            {"status": "closed", "match_status": "ended"}
        )
        dependencies, ledger, transport, clock = self._dependencies(
            [
                _summary_payload(),
                first_timeline,
                json.dumps(closed, separators=(",", ":")).encode("utf-8"),
            ]
        )
        stdout = io.StringIO()
        result = run_cli(
            [
                "--observe",
                "--match-id",
                "sr:sport_event:123456",
                "--duration-seconds",
                "60",
            ],
            environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
            stdout=stdout,
            stderr=io.StringIO(),
            dependencies=dependencies,
        )
        self.assertEqual(result, 0)
        self.assertEqual(transport.routes, ["summary", "timeline", "timeline"])
        self.assertEqual(clock.sleeps, [10.0, 10.0])
        self.assertIn("closed", stdout.getvalue())
        self.assertEqual(len(ledger.records), 3)
        self.assertEqual(getattr(ledger.records[-1], "terminal_reason"), "closed")
        self.assertEqual(ledger.terminals[-1]["reason"], "closed")

    def test_ended_is_observed_until_provider_closes_the_match(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import run_cli

        ended = json.loads(_timeline_payload(include_second=False))
        ended["generated_at"] = "2026-08-01T18:00:12+00:00"
        ended["sport_event_status"].update(
            {"status": "ended", "match_status": "ended"}
        )
        closed = json.loads(_timeline_payload())
        closed["generated_at"] = "2026-08-01T18:00:22+00:00"
        closed["sport_event_status"].update(
            {"status": "closed", "match_status": "ended"}
        )
        dependencies, ledger, transport, _ = self._dependencies(
            [
                _summary_payload(),
                json.dumps(ended).encode("utf-8"),
                json.dumps(closed).encode("utf-8"),
            ]
        )
        result = run_cli(
            [
                "--observe",
                "--match-id",
                "sr:sport_event:123456",
                "--duration-seconds",
                "60",
            ],
            environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=dependencies,
        )
        self.assertEqual(result, 0)
        self.assertEqual(transport.routes, ["summary", "timeline", "timeline"])
        self.assertIsNone(getattr(ledger.records[-2], "terminal_reason"))
        self.assertEqual(ledger.terminals[-1]["reason"], "closed")

    def test_duration_and_interrupt_write_clean_terminal_reasons(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import (
            TrialCliDependencies,
            run_cli,
        )

        dependencies, ledger, transport, _ = self._dependencies(
            [_summary_payload(), _timeline_payload(include_second=False)]
        )
        self.assertEqual(
            run_cli(
                [
                    "--observe",
                    "--match-id",
                    "sr:sport_event:123456",
                    "--duration-seconds",
                    "20",
                ],
                environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                dependencies=dependencies,
            ),
            0,
        )
        self.assertEqual(transport.routes, ["summary", "timeline"])
        self.assertEqual(ledger.terminals[-1]["reason"], "duration_elapsed")

        dependencies, ledger, transport, clock = self._dependencies(
            [_summary_payload()]
        )

        def interrupt(_: float) -> None:
            raise KeyboardInterrupt

        interrupted = TrialCliDependencies(
            ledger_factory=dependencies.ledger_factory,
            transport_factory=dependencies.transport_factory,
            sample_counter=clock.monotonic,
            sleeper=interrupt,
        )
        self.assertEqual(
            run_cli(
                [
                    "--observe",
                    "--match-id",
                    "sr:sport_event:123456",
                    "--duration-seconds",
                    "20",
                ],
                environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                dependencies=interrupted,
            ),
            130,
        )
        self.assertEqual(transport.routes, ["summary"])
        self.assertEqual(ledger.terminals[-1]["reason"], "operator_interrupt")

    def test_stale_live_source_halts_before_burning_more_calls(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import run_cli

        stale = json.loads(_summary_payload())
        stale["generated_at"] = "2026-08-01T17:58:00+00:00"
        dependencies, ledger, transport, _ = self._dependencies(
            [json.dumps(stale).encode("utf-8")]
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        result = run_cli(
            [
                "--observe",
                "--match-id",
                "sr:sport_event:123456",
                "--duration-seconds",
                "60",
            ],
            environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
            stdout=stdout,
            stderr=stderr,
            dependencies=dependencies,
        )
        self.assertEqual(result, 1)
        self.assertEqual(transport.routes, ["summary"])
        self.assertIn("SOURCE AGE", stdout.getvalue())
        self.assertIn("sportradar_source_stale", stderr.getvalue())
        self.assertEqual(ledger.terminals[-1]["reason"], "halted")

    def test_stale_check_and_list_live_halt_after_durable_observation(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import run_cli

        stale_summary = json.loads(_summary_payload())
        stale_summary["generated_at"] = "2026-08-01T17:58:00+00:00"
        stale_list = json.loads(_live_summaries_payload())
        stale_list["generated_at"] = "2026-08-01T17:58:00+00:00"
        cases = (
            (
                ["--check", "--match-id", "sr:sport_event:123456"],
                json.dumps(stale_summary).encode("utf-8"),
                "summary",
            ),
            (
                ["--list-live"],
                json.dumps(stale_list).encode("utf-8"),
                "live_summaries",
            ),
        )
        for argv, payload, route in cases:
            with self.subTest(route=route):
                dependencies, ledger, transport, _ = self._dependencies(
                    [payload]
                )
                stderr = io.StringIO()
                self.assertEqual(
                    run_cli(
                        argv,
                        environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                        stdout=io.StringIO(),
                        stderr=stderr,
                        dependencies=dependencies,
                    ),
                    1,
                )
                self.assertEqual(transport.routes, [route])
                self.assertEqual(len(ledger.records), 1)
                self.assertEqual(ledger.terminals[-1]["reason"], "halted")
                self.assertEqual(
                    ledger.terminals[-1]["code"],
                    "sportradar_source_stale",
                )

    def test_old_terminal_match_wins_over_stale_source_age(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import run_cli

        closed = json.loads(_summary_payload())
        closed["generated_at"] = "2026-08-01T17:00:00+00:00"
        closed["sport_event_status"].update(
            {"status": "closed", "match_status": "ended"}
        )
        dependencies, ledger, transport, _ = self._dependencies(
            [json.dumps(closed).encode("utf-8")]
        )
        self.assertEqual(
            run_cli(
                ["--check", "--match-id", "sr:sport_event:123456"],
                environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                dependencies=dependencies,
            ),
            0,
        )
        self.assertEqual(transport.routes, ["summary"])
        self.assertEqual(ledger.terminals[-1]["reason"], "closed")

    def test_source_time_ahead_records_parser_failure_and_exact_terminal_code(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import run_cli

        ahead = json.loads(_summary_payload())
        ahead["generated_at"] = "2026-08-01T18:01:00+00:00"
        dependencies, ledger, _, _ = self._dependencies(
            [json.dumps(ahead).encode("utf-8")]
        )
        self.assertEqual(
            run_cli(
                ["--check", "--match-id", "sr:sport_event:123456"],
                environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                dependencies=dependencies,
            ),
            1,
        )
        self.assertEqual(
            ledger.parser_failures[-1]["code"],
            "sportradar_source_time_ahead",
        )
        self.assertEqual(
            ledger.terminals[-1],
            {
                "command": "check",
                "provider_match_id": "sr:sport_event:123456",
                "reason": "halted",
                "code": "sportradar_source_time_ahead",
            },
        )

    def test_broken_stdout_records_output_unavailable_terminal(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import run_cli

        class BrokenOutput(io.StringIO):
            def write(self, _: str) -> int:
                raise BrokenPipeError

        dependencies, ledger, _, _ = self._dependencies(
            [_summary_payload()]
        )
        self.assertEqual(
            run_cli(
                ["--check", "--match-id", "sr:sport_event:123456"],
                environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                stdout=BrokenOutput(),
                stderr=io.StringIO(),
                dependencies=dependencies,
            ),
            1,
        )
        self.assertEqual(
            ledger.terminals[-1]["code"],
            "sportradar_output_unavailable",
        )

        class ShortOutput(io.StringIO):
            def write(self, _: str) -> int:
                return 0

        dependencies, ledger, _, _ = self._dependencies(
            [_summary_payload()]
        )
        self.assertEqual(
            run_cli(
                ["--check", "--match-id", "sr:sport_event:123456"],
                environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                stdout=ShortOutput(),
                stderr=io.StringIO(),
                dependencies=dependencies,
            ),
            1,
        )
        self.assertEqual(
            ledger.terminals[-1]["code"],
            "sportradar_output_unavailable",
        )

        dependencies, ledger, _, _ = self._dependencies(
            [_summary_payload()]
        )
        closed_output = io.StringIO()
        closed_output.close()
        self.assertEqual(
            run_cli(
                ["--check", "--match-id", "sr:sport_event:123456"],
                environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                stdout=closed_output,
                stderr=io.StringIO(),
                dependencies=dependencies,
            ),
            1,
        )
        self.assertEqual(
            ledger.terminals[-1]["code"],
            "sportradar_output_unavailable",
        )

    def test_missing_tiebreak_state_renders_unknown(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import run_cli

        document = json.loads(_summary_payload())
        del document["sport_event_status"]["game_state"]["tie_break"]
        dependencies, _, _, _ = self._dependencies(
            [json.dumps(document).encode("utf-8")]
        )
        stdout = io.StringIO()
        self.assertEqual(
            run_cli(
                ["--check", "--match-id", "sr:sport_event:123456"],
                environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                stdout=stdout,
                stderr=io.StringIO(),
                dependencies=dependencies,
            ),
            0,
        )
        self.assertRegex(stdout.getvalue(), r"TIEBREAK\s+\| --\s+\|")

    def test_sigterm_is_recorded_as_operator_interrupt_and_handlers_restore(self) -> None:
        import signal
        from inci_tennis_runtime import sportradar_trial_cli as cli

        dependencies, ledger, _, clock = self._dependencies(
            [_summary_payload()]
        )
        installed: dict[int, object] = {}
        handler_returned: list[bool] = []
        terminal_handler_was_scoped: list[bool] = []
        ledger_open_was_scoped: list[bool] = []
        original = object()

        original_terminal = ledger.record_session_terminal

        def record_terminal(**values: object) -> None:
            terminal_handler_was_scoped.append(
                callable(installed.get(signal.SIGTERM))
                and installed.get(signal.SIGTERM) is not original
            )
            original_terminal(**values)

        ledger.record_session_terminal = record_terminal  # type: ignore[method-assign]

        def get_handler(_: int) -> object:
            return original

        def set_handler(number: int, handler: object) -> object:
            installed[number] = handler
            return original

        def interrupt(_: float) -> None:
            handler = installed[signal.SIGTERM]
            self.assertTrue(callable(handler))
            handler(signal.SIGTERM, None)  # type: ignore[operator]
            handler_returned.append(True)

        def open_ledger() -> object:
            ledger_open_was_scoped.append(
                callable(installed.get(signal.SIGTERM))
                and installed.get(signal.SIGTERM) is not original
            )
            return ledger

        signaled = cli.TrialCliDependencies(
            ledger_factory=open_ledger,
            transport_factory=dependencies.transport_factory,
            sample_counter=clock.monotonic,
            sleeper=interrupt,
        )
        with (
            patch("signal.getsignal", side_effect=get_handler),
            patch("signal.signal", side_effect=set_handler),
        ):
            self.assertEqual(
                cli.run_cli(
                    [
                        "--observe",
                        "--match-id",
                        "sr:sport_event:123456",
                        "--duration-seconds",
                        "20",
                    ],
                    environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                    dependencies=signaled,
                ),
                130,
            )
        self.assertEqual(ledger.terminals[-1]["reason"], "operator_interrupt")
        self.assertEqual(handler_returned, [True])
        self.assertEqual(terminal_handler_was_scoped, [True])
        self.assertEqual(ledger_open_was_scoped, [True])
        expected_signals = {signal.SIGINT, signal.SIGTERM}
        if hasattr(signal, "SIGHUP"):
            expected_signals.add(signal.SIGHUP)
        self.assertEqual(set(installed), expected_signals)
        for number in expected_signals:
            self.assertIs(installed[number], original)
        self.assertIs(installed[signal.SIGTERM], original)

    def test_provider_text_cannot_inject_terminal_control_sequences(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import run_cli

        payload = json.loads(_live_summaries_payload())
        payload["summaries"][0]["sport_event"]["competitors"][0]["name"] = (
            "Bad\x1b[2J\nName"
        )
        dependencies, _, _, _ = self._dependencies(
            [json.dumps(payload).encode("utf-8")]
        )
        stdout = io.StringIO()
        self.assertEqual(
            run_cli(
                ["--list-live"],
                environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                stdout=stdout,
                stderr=io.StringIO(),
                dependencies=dependencies,
            ),
            0,
        )
        self.assertNotIn("\x1b", stdout.getvalue())
        self.assertIn("Bad?[2J Name", stdout.getvalue())

    def test_first_timeline_cannot_roll_back_the_initial_summary(self) -> None:
        from inci_tennis_runtime.sportradar_trial_cli import run_cli

        older = json.loads(_timeline_payload())
        older["generated_at"] = "2026-08-01T18:00:01+00:00"
        dependencies, ledger, transport, _ = self._dependencies(
            [
                _summary_payload(),
                json.dumps(older, separators=(",", ":")).encode("utf-8"),
            ]
        )
        stderr = io.StringIO()
        result = run_cli(
            [
                "--observe",
                "--match-id",
                "sr:sport_event:123456",
                "--duration-seconds",
                "20",
            ],
            environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
            stdout=io.StringIO(),
            stderr=stderr,
            dependencies=dependencies,
        )
        self.assertEqual(result, 1)
        self.assertEqual(transport.routes, ["summary", "timeline"])
        self.assertEqual(len(ledger.records), 1)
        self.assertEqual(len(ledger.parser_failures), 1)
        self.assertEqual(ledger.terminals[-1]["reason"], "halted")
        self.assertIn("sportradar_timeline_before_summary", stderr.getvalue())

    def test_runtime_source_contains_no_execution_authority(self) -> None:
        import inci_tennis_runtime.sportradar_trial_cli as cli

        source = Path(cli.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "executor",
            "kalshi_client",
            "portfolio/orders",
            ".post(",
            ".put(",
            ".patch(",
            ".delete(",
        ):
            self.assertNotIn(forbidden, source.lower())

    def test_check_persists_raw_usage_and_observation_with_real_io(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialTransport,
            TrialUsageLedger,
        )
        from inci_tennis_runtime.sportradar_trial_cli import (
            TrialCliDependencies,
            run_cli,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = _FakeClock()
            response = _FakeResponse(_summary_payload())
            ledger = TrialUsageLedger(
                root,
                monotonic_ns=clock.monotonic,
                wall_ns=clock.wall,
                sleeper=clock.sleep,
            )
            dependencies = TrialCliDependencies(
                ledger_factory=lambda: ledger,
                transport_factory=lambda **values: SportradarTrialTransport(
                    **values,
                    wall_ns=clock.wall,
                    monotonic_ns=clock.monotonic,
                ),
                sample_counter=clock.monotonic,
                sleeper=clock.sleep,
            )
            with patch(
                "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                return_value=response,
            ):
                result = run_cli(
                    [
                        "--check",
                        "--match-id",
                        "sr:sport_event:123456",
                    ],
                    environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                    dependencies=dependencies,
                )
            self.assertEqual(result, 0)
            self.assertEqual(
                len((root / "usage.jsonl").read_text().splitlines()), 1
            )
            rows = (root / "observations.jsonl").read_text().splitlines()
            self.assertEqual(len(rows), 2)
            self.assertEqual(json.loads(rows[0])["command"], "check")
            self.assertEqual(
                json.loads(rows[0])["captured_wall_ns"],
                1_785_607_200_000_000_000,
            )
            self.assertEqual(json.loads(rows[1])["reason"], "check_complete")
            outcomes = (root / "outcomes.jsonl").read_text().splitlines()
            self.assertEqual(len(outcomes), 1)
            self.assertEqual(json.loads(outcomes[0])["outcome"], "captured")
            self.assertEqual(
                list((root / "raw").glob("*.json"))[0].read_bytes(),
                _summary_payload(),
            )

    def test_parser_failure_is_durable_and_session_is_closed(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialTransport,
            TrialUsageLedger,
        )
        from inci_tennis_runtime.sportradar_trial_cli import (
            TrialCliDependencies,
            run_cli,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = TrialUsageLedger(root)
            dependencies = TrialCliDependencies(
                ledger_factory=lambda: ledger,
                transport_factory=SportradarTrialTransport,
            )
            with patch(
                "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                return_value=_FakeResponse(b'{"wrong":true}'),
            ):
                result = run_cli(
                    ["--check", "--match-id", "sr:sport_event:123456"],
                    environ={"SPORTRADAR_API_KEY": "safe-trial-key"},
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                    dependencies=dependencies,
                )
            self.assertEqual(result, 1)
            rows = [
                json.loads(value)
                for value in (root / "observations.jsonl")
                .read_text()
                .splitlines()
            ]
            self.assertEqual(
                [row["schema"] for row in rows],
                [
                    "inci-sportradar-trial-parser-failure-v1",
                    "inci-sportradar-trial-terminal-v1",
                ],
            )
            self.assertEqual(rows[0]["code"], "sportradar_summary_schema_unknown")
            self.assertEqual(rows[1]["reason"], "halted")
            rows[1].update({"reason": "check_complete", "code": None})
            (root / "observations.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            from inci_tennis_io.sportradar_trial_transport import (
                SportradarTrialObserverError,
            )

            with self.assertRaisesRegex(
                SportradarTrialObserverError,
                "sportradar_audit_ledger_corrupt",
            ):
                TrialUsageLedger(root)

    def test_receive_time_is_sampled_before_raw_fsync(self) -> None:
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialTransport,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = _FakeClock()
            response = _FakeResponse(_summary_payload())
            with TrialUsageLedger(
                root,
                monotonic_ns=clock.monotonic,
                wall_ns=clock.wall,
                sleeper=clock.sleep,
            ) as ledger:
                original = ledger.persist_raw

                def delayed_persist(*values: object, **options: object) -> Path:
                    clock.sleep(5.0)
                    return original(*values, **options)

                ledger.persist_raw = delayed_persist  # type: ignore[method-assign]
                with patch(
                    "inci_tennis_io.sportradar_trial_transport.requests.Session.get",
                    return_value=response,
                ):
                    capture = SportradarTrialTransport(
                        api_key="safe-trial-key",
                        ledger=ledger,
                        wall_ns=clock.wall,
                        monotonic_ns=clock.monotonic,
                    ).fetch_summary("sr:sport_event:123456")
            self.assertEqual(
                capture.captured_wall_ns,
                1_785_607_200_000_000_000,
            )


if __name__ == "__main__":
    unittest.main()
