"""Pure coordinator for paper-only live score captures."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Final

from inci_tennis_adapters.live_score_candidates import LiveScoreCaptureContext, NormalizedLiveScore
from inci_tennis_expert.contracts import MatchStatus, PlayerSide, ProviderPoint, TennisState
from inci_tennis_expert.live_paper_contracts import (
    LivePaperRebaseCandidate,
    LivePaperScoreAnchor,
    LivePaperScoreCoordinatorState,
    LivePaperScoreDecision,
    LivePaperScoreDecisionKind,
    LivePaperSourceObservation,
    LivePaperSupport,
    PaperScoreTrust,
    make_live_paper_anchor,
    make_live_paper_transition,
    score_coordinates,
)
from inci_tennis_expert.tennis_score import apply_point


__all__ = (
    "initial_live_paper_score_coordinator_state",
    "reduce_live_paper_scores",
    "observation_from_live_score_facts",
)


_FRESHNESS_NS: Final[int] = 5_000_000_000
_STABLE_REBASE_NS: Final[int] = 250_000_000


def initial_live_paper_score_coordinator_state(canonical_match_id: str) -> LivePaperScoreCoordinatorState:
    return LivePaperScoreCoordinatorState(
        canonical_match_id=canonical_match_id,
        anchor=None,
        local_point_ordinal=0,
        consensus_epoch=0,
        rebase_epoch=0,
        quarantined=False,
        quarantine_barrier_monotonic_ns=None,
        rebase_candidate=None,
    )


def _fresh(observation: LivePaperSourceObservation, now_wall_ns: int, now_monotonic_ns: int) -> bool:
    if observation.captured_wall_ns > now_wall_ns or observation.captured_monotonic_ns > now_monotonic_ns:
        return False
    wall_age_ns = now_wall_ns - observation.captured_wall_ns
    monotonic_age_ns = now_monotonic_ns - observation.captured_monotonic_ns
    return max(wall_age_ns, monotonic_age_ns) + observation.state.last_clock_uncertainty_ns <= _FRESHNESS_NS


def _selection(observations: tuple[LivePaperSourceObservation, ...], now_wall_ns: int, now_monotonic_ns: int) -> tuple[TennisState, PaperScoreTrust, tuple[str, ...], tuple[str, ...], int, int, tuple[str, ...], tuple[str, ...], tuple[LivePaperSupport, ...]] | None:
    fresh = tuple(item for item in observations if _fresh(item, now_wall_ns, now_monotonic_ns))
    if not fresh:
        return None
    coordinates = {score_coordinates(item.state) for item in fresh}
    if len(coordinates) != 1:
        return None
    ordered = tuple(sorted(fresh, key=lambda item: (item.source_id, item.lineage_sha256, item.raw_receipt_sha256)))
    independent_ids = tuple(sorted({item.independent_lineage_id for item in ordered}))
    proven_ids = tuple(sorted({item.independent_lineage_id for item in ordered if item.independence_proven is True}))
    proven_digests = {item.lineage_sha256 for item in ordered if item.independence_proven is True}
    if len(proven_ids) >= 2 and len(proven_digests) >= 2:
        trust = PaperScoreTrust.CONSENSUS_PAPER
    elif len(independent_ids) == 1:
        trust = PaperScoreTrust.SINGLE_SOURCE_PAPER
    else:
        return None
    supporting = (
        tuple(item for item in ordered if item.independence_proven is True)
        if trust is PaperScoreTrust.CONSENSUS_PAPER
        else ordered
    )
    supports_by_receipt: dict[str, LivePaperSupport] = {}
    for item in supporting:
        support = LivePaperSupport(
            item.raw_receipt_sha256,
            item.lineage_sha256,
            item.independent_lineage_id,
            item.independence_proven is True,
            item.independence_proof_sha256,
        )
        existing = supports_by_receipt.get(support.raw_receipt_sha256)
        if existing is not None and existing != support:
            return None
        supports_by_receipt[support.raw_receipt_sha256] = support
    supports = tuple(sorted(supports_by_receipt.values(), key=lambda support: support.raw_receipt_sha256))
    return (
        ordered[0].state,
        trust,
        tuple(sorted({support.lineage_sha256 for support in supports})),
        tuple(support.raw_receipt_sha256 for support in supports),
        max(item.captured_wall_ns for item in ordered),
        max(item.captured_monotonic_ns for item in ordered),
        independent_ids,
        proven_ids,
        supports,
    )


def _fresh_disagreement(observations: tuple[LivePaperSourceObservation, ...], now_wall_ns: int, now_monotonic_ns: int) -> bool:
    fresh = tuple(item for item in observations if _fresh(item, now_wall_ns, now_monotonic_ns))
    return len({score_coordinates(item.state) for item in fresh}) > 1


def _decision(kind: LivePaperScoreDecisionKind, *, trust: PaperScoreTrust = PaperScoreTrust.ABSTAINED, anchor: LivePaperScoreAnchor | None = None, transition: object | None = None, reason: str) -> LivePaperScoreDecision:
    return LivePaperScoreDecision(kind, trust, anchor, transition, reason)  # type: ignore[arg-type]


def _anchor(state: LivePaperScoreCoordinatorState, selected: tuple[TennisState, PaperScoreTrust, tuple[str, ...], tuple[str, ...], int, int, tuple[str, ...], tuple[str, ...], tuple[LivePaperSupport, ...]], *, rebase_epoch: int | None = None, consensus_epoch: int | None = None) -> LivePaperScoreAnchor:
    accepted, trust, lineages, receipts, wall, monotonic, _, proven_ids, supports = selected
    epoch = state.consensus_epoch if consensus_epoch is None else consensus_epoch
    return make_live_paper_anchor(
        canonical_match_id=state.canonical_match_id,
        state=accepted,
        trust=trust,
        supporting_lineage_sha256s=lineages,
        parent_receipt_sha256s=receipts,
        consensus_epoch=epoch,
        correction_epoch=accepted.correction_epoch,
        rebase_epoch=state.rebase_epoch if rebase_epoch is None else rebase_epoch,
        accepted_wall_ns=wall,
        accepted_monotonic_ns=monotonic,
        supporting_independent_lineage_ids=proven_ids,
        supporting_sources=supports,
    )


def _successor(before: TennisState, after: TennisState) -> tuple[PlayerSide, PlayerSide] | None:
    winners: list[PlayerSide] = []
    for winner in (PlayerSide.HOME, PlayerSide.AWAY):
        try:
            projected = apply_point(
                before,
                ProviderPoint(
                    provider_source_id=before.provider_source_id,
                    revision_domain_id=before.revision_domain_id,
                    source_lineage_sha256=before.source_lineage_sha256,
                    provider_event_id=after.last_provider_event_id,
                    provider_match_id=before.provider_match_id,
                    home_player_id=before.home_player_id,
                    away_player_id=before.away_player_id,
                    scheduled_start_wall_ns=before.scheduled_start_wall_ns,
                    match_format=before.match_format,
                    correction_epoch=before.correction_epoch,
                    revision=before.revision + 1,
                    point_winner=winner,
                    server_before_point=before.server_for_next_point,
                    source_wall_ns=after.last_source_wall_ns,
                    source_generated_wall_ns=after.last_source_generated_wall_ns,
                    received_monotonic_ns=after.last_received_monotonic_ns,
                    clock_uncertainty_ns=after.last_clock_uncertainty_ns,
                ),
            ).state
        except Exception:
            continue
        if score_coordinates(projected) == score_coordinates(after):
            winners.append(winner)
    if len(winners) != 1 or before.server_for_next_point is None:
        return None
    return before.server_for_next_point, winners[0]


def _quarantine(state: LivePaperScoreCoordinatorState, *, now_monotonic_ns: int, reason: str) -> tuple[LivePaperScoreCoordinatorState, LivePaperScoreDecision]:
    next_state = replace(
        state,
        quarantined=True,
        quarantine_barrier_monotonic_ns=now_monotonic_ns,
        rebase_candidate=None,
    )
    return next_state, _decision(LivePaperScoreDecisionKind.QUARANTINED, reason=reason)


def reduce_live_paper_scores(state: LivePaperScoreCoordinatorState, observations: tuple[LivePaperSourceObservation, ...], *, now_wall_ns: int, now_monotonic_ns: int) -> tuple[LivePaperScoreCoordinatorState, LivePaperScoreDecision]:
    if type(state) is not LivePaperScoreCoordinatorState:
        raise TypeError("state")
    if type(observations) is not tuple or any(type(item) is not LivePaperSourceObservation for item in observations):
        raise TypeError("observations")
    if any(item.canonical_match_id != state.canonical_match_id for item in observations):
        raise ValueError("canonical_match_id")
    if type(now_wall_ns) is not int or now_wall_ns < 0 or type(now_monotonic_ns) is not int or now_monotonic_ns < 0:
        raise ValueError("now")
    selected = _selection(observations, now_wall_ns, now_monotonic_ns)
    if state.quarantined:
        if selected is None:
            next_state = replace(state, rebase_candidate=None) if _fresh_disagreement(observations, now_wall_ns, now_monotonic_ns) else state
            return next_state, _decision(LivePaperScoreDecisionKind.ABSTAINED, reason="rebase_source_unstable")
        accepted, trust, _, _, _, captured_monotonic, independent_lineage_ids, _, _ = selected
        if captured_monotonic <= state.quarantine_barrier_monotonic_ns:  # type: ignore[operator]
            return state, _decision(LivePaperScoreDecisionKind.ABSTAINED, reason="rebase_before_barrier")
        if trust is PaperScoreTrust.CONSENSUS_PAPER:
            epoch = state.consensus_epoch + 1
            anchor = _anchor(state, selected, rebase_epoch=state.rebase_epoch + 1, consensus_epoch=epoch)
            next_state = LivePaperScoreCoordinatorState(state.canonical_match_id, anchor, 0, epoch, state.rebase_epoch + 1, False, None, None)
            return next_state, _decision(LivePaperScoreDecisionKind.REBASED, trust=trust, anchor=anchor, reason="independent_consensus_rebase")
        if len(independent_lineage_ids) != 1:
            next_state = replace(state, rebase_candidate=None)
            return next_state, _decision(LivePaperScoreDecisionKind.ABSTAINED, reason="rebase_single_lineage_unproved")
        candidate = state.rebase_candidate
        if candidate is None or score_coordinates(candidate.state) != score_coordinates(accepted) or candidate.independent_lineage_id != independent_lineage_ids[0] or captured_monotonic <= candidate.latest_captured_monotonic_ns or now_monotonic_ns - candidate.first_captured_monotonic_ns > _FRESHNESS_NS:
            next_state = replace(state, rebase_candidate=LivePaperRebaseCandidate(accepted, independent_lineage_ids[0], captured_monotonic, captured_monotonic))
            return next_state, _decision(LivePaperScoreDecisionKind.ABSTAINED, reason="rebase_waiting_repeat")
        if captured_monotonic - candidate.first_captured_monotonic_ns < _STABLE_REBASE_NS:
            next_state = replace(state, rebase_candidate=LivePaperRebaseCandidate(candidate.state, candidate.independent_lineage_id, candidate.first_captured_monotonic_ns, captured_monotonic))
            return next_state, _decision(LivePaperScoreDecisionKind.ABSTAINED, reason="rebase_waiting_interval")
        anchor = _anchor(state, selected, rebase_epoch=state.rebase_epoch + 1)
        next_state = LivePaperScoreCoordinatorState(state.canonical_match_id, anchor, 0, state.consensus_epoch, state.rebase_epoch + 1, False, None, None)
        return next_state, _decision(LivePaperScoreDecisionKind.REBASED, trust=trust, anchor=anchor, reason="stable_single_source_rebase")
    if selected is None:
        return state, _decision(LivePaperScoreDecisionKind.ABSTAINED, reason="fresh_complete_score_unavailable")
    accepted, trust, _, _, _, _, _, _, _ = selected
    if state.anchor is None:
        epoch = state.consensus_epoch + (1 if trust is PaperScoreTrust.CONSENSUS_PAPER else 0)
        anchor = _anchor(state, selected, consensus_epoch=epoch)
        next_state = replace(state, anchor=anchor, consensus_epoch=epoch)
        return next_state, _decision(LivePaperScoreDecisionKind.ANCHORED, trust=trust, anchor=anchor, reason="initial_anchor")
    if score_coordinates(accepted) == score_coordinates(state.anchor.state):
        return state, _decision(
            LivePaperScoreDecisionKind.UNCHANGED,
            trust=state.anchor.trust,
            anchor=state.anchor,
            reason="score_unchanged",
        )
    resolved = _successor(state.anchor.state, accepted)
    if resolved is None:
        return _quarantine(state, now_monotonic_ns=now_monotonic_ns, reason="unproved_score_transition")
    server, winner = resolved
    anchor = _anchor(state, selected)
    transition = make_live_paper_transition(
        canonical_match_id=state.canonical_match_id,
        local_point_ordinal=state.local_point_ordinal + 1,
        before_state=state.anchor.state,
        after_state=accepted,
        server=server,
        winner=winner,
        trust=trust,
        supporting_lineage_sha256s=anchor.supporting_lineage_sha256s,
        parent_receipt_sha256s=anchor.parent_receipt_sha256s,
        consensus_epoch=anchor.consensus_epoch,
        correction_epoch=accepted.correction_epoch,
        rebase_epoch=state.rebase_epoch,
        accepted_wall_ns=anchor.accepted_wall_ns,
        accepted_monotonic_ns=anchor.accepted_monotonic_ns,
        supporting_independent_lineage_ids=anchor.supporting_independent_lineage_ids,
        supporting_sources=anchor.supporting_sources,
    )
    next_state = replace(state, anchor=anchor, local_point_ordinal=transition.local_point_ordinal)
    return next_state, _decision(LivePaperScoreDecisionKind.POINT_ACCEPTED, trust=trust, anchor=anchor, transition=transition, reason="exact_point_successor")


def observation_from_live_score_facts(*, canonical_match_id: str, context: LiveScoreCaptureContext, normalized: NormalizedLiveScore, local_revision: int, independence_proof_sha256: str | None = None) -> LivePaperSourceObservation:
    """Project parser facts into the separate, locally-revisioned paper domain."""
    if type(canonical_match_id) is not str or not canonical_match_id:
        raise ValueError("canonical_match_id")
    if type(context) is not LiveScoreCaptureContext or type(normalized) is not NormalizedLiveScore:
        raise TypeError("capture")
    if type(local_revision) is not int or local_revision <= 0:
        raise ValueError("local_revision")
    if context.lineage_independence_proven is True:
        if type(independence_proof_sha256) is not str:
            raise ValueError("independence_proof_sha256")
    elif independence_proof_sha256 is not None:
        raise ValueError("independence_proof_sha256")
    facts = normalized.facts
    if facts is None or normalized.provider_source_id != context.provider_source_id or normalized.source_lineage_sha256 != context.source_lineage_sha256 or normalized.raw_capture_id != context.raw_capture_id or normalized.lineage_independence_proven != context.lineage_independence_proven:
        raise ValueError("paper_score_facts")
    if (facts.provider_match_id, facts.home_player_id, facts.away_player_id) != (context.provider_match_id, context.home_player_id, context.away_player_id):
        raise ValueError("paper_score_facts")
    if facts.status in {MatchStatus.LIVE, MatchStatus.SUSPENDED} and facts.server_for_next_point is None:
        raise ValueError("paper_score_facts")
    source_wall_ns = facts.source_generated_wall_ns if facts.source_generated_wall_ns is not None else context.local_capture_wall_ns
    state = TennisState(
        provider_source_id=context.provider_source_id,
        revision_domain_id="paper-local-revisions-v1",
        source_lineage_sha256=context.source_lineage_sha256,
        provider_match_id=context.provider_match_id,
        home_player_id=context.home_player_id,
        away_player_id=context.away_player_id,
        scheduled_start_wall_ns=context.scheduled_start_wall_ns,
        match_format=context.match_format,
        status=facts.status,
        termination_kind=facts.termination_kind,
        winner=facts.winner,
        retired_side=None,
        completed_sets=facts.completed_sets,
        games_home=facts.games_home,
        games_away=facts.games_away,
        points_home=facts.points_home,
        points_away=facts.points_away,
        in_tiebreak=facts.in_tiebreak,
        tiebreak_points_home=0,
        tiebreak_points_away=0,
        tiebreak_first_server=None,
        server_for_next_point=facts.server_for_next_point,
        correction_epoch=0,
        revision=local_revision,
        snapshot_complete=True,
        last_provider_event_id="paper-local-" + context.raw_capture_id,
        last_event_semantic_sha256=normalized.raw_sha256,
        correction_lineage_sha256=sha256(b"INCI-LIVE-PAPER-LOCAL-REVISION-V1\0" + normalized.raw_sha256.encode("ascii")).hexdigest(),
        last_source_wall_ns=source_wall_ns,
        last_source_generated_wall_ns=source_wall_ns,
        last_received_monotonic_ns=context.local_capture_monotonic_ns,
        last_clock_uncertainty_ns=context.local_clock_uncertainty_ns,
        block_reason=None,
        expected_revision=None,
        observed_revision=None,
        blocked_event_semantic_sha256=None,
        blocked_received_monotonic_ns=None,
    )
    return LivePaperSourceObservation(
        canonical_match_id=canonical_match_id,
        provider_slot=normalized.provider_slot.value,
        source_id=context.provider_source_id,
        independent_lineage_id=context.source_lineage_sha256,
        lineage_sha256=context.source_lineage_sha256,
        independence_proven=normalized.lineage_independence_proven,
        state=state,
        raw_receipt_sha256=normalized.raw_sha256,
        captured_wall_ns=context.local_capture_wall_ns,
        captured_monotonic_ns=context.local_capture_monotonic_ns,
        independence_proof_sha256=independence_proof_sha256,
        authority_label="PAPER_LOCAL_REVISION_TRANSPORT_ONLY",
    )
