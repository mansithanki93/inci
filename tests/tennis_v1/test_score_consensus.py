from __future__ import annotations

from dataclasses import replace
import importlib
import unittest

from inci_tennis_expert.contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ScoreValue,
    SetScore,
    TennisState,
    TennisTransitionReason,
    TerminationKind,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
DEFAULT_INDEPENDENCE_LINEAGE = object()


def tennis_state(**changes: object) -> TennisState:
    values: dict[str, object] = {
        "provider_source_id": "primary",
        "revision_domain_id": "primary-revisions",
        "source_lineage_sha256": SHA_A,
        "provider_match_id": "primary-match",
        "home_player_id": "player-home",
        "away_player_id": "player-away",
        "scheduled_start_wall_ns": 1_000,
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
        "correction_epoch": 1,
        "revision": 7,
        "snapshot_complete": True,
        "last_provider_event_id": "primary-event-7",
        "last_event_semantic_sha256": SHA_B,
        "correction_lineage_sha256": SHA_C,
        "last_source_wall_ns": 2_000,
        "last_source_generated_wall_ns": 1_990,
        "last_received_monotonic_ns": 100,
        "last_clock_uncertainty_ns": 2,
        "block_reason": None,
        "expected_revision": None,
        "observed_revision": None,
        "blocked_event_semantic_sha256": None,
        "blocked_received_monotonic_ns": None,
    }
    values.update(changes)
    return TennisState(**values)  # type: ignore[arg-type]


def witness_state(primary: TennisState, **changes: object) -> TennisState:
    values: dict[str, object] = {
        "provider_source_id": "witness",
        "revision_domain_id": "witness-revisions",
        "source_lineage_sha256": SHA_D,
        "provider_match_id": "witness-match",
        "last_provider_event_id": "witness-event-7",
        "last_event_semantic_sha256": SHA_D,
        "correction_lineage_sha256": SHA_D,
        "last_source_wall_ns": 2_003,
        "last_source_generated_wall_ns": 1_995,
        "last_received_monotonic_ns": 103,
    }
    values.update(changes)
    return replace(primary, **values)


def consensus_api() -> object:
    try:
        module = importlib.import_module("inci_tennis_expert.score_consensus")
    except ModuleNotFoundError as error:
        raise AssertionError("score consensus API is missing") from error
    required = (
        "ConsensusReason",
        "ScoreConsensusPolicy",
        "ScoreSourceConfig",
        "reduce_score_consensus",
    )
    missing = tuple(name for name in required if not hasattr(module, name))
    if missing:
        raise AssertionError(f"score consensus API is missing {missing!r}")
    return module


def score_source_config(
    api: object,
    source_id: object,
    source_lineage_sha256: object,
    max_age_ns: object,
    *,
    independence_lineage_id: object = DEFAULT_INDEPENDENCE_LINEAGE,
    provider_match_id: object | None = None,
    provider_home_player_id: object = "player-home",
    provider_away_player_id: object = "player-away",
    canonical_home_player_id: object = "player-home",
    canonical_away_player_id: object = "player-away",
) -> object:
    if independence_lineage_id is DEFAULT_INDEPENDENCE_LINEAGE:
        independence_lineage_id = source_lineage_sha256
    if provider_match_id is None:
        provider_match_id = (
            "primary-match" if source_id == "primary" else "witness-match"
        )
    constructor = getattr(api, "ScoreSourceConfig")
    return constructor(
        source_id,
        source_lineage_sha256,
        max_age_ns,
        independence_lineage_id=independence_lineage_id,
        provider_match_id=provider_match_id,
        provider_home_player_id=provider_home_player_id,
        provider_away_player_id=provider_away_player_id,
        canonical_home_player_id=canonical_home_player_id,
        canonical_away_player_id=canonical_away_player_id,
    )


class ScoreConsensusTests(unittest.TestCase):
    def test_accepts_primary_with_matching_independent_witness(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        witness = witness_state(primary)
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(
                    api,
                    "primary",
                    SHA_A,
                    20,
                    independence_lineage_id="provider-a",
                ),
                score_source_config(
                    api,
                    "witness",
                    SHA_D,
                    20,
                    independence_lineage_id="provider-b",
                ),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIs(result.accepted_state, primary)
        self.assertIs(result.reason, api.ConsensusReason.ACCEPTED)
        self.assertEqual(
            result.supporting_source_ids,
            ("primary", "witness"),
        )
        self.assertEqual(
            result.supporting_lineages,
            ("provider-a", "provider-b"),
        )

    def test_unknown_lineages_cannot_supply_independent_support(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        witness = witness_state(primary)
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(
                    api,
                    "primary",
                    SHA_A,
                    20,
                    independence_lineage_id=None,
                ),
                score_source_config(
                    api,
                    "witness",
                    SHA_D,
                    20,
                    independence_lineage_id=None,
                ),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(
            result.reason.value,
            "insufficient_distinct_lineages",
        )
        self.assertEqual(result.supporting_source_ids, ())
        self.assertEqual(result.supporting_lineages, ())

    def test_unknown_primary_lineage_cannot_be_confirmed_independent(
        self,
    ) -> None:
        api = consensus_api()
        primary = tennis_state()
        witness_a = witness_state(
            primary,
            provider_source_id="witness-a",
            source_lineage_sha256=SHA_B,
        )
        witness_b = witness_state(
            primary,
            provider_source_id="witness-b",
            revision_domain_id="witness-b-revisions",
            source_lineage_sha256=SHA_D,
        )
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(
                    api,
                    "primary",
                    SHA_A,
                    20,
                    independence_lineage_id=None,
                ),
                score_source_config(
                    api,
                    "witness-a",
                    SHA_B,
                    20,
                    independence_lineage_id="provider-b",
                ),
                score_source_config(
                    api,
                    "witness-b",
                    SHA_D,
                    20,
                    independence_lineage_id="provider-c",
                ),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {
                "primary": primary,
                "witness-a": witness_a,
                "witness-b": witness_b,
            },
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(
            result.reason.value,
            "insufficient_distinct_lineages",
        )

    def test_source_local_players_map_to_common_canonical_orientation(
        self,
    ) -> None:
        api = consensus_api()
        primary = tennis_state(
            provider_match_id="source-a-match",
            home_player_id="source-a-home",
            away_player_id="source-a-away",
        )
        witness = witness_state(
            primary,
            provider_match_id="source-b-match",
            home_player_id="source-b-home",
            away_player_id="source-b-away",
        )
        try:
            primary_config = score_source_config(
                api,
                "primary",
                SHA_A,
                20,
                provider_match_id="source-a-match",
                provider_home_player_id="source-a-home",
                provider_away_player_id="source-a-away",
                canonical_home_player_id="canonical-home",
                canonical_away_player_id="canonical-away",
            )
            witness_config = score_source_config(
                api,
                "witness",
                SHA_D,
                20,
                provider_match_id="source-b-match",
                provider_home_player_id="source-b-home",
                provider_away_player_id="source-b-away",
                canonical_home_player_id="canonical-home",
                canonical_away_player_id="canonical-away",
            )
        except TypeError as error:
            self.fail(f"source identity mapping is unsupported: {error}")
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(primary_config, witness_config),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIs(result.accepted_state, primary)
        self.assertIs(result.reason, api.ConsensusReason.ACCEPTED)

    def test_known_mirror_lineage_does_not_supply_independent_support(
        self,
    ) -> None:
        api = consensus_api()
        primary = tennis_state()
        mirror = witness_state(primary, source_lineage_sha256=SHA_A)
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(
                    api,
                    "primary",
                    SHA_A,
                    20,
                    independence_lineage_id="shared-feed",
                ),
                score_source_config(
                    api,
                    "witness",
                    SHA_A,
                    20,
                    independence_lineage_id="shared-feed",
                ),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": mirror},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(
            result.reason.value,
            "insufficient_distinct_lineages",
        )
        self.assertEqual(result.supporting_source_ids, ())
        self.assertEqual(result.supporting_lineages, ())

    def test_distinct_provider_provenance_can_share_one_mirror_lineage(
        self,
    ) -> None:
        api = consensus_api()
        primary = tennis_state()
        mirror = witness_state(primary)
        try:
            primary_config = score_source_config(
                api,
                "primary",
                SHA_A,
                20,
                independence_lineage_id="shared-upstream",
            )
            mirror_config = score_source_config(
                api,
                "witness",
                SHA_D,
                20,
                independence_lineage_id="shared-upstream",
            )
        except TypeError as error:
            self.fail(f"mirror lineage configuration is unsupported: {error}")
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(primary_config, mirror_config),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": mirror},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(
            result.reason.value,
            "insufficient_distinct_lineages",
        )

    def test_two_witnesses_cannot_outvote_disagreeing_primary(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        witness_a = witness_state(
            primary,
            provider_source_id="witness-a",
            source_lineage_sha256=SHA_B,
            games_home=3,
        )
        witness_b = witness_state(
            primary,
            provider_source_id="witness-b",
            revision_domain_id="witness-b-revisions",
            source_lineage_sha256=SHA_D,
            games_home=3,
        )
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness-a", SHA_B, 20),
                score_source_config(api, "witness-b", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {
                "primary": primary,
                "witness-a": witness_a,
                "witness-b": witness_b,
            },
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(result.reason.value, "primary_quarantined")
        self.assertEqual(result.supporting_source_ids, ())
        self.assertEqual(result.supporting_lineages, ())

    def test_stale_primary_abstains_before_matching_witness(self) -> None:
        api = consensus_api()
        primary = tennis_state(last_received_monotonic_ns=100)
        witness = witness_state(primary, last_received_monotonic_ns=109)
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 5),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(result.reason.value, "primary_stale")

    def test_stale_only_witness_abstains_with_witness_reason(self) -> None:
        api = consensus_api()
        primary = tennis_state(last_received_monotonic_ns=109)
        witness = witness_state(primary, last_received_monotonic_ns=100)
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 5),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(result.reason.value, "witness_stale")

    def test_missing_primary_abstains_deterministically(self) -> None:
        api = consensus_api()
        template = tennis_state()
        witness = witness_state(template)
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        try:
            result = api.reduce_score_consensus(
                policy,
                {"witness": witness},
                now_monotonic_ns=110,
            )
        except KeyError as error:
            self.fail(f"missing primary raised instead of abstaining: {error}")

        self.assertIsNone(result.accepted_state)
        self.assertEqual(result.reason.value, "primary_missing")

    def test_incomplete_primary_abstains_deterministically(self) -> None:
        api = consensus_api()
        witness = witness_state(tennis_state())
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": None, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(result.reason.value, "primary_incomplete")

    def test_incomplete_only_witness_abstains_deterministically(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": None},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(result.reason.value, "witness_incomplete")

    def test_missing_only_witness_abstains_deterministically(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(result.reason.value, "witness_missing")

    def test_blocked_primary_abstains_before_matching_witness(self) -> None:
        api = consensus_api()
        primary = tennis_state(
            block_reason=TennisTransitionReason.PROVIDER_EVENT_CONFLICT,
            blocked_event_semantic_sha256=SHA_D,
            blocked_received_monotonic_ns=105,
        )
        witness = witness_state(
            primary,
            block_reason=None,
            blocked_event_semantic_sha256=None,
            blocked_received_monotonic_ns=None,
        )
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(result.reason.value, "primary_blocked")

    def test_blocked_only_witness_abstains_deterministically(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        witness = witness_state(
            primary,
            block_reason=TennisTransitionReason.PROVIDER_EVENT_CONFLICT,
            blocked_event_semantic_sha256=SHA_B,
            blocked_received_monotonic_ns=106,
        )
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(result.reason.value, "witness_blocked")

    def test_single_consensus_field_mismatch_abstains(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        witness = witness_state(
            primary,
            server_for_next_point=PlayerSide.AWAY,
        )
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(result.reason.value, "state_mismatch")
        self.assertEqual(result.supporting_source_ids, ())
        self.assertEqual(result.supporting_lineages, ())

    def test_unvalidated_source_local_orientation_abstains(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        witness = witness_state(
            primary,
            home_player_id="player-away",
            away_player_id="player-home",
        )
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(
            result.reason.value,
            "source_configuration_mismatch",
        )

    def test_policy_rejects_canonical_player_orientation_mismatch(self) -> None:
        api = consensus_api()

        with self.assertRaisesRegex(ValueError, "canonical_player_identity"):
            api.ScoreConsensusPolicy(
                primary_source_id="primary",
                sources=(
                    score_source_config(api, "primary", SHA_A, 20),
                    score_source_config(
                        api,
                        "witness",
                        SHA_D,
                        20,
                        canonical_home_player_id="player-away",
                        canonical_away_player_id="player-home",
                    ),
                ),
            )

    def test_scheduled_match_identity_mismatch_abstains(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        witness = witness_state(primary, scheduled_start_wall_ns=1_001)
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(result.reason.value, "state_mismatch")

    def test_match_format_mismatch_abstains(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        witness = witness_state(
            primary,
            match_format=MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS,
        )
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(result.reason.value, "state_mismatch")

    def test_lifecycle_coordinate_mismatch_abstains(self) -> None:
        api = consensus_api()
        ended = tennis_state(
            status=MatchStatus.ENDED,
            termination_kind=TerminationKind.NATURAL,
            winner=PlayerSide.HOME,
        )
        cases = (
            (
                "status",
                tennis_state(),
                witness_state(tennis_state(), status=MatchStatus.SUSPENDED),
            ),
            (
                "termination",
                ended,
                witness_state(
                    ended,
                    termination_kind=TerminationKind.RETIREMENT,
                    retired_side=PlayerSide.AWAY,
                ),
            ),
            (
                "winner",
                ended,
                witness_state(ended, winner=PlayerSide.AWAY),
            ),
        )
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        for label, primary, witness in cases:
            with self.subTest(label=label):
                result = api.reduce_score_consensus(
                    policy,
                    {"primary": primary, "witness": witness},
                    now_monotonic_ns=110,
                )

                self.assertIsNone(result.accepted_state)
                self.assertEqual(result.reason.value, "state_mismatch")

    def test_completed_sets_mismatch_abstains(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        witness = witness_state(
            primary,
            completed_sets=(SetScore(6, 3, None, None),),
        )
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(result.reason.value, "state_mismatch")

    def test_game_point_and_tiebreak_coordinate_mismatch_abstains(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        tiebreak_primary = tennis_state(
            points_home=ScoreValue.LOVE,
            in_tiebreak=True,
            tiebreak_points_home=3,
            tiebreak_points_away=2,
            tiebreak_first_server=PlayerSide.HOME,
        )
        cases = (
            ("away_games", primary, witness_state(primary, games_away=2)),
            (
                "home_points",
                primary,
                witness_state(primary, points_home=ScoreValue.THIRTY),
            ),
            (
                "away_points",
                primary,
                witness_state(primary, points_away=ScoreValue.FIFTEEN),
            ),
            (
                "tiebreak_mode",
                tiebreak_primary,
                witness_state(
                    tiebreak_primary,
                    in_tiebreak=False,
                    tiebreak_points_home=0,
                    tiebreak_points_away=0,
                    tiebreak_first_server=None,
                ),
            ),
            (
                "home_tiebreak_points",
                tiebreak_primary,
                witness_state(tiebreak_primary, tiebreak_points_home=4),
            ),
            (
                "away_tiebreak_points",
                tiebreak_primary,
                witness_state(tiebreak_primary, tiebreak_points_away=3),
            ),
            (
                "tiebreak_first_server",
                tiebreak_primary,
                witness_state(
                    tiebreak_primary,
                    tiebreak_first_server=PlayerSide.AWAY,
                ),
            ),
        )
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        for label, case_primary, witness in cases:
            with self.subTest(label=label):
                result = api.reduce_score_consensus(
                    policy,
                    {"primary": case_primary, "witness": witness},
                    now_monotonic_ns=110,
                )

                self.assertIsNone(result.accepted_state)
                self.assertEqual(result.reason.value, "state_mismatch")

    def test_source_local_revision_coordinates_do_not_block_consensus(
        self,
    ) -> None:
        api = consensus_api()
        primary = tennis_state()
        witness = witness_state(primary, correction_epoch=9, revision=42)
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIs(result.accepted_state, primary)
        self.assertEqual(result.accepted_state.correction_epoch, 1)
        self.assertEqual(result.accepted_state.revision, 7)
        self.assertIs(result.reason, api.ConsensusReason.ACCEPTED)

    def test_supporter_order_and_result_equality_are_deterministic(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        witness_a = witness_state(
            primary,
            provider_source_id="a-witness",
            source_lineage_sha256=SHA_B,
        )
        witness_z = witness_state(
            primary,
            provider_source_id="z-witness",
            revision_domain_id="z-witness-revisions",
            source_lineage_sha256=SHA_D,
        )
        primary_config = score_source_config(api, "primary", SHA_A, 20)
        witness_a_config = score_source_config(api, "a-witness", SHA_B, 20)
        witness_z_config = score_source_config(api, "z-witness", SHA_D, 20)
        policy_a = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(witness_z_config, primary_config, witness_a_config),
        )
        policy_b = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(witness_a_config, witness_z_config, primary_config),
        )

        result_a = api.reduce_score_consensus(
            policy_a,
            {
                "z-witness": witness_z,
                "primary": primary,
                "a-witness": witness_a,
            },
            now_monotonic_ns=110,
        )
        result_b = api.reduce_score_consensus(
            policy_b,
            {
                "a-witness": witness_a,
                "z-witness": witness_z,
                "primary": primary,
            },
            now_monotonic_ns=110,
        )

        self.assertEqual(result_a, result_b)
        self.assertEqual(
            result_a.supporting_source_ids,
            ("a-witness", "primary", "z-witness"),
        )
        self.assertEqual(
            result_a.supporting_lineages,
            (SHA_A, SHA_B, SHA_D),
        )

    def test_unconfigured_witness_identity_cannot_support_primary(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        witness = witness_state(
            primary,
            provider_source_id="spoofed-source",
            source_lineage_sha256=SHA_B,
        )
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(
            result.reason.value,
            "source_configuration_mismatch",
        )

    def test_unconfigured_primary_identity_cannot_be_accepted(self) -> None:
        api = consensus_api()
        primary = tennis_state(
            provider_source_id="unexpected-primary",
            source_lineage_sha256=SHA_B,
        )
        witness = witness_state(primary)
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "witness", SHA_D, 20),
            ),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary, "witness": witness},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(
            result.reason.value,
            "source_configuration_mismatch",
        )

    def test_primary_only_policy_has_insufficient_distinct_lineages(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(score_source_config(api, "primary", SHA_A, 20),),
        )

        result = api.reduce_score_consensus(
            policy,
            {"primary": primary},
            now_monotonic_ns=110,
        )

        self.assertIsNone(result.accepted_state)
        self.assertEqual(
            result.reason.value,
            "insufficient_distinct_lineages",
        )

    def test_policy_requires_exactly_one_configured_primary(self) -> None:
        api = consensus_api()
        cases = (
            (score_source_config(api, "witness", SHA_D, 20),),
            (
                score_source_config(api, "primary", SHA_A, 20),
                score_source_config(api, "primary", SHA_A, 20),
            ),
        )

        for sources in cases:
            with self.subTest(sources=sources), self.assertRaisesRegex(
                ValueError,
                "primary_source_id",
            ):
                api.ScoreConsensusPolicy(
                    primary_source_id="primary",
                    sources=sources,
                )

    def test_policy_rejects_duplicate_source_ids(self) -> None:
        api = consensus_api()

        with self.assertRaisesRegex(ValueError, "source_id"):
            api.ScoreConsensusPolicy(
                primary_source_id="primary",
                sources=(
                    score_source_config(api, "primary", SHA_A, 20),
                    score_source_config(api, "witness", SHA_D, 20),
                    score_source_config(api, "witness", SHA_D, 20),
                ),
            )

    def test_policy_requires_immutable_source_configuration(self) -> None:
        api = consensus_api()

        with self.assertRaisesRegex(TypeError, "sources"):
            api.ScoreConsensusPolicy(
                primary_source_id="primary",
                sources=[score_source_config(api, "primary", SHA_A, 20)],
            )

    def test_source_config_rejects_invalid_source_id(self) -> None:
        api = consensus_api()
        cases = (("", ValueError), (1, TypeError))

        for source_id, error_type in cases:
            with self.subTest(source_id=source_id), self.assertRaisesRegex(
                error_type,
                "source_id",
            ):
                score_source_config(api, source_id, SHA_A, 20)

    def test_source_config_requires_lowercase_sha256_lineage(self) -> None:
        api = consensus_api()
        cases = (("a" * 63, ValueError), ("A" * 64, ValueError), (1, TypeError))

        for lineage, error_type in cases:
            with self.subTest(lineage=lineage), self.assertRaisesRegex(
                error_type,
                "source_lineage_sha256",
            ):
                score_source_config(api, "source", lineage, 20)

    def test_source_config_requires_nonnegative_integer_freshness(self) -> None:
        api = consensus_api()
        cases = ((-1, ValueError), (True, TypeError), (1.0, TypeError))

        for max_age_ns, error_type in cases:
            with self.subTest(max_age_ns=max_age_ns), self.assertRaisesRegex(
                error_type,
                "max_age_ns",
            ):
                score_source_config(api, "source", SHA_A, max_age_ns)

    def test_source_config_rejects_invalid_independence_lineage_id(
        self,
    ) -> None:
        api = consensus_api()
        cases = (("", ValueError), (1, TypeError))

        for lineage_id, error_type in cases:
            with self.subTest(lineage_id=lineage_id), self.assertRaisesRegex(
                error_type,
                "independence_lineage_id",
            ):
                score_source_config(
                    api,
                    "source",
                    SHA_A,
                    20,
                    independence_lineage_id=lineage_id,
                )

    def test_source_config_requires_complete_identity_mapping(self) -> None:
        api = consensus_api()
        constructor = getattr(api, "ScoreSourceConfig")

        with self.assertRaisesRegex(ValueError, "provider_identity"):
            constructor("source", SHA_A, 20)


class StatefulScoreConsensusTests(unittest.TestCase):
    def policy(self, api: object) -> object:
        return api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(
                score_source_config(
                    api,
                    "primary",
                    SHA_A,
                    20,
                    independence_lineage_id="provider-a",
                ),
                score_source_config(
                    api,
                    "witness",
                    SHA_D,
                    20,
                    independence_lineage_id="provider-b",
                ),
            ),
        )

    def apply_pair(
        self,
        api: object,
        state: object,
        primary: TennisState,
        witness: TennisState,
        *,
        now: int,
    ) -> tuple[object, object]:
        return api.apply_score_consensus(
            state,
            self.policy(api),
            {"primary": primary, "witness": witness},
            now_monotonic_ns=now,
        )

    def test_joint_score_regression_is_quarantined(self) -> None:
        api = consensus_api()
        first = tennis_state(
            games_home=3,
            games_away=1,
            revision=8,
            last_received_monotonic_ns=110,
        )
        first_witness = witness_state(
            first,
            revision=80,
            last_received_monotonic_ns=111,
        )
        state, accepted = self.apply_pair(
            api,
            api.initial_score_consensus_state(),
            first,
            first_witness,
            now=115,
        )
        regressed = replace(
            first,
            games_home=2,
            revision=9,
            last_received_monotonic_ns=120,
        )
        regressed_witness = witness_state(
            regressed,
            revision=81,
            last_received_monotonic_ns=121,
        )

        next_state, rejected = self.apply_pair(
            api,
            state,
            regressed,
            regressed_witness,
            now=125,
        )

        self.assertIs(accepted.reason, api.ConsensusReason.ACCEPTED)
        self.assertIs(
            rejected.reason,
            api.ConsensusReason.TRANSITION_REGRESSION,
        )
        self.assertIs(next_state.accepted_state, first)
        self.assertTrue(next_state.quarantined)

    def test_joint_point_regression_within_one_game_is_quarantined(
        self,
    ) -> None:
        api = consensus_api()
        first = tennis_state(
            points_home=ScoreValue.FORTY,
            points_away=ScoreValue.THIRTY,
            revision=8,
            last_received_monotonic_ns=110,
        )
        state, _ = self.apply_pair(
            api,
            api.initial_score_consensus_state(),
            first,
            witness_state(
                first,
                revision=80,
                last_received_monotonic_ns=111,
            ),
            now=115,
        )
        regressed = replace(
            first,
            points_home=ScoreValue.FIFTEEN,
            points_away=ScoreValue.LOVE,
            revision=9,
            last_received_monotonic_ns=120,
        )

        next_state, rejected = self.apply_pair(
            api,
            state,
            regressed,
            witness_state(
                regressed,
                revision=81,
                last_received_monotonic_ns=121,
            ),
            now=125,
        )

        self.assertIs(
            rejected.reason,
            api.ConsensusReason.TRANSITION_REGRESSION,
        )
        self.assertIs(next_state.accepted_state, first)
        self.assertTrue(next_state.quarantined)

    def test_game_transition_requires_server_to_alternate(self) -> None:
        api = consensus_api()
        prior = tennis_state(
            completed_sets=(),
            games_home=0,
            games_away=0,
            points_home=ScoreValue.FORTY,
            points_away=ScoreValue.LOVE,
            server_for_next_point=PlayerSide.HOME,
            revision=8,
            last_received_monotonic_ns=110,
        )
        state, _ = self.apply_pair(
            api,
            api.initial_score_consensus_state(),
            prior,
            witness_state(prior, revision=80),
            now=115,
        )
        impossible = replace(
            prior,
            games_home=1,
            points_home=ScoreValue.LOVE,
            server_for_next_point=PlayerSide.HOME,
            revision=9,
            last_received_monotonic_ns=120,
        )

        next_state, rejected = self.apply_pair(
            api,
            state,
            impossible,
            witness_state(
                impossible,
                revision=81,
                last_received_monotonic_ns=121,
            ),
            now=125,
        )

        self.assertIs(
            rejected.reason,
            api.ConsensusReason.TRANSITION_REGRESSION,
        )
        self.assertIs(next_state.accepted_state, prior)
        self.assertTrue(next_state.quarantined)

    def test_multi_game_jump_is_quarantined_even_when_server_cycles(
        self,
    ) -> None:
        api = consensus_api()
        prior = tennis_state(
            completed_sets=(),
            games_home=0,
            games_away=0,
            points_home=ScoreValue.LOVE,
            points_away=ScoreValue.LOVE,
            server_for_next_point=PlayerSide.HOME,
            revision=8,
            last_received_monotonic_ns=110,
        )
        state, _ = self.apply_pair(
            api,
            api.initial_score_consensus_state(),
            prior,
            witness_state(prior, revision=80),
            now=115,
        )
        two_games_later = replace(
            prior,
            games_home=1,
            games_away=1,
            revision=9,
            last_received_monotonic_ns=120,
        )

        next_state, rejected = self.apply_pair(
            api,
            state,
            two_games_later,
            witness_state(
                two_games_later,
                revision=81,
                last_received_monotonic_ns=121,
            ),
            now=125,
        )

        self.assertIs(
            rejected.reason,
            api.ConsensusReason.TRANSITION_REGRESSION,
        )
        self.assertIs(next_state.accepted_state, prior)

    def test_point_reset_without_a_completed_game_is_quarantined(
        self,
    ) -> None:
        api = consensus_api()
        prior = tennis_state(
            points_home=ScoreValue.FORTY,
            points_away=ScoreValue.THIRTY,
            revision=8,
            last_received_monotonic_ns=110,
        )
        state, _ = self.apply_pair(
            api,
            api.initial_score_consensus_state(),
            prior,
            witness_state(prior, revision=80),
            now=115,
        )
        impossible = replace(
            prior,
            points_home=ScoreValue.LOVE,
            points_away=ScoreValue.LOVE,
            revision=9,
            last_received_monotonic_ns=120,
        )

        next_state, rejected = self.apply_pair(
            api,
            state,
            impossible,
            witness_state(
                impossible,
                revision=81,
                last_received_monotonic_ns=121,
            ),
            now=125,
        )

        self.assertIs(
            rejected.reason,
            api.ConsensusReason.TRANSITION_REGRESSION,
        )
        self.assertIs(next_state.accepted_state, prior)

    def test_mid_game_server_change_is_quarantined(self) -> None:
        api = consensus_api()
        prior = tennis_state(
            points_home=ScoreValue.FIFTEEN,
            points_away=ScoreValue.LOVE,
            revision=8,
            last_received_monotonic_ns=110,
        )
        state, _ = self.apply_pair(
            api,
            api.initial_score_consensus_state(),
            prior,
            witness_state(prior, revision=80),
            now=115,
        )
        impossible = replace(
            prior,
            points_home=ScoreValue.THIRTY,
            server_for_next_point=PlayerSide.AWAY,
            revision=9,
            last_received_monotonic_ns=120,
        )

        next_state, rejected = self.apply_pair(
            api,
            state,
            impossible,
            witness_state(
                impossible,
                revision=81,
                last_received_monotonic_ns=121,
            ),
            now=125,
        )

        self.assertIs(
            rejected.reason,
            api.ConsensusReason.TRANSITION_REGRESSION,
        )
        self.assertIs(next_state.accepted_state, prior)

    def test_tiebreak_transition_requires_exact_server_sequence(self) -> None:
        api = consensus_api()
        prior = tennis_state(
            completed_sets=(),
            games_home=6,
            games_away=6,
            points_home=ScoreValue.LOVE,
            points_away=ScoreValue.LOVE,
            in_tiebreak=True,
            tiebreak_points_home=4,
            tiebreak_points_away=3,
            tiebreak_first_server=PlayerSide.HOME,
            server_for_next_point=PlayerSide.HOME,
            revision=8,
            last_received_monotonic_ns=110,
        )
        state, _ = self.apply_pair(
            api,
            api.initial_score_consensus_state(),
            prior,
            witness_state(prior, revision=80),
            now=115,
        )
        wrong_server = replace(
            prior,
            tiebreak_points_away=4,
            server_for_next_point=PlayerSide.AWAY,
            revision=9,
            last_received_monotonic_ns=120,
        )

        next_state, rejected = self.apply_pair(
            api,
            state,
            wrong_server,
            witness_state(
                wrong_server,
                revision=81,
                last_received_monotonic_ns=121,
            ),
            now=125,
        )

        self.assertIs(
            rejected.reason,
            api.ConsensusReason.TRANSITION_REGRESSION,
        )
        self.assertIs(next_state.accepted_state, prior)

    def test_set_transition_requires_the_next_server(self) -> None:
        api = consensus_api()
        prior = tennis_state(
            completed_sets=(),
            games_home=5,
            games_away=4,
            points_home=ScoreValue.FORTY,
            points_away=ScoreValue.LOVE,
            server_for_next_point=PlayerSide.HOME,
            revision=8,
            last_received_monotonic_ns=110,
        )
        state, _ = self.apply_pair(
            api,
            api.initial_score_consensus_state(),
            prior,
            witness_state(prior, revision=80),
            now=115,
        )
        wrong_server = replace(
            prior,
            completed_sets=(SetScore(6, 4, None, None),),
            games_home=0,
            games_away=0,
            points_home=ScoreValue.LOVE,
            server_for_next_point=PlayerSide.HOME,
            revision=9,
            last_received_monotonic_ns=120,
        )

        next_state, rejected = self.apply_pair(
            api,
            state,
            wrong_server,
            witness_state(
                wrong_server,
                revision=81,
                last_received_monotonic_ns=121,
            ),
            now=125,
        )

        self.assertIs(
            rejected.reason,
            api.ConsensusReason.TRANSITION_REGRESSION,
        )
        self.assertIs(next_state.accepted_state, prior)

    def test_legal_point_game_set_and_tiebreak_transitions_advance(
        self,
    ) -> None:
        api = consensus_api()
        cases = (
            (
                tennis_state(
                    completed_sets=(),
                    games_home=0,
                    games_away=0,
                    points_home=ScoreValue.FIFTEEN,
                    revision=8,
                    last_received_monotonic_ns=110,
                ),
                {
                    "points_home": ScoreValue.THIRTY,
                },
            ),
            (
                tennis_state(
                    completed_sets=(),
                    games_home=0,
                    games_away=0,
                    points_home=ScoreValue.FORTY,
                    points_away=ScoreValue.LOVE,
                    revision=8,
                    last_received_monotonic_ns=110,
                ),
                {
                    "games_home": 1,
                    "points_home": ScoreValue.LOVE,
                    "server_for_next_point": PlayerSide.AWAY,
                },
            ),
            (
                tennis_state(
                    completed_sets=(),
                    games_home=5,
                    games_away=4,
                    points_home=ScoreValue.FORTY,
                    points_away=ScoreValue.LOVE,
                    revision=8,
                    last_received_monotonic_ns=110,
                ),
                {
                    "completed_sets": (SetScore(6, 4, None, None),),
                    "games_home": 0,
                    "games_away": 0,
                    "points_home": ScoreValue.LOVE,
                    "server_for_next_point": PlayerSide.AWAY,
                },
            ),
            (
                tennis_state(
                    completed_sets=(),
                    games_home=6,
                    games_away=6,
                    points_home=ScoreValue.LOVE,
                    points_away=ScoreValue.LOVE,
                    in_tiebreak=True,
                    tiebreak_points_home=4,
                    tiebreak_points_away=3,
                    tiebreak_first_server=PlayerSide.HOME,
                    server_for_next_point=PlayerSide.HOME,
                    revision=8,
                    last_received_monotonic_ns=110,
                ),
                {
                    "tiebreak_points_away": 4,
                    "server_for_next_point": PlayerSide.HOME,
                },
            ),
            (
                tennis_state(
                    completed_sets=(),
                    games_home=5,
                    games_away=6,
                    points_home=ScoreValue.FORTY,
                    points_away=ScoreValue.LOVE,
                    server_for_next_point=PlayerSide.HOME,
                    revision=8,
                    last_received_monotonic_ns=110,
                ),
                {
                    "games_home": 6,
                    "in_tiebreak": True,
                    "points_home": ScoreValue.LOVE,
                    "tiebreak_first_server": PlayerSide.AWAY,
                    "server_for_next_point": PlayerSide.AWAY,
                },
            ),
        )

        for prior, changes in cases:
            with self.subTest(prior=prior, changes=changes):
                state, _ = self.apply_pair(
                    api,
                    api.initial_score_consensus_state(),
                    prior,
                    witness_state(prior, revision=80),
                    now=115,
                )
                candidate = replace(
                    prior,
                    **changes,
                    revision=9,
                    last_received_monotonic_ns=120,
                )

                next_state, accepted = self.apply_pair(
                    api,
                    state,
                    candidate,
                    witness_state(
                        candidate,
                        revision=81,
                        last_received_monotonic_ns=121,
                    ),
                    now=125,
                )

                self.assertIs(
                    accepted.reason,
                    api.ConsensusReason.ACCEPTED,
                )
                self.assertIs(next_state.accepted_state, candidate)
                self.assertFalse(next_state.quarantined)

    def test_higher_correction_epoch_establishes_a_new_barrier(self) -> None:
        api = consensus_api()
        prior = tennis_state(
            games_home=4,
            games_away=2,
            revision=8,
            last_received_monotonic_ns=110,
        )
        state, _ = self.apply_pair(
            api,
            api.initial_score_consensus_state(),
            prior,
            witness_state(prior, revision=80),
            now=115,
        )
        correction = replace(
            prior,
            games_home=1,
            games_away=0,
            points_home=ScoreValue.LOVE,
            points_away=ScoreValue.LOVE,
            correction_epoch=2,
            revision=1,
            correction_lineage_sha256=SHA_D,
            last_received_monotonic_ns=120,
        )

        next_state, accepted = self.apply_pair(
            api,
            state,
            correction,
            witness_state(
                correction,
                revision=40,
                last_received_monotonic_ns=121,
            ),
            now=125,
        )

        self.assertIs(accepted.reason, api.ConsensusReason.ACCEPTED)
        self.assertIs(next_state.accepted_state, correction)
        self.assertEqual(next_state.consensus_epoch, 1)
        self.assertFalse(next_state.quarantined)

    def test_initial_authoritative_state_must_be_reachable(self) -> None:
        api = consensus_api()
        impossible = tennis_state(
            completed_sets=(),
            games_home=7,
            games_away=0,
            points_home=ScoreValue.LOVE,
            points_away=ScoreValue.LOVE,
            revision=8,
            last_received_monotonic_ns=110,
        )

        next_state, rejected = self.apply_pair(
            api,
            api.initial_score_consensus_state(),
            impossible,
            witness_state(impossible, revision=80),
            now=115,
        )

        self.assertIs(
            rejected.reason,
            api.ConsensusReason.TRANSITION_REGRESSION,
        )
        self.assertIsNone(next_state.accepted_state)
        self.assertTrue(next_state.quarantined)

    def test_initial_state_rejects_a_tiebreak_that_ended_too_late(
        self,
    ) -> None:
        api = consensus_api()
        impossible = tennis_state(
            completed_sets=(SetScore(7, 6, 8, 5),),
            games_home=0,
            games_away=0,
            points_home=ScoreValue.LOVE,
            points_away=ScoreValue.LOVE,
            revision=8,
            last_received_monotonic_ns=110,
        )

        next_state, rejected = self.apply_pair(
            api,
            api.initial_score_consensus_state(),
            impossible,
            witness_state(impossible, revision=80),
            now=115,
        )

        self.assertIs(
            rejected.reason,
            api.ConsensusReason.TRANSITION_REGRESSION,
        )
        self.assertIsNone(next_state.accepted_state)

    def test_tiebreak_first_server_cannot_change_mid_tiebreak(self) -> None:
        api = consensus_api()
        prior = tennis_state(
            completed_sets=(),
            games_home=6,
            games_away=6,
            points_home=ScoreValue.LOVE,
            points_away=ScoreValue.LOVE,
            in_tiebreak=True,
            tiebreak_points_home=4,
            tiebreak_points_away=3,
            tiebreak_first_server=PlayerSide.HOME,
            server_for_next_point=PlayerSide.HOME,
            revision=8,
            last_received_monotonic_ns=110,
        )
        state, _ = self.apply_pair(
            api,
            api.initial_score_consensus_state(),
            prior,
            witness_state(prior, revision=80),
            now=115,
        )
        changed_first_server = replace(
            prior,
            tiebreak_points_away=4,
            tiebreak_first_server=PlayerSide.AWAY,
            server_for_next_point=PlayerSide.AWAY,
            revision=9,
            last_received_monotonic_ns=120,
        )

        next_state, rejected = self.apply_pair(
            api,
            state,
            changed_first_server,
            witness_state(
                changed_first_server,
                revision=81,
                last_received_monotonic_ns=121,
            ),
            now=125,
        )

        self.assertIs(
            rejected.reason,
            api.ConsensusReason.TRANSITION_REGRESSION,
        )
        self.assertIs(next_state.accepted_state, prior)

    def test_legal_tiebreak_completion_and_natural_match_end_advance(
        self,
    ) -> None:
        api = consensus_api()
        cases = (
            (
                tennis_state(
                    completed_sets=(),
                    games_home=6,
                    games_away=6,
                    points_home=ScoreValue.LOVE,
                    points_away=ScoreValue.LOVE,
                    in_tiebreak=True,
                    tiebreak_points_home=6,
                    tiebreak_points_away=5,
                    tiebreak_first_server=PlayerSide.HOME,
                    server_for_next_point=PlayerSide.HOME,
                    revision=8,
                    last_received_monotonic_ns=110,
                ),
                {
                    "completed_sets": (SetScore(7, 6, 7, 5),),
                    "games_home": 0,
                    "games_away": 0,
                    "in_tiebreak": False,
                    "tiebreak_points_home": 0,
                    "tiebreak_points_away": 0,
                    "tiebreak_first_server": None,
                    "server_for_next_point": PlayerSide.AWAY,
                },
            ),
            (
                tennis_state(
                    completed_sets=(SetScore(6, 4, None, None),),
                    games_home=5,
                    games_away=0,
                    points_home=ScoreValue.FORTY,
                    points_away=ScoreValue.LOVE,
                    server_for_next_point=PlayerSide.HOME,
                    revision=8,
                    last_received_monotonic_ns=110,
                ),
                {
                    "completed_sets": (
                        SetScore(6, 4, None, None),
                        SetScore(6, 0, None, None),
                    ),
                    "games_home": 0,
                    "games_away": 0,
                    "points_home": ScoreValue.LOVE,
                    "status": MatchStatus.ENDED,
                    "termination_kind": TerminationKind.NATURAL,
                    "winner": PlayerSide.HOME,
                    "server_for_next_point": None,
                },
            ),
        )

        for prior, changes in cases:
            with self.subTest(prior=prior):
                state, _ = self.apply_pair(
                    api,
                    api.initial_score_consensus_state(),
                    prior,
                    witness_state(prior, revision=80),
                    now=115,
                )
                candidate = replace(
                    prior,
                    **changes,
                    revision=9,
                    last_received_monotonic_ns=120,
                )

                next_state, accepted = self.apply_pair(
                    api,
                    state,
                    candidate,
                    witness_state(
                        candidate,
                        revision=81,
                        last_received_monotonic_ns=121,
                    ),
                    now=125,
                )

                self.assertIs(
                    accepted.reason,
                    api.ConsensusReason.ACCEPTED,
                )
                self.assertIs(next_state.accepted_state, candidate)

    def test_new_full_consensus_clears_a_disagreement_barrier(self) -> None:
        api = consensus_api()
        primary = tennis_state(last_received_monotonic_ns=100)
        witness = witness_state(primary, last_received_monotonic_ns=101)
        state, _ = self.apply_pair(
            api,
            api.initial_score_consensus_state(),
            primary,
            witness,
            now=105,
        )
        dissent = replace(
            witness,
            games_home=3,
            last_received_monotonic_ns=110,
        )
        quarantined, rejected = self.apply_pair(
            api,
            state,
            primary,
            dissent,
            now=115,
        )
        advanced = replace(
            primary,
            games_home=3,
            server_for_next_point=PlayerSide.AWAY,
            revision=8,
            last_received_monotonic_ns=120,
        )
        advanced_witness = witness_state(
            advanced,
            revision=80,
            last_received_monotonic_ns=121,
        )

        restored, accepted = self.apply_pair(
            api,
            quarantined,
            advanced,
            advanced_witness,
            now=125,
        )

        self.assertIs(rejected.reason, api.ConsensusReason.STATE_MISMATCH)
        self.assertTrue(quarantined.quarantined)
        self.assertIs(accepted.reason, api.ConsensusReason.ACCEPTED)
        self.assertFalse(restored.quarantined)
        self.assertEqual(restored.consensus_epoch, 1)

    def test_source_config_rejects_invalid_identity_values(self) -> None:
        api = consensus_api()
        constructor = getattr(api, "ScoreSourceConfig")
        valid_identity = {
            "provider_match_id": "source-match",
            "provider_home_player_id": "source-home",
            "provider_away_player_id": "source-away",
            "canonical_home_player_id": "canonical-home",
            "canonical_away_player_id": "canonical-away",
        }
        cases = (
            ("provider_match_id", "", ValueError),
            ("provider_home_player_id", "", ValueError),
            ("provider_away_player_id", 1, TypeError),
            ("canonical_home_player_id", "", ValueError),
            ("canonical_away_player_id", 1, TypeError),
        )

        for field, value, error_type in cases:
            identity = {**valid_identity, field: value}
            with self.subTest(field=field), self.assertRaisesRegex(
                error_type,
                field,
            ):
                constructor("source", SHA_A, 20, **identity)

    def test_source_config_rejects_collapsed_player_orientation(self) -> None:
        api = consensus_api()
        cases = (
            (
                {"provider_away_player_id": "player-home"},
                "provider_player_identity",
            ),
            (
                {"canonical_away_player_id": "player-home"},
                "canonical_player_identity",
            ),
        )

        for changes, error_message in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(
                ValueError,
                error_message,
            ):
                score_source_config(
                    api,
                    "source",
                    SHA_A,
                    20,
                    **changes,
                )

    def test_policy_rejects_invalid_primary_source_id(self) -> None:
        api = consensus_api()
        source = score_source_config(api, "primary", SHA_A, 20)
        cases = (("", ValueError), (1, TypeError))

        for primary_source_id, error_type in cases:
            with self.subTest(
                primary_source_id=primary_source_id,
            ), self.assertRaisesRegex(error_type, "primary_source_id"):
                api.ScoreConsensusPolicy(
                    primary_source_id=primary_source_id,
                    sources=(source,),
                )

    def test_policy_rejects_non_source_config_entries(self) -> None:
        api = consensus_api()

        with self.assertRaisesRegex(TypeError, "sources"):
            api.ScoreConsensusPolicy(
                primary_source_id="primary",
                sources=(object(),),
            )

    def test_reducer_rejects_invalid_public_boundary_inputs(self) -> None:
        api = consensus_api()
        primary = tennis_state()
        policy = api.ScoreConsensusPolicy(
            primary_source_id="primary",
            sources=(score_source_config(api, "primary", SHA_A, 20),),
        )
        cases = (
            (object(), {"primary": primary}, 110, TypeError, "policy"),
            (policy, (), 110, TypeError, "observations"),
            (policy, {1: None}, 110, TypeError, "observations"),
            (
                policy,
                {"primary": object()},
                110,
                TypeError,
                "observations",
            ),
            (
                policy,
                {"primary": primary, "unconfigured": None},
                110,
                ValueError,
                "observations",
            ),
            (
                policy,
                {"primary": primary},
                -1,
                ValueError,
                "now_monotonic_ns",
            ),
            (
                policy,
                {"primary": primary},
                True,
                TypeError,
                "now_monotonic_ns",
            ),
        )

        for (
            case_policy,
            observations,
            now_monotonic_ns,
            error_type,
            error_message,
        ) in cases:
            with self.subTest(
                observations=observations,
                now_monotonic_ns=now_monotonic_ns,
            ), self.assertRaisesRegex(error_type, error_message):
                api.reduce_score_consensus(
                    case_policy,
                    observations,
                    now_monotonic_ns=now_monotonic_ns,
                )


if __name__ == "__main__":
    unittest.main()
