from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

import tennis_v1.adapter_contract as adapter_contract
from tennis_v1.adapter_contract import (
    ADAPTER_CLOSURE_DOMAIN,
    AdapterContractError,
    AdapterUsagePlan,
    AuthContract,
    AuthMode,
    ProviderQuotas,
    _AdapterRegistration,
    _AdapterContractSpec,
    _capture_adapter_registration,
    derive_quota_demand,
    load_active_adapter_contract,
)
from tennis_v1.canonical import canonical_json_bytes


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
ADAPTER_FILE = FIXTURE_ROOT / "synthetic_adapter.py"


class _StringSubclass(str):
    pass


class _ObservedString(str):
    def __new__(cls, value: str, calls: list[str]):
        instance = super().__new__(cls, value)
        instance.calls = calls
        return instance

    def __hash__(self) -> int:
        self.calls.append("__hash__")
        return super().__hash__()

    def __eq__(self, other: object) -> bool:
        self.calls.append("__eq__")
        return super().__eq__(other)


class _TupleSubclass(tuple):
    pass


class _ExplodingTuple(tuple):
    def __ne__(self, other: object) -> bool:
        raise RuntimeError("MALFORMED_REGISTRY_EXECUTED")


class _ObservedRegistry(dict):
    def __init__(self, calls: list[str]):
        super().__init__()
        self.calls = calls

    def get(self, key, default=None):
        self.calls.append("get")
        raise RuntimeError("REGISTRY_GET_EXECUTED")


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


class AdapterContractTests(unittest.TestCase):
    def capture(
        self,
        spec: object,
        *,
        adapter_file: Path = ADAPTER_FILE,
        module_paths: tuple[str, ...] = ("synthetic_adapter.py",),
    ):
        with mock.patch.object(adapter_contract, "__file__", str(adapter_file)):
            return _capture_adapter_registration(
                module_paths=module_paths,
                spec=spec,
            )

    def registered(self, registration: object, *, adapter_file: Path = ADAPTER_FILE):
        return mock.patch.dict(
            adapter_contract._ADAPTER_REGISTRY,
            {("synthetic-provider", "trial-v1"): registration},
            clear=True,
        ), mock.patch.object(adapter_contract, "__file__", str(adapter_file))

    def load_synthetic(self):
        registration = self.capture(synthetic_spec())
        registry, source = self.registered(registration)
        with registry, source:
            return load_active_adapter_contract(
                provider_id="synthetic-provider",
                product_tier="trial-v1",
            )

    def test_production_registry_is_empty_and_unknown_provider_fails_closed(self):
        self.assertEqual(adapter_contract._ADAPTER_REGISTRY, {})
        with self.assertRaises(AdapterContractError):
            load_active_adapter_contract(
                provider_id="missing",
                product_tier="trial",
            )

    def test_registration_and_load_do_not_import_or_execute_adapter_source(self):
        module_keys = set(sys.modules)
        first = self.load_synthetic()
        second = self.load_synthetic()
        self.assertEqual(first, second)
        self.assertEqual(set(sys.modules), module_keys)
        self.assertEqual(first.adapter_id, "synthetic-read-only-v1")

    def test_registration_rejects_callable_module_and_mutable_spec_values(self):
        invalid = (
            lambda: None,
            ModuleType("synthetic_invalid"),
            {},
            SimpleNamespace(provider_id="synthetic-provider"),
        )
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(AdapterContractError):
                    self.capture(value)

    def test_spec_requires_exact_frozen_contract_value_types(self):
        invalid_specs = (
            object(),
            {"provider_id": "synthetic-provider"},
            replace(synthetic_spec(), auth=object()),
            replace(synthetic_spec(), usage=object()),
            replace(synthetic_spec(), formats=["rest_json"]),
            replace(synthetic_spec(), provider_id=_StringSubclass("synthetic-provider")),
            replace(synthetic_spec(), adapter_id=_StringSubclass("synthetic-read-only-v1")),
            replace(
                synthetic_spec(),
                auth=AuthContract(AuthMode.API_KEY, (_StringSubclass("SYNTHETIC_API_KEY"),)),
            ),
            replace(
                synthetic_spec(),
                auth=AuthContract(AuthMode.API_KEY, _TupleSubclass(("SYNTHETIC_API_KEY",))),
            ),
            replace(synthetic_spec(), formats=_TupleSubclass(("rest_json",))),
        )
        for value in invalid_specs:
            with self.subTest(value=repr(type(value))):
                with self.assertRaises(AdapterContractError):
                    self.capture(value)

    def test_loaded_contract_and_nested_declarations_are_frozen(self):
        loaded = self.load_synthetic()
        with self.assertRaises(FrozenInstanceError):
            loaded.adapter_id = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            loaded.auth.mode = AuthMode.PUBLIC  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            loaded.usage.max_connections = 2  # type: ignore[misc]

    def test_registry_identity_mismatch_fails_closed(self):
        registration = self.capture(
            synthetic_spec(provider_id="different-provider")
        )
        registry, source = self.registered(registration)
        with registry, source:
            with self.assertRaisesRegex(
                AdapterContractError,
                "adapter contract identity mismatch",
            ):
                load_active_adapter_contract(
                    provider_id="synthetic-provider",
                    product_tier="trial-v1",
                )

    def test_malformed_registry_pins_fail_closed_without_comparison_dispatch(self):
        registration = self.capture(synthetic_spec())
        malformed = _AdapterRegistration(
            module_paths=registration.module_paths,
            spec=registration.spec,
            expected_entries=_ExplodingTuple(registration.expected_entries),
        )
        registry, source = self.registered(malformed)
        with registry, source:
            with self.assertRaisesRegex(
                AdapterContractError,
                "adapter registration pins are invalid",
            ):
                load_active_adapter_contract(
                    provider_id="synthetic-provider",
                    product_tier="trial-v1",
                )

    def test_registry_dict_subclass_fails_closed_before_get_dispatch(self):
        calls: list[str] = []
        registry = _ObservedRegistry(calls)
        contract = None
        error = None
        with mock.patch.object(adapter_contract, "_ADAPTER_REGISTRY", registry):
            try:
                contract = load_active_adapter_contract(
                    provider_id="synthetic-provider",
                    product_tier="trial-v1",
                )
            except Exception as caught:
                error = caught
        self.assertIs(type(error), AdapterContractError)
        self.assertEqual(calls, [])
        self.assertIsNone(contract)

    def test_registry_str_subclass_key_fails_closed_without_equality_dispatch(self):
        registration = self.capture(synthetic_spec())
        calls: list[str] = []
        provider = _ObservedString("synthetic-provider", calls)
        tier = _ObservedString("trial-v1", calls)
        registry = {(provider, tier): registration}
        calls.clear()
        contract = None
        error = None
        with (
            mock.patch.object(adapter_contract, "_ADAPTER_REGISTRY", registry),
            mock.patch.object(adapter_contract, "__file__", str(ADAPTER_FILE)),
        ):
            try:
                contract = load_active_adapter_contract(
                    provider_id="synthetic-provider",
                    product_tier="trial-v1",
                )
            except Exception as caught:
                error = caught
        self.assertIs(type(error), AdapterContractError)
        self.assertEqual(calls, [])
        self.assertIsNone(contract)

    def test_source_change_after_registration_fails_before_any_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            adapter_file = Path(temporary) / "adapter.py"
            adapter_file.write_bytes(ADAPTER_FILE.read_bytes())
            registration = self.capture(
                synthetic_spec(),
                adapter_file=adapter_file,
                module_paths=("adapter.py",),
            )
            adapter_file.write_bytes(
                adapter_file.read_bytes() + b"\n# changed after registration\n"
            )
            registry, source = self.registered(registration, adapter_file=adapter_file)
            with registry, source:
                with self.assertRaisesRegex(
                    AdapterContractError,
                    "active adapter files differ",
                ):
                    load_active_adapter_contract(
                        provider_id="synthetic-provider",
                        product_tier="trial-v1",
                    )

    def test_source_change_during_closure_read_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            adapter_file = Path(temporary) / "adapter.py"
            adapter_file.write_bytes(ADAPTER_FILE.read_bytes())
            registration = self.capture(
                synthetic_spec(),
                adapter_file=adapter_file,
                module_paths=("adapter.py",),
            )
            original_read = adapter_contract._read_component
            reads = 0

            def change_after_read(*args, **kwargs):
                nonlocal reads
                content, identity = original_read(*args, **kwargs)
                reads += 1
                if reads == 2:
                    adapter_file.write_bytes(content + b"\n# changed during read\n")
                return content, identity

            registry, source = self.registered(registration, adapter_file=adapter_file)
            with registry, source, mock.patch.object(
                adapter_contract,
                "_read_component",
                side_effect=change_after_read,
            ):
                with self.assertRaises(AdapterContractError):
                    load_active_adapter_contract(
                        provider_id="synthetic-provider",
                        product_tier="trial-v1",
                    )

    def test_active_adapter_digest_is_exact_closed_file_hash(self):
        loaded = self.load_synthetic()
        content = ADAPTER_FILE.read_bytes()
        expected = hashlib.sha256(
            ADAPTER_CLOSURE_DOMAIN
            + canonical_json_bytes(
                [
                    {
                        "path": "synthetic_adapter.py",
                        "length": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                ]
            )
        ).hexdigest()
        self.assertEqual(loaded.adapter_code_sha256, expected)

    def test_closed_file_set_rejects_missing_symlink_hardlink_and_unexpected_python(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "adapter_contract.py"
            marker.write_text("# root marker\n", encoding="utf-8")
            adapter = root / "adapter.py"
            adapter.write_text("# inert\n", encoding="utf-8")
            with self.assertRaises(AdapterContractError):
                self.capture(synthetic_spec(), adapter_file=marker, module_paths=("missing.py",))
            (root / "extra.py").write_text("# unexpected\n", encoding="utf-8")
            with self.assertRaises(AdapterContractError):
                self.capture(synthetic_spec(), adapter_file=marker, module_paths=("adapter.py",))
            (root / "extra.py").unlink()
            (root / "link.py").symlink_to(adapter)
            with self.assertRaises(AdapterContractError):
                self.capture(synthetic_spec(), adapter_file=marker, module_paths=("link.py",))
            (root / "link.py").unlink()
            (root / "hard.py").hardlink_to(adapter)
            with self.assertRaises(AdapterContractError):
                self.capture(synthetic_spec(), adapter_file=marker, module_paths=("adapter.py", "hard.py"))

    def test_paths_and_nested_python_files_fail_closed(self):
        for paths in (
            ("adapter.py", "adapter.py"),
            ("../adapter.py",),
            ("/adapter.py",),
            ("z.py", "a.py"),
        ):
            with self.subTest(paths=paths):
                with self.assertRaises(AdapterContractError):
                    self.capture(synthetic_spec(), module_paths=paths)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "adapter_contract.py"
            marker.write_text("# root marker\n", encoding="utf-8")
            package = root / "pkg"
            package.mkdir()
            (package / "adapter.py").write_text("# inert\n", encoding="utf-8")
            nested = package / "nested"
            nested.mkdir()
            (nested / "extra.py").write_text("# unexpected\n", encoding="utf-8")
            with self.assertRaises(AdapterContractError):
                self.capture(
                    synthetic_spec(),
                    adapter_file=marker,
                    module_paths=("pkg/adapter.py",),
                )

    def test_paths_reject_str_subclass_before_hash_or_comparison_dispatch(self):
        calls: list[str] = []
        path = _ObservedString("synthetic_adapter.py", calls)
        with self.assertRaises(AdapterContractError):
            self.capture(synthetic_spec(), module_paths=(path,))
        self.assertEqual(calls, [])

    def test_invalid_auth_usage_and_formats_fail_closed(self):
        invalid_specs = (
            replace(synthetic_spec(), auth=AuthContract(AuthMode.PUBLIC, ("A",))),
            replace(synthetic_spec(), auth=AuthContract(AuthMode.API_KEY, ())),
            replace(synthetic_spec(), auth=AuthContract(AuthMode.API_KEY, ("B", "A"))),
            replace(synthetic_spec(), usage=replace(synthetic_spec().usage, max_connections=0)),
            replace(synthetic_spec(), usage=replace(synthetic_spec().usage, startup_requests_fixed=True)),
            replace(synthetic_spec(), formats=()),
            replace(synthetic_spec(), formats=("websocket_json", "rest_json")),
            replace(synthetic_spec(), formats=("rest_json", "rest_json")),
            replace(synthetic_spec(), formats=("unknown",)),
        )
        for value in invalid_specs:
            with self.subTest(value=value):
                with self.assertRaises(AdapterContractError):
                    self.capture(value)

    def test_usage_projection_rejects_subclass_before_any_field_getter(self):
        class HostileUsage(AdapterUsagePlan):
            touches = 0

            def __getattribute__(self, name):
                if name in AdapterUsagePlan.__dataclass_fields__:
                    type(self).touches += 1
                    raise AssertionError("hostile usage getter executed")
                return super().__getattribute__(name)

        hostile = object.__new__(HostileUsage)
        with self.assertRaisesRegex(
            AdapterContractError,
            r"\Aadapter usage contract is invalid\Z",
        ):
            adapter_contract._usage_projection(hostile)
        self.assertEqual(HostileUsage.touches, 0)

    def test_quota_demand_uses_integer_fail_closed_cluster_and_calendar_day_math(self):
        adapter = self.load_synthetic()
        request = SimpleNamespace(
            requested_matches=2,
            now_utc=datetime(2026, 7, 27, 23, 59, 30, tzinfo=timezone.utc),
            session_end_utc=datetime(2026, 7, 28, 0, 0, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            derive_quota_demand(adapter, request),
            ProviderQuotas(12, 12, 12, 1, 2, 4),
        )

    def test_quota_demand_supports_large_integer_demand_exactly(self):
        adapter = self.load_synthetic()
        matches = 10**100
        request = SimpleNamespace(
            requested_matches=matches,
            now_utc=datetime(2026, 7, 27, tzinfo=timezone.utc),
            session_end_utc=datetime(2026, 7, 27, 0, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(
            derive_quota_demand(adapter, request),
            ProviderQuotas(
                requests_per_rolling_60_seconds=5 * matches + 2,
                requests_per_utc_calendar_day=5 * matches + 2,
                requests_per_rolling_second=5 * matches + 2,
                max_connections=1,
                max_subscriptions=matches,
                resync_requests_per_rolling_hour=2 * matches,
            ),
        )

    def test_quota_demand_handles_datetime_max_without_overflow(self):
        adapter = self.load_synthetic()
        request = SimpleNamespace(
            requested_matches=1,
            now_utc=datetime(9999, 12, 31, 23, 59, tzinfo=timezone.utc),
            session_end_utc=datetime.max.replace(tzinfo=timezone.utc),
        )
        self.assertEqual(
            derive_quota_demand(adapter, request),
            ProviderQuotas(7, 7, 7, 1, 1, 2),
        )

    def test_quota_demand_rejects_boolean_negative_and_nonpositive_capacity(self):
        base = self.load_synthetic()
        request = SimpleNamespace(
            requested_matches=1,
            now_utc=datetime(2026, 7, 27, tzinfo=timezone.utc),
            session_end_utc=datetime(2026, 7, 27, 0, 1, tzinfo=timezone.utc),
        )
        values = {name: getattr(base.usage, name) for name in AdapterUsagePlan.__dataclass_fields__}
        for field, invalid in (("startup_requests_fixed", True), ("resync_requests_per_match", -1), ("max_connections", 0), ("subscriptions_per_match", 0)):
            with self.subTest(field=field):
                changed = dict(values)
                changed[field] = invalid
                bad = replace(base, usage=AdapterUsagePlan(**changed))
                with self.assertRaises(AdapterContractError):
                    derive_quota_demand(bad, request)

    def test_quota_demand_rejects_invalid_request_shape_and_interval(self):
        adapter = self.load_synthetic()
        start = datetime(2026, 7, 27, tzinfo=timezone.utc)
        for request in (
            SimpleNamespace(requested_matches=True, now_utc=start, session_end_utc=start.replace(minute=1)),
            SimpleNamespace(requested_matches=1, now_utc=start, session_end_utc=start),
            SimpleNamespace(requested_matches=1, now_utc=start.replace(tzinfo=None), session_end_utc=start.replace(minute=1)),
        ):
            with self.subTest(request=request):
                with self.assertRaises(AdapterContractError):
                    derive_quota_demand(adapter, request)


if __name__ == "__main__":
    unittest.main()
