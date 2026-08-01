from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import unittest

from tennis_v1.canonical import canonical_json_bytes
from tests.tennis_v1 import test_sportradar_tennis_v3 as _existing
from tests.tennis_v1 import (
    test_sportradar_tennis_v3_parser_matrix as _parser_matrix,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"
SUMMARY_SOURCE_WALL_NS = 1_894_726_800_000_000_000
SUMMARY_GENERATED_NS = 1_894_726_800_250_000_000
TIMELINE_SOURCE_WALL_NS = 1_894_730_402_000_000_000
TIMELINE_GENERATED_NS = 1_894_730_402_100_000_000


def _fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_bytes())


def _all_pairs(value: object) -> tuple[tuple[str, object], ...]:
    pairs: list[tuple[str, object]] = []
    stack = [value]
    while stack:
        current = stack.pop()
        if type(current) is dict:
            for key, item in current.items():
                pairs.append((key, item))
                stack.append(item)
        elif type(current) is list:
            stack.extend(current)
    return tuple(pairs)


class SportradarTennisV3AcceptanceMatrixTests(unittest.TestCase):
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

    def _prior(
        self,
        prior_case: str,
        *,
        provider_binding: object,
        universe: object,
    ) -> object:
        return (
            _parser_matrix.SportradarTennisV3ParserMatrixTests
            ._prior(
                self,
                prior_case,
                provider_binding=provider_binding,
                universe=universe,
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
    ) -> tuple[object, object, object, object, object]:
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
        return (
            adapter,
            provider_binding,
            universe,
            captured,
            durable_raw,
        )

    def _assert_summary_error(
        self,
        payload: bytes,
        code: str,
        *,
        source_wall_ns: int = SUMMARY_SOURCE_WALL_NS,
        source_generated_ns: int = SUMMARY_GENERATED_NS,
        provider_sequence: str = "c0.r0",
    ) -> None:
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            SportradarTennisV3CandidateError,
        )

        adapter, _, _, captured, _ = self._bind(
            payload=payload,
            event_type="sportradar_tennis_summary_v3",
            source_wall_ns=source_wall_ns,
            source_generated_ns=source_generated_ns,
            provider_sequence=provider_sequence,
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
        *,
        source_wall_ns: int = TIMELINE_SOURCE_WALL_NS,
        source_generated_ns: int = TIMELINE_GENERATED_NS,
        provider_sequence: str = "c1.r1",
    ) -> None:
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            SportradarTennisV3CandidateError,
        )

        payload = canonical_json_bytes(document)
        adapter, _, _, captured, _ = self._bind(
            payload=payload,
            event_type="sportradar_tennis_timeline_v3",
            source_wall_ns=source_wall_ns,
            source_generated_ns=source_generated_ns,
            provider_sequence=provider_sequence,
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

    def test_t10_sixty_four_snapshots_produce_sixty_four_ordered_drafts(
        self,
    ) -> None:
        """Catches truncation, sorting, dedupe, or a 63-entry off-by-one."""
        from inci_tennis_adapters.registry import (
            normalize_sportradar_candidate_raw,
        )
        from inci_tennis_expert.contracts import (
            ExpertSynchronizationDraftV1,
            ProviderSnapshot,
            SyncInputKind,
            TransitionDisposition,
        )

        fixture = _fixture("sportradar_tennis_timeline_v3.json")
        template = fixture["cases"][0]["payload"]
        first_snapshot = template["timeline"][0]
        entries: list[dict[str, object]] = []
        for epoch in range(64):
            entry = copy.deepcopy(first_snapshot)
            minute, second = divmod(epoch, 60)
            event_time = (
                f"2030-01-15T18:{minute:02d}:{second:02d}Z"
            )
            generated_at = (
                f"2030-01-15T18:{minute:02d}:{second:02d}"
                ".100000000Z"
            )
            entry.update(
                {
                    "id": f"synthetic-snapshot-{epoch:02d}",
                    "correction_epoch": epoch,
                    "revision": 1,
                    "event_time": event_time,
                    "generated_at": generated_at,
                }
            )
            entries.append(entry)
        document = {
            "generated_at": entries[-1]["generated_at"],
            "sport_event": copy.deepcopy(template["sport_event"]),
            "timeline": entries,
        }
        payload = canonical_json_bytes(document)
        source_wall_ns = _parser_matrix._utc_ns(
            entries[-1]["event_time"]
        )
        source_generated_ns = _parser_matrix._utc_ns(
            entries[-1]["generated_at"]
        )
        (
            _,
            provider_binding,
            universe,
            captured,
            durable_raw,
        ) = self._bind(
            payload=payload,
            event_type="sportradar_tennis_timeline_v3",
            source_wall_ns=source_wall_ns,
            source_generated_ns=source_generated_ns,
            provider_sequence="c63.r1",
        )

        drafts = normalize_sportradar_candidate_raw(
            provider_binding=provider_binding,
            universe=universe,
            captured=captured,
            durable_raw=durable_raw,
            prior=None,
        )

        self.assertEqual(len(drafts), 64)
        self.assertTrue(
            all(
                type(draft) is ExpertSynchronizationDraftV1
                for draft in drafts
            )
        )
        self.assertEqual(
            tuple(draft.evidence.kind for draft in drafts),
            (SyncInputKind.TENNIS_ORIGIN,)
            + (SyncInputKind.TENNIS_TRANSITION,) * 63,
        )
        events = tuple(draft.evidence.provider_event for draft in drafts)
        self.assertTrue(
            all(type(event) is ProviderSnapshot for event in events)
        )
        self.assertEqual(
            tuple(
                (
                    event.provider_event_id,
                    event.correction_epoch,
                    event.revision,
                )
                for event in events
            ),
            tuple(
                (f"synthetic-snapshot-{epoch:02d}", epoch, 1)
                for epoch in range(64)
            ),
        )
        self.assertTrue(
            all(
                draft.evidence.tennis_transition.disposition
                is TransitionDisposition.APPLIED
                for draft in drafts[1:]
            )
        )

    def test_r1_adapter_registry_and_digests_repeat_one_thousand_times(
        self,
    ) -> None:
        """Catches mutable adapter state or nondeterministic draft/digest output."""
        from inci_tennis_adapters.registry import (
            normalize_sportradar_candidate_raw,
        )
        from inci_tennis_expert.contracts import (
            canonical_expert_bytes,
            expert_contract_sha256,
        )

        payload = (
            FIXTURES / "sportradar_tennis_summary_v3.json"
        ).read_bytes()
        (
            adapter,
            provider_binding,
            universe,
            captured,
            durable_raw,
        ) = self._bind(
            payload=payload,
            event_type="sportradar_tennis_summary_v3",
            source_wall_ns=SUMMARY_SOURCE_WALL_NS,
            source_generated_ns=SUMMARY_GENERATED_NS,
            provider_sequence="c0.r0",
        )
        event = adapter.normalize_summary(
            payload,
            received_monotonic_ns=captured.local_monotonic_ns,
        )
        drafts = normalize_sportradar_candidate_raw(
            provider_binding=provider_binding,
            universe=universe,
            captured=captured,
            durable_raw=durable_raw,
            prior=None,
        )
        expected = (
            canonical_expert_bytes(event),
            expert_contract_sha256(event),
            tuple(canonical_expert_bytes(draft) for draft in drafts),
            tuple(expert_contract_sha256(draft) for draft in drafts),
        )

        for _ in range(1_000):
            repeated_event = adapter.normalize_summary(
                payload,
                received_monotonic_ns=captured.local_monotonic_ns,
            )
            repeated_drafts = normalize_sportradar_candidate_raw(
                provider_binding=provider_binding,
                universe=universe,
                captured=captured,
                durable_raw=durable_raw,
                prior=None,
            )
            self.assertEqual(
                (
                    canonical_expert_bytes(repeated_event),
                    expert_contract_sha256(repeated_event),
                    tuple(
                        canonical_expert_bytes(draft)
                        for draft in repeated_drafts
                    ),
                    tuple(
                        expert_contract_sha256(draft)
                        for draft in repeated_drafts
                    ),
                ),
                expected,
            )

    def test_f1_committed_fixtures_are_synthetic_and_make_no_claims(
        self,
    ) -> None:
        """Catches real IDs, commercial data, or evidentiary claims in fixtures."""
        summary = _fixture("sportradar_tennis_summary_v3.json")
        timeline = _fixture("sportradar_tennis_timeline_v3.json")
        self.assertEqual(
            set(summary),
            {
                "event_id",
                "generated_at",
                "revision",
                "correction_epoch",
                "snapshot_complete",
                "source_event_time",
                "sport_event",
                "sport_event_status",
                "coverage",
            },
        )
        self.assertEqual(
            set(timeline),
            {"fixture_kind", "cases"},
        )
        self.assertEqual(
            timeline["fixture_kind"],
            "synthetic_sanitized_candidate_cases_v1",
        )
        self.assertTrue(1 <= len(timeline["cases"]) <= 32)

        documents = (summary, timeline)
        forbidden_key_fragments = (
            "url",
            "header",
            "request",
            "credential",
            "environment",
            "account",
            "entitlement",
            "price",
            "quota",
            "commercial",
            "terms",
        )
        forbidden_claims = (
            "official",
            "observed",
            "provider-verified",
            "qualified",
        )
        for document in documents:
            for key, value in _all_pairs(document):
                normalized_key = key.casefold().replace("_", "")
                for fragment in forbidden_key_fragments:
                    self.assertNotIn(fragment, normalized_key)
                if (
                    key == "id"
                    or key.endswith("_id")
                    or key in {"case_id", "prior_case"}
                ) and value is not None:
                    self.assertIs(type(value), str)
                    self.assertTrue(value.startswith("synthetic-"))
                if type(value) is str:
                    lowered = value.casefold()
                    self.assertNotIn("http://", lowered)
                    self.assertNotIn("https://", lowered)
                    self.assertNotIn("@", value)
                    for claim in forbidden_claims:
                        self.assertNotIn(claim, lowered)

    def test_s2_bo5_live_summary_uses_exact_binding_format_and_server(
        self,
    ) -> None:
        """Catches BO5 rejection or projection back to the BO3 binding."""
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            bind_sportradar_tennis_v3_event,
        )
        from inci_tennis_expert.contracts import (
            MatchFormat,
            MatchStatus,
            PlayerSide,
        )
        from tests.tennis_v1.test_expert_contracts import (
            binding_universe,
        )

        document = _fixture("sportradar_tennis_summary_v3.json")
        document["sport_event"]["best_of"] = 5
        document["sport_event"]["match_format"] = (
            "standard_advantage_bo5_tb7_all_sets"
        )
        document["sport_event_status"]["status"] = "LIVE"
        document["sport_event_status"]["server_id"] = (
            "synthetic-player-home"
        )
        payload = canonical_json_bytes(document)
        (
            _,
            provider_binding,
            bo3_universe,
            captured,
            durable_raw,
        ) = self._bind(
            payload=payload,
            event_type="sportradar_tennis_summary_v3",
            source_wall_ns=SUMMARY_SOURCE_WALL_NS,
            source_generated_ns=SUMMARY_GENERATED_NS,
            provider_sequence="c0.r0",
        )
        bo5_binding = replace(
            bo3_universe.bindings[0],
            match_format=(
                MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS
            ),
        )
        bo5_universe = binding_universe(
            bindings=(bo5_binding,),
            metadata=bo3_universe.metadata,
        )
        adapter = bind_sportradar_tennis_v3_event(
            provider_binding=provider_binding,
            universe=bo5_universe,
            captured=captured,
            durable_raw=durable_raw,
        )

        snapshot = adapter.normalize_summary(
            payload,
            received_monotonic_ns=captured.local_monotonic_ns,
        )

        self.assertIs(
            snapshot.match_format,
            MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS,
        )
        self.assertIs(snapshot.match_format, bo5_binding.match_format)
        self.assertIs(snapshot.status, MatchStatus.LIVE)
        self.assertIs(
            snapshot.server_for_next_point,
            PlayerSide.HOME,
        )

    def test_x2_id_lengths_one_and_sixty_four_are_retained_exactly(
        self,
    ) -> None:
        """Catches truncation, prefixing, or canonical projection at ID bounds."""
        boundary_ids = ("A", "Z" + "a" * 63)
        summary = _fixture("sportradar_tennis_summary_v3.json")
        for provider_event_id in boundary_ids:
            document = copy.deepcopy(summary)
            document["event_id"] = provider_event_id
            payload = canonical_json_bytes(document)
            adapter, _, _, captured, _ = self._bind(
                payload=payload,
                event_type="sportradar_tennis_summary_v3",
                source_wall_ns=SUMMARY_SOURCE_WALL_NS,
                source_generated_ns=SUMMARY_GENERATED_NS,
                provider_sequence="c0.r0",
            )
            with self.subTest(
                route="summary",
                length=len(provider_event_id),
            ):
                snapshot = adapter.normalize_summary(
                    payload,
                    received_monotonic_ns=(
                        captured.local_monotonic_ns
                    ),
                )
                self.assertEqual(
                    snapshot.provider_event_id,
                    provider_event_id,
                )

        fixture = _fixture("sportradar_tennis_timeline_v3.json")
        base = fixture["cases"][0]["payload"]
        for provider_event_id in boundary_ids:
            document = copy.deepcopy(base)
            document["timeline"][-1]["id"] = provider_event_id
            payload = canonical_json_bytes(document)
            adapter, _, _, captured, _ = self._bind(
                payload=payload,
                event_type="sportradar_tennis_timeline_v3",
                source_wall_ns=TIMELINE_SOURCE_WALL_NS,
                source_generated_ns=TIMELINE_GENERATED_NS,
                provider_sequence="c1.r1",
            )
            with self.subTest(
                route="timeline",
                length=len(provider_event_id),
            ):
                events = adapter.normalize_timeline(
                    payload,
                    prior=None,
                    received_monotonic_ns=(
                        captured.local_monotonic_ns
                    ),
                )
                self.assertEqual(
                    events[-1].provider_event_id,
                    provider_event_id,
                )

    def test_n_p24_summary_envelope_one_field_mutations_fail_closed(
        self,
    ) -> None:
        """Catches trust in payload envelope fields not matching capture."""
        summary = _fixture("sportradar_tennis_summary_v3.json")
        cases: dict[str, dict[str, object]] = {}
        source = copy.deepcopy(summary)
        source["source_event_time"] = "2030-01-15T17:00:01Z"
        cases["source_event_time"] = source
        generated = copy.deepcopy(summary)
        generated["generated_at"] = "2030-01-15T17:00:01Z"
        cases["generated_at"] = generated
        coordinate = copy.deepcopy(summary)
        coordinate["revision"] = 1
        cases["coordinate"] = coordinate

        for name, document in cases.items():
            with self.subTest(field=name):
                self._assert_summary_error(
                    canonical_json_bytes(document),
                    "candidate_captured_parent_mismatch",
                )

    def test_n_p24_timeline_envelope_one_field_mutations_fail_closed(
        self,
    ) -> None:
        """Catches trust in final timeline envelope fields not matching capture."""
        fixture = _fixture("sportradar_tennis_timeline_v3.json")
        base = fixture["cases"][0]["payload"]
        cases: dict[str, dict[str, object]] = {}
        source = copy.deepcopy(base)
        source["timeline"][-1]["event_time"] = (
            "2030-01-15T18:00:03Z"
        )
        cases["source_event_time"] = source
        generated = copy.deepcopy(base)
        generated["generated_at"] = "2030-01-15T18:00:03Z"
        cases["generated_at"] = generated
        coordinate = copy.deepcopy(base)
        coordinate["timeline"][-1]["revision"] = 2
        cases["coordinate"] = coordinate

        for name, document in cases.items():
            with self.subTest(field=name):
                self._assert_timeline_error(
                    document,
                    "candidate_captured_parent_mismatch",
                )

    def test_n_p2_payload_depth_node_and_byte_bounds_fail_closed(
        self,
    ) -> None:
        """Catches unbounded parser recursion, allocation, or capture size."""
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            SportradarTennisV3CandidateError,
            _strict_json,
        )

        nested = b"[" * 65 + b"0" + b"]" * 65
        excessive_nodes = (
            b'{"items":[' + b",".join([b"0"] * 250_001) + b"]}"
        )
        excessive_bytes = b" " * (8 * 1024 * 1024 + 1)
        for name, payload in (
            ("depth", nested),
            ("nodes", excessive_nodes),
            ("bytes", excessive_bytes),
        ):
            with (
                self.subTest(bound=name),
                self.assertRaisesRegex(
                    SportradarTennisV3CandidateError,
                    r"\Acandidate_payload_invalid\Z",
                ),
            ):
                _strict_json(payload)

    def test_n_p3_noninteger_json_constants_fail_closed(self) -> None:
        """Catches acceptance of non-JSON constants or binary floats."""
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            SportradarTennisV3CandidateError,
            _strict_json,
        )

        cases = (
            b'{"value":NaN}',
            b'{"value":Infinity}',
            b'{"value":-Infinity}',
            b'{"value":1e2}',
            b'{"value":1.0}',
        )
        for payload in cases:
            with (
                self.subTest(payload=payload),
                self.assertRaisesRegex(
                    SportradarTennisV3CandidateError,
                    r"\Acandidate_payload_invalid\Z",
                ),
            ):
                _strict_json(payload)

    def test_n_p4_every_summary_required_key_is_required(
        self,
    ) -> None:
        """Catches accidental defaults for any required summary field."""
        summary = _fixture("sportradar_tennis_summary_v3.json")
        containers = (
            ("summary", (), tuple(summary)),
            (
                "sport_event",
                ("sport_event",),
                tuple(summary["sport_event"]),
            ),
            (
                "home_competitor",
                ("sport_event", "competitors", 0),
                tuple(summary["sport_event"]["competitors"][0]),
            ),
            (
                "away_competitor",
                ("sport_event", "competitors", 1),
                tuple(summary["sport_event"]["competitors"][1]),
            ),
            (
                "sport_event_status",
                ("sport_event_status",),
                tuple(summary["sport_event_status"]),
            ),
            (
                "coverage",
                ("coverage",),
                tuple(summary["coverage"]),
            ),
        )
        for container_name, path, keys in containers:
            for key in keys:
                document = copy.deepcopy(summary)
                target = document
                for component in path:
                    target = target[component]
                del target[key]
                with self.subTest(
                    container=container_name,
                    key=key,
                ):
                    self._assert_summary_error(
                        canonical_json_bytes(document),
                        "candidate_payload_invalid",
                    )

    def test_n_p5_timeline_unknown_keys_at_each_depth_are_schema_unknown(
        self,
    ) -> None:
        """Catches ignored extension bags at any timeline object depth."""
        fixture = _fixture("sportradar_tennis_timeline_v3.json")
        base = fixture["cases"][0]["payload"]
        cases: dict[str, dict[str, object]] = {}
        top = copy.deepcopy(base)
        top["unexpected"] = "synthetic"
        cases["top"] = top
        sport_event = copy.deepcopy(base)
        sport_event["sport_event"]["unexpected"] = "synthetic"
        cases["sport_event"] = sport_event
        competitor = copy.deepcopy(base)
        competitor["sport_event"]["competitors"][0][
            "unexpected"
        ] = "synthetic"
        cases["competitor"] = competitor
        snapshot = copy.deepcopy(base)
        snapshot["timeline"][0]["unexpected"] = "synthetic"
        cases["snapshot"] = snapshot
        point = copy.deepcopy(base)
        point["timeline"][1]["unexpected"] = "synthetic"
        cases["point"] = point

        for depth, document in cases.items():
            with self.subTest(depth=depth):
                self._assert_timeline_error(
                    document,
                    "candidate_schema_unknown",
                )

    def test_n_p6_unknown_enumerations_are_schema_unknown(
        self,
    ) -> None:
        """Catches permissive enum fallbacks for status, score, and events."""
        summary = _fixture("sportradar_tennis_summary_v3.json")
        summary_cases: dict[str, dict[str, object]] = {}
        status = copy.deepcopy(summary)
        status["sport_event_status"]["status"] = "UNKNOWN"
        summary_cases["status"] = status
        termination = copy.deepcopy(summary)
        termination["sport_event_status"]["termination"] = "UNKNOWN"
        summary_cases["termination"] = termination
        score = copy.deepcopy(summary)
        score["sport_event_status"]["points_home"] = "DEUCE"
        summary_cases["score"] = score
        match_type = copy.deepcopy(summary)
        match_type["sport_event"]["type"] = "DOUBLES"
        summary_cases["match_type"] = match_type
        qualifier = copy.deepcopy(summary)
        qualifier["sport_event"]["competitors"][0][
            "qualifier"
        ] = "PLAYER_ONE"
        summary_cases["qualifier"] = qualifier
        for name, document in summary_cases.items():
            with self.subTest(route="summary", enum=name):
                self._assert_summary_error(
                    canonical_json_bytes(document),
                    "candidate_schema_unknown",
                )

        fixture = _fixture("sportradar_tennis_timeline_v3.json")
        base = fixture["cases"][0]["payload"]
        unknown_event = copy.deepcopy(base)
        unknown_event["timeline"][1]["type"] = "GAME"
        self._assert_timeline_error(
            unknown_event,
            "candidate_schema_unknown",
        )
        lifecycle_case = copy.deepcopy(
            fixture["cases"][1]["payload"]
        )
        lifecycle_case["timeline"][0]["lifecycle"] = "TIMEOUT"
        entry = lifecycle_case["timeline"][0]
        self._assert_timeline_error(
            lifecycle_case,
            "candidate_schema_unknown",
            source_wall_ns=_parser_matrix._utc_ns(
                entry["event_time"]
            ),
            source_generated_ns=_parser_matrix._utc_ns(
                lifecycle_case["generated_at"]
            ),
            provider_sequence="c0.r1",
        )

    def test_n_p7_secret_shaped_objects_keys_and_urls_fail_closed(
        self,
    ) -> None:
        """Catches secret-bearing extensions before schema diagnostics."""
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            SportradarTennisV3CandidateError,
            _strict_json,
        )

        summary = _fixture("sportradar_tennis_summary_v3.json")
        direct_cases: dict[str, dict[str, object]] = {}
        for key in ("header", "request"):
            document = copy.deepcopy(summary)
            document[key] = {}
            direct_cases[key] = document
        secret_key = copy.deepcopy(summary)
        secret_key["api_key"] = "redacted"
        direct_cases["secret_key"] = secret_key
        unsafe_url = copy.deepcopy(summary)
        unsafe_url["event_id"] = (
            "https://synthetic-user@invalid.example/event"
        )
        direct_cases["unsafe_url"] = unsafe_url

        for name, document in direct_cases.items():
            with (
                self.subTest(secret_shape=name),
                self.assertRaisesRegex(
                    SportradarTennisV3CandidateError,
                    r"\Acandidate_secret_material\Z",
                ),
            ):
                _strict_json(canonical_json_bytes(document))

        environment = copy.deepcopy(summary)
        environment["environment"] = {}
        self._assert_summary_error(
            canonical_json_bytes(environment),
            "candidate_secret_material",
        )

    def test_n_p8_binding_owned_match_and_player_ids_must_match(
        self,
    ) -> None:
        """Catches payload ownership of binding-selected match/player IDs."""
        summary = _fixture("sportradar_tennis_summary_v3.json")
        cases: dict[str, dict[str, object]] = {}
        match = copy.deepcopy(summary)
        match["sport_event"]["id"] = "synthetic-other-match"
        cases["match"] = match
        home = copy.deepcopy(summary)
        home["sport_event"]["competitors"][0]["id"] = (
            "synthetic-other-home"
        )
        cases["home_player"] = home
        away = copy.deepcopy(summary)
        away["sport_event"]["competitors"][1]["id"] = (
            "synthetic-other-away"
        )
        cases["away_player"] = away

        for name, document in cases.items():
            with self.subTest(identity=name):
                self._assert_summary_error(
                    canonical_json_bytes(document),
                    "candidate_captured_parent_mismatch",
                )

    def test_n_p10_noncanonical_and_invalid_timestamps_fail_closed(
        self,
    ) -> None:
        """Catches timestamp repair, offset acceptance, or range overflow."""
        summary = _fixture("sportradar_tennis_summary_v3.json")
        values = (
            "2030-01-15T17:00:00.1234567890Z",
            "2030-01-15T17:00:00z",
            "2030-01-15T17:00:00+00:00",
            " 2030-01-15T17:00:00Z",
            "2030-02-30T17:00:00Z",
            "2030-01-15T17:00:60Z",
            "1969-12-31T23:59:59Z",
        )
        for value in values:
            document = copy.deepcopy(summary)
            document["source_event_time"] = value
            with self.subTest(timestamp=value):
                self._assert_summary_error(
                    canonical_json_bytes(document),
                    "candidate_payload_invalid",
                )

    def test_n_p13_invalid_score_tiebreak_and_winner_combinations_fail(
        self,
    ) -> None:
        """Catches unreachable or internally contradictory snapshots."""
        summary = _fixture("sportradar_tennis_summary_v3.json")
        cases: dict[str, dict[str, object]] = {}
        scheduled_winner = copy.deepcopy(summary)
        scheduled_winner["sport_event_status"]["winner_id"] = (
            "synthetic-player-home"
        )
        cases["scheduled_winner"] = scheduled_winner
        tiebreak_points = copy.deepcopy(summary)
        tiebreak_points["sport_event_status"][
            "tiebreak_points_home"
        ] = 1
        cases["tiebreak_points_outside_tiebreak"] = tiebreak_points
        tiebreak_server = copy.deepcopy(summary)
        tiebreak_server["sport_event_status"][
            "tiebreak_first_server_id"
        ] = "synthetic-player-home"
        cases["tiebreak_server_outside_tiebreak"] = tiebreak_server
        retired_without_termination = copy.deepcopy(summary)
        retired_without_termination["sport_event_status"][
            "retired_id"
        ] = "synthetic-player-away"
        cases["retired_without_termination"] = (
            retired_without_termination
        )
        impossible_set = copy.deepcopy(summary)
        impossible_set["sport_event_status"]["completed_sets"] = [
            {
                "games_home": 6,
                "games_away": 6,
                "tiebreak_points_home": None,
                "tiebreak_points_away": None,
            }
        ]
        cases["impossible_completed_set"] = impossible_set

        for name, document in cases.items():
            with self.subTest(combination=name):
                self._assert_summary_error(
                    canonical_json_bytes(document),
                    "candidate_payload_invalid",
                )

    def test_n_p16_epoch_advance_requires_complete_snapshot(
        self,
    ) -> None:
        """Catches correction-epoch advancement on point or lifecycle deltas."""
        fixture = _fixture("sportradar_tennis_timeline_v3.json")
        base = fixture["cases"][0]["payload"]

        point = copy.deepcopy(base)
        point["timeline"] = point["timeline"][:2]
        point["timeline"][1]["correction_epoch"] = 1
        point["timeline"][1]["revision"] = 1
        point["generated_at"] = point["timeline"][1]["generated_at"]
        point_entry = point["timeline"][-1]
        self._assert_timeline_error(
            point,
            "candidate_payload_invalid",
            source_wall_ns=_parser_matrix._utc_ns(
                point_entry["event_time"]
            ),
            source_generated_ns=_parser_matrix._utc_ns(
                point["generated_at"]
            ),
            provider_sequence="c1.r1",
        )

    def test_n_p17_revision_gap_is_returned_for_task2_to_block(
        self,
    ) -> None:
        """Catches parser-side gap repair or loss of the Task-2 gap reason."""
        from inci_tennis_adapters.registry import (
            normalize_sportradar_candidate_raw,
        )
        from inci_tennis_expert.contracts import (
            ExpertSynchronizationDraftV1,
            ProviderPoint,
            TennisTransitionReason,
            TransitionDisposition,
        )

        fixture = _fixture("sportradar_tennis_timeline_v3.json")
        base = fixture["cases"][0]["payload"]
        point = copy.deepcopy(base["timeline"][1])
        point["id"] = "synthetic-gap-point"
        point["revision"] = 3
        document = {
            "generated_at": point["generated_at"],
            "sport_event": copy.deepcopy(base["sport_event"]),
            "timeline": [point],
        }
        payload = canonical_json_bytes(document)
        (
            adapter,
            provider_binding,
            universe,
            captured,
            durable_raw,
        ) = self._bind(
            payload=payload,
            event_type="sportradar_tennis_timeline_v3",
            source_wall_ns=_parser_matrix._utc_ns(
                point["event_time"]
            ),
            source_generated_ns=_parser_matrix._utc_ns(
                point["generated_at"]
            ),
            provider_sequence="c0.r3",
        )
        prior = self._prior(
            "synthetic-prior-live",
            provider_binding=provider_binding,
            universe=universe,
        )

        events = adapter.normalize_timeline(
            payload,
            prior=prior,
            received_monotonic_ns=captured.local_monotonic_ns,
        )
        self.assertEqual(len(events), 1)
        self.assertIs(type(events[0]), ProviderPoint)
        self.assertEqual(events[0].revision, 3)
        drafts = normalize_sportradar_candidate_raw(
            provider_binding=provider_binding,
            universe=universe,
            captured=captured,
            durable_raw=durable_raw,
            prior=prior,
        )
        self.assertEqual(len(drafts), 1)
        self.assertIs(type(drafts[0]), ExpertSynchronizationDraftV1)
        transition = drafts[0].evidence.tennis_transition
        self.assertIs(
            transition.disposition,
            TransitionDisposition.BLOCKED,
        )
        self.assertIs(
            transition.reason,
            TennisTransitionReason.PROVIDER_EVENT_GAP,
        )
        self.assertEqual(transition.state.expected_revision, 2)
        self.assertEqual(transition.state.observed_revision, 3)

    def test_n_p18_stale_and_equal_conflicting_events_are_not_repaired(
        self,
    ) -> None:
        """Catches stale/conflicting coordinate rewriting in the parser."""
        from inci_tennis_adapters.registry import (
            normalize_sportradar_candidate_raw,
        )
        from inci_tennis_expert.contracts import (
            ExpertSynchronizationDraftV1,
            TennisTransitionReason,
            TransitionDisposition,
        )

        fixture = _fixture("sportradar_tennis_timeline_v3.json")
        base = fixture["cases"][0]["payload"]
        cases = (
            (
                "stale",
                "synthetic-prior-suspended",
                TennisTransitionReason.PROVIDER_EVENT_STALE,
            ),
            (
                "equal_conflicting",
                "synthetic-prior-live",
                TennisTransitionReason.PROVIDER_EVENT_CONFLICT,
            ),
        )
        for name, prior_case, expected_reason in cases:
            point = copy.deepcopy(base["timeline"][1])
            point["id"] = f"synthetic-{name}-point"
            point["revision"] = 1
            document = {
                "generated_at": point["generated_at"],
                "sport_event": copy.deepcopy(base["sport_event"]),
                "timeline": [point],
            }
            payload = canonical_json_bytes(document)
            (
                _,
                provider_binding,
                universe,
                captured,
                durable_raw,
            ) = self._bind(
                payload=payload,
                event_type="sportradar_tennis_timeline_v3",
                source_wall_ns=_parser_matrix._utc_ns(
                    point["event_time"]
                ),
                source_generated_ns=_parser_matrix._utc_ns(
                    point["generated_at"]
                ),
                provider_sequence="c0.r1",
            )
            prior = self._prior(
                prior_case,
                provider_binding=provider_binding,
                universe=universe,
            )
            with self.subTest(case=name):
                drafts = normalize_sportradar_candidate_raw(
                    provider_binding=provider_binding,
                    universe=universe,
                    captured=captured,
                    durable_raw=durable_raw,
                    prior=prior,
                )
                self.assertEqual(len(drafts), 1)
                self.assertIs(
                    type(drafts[0]),
                    ExpertSynchronizationDraftV1,
                )
                event = drafts[0].evidence.provider_event
                self.assertEqual(event.revision, 1)
                transition = drafts[0].evidence.tennis_transition
                self.assertIs(
                    transition.disposition,
                    TransitionDisposition.BLOCKED,
                )
                self.assertIs(transition.reason, expected_reason)

        lifecycle = copy.deepcopy(base)
        lifecycle_entry = {
            "id": "synthetic-lifecycle-epoch-advance",
            "type": "LIFECYCLE",
            "revision": 1,
            "correction_epoch": 1,
            "event_time": "2030-01-15T18:00:01Z",
            "generated_at": "2030-01-15T18:00:01.100000000Z",
            "lifecycle": "SUSPEND",
            "winner_id": None,
            "retired_id": None,
            "server_id": "synthetic-player-home",
        }
        lifecycle["timeline"] = [
            lifecycle["timeline"][0],
            lifecycle_entry,
        ]
        lifecycle["generated_at"] = lifecycle_entry["generated_at"]
        self._assert_timeline_error(
            lifecycle,
            "candidate_payload_invalid",
            source_wall_ns=_parser_matrix._utc_ns(
                lifecycle_entry["event_time"]
            ),
            source_generated_ns=_parser_matrix._utc_ns(
                lifecycle["generated_at"]
            ),
            provider_sequence="c1.r1",
        )

    def test_n_p23_coordinate_bounds_and_bool_are_payload_invalid(
        self,
    ) -> None:
        """Catches coercion/defaulting of summary coordinates."""
        summary = _fixture("sportradar_tennis_summary_v3.json")
        for field in ("correction_epoch", "revision"):
            for value in (
                True,
                -1,
                9_223_372_036_854_775_808,
            ):
                document = copy.deepcopy(summary)
                document[field] = value
                with self.subTest(field=field, value=value):
                    self._assert_summary_error(
                        canonical_json_bytes(document),
                        "candidate_payload_invalid",
                    )

    def test_n_p26_unsupported_format_spellings_are_never_copied(
        self,
    ) -> None:
        """Catches permissive format inference from best-of alone."""
        summary = _fixture("sportradar_tennis_summary_v3.json")
        formats = (
            "no_ad_bo3",
            "short_set_bo3",
            "match_tiebreak_bo3",
            "advantage_final_set_bo3",
            "final_set_super_tiebreak_bo3",
        )
        for match_format in formats:
            document = copy.deepcopy(summary)
            document["sport_event"]["match_format"] = match_format
            with self.subTest(match_format=match_format):
                self._assert_summary_error(
                    canonical_json_bytes(document),
                    "candidate_payload_invalid",
                )

    def test_n_p27_invalid_id_spellings_are_rejected_not_projected(
        self,
    ) -> None:
        """Catches punctuation stripping, truncation, or prefix repair."""
        summary = _fixture("sportradar_tennis_summary_v3.json")
        identifiers = (
            "synthetic:event",
            "x" * 65,
            "-synthetic-event",
        )
        for identifier in identifiers:
            document = copy.deepcopy(summary)
            document["event_id"] = identifier
            with self.subTest(identifier=identifier):
                self._assert_summary_error(
                    canonical_json_bytes(document),
                    "candidate_payload_invalid",
                )

    def test_injected_fixture_capture_authorizer_stays_test_only(
        self,
    ) -> None:
        """Catches promotion of the synthetic input seam into production."""
        from tests.tennis_v1.sportradar_candidate_fixture_support import (
            InjectedFixtureCaptureAuthorizerV1,
            capture_public_candidate_fixture,
        )

        manifest, _ = _existing.CandidateOutputWriterTests._artifacts(
            self
        )
        with self.assertRaisesRegex(
            TypeError,
            r"\Asynthetic fixture capture binding required\Z",
        ):
            InjectedFixtureCaptureAuthorizerV1(
                session_manifest=manifest,
                source_entity_id="real-match",
                session_start_wall_ns=100,
            )
        with self.assertRaisesRegex(
            ValueError,
            r"\Asynthetic fixture clock outside session\Z",
        ):
            capture_public_candidate_fixture(
                b"{}",
                manifest=manifest,
                source_entity_id="synthetic-match",
                session_start_wall_ns=100,
                local_wall_ns=99,
                local_monotonic_ns=1,
                clock_uncertainty_ns=1,
                event_type="sportradar_tennis_summary_v3",
                source_wall_ns=100,
                source_generated_ns=100,
                provider_sequence="c0.r0",
            )

        repository = Path(__file__).resolve().parents[2]
        forbidden_import = "sportradar_candidate_fixture_support"
        production_roots = (
            "inci_tennis_adapters",
            "inci_tennis_io",
            "inci_tennis_runtime",
            "inci_tennis_expert",
            "tennis_v1",
            "tools",
        )
        for relative_root in production_roots:
            for source in (repository / relative_root).rglob("*.py"):
                self.assertNotIn(
                    forbidden_import,
                    source.read_text(encoding="utf-8"),
                    source,
                )


if __name__ == "__main__":
    unittest.main()
