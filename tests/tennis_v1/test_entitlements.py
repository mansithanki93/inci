from __future__ import annotations

from contextlib import redirect_stdout
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import threading
from typing import TypedDict
import unittest
from unittest import mock

import tennis_v1.adapter_contract as adapter_contract
import tennis_v1.entitlements as entitlements_module
from tennis_v1.canonical import canonical_json_bytes
from tennis_v1.adapter_contract import (
    AdapterUsagePlan,
    AuthContract,
    AuthMode,
    ProviderQuotas,
    _AdapterContractSpec,
    _capture_adapter_registration,
    load_active_adapter_contract,
)
from tennis_v1.entitlements import (
    CAPABILITY_KEYS,
    MANIFEST_KEYS,
    PERMISSION_ARTIFACT_KEYS,
    QUALIFICATION_ARTIFACT_KEYS,
    QUALIFICATION_TRACE_KEYS,
    STRATUM_KEYS,
    TERMS_URL_PATTERN,
    BillingMode,
    CoverageStratum,
    IntendedUse,
    ManifestError,
    PermissionArtifact,
    PermissionBasis,
    PermissionOperation,
    ProviderCapabilities,
    ProviderGate,
    ProviderGateError,
    ProviderManifest,
    QualificationArtifact,
    QualificationDecision,
    QualificationReason,
    QualificationStatus,
    ResearchRequest,
    RequestedStratum,
    QualifiedProviderBinding,
    QualifiedStratumEvidence,
    canonical_manifest_sha256,
    evaluate_provider,
    load_permission_artifact,
    load_provider_manifest,
    load_qualification_trace,
    opaque_id_sha256,
    provider_request_binding_sha256,
)
from tennis_v1.config import TennisV1Config
from tennis_v1.qualification_protocol import qualification_protocol_sha256
UTC_PATTERN = r"^(?:[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z|[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z)$"
BOOL_CAPABILITIES = tuple(sorted(CAPABILITY_KEYS - {"supported_formats", "declared_strata"}))
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
ADAPTER_FILE = FIXTURE_ROOT / "synthetic_adapter.py"
STRATUM = {
    "sport": "tennis",
    "tour": "ATP",
    "competition_tier": "MASTERS-1000",
    "match_format": "BEST_OF_3",
    "round_code": "R16",
}


def synthetic_spec(
    *,
    provider_id: str = "synthetic-provider",
    product_tier: str = "trial-v1",
) -> _AdapterContractSpec:
    return _AdapterContractSpec(
        provider_id=provider_id,
        product_tier=product_tier,
        adapter_id="synthetic-read-only-v1",
        auth=AuthContract(
            mode=AuthMode.API_KEY,
            credential_env_names=("SYNTHETIC_API_KEY",),
        ),
        usage=AdapterUsagePlan(
            startup_requests_fixed=1,
            startup_requests_per_match=2,
            steady_requests_per_minute_fixed=1,
            steady_requests_per_minute_per_match=1,
            resync_requests_per_match=1,
            max_resyncs_per_match_per_hour=2,
            max_connections=1,
            subscriptions_per_match=1,
        ),
        formats=("rest_json", "websocket_json"),
    )


class FixtureBundle(TypedDict):
    manifest: dict[str, object]
    manifest_path: Path
    manifest_sha: str
    permission: dict[str, object]
    permission_path: Path
    permission_sha: str
    evidence_path: Path
    evidence_sha: str
    trace: dict[str, object]
    trace_path: Path
    trace_sha: str
    qualification: dict[str, object]
    qualification_path: Path
    qualification_sha: str


def utc(value: datetime) -> str:
    if value.microsecond:
        return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class FixtureBuilder:
    def __init__(self, root: Path, repo_root: Path):
        self.root = root
        self.repo_root = repo_root
        self.counter = 0
        self.adapter_file = str(ADAPTER_FILE)
        with mock.patch.object(adapter_contract, "__file__", self.adapter_file):
            self.registration = _capture_adapter_registration(
                module_paths=("synthetic_adapter.py",),
                spec=synthetic_spec(),
            )

    def _path(self, name: str) -> Path:
        self.counter += 1
        return self.root / f"{self.counter}-{name}"

    @staticmethod
    def _write_json(path: Path, raw: object, *, sort_keys: bool = False) -> str:
        path.write_text(
            json.dumps(raw, sort_keys=sort_keys, separators=(",", ":")),
            encoding="utf-8",
        )
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def active_adapter(self):
        with (
            mock.patch.object(adapter_contract, "__file__", self.adapter_file),
            mock.patch.dict(
                adapter_contract._ADAPTER_REGISTRY,
                {("synthetic-provider", "trial-v1"): self.registration},
                clear=True,
            ),
        ):
            return load_active_adapter_contract(
                provider_id="synthetic-provider", product_tier="trial-v1"
            )

    def build(
        self,
        *,
        manifest_change=None,
        permission_change=None,
        trace_change=None,
        qualification_change=None,
        evidence: bytes = b"synthetic trial terms snapshot",
        sort_manifest_keys: bool = False,
    ) -> FixtureBundle:
        start = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
        access_end = start + timedelta(days=7)
        trace_start = start + timedelta(minutes=1)
        trace_end = trace_start + timedelta(minutes=10)
        adapter = self.active_adapter()

        evidence_path = self._path("permission-evidence.bin")
        evidence_path.write_bytes(evidence)
        evidence_sha = hashlib.sha256(evidence).hexdigest()
        entitlement_id = "opaque-entitlement-fixture"
        permission = {
            "schema_version": 1,
            "provider_id": "synthetic-provider",
            "product_tier": "trial-v1",
            "entitlement_id_sha256": opaque_id_sha256(entitlement_id),
            "terms_version": "terms-2026-07",
            "basis": "trial_terms",
            "intended_use": "private_paper_evaluation",
            "permitted_operations": [
                "provider_ingest",
                "raw_retention",
                "derived_signals",
            ],
            "access_starts_at": utc(start),
            "access_expires_at": utc(access_end),
            "analysis_expires_at": utc(access_end),
            "raw_retention_until": utc(access_end),
            "reviewed_at": utc(start - timedelta(days=1)),
            "reviewer_id": "reviewer-test",
            "approval_id": "approval-test",
            "evidence_document_sha256": evidence_sha,
        }
        if permission_change:
            permission_change(permission)
        permission_path = self._path("permission.json")
        permission_sha = self._write_json(permission_path, permission)

        matches = [
            {
                "match_id_sha256": "1" * 64,
                "stratum": dict(STRATUM),
                "tested_format": "websocket_json",
                "started_at": utc(trace_start),
                "ended_at": utc(trace_start + timedelta(minutes=5)),
                "tested_capabilities": list(BOOL_CAPABILITIES),
            },
            {
                "match_id_sha256": "2" * 64,
                "stratum": dict(STRATUM),
                "tested_format": "rest_json",
                "started_at": utc(trace_start + timedelta(minutes=2)),
                "ended_at": utc(trace_start + timedelta(minutes=7)),
                "tested_capabilities": list(BOOL_CAPABILITIES),
            },
        ]
        trace = {
            "schema_version": 1,
            "provider_id": "synthetic-provider",
            "product_tier": "trial-v1",
            "source_lineage_id": "synthetic-lineage-v1",
            "adapter_code_sha256": adapter.adapter_code_sha256,
            "auth_contract_sha256": adapter.auth_contract_sha256,
            "quota_contract_sha256": adapter.quota_contract_sha256,
            "qualification_protocol_sha256": qualification_protocol_sha256(),
            "started_at": utc(trace_start),
            "completed_at": utc(trace_end),
            "matches": matches,
            "clean_terminal": True,
        }
        if trace_change:
            trace_change(trace)
        trace_path = self._path("qualification-trace.json")
        trace_sha = self._write_json(trace_path, trace)

        qualification = {
            "schema_version": 1,
            "provider_id": "synthetic-provider",
            "product_tier": "trial-v1",
            "source_lineage_id": "synthetic-lineage-v1",
            "adapter_code_sha256": adapter.adapter_code_sha256,
            "auth_contract_sha256": adapter.auth_contract_sha256,
            "quota_contract_sha256": adapter.quota_contract_sha256,
            "qualification_protocol_sha256": qualification_protocol_sha256(),
            "evidence_trace_sha256": trace_sha,
            "issuer_id": "issuer-test",
            "approval_id": "qualification-approval",
            "issued_at": utc(trace_end + timedelta(minutes=1)),
            "status": "passed",
            "qualified_at": utc(trace_end),
            "qualified_until": utc(trace_end + timedelta(days=6)),
            "observed_matches": 2,
            "simultaneous_matches_tested": 2,
            "strata": [
                {
                    "stratum": dict(STRATUM),
                    "observed_matches": 2,
                    "simultaneous_matches_tested": 2,
                    "tested_formats": ["rest_json", "websocket_json"],
                    "tested_capabilities": list(BOOL_CAPABILITIES),
                }
            ],
        }
        if qualification_change:
            qualification_change(qualification)
        qualification_path = self._path("qualification.json")
        qualification_sha = self._write_json(qualification_path, qualification)

        capabilities = {name: True for name in BOOL_CAPABILITIES}
        capabilities.update(
            {
                "supported_formats": ["rest_json", "websocket_json"],
                "declared_strata": [dict(STRATUM)],
            }
        )
        manifest = {
            "schema_version": 1,
            "provider_id": "synthetic-provider",
            "product_tier": "trial-v1",
            "entitlement_id": entitlement_id,
            "source_lineage_id": "synthetic-lineage-v1",
            "terms_url": "https://provider.invalid/terms/trial-v1",
            "terms_version": "terms-2026-07",
            "permission": {
                "artifact_path": str(permission_path),
                "artifact_sha256": permission_sha,
                "evidence_path": str(evidence_path),
                "evidence_sha256": evidence_sha,
            },
            "billing_mode": "trial",
            "auto_renew": False,
            "access_starts_at": utc(start),
            "access_expires_at": utc(access_end),
            "analysis_expires_at": utc(access_end),
            "raw_retention_until": utc(access_end),
            "max_raw_retention_seconds": 7 * 24 * 60 * 60,
            "credential_env_names": ["SYNTHETIC_API_KEY"],
            "quotas": {
                "requests_per_rolling_60_seconds": 100,
                "requests_per_utc_calendar_day": 10000,
                "requests_per_rolling_second": 20,
                "max_connections": 2,
                "max_subscriptions": 10,
                "resync_requests_per_rolling_hour": 20,
            },
            "capabilities": capabilities,
            "qualification": {
                "artifact_path": str(qualification_path),
                "artifact_sha256": qualification_sha,
                "evidence_trace_path": str(trace_path),
                "evidence_trace_sha256": trace_sha,
            },
        }
        if manifest_change:
            manifest_change(manifest)
        manifest_path = self._path("manifest.json")
        manifest_sha = self._write_json(
            manifest_path, manifest, sort_keys=sort_manifest_keys
        )
        return {
            "manifest": manifest,
            "manifest_path": manifest_path,
            "manifest_sha": manifest_sha,
            "permission": permission,
            "permission_path": permission_path,
            "permission_sha": permission_sha,
            "evidence_path": evidence_path,
            "evidence_sha": evidence_sha,
            "trace": trace,
            "trace_path": trace_path,
            "trace_sha": trace_sha,
            "qualification": qualification,
            "qualification_path": qualification_path,
            "qualification_sha": qualification_sha,
        }

    def load(self, bundle: FixtureBundle):
        with (
            mock.patch.object(adapter_contract, "__file__", self.adapter_file),
            mock.patch.dict(
                adapter_contract._ADAPTER_REGISTRY,
                {("synthetic-provider", "trial-v1"): self.registration},
                clear=True,
            ),
        ):
            return load_provider_manifest(
                bundle["manifest_path"],
                expected_sha256=bundle["manifest_sha"],
                repo_root=self.repo_root,
            )


class ProviderManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()
        self.builder = FixtureBuilder(self.root, self.repo_root)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def assert_invalid(self, **changes) -> None:
        with self.assertRaises(ManifestError):
            self.builder.load(self.builder.build(**changes))

    def test_manifest_round_trips_without_secret_values(self) -> None:
        loaded = self.builder.load(self.builder.build())
        self.assertEqual(loaded.provider_id, "synthetic-provider")
        self.assertEqual(loaded.permission.basis, PermissionBasis.TRIAL_TERMS)
        self.assertEqual(loaded.qualification.status, QualificationStatus.PASSED)
        self.assertEqual(loaded.billing_mode, BillingMode.TRIAL)
        with self.assertRaises(FrozenInstanceError):
            loaded.product_tier = "changed"  # type: ignore[misc]
        self.assertNotIn("opaque-entitlement-fixture", repr(loaded))

    def test_manifest_hash_is_stable_across_json_key_order(self) -> None:
        first = self.builder.load(self.builder.build(sort_manifest_keys=False))
        second = self.builder.load(self.builder.build(sort_manifest_keys=True))
        self.assertEqual(first.canonical_sha256, second.canonical_sha256)
        self.assertEqual(canonical_manifest_sha256(first), first.canonical_sha256)

    def test_exact_file_digest_and_canonical_digest_are_distinct_and_bound(self) -> None:
        bundle = self.builder.build()
        loaded = self.builder.load(bundle)
        self.assertEqual(
            loaded.source_file_sha256,
            hashlib.sha256(Path(bundle["manifest_path"]).read_bytes()).hexdigest(),
        )
        self.assertNotEqual(loaded.source_file_sha256, loaded.canonical_sha256)
        with self.assertRaises(ManifestError):
            load_provider_manifest(
                bundle["manifest_path"],
                expected_sha256="0" * 64,
                repo_root=self.repo_root,
            )

    def test_unknown_or_missing_top_level_field_fails(self) -> None:
        for mutate in (
            lambda raw: raw.__setitem__("live_enabled", True),
            lambda raw: raw.pop("terms_version"),
        ):
            with self.subTest(mutate=mutate):
                self.assert_invalid(manifest_change=mutate)

    def test_unknown_or_missing_nested_field_fails(self) -> None:
        for mutate in (
            lambda raw: raw["quotas"].__setitem__("per_minute", 1),
            lambda raw: raw["capabilities"].pop("point_state"),
            lambda raw: raw["permission"].pop("artifact_sha256"),
        ):
            with self.subTest(mutate=mutate):
                self.assert_invalid(manifest_change=mutate)

    def test_timestamp_requires_utc_z_suffix(self) -> None:
        invalid = (
            "2026-07-27 12:00:00Z",
            "2026-07-27T12:00Z",
            "2026-07-27T12:00:00+00:00",
            "2026-07-27T12:00:00.1Z",
        )
        for timestamp in invalid:
            with self.subTest(timestamp=timestamp):
                self.assert_invalid(
                    manifest_change=lambda raw, value=timestamp: raw.__setitem__(
                        "access_starts_at", value
                    )
                )

    def test_terms_url_requires_public_https_without_userinfo_query_or_fragment(self) -> None:
        for url in (
            "http://provider.invalid/terms",
            "https://user@provider.invalid/terms",
            "https://provider.invalid/terms?version=1",
            "https://provider.invalid/terms#saved",
            "https://provider.invalid:not-a-port/terms",
            "https://provider.invalid:443/terms",
        ):
            with self.subTest(url=url):
                self.assert_invalid(
                    manifest_change=lambda raw, value=url: raw.__setitem__(
                        "terms_url", value
                    )
                )

    def test_trial_terms_and_written_permission_are_discriminated_and_pinned(self) -> None:
        loaded = self.builder.load(self.builder.build())
        self.assertEqual(loaded.permission.intended_use, IntendedUse.PRIVATE_PAPER_EVALUATION)
        self.assertEqual(
            set(loaded.permission.permitted_operations),
            {
                PermissionOperation.PROVIDER_INGEST,
                PermissionOperation.RAW_RETENTION,
                PermissionOperation.DERIVED_SIGNALS,
            },
        )

        def written(permission):
            permission["basis"] = "written_permission"

        loaded_written = self.builder.load(
            self.builder.build(permission_change=written)
        )
        self.assertEqual(
            loaded_written.permission.basis, PermissionBasis.WRITTEN_PERMISSION
        )

    def test_permission_artifact_binds_provider_tier_entitlement_terms_use_and_dates(self) -> None:
        mutations = (
            lambda raw: raw.__setitem__("provider_id", "other"),
            lambda raw: raw.__setitem__("product_tier", "other"),
            lambda raw: raw.__setitem__("entitlement_id_sha256", "a" * 64),
            lambda raw: raw.__setitem__("terms_version", "other"),
            lambda raw: raw.__setitem__("intended_use", "publication"),
            lambda raw: raw.__setitem__("access_expires_at", "2026-08-04T12:00:00Z"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.assert_invalid(permission_change=mutate)

    def test_permission_reviewer_approval_and_review_time_are_structurally_valid(self) -> None:
        for field, value in (
            ("reviewer_id", "*"),
            ("approval_id", "contains space"),
            ("reviewed_at", "2026-07-26T12:00:00+00:00"),
        ):
            with self.subTest(field=field):
                self.assert_invalid(
                    permission_change=lambda raw, f=field, v=value: raw.__setitem__(f, v)
                )

    def test_trial_terms_cannot_grant_post_expiry_analysis_or_publication(self) -> None:
        for operation in ("post_expiry_analysis", "publication"):
            with self.subTest(operation=operation):
                self.assert_invalid(
                    permission_change=lambda raw, op=operation: raw[
                        "permitted_operations"
                    ].append(op)
                )

    def test_written_permission_grants_only_its_explicit_operations_and_windows(self) -> None:
        def permission(raw):
            raw["basis"] = "written_permission"
            raw["permitted_operations"].append("post_expiry_analysis")
            raw["analysis_expires_at"] = "2026-08-05T12:00:00Z"
            raw["raw_retention_until"] = "2026-08-05T12:00:00Z"

        def manifest(raw):
            raw["analysis_expires_at"] = "2026-08-05T12:00:00Z"
            raw["raw_retention_until"] = "2026-08-05T12:00:00Z"
            raw["max_raw_retention_seconds"] = 9 * 24 * 60 * 60

        loaded = self.builder.load(
            self.builder.build(permission_change=permission, manifest_change=manifest)
        )
        self.assertIn(
            PermissionOperation.POST_EXPIRY_ANALYSIS,
            loaded.permission.permitted_operations,
        )
        self.assertNotIn(
            PermissionOperation.PUBLICATION, loaded.permission.permitted_operations
        )

    def test_unrelated_permission_evidence_document_cannot_authorize_operations(self) -> None:
        bundle = self.builder.build()
        Path(bundle["evidence_path"]).write_bytes(b"unrelated")
        with self.assertRaises(ManifestError):
            self.builder.load(bundle)

    def test_manifest_itself_requires_expected_digest_and_repo_external_path(self) -> None:
        bundle = self.builder.build()
        with self.assertRaises(ManifestError):
            load_provider_manifest(
                bundle["manifest_path"], expected_sha256="f" * 64, repo_root=self.repo_root
            )
        inside = self.repo_root / "manifest.json"
        inside.write_bytes(Path(bundle["manifest_path"]).read_bytes())
        with self.assertRaises(ManifestError):
            load_provider_manifest(
                inside,
                expected_sha256=hashlib.sha256(inside.read_bytes()).hexdigest(),
                repo_root=self.repo_root,
            )

    def test_manifest_symlink_nonregular_oversize_and_duplicate_keys_fail(self) -> None:
        bundle = self.builder.build()
        link = self.root / "manifest-link.json"
        link.symlink_to(bundle["manifest_path"])
        with self.assertRaises(ManifestError):
            load_provider_manifest(
                link, expected_sha256=bundle["manifest_sha"], repo_root=self.repo_root
            )
        duplicate = self.root / "duplicate.json"
        duplicate.write_bytes(b'{"schema_version":1,"schema_version":1}')
        with self.assertRaises(ManifestError):
            load_provider_manifest(
                duplicate,
                expected_sha256=hashlib.sha256(duplicate.read_bytes()).hexdigest(),
                repo_root=self.repo_root,
            )
        oversize = self.root / "oversize.json"
        oversize.write_bytes(b" " * (64 * 1024 + 1))
        with self.assertRaises(ManifestError):
            load_provider_manifest(
                oversize,
                expected_sha256=hashlib.sha256(oversize.read_bytes()).hexdigest(),
                repo_root=self.repo_root,
            )

    def test_duplicate_or_invalid_credential_env_name_fails(self) -> None:
        for names in (
            ["SYNTHETIC_API_KEY", "SYNTHETIC_API_KEY"],
            ["lowercase"],
            ["1STARTS_WITH_DIGIT"],
        ):
            with self.subTest(names=names):
                self.assert_invalid(
                    manifest_change=lambda raw, value=names: raw.__setitem__(
                        "credential_env_names", value
                    )
                )

    def test_nonpositive_quota_and_invalid_sha256_fail(self) -> None:
        self.assert_invalid(
            manifest_change=lambda raw: raw["quotas"].__setitem__(
                "requests_per_rolling_second", 0
            )
        )
        self.assert_invalid(
            manifest_change=lambda raw: raw["qualification"].__setitem__(
                "artifact_sha256", "A" * 64
            )
        )

    def test_manifest_repr_contains_no_environment_values(self) -> None:
        sentinel = "credential-value-must-not-leak"
        with mock.patch.dict("os.environ", {"SYNTHETIC_API_KEY": sentinel}):
            loaded = self.builder.load(self.builder.build())
        self.assertNotIn(sentinel, repr(loaded))
        self.assertIn("SYNTHETIC_API_KEY", repr(loaded))

    def test_secret_keys_and_fixture_sentinel_never_reach_repr_errors_or_stdout(self) -> None:
        sentinel = "fixture-secret-sentinel"

        def mutate(raw):
            raw["capabilities"]["api-key"] = sentinel

        bundle = self.builder.build(manifest_change=mutate)
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(ManifestError) as raised:
            self.builder.load(bundle)
        combined = repr(raised.exception) + output.getvalue()
        self.assertNotIn(sentinel, combined)
        self.assertNotIn(str(bundle["manifest_path"]), combined)

    def test_qualification_artifact_is_external_digest_pinned_and_single_open(self) -> None:
        bundle = self.builder.build()
        original = adapter_contract.os.open if hasattr(adapter_contract, "os") else None
        with mock.patch("tennis_v1.pinned_file.os.open", wraps=__import__("os").open) as opened:
            self.builder.load(bundle)
        final = [
            call
            for call in opened.call_args_list
            if call.args and call.args[0] == Path(bundle["qualification_path"]).name
        ]
        self.assertEqual(len(final), 1)
        Path(bundle["qualification_path"]).write_text("{}", encoding="utf-8")
        with self.assertRaises(ManifestError):
            self.builder.load(bundle)

    def test_qualification_artifact_binds_provider_tier_lineage_and_adapter_code(self) -> None:
        for field in ("provider_id", "product_tier", "source_lineage_id", "adapter_code_sha256"):
            with self.subTest(field=field):
                self.assert_invalid(
                    qualification_change=lambda raw, f=field: raw.__setitem__(
                        f, "a" * 64 if f.endswith("sha256") else "other"
                    )
                )

    def test_qualification_binds_trace_protocol_issuer_approval_and_validity_fields(self) -> None:
        for field, value in (
            ("evidence_trace_sha256", "0" * 64),
            ("qualification_protocol_sha256", "0" * 64),
            ("issuer_id", "*"),
            ("approval_id", "bad id"),
            ("qualified_at", "2026-07-27T12:12:00Z"),
            ("qualified_until", "2026-09-27T12:10:00Z"),
        ):
            with self.subTest(field=field):
                self.assert_invalid(
                    qualification_change=lambda raw, f=field, v=value: raw.__setitem__(
                        f, v
                    )
                )

    def test_qualification_trace_is_external_digest_pinned_strict_and_single_open(self) -> None:
        bundle = self.builder.build()
        with mock.patch("tennis_v1.pinned_file.os.open", wraps=__import__("os").open) as opened:
            self.builder.load(bundle)
        final = [
            call
            for call in opened.call_args_list
            if call.args and call.args[0] == Path(bundle["trace_path"]).name
        ]
        self.assertEqual(len(final), 1)
        self.assert_invalid(trace_change=lambda raw: raw.__setitem__("unexpected", True))
        self.assert_invalid(
            trace_change=lambda raw: raw["matches"][0].__setitem__(
                "authorization", "secret"
            )
        )

    def test_standalone_trace_binds_code_owned_protocol_and_adapter(self) -> None:
        bundle = self.builder.build(
            trace_change=lambda raw: raw.__setitem__(
                "qualification_protocol_sha256", "0" * 64
            )
        )
        with (
            mock.patch.object(
                adapter_contract, "__file__", self.builder.adapter_file
            ),
            mock.patch.dict(
                adapter_contract._ADAPTER_REGISTRY,
                {
                    ("synthetic-provider", "trial-v1"): self.builder.registration
                },
                clear=True,
            ),
            self.assertRaises(ManifestError),
        ):
            load_qualification_trace(
                bundle["trace_path"],
                expected_sha256=bundle["trace_sha"],
                repo_root=self.repo_root,
            )

    def test_passed_trace_formats_cannot_exceed_registered_adapter_formats(self) -> None:
        def trace(raw):
            raw["matches"][0]["tested_format"] = "ndjson"

        def qualification(raw):
            raw["strata"][0]["tested_formats"] = [
                "ndjson",
                "rest_json",
            ]

        self.assert_invalid(
            trace_change=trace,
            qualification_change=qualification,
        )

    def test_qualification_summary_is_derived_exactly_from_trace_rows(self) -> None:
        for field, value in (
            ("observed_matches", 3),
            ("simultaneous_matches_tested", 1),
        ):
            with self.subTest(field=field):
                self.assert_invalid(
                    qualification_change=lambda raw, f=field, v=value: raw.__setitem__(
                        f, v
                    )
                )
        self.assert_invalid(
            qualification_change=lambda raw: raw["strata"][0].__setitem__(
                "tested_formats", ["rest_json"]
            )
        )

    def test_active_adapter_digest_is_computed_from_registered_code_closure(self) -> None:
        loaded = self.builder.load(self.builder.build())
        self.assertEqual(
            loaded.qualification.adapter_code_sha256,
            self.builder.active_adapter().adapter_code_sha256,
        )

    def test_auth_and_quota_contract_hashes_bind_manifest_and_qualification(self) -> None:
        for field in ("auth_contract_sha256", "quota_contract_sha256"):
            with self.subTest(field=field):
                self.assert_invalid(
                    qualification_change=lambda raw, f=field: raw.__setitem__(f, "0" * 64)
                )
        self.assert_invalid(
            manifest_change=lambda raw: raw.__setitem__(
                "credential_env_names", ["OTHER_API_KEY"]
            )
        )

    def test_path_or_caller_supplied_adapter_digest_cannot_override_registry(self) -> None:
        bundle = self.builder.build(
            manifest_change=lambda raw: raw.__setitem__(
                "adapter_code_sha256", "0" * 64
            )
        )
        with self.assertRaises(ManifestError):
            self.builder.load(bundle)
        with self.assertRaises(TypeError):
            load_active_adapter_contract(
                provider_id="synthetic-provider",
                product_tier="trial-v1",
                adapter_code_sha256="0" * 64,  # type: ignore[call-arg]
            )

    def test_passed_artifact_requires_time_counts_capacity_and_capabilities(self) -> None:
        mutations = (
            lambda raw: raw.__setitem__("qualified_at", None),
            lambda raw: raw.__setitem__("observed_matches", 0),
            lambda raw: raw.__setitem__("simultaneous_matches_tested", 0),
            lambda raw: raw.__setitem__("strata", []),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.assert_invalid(qualification_change=mutate)
        self.assert_invalid(
            trace_change=lambda raw: raw["matches"][0].__setitem__(
                "tested_capabilities", list(BOOL_CAPABILITIES[:-1])
            ),
            qualification_change=lambda raw: raw["strata"][0].__setitem__(
                "tested_capabilities", list(BOOL_CAPABILITIES[:-1])
            ),
        )

    def test_per_stratum_evidence_requires_counts_formats_capabilities_and_capacity(self) -> None:
        for field, value in (
            ("observed_matches", 0),
            ("simultaneous_matches_tested", 0),
            ("tested_formats", []),
            ("tested_capabilities", []),
        ):
            with self.subTest(field=field):
                self.assert_invalid(
                    qualification_change=lambda raw, f=field, v=value: raw[
                        "strata"
                    ][0].__setitem__(f, v)
                )

    def test_structured_strata_exact_grammars_reject_wildcards_unknowns_duplicates(self) -> None:
        for field, value in (
            ("sport", "*"),
            ("tour", "unknown"),
            ("competition_tier", "Masters 1000"),
            ("match_format", "best-of-3"),
            ("round_code", ""),
        ):
            with self.subTest(field=field):
                self.assert_invalid(
                    manifest_change=lambda raw, f=field, v=value: raw[
                        "capabilities"
                    ]["declared_strata"][0].__setitem__(f, v)
                )
        self.assert_invalid(
            manifest_change=lambda raw: raw["capabilities"]["declared_strata"].append(
                dict(STRATUM)
            )
        )

    def test_documented_json_schemas_match_runtime_required_keys_and_enums(self) -> None:
        schema_root = Path(__file__).parents[2] / "tennis_v1" / "schemas"
        schemas = {
            name: json.loads((schema_root / name).read_text(encoding="utf-8"))
            for name in (
                "provider-entitlement-v1.schema.json",
                "provider-permission-v1.schema.json",
                "provider-qualification-v1.schema.json",
                "provider-qualification-trace-v1.schema.json",
            )
        }
        self.assertEqual(
            set(schemas["provider-entitlement-v1.schema.json"]["required"]),
            MANIFEST_KEYS,
        )
        self.assertEqual(
            set(schemas["provider-permission-v1.schema.json"]["required"]),
            PERMISSION_ARTIFACT_KEYS,
        )
        self.assertEqual(
            set(schemas["provider-qualification-v1.schema.json"]["required"]),
            QUALIFICATION_ARTIFACT_KEYS,
        )
        self.assertEqual(
            set(schemas["provider-qualification-trace-v1.schema.json"]["required"]),
            QUALIFICATION_TRACE_KEYS,
        )
        entitlement_properties = schemas["provider-entitlement-v1.schema.json"][
            "properties"
        ]
        self.assertEqual(
            set(entitlement_properties["capabilities"]["required"]), CAPABILITY_KEYS
        )
        self.assertEqual(
            set(
                schemas["provider-entitlement-v1.schema.json"]["$defs"]["stratum"][
                    "required"
                ]
            ),
            STRATUM_KEYS,
        )
        self.assertEqual(
            set(entitlement_properties["billing_mode"]["enum"]),
            {member.value for member in BillingMode},
        )
        permission_properties = schemas["provider-permission-v1.schema.json"][
            "properties"
        ]
        self.assertEqual(
            set(permission_properties["basis"]["enum"]),
            {member.value for member in PermissionBasis},
        )
        self.assertEqual(
            set(permission_properties["permitted_operations"]["items"]["enum"]),
            {member.value for member in PermissionOperation},
        )
        qualification_properties = schemas[
            "provider-qualification-v1.schema.json"
        ]["properties"]
        self.assertEqual(
            set(qualification_properties["status"]["enum"]),
            {member.value for member in QualificationStatus},
        )
        self.assertEqual(
            schemas["provider-permission-v1.schema.json"]["$defs"]["utc"]["pattern"],
            UTC_PATTERN,
        )
        terms_schema = entitlement_properties["terms_url"]
        self.assertEqual(terms_schema["pattern"], TERMS_URL_PATTERN)
        self.assertEqual(terms_schema["maxLength"], 2048)
        def assert_objects_closed(value):
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertIs(value.get("additionalProperties"), False)
                for nested in value.values():
                    assert_objects_closed(nested)
            elif isinstance(value, list):
                for nested in value:
                    assert_objects_closed(nested)

        for schema in schemas.values():
            assert_objects_closed(schema)

        fixture_root = Path(__file__).parent / "fixtures"
        examples = {
            name: json.loads((fixture_root / name).read_text(encoding="utf-8"))
            for name in (
                "provider_manifest_schema_example.json",
                "provider_permission_schema_example.json",
                "provider_qualification_schema_example.json",
                "provider_qualification_trace_schema_example.json",
            )
        }
        self.assertEqual(
            set(examples["provider_manifest_schema_example.json"]), MANIFEST_KEYS
        )
        self.assertEqual(
            set(examples["provider_permission_schema_example.json"]),
            PERMISSION_ARTIFACT_KEYS,
        )
        self.assertEqual(
            set(examples["provider_qualification_schema_example.json"]),
            QUALIFICATION_ARTIFACT_KEYS,
        )
        self.assertEqual(
            set(examples["provider_qualification_trace_schema_example.json"]),
            QUALIFICATION_TRACE_KEYS,
        )


class HostileDigest(str):
    def __ne__(self, other):
        return False


class ProviderDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()
        self.builder = FixtureBuilder(self.root, self.repo_root)
        self.manifest = self.builder.load(self.builder.build())
        self.now = datetime(2026, 7, 27, 13, 0, tzinfo=timezone.utc)
        self.stratum = self.manifest.capabilities.declared_strata[0]
        self.request = ResearchRequest(
            intended_use=IntendedUse.PRIVATE_PAPER_EVALUATION,
            now_utc=self.now,
            session_end_utc=self.now + timedelta(hours=1),
            required_retention_until=self.now + timedelta(hours=2),
            expiry_safety_margin_seconds=60,
            required_raw_retention_seconds=3600,
            requested_matches=2,
            required_strata=(RequestedStratum(self.stratum, 2),),
        )
        self.config = TennisV1Config(
            schema_version=1,
            state_root=self.root / "state",
            provider_manifest_path=self.root / "manifest.json",
            provider_manifest_sha256=self.manifest.source_file_sha256,
            trusted_permission_reviewer_ids=("reviewer-test",),
            trusted_qualification_issuer_ids=("issuer-test",),
            observed_pool_limit=10,
            paper_position_limit=3,
            source_file_sha256="1" * 64,
            canonical_sha256="2" * 64,
        )
        self.environ = {"SYNTHETIC_API_KEY": "fixture-secret"}

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def adapter_context(self):
        return mock.patch.multiple(
            adapter_contract,
            __file__=self.builder.adapter_file,
            _ADAPTER_REGISTRY={
                ("synthetic-provider", "trial-v1"): self.builder.registration
            },
        )

    def test_manifest_projection_rejects_subclass_before_property_dispatch(self):
        calls: list[str] = []

        class HostileProviderManifest(ProviderManifest):
            def __getattribute__(self, name):
                calls.append(name)
                if name == "schema_version":
                    return 1
                return super().__getattribute__(name)

        hostile = object.__new__(HostileProviderManifest)
        with self.assertRaisesRegex(
            ManifestError,
            r"\Acanonical_manifest: invalid_type\Z",
        ):
            canonical_manifest_sha256(hostile)
        self.assertEqual(calls, [])

    def test_nested_artifact_projections_reject_subclasses_before_dispatch(self):
        calls: list[str] = []

        class HostilePermission(PermissionArtifact):
            def __getattribute__(self, name):
                calls.append(f"permission:{name}")
                return super().__getattribute__(name)

        class HostileQualification(QualificationArtifact):
            def __getattribute__(self, name):
                calls.append(f"qualification:{name}")
                return super().__getattribute__(name)

        for candidate, projection, message in (
            (
                object.__new__(HostilePermission),
                entitlements_module._permission_projection,
                "canonical_manifest: invalid_permission_type",
            ),
            (
                object.__new__(HostileQualification),
                entitlements_module._qualification_projection,
                "canonical_manifest: invalid_qualification_type",
            ),
        ):
            with self.subTest(message=message):
                calls.clear()
                with self.assertRaisesRegex(ManifestError, rf"\A{message}\Z"):
                    projection(candidate)
                self.assertEqual(calls, [])

    def test_qualification_projection_rejects_hostile_nested_strata(self):
        calls: list[str] = []
        existing = self.manifest.qualification.strata[0]

        class HostileEvidence(QualifiedStratumEvidence):
            def __getattribute__(self, name):
                calls.append(f"evidence:{name}")
                return super().__getattribute__(name)

        class HostileStratum(CoverageStratum):
            def __getattribute__(self, name):
                calls.append(f"stratum:{name}")
                return super().__getattribute__(name)

        candidates = (
            replace(
                self.manifest.qualification,
                strata=(object.__new__(HostileEvidence),),
            ),
            replace(
                self.manifest.qualification,
                strata=(
                    replace(
                        existing,
                        stratum=object.__new__(HostileStratum),
                    ),
                ),
            ),
        )
        for candidate in candidates:
            with self.subTest(candidate_type=type(candidate.strata[0]).__name__):
                calls.clear()
                with self.assertRaisesRegex(
                    ManifestError,
                    r"\Acanonical_manifest: invalid_qualification_strata\Z",
                ):
                    entitlements_module._qualification_projection(candidate)
                self.assertEqual(calls, [])

    def test_manifest_projection_rejects_nested_projection_subclasses(self):
        calls: list[str] = []

        class HostileQuotas(ProviderQuotas):
            def __getattribute__(self, name):
                calls.append(f"quota:{name}")
                return 1

        class HostileCapabilities(ProviderCapabilities):
            def __getattribute__(self, name):
                calls.append(f"capability:{name}")
                return True

        for field_name, hostile, error_message in (
            (
                "quotas",
                object.__new__(HostileQuotas),
                "canonical_manifest: invalid_quotas_type",
            ),
            (
                "capabilities",
                object.__new__(HostileCapabilities),
                "canonical_manifest: invalid_capabilities_type",
            ),
        ):
            with self.subTest(field_name=field_name):
                calls.clear()
                candidate = replace(self.manifest, **{field_name: hostile})
                with self.assertRaisesRegex(
                    ManifestError,
                    rf"\A{error_message}\Z",
                ):
                    canonical_manifest_sha256(candidate)
                self.assertEqual(calls, [])

    def test_request_binding_rejects_decision_subclass_before_property_dispatch(self):
        calls: list[str] = []

        class HostileDecision(QualificationDecision):
            def __getattribute__(self, name):
                calls.append(name)
                if name == "eligible":
                    return True
                return super().__getattribute__(name)

        hostile = object.__new__(HostileDecision)
        with self.assertRaises(ProviderGateError) as denied:
            provider_request_binding_sha256(hostile)
        self.assertEqual(
            denied.exception.reason,
            QualificationReason.QUALIFICATION_NOT_PASSED,
        )
        self.assertEqual(calls, [])

    def test_research_request_and_binding_reject_subclasses_before_dispatch(self):
        calls: list[str] = []

        class HostileRequest(ResearchRequest):
            def __getattribute__(self, name):
                calls.append(f"request:{name}")
                return super().__getattribute__(name)

        hostile_request = object.__new__(HostileRequest)
        with self.adapter_context(), self.assertRaisesRegex(
            TypeError,
            r"\Arequest must be ResearchRequest\Z",
        ):
            evaluate_provider(
                self.config,
                self.manifest,
                hostile_request,
                environ=self.environ,
            )
        self.assertEqual(calls, [])

        class HostileBinding(QualifiedProviderBinding):
            def __getattribute__(self, name):
                calls.append(f"binding:{name}")
                return super().__getattribute__(name)

        decision = self.evaluate()
        hostile_decision = replace(
            decision,
            binding=object.__new__(HostileBinding),
        )
        calls.clear()
        with self.assertRaises(ProviderGateError) as denied:
            provider_request_binding_sha256(hostile_decision)
        self.assertEqual(
            denied.exception.reason,
            QualificationReason.QUALIFICATION_NOT_PASSED,
        )
        self.assertEqual(calls, [])

    def test_decision_method_rejects_subclass_before_property_dispatch(self):
        calls: list[str] = []

        class HostileDecision(QualificationDecision):
            def __getattribute__(self, name):
                calls.append(name)
                return super().__getattribute__(name)

        hostile = object.__new__(HostileDecision)
        with self.assertRaisesRegex(
            TypeError,
            r"\Aexact QualificationDecision required\Z",
        ):
            QualificationDecision.require_eligible(hostile)
        self.assertEqual(calls, [])

    def evaluate(self, manifest=None, request=None, config=None, environ=None):
        selected_manifest = self.manifest if manifest is None else manifest
        selected_config = self.config if config is None else config
        if config is None:
            selected_config = replace(
                selected_config,
                provider_manifest_sha256=selected_manifest.source_file_sha256,
            )
        with self.adapter_context():
            return evaluate_provider(
                selected_config,
                selected_manifest,
                self.request if request is None else request,
                environ=self.environ if environ is None else environ,
            )

    def assert_reason(self, reason, *, manifest=None, request=None, config=None, environ=None):
        decision = self.evaluate(manifest, request, config, environ)
        self.assertFalse(decision.eligible)
        self.assertIn(reason, decision.reasons)
        self.assertIsNone(decision.binding)
        self.assertIsNone(decision.provider_request_binding_sha256)
        return decision

    def gate(self, clock, *, manifest=None, environ=None):
        selected_manifest = self.manifest if manifest is None else manifest
        return ProviderGate(
            replace(
                self.config,
                provider_manifest_sha256=selected_manifest.source_file_sha256,
            ),
            selected_manifest,
            self.request,
            environ=self.environ if environ is None else environ,
            clock=clock,
        )

    def written_post_expiry_bundle(
        self,
        *,
        qualified_until: datetime,
        analysis_expires_at: datetime | None = None,
        publication: bool = False,
    ) -> FixtureBundle:
        analysis_end = (
            analysis_expires_at
            if analysis_expires_at is not None
            else self.manifest.access_expires_at + timedelta(days=2)
        )

        def permission_change(raw):
            raw["basis"] = "written_permission"
            raw["permitted_operations"].append("post_expiry_analysis")
            if publication:
                raw["permitted_operations"].append("publication")
            raw["analysis_expires_at"] = utc(analysis_end)
            raw["raw_retention_until"] = utc(analysis_end)

        def manifest_change(raw):
            raw["analysis_expires_at"] = utc(analysis_end)
            raw["raw_retention_until"] = utc(analysis_end)
            delta = analysis_end - self.manifest.access_starts_at
            raw["max_raw_retention_seconds"] = (
                delta.days * 24 * 60 * 60 + delta.seconds
            )

        return self.builder.build(
            permission_change=permission_change,
            manifest_change=manifest_change,
            qualification_change=lambda raw: raw.__setitem__(
                "qualified_until", utc(qualified_until)
            ),
        )

    def written_publication_manifest(self, *, publication: bool):
        def permission_change(raw):
            raw["basis"] = "written_permission"
            if publication:
                raw["permitted_operations"].append("publication")

        return self.builder.load(
            self.builder.build(permission_change=permission_change)
        )

    def test_paid_and_auto_renew_access_are_never_eligible(self) -> None:
        for manifest, reason in (
            (replace(self.manifest, billing_mode=BillingMode.PAID), QualificationReason.PAID_ACCESS_DISABLED),
            (replace(self.manifest, auto_renew=True), QualificationReason.AUTO_RENEW_FORBIDDEN),
        ):
            with self.subTest(reason=reason):
                self.assert_reason(reason, manifest=manifest)

    def test_not_started_expired_and_short_retention_fail(self) -> None:
        cases = (
            (
                replace(self.request, now_utc=self.manifest.access_starts_at - timedelta(seconds=1)),
                QualificationReason.ACCESS_NOT_STARTED,
            ),
            (
                replace(
                    self.request,
                    now_utc=self.manifest.access_expires_at,
                    session_end_utc=self.manifest.access_expires_at + timedelta(hours=1),
                    required_retention_until=self.manifest.access_expires_at + timedelta(hours=2),
                ),
                QualificationReason.ACCESS_EXPIRED,
            ),
            (
                replace(self.manifest, raw_retention_until=self.request.required_retention_until - timedelta(seconds=1)),
                QualificationReason.RETENTION_TOO_SHORT,
            ),
        )
        for request_or_manifest, reason in cases:
            with self.subTest(reason=reason):
                if isinstance(request_or_manifest, ResearchRequest):
                    self.assert_reason(reason, request=request_or_manifest)
                else:
                    self.assert_reason(reason, manifest=request_or_manifest)

    def test_session_end_plus_margin_must_fit_inside_access_window(self) -> None:
        request = replace(
            self.request,
            session_end_utc=self.manifest.access_expires_at - timedelta(seconds=60),
            required_retention_until=self.manifest.access_expires_at,
        )
        self.assert_reason(QualificationReason.SESSION_WINDOW_EXCEEDS_ACCESS, request=request)

    def test_required_retention_must_fit_analysis_and_raw_windows(self) -> None:
        request = replace(
            self.request,
            session_end_utc=self.manifest.analysis_expires_at - timedelta(minutes=30),
            required_retention_until=self.manifest.analysis_expires_at + timedelta(minutes=30),
        )
        decision = self.evaluate(request=request)
        self.assertIn(QualificationReason.ANALYSIS_WINDOW_INADEQUATE, decision.reasons)
        self.assertIn(QualificationReason.RETENTION_TOO_SHORT, decision.reasons)

    def test_required_retention_is_exactly_session_end_plus_required_seconds(self) -> None:
        request = replace(
            self.request,
            required_retention_until=self.request.required_retention_until + timedelta(microseconds=1),
        )
        self.assert_reason(QualificationReason.RETENTION_TOO_SHORT, request=request)

    def test_every_capture_delete_by_covers_the_session_required_horizon(self) -> None:
        manifest = replace(self.manifest, max_raw_retention_seconds=3599)
        self.assert_reason(QualificationReason.RETENTION_TOO_SHORT, manifest=manifest)

    def test_access_expiry_blocks_ingest_but_separate_analysis_permission_is_required(self) -> None:
        qualified_until = self.manifest.access_expires_at + timedelta(days=1)
        manifest = self.builder.load(
            self.written_post_expiry_bundle(
                qualified_until=qualified_until,
            )
        )
        post_expiry = self.manifest.access_expires_at + timedelta(hours=1)
        with self.adapter_context():
            gate = self.gate(lambda: post_expiry, manifest=manifest)
            self.assertTrue(gate.require_analysis().eligible)
            for method_name in (
                "require_start",
                "require_ingest",
                "require_resync",
                "require_transform",
                "require_derived_persist",
                "require_raw_persist",
            ):
                with self.subTest(method=method_name):
                    with self.assertRaises(ProviderGateError) as denied:
                        getattr(gate, method_name)()
                    self.assertEqual(
                        denied.exception.reason,
                        QualificationReason.ACCESS_EXPIRED,
                    )

    def test_analysis_fails_at_qualification_and_analysis_deadline_equality(self) -> None:
        analysis_end = self.manifest.access_expires_at + timedelta(days=2)
        qualification_end = self.manifest.access_expires_at + timedelta(days=1)
        cases = (
            (
                qualification_end,
                qualification_end,
                QualificationReason.QUALIFICATION_NOT_PASSED,
            ),
            (
                analysis_end,
                analysis_end,
                QualificationReason.ANALYSIS_EXPIRED,
            ),
        )
        for qualified_until, clock_now, reason in cases:
            with self.subTest(reason=reason):
                manifest = self.builder.load(
                    self.written_post_expiry_bundle(
                        qualified_until=qualified_until,
                        analysis_expires_at=analysis_end,
                    )
                )
                with self.adapter_context():
                    gate = self.gate(lambda: clock_now, manifest=manifest)
                    with self.assertRaises(ProviderGateError) as denied:
                        gate.require_analysis()
                self.assertEqual(denied.exception.reason, reason)

    def test_qualification_ceiling_is_analysis_and_trial_remains_access_capped(self) -> None:
        analysis_end = self.manifest.access_expires_at + timedelta(days=2)
        after_analysis = analysis_end + timedelta(seconds=1)
        with self.assertRaises(ManifestError):
            self.builder.load(
                self.written_post_expiry_bundle(
                    qualified_until=after_analysis,
                    analysis_expires_at=analysis_end,
                )
            )

        valid_manifest = self.builder.load(
            self.written_post_expiry_bundle(
                qualified_until=analysis_end,
                analysis_expires_at=analysis_end,
            )
        )
        invalid_evaluator_manifest = replace(
            valid_manifest,
            qualification=replace(
                valid_manifest.qualification,
                qualified_until=after_analysis,
            ),
        )
        self.assert_reason(
            QualificationReason.QUALIFICATION_NOT_PASSED,
            manifest=invalid_evaluator_manifest,
        )

        trial_after_access = self.manifest.access_expires_at + timedelta(seconds=1)
        with self.assertRaises(ManifestError):
            self.builder.load(
                self.builder.build(
                    qualification_change=lambda raw: raw.__setitem__(
                        "qualified_until", utc(trial_after_access)
                    )
                )
            )

    def test_thirty_day_qualification_ceiling_is_independent_of_analysis_window(self) -> None:
        qualified_at = self.manifest.qualification.qualified_at
        self.assertIsNotNone(qualified_at)
        exact_ceiling = qualified_at + timedelta(days=30)
        analysis_end = qualified_at + timedelta(days=31)
        accepted = self.builder.load(
            self.written_post_expiry_bundle(
                qualified_until=exact_ceiling,
                analysis_expires_at=analysis_end,
            )
        )
        self.assertEqual(accepted.qualification.qualified_until, exact_ceiling)

        beyond_ceiling = exact_ceiling + timedelta(seconds=1)
        with self.assertRaises(ManifestError):
            self.builder.load(
                self.written_post_expiry_bundle(
                    qualified_until=beyond_ceiling,
                    analysis_expires_at=analysis_end,
                )
            )
        self.assert_reason(
            QualificationReason.QUALIFICATION_NOT_PASSED,
            manifest=replace(
                accepted,
                qualification=replace(
                    accepted.qualification,
                    qualified_until=beyond_ceiling,
                ),
            ),
        )

    def test_every_gate_method_samples_clock_and_denies_at_qualified_until(self) -> None:
        qualified_until = self.manifest.qualification.qualified_until
        self.assertIsNotNone(qualified_until)
        methods = (
            "require_start",
            "require_ingest",
            "require_resync",
            "require_transform",
            "require_derived_persist",
            "require_raw_persist",
            "require_analysis",
            "require_export",
            "seconds_until_access_expiry",
        )
        for method_name in methods:
            samples = []

            def clock():
                samples.append(qualified_until)
                return qualified_until

            with self.subTest(method=method_name), self.adapter_context():
                gate = self.gate(clock)
                with self.assertRaises(ProviderGateError) as denied:
                    getattr(gate, method_name)()
                self.assertEqual(
                    denied.exception.reason,
                    QualificationReason.QUALIFICATION_NOT_PASSED,
                )
                self.assertEqual(samples, [qualified_until])

    def test_every_gate_method_rejects_runtime_binding_substitution(self) -> None:
        methods = (
            "require_start",
            "require_ingest",
            "require_resync",
            "require_transform",
            "require_derived_persist",
            "require_raw_persist",
            "require_analysis",
            "require_export",
            "seconds_until_access_expiry",
        )
        for field_name in ("source_file_sha256", "canonical_sha256"):
            for method_name in methods:
                with (
                    self.subTest(field=field_name, method=method_name),
                    self.adapter_context(),
                ):
                    gate = self.gate(lambda: self.now)
                    gate._manifest = replace(
                        gate._manifest,
                        **{field_name: "e" * 64},
                    )
                    with self.assertRaises(ProviderGateError) as denied:
                        getattr(gate, method_name)()
                    self.assertEqual(
                        denied.exception.reason,
                        QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
                    )
                    self.assertNotIn("fixture-secret", str(denied.exception))

    def test_post_expiry_analysis_and_export_reject_runtime_identity_substitution(
        self,
    ) -> None:
        manifest = self.builder.load(
            self.written_post_expiry_bundle(
                qualified_until=self.manifest.access_expires_at
                + timedelta(days=1),
                publication=True,
            )
        )
        post_expiry = self.manifest.access_expires_at + timedelta(hours=1)

        with self.adapter_context():
            unchanged = self.gate(lambda: post_expiry, manifest=manifest)
            self.assertTrue(unchanged.require_analysis().eligible)
            self.assertTrue(unchanged.require_export().export_allowed)

        for mutation in (
            "request_sha256",
            "manifest_file_sha256",
            "manifest_canonical_sha256",
        ):
            for method_name in ("require_analysis", "require_export"):
                with (
                    self.subTest(mutation=mutation, method=method_name),
                    self.adapter_context(),
                ):
                    gate = self.gate(lambda: post_expiry, manifest=manifest)
                    if mutation == "request_sha256":
                        gate._request = replace(
                            gate._request,
                            expiry_safety_margin_seconds=61,
                        )
                    elif mutation == "manifest_file_sha256":
                        gate._manifest = replace(
                            gate._manifest,
                            source_file_sha256="e" * 64,
                        )
                    else:
                        gate._manifest = replace(
                            gate._manifest,
                            canonical_sha256="e" * 64,
                        )
                    with self.assertRaises(ProviderGateError) as denied:
                        getattr(gate, method_name)()
                    self.assertEqual(
                        denied.exception.reason,
                        QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
                    )

    def test_post_expiry_digest_checks_reject_hostile_str_subclasses(self) -> None:
        manifest = self.builder.load(
            self.written_post_expiry_bundle(
                qualified_until=self.manifest.access_expires_at
                + timedelta(days=1),
                publication=True,
            )
        )
        post_expiry = self.manifest.access_expires_at + timedelta(hours=1)
        for field_name in ("source_file_sha256", "canonical_sha256"):
            for method_name in ("require_analysis", "require_export"):
                with (
                    self.subTest(field=field_name, method=method_name),
                    self.adapter_context(),
                ):
                    gate = self.gate(lambda: post_expiry, manifest=manifest)
                    gate._manifest = replace(
                        gate._manifest,
                        **{field_name: HostileDigest("e" * 64)},
                    )
                    with self.assertRaises(ProviderGateError) as denied:
                        getattr(gate, method_name)()
                    self.assertEqual(
                        denied.exception.reason,
                        QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
                    )

    def test_digest_comparison_errors_fail_closed_and_preserve_process_control(
        self,
    ) -> None:
        secret = "digest-comparison-secret-must-not-escape"
        output = StringIO()
        with (
            mock.patch(
                "tennis_v1.entitlements.hmac.compare_digest",
                side_effect=RuntimeError(secret),
            ),
            redirect_stdout(output),
        ):
            try:
                decision = self.evaluate()
            except Exception as error:
                self.fail(
                    "ordinary evaluator digest-comparison error escaped as "
                    f"{type(error).__name__}"
                )
        self.assertFalse(decision.eligible)
        self.assertEqual(
            decision.reasons,
            (QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,),
        )
        self.assertIsNone(decision.binding)
        self.assertNotIn(secret, repr(decision) + output.getvalue())

        with self.adapter_context():
            gate = self.gate(lambda: self.now)
            denied_error = None
            with (
                mock.patch(
                    "tennis_v1.entitlements.hmac.compare_digest",
                    side_effect=RuntimeError(secret),
                ),
                redirect_stdout(output),
            ):
                try:
                    gate.require_start()
                except ProviderGateError as error:
                    denied_error = error
                except Exception as error:
                    self.fail(
                        "ordinary gate digest-comparison error escaped as "
                        f"{type(error).__name__}"
                    )
                else:
                    self.fail("ordinary gate digest-comparison error was accepted")
        self.assertIsNotNone(denied_error)
        self.assertEqual(
            denied_error.reason,
            QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
        )
        self.assertNotIn(
            secret,
            repr(denied_error) + str(denied_error) + output.getvalue(),
        )

        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception=exception_type):
                with (
                    mock.patch(
                        "tennis_v1.entitlements.hmac.compare_digest",
                        side_effect=exception_type(),
                    ),
                    self.assertRaises(exception_type),
                ):
                    self.evaluate()
                with self.adapter_context():
                    gate = self.gate(lambda: self.now)
                    with (
                        mock.patch(
                            "tennis_v1.entitlements.hmac.compare_digest",
                            side_effect=exception_type(),
                        ),
                        self.assertRaises(exception_type),
                    ):
                        gate.require_start()

    def test_evaluator_requires_exact_config_manifest_digest_pin(self) -> None:
        original = self.manifest.source_file_sha256
        cases = (
            (
                "ordinary_mismatch",
                replace(self.config, provider_manifest_sha256="e" * 64),
                self.manifest,
            ),
            (
                "hostile_config_mismatch",
                replace(
                    self.config,
                    provider_manifest_sha256=HostileDigest("e" * 64),
                ),
                self.manifest,
            ),
            (
                "hostile_manifest_mismatch",
                self.config,
                replace(
                    self.manifest,
                    source_file_sha256=HostileDigest("e" * 64),
                ),
            ),
            (
                "matching_hostile_subclasses",
                replace(
                    self.config,
                    provider_manifest_sha256=HostileDigest(original),
                ),
                replace(
                    self.manifest,
                    source_file_sha256=HostileDigest(original),
                ),
            ),
            (
                "matching_uppercase",
                replace(self.config, provider_manifest_sha256="E" * 64),
                replace(self.manifest, source_file_sha256="E" * 64),
            ),
            (
                "matching_wrong_length",
                replace(self.config, provider_manifest_sha256="e" * 63),
                replace(self.manifest, source_file_sha256="e" * 63),
            ),
        )
        for name, config, manifest in cases:
            with self.subTest(case=name):
                decision = self.evaluate(config=config, manifest=manifest)
                self.assertFalse(decision.eligible)
                self.assertEqual(
                    decision.reasons,
                    (QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,),
                )
                self.assertIsNone(decision.binding)
                self.assertIsNone(decision.provider_request_binding_sha256)

    def test_gate_start_requires_config_manifest_digest_pin(self) -> None:
        cases = (
            (
                "ordinary_mismatch",
                replace(self.config, provider_manifest_sha256="e" * 64),
                self.manifest,
            ),
            (
                "hostile_config_mismatch",
                replace(
                    self.config,
                    provider_manifest_sha256=HostileDigest("e" * 64),
                ),
                self.manifest,
            ),
            (
                "hostile_manifest_mismatch",
                self.config,
                replace(
                    self.manifest,
                    source_file_sha256=HostileDigest("e" * 64),
                ),
            ),
        )
        for name, config, manifest in cases:
            with self.subTest(case=name), self.adapter_context():
                gate = ProviderGate(
                    config,
                    manifest,
                    self.request,
                    environ=self.environ,
                    clock=lambda: self.now,
                )
                with self.assertRaises(ProviderGateError) as denied:
                    gate.require_start()
                self.assertEqual(
                    denied.exception.reason,
                    QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
                )

    def test_gate_rejects_runtime_request_and_registry_substitution(self) -> None:
        with self.adapter_context():
            gate = self.gate(lambda: self.now)
            gate._request = replace(
                gate._request,
                expiry_safety_margin_seconds=61,
            )
            with self.assertRaises(ProviderGateError) as request_denied:
                gate.require_start()
        self.assertEqual(
            request_denied.exception.reason,
            QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
        )

        with self.adapter_context():
            gate = self.gate(lambda: self.now)
            adapter_contract._ADAPTER_REGISTRY.clear()
            with self.assertRaises(ProviderGateError) as registry_denied:
                gate.require_start()
        self.assertEqual(
            registry_denied.exception.reason,
            QualificationReason.ADAPTER_MISMATCH,
        )

    def test_permission_drift_cannot_revoke_or_expand_export_authority(self) -> None:
        authorized = self.written_publication_manifest(publication=True)
        with self.adapter_context():
            authorized_gate = self.gate(lambda: self.now, manifest=authorized)
            self.assertTrue(authorized_gate.require_export().export_allowed)
            authorized_gate._manifest = replace(
                authorized_gate._manifest,
                permission=replace(
                    authorized_gate._manifest.permission,
                    permitted_operations=tuple(
                        operation
                        for operation in authorized_gate._manifest.permission.permitted_operations
                        if operation is not PermissionOperation.PUBLICATION
                    ),
                ),
            )
            with self.assertRaises(ProviderGateError) as revoked:
                authorized_gate.require_export()
        self.assertEqual(
            revoked.exception.reason,
            QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
        )

        private = self.written_publication_manifest(publication=False)
        with self.adapter_context():
            private_gate = self.gate(lambda: self.now, manifest=private)
            self.assertTrue(private_gate.require_analysis().eligible)
            with self.assertRaises(ProviderGateError) as unchanged_private:
                private_gate.require_export()
            self.assertEqual(
                unchanged_private.exception.reason,
                QualificationReason.MANDATORY_PERMISSION_MISSING,
            )
            private_gate._manifest = replace(
                private_gate._manifest,
                permission=replace(
                    private_gate._manifest.permission,
                    permitted_operations=private_gate._manifest.permission.permitted_operations
                    + (PermissionOperation.PUBLICATION,),
                ),
            )
            with self.assertRaises(ProviderGateError) as granted:
                private_gate.require_export()
        self.assertEqual(
            granted.exception.reason,
            QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
        )

    def test_every_gate_method_rejects_canonical_semantic_drift(self) -> None:
        methods = (
            "require_start",
            "require_ingest",
            "require_resync",
            "require_transform",
            "require_derived_persist",
            "require_raw_persist",
            "require_analysis",
            "require_export",
            "seconds_until_access_expiry",
        )
        for method_name in methods:
            with self.subTest(method=method_name), self.adapter_context():
                gate = self.gate(lambda: self.now)
                gate._manifest = replace(
                    gate._manifest,
                    terms_url="https://provider.invalid/terms/semantic-drift",
                )
                with self.assertRaises(ProviderGateError) as denied:
                    getattr(gate, method_name)()
                self.assertEqual(
                    denied.exception.reason,
                    QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
                )

    def test_evaluator_rejects_canonical_semantic_drift_before_binding(self) -> None:
        drifted = replace(
            self.manifest,
            terms_url="https://provider.invalid/terms/direct-evaluator-drift",
        )
        decision = self.evaluate(manifest=drifted)
        self.assertFalse(decision.eligible)
        self.assertEqual(
            decision.reasons,
            (QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,),
        )
        self.assertIsNone(decision.binding)
        self.assertIsNone(decision.provider_request_binding_sha256)
        with self.assertRaises(ProviderGateError) as denied:
            decision.require_eligible()
        self.assertEqual(
            denied.exception.reason,
            QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
        )

    def test_gate_first_call_rejects_manifest_drifted_before_construction(
        self,
    ) -> None:
        drifted = replace(
            self.manifest,
            terms_url="https://provider.invalid/terms/preconstruction-drift",
        )
        with self.adapter_context():
            gate = self.gate(lambda: self.now, manifest=drifted)
            with self.assertRaises(ProviderGateError) as denied:
                gate.require_start()
        self.assertEqual(
            denied.exception.reason,
            QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
        )

    def test_evaluator_redacts_canonical_recomputation_errors_and_preserves_process_control(
        self,
    ) -> None:
        secret = "evaluator-canonical-secret-must-not-escape"
        output = StringIO()
        with (
            mock.patch(
                "tennis_v1.entitlements.canonical_manifest_sha256",
                side_effect=RuntimeError(secret),
            ),
            redirect_stdout(output),
        ):
            decision = self.evaluate()
        self.assertFalse(decision.eligible)
        self.assertEqual(
            decision.reasons,
            (QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,),
        )
        self.assertIsNone(decision.binding)
        self.assertIsNone(decision.provider_request_binding_sha256)
        with self.assertRaises(ProviderGateError) as denied:
            decision.require_eligible()
        self.assertEqual(
            denied.exception.reason,
            QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
        )
        self.assertNotIn(
            secret,
            repr(decision)
            + repr(denied.exception)
            + str(denied.exception)
            + output.getvalue(),
        )

        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception=exception_type):
                with (
                    mock.patch(
                        "tennis_v1.entitlements.canonical_manifest_sha256",
                        side_effect=exception_type(),
                    ),
                    self.assertRaises(exception_type),
                ):
                    self.evaluate()

    def test_gate_redacts_canonical_recomputation_errors_and_preserves_process_control(self) -> None:
        secret = "canonical-error-secret-must-not-escape"
        output = StringIO()
        with self.adapter_context():
            gate = self.gate(lambda: self.now)
            with (
                mock.patch(
                    "tennis_v1.entitlements.canonical_manifest_sha256",
                    side_effect=RuntimeError(secret),
                ),
                redirect_stdout(output),
                self.assertRaises(ProviderGateError) as denied,
            ):
                gate.require_analysis()
        self.assertEqual(
            denied.exception.reason,
            QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
        )
        self.assertNotIn(
            secret,
            repr(denied.exception) + str(denied.exception) + output.getvalue(),
        )

        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception=exception_type), self.adapter_context():
                gate = self.gate(lambda: self.now)
                with (
                    mock.patch(
                        "tennis_v1.entitlements.canonical_manifest_sha256",
                        side_effect=exception_type(),
                    ),
                    self.assertRaises(exception_type),
                ):
                    gate.require_analysis()

    def test_credential_boundary_rejects_hostile_values_and_mapping_errors(self) -> None:
        secret = "hostile-secret-must-not-escape"
        calls: list[str] = []

        class ForgedBlank(str):
            def strip(self, *args, **kwargs):
                calls.append("strip")
                return "forged-nonblank"

        output = StringIO()
        with (
            redirect_stdout(output),
            self.assertRaisesRegex(
                TypeError,
                r"\Aenviron: exact_dict_of_exact_str_required\Z",
            ) as forged_denied,
        ):
            self.evaluate(
                environ={"SYNTHETIC_API_KEY": ForgedBlank(secret)}
            )
        self.assertEqual(calls, [])
        self.assertNotIn(
            secret,
            repr(forged_denied.exception)
            + str(forged_denied.exception)
            + output.getvalue(),
        )

        class ExplodingMapping(dict):
            def get(self, key, default=None):
                calls.append("get")
                raise RuntimeError(secret)

        calls.clear()
        with self.assertRaisesRegex(
            TypeError,
            r"\Aenviron: exact_dict_of_exact_str_required\Z",
        ):
            self.evaluate(environ=ExplodingMapping())
        self.assertEqual(calls, [])

    def test_environment_boundary_rejects_nonexact_dict_without_dispatch(self) -> None:
        calls: list[str] = []

        class HostileMapping(Mapping):
            def __getitem__(self, key):
                calls.append("__getitem__")
                return "fixture-secret"

            def __iter__(self):
                calls.append("__iter__")
                return iter(("SYNTHETIC_API_KEY",))

            def __len__(self):
                calls.append("__len__")
                return 1

            def get(self, key, default=None):
                calls.append("get")
                return "fixture-secret"

        class HostileDict(dict):
            def __iter__(self):
                calls.append("dict.__iter__")
                return super().__iter__()

            def __getitem__(self, key):
                calls.append("dict.__getitem__")
                return super().__getitem__(key)

            def items(self):
                calls.append("dict.items")
                return super().items()

            def get(self, key, default=None):
                calls.append("dict.get")
                return super().get(key, default)

        for environ in (
            HostileMapping(),
            HostileDict(SYNTHETIC_API_KEY="fixture-secret"),
        ):
            with self.subTest(environment_type=type(environ).__name__):
                calls.clear()
                with self.assertRaisesRegex(
                    TypeError,
                    r"\Aenviron: exact_dict_of_exact_str_required\Z",
                ):
                    self.evaluate(environ=environ)
                self.assertEqual(calls, [])

    def test_environment_snapshot_rejects_nonexact_strings_without_dispatch(self) -> None:
        calls: list[str] = []

        class HostileString(str):
            def __str__(self):
                calls.append("__str__")
                return super().__str__()

            def __repr__(self):
                calls.append("__repr__")
                return super().__repr__()

            def __hash__(self):
                calls.append("__hash__")
                return super().__hash__()

            def __eq__(self, other):
                calls.append("__eq__")
                return super().__eq__(other)

            def strip(self, *args, **kwargs):
                calls.append("strip")
                return super().strip(*args, **kwargs)

        hostile_key = HostileString("SYNTHETIC_API_KEY")
        key_environment = {hostile_key: "fixture-secret"}
        calls.clear()
        with self.assertRaisesRegex(
            TypeError,
            r"\Aenviron: exact_dict_of_exact_str_required\Z",
        ):
            self.evaluate(environ=key_environment)
        self.assertEqual(calls, [])

        hostile_value = HostileString("fixture-secret")
        value_environment = {"SYNTHETIC_API_KEY": hostile_value}
        calls.clear()
        with self.assertRaisesRegex(
            TypeError,
            r"\Aenviron: exact_dict_of_exact_str_required\Z",
        ):
            self.evaluate(environ=value_environment)
        self.assertEqual(calls, [])

    def test_provider_gate_revalidates_live_exact_environment_snapshot(self) -> None:
        live_environment = {"SYNTHETIC_API_KEY": "fixture-secret"}
        with self.adapter_context():
            gate = self.gate(lambda: self.now, environ=live_environment)
            gate.require_start()

            live_environment.pop("SYNTHETIC_API_KEY")
            with self.assertRaises(ProviderGateError) as removed:
                gate.require_ingest()
            self.assertEqual(
                removed.exception.reason,
                QualificationReason.CREDENTIAL_MISSING,
            )

            live_environment["SYNTHETIC_API_KEY"] = " "
            with self.assertRaises(ProviderGateError) as blanked:
                gate.require_ingest()
            self.assertEqual(
                blanked.exception.reason,
                QualificationReason.CREDENTIAL_MISSING,
            )

    def test_direct_evaluation_isolated_from_caller_mutation_after_snapshot(self) -> None:
        import tennis_v1.entitlements as entitlements_module

        original = entitlements_module._evaluate_provider_as_of
        snapshot_ready = threading.Event()
        continue_evaluation = threading.Event()
        results: list[QualificationDecision] = []
        failures: list[BaseException] = []
        live_environment = {"SYNTHETIC_API_KEY": "fixture-secret"}

        def pause_after_snapshot(*args, environ, as_of):
            self.assertIs(type(environ), dict)
            self.assertIsNot(environ, live_environment)
            snapshot_ready.set()
            if not continue_evaluation.wait(2.0):
                raise AssertionError("evaluation_snapshot_release_timeout")
            return original(*args, environ=environ, as_of=as_of)

        def evaluate_in_thread() -> None:
            try:
                results.append(self.evaluate(environ=live_environment))
            except BaseException as error:
                failures.append(error)

        with mock.patch.object(
            entitlements_module,
            "_evaluate_provider_as_of",
            side_effect=pause_after_snapshot,
        ):
            worker = threading.Thread(target=evaluate_in_thread)
            worker.start()
            self.assertTrue(snapshot_ready.wait(2.0))
            live_environment["SYNTHETIC_API_KEY"] = " "
            continue_evaluation.set()
            worker.join(2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].eligible)
        self.assertIn(
            QualificationReason.CREDENTIAL_MISSING,
            self.evaluate(environ=live_environment).reasons,
        )

    def test_provider_gate_rejects_hostile_environment_before_dispatch(self) -> None:
        calls: list[str] = []
        secret = "hostile-gate-secret-must-not-escape"

        class HostileDict(dict):
            def get(self, key, default=None):
                calls.append("get")
                return super().get(key, default)

            def items(self):
                calls.append("items")
                return super().items()

            def __iter__(self):
                calls.append("__iter__")
                return super().__iter__()

        class HostileMapping(Mapping):
            def __getitem__(self, key):
                calls.append("mapping.__getitem__")
                return secret

            def __iter__(self):
                calls.append("mapping.__iter__")
                return iter(("SYNTHETIC_API_KEY",))

            def __len__(self):
                calls.append("mapping.__len__")
                return 1

        class HostileString(str):
            def __str__(self):
                calls.append("string.__str__")
                return super().__str__()

            def __repr__(self):
                calls.append("string.__repr__")
                return super().__repr__()

            def strip(self, *args, **kwargs):
                calls.append("string.strip")
                return super().strip(*args, **kwargs)

        hostile_key = HostileString("SYNTHETIC_API_KEY")
        candidates = (
            HostileMapping(),
            HostileDict(SYNTHETIC_API_KEY=secret),
            {hostile_key: secret},
            {"SYNTHETIC_API_KEY": HostileString(secret)},
        )
        calls.clear()
        for hostile in candidates:
            with self.subTest(environment_type=type(hostile).__name__):
                calls.clear()
                with (
                    self.adapter_context(),
                    self.assertRaisesRegex(
                        TypeError,
                        r"\Aenviron: exact_dict_of_exact_str_required\Z",
                    ) as denied,
                ):
                    self.gate(lambda: self.now, environ=hostile)
                self.assertEqual(calls, [])
                self.assertNotIn(
                    secret,
                    repr(denied.exception) + str(denied.exception),
                )

        live_environment = {"SYNTHETIC_API_KEY": "fixture-secret"}
        with self.adapter_context():
            gate = self.gate(lambda: self.now, environ=live_environment)
            gate.require_start()
            live_environment["SYNTHETIC_API_KEY"] = HostileString(secret)
            calls.clear()
            with self.assertRaisesRegex(
                TypeError,
                r"\Aenviron: exact_dict_of_exact_str_required\Z",
            ) as denied:
                gate.require_ingest()
        self.assertEqual(calls, [])
        self.assertNotIn(
            secret,
            repr(denied.exception) + str(denied.exception),
        )

    def test_each_mandatory_permission_fails_when_absent_or_unauthorized(self) -> None:
        for operation in (
            PermissionOperation.PROVIDER_INGEST,
            PermissionOperation.RAW_RETENTION,
            PermissionOperation.DERIVED_SIGNALS,
        ):
            permission = replace(
                self.manifest.permission,
                permitted_operations=tuple(
                    item
                    for item in self.manifest.permission.permitted_operations
                    if item is not operation
                ),
            )
            with self.subTest(operation=operation):
                self.assert_reason(
                    QualificationReason.MANDATORY_PERMISSION_MISSING,
                    manifest=replace(self.manifest, permission=permission),
                )
        unauthorized_trial = replace(
            self.manifest.permission,
            permitted_operations=self.manifest.permission.permitted_operations
            + (PermissionOperation.PUBLICATION,),
        )
        self.assert_reason(
            QualificationReason.MANDATORY_PERMISSION_MISSING,
            manifest=replace(self.manifest, permission=unauthorized_trial),
        )

    def test_publication_denied_allows_private_research_but_blocks_export(self) -> None:
        decision = self.evaluate()
        self.assertTrue(decision.eligible)
        self.assertFalse(decision.export_allowed)
        with self.adapter_context():
            gate = self.gate(lambda: self.now)
            gate.require_analysis()
            with self.assertRaises(ProviderGateError) as raised:
                gate.require_export()
        self.assertEqual(
            raised.exception.reason, QualificationReason.MANDATORY_PERMISSION_MISSING
        )

    def test_missing_or_blank_credential_fails_without_echoing_value(self) -> None:
        secret = "never-echo-this-value"
        for environ in ({}, {"SYNTHETIC_API_KEY": " \t"}, {"SYNTHETIC_API_KEY": secret}):
            with self.subTest(environ=environ):
                if environ.get("SYNTHETIC_API_KEY") == secret:
                    decision = self.evaluate(environ=environ)
                    self.assertTrue(decision.eligible)
                    continue
                decision = self.assert_reason(
                    QualificationReason.CREDENTIAL_MISSING, environ=environ
                )
                with self.assertRaises(ProviderGateError) as raised:
                    decision.require_eligible()
                self.assertNotIn(secret, str(raised.exception))
        live_environ = {"SYNTHETIC_API_KEY": secret}
        with self.adapter_context():
            gate = self.gate(lambda: self.now, environ=live_environ)
            gate.require_start()
            live_environ["SYNTHETIC_API_KEY"] = " "
            with self.assertRaises(ProviderGateError) as raised:
                gate.require_ingest()
        self.assertEqual(
            raised.exception.reason, QualificationReason.CREDENTIAL_MISSING
        )
        self.assertNotIn(secret, str(raised.exception))

    def test_path_or_arbitrary_manifest_env_name_cannot_satisfy_adapter_auth(self) -> None:
        manifest = replace(self.manifest, credential_env_names=("HOME",))
        decision = self.assert_reason(
            QualificationReason.ADAPTER_MISMATCH,
            manifest=manifest,
            environ={"HOME": "/sensitive/path"},
        )
        with self.assertRaises(ProviderGateError) as raised:
            decision.require_eligible()
        self.assertNotIn("/sensitive/path", str(raised.exception))

    def test_requested_and_qualification_capacity_must_cover_pool(self) -> None:
        request = replace(
            self.request,
            requested_matches=3,
            required_strata=(RequestedStratum(self.stratum, 3),),
        )
        decision = self.evaluate(request=request)
        self.assertIn(QualificationReason.QUALIFICATION_CAPACITY_INADEQUATE, decision.reasons)
        self.assertIn(QualificationReason.STRATUM_NOT_QUALIFIED, decision.reasons)

    def test_quota_demand_is_derived_from_duration_pool_and_adapter_usage_plan(self) -> None:
        manifest = replace(
            self.manifest,
            quotas=replace(self.manifest.quotas, requests_per_utc_calendar_day=188),
        )
        self.assert_reason(QualificationReason.QUOTA_INADEQUATE, manifest=manifest)

    def test_each_exact_rolling_calendar_connection_subscription_and_resync_quota_is_enforced(self) -> None:
        required = {
            "requests_per_rolling_60_seconds": 12,
            "requests_per_utc_calendar_day": 189,
            "requests_per_rolling_second": 12,
            "max_connections": 1,
            "max_subscriptions": 2,
            "resync_requests_per_rolling_hour": 4,
        }
        for name, value in required.items():
            quotas = replace(self.manifest.quotas, **{name: value - 1})
            with self.subTest(name=name):
                self.assert_reason(
                    QualificationReason.QUOTA_INADEQUATE,
                    manifest=replace(self.manifest, quotas=quotas),
                )

    def test_every_causal_capability_is_mandatory(self) -> None:
        for name in BOOL_CAPABILITIES:
            with self.subTest(name=name):
                capabilities = replace(self.manifest.capabilities, **{name: False})
                self.assert_reason(
                    QualificationReason.CAPABILITY_MISSING,
                    manifest=replace(self.manifest, capabilities=capabilities),
                )

    def test_qualification_must_pass_and_bind_active_adapter_code(self) -> None:
        failed = replace(
            self.manifest.qualification,
            status=QualificationStatus.FAILED,
            qualified_at=None,
            qualified_until=None,
        )
        self.assert_reason(
            QualificationReason.QUALIFICATION_NOT_PASSED,
            manifest=replace(self.manifest, qualification=failed),
        )
        mismatch = replace(
            self.manifest.qualification, adapter_code_sha256="0" * 64
        )
        self.assert_reason(
            QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
            manifest=replace(self.manifest, qualification=mismatch),
        )
        for qualification in (
            replace(
                self.manifest.qualification,
                qualification_protocol_sha256="0" * 64,
            ),
            replace(
                self.manifest.qualification,
                provider_id="another-provider",
            ),
        ):
            with self.subTest(qualification=qualification):
                self.assert_reason(
                    QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
                    manifest=replace(self.manifest, qualification=qualification),
                )

    def test_evaluator_rejects_untrusted_permission_reviewer_and_future_review(self) -> None:
        config = replace(
            self.config, trusted_permission_reviewer_ids=("another-reviewer",)
        )
        self.assert_reason(
            QualificationReason.MANDATORY_PERMISSION_MISSING, config=config
        )
        permission = replace(
            self.manifest.permission, reviewed_at=self.now + timedelta(seconds=1)
        )
        self.assert_reason(
            QualificationReason.MANDATORY_PERMISSION_MISSING,
            manifest=replace(self.manifest, permission=permission),
        )

    def test_evaluator_rejects_untrusted_issuer_future_issue_and_expired_qualification(self) -> None:
        untrusted = replace(
            self.config, trusted_qualification_issuer_ids=("another-issuer",)
        )
        self.assert_reason(
            QualificationReason.QUALIFICATION_NOT_PASSED, config=untrusted
        )
        for qualification in (
            replace(
                self.manifest.qualification,
                issued_at=self.now + timedelta(seconds=1),
            ),
            replace(
                self.manifest.qualification,
                qualified_until=self.now,
            ),
        ):
            with self.subTest(qualification=qualification):
                self.assert_reason(
                    QualificationReason.QUALIFICATION_NOT_PASSED,
                    manifest=replace(self.manifest, qualification=qualification),
                )

    def test_provider_request_binding_hash_covers_every_binding_and_request_field(self) -> None:
        decision = self.evaluate()
        self.assertTrue(decision.eligible)
        self.assertEqual(
            set(type(decision.binding).__dataclass_fields__),
            {
                "provider_id",
                "product_tier",
                "source_lineage_id",
                "entitlement_id_sha256",
                "manifest_file_sha256",
                "manifest_canonical_sha256",
                "qualification_artifact_sha256",
                "permission_artifact_sha256",
                "qualification_trace_sha256",
                "adapter_code_sha256",
                "auth_contract_sha256",
                "quota_contract_sha256",
                "session_end_utc",
                "required_retention_until",
                "access_expires_at",
                "analysis_expires_at",
                "qualified_until",
            },
        )
        original = provider_request_binding_sha256(decision)
        expected_binding = {
            "provider_id": decision.binding.provider_id,
            "product_tier": decision.binding.product_tier,
            "source_lineage_id": decision.binding.source_lineage_id,
            "entitlement_id_sha256": decision.binding.entitlement_id_sha256,
            "manifest_file_sha256": decision.binding.manifest_file_sha256,
            "manifest_canonical_sha256": decision.binding.manifest_canonical_sha256,
            "qualification_artifact_sha256": decision.binding.qualification_artifact_sha256,
            "permission_artifact_sha256": decision.binding.permission_artifact_sha256,
            "qualification_trace_sha256": decision.binding.qualification_trace_sha256,
            "adapter_code_sha256": decision.binding.adapter_code_sha256,
            "auth_contract_sha256": decision.binding.auth_contract_sha256,
            "quota_contract_sha256": decision.binding.quota_contract_sha256,
            "session_end_utc": utc(decision.binding.session_end_utc),
            "required_retention_until": utc(decision.binding.required_retention_until),
            "access_expires_at": utc(decision.binding.access_expires_at),
            "analysis_expires_at": utc(decision.binding.analysis_expires_at),
            "qualified_until": utc(decision.binding.qualified_until),
        }
        expected = hashlib.sha256(
            b"INCI-PROVIDER-REQUEST-BINDING-V1\0"
            + canonical_json_bytes(
                {
                    "request_sha256": decision.request_sha256,
                    "binding": expected_binding,
                }
            )
        ).hexdigest()
        self.assertEqual(original, expected)
        for field in fields(decision.binding):
            value = getattr(decision.binding, field.name)
            changed = (
                value + timedelta(microseconds=1)
                if isinstance(value, datetime)
                else ("f" * 64 if isinstance(value, str) and len(value) == 64 else value + "-changed")
            )
            mutated = replace(
                decision, binding=replace(decision.binding, **{field.name: changed})
            )
            with self.subTest(field=field.name):
                self.assertNotEqual(provider_request_binding_sha256(mutated), original)

        request_mutations = (
            replace(self.request, intended_use=object()),
            replace(self.request, now_utc=self.request.now_utc + timedelta(seconds=1)),
            replace(
                self.request,
                session_end_utc=self.request.session_end_utc + timedelta(seconds=1),
            ),
            replace(
                self.request,
                required_retention_until=self.request.required_retention_until
                + timedelta(seconds=1),
            ),
            replace(self.request, expiry_safety_margin_seconds=61),
            replace(self.request, required_raw_retention_seconds=3601),
            replace(self.request, requested_matches=1),
            replace(
                self.request,
                required_strata=(RequestedStratum(self.stratum, 1),),
            ),
        )
        for mutation in request_mutations:
            with self.subTest(request=mutation):
                self.assertNotEqual(
                    self.evaluate(request=mutation).request_sha256,
                    decision.request_sha256,
                )

        other = replace(self.stratum, round_code="QF")
        first = replace(
            self.request,
            required_strata=(
                RequestedStratum(self.stratum, 1),
                RequestedStratum(other, 1),
            ),
        )
        reversed_order = replace(first, required_strata=tuple(reversed(first.required_strata)))
        self.assertEqual(
            self.evaluate(request=first).request_sha256,
            self.evaluate(request=reversed_order).request_sha256,
        )

    def test_structured_strata_and_format_must_be_declared_and_tested(self) -> None:
        other = replace(self.stratum, round_code="QF")
        request = replace(
            self.request, required_strata=(RequestedStratum(other, 2),)
        )
        self.assert_reason(QualificationReason.STRATUM_NOT_QUALIFIED, request=request)
        evidence = replace(
            self.manifest.qualification.strata[0], tested_formats=("ndjson",)
        )
        qualification = replace(
            self.manifest.qualification, strata=(evidence,)
        )
        self.assert_reason(
            QualificationReason.FORMAT_UNSUPPORTED,
            manifest=replace(self.manifest, qualification=qualification),
        )

    def test_reasons_are_complete_sorted_and_stable(self) -> None:
        manifest = replace(
            self.manifest,
            billing_mode=BillingMode.PAID,
            auto_renew=True,
            credential_env_names=("HOME",),
        )
        first = self.evaluate(manifest=manifest, environ={})
        second = self.evaluate(manifest=manifest, environ={})
        self.assertEqual(first.reasons, second.reasons)
        self.assertEqual(
            first.reasons, tuple(sorted(set(first.reasons), key=lambda item: item.value))
        )
        self.assertTrue(
            {
                QualificationReason.PAID_ACCESS_DISABLED,
                QualificationReason.AUTO_RENEW_FORBIDDEN,
                QualificationReason.ADAPTER_MISMATCH,
                QualificationReason.CREDENTIAL_MISSING,
            }.issubset(first.reasons)
        )

    def test_require_eligible_raises_one_redacted_summary(self) -> None:
        decision = self.evaluate(environ={})
        with self.assertRaises(ProviderGateError) as raised:
            decision.require_eligible()
        message = str(raised.exception)
        self.assertEqual(raised.exception.reason, QualificationReason.CREDENTIAL_MISSING)
        self.assertIn("credential_missing", message)
        self.assertNotIn(str(self.manifest.permission_artifact_path), message)
        self.assertNotIn("fixture-secret", message)

    def test_gate_rechecks_clock_for_every_operation_and_never_extends_on_rollback(self) -> None:
        times = [self.now, self.now + timedelta(seconds=1), self.now]
        with self.adapter_context():
            gate = self.gate(lambda: times.pop(0))
            gate.require_start()
            gate.require_ingest()
            with self.assertRaises(ProviderGateError) as raised:
                gate.require_resync()
        self.assertEqual(raised.exception.reason, QualificationReason.CLOCK_ROLLBACK)

    def test_require_raw_persist_is_zero_arg_and_uses_only_authoritative_clock(self) -> None:
        import inspect

        parameters = tuple(inspect.signature(ProviderGate.require_raw_persist).parameters)
        self.assertEqual(parameters, ("self",))
        with self.adapter_context():
            gate = self.gate(lambda: self.now)
            deadline = gate.require_raw_persist()
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        expected_time = min(
            self.manifest.raw_retention_until,
            self.now + timedelta(seconds=self.manifest.max_raw_retention_seconds),
        )
        delta = expected_time - epoch
        expected = (
            (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        ) * 1000
        self.assertEqual(deadline, expected)

    def test_session_end_between_raw_persist_transform_and_derived_persist_denies(self) -> None:
        before = self.request.session_end_utc - timedelta(microseconds=1)
        at_end = self.request.session_end_utc
        times = [before, at_end, at_end]
        manifest_with_extended_qualification = replace(
            self.manifest,
            qualification=replace(
                self.manifest.qualification,
                qualified_until=self.manifest.access_expires_at,
            ),
        )
        manifest = replace(
            manifest_with_extended_qualification,
            canonical_sha256=canonical_manifest_sha256(
                manifest_with_extended_qualification
            ),
        )
        with self.adapter_context():
            gate = self.gate(lambda: times.pop(0), manifest=manifest)
            gate.require_raw_persist()
            with self.assertRaises(ProviderGateError) as transform:
                gate.require_transform()
            with self.assertRaises(ProviderGateError) as persist:
                gate.require_derived_persist()
        self.assertEqual(
            transform.exception.reason,
            QualificationReason.SESSION_WINDOW_EXCEEDS_ACCESS,
        )
        self.assertEqual(
            persist.exception.reason,
            QualificationReason.SESSION_WINDOW_EXCEEDS_ACCESS,
        )

    def test_provider_session_window_is_half_open_and_close_remains_authorized(self) -> None:
        operation_names = (
            "require_start",
            "require_ingest",
            "require_resync",
            "require_transform",
            "require_derived_persist",
            "require_raw_persist",
        )
        for method_name in operation_names:
            with self.subTest(method=method_name), self.adapter_context():
                gate = self.gate(lambda: self.request.session_end_utc)
                with self.assertRaises(ProviderGateError):
                    getattr(gate, method_name)()

        with self.adapter_context():
            close_gate = self.gate(lambda: self.request.session_end_utc)
            decision = close_gate.require_close()
            poll = close_gate.poll_session()
        self.assertIs(decision, close_gate._initial_decision)
        self.assertTrue(poll.session_ended)
        self.assertIs(poll.decision, close_gate._initial_decision)

    def test_provider_session_poll_is_false_before_end_and_rebinds_current_evidence(self) -> None:
        with self.adapter_context():
            gate = self.gate(
                lambda: self.request.session_end_utc - timedelta(microseconds=1)
            )
            poll = gate.poll_session()
        self.assertFalse(poll.session_ended)
        self.assertIs(poll.decision, gate._initial_decision)


if __name__ == "__main__":
    unittest.main()
