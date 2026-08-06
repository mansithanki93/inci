from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from inci_tennis_expert.live_paper_session import (
    LivePaperHeartbeatInput,
    LivePaperRecordKind,
    encode_live_paper_checkpoint,
    encode_live_paper_records,
    reduce_live_paper_input,
    replay_live_paper_records,
)
from inci_tennis_expert.live_paper_execution import PaperActionKind


HOME_MARKET_ID = "9b0f6b43-5b68-4f9f-9f02-9a2d1b8ac1a1"
AWAY_MARKET_ID = "8a0f6b43-5b68-4f9f-9f02-9a2d1b8ac1a2"
PROOF = sha256(b"independent-api-tennis-lineage").hexdigest()
LINEAGE = sha256(b"api-tennis-lineage").hexdigest()


def _manifest() -> dict[str, object]:
    return {
        "schema": "inci.live-paper-match-manifest",
        "version": 1,
        "canonical_match_id": "canonical-match-1",
        "scheduled_start_wall_ns": 2_000_000_000,
        "match_format": "STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS",
        "home_player_id": "home-1",
        "away_player_id": "away-1",
        "providers": [
            {
                "slot": "api_tennis",
                "source_id": "api-tennis-primary",
                "provider_match_id": "101",
                "home_player_id": "201",
                "away_player_id": "202",
                "independent_lineage_id": "api-tennis-lineage",
                "source_lineage_sha256": LINEAGE,
                "independence_proven": True,
                "independence_proof_sha256": PROOF,
            }
        ],
        "markets": {
            "home": {
                "ticker": "KXTENNIS-HOME",
                "market_id": HOME_MARKET_ID,
                "yes_player_side": "HOME",
            },
            "away": {
                "ticker": "KXTENNIS-AWAY",
                "market_id": AWAY_MARKET_ID,
                "yes_player_side": "AWAY",
            },
        },
        "fee_schedule": {
            "schedule_id": "paper-fees-v1",
            "series_tickers": ["KXTENNIS"],
            "taker_rate": "0",
            "maker_rate": "0",
            "taker_multiplier": "1",
            "maker_multiplier": "1",
            "trade_fee_precision": "0.0001",
            "balance_precision": "0.0001",
            "effective_from_wall_ns": 1,
            "effective_until_wall_ns": None,
        },
        "fee_series_ticker": "KXTENNIS",
    }


def _score_payload(*, points: str = "0 - 0", sets: list[dict[str, object]] | None = None) -> bytes:
    return json.dumps(
        {
            "success": 1,
            "result": [
                {
                    "event_key": "101",
                    "first_player_key": "201",
                    "second_player_key": "202",
                    "event_type_type": "Singles",
                    "event_status": "Live",
                    "event_winner": None,
                    "event_game_result": points,
                    "event_serve": "home",
                    "scores": sets
                    if sets is not None
                    else [
                        {"score_set": "1", "score_first": "0", "score_second": "0"}
                    ],
                    "pointbypoint": [],
                }
            ],
        },
        separators=(",", ":"),
    ).encode("ascii")


def _score_envelope(raw: bytes, *, ordinal: int = 1, wall: int = 1_000_000_000, mono: int = 1_000_000_000) -> dict[str, object]:
    return {
        "kind": "score_capture",
        "provider_slot": "api_tennis",
        "provider_source_id": "api-tennis-primary",
        "provider_match_id": "101",
        "home_player_id": "201",
        "away_player_id": "202",
        "independent_lineage_id": "api-tennis-lineage",
        "source_lineage_sha256": LINEAGE,
        "independence_proven": True,
        "independence_proof_sha256": PROOF,
        "raw_capture_id": f"score-{ordinal}",
        "captured_wall_ns": wall,
        "captured_monotonic_ns": mono,
        "clock_uncertainty_ns": 0,
        "payload_base64": base64.b64encode(raw).decode("ascii"),
    }


def _kalshi_envelope(raw: bytes, *, wall: int, mono: int, generation: int = 1) -> dict[str, object]:
    return {
        "kind": "kalshi_frame",
        "physical_connection_generation": generation,
        "captured_wall_ns": wall,
        "captured_monotonic_ns": mono,
        "clock_uncertainty_ns": 0,
        "payload_base64": base64.b64encode(raw).decode("ascii"),
    }


def _wire(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


class LiveTwoModelPaperCliTests(unittest.TestCase):
    def test_trained_artifact_decoder_is_a_public_validating_seam(self) -> None:
        from inci_tennis_expert.live_two_model import build_operator_bootstrap_artifacts
        from inci_tennis_expert.pilot_contracts import (
            ServeStrengthArtifact,
            canonical_pilot_contract_bytes,
        )
        from inci_tennis_runtime.two_model_pilot_cli import (
            PilotCliError,
            decode_pilot_contract,
        )

        static, _ = build_operator_bootstrap_artifacts(
            canonical_match_id="canonical-match-1",
            scheduled_start_wall_ns=2_000_000_000,
            cutoff_wall_ns=1_999_999_999,
            home_serve_point_probability=Decimal("0.64"),
            away_serve_point_probability=Decimal("0.61"),
        )
        self.assertEqual(
            decode_pilot_contract(
                canonical_pilot_contract_bytes(static),
                ServeStrengthArtifact,
            ),
            static,
        )
        with self.assertRaises(PilotCliError):
            decode_pilot_contract(b"{}", ServeStrengthArtifact)

    def test_parser_requires_growing_files_xor_live_and_artifacts_xor_both_priors(self) -> None:
        from inci_tennis_runtime.live_two_model_paper_cli import LivePaperCliError, parse_cli_arguments

        base = [
            "--manifest", "/tmp/manifest.json", "--session-log", "/tmp/session.jsonl",
            "--checkpoint", "/tmp/checkpoint.json",
        ]
        with self.assertRaises(LivePaperCliError):
            parse_cli_arguments(base + ["--bootstrap-home-serve", "0.64"])
        with self.assertRaises(LivePaperCliError):
            parse_cli_arguments(base + ["--bootstrap-home-serve", "0.64", "--bootstrap-away-serve", "0.61"])
        with self.assertRaises(LivePaperCliError):
            parse_cli_arguments(base + ["--live-readonly", "--score-stream", "/tmp/scores", "--kalshi-stream", "/tmp/books", "--bootstrap-home-serve", "0.64", "--bootstrap-away-serve", "0.61"])
        parsed = parse_cli_arguments(base + ["--score-stream", "/tmp/scores", "--kalshi-stream", "/tmp/books", "--static-artifact", "/tmp/static", "--dynamic-artifact", "/tmp/dynamic"])
        self.assertFalse(parsed.live_readonly)
        for forbidden in ("--score-freshness", "--latency-seconds", "--maximum-debit", "--minimum-edge", "--maximum-hold"):
            with self.assertRaises(LivePaperCliError):
                parse_cli_arguments(base + ["--score-stream", "/tmp/scores", "--kalshi-stream", "/tmp/books", "--bootstrap-home-serve", "0.64", "--bootstrap-away-serve", "0.61", forbidden, "1"])

    def test_startup_rejects_relative_symlink_nonregular_and_colliding_paths(self) -> None:
        from inci_tennis_runtime.live_two_model_paper_cli import LivePaperCliError, validate_cli_paths

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            score = root / "score.jsonl"
            book = root / "book.jsonl"
            for path in (manifest, score, book):
                path.write_bytes(b"{}\n")
            link = root / "manifest-link"
            link.symlink_to(manifest)
            with self.assertRaises(LivePaperCliError):
                validate_cli_paths(manifest=Path("relative"), score_stream=score, kalshi_stream=book, static_artifact=None, dynamic_artifact=None, session_log=root / "out", checkpoint=root / "checkpoint")
            with self.assertRaises(LivePaperCliError):
                validate_cli_paths(manifest=link, score_stream=score, kalshi_stream=book, static_artifact=None, dynamic_artifact=None, session_log=root / "out", checkpoint=root / "checkpoint")
            with self.assertRaises(LivePaperCliError):
                validate_cli_paths(manifest=manifest, score_stream=score, kalshi_stream=book, static_artifact=None, dynamic_artifact=None, session_log=score, checkpoint=root / "checkpoint")
            with self.assertRaises(LivePaperCliError):
                validate_cli_paths(manifest=manifest, score_stream=score, kalshi_stream=book, static_artifact=None, dynamic_artifact=None, session_log=root / "missing" / "out", checkpoint=root / "checkpoint")

    def test_manifest_is_exact_and_proof_digest_must_match_before_consensus(self) -> None:
        from inci_tennis_runtime.live_paper_capture_bridge import LivePaperBridgeError, load_live_paper_manifest

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(_manifest(), sort_keys=True, separators=(",", ":")), encoding="ascii")
            manifest = load_live_paper_manifest(path)
            self.assertEqual(manifest.binding.home_market_id, HOME_MARKET_ID)
            changed = _manifest()
            changed["extra"] = "caller-controlled"
            path.write_text(json.dumps(changed), encoding="ascii")
            with self.assertRaises(LivePaperBridgeError):
                load_live_paper_manifest(path)
            changed = _manifest()
            changed["markets"]["home"]["market_id"] = HOME_MARKET_ID.upper()  # type: ignore[index]
            path.write_text(json.dumps(changed), encoding="ascii")
            with self.assertRaises(LivePaperBridgeError):
                load_live_paper_manifest(path)

    def test_growing_jsonl_rejects_duplicate_keys_and_json_numbers(self) -> None:
        from inci_tennis_runtime.live_two_model_paper_cli import LivePaperCliError, _jsonl

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.jsonl"
            path.write_bytes(
                b'{"captured_wall_ns":1,"captured_wall_ns":2,"captured_monotonic_ns":1}\n'
            )
            with self.assertRaises(LivePaperCliError):
                _jsonl(path, "score")
            path.write_bytes(
                b'{"captured_wall_ns":1.0,"captured_monotonic_ns":1}\n'
            )
            with self.assertRaises(LivePaperCliError):
                _jsonl(path, "score")

    def test_bridge_parses_raw_score_and_raw_ws_payloads_without_normalized_input(self) -> None:
        from inci_tennis_runtime.live_paper_capture_bridge import GrowingJsonlCaptureBridge, LivePaperBridgeError, LivePaperCaptureObserver, manifest_from_document

        manifest = manifest_from_document(_manifest())
        bridge = GrowingJsonlCaptureBridge.bootstrap(
            manifest,
            home_serve_probability=Decimal("0.80"),
            away_serve_probability=Decimal("0.20"),
            opened_wall_ns=900_000_000,
            opened_monotonic_ns=900_000_000,
        )
        anchor_records = bridge.accept_score_envelope(_score_envelope(_score_payload()))
        self.assertIn(LivePaperRecordKind.ANCHOR, tuple(row.kind for row in anchor_records))
        self.assertEqual(
            bridge.state.score_coordinator.anchor.supporting_independent_lineage_ids,
            ("api-tennis-lineage",),
        )
        smuggled = _score_envelope(_score_payload(), ordinal=2)
        smuggled["normalized_probability"] = "0.99"
        with self.assertRaises(LivePaperBridgeError):
            bridge.accept_score_envelope(smuggled)
        wrong_proof = _score_envelope(_score_payload(), ordinal=2)
        wrong_proof["independence_proof_sha256"] = "f" * 64
        with self.assertRaises(LivePaperBridgeError):
            bridge.accept_score_envelope(wrong_proof)

        ack = _wire({"id": 1, "type": "subscribed", "msg": {"channel": "orderbook_delta", "sid": 2}})
        home = _wire({"type": "orderbook_snapshot", "sid": 2, "seq": 1, "msg": {"market_ticker": "KXTENNIS-HOME", "market_id": HOME_MARKET_ID, "yes_dollars_fp": [["0.10", "100.00"]], "no_dollars_fp": [["0.20", "100.00"]]}})
        away = _wire({"type": "orderbook_snapshot", "sid": 2, "seq": 2, "msg": {"market_ticker": "KXTENNIS-AWAY", "market_id": AWAY_MARKET_ID, "yes_dollars_fp": [["0.10", "100.00"]], "no_dollars_fp": [["0.20", "100.00"]]}})
        bridge.accept_kalshi_envelope(_kalshi_envelope(ack, wall=1_100_000_000, mono=1_100_000_000))
        bridge.accept_kalshi_envelope(_kalshi_envelope(home, wall=1_200_000_000, mono=1_200_000_000))
        rows = bridge.accept_kalshi_envelope(_kalshi_envelope(away, wall=1_300_000_000, mono=1_300_000_000))
        self.assertIn(LivePaperRecordKind.RAW_L2_RECEIPT, tuple(row.kind for row in rows))
        ack_2 = _wire({"id": 2, "type": "subscribed", "msg": {"channel": "orderbook_delta", "sid": 3}})
        home_2 = _wire({"type": "orderbook_snapshot", "sid": 3, "seq": 1, "msg": {"market_ticker": "KXTENNIS-HOME", "market_id": HOME_MARKET_ID, "yes_dollars_fp": [["0.10", "100.00"]], "no_dollars_fp": [["0.20", "100.00"]]}})
        away_2 = _wire({"type": "orderbook_snapshot", "sid": 3, "seq": 2, "msg": {"market_ticker": "KXTENNIS-AWAY", "market_id": AWAY_MARKET_ID, "yes_dollars_fp": [["0.10", "100.00"]], "no_dollars_fp": [["0.20", "100.00"]]}})
        bridge.accept_kalshi_envelope(_kalshi_envelope(ack_2, wall=1_400_000_000, mono=1_400_000_000, generation=2))
        bridge.accept_kalshi_envelope(_kalshi_envelope(home_2, wall=1_500_000_000, mono=1_500_000_000, generation=2))
        reconnected = bridge.accept_kalshi_envelope(_kalshi_envelope(away_2, wall=1_600_000_000, mono=1_600_000_000, generation=2))
        self.assertIn(LivePaperRecordKind.RAW_L2_RECEIPT, tuple(row.kind for row in reconnected))
        observer = LivePaperCaptureObserver(bridge)

        async def observe(payload: bytes, wall: int) -> None:
            await observer.after_kalshi_commit(
                frame=SimpleNamespace(
                    payload=payload,
                    physical_connection_generation=1,
                ),
                durable_receipt=object(),
                captured_wall_ns=wall,
                captured_monotonic_ns=wall,
                clock_uncertainty_ns=0,
            )

        ack_3 = _wire({"id": 3, "type": "subscribed", "msg": {"channel": "orderbook_delta", "sid": 4}})
        home_3 = _wire({"type": "orderbook_snapshot", "sid": 4, "seq": 1, "msg": {"market_ticker": "KXTENNIS-HOME", "market_id": HOME_MARKET_ID, "yes_dollars_fp": [["0.10", "100.00"]], "no_dollars_fp": [["0.20", "100.00"]]}})
        away_3 = _wire({"type": "orderbook_snapshot", "sid": 4, "seq": 2, "msg": {"market_ticker": "KXTENNIS-AWAY", "market_id": AWAY_MARKET_ID, "yes_dollars_fp": [["0.10", "100.00"]], "no_dollars_fp": [["0.20", "100.00"]]}})
        asyncio.run(observe(ack_3, 1_700_000_000))
        asyncio.run(observe(home_3, 1_800_000_000))
        asyncio.run(observe(away_3, 1_900_000_000))
        latest_l2 = next(
            row for row in reversed(bridge.records)
            if row.kind is LivePaperRecordKind.RAW_L2_RECEIPT
        )
        self.assertEqual(
            latest_l2.payload.body.frame.physical_connection_generation,
            3,
        )

    def test_sportradar_terminal_rejects_non_natural_and_illegal_sets(self) -> None:
        import inci_tennis_adapters.sportradar_trial_v3 as sportradar_trial_v3
        from inci_tennis_runtime.live_paper_capture_bridge import (
            GrowingJsonlCaptureBridge,
            LivePaperBridgeError,
            manifest_from_document,
        )

        document = _manifest()
        document["providers"] = [{
            "slot": "sportradar",
            "source_id": "sportradar-primary",
            "provider_match_id": "sr:sport_event:101",
            "home_player_id": "sr:competitor:201",
            "away_player_id": "sr:competitor:202",
            "independent_lineage_id": "sportradar-lineage",
            "source_lineage_sha256": LINEAGE,
            "independence_proven": None,
            "independence_proof_sha256": None,
        }]
        bridge = GrowingJsonlCaptureBridge.bootstrap(
            manifest_from_document(document),
            home_serve_probability=Decimal("0.64"),
            away_serve_probability=Decimal("0.61"),
            opened_wall_ns=1_000_000_000,
            opened_monotonic_ns=1_000_000_000,
        )

        def score(match_status: str) -> SimpleNamespace:
            return SimpleNamespace(
                home_id="sr:competitor:201",
                away_id="sr:competitor:202",
                start_wall_ns=2_000_000_000,
                best_of=3,
                status="ended",
                match_status=match_status,
                sets_home=2,
                sets_away=0,
                games_home=6,
                games_away=3,
                points_home="--",
                points_away="--",
                serving=None,
                in_tiebreak=None,
                generated_wall_ns=3_000_000_000,
            )

        def raw_sets(first: tuple[int, int], second: tuple[int, int]) -> bytes:
            return json.dumps({
                "sport_event_status": {
                    "period_scores": [
                        {"home_score": first[0], "away_score": first[1]},
                        {"home_score": second[0], "away_score": second[1]},
                    ]
                }
            }, separators=(",", ":")).encode("ascii")

        with mock.patch.object(
            sportradar_trial_v3,
            "parse_sport_event_timeline",
            side_effect=sportradar_trial_v3.SportradarWireContractError(),
        ), mock.patch.object(
            sportradar_trial_v3,
            "parse_sport_event_summary",
            return_value=score("retired"),
        ):
            with self.assertRaises(LivePaperBridgeError):
                bridge.accept_sportradar_capture(
                    raw_sets((6, 4), (6, 3)),
                    captured_wall_ns=3_000_000_000,
                    captured_monotonic_ns=3_000_000_000,
                    clock_uncertainty_ns=0,
                )
        with mock.patch.object(
            sportradar_trial_v3,
            "parse_sport_event_timeline",
            side_effect=sportradar_trial_v3.SportradarWireContractError(),
        ), mock.patch.object(
            sportradar_trial_v3,
            "parse_sport_event_summary",
            return_value=score("ended"),
        ):
            with self.assertRaises(LivePaperBridgeError):
                bridge.accept_sportradar_capture(
                    raw_sets((4, 3), (4, 2)),
                    captured_wall_ns=3_000_000_000,
                    captured_monotonic_ns=3_000_000_000,
                    clock_uncertainty_ns=0,
                )
            rows = bridge.accept_sportradar_capture(
                raw_sets((6, 4), (6, 3)),
                captured_wall_ns=3_000_000_001,
                captured_monotonic_ns=3_000_000_001,
                clock_uncertainty_ns=0,
            )
        self.assertIn(LivePaperRecordKind.ANCHOR, tuple(row.kind for row in rows))
        self.assertEqual(
            bridge.state.score_coordinator.anchor.state.status.value,
            "ended",
        )

    def test_no_network_fixture_run_banner_terminal_and_replay_are_exact(self) -> None:
        from inci_tennis_runtime.live_two_model_paper_cli import run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            scores = root / "scores.jsonl"
            books = root / "books.jsonl"
            session = root / "session.jsonl"
            checkpoint = root / "checkpoint.json"
            manifest.write_text(json.dumps(_manifest(), sort_keys=True, separators=(",", ":")), encoding="ascii")
            score_rows = [
                _score_envelope(_score_payload(), wall=1_000_000_000, mono=1_000_000_000),
                _score_envelope(_score_payload(points="15 - 0"), ordinal=2, wall=2_000_000_000, mono=2_000_000_000),
                _score_envelope(_score_payload(sets=[{"score_set": "1", "score_first": "6", "score_second": "4"}, {"score_set": "2", "score_first": "0", "score_second": "0"}]), ordinal=3, wall=3_000_000_000, mono=3_000_000_000),
                _score_envelope(_score_payload(sets=[{"score_set": "1", "score_first": "6", "score_second": "4"}, {"score_set": "2", "score_first": "0", "score_second": "0"}]), ordinal=4, wall=3_300_000_000, mono=3_300_000_000),
                _score_envelope(_score_payload(sets=[{"score_set": "1", "score_first": "6", "score_second": "4"}, {"score_set": "2", "score_first": "0", "score_second": "0"}]), ordinal=5, wall=3_600_000_000, mono=3_600_000_000),
                _score_envelope(_score_payload(points="15 - 0", sets=[{"score_set": "1", "score_first": "6", "score_second": "4"}, {"score_set": "2", "score_first": "0", "score_second": "0"}]), ordinal=6, wall=3_700_000_000, mono=3_700_000_000),
            ]
            scores.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in score_rows), encoding="ascii")
            ack = _wire({"id": 1, "type": "subscribed", "msg": {"channel": "orderbook_delta", "sid": 2}})
            home = _wire({"type": "orderbook_snapshot", "sid": 2, "seq": 1, "msg": {"market_ticker": "KXTENNIS-HOME", "market_id": HOME_MARKET_ID, "yes_dollars_fp": [["0.10", "100.00"]], "no_dollars_fp": [["0.20", "100.00"]]}})
            away = _wire({"type": "orderbook_snapshot", "sid": 2, "seq": 2, "msg": {"market_ticker": "KXTENNIS-AWAY", "market_id": AWAY_MARKET_ID, "yes_dollars_fp": [["0.10", "100.00"]], "no_dollars_fp": [["0.20", "100.00"]]}})
            book_rows = [
                _kalshi_envelope(ack, wall=2_100_000_000, mono=2_100_000_000),
                _kalshi_envelope(home, wall=2_200_000_000, mono=2_200_000_000),
                _kalshi_envelope(away, wall=2_300_000_000, mono=2_300_000_000),
                _kalshi_envelope(_wire({"type": "orderbook_delta", "sid": 2, "seq": 3, "msg": {"market_ticker": "KXTENNIS-HOME", "market_id": HOME_MARKET_ID, "price_dollars": "0.10", "delta_fp": "1.00", "side": "yes"}}), wall=3_800_000_000, mono=3_800_000_000),
                _kalshi_envelope(_wire({"type": "orderbook_delta", "sid": 2, "seq": 4, "msg": {"market_ticker": "KXTENNIS-HOME", "market_id": HOME_MARKET_ID, "price_dollars": "0.20", "delta_fp": "-90.00", "side": "no"}}), wall=4_800_000_000, mono=4_800_000_000),
                _kalshi_envelope(_wire({"type": "orderbook_delta", "sid": 2, "seq": 5, "msg": {"market_ticker": "KXTENNIS-HOME", "market_id": HOME_MARKET_ID, "price_dollars": "0.20", "delta_fp": "90.00", "side": "no"}}), wall=5_800_000_000, mono=5_800_000_000),
                _kalshi_envelope(_wire({"type": "orderbook_delta", "sid": 2, "seq": 6, "msg": {"market_ticker": "KXTENNIS-HOME", "market_id": HOME_MARKET_ID, "price_dollars": "0.10", "delta_fp": "1.00", "side": "yes"}}), wall=6_900_000_000, mono=6_900_000_000),
                _kalshi_envelope(_wire({"type": "orderbook_delta", "sid": 2, "seq": 7, "msg": {"market_ticker": "KXTENNIS-HOME", "market_id": HOME_MARKET_ID, "price_dollars": "0.10", "delta_fp": "1.00", "side": "yes"}}), wall=7_900_000_000, mono=7_900_000_000),
            ]
            books.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in book_rows), encoding="ascii")
            output = io.StringIO()
            status = run([
                "--manifest", str(manifest), "--score-stream", str(scores),
                "--kalshi-stream", str(books), "--session-log", str(session),
                "--checkpoint", str(checkpoint), "--bootstrap-home-serve", "0.80",
                "--bootstrap-away-serve", "0.20", "--stop-at-eof",
            ], stdout=output)
            self.assertEqual(status, 0)
            self.assertEqual(output.getvalue().splitlines()[0], "LIVE MODELS 1+2 / PAPER ONLY / NO REAL ORDERS")
            self.assertRegex(output.getvalue(), r"pnl=-?[0-9]")
            first = session.read_bytes()
            replay = replay_live_paper_records(first, require_terminal=True)
            self.assertTrue(replay.state.terminal)
            kinds = tuple(row.kind for row in replay.records)
            self.assertIn(LivePaperRecordKind.FORECAST, kinds)
            self.assertIn(LivePaperRecordKind.TRANSITION, kinds)
            self.assertTrue(any(getattr(row.payload.body, "reason", None) == "before_completed_set" for row in replay.records if row.kind is LivePaperRecordKind.REJECTION))
            actions = tuple(row.payload.body for row in replay.records if row.kind is LivePaperRecordKind.ACTION)
            fills = tuple(row.payload.body.fill for row in replay.records if row.kind is LivePaperRecordKind.FILL)
            self.assertEqual(tuple(action.kind for action in actions), (PaperActionKind.BUY, PaperActionKind.SELL))
            self.assertEqual(tuple(fill.action_kind for fill in fills), (PaperActionKind.BUY, PaperActionKind.BUY, PaperActionKind.SELL))
            self.assertLess(fills[0].quantity, actions[0].quantity)
            self.assertEqual(sum((fill.quantity for fill in fills[:2]), Decimal("0")), actions[0].quantity)
            self.assertEqual(run([
                "--manifest", str(manifest), "--score-stream", str(scores),
                "--kalshi-stream", str(books), "--session-log", str(session),
                "--checkpoint", str(checkpoint), "--bootstrap-home-serve", "0.80",
                "--bootstrap-away-serve", "0.20", "--stop-at-eof", "--replay-only",
            ], stdout=io.StringIO()), 0)
            self.assertEqual(session.read_bytes(), first)

            checkpoint.write_bytes(b"corrupt-checkpoint")
            self.assertEqual(run([
                "--manifest", str(manifest), "--score-stream", str(scores),
                "--kalshi-stream", str(books), "--session-log", str(session),
                "--checkpoint", str(checkpoint), "--bootstrap-home-serve", "0.80",
                "--bootstrap-away-serve", "0.20", "--stop-at-eof", "--replay-only",
            ], stdout=io.StringIO()), 0)
            self.assertEqual(session.read_bytes(), first)

    def test_resume_rebuilds_raw_adapter_state_without_refeeding_committed_input(self) -> None:
        from inci_tennis_runtime.live_paper_capture_bridge import (
            GrowingJsonlCaptureBridge,
            manifest_from_document,
        )
        from inci_tennis_runtime.live_two_model_paper_cli import run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            scores = root / "scores.jsonl"
            books = root / "books.jsonl"
            session = root / "session.jsonl"
            checkpoint = root / "checkpoint.json"
            document = _manifest()
            manifest_path.write_text(
                json.dumps(document, sort_keys=True, separators=(",", ":")),
                encoding="ascii",
            )
            score = _score_envelope(_score_payload())
            scores.write_text(
                json.dumps(score, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="ascii",
            )
            books.write_bytes(b"")
            bridge = GrowingJsonlCaptureBridge.bootstrap(
                manifest_from_document(document),
                home_serve_probability=Decimal("0.80"),
                away_serve_probability=Decimal("0.20"),
                opened_wall_ns=900_000_000,
                opened_monotonic_ns=900_000_000,
            )
            bridge.accept_score_envelope(score)
            committed = encode_live_paper_records(tuple(bridge.records))
            committed_count = len(bridge.records)
            session.write_bytes(committed)
            checkpoint.write_bytes(encode_live_paper_checkpoint(bridge.state))

            self.assertEqual(run([
                "--manifest", str(manifest_path), "--score-stream", str(scores),
                "--kalshi-stream", str(books), "--session-log", str(session),
                "--checkpoint", str(checkpoint), "--bootstrap-home-serve", "0.80",
                "--bootstrap-away-serve", "0.20", "--stop-at-eof",
            ], stdout=io.StringIO()), 0)
            replay = replay_live_paper_records(session.read_bytes(), require_terminal=True)
            self.assertEqual(len(replay.records), committed_count + 1)
            self.assertEqual(session.read_bytes()[:len(committed)], committed)

    def test_live_observer_batch_is_not_committed_twice_on_collector_return(self) -> None:
        from inci_tennis_runtime.live_two_model_paper_cli import run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            session = root / "session.jsonl"
            checkpoint = root / "checkpoint.json"
            document = _manifest()
            provider = document["providers"][0]  # type: ignore[index]
            provider["slot"] = "sportradar"
            provider["source_id"] = "sportradar-primary"
            manifest.write_text(
                json.dumps(document, sort_keys=True, separators=(",", ":")),
                encoding="ascii",
            )

            def fake_collector(argv: list[str], *, stdout: object, stderr: object, dependencies: object) -> int:
                del argv, stdout, stderr
                observer = dependencies.capture_observer
                bridge = observer.bridge
                state, records = reduce_live_paper_input(
                    bridge.state,
                    LivePaperHeartbeatInput(
                        bridge.state.next_heartbeat_wall_ns,
                        bridge.state.next_heartbeat_monotonic_ns,
                    ),
                )
                bridge.state = state
                bridge.records.extend(records)
                observer._sink(records)
                return 0

            import inci_tennis_runtime.live_shadow_cli as live_shadow_cli

            with mock.patch.object(live_shadow_cli, "run_cli", side_effect=fake_collector):
                self.assertEqual(run([
                    "--live-readonly", "--manifest", str(manifest),
                    "--session-log", str(session), "--checkpoint", str(checkpoint),
                    "--bootstrap-home-serve", "0.80", "--bootstrap-away-serve", "0.20",
                    "--duration-seconds", "1",
                ], stdout=io.StringIO()), 0)
            replay = replay_live_paper_records(session.read_bytes(), require_terminal=True)
            self.assertEqual(
                tuple(row.kind for row in replay.records),
                (LivePaperRecordKind.HEARTBEAT, LivePaperRecordKind.TERMINAL),
            )


if __name__ == "__main__":
    unittest.main()
