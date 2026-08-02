from __future__ import annotations

from dataclasses import FrozenInstanceError
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


_TICKERS = ("KXTENNIS-MATCH-HOME", "KXTENNIS-MATCH-AWAY")
_WALL_NS = 1_785_607_205_000_000_000


def _private_payload(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def _resolved_store(root: Path) -> Path:
    from inci_tennis_io.shadow_evidence import (
        ShadowEvidenceStore,
        ShadowResolutionEvidence,
    )

    root = root.resolve(strict=False)
    provider = root.parent / "provider-discovery.json"
    _private_payload(provider, b'{"provider":"discovery"}')
    with ShadowEvidenceStore(root) as store:
        path = store.ledger_path
        store.append_resolution(
            ShadowResolutionEvidence(
                selected_wall_ns=_WALL_NS + 2,
                provider_match_id="sr:sport_event:123456",
                provider_start_wall_ns=_WALL_NS - 10_000_000_000,
                event_ticker="KXTENNIS-MATCH",
                home_player_name="Player Home",
                away_player_name="Player Away",
                market_tickers=_TICKERS,
                provider_discovery_raw_path=str(provider),
                provider_discovery_raw_sha256=sha256(provider.read_bytes()).hexdigest(),
                kalshi_catalog_sha256="1" * 64,
                resolver_snapshot_sha256="2" * 64,
                resolver_rule_version="strict-name-start-v1",
            )
        )
        store.append_terminal(
            reason="duration_elapsed",
            code=None,
            ended_wall_ns=_WALL_NS + 30,
            ended_monotonic_ns=30,
            provider_match_id="sr:sport_event:123456",
            market_tickers=_TICKERS,
            sportradar_captures=0,
            kalshi_frames=0,
        )
    return path


def _price_only_store(root: Path) -> Path:
    from inci_tennis_io.shadow_evidence import (
        PriceOnlyEvidenceObservation,
        PriceOnlySessionEvidence,
        ShadowEvidenceStore,
        ShadowMarketCandidate,
    )

    root = root.resolve(strict=False)
    with ShadowEvidenceStore(root) as store:
        path = store.ledger_path
        store.append_price_only_session(
            PriceOnlySessionEvidence(
                selected_wall_ns=_WALL_NS + 2,
                selected_monotonic_ns=2,
                event_ticker="KXTENNIS-MATCH",
                player_a_name="Player A",
                player_b_name="Player B",
                market_tickers=_TICKERS,
                scheduled_start_wall_ns=_WALL_NS - 10_000_000_000,
                catalog_sport="tennis",
                catalog_scope="atp",
                catalog_queried_competitions=("atp",),
                catalog_series_ticker="KXATP",
                catalog_milestone_id="match",
                catalog_milestone_league="ATP",
                initial_book_state="empty",
                initial_market_a=ShadowMarketCandidate(_TICKERS[0], None, None, None, None),
                initial_market_b=ShadowMarketCandidate(_TICKERS[1], None, None, None, None),
                provider_discovery_state="unavailable",
                provider_discovery_reason="provider_key_missing",
                provider_discovery_raw_path=None,
                provider_discovery_raw_sha256=None,
                kalshi_catalog_sha256="1" * 64,
                resolver_snapshot_sha256="2" * 64,
                resolver_version="kalshi-first-hybrid-v1",
                registry_digest="3" * 64,
            )
        )
        frame_payload = b'{"type":"orderbook_snapshot"}'
        receipt = store.persist_kalshi_frame(
            SimpleNamespace(
                payload=frame_payload,
                captured_wall_ns=_WALL_NS + 10,
                captured_monotonic_ns=10,
                clock_uncertainty_ns=3,
                physical_connection_generation=1,
                raw_sha256=sha256(frame_payload).hexdigest(),
            )
        )
        store.append_price_only_observation(
            PriceOnlyEvidenceObservation(
                observed_wall_ns=_WALL_NS + 20,
                observed_monotonic_ns=20,
                clock_uncertainty_ns=3,
                event_ticker="KXTENNIS-MATCH",
                market_tickers=_TICKERS,
                kalshi_raw_path=receipt.raw_path,
                kalshi_raw_sha256=receipt.raw_sha256,
                kalshi_captured_wall_ns=receipt.captured_wall_ns,
                kalshi_captured_monotonic_ns=receipt.captured_monotonic_ns,
                kalshi_generation=1,
                kalshi_sequence=1,
                kalshi_age_ns=1,
                kalshi_status="candidate",
                market_a=ShadowMarketCandidate(
                    _TICKERS[0], "0.31", "0.34", "12.00", "8.00"
                ),
                market_b=ShadowMarketCandidate(
                    _TICKERS[1], "0.66", "0.69", "6.00", "9.00"
                ),
                reason="candidate_snapshot_applied",
                kalshi_frames=1,
            )
        )
        store.append_price_only_terminal(
            reason="duration_elapsed",
            code=None,
            ended_wall_ns=_WALL_NS + 30,
            ended_monotonic_ns=30,
            event_ticker="KXTENNIS-MATCH",
            market_tickers=_TICKERS,
            kalshi_frames=1,
        )
    return path


class ShadowSettlementSourceAuditTests(unittest.TestCase):
    def test_audits_exact_verified_source_without_writing(self) -> None:
        """Catches accepting a source without retaining its verified identity."""

        from inci_tennis_io.shadow_evidence import audit_shadow_settlement_source

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger = _resolved_store(root)
            ledger = ledger.resolve(strict=True)
            before = {
                path: path.read_bytes()
                for path in sorted(root.rglob("*"))
                if path.is_file()
            }
            with audit_shadow_settlement_source(ledger) as source:
                self.assertEqual(source.session_path, ledger)
                self.assertEqual(source.ledger_sha256, sha256(ledger.read_bytes()).hexdigest())
                self.assertEqual(source.mode, "VERIFIED")
                self.assertEqual(source.event_ticker, "KXTENNIS-MATCH")
                self.assertEqual(source.market_tickers, _TICKERS)
                self.assertEqual(source.player_names, ("Player Home", "Player Away"))
                rows = [json.loads(line) for line in ledger.read_text().splitlines()]
                self.assertEqual(source.first_row_sha256, rows[0]["row_sha256"])
                self.assertEqual(source.terminal_row_sha256, rows[-1]["row_sha256"])
            self.assertEqual(
                before,
                {
                    path: path.read_bytes()
                    for path in sorted(root.rglob("*"))
                    if path.is_file()
                },
            )

    def test_audits_price_only_source_with_its_own_identity(self) -> None:
        """Catches deriving a PRICE_ONLY identity from verified-only fields."""

        from inci_tennis_io.shadow_evidence import audit_shadow_settlement_source

        with tempfile.TemporaryDirectory() as directory:
            ledger = _price_only_store(Path(directory) / "shadow").resolve()
            with audit_shadow_settlement_source(ledger) as source:
                self.assertEqual(source.mode, "PRICE_ONLY")
                self.assertEqual(source.event_ticker, "KXTENNIS-MATCH")
                self.assertEqual(source.market_tickers, _TICKERS)
                self.assertEqual(source.player_names, ("Player A", "Player B"))
                self.assertNotEqual(
                    source.first_row_sha256, source.terminal_row_sha256
                )
                with self.assertRaises(FrozenInstanceError):
                    source.mode = "VERIFIED"  # type: ignore[misc]

    def test_rejects_manual_or_incomplete_terminal_sources(self) -> None:
        """Catches exposing terminal ledgers that are not exact eligible sources."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
            ShadowResolutionEvidence,
            audit_shadow_settlement_source,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as store:
                manual = store.ledger_path
                store.append_terminal(
                    reason="duration_elapsed", code=None,
                    ended_wall_ns=_WALL_NS + 30, ended_monotonic_ns=30,
                    provider_match_id="sr:sport_event:123456",
                    market_tickers=_TICKERS, sportradar_captures=0,
                    kalshi_frames=0,
                )
            with self.assertRaisesRegex(ShadowEvidenceError, "shadow_evidence_state_invalid"):
                audit_shadow_settlement_source(manual.resolve())
            provider = Path(directory) / "incomplete-provider.json"
            _private_payload(provider, b'{"provider":"discovery"}')
            with ShadowEvidenceStore(root) as store:
                incomplete = store.ledger_path
                store.append_resolution(
                    ShadowResolutionEvidence(
                        selected_wall_ns=_WALL_NS + 2,
                        provider_match_id="sr:sport_event:123456",
                        provider_start_wall_ns=_WALL_NS - 10_000_000_000,
                        event_ticker="KXTENNIS-MATCH",
                        home_player_name="Player Home", away_player_name="Player Away",
                        market_tickers=_TICKERS,
                        provider_discovery_raw_path=str(provider),
                        provider_discovery_raw_sha256=sha256(provider.read_bytes()).hexdigest(),
                        kalshi_catalog_sha256="1" * 64, resolver_snapshot_sha256="2" * 64,
                        resolver_rule_version="strict-name-start-v1",
                    )
                )
            with self.assertRaises(ShadowEvidenceError):
                audit_shadow_settlement_source(incomplete.resolve())

    def test_rejects_unsafe_source_paths_files_and_lock_contention(self) -> None:
        """Catches bypassing canonical ownership, modes, or active-writer exclusion."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
            audit_shadow_settlement_source,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger = _resolved_store(root).resolve()
            alias = ledger.parent / "alias.jsonl"
            alias.symlink_to(ledger)
            with self.assertRaises(ShadowEvidenceError):
                audit_shadow_settlement_source(alias)
            alias.unlink()
            hardlink = ledger.parent / "hardlink.jsonl"
            os.link(ledger, hardlink)
            with self.assertRaises(ShadowEvidenceError):
                audit_shadow_settlement_source(ledger)
            hardlink.unlink()
            ledger.chmod(0o644)
            with self.assertRaises(ShadowEvidenceError):
                audit_shadow_settlement_source(ledger)
            ledger.chmod(0o600)
            with ShadowEvidenceStore(root):
                with self.assertRaisesRegex(ShadowEvidenceError, "shadow_evidence_locked"):
                    audit_shadow_settlement_source(ledger)

    def test_rejects_tampered_root_and_holds_lock_until_idempotent_close(self) -> None:
        """Catches accepting altered evidence or releasing its source lock early."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
            audit_shadow_settlement_source,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger = _resolved_store(root).resolve()
            lease = audit_shadow_settlement_source(ledger)
            try:
                with self.assertRaisesRegex(ShadowEvidenceError, "shadow_evidence_locked"):
                    ShadowEvidenceStore(root)
            finally:
                lease.close()
                lease.close()
            with ShadowEvidenceStore(root) as store:
                store.append_terminal(
                    reason="duration_elapsed", code=None,
                    ended_wall_ns=_WALL_NS + 31, ended_monotonic_ns=31,
                    provider_match_id="sr:sport_event:123457",
                    market_tickers=_TICKERS, sportradar_captures=0,
                    kalshi_frames=0,
                )
            payload = ledger.read_bytes()
            ledger.write_bytes(payload.replace(b"Player Home", b"Player H0me", 1))
            ledger.chmod(0o600)
            with self.assertRaises(ShadowEvidenceError):
                audit_shadow_settlement_source(ledger)

    def test_rejects_raw_and_commit_marker_tampering(self) -> None:
        """Catches auditing a selected source while ignoring other root evidence."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            audit_shadow_settlement_source,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = (Path(directory) / "raw-shadow").resolve()
            ledger = _price_only_store(root).resolve()
            raw = next((root / "raw").iterdir())
            raw.write_bytes(b"tampered")
            raw.chmod(0o600)
            with self.assertRaises(ShadowEvidenceError):
                audit_shadow_settlement_source(ledger)
            marker_root = (Path(directory) / "marker-shadow").resolve()
            marker_ledger = _price_only_store(marker_root).resolve()
            marker = marker_ledger.with_suffix(".commit")
            marker.write_bytes(b"{}\n")
            marker.chmod(0o600)
            with self.assertRaises(ShadowEvidenceError):
                audit_shadow_settlement_source(marker_ledger)

    def test_context_primary_error_wins_over_unlock_error(self) -> None:
        """Catches cleanup failures masking a caller's BaseException."""

        from inci_tennis_io.shadow_evidence import audit_shadow_settlement_source

        with tempfile.TemporaryDirectory() as directory:
            ledger = _resolved_store(Path(directory) / "shadow").resolve()
            lease = audit_shadow_settlement_source(ledger)
            original_flock = fcntl.flock

            def failing_unlock(descriptor: int, operation: int) -> None:
                if operation == fcntl.LOCK_UN:
                    raise OSError("unlock")
                original_flock(descriptor, operation)

            with patch(
                "inci_tennis_io.shadow_evidence.fcntl.flock",
                side_effect=failing_unlock,
            ):
                with self.assertRaisesRegex(RuntimeError, "primary"):
                    with lease:
                        raise RuntimeError("primary")
