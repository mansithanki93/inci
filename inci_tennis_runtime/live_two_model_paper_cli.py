"""Runnable live Models 1+2 paper-only bridge.

Growing-file mode is deterministic and network-free.  Live mode delegates all
catalog, Sportradar, and Kalshi I/O to the existing read-only shadow command.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import time
from typing import TextIO

from inci_tennis_expert.live_paper_session import (
    LivePaperHeartbeatInput,
    LivePaperRecord,
    LivePaperRecordKind,
    LivePaperTerminalInput,
    compute_live_paper_provider_authority_sha256,
    encode_live_paper_checkpoint,
    encode_live_paper_records,
    load_live_paper_checkpoint,
    reduce_live_paper_input,
    replay_live_paper_records,
)
from inci_tennis_expert.live_two_model import (
    LiveArtifactAuthority,
    build_operator_bootstrap_artifacts,
)
from inci_tennis_expert.pilot_contracts import ServeStrengthArtifact
from inci_tennis_expert.pilot_dynamic_model import DynamicPointArtifact
from inci_tennis_runtime.live_paper_capture_bridge import (
    GrowingJsonlCaptureBridge,
    LivePaperBridgeError,
    LivePaperCaptureObserver,
    LivePaperManifest,
    live_paper_provider_authorities,
    load_live_paper_manifest,
)


BANNER = "LIVE MODELS 1+2 / PAPER ONLY / NO REAL ORDERS"
_MAX_STREAM_BYTES = 32 * 1024 * 1024
_MAX_LINE_BYTES = 1 * 1024 * 1024


class LivePaperCliError(ValueError):
    """Fixed-code CLI, path, or persistence rejection."""


def _fail(code: str) -> None:
    raise LivePaperCliError(code)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        _fail("invalid_arguments")


@dataclass(frozen=True, slots=True)
class LivePaperCliArguments:
    live_readonly: bool
    manifest: Path | None
    score_stream: Path | None
    kalshi_stream: Path | None
    session_log: Path
    checkpoint: Path | None
    static_artifact: Path | None
    dynamic_artifact: Path | None
    bootstrap_home_serve: Decimal | None
    bootstrap_away_serve: Decimal | None
    duration_seconds: int
    stop_at_eof: bool
    replay_only: bool


def _probability(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation:
        _fail("invalid_probability")
    if not result.is_finite() or not Decimal("0") < result < Decimal("1"):
        _fail("invalid_probability")
    return result


def _parser() -> _Parser:
    parser = _Parser(
        prog="python -m inci_tennis_runtime.live_two_model_paper_cli",
        allow_abbrev=False,
        description="Live Models 1+2 paper-only runner; never places orders",
    )
    parser.add_argument("--live-readonly", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--score-stream", type=Path)
    parser.add_argument("--kalshi-stream", type=Path)
    parser.add_argument("--session-log", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--static-artifact", type=Path)
    parser.add_argument("--dynamic-artifact", type=Path)
    parser.add_argument("--bootstrap-home-serve", type=_probability)
    parser.add_argument("--bootstrap-away-serve", type=_probability)
    parser.add_argument("--duration-seconds", type=int)
    parser.add_argument("--stop-at-eof", action="store_true")
    parser.add_argument("--replay-only", action="store_true")
    return parser


def parse_cli_arguments(argv: list[str] | None) -> LivePaperCliArguments:
    value = _parser().parse_args(argv)
    if value.session_log is None:
        _fail("invalid_arguments")
    if value.replay_only:
        conflicts = (
            value.live_readonly, value.manifest, value.score_stream,
            value.kalshi_stream, value.checkpoint, value.static_artifact,
            value.dynamic_artifact, value.bootstrap_home_serve,
            value.bootstrap_away_serve, value.duration_seconds,
            value.stop_at_eof,
        )
        if (
            value.duration_seconds is not None
            or value.stop_at_eof
            or any(item not in (None, False) for item in conflicts[:-2])
        ):
            _fail("replay_option_conflict")
        return LivePaperCliArguments(
            False, None, None, None, value.session_log, None,
            None, None, None, None, 600, False, True,
        )
    if value.manifest is None or value.checkpoint is None:
        _fail("invalid_arguments")
    streams = (value.score_stream, value.kalshi_stream)
    if value.live_readonly:
        if any(item is not None for item in streams) or value.stop_at_eof:
            _fail("input_mode_xor")
    elif any(item is None for item in streams):
        _fail("input_mode_xor")
    artifacts = (value.static_artifact, value.dynamic_artifact)
    priors = (value.bootstrap_home_serve, value.bootstrap_away_serve)
    artifact_mode = all(item is not None for item in artifacts) and all(item is None for item in priors)
    bootstrap_mode = all(item is None for item in artifacts) and all(item is not None for item in priors)
    if not (artifact_mode ^ bootstrap_mode):
        _fail("artifact_bootstrap_xor")
    duration_seconds = 600 if value.duration_seconds is None else value.duration_seconds
    minimum_duration = 10 if value.live_readonly else 1
    if (
        type(duration_seconds) is not int
        or not minimum_duration <= duration_seconds <= 3_600
    ):
        _fail("duration_seconds")
    return LivePaperCliArguments(
        value.live_readonly, value.manifest, value.score_stream,
        value.kalshi_stream, value.session_log, value.checkpoint,
        value.static_artifact, value.dynamic_artifact,
        value.bootstrap_home_serve, value.bootstrap_away_serve,
        duration_seconds, value.stop_at_eof, False,
    )


def _absolute_regular(path: Path, code: str) -> None:
    if not path.is_absolute():
        _fail(code)
    try:
        value = os.lstat(path)
    except OSError as error:
        raise LivePaperCliError(code) from error
    if not stat.S_ISREG(value.st_mode):
        _fail(code)


def _output_candidate(path: Path, code: str) -> None:
    if not path.is_absolute():
        _fail(code)
    try:
        parent = os.lstat(path.parent)
    except OSError as error:
        raise LivePaperCliError(code) from error
    if not stat.S_ISDIR(parent.st_mode):
        _fail(code)
    try:
        value = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise LivePaperCliError(code) from error
    if not stat.S_ISREG(value.st_mode):
        _fail(code)


def validate_cli_paths(
    *,
    manifest: Path,
    score_stream: Path | None,
    kalshi_stream: Path | None,
    static_artifact: Path | None,
    dynamic_artifact: Path | None,
    session_log: Path,
    checkpoint: Path,
) -> None:
    inputs = tuple(
        item for item in (
            manifest, score_stream, kalshi_stream, static_artifact,
            dynamic_artifact,
        ) if item is not None
    )
    for path in inputs:
        _absolute_regular(path, "input_path")
    _output_candidate(session_log, "session_log_path")
    _output_candidate(checkpoint, "checkpoint_path")
    identities = tuple(
        os.path.normcase(str(path.resolve(strict=False)))
        for path in inputs + (session_log, checkpoint)
    )
    if len(set(identities)) != len(identities):
        _fail("path_collision")
    inode_identities: list[tuple[int, int]] = []
    for path in inputs + tuple(
        item for item in (session_log, checkpoint) if item.exists()
    ):
        value = os.lstat(path)
        identity = (value.st_dev, value.st_ino)
        if identity in inode_identities:
            _fail("path_collision")
        inode_identities.append(identity)


def _read_regular(path: Path, code: str, maximum: int) -> bytes:
    try:
        before = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            after = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                _fail(code)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(1_048_576, maximum + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum:
                    _fail(code)
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except LivePaperCliError:
        raise
    except OSError as error:
        raise LivePaperCliError(code) from error


def _strict_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            _fail("duplicate_json_key")
        value[key] = item
    return value


def _reject_json_number(_: str) -> object:
    _fail("json_number")


def _jsonl_row(
    line: bytes, source: str, ordinal: int,
) -> tuple[int, int, str, dict[str, object]]:
    if not line or len(line) > _MAX_LINE_BYTES:
        _fail(source + "_line")
    try:
        value = json.loads(
            line.decode("ascii"),
            object_pairs_hook=_strict_json_pairs,
            parse_float=_reject_json_number,
            parse_constant=_reject_json_number,
        )
    except Exception as error:
        raise LivePaperCliError(source + "_json") from error
    if type(value) is not dict:
        _fail(source + "_json")
    wall = value.get("captured_wall_ns")
    mono = value.get("captured_monotonic_ns")
    if type(wall) is not int or type(mono) is not int:
        _fail(source + "_clock")
    return mono, ordinal, source, value


class _GrowingJsonlReader:
    """Bounded append reader retaining an incomplete final JSONL row."""

    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.source = source
        self.offset = 0
        self.partial = b""
        self.authenticated_prefix = b""
        self.ordinal = 0

    def poll(self) -> list[tuple[int, int, str, dict[str, object]]]:
        raw = _read_regular(
            self.path, self.source + "_stream", _MAX_STREAM_BYTES
        )
        if (
            len(raw) < self.offset
            or raw[: self.offset] != self.authenticated_prefix
        ):
            _fail("growing_stream_prefix_changed")
        suffix = raw[self.offset:]
        self.authenticated_prefix = raw
        self.offset = len(raw)
        pending = self.partial + suffix
        last_newline = pending.rfind(b"\n")
        if last_newline < 0:
            if len(pending) > _MAX_LINE_BYTES:
                _fail(self.source + "_partial_line")
            self.partial = pending
            return []
        complete = pending[:last_newline]
        self.partial = pending[last_newline + 1:]
        if len(self.partial) > _MAX_LINE_BYTES:
            _fail(self.source + "_partial_line")
        rows: list[tuple[int, int, str, dict[str, object]]] = []
        for line in complete.split(b"\n"):
            self.ordinal += 1
            rows.append(_jsonl_row(line, self.source, self.ordinal))
        return rows

    def finish(self) -> None:
        if self.partial:
            _fail(self.source + "_partial_line")


def _jsonl(path: Path, source: str) -> list[tuple[int, int, str, dict[str, object]]]:
    reader = _GrowingJsonlReader(path, source)
    rows = reader.poll()
    reader.finish()
    return rows


def _ordered_rows(
    rows: list[tuple[int, int, str, dict[str, object]]],
) -> list[tuple[int, int, str, dict[str, object]]]:
    return sorted(
        rows,
        key=lambda item: (
            item[0], int(item[3]["captured_wall_ns"]),
            0 if item[2] == "score" else 1, item[1],
        ),
    )


def _validate_clock_order(
    rows: list[tuple[int, int, str, dict[str, object]]],
    boundary: tuple[int, int] | None,
) -> tuple[int, int] | None:
    current = boundary
    for mono, _, _, row in rows:
        wall = int(row["captured_wall_ns"])
        if current is not None and (wall < current[0] or mono < current[1]):
            _fail("captured_clock_regression")
        current = (wall, mono)
    return current


class _DurableSessionWriter:
    def __init__(self, log_path: Path, checkpoint_path: Path, existing: bytes) -> None:
        self.log_path = log_path
        self.checkpoint_path = checkpoint_path
        flags = os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        created = False
        try:
            if existing:
                descriptor = os.open(log_path, flags)
            else:
                try:
                    descriptor = os.open(
                        log_path,
                        flags | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    created = True
                except FileExistsError:
                    descriptor = os.open(log_path, flags)
            try:
                fcntl.flock(
                    descriptor,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except OSError:
                os.close(descriptor)
                _fail("session_log_locked")
            descriptor_stat = os.fstat(descriptor)
            path_stat = os.lstat(log_path)
            if (
                not stat.S_ISREG(descriptor_stat.st_mode)
                or not stat.S_ISREG(path_stat.st_mode)
                or (descriptor_stat.st_dev, descriptor_stat.st_ino)
                != (path_stat.st_dev, path_stat.st_ino)
            ):
                _fail("session_log_changed")
            if descriptor_stat.st_size > 32 * 1024 * 1024:
                _fail("session_log_changed")
            current = self._pread_exact(descriptor, descriptor_stat.st_size)
            if current != existing:
                _fail(
                    "session_log_changed"
                    if existing
                    else "session_log_exists"
                )
            os.fchmod(descriptor, 0o600)
        except BaseException:
            if "descriptor" in locals():
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise
        if created:
            parent = os.open(log_path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        self.descriptor = descriptor
        self.device_inode = (descriptor_stat.st_dev, descriptor_stat.st_ino)
        self.encoded = existing
        self.length = len(existing)
        self.closed = False

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            os.close(self.descriptor)

    @staticmethod
    def _pread_exact(descriptor: int, size: int) -> bytes:
        chunks: list[bytes] = []
        offset = 0
        while offset < size:
            chunk = os.pread(descriptor, size - offset, offset)
            if not chunk:
                _fail("session_log_changed")
            chunks.append(chunk)
            offset += len(chunk)
        return b"".join(chunks)

    def _authenticate_append_descriptor(self) -> None:
        descriptor_stat = os.fstat(self.descriptor)
        path_stat = os.lstat(self.log_path)
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or not stat.S_ISREG(path_stat.st_mode)
            or (descriptor_stat.st_dev, descriptor_stat.st_ino)
            != self.device_inode
            or (path_stat.st_dev, path_stat.st_ino) != self.device_inode
            or descriptor_stat.st_size != self.length
            or self._pread_exact(self.descriptor, self.length) != self.encoded
        ):
            _fail("session_log_changed")

    @staticmethod
    def _write_all(descriptor: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("write_failed")
            view = view[written:]

    def commit(self, records: tuple[LivePaperRecord, ...], state: object) -> None:
        if records:
            encoded = encode_live_paper_records(records)
            if (
                self.length > len(encoded)
                or encoded[: self.length] != self.encoded
            ):
                _fail("session_log_length")
            suffix = encoded[self.length:]
            if not suffix:
                _fail("session_log_append")
            self._authenticate_append_descriptor()
            self._write_all(self.descriptor, suffix)
            os.fsync(self.descriptor)
            self.encoded = encoded
            self.length = len(encoded)
        checkpoint = encode_live_paper_checkpoint(state)  # type: ignore[arg-type]
        temporary_descriptor: int | None = None
        temporary_path: Path | None = None
        try:
            temporary_descriptor, name = tempfile.mkstemp(
                prefix="." + self.checkpoint_path.name + ".",
                suffix=".tmp",
                dir=self.checkpoint_path.parent,
            )
            temporary_path = Path(name)
            os.fchmod(temporary_descriptor, 0o600)
            self._write_all(temporary_descriptor, checkpoint)
            os.fsync(temporary_descriptor)
            os.close(temporary_descriptor)
            temporary_descriptor = None
            if self.checkpoint_path.is_symlink():
                _fail("checkpoint_symlink")
            os.replace(temporary_path, self.checkpoint_path)
            temporary_path = None
            parent = os.open(self.checkpoint_path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if temporary_descriptor is not None:
                os.close(temporary_descriptor)
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass


def _artifact_pair(args: LivePaperCliArguments, manifest: LivePaperManifest) -> tuple[ServeStrengthArtifact, DynamicPointArtifact, LiveArtifactAuthority]:
    if args.bootstrap_home_serve is not None and args.bootstrap_away_serve is not None:
        static, dynamic = build_operator_bootstrap_artifacts(
            canonical_match_id=manifest.canonical_match_id,
            scheduled_start_wall_ns=manifest.scheduled_start_wall_ns,
            cutoff_wall_ns=manifest.scheduled_start_wall_ns - 1,
            home_serve_point_probability=args.bootstrap_home_serve,
            away_serve_point_probability=args.bootstrap_away_serve,
        )
        return static, dynamic, LiveArtifactAuthority.OPERATOR_BOOTSTRAP
    if args.static_artifact is None or args.dynamic_artifact is None:
        _fail("artifact_mode")
    from inci_tennis_runtime.two_model_pilot_cli import decode_pilot_contract

    static = decode_pilot_contract(
        _read_regular(args.static_artifact, "static_artifact", 8 * 1024 * 1024),
        ServeStrengthArtifact,
    )
    dynamic = decode_pilot_contract(
        _read_regular(args.dynamic_artifact, "dynamic_artifact", 8 * 1024 * 1024),
        DynamicPointArtifact,
    )
    return static, dynamic, LiveArtifactAuthority.TRAINED_ARTIFACT


def _startup_disclosure(
    stream: TextIO,
    bridge: GrowingJsonlCaptureBridge,
    manifest: LivePaperManifest,
    state_root: Path,
) -> None:
    config = bridge.state.config
    sources = ",".join(
        f"{provider.slot}/{provider.source_id}:"
        + "independence_proven="
        + (
            "true" if provider.independence_proven is True
            else "false" if provider.independence_proven is False
            else "unknown"
        )
        for provider in config.provider_authorities
    )
    proven_lineages = {
        provider.independent_lineage_id
        for provider in config.provider_authorities
        if provider.independence_proven is True
    }
    trust_eligibility = (
        "CONSENSUS_PAPER"
        if len(proven_lineages) >= 2
        else "SINGLE_SOURCE_PAPER"
        if config.provider_authorities
        else "ABSTAINED"
    )
    binding = config.market_binding
    stream.write(
        "startup "
        f"sources={sources} trust_eligibility={trust_eligibility} "
        f"artifact_authority={config.artifact_authority.value} "
        f"static_sha256={config.static_artifact.artifact_sha256} "
        f"dynamic_sha256={config.dynamic_artifact.artifact_sha256} "
        f"canonical_match_id={config.canonical_match_id} "
        f"scheduled_start_wall_ns={binding.scheduled_start_wall_ns} "
        f"match_format={manifest.match_format.name} "
        f"HOME={binding.home_ticker}/{binding.home_market_id}/YES_HOME "
        f"AWAY={binding.away_ticker}/{binding.away_market_id}/YES_AWAY "
        f"max_debit={format(config.maximum_debit, 'f')} "
        f"minimum_edge={format(config.minimum_edge, 'f')} "
        f"exit_profit=+{format(config.exit_profit, 'f')} "
        f"exit_loss=-{format(config.exit_loss, 'f')} "
        f"maximum_hold={config.maximum_hold_ns // 1_000_000_000}s "
        f"decision_latency={config.decision_latency_ns // 1_000_000_000}s "
        f"freshness={config.score_freshness_ns // 1_000_000_000}s "
        f"score_freshness={config.score_freshness_ns // 1_000_000_000}s "
        f"book_freshness={config.book_freshness_ns // 1_000_000_000}s "
        f"state_root={state_root} NO REAL ORDERS\n"
    )
    stream.flush()


def _top_executable(levels: object) -> str:
    for level in levels:  # type: ignore[union-attr]
        if Decimal("0") < level.price < Decimal("1"):
            return f"{format(level.price, 'f')}@{format(level.quantity, 'f')}"
    return "--"


def _dashboard(
    stream: TextIO,
    bridge: GrowingJsonlCaptureBridge,
    last_kind: str,
    *,
    now_monotonic_ns: int | None = None,
) -> None:
    state = bridge.state
    records = bridge.records
    anchor = state.score_coordinator.anchor
    trust = "ABSTAINED" if anchor is None else anchor.trust.value
    forecast = state.latest_forecast
    model_1_match = "--" if forecast is None or forecast.model_1_match_probability is None else format(forecast.model_1_match_probability, "f")
    model_2_match = "--" if forecast is None or forecast.model_2_match_probability is None else format(forecast.model_2_match_probability, "f")
    model_1_set = "--" if forecast is None or forecast.model_1_current_set_probability is None else format(forecast.model_1_current_set_probability, "f")
    model_2_set = "--" if forecast is None or forecast.model_2_current_set_probability is None else format(forecast.model_2_current_set_probability, "f")
    position = state.portfolio.position
    position_text = "flat" if position is None else f"{position.ticker} x {position.quantity}"
    cash = Decimal("0")
    for record in records:
        if record.kind is not LivePaperRecordKind.FILL:
            continue
        fill = record.payload.body.fill
        amount = fill.debit_or_credit
        cash += (
            amount - fill.fees
            if fill.action_kind.value == "SELL"
            else -amount - fill.fees
        )
    pnl = format(cash, "f") if position is None else "--"
    if position is not None:
        for record in reversed(records):
            if record.kind is LivePaperRecordKind.MARK:
                mark = record.payload.body
                if mark.ticker == position.ticker and mark.fully_priced:
                    pnl = format(cash + mark.net_liquidation_value, "f")
                break
    score = "--"
    server = "--"
    if anchor is not None:
        score = (
            f"sets={len(anchor.state.completed_sets)} games="
            f"{anchor.state.games_home}-{anchor.state.games_away} points="
            f"{anchor.state.points_home.value}-{anchor.state.points_away.value}"
        )
        server = (
            "--" if anchor.state.server_for_next_point is None
            else anchor.state.server_for_next_point.value
        )
    latest_book = bridge.last_book_monotonic_ns
    observed_now = now_monotonic_ns
    if observed_now is None:
        observed_now = max(
            (
                value
                for record in records
                for value in (
                    getattr(record.payload.body, "observed_monotonic_ns", None),
                    getattr(
                        getattr(record.payload.body, "frame", None),
                        "captured_monotonic_ns",
                        None,
                    ),
                )
                if type(value) is int
            ),
            default=None,
        )
    book_age = "--" if latest_book is None or observed_now is None else f"{max(0, observed_now - latest_book) / 1_000_000_000:.3f}s"
    elapsed = (
        "--" if observed_now is None
        else f"{max(0, observed_now - state.config.opened_monotonic_ns) / 1_000_000_000:.3f}s"
    )
    latest_sources: dict[tuple[str, str], int] = {}
    latest_frame = None
    for record in records:
        if record.kind is LivePaperRecordKind.RAW_SCORE_RECEIPT:
            for observation in record.payload.body.observations:
                latest_sources[(observation.provider_slot, observation.source_id)] = (
                    observation.captured_monotonic_ns
                )
        elif record.kind is LivePaperRecordKind.RAW_L2_RECEIPT:
            latest_frame = record.payload.body.frame
    source_health_parts: list[str] = []
    for provider in state.config.provider_authorities:
        captured = latest_sources.get((provider.slot, provider.source_id))
        health = "seen" if captured is not None else "missing"
        source_health_parts.append(
            f"{provider.slot}/{provider.source_id}:{health}"
        )
    source_health = ",".join(source_health_parts) or "none"
    home_book = away_book = "--"
    if latest_frame is not None:
        home_book = (
            f"bid:{_top_executable(latest_frame.home.yes_bids)}/"
            f"ask:{_top_executable(latest_frame.home.yes_asks)}"
        )
        away_book = (
            f"bid:{_top_executable(latest_frame.away.yes_bids)}/"
            f"ask:{_top_executable(latest_frame.away.yes_asks)}"
        )
    pending_action = state.portfolio.pending_action
    pending = (
        "none" if pending_action is None
        else f"{pending_action.kind.value}:{pending_action.ticker}x"
        f"{format(pending_action.remaining_quantity, 'f')}"
    )
    last_decision = "none"
    rejection_counts: dict[str, int] = {}
    for record in records:
        if record.kind in {
            LivePaperRecordKind.REJECTION, LivePaperRecordKind.ABSTENTION,
        }:
            reason = str(getattr(record.payload.body, "reason", record.kind.value))
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            last_decision = f"{record.kind.value}:{reason}"
        elif record.kind is LivePaperRecordKind.ACTION:
            action = record.payload.body
            last_decision = f"action:{action.kind.value}:{action.ticker}"
    rejection_counts_text = (
        ",".join(
            f"{reason}:{rejection_counts[reason]}"
            for reason in sorted(rejection_counts)
        )
        or "none"
    )
    rejection = last_kind
    for record in reversed(records):
        if record.kind in {LivePaperRecordKind.REJECTION, LivePaperRecordKind.ABSTENTION}:
            body = record.payload.body
            rejection = str(getattr(body, "reason", record.kind.value))
            break
    stream.write(
        f"event={last_kind} elapsed={elapsed} source_health={source_health} "
        f"trust={trust} score={score} server={server} "
        f"model1_set={model_1_set} model1_match={model_1_match} "
        f"model2_set={model_2_set} model2_match={model_2_match} "
        f"home_book={home_book} away_book={away_book} book_age={book_age} "
        f"pending={pending} last_decision={last_decision} "
        f"rejection_counts={rejection_counts_text} "
        f"paper_position={position_text} pnl={pnl} "
        f"top_rejection={rejection}\n"
    )
    stream.flush()


def _restore_or_open(
    args: LivePaperCliArguments,
    manifest: LivePaperManifest,
    static: ServeStrengthArtifact,
    dynamic: DynamicPointArtifact,
    authority: LiveArtifactAuthority,
    *,
    opened_wall_ns: int,
    opened_monotonic_ns: int,
) -> tuple[GrowingJsonlCaptureBridge, bytes]:
    if args.session_log.exists():
        raw = _read_regular(args.session_log, "session_log", 32 * 1024 * 1024)
        if not raw:
            _fail("empty_existing_session")
        checkpoint_raw = (
            _read_regular(args.checkpoint, "checkpoint", 16 * 1024 * 1024)
            if args.checkpoint.exists() else None
        )
        replay = load_live_paper_checkpoint(checkpoint_raw, raw)
        config = replay.state.config
        provider_authorities = live_paper_provider_authorities(manifest)
        if (
            config.canonical_match_id != manifest.canonical_match_id
            or config.manifest_sha256 != manifest.manifest_sha256
            or config.provider_authorities != provider_authorities
            or config.provider_authority_sha256
            != compute_live_paper_provider_authority_sha256(
                provider_authorities
            )
            or config.market_binding != manifest.binding
            or config.static_artifact != static
            or config.dynamic_artifact != dynamic
            or config.artifact_authority is not authority
            or config.fee_schedule != manifest.fee_schedule
            or config.fee_series_ticker != manifest.fee_series_ticker
        ):
            _fail("resume_config_mismatch")
        bridge = GrowingJsonlCaptureBridge(manifest, replay.state)
        bridge.restore_records(
            replay.records,
            reconstruct_live_adapter=args.live_readonly,
        )
        return bridge, raw
    if args.checkpoint.exists():
        _fail("checkpoint_without_log")
    bridge = GrowingJsonlCaptureBridge.from_artifacts(
        manifest,
        static_artifact=static,
        dynamic_artifact=dynamic,
        artifact_authority=authority,
        opened_wall_ns=opened_wall_ns,
        opened_monotonic_ns=opened_monotonic_ns,
    )
    return bridge, b""


def _rehydrate_committed_inputs(
    bridge: GrowingJsonlCaptureBridge,
    merged: list[tuple[int, int, str, dict[str, object]]],
) -> tuple[int, tuple[int, int] | None]:
    receipts = tuple(
        record.payload.body
        for record in bridge.records
        if record.kind in {
            LivePaperRecordKind.RAW_SCORE_RECEIPT,
            LivePaperRecordKind.RAW_CAPTURE_RECEIPT,
            LivePaperRecordKind.RAW_L2_RECEIPT,
        }
    )
    if not receipts:
        return 0, None
    receipt_index = 0
    for row_index, (_, _, source, row) in enumerate(merged):
        projected = bridge.rehydrate_envelope(source, row)
        if projected is None:
            continue
        if projected != receipts[receipt_index]:
            _fail("growing_stream_committed_prefix_mismatch")
        receipt_index += 1
        if receipt_index == len(receipts):
            return row_index + 1, (
                int(row["captured_wall_ns"]),
                int(row["captured_monotonic_ns"]),
            )
    _fail("growing_stream_missing_committed_prefix")


def _terminal(
    bridge: GrowingJsonlCaptureBridge,
    reason: str,
    wall: int,
    monotonic: int,
) -> tuple[LivePaperRecord, ...]:
    state, records = reduce_live_paper_input(
        bridge.state,
        LivePaperTerminalInput(reason, wall, monotonic),
    )
    bridge.state = state
    bridge.records.extend(records)
    return records


def _run_growing(args: LivePaperCliArguments, manifest: LivePaperManifest, stdout: TextIO) -> int:
    assert args.score_stream is not None and args.kalshi_stream is not None
    assert args.checkpoint is not None
    score_reader = _GrowingJsonlReader(args.score_stream, "score")
    kalshi_reader = _GrowingJsonlReader(args.kalshi_stream, "kalshi")
    score_rows = score_reader.poll()
    kalshi_rows = kalshi_reader.poll()
    if args.stop_at_eof:
        score_reader.finish()
        kalshi_reader.finish()
    merged = _ordered_rows(score_rows + kalshi_rows)
    _validate_clock_order(merged, None)
    first_wall = (
        int(merged[0][3]["captured_wall_ns"])
        if merged else time.time_ns()
    )
    first_mono = merged[0][0] if merged else time.monotonic_ns()
    static, dynamic, authority = _artifact_pair(args, manifest)
    bridge, existing = _restore_or_open(
        args, manifest, static, dynamic, authority,
        opened_wall_ns=max(0, int(first_wall) - 1),
        opened_monotonic_ns=max(0, first_mono - 1),
    )
    _startup_disclosure(stdout, bridge, manifest, args.session_log.parent)
    if bridge.state.terminal:
        _fail("session_already_terminal")
    committed_row_count, clock_boundary = (
        _rehydrate_committed_inputs(bridge, merged)
        if existing else (0, None)
    )
    writer = _DurableSessionWriter(args.session_log, args.checkpoint, existing)
    last_wall = first_wall if clock_boundary is None else clock_boundary[0]
    last_mono = first_mono if clock_boundary is None else clock_boundary[1]
    stop = False

    def request_stop(*_: object) -> None:
        nonlocal stop
        stop = True

    installed: list[tuple[int, object]] = []
    started_tail = time.monotonic_ns()
    next_heartbeat = started_tail + 60_000_000_000

    def process_rows(
        rows: list[tuple[int, int, str, dict[str, object]]]
    ) -> None:
        nonlocal last_wall, last_mono, clock_boundary
        _validate_clock_order(rows, clock_boundary)
        for mono, _, source, row in rows:
            if stop:
                break
            wall = int(row["captured_wall_ns"])
            records = (
                bridge.accept_score_envelope(row)
                if source == "score"
                else bridge.accept_kalshi_envelope(row)
            )
            last_wall = wall
            last_mono = mono
            clock_boundary = (last_wall, last_mono)
            if records:
                writer.commit(tuple(bridge.records), bridge.state)
                _dashboard(
                    stdout, bridge, records[-1].kind.value,
                    now_monotonic_ns=mono,
                )
    try:
        for selected in (signal.SIGINT, signal.SIGTERM):
            try:
                installed.append((selected, signal.getsignal(selected)))
                signal.signal(selected, request_stop)
            except (ValueError, OSError):
                pass
        process_rows(merged[committed_row_count:])
        while (
            not stop
            and not args.stop_at_eof
            and time.monotonic_ns() - started_tail
            < args.duration_seconds * 1_000_000_000
        ):
            additions = score_reader.poll() + kalshi_reader.poll()
            if additions:
                process_rows(_ordered_rows(additions))
            now = time.monotonic_ns()
            if now >= next_heartbeat:
                wall = time.time_ns()
                state, records = reduce_live_paper_input(
                    bridge.state,
                    LivePaperHeartbeatInput(wall, now),
                )
                bridge.state = state
                bridge.records.extend(records)
                if records:
                    writer.commit(tuple(bridge.records), bridge.state)
                    _dashboard(
                        stdout, bridge, records[-1].kind.value,
                        now_monotonic_ns=now,
                    )
                    last_wall = wall
                    last_mono = now
                next_heartbeat += 60_000_000_000
            if not additions:
                time.sleep(0.1)
        reason = (
            "operator_interrupt"
            if stop
            else "streams_exhausted"
            if args.stop_at_eof
            else "duration_elapsed"
        )
        records = _terminal(bridge, reason, last_wall + 1, last_mono + 1)
        writer.commit(tuple(bridge.records), bridge.state)
        _dashboard(stdout, bridge, records[-1].kind.value)
    except (LivePaperBridgeError, ValueError):
        if not bridge.state.terminal:
            records = _terminal(
                bridge, "halted", max(0, last_wall + 1), max(0, last_mono + 1)
            )
            writer.commit(tuple(bridge.records), bridge.state)
            _dashboard(stdout, bridge, records[-1].kind.value)
        raise
    finally:
        for selected, previous in installed:
            signal.signal(selected, previous)
        writer.close()
    return 130 if stop else 0


def _run_live(args: LivePaperCliArguments, manifest: LivePaperManifest, stdout: TextIO, stderr: TextIO) -> int:
    from inci_tennis_runtime.live_shadow_cli import (
        LiveShadowCliDependencies,
        run_cli as run_shadow_cli,
    )

    static, dynamic, authority = _artifact_pair(args, manifest)
    bridge, existing = _restore_or_open(
        args, manifest, static, dynamic, authority,
        opened_wall_ns=time.time_ns(), opened_monotonic_ns=time.monotonic_ns(),
    )
    _startup_disclosure(stdout, bridge, manifest, args.session_log.parent)
    if bridge.state.terminal:
        _fail("session_already_terminal")
    writer = _DurableSessionWriter(args.session_log, args.checkpoint, existing)

    def validate(match_id: str, tickers: tuple[str, str]) -> None:
        providers = tuple(row for row in manifest.providers if row.slot == "sportradar")
        if len(providers) != 1 or match_id != providers[0].provider_match_id or tickers != (manifest.binding.home_ticker, manifest.binding.away_ticker):
            raise LivePaperCliError("live_selection_manifest_mismatch")

    def persist_observer_records(_: tuple[LivePaperRecord, ...]) -> None:
        writer.commit(tuple(bridge.records), bridge.state)
        _dashboard(stdout, bridge, bridge.records[-1].kind.value)

    observer = LivePaperCaptureObserver(
        bridge, record_sink=persist_observer_records
    )
    dependencies = replace(
        LiveShadowCliDependencies(),
        capture_observer=observer,
        collection_identity_validator=validate,
    )
    try:
        status = run_shadow_cli(
            ["--choose", "--duration-seconds", str(args.duration_seconds), "--poll-seconds", "10"],
            stdout=stdout, stderr=stderr, dependencies=dependencies,
        )
        if not bridge.state.terminal:
            terminal_reason = (
                "operator_interrupt"
                if status == 130
                else "collector_terminal"
                if status == 0
                else "halted"
            )
            records = _terminal(
                bridge,
                terminal_reason,
                time.time_ns(), time.monotonic_ns(),
            )
            writer.commit(tuple(bridge.records), bridge.state)
            _dashboard(stdout, bridge, records[-1].kind.value)
        return status
    finally:
        writer.close()


def run(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    try:
        args = parse_cli_arguments(argv)
        output.write(BANNER + "\n")
        output.flush()
        if args.replay_only:
            _absolute_regular(args.session_log, "session_log_path")
            raw = _read_regular(
                args.session_log, "session_log", 32 * 1024 * 1024
            )
            replay = replay_live_paper_records(raw, require_terminal=True)
            output.write(
                f"event=replay_verified records={len(replay.records)} "
                "terminal=true NO REAL ORDERS\n"
            )
            output.flush()
            return 0
        assert args.manifest is not None and args.checkpoint is not None
        validate_cli_paths(
            manifest=args.manifest,
            score_stream=args.score_stream,
            kalshi_stream=args.kalshi_stream,
            static_artifact=args.static_artifact,
            dynamic_artifact=args.dynamic_artifact,
            session_log=args.session_log,
            checkpoint=args.checkpoint,
        )
        manifest = load_live_paper_manifest(args.manifest)
        return (
            _run_live(args, manifest, output, errors)
            if args.live_readonly
            else _run_growing(args, manifest, output)
        )
    except (LivePaperCliError, LivePaperBridgeError, OSError, ValueError) as error:
        errors.write(f"live_two_model_paper: {error}\n")
        errors.flush()
        return 2 if str(error) == "invalid_arguments" else 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
