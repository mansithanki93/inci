from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from decimal import (
    Clamped,
    Decimal,
    DivisionByZero,
    FloatOperation,
    InvalidOperation,
    Overflow,
    ROUND_DOWN,
    getcontext,
    localcontext,
)
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
import unittest
from unittest import mock

import inci_tennis_expert.contracts as contracts
from inci_tennis_expert.contracts import (
    ArtifactPin,
    BindingMarketMetadata,
    BindingMetadata,
    BindingReviewDecision,
    BindingRoute,
    BindingUniverse,
    BookSyncCursor,
    BookDelta,
    BookEventKind,
    BookLevel,
    BookSnapshot,
    BookState,
    BookTransitionResult,
    ContractSide,
    CausalPointWitness,
    DecisionAction,
    DecisionReason,
    ExpertContractError,
    FairValueEstimate,
    MarketLifecycle,
    MarketStatus,
    MatchBinding,
    MatchFormat,
    MatchStatus,
    LastSyncEmission,
    OpportunityFrame,
    PairedTimeObservation,
    PlayerSide,
    PolicyDecision,
    PolicyEstimate,
    PolicyPathEstimate,
    PendingBookMove,
    ProviderLifecycle,
    ProviderLifecycleKind,
    ProviderPoint,
    ProviderSnapshot,
    ScoreValue,
    SettlementSemantics,
    SetScore,
    SyncPolicy,
    SyncInputKind,
    SyncReason,
    SyncResult,
    SynchronizationInput,
    SynchronizationSessionState,
    SynchronizationTransitionResult,
    TennisSyncCursor,
    TennisState,
    TennisStateInvariantError,
    TennisTransitionError,
    TennisTransitionReason,
    TennisTransitionResult,
    TerminationKind,
    TransitionDisposition,
    TrustedSnapshot,
    canonical_expert_bytes,
    canonical_binding_review_artifact_bytes,
    compute_binding_review_artifact_sha256,
    compute_binding_review_evidence_sha256,
    compute_binding_universe_sha256,
    compute_membership_projection_sha256,
    compute_settlement_projection_sha256,
    expert_contract_sha256,
    player_side_for_contract,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64


def paired_time(**changes: object) -> PairedTimeObservation:
    values: dict[str, object] = {
        "wall_ns": 200,
        "monotonic_ns": 100,
        "clock_uncertainty_ns": 2,
    }
    values.update(changes)
    return PairedTimeObservation(**values)  # type: ignore[arg-type]


def provider_snapshot(**changes: object) -> ProviderSnapshot:
    values: dict[str, object] = {
        "provider_source_id": "provider-a",
        "revision_domain_id": "revision-a",
        "source_lineage_sha256": SHA_A,
        "provider_event_id": "event-1",
        "provider_match_id": "provider-match-1",
        "home_player_id": "player-home",
        "away_player_id": "player-away",
        "scheduled_start_wall_ns": 100,
        "match_format": MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        "status": MatchStatus.LIVE,
        "termination_kind": TerminationKind.NONE,
        "winner": None,
        "retired_side": None,
        "completed_sets": (SetScore(6, 4, None, None),),
        "games_home": 2,
        "games_away": 1,
        "points_home": ScoreValue.FIFTEEN,
        "points_away": ScoreValue.LOVE,
        "in_tiebreak": False,
        "tiebreak_points_home": 0,
        "tiebreak_points_away": 0,
        "tiebreak_first_server": None,
        "server_for_next_point": PlayerSide.HOME,
        "correction_epoch": 0,
        "revision": 4,
        "source_wall_ns": 150,
        "source_generated_wall_ns": 149,
        "received_monotonic_ns": 90,
        "clock_uncertainty_ns": 2,
        "snapshot_complete": True,
    }
    values.update(changes)
    return ProviderSnapshot(**values)  # type: ignore[arg-type]


def provider_point(**changes: object) -> ProviderPoint:
    values: dict[str, object] = {
        "provider_source_id": "provider-a",
        "revision_domain_id": "revision-a",
        "source_lineage_sha256": SHA_A,
        "provider_event_id": "event-2",
        "provider_match_id": "provider-match-1",
        "home_player_id": "player-home",
        "away_player_id": "player-away",
        "scheduled_start_wall_ns": 100,
        "match_format": MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        "correction_epoch": 0,
        "revision": 5,
        "point_winner": PlayerSide.HOME,
        "server_before_point": PlayerSide.HOME,
        "source_wall_ns": 151,
        "source_generated_wall_ns": 150,
        "received_monotonic_ns": 91,
        "clock_uncertainty_ns": 2,
    }
    values.update(changes)
    return ProviderPoint(**values)  # type: ignore[arg-type]


def provider_lifecycle(**changes: object) -> ProviderLifecycle:
    values: dict[str, object] = {
        "provider_source_id": "provider-a",
        "revision_domain_id": "revision-a",
        "source_lineage_sha256": SHA_A,
        "provider_event_id": "event-3",
        "provider_match_id": "provider-match-1",
        "home_player_id": "player-home",
        "away_player_id": "player-away",
        "scheduled_start_wall_ns": 100,
        "match_format": MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        "correction_epoch": 0,
        "revision": 6,
        "kind": ProviderLifecycleKind.SUSPEND,
        "winner": None,
        "retired_side": None,
        "server_for_next_point": PlayerSide.HOME,
        "source_wall_ns": 152,
        "source_generated_wall_ns": 151,
        "received_monotonic_ns": 92,
        "clock_uncertainty_ns": 2,
    }
    values.update(changes)
    return ProviderLifecycle(**values)  # type: ignore[arg-type]


def tennis_state(**changes: object) -> TennisState:
    values: dict[str, object] = {
        "provider_source_id": "provider-a",
        "revision_domain_id": "revision-a",
        "source_lineage_sha256": SHA_A,
        "provider_match_id": "provider-match-1",
        "home_player_id": "player-home",
        "away_player_id": "player-away",
        "scheduled_start_wall_ns": 100,
        "match_format": MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        "status": MatchStatus.LIVE,
        "termination_kind": TerminationKind.NONE,
        "winner": None,
        "retired_side": None,
        "completed_sets": (SetScore(6, 4, None, None),),
        "games_home": 2,
        "games_away": 1,
        "points_home": ScoreValue.FIFTEEN,
        "points_away": ScoreValue.LOVE,
        "in_tiebreak": False,
        "tiebreak_points_home": 0,
        "tiebreak_points_away": 0,
        "tiebreak_first_server": None,
        "server_for_next_point": PlayerSide.HOME,
        "correction_epoch": 0,
        "revision": 4,
        "snapshot_complete": True,
        "last_provider_event_id": "event-1",
        "last_event_semantic_sha256": SHA_B,
        "correction_lineage_sha256": SHA_C,
        "last_source_wall_ns": 150,
        "last_source_generated_wall_ns": 149,
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


def book_level(price: str = "0.45", quantity: str = "10") -> BookLevel:
    return BookLevel(Decimal(price), Decimal(quantity))


def book_snapshot(**changes: object) -> BookSnapshot:
    values: dict[str, object] = {
        "ticker": "MATCH-HOME",
        "connection_epoch": 1,
        "sequence": 7,
        "market_status": MarketStatus.OPEN,
        "scheduled_close_wall_ns": 1_000,
        "source_wall_ns": 160,
        "observed_monotonic_ns": 91,
        "clock_uncertainty_ns": 2,
        "yes_bids": (book_level("0.45", "10"), book_level("0.40", "2")),
        "no_bids": (book_level("0.50", "8"), book_level("0.45", "3")),
    }
    values.update(changes)
    return BookSnapshot(**values)  # type: ignore[arg-type]


def book_delta(**changes: object) -> BookDelta:
    values: dict[str, object] = {
        "ticker": "MATCH-HOME",
        "connection_epoch": 1,
        "sequence": 8,
        "source_wall_ns": 161,
        "observed_monotonic_ns": 92,
        "clock_uncertainty_ns": 2,
        "contract_side": ContractSide.YES,
        "price": Decimal("0.45"),
        "quantity": Decimal("0"),
    }
    values.update(changes)
    return BookDelta(**values)  # type: ignore[arg-type]


def market_lifecycle(**changes: object) -> MarketLifecycle:
    values: dict[str, object] = {
        "ticker": "MATCH-HOME",
        "connection_epoch": 1,
        "market_status": MarketStatus.OPEN,
        "scheduled_close_wall_ns": 1_000,
        "source_wall_ns": 160,
        "observed_monotonic_ns": 92,
        "clock_uncertainty_ns": 2,
    }
    values.update(changes)
    return MarketLifecycle(**values)  # type: ignore[arg-type]


def book_state(**changes: object) -> BookState:
    values: dict[str, object] = {
        "ticker": "MATCH-HOME",
        "connection_epoch": 1,
        "sequence": 7,
        "market_status": MarketStatus.OPEN,
        "scheduled_close_wall_ns": 1_000,
        "book_source_wall_ns": 160,
        "book_observed_monotonic_ns": 91,
        "book_clock_uncertainty_ns": 2,
        "lifecycle_source_wall_ns": 160,
        "lifecycle_observed_monotonic_ns": 92,
        "lifecycle_clock_uncertainty_ns": 2,
        "yes_bids": (book_level("0.45", "10"), book_level("0.40", "2")),
        "no_bids": (book_level("0.50", "8"), book_level("0.45", "3")),
        "trusted": True,
        "sequence_gap": False,
        "last_executable_move": Decimal("0.03"),
        "last_executable_move_monotonic_ns": 90,
        "last_snapshot_sha256": SHA_D,
        "last_event_sha256": SHA_E,
    }
    values.update(changes)
    return BookState(**values)  # type: ignore[arg-type]


def book_transition(**changes: object) -> BookTransitionResult:
    state = changes.pop(
        "state",
        book_state(
            book_observed_monotonic_ns=91,
            last_executable_move=Decimal("0"),
            last_executable_move_monotonic_ns=91,
            last_snapshot_sha256=SHA_D,
            last_event_sha256=SHA_D,
        ),
    )
    values: dict[str, object] = {
        "state": state,
        "accepted_event_kind": BookEventKind.SNAPSHOT,
        "accepted_event_sha256": SHA_D,
        "executable_move": Decimal("0"),
        "move_observed_monotonic_ns": 91,
        "connection_epoch": 1,
        "sequence": 7,
        "top_of_book_changed": False,
    }
    values.update(changes)
    return BookTransitionResult(**values)  # type: ignore[arg-type]


def decode_canonical_contract(encoded: bytes) -> object:
    document = json.loads(encoded)
    if document["canonical_version"] != 1:
        raise AssertionError("canonical_version")
    if document["domain"] != "inci-tennis-expert":
        raise AssertionError("domain")
    enums = {enum.__name__: enum for enum in contracts._REGISTERED_ENUMS}
    records = {
        record.__name__: record for record in contracts._REGISTERED_DATACLASSES
    }

    def decode(value: object) -> object:
        if value is None or type(value) in {bool, int, str}:
            return value
        if type(value) is not dict:
            raise AssertionError("projection")
        if "$decimal" in value:
            return Decimal(value["$decimal"])  # type: ignore[arg-type]
        if "$enum" in value:
            return enums[value["$enum"]](value["value"])  # type: ignore[index]
        if "$tuple" in value:
            return tuple(decode(item) for item in value["$tuple"])  # type: ignore[index]
        if "$list" in value:
            return [decode(item) for item in value["$list"]]  # type: ignore[index]
        if "$dict" in value:
            return {
                key: decode(item)
                for key, item in value["$dict"]  # type: ignore[index]
            }
        record = records[value["$contract"]]  # type: ignore[index]
        field_values = value["fields"]  # type: ignore[index]
        return record(
            **{
                field.name: decode(field_values[field.name])
                for field in fields(record)
            }
        )

    return decode(document["value"])


def match_binding(**changes: object) -> MatchBinding:
    values: dict[str, object] = {
        "provider_match_id": "provider-match-1",
        "canonical_match_id": "canonical-match-9",
        "provider_source_id": "provider-a",
        "revision_domain_id": "revision-a",
        "source_lineage_sha256": SHA_A,
        "provider_home_player_id": "player-home",
        "provider_away_player_id": "player-away",
        "kalshi_event_ticker": "MATCH-EVENT",
        "home_market_ticker": "MATCH-HOME",
        "away_market_ticker": "MATCH-AWAY",
        "match_format": MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        "scheduled_start_wall_ns": 100,
        "start_tolerance_ns": 5,
        "artifact_created_wall_ns": 50,
        "binding_artifact_sha256": SHA_F,
    }
    values.update(changes)
    return MatchBinding(**values)  # type: ignore[arg-type]


def binding_route(**changes: object) -> BindingRoute:
    values: dict[str, object] = {
        "player_side": PlayerSide.HOME,
        "market_ticker": "MATCH-HOME",
        "contract_side": ContractSide.YES,
    }
    values.update(changes)
    return BindingRoute(**values)  # type: ignore[arg-type]


def settlement_semantics(**changes: object) -> SettlementSemantics:
    values: dict[str, object] = {
        "result_authority": "kalshi_finalized_market_result",
        "natural_completion": "yes_if_named_player_final_winner",
        "retirement_after_point": "yes_if_named_player_final_winner",
        "walkover_before_point": "void",
        "default_after_point": "yes_if_named_player_final_winner",
        "disqualification_after_point": "void",
        "cancellation": "void",
        "postponement": "defer",
        "abandonment": "await_latest_finalized_result",
        "amendment": "await_latest_finalized_result",
        "void_treatment": "no_directional_settlement",
        "raw_rules_sha256": SHA_A,
    }
    values["projection_sha256"] = compute_settlement_projection_sha256(
        **values  # type: ignore[arg-type]
    )
    values.update(changes)
    return SettlementSemantics(**values)  # type: ignore[arg-type]


def binding_market_metadata(
    *,
    player_side: PlayerSide = PlayerSide.HOME,
    **changes: object,
) -> BindingMarketMetadata:
    is_home = player_side is PlayerSide.HOME
    values: dict[str, object] = {
        "series_ticker": "TENNIS-SERIES",
        "event_ticker": "MATCH-EVENT",
        "event_id": "kalshi-event-1",
        "market_ticker": "MATCH-HOME" if is_home else "MATCH-AWAY",
        "market_id": "market-home" if is_home else "market-away",
        "yes_player_side": player_side,
        "yes_provider_player_id": "player-home" if is_home else "player-away",
        "yes_canonical_player_id": (
            "canonical-home" if is_home else "canonical-away"
        ),
        "product": "match_winner",
        "event_catalog_sha256": SHA_B,
        "membership_source_id": "kalshi-events-api",
        "membership_source_version": "v2",
        "membership_captured_wall_ns": 40,
        "membership_evidence_sha256": SHA_C if is_home else SHA_D,
        "market_text_sha256": SHA_E,
        "settlement_rule_text_sha256": SHA_A,
        "settlement": settlement_semantics(),
    }
    values["membership_projection_sha256"] = (
        compute_membership_projection_sha256(
            series_ticker=values["series_ticker"],  # type: ignore[arg-type]
            event_ticker=values["event_ticker"],  # type: ignore[arg-type]
            event_id=values["event_id"],  # type: ignore[arg-type]
            market_ticker=values["market_ticker"],  # type: ignore[arg-type]
            market_id=values["market_id"],  # type: ignore[arg-type]
            product=values["product"],  # type: ignore[arg-type]
            event_catalog_sha256=values["event_catalog_sha256"],  # type: ignore[arg-type]
            membership_source_id=values["membership_source_id"],  # type: ignore[arg-type]
            membership_source_version=values["membership_source_version"],  # type: ignore[arg-type]
            membership_captured_wall_ns=values["membership_captured_wall_ns"],  # type: ignore[arg-type]
            membership_evidence_sha256=values["membership_evidence_sha256"],  # type: ignore[arg-type]
        )
    )
    values.update(changes)
    return BindingMarketMetadata(**values)  # type: ignore[arg-type]


def binding_metadata(**changes: object) -> BindingMetadata:
    home = binding_market_metadata()
    away = binding_market_metadata(player_side=PlayerSide.AWAY)
    values: dict[str, object] = {
        "canonical_match_id": "canonical-match-9",
        "canonical_home_player_id": "canonical-home",
        "canonical_away_player_id": "canonical-away",
        "tournament_id": "tournament-1",
        "season_id": "season-2026",
        "draw_id": "draw-main",
        "round_id": "round-1",
        "tour_id": "tour-atp",
        "tier_id": "tier-250",
        "surface": "hard",
        "provider_snapshot_sha256": SHA_C,
        "kalshi_event_sha256": SHA_D,
        "markets": (home, away),
        "authorized_routes": (
            binding_route(),
            binding_route(
                player_side=PlayerSide.AWAY,
                market_ticker="MATCH-AWAY",
            ),
        ),
    }
    values.update(changes)
    return BindingMetadata(**values)  # type: ignore[arg-type]


def binding_review_decision(
    *,
    bindings: tuple[MatchBinding, ...] | None = None,
    metadata: tuple[BindingMetadata, ...] | None = None,
    **changes: object,
) -> BindingReviewDecision:
    binding_values = (match_binding(),) if bindings is None else bindings
    metadata_values = (binding_metadata(),) if metadata is None else metadata
    evidence = compute_binding_review_evidence_sha256(
        ArtifactPin("binding-artifact-1", SHA_F),
        binding_values,
        metadata_values,
    )
    values: dict[str, object] = {
        "review_artifact_id": "binding-review-1",
        "review_artifact_created_wall_ns": 70,
        "binding_artifact_id": "binding-artifact-1",
        "binding_artifact_sha256": SHA_F,
        "decision": "approved",
        "reviewer_id": "reviewer-1",
        "reviewed_wall_ns": 60,
        "review_evidence_sha256": evidence,
    }
    values.update(changes)
    values["review_artifact_sha256"] = (
        compute_binding_review_artifact_sha256(
            review_artifact_id=values["review_artifact_id"],  # type: ignore[arg-type]
            review_artifact_created_wall_ns=values["review_artifact_created_wall_ns"],  # type: ignore[arg-type]
            binding_artifact_id=values["binding_artifact_id"],  # type: ignore[arg-type]
            binding_artifact_sha256=values["binding_artifact_sha256"],  # type: ignore[arg-type]
            decision=values["decision"],  # type: ignore[arg-type]
            reviewer_id=values["reviewer_id"],  # type: ignore[arg-type]
            reviewed_wall_ns=values["reviewed_wall_ns"],  # type: ignore[arg-type]
            review_evidence_sha256=values["review_evidence_sha256"],  # type: ignore[arg-type]
        )
    )
    return BindingReviewDecision(**values)  # type: ignore[arg-type]


def binding_universe(**changes: object) -> BindingUniverse:
    bindings = changes.pop("bindings", (match_binding(),))
    metadata = changes.pop("metadata", (binding_metadata(),))
    assert type(bindings) is tuple
    assert type(metadata) is tuple
    review = changes.pop(
        "review",
        binding_review_decision(bindings=bindings, metadata=metadata),
    )
    assert type(review) is BindingReviewDecision
    pin = ArtifactPin("binding-artifact-1", SHA_F)
    values: dict[str, object] = {
        "raw_artifact_id": pin.artifact_id,
        "raw_artifact_sha256": pin.artifact_sha256,
        "review": review,
        "bindings": bindings,
        "metadata": metadata,
        "universe_sha256": compute_binding_universe_sha256(
            pin,
            review,
            bindings,  # type: ignore[arg-type]
            metadata,  # type: ignore[arg-type]
        ),
    }
    values.update(changes)
    return BindingUniverse(**values)  # type: ignore[arg-type]


def sync_policy(**changes: object) -> SyncPolicy:
    values: dict[str, object] = {
        "universe_sha256": SHA_A,
        "max_score_age_ns": 20,
        "max_book_age_ns": 20,
        "max_lifecycle_age_ns": 20,
        "max_score_book_skew_ns": 20,
        "max_clock_uncertainty_ns": 5,
        "large_book_move_threshold": Decimal("0.10"),
        "explanation_window_ns": 10,
        "minimum_close_horizon_ns": 100,
    }
    values.update(changes)
    return SyncPolicy(**values)  # type: ignore[arg-type]


def trusted_snapshot(**changes: object) -> TrustedSnapshot:
    values: dict[str, object] = {
        "decision_sequence": 1,
        "decision_time": paired_time(),
        "tennis": tennis_state(),
        "book": book_state(),
        "binding": match_binding(),
        "sync_policy_sha256": SHA_B,
        "causal_provider_revision": 4,
    }
    values.update(changes)
    return TrustedSnapshot(**values)  # type: ignore[arg-type]


def opportunity(**changes: object) -> OpportunityFrame:
    snapshot = changes.pop("snapshot", trusted_snapshot())
    assert type(snapshot) is TrustedSnapshot
    binding_sha256 = expert_contract_sha256(snapshot.binding)
    snapshot_sha256 = expert_contract_sha256(snapshot)
    universe_sha256 = changes.pop("universe_sha256", SHA_A)
    identity = {
        "universe_sha256": universe_sha256,
        "canonical_match_id": snapshot.binding.canonical_match_id,
        "ticker": snapshot.book.ticker,
        "decision_sequence": snapshot.decision_sequence,
        "decision_time": snapshot.decision_time,
        "binding_sha256": binding_sha256,
        "provider_revision": snapshot.tennis.revision,
        "book_connection_epoch": snapshot.book.connection_epoch,
        "book_sequence": snapshot.book.sequence,
        "snapshot_sha256": snapshot_sha256,
    }
    opportunity_id = hashlib.sha256(
        b"INCI-OPPORTUNITY-ID-V1\0" + canonical_expert_bytes(identity)
    ).hexdigest()
    values: dict[str, object] = {
        "opportunity_id": opportunity_id,
        "universe_sha256": universe_sha256,
        "canonical_match_id": snapshot.binding.canonical_match_id,
        "ticker": snapshot.book.ticker,
        "decision_sequence": snapshot.decision_sequence,
        "decision_time": snapshot.decision_time,
        "binding_sha256": binding_sha256,
        "provider_revision": snapshot.tennis.revision,
        "book_connection_epoch": snapshot.book.connection_epoch,
        "book_sequence": snapshot.book.sequence,
        "snapshot_sha256": snapshot_sha256,
        "snapshot": snapshot,
    }
    values.update(changes)
    return OpportunityFrame(**values)  # type: ignore[arg-type]


def causal_point_witness(**changes: object) -> CausalPointWitness:
    values: dict[str, object] = {
        "canonical_match_id": "canonical-match-9",
        "correction_epoch": 1,
        "revision": 4,
        "event_semantic_sha256": SHA_D,
        "received_monotonic_ns": 90,
    }
    values.update(changes)
    return CausalPointWitness(**values)  # type: ignore[arg-type]


def pending_book_move(**changes: object) -> PendingBookMove:
    values: dict[str, object] = {
        "canonical_match_id": "canonical-match-9",
        "ticker": "MATCH-HOME",
        "first_move_monotonic_ns": 91,
        "last_move_monotonic_ns": 91,
        "first_connection_epoch": 1,
        "first_sequence": 7,
        "first_event_sha256": SHA_D,
        "last_connection_epoch": 1,
        "last_sequence": 7,
        "last_event_sha256": SHA_D,
        "move_count": 1,
        "max_magnitude": Decimal("0.10"),
        "tennis_correction_epoch_floor": 1,
        "book_connection_epoch_floor": 1,
    }
    values.update(changes)
    return PendingBookMove(**values)  # type: ignore[arg-type]


def tennis_sync_cursor(**changes: object) -> TennisSyncCursor:
    binding = match_binding()
    metadata = binding_metadata()
    values: dict[str, object] = {
        "canonical_match_id": binding.canonical_match_id,
        "binding_sha256": expert_contract_sha256(binding),
        "binding_metadata_sha256": expert_contract_sha256(metadata),
        "tennis": None,
        "last_state_sha256": None,
        "last_input_sha256": None,
        "last_point_witness": None,
    }
    values.update(changes)
    return TennisSyncCursor(**values)  # type: ignore[arg-type]


def last_sync_emission(**changes: object) -> LastSyncEmission:
    values: dict[str, object] = {
        "fingerprint_sha256": SHA_A,
        "provider_correction_epoch": 1,
        "provider_revision": 4,
        "provider_event_semantic_sha256": SHA_D,
        "book_connection_epoch": 1,
        "book_sequence": 7,
    }
    values.update(changes)
    return LastSyncEmission(**values)  # type: ignore[arg-type]


def book_sync_cursor(**changes: object) -> BookSyncCursor:
    binding = match_binding()
    metadata = binding_metadata()
    values: dict[str, object] = {
        "canonical_match_id": binding.canonical_match_id,
        "ticker": binding.home_market_ticker,
        "binding_sha256": expert_contract_sha256(binding),
        "binding_metadata_sha256": expert_contract_sha256(metadata),
        "book": None,
        "last_state_sha256": None,
        "last_input_sha256": None,
        "pending_move": None,
        "causal_point_witness": None,
        "consumed_point_witness": None,
        "last_emission": None,
    }
    values.update(changes)
    return BookSyncCursor(**values)  # type: ignore[arg-type]


def synchronization_session_state(
    **changes: object,
) -> SynchronizationSessionState:
    universe = changes.pop("universe", binding_universe())
    assert type(universe) is BindingUniverse
    policy = changes.pop(
        "policy",
        sync_policy(universe_sha256=universe.universe_sha256),
    )
    assert type(policy) is SyncPolicy
    binding = universe.bindings[0]
    metadata = universe.metadata[0]
    binding_sha256 = expert_contract_sha256(binding)
    metadata_sha256 = expert_contract_sha256(metadata)
    tennis_cursors = (
        TennisSyncCursor(
            canonical_match_id=binding.canonical_match_id,
            binding_sha256=binding_sha256,
            binding_metadata_sha256=metadata_sha256,
            tennis=None,
            last_state_sha256=None,
            last_input_sha256=None,
            last_point_witness=None,
        ),
    )
    book_cursors = tuple(
        BookSyncCursor(
            canonical_match_id=binding.canonical_match_id,
            ticker=ticker,
            binding_sha256=binding_sha256,
            binding_metadata_sha256=metadata_sha256,
            book=None,
            last_state_sha256=None,
            last_input_sha256=None,
            pending_move=None,
            causal_point_witness=None,
            consumed_point_witness=None,
            last_emission=None,
        )
        for ticker in sorted(
            (binding.home_market_ticker, binding.away_market_ticker)
        )
    )
    values: dict[str, object] = {
        "universe": universe,
        "policy": policy,
        "universe_sha256": universe.universe_sha256,
        "sync_policy_sha256": expert_contract_sha256(policy),
        "decision_sequence": 0,
        "last_observation": None,
        "tennis_cursors": tennis_cursors,
        "book_cursors": book_cursors,
    }
    values.update(changes)
    return SynchronizationSessionState(**values)  # type: ignore[arg-type]


def synchronization_input(**changes: object) -> SynchronizationInput:
    values: dict[str, object] = {
        "kind": SyncInputKind.CLOCK,
        "canonical_match_id": "canonical-match-9",
        "ticker": "MATCH-HOME",
        "previous_state_sha256": None,
        "provider_event": None,
        "tennis_transition": None,
        "book_event": None,
        "book_transition": None,
        "book_resnapshot_state": None,
    }
    values.update(changes)
    return SynchronizationInput(**values)  # type: ignore[arg-type]


def sync_result(**changes: object) -> SyncResult:
    values: dict[str, object] = {
        "canonical_match_id": "canonical-match-9",
        "ticker": "MATCH-HOME",
        "snapshot": None,
        "opportunity": None,
        "reason": SyncReason.SNAPSHOT_INCOMPLETE,
    }
    values.update(changes)
    return SyncResult(**values)  # type: ignore[arg-type]


def synchronization_transition_result(
    **changes: object,
) -> SynchronizationTransitionResult:
    prior = changes.pop("prior", synchronization_session_state())
    evidence = changes.pop("input", synchronization_input())
    observation = changes.pop("observation", paired_time())
    assert type(prior) is SynchronizationSessionState
    assert type(evidence) is SynchronizationInput
    assert type(observation) is PairedTimeObservation
    state = changes.pop(
        "state",
        replace(prior, last_observation=observation),
    )
    values: dict[str, object] = {
        "state": state,
        "input": evidence,
        "input_sha256": expert_contract_sha256(evidence),
        "prior_session_sha256": expert_contract_sha256(prior),
        "prior_decision_sequence": prior.decision_sequence,
        "observation": observation,
        "results": (sync_result(),),
    }
    values.update(changes)
    return SynchronizationTransitionResult(**values)  # type: ignore[arg-type]


def fair_value(**changes: object) -> FairValueEstimate:
    values: dict[str, object] = {
        "player_side": PlayerSide.HOME,
        "fair_probability": Decimal("0.60"),
        "lower_probability": Decimal("0.55"),
        "upper_probability": Decimal("0.65"),
        "supported": True,
        "stratum": "bo3-hard-live",
        "model_sha256": SHA_A,
        "prematch_artifact_sha256": SHA_B,
        "feature_definition_sha256": SHA_C,
        "feature_vector_sha256": SHA_D,
        "calibration_artifact_sha256": SHA_E,
        "abstention_reason": None,
    }
    values.update(changes)
    return FairValueEstimate(**values)  # type: ignore[arg-type]


def policy_path(**changes: object) -> PolicyPathEstimate:
    values: dict[str, object] = {
        "path_id": "hold-win",
        "probability": Decimal("1"),
        "filled_quantity": Decimal("2"),
        "residual_quantity": Decimal("0"),
        "net_pnl": Decimal("0.10"),
        "exit_price": Decimal("0.60"),
    }
    values.update(changes)
    return PolicyPathEstimate(**values)  # type: ignore[arg-type]


def policy_estimate(**changes: object) -> PolicyEstimate:
    fv = fair_value()
    values: dict[str, object] = {
        "opportunity_id": "1" * 64,
        "canonical_match_id": "canonical-match-9",
        "ticker": "MATCH-HOME",
        "contract_side": ContractSide.YES,
        "player_side": PlayerSide.HOME,
        "quantity": Decimal("2"),
        "limit_price": Decimal("0.51"),
        "executable_quantity": Decimal("2"),
        "paths": (policy_path(),),
        "probability_tolerance": Decimal("0"),
        "expected_net_pnl": Decimal("0.10"),
        "lower_expected_net_pnl": Decimal("0.05"),
        "upper_expected_net_pnl": Decimal("0.15"),
        "fair_value_sha256": expert_contract_sha256(fv),
        "fee_schedule_sha256": SHA_A,
        "policy_artifact_sha256": SHA_B,
        "supported": True,
        "abstention_reason": None,
    }
    values.update(changes)
    return PolicyEstimate(**values)  # type: ignore[arg-type]


def policy_decision(**changes: object) -> PolicyDecision:
    values: dict[str, object] = {
        "opportunity_id": "1" * 64,
        "canonical_match_id": "canonical-match-9",
        "decision_sequence": 1,
        "decision_time": paired_time(),
        "ticker": "MATCH-HOME",
        "contract_side": ContractSide.YES,
        "player_side": PlayerSide.HOME,
        "quantity": Decimal("2"),
        "limit_price": Decimal("0.51"),
        "action": DecisionAction.PAPER_BUY,
        "reason": DecisionReason.CONSERVATIVE_VALUE_POSITIVE,
        "decision_input_sha256": SHA_C,
        "policy_artifact_sha256": SHA_B,
        "fee_schedule_sha256": SHA_A,
    }
    values.update(changes)
    return PolicyDecision(**values)  # type: ignore[arg-type]


def all_valid_contracts() -> tuple[object, ...]:
    state = tennis_state()
    transition = TennisTransitionResult(
        state,
        TransitionDisposition.APPLIED,
        TennisTransitionReason.POINT_APPLIED,
        SHA_D,
    )
    return (
        ArtifactPin("artifact-1", SHA_A),
        paired_time(),
        SetScore(6, 4, None, None),
        provider_snapshot(),
        provider_point(),
        provider_lifecycle(),
        state,
        transition,
        book_level(),
        book_snapshot(),
        book_delta(),
        market_lifecycle(),
        book_state(),
        book_transition(),
        match_binding(),
        binding_route(),
        settlement_semantics(),
        binding_market_metadata(),
        binding_metadata(),
        binding_review_decision(),
        binding_universe(),
        causal_point_witness(),
        pending_book_move(),
        tennis_sync_cursor(),
        last_sync_emission(),
        book_sync_cursor(),
        synchronization_session_state(),
        synchronization_input(),
        sync_result(),
        synchronization_transition_result(),
        sync_policy(),
        trusted_snapshot(),
        opportunity(),
        fair_value(),
        policy_path(),
        policy_estimate(),
        policy_decision(),
    )


ENUM_WIRE_VALUES = {
    "PlayerSide": {"HOME": "home", "AWAY": "away"},
    "ContractSide": {"YES": "yes", "NO": "no"},
    "MatchStatus": {
        "SCHEDULED": "scheduled",
        "LIVE": "live",
        "SUSPENDED": "suspended",
        "ENDED": "ended",
        "CANCELLED": "cancelled",
    },
    "MatchFormat": {
        "STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS": (
            "standard_advantage_bo3_tb7_all_sets"
        ),
        "STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS": (
            "standard_advantage_bo5_tb7_all_sets"
        ),
        "UNSUPPORTED": "unsupported",
    },
    "TerminationKind": {
        "NONE": "none",
        "NATURAL": "natural",
        "WALKOVER": "walkover",
        "RETIREMENT": "retirement",
        "CANCELLATION": "cancellation",
    },
    "ProviderLifecycleKind": {
        "START": "start",
        "SUSPEND": "suspend",
        "RESUME": "resume",
        "WALKOVER": "walkover",
        "RETIREMENT": "retirement",
        "CANCEL": "cancel",
        "NATURAL_END_CONFIRMATION": "natural_end_confirmation",
    },
    "ScoreValue": {
        "LOVE": "love",
        "FIFTEEN": "fifteen",
        "THIRTY": "thirty",
        "FORTY": "forty",
        "ADVANTAGE": "advantage",
    },
    "TransitionDisposition": {
        "APPLIED": "applied",
        "DUPLICATE": "duplicate",
        "BLOCKED": "blocked",
    },
    "TennisTransitionReason": {
        name: value
        for name, value in (
            ("POINT_APPLIED", "point_applied"),
            ("LIFECYCLE_APPLIED", "lifecycle_applied"),
            ("NATURAL_END_CONFIRMED", "natural_end_confirmed"),
            ("CORRECTION_APPLIED", "correction_applied"),
            ("EXACT_DUPLICATE", "exact_duplicate"),
            ("CORRECTION_REQUIRED", "correction_required"),
            ("PROVIDER_EVENT_GAP", "provider_event_gap"),
            ("PROVIDER_EVENT_STALE", "provider_event_stale"),
            ("PROVIDER_EVENT_CONFLICT", "provider_event_conflict"),
            ("CORRECTION_EPOCH_STALE", "correction_epoch_stale"),
            ("CORRECTION_EPOCH_AHEAD", "correction_epoch_ahead"),
            ("IDENTITY_MISMATCH", "identity_mismatch"),
            ("FORMAT_MISMATCH", "format_mismatch"),
            ("SOURCE_LINEAGE_MISMATCH", "source_lineage_mismatch"),
            ("REVISION_DOMAIN_MISMATCH", "revision_domain_mismatch"),
            ("RECEIVE_TIME_REGRESSION", "receive_time_regression"),
            ("SERVER_MISMATCH", "server_mismatch"),
            ("POINT_WHILE_NOT_LIVE", "point_while_not_live"),
            ("TERMINAL_ABSORBING", "terminal_absorbing"),
            ("ILLEGAL_LIFECYCLE_TRANSITION", "illegal_lifecycle_transition"),
            ("UNSUPPORTED_FORMAT", "unsupported_format"),
            ("SNAPSHOT_INVALID", "snapshot_invalid"),
            ("CORRECTION_EPOCH_NOT_NEWER", "correction_epoch_not_newer"),
            ("CORRECTION_SNAPSHOT_INVALID", "correction_snapshot_invalid"),
        )
    },
    "MarketStatus": {
        "PREOPEN": "preopen",
        "OPEN": "open",
        "SUSPENDED": "suspended",
        "CLOSED": "closed",
        "SETTLED": "settled",
        "CANCELLED": "cancelled",
    },
    "BookEventKind": {
        "SNAPSHOT": "snapshot",
        "DELTA": "delta",
        "LIFECYCLE": "lifecycle",
    },
    "SyncInputKind": {
        "TENNIS_ORIGIN": "tennis_origin",
        "TENNIS_TRANSITION": "tennis_transition",
        "BOOK_TRANSITION": "book_transition",
        "BOOK_RESNAPSHOT_REQUIRED": "book_resnapshot_required",
        "CLOCK": "clock",
    },
    "DecisionAction": {
        "ABSTAIN": "abstain",
        "PAPER_BUY": "paper_buy",
        "PAPER_SELL": "paper_sell",
    },
    "SyncReason": {
        name: value
        for name, value in (
            ("TRUSTED_SYNCHRONIZED", "trusted_synchronized"),
            ("DUPLICATE_STATE_SUPPRESSED", "duplicate_state_suppressed"),
            ("SNAPSHOT_INCOMPLETE", "snapshot_incomplete"),
            ("SCORE_STALE", "score_stale"),
            ("BOOK_STALE", "book_stale"),
            ("LIFECYCLE_STALE", "lifecycle_stale"),
            ("BOOK_UNTRUSTED", "book_untrusted"),
            ("BOOK_NOT_EXECUTABLE", "book_not_executable"),
            ("MARKET_NOT_OPEN", "market_not_open"),
            ("MARKET_SUSPENDED", "market_suspended"),
            ("MARKET_ENDED", "market_ended"),
            ("UNKNOWN_SERVER", "unknown_server"),
            ("BINDING_AMBIGUOUS", "binding_ambiguous"),
            ("BINDING_DRIFT", "binding_drift"),
            ("SEQUENCE_GAP", "sequence_gap"),
            ("UNEXPLAINED_BOOK_MOVE", "unexplained_book_move"),
            ("MATCH_NOT_STARTED", "match_not_started"),
            ("MATCH_SUSPENDED", "match_suspended"),
            ("MATCH_ENDED", "match_ended"),
            ("CORRECTION_PENDING", "correction_pending"),
            ("CONTRACT_MISMATCH", "contract_mismatch"),
            ("CLOCK_UNCERTAIN", "clock_uncertain"),
            ("CLOSE_HORIZON_INSUFFICIENT", "close_horizon_insufficient"),
        )
    },
    "DecisionReason": {
        name: value
        for name, value in (
            ("CONSERVATIVE_VALUE_POSITIVE", "conservative_value_positive"),
            ("BASELINE_SIGNAL_POSITIVE", "baseline_signal_positive"),
            ("SIMPLE_SCORE_VALUE_POSITIVE", "simple_score_value_positive"),
            ("NO_TRADE_BASELINE", "no_trade_baseline"),
            ("SIGNAL_NOT_TRIGGERED", "signal_not_triggered"),
            ("SYNC_UNTRUSTED", "sync_untrusted"),
            ("MODEL_UNSUPPORTED", "model_unsupported"),
            ("MODEL_UNCERTAIN", "model_uncertain"),
            ("MODEL_OUT_OF_DISTRIBUTION", "model_out_of_distribution"),
            ("POLICY_UNSEALED", "policy_unsealed"),
            ("EDGE_BELOW_COST", "edge_below_cost"),
            ("PRICE_OUTSIDE_LIMIT", "price_outside_limit"),
            ("INSUFFICIENT_DEPTH", "insufficient_depth"),
            ("NOT_SELECTED", "not_selected"),
            ("PENDING_ORDER", "pending_order"),
            ("RISK_CONFLICT", "risk_conflict"),
            ("POST_STOP_COOLDOWN", "post_stop_cooldown"),
            ("SIGNAL_NOT_RESET", "signal_not_reset"),
            ("ATTEMPT_LIMIT", "attempt_limit"),
            ("MATCH_LOSS_LIMIT", "match_loss_limit"),
            ("CONSECUTIVE_LOSS_LIMIT", "consecutive_loss_limit"),
            ("SESSION_LOSS_LIMIT", "session_loss_limit"),
            ("PORTFOLIO_CAPACITY", "portfolio_capacity"),
            ("PORTFOLIO_HALTED", "portfolio_halted"),
            ("DUE_RECHECK_FAILED", "due_recheck_failed"),
            ("FAIR_VALUE_CONVERGED", "fair_value_converged"),
            ("THESIS_INVALIDATED", "thesis_invalidated"),
            ("POLICY_VALUE_NEGATIVE", "policy_value_negative"),
            ("HOLDING_HORIZON_REACHED", "holding_horizon_reached"),
            ("RISK_EXIT_REQUIRED", "risk_exit_required"),
            ("MARKET_SUSPENDED_EXIT", "market_suspended_exit"),
            ("SETTLEMENT_EXIT", "settlement_exit"),
            ("SHUTDOWN_EXIT", "shutdown_exit"),
        )
    },
}

ENUM_WIRE_VALUES.update(
    {
        "ExpertEventKindV1": {
            "SYNCHRONIZATION_APPLIED": "synchronization_applied",
            "OBSERVATION_IGNORED": "observation_ignored",
            "OBSERVATION_REJECTED": "observation_rejected",
        },
        "ExpertReplayDiagnosticRoleV1": {
            "PHASE1_MARKER": "phase1_marker",
            "PHASE1_WAL": "phase1_wal",
            "EXPERT_MARKER": "expert_marker",
            "EXPERT_JOURNAL": "expert_journal",
        },
        "ExpertReplayDiagnosticIssueV1": {
            name: value
            for name, value in (
                ("ENTRY_MISSING", "entry_missing"),
                ("ENTRY_NOT_REGULAR", "entry_not_regular"),
                ("ENTRY_IDENTITY_INVALID", "entry_identity_invalid"),
                ("ENTRY_REPLACED", "entry_replaced"),
                ("PREFIX_TRUNCATED", "prefix_truncated"),
                ("HEADER_INVALID", "header_invalid"),
                ("SESSION_START_INVALID", "session_start_invalid"),
                ("MANIFEST_FRAME_INVALID", "manifest_frame_invalid"),
                ("SCAN_INVALID", "scan_invalid"),
            )
        },
        "ExpertIgnoreReasonV1": {
            "NORMALIZER_NOT_REGISTERED": "normalizer_not_registered",
            "EVENT_NOT_RELEVANT": "event_not_relevant",
        },
        "ExpertRejectReasonV1": {
            name: value
            for name, value in (
                ("PARENT_CONTRACT_INVALID", "parent_contract_invalid"),
                ("NORMALIZER_SCHEMA_UNKNOWN", "normalizer_schema_unknown"),
                ("NORMALIZER_PAYLOAD_INVALID", "normalizer_payload_invalid"),
                (
                    "NORMALIZER_CONTRACT_VIOLATION",
                    "normalizer_contract_violation",
                ),
                ("NORMALIZER_EXCEPTION", "normalizer_exception"),
                ("GROUP_CAPACITY_EXCEEDED", "group_capacity_exceeded"),
                (
                    "PERSISTENCE_CAPACITY_EXCEEDED",
                    "persistence_capacity_exceeded",
                ),
                (
                    "SYNCHRONIZATION_SESSION_DRIFT",
                    "synchronization_session_drift",
                ),
                ("REDUCER_EXCEPTION", "reducer_exception"),
                ("PRIOR_OUTCOME_HALTED", "prior_outcome_halted"),
                ("PRIOR_GROUP_HALTED", "prior_group_halted"),
                ("STATIC_SESSION_HALT", "static_session_halt"),
            )
        },
        "ExpertTerminalReasonV1": {
            "OPERATOR_STOP": "operator_stop",
            "SESSION_END": "session_end",
            "EXPERT_HALT": "expert_halt",
        },
        "ExpertJournalFrameKindV1": {
            "MANIFEST": "manifest",
            "PARENT_GROUP": "parent_group",
            "TERMINAL": "terminal",
        },
        "ExpertJournalScanIssueV1": {
            name: value
            for name, value in (
                ("MISSING_TERMINAL", "missing_terminal"),
                ("HALTED_TERMINAL", "halted_terminal"),
                ("TORN_TAIL", "torn_tail"),
                ("CORRUPT_TAIL", "corrupt_tail"),
                ("DURABLE_UNACKNOWLEDGED", "durable_unacknowledged"),
                ("EVIDENCE_IDENTITY_LOST", "evidence_identity_lost"),
            )
        },
        "ExpertReplayMismatchV1": {
            name: name.lower()
            for name in (
                "EVIDENCE_CONTEXT_MISMATCH",
                "EVIDENCE_REPLAY_NOT_EXACT",
                "EVIDENCE_TERMINAL_NOT_CLEAN",
                "EVIDENCE_SESSION_MISMATCH",
                "EVIDENCE_MANIFEST_MISMATCH",
                "RETENTION_AUTHORIZATION_MISMATCH",
                "RETENTION_DEADLINE_REACHED",
                "EVIDENCE_IDENTITY_MISMATCH",
                "CURRENT_ENVIRONMENT_MISMATCH",
                "RETENTION_BINDING_MISMATCH",
                "COMPANION_SCAN_INVALID",
                "COMPANION_MANIFEST_MISMATCH",
                "EXPERT_SEQUENCE_MISMATCH",
                "PARENT_MISSING",
                "PARENT_EXTRA",
                "PARENT_ORDER_MISMATCH",
                "PARENT_KIND_MISMATCH",
                "PARENT_DIGEST_MISMATCH",
                "PARENT_GROUP_SHAPE_MISMATCH",
                "PRIOR_RECORD_CHAIN_MISMATCH",
                "PRIOR_STATE_MISMATCH",
                "EVENT_SCHEMA_UNPINNED",
                "RECORD_DIGEST_MISMATCH",
                "PAYLOAD_DESCRIPTOR_MISMATCH",
                "PAYLOAD_BYTES_MISMATCH",
                "NORMALIZED_OBSERVATION_MISMATCH",
                "REDUCTION_MISMATCH",
                "POST_STATE_MISMATCH",
                "TRACE_MISMATCH",
                "TERMINAL_MISSING",
                "TERMINAL_REASON_MISMATCH",
                "TERMINAL_COUNT_MISMATCH",
                "TERMINAL_PROVENANCE_MISMATCH",
                "TERMINAL_STATE_MISMATCH",
                "TERMINAL_TRACE_MISMATCH",
            )
        },
    }
)


EXPECTED_FIELD_ORDER = {
    "ArtifactPin": ("artifact_id", "artifact_sha256"),
    "PairedTimeObservation": ("wall_ns", "monotonic_ns", "clock_uncertainty_ns"),
    "SetScore": (
        "games_home",
        "games_away",
        "tiebreak_points_home",
        "tiebreak_points_away",
    ),
    "ProviderSnapshot": (
        "provider_source_id",
        "revision_domain_id",
        "source_lineage_sha256",
        "provider_event_id",
        "provider_match_id",
        "home_player_id",
        "away_player_id",
        "scheduled_start_wall_ns",
        "match_format",
        "status",
        "termination_kind",
        "winner",
        "retired_side",
        "completed_sets",
        "games_home",
        "games_away",
        "points_home",
        "points_away",
        "in_tiebreak",
        "tiebreak_points_home",
        "tiebreak_points_away",
        "tiebreak_first_server",
        "server_for_next_point",
        "correction_epoch",
        "revision",
        "source_wall_ns",
        "source_generated_wall_ns",
        "received_monotonic_ns",
        "clock_uncertainty_ns",
        "snapshot_complete",
    ),
    "ProviderPoint": (
        "provider_source_id",
        "revision_domain_id",
        "source_lineage_sha256",
        "provider_event_id",
        "provider_match_id",
        "home_player_id",
        "away_player_id",
        "scheduled_start_wall_ns",
        "match_format",
        "correction_epoch",
        "revision",
        "point_winner",
        "server_before_point",
        "source_wall_ns",
        "source_generated_wall_ns",
        "received_monotonic_ns",
        "clock_uncertainty_ns",
    ),
    "ProviderLifecycle": (
        "provider_source_id",
        "revision_domain_id",
        "source_lineage_sha256",
        "provider_event_id",
        "provider_match_id",
        "home_player_id",
        "away_player_id",
        "scheduled_start_wall_ns",
        "match_format",
        "correction_epoch",
        "revision",
        "kind",
        "winner",
        "retired_side",
        "server_for_next_point",
        "source_wall_ns",
        "source_generated_wall_ns",
        "received_monotonic_ns",
        "clock_uncertainty_ns",
    ),
    "TennisState": (
        "provider_source_id",
        "revision_domain_id",
        "source_lineage_sha256",
        "provider_match_id",
        "home_player_id",
        "away_player_id",
        "scheduled_start_wall_ns",
        "match_format",
        "status",
        "termination_kind",
        "winner",
        "retired_side",
        "completed_sets",
        "games_home",
        "games_away",
        "points_home",
        "points_away",
        "in_tiebreak",
        "tiebreak_points_home",
        "tiebreak_points_away",
        "tiebreak_first_server",
        "server_for_next_point",
        "correction_epoch",
        "revision",
        "snapshot_complete",
        "last_provider_event_id",
        "last_event_semantic_sha256",
        "correction_lineage_sha256",
        "last_source_wall_ns",
        "last_source_generated_wall_ns",
        "last_received_monotonic_ns",
        "last_clock_uncertainty_ns",
        "block_reason",
        "expected_revision",
        "observed_revision",
        "blocked_event_semantic_sha256",
        "blocked_received_monotonic_ns",
    ),
    "TennisTransitionResult": (
        "state",
        "disposition",
        "reason",
        "event_semantic_sha256",
    ),
    "BookLevel": ("price", "quantity"),
    "BookSnapshot": (
        "ticker",
        "connection_epoch",
        "sequence",
        "market_status",
        "scheduled_close_wall_ns",
        "source_wall_ns",
        "observed_monotonic_ns",
        "clock_uncertainty_ns",
        "yes_bids",
        "no_bids",
    ),
    "BookDelta": (
        "ticker",
        "connection_epoch",
        "sequence",
        "source_wall_ns",
        "observed_monotonic_ns",
        "clock_uncertainty_ns",
        "contract_side",
        "price",
        "quantity",
    ),
    "MarketLifecycle": (
        "ticker",
        "connection_epoch",
        "market_status",
        "scheduled_close_wall_ns",
        "source_wall_ns",
        "observed_monotonic_ns",
        "clock_uncertainty_ns",
    ),
    "BookState": (
        "ticker",
        "connection_epoch",
        "sequence",
        "market_status",
        "scheduled_close_wall_ns",
        "book_source_wall_ns",
        "book_observed_monotonic_ns",
        "book_clock_uncertainty_ns",
        "lifecycle_source_wall_ns",
        "lifecycle_observed_monotonic_ns",
        "lifecycle_clock_uncertainty_ns",
        "yes_bids",
        "no_bids",
        "trusted",
        "sequence_gap",
        "last_executable_move",
        "last_executable_move_monotonic_ns",
        "last_snapshot_sha256",
        "last_event_sha256",
    ),
    "BookTransitionResult": (
        "state",
        "accepted_event_kind",
        "accepted_event_sha256",
        "executable_move",
        "move_observed_monotonic_ns",
        "connection_epoch",
        "sequence",
        "top_of_book_changed",
    ),
    "MatchBinding": (
        "provider_match_id",
        "canonical_match_id",
        "provider_source_id",
        "revision_domain_id",
        "source_lineage_sha256",
        "provider_home_player_id",
        "provider_away_player_id",
        "kalshi_event_ticker",
        "home_market_ticker",
        "away_market_ticker",
        "match_format",
        "scheduled_start_wall_ns",
        "start_tolerance_ns",
        "artifact_created_wall_ns",
        "binding_artifact_sha256",
    ),
    "BindingRoute": (
        "player_side",
        "market_ticker",
        "contract_side",
    ),
    "SettlementSemantics": (
        "result_authority",
        "natural_completion",
        "retirement_after_point",
        "walkover_before_point",
        "default_after_point",
        "disqualification_after_point",
        "cancellation",
        "postponement",
        "abandonment",
        "amendment",
        "void_treatment",
        "raw_rules_sha256",
        "projection_sha256",
    ),
    "BindingMarketMetadata": (
        "series_ticker",
        "event_ticker",
        "event_id",
        "market_ticker",
        "market_id",
        "yes_player_side",
        "yes_provider_player_id",
        "yes_canonical_player_id",
        "product",
        "event_catalog_sha256",
        "membership_source_id",
        "membership_source_version",
        "membership_captured_wall_ns",
        "membership_evidence_sha256",
        "membership_projection_sha256",
        "market_text_sha256",
        "settlement_rule_text_sha256",
        "settlement",
    ),
    "BindingMetadata": (
        "canonical_match_id",
        "canonical_home_player_id",
        "canonical_away_player_id",
        "tournament_id",
        "season_id",
        "draw_id",
        "round_id",
        "tour_id",
        "tier_id",
        "surface",
        "provider_snapshot_sha256",
        "kalshi_event_sha256",
        "markets",
        "authorized_routes",
    ),
    "BindingReviewDecision": (
        "review_artifact_id",
        "review_artifact_sha256",
        "review_artifact_created_wall_ns",
        "binding_artifact_id",
        "binding_artifact_sha256",
        "decision",
        "reviewer_id",
        "reviewed_wall_ns",
        "review_evidence_sha256",
    ),
    "BindingUniverse": (
        "raw_artifact_id",
        "raw_artifact_sha256",
        "review",
        "bindings",
        "metadata",
        "universe_sha256",
    ),
    "CausalPointWitness": (
        "canonical_match_id",
        "correction_epoch",
        "revision",
        "event_semantic_sha256",
        "received_monotonic_ns",
    ),
    "PendingBookMove": (
        "canonical_match_id",
        "ticker",
        "first_move_monotonic_ns",
        "last_move_monotonic_ns",
        "first_connection_epoch",
        "first_sequence",
        "first_event_sha256",
        "last_connection_epoch",
        "last_sequence",
        "last_event_sha256",
        "move_count",
        "max_magnitude",
        "tennis_correction_epoch_floor",
        "book_connection_epoch_floor",
    ),
    "TennisSyncCursor": (
        "canonical_match_id",
        "binding_sha256",
        "binding_metadata_sha256",
        "tennis",
        "last_state_sha256",
        "last_input_sha256",
        "last_point_witness",
    ),
    "LastSyncEmission": (
        "fingerprint_sha256",
        "provider_correction_epoch",
        "provider_revision",
        "provider_event_semantic_sha256",
        "book_connection_epoch",
        "book_sequence",
    ),
    "BookSyncCursor": (
        "canonical_match_id",
        "ticker",
        "binding_sha256",
        "binding_metadata_sha256",
        "book",
        "last_state_sha256",
        "last_input_sha256",
        "pending_move",
        "causal_point_witness",
        "consumed_point_witness",
        "last_emission",
    ),
    "SynchronizationSessionState": (
        "universe",
        "policy",
        "universe_sha256",
        "sync_policy_sha256",
        "decision_sequence",
        "last_observation",
        "tennis_cursors",
        "book_cursors",
    ),
    "SynchronizationInput": (
        "kind",
        "canonical_match_id",
        "ticker",
        "previous_state_sha256",
        "provider_event",
        "tennis_transition",
        "book_event",
        "book_transition",
        "book_resnapshot_state",
    ),
    "SyncResult": (
        "canonical_match_id",
        "ticker",
        "snapshot",
        "opportunity",
        "reason",
    ),
    "SynchronizationTransitionResult": (
        "state",
        "input",
        "input_sha256",
        "prior_session_sha256",
        "prior_decision_sequence",
        "observation",
        "results",
    ),
    "SyncPolicy": (
        "universe_sha256",
        "max_score_age_ns",
        "max_book_age_ns",
        "max_lifecycle_age_ns",
        "max_score_book_skew_ns",
        "max_clock_uncertainty_ns",
        "large_book_move_threshold",
        "explanation_window_ns",
        "minimum_close_horizon_ns",
    ),
    "TrustedSnapshot": (
        "decision_sequence",
        "decision_time",
        "tennis",
        "book",
        "binding",
        "sync_policy_sha256",
        "causal_provider_revision",
    ),
    "OpportunityFrame": (
        "opportunity_id",
        "universe_sha256",
        "canonical_match_id",
        "ticker",
        "decision_sequence",
        "decision_time",
        "binding_sha256",
        "provider_revision",
        "book_connection_epoch",
        "book_sequence",
        "snapshot_sha256",
        "snapshot",
    ),
    "FairValueEstimate": (
        "player_side",
        "fair_probability",
        "lower_probability",
        "upper_probability",
        "supported",
        "stratum",
        "model_sha256",
        "prematch_artifact_sha256",
        "feature_definition_sha256",
        "feature_vector_sha256",
        "calibration_artifact_sha256",
        "abstention_reason",
    ),
    "PolicyPathEstimate": (
        "path_id",
        "probability",
        "filled_quantity",
        "residual_quantity",
        "net_pnl",
        "exit_price",
    ),
    "PolicyEstimate": (
        "opportunity_id",
        "canonical_match_id",
        "ticker",
        "contract_side",
        "player_side",
        "quantity",
        "limit_price",
        "executable_quantity",
        "paths",
        "probability_tolerance",
        "expected_net_pnl",
        "lower_expected_net_pnl",
        "upper_expected_net_pnl",
        "fair_value_sha256",
        "fee_schedule_sha256",
        "policy_artifact_sha256",
        "supported",
        "abstention_reason",
    ),
    "PolicyDecision": (
        "opportunity_id",
        "canonical_match_id",
        "decision_sequence",
        "decision_time",
        "ticker",
        "contract_side",
        "player_side",
        "quantity",
        "limit_price",
        "action",
        "reason",
        "decision_input_sha256",
        "policy_artifact_sha256",
        "fee_schedule_sha256",
    ),
}

EXPECTED_FIELD_ORDER.update(
    {
        "ExpertCurrentEnvironmentV1": (
            "schema_version",
            "phase1_code_sha256",
            "phase1_adapter_code_sha256",
            "expert_code_sha256",
            "io_code_sha256",
            "expert_adapter_code_sha256",
            "runtime_code_sha256",
            "dependency_lock_sha256",
            "python_runtime_sha256",
            "normalizer_registry_sha256",
            "structural_schema_bundle_sha256",
            "event_schema_bundle_sha256",
        ),
        "ExpertProviderDomainBindingV1": (
            "schema_version",
            "phase1_session_manifest_sha256",
            "match_binding_universe_sha256",
            "provider_id",
            "product_tier",
            "source_lineage_id",
            "provider_manifest_canonical_sha256",
            "provider_source_lineage_sha256",
            "revision_domain_id",
            "provider_domain_binding_sha256",
        ),
        "ExpertRetentionBindingV1": (
            "schema_version",
            "session_id",
            "evidence_session_manifest_sha256",
            "provider_request_binding_sha256",
            "permission_artifact_sha256",
            "qualification_artifact_sha256",
            "qualification_trace_sha256",
            "retention_delete_by_ns",
            "access_expires_at_ns",
            "analysis_expires_at_ns",
            "retention_binding_sha256",
        ),
        "ExpertSchemaPinV1": (
            "schema_role",
            "contract_name",
            "resource_name",
            "schema_resource_sha256",
        ),
        "ExpertStructuralSchemaBundleV1": (
            "schema_version",
            "pins",
            "bundle_sha256",
        ),
        "ExpertEventSchemaPinV1": (
            "event_kind",
            "event_version",
            "payload_contract_name",
            "resource_name",
            "schema_resource_sha256",
        ),
        "ExpertEventSchemaBundleV1": (
            "schema_version",
            "pins",
            "bundle_sha256",
        ),
        "ExpertNormalizerPinV1": (
            "normalizer_id",
            "source_kind",
            "source_id",
            "event_type",
            "event_version",
            "normalizer_code_sha256",
            "normalizer_schema_sha256",
        ),
        "ExpertNormalizerRegistryV1": (
            "schema_version",
            "fallback",
            "entries",
            "registry_sha256",
        ),
        "ExpertCollectedEnvironmentV1": (
            "current",
            "normalizers",
            "structural_schemas",
            "event_schemas",
        ),
        "ExpertCapacityProofV1": (
            "schema_version",
            "match_binding_universe_sha256",
            "sync_policy_sha256",
            "maximum_output_count",
            "maximum_synchronization_state_bytes",
            "maximum_transition_payload_bytes",
            "maximum_rejected_payload_bytes",
            "maximum_event_payload_bytes",
            "maximum_group_payload_area_bytes",
            "maximum_group_metadata_bytes",
            "maximum_group_frame_bytes",
            "maximum_terminal_metadata_bytes",
            "maximum_terminal_frame_bytes",
            "emergency_reserve_bytes",
            "proof_sha256",
        ),
        "ExpertSessionManifestV1": (
            "schema_version",
            "session_id",
            "evidence_session_manifest_sha256",
            "evidence_session_start_record_sha256",
            "provider_id",
            "product_tier",
            "source_lineage_id",
            "provider_manifest_file_sha256",
            "provider_manifest_canonical_sha256",
            "entitlement_id_sha256",
            "provider_request_binding_sha256",
            "permission_artifact_sha256",
            "qualification_artifact_sha256",
            "qualification_trace_sha256",
            "provider_domain",
            "environment",
            "retention",
            "match_binding_universe_sha256",
            "binding_raw_artifact_id",
            "binding_raw_artifact_sha256",
            "binding_review_artifact_id",
            "binding_review_artifact_sha256",
            "sync_policy_sha256",
            "initial_synchronization_sha256",
            "normalizers",
            "structural_schemas",
            "event_schemas",
            "capacity",
            "artifact_pins",
            "manifest_sha256",
        ),
        "ExpertSynchronizationDraftV1": ("evidence",),
        "ExpertIgnoredDraftV1": ("reason",),
        "ExpertRejectedDraftV1": ("reason",),
        "ExpertParentEvidenceV1": (
            "session_id",
            "ingest_seq",
            "record_sha256",
            "event_type",
            "event_version",
            "local_wall_ns",
            "local_monotonic_ns",
            "clock_uncertainty_ns",
        ),
        "ExpertSynchronizationObservationV1": (
            "parent",
            "parent_output_index",
            "parent_output_count",
            "normalizer_id",
            "normalizer_code_sha256",
            "normalizer_schema_sha256",
            "evidence",
            "observation",
        ),
        "ExpertIgnoredObservationV1": (
            "parent",
            "parent_output_index",
            "parent_output_count",
            "normalizer_id",
            "normalizer_code_sha256",
            "normalizer_schema_sha256",
            "reason",
        ),
        "ExpertRejectedObservationV1": (
            "parent",
            "parent_output_index",
            "parent_output_count",
            "normalizer_id",
            "normalizer_code_sha256",
            "normalizer_schema_sha256",
            "reason",
        ),
        "ExpertStateV1": (
            "schema_version",
            "session_id",
            "expert_manifest_sha256",
            "match_binding_universe_sha256",
            "sync_policy_sha256",
            "initial_synchronization_sha256",
            "synchronization",
            "rejected_parent_count",
            "halted",
            "halt_reason",
        ),
        "ExpertSynchronizationAppliedPayloadV1": (
            "observation",
            "transition",
        ),
        "ExpertObservationIgnoredPayloadV1": ("observation",),
        "ExpertObservationRejectedPayloadV1": ("observation", "reason"),
        "ExpertOutcomeDraftV1": (
            "event_kind",
            "event_version",
            "event_schema_sha256",
            "payload",
            "prior_expert_state_sha256",
            "post_state",
            "post_expert_state_sha256",
        ),
        "ExpertReductionV1": (
            "prior_expert_state_sha256",
            "outcomes",
            "final_state",
            "final_expert_state_sha256",
            "halt_required",
        ),
        "ExpertJournalCursorV1": (
            "schema_version",
            "session_id",
            "group_count",
            "record_count",
            "last_parent_ingest_seq",
            "last_parent_record_sha256",
            "expert_seq",
            "expert_record_sha256",
            "expert_state_sha256",
            "expert_trace_sha256",
        ),
        "ExpertPayloadDescriptorV1": (
            "schema_version",
            "content_type",
            "payload_encoding",
            "payload_contract_name",
            "payload_length",
            "payload_sha256",
        ),
        "ExpertJournalRecordV1": (
            "schema_version",
            "session_id",
            "expert_manifest_sha256",
            "provider_request_binding_sha256",
            "match_binding_universe_sha256",
            "retention_binding_sha256",
            "expert_seq",
            "parent",
            "parent_output_index",
            "parent_output_count",
            "event_kind",
            "event_version",
            "event_schema_sha256",
            "prior_expert_record_sha256",
            "prior_expert_state_sha256",
            "payload",
            "post_expert_state_sha256",
            "record_sha256",
        ),
        "ExpertTraceStepV1": (
            "schema_version",
            "expert_seq",
            "prior_trace_sha256",
            "expert_record_sha256",
            "post_expert_state_sha256",
            "post_trace_sha256",
        ),
        "ExpertJournalGroupV1": (
            "schema_version",
            "session_id",
            "expert_manifest_sha256",
            "group_sequence",
            "parent",
            "parent_output_count",
            "first_expert_seq",
            "prior_expert_record_sha256",
            "prior_expert_state_sha256",
            "records",
            "trace_steps",
            "final_expert_record_sha256",
            "post_expert_state_sha256",
            "post_trace_sha256",
            "group_sha256",
        ),
        "ExpertSessionTerminalV1": (
            "schema_version",
            "session_id",
            "expert_manifest_sha256",
            "provider_request_binding_sha256",
            "match_binding_universe_sha256",
            "retention_binding_sha256",
            "evidence_terminal_ingest_seq",
            "evidence_terminal_record_sha256",
            "evidence_terminal_clean",
            "evidence_terminal_reason",
            "evidence_raw_count",
            "evidence_derived_count",
            "expert_group_count",
            "expert_record_count",
            "last_parent_ingest_seq",
            "last_parent_record_sha256",
            "final_expert_seq",
            "final_expert_record_sha256",
            "final_expert_state_sha256",
            "final_expert_trace_sha256",
            "clean",
            "reason",
            "research_evaluable",
            "terminal_sha256",
        ),
        "DurableExpertAppendReceiptV1": (
            "session_id",
            "group_sequence",
            "group_sha256",
            "last_parent_record_sha256",
            "last_expert_seq",
            "final_expert_record_sha256",
            "post_expert_state_sha256",
            "post_expert_trace_sha256",
            "durable_end_offset",
        ),
        "DurableExpertTerminalReceiptV1": (
            "session_id",
            "terminal_sha256",
            "terminal_frame_sequence",
            "durable_end_offset",
            "reserve_already_consumed",
        ),
        "DurableExpertEmergencyReceiptV1": (
            "session_id",
            "group_receipt",
            "terminal_receipt",
            "reserve_already_consumed",
        ),
        "ExpertPurgeReportV1": (
            "due_sessions",
            "evidence_missing_sessions",
            "evidence_replaced_sessions",
            "recovered_markers",
        ),
        "ExpertPhysicalFileIdentityV1": (
            "schema_version",
            "role",
            "device",
            "inode",
            "uid",
            "mode",
            "link_count",
            "size",
            "mtime_ns",
            "ctime_ns",
            "canonical_marker_sha256",
            "file_header_sha256",
            "session_anchor_sha256",
            "identity_sha256",
        ),
        "EvidenceReplayContextV1": (
            "schema_version",
            "session_manifest",
            "session_manifest_sha256",
            "session_start",
            "session_start_record_sha256",
            "replay_result",
            "evidence_terminal",
            "evidence_terminal_record_sha256",
            "evidence_marker_identity",
            "evidence_wal_identity",
        ),
        "RetentionReplayAuthorizationV1": (
            "schema_version",
            "session_id",
            "authorization_sequence",
            "authorized_operation",
            "expected_parent_ingest_seq",
            "evidence_session_manifest_sha256",
            "evidence_session_start_record_sha256",
            "evidence_terminal_record_sha256",
            "expert_manifest_sha256",
            "retention_binding_sha256",
            "provider_request_binding_sha256",
            "permission_artifact_sha256",
            "qualification_artifact_sha256",
            "qualification_trace_sha256",
            "evidence_marker_identity",
            "evidence_wal_identity",
            "companion_marker_identity",
            "companion_journal_identity",
            "common_deadline_ns",
            "final_sampled_wall_ns",
            "authorization_sha256",
        ),
        "ExpertJournalScanSummaryV1": (
            "schema_version",
            "file_size",
            "last_good_offset",
            "last_frame_sequence",
            "group_count",
            "record_count",
            "terminal_clean",
            "issue",
            "journal_valid",
        ),
        "ExpertReplayBeginReadyV1": ("evidence", "manifest"),
        "ExpertReplayDiagnosticFileProofV1": (
            "schema_version",
            "role",
            "entry_present",
            "device",
            "inode",
            "uid",
            "mode",
            "link_count",
            "mtime_ns",
            "ctime_ns",
            "observed_size",
            "observed_prefix_length",
            "observed_prefix_sha256",
            "issue",
            "proof_sha256",
        ),
        "ExpertReplayDiagnosticProofV1": (
            "schema_version",
            "session_id",
            "mismatch",
            "phase1_replay_summary_sha256",
            "file_proofs",
            "companion_scan",
            "common_deadline_ns",
            "final_sampled_wall_ns",
            "acknowledged_parent_count",
            "acknowledged_expert_record_count",
            "proof_sha256",
        ),
        "ExpertReplayAccumulatorV1": (
            "schema_version",
            "manifest",
            "current_environment",
            "evidence",
            "state",
            "cursor",
            "evidence_raw_count",
            "evidence_derived_count",
            "processed_parent_count",
            "last_authorization_sequence",
            "last_authorization_sha256",
            "mismatch",
        ),
        "ExpertReplayResultV1": (
            "state",
            "trace_sha256",
            "evidence_raw_count",
            "evidence_derived_count",
            "expert_group_count",
            "expert_record_count",
            "evidence_exact",
            "companion_valid",
            "terminals_aligned",
            "exact_replay",
            "mismatch",
            "final_authorization_sha256",
            "evaluation_input_eligible",
            "research_evaluable",
        ),
        "ExpertReplayDeniedV1": ("result", "proof"),
    }
)

KNOWN_CONTRACT_DIGESTS = {
    "ArtifactPin": "f00d29eb62269abfde1d968f9e38ec17e86f383cef00f6ea3208edc7a3608e9a",
    "PairedTimeObservation": "bd4915417b76e8e24eceb318e2732b81cde77ef9ebed7c1970752b16c2b2c857",
    "SetScore": "478a5a8e81cc28e4a4c733bd20e82e9e2bd8d58d8c86f2acaa15ea45673973d8",
    "ProviderSnapshot": "aa1a8cfec021fa2723274c5f0e7093f71a8f729ddb6c4491e8adc3affeebb9c3",
    "ProviderPoint": "b57b1ca506232a9a96ba324d6aa2bac33c3875a45a2d9e9ea63d61c7131ab5a7",
    "ProviderLifecycle": "37f4135a0db6910bd2bab78714bbcf809bb0d98cf4e694994c42cc3156048f4e",
    "TennisState": "209fb477203fe7c681548ab73a59a0357addbabc16d08aa07f37d38114d9f049",
    "TennisTransitionResult": "6b1c6c6c27cf3d57226adcce00d26c4c94070248ccc830859f46c48ea6063201",
    "BookLevel": "c24351662ebbd130c09cecccc6e2a5494e72596ba50db6ca1c134772e2b33f0e",
    "BookSnapshot": "15fd5cf7cd29ff63724ea013c502f8a2b3df303f83ae918a9c99fd703732c739",
    "BookDelta": "85a85dfe27c619c63381a92e3777d856796e95e43d915042e4301019722dbbb9",
    "MarketLifecycle": "1916e5b3b9f71f665e6c11c6c290ae492bc71040e274066631f4af75d2ee719f",
    "BookState": "e57048e9fc1b404f11d8995535320d433f08b7cb99edcf8d92666db74c745c52",
    "BookTransitionResult": "daae7db24f381a559135230ab79eb55a71518ca212c792da92bd4b13c56f6084",
    "MatchBinding": "95976ef268640fcb11642c4848e630f7498cfe5c7aedb6eee957685753533ebd",
    "BindingRoute": "edc6512994564863194701b6a93e4fc926c7fa14060ab1eeedca5ec2e402798e",
    "SettlementSemantics": "2f82b165aca7d49311537822035944547cbae969ceec1c2621a234bd986ea5e7",
    "BindingMarketMetadata": "57097e4fc76ce6786cb0a4f3690bbfc7ba216b23cd0f8cd689b380f859a9914c",
    "BindingMetadata": "fc8350502f61e621cfce1d4285bea865e2582edea397acfeb8fd41250a95df8e",
    "BindingReviewDecision": "e1aef905681afb2007c26933306ff844e6943da6f75a7ab7f5f6854d98fc2c55",
    "BindingUniverse": "5a78250912472f4de75970c7249ca671cf3b9d13e7a45595bddab8e13d0bdae3",
    "CausalPointWitness": "a2389e45c3b4c48a15c3c3bc8cdd0428322e598ad2006dc894ef90078c7176d9",
    "PendingBookMove": "08f305b45ac360a074451448c221dae290e76eebe73082d0188b1bac89cb9b3e",
    "TennisSyncCursor": "ff722495c2d8c052ea06a6d6e9a00c354bebf64d5f1f75bb319c2a70c1f04c86",
    "LastSyncEmission": "804d23295a0a0d59d15df346e7727bc8bf4d17c4841284bfab719d9edde20d56",
    "BookSyncCursor": "42111cb7cfde2990d5f4035eac2fda8696bd6aa9f048776548e57a8c4fa1a5d4",
    "SynchronizationSessionState": "c94d961506db12b10b24e47aedfa4811f8b59aef59b53f720d8825220d5d376b",
    "SynchronizationInput": "fdd8686679ea17889eff9f658043819a368facac5b32e0e41b4dc87dd5075ec8",
    "SyncResult": "cf46a905f38634ee752ffe586c393e6bcbed27daee81d1b93e79d7e4059ed89f",
    "SynchronizationTransitionResult": "9efdb0b73fbe4c293b1e6ad6b36d3273038ba6789989c8f84f37c0a9712a1a5b",
    "SyncPolicy": "18cfb6ba371ecc60fe4b0cfe084fc92df65faccb6946daefcde49f6ca0040169",
    "TrustedSnapshot": "6cf8ffe4ad49955a3d1d5189a974fdc9fff35500c1759ab96ce6053fdd9ce3ab",
    "OpportunityFrame": "f7bfb92e80089f754810372c86784bc6d6dbdc645ca1a931f9c81d4426b0fd0c",
    "FairValueEstimate": "ad7aec2df232efa1b8fe4c89900dd3cc3f3c1320e5cd3eb7014fa4a6cb4db8d6",
    "PolicyPathEstimate": "7edfbfc64c669fc47e1580592a0637836d6774756ffdbbe0e1d7199a67f1e9e6",
    "PolicyEstimate": "7a711ade48ac4c14d2a113934009fdddefeb54253452188a8987de6ed780889e",
    "PolicyDecision": "1c6577e07def6902ac3ff14b1b2677a273afb0e7c6e5a9d717538392f988014d",
}


class VocabularyAndShapeTests(unittest.TestCase):
    def test_enum_vocabulary_is_exact_and_domains_are_distinct(self) -> None:
        enum_classes = {
            name: getattr(contracts, name) for name in ENUM_WIRE_VALUES
        }
        for name, expected in ENUM_WIRE_VALUES.items():
            enum_class = enum_classes[name]
            with self.subTest(enum=name):
                self.assertTrue(issubclass(enum_class, str))
                self.assertTrue(issubclass(enum_class, Enum))
                self.assertEqual(
                    {member.name: member.value for member in enum_class},
                    expected,
                )
        self.assertIsNot(PlayerSide, ContractSide)
        self.assertIsNot(PlayerSide, DecisionAction)
        self.assertIsNot(ContractSide, DecisionAction)

    def test_dataclass_field_order_frozen_slots_and_registry_are_exact(self) -> None:
        expected_classes = {
            name: getattr(contracts, name) for name in EXPECTED_FIELD_ORDER
        }
        for name, expected_fields in EXPECTED_FIELD_ORDER.items():
            cls = expected_classes[name]
            with self.subTest(contract=name):
                self.assertTrue(is_dataclass(cls))
                self.assertEqual(tuple(field.name for field in fields(cls)), expected_fields)
                self.assertIn("__slots__", cls.__dict__)
        expected_dataclass_registry = tuple(expected_classes.values())
        expected_enum_registry = tuple(
            getattr(contracts, name) for name in ENUM_WIRE_VALUES
        )
        self.assertEqual(
            contracts._REGISTERED_DATACLASSES,
            expected_dataclass_registry,
        )
        self.assertEqual(
            contracts._REGISTERED_ENUMS,
            expected_enum_registry,
        )
        self.assertEqual(
            {
                value
                for value in vars(contracts).values()
                if (
                    isinstance(value, type)
                    and value.__module__ == contracts.__name__
                    and is_dataclass(value)
                )
            },
            set(expected_dataclass_registry),
        )
        self.assertEqual(
            {
                value
                for value in vars(contracts).values()
                if (
                    isinstance(value, type)
                    and value.__module__ == contracts.__name__
                    and issubclass(value, Enum)
                )
            },
            set(expected_enum_registry),
        )

    def test_timestamp_field_names_expose_one_unambiguous_clock_domain(self) -> None:
        exact_time_names = {
            "wall_ns",
            "monotonic_ns",
            "clock_uncertainty_ns",
            "scheduled_start_wall_ns",
            "source_wall_ns",
            "source_generated_wall_ns",
            "received_monotonic_ns",
            "last_source_wall_ns",
            "last_source_generated_wall_ns",
            "last_received_monotonic_ns",
            "last_clock_uncertainty_ns",
            "blocked_received_monotonic_ns",
            "observed_monotonic_ns",
            "scheduled_close_wall_ns",
            "book_source_wall_ns",
            "book_observed_monotonic_ns",
            "book_clock_uncertainty_ns",
            "lifecycle_source_wall_ns",
            "lifecycle_observed_monotonic_ns",
            "lifecycle_clock_uncertainty_ns",
            "last_executable_move_monotonic_ns",
            "move_observed_monotonic_ns",
            "first_move_monotonic_ns",
            "last_move_monotonic_ns",
            "start_tolerance_ns",
            "artifact_created_wall_ns",
            "membership_captured_wall_ns",
            "reviewed_wall_ns",
            "review_artifact_created_wall_ns",
            "max_score_age_ns",
            "max_book_age_ns",
            "max_lifecycle_age_ns",
            "max_score_book_skew_ns",
            "max_clock_uncertainty_ns",
            "explanation_window_ns",
            "minimum_close_horizon_ns",
        }
        observed = {
            field.name
            for value in all_valid_contracts()
            for field in fields(value)
            if field.name.endswith("_ns")
        }
        self.assertEqual(observed, exact_time_names)
        self.assertNotIn("timestamp", observed)
        self.assertNotIn("time_ns", observed)

    def test_every_contract_rejects_subclasses_before_field_dispatch(self) -> None:
        for value in all_valid_contracts():
            cls = type(value)
            accesses: list[str] = []

            def hostile_getattribute(self: object, name: str) -> object:
                accesses.append(name)
                return super(type(self), self).__getattribute__(name)

            hostile_cls = type(
                f"Hostile{cls.__name__}",
                (cls,),
                {"__getattribute__": hostile_getattribute},
            )
            hostile = object.__new__(hostile_cls)
            with self.subTest(contract=cls.__name__):
                with self.assertRaises(TypeError):
                    cls.__post_init__(hostile)
                self.assertEqual(accesses, [])

    def test_every_contract_is_immutable(self) -> None:
        for value in all_valid_contracts():
            first_field = fields(value)[0].name
            with self.subTest(contract=type(value).__name__):
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, first_field, getattr(value, first_field))


class ExactTypeAndCommonValidationTests(unittest.TestCase):
    def test_every_contract_field_rejects_a_wrong_exact_python_type(self) -> None:
        class StringSubclass(str):
            pass

        for value in all_valid_contracts():
            for field in fields(value):
                current = getattr(value, field.name)
                if isinstance(current, Enum):
                    invalid: object = current.value
                elif type(current) is bool:
                    invalid = 1
                elif type(current) is int:
                    invalid = True
                elif type(current) is Decimal:
                    invalid = float(current)
                elif type(current) is str:
                    invalid = StringSubclass(current)
                elif type(current) is tuple:
                    invalid = list(current)
                elif current is None or is_dataclass(current):
                    invalid = object()
                else:
                    self.fail(
                        f"missing wrong-type fixture for "
                        f"{type(value).__name__}.{field.name}"
                    )
                with self.subTest(
                    contract=type(value).__name__,
                    field=field.name,
                ):
                    with self.assertRaises(TypeError):
                        replace(value, **{field.name: invalid})

    def test_integer_bounds_are_exact_and_bool_is_never_an_integer(self) -> None:
        self.assertEqual(
            json.loads(canonical_expert_bytes(10**256 - 1))["value"],
            10**256 - 1,
        )
        self.assertEqual(
            json.loads(canonical_expert_bytes(-(10**256 - 1)))["value"],
            -(10**256 - 1),
        )
        for value in (True, False):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    PairedTimeObservation(value, 0, 0)
        for value in (10**256, -(10**256), 10**10000):
            with self.subTest(sign=value < 0):
                with self.assertRaises(ExpertContractError):
                    canonical_expert_bytes(value)
        with self.assertRaises(ExpertContractError):
            PairedTimeObservation(-1, 0, 0)

    def test_decimal_fields_require_exact_finite_decimal_never_float(self) -> None:
        with self.assertRaises(TypeError):
            BookLevel(0.5, Decimal("1"))  # type: ignore[arg-type]
        for value in (
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("-Infinity"),
        ):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ExpertContractError):
                    BookLevel(value, Decimal("1"))

    def test_safe_ids_tickers_and_sha256_are_strict(self) -> None:
        for value in ("", "space bad", "https:secret", "HTTP:x", "file:path", "a" * 129):
            with self.subTest(safe_id=value[:20]):
                with self.assertRaises(ExpertContractError):
                    ArtifactPin(value, SHA_A)
        for value in ("bad-lower", "BAD SPACE", "", "A" * 129):
            with self.subTest(ticker=value[:20]):
                with self.assertRaises(ExpertContractError):
                    match_binding(kalshi_event_ticker=value)
        for value in ("A" * 64, "f" * 63, "g" * 64):
            with self.subTest(sha=value[:10]):
                with self.assertRaises(ExpertContractError):
                    ArtifactPin("artifact", value)
        with self.assertRaises(TypeError):
            ArtifactPin(1, SHA_A)  # type: ignore[arg-type]

    def test_tuple_and_nested_contract_types_are_exact(self) -> None:
        with self.assertRaises(TypeError):
            provider_snapshot(completed_sets=[SetScore(6, 4, None, None)])
        with self.assertRaises(TypeError):
            provider_snapshot(completed_sets=(object(),))
        with self.assertRaises(TypeError):
            trusted_snapshot(decision_time=object())
        with self.assertRaises(TypeError):
            book_state(yes_bids=[book_level()])

    def test_optional_fields_reject_wrong_domains(self) -> None:
        for builder, field, value in (
            (provider_snapshot, "winner", "home"),
            (provider_snapshot, "retired_side", ContractSide.YES),
            (provider_snapshot, "tiebreak_first_server", "home"),
            (provider_lifecycle, "server_for_next_point", "home"),
            (tennis_state, "block_reason", SyncReason.BOOK_STALE),
            (fair_value, "calibration_artifact_sha256", 1),
            (fair_value, "abstention_reason", "model_unsupported"),
            (policy_path, "exit_price", 0.5),
            (policy_decision, "ticker", 1),
        ):
            with self.subTest(builder=builder.__name__, field=field):
                with self.assertRaises(TypeError):
                    builder(**{field: value})

    def test_raw_provider_records_require_a_provider_event_id(self) -> None:
        for builder in (
            provider_snapshot,
            provider_point,
            provider_lifecycle,
        ):
            with self.subTest(builder=builder.__name__):
                with self.assertRaises(TypeError):
                    builder(provider_event_id=None)


class TennisContractInvariantTests(unittest.TestCase):
    def test_set_score_is_structural_completed_set_only(self) -> None:
        self.assertEqual(SetScore(1, 0, None, None).games_home, 1)
        self.assertEqual(SetScore(6, 6, 7, 5).tiebreak_points_home, 7)
        for values in (
            (-1, 0, None, None),
            (0, -1, None, None),
            (6, 6, 7, None),
            (6, 6, None, 5),
            (6, 6, -1, 5),
        ):
            with self.subTest(values=values):
                with self.assertRaises(ExpertContractError):
                    SetScore(*values)

    def test_normal_and_tiebreak_score_shapes_are_consistent(self) -> None:
        with self.assertRaises(ExpertContractError):
            provider_snapshot(
                points_home=ScoreValue.ADVANTAGE,
                points_away=ScoreValue.ADVANTAGE,
            )
        with self.assertRaises(ExpertContractError):
            provider_snapshot(
                points_home=ScoreValue.ADVANTAGE,
                points_away=ScoreValue.THIRTY,
            )
        with self.assertRaises(ExpertContractError):
            provider_snapshot(tiebreak_points_home=1)
        with self.assertRaises(ExpertContractError):
            provider_snapshot(tiebreak_first_server=PlayerSide.HOME)
        provider_snapshot(
            in_tiebreak=True,
            points_home=ScoreValue.LOVE,
            points_away=ScoreValue.LOVE,
            tiebreak_points_home=3,
            tiebreak_points_away=2,
            tiebreak_first_server=PlayerSide.AWAY,
        )
        with self.assertRaises(ExpertContractError):
            provider_snapshot(
                in_tiebreak=True,
                points_home=ScoreValue.FIFTEEN,
                tiebreak_first_server=PlayerSide.AWAY,
            )

    def test_status_and_termination_pairings_are_exact(self) -> None:
        provider_snapshot(status=MatchStatus.SCHEDULED, server_for_next_point=None)
        provider_snapshot(status=MatchStatus.SUSPENDED)
        provider_snapshot(
            status=MatchStatus.ENDED,
            termination_kind=TerminationKind.NATURAL,
            winner=PlayerSide.HOME,
            server_for_next_point=None,
        )
        provider_snapshot(
            status=MatchStatus.ENDED,
            termination_kind=TerminationKind.WALKOVER,
            winner=PlayerSide.AWAY,
            server_for_next_point=None,
        )
        provider_snapshot(
            status=MatchStatus.ENDED,
            termination_kind=TerminationKind.RETIREMENT,
            winner=PlayerSide.HOME,
            retired_side=PlayerSide.AWAY,
            server_for_next_point=None,
        )
        provider_snapshot(
            status=MatchStatus.CANCELLED,
            termination_kind=TerminationKind.CANCELLATION,
            server_for_next_point=None,
        )
        invalid = (
            {"status": MatchStatus.LIVE, "winner": PlayerSide.HOME},
            {
                "status": MatchStatus.ENDED,
                "termination_kind": TerminationKind.NONE,
                "server_for_next_point": None,
            },
            {
                "status": MatchStatus.ENDED,
                "termination_kind": TerminationKind.RETIREMENT,
                "winner": PlayerSide.HOME,
                "retired_side": PlayerSide.HOME,
                "server_for_next_point": None,
            },
            {
                "status": MatchStatus.CANCELLED,
                "termination_kind": TerminationKind.CANCELLATION,
                "winner": PlayerSide.HOME,
                "server_for_next_point": None,
            },
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(ExpertContractError):
                    provider_snapshot(**changes)

    def test_raw_provider_accepts_unsupported_but_state_rejects_it(self) -> None:
        self.assertIs(
            provider_snapshot(match_format=MatchFormat.UNSUPPORTED).match_format,
            MatchFormat.UNSUPPORTED,
        )
        provider_point(match_format=MatchFormat.UNSUPPORTED)
        provider_lifecycle(match_format=MatchFormat.UNSUPPORTED)
        with self.assertRaises(ExpertContractError):
            tennis_state(match_format=MatchFormat.UNSUPPORTED)
        with self.assertRaises(ExpertContractError):
            match_binding(match_format=MatchFormat.UNSUPPORTED)

    def test_provider_incremental_revisions_are_strictly_positive(self) -> None:
        for builder in (provider_point, provider_lifecycle):
            with self.subTest(builder=builder.__name__, revision=True):
                with self.assertRaises(TypeError):
                    builder(revision=True)
            for revision in (0, -1):
                with self.subTest(builder=builder.__name__, revision=revision):
                    with self.assertRaises(ExpertContractError):
                        builder(revision=revision)
        self.assertEqual(provider_snapshot(revision=0).revision, 0)

    def test_tennis_state_block_reason_set_and_metadata_are_exact(self) -> None:
        installed = {
            TennisTransitionReason.PROVIDER_EVENT_GAP,
            TennisTransitionReason.PROVIDER_EVENT_STALE,
            TennisTransitionReason.PROVIDER_EVENT_CONFLICT,
            TennisTransitionReason.CORRECTION_EPOCH_STALE,
            TennisTransitionReason.CORRECTION_EPOCH_AHEAD,
            TennisTransitionReason.IDENTITY_MISMATCH,
            TennisTransitionReason.FORMAT_MISMATCH,
            TennisTransitionReason.SOURCE_LINEAGE_MISMATCH,
            TennisTransitionReason.REVISION_DOMAIN_MISMATCH,
            TennisTransitionReason.RECEIVE_TIME_REGRESSION,
            TennisTransitionReason.SERVER_MISMATCH,
            TennisTransitionReason.POINT_WHILE_NOT_LIVE,
            TennisTransitionReason.TERMINAL_ABSORBING,
            TennisTransitionReason.ILLEGAL_LIFECYCLE_TRANSITION,
            TennisTransitionReason.UNSUPPORTED_FORMAT,
            TennisTransitionReason.CORRECTION_EPOCH_NOT_NEWER,
            TennisTransitionReason.CORRECTION_SNAPSHOT_INVALID,
        }
        self.assertEqual(contracts._TENNIS_STATE_INSTALLED_BLOCK_REASONS, frozenset(installed))
        for reason in TennisTransitionReason:
            changes: dict[str, object] = {
                "block_reason": reason,
                "blocked_event_semantic_sha256": SHA_D,
                "blocked_received_monotonic_ns": 93,
            }
            if reason is TennisTransitionReason.PROVIDER_EVENT_GAP:
                changes.update(expected_revision=5, observed_revision=7)
            with self.subTest(reason=reason):
                if reason in installed:
                    tennis_state(**changes)
                else:
                    with self.assertRaises(ExpertContractError):
                        tennis_state(**changes)
        for changes in (
            {"expected_revision": 5},
            {"blocked_event_semantic_sha256": SHA_D},
            {
                "block_reason": TennisTransitionReason.SERVER_MISMATCH,
                "blocked_event_semantic_sha256": SHA_D,
            },
            {
                "block_reason": TennisTransitionReason.PROVIDER_EVENT_GAP,
                "blocked_event_semantic_sha256": SHA_D,
                "blocked_received_monotonic_ns": 93,
            },
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ExpertContractError):
                    tennis_state(**changes)

    def test_state_is_always_complete_and_player_identity_is_ordered(self) -> None:
        with self.assertRaises(ExpertContractError):
            tennis_state(snapshot_complete=False)
        for builder in (provider_snapshot, provider_point, provider_lifecycle, tennis_state):
            with self.subTest(builder=builder.__name__):
                with self.assertRaises(ExpertContractError):
                    builder(away_player_id="player-home")

    def test_transition_exception_boundary_is_closed(self) -> None:
        for reason in (
            TennisTransitionReason.UNSUPPORTED_FORMAT,
            TennisTransitionReason.SNAPSHOT_INVALID,
        ):
            exc = TennisTransitionError(reason)
            self.assertIs(exc.reason, reason)
            self.assertEqual(str(exc), reason.value)
        with self.assertRaises(ExpertContractError):
            TennisTransitionError(TennisTransitionReason.PROVIDER_EVENT_GAP)
        with self.assertRaises(TypeError):
            TennisTransitionError("snapshot_invalid")  # type: ignore[arg-type]
        self.assertEqual(str(TennisStateInvariantError()), "tennis_state_invariant_error")


class BookTransitionContractTests(unittest.TestCase):
    def test_book_transition_valid_forms_are_exact_and_canonical(self) -> None:
        snapshot = book_transition()
        delta_state = book_state(
            book_observed_monotonic_ns=92,
            last_executable_move=Decimal("0.10"),
            last_executable_move_monotonic_ns=92,
            last_snapshot_sha256=SHA_D,
            last_event_sha256=SHA_E,
        )
        delta = book_transition(
            state=delta_state,
            accepted_event_kind=BookEventKind.DELTA,
            accepted_event_sha256=SHA_E,
            executable_move=Decimal("0.10"),
            move_observed_monotonic_ns=92,
            top_of_book_changed=True,
        )
        lifecycle_state = book_state(
            trusted=False,
            sequence_gap=True,
            last_event_sha256=SHA_F,
        )
        lifecycle = book_transition(
            state=lifecycle_state,
            accepted_event_kind=BookEventKind.LIFECYCLE,
            accepted_event_sha256=SHA_F,
            executable_move=Decimal("0"),
            move_observed_monotonic_ns=None,
            top_of_book_changed=False,
        )
        suppressed = book_transition(
            state=lifecycle_state,
            accepted_event_kind=None,
            accepted_event_sha256=None,
            executable_move=Decimal("0"),
            move_observed_monotonic_ns=None,
            top_of_book_changed=False,
        )
        for value in (snapshot, delta, lifecycle, suppressed):
            with self.subTest(kind=value.accepted_event_kind):
                self.assertEqual(
                    decode_canonical_contract(canonical_expert_bytes(value)),
                    value,
                )

    def test_book_transition_field_validation_order_is_exact(self) -> None:
        cases = (
            ({"state": object()}, TypeError, "state"),
            (
                {"accepted_event_kind": ContractSide.YES},
                TypeError,
                "accepted_event_kind",
            ),
            ({"accepted_event_sha256": 7}, TypeError, "accepted_event_sha256"),
            (
                {"accepted_event_sha256": "A" * 64},
                ExpertContractError,
                "accepted_event_sha256",
            ),
            ({"executable_move": 0.1}, TypeError, "executable_move"),
            (
                {"executable_move": Decimal("NaN")},
                ExpertContractError,
                "executable_move",
            ),
            (
                {"move_observed_monotonic_ns": True},
                TypeError,
                "move_observed_monotonic_ns",
            ),
            ({"connection_epoch": True}, TypeError, "connection_epoch"),
            ({"sequence": 0}, ExpertContractError, "sequence"),
            ({"top_of_book_changed": 1}, TypeError, "top_of_book_changed"),
        )
        for changes, error, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(error, f"^{message}$"):
                    book_transition(**changes)

    def test_book_transition_cross_field_precedence_is_exact(self) -> None:
        trusted = book_transition().state
        gapped = book_state(
            trusted=False,
            sequence_gap=True,
            last_executable_move=Decimal("0.10"),
            last_executable_move_monotonic_ns=90,
        )
        cases = (
            (
                {
                    "accepted_event_kind": None,
                    "accepted_event_sha256": SHA_D,
                },
                "accepted_event",
            ),
            ({"connection_epoch": 2, "sequence": 8}, "connection_epoch"),
            ({"sequence": 8, "executable_move": Decimal("0.2")}, "sequence"),
            (
                {
                    "state": gapped,
                    "accepted_event_kind": BookEventKind.DELTA,
                    "accepted_event_sha256": SHA_F,
                    "executable_move": Decimal("0.20"),
                    "move_observed_monotonic_ns": 91,
                    "top_of_book_changed": True,
                },
                "accepted_event_state",
            ),
            (
                {
                    "state": trusted,
                    "accepted_event_sha256": SHA_F,
                    "executable_move": Decimal("0.20"),
                    "top_of_book_changed": True,
                },
                "event_move",
            ),
            (
                {
                    "state": trusted,
                    "accepted_event_sha256": SHA_F,
                },
                "accepted_event_sha256",
            ),
        )
        for changes, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ExpertContractError, f"^{message}$"):
                    book_transition(**changes)

    def test_snapshot_delta_and_lifecycle_specific_invariants_are_exact(self) -> None:
        state = book_transition().state
        lifecycle_state = book_state(
            trusted=False,
            sequence_gap=True,
            last_event_sha256=SHA_E,
        )
        cases = (
            (
                {
                    "accepted_event_kind": None,
                    "accepted_event_sha256": None,
                    "move_observed_monotonic_ns": 91,
                },
                "event_move",
            ),
            (
                {
                    "state": lifecycle_state,
                    "accepted_event_kind": BookEventKind.LIFECYCLE,
                    "accepted_event_sha256": SHA_E,
                    "move_observed_monotonic_ns": 91,
                },
                "event_move",
            ),
            (
                {
                    "state": lifecycle_state,
                    "accepted_event_kind": BookEventKind.LIFECYCLE,
                    "accepted_event_sha256": SHA_F,
                    "move_observed_monotonic_ns": None,
                },
                "accepted_event_sha256",
            ),
            (
                {
                    "state": state,
                    "accepted_event_kind": BookEventKind.DELTA,
                    "accepted_event_sha256": SHA_D,
                },
                None,
            ),
            (
                {
                    "state": book_state(
                        book_observed_monotonic_ns=91,
                        last_executable_move=Decimal("0"),
                        last_executable_move_monotonic_ns=91,
                        last_snapshot_sha256=SHA_E,
                        last_event_sha256=SHA_D,
                    ),
                    "accepted_event_kind": BookEventKind.SNAPSHOT,
                    "accepted_event_sha256": SHA_D,
                },
                "accepted_event_sha256",
            ),
        )
        for changes, message in cases:
            with self.subTest(message=message):
                if message is None:
                    self.assertIsInstance(
                        book_transition(**changes),
                        BookTransitionResult,
                    )
                else:
                    with self.assertRaisesRegex(
                        ExpertContractError,
                        f"^{message}$",
                    ):
                        book_transition(**changes)

    def test_transition_move_time_and_changed_flag_must_match_state(self) -> None:
        cases = (
            {"executable_move": Decimal("0.01"), "top_of_book_changed": True},
            {"move_observed_monotonic_ns": 90},
            {
                "state": book_state(
                    book_observed_monotonic_ns=91,
                    last_executable_move=Decimal("0"),
                    last_executable_move_monotonic_ns=90,
                    last_snapshot_sha256=SHA_D,
                    last_event_sha256=SHA_D,
                )
            },
            {"top_of_book_changed": True},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "^event_move$",
                ):
                    book_transition(**changes)

    def test_hostile_canonical_cross_field_mutations_match_constructor(self) -> None:
        encoded = canonical_expert_bytes(book_transition())
        gapped_state = book_state(
            trusted=False,
            sequence_gap=True,
            book_observed_monotonic_ns=91,
            last_executable_move=Decimal("0"),
            last_executable_move_monotonic_ns=91,
            last_snapshot_sha256=SHA_D,
            last_event_sha256=SHA_D,
        )
        mutation_cases = (
            (
                "state",
                json.loads(canonical_expert_bytes(gapped_state))["value"],
                ExpertContractError,
                "accepted_event_state",
            ),
            (
                "accepted_event_kind",
                json.loads(canonical_expert_bytes(ContractSide.YES))["value"],
                TypeError,
                "accepted_event_kind",
            ),
            (
                "accepted_event_sha256",
                None,
                ExpertContractError,
                "accepted_event",
            ),
            ("connection_epoch", 2, "connection_epoch"),
            ("sequence", 8, "sequence"),
            ("executable_move", {"$decimal": "0.1"}, "event_move"),
            ("move_observed_monotonic_ns", 90, "event_move"),
            ("top_of_book_changed", True, "event_move"),
            ("accepted_event_sha256", SHA_E, "accepted_event_sha256"),
        )
        for mutation in mutation_cases:
            if len(mutation) == 3:
                field_name, replacement, message = mutation
                error = ExpertContractError
            else:
                field_name, replacement, error, message = mutation
            document = json.loads(encoded)
            document["value"]["fields"][field_name] = replacement
            hostile = json.dumps(
                document,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
            with self.subTest(field=field_name):
                with self.assertRaisesRegex(
                    error,
                    f"^{message}$",
                ):
                    decode_canonical_contract(hostile)

    def test_canonical_snapshot_delta_collision_precedence_is_exact(self) -> None:
        trusted_results = (
            book_transition(),
            book_transition(
                state=book_state(
                    book_observed_monotonic_ns=92,
                    last_executable_move=Decimal("0.10"),
                    last_executable_move_monotonic_ns=92,
                    last_snapshot_sha256=SHA_D,
                    last_event_sha256=SHA_E,
                ),
                accepted_event_kind=BookEventKind.DELTA,
                accepted_event_sha256=SHA_E,
                executable_move=Decimal("0.10"),
                move_observed_monotonic_ns=92,
                top_of_book_changed=True,
            ),
        )
        for valid in trusted_results:
            with self.subTest(
                kind=valid.accepted_event_kind,
                collision="gapped_state_move_time_digest",
            ):
                document = json.loads(canonical_expert_bytes(valid))
                gapped_state = replace(
                    valid.state,
                    trusted=False,
                    sequence_gap=True,
                )
                fields_document = document["value"]["fields"]
                fields_document["state"] = json.loads(
                    canonical_expert_bytes(gapped_state)
                )["value"]
                fields_document["accepted_event_sha256"] = SHA_F
                fields_document["executable_move"] = {"$decimal": "0.2"}
                fields_document["move_observed_monotonic_ns"] = 90
                fields_document["top_of_book_changed"] = False
                hostile = json.dumps(
                    document,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("ascii")
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "^accepted_event_state$",
                ):
                    decode_canonical_contract(hostile)

            with self.subTest(
                kind=valid.accepted_event_kind,
                collision="trusted_latest_move_time_and_digest",
            ):
                document = json.loads(canonical_expert_bytes(valid))
                mismatched_state = replace(
                    valid.state,
                    last_executable_move=Decimal("0.2"),
                    last_executable_move_monotonic_ns=90,
                )
                fields_document = document["value"]["fields"]
                fields_document["state"] = json.loads(
                    canonical_expert_bytes(mismatched_state)
                )["value"]
                fields_document["accepted_event_sha256"] = SHA_F
                hostile = json.dumps(
                    document,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("ascii")
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "^event_move$",
                ):
                    decode_canonical_contract(hostile)

    def test_book_transition_canonical_bytes_and_digest_are_known(self) -> None:
        encoded = canonical_expert_bytes(book_transition())
        self.assertEqual(len(encoded), 1563)
        self.assertEqual(
            hashlib.sha256(encoded).hexdigest(),
            "781632df95aa4b5bfe65162add12424e692e696c1ec036d06ee83297e869c03c",
        )
        self.assertEqual(
            expert_contract_sha256(book_transition()),
            "daae7db24f381a559135230ab79eb55a71518ca212c792da92bd4b13c56f6084",
        )


class BookBindingAndSynchronizationInvariantTests(unittest.TestCase):
    def test_book_ladders_are_strictly_descending_unique_and_not_crossed(self) -> None:
        for changes in (
            {"yes_bids": (book_level("0.40"), book_level("0.45"))},
            {"yes_bids": (book_level("0.45"), book_level("0.45"))},
            {"yes_bids": (book_level("0.60"),), "no_bids": (book_level("0.50"),)},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ExpertContractError):
                    book_snapshot(**changes)
        BookSnapshot(
            "MATCH-HOME",
            0,
            1,
            MarketStatus.OPEN,
            1_000,
            0,
            0,
            0,
            (),
            (),
        )

    def test_book_prices_quantities_sequences_and_trust_are_bounded(self) -> None:
        for values in (("-0.01", "1"), ("1.01", "1"), ("0.5", "0"), ("0.5", "-1")):
            with self.subTest(values=values):
                with self.assertRaises(ExpertContractError):
                    book_level(*values)
        for builder in (book_snapshot, book_delta, book_state):
            with self.subTest(builder=builder.__name__):
                with self.assertRaises(ExpertContractError):
                    builder(sequence=0)
        self.assertEqual(book_delta(quantity=Decimal("0")).quantity, Decimal("0"))
        with self.assertRaises(ExpertContractError):
            book_delta(quantity=Decimal("-0.01"))
        with self.assertRaises(ExpertContractError):
            book_state(trusted=False)
        with self.assertRaises(ExpertContractError):
            book_state(sequence_gap=True)
        book_state(trusted=False, sequence_gap=True)
        with self.assertRaises(ExpertContractError):
            book_state(last_executable_move_monotonic_ns=92)

    def test_binding_orientation_is_exact_for_four_cases(self) -> None:
        binding = match_binding()
        expected = {
            ("MATCH-HOME", ContractSide.YES): PlayerSide.HOME,
            ("MATCH-HOME", ContractSide.NO): PlayerSide.AWAY,
            ("MATCH-AWAY", ContractSide.YES): PlayerSide.AWAY,
            ("MATCH-AWAY", ContractSide.NO): PlayerSide.HOME,
        }
        for args, player_side in expected.items():
            with self.subTest(args=args):
                self.assertIs(player_side_for_contract(binding, *args), player_side)
        with self.assertRaisesRegex(ExpertContractError, "contract_mismatch"):
            player_side_for_contract(binding, "OTHER", ContractSide.YES)
        for args in (
            (object(), "MATCH-HOME", ContractSide.YES),
            (binding, 1, ContractSide.YES),
            (binding, "MATCH-HOME", PlayerSide.HOME),
        ):
            with self.subTest(args=args):
                with self.assertRaises(TypeError):
                    player_side_for_contract(*args)  # type: ignore[arg-type]

    def test_binding_preserves_provider_and_canonical_match_identity(self) -> None:
        binding = match_binding()
        self.assertEqual(binding.provider_match_id, "provider-match-1")
        self.assertEqual(binding.canonical_match_id, "canonical-match-9")
        coincident_text = match_binding(
            provider_match_id="same-match-text",
            canonical_match_id="same-match-text",
        )
        self.assertEqual(coincident_text.provider_match_id, "same-match-text")
        self.assertEqual(coincident_text.canonical_match_id, "same-match-text")
        projected = json.loads(canonical_expert_bytes(coincident_text))["value"][
            "fields"
        ]
        self.assertEqual(projected["provider_match_id"], "same-match-text")
        self.assertEqual(projected["canonical_match_id"], "same-match-text")
        for changes in (
            {"provider_away_player_id": "player-home"},
            {"away_market_ticker": "MATCH-HOME"},
            {"artifact_created_wall_ns": 101},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ExpertContractError):
                    match_binding(**changes)

    def test_sync_policy_strict_durations_and_probability_threshold(self) -> None:
        for field in (
            "max_score_age_ns",
            "max_book_age_ns",
            "max_lifecycle_age_ns",
            "explanation_window_ns",
        ):
            with self.subTest(field=field):
                with self.assertRaises(ExpertContractError):
                    sync_policy(**{field: 0})
        for value in (Decimal("-0.1"), Decimal("1.1")):
            with self.assertRaises(ExpertContractError):
                sync_policy(large_book_move_threshold=value)

    def test_trusted_snapshot_requires_live_synchronized_open_executable_graph(self) -> None:
        for changes in (
            {"tennis": tennis_state(block_reason=TennisTransitionReason.SERVER_MISMATCH,
                                     blocked_event_semantic_sha256=SHA_D,
                                     blocked_received_monotonic_ns=91)},
            {"tennis": tennis_state(status=MatchStatus.SUSPENDED)},
            {"tennis": tennis_state(server_for_next_point=None)},
            {"book": book_state(trusted=False, sequence_gap=True)},
            {"book": book_state(market_status=MarketStatus.SUSPENDED)},
            {"book": book_state(yes_bids=(), no_bids=())},
            {"decision_time": paired_time(monotonic_ns=89)},
            {"decision_time": paired_time(wall_ns=1_000)},
            {"causal_provider_revision": 5},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ExpertContractError):
                    trusted_snapshot(**changes)

    def test_trusted_book_is_executable_with_either_ladder(self) -> None:
        trusted_snapshot(book=book_state(yes_bids=(), no_bids=(book_level("0.50"),)))
        trusted_snapshot(book=book_state(yes_bids=(book_level("0.45"),), no_bids=()))
        trusted_snapshot(book=book_state())
        with self.assertRaises(ExpertContractError):
            trusted_snapshot(book=book_state(yes_bids=(), no_bids=()))

    def test_trusted_snapshot_rejects_identity_lineage_format_or_ticker_drift(self) -> None:
        for changes in (
            {"binding": match_binding(provider_match_id="different")},
            {"binding": match_binding(provider_home_player_id="different")},
            {"binding": match_binding(provider_source_id="different")},
            {"binding": match_binding(revision_domain_id="different")},
            {"binding": match_binding(source_lineage_sha256=SHA_B)},
            {
                "binding": match_binding(
                    match_format=MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS
                )
            },
            {"binding": match_binding(scheduled_start_wall_ns=99)},
            {"book": book_state(ticker="OTHER")},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ExpertContractError):
                    trusted_snapshot(**changes)

    def test_trusted_snapshot_never_compares_wall_and_monotonic_domains(self) -> None:
        tennis = tennis_state(last_received_monotonic_ns=9_000)
        book = book_state(
            scheduled_close_wall_ns=20_000,
            book_observed_monotonic_ns=9_001,
            lifecycle_observed_monotonic_ns=9_002,
            last_executable_move_monotonic_ns=9_000,
        )
        value = trusted_snapshot(
            tennis=tennis,
            book=book,
            decision_time=paired_time(wall_ns=10_000, monotonic_ns=9_003),
        )
        self.assertEqual(value.decision_time.wall_ns, 10_000)
        self.assertEqual(value.decision_time.monotonic_ns, 9_003)


class OpportunityModelAndDecisionInvariantTests(unittest.TestCase):
    def test_opportunity_repeats_exact_embedded_snapshot_identity(self) -> None:
        value = opportunity()
        self.assertEqual(value.canonical_match_id, "canonical-match-9")
        self.assertEqual(value.snapshot.tennis.provider_match_id, "provider-match-1")
        for field, invalid in (
            ("canonical_match_id", "provider-match-1"),
            ("ticker", "MATCH-AWAY"),
            ("decision_sequence", 2),
            ("decision_time", paired_time(wall_ns=201)),
            ("binding_sha256", SHA_A),
            ("provider_revision", 5),
            ("book_connection_epoch", 2),
            ("book_sequence", 8),
            ("snapshot_sha256", SHA_A),
            ("opportunity_id", SHA_A),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ExpertContractError):
                    opportunity(**{field: invalid})

    def test_fair_value_probability_support_and_abstention_are_consistent(self) -> None:
        for changes in (
            {"lower_probability": Decimal("0.61")},
            {"upper_probability": Decimal("0.59")},
            {"fair_probability": Decimal("1.1")},
            {"supported": True, "abstention_reason": DecisionReason.MODEL_UNSUPPORTED},
            {"supported": False, "abstention_reason": None},
            {"supported": False, "abstention_reason": DecisionReason.EDGE_BELOW_COST},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ExpertContractError):
                    fair_value(**changes)
        for reason in (
            DecisionReason.MODEL_UNSUPPORTED,
            DecisionReason.MODEL_UNCERTAIN,
            DecisionReason.MODEL_OUT_OF_DISTRIBUTION,
        ):
            fair_value(
                supported=False,
                calibration_artifact_sha256=None,
                abstention_reason=reason,
            )

    def test_policy_path_and_estimate_arithmetic_invariants(self) -> None:
        for changes in (
            {"residual_quantity": Decimal("3")},
            {"filled_quantity": Decimal("-1")},
            {"probability": Decimal("1.1")},
            {"exit_price": Decimal("-0.1")},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ExpertContractError):
                    policy_path(**changes)
        with self.assertRaises(ExpertContractError):
            policy_estimate(quantity=Decimal("0"))
        with self.assertRaises(ExpertContractError):
            policy_estimate(executable_quantity=Decimal("3"))
        with self.assertRaises(ExpertContractError):
            policy_estimate(lower_expected_net_pnl=Decimal("0.11"))
        with self.assertRaises(ExpertContractError):
            policy_estimate(paths=(policy_path(path_id="z"), policy_path(path_id="a")))
        with self.assertRaises(ExpertContractError):
            policy_estimate(paths=(policy_path(), policy_path()))
        with self.assertRaises(ExpertContractError):
            policy_estimate(paths=(policy_path(filled_quantity=Decimal("3")),))
        with self.assertRaises(ExpertContractError):
            policy_estimate(probability_tolerance=Decimal("0.0000000000011"))
        unsupported = policy_estimate(
            paths=(),
            expected_net_pnl=Decimal("0"),
            lower_expected_net_pnl=Decimal("0"),
            upper_expected_net_pnl=Decimal("0"),
            supported=False,
            abstention_reason=DecisionReason.MODEL_UNSUPPORTED,
        )
        self.assertEqual(unsupported.executable_quantity, Decimal("2"))
        with self.assertRaises(ExpertContractError):
            replace(unsupported, expected_net_pnl=Decimal("0.01"))

    def test_supported_policy_expected_value_uses_private_decimal_context(self) -> None:
        paths = (
            policy_path(
                path_id="lose",
                probability=Decimal("0.4"),
                net_pnl=Decimal("-0.2"),
            ),
            policy_path(
                path_id="win",
                probability=Decimal("0.6"),
                net_pnl=Decimal("0.3"),
            ),
        )
        expected = Decimal("0.10")
        baseline = policy_estimate(paths=paths, expected_net_pnl=expected)
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            context.traps[FloatOperation] = True
            context.traps[Clamped] = True
            changed = policy_estimate(paths=paths, expected_net_pnl=expected)
        self.assertEqual(changed, baseline)
        self.assertEqual(
            expert_contract_sha256(changed),
            expert_contract_sha256(baseline),
        )

    def test_policy_aggregate_decimal_signals_are_contract_errors(self) -> None:
        huge = Decimal("1E+1000001")
        paths = (
            policy_path(
                path_id="huge-a",
                probability=Decimal("0.5"),
                net_pnl=huge,
            ),
            policy_path(
                path_id="huge-b",
                probability=Decimal("0.5"),
                net_pnl=huge,
            ),
        )
        before = getcontext().copy()
        with self.assertRaises(ExpertContractError) as caught:
            policy_estimate(
                paths=paths,
                expected_net_pnl=huge,
                lower_expected_net_pnl=huge,
                upper_expected_net_pnl=huge,
            )
        self.assertIsNone(caught.exception.__cause__)
        after = getcontext()
        self.assertEqual(after.prec, before.prec)
        self.assertEqual(after.rounding, before.rounding)
        self.assertEqual(after.traps, before.traps)
        self.assertEqual(after.flags, before.flags)

    def test_policy_decision_action_reason_and_order_authority_are_closed(self) -> None:
        buy_reasons = {
            DecisionReason.CONSERVATIVE_VALUE_POSITIVE,
            DecisionReason.BASELINE_SIGNAL_POSITIVE,
            DecisionReason.SIMPLE_SCORE_VALUE_POSITIVE,
        }
        sell_reasons = {
            DecisionReason.FAIR_VALUE_CONVERGED,
            DecisionReason.THESIS_INVALIDATED,
            DecisionReason.POLICY_VALUE_NEGATIVE,
            DecisionReason.HOLDING_HORIZON_REACHED,
            DecisionReason.RISK_EXIT_REQUIRED,
            DecisionReason.MARKET_SUSPENDED_EXIT,
            DecisionReason.SETTLEMENT_EXIT,
            DecisionReason.SHUTDOWN_EXIT,
        }
        for reason in buy_reasons:
            policy_decision(reason=reason)
        for reason in sell_reasons:
            policy_decision(action=DecisionAction.PAPER_SELL, reason=reason)
        for reason in DecisionReason:
            if reason not in buy_reasons | sell_reasons:
                policy_decision(
                    ticker=None,
                    contract_side=None,
                    player_side=None,
                    quantity=Decimal("0"),
                    limit_price=None,
                    action=DecisionAction.ABSTAIN,
                    reason=reason,
                )
        for field, invalid in (
            ("ticker", None),
            ("contract_side", None),
            ("player_side", None),
            ("quantity", Decimal("0")),
            ("limit_price", None),
        ):
            with self.subTest(order_field=field):
                with self.assertRaises(ExpertContractError):
                    policy_decision(**{field: invalid})
        with self.assertRaises(ExpertContractError):
            policy_decision(
                ticker=None,
                contract_side=None,
                player_side=None,
                quantity=Decimal("1"),
                limit_price=None,
                action=DecisionAction.ABSTAIN,
                reason=DecisionReason.EDGE_BELOW_COST,
            )
        with self.assertRaises(ExpertContractError):
            policy_decision(reason=DecisionReason.EDGE_BELOW_COST)
        with self.assertRaises(ExpertContractError):
            policy_decision(
                action=DecisionAction.PAPER_SELL,
                reason=DecisionReason.CONSERVATIVE_VALUE_POSITIVE,
            )

    def test_decision_paired_time_is_exactly_embedded(self) -> None:
        observation = paired_time(wall_ns=333, monotonic_ns=222)
        decision = policy_decision(decision_time=observation)
        self.assertIs(decision.decision_time, observation)


class CanonicalEncodingTests(unittest.TestCase):
    def test_known_primitive_and_container_vectors(self) -> None:
        vectors = (
            (
                None,
                b'{"canonical_version":1,"domain":"inci-tennis-expert","value":null}',
            ),
            (
                True,
                b'{"canonical_version":1,"domain":"inci-tennis-expert","value":true}',
            ),
            (
                7,
                b'{"canonical_version":1,"domain":"inci-tennis-expert","value":7}',
            ),
            (
                "é",
                b'{"canonical_version":1,"domain":"inci-tennis-expert","value":"\\u00e9"}',
            ),
            (
                Decimal("0.50"),
                b'{"canonical_version":1,"domain":"inci-tennis-expert","value":{"$decimal":"0.5"}}',
            ),
            (
                (1, "x"),
                b'{"canonical_version":1,"domain":"inci-tennis-expert","value":{"$tuple":[1,"x"]}}',
            ),
            (
                [1, "x"],
                b'{"canonical_version":1,"domain":"inci-tennis-expert","value":{"$list":[1,"x"]}}',
            ),
            (
                {"z": 1, "a": 2},
                b'{"canonical_version":1,"domain":"inci-tennis-expert","value":{"$dict":[["a",2],["z",1]]}}',
            ),
        )
        for value, expected in vectors:
            with self.subTest(value=repr(value)):
                self.assertEqual(canonical_expert_bytes(value), expected)

    def test_known_enum_vectors_cover_every_registered_member(self) -> None:
        for enum_name, members in ENUM_WIRE_VALUES.items():
            enum_class = getattr(contracts, enum_name)
            for member_name, wire_value in members.items():
                expected = (
                    '{"canonical_version":1,"domain":"inci-tennis-expert",'
                    f'"value":{{"$enum":"{enum_name}","value":"{wire_value}"}}}}'
                ).encode("ascii")
                with self.subTest(enum=enum_name, member=member_name):
                    self.assertEqual(
                        canonical_expert_bytes(enum_class[member_name]),
                        expected,
                    )

    def test_known_artifact_contract_vector(self) -> None:
        expected = (
            b'{"canonical_version":1,"domain":"inci-tennis-expert","value":'
            b'{"$contract":"ArtifactPin","$version":1,"fields":'
            b'{"artifact_id":"artifact-1","artifact_sha256":"'
            + b"a" * 64
            + b'"}}}'
        )
        self.assertEqual(
            canonical_expert_bytes(ArtifactPin("artifact-1", SHA_A)),
            expected,
        )

    def test_every_registered_dataclass_has_a_stable_typed_vector(self) -> None:
        for value in all_valid_contracts():
            encoded = canonical_expert_bytes(value)
            document = json.loads(encoded)
            projected = document["value"]
            with self.subTest(contract=type(value).__name__):
                self.assertEqual(document["canonical_version"], 1)
                self.assertEqual(document["domain"], "inci-tennis-expert")
                self.assertEqual(projected["$contract"], type(value).__name__)
                self.assertEqual(projected["$version"], 1)
                self.assertEqual(
                    set(projected["fields"]),
                    set(EXPECTED_FIELD_ORDER[type(value).__name__]),
                )
                self.assertEqual(canonical_expert_bytes(value), encoded)
                self.assertEqual(
                    expert_contract_sha256(value),
                    KNOWN_CONTRACT_DIGESTS[type(value).__name__],
                )

    def test_decimal_normalization_is_exact_bounded_and_context_independent(self) -> None:
        expected = {
            Decimal("0.50"): "0.5",
            Decimal("5E-1"): "0.5",
            Decimal("-0.000"): "0",
            Decimal("1000"): "1000",
            Decimal("1E+3"): "1000",
            Decimal("1.2300"): "1.23",
            Decimal("1000E-3"): "1",
        }
        for value, decimal_text in expected.items():
            with self.subTest(value=repr(value)):
                projection = json.loads(canonical_expert_bytes(value))["value"]
                self.assertEqual(projection, {"$decimal": decimal_text})
        original = canonical_expert_bytes(Decimal("1.2300"))
        with localcontext() as context:
            context.prec = 1
            context.rounding = ROUND_DOWN
            context.traps[InvalidOperation] = False
            context.traps[DivisionByZero] = False
            context.traps[Overflow] = False
            self.assertEqual(canonical_expert_bytes(Decimal("1.2300")), original)

    def test_huge_decimal_exponents_are_rejected_before_fixed_format(self) -> None:
        class FormatBombDecimal(Decimal):
            def __format__(self, spec: str) -> str:
                raise AssertionError("format must not run")

        for value in (Decimal("1E+1000000000"), Decimal("1E-1000000000")):
            with self.subTest(value=value.as_tuple().exponent):
                with mock.patch(
                    "builtins.format",
                    side_effect=AssertionError("format must not run"),
                ) as format_call:
                    with self.assertRaises(ExpertContractError):
                        canonical_expert_bytes(value)
                    format_call.assert_not_called()
        with self.assertRaises(TypeError):
            canonical_expert_bytes(FormatBombDecimal("1E+1000000000"))

    def test_canonical_input_registry_rejects_unsupported_values_and_cycles(self) -> None:
        class ForeignEnum(str, Enum):
            VALUE = "value"

        class ArtifactSubclass(ArtifactPin):
            pass

        cyclic_list: list[object] = []
        cyclic_list.append(cyclic_list)
        cyclic_dict: dict[str, object] = {}
        cyclic_dict["self"] = cyclic_dict
        indirect_left: list[object] = []
        indirect_right = {"left": indirect_left}
        indirect_left.append(indirect_right)
        shared: list[object] = [1, 2]
        self.assertEqual(
            canonical_expert_bytes([shared, shared]),
            canonical_expert_bytes([[1, 2], [1, 2]]),
        )
        artifact_subclass = object.__new__(ArtifactSubclass)
        unsupported_types = (
            1.0,
            b"x",
            bytearray(b"x"),
            {1},
            frozenset({1}),
            object(),
            ForeignEnum.VALUE,
            artifact_subclass,
            {"bad": object()},
            {1: "bad"},
        )
        for value in unsupported_types:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(TypeError):
                    canonical_expert_bytes(value)
        semantic_invalid = (
            cyclic_list,
            cyclic_dict,
            indirect_left,
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("1E+300"),
        )
        for value in semantic_invalid:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ExpertContractError):
                    canonical_expert_bytes(value)

    def test_dict_keys_are_type_checked_before_any_comparison(self) -> None:
        comparisons: list[str] = []

        class HostileStringSubclass(str):
            def __lt__(self, other: object) -> bool:
                comparisons.append("called")
                raise AssertionError("hostile comparator executed")

        value = {
            HostileStringSubclass("z"): 1,
            HostileStringSubclass("a"): 2,
        }
        with self.assertRaises(TypeError):
            canonical_expert_bytes(value)
        self.assertEqual(comparisons, [])

    def test_mapping_order_is_irrelevant_and_field_change_changes_digest(self) -> None:
        left = {"z": [1, 2], "a": {"y": Decimal("0.50"), "x": True}}
        right = {"a": {"x": True, "y": Decimal("0.5")}, "z": [1, 2]}
        self.assertEqual(canonical_expert_bytes(left), canonical_expert_bytes(right))
        pin = ArtifactPin("artifact-1", SHA_A)
        equal_pin = ArtifactPin("artifact-1", SHA_A)
        changed_pin = ArtifactPin("artifact-2", SHA_A)
        self.assertEqual(canonical_expert_bytes(pin), canonical_expert_bytes(equal_pin))
        self.assertEqual(expert_contract_sha256(pin), expert_contract_sha256(equal_pin))
        self.assertNotEqual(expert_contract_sha256(pin), expert_contract_sha256(changed_pin))

    def test_expert_contract_digest_known_vector(self) -> None:
        value = ArtifactPin("artifact-1", SHA_A)
        self.assertEqual(
            expert_contract_sha256(value),
            "f00d29eb62269abfde1d968f9e38ec17e86f383cef00f6ea3208edc7a3608e9a",
        )

    def test_opportunity_id_and_policy_decision_digest_known_vectors(self) -> None:
        frame = opportunity()
        self.assertEqual(
            frame.opportunity_id,
            "49cfab7d8100a9d3b1b44965c931758d6d24f864d170fd37a1aef802e35e7f8b",
        )
        decision = policy_decision()
        digest = hashlib.sha256(
            b"INCI-POLICY-DECISION-V1\0" + canonical_expert_bytes(decision)
        ).hexdigest()
        self.assertEqual(
            digest,
            "6bbcb0741d64903f8bbf99c99bebb69b9fa77148bc507f389938e75ad29768b5",
        )

    def test_global_decimal_context_is_not_mutated(self) -> None:
        before = getcontext().copy()
        canonical_expert_bytes(Decimal("0.50"))
        policy_estimate()
        after = getcontext()
        self.assertEqual(after.prec, before.prec)
        self.assertEqual(after.rounding, before.rounding)
        self.assertEqual(after.traps, before.traps)
        self.assertEqual(after.flags, before.flags)


class TestTask4CanonicalContractRed(unittest.TestCase):
    def test_task4_contract_symbols_and_digest_apis_exist(self) -> None:
        required = (
            "BindingRoute",
            "SettlementSemantics",
            "BindingMarketMetadata",
            "BindingMetadata",
            "BindingReviewDecision",
            "BindingUniverse",
            "compute_settlement_projection_sha256",
            "compute_membership_projection_sha256",
            "compute_binding_review_evidence_sha256",
            "canonical_binding_review_artifact_bytes",
            "compute_binding_review_artifact_sha256",
            "compute_binding_universe_sha256",
        )
        self.assertEqual(
            tuple(name for name in required if not hasattr(contracts, name)),
            (),
        )

    def test_task4_digest_function_known_vectors_and_type_precedence(self) -> None:
        universe = binding_universe()
        settlement = universe.metadata[0].markets[0].settlement
        market = universe.metadata[0].markets[0]
        pin = ArtifactPin(
            universe.raw_artifact_id,
            universe.raw_artifact_sha256,
        )
        self.assertEqual(
            settlement.projection_sha256,
            "22e38b09c65b821f8604fa0fa62771e762359c06af9a0a7037c144c2db1d8e55",
        )
        self.assertEqual(
            market.membership_projection_sha256,
            "f9d6755b156f110cb9910cdf2e8a1a123156e5130a68fbe34d90307103fd880a",
        )
        self.assertEqual(
            compute_binding_review_evidence_sha256(
                pin,
                universe.bindings,
                universe.metadata,
            ),
            "4b56f50cdb629e6ee0150361d49d047c4875bef8c49abb7fd87cdd8c4cf1f87a",
        )
        self.assertEqual(
            universe.review.review_artifact_sha256,
            "511b5a785d2a959a5fb50aa661e44f16acc693029143d728e3f8e3642ec10cc4",
        )
        self.assertEqual(
            universe.universe_sha256,
            "f9e63cdd3eed68230d2327de5628b1e6494f5479bbf247767912eb827d38d2c4",
        )
        with self.assertRaisesRegex(TypeError, "^manifest_pin$"):
            compute_binding_review_evidence_sha256(
                object(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "^bindings$"):
            compute_binding_review_evidence_sha256(
                pin,
                object(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "^metadata$"):
            compute_binding_review_evidence_sha256(
                pin,
                universe.bindings,
                object(),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "^manifest_pin$"):
            compute_binding_universe_sha256(
                object(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "^review$"):
            compute_binding_universe_sha256(
                pin,
                object(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
            )

    def test_task4_contract_constructor_precedence_and_review_bytes(self) -> None:
        universe = binding_universe()
        route = universe.metadata[0].authorized_routes[0]
        with self.assertRaises(TypeError):
            BindingRoute.__post_init__(object.__new__(type("R", (BindingRoute,), {})))
        with self.assertRaisesRegex(TypeError, "^player_side$"):
            replace(
                route,
                player_side="home",  # type: ignore[arg-type]
                market_ticker="bad ticker",
                contract_side=ContractSide.NO,
            )
        with self.assertRaisesRegex(ExpertContractError, "^market_ticker$"):
            replace(
                route,
                market_ticker="bad ticker",
                contract_side=ContractSide.NO,
            )
        with self.assertRaisesRegex(ExpertContractError, "^contract_side$"):
            replace(route, contract_side=ContractSide.NO)
        review = universe.review
        raw = canonical_binding_review_artifact_bytes(
            review_artifact_id=review.review_artifact_id,
            review_artifact_created_wall_ns=(
                review.review_artifact_created_wall_ns
            ),
            binding_artifact_id=review.binding_artifact_id,
            binding_artifact_sha256=review.binding_artifact_sha256,
            decision=review.decision,
            reviewer_id=review.reviewer_id,
            reviewed_wall_ns=review.reviewed_wall_ns,
            review_evidence_sha256=review.review_evidence_sha256,
        )
        self.assertEqual(
            raw,
            (
                b'{"schema_version":1,"artifact_id":"binding-review-1",'
                b'"artifact_created_wall_ns":70,'
                b'"binding_artifact_id":"binding-artifact-1",'
                b'"binding_artifact_sha256":"'
                + b"f" * 64
                + b'","decision":"approved","reviewer_id":"reviewer-1",'
                b'"reviewed_wall_ns":60,"review_evidence_sha256":"'
                + review.review_evidence_sha256.encode("ascii")
                + b'"}'
            ),
        )


class Task5ContractInvariantTests(unittest.TestCase):
    def test_task5_scalar_type_and_local_value_precedence(self) -> None:
        with self.assertRaisesRegex(TypeError, "^revision$"):
            causal_point_witness(
                revision=True,
                event_semantic_sha256="bad",
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^first_move_monotonic_ns$",
        ):
            pending_book_move(
                first_move_monotonic_ns=-1,
                last_move_monotonic_ns=0,
                max_magnitude=Decimal("0"),
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^max_magnitude$",
        ):
            pending_book_move(max_magnitude=Decimal("0"))
        with self.assertRaisesRegex(TypeError, "^kind$"):
            synchronization_input(
                kind="clock",  # type: ignore[arg-type]
                ticker="bad ticker",
            )

    def test_pending_move_cross_field_precedence(self) -> None:
        with self.assertRaisesRegex(
            ExpertContractError,
            "^pending_move_order$",
        ):
            pending_book_move(
                first_move_monotonic_ns=92,
                last_move_monotonic_ns=91,
                first_connection_epoch=2,
                last_connection_epoch=1,
                book_connection_epoch_floor=9,
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^pending_move_identity$",
        ):
            pending_book_move(
                first_connection_epoch=2,
                last_connection_epoch=1,
                book_connection_epoch_floor=2,
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^pending_move_floor$",
        ):
            pending_book_move(book_connection_epoch_floor=2)

    def test_tennis_cursor_state_and_witness_bounds(self) -> None:
        with self.assertRaisesRegex(
            ExpertContractError,
            "^tennis_cursor_state$",
        ):
            tennis_sync_cursor(last_state_sha256=SHA_A)
        tennis = tennis_state(
            correction_epoch=1,
            revision=4,
            last_event_semantic_sha256=SHA_D,
            last_received_monotonic_ns=90,
        )
        witness = causal_point_witness()
        cursor = tennis_sync_cursor(
            tennis=tennis,
            last_state_sha256=expert_contract_sha256(tennis),
            last_input_sha256=SHA_A,
            last_point_witness=witness,
        )
        self.assertEqual(cursor.last_point_witness, witness)
        with self.assertRaisesRegex(
            ExpertContractError,
            "^tennis_cursor_state$",
        ):
            replace(cursor, last_state_sha256=SHA_B)
        with self.assertRaisesRegex(
            ExpertContractError,
            "^tennis_cursor_witness$",
        ):
            replace(
                cursor,
                last_point_witness=replace(
                    witness,
                    canonical_match_id="other-match",
                ),
            )

    def test_book_cursor_pending_witness_and_emission_bounds(self) -> None:
        with self.assertRaisesRegex(
            ExpertContractError,
            "^book_cursor_binding$",
        ):
            book_sync_cursor(
                ticker="MATCH-AWAY",
                book=book_state(ticker="MATCH-HOME"),
                last_state_sha256=expert_contract_sha256(
                    book_state(ticker="MATCH-HOME")
                ),
                last_input_sha256=SHA_A,
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^book_cursor_state$",
        ):
            book_sync_cursor(pending_move=pending_book_move())
        book = book_state(
            ticker="MATCH-HOME",
            book_observed_monotonic_ns=95,
        )
        cursor = book_sync_cursor(
            book=book,
            last_state_sha256=expert_contract_sha256(book),
            last_input_sha256=SHA_A,
            pending_move=pending_book_move(),
        )
        self.assertEqual(cursor.pending_move, pending_book_move())
        with self.assertRaisesRegex(
            ExpertContractError,
            "^book_cursor_pending$",
        ):
            replace(
                cursor,
                pending_move=pending_book_move(
                    first_sequence=book.sequence + 1,
                    last_sequence=book.sequence + 1,
                ),
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^book_cursor_witness$",
        ):
            replace(
                cursor,
                causal_point_witness=causal_point_witness(),
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^book_cursor_emission$",
        ):
            replace(
                cursor,
                pending_move=None,
                last_emission=last_sync_emission(
                    book_sequence=book.sequence + 1,
                ),
            )

    def test_session_universe_policy_cursor_and_floor_checks(self) -> None:
        session = synchronization_session_state()
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_session_universe$",
        ):
            replace(session, universe_sha256=SHA_A)
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_session_policy$",
        ):
            replace(
                session,
                policy=sync_policy(universe_sha256=SHA_A),
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_session_cursors$",
        ):
            replace(
                session,
                book_cursors=tuple(reversed(session.book_cursors)),
            )
        first = session.book_cursors[0]
        book = book_state(
            ticker=first.ticker,
            book_observed_monotonic_ns=95,
        )
        hostile = replace(
            first,
            book=book,
            last_state_sha256=expert_contract_sha256(book),
            last_input_sha256=SHA_A,
            pending_move=pending_book_move(
                ticker=first.ticker,
                tennis_correction_epoch_floor=0,
            ),
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_session_cursors$",
        ):
            replace(
                session,
                book_cursors=(hostile, *session.book_cursors[1:]),
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_session_cursors$",
        ):
            replace(
                session,
                decision_sequence=1,
                last_observation=paired_time(),
            )
        populated_book = book_state(
            ticker=session.book_cursors[0].ticker,
        )
        populated_cursor = replace(
            session.book_cursors[0],
            book=populated_book,
            last_state_sha256=expert_contract_sha256(populated_book),
            last_input_sha256=SHA_A,
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_session_cursors$",
        ):
            replace(
                session,
                book_cursors=(
                    populated_cursor,
                    *session.book_cursors[1:],
                ),
            )

    def test_input_shape_and_ticker_disagreement_are_distinct(self) -> None:
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_input_shape$",
        ):
            synchronization_input(provider_event=provider_snapshot())
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_input_shape$",
        ):
            synchronization_input(
                kind=SyncInputKind.TENNIS_ORIGIN,
                ticker="MATCH-HOME",
                provider_event=provider_snapshot(),
            )
        event = book_snapshot(ticker="MATCH-AWAY")
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_input_ticker$",
        ):
            synchronization_input(
                kind=SyncInputKind.BOOK_TRANSITION,
                ticker="MATCH-HOME",
                book_event=event,
            )

    def test_result_and_transition_cross_object_checks(self) -> None:
        with self.assertRaisesRegex(
            ExpertContractError,
            "^sync_result_shape$",
        ):
            sync_result(reason=SyncReason.TRUSTED_SYNCHRONIZED)
        with self.assertRaisesRegex(
            ExpertContractError,
            "^sync_result_shape$",
        ):
            sync_result(snapshot=trusted_snapshot())
        transition = synchronization_transition_result()
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_input$",
        ):
            replace(transition, input_sha256=SHA_A)
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_state$",
        ):
            replace(
                transition,
                state=replace(
                    transition.state,
                    last_observation=paired_time(wall_ns=201),
                ),
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_results$",
        ):
            replace(transition, results=())

    def test_task5_contracts_round_trip_and_digest_mutations(self) -> None:
        values = (
            causal_point_witness(),
            pending_book_move(),
            tennis_sync_cursor(),
            last_sync_emission(),
            book_sync_cursor(),
            synchronization_session_state(),
            synchronization_input(),
            sync_result(),
            synchronization_transition_result(),
        )
        for value in values:
            with self.subTest(contract=type(value).__name__):
                encoded = canonical_expert_bytes(value)
                self.assertEqual(
                    decode_canonical_contract(encoded),
                    value,
                )
        self.assertNotEqual(
            expert_contract_sha256(causal_point_witness()),
            expert_contract_sha256(
                causal_point_witness(revision=5)
            ),
        )
        mutations = (
            (
                pending_book_move(),
                pending_book_move(max_magnitude=Decimal("0.11")),
            ),
            (
                tennis_sync_cursor(),
                tennis_sync_cursor(binding_sha256=SHA_B),
            ),
            (
                last_sync_emission(),
                last_sync_emission(fingerprint_sha256=SHA_B),
            ),
            (
                book_sync_cursor(),
                book_sync_cursor(binding_sha256=SHA_B),
            ),
            (
                synchronization_session_state(),
                synchronization_session_state(
                    last_observation=paired_time(),
                ),
            ),
            (
                synchronization_input(),
                synchronization_input(canonical_match_id="other-match"),
            ),
            (
                sync_result(),
                sync_result(canonical_match_id="other-match"),
            ),
            (
                synchronization_transition_result(),
                synchronization_transition_result(
                    prior_session_sha256=SHA_B,
                ),
            ),
        )
        for original_value, mutated_value in mutations:
            with self.subTest(contract=type(original_value).__name__):
                self.assertNotEqual(
                    expert_contract_sha256(original_value),
                    expert_contract_sha256(mutated_value),
                )
        original = canonical_expert_bytes(pending_book_move())
        with localcontext() as context:
            context.prec = 1
            context.rounding = ROUND_DOWN
            context.traps[InvalidOperation] = False
            context.traps[DivisionByZero] = False
            context.traps[Overflow] = False
            self.assertEqual(
                canonical_expert_bytes(pending_book_move()),
                original,
            )

    def test_pending_match_ticker_time_and_coordinate_bounds(self) -> None:
        book = book_state(
            ticker="MATCH-HOME",
            connection_epoch=2,
            sequence=7,
            book_observed_monotonic_ns=95,
        )
        base = book_sync_cursor(
            book=book,
            last_state_sha256=expert_contract_sha256(book),
            last_input_sha256=SHA_A,
        )
        cases = (
            pending_book_move(canonical_match_id="other-match"),
            pending_book_move(ticker="MATCH-AWAY"),
            pending_book_move(
                first_connection_epoch=2,
                last_connection_epoch=2,
                book_connection_epoch_floor=2,
                first_sequence=8,
                last_sequence=8,
            ),
            pending_book_move(
                first_move_monotonic_ns=96,
                last_move_monotonic_ns=96,
            ),
        )
        for pending in cases:
            with self.subTest(pending=pending):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "^book_cursor_pending$",
                ):
                    replace(base, pending_move=pending)

    def test_session_floor_witness_and_emission_mutation_matrix(self) -> None:
        tennis = tennis_state(
            correction_epoch=1,
            revision=4,
            last_event_semantic_sha256=SHA_D,
            last_received_monotonic_ns=90,
        )
        tennis_cursor_value = tennis_sync_cursor(
            tennis=tennis,
            last_state_sha256=expert_contract_sha256(tennis),
            last_input_sha256=SHA_A,
            last_point_witness=causal_point_witness(),
        )
        book = book_state(
            ticker="MATCH-HOME",
            connection_epoch=1,
            sequence=7,
            book_observed_monotonic_ns=95,
        )
        current_witness = causal_point_witness()
        book_cursor_value = book_sync_cursor(
            book=book,
            last_state_sha256=expert_contract_sha256(book),
            last_input_sha256=SHA_A,
            causal_point_witness=current_witness,
            consumed_point_witness=current_witness,
            last_emission=last_sync_emission(),
        )
        empty = synchronization_session_state()
        book_cursors = tuple(
            book_cursor_value
            if cursor.ticker == "MATCH-HOME"
            else cursor
            for cursor in empty.book_cursors
        )
        observed_empty = replace(
            empty,
            last_observation=paired_time(),
        )
        valid = replace(
            observed_empty,
            decision_sequence=1,
            tennis_cursors=(tennis_cursor_value,),
            book_cursors=book_cursors,
        )
        self.assertEqual(
            valid.book_cursors[-1].causal_point_witness,
            current_witness,
        )

        def hostile_session(
            *,
            tennis_cursor_hostile: TennisSyncCursor = tennis_cursor_value,
            book_cursor_hostile: BookSyncCursor = book_cursor_value,
        ) -> SynchronizationSessionState:
            return replace(
                observed_empty,
                decision_sequence=(
                    1
                    if book_cursor_hostile.last_emission is not None
                    else 0
                ),
                tennis_cursors=(tennis_cursor_hostile,),
                book_cursors=tuple(
                    book_cursor_hostile
                    if cursor.ticker == "MATCH-HOME"
                    else cursor
                    for cursor in empty.book_cursors
                ),
            )

        hostile_witnesses = (
            replace(current_witness, revision=5),
            replace(current_witness, received_monotonic_ns=91),
            replace(current_witness, event_semantic_sha256=SHA_A),
        )
        for witness in hostile_witnesses:
            with self.subTest(witness=witness):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "^synchronization_session_cursors$",
                ):
                    hostile_session(
                        book_cursor_hostile=book_sync_cursor(
                            book=book,
                            last_state_sha256=expert_contract_sha256(
                                book
                            ),
                            last_input_sha256=SHA_A,
                            consumed_point_witness=witness,
                        )
                    )
        old_epoch = replace(current_witness, correction_epoch=0)
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_session_cursors$",
        ):
            hostile_session(
                book_cursor_hostile=book_sync_cursor(
                    book=book,
                    last_state_sha256=expert_contract_sha256(book),
                    last_input_sha256=SHA_A,
                    causal_point_witness=old_epoch,
                    consumed_point_witness=old_epoch,
                )
            )
        for emission in (
            last_sync_emission(provider_revision=5),
            last_sync_emission(
                provider_event_semantic_sha256=SHA_A
            ),
        ):
            with self.subTest(emission=emission):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "^synchronization_session_cursors$",
                ):
                    hostile_session(
                        book_cursor_hostile=book_sync_cursor(
                            book=book,
                            last_state_sha256=expert_contract_sha256(
                                book
                            ),
                            last_input_sha256=SHA_A,
                            last_emission=emission,
                        )
                    )

    def test_pre_and_post_origin_pending_floor_controls(self) -> None:
        empty = synchronization_session_state()
        observed_empty = replace(
            empty,
            last_observation=paired_time(),
        )
        first = empty.book_cursors[-1]
        book = book_state(
            ticker=first.ticker,
            connection_epoch=1,
            sequence=7,
            book_observed_monotonic_ns=95,
        )
        pre_origin = replace(
            first,
            book=book,
            last_state_sha256=expert_contract_sha256(book),
            last_input_sha256=SHA_A,
            pending_move=pending_book_move(
                ticker=first.ticker,
                tennis_correction_epoch_floor=None,
            ),
        )
        replace(
            observed_empty,
            book_cursors=(*empty.book_cursors[:-1], pre_origin),
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_session_cursors$",
        ):
            replace(
                observed_empty,
                book_cursors=(
                    *empty.book_cursors[:-1],
                    replace(
                        pre_origin,
                        pending_move=pending_book_move(
                            ticker=first.ticker,
                            tennis_correction_epoch_floor=0,
                        ),
                    ),
                ),
            )

        tennis = tennis_state(correction_epoch=1)
        installed_tennis = tennis_sync_cursor(
            tennis=tennis,
            last_state_sha256=expert_contract_sha256(tennis),
            last_input_sha256=SHA_A,
        )
        for floor in (None, 2):
            hostile = replace(
                pre_origin,
                pending_move=pending_book_move(
                    ticker=first.ticker,
                    tennis_correction_epoch_floor=floor,
                ),
            )
            with self.subTest(floor=floor):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "^synchronization_session_cursors$",
                ):
                    replace(
                        observed_empty,
                        tennis_cursors=(installed_tennis,),
                        book_cursors=(
                            *empty.book_cursors[:-1],
                            hostile,
                        ),
                    )


class Task5CanonicalContractRed(unittest.TestCase):
    def test_task5_contract_symbols_and_registry_are_present(self) -> None:
        names = (
            "SyncInputKind",
            "CausalPointWitness",
            "PendingBookMove",
            "TennisSyncCursor",
            "LastSyncEmission",
            "BookSyncCursor",
            "SynchronizationSessionState",
            "SynchronizationInput",
            "SyncResult",
            "SynchronizationTransitionResult",
        )
        missing = tuple(
            name
            for name in names
            if not hasattr(contracts, name)
        )
        self.assertEqual(missing, ())


def _task6_capacity(**changes: object):
    values: dict[str, object] = {
        "schema_version": 1,
        "match_binding_universe_sha256": SHA_A,
        "sync_policy_sha256": SHA_B,
        "maximum_output_count": 64,
        "maximum_synchronization_state_bytes": 131_064,
        "maximum_transition_payload_bytes": 131_064,
        "maximum_rejected_payload_bytes": 131_064,
        "maximum_event_payload_bytes": 131_064,
        "maximum_group_payload_area_bytes": 8_388_608,
        "maximum_group_metadata_bytes": 8_388_532,
        "maximum_group_frame_bytes": 16_777_216,
        "maximum_terminal_metadata_bytes": 1_048_576,
        "maximum_terminal_frame_bytes": 1_048_652,
        "emergency_reserve_bytes": 17_825_868,
    }
    values.update(changes)
    values["proof_sha256"] = contracts.compute_expert_capacity_proof_sha256(
        **values
    )
    return contracts.ExpertCapacityProofV1(**values)


def _task6_environment_components():
    structural_pins = tuple(
        contracts.ExpertSchemaPinV1(
            schema_role=role,
            contract_name=contract_name,
            resource_name=resource_name,
            schema_resource_sha256=SHA_A,
        )
        for role, contract_name, resource_name
        in contracts._STRUCTURAL_SCHEMA_SPEC
    )
    structural = contracts.ExpertStructuralSchemaBundleV1(
        schema_version=1,
        pins=structural_pins,
        bundle_sha256=contracts.expert_structural_schema_bundle_sha256(
            schema_version=1,
            pins=structural_pins,
        ),
    )
    event_pins = tuple(
        contracts.ExpertEventSchemaPinV1(
            event_kind=kind,
            event_version=event_version,
            payload_contract_name=contract_name,
            resource_name=resource_name,
            schema_resource_sha256=(
                contracts.expert_event_schema_resource_sha256(kind)
            ),
        )
        for kind, event_version, contract_name, resource_name
        in contracts._EVENT_SCHEMA_SPEC
    )
    event_schemas = contracts.ExpertEventSchemaBundleV1(
        schema_version=1,
        pins=event_pins,
        bundle_sha256=contracts.expert_event_schema_bundle_sha256(
            schema_version=1,
            pins=event_pins,
        ),
    )
    fallback = contracts.ExpertNormalizerPinV1(
        normalizer_id="task6-fallback-v1",
        source_kind="fallback",
        source_id="task6",
        event_type="unregistered",
        event_version=1,
        normalizer_code_sha256=SHA_B,
        normalizer_schema_sha256=SHA_C,
    )
    normalizers = contracts.ExpertNormalizerRegistryV1(
        schema_version=1,
        fallback=fallback,
        entries=(),
        registry_sha256=contracts.expert_normalizer_registry_sha256(
            schema_version=1,
            fallback=fallback,
            entries=(),
        ),
    )
    current = contracts.ExpertCurrentEnvironmentV1(
        schema_version=1,
        phase1_code_sha256=SHA_A,
        phase1_adapter_code_sha256=SHA_B,
        expert_code_sha256=SHA_C,
        io_code_sha256=SHA_D,
        expert_adapter_code_sha256=SHA_E,
        runtime_code_sha256=SHA_F,
        dependency_lock_sha256=SHA_A,
        python_runtime_sha256=SHA_B,
        normalizer_registry_sha256=normalizers.registry_sha256,
        structural_schema_bundle_sha256=structural.bundle_sha256,
        event_schema_bundle_sha256=event_schemas.bundle_sha256,
    )
    return current, normalizers, structural, event_schemas


def _task6_phase1_anchor(
    manifest_sha256: str,
    start_record_sha256: str,
) -> str:
    return hashlib.sha256(
        b"INCI-EXPERT-PHASE1-SESSION-ANCHOR-V1\0"
        + canonical_expert_bytes(
            (manifest_sha256, start_record_sha256)
        )
    ).hexdigest()


def _task6_identity(
    role: str,
    *,
    session_anchor_sha256: str = SHA_C,
):
    marker = role in {"phase1_marker", "expert_marker"}
    values: dict[str, object] = {
        "schema_version": 1,
        "role": role,
        "device": 1,
        "inode": 2,
        "uid": 3,
        "mode": 0o600,
        "link_count": 1,
        "size": 64,
        "mtime_ns": 10,
        "ctime_ns": 11,
        "canonical_marker_sha256": SHA_A if marker else None,
        "file_header_sha256": None if marker else SHA_B,
        "session_anchor_sha256": session_anchor_sha256,
    }
    values["identity_sha256"] = (
        contracts.compute_expert_physical_file_identity_sha256(**values)
    )
    return contracts._create_expert_physical_file_identity_v1(**values)


def _task6_authorization(
    *,
    operation: str,
    sequence: int,
    parent: int | None = None,
    evidence_marker_anchor: str | None = None,
    evidence_wal_anchor: str | None = None,
    companion_marker_anchor: str = SHA_C,
    companion_journal_anchor: str = SHA_C,
):
    evidence_anchor = _task6_phase1_anchor(SHA_A, SHA_B)
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": "11111111-1111-4111-8111-111111111111",
        "authorization_sequence": sequence,
        "authorized_operation": operation,
        "expected_parent_ingest_seq": parent,
        "evidence_session_manifest_sha256": SHA_A,
        "evidence_session_start_record_sha256": SHA_B,
        "evidence_terminal_record_sha256": SHA_C,
        "expert_manifest_sha256": SHA_D,
        "retention_binding_sha256": SHA_E,
        "provider_request_binding_sha256": SHA_F,
        "permission_artifact_sha256": SHA_A,
        "qualification_artifact_sha256": SHA_B,
        "qualification_trace_sha256": SHA_C,
        "evidence_marker_identity": _task6_identity(
            "phase1_marker",
            session_anchor_sha256=(
                evidence_anchor
                if evidence_marker_anchor is None
                else evidence_marker_anchor
            ),
        ),
        "evidence_wal_identity": _task6_identity(
            "phase1_wal",
            session_anchor_sha256=(
                evidence_anchor
                if evidence_wal_anchor is None
                else evidence_wal_anchor
            ),
        ),
        "companion_marker_identity": _task6_identity(
            "expert_marker",
            session_anchor_sha256=companion_marker_anchor,
        ),
        "companion_journal_identity": _task6_identity(
            "expert_journal",
            session_anchor_sha256=companion_journal_anchor,
        ),
        "common_deadline_ns": 100,
        "final_sampled_wall_ns": 99,
    }
    values["authorization_sha256"] = (
        contracts.compute_retention_replay_authorization_sha256(**values)
    )
    return contracts._create_retention_replay_authorization_v1(**values)


def _task6_diagnostic_file(
    role: contracts.ExpertReplayDiagnosticRoleV1,
    *,
    issue: contracts.ExpertReplayDiagnosticIssueV1 = (
        contracts.ExpertReplayDiagnosticIssueV1.ENTRY_REPLACED
    ),
    prefix_sha256: str | None = None,
    entry_present: bool = True,
):
    empty_sha256 = hashlib.sha256(b"").hexdigest()
    values: dict[str, object] = {
        "schema_version": 1,
        "role": role,
        "entry_present": entry_present,
        "device": 1 if entry_present else None,
        "inode": 2 if entry_present else None,
        "uid": 3 if entry_present else None,
        "mode": 0o600 if entry_present else None,
        "link_count": 1 if entry_present else None,
        "mtime_ns": 10 if entry_present else None,
        "ctime_ns": 11 if entry_present else None,
        "observed_size": 5 if entry_present else 0,
        "observed_prefix_length": 0,
        "observed_prefix_sha256": (
            empty_sha256
            if prefix_sha256 is None
            else prefix_sha256
        ),
        "issue": issue,
    }
    values["proof_sha256"] = (
        contracts.compute_expert_replay_diagnostic_file_proof_sha256(
            **values
        )
    )
    return contracts._create_expert_replay_diagnostic_file_proof_v1(
        **values
    )


def _task6_diagnostic_proof(
    mismatch: contracts.ExpertReplayMismatchV1,
    *,
    phase1_summary: str | None = SHA_A,
    file_proofs: tuple[object, ...] = (),
    companion_scan: object | None = None,
    parent_count: int = 0,
    record_count: int = 0,
):
    deadline = (
        mismatch
        is contracts.ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
    )
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": "11111111-1111-4111-8111-111111111111",
        "mismatch": mismatch,
        "phase1_replay_summary_sha256": phase1_summary,
        "file_proofs": file_proofs,
        "companion_scan": companion_scan,
        "common_deadline_ns": 100,
        "final_sampled_wall_ns": 100 if deadline else 99,
        "acknowledged_parent_count": parent_count,
        "acknowledged_expert_record_count": record_count,
    }
    values["proof_sha256"] = (
        contracts.compute_expert_replay_diagnostic_proof_sha256(**values)
    )
    return contracts._create_expert_replay_diagnostic_proof_v1(**values)


def _task6_evidence_context(
    *,
    start_manifest=None,
    replay_result=None,
    terminal: bool = True,
    evidence_marker_anchor: str | None = None,
    evidence_wal_anchor: str | None = None,
    session_id: str | None = None,
):
    from tennis_v1.canonical import canonical_json_bytes
    from tennis_v1.replay_core import ReplayResult
    from tennis_v1.session import (
        canonical_session_manifest_bytes,
        session_manifest_sha256,
    )
    from tennis_v1.state import canonical_state_bytes, initial_state
    from tennis_v1.wal import _control_event
    from tests.tennis_v1.test_events import manifest

    session_manifest = manifest(
        **({} if session_id is None else {"session_id": session_id})
    )
    persisted_manifest = (
        session_manifest if start_manifest is None else start_manifest
    )
    start = _control_event(
        persisted_manifest,
        ingest_seq=1,
        event_type="SESSION_START",
        payload=canonical_session_manifest_bytes(persisted_manifest),
    )
    result = (
        ReplayResult(
            state=initial_state(session_manifest.session_id),
            trace_sha256=SHA_A,
            raw_count=0,
            derived_count=0,
            terminal_clean=True,
            wal_valid=True,
            exact_replay=True,
            scan_issue=None,
            replay_mismatch=None,
        )
        if replay_result is None
        else replay_result
    )
    evidence_terminal = None
    if terminal:
        assert result.state is not None
        payload = canonical_json_bytes(
            {
                "terminal_version": 1,
                "clean": result.terminal_clean,
                "reason": (
                    "operator_stop"
                    if result.terminal_clean
                    else "operator_halt"
                ),
                "trace_sha256": result.trace_sha256,
                "final_state_sha256": hashlib.sha256(
                    canonical_state_bytes(result.state)
                ).hexdigest(),
                "record_count_before_terminal": (
                    1 + result.raw_count + result.derived_count
                ),
                "raw_count": result.raw_count,
                "derived_count": result.derived_count,
                "last_applied_raw_seq": (
                    result.state.last_applied_raw_seq
                ),
                "config_file_sha256": session_manifest.config_file_sha256,
                "config_canonical_sha256": (
                    session_manifest.config_canonical_sha256
                ),
                "code_sha256": session_manifest.code_sha256,
                "session_manifest_sha256": (
                    session_manifest_sha256(session_manifest)
                ),
                "provider_manifest_file_sha256": (
                    session_manifest.provider_manifest_file_sha256
                ),
                "provider_manifest_canonical_sha256": (
                    session_manifest.provider_manifest_canonical_sha256
                ),
                "entitlement_id_sha256": (
                    session_manifest.entitlement_id_sha256
                ),
                "permission_artifact_sha256": (
                    session_manifest.permission_artifact_sha256
                ),
                "qualification_artifact_sha256": (
                    session_manifest.qualification_artifact_sha256
                ),
                "qualification_trace_sha256": (
                    session_manifest.qualification_trace_sha256
                ),
                "adapter_code_sha256": (
                    session_manifest.adapter_code_sha256
                ),
                "auth_contract_sha256": (
                    session_manifest.auth_contract_sha256
                ),
                "quota_contract_sha256": (
                    session_manifest.quota_contract_sha256
                ),
                "required_retention_until_ns": (
                    session_manifest.required_retention_until_ns
                ),
                "research_evaluable": False,
            }
        )
        evidence_terminal = _control_event(
            session_manifest,
            ingest_seq=2 + result.raw_count + result.derived_count,
            event_type="SESSION_HALT",
            payload=payload,
        )
    manifest_sha256 = session_manifest_sha256(session_manifest)
    start_record_sha256 = contracts.canonical_record_sha256(start)
    evidence_anchor = _task6_phase1_anchor(
        manifest_sha256,
        start_record_sha256,
    )
    values = {
        "schema_version": 1,
        "session_manifest": session_manifest,
        "session_manifest_sha256": manifest_sha256,
        "session_start": start,
        "session_start_record_sha256": start_record_sha256,
        "replay_result": result,
        "evidence_terminal": evidence_terminal,
        "evidence_terminal_record_sha256": (
            None
            if evidence_terminal is None
            else contracts.canonical_record_sha256(evidence_terminal)
        ),
        "evidence_marker_identity": _task6_identity(
            "phase1_marker",
            session_anchor_sha256=(
                evidence_anchor
                if evidence_marker_anchor is None
                else evidence_marker_anchor
            ),
        ),
        "evidence_wal_identity": _task6_identity(
            "phase1_wal",
            session_anchor_sha256=(
                evidence_anchor
                if evidence_wal_anchor is None
                else evidence_wal_anchor
            ),
        ),
    }
    return contracts._create_evidence_replay_context_v1(**values)


def _task6_plain_schema_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if type(value) is Decimal:
        return str(value)
    if is_dataclass(value):
        return {
            item.name: _task6_plain_schema_value(
                getattr(value, item.name)
            )
            for item in fields(value)
        }
    if type(value) in (tuple, list):
        return [_task6_plain_schema_value(item) for item in value]
    if type(value) is dict:
        return {
            key: _task6_plain_schema_value(item)
            for key, item in value.items()
        }
    return value


def _task6_schema_matches(
    instance: object,
    schema: dict[str, object],
    root: dict[str, object],
) -> bool:
    reference = schema.get("$ref")
    if reference is not None:
        if (
            type(reference) is not str
            or not reference.startswith("#/$defs/")
        ):
            return False
        target = root.get("$defs", {}).get(
            reference.removeprefix("#/$defs/")
        )
        return (
            type(target) is dict
            and _task6_schema_matches(instance, target, root)
        )
    all_of = schema.get("allOf")
    if all_of is not None and (
        type(all_of) is not list
        or not all(
            type(item) is dict
            and _task6_schema_matches(instance, item, root)
            for item in all_of
        )
    ):
        return False
    one_of = schema.get("oneOf")
    if one_of is not None and (
        type(one_of) is not list
        or sum(
            type(item) is dict
            and _task6_schema_matches(instance, item, root)
            for item in one_of
        )
        != 1
    ):
        return False
    if "const" in schema and instance != schema["const"]:
        return False
    enum = schema.get("enum")
    if enum is not None and (
        type(enum) is not list or instance not in enum
    ):
        return False
    declared_type = schema.get("type")
    if declared_type is not None:
        names = (
            declared_type
            if type(declared_type) is list
            else [declared_type]
        )
        matches_type = {
            "null": lambda: instance is None,
            "boolean": lambda: type(instance) is bool,
            "integer": lambda: type(instance) is int,
            "number": lambda: (
                type(instance) in (int, float)
                and type(instance) is not bool
            ),
            "string": lambda: type(instance) is str,
            "array": lambda: type(instance) is list,
            "object": lambda: type(instance) is dict,
        }
        if not any(
            type(name) is str
            and name in matches_type
            and matches_type[name]()
            for name in names
        ):
            return False
    if type(instance) is str and "pattern" in schema:
        pattern = schema["pattern"]
        if type(pattern) is not str or re.search(pattern, instance) is None:
            return False
    if type(instance) is int and type(instance) is not bool:
        if "minimum" in schema and instance < schema["minimum"]:
            return False
        if "maximum" in schema and instance > schema["maximum"]:
            return False
    if type(instance) is dict:
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if (
            type(required) is not list
            or type(properties) is not dict
            or any(name not in instance for name in required)
        ):
            return False
        if schema.get("additionalProperties") is False and any(
            name not in properties for name in instance
        ):
            return False
        if any(
            name in instance
            and (
                type(child) is not dict
                or not _task6_schema_matches(
                    instance[name],
                    child,
                    root,
                )
            )
            for name, child in properties.items()
        ):
            return False
    if type(instance) is list:
        if "minItems" in schema and len(instance) < schema["minItems"]:
            return False
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            return False
        prefix_items = schema.get("prefixItems", [])
        if type(prefix_items) is not list:
            return False
        for index, child in enumerate(prefix_items):
            if (
                index >= len(instance)
                or type(child) is not dict
                or not _task6_schema_matches(
                    instance[index],
                    child,
                    root,
                )
            ):
                return False
        items = schema.get("items")
        if items is False and len(instance) > len(prefix_items):
            return False
        if type(items) is dict and any(
            not _task6_schema_matches(item, items, root)
            for item in instance[len(prefix_items) :]
        ):
            return False
    return True


class Task6ContractInvariantTests(unittest.TestCase):
    def test_collected_environment_is_private_registered_and_cross_bound(
        self,
    ) -> None:
        self.assertTrue(
            hasattr(contracts, "ExpertCollectedEnvironmentV1")
        )
        collected_type = contracts.ExpertCollectedEnvironmentV1
        self.assertIn(collected_type, contracts._REGISTERED_DATACLASSES)
        current, normalizers, structural, event_schemas = (
            _task6_environment_components()
        )
        with self.assertRaisesRegex(
            TypeError,
            "private construction required",
        ):
            collected_type(
                current=current,
                normalizers=normalizers,
                structural_schemas=structural,
                event_schemas=event_schemas,
            )
        collected = contracts._create_expert_collected_environment_v1(
            current=current,
            normalizers=normalizers,
            structural_schemas=structural,
            event_schemas=event_schemas,
        )
        self.assertIs(collected.current, current)
        with self.assertRaisesRegex(
            ExpertContractError,
            "collected_environment",
        ):
            contracts._create_expert_collected_environment_v1(
                current=replace(
                    current,
                    normalizer_registry_sha256=SHA_F,
                ),
                normalizers=normalizers,
                structural_schemas=structural,
                event_schemas=event_schemas,
            )

    def test_evidence_replay_context_closes_start_replay_and_terminal_shapes(
        self,
    ) -> None:
        from tennis_v1.canonical import canonical_json_bytes
        from tennis_v1.replay_core import ReplayResult
        from tennis_v1.state import initial_state
        from tennis_v1.wal import ScanIssue
        from tests.tennis_v1.test_events import manifest

        valid = _task6_evidence_context()
        self.assertTrue(valid.replay_result.exact_replay)
        ruled_anchor = _task6_phase1_anchor(
            valid.session_manifest_sha256,
            valid.session_start_record_sha256,
        )
        self.assertEqual(
            valid.evidence_marker_identity.session_anchor_sha256,
            ruled_anchor,
        )
        self.assertEqual(
            valid.evidence_wal_identity.session_anchor_sha256,
            ruled_anchor,
        )
        for field_name in (
            "evidence_marker_anchor",
            "evidence_wal_anchor",
        ):
            with self.subTest(identity=field_name):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "evidence_identity",
                ):
                    _task6_evidence_context(**{field_name: SHA_C})

        stateless = ReplayResult(
            state=None,
            trace_sha256=None,
            raw_count=0,
            derived_count=0,
            terminal_clean=False,
            wal_valid=True,
            exact_replay=False,
            scan_issue=ScanIssue.MISSING_TERMINAL,
            replay_mismatch=None,
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "replay_result",
        ):
            _task6_evidence_context(
                replay_result=stateless,
                terminal=False,
            )

        with self.assertRaisesRegex(
            ExpertContractError,
            "session_start",
        ):
            _task6_evidence_context(
                start_manifest=manifest(code_sha256=SHA_F),
            )

        forged_result = ReplayResult(
            state=initial_state(valid.session_manifest.session_id),
            trace_sha256=SHA_A,
            raw_count=99,
            derived_count=88,
            terminal_clean=True,
            wal_valid=True,
            exact_replay=True,
            scan_issue=None,
            replay_mismatch=None,
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "replay_result",
        ):
            _task6_evidence_context(replay_result=forged_result)

        with self.assertRaisesRegex(
            ExpertContractError,
            "evidence_terminal",
        ):
            _task6_evidence_context(terminal=False)

        terminal = valid.evidence_terminal
        assert terminal is not None
        payload = json.loads(terminal.payload)
        payload["raw_count"] = 1
        encoded = canonical_json_bytes(payload)
        changed = replace(
            terminal,
            payload=encoded,
            payload_sha256=hashlib.sha256(encoded).hexdigest(),
        )
        values = {
            item.name: getattr(valid, item.name)
            for item in fields(valid)
        }
        values["evidence_terminal"] = changed
        values["evidence_terminal_record_sha256"] = (
            contracts.canonical_record_sha256(changed)
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "evidence_terminal",
        ):
            contracts._create_evidence_replay_context_v1(**values)

    def test_replay_authorization_operation_sequence_shapes_are_exact(
        self,
    ) -> None:
        valid = _task6_authorization(
            operation="begin",
            sequence=0,
        )
        self.assertEqual(valid.authorization_sequence, 0)
        ruled_anchor = _task6_phase1_anchor(
            valid.evidence_session_manifest_sha256,
            valid.evidence_session_start_record_sha256,
        )
        self.assertEqual(
            valid.evidence_marker_identity.session_anchor_sha256,
            ruled_anchor,
        )
        self.assertEqual(
            valid.evidence_wal_identity.session_anchor_sha256,
            ruled_anchor,
        )
        for operation, sequence, parent in (
            ("begin", 1, None),
            ("parent_group", 0, 7),
            ("finish", 0, None),
        ):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "authorization_sequence",
                ):
                    _task6_authorization(
                        operation=operation,
                        sequence=sequence,
                        parent=parent,
                    )
        for field_name in (
            "evidence_marker_anchor",
            "evidence_wal_anchor",
        ):
            with self.subTest(identity=field_name):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "authorization_identity",
                ):
                    _task6_authorization(
                        operation="begin",
                        sequence=0,
                        **{field_name: SHA_C},
                    )
        with self.assertRaisesRegex(
            ExpertContractError,
            "authorization_identity",
        ):
            _task6_authorization(
                operation="begin",
                sequence=0,
                companion_marker_anchor=SHA_D,
                companion_journal_anchor=SHA_E,
            )

    def test_diagnostic_file_and_aggregate_matrices_are_closed(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ExpertContractError,
            "diagnostic_file_proof",
        ):
            _task6_diagnostic_file(
                contracts.ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
                issue=(
                    contracts.ExpertReplayDiagnosticIssueV1.ENTRY_MISSING
                ),
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "diagnostic_file_proof",
        ):
            _task6_diagnostic_file(
                contracts.ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
                prefix_sha256=SHA_A,
            )

        mismatch = contracts.ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH
        with self.assertRaisesRegex(
            ExpertContractError,
            "diagnostic_file_proofs",
        ):
            _task6_diagnostic_proof(mismatch)
        with self.assertRaisesRegex(
            ExpertContractError,
            "diagnostic_file_proofs",
        ):
            _task6_diagnostic_proof(
                mismatch,
                file_proofs=(
                    _task6_diagnostic_file(
                        contracts.ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
                    ),
                    _task6_diagnostic_file(
                        contracts.ExpertReplayDiagnosticRoleV1.PHASE1_WAL
                    ),
                ),
            )
        for issue, entry_present in (
            (
                contracts.ExpertReplayDiagnosticIssueV1.ENTRY_MISSING,
                False,
            ),
            (
                contracts.ExpertReplayDiagnosticIssueV1.ENTRY_NOT_REGULAR,
                True,
            ),
            (
                contracts.ExpertReplayDiagnosticIssueV1.ENTRY_IDENTITY_INVALID,
                True,
            ),
            (
                contracts.ExpertReplayDiagnosticIssueV1.ENTRY_REPLACED,
                True,
            ),
        ):
            with self.subTest(identity_issue=issue):
                proof = _task6_diagnostic_proof(
                    mismatch,
                    file_proofs=(
                        _task6_diagnostic_file(
                            contracts.ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
                            issue=issue,
                            entry_present=entry_present,
                        ),
                    ),
                )
                self.assertEqual(proof.mismatch, mismatch)
        for issue in (
            contracts.ExpertReplayDiagnosticIssueV1.PREFIX_TRUNCATED,
            contracts.ExpertReplayDiagnosticIssueV1.HEADER_INVALID,
            contracts.ExpertReplayDiagnosticIssueV1.SESSION_START_INVALID,
            contracts.ExpertReplayDiagnosticIssueV1.MANIFEST_FRAME_INVALID,
            contracts.ExpertReplayDiagnosticIssueV1.SCAN_INVALID,
        ):
            with self.subTest(nonidentity_issue=issue):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "diagnostic_file_proofs",
                ):
                    _task6_diagnostic_proof(
                        mismatch,
                        file_proofs=(
                            _task6_diagnostic_file(
                                contracts.ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
                                issue=issue,
                            ),
                        ),
                    )

        with self.assertRaisesRegex(
            ExpertContractError,
            "journal_scan_summary",
        ):
            contracts.ExpertJournalScanSummaryV1(
                schema_version=1,
                file_size=0,
                last_good_offset=0,
                last_frame_sequence=0,
                group_count=0,
                record_count=0,
                terminal_clean=False,
                issue=None,
                journal_valid=True,
            )
        scan = contracts.ExpertJournalScanSummaryV1(
            schema_version=1,
            file_size=0,
            last_good_offset=0,
            last_frame_sequence=0,
            group_count=0,
            record_count=0,
            terminal_clean=True,
            issue=None,
            journal_valid=True,
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "diagnostic_pre_replay",
        ):
            _task6_diagnostic_proof(
                contracts.ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                phase1_summary=None,
                companion_scan=scan,
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "diagnostic_pre_replay",
        ):
            _task6_diagnostic_proof(
                contracts.ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
                phase1_summary=None,
                parent_count=1,
                record_count=1,
            )

    def test_denied_without_phase1_summary_has_zero_inexact_evidence(
        self,
    ) -> None:
        mismatch = (
            contracts.ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH
        )
        proof = _task6_diagnostic_proof(
            mismatch,
            phase1_summary=None,
        )
        invalid = contracts.ExpertReplayResultV1(
            state=None,
            trace_sha256=None,
            evidence_raw_count=0,
            evidence_derived_count=0,
            expert_group_count=0,
            expert_record_count=0,
            evidence_exact=True,
            companion_valid=False,
            terminals_aligned=False,
            exact_replay=False,
            mismatch=mismatch,
            final_authorization_sha256=None,
            evaluation_input_eligible=False,
            research_evaluable=False,
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "replay_denied",
        ):
            contracts._create_expert_replay_denied_v1(
                result=invalid,
                proof=proof,
            )

    def test_replay_accumulator_environment_first_match_matrix(
        self,
    ) -> None:
        from inci_tennis_expert.state import initial_expert_state
        from tests.tennis_v1.test_expert_observation import task6_artifacts

        universe, policy, manifest = task6_artifacts()
        evidence = _task6_evidence_context(
            session_id=manifest.session_id,
        )
        state = initial_expert_state(manifest, universe, policy)
        state_sha256 = contracts.expert_state_sha256(state)
        cursor = contracts.ExpertJournalCursorV1(
            schema_version=1,
            session_id=manifest.session_id,
            group_count=0,
            record_count=0,
            last_parent_ingest_seq=0,
            last_parent_record_sha256=(
                manifest.evidence_session_start_record_sha256
            ),
            expert_seq=0,
            expert_record_sha256=manifest.manifest_sha256,
            expert_state_sha256=state_sha256,
            expert_trace_sha256=contracts.expert_trace_seed_sha256(
                manifest.session_id,
                manifest.manifest_sha256,
                state_sha256,
            ),
        )
        equal_environment = manifest.environment
        unequal_environment = replace(
            equal_environment,
            phase1_code_sha256=SHA_F,
        )
        self.assertNotEqual(equal_environment, unequal_environment)
        contracts.ExpertCurrentEnvironmentV1.__post_init__(
            unequal_environment
        )

        def accumulator(
            mismatch: contracts.ExpertReplayMismatchV1 | None,
            current_environment,
        ):
            return contracts.ExpertReplayAccumulatorV1(
                schema_version=1,
                manifest=manifest,
                current_environment=current_environment,
                evidence=evidence,
                state=state,
                cursor=cursor,
                evidence_raw_count=0,
                evidence_derived_count=0,
                processed_parent_count=0,
                last_authorization_sequence=0,
                last_authorization_sha256=SHA_A,
                mismatch=mismatch,
            )

        earlier_mismatches = (
            contracts.ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
            contracts.ExpertReplayMismatchV1.EVIDENCE_REPLAY_NOT_EXACT,
            contracts.ExpertReplayMismatchV1.EVIDENCE_TERMINAL_NOT_CLEAN,
            contracts.ExpertReplayMismatchV1.EVIDENCE_SESSION_MISMATCH,
            contracts.ExpertReplayMismatchV1.EVIDENCE_MANIFEST_MISMATCH,
            contracts.ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            contracts.ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            contracts.ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
        )
        environment_mismatch = (
            contracts.ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH
        )
        later_mismatches = (
            contracts.ExpertReplayMismatchV1.RETENTION_BINDING_MISMATCH,
            contracts.ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
            contracts.ExpertReplayMismatchV1.COMPANION_MANIFEST_MISMATCH,
            contracts.ExpertReplayMismatchV1.EXPERT_SEQUENCE_MISMATCH,
            contracts.ExpertReplayMismatchV1.PARENT_MISSING,
            contracts.ExpertReplayMismatchV1.PARENT_EXTRA,
            contracts.ExpertReplayMismatchV1.PARENT_ORDER_MISMATCH,
            contracts.ExpertReplayMismatchV1.PARENT_KIND_MISMATCH,
            contracts.ExpertReplayMismatchV1.PARENT_DIGEST_MISMATCH,
            contracts.ExpertReplayMismatchV1.PARENT_GROUP_SHAPE_MISMATCH,
            contracts.ExpertReplayMismatchV1.PRIOR_RECORD_CHAIN_MISMATCH,
            contracts.ExpertReplayMismatchV1.PRIOR_STATE_MISMATCH,
            contracts.ExpertReplayMismatchV1.EVENT_SCHEMA_UNPINNED,
            contracts.ExpertReplayMismatchV1.RECORD_DIGEST_MISMATCH,
            contracts.ExpertReplayMismatchV1.PAYLOAD_DESCRIPTOR_MISMATCH,
            contracts.ExpertReplayMismatchV1.PAYLOAD_BYTES_MISMATCH,
            contracts.ExpertReplayMismatchV1.NORMALIZED_OBSERVATION_MISMATCH,
            contracts.ExpertReplayMismatchV1.REDUCTION_MISMATCH,
            contracts.ExpertReplayMismatchV1.POST_STATE_MISMATCH,
            contracts.ExpertReplayMismatchV1.TRACE_MISMATCH,
            contracts.ExpertReplayMismatchV1.TERMINAL_MISSING,
            contracts.ExpertReplayMismatchV1.TERMINAL_REASON_MISMATCH,
            contracts.ExpertReplayMismatchV1.TERMINAL_COUNT_MISMATCH,
            contracts.ExpertReplayMismatchV1.TERMINAL_PROVENANCE_MISMATCH,
            contracts.ExpertReplayMismatchV1.TERMINAL_STATE_MISMATCH,
            contracts.ExpertReplayMismatchV1.TERMINAL_TRACE_MISMATCH,
        )
        self.assertEqual(
            (
                *earlier_mismatches,
                environment_mismatch,
                *later_mismatches,
            ),
            tuple(contracts.ExpertReplayMismatchV1),
        )

        self.assertIsNone(
            accumulator(None, equal_environment).mismatch
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "replay_accumulator",
        ):
            accumulator(None, unequal_environment)

        for mismatch in earlier_mismatches:
            for current_environment in (
                equal_environment,
                unequal_environment,
            ):
                with self.subTest(
                    mismatch=mismatch,
                    equal=(
                        current_environment == equal_environment
                    ),
                ):
                    self.assertEqual(
                        accumulator(
                            mismatch,
                            current_environment,
                        ).mismatch,
                        mismatch,
                    )

        with self.subTest(
            mismatch=environment_mismatch,
            equal=True,
        ):
            with self.assertRaisesRegex(
                ExpertContractError,
                "replay_accumulator",
            ):
                accumulator(environment_mismatch, equal_environment)
        with self.subTest(
            mismatch=environment_mismatch,
            equal=False,
        ):
            self.assertEqual(
                accumulator(
                    environment_mismatch,
                    unequal_environment,
                ).mismatch,
                environment_mismatch,
            )

        for mismatch in later_mismatches:
            with self.subTest(mismatch=mismatch, equal=True):
                self.assertEqual(
                    accumulator(
                        mismatch,
                        equal_environment,
                    ).mismatch,
                    mismatch,
                )
            with self.subTest(mismatch=mismatch, equal=False):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "replay_accumulator",
                ):
                    accumulator(
                        mismatch,
                        unequal_environment,
                    )

    def test_clean_terminal_reason_exactly_matches_phase1_reason(
        self,
    ) -> None:
        def terminal(
            *,
            evidence_reason: str,
            reason: contracts.ExpertTerminalReasonV1,
            clean: bool = True,
            **changes: object,
        ):
            values: dict[str, object] = {
                "schema_version": 1,
                "session_id": "11111111-1111-4111-8111-111111111111",
                "expert_manifest_sha256": SHA_A,
                "provider_request_binding_sha256": SHA_B,
                "match_binding_universe_sha256": SHA_C,
                "retention_binding_sha256": SHA_D,
                "evidence_terminal_ingest_seq": 2,
                "evidence_terminal_record_sha256": SHA_E,
                "evidence_terminal_clean": clean,
                "evidence_terminal_reason": evidence_reason,
                "evidence_raw_count": 0,
                "evidence_derived_count": 0,
                "expert_group_count": 0,
                "expert_record_count": 0,
                "last_parent_ingest_seq": 0,
                "last_parent_record_sha256": SHA_F,
                "final_expert_seq": 0,
                "final_expert_record_sha256": SHA_A,
                "final_expert_state_sha256": SHA_B,
                "final_expert_trace_sha256": SHA_C,
                "clean": clean,
                "reason": reason,
                "research_evaluable": False,
            }
            values.update(changes)
            values["terminal_sha256"] = (
                contracts.compute_expert_session_terminal_sha256(**values)
            )
            return contracts.ExpertSessionTerminalV1(**values)

        for text, reason in (
            (
                "operator_stop",
                contracts.ExpertTerminalReasonV1.OPERATOR_STOP,
            ),
            (
                "session_end",
                contracts.ExpertTerminalReasonV1.SESSION_END,
            ),
        ):
            self.assertEqual(
                terminal(evidence_reason=text, reason=reason).reason,
                reason,
            )
        for text, reason in (
            (
                "operator_stop",
                contracts.ExpertTerminalReasonV1.SESSION_END,
            ),
            (
                "session_end",
                contracts.ExpertTerminalReasonV1.OPERATOR_STOP,
            ),
        ):
            with self.subTest(evidence_reason=text, reason=reason):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "terminal_alignment",
                ):
                    terminal(evidence_reason=text, reason=reason)

        halted_reasons = (
            "operator_halt",
            "initialization_failure",
            "capture_contract_violation",
            "provider_gate_denied",
            "retention_global_halt",
            "disk_low",
            "reducer_exception",
            "derived_validation_failure",
            "trace_exception",
            "ingress_backpressure",
            "ingress_owner_unresponsive",
        )
        for evidence_reason in halted_reasons:
            with self.subTest(halted_reason=evidence_reason):
                self.assertEqual(
                    terminal(
                        evidence_reason=evidence_reason,
                        reason=contracts.ExpertTerminalReasonV1.EXPERT_HALT,
                        clean=False,
                    ).evidence_terminal_reason,
                    evidence_reason,
                )
        with self.subTest(halted_reason="invalid"):
            with self.assertRaisesRegex(
                ExpertContractError,
                "terminal_alignment",
            ):
                terminal(
                    evidence_reason="not_a_phase1_terminal_reason",
                    reason=contracts.ExpertTerminalReasonV1.EXPERT_HALT,
                    clean=False,
                )

        invalid_count_or_sequence_shapes = (
            {"evidence_terminal_ingest_seq": 0},
            {"evidence_terminal_ingest_seq": 1},
            {
                "evidence_terminal_ingest_seq": 101,
                "evidence_raw_count": 1,
                "evidence_derived_count": 99,
                "expert_group_count": 1,
                "expert_record_count": 1,
                "last_parent_ingest_seq": 2,
                "final_expert_seq": 1,
            },
            {
                "evidence_terminal_ingest_seq": 4,
                "evidence_raw_count": 1,
                "evidence_derived_count": 1,
                "expert_group_count": 1,
                "expert_record_count": 1,
                "last_parent_ingest_seq": 4,
                "final_expert_seq": 1,
            },
            {
                "evidence_terminal_ingest_seq": 5,
                "evidence_raw_count": 1,
                "evidence_derived_count": 1,
                "expert_group_count": 1,
                "expert_record_count": 1,
                "last_parent_ingest_seq": 2,
                "final_expert_seq": 1,
            },
            {
                "evidence_terminal_ingest_seq": 4,
                "evidence_raw_count": 1,
                "evidence_derived_count": 1,
                "expert_group_count": 1,
                "expert_record_count": 1,
                "last_parent_ingest_seq": 3,
                "final_expert_seq": 1,
            },
        )
        for changes in invalid_count_or_sequence_shapes:
            with self.subTest(invalid_terminal_shape=changes):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "terminal_alignment",
                ):
                    terminal(
                        evidence_reason="operator_stop",
                        reason=contracts.ExpertTerminalReasonV1.OPERATOR_STOP,
                        **changes,
                    )

    def test_capacity_proof_requires_all_three_ruled_exact_131064_caps(
        self,
    ) -> None:
        self.assertEqual(
            _task6_capacity().maximum_synchronization_state_bytes,
            131_064,
        )
        for field_name in (
            "maximum_synchronization_state_bytes",
            "maximum_transition_payload_bytes",
            "maximum_rejected_payload_bytes",
        ):
            for value in (131_063, 131_065):
                with self.subTest(field=field_name, value=value):
                    with self.assertRaisesRegex(
                        ExpertContractError,
                        "capacity_proof",
                    ):
                        _task6_capacity(**{field_name: value})

    def test_task6_schemas_are_closed_local_and_exactly_bounded(
        self,
    ) -> None:
        schema_root = (
            Path(__file__).resolve().parents[2]
            / "inci_tennis_expert"
            / "schemas"
        )
        names = (
            "expert-session-manifest-v1.schema.json",
            "expert-journal-record-v1.schema.json",
            "expert-journal-group-v1.schema.json",
            "expert-session-terminal-v1.schema.json",
            "expert-synchronization-applied-v1.schema.json",
            "expert-observation-ignored-v1.schema.json",
            "expert-observation-rejected-v1.schema.json",
        )
        maximum_integer = 10**256 - 1

        def walk(
            node: object,
            *,
            root: dict[str, object],
            path: str,
        ) -> None:
            if type(node) is list:
                for index, item in enumerate(node):
                    walk(item, root=root, path=f"{path}/{index}")
                return
            if type(node) is not dict:
                return
            if "$ref" in node:
                ref = node["$ref"]
                self.assertIs(type(ref), str, path)
                self.assertTrue(ref.startswith("#/$defs/"), path)
                self.assertIn(ref.removeprefix("#/$defs/"), root["$defs"])
            if node.get("type") == "object":
                self.assertIs(node.get("additionalProperties"), False, path)
                properties = node.get("properties")
                required = node.get("required")
                self.assertIs(type(properties), dict, path)
                self.assertIs(type(required), list, path)
                self.assertEqual(set(required), set(properties), path)
            if (
                node.get("type") == "integer"
                and "const" not in node
            ):
                self.assertIn("minimum", node, path)
                self.assertIn("maximum", node, path)
                self.assertLessEqual(node["minimum"], node["maximum"])
                self.assertLessEqual(node["maximum"], maximum_integer)
            for key, value in node.items():
                walk(value, root=root, path=f"{path}/{key}")

        for name in names:
            with self.subTest(schema=name):
                root = json.loads((schema_root / name).read_bytes())
                walk(root, root=root, path=name)

        manifest = json.loads(
            (
                schema_root / "expert-session-manifest-v1.schema.json"
            ).read_bytes()
        )
        event_pins = manifest["$defs"]["eventBundle"]["properties"]["pins"]
        self.assertIs(event_pins.get("items"), False)
        expected_event_pins = (
            (
                "synchronization_applied",
                "ExpertSynchronizationAppliedPayloadV1",
                "expert-synchronization-applied-v1.schema.json",
            ),
            (
                "observation_ignored",
                "ExpertObservationIgnoredPayloadV1",
                "expert-observation-ignored-v1.schema.json",
            ),
            (
                "observation_rejected",
                "ExpertObservationRejectedPayloadV1",
                "expert-observation-rejected-v1.schema.json",
            ),
        )
        prefix_items = event_pins.get("prefixItems")
        self.assertIs(type(prefix_items), list)
        self.assertEqual(len(prefix_items), len(expected_event_pins))
        for item, expected in zip(
            prefix_items,
            expected_event_pins,
            strict=True,
        ):
            overlay = item["allOf"][1]["properties"]
            self.assertEqual(
                (
                    overlay["event_kind"]["const"],
                    overlay["payload_contract_name"]["const"],
                    overlay["resource_name"]["const"],
                ),
                expected,
            )
            self.assertEqual(overlay["event_version"]["const"], 1)

        terminal_schema = json.loads(
            (
                schema_root / "expert-session-terminal-v1.schema.json"
            ).read_bytes()
        )
        with self.subTest(schema_semantics="terminal_sequence"):
            terminal_sequence = terminal_schema["$defs"].get(
                "terminalSequence"
            )
            self.assertIs(type(terminal_sequence), dict)
            self.assertEqual(terminal_sequence.get("minimum"), 2)
            self.assertEqual(
                terminal_schema["properties"][
                    "evidence_terminal_ingest_seq"
                ],
                {"$ref": "#/$defs/terminalSequence"},
            )
        halted_reasons = (
            "operator_halt",
            "initialization_failure",
            "capture_contract_violation",
            "provider_gate_denied",
            "retention_global_halt",
            "disk_low",
            "reducer_exception",
            "derived_validation_failure",
            "trace_exception",
            "ingress_backpressure",
            "ingress_owner_unresponsive",
        )
        with self.subTest(schema_semantics="halted_reason_vocabulary"):
            halted_branch = terminal_schema["oneOf"][2]
            self.assertEqual(
                halted_branch["properties"].get(
                    "evidence_terminal_reason",
                    {},
                ).get("enum"),
                list(halted_reasons),
            )

        rejected_schema = json.loads(
            (
                schema_root
                / "expert-observation-rejected-v1.schema.json"
            ).read_bytes()
        )
        parent = contracts.ExpertParentEvidenceV1(
            session_id="11111111-1111-4111-8111-111111111111",
            ingest_seq=2,
            record_sha256=SHA_A,
            event_type="timer_tick",
            event_version=1,
            local_wall_ns=200,
            local_monotonic_ns=100,
            clock_uncertainty_ns=2,
        )
        common = {
            "parent": parent,
            "parent_output_index": 0,
            "parent_output_count": 1,
            "normalizer_id": "normalizer-v1",
            "normalizer_code_sha256": SHA_B,
            "normalizer_schema_sha256": SHA_C,
        }
        synchronized = contracts.ExpertSynchronizationObservationV1(
            **common,
            evidence=synchronization_input(),
            observation=paired_time(),
        )
        ignored = contracts.ExpertIgnoredObservationV1(
            **common,
            reason=contracts.ExpertIgnoreReasonV1.EVENT_NOT_RELEVANT,
        )
        synchronization_reasons = (
            "synchronization_session_drift",
            "reducer_exception",
            "prior_outcome_halted",
        )
        for reason in synchronization_reasons:
            with self.subTest(
                schema_semantics="synchronization_rejection",
                reason=reason,
            ):
                self.assertTrue(
                    _task6_schema_matches(
                        {
                            "observation": _task6_plain_schema_value(
                                synchronized
                            ),
                            "reason": reason,
                        },
                        rejected_schema,
                        rejected_schema,
                    )
                )
        with self.subTest(schema_semantics="ignored_rejection"):
            self.assertTrue(
                _task6_schema_matches(
                    {
                        "observation": _task6_plain_schema_value(ignored),
                        "reason": "static_session_halt",
                    },
                    rejected_schema,
                    rejected_schema,
                )
            )
        reject_reasons = tuple(
            item.value for item in contracts.ExpertRejectReasonV1
        )
        for index, inner_reason in enumerate(reject_reasons):
            rejected = contracts.ExpertRejectedObservationV1(
                **common,
                reason=contracts.ExpertRejectReasonV1(inner_reason),
            )
            plain_rejected = _task6_plain_schema_value(rejected)
            with self.subTest(
                schema_semantics="exact_rejected_reason",
                reason=inner_reason,
            ):
                self.assertTrue(
                    _task6_schema_matches(
                        {
                            "observation": plain_rejected,
                            "reason": inner_reason,
                        },
                        rejected_schema,
                        rejected_schema,
                    )
                )
                self.assertFalse(
                    _task6_schema_matches(
                        {
                            "observation": plain_rejected,
                            "reason": reject_reasons[
                                (index + 1) % len(reject_reasons)
                            ],
                        },
                        rejected_schema,
                        rejected_schema,
                    )
                )
        for reason in reject_reasons:
            expected = reason in synchronization_reasons
            with self.subTest(
                schema_semantics="closed_synchronization_reason",
                reason=reason,
            ):
                self.assertIs(
                    _task6_schema_matches(
                        {
                            "observation": _task6_plain_schema_value(
                                synchronized
                            ),
                            "reason": reason,
                        },
                        rejected_schema,
                        rejected_schema,
                    ),
                    expected,
                )
            with self.subTest(
                schema_semantics="closed_ignored_reason",
                reason=reason,
            ):
                self.assertIs(
                    _task6_schema_matches(
                        {
                            "observation": _task6_plain_schema_value(
                                ignored
                            ),
                            "reason": reason,
                        },
                        rejected_schema,
                        rejected_schema,
                    ),
                    reason == "static_session_halt",
                )

    def test_task6_schema_resources_are_exactly_installed_and_parseable(
        self,
    ) -> None:
        schema_root = (
            Path(__file__).resolve().parents[2]
            / "inci_tennis_expert"
            / "schemas"
        )
        names = (
            "expert-session-manifest-v1.schema.json",
            "expert-journal-record-v1.schema.json",
            "expert-journal-group-v1.schema.json",
            "expert-session-terminal-v1.schema.json",
            "expert-synchronization-applied-v1.schema.json",
            "expert-observation-ignored-v1.schema.json",
            "expert-observation-rejected-v1.schema.json",
        )
        documents = tuple(
            json.loads((schema_root / name).read_bytes()) for name in names
        )
        self.assertTrue(
            all(
                document["$schema"]
                == "https://json-schema.org/draft/2020-12/schema"
                and document["type"] == "object"
                and document["additionalProperties"] is False
                for document in documents
            )
        )
        self.assertEqual(
            (schema_root / "task6-fallback-no-payload-v1.schema.json").read_bytes(),
            b"false\n",
        )

    def test_event_schema_registry_matches_installed_resources_independently(
        self,
    ) -> None:
        schema_root = (
            Path(__file__).resolve().parents[2]
            / "inci_tennis_expert"
            / "schemas"
        )
        expected = (
            (
                contracts.ExpertEventKindV1.SYNCHRONIZATION_APPLIED,
                "expert-synchronization-applied-v1.schema.json",
            ),
            (
                contracts.ExpertEventKindV1.OBSERVATION_IGNORED,
                "expert-observation-ignored-v1.schema.json",
            ),
            (
                contracts.ExpertEventKindV1.OBSERVATION_REJECTED,
                "expert-observation-rejected-v1.schema.json",
            ),
        )
        self.assertEqual(
            tuple(
                contracts.expert_event_schema_resource_sha256(kind)
                for kind, _ in expected
            ),
            tuple(
                hashlib.sha256((schema_root / name).read_bytes()).hexdigest()
                for _, name in expected
            ),
        )
        with self.assertRaises(TypeError):
            contracts.expert_event_schema_resource_sha256(
                "synchronization_applied"  # type: ignore[arg-type]
            )

    def test_journal_cursor_genesis_and_nonempty_count_shapes_are_closed(
        self,
    ) -> None:
        genesis = contracts.ExpertJournalCursorV1(
            schema_version=1,
            session_id="11111111-1111-4111-8111-111111111111",
            group_count=0,
            record_count=0,
            last_parent_ingest_seq=0,
            last_parent_record_sha256=SHA_A,
            expert_seq=0,
            expert_record_sha256=SHA_B,
            expert_state_sha256=SHA_C,
            expert_trace_sha256=SHA_D,
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "journal_cursor",
        ):
            replace(genesis, last_parent_ingest_seq=7)
        nonempty = replace(
            genesis,
            group_count=1,
            record_count=2,
            last_parent_ingest_seq=7,
            expert_seq=2,
        )
        self.assertEqual(nonempty.last_parent_ingest_seq, 7)
        with self.assertRaisesRegex(
            ExpertContractError,
            "journal_cursor",
        ):
            replace(nonempty, last_parent_ingest_seq=0)

    def test_terminal_requires_one_group_per_raw_and_exact_empty_shape(
        self,
    ) -> None:
        def terminal(**changes: object):
            values: dict[str, object] = {
                "schema_version": 1,
                "session_id": "11111111-1111-4111-8111-111111111111",
                "expert_manifest_sha256": SHA_A,
                "provider_request_binding_sha256": SHA_B,
                "match_binding_universe_sha256": SHA_C,
                "retention_binding_sha256": SHA_D,
                "evidence_terminal_ingest_seq": 2,
                "evidence_terminal_record_sha256": SHA_E,
                "evidence_terminal_clean": True,
                "evidence_terminal_reason": "session_end",
                "evidence_raw_count": 0,
                "evidence_derived_count": 0,
                "expert_group_count": 0,
                "expert_record_count": 0,
                "last_parent_ingest_seq": 0,
                "last_parent_record_sha256": SHA_F,
                "final_expert_seq": 0,
                "final_expert_record_sha256": SHA_A,
                "final_expert_state_sha256": SHA_B,
                "final_expert_trace_sha256": SHA_C,
                "clean": True,
                "reason": contracts.ExpertTerminalReasonV1.SESSION_END,
                "research_evaluable": False,
            }
            values.update(changes)
            values["terminal_sha256"] = (
                contracts.compute_expert_session_terminal_sha256(**values)
            )
            return contracts.ExpertSessionTerminalV1(**values)

        self.assertEqual(terminal().expert_group_count, 0)
        with self.assertRaisesRegex(
            ExpertContractError,
            "terminal_alignment",
        ):
            terminal(evidence_raw_count=99)
        with self.assertRaisesRegex(
            ExpertContractError,
            "terminal_alignment",
        ):
            terminal(last_parent_ingest_seq=7)
        nonempty = terminal(
            evidence_terminal_ingest_seq=4,
            evidence_raw_count=1,
            evidence_derived_count=1,
            expert_group_count=1,
            expert_record_count=2,
            last_parent_ingest_seq=2,
            final_expert_seq=2,
        )
        self.assertEqual(nonempty.evidence_raw_count, 1)
        with self.assertRaisesRegex(
            ExpertContractError,
            "terminal_alignment",
        ):
            terminal(
                evidence_terminal_ingest_seq=4,
                evidence_raw_count=1,
                evidence_derived_count=1,
                expert_group_count=1,
                expert_record_count=2,
                last_parent_ingest_seq=0,
                final_expert_seq=2,
            )


if __name__ == "__main__":
    unittest.main()
