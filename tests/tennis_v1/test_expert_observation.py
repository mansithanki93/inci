from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from decimal import Decimal
import unittest

from tennis_v1.codec import canonical_record_sha256
from tennis_v1.events import (
    PersistedEvent,
    ProvenanceState,
    RecordKind,
    SourceKind,
)

from inci_tennis_expert.contracts import (
    ExpertCapacityProofV1,
    ExpertCurrentEnvironmentV1,
    ExpertEventSchemaBundleV1,
    ExpertEventSchemaPinV1,
    ExpertEventKindV1,
    ExpertIgnoreReasonV1,
    ExpertIgnoredDraftV1,
    ExpertIgnoredObservationV1,
    ExpertNormalizerPinV1,
    ExpertNormalizerRegistryV1,
    ExpertObservationRejectedPayloadV1,
    ExpertParentEvidenceV1,
    ExpertProviderDomainBindingV1,
    ExpertRejectedDraftV1,
    ExpertRejectedObservationV1,
    ExpertRejectReasonV1,
    ExpertRetentionBindingV1,
    ExpertSchemaPinV1,
    ExpertSessionManifestV1,
    ExpertStructuralSchemaBundleV1,
    ExpertSynchronizationDraftV1,
    ExpertSynchronizationObservationV1,
    BookLevel,
    ContractSide,
    PlayerSide,
    SyncInputKind,
    SynchronizationInput,
    canonical_expert_bytes,
    compute_membership_projection_sha256,
    compute_expert_provider_source_lineage_sha256,
    expert_contract_sha256,
)
from inci_tennis_expert.observation import (
    _parent_evidence,
    bind_expert_observation_drafts,
    normalize_expert_parent,
    prove_expert_capacity,
)
from inci_tennis_expert.market_book import book_from_snapshot
from inci_tennis_expert.synchronizer import (
    synchronization_session_from_artifacts,
)
from tests.tennis_v1.test_expert_contracts import (
    binding_metadata,
    binding_market_metadata,
    binding_route,
    binding_universe,
    book_snapshot,
    book_state,
    match_binding,
    sync_policy,
    synchronization_input,
)


SHA_A = "a" * 64
SCHEMA_ROOT = (
    Path(__file__).resolve().parents[2]
    / "inci_tennis_expert"
    / "schemas"
)


def domain_sha256(domain: bytes, projection: object) -> str:
    return sha256(domain + canonical_expert_bytes(projection)).hexdigest()


def _numbered_binding_artifacts(
    count: int,
    source_lineage_sha256: str,
) -> tuple[tuple[object, ...], tuple[object, ...]]:
    bindings: list[object] = []
    metadata_items: list[object] = []
    for index in range(1, count + 1):
        token = f"{index:03d}"
        canonical_match_id = f"canonical-match-{token}"
        provider_home_player_id = f"provider-home-{token}"
        provider_away_player_id = f"provider-away-{token}"
        canonical_home_player_id = f"canonical-home-{token}"
        canonical_away_player_id = f"canonical-away-{token}"
        event_ticker = f"MATCH-{token}-EVENT"
        event_id = f"kalshi-event-{token}"
        home_ticker = f"MATCH-{token}-HOME"
        away_ticker = f"MATCH-{token}-AWAY"

        binding = match_binding(
            provider_match_id=f"provider-match-{token}",
            canonical_match_id=canonical_match_id,
            source_lineage_sha256=source_lineage_sha256,
            provider_home_player_id=provider_home_player_id,
            provider_away_player_id=provider_away_player_id,
            kalshi_event_ticker=event_ticker,
            home_market_ticker=home_ticker,
            away_market_ticker=away_ticker,
        )
        markets = []
        for side, ticker, market_suffix, provider_player, canonical_player in (
            (
                PlayerSide.HOME,
                home_ticker,
                "home",
                provider_home_player_id,
                canonical_home_player_id,
            ),
            (
                PlayerSide.AWAY,
                away_ticker,
                "away",
                provider_away_player_id,
                canonical_away_player_id,
            ),
        ):
            market_id = f"market-{market_suffix}-{token}"
            evidence_sha256 = (
                sha256(f"membership-{market_suffix}-{token}".encode()).hexdigest()
            )
            projection_sha256 = compute_membership_projection_sha256(
                series_ticker="TENNIS-SERIES",
                event_ticker=event_ticker,
                event_id=event_id,
                market_ticker=ticker,
                market_id=market_id,
                product="match_winner",
                event_catalog_sha256="b" * 64,
                membership_source_id="kalshi-events-api",
                membership_source_version="v2",
                membership_captured_wall_ns=40,
                membership_evidence_sha256=evidence_sha256,
            )
            markets.append(
                binding_market_metadata(
                    player_side=side,
                    event_ticker=event_ticker,
                    event_id=event_id,
                    market_ticker=ticker,
                    market_id=market_id,
                    yes_provider_player_id=provider_player,
                    yes_canonical_player_id=canonical_player,
                    membership_evidence_sha256=evidence_sha256,
                    membership_projection_sha256=projection_sha256,
                )
            )
        metadata = binding_metadata(
            canonical_match_id=canonical_match_id,
            canonical_home_player_id=canonical_home_player_id,
            canonical_away_player_id=canonical_away_player_id,
            markets=tuple(markets),
            authorized_routes=(
                binding_route(
                    player_side=PlayerSide.HOME,
                    market_ticker=home_ticker,
                    contract_side=ContractSide.YES,
                ),
                binding_route(
                    player_side=PlayerSide.AWAY,
                    market_ticker=away_ticker,
                    contract_side=ContractSide.YES,
                ),
            ),
        )
        bindings.append(binding)
        metadata_items.append(metadata)
    return tuple(bindings), tuple(metadata_items)


def task6_artifacts(
    *,
    binding_count: int = 1,
) -> tuple[object, object, ExpertSessionManifestV1]:
    provider_id = "provider-a"
    product_tier = "trial-tier"
    source_lineage_id = "lineage-a"
    provider_manifest_sha256 = "c" * 64
    lineage_sha256 = domain_sha256(
        b"INCI-EXPERT-PROVIDER-SOURCE-LINEAGE-V1\0",
        (
            provider_id,
            product_tier,
            source_lineage_id,
            provider_manifest_sha256,
        ),
    )
    if binding_count == 1:
        binding = match_binding(source_lineage_sha256=lineage_sha256)
        bindings: tuple[object, ...] = (binding,)
        metadata_items: tuple[object, ...] = (binding_metadata(),)
    else:
        bindings, metadata_items = _numbered_binding_artifacts(
            binding_count,
            lineage_sha256,
        )
        binding = bindings[0]
    universe = binding_universe(
        bindings=bindings,
        metadata=metadata_items,
    )
    policy = sync_policy(universe_sha256=universe.universe_sha256)
    empty_sync = synchronization_session_from_artifacts(universe, policy)
    sync_sha256 = expert_contract_sha256(policy)
    initial_sync_sha256 = expert_contract_sha256(empty_sync)

    structural_spec = (
        (
            "session_manifest",
            "ExpertSessionManifestV1",
            "expert-session-manifest-v1.schema.json",
        ),
        (
            "journal_record",
            "ExpertJournalRecordV1",
            "expert-journal-record-v1.schema.json",
        ),
        (
            "parent_group",
            "ExpertJournalGroupV1",
            "expert-journal-group-v1.schema.json",
        ),
        (
            "session_terminal",
            "ExpertSessionTerminalV1",
            "expert-session-terminal-v1.schema.json",
        ),
    )
    structural_pins = tuple(
        ExpertSchemaPinV1(
            role,
            contract,
            resource,
            sha256((SCHEMA_ROOT / resource).read_bytes()).hexdigest(),
        )
        for role, contract, resource in structural_spec
    )
    structural = ExpertStructuralSchemaBundleV1(
        schema_version=1,
        pins=structural_pins,
        bundle_sha256=domain_sha256(
            b"INCI-EXPERT-STRUCTURAL-SCHEMA-BUNDLE-V1\0",
            (1, structural_pins),
        ),
    )
    event_spec = (
        (
            ExpertEventKindV1.SYNCHRONIZATION_APPLIED,
            "ExpertSynchronizationAppliedPayloadV1",
            "expert-synchronization-applied-v1.schema.json",
        ),
        (
            ExpertEventKindV1.OBSERVATION_IGNORED,
            "ExpertObservationIgnoredPayloadV1",
            "expert-observation-ignored-v1.schema.json",
        ),
        (
            ExpertEventKindV1.OBSERVATION_REJECTED,
            "ExpertObservationRejectedPayloadV1",
            "expert-observation-rejected-v1.schema.json",
        ),
    )
    event_pins = tuple(
        ExpertEventSchemaPinV1(
            kind,
            1,
            contract,
            resource,
            sha256((SCHEMA_ROOT / resource).read_bytes()).hexdigest(),
        )
        for kind, contract, resource in event_spec
    )
    event_schemas = ExpertEventSchemaBundleV1(
        schema_version=1,
        pins=event_pins,
        bundle_sha256=domain_sha256(
            b"INCI-EXPERT-EVENT-SCHEMA-BUNDLE-V1\0",
            (1, event_pins),
        ),
    )
    fallback_source = (
        Path(__file__).resolve().parents[2]
        / "inci_tennis_expert"
        / "task6_fallback_normalizer.py"
    ).read_bytes()
    fallback = ExpertNormalizerPinV1(
        normalizer_id="task6-fallback-v1",
        source_kind="fallback",
        source_id="task6",
        event_type="unregistered",
        event_version=1,
        normalizer_code_sha256=domain_sha256(
            b"INCI-EXPERT-NORMALIZER-CODE-V1\0",
            (
                (
                    "inci_tennis_expert/task6_fallback_normalizer.py",
                    sha256(fallback_source).hexdigest(),
                ),
            ),
        ),
        normalizer_schema_sha256=sha256(
            (
                SCHEMA_ROOT / "task6-fallback-no-payload-v1.schema.json"
            ).read_bytes()
        ).hexdigest(),
    )
    normalizers = ExpertNormalizerRegistryV1(
        schema_version=1,
        fallback=fallback,
        entries=(),
        registry_sha256=domain_sha256(
            b"INCI-EXPERT-NORMALIZER-REGISTRY-V1\0",
            (1, fallback, ()),
        ),
    )
    evidence_manifest_sha256 = "b" * 64
    provider_domain_values: dict[str, object] = {
        "schema_version": 1,
        "phase1_session_manifest_sha256": evidence_manifest_sha256,
        "match_binding_universe_sha256": universe.universe_sha256,
        "provider_id": provider_id,
        "product_tier": product_tier,
        "source_lineage_id": source_lineage_id,
        "provider_manifest_canonical_sha256": provider_manifest_sha256,
        "provider_source_lineage_sha256": lineage_sha256,
        "revision_domain_id": binding.revision_domain_id,
    }
    provider_domain_values["provider_domain_binding_sha256"] = domain_sha256(
        b"INCI-EXPERT-PROVIDER-DOMAIN-BINDING-V1\0",
        tuple(provider_domain_values.values()),
    )
    provider_domain = ExpertProviderDomainBindingV1(
        **provider_domain_values  # type: ignore[arg-type]
    )
    session_id = "11111111-1111-4111-8111-111111111111"
    retention_values: dict[str, object] = {
        "schema_version": 1,
        "session_id": session_id,
        "evidence_session_manifest_sha256": evidence_manifest_sha256,
        "provider_request_binding_sha256": "d" * 64,
        "permission_artifact_sha256": "e" * 64,
        "qualification_artifact_sha256": "f" * 64,
        "qualification_trace_sha256": "1" * 64,
        "retention_delete_by_ns": 1_000,
        "access_expires_at_ns": 800,
        "analysis_expires_at_ns": 900,
    }
    retention_values["retention_binding_sha256"] = domain_sha256(
        b"INCI-EXPERT-RETENTION-BINDING-V1\0",
        tuple(retention_values.values()),
    )
    retention = ExpertRetentionBindingV1(
        **retention_values  # type: ignore[arg-type]
    )
    capacity = prove_expert_capacity(universe, policy)
    environment = ExpertCurrentEnvironmentV1(
        schema_version=1,
        phase1_code_sha256="2" * 64,
        phase1_adapter_code_sha256="3" * 64,
        expert_code_sha256="4" * 64,
        io_code_sha256="5" * 64,
        expert_adapter_code_sha256="6" * 64,
        runtime_code_sha256="7" * 64,
        dependency_lock_sha256="8" * 64,
        python_runtime_sha256="9" * 64,
        normalizer_registry_sha256=normalizers.registry_sha256,
        structural_schema_bundle_sha256=structural.bundle_sha256,
        event_schema_bundle_sha256=event_schemas.bundle_sha256,
    )
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": session_id,
        "evidence_session_manifest_sha256": evidence_manifest_sha256,
        "evidence_session_start_record_sha256": "a" * 64,
        "provider_id": provider_id,
        "product_tier": product_tier,
        "source_lineage_id": source_lineage_id,
        "provider_manifest_file_sha256": "2" * 64,
        "provider_manifest_canonical_sha256": provider_manifest_sha256,
        "entitlement_id_sha256": "3" * 64,
        "provider_request_binding_sha256": retention.provider_request_binding_sha256,
        "permission_artifact_sha256": retention.permission_artifact_sha256,
        "qualification_artifact_sha256": retention.qualification_artifact_sha256,
        "qualification_trace_sha256": retention.qualification_trace_sha256,
        "provider_domain": provider_domain,
        "environment": environment,
        "retention": retention,
        "match_binding_universe_sha256": universe.universe_sha256,
        "binding_raw_artifact_id": universe.raw_artifact_id,
        "binding_raw_artifact_sha256": universe.raw_artifact_sha256,
        "binding_review_artifact_id": universe.review.review_artifact_id,
        "binding_review_artifact_sha256": universe.review.review_artifact_sha256,
        "sync_policy_sha256": sync_sha256,
        "initial_synchronization_sha256": initial_sync_sha256,
        "normalizers": normalizers,
        "structural_schemas": structural,
        "event_schemas": event_schemas,
        "capacity": capacity,
        "artifact_pins": (),
    }
    values["manifest_sha256"] = domain_sha256(
        b"INCI-EXPERT-SESSION-MANIFEST-V1\0",
        tuple(values.values()),
    )
    manifest = ExpertSessionManifestV1(**values)  # type: ignore[arg-type]
    return universe, policy, manifest


def raw_parent(**changes: object) -> PersistedEvent:
    payload = b'{"safe":"payload"}'
    values: dict[str, object] = {
        "journal_version": 1,
        "record_kind": RecordKind.RAW,
        "ingest_seq": 2,
        "session_id": "11111111-1111-4111-8111-111111111111",
        "event_type": "timer_tick",
        "event_version": 1,
        "source_kind": SourceKind.TIMER,
        "source_id": "clock",
        "source_entity_id": "clock-1",
        "endpoint_id": None,
        "endpoint_state": ProvenanceState.ABSENT,
        "channel_id": None,
        "channel_state": ProvenanceState.ABSENT,
        "request_id": None,
        "request_id_state": ProvenanceState.ABSENT,
        "source_wall_ns": None,
        "source_generated_ns": None,
        "local_wall_ns": 200,
        "local_monotonic_ns": 100,
        "clock_uncertainty_ns": 2,
        "connection_epoch": 0,
        "provider_sequence": None,
        "parent_ingest_seq": None,
        "content_type": "application/json",
        "payload_encoding": "canonical-json-v1",
        "payload_transform": "identity-public-market-v1",
        "retention_delete_by_ns": None,
        "payload_sha256": sha256(payload).hexdigest(),
        "payload": payload,
    }
    values.update(changes)
    return PersistedEvent(**values)  # type: ignore[arg-type]


def capacity_boundary_draft(
    canonical_match_id: str,
) -> ExpertSynchronizationDraftV1:
    levels = [
        BookLevel(
            Decimal(10_000 - index) / Decimal(10_000),
            Decimal("10"),
        )
        for index in range(1, 594)
    ]
    levels[-1] = BookLevel(
        levels[-1].price,
        Decimal("1" * 32),
    )
    snapshot = book_snapshot(
        yes_bids=tuple(levels),
        no_bids=(),
    )
    return ExpertSynchronizationDraftV1(
        synchronization_input(
            kind=SyncInputKind.BOOK_TRANSITION,
            canonical_match_id=canonical_match_id,
            previous_state_sha256=None,
            book_event=snapshot,
            book_transition=book_from_snapshot(snapshot),
        )
    )


class Task6ObservationContractTests(unittest.TestCase):
    def test_closed_event_and_reason_vocabularies(self) -> None:
        self.assertEqual(
            tuple(item.value for item in ExpertEventKindV1),
            (
                "synchronization_applied",
                "observation_ignored",
                "observation_rejected",
            ),
        )
        self.assertEqual(
            ExpertIgnoreReasonV1.NORMALIZER_NOT_REGISTERED.value,
            "normalizer_not_registered",
        )
        self.assertEqual(
            ExpertRejectReasonV1.SYNCHRONIZATION_SESSION_DRIFT.value,
            "synchronization_session_drift",
        )

    def test_provider_lineage_formula_keeps_tier_and_revision_domains_distinct(
        self,
    ) -> None:
        provider_manifest_sha256 = "c" * 64
        expected = domain_sha256(
            b"INCI-EXPERT-PROVIDER-SOURCE-LINEAGE-V1\0",
            (
                "provider-a",
                "trial-tier",
                "lineage-a",
                provider_manifest_sha256,
            ),
        )
        self.assertEqual(
            compute_expert_provider_source_lineage_sha256(
                "provider-a",
                "trial-tier",
                "lineage-a",
                provider_manifest_sha256,
            ),
            expected,
        )
        self.assertNotEqual(
            expected,
            compute_expert_provider_source_lineage_sha256(
                "provider-a",
                "paid-tier",
                "lineage-a",
                provider_manifest_sha256,
            ),
        )
        universe, _, manifest = task6_artifacts()
        self.assertEqual(
            manifest.provider_domain.revision_domain_id,
            universe.bindings[0].revision_domain_id,
        )
        self.assertNotEqual(
            manifest.product_tier,
            manifest.provider_domain.revision_domain_id,
        )

    def test_event_schema_pin_rejects_a_well_formed_replacement_digest(
        self,
    ) -> None:
        _, _, manifest = task6_artifacts()
        with self.assertRaisesRegex(ValueError, "schema_resource_sha256"):
            replace(
                manifest.event_schemas.pins[0],
                schema_resource_sha256="0" * 64,
            )

    def test_parent_evidence_copies_exact_durable_raw_identity_and_time(self) -> None:
        parent = raw_parent()
        pin = ExpertNormalizerPinV1(
            normalizer_id="task6-fallback-v1",
            source_kind="fallback",
            source_id="task6",
            event_type="unregistered",
            event_version=1,
            normalizer_code_sha256=SHA_A,
            normalizer_schema_sha256=SHA_A,
        )
        observation = ExpertIgnoredObservationV1(
            parent=ExpertParentEvidenceV1(
                session_id=parent.session_id,
                ingest_seq=parent.ingest_seq,
                record_sha256=canonical_record_sha256(parent),
                event_type=parent.event_type,
                event_version=parent.event_version,
                local_wall_ns=parent.local_wall_ns,
                local_monotonic_ns=parent.local_monotonic_ns,
                clock_uncertainty_ns=parent.clock_uncertainty_ns,
            ),
            parent_output_index=0,
            parent_output_count=1,
            normalizer_id=pin.normalizer_id,
            normalizer_code_sha256=pin.normalizer_code_sha256,
            normalizer_schema_sha256=pin.normalizer_schema_sha256,
            reason=ExpertIgnoreReasonV1.NORMALIZER_NOT_REGISTERED,
        )
        self.assertEqual(observation.parent.record_sha256, canonical_record_sha256(parent))
        self.assertEqual(
            (
                observation.parent.local_wall_ns,
                observation.parent.local_monotonic_ns,
                observation.parent.clock_uncertainty_ns,
            ),
            (200, 100, 2),
        )

    def test_control_parent_is_not_observation_evidence(self) -> None:
        parent = raw_parent(
            record_kind=RecordKind.CONTROL,
            event_type="SESSION_START",
            source_kind=SourceKind.SYSTEM,
            source_id="tennis-v1",
            source_entity_id="11111111-1111-4111-8111-111111111111",
            channel_id="session-control",
            channel_state=ProvenanceState.SAFE_ORIGINAL,
            clock_uncertainty_ns=0,
            content_type="application/vnd.inci.session-manifest+json",
        )
        with self.assertRaises(ValueError):
            _parent_evidence(parent)

    def test_draft_shapes_are_exclusive(self) -> None:
        self.assertEqual(
            ExpertIgnoredDraftV1(
                ExpertIgnoreReasonV1.EVENT_NOT_RELEVANT
            ).reason,
            ExpertIgnoreReasonV1.EVENT_NOT_RELEVANT,
        )
        self.assertEqual(
            ExpertRejectedDraftV1(
                ExpertRejectReasonV1.NORMALIZER_EXCEPTION
            ).reason,
            ExpertRejectReasonV1.NORMALIZER_EXCEPTION,
        )
        self.assertTrue(hasattr(ExpertSynchronizationDraftV1, "__dataclass_fields__"))
        self.assertTrue(hasattr(ExpertSynchronizationObservationV1, "__dataclass_fields__"))
        self.assertTrue(hasattr(ExpertRejectedObservationV1, "__dataclass_fields__"))

    def test_binder_rejects_empty_and_mixed_draft_groups_before_binding(self) -> None:
        # The manifest fixture is intentionally replaced by a sentinel here:
        # group-shape validation must precede manifest-dependent dispatch.
        with self.assertRaises((TypeError, ValueError)):
            bind_expert_observation_drafts(
                object(),  # type: ignore[arg-type]
                raw_parent(),
                object(),  # type: ignore[arg-type]
                (),
            )

    def test_static_fallback_ignores_payload_and_binds_one_ignored_result(self) -> None:
        _, _, manifest = task6_artifacts()
        first = normalize_expert_parent(manifest, raw_parent(payload=b"one", payload_sha256=sha256(b"one").hexdigest()))
        second = normalize_expert_parent(manifest, raw_parent(payload=b"two", payload_sha256=sha256(b"two").hexdigest()))
        self.assertEqual(len(first), 1)
        self.assertEqual(type(first[0]), ExpertIgnoredObservationV1)
        self.assertEqual(
            first[0].reason,
            ExpertIgnoreReasonV1.NORMALIZER_NOT_REGISTERED,
        )
        self.assertEqual(first[0].parent_output_index, 0)
        self.assertEqual(first[0].parent_output_count, 1)
        self.assertEqual(first[0].normalizer_id, "task6-fallback-v1")
        self.assertNotEqual(
            first[0].parent.record_sha256,
            second[0].parent.record_sha256,
        )
        self.assertEqual(first[0].reason, second[0].reason)

    def test_binder_copies_raw_time_into_each_synchronization_wrapper(self) -> None:
        _, _, manifest = task6_artifacts()
        evidence = synchronization_input()
        wrappers = bind_expert_observation_drafts(
            manifest,
            raw_parent(),
            manifest.normalizers.fallback,
            (
                ExpertSynchronizationDraftV1(evidence),
                ExpertSynchronizationDraftV1(evidence),
            ),
        )
        self.assertEqual(len(wrappers), 2)
        self.assertTrue(
            all(type(item) is ExpertSynchronizationObservationV1 for item in wrappers)
        )
        self.assertEqual(
            tuple(item.parent_output_index for item in wrappers),
            (0, 1),
        )
        self.assertEqual(
            tuple(item.observation for item in wrappers),
            (
                wrappers[0].observation,
                wrappers[0].observation,
            ),
        )
        self.assertEqual(
            (
                wrappers[0].observation.wall_ns,
                wrappers[0].observation.monotonic_ns,
                wrappers[0].observation.clock_uncertainty_ns,
            ),
            (200, 100, 2),
        )

    def test_mixed_and_sixty_five_drafts_are_contract_violations(self) -> None:
        _, _, manifest = task6_artifacts()
        evidence = synchronization_input()
        with self.assertRaisesRegex(ValueError, "normalizer_output_shape"):
            bind_expert_observation_drafts(
                manifest,
                raw_parent(),
                manifest.normalizers.fallback,
                (
                    ExpertSynchronizationDraftV1(evidence),
                    ExpertIgnoredDraftV1(
                        ExpertIgnoreReasonV1.EVENT_NOT_RELEVANT
                    ),
                ),
            )
        with self.assertRaisesRegex(ValueError, "normalizer_output_shape"):
            bind_expert_observation_drafts(
                manifest,
                raw_parent(),
                manifest.normalizers.fallback,
                tuple(
                    ExpertSynchronizationDraftV1(evidence)
                    for _ in range(65)
                ),
            )

    def test_unbounded_task1_ladder_is_replaced_before_task5_reduction(self) -> None:
        _, _, manifest = task6_artifacts()
        levels = tuple(
            BookLevel(
                Decimal(1_000_000 - index) / Decimal(1_000_001),
                Decimal("1"),
            )
            for index in range(2_000)
        )
        large_book = book_state(
            yes_bids=levels,
            no_bids=(),
            last_executable_move=Decimal("0"),
        )
        evidence = SynchronizationInput(
            kind=SyncInputKind.BOOK_RESNAPSHOT_REQUIRED,
            canonical_match_id="canonical-match-9",
            ticker="MATCH-HOME",
            previous_state_sha256="a" * 64,
            provider_event=None,
            tennis_transition=None,
            book_event=None,
            book_transition=None,
            book_resnapshot_state=large_book,
        )
        wrappers = bind_expert_observation_drafts(
            manifest,
            raw_parent(),
            manifest.normalizers.fallback,
            (ExpertSynchronizationDraftV1(evidence),),
        )
        self.assertEqual(len(wrappers), 1)
        self.assertEqual(type(wrappers[0]), ExpertRejectedObservationV1)
        self.assertEqual(
            wrappers[0].reason,
            ExpertRejectReasonV1.GROUP_CAPACITY_EXCEEDED,
        )

    def test_one_and_sixty_four_outputs_honor_exact_byte_boundaries(self) -> None:
        _, _, manifest = task6_artifacts()
        at_limit = capacity_boundary_draft("canonical-match-90")
        over_limit = capacity_boundary_draft("canonical-match-900")
        self.assertEqual(len(canonical_expert_bytes(at_limit)), 131_064)
        self.assertEqual(len(canonical_expert_bytes(over_limit)), 131_065)

        one = bind_expert_observation_drafts(
            manifest,
            raw_parent(),
            manifest.normalizers.fallback,
            (at_limit,),
        )
        self.assertEqual(len(one), 1)
        self.assertEqual(type(one[0]), ExpertSynchronizationObservationV1)

        maximum = bind_expert_observation_drafts(
            manifest,
            raw_parent(),
            manifest.normalizers.fallback,
            (at_limit,) * 64,
        )
        self.assertEqual(len(maximum), 64)
        self.assertEqual(
            tuple(item.parent_output_index for item in maximum),
            tuple(range(64)),
        )

        rejected = bind_expert_observation_drafts(
            manifest,
            raw_parent(),
            manifest.normalizers.fallback,
            (over_limit,),
        )
        self.assertEqual(len(rejected), 1)
        self.assertEqual(type(rejected[0]), ExpertRejectedObservationV1)
        self.assertEqual(
            rejected[0].reason,
            ExpertRejectReasonV1.GROUP_CAPACITY_EXCEEDED,
        )

    def test_capacity_proof_binds_artifacts_and_enforced_task6_ceiling(self) -> None:
        universe, policy, _ = task6_artifacts()
        proof = prove_expert_capacity(universe, policy)
        self.assertEqual(proof.maximum_output_count, 64)
        self.assertEqual(proof.maximum_synchronization_state_bytes, 131_064)
        self.assertEqual(proof.maximum_event_payload_bytes, 131_064)
        self.assertEqual(proof.maximum_group_payload_area_bytes, 8_388_608)
        self.assertEqual(proof.maximum_group_frame_bytes, 16_777_216)
        self.assertEqual(proof.maximum_terminal_frame_bytes, 1_048_652)
        self.assertEqual(proof.emergency_reserve_bytes, 17_825_868)

    def test_rejected_payload_reason_matrix_is_exact_and_closed(self) -> None:
        _, _, manifest = task6_artifacts()
        ignored = normalize_expert_parent(manifest, raw_parent())[0]
        synchronization = bind_expert_observation_drafts(
            manifest,
            raw_parent(),
            manifest.normalizers.fallback,
            (ExpertSynchronizationDraftV1(synchronization_input()),),
        )[0]
        rejected_base = bind_expert_observation_drafts(
            manifest,
            raw_parent(),
            manifest.normalizers.fallback,
            (
                ExpertRejectedDraftV1(
                    ExpertRejectReasonV1.PARENT_CONTRACT_INVALID
                ),
            ),
        )[0]
        self.assertEqual(type(ignored), ExpertIgnoredObservationV1)
        self.assertEqual(
            type(synchronization),
            ExpertSynchronizationObservationV1,
        )
        self.assertEqual(type(rejected_base), ExpertRejectedObservationV1)

        for reason in ExpertRejectReasonV1:
            rejected = replace(rejected_base, reason=reason)
            ExpertObservationRejectedPayloadV1(rejected, reason)
            wrong = next(item for item in ExpertRejectReasonV1 if item is not reason)
            with self.assertRaisesRegex(ValueError, "rejected_payload"):
                ExpertObservationRejectedPayloadV1(rejected, wrong)

        synchronization_reasons = {
            ExpertRejectReasonV1.SYNCHRONIZATION_SESSION_DRIFT,
            ExpertRejectReasonV1.REDUCER_EXCEPTION,
            ExpertRejectReasonV1.PRIOR_OUTCOME_HALTED,
        }
        for reason in ExpertRejectReasonV1:
            if reason in synchronization_reasons:
                ExpertObservationRejectedPayloadV1(
                    synchronization,
                    reason,
                )
            else:
                with self.assertRaisesRegex(ValueError, "rejected_payload"):
                    ExpertObservationRejectedPayloadV1(
                        synchronization,
                        reason,
                    )

        for reason in ExpertRejectReasonV1:
            if reason is ExpertRejectReasonV1.STATIC_SESSION_HALT:
                ExpertObservationRejectedPayloadV1(ignored, reason)
            else:
                with self.assertRaisesRegex(ValueError, "rejected_payload"):
                    ExpertObservationRejectedPayloadV1(ignored, reason)


if __name__ == "__main__":
    unittest.main()
