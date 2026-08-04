from __future__ import annotations

from decimal import Decimal
import unittest

from inci_tennis_expert.calibration import (
    CalibrationPolicy,
    PredictionOutcome,
    apply_calibration,
    calibrate_chronologically,
)
from inci_tennis_expert.contracts import (
    DecisionReason,
    FairValueEstimate,
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ScoreValue,
    TennisState,
    TerminationKind,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def raw_estimate(**changes: object) -> FairValueEstimate:
    values: dict[str, object] = {
        "player_side": PlayerSide.HOME,
        "fair_probability": Decimal("0.50"),
        "lower_probability": Decimal("0.45"),
        "upper_probability": Decimal("0.55"),
        "supported": True,
        "stratum": "bo3-hard-live",
        "model_sha256": SHA_A,
        "prematch_artifact_sha256": SHA_B,
        "feature_definition_sha256": SHA_C,
        "feature_vector_sha256": SHA_D,
        "calibration_artifact_sha256": None,
        "abstention_reason": None,
    }
    values.update(changes)
    return FairValueEstimate(**values)  # type: ignore[arg-type]


def policy(**changes: object) -> CalibrationPolicy:
    values: dict[str, object] = {
        "policy_id": "calibration-policy-1",
        "stratum": "bo3-hard-live",
        "training_cutoff_wall_ns": 1_000,
        "minimum_samples": 3,
        "raw_model_sha256": SHA_A,
        "prematch_artifact_sha256": SHA_B,
        "feature_definition_sha256": SHA_C,
        "maximum_abs_adjustment": Decimal("0.10"),
        "uncertainty_widening": Decimal("0.02"),
    }
    values.update(changes)
    return CalibrationPolicy(**values)  # type: ignore[arg-type]


def outcome(
    index: int,
    winning_side: PlayerSide,
    **changes: object,
) -> PredictionOutcome:
    values: dict[str, object] = {
        "prediction_id": f"prediction-{index}",
        "prediction_wall_ns": 100 + index,
        "outcome_wall_ns": 500 + index,
        "raw_estimate": raw_estimate(),
        "winning_side": winning_side,
    }
    values.update(changes)
    return PredictionOutcome(**values)  # type: ignore[arg-type]


def state(**changes: object) -> TennisState:
    values: dict[str, object] = {
        "provider_source_id": "provider-a",
        "revision_domain_id": "revision-a",
        "source_lineage_sha256": SHA_A,
        "provider_match_id": "match-1",
        "home_player_id": "player-home",
        "away_player_id": "player-away",
        "scheduled_start_wall_ns": 100,
        "match_format": MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        "status": MatchStatus.LIVE,
        "termination_kind": TerminationKind.NONE,
        "winner": None,
        "retired_side": None,
        "completed_sets": (),
        "games_home": 0,
        "games_away": 0,
        "points_home": ScoreValue.LOVE,
        "points_away": ScoreValue.LOVE,
        "in_tiebreak": False,
        "tiebreak_points_home": 0,
        "tiebreak_points_away": 0,
        "tiebreak_first_server": None,
        "server_for_next_point": PlayerSide.HOME,
        "correction_epoch": 0,
        "revision": 1,
        "snapshot_complete": True,
        "last_provider_event_id": "event-1",
        "last_event_semantic_sha256": SHA_B,
        "correction_lineage_sha256": SHA_C,
        "last_source_wall_ns": 1_500,
        "last_source_generated_wall_ns": 1_499,
        "last_received_monotonic_ns": 90,
        "last_clock_uncertainty_ns": 2,
        "block_reason": None,
        "expected_revision": None,
        "observed_revision": None,
        "blocked_event_semantic_sha256": None,
        "blocked_received_monotonic_ns": None,
    }
    values.update(changes)
    return TennisState(**values)  # type: ignore[arg-type]


class CalibrationTests(unittest.TestCase):
    def test_calibration_uses_chronological_pre_cutoff_outcomes_only(self) -> None:
        with self.assertRaises(Exception):
            calibrate_chronologically(
                (
                    outcome(2, PlayerSide.HOME),
                    outcome(1, PlayerSide.HOME),
                ),
                policy(),
            )
        with self.assertRaises(Exception):
            calibrate_chronologically(
                (
                    outcome(1, PlayerSide.HOME),
                    outcome(2, PlayerSide.HOME, outcome_wall_ns=1_001),
                ),
                policy(),
            )

    def test_successful_calibration_binds_artifact_and_widens_interval(self) -> None:
        artifact = calibrate_chronologically(
            (
                outcome(1, PlayerSide.HOME),
                outcome(2, PlayerSide.HOME),
                outcome(3, PlayerSide.AWAY),
            ),
            policy(),
        )
        self.assertTrue(artifact.supported)
        self.assertEqual(artifact.sample_count, 3)
        self.assertEqual(artifact.intercept_adjustment, Decimal("0.100000000000"))
        calibrated = apply_calibration(raw_estimate(), artifact, state=state())
        self.assertTrue(calibrated.supported)
        self.assertEqual(
            calibrated.calibration_artifact_sha256,
            artifact.calibration_artifact_sha256,
        )
        self.assertEqual(calibrated.fair_probability, Decimal("0.600000000000"))
        self.assertEqual(calibrated.lower_probability, Decimal("0.530000000000"))
        self.assertEqual(calibrated.upper_probability, Decimal("0.670000000000"))

    def test_digest_mismatch_abstains(self) -> None:
        artifact = calibrate_chronologically(
            (
                outcome(1, PlayerSide.HOME),
                outcome(2, PlayerSide.HOME),
                outcome(3, PlayerSide.AWAY),
            ),
            policy(),
        )
        mismatched = apply_calibration(
            raw_estimate(prematch_artifact_sha256=SHA_D),
            artifact,
            state=state(),
        )
        self.assertFalse(mismatched.supported)
        self.assertEqual(
            mismatched.abstention_reason,
            DecisionReason.MODEL_OUT_OF_DISTRIBUTION,
        )
        self.assertIsNone(mismatched.calibration_artifact_sha256)

    def test_missing_support_abstains(self) -> None:
        unsupported_artifact = calibrate_chronologically(
            (
                outcome(1, PlayerSide.HOME),
                outcome(2, PlayerSide.AWAY),
            ),
            policy(minimum_samples=3),
        )
        self.assertFalse(unsupported_artifact.supported)
        estimate = apply_calibration(
            raw_estimate(),
            unsupported_artifact,
            state=state(),
        )
        self.assertFalse(estimate.supported)
        self.assertEqual(estimate.abstention_reason, DecisionReason.MODEL_UNCERTAIN)


if __name__ == "__main__":
    unittest.main()
