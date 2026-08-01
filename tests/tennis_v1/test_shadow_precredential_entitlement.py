from __future__ import annotations

import ast
import copy
from dataclasses import FrozenInstanceError, fields
import hashlib
import inspect
import os
import pickle
import re
import threading
from types import MappingProxyType
import unittest
from unittest import mock

import tennis_v1.entitlements as entitlements
from tennis_v1.canonical import canonical_json_bytes
from tennis_v1.entitlements import (
    FrozenKalshiTransportCredentialViewV1,
    FrozenProviderGateEvaluationCredentialViewV1,
    FrozenProviderTransportCredentialViewV1,
    FrozenShadowCredentialsV1,
    PrecredentialProviderEntitlementV1,
    ProviderManifest,
    ResearchRequest,
    SealedCredentialLifecycleOracleAuthorityV1,
    SealedCredentialLifecycleOracleResultV1,
    SealedCredentialOracleCaseV1,
    ShadowCredentialContractAuthorityV1,
    claim_frozen_kalshi_transport_credential_view_v1,
    claim_frozen_provider_gate_evaluation_credential_view_v1,
    claim_frozen_provider_transport_credential_view_v1,
    evaluate_provider_precredential,
    finalize_provider_entitlement_v1,
    read_frozen_shadow_credentials_v1,
    revoke_frozen_shadow_credentials_v1,
    revoke_provider_precredential_v1,
    _issue_sealed_credential_lifecycle_oracle_authority_v1,
    _run_sealed_credential_lifecycle_oracle_v1,
)
from tennis_v1.config import TennisV1Config


PROVIDER_NAME = "INCI_TEST_PROVIDER_CREDENTIAL_SENTINEL"
KALSHI_NAME = "INCI_TEST_KALSHI_CREDENTIAL_SENTINEL"
PROVIDER_VALUE = "VISIBLE_NONSECRET_PROVIDER_TEST_VALUE"
KALSHI_VALUE = "VISIBLE_NONSECRET_KALSHI_TEST_VALUE"
FIXTURE_ENVIRONMENT = {
    PROVIDER_NAME: PROVIDER_VALUE,
    KALSHI_NAME: KALSHI_VALUE,
}


SUCCESS_CASES = (
    SealedCredentialOracleCaseV1.EXACT_SENTINEL_UNION_SUCCESS,
    SealedCredentialOracleCaseV1.POST_FREEZE_AMBIENT_DRIFT,
    SealedCredentialOracleCaseV1.PRECREDENTIAL_CONSUME,
    SealedCredentialOracleCaseV1.GATE_POLL_WITHOUT_REREAD,
    SealedCredentialOracleCaseV1.VIEWS_GATE_PROVIDER_KALSHI,
    SealedCredentialOracleCaseV1.VIEWS_GATE_KALSHI_PROVIDER,
    SealedCredentialOracleCaseV1.VIEWS_PROVIDER_GATE_KALSHI,
    SealedCredentialOracleCaseV1.VIEWS_PROVIDER_KALSHI_GATE,
    SealedCredentialOracleCaseV1.VIEWS_KALSHI_GATE_PROVIDER,
    SealedCredentialOracleCaseV1.VIEWS_KALSHI_PROVIDER_GATE,
    SealedCredentialOracleCaseV1.GATE_ONLY_THEN_TRANSPORTS,
    SealedCredentialOracleCaseV1.PARTIAL_TRANSFER_THEN_REVOKE,
    SealedCredentialOracleCaseV1.VIEW_REPEAT_REJECTED,
    SealedCredentialOracleCaseV1.VIEW_CROSS_ROLE_REJECTED,
    SealedCredentialOracleCaseV1.VIEW_REBUILT_REJECTED,
    SealedCredentialOracleCaseV1.VIEW_FOREIGN_REJECTED,
    SealedCredentialOracleCaseV1.WRONG_OWNER_REJECTED,
)


_PRODUCTION_VIEW_ROLES = {
    "gate": (
        "gate_view",
        "FrozenProviderGateEvaluationCredentialViewV1",
        "claim_frozen_provider_gate_evaluation_credential_view_v1",
    ),
    "provider": (
        "provider_view",
        "FrozenProviderTransportCredentialViewV1",
        "claim_frozen_provider_transport_credential_view_v1",
    ),
    "kalshi": (
        "kalshi_view",
        "FrozenKalshiTransportCredentialViewV1",
        "claim_frozen_kalshi_transport_credential_view_v1",
    ),
}


def _top_level_definition(
    tree: ast.Module,
    name: str,
    definition_type: type[ast.ClassDef] | type[ast.FunctionDef],
) -> ast.ClassDef | ast.FunctionDef:
    matches = [
        node
        for node in tree.body
        if type(node) is definition_type and node.name == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one top-level {name}, got {len(matches)}")
    return matches[0]


def _ast_shape(node: ast.AST) -> str:
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _expression_shape(source: str) -> str:
    return _ast_shape(ast.parse(source, mode="eval").body)


def _record_constructor_calls(issuer: ast.FunctionDef) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(issuer)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_FrozenCredentialViewRecord"
    ]


def _compare_shape_exists(function: ast.FunctionDef, source: str) -> bool:
    expected = _expression_shape(source)
    return any(
        isinstance(node, ast.Compare) and _ast_shape(node) == expected
        for node in ast.walk(function)
    )


def _assert_production_view_identity_contract(tree: ast.Module) -> None:
    record_class = _top_level_definition(
        tree,
        "_FrozenCredentialViewRecord",
        ast.ClassDef,
    )
    assert isinstance(record_class, ast.ClassDef)
    view_fields = [
        node
        for node in record_class.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "view"
    ]
    if len(view_fields) != 1:
        raise AssertionError("record must define exactly one view identity field")
    view_field = view_fields[0]
    if _ast_shape(view_field.annotation) != _expression_shape(
        "weakref.ReferenceType[object]"
    ):
        raise AssertionError("record view identity field must be an exact weakref")
    if not (
        isinstance(view_field.value, ast.Call)
        and isinstance(view_field.value.func, ast.Name)
        and view_field.value.func.id == "field"
        and not view_field.value.args
        and len(view_field.value.keywords) == 1
        and view_field.value.keywords[0].arg == "repr"
        and isinstance(view_field.value.keywords[0].value, ast.Constant)
        and view_field.value.keywords[0].value.value is False
    ):
        raise AssertionError("record view identity field must be redacted from repr")

    issuer = _top_level_definition(
        tree,
        "read_frozen_shadow_credentials_v1",
        ast.FunctionDef,
    )
    assert isinstance(issuer, ast.FunctionDef)
    views_assignments = [
        node
        for node in ast.walk(issuer)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "views"
        and isinstance(node.value, ast.Dict)
    ]
    if len(views_assignments) != 1:
        raise AssertionError("issuer must define one ruled three-role view map")
    views_dict = views_assignments[0].value
    role_map = {
        key.value: value.id
        for key, value in zip(views_dict.keys, views_dict.values, strict=True)
        if isinstance(key, ast.Constant)
        and isinstance(key.value, str)
        and isinstance(value, ast.Name)
    }
    expected_role_map = {
        role: specification[0]
        for role, specification in _PRODUCTION_VIEW_ROLES.items()
    }
    if role_map != expected_role_map or len(views_dict.keys) != len(role_map):
        raise AssertionError("issuer view map must cover exactly gate/provider/kalshi")

    view_loops = [
        node
        for node in ast.walk(issuer)
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Tuple)
        and len(node.target.elts) == 2
        and all(isinstance(element, ast.Name) for element in node.target.elts)
        and tuple(element.id for element in node.target.elts) == ("role", "view")
        and _ast_shape(node.iter) == _expression_shape("views.items()")
    ]
    if len(view_loops) != 1:
        raise AssertionError("issuer must issue every ruled role through one view loop")
    constructors = _record_constructor_calls(view_loops[0])
    if len(constructors) != 1:
        raise AssertionError("issuer loop must create one record per ruled view")
    keywords = {keyword.arg: keyword.value for keyword in constructors[0].keywords}
    view_binding = keywords.get("view")
    if view_binding is None or _ast_shape(view_binding) != _expression_shape(
        "weakref.ref(view)"
    ):
        raise AssertionError("issuer record must bind weakref identity to issued view")
    role_binding = keywords.get("role")
    if role_binding is None or _ast_shape(role_binding) != _expression_shape("role"):
        raise AssertionError("issuer record must bind the loop's ruled role")

    for role, (_, expected_type, claim_name) in _PRODUCTION_VIEW_ROLES.items():
        claim = _top_level_definition(tree, claim_name, ast.FunctionDef)
        assert isinstance(claim, ast.FunctionDef)
        claim_calls = [
            node
            for node in ast.walk(claim)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_claim_frozen_credential_view"
        ]
        expected_call = _expression_shape(
            f'_claim_frozen_credential_view(credentials, "{role}")'
        )
        if len(claim_calls) != 1 or _ast_shape(claim_calls[0]) != expected_call:
            raise AssertionError(f"{role} claim must select its exact ruled role")
        expected_assert = _expression_shape(f"type(view) is {expected_type}")
        if not any(
            isinstance(node, ast.Assert)
            and _ast_shape(node.test) == expected_assert
            for node in claim.body
        ):
            raise AssertionError(f"{role} claim must enforce its exact view type")

    validator = _top_level_definition(
        tree,
        "_require_frozen_view_record",
        ast.FunctionDef,
    )
    assert isinstance(validator, ast.FunctionDef)
    if not _compare_shape_exists(
        validator,
        "type(record.view) is not weakref.ReferenceType",
    ):
        raise AssertionError("validator must require the exact weakref runtime type")
    if not _compare_shape_exists(validator, "record.view() is not value"):
        raise AssertionError("validator must require referent identity")


class ShadowPrecredentialSurfaceTests(unittest.TestCase):
    def test_production_three_view_identity_contract_is_structurally_complete(
        self,
    ) -> None:
        tree = ast.parse(inspect.getsource(entitlements))

        missing_field = copy.deepcopy(tree)
        record_class = _top_level_definition(
            missing_field,
            "_FrozenCredentialViewRecord",
            ast.ClassDef,
        )
        assert isinstance(record_class, ast.ClassDef)
        record_class.body = [
            node
            for node in record_class.body
            if not (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "view"
            )
        ]
        with self.assertRaisesRegex(AssertionError, "view identity field"):
            _assert_production_view_identity_contract(missing_field)

        missing_binding = copy.deepcopy(tree)
        issuer = _top_level_definition(
            missing_binding,
            "read_frozen_shadow_credentials_v1",
            ast.FunctionDef,
        )
        assert isinstance(issuer, ast.FunctionDef)
        constructor = _record_constructor_calls(issuer)[0]
        constructor.keywords = [
            keyword for keyword in constructor.keywords if keyword.arg != "view"
        ]
        with self.assertRaisesRegex(AssertionError, "bind weakref identity"):
            _assert_production_view_identity_contract(missing_binding)

        missing_referent_check = copy.deepcopy(tree)
        validator = _top_level_definition(
            missing_referent_check,
            "_require_frozen_view_record",
            ast.FunctionDef,
        )
        assert isinstance(validator, ast.FunctionDef)
        referent_comparisons = [
            node
            for node in ast.walk(validator)
            if isinstance(node, ast.Compare)
            and _ast_shape(node) == _expression_shape("record.view() is not value")
        ]
        self.assertEqual(len(referent_comparisons), 1)
        referent_comparisons[0].ops = [ast.Is()]
        with self.assertRaisesRegex(AssertionError, "referent identity"):
            _assert_production_view_identity_contract(missing_referent_check)

        missing_role = copy.deepcopy(tree)
        issuer = _top_level_definition(
            missing_role,
            "read_frozen_shadow_credentials_v1",
            ast.FunctionDef,
        )
        assert isinstance(issuer, ast.FunctionDef)
        views_assignment = next(
            node
            for node in ast.walk(issuer)
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "views"
        )
        assert isinstance(views_assignment.value, ast.Dict)
        views_assignment.value.keys.pop()
        views_assignment.value.values.pop()
        with self.assertRaisesRegex(AssertionError, "gate/provider/kalshi"):
            _assert_production_view_identity_contract(missing_role)

        _assert_production_view_identity_contract(tree)

    def test_production_signatures_have_no_caller_environment_clock_adapter_or_names(
        self,
    ) -> None:
        self.assertEqual(
            tuple(inspect.signature(evaluate_provider_precredential).parameters),
            ("config", "manifest", "request", "credential_contract_authority"),
        )
        self.assertEqual(
            tuple(inspect.signature(read_frozen_shadow_credentials_v1).parameters),
            ("precredential",),
        )
        self.assertEqual(
            tuple(inspect.signature(finalize_provider_entitlement_v1).parameters),
            ("precredential", "gate_credentials"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    _issue_sealed_credential_lifecycle_oracle_authority_v1
                ).parameters
            ),
            ("case",),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    _run_sealed_credential_lifecycle_oracle_v1
                ).parameters
            ),
            ("authority",),
        )
        for function in (
            evaluate_provider_precredential,
            read_frozen_shadow_credentials_v1,
            finalize_provider_entitlement_v1,
        ):
            parameters = tuple(inspect.signature(function).parameters)
            for forbidden in (
                "environ",
                "clock",
                "now",
                "adapter",
                "provider_credential_env_names",
                "kalshi_credential_env_names",
            ):
                self.assertNotIn(forbidden, parameters)

    def test_production_authority_is_nonconstructible_and_has_no_issuer(self) -> None:
        with self.assertRaisesRegex(TypeError, "bootstrap-issued"):
            ShadowCredentialContractAuthorityV1()
        with self.assertRaisesRegex(TypeError, "cannot be subclassed"):
            class _Subclass(ShadowCredentialContractAuthorityV1):
                pass

        issuer_names = {
            name
            for name in vars(entitlements)
            if "credential_contract_authority" in name.casefold()
            and ("issue" in name.casefold() or "create" in name.casefold())
        }
        self.assertEqual(issuer_names, set())
        source = inspect.getsource(entitlements)
        self.assertNotRegex(
            source,
            r"object\.__new__\(ShadowCredentialContractAuthorityV1\)",
        )

    def test_production_capabilities_are_exact_opaque_redacted_and_noncopyable(
        self,
    ) -> None:
        opaque_types = (
            ShadowCredentialContractAuthorityV1,
            FrozenShadowCredentialsV1,
            FrozenProviderGateEvaluationCredentialViewV1,
            FrozenProviderTransportCredentialViewV1,
            FrozenKalshiTransportCredentialViewV1,
        )
        for capability_type in opaque_types:
            with self.subTest(capability_type=capability_type.__name__):
                rebuilt = object.__new__(capability_type)
                self.assertIn("redacted", repr(rebuilt).casefold())
                with self.assertRaises(TypeError):
                    copy.copy(rebuilt)
                with self.assertRaises(TypeError):
                    copy.deepcopy(rebuilt)
                with self.assertRaises((TypeError, pickle.PicklingError)):
                    pickle.dumps(rebuilt)
                with self.assertRaisesRegex(TypeError, "cannot be subclassed"):
                    type("InvalidSubclass", (capability_type,), {})

    def test_precredential_has_only_ruled_public_provenance_fields(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(PrecredentialProviderEntitlementV1)),
            (
                "schema_version",
                "provider_id",
                "product_tier",
                "request_sha256",
                "manifest_file_sha256",
                "manifest_canonical_sha256",
                "auth_contract_sha256",
                "required_provider_credential_env_names",
                "evaluated_at_utc",
                "proof_sha256",
            ),
        )
        rebuilt = object.__new__(PrecredentialProviderEntitlementV1)
        self.assertIn("redacted", repr(rebuilt).casefold())
        with self.assertRaises(TypeError):
            copy.copy(rebuilt)

    def test_invalid_or_oracle_objects_cannot_enter_production_wrappers(self) -> None:
        oracle = _issue_sealed_credential_lifecycle_oracle_authority_v1(
            SealedCredentialOracleCaseV1.NONCREDENTIAL_ALLOW
        )
        with self.assertRaises(RuntimeError) as authority_rejected:
            evaluate_provider_precredential(
                object.__new__(TennisV1Config),
                object.__new__(ProviderManifest),
                object.__new__(ResearchRequest),
                credential_contract_authority=oracle,  # type: ignore[arg-type]
            )
        self.assertEqual(
            authority_rejected.exception.args,
            ("shadow_credential_contract_invalid",),
        )
        for call, expected in (
            (
                lambda: read_frozen_shadow_credentials_v1(oracle),
                "shadow_precredential_entitlement_invalid",
            ),
            (
                lambda: finalize_provider_entitlement_v1(oracle, oracle),
                "shadow_precredential_entitlement_invalid",
            ),
            (
                lambda: revoke_provider_precredential_v1(oracle),
                "shadow_precredential_entitlement_invalid",
            ),
            (
                lambda: revoke_frozen_shadow_credentials_v1(oracle),
                "shadow_credentials_invalid",
            ),
            (
                lambda: claim_frozen_provider_gate_evaluation_credential_view_v1(
                    oracle
                ),
                "shadow_credentials_invalid",
            ),
            (
                lambda: claim_frozen_provider_transport_credential_view_v1(oracle),
                "shadow_credentials_invalid",
            ),
            (
                lambda: claim_frozen_kalshi_transport_credential_view_v1(oracle),
                "shadow_credentials_invalid",
            ),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(RuntimeError) as raised:
                    call()
                self.assertEqual(raised.exception.args, (expected,))

    def test_shared_name_read_freeze_and_lifecycle_kernels_are_strict(self) -> None:
        validate = entitlements._validate_shadow_credential_name_tuples
        read_and_freeze = entitlements._read_and_freeze_shadow_credentials
        self.assertEqual(validate((), ()), ((), (), ()))
        eight = tuple(f"A_{index}" for index in range(8))
        self.assertEqual(validate(eight, ())[0], eight)
        invalid_pairs = (
            (("B", "A"), ()),
            (("A", "A"), ()),
            (("A",), ("A",)),
            (("a",), ()),
            (("A-KEY",), ()),
            (("A" * 65,), ()),
            (tuple(f"A_{index}" for index in range(9)), ()),
        )
        for provider, kalshi in invalid_pairs:
            with self.subTest(provider=provider, kalshi=kalshi):
                with self.assertRaises(RuntimeError) as raised:
                    validate(provider, kalshi)
                self.assertEqual(
                    raised.exception.args,
                    ("shadow_credential_contract_invalid",),
                )

        ambient = {
            PROVIDER_NAME: PROVIDER_VALUE,
            KALSHI_NAME: KALSHI_VALUE,
            "UNRELATED": "must-not-be-read",
        }
        reads: list[str] = []

        def reader(name: str) -> str:
            reads.append(name)
            return ambient[name]

        frozen = read_and_freeze(
            (PROVIDER_NAME,),
            (KALSHI_NAME,),
            reader=reader,
        )
        self.assertIs(type(frozen), MappingProxyType)
        self.assertEqual(reads, [KALSHI_NAME, PROVIDER_NAME])
        ambient[PROVIDER_NAME] = "changed-after-freeze"
        self.assertEqual(frozen[PROVIDER_NAME], PROVIDER_VALUE)
        with self.assertRaises(TypeError):
            frozen[PROVIDER_NAME] = "mutation"  # type: ignore[index]

    def test_oracle_and_compatibility_paths_call_the_same_pure_kernels(self) -> None:
        evaluator_source = inspect.getsource(entitlements._evaluate_provider_kernel)
        oracle_source = inspect.getsource(
            entitlements._run_sealed_credential_lifecycle_oracle_v1
        )
        for kernel in (
            "_noncredential_decision_kernel",
            "_credential_presence_reasons",
        ):
            self.assertIn(kernel, evaluator_source)
            self.assertIn(kernel, oracle_source)
        for kernel in (
            "_read_and_freeze_shadow_credentials",
            "_transition_lifecycle_state",
            "_transition_view_state",
        ):
            self.assertIn(kernel, oracle_source)
        for production_constructor in (
            "PrecredentialProviderEntitlementV1",
            "FrozenShadowCredentialsV1",
            "FrozenProviderGateEvaluationCredentialViewV1",
            "FrozenProviderTransportCredentialViewV1",
            "FrozenKalshiTransportCredentialViewV1",
            "ProviderGate",
        ):
            self.assertNotIn(production_constructor, oracle_source)


class SealedCredentialLifecycleOracleTests(unittest.TestCase):
    def run_case(
        self,
        case: SealedCredentialOracleCaseV1,
        environment: dict[str, str] | None = None,
    ) -> SealedCredentialLifecycleOracleResultV1:
        chosen = FIXTURE_ENVIRONMENT if environment is None else environment
        with mock.patch.dict(os.environ, chosen, clear=True):
            authority = _issue_sealed_credential_lifecycle_oracle_authority_v1(case)
            result = _run_sealed_credential_lifecycle_oracle_v1(authority)
        self.assertIs(type(result), SealedCredentialLifecycleOracleResultV1)
        return result

    def test_every_closed_case_runs_to_a_terminal_sealed_result(self) -> None:
        environments = {
            SealedCredentialOracleCaseV1.NONCREDENTIAL_ALLOW: {},
            SealedCredentialOracleCaseV1.NONCREDENTIAL_DENY: {},
            SealedCredentialOracleCaseV1.PRECREDENTIAL_REVOKE_REPEAT: {},
            SealedCredentialOracleCaseV1.PROVIDER_MISSING: {
                KALSHI_NAME: KALSHI_VALUE
            },
            SealedCredentialOracleCaseV1.PROVIDER_EMPTY: {
                PROVIDER_NAME: "",
                KALSHI_NAME: KALSHI_VALUE,
            },
            SealedCredentialOracleCaseV1.PROVIDER_WHITESPACE: {
                PROVIDER_NAME: " ",
                KALSHI_NAME: KALSHI_VALUE,
            },
            SealedCredentialOracleCaseV1.KALSHI_MISSING: {
                PROVIDER_NAME: PROVIDER_VALUE
            },
            SealedCredentialOracleCaseV1.KALSHI_EMPTY: {
                PROVIDER_NAME: PROVIDER_VALUE,
                KALSHI_NAME: "",
            },
            SealedCredentialOracleCaseV1.KALSHI_WHITESPACE: {
                PROVIDER_NAME: PROVIDER_VALUE,
                KALSHI_NAME: "\t",
            },
        }
        observed: set[SealedCredentialOracleCaseV1] = set()
        for case in SealedCredentialOracleCaseV1:
            with self.subTest(case=case.value):
                result = self.run_case(case, environments.get(case))
                self.assertIs(result.case, case)
                self.assertTrue(result.all_oracle_capabilities_terminal)
                self.assertTrue(result.production_authority_issuer_absent)
                self.assertEqual(result.unexpected_read_count, 0)
                observed.add(case)
        self.assertEqual(observed, set(SealedCredentialOracleCaseV1))

    def test_case_enum_is_closed_and_covers_complete_ruled_matrix(self) -> None:
        self.assertEqual(
            tuple(item.value for item in SealedCredentialOracleCaseV1),
            (
                "noncredential_allow",
                "noncredential_deny",
                "exact_sentinel_union_success",
                "provider_missing",
                "provider_empty",
                "provider_whitespace",
                "kalshi_missing",
                "kalshi_empty",
                "kalshi_whitespace",
                "post_freeze_ambient_drift",
                "precredential_consume",
                "precredential_revoke_repeat",
                "gate_poll_without_reread",
                "views_gate_provider_kalshi",
                "views_gate_kalshi_provider",
                "views_provider_gate_kalshi",
                "views_provider_kalshi_gate",
                "views_kalshi_gate_provider",
                "views_kalshi_provider_gate",
                "gate_only_then_transports",
                "partial_transfer_then_revoke",
                "view_repeat_rejected",
                "view_cross_role_rejected",
                "view_rebuilt_rejected",
                "view_foreign_rejected",
                "wrong_owner_rejected",
            ),
        )

    def test_authority_is_exact_one_shot_redacted_noncopyable_and_nonpickleable(
        self,
    ) -> None:
        authority = _issue_sealed_credential_lifecycle_oracle_authority_v1(
            SealedCredentialOracleCaseV1.NONCREDENTIAL_ALLOW
        )
        self.assertIn("redacted", repr(authority).casefold())
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises((TypeError, pickle.PicklingError)):
                    operation(authority)
        with mock.patch.dict(os.environ, {}, clear=True):
            _run_sealed_credential_lifecycle_oracle_v1(authority)
            with self.assertRaises(RuntimeError) as consumed:
                _run_sealed_credential_lifecycle_oracle_v1(authority)
        self.assertEqual(
            consumed.exception.args,
            ("shadow_credential_lifecycle_oracle_consumed",),
        )

    def test_authority_rejects_wrong_thread_without_consuming_owner_use(self) -> None:
        authority = _issue_sealed_credential_lifecycle_oracle_authority_v1(
            SealedCredentialOracleCaseV1.NONCREDENTIAL_ALLOW
        )
        observed: list[tuple[object, ...]] = []

        def foreign_use() -> None:
            try:
                _run_sealed_credential_lifecycle_oracle_v1(authority)
            except RuntimeError as error:
                observed.append(error.args)

        worker = threading.Thread(target=foreign_use)
        worker.start()
        worker.join()
        self.assertEqual(
            observed,
            [("shadow_credential_lifecycle_oracle_invalid",)],
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            result = _run_sealed_credential_lifecycle_oracle_v1(authority)
        self.assertTrue(result.all_oracle_capabilities_terminal)

    def test_success_reads_only_each_fixed_sentinel_exactly_once(self) -> None:
        production_ledger_sizes = (
            len(entitlements._SHADOW_CREDENTIAL_CONTRACT_RECORDS),
            len(entitlements._PRECREDENTIAL_RECORDS),
            len(entitlements._FROZEN_CREDENTIAL_RECORDS),
            len(entitlements._FROZEN_CREDENTIAL_VIEW_RECORDS),
            len(entitlements._FROZEN_PROVIDER_GATES),
        )
        result = self.run_case(
            SealedCredentialOracleCaseV1.EXACT_SENTINEL_UNION_SUCCESS
        )
        self.assertEqual(
            (
                len(entitlements._SHADOW_CREDENTIAL_CONTRACT_RECORDS),
                len(entitlements._PRECREDENTIAL_RECORDS),
                len(entitlements._FROZEN_CREDENTIAL_RECORDS),
                len(entitlements._FROZEN_CREDENTIAL_VIEW_RECORDS),
                len(entitlements._FROZEN_PROVIDER_GATES),
            ),
            production_ledger_sizes,
        )
        self.assertEqual(
            result.read_count_by_name,
            ((KALSHI_NAME, 1), (PROVIDER_NAME, 1)),
        )
        self.assertEqual(result.unexpected_read_count, 0)
        self.assertEqual(result.post_freeze_reread_count, 0)
        self.assertEqual(result.noncredential_decision, "eligible")
        self.assertEqual(result.credential_decision, "eligible")
        self.assertEqual(result.gate_decision, "eligible")
        self.assertTrue(result.all_oracle_capabilities_terminal)
        self.assertTrue(result.production_authority_issuer_absent)
        self.assertRegex(result.fixture_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(result.lifecycle_trace_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(result.result_sha256, r"^[0-9a-f]{64}$")
        rendered = repr(result)
        self.assertNotIn(PROVIDER_VALUE, rendered)
        self.assertNotIn(KALSHI_VALUE, rendered)

    def test_noncredential_decisions_never_read_environment(self) -> None:
        allowed = self.run_case(
            SealedCredentialOracleCaseV1.NONCREDENTIAL_ALLOW,
            {},
        )
        denied = self.run_case(
            SealedCredentialOracleCaseV1.NONCREDENTIAL_DENY,
            {},
        )
        for result in (allowed, denied):
            self.assertEqual(
                result.read_count_by_name,
                ((KALSHI_NAME, 0), (PROVIDER_NAME, 0)),
            )
            self.assertEqual(result.unexpected_read_count, 0)
        self.assertEqual(allowed.noncredential_decision, "eligible")
        self.assertEqual(allowed.credential_decision, "not_exercised")
        self.assertEqual(denied.noncredential_decision, "denied")
        self.assertEqual(denied.credential_decision, "not_exercised")

    def test_missing_empty_and_whitespace_matrix_is_fail_closed(self) -> None:
        matrix = (
            (
                SealedCredentialOracleCaseV1.PROVIDER_MISSING,
                {KALSHI_NAME: KALSHI_VALUE},
            ),
            (
                SealedCredentialOracleCaseV1.PROVIDER_EMPTY,
                {PROVIDER_NAME: "", KALSHI_NAME: KALSHI_VALUE},
            ),
            (
                SealedCredentialOracleCaseV1.PROVIDER_WHITESPACE,
                {PROVIDER_NAME: " \t", KALSHI_NAME: KALSHI_VALUE},
            ),
            (
                SealedCredentialOracleCaseV1.KALSHI_MISSING,
                {PROVIDER_NAME: PROVIDER_VALUE},
            ),
            (
                SealedCredentialOracleCaseV1.KALSHI_EMPTY,
                {PROVIDER_NAME: PROVIDER_VALUE, KALSHI_NAME: ""},
            ),
            (
                SealedCredentialOracleCaseV1.KALSHI_WHITESPACE,
                {PROVIDER_NAME: PROVIDER_VALUE, KALSHI_NAME: "\n"},
            ),
        )
        for case, environment in matrix:
            with self.subTest(case=case.value):
                result = self.run_case(case, environment)
                self.assertEqual(result.credential_decision, "credential_missing")
                self.assertEqual(result.gate_decision, "not_issued")
                self.assertEqual(
                    result.read_count_by_name,
                    ((KALSHI_NAME, 1), (PROVIDER_NAME, 1)),
                )
                self.assertTrue(result.all_oracle_capabilities_terminal)

    def test_post_freeze_drift_and_gate_poll_do_not_reread_ambient_environment(
        self,
    ) -> None:
        for case in (
            SealedCredentialOracleCaseV1.POST_FREEZE_AMBIENT_DRIFT,
            SealedCredentialOracleCaseV1.GATE_POLL_WITHOUT_REREAD,
        ):
            with self.subTest(case=case.value):
                result = self.run_case(case)
                self.assertTrue(result.drift_ignored)
                self.assertEqual(result.post_freeze_reread_count, 0)
                self.assertEqual(result.gate_decision, "eligible")
                self.assertEqual(
                    result.read_count_by_name,
                    ((KALSHI_NAME, 1), (PROVIDER_NAME, 1)),
                )

    def test_every_three_view_transfer_order_finishes_terminal(self) -> None:
        for case in SUCCESS_CASES[4:10]:
            with self.subTest(case=case.value):
                result = self.run_case(case)
                self.assertEqual(result.credential_decision, "eligible")
                self.assertEqual(result.gate_decision, "eligible")
                self.assertTrue(result.all_oracle_capabilities_terminal)
                self.assertEqual(result.post_freeze_reread_count, 0)

    def test_gate_only_then_transports_and_partial_failure_unwind(self) -> None:
        gate_only = self.run_case(
            SealedCredentialOracleCaseV1.GATE_ONLY_THEN_TRANSPORTS
        )
        partial = self.run_case(
            SealedCredentialOracleCaseV1.PARTIAL_TRANSFER_THEN_REVOKE
        )
        consumed = self.run_case(
            SealedCredentialOracleCaseV1.PRECREDENTIAL_CONSUME
        )
        self.assertEqual(gate_only.gate_decision, "eligible")
        self.assertTrue(gate_only.all_oracle_capabilities_terminal)
        self.assertEqual(partial.gate_decision, "not_issued")
        self.assertTrue(partial.all_oracle_capabilities_terminal)
        self.assertEqual(consumed.gate_decision, "eligible")
        self.assertTrue(consumed.all_oracle_capabilities_terminal)

    def test_revoke_repeat_rebuilt_cross_role_repeat_and_wrong_owner_are_closed(
        self,
    ) -> None:
        for case in (
            SealedCredentialOracleCaseV1.PRECREDENTIAL_REVOKE_REPEAT,
            SealedCredentialOracleCaseV1.VIEW_REPEAT_REJECTED,
            SealedCredentialOracleCaseV1.VIEW_CROSS_ROLE_REJECTED,
            SealedCredentialOracleCaseV1.VIEW_REBUILT_REJECTED,
            SealedCredentialOracleCaseV1.VIEW_FOREIGN_REJECTED,
            SealedCredentialOracleCaseV1.WRONG_OWNER_REJECTED,
        ):
            with self.subTest(case=case.value):
                result = self.run_case(
                    case,
                    {} if case is SealedCredentialOracleCaseV1.PRECREDENTIAL_REVOKE_REPEAT else None,
                )
                self.assertTrue(result.all_oracle_capabilities_terminal)
                self.assertEqual(result.unexpected_read_count, 0)

    def test_oracle_result_is_frozen_slotted_hash_bound_and_exact_type_distinct(
        self,
    ) -> None:
        result = self.run_case(
            SealedCredentialOracleCaseV1.EXACT_SENTINEL_UNION_SUCCESS
        )
        self.assertEqual(
            tuple(item.name for item in fields(type(result))),
            (
                "schema_version",
                "case",
                "fixture_sha256",
                "read_count_by_name",
                "unexpected_read_count",
                "post_freeze_reread_count",
                "noncredential_decision",
                "credential_decision",
                "gate_decision",
                "drift_ignored",
                "lifecycle_trace_sha256",
                "all_oracle_capabilities_terminal",
                "production_authority_issuer_absent",
                "result_sha256",
            ),
        )
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            result.gate_decision = "denied"  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            result.extra = True  # type: ignore[attr-defined]
        self.assertIsNot(type(result), PrecredentialProviderEntitlementV1)
        self.assertIsNot(type(result), FrozenShadowCredentialsV1)
        self.assertFalse(hasattr(result, "credential_values"))
        self.assertFalse(hasattr(result, "provider_gate"))
        projection = {
            "schema_version": 1,
            "case": result.case.value,
            "fixture_sha256": result.fixture_sha256,
            "read_count_by_name": [list(item) for item in result.read_count_by_name],
            "unexpected_read_count": result.unexpected_read_count,
            "post_freeze_reread_count": result.post_freeze_reread_count,
            "noncredential_decision": result.noncredential_decision,
            "credential_decision": result.credential_decision,
            "gate_decision": result.gate_decision,
            "drift_ignored": result.drift_ignored,
            "lifecycle_trace_sha256": result.lifecycle_trace_sha256,
            "all_oracle_capabilities_terminal": (
                result.all_oracle_capabilities_terminal
            ),
            "production_authority_issuer_absent": (
                result.production_authority_issuer_absent
            ),
        }
        expected = hashlib.sha256(
            b"INCI-SEALED-CREDENTIAL-LIFECYCLE-RESULT-V1\0"
            + canonical_json_bytes(projection)
        ).hexdigest()
        self.assertEqual(result.result_sha256, expected)

    def test_invalid_case_and_rebuilt_authority_fail_with_fixed_sanitized_errors(
        self,
    ) -> None:
        with self.assertRaises(RuntimeError) as invalid_case:
            _issue_sealed_credential_lifecycle_oracle_authority_v1("success")  # type: ignore[arg-type]
        self.assertEqual(
            invalid_case.exception.args,
            ("shadow_credential_lifecycle_oracle_invalid",),
        )
        rebuilt = object.__new__(SealedCredentialLifecycleOracleAuthorityV1)
        with self.assertRaises(RuntimeError) as rebuilt_error:
            _run_sealed_credential_lifecycle_oracle_v1(rebuilt)
        self.assertEqual(
            rebuilt_error.exception.args,
            ("shadow_credential_lifecycle_oracle_invalid",),
        )
        for message in (str(invalid_case.exception), str(rebuilt_error.exception)):
            self.assertNotIn(PROVIDER_VALUE, message)
            self.assertNotIn(KALSHI_VALUE, message)
            self.assertNotRegex(message, re.escape(os.path.expanduser("~")))


if __name__ == "__main__":
    unittest.main()
