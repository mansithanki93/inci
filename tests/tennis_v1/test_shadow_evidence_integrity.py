from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest


MATCH_ID = "sr:sport_event:123456"
TICKERS = ("KXTENNIS-MATCH-HOME", "KXTENNIS-MATCH-AWAY")
WALL_NS = 1_785_607_205_000_000_000
ZERO_DIGEST = "0" * 64


def _private_payload(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def _frame(payload: bytes = b'{"type":"orderbook_snapshot"}') -> object:
    return SimpleNamespace(
        payload=payload,
        captured_wall_ns=WALL_NS + 10,
        captured_monotonic_ns=10,
        clock_uncertainty_ns=3,
        physical_connection_generation=1,
        raw_sha256=sha256(payload).hexdigest(),
    )


def _observation(provider_path: Path, reference: object) -> object:
    from inci_tennis_io.shadow_evidence import (
        ShadowEvidenceObservation,
        ShadowMarketCandidate,
    )

    provider_payload = provider_path.read_bytes()
    return ShadowEvidenceObservation(
        observed_wall_ns=WALL_NS + 20,
        observed_monotonic_ns=20,
        clock_uncertainty_ns=3,
        provider_match_id=MATCH_ID,
        market_tickers=TICKERS,
        provider_generated_wall_ns=WALL_NS,
        provider_captured_wall_ns=WALL_NS + 1,
        provider_request_started_wall_ns=WALL_NS,
        provider_request_started_monotonic_ns=1,
        provider_request_completed_wall_ns=WALL_NS + 1,
        provider_request_completed_monotonic_ns=2,
        provider_clock_uncertainty_ns=1,
        provider_raw_path=str(provider_path),
        provider_raw_sha256=sha256(provider_payload).hexdigest(),
        home_player_name="Player Home",
        away_player_name="Player Away",
        match_status="1st_set",
        sets=(0, 0),
        games=(3, 2),
        points=("30", "15"),
        server="home",
        sportradar_age_ns=1,
        progression="initial",
        last_event_id=None,
        last_event_type=None,
        last_event_result=None,
        kalshi_raw_path=reference.raw_path,
        kalshi_raw_sha256=reference.raw_sha256,
        kalshi_captured_wall_ns=reference.captured_wall_ns,
        kalshi_captured_monotonic_ns=reference.captured_monotonic_ns,
        kalshi_generation=1,
        kalshi_sequence=1,
        kalshi_age_ns=1,
        kalshi_status="candidate",
        home_market=ShadowMarketCandidate(
            TICKERS[0], "0.31", "0.34", "12.00", "8.00"
        ),
        away_market=ShadowMarketCandidate(
            TICKERS[1], "0.66", "0.69", "6.00", "9.00"
        ),
        reason="candidate_snapshot_applied",
        sportradar_captures=1,
        kalshi_frames=1,
    )


def _resolution(provider_path: Path) -> object:
    from inci_tennis_io.shadow_evidence import ShadowResolutionEvidence

    payload = provider_path.read_bytes()
    return ShadowResolutionEvidence(
        selected_wall_ns=WALL_NS + 2,
        provider_match_id=MATCH_ID,
        provider_start_wall_ns=WALL_NS - 10_000_000_000,
        event_ticker="KXTENNIS-MATCH",
        home_player_name="Player Home",
        away_player_name="Player Away",
        market_tickers=TICKERS,
        provider_discovery_raw_path=str(provider_path),
        provider_discovery_raw_sha256=sha256(payload).hexdigest(),
        kalshi_catalog_sha256="1" * 64,
        resolver_snapshot_sha256="2" * 64,
        resolver_rule_version="strict-name-start-v1",
    )


def _terminal(
    store: object,
    *,
    sportradar_captures: int = 1,
    kalshi_frames: int = 1,
) -> None:
    store.append_terminal(
        reason="duration_elapsed",
        code=None,
        ended_wall_ns=WALL_NS + 30,
        ended_monotonic_ns=30,
        provider_match_id=MATCH_ID,
        market_tickers=TICKERS,
        sportradar_captures=sportradar_captures,
        kalshi_frames=kalshi_frames,
    )


def _complete_session(root: Path) -> tuple[Path, Path]:
    from inci_tennis_io.shadow_evidence import ShadowEvidenceStore

    provider_path = root.parent / "provider-summary.json"
    _private_payload(provider_path, b'{"provider":"summary"}')
    with ShadowEvidenceStore(root) as store:
        ledger_path = store.ledger_path
        reference = store.persist_kalshi_frame(_frame())
        store.append_observation(_observation(provider_path, reference))
        _terminal(store)
    return ledger_path, provider_path


def _complete_resolved_session(root: Path) -> tuple[Path, Path]:
    from inci_tennis_io.shadow_evidence import ShadowEvidenceStore

    provider_path = root.parent / "provider-live-summaries.json"
    _private_payload(provider_path, b'{"provider":"live-summaries"}')
    summary_path = root.parent / "provider-summary.json"
    _private_payload(summary_path, b'{"provider":"summary"}')
    with ShadowEvidenceStore(root) as store:
        ledger_path = store.ledger_path
        store.append_resolution(_resolution(provider_path))
        reference = store.persist_kalshi_frame(_frame())
        store.append_observation(_observation(summary_path, reference))
        _terminal(store)
    return ledger_path, provider_path


def _read_rows(ledger: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in ledger.read_text().splitlines()]


def _rewrite_rows_with_valid_chain(
    ledger: Path, rows: list[dict[str, object]]
) -> None:
    previous = ZERO_DIGEST
    encoded_rows: list[str] = []
    for number, original in enumerate(rows, start=1):
        row = dict(original)
        row["row_number"] = number
        row["previous_row_sha256"] = previous
        row.pop("row_sha256", None)
        chain_payload = json.dumps(
            row, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        current = sha256(chain_payload).hexdigest()
        row["row_sha256"] = current
        encoded_rows.append(
            json.dumps(
                row, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        )
        previous = current
    ledger.write_text("\n".join(encoded_rows) + "\n")
    ledger.chmod(0o600)


class ShadowEvidenceIntegrityTests(unittest.TestCase):
    def test_resolution_is_first_durable_row_and_reaudits(self) -> None:
        """Catches losing the automatic identity proof before market frames."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger, _ = _complete_resolved_session(root)
            rows = _read_rows(ledger)

            self.assertEqual(rows[0]["kind"], "resolution")
            self.assertEqual(rows[0]["row_number"], 1)
            self.assertEqual(rows[0]["provider_match_id"], MATCH_ID)
            self.assertEqual(rows[0]["market_tickers"], list(TICKERS))
            with ShadowEvidenceStore(root) as reopened:
                _terminal(reopened, sportradar_captures=0, kalshi_frames=0)

    def test_resolution_must_be_once_and_before_every_other_row(self) -> None:
        """Catches replacing or inserting a chooser binding mid-session."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "shadow"
            provider = base / "provider-live-summaries.json"
            _private_payload(provider, b'{"provider":"live-summaries"}')
            with ShadowEvidenceStore(root) as store:
                value = _resolution(provider)
                store.append_resolution(value)
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_resolution_invalid"
                ):
                    store.append_resolution(value)
                store.append_terminal(
                    reason="duration_elapsed",
                    code=None,
                    ended_wall_ns=WALL_NS + 30,
                    ended_monotonic_ns=30,
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_captures=0,
                    kalshi_frames=0,
                )

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "shadow"
            provider = base / "provider-live-summaries.json"
            _private_payload(provider, b'{"provider":"live-summaries"}')
            with ShadowEvidenceStore(root) as store:
                store.persist_kalshi_frame(_frame())
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_resolution_invalid"
                ):
                    store.append_resolution(_resolution(provider))
                store.append_terminal(
                    reason="duration_elapsed",
                    code=None,
                    ended_wall_ns=WALL_NS + 30,
                    ended_monotonic_ns=30,
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_captures=0,
                    kalshi_frames=1,
                )

    def test_resolution_raw_reference_and_selected_identity_are_reaudited(self) -> None:
        """Catches tampering with discovery bytes or later selected identity."""

        for mutation in ("raw", "observation_identity", "terminal_identity"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                ledger, provider = _complete_resolved_session(root)
                if mutation == "raw":
                    provider.write_bytes(b'{"provider":"tampered"}')
                else:
                    rows = _read_rows(ledger)
                    target = rows[2] if mutation == "observation_identity" else rows[3]
                    target["market_tickers"] = [TICKERS[1], TICKERS[0]]
                    _rewrite_rows_with_valid_chain(ledger, rows)

                self._assert_reopen_rejected(root)

    def test_resolution_row_cannot_be_deleted_to_downgrade_auto_to_manual(self) -> None:
        """Catches prefix deletion bypassing the automatic-binding audit."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger, _ = _complete_resolved_session(root)
            rows = _read_rows(ledger)
            self.assertEqual(rows[0]["kind"], "resolution")
            _rewrite_rows_with_valid_chain(ledger, rows[1:])

            self._assert_reopen_rejected(root)

    def test_manual_session_without_resolution_remains_compatible(self) -> None:
        """Catches requiring new chooser evidence for explicit diagnostic mode."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            _complete_session(root)
            with ShadowEvidenceStore(root) as reopened:
                _terminal(reopened, sportradar_captures=0, kalshi_frames=0)

    def test_terminal_frame_count_must_equal_durable_capture_rows(self) -> None:
        """Catches a cancellation terminal hiding an already persisted frame."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as store:
                store.persist_kalshi_frame(_frame())
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_terminal_invalid"
                ):
                    store.append_terminal(
                        reason="cancelled",
                        code=None,
                        ended_wall_ns=WALL_NS + 30,
                        ended_monotonic_ns=30,
                        provider_match_id=MATCH_ID,
                        market_tickers=TICKERS,
                        sportradar_captures=0,
                        kalshi_frames=0,
                    )
                store.append_terminal(
                    reason="cancelled",
                    code=None,
                    ended_wall_ns=WALL_NS + 31,
                    ended_monotonic_ns=31,
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_captures=0,
                    kalshi_frames=1,
                )

    def _assert_reopen_rejected(self, root: Path) -> None:
        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with self.assertRaisesRegex(
            ShadowEvidenceError, "shadow_evidence_prior_corrupt"
        ):
            ShadowEvidenceStore(root)

    def test_rows_are_contiguous_and_deterministically_hash_chained(self) -> None:
        """Catches omitting a row from the session's tamper-evident chain."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger, _ = _complete_session(root)
            rows = _read_rows(ledger)

            previous = ZERO_DIGEST
            session_id = ledger.stem.removeprefix("session-")
            for number, row in enumerate(rows, start=1):
                self.assertEqual(row["session_id"], session_id)
                self.assertEqual(row["row_number"], number)
                self.assertEqual(row["previous_row_sha256"], previous)
                claimed = row.pop("row_sha256")
                encoded = json.dumps(
                    row,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                self.assertEqual(claimed, sha256(encoded).hexdigest())
                previous = claimed

            with ShadowEvidenceStore(root) as reopened:
                _terminal(
                    reopened,
                    sportradar_captures=0,
                    kalshi_frames=0,
                )

    def test_tampered_observation_field_is_rejected_on_reopen(self) -> None:
        """Catches an edited evidence value whose chain digest was not updated."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger, _ = _complete_session(root)
            rows = _read_rows(ledger)
            rows[1]["home_player_name"] = "Tampered Player"
            ledger.write_text(
                "\n".join(
                    json.dumps(row, sort_keys=True, separators=(",", ":"))
                    for row in rows
                )
                + "\n"
            )
            ledger.chmod(0o600)

            self._assert_reopen_rejected(root)

    def test_deleted_or_reordered_row_is_rejected_on_reopen(self) -> None:
        """Catches ledger truncation or line reordering across a valid session."""

        for mutation in ("deleted", "reordered"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                ledger, _ = _complete_session(root)
                lines = ledger.read_text().splitlines()
                if mutation == "deleted":
                    lines.pop(1)
                else:
                    lines[0], lines[1] = lines[1], lines[0]
                ledger.write_text("\n".join(lines) + "\n")
                ledger.chmod(0o600)

                self._assert_reopen_rejected(root)

    def test_duplicate_json_key_is_rejected_on_reopen(self) -> None:
        """Catches ambiguous JSON whose duplicate key could parse two ways."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger, _ = _complete_session(root)
            text = ledger.read_text().replace(
                '"kind":"kalshi_capture"',
                '"kind":"kalshi_capture","kind":"terminal"',
                1,
            )
            ledger.write_text(text)
            ledger.chmod(0o600)

            self._assert_reopen_rejected(root)

    def test_broken_session_or_row_metadata_is_rejected_on_reopen(self) -> None:
        """Catches session substitution and non-contiguous row numbering."""

        for field, value in (
            ("session_id", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
            ("row_number", 99),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                ledger, _ = _complete_session(root)
                rows = _read_rows(ledger)
                rows[1][field] = value
                ledger.write_text(
                    "\n".join(
                        json.dumps(row, sort_keys=True, separators=(",", ":"))
                        for row in rows
                    )
                    + "\n"
                )
                ledger.chmod(0o600)

                self._assert_reopen_rejected(root)

    def test_missing_or_tampered_provider_raw_is_rejected_on_reopen(self) -> None:
        """Catches an observation whose provider source bytes no longer verify."""

        for mutation in ("missing", "tampered"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                _, provider = _complete_session(root)
                if mutation == "missing":
                    provider.unlink()
                else:
                    provider.write_bytes(b'{"provider":"tampered"}')

                self._assert_reopen_rejected(root)

    def test_recomputed_chain_cannot_hide_unknown_row_shape(self) -> None:
        """Catches extra unreviewed fields even if an attacker recomputes hashes."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger, _ = _complete_session(root)
            rows = _read_rows(ledger)
            rows[1]["unexpected_field"] = "not reviewed"
            _rewrite_rows_with_valid_chain(ledger, rows)

            self._assert_reopen_rejected(root)

    def test_recomputed_chain_cannot_hide_wrong_terminal_schema(self) -> None:
        """Catches relabeling a terminal row with another reviewed schema."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger, _ = _complete_session(root)
            rows = _read_rows(ledger)
            rows[-1]["schema"] = "inci-tennis-unqualified-shadow-observation-v1"
            _rewrite_rows_with_valid_chain(ledger, rows)

            self._assert_reopen_rejected(root)

    def test_observation_requires_earlier_same_session_kalshi_receipt(self) -> None:
        """Catches an observation moved before the raw-capture receipt it cites."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger, _ = _complete_session(root)
            rows = _read_rows(ledger)
            _rewrite_rows_with_valid_chain(ledger, [rows[1], rows[0], rows[2]])

            self._assert_reopen_rejected(root)

    def test_recomputed_chain_cannot_hide_invalid_observation_semantics(self) -> None:
        """Catches internally inconsistent counters, references, or book status."""

        mutations = {
            "partial_kalshi_reference": {
                "kalshi_raw_sha256": None,
            },
            "candidate_missing_one_book": {
                "away_yes_bid": None,
                "away_yes_ask": None,
                "away_bid_depth": None,
                "away_ask_depth": None,
            },
            "noncandidate_exposes_book": {
                "kalshi_status": "incomplete",
                "reason": "candidate_book_incomplete",
            },
            "frames_without_reference": {
                "kalshi_raw_path": None,
                "kalshi_raw_sha256": None,
                "kalshi_captured_wall_ns": None,
                "kalshi_captured_monotonic_ns": None,
                "kalshi_generation": None,
                "kalshi_sequence": None,
                "kalshi_age_ns": None,
                "kalshi_status": "waiting",
                "home_yes_bid": None,
                "home_yes_ask": None,
                "home_bid_depth": None,
                "home_ask_depth": None,
                "away_yes_bid": None,
                "away_yes_ask": None,
                "away_bid_depth": None,
                "away_ask_depth": None,
                "reason": "candidate_book_incomplete",
                "kalshi_frames": 1,
            },
            "candidate_without_reference": {
                "kalshi_raw_path": None,
                "kalshi_raw_sha256": None,
                "kalshi_captured_wall_ns": None,
                "kalshi_captured_monotonic_ns": None,
                "kalshi_generation": None,
                "kalshi_sequence": None,
                "kalshi_age_ns": None,
                "kalshi_frames": 0,
            },
            "reference_without_frames": {
                "kalshi_frames": 0,
            },
            "observation_without_provider_capture": {
                "sportradar_captures": 0,
            },
        }
        for name, changes in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                ledger, _ = _complete_session(root)
                rows = _read_rows(ledger)
                rows[1].update(changes)
                _rewrite_rows_with_valid_chain(ledger, rows)

                self._assert_reopen_rejected(root)

    def test_append_rejects_kalshi_reference_not_previously_receipted(self) -> None:
        """Catches accepting a raw file that this session never durably receipted."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "shadow"
            provider = base / "provider-summary.json"
            _private_payload(provider, b'{"provider":"summary"}')
            with ShadowEvidenceStore(root) as store:
                receipt = store.persist_kalshi_frame(_frame())
                fake_raw = root / "raw" / "not-receipted.bin"
                _private_payload(fake_raw, b'{"other":"frame"}')
                fake = replace(
                    _observation(provider, receipt),
                    kalshi_raw_path=str(fake_raw),
                    kalshi_raw_sha256=sha256(fake_raw.read_bytes()).hexdigest(),
                )
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_reference_invalid"
                ):
                    store.append_observation(fake)
                fake_raw.unlink()
                _terminal(store)

    def test_append_rejects_malformed_market_with_stable_error(self) -> None:
        """Catches leaking an AttributeError for a malformed observation shape."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "shadow"
            provider = base / "provider-summary.json"
            _private_payload(provider, b'{"provider":"summary"}')
            with ShadowEvidenceStore(root) as store:
                receipt = store.persist_kalshi_frame(_frame())
                malformed = replace(
                    _observation(provider, receipt),
                    home_market=object(),
                )
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_row_invalid"
                ):
                    store.append_observation(malformed)
                _terminal(store)


if __name__ == "__main__":
    unittest.main()
