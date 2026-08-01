from dataclasses import FrozenInstanceError
import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from tennis_v1 import pinned_file
import tennis_v1.config as tennis_config
from tennis_v1.canonical import CanonicalJsonError, canonical_json_bytes
from tennis_v1.config import (
    ConfigError,
    canonical_config_sha256,
    load_config,
    session_wal_path,
)


class TennisV1ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary_directory.name).resolve()
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()
        self.config_number = 0

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def valid_config(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "state_root": str(self.root / "state"),
            "provider_manifest_path": str(self.root / "provider.json"),
            "provider_manifest_sha256": "a" * 64,
            "trusted_permission_reviewer_ids": ["reviewer-test"],
            "trusted_qualification_issuer_ids": ["issuer-test"],
            "observed_pool_limit": 10,
            "paper_position_limit": 3,
        }

    def write_json(self, value: object, *, sort_keys: bool = False) -> pathlib.Path:
        self.config_number += 1
        path = self.root / f"config-{self.config_number}.json"
        path.write_text(json.dumps(value, sort_keys=sort_keys), encoding="utf-8")
        return path

    def test_loads_frozen_config_and_normalizes_absolute_paths(self) -> None:
        config = load_config(self.write_json(self.valid_config()), repo_root=self.repo_root)
        self.assertEqual(config.observed_pool_limit, 10)
        self.assertTrue(config.state_root.is_absolute())
        with self.assertRaises(FrozenInstanceError):
            config.observed_pool_limit = 9  # type: ignore[misc]

    def test_rejects_unknown_order_or_live_controls(self) -> None:
        for forbidden in (
            "live_enabled",
            "demo_enabled",
            "order_url",
            "api_key",
            "private_key",
            "provider_url",
        ):
            raw = self.valid_config()
            raw[forbidden] = "unsafe"
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(ConfigError):
                    load_config(self.write_json(raw), repo_root=self.repo_root)

    def test_rejects_relative_paths_and_limits_outside_policy(self) -> None:
        bad = self.valid_config()
        bad["state_root"] = "logs"
        with self.assertRaises(ConfigError):
            load_config(self.write_json(bad), repo_root=self.repo_root)
        for field, value in (
            ("observed_pool_limit", 0),
            ("observed_pool_limit", 11),
            ("paper_position_limit", 0),
            ("paper_position_limit", 4),
        ):
            bad = self.valid_config()
            bad[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(ConfigError):
                    load_config(self.write_json(bad), repo_root=self.repo_root)

    def test_rejects_repository_paths_symlinked_paths_and_unsorted_trust_anchors(self) -> None:
        repository_path = self.valid_config()
        repository_path["provider_manifest_path"] = str(self.repo_root / "provider.json")
        with self.assertRaises(ConfigError):
            load_config(self.write_json(repository_path), repo_root=self.repo_root)

        target = self.root / "target-state"
        target.mkdir()
        linked = self.root / "linked-state"
        linked.symlink_to(target, target_is_directory=True)
        symlinked_path = self.valid_config()
        symlinked_path["state_root"] = str(linked)
        with self.assertRaises(ConfigError):
            load_config(self.write_json(symlinked_path), repo_root=self.repo_root)

        unsorted_anchors = self.valid_config()
        unsorted_anchors["trusted_permission_reviewer_ids"] = ["reviewer-z", "reviewer-a"]
        with self.assertRaises(ConfigError):
            load_config(self.write_json(unsorted_anchors), repo_root=self.repo_root)

    def test_rejects_config_inside_a_symlink_alias_of_repo_root(self) -> None:
        physical_repo = self.root / "physical-repo"
        physical_repo.mkdir()
        repo_alias = self.root / "repo-alias"
        repo_alias.symlink_to(physical_repo, target_is_directory=True)
        config_path = physical_repo / "config.json"
        config_path.write_text(json.dumps(self.valid_config()), encoding="utf-8")

        with self.assertRaises(ConfigError):
            load_config(config_path, repo_root=repo_alias)

    def test_loads_exact_bytes_from_one_final_open_and_propagates_read_drift(self) -> None:
        """Config parsing cannot switch to a pathname after the pinned descriptor read."""
        config_path = self.write_json(self.valid_config())
        source_bytes = config_path.read_bytes()
        original_open = pinned_file.os.open
        with mock.patch.object(pinned_file.os, "open", wraps=original_open) as open_call:
            loaded = load_config(config_path, repo_root=self.repo_root)
        final_opens = [
            call
            for call in open_call.call_args_list
            if call.args and call.args[0] == config_path.name
        ]
        self.assertEqual(len(final_opens), 1)
        self.assertEqual(loaded.source_file_sha256, hashlib.sha256(source_bytes).hexdigest())

        original_read = pinned_file._read_bounded

        def replace_after_read(descriptor: int, max_bytes: int) -> bytes:
            content = original_read(descriptor, max_bytes)
            config_path.write_bytes(b"{}")
            return content

        with mock.patch.object(pinned_file, "_read_bounded", side_effect=replace_after_read):
            with self.assertRaises(ConfigError):
                load_config(config_path, repo_root=self.repo_root)

    def test_parses_captured_pinned_bytes_after_the_pathname_is_corrupted(self) -> None:
        """The loader must not re-open the pathname after its pinned read returns."""
        config_path = self.write_json(self.valid_config())
        captured_bytes = config_path.read_bytes()
        real_pinned_read = tennis_config.read_pinned_file

        def read_then_corrupt_path(*args, **kwargs):
            pinned = real_pinned_read(*args, **kwargs)
            config_path.write_bytes(b"this is no longer JSON")
            return pinned

        with mock.patch.object(
            tennis_config, "read_pinned_file", side_effect=read_then_corrupt_path
        ):
            loaded = load_config(config_path, repo_root=self.repo_root)

        self.assertEqual(
            loaded.source_file_sha256,
            hashlib.sha256(captured_bytes).hexdigest(),
        )
        self.assertEqual(loaded.observed_pool_limit, 10)

    def test_session_wal_path_is_confined_and_requires_canonical_uuid(self) -> None:
        config = load_config(self.write_json(self.valid_config()), repo_root=self.repo_root)
        session_id = "1f8b7b52-fdad-4dc1-a7a1-c2b1d4afaa12"
        self.assertEqual(
            session_wal_path(config, session_id),
            config.state_root / "sessions" / f"{session_id}.wal",
        )
        for invalid in ("../escape", session_id.upper(), "{" + session_id + "}"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ConfigError):
                    session_wal_path(config, invalid)

    def test_rejects_bom_non_utf8_duplicate_keys_floats_and_boolean_limits(self) -> None:
        malformed = (
            b'\xef\xbb\xbf{}',
            b'\xff{}',
            b'{"schema_version":1,"schema_version":1}',
            b'{"schema_version":1.0}',
        )
        for payload in malformed:
            path = self.root / f"raw-{len(payload)}.json"
            path.write_bytes(payload)
            with self.subTest(payload=payload):
                with self.assertRaises(ConfigError):
                    load_config(path, repo_root=self.repo_root)
        boolean_limit = self.valid_config()
        boolean_limit["observed_pool_limit"] = True
        with self.assertRaises(ConfigError):
            load_config(self.write_json(boolean_limit), repo_root=self.repo_root)

    def test_source_and_canonical_digests_are_distinct_and_bound(self) -> None:
        raw = self.valid_config()
        first_path = self.write_json(raw, sort_keys=False)
        second_path = self.write_json(raw, sort_keys=True)
        first = load_config(first_path, repo_root=self.repo_root)
        second = load_config(second_path, repo_root=self.repo_root)

        self.assertNotEqual(first.source_file_sha256, second.source_file_sha256)
        self.assertEqual(first.canonical_sha256, second.canonical_sha256)
        self.assertEqual(first.canonical_sha256, canonical_config_sha256(first))
        self.assertEqual(
            first.source_file_sha256,
            hashlib.sha256(first_path.read_bytes()).hexdigest(),
        )

    def test_canonical_json_rejects_floats_nonstring_keys_and_unknown_types(self) -> None:
        for value in ({"limit": 1.0}, {1: "bad"}, {"value": object()}):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(CanonicalJsonError):
                    canonical_json_bytes(value)


if __name__ == "__main__":
    unittest.main()
