from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
import uuid

from tennis_v1.fingerprints import code_sha256, new_session_id


class FingerprintTests(unittest.TestCase):
    def test_code_fingerprint_covers_sorted_source_and_schema_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "schemas").mkdir()
            (root / "z.py").write_bytes(b"z = 1\n")
            (root / "a.py").write_bytes(b"a = 2\n")
            (root / "schemas" / "x.json").write_bytes(b'{"type":"object"}')
            first = code_sha256(root)
            os.utime(root / "a.py", None)
            self.assertEqual(code_sha256(root), first)
            (root / "a.py").write_bytes(b"a = 3\n")
            self.assertNotEqual(code_sha256(root), first)

    def test_code_fingerprint_rejects_symlinked_or_unknown_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "a.py").write_bytes(b"a = 1\n")
            (root / "unknown.txt").write_bytes(b"x")
            with self.assertRaises(ValueError):
                code_sha256(root)
            (root / "unknown.txt").unlink()
            (root / "linked.py").symlink_to(root / "a.py")
            with self.assertRaises(ValueError):
                code_sha256(root)

    def test_code_fingerprint_excludes_cache_and_compiled_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "a.py").write_bytes(b"a = 1\n")
            baseline = code_sha256(root)
            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "a.pyc").write_bytes(b"compiled")
            self.assertEqual(code_sha256(root), baseline)

    def test_code_fingerprint_rejects_relative_absolute_and_ancestor_symlink_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            real = root / "real"
            real.mkdir()
            (real / "a.py").write_bytes(b"a = 1\n")
            direct_link = root / "direct-link"
            direct_link.symlink_to(real, target_is_directory=True)
            parent = root / "parent"
            parent.mkdir()
            nested = parent / "nested"
            nested.mkdir()
            (nested / "a.py").write_bytes(b"a = 1\n")
            ancestor_link = root / "ancestor-link"
            ancestor_link.symlink_to(parent, target_is_directory=True)
            candidates = (
                direct_link,
                Path(os.path.relpath(direct_link, Path.cwd())),
                ancestor_link / "nested",
                Path(os.path.relpath(ancestor_link / "nested", Path.cwd())),
            )
            for candidate in candidates:
                with self.subTest(candidate=candidate):
                    with self.assertRaises(ValueError):
                        code_sha256(candidate)

    def test_new_session_id_is_unique_canonical_uuid_and_not_config_driven(self):
        first = new_session_id()
        second = new_session_id()
        self.assertNotEqual(first, second)
        self.assertEqual(str(uuid.UUID(first)), first)
        self.assertEqual(uuid.UUID(first).version, 4)
