from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import unittest

from inci_tennis_expert.consensus_l2_research import ConsensusL2ResearchFrameV1
from inci_tennis_expert.contracts import (
    BindingMarketMetadata,
    BindingMetadata,
    BindingRoute,
    ContractSide,
    MatchBinding,
    PlayerSide,
    SettlementSemantics,
    compute_membership_projection_sha256,
    compute_settlement_projection_sha256,
)
from inci_tennis_expert.pilot_contracts import (
    PilotExecutionScenario,
    compute_execution_scenario_sha256,
)
from tests.tennis_v1 import test_consensus_l2_research as consensus_fixture

from inci_tennis_expert.pilot_frame_adapter import (
    PilotFrameAdapterError,
    build_pilot_decision_frame,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _execution_scenario(*, maximum_pair_latency_ns: int = 50) -> PilotExecutionScenario:
    values = dict(
        version="pilot-execution-v1",
        decision_to_arrival_ns=10,
        maximum_pair_latency_ns=maximum_pair_latency_ns,
        flat_wait_horizon_ns=1_000,
        holding_horizon_ns=300_000_000_000,
    )
    return PilotExecutionScenario(
        artifact_sha256=compute_execution_scenario_sha256(**values), **values
    )


def _settlement() -> SettlementSemantics:
    values = dict(
        result_authority="kalshi_finalized_market_result",
        natural_completion="yes_if_named_player_final_winner",
        retirement_after_point="yes_if_named_player_final_winner",
        walkover_before_point="void",
        default_after_point="yes_if_named_player_final_winner",
        disqualification_after_point="void",
        cancellation="void",
        postponement="defer",
        abandonment="await_latest_finalized_result",
        amendment="await_latest_finalized_result",
        void_treatment="no_directional_settlement",
        raw_rules_sha256=SHA_A,
    )
    return SettlementSemantics(
        projection_sha256=compute_settlement_projection_sha256(**values), **values
    )


def _market(*, side: PlayerSide, ticker: str, market_id: str) -> BindingMarketMetadata:
    values = dict(
        series_ticker="TENNIS",
        event_ticker="TENNIS-MATCH-1",
        event_id="event-1",
        market_ticker=ticker,
        market_id=market_id,
        yes_player_side=side,
        yes_provider_player_id=("private-player-home" if side is PlayerSide.HOME else "private-player-away"),
        yes_canonical_player_id=("canonical-home" if side is PlayerSide.HOME else "canonical-away"),
        product="match_winner",
        event_catalog_sha256=SHA_A,
        membership_source_id="catalog",
        membership_source_version="v1",
        membership_captured_wall_ns=900,
        membership_evidence_sha256=SHA_B,
        market_text_sha256=SHA_C,
        settlement_rule_text_sha256=SHA_A,
        settlement=_settlement(),
    )
    return BindingMarketMetadata(
        membership_projection_sha256=compute_membership_projection_sha256(
            series_ticker=values["series_ticker"],
            event_ticker=values["event_ticker"],
            event_id=values["event_id"],
            market_ticker=values["market_ticker"],
            market_id=values["market_id"],
            product=values["product"],
            event_catalog_sha256=values["event_catalog_sha256"],
            membership_source_id=values["membership_source_id"],
            membership_source_version=values["membership_source_version"],
            membership_captured_wall_ns=values["membership_captured_wall_ns"],
            membership_evidence_sha256=values["membership_evidence_sha256"],
        ),
        **values,
    )


def _binding() -> MatchBinding:
    return MatchBinding(
        provider_match_id="primary-match",
        canonical_match_id="canonical-match-1",
        provider_source_id="primary",
        revision_domain_id="primary-revisions",
        source_lineage_sha256=SHA_A,
        provider_home_player_id="private-player-home",
        provider_away_player_id="private-player-away",
        kalshi_event_ticker="TENNIS-MATCH-1",
        home_market_ticker=consensus_fixture.TICKERS[0],
        away_market_ticker=consensus_fixture.TICKERS[1],
        match_format=consensus_fixture._tennis_state().match_format,
        scheduled_start_wall_ns=900,
        start_tolerance_ns=1,
        artifact_created_wall_ns=899,
        binding_artifact_sha256=SHA_D,
    )


def _binding_metadata() -> BindingMetadata:
    markets = (
        _market(side=PlayerSide.HOME, ticker=consensus_fixture.TICKERS[0], market_id=consensus_fixture.MARKET_IDS[0]),
        _market(side=PlayerSide.AWAY, ticker=consensus_fixture.TICKERS[1], market_id=consensus_fixture.MARKET_IDS[1]),
    )
    return BindingMetadata(
        canonical_match_id="canonical-match-1",
        canonical_home_player_id="canonical-home",
        canonical_away_player_id="canonical-away",
        tournament_id="tournament-1",
        season_id="season-1",
        draw_id="draw-1",
        round_id="round-1",
        tour_id="tour-1",
        tier_id="tier-1",
        surface="hard",
        provider_snapshot_sha256=SHA_B,
        kalshi_event_sha256=SHA_C,
        markets=markets,
        authorized_routes=(
            BindingRoute(PlayerSide.HOME, markets[0].market_ticker, ContractSide.YES),
            BindingRoute(PlayerSide.AWAY, markets[1].market_ticker, ContractSide.YES),
        ),
    )


def _valid_args() -> dict[str, object]:
    api = consensus_fixture._research_api()
    accepted = consensus_fixture._transition(api)
    prior = replace(
        accepted.accepted_state,
        points_home=accepted.accepted_state.points_home.__class__.LOVE,
        revision=6,
        last_provider_event_id="primary-event-6",
        last_event_semantic_sha256=SHA_D,
        last_received_monotonic_ns=90,
    )
    observation = consensus_fixture._book_observation(api, captured_monotonic_ns=130)
    frame = ConsensusL2ResearchFrameV1(accepted, observation)
    return dict(
        prior_state=prior,
        frame=frame,
        binding=_binding(),
        metadata=_binding_metadata(),
        expected_consensus_epoch=3,
        execution_scenario=_execution_scenario(),
    )


class PilotFrameAdapterTests(unittest.TestCase):
    def test_projects_exact_atomic_point_and_direct_successor_book(self) -> None:
        actual = build_pilot_decision_frame(**_valid_args())
        self.assertEqual(actual.point_event.winner, PlayerSide.HOME)
        self.assertEqual(actual.home_book.player_side, PlayerSide.HOME)
        self.assertEqual(actual.home_book.source_frame_id, actual.source_frame_id)

    def test_swapped_market_binding_is_rejected(self) -> None:
        values = _valid_args()
        binding = values.pop("binding")
        assert isinstance(binding, MatchBinding)
        with self.assertRaisesRegex(PilotFrameAdapterError, "^market_orientation$"):
            build_pilot_decision_frame(
                binding=replace(
                    binding,
                    home_market_ticker=binding.away_market_ticker,
                    away_market_ticker=binding.home_market_ticker,
                ),
                **values,
            )

    def test_stale_pair_is_rejected_even_when_frame_contract_is_valid(self) -> None:
        values = _valid_args()
        values["execution_scenario"] = _execution_scenario(maximum_pair_latency_ns=9)
        with self.assertRaisesRegex(PilotFrameAdapterError, "^pair_stale$"):
            build_pilot_decision_frame(**values)
