from __future__ import annotations

from dataclasses import replace
from decimal import Context, Decimal, localcontext
import unittest

from inci_tennis_expert.contracts import MatchFormat, PlayerSide, SetScore

try:
    import inci_tennis_expert.first_set_model as first_set_model
except ModuleNotFoundError:
    first_set_model = None  # type: ignore[assignment]


BO3 = MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS
BO5 = MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS
HOME_SIX_LOVE = SetScore(6, 0, None, None)
PRIMARY_LINEAGE_SHA256 = "1" * 64
WITNESS_LINEAGE_SHA256 = "2" * 64


class FirstSetModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(
            first_set_model,
            "inci_tennis_expert.first_set_model is not implemented",
        )

    def model(self):
        assert first_set_model is not None
        return first_set_model.FirstSetBayesianModel(
            home_prior=first_set_model.BetaDistribution(
                alpha=Decimal("8"),
                beta=Decimal("2"),
            ),
            away_prior=first_set_model.BetaDistribution(
                alpha=Decimal("6"),
                beta=Decimal("4"),
            ),
            parameters=first_set_model.FirstSetParameters(
                version="first-set-service-points-v1",
                evidence_weight=Decimal("0.5"),
            ),
            match_format=BO3,
        )

    def point(self, **changes: object):
        assert first_set_model is not None
        values: dict[str, object] = {
            "point_id": "point-1",
            "sequence_number": 1,
            "set_number": 1,
            "server": PlayerSide.HOME,
            "winner": PlayerSide.HOME,
            "consensus_epoch": 7,
            "consensus_transition_sha256": f"{1:064x}",
            "supporting_source_lineage_sha256s": (
                PRIMARY_LINEAGE_SHA256,
                WITNESS_LINEAGE_SHA256,
            ),
        }
        transition_digest_was_supplied = (
            "consensus_transition_sha256" in changes
        )
        values.update(changes)
        if not transition_digest_was_supplied:
            sequence_number = values["sequence_number"]
            assert type(sequence_number) is int
            values["consensus_transition_sha256"] = (
                f"{sequence_number:064x}"
            )
        return first_set_model.FirstSetPoint(**values)

    def straight_home_set(self):
        points = []
        sequence = 0
        for game in range(6):
            server = PlayerSide.HOME if game % 2 == 0 else PlayerSide.AWAY
            for _ in range(4):
                sequence += 1
                points.append(
                    self.point(
                        point_id=f"p{sequence}",
                        sequence_number=sequence,
                        server=server,
                        winner=PlayerSide.HOME,
                    )
                )
        return tuple(points)

    def test_service_beta_updates_use_winner_relative_to_server(self) -> None:
        assert first_set_model is not None
        model = self.model()
        points = self.straight_home_set()
        for point in points:
            self.assertTrue(model.observe_point(point))

        review = model.complete_set_one(
            terminal_set=HOME_SIX_LOVE,
        )

        self.assertTrue(review.supported)
        self.assertIsNone(review.abstention_reason)
        assert review.home_posterior is not None
        assert review.away_posterior is not None
        self.assertEqual(review.home_posterior.alpha, Decimal("14"))
        self.assertEqual(review.home_posterior.beta, Decimal("2"))
        self.assertEqual(review.away_posterior.alpha, Decimal("6"))
        self.assertEqual(review.away_posterior.beta, Decimal("10"))
        self.assertEqual(review.home_support.service_points_won, 12)
        self.assertEqual(review.home_support.service_points_lost, 0)
        self.assertEqual(review.away_support.service_points_won, 0)
        self.assertEqual(review.away_support.service_points_lost, 12)
        self.assertEqual(review.consensus_epoch, 7)
        self.assertEqual(
            review.supporting_source_lineage_sha256s,
            (PRIMARY_LINEAGE_SHA256, WITNESS_LINEAGE_SHA256),
        )

    def test_duplicate_point_id_is_rejected_before_a_second_update(self) -> None:
        assert first_set_model is not None
        model = self.model()
        points = self.straight_home_set()
        model.observe_point(points[0])

        with self.assertRaises(first_set_model.FirstSetModelError):
            model.observe_point(points[0])

        for point in points[1:]:
            model.observe_point(point)

        review = model.complete_set_one(
            terminal_set=HOME_SIX_LOVE,
        )
        assert review.home_posterior is not None
        self.assertEqual(review.home_posterior.alpha, Decimal("14"))
        self.assertEqual(review.home_support.service_points, 12)

    def test_posterior_arithmetic_ignores_ambient_decimal_context(self) -> None:
        assert first_set_model is not None
        model = first_set_model.FirstSetBayesianModel(
            home_prior=first_set_model.BetaDistribution(
                alpha=Decimal("8.1234567890123456789"),
                beta=Decimal("2.9876543210987654321"),
            ),
            away_prior=first_set_model.BetaDistribution(
                alpha=Decimal("6"),
                beta=Decimal("4"),
            ),
            parameters=first_set_model.FirstSetParameters(
                version="decimal-context-independent-v1",
                evidence_weight=Decimal("0.3333333333333333333333333333"),
            ),
            match_format=BO3,
        )

        with localcontext(Context(prec=6)):
            for point in self.straight_home_set():
                model.observe_point(point)
            review = model.complete_set_one(
                terminal_set=HOME_SIX_LOVE,
            )

        assert review.home_posterior is not None
        self.assertEqual(
            review.home_posterior.alpha,
            Decimal("12.1234567890123456788999999996"),
        )
        self.assertEqual(
            review.home_posterior.beta,
            Decimal("2.9876543210987654321"),
        )

    def test_set_one_freeze_is_idempotent_and_later_points_do_not_adapt_skill(
        self,
    ) -> None:
        model = self.model()
        for point in self.straight_home_set():
            model.observe_point(point)
        first_review = model.complete_set_one(
            terminal_set=HOME_SIX_LOVE,
        )

        accepted = model.observe_point(
            self.point(
                point_id="set-two-point",
                sequence_number=2,
                set_number=2,
                server=PlayerSide.HOME,
                winner=PlayerSide.AWAY,
            )
        )
        second_review = model.complete_set_one(
            terminal_set=HOME_SIX_LOVE,
        )

        self.assertFalse(accepted)
        self.assertTrue(model.is_frozen)
        self.assertIs(second_review, first_review)
        self.assertEqual(second_review.home_posterior, first_review.home_posterior)
        self.assertEqual(second_review.away_posterior, first_review.away_posterior)

    def test_incomplete_history_abstains_without_publishing_a_posterior(self) -> None:
        assert first_set_model is not None
        model = self.model()
        model.observe_point(self.point(point_id="p1", sequence_number=1))
        model.observe_point(self.point(point_id="p3", sequence_number=3))

        review = model.complete_set_one(
            terminal_set=HOME_SIX_LOVE,
        )

        self.assertFalse(review.supported)
        self.assertEqual(
            review.abstention_reason,
            first_set_model.FirstSetAbstentionReason.INCOMPLETE_HISTORY,
        )
        self.assertIsNone(review.home_posterior)
        self.assertIsNone(review.away_posterior)
        self.assertEqual(review.home_support.service_points, 0)
        self.assertEqual(review.away_support.service_points, 0)

    def test_ambiguous_server_abstains_instead_of_inferring_from_score(self) -> None:
        assert first_set_model is not None
        model = self.model()
        model.observe_point(
            self.point(
                server=None,
                winner=PlayerSide.HOME,
            )
        )

        review = model.complete_set_one(
            terminal_set=HOME_SIX_LOVE,
        )

        self.assertFalse(review.supported)
        self.assertEqual(
            review.abstention_reason,
            first_set_model.FirstSetAbstentionReason.AMBIGUOUS_SERVER,
        )
        self.assertIsNone(review.home_posterior)
        self.assertIsNone(review.away_posterior)

    def test_missing_provenance_and_non_bo3_format_fail_closed(self) -> None:
        assert first_set_model is not None
        model = self.model()
        for point in self.straight_home_set():
            if point.sequence_number == 12:
                point = self.point(
                    point_id=point.point_id,
                    sequence_number=point.sequence_number,
                    server=point.server,
                    winner=point.winner,
                    consensus_transition_sha256=None,
                )
            model.observe_point(point)

        review = model.complete_set_one(
            terminal_set=HOME_SIX_LOVE,
        )

        self.assertFalse(review.supported)
        self.assertEqual(
            review.abstention_reason,
            first_set_model.FirstSetAbstentionReason.MISSING_PROVENANCE,
        )
        with self.assertRaises(first_set_model.FirstSetModelError):
            first_set_model.FirstSetBayesianModel(
                home_prior=first_set_model.BetaDistribution(
                    Decimal("1"), Decimal("1")
                ),
                away_prior=first_set_model.BetaDistribution(
                    Decimal("1"), Decimal("1")
                ),
                parameters=first_set_model.FirstSetParameters(
                    version="v1",
                    evidence_weight=Decimal("0.5"),
                ),
                match_format=BO5,
            )

    def test_one_point_cannot_claim_a_complete_first_set(self) -> None:
        assert first_set_model is not None
        model = self.model()
        model.observe_point(self.point())

        review = model.complete_set_one(
            terminal_set=HOME_SIX_LOVE,
        )

        self.assertFalse(review.supported)
        self.assertEqual(
            review.abstention_reason,
            first_set_model.FirstSetAbstentionReason.ILLEGAL_HISTORY,
        )

    def test_invalid_source_lineage_evidence_blocks_the_review(self) -> None:
        assert first_set_model is not None
        model = self.model()
        for point in self.straight_home_set():
            if point.sequence_number == 8:
                point = self.point(
                    point_id=point.point_id,
                    sequence_number=point.sequence_number,
                    server=point.server,
                    winner=point.winner,
                    supporting_source_lineage_sha256s=(
                        PRIMARY_LINEAGE_SHA256,
                    ),
                )
            model.observe_point(point)

        review = model.complete_set_one(terminal_set=HOME_SIX_LOVE)

        self.assertFalse(review.supported)
        self.assertEqual(
            review.abstention_reason,
            first_set_model.FirstSetAbstentionReason.INVALID_PROVENANCE,
        )

    def test_mixed_consensus_epochs_block_the_review(self) -> None:
        assert first_set_model is not None
        model = self.model()
        for point in self.straight_home_set():
            if point.sequence_number >= 13:
                point = self.point(
                    point_id=point.point_id,
                    sequence_number=point.sequence_number,
                    server=point.server,
                    winner=point.winner,
                    consensus_epoch=8,
                )
            model.observe_point(point)

        review = model.complete_set_one(terminal_set=HOME_SIX_LOVE)

        self.assertFalse(review.supported)
        self.assertEqual(
            review.abstention_reason,
            first_set_model.FirstSetAbstentionReason.MIXED_CONSENSUS_EPOCHS,
        )

    def test_replayed_consensus_transition_digest_blocks_the_review(self) -> None:
        assert first_set_model is not None
        model = self.model()
        replayed_digest = f"{11:064x}"
        for point in self.straight_home_set():
            if point.sequence_number == 12:
                point = self.point(
                    point_id=point.point_id,
                    sequence_number=point.sequence_number,
                    server=point.server,
                    winner=point.winner,
                    consensus_transition_sha256=replayed_digest,
                )
            model.observe_point(point)

        review = model.complete_set_one(terminal_set=HOME_SIX_LOVE)

        self.assertFalse(review.supported)
        self.assertEqual(
            review.abstention_reason,
            first_set_model.FirstSetAbstentionReason.REPLAYED_TRANSITION,
        )

    def test_review_binds_point_history_terminal_set_and_stable_digest(self) -> None:
        assert first_set_model is not None
        first_model = self.model()
        second_model = self.model()
        for point in self.straight_home_set():
            first_model.observe_point(point)
            second_model.observe_point(point)

        first_review = first_model.complete_set_one(terminal_set=HOME_SIX_LOVE)
        second_review = second_model.complete_set_one(
            terminal_set=HOME_SIX_LOVE
        )

        self.assertEqual(
            first_review.consensus_transition_sha256s,
            tuple(f"{sequence:064x}" for sequence in range(1, 25)),
        )
        self.assertEqual(first_review.point_history, self.straight_home_set())
        self.assertEqual(first_review.terminal_set, HOME_SIX_LOVE)
        self.assertEqual(
            first_review.point_history_sha256,
            second_review.point_history_sha256,
        )
        self.assertEqual(
            first_review.point_history_sha256,
            "9e7cc805e5f9215a657425890bdad2cfd2de16ef5d2305c1f40e7a3ae199e552",
        )
        self.assertEqual(
            first_review.terminal_set_sha256,
            second_review.terminal_set_sha256,
        )
        self.assertEqual(
            first_review.terminal_set_sha256,
            "8ff415cd861fcfbb0003cfddf415e4a6e1456cbaa003fbc7743d7bbcd76eca8c",
        )
        self.assertEqual(
            first_set_model.first_set_review_sha256(first_review),
            first_set_model.first_set_review_sha256(second_review),
        )
        self.assertEqual(
            first_set_model.first_set_review_sha256(first_review),
            "dcb8987a9663645784dbdf85eeaa6d37cc25cc5f55afc254067606340314a6d6",
        )
        self.assertEqual(len(first_review.point_history_sha256), 64)
        self.assertEqual(len(first_review.terminal_set_sha256), 64)
        self.assertEqual(
            len(first_set_model.first_set_review_sha256(first_review)),
            64,
        )

    def test_review_digest_changes_with_valid_consensus_provenance(self) -> None:
        assert first_set_model is not None
        epoch_seven = self.model()
        epoch_eight = self.model()
        for point in self.straight_home_set():
            epoch_seven.observe_point(point)
            epoch_eight.observe_point(
                self.point(
                    point_id=point.point_id,
                    sequence_number=point.sequence_number,
                    server=point.server,
                    winner=point.winner,
                    consensus_epoch=8,
                )
            )

        first_review = epoch_seven.complete_set_one(
            terminal_set=HOME_SIX_LOVE
        )
        second_review = epoch_eight.complete_set_one(
            terminal_set=HOME_SIX_LOVE
        )

        self.assertTrue(first_review.supported)
        self.assertTrue(second_review.supported)
        self.assertNotEqual(
            first_review.point_history_sha256,
            second_review.point_history_sha256,
        )
        self.assertNotEqual(
            first_set_model.first_set_review_sha256(first_review),
            first_set_model.first_set_review_sha256(second_review),
        )

    def test_review_provenance_digests_are_self_verifying(self) -> None:
        assert first_set_model is not None
        model = self.model()
        for point in self.straight_home_set():
            model.observe_point(point)
        review = model.complete_set_one(terminal_set=HOME_SIX_LOVE)

        with self.assertRaises(first_set_model.FirstSetModelError):
            replace(review, point_history_sha256="e" * 64)
        with self.assertRaises(first_set_model.FirstSetModelError):
            replace(review, terminal_set_sha256="f" * 64)


if __name__ == "__main__":
    unittest.main()
