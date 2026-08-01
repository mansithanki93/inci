"""Dependency-free terminal observer for one Sportradar Tennis trial match."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import signal
import sys
from typing import Callable, Mapping, TextIO
import unicodedata

from inci_tennis_adapters import sportradar_trial_v3 as trial_wire
from inci_tennis_io.facade import (
    SportradarTrialObserverError,
    SportradarTrialTransport,
    TrialCapture,
    TrialObservationRecord,
    TrialUsageLedger,
    load_trial_api_key,
    trial_monotonic_ns,
    trial_sleep,
)


_TERMINAL_STATUSES = frozenset(
    {"closed", "cancelled", "abandoned"}
)
_POLL_SECONDS = 10
_MAXIMUM_DURATION_SECONDS = 3_600
_MAXIMUM_SOURCE_AGE_NS = 60_000_000_000
_MAXIMUM_SOURCE_FUTURE_NS = 5_000_000_000


class _UsageError(ValueError):
    pass


class _OutputUnavailable(RuntimeError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _UsageError("invalid command arguments")


@dataclass(frozen=True, slots=True)
class TrialCliDependencies:
    ledger_factory: Callable[[], object] = TrialUsageLedger
    transport_factory: Callable[..., object] = SportradarTrialTransport
    sample_counter: Callable[[], int] = trial_monotonic_ns
    sleeper: Callable[[float], None] = trial_sleep


def _parser() -> _Parser:
    parser = _Parser(
        prog="python -m inci_tennis_runtime.sportradar_trial_cli",
        description="Read-only Sportradar Tennis trial observer",
    )
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--list-live", action="store_true")
    command.add_argument("--check", action="store_true")
    command.add_argument("--observe", action="store_true")
    parser.add_argument("--match-id")
    parser.add_argument("--duration-seconds", type=int)
    return parser


def _validate_arguments(arguments: argparse.Namespace) -> int:
    if arguments.list_live:
        if arguments.match_id is not None or arguments.duration_seconds is not None:
            raise _UsageError("--list-live does not accept match or duration")
        return 0
    if arguments.match_id is None:
        raise _UsageError("--match-id is required")
    if arguments.check:
        if arguments.duration_seconds is not None:
            raise _UsageError("--duration-seconds is only valid with --observe")
        return 0
    duration = (
        _MAXIMUM_DURATION_SECONDS
        if arguments.duration_seconds is None
        else arguments.duration_seconds
    )
    if type(duration) is not int or duration < _POLL_SECONDS or duration > _MAXIMUM_DURATION_SECONDS:
        raise _UsageError("--duration-seconds must be from 10 through 3600")
    return duration


def _safe_write(stream: TextIO, text: str) -> None:
    try:
        written = stream.write(text)
        if written != len(text):
            raise _OutputUnavailable
        stream.flush()
    except (OSError, UnicodeError, ValueError):
        raise _OutputUnavailable from None


def _best_effort_write(stream: TextIO, text: str) -> None:
    try:
        _safe_write(stream, text)
    except _OutputUnavailable:
        pass


class _OperatorSignalState:
    __slots__ = ("requested",)

    def __init__(self) -> None:
        self.requested = False

    def request(self, *_: object) -> None:
        self.requested = True

    def check(self) -> None:
        if self.requested:
            raise KeyboardInterrupt


@contextmanager
def _operator_signal_handlers() -> Iterator[_OperatorSignalState]:
    state = _OperatorSignalState()
    numbers = [signal.SIGINT, signal.SIGTERM]
    hangup = getattr(signal, "SIGHUP", None)
    if isinstance(hangup, int) and hangup not in numbers:
        numbers.append(hangup)
    previous: list[tuple[int, object]] = []
    try:
        for number in numbers:
            prior = signal.getsignal(number)
            signal.signal(number, state.request)
            previous.append((number, prior))
    except (OSError, ValueError):
        for number, prior in reversed(previous):
            try:
                signal.signal(number, prior)
            except (OSError, ValueError):
                pass
        raise SportradarTrialObserverError(
            "sportradar_signal_handler_unavailable"
        ) from None
    try:
        yield state
    finally:
        restoration_failed = False
        for number, prior in reversed(previous):
            try:
                signal.signal(number, prior)
            except (OSError, ValueError):
                restoration_failed = True
        if restoration_failed:
            raise SportradarTrialObserverError(
                "sportradar_signal_handler_unavailable"
            )


def _is_terminal(snapshot: trial_wire.SportradarScoreSnapshot) -> str | None:
    if snapshot.status in _TERMINAL_STATUSES:
        return snapshot.status
    if snapshot.match_status in {"cancelled", "abandoned"}:
        return snapshot.match_status
    return None


def _terminal_text(value: object, *, maximum: int = 256) -> str:
    text = str(value)
    filtered = "".join(
        " " if character in "\r\n\t" else "?"
        if unicodedata.category(character).startswith("C")
        else character
        for character in text
    )
    return " ".join(filtered.split())[:maximum]


def _table(rows: list[tuple[str, str]]) -> str:
    safe_rows = [
        (_terminal_text(label, maximum=32), _terminal_text(value, maximum=64))
        for label, value in rows
    ]
    width = max(len(label) for label, _ in safe_rows)
    border = "+" + "-" * (width + 2) + "+" + "-" * 66 + "+\n"
    lines = [border]
    for label, value in safe_rows:
        lines.append(f"| {label:<{width}} | {value:<64} |\n")
    lines.append(border)
    return "".join(lines)


def _source_age_or_record_failure(
    ledger: object,
    *,
    command: str,
    capture: TrialCapture,
    generated_wall_ns: int,
) -> float:
    try:
        difference = capture.captured_wall_ns - generated_wall_ns
        if difference < -_MAXIMUM_SOURCE_FUTURE_NS:
            raise SportradarTrialObserverError(
                "sportradar_source_time_ahead"
            )
        return max(0, difference) / 1_000_000_000
    except SportradarTrialObserverError as error:
        ledger.record_parser_failure(  # type: ignore[union-attr]
            command=command,
            reservation=capture.reservation,
            code=error.code,
        )
        raise


def _stale_live_source(
    snapshot: trial_wire.SportradarScoreSnapshot,
    source_age_seconds: float,
) -> bool:
    if _is_terminal(snapshot) is not None:
        return False
    active_feed = snapshot.status in {"live", "started", "ended"} or (
        snapshot.match_status is not None
        and snapshot.match_status
        not in {
            "not_started",
            "match_about_to_start",
            "postponed",
            "cancelled",
            "walkover",
            "abandoned",
        }
    )
    return (
        active_feed
        and source_age_seconds * 1_000_000_000 > _MAXIMUM_SOURCE_AGE_NS
    )


def _render_snapshot(
    stream: TextIO,
    snapshot: trial_wire.SportradarScoreSnapshot,
    *,
    last_event: str,
    calls: int,
    remaining_session: int,
    remaining_access: int,
    source_age_seconds: float,
) -> None:
    sets_home = "--" if snapshot.sets_home is None else str(snapshot.sets_home)
    sets_away = "--" if snapshot.sets_away is None else str(snapshot.sets_away)
    games_home = "--" if snapshot.games_home is None else str(snapshot.games_home)
    games_away = "--" if snapshot.games_away is None else str(snapshot.games_away)
    rows = [
        ("MODE", "SPORTRADAR TRIAL OBSERVER — READ ONLY"),
        ("MATCH", snapshot.provider_match_id),
        ("PLAYERS", f"{snapshot.home_name} vs {snapshot.away_name}"),
        ("STATE", f"{snapshot.status} / {snapshot.match_status or '--'}"),
        (
            "SETS",
            f"{snapshot.home_name}: {sets_home} | "
            f"{snapshot.away_name}: {sets_away}",
        ),
        ("CURRENT SET GAMES", f"{games_home} - {games_away}"),
        ("POINTS", f"{snapshot.points_home} - {snapshot.points_away}"),
        ("SERVER", snapshot.serving or "unknown"),
        (
            "TIEBREAK",
            "--"
            if snapshot.in_tiebreak is None
            else "yes"
            if snapshot.in_tiebreak
            else "no",
        ),
        ("LAST EVENT", last_event),
        ("SOURCE AGE", f"{source_age_seconds:.1f}s"),
        ("CALLS THIS RUN", str(calls)),
        ("RUN CALLS LEFT", str(remaining_session)),
        ("TRIAL CALLS LEFT", str(remaining_access)),
    ]
    try:
        interactive = bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        interactive = False
    prefix = "\x1b[2J\x1b[H" if interactive else ""
    _safe_write(stream, prefix + _table(rows))


def _record(
    ledger: object,
    *,
    command: str,
    capture: TrialCapture,
    snapshot: trial_wire.SportradarScoreSnapshot,
    progression: str,
    last_event_id: int | None,
    terminal_reason: str | None,
) -> None:
    record = TrialObservationRecord(
        command=command,
        reservation=capture.reservation,
        provider_match_id=snapshot.provider_match_id,
        generated_wall_ns=snapshot.generated_wall_ns,
        captured_wall_ns=capture.captured_wall_ns,
        status=snapshot.status,
        match_status=snapshot.match_status,
        payload_sha256=sha256(capture.payload).hexdigest(),
        raw_path=capture.raw_path,
        progression=progression,
        last_event_id=last_event_id,
        terminal_reason=terminal_reason,
    )
    ledger.record_observation(record)  # type: ignore[union-attr]


def _limits(ledger: object) -> tuple[int, int, int]:
    try:
        return (
            int(ledger.session_attempts),
            int(ledger.remaining_session_attempts),
            int(ledger.remaining_access_attempts),
        )
    except (AttributeError, TypeError, ValueError):
        raise SportradarTrialObserverError(
            "sportradar_usage_ledger_invalid"
        ) from None


def _list_live(
    transport: object,
    ledger: object,
    stdout: TextIO,
    stop_check: Callable[[], None],
) -> str:
    stop_check()
    capture = transport.fetch_live_summaries()  # type: ignore[union-attr]
    stop_check()
    try:
        envelope = trial_wire.parse_live_summaries_envelope(capture.payload)
    except trial_wire.SportradarWireContractError as error:
        ledger.record_parser_failure(  # type: ignore[union-attr]
            command="list_live",
            reservation=capture.reservation,
            code=error.code,
        )
        raise
    snapshots = envelope.snapshots
    source_age = _source_age_or_record_failure(
        ledger,
        command="list_live",
        capture=capture,
        generated_wall_ns=envelope.generated_wall_ns,
    )
    lines = [
        "SPORTRADAR TENNIS — LIVE MATCHES — READ ONLY\n",
        f"SOURCE AGE: {source_age:.1f}s\n",
        "MATCH ID                         STATE          MATCH\n",
        "--------------------------------  -------------  "
        "----------------------------------------------\n",
    ]
    for snapshot in sorted(
        snapshots,
        key=lambda item: (item.start_wall_ns, item.provider_match_id),
    ):
        lines.append(
            f"{_terminal_text(snapshot.provider_match_id, maximum=32):<32}  "
            f"{_terminal_text(snapshot.status, maximum=13):<13}  "
            f"{_terminal_text(snapshot.home_name, maximum=64)} vs "
            f"{_terminal_text(snapshot.away_name, maximum=64)}\n"
        )
    if not snapshots:
        lines.append("No live matches returned.\n")
    ledger.record_observation(  # type: ignore[union-attr]
        TrialObservationRecord(
            command="list_live",
            reservation=capture.reservation,
            provider_match_id=None,
            generated_wall_ns=envelope.generated_wall_ns,
            captured_wall_ns=capture.captured_wall_ns,
            status="listed",
            match_status=None,
            payload_sha256=sha256(capture.payload).hexdigest(),
            raw_path=capture.raw_path,
            progression="discovery",
            last_event_id=None,
            terminal_reason="empty" if not snapshots else None,
        )
    )
    _safe_write(stdout, "".join(lines))
    stop_check()
    if source_age * 1_000_000_000 > _MAXIMUM_SOURCE_AGE_NS:
        raise SportradarTrialObserverError("sportradar_source_stale")
    return "list_complete"


def _check(
    transport: object,
    ledger: object,
    stdout: TextIO,
    match_id: str,
    stop_check: Callable[[], None],
) -> str:
    stop_check()
    capture = transport.fetch_summary(match_id)  # type: ignore[union-attr]
    stop_check()
    try:
        snapshot = trial_wire.parse_sport_event_summary(
            capture.payload,
            expected_match_id=match_id,
        )
    except trial_wire.SportradarWireContractError as error:
        ledger.record_parser_failure(  # type: ignore[union-attr]
            command="check",
            reservation=capture.reservation,
            code=error.code,
        )
        raise
    source_age = _source_age_or_record_failure(
        ledger,
        command="check",
        capture=capture,
        generated_wall_ns=snapshot.generated_wall_ns,
    )
    terminal = _is_terminal(snapshot)
    _record(
        ledger,
        command="check",
        capture=capture,
        snapshot=snapshot,
        progression="initial",
        last_event_id=None,
        terminal_reason=terminal,
    )
    calls, remaining_session, remaining_access = _limits(ledger)
    _render_snapshot(
        stdout,
        snapshot,
        last_event="summary check",
        calls=calls,
        remaining_session=remaining_session,
        remaining_access=remaining_access,
        source_age_seconds=source_age,
    )
    stop_check()
    if terminal is not None:
        return terminal
    if _stale_live_source(snapshot, source_age):
        raise SportradarTrialObserverError("sportradar_source_stale")
    return "check_complete"


def _observe(
    transport: object,
    ledger: object,
    stdout: TextIO,
    match_id: str,
    duration_seconds: int,
    dependencies: TrialCliDependencies,
    stop_check: Callable[[], None],
) -> str:
    start_ns = dependencies.sample_counter()
    stop_check()
    capture = transport.fetch_summary(match_id)  # type: ignore[union-attr]
    stop_check()
    try:
        snapshot = trial_wire.parse_sport_event_summary(
            capture.payload,
            expected_match_id=match_id,
        )
    except trial_wire.SportradarWireContractError as error:
        ledger.record_parser_failure(  # type: ignore[union-attr]
            command="observe",
            reservation=capture.reservation,
            code=error.code,
        )
        raise
    source_age = _source_age_or_record_failure(
        ledger,
        command="observe",
        capture=capture,
        generated_wall_ns=snapshot.generated_wall_ns,
    )
    terminal = _is_terminal(snapshot)
    _record(
        ledger,
        command="observe",
        capture=capture,
        snapshot=snapshot,
        progression="initial",
        last_event_id=None,
        terminal_reason=terminal,
    )
    calls, remaining_session, remaining_access = _limits(ledger)
    _render_snapshot(
        stdout,
        snapshot,
        last_event="summary",
        calls=calls,
        remaining_session=remaining_session,
        remaining_access=remaining_access,
        source_age_seconds=source_age,
    )
    stop_check()
    if terminal is not None:
        return terminal
    if _stale_live_source(snapshot, source_age):
        raise SportradarTrialObserverError("sportradar_source_stale")

    previous: trial_wire.SportradarTimelineSnapshot | None = None
    duration_ns = duration_seconds * 1_000_000_000
    while True:
        stop_check()
        now_ns = dependencies.sample_counter()
        if type(now_ns) is not int or now_ns < start_ns:
            raise SportradarTrialObserverError("sportradar_clock_invalid")
        remaining_ns = duration_ns - (now_ns - start_ns)
        if remaining_ns <= 0:
            return "duration_elapsed"
        wait_seconds = min(_POLL_SECONDS, remaining_ns / 1_000_000_000)
        dependencies.sleeper(wait_seconds)
        stop_check()
        if dependencies.sample_counter() - start_ns >= duration_ns:
            return "duration_elapsed"

        capture = transport.fetch_timeline(match_id)  # type: ignore[union-attr]
        stop_check()
        try:
            current = trial_wire.parse_sport_event_timeline(
                capture.payload,
                expected_match_id=match_id,
            )
            if previous is None:
                trial_wire.validate_timeline_after_summary(snapshot, current)
                progression = "initial_timeline"
            else:
                progression = trial_wire.validate_timeline_progression(
                    previous,
                    current,
                )
        except trial_wire.SportradarWireContractError as error:
            ledger.record_parser_failure(  # type: ignore[union-attr]
                command="observe",
                reservation=capture.reservation,
                code=error.code,
            )
            raise
        source_age = _source_age_or_record_failure(
            ledger,
            command="observe",
            capture=capture,
            generated_wall_ns=current.score.generated_wall_ns,
        )
        last_event = current.events[-1] if current.events else None
        terminal = _is_terminal(current.score)
        _record(
            ledger,
            command="observe",
            capture=capture,
            snapshot=current.score,
            progression=progression,
            last_event_id=None if last_event is None else last_event.event_id,
            terminal_reason=terminal,
        )
        calls, remaining_session, remaining_access = _limits(ledger)
        event_text = (
            "none"
            if last_event is None
            else f"{last_event.event_id} {last_event.event_type}"
            + (f" ({last_event.result})" if last_event.result else "")
        )
        _render_snapshot(
            stdout,
            current.score,
            last_event=event_text,
            calls=calls,
            remaining_session=remaining_session,
            remaining_access=remaining_access,
            source_age_seconds=source_age,
        )
        stop_check()
        if terminal is not None:
            return terminal
        if _stale_live_source(current.score, source_age):
            raise SportradarTrialObserverError("sportradar_source_stale")
        previous = current


def run_cli(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    dependencies: TrialCliDependencies | None = None,
) -> int:
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    services = TrialCliDependencies() if dependencies is None else dependencies
    try:
        arguments = _parser().parse_args(argv)
        duration = _validate_arguments(arguments)
    except _UsageError as error:
        _best_effort_write(errors, f"ERROR: {error}\n")
        return 2
    api_key = (
        load_trial_api_key()
        if environ is None
        else environ.get("SPORTRADAR_API_KEY")
    )
    if type(api_key) is not str or not api_key:
        _best_effort_write(
            errors,
            "ERROR: SPORTRADAR_API_KEY is not loaded\n",
        )
        return 2

    command_name = (
        "list_live" if arguments.list_live else "check" if arguments.check else "observe"
    )
    provider_match_id = None if arguments.list_live else arguments.match_id
    try:
        with _operator_signal_handlers() as operator_signals:
            with services.ledger_factory() as ledger:
                try:
                    operator_signals.check()
                    recovered = sum(
                        int(getattr(ledger, name, 0))
                        for name in (
                            "recovered_uncertain_attempts",
                            "recovered_incomplete_captures",
                            "recovered_unclean_sessions",
                        )
                    )
                    if recovered:
                        _safe_write(
                            errors,
                            "WARNING: recovered "
                            f"{recovered} incomplete prior audit record(s)\n",
                        )
                    with services.transport_factory(
                        api_key=api_key,
                        ledger=ledger,
                    ) as transport:
                        if arguments.list_live:
                            terminal_reason = _list_live(
                                transport,
                                ledger,
                                output,
                                operator_signals.check,
                            )
                        elif arguments.check:
                            terminal_reason = _check(
                                transport,
                                ledger,
                                output,
                                arguments.match_id,
                                operator_signals.check,
                            )
                        else:
                            terminal_reason = _observe(
                                transport,
                                ledger,
                                output,
                                arguments.match_id,
                                duration,
                                services,
                                operator_signals.check,
                            )
                    operator_signals.check()
                except KeyboardInterrupt:
                    ledger.record_session_terminal(  # type: ignore[union-attr]
                        command=command_name,
                        provider_match_id=provider_match_id,
                        reason="operator_interrupt",
                        code="sportradar_operator_interrupt",
                    )
                    _best_effort_write(
                        errors, "STOPPED: operator interrupt\n"
                    )
                    return 130
                except (
                    SportradarTrialObserverError,
                    trial_wire.SportradarWireContractError,
                ) as error:
                    ledger.record_session_terminal(  # type: ignore[union-attr]
                        command=command_name,
                        provider_match_id=provider_match_id,
                        reason="halted",
                        code=error.code,
                    )
                    _best_effort_write(errors, f"HALTED: {error}\n")
                    return 1
                except _OutputUnavailable:
                    ledger.record_session_terminal(  # type: ignore[union-attr]
                        command=command_name,
                        provider_match_id=provider_match_id,
                        reason="halted",
                        code="sportradar_output_unavailable",
                    )
                    _best_effort_write(
                        errors,
                        "HALTED: sportradar_output_unavailable\n",
                    )
                    return 1
                except Exception:
                    ledger.record_session_terminal(  # type: ignore[union-attr]
                        command=command_name,
                        provider_match_id=provider_match_id,
                        reason="halted",
                        code="sportradar_internal_error",
                    )
                    _best_effort_write(
                        errors,
                        "HALTED: sportradar_internal_error\n",
                    )
                    return 1
                ledger.record_session_terminal(  # type: ignore[union-attr]
                    command=command_name,
                    provider_match_id=provider_match_id,
                    reason=terminal_reason,
                )
                return 0
    except KeyboardInterrupt:
        _best_effort_write(errors, "STOPPED: operator interrupt\n")
        return 130
    except SportradarTrialObserverError as error:
        _best_effort_write(errors, f"HALTED: {error}\n")
        return 1
    except Exception:
        _best_effort_write(errors, "HALTED: sportradar_internal_error\n")
        return 1


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("TrialCliDependencies", "main", "run_cli")
