import hashlib
import os
import pathlib
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from tennis_v1 import pinned_file
from tennis_v1.pinned_file import PinnedFileError, read_pinned_file


class PinnedFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        # macOS commonly exposes this temporary directory through /var, a
        # symlink; test the physical tree because the loader must reject it.
        self.root = pathlib.Path(self.temporary_directory.name).resolve()
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_reads_exact_regular_file_bytes_and_their_hash(self) -> None:
        """Changing bytes after their digest is pinned must be observable."""
        source = self.root / "config.json"
        source_bytes = b'{"schema_version":1}'
        source.write_bytes(source_bytes)

        pinned = read_pinned_file(
            source,
            expected_sha256=hashlib.sha256(source_bytes).hexdigest(),
            repo_root=self.repo_root,
            max_bytes=1024,
        )

        self.assertEqual(pinned.data, source_bytes)
        self.assertEqual(pinned.sha256, hashlib.sha256(source_bytes).hexdigest())

    def test_forbidden_root_rejects_equal_and_descendant_before_filesystem_probe(
        self,
    ) -> None:
        """A restricted read must deny state paths before opening even the repo."""
        state_root = self.root / "state"
        candidates = (
            state_root,
            state_root / "nested" / "artifact.json",
        )

        for candidate in candidates:
            with (
                self.subTest(candidate=candidate),
                mock.patch.object(
                    pinned_file.os,
                    "open",
                    side_effect=AssertionError("filesystem probe"),
                ) as open_call,
                self.assertRaisesRegex(
                    PinnedFileError,
                    r"\Apinned file overlaps forbidden root\Z",
                ),
            ):
                read_pinned_file(
                    candidate,
                    expected_sha256=None,
                    repo_root=self.repo_root,
                    max_bytes=64,
                    forbidden_root=state_root,
                )
            open_call.assert_not_called()

    def test_forbidden_root_rejects_ancestor_casefold_and_unicode_aliases_before_probe(
        self,
    ) -> None:
        """Deny-only path comparison is conservative across filesystem aliases."""
        pairs = (
            (
                self.root / "StateRoot",
                self.root / "stateroot" / "artifact.json",
            ),
            (
                self.root / "Cafe\u0301State",
                self.root / "Caf\u00e9State" / "artifact.json",
            ),
            (
                self.root / "state",
                self.root / "state" / "nested",
            ),
        )

        for candidate, forbidden_root in pairs:
            with (
                self.subTest(
                    candidate=candidate,
                    forbidden_root=forbidden_root,
                ),
                mock.patch.object(
                    pinned_file.os,
                    "open",
                    side_effect=AssertionError("filesystem probe"),
                ) as open_call,
                self.assertRaisesRegex(
                    PinnedFileError,
                    r"\Apinned file overlaps forbidden root\Z",
                ),
            ):
                read_pinned_file(
                    candidate,
                    expected_sha256=None,
                    repo_root=self.repo_root,
                    max_bytes=64,
                    forbidden_root=forbidden_root,
                )
            open_call.assert_not_called()

    def test_forbidden_root_rejects_real_case_insensitive_volume_alias(self) -> None:
        """A real same-volume case alias cannot expose state bytes."""
        state_root = self.root / "StateRoot"
        state_root.mkdir()
        target = state_root / "artifact.json"
        target.write_bytes(b"state bytes")
        alias_root = self.root / "stateroot"
        if not alias_root.exists() or alias_root == state_root:
            self.skipTest("temporary volume is case-sensitive")

        with (
            mock.patch.object(
                pinned_file.os,
                "open",
                side_effect=AssertionError("filesystem probe"),
            ) as open_call,
            self.assertRaisesRegex(
                PinnedFileError,
                r"\Apinned file overlaps forbidden root\Z",
            ),
        ):
            read_pinned_file(
                alias_root / target.name,
                expected_sha256=None,
                repo_root=self.repo_root,
                max_bytes=64,
                forbidden_root=state_root,
            )
        open_call.assert_not_called()

    def test_forbidden_root_rejects_relative_and_dotdot_spelling_before_probe(
        self,
    ) -> None:
        """Restricted reads cannot normalize hostile spelling into authority."""
        state_root = self.root / "state"
        candidates = (
            pathlib.Path("relative-artifact.json"),
            self.root / "outside" / ".." / "state" / "artifact.json",
        )

        for candidate in candidates:
            with (
                self.subTest(candidate=candidate),
                mock.patch.object(
                    pinned_file.os,
                    "open",
                    side_effect=AssertionError("filesystem probe"),
                ) as open_call,
                self.assertRaisesRegex(
                    PinnedFileError,
                    r"\Arestricted pinned-file path is invalid\Z",
                ),
            ):
                read_pinned_file(
                    candidate,
                    expected_sha256=None,
                    repo_root=self.repo_root,
                    max_bytes=64,
                    forbidden_root=state_root,
                )
            open_call.assert_not_called()

    def test_forbidden_root_never_resolves_or_stats_the_root(self) -> None:
        """The deny root is lexical input, never a filesystem target."""
        source = self.root / "outside.json"
        source.write_bytes(b"{}")
        state_root = self.root / "missing-state"

        with (
            mock.patch.object(
                pathlib.Path,
                "resolve",
                side_effect=AssertionError("forbidden root resolve"),
            ) as resolve_call,
            mock.patch.object(
                pathlib.Path,
                "stat",
                side_effect=AssertionError("forbidden root stat"),
            ) as stat_call,
        ):
            pinned = read_pinned_file(
                source,
                expected_sha256=hashlib.sha256(b"{}").hexdigest(),
                repo_root=self.repo_root,
                max_bytes=64,
                forbidden_root=state_root,
            )

        self.assertEqual(pinned.data, b"{}")
        resolve_call.assert_not_called()
        stat_call.assert_not_called()

    def test_forbidden_root_does_not_follow_an_external_symlink_alias(self) -> None:
        """An alias outside state cannot bypass the lexical deny boundary."""
        state_root = self.root / "state"
        state_root.mkdir()
        target = state_root / "artifact.json"
        target.write_bytes(b"secret state bytes")
        alias = self.root / "alias.json"
        alias.symlink_to(target)

        with self.assertRaises(PinnedFileError):
            read_pinned_file(
                alias,
                expected_sha256=None,
                repo_root=self.repo_root,
                max_bytes=64,
                forbidden_root=state_root,
            )
        self.assertEqual(target.read_bytes(), b"secret state bytes")

    def test_rejects_every_symlink_component_and_nonregular_file(self) -> None:
        target_directory = self.root / "target"
        target_directory.mkdir()
        target = target_directory / "config.json"
        target.write_bytes(b"{}")
        linked_directory = self.root / "linked"
        linked_directory.symlink_to(target_directory, target_is_directory=True)
        file_link = self.root / "config-link.json"
        file_link.symlink_to(target)

        for candidate in (linked_directory / "config.json", file_link, target_directory):
            with self.subTest(candidate=candidate.name):
                with self.assertRaises(PinnedFileError):
                    read_pinned_file(
                        candidate,
                        expected_sha256=None,
                        repo_root=self.repo_root,
                        max_bytes=1024,
                    )

    def test_rejects_oversize_content_and_digest_mismatch(self) -> None:
        source = self.root / "config.json"
        source.write_bytes(b"abcdef")
        with self.assertRaises(PinnedFileError):
            read_pinned_file(source, expected_sha256=None, repo_root=self.repo_root, max_bytes=5)
        with self.assertRaises(PinnedFileError):
            read_pinned_file(source, expected_sha256="0" * 64, repo_root=self.repo_root, max_bytes=6)

    def test_final_open_requires_nonblocking_mode_before_fstat(self) -> None:
        """A FIFO must not block the loader before its nonregular type is checked."""
        source = self.root / "config.json"
        source.write_bytes(b"{}")
        original_open = pinned_file.os.open

        def guarded_open(path, flags, *args, **kwargs):
            if path == source.name:
                self.assertNotEqual(flags & os.O_NONBLOCK, 0)
            return original_open(path, flags, *args, **kwargs)

        with mock.patch.object(pinned_file.os, "open", side_effect=guarded_open):
            read_pinned_file(source, expected_sha256=None, repo_root=self.repo_root, max_bytes=64)

    def test_rejects_file_inside_a_symlink_alias_of_repository_root(self) -> None:
        """Physical repository identity, not lexical spelling, defines the boundary."""
        physical_repo = self.root / "physical-repo"
        physical_repo.mkdir()
        repo_alias = self.root / "repo-alias"
        repo_alias.symlink_to(physical_repo, target_is_directory=True)
        source = physical_repo / "config.json"
        source.write_bytes(b"{}")

        with self.assertRaises(PinnedFileError):
            read_pinned_file(source, expected_sha256=None, repo_root=repo_alias, max_bytes=64)

    def test_rejects_fifo_and_device_without_reading_them(self) -> None:
        """Nonregular inputs are rejected after nonblocking open, without a writer."""
        fifo = self.root / "config.fifo"
        os.mkfifo(fifo)
        with self.assertRaises(PinnedFileError):
            read_pinned_file(fifo, expected_sha256=None, repo_root=self.repo_root, max_bytes=64)

        null_device = pathlib.Path("/dev/null")
        if not null_device.exists():
            self.skipTest("this POSIX runtime has no null device")
        with self.assertRaises(PinnedFileError):
            read_pinned_file(null_device, expected_sha256=None, repo_root=self.repo_root, max_bytes=64)

    def test_detects_each_required_metadata_drift_after_open(self) -> None:
        """A replacement race changing any pinned identity field must abort."""
        source = self.root / "config.json"
        source.write_bytes(b"{}")
        original_fstat = pinned_file.os.fstat
        original = original_fstat
        fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_size", "st_mtime_ns", "st_ctime_ns")

        for field in fields:
            regular_file_calls = 0

            def drifting_fstat(descriptor: int, *, changed_field: str = field):
                nonlocal regular_file_calls
                value = original(descriptor)
                if stat.S_ISREG(value.st_mode):
                    regular_file_calls += 1
                if stat.S_ISREG(value.st_mode) and regular_file_calls == 3:
                    attributes = {
                        name: getattr(value, name)
                        for name in fields
                    }
                    attributes[changed_field] += 1
                    return SimpleNamespace(**attributes)
                return value

            with self.subTest(field=field), mock.patch.object(
                pinned_file.os, "fstat", side_effect=drifting_fstat
            ):
                with self.assertRaises(PinnedFileError):
                    read_pinned_file(source, expected_sha256=None, repo_root=self.repo_root, max_bytes=64)


if __name__ == "__main__":
    unittest.main()
