from __future__ import annotations

import copy
import contextlib
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone
from hashlib import sha256
import inspect
import io
import json
import os
from pathlib import Path
import pickle
import stat
import tempfile
import unittest
import uuid

from inci_tennis_expert.contracts import canonical_expert_bytes
from inci_tennis_io import facade
from inci_tennis_io.ports import (
    CandidateObservationStartupAuthorityV1,
    CandidateQualificationAppendReceiptV1,
    CandidateQualificationCommitReceiptV1,
    CandidateQualificationOutputWriterV1,
    CandidateSourceSealCollectionAuthorityV1,
    CandidateSourceSealsV1,
    SportradarCandidatePreparedReadV1,
)
from tennis_v1.events import CapturedInput, SessionManifest
from tennis_v1.retention import RetentionCoordinator
from tennis_v1.session import session_manifest_sha256
from tests.tennis_v1.test_retention import MutableClock, make_config


OPAQUE_CANDIDATE_TYPES = (
    CandidateObservationStartupAuthorityV1,
    CandidateQualificationOutputWriterV1,
    CandidateSourceSealCollectionAuthorityV1,
    SportradarCandidatePreparedReadV1,
)
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _candidate_capture_envelope_sha256(
    captured: CapturedInput,
) -> str:
    return sha256(
        b"INCI-SPORTRADAR-CANDIDATE-CAPTURE-ENVELOPE-V1\0"
        + canonical_expert_bytes(
            (
                ("session_id", captured.session_id),
                ("event_type", captured.event_type),
                ("event_version", captured.event_version),
                ("source_kind", captured.source_kind.value),
                ("source_id", captured.source_id),
                ("source_entity_id", captured.source_entity_id),
                ("endpoint_id", captured.endpoint_id),
                ("endpoint_state", captured.endpoint_state.value),
                ("channel_id", captured.channel_id),
                ("channel_state", captured.channel_state.value),
                ("request_id", captured.request_id),
                ("request_id_state", captured.request_id_state.value),
                ("source_wall_ns", captured.source_wall_ns),
                ("source_generated_ns", captured.source_generated_ns),
                ("local_wall_ns", captured.local_wall_ns),
                ("local_monotonic_ns", captured.local_monotonic_ns),
                ("clock_uncertainty_ns", captured.clock_uncertainty_ns),
                ("connection_epoch", captured.connection_epoch),
                ("provider_sequence", captured.provider_sequence),
                ("content_type", captured.content_type),
                ("payload_encoding", captured.payload_encoding),
                ("payload_transform", captured.payload_transform),
                ("retention_delete_by_ns", captured.retention_delete_by_ns),
                ("payload_sha256", sha256(captured.payload).hexdigest()),
            )
        )
    ).hexdigest()


def _candidate_parser_evidence_sha256(
    captured: CapturedInput,
    *,
    parser_outcome: str,
    reason: str | None,
    output_contract_sha256s: tuple[str, ...],
    capabilities: tuple[tuple[str, bool], ...],
    first_correction_epoch: int | None,
    first_revision: int | None,
    last_correction_epoch: int | None,
    last_revision: int | None,
) -> str:
    return sha256(
        b"INCI-SPORTRADAR-CANDIDATE-PARSER-EVIDENCE-V1\0"
        + canonical_expert_bytes(
            (
                ("schema_version", 1),
                ("event_type", captured.event_type),
                ("event_version", captured.event_version),
                ("payload_sha256", sha256(captured.payload).hexdigest()),
                (
                    "capture_envelope_sha256",
                    _candidate_capture_envelope_sha256(captured),
                ),
                ("parser_outcome", parser_outcome),
                ("reason", reason),
                (
                    "output_contract_sha256s",
                    output_contract_sha256s,
                ),
                ("capabilities", capabilities),
                ("first_correction_epoch", first_correction_epoch),
                ("first_revision", first_revision),
                ("last_correction_epoch", last_correction_epoch),
                ("last_revision", last_revision),
            )
        )
    ).hexdigest()


class CandidateRegistrationSafetyTests(unittest.TestCase):
    def test_candidate_import_does_not_activate_any_production_registry(
        self,
    ) -> None:
        from tennis_v1 import adapter_contract
        from inci_tennis_adapters import registry

        self.assertEqual(adapter_contract._ADAPTER_REGISTRY, {})
        self.assertIs(type(registry.PRODUCTION_PROVIDER_REGISTRY), tuple)
        self.assertEqual(registry.PRODUCTION_PROVIDER_REGISTRY, ())

    def test_no_task7_source_issues_startup_authority(self) -> None:
        forbidden = (
            "issue_candidate_observation_startup",
            "_create_candidate_observation_startup",
            "CandidateObservationStartupAuthorityV1(",
        )
        paths = (
            Path("inci_tennis_adapters/candidate_contracts.py"),
            Path("inci_tennis_adapters/registry.py"),
            Path("inci_tennis_adapters/sportradar_tennis_v3.py"),
            Path("inci_tennis_io/provider_readonly.py"),
            Path(
                "inci_tennis_runtime/"
                "provider_qualification_controller.py"
            ),
            Path("tools/qualify_sportradar_tennis_v3.py"),
        )
        for path in paths:
            with self.subTest(path=str(path)):
                source = path.read_text(encoding="utf-8")
                for token in forbidden:
                    self.assertNotIn(token, source)


class CandidateValueContractTests(unittest.TestCase):
    def test_candidate_value_contracts_are_exact_private_values(self) -> None:
        from inci_tennis_adapters.candidate_contracts import (
            CandidateParserEvidenceV1,
            CandidateProviderBindingV1,
            CandidateQualificationDecisionV1,
            CandidateQuotaClosureV1,
        )

        expected = {
            CandidateQuotaClosureV1: (
                "schema_version",
                "usage",
                "declared",
                "demand",
                "requested_matches",
                "duration_seconds",
                "polling_interval_seconds",
                "maximum_candidate_trace_bytes",
                "quota_closure_sha256",
            ),
            CandidateProviderBindingV1: (
                "schema_version",
                "authority_scope",
                "provider_id",
                "product_tier",
                "source_lineage_id",
                "candidate_manifest_sha256",
                "provider_manifest_canonical_sha256",
                "provider_source_lineage_sha256",
                "candidate_authorization_sha256",
                "permission_artifact_sha256",
                "match_binding_universe_sha256",
                "binding_raw_artifact_sha256",
                "binding_review_artifact_sha256",
                "candidate_source_seals_sha256",
                "auth_contract_sha256",
                "quota_closure_sha256",
                "candidate_research_request_sha256",
                "session_id",
                "candidate_permission_scope_sha256",
                "candidate_preobservation_trace_sha256",
                "session_start_wall_ns",
                "session_end_wall_ns",
                "retention_delete_by_ns",
                "access_expires_at_ns",
                "analysis_expires_at_ns",
                "binding_sha256",
            ),
            CandidateQualificationDecisionV1: (
                "schema_version",
                "eligible_for_candidate_observation",
                "reasons",
                "binding",
                "quota",
                "decision_sha256",
            ),
            CandidateParserEvidenceV1: (
                "schema_version",
                "event_type",
                "event_version",
                "payload_sha256",
                "capture_envelope_sha256",
                "parser_outcome",
                "reason",
                "output_contract_sha256s",
                "capabilities",
                "first_correction_epoch",
                "first_revision",
                "last_correction_epoch",
                "last_revision",
                "evidence_sha256",
            ),
        }
        for contract, field_names in expected.items():
            with self.subTest(contract=contract.__name__):
                self.assertEqual(
                    tuple(field.name for field in fields(contract)),
                    field_names,
                )
                self.assertEqual(contract.__slots__, field_names)
                with self.assertRaises(TypeError):
                    contract()
                with self.assertRaises(TypeError):
                    type(
                        contract.__name__,
                        (contract,),
                        {"__module__": contract.__module__},
                    )

    def test_candidate_decision_requires_exact_reason_enums(self) -> None:
        from inci_tennis_adapters.candidate_contracts import (
            CandidateQualificationDecisionV1,
            candidate_decision_projection,
        )
        from tennis_v1.entitlements import QualificationReason

        with self.assertRaises(ValueError):
            CandidateQualificationDecisionV1._create(
                schema_version=1,
                eligible_for_candidate_observation=False,
                reasons=("capability_missing",),
                binding=None,
                quota=None,
            )
        decision = CandidateQualificationDecisionV1._create(
            schema_version=1,
            eligible_for_candidate_observation=False,
            reasons=(QualificationReason.CAPABILITY_MISSING,),
            binding=None,
            quota=None,
        )
        expected = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-DECISION-V1\0"
            + canonical_expert_bytes(
                candidate_decision_projection(decision)
            )
        ).hexdigest()
        self.assertEqual(decision.decision_sha256, expected)
        self.assertEqual(
            repr(decision),
            "<CandidateQualificationDecisionV1 redacted>",
        )
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.assertRaises(TypeError):
                operation(decision)
        with self.assertRaises(FrozenInstanceError):
            decision.reasons = ()  # type: ignore[misc]


class CandidateOfflineCapacityTests(unittest.TestCase):
    def test_trace_capacity_boundary_vectors_are_exact(self) -> None:
        from inci_tennis_io.provider_readonly import (
            candidate_maximum_trace_bytes,
        )

        vectors = (
            (1, 1_250, 268_423_168),
            (1, 1_251, 270_536_704),
            (10, 100, 253_628_416),
            (10, 101, 274_763_776),
            (1, 3_600, 765_104_128),
        )
        for matches, duration, expected in vectors:
            with self.subTest(matches=matches, duration=duration):
                self.assertEqual(
                    candidate_maximum_trace_bytes(
                        requested_matches=matches,
                        duration_seconds=duration,
                    ),
                    expected,
                )
        for invalid in (True, 0, 3_601):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    candidate_maximum_trace_bytes(
                        requested_matches=1,
                        duration_seconds=invalid,
                    )

    def test_quota_demand_uses_exact_utc_day_intersections(self) -> None:
        from inci_tennis_io.provider_readonly import candidate_quota_demand

        day_ns = 86_400_000_000_000
        demand = candidate_quota_demand(
            requested_matches=2,
            session_start_wall_ns=day_ns - 30_000_000_000,
            session_end_wall_ns=day_ns + 30_000_000_000,
        )
        self.assertEqual(
            (
                demand.requests_per_rolling_60_seconds,
                demand.requests_per_utc_calendar_day,
                demand.requests_per_rolling_second,
                demand.max_connections,
                demand.max_subscriptions,
                demand.resync_requests_per_rolling_hour,
            ),
            (18, 18, 18, 1, 2, 4),
        )


class CandidateCliSafetyTests(unittest.TestCase):
    def test_help_and_argument_errors_are_fixed_and_side_effect_free(
        self,
    ) -> None:
        from tools.qualify_sportradar_tennis_v3 import main

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as help_exit,
        ):
            main(("--help",))
        self.assertEqual(help_exit.exception.code, 0)
        self.assertIn("--manifest", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as syntax_exit,
        ):
            main(("--demo",))
        self.assertEqual(syntax_exit.exception.code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "candidate observation denied: argument parsing failed\n",
        )

    def test_invalid_offline_inputs_have_one_redacted_denial(self) -> None:
        from tools.qualify_sportradar_tennis_v3 import main

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(
                (
                    "--manifest",
                    "/definitely-absent-manifest",
                    "--binding",
                    "/definitely-absent-authorization",
                    "--duration-seconds",
                    "10",
                    "--output-dir",
                    "/definitely-absent-output",
                )
            )
        self.assertEqual(exit_code, 4)
        self.assertEqual(
            stderr.getvalue(),
            "candidate observation denied: offline validation failed\n",
        )


class CandidatePortContractTests(unittest.TestCase):
    def test_candidate_capabilities_are_opaque_redacted_and_nontransferable(
        self,
    ) -> None:
        for capability_type in OPAQUE_CANDIDATE_TYPES:
            with self.subTest(capability=capability_type.__name__):
                with self.assertRaises(TypeError):
                    capability_type()
                with self.assertRaises(TypeError):
                    type("Hostile", (capability_type,), {})
                forged = object.__new__(capability_type)
                self.assertEqual(
                    repr(forged),
                    f"<{capability_type.__name__} redacted>",
                )
                for operation in (copy.copy, copy.deepcopy, pickle.dumps):
                    with self.assertRaises(TypeError):
                        operation(forged)

    def test_candidate_receipts_and_source_seals_have_exact_frozen_fields(
        self,
    ) -> None:
        expected = {
            CandidateQualificationAppendReceiptV1: (
                "schema_version",
                "session_id",
                "record_index",
                "record_type",
                "record_sha256",
                "trace_prefix_sha256",
                "durable_trace_length",
                "retention_delete_by_ns",
                "fsynced",
                "receipt_sha256",
            ),
            CandidateQualificationCommitReceiptV1: (
                "schema_version",
                "session_id",
                "final_basename",
                "summary_sha256",
                "summary_length",
                "trace_sha256",
                "trace_length",
                "trace_record_count",
                "terminal_record_sha256",
                "terminal_reason",
                "retention_delete_by_ns",
                "files_fsynced",
                "staging_directory_fsynced",
                "parent_fsynced",
                "receipt_sha256",
            ),
            CandidateSourceSealsV1: (
                "schema_version",
                "normalizer_pins",
                "candidate_adapter_inventory_sha256",
                "candidate_io_bridge_inventory_sha256",
                "provider_transport_source_sha256",
                "qualification_controller_source_sha256",
                "qualification_tool_source_sha256",
                "candidate_manifest_schema_sha256",
                "candidate_authorization_schema_sha256",
                "candidate_output_schema_sha256",
                "qualification_protocol_sha256",
                "candidate_source_seals_sha256",
            ),
        }
        for contract, field_names in expected.items():
            with self.subTest(contract=contract.__name__):
                self.assertEqual(
                    tuple(field.name for field in fields(contract)),
                    field_names,
                )
                self.assertEqual(
                    tuple(inspect.signature(contract).parameters),
                    ("_", "__"),
                )
                with self.assertRaises(TypeError):
                    contract()
                self.assertEqual(
                    contract.__dataclass_params__.frozen,
                    True,
                )
                self.assertEqual(contract.__slots__, field_names)


class CandidateOutputWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temporary.name).resolve()
        self.clock = MutableClock(101)
        self.coordinator = RetentionCoordinator.acquire(
            make_config(self.root_path / "state"),
            clock_ns=self.clock,
        )
        self.coordinator.recover_and_purge()
        request = (
            self.coordinator.issue_expert_state_root_account_lock_request()
        )
        self.root_authority = facade.acquire_expert_journal_root(request)
        self.output_parent = self.root_path / "output"
        self.output_parent.mkdir(mode=0o700)

    def tearDown(self) -> None:
        self.coordinator.close()
        self.temporary.cleanup()

    def _artifacts(
        self,
        *,
        requested_provider_match_ids: tuple[str, ...] = (
            "synthetic-match",
        ),
    ) -> tuple[SessionManifest, dict[str, object]]:
        values = {
            "candidate_manifest_sha256": "1" * 64,
            "manifest_core_sha256": "2" * 64,
            "candidate_authorization_sha256": "3" * 64,
            "candidate_decision_sha256": "4" * 64,
            "candidate_binding_sha256": "5" * 64,
            "quota_closure_sha256": "6" * 64,
            "candidate_source_seals_sha256": "7" * 64,
            "match_binding_universe_sha256": "8" * 64,
            "requested_provider_match_ids": requested_provider_match_ids,
            "session_start_wall_ns": 100,
            "maximum_candidate_trace_bytes": 1_000_000,
        }
        session_name = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-SESSION-NAME-V1\0"
            + canonical_expert_bytes(
                (
                    (
                        "candidate_manifest_sha256",
                        values["candidate_manifest_sha256"],
                    ),
                    (
                        "manifest_core_sha256",
                        values["manifest_core_sha256"],
                    ),
                    (
                        "candidate_authorization_sha256",
                        values["candidate_authorization_sha256"],
                    ),
                    (
                        "candidate_source_seals_sha256",
                        values["candidate_source_seals_sha256"],
                    ),
                    (
                        "quota_closure_sha256",
                        values["quota_closure_sha256"],
                    ),
                    (
                        "match_binding_universe_sha256",
                        values["match_binding_universe_sha256"],
                    ),
                    (
                        "requested_provider_match_ids",
                        values["requested_provider_match_ids"],
                    ),
                    ("session_start_wall_ns", 100),
                    ("session_end_wall_ns", 200),
                    ("retention_delete_by_ns", 300),
                )
            )
        ).hexdigest()
        session_id = str(
            uuid.uuid5(
                uuid.UUID("8f4c1777-5fea-521a-aaab-60afdc79e328"),
                session_name,
            )
        )
        trace_sha256 = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-PREOBSERVATION-TRACE-V1\0"
            + canonical_expert_bytes(
                (
                    ("trace_state", "empty_pre_observation"),
                    ("session_id", session_id),
                    (
                        "candidate_manifest_sha256",
                        values["candidate_manifest_sha256"],
                    ),
                    (
                        "manifest_core_sha256",
                        values["manifest_core_sha256"],
                    ),
                    (
                        "candidate_authorization_sha256",
                        values["candidate_authorization_sha256"],
                    ),
                    (
                        "candidate_source_seals_sha256",
                        values["candidate_source_seals_sha256"],
                    ),
                    (
                        "quota_closure_sha256",
                        values["quota_closure_sha256"],
                    ),
                    (
                        "match_binding_universe_sha256",
                        values["match_binding_universe_sha256"],
                    ),
                    (
                        "requested_provider_match_ids",
                        values["requested_provider_match_ids"],
                    ),
                    ("retention_delete_by_ns", 300),
                )
            )
        ).hexdigest()
        manifest = SessionManifest(
            schema_version=1,
            session_id=session_id,
            created_wall_ns=90,
            config_file_sha256=values["candidate_manifest_sha256"],
            config_canonical_sha256=values["manifest_core_sha256"],
            code_sha256=values["candidate_source_seals_sha256"],
            research_request_sha256="9" * 64,
            provider_id="sportradar",
            product_tier="synthetic-tier",
            source_lineage_id="synthetic-lineage",
            provider_manifest_file_sha256=(
                values["candidate_manifest_sha256"]
            ),
            provider_manifest_canonical_sha256=(
                values["manifest_core_sha256"]
            ),
            entitlement_id_sha256="a" * 64,
            terms_version="synthetic-terms",
            permission_artifact_sha256="b" * 64,
            qualification_artifact_sha256=(
                values["candidate_authorization_sha256"]
            ),
            qualification_trace_sha256=trace_sha256,
            adapter_code_sha256="c" * 64,
            auth_contract_sha256="d" * 64,
            quota_contract_sha256=values["quota_closure_sha256"],
            session_end_ns=200,
            required_retention_until_ns=300,
            access_expires_at_ns=220,
            analysis_expires_at_ns=400,
            research_evaluable=False,
        )
        return manifest, values

    def _eligible_candidate_inputs(
        self,
        source_seals: CandidateSourceSealsV1,
        *,
        required_capabilities: tuple[str, ...] | None = None,
        duration_seconds: int = 1_200,
    ) -> object:
        from inci_tennis_adapters.candidate_contracts import (
            REQUIRED_CANDIDATE_CAPABILITIES,
            candidate_quotas_projection,
            candidate_usage_projection,
        )
        from inci_tennis_expert.contracts import (
            MatchFormat,
            PlayerSide,
            compute_expert_provider_source_lineage_sha256,
        )
        from inci_tennis_io.provider_readonly import (
            SPORTRADAR_CANDIDATE_USAGE,
            ValidatedCandidateOfflineArtifactsV1,
            candidate_maximum_trace_bytes,
            candidate_quota_demand,
        )
        from tennis_v1.canonical import canonical_json_bytes
        from tennis_v1.entitlements import CoverageStratum, RequestedStratum
        from tests.tennis_v1.test_expert_contracts import (
            binding_market_metadata,
            binding_metadata,
            binding_universe,
            match_binding,
        )

        manifest_core_sha256 = "2" * 64
        capabilities = (
            REQUIRED_CANDIDATE_CAPABILITIES
            if required_capabilities is None
            else required_capabilities
        )
        provider_lineage_sha256 = (
            compute_expert_provider_source_lineage_sha256(
                "sportradar",
                "synthetic-tier",
                "synthetic-lineage",
                manifest_core_sha256,
            )
        )
        home_market = binding_market_metadata(
            yes_provider_player_id="synthetic-player-home",
            yes_canonical_player_id="synthetic-canonical-home",
        )
        away_market = binding_market_metadata(
            player_side=PlayerSide.AWAY,
            yes_provider_player_id="synthetic-player-away",
            yes_canonical_player_id="synthetic-canonical-away",
        )
        metadata = binding_metadata(
            canonical_match_id="synthetic-canonical-match",
            canonical_home_player_id="synthetic-canonical-home",
            canonical_away_player_id="synthetic-canonical-away",
            markets=(home_market, away_market),
        )
        binding = match_binding(
            provider_match_id="synthetic-match-1",
            canonical_match_id="synthetic-canonical-match",
            provider_source_id="sportradar",
            revision_domain_id="synthetic-revision-domain",
            source_lineage_sha256=provider_lineage_sha256,
            provider_home_player_id="synthetic-player-home",
            provider_away_player_id="synthetic-player-away",
            scheduled_start_wall_ns=1_894_730_400_000_000_000,
        )
        universe = binding_universe(
            bindings=(binding,),
            metadata=(metadata,),
        )
        required_strata = (
            RequestedStratum(
                stratum=CoverageStratum(
                    sport="tennis",
                    tour="tour-atp",
                    competition_tier="tier-250",
                    match_format=(
                        MatchFormat
                        .STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS
                        .value
                    ),
                    round_code="round-1",
                ),
                matches=1,
            ),
        )
        strata_projection = (
            (
                ("sport", "tennis"),
                ("tour", "tour-atp"),
                ("competition_tier", "tier-250"),
                (
                    "match_format",
                    (
                        MatchFormat
                        .STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS
                        .value
                    ),
                ),
                ("round_code", "round-1"),
                ("matches", 1),
            ),
        )
        start = 1_894_730_400_000_000_000
        duration = duration_seconds
        end = start + duration * 1_000_000_000
        demand = candidate_quota_demand(
            requested_matches=1,
            session_start_wall_ns=start,
            session_end_wall_ns=end,
        )
        authorization_evidence_sha256 = sha256(
            (
                b"INCI-SPORTRADAR-CANDIDATE-"
                b"AUTHORIZATION-EVIDENCE-V1\0"
            )
            + canonical_expert_bytes(
                (
                    (
                        "candidate_source_seals_sha256",
                        source_seals.candidate_source_seals_sha256,
                    ),
                    ("permission_artifact_sha256", "b" * 64),
                    (
                        "manifest_core_sha256",
                        manifest_core_sha256,
                    ),
                    (
                        "binding_manifest_sha256",
                        universe.raw_artifact_sha256,
                    ),
                    (
                        "binding_review_sha256",
                        universe.review.review_artifact_sha256,
                    ),
                    (
                        "requested_provider_match_ids",
                        ("synthetic-match-1",),
                    ),
                    (
                        "required_candidate_capabilities",
                        capabilities,
                    ),
                    ("required_strata", strata_projection),
                    ("duration_seconds", duration),
                    (
                        "usage",
                        candidate_usage_projection(
                            SPORTRADAR_CANDIDATE_USAGE
                        ),
                    ),
                    (
                        "declared_quotas",
                        candidate_quotas_projection(demand),
                    ),
                )
            )
        ).hexdigest()
        auth_contract_sha256 = sha256(
            b"INCI-AUTH-CONTRACT-V1\0"
            + canonical_json_bytes(
                {
                    "credential_env_names": ["SPORTRADAR_API_KEY"],
                    "mode": "api_key",
                }
            )
        ).hexdigest()
        allowed_strata_sha256 = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-REQUIRED-STRATA-V1\0"
            + canonical_expert_bytes(strata_projection)
        ).hexdigest()
        return ValidatedCandidateOfflineArtifactsV1(
            schema_version=1,
            artifact_created_wall_ns=start - 1,
            provider_id="sportradar",
            product_tier="synthetic-tier",
            source_lineage_id="synthetic-lineage",
            terms_version="synthetic-terms",
            permission_artifact_sha256="b" * 64,
            candidate_manifest_sha256="1" * 64,
            manifest_core_sha256=manifest_core_sha256,
            candidate_authorization_sha256="3" * 64,
            authorization_evidence_sha256=(
                authorization_evidence_sha256
            ),
            binding_manifest_sha256=universe.raw_artifact_sha256,
            binding_review_sha256=(
                universe.review.review_artifact_sha256
            ),
            provider_source_lineage_sha256=provider_lineage_sha256,
            declared_quotas=demand,
            demand_quotas=demand,
            requested_provider_match_ids=("synthetic-match-1",),
            required_candidate_capabilities=capabilities,
            required_strata=required_strata,
            session_start_wall_ns=start,
            session_end_wall_ns=end,
            required_retention_until_ns=end + 600_000_000_000,
            access_expires_at_ns=end + 1_200_000_000_000,
            analysis_expires_at_ns=end + 3_600_000_000_000,
            duration_seconds=duration,
            maximum_candidate_trace_bytes=(
                candidate_maximum_trace_bytes(
                    requested_matches=1,
                    duration_seconds=duration,
                )
            ),
            auth_contract_sha256=auth_contract_sha256,
            allowed_strata_sha256=allowed_strata_sha256,
            universe=universe,
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
        from inci_tennis_io.provider_readonly import (
            build_sportradar_candidate_session_manifest,
        )
        from inci_tennis_runtime.provider_qualification_controller import (
            evaluate_sportradar_candidate_offline,
        )
        from tennis_v1.entitlements import QualifiedProviderBinding
        from tests.tennis_v1.sportradar_candidate_fixture_support import (
            capture_public_candidate_fixture,
        )
        from tests.tennis_v1.test_events import event_with

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
        manifest = build_sportradar_candidate_session_manifest(
            artifacts=artifacts,
            source_seals=source_seals,
            decision=decision,
        )

        local_wall_ns = artifacts.session_start_wall_ns + 1_000_000_000
        local_monotonic_ns = 700
        captured = capture_public_candidate_fixture(
            payload,
            manifest=manifest,
            source_entity_id="synthetic-match-1",
            session_start_wall_ns=artifacts.session_start_wall_ns,
            local_wall_ns=local_wall_ns,
            local_monotonic_ns=local_monotonic_ns,
            clock_uncertainty_ns=2,
            event_type=event_type,
            source_wall_ns=source_wall_ns,
            source_generated_ns=source_generated_ns,
            provider_sequence=provider_sequence,
        )
        durable_raw = event_with(
            payload,
            journal_version=1,
            ingest_seq=1,
            session_id=captured.session_id,
            event_type=captured.event_type,
            event_version=captured.event_version,
            source_kind=captured.source_kind,
            source_id=captured.source_id,
            source_entity_id=captured.source_entity_id,
            endpoint_id=captured.endpoint_id,
            endpoint_state=captured.endpoint_state,
            channel_id=captured.channel_id,
            channel_state=captured.channel_state,
            request_id=captured.request_id,
            request_id_state=captured.request_id_state,
            source_wall_ns=captured.source_wall_ns,
            source_generated_ns=captured.source_generated_ns,
            local_wall_ns=captured.local_wall_ns,
            local_monotonic_ns=captured.local_monotonic_ns,
            clock_uncertainty_ns=captured.clock_uncertainty_ns,
            connection_epoch=captured.connection_epoch,
            provider_sequence=captured.provider_sequence,
            content_type=captured.content_type,
            payload_encoding=captured.payload_encoding,
            payload_transform=captured.payload_transform,
            retention_delete_by_ns=captured.retention_delete_by_ns,
        )

        def as_utc(ns: int) -> datetime:
            return datetime.fromtimestamp(
                ns // 1_000_000_000,
                tz=timezone.utc,
            )

        provider_binding = QualifiedProviderBinding(
            provider_id="sportradar",
            product_tier=artifacts.product_tier,
            source_lineage_id=artifacts.source_lineage_id,
            entitlement_id_sha256="a" * 64,
            manifest_file_sha256=artifacts.candidate_manifest_sha256,
            manifest_canonical_sha256=artifacts.manifest_core_sha256,
            qualification_artifact_sha256=(
                artifacts.candidate_authorization_sha256
            ),
            permission_artifact_sha256=(
                artifacts.permission_artifact_sha256
            ),
            qualification_trace_sha256="c" * 64,
            adapter_code_sha256=(
                source_seals.candidate_adapter_inventory_sha256
            ),
            auth_contract_sha256=artifacts.auth_contract_sha256,
            quota_contract_sha256=decision.quota.quota_closure_sha256,
            session_end_utc=as_utc(artifacts.session_end_wall_ns),
            required_retention_until=as_utc(
                artifacts.required_retention_until_ns
            ),
            access_expires_at=as_utc(artifacts.access_expires_at_ns),
            analysis_expires_at=as_utc(artifacts.analysis_expires_at_ns),
            qualified_until=as_utc(artifacts.analysis_expires_at_ns),
        )
        return (
            provider_binding,
            artifacts.universe,
            captured,
            durable_raw,
        )

    def test_source_seals_are_exact_and_collection_authority_is_one_shot(
        self,
    ) -> None:
        authority = (
            facade.issue_sportradar_candidate_source_seal_collection_authority(
                self.root_authority
            )
        )
        seals = facade.collect_sportradar_candidate_source_seals(authority)
        self.assertIs(type(seals), CandidateSourceSealsV1)
        self.assertEqual(
            tuple(
                (
                    pin.source_kind,
                    pin.source_id,
                    pin.event_type,
                    pin.event_version,
                    pin.normalizer_id,
                )
                for pin in seals.normalizer_pins
            ),
            (
                (
                    "provider",
                    "sportradar",
                    "sportradar_tennis_summary_v3",
                    1,
                    "sportradar-tennis-summary-v3",
                ),
                (
                    "provider",
                    "sportradar",
                    "sportradar_tennis_timeline_v3",
                    1,
                    "sportradar-tennis-timeline-v3",
                ),
                (
                    "provider",
                    "sportradar",
                    "sportradar_tennis_transport_error_v1",
                    1,
                    "sportradar-tennis-transport-error-v1",
                ),
            ),
        )
        for field in fields(CandidateSourceSealsV1):
            if field.name in {"schema_version", "normalizer_pins"}:
                continue
            value = getattr(seals, field.name)
            self.assertRegex(value, r"\A[0-9a-f]{64}\Z")
            self.assertNotEqual(value, "0" * 64)
        with self.assertRaisesRegex(
            ValueError,
            r"\Acandidate_source_seal_collection_failed\Z",
        ):
            facade.collect_sportradar_candidate_source_seals(authority)

    def test_candidate_decision_manifest_and_capture_authority_are_inert(
        self,
    ) -> None:
        from inci_tennis_io.provider_readonly import (
            CandidateObservationUnavailable,
            build_sportradar_candidate_session_manifest,
            issue_sportradar_candidate_capture_authorities,
        )
        from inci_tennis_runtime.provider_qualification_controller import (
            evaluate_sportradar_candidate_offline,
        )
        from tennis_v1.capture import (
            capture_public_json,
            redacted_provenance,
        )
        from tennis_v1.entitlements import QualificationReason

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
        self.assertTrue(decision.eligible_for_candidate_observation)
        self.assertEqual(
            decision.reasons,
            (QualificationReason.ELIGIBLE,),
        )
        manifest = build_sportradar_candidate_session_manifest(
            artifacts=artifacts,
            source_seals=source_seals,
            decision=decision,
        )
        self.assertFalse(manifest.research_evaluable)
        self.assertEqual(
            (
                manifest.session_id,
                manifest.config_file_sha256,
                manifest.config_canonical_sha256,
                manifest.code_sha256,
                manifest.provider_id,
                manifest.adapter_code_sha256,
                manifest.quota_contract_sha256,
            ),
            (
                decision.binding.session_id,
                decision.binding.candidate_manifest_sha256,
                decision.binding.provider_manifest_canonical_sha256,
                decision.binding.candidate_source_seals_sha256,
                "sportradar",
                source_seals.candidate_adapter_inventory_sha256,
                decision.quota.quota_closure_sha256,
            ),
        )
        authorities = issue_sportradar_candidate_capture_authorities(
            artifacts=artifacts,
            source_seals=source_seals,
            decision=decision,
            session_manifest=manifest,
        )
        self.assertEqual(len(authorities), 1)
        with self.assertRaises(CandidateObservationUnavailable):
            capture_public_json(
                b"{}",
                authority=authorities[0],
                content_type="application/json",
                request_id=redacted_provenance(),
                event_type="sportradar_tennis_summary_v3",
                event_version=1,
                source_wall_ns=1,
                source_generated_ns=1,
                provider_sequence="1",
            )

    def test_candidate_manifest_recomputes_every_derived_binding_equation(
        self,
    ) -> None:
        from inci_tennis_adapters.candidate_contracts import (
            CandidateProviderBindingV1,
            CandidateQuotaClosureV1,
        )
        from inci_tennis_io.provider_readonly import (
            CandidateOfflineValidationError,
            build_sportradar_candidate_session_manifest,
            make_sportradar_candidate_eligible_decision,
        )
        from inci_tennis_runtime.provider_qualification_controller import (
            evaluate_sportradar_candidate_offline,
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
        self.assertIsNotNone(decision.quota)
        binding = decision.binding
        quota = decision.quota
        assert binding is not None
        assert quota is not None

        def forge_binding(
            **changes: object,
        ) -> CandidateProviderBindingV1:
            values = {
                field.name: getattr(binding, field.name)
                for field in fields(CandidateProviderBindingV1)
                if field.name != "binding_sha256"
            }
            values.update(changes)
            return CandidateProviderBindingV1._create(
                **values  # type: ignore[arg-type]
            )

        changed_uuid = str(
            uuid.uuid5(
                uuid.UUID("8f4c1777-5fea-521a-aaab-60afdc79e328"),
                "tampered-session",
            )
        )
        mutations = (
            {"candidate_manifest_sha256": "f" * 64},
            {"candidate_authorization_sha256": "f" * 64},
            {"permission_artifact_sha256": "f" * 64},
            {"provider_source_lineage_sha256": "f" * 64},
            {"candidate_source_seals_sha256": "f" * 64},
            {"candidate_research_request_sha256": "f" * 64},
            {"session_id": changed_uuid},
            {"candidate_permission_scope_sha256": "f" * 64},
            {"candidate_preobservation_trace_sha256": "f" * 64},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                forged_decision = (
                    make_sportradar_candidate_eligible_decision(
                        binding=forge_binding(**mutation),
                        quota=quota,
                    )
                )
                with self.assertRaises(
                    CandidateOfflineValidationError
                ):
                    build_sportradar_candidate_session_manifest(
                        artifacts=artifacts,
                        source_seals=source_seals,
                        decision=forged_decision,
                    )

        changed_declared = replace(
            quota.declared,
            requests_per_utc_calendar_day=(
                quota.declared.requests_per_utc_calendar_day + 1
            ),
        )
        changed_quota = CandidateQuotaClosureV1._create(
            schema_version=quota.schema_version,
            usage=quota.usage,
            declared=changed_declared,
            demand=quota.demand,
            requested_matches=quota.requested_matches,
            duration_seconds=quota.duration_seconds,
            polling_interval_seconds=quota.polling_interval_seconds,
            maximum_candidate_trace_bytes=(
                quota.maximum_candidate_trace_bytes
            ),
        )
        quota_binding = forge_binding(
            quota_closure_sha256=changed_quota.quota_closure_sha256,
        )
        quota_decision = make_sportradar_candidate_eligible_decision(
            binding=quota_binding,
            quota=changed_quota,
        )
        with self.assertRaises(CandidateOfflineValidationError):
            build_sportradar_candidate_session_manifest(
                artifacts=artifacts,
                source_seals=source_seals,
                decision=quota_decision,
            )

    def test_missing_candidate_capability_returns_ineligible_decision(
        self,
    ) -> None:
        from inci_tennis_adapters.candidate_contracts import (
            REQUIRED_CANDIDATE_CAPABILITIES,
        )
        from inci_tennis_runtime.provider_qualification_controller import (
            evaluate_sportradar_candidate_offline,
        )
        from tennis_v1.entitlements import QualificationReason

        seal_authority = (
            facade.issue_sportradar_candidate_source_seal_collection_authority(
                self.root_authority
            )
        )
        source_seals = facade.collect_sportradar_candidate_source_seals(
            seal_authority
        )
        artifacts = self._eligible_candidate_inputs(
            source_seals,
            required_capabilities=REQUIRED_CANDIDATE_CAPABILITIES[:-1],
        )
        decision = evaluate_sportradar_candidate_offline(
            artifacts=artifacts,
            source_seals=source_seals,
        )
        self.assertFalse(decision.eligible_for_candidate_observation)
        self.assertEqual(
            decision.reasons,
            (QualificationReason.CAPABILITY_MISSING,),
        )
        self.assertIsNone(decision.binding)
        self.assertIsNone(decision.quota)

    def test_candidate_qualification_reasons_are_complete_and_sorted(
        self,
    ) -> None:
        from inci_tennis_adapters.candidate_contracts import (
            REQUIRED_CANDIDATE_CAPABILITIES,
        )
        from inci_tennis_runtime.provider_qualification_controller import (
            evaluate_sportradar_candidate_offline,
        )
        from tennis_v1.entitlements import QualificationReason

        seal_authority = (
            facade.issue_sportradar_candidate_source_seal_collection_authority(
                self.root_authority
            )
        )
        source_seals = facade.collect_sportradar_candidate_source_seals(
            seal_authority
        )
        eligible = self._eligible_candidate_inputs(source_seals)
        capacity = self._eligible_candidate_inputs(
            source_seals,
            duration_seconds=3_600,
        )
        unsupported_stratum = replace(
            eligible.required_strata[0],
            stratum=replace(
                eligible.required_strata[0].stratum,
                match_format="unsupported_format",
            ),
        )
        inadequate_quota = replace(
            eligible.declared_quotas,
            requests_per_rolling_60_seconds=(
                eligible.declared_quotas
                .requests_per_rolling_60_seconds
                - 1
            ),
        )
        cases = (
            (
                replace(
                    eligible,
                    required_candidate_capabilities=(
                        REQUIRED_CANDIDATE_CAPABILITIES[:-1]
                    ),
                ),
                (QualificationReason.CAPABILITY_MISSING,),
            ),
            (
                replace(
                    eligible,
                    required_strata=(unsupported_stratum,),
                ),
                (QualificationReason.FORMAT_UNSUPPORTED,),
            ),
            (
                replace(
                    eligible,
                    declared_quotas=inadequate_quota,
                ),
                (QualificationReason.QUOTA_INADEQUATE,),
            ),
            (
                capacity,
                (
                    QualificationReason
                    .QUALIFICATION_CAPACITY_INADEQUATE,
                ),
            ),
        )
        for artifacts, expected_reasons in cases:
            with self.subTest(expected_reasons=expected_reasons):
                decision = evaluate_sportradar_candidate_offline(
                    artifacts=artifacts,
                    source_seals=source_seals,
                )
                self.assertFalse(
                    decision.eligible_for_candidate_observation
                )
                self.assertEqual(decision.reasons, expected_reasons)
                self.assertIsNone(decision.binding)
                self.assertIsNone(decision.quota)

        multiple = replace(
            capacity,
            required_candidate_capabilities=(
                REQUIRED_CANDIDATE_CAPABILITIES[:-1]
            ),
            required_strata=(
                replace(
                    capacity.required_strata[0],
                    stratum=replace(
                        capacity.required_strata[0].stratum,
                        match_format="unsupported_format",
                    ),
                ),
            ),
            declared_quotas=replace(
                capacity.declared_quotas,
                requests_per_rolling_60_seconds=(
                    capacity.declared_quotas
                    .requests_per_rolling_60_seconds
                    - 1
                ),
            ),
        )
        multiple_decision = evaluate_sportradar_candidate_offline(
            artifacts=multiple,
            source_seals=source_seals,
        )
        expected_multiple = tuple(
            sorted(
                (
                    QualificationReason.CAPABILITY_MISSING,
                    QualificationReason.FORMAT_UNSUPPORTED,
                    QualificationReason.QUOTA_INADEQUATE,
                    QualificationReason
                    .QUALIFICATION_CAPACITY_INADEQUATE,
                ),
                key=lambda item: item.value,
            )
        )
        self.assertEqual(multiple_decision.reasons, expected_multiple)

        evidence_mismatch = replace(
            eligible,
            authorization_evidence_sha256="f" * 64,
        )
        mismatch_decision = evaluate_sportradar_candidate_offline(
            artifacts=evidence_mismatch,
            source_seals=source_seals,
        )
        self.assertEqual(
            mismatch_decision.reasons,
            (
                QualificationReason
                .QUALIFICATION_EVIDENCE_MISMATCH,
            ),
        )
        self.assertFalse(
            mismatch_decision.eligible_for_candidate_observation
        )
        self.assertIsNone(mismatch_decision.binding)
        self.assertIsNone(mismatch_decision.quota)

    def test_candidate_artifacts_reject_bool_integers_and_symlinked_paths(
        self,
    ) -> None:
        from inci_tennis_io.provider_readonly import (
            CandidateOfflineValidationError,
            _open_external_parent,
            _validate_output_directory,
        )

        seal_authority = (
            facade.issue_sportradar_candidate_source_seal_collection_authority(
                self.root_authority
            )
        )
        source_seals = facade.collect_sportradar_candidate_source_seals(
            seal_authority
        )
        eligible = self._eligible_candidate_inputs(source_seals)
        for changes in (
            {"schema_version": True},
            {"artifact_created_wall_ns": True},
            {"duration_seconds": True},
            {"maximum_candidate_trace_bytes": True},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(
                    CandidateOfflineValidationError
                ):
                    replace(eligible, **changes)

        actual_parent = self.root_path / "external-parent"
        actual_parent.mkdir(mode=0o700)
        symlink_parent = self.root_path / "external-link"
        symlink_parent.symlink_to(
            actual_parent,
            target_is_directory=True,
        )
        with self.assertRaises(CandidateOfflineValidationError):
            _open_external_parent(
                str(symlink_parent / "candidate-manifest.json")
            )
        with self.assertRaises(CandidateOfflineValidationError):
            _validate_output_directory(
                str(symlink_parent / "candidate-output")
            )

        git_parent = self.root_path / "git-parent"
        git_parent.mkdir(mode=0o700)
        (git_parent / ".git").mkdir(mode=0o700)
        with self.assertRaises(CandidateOfflineValidationError):
            _open_external_parent(
                str(git_parent / "candidate-manifest.json")
            )
        with self.assertRaises(CandidateOfflineValidationError):
            _validate_output_directory(
                str(git_parent / "candidate-output")
            )

    def test_valid_offline_cli_artifacts_reach_only_startup_absent(
        self,
    ) -> None:
        from inci_tennis_adapters.candidate_contracts import (
            REQUIRED_CANDIDATE_CAPABILITIES,
            candidate_quotas_projection,
        )
        from inci_tennis_expert.contracts import (
            compute_expert_provider_source_lineage_sha256,
        )
        from inci_tennis_io.provider_readonly import (
            candidate_quota_demand,
            sportradar_candidate_offline_is_eligible,
            validate_sportradar_candidate_offline_artifacts,
        )
        from tennis_v1.canonical import canonical_json_bytes
        from tests.tennis_v1.test_match_binding import (
            _valid_payloads_from_document,
            manifest_document,
        )
        from tools.qualify_sportradar_tennis_v3 import main as cli_main

        input_parent = self.root_path / "cli-inputs"
        input_parent.mkdir(mode=0o700)
        output_dir = self.root_path / "cli-output"
        output_dir.mkdir(mode=0o700)
        manifest_path = input_parent / "candidate-manifest.json"
        authorization_path = input_parent / "candidate-authorization.json"
        binding_manifest_path = input_parent / "binding-manifest.json"
        binding_review_path = input_parent / "binding-review.json"

        duration = 100
        start = 1_894_730_400_000_000_000
        end = start + duration * 1_000_000_000
        retention = end + 600_000_000_000
        access = end + 1_200_000_000_000
        analysis = end + 3_600_000_000_000
        required_strata = [
            {
                "sport": "tennis",
                "tour": "tour-atp",
                "competition_tier": "tier-250",
                "match_format": (
                    "standard_advantage_bo3_tb7_all_sets"
                ),
                "round_code": "round-000",
                "matches": 1,
            }
        ]
        demand = candidate_quota_demand(
            requested_matches=1,
            session_start_wall_ns=start,
            session_end_wall_ns=end,
        )
        declared_quotas = dict(candidate_quotas_projection(demand))
        manifest: dict[str, object] = {
            "schema_version": 1,
            "artifact_id": "candidate-manifest-1",
            "artifact_created_wall_ns": start - 10,
            "provider_id": "sportradar",
            "product_tier": "synthetic-tier",
            "source_lineage_id": "synthetic-lineage",
            "terms_version": "synthetic-terms",
            "permission_artifact_sha256": "b" * 64,
            "authorization_artifact_sha256": "0" * 64,
            "credential_env_names": ["SPORTRADAR_API_KEY"],
            "declared_quotas": declared_quotas,
            "session_start_wall_ns": start,
            "session_end_wall_ns": end,
            "required_retention_until_ns": retention,
            "access_expires_at_ns": access,
            "analysis_expires_at_ns": analysis,
            "requested_provider_match_ids": ["provider-match-000"],
            "required_candidate_capabilities": list(
                REQUIRED_CANDIDATE_CAPABILITIES
            ),
            "required_strata": required_strata,
            "binding_manifest_path": str(binding_manifest_path),
            "binding_manifest_artifact_id": "binding-manifest-1",
            "binding_manifest_sha256": "0" * 64,
            "binding_review_path": str(binding_review_path),
            "binding_review_artifact_id": "binding-review-custom",
            "binding_review_sha256": "0" * 64,
            "manifest_core_sha256": "0" * 64,
        }
        core_exclusions = {
            "authorization_artifact_sha256",
            "binding_manifest_path",
            "binding_manifest_artifact_id",
            "binding_manifest_sha256",
            "binding_review_path",
            "binding_review_artifact_id",
            "binding_review_sha256",
            "manifest_core_sha256",
        }
        manifest_core_sha256 = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-MANIFEST-CORE-V1\0"
            + canonical_json_bytes(
                {
                    key: value
                    for key, value in manifest.items()
                    if key not in core_exclusions
                }
            )
        ).hexdigest()
        manifest["manifest_core_sha256"] = manifest_core_sha256
        source_lineage_sha256 = (
            compute_expert_provider_source_lineage_sha256(
                "sportradar",
                "synthetic-tier",
                "synthetic-lineage",
                manifest_core_sha256,
            )
        )

        binding_document = manifest_document()
        raw_binding = binding_document["bindings"][0]
        provider = raw_binding["provider"]
        provider["source_id"] = "sportradar"
        provider["source_lineage_sha256"] = source_lineage_sha256
        (
            binding_manifest_payload,
            binding_review_payload,
            binding_manifest_pin,
            binding_review_pin,
            _,
        ) = _valid_payloads_from_document(binding_document)
        binding_manifest_path.write_bytes(binding_manifest_payload)
        binding_review_path.write_bytes(binding_review_payload)
        manifest["binding_manifest_artifact_id"] = (
            binding_manifest_pin.artifact_id
        )
        manifest["binding_manifest_sha256"] = (
            binding_manifest_pin.artifact_sha256
        )
        manifest["binding_review_artifact_id"] = (
            binding_review_pin.artifact_id
        )
        manifest["binding_review_sha256"] = (
            binding_review_pin.artifact_sha256
        )

        strata_projection = tuple(
            tuple(
                (key, item[key])
                for key in (
                    "sport",
                    "tour",
                    "competition_tier",
                    "match_format",
                    "round_code",
                    "matches",
                )
            )
            for item in required_strata
        )
        allowed_strata_sha256 = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-REQUIRED-STRATA-V1\0"
            + canonical_expert_bytes(strata_projection)
        ).hexdigest()
        authorization = {
            "schema_version": 1,
            "artifact_id": "candidate-authorization-1",
            "artifact_created_wall_ns": start - 8,
            "candidate_manifest_core_sha256": manifest_core_sha256,
            "decision": (
                "approved_for_candidate_read_only_observation"
            ),
            "reviewer_id": "synthetic-reviewer",
            "reviewed_wall_ns": start - 9,
            "allowed_provider_id": "sportradar",
            "allowed_product_tier": "synthetic-tier",
            "allowed_duration_seconds": duration,
            "allowed_match_ids": ["provider-match-000"],
            "required_candidate_capabilities": list(
                REQUIRED_CANDIDATE_CAPABILITIES
            ),
            "allowed_strata_sha256": allowed_strata_sha256,
            "publication_allowed": False,
            "authorization_evidence_sha256": "e" * 64,
        }
        authorization_payload = canonical_json_bytes(authorization)
        authorization_path.write_bytes(authorization_payload)
        manifest["authorization_artifact_sha256"] = sha256(
            authorization_payload
        ).hexdigest()
        manifest_payload = canonical_json_bytes(manifest)
        manifest_path.write_bytes(manifest_payload)

        artifacts = validate_sportradar_candidate_offline_artifacts(
            manifest_path=str(manifest_path),
            binding_path=str(authorization_path),
            duration_seconds=duration,
            output_dir=str(output_dir),
        )
        self.assertTrue(
            sportradar_candidate_offline_is_eligible(artifacts)
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = cli_main(
                (
                    "--manifest",
                    str(manifest_path),
                    "--binding",
                    str(authorization_path),
                    "--duration-seconds",
                    str(duration),
                    "--output-dir",
                    str(output_dir),
                )
            )
        self.assertEqual(exit_code, 3)
        self.assertEqual(
            stderr.getvalue(),
            (
                "candidate observation unavailable: "
                "startup authority absent\n"
            ),
        )
        self.assertEqual(tuple(output_dir.iterdir()), ())

    def test_summary_fixture_normalizes_exactly_and_repeatably(self) -> None:
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            SportradarTennisV3Adapter,
            _output_contract_sha256,
            bind_sportradar_tennis_v3_event,
        )
        from inci_tennis_expert.contracts import (
            MatchStatus,
            ProviderSnapshot,
            ScoreValue,
            TerminationKind,
            expert_contract_sha256,
        )

        payload = (
            FIXTURES / "sportradar_tennis_summary_v3.json"
        ).read_bytes()
        provider_binding, universe, captured, durable_raw = (
            self._pure_parser_inputs(
                payload=payload,
                event_type="sportradar_tennis_summary_v3",
                source_wall_ns=1_894_726_800_000_000_000,
                source_generated_ns=1_894_726_800_250_000_000,
                provider_sequence="c0.r0",
            )
        )
        adapter = bind_sportradar_tennis_v3_event(
            provider_binding=provider_binding,
            universe=universe,
            captured=captured,
            durable_raw=durable_raw,
        )
        self.assertIs(type(adapter), SportradarTennisV3Adapter)
        self.assertEqual(
            repr(adapter),
            "<SportradarTennisV3Adapter pure_test_only redacted>",
        )
        with self.assertRaises(TypeError):
            type("HostileAdapter", (SportradarTennisV3Adapter,), {})
        snapshot = adapter.normalize_summary(
            payload,
            received_monotonic_ns=captured.local_monotonic_ns,
        )
        self.assertIs(type(snapshot), ProviderSnapshot)
        self.assertEqual(
            (
                snapshot.provider_source_id,
                snapshot.revision_domain_id,
                snapshot.provider_match_id,
                snapshot.home_player_id,
                snapshot.away_player_id,
                snapshot.scheduled_start_wall_ns,
                snapshot.status,
                snapshot.termination_kind,
                snapshot.completed_sets,
                snapshot.games_home,
                snapshot.games_away,
                snapshot.points_home,
                snapshot.points_away,
                snapshot.server_for_next_point,
                snapshot.correction_epoch,
                snapshot.revision,
                snapshot.source_wall_ns,
                snapshot.source_generated_wall_ns,
                snapshot.received_monotonic_ns,
                snapshot.clock_uncertainty_ns,
                snapshot.snapshot_complete,
            ),
            (
                "sportradar",
                "synthetic-revision-domain",
                "synthetic-match-1",
                "synthetic-player-home",
                "synthetic-player-away",
                1_894_730_400_000_000_000,
                MatchStatus.SCHEDULED,
                TerminationKind.NONE,
                (),
                0,
                0,
                ScoreValue.LOVE,
                ScoreValue.LOVE,
                None,
                0,
                0,
                1_894_726_800_000_000_000,
                1_894_726_800_250_000_000,
                captured.local_monotonic_ns,
                captured.clock_uncertainty_ns,
                True,
            ),
        )
        self.assertEqual(
            expert_contract_sha256(snapshot),
            "da3deb5871d7ab5b7f54b2d92c6ce185"
            "226d76d74ca9bbeb1413b57977c64868",
        )
        self.assertEqual(
            _output_contract_sha256(snapshot),
            expert_contract_sha256(snapshot),
        )
        expected_bytes = canonical_expert_bytes(snapshot)
        for _ in range(1_000):
            repeated = adapter.normalize_summary(
                payload,
                received_monotonic_ns=captured.local_monotonic_ns,
            )
            self.assertEqual(
                canonical_expert_bytes(repeated),
                expected_bytes,
            )

    def test_timeline_retains_snapshot_point_correction_order(self) -> None:
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            bind_sportradar_tennis_v3_event,
        )
        from inci_tennis_expert.contracts import (
            PlayerSide,
            ProviderPoint,
            ProviderSnapshot,
        )
        from tennis_v1.canonical import canonical_json_bytes

        fixture = json.loads(
            (
                FIXTURES / "sportradar_tennis_timeline_v3.json"
            ).read_bytes()
        )
        payload = canonical_json_bytes(fixture["cases"][0]["payload"])
        provider_binding, universe, captured, durable_raw = (
            self._pure_parser_inputs(
                payload=payload,
                event_type="sportradar_tennis_timeline_v3",
                source_wall_ns=1_894_730_402_000_000_000,
                source_generated_ns=1_894_730_402_100_000_000,
                provider_sequence="c1.r1",
            )
        )
        adapter = bind_sportradar_tennis_v3_event(
            provider_binding=provider_binding,
            universe=universe,
            captured=captured,
            durable_raw=durable_raw,
        )
        events = adapter.normalize_timeline(
            payload,
            prior=None,
            received_monotonic_ns=captured.local_monotonic_ns,
        )
        self.assertEqual(
            tuple(type(event) for event in events),
            (ProviderSnapshot, ProviderPoint, ProviderSnapshot),
        )
        self.assertEqual(
            tuple(
                (event.correction_epoch, event.revision)
                for event in events
            ),
            ((0, 1), (0, 2), (1, 1)),
        )
        self.assertEqual(events[1].point_winner, PlayerSide.HOME)
        self.assertEqual(events[1].server_before_point, PlayerSide.HOME)

    def test_summary_unknown_key_is_schema_unknown_not_dynamic(self) -> None:
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            SportradarTennisV3CandidateError,
            bind_sportradar_tennis_v3_event,
        )
        from tennis_v1.canonical import canonical_json_bytes

        document = json.loads(
            (
                FIXTURES / "sportradar_tennis_summary_v3.json"
            ).read_bytes()
        )
        document["unexpected"] = "never accepted"
        payload = canonical_json_bytes(document)
        provider_binding, universe, captured, durable_raw = (
            self._pure_parser_inputs(
                payload=payload,
                event_type="sportradar_tennis_summary_v3",
                source_wall_ns=1_894_726_800_000_000_000,
                source_generated_ns=1_894_726_800_250_000_000,
                provider_sequence="c0.r0",
            )
        )
        adapter = bind_sportradar_tennis_v3_event(
            provider_binding=provider_binding,
            universe=universe,
            captured=captured,
            durable_raw=durable_raw,
        )
        with self.assertRaisesRegex(
            SportradarTennisV3CandidateError,
            r"\Acandidate_schema_unknown\Z",
        ):
            adapter.normalize_summary(
                payload,
                received_monotonic_ns=captured.local_monotonic_ns,
            )

    def test_summary_coordinate_must_equal_captured_provider_sequence(
        self,
    ) -> None:
        from inci_tennis_adapters.sportradar_tennis_v3 import (
            SportradarTennisV3CandidateError,
            bind_sportradar_tennis_v3_event,
        )

        payload = (
            FIXTURES / "sportradar_tennis_summary_v3.json"
        ).read_bytes()
        provider_binding, universe, captured, durable_raw = (
            self._pure_parser_inputs(
                payload=payload,
                event_type="sportradar_tennis_summary_v3",
                source_wall_ns=1_894_726_800_000_000_000,
                source_generated_ns=1_894_726_800_250_000_000,
                provider_sequence="c0.r1",
            )
        )
        adapter = bind_sportradar_tennis_v3_event(
            provider_binding=provider_binding,
            universe=universe,
            captured=captured,
            durable_raw=durable_raw,
        )
        with self.assertRaisesRegex(
            SportradarTennisV3CandidateError,
            r"\Acandidate_captured_parent_mismatch\Z",
        ):
            adapter.normalize_summary(
                payload,
                received_monotonic_ns=captured.local_monotonic_ns,
            )

    def test_failure_trace_is_fsynced_chained_and_atomically_finalized(
        self,
    ) -> None:
        manifest, values = self._artifacts()
        writer = facade.create_sportradar_candidate_output_writer(
            self.root_authority,
            output_parent=str(self.output_parent),
            session_manifest=manifest,
            session_manifest_sha256=session_manifest_sha256(manifest),
            **values,
        )
        failure = facade.append_sportradar_candidate_failure(
            writer,
            prior_receipt=None,
            stage="permit",
            failure_code="contract_failed",
            permit_receipt=None,
            capture_receipt=None,
        )
        self.assertTrue(failure.fsynced)
        commit = facade.finalize_sportradar_candidate_output(
            writer,
            prior_receipt=failure,
            terminal_reason="internal_contract_failure",
        )
        final = self.output_parent / commit.final_basename
        summary_path = final / "qualification-output-v1.json"
        trace_path = final / "qualification-captures-v1.jsonl"
        self.assertEqual(stat.S_IMODE(final.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(summary_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(trace_path.stat().st_mode), 0o600)
        trace_bytes = trace_path.read_bytes()
        self.assertEqual(sha256(trace_bytes).hexdigest(), commit.trace_sha256)
        rows = tuple(
            json.loads(line)
            for line in trace_bytes.decode("ascii").splitlines()
        )
        self.assertEqual(
            tuple(row["record_type"] for row in rows),
            ("failure", "terminal"),
        )
        self.assertEqual(rows[0]["record_index"], 1)
        self.assertEqual(rows[1]["record_index"], 2)
        self.assertEqual(
            rows[1]["previous_record_sha256"],
            sha256(
                (
                    json.dumps(
                        rows[0],
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    )
                    + "\n"
                ).encode("ascii")
            ).hexdigest(),
        )
        summary = json.loads(summary_path.read_bytes())
        self.assertEqual(summary["trace_record_count"], 2)
        self.assertEqual(summary["failure_count"], 1)
        self.assertEqual(
            summary["production_preflight_status"],
            "not_run_candidate_only",
        )

    def test_candidate_output_protocol_requires_permit_capture_parser_order(
        self,
    ) -> None:
        from inci_tennis_io.ports import (
            CandidateQualificationCommitReceiptV1,
        )
        from tests.tennis_v1.sportradar_candidate_fixture_support import (
            capture_public_candidate_fixture,
        )

        manifest, values = self._artifacts()
        writer = facade.create_sportradar_candidate_output_writer(
            self.root_authority,
            output_parent=str(self.output_parent),
            session_manifest=manifest,
            session_manifest_sha256=session_manifest_sha256(manifest),
            **values,
        )

        captured = capture_public_candidate_fixture(
            b"{}",
            manifest=manifest,
            source_entity_id="synthetic-match",
            session_start_wall_ns=100,
            local_wall_ns=101,
            local_monotonic_ns=700,
            clock_uncertainty_ns=2,
            event_type="sportradar_tennis_summary_v3",
            source_wall_ns=100,
            source_generated_ns=100,
            provider_sequence="c0.r1",
        )

        with self.assertRaises(ValueError):
            facade.append_sportradar_candidate_capture(
                writer,
                prior_receipt=None,  # type: ignore[arg-type]
                captured=captured,
            )

        quota_coordinates = (
            ("rolling_second_attempts", 0),
            ("rolling_60_seconds_attempts", 0),
            ("utc_day_attempts", 0),
            ("active_connections", 1),
            ("active_subscriptions", 1),
            ("rolling_hour_resync_attempts", 0),
        )
        permit_sha256 = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-READ-PERMIT-V1\0"
            + canonical_expert_bytes(
                (
                    ("session_id", manifest.session_id),
                    ("route", "summary"),
                    ("provider_match_id", "synthetic-match"),
                    ("resync", False),
                    ("connection_epoch", 1),
                    ("quota_coordinates", quota_coordinates),
                    (
                        "previous_record_sha256",
                        manifest.qualification_trace_sha256,
                    ),
                )
            )
        ).hexdigest()
        permit = facade.append_sportradar_candidate_permit(
            writer,
            prior_receipt=None,
            route="summary",
            provider_match_id="synthetic-match",
            resync=False,
            connection_epoch=1,
            permit_sha256=permit_sha256,
            quota_coordinates=quota_coordinates,
        )
        capabilities = (
            ("correction_semantics", True),
            ("current_server", True),
            ("match_format", True),
            ("monotonic_sequence_or_revision", True),
            ("point_state", True),
            ("provider_generated_time", True),
            ("resync_snapshot", True),
            ("source_event_time", True),
            ("stable_match_ids", True),
            ("stable_player_ids", True),
        )
        with self.assertRaises(ValueError):
            facade.append_sportradar_candidate_parser_result(
                writer,
                prior_receipt=permit,
                capture_receipt=permit,
                evidence_sha256="a" * 64,
                parser_outcome="accepted",
                reason=None,
                output_contract_sha256s=("b" * 64,),
                capabilities=capabilities,
                first_correction_epoch=0,
                first_revision=1,
                last_correction_epoch=0,
                last_revision=1,
            )

        capture_receipt = facade.append_sportradar_candidate_capture(
            writer,
            prior_receipt=permit,
            captured=captured,
        )
        with self.assertRaises(ValueError):
            facade.append_sportradar_candidate_parser_result(
                writer,
                prior_receipt=permit,
                capture_receipt=capture_receipt,
                evidence_sha256="a" * 64,
                parser_outcome="accepted",
                reason=None,
                output_contract_sha256s=("b" * 64,),
                capabilities=capabilities,
                first_correction_epoch=0,
                first_revision=1,
                last_correction_epoch=0,
                last_revision=1,
            )
        parser_receipt = facade.append_sportradar_candidate_parser_result(
            writer,
            prior_receipt=capture_receipt,
            capture_receipt=capture_receipt,
            evidence_sha256=_candidate_parser_evidence_sha256(
                captured,
                parser_outcome="accepted",
                reason=None,
                output_contract_sha256s=("b" * 64,),
                capabilities=capabilities,
                first_correction_epoch=0,
                first_revision=1,
                last_correction_epoch=0,
                last_revision=1,
            ),
            parser_outcome="accepted",
            reason=None,
            output_contract_sha256s=("b" * 64,),
            capabilities=capabilities,
            first_correction_epoch=0,
            first_revision=1,
            last_correction_epoch=0,
            last_revision=1,
        )
        commit = facade.finalize_sportradar_candidate_output(
            writer,
            prior_receipt=parser_receipt,
            terminal_reason="completed",
        )
        self.assertIs(type(commit), CandidateQualificationCommitReceiptV1)
        self.assertEqual(commit.trace_record_count, 4)
        self.assertEqual(commit.terminal_reason, "completed")
        self.assertTrue(commit.files_fsynced)
        self.assertTrue(commit.staging_directory_fsynced)
        self.assertTrue(commit.parent_fsynced)
        with self.assertRaises(ValueError):
            facade.finalize_sportradar_candidate_output(
                writer,
                prior_receipt=parser_receipt,
                terminal_reason="completed",
            )


if __name__ == "__main__":
    unittest.main()
