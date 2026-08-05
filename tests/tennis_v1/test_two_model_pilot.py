from __future__ import annotations

import ast
from dataclasses import fields, replace
from decimal import Decimal
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from inci_tennis_expert.contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ProviderPoint,
    ScoreValue,
    TennisState,
    TerminationKind,
)
from inci_tennis_expert.pilot_contracts import (
    PilotPointEvent,
    ServeStrengthArtifact,
    canonical_pilot_contract_bytes,
    compute_serve_strength_artifact_sha256,
    compute_training_match_ids_sha256,
    pilot_contract_sha256,
)
from inci_tennis_expert.pilot_dynamic_model import (
    DynamicParameterCandidate,
    DynamicPointArtifact,
    compute_dynamic_point_artifact_sha256,
)
from inci_tennis_expert.pilot_static_model import evaluate_static_outcome
from inci_tennis_expert.tennis_score import apply_point
from inci_tennis_expert.two_model_pilot import (
    TwoModelAbstentionReason,
    TwoModelPilotError,
    TwoModelRowStatus,
    encode_two_model_rows,
    initialize_two_model_pilot,
    run_two_model_event,
)
from inci_tennis_runtime.two_model_pilot_cli import PilotCliError, _write_exclusive


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)


def _initial_state(
    *,
    correction_epoch: int = 0,
    last_event_id: str = "event-0",
) -> TennisState:
    return TennisState(
        provider_source_id="primary",
        revision_domain_id="primary-revisions",
        source_lineage_sha256=SHA_A,
        provider_match_id="provider-match-1",
        home_player_id="home-player",
        away_player_id="away-player",
        scheduled_start_wall_ns=9_000,
        match_format=MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        status=MatchStatus.LIVE,
        termination_kind=TerminationKind.NONE,
        winner=None,
        retired_side=None,
        completed_sets=(),
        games_home=0,
        games_away=0,
        points_home=ScoreValue.LOVE,
        points_away=ScoreValue.LOVE,
        in_tiebreak=False,
        tiebreak_points_home=0,
        tiebreak_points_away=0,
        tiebreak_first_server=None,
        server_for_next_point=PlayerSide.HOME,
        correction_epoch=correction_epoch,
        revision=1,
        snapshot_complete=True,
        last_provider_event_id=last_event_id,
        last_event_semantic_sha256=SHA_B,
        correction_lineage_sha256=SHA_C,
        last_source_wall_ns=1_000,
        last_source_generated_wall_ns=1_000,
        last_received_monotonic_ns=1,
        last_clock_uncertainty_ns=0,
        block_reason=None,
        expected_revision=None,
        observed_revision=None,
        blocked_event_semantic_sha256=None,
        blocked_received_monotonic_ns=None,
    )


def _event(
    *,
    point_id: str,
    sequence_number: int,
    before: TennisState,
    winner: PlayerSide,
    canonical_match_id: str = "match-1",
) -> PilotPointEvent:
    server = before.server_for_next_point
    assert server in (PlayerSide.HOME, PlayerSide.AWAY)
    after = apply_point(
        before,
        ProviderPoint(
            provider_source_id=before.provider_source_id,
            revision_domain_id=before.revision_domain_id,
            source_lineage_sha256=before.source_lineage_sha256,
            provider_event_id=f"event-{point_id}",
            provider_match_id=before.provider_match_id,
            home_player_id=before.home_player_id,
            away_player_id=before.away_player_id,
            scheduled_start_wall_ns=before.scheduled_start_wall_ns,
            match_format=before.match_format,
            correction_epoch=before.correction_epoch,
            revision=before.revision + 1,
            point_winner=winner,
            server_before_point=server,
            source_wall_ns=1_000 + sequence_number,
            source_generated_wall_ns=1_000 + sequence_number,
            received_monotonic_ns=1 + sequence_number,
            clock_uncertainty_ns=0,
        ),
    ).state
    return PilotPointEvent(
        canonical_match_id=canonical_match_id,
        point_id=point_id,
        sequence_number=sequence_number,
        before_state=before,
        after_state=after,
        server=server,
        winner=winner,
        consensus_epoch=before.correction_epoch,
        consensus_transition_sha256=SHA_D,
        supporting_source_lineage_sha256s=(SHA_A, SHA_B),
        received_wall_ns=1_000 + sequence_number,
        accepted_monotonic_ns=1 + sequence_number,
    )


def _serve_artifact() -> ServeStrengthArtifact:
    values = {
        "version": "pilot-serve-v1",
        "target_canonical_match_id": "match-1",
        "target_scheduled_start_wall_ns": 9_000,
        "cutoff_wall_ns": 8_999,
        "training_match_ids": ("match-static-train",),
        "training_match_ids_sha256": compute_training_match_ids_sha256(
            ("match-static-train",)
        ),
        "source_data_sha256": SHA_A,
        "feature_definition_sha256": SHA_B,
        "code_sha256": SHA_C,
        "home_serve_point_probability": Decimal("0.64"),
        "away_serve_point_probability": Decimal("0.61"),
    }
    return ServeStrengthArtifact(
        artifact_sha256=compute_serve_strength_artifact_sha256(**values),
        **values,
    )


def _dynamic_artifact() -> DynamicPointArtifact:
    selected = DynamicParameterCandidate(
        transition_matrix=(
            (Decimal("0.8"), Decimal("0.15"), Decimal("0.05")),
            (Decimal("0.1"), Decimal("0.8"), Decimal("0.1")),
            (Decimal("0.05"), Decimal("0.15"), Decimal("0.8")),
        ),
        home_initial_weights=(Decimal("0.2"), Decimal("0.6"), Decimal("0.2")),
        away_initial_weights=(Decimal("0.2"), Decimal("0.6"), Decimal("0.2")),
        logit_offsets=(Decimal("-0.5"), Decimal("0"), Decimal("0.5")),
    )
    values = {
        "version": "pilot-dynamic-v1",
        "target_canonical_match_id": "match-1",
        "target_scheduled_start_wall_ns": 9_000,
        "cutoff_wall_ns": 8_999,
        "training_match_ids": ("match-dynamic-train",),
        "validation_match_ids": ("match-dynamic-validation",),
        "source_data_sha256": SHA_A,
        "feature_definition_sha256": SHA_B,
        "code_sha256": SHA_C,
        "selected": selected,
    }
    return DynamicPointArtifact(
        artifact_sha256=compute_dynamic_point_artifact_sha256(**values),
        **values,
    )


def _initialized():
    return initialize_two_model_pilot(_serve_artifact(), _dynamic_artifact())


def _write_inputs(directory: Path) -> tuple[Path, Path, Path, tuple[PilotPointEvent, ...]]:
    first = _event(
        point_id="point-1",
        sequence_number=1,
        before=_initial_state(),
        winner=PlayerSide.HOME,
    )
    second = _event(
        point_id="point-2",
        sequence_number=2,
        before=first.after_state,
        winner=PlayerSide.AWAY,
    )
    replay = directory / "events.jsonl"
    static = directory / "static.json"
    dynamic = directory / "dynamic.json"
    replay.write_bytes(
        b"".join(canonical_pilot_contract_bytes(event) + b"\n" for event in (first, second))
    )
    static.write_bytes(canonical_pilot_contract_bytes(_serve_artifact()))
    dynamic.write_bytes(canonical_pilot_contract_bytes(_dynamic_artifact()))
    return replay, static, dynamic, (first, second)


def _run_cli(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(PYTHON), "-m", "inci_tennis_runtime.two_model_pilot_cli", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class TwoModelPilotTests(unittest.TestCase):
    def test_non_model_dynamic_state_halts_with_fixed_code(self) -> None:
        state = _initialized()
        object.__setattr__(state, "dynamic_model", object())
        event = _event(
            point_id="point-1",
            sequence_number=1,
            before=_initial_state(),
            winner=PlayerSide.HOME,
        )

        with self.assertRaisesRegex(TwoModelPilotError, "^state$"):
            run_two_model_event(state, event)

        self.assertEqual(state.last_valid_sequence_number, 0)

    def test_untrusted_state_digest_halts_before_model_prediction(self) -> None:
        state = _initialized()
        object.__setattr__(state, "state_sha256", "f" * 64)
        event = _event(
            point_id="point-1",
            sequence_number=1,
            before=_initial_state(),
            winner=PlayerSide.HOME,
        )

        with patch.object(
            type(state.dynamic_model),
            "predictive_home_point_probability",
            side_effect=AssertionError("prediction reached"),
        ):
            with self.assertRaisesRegex(TwoModelPilotError, "^state$"):
                run_two_model_event(state, event)

        self.assertEqual(state.last_valid_sequence_number, 0)
        self.assertEqual(state.dynamic_model.observed_point_ids, ())

    def test_nested_belief_or_model_tamper_halts_before_sequence_advance(self) -> None:
        event = _event(
            point_id="point-1",
            sequence_number=1,
            before=_initial_state(),
            winner=PlayerSide.HOME,
        )
        cases = (
            (
                "belief_weights",
                lambda state: object.__setattr__(
                    state.dynamic_model.belief,
                    "home_weights",
                    (Decimal("1"), Decimal("0"), Decimal("0")),
                ),
            ),
            (
                "belief_digest",
                lambda state: object.__setattr__(
                    state.dynamic_model.belief,
                    "belief_sha256",
                    "f" * 64,
                ),
            ),
            (
                "model_observed_ids",
                lambda state: object.__setattr__(
                    state.dynamic_model,
                    "observed_point_ids",
                    ("ghost-point",),
                ),
            ),
        )
        for label, mutate in cases:
            with self.subTest(tamper=label):
                state = _initialized()
                mutate(state)

                with patch.object(
                    type(state.dynamic_model),
                    "predictive_home_point_probability",
                    side_effect=AssertionError("prediction reached"),
                ):
                    with self.assertRaisesRegex(TwoModelPilotError, "^state$"):
                        run_two_model_event(state, event)

                self.assertEqual(state.last_valid_sequence_number, 0)

    def test_static_and_causal_dynamic_outputs_share_exact_point(self) -> None:
        state = _initialized()
        event = _event(
            point_id="point-1",
            sequence_number=1,
            before=_initial_state(),
            winner=PlayerSide.HOME,
        )
        expected_pre_point_probability = (
            state.dynamic_model.predictive_home_point_probability(event)
        )

        next_state, row = run_two_model_event(state, event)

        self.assertIs(row.status, TwoModelRowStatus.MODELS_EVALUATED)
        self.assertEqual(row.model_1_static, evaluate_static_outcome(event, _serve_artifact()))
        self.assertEqual(row.point_id, event.point_id)
        self.assertEqual(row.dynamic_prior_belief, state.dynamic_model.belief)
        self.assertEqual(row.dynamic_post_belief, next_state.dynamic_model.belief)
        self.assertEqual(
            row.dynamic_pre_home_point_probability,
            expected_pre_point_probability,
        )
        self.assertNotEqual(
            row.dynamic_pre_home_point_probability,
            row.model_2_dynamic.home_next_point_probability,
        )
        self.assertEqual(row.claim, "PLUMBING_ONLY")
        self.assertEqual(row.authority, "RESEARCH_ONLY / NO_ORDERS")

    def test_invalid_stream_events_abstain_without_changing_last_valid_state(self) -> None:
        first = _event(
            point_id="point-1",
            sequence_number=1,
            before=_initial_state(),
            winner=PlayerSide.HOME,
        )
        accepted, _ = run_two_model_event(_initialized(), first)
        corrected_before = replace(first.after_state, correction_epoch=1)
        discontinuous_before = replace(
            first.after_state,
            last_provider_event_id="unrelated-event",
        )
        cases = (
            (
                TwoModelAbstentionReason.DUPLICATE_POINT,
                first,
            ),
            (
                TwoModelAbstentionReason.SEQUENCE_GAP,
                _event(
                    point_id="point-3",
                    sequence_number=3,
                    before=first.after_state,
                    winner=PlayerSide.HOME,
                ),
            ),
            (
                TwoModelAbstentionReason.CORRECTION_EPOCH_CHANGED,
                _event(
                    point_id="point-2-correction",
                    sequence_number=2,
                    before=corrected_before,
                    winner=PlayerSide.HOME,
                ),
            ),
            (
                TwoModelAbstentionReason.STATE_DISCONTINUITY,
                _event(
                    point_id="point-2-discontinuous",
                    sequence_number=2,
                    before=discontinuous_before,
                    winner=PlayerSide.HOME,
                ),
            ),
            (
                TwoModelAbstentionReason.MATCH_MISMATCH,
                _event(
                    point_id="point-2-other-match",
                    sequence_number=2,
                    before=first.after_state,
                    winner=PlayerSide.HOME,
                    canonical_match_id="match-other",
                ),
            ),
        )

        for expected_reason, event in cases:
            with self.subTest(reason=expected_reason.value):
                returned, row = run_two_model_event(accepted, event)
                self.assertIs(returned, accepted)
                self.assertIs(row.status, TwoModelRowStatus.ABSTAINED)
                self.assertIs(row.abstention_reason, expected_reason)
                self.assertEqual(row.prior_state_sha256, row.resulting_state_sha256)

    def test_tampered_artifact_abstains_without_changing_state(self) -> None:
        state = _initialized()
        object.__setattr__(
            state.static_artifact,
            "home_serve_point_probability",
            Decimal("0.65"),
        )
        event = _event(
            point_id="point-1",
            sequence_number=1,
            before=_initial_state(),
            winner=PlayerSide.HOME,
        )

        returned, row = run_two_model_event(state, event)

        self.assertIs(returned, state)
        self.assertIs(row.abstention_reason, TwoModelAbstentionReason.ARTIFACT_MISMATCH)
        self.assertFalse(row.model_1_static.supported)
        self.assertFalse(row.model_2_dynamic.supported)

    def test_invalid_event_identity_uses_safe_digest_bound_abstention_fields(self) -> None:
        cases = (
            ("point_id", "", "point_id"),
            ("canonical_match_id", "bad match", "canonical_match_id"),
            ("sequence_number", 0, "sequence_number"),
            ("sequence_type", "one", "sequence_number"),
        )
        for label, value, field_name in cases:
            with self.subTest(tamper=label):
                state = _initialized()
                event = _event(
                    point_id="point-1",
                    sequence_number=1,
                    before=_initial_state(),
                    winner=PlayerSide.HOME,
                )
                object.__setattr__(event, field_name, value)
                event_sha256 = pilot_contract_sha256(event)

                returned, row = run_two_model_event(state, event)

                self.assertIs(returned, state)
                self.assertIs(row.status, TwoModelRowStatus.ABSTAINED)
                self.assertIs(
                    row.abstention_reason,
                    TwoModelAbstentionReason.INVALID_EVENT,
                )
                self.assertEqual(row.point_event_sha256, event_sha256)
                expected_point_id = (
                    f"invalid-point-{event_sha256[:16]}"
                    if field_name == "point_id"
                    else "point-1"
                )
                expected_match_id = (
                    f"invalid-match-{event_sha256[:16]}"
                    if field_name == "canonical_match_id"
                    else "match-1"
                )
                self.assertEqual(row.point_id, expected_point_id)
                self.assertEqual(row.canonical_match_id, expected_match_id)
                self.assertEqual(
                    row.sequence_number,
                    1,
                )
                self.assertEqual(
                    row.prior_state_sha256,
                    row.resulting_state_sha256,
                )

    def test_oversized_sequence_uses_distinct_domain_marked_event_digest(self) -> None:
        oversized = 9_223_372_036_854_775_808
        rows = []
        for sequence_number in (oversized, oversized + 1):
            state = _initialized()
            event = _event(
                point_id="point-1",
                sequence_number=1,
                before=_initial_state(),
                winner=PlayerSide.HOME,
            )
            object.__setattr__(event, "sequence_number", sequence_number)
            remaining_fields = {
                field.name: getattr(event, field.name)
                for field in fields(PilotPointEvent)
                if field.name
                not in {"canonical_match_id", "point_id", "sequence_number"}
            }
            expected_sha256 = pilot_contract_sha256(
                {
                    "domain": "inci-tennis-two-model-invalid-event-identity-v1",
                    "contract_type": "PilotPointEvent",
                    "identity_fields": {
                        "canonical_match_id": {
                            "python_type": "str",
                            "value": "match-1",
                        },
                        "point_id": {
                            "python_type": "str",
                            "value": "point-1",
                        },
                        "sequence_number": {
                            "python_type": "int",
                            "decimal_value": str(sequence_number),
                        },
                    },
                    "remaining_fields": remaining_fields,
                }
            )

            returned, row = run_two_model_event(state, event)

            self.assertIs(returned, state)
            self.assertIs(row.status, TwoModelRowStatus.ABSTAINED)
            self.assertIs(
                row.abstention_reason,
                TwoModelAbstentionReason.INVALID_EVENT,
            )
            self.assertEqual(row.sequence_number, 1)
            self.assertEqual(row.point_event_sha256, expected_sha256)
            self.assertEqual(row.prior_state_sha256, row.resulting_state_sha256)
            canonical_pilot_contract_bytes(row)
            rows.append(row)

        self.assertNotEqual(rows[0].point_event_sha256, rows[1].point_event_sha256)

    def test_identical_replays_are_byte_identical(self) -> None:
        first = _event(
            point_id="point-1",
            sequence_number=1,
            before=_initial_state(),
            winner=PlayerSide.HOME,
        )
        second = _event(
            point_id="point-2",
            sequence_number=2,
            before=first.after_state,
            winner=PlayerSide.AWAY,
        )

        encoded: list[bytes] = []
        for _ in range(2):
            state = _initialized()
            rows = []
            for event in (first, second):
                state, row = run_two_model_event(state, event)
                rows.append(row)
            encoded.append(encode_two_model_rows(tuple(rows)))

        self.assertEqual(encoded[0], encoded[1])
        self.assertTrue(encoded[0].endswith(b"\n"))
        self.assertEqual(encoded[0].count(b"\n"), 2)


class TwoModelPilotCliTests(unittest.TestCase):
    def test_atomic_output_failure_leaves_no_partial_final_or_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output = directory / "comparison.jsonl"
            original_write = os.write
            calls = 0

            def short_then_enospc(fd: int, data: object) -> int:
                nonlocal calls
                calls += 1
                if calls == 1:
                    return original_write(fd, memoryview(data)[:7])
                raise OSError(errno.ENOSPC, "synthetic disk full")

            with patch(
                "inci_tennis_runtime.two_model_pilot_cli.os.write",
                side_effect=short_then_enospc,
            ):
                with self.assertRaisesRegex(PilotCliError, "^output$"):
                    _write_exclusive(output, b"canonical-comparison-bytes")

            self.assertFalse(output.exists())
            self.assertEqual(tuple(directory.iterdir()), ())

    def test_cli_runs_real_synthetic_fixture_and_writes_only_comparison_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            replay, static, dynamic, events = _write_inputs(directory)
            output = directory / "comparison.jsonl"
            second_output = directory / "comparison-second.jsonl"

            result = _run_cli(
                "--replay",
                str(replay),
                "--static-artifact",
                str(static),
                "--dynamic-artifact",
                str(dynamic),
                "--output",
                str(output),
            )
            second_result = _run_cli(
                "--replay",
                str(replay),
                "--static-artifact",
                str(static),
                "--dynamic-artifact",
                str(dynamic),
                "--output",
                str(second_output),
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(
                second_result.returncode,
                0,
                second_result.stderr.decode(),
            )
            self.assertEqual(output.read_bytes(), second_output.read_bytes())
            state = _initialized()
            expected_rows = []
            for event in events:
                state, row = run_two_model_event(state, event)
                expected_rows.append(row)
            self.assertEqual(
                output.read_bytes(),
                encode_two_model_rows(tuple(expected_rows)),
            )
            rows = output.read_bytes().splitlines()
            self.assertEqual(len(rows), 2)
            for encoded, event, expected_row in zip(
                rows,
                events,
                expected_rows,
                strict=True,
            ):
                document = json.loads(encoded)
                value = document["value"]
                row_fields = value["fields"]
                self.assertEqual(value["$contract"], "TwoModelComparisonRow")
                self.assertEqual(row_fields["claim"], "PLUMBING_ONLY")
                self.assertEqual(
                    row_fields["authority"],
                    "RESEARCH_ONLY / NO_ORDERS",
                )
                self.assertEqual(
                    row_fields["status"],
                    {"$enum": "TwoModelRowStatus", "value": "models_evaluated"},
                )
                self.assertTrue(row_fields["model_1_static"]["fields"]["supported"])
                self.assertTrue(row_fields["model_2_dynamic"]["fields"]["supported"])
                self.assertEqual(
                    row_fields["point_event_sha256"],
                    pilot_contract_sha256(event),
                )
                row_projection = {
                    field.name: getattr(expected_row, field.name)
                    for field in fields(expected_row)
                    if field.name != "row_sha256"
                }
                self.assertEqual(
                    expected_row.row_sha256,
                    pilot_contract_sha256(row_projection),
                )
                self.assertEqual(
                    row_fields["row_sha256"],
                    expected_row.row_sha256,
                )

    def test_cli_rejects_symlink_existing_output_malformed_partial_and_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            replay, static, dynamic, _ = _write_inputs(directory)

            symlink = directory / "replay-link.jsonl"
            symlink.symlink_to(replay)
            existing = directory / "existing.jsonl"
            existing.write_bytes(b"keep")
            malformed = directory / "malformed.jsonl"
            malformed.write_bytes(b"{not-json}\n")
            partial = directory / "partial.jsonl"
            partial.write_bytes(replay.read_bytes().rstrip(b"\n"))
            tampered = directory / "tampered-static.json"
            tampered.write_bytes(static.read_bytes().replace(b'"0.64"', b'"0.65"'))

            cases = (
                ("symlink", symlink, static, dynamic, directory / "symlink-out.jsonl"),
                ("existing", replay, static, dynamic, existing),
                ("malformed", malformed, static, dynamic, directory / "bad-out.jsonl"),
                ("partial", partial, static, dynamic, directory / "partial-out.jsonl"),
                ("tampered", replay, tampered, dynamic, directory / "tamper-out.jsonl"),
            )
            for label, candidate_replay, candidate_static, candidate_dynamic, output in cases:
                with self.subTest(case=label):
                    result = _run_cli(
                        "--replay",
                        str(candidate_replay),
                        "--static-artifact",
                        str(candidate_static),
                        "--dynamic-artifact",
                        str(candidate_dynamic),
                        "--output",
                        str(output),
                    )
                    self.assertEqual(result.returncode, 1, result.stderr.decode())
            self.assertEqual(existing.read_bytes(), b"keep")
            self.assertFalse(
                any(path.name.endswith(".tmp") for path in directory.iterdir())
            )

    def test_cli_usage_errors_return_two(self) -> None:
        result = _run_cli("--replay", "/missing")
        self.assertEqual(result.returncode, 2)

    def test_modules_have_no_live_or_trading_dependency(self) -> None:
        allowed = {
            "inci_tennis_expert/two_model_pilot.py": {
                "__future__",
                "dataclasses",
                "decimal",
                "enum",
                "re",
                "inci_tennis_expert.contracts",
                "inci_tennis_expert.pilot_contracts",
                "inci_tennis_expert.pilot_dynamic_model",
                "inci_tennis_expert.pilot_static_model",
            },
            "inci_tennis_runtime/two_model_pilot_cli.py": {
                "__future__",
                "argparse",
                "decimal",
                "json",
                "os",
                "pathlib",
                "stat",
                "sys",
                "tempfile",
                "inci_tennis_expert.contracts",
                "inci_tennis_expert.pilot_contracts",
                "inci_tennis_expert.pilot_dynamic_model",
                "inci_tennis_expert.tennis_score",
                "inci_tennis_expert.two_model_pilot",
            },
        }
        forbidden = {
            "socket",
            "urllib",
            "http",
            "requests",
            "credentials",
            "credential",
            "orders",
            "order",
            "executor",
            "execution",
            "market_book",
            "book",
            "markout",
            "policy",
        }
        for relative in (
            "inci_tennis_expert/two_model_pilot.py",
            "inci_tennis_runtime/two_model_pilot_cli.py",
        ):
            path = ROOT / relative
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    imported.add(node.module)
            self.assertEqual(
                imported - allowed[relative],
                set(),
                f"{relative}: unexpected imports",
            )
            components = {
                component
                for module in imported
                for component in module.lower().split(".")
            }
            self.assertTrue(
                components.isdisjoint(forbidden),
                f"{relative}: {sorted(components & forbidden)}",
            )


if __name__ == "__main__":
    unittest.main()
