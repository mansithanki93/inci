from __future__ import annotations

import copy
from hashlib import sha256
import json
import os
from pathlib import Path
import pickle
import pwd
import stat
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import warnings

from process_lock import ProcessLock, ProcessLockError

import inci_tennis_io.account_lock as account_lock_module
from inci_tennis_io.account_lock import (
    CandidateAccountLockV1,
    ProductionAccountLockError,
    ProductionAccountLockGrantV1,
    ProductionAccountLockLeaseV1,
    LockedProductionStateRootsV1,
    acquire_production_account_lock,
    consume_production_account_lock,
    derive_locked_production_state_roots_v1,
    derive_production_account_lock_path,
    release_production_account_lock,
    revoke_production_account_lock_grant,
)


_ROOT_HOLDER = r"""
import os
from pathlib import Path
import sys
import time

from process_lock import ProcessLock

os.umask(0o022)
lock = ProcessLock(sys.argv[1])
lock.acquire()
Path(sys.argv[2]).write_bytes(b"ready")
try:
    while not Path(sys.argv[3]).exists():
        time.sleep(0.01)
finally:
    lock.release()
"""


_ROOT_TRY = r"""
import sys

from process_lock import ProcessLock, ProcessLockError

lock = ProcessLock(sys.argv[1])
try:
    lock.acquire()
except ProcessLockError:
    raise SystemExit(23)
else:
    lock.release()
    raise SystemExit(0)
"""


_UNCERTAINTY_CHILD = r"""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import inci_tennis_io.account_lock as module

operation = sys.argv[1]
with tempfile.TemporaryDirectory() as temporary:
    home = Path(temporary).resolve(strict=True)
    os.chmod(home, 0o700)
    module.pwd.getpwuid = lambda _: SimpleNamespace(pw_dir=str(home))
    grant = module.acquire_production_account_lock(
        environment="production",
        subaccount=0,
    )
    lease = None
    if operation == "release":
        lease = module.consume_production_account_lock(grant)
    real_close = module.os.close
    real_open = module.os.open
    failed = False

    def close_then_fail(fd):
        global failed
        real_close(fd)
        if not failed:
            failed = True
            raise OSError("private descriptor detail")

    module.os.close = close_then_fail
    try:
        if operation == "release":
            module.release_production_account_lock(lease)
        else:
            module.revoke_production_account_lock_grant(grant)
    except module.ProductionAccountLockError as error:
        first = str(error)
    else:
        first = "unexpected-success"
    opened = False

    def forbidden_open(*args, **kwargs):
        global opened
        opened = True
        raise AssertionError("halted acquisition performed open")

    module.os.open = forbidden_open
    try:
        module.acquire_production_account_lock(
            environment="production",
            subaccount=1,
        )
    except module.ProductionAccountLockError as error:
        second = str(error)
    else:
        second = "unexpected-success"
    print(first)
    print(second)
    print("opened" if opened else "no-open")
    module.os.open = real_open
    module.os.close = real_close
"""


class _StringSubclass(str):
    pass


class _IntSubclass(int):
    pass


class _ProductionAccountLockCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve(strict=True)
        os.chmod(self.home, 0o700)
        self.control_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.control_temporary.cleanup)
        self.control = Path(self.control_temporary.name).resolve(strict=True)
        self.real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        self.passwd_patch = mock.patch.object(
            account_lock_module.pwd,
            "getpwuid",
            return_value=SimpleNamespace(pw_dir=str(self.home)),
        )
        self.passwd_mock = self.passwd_patch.start()
        self.addCleanup(self.passwd_patch.stop)
        self._grants: list[ProductionAccountLockGrantV1] = []
        self._leases: list[ProductionAccountLockLeaseV1] = []
        self._children: list[tuple[subprocess.Popen[str], Path]] = []
        self.addCleanup(self._cleanup_children)
        self.addCleanup(self._cleanup_capabilities)

    def _cleanup_capabilities(self) -> None:
        for lease in reversed(self._leases):
            try:
                release_production_account_lock(lease)
            except ProductionAccountLockError:
                pass
        for grant in reversed(self._grants):
            try:
                revoke_production_account_lock_grant(grant)
            except ProductionAccountLockError:
                pass

    def _cleanup_children(self) -> None:
        for process, stop in reversed(self._children):
            try:
                stop.write_bytes(b"stop")
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

    def _path(self, subaccount: int = 0) -> Path:
        return derive_production_account_lock_path(
            environment="production",
            subaccount=subaccount,
        )

    def _acquire(self, subaccount: int = 0) -> ProductionAccountLockGrantV1:
        grant = acquire_production_account_lock(
            environment="production",
            subaccount=subaccount,
        )
        self._grants.append(grant)
        return grant

    def _consume(
        self,
        grant: ProductionAccountLockGrantV1,
    ) -> ProductionAccountLockLeaseV1:
        lease = consume_production_account_lock(grant)
        self._leases.append(lease)
        return lease

    def _make_parent(
        self,
        subaccount: int = 0,
        *,
        mode: int = 0o700,
    ) -> Path:
        directory = self._path(subaccount).parent
        current = self.home
        for component in directory.relative_to(self.home).parts:
            current /= component
            current.mkdir(exist_ok=True)
            os.chmod(current, mode)
        return directory

    def _child_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment["PYTHONPYCACHEPREFIX"] = (
            f"/tmp/inci-task9-production-account-lock-{os.getpid()}"
        )
        return environment

    def _start_root_holder(self, path: Path) -> subprocess.Popen[str]:
        token = f"{len(self._children)}-{time.monotonic_ns()}"
        ready = self.control / f"ready-{token}"
        stop = self.control / f"stop-{token}"
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                _ROOT_HOLDER,
                str(path),
                str(ready),
                str(stop),
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=self._child_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._children.append((process, stop))
        deadline = time.monotonic() + 5
        while not ready.exists():
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=1)
                self.fail(
                    "root ProcessLock holder exited before readiness: "
                    f"{process.returncode}; stdout={stdout!r}; stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                self.fail("root ProcessLock holder readiness timeout")
            time.sleep(0.01)
        return process

    def _stop_root_holder(self, process: subprocess.Popen[str]) -> None:
        for child, stop in self._children:
            if child is process:
                stop.write_bytes(b"stop")
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, "")
                self._children.remove((child, stop))
                return
        self.fail("unknown root ProcessLock holder")

    def _assert_error(self, expected: str, callable_) -> None:
        with self.assertRaises(ProductionAccountLockError) as caught:
            callable_()
        self.assertEqual(str(caught.exception), expected)
        self.assertNotIn(str(self.home), str(caught.exception))
        self.assertNotIn(str(self.real_home), str(caught.exception))


class ProductionAccountLockPathTests(_ProductionAccountLockCase):
    def test_path_formula_bounds_and_argument_precedence(self) -> None:
        for subaccount in (0, 32):
            with self.subTest(subaccount=subaccount):
                self.assertEqual(
                    self._path(subaccount),
                    self.home
                    / ".local"
                    / "state"
                    / "inci"
                    / "production"
                    / f"subaccount-{subaccount}"
                    / "inci.lock",
                )
        invalid = (
            (None, 0, "production_account_lock_environment_invalid"),
            ("demo", 0, "production_account_lock_environment_invalid"),
            (
                _StringSubclass("production"),
                0,
                "production_account_lock_environment_invalid",
            ),
            ("production", True, "production_account_lock_subaccount_invalid"),
            ("production", -1, "production_account_lock_subaccount_invalid"),
            ("production", 33, "production_account_lock_subaccount_invalid"),
            ("production", 1.0, "production_account_lock_subaccount_invalid"),
            (
                "production",
                _IntSubclass(1),
                "production_account_lock_subaccount_invalid",
            ),
        )
        real_open = os.open
        for environment, subaccount, error in invalid:
            with self.subTest(environment=environment, subaccount=subaccount):
                self.passwd_mock.reset_mock()
                with mock.patch.object(
                    account_lock_module.os,
                    "open",
                    wraps=real_open,
                ) as open_spy:
                    self._assert_error(
                        error,
                        lambda: derive_production_account_lock_path(
                            environment=environment,  # type: ignore[arg-type]
                            subaccount=subaccount,  # type: ignore[arg-type]
                        ),
                    )
                self.passwd_mock.assert_not_called()
                open_spy.assert_not_called()

    def test_passwd_home_lexical_contract_and_ambient_paths_are_ignored(
        self,
    ) -> None:
        invalid = (
            "",
            "relative/home",
            f"{self.home}/",
            str(self.home / ".." / self.home.name),
            _StringSubclass(str(self.home)),
        )
        for value in invalid:
            with self.subTest(value=repr(value)):
                self.passwd_mock.return_value = SimpleNamespace(pw_dir=value)
                self._assert_error(
                    "production_account_lock_passwd_home_invalid",
                    lambda: self._path(),
                )
        self.passwd_mock.return_value = SimpleNamespace(pw_dir=str(self.home))
        expected = self._path()
        other_home = self.home / "environment-home"
        other_cwd = self.home / "cwd"
        other_home.mkdir()
        other_cwd.mkdir()
        original = Path.cwd()
        try:
            with mock.patch.dict(
                os.environ,
                {"HOME": str(other_home)},
                clear=False,
            ):
                os.chdir(other_cwd)
                self.assertEqual(self._path(), expected)
        finally:
            os.chdir(original)

    def test_platform_gate_fails_before_filesystem_open(self) -> None:
        real_open = os.open
        cases = (
            (account_lock_module.os, "O_NOFOLLOW"),
            (account_lock_module.os, "O_CLOEXEC"),
            (account_lock_module.os, "O_DIRECTORY"),
            (account_lock_module.os, "fstat"),
            (account_lock_module.os, "fchmod"),
            (account_lock_module.os, "fsync"),
            (account_lock_module.os, "close"),
            (account_lock_module.fcntl, "LOCK_EX"),
            (account_lock_module.fcntl, "LOCK_NB"),
            (account_lock_module.fcntl, "LOCK_UN"),
            (account_lock_module.fcntl, "flock"),
        )
        for namespace, name in cases:
            with self.subTest(name=name), mock.patch.object(
                namespace,
                name,
                None,
            ), mock.patch.object(
                account_lock_module.os,
                "open",
                wraps=real_open,
            ) as open_spy:
                self._assert_error(
                    "production_account_lock_platform_unsupported",
                    lambda: self._acquire(),
                )
                open_spy.assert_not_called()

    def test_new_file_is_exact_0600_empty_and_fsynced_before_return(self) -> None:
        real_fsync = os.fsync
        observations: list[str] = []

        def fsync_spy(fd: int) -> None:
            value = os.fstat(fd)
            observations.append(
                "file" if stat.S_ISREG(value.st_mode) else "directory"
            )
            real_fsync(fd)

        old_umask = os.umask(0o777)
        try:
            with mock.patch.object(
                account_lock_module.os,
                "fsync",
                side_effect=fsync_spy,
            ):
                grant = self._acquire()
        finally:
            os.umask(old_umask)
        path = self._path()
        value = path.stat()
        self.assertEqual(stat.S_IMODE(value.st_mode), 0o600)
        self.assertEqual(value.st_nlink, 1)
        self.assertEqual(path.read_bytes(), b"")
        self.assertEqual(observations, ["file", "directory"])
        revoke_production_account_lock_grant(grant)

    def test_existing_0600_and_0644_pid_bytes_and_mode_are_preserved(
        self,
    ) -> None:
        for subaccount, mode in enumerate((0o600, 0o644)):
            with self.subTest(mode=oct(mode)):
                parent = self._make_parent(subaccount, mode=0o755)
                path = parent / "inci.lock"
                content = f"pid-{subaccount}\n".encode()
                path.write_bytes(content)
                os.chmod(path, mode)
                with mock.patch.object(
                    account_lock_module.os,
                    "fchmod",
                    wraps=os.fchmod,
                ) as chmod_spy:
                    grant = self._acquire(subaccount)
                    revoke_production_account_lock_grant(grant)
                chmod_spy.assert_not_called()
                self.assertEqual(path.read_bytes(), content)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def test_existing_file_mode_type_owner_and_link_matrix_is_exact(self) -> None:
        cases = (
            ("0400", 0o400),
            ("0444", 0o444),
            ("0640", 0o640),
            ("0660", 0o660),
            ("0700", 0o700),
        )
        for subaccount, (label, mode) in enumerate(cases):
            with self.subTest(case=label):
                path = self._make_parent(subaccount) / "inci.lock"
                path.write_bytes(b"pid")
                os.chmod(path, mode)
                self._assert_error(
                    "production_account_lock_file_invalid",
                    lambda value=subaccount: self._acquire(value),
                )
        for offset, case in enumerate(("symlink", "hardlink", "directory"), 10):
            with self.subTest(case=case):
                parent = self._make_parent(offset)
                path = parent / "inci.lock"
                if case == "symlink":
                    target = parent / "target"
                    target.write_bytes(b"x")
                    os.symlink(target, path)
                elif case == "hardlink":
                    path.write_bytes(b"x")
                    os.chmod(path, 0o600)
                    os.link(path, parent / "second")
                else:
                    path.mkdir()
                self._assert_error(
                    "production_account_lock_file_invalid",
                    lambda value=offset: self._acquire(value),
                )

    def test_unsafe_directory_and_file_owner_are_rejected(self) -> None:
        local = self.home / ".local"
        local.mkdir()
        os.chmod(local, 0o770)
        self._assert_error(
            "production_account_lock_directory_invalid",
            lambda: self._acquire(),
        )
        os.chmod(local, 0o700)
        with mock.patch.object(
            account_lock_module.os,
            "geteuid",
            return_value=os.geteuid() + 1,
        ):
            self._assert_error(
                "production_account_lock_directory_invalid",
                lambda: self._acquire(),
            )
        local.rmdir()
        path = self._make_parent() / "inci.lock"
        path.write_bytes(b"pid")
        os.chmod(path, 0o600)
        real_fstat = os.fstat

        def wrong_file_owner(fd: int):
            value = real_fstat(fd)
            if not stat.S_ISREG(value.st_mode):
                return value
            fields = list(value)
            fields[4] = os.geteuid() + 1
            return os.stat_result(fields)

        with mock.patch.object(
            account_lock_module.os,
            "fstat",
            side_effect=wrong_file_owner,
        ):
            self._assert_error(
                "production_account_lock_file_invalid",
                lambda: self._acquire(),
            )

    def test_descriptor_operations_remain_under_passwd_home(self) -> None:
        real_open = os.open
        real_mkdir = os.mkdir
        fd_paths: dict[int, Path] = {}
        operations: list[Path] = []

        def resolve(path, dir_fd):
            raw = os.fspath(path)
            candidate = Path(raw)
            if candidate.is_absolute():
                return Path(os.path.normpath(raw))
            parent = fd_paths.get(dir_fd)
            if parent is None:
                self.fail(f"untracked descriptor-relative path: {raw!r}")
            return Path(os.path.normpath(str(parent / raw)))

        def open_spy(path, flags, mode=0o777, *, dir_fd=None):
            resolved = resolve(path, dir_fd)
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            fd_paths[descriptor] = resolved
            operations.append(resolved)
            return descriptor

        def mkdir_spy(path, mode=0o777, *, dir_fd=None):
            resolved = resolve(path, dir_fd)
            result = real_mkdir(path, mode, dir_fd=dir_fd)
            operations.append(resolved)
            return result

        with mock.patch.object(
            account_lock_module.os,
            "open",
            side_effect=open_spy,
        ), mock.patch.object(
            account_lock_module.os,
            "mkdir",
            side_effect=mkdir_spy,
        ):
            grant = self._acquire()
            revoke_production_account_lock_grant(grant)
        self.assertTrue(operations)
        for path in operations:
            self.assertEqual(
                os.path.commonpath((str(self.home), str(path))),
                str(self.home),
            )
            if self.real_home != self.home:
                self.assertNotEqual(
                    os.path.commonpath((str(self.real_home), str(path))),
                    str(self.real_home),
                )

    def test_contention_is_redacted_and_preserves_existing_bytes(self) -> None:
        path = self._path()
        process = self._start_root_holder(path)
        before = path.read_bytes()
        before_mode = stat.S_IMODE(path.stat().st_mode)
        self._assert_error(
            "production_account_lock_contended",
            lambda: self._acquire(),
        )
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), before_mode)
        self._stop_root_holder(process)

    def test_post_flock_directory_and_lock_replacement_are_detected(self) -> None:
        for subaccount, target in ((20, "directory"), (21, "lock")):
            with self.subTest(target=target):
                real_flock = account_lock_module.fcntl.flock
                replaced = False
                path = self._path(subaccount)

                def racing_flock(fd: int, operation: int) -> None:
                    nonlocal replaced
                    real_flock(fd, operation)
                    if operation & account_lock_module.fcntl.LOCK_NB and not replaced:
                        replaced = True
                        if target == "lock":
                            path.rename(path.with_name("inci.lock-held"))
                            path.write_bytes(b"replacement")
                            os.chmod(path, 0o600)
                        else:
                            local = self.home / ".local"
                            local.rename(self.home / ".local-held")
                            local.mkdir()
                            os.chmod(local, 0o700)

                with mock.patch.object(
                    account_lock_module.fcntl,
                    "flock",
                    side_effect=racing_flock,
                ):
                    self._assert_error(
                        "production_account_lock_path_replaced",
                        lambda value=subaccount: self._acquire(value),
                    )


class ProductionAccountLockLifecycleTests(_ProductionAccountLockCase):
    def test_grant_and_lease_are_opaque_and_candidate_is_rejected(self) -> None:
        for capability in (
            ProductionAccountLockGrantV1,
            ProductionAccountLockLeaseV1,
        ):
            with self.subTest(capability=capability.__name__):
                with self.assertRaises(TypeError):
                    capability()
                with self.assertRaises(TypeError):
                    type("Forbidden", (capability,), {})
        grant = self._acquire()
        self.assertIn("redacted", repr(grant))
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(TypeError):
                    operation(grant)
        candidate = object.__new__(CandidateAccountLockV1)
        self._assert_error(
            "production_account_lock_grant_invalid",
            lambda: consume_production_account_lock(candidate),  # type: ignore[arg-type]
        )
        self._assert_error(
            "production_account_lock_grant_invalid",
            lambda: revoke_production_account_lock_grant(candidate),  # type: ignore[arg-type]
        )
        self._assert_error(
            "production_account_lock_lease_invalid",
            lambda: derive_locked_production_state_roots_v1(candidate),  # type: ignore[arg-type]
        )
        self._assert_error(
            "production_account_lock_lease_invalid",
            lambda: release_production_account_lock(candidate),  # type: ignore[arg-type]
        )

    def test_consume_moves_one_held_grant_to_one_lease(self) -> None:
        grant = self._acquire()
        lease = self._consume(grant)
        self.assertIs(type(lease), ProductionAccountLockLeaseV1)
        self.assertIn("redacted", repr(lease))
        self._assert_error(
            "production_account_lock_grant_consumed",
            lambda: consume_production_account_lock(grant),
        )
        self._assert_error(
            "production_account_lock_grant_consumed",
            lambda: revoke_production_account_lock_grant(grant),
        )
        contender = ProcessLock(str(self._path()))
        with self.assertRaises(ProcessLockError):
            contender.acquire()
        contender.release()
        release_production_account_lock(lease)

    def test_revoke_is_exact_once_and_terminal(self) -> None:
        grant = self._acquire()
        revoke_production_account_lock_grant(grant)
        self._assert_error(
            "production_account_lock_grant_revoked",
            lambda: revoke_production_account_lock_grant(grant),
        )
        self._assert_error(
            "production_account_lock_grant_revoked",
            lambda: consume_production_account_lock(grant),
        )
        root = ProcessLock(str(self._path()))
        root.acquire()
        root.release()

    def test_wrong_thread_cannot_consume_or_revoke_grant(self) -> None:
        grant = self._acquire()
        errors: list[str] = []

        def consume() -> None:
            try:
                consume_production_account_lock(grant)
            except ProductionAccountLockError as error:
                errors.append(str(error))

        thread = threading.Thread(target=consume)
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ["production_account_lock_grant_invalid"])
        contender = ProcessLock(str(self._path()))
        with self.assertRaises(ProcessLockError):
            contender.acquire()
        contender.release()
        revoke_production_account_lock_grant(grant)

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    def test_fork_child_cannot_consume_parent_grant(self) -> None:
        grant = self._acquire()
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            try:
                warnings.simplefilter("ignore", ResourceWarning)
                os.close(read_fd)
                try:
                    consume_production_account_lock(grant)
                except ProductionAccountLockError as error:
                    payload = str(error).encode("ascii")
                else:
                    payload = b"unexpected-success"
                os.write(write_fd, payload)
            finally:
                os._exit(0)
        os.close(write_fd)
        payload = os.read(read_fd, 256)
        os.close(read_fd)
        _, status = os.waitpid(child, 0)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(payload, b"production_account_lock_grant_invalid")
        revoke_production_account_lock_grant(grant)

    def test_release_is_exact_once_with_zero_retry_io(self) -> None:
        lease = self._consume(self._acquire())
        release_production_account_lock(lease)
        with mock.patch.object(
            account_lock_module.fcntl,
            "flock",
            side_effect=AssertionError("unexpected flock retry"),
        ), mock.patch.object(
            account_lock_module.os,
            "close",
            side_effect=AssertionError("unexpected close retry"),
        ):
            self._assert_error(
                "production_account_lock_lease_invalid",
                lambda: release_production_account_lock(lease),
            )

    def test_wrong_thread_cannot_release_lease(self) -> None:
        lease = self._consume(self._acquire())
        errors: list[str] = []

        def release() -> None:
            try:
                release_production_account_lock(lease)
            except ProductionAccountLockError as error:
                errors.append(str(error))

        thread = threading.Thread(target=release)
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ["production_account_lock_lease_invalid"])
        release_production_account_lock(lease)

    def test_roots_are_exact_digest_bound_and_invalid_after_release(self) -> None:
        lease = self._consume(self._acquire(3))
        roots = derive_locked_production_state_roots_v1(lease)
        self.assertIs(type(roots), LockedProductionStateRootsV1)
        self.assertEqual(roots.environment, "production")
        self.assertEqual(roots.subaccount, 3)
        self.assertEqual(roots.phase1_state_root, self._path(3).parent / "tennis-v1")
        self.assertEqual(
            roots.expert_state_root,
            self._path(3).parent / "tennis-v1" / "expert-v1",
        )
        projection = {
            "environment": "production",
            "subaccount": 3,
            "phase1_state_root": str(roots.phase1_state_root),
            "expert_state_root": str(roots.expert_state_root),
        }
        canonical = json.dumps(
            projection,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        expected = sha256(
            b"INCI-LOCKED-PRODUCTION-STATE-ROOTS-V1\0" + canonical
        ).hexdigest()
        self.assertEqual(roots.roots_sha256, expected)
        self.assertIs(derive_locked_production_state_roots_v1(lease), roots)
        self.assertIs(
            account_lock_module._require_locked_production_state_roots_v1(
                roots,
                lease,
            ),
            roots,
        )
        release_production_account_lock(lease)
        self._assert_error(
            "production_account_roots_invalid",
            lambda: account_lock_module._require_locked_production_state_roots_v1(
                roots,
                lease,
            ),
        )

    def test_roots_reject_forged_and_other_lease_objects(self) -> None:
        lease_a = self._consume(self._acquire(4))
        lease_b = self._consume(self._acquire(5))
        roots = derive_locked_production_state_roots_v1(lease_a)
        forged = object.__new__(LockedProductionStateRootsV1)
        self._assert_error(
            "production_account_roots_invalid",
            lambda: account_lock_module._require_locked_production_state_roots_v1(
                forged,
                lease_a,
            ),
        )
        self._assert_error(
            "production_account_roots_invalid",
            lambda: account_lock_module._require_locked_production_state_roots_v1(
                roots,
                lease_b,
            ),
        )
        release_production_account_lock(lease_b)
        release_production_account_lock(lease_a)

    def test_release_path_replacement_is_terminal_after_cleanup(self) -> None:
        lease = self._consume(self._acquire())
        path = self._path()
        held = path.with_name("inci.lock-held")
        path.rename(held)
        path.write_bytes(b"replacement")
        os.chmod(path, 0o600)
        self._assert_error(
            "production_account_lock_path_replaced",
            lambda: release_production_account_lock(lease),
        )
        self._assert_error(
            "production_account_lock_lease_invalid",
            lambda: release_production_account_lock(lease),
        )
        root = ProcessLock(str(held))
        root.acquire()
        root.release()

    def test_revoke_and_release_uncertainty_latch_halt_in_subprocess(self) -> None:
        for operation in ("revoke", "release"):
            with self.subTest(operation=operation):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        _UNCERTAINTY_CHILD,
                        operation,
                    ],
                    cwd=Path(__file__).resolve().parents[2],
                    env=self._child_environment(),
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"stdout={result.stdout!r}; stderr={result.stderr!r}",
                )
                self.assertEqual(
                    result.stdout.splitlines(),
                    [
                        "production_account_lock_release_uncertain",
                        "production_account_lock_release_uncertain",
                        "no-open",
                    ],
                )
                self.assertEqual(result.stderr, "")


class ProductionAccountLockCompatibilityTests(_ProductionAccountLockCase):
    def test_production_lock_blocks_root_v6_without_rewriting(self) -> None:
        grant = self._acquire()
        lease = self._consume(grant)
        path = self._path()
        before = path.read_bytes()
        result = subprocess.run(
            [sys.executable, "-B", "-c", _ROOT_TRY, str(path)],
            cwd=Path(__file__).resolve().parents[2],
            env=self._child_environment(),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 23)
        self.assertEqual(path.read_bytes(), before)
        release_production_account_lock(lease)

    def test_root_v6_blocks_production_then_existing_pid_file_is_preserved(
        self,
    ) -> None:
        path = self._path()
        process = self._start_root_holder(path)
        during = path.read_bytes()
        mode = stat.S_IMODE(path.stat().st_mode)
        self._assert_error(
            "production_account_lock_contended",
            lambda: self._acquire(),
        )
        self.assertEqual(path.read_bytes(), during)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        self._stop_root_holder(process)
        lease = self._consume(self._acquire())
        self.assertEqual(path.read_bytes(), during)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        release_production_account_lock(lease)
        self.assertEqual(path.read_bytes(), during)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def test_different_subaccounts_and_child_retention_lock_are_distinct(
        self,
    ) -> None:
        lease_a = self._consume(self._acquire(0))
        lease_b = self._consume(self._acquire(1))
        roots = derive_locked_production_state_roots_v1(lease_a)
        self.assertNotEqual(
            self._path(0),
            roots.phase1_state_root / "retention.lock",
        )
        root = ProcessLock(str(roots.phase1_state_root / "retention.lock"))
        root.acquire()
        root.release()
        release_production_account_lock(lease_b)
        release_production_account_lock(lease_a)


if __name__ == "__main__":
    unittest.main()
