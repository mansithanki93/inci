from __future__ import annotations

from decimal import Decimal
import sys
import unittest

sys.dont_write_bytecode = True

from inci_tennis_expert.contracts import ExpertContractError, PlayerSide
from inci_tennis_expert.prematch_model import (
    PREMATCH_FEATURE_DEFINITION_SHA256,
    FrozenPrematchArtifact,
    HistoricalRow,
    build_features,
    estimate_prematch,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64


def row(**changes: object) -> HistoricalRow:
    values: dict[str, object] = {
        "provider_match_id": "match-1",
        "home_player_id": "player-home",
        "away_player_id": "player-away",
        "surface": "hard",
        "match_start_wall_ns": 100,
        "observed_wall_ns": 150,
        "revised_wall_ns": 175,
        "source_lineage_sha256": SHA_A,
        "row_sha256": SHA_B,
        "winner_side": PlayerSide.HOME,
        "home_serve_points_won": 70,
        "home_serve_points_total": 100,
        "away_serve_points_won": 50,
        "away_serve_points_total": 100,
        "home_return_points_won": 50,
        "home_return_points_total": 100,
        "away_return_points_won": 30,
        "away_return_points_total": 100,
        "home_ranking": 5,
        "away_ranking": 50,
        "ranking_as_of_wall_ns": 180,
    }
    values.update(changes)
    return HistoricalRow(**values)  # type: ignore[arg-type]


def artifact(**changes: object) -> FrozenPrematchArtifact:
    values: dict[str, object] = {
        "artifact_id": "prematch-artifact-1",
        "model_sha256": SHA_A,
        "feature_definition_sha256": PREMATCH_FEATURE_DEFINITION_SHA256,
        "training_cutoff_wall_ns": 900,
        "source_dataset_sha256": SHA_B,
        "entitlement_sha256": SHA_C,
        "manifest_sha256": SHA_D,
        "access_decision_sha256": SHA_E,
        "tour_serve_alpha": Decimal("32"),
        "tour_serve_beta": Decimal("28"),
        "surface_serve_alpha": Decimal("40"),
        "surface_serve_beta": Decimal("20"),
        "return_alpha": Decimal("42"),
        "return_beta": Decimal("58"),
        "recency_half_life_ns": 86_400_000_000_000,
        "opponent_adjustment_weight": Decimal("0.25"),
        "minimum_effective_sample_size": Decimal("25"),
        "model_build_sha256": SHA_F,
    }
    values.update(changes)
    return FrozenPrematchArtifact(**values)  # type: ignore[arg-type]


class PrematchModelTests(unittest.TestCase):
    def test_build_features_excludes_point_in_time_leakage(self) -> None:
        scheduled = 1_000
        clean = row(row_sha256=SHA_B)
        future_match = row(
            provider_match_id="match-2",
            row_sha256=SHA_C,
            match_start_wall_ns=1_000,
            observed_wall_ns=1_001,
            revised_wall_ns=1_001,
        )
        revised_after_start = row(
            provider_match_id="match-3",
            row_sha256=SHA_D,
            match_start_wall_ns=200,
            observed_wall_ns=250,
            revised_wall_ns=1_000,
        )
        future_ranking = row(
            provider_match_id="match-4",
            row_sha256=SHA_E,
            match_start_wall_ns=300,
            observed_wall_ns=350,
            revised_wall_ns=375,
            home_ranking=1,
            away_ranking=1,
            ranking_as_of_wall_ns=1_000,
        )
        features = build_features(
            (clean, future_match, revised_after_start, future_ranking),
            player_home_id="player-home",
            player_away_id="player-away",
            surface="hard",
            scheduled_start_wall_ns=scheduled,
        )
        self.assertEqual(features.eligible_row_count, 2)
        self.assertEqual(features.discarded_row_count, 2)
        self.assertEqual(features.home_ranking, 5)
        self.assertEqual(features.away_ranking, 50)
        self.assertNotEqual(features.source_rows_sha256, SHA_C)
        self.assertEqual(
            features.feature_definition_sha256,
            PREMATCH_FEATURE_DEFINITION_SHA256,
        )

    def test_estimate_prematch_shrinks_unknown_players_to_prior(self) -> None:
        frozen = artifact()
        known_features = build_features(
            (row(),),
            player_home_id="player-home",
            player_away_id="player-away",
            surface="hard",
            scheduled_start_wall_ns=1_000,
        )
        unknown_features = build_features(
            (),
            player_home_id="unknown-home",
            player_away_id="unknown-away",
            surface="hard",
            scheduled_start_wall_ns=1_000,
        )
        known = estimate_prematch(known_features, frozen)
        unknown = estimate_prematch(unknown_features, frozen)
        self.assertGreater(
            known.home_serve_point_probability,
            unknown.home_serve_point_probability,
        )
        self.assertEqual(unknown.home_serve_point_probability, Decimal("0.600000000000"))
        known_width = known.home_serve_point_upper - known.home_serve_point_lower
        unknown_width = (
            unknown.home_serve_point_upper - unknown.home_serve_point_lower
        )
        self.assertGreater(unknown_width, known_width)
        self.assertEqual(known.training_cutoff_wall_ns, 900)
        self.assertEqual(known.model_sha256, SHA_A)

    def test_estimate_rejects_feature_artifact_digest_mismatch(self) -> None:
        features = build_features(
            (row(),),
            player_home_id="player-home",
            player_away_id="player-away",
            surface="hard",
            scheduled_start_wall_ns=1_000,
        )
        with self.assertRaises(ExpertContractError):
            estimate_prematch(
                features,
                artifact(feature_definition_sha256=SHA_F),
            )


if __name__ == "__main__":
    unittest.main()
