from __future__ import annotations

import copy
import errno
import fcntl
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

from config import Config
from process_lock import ProcessLock, ProcessLockError

import inci_tennis_io.account_lock as account_lock_module
from inci_tennis_io.account_lock import (
    CandidateAccountLockError,
    CandidateAccountLockV1,
    acquire_candidate_account_lock,
    derive_candidate_account_lock_path,
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
import os
import sys

from process_lock import ProcessLock, ProcessLockError

os.umask(0o022)
lock = ProcessLock(sys.argv[1])
try:
    lock.acquire()
except ProcessLockError:
    raise SystemExit(23)
else:
    lock.release()
    raise SystemExit(0)
"""


class _StringSubclass(str):
    pass


class _IntSubclass(int):
    pass


class CandidateAccountLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve(strict=True)
        os.chmod(self.home, 0o700)
        self.control_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.control_temporary.cleanup)
        self.control = Path(
            self.control_temporary.name
        ).resolve(strict=True)
        self.real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        self.passwd_record = SimpleNamespace(pw_dir=str(self.home))
        self.passwd_patch = mock.patch.object(
            account_lock_module.pwd,
            "getpwuid",
            return_value=self.passwd_record,
        )
        self.passwd_mock = self.passwd_patch.start()
        self.addCleanup(self.passwd_patch.stop)
        self._held: list[CandidateAccountLockV1] = []
        self._children: list[tuple[subprocess.Popen[str], Path]] = []
        self.addCleanup(self._cleanup_children)
        self.addCleanup(self._cleanup_locks)

    def _cleanup_locks(self) -> None:
        for item in reversed(self._held):
            try:
                item.release()
            except CandidateAccountLockError:
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
        return derive_candidate_account_lock_path(
            environment="production",
            subaccount=subaccount,
        )

    def _acquire(self, subaccount: int = 0) -> CandidateAccountLockV1:
        item = acquire_candidate_account_lock(
            environment="production",
            subaccount=subaccount,
        )
        self._held.append(item)
        return item

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
            f"/tmp/inci-task8-account-lock-child-{os.getpid()}"
        )
        return environment

    def _start_root_holder(
        self,
        path: Path,
    ) -> subprocess.Popen[str]:
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
        with self.assertRaises(CandidateAccountLockError) as caught:
            callable_()
        self.assertEqual(str(caught.exception), expected)
        self.assertNotIn(str(self.home), str(caught.exception))
        self.assertNotIn(str(self.real_home), str(caught.exception))

    def test_derivation_matches_root_v6_formula_at_subaccount_bounds(self) -> None:
        for subaccount in (0, 32):
            with self.subTest(subaccount=subaccount):
                config = Config(subaccount=subaccount)
                actual = self._path(subaccount)
                expected = (
                    self.home
                    / ".local"
                    / "state"
                    / "inci"
                    / "production"
                    / f"subaccount-{subaccount}"
                    / "inci.lock"
                )
                self.assertIsInstance(actual, Path)
                self.assertEqual(actual, expected)
                self.assertEqual(actual, Path(config.process_lock_path))

    def test_invalid_arguments_do_no_passwd_or_filesystem_io(self) -> None:
        invalid = (
            (None, 0, "candidate_account_lock_environment_invalid"),
            ("demo", 0, "candidate_account_lock_environment_invalid"),
            (
                _StringSubclass("production"),
                0,
                "candidate_account_lock_environment_invalid",
            ),
            ("production", True, "candidate_account_lock_subaccount_invalid"),
            ("production", -1, "candidate_account_lock_subaccount_invalid"),
            ("production", 33, "candidate_account_lock_subaccount_invalid"),
            ("production", 1.0, "candidate_account_lock_subaccount_invalid"),
            (
                "production",
                _IntSubclass(1),
                "candidate_account_lock_subaccount_invalid",
            ),
        )
        real_open = os.open
        for environment, subaccount, message in invalid:
            with self.subTest(environment=environment, subaccount=subaccount):
                self.passwd_mock.reset_mock()
                with mock.patch.object(
                    account_lock_module.os,
                    "open",
                    wraps=real_open,
                ) as open_mock:
                    self._assert_error(
                        message,
                        lambda: derive_candidate_account_lock_path(
                            environment=environment,  # type: ignore[arg-type]
                            subaccount=subaccount,  # type: ignore[arg-type]
                        ),
                    )
                self.passwd_mock.assert_not_called()
                open_mock.assert_not_called()

    def test_passwd_home_uses_exact_lexical_contract(self) -> None:
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
                    "candidate_account_lock_passwd_home_invalid",
                    lambda: self._path(),
                )

    def test_home_environment_and_cwd_cannot_move_the_lock(self) -> None:
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

    def test_missing_required_platform_primitives_fail_before_open(
        self,
    ) -> None:
        real_open = os.open
        cases = (
            (account_lock_module.os, "O_NOFOLLOW"),
            (account_lock_module.os, "O_CLOEXEC"),
            (account_lock_module.os, "O_DIRECTORY"),
            (account_lock_module.os, "fstat"),
            (account_lock_module.os, "fchmod"),
            (account_lock_module.os, "close"),
            (account_lock_module.os, "getpid"),
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
            ) as open_mock:
                self._assert_error(
                    "candidate_account_lock_platform_unsupported",
                    lambda: self._acquire(),
                )
                open_mock.assert_not_called()

    def test_missing_hierarchy_and_lock_are_private_and_empty(self) -> None:
        item = self._acquire()
        path = self._path()
        current = self.home
        for component in (
            ".local",
            "state",
            "inci",
            "production",
            "subaccount-0",
        ):
            current /= component
            value = current.stat()
            self.assertTrue(stat.S_ISDIR(value.st_mode))
            self.assertEqual(stat.S_IMODE(value.st_mode), 0o700)
            self.assertEqual(value.st_uid, os.geteuid())
        lock_stat = path.stat()
        self.assertTrue(stat.S_ISREG(lock_stat.st_mode))
        self.assertEqual(stat.S_IMODE(lock_stat.st_mode), 0o600)
        self.assertEqual(lock_stat.st_nlink, 1)
        self.assertEqual(path.read_bytes(), b"")
        item.release()
        self.assertEqual(path.read_bytes(), b"")

    def test_existing_safe_0755_directories_and_0644_bytes_are_preserved(
        self,
    ) -> None:
        parent = self._make_parent(mode=0o755)
        path = parent / "inci.lock"
        original = b"root-v6-pid\n"
        path.write_bytes(original)
        os.chmod(path, 0o644)
        item = self._acquire()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        item.release()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_unsafe_directory_owner_mode_and_symlink_are_denied(self) -> None:
        local = self.home / ".local"
        local.mkdir()
        os.chmod(local, 0o770)
        self._assert_error(
            "candidate_account_lock_directory_invalid",
            lambda: self._acquire(),
        )
        os.chmod(local, 0o700)
        with mock.patch.object(
            account_lock_module.os,
            "geteuid",
            return_value=os.geteuid() + 1,
        ):
            self._assert_error(
                "candidate_account_lock_directory_invalid",
                lambda: self._acquire(),
            )
        local.rmdir()
        target = self.home / "local-target"
        target.mkdir()
        os.symlink(target, local)
        self._assert_error(
            "candidate_account_lock_directory_invalid",
            lambda: self._acquire(),
        )

    def test_symlink_hardlink_mode_and_nonregular_lock_are_denied(self) -> None:
        cases = ("symlink", "hardlink", "executable", "group-write", "directory")
        for subaccount, case in enumerate(cases):
            with self.subTest(case=case):
                parent = self._make_parent(subaccount)
                path = parent / "inci.lock"
                if case == "symlink":
                    target = parent / "target"
                    target.write_bytes(b"x")
                    os.symlink(target, path)
                elif case == "hardlink":
                    path.write_bytes(b"x")
                    os.chmod(path, 0o600)
                    os.link(path, parent / "second-link")
                elif case == "executable":
                    path.write_bytes(b"x")
                    os.chmod(path, 0o700)
                elif case == "group-write":
                    path.write_bytes(b"x")
                    os.chmod(path, 0o660)
                else:
                    path.mkdir()
                self._assert_error(
                    "candidate_account_lock_file_invalid",
                    lambda value=subaccount: self._acquire(value),
                )

    def test_unsafe_lock_file_owner_is_denied(self) -> None:
        parent = self._make_parent()
        path = parent / "inci.lock"
        path.write_bytes(b"owner")
        os.chmod(path, 0o600)
        real_fstat = os.fstat

        def mismatched_file_owner(fd: int):
            value = real_fstat(fd)
            if not stat.S_ISREG(value.st_mode):
                return value
            fields = list(value)
            fields[4] = os.geteuid() + 1
            return os.stat_result(fields)

        with mock.patch.object(
            account_lock_module.os,
            "fstat",
            side_effect=mismatched_file_owner,
        ):
            self._assert_error(
                "candidate_account_lock_file_invalid",
                lambda: self._acquire(),
            )

    def test_every_open_has_nofollow_cloexec_and_directory_flag(self) -> None:
        real_open = os.open
        calls: list[int] = []

        def observing_open(path, flags, mode=0o777, *, dir_fd=None):
            calls.append(flags)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(
            account_lock_module.os,
            "open",
            side_effect=observing_open,
        ):
            item = self._acquire()
            item.release()
        self.assertGreaterEqual(len(calls), 7)
        for flags in calls:
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertTrue(flags & os.O_CLOEXEC)
            if flags & os.O_DIRECTORY:
                self.assertFalse(flags & os.O_CREAT)
        directory_calls = [
            flags for flags in calls if flags & os.O_DIRECTORY
        ]
        file_calls = [
            flags for flags in calls if not flags & os.O_DIRECTORY
        ]
        self.assertEqual(len(directory_calls), 11)
        self.assertEqual(len(file_calls), 1)
        self.assertTrue(file_calls[0] & os.O_CREAT)
        self.assertTrue(file_calls[0] & os.O_EXCL)

    def test_directory_replacement_after_flock_is_detected_and_cleaned(
        self,
    ) -> None:
        real_flock = fcntl.flock
        replaced = False

        def racing_flock(fd: int, operation: int) -> None:
            nonlocal replaced
            real_flock(fd, operation)
            if operation & fcntl.LOCK_NB and not replaced:
                replaced = True
                original = self.home / ".local"
                original.rename(self.home / ".local-held")
                replacement = self.home / ".local"
                replacement.mkdir()
                os.chmod(replacement, 0o700)

        with mock.patch.object(
            account_lock_module.fcntl,
            "flock",
            side_effect=racing_flock,
        ):
            self._assert_error(
                "candidate_account_lock_path_replaced",
                lambda: self._acquire(),
            )
        held_path = (
            self.home
            / ".local-held"
            / "state"
            / "inci"
            / "production"
            / "subaccount-0"
            / "inci.lock"
        )
        root = ProcessLock(str(held_path))
        root.acquire()
        root.release()

    def test_lock_replacement_after_flock_is_detected_and_cleaned(self) -> None:
        real_flock = fcntl.flock
        replaced = False

        def racing_flock(fd: int, operation: int) -> None:
            nonlocal replaced
            real_flock(fd, operation)
            if operation & fcntl.LOCK_NB and not replaced:
                replaced = True
                path = self._path()
                path.rename(path.with_name("inci.lock-held"))
                path.write_bytes(b"replacement")
                os.chmod(path, 0o600)

        with mock.patch.object(
            account_lock_module.fcntl,
            "flock",
            side_effect=racing_flock,
        ):
            self._assert_error(
                "candidate_account_lock_path_replaced",
                lambda: self._acquire(),
            )
        held_path = self._path().with_name("inci.lock-held")
        root = ProcessLock(str(held_path))
        root.acquire()
        root.release()

    def test_post_flock_unsafe_replacements_are_path_replaced_and_cleaned(
        self,
    ) -> None:
        cases = (
            (10, "unsafe-directory"),
            (11, "symlink-directory"),
            (12, "unsafe-lock"),
        )
        for subaccount, case in cases:
            with self.subTest(case=case):
                real_flock = fcntl.flock
                real_close = os.close
                closed: list[int] = []
                replaced = False
                path = self._path(subaccount)
                parent = path.parent
                held_parent = parent.with_name(parent.name + "-held")
                held_lock = path.with_name("inci.lock-held")

                def racing_flock(fd: int, operation: int) -> None:
                    nonlocal replaced
                    real_flock(fd, operation)
                    if operation & fcntl.LOCK_NB and not replaced:
                        replaced = True
                        if case == "unsafe-lock":
                            path.rename(held_lock)
                            path.write_bytes(b"unsafe")
                            os.chmod(path, 0o660)
                        else:
                            parent.rename(held_parent)
                            if case == "unsafe-directory":
                                parent.mkdir()
                                os.chmod(parent, 0o770)
                            else:
                                os.symlink(held_parent, parent)

                def observing_close(fd: int) -> None:
                    closed.append(fd)
                    real_close(fd)

                with mock.patch.object(
                    account_lock_module.fcntl,
                    "flock",
                    side_effect=racing_flock,
                ), mock.patch.object(
                    account_lock_module.os,
                    "close",
                    side_effect=observing_close,
                ):
                    self._assert_error(
                        "candidate_account_lock_path_replaced",
                        lambda value=subaccount: self._acquire(value),
                    )
                self.assertEqual(len(closed), 7)
                self.assertEqual(len(set(closed)), 7)
                root_path = (
                    held_lock
                    if case == "unsafe-lock"
                    else held_parent / "inci.lock"
                )
                root = ProcessLock(str(root_path))
                root.acquire()
                root.release()

    def test_release_unsafe_replacements_are_path_replaced_and_cleaned(
        self,
    ) -> None:
        cases = (
            (13, "unsafe-directory"),
            (14, "symlink-directory"),
            (15, "unsafe-lock"),
        )
        for subaccount, case in cases:
            with self.subTest(case=case):
                item = self._acquire(subaccount)
                path = self._path(subaccount)
                parent = path.parent
                held_parent = parent.with_name(parent.name + "-held")
                held_lock = path.with_name("inci.lock-held")
                if case == "unsafe-lock":
                    path.rename(held_lock)
                    path.write_bytes(b"unsafe")
                    os.chmod(path, 0o660)
                else:
                    parent.rename(held_parent)
                    if case == "unsafe-directory":
                        parent.mkdir()
                        os.chmod(parent, 0o770)
                    else:
                        os.symlink(held_parent, parent)
                real_close = os.close
                closed: list[int] = []

                def observing_close(fd: int) -> None:
                    closed.append(fd)
                    real_close(fd)

                with mock.patch.object(
                    account_lock_module.os,
                    "close",
                    side_effect=observing_close,
                ):
                    self._assert_error(
                        "candidate_account_lock_path_replaced",
                        item.release,
                    )
                self.assertEqual(len(closed), 7)
                self.assertEqual(len(set(closed)), 7)
                with mock.patch.object(
                    account_lock_module.fcntl,
                    "flock",
                    side_effect=AssertionError("unexpected terminal retry"),
                ), mock.patch.object(
                    account_lock_module.os,
                    "close",
                    side_effect=AssertionError("unexpected terminal retry"),
                ):
                    self.assertIsNone(item.release())
                root_path = (
                    held_lock
                    if case == "unsafe-lock"
                    else held_parent / "inci.lock"
                )
                root = ProcessLock(str(root_path))
                root.acquire()
                root.release()

    def test_candidate_blocks_actual_root_v6_before_root_writes(self) -> None:
        item = self._acquire()
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
        self.assertEqual(
            result.returncode,
            23,
            msg=f"stdout={result.stdout!r}; stderr={result.stderr!r}",
        )
        self.assertEqual(path.read_bytes(), before)
        item.release()

    def test_actual_root_v6_blocks_candidate_and_released_file_is_compatible(
        self,
    ) -> None:
        path = self._path()
        process = self._start_root_holder(path)
        during = path.read_bytes()
        self.assertTrue(during)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self._assert_error(
            "candidate_account_lock_contended",
            lambda: self._acquire(),
        )
        self.assertEqual(path.read_bytes(), during)
        self._stop_root_holder(process)
        item = self._acquire()
        self.assertEqual(path.read_bytes(), during)
        item.release()
        self.assertEqual(path.read_bytes(), during)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_different_subaccounts_are_independent(self) -> None:
        second_path = self._path(1)
        initializer = ProcessLock(str(second_path))
        initializer.acquire()
        initializer.release()
        first = self._acquire(0)
        process = self._start_root_holder(second_path)
        self.assertIsNone(process.poll())
        self._stop_root_holder(process)
        first.release()

    def test_wrong_thread_cannot_release_or_weaken_the_lock(self) -> None:
        item = self._acquire()
        errors: list[str] = []

        def release() -> None:
            try:
                item.release()
            except CandidateAccountLockError as error:
                errors.append(str(error))

        thread = threading.Thread(target=release)
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ["candidate_account_lock_thread_invalid"])
        contender = ProcessLock(str(self._path()))
        with self.assertRaises(ProcessLockError):
            contender.acquire()
        contender.release()
        item.release()

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    def test_fork_child_cannot_release_or_reuse_parent_handle(self) -> None:
        item = self._acquire()
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            try:
                warnings.simplefilter("ignore", ResourceWarning)
                os.close(read_fd)
                try:
                    item.release()
                except CandidateAccountLockError as error:
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
        self.assertEqual(
            payload,
            b"candidate_account_lock_fork_invalid",
        )
        contender = ProcessLock(str(self._path()))
        with self.assertRaises(ProcessLockError):
            contender.acquire()
        contender.release()
        item.release()

    def test_release_revalidates_every_named_edge_and_is_terminal(self) -> None:
        targets = (
            ".local",
            "state",
            "inci",
            "production",
            "subaccount-0",
            "inci.lock",
        )
        for target in targets:
            with self.subTest(target=target):
                with tempfile.TemporaryDirectory() as temporary:
                    home = Path(temporary).resolve(strict=True)
                    os.chmod(home, 0o700)
                    record = SimpleNamespace(pw_dir=str(home))
                    with mock.patch.object(
                        account_lock_module.pwd,
                        "getpwuid",
                        return_value=record,
                    ):
                        item = acquire_candidate_account_lock(
                            environment="production",
                            subaccount=0,
                        )
                        path = derive_candidate_account_lock_path(
                            environment="production",
                            subaccount=0,
                        )
                        if target == "inci.lock":
                            original = path
                        else:
                            components = (
                                ".local",
                                "state",
                                "inci",
                                "production",
                                "subaccount-0",
                            )
                            original = home.joinpath(
                                *components[: components.index(target) + 1]
                            )
                        held = original.with_name(original.name + "-held")
                        original.rename(held)
                        if target == "inci.lock":
                            original.write_bytes(b"replacement")
                            os.chmod(original, 0o600)
                        else:
                            original.mkdir()
                            os.chmod(original, 0o700)
                        self._assert_error(
                            "candidate_account_lock_path_replaced",
                            item.release,
                        )
                        with mock.patch.object(
                            account_lock_module.fcntl,
                            "flock",
                            side_effect=AssertionError(
                                "unexpected terminal retry"
                            ),
                        ), mock.patch.object(
                            account_lock_module.os,
                            "close",
                            side_effect=AssertionError(
                                "unexpected terminal retry"
                            ),
                        ):
                            self.assertIsNone(item.release())

    def test_successful_release_is_same_owner_idempotent_with_zero_io(
        self,
    ) -> None:
        item = self._acquire()
        item.release()
        with mock.patch.object(
            account_lock_module.fcntl,
            "flock",
            side_effect=AssertionError("unexpected flock"),
        ), mock.patch.object(
            account_lock_module.os,
            "close",
            side_effect=AssertionError("unexpected close"),
        ):
            self.assertIsNone(item.release())

    def test_release_uncertainty_is_terminal_redacted_and_not_retried(
        self,
    ) -> None:
        item = self._acquire()
        real_close = os.close
        failed = False

        def close_then_fail(fd: int) -> None:
            nonlocal failed
            real_close(fd)
            if not failed:
                failed = True
                raise OSError("raw descriptor detail")

        with mock.patch.object(
            account_lock_module.os,
            "close",
            side_effect=close_then_fail,
        ):
            self._assert_error(
                "candidate_account_lock_release_uncertain",
                item.release,
            )
        with mock.patch.object(
            account_lock_module.fcntl,
            "flock",
            side_effect=AssertionError("unexpected retry"),
        ), mock.patch.object(
            account_lock_module.os,
            "close",
            side_effect=AssertionError("unexpected retry"),
        ):
            self._assert_error(
                "candidate_account_lock_release_uncertain",
                item.release,
            )
        root = ProcessLock(str(self._path()))
        root.acquire()
        root.release()

    def test_acquire_cleanup_uncertainty_overrides_contention(self) -> None:
        path = self._path()
        process = self._start_root_holder(path)
        real_close = os.close
        failed = False

        def close_then_fail(fd: int) -> None:
            nonlocal failed
            real_close(fd)
            if not failed:
                failed = True
                raise OSError("raw descriptor detail")

        with mock.patch.object(
            account_lock_module.os,
            "close",
            side_effect=close_then_fail,
        ):
            self._assert_error(
                "candidate_account_lock_acquire_uncertain",
                lambda: self._acquire(),
            )
        self._stop_root_holder(process)

    def test_validation_cleanup_uncertainty_overrides_identity_error(
        self,
    ) -> None:
        real_fstat = os.fstat
        real_close = os.close
        failed = False

        def unsafe_home(fd: int):
            value = real_fstat(fd)
            fields = list(value)
            fields[0] |= 0o022
            return os.stat_result(fields)

        def close_then_fail(fd: int) -> None:
            nonlocal failed
            real_close(fd)
            if not failed:
                failed = True
                raise OSError("raw cleanup ambiguity")

        with mock.patch.object(
            account_lock_module.os,
            "fstat",
            side_effect=unsafe_home,
        ), mock.patch.object(
            account_lock_module.os,
            "close",
            side_effect=close_then_fail,
        ):
            self._assert_error(
                "candidate_account_lock_acquire_uncertain",
                lambda: self._acquire(),
            )

    def test_generic_acquire_failure_is_fixed_and_redacted(self) -> None:
        real_flock = fcntl.flock

        def failed_flock(descriptor: int, operation: int) -> None:
            if operation & fcntl.LOCK_NB:
                raise OSError(errno.EIO, f"raw {self.home}")
            real_flock(descriptor, operation)

        with mock.patch.object(
            account_lock_module.fcntl,
            "flock",
            side_effect=failed_flock,
        ):
            self._assert_error(
                "candidate_account_lock_acquire_failed",
                lambda: self._acquire(),
            )

    def _forged_exact_authority(self):
        return account_lock_module._LockAuthority(
            home_path=str(self.home),
            component_names=(),
            directory_fds=(),
            lock_fd=-1,
            observation=(
                (),
                account_lock_module._Identity(
                    device=0,
                    inode=0,
                    mode=stat.S_IFREG | 0o600,
                    owner=os.geteuid(),
                    links=1,
                ),
            ),
            owner_pid=os.getpid(),
            owner_thread=threading.current_thread(),
        )

    def test_forged_exact_authority_handle_is_stale_and_redacted(
        self,
    ) -> None:
        forged = object.__new__(CandidateAccountLockV1)
        authority = self._forged_exact_authority()
        with self.assertRaises(AttributeError):
            object.__setattr__(forged, "_authority", authority)
        self._assert_error(
            "candidate_account_lock_stale",
            forged.release,
        )

    def test_legitimate_handle_has_no_authority_slot_or_swap_surface(
        self,
    ) -> None:
        item = self._acquire()
        self.assertFalse(hasattr(item, "_authority"))
        self.assertFalse(hasattr(item, "__dict__"))
        with self.assertRaises(AttributeError):
            object.__setattr__(
                item,
                "_authority",
                self._forged_exact_authority(),
            )
        contender = ProcessLock(str(self._path()))
        with self.assertRaises(ProcessLockError):
            contender.acquire()
        contender.release()
        item.release()

    def test_corrupt_unregistered_handle_is_stale_and_redacted(self) -> None:
        forged = object.__new__(CandidateAccountLockV1)
        self._assert_error(
            "candidate_account_lock_stale",
            forged.release,
        )

    def test_opaque_handle_rejects_construction_copy_pickle_and_subclass(
        self,
    ) -> None:
        with self.assertRaises(TypeError):
            CandidateAccountLockV1()
        with self.assertRaises(TypeError):

            class _Subclass(CandidateAccountLockV1):
                pass

        item = self._acquire()
        self.assertEqual(repr(item), "<CandidateAccountLockV1 redacted>")
        self.assertFalse(hasattr(item, "path"))
        self.assertFalse(hasattr(item, "fd"))
        for operation in (
            copy.copy,
            copy.deepcopy,
            pickle.dumps,
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(TypeError):
                    operation(item)
        item.release()

    def test_descriptor_path_spy_proves_only_patched_home_is_touched(
        self,
    ) -> None:
        real_open = os.open
        real_mkdir = os.mkdir
        real_flock = fcntl.flock
        fd_paths: dict[int, Path] = {}
        operations: list[tuple[str, Path]] = []

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
            operations.append(("open", resolved))
            return descriptor

        def mkdir_spy(path, mode=0o777, *, dir_fd=None):
            resolved = resolve(path, dir_fd)
            result = real_mkdir(path, mode, dir_fd=dir_fd)
            operations.append(("mkdir", resolved))
            return result

        def flock_spy(fd, operation):
            resolved = fd_paths.get(fd)
            if resolved is None:
                self.fail(f"flock used untracked descriptor {fd}")
            operations.append(("flock", resolved))
            return real_flock(fd, operation)

        with mock.patch.object(
            account_lock_module.os,
            "open",
            side_effect=open_spy,
        ), mock.patch.object(
            account_lock_module.os,
            "mkdir",
            side_effect=mkdir_spy,
        ), mock.patch.object(
            account_lock_module.fcntl,
            "flock",
            side_effect=flock_spy,
        ):
            item = self._acquire()
            item.release()

        self.assertTrue(any(kind == "open" for kind, _ in operations))
        self.assertTrue(any(kind == "mkdir" for kind, _ in operations))
        self.assertTrue(any(kind == "flock" for kind, _ in operations))
        for kind, path in operations:
            with self.subTest(kind=kind, path=path):
                self.assertEqual(
                    os.path.commonpath((str(self.home), str(path))),
                    str(self.home),
                )
                if self.real_home != self.home:
                    self.assertNotEqual(
                        os.path.commonpath(
                            (str(self.real_home), str(path))
                        ),
                        str(self.real_home),
                    )


if __name__ == "__main__":
    unittest.main()
