from __future__ import annotations

from decimal import Context, Decimal, localcontext
import unittest

from inci_tennis_expert.contracts import MatchFormat, PlayerSide

try:
    import inci_tennis_expert.prematch_model as prematch_model
except ModuleNotFoundError:
    prematch_model = None  # type: ignore[assignment]


BO3 = MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS
BO5 = MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS


class PrematchModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(
            prematch_model,
            "inci_tennis_expert.prematch_model is not implemented",
        )

    def parameters(self):
        assert prematch_model is not None
        return prematch_model.EloParameters(
            version="ordinary-and-surface-elo-v1",
            baseline=Decimal("1500"),
            k_factor=Decimal("32"),
            scale=Decimal("400"),
        )

    def match(self, **changes: object):
        assert prematch_model is not None
        values: dict[str, object] = {
            "match_id": "historical-1",
            "home_player_id": "target-home",
            "away_player_id": "opponent-x",
            "surface": "clay",
            "winner": PlayerSide.HOME,
            "completed_wall_ns": 50,
            "received_wall_ns": 60,
            "match_format": BO3,
        }
        values.update(changes)
        return prematch_model.HistoricalMatch(**values)

    def evidence(self, **changes: object):
        assert prematch_model is not None
        values: dict[str, object] = {
            "evidence_id": "evidence-1",
            "feature": prematch_model.EvidenceFeature.ELO,
            "probability": Decimal("0.60"),
            "event_wall_ns": 50,
            "received_wall_ns": 60,
        }
        values.update(changes)
        return prematch_model.PrematchEvidence(**values)

    def test_cutoff_excludes_future_receipts_and_events_without_backdating(
        self,
    ) -> None:
        assert prematch_model is not None
        rows = (
            self.match(
                match_id="eligible-at-receipt-boundary",
                completed_wall_ns=80,
                received_wall_ns=90,
            ),
            self.match(
                match_id="eligible-at-receipt-boundary",
                completed_wall_ns=40,
                received_wall_ns=91,
            ),
            self.match(
                match_id="completion-at-cutoff",
                completed_wall_ns=90,
                received_wall_ns=90,
            ),
        )

        try:
            snapshot = prematch_model.build_elo_snapshot(
                rows,
                player_home_id="target-home",
                player_away_id="target-away",
                surface="clay",
                scheduled_start_wall_ns=100,
                first_in_play_received_wall_ns=90,
                match_format=BO3,
                parameters=self.parameters(),
            )
        except prematch_model.PrematchModelError as error:
            self.fail(f"post-cutoff correction was not ignored: {error}")

        self.assertEqual(snapshot.cutoff_wall_ns, 90)
        self.assertEqual(snapshot.eligible_match_count, 1)
        self.assertEqual(snapshot.excluded_future_received_count, 1)
        self.assertEqual(snapshot.excluded_not_before_cutoff_count, 1)
        self.assertEqual(snapshot.home_overall_rating, Decimal("1516.0"))
        self.assertEqual(snapshot.away_overall_rating, Decimal("1500"))
        self.assertEqual(snapshot.home_overall_support, 1)
        self.assertEqual(snapshot.away_overall_support, 0)

    def test_elo_is_chronological_and_surface_specific(self) -> None:
        assert prematch_model is not None
        rows = (
            self.match(
                match_id="hard-first",
                surface="hard",
                completed_wall_ns=10,
                received_wall_ns=11,
            ),
            self.match(
                match_id="clay-second",
                away_player_id="target-away",
                surface="clay",
                completed_wall_ns=20,
                received_wall_ns=21,
            ),
        )
        keyword_args = {
            "player_home_id": "target-home",
            "player_away_id": "target-away",
            "surface": "clay",
            "scheduled_start_wall_ns": 100,
            "first_in_play_received_wall_ns": None,
            "match_format": BO3,
            "parameters": self.parameters(),
        }

        chronological = prematch_model.build_elo_snapshot(rows, **keyword_args)
        reversed_input = prematch_model.build_elo_snapshot(
            tuple(reversed(rows)), **keyword_args
        )

        self.assertEqual(reversed_input, chronological)
        self.assertEqual(
            chronological.home_surface_rating,
            Decimal("1516.0"),
        )
        self.assertEqual(
            chronological.away_surface_rating,
            Decimal("1484.0"),
        )
        self.assertNotEqual(
            chronological.home_overall_rating,
            chronological.home_surface_rating,
        )
        self.assertEqual(chronological.home_overall_support, 2)
        self.assertEqual(chronological.home_surface_support, 1)
        self.assertEqual(chronological.away_overall_support, 1)
        self.assertEqual(chronological.away_surface_support, 1)

    def test_unknown_players_use_configured_baseline_with_zero_support(self) -> None:
        assert prematch_model is not None
        snapshot = prematch_model.build_elo_snapshot(
            (),
            player_home_id="new-home",
            player_away_id="new-away",
            surface="grass",
            scheduled_start_wall_ns=100,
            first_in_play_received_wall_ns=None,
            match_format=BO3,
            parameters=self.parameters(),
        )

        self.assertEqual(snapshot.home_overall_rating, Decimal("1500"))
        self.assertEqual(snapshot.away_overall_rating, Decimal("1500"))
        self.assertEqual(snapshot.home_surface_rating, Decimal("1500"))
        self.assertEqual(snapshot.away_surface_rating, Decimal("1500"))
        self.assertEqual(snapshot.home_overall_support, 0)
        self.assertEqual(snapshot.away_surface_support, 0)

    def test_elo_arithmetic_ignores_ambient_decimal_context(self) -> None:
        assert prematch_model is not None
        rows = (
            self.match(
                match_id="first",
                home_player_id="home",
                away_player_id="opponent",
                surface="hard",
                completed_wall_ns=10,
                received_wall_ns=11,
            ),
            self.match(
                match_id="second",
                home_player_id="home",
                away_player_id="away",
                surface="hard",
                completed_wall_ns=20,
                received_wall_ns=21,
            ),
        )
        parameters = prematch_model.EloParameters(
            version="decimal-context-independent-v1",
            baseline=Decimal("1501"),
            k_factor=Decimal("2420000"),
            scale=Decimal("1210000"),
        )

        with localcontext(Context(prec=6)):
            snapshot = prematch_model.build_elo_snapshot(
                rows,
                player_home_id="home",
                player_away_id="away",
                surface="hard",
                scheduled_start_wall_ns=100,
                first_in_play_received_wall_ns=None,
                match_format=BO3,
                parameters=parameters,
            )

        self.assertEqual(snapshot.home_overall_rating, Decimal("1431501"))
        self.assertEqual(snapshot.away_overall_rating, Decimal("-218499"))

    def test_evidence_uses_same_causal_cutoff_and_explicit_missing_mask(self) -> None:
        assert prematch_model is not None
        observations = (
            self.evidence(
                evidence_id="elo-eligible",
                feature=prematch_model.EvidenceFeature.ELO,
                probability=Decimal("0.60"),
                event_wall_ns=50,
                received_wall_ns=60,
            ),
            self.evidence(
                evidence_id="serve-return-explicitly-missing",
                feature=prematch_model.EvidenceFeature.SERVE_RETURN,
                probability=None,
                event_wall_ns=70,
                received_wall_ns=80,
            ),
            self.evidence(
                evidence_id="elo-eligible",
                feature=prematch_model.EvidenceFeature.ELO,
                probability=Decimal("0.75"),
                event_wall_ns=40,
                received_wall_ns=91,
            ),
            self.evidence(
                evidence_id="external-at-cutoff",
                feature=prematch_model.EvidenceFeature.EXTERNAL,
                probability=Decimal("0.55"),
                event_wall_ns=90,
                received_wall_ns=90,
            ),
        )

        try:
            snapshot = prematch_model.build_evidence_snapshot(
                observations,
                scheduled_start_wall_ns=100,
                first_in_play_received_wall_ns=90,
                match_format=BO3,
            )
        except prematch_model.PrematchModelError as error:
            self.fail(f"post-cutoff evidence correction was not ignored: {error}")

        self.assertEqual(snapshot.cutoff_wall_ns, 90)
        self.assertEqual(
            snapshot.feature_mask,
            (True, False, False, False),
        )
        self.assertEqual(
            snapshot.probabilities,
            (Decimal("0.60"), None, None, None),
        )
        self.assertEqual(snapshot.eligible_observation_count, 2)
        self.assertEqual(snapshot.excluded_future_received_count, 1)
        self.assertEqual(snapshot.excluded_not_before_cutoff_count, 1)

    def test_log_odds_combiner_requires_exact_artifact_mask(self) -> None:
        assert prematch_model is not None
        snapshot = prematch_model.build_evidence_snapshot(
            (self.evidence(),),
            scheduled_start_wall_ns=100,
            first_in_play_received_wall_ns=None,
            match_format=BO3,
        )
        matching = prematch_model.FrozenLogOddsArtifact(
            version="prematch-log-odds-v1",
            feature_mask=(True, False, False, False),
            intercept=Decimal("0"),
            coefficients=(
                Decimal("1"),
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
            ),
        )
        mismatching = prematch_model.FrozenLogOddsArtifact(
            version="prematch-log-odds-v1-wrong-mask",
            feature_mask=(True, True, False, False),
            intercept=Decimal("0"),
            coefficients=(
                Decimal("1"),
                Decimal("1"),
                Decimal("0"),
                Decimal("0"),
            ),
        )

        result = prematch_model.combine_log_odds(snapshot, matching)

        self.assertEqual(result.probability, Decimal("0.600000000000000000"))
        self.assertEqual(result.feature_mask, (True, False, False, False))
        with self.assertRaises(prematch_model.PrematchModelError):
            prematch_model.combine_log_odds(snapshot, mismatching)

    def test_probability_and_standard_bo3_invariants_are_enforced(self) -> None:
        assert prematch_model is not None
        for probability in (
            Decimal("-0.01"),
            Decimal("0"),
            Decimal("1"),
            Decimal("1.01"),
        ):
            with self.subTest(probability=probability):
                with self.assertRaises(prematch_model.PrematchModelError):
                    self.evidence(probability=probability)

        with self.assertRaises(prematch_model.PrematchModelError):
            prematch_model.build_elo_snapshot(
                (),
                player_home_id="home",
                player_away_id="away",
                surface="hard",
                scheduled_start_wall_ns=100,
                first_in_play_received_wall_ns=None,
                match_format=BO5,
                parameters=self.parameters(),
            )


if __name__ == "__main__":
    unittest.main()
