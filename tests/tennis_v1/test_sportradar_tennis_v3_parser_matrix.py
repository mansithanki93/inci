from __future__ import annotations

import calendar
import copy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from inci_tennis_expert.contracts import (
    ExpertIgnoreReasonV1,
    MatchStatus,
    PlayerSide,
    ProviderLifecycle,
    ProviderLifecycleKind,
    ScoreValue,
    SetScore,
    TerminationKind,
    TransitionDisposition,
    compute_expert_provider_source_lineage_sha256,
)
from inci_tennis_expert.tennis_score import (
    apply_lifecycle,
    validate_tennis_state,
)
from inci_tennis_io import facade
from tennis_v1.canonical import canonical_json_bytes
from tests.tennis_v1 import test_expert_contracts as _expert_helpers
from tests.tennis_v1 import test_sportradar_tennis_v3 as _existing


FIXTURES = Path(__file__).resolve().parent / "fixtures"
SUMMARY_SOURCE_WALL_NS = 1_894_726_800_000_000_000
SUMMARY_GENERATED_NS = 1_894_726_800_250_000_000
TIMELINE_SOURCE_WALL_NS = 1_894_730_402_000_000_000
TIMELINE_GENERATED_NS = 1_894_730_402_100_000_000


def _fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_bytes())


def _utc_ns(value: str) -> int:
    whole = value[:-1]
    if "." in whole:
        seconds_text, fraction = whole.split(".", 1)
    else:
        seconds_text, fraction = whole, ""
    parsed = datetime.strptime(
        seconds_text,
        "%Y-%m-%dT%H:%M:%S",
    ).replace(tzinfo=timezone.utc)
    return (
        calendar.timegm(parsed.utctimetuple()) * 1_000_000_000
        + int(fraction.ljust(9, "0") or "0")
    )


class SportradarTennisV3ParserMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        _existing.CandidateOutputWriterTests.setUp(self)

    def tearDown(self) -> None:
        _existing.CandidateOutputWriterTests.tearDown(self)

    def _eligible_candidate_inputs(
        self,
        source_seals: object,
        *,
        required_capabilities: tuple[str, ...] | None = None,
    ) -> object:
        return (
            _existing.CandidateOutputWriterTests
            ._eligible_candidate_inputs(
                self,
                source_seals,
                required_capabilities=required_capabilities,
            )
        )

    def _pure_parser_inputs(
        self,
        *,
        payload: bytes,
        event_type: str,
        source_wall_ns: int,
        source_generated_ns: int,
        provider_sequence: str,
    ) -> tuple[object, object, object, object]:
        return (
            _existing.CandidateOutputWriterTests
            ._pure_parser_inputs(
                self,
                payload=payload,
                event_type=event_type,
                source_wall_ns=source_wall_ns,
                source_generated_ns=source_generated_ns,
                provider_sequence=provider_sequence,
            )
        )

    def _bind(
        self,
        *,
        payload: bytes,
        event_type: str,
        source_wall_ns: int,
        source_generated_ns: int,
        provider_sequence: str,
    ) -> tuple[object, object, object, object]:
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            bind_sportradar_tennis_v3_event,
        )

        provider_binding, universe, captured, durable_raw = (
            self._pure_parser_inputs(
                payload=payload,
                event_type=event_type,
                source_wall_ns=source_wall_ns,
                source_generated_ns=source_generated_ns,
                provider_sequence=provider_sequence,
            )
        )
        adapter = bind_sportradar_tennis_v3_event(
            provider_binding=provider_binding,
            universe=universe,
            captured=captured,
            durable_raw=durable_raw,
        )
        return adapter, provider_binding, universe, captured

    def _assert_summary_error(
        self,
        payload: bytes,
        code: str,
    ) -> None:
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            SportradarTennisV3CandidateError,
        )

        adapter, _, _, captured = self._bind(
            payload=payload,
            event_type="sportradar_tennis_summary_v3",
            source_wall_ns=SUMMARY_SOURCE_WALL_NS,
            source_generated_ns=SUMMARY_GENERATED_NS,
            provider_sequence="c0.r0",
        )
        with self.assertRaisesRegex(
            SportradarTennisV3CandidateError,
            rf"\A{code}\Z",
        ):
            adapter.normalize_summary(
                payload,
                received_monotonic_ns=captured.local_monotonic_ns,
            )

    def _assert_timeline_error(
        self,
        document: dict[str, object],
        code: str,
    ) -> None:
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            SportradarTennisV3CandidateError,
        )

        payload = canonical_json_bytes(document)
        adapter, _, _, captured = self._bind(
            payload=payload,
            event_type="sportradar_tennis_timeline_v3",
            source_wall_ns=TIMELINE_SOURCE_WALL_NS,
            source_generated_ns=TIMELINE_GENERATED_NS,
            provider_sequence="c1.r1",
        )
        with self.assertRaisesRegex(
            SportradarTennisV3CandidateError,
            rf"\A{code}\Z",
        ):
            adapter.normalize_timeline(
                payload,
                prior=None,
                received_monotonic_ns=captured.local_monotonic_ns,
            )

    def _prior(
        self,
        prior_case: str,
        *,
        provider_binding: object,
        universe: object,
    ) -> object:
        selected = universe.bindings[0]
        lineage = compute_expert_provider_source_lineage_sha256(
            provider_binding.provider_id,
            provider_binding.product_tier,
            provider_binding.source_lineage_id,
            provider_binding.manifest_canonical_sha256,
        )
        common = {
            "provider_source_id": provider_binding.provider_id,
            "revision_domain_id": selected.revision_domain_id,
            "source_lineage_sha256": lineage,
            "provider_match_id": selected.provider_match_id,
            "home_player_id": selected.provider_home_player_id,
            "away_player_id": selected.provider_away_player_id,
            "scheduled_start_wall_ns": (
                selected.scheduled_start_wall_ns
            ),
            "match_format": selected.match_format,
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
            "correction_epoch": 0,
            "snapshot_complete": True,
            "last_provider_event_id": (
                f"{prior_case}-event"
            ),
            "last_event_semantic_sha256": "d" * 64,
            "correction_lineage_sha256": "e" * 64,
            "last_received_monotonic_ns": 699,
            "last_clock_uncertainty_ns": 2,
            "block_reason": None,
            "expected_revision": None,
            "observed_revision": None,
            "blocked_event_semantic_sha256": None,
            "blocked_received_monotonic_ns": None,
        }
        if prior_case == "synthetic-prior-scheduled":
            state = _expert_helpers.tennis_state(
                **common,
                status=MatchStatus.SCHEDULED,
                termination_kind=TerminationKind.NONE,
                server_for_next_point=None,
                revision=0,
                last_source_wall_ns=0,
                last_source_generated_wall_ns=0,
            )
        elif prior_case == "synthetic-prior-live":
            state = _expert_helpers.tennis_state(
                **common,
                status=MatchStatus.LIVE,
                termination_kind=TerminationKind.NONE,
                server_for_next_point=PlayerSide.HOME,
                revision=1,
                last_source_wall_ns=1_894_730_400_000_000_000,
                last_source_generated_wall_ns=(
                    1_894_730_400_000_000_000
                ),
            )
        elif prior_case == "synthetic-prior-suspended":
            state = _expert_helpers.tennis_state(
                **common,
                status=MatchStatus.SUSPENDED,
                termination_kind=TerminationKind.NONE,
                server_for_next_point=PlayerSide.HOME,
                revision=2,
                last_source_wall_ns=1_894_730_460_000_000_000,
                last_source_generated_wall_ns=(
                    1_894_730_460_100_000_000
                ),
            )
        elif prior_case == "synthetic-prior-natural-ended":
            natural = dict(common)
            natural.update(
                {
                    "winner": PlayerSide.HOME,
                    "completed_sets": (
                        SetScore(6, 0, None, None),
                        SetScore(6, 0, None, None),
                    ),
                }
            )
            state = _expert_helpers.tennis_state(
                **natural,
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.NATURAL,
                server_for_next_point=None,
                revision=3,
                last_source_wall_ns=1_894_735_740_000_000_000,
                last_source_generated_wall_ns=(
                    1_894_735_740_100_000_000
                ),
            )
        else:
            raise AssertionError("unknown synthetic prior")
        validate_tennis_state(state)
        return state

    def test_all_seven_lifecycle_fixtures_have_lawful_priors(
        self,
    ) -> None:
        fixture = _fixture("sportradar_tennis_timeline_v3.json")
        self.assertIs(type(fixture), dict)
        lifecycle_cases = fixture["cases"][1:]
        expected = {
            "synthetic-lifecycle-start": (
                ProviderLifecycleKind.START,
                PlayerSide.HOME,
                None,
                None,
            ),
            "synthetic-lifecycle-suspend": (
                ProviderLifecycleKind.SUSPEND,
                PlayerSide.HOME,
                None,
                None,
            ),
            "synthetic-lifecycle-resume": (
                ProviderLifecycleKind.RESUME,
                PlayerSide.HOME,
                None,
                None,
            ),
            "synthetic-lifecycle-walkover": (
                ProviderLifecycleKind.WALKOVER,
                None,
                PlayerSide.HOME,
                None,
            ),
            "synthetic-lifecycle-retirement": (
                ProviderLifecycleKind.RETIREMENT,
                None,
                PlayerSide.HOME,
                PlayerSide.AWAY,
            ),
            "synthetic-lifecycle-cancel": (
                ProviderLifecycleKind.CANCEL,
                None,
                None,
                None,
            ),
            "synthetic-lifecycle-natural-end-confirmation": (
                ProviderLifecycleKind.NATURAL_END_CONFIRMATION,
                None,
                PlayerSide.HOME,
                None,
            ),
        }
        self.assertEqual(len(lifecycle_cases), 7)
        for case in lifecycle_cases:
            case_id = case["case_id"]
            document = case["payload"]
            entry = document["timeline"][-1]
            payload = canonical_json_bytes(document)
            with self.subTest(case_id=case_id):
                adapter, provider_binding, universe, captured = (
                    self._bind(
                        payload=payload,
                        event_type=(
                            "sportradar_tennis_timeline_v3"
                        ),
                        source_wall_ns=_utc_ns(
                            entry["event_time"]
                        ),
                        source_generated_ns=_utc_ns(
                            document["generated_at"]
                        ),
                        provider_sequence=(
                            f"c{entry['correction_epoch']}."
                            f"r{entry['revision']}"
                        ),
                    )
                )
                prior = self._prior(
                    case["prior_case"],
                    provider_binding=provider_binding,
                    universe=universe,
                )
                events = adapter.normalize_timeline(
                    payload,
                    prior=prior,
                    received_monotonic_ns=(
                        captured.local_monotonic_ns
                    ),
                )
                self.assertEqual(len(events), 1)
                lifecycle = events[0]
                self.assertIs(type(lifecycle), ProviderLifecycle)
                (
                    kind,
                    server,
                    winner,
                    retired,
                ) = expected[case_id]
                self.assertIs(lifecycle.kind, kind)
                self.assertIs(
                    lifecycle.server_for_next_point,
                    server,
                )
                self.assertIs(lifecycle.winner, winner)
                self.assertIs(lifecycle.retired_side, retired)
                transition = apply_lifecycle(prior, lifecycle)
                self.assertIs(
                    transition.disposition,
                    TransitionDisposition.APPLIED,
                )

    def test_sanitized_transport_error_route_is_ignored(self) -> None:
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            inspect_sportradar_candidate_capture,
        )
        from inci_tennis_io.provider_readonly import (
            build_sportradar_candidate_session_manifest,
        )
        from inci_tennis_runtime.provider_qualification_controller import (
            evaluate_sportradar_candidate_offline,
        )
        from tests.tennis_v1.sportradar_candidate_fixture_support import (
            capture_transport_candidate_fixture,
        )

        seal_authority = (
            facade.issue_sportradar_candidate_source_seal_collection_authority(
                self.root_authority
            )
        )
        source_seals = facade.collect_sportradar_candidate_source_seals(
            seal_authority
        )
        artifacts = self._eligible_candidate_inputs(source_seals)
        decision = evaluate_sportradar_candidate_offline(
            artifacts=artifacts,
            source_seals=source_seals,
        )
        self.assertIsNotNone(decision.binding)
        manifest = build_sportradar_candidate_session_manifest(
            artifacts=artifacts,
            source_seals=source_seals,
            decision=decision,
        )

        captured = capture_transport_candidate_fixture(
            manifest=manifest,
            source_entity_id="synthetic-match-1",
            session_start_wall_ns=artifacts.session_start_wall_ns,
            local_wall_ns=(
                artifacts.session_start_wall_ns + 1_000_000_000
            ),
            local_monotonic_ns=700,
            clock_uncertainty_ns=2,
            exception_type="timeout_error",
            status_code=None,
            error_code="timeout",
        )
        evidence = inspect_sportradar_candidate_capture(
            binding=decision.binding,
            universe=artifacts.universe,
            captured=captured,
            prior=None,
        )
        self.assertEqual(evidence.parser_outcome, "ignored")
        self.assertEqual(
            evidence.reason,
            ExpertIgnoreReasonV1.EVENT_NOT_RELEVANT.value,
        )
        self.assertEqual(evidence.output_contract_sha256s, ())
        self.assertTrue(
            all(not value for _, value in evidence.capabilities)
        )

    def test_registry_summary_prior_mismatch_precedes_payload_parse(
        self,
    ) -> None:
        from inci_tennis_adapters.registry import (
            normalize_sportradar_candidate_raw,
        )
        from inci_tennis_expert.contracts import (
            ExpertRejectedDraftV1,
            ExpertRejectReasonV1,
        )

        summary = _fixture(
            "sportradar_tennis_summary_v3.json"
        )
        cases = {"valid": summary}
        missing_coverage = copy.deepcopy(summary)
        del missing_coverage["coverage"]
        cases["payload_invalid"] = missing_coverage
        for name, document in cases.items():
            payload = canonical_json_bytes(document)
            (
                provider_binding,
                universe,
                captured,
                durable_raw,
            ) = self._pure_parser_inputs(
                payload=payload,
                event_type="sportradar_tennis_summary_v3",
                source_wall_ns=SUMMARY_SOURCE_WALL_NS,
                source_generated_ns=SUMMARY_GENERATED_NS,
                provider_sequence="c0.r0",
            )
            prior = self._prior(
                "synthetic-prior-scheduled",
                provider_binding=provider_binding,
                universe=universe,
            )
            mismatched = replace(
                prior,
                provider_match_id="synthetic-other-match",
            )
            validate_tennis_state(mismatched)
            with self.subTest(payload=name):
                drafts = normalize_sportradar_candidate_raw(
                    provider_binding=provider_binding,
                    universe=universe,
                    captured=captured,
                    durable_raw=durable_raw,
                    prior=mismatched,
                )
                self.assertEqual(len(drafts), 1)
                self.assertIs(type(drafts[0]), ExpertRejectedDraftV1)
                self.assertIs(
                    drafts[0].reason,
                    (
                        ExpertRejectReasonV1
                        .NORMALIZER_CONTRACT_VIOLATION
                    ),
                )

    def test_raw_json_lexical_failures_are_payload_invalid(self) -> None:
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            SportradarTennisV3CandidateError,
            _strict_json,
        )

        cases = {
            "bom": b"\xef\xbb\xbf{}",
            "malformed_utf8": b'{"value":"\xff"}',
            "malformed_json": b"{",
            "duplicate": b'{"value":1,"value":2}',
            "float": b'{"value":1.25}',
        }
        for name, payload in cases.items():
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    SportradarTennisV3CandidateError,
                    r"\Acandidate_payload_invalid\Z",
                ),
            ):
                _strict_json(payload)

    def test_bool_integer_and_bad_revision_values_are_rejected(
        self,
    ) -> None:
        summary = _fixture(
            "sportradar_tennis_summary_v3.json"
        )
        for name, value in (
            ("bool", True),
            ("negative", -1),
        ):
            mutated = copy.deepcopy(summary)
            mutated["revision"] = value
            with self.subTest(route="summary", mutation=name):
                self._assert_summary_error(
                    canonical_json_bytes(mutated),
                    "candidate_payload_invalid",
                )

        timeline_fixture = _fixture(
            "sportradar_tennis_timeline_v3.json"
        )
        base = timeline_fixture["cases"][0]["payload"]
        for name, value in (
            ("zero", 0),
            ("negative", -1),
            ("bool", True),
        ):
            mutated = copy.deepcopy(base)
            mutated["timeline"][0]["revision"] = value
            with self.subTest(route="timeline", mutation=name):
                self._assert_timeline_error(
                    mutated,
                    "candidate_payload_invalid",
                )

        nonmonotonic = copy.deepcopy(base)
        nonmonotonic["timeline"][1]["revision"] = 1
        self._assert_timeline_error(
            nonmonotonic,
            "candidate_payload_invalid",
        )

    def test_missing_and_unknown_keys_keep_exact_classification(
        self,
    ) -> None:
        summary = _fixture(
            "sportradar_tennis_summary_v3.json"
        )
        missing_documents: dict[str, dict[str, object]] = {}
        missing_top = copy.deepcopy(summary)
        del missing_top["coverage"]
        missing_documents["top"] = missing_top
        missing_sport_event = copy.deepcopy(summary)
        del missing_sport_event["sport_event"]["id"]
        missing_documents["sport_event"] = missing_sport_event
        missing_competitor = copy.deepcopy(summary)
        del missing_competitor["sport_event"]["competitors"][0]["id"]
        missing_documents["competitor"] = missing_competitor
        missing_status = copy.deepcopy(summary)
        del missing_status["sport_event_status"]["server_id"]
        missing_documents["status"] = missing_status
        missing_coverage = copy.deepcopy(summary)
        del missing_coverage["coverage"]["current_server"]
        missing_documents["coverage"] = missing_coverage
        for name, document in missing_documents.items():
            with self.subTest(kind="missing", depth=name):
                self._assert_summary_error(
                    canonical_json_bytes(document),
                    "candidate_payload_invalid",
                )

        unknown_documents: dict[str, dict[str, object]] = {}
        unknown_top = copy.deepcopy(summary)
        unknown_top["unexpected"] = "synthetic"
        unknown_documents["top"] = unknown_top
        unknown_sport_event = copy.deepcopy(summary)
        unknown_sport_event["sport_event"]["unexpected"] = (
            "synthetic"
        )
        unknown_documents["sport_event"] = unknown_sport_event
        unknown_competitor = copy.deepcopy(summary)
        unknown_competitor["sport_event"]["competitors"][0][
            "unexpected"
        ] = "synthetic"
        unknown_documents["competitor"] = unknown_competitor
        unknown_status = copy.deepcopy(summary)
        unknown_status["sport_event_status"]["unexpected"] = (
            "synthetic"
        )
        unknown_documents["status"] = unknown_status
        unknown_coverage = copy.deepcopy(summary)
        unknown_coverage["coverage"]["unexpected"] = True
        unknown_documents["coverage"] = unknown_coverage
        for name, document in unknown_documents.items():
            with self.subTest(kind="unknown", depth=name):
                self._assert_summary_error(
                    canonical_json_bytes(document),
                    "candidate_schema_unknown",
                )

    def test_missing_server_timestamps_ids_and_snapshot_completeness(
        self,
    ) -> None:
        summary = _fixture(
            "sportradar_tennis_summary_v3.json"
        )
        summary_cases: dict[str, dict[str, object]] = {}
        live_without_server = copy.deepcopy(summary)
        live_without_server["sport_event_status"]["status"] = "LIVE"
        summary_cases["live_server"] = live_without_server
        missing_generated = copy.deepcopy(summary)
        del missing_generated["generated_at"]
        summary_cases["generated_at"] = missing_generated
        missing_source_time = copy.deepcopy(summary)
        del missing_source_time["source_event_time"]
        summary_cases["source_event_time"] = missing_source_time
        missing_event_id = copy.deepcopy(summary)
        del missing_event_id["event_id"]
        summary_cases["event_id"] = missing_event_id
        missing_match_id = copy.deepcopy(summary)
        del missing_match_id["sport_event"]["id"]
        summary_cases["match_id"] = missing_match_id
        missing_player_id = copy.deepcopy(summary)
        del missing_player_id["sport_event"]["competitors"][1]["id"]
        summary_cases["player_id"] = missing_player_id
        incomplete = copy.deepcopy(summary)
        incomplete["snapshot_complete"] = False
        summary_cases["snapshot_complete_false"] = incomplete
        missing_complete = copy.deepcopy(summary)
        del missing_complete["snapshot_complete"]
        summary_cases["snapshot_complete_missing"] = missing_complete
        for name, document in summary_cases.items():
            with self.subTest(route="summary", mutation=name):
                self._assert_summary_error(
                    canonical_json_bytes(document),
                    "candidate_payload_invalid",
                )

        timeline_fixture = _fixture(
            "sportradar_tennis_timeline_v3.json"
        )
        base = timeline_fixture["cases"][0]["payload"]
        timeline_cases: dict[str, dict[str, object]] = {}
        point_without_server = copy.deepcopy(base)
        point_without_server["timeline"][1]["server_id"] = None
        timeline_cases["point_server"] = point_without_server
        start_without_server = copy.deepcopy(
            timeline_fixture["cases"][1]["payload"]
        )
        start_without_server["timeline"][0]["server_id"] = None
        timeline_cases["start_server"] = start_without_server
        missing_document_generated = copy.deepcopy(base)
        del missing_document_generated["generated_at"]
        timeline_cases["document_generated_at"] = (
            missing_document_generated
        )
        missing_event_time = copy.deepcopy(base)
        del missing_event_time["timeline"][0]["event_time"]
        timeline_cases["event_time"] = missing_event_time
        missing_entry_generated = copy.deepcopy(base)
        del missing_entry_generated["timeline"][0]["generated_at"]
        timeline_cases["entry_generated_at"] = missing_entry_generated
        incomplete_snapshot = copy.deepcopy(base)
        incomplete_snapshot["timeline"][0][
            "snapshot_complete"
        ] = False
        timeline_cases["snapshot_complete_false"] = (
            incomplete_snapshot
        )
        missing_snapshot_complete = copy.deepcopy(base)
        del missing_snapshot_complete["timeline"][0][
            "snapshot_complete"
        ]
        timeline_cases["snapshot_complete_missing"] = (
            missing_snapshot_complete
        )
        for name, document in timeline_cases.items():
            with self.subTest(route="timeline", mutation=name):
                self._assert_timeline_error(
                    document,
                    "candidate_payload_invalid",
                )

    def test_timeline_cardinality_and_local_poll_counter_reject(
        self,
    ) -> None:
        fixture = _fixture(
            "sportradar_tennis_timeline_v3.json"
        )
        base = fixture["cases"][0]["payload"]
        empty = copy.deepcopy(base)
        empty["timeline"] = []
        self._assert_timeline_error(
            empty,
            "candidate_payload_invalid",
        )

        too_many = copy.deepcopy(base)
        template = copy.deepcopy(base["timeline"][1])
        entries = []
        for revision in range(1, 66):
            entry = copy.deepcopy(template)
            entry["id"] = f"synthetic-point-{revision}"
            entry["correction_epoch"] = 0
            entry["revision"] = revision
            entries.append(entry)
        too_many["timeline"] = entries
        self._assert_timeline_error(
            too_many,
            "candidate_payload_invalid",
        )

        extra_poll = copy.deepcopy(base)
        extra_poll["timeline"][1]["local_poll_counter"] = 17
        self._assert_timeline_error(
            extra_poll,
            "candidate_schema_unknown",
        )

        substituted_poll = copy.deepcopy(base)
        del substituted_poll["timeline"][1]["revision"]
        substituted_poll["timeline"][1]["local_poll_counter"] = 2
        self._assert_timeline_error(
            substituted_poll,
            "candidate_payload_invalid",
        )

    def test_bo3_bo5_best_of_mismatch_is_payload_invalid(self) -> None:
        summary = _fixture(
            "sportradar_tennis_summary_v3.json"
        )
        cases = (
            (
                5,
                "standard_advantage_bo3_tb7_all_sets",
            ),
            (
                3,
                "standard_advantage_bo5_tb7_all_sets",
            ),
        )
        for best_of, match_format in cases:
            mutated = copy.deepcopy(summary)
            mutated["sport_event"]["best_of"] = best_of
            mutated["sport_event"]["match_format"] = match_format
            with self.subTest(
                best_of=best_of,
                match_format=match_format,
            ):
                self._assert_summary_error(
                    canonical_json_bytes(mutated),
                    "candidate_payload_invalid",
                )

    def test_environment_objects_are_secret_material(self) -> None:
        summary = _fixture(
            "sportradar_tennis_summary_v3.json"
        )
        for environment in ({}, {"foo": "bar"}):
            mutated = copy.deepcopy(summary)
            mutated["environment"] = environment
            with self.subTest(environment=environment):
                self._assert_summary_error(
                    canonical_json_bytes(mutated),
                    "candidate_secret_material",
                )

    def test_noncanonical_json_string_escapes_are_rejected(self) -> None:
        payload = (
            FIXTURES / "sportradar_tennis_summary_v3.json"
        ).read_bytes()
        cases = {
            "key": payload.replace(
                b'"event_id"',
                b'"event\\u005fid"',
                1,
            ),
            "value": payload.replace(
                b'"synthetic-summary-event-1"',
                b'"synthetic-summary-event-\\u0031"',
                1,
            ),
        }
        for name, mutated in cases.items():
            self.assertNotEqual(mutated, payload)
            with self.subTest(name=name):
                self._assert_summary_error(
                    mutated,
                    "candidate_payload_invalid",
                )

    def test_noncanonical_signed_zero_coordinates_are_rejected(
        self,
    ) -> None:
        payload = (
            FIXTURES / "sportradar_tennis_summary_v3.json"
        ).read_bytes()
        cases = {
            "correction_epoch": payload.replace(
                b'"correction_epoch": 0',
                b'"correction_epoch": -0',
                1,
            ),
            "revision": payload.replace(
                b'"revision": 0',
                b'"revision": -0',
                1,
            ),
        }
        for name, mutated in cases.items():
            self.assertNotEqual(mutated, payload)
            with self.subTest(name=name):
                self._assert_summary_error(
                    mutated,
                    "candidate_payload_invalid",
                )
