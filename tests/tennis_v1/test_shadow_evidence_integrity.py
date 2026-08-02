from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


MATCH_ID = "sr:sport_event:123456"
TICKERS = ("KXTENNIS-MATCH-HOME", "KXTENNIS-MATCH-AWAY")
WALL_NS = 1_785_607_205_000_000_000
ZERO_DIGEST = "0" * 64
FROZEN_LEGACY_TERMINAL_JSONL = (
    b'{"code":null,"ended_monotonic_ns":30,"ended_wall_ns":1785607205000000030,'
    b'"kalshi_frames":0,"kind":"terminal","market_tickers":["KXTENNIS-MATCH-HOME",'
    b'"KXTENNIS-MATCH-AWAY"],"previous_row_sha256":"0000000000000000000000000000000000000000000000000000000000000000",'
    b'"provider_match_id":"sr:sport_event:123456","reason":"duration_elapsed",'
    b'"row_number":1,"row_sha256":"f5bb90e33b258d9e42aab54fa29916ef7f250abd93ccf98dd3b087dcefaa828f",'
    b'"schema":"inci-tennis-unqualified-shadow-terminal-v1","session_id":"11111111-1111-4111-8111-111111111111",'
    b'"sportradar_captures":0,"trust":"unqualified_shadow"}\n'
)


def _watermark_bytes(
    session_id: str, row_number: int, row_sha256: str
) -> bytes:
    return (
        json.dumps(
            {
                "row_number": row_number,
                "row_sha256": row_sha256,
                "schema": "inci-tennis-shadow-commit-watermark-v1",
                "session_id": session_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


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


def _price_only_session(
    *,
    selected_wall_ns: int = WALL_NS + 2,
    predecessor_session_id: str | None = None,
    predecessor_terminal_row_sha256: str | None = None,
) -> object:
    from inci_tennis_io.shadow_evidence import (
        PriceOnlySessionEvidence,
        ShadowMarketCandidate,
    )

    return PriceOnlySessionEvidence(
        selected_wall_ns=selected_wall_ns,
        selected_monotonic_ns=2,
        event_ticker="KXTENNIS-MATCH",
        player_a_name="Player A",
        player_b_name="Player B",
        market_tickers=TICKERS,
        scheduled_start_wall_ns=WALL_NS - 10_000_000_000,
        catalog_sport="tennis",
        catalog_scope="atp",
        catalog_queried_competitions=("atp", "atp_challenger"),
        catalog_series_ticker="KXATP",
        catalog_milestone_id="match",
        catalog_milestone_league="ATP",
        initial_book_state="one_sided",
        initial_market_a=ShadowMarketCandidate(
            TICKERS[0], "0.31", None, "12.00", None
        ),
        initial_market_b=ShadowMarketCandidate(
            TICKERS[1], None, "0.69", None, "9.00"
        ),
        provider_discovery_state="unavailable",
        provider_discovery_reason="provider_key_missing",
        provider_discovery_raw_path=None,
        provider_discovery_raw_sha256=None,
        kalshi_catalog_sha256="1" * 64,
        resolver_snapshot_sha256="2" * 64,
        resolver_version="kalshi-first-hybrid-v1",
        registry_digest="3" * 64,
        predecessor_session_id=predecessor_session_id,
        predecessor_terminal_row_sha256=predecessor_terminal_row_sha256,
    )


def _price_only_observation(reference: object) -> object:
    from inci_tennis_io.shadow_evidence import (
        PriceOnlyEvidenceObservation,
        ShadowMarketCandidate,
    )

    return PriceOnlyEvidenceObservation(
        observed_wall_ns=WALL_NS + 20,
        observed_monotonic_ns=20,
        clock_uncertainty_ns=3,
        event_ticker="KXTENNIS-MATCH",
        market_tickers=TICKERS,
        kalshi_raw_path=reference.raw_path,
        kalshi_raw_sha256=reference.raw_sha256,
        kalshi_captured_wall_ns=reference.captured_wall_ns,
        kalshi_captured_monotonic_ns=reference.captured_monotonic_ns,
        kalshi_generation=1,
        kalshi_sequence=1,
        kalshi_age_ns=1,
        kalshi_status="candidate",
        market_a=ShadowMarketCandidate(
            TICKERS[0], "0.31", "0.34", "12.00", "8.00"
        ),
        market_b=ShadowMarketCandidate(
            TICKERS[1], "0.66", "0.69", "6.00", "9.00"
        ),
        reason="candidate_snapshot_applied",
        kalshi_frames=1,
    )


def _price_only_reference() -> object:
    return SimpleNamespace(
        raw_path="/unreceipted/kalshi.bin",
        raw_sha256="4" * 64,
        captured_wall_ns=WALL_NS + 10,
        captured_monotonic_ns=10,
    )


def _price_only_terminal(*, kalshi_frames: int = 1) -> dict[str, object]:
    return {
        "reason": "duration_elapsed",
        "code": None,
        "ended_wall_ns": WALL_NS + 30,
        "ended_monotonic_ns": 30,
        "event_ticker": "KXTENNIS-MATCH",
        "market_tickers": TICKERS,
        "kalshi_frames": kalshi_frames,
    }


def _complete_price_only_session(root: Path) -> Path:
    from inci_tennis_io.shadow_evidence import ShadowEvidenceStore

    with ShadowEvidenceStore(root) as store:
        ledger = store.ledger_path
        store.append_price_only_session(_price_only_session())
        receipt = store.persist_kalshi_frame(_frame())
        store.append_price_only_observation(_price_only_observation(receipt))
        store.append_price_only_terminal(**_price_only_terminal())
    return ledger


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

    def test_price_only_session_reopens_without_synchronized_fields(self) -> None:
        """Catches price-only rows accidentally depending on provider evidence."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as store:
                ledger = store.ledger_path
                store.append_price_only_session(_price_only_session())
                receipt = store.persist_kalshi_frame(_frame())
                store.append_price_only_observation(
                    _price_only_observation(receipt)
                )
                store.append_price_only_terminal(**_price_only_terminal())
                terminal_digest = store.terminal_row_sha256

            rows = _read_rows(ledger)
            self.assertEqual(
                [row["kind"] for row in rows],
                [
                    "price_only_session",
                    "price_only_kalshi_capture",
                    "price_only_observation",
                    "price_only_terminal",
                ],
            )
            self.assertEqual([row["trust"] for row in rows], ["PRICE_ONLY"] * 4)
            self.assertEqual(rows[-1]["session_row_sha256"], rows[0]["row_sha256"])
            self.assertEqual(rows[-1]["row_sha256"], terminal_digest)
            self.assertFalse(
                any(
                    "home" in field.casefold() or "away" in field.casefold()
                    for row in rows
                    for field in row
                )
            )
            forbidden = (
                "provider",
                "score",
                "signal",
                "profit",
                "loss",
                "recommendation",
                "order",
            )
            self.assertFalse(
                any(
                    any(term in field.casefold() for term in forbidden)
                    for row in rows[1:]
                    for field in row
                )
            )
            ShadowEvidenceStore(root).close()

    def test_kalshi_only_credentials_do_not_require_sportradar(self) -> None:
        """Catches retaining the provider-key requirement for price-only runs."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            load_kalshi_only_credential_material,
        )

        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "kalshi.pem"
            _private_payload(key_path, b"private-key")
            material = load_kalshi_only_credential_material(
                {
                    "KALSHI_API_KEY_ID": "kalshi-read-only",
                    "KALSHI_PRIVATE_KEY_PATH": str(key_path),
                }
            )
            self.assertEqual(material.kalshi_api_key_id, "kalshi-read-only")
            self.assertEqual(material.kalshi_private_key_path, key_path)
            with self.assertRaisesRegex(
                ShadowEvidenceError, "shadow_credentials_missing"
            ):
                load_kalshi_only_credential_material({})

    def test_price_only_audit_rejects_kind_trust_mixing_and_field_injection(self) -> None:
        """Catches a recomputed chain that changes the sealed price-only shape."""

        for mutation in ("trust", "provider_field", "legacy_kind"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                ledger = _complete_price_only_session(root)
                rows = _read_rows(ledger)
                if mutation == "trust":
                    rows[1]["trust"] = "unqualified_shadow"
                elif mutation == "provider_field":
                    rows[2]["provider_match_id"] = MATCH_ID
                else:
                    rows[1]["kind"] = "kalshi_capture"
                _rewrite_rows_with_valid_chain(ledger, rows)
                self._assert_reopen_rejected(root)

    def test_price_only_requires_session_first_and_receipt_before_candidate(self) -> None:
        """Catches mixing grammars or projecting a candidate before durable raw evidence."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as store:
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_row_invalid"
                ):
                    store.append_price_only_observation(
                        _price_only_observation(_price_only_reference())
                    )
                store.append_price_only_session(_price_only_session())
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_reference_invalid"
                ):
                    store.append_price_only_observation(
                        _price_only_observation(_price_only_reference())
                    )
                store.append_price_only_terminal(
                    **_price_only_terminal(kalshi_frames=0)
                )

    def test_price_only_candidate_and_terminal_invariants_reaudit(self) -> None:
        """Catches book exposure, first-row deletion, or terminal identity/count drift."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        mutations = {
            "candidate_hides_book": {"market_a_yes_bid": None},
            "noncandidate_exposes_book": {
                "kalshi_status": "waiting",
                "reason": "candidate_book_incomplete",
            },
            "terminal_count": {"kalshi_frames": 0},
            "terminal_identity": {"event_ticker": "KXTENNIS-OTHER"},
            "session_no_execution_literal": {
                "execution_authorized": True,
            },
        }
        for name, changes in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                ledger = _complete_price_only_session(root)
                rows = _read_rows(ledger)
                target = (
                    rows[-1]
                    if name.startswith("terminal")
                    else rows[0]
                    if name.startswith("session")
                    else rows[2]
                )
                target.update(changes)
                _rewrite_rows_with_valid_chain(ledger, rows)
                if name.startswith("session"):
                    rows = _read_rows(ledger)
                    rows[-1]["session_row_sha256"] = rows[0]["row_sha256"]
                    _rewrite_rows_with_valid_chain(ledger, rows)
                self._assert_reopen_rejected(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger = _complete_price_only_session(root)
            rows = _read_rows(ledger)
            _rewrite_rows_with_valid_chain(ledger, rows[1:])
            with self.assertRaisesRegex(
                ShadowEvidenceError,
                "shadow_evidence_(prior_corrupt|unclean_session)",
            ):
                ShadowEvidenceStore(root)

    def test_price_only_raw_integrity_and_unclean_sessions_reject_reopen(self) -> None:
        """Catches raw rollback and allowing an unfinished price-only session to pass."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        for mutation in ("missing", "tampered", "orphan", "unclean"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                ledger = _complete_price_only_session(root)
                rows = _read_rows(ledger)
                raw_path = Path(rows[1]["raw_path"])
                if mutation == "missing":
                    raw_path.unlink()
                elif mutation == "tampered":
                    raw_path.write_bytes(b'{"tampered":true}')
                    raw_path.chmod(0o600)
                elif mutation == "orphan":
                    _private_payload(root / "raw" / "orphan.bin", b"orphan")
                else:
                    ledger.write_text("\n".join(ledger.read_text().splitlines()[:-1]) + "\n")
                    ledger.chmod(0o600)
                with self.assertRaisesRegex(
                    ShadowEvidenceError,
                    "shadow_evidence_(prior_corrupt|unclean_session)",
                ):
                    ShadowEvidenceStore(root)

    def test_price_only_and_legacy_grammars_cannot_be_mixed_in_either_direction(self) -> None:
        """Catches an append API silently relabeling an already selected grammar."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as store:
                store.append_price_only_session(_price_only_session())
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_row_invalid"
                ):
                    store.append_observation(object())
                store.append_price_only_terminal(
                    **_price_only_terminal(kalshi_frames=0)
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as store:
                store.persist_kalshi_frame(_frame())
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_row_invalid"
                ):
                    store.append_price_only_session(_price_only_session())
                _terminal(store, sportradar_captures=0, kalshi_frames=1)

    def test_price_only_terminal_digest_and_failover_predecessor_are_bound(self) -> None:
        """Catches linking failover to anything other than a durable terminal row."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            legacy_ledger, _ = _complete_resolved_session(root)
            legacy_rows = _read_rows(legacy_ledger)
            predecessor_digest = legacy_rows[-1]["row_sha256"]
            predecessor_session_id = legacy_ledger.stem.removeprefix("session-")
            self.assertIsInstance(predecessor_digest, str)

            with ShadowEvidenceStore(root) as second:
                second.append_price_only_session(
                    _price_only_session(
                        selected_wall_ns=WALL_NS + 40,
                        predecessor_session_id=predecessor_session_id,
                        predecessor_terminal_row_sha256=predecessor_digest,
                    )
                )
                second.append_price_only_terminal(
                    **_price_only_terminal(kalshi_frames=0)
                )

            with ShadowEvidenceStore(root) as rejected:
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_row_invalid"
                ):
                    rejected.append_price_only_session(
                        replace(
                            _price_only_session(),
                            predecessor_session_id=predecessor_session_id,
                        )
                    )

    def test_legacy_rows_remain_byte_identical_through_price_only_audit_extension(self) -> None:
        """Catches the additive auditor rewriting a frozen synchronized ledger."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger, _ = _complete_session(root)
            frozen_legacy_bytes = ledger.read_bytes()
            with ShadowEvidenceStore(root) as reopened:
                _terminal(reopened, sportradar_captures=0, kalshi_frames=0)
            self.assertEqual(ledger.read_bytes(), frozen_legacy_bytes)

    def test_literal_frozen_legacy_terminal_writer_and_reopen_do_not_mutate(self) -> None:
        """Catches a price-only refactor changing a pre-existing terminal byte."""

        import inci_tennis_io.shadow_evidence as evidence_module
        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore

        fixed_session_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with patch.object(evidence_module.uuid, "uuid4", return_value=fixed_session_id):
                with ShadowEvidenceStore(root) as store:
                    ledger = store.ledger_path
                    _terminal(store, sportradar_captures=0, kalshi_frames=0)
            self.assertEqual(ledger.read_bytes(), FROZEN_LEGACY_TERMINAL_JSONL)

            with ShadowEvidenceStore(root) as reopened:
                _terminal(reopened, sportradar_captures=0, kalshi_frames=0)
            self.assertEqual(ledger.read_bytes(), FROZEN_LEGACY_TERMINAL_JSONL)

    def test_price_only_crash_boundaries_refuse_the_next_session(self) -> None:
        """Catches accepting a session interrupted after any durable nonterminal row."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        for boundary in ("session", "capture_receipt", "observation"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                with ShadowEvidenceStore(root) as store:
                    store.append_price_only_session(_price_only_session())
                    if boundary != "session":
                        receipt = store.persist_kalshi_frame(_frame())
                        if boundary == "observation":
                            store.append_price_only_observation(
                                _price_only_observation(receipt)
                            )
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_unclean_session"
                ):
                    ShadowEvidenceStore(root)

    def test_price_only_initial_book_snapshot_is_complete_and_state_bound(self) -> None:
        """Catches reducing the selected catalog snapshot to a state label."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
            ShadowMarketCandidate,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as store:
                store.append_price_only_session(_price_only_session())
                row = _read_rows(store.ledger_path)[0]
                self.assertEqual(
                    (
                        row["initial_market_a_ticker"],
                        row["initial_market_a_yes_bid"],
                        row["initial_market_a_yes_ask"],
                        row["initial_market_a_bid_depth"],
                        row["initial_market_a_ask_depth"],
                    ),
                    (TICKERS[0], "0.31", None, "12.00", None),
                )
                self.assertEqual(
                    (
                        row["initial_market_b_ticker"],
                        row["initial_market_b_yes_bid"],
                        row["initial_market_b_yes_ask"],
                        row["initial_market_b_bid_depth"],
                        row["initial_market_b_ask_depth"],
                    ),
                    (TICKERS[1], None, "0.69", None, "9.00"),
                )
                store.append_price_only_terminal(
                    **_price_only_terminal(kalshi_frames=0)
                )

        invalid_states = (
            replace(_price_only_session(), initial_book_state="empty"),
            replace(_price_only_session(), initial_book_state="two_sided"),
            replace(
                _price_only_session(),
                initial_market_a=ShadowMarketCandidate(
                    TICKERS[0], None, None, None, None
                ),
                initial_market_b=ShadowMarketCandidate(
                    TICKERS[1], None, None, None, None
                ),
            ),
        )
        for value in invalid_states:
            with self.subTest(value=value.initial_book_state), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                with ShadowEvidenceStore(root) as store:
                    with self.assertRaisesRegex(
                        ShadowEvidenceError, "shadow_evidence_row_invalid"
                    ):
                        store.append_price_only_session(value)

    def test_price_only_terminal_is_narrow_and_digest_is_read_only(self) -> None:
        """Catches unsupported close meanings or exposing an unfynced terminal."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as store:
                self.assertIsNone(store.terminal_row_sha256)
                with self.assertRaises(AttributeError):
                    store.terminal_row_sha256 = "f" * 64
                store.append_price_only_session(_price_only_session())
                for reason, code in (
                    ("closed", None),
                    ("abandoned", None),
                    ("duration_elapsed", "not-allowed"),
                    ("halted", 7),
                    ("halted", "not-allowed"),
                ):
                    with self.assertRaisesRegex(
                        ShadowEvidenceError, "shadow_evidence_terminal_invalid"
                    ):
                        store.append_price_only_terminal(
                            **{
                                **_price_only_terminal(kalshi_frames=0),
                                "reason": reason,
                                "code": code,
                            }
                        )
                store.append_price_only_terminal(
                    **_price_only_terminal(kalshi_frames=0)
                )
                persisted = _read_rows(store.ledger_path)[-1]["row_sha256"]
                self.assertEqual(store.terminal_row_sha256, persisted)
                with self.assertRaises(AttributeError):
                    store.terminal_row_sha256 = "f" * 64

    def test_price_only_predecessor_requires_verified_auto_terminal(self) -> None:
        """Catches accepting price-only, manual, future, or nonterminal links."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        for mode in ("nonterminal", "future", "nonexistent"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                verified, _ = _complete_resolved_session(root)
                verified_rows = _read_rows(verified)
                verified_id = verified.stem.removeprefix("session-")
                session_id, digest, selected = (
                    (verified_id, verified_rows[0]["row_sha256"], WALL_NS + 40)
                    if mode == "nonterminal"
                    else (verified_id, verified_rows[-1]["row_sha256"], WALL_NS + 1)
                    if mode == "future"
                    else (
                        "11111111-1111-4111-8111-111111111111",
                        "a" * 64,
                        WALL_NS + 40,
                    )
                )
                with ShadowEvidenceStore(root) as store:
                    with self.assertRaisesRegex(
                        ShadowEvidenceError, "shadow_evidence_reference_invalid"
                    ):
                        store.append_price_only_session(
                            _price_only_session(
                                selected_wall_ns=selected,
                                predecessor_session_id=session_id,
                                predecessor_terminal_row_sha256=digest,
                            )
                        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as price_only:
                price_only.append_price_only_session(_price_only_session())
                price_only.append_price_only_terminal(
                    **_price_only_terminal(kalshi_frames=0)
                )
                price_id = price_only.session_id
                price_digest = price_only.terminal_row_sha256
            with ShadowEvidenceStore(root) as store:
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_reference_invalid"
                ):
                    store.append_price_only_session(
                        _price_only_session(
                            selected_wall_ns=WALL_NS + 40,
                            predecessor_session_id=price_id,
                            predecessor_terminal_row_sha256=price_digest,
                        )
                    )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as manual:
                _terminal(manual, sportradar_captures=0, kalshi_frames=0)
                manual_id = manual.session_id
                manual_digest = manual.terminal_row_sha256
            with ShadowEvidenceStore(root) as store:
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_reference_invalid"
                ):
                    store.append_price_only_session(
                        _price_only_session(
                            selected_wall_ns=WALL_NS + 40,
                            predecessor_session_id=manual_id,
                            predecessor_terminal_row_sha256=manual_digest,
                        )
                    )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as store:
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_reference_invalid"
                ):
                    store.append_price_only_session(
                        _price_only_session(
                            selected_wall_ns=WALL_NS + 40,
                            predecessor_session_id=store.session_id,
                            predecessor_terminal_row_sha256="a" * 64,
                        )
                    )

    def test_raw_append_failures_poison_without_publishing_a_receipt(self) -> None:
        """Catches continuing after a raw durability boundary fails."""

        import inci_tennis_io.shadow_evidence as evidence_module
        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        for boundary in ("open", "raw_write", "raw_directory", "ledger"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                with ShadowEvidenceStore(root) as store:
                    store.append_price_only_session(_price_only_session())
                    row_number_before = store._row_number
                    previous_digest_before = store._previous_row_sha256
                    original_open = evidence_module._open_private_file
                    original_write = evidence_module._write_all
                    original_directory = evidence_module._fsync_directory
                    calls = 0

                    def fail_open(path: Path, **kwargs: object) -> int:
                        if path.parent == store.raw_root:
                            raise ShadowEvidenceError("shadow_evidence_state_unavailable")
                        return original_open(path, **kwargs)

                    def fail_second_write(*args: object, **kwargs: object) -> None:
                        nonlocal calls
                        calls += 1
                        if boundary == "ledger" and calls == 3:
                            raise ShadowEvidenceError("shadow_evidence_write_failed")
                        if boundary == "raw_write" and calls == 1:
                            raise ShadowEvidenceError("shadow_evidence_raw_write_failed")
                        original_write(*args, **kwargs)

                    def fail_raw_directory(path: Path) -> None:
                        if path == store.raw_root:
                            raise ShadowEvidenceError("shadow_evidence_write_failed")
                        original_directory(path)

                    with (
                        patch.object(
                            evidence_module,
                            "_open_private_file",
                            fail_open if boundary == "open" else original_open,
                        ),
                        patch.object(
                            evidence_module,
                            "_write_all",
                            fail_second_write,
                        ),
                        patch.object(
                            evidence_module,
                            "_fsync_directory",
                            fail_raw_directory
                            if boundary == "raw_directory"
                            else original_directory,
                        ),
                    ):
                        with self.assertRaises(ShadowEvidenceError):
                            store.persist_kalshi_frame(_frame())
                    self.assertTrue(store._poisoned)
                    self.assertEqual(store._raw_number, 0)
                    self.assertEqual(store._kalshi_receipts, {})
                    self.assertEqual(store._row_number, row_number_before)
                    self.assertEqual(
                        store._previous_row_sha256, previous_digest_before
                    )
                    self.assertIsNone(store.terminal_row_sha256)
                    with self.assertRaisesRegex(
                        ShadowEvidenceError, "shadow_evidence_closed"
                    ):
                        store.persist_kalshi_frame(_frame())
                with self.assertRaisesRegex(
                    ShadowEvidenceError,
                    "shadow_evidence_(prior_corrupt|unclean_session)",
                ):
                    ShadowEvidenceStore(root)

    def test_raw_fsync_boundaries_poison_without_a_receipt(self) -> None:
        """Catches publishing a capture when its raw durability chain faults."""

        import inci_tennis_io.shadow_evidence as evidence_module
        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        for boundary in (
            "raw_fsync",
            "raw_directory_fsync",
            "capture_ledger_fsync",
        ):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                with ShadowEvidenceStore(root) as store:
                    store.append_price_only_session(_price_only_session())
                    row_number_before = store._row_number
                    previous_digest_before = store._previous_row_sha256
                    original_open = evidence_module._open_private_file
                    original_fsync = evidence_module.os.fsync
                    original_directory = evidence_module._fsync_directory
                    raw_descriptors: set[int] = set()

                    def track_open(path: Path, **kwargs: object) -> int:
                        descriptor = original_open(path, **kwargs)
                        if path.parent == store.raw_root:
                            raw_descriptors.add(descriptor)
                        return descriptor

                    def fail_fsync(descriptor: int) -> None:
                        if (
                            boundary == "raw_fsync"
                            and descriptor in raw_descriptors
                        ) or (
                            boundary == "capture_ledger_fsync"
                            and descriptor == store._ledger_fd
                        ):
                            raise OSError("injected fsync failure")
                        original_fsync(descriptor)

                    def fail_raw_directory(path: Path) -> None:
                        if boundary == "raw_directory_fsync" and path == store.raw_root:
                            raise ShadowEvidenceError(
                                "shadow_evidence_write_failed"
                            )
                        original_directory(path)

                    with (
                        patch.object(
                            evidence_module, "_open_private_file", track_open
                        ),
                        patch.object(evidence_module.os, "fsync", fail_fsync),
                        patch.object(
                            evidence_module,
                            "_fsync_directory",
                            fail_raw_directory,
                        ),
                    ):
                        with self.assertRaises(ShadowEvidenceError):
                            store.persist_kalshi_frame(_frame())
                    self.assertTrue(store._poisoned)
                    self.assertEqual(store._raw_number, 0)
                    self.assertEqual(store._kalshi_receipts, {})
                    self.assertEqual(store._row_number, row_number_before)
                    self.assertEqual(
                        store._previous_row_sha256, previous_digest_before
                    )
                    with self.assertRaisesRegex(
                        ShadowEvidenceError, "shadow_evidence_closed"
                    ):
                        store.persist_kalshi_frame(_frame())
                with self.assertRaisesRegex(
                    ShadowEvidenceError,
                    "shadow_evidence_(prior_corrupt|unclean_session)",
                ):
                    ShadowEvidenceStore(root)

    def test_ledger_faults_poison_capture_observation_and_terminal_before_publish(self) -> None:
        """Catches ledger failures publishing any append type before its marker clears."""

        import inci_tennis_io.shadow_evidence as evidence_module
        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        for operation, boundary in (
            ("capture", "ledger_write"),
            ("capture", "ledger_fsync"),
            ("observation", "ledger_write"),
            ("observation", "ledger_fsync"),
            ("terminal", "ledger_write"),
            ("terminal", "ledger_fsync"),
        ):
            with self.subTest(operation=operation, boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                with ShadowEvidenceStore(root) as store:
                    store.append_price_only_session(_price_only_session())
                    receipt = (
                        store.persist_kalshi_frame(_frame())
                        if operation == "observation"
                        else None
                    )
                    row_number_before = store._row_number
                    raw_number_before = store._raw_number
                    receipts_before = dict(store._kalshi_receipts)
                    previous_digest_before = store._previous_row_sha256
                    pending_path = root / f"session-{store.session_id}.pending"
                    original_write = evidence_module._write_all
                    original_fsync = evidence_module.os.fsync

                    if boundary == "ledger_write":
                        def fail_write(
                            descriptor: int, payload: bytes, code: str
                        ) -> None:
                            if descriptor == store._ledger_fd:
                                raise ShadowEvidenceError(
                                    "shadow_evidence_write_failed"
                                )
                            original_write(descriptor, payload, code)

                        fault = patch.object(
                            evidence_module, "_write_all", fail_write
                        )
                    else:
                        def fail_fsync(descriptor: int) -> None:
                            if descriptor == store._ledger_fd:
                                raise OSError("injected ledger fsync failure")
                            original_fsync(descriptor)

                        fault = patch.object(
                            evidence_module.os, "fsync", fail_fsync
                        )

                    with fault:
                        with self.assertRaises(ShadowEvidenceError):
                            if operation == "capture":
                                store.persist_kalshi_frame(_frame())
                            elif operation == "observation":
                                store.append_price_only_observation(
                                    _price_only_observation(receipt)
                                )
                            else:
                                store.append_price_only_terminal(
                                    **_price_only_terminal(kalshi_frames=0)
                                )
                    self.assertTrue(store._poisoned)
                    self.assertTrue(pending_path.exists())
                    self.assertEqual(store._row_number, row_number_before)
                    self.assertEqual(store._raw_number, raw_number_before)
                    self.assertEqual(store._kalshi_receipts, receipts_before)
                    self.assertEqual(
                        store._previous_row_sha256, previous_digest_before
                    )
                    self.assertIsNone(store.terminal_row_sha256)
                    with self.assertRaisesRegex(
                        ShadowEvidenceError, "shadow_evidence_closed"
                    ):
                        store.append_price_only_terminal(
                            **_price_only_terminal(kalshi_frames=raw_number_before)
                        )
                with self.assertRaisesRegex(
                    ShadowEvidenceError,
                    "shadow_evidence_(prior_corrupt|unclean_session)",
                ):
                    ShadowEvidenceStore(root)

    def test_pending_marker_is_canonical_and_cleared_before_publish(self) -> None:
        """Catches appending a row before a durable canonical pending marker."""

        import inci_tennis_io.shadow_evidence as evidence_module
        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as store:
                pending_path = root / f"session-{store.session_id}.pending"
                original_write = evidence_module._write_all
                marker_seen = False

                def inspect_ledger_write(
                    descriptor: int, payload: bytes, code: str
                ) -> None:
                    nonlocal marker_seen
                    row = json.loads(payload)
                    if row.get("kind") == "price_only_session":
                        self.assertTrue(pending_path.is_file())
                        marker_bytes = pending_path.read_bytes()
                        marker = json.loads(marker_bytes)
                        self.assertEqual(
                            marker_bytes,
                            _watermark_bytes(
                                store.session_id, 1, row["row_sha256"]
                            ),
                        )
                        self.assertEqual(marker["row_sha256"], row["row_sha256"])
                        marker_seen = True
                    original_write(descriptor, payload, code)

                with patch.object(
                    evidence_module, "_write_all", inspect_ledger_write
                ):
                    store.append_price_only_session(_price_only_session())
                self.assertTrue(marker_seen)
                self.assertFalse(pending_path.exists())
                self.assertEqual(store._row_number, 1)
                self.assertEqual(
                    store._previous_row_sha256,
                    _read_rows(store.ledger_path)[0]["row_sha256"],
                )
                store.append_price_only_terminal(
                    **_price_only_terminal(kalshi_frames=0)
                )
            with ShadowEvidenceStore(root) as reopened:
                _terminal(reopened, sportradar_captures=0, kalshi_frames=0)

    def test_pending_prewrite_failures_poison_before_row_publication(self) -> None:
        """Catches accepting a row when pending-marker preparation fails."""

        import inci_tennis_io.shadow_evidence as evidence_module
        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        for boundary in (
            "marker_open",
            "marker_write",
            "marker_fsync",
            "marker_directory_fsync",
        ):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                with ShadowEvidenceStore(root) as store:
                    pending_path = root / f"session-{store.session_id}.pending"
                    original_open = evidence_module._open_private_file
                    original_write = evidence_module._write_all
                    original_fsync = evidence_module.os.fsync
                    pending_descriptors: set[int] = set()

                    def track_open(path: Path, **kwargs: object) -> int:
                        descriptor = original_open(path, **kwargs)
                        if path == pending_path:
                            pending_descriptors.add(descriptor)
                        return descriptor

                    def fail_open(path: Path, **kwargs: object) -> int:
                        if path == pending_path:
                            raise ShadowEvidenceError(
                                "shadow_evidence_write_failed"
                            )
                        return track_open(path, **kwargs)

                    def fail_pending_write(
                        descriptor: int, payload: bytes, code: str
                    ) -> None:
                        if b'"schema":"inci-tennis-shadow-commit-watermark-v1"' in payload:
                            raise ShadowEvidenceError(
                                "shadow_evidence_write_failed"
                            )
                        original_write(descriptor, payload, code)

                    def fail_pending_fsync(descriptor: int) -> None:
                        if descriptor in pending_descriptors:
                            raise OSError("injected pending fsync failure")
                        original_fsync(descriptor)

                    original_directory = evidence_module._fsync_directory

                    def fail_marker_directory(path: Path) -> None:
                        if path == root:
                            raise ShadowEvidenceError(
                                "shadow_evidence_write_failed"
                            )
                        original_directory(path)

                    with (
                        patch.object(
                            evidence_module,
                            "_open_private_file",
                            fail_open if boundary == "marker_open" else track_open,
                        ),
                        patch.object(
                            evidence_module,
                            "_write_all",
                            fail_pending_write
                            if boundary == "marker_write"
                            else original_write,
                        ),
                        patch.object(
                            evidence_module.os,
                            "fsync",
                            fail_pending_fsync
                            if boundary == "marker_fsync"
                            else original_fsync,
                        ),
                        patch.object(
                            evidence_module,
                            "_fsync_directory",
                            fail_marker_directory
                            if boundary == "marker_directory_fsync"
                            else original_directory,
                        ),
                    ):
                        with self.assertRaises(ShadowEvidenceError):
                            store.append_price_only_session(_price_only_session())
                    self.assertTrue(store._poisoned)
                    self.assertEqual(store._row_number, 0)
                    self.assertEqual(store._previous_row_sha256, ZERO_DIGEST)
                    self.assertEqual(store._raw_number, 0)
                    self.assertEqual(store._kalshi_receipts, {})
                    self.assertIsNone(store.terminal_row_sha256)
                    if boundary != "marker_open":
                        self.assertTrue(pending_path.exists())
                    with self.assertRaisesRegex(
                        ShadowEvidenceError,
                        "shadow_evidence_(closed|row_invalid)",
                    ):
                        store.append_price_only_session(_price_only_session())
                with self.assertRaisesRegex(
                    ShadowEvidenceError,
                    "shadow_evidence_(prior_corrupt|unclean_session|state_unsafe)",
                ):
                    ShadowEvidenceStore(root)

    def test_commit_transition_faults_never_publish_before_watermark_durability(self) -> None:
        """Catches publishing a receipt or terminal before the commit watermark."""

        import inci_tennis_io.shadow_evidence as evidence_module
        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        for operation, boundary in (
            ("capture", "replace"),
            ("capture", "commit_directory_fsync"),
            ("terminal", "replace"),
            ("terminal", "commit_directory_fsync"),
        ):
            with self.subTest(operation=operation, boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "shadow"
                with ShadowEvidenceStore(root) as store:
                    store.append_price_only_session(_price_only_session())
                    pending_path = root / f"session-{store.session_id}.pending"
                    commit_path = root / f"session-{store.session_id}.commit"
                    self.assertTrue(commit_path.is_file())
                    previous_commit = commit_path.read_bytes()
                    row_number_before = store._row_number
                    previous_digest_before = store._previous_row_sha256
                    original_replace = evidence_module.os.replace
                    original_directory = evidence_module._fsync_directory
                    root_fsyncs = 0

                    def fail_replace(source: Path, destination: Path) -> None:
                        if source == pending_path and destination == commit_path:
                            raise OSError("injected watermark replace failure")
                        original_replace(source, destination)

                    def fail_commit_directory(path: Path) -> None:
                        nonlocal root_fsyncs
                        if path == root:
                            root_fsyncs += 1
                            if root_fsyncs == 2:
                                raise ShadowEvidenceError(
                                    "shadow_evidence_write_failed"
                                )
                        original_directory(path)

                    with (
                        patch.object(
                            evidence_module.os,
                            "replace",
                            fail_replace if boundary == "replace" else original_replace,
                        ),
                        patch.object(
                            evidence_module,
                            "_fsync_directory",
                            fail_commit_directory
                            if boundary == "commit_directory_fsync"
                            else original_directory,
                        ),
                    ):
                        with self.assertRaises(ShadowEvidenceError):
                            if operation == "capture":
                                store.persist_kalshi_frame(_frame())
                            else:
                                store.append_price_only_terminal(
                                    **_price_only_terminal(kalshi_frames=0)
                                )
                    self.assertTrue(store._poisoned)
                    self.assertEqual(store._row_number, row_number_before)
                    self.assertEqual(
                        store._previous_row_sha256, previous_digest_before
                    )
                    self.assertEqual(store._raw_number, 0)
                    self.assertEqual(store._kalshi_receipts, {})
                    self.assertIsNone(store.terminal_row_sha256)
                    with self.assertRaisesRegex(
                        ShadowEvidenceError, "shadow_evidence_closed"
                    ):
                        store.append_price_only_terminal(
                            **_price_only_terminal(kalshi_frames=0)
                        )
                    if boundary == "replace":
                        self.assertTrue(pending_path.exists())
                        self.assertEqual(commit_path.read_bytes(), previous_commit)
                    else:
                        self.assertFalse(pending_path.exists())
                        self.assertEqual(
                            json.loads(commit_path.read_bytes())["row_sha256"],
                            _read_rows(store.ledger_path)[-1]["row_sha256"],
                        )
                if boundary == "commit_directory_fsync" and operation == "terminal":
                    with ShadowEvidenceStore(root) as reopened:
                        _terminal(reopened, sportradar_captures=0, kalshi_frames=0)
                else:
                    with self.assertRaisesRegex(
                        ShadowEvidenceError,
                        "shadow_evidence_(prior_corrupt|unclean_session|state_unsafe)",
                    ):
                        ShadowEvidenceStore(root)

    def test_pending_marker_inventory_rejects_crash_and_invalid_artifacts(self) -> None:
        """Catches ignoring pending-marker artifacts outside the ledger glob."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        for artifact in (
            "surviving",
            "malformed",
            "orphan",
            "poisoned",
            "mode",
            "directory",
            "symlink",
            "hardlink",
            "unknown",
            "multiple",
        ):
            with self.subTest(artifact=artifact), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                root = base / "shadow"
                ledger, _ = _complete_session(root)
                session_id = ledger.stem.removeprefix("session-")
                marker_id = (
                    "11111111-1111-4111-8111-111111111111"
                    if artifact == "orphan"
                    else session_id
                )
                pending_path = root / f"session-{marker_id}.pending"
                payload = _watermark_bytes(marker_id, 4, "a" * 64)

                if artifact == "malformed":
                    _private_payload(pending_path, b"{not-json}\n")
                elif artifact == "poisoned":
                    _private_payload(
                        root / f"session-{session_id}.poisoned", payload
                    )
                elif artifact == "mode":
                    _private_payload(pending_path, payload)
                    pending_path.chmod(0o644)
                elif artifact == "directory":
                    pending_path.mkdir(mode=0o700)
                elif artifact == "symlink":
                    target = base / "pending-target"
                    _private_payload(target, payload)
                    pending_path.symlink_to(target)
                elif artifact == "hardlink":
                    source = base / "pending-source"
                    _private_payload(source, payload)
                    os.link(source, pending_path)
                elif artifact == "unknown":
                    _private_payload(
                        root / f"session-{session_id}.pending.bak", payload
                    )
                elif artifact == "multiple":
                    _private_payload(pending_path, payload)
                    _private_payload(
                        root / f"session-{session_id}.poisoned", payload
                    )
                else:
                    _private_payload(pending_path, payload)

                with self.assertRaisesRegex(
                    ShadowEvidenceError,
                    "shadow_evidence_(prior_corrupt|unclean_session|state_unsafe)",
                ):
                    ShadowEvidenceStore(root)

    def test_commit_watermark_advances_with_each_durable_row_and_reopens(self) -> None:
        """Catches retaining an old commit watermark after a later row fsyncs."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as store:
                ledger = store.ledger_path
                commit_path = root / f"session-{store.session_id}.commit"

                def assert_watermark() -> None:
                    rows = _read_rows(ledger)
                    self.assertEqual(
                        commit_path.read_bytes(),
                        _watermark_bytes(
                            store.session_id,
                            len(rows),
                            rows[-1]["row_sha256"],
                        ),
                    )

                store.append_price_only_session(_price_only_session())
                self.assertTrue(commit_path.is_file())
                assert_watermark()
                receipt = store.persist_kalshi_frame(_frame())
                assert_watermark()
                store.append_price_only_observation(
                    _price_only_observation(receipt)
                )
                assert_watermark()
                store.append_price_only_terminal(
                    **_price_only_terminal(kalshi_frames=1)
                )
                assert_watermark()
                self.assertFalse(
                    (root / f"session-{store.session_id}.pending").exists()
                )
            with ShadowEvidenceStore(root) as reopened:
                _terminal(reopened, sportradar_captures=0, kalshi_frames=0)

    def test_commit_marker_audit_accepts_tail_and_rejects_invalid_artifacts(self) -> None:
        """Catches accepting a commit watermark that cannot prove a final tail."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            ledger, _ = _complete_resolved_session(root)
            rows = _read_rows(ledger)
            session_id = ledger.stem.removeprefix("session-")
            _private_payload(
                root / f"session-{session_id}.commit",
                _watermark_bytes(session_id, len(rows), rows[-1]["row_sha256"]),
            )
            try:
                with ShadowEvidenceStore(root) as reopened:
                    _terminal(reopened, sportradar_captures=0, kalshi_frames=0)
            except ShadowEvidenceError as error:
                self.fail(f"valid commit watermark was rejected: {error}")

        for artifact in (
            "old_watermark",
            "commit_without_ledger",
            "malformed",
            "mode",
            "symlink",
            "hardlink",
            "unknown",
            "multiple",
        ):
            with self.subTest(artifact=artifact), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                root = base / "shadow"
                session_id = "11111111-1111-4111-8111-111111111111"
                if artifact == "commit_without_ledger":
                    root.mkdir(mode=0o700)
                    (root / "raw").mkdir(mode=0o700)
                    _private_payload(
                        root / f"session-{session_id}.commit",
                        _watermark_bytes(session_id, 1, "a" * 64),
                    )
                else:
                    ledger, _ = _complete_resolved_session(root)
                    rows = _read_rows(ledger)
                    session_id = ledger.stem.removeprefix("session-")
                    commit_path = root / f"session-{session_id}.commit"
                    payload = _watermark_bytes(
                        session_id, len(rows), rows[-1]["row_sha256"]
                    )
                    if artifact == "old_watermark":
                        payload = _watermark_bytes(
                            session_id, len(rows) - 1, rows[-2]["row_sha256"]
                        )
                    if artifact == "malformed":
                        _private_payload(commit_path, b"{not-json}\n")
                    elif artifact == "mode":
                        _private_payload(commit_path, payload)
                        commit_path.chmod(0o644)
                    elif artifact == "symlink":
                        target = base / "commit-target"
                        _private_payload(target, payload)
                        commit_path.unlink()
                        commit_path.symlink_to(target)
                    elif artifact == "hardlink":
                        source = base / "commit-source"
                        _private_payload(source, payload)
                        commit_path.unlink()
                        os.link(source, commit_path)
                    elif artifact == "unknown":
                        _private_payload(
                            root / f"session-{session_id}.commit.bak", payload
                        )
                    elif artifact == "multiple":
                        _private_payload(commit_path, payload)
                        _private_payload(
                            root / f"session-{session_id}.commit.bak", payload
                        )
                    else:
                        _private_payload(commit_path, payload)

                with self.assertRaisesRegex(
                    ShadowEvidenceError,
                    "shadow_evidence_(prior_corrupt|unclean_session|state_unsafe)",
                ):
                    ShadowEvidenceStore(root)

    def test_raw_close_failure_is_sanitized_and_poisoned(self) -> None:
        """Catches leaking a raw descriptor-close error after frame persistence."""

        import inci_tennis_io.shadow_evidence as evidence_module
        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as store:
                store.append_price_only_session(_price_only_session())
                original_open = evidence_module._open_private_file
                original_close = evidence_module.os.close
                raw_descriptors: set[int] = set()

                def track_open(path: Path, **kwargs: object) -> int:
                    descriptor = original_open(path, **kwargs)
                    if path.parent == store.raw_root:
                        raw_descriptors.add(descriptor)
                    return descriptor

                def fail_raw_close(descriptor: int) -> None:
                    if descriptor in raw_descriptors:
                        raise OSError("injected raw close failure")
                    original_close(descriptor)

                with (
                    patch.object(evidence_module, "_open_private_file", track_open),
                    patch.object(evidence_module.os, "close", fail_raw_close),
                ):
                    try:
                        store.persist_kalshi_frame(_frame())
                    except ShadowEvidenceError as error:
                        self.assertEqual(
                            str(error), "shadow_evidence_raw_write_failed"
                        )
                    except OSError as error:
                        self.fail(f"raw close leaked an OSError: {error}")
                    else:
                        self.fail("raw close failure did not reject persistence")
                self.assertTrue(store._poisoned)
                self.assertEqual(store._raw_number, 0)
                self.assertEqual(store._kalshi_receipts, {})
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_closed"
                ):
                    store.persist_kalshi_frame(_frame())
            with self.assertRaisesRegex(
                ShadowEvidenceError,
                "shadow_evidence_(prior_corrupt|unclean_session|state_unsafe)",
            ):
                ShadowEvidenceStore(root)

    def test_raw_close_failure_preserves_a_primary_write_failure(self) -> None:
        """Catches a close error masking the raw write failure that came first."""

        import inci_tennis_io.shadow_evidence as evidence_module
        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            with ShadowEvidenceStore(root) as store:
                store.append_price_only_session(_price_only_session())
                original_open = evidence_module._open_private_file
                original_close = evidence_module.os.close
                raw_descriptors: set[int] = set()

                def track_open(path: Path, **kwargs: object) -> int:
                    descriptor = original_open(path, **kwargs)
                    if path.parent == store.raw_root:
                        raw_descriptors.add(descriptor)
                    return descriptor

                def fail_raw_close(descriptor: int) -> None:
                    if descriptor in raw_descriptors:
                        raise OSError("injected raw close failure")
                    original_close(descriptor)

                original_write = evidence_module._write_all

                def fail_raw_write(
                    descriptor: int, payload: bytes, code: str
                ) -> None:
                    if descriptor in raw_descriptors:
                        raise ShadowEvidenceError(
                            "shadow_evidence_raw_write_failed"
                        )
                    original_write(descriptor, payload, code)

                with (
                    patch.object(evidence_module, "_open_private_file", track_open),
                    patch.object(evidence_module, "_write_all", fail_raw_write),
                    patch.object(evidence_module.os, "close", fail_raw_close),
                ):
                    try:
                        store.persist_kalshi_frame(_frame())
                    except ShadowEvidenceError as error:
                        self.assertEqual(
                            str(error), "shadow_evidence_raw_write_failed"
                        )
                    except OSError as error:
                        self.fail(f"raw close masked the primary failure: {error}")
                    else:
                        self.fail("raw write failure did not reject persistence")
                self.assertTrue(store._poisoned)
                self.assertEqual(store._raw_number, 0)
                self.assertEqual(store._kalshi_receipts, {})


if __name__ == "__main__":
    unittest.main()
