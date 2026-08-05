"""Filesystem-only command for the deterministic two-model tennis pilot."""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from inci_tennis_expert.contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ProviderPoint,
    ScoreValue,
    SetScore,
    TennisState,
    TennisTransitionReason,
    TerminationKind,
)
from inci_tennis_expert.pilot_contracts import (
    PilotPointEvent,
    ServeStrengthArtifact,
    canonical_pilot_contract_bytes,
    compute_serve_strength_artifact_sha256,
    compute_training_match_ids_sha256,
)
from inci_tennis_expert.pilot_dynamic_model import (
    DynamicParameterCandidate,
    DynamicPointArtifact,
    compute_dynamic_point_artifact_sha256,
)
from inci_tennis_expert.tennis_score import apply_point
from inci_tennis_expert.two_model_pilot import (
    TwoModelPilotError,
    encode_two_model_rows,
    initialize_two_model_pilot,
    run_two_model_event,
)


class PilotCliError(ValueError):
    """Fixed-code input, validation, or filesystem halt."""


_CONTRACTS = {
    candidate.__name__: candidate
    for candidate in (
        DynamicParameterCandidate,
        DynamicPointArtifact,
        PilotPointEvent,
        ServeStrengthArtifact,
        SetScore,
        TennisState,
    )
}
_ENUMS = {
    candidate.__name__: candidate
    for candidate in (
        MatchFormat,
        MatchStatus,
        PlayerSide,
        ScoreValue,
        TennisTransitionReason,
        TerminationKind,
    )
}
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_SHA_D = "d" * 64


def _halt(code: str) -> None:
    raise PilotCliError(code)


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _halt("duplicate_json_key")
        result[key] = value
    return result


def _reject_json_number(_: str) -> object:
    _halt("json_number")


def _decode_projection(value: object) -> object:
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is not dict:
        _halt("canonical_projection")
    keys = set(value)
    if keys == {"$decimal"}:
        text = value["$decimal"]
        if type(text) is not str:
            _halt("canonical_decimal")
        try:
            result = Decimal(text)
        except Exception as error:
            raise PilotCliError("canonical_decimal") from error
        if not result.is_finite():
            _halt("canonical_decimal")
        return result
    if keys == {"$enum", "value"}:
        name = value["$enum"]
        raw = value["value"]
        if type(name) is not str or type(raw) is not str or name not in _ENUMS:
            _halt("canonical_enum")
        try:
            return _ENUMS[name](raw)
        except ValueError as error:
            raise PilotCliError("canonical_enum") from error
    if keys == {"$tuple"}:
        items = value["$tuple"]
        if type(items) is not list:
            _halt("canonical_tuple")
        return tuple(_decode_projection(item) for item in items)
    if keys == {"$list"}:
        items = value["$list"]
        if type(items) is not list:
            _halt("canonical_list")
        return [_decode_projection(item) for item in items]
    if keys == {"$dict"}:
        pairs = value["$dict"]
        if type(pairs) is not list:
            _halt("canonical_dict")
        result: dict[str, object] = {}
        for pair in pairs:
            if (
                type(pair) is not list
                or len(pair) != 2
                or type(pair[0]) is not str
                or pair[0] in result
            ):
                _halt("canonical_dict")
            result[pair[0]] = _decode_projection(pair[1])
        return result
    if keys == {"$contract", "$version", "fields"}:
        name = value["$contract"]
        version = value["$version"]
        raw_fields = value["fields"]
        if (
            type(name) is not str
            or name not in _CONTRACTS
            or version != 1
            or type(raw_fields) is not dict
        ):
            _halt("canonical_contract")
        decoded_fields = {
            key: _decode_projection(item)
            for key, item in raw_fields.items()
        }
        try:
            return _CONTRACTS[name](**decoded_fields)
        except Exception as error:
            raise PilotCliError("contract_validation") from error
    _halt("canonical_projection")


def _decode_contract(data: bytes, expected: type[object]) -> object:
    try:
        text = data.decode("ascii")
        document = json.loads(
            text,
            object_pairs_hook=_strict_pairs,
            parse_float=_reject_json_number,
            parse_constant=_reject_json_number,
        )
    except PilotCliError:
        raise
    except Exception as error:
        raise PilotCliError("malformed_json") from error
    if (
        type(document) is not dict
        or set(document) != {"canonical_version", "domain", "value"}
        or document["canonical_version"] != 1
        or document["domain"] != "inci-tennis-pilot"
    ):
        _halt("canonical_document")
    result = _decode_projection(document["value"])
    if type(result) is not expected or canonical_pilot_contract_bytes(result) != data:
        _halt("noncanonical_document")
    return result


def _absolute(path: Path, name: str) -> Path:
    if not path.is_absolute():
        _halt(name)
    return path


def _read_regular(path: Path, name: str) -> bytes:
    candidate = _absolute(path, name)
    try:
        before = os.lstat(candidate)
        if not stat.S_ISREG(before.st_mode):
            _halt(name)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags)
        try:
            after = os.fstat(descriptor)
            if (
                not stat.S_ISREG(after.st_mode)
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            ):
                _halt(name)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1_048_576)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except PilotCliError:
        raise
    except OSError as error:
        raise PilotCliError(name) from error


def _read_replay(path: Path) -> tuple[PilotPointEvent, ...]:
    data = _read_regular(path, "replay_input")
    if not data or not data.endswith(b"\n"):
        _halt("partial_final_record")
    records = data[:-1].split(b"\n")
    if not records or any(not record for record in records):
        _halt("blank_record")
    return tuple(
        _decode_contract(record, PilotPointEvent)  # type: ignore[arg-type]
        for record in records
    )


def _write_exclusive(path: Path, data: bytes) -> None:
    candidate = _absolute(path, "output")
    descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{candidate.name}.",
            suffix=".tmp",
            dir=candidate.parent,
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _halt("output_write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(temporary_path, candidate, follow_symlinks=False)
        os.unlink(temporary_path)
        temporary_path = None
        parent_descriptor = os.open(candidate.parent, os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except PilotCliError:
        raise
    except OSError as error:
        raise PilotCliError("output") from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _serve_example() -> ServeStrengthArtifact:
    training_ids = ("synthetic-static-training",)
    values = {
        "version": "pilot-serve-v1",
        "target_canonical_match_id": "synthetic-match",
        "target_scheduled_start_wall_ns": 9_000,
        "cutoff_wall_ns": 8_999,
        "training_match_ids": training_ids,
        "training_match_ids_sha256": compute_training_match_ids_sha256(training_ids),
        "source_data_sha256": _SHA_A,
        "feature_definition_sha256": _SHA_B,
        "code_sha256": _SHA_C,
        "home_serve_point_probability": Decimal("0.64"),
        "away_serve_point_probability": Decimal("0.61"),
    }
    return ServeStrengthArtifact(
        artifact_sha256=compute_serve_strength_artifact_sha256(**values),
        **values,
    )


def _dynamic_example() -> DynamicPointArtifact:
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
        "target_canonical_match_id": "synthetic-match",
        "target_scheduled_start_wall_ns": 9_000,
        "cutoff_wall_ns": 8_999,
        "training_match_ids": ("synthetic-dynamic-training",),
        "validation_match_ids": ("synthetic-dynamic-validation",),
        "source_data_sha256": _SHA_A,
        "feature_definition_sha256": _SHA_B,
        "code_sha256": _SHA_C,
        "selected": selected,
    }
    return DynamicPointArtifact(
        artifact_sha256=compute_dynamic_point_artifact_sha256(**values),
        **values,
    )


def _example_events() -> tuple[PilotPointEvent, ...]:
    state = TennisState(
        provider_source_id="synthetic-source",
        revision_domain_id="synthetic-revisions",
        source_lineage_sha256=_SHA_A,
        provider_match_id="synthetic-provider-match",
        home_player_id="synthetic-home",
        away_player_id="synthetic-away",
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
        correction_epoch=0,
        revision=1,
        snapshot_complete=True,
        last_provider_event_id="synthetic-event-0",
        last_event_semantic_sha256=_SHA_B,
        correction_lineage_sha256=_SHA_C,
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
    events: list[PilotPointEvent] = []
    for sequence_number, winner in enumerate(
        (PlayerSide.HOME, PlayerSide.AWAY),
        start=1,
    ):
        server = state.server_for_next_point
        if server not in (PlayerSide.HOME, PlayerSide.AWAY):
            _halt("synthetic_fixture")
        point_id = f"synthetic-point-{sequence_number}"
        after = apply_point(
            state,
            ProviderPoint(
                provider_source_id=state.provider_source_id,
                revision_domain_id=state.revision_domain_id,
                source_lineage_sha256=state.source_lineage_sha256,
                provider_event_id=f"synthetic-event-{sequence_number}",
                provider_match_id=state.provider_match_id,
                home_player_id=state.home_player_id,
                away_player_id=state.away_player_id,
                scheduled_start_wall_ns=state.scheduled_start_wall_ns,
                match_format=state.match_format,
                correction_epoch=0,
                revision=state.revision + 1,
                point_winner=winner,
                server_before_point=server,
                source_wall_ns=1_000 + sequence_number,
                source_generated_wall_ns=1_000 + sequence_number,
                received_monotonic_ns=1 + sequence_number,
                clock_uncertainty_ns=0,
            ),
        ).state
        events.append(
            PilotPointEvent(
                canonical_match_id="synthetic-match",
                point_id=point_id,
                sequence_number=sequence_number,
                before_state=state,
                after_state=after,
                server=server,
                winner=winner,
                consensus_epoch=0,
                consensus_transition_sha256=_SHA_D,
                supporting_source_lineage_sha256s=(_SHA_A, _SHA_B),
                received_wall_ns=1_000 + sequence_number,
                accepted_monotonic_ns=1 + sequence_number,
            )
        )
        state = after
    return tuple(events)


def _write_example(directory: Path) -> None:
    target = _absolute(directory, "example_directory")
    try:
        target.mkdir(mode=0o700)
    except OSError as error:
        raise PilotCliError("example_directory") from error
    _write_exclusive(target / "static.json", canonical_pilot_contract_bytes(_serve_example()))
    _write_exclusive(target / "dynamic.json", canonical_pilot_contract_bytes(_dynamic_example()))
    replay = b"".join(
        canonical_pilot_contract_bytes(event) + b"\n"
        for event in _example_events()
    )
    _write_exclusive(target / "events.jsonl", replay)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PLUMBING_ONLY offline two-model tennis comparison; NO ORDERS",
    )
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--static-artifact", type=Path)
    parser.add_argument("--dynamic-artifact", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--write-example", type=Path)
    return parser


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.write_example is not None:
        if any(
            value is not None
            for value in (
                args.replay,
                args.static_artifact,
                args.dynamic_artifact,
                args.output,
            )
        ):
            parser.error("--write-example cannot be combined with replay arguments")
        _write_example(args.write_example)
        return
    if any(
        value is None
        for value in (
            args.replay,
            args.static_artifact,
            args.dynamic_artifact,
            args.output,
        )
    ):
        parser.error(
            "--replay, --static-artifact, --dynamic-artifact, and --output are required"
        )
    events = _read_replay(args.replay)
    static_artifact = _decode_contract(
        _read_regular(args.static_artifact, "static_artifact_input"),
        ServeStrengthArtifact,
    )
    dynamic_artifact = _decode_contract(
        _read_regular(args.dynamic_artifact, "dynamic_artifact_input"),
        DynamicPointArtifact,
    )
    match_id = static_artifact.target_canonical_match_id
    if (
        dynamic_artifact.target_canonical_match_id != match_id
        or any(event.canonical_match_id != match_id for event in events)
    ):
        _halt("mixed_matches")
    state = initialize_two_model_pilot(static_artifact, dynamic_artifact)
    rows = []
    for event in events:
        state, row = run_two_model_event(state, event)
        rows.append(row)
    _write_exclusive(args.output, encode_two_model_rows(tuple(rows)))


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        _run(args, parser)
    except (PilotCliError, TwoModelPilotError, OSError, ValueError) as error:
        print(f"two_model_pilot: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
