from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True

from inci_tennis_adapters.espn_tennis import (
    ESPN_PROVIDER_ID,
    EspnScoreboardNormalization,
    EspnTennisError,
    espn_source_lineage_sha256,
    live_snapshots,
    normalize_espn_scoreboard,
)
from inci_tennis_expert.contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ScoreValue,
    TerminationKind,
)


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "espn_tennis_scoreboard_v2.json"
)
LINEAGE = espn_source_lineage_sha256("c" * 64)
SOURCE_WALL_NS = 1_770_000_000_000_000_000
RECEIVED_MONOTONIC_NS = 4_000_000_000
CLOCK_UNCERTAINTY_NS = 1_000_000_000


def normalization() -> EspnScoreboardNormalization:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return normalize_espn_scoreboard(
        document,
        source_lineage_sha256=LINEAGE,
        source_wall_ns=SOURCE_WALL_NS,
        received_monotonic_ns=RECEIVED_MONOTONIC_NS,
        clock_uncertainty_ns=CLOCK_UNCERTAINTY_NS,
    )


class EspnNormalizationTests(unittest.TestCase):
    def test_fixture_normalizes_every_singles_status(self) -> None:
        result = normalization()
        self.assertGreaterEqual(len(result.snapshots), 5)
        statuses = {
            snapshot.provider_match_id: snapshot.status
            for snapshot in result.snapshots
        }
        self.assertIn(MatchStatus.LIVE, statuses.values())
        self.assertIn(MatchStatus.ENDED, statuses.values())
        self.assertIn(MatchStatus.SCHEDULED, statuses.values())
        for snapshot in result.snapshots:
            self.assertEqual(snapshot.provider_source_id, ESPN_PROVIDER_ID)
            self.assertEqual(snapshot.source_lineage_sha256, LINEAGE)
            self.assertTrue(snapshot.snapshot_complete)
            self.assertEqual(snapshot.correction_epoch, 0)
            self.assertNotEqual(
                snapshot.home_player_id,
                snapshot.away_player_id,
            )

    def test_live_match_carries_games_and_optional_server(self) -> None:
        live = live_snapshots(normalization())
        self.assertGreaterEqual(len(live), 2)
        for snapshot in live:
            self.assertIs(snapshot.status, MatchStatus.LIVE)
            self.assertIs(snapshot.termination_kind, TerminationKind.NONE)
            self.assertIsNone(snapshot.winner)
            self.assertIn(
                snapshot.server_for_next_point,
                (PlayerSide.HOME, PlayerSide.AWAY, None),
            )
            self.assertGreaterEqual(snapshot.games_home, 0)
            self.assertGreaterEqual(snapshot.games_away, 0)

    def test_server_is_read_when_espn_publishes_possession(self) -> None:
        # ESPN publishes `possession` on some live matches and omits it on
        # others, so the server is genuinely optional on a live snapshot.
        live = live_snapshots(normalization())
        servers = [snapshot.server_for_next_point for snapshot in live]
        self.assertIn(PlayerSide.HOME, servers)
        self.assertIn(None, servers)

    def test_points_are_always_love_because_espn_omits_them(self) -> None:
        for snapshot in normalization().snapshots:
            self.assertIs(snapshot.points_home, ScoreValue.LOVE)
            self.assertIs(snapshot.points_away, ScoreValue.LOVE)

    def test_completed_tiebreak_sets_carry_tiebreak_points(self) -> None:
        found = False
        for snapshot in normalization().snapshots:
            for entry in snapshot.completed_sets:
                if entry.tiebreak_points_home is not None:
                    found = True
                    self.assertIsNotNone(entry.tiebreak_points_away)
                    self.assertIn(7, (entry.games_home, entry.games_away))
        self.assertTrue(found, "fixture should contain a tiebreak set")

    def test_retired_match_names_winner_and_retired_side(self) -> None:
        retired = [
            snapshot
            for snapshot in normalization().snapshots
            if snapshot.termination_kind is TerminationKind.RETIREMENT
        ]
        self.assertEqual(len(retired), 1)
        snapshot = retired[0]
        self.assertIs(snapshot.status, MatchStatus.ENDED)
        self.assertIsNotNone(snapshot.winner)
        self.assertIsNotNone(snapshot.retired_side)
        self.assertIsNot(snapshot.winner, snapshot.retired_side)

    def test_walkover_has_winner_without_retired_side(self) -> None:
        walkovers = [
            snapshot
            for snapshot in normalization().snapshots
            if snapshot.termination_kind is TerminationKind.WALKOVER
        ]
        self.assertEqual(len(walkovers), 1)
        self.assertIsNotNone(walkovers[0].winner)
        self.assertIsNone(walkovers[0].retired_side)

    def test_doubles_are_skipped_not_normalized(self) -> None:
        result = normalization()
        reasons = {entry.reason for entry in result.skipped}
        self.assertIn("not_singles", reasons)
        skipped_ids = {entry.competition_id for entry in result.skipped}
        normalized_ids = {
            snapshot.provider_match_id for snapshot in result.snapshots
        }
        self.assertEqual(skipped_ids & normalized_ids, set())

    def test_non_major_defaults_to_best_of_three(self) -> None:
        for snapshot in normalization().snapshots:
            self.assertIs(
                snapshot.match_format,
                MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
            )

    def test_major_mens_singles_is_best_of_five(self) -> None:
        document = json.loads(FIXTURE.read_text(encoding="utf-8"))
        document["events"][0]["major"] = True
        result = normalize_espn_scoreboard(
            document,
            source_lineage_sha256=LINEAGE,
            source_wall_ns=SOURCE_WALL_NS,
            received_monotonic_ns=RECEIVED_MONOTONIC_NS,
            clock_uncertainty_ns=CLOCK_UNCERTAINTY_NS,
        )
        formats = {snapshot.match_format for snapshot in result.snapshots}
        self.assertIn(
            MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS,
            formats,
        )

    def test_revision_advances_with_games_played(self) -> None:
        document = json.loads(FIXTURE.read_text(encoding="utf-8"))
        before = normalize_espn_scoreboard(
            document,
            source_lineage_sha256=LINEAGE,
            source_wall_ns=SOURCE_WALL_NS,
            received_monotonic_ns=RECEIVED_MONOTONIC_NS,
            clock_uncertainty_ns=CLOCK_UNCERTAINTY_NS,
        )
        live_before = live_snapshots(before)[0]

        target = live_before.provider_match_id
        for event in document["events"]:
            for grouping in event["groupings"]:
                for competition in grouping["competitions"]:
                    if competition["id"] != target:
                        continue
                    for competitor in competition["competitors"]:
                        if competitor["homeAway"] == "home":
                            competitor["linescores"][-1]["value"] += 1.0
        after = normalize_espn_scoreboard(
            document,
            source_lineage_sha256=LINEAGE,
            source_wall_ns=SOURCE_WALL_NS,
            received_monotonic_ns=RECEIVED_MONOTONIC_NS,
            clock_uncertainty_ns=CLOCK_UNCERTAINTY_NS,
        )
        live_after = after.snapshot_for(target)
        self.assertGreater(live_after.revision, live_before.revision)
        self.assertNotEqual(
            live_after.provider_event_id,
            live_before.provider_event_id,
        )

    def test_identical_documents_produce_identical_revisions(self) -> None:
        first = normalization()
        second = normalization()
        self.assertEqual(
            tuple(s.provider_event_id for s in first.snapshots),
            tuple(s.provider_event_id for s in second.snapshots),
        )

    def test_snapshot_for_unknown_match_fails_closed(self) -> None:
        result = normalization()
        with self.assertRaises(EspnTennisError):
            result.snapshot_for("not-a-real-competition")

    def test_skipped_match_lookup_fails_closed(self) -> None:
        result = normalization()
        skipped = result.skipped[0]
        with self.assertRaises(EspnTennisError):
            result.snapshot_for(skipped.competition_id)

    def test_malformed_documents_are_rejected(self) -> None:
        for bad in ([], "scoreboard", 7, None):
            with self.subTest(bad=bad):
                with self.assertRaises(EspnTennisError):
                    normalize_espn_scoreboard(
                        bad,
                        source_lineage_sha256=LINEAGE,
                        source_wall_ns=SOURCE_WALL_NS,
                        received_monotonic_ns=RECEIVED_MONOTONIC_NS,
                        clock_uncertainty_ns=CLOCK_UNCERTAINTY_NS,
                    )

    def test_tbd_draw_placeholders_are_skipped(self) -> None:
        # ESPN fills undecided draw slots with negative ids and a TBD name.
        document = json.loads(FIXTURE.read_text(encoding="utf-8"))
        competition = None
        for grouping in document["events"][0]["groupings"]:
            for candidate in grouping["competitions"]:
                if candidate["status"]["type"]["name"] == "STATUS_SCHEDULED":
                    competition = candidate
                    break
            if competition is not None:
                break
        self.assertIsNotNone(competition)
        for competitor in competition["competitors"]:
            competitor["id"] = "-3"
        result = normalize_espn_scoreboard(
            document,
            source_lineage_sha256=LINEAGE,
            source_wall_ns=SOURCE_WALL_NS,
            received_monotonic_ns=RECEIVED_MONOTONIC_NS,
            clock_uncertainty_ns=CLOCK_UNCERTAINTY_NS,
        )
        skipped = {
            entry.competition_id: entry.reason for entry in result.skipped
        }
        self.assertEqual(
            skipped.get(competition["id"]),
            "espn_player_placeholder",
        )

    def test_one_bad_competition_does_not_discard_the_poll(self) -> None:
        document = json.loads(FIXTURE.read_text(encoding="utf-8"))
        competition = document["events"][0]["groupings"][0]["competitions"][0]
        for competitor in competition["competitors"]:
            competitor["id"] = "shared-identity"
        result = normalize_espn_scoreboard(
            document,
            source_lineage_sha256=LINEAGE,
            source_wall_ns=SOURCE_WALL_NS,
            received_monotonic_ns=RECEIVED_MONOTONIC_NS,
            clock_uncertainty_ns=CLOCK_UNCERTAINTY_NS,
        )
        self.assertGreaterEqual(len(result.snapshots), 3)
        reasons = {entry.reason for entry in result.skipped}
        self.assertTrue(
            any(reason.startswith("contract_rejected:") for reason in reasons),
            reasons,
        )

    def test_competitor_count_violation_is_skipped(self) -> None:
        document = json.loads(FIXTURE.read_text(encoding="utf-8"))
        competition = document["events"][0]["groupings"][0]["competitions"][0]
        competition["competitors"] = competition["competitors"][:1]
        result = normalize_espn_scoreboard(
            document,
            source_lineage_sha256=LINEAGE,
            source_wall_ns=SOURCE_WALL_NS,
            received_monotonic_ns=RECEIVED_MONOTONIC_NS,
            clock_uncertainty_ns=CLOCK_UNCERTAINTY_NS,
        )
        reasons = {entry.reason for entry in result.skipped}
        self.assertIn("espn_competitor_count", reasons)


if __name__ == "__main__":
    unittest.main()
