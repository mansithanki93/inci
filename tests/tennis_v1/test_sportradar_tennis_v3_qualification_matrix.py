from __future__ import annotations

from dataclasses import fields
from hashlib import sha256
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from inci_tennis_expert.contracts import (
    ArtifactPin,
    BindingReviewDecision,
    BindingUniverse,
    canonical_binding_review_artifact_bytes,
    canonical_expert_bytes,
    compute_binding_review_evidence_sha256,
    compute_binding_universe_sha256,
    compute_expert_provider_source_lineage_sha256,
)
from inci_tennis_io import facade
from inci_tennis_io.ports import (
    CandidateSourceSealCollectionAuthorityV1,
    CandidateSourceSealsV1,
)
from tennis_v1.canonical import canonical_json_bytes
from tennis_v1.capture import CaptureAuthority
from tennis_v1.entitlements import QualificationReason
from tennis_v1.events import SessionManifest, SourceKind
from tennis_v1.retention import RetentionCoordinator
from tests.tennis_v1.test_match_binding import (
    manifest_document,
    projections_for,
)
from tests.tennis_v1.test_retention import MutableClock, make_config


ROOT = Path(__file__).resolve().parents[2]
SESSION_NAMESPACE = uuid.UUID(
    "8f4c1777-5fea-521a-aaab-60afdc79e328"
)
DAY_NS = 86_400_000_000_000

QUOTA_FIELDS = (
    "requests_per_rolling_60_seconds",
    "requests_per_utc_calendar_day",
    "requests_per_rolling_second",
    "max_connections",
    "max_subscriptions",
    "resync_requests_per_rolling_hour",
)
ADAPTER_SOURCES = (
    "inci_tennis_adapters/candidate_contracts.py",
    "inci_tennis_adapters/registry.py",
    "inci_tennis_adapters/sportradar_tennis_v3.py",
)
SCHEMAS = (
    (
        "inci_tennis_adapters/schemas/"
        "sportradar-tennis-summary-v3-candidate-v1.schema.json"
    ),
    (
        "inci_tennis_adapters/schemas/"
        "sportradar-tennis-timeline-v3-candidate-v1.schema.json"
    ),
    (
        "inci_tennis_adapters/schemas/"
        "sportradar-tennis-transport-error-v1.schema.json"
    ),
    (
        "inci_tennis_adapters/schemas/"
        "sportradar-tennis-candidate-manifest-v1.schema.json"
    ),
    (
        "inci_tennis_adapters/schemas/"
        "sportradar-tennis-candidate-authorization-v1.schema.json"
    ),
    (
        "inci_tennis_adapters/schemas/"
        "sportradar-tennis-qualification-output-v1.schema.json"
    ),
)
IO_SOURCES = (
    "inci_tennis_io/ports.py",
    "inci_tennis_io/expert_journal_store.py",
    "inci_tennis_io/facade.py",
    "inci_tennis_io/provider_readonly.py",
)
CONTROLLER_SOURCE = (
    "inci_tennis_runtime/provider_qualification_controller.py"
)
TOOL_SOURCE = "tools/qualify_sportradar_tennis_v3.py"
ALL_SEALED_PATHS = tuple(
    dict.fromkeys(
        (
            *ADAPTER_SOURCES,
            *SCHEMAS,
            *IO_SOURCES,
            CONTROLLER_SOURCE,
            TOOL_SOURCE,
        )
    )
)


def _digest(domain: bytes, projection: object) -> str:
    return sha256(
        domain + canonical_expert_bytes(projection)
    ).hexdigest()


def _raw_sha256(
    logical: str,
    replacements: dict[str, bytes],
) -> str:
    content = replacements.get(logical)
    if content is None:
        content = (ROOT / logical).read_bytes()
    return sha256(content).hexdigest()


def _inventory_sha256(
    *,
    domain: bytes,
    inventory: tuple[str, ...],
    replacements: dict[str, bytes],
) -> str:
    return _digest(
        domain,
        tuple(
            (logical, _raw_sha256(logical, replacements))
            for logical in inventory
        ),
    )


def _expected_source_seals(
    replacements: dict[str, bytes] | None = None,
) -> tuple[
    tuple[tuple[object, ...], ...],
    dict[str, str],
]:
    replaced = {} if replacements is None else replacements
    registry = "inci_tennis_adapters/registry.py"
    parser = "inci_tennis_adapters/sportradar_tennis_v3.py"
    normalizer_code_sha256 = _digest(
        b"INCI-EXPERT-NORMALIZER-CODE-V1\0",
        (
            (registry, _raw_sha256(registry, replaced)),
            (parser, _raw_sha256(parser, replaced)),
        ),
    )
    route_specs = (
        (
            "sportradar-tennis-summary-v3",
            "sportradar_tennis_summary_v3",
            SCHEMAS[0],
        ),
        (
            "sportradar-tennis-timeline-v3",
            "sportradar_tennis_timeline_v3",
            SCHEMAS[1],
        ),
        (
            "sportradar-tennis-transport-error-v1",
            "sportradar_tennis_transport_error_v1",
            SCHEMAS[2],
        ),
    )
    pins = tuple(
        (
            normalizer_id,
            "provider",
            "sportradar",
            event_type,
            1,
            normalizer_code_sha256,
            _raw_sha256(schema, replaced),
        )
        for normalizer_id, event_type, schema in route_specs
    )
    values = {
        "candidate_adapter_inventory_sha256": _inventory_sha256(
            domain=(
                b"INCI-SPORTRADAR-CANDIDATE-ADAPTER-INVENTORY-V1\0"
            ),
            inventory=(*ADAPTER_SOURCES, *SCHEMAS),
            replacements=replaced,
        ),
        "candidate_io_bridge_inventory_sha256": _inventory_sha256(
            domain=(
                b"INCI-SPORTRADAR-CANDIDATE-IO-BRIDGE-INVENTORY-V1\0"
            ),
            inventory=IO_SOURCES,
            replacements=replaced,
        ),
        "provider_transport_source_sha256": _raw_sha256(
            "inci_tennis_io/provider_readonly.py",
            replaced,
        ),
        "qualification_controller_source_sha256": _raw_sha256(
            CONTROLLER_SOURCE,
            replaced,
        ),
        "qualification_tool_source_sha256": _raw_sha256(
            TOOL_SOURCE,
            replaced,
        ),
        "candidate_manifest_schema_sha256": _raw_sha256(
            SCHEMAS[3],
            replaced,
        ),
        "candidate_authorization_schema_sha256": _raw_sha256(
            SCHEMAS[4],
            replaced,
        ),
        "candidate_output_schema_sha256": _raw_sha256(
            SCHEMAS[5],
            replaced,
        ),
    }
    values["qualification_protocol_sha256"] = _digest(
        (
            b"INCI-SPORTRADAR-CANDIDATE-"
            b"QUALIFICATION-PROTOCOL-V1\0"
        ),
        (
            (
                "candidate_adapter_inventory_sha256",
                values["candidate_adapter_inventory_sha256"],
            ),
            (
                "candidate_io_bridge_inventory_sha256",
                values["candidate_io_bridge_inventory_sha256"],
            ),
            (
                "provider_transport_source_sha256",
                values["provider_transport_source_sha256"],
            ),
            (
                "qualification_controller_source_sha256",
                values["qualification_controller_source_sha256"],
            ),
            (
                "qualification_tool_source_sha256",
                values["qualification_tool_source_sha256"],
            ),
            (
                "candidate_manifest_schema_sha256",
                values["candidate_manifest_schema_sha256"],
            ),
            (
                "candidate_authorization_schema_sha256",
                values["candidate_authorization_schema_sha256"],
            ),
            (
                "candidate_output_schema_sha256",
                values["candidate_output_schema_sha256"],
            ),
            ("duration_max_seconds", 3_600),
            ("polling_interval_seconds", 10),
            ("transport_origin", "https://api.sportradar.com"),
            (
                "output_protocol",
                "candidate-qualification-output-v1",
            ),
        ),
    )
    pin_projection = tuple(
        (
            ("normalizer_id", pin[0]),
            ("source_kind", pin[1]),
            ("source_id", pin[2]),
            ("event_type", pin[3]),
            ("event_version", pin[4]),
            ("normalizer_code_sha256", pin[5]),
            ("normalizer_schema_sha256", pin[6]),
        )
        for pin in pins
    )
    outer = (
        ("schema_version", 1),
        ("normalizer_pins", pin_projection),
        (
            "candidate_adapter_inventory_sha256",
            values["candidate_adapter_inventory_sha256"],
        ),
        (
            "candidate_io_bridge_inventory_sha256",
            values["candidate_io_bridge_inventory_sha256"],
        ),
        (
            "provider_transport_source_sha256",
            values["provider_transport_source_sha256"],
        ),
        (
            "qualification_controller_source_sha256",
            values["qualification_controller_source_sha256"],
        ),
        (
            "qualification_tool_source_sha256",
            values["qualification_tool_source_sha256"],
        ),
        (
            "candidate_manifest_schema_sha256",
            values["candidate_manifest_schema_sha256"],
        ),
        (
            "candidate_authorization_schema_sha256",
            values["candidate_authorization_schema_sha256"],
        ),
        (
            "candidate_output_schema_sha256",
            values["candidate_output_schema_sha256"],
        ),
        (
            "qualification_protocol_sha256",
            values["qualification_protocol_sha256"],
        ),
    )
    values["candidate_source_seals_sha256"] = _digest(
        b"INCI-SPORTRADAR-CANDIDATE-SOURCE-SEALS-V1\0",
        outer,
    )
    return pins, values


def _build_binding_artifacts(
    *,
    count: int,
    source_lineage_sha256: str,
) -> tuple[bytes, bytes, ArtifactPin, ArtifactPin, BindingUniverse]:
    document = manifest_document(count)
    for raw_binding in document["bindings"]:  # type: ignore[index]
        provider = raw_binding["provider"]
        provider["source_id"] = "sportradar"
        provider["source_lineage_sha256"] = source_lineage_sha256
    manifest_payload = canonical_json_bytes(document)
    manifest_pin = ArtifactPin(
        document["artifact_id"],  # type: ignore[arg-type]
        sha256(manifest_payload).hexdigest(),
    )
    bindings, metadata = projections_for(document, manifest_pin)
    review_evidence_sha256 = (
        compute_binding_review_evidence_sha256(
            manifest_pin,
            bindings,
            metadata,
        )
    )
    review_values = {
        "review_artifact_id": "binding-review-candidate",
        "review_artifact_created_wall_ns": 1_200_000_000,
        "binding_artifact_id": manifest_pin.artifact_id,
        "binding_artifact_sha256": manifest_pin.artifact_sha256,
        "decision": "approved",
        "reviewer_id": "synthetic-reviewer",
        "reviewed_wall_ns": 1_100_000_000,
        "review_evidence_sha256": review_evidence_sha256,
    }
    review_payload = canonical_binding_review_artifact_bytes(
        **review_values  # type: ignore[arg-type]
    )
    review_pin = ArtifactPin(
        review_values["review_artifact_id"],  # type: ignore[arg-type]
        sha256(review_payload).hexdigest(),
    )
    review = BindingReviewDecision(
        review_artifact_sha256=review_pin.artifact_sha256,
        **review_values,  # type: ignore[arg-type]
    )
    universe = BindingUniverse(
        manifest_pin.artifact_id,
        manifest_pin.artifact_sha256,
        review,
        bindings,
        metadata,
        compute_binding_universe_sha256(
            manifest_pin,
            review,
            bindings,
            metadata,
        ),
    )
    return (
        manifest_payload,
        review_payload,
        manifest_pin,
        review_pin,
        universe,
    )


def _validated_artifacts(
    *,
    parent: Path,
    source_seals: CandidateSourceSealsV1,
    duration_seconds: int = 100,
    requested_matches: int = 1,
    declared_changes: dict[str, int] | None = None,
):
    from inci_tennis_adapters.candidate_contracts import (
        REQUIRED_CANDIDATE_CAPABILITIES,
        candidate_quotas_projection,
        candidate_usage_projection,
    )
    from inci_tennis_io.provider_readonly import (
        SPORTRADAR_CANDIDATE_USAGE,
        candidate_quota_demand,
        validate_sportradar_candidate_offline_artifacts,
    )

    case_root = Path(
        tempfile.mkdtemp(prefix="qualification-", dir=parent)
    )
    input_dir = case_root / "inputs"
    output_dir = case_root / "output"
    input_dir.mkdir(mode=0o700)
    output_dir.mkdir(mode=0o700)
    manifest_path = input_dir / "candidate-manifest.json"
    authorization_path = input_dir / "candidate-authorization.json"
    binding_manifest_path = input_dir / "binding-manifest.json"
    binding_review_path = input_dir / "binding-review.json"

    start = 1_894_730_400_000_000_000
    end = start + duration_seconds * 1_000_000_000
    retention = end + 600_000_000_000
    access = end + 1_200_000_000_000
    analysis = end + 3_600_000_000_000
    requested_ids = [
        f"provider-match-{index:03d}"
        for index in range(requested_matches)
    ]
    required_strata = [
        {
            "sport": "tennis",
            "tour": "tour-atp",
            "competition_tier": "tier-250",
            "match_format": (
                "standard_advantage_bo3_tb7_all_sets"
            ),
            "round_code": f"round-{index:03d}",
            "matches": 1,
        }
        for index in range(requested_matches)
    ]
    demand = candidate_quota_demand(
        requested_matches=requested_matches,
        session_start_wall_ns=start,
        session_end_wall_ns=end,
    )
    declared = dict(candidate_quotas_projection(demand))
    if declared_changes is not None:
        declared.update(declared_changes)
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
        "declared_quotas": declared,
        "session_start_wall_ns": start,
        "session_end_wall_ns": end,
        "required_retention_until_ns": retention,
        "access_expires_at_ns": access,
        "analysis_expires_at_ns": analysis,
        "requested_provider_match_ids": requested_ids,
        "required_candidate_capabilities": list(
            REQUIRED_CANDIDATE_CAPABILITIES
        ),
        "required_strata": required_strata,
        "binding_manifest_path": str(binding_manifest_path),
        "binding_manifest_artifact_id": "pending-binding",
        "binding_manifest_sha256": "0" * 64,
        "binding_review_path": str(binding_review_path),
        "binding_review_artifact_id": "pending-review",
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
    (
        binding_payload,
        review_payload,
        binding_pin,
        review_pin,
        _,
    ) = _build_binding_artifacts(
        count=requested_matches,
        source_lineage_sha256=source_lineage_sha256,
    )
    binding_manifest_path.write_bytes(binding_payload)
    binding_review_path.write_bytes(review_payload)
    manifest["binding_manifest_artifact_id"] = binding_pin.artifact_id
    manifest["binding_manifest_sha256"] = binding_pin.artifact_sha256
    manifest["binding_review_artifact_id"] = review_pin.artifact_id
    manifest["binding_review_sha256"] = review_pin.artifact_sha256

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
    allowed_strata_sha256 = _digest(
        b"INCI-SPORTRADAR-CANDIDATE-REQUIRED-STRATA-V1\0",
        strata_projection,
    )
    authorization_evidence_sha256 = _digest(
        (
            b"INCI-SPORTRADAR-CANDIDATE-"
            b"AUTHORIZATION-EVIDENCE-V1\0"
        ),
        (
            (
                "candidate_source_seals_sha256",
                source_seals.candidate_source_seals_sha256,
            ),
            ("permission_artifact_sha256", "b" * 64),
            ("manifest_core_sha256", manifest_core_sha256),
            (
                "binding_manifest_sha256",
                binding_pin.artifact_sha256,
            ),
            (
                "binding_review_sha256",
                review_pin.artifact_sha256,
            ),
            (
                "requested_provider_match_ids",
                tuple(requested_ids),
            ),
            (
                "required_candidate_capabilities",
                REQUIRED_CANDIDATE_CAPABILITIES,
            ),
            ("required_strata", strata_projection),
            ("duration_seconds", duration_seconds),
            (
                "usage",
                candidate_usage_projection(
                    SPORTRADAR_CANDIDATE_USAGE
                ),
            ),
            (
                "declared_quotas",
                tuple(
                    (name, declared[name])
                    for name in QUOTA_FIELDS
                ),
            ),
        ),
    )
    authorization = {
        "schema_version": 1,
        "artifact_id": "candidate-authorization-1",
        "artifact_created_wall_ns": start - 8,
        "candidate_manifest_core_sha256": manifest_core_sha256,
        "decision": "approved_for_candidate_read_only_observation",
        "reviewer_id": "synthetic-reviewer",
        "reviewed_wall_ns": start - 9,
        "allowed_provider_id": "sportradar",
        "allowed_product_tier": "synthetic-tier",
        "allowed_duration_seconds": duration_seconds,
        "allowed_match_ids": requested_ids,
        "required_candidate_capabilities": list(
            REQUIRED_CANDIDATE_CAPABILITIES
        ),
        "allowed_strata_sha256": allowed_strata_sha256,
        "publication_allowed": False,
        "authorization_evidence_sha256": (
            authorization_evidence_sha256
        ),
    }
    authorization_payload = canonical_json_bytes(authorization)
    authorization_path.write_bytes(authorization_payload)
    manifest["authorization_artifact_sha256"] = sha256(
        authorization_payload
    ).hexdigest()
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    return validate_sportradar_candidate_offline_artifacts(
        manifest_path=str(manifest_path),
        binding_path=str(authorization_path),
        duration_seconds=duration_seconds,
        output_dir=str(output_dir),
    )


class SportradarQualificationAcceptanceMatrixTests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root_path = Path(cls.temporary.name).resolve()
        cls.clock = MutableClock(101)
        cls.coordinator = RetentionCoordinator.acquire(
            make_config(cls.root_path / "state"),
            clock_ns=cls.clock,
        )
        cls.coordinator.recover_and_purge()
        request = (
            cls.coordinator
            .issue_expert_state_root_account_lock_request()
        )
        cls.root_authority = facade.acquire_expert_journal_root(
            request
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.coordinator.close()
        cls.temporary.cleanup()

    def _collect_source_seals(self) -> CandidateSourceSealsV1:
        authority = (
            facade
            .issue_sportradar_candidate_source_seal_collection_authority(
                self.root_authority
            )
        )
        return facade.collect_sportradar_candidate_source_seals(
            authority
        )

    def test_source_code_and_schema_seals_recompute_exactly(self) -> None:
        seals = self._collect_source_seals()
        expected_pins, expected_values = _expected_source_seals()
        self.assertEqual(
            tuple(
                (
                    pin.normalizer_id,
                    pin.source_kind,
                    pin.source_id,
                    pin.event_type,
                    pin.event_version,
                    pin.normalizer_code_sha256,
                    pin.normalizer_schema_sha256,
                )
                for pin in seals.normalizer_pins
            ),
            expected_pins,
        )
        for name, expected in expected_values.items():
            with self.subTest(field=name):
                self.assertEqual(getattr(seals, name), expected)

    def test_every_sealed_source_is_tamper_sensitive(self) -> None:
        _, baseline = _expected_source_seals()
        for logical in ALL_SEALED_PATHS:
            with self.subTest(logical=logical):
                original = (ROOT / logical).read_bytes()
                _, changed = _expected_source_seals(
                    {logical: original + b"\x00"}
                )
                self.assertNotEqual(
                    changed["candidate_source_seals_sha256"],
                    baseline["candidate_source_seals_sha256"],
                )
                if logical in {
                    "inci_tennis_adapters/registry.py",
                    (
                        "inci_tennis_adapters/"
                        "sportradar_tennis_v3.py"
                    ),
                }:
                    changed_pins, _ = _expected_source_seals(
                        {logical: original + b"\x00"}
                    )
                    baseline_pins, _ = _expected_source_seals()
                    self.assertNotEqual(
                        changed_pins[0][5],
                        baseline_pins[0][5],
                    )
                if logical in SCHEMAS[:3]:
                    changed_pins, _ = _expected_source_seals(
                        {logical: original + b"\x00"}
                    )
                    baseline_pins, _ = _expected_source_seals()
                    route_index = SCHEMAS.index(logical)
                    self.assertNotEqual(
                        changed_pins[route_index][6],
                        baseline_pins[route_index][6],
                    )

    def test_candidate_routes_are_literal_and_registries_stay_empty(
        self,
    ) -> None:
        from inci_tennis_adapters import registry
        from tennis_v1 import adapter_contract

        expected = (
            (
                "provider",
                "sportradar",
                "sportradar_tennis_summary_v3",
                1,
                "sportradar-tennis-summary-v3",
                SCHEMAS[0],
            ),
            (
                "provider",
                "sportradar",
                "sportradar_tennis_timeline_v3",
                1,
                "sportradar-tennis-timeline-v3",
                SCHEMAS[1],
            ),
            (
                "provider",
                "sportradar",
                "sportradar_tennis_transport_error_v1",
                1,
                "sportradar-tennis-transport-error-v1",
                SCHEMAS[2],
            ),
        )
        self.assertEqual(registry.CANDIDATE_ROUTES, expected)
        keys = tuple(route[:4] for route in expected)
        self.assertEqual(keys, tuple(sorted(keys)))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(adapter_contract._ADAPTER_REGISTRY, {})
        self.assertEqual(registry.PRODUCTION_PROVIDER_REGISTRY, ())

    def test_q7_uuid_trace_manifest_and_real_authority_equations(
        self,
    ) -> None:
        from inci_tennis_io.provider_readonly import (
            build_sportradar_candidate_session_manifest,
            issue_sportradar_candidate_capture_authorities,
        )
        from inci_tennis_runtime.provider_qualification_controller import (
            evaluate_sportradar_candidate_offline,
        )

        source_seals = self._collect_source_seals()
        artifacts = _validated_artifacts(
            parent=self.root_path,
            source_seals=source_seals,
            requested_matches=2,
        )
        decision = evaluate_sportradar_candidate_offline(
            artifacts=artifacts,
            source_seals=source_seals,
        )
        self.assertTrue(
            decision.eligible_for_candidate_observation
        )
        self.assertEqual(
            decision.reasons,
            (QualificationReason.ELIGIBLE,),
        )
        self.assertIsNotNone(decision.binding)
        self.assertIsNotNone(decision.quota)
        binding = decision.binding
        quota = decision.quota
        assert binding is not None
        assert quota is not None

        strata_projection = tuple(
            (
                ("sport", item.stratum.sport),
                ("tour", item.stratum.tour),
                (
                    "competition_tier",
                    item.stratum.competition_tier,
                ),
                ("match_format", item.stratum.match_format),
                ("round_code", item.stratum.round_code),
                ("matches", item.matches),
            )
            for item in artifacts.required_strata
        )
        expected_research_request_sha256 = _digest(
            b"INCI-SPORTRADAR-CANDIDATE-RESEARCH-REQUEST-V1\0",
            (
                ("intended_use", "private_paper_evaluation"),
                (
                    "session_start_wall_ns",
                    artifacts.session_start_wall_ns,
                ),
                (
                    "session_end_wall_ns",
                    artifacts.session_end_wall_ns,
                ),
                (
                    "required_retention_until_ns",
                    artifacts.required_retention_until_ns,
                ),
                ("expiry_safety_margin_seconds", 60),
                (
                    "required_raw_retention_seconds",
                    (
                        artifacts.required_retention_until_ns
                        - artifacts.session_start_wall_ns
                    )
                    // 1_000_000_000,
                ),
                ("requested_matches", 2),
                (
                    "required_candidate_capabilities",
                    artifacts.required_candidate_capabilities,
                ),
                ("required_strata", strata_projection),
            ),
        )
        expected_permission_scope_sha256 = _digest(
            b"INCI-SPORTRADAR-CANDIDATE-PERMISSION-SCOPE-V1\0",
            (
                ("provider_id", "sportradar"),
                ("product_tier", artifacts.product_tier),
                ("terms_version", artifacts.terms_version),
                (
                    "permission_artifact_sha256",
                    artifacts.permission_artifact_sha256,
                ),
                ("intended_use", "private_paper_evaluation"),
                ("publication_allowed", False),
            ),
        )
        expected_session_name_sha256 = _digest(
            b"INCI-SPORTRADAR-CANDIDATE-SESSION-NAME-V1\0",
            (
                (
                    "candidate_manifest_sha256",
                    artifacts.candidate_manifest_sha256,
                ),
                (
                    "manifest_core_sha256",
                    artifacts.manifest_core_sha256,
                ),
                (
                    "candidate_authorization_sha256",
                    artifacts.candidate_authorization_sha256,
                ),
                (
                    "candidate_source_seals_sha256",
                    source_seals.candidate_source_seals_sha256,
                ),
                (
                    "quota_closure_sha256",
                    quota.quota_closure_sha256,
                ),
                (
                    "match_binding_universe_sha256",
                    artifacts.universe.universe_sha256,
                ),
                (
                    "requested_provider_match_ids",
                    artifacts.requested_provider_match_ids,
                ),
                (
                    "session_start_wall_ns",
                    artifacts.session_start_wall_ns,
                ),
                (
                    "session_end_wall_ns",
                    artifacts.session_end_wall_ns,
                ),
                (
                    "retention_delete_by_ns",
                    artifacts.required_retention_until_ns,
                ),
            ),
        )
        expected_session_id = str(
            uuid.uuid5(
                SESSION_NAMESPACE,
                expected_session_name_sha256,
            )
        )
        expected_trace_sha256 = _digest(
            (
                b"INCI-SPORTRADAR-CANDIDATE-"
                b"PREOBSERVATION-TRACE-V1\0"
            ),
            (
                ("trace_state", "empty_pre_observation"),
                ("session_id", expected_session_id),
                (
                    "candidate_manifest_sha256",
                    artifacts.candidate_manifest_sha256,
                ),
                (
                    "manifest_core_sha256",
                    artifacts.manifest_core_sha256,
                ),
                (
                    "candidate_authorization_sha256",
                    artifacts.candidate_authorization_sha256,
                ),
                (
                    "candidate_source_seals_sha256",
                    source_seals.candidate_source_seals_sha256,
                ),
                (
                    "quota_closure_sha256",
                    quota.quota_closure_sha256,
                ),
                (
                    "match_binding_universe_sha256",
                    artifacts.universe.universe_sha256,
                ),
                (
                    "requested_provider_match_ids",
                    artifacts.requested_provider_match_ids,
                ),
                (
                    "retention_delete_by_ns",
                    artifacts.required_retention_until_ns,
                ),
            ),
        )
        self.assertEqual(
            binding.candidate_research_request_sha256,
            expected_research_request_sha256,
        )
        self.assertEqual(
            binding.candidate_permission_scope_sha256,
            expected_permission_scope_sha256,
        )
        self.assertEqual(binding.session_id, expected_session_id)
        self.assertEqual(
            binding.candidate_preobservation_trace_sha256,
            expected_trace_sha256,
        )
        self.assertNotEqual(expected_trace_sha256, "0" * 64)
        parsed_uuid = uuid.UUID(expected_session_id)
        self.assertEqual(parsed_uuid.version, 5)
        self.assertEqual(str(parsed_uuid), expected_session_id)

        manifest = build_sportradar_candidate_session_manifest(
            artifacts=artifacts,
            source_seals=source_seals,
            decision=decision,
        )
        expected_field_names = (
            "schema_version",
            "session_id",
            "created_wall_ns",
            "config_file_sha256",
            "config_canonical_sha256",
            "code_sha256",
            "research_request_sha256",
            "provider_id",
            "product_tier",
            "source_lineage_id",
            "provider_manifest_file_sha256",
            "provider_manifest_canonical_sha256",
            "entitlement_id_sha256",
            "terms_version",
            "permission_artifact_sha256",
            "qualification_artifact_sha256",
            "qualification_trace_sha256",
            "adapter_code_sha256",
            "auth_contract_sha256",
            "quota_contract_sha256",
            "session_end_ns",
            "required_retention_until_ns",
            "access_expires_at_ns",
            "analysis_expires_at_ns",
            "research_evaluable",
        )
        self.assertEqual(
            tuple(field.name for field in fields(SessionManifest)),
            expected_field_names,
        )
        expected_manifest = {
            "schema_version": 1,
            "session_id": expected_session_id,
            "created_wall_ns": artifacts.artifact_created_wall_ns,
            "config_file_sha256": (
                artifacts.candidate_manifest_sha256
            ),
            "config_canonical_sha256": (
                artifacts.manifest_core_sha256
            ),
            "code_sha256": (
                source_seals.candidate_source_seals_sha256
            ),
            "research_request_sha256": (
                expected_research_request_sha256
            ),
            "provider_id": "sportradar",
            "product_tier": artifacts.product_tier,
            "source_lineage_id": artifacts.source_lineage_id,
            "provider_manifest_file_sha256": (
                artifacts.candidate_manifest_sha256
            ),
            "provider_manifest_canonical_sha256": (
                artifacts.manifest_core_sha256
            ),
            "entitlement_id_sha256": (
                expected_permission_scope_sha256
            ),
            "terms_version": artifacts.terms_version,
            "permission_artifact_sha256": (
                artifacts.permission_artifact_sha256
            ),
            "qualification_artifact_sha256": (
                artifacts.candidate_authorization_sha256
            ),
            "qualification_trace_sha256": (
                expected_trace_sha256
            ),
            "adapter_code_sha256": (
                source_seals.candidate_adapter_inventory_sha256
            ),
            "auth_contract_sha256": (
                artifacts.auth_contract_sha256
            ),
            "quota_contract_sha256": (
                quota.quota_closure_sha256
            ),
            "session_end_ns": artifacts.session_end_wall_ns,
            "required_retention_until_ns": (
                artifacts.required_retention_until_ns
            ),
            "access_expires_at_ns": (
                artifacts.access_expires_at_ns
            ),
            "analysis_expires_at_ns": (
                artifacts.analysis_expires_at_ns
            ),
            "research_evaluable": False,
        }
        self.assertEqual(len(expected_manifest), 25)
        for name in expected_field_names:
            with self.subTest(manifest_field=name):
                self.assertEqual(
                    getattr(manifest, name),
                    expected_manifest[name],
                )

        authorities = issue_sportradar_candidate_capture_authorities(
            artifacts=artifacts,
            source_seals=source_seals,
            decision=decision,
            session_manifest=manifest,
        )
        self.assertEqual(len(authorities), 2)
        for authority, provider_match_id in zip(
            authorities,
            artifacts.requested_provider_match_ids,
            strict=True,
        ):
            with self.subTest(provider_match_id=provider_match_id):
                self.assertIs(type(authority), CaptureAuthority)
                self.assertEqual(
                    (
                        authority.session_id,
                        authority.source_kind,
                        authority.source_id,
                        authority.source_entity_id,
                        authority.endpoint_id,
                        authority.channel_id,
                        authority.connection_epoch,
                    ),
                    (
                        expected_session_id,
                        SourceKind.PROVIDER,
                        "sportradar",
                        provider_match_id,
                        "sportradar-api",
                        "sportradar-rest",
                        1,
                    ),
                )

    def test_all_six_quota_coordinates_fail_closed_independently(
        self,
    ) -> None:
        from inci_tennis_io.provider_readonly import (
            candidate_quota_demand,
        )
        from inci_tennis_runtime.provider_qualification_controller import (
            evaluate_sportradar_candidate_offline,
        )

        source_seals = self._collect_source_seals()
        start = 1_894_730_400_000_000_000
        demand = candidate_quota_demand(
            requested_matches=1,
            session_start_wall_ns=start,
            session_end_wall_ns=start + 100_000_000_000,
        )
        for name in QUOTA_FIELDS:
            with self.subTest(quota=name):
                artifacts = _validated_artifacts(
                    parent=self.root_path,
                    source_seals=source_seals,
                    declared_changes={
                        name: getattr(demand, name) - 1,
                    },
                )
                decision = evaluate_sportradar_candidate_offline(
                    artifacts=artifacts,
                    source_seals=source_seals,
                )
                self.assertFalse(
                    decision.eligible_for_candidate_observation
                )
                self.assertEqual(
                    decision.reasons,
                    (QualificationReason.QUOTA_INADEQUATE,),
                )
                self.assertIsNone(decision.binding)
                self.assertIsNone(decision.quota)

    def test_half_open_duration_and_utc_boundary_vectors_are_exact(
        self,
    ) -> None:
        from inci_tennis_io.provider_readonly import (
            candidate_maximum_trace_bytes,
            candidate_quota_demand,
        )

        start = DAY_NS - 500_000_000
        vectors = (
            (
                1,
                (18, 18, 18, 1, 2, 4),
                4_231_168,
            ),
            (
                10,
                (18, 18, 18, 1, 2, 4),
                4_231_168,
            ),
            (
                11,
                (18, 18, 18, 1, 2, 4),
                12_685_312,
            ),
            (
                3_600,
                (18, 724, 18, 1, 2, 4),
                1_530_204_160,
            ),
        )
        for duration, expected_quota, expected_trace in vectors:
            with self.subTest(duration=duration):
                demand = candidate_quota_demand(
                    requested_matches=2,
                    session_start_wall_ns=start,
                    session_end_wall_ns=(
                        start + duration * 1_000_000_000
                    ),
                )
                self.assertEqual(
                    tuple(
                        getattr(demand, name)
                        for name in QUOTA_FIELDS
                    ),
                    expected_quota,
                )
                self.assertEqual(
                    candidate_maximum_trace_bytes(
                        requested_matches=2,
                        duration_seconds=duration,
                    ),
                    expected_trace,
                )

    def test_source_seal_authority_is_one_shot_and_owner_bound(
        self,
    ) -> None:
        with self.assertRaises(TypeError):
            CandidateSourceSealCollectionAuthorityV1()

        authority = (
            facade
            .issue_sportradar_candidate_source_seal_collection_authority(
                self.root_authority
            )
        )
        self.assertIs(
            type(
                facade.collect_sportradar_candidate_source_seals(
                    authority
                )
            ),
            CandidateSourceSealsV1,
        )
        with self.assertRaisesRegex(
            ValueError,
            r"\Acandidate_source_seal_collection_failed\Z",
        ):
            facade.collect_sportradar_candidate_source_seals(
                authority
            )

        cross_thread = (
            facade
            .issue_sportradar_candidate_source_seal_collection_authority(
                self.root_authority
            )
        )
        failures: list[BaseException] = []

        def collect_from_wrong_owner() -> None:
            try:
                facade.collect_sportradar_candidate_source_seals(
                    cross_thread
                )
            except BaseException as error:
                failures.append(error)

        worker = threading.Thread(target=collect_from_wrong_owner)
        worker.start()
        worker.join()
        self.assertEqual(len(failures), 1)
        self.assertIs(type(failures[0]), ValueError)
        self.assertEqual(
            str(failures[0]),
            "candidate_source_seal_collection_failed",
        )
        with self.assertRaisesRegex(
            ValueError,
            r"\Acandidate_source_seal_collection_failed\Z",
        ):
            facade.collect_sportradar_candidate_source_seals(
                cross_thread
            )


if __name__ == "__main__":
    unittest.main()
