"""Fail-closed projection from a consensus score/L2 barrier to pilot input."""

from __future__ import annotations

from decimal import Decimal

from inci_tennis_expert.consensus_l2_research import (
    ConsensusL2ResearchFrameV1,
)
from inci_tennis_expert.contracts import (
    BindingMetadata,
    ContractSide,
    MatchBinding,
    PlayerSide,
    TennisState,
    expert_contract_sha256,
)
from inci_tennis_expert.pilot_contracts import (
    PilotBookSnapshot,
    PilotContractError,
    PilotDecisionFrame,
    PilotExecutionScenario,
    PilotPointEvent,
    PilotPriceLevel,
    compute_pilot_book_snapshot_sha256,
    pilot_contract_sha256,
)


__all__ = ("PilotFrameAdapterError", "build_pilot_decision_frame")


class PilotFrameAdapterError(ValueError):
    """Typed rejection of an unprojectable causal decision frame."""


def _fail(code: str) -> None:
    raise PilotFrameAdapterError(code)


def _validate_parent_types(
    prior_state: TennisState,
    frame: ConsensusL2ResearchFrameV1,
    binding: MatchBinding,
    metadata: BindingMetadata,
    expected_consensus_epoch: int,
    execution_scenario: PilotExecutionScenario,
) -> None:
    if type(prior_state) is not TennisState:
        _fail("prior_state")
    if type(frame) is not ConsensusL2ResearchFrameV1:
        _fail("frame_parent")
    if type(binding) is not MatchBinding or type(metadata) is not BindingMetadata:
        _fail("binding_parent")
    if type(expected_consensus_epoch) is not int or expected_consensus_epoch < 0:
        _fail("consensus_epoch")
    if type(execution_scenario) is not PilotExecutionScenario:
        _fail("execution_scenario")


def _validate_binding(
    *,
    prior_state: TennisState,
    frame: ConsensusL2ResearchFrameV1,
    binding: MatchBinding,
    metadata: BindingMetadata,
) -> None:
    transition = frame.consensus_transition
    state = transition.accepted_state
    if (
        frame.l2_observation.canonical_match_id != binding.canonical_match_id
        or transition.canonical_match_id != binding.canonical_match_id
        or metadata.canonical_match_id != binding.canonical_match_id
        or prior_state.provider_match_id != binding.provider_match_id
        or state.provider_match_id != binding.provider_match_id
        or prior_state.provider_source_id != binding.provider_source_id
        or state.provider_source_id != binding.provider_source_id
        or prior_state.revision_domain_id != binding.revision_domain_id
        or state.revision_domain_id != binding.revision_domain_id
        or prior_state.source_lineage_sha256 != binding.source_lineage_sha256
        or state.source_lineage_sha256 != binding.source_lineage_sha256
        or prior_state.home_player_id != binding.provider_home_player_id
        or state.home_player_id != binding.provider_home_player_id
        or prior_state.away_player_id != binding.provider_away_player_id
        or state.away_player_id != binding.provider_away_player_id
        or prior_state.scheduled_start_wall_ns != binding.scheduled_start_wall_ns
        or state.scheduled_start_wall_ns != binding.scheduled_start_wall_ns
        or prior_state.match_format != binding.match_format
        or state.match_format != binding.match_format
    ):
        _fail("binding_mismatch")
    home_metadata, away_metadata = metadata.markets
    if (
        transition.market_tickers != (
            binding.home_market_ticker,
            binding.away_market_ticker,
        )
        or transition.market_tickers != (
            home_metadata.market_ticker,
            away_metadata.market_ticker,
        )
        or transition.market_ids != (
            home_metadata.market_id,
            away_metadata.market_id,
        )
        or home_metadata.yes_player_side is not PlayerSide.HOME
        or away_metadata.yes_player_side is not PlayerSide.AWAY
        or home_metadata.yes_provider_player_id != binding.provider_home_player_id
        or away_metadata.yes_provider_player_id != binding.provider_away_player_id
        or home_metadata.yes_canonical_player_id != metadata.canonical_home_player_id
        or away_metadata.yes_canonical_player_id != metadata.canonical_away_player_id
    ):
        _fail("market_orientation")


def _project_book(
    *,
    frame: ConsensusL2ResearchFrameV1,
    binding: MatchBinding,
    metadata: BindingMetadata,
    index: int,
) -> PilotBookSnapshot:
    observation = frame.l2_observation
    transition = frame.consensus_transition
    source = observation.markets[index]
    market_metadata = metadata.markets[index]
    side = PlayerSide.HOME if index == 0 else PlayerSide.AWAY
    bids = tuple(
        PilotPriceLevel(level.price, level.quantity)
        for level in sorted(source.yes_levels, key=lambda item: item.price, reverse=True)
    )
    asks = tuple(
        PilotPriceLevel(Decimal("1") - level.price, level.quantity)
        for level in sorted(source.no_levels, key=lambda item: item.price, reverse=True)
    )
    values: dict[str, object] = {
        "canonical_match_id": binding.canonical_match_id,
        "player_side": side,
        "market_ticker": market_metadata.market_ticker,
        "market_id": market_metadata.market_id,
        "contract_side": ContractSide.YES,
        "bid_levels": bids,
        "ask_levels": asks,
        "captured_wall_ns": observation.captured_wall_ns,
        "captured_monotonic_ns": observation.captured_monotonic_ns,
        "source_frame_id": frame.frame_id,
        "source_l2_observation_sha256": observation.observation_sha256,
        "physical_connection_generation": observation.physical_connection_generation,
        "subscription_id": observation.subscription_id,
        "global_sequence": observation.global_sequence,
        "consensus_epoch": transition.consensus_epoch,
        "correction_epoch": transition.correction_epoch,
        "accepted_score_sha256": transition.accepted_score_sha256,
        "match_binding_sha256": expert_contract_sha256(binding),
        "binding_metadata_sha256": expert_contract_sha256(metadata),
        "trusted": True,
        "stale": False,
    }
    return PilotBookSnapshot(
        book_sha256=compute_pilot_book_snapshot_sha256(**values),
        **values,  # type: ignore[arg-type]
    )


def build_pilot_decision_frame(
    *,
    prior_state: TennisState,
    frame: ConsensusL2ResearchFrameV1,
    binding: MatchBinding,
    metadata: BindingMetadata,
    expected_consensus_epoch: int,
    execution_scenario: PilotExecutionScenario,
) -> PilotDecisionFrame:
    """Project one exact score successor and its direct paired full-L2 book."""
    _validate_parent_types(
        prior_state,
        frame,
        binding,
        metadata,
        expected_consensus_epoch,
        execution_scenario,
    )
    _validate_binding(
        prior_state=prior_state,
        frame=frame,
        binding=binding,
        metadata=metadata,
    )
    transition = frame.consensus_transition
    observation = frame.l2_observation
    if (
        transition.consensus_epoch != expected_consensus_epoch
        or transition.correction_epoch != prior_state.correction_epoch
        or transition.correction_epoch != transition.accepted_state.correction_epoch
    ):
        _fail("consensus_epoch")
    pair_delay = observation.captured_monotonic_ns - transition.consensus_accepted_monotonic_ns
    if pair_delay < 0 or pair_delay > execution_scenario.maximum_pair_latency_ns:
        _fail("pair_stale")
    lineages = tuple(supporter.source_lineage_sha256 for supporter in transition.supporters)
    try:
        point_event = PilotPointEvent(
            canonical_match_id=binding.canonical_match_id,
            point_id=transition.accepted_state.last_provider_event_id,
            sequence_number=transition.consensus_record_sequence,
            before_state=prior_state,
            after_state=transition.accepted_state,
            server=prior_state.server_for_next_point,
            winner=_winner_for_exact_successor(prior_state, transition.accepted_state),
            consensus_epoch=transition.consensus_epoch,
            consensus_transition_sha256=transition.accepted_score_sha256,
            supporting_source_lineage_sha256s=lineages,
            received_wall_ns=transition.consensus_accepted_wall_ns,
            accepted_monotonic_ns=transition.consensus_accepted_monotonic_ns,
        )
    except (PilotContractError, ValueError):
        _fail("point_transition")
    home_book = _project_book(frame=frame, binding=binding, metadata=metadata, index=0)
    away_book = _project_book(frame=frame, binding=binding, metadata=metadata, index=1)
    values: dict[str, object] = {
        "point_event": point_event,
        "home_book": home_book,
        "away_book": away_book,
        "source_frame_id": frame.frame_id,
        "source_l2_observation_sha256": observation.observation_sha256,
        "consensus_transition_sha256": transition.accepted_score_sha256,
        "accepted_score_sha256": transition.accepted_score_sha256,
        "match_binding_sha256": expert_contract_sha256(binding),
        "binding_metadata_sha256": expert_contract_sha256(metadata),
        "execution_scenario_sha256": execution_scenario.artifact_sha256,
        "consensus_accepted_wall_ns": transition.consensus_accepted_wall_ns,
        "consensus_accepted_monotonic_ns": transition.consensus_accepted_monotonic_ns,
        "book_captured_wall_ns": observation.captured_wall_ns,
        "book_captured_monotonic_ns": observation.captured_monotonic_ns,
    }
    return PilotDecisionFrame(
        decision_frame_sha256=pilot_contract_sha256(values),
        **values,  # type: ignore[arg-type]
    )


def _winner_for_exact_successor(
    before: TennisState,
    after: TennisState,
) -> PlayerSide:
    """Return the sole legal next-point winner, rejecting duplicate/jumps."""
    candidates: list[PlayerSide] = []
    for winner in (PlayerSide.HOME, PlayerSide.AWAY):
        try:
            # PilotPointEvent independently verifies the candidate score.  Its
            # local construction is deliberately deferred until this function
            # resolves a unique winner from score coordinates alone.
            from inci_tennis_expert.pilot_contracts import _expected_next_score, _point_score_coordinates

            if _expected_next_score(before, winner) == _point_score_coordinates(after):
                candidates.append(winner)
        except (ValueError, PilotContractError):
            continue
    if len(candidates) != 1:
        _fail("point_transition")
    return candidates[0]
