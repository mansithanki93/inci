import hashlib
import pathlib
import re
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-07-27-tennis-v1-legacy-baseline.sha256"
)
EXPECTED_PROTECTED_PATHS = {
    "README.md",
    "analyze.py",
    "bot.py",
    "config.py",
    "engine.py",
    "executor.py",
    "fees.py",
    "kalshi_client.py",
    "market_data.py",
    "order_journal.py",
    "order_resolution.py",
    "pnl_ledger.py",
    "process_lock.py",
    "replay.py",
    "research_log.py",
    "safety.py",
    "schemas.py",
    "signals.py",
    "sports_discovery.py",
    "strategy.py",
    "tests.py",
}
MANIFEST_ROW = re.compile(r"([0-9a-f]{64})  ([^\n]+)")


def parse_manifest(manifest_path: pathlib.Path) -> dict[str, str]:
    rows = manifest_path.read_text(encoding="utf-8").splitlines()
    if len(rows) != 21:
        raise AssertionError("legacy baseline manifest must contain exactly 21 rows")

    manifest: dict[str, str] = {}
    for row in rows:
        match = MANIFEST_ROW.fullmatch(row)
        if match is None:
            raise AssertionError(f"invalid legacy baseline row: {row!r}")
        digest, relative_path = match.groups()
        path = pathlib.PurePosixPath(relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise AssertionError(f"unsafe legacy baseline path: {relative_path!r}")
        if relative_path in manifest:
            raise AssertionError(f"duplicate legacy baseline path: {relative_path!r}")
        manifest[relative_path] = digest

    if set(manifest) != EXPECTED_PROTECTED_PATHS:
        raise AssertionError("legacy baseline paths do not match the protected path set")
    return manifest


def sha256_from_single_descriptor(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_descriptor:
        while chunk := file_descriptor.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class LegacyBaselineTest(unittest.TestCase):
    def test_protected_legacy_files_match_committed_baseline(self) -> None:
        manifest = parse_manifest(MANIFEST_PATH)
        for relative_path, expected_digest in manifest.items():
            path = REPOSITORY_ROOT / relative_path
            self.assertTrue(path.exists(), f"missing protected legacy file: {relative_path}")
            self.assertFalse(path.is_symlink(), f"symlinked protected legacy file: {relative_path}")
            self.assertTrue(path.is_file(), f"non-regular protected legacy file: {relative_path}")
            self.assertEqual(
                sha256_from_single_descriptor(path),
                expected_digest,
                f"legacy baseline mismatch: {relative_path}",
            )
