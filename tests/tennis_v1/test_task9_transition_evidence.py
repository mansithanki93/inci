from __future__ import annotations

import ast
import base64
import csv
import hashlib
import inspect
import importlib.metadata
import importlib.util
import errno
import grp
import json
import os
import pwd
import queue
import re
import socket
import stat
import subprocess
from pathlib import Path
import sys
import sysconfig
import tempfile
import threading
import time
import unittest
import weakref
from dataclasses import fields, replace
from unittest import mock

# TASK9_ROUND19_UNITTEST_PREIMPORT_GUARD_BEGIN_V1
_TASK9_ROUND19_CHILD_PROBE_KIND_V1 = os.environ.get(
    "INCI_TASK9_BOOTSTRAP_PATH_PROBE"
)
_TASK9_ROUND19_CHILD_GUARD_ACTIVE_V1 = (
    _TASK9_ROUND19_CHILD_PROBE_KIND_V1 == "UNITTEST_MODULE"
)
if _TASK9_ROUND19_CHILD_GUARD_ACTIVE_V1:
    _TASK9_ROUND19_EXCLUDED_BASE_V1 = (
        "/opt/homebrew/Cellar/python@3.14/3.14.5/lib/python3.14/site-packages"
    )

    def _task9_round19_child_forbidden_v1(*args, **kwargs):
        raise RuntimeError("task9_bootstrap_capability_used")

    def _task9_round19_child_audit_v1(event, args):
        if event.startswith("socket."):
            raise RuntimeError("task9_bootstrap_capability_used")
        if event in ("open", "os.open") and args:
            candidate = args[0]
            if isinstance(candidate, (str, bytes, os.PathLike)):
                rendered = os.fsdecode(candidate)
                if rendered == _TASK9_ROUND19_EXCLUDED_BASE_V1 or rendered.startswith(
                    _TASK9_ROUND19_EXCLUDED_BASE_V1 + "/"
                ):
                    raise RuntimeError("task9_bootstrap_capability_used")

    sys.addaudithook(_task9_round19_child_audit_v1)
    socket.socket = _task9_round19_child_forbidden_v1
    socket.getaddrinfo = _task9_round19_child_forbidden_v1

import tools.task9_transition_evidence as transition_evidence
from tools.task9_transition_evidence import (
    TASK9_EVIDENCE_DECODER_TABLE_V2,
    TASK9_EVIDENCE_STAGE_ROWS_V1,
    TASK9_NO_REPLACE_PROMOTION_POLICY_V2,
    TASK9_STAGE_OWNED_PATHS_V1,
    TASK9_TRANSIENT_WRITE_PATHS_V1,
    Task9EvidenceStageIdV1,
    Task9EvidencePathSnapshotV1,
    Task9ProceduralAssignmentWriteReceiptV1,
    Task9ChainAcceptanceReceiptV1,
    Task9FunctionalWaveReviewV1,
    Task9FunctionalWaveIdV1,
    Task9ProceduralRoleBindingV1,
    Task9ProceduralWorkflowAssignmentEvidenceV1,
    Task9StageOutputKindV1,
    Task9TransitionPathV1,
    Task9TransitionEvidenceError,
    _Task9LinkCallOutcomeV1,
    _call_task9_link_noreplace_v1,
    task9_evidence_decoder_table_preimage_bytes_v2,
    task9_evidence_decoder_table_rows_json_bytes_v2,
    task9_evidence_decoder_table_sha256_v2,
    parse_task9_functional_wave_review_v1,
    parse_task9_procedural_assignment_write_receipt_v1,
    parse_task9_chain_acceptance_receipt_v1,
    validate_task9_functional_wave_review_structure_v1,
    validate_task9_transition_path_structure_v1,
    validate_task9_evidence_decoder_table_v2,
    validate_task9_evidence_path_snapshot_structure_v1,
    validate_task9_evidence_root_snapshot_structure_v1,
    issue_task9_evidence_root_snapshot_v1,
    close_task9_evidence_root_snapshot_v1,
    read_task9_procedural_assignment_write_receipt_from_snapshot_v1,
    issue_task9_procedural_workflow_assignment_evidence_v1,
    issue_task9_procedural_assignment_reservation_v1,
    issue_task9_procedural_assignment_recovery_reservation_v1,
    recover_task9_procedural_assignment_write_receipt_v1,
    write_task9_functional_wave_review_v1,
)

if _TASK9_ROUND19_CHILD_GUARD_ACTIVE_V1:
    import requests.sessions as _task9_round19_requests_sessions_v1
    import kalshi_client as _task9_round19_kalshi_client_v1
    import executor as _task9_round19_executor_v1

    _task9_round19_requests_sessions_v1.Session.request = (
        _task9_round19_child_forbidden_v1
    )
    _task9_round19_kalshi_client_v1.KalshiClient._request = (
        _task9_round19_child_forbidden_v1
    )
    _task9_round19_executor_v1.Executor.execute = (
        _task9_round19_child_forbidden_v1
    )
# TASK9_ROUND19_UNITTEST_PREIMPORT_GUARD_END_V1

# TASK9_ROUND19_COMMAND_BOOTSTRAP_BEGIN_V1
class Round19CommandEvidenceBootstrapTests(unittest.TestCase):
    """Final pre-review path-closure bootstrap contract (exactly six methods)."""

    _LAUNCHER = "/Users/mthanki/.venvs/inci-expert-py314/bin/python"
    _REPOSITORY = "/Users/mthanki/Downloads/inci-tennis-v1"
    _STDLIB = (
        "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/"
        "Python.framework/Versions/3.14/lib/python3.14"
    )
    _VENV_PURELIB = (
        "/Users/mthanki/.venvs/inci-expert-py314/lib/python3.14/site-packages"
    )
    _BASE_PURELIB = (
        "/opt/homebrew/Cellar/python@3.14/3.14.5/lib/python3.14/site-packages"
    )
    _FRAMEWORK_BINARY = (
        "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/"
        "Python.framework/Versions/3.14/Python"
    )

    @staticmethod
    def _fd_count():
        return len(os.listdir("/dev/fd"))

    @staticmethod
    def _stat9(value):
        return (
            value.st_dev, value.st_ino, value.st_mode, value.st_uid,
            value.st_gid, value.st_nlink, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns,
        )

    @classmethod
    def _descriptor_bytes(cls, path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            chunks = []
            while True:
                chunk = os.read(fd, 1_048_576)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        if cls._stat9(before) != cls._stat9(after):
            raise AssertionError(f"descriptor drift while reading {path}")
        return before, b"".join(chunks)

    @classmethod
    def _entries_sha(cls, path):
        fd = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            before = os.fstat(fd)
            rows = []
            for name in sorted(os.listdir(fd)):
                value = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(value.st_mode):
                    kind = "DIRECTORY"
                elif stat.S_ISREG(value.st_mode):
                    kind = "REGULAR"
                elif stat.S_ISLNK(value.st_mode):
                    kind = "SYMLINK"
                else:
                    kind = "SPECIAL"
                rows.append((name, kind))
            after = os.fstat(fd)
        finally:
            os.close(fd)
        if cls._stat9(before) != cls._stat9(after):
            raise AssertionError(f"directory drift while reading {path}")
        payload = json.dumps(
            rows, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        return cls._stat9(after), hashlib.sha256(
            b"INCI-TASK-9-DIRECTORY-ENTRIES-V1\0" + payload
        ).hexdigest()

    @classmethod
    def _descriptor_directory_identity(cls, path):
        current = os.open(
            "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        try:
            for component in tuple(item for item in path.split("/") if item):
                next_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=current,
                )
                os.close(current)
                current = next_fd
            before = os.fstat(current)
            os.listdir(current)
            after = os.fstat(current)
            cls_identity = cls._stat9(before)
            if cls_identity != cls._stat9(after):
                raise AssertionError(f"directory drift while reading {path}")
            return cls_identity
        finally:
            os.close(current)

    @staticmethod
    def _projection(value, *, exclude=()):
        if hasattr(type(value), "__dataclass_fields__"):
            result = {}
            for field in fields(type(value)):
                if field.name in exclude:
                    continue
                cell = getattr(value, field.name)
                if field.name == "raw_bytes":
                    result["raw_bytes_hex"] = cell.hex()
                else:
                    result[field.name] = (
                        Round19CommandEvidenceBootstrapTests._projection(cell)
                    )
            return result
        if isinstance(value, tuple):
            return [
                Round19CommandEvidenceBootstrapTests._projection(item)
                for item in value
            ]
        if isinstance(value, list):
            return [
                Round19CommandEvidenceBootstrapTests._projection(item)
                for item in value
            ]
        if isinstance(value, dict):
            return {
                key: Round19CommandEvidenceBootstrapTests._projection(item)
                for key, item in value.items()
                if key not in exclude
            }
        return value

    @classmethod
    def _canonical(cls, value):
        return json.dumps(
            cls._projection(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")

    @classmethod
    def _domain_sha(cls, domain, value):
        return hashlib.sha256(
            domain.encode("ascii") + b"\0" + cls._canonical(value)
        ).hexdigest()

    def _require(self, module, name):
        value = getattr(module, name, None)
        self.assertIsNotNone(
            value, f"semantic bootstrap omission: {name} is absent"
        )
        return value

    def _origin_module(self):
        temporary = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name, "origin-repository")
        module_path = root / "tools/task9_transition_evidence.py"
        module_path.parent.mkdir(parents=True, mode=0o700)
        module_path.write_bytes(Path(transition_evidence.__file__).read_bytes())
        module_path.chmod(0o600)
        pyproject = root / "pyproject.toml"
        pyproject.write_bytes(
            Path(transition_evidence.__file__).parents[1]
            .joinpath("pyproject.toml").read_bytes()
        )
        pyproject.chmod(0o600)
        root.chmod(0o700)
        spec = importlib.util.spec_from_file_location(
            f"task9_round19_origin_{id(root)}", module_path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        return module, root, module_path

    @classmethod
    def _endpoint_policy_rows(cls):
        stdlib_parent = str(Path(cls._STDLIB).parent)
        framework_version = str(Path(cls._FRAMEWORK_BINARY).parent)
        return (
            ("ABSENT_STDLIB_ZIP", "ABSENT_PARENT",
             f"{stdlib_parent}/python314.zip", stdlib_parent),
            ("COMMAND_CWD", "DIRECTORY_PARENT", cls._REPOSITORY,
             "/Users/mthanki/Downloads"),
            ("LAUNCHER_0_LINK", "LINK_PARENT", cls._LAUNCHER,
             "/Users/mthanki/.venvs/inci-expert-py314/bin"),
            ("LAUNCHER_0_TARGET", "TARGET_PARENT",
             "/Users/mthanki/.venvs/inci-expert-py314/bin/python3.14",
             "/Users/mthanki/.venvs/inci-expert-py314/bin"),
            ("LAUNCHER_1_LINK", "LINK_PARENT",
             "/Users/mthanki/.venvs/inci-expert-py314/bin/python3.14",
             "/Users/mthanki/.venvs/inci-expert-py314/bin"),
            ("LAUNCHER_1_TARGET", "TARGET_PARENT",
             "/opt/homebrew/opt/python@3.14/bin/python3.14",
             "/opt/homebrew/Cellar/python@3.14/3.14.5/bin"),
            ("LAUNCHER_2_LINK", "LINK_PARENT",
             "/opt/homebrew/opt/python@3.14", "/opt/homebrew/opt"),
            ("LAUNCHER_2_TARGET", "TARGET_PARENT",
             "/opt/homebrew/Cellar/python@3.14/3.14.5/bin/python3.14",
             "/opt/homebrew/Cellar/python@3.14/3.14.5/bin"),
            ("LAUNCHER_3_LINK", "LINK_PARENT",
             "/opt/homebrew/Cellar/python@3.14/3.14.5/bin/python3.14",
             "/opt/homebrew/Cellar/python@3.14/3.14.5/bin"),
            ("LAUNCHER_3_TARGET", "TARGET_PARENT",
             f"{framework_version}/bin/python3.14", f"{framework_version}/bin"),
            ("PURELIB_ROOT", "DIRECTORY_PARENT", cls._VENV_PURELIB,
             str(Path(cls._VENV_PURELIB).parent)),
            ("PYVENV_CONFIG", "FILE_PARENT",
             "/Users/mthanki/.venvs/inci-expert-py314/pyvenv.cfg",
             "/Users/mthanki/.venvs/inci-expert-py314"),
            ("RESOLVED_STDLIB", "DIRECTORY_PARENT", cls._STDLIB,
             stdlib_parent),
            ("RUNTIME_A_LINK", "LINK_PARENT",
             f"{cls._STDLIB}/config-3.14-darwin/libpython3.14.a",
             f"{cls._STDLIB}/config-3.14-darwin"),
            ("RUNTIME_A_TARGET", "TARGET_PARENT", cls._FRAMEWORK_BINARY,
             framework_version),
            ("RUNTIME_DYLIB_LINK", "LINK_PARENT",
             f"{cls._STDLIB}/config-3.14-darwin/libpython3.14.dylib",
             f"{cls._STDLIB}/config-3.14-darwin"),
            ("RUNTIME_DYLIB_TARGET", "TARGET_PARENT", cls._FRAMEWORK_BINARY,
             framework_version),
            ("RUNTIME_SITE_LINK", "LINK_PARENT",
             f"{cls._STDLIB}/site-packages", cls._STDLIB),
            ("RUNTIME_SITE_TARGET", "TARGET_PARENT", cls._BASE_PURELIB,
             str(Path(cls._BASE_PURELIB).parent)),
            ("STDLIB_DYNLOAD", "DIRECTORY_PARENT",
             f"{cls._STDLIB}/lib-dynload", cls._STDLIB),
            ("STDLIB_ROOT_0_LINK", "LINK_PARENT",
             "/opt/homebrew/opt/python@3.14", "/opt/homebrew/opt"),
            ("STDLIB_ROOT_0_TARGET", "TARGET_PARENT", cls._STDLIB,
             stdlib_parent),
            ("VENV_PURELIB", "DIRECTORY_PARENT", cls._VENV_PURELIB,
             str(Path(cls._VENV_PURELIB).parent)),
        )

    @classmethod
    def _component_policy_rows(cls):
        rows = []
        for endpoint_key, _role, _endpoint, parent in cls._endpoint_policy_rows():
            components = [item for item in parent.split("/") if item]
            absolute = "/"
            rows.append((endpoint_key, 0, absolute))
            for index, component in enumerate(components, 1):
                absolute = f"/{component}" if absolute == "/" else f"{absolute}/{component}"
                rows.append((endpoint_key, index, absolute))
        return tuple(rows)

    @classmethod
    def _policy_projections(cls):
        path_hops = (
            ("LAUNCHER", 0, cls._LAUNCHER, "python3.14",
             "/Users/mthanki/.venvs/inci-expert-py314/bin/python3.14"),
            ("LAUNCHER", 1,
             "/Users/mthanki/.venvs/inci-expert-py314/bin/python3.14",
             "/opt/homebrew/opt/python@3.14/bin/python3.14",
             "/opt/homebrew/opt/python@3.14/bin/python3.14"),
            ("LAUNCHER", 2, "/opt/homebrew/opt/python@3.14",
             "../Cellar/python@3.14/3.14.5",
             "/opt/homebrew/Cellar/python@3.14/3.14.5/bin/python3.14"),
            ("LAUNCHER", 3,
             "/opt/homebrew/Cellar/python@3.14/3.14.5/bin/python3.14",
             "../Frameworks/Python.framework/Versions/3.14/bin/python3.14",
             f"{Path(cls._FRAMEWORK_BINARY).parent}/bin/python3.14"),
            ("STDLIB_ROOT", 0, "/opt/homebrew/opt/python@3.14",
             "../Cellar/python@3.14/3.14.5", cls._STDLIB),
        )
        config_rows = (
            ("home", "/opt/homebrew/opt/python@3.14/bin"),
            ("include-system-site-packages", "false"),
            ("version", "3.14.5"),
            ("executable",
             f"{Path(cls._FRAMEWORK_BINARY).parent}/bin/python3.14"),
            ("command",
             "/opt/homebrew/opt/python@3.14/bin/python3.14 -m venv "
             "/Users/mthanki/.venvs/inci-expert-py314"),
        )
        search_rows = (
            (0, cls._REPOSITORY, "COMMAND_CWD", "PRESENT"),
            (1, f"{Path(cls._STDLIB).parent}/python314.zip",
             "ABSENT_STDLIB_ZIP", "ABSENT"),
            (2, cls._STDLIB, "RESOLVED_STDLIB", "PRESENT"),
            (3, f"{cls._STDLIB}/lib-dynload", "STDLIB_DYNLOAD", "PRESENT"),
            (4, cls._VENV_PURELIB, "VENV_PURELIB", "PRESENT"),
        )
        fixed = (
            ("INCI_DEMO_DISABLED", "1"), ("INCI_LIVE_DISABLED", "1"),
            ("INCI_NETWORK_DISABLED", "1"),
            ("INCI_ORDER_EXECUTION_DISABLED", "1"), ("LANG", "C"),
            ("LC_ALL", "C"), ("PYTHONDONTWRITEBYTECODE", "1"),
            ("PYTHONHASHSEED", "0"), ("PYTHONIOENCODING", "utf-8:strict"),
            ("PYTHONNOUSERSITE", "1"), ("PYTHONUNBUFFERED", "1"),
            ("PYTHONUTF8", "1"), ("TMPDIR", "/tmp"), ("TZ", "UTC"),
        )
        return (
            (
                "TASK9_INTERPRETER_PATH_CLOSURE_ALLOWANCE_V1",
                "INCI-TASK-9-INTERPRETER-PATH-CLOSURE-ALLOWANCE-V1",
                1692,
                "240ba2f798b8d2b0581a33f8fbbbea43bfccc6bd6c237271392a23b84260bcaf",
                "137d6b7d31f5e992b307a2cc4e6f32c5f2e4ccbf3052434006ddb62a6b6be6d2",
                {
                    "schema_version": 1,
                    "allowance_id": "TASK9_INTERPRETER_PATH_CLOSURE_ALLOWANCE_V1",
                    "max_symlink_hops": 8,
                    "path_hop_rows": path_hops,
                    "zero_hop_scopes": (("PURELIB_ROOT", cls._VENV_PURELIB),),
                    "runtime_symlink_rows": (
                        ("config-3.14-darwin/libpython3.14.a",
                         "../../../Python", "REGULAR",
                         "CPYTHON_FRAMEWORK_BINARY", "DATA"),
                        ("config-3.14-darwin/libpython3.14.dylib",
                         "../../../Python", "REGULAR",
                         "CPYTHON_FRAMEWORK_BINARY", "EXTENSION"),
                        ("site-packages",
                         "../../../../../../lib/python3.14/site-packages",
                         "DIRECTORY", "BASE_PURELIB_ROOT", None),
                    ),
                    "regular_target_rows": (
                        ("CPYTHON_FRAMEWORK_BINARY", cls._FRAMEWORK_BINARY),
                    ),
                    "excluded_directory_target_rows": (
                        ("BASE_PURELIB_ROOT", cls._BASE_PURELIB),
                    ),
                },
            ),
            (
                "TASK9_PYVENV_CONFIG_POLICY_V1",
                "INCI-TASK-9-PYVENV-CONFIG-POLICY-V1",
                676,
                "ed97574214040ad716e05a3e49624c97f98fede0d9536c2f95ae64927002989e",
                "024b655a2538071f6ad7d351724e3282a5e64716bc2fb176c2eb12cbbd7f7b60",
                {
                    "schema_version": 1,
                    "policy_id": "TASK9_PYVENV_CONFIG_POLICY_V1",
                    "path": "/Users/mthanki/.venvs/inci-expert-py314/pyvenv.cfg",
                    "content_size": 308,
                    "content_sha256": "5b4e9e15d664eaf4b663b4849b61242aecb485beb3091b2595b545f933d88095",
                    "encoding": "STRICT_UTF8", "line_ending": "LF_ONLY",
                    "terminal_lf": True, "parsed_rows": config_rows,
                    "include_system_site_packages": False,
                },
            ),
            (
                "TASK9_SANITIZED_IMPORT_SEARCH_PATH_POLICY_V1",
                "INCI-TASK-9-IMPORT-SEARCH-PATH-POLICY-V1",
                894,
                "bc7e10e99cc6476a566cb769526eb757b28102a0a8882d2b2c32cf8a0448f910",
                "4fc73c4632f1af17183e3d164bdefad21955eabd4b7c248e797d28242f466b79",
                {
                    "schema_version": 1,
                    "policy_id": "TASK9_SANITIZED_IMPORT_SEARCH_PATH_POLICY_V1",
                    "exact_row_count": 5, "rows": search_rows,
                    "excluded_base_purelib_path": cls._BASE_PURELIB,
                    "excluded_base_purelib_relation":
                        "DISTINCT_FROM_VENV_PURELIB",
                    "excluded_base_purelib_search_state": "ABSENT",
                },
            ),
            (
                "TASK9_PATH_ENDPOINT_PARENT_ALLOWANCE_V1",
                "INCI-TASK-9-PATH-ENDPOINT-PARENT-ALLOWANCE-V1",
                4235,
                "e1fb11fdc62b30379563aeb42a655069e5d6e92ab4771641adcf1053eaae1c5f",
                "d3606e079f9b28a376b686cf7cb9bcc0fac20953ca26ab4f05ab00fcb1abf452",
                {
                    "schema_version": 1,
                    "policy_id": "TASK9_PATH_ENDPOINT_PARENT_ALLOWANCE_V1",
                    "exact_row_count": 23,
                    "rows": cls._endpoint_policy_rows(),
                },
            ),
            (
                "TASK9_PATH_COMPONENT_ALLOWANCE_V1",
                "INCI-TASK-9-PATH-COMPONENT-ALLOWANCE-V1",
                11836,
                "666ab452af207440e9d80ac8435bb5346da34b0b3edb0efe45752d74d558d56c",
                "40398598a215c9a31fbda2bf12e23814c4eefdc8fe60100860aebd0a8763b6af",
                {
                    "schema_version": 1,
                    "policy_id": "TASK9_PATH_COMPONENT_ALLOWANCE_V1",
                    "exact_row_count": 192,
                    "rows": cls._component_policy_rows(),
                },
            ),
            (
                "TASK9_BOOTSTRAP_PROBE_ENVIRONMENT_POLICY_V1",
                "INCI-TASK-9-BOOTSTRAP-PROBE-ENVIRONMENT-POLICY-V1",
                808,
                "0704dbf931e418f469a95a3e65977e5f144793532894e526e36f1270cfe78263",
                "97ca402820fcef9cd516b9b1814757f19cee110ee8d362bedcdc242f2f4bfdbb",
                {
                    "schema_version": 1,
                    "policy_id": "TASK9_BOOTSTRAP_PROBE_ENVIRONMENT_POLICY_V1",
                    "inherit_parent_environment": False,
                    "probe_kinds": ("UNITTEST_MODULE", "FROZEN_V6_SCRIPT"),
                    "probe_row_name": "INCI_TASK9_BOOTSTRAP_PATH_PROBE",
                    "fixed_rows": fixed,
                    "dynamic_rows": (
                        ("HOME",
                         "/tmp/inci-task9-home-bootstrap-probe-"
                         "<probe-kind-lower>-<positive-allocation-coordinate>"),
                        ("PYTHONPYCACHEPREFIX",
                         "/tmp/inci-task9-pycache-bootstrap-probe-"
                         "<probe-kind-lower>-<positive-allocation-coordinate>"),
                    ),
                },
            ),
            (
                "TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_V1",
                "INCI-TASK-9-TRUSTED-HOMEBREW-COMPONENT-MODE-POLICY-V1",
                744,
                "8731428ad93bab8c9c190c4e61f5a41b003cec33fc472030145d7733ef9f1962",
                "a0d56a544d01190f251b6253c83ca25aeb8c5765871435d2a1ca363ea0ea51d6",
                {
                    "schema_version": 1,
                    "policy_id":
                        "TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_V1",
                    "default_directory_rule":
                        "OWNER_UID_ZERO_OR_EFFECTIVE_UID_AND_NO_GROUP_OR_OTHER_WRITE",
                    "effective_group_membership_source":
                        "MACOS_OPENDIRECTORY_GETGROUPLIST",
                    "group_id_normalization":
                        "SIGNED_INT32_INPUT_UINT32_OUTPUT",
                    "exact_group_member_count": 2,
                    "exception_rows": (
                        ("/opt/homebrew/Cellar", "EFFECTIVE_UID", 80, "0775"),
                        ("/opt/homebrew/opt", "EFFECTIVE_UID", 80, "0775"),
                    ),
                    "required_group_member_role_uids":
                        ("ROOT_UID", "EFFECTIVE_UID"),
                    "bind_resolved_member_names": True,
                    "require_before_after_equality": True,
                    "require_complete_passwd_universe": True,
                    "require_distinct_role_uids": True,
                    "require_empty_primary_gid_member_rows": True,
                    "require_zero_membership_query_errors": True,
                },
            ),
        )

    def _emit_child_path_probe_if_requested(self):
        if os.environ.get("INCI_TASK9_BOOTSTRAP_PATH_PROBE") != "UNITTEST_MODULE":
            return False
        expected = tuple(
            row[1] for row in self._policy_projections()[2][5]["rows"]
        )
        observed = tuple(os.path.abspath(value or os.getcwd()) for value in sys.path)
        self.assertEqual(observed, expected)
        states = tuple(
            row[3] for row in self._policy_projections()[2][5]["rows"]
        )
        roles = tuple(
            row[2] for row in self._policy_projections()[2][5]["rows"]
        )
        rows = []
        for index, path in enumerate(expected):
            present = states[index] == "PRESENT"
            self.assertEqual(os.path.lexists(path), present)
            identity = None
            if present:
                identity = self._descriptor_directory_identity(path)
            rows.append({
                "index": index, "absolute_path": path, "role": roles[index],
                "state": states[index], "path_stat_identity": identity,
            })
        projection = {
            "schema_version": 1,
            "policy_sha256":
                "4fc73c4632f1af17183e3d164bdefad21955eabd4b7c248e797d28242f466b79",
            "rows": tuple(rows),
        }
        payload_sha = self._domain_sha(
            "INCI-TASK-9-IMPORT-SEARCH-ROW-PROJECTION-V1", projection
        )
        print(f"INCI_TASK9_CHILD_PATH_V1 UNITTEST_MODULE {payload_sha}")
        return True

    def _assert_policy_pins(self, module):
        expected_rows = self._policy_projections()
        values = tuple(self._require(module, row[0]) for row in expected_rows)
        for value, (
            name, domain, byte_count, raw_sha, domain_sha, expected
        ) in zip(values, expected_rows):
            with self.subTest(policy=name):
                actual = self._projection(value, exclude=("policy_sha256", "allowance_sha256"))
                canonical = json.dumps(
                    actual, ensure_ascii=True, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                ).encode("ascii")
                self.assertEqual(actual, self._projection(expected))
                self.assertEqual(
                    (len(canonical), hashlib.sha256(canonical).hexdigest()),
                    (byte_count, raw_sha),
                )
                digest_field = (
                    "allowance_sha256"
                    if hasattr(value, "allowance_sha256") else "policy_sha256"
                )
                self.assertEqual(getattr(value, digest_field), domain_sha)
                self.assertEqual(
                    hashlib.sha256(
                        domain.encode("ascii") + b"\0" + canonical
                    ).hexdigest(),
                    domain_sha,
                )

    @staticmethod
    def _round5_witness_projection():
        return {
            "schema_version": 1,
            "witness_id":
                "TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_V1",
            "passwd_raw_row_count": 267,
            "passwd_raw_canonical_bytes": 21_762,
            "passwd_raw_rows_sha256":
                "f6fe0a487223c6f2744b996f82aafc3cec775321285ad13e07388086e728977e",
            "passwd_unique_row_count": 135,
            "passwd_unique_canonical_bytes": 10_994,
            "passwd_unique_rows_sha256":
                "1473637d6258b4652e1a1fcecf9e779c9d5f3ab34de83c0f41a31407edac99d1",
            "effective_group_access_row_count": 135,
            "effective_group_access_canonical_bytes": 14_217,
            "effective_group_access_rows_sha256":
                "30f8ea202ae4dc31a5e96a2e8cdbb1d25dd455db7629e5259168671c28861381",
        }

    @classmethod
    def _round5_independent_gid(cls, value):
        if type(value) is not int or isinstance(value, bool):
            raise AssertionError(f"non-exact independent gid: {value!r}")
        native_u = 1 << 64
        if 0 <= value <= (1 << 32) - 1:
            return value
        if -(1 << 31) <= value < 0:
            return value + (1 << 32)
        if native_u - (1 << 31) <= value < native_u:
            return value - (native_u - (1 << 32))
        raise AssertionError(f"invalid independently observed gid: {value}")

    @classmethod
    def _round5_independent_host_projections(cls):
        canonical = cls._canonical
        raw_rows = tuple(
            sorted((tuple(row) for row in pwd.getpwall()), key=canonical)
        )
        unique_by_bytes = {}
        for row in raw_rows:
            unique_by_bytes.setdefault(canonical(row), row)
        unique_rows = tuple(unique_by_bytes[key] for key in sorted(unique_by_bytes))
        access_rows = []
        for row in unique_rows:
            canonical_gid = row[3]
            signed_base_gid = (
                canonical_gid
                if canonical_gid <= (1 << 31) - 1
                else canonical_gid - (1 << 32)
            )
            normalized = tuple(sorted({
                cls._round5_independent_gid(value)
                for value in os.getgrouplist(row[0], signed_base_gid)
            }))
            if canonical_gid not in normalized:
                raise AssertionError(
                    f"base gid omitted from independent access row: {row[0]}"
                )
            access_rows.append((row, normalized))
        result = []
        for rows in (raw_rows, unique_rows, tuple(access_rows)):
            payload = canonical(rows)
            result.append(
                (rows, len(payload), hashlib.sha256(payload).hexdigest())
            )
        return tuple(result)

    def _assert_round5_homebrew_positive(self):
        policy_type = self._require(
            transition_evidence, "Task9TrustedHomebrewComponentModePolicyV1"
        )
        self.assertEqual(
            tuple(field.name for field in fields(policy_type)),
            (
                "schema_version", "policy_id", "default_directory_rule",
                "effective_group_membership_source", "group_id_normalization",
                "exact_group_member_count", "exception_rows",
                "required_group_member_role_uids", "bind_resolved_member_names",
                "require_before_after_equality", "require_complete_passwd_universe",
                "require_distinct_role_uids", "require_empty_primary_gid_member_rows",
                "require_zero_membership_query_errors", "policy_sha256",
            ),
        )
        witness_type = self._require(
            transition_evidence,
            "Task9InstalledHostPasswdGroupAccessWitnessV1",
        )
        self.assertEqual(
            tuple(field.name for field in fields(witness_type)),
            (
                "schema_version", "witness_id", "passwd_raw_row_count",
                "passwd_raw_canonical_bytes", "passwd_raw_rows_sha256",
                "passwd_unique_row_count", "passwd_unique_canonical_bytes",
                "passwd_unique_rows_sha256",
                "effective_group_access_row_count",
                "effective_group_access_canonical_bytes",
                "effective_group_access_rows_sha256", "witness_sha256",
            ),
        )
        witness = self._require(
            transition_evidence,
            "TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_V1",
        )
        witness_projection = self._projection(
            witness, exclude=("witness_sha256",)
        )
        self.assertEqual(witness_projection, self._round5_witness_projection())
        witness_bytes = self._canonical(witness_projection)
        self.assertEqual(
            (len(witness_bytes), hashlib.sha256(witness_bytes).hexdigest()),
            (594,
             "a080ddcb439edf1c6c52209ff42751160e94614f31fb9cc41d20e87a3be97945"),
        )
        self.assertEqual(
            witness.witness_sha256,
            "8312e51848e7c7ebd477fe1d79f62c8a0c28f4a18031c77a60769b35f87fa519",
        )
        self.assertEqual(
            self._domain_sha(
                "INCI-TASK-9-INSTALLED-HOST-PASSWD-GROUP-ACCESS-WITNESS-V1",
                witness_projection,
            ),
            witness.witness_sha256,
        )
        first = self._round5_independent_host_projections()
        second = self._round5_independent_host_projections()
        self.assertEqual(first, second)
        self.assertEqual(
            tuple((len(rows), byte_count, digest)
                  for rows, byte_count, digest in first),
            (
                (267, 21_762,
                 "f6fe0a487223c6f2744b996f82aafc3cec775321285ad13e07388086e728977e"),
                (135, 10_994,
                 "1473637d6258b4652e1a1fcecf9e779c9d5f3ab34de83c0f41a31407edac99d1"),
                (135, 14_217,
                 "30f8ea202ae4dc31a5e96a2e8cdbb1d25dd455db7629e5259168671c28861381"),
            ),
        )
        capture = self._require(
            transition_evidence,
            "_task9_bootstrap_capture_trusted_homebrew_component_mode_evidence_v1",
        )
        self.assertEqual(tuple(inspect.signature(capture).parameters), ())
        evidence = capture()
        self.assertEqual(
            tuple(field.name for field in fields(type(evidence))),
            (
                "schema_version", "policy_sha256", "installed_host_witness",
                "passwd_raw_row_count", "passwd_raw_canonical_bytes",
                "passwd_raw_rows_sha256", "passwd_raw_rows",
                "passwd_unique_row_count", "passwd_unique_canonical_bytes",
                "passwd_unique_rows_sha256", "passwd_unique_rows",
                "passwd_name_conflict_rows", "passwd_uid_conflict_rows",
                "root_role_passwd_row", "effective_uid_role_passwd_row",
                "gid80_group_row", "gid80_member_resolution_rows",
                "primary_gid_member_rows", "effective_group_access_row_count",
                "effective_group_access_canonical_bytes",
                "effective_group_access_rows_sha256",
                "effective_group_access_rows", "membership_query_error_rows",
                "effective_gid80_member_rows", "builtin_identity_rows",
                "component_rows", "evidence_sha256",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(type(evidence.component_rows[0]))),
            ("path", "owner_role", "stat_identity", "entries_sha256"),
        )
        self.assertIs(evidence.installed_host_witness, witness)
        self.assertEqual(
            (
                evidence.passwd_raw_rows,
                evidence.passwd_unique_rows,
                evidence.effective_group_access_rows,
            ),
            (first[0][0], first[1][0], first[2][0]),
        )
        self.assertEqual(
            (
                evidence.passwd_raw_row_count,
                evidence.passwd_raw_canonical_bytes,
                evidence.passwd_raw_rows_sha256,
                evidence.passwd_unique_row_count,
                evidence.passwd_unique_canonical_bytes,
                evidence.passwd_unique_rows_sha256,
                evidence.effective_group_access_row_count,
                evidence.effective_group_access_canonical_bytes,
                evidence.effective_group_access_rows_sha256,
            ),
            (
                267, 21_762,
                "f6fe0a487223c6f2744b996f82aafc3cec775321285ad13e07388086e728977e",
                135, 10_994,
                "1473637d6258b4652e1a1fcecf9e779c9d5f3ab34de83c0f41a31407edac99d1",
                135, 14_217,
                "30f8ea202ae4dc31a5e96a2e8cdbb1d25dd455db7629e5259168671c28861381",
            ),
        )
        self.assertEqual(
            (evidence.passwd_name_conflict_rows,
             evidence.passwd_uid_conflict_rows,
             evidence.primary_gid_member_rows,
             evidence.membership_query_error_rows),
            ((), (), (), ()),
        )
        self.assertEqual(evidence.root_role_passwd_row[2], 0)
        self.assertEqual(
            evidence.effective_uid_role_passwd_row[2], os.geteuid()
        )
        self.assertGreater(evidence.effective_uid_role_passwd_row[2], 0)
        self.assertNotEqual(
            evidence.root_role_passwd_row[0],
            evidence.effective_uid_role_passwd_row[0],
        )
        self.assertEqual(evidence.gid80_group_row[2], 80)
        expected_gid80_rows = tuple(sorted(
            (evidence.root_role_passwd_row,
             evidence.effective_uid_role_passwd_row),
            key=self._canonical,
        ))
        self.assertEqual(
            evidence.effective_gid80_member_rows, expected_gid80_rows
        )
        self.assertEqual(
            tuple((row.path, row.owner_role) for row in evidence.component_rows),
            (("/opt/homebrew/Cellar", "EFFECTIVE_UID"),
             ("/opt/homebrew/opt", "EFFECTIVE_UID")),
        )
        for row in evidence.component_rows:
            self.assertEqual(len(row.stat_identity), 9)
            self.assertRegex(row.entries_sha256, "^[0-9a-f]{64}$")
        self.assertRegex(evidence.evidence_sha256, "^[0-9a-f]{64}$")
        snapshot = self._require(
            transition_evidence,
            "_capture_task9_bootstrap_path_closure_snapshot_v1",
        )()
        embedded = (
            snapshot.interpreter_evidence.path_closure_evidence
            .trusted_homebrew_component_mode_evidence
        )
        self.assertIs(embedded.installed_host_witness, witness)
        self.assertEqual(
            self._projection(embedded), self._projection(evidence)
        )
        gid_to_i32 = self._require(
            transition_evidence, "_task9_gid_u32_to_i32_v1"
        )
        normalize_gid = self._require(
            transition_evidence, "_task9_normalize_getgrouplist_gid_v1"
        )
        self.assertEqual(
            tuple(inspect.signature(gid_to_i32).parameters), ("value",)
        )
        self.assertEqual(
            tuple(inspect.signature(normalize_gid).parameters), ("value",)
        )
        self.assertEqual(
            tuple(gid_to_i32(value) for value in
                  (0, (1 << 31) - 1, 1 << 31, (1 << 32) - 2, (1 << 32) - 1)),
            (0, (1 << 31) - 1, -(1 << 31), -2, -1),
        )
        self.assertEqual(
            tuple(normalize_gid(value) for value in
                  (0, (1 << 31) - 1, -(1 << 31), -2,
                   (1 << 32) - 1, (1 << 64) - 2)),
            (0, (1 << 31) - 1, 1 << 31, (1 << 32) - 2,
             (1 << 32) - 1, (1 << 32) - 2),
        )
        for operation, rejected in (
            (gid_to_i32, (False, -1, 1 << 32)),
            (normalize_gid,
             (False, -(1 << 31) - 1, 1 << 32,
              (1 << 64) - (1 << 31) - 1, 1 << 64)),
        ):
            for value in rejected:
                with self.subTest(gid_operation=operation.__name__, value=value):
                    with self.assertRaisesRegex(
                        transition_evidence.Task9TransitionEvidenceError,
                        "^task9_bootstrap_homebrew_membership_invalid$",
                    ):
                        operation(value)

    def _assert_round5_homebrew_negatives(self):
        capture = self._require(
            transition_evidence,
            "_task9_bootstrap_capture_trusted_homebrew_component_mode_evidence_v1",
        )
        original_pwd = self._require(
            transition_evidence, "_TASK9_HOMEBREW_PWD_V1"
        )
        original_grp = self._require(
            transition_evidence, "_TASK9_HOMEBREW_GRP_V1"
        )
        original_os = self._require(
            transition_evidence, "_TASK9_HOMEBREW_OS_V1"
        )
        baseline = capture()
        descriptors = self._fd_count()
        raw = list(pwd.getpwall())
        role_uids = {0, os.geteuid()}
        counts = {}
        for row in raw:
            counts[tuple(row)] = counts.get(tuple(row), 0) + 1
        duplicate_rows = tuple(
            row for row, count in counts.items()
            if count > 1 and row[2] not in role_uids
        )
        singleton_rows = tuple(
            row for row, count in counts.items()
            if count == 1 and row[2] not in role_uids
        )
        self.assertGreaterEqual(len(duplicate_rows), 2)
        self.assertGreaterEqual(len(singleton_rows), 2)

        class DelegatingSpy:
            def __init__(self, wrapped):
                self.wrapped = wrapped
                self.calls = 0

            def __getattr__(self, name):
                return getattr(self.wrapped, name)

        class PwdSpy(DelegatingSpy):
            def __init__(self, wrapped, transform):
                super().__init__(wrapped)
                self.transform = transform

            def getpwall(self):
                self.calls += 1
                return self.transform(list(self.wrapped.getpwall()))

        class GrpSpy(DelegatingSpy):
            def __init__(self, wrapped, transform):
                super().__init__(wrapped)
                self.transform = transform

            def getgrgid(self, gid):
                self.calls += 1
                value = self.wrapped.getgrgid(gid)
                return self.transform(value)

        class OsSpy(DelegatingSpy):
            def __init__(self, wrapped, target_name, transform):
                super().__init__(wrapped)
                self.target_name = target_name
                self.transform = transform

            def getgrouplist(self, name, gid):
                self.calls += 1
                if name == self.target_name:
                    return self.transform(
                        lambda: self.wrapped.getgrouplist(name, gid)
                    )
                return self.wrapped.getgrouplist(name, gid)

        def replace_one(rows, old, new):
            result = []
            replaced = False
            for row in rows:
                if not replaced and tuple(row) == tuple(old):
                    result.append(new)
                    replaced = True
                else:
                    result.append(row)
            self.assertTrue(replaced)
            return result

        def run_spy(label, seam_name, spy):
            with self.subTest(homebrew_fault=label):
                setattr(transition_evidence, seam_name, spy)
                try:
                    with self.assertRaisesRegex(
                        transition_evidence.Task9TransitionEvidenceError,
                        "^task9_bootstrap_homebrew_membership_invalid$",
                    ):
                        capture()
                finally:
                    setattr(
                        transition_evidence,
                        seam_name,
                        {
                            "_TASK9_HOMEBREW_PWD_V1": original_pwd,
                            "_TASK9_HOMEBREW_GRP_V1": original_grp,
                            "_TASK9_HOMEBREW_OS_V1": original_os,
                        }[seam_name],
                    )
                self.assertGreater(spy.calls, 0)
                self.assertIs(
                    getattr(transition_evidence, seam_name),
                    {
                        "_TASK9_HOMEBREW_PWD_V1": original_pwd,
                        "_TASK9_HOMEBREW_GRP_V1": original_grp,
                        "_TASK9_HOMEBREW_OS_V1": original_os,
                    }[seam_name],
                )
                self.assertEqual(self._fd_count(), descriptors)
                recaptured = capture()
                self.assertEqual(
                    self._projection(recaptured), self._projection(baseline)
                )

        duplicate_a, duplicate_b = duplicate_rows[:2]
        singleton_a, singleton_b = singleton_rows[:2]

        def partial_raw(rows):
            removed = False
            result = []
            for row in rows:
                if not removed and tuple(row) == duplicate_a:
                    removed = True
                    continue
                result.append(row)
            return result

        def partial_unique(rows):
            return [row for row in rows if tuple(row) != singleton_a]

        substituted = pwd.struct_passwd(
            tuple(singleton_a[:4])
            + (singleton_a[4] + "-task9-substituted",)
            + tuple(singleton_a[5:])
        )

        def same_count_substitution(rows):
            return replace_one(rows, singleton_a, substituted)

        def multiplicity_transfer(rows):
            return replace_one(
                rows, duplicate_a, pwd.struct_passwd(duplicate_b)
            )

        primary_gid = pwd.struct_passwd(
            tuple(singleton_b[:3]) + (80,) + tuple(singleton_b[4:])
        )

        def primary_gid_appearance(rows):
            return replace_one(rows, singleton_b, primary_gid)

        non_ascii = pwd.struct_passwd(
            tuple(singleton_b[:4])
            + (singleton_b[4] + "\N{LATIN SMALL LETTER E WITH ACUTE}",)
            + tuple(singleton_b[5:])
        )

        def non_ascii_row(rows):
            return replace_one(rows, singleton_b, non_ascii)

        def malformed_row(rows):
            return replace_one(rows, singleton_b, tuple(singleton_b))

        for label, transform in (
            ("stable-partial-raw", partial_raw),
            ("stable-partial-unique", partial_unique),
            ("same-count-substitution", same_count_substitution),
            ("duplicate-multiplicity-transfer", multiplicity_transfer),
            ("primary-gid-appearance", primary_gid_appearance),
            ("non-ascii-passwd-row", non_ascii_row),
            ("malformed-passwd-row", malformed_row),
        ):
            run_spy(
                label, "_TASK9_HOMEBREW_PWD_V1",
                PwdSpy(original_pwd, transform),
            )

        gid80 = grp.getgrgid(80)
        role_names = (
            pwd.getpwuid(0).pw_name,
            pwd.getpwuid(os.geteuid()).pw_name,
        )
        for label, members in (
            ("gid80-extra-member", tuple(gid80.gr_mem) + ("task9-extra",)),
            ("gid80-missing-member", (role_names[0],)),
            ("gid80-duplicate-member", role_names + (role_names[1],)),
            ("gid80-remapped-members", ("task9-root", "task9-effective")),
        ):
            run_spy(
                label, "_TASK9_HOMEBREW_GRP_V1",
                GrpSpy(
                    original_grp,
                    lambda row, members=members: grp.struct_group(
                        (row.gr_name, row.gr_passwd, row.gr_gid, list(members))
                    ),
                ),
            )

        target_name = singleton_a[0]
        run_spy(
            "effective-access-drift", "_TASK9_HOMEBREW_OS_V1",
            OsSpy(
                original_os, target_name,
                lambda operation: list(operation()) + [123_456_789],
            ),
        )
        run_spy(
            "membership-query-error", "_TASK9_HOMEBREW_OS_V1",
            OsSpy(
                original_os, target_name,
                lambda operation: (_ for _ in ()).throw(
                    OSError(errno.EIO, "sealed membership query failure")
                ),
            ),
        )

        witness_name = "TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_V1"
        witness = self._require(transition_evidence, witness_name)
        replacement_witness = replace(witness)
        self.assertIsNot(replacement_witness, witness)
        setattr(transition_evidence, witness_name, replacement_witness)
        try:
            with self.assertRaisesRegex(
                transition_evidence.Task9TransitionEvidenceError,
                "^task9_bootstrap_homebrew_membership_invalid$",
            ):
                capture()
        finally:
            setattr(transition_evidence, witness_name, witness)
        self.assertIs(getattr(transition_evidence, witness_name), witness)
        self.assertEqual(self._fd_count(), descriptors)
        self.assertEqual(
            self._projection(capture()), self._projection(baseline)
        )

        validate_pair = self._require(
            transition_evidence,
            "_validate_task9_trusted_homebrew_component_mode_evidence_pair_v1",
        )
        self.assertEqual(
            tuple(inspect.signature(validate_pair).parameters),
            ("before", "after"),
        )
        validate_pair(baseline, baseline)
        for drifted in (
            replace(baseline, evidence_sha256="0" * 64),
            replace(baseline, installed_host_witness=replacement_witness),
        ):
            with self.assertRaisesRegex(
                transition_evidence.Task9TransitionEvidenceError,
                "^task9_bootstrap_homebrew_membership_drift$",
            ):
                validate_pair(baseline, drifted)
        self.assertEqual(self._fd_count(), descriptors)
        self.assertEqual(
            self._projection(capture()), self._projection(baseline)
        )

    def test_bootstrap_root_issuer_uses_only_module_origin_and_rejects_decoys(self):
        allowance = self._require(
            transition_evidence,
            "TASK9_INTERPRETER_PATH_CLOSURE_ALLOWANCE_V1",
        )
        self.assertEqual(
            tuple(row[:2] for row in allowance.path_hop_rows),
            (("LAUNCHER", 0), ("LAUNCHER", 1), ("LAUNCHER", 2),
             ("LAUNCHER", 3), ("STDLIB_ROOT", 0)),
        )
        self.assertEqual(allowance.zero_hop_scopes, (("PURELIB_ROOT", self._VENV_PURELIB),))
        self.assertEqual(allowance.max_symlink_hops, 8)
        module, root, _module_path = self._origin_module()
        issuer = self._require(module, "_issue_task9_evidence_root_authority_v1")
        revoke = self._require(module, "_revoke_task9_evidence_root_authority_v1")
        acquire = self._require(module, "_acquire_task9_bootstrap_mutation_lease_v1")
        release = self._require(module, "_release_task9_bootstrap_mutation_lease_v1")
        self.assertEqual(tuple(inspect.signature(issuer).parameters), ())
        descriptors = self._fd_count()
        decoy = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(decoy.cleanup)
        previous_cwd = os.getcwd()
        previous_root = os.environ.get("TASK9_EVIDENCE_ROOT")
        os.chdir(decoy.name)
        os.environ["TASK9_EVIDENCE_ROOT"] = decoy.name
        try:
            authority = issuer()
        finally:
            os.chdir(previous_cwd)
            if previous_root is None:
                os.environ.pop("TASK9_EVIDENCE_ROOT", None)
            else:
                os.environ["TASK9_EVIDENCE_ROOT"] = previous_root
        self.assertEqual(self._fd_count(), descriptors + 1)
        lease = acquire(authority)
        self.assertEqual(self._exec_flock_exit(root), 73)
        release(lease)
        self.assertEqual(self._exec_flock_exit(root), 0)
        self.assertEqual(self._fd_count(), descriptors)
        authority = issuer()
        revoke(authority)
        self.assertEqual(self._fd_count(), descriptors)
        self.assertFalse(hasattr(module, "_task9_bootstrap_resolve_symlink_chain_v1"))
        self.assertNotIn(
            "sysconfig",
            tuple(allowance.zero_hop_scopes[0]),
        )

    @staticmethod
    def _exec_flock_exit(root):
        program = (
            "import fcntl,os,sys;"
            "fd=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC);"
            "\ntry:\n fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
            "except BlockingIOError:\n sys.exit(73)\n"
            "else:\n fcntl.flock(fd,fcntl.LOCK_UN);sys.exit(0)\n"
        )
        completed = subprocess.run(
            (Round19CommandEvidenceBootstrapTests._LAUNCHER, "-B", "-c",
             program, str(root)),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={
                "LANG": "C", "LC_ALL": "C",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                "PYTHONUTF8": "1",
            },
            check=False,
        )
        if completed.stdout:
            raise AssertionError(completed.stdout.decode("utf-8", "replace"))
        return completed.returncode

    def test_bootstrap_dependency_genesis_and_empty_first_antecedent_are_exact(self):
        if self._emit_child_path_probe_if_requested():
            return
        self._assert_policy_pins(transition_evidence)
        self._assert_round5_homebrew_positive()
        capture = self._require(
            transition_evidence,
            "_capture_task9_bootstrap_path_closure_snapshot_v1",
        )
        dependency_capture = self._require(
            transition_evidence,
            "_capture_task9_bootstrap_dependency_observation_v1",
        )
        snapshot = capture()
        dependency = dependency_capture()
        self.assertEqual(snapshot.schema_version, 1)
        self.assertEqual(
            snapshot.snapshot_kind,
            "PD_INTEGRATION_BOOTSTRAP_PATH_CLOSURE_SNAPSHOT_V1",
        )
        self.assertEqual(
            snapshot.dependency_inventory_sha256, dependency.inventory_sha256
        )
        closure = snapshot.interpreter_evidence.path_closure_evidence
        config = closure.pyvenv_config_evidence
        _stat, raw = self._descriptor_bytes(config.path)
        self.assertEqual(config.raw_bytes, raw)
        self.assertEqual(
            (config.size, config.content_sha256),
            (308, hashlib.sha256(raw).hexdigest()),
        )
        self.assertEqual(config.parsed_rows[1], ("include-system-site-packages", "false"))
        self.assertFalse(
            transition_evidence.TASK9_PYVENV_CONFIG_POLICY_V1
            .include_system_site_packages
        )
        search = closure.import_search_path_evidence
        self.assertEqual(tuple(row.index for row in search.rows), tuple(range(5)))
        for row in search.rows:
            if row.state == "PRESENT":
                self.assertIsNotNone(row.path_stat_identity)
                self.assertIsNone(row.absent_parent_path)
                self.assertIsNone(row.absent_parent_stat_identity)
                self.assertIsNone(row.absent_parent_entries_sha256)
            else:
                self.assertIsNone(row.path_stat_identity)
                self.assertIsNotNone(row.absent_parent_path)
                self.assertIsNotNone(row.absent_parent_stat_identity)
                self.assertIsNotNone(row.absent_parent_entries_sha256)
        excluded = search.excluded_base_purelib_directory
        self.assertEqual(excluded.relation_to_venv_purelib, "DISTINCT_FROM_VENV_PURELIB")
        self.assertIsNone(excluded.active_search_path_index)
        genesis_type = self._require(
            transition_evidence, "Task9CommandDependencyGenesisV1"
        )
        self.assertEqual(
            tuple(field.name for field in fields(genesis_type)),
            (
                "schema_version", "genesis_id", "dependency_inventory",
                "interpreter_evidence", "root_binding_policy_sha256",
                "captured_monotonic_ns", "genesis_sha256",
            ),
        )
        self.assertEqual(
            transition_evidence._task9_future_genesis_projection_v1(
                dependency, snapshot.interpreter_evidence
            )["antecedent_chain_receipt_sha256s"],
            (),
        )
        for forbidden in (
            "_issue_task9_command_dependency_genesis_v1",
            "_reserve_task9_command_dependency_freeze_v1",
            "_freeze_task9_command_dependencies_v1",
            "_TASK9_BOOTSTRAP_GENESIS_LEDGER",
            "_TASK9_BOOTSTRAP_RESERVATION_LEDGER",
            "_TASK9_BOOTSTRAP_SEAL_LEDGER",
        ):
            self.assertFalse(hasattr(transition_evidence, forbidden), forbidden)

    def test_bootstrap_execution_lease_detects_in_place_and_replace_restore_drift(self):
        capture = self._require(
            transition_evidence,
            "_capture_task9_bootstrap_path_closure_snapshot_v1",
        )
        validate_pair = self._require(
            transition_evidence, "_validate_task9_bootstrap_snapshot_pair_v1"
        )
        original_os = self._require(
            transition_evidence, "_TASK9_PATH_CLOSURE_OS_V1"
        )
        baseline = capture()
        descriptors = self._fd_count()

        class StatView:
            def __init__(self, value, **changes):
                for name in (
                    "st_dev", "st_ino", "st_mode", "st_uid", "st_gid",
                    "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns",
                ):
                    setattr(self, name, changes.get(name, getattr(value, name)))

        class SealedLiteralOperationSpy:
            """Mutates one exact literal observation and cannot select a path."""

            def __init__(self, wrapped, operation, exact_path, mutation):
                self.wrapped = wrapped
                self.operation = operation
                self.exact_path = exact_path
                self.mutation = mutation
                self.fired = False

            def __getattr__(self, name):
                return getattr(self.wrapped, name)

            def _path(self, value, dir_fd=None):
                if isinstance(value, int):
                    return os.path.normpath(os.readlink(f"/dev/fd/{value}"))
                rendered = os.fsdecode(value)
                if rendered.startswith("/"):
                    return os.path.normpath(rendered)
                if dir_fd is None:
                    return os.path.normpath(rendered)
                parent = os.readlink(f"/dev/fd/{dir_fd}")
                return os.path.normpath(os.path.join(parent, rendered))

            def _matches(self, operation, value, dir_fd=None):
                return (
                    not self.fired
                    and self.operation == operation
                    and self._path(value, dir_fd) == self.exact_path
                )

            def _stat_mutation(self, value):
                mutation = self.mutation
                if mutation == "OWNER":
                    return StatView(value, st_uid=value.st_uid + 10_000)
                if mutation == "WRITABLE":
                    return StatView(value, st_mode=value.st_mode | 0o022)
                if mutation == "NLINK":
                    return StatView(value, st_nlink=value.st_nlink + 1)
                if mutation == "SPECIAL":
                    return StatView(value, st_mode=stat.S_IFIFO | 0o600)
                if mutation == "IDENTITY":
                    return StatView(value, st_ctime_ns=value.st_ctime_ns + 1)
                raise AssertionError(mutation)

            def open(self, path, flags, *args, dir_fd=None, **kwargs):
                if self._matches("open", path, dir_fd):
                    self.fired = True
                    raise OSError(errno.ENOENT, "governed literal missing")
                return self.wrapped.open(
                    path, flags, *args, dir_fd=dir_fd, **kwargs
                )

            def stat(self, path, *args, dir_fd=None, **kwargs):
                if self._matches("stat", path, dir_fd):
                    self.fired = True
                    value = self.wrapped.stat(
                        path, *args, dir_fd=dir_fd, **kwargs
                    )
                    return self._stat_mutation(value)
                return self.wrapped.stat(path, *args, dir_fd=dir_fd, **kwargs)

            def lstat(self, path, *args, dir_fd=None, **kwargs):
                if self._matches("lstat", path, dir_fd):
                    self.fired = True
                    if self.mutation == "MISSING":
                        raise FileNotFoundError(errno.ENOENT, "missing", path)
                    if self.mutation == "APPEAR":
                        return self.wrapped.stat(
                            "/Users/mthanki/.venvs/inci-expert-py314/pyvenv.cfg",
                            follow_symlinks=False,
                        )
                    value = self.wrapped.stat(
                        path, *args, dir_fd=dir_fd, follow_symlinks=False
                    )
                    return self._stat_mutation(value)
                return self.wrapped.stat(
                    path, *args, dir_fd=dir_fd, follow_symlinks=False
                )

            def readlink(self, path, *args, dir_fd=None, **kwargs):
                if self._matches("readlink", path, dir_fd):
                    self.fired = True
                    return self.mutation
                return self.wrapped.readlink(
                    path, *args, dir_fd=dir_fd, **kwargs
                )

            def fstat(self, fd):
                value = self.wrapped.fstat(fd)
                if self._matches("fstat", fd):
                    self.fired = True
                    return self._stat_mutation(value)
                return value

            def read(self, fd, size):
                content = self.wrapped.read(fd, size)
                if self._matches("read", fd):
                    self.fired = True
                    if self.mutation == "NON_UTF8":
                        return b"\xff" + content[1:]
                    if self.mutation == "TRUE_CONFIG":
                        return content.replace(b"false", b"true ", 1)
                    if self.mutation == "CRLF":
                        return content.replace(b"\n", b"\r", 1)
                    if self.mutation == "CONTENT":
                        return (b"X" if content[:1] != b"X" else b"Y") + content[1:]
                return content

            def listdir(self, path="."):
                if self._matches("listdir", path):
                    self.fired = True
                    rows = list(self.wrapped.listdir(path))
                    if self.mutation == "EXTRA":
                        rows.append("task9-unexpected-entry")
                    elif self.mutation == "MISSING":
                        rows = rows[1:]
                    return rows
                return self.wrapped.listdir(path)

        stdlib = self._STDLIB
        config = "/Users/mthanki/.venvs/inci-expert-py314/pyvenv.cfg"
        absent_zip = f"{Path(stdlib).parent}/python314.zip"
        base_file = f"{self._BASE_PURELIB}/pip/__init__.py"
        matrix = (
            ("launcher-link-missing", "lstat", self._LAUNCHER, "MISSING"),
            ("launcher-extra-or-reordered", "readlink", self._LAUNCHER, "python-extra"),
            ("launcher-retargeted", "readlink", self._LAUNCHER, "python3.13"),
            ("opt-component-replaced", "fstat", "/opt/homebrew/opt", "IDENTITY"),
            ("stdlib-formula-link-replaced", "readlink",
             "/opt/homebrew/opt/python@3.14", "../Cellar/python@3.14/3.14.4"),
            ("unexpected-purelib-hop", "open", self._VENV_PURELIB, "MISSING"),
            ("hop-cycle", "readlink", self._LAUNCHER, "python"),
            ("ninth-hop", "readlink", self._LAUNCHER, "hop-1"),
            ("runtime-link-missing", "lstat",
             f"{stdlib}/config-3.14-darwin/libpython3.14.a", "MISSING"),
            ("runtime-link-extra", "listdir", stdlib, "EXTRA"),
            ("runtime-link-retargeted", "readlink",
             f"{stdlib}/config-3.14-darwin/libpython3.14.a", "../../Python"),
            ("runtime-link-kind", "lstat",
             f"{stdlib}/config-3.14-darwin/libpython3.14.a", "SPECIAL"),
            ("runtime-alias-diverges", "readlink",
             f"{stdlib}/config-3.14-darwin/libpython3.14.dylib", "../../Python"),
            ("framework-write-restore", "read", self._FRAMEWORK_BINARY, "CONTENT"),
            ("framework-chmod-restore", "fstat", self._FRAMEWORK_BINARY, "WRITABLE"),
            ("framework-replace-restore", "fstat", self._FRAMEWORK_BINARY, "IDENTITY"),
            ("base-directory-replace-restore", "fstat", self._BASE_PURELIB, "IDENTITY"),
            ("base-file-write-restore", "read", base_file, "CONTENT"),
            ("base-file-chmod-restore", "fstat", base_file, "WRITABLE"),
            ("base-file-replace-restore", "fstat", base_file, "IDENTITY"),
            ("base-extra-file", "listdir", self._BASE_PURELIB, "EXTRA"),
            ("base-missing-file", "listdir", self._BASE_PURELIB, "MISSING"),
            ("base-symlink-or-special", "stat", base_file, "SPECIAL"),
            ("pyvenv-missing", "open", config, "MISSING"),
            ("pyvenv-extra-reordered-duplicate", "read", config, "CONTENT"),
            ("pyvenv-true", "read", config, "TRUE_CONFIG"),
            ("pyvenv-non-utf8", "read", config, "NON_UTF8"),
            ("pyvenv-crlf", "read", config, "CRLF"),
            ("pyvenv-write-restore", "fstat", config, "IDENTITY"),
            ("pyvenv-replace-restore", "fstat", config, "IDENTITY"),
            ("absent-zip-appears", "lstat", absent_zip, "APPEAR"),
            ("absent-parent-replaced", "fstat", str(Path(absent_zip).parent), "IDENTITY"),
            ("component-owner", "fstat", "/opt/homebrew", "OWNER"),
            ("component-mode", "fstat", "/opt/homebrew", "WRITABLE"),
            ("link-nlink", "lstat", self._LAUNCHER, "NLINK"),
            ("target-special", "fstat", self._FRAMEWORK_BINARY, "SPECIAL"),
            ("entries-instability", "listdir", "/opt/homebrew/opt", "EXTRA"),
        )
        for label, operation, exact_path, mutation in matrix:
            with self.subTest(fault=label):
                spy = SealedLiteralOperationSpy(
                    original_os, operation, exact_path, mutation
                )
                transition_evidence._TASK9_PATH_CLOSURE_OS_V1 = spy
                try:
                    with self.assertRaises(
                        transition_evidence.Task9TransitionEvidenceError
                    ):
                        capture()
                finally:
                    transition_evidence._TASK9_PATH_CLOSURE_OS_V1 = original_os
                self.assertTrue(spy.fired, label)
                self.assertEqual(self._fd_count(), descriptors)
                capture()

        real_path_before = tuple(sys.path)
        original_import_search_sys = self._require(
            transition_evidence, "_TASK9_IMPORT_SEARCH_SYS_V1"
        )
        self.assertIs(original_import_search_sys, sys)
        self.assertNotIn(self._BASE_PURELIB, real_path_before)

        class SealedImportSearchObserver:
            __slots__ = (
                "_injected_path", "_read_count", "_real_path_at_read",
            )

            def __init__(self, path):
                object.__setattr__(self, "_injected_path", path)
                object.__setattr__(self, "_read_count", 0)
                object.__setattr__(self, "_real_path_at_read", None)

            def __setattr__(self, name, value):
                raise AttributeError("sealed import-search observer")

            @property
            def path(self):
                if self._read_count != 0:
                    raise AssertionError("sealed import-search path read repeated")
                object.__setattr__(self, "_read_count", 1)
                object.__setattr__(
                    self, "_real_path_at_read", tuple(sys.path)
                )
                return self._injected_path

            @property
            def read_count(self):
                return self._read_count

            @property
            def real_path_at_read(self):
                return self._real_path_at_read

        observer = SealedImportSearchObserver(
            real_path_before + (self._BASE_PURELIB,)
        )
        self.assertEqual(
            SealedImportSearchObserver.__slots__,
            ("_injected_path", "_read_count", "_real_path_at_read"),
        )
        self.assertFalse(hasattr(observer, "__dict__"))
        with self.assertRaises(AttributeError):
            observer._injected_path = ()
        with self.assertRaises(AttributeError):
            observer.path = ()
        transition_evidence._TASK9_IMPORT_SEARCH_SYS_V1 = observer
        try:
            with self.assertRaisesRegex(
                transition_evidence.Task9TransitionEvidenceError,
                "^task9_bootstrap_import_search_invalid$",
            ):
                capture()
            self.assertEqual(tuple(sys.path), real_path_before)
        finally:
            transition_evidence._TASK9_IMPORT_SEARCH_SYS_V1 = (
                original_import_search_sys
            )
        self.assertIs(
            transition_evidence._TASK9_IMPORT_SEARCH_SYS_V1,
            original_import_search_sys,
        )
        self.assertEqual(observer.read_count, 1)
        self.assertEqual(observer.real_path_at_read, real_path_before)
        self.assertEqual(tuple(sys.path), real_path_before)
        stable_after = capture()
        validate_pair(baseline, stable_after)
        for field, value in (
            ("root_binding_policy_sha256", "0" * 64),
            ("interpreter_path_closure_evidence_sha256", "f" * 64),
            ("dependency_inventory_sha256", "a" * 64),
        ):
            with self.subTest(pair_drift=field):
                forged = replace(stable_after, **{field: value})
                with self.assertRaises(
                    transition_evidence.Task9TransitionEvidenceError
                ):
                    validate_pair(baseline, forged)
        self._assert_round5_homebrew_negatives()
        self.assertEqual(self._fd_count(), descriptors)

    def test_bootstrap_recaptures_interpreter_distribution_and_directory_identities(self):
        snapshot = self._require(
            transition_evidence,
            "_capture_task9_bootstrap_path_closure_snapshot_v1",
        )()
        interpreter = snapshot.interpreter_evidence
        self.assertEqual(
            (interpreter.implementation_name, interpreter.version_info,
             interpreter.cache_tag),
            ("cpython", (3, 14, 5, "final", 0), "cpython-314"),
        )
        executable_stat, executable_bytes = self._descriptor_bytes(
            interpreter.resolved_executable_path
        )
        self.assertEqual(
            (
                interpreter.resolved_executable_stat_identity,
                interpreter.resolved_executable_sha256,
            ),
            (
                self._stat9(executable_stat),
                hashlib.sha256(executable_bytes).hexdigest(),
            ),
        )
        self.assertNotIn(
            "SYMLINK", {row.file_kind for row in interpreter.runtime_inventory_rows}
        )
        links = interpreter.runtime_symlink_inventory_evidence
        self.assertEqual(
            tuple(row.relative_path for row in links.runtime_symlink_inventory_rows),
            (
                "config-3.14-darwin/libpython3.14.a",
                "config-3.14-darwin/libpython3.14.dylib",
                "site-packages",
            ),
        )
        self.assertEqual(len(links.regular_target_rows), 1)
        target = links.regular_target_rows[0]
        self.assertEqual(target.resolved_target_path, self._FRAMEWORK_BINARY)
        for row in links.runtime_symlink_inventory_rows[:2]:
            self.assertEqual(
                (row.target_size, row.target_stat_identity,
                 row.target_content_sha256),
                (target.target_size, target.target_stat_identity,
                 target.target_content_sha256),
            )
        excluded = links.excluded_base_purelib_directory
        self.assertIs(
            excluded,
            interpreter.path_closure_evidence.import_search_path_evidence
            .excluded_base_purelib_directory,
        )
        self.assertEqual(
            (excluded.exact_file_count, excluded.exact_directory_count,
             excluded.exact_file_bytes),
            (487, 79, 5_657_777),
        )
        self.assertEqual(
            sum(row.size for row in excluded.file_rows), 5_657_777
        )
        for row in excluded.file_rows:
            value, content = self._descriptor_bytes(
                f"{self._BASE_PURELIB}/{row.relative_path}"
            )
            self.assertEqual(
                (row.size, row.stat_identity, row.content_sha256),
                (len(content), self._stat9(value),
                 hashlib.sha256(content).hexdigest()),
            )
        self.assertEqual(
            tuple(row.normalized_name for row in interpreter.external_distribution_inventory_rows),
            ("certifi", "cffi", "charset-normalizer", "cryptography", "idna",
             "pip", "pycparser", "requests", "urllib3"),
        )
        for distribution in interpreter.external_distribution_inventory_rows:
            self.assertTrue(distribution.version)
            for row in distribution.file_rows:
                absolute = os.path.normpath(
                    os.path.join(self._VENV_PURELIB, row.relative_path)
                )
                value, content = self._descriptor_bytes(absolute)
                self.assertEqual(
                    (row.size, row.stat_identity, row.content_sha256),
                    (len(content), self._stat9(value),
                     hashlib.sha256(content).hexdigest()),
                )
        closure = interpreter.path_closure_evidence
        self.assertEqual(len(closure.component_directory_identity_rows), 192)
        self.assertEqual(len(closure.endpoint_parent_identity_rows), 23)
        self.assertEqual(
            tuple(
                (row.endpoint_key, row.component_index, row.absolute_path)
                for row in closure.component_directory_identity_rows
            ),
            self._component_policy_rows(),
        )
        for row in closure.component_directory_identity_rows:
            identity, entries = self._entries_sha(row.absolute_path)
            self.assertEqual(
                (row.stat_identity, row.entries_sha256), (identity, entries)
            )
        for row in closure.endpoint_parent_identity_rows:
            identity, entries = self._entries_sha(row.parent_path)
            self.assertEqual(
                (row.parent_stat_identity, row.parent_entries_sha256),
                (identity, entries),
            )

    def test_bootstrap_runner_freezes_before_and_after_without_acceptance_authority(self):
        self._require(transition_evidence, "Task9BootstrapRunObservationV1")
        runner = self._require(
            transition_evidence, "_run_task9_command_bootstrap_exercise_v1"
        )
        self.assertEqual(tuple(inspect.signature(runner).parameters), ())
        observation = runner()
        self.assertEqual(
            tuple(field.name for field in fields(type(observation))),
            (
                "schema_version", "observation_kind", "fixed_test_target",
                "before_snapshot", "after_snapshot",
                "unittest_environment_rows",
                "unittest_environment_rows_sha256",
                "frozen_v6_environment_rows",
                "frozen_v6_environment_rows_sha256",
                "unittest_stdout_utf8", "unittest_stdout_size",
                "unittest_stdout_sha256", "frozen_v6_stdout_utf8",
                "frozen_v6_stdout_size", "frozen_v6_stdout_sha256",
                "unittest_search_row_projection_sha256",
                "frozen_v6_search_row_projection_sha256",
                "unittest_sentinel_sha256", "frozen_v6_sentinel_sha256",
                "started_monotonic_ns", "unittest_child_pid",
                "unittest_completed_monotonic_ns",
                "frozen_v6_started_monotonic_ns", "frozen_v6_child_pid",
                "frozen_v6_completed_monotonic_ns", "completed_monotonic_ns",
                "wall_duration_ns", "unittest_returncode",
                "frozen_v6_returncode", "tests_run", "failures", "errors",
                "skipped", "semantic_outcome", "observation_sha256",
            ),
        )
        self.assertEqual(
            observation.observation_kind,
            "PD_INTEGRATION_BOOTSTRAP_RUN_OBSERVATION_V1",
        )
        self.assertNotIsInstance(
            observation,
            getattr(transition_evidence, "Task9CommandEvidenceV1", object),
        )
        for forbidden in (
            "command_id", "stage_id", "argv", "frozen_dependency_seal",
            "acceptance", "review", "chain_receipt_sha256", "release_status",
        ):
            self.assertFalse(hasattr(observation, forbidden), forbidden)
        before = observation.before_snapshot
        after = observation.after_snapshot
        for field in fields(type(before)):
            if field.name not in ("captured_monotonic_ns", "snapshot_sha256"):
                self.assertEqual(
                    getattr(before, field.name), getattr(after, field.name),
                    field.name,
                )
        self.assertEqual(
            observation.unittest_search_row_projection_sha256,
            before.interpreter_evidence.path_closure_evidence
            .import_search_path_evidence.row_projection_sha256,
        )
        self.assertEqual(
            observation.frozen_v6_search_row_projection_sha256,
            observation.unittest_search_row_projection_sha256,
        )
        for kind, rows in (
            ("UNITTEST_MODULE", observation.unittest_environment_rows),
            ("FROZEN_V6_SCRIPT", observation.frozen_v6_environment_rows),
        ):
            self.assertEqual(len(rows), 17)
            environment = dict(rows)
            self.assertEqual(
                environment["INCI_TASK9_BOOTSTRAP_PATH_PROBE"], kind
            )
            self.assertRegex(
                environment["HOME"],
                rf"^/tmp/inci-task9-home-bootstrap-probe-{kind.lower()}-[1-9][0-9]*$",
            )
            self.assertRegex(
                environment["PYTHONPYCACHEPREFIX"],
                rf"^/tmp/inci-task9-pycache-bootstrap-probe-{kind.lower()}-[1-9][0-9]*$",
            )
            self.assertFalse(Path(environment["HOME"]).exists())
            self.assertFalse(Path(environment["PYTHONPYCACHEPREFIX"]).exists())
            rows_sha = self._domain_sha(
                "INCI-TASK-9-BOOTSTRAP-PROBE-ENVIRONMENT-ROWS-V1",
                {"schema_version": 1, "probe_kind": kind, "rows": rows},
            )
            self.assertEqual(
                rows_sha,
                (
                    observation.unittest_environment_rows_sha256
                    if kind == "UNITTEST_MODULE"
                    else observation.frozen_v6_environment_rows_sha256
                ),
            )
        self.assertTrue(
            observation.started_monotonic_ns
            < observation.unittest_completed_monotonic_ns
            <= observation.frozen_v6_started_monotonic_ns
            < observation.frozen_v6_completed_monotonic_ns
            <= observation.completed_monotonic_ns
        )
        self.assertNotEqual(
            observation.unittest_child_pid, observation.frozen_v6_child_pid
        )
        self.assertEqual(
            observation.wall_duration_ns,
            observation.completed_monotonic_ns
            - observation.started_monotonic_ns,
        )
        self.assertEqual(
            (observation.unittest_returncode,
             observation.frozen_v6_returncode, observation.tests_run,
             observation.failures, observation.errors, observation.skipped,
             observation.semantic_outcome),
            (0, 0, 1, 0, 0, 0, "GREEN"),
        )
        for kind, stdout, size, stdout_sha, sentinel_sha, payload_sha in (
            (
                "UNITTEST_MODULE", observation.unittest_stdout_utf8,
                observation.unittest_stdout_size,
                observation.unittest_stdout_sha256,
                observation.unittest_sentinel_sha256,
                observation.unittest_search_row_projection_sha256,
            ),
            (
                "FROZEN_V6_SCRIPT", observation.frozen_v6_stdout_utf8,
                observation.frozen_v6_stdout_size,
                observation.frozen_v6_stdout_sha256,
                observation.frozen_v6_sentinel_sha256,
                observation.frozen_v6_search_row_projection_sha256,
            ),
        ):
            encoded = stdout.encode("utf-8", "strict")
            self.assertEqual((size, stdout_sha), (len(encoded), hashlib.sha256(encoded).hexdigest()))
            matches = re.findall(
                rf"^INCI_TASK9_CHILD_PATH_V1 {kind} ([0-9a-f]{{64}})$",
                stdout, re.MULTILINE,
            )
            self.assertEqual(matches, [payload_sha])
            line = f"INCI_TASK9_CHILD_PATH_V1 {kind} {payload_sha}\n".encode("ascii")
            self.assertEqual(
                sentinel_sha,
                hashlib.sha256(
                    b"INCI-TASK-9-CHILD-PATH-PROBE-SENTINEL-V1\0" + line
                ).hexdigest(),
            )
        interpreter_projection = self._projection(before.interpreter_evidence)
        self.assertIn("path_closure_evidence", interpreter_projection)
        self.assertIn("runtime_symlink_inventory_evidence", interpreter_projection)
        self.assertIn(
            "excluded_base_purelib_directory",
            interpreter_projection["path_closure_evidence"]
            ["import_search_path_evidence"],
        )
        size_guard = self._require(
            transition_evidence, "_task9_enforce_command_canonical_byte_count_v1"
        )
        size_guard(4_194_304)
        with self.assertRaises(transition_evidence.Task9TransitionEvidenceError):
            size_guard(4_194_305)
        bundle_guard = self._require(
            transition_evidence, "_task9_enforce_evidence_bundle_capacity_v1"
        )
        bundle_guard(
            evidence_id="PREDECESSOR", occurrence_count=34,
            command_byte_counts=(4_194_304,) * 34,
            command_elided_byte_count=8_388_608,
            bundle_byte_count=150_994_944,
        )
        for change in (
            {"command_elided_byte_count": 8_388_609},
            {"bundle_byte_count": 150_994_945},
            {"evidence_id": "FUNCTIONAL_B", "occurrence_count": 34},
        ):
            values = {
                "evidence_id": "PREDECESSOR", "occurrence_count": 34,
                "command_byte_counts": (4_194_304,) * 34,
                "command_elided_byte_count": 8_388_608,
                "bundle_byte_count": 150_994_944,
            }
            values.update(change)
            with self.assertRaises(transition_evidence.Task9TransitionEvidenceError):
                bundle_guard(**values)

    def test_bootstrap_preserves_decoder_paths_empty_registries_and_zero_network(self):
        snapshot = self._require(
            transition_evidence,
            "_capture_task9_bootstrap_path_closure_snapshot_v1",
        )()
        runner = self._require(
            transition_evidence, "_run_task9_command_bootstrap_exercise_v1"
        )
        import requests.sessions as guarded_requests
        import kalshi_client as guarded_kalshi
        import executor as guarded_executor

        def forbidden_capability(*args, **kwargs):
            raise AssertionError("task9_bootstrap_capability_used")

        guarded = (
            (socket, "socket"), (socket, "getaddrinfo"),
            (guarded_requests.Session, "request"),
            (guarded_kalshi.KalshiClient, "_request"),
            (guarded_executor.Executor, "execute"),
        )
        originals = tuple((owner, name, getattr(owner, name)) for owner, name in guarded)
        try:
            for owner, name, _value in originals:
                setattr(owner, name, forbidden_capability)
            guarded_observation = runner()
        finally:
            for owner, name, value in originals:
                setattr(owner, name, value)
        self.assertEqual(guarded_observation.semantic_outcome, "GREEN")
        self.assertEqual(len(transition_evidence.TASK9_EVIDENCE_DECODER_TABLE_V3), 147)
        self.assertEqual(
            transition_evidence.task9_evidence_decoder_table_sha256_v3(),
            "8e229254772ad9af77c97ed256f54d5e5bd1dfe5097909f97d2e3976f1c5572e",
        )
        self.assertEqual(
            (len(transition_evidence.TASK9_STAGE_OWNED_PATHS_V1),
             len(transition_evidence.TASK9_TRANSIENT_WRITE_PATHS_V1)),
            (72, 36),
        )
        dependency = self._require(
            transition_evidence,
            "_capture_task9_bootstrap_dependency_observation_v1",
        )()
        by_path = {row.relative_path: row for row in dependency.inventory_rows}
        boundary = (
            ("inci_tennis_adapters/registry.py", "PRESENT"),
            ("inci_tennis_runtime/shadow_activation.py", "ABSENT"),
            ("inci_tennis_runtime/shadow_sources.py", "ABSENT"),
            ("inci_tennis_runtime/bootstrap.py", "ABSENT"),
            ("executor.py", "PRESENT"), ("kalshi_client.py", "PRESENT"),
            ("market_data.py", "PRESENT"), ("order_resolution.py", "PRESENT"),
        )
        root = Path(transition_evidence.__file__).parents[1]
        for relative_path, expected_state in boundary:
            row = by_path[relative_path]
            path = root / relative_path
            actual_state = "PRESENT" if path.exists() else "ABSENT"
            self.assertEqual((row.state, actual_state), (expected_state, expected_state))
            if actual_state == "PRESENT":
                value, content = self._descriptor_bytes(path)
                identity = dict(dependency.file_identity_rows)[relative_path]
                self.assertEqual(identity, self._stat9(value))
                self.assertEqual(
                    row.content_sha256, hashlib.sha256(content).hexdigest()
                )
            else:
                self.assertIsNone(row.content_sha256)
                self.assertNotIn(relative_path, dict(dependency.file_identity_rows))
        retained = {}
        for relative_path, row in by_path.items():
            if row.state == "PRESENT" and relative_path.endswith(".py"):
                retained[relative_path] = self._descriptor_bytes(root / relative_path)[1]
        closed_ast_rows = (
            ("inci_tennis_adapters/registry.py", "PRODUCTION_PROVIDER_REGISTRY", "EMPTY"),
            ("inci_tennis_runtime/shadow_activation.py", "_SHADOW_ACTIVATION_REGISTRY_V1", "ABSENT_MODULE"),
            ("inci_tennis_runtime/shadow_activation.py", "NATIVE_CAPACITY_CERTIFICATE_REGISTRY_V1", "ABSENT_MODULE"),
            ("inci_tennis_runtime/shadow_sources.py", "_issue_shadow_transport_factory_issuer_v1", "ABSENT_MODULE"),
            ("inci_tennis_runtime/bootstrap.py", "_issue_shadow_startup_authority_v1", "ABSENT_MODULE"),
        )
        for relative_path, symbol, evidence_kind in closed_ast_rows:
            content = retained.get(relative_path)
            if content is None:
                self.assertEqual(evidence_kind, "ABSENT_MODULE")
                self.assertEqual(
                    (by_path[relative_path].state,
                     by_path[relative_path].content_sha256),
                    ("ABSENT", None),
                )
                continue
            tree = ast.parse(content, filename=relative_path)
            definitions = tuple(
                node for node in ast.walk(tree)
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and node.name == symbol
                )
            )
            assignments = tuple(
                node for node in tree.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == symbol
                    for target in (
                        node.targets if isinstance(node, ast.Assign) else (node.target,)
                    )
                )
            )
            if evidence_kind == "ABSENT":
                self.assertEqual((definitions, assignments), ((), ()))
            else:
                self.assertEqual(len(assignments), 1)
                self.assertIsInstance(assignments[0].value, ast.Tuple)
                self.assertEqual(assignments[0].value.elts, [])
        loader_allowance = {
            "inci_tennis_io/expert_journal_store.py",
            "tests/tennis_v1/test_expert_journal_store.py",
            "tests/tennis_v1/test_task9_transition_evidence.py",
        }
        ast_sources = dict(retained)
        for relative_path in (
            "inci_tennis_io/expert_journal_store.py",
            "tests/tennis_v1/test_expert_journal_store.py",
            "tests/tennis_v1/test_expert_replay.py",
            "tests/tennis_v1/test_kalshi_candidate.py",
        ):
            ast_sources[relative_path] = self._descriptor_bytes(
                root / relative_path
            )[1]
        dynamic_import_findings = []
        for relative_path, content in ast_sources.items():
            tree = ast.parse(content, filename=relative_path)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    targets = (
                        node.targets if isinstance(node, ast.Assign)
                        else (node.target,)
                    )
                    self.assertFalse(
                        any(
                            isinstance(target, ast.Subscript)
                            and isinstance(target.value, ast.Attribute)
                            and isinstance(target.value.value, ast.Name)
                            and target.value.value.id == "sys"
                            and target.value.attr == "path"
                            for target in targets
                        ),
                        relative_path,
                    )
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                sys_path_mutation = (
                    isinstance(function, ast.Attribute)
                    and function.attr in ("append", "extend", "insert", "remove", "pop", "clear")
                    and isinstance(function.value, ast.Attribute)
                    and isinstance(function.value.value, ast.Name)
                    and function.value.value.id == "sys"
                    and function.value.attr == "path"
                )
                if sys_path_mutation:
                    self.fail(f"real sys.path mutation is forbidden: {relative_path}")
                direct_loader = (
                    isinstance(function, ast.Attribute)
                    and function.attr in (
                        "SourceFileLoader", "spec_from_file_location", "run_path"
                    )
                )
                if direct_loader:
                    self.assertIn(relative_path, loader_allowance)
                dynamic_import = (
                    isinstance(function, ast.Name)
                    and function.id == "__import__"
                ) or (
                    isinstance(function, ast.Attribute)
                    and function.attr == "import_module"
                    and isinstance(function.value, ast.Name)
                    and function.value.id == "importlib"
                )
                if dynamic_import and not (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    dynamic_import_findings.append(
                        (relative_path, node.lineno, ast.unparse(node))
                    )
        self.assertEqual(dynamic_import_findings, [])
        self.assertEqual(
            snapshot.interpreter_evidence.path_closure_evidence
            .import_search_path_evidence.rows[4].absolute_path,
            self._VENV_PURELIB,
        )
        self.assertNotIn(
            self._BASE_PURELIB,
            tuple(row.absolute_path for row in
                  snapshot.interpreter_evidence.path_closure_evidence
                  .import_search_path_evidence.rows),
        )
        import inci_tennis_adapters.registry as adapter_registry
        self.assertEqual(adapter_registry.PRODUCTION_PROVIDER_REGISTRY, ())
        for forbidden in (
            "_issue_shadow_transport_factory_issuer_v1",
            "_issue_shadow_startup_authority_v1",
            "_issue_task9_command_dependency_genesis_v1",
        ):
            self.assertFalse(
                hasattr(transition_evidence, forbidden),
                forbidden,
            )
        test_bytes = Path(__file__).read_bytes()
        source_bytes = Path(transition_evidence.__file__).read_bytes()
        frozen_bytes = root.joinpath("tests.py").read_bytes()
        test_marker_rows = (
            (b"# TASK9_ROUND19_UNITTEST_PREIMPORT_GUARD_BEGIN_V1\n",
             b"# TASK9_ROUND19_UNITTEST_PREIMPORT_GUARD_END_V1\n", test_bytes),
            (b"# TASK9_ROUND19_COMMAND_BOOTSTRAP_BEGIN_V1\n",
             b"# TASK9_ROUND19_COMMAND_BOOTSTRAP_END_V1\n", test_bytes),
            (b"# TASK9_ROUND19_CAPACITY_ASSERTION_SUBSTRATE_BEGIN_V1\n",
             b"# TASK9_ROUND19_CAPACITY_ASSERTION_SUBSTRATE_END_V1\n", test_bytes),
            (b"# TASK9_ROUND19_EVIDENCE_BUNDLE_CAPACITY_CONTRACT_BEGIN_V1\n",
             b"# TASK9_ROUND19_EVIDENCE_BUNDLE_CAPACITY_CONTRACT_END_V1\n", test_bytes),
            (b"# TASK9_ROUND19_DECODER_CAPTURE_CAPACITY_REPAIR_CONTRACT_BEGIN_V1\n",
             b"# TASK9_ROUND19_DECODER_CAPTURE_CAPACITY_REPAIR_CONTRACT_END_V1\n", test_bytes),
        )
        for begin, end, content in test_marker_rows + (
            (b"# TASK9_ROUND19_COMMAND_BOOTSTRAP_BEGIN_V1\n",
             b"# TASK9_ROUND19_COMMAND_BOOTSTRAP_END_V1\n", source_bytes),
            (b"# TASK9_ROUND19_FROZEN_V6_PATH_PROBE_BEGIN_V1\n",
             b"# TASK9_ROUND19_FROZEN_V6_PATH_PROBE_END_V1\n", frozen_bytes),
        ):
            self.assertEqual((content.count(begin), content.count(end)), (1, 1))
            self.assertLess(content.index(begin), content.index(end))
            inclusive = content[
                content.index(begin):content.index(end) + len(end)
            ]
            self.assertRegex(hashlib.sha256(inclusive).hexdigest(), "^[0-9a-f]{64}$")
        test_intervals = sorted(
            (test_bytes.index(begin), test_bytes.index(end) + len(end))
            for begin, end, _content in test_marker_rows
        )
        self.assertTrue(all(
            left[1] <= right[0]
            for left, right in zip(test_intervals, test_intervals[1:])
        ))
        self.assertEqual(
            transition_evidence.TASK9_BOOTSTRAP_PRODUCTION_REGISTRIES_V1, ()
        )
        self.assertEqual(
            transition_evidence.TASK9_BOOTSTRAP_NETWORK_CAPABILITIES_V1, ()
        )
        self.assertEqual(
            transition_evidence.TASK9_BOOTSTRAP_NETWORK_CALL_PATHS_V1, ()
        )


# TASK9_ROUND19_COMMAND_BOOTSTRAP_END_V1


# TASK9_ROUND19_CAPACITY_ASSERTION_SUBSTRATE_BEGIN_V1
class _Round19CapacityAssertions:
    @staticmethod
    def _require(name):
        value = getattr(transition_evidence, name, None)
        if value is None:
            raise AssertionError(f"semantic capacity omission: {name} is absent")
        return value

    @staticmethod
    def _canonical(value):
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")

    @classmethod
    def _assert_rejected(cls, operation, *args):
        with unittest.TestCase().assertRaises(Task9TransitionEvidenceError):
            operation(*args)
# TASK9_ROUND19_CAPACITY_ASSERTION_SUBSTRATE_END_V1


# TASK9_ROUND19_EVIDENCE_BUNDLE_CAPACITY_CONTRACT_BEGIN_V1
class Round19EvidenceBundleCapacityContractTests(
    _Round19CapacityAssertions, unittest.TestCase
):
    def test_maximum_valid_pinned_command_projection_fits_four_mibibytes(self):
        cap = self._require("TASK9_COMMAND_CANONICAL_BYTE_CAP_V1")
        witness = self._require("_task9_maximum_command_capacity_witness_v1")()
        encoded = self._canonical(witness)
        self.assertEqual(cap, 4_194_304)
        self.assertLessEqual(len(encoded), cap)
        interpreter = witness["interpreter_evidence"]
        self.assertIn("runtime_symlink_inventory_evidence", interpreter)
        self.assertIn("path_closure_evidence", interpreter)
        closure = interpreter["path_closure_evidence"]
        self.assertIn("pyvenv_config_evidence", closure)
        self.assertIn("import_search_path_evidence", closure)
        self.assertIn(
            "excluded_base_purelib_directory",
            closure["import_search_path_evidence"],
        )
        validate = self._require("_validate_task9_command_capacity_v1")
        validate(witness)
        overflow = dict(witness)
        overflow["stdout_utf8"] = witness["stdout_utf8"] + "x"
        with self.assertRaises(Task9TransitionEvidenceError):
            validate(overflow)

    def test_all_nine_bundle_occurrence_counts_are_exact_and_predecessor_is_thirty_four(self):
        rows = self._require("TASK9_BUNDLE_COMMAND_OCCURRENCE_CARDINALITY_V1")
        self.assertEqual(
            rows,
            (
                ("PREDECESSOR", 34), ("FUNCTIONAL_A", 26),
                ("FUNCTIONAL_B", 8), ("FUNCTIONAL_C", 8),
                ("FUNCTIONAL_D", 8), ("FUNCTIONAL_E", 8),
                ("FUNCTIONAL_R", 8), ("FINAL_RESEAL", 10),
                ("RELEASE_SUPPORT", 9),
            ),
        )
        counter = self._require("_count_task9_command_occurrences_v1")
        command_type = self._require("Task9CommandEvidenceV1")
        token = object.__new__(command_type)
        for bundle_id, expected in rows:
            shape = tuple(token for _ in range(expected))
            self.assertEqual(counter(shape), expected, bundle_id)
            self.assertEqual(counter((shape, shape)), expected * 2, bundle_id)

    def test_closed_formula_equals_decoder_cap_and_each_one_byte_overflow_fails_closed(self):
        command_cap = self._require("TASK9_COMMAND_CANONICAL_BYTE_CAP_V1")
        noncommand_cap = self._require(
            "TASK9_BUNDLE_NONCOMMAND_CANONICAL_BYTE_CAP_V1"
        )
        decoder_cap = self._require("TASK9_EVIDENCE_BUNDLE_DECODER_CAP_V1")
        self.assertEqual(
            (command_cap, noncommand_cap, decoder_cap),
            (4_194_304, 8_388_608, 150_994_944),
        )
        self.assertEqual(noncommand_cap + 34 * command_cap, decoder_cap)
        validate = self._require("_validate_task9_bundle_capacity_v1")
        validate("PREDECESSOR", (command_cap,) * 34, noncommand_cap, decoder_cap)
        for mutation in (
            ((command_cap + 1,) + (command_cap,) * 33, noncommand_cap, decoder_cap),
            ((command_cap,) * 34, noncommand_cap + 1, decoder_cap),
            ((command_cap,) * 34, noncommand_cap, decoder_cap + 1),
        ):
            with self.subTest(mutation=mutation[1:]):
                with self.assertRaises(Task9TransitionEvidenceError):
                    validate("PREDECESSOR", *mutation)
# TASK9_ROUND19_EVIDENCE_BUNDLE_CAPACITY_CONTRACT_END_V1


# TASK9_ROUND19_DECODER_CAPTURE_CAPACITY_REPAIR_CONTRACT_BEGIN_V1
class Round19DecoderCaptureCapacityRepairContractTests(
    _Round19CapacityAssertions, unittest.TestCase
):
    _V3_DECODER_SHA = (
        "8e229254772ad9af77c97ed256f54d5e5bd1dfe5097909f97d2e3976f1c5572e"
    )
    _V3_CAPTURE_SHA = (
        "7f5b1dd6828ee429435507c7321fa49c20a432692e8fa1394dbb8c4ea358f356"
    )

    def test_v3_decoder_table_is_exact_147_row_literal_with_only_nine_bundle_cap_substitutions(self):
        v3 = self._require("TASK9_EVIDENCE_DECODER_TABLE_V3")
        v2 = transition_evidence.TASK9_EVIDENCE_DECODER_TABLE_V2
        self.assertEqual(len(v3), 147)
        changed = tuple(index for index, pair in enumerate(zip(v2, v3)) if pair[0] != pair[1])
        self.assertEqual(changed, (22, 37, 40, 43, 46, 49, 52, 97, 118))
        for index, (old, new) in enumerate(zip(v2, v3)):
            if index in changed:
                self.assertEqual(old[:3] + old[4:], new[:3] + new[4:])
                self.assertEqual((old[3], new[3]), (16_777_216, 150_994_944))
            else:
                self.assertEqual(old, new)
        source = Path(transition_evidence.__file__).read_bytes()
        tree = ast.parse(source)
        assignments = tuple(
            node for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "TASK9_EVIDENCE_DECODER_TABLE_V3"
        )
        self.assertEqual(len(assignments), 1)
        self.assertIsInstance(assignments[0].value, ast.Tuple)

    def test_v3_decoder_table_and_capture_policy_match_all_exact_canonical_pins(self):
        rows_bytes = self._require(
            "task9_evidence_decoder_table_rows_json_bytes_v3"
        )()
        preimage = self._require(
            "task9_evidence_decoder_table_preimage_bytes_v3"
        )()
        self.assertEqual(
            (len(rows_bytes), hashlib.sha256(rows_bytes).hexdigest()),
            (23_501, "6c0165d33def319485c1af9b3e5de3cd475e9d82128ff72fde9dcb73a0389c68"),
        )
        self.assertEqual(
            (len(preimage), hashlib.sha256(preimage).hexdigest()),
            (23_529, "65974ea88043b725230531e47679e0b357eed85d4ca110b7b4b2bfaf81990855"),
        )
        self.assertEqual(
            self._require("task9_evidence_decoder_table_sha256_v3")(),
            self._V3_DECODER_SHA,
        )
        policy = self._require("TASK9_EVIDENCE_CAPTURE_POLICY_V3")
        projection = dict(policy)
        canonical = self._canonical(projection)
        self.assertEqual(
            (len(canonical), hashlib.sha256(canonical).hexdigest()),
            (2_571, "c65bd68e6288956e4103ed0a304afd11b11eeb86fa87523b506001a41efaa40e"),
        )
        self.assertEqual(
            self._require("task9_evidence_capture_policy_sha256_v3")(),
            self._V3_CAPTURE_SHA,
        )

    def test_active_paths_reject_v2_and_v2_v3_mixing_without_fallback(self):
        validate = self._require("_validate_task9_active_v3_digest_pair_v1")
        v2_capture = transition_evidence.task9_evidence_capture_policy_sha256_v2()
        v2_decoder = transition_evidence.task9_evidence_decoder_table_sha256_v2()
        validate(self._V3_CAPTURE_SHA, self._V3_DECODER_SHA)
        for pair in (
            (v2_capture, v2_decoder), (v2_capture, self._V3_DECODER_SHA),
            (self._V3_CAPTURE_SHA, v2_decoder), (None, self._V3_DECODER_SHA),
        ):
            with self.subTest(pair=pair):
                with self.assertRaises(Task9TransitionEvidenceError):
                    validate(*pair)

    def test_all_nine_semantic_maxima_sum_and_aggregate_accept_exact_reject_plus_one(self):
        maxima = self._require("TASK9_BUNDLE_SEMANTIC_MAXIMA_V1")
        self.assertEqual(
            maxima,
            (
                ("PREDECESSOR", 150_994_944),
                ("FUNCTIONAL_A", 117_440_512),
                ("FUNCTIONAL_B", 41_943_040),
                ("FUNCTIONAL_C", 41_943_040),
                ("FUNCTIONAL_D", 41_943_040),
                ("FUNCTIONAL_E", 41_943_040),
                ("FUNCTIONAL_R", 41_943_040),
                ("FINAL_RESEAL", 50_331_648),
                ("RELEASE_SUPPORT", 46_137_344),
            ),
        )
        self.assertEqual(sum(value for _name, value in maxima), 574_619_648)
        aggregate = self._require("TASK9_EVIDENCE_AGGREGATE_RETAINED_BYTE_CAP_V3")
        self.assertEqual(aggregate, 843_055_104)
        semantic = self._require("_validate_task9_bundle_semantic_size_v1")
        aggregate_check = self._require("_validate_task9_aggregate_capacity_v3")
        for bundle_id, maximum in maxima:
            semantic(bundle_id, maximum)
            with self.assertRaises(Task9TransitionEvidenceError):
                semantic(bundle_id, maximum + 1)
        aggregate_check(aggregate)
        with self.assertRaises(Task9TransitionEvidenceError):
            aggregate_check(aggregate + 1)

    def test_each_bundle_raw_row_accepts_exact_uniform_cap_and_rejects_plus_one_before_decode(self):
        validate = self._require("_validate_task9_capture_preallocation_v3")
        bundle_rows = tuple(
            row for row in transition_evidence.TASK9_EVIDENCE_DECODER_TABLE_V3
            if row[1] == "TASK9_EVIDENCE_BUNDLE_V1"
        )
        self.assertEqual(len(bundle_rows), 9)
        for row in bundle_rows:
            validate(row, 150_994_944, 0)
            with self.assertRaises(Task9TransitionEvidenceError):
                validate(row, 150_994_945, 0)

    def test_writer_rejects_semantic_and_aggregate_overflow_before_first_create(self):
        validate = self._require("_validate_task9_bundle_write_capacity_v3")
        validate("PREDECESSOR", 150_994_944, 268_435_456)
        for args in (
            ("PREDECESSOR", 150_994_945, 0),
            ("FUNCTIONAL_B", 41_943_041, 0),
            ("PREDECESSOR", 150_994_944, 692_060_161),
        ):
            with self.subTest(args=args):
                with self.assertRaises(Task9TransitionEvidenceError):
                    validate(*args)

    def test_snapshot_rejects_row_and_aggregate_overflow_before_allocation_read_or_decode(self):
        validate = self._require("_validate_task9_capture_preallocation_v3")
        row = next(
            item for item in transition_evidence.TASK9_EVIDENCE_DECODER_TABLE_V3
            if item[1] == "TASK9_EVIDENCE_BUNDLE_V1"
        )
        with self.assertRaises(Task9TransitionEvidenceError):
            validate(row, 150_994_945, 0)
        with self.assertRaises(Task9TransitionEvidenceError):
            validate(row, 1, 843_055_104)
        self.assertEqual(
            transition_evidence.TASK9_EVIDENCE_CAPTURE_POLICY_V3[
                "descriptor_size_check_step_ids"
            ][2:6],
            ("FSTAT_BEFORE_SIZE", "CHECK_ROW_CAP", "CHECK_SINGLE_FILE_CAP",
             "CHECK_AGGREGATE_REMAINING"),
        )

    def test_cache_receipt_validator_and_recovery_reject_mixed_digests(self):
        validate = self._require("_validate_task9_active_v3_digest_pair_v1")
        validators = (
            transition_evidence.validate_task9_evidence_root_snapshot_structure_v1,
            self._require("_task9_chain_validator_projection_sha256_v2"),
        )
        self.assertEqual(len(validators), 2)
        for pair in (
            (transition_evidence.task9_evidence_capture_policy_sha256_v2(),
             self._V3_DECODER_SHA),
            (self._V3_CAPTURE_SHA,
             transition_evidence.task9_evidence_decoder_table_sha256_v2()),
        ):
            with self.assertRaises(Task9TransitionEvidenceError):
                validate(*pair)

    def test_capacity_repair_adds_no_path_row_decoder_authority_network_or_order_surface(self):
        self._require("TASK9_EVIDENCE_DECODER_TABLE_V3")
        self.assertEqual(
            tuple(row[0] for row in transition_evidence.TASK9_EVIDENCE_DECODER_TABLE_V3),
            tuple(row[0] for row in transition_evidence.TASK9_EVIDENCE_DECODER_TABLE_V2),
        )
        self.assertEqual(
            transition_evidence.TASK9_BOOTSTRAP_PRODUCTION_REGISTRIES_V1, ()
        )
        self.assertEqual(
            transition_evidence.TASK9_BOOTSTRAP_NETWORK_CAPABILITIES_V1, ()
        )
        self.assertEqual(
            transition_evidence.TASK9_BOOTSTRAP_NETWORK_CALL_PATHS_V1, ()
        )
        for forbidden in (
            "set_task9_capture_cap", "set_task9_decoder_table",
            "issue_task9_capacity_authority", "task9_capacity_network_probe",
            "task9_capacity_order_executor",
        ):
            self.assertFalse(hasattr(transition_evidence, forbidden))
# TASK9_ROUND19_DECODER_CAPTURE_CAPACITY_REPAIR_CONTRACT_END_V1


class Task9TransitionEvidenceTests(unittest.TestCase):
    @staticmethod
    def _digest(domain, projection):
        payload = json.dumps(
            projection,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        return hashlib.sha256(domain.encode("ascii") + b"\0" + payload).hexdigest()

    def test_literal_decoder_table_matches_governed_round17_fingerprint(self):
        rows = validate_task9_evidence_decoder_table_v2(
            TASK9_EVIDENCE_DECODER_TABLE_V2
        )
        rows_bytes = task9_evidence_decoder_table_rows_json_bytes_v2()
        preimage = task9_evidence_decoder_table_preimage_bytes_v2()

        self.assertIs(rows, TASK9_EVIDENCE_DECODER_TABLE_V2)
        self.assertEqual(len(rows), 147)
        self.assertEqual(len(rows_bytes), 23_492)
        self.assertEqual(
            hashlib.sha256(rows_bytes).hexdigest(),
            "ac31842447a9e0e029cd77065121d2bc38c7a1ef18a5c2f1327b2a120b0c1903",
        )
        self.assertEqual(len(preimage), 23_520)
        self.assertEqual(
            hashlib.sha256(preimage).hexdigest(),
            "92b4c561070364fd6313d0fc0cfe53da2d8aab2f4c38841cf617e6ebca50fd9a",
        )
        self.assertEqual(
            task9_evidence_decoder_table_sha256_v2(),
            "2c30b4492eaf322127a9a53024b3dd6232f2b2ffc3292c943db4be2f7074be40",
        )

    def test_decoder_table_validator_rejects_reorder_and_field_drift(self):
        reordered = list(TASK9_EVIDENCE_DECODER_TABLE_V2)
        reordered[0], reordered[1] = reordered[1], reordered[0]
        drifted = list(TASK9_EVIDENCE_DECODER_TABLE_V2)
        drifted[0] = (*drifted[0][:-1], "NEVER")

        for candidate in (tuple(reordered), tuple(drifted)):
            with self.subTest(candidate=candidate[0]):
                with self.assertRaisesRegex(
                    Task9TransitionEvidenceError,
                    "^task9_evidence_structure_invalid$",
                ):
                    validate_task9_evidence_decoder_table_v2(candidate)

    def test_closed_stage_path_tables_cover_exactly_36_final_and_36_temp_paths(self):
        expected_stage_ids = (
            "PREDECESSOR_TRANSITION_MANIFEST",
            "PREDECESSOR_TRANSITION_REVIEW",
            "POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW",
            "FUNCTIONAL_WAVE_REVIEW_A",
            "FUNCTIONAL_WAVE_REVIEW_B",
            "FUNCTIONAL_WAVE_REVIEW_C",
            "FUNCTIONAL_WAVE_REVIEW_D",
            "FUNCTIONAL_WAVE_REVIEW_E",
            "FUNCTIONAL_WAVE_REVIEW_R",
            "FINAL_RESEAL_TRANSITION",
            "FINAL_RESEAL_REVIEW",
            "RELEASE_EVIDENCE",
        )
        self.assertEqual(
            tuple(stage.value for stage in Task9EvidenceStageIdV1),
            expected_stage_ids,
        )
        self.assertEqual(len(TASK9_EVIDENCE_STAGE_ROWS_V1), 12)
        self.assertEqual(len(TASK9_TRANSIENT_WRITE_PATHS_V1), 36)
        self.assertEqual(len(TASK9_STAGE_OWNED_PATHS_V1), 72)
        self.assertEqual(len(set(TASK9_TRANSIENT_WRITE_PATHS_V1)), 36)
        self.assertEqual(len(set(TASK9_STAGE_OWNED_PATHS_V1)), 72)
        self.assertEqual(
            tuple(path for row in TASK9_EVIDENCE_STAGE_ROWS_V1 for path in row[6:9]),
            TASK9_TRANSIENT_WRITE_PATHS_V1,
        )
        final_paths = tuple(
            path for row in TASK9_EVIDENCE_STAGE_ROWS_V1 for path in row[3:6]
        )
        self.assertEqual(TASK9_STAGE_OWNED_PATHS_V1, final_paths + TASK9_TRANSIENT_WRITE_PATHS_V1)
        self.assertTrue(all(len(path.encode("ascii")) <= 192 for path in TASK9_STAGE_OWNED_PATHS_V1))
        self.assertEqual(
            tuple(kind.value for kind in Task9StageOutputKindV1),
            ("ARTIFACT", "PROCEDURAL_ASSIGNMENT_RECEIPT", "CHAIN_ACCEPTANCE_RECEIPT"),
        )

    def test_literal_stage_contracts_have_exact_cardinalities_paths_and_governed_table(self):
        contracts = transition_evidence.TASK9_CHAIN_STAGE_CONTRACTS_V1
        expected_cardinalities = (
            (2, 0, 2, 0), (2, 0, 2, 1), (2, 4, 2, 2),
            (2, 0, 2, 1), (2, 0, 2, 1), (2, 0, 2, 1),
            (2, 0, 2, 1), (2, 0, 2, 1), (2, 0, 2, 1),
            (8, 0, 2, 9), (3, 4, 2, 1), (11, 5, 2, 11),
        )
        self.assertEqual(len(contracts), 12)
        self.assertEqual(
            tuple(
                (len(contract.O_rows) + len(contract.T_rows), len(contract.B_rows),
                 len(contract.S_rows), len(contract.P_rows))
                for contract in contracts
            ),
            expected_cardinalities,
        )
        for contract, path_row in zip(
            contracts, transition_evidence.TASK9_EVIDENCE_STAGE_ROWS_V1, strict=True
        ):
            self.assertEqual(contract.stage_id, path_row[0])
            self.assertEqual((contract.A[0], contract.W[0], contract.C[0]), path_row[3:6])
            self.assertEqual((contract.A[1], contract.W[1], contract.C[1]), path_row[6:9])
            projection = {
                "schema_version": contract.schema_version,
                "stage_id": contract.stage_id,
                "A": list(contract.A),
                "W": list(contract.W),
                "O_rows": [list(row) for row in contract.O_rows],
                "T_rows": [list(row) for row in contract.T_rows],
                "B_rows": [list(row) for row in contract.B_rows],
                "S_rows": [list(row) for row in contract.S_rows],
                "P_rows": [list(row) for row in contract.P_rows],
                "C": list(contract.C),
            }
            self.assertEqual(
                contract.stage_contract_sha256,
                self._digest("INCI-TASK-9-CHAIN-STAGE-CONTRACT-V1", projection),
            )
        table = transition_evidence.TASK9_CHAIN_STAGE_CONTRACT_TABLE_V1
        self.assertEqual(table.schema_version, 1)
        self.assertEqual(table.table_id, "TASK9_CHAIN_STAGE_CONTRACT_TABLE_V1")
        self.assertEqual(
            table.stage_contract_sha256s,
            tuple(contract.stage_contract_sha256 for contract in contracts),
        )

    def test_raw_sequence_contract_is_literal_stage_ordered_and_closed(self):
        row = transition_evidence.TASK9_RAW_SHA256_SEQUENCE_CONTRACT_ROW_V1
        governing_paths = (
            ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-controller-rulings.md",
            ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-implementation-adjudications.md",
            ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-preflight-map.md",
            ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-parallel-execution-brief.md",
        )
        expected_cardinalities = (0, 0, 4, 0, 0, 0, 0, 0, 0, 0, 4, 5)

        self.assertEqual(row.schema_version, 1)
        self.assertEqual(
            row.owner_type,
            "tools.task9_transition_evidence.Task9ChainAcceptanceReceiptV1",
        )
        self.assertEqual(row.field_name, "raw_evidence_content_sha256s")
        self.assertEqual(row.mode, "RAW_REF_SEQUENCE")
        self.assertIsNone(row.domain)
        self.assertEqual(row.projection_fields, ())
        self.assertEqual(row.self_exclusions, ())
        self.assertEqual(row.stage_id_field, "stage_id")
        self.assertEqual(row.sequence_order_rule, "TASK9_STAGE_CONTRACT_B_ROW_ORDER_V1")
        self.assertEqual(
            row.sequence_duplicate_rule,
            "FORBID_EXACT_OBJECT_PATH_AND_DIGEST_DUPLICATES",
        )
        self.assertEqual(
            tuple(stage.stage_id for stage in row.position_rows_by_stage),
            tuple(stage.value for stage in Task9EvidenceStageIdV1),
        )
        self.assertEqual(
            tuple(stage.cardinality for stage in row.position_rows_by_stage),
            expected_cardinalities,
        )
        self.assertEqual(
            tuple(position.relative_path for position in row.position_rows_by_stage[2].positions),
            governing_paths,
        )
        self.assertEqual(
            tuple(position.relative_path for position in row.position_rows_by_stage[10].positions),
            governing_paths,
        )
        self.assertEqual(
            tuple(position.relative_path for position in row.position_rows_by_stage[11].positions),
            governing_paths + ("docs/tennis_v1/README.md",),
        )
        for stage in row.position_rows_by_stage:
            self.assertEqual(stage.cardinality, len(stage.positions))
            for position in stage.positions:
                self.assertEqual(
                    position.reference_owner_type,
                    "tools.task9_transition_evidence.Task9EvidencePathSnapshotV1",
                )
                self.assertEqual(position.reference_field, "content_sha256")

    def test_weak_identity_classifier_purges_dead_repeats_same_and_rejects_collision(self):
        class Candidate:
            pass

        first = Candidate()
        entries = {7: weakref.ref(first)}
        self.assertEqual(
            transition_evidence._classify_task9_weak_identity_v1(
                entries, numeric_key=7, candidate=first
            ),
            "TERMINAL_REPEAT",
        )

        different = Candidate()
        with self.assertRaisesRegex(
            Task9TransitionEvidenceError,
            "^task9_evidence_weak_identity_collision$",
        ):
            transition_evidence._classify_task9_weak_identity_v1(
                entries, numeric_key=7, candidate=different
            )
        self.assertIs(entries[7](), first)

        del first
        self.assertIsNone(entries[7]())
        self.assertEqual(
            transition_evidence._classify_task9_weak_identity_v1(
                entries, numeric_key=7, candidate=different
            ),
            "NEW",
        )
        self.assertNotIn(7, entries)

        live_record = {"ref": weakref.ref(different), "state": "FRESH"}
        live_ledger = {id(different): live_record}
        self.assertIs(
            transition_evidence._task9_get_live_record_v1(
                live_ledger, different
            ),
            live_record,
        )

        collision_candidate = Candidate()
        collision_ledger = {id(collision_candidate): live_record}
        with self.assertRaisesRegex(
            Task9TransitionEvidenceError,
            "^task9_evidence_weak_identity_collision$",
        ):
            transition_evidence._task9_get_live_record_v1(
                collision_ledger, collision_candidate
            )

    def test_v2_promotion_policy_is_self_bound_and_forbids_unsafe_primitives(self):
        policy = TASK9_NO_REPLACE_PROMOTION_POLICY_V2
        self.assertEqual(policy.schema_version, 2)
        self.assertEqual(policy.policy_id, "TASK9_NO_REPLACE_PROMOTION_POLICY_V2")
        self.assertEqual(policy.supported_platforms, ("linux", "darwin"))
        self.assertEqual(policy.publication_primitive, "PYTHON_OS_LINK_SAME_DIRFD_NOFOLLOW")
        self.assertIn("OS_RENAME", policy.forbidden)
        self.assertIn("OS_REPLACE", policy.forbidden)
        self.assertIn("CALLER_ADAPTER", policy.forbidden)
        self.assertRegex(policy.policy_sha256, "^[0-9a-f]{64}$")

    def test_private_link_wrapper_normalizes_success_conflict_and_all_uncertainty(self):
        calls = []

        def invoke(outcome):
            original = transition_evidence._TASK9_OS_LINK_CALL_V1

            def spy(src, dst, *, src_dir_fd, dst_dir_fd, follow_symlinks):
                calls.append((src, dst, src_dir_fd, dst_dir_fd, follow_symlinks))
                if outcome == "eexist":
                    raise OSError(errno.EEXIST, "not exposed")
                if outcome == "oserror":
                    raise OSError(errno.EINTR, "not exposed")
                if outcome == "runtime":
                    raise RuntimeError("task9_test_non_oserror_sentinel")
                return None

            transition_evidence._TASK9_OS_LINK_CALL_V1 = spy
            try:
                return _call_task9_link_noreplace_v1(
                    root_fd=7,
                    temp_relative_path="temp",
                    final_relative_path="final",
                )
            finally:
                transition_evidence._TASK9_OS_LINK_CALL_V1 = original
                self.assertIs(transition_evidence._TASK9_OS_LINK_CALL_V1, original)

        self.assertIs(invoke("ok"), _Task9LinkCallOutcomeV1.LINK_CREATED)
        self.assertIs(invoke("eexist"), _Task9LinkCallOutcomeV1.FINAL_EXISTS)
        self.assertIs(invoke("oserror"), _Task9LinkCallOutcomeV1.CALL_UNCERTAIN)
        self.assertIs(invoke("runtime"), _Task9LinkCallOutcomeV1.CALL_UNCERTAIN)
        self.assertEqual(calls, [("temp", "final", 7, 7, False)] * 4)

    def test_promotion_rejects_same_size_content_drift_before_publication(self):
        with tempfile.TemporaryDirectory() as root:
            authority = transition_evidence._issue_task9_evidence_pair_write_authority_v1(
                root,
                stage_id=Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_B,
                write_mode="INITIAL",
            )
            payload = b"expected canonical bytes"
            record = transition_evidence._TASK9_PAIR_AUTHORITY_LEDGER[id(authority)]
            record["pending_payload"] = payload
            original_write_all = transition_evidence._task9_write_all

            def corrupt_same_size(fd, value):
                original_write_all(fd, b"X" * len(value))

            transition_evidence._task9_write_all = corrupt_same_size
            try:
                with self.assertRaisesRegex(
                    Task9TransitionEvidenceError,
                    "^task9_evidence_filesystem_fact_mismatch$",
                ):
                    transition_evidence._write_and_promote_task9_stage_output_v1(
                        authority,
                        output_kind=Task9StageOutputKindV1.ARTIFACT,
                        payload=payload,
                    )
            finally:
                transition_evidence._task9_write_all = original_write_all
                transition_evidence._TASK9_PAIR_AUTHORITY_LEDGER.pop(
                    id(authority), None
                )

            final_path = TASK9_EVIDENCE_STAGE_ROWS_V1[4][3]
            temp_path = TASK9_EVIDENCE_STAGE_ROWS_V1[4][6]
            self.assertFalse(Path(root, final_path).exists())
            self.assertFalse(Path(root, temp_path).exists())

    def test_transition_path_requires_exact_absent_present_sha_parity(self):
        present_sha = "a" * 64
        value = Task9TransitionPathV1(
            path="tools/task9_transition_evidence.py",
            before_state="ABSENT",
            before_sha256=None,
            after_state="PRESENT",
            after_sha256=present_sha,
        )
        self.assertIs(validate_task9_transition_path_structure_v1(value), value)

        for candidate in (
            Task9TransitionPathV1("x", "ABSENT", present_sha, "PRESENT", present_sha),
            Task9TransitionPathV1("x", "PRESENT", None, "PRESENT", present_sha),
            Task9TransitionPathV1("../x", "ABSENT", None, "PRESENT", present_sha),
        ):
            with self.assertRaisesRegex(
                Task9TransitionEvidenceError,
                "^task9_evidence_structure_invalid$",
            ):
                validate_task9_transition_path_structure_v1(candidate)

    def test_functional_review_parser_is_canonical_structural_and_restart_safe(self):
        binding_rows = (
            ("CONTROLLER_OPERATOR", "controller"),
            ("FUNCTIONAL_OWNER_B", "owner-b"),
            ("FUNCTIONAL_REVIEWER_B", "reviewer-b"),
        )
        bindings = []
        for role_id, local_label in binding_rows:
            binding_projection = {
                "schema_version": 1,
                "role_id": role_id,
                "local_label": local_label,
            }
            bindings.append(
                Task9ProceduralRoleBindingV1(
                    **binding_projection,
                    binding_sha256=self._digest(
                        "INCI-TASK-9-PROCEDURAL-ROLE-BINDING-V1",
                        binding_projection,
                    ),
                )
            )
        assignment_projection = {
            "schema_version": 1,
            "workflow_id": "TASK9",
            "assignment_scope": "FUNCTIONAL_WAVE_REVIEW_B",
            "controller_operator_label": "controller",
            "creator_controller_label": None,
            "role_bindings": [
                {
                    "schema_version": binding.schema_version,
                    "role_id": binding.role_id,
                    "local_label": binding.local_label,
                    "binding_sha256": binding.binding_sha256,
                }
                for binding in bindings
            ],
            "role_binding_sha256s": [binding.binding_sha256 for binding in bindings],
            "reviewer_label": "reviewer-b",
            "identity_assurance": "PROCEDURAL_LOCAL_ATTESTATION",
            "controller_operator_attested": True,
        }
        assignment = Task9ProceduralWorkflowAssignmentEvidenceV1(
            schema_version=1,
            workflow_id="TASK9",
            assignment_scope="FUNCTIONAL_WAVE_REVIEW_B",
            controller_operator_label="controller",
            creator_controller_label=None,
            role_bindings=tuple(bindings),
            role_binding_sha256s=tuple(
                binding.binding_sha256 for binding in bindings
            ),
            reviewer_label="reviewer-b",
            identity_assurance="PROCEDURAL_LOCAL_ATTESTATION",
            controller_operator_attested=True,
            assignment_sha256=self._digest(
                "INCI-TASK-9-PROCEDURAL-WORKFLOW-ASSIGNMENT-EVIDENCE-V1",
                assignment_projection,
            ),
        )
        artifact_projection = {
            "schema_version": 1,
            "wave_id": "B",
            "reviewer_id": "reviewer-b",
            "reviewed_tree_sha256": "b" * 64,
            "disposition": "CLEAN",
            "procedural_assignment_evidence": {
                **assignment_projection,
                "assignment_sha256": assignment.assignment_sha256,
            },
            "procedural_assignment_evidence_sha256": assignment.assignment_sha256,
            "identity_assurance": "PROCEDURAL_LOCAL_ATTESTATION",
            "controller_operator_attested": True,
        }
        artifact = Task9FunctionalWaveReviewV1(
            schema_version=1,
            wave_id="B",
            reviewer_id="reviewer-b",
            reviewed_tree_sha256="b" * 64,
            disposition="CLEAN",
            procedural_assignment_evidence=assignment,
            procedural_assignment_evidence_sha256=assignment.assignment_sha256,
            identity_assurance="PROCEDURAL_LOCAL_ATTESTATION",
            controller_operator_attested=True,
            review_sha256=self._digest(
                "INCI-TASK-9-FUNCTIONAL-WAVE-REVIEW-V1",
                artifact_projection,
            ),
        )
        self.assertIs(validate_task9_functional_wave_review_structure_v1(artifact), artifact)

        payload_projection = {
            **artifact_projection,
            "review_sha256": artifact.review_sha256,
        }
        payload = json.dumps(
            payload_projection,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        parsed = parse_task9_functional_wave_review_v1(payload)
        self.assertIsInstance(parsed, Task9FunctionalWaveReviewV1)
        self.assertEqual(parsed.review_sha256, artifact.review_sha256)

        for malformed in (
            payload + b"\n",
            payload.replace(b'"wave_id":"B"', b'"wave_id":"B","wave_id":"B"'),
        ):
            with self.assertRaisesRegex(
                Task9TransitionEvidenceError,
                "^task9_evidence_structure_invalid$",
            ):
                parse_task9_functional_wave_review_v1(malformed)

    def test_absent_path_snapshot_has_exact_null_parity_and_self_digest(self):
        projection = {
            "schema_version": 1,
            "relative_path": "task-9-release-evidence-v1.json",
            "state": "ABSENT",
            "device": None,
            "inode": None,
            "mode": None,
            "owner": None,
            "links": None,
            "size": None,
            "mtime_ns": None,
            "ctime_ns": None,
            "content_sha256": None,
        }
        value = Task9EvidencePathSnapshotV1(
            **projection,
            path_snapshot_sha256=self._digest(
                "INCI-TASK-9-EVIDENCE-PATH-SNAPSHOT-V1", projection
            ),
        )
        self.assertIs(validate_task9_evidence_path_snapshot_structure_v1(value), value)

        drifted = Task9EvidencePathSnapshotV1(
            **{**projection, "size": 0},
            path_snapshot_sha256=value.path_snapshot_sha256,
        )
        with self.assertRaisesRegex(
            Task9TransitionEvidenceError, "^task9_evidence_structure_invalid$"
        ):
            validate_task9_evidence_path_snapshot_structure_v1(drifted)

    def test_assignment_and_chain_receipt_parsers_require_exact_canonical_bytes(self):
        assignment_projection = {
            "schema_version": 1,
            "receipt_id": "task-9-procedural-assignment-write-receipt-v1",
            "stage_id": "FUNCTIONAL_WAVE_REVIEW_B",
            "artifact_family": "FUNCTIONAL_WAVE_REVIEW",
            "artifact_relative_path": "task-9-functional-wave-review-b-v1.json",
            "artifact_temp_relative_path": "task-9-functional-wave-review-b-v1.json.tmp-v1",
            "receipt_relative_path": "task-9-functional-wave-review-b-procedural-assignment-write-receipt-v1.json",
            "receipt_temp_relative_path": "task-9-functional-wave-review-b-procedural-assignment-write-receipt-v1.json.tmp-v1",
            "assignment_scope": "FUNCTIONAL_WAVE_REVIEW_B",
            "assignment_sha256": "1" * 64,
            "artifact_self_field": "review_sha256",
            "artifact_self_sha256": "2" * 64,
            "artifact_content_sha256": "3" * 64,
            "promotion_policy_sha256": TASK9_NO_REPLACE_PROMOTION_POLICY_V2.policy_sha256,
            "writer_projection_sha256": transition_evidence._task9_assignment_writer_projection_sha256_v2(
                "FUNCTIONAL_WAVE_REVIEW_B"
            ),
            "write_mode": "INITIAL",
        }
        assignment_payload_projection = {
            **assignment_projection,
            "receipt_sha256": self._digest(
                "INCI-TASK-9-PROCEDURAL-ASSIGNMENT-WRITE-RECEIPT-V1",
                assignment_projection,
            ),
        }
        assignment_payload = json.dumps(
            assignment_payload_projection,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        assignment_receipt = parse_task9_procedural_assignment_write_receipt_v1(
            assignment_payload
        )
        self.assertIsInstance(
            assignment_receipt, Task9ProceduralAssignmentWriteReceiptV1
        )

        chain_projection = {
            "schema_version": 1,
            "receipt_id": "task-9-chain-acceptance-receipt-v1",
            "stage_id": "FUNCTIONAL_WAVE_REVIEW_B",
            "artifact_family": "FUNCTIONAL_WAVE_REVIEW",
            "artifact_relative_path": "task-9-functional-wave-review-b-v1.json",
            "receipt_relative_path": "task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json",
            "receipt_temp_relative_path": "task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json.tmp-v1",
            "artifact_self_field": "review_sha256",
            "artifact_self_sha256": "2" * 64,
            "artifact_content_sha256": "3" * 64,
            "evidence_root_snapshot_sha256": "5" * 64,
            "procedural_assignment_write_receipt_sha256": assignment_receipt.receipt_sha256,
            "semantic_evidence_sha256s": ["6" * 64, "7" * 64],
            "raw_evidence_content_sha256s": [],
            "seal_sha256s": ["8" * 64, "9" * 64],
            "antecedent_chain_receipt_sha256s": ["a" * 64],
            "stage_contract_sha256": transition_evidence.TASK9_CHAIN_STAGE_CONTRACTS_V1[
                4
            ].stage_contract_sha256,
            "capture_policy_sha256": transition_evidence.task9_evidence_capture_policy_sha256_v2(),
            "decoder_table_sha256": task9_evidence_decoder_table_sha256_v2(),
            "promotion_policy_sha256": TASK9_NO_REPLACE_PROMOTION_POLICY_V2.policy_sha256,
            "acceptance": "ACCEPTED",
            "validator_projection_sha256": transition_evidence._task9_chain_validator_projection_sha256_v2(
                "FUNCTIONAL_WAVE_REVIEW_B"
            ),
        }
        chain_payload_projection = {
            **chain_projection,
            "receipt_sha256": self._digest(
                "INCI-TASK-9-CHAIN-ACCEPTANCE-RECEIPT-V1", chain_projection
            ),
        }
        chain_payload = json.dumps(
            chain_payload_projection,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        chain_receipt = parse_task9_chain_acceptance_receipt_v1(chain_payload)
        self.assertIsInstance(chain_receipt, Task9ChainAcceptanceReceiptV1)

        with tempfile.TemporaryDirectory() as root:
            receipt_path = Path(root, assignment_projection["receipt_relative_path"])
            receipt_path.write_bytes(assignment_payload)
            os.chmod(receipt_path, 0o600)
            authority = transition_evidence._issue_task9_evidence_root_authority_v1(
                root
            )
            snapshot = issue_task9_evidence_root_snapshot_v1(authority)
            try:
                first = read_task9_procedural_assignment_write_receipt_from_snapshot_v1(
                    snapshot,
                    stage_id=Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_B,
                )
                second = read_task9_procedural_assignment_write_receipt_from_snapshot_v1(
                    snapshot,
                    stage_id=Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_B,
                )
                self.assertIs(first, second)
                self.assertEqual(first.receipt_sha256, assignment_receipt.receipt_sha256)
            finally:
                close_task9_evidence_root_snapshot_v1(snapshot)

    def test_descriptor_snapshot_captures_closed_universe_and_closes_idempotently(self):
        with tempfile.TemporaryDirectory() as root:
            readme = Path(root, "docs", "tennis_v1", "README.md")
            readme.parent.mkdir(parents=True)
            readme.write_bytes(b"offline evidence\n")
            os.chmod(readme, 0o600)

            authority = transition_evidence._issue_task9_evidence_root_authority_v1(
                root
            )
            snapshot = issue_task9_evidence_root_snapshot_v1(authority)
            self.assertIs(
                validate_task9_evidence_root_snapshot_structure_v1(snapshot),
                snapshot,
            )
            self.assertEqual(snapshot.closed_path_count, 147)
            self.assertEqual(snapshot.present_path_count, 1)
            self.assertEqual(snapshot.captured_bytes_total, len(b"offline evidence\n"))
            self.assertEqual(snapshot.closed_temp_path_count, 36)
            self.assertEqual(snapshot.present_temp_path_count, 0)
            self.assertEqual(snapshot.transient_state, "CLEAN")
            self.assertIsNone(close_task9_evidence_root_snapshot_v1(snapshot))
            self.assertIsNone(close_task9_evidence_root_snapshot_v1(snapshot))

    def test_missing_snapshot_evidence_latches_terminal_decode_failure(self):
        with tempfile.TemporaryDirectory() as root:
            readme = Path(root, "docs", "tennis_v1", "README.md")
            readme.parent.mkdir(parents=True)
            readme.write_bytes(b"retained bytes\n")
            os.chmod(readme, 0o600)
            authority = transition_evidence._issue_task9_evidence_root_authority_v1(
                root
            )
            snapshot = issue_task9_evidence_root_snapshot_v1(authority)
            parser_calls = []

            try:
                with self.assertRaisesRegex(
                    Task9TransitionEvidenceError,
                    "^task9_evidence_decode_invalid$",
                ):
                    read_task9_procedural_assignment_write_receipt_from_snapshot_v1(
                        snapshot,
                        stage_id=Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_B,
                    )

                with self.assertRaisesRegex(
                    Task9TransitionEvidenceError,
                    "^task9_evidence_decode_invalid$",
                ):
                    transition_evidence._task9_read_snapshot_decoded(
                        snapshot,
                        relative_path="docs/tennis_v1/README.md",
                        decoder_id="TASK9_RAW_GOVERNED_BYTES_V1",
                        parser=lambda payload: parser_calls.append(payload),
                    )
                self.assertEqual(parser_calls, [])
            finally:
                close_task9_evidence_root_snapshot_v1(snapshot)

    def test_initial_pair_writer_creates_artifact_then_assignment_receipt_once(self):
        bindings = []
        for role_id, local_label in (
            ("CONTROLLER_OPERATOR", "controller"),
            ("FUNCTIONAL_OWNER_B", "owner-b"),
            ("FUNCTIONAL_REVIEWER_B", "reviewer-b"),
        ):
            projection = {
                "schema_version": 1,
                "role_id": role_id,
                "local_label": local_label,
            }
            bindings.append(
                Task9ProceduralRoleBindingV1(
                    **projection,
                    binding_sha256=self._digest(
                        "INCI-TASK-9-PROCEDURAL-ROLE-BINDING-V1", projection
                    ),
                )
            )
        bindings = tuple(bindings)
        attestation = transition_evidence._issue_task9_procedural_attestation_authority_v1(
            assignment_scope="FUNCTIONAL_WAVE_REVIEW_B",
            controller_operator_label="controller",
            creator_controller_label=None,
            role_bindings=bindings,
            reviewer_label="reviewer-b",
        )
        assignment = issue_task9_procedural_workflow_assignment_evidence_v1(
            attestation,
            assignment_scope=transition_evidence.Task9ProceduralAssignmentScopeV1.FUNCTIONAL_WAVE_REVIEW_B,
            controller_operator_label="controller",
            creator_controller_label=None,
            role_bindings=bindings,
            reviewer_label="reviewer-b",
        )
        artifact_projection = {
            "schema_version": 1,
            "wave_id": "B",
            "reviewer_id": "reviewer-b",
            "reviewed_tree_sha256": "e" * 64,
            "disposition": "CLEAN",
            "procedural_assignment_evidence": transition_evidence._task9_public_projection(
                assignment
            ),
            "procedural_assignment_evidence_sha256": assignment.assignment_sha256,
            "identity_assurance": "PROCEDURAL_LOCAL_ATTESTATION",
            "controller_operator_attested": True,
        }
        artifact = Task9FunctionalWaveReviewV1(
            schema_version=1,
            wave_id="B",
            reviewer_id="reviewer-b",
            reviewed_tree_sha256="e" * 64,
            disposition="CLEAN",
            procedural_assignment_evidence=assignment,
            procedural_assignment_evidence_sha256=assignment.assignment_sha256,
            identity_assurance="PROCEDURAL_LOCAL_ATTESTATION",
            controller_operator_attested=True,
            review_sha256=self._digest(
                "INCI-TASK-9-FUNCTIONAL-WAVE-REVIEW-V1", artifact_projection
            ),
        )
        with tempfile.TemporaryDirectory() as root:
            pair = transition_evidence._issue_task9_evidence_pair_write_authority_v1(
                root,
                stage_id=Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_B,
                write_mode="INITIAL",
            )
            reservation = issue_task9_procedural_assignment_reservation_v1(
                assignment, artifact, write_authority=pair
            )
            receipt = write_task9_functional_wave_review_v1(
                artifact,
                wave_id=Task9FunctionalWaveIdV1.B,
                reservation=reservation,
                write_authority=pair,
            )
            self.assertTrue(Path(root, "task-9-functional-wave-review-b-v1.json").is_file())
            self.assertTrue(Path(root, receipt.receipt_relative_path).is_file())
            self.assertFalse(Path(root, receipt.artifact_temp_relative_path).exists())
            self.assertFalse(Path(root, receipt.receipt_temp_relative_path).exists())
            with self.assertRaisesRegex(
                Task9TransitionEvidenceError,
                "^task9_procedural_assignment_reservation_consumed$",
            ):
                write_task9_functional_wave_review_v1(
                    artifact,
                    wave_id=Task9FunctionalWaveIdV1.B,
                    reservation=reservation,
                    write_authority=pair,
                )
            artifact_path = Path(root, "task-9-functional-wave-review-b-v1.json")
            original_artifact_bytes = artifact_path.read_bytes()
            Path(root, receipt.receipt_relative_path).unlink()
            recovery_attestation = transition_evidence._issue_task9_procedural_attestation_authority_v1(
                assignment_scope="FUNCTIONAL_WAVE_REVIEW_B",
                controller_operator_label="controller",
                creator_controller_label=None,
                role_bindings=bindings,
                reviewer_label="reviewer-b",
            )
            recovery_pair = transition_evidence._issue_task9_evidence_pair_write_authority_v1(
                root,
                stage_id=Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_B,
                write_mode="RECOVERY",
            )
            recovery_reservation = (
                issue_task9_procedural_assignment_recovery_reservation_v1(
                    recovery_attestation,
                    stage_id=Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_B,
                    write_authority=recovery_pair,
                )
            )
            recovered = recover_task9_procedural_assignment_write_receipt_v1(
                recovery_reservation, write_authority=recovery_pair
            )
            self.assertEqual(recovered.write_mode, "RECOVERY")
            self.assertEqual(artifact_path.read_bytes(), original_artifact_bytes)


if __name__ == "__main__":
    unittest.main()
