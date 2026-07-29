from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import tennis_v1.adapter_contract as adapter_contract
import tennis_v1.ingress as ingress_module
import tennis_v1.pinned_file as pinned_file
import tennis_v1.preflight as preflight_module
import tennis_v1.retention as retention_module
import tennis_v1.sequencer as sequencer_module
import tennis_v1.wal as wal_module
from tennis_v1.adapter_contract import (
    AdapterContractError,
    AuthContract,
    AuthMode,
    _capture_adapter_registration,
)
from tennis_v1.config import TennisV1Config, canonical_config_sha256
from tennis_v1.entitlements import (
    CoverageStratum,
    IntendedUse,
    ManifestError,
    QualificationDecision,
    QualificationReason,
    QualifiedProviderBinding,
    ResearchRequest,
    RequestedStratum,
    evaluate_provider,
    load_provider_manifest,
    opaque_id_sha256,
    provider_request_binding_sha256,
)
from tennis_v1.preflight import (
    EntitlementPreflight,
    EntitlementPreflightError,
    run_entitlement_preflight,
)
from tennis_v1.session import (
    build_session_manifest,
    require_decision_matches_session,
    session_manifest_sha256,
)
from tests.tennis_v1.test_entitlements import (
    FixtureBuilder,
    STRATUM,
    synthetic_spec,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PATH = REPOSITORY_ROOT / "provider_manifest.example.json"
README_PATH = REPOSITORY_ROOT / "docs" / "tennis_v1" / "README.md"
EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


class _HostileMapping(Mapping[str, str]):
    def __init__(self, calls: list[str]):
        self.calls = calls

    def __getitem__(self, key: str) -> str:
        self.calls.append("getitem")
        return "secret"

    def __iter__(self):
        self.calls.append("iter")
        return iter(("SYNTHETIC_API_KEY",))

    def __len__(self) -> int:
        self.calls.append("len")
        return 1

    def get(self, key: str, default=None):
        self.calls.append("get")
        return "secret"


class _HostileDict(dict[str, str]):
    def __iter__(self):
        raise AssertionError("dict subclass iteration")

    def get(self, key: str, default=None):
        raise AssertionError("dict subclass get")

    def items(self):
        raise AssertionError("dict subclass items")


class _StringSubclass(str):
    pass


class _IntegerSubclass(int):
    pass


class _TupleSubclass(tuple):
    pass


class _DatetimeSubclass(datetime):
    pass


class EntitlementPreflightCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        self.builder = FixtureBuilder(self.root, REPOSITORY_ROOT)
        self.bundle = self.builder.build()
        self.stratum = CoverageStratum(**STRATUM)
        self.now = datetime(2026, 7, 27, 13, 0, tzinfo=timezone.utc)
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
        self.config = self.make_config()
        self.environ = {"SYNTHETIC_API_KEY": "TOP_SECRET_ENV_VALUE"}

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def make_config(
        self,
        *,
        bundle=None,
        **changes: object,
    ) -> TennisV1Config:
        selected_bundle = self.bundle if bundle is None else bundle
        provisional = TennisV1Config(
            schema_version=1,
            state_root=self.root / "state",
            provider_manifest_path=selected_bundle["manifest_path"],
            provider_manifest_sha256=selected_bundle["manifest_sha"],
            trusted_permission_reviewer_ids=("reviewer-test",),
            trusted_qualification_issuer_ids=("issuer-test",),
            observed_pool_limit=10,
            paper_position_limit=3,
            source_file_sha256="f" * 64,
            canonical_sha256="",
        )
        provisional = replace(provisional, **changes)
        return replace(
            provisional,
            canonical_sha256=canonical_config_sha256(provisional),
        )

    def adapter_context(self):
        return mock.patch.multiple(
            adapter_contract,
            __file__=self.builder.adapter_file,
            _ADAPTER_REGISTRY={
                ("synthetic-provider", "trial-v1"): self.builder.registration
            },
        )

    def loaded_manifest_and_decision(
        self,
    ) -> tuple[object, QualificationDecision]:
        with self.adapter_context():
            manifest = load_provider_manifest(
                self.bundle["manifest_path"],
                expected_sha256=self.bundle["manifest_sha"],
                repo_root=REPOSITORY_ROOT,
            )
            decision = evaluate_provider(
                self.config,
                manifest,
                self.request,
                environ=self.environ,
            )
        return manifest, decision

    def preflight(
        self,
        *,
        config: TennisV1Config | None = None,
        request: ResearchRequest | None = None,
        environ: dict[str, str] | object | None = None,
    ) -> EntitlementPreflight:
        selected_environment = self.environ if environ is None else environ
        with self.adapter_context():
            return run_entitlement_preflight(
                self.config if config is None else config,
                self.request if request is None else request,
                environ=selected_environment,
            )

    def test_public_signature_has_no_authority_or_override_seam(self) -> None:
        signature = inspect.signature(run_entitlement_preflight)
        self.assertEqual(tuple(signature.parameters), ("config", "request", "environ"))
        self.assertEqual(
            signature.parameters["environ"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertNotIn("kwargs", tuple(signature.parameters))
        with self.assertRaises(TypeError):
            run_entitlement_preflight(  # type: ignore[call-arg]
                self.config,
                self.request,
                environ=self.environ,
                repo_root=REPOSITORY_ROOT,
            )

    def test_valid_bundle_returns_frozen_redacted_diagnostic_not_authority(
        self,
    ) -> None:
        result = self.preflight()
        delta = self.request.required_retention_until - EPOCH_UTC
        expected_delete_ns = (
            (delta.days * 86_400 + delta.seconds) * 1_000_000
            + delta.microseconds
        ) * 1_000

        self.assertIs(type(result), EntitlementPreflight)
        self.assertTrue(result.eligible)
        self.assertEqual(result.reasons, ("eligible",))
        self.assertFalse(result.export_allowed)
        self.assertEqual(result.provider_id, "synthetic-provider")
        self.assertEqual(result.product_tier, "trial-v1")
        self.assertEqual(result.requested_matches, 2)
        self.assertEqual(result.planned_session_delete_by_ns, expected_delete_ns)
        self.assertIs(type(result.qualified_until), datetime)
        with self.assertRaises(FrozenInstanceError):
            result.eligible = False  # type: ignore[misc]

        serialized = repr(asdict(result))
        for forbidden in (
            "TOP_SECRET_ENV_VALUE",
            "SYNTHETIC_API_KEY",
            "opaque-entitlement-fixture",
            str(self.bundle["manifest_path"]),
            "reviewer-test",
            "issuer-test",
        ):
            self.assertNotIn(forbidden, serialized)
            self.assertNotIn(forbidden, repr(result))
        self.assertFalse(
            any(
                hasattr(result, name)
                for name in (
                    "binding",
                    "decision",
                    "manifest",
                    "adapter",
                    "loader",
                    "authorize",
                    "persist",
                )
            )
        )

    def test_every_sensitive_layer_is_redacted_from_success_and_failure(
        self,
    ) -> None:
        sentinels = {
            "environment_name": "REDACTION_ENV_SENTINEL",
            "environment_value": "REDACTION_ENV_VALUE_SENTINEL",
            "artifact": "REDACTION_ARTIFACT_SENTINEL",
            "path": "REDACTION_PATH_SENTINEL",
            "entitlement": "REDACTION_ENTITLEMENT_SENTINEL",
            "lineage": "REDACTION_LINEAGE_SENTINEL",
            "terms_url": "https://redaction-terms-sentinel.invalid/private",
            "terms_version": "REDACTION-TERMS-SENTINEL",
            "reviewer": "REDACTION-REVIEWER-SENTINEL",
            "permission_approval": "REDACTION-PERMISSION-APPROVAL",
            "issuer": "REDACTION-ISSUER-SENTINEL",
            "qualification_approval": "REDACTION-QUALIFICATION-APPROVAL",
        }
        secret_root = self.root / sentinels["path"]
        secret_root.mkdir()
        secret_builder = FixtureBuilder(secret_root, REPOSITORY_ROOT)
        secret_spec = replace(
            synthetic_spec(),
            auth=AuthContract(
                mode=AuthMode.API_KEY,
                credential_env_names=(sentinels["environment_name"],),
            ),
        )
        with mock.patch.object(
            adapter_contract,
            "__file__",
            secret_builder.adapter_file,
        ):
            secret_builder.registration = _capture_adapter_registration(
                module_paths=("synthetic_adapter.py",),
                spec=secret_spec,
            )

        def permission_change(raw):
            raw["entitlement_id_sha256"] = opaque_id_sha256(
                sentinels["entitlement"]
            )
            raw["terms_version"] = sentinels["terms_version"]
            raw["reviewer_id"] = sentinels["reviewer"]
            raw["approval_id"] = sentinels["permission_approval"]

        def trace_change(raw):
            raw["source_lineage_id"] = sentinels["lineage"]

        def qualification_change(raw):
            raw["source_lineage_id"] = sentinels["lineage"]
            raw["issuer_id"] = sentinels["issuer"]
            raw["approval_id"] = sentinels["qualification_approval"]

        def manifest_change(raw):
            raw["entitlement_id"] = sentinels["entitlement"]
            raw["source_lineage_id"] = sentinels["lineage"]
            raw["terms_url"] = sentinels["terms_url"]
            raw["terms_version"] = sentinels["terms_version"]
            raw["credential_env_names"] = [sentinels["environment_name"]]

        secret_bundle = secret_builder.build(
            manifest_change=manifest_change,
            permission_change=permission_change,
            trace_change=trace_change,
            qualification_change=qualification_change,
            evidence=sentinels["artifact"].encode("utf-8"),
        )
        secret_config = self.make_config(
            bundle=secret_bundle,
            trusted_permission_reviewer_ids=(sentinels["reviewer"],),
            trusted_qualification_issuer_ids=(sentinels["issuer"],),
        )
        secret_environment = {
            sentinels["environment_name"]: sentinels["environment_value"]
        }

        secret_adapter_context = mock.patch.multiple(
            adapter_contract,
            __file__=secret_builder.adapter_file,
            _ADAPTER_REGISTRY={
                ("synthetic-provider", "trial-v1"): (
                    secret_builder.registration
                )
            },
        )
        with secret_adapter_context:
            result = run_entitlement_preflight(
                secret_config,
                self.request,
                environ=secret_environment,
            )
        result_surfaces = (
            result,
            asdict(result),
            tuple(
                getattr(result, name)
                for name in result.__slots__
            ),
        )
        for sentinel in sentinels.values():
            for surface in result_surfaces:
                self.assertNotIn(sentinel, repr(surface))

        with secret_adapter_context:
            ineligible = run_entitlement_preflight(
                secret_config,
                self.request,
                environ={},
            )
        self.assertFalse(ineligible.eligible)
        self.assertIn("credential_missing", ineligible.reasons)
        ineligible_surfaces = (
            ineligible,
            asdict(ineligible),
            tuple(
                getattr(ineligible, name)
                for name in ineligible.__slots__
            ),
        )
        for sentinel in sentinels.values():
            for surface in ineligible_surfaces:
                self.assertNotIn(sentinel, repr(surface))

        invalid_config = self.make_config(
            bundle=secret_bundle,
            provider_manifest_sha256="0" * 64,
            trusted_permission_reviewer_ids=(sentinels["reviewer"],),
            trusted_qualification_issuer_ids=(sentinels["issuer"],),
        )
        with (
            secret_adapter_context,
            self.assertRaises(EntitlementPreflightError) as raised,
        ):
            run_entitlement_preflight(
                invalid_config,
                self.request,
                environ=secret_environment,
            )
        error = raised.exception
        error_surfaces = (
            error.args,
            str(error),
            repr(error),
            getattr(error, "__notes__", ()),
            error.__cause__,
            error.__context__,
        )
        for sentinel in sentinels.values():
            for surface in error_surfaces:
                self.assertNotIn(sentinel, repr(surface))
        self.assertEqual(error.args, ("entitlement_preflight_failed",))
        self.assertEqual(vars(error), {})
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def test_exact_environment_is_privately_copied_and_hostile_inputs_do_not_dispatch(
        self,
    ) -> None:
        calls: list[str] = []
        hostile_mapping = _HostileMapping(calls)
        with self.assertRaises(EntitlementPreflightError):
            self.preflight(environ=hostile_mapping)
        self.assertEqual(calls, [])

        with self.assertRaises(EntitlementPreflightError):
            self.preflight(environ=_HostileDict(SYNTHETIC_API_KEY="secret"))

        for malformed in (
            {_StringSubclass("SYNTHETIC_API_KEY"): "secret"},
            {"SYNTHETIC_API_KEY": _StringSubclass("secret")},
            {1: "secret"},
            {"SYNTHETIC_API_KEY": object()},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(
                EntitlementPreflightError
            ):
                self.preflight(environ=malformed)

    def test_verified_expiry_is_policy_denial_not_parser_failure(self) -> None:
        expired_now = datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc)
        request = replace(
            self.request,
            now_utc=expired_now,
            session_end_utc=expired_now + timedelta(hours=1),
            required_retention_until=expired_now + timedelta(hours=2),
        )
        result = self.preflight(
            request=request,
            environ={
                "SYNTHETIC_API_KEY": "secret",
                "ACCESS_EXPIRES_AT": "2999-01-01T00:00:00Z",
                "QUALIFIED_UNTIL": "2999-01-01T00:00:00Z",
            },
        )
        self.assertFalse(result.eligible)
        self.assertIn(QualificationReason.ACCESS_EXPIRED.value, result.reasons)
        self.assertIn(
            QualificationReason.QUALIFICATION_NOT_PASSED.value,
            result.reasons,
        )
        self.assertEqual(result.reasons, tuple(sorted(set(result.reasons))))
        self.assertFalse(result.export_allowed)
        self.assertIs(type(result.qualified_until), datetime)

    def test_empty_production_registry_is_a_stable_verification_failure(self) -> None:
        self.assertEqual(adapter_contract._ADAPTER_REGISTRY, {})
        with self.assertRaises(EntitlementPreflightError) as raised:
            run_entitlement_preflight(
                self.config,
                self.request,
                environ=self.environ,
            )
        self.assertEqual(adapter_contract._ADAPTER_REGISTRY, {})
        self.assertEqual(raised.exception.args, ("entitlement_preflight_failed",))
        self.assertEqual(str(raised.exception), "entitlement_preflight_failed")
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        self.assertFalse(hasattr(raised.exception, "__notes__"))

    def test_config_structural_gate_covers_every_field_before_environment_or_io(
        self,
    ) -> None:
        class PathSubclass(type(Path())):
            pass

        class HostileConfig(TennisV1Config):
            def __getattribute__(self, name):
                raise AssertionError(f"config getter:{name}")

        hostile = object.__new__(HostileConfig)
        malformed = (
            hostile,
            replace(self.config, schema_version=True),
            replace(self.config, schema_version=_IntegerSubclass(1)),
            replace(self.config, state_root=str(self.config.state_root)),
            replace(self.config, state_root=Path("relative-state")),
            replace(
                self.config,
                state_root=self.root / "outside" / ".." / "state",
            ),
            replace(
                self.config,
                state_root=PathSubclass(str(self.config.state_root)),
            ),
            replace(
                self.config,
                provider_manifest_path=str(self.config.provider_manifest_path),
            ),
            replace(
                self.config,
                provider_manifest_path=Path("relative-manifest.json"),
            ),
            replace(
                self.config,
                provider_manifest_path=(
                    self.root / "outside" / ".." / "manifest.json"
                ),
            ),
            replace(
                self.config,
                provider_manifest_path=PathSubclass(
                    str(self.config.provider_manifest_path)
                ),
            ),
            replace(
                self.config,
                provider_manifest_sha256=_StringSubclass("a" * 64),
            ),
            replace(self.config, source_file_sha256=_StringSubclass("a" * 64)),
            replace(self.config, canonical_sha256=_StringSubclass("a" * 64)),
            replace(
                self.config,
                trusted_permission_reviewer_ids=["reviewer-test"],
            ),
            replace(
                self.config,
                trusted_permission_reviewer_ids=_TupleSubclass(("reviewer-test",)),
            ),
            replace(
                self.config,
                trusted_permission_reviewer_ids=(_StringSubclass("reviewer-test"),),
            ),
            replace(
                self.config,
                trusted_permission_reviewer_ids=("reviewer-z", "reviewer-a"),
            ),
            replace(
                self.config,
                trusted_permission_reviewer_ids=("reviewer-test", "reviewer-test"),
            ),
            replace(
                self.config,
                trusted_qualification_issuer_ids=["issuer-test"],
            ),
            replace(
                self.config,
                trusted_qualification_issuer_ids=_TupleSubclass(
                    ("issuer-test",)
                ),
            ),
            replace(
                self.config,
                trusted_qualification_issuer_ids=(_StringSubclass("issuer-test"),),
            ),
            replace(
                self.config,
                trusted_qualification_issuer_ids=("issuer-z", "issuer-a"),
            ),
            replace(
                self.config,
                trusted_qualification_issuer_ids=("issuer-test", "issuer-test"),
            ),
            replace(self.config, observed_pool_limit=True),
            replace(self.config, observed_pool_limit=_IntegerSubclass(10)),
            replace(self.config, observed_pool_limit=11),
            replace(self.config, paper_position_limit=True),
            replace(self.config, paper_position_limit=_IntegerSubclass(3)),
            replace(self.config, paper_position_limit=4),
            replace(self.config, canonical_sha256="0" * 64),
        )
        for candidate in malformed:
            with (
                self.subTest(candidate_type=type(candidate).__name__),
                mock.patch.object(
                    preflight_module,
                    "_snapshot_environment",
                    side_effect=AssertionError("environment dispatch"),
                ) as snapshot_call,
                mock.patch.object(
                    preflight_module,
                    "_load_provider_manifest_restricted",
                    side_effect=AssertionError("bundle I/O"),
                ) as load_call,
                self.assertRaises(EntitlementPreflightError),
            ):
                run_entitlement_preflight(
                    candidate,
                    self.request,
                    environ=self.environ,
                )
            snapshot_call.assert_not_called()
            load_call.assert_not_called()

    def test_request_structural_gate_covers_every_nested_field_before_dispatch(
        self,
    ) -> None:
        class HostileRequest(ResearchRequest):
            def __getattribute__(self, name):
                raise AssertionError(f"request getter:{name}")

        class HostileRequestedStratum(RequestedStratum):
            def __getattribute__(self, name):
                raise AssertionError(f"requested stratum getter:{name}")

        class HostileCoverageStratum(CoverageStratum):
            def __getattribute__(self, name):
                raise AssertionError(f"coverage getter:{name}")

        custom_utc = timezone(timedelta(0), name="CUSTOM_UTC")
        datetime_subclass = _DatetimeSubclass(
            2026,
            7,
            27,
            13,
            0,
            tzinfo=timezone.utc,
        )
        malformed = (
            object.__new__(HostileRequest),
            replace(self.request, intended_use="private_paper_evaluation"),
            replace(self.request, now_utc=self.now.replace(tzinfo=None)),
            replace(self.request, now_utc=self.now.astimezone(custom_utc)),
            replace(self.request, now_utc=datetime_subclass),
            replace(self.request, session_end_utc=self.now.replace(tzinfo=None)),
            replace(
                self.request,
                required_retention_until=self.now.astimezone(custom_utc),
            ),
            replace(self.request, expiry_safety_margin_seconds=True),
            replace(
                self.request,
                required_raw_retention_seconds=_IntegerSubclass(3600),
            ),
            replace(self.request, requested_matches="2"),
            replace(self.request, required_strata=[self.request.required_strata[0]]),
            replace(
                self.request,
                required_strata=_TupleSubclass(self.request.required_strata),
            ),
            replace(
                self.request,
                required_strata=(object.__new__(HostileRequestedStratum),),
            ),
            replace(
                self.request,
                required_strata=(
                    RequestedStratum(
                        object.__new__(HostileCoverageStratum),
                        2,
                    ),
                ),
            ),
            replace(
                self.request,
                required_strata=(
                    RequestedStratum(self.stratum, _IntegerSubclass(2)),
                ),
            ),
        )
        for field_name in (
            "sport",
            "tour",
            "competition_tier",
            "match_format",
            "round_code",
        ):
            malformed_stratum = replace(
                self.stratum,
                **{field_name: _StringSubclass(getattr(self.stratum, field_name))},
            )
            malformed += (
                replace(
                    self.request,
                    required_strata=(RequestedStratum(malformed_stratum, 2),),
                ),
            )

        for candidate in malformed:
            with (
                self.subTest(candidate_type=type(candidate).__name__),
                mock.patch.object(
                    preflight_module,
                    "_snapshot_environment",
                    side_effect=AssertionError("environment dispatch"),
                ) as snapshot_call,
                mock.patch.object(
                    preflight_module,
                    "_load_provider_manifest_restricted",
                    side_effect=AssertionError("bundle I/O"),
                ) as load_call,
                self.assertRaises(EntitlementPreflightError),
            ):
                run_entitlement_preflight(
                    self.config,
                    candidate,
                    environ=self.environ,
                )
            snapshot_call.assert_not_called()
            load_call.assert_not_called()

    def test_trust_anchor_collections_reject_more_than_64_before_dispatch(
        self,
    ) -> None:
        reviewers = tuple(f"reviewer-{index:02d}" for index in range(65))
        issuers = tuple(f"issuer-{index:02d}" for index in range(65))
        candidates = (
            self.make_config(trusted_permission_reviewer_ids=reviewers),
            self.make_config(trusted_qualification_issuer_ids=issuers),
        )
        for candidate in candidates:
            with (
                mock.patch.object(
                    preflight_module,
                    "_snapshot_environment",
                    side_effect=AssertionError("environment dispatch"),
                ) as snapshot_call,
                mock.patch.object(
                    preflight_module,
                    "_load_provider_manifest_restricted",
                    side_effect=AssertionError("bundle I/O"),
                ) as load_call,
                self.assertRaises(EntitlementPreflightError),
            ):
                run_entitlement_preflight(
                    candidate,
                    self.request,
                    environ=self.environ,
                )
            snapshot_call.assert_not_called()
            load_call.assert_not_called()

    def test_well_typed_semantic_inadequacy_returns_ineligible_diagnostic(
        self,
    ) -> None:
        request = replace(
            self.request,
            session_end_utc=self.now - timedelta(hours=1),
            required_retention_until=self.now - timedelta(hours=2),
            expiry_safety_margin_seconds=-1,
            required_raw_retention_seconds=-1,
            requested_matches=0,
            required_strata=(),
        )
        result = self.preflight(request=request)
        self.assertFalse(result.eligible)
        self.assertNotIn("eligible", result.reasons)
        self.assertTrue(result.reasons)

    def test_caller_mutation_after_snapshot_cannot_change_evaluation(self) -> None:
        original = dict(self.environ)
        real_evaluator = preflight_module._evaluate_provider_as_of

        def mutate_then_evaluate(*args, **kwargs):
            original.clear()
            original["SYNTHETIC_API_KEY"] = ""
            return real_evaluator(*args, **kwargs)

        with (
            self.adapter_context(),
            mock.patch.object(
                preflight_module,
                "_evaluate_provider_as_of",
                side_effect=mutate_then_evaluate,
            ),
        ):
            result = run_entitlement_preflight(
                self.config,
                self.request,
                environ=original,
            )
        self.assertTrue(result.eligible)
        self.assertEqual(original, {"SYNTHETIC_API_KEY": ""})

    def test_state_root_overlap_is_rejected_before_environment_and_bundle_io(
        self,
    ) -> None:
        overlap_paths = (
            REPOSITORY_ROOT,
            REPOSITORY_ROOT.parent,
            REPOSITORY_ROOT / "state",
            self.bundle["manifest_path"],
            self.bundle["manifest_path"].parent,
            self.bundle["manifest_path"] / "state",
        )
        for state_root in overlap_paths:
            config = self.make_config(state_root=state_root)
            with (
                self.subTest(state_root=state_root),
                mock.patch.object(
                    preflight_module,
                    "_snapshot_environment",
                    side_effect=AssertionError("environment dispatch"),
                ) as snapshot_call,
                mock.patch.object(
                    preflight_module,
                    "_load_provider_manifest_restricted",
                    side_effect=AssertionError("bundle I/O"),
                ) as load_call,
                self.assertRaises(EntitlementPreflightError),
            ):
                run_entitlement_preflight(
                    config,
                    self.request,
                    environ=self.environ,
                )
            snapshot_call.assert_not_called()
            load_call.assert_not_called()

    def test_state_overlap_rejects_casefold_and_unicode_aliases_before_io(
        self,
    ) -> None:
        candidates = (
            self.make_config(
                state_root=self.root / "StateRoot",
                provider_manifest_path=(
                    self.root / "stateroot" / "manifest.json"
                ),
            ),
            self.make_config(
                state_root=self.root / "Caf\u00e9State",
                provider_manifest_path=(
                    self.root / "Cafe\u0301State" / "manifest.json"
                ),
            ),
        )
        for config in candidates:
            with (
                self.subTest(
                    state_root=config.state_root,
                    manifest=config.provider_manifest_path,
                ),
                mock.patch.object(
                    preflight_module,
                    "_snapshot_environment",
                    side_effect=AssertionError("environment dispatch"),
                ) as snapshot_call,
                mock.patch.object(
                    preflight_module,
                    "_load_provider_manifest_restricted",
                    side_effect=AssertionError("bundle I/O"),
                ) as load_call,
                self.assertRaises(EntitlementPreflightError),
            ):
                run_entitlement_preflight(
                    config,
                    self.request,
                    environ=self.environ,
                )
            snapshot_call.assert_not_called()
            load_call.assert_not_called()

    def test_state_overlap_rejects_real_case_insensitive_volume_alias(self) -> None:
        manifest_directory = self.bundle["manifest_path"].parent
        alias_directory = manifest_directory.with_name(
            manifest_directory.name.swapcase()
        )
        if (
            alias_directory == manifest_directory
            or not alias_directory.exists()
        ):
            self.skipTest("temporary volume is case-sensitive")
        config = self.make_config(state_root=alias_directory)

        with (
            mock.patch.object(
                preflight_module,
                "_snapshot_environment",
                side_effect=AssertionError("environment dispatch"),
            ) as snapshot_call,
            mock.patch.object(
                preflight_module,
                "_load_provider_manifest_restricted",
                side_effect=AssertionError("bundle I/O"),
            ) as load_call,
            self.assertRaises(EntitlementPreflightError),
        ):
            run_entitlement_preflight(
                config,
                self.request,
                environ=self.environ,
            )
        snapshot_call.assert_not_called()
        load_call.assert_not_called()

    def test_each_nested_reference_equal_or_under_state_is_denied_before_target_probe(
        self,
    ) -> None:
        references = (
            ("permission", "artifact_path"),
            ("permission", "evidence_path"),
            ("qualification", "artifact_path"),
            ("qualification", "evidence_trace_path"),
        )
        for section, field in references:
            for suffix in ((), ("nested", f"{section}-{field}.json")):
                state_root = self.root / f"forbidden-{section}-{field}-{len(suffix)}"
                candidate = state_root.joinpath(*suffix)

                def change(raw, *, selected=candidate, group=section, key=field):
                    raw[group][key] = str(selected)

                bundle = self.builder.build(manifest_change=change)
                config = self.make_config(
                    bundle=bundle,
                    state_root=state_root,
                )
                real_open = pinned_file.os.open
                with (
                    self.subTest(section=section, field=field, suffix=suffix),
                    self.adapter_context(),
                    mock.patch.object(
                        pinned_file.os,
                        "open",
                        wraps=real_open,
                    ) as open_call,
                    self.assertRaises(EntitlementPreflightError),
                ):
                    run_entitlement_preflight(
                        config,
                        self.request,
                        environ=self.environ,
                    )
                opened_names = {
                    str(call.args[0])
                    for call in open_call.call_args_list
                    if call.args
                }
                self.assertNotIn(state_root.name, opened_names)
                self.assertFalse(state_root.exists())

    def test_nested_dotdot_and_symlink_alias_cannot_reach_state(self) -> None:
        state_root = self.root / "state-alias-target"
        dotdot_path = (
            self.root
            / "outside"
            / ".."
            / state_root.name
            / "permission.json"
        )

        def dotdot_change(raw):
            raw["permission"]["artifact_path"] = str(dotdot_path)

        dotdot_bundle = self.builder.build(manifest_change=dotdot_change)
        dotdot_config = self.make_config(
            bundle=dotdot_bundle,
            state_root=state_root,
        )
        with self.adapter_context(), self.assertRaises(EntitlementPreflightError):
            run_entitlement_preflight(
                dotdot_config,
                self.request,
                environ=self.environ,
            )
        self.assertFalse(state_root.exists())

        state_root.mkdir()
        target_manifest = state_root / "manifest.json"
        target_manifest.write_bytes(self.bundle["manifest_path"].read_bytes())
        alias = self.root / "manifest-alias.json"
        alias.symlink_to(target_manifest)
        alias_config = self.make_config(
            provider_manifest_path=alias,
            provider_manifest_sha256=hashlib.sha256(
                target_manifest.read_bytes()
            ).hexdigest(),
            state_root=state_root,
        )
        with self.adapter_context(), self.assertRaises(EntitlementPreflightError):
            run_entitlement_preflight(
                alias_config,
                self.request,
                environ=self.environ,
            )
        self.assertEqual(
            target_manifest.read_bytes(),
            self.bundle["manifest_path"].read_bytes(),
        )

    def test_code_derived_root_is_invariant_and_passed_exactly_to_loader(
        self,
    ) -> None:
        real_loader = preflight_module._load_provider_manifest_restricted
        original_cwd = Path.cwd()
        try:
            os.chdir(self.root)
            with (
                self.adapter_context(),
                mock.patch.object(
                    preflight_module,
                    "__file__",
                    "/attacker/controlled/preflight.py",
                ),
                mock.patch.object(
                    preflight_module,
                    "_load_provider_manifest_restricted",
                    wraps=real_loader,
                ) as load_call,
            ):
                result = run_entitlement_preflight(
                    self.config,
                    self.request,
                    environ=dict(self.environ),
                )
        finally:
            os.chdir(original_cwd)
        self.assertTrue(result.eligible)
        self.assertEqual(
            load_call.call_args.kwargs["repo_root"],
            REPOSITORY_ROOT,
        )
        self.assertIs(
            type(load_call.call_args.kwargs["repo_root"]),
            type(Path()),
        )

    def test_repository_file_and_external_symlink_alias_are_both_rejected(
        self,
    ) -> None:
        digest = hashlib.sha256(EXAMPLE_PATH.read_bytes()).hexdigest()
        direct = self.make_config(
            provider_manifest_path=EXAMPLE_PATH,
            provider_manifest_sha256=digest,
        )
        alias = self.root / "repo-manifest-alias.json"
        alias.symlink_to(EXAMPLE_PATH)
        aliased = self.make_config(
            provider_manifest_path=alias,
            provider_manifest_sha256=digest,
        )
        for config in (direct, aliased):
            with self.subTest(path=config.provider_manifest_path), self.assertRaises(
                EntitlementPreflightError
            ):
                run_entitlement_preflight(
                    config,
                    self.request,
                    environ=self.environ,
                )

    def test_success_denial_and_failure_never_create_or_write_state(self) -> None:
        state_root = self.config.state_root
        before = tuple(
            sorted(
                path.relative_to(self.root).as_posix()
                for path in self.root.rglob("*")
            )
        )
        real_open = pinned_file.os.open
        with (
            self.adapter_context(),
            mock.patch.object(
                pinned_file.os,
                "open",
                wraps=real_open,
            ) as open_call,
        ):
            self.assertTrue(
                run_entitlement_preflight(
                    self.config,
                    self.request,
                    environ=self.environ,
                ).eligible
            )
            denied_request = replace(self.request, requested_matches=0)
            self.assertFalse(
                run_entitlement_preflight(
                    self.config,
                    denied_request,
                    environ=self.environ,
                ).eligible
            )
        with self.assertRaises(EntitlementPreflightError):
            run_entitlement_preflight(
                self.config,
                self.request,
                environ=self.environ,
            )
        after = tuple(
            sorted(
                path.relative_to(self.root).as_posix()
                for path in self.root.rglob("*")
            )
        )
        self.assertEqual(after, before)
        self.assertFalse(state_root.exists())
        for call in open_call.call_args_list:
            if len(call.args) >= 2:
                flags = call.args[1]
                self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT), 0)
            if call.args:
                self.assertNotEqual(str(call.args[0]), state_root.name)

    def test_malformed_decision_matrix_is_rejected_before_projection(self) -> None:
        manifest, eligible = self.loaded_manifest_and_decision()
        self.assertTrue(eligible.eligible)
        self.assertIs(type(eligible.binding), QualifiedProviderBinding)
        valid_ineligible = replace(
            eligible,
            eligible=False,
            reasons=(QualificationReason.ACCESS_EXPIRED,),
            export_allowed=False,
            provider_request_binding_sha256=None,
            binding=None,
        )
        mutated_binding = replace(
            eligible.binding,
            provider_id="different-provider",
        )
        rebound = replace(
            eligible,
            binding=mutated_binding,
            provider_request_binding_sha256=None,
        )
        rebound = replace(
            rebound,
            provider_request_binding_sha256=provider_request_binding_sha256(
                rebound
            ),
        )
        malformed = (
            replace(eligible, manifest_file_sha256="0" * 64),
            replace(eligible, manifest_canonical_sha256="0" * 64),
            replace(eligible, request_sha256="0" * 64),
            replace(eligible, eligible=1),
            replace(eligible, export_allowed=1),
            replace(eligible, export_allowed=True),
            replace(eligible, reasons=()),
            replace(
                valid_ineligible,
                reasons=(
                    QualificationReason.ACCESS_EXPIRED,
                    QualificationReason.ACCESS_EXPIRED,
                ),
            ),
            replace(
                valid_ineligible,
                reasons=(
                    QualificationReason.ADAPTER_MISMATCH,
                    QualificationReason.ACCESS_EXPIRED,
                ),
            ),
            replace(eligible, binding=None),
            replace(eligible, provider_request_binding_sha256=None),
            replace(
                valid_ineligible,
                binding=eligible.binding,
            ),
            replace(
                valid_ineligible,
                provider_request_binding_sha256=(
                    eligible.provider_request_binding_sha256
                ),
            ),
            replace(valid_ineligible, export_allowed=True),
            replace(
                valid_ineligible,
                reasons=(QualificationReason.ELIGIBLE,),
            ),
            replace(
                valid_ineligible,
                reasons=(QualificationReason.ADAPTER_MISMATCH,),
            ),
            replace(
                valid_ineligible,
                reasons=(
                    QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
                ),
            ),
            rebound,
        )
        for candidate in malformed:
            with (
                self.subTest(candidate=candidate),
                self.adapter_context(),
                mock.patch.object(
                    preflight_module,
                    "_evaluate_provider_as_of",
                    return_value=candidate,
                ),
                self.assertRaises(EntitlementPreflightError) as raised,
            ):
                run_entitlement_preflight(
                    self.config,
                    self.request,
                    environ=self.environ,
                )
            self.assertEqual(
                raised.exception.args,
                ("entitlement_preflight_failed",),
            )
            self.assertIsNone(raised.exception.__cause__)
            self.assertIsNone(raised.exception.__context__)

        with (
            self.adapter_context(),
            mock.patch.object(
                preflight_module,
                "_evaluate_provider_as_of",
                return_value=valid_ineligible,
            ),
        ):
            projected = run_entitlement_preflight(
                self.config,
                self.request,
                environ=self.environ,
            )
        self.assertFalse(projected.eligible)
        self.assertEqual(projected.reasons, ("access_expired",))

    def test_hostile_decision_and_binding_subclasses_do_not_dispatch(self) -> None:
        _, eligible = self.loaded_manifest_and_decision()
        calls: list[str] = []

        class HostileDecision(QualificationDecision):
            def __getattribute__(self, name):
                calls.append(f"decision:{name}")
                raise AssertionError(name)

        class HostileBinding(QualifiedProviderBinding):
            def __getattribute__(self, name):
                calls.append(f"binding:{name}")
                raise AssertionError(name)

        hostile_decision = object.__new__(HostileDecision)
        candidates = (
            hostile_decision,
            replace(
                eligible,
                binding=object.__new__(HostileBinding),
            ),
        )
        for candidate in candidates:
            calls.clear()
            with (
                self.adapter_context(),
                mock.patch.object(
                    preflight_module,
                    "_evaluate_provider_as_of",
                    return_value=candidate,
                ),
                self.assertRaises(EntitlementPreflightError),
            ):
                run_entitlement_preflight(
                    self.config,
                    self.request,
                    environ=self.environ,
                )
            self.assertEqual(calls, [])

    def test_failed_and_untested_qualification_keep_none_deadline(self) -> None:
        for status in ("failed", "untested"):
            def change(raw, *, selected=status):
                raw["status"] = selected
                raw["qualified_at"] = None
                raw["qualified_until"] = None
                raw["observed_matches"] = 0
                raw["simultaneous_matches_tested"] = 0
                raw["strata"] = []

            bundle = self.builder.build(
                trace_change=lambda raw: raw.__setitem__("matches", []),
                qualification_change=change,
            )
            config = self.make_config(bundle=bundle)
            with self.adapter_context():
                result = run_entitlement_preflight(
                    config,
                    self.request,
                    environ=self.environ,
                )
            self.assertFalse(result.eligible)
            self.assertIsNone(result.qualified_until)
            self.assertIn("qualification_not_passed", result.reasons)

    def test_untrusted_and_future_policy_inputs_return_ineligible_diagnostics(
        self,
    ) -> None:
        untrusted_reviewer = self.make_config(
            trusted_permission_reviewer_ids=("different-reviewer",),
        )
        untrusted_issuer = self.make_config(
            trusted_qualification_issuer_ids=("different-issuer",),
        )
        for config, expected in (
            (untrusted_reviewer, "mandatory_permission_missing"),
            (untrusted_issuer, "qualification_not_passed"),
        ):
            result = self.preflight(config=config)
            self.assertFalse(result.eligible)
            self.assertIn(expected, result.reasons)

    def test_policy_denials_project_through_the_complete_preflight(self) -> None:
        missing_credential = self.preflight(environ={})
        self.assertFalse(missing_credential.eligible)
        self.assertIn("credential_missing", missing_credential.reasons)

        def starve_quotas(raw):
            raw["quotas"] = {
                key: 1
                for key in raw["quotas"]
            }

        quota_bundle = self.builder.build(manifest_change=starve_quotas)
        quota_config = self.make_config(bundle=quota_bundle)
        with self.adapter_context():
            quota = run_entitlement_preflight(
                quota_config,
                self.request,
                environ=self.environ,
            )
        self.assertFalse(quota.eligible)
        self.assertIn("quota_inadequate", quota.reasons)

        def remove_capabilities(raw):
            for key, value in raw["capabilities"].items():
                if type(value) is bool:
                    raw["capabilities"][key] = False

        def empty_trace(raw):
            raw["matches"] = []

        def failed_qualification(raw):
            raw["status"] = "failed"
            raw["qualified_at"] = None
            raw["qualified_until"] = None
            raw["observed_matches"] = 0
            raw["simultaneous_matches_tested"] = 0
            raw["strata"] = []

        capability_bundle = self.builder.build(
            manifest_change=remove_capabilities,
            trace_change=empty_trace,
            qualification_change=failed_qualification,
        )
        capability_config = self.make_config(bundle=capability_bundle)
        with self.adapter_context():
            capability = run_entitlement_preflight(
                capability_config,
                self.request,
                environ=self.environ,
            )
        self.assertFalse(capability.eligible)
        self.assertIn("capability_missing", capability.reasons)

        different_stratum = replace(self.stratum, tour="WTA")
        stratum_request = replace(
            self.request,
            required_strata=(RequestedStratum(different_stratum, 2),),
        )
        stratum = self.preflight(request=stratum_request)
        self.assertFalse(stratum.eligible)
        self.assertIn("stratum_not_qualified", stratum.reasons)

        def future_review(raw):
            raw["reviewed_at"] = "2026-07-28T13:00:00Z"

        def future_issue(raw):
            raw["issued_at"] = "2026-07-28T13:00:00Z"

        for bundle, expected in (
            (
                self.builder.build(permission_change=future_review),
                "mandatory_permission_missing",
            ),
            (
                self.builder.build(qualification_change=future_issue),
                "qualification_not_passed",
            ),
        ):
            config = self.make_config(bundle=bundle)
            with self.adapter_context():
                result = run_entitlement_preflight(
                    config,
                    self.request,
                    environ=self.environ,
                )
            self.assertFalse(result.eligible)
            self.assertIn(expected, result.reasons)

    def test_format_policy_denial_projects_but_adapter_drift_is_verification_failure(
        self,
    ) -> None:
        _, eligible = self.loaded_manifest_and_decision()
        format_denial = replace(
            eligible,
            eligible=False,
            reasons=(QualificationReason.FORMAT_UNSUPPORTED,),
            export_allowed=False,
            provider_request_binding_sha256=None,
            binding=None,
        )
        with (
            self.adapter_context(),
            mock.patch.object(
                preflight_module,
                "_evaluate_provider_as_of",
                return_value=format_denial,
            ),
        ):
            unsupported_format = run_entitlement_preflight(
                self.config,
                self.request,
                environ=self.environ,
            )
        self.assertFalse(unsupported_format.eligible)
        self.assertEqual(
            unsupported_format.reasons,
            ("format_unsupported",),
        )

        adapter = self.builder.active_adapter()
        with (
            mock.patch(
                "tennis_v1.entitlements.load_active_adapter_contract",
                side_effect=(
                    adapter,
                    adapter,
                    adapter,
                    AdapterContractError("synthetic adapter vanished"),
                ),
            ),
            self.assertRaises(EntitlementPreflightError) as raised,
        ):
            run_entitlement_preflight(
                self.config,
                self.request,
                environ=self.environ,
            )
        self.assertEqual(
            raised.exception.args,
            ("entitlement_preflight_failed",),
        )
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_every_qualification_reason_has_one_frozen_preflight_outcome(
        self,
    ) -> None:
        _, eligible = self.loaded_manifest_and_decision()
        verification_failures = {
            QualificationReason.ADAPTER_MISMATCH,
            QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH,
        }
        policy_denials = tuple(
            reason
            for reason in QualificationReason
            if reason is not QualificationReason.ELIGIBLE
            and reason not in verification_failures
        )
        self.assertEqual(len(policy_denials), 17)

        projected = self.preflight()
        self.assertTrue(projected.eligible)
        self.assertEqual(projected.reasons, ("eligible",))

        for reason in policy_denials:
            candidate = replace(
                eligible,
                eligible=False,
                reasons=(reason,),
                export_allowed=False,
                provider_request_binding_sha256=None,
                binding=None,
            )
            with (
                self.subTest(reason=reason.value),
                self.adapter_context(),
                mock.patch.object(
                    preflight_module,
                    "_evaluate_provider_as_of",
                    return_value=candidate,
                ),
            ):
                result = run_entitlement_preflight(
                    self.config,
                    self.request,
                    environ=self.environ,
                )
            self.assertFalse(result.eligible)
            self.assertEqual(result.reasons, (reason.value,))

        for reason in verification_failures:
            candidate = replace(
                eligible,
                eligible=False,
                reasons=(reason,),
                export_allowed=False,
                provider_request_binding_sha256=None,
                binding=None,
            )
            with (
                self.subTest(reason=reason.value),
                self.adapter_context(),
                mock.patch.object(
                    preflight_module,
                    "_evaluate_provider_as_of",
                    return_value=candidate,
                ),
                self.assertRaises(EntitlementPreflightError) as raised,
            ):
                run_entitlement_preflight(
                    self.config,
                    self.request,
                    environ=self.environ,
                )
            self.assertEqual(
                raised.exception.args,
                ("entitlement_preflight_failed",),
            )
            self.assertIsNone(raised.exception.__cause__)
            self.assertIsNone(raised.exception.__context__)

    def test_export_projection_matches_verified_publication_permission(self) -> None:
        self.assertFalse(self.preflight().export_allowed)

        def publication_permission(raw):
            raw["basis"] = "written_permission"
            raw["permitted_operations"].append("publication")

        bundle = self.builder.build(
            permission_change=publication_permission,
        )
        config = self.make_config(bundle=bundle)
        with self.adapter_context():
            result = run_entitlement_preflight(
                config,
                self.request,
                environ=self.environ,
            )
        self.assertTrue(result.eligible)
        self.assertTrue(result.export_allowed)

    def test_verification_failures_share_one_redacted_error_surface(self) -> None:
        def manifest_schema(raw):
            raw["schema_version"] = 2

        def permission_digest(raw):
            raw["permission"]["artifact_sha256"] = "0" * 64

        def evidence_digest(raw):
            raw["permission"]["evidence_sha256"] = "0" * 64

        def qualification_digest(raw):
            raw["qualification"]["artifact_sha256"] = "0" * 64

        def trace_digest(raw):
            raw["qualification"]["evidence_trace_sha256"] = "0" * 64

        def cross_binding(raw):
            raw["product_tier"] = "different-tier"

        bundles = (
            self.builder.build(manifest_change=manifest_schema),
            self.builder.build(manifest_change=permission_digest),
            self.builder.build(manifest_change=evidence_digest),
            self.builder.build(manifest_change=qualification_digest),
            self.builder.build(manifest_change=trace_digest),
            self.builder.build(permission_change=cross_binding),
        )
        for bundle in bundles:
            config = self.make_config(bundle=bundle)
            with (
                self.subTest(manifest=bundle["manifest_path"]),
                self.adapter_context(),
                self.assertRaises(EntitlementPreflightError) as raised,
            ):
                run_entitlement_preflight(
                    config,
                    self.request,
                    environ=self.environ,
                )
            error = raised.exception
            self.assertEqual(error.args, ("entitlement_preflight_failed",))
            self.assertEqual(str(error), "entitlement_preflight_failed")
            self.assertNotIn(str(bundle["manifest_path"]), repr(error))
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            self.assertFalse(hasattr(error, "__notes__"))

    def test_unexpected_errors_are_redacted_but_process_control_propagates(
        self,
    ) -> None:
        secret = "SECRET_INTERNAL_PATH_AND_PROVIDER_VALUE"
        with (
            mock.patch.object(
                preflight_module,
                "_load_provider_manifest_restricted",
                side_effect=ValueError(secret),
            ),
            self.assertRaises(EntitlementPreflightError) as raised,
        ):
            run_entitlement_preflight(
                self.config,
                self.request,
                environ={"SYNTHETIC_API_KEY": secret},
            )
        error = raised.exception
        for surface in (
            error.args,
            str(error),
            repr(error),
            getattr(error, "__notes__", ()),
            error.__cause__,
            error.__context__,
        ):
            self.assertNotIn(secret, repr(surface))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

        for process_control in (KeyboardInterrupt(), SystemExit(17)):
            with (
                self.subTest(exception=type(process_control).__name__),
                mock.patch.object(
                    preflight_module,
                    "_load_provider_manifest_restricted",
                    side_effect=process_control,
                ),
                self.assertRaises(type(process_control)),
            ):
                run_entitlement_preflight(
                    self.config,
                    self.request,
                    environ=self.environ,
                )

    def test_preflight_result_is_rejected_by_decision_authority_boundaries(
        self,
    ) -> None:
        manifest, decision = self.loaded_manifest_and_decision()
        result = self.preflight()
        session_id = "1f8b7b52-fdad-4dc1-a7a1-c2b1d4afaa12"
        with self.assertRaises(TypeError):
            build_session_manifest(
                config=self.config,
                provider_manifest=manifest,
                qualification=result,  # type: ignore[arg-type]
                session_id=session_id,
                created_wall_ns=1,
                code_sha256="1" * 64,
            )
        with self.assertRaises(TypeError):
            require_decision_matches_session(
                result,  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
            )
        with self.assertRaises(Exception):
            provider_request_binding_sha256(result)  # type: ignore[arg-type]

        session_manifest = build_session_manifest(
            config=self.config,
            provider_manifest=manifest,
            qualification=decision,
            session_id=session_id,
            created_wall_ns=1,
            code_sha256="1" * 64,
        )
        marker = retention_module.RetentionMarker(
            schema_version=1,
            session_id=session_id,
            wal_basename=retention_module._wal_basename(session_id),
            reserve_basename=retention_module._reserve_basename(session_id),
            delete_by_ns=session_manifest.required_retention_until_ns,
            session_manifest_sha256=session_manifest_sha256(
                session_manifest
            ),
            provider_request_binding_sha256=(
                decision.provider_request_binding_sha256
            ),
            provider_manifest_file_sha256=(
                session_manifest.provider_manifest_file_sha256
            ),
            entitlement_id_sha256=(
                session_manifest.entitlement_id_sha256
            ),
            qualification_artifact_sha256=(
                session_manifest.qualification_artifact_sha256
            ),
            created_at_ns=1,
        )
        with (
            mock.patch.object(
                retention_module,
                "session_manifest_sha256",
                side_effect=AssertionError("retention state inspected"),
            ) as retention_state,
            mock.patch.object(
                retention_module,
                "provider_request_binding_sha256",
                side_effect=AssertionError("retention binding inspected"),
            ) as retention_binding,
            self.assertRaises(TypeError),
        ):
            retention_module._manifest_matches_marker(
                session_manifest,
                result,  # type: ignore[arg-type]
                marker,
            )
        retention_state.assert_not_called()
        retention_binding.assert_not_called()

        with (
            mock.patch.object(
                wal_module,
                "_claim_provider_wal_reader",
                side_effect=AssertionError("WAL authority dispatched"),
            ) as wal_claim,
            self.assertRaises(TypeError),
        ):
            wal_module.JournalReader.create(
                read_capability=result,  # type: ignore[arg-type]
            )
        wal_claim.assert_not_called()

        ingress = ingress_module.BoundedIngress(
            capacity=1,
            producer_timeout_seconds=1.0,
            receipt_timeout_seconds=1.0,
        )
        self.assertTrue(ingress._queue.empty())
        with (
            mock.patch.object(
                ingress_module,
                "_validate_exact_ingress_item",
                side_effect=AssertionError("ingress item dispatched"),
            ) as ingress_validate,
            self.assertRaises(TypeError),
        ):
            ingress.enqueue(result)  # type: ignore[arg-type]
        ingress_validate.assert_not_called()
        self.assertTrue(ingress._queue.empty())
        self.assertIsNone(ingress.halt_reason)

        hostile_coordinator = mock.MagicMock()
        hostile_session = mock.MagicMock()
        with self.assertRaises(TypeError):
            sequencer_module.bind_provider_persistence_authorizer(
                gate=result,  # type: ignore[arg-type]
                coordinator=hostile_coordinator,
                session_manifest=hostile_session,
            )
        self.assertEqual(hostile_coordinator.mock_calls, [])
        self.assertEqual(hostile_session.mock_calls, [])


class DisabledExampleTests(unittest.TestCase):
    def test_task5_readme_section_preserves_task7_and_states_exact_boundary(
        self,
    ) -> None:
        text = README_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "## Local entitlement preflight and external artifacts",
            text,
        )
        self.assertIn(
            "## Tennis v1 Phase 1 Research-Only Boundary",
            text,
        )
        lowered = " ".join(text.lower().split())
        for meaning in (
            "production adapter registry is empty",
            "cannot currently succeed",
            "schema documentation only",
            "grants no authority",
            "outside git",
            "independently digest-pinned",
            "no trial starts",
            "auto-upgrades",
            "subscribes",
            "access, analysis, qualification, and physical-retention deadlines",
            "read-only",
            "touches no state root",
            "not runtime startup",
            "an eligible diagnostic grants no network, session, retention, or wal authority",
        ):
            with self.subTest(meaning=meaning):
                self.assertIn(meaning, lowered)

    def test_example_has_exact_synthetic_disabled_schema(self) -> None:
        raw = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            set(raw),
            {
                "schema_version",
                "provider_id",
                "product_tier",
                "entitlement_id",
                "source_lineage_id",
                "terms_url",
                "terms_version",
                "permission",
                "billing_mode",
                "auto_renew",
                "access_starts_at",
                "access_expires_at",
                "analysis_expires_at",
                "raw_retention_until",
                "max_raw_retention_seconds",
                "credential_env_names",
                "quotas",
                "capabilities",
                "qualification",
            },
        )
        self.assertEqual(raw["schema_version"], 1)
        self.assertEqual(raw["provider_id"], "EXAMPLE_DISABLED")
        self.assertEqual(raw["product_tier"], "UNQUALIFIED_TEMPLATE")
        self.assertEqual(raw["entitlement_id"], "NO_ENTITLEMENT")
        self.assertEqual(raw["source_lineage_id"], "EXAMPLE_DISABLED_LINEAGE")
        self.assertEqual(raw["terms_url"], "https://example.invalid/disabled")
        self.assertEqual(raw["terms_version"], "UNQUALIFIED_TEMPLATE")
        self.assertEqual(raw["billing_mode"], "trial")
        self.assertIs(raw["auto_renew"], False)
        self.assertEqual(raw["credential_env_names"], [])
        self.assertEqual(raw["access_starts_at"], "2000-01-01T00:00:00Z")
        self.assertEqual(raw["access_expires_at"], "2000-01-02T00:00:00Z")
        self.assertEqual(raw["analysis_expires_at"], "2000-01-02T00:00:00Z")
        self.assertEqual(raw["raw_retention_until"], "2000-01-03T00:00:00Z")
        self.assertEqual(raw["max_raw_retention_seconds"], 172800)
        self.assertEqual(
            set(raw["permission"]),
            {
                "artifact_path",
                "artifact_sha256",
                "evidence_path",
                "evidence_sha256",
            },
        )
        self.assertEqual(
            set(raw["qualification"]),
            {
                "artifact_path",
                "artifact_sha256",
                "evidence_trace_path",
                "evidence_trace_sha256",
            },
        )
        for reference in (raw["permission"], raw["qualification"]):
            for key, value in reference.items():
                if key.endswith("_sha256"):
                    self.assertEqual(value, "0" * 64)
                else:
                    self.assertTrue(value.startswith("DISABLED_DO_NOT_CREATE/"))
        self.assertEqual(
            set(raw["quotas"]),
            {
                "requests_per_rolling_60_seconds",
                "requests_per_utc_calendar_day",
                "requests_per_rolling_second",
                "max_connections",
                "max_subscriptions",
                "resync_requests_per_rolling_hour",
            },
        )
        self.assertTrue(all(value == 1 for value in raw["quotas"].values()))
        capabilities = raw["capabilities"]
        self.assertEqual(
            set(capabilities),
            {
                "stable_match_ids",
                "stable_player_ids",
                "point_state",
                "current_server",
                "match_format",
                "source_event_time",
                "provider_generated_time",
                "monotonic_sequence_or_revision",
                "correction_semantics",
                "resync_snapshot",
                "supported_formats",
                "declared_strata",
            },
        )
        self.assertEqual(
            capabilities["supported_formats"],
            ["rest_json"],
        )
        self.assertEqual(
            capabilities["declared_strata"],
            [
                {
                    "sport": "tennis",
                    "tour": "SYNTHETIC",
                    "competition_tier": "SYNTHETIC",
                    "match_format": "BEST_OF_3",
                    "round_code": "SYNTHETIC",
                }
            ],
        )
        self.assertEqual(
            set(capabilities["declared_strata"][0]),
            {
                "sport",
                "tour",
                "competition_tier",
                "match_format",
                "round_code",
            },
        )
        boolean_capabilities = {
            key: value
            for key, value in capabilities.items()
            if key not in {"supported_formats", "declared_strata"}
        }
        self.assertEqual(len(boolean_capabilities), 10)
        self.assertTrue(all(value is False for value in boolean_capabilities.values()))

        recursive_keys: set[str] = set()

        def collect_keys(value: object) -> None:
            if type(value) is dict:
                recursive_keys.update(value)
                for nested in value.values():
                    collect_keys(nested)
            elif type(value) is list:
                for nested in value:
                    collect_keys(nested)

        collect_keys(raw)
        self.assertTrue(
            recursive_keys.isdisjoint(
                {
                    "permission_granted",
                    "permission_basis",
                    "quota_demand",
                    "qualification_result",
                    "request_override",
                    "reviewer_id",
                    "issuer_id",
                    "approval_id",
                    "credential",
                    "api_key",
                    "token",
                    "private_key",
                    "adapter",
                    "registry",
                    "loader",
                    "callback",
                    "live",
                    "live_enabled",
                    "demo",
                    "demo_enabled",
                    "order",
                    "orders",
                    "order_url",
                }
            )
        )

    def test_example_is_not_runtime_loadable_and_has_no_special_case(self) -> None:
        digest = hashlib.sha256(EXAMPLE_PATH.read_bytes()).hexdigest()
        with self.assertRaises(ManifestError):
            load_provider_manifest(
                EXAMPLE_PATH,
                expected_sha256=digest,
                repo_root=REPOSITORY_ROOT,
            )
        for path in (REPOSITORY_ROOT / "tennis_v1").rglob("*.py"):
            self.assertNotIn(
                "EXAMPLE_DISABLED",
                path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
