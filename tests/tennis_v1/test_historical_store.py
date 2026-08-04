from __future__ import annotations

import unittest

from inci_tennis_expert.contracts import ExpertContractError
from inci_tennis_expert.prematch_model import HistoricalRow
from inci_tennis_io.historical_store import (
    HistoricalDatasetManifest,
    HistoricalEntitlementArtifact,
    authorize_historical_dataset,
    freeze_historical_rows,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def entitlement(**changes: object) -> HistoricalEntitlementArtifact:
    values: dict[str, object] = {
        "entitlement_id": "hist-entitlement-1",
        "provider_id": "provider-a",
        "product_id": "prematch-history",
        "source_lineage_sha256": SHA_A,
        "authorized_dataset_sha256": SHA_B,
        "issued_wall_ns": 1,
        "not_before_wall_ns": 10,
        "not_after_wall_ns": 1_000,
        "retention_delete_after_wall_ns": 1_100,
        "publication_not_before_wall_ns": 1_050,
        "active": True,
        "analysis_use_granted": True,
        "derivative_use_granted": True,
        "artifact_sha256": SHA_C,
    }
    values.update(changes)
    return HistoricalEntitlementArtifact(**values)  # type: ignore[arg-type]


def manifest(**changes: object) -> HistoricalDatasetManifest:
    values: dict[str, object] = {
        "dataset_id": "hist-dataset-1",
        "provider_id": "provider-a",
        "product_id": "prematch-history",
        "source_lineage_sha256": SHA_A,
        "declared_dataset_sha256": SHA_B,
        "observed_dataset_sha256": SHA_B,
        "row_count": 2,
        "min_match_start_wall_ns": 20,
        "max_match_start_wall_ns": 900,
        "frozen_at_wall_ns": 900,
        "manifest_sha256": SHA_C,
    }
    values.update(changes)
    return HistoricalDatasetManifest(**values)  # type: ignore[arg-type]


def row(**changes: object) -> HistoricalRow:
    values: dict[str, object] = {
        "provider_match_id": "match-1",
        "home_player_id": "player-home",
        "away_player_id": "player-away",
        "surface": "hard",
        "match_start_wall_ns": 100,
        "observed_wall_ns": 200,
        "revised_wall_ns": 250,
        "source_lineage_sha256": SHA_A,
        "row_sha256": SHA_B,
        "winner_side": None,
        "home_serve_points_won": 65,
        "home_serve_points_total": 100,
        "away_serve_points_won": 55,
        "away_serve_points_total": 100,
        "home_return_points_won": 45,
        "home_return_points_total": 100,
        "away_return_points_won": 35,
        "away_return_points_total": 100,
    }
    values.update(changes)
    return HistoricalRow(**values)  # type: ignore[arg-type]


class HistoricalStoreTests(unittest.TestCase):
    def test_authorizes_only_exact_active_entitlement(self) -> None:
        decision = authorize_historical_dataset(
            entitlement(),
            manifest(),
            official_window_start_wall_ns=100,
            official_window_end_wall_ns=900,
        )
        self.assertTrue(decision.authorized)
        self.assertEqual(decision.reason, "authorized")
        self.assertEqual(decision.dataset_sha256, SHA_B)

    def test_fail_closed_denial_modes(self) -> None:
        cases = (
            (
                entitlement(active=False),
                manifest(),
                "entitlement_inactive",
            ),
            (
                entitlement(analysis_use_granted=False),
                manifest(),
                "use_not_granted",
            ),
            (
                entitlement(derivative_use_granted=False),
                manifest(),
                "use_not_granted",
            ),
            (
                entitlement(provider_id="provider-b"),
                manifest(),
                "provider_mismatch",
            ),
            (
                entitlement(product_id="other-product"),
                manifest(),
                "product_mismatch",
            ),
            (
                entitlement(source_lineage_sha256=SHA_C),
                manifest(),
                "lineage_mismatch",
            ),
            (
                entitlement(),
                manifest(observed_dataset_sha256=SHA_C),
                "dataset_digest_drift",
            ),
            (
                entitlement(authorized_dataset_sha256=SHA_C),
                manifest(),
                "dataset_not_entitled",
            ),
            (
                entitlement(not_after_wall_ns=500),
                manifest(),
                "official_window_unauthorized",
            ),
            (
                entitlement(
                    publication_not_before_wall_ns=1_200,
                    retention_delete_after_wall_ns=1_100,
                ),
                manifest(),
                "retention_publication_conflict",
            ),
        )
        for ent, man, reason in cases:
            with self.subTest(reason=reason):
                decision = authorize_historical_dataset(
                    ent,
                    man,
                    official_window_start_wall_ns=100,
                    official_window_end_wall_ns=900,
                )
                self.assertFalse(decision.authorized)
                self.assertEqual(decision.reason, reason)

    def test_hashes_without_eligible_entitlement_remain_unusable(self) -> None:
        decision = authorize_historical_dataset(
            entitlement(active=False),
            manifest(),
            official_window_start_wall_ns=100,
            official_window_end_wall_ns=900,
        )
        with self.assertRaises(ExpertContractError):
            freeze_historical_rows((row(),), decision)

    def test_freeze_rows_rejects_lineage_and_window_escape(self) -> None:
        decision = authorize_historical_dataset(
            entitlement(),
            manifest(),
            official_window_start_wall_ns=100,
            official_window_end_wall_ns=900,
        )
        self.assertEqual(freeze_historical_rows((row(),), decision), (row(),))
        with self.assertRaises(ExpertContractError):
            freeze_historical_rows(
                (row(source_lineage_sha256=SHA_C),),
                decision,
            )
        with self.assertRaises(ExpertContractError):
            freeze_historical_rows(
                (row(match_start_wall_ns=901),),
                decision,
            )


if __name__ == "__main__":
    unittest.main()
