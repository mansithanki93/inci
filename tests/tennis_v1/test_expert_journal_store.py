from __future__ import annotations

import ast
import copy
from contextlib import ExitStack
from dataclasses import fields, replace
import errno
from importlib.machinery import SourceFileLoader
import inspect
import json
import os
from pathlib import Path
import pickle
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import warnings
from unittest import mock

import inci_tennis_io.expert_journal_store as store_module
from inci_tennis_expert.contracts import (
    DurableExpertAppendReceiptV1,
    DurableExpertEmergencyReceiptV1,
    DurableExpertTerminalReceiptV1,
    ExpertJournalScanSummaryV1,
    ExpertPhysicalFileIdentityV1,
    ExpertProviderDomainBindingV1,
    ExpertPurgeReportV1,
    ExpertRetentionBindingV1,
    ExpertReplayDiagnosticRoleV1,
    ExpertReplayMismatchV1,
    ExpertTerminalReasonV1,
    compute_expert_provider_domain_binding_sha256,
    compute_expert_provider_source_lineage_sha256,
    compute_expert_retention_binding_sha256,
    compute_expert_session_manifest_sha256,
)
from inci_tennis_expert.reducer import initial_expert_state
from inci_tennis_io import facade
from inci_tennis_io.ports import (
    CandidateObservationStartupAuthorityV1,
    CandidateQualificationOutputWriterV1,
    CandidateSourceSealCollectionAuthorityV1,
    SportradarCandidatePreparedReadV1,
    ExpertEmergencyAppendPermitV1,
    ExpertEnvironmentCollectionAuthorityV1,
    ExpertJournalAppendPermitV1,
    ExpertJournalPurgeCapabilityV1,
    ExpertJournalReadCapabilityV1,
    ExpertJournalRootAuthorityV1,
    ExpertJournalTerminalPermitV1,
    ExpertJournalWriteCapabilityV1,
    ExpertLiveAuthorizationDenied,
    ExpertPrewriteCapacityError,
    ExpertReplayAccessDenied,
    ExpertReplayConstructionAuthorityV1,
)
from tennis_v1.retention import RetentionCoordinator, RetentionError
from tennis_v1.fingerprints import code_sha256
from tennis_v1.reducer import initial_trace as phase1_initial_trace
from tennis_v1.replay_core import ReplayMismatch
from tennis_v1.state import canonical_state_bytes, initial_state as phase1_initial_state
from tennis_v1.wal import JournalReader, JournalWriter
from tests.tennis_v1.test_expert_journal_codec import (
    _genesis_cursor,
    _group_fixture,
    _manifest_fixture,
    _terminal_fixture,
)
from tests.tennis_v1.test_expert_observation import raw_parent
from tests.tennis_v1.test_expert_observation import task6_artifacts
from tests.tennis_v1.test_retention import (
    MutableClock,
    make_config,
    make_manifest_decision,
    session_start_frame,
)


CAPABILITY_TYPES = (
    CandidateObservationStartupAuthorityV1,
    CandidateQualificationOutputWriterV1,
    CandidateSourceSealCollectionAuthorityV1,
    SportradarCandidatePreparedReadV1,
    ExpertJournalRootAuthorityV1,
    ExpertEnvironmentCollectionAuthorityV1,
    ExpertReplayConstructionAuthorityV1,
    ExpertJournalWriteCapabilityV1,
    ExpertJournalReadCapabilityV1,
    ExpertJournalPurgeCapabilityV1,
    ExpertJournalAppendPermitV1,
    ExpertJournalTerminalPermitV1,
    ExpertEmergencyAppendPermitV1,
)

FACADE_FUNCTIONS = (
    "issue_sportradar_candidate_source_seal_collection_authority",
    "collect_sportradar_candidate_source_seals",
    "create_sportradar_candidate_output_writer",
    "append_sportradar_candidate_permit",
    "append_sportradar_candidate_capture",
    "append_sportradar_candidate_parser_result",
    "append_sportradar_candidate_failure",
    "finalize_sportradar_candidate_output",
    "abort_sportradar_candidate_output",
    "prepare_sportradar_summary_read",
    "prepare_sportradar_timeline_read",
    "read_sportradar_summary",
    "read_sportradar_timeline",
    "acquire_expert_journal_root",
    "issue_expert_environment_collection_authority",
    "collect_expert_current_environment",
    "sample_expert_retention_wall_ns",
    "issue_expert_replay_construction_authority",
    "create_expert_journal",
    "recover_and_purge_expert_journals",
    "issue_expert_read_capability",
    "issue_expert_purge_capability",
    "issue_expert_append_permit",
    "append_expert_group",
    "acknowledge_expert_publication",
    "issue_expert_terminal_permit",
    "append_expert_terminal",
    "issue_expert_emergency_append_permit",
    "append_expert_emergency_group_and_terminal",
    "read_expert_manifest",
    "read_next_expert_group",
    "read_expert_terminal_and_summary",
    "prove_expert_live_evidence_tail",
    "build_aligned_expert_terminal",
    "inspect_phase1_evidence_file_identities",
    "inspect_expert_companion_file_identities",
    "prepare_expert_replay_begin",
    "read_next_replay_evidence_parent",
    "read_next_replay_companion_group",
    "read_replay_finish_material",
    "issue_begin_replay_authorization",
    "acknowledge_begin_replay",
    "issue_parent_group_replay_authorization",
    "acknowledge_parent_group_replay",
    "issue_finish_replay_authorization",
    "acknowledge_finish_replay",
    "take_expert_replay_denial",
    "abort_expert_replay_construction",
    "purge_expert_session",
    "abort_expert_writer",
    "close_expert_reader",
    "revoke_expert_reader",
)


class ExpertPortContractTests(unittest.TestCase):
    def test_error_surfaces_are_fixed_and_never_include_provider_filesystem_or_exception_text(
        self,
    ) -> None:
        cases = (
            (ExpertLiveAuthorizationDenied, "expert_live_authorization_denied"),
            (ExpertPrewriteCapacityError, "expert_prewrite_capacity_low"),
            (ExpertReplayAccessDenied, "expert_replay_access_denied"),
        )
        for exception_type, expected in cases:
            with self.subTest(exception=exception_type.__name__):
                error = exception_type()
                self.assertEqual(error.args, (expected,))
                self.assertEqual(str(error), expected)
                if exception_type is ExpertPrewriteCapacityError:
                    self.assertIsNone(error.requested_bytes)
                    self.assertIsNone(error.available_bytes)
                    self.assertIsNone(error.emergency_reserve_bytes)
                with self.assertRaises(TypeError):
                    exception_type("provider-secret")  # type: ignore[call-arg]

        invalid_capacity_observations = (
            {"requested_bytes": 1},
            {
                "requested_bytes": True,
                "available_bytes": 1,
                "emergency_reserve_bytes": 1,
            },
            {
                "requested_bytes": -1,
                "available_bytes": 1,
                "emergency_reserve_bytes": 1,
            },
            {
                "requested_bytes": 1,
                "available_bytes": 9_223_372_036_854_775_808,
                "emergency_reserve_bytes": 1,
            },
        )
        for values in invalid_capacity_observations:
            with self.subTest(values=values):
                with self.assertRaises((TypeError, ValueError)):
                    ExpertPrewriteCapacityError(**values)

    def test_every_capability_and_permit_rejects_forgery_double_use_stale_thread_fork_and_generation_drift(
        self,
    ) -> None:
        for capability_type in CAPABILITY_TYPES:
            with self.subTest(capability=capability_type.__name__):
                with self.assertRaises(TypeError):
                    capability_type()
                with self.assertRaises(TypeError):
                    type("Hostile", (capability_type,), {})
                forged = object.__new__(capability_type)
                self.assertIn("redacted", repr(forged))
                for operation in (
                    copy.copy,
                    copy.deepcopy,
                    pickle.dumps,
                ):
                    with self.assertRaises(TypeError):
                        operation(forged)
                public_names = {
                    name
                    for name in dir(forged)
                    if not name.startswith("_")
                }
                self.assertFalse(
                    public_names
                    & {
                        "path",
                        "basename",
                        "descriptor",
                        "fd",
                        "callback",
                        "write",
                        "read",
                        "truncate",
                        "rename",
                        "repair",
                    }
                )

    def test_ports_reexport_only_frozen_result_contracts(self) -> None:
        expected = (
            DurableExpertAppendReceiptV1,
            DurableExpertTerminalReceiptV1,
            DurableExpertEmergencyReceiptV1,
            ExpertPurgeReportV1,
            ExpertPhysicalFileIdentityV1,
            ExpertJournalScanSummaryV1,
        )
        for contract in expected:
            with self.subTest(contract=contract.__name__):
                self.assertIs(getattr(store_module, contract.__name__), contract)


class ExpertFacadeBoundaryTests(unittest.TestCase):
    def test_facade_and_ports_match_exact_frozen_exports_signatures_and_no_authority_leakage(
        self,
    ) -> None:
        for name in FACADE_FUNCTIONS:
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(facade, name)))
        self.assertFalse(hasattr(facade, "close_expert_journal_root"))
        self.assertFalse(hasattr(facade, "verify_durable_event"))

    def test_additive_signatures_are_exact_and_parser_free(self) -> None:
        tail = inspect.signature(facade.prove_expert_live_evidence_tail)
        self.assertEqual(tuple(tail.parameters), ("writer", "published_cursor"))
        self.assertEqual(
            tail.parameters["published_cursor"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        aligned = inspect.signature(facade.build_aligned_expert_terminal)
        self.assertEqual(
            tuple(aligned.parameters),
            ("writer", "final_state", "final_cursor"),
        )
        self.assertTrue(
            all(
                aligned.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
                for name in ("final_state", "final_cursor")
            )
        )
        replay = inspect.signature(
            facade.issue_expert_replay_construction_authority
        )
        self.assertEqual(
            tuple(replay.parameters),
            ("authority", "persistence_authorizer", "coordinator"),
        )

    def test_package_root_remains_empty_and_store_has_static_io_boundary(self) -> None:
        package_root = Path(store_module.__file__).with_name("__init__.py")
        self.assertEqual(
            package_root.read_bytes(),
            (
                b"# Sealed package root; use inci_tennis_io.facade for "
                b"public interfaces.\n"
            ),
        )
        tree = ast.parse(Path(store_module.__file__).read_text("utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        forbidden = {
            "requests",
            "urllib",
            "socket",
            "subprocess",
            "webbrowser",
            "kalshi_client",
        }
        self.assertTrue(imports.isdisjoint(forbidden))


class ExpertRootAuthorityTests(unittest.TestCase):
    SOURCE_PACKAGES = (
        "tennis_v1",
        "inci_tennis_expert",
        "inci_tennis_io",
        "inci_tennis_adapters",
        "inci_tennis_runtime",
    )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temporary.name).resolve()
        self.clock = MutableClock(123)
        self.coordinator = RetentionCoordinator.acquire(
            make_config(self.root_path / "state"),
            clock_ns=self.clock,
        )
        self.coordinator.recover_and_purge()

    def tearDown(self) -> None:
        self.coordinator.close()
        self.temporary.cleanup()

    def acquire_root(self) -> ExpertJournalRootAuthorityV1:
        request = (
            self.coordinator.issue_expert_state_root_account_lock_request()
        )
        return facade.acquire_expert_journal_root(request)

    def make_source_distribution(self, parent: Path) -> None:
        parent.mkdir()
        for package_name in self.SOURCE_PACKAGES:
            package = parent / package_name
            package.mkdir()
            (package / "__init__.py").write_bytes(b"\n")
        (parent / "pyproject.toml").write_bytes(b"[project]\n")
        (parent / "requirements.txt").write_bytes(b"\n")

    def patch_source_distribution(
        self,
        roots: dict[str, Path],
    ) -> ExitStack:
        stack = ExitStack()
        for package_name in self.SOURCE_PACKAGES:
            if package_name == "tennis_v1":
                import tennis_v1 as module
            elif package_name == "inci_tennis_expert":
                import inci_tennis_expert as module
            elif package_name == "inci_tennis_io":
                import inci_tennis_io as module
            elif package_name == "inci_tennis_adapters":
                import inci_tennis_adapters as module
            elif package_name == "inci_tennis_runtime":
                import inci_tennis_runtime as module
            else:
                raise AssertionError(
                    f"unknown governed source package: {package_name}"
                )
            package = roots[package_name] / package_name
            origin = str(package / "__init__.py")
            loader = SourceFileLoader(package_name, origin)
            stack.enter_context(
                mock.patch.object(module, "__file__", origin)
            )
            stack.enter_context(
                mock.patch.object(module, "__loader__", loader)
            )
            stack.enter_context(
                mock.patch.object(module, "__path__", [str(package)])
            )
            spec = module.__spec__
            self.assertIsNotNone(spec)
            stack.enter_context(
                mock.patch.object(spec, "origin", origin)
            )
            stack.enter_context(
                mock.patch.object(spec, "loader", loader)
            )
            stack.enter_context(
                mock.patch.object(
                    spec,
                    "submodule_search_locations",
                    [str(package)],
                )
            )
        return stack

    def test_one_request_bootstraps_one_descriptor_relative_expert_root(self) -> None:
        authority = self.acquire_root()
        state_root = self.root_path / "state"
        for relative in (
            "expert-v1",
            "expert-v1/sessions",
            "expert-v1/markers",
        ):
            path = state_root / relative
            self.assertTrue(path.is_dir())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        self.assertEqual(facade.sample_expert_retention_wall_ns(authority), 123)

    def test_root_uses_the_transferred_mutable_clock_repeatedly(self) -> None:
        authority = self.acquire_root()
        self.assertEqual(facade.sample_expert_retention_wall_ns(authority), 123)
        self.clock.now_ns = 456
        self.assertEqual(facade.sample_expert_retention_wall_ns(authority), 456)

    def test_request_and_root_are_not_reusable_or_forgeable(self) -> None:
        request = (
            self.coordinator.issue_expert_state_root_account_lock_request()
        )
        authority = facade.acquire_expert_journal_root(request)
        with self.assertRaises((RetentionError, ValueError)):
            facade.acquire_expert_journal_root(request)
        with self.assertRaises((RetentionError, ValueError)):
            self.coordinator.issue_expert_state_root_account_lock_request()
        forged = object.__new__(ExpertJournalRootAuthorityV1)
        with self.assertRaises((TypeError, ValueError)):
            facade.sample_expert_retention_wall_ns(forged)
        self.assertEqual(facade.sample_expert_retention_wall_ns(authority), 123)

    def test_cross_thread_root_use_is_rejected_without_clock_sampling(self) -> None:
        authority = self.acquire_root()
        outcomes: list[BaseException | int] = []

        def misuse() -> None:
            try:
                outcomes.append(
                    facade.sample_expert_retention_wall_ns(authority)
                )
            except BaseException as error:
                outcomes.append(error)

        thread = threading.Thread(target=misuse)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], (RetentionError, ValueError))
        self.assertEqual(facade.sample_expert_retention_wall_ns(authority), 123)

    def test_coordinator_close_is_the_root_close_seam(self) -> None:
        authority = self.acquire_root()
        self.coordinator.close()
        with self.assertRaises((RetentionError, ValueError)):
            facade.sample_expert_retention_wall_ns(authority)
        with self.assertRaises((RetentionError, ValueError)):
            facade.recover_and_purge_expert_journals(authority)

    def test_recovery_on_empty_inventory_is_sorted_and_root_remains_live(
        self,
    ) -> None:
        authority = self.acquire_root()
        report = facade.recover_and_purge_expert_journals(authority)
        self.assertEqual(
            report,
            ExpertPurgeReportV1((), (), (), ()),
        )
        self.assertEqual(facade.sample_expert_retention_wall_ns(authority), 123)

    def test_root_bootstrap_failure_revokes_grant_and_leaks_no_subroot(self) -> None:
        request = (
            self.coordinator.issue_expert_state_root_account_lock_request()
        )
        state_root = self.root_path / "state"
        with mock.patch.object(
            store_module.os,
            "mkdir",
            side_effect=OSError("secret bootstrap failure"),
        ):
            with self.assertRaises(ValueError) as caught:
                facade.acquire_expert_journal_root(request)
        self.assertNotIn("secret", str(caught.exception))
        self.assertFalse((state_root / "expert-v1").exists())

    def test_source_temporary_close_uncertainty_returns_no_root_authority(
        self,
    ) -> None:
        request = (
            self.coordinator.issue_expert_state_root_account_lock_request()
        )
        state_root = self.root_path / "state"
        roots_before = set(store_module._ROOTS)
        target_descriptor: int | None = None
        close_attempts = 0
        original_open = store_module.os.open
        original_close = store_module.os.close
        original_revoke = (
            store_module._revoke_expert_state_root_account_lock_grant
        )

        def observe_source_open(
            path: object,
            flags: int,
            *args: object,
            **keywords: object,
        ) -> int:
            nonlocal target_descriptor
            descriptor = original_open(path, flags, *args, **keywords)
            if path == "pyproject.toml" and target_descriptor is None:
                target_descriptor = descriptor
            return descriptor

        def uncertain_source_close(descriptor: int) -> None:
            nonlocal close_attempts
            if descriptor == target_descriptor:
                close_attempts += 1
                original_close(descriptor)
                raise OSError(
                    errno.EIO,
                    "forced_source_temporary_close_uncertainty",
                )
            original_close(descriptor)

        with (
            mock.patch.object(
                store_module.os,
                "open",
                side_effect=observe_source_open,
            ),
            mock.patch.object(
                store_module.os,
                "close",
                side_effect=uncertain_source_close,
            ),
            mock.patch.object(
                store_module,
                "_revoke_expert_state_root_account_lock_grant",
                wraps=original_revoke,
            ) as revoke,
            self.assertRaisesRegex(
                OSError,
                "^expert_source_descriptor_close_uncertain$",
            ),
        ):
            facade.acquire_expert_journal_root(request)

        self.assertIsNotNone(target_descriptor)
        self.assertEqual(close_attempts, 1)
        with self.assertRaises(OSError):
            os.fstat(target_descriptor)
        self.assertEqual(set(store_module._ROOTS), roots_before)
        self.assertFalse((state_root / "expert-v1").exists())
        revoke.assert_called_once()
        with self.assertRaises((RetentionError, ValueError)):
            facade.acquire_expert_journal_root(request)

    def test_source_authority_rejects_mixed_loaded_package_origins(
        self,
    ) -> None:
        first = self.root_path / "source-a"
        second = self.root_path / "source-b"
        self.make_source_distribution(first)
        self.make_source_distribution(second)
        roots = dict.fromkeys(self.SOURCE_PACKAGES, first)
        roots["inci_tennis_runtime"] = second
        with self.patch_source_distribution(roots):
            with self.assertRaisesRegex(
                ValueError,
                "^expert_root_acquisition_failed$",
            ):
                self.acquire_root()

    def test_source_authority_rejects_bytecode_only_origin(
        self,
    ) -> None:
        source = self.root_path / "source"
        self.make_source_distribution(source)
        roots = dict.fromkeys(self.SOURCE_PACKAGES, source)
        module = __import__("inci_tennis_runtime")
        spec = module.__spec__
        self.assertIsNotNone(spec)
        with self.patch_source_distribution(roots):
            pyc = str(source / "inci_tennis_runtime" / "__init__.pyc")
            pyc_loader = SourceFileLoader("inci_tennis_runtime", pyc)
            with (
                mock.patch.object(module, "__file__", pyc),
                mock.patch.object(module, "__loader__", pyc_loader),
                mock.patch.object(spec, "origin", pyc),
                mock.patch.object(spec, "loader", pyc_loader),
                self.assertRaisesRegex(
                    ValueError,
                    "^expert_root_acquisition_failed$",
                ),
            ):
                self.acquire_root()

    def test_source_authority_rejects_namespace_package_alias(self) -> None:
        source = self.root_path / "source"
        self.make_source_distribution(source)
        roots = dict.fromkeys(self.SOURCE_PACKAGES, source)
        module = __import__("inci_tennis_runtime")
        spec = module.__spec__
        self.assertIsNotNone(spec)
        package = str(source / "inci_tennis_runtime")
        with (
            self.patch_source_distribution(roots),
            mock.patch.object(module, "__file__", None),
            mock.patch.object(module, "__loader__", None),
            mock.patch.object(module, "__path__", (package,)),
            mock.patch.object(spec, "origin", None),
            mock.patch.object(spec, "loader", None),
            mock.patch.object(
                spec,
                "submodule_search_locations",
                (package,),
            ),
            self.assertRaisesRegex(
                ValueError,
                "^expert_root_acquisition_failed$",
            ),
        ):
            self.acquire_root()

    def test_source_authority_rejects_lexical_path_alias(self) -> None:
        source = self.root_path / "source"
        self.make_source_distribution(source)
        roots = dict.fromkeys(self.SOURCE_PACKAGES, source)
        module = __import__("inci_tennis_runtime")
        spec = module.__spec__
        self.assertIsNotNone(spec)
        package = str(
            source
            / "inci_tennis_runtime"
            / ".."
            / "inci_tennis_runtime"
        )
        origin = package + "/__init__.py"
        loader = SourceFileLoader("inci_tennis_runtime", origin)
        with (
            self.patch_source_distribution(roots),
            mock.patch.object(module, "__file__", origin),
            mock.patch.object(module, "__loader__", loader),
            mock.patch.object(module, "__path__", [package]),
            mock.patch.object(spec, "origin", origin),
            mock.patch.object(spec, "loader", loader),
            mock.patch.object(
                spec,
                "submodule_search_locations",
                [package],
            ),
            self.assertRaisesRegex(
                ValueError,
                "^expert_root_acquisition_failed$",
            ),
        ):
            self.acquire_root()

    def test_retained_source_authority_rejects_loaded_origin_and_path_mutation(
        self,
    ) -> None:
        authority = self.acquire_root()
        root = store_module._ROOTS[authority]
        package = root.source_packages[0]
        module = sys.modules[package.name]
        spec = module.__spec__
        self.assertIsNotNone(spec)
        cases = (
            ("file", module, "__file__", package.origin + ".alias"),
            ("origin", spec, "origin", package.origin + ".alias"),
            ("path-empty", module, "__path__", []),
            (
                "path-multiple",
                module,
                "__path__",
                [package.package_path, package.package_path],
            ),
            (
                "spec-path",
                spec,
                "submodule_search_locations",
                [],
            ),
        )
        for name, target, attribute, replacement in cases:
            with (
                self.subTest(mutation=name),
                mock.patch.object(target, attribute, replacement),
                self.assertRaisesRegex(
                    ValueError,
                    "^expert_source_root_invalid$",
                ),
            ):
                store_module._validate_source_root(root)

    def test_source_authority_rejects_symlinked_init_child(self) -> None:
        source = self.root_path / "source"
        self.make_source_distribution(source)
        target = source / "runtime-init.py"
        target.write_bytes(b"\n")
        init = source / "inci_tennis_runtime" / "__init__.py"
        init.unlink()
        init.symlink_to(target)
        roots = dict.fromkeys(self.SOURCE_PACKAGES, source)
        with self.patch_source_distribution(roots):
            with self.assertRaisesRegex(
                ValueError,
                "^expert_root_acquisition_failed$",
            ):
                self.acquire_root()

    def test_source_authority_rejects_symlinked_package_child(self) -> None:
        source = self.root_path / "source"
        self.make_source_distribution(source)
        package = source / "inci_tennis_runtime"
        target = source / "inci_tennis_runtime-real"
        package.rename(target)
        package.symlink_to(target, target_is_directory=True)
        roots = dict.fromkeys(self.SOURCE_PACKAGES, source)
        with self.patch_source_distribution(roots):
            with self.assertRaisesRegex(
                ValueError,
                "^expert_root_acquisition_failed$",
            ):
                self.acquire_root()

    def test_source_authority_rejects_loaded_origin_mutation_during_acquisition(
        self,
    ) -> None:
        source = self.root_path / "source"
        self.make_source_distribution(source)
        roots = dict.fromkeys(self.SOURCE_PACKAGES, source)
        module = __import__("inci_tennis_runtime")
        original_check = store_module._source_named_file_identity

        def mutate_after_dependency_check(
            directory_fd: int,
            basename: str,
        ) -> tuple[int, ...]:
            identity = original_check(directory_fd, basename)
            if basename == "requirements.txt":
                module.__file__ = module.__file__ + ".alias"
            return identity

        with (
            self.patch_source_distribution(roots),
            mock.patch.object(
                store_module,
                "_source_named_file_identity",
                side_effect=mutate_after_dependency_check,
            ),
        ):
            try:
                authority = self.acquire_root()
            except ValueError as error:
                self.assertEqual(
                    str(error),
                    "expert_root_acquisition_failed",
                )
            else:
                store_module._fatal_root(
                    store_module._ROOTS[authority]
                )
                self.fail(
                    "origin mutation escaped source acquisition"
                )

    def test_source_authority_rejects_same_byte_init_replacement_during_acquisition(
        self,
    ) -> None:
        source = self.root_path / "source"
        self.make_source_distribution(source)
        roots = dict.fromkeys(self.SOURCE_PACKAGES, source)
        original_check = store_module._source_named_file_identity
        init = source / "inci_tennis_runtime" / "__init__.py"

        def replace_after_dependency_check(
            directory_fd: int,
            basename: str,
        ) -> tuple[int, ...]:
            identity = original_check(directory_fd, basename)
            if basename == "requirements.txt":
                replacement = init.with_name("replacement.py")
                replacement.write_bytes(init.read_bytes())
                replacement.replace(init)
            return identity

        with (
            self.patch_source_distribution(roots),
            mock.patch.object(
                store_module,
                "_source_named_file_identity",
                side_effect=replace_after_dependency_check,
            ),
        ):
            try:
                authority = self.acquire_root()
            except ValueError as error:
                self.assertEqual(
                    str(error),
                    "expert_root_acquisition_failed",
                )
            else:
                store_module._fatal_root(
                    store_module._ROOTS[authority]
                )
                self.fail(
                    "same-byte init replacement escaped source acquisition"
                )

    def test_retained_source_authority_detects_same_byte_init_replacement(
        self,
    ) -> None:
        source = self.root_path / "source"
        self.make_source_distribution(source)
        roots = dict.fromkeys(self.SOURCE_PACKAGES, source)
        with self.patch_source_distribution(roots):
            authority = self.acquire_root()
            root = store_module._ROOTS[authority]
            init = source / "inci_tennis_runtime" / "__init__.py"
            replacement = source / "replacement.py"
            replacement.write_bytes(init.read_bytes())
            replacement.replace(init)
            with self.assertRaisesRegex(
                ValueError,
                "^expert_root_authority_invalid$",
            ):
                facade.sample_expert_retention_wall_ns(authority)

    def test_retained_source_authority_detects_same_byte_package_replacement(
        self,
    ) -> None:
        source = self.root_path / "source"
        self.make_source_distribution(source)
        roots = dict.fromkeys(self.SOURCE_PACKAGES, source)
        with self.patch_source_distribution(roots):
            authority = self.acquire_root()
            root = store_module._ROOTS[authority]
            package = source / "inci_tennis_runtime"
            displaced = source / "inci_tennis_runtime-old"
            package.rename(displaced)
            package.mkdir()
            (package / "__init__.py").write_bytes(
                (displaced / "__init__.py").read_bytes()
            )
            with self.assertRaisesRegex(
                ValueError,
                "^expert_root_authority_invalid$",
            ):
                facade.sample_expert_retention_wall_ns(authority)

    def test_retained_source_authority_rejects_same_byte_dependency_replacement(
        self,
    ) -> None:
        source = self.root_path / "source"
        self.make_source_distribution(source)
        roots = dict.fromkeys(self.SOURCE_PACKAGES, source)
        with self.patch_source_distribution(roots):
            authority = self.acquire_root()
            root = store_module._ROOTS[authority]
            self.assertEqual(
                tuple(
                    dependency.basename
                    for dependency in root.source_dependencies
                ),
                ("pyproject.toml", "requirements.txt"),
            )
            dependency = source / "requirements.txt"
            replacement = source / "replacement-requirements.txt"
            replacement.write_bytes(dependency.read_bytes())
            replacement.replace(dependency)
            with self.assertRaisesRegex(
                ValueError,
                "^expert_root_authority_invalid$",
            ):
                facade.sample_expert_retention_wall_ns(authority)

    def test_retained_source_authority_rejects_same_byte_pyproject_replacement(
        self,
    ) -> None:
        source = self.root_path / "source"
        self.make_source_distribution(source)
        roots = dict.fromkeys(self.SOURCE_PACKAGES, source)
        with self.patch_source_distribution(roots):
            authority = self.acquire_root()
            dependency = source / "pyproject.toml"
            replacement = source / "replacement-pyproject.toml"
            replacement.write_bytes(dependency.read_bytes())
            replacement.replace(dependency)
            with self.assertRaisesRegex(
                ValueError,
                "^expert_root_authority_invalid$",
            ):
                facade.sample_expert_retention_wall_ns(authority)

    def test_environment_collection_rejects_extra_package_artifacts(
        self,
    ) -> None:
        repository_root = Path(store_module.__file__).resolve().parent.parent
        adapter_code_sha256 = "a" * 64
        adapter = mock.Mock(
            adapter_code_sha256=adapter_code_sha256,
        )
        cases = (
            ("python", "inci_tennis_io/unreviewed.py", b"VALUE = 1\n", "file"),
            (
                "schema",
                "inci_tennis_expert/schemas/unreviewed.schema.json",
                b"{}\n",
                "file",
            ),
            (
                "bytecode",
                "inci_tennis_runtime/unreviewed.pyc",
                b"hostile",
                "file",
            ),
            (
                "resource",
                "inci_tennis_adapters/unreviewed.txt",
                b"hostile",
                "file",
            ),
            (
                "cache",
                "inci_tennis_runtime/__pycache__",
                b"hostile",
                "cache",
            ),
            (
                "special",
                "inci_tennis_adapters/hostile.fifo",
                b"",
                "fifo",
            ),
        )
        for name, relative, content, artifact_kind in cases:
            with self.subTest(artifact=name):
                case_root = self.root_path / f"inventory-{name}"
                source = case_root / "source"
                source.mkdir(parents=True)
                for package_name in self.SOURCE_PACKAGES:
                    shutil.copytree(
                        repository_root / package_name,
                        source / package_name,
                    )
                for basename in store_module._DEPENDENCY_INVENTORY:
                    shutil.copy2(
                        repository_root / basename,
                        source / basename,
                    )
                artifact = source / relative
                if artifact_kind == "cache":
                    artifact.mkdir()
                    (artifact / "module.cpython-314.pyc").write_bytes(content)
                elif artifact_kind == "fifo":
                    os.mkfifo(artifact)
                else:
                    artifact.write_bytes(content)
                roots = dict.fromkeys(self.SOURCE_PACKAGES, source)
                coordinator = RetentionCoordinator.acquire(
                    make_config(case_root / "state"),
                    clock_ns=self.clock,
                )
                coordinator.recover_and_purge()
                root = None
                try:
                    with self.patch_source_distribution(roots):
                        request = (
                            coordinator
                            .issue_expert_state_root_account_lock_request()
                        )
                        authority = facade.acquire_expert_journal_root(request)
                        root = store_module._ROOTS[authority]
                        manifest = mock.Mock(
                            provider_id="synthetic-provider",
                            product_tier="trial-v1",
                            code_sha256=code_sha256(
                                source / "tennis_v1"
                            ),
                            adapter_code_sha256=adapter_code_sha256,
                        )
                        with mock.patch.object(
                            store_module,
                            "load_active_adapter_contract",
                            return_value=adapter,
                        ):
                            with self.assertRaisesRegex(
                                ValueError,
                                "^expert_environment_inventory_invalid$",
                            ):
                                store_module._installed_environment(
                                    root,
                                    manifest,
                                )
                        store_module._fatal_root(root)
                        root = None
                finally:
                    if root is not None:
                        store_module._fatal_root(root)
                    coordinator.close()


class ExpertPhysicalStoreTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temporary.name).resolve()
        self.clock = MutableClock(1)
        self.coordinator = RetentionCoordinator.acquire(
            make_config(self.root_path / "state"),
            clock_ns=self.clock,
        )
        self.coordinator.recover_and_purge()
        request = (
            self.coordinator.issue_expert_state_root_account_lock_request()
        )
        self.authority = facade.acquire_expert_journal_root(request)
        self.manifest = _manifest_fixture()
        self.cursor = _genesis_cursor(self.manifest)
        self.gate = mock.patch.object(
            store_module,
            "_creation_gate",
            return_value=1,
        )
        self.gate.start()
        self.writer = facade.create_expert_journal(
            self.authority,
            self.manifest,
            self.cursor,
            persistence_authorizer=object(),  # type: ignore[arg-type]
            coordinator=self.coordinator,
        )

    def tearDown(self) -> None:
        try:
            state = store_module._WRITERS.get(self.writer)
            if state is not None and state.state not in {"closed", "poisoned"}:
                with mock.patch.object(
                    store_module,
                    "_purge_names",
                    return_value=None,
                ):
                    facade.abort_expert_writer(self.writer)
        except Exception:
            pass
        self.gate.stop()
        self.coordinator.close()
        self.temporary.cleanup()

    def paths(self) -> tuple[Path, Path, Path]:
        base = self.root_path / "state" / "expert-v1"
        return (
            base / "markers" / (
                self.manifest.session_id + ".expert-retention-v1.json"
            ),
            base / "sessions" / (
                self.manifest.session_id + ".expert-reserve-v1"
            ),
            base / "sessions" / (
                self.manifest.session_id + ".expert-journal-v1"
            ),
        )

    def prove_tail(
        self,
        *,
        unseen: object | None = None,
        second_unseen: object | None = None,
        parent_digest: str | None = None,
        clean: bool = True,
        reason: str = "operator_stop",
        published_cursor: object | None = None,
        event_mutation: tuple[int, dict[str, object]] | None = None,
    ) -> object | None:
        def event_clone(**changes: object):
            source = raw_parent()
            target = object.__new__(type(source))
            for item in fields(source):
                object.__setattr__(
                    target,
                    item.name,
                    changes.get(item.name, getattr(source, item.name)),
                )
            return target

        start = event_clone(
            record_kind=store_module.RecordKind.CONTROL,
            ingest_seq=1,
            event_type="SESSION_START",
        )
        phase1_manifest, _ = make_manifest_decision(
            self.manifest.session_id
        )
        raw_count = int(unseen is not None) + int(
            second_unseen is not None
        )
        last_raw_ingest_seq = (
            second_unseen.ingest_seq
            if second_unseen is not None
            else unseen.ingest_seq
            if unseen is not None
            else 0
        )
        terminal_payload = store_module.canonical_json_bytes(
            {
                "terminal_version": 1,
                "clean": clean,
                "reason": reason,
                "trace_sha256": "0" * 64,
                "final_state_sha256": "1" * 64,
                "record_count_before_terminal": 1 + 2 * raw_count,
                "raw_count": raw_count,
                "derived_count": raw_count,
                "last_applied_raw_seq": last_raw_ingest_seq,
                "config_file_sha256": (
                    phase1_manifest.config_file_sha256
                ),
                "config_canonical_sha256": (
                    phase1_manifest.config_canonical_sha256
                ),
                "code_sha256": phase1_manifest.code_sha256,
                "session_manifest_sha256": (
                    store_module.session_manifest_sha256(
                        phase1_manifest
                    )
                ),
                "provider_manifest_file_sha256": (
                    phase1_manifest.provider_manifest_file_sha256
                ),
                "provider_manifest_canonical_sha256": (
                    phase1_manifest.provider_manifest_canonical_sha256
                ),
                "entitlement_id_sha256": (
                    phase1_manifest.entitlement_id_sha256
                ),
                "permission_artifact_sha256": (
                    phase1_manifest.permission_artifact_sha256
                ),
                "qualification_artifact_sha256": (
                    phase1_manifest.qualification_artifact_sha256
                ),
                "qualification_trace_sha256": (
                    phase1_manifest.qualification_trace_sha256
                ),
                "adapter_code_sha256": (
                    phase1_manifest.adapter_code_sha256
                ),
                "auth_contract_sha256": (
                    phase1_manifest.auth_contract_sha256
                ),
                "quota_contract_sha256": (
                    phase1_manifest.quota_contract_sha256
                ),
                "required_retention_until_ns": (
                    phase1_manifest.required_retention_until_ns
                ),
                "research_evaluable": False,
            }
        )
        terminal = event_clone(
            record_kind=store_module.RecordKind.CONTROL,
            ingest_seq=(
                6
                if second_unseen is not None
                else 4
                if unseen is not None
                else 2
            ),
            event_type="SESSION_HALT",
            session_id=phase1_manifest.session_id,
            local_wall_ns=phase1_manifest.created_wall_ns,
            local_monotonic_ns=0,
            payload=terminal_payload,
            payload_sha256=store_module.sha256(terminal_payload).hexdigest(),
        )
        events = [start]
        if unseen is not None:
            events.append(unseen)
            events.append(
                event_clone(
                    record_kind=store_module.RecordKind.DERIVED,
                    ingest_seq=3,
                    parent_ingest_seq=unseen.ingest_seq,
                )
            )
        if second_unseen is not None:
            events.append(second_unseen)
            events.append(
                event_clone(
                    record_kind=store_module.RecordKind.DERIVED,
                    ingest_seq=5,
                    parent_ingest_seq=second_unseen.ingest_seq,
                )
            )
        events.append(terminal)
        if event_mutation is not None:
            event_index, changes = event_mutation
            source = events[event_index]
            mutated = object.__new__(type(source))
            for item in fields(source):
                object.__setattr__(
                    mutated,
                    item.name,
                    changes.get(item.name, getattr(source, item.name)),
                )
            events[event_index] = mutated

        class Phase1Reader:
            def iter_records(
                inner_self,
                *,
                diagnostic_prefix: bool = False,
            ):
                if not clean and not diagnostic_prefix:
                    raise AssertionError(
                        "halted Phase-1 tail requires diagnostic iteration"
                    )
                return iter(events)

            def close(inner_self) -> None:
                return None

        def digest(event: object) -> str:
            if event is start:
                return self.manifest.evidence_session_start_record_sha256
            if event is unseen and parent_digest is not None:
                return parent_digest
            return "e" * 64

        phase1_identities = (object(), object())
        companion_identities = (object(), object())
        with mock.patch.object(
            store_module,
            "_require_authorizer",
            return_value=phase1_manifest,
        ), mock.patch.object(
            store_module,
            "_phase1_sample_wall_ns",
            return_value=2,
        ), mock.patch.object(
            RetentionCoordinator,
            "issue_read_capability",
            return_value=object(),
        ), mock.patch.object(
            store_module.JournalReader,
            "open",
            return_value=Phase1Reader(),
        ), mock.patch.object(
            store_module,
            "canonical_record_sha256",
            side_effect=digest,
        ), mock.patch.object(
            store_module,
            "inspect_phase1_evidence_file_identities",
            return_value=phase1_identities,
        ), mock.patch.object(
            store_module,
            "inspect_expert_companion_file_identities",
            return_value=companion_identities,
        ):
            return facade.prove_expert_live_evidence_tail(
                self.writer,
                published_cursor=(
                    self.cursor
                    if published_cursor is None
                    else published_cursor
                ),
            )

    def build_private_terminal(
        self,
        final_cursor: object,
    ) -> tuple[object, object]:
        universe, policy, expert_manifest = task6_artifacts()
        final_state = initial_expert_state(
            expert_manifest,
            universe,
            policy,
        )
        with mock.patch.object(
            store_module,
            "expert_state_sha256",
            return_value=final_cursor.expert_state_sha256,
        ), mock.patch.object(
            store_module,
            "_terminal_material_gate",
            return_value=None,
        ), mock.patch.object(
            store_module,
            "canonical_record_sha256",
            return_value=final_cursor.last_parent_record_sha256,
        ):
            return facade.build_aligned_expert_terminal(
                self.writer,
                final_state=final_state,
                final_cursor=final_cursor,
            )

    def bind_emergency_permit(self):
        group, payloads, candidate = _group_fixture(
            self.manifest,
            self.cursor,
        )
        unseen = raw_parent(
            session_id=self.manifest.session_id,
            ingest_seq=group.parent.ingest_seq,
            event_type=group.parent.event_type,
            event_version=group.parent.event_version,
            local_wall_ns=group.parent.local_wall_ns,
            local_monotonic_ns=group.parent.local_monotonic_ns,
            clock_uncertainty_ns=group.parent.clock_uncertainty_ns,
        )
        self.prove_tail(
            unseen=unseen,
            parent_digest=group.parent.record_sha256,
        )
        frame = store_module.encode_expert_group_frame(
            group,
            payloads,
            prior_cursor=self.cursor,
        )
        required = 67_108_864 + 1_048_652 + len(frame)
        with mock.patch.object(
            store_module,
            "_live_gate",
            return_value=2,
        ), mock.patch.object(
            store_module,
            "canonical_record_sha256",
            return_value=group.parent.record_sha256,
        ), (
            mock.patch.object(
                store_module.os,
                "fstatvfs",
                return_value=mock.Mock(f_bavail=required, f_frsize=1),
            )
        ):
            with self.assertRaises(ExpertPrewriteCapacityError) as raised:
                facade.issue_expert_append_permit(
                    self.writer,
                    self.cursor.expert_state_sha256,
                    self.cursor,
                    group,
                    payloads,
                )
        evidence_terminal, terminal = self.build_private_terminal(candidate)
        with mock.patch.object(
            store_module,
            "_terminal_gate",
            return_value=None,
        ), mock.patch.object(
            store_module,
            "canonical_record_sha256",
            return_value=group.parent.record_sha256,
        ):
            permit = facade.issue_expert_emergency_append_permit(
                self.writer,
                expected_state_sha256=self.cursor.expert_state_sha256,
                expected_cursor=self.cursor,
                evidence_terminal=evidence_terminal,
                group=group,
                payloads=payloads,
                terminal=terminal,
            )
        return permit, group, payloads, candidate, terminal

    def test_reserve_is_exactly_physically_allocated_and_bound_to_immutable_private_identity(
        self,
    ) -> None:
        marker, reserve, journal = self.paths()
        self.assertTrue(marker.is_file())
        self.assertTrue(reserve.is_file())
        self.assertTrue(journal.is_file())
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(reserve.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o600)
        self.assertEqual(reserve.stat().st_size, 17_825_868)
        self.assertGreaterEqual(reserve.stat().st_blocks * 512, 17_825_868)
        marker_value = store_module._decode_expert_marker(
            marker.read_bytes()
        )
        self.assertEqual(marker_value["created_at_ns"], 1)
        content = journal.read_bytes()
        self.assertEqual(content[:16], b"INCIXJ01\x00\x01\x00\x00\x00\x00\x00\x10")
        self.assertEqual(
            store_module.decode_expert_manifest_frame(content[16:]),
            self.manifest,
        )

    def test_create_orders_marker_reserve_journal_and_all_required_fsyncs_before_return(
        self,
    ) -> None:
        self.assertEqual(
            store_module._decode_expert_marker(self.paths()[0].read_bytes())[
                "journal_basename"
            ],
            self.paths()[2].name,
        )
        for path in self.paths():
            self.assertGreater(path.stat().st_size, 0)

        temporary = tempfile.TemporaryDirectory()
        root_path = Path(temporary.name).resolve()
        coordinator = RetentionCoordinator.acquire(
            make_config(root_path / "state"),
            clock_ns=MutableClock(1),
        )
        coordinator.recover_and_purge()
        authority = facade.acquire_expert_journal_root(
            coordinator.issue_expert_state_root_account_lock_request()
        )
        root = store_module._ROOTS[authority]
        manifest = _manifest_fixture()
        cursor = _genesis_cursor(manifest)
        events: list[str] = []
        roles: dict[int, str] = {
            root.markers_fd: "markers_directory",
            root.sessions_fd: "sessions_directory",
        }
        real_open = store_module.os.open
        real_write = store_module._complete_write
        real_allocate = store_module._allocate_reserve
        real_fsync = store_module.os.fsync

        def gated(*_: object, **__: object) -> int:
            events.append("gate")
            return len([item for item in events if item == "gate"])

        def opened(name: object, *args: object, **kwargs: object) -> int:
            descriptor = real_open(name, *args, **kwargs)
            if type(name) is str:
                if name.endswith(".expert-retention-v1.json"):
                    roles[descriptor] = "marker"
                    events.append("open_marker")
                elif name.endswith(".expert-reserve-v1"):
                    roles[descriptor] = "reserve"
                    events.append("open_reserve")
                elif name.endswith(".expert-journal-v1"):
                    roles[descriptor] = "journal"
                    events.append("open_journal")
            return descriptor

        def written(descriptor: int, content: bytes) -> None:
            if content.startswith(b"{"):
                events.append("write_marker")
            elif content.startswith(b"INCIXJ01"):
                events.append("write_header")
            else:
                events.append("write_manifest")
            real_write(descriptor, content)

        def allocated(descriptor: int) -> None:
            events.append("allocate_reserve")
            real_allocate(descriptor)

        def synced(descriptor: int) -> None:
            events.append("fsync_" + roles[descriptor])
            real_fsync(descriptor)

        with mock.patch.object(
            store_module,
            "_creation_gate",
            side_effect=gated,
        ), mock.patch.object(
            store_module.os,
            "open",
            side_effect=opened,
        ), mock.patch.object(
            store_module,
            "_complete_write",
            side_effect=written,
        ), mock.patch.object(
            store_module,
            "_allocate_reserve",
            side_effect=allocated,
        ), mock.patch.object(
            store_module.os,
            "fsync",
            side_effect=synced,
        ):
            writer = facade.create_expert_journal(
                authority,
                manifest,
                cursor,
                persistence_authorizer=object(),  # type: ignore[arg-type]
                coordinator=coordinator,
            )
            events.append("return")
        self.assertEqual(
            events,
            [
                "gate",
                "open_marker",
                "write_marker",
                "fsync_marker",
                "fsync_markers_directory",
                "gate",
                "open_reserve",
                "allocate_reserve",
                "fsync_reserve",
                "fsync_sessions_directory",
                "gate",
                "open_journal",
                "write_header",
                "write_manifest",
                "fsync_journal",
                "fsync_sessions_directory",
                "gate",
                "return",
            ],
        )
        facade.abort_expert_writer(writer)
        coordinator.close()
        temporary.cleanup()

    def test_create_complete_write_handles_partial_progress_and_rejects_zero_or_error_at_every_object(
        self,
    ) -> None:
        read_fd, write_fd = os.pipe()
        try:
            original = store_module.os.write
            calls: list[int] = []

            def partial(fd: int, content: object) -> int:
                data = bytes(content)
                calls.append(len(data))
                return original(fd, data[: max(1, len(data) // 2)])

            with mock.patch.object(store_module.os, "write", side_effect=partial):
                store_module._complete_write(write_fd, b"abcdefgh")
            os.close(write_fd)
            write_fd = -1
            self.assertEqual(os.read(read_fd, 8), b"abcdefgh")
            self.assertGreater(len(calls), 1)
            null_fd = os.open(os.devnull, os.O_WRONLY)
            try:
                with mock.patch.object(store_module.os, "write", return_value=0):
                    with self.assertRaises(OSError):
                        store_module._complete_write(null_fd, b"x")
            finally:
                os.close(null_fd)
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)

        for failure_call in (1, 2, 3):
            with self.subTest(failure_call=failure_call):
                temporary = tempfile.TemporaryDirectory()
                root_path = Path(temporary.name).resolve()
                coordinator = RetentionCoordinator.acquire(
                    make_config(root_path / "state"),
                    clock_ns=MutableClock(1),
                )
                coordinator.recover_and_purge()
                authority = facade.acquire_expert_journal_root(
                    coordinator.issue_expert_state_root_account_lock_request()
                )
                manifest = _manifest_fixture()
                cursor = _genesis_cursor(manifest)
                original_write = store_module.os.write
                attempted = 0

                def fail_at_call(
                    descriptor: int,
                    content: object,
                ) -> int:
                    nonlocal attempted
                    attempted += 1
                    if attempted == failure_call:
                        if failure_call == 2:
                            raise OSError(
                                errno.EIO,
                                "secret-create-device",
                            )
                        return 0
                    return original_write(descriptor, content)

                with mock.patch.object(
                    store_module,
                    "_creation_gate",
                    return_value=1,
                ), mock.patch.object(
                    store_module.os,
                    "write",
                    side_effect=fail_at_call,
                ):
                    with self.assertRaises(OSError):
                        facade.create_expert_journal(
                            authority,
                            manifest,
                            cursor,
                            persistence_authorizer=object(),  # type: ignore[arg-type]
                            coordinator=coordinator,
                        )
                self.assertEqual(attempted, failure_call)
                expert_root = root_path / "state" / "expert-v1"
                self.assertEqual(
                    tuple((expert_root / "sessions").iterdir()),
                    (),
                )
                self.assertEqual(
                    tuple((expert_root / "markers").iterdir()),
                    (),
                )
                coordinator.close()
                temporary.cleanup()

    def test_create_denial_at_each_of_four_seams_writes_nothing_further_and_purges(
        self,
    ) -> None:
        for denied_seam in range(4):
            with self.subTest(denied_seam=denied_seam):
                temporary = tempfile.TemporaryDirectory()
                root_path = Path(temporary.name).resolve()
                coordinator = RetentionCoordinator.acquire(
                    make_config(root_path / "state"),
                    clock_ns=MutableClock(1),
                )
                coordinator.recover_and_purge()
                authority = facade.acquire_expert_journal_root(
                    coordinator.issue_expert_state_root_account_lock_request()
                )
                manifest = _manifest_fixture()
                cursor = _genesis_cursor(manifest)
                gates: list[object] = [1] * denied_seam
                gates.append(ExpertLiveAuthorizationDenied())
                with mock.patch.object(
                    store_module,
                    "_creation_gate",
                    side_effect=gates,
                ):
                    with self.assertRaises(ExpertLiveAuthorizationDenied):
                        facade.create_expert_journal(
                            authority,
                            manifest,
                            cursor,
                            persistence_authorizer=object(),  # type: ignore[arg-type]
                            coordinator=coordinator,
                        )
                expert_root = root_path / "state" / "expert-v1"
                self.assertEqual(
                    tuple((expert_root / "sessions").iterdir()),
                    (),
                )
                self.assertEqual(
                    tuple((expert_root / "markers").iterdir()),
                    (),
                )
                coordinator.close()
                temporary.cleanup()

    def test_append_group_consumes_one_permit_complete_writes_fsyncs_once_and_enters_receipt_pending(
        self,
    ) -> None:
        group, payloads, candidate = _group_fixture(
            self.manifest,
            self.cursor,
        )
        with mock.patch.object(store_module, "_live_gate", return_value=2):
            permit = facade.issue_expert_append_permit(
                self.writer,
                self.cursor.expert_state_sha256,
                self.cursor,
                group,
                payloads,
            )
            with self.assertRaises(ValueError):
                facade.issue_expert_terminal_permit(
                    self.writer,
                    _terminal_fixture(self.manifest, candidate),
                )
            receipt = facade.append_expert_group(permit)
            with self.assertRaises(ValueError):
                facade.issue_expert_append_permit(
                    self.writer,
                    candidate.expert_state_sha256,
                    candidate,
                    group,
                    payloads,
                )
            facade.acknowledge_expert_publication(
                self.writer,
                receipt=receipt,
                candidate_state_sha256=candidate.expert_state_sha256,
                candidate_cursor=candidate,
            )
        reader = facade.issue_expert_read_capability(
            self.authority,
            self.manifest,
        )
        try:
            self.assertEqual(facade.read_expert_manifest(reader), self.manifest)
            self.assertEqual(
                facade.read_next_expert_group(reader),
                (group, payloads),
            )
            terminal, summary = facade.read_expert_terminal_and_summary(reader)
            self.assertIsNone(terminal)
            self.assertEqual(summary.group_count, 1)
            self.assertEqual(
                summary.issue.value,
                "missing_terminal",
            )
        finally:
            facade.close_expert_reader(reader)

    def test_acknowledgement_is_not_a_second_cas_and_only_exact_receipt_candidate_clears_pending(
        self,
    ) -> None:
        group, payloads, candidate = _group_fixture(
            self.manifest,
            self.cursor,
        )
        with mock.patch.object(store_module, "_live_gate", return_value=2):
            permit = facade.issue_expert_append_permit(
                self.writer,
                self.cursor.expert_state_sha256,
                self.cursor,
                group,
                payloads,
            )
            receipt = facade.append_expert_group(permit)
            mutated = replace(
                receipt,
                durable_end_offset=receipt.durable_end_offset + 1,
            )
            with self.assertRaises(ValueError):
                facade.acknowledge_expert_publication(
                    self.writer,
                    receipt=mutated,
                    candidate_state_sha256=(
                        candidate.expert_state_sha256
                    ),
                    candidate_cursor=candidate,
                )
        self.assertEqual(store_module._WRITERS[self.writer].state, "poisoned")

        self.tearDown()
        self.setUp()
        group, payloads, candidate = _group_fixture(
            self.manifest,
            self.cursor,
        )
        with mock.patch.object(store_module, "_live_gate", return_value=2):
            permit = facade.issue_expert_append_permit(
                self.writer,
                self.cursor.expert_state_sha256,
                self.cursor,
                group,
                payloads,
            )
            receipt = facade.append_expert_group(permit)
            with mock.patch.object(
                store_module,
                "_candidate_cursor",
                side_effect=AssertionError("second CAS forbidden"),
            ):
                facade.acknowledge_expert_publication(
                    self.writer,
                    receipt=receipt,
                    candidate_state_sha256=(
                        candidate.expert_state_sha256
                    ),
                    candidate_cursor=candidate,
                )
        self.assertEqual(
            store_module._WRITERS[self.writer].state,
            "ordinary_ready",
        )

    def test_append_pass_then_enospc_is_uncertain_poisoned_and_cannot_use_reserve(
        self,
    ) -> None:
        group, payloads, candidate = _group_fixture(
            self.manifest,
            self.cursor,
        )
        before = self.paths()[2].read_bytes()
        with mock.patch.object(store_module, "_live_gate", return_value=2):
            permit = facade.issue_expert_append_permit(
                self.writer,
                self.cursor.expert_state_sha256,
                self.cursor,
                group,
                payloads,
            )
            with mock.patch.object(
                store_module,
                "_complete_write",
                side_effect=OSError(errno.ENOSPC, "secret-device"),
            ):
                with self.assertRaises(OSError):
                    facade.append_expert_group(permit)
        self.assertEqual(self.paths()[2].read_bytes(), before)
        self.assertEqual(store_module._WRITERS[self.writer].state, "poisoned")
        with self.assertRaises(ValueError):
            facade.issue_expert_emergency_append_permit(
                self.writer,
                expected_state_sha256=self.cursor.expert_state_sha256,
                expected_cursor=self.cursor,
                evidence_terminal=raw_parent(
                    session_id=self.manifest.session_id
                ),
                group=group,
                payloads=payloads,
                terminal=_terminal_fixture(self.manifest, candidate),
            )

    def test_append_capacity_probe_is_same_lock_sole_cas_with_strict_equality(
        self,
    ) -> None:
        group, payloads, _ = _group_fixture(self.manifest, self.cursor)
        frame = store_module.encode_expert_group_frame(
            group,
            payloads,
            prior_cursor=self.cursor,
        )
        required = 67_108_864 + 1_048_652 + len(frame)
        fragment_size = 3
        available_blocks = required // fragment_size
        available_bytes = available_blocks * fragment_size
        values = mock.Mock(
            f_bavail=available_blocks,
            f_frsize=fragment_size,
        )
        with mock.patch.object(
            store_module,
            "_live_gate",
            return_value=2,
        ), mock.patch.object(
            store_module,
            "canonical_record_sha256",
            return_value=group.parent.record_sha256,
        ), (
            mock.patch.object(store_module.os, "fstatvfs", return_value=values)
        ):
            with self.assertRaises(ExpertPrewriteCapacityError) as raised:
                facade.issue_expert_append_permit(
                    self.writer,
                    self.cursor.expert_state_sha256,
                    self.cursor,
                    group,
                    payloads,
                )
        self.assertEqual(raised.exception.requested_bytes, len(frame))
        self.assertEqual(raised.exception.available_bytes, available_bytes)
        self.assertEqual(
            raised.exception.emergency_reserve_bytes,
            store_module.EXPERT_EMERGENCY_RESERVE_BYTES,
        )
        self.assertEqual(
            store_module._WRITERS[self.writer].state,
            "ordinary_ready",
        )

    def test_live_tail_accepts_capacity_halt_via_diagnostic_iteration(
        self,
    ) -> None:
        self.assertIsNone(
            self.prove_tail(
                clean=False,
                reason="disk_low",
            )
        )
        tail = store_module._WRITERS[self.writer].tail
        self.assertIsInstance(tail, dict)
        self.assertFalse(tail["terminal_payload"]["clean"])
        self.assertEqual(
            tail["terminal_payload"]["reason"],
            "disk_low",
        )

    def test_stale_cas_and_uncertain_write_poison_without_retry(self) -> None:
        group, payloads, _ = _group_fixture(self.manifest, self.cursor)
        with mock.patch.object(store_module, "_live_gate", return_value=2):
            with self.assertRaises(ValueError):
                facade.issue_expert_append_permit(
                    self.writer,
                    "f" * 64,
                    self.cursor,
                    group,
                    payloads,
                )
        self.assertEqual(store_module._WRITERS[self.writer].state, "poisoned")

    def test_ordinary_terminal_accepts_only_once_bound_store_built_pair_and_releases_reserve_once(
        self,
    ) -> None:
        caller_terminal = _terminal_fixture(self.manifest, self.cursor)
        before = self.paths()[2].read_bytes()
        with self.assertRaises(ValueError):
            facade.issue_expert_terminal_permit(
                self.writer,
                caller_terminal,
            )
        self.assertEqual(self.paths()[2].read_bytes(), before)
        self.assertEqual(store_module._WRITERS[self.writer].state, "poisoned")

        # Re-create after proving a live tail because caller-shaped equality is
        # intentionally insufficient once the first writer is poisoned.
        self.tearDown()
        self.setUp()
        self.assertIsNone(self.prove_tail())
        _, terminal = self.build_private_terminal(self.cursor)
        with mock.patch.object(
            store_module,
            "_terminal_gate",
            return_value=None,
        ):
            permit = facade.issue_expert_terminal_permit(
                self.writer,
                terminal,
            )
            self.assertFalse(self.paths()[1].exists())
            receipt = facade.append_expert_terminal(permit)
        self.assertTrue(receipt.reserve_already_consumed)
        self.assertEqual(store_module._WRITERS[self.writer].state, "closed")
        with self.assertRaises(ValueError):
            facade.append_expert_terminal(permit)
        reader = facade.issue_expert_read_capability(
            self.authority,
            self.manifest,
        )
        try:
            facade.read_expert_manifest(reader)
            self.assertIsNone(facade.read_next_expert_group(reader))
            observed, summary = facade.read_expert_terminal_and_summary(
                reader
            )
            self.assertEqual(observed, terminal)
            self.assertIsNone(summary.issue)
            self.assertTrue(summary.terminal_clean)
            self.assertTrue(summary.journal_valid)
        finally:
            facade.close_expert_reader(reader)

    def test_ordinary_terminal_capacity_write_fsync_end_offset_close_and_receipt_failure_matrix(
        self,
    ) -> None:
        self.assertIsNone(self.prove_tail())
        _, terminal = self.build_private_terminal(self.cursor)
        before = self.paths()[2].read_bytes()
        with mock.patch.object(store_module, "_terminal_gate", return_value=None):
            permit = facade.issue_expert_terminal_permit(
                self.writer,
                terminal,
            )
            with mock.patch.object(
                store_module.os,
                "fstatvfs",
                return_value=mock.Mock(f_bavail=0, f_frsize=1),
            ):
                with self.assertRaises(OSError):
                    facade.append_expert_terminal(permit)
        self.assertEqual(self.paths()[2].read_bytes(), before)
        self.assertEqual(store_module._WRITERS[self.writer].state, "poisoned")
        with self.assertRaises(ValueError):
            facade.append_expert_terminal(permit)

    def test_terminal_gate_revalidates_proven_phase1_and_marker_identities(
        self,
    ) -> None:
        self.assertIsNone(self.prove_tail())
        _, terminal = self.build_private_terminal(self.cursor)
        state = store_module._WRITERS[self.writer]
        state.authorizer = mock.Mock()
        state.authorizer.poll_session.return_value = False
        with mock.patch.object(
            store_module,
            "_require_authorizer",
            return_value=make_manifest_decision(
                self.manifest.session_id
            )[0],
        ), mock.patch.object(
            store_module,
            "_phase1_sample_wall_ns",
            return_value=2,
        ), mock.patch.object(
            store_module,
            "_validate_tail_static_identities",
            side_effect=ValueError("identity drift"),
        ) as validate:
            with self.assertRaises(ExpertLiveAuthorizationDenied):
                store_module._terminal_gate(state, terminal)
        validate.assert_called_once_with(state, state.tail)

    def test_emergency_append_first_use_identity_denial_writes_zero_bytes(
        self,
    ) -> None:
        self.tearDown()
        self.temporary = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temporary.name).resolve()
        self.clock = MutableClock(1)
        self.coordinator = RetentionCoordinator.acquire(
            make_config(self.root_path / "state"),
            clock_ns=self.clock,
        )
        self.coordinator.recover_and_purge()
        self.manifest = _manifest_fixture()
        self.cursor = _genesis_cursor(self.manifest)
        phase1_manifest, _ = make_manifest_decision(
            self.manifest.session_id
        )
        phase1_marker = (
            self.root_path
            / "state"
            / "retention-markers"
            / f"{phase1_manifest.session_id}.marker.json"
        )
        phase1_wal = (
            self.root_path
            / "state"
            / "sessions"
            / f"{phase1_manifest.session_id}.wal"
        )
        phase1_marker.write_bytes(b"phase1-marker-v1")
        phase1_marker.chmod(0o600)
        phase1_wal.write_bytes(b"INCIWAL\x00\x00\x01\x00\x00\x00\x00\x00\x10")
        phase1_wal.chmod(0o600)
        self.authority = facade.acquire_expert_journal_root(
            self.coordinator.issue_expert_state_root_account_lock_request()
        )
        self.gate = mock.patch.object(
            store_module,
            "_creation_gate",
            return_value=1,
        )
        self.gate.start()
        self.writer = facade.create_expert_journal(
            self.authority,
            self.manifest,
            self.cursor,
            persistence_authorizer=object(),  # type: ignore[arg-type]
            coordinator=self.coordinator,
        )

        permit, _, _, candidate, terminal = self.bind_emergency_permit()
        writer_state = store_module._WRITERS[self.writer]
        tail = writer_state.tail
        self.assertIsInstance(tail, dict)
        phase1_identities = (
            facade.inspect_phase1_evidence_file_identities(
                self.authority,
                session_manifest=tail["phase1_manifest"],
                session_start=tail["session_start"],
            )
        )
        companion_identities = (
            facade.inspect_expert_companion_file_identities(
                self.authority,
                manifest=self.manifest,
            )
        )
        tail["identities"] = phase1_identities + companion_identities

        with phase1_wal.open("r+b") as stream:
            stream.seek(0)
            stream.write(b"X")
            stream.flush()
            os.fsync(stream.fileno())

        journal = self.paths()[2]
        before = journal.read_bytes()
        journal_fd = writer_state.journal_fd
        writer_state.authorizer = mock.Mock()
        writer_state.authorizer.poll_session.return_value = False
        complete_write = store_module._complete_write
        real_fsync = os.fsync
        with mock.patch.object(
            store_module,
            "_require_authorizer",
            return_value=tail["phase1_manifest"],
        ), mock.patch.object(
            store_module,
            "_validate_tail_static_identities",
            wraps=store_module._validate_tail_static_identities,
        ) as identity_gate, mock.patch.object(
            store_module,
            "_complete_write",
            wraps=complete_write,
        ) as write, mock.patch.object(
            store_module.os,
            "fsync",
            wraps=real_fsync,
        ) as fsync:
            with self.assertRaises(ExpertLiveAuthorizationDenied):
                facade.append_expert_emergency_group_and_terminal(permit)
        self.assertEqual(journal.read_bytes(), before)
        identity_gate.assert_called_once_with(writer_state, tail)
        write.assert_not_called()
        self.assertNotIn(
            mock.call(journal_fd),
            fsync.call_args_list,
        )
        self.assertEqual(writer_state.state, "poisoned")

    def test_uncertain_close_consumes_descriptor_before_attempt_and_never_retries(
        self,
    ) -> None:
        self.assertIsNone(self.prove_tail())
        _, terminal = self.build_private_terminal(self.cursor)
        state = store_module._WRITERS[self.writer]
        reserve_fd = state.reserve_fd
        reserve_calls: list[int] = []
        original_close = os.close

        def uncertain_reserve_close(descriptor):
            reserve_calls.append(descriptor)
            if descriptor == reserve_fd:
                raise OSError("uncertain reserve close")
            return original_close(descriptor)

        with mock.patch.object(
            store_module,
            "_terminal_gate",
            return_value=None,
        ), mock.patch.object(
            store_module.os,
            "close",
            side_effect=uncertain_reserve_close,
        ):
            with self.assertRaises(OSError):
                facade.issue_expert_terminal_permit(
                    self.writer,
                    terminal,
                )
        self.assertEqual(reserve_calls.count(reserve_fd), 1)
        self.assertEqual(state.reserve_fd, -1)

        self.tearDown()
        self.setUp()
        self.assertIsNone(self.prove_tail())
        _, terminal = self.build_private_terminal(self.cursor)
        with mock.patch.object(
            store_module,
            "_terminal_gate",
            return_value=None,
        ):
            permit = facade.issue_expert_terminal_permit(
                self.writer,
                terminal,
            )
        state = store_module._WRITERS[self.writer]
        journal_fd = state.journal_fd
        journal_calls: list[int] = []

        def uncertain_journal_close(descriptor):
            journal_calls.append(descriptor)
            if descriptor == journal_fd:
                raise OSError("uncertain journal close")
            return original_close(descriptor)

        with mock.patch.object(
            store_module,
            "_terminal_gate",
            return_value=None,
        ), mock.patch.object(
            store_module.os,
            "close",
            side_effect=uncertain_journal_close,
        ):
            with self.assertRaises(OSError):
                facade.append_expert_terminal(permit)
        self.assertEqual(journal_calls.count(journal_fd), 1)
        self.assertEqual(state.journal_fd, -1)

        self.tearDown()
        self.setUp()
        emergency, _, _, _, _ = self.bind_emergency_permit()
        state = store_module._WRITERS[self.writer]
        emergency_fd = state.journal_fd
        emergency_calls: list[int] = []

        def uncertain_emergency_close(descriptor):
            emergency_calls.append(descriptor)
            if descriptor == emergency_fd:
                raise OSError("uncertain emergency close")
            return original_close(descriptor)

        with mock.patch.object(
            store_module,
            "_terminal_gate",
            return_value=None,
        ), mock.patch.object(
            store_module.os,
            "close",
            side_effect=uncertain_emergency_close,
        ):
            with self.assertRaises(OSError):
                facade.append_expert_emergency_group_and_terminal(
                    emergency
                )
        self.assertEqual(emergency_calls.count(emergency_fd), 1)
        self.assertEqual(state.journal_fd, -1)

    def test_emergency_permit_requires_proven_no_write_capacity_denial_one_unseen_raw_and_exact_bound_pair(
        self,
    ) -> None:
        group, payloads, candidate = _group_fixture(
            self.manifest,
            self.cursor,
        )
        caller_terminal = _terminal_fixture(self.manifest, candidate)
        evidence_terminal = raw_parent(
            session_id=self.manifest.session_id,
        )
        with self.assertRaises(ValueError):
            facade.issue_expert_emergency_append_permit(
                self.writer,
                expected_state_sha256=self.cursor.expert_state_sha256,
                expected_cursor=self.cursor,
                evidence_terminal=evidence_terminal,
                group=group,
                payloads=payloads,
                terminal=caller_terminal,
            )
        self.assertEqual(store_module._WRITERS[self.writer].state, "poisoned")

        self.tearDown()
        self.setUp()
        group, payloads, candidate = _group_fixture(
            self.manifest,
            self.cursor,
        )
        unseen = raw_parent(
            session_id=self.manifest.session_id,
            ingest_seq=group.parent.ingest_seq,
            event_type=group.parent.event_type,
            event_version=group.parent.event_version,
            local_wall_ns=group.parent.local_wall_ns,
            local_monotonic_ns=group.parent.local_monotonic_ns,
            clock_uncertainty_ns=group.parent.clock_uncertainty_ns,
        )
        self.assertIs(
            self.prove_tail(
                unseen=unseen,
                parent_digest=group.parent.record_sha256,
            ),
            unseen,
        )
        required = 67_108_864 + 1_048_652 + len(
            store_module.encode_expert_group_frame(
                group,
                payloads,
                prior_cursor=self.cursor,
            )
        )
        values = mock.Mock(f_bavail=required, f_frsize=1)
        with mock.patch.object(
            store_module,
            "_live_gate",
            return_value=2,
        ), mock.patch.object(
            store_module,
            "canonical_record_sha256",
            return_value=group.parent.record_sha256,
        ), (
            mock.patch.object(store_module.os, "fstatvfs", return_value=values)
        ):
            with self.assertRaises(ExpertPrewriteCapacityError):
                facade.issue_expert_append_permit(
                    self.writer,
                    self.cursor.expert_state_sha256,
                    self.cursor,
                    group,
                    payloads,
                )
        evidence_terminal, terminal = self.build_private_terminal(candidate)
        with mock.patch.object(
            store_module,
            "_terminal_gate",
            return_value=None,
        ), mock.patch.object(
            store_module,
            "canonical_record_sha256",
            return_value=group.parent.record_sha256,
        ):
            permit = facade.issue_expert_emergency_append_permit(
                self.writer,
                expected_state_sha256=self.cursor.expert_state_sha256,
                expected_cursor=self.cursor,
                evidence_terminal=evidence_terminal,
                group=group,
                payloads=payloads,
                terminal=terminal,
            )
            self.assertFalse(self.paths()[1].exists())
            receipt = facade.append_expert_emergency_group_and_terminal(
                permit
            )
        self.assertEqual(receipt.group_receipt.group_sha256, group.group_sha256)
        self.assertEqual(
            receipt.terminal_receipt.terminal_sha256,
            terminal.terminal_sha256,
        )
        self.assertEqual(store_module._WRITERS[self.writer].state, "closed")

    def test_live_tail_proves_zero_one_or_rejects_two_unseen_with_bounded_memory_and_physical_eof(
        self,
    ) -> None:
        self.assertIsNone(self.prove_tail())

        self.tearDown()
        self.setUp()
        first = raw_parent(
            session_id=self.manifest.session_id,
            ingest_seq=2,
        )
        self.assertIs(self.prove_tail(unseen=first), first)

        self.tearDown()
        self.setUp()
        first = raw_parent(
            session_id=self.manifest.session_id,
            ingest_seq=2,
        )
        second = raw_parent(
            session_id=self.manifest.session_id,
            ingest_seq=4,
        )
        with self.assertRaises(ValueError):
            self.prove_tail(
                unseen=first,
                second_unseen=second,
            )
        self.assertEqual(store_module._WRITERS[self.writer].state, "poisoned")
        source = inspect.getsource(
            store_module.prove_expert_live_evidence_tail
        )
        tree = ast.parse(source)
        self.assertFalse(
            any(
                isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp))
                for node in ast.walk(tree)
            )
        )

    def test_zero_unseen_covered_groups_anchor_terminal_to_last_covered_raw(
        self,
    ) -> None:
        group, payloads, candidate = _group_fixture(
            self.manifest,
            self.cursor,
        )
        covered_raw = raw_parent(
            session_id=self.manifest.session_id,
            ingest_seq=group.parent.ingest_seq,
            event_type=group.parent.event_type,
            event_version=group.parent.event_version,
            local_wall_ns=group.parent.local_wall_ns,
            local_monotonic_ns=group.parent.local_monotonic_ns,
            clock_uncertainty_ns=group.parent.clock_uncertainty_ns,
        )
        with mock.patch.object(
            store_module,
            "_live_gate",
            return_value=2,
        ), mock.patch.object(
            store_module,
            "canonical_record_sha256",
            return_value=group.parent.record_sha256,
        ):
            permit = facade.issue_expert_append_permit(
                self.writer,
                self.cursor.expert_state_sha256,
                self.cursor,
                group,
                payloads,
            )
            receipt = facade.append_expert_group(permit)
            facade.acknowledge_expert_publication(
                self.writer,
                receipt=receipt,
                candidate_state_sha256=candidate.expert_state_sha256,
                candidate_cursor=candidate,
            )
        self.assertIsNone(
            self.prove_tail(
                unseen=covered_raw,
                parent_digest=group.parent.record_sha256,
                published_cursor=candidate,
            )
        )
        _, terminal = self.build_private_terminal(candidate)
        self.assertEqual(
            terminal.last_parent_record_sha256,
            group.parent.record_sha256,
        )

    def test_live_tail_rejects_extra_companion_group_and_invalid_derived_grammar(
        self,
    ) -> None:
        group, payloads, _ = _group_fixture(self.manifest, self.cursor)
        frame = store_module.encode_expert_group_frame(
            group,
            payloads,
            prior_cursor=self.cursor,
        )
        state = store_module._WRITERS[self.writer]
        store_module._complete_write(state.journal_fd, frame)
        os.fsync(state.journal_fd)
        state.journal_identity = store_module._journal_identity(
            state.root,
            fd=state.journal_fd,
            basename=state.journal_basename,
            generation=state.generation,
        )
        with self.assertRaises(ValueError):
            self.prove_tail()

        self.tearDown()
        self.setUp()
        first = raw_parent(
            session_id=self.manifest.session_id,
            ingest_seq=2,
        )
        with self.assertRaises(ValueError):
            self.prove_tail(
                unseen=first,
                event_mutation=(
                    2,
                    {
                        "ingest_seq": 7,
                        "parent_ingest_seq": 999,
                    },
                ),
            )

    def test_emergency_combined_append_exercises_every_between_frame_failure_without_retry_or_publication(
        self,
    ) -> None:
        permit, group, payloads, _, _ = self.bind_emergency_permit()
        group_frame = store_module.encode_expert_group_frame(
            group,
            payloads,
            prior_cursor=self.cursor,
        )
        before = self.paths()[2].read_bytes()
        with mock.patch.object(
            store_module,
            "_terminal_gate",
            return_value=None,
        ), mock.patch.object(
            store_module.os,
            "fsync",
            side_effect=OSError("secret-between-frame-fsync"),
        ):
            with self.assertRaises(OSError):
                facade.append_expert_emergency_group_and_terminal(permit)
        content = self.paths()[2].read_bytes()
        self.assertEqual(content, before + group_frame)
        self.assertEqual(store_module._WRITERS[self.writer].state, "poisoned")
        with self.assertRaises(ValueError):
            facade.append_expert_emergency_group_and_terminal(permit)

    def test_live_tail_ordinary_ack_advances_only_exact_expert_journal_identity(
        self,
    ) -> None:
        group, payloads, candidate = _group_fixture(
            self.manifest,
            self.cursor,
        )
        unseen = raw_parent(
            session_id=self.manifest.session_id,
            ingest_seq=group.parent.ingest_seq,
            event_type=group.parent.event_type,
            event_version=group.parent.event_version,
            local_wall_ns=group.parent.local_wall_ns,
            local_monotonic_ns=group.parent.local_monotonic_ns,
            clock_uncertainty_ns=group.parent.clock_uncertainty_ns,
        )
        self.prove_tail(
            unseen=unseen,
            parent_digest=group.parent.record_sha256,
        )
        tail = store_module._WRITERS[self.writer].tail
        identities = tail["identities"]
        with mock.patch.object(
            store_module,
            "_live_gate",
            return_value=2,
        ), mock.patch.object(
            store_module,
            "canonical_record_sha256",
            return_value=group.parent.record_sha256,
        ), mock.patch.object(
            store_module,
            "inspect_phase1_evidence_file_identities",
            return_value=identities[:2],
        ), mock.patch.object(
            store_module,
            "inspect_expert_companion_file_identities",
            return_value=(identities[2], object()),
        ):
            permit = facade.issue_expert_append_permit(
                self.writer,
                self.cursor.expert_state_sha256,
                self.cursor,
                group,
                payloads,
            )
            receipt = facade.append_expert_group(permit)
            journal = self.paths()[2]
            content = bytearray(journal.read_bytes())
            content[-1] ^= 1
            with journal.open("r+b") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            with self.assertRaises(ValueError):
                facade.acknowledge_expert_publication(
                    self.writer,
                    receipt=receipt,
                    candidate_state_sha256=(
                        candidate.expert_state_sha256
                    ),
                    candidate_cursor=candidate,
                )
        self.assertEqual(store_module._WRITERS[self.writer].state, "poisoned")

        self.tearDown()
        self.setUp()
        group, payloads, candidate = _group_fixture(
            self.manifest,
            self.cursor,
        )
        unseen = raw_parent(
            session_id=self.manifest.session_id,
            ingest_seq=group.parent.ingest_seq,
            event_type=group.parent.event_type,
            event_version=group.parent.event_version,
            local_wall_ns=group.parent.local_wall_ns,
            local_monotonic_ns=group.parent.local_monotonic_ns,
            clock_uncertainty_ns=group.parent.clock_uncertainty_ns,
        )
        self.prove_tail(
            unseen=unseen,
            parent_digest=group.parent.record_sha256,
        )
        tail = store_module._WRITERS[self.writer].tail
        identities = tail["identities"]
        with mock.patch.object(
            store_module,
            "_live_gate",
            return_value=2,
        ), mock.patch.object(
            store_module,
            "canonical_record_sha256",
            return_value=group.parent.record_sha256,
        ), mock.patch.object(
            store_module,
            "inspect_phase1_evidence_file_identities",
            return_value=identities[:2],
        ), mock.patch.object(
            store_module,
            "inspect_expert_companion_file_identities",
            return_value=(identities[2], object()),
        ):
            permit = facade.issue_expert_append_permit(
                self.writer,
                self.cursor.expert_state_sha256,
                self.cursor,
                group,
                payloads,
            )
            receipt = facade.append_expert_group(permit)
            facade.acknowledge_expert_publication(
                self.writer,
                receipt=receipt,
                candidate_state_sha256=candidate.expert_state_sha256,
                candidate_cursor=candidate,
            )
        _, terminal = self.build_private_terminal(candidate)
        self.assertEqual(terminal.expert_group_count, 1)

    def test_aligned_terminal_maps_every_clean_and_halted_reason_and_rejects_every_field_mutation(
        self,
    ) -> None:
        cases = (
            (True, "operator_stop", ExpertTerminalReasonV1.OPERATOR_STOP),
            (True, "session_end", ExpertTerminalReasonV1.SESSION_END),
            (False, "operator_halt", ExpertTerminalReasonV1.EXPERT_HALT),
        )
        for index, (clean, reason, expected) in enumerate(cases):
            with self.subTest(clean=clean, reason=reason):
                if index:
                    self.tearDown()
                    self.setUp()
                self.assertIsNone(
                    self.prove_tail(clean=clean, reason=reason)
                )
                if index == 0:
                    tail = store_module._WRITERS[self.writer].tail
                    self.assertIsInstance(tail, dict)
                    evidence_terminal = tail["terminal"]
                    phase1_manifest, _ = make_manifest_decision(
                        self.manifest.session_id
                    )
                    original_payload = json.loads(
                        evidence_terminal.payload.decode("ascii")
                    )
                    for field_name, changed in (
                        ("raw_count", 1),
                        ("record_count_before_terminal", 2),
                        ("code_sha256", "f" * 64),
                        ("research_evaluable", True),
                    ):
                        with self.subTest(field=field_name):
                            payload = dict(original_payload)
                            payload[field_name] = changed
                            encoded = store_module.canonical_json_bytes(
                                payload
                            )
                            mutated = object.__new__(
                                type(evidence_terminal)
                            )
                            for item in fields(evidence_terminal):
                                object.__setattr__(
                                    mutated,
                                    item.name,
                                    encoded
                                    if item.name == "payload"
                                    else store_module.sha256(
                                        encoded
                                    ).hexdigest()
                                    if item.name == "payload_sha256"
                                    else getattr(
                                        evidence_terminal,
                                        item.name,
                                    ),
                                )
                            with self.assertRaises(ValueError):
                                store_module._decode_phase1_terminal_payload(
                                    mutated,
                                    phase1_manifest,
                                    raw_count=0,
                                    derived_count=0,
                                    last_raw_ingest_seq=0,
                                )
                _, terminal = self.build_private_terminal(self.cursor)
                self.assertIs(terminal.reason, expected)
                self.assertIs(terminal.clean, clean)
                universe, policy, expert_manifest = task6_artifacts()
                with self.assertRaises(ValueError):
                    facade.build_aligned_expert_terminal(
                        self.writer,
                        final_state=initial_expert_state(
                            expert_manifest,
                            universe,
                            policy,
                        ),
                        final_cursor=self.cursor,
                    )

    def test_reader_covers_every_cut_corruption_oversize_and_terminal_eof_with_one_group_memory(
        self,
    ) -> None:
        journal = self.paths()[2]
        original = journal.read_bytes()
        for cut in (0, 1, 15, 16, len(original) - 1):
            with self.subTest(cut=cut):
                with journal.open("r+b") as stream:
                    stream.truncate(cut)
                with self.assertRaises((ValueError, OSError)):
                    reader = facade.issue_expert_read_capability(
                        self.authority,
                        self.manifest,
                    )
                    try:
                        facade.read_expert_manifest(reader)
                    finally:
                        facade.close_expert_reader(reader)
                with journal.open("wb") as stream:
                    stream.write(original)
                os.chmod(journal, 0o600)

    def test_reader_and_identity_collectors_revalidate_descriptor_and_named_entry_around_every_read(
        self,
    ) -> None:
        marker_identity, journal_identity = (
            facade.inspect_expert_companion_file_identities(
                self.authority,
                manifest=self.manifest,
            )
        )
        self.assertEqual(marker_identity.role, "expert_marker")
        self.assertEqual(journal_identity.role, "expert_journal")
        self.assertEqual(marker_identity.mode, 0o600)
        self.assertEqual(journal_identity.mode, 0o600)
        marker = self.paths()[0]
        content = bytearray(marker.read_bytes())
        content[-2] ^= 1
        marker.write_bytes(bytes(content))
        os.chmod(marker, 0o600)
        with self.assertRaises(ValueError):
            facade.issue_expert_read_capability(
                self.authority,
                self.manifest,
            )

    def test_reader_close_rejects_wrong_thread_without_consuming_authority(
        self,
    ) -> None:
        reader = facade.issue_expert_read_capability(
            self.authority,
            self.manifest,
        )
        state = store_module._READERS[reader]
        descriptor = state.fd
        outcomes: list[object] = []

        def close_from_wrong_thread() -> None:
            try:
                facade.close_expert_reader(reader)
            except BaseException as error:
                outcomes.append(error)
            else:
                outcomes.append(None)

        worker = threading.Thread(target=close_from_wrong_thread)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIs(type(outcomes[0]), ValueError)
        self.assertIs(store_module._READERS.get(reader), state)
        self.assertFalse(state.closed)
        self.assertEqual(state.fd, descriptor)
        os.fstat(descriptor)

        facade.close_expert_reader(reader)
        self.assertNotIn(reader, store_module._READERS)
        with self.assertRaisesRegex(ValueError, "^expert_reader_invalid$"):
            facade.close_expert_reader(reader)

    def test_reader_close_rejects_pid_and_root_drift_before_mutation(
        self,
    ) -> None:
        reader = facade.issue_expert_read_capability(
            self.authority,
            self.manifest,
        )
        state = store_module._READERS[reader]
        descriptor = state.fd
        actual_pid = os.getpid()
        with (
            mock.patch.object(
                store_module.os,
                "getpid",
                return_value=actual_pid + 1,
            ),
            self.assertRaisesRegex(
                ValueError,
                "^expert_reader_invalid$",
            ),
        ):
            facade.close_expert_reader(reader)
        self.assertIs(store_module._READERS.get(reader), state)
        self.assertFalse(state.closed)
        self.assertEqual(state.fd, descriptor)

        state.root.active = False
        try:
            with self.assertRaisesRegex(
                ValueError,
                "^expert_reader_invalid$",
            ):
                facade.revoke_expert_reader(reader)
        finally:
            state.root.active = True
        self.assertIs(store_module._READERS.get(reader), state)
        self.assertFalse(state.closed)
        self.assertEqual(state.fd, descriptor)

        removed = store_module._ROOTS.pop(state.root.token)
        try:
            with self.assertRaisesRegex(
                ValueError,
                "^expert_reader_invalid$",
            ):
                facade.close_expert_reader(reader)
        finally:
            store_module._ROOTS[state.root.token] = removed
        self.assertIs(store_module._READERS.get(reader), state)
        self.assertFalse(state.closed)
        self.assertEqual(state.fd, descriptor)
        os.fstat(descriptor)

        facade.revoke_expert_reader(reader)
        self.assertNotIn(reader, store_module._READERS)

    def test_root_fatal_teardown_is_idempotent_and_closes_all_store_descendants_without_leaks(
        self,
    ) -> None:
        reader = facade.issue_expert_read_capability(
            self.authority,
            self.manifest,
        )
        writer_state = store_module._WRITERS[self.writer]
        reader_state = store_module._READERS[reader]
        writer_fd = writer_state.journal_fd
        reader_fd = reader_state.fd
        source_fds = tuple(
            descriptor
            for package in store_module._ROOTS[
                self.authority
            ].source_packages
            for descriptor in (package.directory_fd, package.init_fd)
        )
        store_module._fatal_root(store_module._ROOTS[self.authority])
        store_module._fatal_root(store_module._ROOTS[self.authority])
        self.assertEqual(writer_state.state, "poisoned")
        self.assertTrue(reader_state.closed)
        for descriptor in (writer_fd, reader_fd, *source_fds):
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_environment_collection_authority_is_one_shot_descriptor_stable_and_deadline_bound(
        self,
    ) -> None:
        phase1_manifest, _ = make_manifest_decision()

        def authority():
            token = object.__new__(ExpertEnvironmentCollectionAuthorityV1)
            state = store_module._EnvironmentAuthority(
                store_module._ROOTS[self.authority],
                object(),  # type: ignore[arg-type]
                self.coordinator,
                os.getpid(),
                threading.current_thread(),
            )
            store_module._ENVIRONMENTS[token] = state
            return token, state

        token, state = authority()
        with mock.patch.object(
            store_module,
            "_require_authorizer",
            return_value=phase1_manifest,
        ), mock.patch.object(
            store_module,
            "_phase1_sample_wall_ns",
            return_value=phase1_manifest.analysis_expires_at_ns,
        ):
            with self.assertRaises(ExpertLiveAuthorizationDenied):
                facade.collect_expert_current_environment(token)
        self.assertTrue(state.consumed)
        with self.assertRaises(ValueError):
            facade.collect_expert_current_environment(token)

        inventory_root = self.root_path / "inventory-mutation-matrix"
        inventory_sets = (
            store_module._EXPERT_INVENTORY,
            store_module._IO_INVENTORY,
            store_module._ADAPTER_INVENTORY,
            store_module._RUNTIME_INVENTORY,
            store_module._DEPENDENCY_INVENTORY,
        )
        for inventory_index, inventory in enumerate(inventory_sets):
            for logical in inventory:
                path = inventory_root / logical
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(logical.encode("ascii"))
            domain = (
                b"INCI-EXPERT-TEST-INVENTORY-V1\0"
                + bytes((inventory_index,))
            )
            expected = store_module._inventory_digest(
                inventory_root,
                inventory,
                domain,
            )
            for logical in inventory:
                with self.subTest(inventory_member=logical):
                    path = inventory_root / logical
                    original = path.read_bytes()
                    path.write_bytes(original + b"!")
                    self.assertNotEqual(
                        store_module._inventory_digest(
                            inventory_root,
                            inventory,
                            domain,
                        ),
                        expected,
                    )
                    path.write_bytes(original)

        token, state = authority()
        with mock.patch.object(
            store_module,
            "_require_authorizer",
            return_value=phase1_manifest,
        ), mock.patch.object(
            store_module,
            "_phase1_sample_wall_ns",
            return_value=1,
        ), mock.patch.object(
            store_module,
            "_schema_objects_fd",
            side_effect=OSError("secret-mutated-schema-path"),
        ):
            with self.assertRaises(ValueError) as caught:
                facade.collect_expert_current_environment(token)
        self.assertEqual(
            str(caught.exception),
            "expert_environment_collection_invalid",
        )
        self.assertNotIn("secret", str(caught.exception))
        self.assertTrue(state.consumed)
        with self.assertRaises(ValueError):
            facade.collect_expert_current_environment(token)

    def test_recovery_and_purge_classify_validate_and_delete_companion_first_in_exact_order(
        self,
    ) -> None:
        phase1_manifest, _ = make_manifest_decision(
            self.manifest.session_id
        )
        start_frame = session_start_frame(phase1_manifest)
        prefix = struct.unpack(">4sBBHQQII", start_frame[:32])
        metadata_end = 32 + prefix[6]
        payload_end = metadata_end + prefix[7]
        start = store_module.decode_record(
            start_frame[32:metadata_end],
            start_frame[metadata_end:payload_end],
        )
        marker = store_module.RetentionMarker(
            1,
            phase1_manifest.session_id,
            f"{phase1_manifest.session_id}.wal",
            f"{phase1_manifest.session_id}.reserve",
            phase1_manifest.required_retention_until_ns,
            store_module.session_manifest_sha256(phase1_manifest),
            "a" * 64,
            phase1_manifest.provider_manifest_file_sha256,
            phase1_manifest.entitlement_id_sha256,
            phase1_manifest.qualification_artifact_sha256,
            1,
        )
        phase1_marker_path = (
            self.root_path
            / "state"
            / "retention-markers"
            / f"{phase1_manifest.session_id}.marker.json"
        )
        phase1_wal_path = (
            self.root_path
            / "state"
            / "sessions"
            / f"{phase1_manifest.session_id}.wal"
        )
        phase1_marker_path.write_bytes(
            store_module.canonical_json_bytes(
                {
                    item.name: getattr(marker, item.name)
                    for item in fields(marker)
                }
            )
        )
        phase1_wal_path.write_bytes(
            struct.pack(">8sHHI", b"INCIWAL\x00", 1, 0, 16)
            + start_frame
        )
        os.chmod(phase1_marker_path, 0o600)
        os.chmod(phase1_wal_path, 0o600)
        expert_marker = {
            "session_id": phase1_manifest.session_id,
            "evidence_session_manifest_sha256": (
                store_module.session_manifest_sha256(phase1_manifest)
            ),
            "evidence_session_start_record_sha256": (
                store_module.canonical_record_sha256(start)
            ),
            "provider_request_binding_sha256": "a" * 64,
        }
        store_module._validate_phase1_evidence_binding(
            store_module._ROOTS[self.authority],
            expert_marker,
        )
        original_wal = phase1_wal_path.read_bytes()
        phase1_wal_path.write_bytes(b"X" + original_wal[1:])
        os.chmod(phase1_wal_path, 0o600)
        with self.assertRaises(ValueError):
            store_module._validate_phase1_evidence_binding(
                store_module._ROOTS[self.authority],
                expert_marker,
            )
        phase1_wal_path.write_bytes(original_wal)
        os.chmod(phase1_wal_path, 0o600)
        with mock.patch.object(
            store_module,
            "_phase1_sample_wall_ns",
            return_value=1,
        ):
            capability = facade.issue_expert_purge_capability(
                self.authority,
                self.manifest,
            )
        root = store_module._ROOTS[self.authority]
        events: list[tuple[object, ...]] = []
        original_unlink = store_module._unlink_if_present
        original_fsync = os.fsync
        original_recovery = RetentionCoordinator.recover_and_purge

        def observed_unlink(directory_fd, basename):
            role = (
                "sessions"
                if directory_fd == root.sessions_fd
                else "markers"
            )
            events.append(("unlink", role, basename))
            return original_unlink(directory_fd, basename)

        def observed_fsync(descriptor):
            if descriptor == root.sessions_fd:
                events.append(("fsync", "sessions"))
            elif descriptor == root.markers_fd:
                events.append(("fsync", "markers"))
            return original_fsync(descriptor)

        def observed_recovery(coordinator):
            events.append(("phase1_recover_and_purge",))
            return original_recovery(coordinator)

        with mock.patch.object(
            store_module,
            "_unlink_if_present",
            side_effect=observed_unlink,
        ), mock.patch.object(
            store_module.os,
            "fsync",
            side_effect=observed_fsync,
        ), mock.patch.object(
            RetentionCoordinator,
            "recover_and_purge",
            new=observed_recovery,
        ), mock.patch.object(
            store_module,
            "_phase1_sample_wall_ns",
            return_value=1,
        ):
            facade.purge_expert_session(capability)
        self.assertEqual(
            events[:6],
            [
                (
                    "unlink",
                    "sessions",
                    f"{self.manifest.session_id}.expert-journal-v1",
                ),
                (
                    "unlink",
                    "sessions",
                    f"{self.manifest.session_id}.expert-reserve-v1",
                ),
                ("fsync", "sessions"),
                (
                    "unlink",
                    "markers",
                    f"{self.manifest.session_id}.expert-retention-v1.json",
                ),
                ("fsync", "markers"),
                ("phase1_recover_and_purge",),
            ],
        )
        self.assertTrue(all(not path.exists() for path in self.paths()))
        with self.assertRaises(ValueError):
            facade.purge_expert_session(capability)
        self.assertTrue(store_module._ROOTS[self.authority].active)

        for classification, failure, expected in (
            (
                "evidence_missing",
                FileNotFoundError("missing"),
                ExpertPurgeReportV1(
                    (),
                    (self.manifest.session_id,),
                    (),
                    (),
                ),
            ),
            (
                "evidence_replaced",
                ValueError("replaced"),
                ExpertPurgeReportV1(
                    (),
                    (),
                    (self.manifest.session_id,),
                    (),
                ),
            ),
        ):
            with self.subTest(classification=classification):
                self.tearDown()
                self.setUp()
                with mock.patch.object(
                    store_module,
                    "_validate_phase1_evidence_binding",
                    side_effect=failure,
                ):
                    report = facade.recover_and_purge_expert_journals(
                        self.authority
                    )
                self.assertEqual(report, expected)
                self.assertTrue(
                    all(not path.exists() for path in self.paths())
                )

        self.tearDown()
        self.setUp()
        self.clock.now_ns = (
            self.manifest.retention.retention_delete_by_ns
        )
        report = facade.recover_and_purge_expert_journals(self.authority)
        self.assertEqual(
            report,
            ExpertPurgeReportV1(
                (self.manifest.session_id,),
                (),
                (),
                (),
            ),
        )

        self.tearDown()
        self.setUp()
        self.paths()[0].unlink()
        report = facade.recover_and_purge_expert_journals(self.authority)
        self.assertEqual(
            report,
            ExpertPurgeReportV1(
                (),
                (),
                (),
                (self.manifest.session_id,),
            ),
        )

        for failure_seam in (
            "journal_unlink",
            "reserve_unlink",
            "sessions_fsync",
            "marker_unlink",
            "markers_fsync",
        ):
            with self.subTest(purge_failure_seam=failure_seam):
                self.tearDown()
                self.setUp()
                root = store_module._ROOTS[self.authority]
                capability = facade.issue_expert_purge_capability(
                    self.authority,
                    self.manifest,
                )
                original_unlink = store_module._unlink_if_present
                original_fsync = os.fsync

                def failing_unlink(directory_fd, basename):
                    role = (
                        "journal"
                        if basename.endswith(".expert-journal-v1")
                        else "reserve"
                        if basename.endswith(".expert-reserve-v1")
                        else "marker"
                    )
                    if failure_seam == f"{role}_unlink":
                        raise OSError("secret purge unlink")
                    return original_unlink(directory_fd, basename)

                def failing_fsync(descriptor):
                    role = (
                        "sessions"
                        if descriptor == root.sessions_fd
                        else "markers"
                        if descriptor == root.markers_fd
                        else None
                    )
                    if failure_seam == f"{role}_fsync":
                        raise OSError("secret purge fsync")
                    return original_fsync(descriptor)

                with mock.patch.object(
                    store_module,
                    "_unlink_if_present",
                    side_effect=failing_unlink,
                ), mock.patch.object(
                    store_module.os,
                    "fsync",
                    side_effect=failing_fsync,
                ):
                    with self.assertRaises(OSError) as caught:
                        facade.purge_expert_session(capability)
                self.assertEqual(
                    str(caught.exception),
                    "expert_journal_durability_failed",
                )
                self.assertEqual(
                    store_module._WRITERS[self.writer].state,
                    "poisoned",
                )
                with self.assertRaises(ValueError):
                    facade.purge_expert_session(capability)

    def test_create_append_terminal_emergency_and_purge_crash_seams_are_nonresumable(
        self,
    ) -> None:
        cases = (
            ("marker", "truncate", 1),
            ("marker", "unlink", None),
            ("reserve", "truncate", 0),
            ("journal", "truncate", 1),
            ("journal", "truncate", 16),
            ("journal", "truncate", -1),
        )
        first = True
        for role, operation, requested_cut in cases:
            for repetition in range(3):
                with self.subTest(
                    role=role,
                    operation=operation,
                    cut=requested_cut,
                    repetition=repetition,
                ):
                    if not first:
                        self.tearDown()
                        self.setUp()
                    first = False
                    target = {
                        "marker": self.paths()[0],
                        "reserve": self.paths()[1],
                        "journal": self.paths()[2],
                    }[role]
                    original_size = target.stat().st_size
                    cut = (
                        original_size - 1
                        if requested_cut == -1
                        else requested_cut
                    )
                    read_pipe, write_pipe = os.pipe()
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", DeprecationWarning)
                        child = os.fork()
                    if child == 0:
                        try:
                            os.close(write_pipe)
                            if os.read(read_pipe, 1) != b"!":
                                os._exit(91)
                            if operation == "unlink":
                                os.unlink(target)
                            else:
                                descriptor = os.open(target, os.O_WRONLY)
                                try:
                                    os.ftruncate(descriptor, cut)
                                    os.fsync(descriptor)
                                finally:
                                    os.close(descriptor)
                            os._exit(0)
                        except BaseException:
                            os._exit(92)
                    os.close(read_pipe)
                    os.write(write_pipe, b"!")
                    os.close(write_pipe)
                    waited, status = os.waitpid(child, 0)
                    self.assertEqual(waited, child)
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                    if operation == "unlink":
                        self.assertFalse(target.exists())
                    else:
                        self.assertEqual(target.stat().st_size, cut)
                    report = facade.recover_and_purge_expert_journals(
                        self.authority
                    )
                    classified = (
                        report.due_sessions
                        + report.evidence_missing_sessions
                        + report.evidence_replaced_sessions
                        + report.recovered_markers
                    )
                    self.assertEqual(
                        classified,
                        (self.manifest.session_id,),
                    )
                    self.assertTrue(
                        all(not path.exists() for path in self.paths())
                    )
                    self.assertEqual(
                        store_module._WRITERS[self.writer].state,
                        "poisoned",
                    )
                    group, payloads, _ = _group_fixture(
                        self.manifest,
                        self.cursor,
                    )
                    with self.assertRaises(ValueError):
                        facade.issue_expert_append_permit(
                            self.writer,
                            self.cursor.expert_state_sha256,
                            self.cursor,
                            group,
                            payloads,
                        )

    def test_recovery_rejects_marker_filename_content_session_mismatch_before_binding(
        self,
    ) -> None:
        marker_path = self.paths()[0]
        marker = store_module._decode_expert_marker(
            marker_path.read_bytes()
        )
        other_session = "87654321-4321-4321-8321-cba987654321"
        marker["session_id"] = other_session
        marker["journal_basename"] = store_module._journal_basename(
            other_session
        )
        marker["reserve_basename"] = store_module._reserve_basename(
            other_session
        )
        marker_path.write_bytes(store_module.canonical_json_bytes(marker))
        os.chmod(marker_path, 0o600)
        with mock.patch.object(
            store_module,
            "_validate_phase1_evidence_binding",
            side_effect=FileNotFoundError("wrong session"),
        ) as binding:
            report = facade.recover_and_purge_expert_journals(
                self.authority
            )
        binding.assert_not_called()
        self.assertEqual(
            report.evidence_replaced_sessions,
            (self.manifest.session_id,),
        )


class ExpertReplayAuthorityStoreTests(unittest.TestCase):
    _FATAL_ROOT_CHILD = "INCI_EXPERT_FATAL_ROOT_TEST_CHILD"
    _PREPARE_COLLISION_CHILD = "INCI_EXPERT_PREPARE_COLLISION_CHILD"

    def _run_current_test_case_in_subprocess(
        self,
        *,
        variable: str,
        marker: str,
    ) -> None:
        environment = os.environ.copy()
        environment[variable] = marker
        with tempfile.TemporaryDirectory() as cache_root:
            environment["PYTHONPYCACHEPREFIX"] = cache_root
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "unittest",
                    "-v",
                    self.id(),
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                timeout=120,
            )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def _run_fatal_root_test_in_subprocess(self) -> bool:
        test_id = self.id()
        if os.environ.get(self._FATAL_ROOT_CHILD) == test_id:
            return False
        environment = os.environ.copy()
        environment[self._FATAL_ROOT_CHILD] = test_id
        with tempfile.TemporaryDirectory() as cache_root:
            environment["PYTHONPYCACHEPREFIX"] = cache_root
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "unittest",
                    "-v",
                    test_id,
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                timeout=120,
            )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        return True

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temporary.name).resolve()
        self.clock = MutableClock(1)
        self.coordinator = RetentionCoordinator.acquire(
            make_config(self.root_path / "state"),
            clock_ns=self.clock,
        )
        self.coordinator.recover_and_purge()
        self.manifest, self.decision = make_manifest_decision()
        self.active_adapter = mock.Mock(
            adapter_code_sha256=self.manifest.adapter_code_sha256,
        )
        self.active_adapter_patch = mock.patch.object(
            store_module,
            "load_active_adapter_contract",
            return_value=self.active_adapter,
        )
        self.active_adapter_patch.start()
        self.synthetic_environment_fixture_enabled = False
        original_installed_environment = (
            store_module._installed_environment
        )

        def installed_environment_for_fixture(
            root: object,
            manifest: object,
            **keywords: object,
        ) -> object:
            if not self.synthetic_environment_fixture_enabled:
                return original_installed_environment(
                    root,
                    manifest,
                    **keywords,
                )
            with mock.patch.object(
                store_module,
                "_phase1_code_sha256_fd",
                return_value=manifest.code_sha256,
            ):
                return original_installed_environment(
                    root,
                    manifest,
                    **keywords,
                )

        self.installed_environment_patch = mock.patch.object(
            store_module,
            "_installed_environment",
            side_effect=installed_environment_for_fixture,
        )
        self.installed_environment_patch.start()
        self.provider_gate = mock.Mock()
        self.provider_gate.require_start.return_value = self.decision
        self.provider_gate.require_raw_persist.return_value = (
            self.manifest.required_retention_until_ns
        )
        self.provider_gate.require_analysis.return_value = self.decision
        self.provider_gate.require_close.return_value = self.decision
        self.authorizer = object.__new__(
            store_module.ProviderPersistenceAuthorizer
        )
        object.__setattr__(
            self.authorizer,
            "gate",
            self.provider_gate,
        )
        object.__setattr__(
            self.authorizer,
            "coordinator",
            self.coordinator,
        )
        object.__setattr__(
            self.authorizer,
            "session_manifest",
            self.manifest,
        )
        object.__setattr__(
            self.authorizer,
            "bound_decision",
            self.decision,
        )
        write_capability = self.coordinator.arm_before_wal(
            session_manifest=self.manifest,
            decision=self.decision,
            persistence_authorizer=self.authorizer,
        )
        phase1_writer = JournalWriter.create(
            write_capability=write_capability,
            session_manifest=self.manifest,
        )
        start_frame = session_start_frame(self.manifest)
        prefix = struct.unpack(">4sBBHQQII", start_frame[:32])
        metadata_end = 32 + prefix[6]
        payload_end = metadata_end + prefix[7]
        session_start = store_module.decode_record(
            start_frame[32:metadata_end],
            start_frame[metadata_end:payload_end],
        )
        self.session_start = session_start
        phase1_writer.close_clean(
            reason="operator_stop",
            trace_sha256=phase1_initial_trace(session_start).hex(),
            final_state_sha256=store_module.sha256(
                canonical_state_bytes(
                    phase1_initial_state(self.manifest.session_id)
                )
            ).hexdigest(),
            last_applied_raw_seq=0,
        )
        self.authority = facade.acquire_expert_journal_root(
            self.coordinator.issue_expert_state_root_account_lock_request()
        )
        self.root = store_module._ROOTS[self.authority]

    def tearDown(self) -> None:
        self.installed_environment_patch.stop()
        self.active_adapter_patch.stop()
        self.coordinator.close()
        self.temporary.cleanup()

    def replay_state(self, state_name: str) -> tuple[object, dict[str, object]]:
        token = object.__new__(ExpertReplayConstructionAuthorityV1)
        state: dict[str, object] = {
            "authority": token,
            "root": self.root,
            "generation": self.root.generation,
            "owner_pid": os.getpid(),
            "owner_thread": threading.current_thread(),
            "authorizer": object(),
            "coordinator": self.coordinator,
            "manifest": self.manifest,
            "deadline": self.manifest.required_retention_until_ns,
            "state": state_name,
            "sequence": 0,
            "outstanding": None,
            "evidence_index": 0,
            "group_index": 0,
            "closed": False,
        }
        store_module._REPLAYS[token] = state
        return token, state

    def bound_expert_manifest(self, phase1, decision):
        self.synthetic_environment_fixture_enabled = True
        base = _manifest_fixture()
        (
            current_environment,
            current_normalizers,
            current_structural_schemas,
            current_event_schemas,
        ) = store_module._installed_environment(self.root, phase1)
        phase1_digest = store_module.session_manifest_sha256(phase1)
        start_frame = session_start_frame(phase1)
        prefix = struct.unpack(">4sBBHQQII", start_frame[:32])
        metadata_end = 32 + prefix[6]
        payload_end = metadata_end + prefix[7]
        start = store_module.decode_record(
            start_frame[32:metadata_end],
            start_frame[metadata_end:payload_end],
        )
        provider_values = {
            item.name: getattr(base.provider_domain, item.name)
            for item in fields(base.provider_domain)
            if item.name != "provider_domain_binding_sha256"
        }
        provider_values.update(
            {
                "phase1_session_manifest_sha256": phase1_digest,
                "provider_id": phase1.provider_id,
                "product_tier": phase1.product_tier,
                "source_lineage_id": phase1.source_lineage_id,
                "provider_manifest_canonical_sha256": (
                    phase1.provider_manifest_canonical_sha256
                ),
                "provider_source_lineage_sha256": (
                    compute_expert_provider_source_lineage_sha256(
                        phase1.provider_id,
                        phase1.product_tier,
                        phase1.source_lineage_id,
                        phase1.provider_manifest_canonical_sha256,
                    )
                ),
            }
        )
        provider_domain = ExpertProviderDomainBindingV1(
            **provider_values,
            provider_domain_binding_sha256=(
                compute_expert_provider_domain_binding_sha256(
                    **provider_values
                )
            ),
        )
        retention_values = {
            item.name: getattr(base.retention, item.name)
            for item in fields(base.retention)
            if item.name != "retention_binding_sha256"
        }
        retention_values.update(
            {
                "session_id": phase1.session_id,
                "evidence_session_manifest_sha256": phase1_digest,
                "provider_request_binding_sha256": (
                    decision.provider_request_binding_sha256
                ),
                "permission_artifact_sha256": (
                    phase1.permission_artifact_sha256
                ),
                "qualification_artifact_sha256": (
                    phase1.qualification_artifact_sha256
                ),
                "qualification_trace_sha256": (
                    phase1.qualification_trace_sha256
                ),
                "retention_delete_by_ns": (
                    phase1.required_retention_until_ns
                ),
                "access_expires_at_ns": phase1.access_expires_at_ns,
                "analysis_expires_at_ns": phase1.analysis_expires_at_ns,
            }
        )
        retention = ExpertRetentionBindingV1(
            **retention_values,
            retention_binding_sha256=(
                compute_expert_retention_binding_sha256(
                    **retention_values
                )
            ),
        )
        values = {
            item.name: getattr(base, item.name)
            for item in fields(base)
            if item.name != "manifest_sha256"
        }
        values.update(
            {
                "session_id": phase1.session_id,
                "evidence_session_manifest_sha256": phase1_digest,
                "evidence_session_start_record_sha256": (
                    store_module.canonical_record_sha256(start)
                ),
                "provider_id": phase1.provider_id,
                "product_tier": phase1.product_tier,
                "source_lineage_id": phase1.source_lineage_id,
                "provider_manifest_file_sha256": (
                    phase1.provider_manifest_file_sha256
                ),
                "provider_manifest_canonical_sha256": (
                    phase1.provider_manifest_canonical_sha256
                ),
                "entitlement_id_sha256": phase1.entitlement_id_sha256,
                "provider_request_binding_sha256": (
                    decision.provider_request_binding_sha256
                ),
                "permission_artifact_sha256": (
                    phase1.permission_artifact_sha256
                ),
                "qualification_artifact_sha256": (
                    phase1.qualification_artifact_sha256
                ),
                "qualification_trace_sha256": (
                    phase1.qualification_trace_sha256
                ),
                "provider_domain": provider_domain,
                "environment": current_environment,
                "retention": retention,
                "normalizers": current_normalizers,
                "structural_schemas": current_structural_schemas,
                "event_schemas": current_event_schemas,
            }
        )
        return store_module.ExpertSessionManifestV1(
            **values,
            manifest_sha256=compute_expert_session_manifest_sha256(
                **values
            ),
        )

    def create_real_companion(self):
        expert_manifest = self.bound_expert_manifest(
            self.manifest,
            self.decision,
        )
        cursor = _genesis_cursor(expert_manifest)
        with mock.patch.object(
            store_module,
            "_creation_gate",
            return_value=1,
        ):
            writer = facade.create_expert_journal(
                self.authority,
                expert_manifest,
                cursor,
                persistence_authorizer=self.authorizer,
                coordinator=self.coordinator,
            )
        return writer, expert_manifest, cursor

    def test_prepare_semantic_proof_uses_last_sample_without_later_gate(
        self,
    ) -> None:
        for mismatch in (
            ExpertReplayMismatchV1.EVIDENCE_SESSION_MISMATCH,
            ExpertReplayMismatchV1.EVIDENCE_MANIFEST_MISMATCH,
        ):
            with self.subTest(mismatch=mismatch):
                state = {"_last_sampled_wall_ns": 17}
                sentinel = object()
                with (
                    mock.patch.object(
                        store_module,
                        "_prepare_replay_access_gate",
                        side_effect=AssertionError(
                            "semantic proof re-gated at deadline"
                        ),
                    ),
                    mock.patch.object(
                        store_module,
                        "_require_prepare_replay_full_integrity",
                        side_effect=AssertionError(
                            "semantic proof re-scanned at deadline"
                        ),
                    ),
                    mock.patch.object(
                        store_module,
                        "_close_prepare_with_denial",
                        return_value=sentinel,
                    ) as close_denial,
                ):
                    returned = (
                        store_module
                        ._close_prepare_semantic_denial_after_gate(
                            state,
                            mismatch,
                        )
                    )
                self.assertIs(returned, sentinel)
                self.assertEqual(
                    close_denial.call_args.kwargs["sampled"],
                    17,
                )

    def test_malformed_identity_set_fails_before_identity_indexing(
        self,
    ) -> None:
        token, state = self.replay_state("begin_ready")
        state.update(
            {
                "authority": token,
                "owner_pid": os.getpid(),
                "owner_thread": threading.current_thread(),
                "identity_set": (object(),),
                "evidence": object(),
                "expert_manifest": object(),
                "expected_environment": object(),
            }
        )
        with (
            mock.patch.object(
                store_module,
                "_replay_access_gate",
                return_value=1,
            ),
            mock.patch.object(
                store_module,
                "_raise_contextual_authorization_loss",
                side_effect=ExpertReplayAccessDenied(),
            ) as deny,
            mock.patch.object(
                store_module,
                "_guarded_phase1_evidence_file_identities",
                side_effect=AssertionError(
                    "malformed identity_set was indexed"
                ),
            ),
            self.assertRaises(ExpertReplayAccessDenied),
        ):
            store_module._replay_state(token, "begin_ready")
        deny.assert_called_once_with(state)

    def test_denial_transition_purges_begin_parent_and_finish_payloads(
        self,
    ) -> None:
        sensitive = {
            "accumulator",
            "current_parent",
            "current_group",
            "finish_material",
            "expected_environment",
            "evidence",
            "expert_manifest",
            "identity_set",
            "phase1_replay_result",
        }
        for phase in ("begin", "parent", "finish"):
            with self.subTest(phase=phase):
                state = {
                    "deadline": 100,
                    "state": phase,
                    "outstanding": object(),
                    **{name: object() for name in sensitive},
                }
                denial = object()
                with (
                    mock.patch.object(
                        store_module,
                        "_contextual_replay_denial",
                        return_value=denial,
                    ),
                    mock.patch.object(
                        store_module,
                        "_close_replay_owned_readers",
                        return_value=None,
                    ),
                ):
                    returned = store_module._transition_replay_denial(
                        state,
                        mismatch=(
                            ExpertReplayMismatchV1
                            .EVIDENCE_CONTEXT_MISMATCH
                        ),
                        sampled=1,
                    )
                self.assertIs(returned, denial)
                self.assertEqual(state["state"], "terminal_denied")
                self.assertIsNone(state["outstanding"])
                self.assertTrue(sensitive.isdisjoint(state))

    def test_prepare_authorizer_uses_bound_analysis_without_inventory_probe(
        self,
    ) -> None:
        before = self.provider_gate.require_analysis.call_count
        with mock.patch.object(
            RetentionCoordinator,
            "require_provider_operation",
            side_effect=AssertionError(
                "prepare consulted the normal evidence inventory"
            ),
        ) as inventory_probe:
            manifest = store_module._require_prepare_replay_authorizer(
                self.root,
                self.authorizer,
                self.coordinator,
            )
        inventory_probe.assert_not_called()
        self.assertIs(manifest, self.manifest)
        self.assertEqual(
            self.provider_gate.require_analysis.call_count,
            before + 1,
        )
        self.provider_gate.require_analysis.side_effect = RuntimeError(
            "analysis denied"
        )
        with self.assertRaises(ExpertLiveAuthorizationDenied):
            store_module._require_prepare_replay_authorizer(
                self.root,
                self.authorizer,
                self.coordinator,
            )

    def test_replay_gate_reauthorizes_after_second_root_validation(
        self,
    ) -> None:
        state = {
            "root": self.root,
            "generation": self.root.generation,
            "owner_pid": os.getpid(),
            "owner_thread": threading.current_thread(),
            "authorizer": self.authorizer,
            "coordinator": self.coordinator,
            "manifest": self.manifest,
            "deadline": self.manifest.required_retention_until_ns,
        }
        validations = 0

        def revoke_on_second_validation(root: object) -> None:
            nonlocal validations
            self.assertIs(root, self.root)
            validations += 1
            if validations == 2:
                self.provider_gate.require_analysis.side_effect = (
                    RuntimeError("revoked")
                )

        with (
            mock.patch.object(
                store_module,
                "_sample_contextual_replay_state",
                return_value=1,
            ),
            mock.patch.object(
                store_module,
                "_validate_replay_prepare_root_after_access_gate",
                side_effect=revoke_on_second_validation,
            ),
            mock.patch.object(
                store_module,
                "_raise_contextual_authorization_loss",
                side_effect=ExpertReplayAccessDenied(),
            ) as deny,
            mock.patch.object(
                store_module.os,
                "pread",
                side_effect=AssertionError("revoked gate read bytes"),
            ),
            self.assertRaises(ExpertReplayAccessDenied),
        ):
            store_module._replay_access_gate(state)
        self.assertEqual(validations, 2)
        deny.assert_called_once_with(state)

    def test_readerless_gate_reauthorizes_after_final_root_validation(
        self,
    ) -> None:
        validations = 0
        authorizations = 0

        def revoke_on_second_validation(root: object) -> None:
            nonlocal validations
            self.assertIs(root, self.root)
            validations += 1

        def authorize(root: object, authorizer: object, coordinator: object):
            nonlocal authorizations
            self.assertIs(root, self.root)
            self.assertIs(authorizer, self.authorizer)
            self.assertIs(coordinator, self.coordinator)
            authorizations += 1
            if validations >= 2:
                raise ExpertLiveAuthorizationDenied()
            return self.manifest

        with (
            mock.patch.object(
                store_module,
                "_sample_replay_prepare_wall_ns",
                return_value=1,
            ),
            mock.patch.object(
                store_module,
                "_validate_replay_prepare_root_after_access_gate",
                side_effect=revoke_on_second_validation,
            ),
            mock.patch.object(
                store_module,
                "_require_authorizer",
                side_effect=authorize,
            ),
            mock.patch.object(
                store_module.os,
                "stat",
                side_effect=AssertionError("readerless gate inspected files"),
            ),
        ):
            denial = store_module._readerless_replay_access_gate(
                self.root,
                manifest=self.manifest,
                persistence_authorizer=self.authorizer,
                coordinator=self.coordinator,
            )
        self.assertIs(type(denial), store_module.ExpertReplayDeniedV1)
        self.assertIs(
            denial.result.mismatch,
            ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
        )
        self.assertEqual(validations, 2)
        self.assertEqual(authorizations, 2)

    def test_readerless_issuance_observes_no_phase1_entries(self) -> None:
        before = set(store_module._REPLAYS)
        with (
            mock.patch.object(
                store_module,
                "_named_file_identity_observation",
                side_effect=AssertionError(
                    "readerless issuance observed Phase-1"
                ),
            ) as observe,
            mock.patch.object(
                JournalReader,
                "open",
                side_effect=AssertionError(
                    "readerless issuance opened Phase-1"
                ),
            ) as reader_open,
        ):
            authority = (
                store_module.issue_expert_replay_construction_authority(
                    self.authority,
                    persistence_authorizer=self.authorizer,
                    coordinator=self.coordinator,
                )
            )
        self.assertIs(
            type(authority),
            ExpertReplayConstructionAuthorityV1,
        )
        observe.assert_not_called()
        reader_open.assert_not_called()
        self.assertEqual(set(store_module._REPLAYS) - before, {authority})
        store_module.abort_expert_replay_construction(authority)

    def test_wrong_thread_abort_leaves_owner_authority_live(self) -> None:
        authority = (
            store_module.issue_expert_replay_construction_authority(
                self.authority,
                persistence_authorizer=self.authorizer,
                coordinator=self.coordinator,
            )
        )
        self.assertIs(
            type(authority),
            ExpertReplayConstructionAuthorityV1,
        )
        state = store_module._REPLAYS[authority]
        before = dict(state)
        errors: list[BaseException] = []

        def wrong_thread_abort() -> None:
            try:
                store_module.abort_expert_replay_construction(authority)
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=wrong_thread_abort)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIs(type(errors[0]), ValueError)
        self.assertEqual(state, before)
        store_module.abort_expert_replay_construction(authority)

    def test_wrong_thread_replay_call_is_nonmutating(self) -> None:
        authority, state = self.replay_state("new")
        state.update(
            {
                "authority": authority,
                "owner_pid": os.getpid(),
                "owner_thread": threading.current_thread(),
            }
        )
        before = dict(state)
        errors: list[BaseException] = []

        def wrong_thread_call() -> None:
            try:
                store_module._replay_authority_state(authority, "new")
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=wrong_thread_call)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIs(type(errors[0]), ValueError)
        self.assertEqual(state, before)
        store_module.abort_expert_replay_construction(authority)

    def test_postfork_replay_call_is_nonmutating(self) -> None:
        authority, state = self.replay_state("new")
        state.update(
            {
                "authority": authority,
                "owner_pid": os.getpid(),
                "owner_thread": threading.current_thread(),
            }
        )
        before = dict(state)
        with (
            mock.patch.object(store_module.os, "getpid", return_value=-1),
            self.assertRaisesRegex(
                ValueError,
                "^expert_replay_authority_invalid$",
            ),
        ):
            store_module._replay_authority_state(authority, "new")
        self.assertEqual(state, before)

    def test_denial_close_uncertainty_purges_sensitive_state(self) -> None:
        sensitive = {
            "accumulator",
            "current_parent",
            "current_group",
            "finish_material",
            "expected_environment",
            "evidence",
            "expert_manifest",
            "identity_set",
            "phase1_replay_result",
        }
        state = {
            "deadline": 100,
            "state": "pair_empty",
            "closed": False,
            "outstanding": object(),
            **{name: object() for name in sensitive},
        }
        with (
            mock.patch.object(
                store_module,
                "_contextual_replay_denial",
                return_value=object(),
            ),
            mock.patch.object(
                store_module,
                "_close_replay_owned_readers",
                side_effect=store_module._ReplayCloseUncertain(),
            ),
            self.assertRaisesRegex(
                OSError,
                "^expert_replay_close_uncertain$",
            ),
        ):
            store_module._transition_replay_denial(
                state,
                mismatch=ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
                sampled=1,
            )
        self.assertTrue(sensitive.isdisjoint(state))
        self.assertNotIn("denial", state)
        self.assertTrue(state["closed"])
        self.assertEqual(state["state"], "aborted_closed")

    def test_prepare_denial_close_uncertainty_exposes_no_cached_denial(
        self,
    ) -> None:
        state = {
            "root": self.root,
            "manifest": self.manifest,
            "deadline": 100,
            "state": "new",
            "closed": False,
            "evidence": object(),
            "expert_manifest": object(),
            "identity_set": object(),
        }
        with (
            mock.patch.object(
                store_module,
                "_contextual_replay_denial",
                return_value=object(),
            ),
            mock.patch.object(
                store_module,
                "_close_replay_owned_readers",
                side_effect=store_module._ReplayCloseUncertain(),
            ),
            mock.patch.object(store_module, "_purge_names"),
            self.assertRaisesRegex(
                OSError,
                "^expert_replay_close_uncertain$",
            ),
        ):
            store_module._close_prepare_with_denial(
                state,
                mismatch=ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
                sampled=1,
            )
        self.assertNotIn("denial", state)
        self.assertNotIn("evidence", state)
        self.assertNotIn("expert_manifest", state)
        self.assertNotIn("identity_set", state)
        self.assertTrue(state["closed"])
        self.assertEqual(state["state"], "aborted_closed")

    def test_transition_purge_uncertainty_exposes_no_denial(self) -> None:
        sensitive = {
            "accumulator",
            "current_parent",
            "current_group",
            "finish_material",
            "expected_environment",
            "evidence",
            "expert_manifest",
            "identity_set",
            "phase1_replay_result",
        }
        state = {
            "root": self.root,
            "manifest": self.manifest,
            "deadline": 100,
            "state": "pair_empty",
            "closed": False,
            "outstanding": object(),
            **{name: object() for name in sensitive},
        }
        with (
            mock.patch.object(
                store_module,
                "_contextual_replay_denial",
                return_value=object(),
            ),
            mock.patch.object(
                store_module,
                "_close_replay_owned_readers",
            ),
            mock.patch.object(
                store_module,
                "_purge_names",
                side_effect=OSError(
                    "expert_session_invalidation_close_uncertain"
                ),
            ),
            self.assertRaisesRegex(
                OSError,
                "^expert_replay_close_uncertain$",
            ),
        ):
            store_module._transition_replay_denial(
                state,
                mismatch=(
                    ExpertReplayMismatchV1
                    .RETENTION_AUTHORIZATION_MISMATCH
                ),
                sampled=1,
            )
        self.assertTrue(sensitive.isdisjoint(state))
        self.assertNotIn("denial", state)
        self.assertTrue(state["closed"])
        self.assertEqual(state["state"], "aborted_closed")

    def test_prepare_purge_uncertainty_exposes_no_denial(self) -> None:
        sensitive = {
            "evidence",
            "expert_manifest",
            "identity_set",
            "prepare_expected_environment",
        }
        state = {
            "root": self.root,
            "manifest": self.manifest,
            "deadline": 100,
            "state": "new",
            "closed": False,
            **{name: object() for name in sensitive},
        }
        with (
            mock.patch.object(
                store_module,
                "_contextual_replay_denial",
                return_value=object(),
            ),
            mock.patch.object(
                store_module,
                "_close_replay_owned_readers",
            ),
            mock.patch.object(
                store_module,
                "_purge_names",
                side_effect=OSError(
                    "expert_session_invalidation_close_uncertain"
                ),
            ),
            self.assertRaisesRegex(
                OSError,
                "^expert_replay_close_uncertain$",
            ),
        ):
            store_module._close_prepare_with_denial(
                state,
                mismatch=ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
                sampled=1,
            )
        self.assertTrue(sensitive.isdisjoint(state))
        self.assertNotIn("denial", state)
        self.assertTrue(state["closed"])
        self.assertEqual(state["state"], "aborted_closed")

    def test_purge_close_uncertainty_never_claims_clean_invalidation(
        self,
    ) -> None:
        if self._run_fatal_root_test_in_subprocess():
            return
        writer, _, _ = self.create_real_companion()
        writer_state = store_module._WRITERS[writer]
        failed_descriptor = writer_state.journal_fd
        original_close = store_module.os.close
        attempts = 0

        def uncertain_close(descriptor: int) -> None:
            nonlocal attempts
            if descriptor == failed_descriptor:
                attempts += 1
                original_close(descriptor)
                raise OSError(errno.EIO, "forced_invalidation_close")
            original_close(descriptor)

        with (
            mock.patch.object(
                store_module.os,
                "close",
                side_effect=uncertain_close,
            ),
            self.assertRaisesRegex(
                OSError,
                "^expert_session_invalidation_close_uncertain$",
            ),
        ):
            store_module._purge_names(
                self.root,
                self.manifest.session_id,
            )
        self.assertEqual(attempts, 1)
        self.assertEqual(writer_state.journal_fd, -1)
        self.assertEqual(writer_state.reserve_fd, -1)
        self.assertEqual(writer_state.state, "poisoned")
        self.assertFalse(self.root.active)

    def test_prepare_temporary_close_uncertainty_fails_closed(
        self,
    ) -> None:
        if self._run_fatal_root_test_in_subprocess():
            return
        basename = "prepare-close-probe"
        path = (
            self.root_path
            / "state"
            / "expert-v1"
            / "markers"
            / basename
        )
        path.write_bytes(b"probe")
        os.chmod(path, 0o600)
        opened_descriptor = -1
        original_open = store_module.os.open
        original_close = store_module.os.close

        def record_open(name: object, flags: int, **kwargs: object) -> int:
            nonlocal opened_descriptor
            descriptor = original_open(name, flags, **kwargs)
            if name == basename:
                opened_descriptor = descriptor
            return descriptor

        def uncertain_close(descriptor: int) -> None:
            if descriptor == opened_descriptor:
                original_close(descriptor)
                raise OSError(errno.EIO, "forced_prepare_close")
            original_close(descriptor)

        with (
            mock.patch.object(
                store_module,
                "_require_prepare_replay_full_integrity",
                return_value=1,
            ),
            mock.patch.object(
                store_module.os,
                "open",
                side_effect=record_open,
            ),
            mock.patch.object(
                store_module.os,
                "close",
                side_effect=uncertain_close,
            ),
            self.assertRaisesRegex(
                OSError,
                "^expert_replay_close_uncertain$",
            ),
        ):
            store_module._read_prepare_replay_named_content(
                {"root": self.root},
                self.root.markers_fd,
                basename,
                1024,
            )
        self.assertGreaterEqual(opened_descriptor, 0)
        self.assertFalse(self.root.active)

    def test_identity_errno_is_not_fabricated_as_entry_missing(
        self,
    ) -> None:
        with mock.patch.object(
            store_module.os,
            "stat",
            side_effect=FileNotFoundError(errno.ENOENT, "missing"),
        ):
            self.assertIsNone(
                store_module._named_file_identity_observation(
                    self.root.markers_fd,
                    "missing",
                )
            )
        with (
            mock.patch.object(
                store_module.os,
                "stat",
                side_effect=OSError(errno.EIO, "forced_eio"),
            ),
            self.assertRaises(OSError) as raised,
        ):
            store_module._named_file_identity_observation(
                self.root.markers_fd,
                "unreadable",
            )
        self.assertEqual(raised.exception.errno, errno.EIO)

        proof_state = {
            "root": self.root,
            "manifest": self.manifest,
            "state": "new",
        }
        with (
            mock.patch.object(
                store_module,
                "_require_prepare_replay_access",
                return_value=1,
            ),
            mock.patch.object(
                store_module.os,
                "stat",
                side_effect=OSError(errno.EIO, "forced_proof_eio"),
            ),
            self.assertRaises(OSError) as proof_error,
        ):
            store_module._identity_file_proof(
                proof_state,
                ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
            )
        self.assertEqual(proof_error.exception.errno, errno.EIO)

    def test_replay_authority_deadline_equality_returns_closed_readerless_denial(
        self,
    ) -> None:
        self.clock.now_ns = self.manifest.required_retention_until_ns
        before_replays = set(store_module._REPLAYS)
        before_analysis_calls = self.provider_gate.require_analysis.call_count
        with (
            mock.patch.object(
                store_module,
                "_read_named_content",
                side_effect=AssertionError(
                    "deadline issuance read a named entry"
                ),
            ) as named_read,
            mock.patch.object(
                JournalReader,
                "open",
                side_effect=AssertionError(
                    "deadline issuance opened Phase-1"
                ),
            ) as phase1_open,
        ):
            denial = (
                facade.issue_expert_replay_construction_authority(
                    self.authority,
                    persistence_authorizer=self.authorizer,
                    coordinator=self.coordinator,
                )
            )
        self.assertIs(type(denial), store_module.ExpertReplayDeniedV1)
        self.assertIs(
            denial.result.mismatch,
            ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
        )
        self.assertIsNone(denial.result.state)
        self.assertEqual(
            (
                denial.result.evidence_raw_count,
                denial.result.evidence_derived_count,
                denial.result.expert_group_count,
                denial.result.expert_record_count,
            ),
            (0, 0, 0, 0),
        )
        self.assertEqual(denial.proof.file_proofs, ())
        self.assertIsNone(denial.proof.companion_scan)
        self.assertIsNone(
            denial.proof.phase1_replay_summary_sha256
        )
        self.assertEqual(
            denial.proof.common_deadline_ns,
            self.manifest.required_retention_until_ns,
        )
        self.assertEqual(
            denial.proof.final_sampled_wall_ns,
            self.manifest.required_retention_until_ns,
        )
        self.assertEqual(set(store_module._REPLAYS), before_replays)
        self.assertEqual(
            self.provider_gate.require_analysis.call_count,
            before_analysis_calls,
        )
        named_read.assert_not_called()
        phase1_open.assert_not_called()

    def test_replay_prepare_treats_pre_first_observation_replacement_as_baseline(
        self,
    ) -> None:
        writer, _, _ = self.create_real_companion()
        replay = facade.issue_expert_replay_construction_authority(
            self.authority,
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        marker_path = (
            self.root_path
            / "state"
            / "retention-markers"
            / f"{self.manifest.session_id}.marker.json"
        )
        original = marker_path.stat()
        replacement = marker_path.with_name(
            marker_path.name + ".replacement"
        )
        replacement.write_bytes(marker_path.read_bytes())
        os.chmod(replacement, 0o600)
        os.replace(replacement, marker_path)
        observed = marker_path.stat()
        self.assertNotEqual(
            (observed.st_dev, observed.st_ino),
            (original.st_dev, original.st_ino),
        )
        ready = facade.prepare_expert_replay_begin(replay)
        self.assertIs(
            type(ready),
            store_module.ExpertReplayBeginReadyV1,
        )
        replay_state = store_module._REPLAYS[replay]
        self.assertEqual(
            replay_state["phase1_bootstrap_identities"][0],
            store_module._stat_identity_observation(observed),
        )
        self.assertEqual(replay_state["state"], "begin_ready")
        facade.abort_expert_replay_construction(replay)
        facade.abort_expert_writer(writer)

    def _assert_missing_phase1_bootstrap_denial(
        self,
        *,
        path: Path,
        role: ExpertReplayDiagnosticRoleV1,
    ) -> None:
        self.create_real_companion()
        replay = facade.issue_expert_replay_construction_authority(
            self.authority,
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        path.unlink()
        with (
            mock.patch.object(
                JournalReader,
                "open",
                side_effect=AssertionError(
                    "missing Phase-1 entry opened a reader"
                ),
            ) as phase1_open,
            mock.patch.object(
                store_module,
                "_read_named_content",
                side_effect=AssertionError(
                    "missing Phase-1 entry read companion bytes"
                ),
            ) as companion_read,
            mock.patch.object(
                store_module,
                "_pread_exact",
                side_effect=AssertionError(
                    "missing Phase-1 entry read diagnostic bytes"
                ),
            ) as diagnostic_read,
        ):
            denial = facade.prepare_expert_replay_begin(replay)
            with self.assertRaises(ValueError):
                facade.prepare_expert_replay_begin(replay)
        phase1_open.assert_not_called()
        companion_read.assert_not_called()
        diagnostic_read.assert_not_called()
        self.assertIs(type(denial), store_module.ExpertReplayDeniedV1)
        self.assertIs(
            denial.result.mismatch,
            ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
        )
        self.assertEqual(len(denial.proof.file_proofs), 1)
        proof = denial.proof.file_proofs[0]
        self.assertIs(proof.role, role)
        self.assertFalse(proof.entry_present)
        self.assertIs(
            proof.issue,
            store_module.ExpertReplayDiagnosticIssueV1.ENTRY_MISSING,
        )
        self.assertEqual(
            (
                proof.observed_size,
                proof.observed_prefix_length,
                proof.observed_prefix_sha256,
            ),
            (0, 0, store_module.sha256(b"").hexdigest()),
        )
        replay_state = store_module._REPLAYS[replay]
        self.assertTrue(replay_state["closed"])
        self.assertEqual(replay_state["state"], "denied_closed")
        self.assertTrue(self.root.active)
        self.assertNotIn("phase1_reader", replay_state)
        self.assertNotIn("companion_reader", replay_state)

    def test_replay_prepare_returns_closed_marker_missing_proof_before_reads(
        self,
    ) -> None:
        if self._run_fatal_root_test_in_subprocess():
            return
        self._assert_missing_phase1_bootstrap_denial(
            path=(
                self.root_path
                / "state"
                / "retention-markers"
                / f"{self.manifest.session_id}.marker.json"
            ),
            role=ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
        )

    def test_replay_prepare_returns_closed_wal_missing_proof_before_reads(
        self,
    ) -> None:
        self._assert_missing_phase1_bootstrap_denial(
            path=(
                self.root_path
                / "state"
                / "sessions"
                / f"{self.manifest.session_id}.wal"
            ),
            role=ExpertReplayDiagnosticRoleV1.PHASE1_WAL,
        )

    def _assert_prepare_access_gate_precedes_missing_phase1_entry(
        self,
        *,
        role: ExpertReplayDiagnosticRoleV1,
        expected_mismatch: ExpertReplayMismatchV1,
    ) -> None:
        child_case = os.environ.get(self._PREPARE_COLLISION_CHILD)
        for collision_timing in ("before_prepare", "after_entry_gate"):
            marker = "::".join(
                (
                    self.id(),
                    role.value,
                    expected_mismatch.value,
                    collision_timing,
                )
            )
            if child_case is None:
                self._run_current_test_case_in_subprocess(
                    variable=self._PREPARE_COLLISION_CHILD,
                    marker=marker,
                )
                continue
            if child_case != marker:
                continue
            with self.subTest(collision_timing=collision_timing):
                self._run_prepare_access_gate_collision(
                    role=role,
                    expected_mismatch=expected_mismatch,
                    collision_timing=collision_timing,
                )

    def _run_prepare_access_gate_collision(
        self,
        *,
        role: ExpertReplayDiagnosticRoleV1,
        expected_mismatch: ExpertReplayMismatchV1,
        collision_timing: str,
    ) -> None:
        self.create_real_companion()
        replay = facade.issue_expert_replay_construction_authority(
            self.authority,
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        replay_state = store_module._REPLAYS[replay]
        path = {
            ExpertReplayDiagnosticRoleV1.PHASE1_MARKER: (
                self.root_path
                / "state"
                / "retention-markers"
                / f"{self.manifest.session_id}.marker.json"
            ),
            ExpertReplayDiagnosticRoleV1.PHASE1_WAL: (
                self.root_path
                / "state"
                / "sessions"
                / f"{self.manifest.session_id}.wal"
            ),
        }[role]
        barrier_fired = False

        def inject_access_loss() -> None:
            if (
                expected_mismatch
                is ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
            ):
                self.clock.now_ns = (
                    self.manifest.required_retention_until_ns
                )
            else:
                self.provider_gate.require_analysis.side_effect = (
                    RuntimeError(
                        "analysis denied before evidence observation"
                    )
                )

        if collision_timing == "before_prepare":
            path.unlink()
            inject_access_loss()

        real_authorize = (
            store_module._require_prepare_replay_authorizer
        )
        real_observe = store_module._named_file_identity_observation

        def authorize_with_after_gate_barrier(
            root,
            persistence_authorizer,
            coordinator,
        ):
            nonlocal barrier_fired
            manifest = real_authorize(
                root,
                persistence_authorizer,
                coordinator,
            )
            if (
                collision_timing == "after_entry_gate"
                and role
                is ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
                and not barrier_fired
            ):
                barrier_fired = True
                path.unlink()
                inject_access_loss()
            return manifest

        def observe_with_between_role_barrier(
            directory_fd: int,
            basename: str,
        ):
            nonlocal barrier_fired
            observation = real_observe(directory_fd, basename)
            if (
                collision_timing == "after_entry_gate"
                and role is ExpertReplayDiagnosticRoleV1.PHASE1_WAL
                and not barrier_fired
                and directory_fd == self.root.evidence_markers_fd
                and basename
                == f"{self.manifest.session_id}.marker.json"
            ):
                barrier_fired = True
                path.unlink()
                inject_access_loss()
            return observation

        before_analysis_calls = (
            self.provider_gate.require_analysis.call_count
        )
        with (
            mock.patch.object(
                self.coordinator,
                "_clock_ns",
                side_effect=self.clock,
            ) as clock_sample,
            mock.patch.object(
                store_module,
                "_named_file_identity_observation",
                side_effect=observe_with_between_role_barrier,
            ) as identity_observation,
            mock.patch.object(
                store_module,
                "_require_prepare_replay_authorizer",
                side_effect=authorize_with_after_gate_barrier,
            ),
            mock.patch.object(
                store_module,
                "_identity_file_proof",
                wraps=store_module._identity_file_proof,
            ) as file_proof,
            mock.patch.object(
                store_module,
                "_purge_names",
                wraps=store_module._purge_names,
            ) as purge,
            mock.patch.object(
                JournalReader,
                "open",
                side_effect=AssertionError(
                    "access denial opened a Phase-1 reader"
                ),
            ) as phase1_open,
            mock.patch.object(
                store_module,
                "_read_named_content",
                side_effect=AssertionError(
                    "access denial read companion bytes"
                ),
            ) as companion_read,
        ):
            denial = facade.prepare_expert_replay_begin(replay)
            with self.assertRaises(ValueError):
                facade.prepare_expert_replay_begin(replay)
        self.assertIs(type(denial), store_module.ExpertReplayDeniedV1)
        self.assertIs(denial.result.mismatch, expected_mismatch)
        self.assertIsNone(denial.result.state)
        self.assertEqual(
            (
                denial.result.evidence_raw_count,
                denial.result.evidence_derived_count,
                denial.result.expert_group_count,
                denial.result.expert_record_count,
            ),
            (0, 0, 0, 0),
        )
        self.assertEqual(denial.proof.file_proofs, ())
        self.assertIsNone(denial.proof.companion_scan)
        self.assertIsNone(
            denial.proof.phase1_replay_summary_sha256
        )
        self.assertEqual(
            denial.proof.common_deadline_ns,
            self.manifest.required_retention_until_ns,
        )
        self.assertEqual(
            denial.proof.final_sampled_wall_ns,
            self.clock.now_ns,
        )
        if collision_timing == "before_prepare":
            if expected_mismatch is (
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
            ):
                expected_clock_calls = (
                    1
                    if role
                    is ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
                    else 2
                )
                expected_analysis_calls = 0
            else:
                expected_clock_calls = (
                    2
                    if role
                    is ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
                    else 3
                )
                expected_analysis_calls = 1
            expected_identity_observations = 0
        else:
            self.assertTrue(barrier_fired)
            target_index = (
                0
                if role
                is ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
                else 1
            )
            if (
                role is ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
                and expected_mismatch is (
                    ExpertReplayMismatchV1
                    .RETENTION_AUTHORIZATION_MISMATCH
                )
            ):
                expected_clock_calls = 3
            elif role is ExpertReplayDiagnosticRoleV1.PHASE1_MARKER:
                expected_clock_calls = 2
            elif expected_mismatch is (
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
            ):
                expected_clock_calls = 8
            else:
                expected_clock_calls = 9
            if (
                role is ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
                and expected_mismatch is (
                    ExpertReplayMismatchV1
                    .RETENTION_AUTHORIZATION_MISMATCH
                )
            ):
                expected_analysis_calls = 2
            elif role is ExpertReplayDiagnosticRoleV1.PHASE1_MARKER:
                expected_analysis_calls = 1
            elif expected_mismatch is (
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
            ):
                expected_analysis_calls = 4
            else:
                expected_analysis_calls = 5
            expected_identity_observations = target_index
        self.assertEqual(clock_sample.call_count, expected_clock_calls)
        self.assertEqual(
            self.provider_gate.require_analysis.call_count,
            before_analysis_calls + expected_analysis_calls,
        )
        self.assertEqual(
            identity_observation.call_count,
            expected_identity_observations,
        )
        self.assertFalse(
            any(
                call.args[1] == path.name
                for call in identity_observation.call_args_list
            )
        )
        file_proof.assert_not_called()
        phase1_open.assert_not_called()
        companion_read.assert_not_called()
        purge.assert_called_once_with(
            self.root,
            self.manifest.session_id,
            preserve_replay=replay_state,
        )
        self.assertTrue(replay_state["closed"])
        self.assertEqual(replay_state["state"], "denied_closed")
        self.assertNotIn("phase1_reader", replay_state)
        self.assertNotIn("companion_reader", replay_state)

    def test_prepare_deadline_equality_precedes_missing_phase1_marker_observation(
        self,
    ) -> None:
        self._assert_prepare_access_gate_precedes_missing_phase1_entry(
            role=ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
            expected_mismatch=(
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
            ),
        )

    def test_prepare_deadline_equality_precedes_missing_phase1_wal_observation(
        self,
    ) -> None:
        self._assert_prepare_access_gate_precedes_missing_phase1_entry(
            role=ExpertReplayDiagnosticRoleV1.PHASE1_WAL,
            expected_mismatch=(
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
            ),
        )

    def test_prepare_authorization_loss_precedes_missing_phase1_marker_observation(
        self,
    ) -> None:
        self._assert_prepare_access_gate_precedes_missing_phase1_entry(
            role=ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
            expected_mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
        )

    def test_prepare_authorization_loss_precedes_missing_phase1_wal_observation(
        self,
    ) -> None:
        self._assert_prepare_access_gate_precedes_missing_phase1_entry(
            role=ExpertReplayDiagnosticRoleV1.PHASE1_WAL,
            expected_mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
        )

    def test_replay_prepare_classifies_replacement_during_proof_read(
        self,
    ) -> None:
        marker_path = (
            self.root_path
            / "state"
            / "retention-markers"
            / f"{self.manifest.session_id}.marker.json"
        )
        marker_bytes = marker_path.read_bytes()
        first_replacement = marker_path.with_name(
            marker_path.name + ".first"
        )
        first_replacement.write_bytes(marker_bytes)
        os.chmod(first_replacement, 0o600)
        os.replace(first_replacement, marker_path)
        opened_identity = marker_path.stat()
        real_pread = store_module.os.pread
        proof_reads = 0

        def replace_during_prefix(
            descriptor: int,
            length: int,
            offset: int,
        ) -> bytes:
            nonlocal proof_reads
            proof_reads += 1
            prefix = real_pread(descriptor, length, offset)
            second_replacement = marker_path.with_name(
                marker_path.name + ".second"
            )
            second_replacement.write_bytes(marker_bytes)
            os.chmod(second_replacement, 0o600)
            os.replace(second_replacement, marker_path)
            return prefix

        with (
            mock.patch.object(
                store_module,
                "_replay_access_gate",
                return_value=1,
            ),
            mock.patch.object(
                store_module.os,
                "pread",
                side_effect=replace_during_prefix,
            ),
        ):
            proof = store_module._identity_file_proof(
                {
                    "root": self.root,
                    "manifest": self.manifest,
                    "deadline": (
                        self.manifest.required_retention_until_ns
                    ),
                },
                ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
            )
        self.assertEqual(proof_reads, 1)
        self.assertIs(
            proof.role,
            ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
        )
        self.assertIs(
            proof.issue,
            store_module.ExpertReplayDiagnosticIssueV1.ENTRY_REPLACED,
        )
        self.assertEqual(
            (proof.device, proof.inode),
            (opened_identity.st_dev, opened_identity.st_ino),
        )
        self.assertEqual(
            (
                proof.observed_prefix_length,
                proof.observed_prefix_sha256,
            ),
            (
                min(len(marker_bytes), 4096),
                store_module.sha256(marker_bytes[:4096]).hexdigest(),
            ),
        )

    def test_replay_prepare_returns_typed_closed_denials_before_or_for_companion_scan(
        self,
    ) -> None:
        self.synthetic_environment_fixture_enabled = True
        replay = facade.issue_expert_replay_construction_authority(
            self.authority,
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        denied = facade.prepare_expert_replay_begin(replay)
        self.assertIsInstance(denied, store_module.ExpertReplayDeniedV1)
        self.assertIs(
            denied.result.mismatch,
            ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
        )
        self.assertEqual(
            store_module._REPLAYS[replay]["state"],
            "denied_closed",
        )
        self.assertEqual(len(denied.proof.file_proofs), 1)
        missing_proof = denied.proof.file_proofs[0]
        self.assertIs(
            missing_proof.role,
            ExpertReplayDiagnosticRoleV1.EXPERT_MARKER,
        )
        self.assertFalse(missing_proof.entry_present)
        self.assertIs(
            missing_proof.issue,
            store_module.ExpertReplayDiagnosticIssueV1.ENTRY_MISSING,
        )

        self.tearDown()
        self.setUp()
        writer, _, _ = self.create_real_companion()
        state = store_module._WRITERS[writer]
        os.pwrite(state.journal_fd, b"X", 0)
        os.fsync(state.journal_fd)
        details = os.fstat(state.journal_fd)
        prefix_length = min(details.st_size, 4096)
        journal_path = (
            self.root_path
            / "state"
            / "expert-v1"
            / "sessions"
            / store_module._journal_basename(self.manifest.session_id)
        )
        prefix = journal_path.read_bytes()[:prefix_length]
        replay = facade.issue_expert_replay_construction_authority(
            self.authority,
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        denied = facade.prepare_expert_replay_begin(replay)
        self.assertIsInstance(denied, store_module.ExpertReplayDeniedV1)
        self.assertIs(
            denied.result.mismatch,
            ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
        )
        self.assertIsNone(denied.result.state)
        self.assertEqual(len(denied.proof.file_proofs), 1)
        proof = denied.proof.file_proofs[0]
        self.assertIs(
            proof.role,
            ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL,
        )
        self.assertTrue(proof.entry_present)
        self.assertEqual(
            (
                proof.device,
                proof.inode,
                proof.uid,
                proof.mode,
                proof.link_count,
                proof.mtime_ns,
                proof.ctime_ns,
                proof.observed_size,
                proof.observed_prefix_length,
                proof.observed_prefix_sha256,
                proof.issue,
            ),
            (
                details.st_dev,
                details.st_ino,
                details.st_uid,
                stat.S_IMODE(details.st_mode),
                details.st_nlink,
                details.st_mtime_ns,
                details.st_ctime_ns,
                details.st_size,
                len(prefix),
                store_module.sha256(prefix).hexdigest(),
                store_module.ExpertReplayDiagnosticIssueV1.HEADER_INVALID,
            ),
        )
        replay_state = store_module._REPLAYS[replay]
        self.assertTrue(replay_state["closed"])
        self.assertEqual(replay_state["state"], "denied_closed")
        self.assertNotIn("phase1_reader", replay_state)
        self.assertNotIn("companion_reader", replay_state)

        self.tearDown()
        self.setUp()
        exact = store_module.replay_exact(
            expected_session_manifest_sha256=(
                store_module.session_manifest_sha256(self.manifest)
            ),
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        nonexact = replace(
            exact,
            exact_replay=False,
            replay_mismatch=ReplayMismatch.RAW_REDUCTION,
        )
        replay = facade.issue_expert_replay_construction_authority(
            self.authority,
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        with mock.patch.object(
            store_module,
            "replay_exact",
            return_value=nonexact,
        ), mock.patch.object(
            store_module,
            "_read_named_content",
            side_effect=AssertionError("companion read before precedence"),
        ) as companion_read:
            denied = facade.prepare_expert_replay_begin(replay)
        companion_read.assert_not_called()
        self.assertIsInstance(denied, store_module.ExpertReplayDeniedV1)
        self.assertIs(
            denied.result.mismatch,
            ExpertReplayMismatchV1.EVIDENCE_REPLAY_NOT_EXACT,
        )

        self.tearDown()
        self.setUp()
        wal_path = (
            self.root_path
            / "state"
            / "sessions"
            / f"{self.manifest.session_id}.wal"
        )
        descriptor = os.open(wal_path, os.O_WRONLY)
        try:
            os.pwrite(descriptor, b"X", 0)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        replay = facade.issue_expert_replay_construction_authority(
            self.authority,
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        with mock.patch.object(
            store_module,
            "_read_named_content",
            side_effect=AssertionError(
                "companion read before Phase-1 context denial"
            ),
        ) as companion_read:
            denied = facade.prepare_expert_replay_begin(replay)
        companion_read.assert_not_called()
        self.assertIsInstance(denied, store_module.ExpertReplayDeniedV1)
        self.assertIs(
            denied.result.mismatch,
            ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
        )

    def test_prepare_companion_marker_enoent_is_item11_while_other_os_errors_remain_operational(
        self,
    ) -> None:
        self.synthetic_environment_fixture_enabled = True
        replay = facade.issue_expert_replay_construction_authority(
            self.authority,
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        denied = facade.prepare_expert_replay_begin(replay)
        self.assertIsInstance(denied, store_module.ExpertReplayDeniedV1)
        self.assertIs(
            denied.result.mismatch,
            ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
        )
        self.assertEqual(len(denied.proof.file_proofs), 1)
        proof = denied.proof.file_proofs[0]
        self.assertIs(
            proof.role,
            ExpertReplayDiagnosticRoleV1.EXPERT_MARKER,
        )
        self.assertIs(
            proof.issue,
            store_module.ExpertReplayDiagnosticIssueV1.ENTRY_MISSING,
        )

        for error_number in (errno.EIO, errno.ENOTDIR):
            with self.subTest(error_number=error_number):
                self.tearDown()
                self.setUp()
                self.synthetic_environment_fixture_enabled = True
                replay = facade.issue_expert_replay_construction_authority(
                    self.authority,
                    persistence_authorizer=self.authorizer,
                    coordinator=self.coordinator,
                )
                replay_state = store_module._REPLAYS[replay]
                with (
                    mock.patch.object(
                        store_module,
                        "_read_prepare_replay_named_content",
                        side_effect=OSError(
                            error_number,
                            "forced_companion_marker_read_error",
                        ),
                    ),
                    self.assertRaisesRegex(
                        OSError,
                        "^expert_replay_read_failed$",
                    ),
                ):
                    facade.prepare_expert_replay_begin(replay)
                self.assertEqual(
                    replay_state["state"],
                    "aborted_closed",
                )
                self.assertNotIn("denial", replay_state)

    def test_prepare_companion_journal_enoent_is_item11_while_other_os_errors_remain_operational(
        self,
    ) -> None:
        self.create_real_companion()
        replay = facade.issue_expert_replay_construction_authority(
            self.authority,
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        journal_basename = store_module._journal_basename(
            self.manifest.session_id
        )
        journal_path = (
            self.root_path
            / "state"
            / "expert-v1"
            / "sessions"
            / journal_basename
        )
        journal_path.unlink()
        denied = facade.prepare_expert_replay_begin(replay)
        self.assertIs(type(denied), store_module.ExpertReplayDeniedV1)
        self.assertIs(
            denied.result.mismatch,
            ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
        )
        self.assertEqual(len(denied.proof.file_proofs), 1)
        proof = denied.proof.file_proofs[0]
        self.assertIs(
            proof.role,
            ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL,
        )
        self.assertIs(
            proof.issue,
            store_module.ExpertReplayDiagnosticIssueV1.ENTRY_MISSING,
        )

        for error_number in (errno.EIO, errno.ENOTDIR):
            with self.subTest(error_number=error_number):
                self.tearDown()
                self.setUp()
                self.create_real_companion()
                replay = facade.issue_expert_replay_construction_authority(
                    self.authority,
                    persistence_authorizer=self.authorizer,
                    coordinator=self.coordinator,
                )
                replay_state = store_module._REPLAYS[replay]
                journal_basename = store_module._journal_basename(
                    self.manifest.session_id
                )
                original_open = store_module.os.open

                def fail_journal_open(
                    path: object,
                    flags: int,
                    *args: object,
                    **keywords: object,
                ) -> int:
                    if path == journal_basename:
                        raise OSError(
                            error_number,
                            "forced_companion_journal_open_error",
                        )
                    return original_open(
                        path,
                        flags,
                        *args,
                        **keywords,
                    )

                with (
                    mock.patch.object(
                        store_module.os,
                        "open",
                        side_effect=fail_journal_open,
                    ),
                    self.assertRaisesRegex(
                        OSError,
                        "^expert_replay_read_failed$",
                    ),
                ):
                    facade.prepare_expert_replay_begin(replay)
                self.assertEqual(
                    replay_state["state"],
                    "aborted_closed",
                )
                self.assertNotIn("denial", replay_state)

    def test_mid_replay_denial_closes_both_readers_and_preserves_lawful_context(
        self,
    ) -> None:
        writer, _, _ = self.create_real_companion()
        replay = facade.issue_expert_replay_construction_authority(
            self.authority,
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        facade.prepare_expert_replay_begin(replay)
        state = store_module._REPLAYS[replay]
        companion = state["companion_reader"]
        companion_fd = store_module._READERS[companion].fd
        phase1_reader = state["phase1_reader"]
        expected_raw_count = state["evidence"].replay_result.raw_count
        expected_derived_count = (
            state["evidence"].replay_result.derived_count
        )
        self.clock.now_ns = self.manifest.required_retention_until_ns
        with self.assertRaises(ExpertReplayAccessDenied):
            facade.issue_begin_replay_authorization(replay)
        self.assertNotIn(companion, store_module._READERS)
        with self.assertRaises(OSError):
            os.fstat(companion_fd)
        self.assertTrue(
            object.__getattribute__(phase1_reader, "_closed")
        )
        denial = facade.take_expert_replay_denial(replay)
        self.assertIs(
            denial.result.mismatch,
            ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
        )
        self.assertIsNotNone(
            denial.proof.phase1_replay_summary_sha256
        )
        self.assertEqual(
            denial.result.evidence_raw_count,
            expected_raw_count,
        )
        self.assertEqual(
            denial.result.evidence_derived_count,
            expected_derived_count,
        )
        self.assertNotIn("evidence", state)
        self.assertEqual(
            store_module._WRITERS[writer].state,
            "poisoned",
        )

    def test_mid_replay_diagnostic_preserves_last_acknowledged_cursor_counts(
        self,
    ) -> None:
        writer, expert_manifest, cursor = self.create_real_companion()
        replay = facade.issue_expert_replay_construction_authority(
            self.authority,
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        facade.prepare_expert_replay_begin(replay)
        state = store_module._REPLAYS[replay]
        _, _, acknowledged = _group_fixture(expert_manifest, cursor)
        accumulator = object.__new__(
            store_module.ExpertReplayAccumulatorV1
        )
        object.__setattr__(accumulator, "cursor", acknowledged)
        object.__setattr__(accumulator, "mismatch", None)
        state["accumulator"] = accumulator
        denial = store_module._contextual_replay_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
            sampled=1,
        )
        self.assertEqual(
            denial.proof.acknowledged_parent_count,
            acknowledged.group_count,
        )
        self.assertEqual(
            denial.proof.acknowledged_expert_record_count,
            acknowledged.record_count,
        )
        self.assertEqual(
            denial.result.expert_group_count,
            acknowledged.group_count,
        )
        self.assertEqual(
            denial.result.expert_record_count,
            acknowledged.record_count,
        )
        facade.abort_expert_replay_construction(replay)
        facade.abort_expert_writer(writer)

    def test_replay_proves_terminal_physical_eof_rejects_unknown_records_and_drains_unmatched_side(
        self,
    ) -> None:
        token, state = self.replay_state("pair_empty")
        terminal = raw_parent(session_id=self.manifest.session_id)
        terminal = self._clone_event(
            terminal,
            record_kind=store_module.RecordKind.CONTROL,
            event_type="SESSION_HALT",
        )
        trailing = raw_parent(
            session_id=self.manifest.session_id,
            ingest_seq=4,
        )
        state["phase1_records"] = iter((terminal, trailing))
        with mock.patch.object(
            store_module,
            "_replay_cached_state",
            return_value=state,
        ), mock.patch.object(
            store_module,
            "_replay_full_integrity_gate",
            return_value=None,
        ), mock.patch.object(
            store_module,
            "_require_authorizer",
            return_value=self.manifest,
        ), mock.patch.object(
            store_module,
            "_phase1_sample_wall_ns",
            return_value=1,
        ):
            with self.assertRaises(ExpertReplayAccessDenied):
                facade.read_next_replay_evidence_parent(token)
        denial = facade.take_expert_replay_denial(token)
        self.assertIs(
            denial.result.mismatch,
            ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
        )

        self.tearDown()
        self.setUp()
        token, state = self.replay_state("evidence_eof_ready")
        state["companion_reader"] = mock.sentinel.companion
        unmatched = (mock.sentinel.group, ())
        with mock.patch.object(
            store_module,
            "_replay_cached_state",
            return_value=state,
        ), mock.patch.object(
            store_module,
            "_require_authorizer",
            return_value=self.manifest,
        ), mock.patch.object(
            store_module,
            "_phase1_sample_wall_ns",
            return_value=1,
        ), mock.patch.object(
            store_module,
            "read_next_expert_group",
            side_effect=(unmatched, unmatched, None),
        ) as read_group, mock.patch.object(
            store_module,
            "read_expert_terminal_and_summary",
            return_value=(None, mock.sentinel.summary),
        ):
            self.assertEqual(
                facade.read_next_replay_companion_group(token),
                unmatched,
            )
            self.assertEqual(state["state"], "cardinality_mismatch")
            facade.read_replay_finish_material(token)
        self.assertEqual(read_group.call_count, 3)

        self.tearDown()
        self.setUp()
        token, state = self.replay_state("cardinality_mismatch")
        parent = raw_parent(
            session_id=self.manifest.session_id,
            ingest_seq=2,
        )
        read_capability = self.coordinator.issue_read_capability(
            persistence_authorizer=self.authorizer
        )
        phase1_reader = JournalReader.open(
            read_capability=read_capability
        )
        try:
            terminal_source = tuple(phase1_reader.iter_records())[-1]
        finally:
            phase1_reader.close()
        terminal = self._clone_event(
            terminal_source,
            ingest_seq=3,
        )
        state["phase1_records"] = iter((parent, terminal))
        state["last_phase1_ingest_seq"] = 1
        state["cardinality_side"] = "evidence"
        state["companion_reader"] = mock.sentinel.companion
        with mock.patch.object(
            store_module,
            "_replay_cached_state",
            return_value=state,
        ), mock.patch.object(
            store_module,
            "_replay_full_integrity_gate",
            return_value=None,
        ), mock.patch.object(
            store_module,
            "_require_authorizer",
            return_value=self.manifest,
        ), mock.patch.object(
            store_module,
            "_phase1_sample_wall_ns",
            return_value=1,
        ), mock.patch.object(
            store_module,
            "read_expert_terminal_and_summary",
            return_value=(None, mock.sentinel.summary),
        ):
            facade.read_replay_finish_material(token)
        self.assertTrue(state["phase1_terminal_seen"])
        self.assertTrue(state["phase1_physical_eof"])

    def test_identity_collectors_reject_mutation_between_anchor_read_and_post_stat(
        self,
    ) -> None:
        writer, expert_manifest, _ = self.create_real_companion()
        expert_marker_path = (
            self.root_path
            / "state"
            / "expert-v1"
            / "markers"
            / store_module._marker_basename(self.manifest.session_id)
        )
        original_pread = store_module._pread_exact
        expert_mutated = False

        def mutate_expert_after_anchor(descriptor, offset, length):
            nonlocal expert_mutated
            content = original_pread(descriptor, offset, length)
            if (
                not expert_mutated
                and offset == 0
                and length == expert_marker_path.stat().st_size
                and os.fstat(descriptor).st_ino
                == expert_marker_path.stat().st_ino
            ):
                expert_mutated = True
                expert_marker_path.write_bytes(content + b" ")
                os.chmod(expert_marker_path, 0o600)
            return content

        with mock.patch.object(
            store_module,
            "_pread_exact",
            side_effect=mutate_expert_after_anchor,
        ):
            with self.assertRaises(ValueError):
                facade.inspect_expert_companion_file_identities(
                    self.authority,
                    manifest=expert_manifest,
                )
        self.assertTrue(expert_mutated)
        facade.abort_expert_writer(writer)

        marker_path = (
            self.root_path
            / "state"
            / "retention-markers"
            / f"{self.manifest.session_id}.marker.json"
        )
        mutated = False

        def mutate_after_anchor(descriptor, offset, length):
            nonlocal mutated
            content = original_pread(descriptor, offset, length)
            if not mutated and offset == 0 and length == marker_path.stat().st_size:
                mutated = True
                marker_path.write_bytes(content + b" ")
                os.chmod(marker_path, 0o600)
            return content

        with mock.patch.object(
            store_module,
            "_pread_exact",
            side_effect=mutate_after_anchor,
        ):
            with self.assertRaises(ValueError):
                facade.inspect_phase1_evidence_file_identities(
                    self.authority,
                    session_manifest=self.manifest,
                    session_start=self.session_start,
                )
        self.assertTrue(mutated)

    def test_diagnostic_file_proof_classifies_invalid_header_not_every_open_entry_as_replaced(
        self,
    ) -> None:
        writer, expert_manifest, _ = self.create_real_companion()
        state = store_module._WRITERS[writer]
        os.pwrite(state.journal_fd, b"X", 0)
        os.fsync(state.journal_fd)
        original_stat = os.stat
        named_stats: list[tuple[object, object]] = []

        def observe_named_stat(path, *args, **kwargs):
            named_stats.append((path, kwargs.get("dir_fd")))
            return original_stat(path, *args, **kwargs)

        with (
            mock.patch.object(
                store_module,
                "_replay_access_gate",
                return_value=1,
            ),
            mock.patch.object(
                store_module.os,
                "stat",
                side_effect=observe_named_stat,
            ),
        ):
            proof = store_module._identity_file_proof(
                {
                    "root": self.root,
                    "manifest": self.manifest,
                    "expert_manifest": expert_manifest,
                    "deadline": self.manifest.required_retention_until_ns,
                },
                ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL,
            )
        self.assertIs(
            proof.issue,
            store_module.ExpertReplayDiagnosticIssueV1.HEADER_INVALID,
        )
        self.assertIn(
            (
                store_module._journal_basename(self.manifest.session_id),
                self.root.sessions_fd,
            ),
            named_stats,
        )

    def test_environment_inventory_uses_retained_source_root_descriptor(
        self,
    ) -> None:
        with mock.patch.object(
            Path,
            "read_bytes",
            side_effect=AssertionError("ambient path read"),
        ):
            observed = store_module._read_source_file(
                self.root,
                "inci_tennis_expert/contracts.py",
            )
            phase1_digest = store_module._phase1_code_sha256_fd(self.root)
        source_root = Path(store_module.__file__).resolve().parent.parent
        self.assertEqual(
            observed,
            (
                source_root
                / "inci_tennis_expert"
                / "contracts.py"
            ).read_bytes(),
        )
        self.assertEqual(
            phase1_digest,
            code_sha256(source_root / "tennis_v1"),
        )

    def _environment_gate_fixture(self) -> tuple[object, object]:
        adapter_code_sha256 = "a" * 64
        manifest = mock.Mock(
            provider_id="synthetic-provider",
            product_tier="trial-v1",
            code_sha256=store_module._phase1_code_sha256_fd(self.root),
            adapter_code_sha256=adapter_code_sha256,
        )
        adapter = mock.Mock(
            adapter_code_sha256=adapter_code_sha256,
        )
        return manifest, adapter

    def test_environment_descriptor_close_uncertainty_returns_no_environment(
        self,
    ) -> None:
        if self._run_fatal_root_test_in_subprocess():
            return
        manifest, adapter = self._environment_gate_fixture()
        root = self.root
        store_module._read_source_file(
            root,
            "inci_tennis_expert/contracts.py",
        )
        root.last_environment = mock.sentinel.previous_environment
        original_dup = os.dup
        original_close = os.close
        target_fd = -1
        uncertain_target_attempts = 0

        def capture_source_root_dup(descriptor: int) -> int:
            nonlocal target_fd
            duplicated = original_dup(descriptor)
            if descriptor == root.source_root_fd and target_fd < 0:
                target_fd = duplicated
            return duplicated

        def uncertain_temporary_close(descriptor: int) -> None:
            nonlocal uncertain_target_attempts
            if descriptor == target_fd:
                uncertain_target_attempts += 1
                raise OSError(errno.EIO, "uncertain environment close")
            original_close(descriptor)

        try:
            with (
                mock.patch.object(
                    store_module,
                    "load_active_adapter_contract",
                    return_value=adapter,
                ),
                mock.patch.object(
                    store_module.os,
                    "dup",
                    side_effect=capture_source_root_dup,
                ),
                mock.patch.object(
                    store_module.os,
                    "close",
                    side_effect=uncertain_temporary_close,
                ),
                self.assertRaisesRegex(
                    OSError,
                    "^expert_environment_descriptor_close_uncertain$",
                ),
            ):
                store_module._installed_environment(root, manifest)
        finally:
            if target_fd >= 0:
                try:
                    original_close(target_fd)
                except OSError:
                    pass

        self.assertGreaterEqual(target_fd, 0)
        self.assertEqual(uncertain_target_attempts, 1)
        self.assertFalse(root.active)
        self.assertEqual(root.source_content_cache, {})
        self.assertIsNone(root.last_environment)

    def test_warm_environment_inventory_has_exact_bounded_gate_budget(
        self,
    ) -> None:
        manifest, adapter = self._environment_gate_fixture()
        with mock.patch.object(
            store_module,
            "load_active_adapter_contract",
            return_value=adapter,
        ):
            expected = store_module._installed_environment(
                self.root,
                manifest,
            )
            gate_count = 0

            def gate() -> None:
                nonlocal gate_count
                gate_count += 1

            with mock.patch.object(
                store_module.os,
                "pread",
                side_effect=AssertionError("warm source byte read"),
            ) as pread:
                observed = store_module._installed_environment(
                    self.root,
                    manifest,
                    gate=gate,
                )

        self.assertEqual(observed, expected)
        pread.assert_not_called()
        self.assertEqual(gate_count, 115)

    def test_cold_environment_inventory_gates_each_pread_immediately(
        self,
    ) -> None:
        manifest, adapter = self._environment_gate_fixture()
        self.root.source_content_cache.clear()
        events: list[str] = []
        original_pread = store_module.os.pread

        def gate() -> None:
            events.append("gate")

        def traced_pread(
            descriptor: int,
            length: int,
            offset: int,
        ) -> bytes:
            events.append("pread")
            return original_pread(descriptor, length, offset)

        with (
            mock.patch.object(
                store_module,
                "load_active_adapter_contract",
                return_value=adapter,
            ),
            mock.patch.object(
                store_module.os,
                "pread",
                side_effect=traced_pread,
            ),
        ):
            store_module._installed_environment(
                self.root,
                manifest,
                gate=gate,
            )

        pread_indexes = tuple(
            index
            for index, event in enumerate(events)
            if event == "pread"
        )
        self.assertTrue(pread_indexes)
        for index in pread_indexes:
            self.assertGreater(index, 0)
            self.assertLess(index + 1, len(events))
            self.assertEqual(
                events[index - 1:index + 2],
                ["gate", "pread", "gate"],
            )
        self.assertEqual(
            events.count("gate"),
            115 + 2 * len(pread_indexes),
        )
        self.assertEqual(events.count("gate"), 313)

    def test_cached_source_file_uses_one_logical_gate_and_no_pread(
        self,
    ) -> None:
        logical = "requirements.txt"
        expected = store_module._read_source_file(self.root, logical)
        gate_count = 0

        def gate() -> None:
            nonlocal gate_count
            gate_count += 1

        with mock.patch.object(
            store_module.os,
            "pread",
            side_effect=AssertionError("cached source byte read"),
        ) as pread:
            observed = store_module._read_source_file(
                self.root,
                logical,
                gate=gate,
            )

        self.assertEqual(observed, expected)
        pread.assert_not_called()
        self.assertEqual(gate_count, 1)

    def test_guarded_named_read_uses_access_for_metadata_and_full_for_bytes(
        self,
    ) -> None:
        basename = "dual-gate.bin"
        path = self.root_path / basename
        content = b"dual-gate-content"
        path.write_bytes(content)
        path.chmod(0o600)
        directory_fd = os.open(
            self.root_path,
            store_module._OPEN_DIRECTORY_FLAGS,
        )
        descriptor = os.open(
            basename,
            store_module._OPEN_FILE_READ_FLAGS,
            dir_fd=directory_fd,
        )
        events: list[str] = []
        original_pread = store_module.os.pread

        def access_gate() -> None:
            events.append("access")

        def full_gate() -> None:
            events.append("full")

        def traced_pread(
            fd: int,
            length: int,
            offset: int,
        ) -> bytes:
            events.append("pread")
            return original_pread(fd, length, offset)

        try:
            with mock.patch.object(
                store_module.os,
                "pread",
                side_effect=traced_pread,
            ):
                observed, _ = (
                    store_module._guarded_stable_named_file_read(
                        fd=descriptor,
                        directory_fd=directory_fd,
                        basename=basename,
                        offset=0,
                        length=len(content),
                        gate=access_gate,
                        byte_gate=full_gate,
                    )
                )
        finally:
            os.close(descriptor)
            os.close(directory_fd)

        self.assertEqual(observed, content)
        self.assertEqual(
            events,
            [
                "access",
                "access",
                "access",
                "full",
                "pread",
                "full",
                "access",
                "access",
                "access",
            ],
        )

    def test_guarded_named_read_pre_and_post_byte_full_failures_do_not_return(
        self,
    ) -> None:
        basename = "dual-gate-failure.bin"
        path = self.root_path / basename
        content = b"dual-gate-failure"
        path.write_bytes(content)
        path.chmod(0o600)
        for failure_call, expected_preads in ((1, 0), (2, 1)):
            with self.subTest(failure_call=failure_call):
                directory_fd = os.open(
                    self.root_path,
                    store_module._OPEN_DIRECTORY_FLAGS,
                )
                descriptor = os.open(
                    basename,
                    store_module._OPEN_FILE_READ_FLAGS,
                    dir_fd=directory_fd,
                )
                full_calls = 0
                pread_calls = 0
                original_pread = store_module.os.pread

                def access_gate() -> None:
                    return None

                def full_gate() -> None:
                    nonlocal full_calls
                    full_calls += 1
                    if full_calls == failure_call:
                        raise RuntimeError("full_integrity_denied")

                def traced_pread(
                    fd: int,
                    length: int,
                    offset: int,
                ) -> bytes:
                    nonlocal pread_calls
                    pread_calls += 1
                    return original_pread(fd, length, offset)

                try:
                    with (
                        mock.patch.object(
                            store_module.os,
                            "pread",
                            side_effect=traced_pread,
                        ),
                        self.assertRaisesRegex(
                            RuntimeError,
                            "^full_integrity_denied$",
                        ),
                    ):
                        store_module._guarded_stable_named_file_read(
                            fd=descriptor,
                            directory_fd=directory_fd,
                            basename=basename,
                            offset=0,
                            length=len(content),
                            gate=access_gate,
                            byte_gate=full_gate,
                        )
                finally:
                    os.close(descriptor)
                    os.close(directory_fd)

                self.assertEqual(pread_calls, expected_preads)

    def test_identity_collectors_have_bounded_full_byte_gate_budget(
        self,
    ) -> None:
        phase1_access = 0
        phase1_full = 0

        def phase1_access_gate() -> None:
            nonlocal phase1_access
            phase1_access += 1

        def phase1_full_gate() -> None:
            nonlocal phase1_full
            phase1_full += 1

        store_module._guarded_phase1_evidence_file_identities(
            self.root,
            session_manifest=self.manifest,
            session_start=self.session_start,
            gate=phase1_access_gate,
            byte_gate=phase1_full_gate,
        )
        self.assertEqual((phase1_access, phase1_full), (17, 4))

        writer, expert_manifest, _ = self.create_real_companion()
        companion_access = 0
        companion_full = 0

        def companion_access_gate() -> None:
            nonlocal companion_access
            companion_access += 1

        def companion_full_gate() -> None:
            nonlocal companion_full
            companion_full += 1

        try:
            store_module._guarded_expert_companion_file_identities(
                self.root,
                manifest=expert_manifest,
                gate=companion_access_gate,
                byte_gate=companion_full_gate,
            )
        finally:
            facade.abort_expert_writer(writer)
        self.assertEqual(
            (companion_access, companion_full),
            (29, 8),
        )

    @staticmethod
    def _clone_event(source, **changes):
        target = object.__new__(type(source))
        for item in fields(source):
            object.__setattr__(
                target,
                item.name,
                changes.get(item.name, getattr(source, item.name)),
            )
        return target

    def test_replay_construction_authority_runs_every_state_edge_and_never_reads_with_an_outstanding_token(
        self,
    ) -> None:
        expert_manifest = self.bound_expert_manifest(
            self.manifest,
            self.decision,
        )
        cursor = _genesis_cursor(expert_manifest)
        with mock.patch.object(
            store_module,
            "_creation_gate",
            return_value=1,
        ):
            writer = facade.create_expert_journal(
                self.authority,
                expert_manifest,
                cursor,
                persistence_authorizer=self.authorizer,
                coordinator=self.coordinator,
            )
        replay = facade.issue_expert_replay_construction_authority(
            self.authority,
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        self.assertIsInstance(
            replay,
            ExpertReplayConstructionAuthorityV1,
        )
        ready = facade.prepare_expert_replay_begin(replay)
        self.assertEqual(ready.manifest, expert_manifest)
        self.assertEqual(
            ready.evidence.session_manifest,
            self.manifest,
        )
        prepared = store_module._REPLAYS[replay]
        self.assertEqual(prepared["state"], "begin_ready")
        self.assertIsInstance(
            prepared["phase1_reader"],
            store_module.JournalReader,
        )
        self.assertIsInstance(
            prepared["companion_reader"],
            ExpertJournalReadCapabilityV1,
        )
        prepared_companion_reader = prepared["companion_reader"]
        prepared_payload = {
            name: prepared[name]
            for name in (
                "identity_set",
                "evidence",
                "expert_manifest",
                "expected_environment",
            )
        }
        facade.abort_expert_replay_construction(replay)
        self.assertEqual(prepared["state"], "aborted_closed")
        self.assertNotIn(
            prepared_companion_reader,
            store_module._READERS,
        )
        for operation in (
            facade.prepare_expert_replay_begin,
            facade.read_next_replay_evidence_parent,
            facade.read_next_replay_companion_group,
            facade.read_replay_finish_material,
            facade.issue_begin_replay_authorization,
            facade.issue_parent_group_replay_authorization,
            facade.issue_finish_replay_authorization,
            facade.take_expert_replay_denial,
            facade.abort_expert_replay_construction,
        ):
            with self.subTest(post_close_operation=operation.__name__):
                with self.assertRaises(ValueError):
                    operation(replay)
        closed_authorization = object.__new__(
            store_module.RetentionReplayAuthorizationV1
        )
        closed_accumulator = object.__new__(
            store_module.ExpertReplayAccumulatorV1
        )
        closed_result = object.__new__(
            store_module.ExpertReplayResultV1
        )
        for operation in (
            lambda: facade.acknowledge_begin_replay(
                replay,
                authorization=closed_authorization,
                accumulator=closed_accumulator,
            ),
            lambda: facade.acknowledge_parent_group_replay(
                replay,
                authorization=closed_authorization,
                accumulator=closed_accumulator,
            ),
            lambda: facade.acknowledge_finish_replay(
                replay,
                authorization=closed_authorization,
                result=closed_result,
            ),
        ):
            with self.assertRaises(ValueError):
                operation()
        facade.abort_expert_writer(writer)

        def bare_contract(contract, **values):
            result = object.__new__(contract)
            for name, value in values.items():
                object.__setattr__(result, name, value)
            return result

        authorization = bare_contract(
            store_module.RetentionReplayAuthorizationV1,
            authorization_sha256="a" * 64,
        )
        accumulator = bare_contract(
            store_module.ExpertReplayAccumulatorV1,
            last_authorization_sha256="a" * 64,
            mismatch=None,
        )
        result = bare_contract(
            store_module.ExpertReplayResultV1,
            final_authorization_sha256="a" * 64,
        )
        token, state = self.replay_state("begin_ready")
        state.update(prepared_payload)
        expert_manifest = state["expert_manifest"]
        synchronization = mock.Mock(
            universe_sha256=(
                expert_manifest.match_binding_universe_sha256
            ),
            sync_policy_sha256=expert_manifest.sync_policy_sha256,
            universe=mock.sentinel.universe,
            policy=mock.sentinel.policy,
        )
        object.__setattr__(
            accumulator,
            "current_environment",
            state["expected_environment"],
        )
        object.__setattr__(
            accumulator,
            "state",
            mock.Mock(synchronization=synchronization),
        )
        state["companion_reader"] = mock.sentinel.reader
        issued_operations: list[str] = []

        def issue(**values):
            issued_operations.append(values["authorized_operation"])
            return authorization

        parent = raw_parent(session_id=self.manifest.session_id)
        state["phase1_records"] = iter((parent,))
        with mock.patch.object(
            store_module,
            "_require_authorizer",
            return_value=self.manifest,
        ), mock.patch.object(
            store_module,
            "_replay_full_integrity_gate",
            return_value=None,
        ), mock.patch.object(
            store_module,
            "_close_replay_owned_readers",
            return_value=None,
        ), mock.patch.object(
            store_module,
            "deepcopy",
            side_effect=lambda value: value,
        ), mock.patch.object(
            store_module.RetentionReplayAuthorizationV1,
            "_validate",
            return_value=None,
        ), mock.patch.object(
            store_module.ExpertReplayAccumulatorV1,
            "__post_init__",
            return_value=None,
        ), mock.patch.object(
            store_module.ExpertReplayResultV1,
            "__post_init__",
            return_value=None,
        ), mock.patch.object(
            store_module,
            "begin_expert_replay",
            return_value=accumulator,
        ), mock.patch.object(
            store_module,
            "replay_expert_parent_group",
            return_value=accumulator,
        ), mock.patch.object(
            store_module,
            "finish_expert_replay",
            return_value=result,
        ), mock.patch.object(
            store_module,
            "_phase1_sample_wall_ns",
            return_value=1,
        ), mock.patch.object(
            store_module,
            "inspect_phase1_evidence_file_identities",
            return_value=(mock.sentinel.phase1_marker, mock.sentinel.phase1_wal),
        ), mock.patch.object(
            store_module,
            "inspect_expert_companion_file_identities",
            return_value=(mock.sentinel.expert_marker, mock.sentinel.expert_journal),
        ), mock.patch.object(
            store_module,
            "compute_retention_replay_authorization_sha256",
            return_value="a" * 64,
        ), mock.patch.object(
            store_module,
            "_create_retention_replay_authorization_v1",
            side_effect=issue,
        ), mock.patch.object(
            store_module,
            "read_next_expert_group",
            side_effect=((mock.sentinel.group, (mock.sentinel.payload,)), None),
        ), mock.patch.object(
            store_module,
            "_replay_group_seals",
            return_value=("b" * 64, ((1, "c" * 64),)),
        ), mock.patch.object(
            store_module,
            "_validated_replay_pair_snapshots",
            side_effect=lambda replay_state: (
                replay_state["current_parent"],
                replay_state["current_group"],
            ),
        ), mock.patch.object(
            store_module,
            "read_expert_terminal_and_summary",
            return_value=(None, mock.sentinel.summary),
        ):
            self.assertIs(
                facade.issue_begin_replay_authorization(token),
                authorization,
            )
            self.assertEqual(state["state"], "begin_auth_outstanding")
            facade.acknowledge_begin_replay(
                token,
                authorization=authorization,
                accumulator=accumulator,
            )
            self.assertEqual((state["state"], state["sequence"]), ("pair_empty", 1))
            self.assertIs(
                facade.read_next_replay_evidence_parent(token),
                parent,
            )
            self.assertEqual(state["state"], "evidence_parent_ready")
            self.assertEqual(
                facade.read_next_replay_companion_group(token),
                (mock.sentinel.group, (mock.sentinel.payload,)),
            )
            self.assertEqual(state["state"], "pair_complete")
            self.assertIs(
                facade.issue_parent_group_replay_authorization(token),
                authorization,
            )
            self.assertEqual(state["state"], "parent_auth_outstanding")
            facade.acknowledge_parent_group_replay(
                token,
                authorization=authorization,
                accumulator=accumulator,
            )
            self.assertEqual((state["state"], state["sequence"]), ("pair_empty", 2))
            self.assertIsNone(facade.read_next_replay_evidence_parent(token))
            self.assertEqual(state["state"], "evidence_eof_ready")
            self.assertIsNone(facade.read_next_replay_companion_group(token))
            self.assertEqual(state["state"], "both_eof")
            self.assertEqual(
                facade.read_replay_finish_material(token),
                (None, mock.sentinel.summary),
            )
            self.assertEqual(state["state"], "finish_ready")
            self.assertIs(
                facade.issue_finish_replay_authorization(token),
                authorization,
            )
            self.assertEqual(state["state"], "finish_auth_outstanding")
            facade.acknowledge_finish_replay(
                token,
                authorization=authorization,
                result=result,
            )
        self.assertEqual(state["state"], "consumed_closed")
        self.assertEqual(issued_operations, ["begin", "parent_group", "finish"])

        outstanding, outstanding_state = self.replay_state(
            "begin_auth_outstanding"
        )
        outstanding_state["outstanding"] = authorization
        with mock.patch.object(
            store_module,
            "_require_authorizer",
            return_value=self.manifest,
        ), mock.patch.object(
            store_module,
            "_phase1_sample_wall_ns",
            return_value=1,
        ):
            with self.assertRaises(ValueError):
                facade.read_next_replay_evidence_parent(outstanding)
        self.assertTrue(outstanding_state["closed"])
        self.assertEqual(
            outstanding_state["state"],
            "aborted_closed",
        )

        for evidence_present, companion_present in (
            (True, False),
            (False, True),
        ):
            branch, branch_state = self.replay_state(
                "evidence_parent_ready"
                if evidence_present
                else "evidence_eof_ready"
            )
            branch_state.update(prepared_payload)
            branch_state["companion_reader"] = mock.sentinel.reader
            if evidence_present:
                branch_parent = raw_parent(
                    session_id=self.manifest.session_id
                )
                branch_state["current_parent"] = branch_parent
                branch_state["current_parent_record_sha256"] = (
                    store_module._replay_parent_record_sha256(
                        branch_parent
                    )
                )
            with mock.patch.object(
                store_module,
                "_require_authorizer",
                return_value=self.manifest,
            ), mock.patch.object(
                store_module,
                "_phase1_sample_wall_ns",
                return_value=1,
            ), mock.patch.object(
                store_module,
                "read_next_expert_group",
                return_value=(
                    (mock.sentinel.group, ())
                    if companion_present
                    else None
                ),
            ), mock.patch.object(
                store_module,
                "_close_replay_owned_readers",
                return_value=None,
            ):
                facade.read_next_replay_companion_group(branch)
            self.assertEqual(branch_state["state"], "cardinality_mismatch")
            facade.abort_expert_replay_construction(branch)

        wrong, wrong_state = self.replay_state("begin_auth_outstanding")
        wrong_state.update(prepared_payload)
        wrong_state["outstanding"] = authorization
        bad_accumulator = bare_contract(
            store_module.ExpertReplayAccumulatorV1,
            last_authorization_sha256="b" * 64,
            mismatch=None,
        )
        with mock.patch.object(
            store_module,
            "_require_authorizer",
            return_value=self.manifest,
        ), mock.patch.object(
            store_module,
            "_phase1_sample_wall_ns",
            return_value=1,
        ), mock.patch.object(
            store_module,
            "_replay_full_integrity_gate",
            return_value=None,
        ):
            with self.assertRaises(ValueError):
                facade.acknowledge_begin_replay(
                    wrong,
                    authorization=authorization,
                    accumulator=bad_accumulator,
                )
        self.assertEqual(wrong_state["state"], "aborted_closed")

    def test_replay_deadline_authorization_identity_environment_denials_have_exact_proofs_and_no_forbidden_read(
        self,
    ) -> None:
        token, state = self.replay_state("new")
        with mock.patch.object(
            store_module,
            "_sample_replay_prepare_wall_ns",
            return_value=1,
        ), mock.patch.object(
            store_module,
            "_require_prepare_replay_authorizer",
            side_effect=ExpertLiveAuthorizationDenied(),
        ), mock.patch.object(
            store_module.JournalReader,
            "open",
        ) as open_reader:
            denial = facade.prepare_expert_replay_begin(token)
        open_reader.assert_not_called()
        self.assertEqual(state["state"], "denied_closed")
        self.assertIs(
            denial.result.mismatch,
            ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
        )
        self.assertEqual(denial.proof.file_proofs, ())
        with self.assertRaises(ValueError):
            facade.prepare_expert_replay_begin(token)

        token, state = self.replay_state("new")
        with mock.patch.object(
            store_module,
            "_sample_replay_prepare_wall_ns",
            return_value=self.manifest.required_retention_until_ns,
        ), mock.patch.object(
            store_module,
            "_require_prepare_replay_authorizer",
        ) as authorize, mock.patch.object(
            store_module.JournalReader,
            "open",
        ) as open_reader:
            denial = facade.prepare_expert_replay_begin(token)
        authorize.assert_not_called()
        open_reader.assert_not_called()
        self.assertEqual(state["state"], "denied_closed")
        self.assertIs(
            denial.result.mismatch,
            ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
        )
        self.assertEqual(denial.proof.file_proofs, ())

        def bare_contract(contract, **values):
            result = object.__new__(contract)
            for name, value in values.items():
                object.__setattr__(result, name, value)
            return result

        authorization = bare_contract(
            store_module.RetentionReplayAuthorizationV1,
            authorization_sha256="a" * 64,
        )
        accumulator = bare_contract(
            store_module.ExpertReplayAccumulatorV1,
            last_authorization_sha256="a" * 64,
            mismatch=None,
        )

        class IterationProbe:
            def __init__(self):
                self.calls = 0

            def __iter__(self):
                return self

            def __next__(self):
                self.calls += 1
                raise StopIteration

        for seam in ("read", "issue", "ack"):
            with self.subTest(deadline_equality_seam=seam):
                state_name = {
                    "read": "pair_empty",
                    "issue": "begin_ready",
                    "ack": "begin_auth_outstanding",
                }[seam]
                token, state = self.replay_state(state_name)
                probe = IterationProbe()
                state["phase1_records"] = probe
                state["outstanding"] = authorization
                with mock.patch.object(
                    store_module,
                    "_phase1_sample_wall_ns",
                    return_value=(
                        self.manifest.required_retention_until_ns
                    ),
                ), mock.patch.object(
                    store_module,
                    "_require_authorizer",
                ) as authorize, mock.patch.object(
                    store_module,
                    "inspect_phase1_evidence_file_identities",
                ) as evidence_identity, mock.patch.object(
                    store_module,
                    "inspect_expert_companion_file_identities",
                ) as companion_identity:
                    with self.assertRaises(ExpertReplayAccessDenied):
                        if seam == "read":
                            facade.read_next_replay_evidence_parent(token)
                        elif seam == "issue":
                            facade.issue_begin_replay_authorization(token)
                        else:
                            facade.acknowledge_begin_replay(
                                token,
                                authorization=authorization,
                                accumulator=accumulator,
                            )
                self.assertEqual(probe.calls, 0)
                authorize.assert_not_called()
                evidence_identity.assert_not_called()
                companion_identity.assert_not_called()
                denial = facade.take_expert_replay_denial(token)
                self.assertIs(
                    denial.result.mismatch,
                    ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
                )

        expert_manifest = _manifest_fixture()
        journal_path = (
            self.root_path
            / "state"
            / "expert-v1"
            / "sessions"
            / f"{expert_manifest.session_id}.expert-journal-v1"
        )
        journal_path.write_bytes(b"changed-identity")
        os.chmod(journal_path, 0o600)
        identity_state: dict[str, object] = {
            "root": self.root,
            "expert_manifest": expert_manifest,
            "deadline": self.manifest.required_retention_until_ns,
        }
        with mock.patch.object(
            store_module,
            "_replay_access_gate",
            return_value=1,
        ):
            identity_denial = store_module._identity_denial(
                identity_state,
                sampled=1,
                role=(
                    store_module.ExpertReplayDiagnosticRoleV1
                    .EXPERT_JOURNAL
                ),
            )
        self.assertIs(
            identity_denial.result.mismatch,
            ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
        )
        self.assertEqual(len(identity_denial.proof.file_proofs), 1)
        self.assertLessEqual(
            identity_denial.proof.file_proofs[0].observed_prefix_length,
            4096,
        )

        writer, _, _ = self.create_real_companion()
        seed_replay = (
            facade.issue_expert_replay_construction_authority(
                self.authority,
                persistence_authorizer=self.authorizer,
                coordinator=self.coordinator,
            )
        )
        facade.prepare_expert_replay_begin(seed_replay)
        seed_state = store_module._REPLAYS[seed_replay]
        prepared_payload = {
            name: seed_state[name]
            for name in (
                "identity_set",
                "evidence",
                "expert_manifest",
                "expected_environment",
            )
        }
        facade.abort_expert_replay_construction(seed_replay)
        expected_environment = prepared_payload["expected_environment"]
        environment_fields = tuple(
            item.name
            for item in fields(expected_environment)
            if item.name.endswith("_sha256")
        )
        self.assertEqual(len(environment_fields), 11)
        try:
            for field_name in environment_fields:
                with self.subTest(environment_field=field_name):
                    token, state = self.replay_state("finish_closing")
                    state.update(prepared_payload)
                    self.root.last_environment = expected_environment
                    changed = replace(
                        expected_environment,
                        **{
                            field_name: (
                                "b" * 64
                                if getattr(
                                    expected_environment,
                                    field_name,
                                )
                                != "b" * 64
                                else "c" * 64
                            )
                        },
                    )
                    with (
                        mock.patch.object(
                            store_module,
                            "_phase1_sample_wall_ns",
                            return_value=1,
                        ),
                        mock.patch.object(
                            store_module,
                            "_require_authorizer",
                            return_value=self.manifest,
                        ),
                        mock.patch.object(
                            store_module,
                            "_installed_environment",
                            return_value=(
                                changed,
                                mock.sentinel.normalizers,
                                mock.sentinel.structural,
                                mock.sentinel.event,
                            ),
                        ) as installed,
                        mock.patch.object(
                            store_module,
                            "_purge_names",
                            return_value=None,
                        ),
                        self.assertRaises(ExpertReplayAccessDenied),
                    ):
                        store_module._replay_state(
                            token,
                            "finish_closing",
                        )
                    installed.assert_called_once()
                    self.assertEqual(
                        installed.call_args.args,
                        (self.root, self.manifest),
                    )
                    self.assertTrue(
                        callable(
                            installed.call_args.kwargs["gate"]
                        )
                    )
                    denial = facade.take_expert_replay_denial(token)
                    self.assertIs(
                        denial.result.mismatch,
                        (
                            ExpertReplayMismatchV1
                            .CURRENT_ENVIRONMENT_MISMATCH
                        ),
                    )
                    self.assertEqual(denial.proof.file_proofs, ())
        finally:
            facade.abort_expert_writer(writer)

    def test_replay_root_gate_defers_loaded_source_drift_to_environment_validation(
        self,
    ) -> None:
        package = self.root.source_packages[-1]
        with mock.patch.object(
            package.module,
            "__file__",
            package.origin + ".alias",
        ):
            store_module._validate_replay_prepare_root_after_access_gate(
                self.root
            )
            with self.assertRaisesRegex(
                ValueError,
                "^expert_source_root_invalid$",
            ):
                store_module._validate_source_root(self.root)


class ExpertRootPhase1LifecycleIntegrationTests(unittest.TestCase):
    def test_root_acquired_before_phase1_arm_survives_real_startup_environment_collection(
        self,
    ) -> None:
        from tennis_v1.sequencer import (
            EventRuntime,
            bind_provider_persistence_authorizer,
        )
        from tests.tennis_v1.test_sequencer import concrete_environment

        with concrete_environment() as (
            _,
            coordinator,
            provider_gate,
            phase1_manifest,
        ):
            runtime = None
            authorizer = bind_provider_persistence_authorizer(
                gate=provider_gate,
                coordinator=coordinator,
                session_manifest=phase1_manifest,
            )
            authority = facade.acquire_expert_journal_root(
                coordinator.issue_expert_state_root_account_lock_request()
            )
            root_identity = id(authority)
            capability = coordinator.arm_before_wal(
                session_manifest=phase1_manifest,
                decision=authorizer.bound_decision,
                persistence_authorizer=authorizer,
            )
            phase1_writer = JournalWriter.create(
                write_capability=capability,
                session_manifest=phase1_manifest,
            )
            runtime = EventRuntime(
                writer=phase1_writer,
                state=phase1_initial_state(phase1_manifest.session_id),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
            try:
                environment_authority = (
                    facade.issue_expert_environment_collection_authority(
                        authority,
                        persistence_authorizer=authorizer,
                        coordinator=coordinator,
                    )
                )
                _, _, template = task6_artifacts()
                with mock.patch.object(
                    store_module,
                    "_installed_environment",
                    return_value=(
                        template.environment,
                        template.normalizers,
                        template.structural_schemas,
                        template.event_schemas,
                    ),
                ):
                    collected = facade.collect_expert_current_environment(
                        environment_authority
                    )
                self.assertEqual(id(authority), root_identity)
                self.assertIs(
                    store_module._ROOTS[authority].coordinator,
                    coordinator,
                )
                self.assertIs(collected.current, template.environment)
            finally:
                if not object.__getattribute__(runtime, "_closed"):
                    runtime.close_clean("operator_stop")

    def test_pre_phase1_root_survives_clean_close_terminalization_and_same_root_replay(
        self,
    ) -> None:
        import tests.tennis_v1.test_expert_replay as replay_fixture

        phase1_fixture = replay_fixture._test_events.SessionContractTests(
            "test_session_manifest_requires_verified_eligible_matching_inputs"
        )
        phase1_fixture.setUp()
        adapter_patch = mock.patch.multiple(
            replay_fixture.phase1_adapter_contract,
            __file__=phase1_fixture.builder.adapter_file,
            _ADAPTER_REGISTRY={
                (
                    "synthetic-provider",
                    "trial-v1",
                ): phase1_fixture.builder.registration
            },
        )
        coordinator = None
        expert_writer = None
        replay = None
        runtime = None
        try:
            adapter_patch.start()
            phase1_manifest = phase1_fixture.build(
                code_sha256=replay_fixture.phase1_code_sha256(
                    replay_fixture.ROOT / "tennis_v1"
                )
            )
            clock = MutableClock(phase1_manifest.created_wall_ns)
            coordinator = RetentionCoordinator.acquire(
                phase1_fixture.config,
                clock_ns=clock,
            )
            coordinator.recover_and_purge()

            provider_gate = replay_fixture.ProviderGate(
                phase1_fixture.config,
                phase1_fixture.provider_manifest,
                phase1_fixture.request,
                environ={"SYNTHETIC_API_KEY": "fixture-secret"},
                clock=lambda: phase1_fixture.now,
            )
            authorizer = replay_fixture.bind_provider_persistence_authorizer(
                gate=provider_gate,
                coordinator=coordinator,
                session_manifest=phase1_manifest,
            )
            write_capability = coordinator.arm_before_wal(
                session_manifest=phase1_manifest,
                decision=authorizer.bound_decision,
                persistence_authorizer=authorizer,
            )
            phase1_writer = JournalWriter.create(
                write_capability=write_capability,
                session_manifest=phase1_manifest,
            )
            runtime = replay_fixture.EventRuntime(
                writer=phase1_writer,
                state=phase1_initial_state(phase1_manifest.session_id),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )

            authority = facade.acquire_expert_journal_root(
                coordinator.issue_expert_state_root_account_lock_request()
            )
            root_identity = id(authority)

            environment_authority = (
                facade.issue_expert_environment_collection_authority(
                    authority,
                    persistence_authorizer=authorizer,
                    coordinator=coordinator,
                )
            )
            collected = facade.collect_expert_current_environment(
                environment_authority
            )
            universe, policy, manifest = (
                replay_fixture._real_expert_manifest(
                    phase1=phase1_manifest,
                    session_start=phase1_writer.session_start,
                    authorizer=authorizer,
                    collected=collected,
                )
            )
            state = initial_expert_state(manifest, universe, policy)
            cursor = replay_fixture._genesis_cursor(manifest, state)
            expert_writer = facade.create_expert_journal(
                authority,
                manifest,
                cursor,
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
            store_root = store_module._ROOTS[authority]
            grant_authority = coordinator._expert_root_grants[
                store_root.grant
            ]
            generations = (
                coordinator._generation,
                grant_authority.generation,
                store_root.generation,
                store_module._WRITERS[expert_writer].generation,
            )

            parent = runtime.ingest(
                replay_fixture.captured(
                    authorizer,
                    provider_sequence="A-1",
                )
            )
            group, payloads, candidate, reduction = (
                replay_fixture._independent_group(
                    manifest,
                    cursor,
                    parent,
                    prior_state_override=state,
                )
            )
            append_permit = facade.issue_expert_append_permit(
                expert_writer,
                cursor.expert_state_sha256,
                cursor,
                group,
                payloads,
            )
            receipt = facade.append_expert_group(append_permit)
            facade.acknowledge_expert_publication(
                expert_writer,
                receipt=receipt,
                candidate_state_sha256=candidate.expert_state_sha256,
                candidate_cursor=candidate,
            )
            cursor = candidate
            state = reduction.final_state

            phase1_terminal = runtime.close_clean("operator_stop")
            self.assertEqual(
                (
                    coordinator._generation,
                    grant_authority.generation,
                    store_root.generation,
                    store_module._WRITERS[expert_writer].generation,
                ),
                generations,
            )
            current_sessions = tuple(
                (
                    value.st_dev,
                    value.st_ino,
                    value.st_mode,
                    value.st_nlink,
                    value.st_uid,
                )
                for value in (
                    os.fstat(coordinator._sessions_fd),
                    os.fstat(grant_authority.sessions_fd),
                    os.stat(
                        "sessions",
                        dir_fd=coordinator._state_fd,
                        follow_symlinks=False,
                    ),
                )
            )
            self.assertEqual(
                current_sessions,
                (current_sessions[0],) * 3,
            )
            self.assertEqual(
                facade.sample_expert_retention_wall_ns(authority),
                clock.now_ns,
            )
            self.assertEqual(id(authority), root_identity)
            self.assertIsNone(
                facade.prove_expert_live_evidence_tail(
                    expert_writer,
                    published_cursor=cursor,
                )
            )
            evidence_terminal, terminal = (
                facade.build_aligned_expert_terminal(
                    expert_writer,
                    final_state=state,
                    final_cursor=cursor,
                )
            )
            self.assertEqual(evidence_terminal, phase1_terminal)
            terminal_permit = facade.issue_expert_terminal_permit(
                expert_writer,
                terminal,
            )
            facade.append_expert_terminal(terminal_permit)

            replay = facade.issue_expert_replay_construction_authority(
                authority,
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
            self.assertIsInstance(
                replay,
                ExpertReplayConstructionAuthorityV1,
            )
            with mock.patch.object(
                store_module,
                "_installed_environment",
                return_value=(
                    collected.current,
                    collected.normalizers,
                    collected.structural_schemas,
                    collected.event_schemas,
                ),
            ):
                ready = facade.prepare_expert_replay_begin(replay)
            self.assertEqual(ready.manifest, manifest)
            facade.abort_expert_replay_construction(replay)
            replay = None
        finally:
            if replay is not None:
                state = store_module._REPLAYS.get(replay)
                if state is not None and not state.get("closed"):
                    facade.abort_expert_replay_construction(replay)
            if runtime is not None and not object.__getattribute__(
                runtime,
                "_closed",
            ):
                runtime.close_clean("operator_stop")
            if expert_writer is not None:
                state = store_module._WRITERS.get(expert_writer)
                if state is not None and state.state not in {
                    "closed",
                    "poisoned",
                }:
                    facade.abort_expert_writer(expert_writer)
            if coordinator is not None:
                coordinator.close()
            adapter_patch.stop()
            phase1_fixture.tearDown()


class ExpertStoreStaticInvariantTests(unittest.TestCase):
    def test_task8_candidate_and_account_lock_inventories_are_exact_and_disjoint(
        self,
    ) -> None:
        expected_sportradar_resources = (
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
        expected_kalshi = (
            "inci_tennis_adapters/kalshi_candidate.py",
            (
                "inci_tennis_adapters/schemas/"
                "kalshi-orderbook-snapshot-synthetic-candidate-v1.schema.json"
            ),
            (
                "inci_tennis_adapters/schemas/"
                "kalshi-orderbook-delta-synthetic-candidate-v1.schema.json"
            ),
            (
                "inci_tennis_adapters/schemas/"
                "kalshi-market-lifecycle-synthetic-candidate-v1.schema.json"
            ),
            (
                "inci_tennis_adapters/schemas/"
                "kalshi-public-trade-synthetic-candidate-v1.schema.json"
            ),
        )
        self.assertEqual(
            store_module._KALSHI_CANDIDATE_ADAPTER_INVENTORY,
            expected_kalshi,
        )
        self.assertEqual(
            store_module._KALSHI_CANDIDATE_SCHEMA_INVENTORY,
            expected_kalshi[1:],
        )
        self.assertEqual(
            store_module._ADAPTER_INVENTORY[-5:],
            expected_kalshi,
        )
        self.assertEqual(
            store_module._IO_INVENTORY[-1],
            "inci_tennis_io/account_lock.py",
        )
        self.assertEqual(
            store_module._CANDIDATE_SCHEMA_INVENTORY,
            expected_sportradar_resources,
        )
        self.assertEqual(
            len(store_module._ADAPTER_INVENTORY),
            len(set(store_module._ADAPTER_INVENTORY)),
        )
        source_inventory = store_module._SOURCE_PACKAGE_INVENTORIES[
            "inci_tennis_adapters"
        ]
        self.assertEqual(len(source_inventory), len(set(source_inventory)))

    def test_prepare_identity_reads_use_snapshot_io_gates_and_pread_takes_full_snapshot(
        self,
    ) -> None:
        tree = ast.parse(
            inspect.getsource(
                store_module.prepare_expert_replay_begin
            )
        )
        expected_counts = {
            "_guarded_phase1_evidence_file_identities": 2,
            "_guarded_expert_companion_file_identities": 1,
        }
        observed_counts = {name: 0 for name in expected_counts}
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Name)
                or node.func.id not in expected_counts
            ):
                continue
            observed_counts[node.func.id] += 1
            keywords = {
                keyword.arg: keyword.value
                for keyword in node.keywords
                if keyword.arg is not None
            }
            self.assertIn("gate", keywords)
            self.assertIn("byte_gate", keywords)
            for keyword in ("gate", "byte_gate"):
                self.assertIn(
                    "_require_prepare_replay_snapshot_io_gate",
                    {
                        candidate.id
                        for candidate in ast.walk(keywords[keyword])
                        if isinstance(candidate, ast.Name)
                    },
                )
        self.assertEqual(observed_counts, expected_counts)
        pread_tree = ast.parse(
            inspect.getsource(store_module._prepare_replay_pread)
        )
        pread_calls = {
            node.func.id
            for node in ast.walk(pread_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        self.assertIn(
            "_take_prepare_replay_full_integrity_snapshot",
            pread_calls,
        )
        self.assertIn("_gated_pread_exact", pread_calls)

    def test_store_uses_opaque_root_validation_and_descriptor_relative_environment_inventory(
        self,
    ) -> None:
        root_source = inspect.getsource(store_module._require_root)
        acquire_source = inspect.getsource(
            store_module.acquire_expert_journal_root
        )
        environment_source = inspect.getsource(
            store_module._installed_environment
        )
        self.assertNotIn('"_generation"', root_source)
        self.assertNotIn('"_generation"', acquire_source)
        self.assertIn("_phase1_sample_wall_ns", root_source)
        self.assertNotIn("Path(", environment_source)
        self.assertNotIn(".resolve(", environment_source)
        self.assertIn("_read_source_file", environment_source)
        self.assertIn(
            "source_root_fd",
            inspect.getsource(store_module._read_source_file),
        )

    def test_every_ordinary_and_emergency_terminal_seam_reenters_identity_gate(
        self,
    ) -> None:
        expected_counts = {
            "issue_expert_terminal_permit": 1,
            "append_expert_terminal": 2,
            "issue_expert_emergency_append_permit": 1,
            "append_expert_emergency_group_and_terminal": 3,
        }
        for function_name, expected in expected_counts.items():
            with self.subTest(function=function_name):
                tree = ast.parse(
                    inspect.getsource(
                        getattr(store_module, function_name)
                    )
                )
                calls = sum(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_terminal_gate"
                    for node in ast.walk(tree)
                )
                self.assertEqual(calls, expected)
        gate_source = inspect.getsource(store_module._terminal_gate)
        self.assertIn("_validate_tail_static_identities", gate_source)

    def test_static_limits_marker_fields_writer_states_and_facade_inventory_equal_the_rulings(
        self,
    ) -> None:
        self.assertEqual(store_module.EXPERT_MIN_FREE_BYTES, 67_108_864)
        self.assertEqual(
            store_module.EXPERT_EMERGENCY_RESERVE_BYTES,
            17_825_868,
        )
        self.assertEqual(
            store_module.EXPERT_MARKER_FIELDS,
            (
                "schema_version",
                "session_id",
                "journal_basename",
                "reserve_basename",
                "expert_manifest_sha256",
                "evidence_session_manifest_sha256",
                "evidence_session_start_record_sha256",
                "provider_request_binding_sha256",
                "retention_binding_sha256",
                "retention_delete_by_ns",
                "created_at_ns",
            ),
        )

    def test_marker_parser_rejects_complete_noncanonical_and_semantic_mutation_matrix(
        self,
    ) -> None:
        canonical = (
            b'{"created_at_ns":1,'
            b'"evidence_session_manifest_sha256":"' + b"a" * 64 + b'",'
            b'"evidence_session_start_record_sha256":"' + b"b" * 64 + b'",'
            b'"expert_manifest_sha256":"' + b"c" * 64 + b'",'
            b'"journal_basename":"11111111-1111-4111-8111-111111111111.'
            b'expert-journal-v1",'
            b'"provider_request_binding_sha256":"' + b"d" * 64 + b'",'
            b'"reserve_basename":"11111111-1111-4111-8111-111111111111.'
            b'expert-reserve-v1",'
            b'"retention_binding_sha256":"' + b"e" * 64 + b'",'
            b'"retention_delete_by_ns":2,'
            b'"schema_version":1,'
            b'"session_id":"11111111-1111-4111-8111-111111111111"}'
        )
        parsed = store_module._decode_expert_marker(canonical)
        self.assertEqual(parsed["created_at_ns"], 1)
        mutations = (
            b" " + canonical,
            canonical + b"\n",
            b"\xef\xbb\xbf" + canonical,
            canonical.replace(b'"created_at_ns":1', b'"created_at_ns":1.0'),
            canonical.replace(
                b'"schema_version":1',
                b'"unknown":1,"schema_version":1',
            ),
            canonical.replace(
                b'{"created_at_ns":1,',
                b'{"created_at_ns":1,"created_at_ns":1,',
            ),
            canonical.replace(b'"created_at_ns":1,', b""),
            canonical.replace(b'"schema_version":1', b'"schema_version":true'),
            canonical.replace(b'"created_at_ns":1', b'"created_at_ns":-1'),
            canonical.replace(
                b'"retention_delete_by_ns":2',
                b'"retention_delete_by_ns":1',
            ),
            canonical.replace(b'"' + b"a" * 64 + b'"', b'"' + b"A" * 64 + b'"'),
            canonical.replace(b'"' + b"b" * 64 + b'"', b'"short"'),
            canonical.replace(
                b".expert-journal-v1",
                b".wrong-journal",
            ),
            canonical.replace(
                b'"session_id":"11111111',
                b'"session_id":"bad/path',
            ),
            canonical.replace(b'":1,', b'": 1,', 1),
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated[:20]):
                with self.assertRaises(ValueError):
                    store_module._decode_expert_marker(mutated)

    def test_capacity_equality_is_a_no_permit_no_write_signal(self) -> None:
        required = 67_108_864 + 1_048_652 + 4096
        fake = mock.Mock(f_bavail=required, f_frsize=1)
        with self.assertRaises(ExpertPrewriteCapacityError) as raised:
            store_module._validate_available_capacity(
                fake,
                candidate_group_frame_bytes=4096,
            )
        self.assertEqual(raised.exception.requested_bytes, 4096)
        self.assertEqual(raised.exception.available_bytes, required)
        self.assertEqual(
            raised.exception.emergency_reserve_bytes,
            store_module.EXPERT_EMERGENCY_RESERVE_BYTES,
        )
        self.assertEqual(
            raised.exception.args,
            ("expert_prewrite_capacity_low",),
        )
        fake.f_bavail = required + 1
        self.assertIsNone(
            store_module._validate_available_capacity(
                fake,
                candidate_group_frame_bytes=4096,
            )
        )

    def test_store_has_no_timing_sleep_unruled_path_repair_or_descriptor_leak_on_any_exit(
        self,
    ) -> None:
        source = Path(store_module.__file__).read_text("utf-8")
        tree = ast.parse(source)
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        self.assertNotIn("sleep", calls)
        self.assertTrue(
            {"truncate", "replace", "renames"}.isdisjoint(calls)
        )


if __name__ == "__main__":
    unittest.main()
