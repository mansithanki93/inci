from __future__ import annotations

from dataclasses import replace
import importlib
import unittest

from inci_tennis_expert.contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ProviderPoint,
    ScoreValue,
    TennisState,
    TerminationKind,
)
from inci_tennis_expert.tennis_score import apply_point


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
FRESHNESS_NS = 5_000_000_000


def _api() -> object:
    try:
        contracts = importlib.import_module("inci_tennis_expert.live_paper_contracts")
        score = importlib.import_module("inci_tennis_expert.live_paper_score")
    except ModuleNotFoundError as error:
        raise AssertionError("live paper score API is missing") from error
    required = (
        "LivePaperSourceObservation",
        "LivePaperScoreDecisionKind",
        "PaperScoreTrust",
        "initial_live_paper_score_coordinator_state",
        "reduce_live_paper_scores",
    )
    missing = tuple(
        name for name in required if not hasattr(contracts, name) and not hasattr(score, name)
    )
    if missing:
        raise AssertionError(f"live paper score API is missing {missing!r}")
    return contracts, score


def _state(**changes: object) -> TennisState:
    values: dict[str, object] = {
        "provider_source_id": "provider-a",
        "revision_domain_id": "provider-a-local",
        "source_lineage_sha256": SHA_A,
        "provider_match_id": "provider-a-match",
        "home_player_id": "home",
        "away_player_id": "away",
        "scheduled_start_wall_ns": 10,
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
        "last_provider_event_id": "capture-a",
        "last_event_semantic_sha256": SHA_B,
        "correction_lineage_sha256": SHA_C,
        "last_source_wall_ns": 100,
        "last_source_generated_wall_ns": 100,
        "last_received_monotonic_ns": 100,
        "last_clock_uncertainty_ns": 0,
        "block_reason": None,
        "expected_revision": None,
        "observed_revision": None,
        "blocked_event_semantic_sha256": None,
        "blocked_received_monotonic_ns": None,
    }
    values.update(changes)
    return TennisState(**values)  # type: ignore[arg-type]


def _after_one_point(before: TennisState, winner: PlayerSide) -> TennisState:
    return apply_point(
        before,
        ProviderPoint(
            provider_source_id=before.provider_source_id,
            revision_domain_id=before.revision_domain_id,
            source_lineage_sha256=before.source_lineage_sha256,
            provider_event_id="capture-next",
            provider_match_id=before.provider_match_id,
            home_player_id=before.home_player_id,
            away_player_id=before.away_player_id,
            scheduled_start_wall_ns=before.scheduled_start_wall_ns,
            match_format=before.match_format,
            correction_epoch=before.correction_epoch,
            revision=before.revision + 1,
            point_winner=winner,
            server_before_point=before.server_for_next_point,
            source_wall_ns=before.last_source_wall_ns + 1,
            source_generated_wall_ns=before.last_source_generated_wall_ns + 1,
            received_monotonic_ns=before.last_received_monotonic_ns + 1,
            clock_uncertainty_ns=0,
        ),
    ).state


def _observation(
    api: object,
    state: TennisState,
    *,
    source_id: str = "source-a",
    lineage_id: str = "lineage-a",
    lineage_sha256: str = SHA_A,
    independent: bool | None = True,
    captured_monotonic_ns: int = 1_000,
    canonical_match_id: str = "match-1",
    raw_receipt_sha256: str = SHA_D,
    independence_proof_sha256: str | None = SHA_B,
) -> object:
    contracts, _ = api
    return contracts.LivePaperSourceObservation(
        canonical_match_id=canonical_match_id,
        provider_slot="fixture",
        source_id=source_id,
        independent_lineage_id=lineage_id,
        lineage_sha256=lineage_sha256,
        independence_proven=independent,
        state=state,
        raw_receipt_sha256=raw_receipt_sha256,
        captured_wall_ns=captured_monotonic_ns,
        captured_monotonic_ns=captured_monotonic_ns,
        independence_proof_sha256=(
            independence_proof_sha256 if independent is True else None
        ),
    )


def _reduce(api: object, state: object, observations: tuple[object, ...], now: int = 1_000) -> tuple[object, object]:
    _, score = api
    return score.reduce_live_paper_scores(
        state, observations, now_wall_ns=now, now_monotonic_ns=now
    )


class LivePaperScoreCoordinatorTests(unittest.TestCase):
    def test_one_fresh_source_anchors_as_single_source_paper(self) -> None:
        api = _api()
        contracts, score = api
        before = _state()

        next_state, decision = _reduce(
            api,
            score.initial_live_paper_score_coordinator_state("match-1"),
            (_observation(api, before),),
        )

        self.assertIs(decision.kind, contracts.LivePaperScoreDecisionKind.ANCHORED)
        self.assertIs(decision.anchor.trust, contracts.PaperScoreTrust.SINGLE_SOURCE_PAPER)
        self.assertEqual(next_state.local_point_ordinal, 0)

    def test_two_proven_independent_lineages_upgrade_anchor_to_consensus(self) -> None:
        api = _api()
        contracts, score = api
        before = _state()
        twin = replace(
            before,
            provider_source_id="provider-b",
            revision_domain_id="provider-b-local",
            source_lineage_sha256=SHA_B,
            provider_match_id="provider-b-match",
        )

        _, decision = _reduce(
            api,
            score.initial_live_paper_score_coordinator_state("match-1"),
            (
                _observation(api, before),
                _observation(api, twin, source_id="source-b", lineage_id="lineage-b", lineage_sha256=SHA_B, raw_receipt_sha256=SHA_C, independence_proof_sha256=SHA_D),
            ),
        )

        self.assertIs(decision.anchor.trust, contracts.PaperScoreTrust.CONSENSUS_PAPER)
        self.assertEqual(decision.anchor.supporting_lineage_sha256s, (SHA_A, SHA_B))
        self.assertEqual(
            tuple(
                (support.raw_receipt_sha256, support.independence_proof_sha256)
                for support in decision.anchor.supporting_sources
            ),
            ((SHA_C, SHA_D), (SHA_D, SHA_B)),
        )

    def test_mirrored_endpoints_on_one_lineage_remain_single_source_paper(self) -> None:
        api = _api()
        contracts, score = api
        before = _state()
        mirror = replace(before, provider_source_id="mirror", revision_domain_id="mirror-local")

        _, decision = _reduce(
            api,
            score.initial_live_paper_score_coordinator_state("match-1"),
            (
                _observation(api, before),
                _observation(api, mirror, source_id="mirror", lineage_id="lineage-a"),
            ),
        )

        self.assertIs(decision.anchor.trust, contracts.PaperScoreTrust.SINGLE_SOURCE_PAPER)
        self.assertEqual(decision.anchor.supporting_lineage_sha256s, (SHA_A,))

    def test_multiple_unproven_lineages_abstain_instead_of_downgrading_to_single_source(self) -> None:
        api = _api()
        contracts, score = api
        before = _state()
        other = replace(before, provider_source_id="provider-b", source_lineage_sha256=SHA_B)

        _, decision = _reduce(
            api,
            score.initial_live_paper_score_coordinator_state("match-1"),
            (
                _observation(api, before, independent=None),
                _observation(api, other, source_id="source-b", lineage_id="lineage-b", lineage_sha256=SHA_B, independent=None),
            ),
        )

        self.assertIs(decision.kind, contracts.LivePaperScoreDecisionKind.ABSTAINED)

    def test_fresh_score_disagreement_abstains(self) -> None:
        api = _api()
        contracts, score = api
        before = _state()
        disagreeing = _after_one_point(before, PlayerSide.HOME)

        next_state, decision = _reduce(
            api,
            score.initial_live_paper_score_coordinator_state("match-1"),
            (
                _observation(api, before),
                _observation(api, disagreeing, source_id="source-b", lineage_id="lineage-b", lineage_sha256=SHA_B),
            ),
        )

        self.assertIs(decision.kind, contracts.LivePaperScoreDecisionKind.ABSTAINED)
        self.assertIsNone(next_state.anchor)

    def test_stale_or_missing_sources_abstain(self) -> None:
        api = _api()
        contracts, score = api
        initial = score.initial_live_paper_score_coordinator_state("match-1")

        _, missing = _reduce(api, initial, ())
        _, stale = _reduce(
            api,
            initial,
            (_observation(api, _state(), captured_monotonic_ns=1_000),),
            now=1_000 + FRESHNESS_NS + 1,
        )

        self.assertIs(missing.kind, contracts.LivePaperScoreDecisionKind.ABSTAINED)
        self.assertIs(stale.kind, contracts.LivePaperScoreDecisionKind.ABSTAINED)

    def test_unchanged_or_duplicate_capture_does_not_update_point_ordinal(self) -> None:
        api = _api()
        contracts, score = api
        before = _state()
        initial, _ = _reduce(api, score.initial_live_paper_score_coordinator_state("match-1"), (_observation(api, before),))

        next_state, unchanged = _reduce(api, initial, (_observation(api, before, captured_monotonic_ns=1_001),), now=1_001)
        duplicate_state, duplicate = _reduce(api, next_state, (_observation(api, before, captured_monotonic_ns=1_001),), now=1_001)

        self.assertIs(unchanged.kind, contracts.LivePaperScoreDecisionKind.UNCHANGED)
        self.assertIs(duplicate.kind, contracts.LivePaperScoreDecisionKind.UNCHANGED)
        self.assertIs(next_state, initial)
        self.assertIs(duplicate_state, initial)
        self.assertEqual(duplicate_state.local_point_ordinal, 0)

    def test_rejects_observation_bound_to_other_canonical_match(self) -> None:
        api = _api()
        _, score = api

        with self.assertRaisesRegex(ValueError, "^canonical_match_id$"):
            _reduce(
                api,
                score.initial_live_paper_score_coordinator_state("match-1"),
                (_observation(api, _state(), canonical_match_id="match-2"),),
            )

    def test_facts_projection_binds_canonical_match_and_uses_paper_local_revision_identity(self) -> None:
        from inci_tennis_adapters.live_score_candidates import (
            LiveScoreCaptureContext,
            LiveScoreFacts,
            NormalizedLiveScore,
            ProviderSlot,
        )

        api = _api()
        _, score = api
        context = LiveScoreCaptureContext(
            provider_source_id="provider-a",
            revision_domain_id="provider-revisions",
            source_lineage_sha256=SHA_A,
            provider_match_id="provider-a-match",
            home_player_id="home",
            away_player_id="away",
            scheduled_start_wall_ns=10,
            match_format=MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
            local_capture_wall_ns=1_000,
            local_capture_monotonic_ns=1_000,
            local_clock_uncertainty_ns=0,
            raw_capture_id="capture-a",
            lineage_independence_proven=True,
        )
        facts = LiveScoreFacts(
            "provider-a-match", "home", "away", MatchStatus.LIVE,
            TerminationKind.NONE, None, (), 0, 0, ScoreValue.LOVE,
            ScoreValue.LOVE, False, PlayerSide.HOME, None, (),
        )
        normalized = NormalizedLiveScore(
            ProviderSlot.API_TENNIS, "provider-a", SHA_A, "capture-a",
            SHA_D, "fixture", facts, None, None, (), True,
        )

        with self.assertRaisesRegex(ValueError, "^independence_proof_sha256$"):
            score.observation_from_live_score_facts(
                canonical_match_id="match-1", context=context,
                normalized=normalized, local_revision=7,
                independence_proof_sha256=None,
            )
        unproven_context = replace(context, lineage_independence_proven=False)
        unproven_normalized = replace(
            normalized, lineage_independence_proven=False,
        )
        with self.assertRaisesRegex(ValueError, "^independence_proof_sha256$"):
            score.observation_from_live_score_facts(
                canonical_match_id="match-1", context=unproven_context,
                normalized=unproven_normalized, local_revision=7,
                independence_proof_sha256=SHA_C,
            )

        observation = score.observation_from_live_score_facts(
            canonical_match_id="match-1", context=context, normalized=normalized,
            local_revision=7, independence_proof_sha256=SHA_C,
        )

        self.assertEqual(observation.canonical_match_id, "match-1")
        self.assertEqual(observation.state.revision_domain_id, "paper-local-revisions-v1")
        self.assertEqual(observation.state.revision, 7)
        self.assertEqual(observation.state.last_provider_event_id, "paper-local-capture-a")

    def test_consensus_contract_requires_auditable_proven_support_mapping(self) -> None:
        api = _api()
        contracts, _ = api
        support_a = contracts.LivePaperSupport(SHA_A, SHA_B, "lineage-a", False, None)
        support_b = contracts.LivePaperSupport(SHA_C, SHA_D, "lineage-b", True, SHA_A)

        with self.assertRaisesRegex(contracts.LivePaperContractError, "^consensus_support$"):
            contracts.make_live_paper_anchor(
                canonical_match_id="match-1",
                state=_state(),
                trust=contracts.PaperScoreTrust.CONSENSUS_PAPER,
                supporting_lineage_sha256s=(SHA_B, SHA_D),
                parent_receipt_sha256s=(SHA_A, SHA_C),
                consensus_epoch=1,
                correction_epoch=0,
                rebase_epoch=0,
                accepted_wall_ns=1_000,
                accepted_monotonic_ns=1_000,
                supporting_independent_lineage_ids=("lineage-a", "lineage-b"),
                supporting_sources=(support_a, support_b),
            )

    def test_proven_lineage_requires_proof_and_unproven_lineage_rejects_one(self) -> None:
        api = _api()
        contracts, _ = api
        values = dict(
            canonical_match_id="match-1",
            provider_slot="fixture",
            source_id="source-a",
            independent_lineage_id="lineage-a",
            lineage_sha256=SHA_A,
            state=_state(),
            raw_receipt_sha256=SHA_D,
            captured_wall_ns=1_000,
            captured_monotonic_ns=1_000,
        )

        with self.assertRaisesRegex(contracts.LivePaperContractError, "^independence_proof_sha256$"):
            contracts.LivePaperSourceObservation(
                independence_proven=True, independence_proof_sha256=None, **values
            )
        with self.assertRaisesRegex(contracts.LivePaperContractError, "^independence_proof_sha256$"):
            contracts.LivePaperSourceObservation(
                independence_proven=False, independence_proof_sha256=SHA_B, **values
            )

    def test_exact_successor_emits_exact_winner_server_and_one_ordinal(self) -> None:
        api = _api()
        contracts, score = api
        before = _state()
        initial, _ = _reduce(api, score.initial_live_paper_score_coordinator_state("match-1"), (_observation(api, before),))
        after = _after_one_point(before, PlayerSide.HOME)

        next_state, decision = _reduce(api, initial, (_observation(api, after, captured_monotonic_ns=1_001),), now=1_001)

        self.assertIs(decision.kind, contracts.LivePaperScoreDecisionKind.POINT_ACCEPTED)
        self.assertEqual(decision.transition.local_point_ordinal, 1)
        self.assertIs(decision.transition.server, PlayerSide.HOME)
        self.assertIs(decision.transition.winner, PlayerSide.HOME)
        self.assertEqual(next_state.local_point_ordinal, 1)

    def test_multi_point_gap_quarantines_then_stable_reanchor_increments_epoch(self) -> None:
        api = _api()
        contracts, score = api
        before = _state()
        initial, _ = _reduce(api, score.initial_live_paper_score_coordinator_state("match-1"), (_observation(api, before),))
        gap = _after_one_point(_after_one_point(before, PlayerSide.HOME), PlayerSide.AWAY)

        quarantined_state, quarantined = _reduce(api, initial, (_observation(api, gap, captured_monotonic_ns=2_000),), now=2_000)
        pending_state, pending = _reduce(api, quarantined_state, (_observation(api, gap, captured_monotonic_ns=2_001),), now=2_001)
        rebased_state, rebased = _reduce(api, pending_state, (_observation(api, gap, captured_monotonic_ns=2_250_000_001),), now=2_250_000_001)

        self.assertIs(quarantined.kind, contracts.LivePaperScoreDecisionKind.QUARANTINED)
        self.assertIs(pending.kind, contracts.LivePaperScoreDecisionKind.ABSTAINED)
        self.assertIs(rebased.kind, contracts.LivePaperScoreDecisionKind.REBASED)
        self.assertEqual(rebased_state.rebase_epoch, 1)
        self.assertEqual(rebased_state.local_point_ordinal, 0)


if __name__ == "__main__":
    unittest.main()
