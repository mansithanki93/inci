from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
import unittest

from inci_tennis_expert.contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ProviderPoint,
    ScoreValue,
    SetScore,
    TennisState,
    TerminationKind,
)
from inci_tennis_expert.fee_schedule import FrozenFeeSchedule
from inci_tennis_expert.live_paper_contracts import (
    LivePaperMarketBinding,
    LivePaperSourceObservation,
)
from inci_tennis_expert.live_paper_execution import (
    LivePaperL2Frame,
    LivePaperL2Level,
    LivePaperL2Market,
)
from inci_tennis_expert.live_two_model import (
    LiveArtifactAuthority,
    build_operator_bootstrap_artifacts,
)
from inci_tennis_expert.tennis_score import apply_point


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
HOME_ID = "9b0f6b43-5b68-4f9f-9f02-9a2d1b8ac1a1"
AWAY_ID = "8a0f6b43-5b68-4f9f-9f02-9a2d1b8ac1a2"


def _api() -> object:
    try:
        from inci_tennis_expert import live_paper_session
    except ImportError as error:
        raise AssertionError("live paper session API is missing") from error
    return live_paper_session


def _binding() -> LivePaperMarketBinding:
    return LivePaperMarketBinding(
        canonical_match_id="match-1",
        scheduled_start_wall_ns=9_000,
        home_player_id="home",
        away_player_id="away",
        home_ticker="KXTENNIS-HOME",
        home_market_id=HOME_ID,
        home_yes_player_side=PlayerSide.HOME,
        away_ticker="KXTENNIS-AWAY",
        away_market_id=AWAY_ID,
        away_yes_player_side=PlayerSide.AWAY,
    )


def _config(api: object) -> object:
    static, dynamic = build_operator_bootstrap_artifacts(
        canonical_match_id="match-1",
        scheduled_start_wall_ns=9_000,
        cutoff_wall_ns=8_999,
        home_serve_point_probability=Decimal(".80"),
        away_serve_point_probability=Decimal(".20"),
    )
    fees = FrozenFeeSchedule(
        schedule_id="fees-v1",
        series_tickers=("KXTENNIS",),
        taker_rate=Decimal("0"),
        maker_rate=Decimal("0"),
        taker_multiplier=Decimal("1"),
        maker_multiplier=Decimal("1"),
        trade_fee_precision=Decimal("0.0001"),
        balance_precision=Decimal("0.0001"),
        effective_from_wall_ns=1,
        effective_until_wall_ns=None,
    )
    authority = api.LivePaperProviderAuthority(
        slot="fixture",
        source_id="source-a",
        provider_match_id="provider-match",
        home_player_id="home",
        away_player_id="away",
        independent_lineage_id="lineage-a",
        source_lineage_sha256=SHA_A,
        independence_proven=True,
        independence_proof_sha256=SHA_B,
    )
    authorities = (authority,)
    return api.LivePaperSessionConfig(
        canonical_match_id="match-1",
        manifest_sha256="9" * 64,
        provider_authorities=authorities,
        provider_authority_sha256=(
            api.compute_live_paper_provider_authority_sha256(authorities)
        ),
        static_artifact=static,
        dynamic_artifact=dynamic,
        artifact_authority=LiveArtifactAuthority.OPERATOR_BOOTSTRAP,
        market_binding=_binding(),
        fee_schedule=fees,
        fee_series_ticker="KXTENNIS",
        opened_wall_ns=1_000_000_000,
        opened_monotonic_ns=1_000_000_000,
    )


def _consensus_config(api: object) -> object:
    config = _config(api)
    authority_b = api.LivePaperProviderAuthority(
        slot="fixture",
        source_id="source-b",
        provider_match_id="provider-b-match",
        home_player_id="provider-b-home",
        away_player_id="provider-b-away",
        independent_lineage_id="lineage-b",
        source_lineage_sha256=SHA_C,
        independence_proven=True,
        independence_proof_sha256=SHA_D,
    )
    authorities = (*config.provider_authorities, authority_b)
    return replace(
        config,
        provider_authorities=authorities,
        provider_authority_sha256=(
            api.compute_live_paper_provider_authority_sha256(authorities)
        ),
    )


def _score_state() -> TennisState:
    return TennisState(
        provider_source_id="provider-a",
        revision_domain_id="paper-local",
        source_lineage_sha256=SHA_A,
        provider_match_id="provider-match",
        home_player_id="home",
        away_player_id="away",
        scheduled_start_wall_ns=9_000,
        match_format=MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        status=MatchStatus.LIVE,
        termination_kind=TerminationKind.NONE,
        winner=None,
        retired_side=None,
        completed_sets=(SetScore(6, 4, None, None),),
        games_home=0,
        games_away=0,
        points_home=ScoreValue.LOVE,
        points_away=ScoreValue.LOVE,
        in_tiebreak=False,
        tiebreak_points_home=0,
        tiebreak_points_away=0,
        tiebreak_first_server=None,
        server_for_next_point=PlayerSide.HOME,
        correction_epoch=0,
        revision=1,
        snapshot_complete=True,
        last_provider_event_id="capture-a",
        last_event_semantic_sha256=SHA_B,
        correction_lineage_sha256=SHA_C,
        last_source_wall_ns=2_000_000_000,
        last_source_generated_wall_ns=2_000_000_000,
        last_received_monotonic_ns=2_000_000_000,
        last_clock_uncertainty_ns=0,
        block_reason=None,
        expected_revision=None,
        observed_revision=None,
        blocked_event_semantic_sha256=None,
        blocked_received_monotonic_ns=None,
    )


def _score_input(api: object) -> object:
    observation = LivePaperSourceObservation(
        canonical_match_id="match-1",
        provider_slot="fixture",
        source_id="source-a",
        independent_lineage_id="lineage-a",
        lineage_sha256=SHA_A,
        independence_proven=True,
        state=_score_state(),
        raw_receipt_sha256=SHA_D,
        captured_wall_ns=2_000_000_000,
        captured_monotonic_ns=2_000_000_000,
        independence_proof_sha256=SHA_B,
    )
    return api.LivePaperScoreBatchInput(
        observations=(observation,),
        observed_wall_ns=2_000_000_000,
        observed_monotonic_ns=2_000_000_000,
    )


def _skewed_consensus_score_input(api: object) -> object:
    base = _score_input(api).observations[0]
    now_ns = 10_000_000_000
    old_ns = 6_000_000_000
    uncertainty_ns = 1_000_000_000
    source_a = replace(
        base,
        state=replace(
            base.state,
            last_clock_uncertainty_ns=uncertainty_ns,
        ),
        captured_wall_ns=now_ns,
        captured_monotonic_ns=old_ns,
    )
    source_b = replace(
        base,
        source_id="source-b",
        independent_lineage_id="lineage-b",
        lineage_sha256=SHA_C,
        independence_proof_sha256=SHA_D,
        raw_receipt_sha256="e" * 64,
        state=replace(
            base.state,
            provider_source_id="provider-b",
            revision_domain_id="provider-b-local",
            source_lineage_sha256=SHA_C,
            provider_match_id="provider-b-match",
            home_player_id="provider-b-home",
            away_player_id="provider-b-away",
            last_clock_uncertainty_ns=uncertainty_ns,
        ),
        captured_wall_ns=old_ns,
        captured_monotonic_ns=now_ns,
    )
    return api.LivePaperScoreBatchInput(
        (source_a, source_b),
        now_ns,
        now_ns,
    )


def _l2_input(api: object, sequence: int, now: int, receipt: str) -> object:
    binding = _binding()
    frame = LivePaperL2Frame(
        binding=binding,
        home=LivePaperL2Market(
            ticker=binding.home_ticker,
            market_id=binding.home_market_id,
            yes_player_side=PlayerSide.HOME,
            yes_bids=(LivePaperL2Level(Decimal(".30"), Decimal("100")),),
            yes_asks=(LivePaperL2Level(Decimal(".30"), Decimal("100")),),
        ),
        away=LivePaperL2Market(
            ticker=binding.away_ticker,
            market_id=binding.away_market_id,
            yes_player_side=PlayerSide.AWAY,
            yes_bids=(LivePaperL2Level(Decimal(".20"), Decimal("100")),),
            yes_asks=(LivePaperL2Level(Decimal(".80"), Decimal("100")),),
        ),
        physical_connection_generation=1,
        subscription_id=2,
        global_sequence=sequence,
        raw_l2_state_sha256=SHA_A,
        raw_parent_receipt_sha256=receipt,
        captured_wall_ns=now,
        captured_monotonic_ns=now,
        clock_uncertainty_ns=0,
    )
    return api.LivePaperL2Input(frame, now, now)


def _captured_l2_input(api: object, sequence: int, captured: int, observed: int, receipt: str) -> object:
    item = _l2_input(api, sequence, observed, receipt)
    return replace(
        item,
        frame=replace(
            item.frame,
            captured_wall_ns=captured,
            captured_monotonic_ns=captured,
        ),
    )


def _complete_session(api: object) -> tuple[object, tuple[object, ...]]:
    state = api.open_live_paper_session(_config(api))
    records: tuple[object, ...] = ()
    for item in (
        _score_input(api),
        _l2_input(api, 1, 3_000_000_000, "1" * 64),
        _l2_input(api, 2, 4_000_000_001, "2" * 64),
        api.LivePaperHeartbeatInput(61_000_000_000, 61_000_000_000),
        api.LivePaperTerminalInput("operator_stop", 62_000_000_000, 62_000_000_000),
    ):
        state, emitted = api.reduce_live_paper_input(state, item)
        records += emitted
    return state, records


def _advanced_observation(api: object, count: int) -> object:
    state = _score_state()
    for ordinal in range(1, count + 1):
        state = apply_point(
            state,
            ProviderPoint(
                provider_source_id=state.provider_source_id,
                revision_domain_id=state.revision_domain_id,
                source_lineage_sha256=state.source_lineage_sha256,
                provider_event_id=f"capture-{ordinal}",
                provider_match_id=state.provider_match_id,
                home_player_id=state.home_player_id,
                away_player_id=state.away_player_id,
                scheduled_start_wall_ns=state.scheduled_start_wall_ns,
                match_format=state.match_format,
                correction_epoch=state.correction_epoch,
                revision=state.revision + 1,
                point_winner=PlayerSide.HOME,
                server_before_point=state.server_for_next_point,
                source_wall_ns=2_000_000_000 + ordinal,
                source_generated_wall_ns=2_000_000_000 + ordinal,
                received_monotonic_ns=2_000_000_000 + ordinal,
                clock_uncertainty_ns=0,
            ),
        ).state
    return replace(
        _score_input(api).observations[0],
        state=state,
        raw_receipt_sha256="e" * 64,
        captured_wall_ns=2_000_000_000 + count,
        captured_monotonic_ns=2_000_000_000 + count,
    )


class LivePaperSessionTests(unittest.TestCase):
    def test_manifest_and_provider_authority_are_authenticated_by_log_and_checkpoint(self) -> None:
        """Catches resume accepting a different provider/proof authority set."""
        api = _api()
        authority = api.LivePaperProviderAuthority(
            slot="api_tennis",
            source_id="source-a",
            provider_match_id="provider-match",
            home_player_id="home",
            away_player_id="away",
            independent_lineage_id="lineage-a",
            source_lineage_sha256=SHA_A,
            independence_proven=True,
            independence_proof_sha256=SHA_B,
        )
        authority_digest = api.compute_live_paper_provider_authority_sha256(
            (authority,)
        )
        config = replace(
            _config(api),
            manifest_sha256="9" * 64,
            provider_authorities=(authority,),
            provider_authority_sha256=authority_digest,
        )
        state = api.open_live_paper_session(config)
        state, records = api.reduce_live_paper_input(state, _score_input(api))
        replay = api.replay_live_paper_records(api.encode_live_paper_records(records))
        checkpoint = api.decode_live_paper_checkpoint(
            api.encode_live_paper_checkpoint(state)
        )
        self.assertEqual(replay.state.config, config)
        self.assertEqual(checkpoint.config, config)
        with self.assertRaises(api.LivePaperSessionError):
            replace(config, provider_authority_sha256="7" * 64)

    def test_exact_point_transition_updates_model_and_replays_without_inventing_a_gap(self) -> None:
        """Catches a session dropping or fabricating the one-point causal model update."""
        api = _api()
        state = api.open_live_paper_session(_config(api))
        state, anchor_rows = api.reduce_live_paper_input(state, _score_input(api))
        item = api.LivePaperScoreBatchInput(
            (_advanced_observation(api, 1),),
            2_000_000_001,
            2_000_000_001,
        )
        state, transition_rows = api.reduce_live_paper_input(state, item)
        self.assertEqual(state.score_coordinator.local_point_ordinal, 1)
        self.assertEqual(state.live_model.local_point_ordinal, 1)
        self.assertIn("transition", tuple(record.kind.value for record in transition_rows))
        self.assertGreater(transition_rows[-1].record_ordinal, state.live_model.local_point_ordinal)
        replay = api.replay_live_paper_records(api.encode_live_paper_records(anchor_rows + transition_rows))
        self.assertEqual(replay.state, state)

    def test_canonical_chain_is_deterministic_and_record_ordinal_is_not_point_ordinal(self) -> None:
        """Catches nondeterministic JSON or coupling evidence rows to point updates."""
        api = _api()
        first_state, first = _complete_session(api)
        second_state, second = _complete_session(api)
        first_raw = api.encode_live_paper_records(first)
        self.assertEqual(first_raw, api.encode_live_paper_records(second))
        self.assertEqual(first_state, second_state)
        rows = tuple(json.loads(line) for line in first_raw.splitlines())
        self.assertEqual(tuple(row["record_ordinal"] for row in rows), tuple(range(1, len(rows) + 1)))
        transition_rows = tuple(row for row in rows if row["kind"] == "transition")
        self.assertFalse(transition_rows)
        self.assertGreater(first_state.record_count, first_state.score_coordinator.local_point_ordinal)
        self.assertTrue(all(line == json.dumps(json.loads(line), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") for line in first_raw.splitlines()))

    def test_replay_recomputes_an_equivalent_model_portfolio_and_record_stream(self) -> None:
        """Catches replay trusting recorded forecasts, actions, fills, or marks."""
        api = _api()
        state, records = _complete_session(api)
        raw = api.encode_live_paper_records(records)
        replay = api.replay_live_paper_records(raw, require_terminal=True)
        self.assertEqual(replay.state, state)
        self.assertEqual(api.encode_live_paper_records(replay.records), raw)
        self.assertIsNotNone(state.portfolio.position)
        self.assertEqual(state.portfolio.position.quantity, Decimal("100"))
        kinds = tuple(record.kind.value for record in records)
        for kind in ("raw_score_receipt", "raw_l2_receipt", "anchor", "forecast", "action", "fill", "mark", "checkpoint", "heartbeat", "terminal"):
            self.assertIn(kind, kinds)
        checkpoint_index = kinds.index("checkpoint")
        checkpoint_projection = records[checkpoint_index].payload.body
        self.assertEqual(
            checkpoint_projection.previous_encoded_log_bytes,
            len(api.encode_live_paper_records(records[:checkpoint_index])),
        )
        mark = next(record.payload.body for record in records if record.kind.value == "mark")
        self.assertTrue(mark.fully_priced)
        self.assertEqual(mark.priced_quantity, Decimal("100"))
        self.assertEqual(mark.net_liquidation_value, Decimal("30.0000"))
        self.assertEqual(mark.unrealized_pnl, Decimal("0.0000"))

    def test_checkpoint_authentication_and_corrupt_fallback_never_start_fresh(self) -> None:
        """Catches accepting a forged checkpoint or treating its loss as a new session."""
        api = _api()
        state, records = _complete_session(api)
        log = api.encode_live_paper_records(records)
        checkpoint = api.encode_live_paper_checkpoint(state)
        self.assertEqual(api.decode_live_paper_checkpoint(checkpoint), state)
        loaded = api.load_live_paper_checkpoint(checkpoint, log, require_terminal=True)
        self.assertTrue(loaded.checkpoint_used)
        self.assertEqual(loaded.state, state)
        corrupt = checkpoint[:-2] + b"x}"
        fallback = api.load_live_paper_checkpoint(corrupt, log, require_terminal=True)
        self.assertFalse(fallback.checkpoint_used)
        self.assertEqual(fallback.state, state)
        missing = api.load_live_paper_checkpoint(None, log, require_terminal=True)
        self.assertFalse(missing.checkpoint_used)
        self.assertEqual(missing.state, state)
        forged = api.encode_live_paper_checkpoint(replace(state, latest_forecast=None))
        rejected_forgery = api.load_live_paper_checkpoint(forged, log, require_terminal=True)
        self.assertFalse(rejected_forgery.checkpoint_used)
        self.assertEqual(rejected_forgery.state, state)
        forged_counter = api.encode_live_paper_checkpoint(
            replace(state, encoded_log_bytes=state.encoded_log_bytes - 1)
        )
        rejected_counter = api.load_live_paper_checkpoint(
            forged_counter,
            log,
            require_terminal=True,
        )
        self.assertFalse(rejected_counter.checkpoint_used)
        self.assertEqual(rejected_counter.state, state)

    def test_checkpoint_restart_replays_a_verified_suffix(self) -> None:
        """Catches a mid-log checkpoint being ignored or trusted without prefix validation."""
        api = _api()
        state = api.open_live_paper_session(_config(api))
        state, prefix = api.reduce_live_paper_input(state, _score_input(api))
        checkpoint = api.encode_live_paper_checkpoint(state)
        state, later = api.reduce_live_paper_input(state, _l2_input(api, 1, 3_000_000_000, "1" * 64))
        state, terminal = api.reduce_live_paper_input(state, api.LivePaperTerminalInput("stop", 4_000_000_000, 4_000_000_000))
        loaded = api.load_live_paper_checkpoint(checkpoint, api.encode_live_paper_records(prefix + later + terminal), require_terminal=True)
        self.assertTrue(loaded.checkpoint_used)
        self.assertEqual(loaded.state, state)

    def test_quarantine_and_stale_scores_durably_block_entry(self) -> None:
        """Catches paper entry from a quarantined anchor or score older than five seconds."""
        api = _api()
        anchored = api.open_live_paper_session(_config(api))
        anchored, _ = api.reduce_live_paper_input(anchored, _score_input(api))
        gap = api.LivePaperScoreBatchInput((_advanced_observation(api, 2),), 2_000_000_002, 2_000_000_002)
        quarantined, gap_rows = api.reduce_live_paper_input(anchored, gap)
        self.assertTrue(quarantined.score_coordinator.quarantined)
        quarantined, rows = api.reduce_live_paper_input(quarantined, _l2_input(api, 1, 3_000_000_000, "1" * 64))
        self.assertNotIn("action", tuple(record.kind.value for record in rows))
        self.assertIn("abstention", tuple(record.kind.value for record in gap_rows))
        self.assertIn("rejection", tuple(record.kind.value for record in rows))

        pending, _ = api.reduce_live_paper_input(anchored, _l2_input(api, 1, 3_000_000_000, "3" * 64))
        self.assertIsNotNone(pending.portfolio.pending_action)
        pending, invalidation_rows = api.reduce_live_paper_input(pending, gap)
        self.assertIsNone(pending.portfolio.pending_action)
        self.assertIn("rejection", tuple(record.kind.value for record in invalidation_rows))
        pending, rows = api.reduce_live_paper_input(pending, _l2_input(api, 2, 4_100_000_000, "4" * 64))
        self.assertNotIn("fill", tuple(record.kind.value for record in rows))

        stale, rows = api.reduce_live_paper_input(anchored, _l2_input(api, 1, 8_000_000_001, "2" * 64))
        self.assertNotIn("action", tuple(record.kind.value for record in rows))
        self.assertEqual(rows[-1].payload.body.reason, "score_stale")

    def test_stale_score_invalidates_pending_buy_before_later_book_can_fill(self) -> None:
        """Catches a delayed BUY filling after its score authority has expired."""
        api = _api()
        state = api.open_live_paper_session(_config(api))
        state, score_rows = api.reduce_live_paper_input(state, _score_input(api))
        state, action_rows = api.reduce_live_paper_input(
            state, _l2_input(api, 1, 3_000_000_000, "1" * 64)
        )
        self.assertIsNotNone(state.portfolio.pending_action)
        state, stale_rows = api.reduce_live_paper_input(
            state, _l2_input(api, 2, 9_000_000_000, "2" * 64)
        )
        self.assertIsNone(state.portfolio.pending_action)
        self.assertIsNone(state.portfolio.position)
        self.assertNotIn("fill", tuple(record.kind.value for record in stale_rows))
        self.assertEqual(stale_rows[-1].kind.value, "rejection")
        self.assertEqual(stale_rows[-1].payload.body.reason, "score_stale")
        raw = api.encode_live_paper_records(score_rows + action_rows + stale_rows)
        self.assertEqual(api.replay_live_paper_records(raw).state, state)

    def test_generation_rollover_invalidates_and_reissues_pending_action_causally(self) -> None:
        """Catches an old-generation action blocking the paper portfolio forever."""
        api = _api()
        state = api.open_live_paper_session(_config(api))
        state, _ = api.reduce_live_paper_input(state, _score_input(api))
        state, _ = api.reduce_live_paper_input(
            state, _l2_input(api, 1, 3_000_000_000, "1" * 64)
        )
        old_action = state.portfolio.pending_action
        self.assertIsNotNone(old_action)
        reconnect = _l2_input(api, 1, 3_500_000_000, "2" * 64)
        reconnect = replace(
            reconnect,
            frame=replace(
                reconnect.frame,
                physical_connection_generation=2,
                subscription_id=3,
            ),
        )
        state, rows = api.reduce_live_paper_input(state, reconnect)
        self.assertNotIn("fill", tuple(row.kind.value for row in rows))
        self.assertTrue(
            any(
                row.kind.value == "rejection"
                and row.payload.body.reason == "book_generation_changed"
                for row in rows
            )
        )
        self.assertIsNotNone(state.portfolio.pending_action)
        self.assertNotEqual(state.portfolio.pending_action, old_action)
        self.assertEqual(state.portfolio.pending_action.decision_generation, 2)

    def test_l2_parent_receipt_generation_must_match_the_paper_frame(self) -> None:
        """Catches a collector-local generation masquerading as session authority."""
        api = _api()
        item = _l2_input(api, 1, 3_000_000_000, "1" * 64)
        parent_generation = 2
        parent_digest = api.compute_live_paper_parent_receipt_sha256(
            source_kind="shadow_kalshi_capture",
            capture_id="/tmp/kalshi-generation-2.bin",
            raw_reference="/tmp/kalshi-generation-2.bin",
            raw_sha256=item.frame.raw_parent_receipt_sha256,
            durable_receipt_sha256=SHA_B,
            captured_wall_ns=item.frame.captured_wall_ns,
            captured_monotonic_ns=item.frame.captured_monotonic_ns,
            clock_uncertainty_ns=item.frame.clock_uncertainty_ns,
            physical_connection_generation=parent_generation,
        )
        parent = api.LivePaperDurableParentReceipt(
            "shadow_kalshi_capture",
            "/tmp/kalshi-generation-2.bin",
            "/tmp/kalshi-generation-2.bin",
            item.frame.raw_parent_receipt_sha256,
            SHA_B,
            parent_digest,
            item.frame.captured_wall_ns,
            item.frame.captured_monotonic_ns,
            item.frame.clock_uncertainty_ns,
            parent_generation,
        )

        with self.assertRaises(api.LivePaperSessionError):
            replace(item, durable_parent_receipt=parent)

    def test_book_capture_must_follow_score_even_when_observed_later(self) -> None:
        """Catches an old decision frame being relabelled as causal by a later observation clock."""
        api = _api()
        state = api.open_live_paper_session(_config(api))
        state, score_rows = api.reduce_live_paper_input(state, _score_input(api))
        state, rejected = api.reduce_live_paper_input(
            state,
            _captured_l2_input(api, 1, 1_500_000_000, 3_000_000_000, "1" * 64),
        )
        self.assertIsNone(state.portfolio.pending_action)
        self.assertEqual(rejected[-1].payload.body.reason, "book_precedes_score")
        state, accepted = api.reduce_live_paper_input(
            state,
            _captured_l2_input(api, 2, 2_100_000_000, 3_100_000_000, "2" * 64),
        )
        self.assertIsNotNone(state.portfolio.pending_action)
        self.assertIn("action", tuple(record.kind.value for record in accepted))
        raw = api.encode_live_paper_records(score_rows + rejected + accepted)
        self.assertEqual(api.replay_live_paper_records(raw).state, state)

    def test_unchanged_score_clock_ignores_future_observation_rejected_by_coordinator(self) -> None:
        """Catches an ignored capture poisoning the causal score clock and invalidating entry."""
        api = _api()
        state = api.open_live_paper_session(_config(api))
        state, anchor_rows = api.reduce_live_paper_input(state, _score_input(api))
        original = _score_input(api).observations[0]
        fresh = replace(
            original,
            raw_receipt_sha256="5" * 64,
            captured_wall_ns=3_000_000_000,
            captured_monotonic_ns=3_000_000_000,
        )
        ignored_future = replace(
            original,
            source_id="ignored-future",
            raw_receipt_sha256="6" * 64,
            captured_wall_ns=99_000_000_000,
            captured_monotonic_ns=99_000_000_000,
        )
        state, unchanged_rows = api.reduce_live_paper_input(
            state,
            api.LivePaperScoreBatchInput(
                (fresh, ignored_future),
                3_000_000_000,
                3_000_000_000,
            ),
        )
        self.assertEqual(state.last_score_monotonic_ns, 3_000_000_000)
        state, book_rows = api.reduce_live_paper_input(
            state,
            _captured_l2_input(api, 1, 3_100_000_000, 3_100_000_000, "7" * 64),
        )
        self.assertIn("action", tuple(record.kind.value for record in book_rows))
        raw = api.encode_live_paper_records(anchor_rows + unchanged_rows + book_rows)
        self.assertEqual(api.replay_live_paper_records(raw).state, state)

    def test_consensus_clock_skew_cannot_extend_score_actionability(self) -> None:
        """Catches component-wise maxima creating a young clock no source observed."""
        api = _api()
        state = api.open_live_paper_session(_consensus_config(api))

        state, score_rows = api.reduce_live_paper_input(
            state,
            _skewed_consensus_score_input(api),
        )
        state, preceding_rows = api.reduce_live_paper_input(
            state,
            _captured_l2_input(
                api,
                1,
                9_000_000_000,
                10_000_000_000,
                "6" * 64,
            ),
        )
        state, book_rows = api.reduce_live_paper_input(
            state,
            _l2_input(api, 2, 14_000_000_000, "7" * 64),
        )

        self.assertEqual(state.last_score_wall_ns, 10_000_000_000)
        self.assertEqual(state.last_score_monotonic_ns, 10_000_000_000)
        self.assertEqual(state.last_score_clock_uncertainty_ns, 5_000_000_000)
        self.assertEqual(
            preceding_rows[-1].payload.body.reason,
            "book_precedes_score",
        )
        self.assertIsNone(state.portfolio.pending_action)
        self.assertEqual(book_rows[-1].payload.body.reason, "score_stale")
        self.assertNotIn("action", tuple(record.kind.value for record in book_rows))
        raw = api.encode_live_paper_records(score_rows + preceding_rows + book_rows)
        self.assertEqual(api.replay_live_paper_records(raw).state, state)

    def test_unchanged_consensus_refresh_uses_conservative_clock_basis(self) -> None:
        """Catches unchanged score refresh synthesizing the same young clock pair."""
        api = _api()
        state = api.open_live_paper_session(_consensus_config(api))
        state, anchor_rows = api.reduce_live_paper_input(state, _score_input(api))

        state, unchanged_rows = api.reduce_live_paper_input(
            state,
            _skewed_consensus_score_input(api),
        )
        state, book_rows = api.reduce_live_paper_input(
            state,
            _l2_input(api, 1, 14_000_000_000, "7" * 64),
        )

        self.assertEqual(state.last_score_wall_ns, 10_000_000_000)
        self.assertEqual(state.last_score_monotonic_ns, 10_000_000_000)
        self.assertEqual(state.last_score_clock_uncertainty_ns, 5_000_000_000)
        self.assertIsNone(state.portfolio.pending_action)
        self.assertEqual(book_rows[-1].payload.body.reason, "score_stale")
        self.assertNotIn("action", tuple(record.kind.value for record in book_rows))
        raw = api.encode_live_paper_records(
            anchor_rows + unchanged_rows + book_rows
        )
        self.assertEqual(api.replay_live_paper_records(raw).state, state)

    def test_consensus_rebase_clock_skew_cannot_extend_score_actionability(self) -> None:
        """Catches a rebase anchor carrying the same synthetic young clock."""
        api = _api()
        state = api.open_live_paper_session(_consensus_config(api))
        state, anchor_rows = api.reduce_live_paper_input(state, _score_input(api))
        gap = _advanced_observation(api, 2)
        state, quarantine_rows = api.reduce_live_paper_input(
            state,
            api.LivePaperScoreBatchInput(
                (gap,),
                3_000_000_000,
                3_000_000_000,
            ),
        )
        self.assertTrue(state.score_coordinator.quarantined)

        state, rebase_rows = api.reduce_live_paper_input(
            state,
            _skewed_consensus_score_input(api),
        )
        state, book_rows = api.reduce_live_paper_input(
            state,
            _l2_input(api, 1, 14_000_000_000, "7" * 64),
        )

        self.assertFalse(state.score_coordinator.quarantined)
        self.assertEqual(state.score_coordinator.rebase_epoch, 1)
        self.assertEqual(state.last_score_wall_ns, 10_000_000_000)
        self.assertEqual(state.last_score_monotonic_ns, 10_000_000_000)
        self.assertEqual(state.last_score_clock_uncertainty_ns, 5_000_000_000)
        self.assertIsNone(state.portfolio.pending_action)
        self.assertEqual(book_rows[-1].payload.body.reason, "score_stale")
        self.assertNotIn("action", tuple(record.kind.value for record in book_rows))
        raw = api.encode_live_paper_records(
            anchor_rows + quarantine_rows + rebase_rows + book_rows
        )
        self.assertEqual(api.replay_live_paper_records(raw).state, state)

    def test_integrity_rejections_cover_shape_chain_terminal_and_truncation(self) -> None:
        """Catches permissive parsing of ambiguous, altered, or incomplete evidence."""
        api = _api()
        state, records = _complete_session(api)
        raw = api.encode_live_paper_records(records)
        rows = [json.loads(line) for line in raw.splitlines()]
        mutations: list[bytes] = []
        extra = dict(rows[0]); extra["unknown"] = 1
        mutations.append(json.dumps(extra, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        gap = [dict(row) for row in rows]; gap[1]["record_ordinal"] = 99
        mutations.append(b"\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() for row in gap) + b"\n")
        reordered = rows[:]; reordered[0], reordered[1] = reordered[1], reordered[0]
        mutations.append(b"\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() for row in reordered) + b"\n")
        bad_parent = [dict(row) for row in rows]; bad_parent[1]["previous_record_sha256"] = "0" * 64
        mutations.append(b"\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() for row in bad_parent) + b"\n")
        wrong_json_type = [dict(row) for row in rows]; wrong_json_type[0]["version"] = 1.0
        mutations.append(b"\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() for row in wrong_json_type) + b"\n")
        duplicate_key = raw.splitlines()[0][:-1] + b',"kind":"terminal"}\n'
        mutations.append(duplicate_key)
        mutations.append(raw[:-3])
        for mutation in mutations:
            with self.subTest(mutation=mutation[:80]):
                with self.assertRaises(api.LivePaperSessionError):
                    api.replay_live_paper_records(mutation)
        with self.assertRaises(api.LivePaperSessionError):
            api.replay_live_paper_records(api.encode_live_paper_records(records[:-1]), require_terminal=True)
        with self.assertRaises(api.LivePaperSessionError):
            api.reduce_live_paper_input(state, api.LivePaperHeartbeatInput(123_000_000_000, 123_000_000_000))

    def test_v1_codec_registry_does_not_auto_enroll_unrelated_module_types(self) -> None:
        """Catches a newly imported dataclass silently becoming replay-deserializable."""
        api = _api()
        state = _score_state()
        unrelated = ProviderPoint(
            provider_source_id=state.provider_source_id,
            revision_domain_id=state.revision_domain_id,
            source_lineage_sha256=state.source_lineage_sha256,
            provider_event_id="unrelated",
            provider_match_id=state.provider_match_id,
            home_player_id=state.home_player_id,
            away_player_id=state.away_player_id,
            scheduled_start_wall_ns=state.scheduled_start_wall_ns,
            match_format=state.match_format,
            correction_epoch=0,
            revision=2,
            point_winner=PlayerSide.HOME,
            server_before_point=PlayerSide.HOME,
            source_wall_ns=2_000_000_001,
            source_generated_wall_ns=2_000_000_001,
            received_monotonic_ns=2_000_000_001,
            clock_uncertainty_ns=0,
        )
        with self.assertRaisesRegex(api.LivePaperSessionError, "unknown_type"):
            api._unproject(api._project(unrelated))

    def test_untrusted_json_is_bounded_and_parser_failures_are_normalized(self) -> None:
        """Catches replay allocating or recursing without fixed codec limits."""
        api = _api()
        with self.assertRaisesRegex(api.LivePaperSessionError, "log_too_large"):
            api.replay_live_paper_records(b" " * (32 * 1024 * 1024 + 1) + b"\n")
        with self.assertRaisesRegex(api.LivePaperSessionError, "json_depth"):
            api._parse_json(b"[" * 2_000 + b"0" + b"]" * 2_000)
        oversized_collection = json.dumps(list(range(10_001)), separators=(",", ":")).encode("ascii")
        with self.assertRaisesRegex(api.LivePaperSessionError, "json_collection"):
            api._parse_json(oversized_collection)
        oversized_key = b'{"' + b"a" * 4_097 + b'":0}'
        with self.assertRaisesRegex(api.LivePaperSessionError, "json_key"):
            api._parse_json(oversized_key)

    def test_replay_rejects_rehashed_forged_derived_rows(self) -> None:
        """Catches a valid hash chain substituting persisted output for causal recomputation."""
        api = _api()
        _, records = _complete_session(api)
        mutations = {
            "forecast": lambda body: replace(body, forecast_label="FORGED"),
            "action": lambda body: replace(body, decision_global_sequence=body.decision_global_sequence + 10),
            "fill": lambda body: replace(
                body,
                fill=replace(body.fill, debit_or_credit=body.fill.debit_or_credit + Decimal(".01")),
            ),
            "mark": lambda body: replace(body, gross_credit=body.gross_credit + Decimal(".01")),
        }
        for kind, mutate in mutations.items():
            target = next(index for index, record in enumerate(records) if record.kind.value == kind)
            previous = "0" * 64
            forged_records = []
            for index, original in enumerate(records):
                payload = original.payload
                if index == target:
                    payload = type(payload)(payload.config, mutate(payload.body))
                projection = api._project(payload)
                payload_sha = api._digest(api._canonical_json(projection))
                record_sha = api._record_digest(
                    record_ordinal=index + 1,
                    previous_record_sha256=previous,
                    kind=original.kind,
                    payload_projection=projection,
                    payload_sha256=payload_sha,
                )
                forged = api.LivePaperRecord(
                    original.schema,
                    original.version,
                    index + 1,
                    previous,
                    original.kind,
                    payload,
                    payload_sha,
                    record_sha,
                )
                forged_records.append(forged)
                previous = record_sha
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(api.LivePaperSessionError, "replay_mismatch"):
                    api.replay_live_paper_records(api.encode_live_paper_records(tuple(forged_records)))

    def test_producer_rejects_oversized_causal_inputs_before_emission(self) -> None:
        """Catches a local score batch or payload producing evidence replay cannot decode."""
        api = _api()
        observation = _score_input(api).observations[0]
        with self.assertRaisesRegex(api.LivePaperSessionError, "input_collection"):
            api.LivePaperScoreBatchInput(
                (observation,) * 10_001,
                2_000_000_000,
                2_000_000_000,
            )

        state = api.open_live_paper_session(_config(api))
        oversized = api.LivePaperTerminalInput(
            "x" * (1024 * 1024 + 1),
            2_000_000_000,
            2_000_000_000,
        )
        with self.assertRaisesRegex(api.LivePaperSessionError, "json_string"):
            api.reduce_live_paper_input(state, oversized)

        deeply_nested: object = "leaf"
        for _ in range(2_000):
            deeply_nested = (deeply_nested,)
        with self.assertRaisesRegex(api.LivePaperSessionError, "json_depth"):
            api._make_record(
                state,
                api.LivePaperRecordKind.REJECTION,
                deeply_nested,
            )
        usable, rows = api.reduce_live_paper_input(
            state,
            api.LivePaperTerminalInput("stop", 2_000_000_000, 2_000_000_000),
        )
        self.assertEqual(rows[0].record_ordinal, 1)
        self.assertTrue(usable.terminal)

    def test_reducer_rejects_append_before_session_log_limit(self) -> None:
        """Catches a reducer advancing state for a chain its encoder must reject."""
        api = _api()
        observation = _score_input(api).observations[0]
        large_input = api.LivePaperScoreBatchInput(
            (observation,) * 2_500,
            2_000_000_000,
            2_000_000_000,
        )
        state = api.open_live_paper_session(_config(api))
        records = ()
        for _ in range(5):
            state, emitted = api.reduce_live_paper_input(state, large_input)
            records += emitted
            encoded = api.encode_live_paper_records(records)
            self.assertEqual(api.replay_live_paper_records(encoded).state, state)

        self.assertGreater(len(encoded), 28 * 1024 * 1024)
        before = (
            state,
            state.record_head_sha256,
            state.record_count,
            state.encoded_log_bytes,
        )
        with self.assertRaisesRegex(api.LivePaperSessionError, "log_too_large"):
            api.reduce_live_paper_input(state, large_input)
        self.assertEqual(state.encoded_log_bytes, len(encoded))
        self.assertEqual(
            (
                state,
                state.record_head_sha256,
                state.record_count,
                state.encoded_log_bytes,
            ),
            before,
        )
        self.assertEqual(api.replay_live_paper_records(encoded).state, state)

    def test_producer_bounds_record_count_and_checkpoint_tree(self) -> None:
        """Catches encoders returning oversized record sets or checkpoint trees."""
        api = _api()
        initial = api.open_live_paper_session(_config(api))
        _, one = api.reduce_live_paper_input(
            initial,
            api.LivePaperTerminalInput("stop", 2_000_000_000, 2_000_000_000),
        )
        with self.assertRaisesRegex(api.LivePaperSessionError, "record_count"):
            api.encode_live_paper_records(one * 100_001)

        completed, _ = _complete_session(api)
        consumed = completed.portfolio.consumed_depth[0]
        oversized_state = replace(
            completed,
            portfolio=replace(
                completed.portfolio,
                consumed_depth=(consumed,) * 10_001,
            ),
        )
        with self.assertRaisesRegex(api.LivePaperSessionError, "json_collection"):
            api.encode_live_paper_checkpoint(oversized_state)

    def test_every_successful_producer_blob_round_trips(self) -> None:
        """Catches producer/decoder limits drifting for accepted evidence."""
        api = _api()
        with self.assertRaisesRegex(api.LivePaperSessionError, "records"):
            api.encode_live_paper_records(())
        state, records = _complete_session(api)
        log = api.encode_live_paper_records(records)
        self.assertEqual(api.replay_live_paper_records(log, require_terminal=True).state, state)
        checkpoint = api.encode_live_paper_checkpoint(state)
        self.assertEqual(api.decode_live_paper_checkpoint(checkpoint), state)

    def test_heartbeat_advances_only_by_complete_sixty_second_intervals(self) -> None:
        """Catches early heartbeat output or schedule drift after a late tick."""
        api = _api()
        state = api.open_live_paper_session(_config(api))
        unchanged, emitted = api.reduce_live_paper_input(state, api.LivePaperHeartbeatInput(60_999_999_999, 60_999_999_999))
        self.assertEqual(unchanged, state)
        self.assertFalse(emitted)
        advanced, emitted = api.reduce_live_paper_input(state, api.LivePaperHeartbeatInput(181_000_000_000, 181_000_000_000))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(advanced.next_heartbeat_monotonic_ns, 241_000_000_000)
        rollback, emitted = api.reduce_live_paper_input(state, api.LivePaperHeartbeatInput(0, 61_000_000_000))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(rollback.next_heartbeat_monotonic_ns, 121_000_000_000)


if __name__ == "__main__":
    unittest.main()
