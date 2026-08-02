"""Fail-closed CLI composition for the unqualified live tennis shadow."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from re import compile as pattern_compile
import signal
import sys
from typing import Iterator, TextIO

from inci_tennis_io.facade import (
    KalshiReadOnlyCredentials,
    KalshiReadOnlyTransport,
    ShadowEvidenceStore,
    SportradarShadowAsyncTransport,
    TrialUsageLedger,
    default_shadow_state_root,
    load_shadow_credential_material,
    shadow_kalshi_clock_observation,
    shadow_monotonic_ns,
    shadow_pause,
    shadow_wall_ns,
)
from inci_tennis_runtime.live_shadow_collector import (
    CandidateMarketProjection,
    CandidateMarketView,
    LiveShadowCollector,
    ShadowCollectorError,
)


_MATCH = pattern_compile(r"sr:sport_event:[1-9][0-9]*\Z")
_TICKER = pattern_compile(r"[A-Z0-9][A-Z0-9._-]{0,127}\Z")
_SAFE_CODE = pattern_compile(
    r"(?:shadow|kalshi|sportradar)_[a-z0-9_]{1,96}\Z"
)


class _UsageError(ValueError):
    pass


class _OutputError(RuntimeError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _UsageError("invalid command arguments")


def _async_provider(**values: object) -> object:
    return SportradarShadowAsyncTransport(**values)


def _decimal_text(value: object) -> str | None:
    return None if value is None else format(value, "f")


class UnqualifiedKalshiProjector:
    """Adapt the pure aggregate reducer to the collector's narrow port."""

    def __init__(self, tickers: tuple[str, str]) -> None:
        import inci_tennis_adapters.kalshi_v2 as kalshi_v2

        self._tickers = tickers
        self._adapter = kalshi_v2
        self._reducer = kalshi_v2.UnqualifiedTwoTickerBookReducer(tickers)
        self._generation: int | None = None

    def begin_subscription(self, receipt: object) -> None:
        if getattr(receipt, "command", None) != "subscribe":
            raise ShadowCollectorError("kalshi_command_receipt_invalid")
        generation = getattr(receipt, "physical_connection_generation", None)
        request_id = getattr(receipt, "request_id", None)
        self._reducer.begin_subscription(generation, request_id)
        self._generation = generation

    def snapshot_requested(self, receipt: object) -> None:
        if getattr(receipt, "command", None) != "get_snapshot":
            raise ShadowCollectorError("kalshi_command_receipt_invalid")
        state = self._reducer.state
        self._reducer.expect_snapshot(
            getattr(receipt, "physical_connection_generation", None),
            state.sid,
            getattr(receipt, "request_id", None),
        )

    def disconnect(self, generation: int | None) -> None:
        selected = self._generation if generation is None else generation
        if selected is None:
            return
        self._reducer.disconnect(selected)

    def apply(self, frame: object) -> CandidateMarketProjection:
        try:
            payload = frame.payload
            generation = frame.physical_connection_generation
            parsed = self._adapter.parse_unqualified_book_message(payload)
            state = self._reducer.apply(parsed, generation)
        except ShadowCollectorError:
            raise
        except Exception:
            raise ShadowCollectorError("kalshi_ws_contract_invalid") from None
        if state.status == "terminal":
            raise ShadowCollectorError("kalshi_stream_terminal")
        if state.status == "ready":
            status = "candidate"
            reason = "candidate_book_ready"
        elif state.status == "empty_book":
            status = "incomplete"
            reason = "empty_book"
        elif state.status == "invalidated":
            mapping = {
                "sequence_gap": ("gap", "kalshi_sequence_gap"),
                "sequence_duplicate": (
                    "duplicate",
                    "kalshi_sequence_duplicate",
                ),
                "sequence_out_of_order": (
                    "out_of_order",
                    "kalshi_sequence_out_of_order",
                ),
            }
            status, reason = mapping.get(
                state.reason,
                ("snapshot_required", "kalshi_resnapshot_requested"),
            )
        else:
            status = "incomplete"
            reason = "candidate_book_incomplete"
        markets = tuple(
            CandidateMarketView(
                ticker=view.ticker,
                yes_bid=(
                    _decimal_text(view.yes_bid)
                    if status == "candidate"
                    else None
                ),
                yes_ask=(
                    _decimal_text(view.yes_ask)
                    if status == "candidate"
                    else None
                ),
                bid_depth=(
                    _decimal_text(view.yes_bid_depth)
                    if status == "candidate"
                    else None
                ),
                ask_depth=(
                    _decimal_text(view.yes_ask_depth)
                    if status == "candidate"
                    else None
                ),
            )
            for view in state.views
        )
        if len(markets) != 2:
            raise ShadowCollectorError("kalshi_candidate_state_invalid")
        return CandidateMarketProjection(
            markets=(markets[0], markets[1]),
            generation=state.generation,
            sequence=state.sequence,
            subscription_id=state.sid,
            status=status,
            reason=reason,
            snapshot_needed=state.snapshot_needed,
        )


def _kalshi_transport(material: object, tickers: tuple[str, str]) -> object:
    """Compose the read-only transport through the reviewed IO facade."""

    credential = KalshiReadOnlyCredentials(
        api_key_id=material.kalshi_api_key_id,
        private_key_path=material.kalshi_private_key_path,
    )
    return KalshiReadOnlyTransport(
        credentials=credential,
        market_tickers=tickers,
        clock_observer=shadow_kalshi_clock_observation,
    )


@dataclass(frozen=True, slots=True)
class LiveShadowCliDependencies:
    credential_loader: Callable[..., object] = load_shadow_credential_material
    trial_ledger_factory: Callable[[], object] = TrialUsageLedger
    sportradar_transport_factory: Callable[..., object] = (
        _async_provider
    )
    evidence_store_factory: Callable[[], object] = ShadowEvidenceStore
    evidence_root: Callable[[], object] = default_shadow_state_root
    kalshi_transport_factory: Callable[[object, tuple[str, str]], object] = (
        _kalshi_transport
    )
    projector_factory: Callable[[tuple[str, str]], object] = (
        UnqualifiedKalshiProjector
    )
    collector_factory: Callable[..., object] = LiveShadowCollector
    wall_ns: Callable[[], int] = shadow_wall_ns
    monotonic_ns: Callable[[], int] = shadow_monotonic_ns
    pause: Callable[..., object] = shadow_pause


def _parser() -> _Parser:
    parser = _Parser(
        prog="python -m inci_tennis_runtime.live_shadow_cli",
        description=(
            "Read-only unqualified tennis evidence collector; no orders"
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--match-id", required=True)
    parser.add_argument("--home-ticker", required=True)
    parser.add_argument("--away-ticker", required=True)
    parser.add_argument("--duration-seconds", required=True, type=int)
    parser.add_argument("--poll-seconds", type=int, default=10)
    return parser


def _arguments(argv: list[str] | None) -> tuple[str, tuple[str, str], int, int]:
    value = _parser().parse_args(argv)
    tickers = (value.home_ticker, value.away_ticker)
    if (
        type(value.match_id) is not str
        or _MATCH.fullmatch(value.match_id) is None
        or any(type(item) is not str or _TICKER.fullmatch(item) is None for item in tickers)
        or tickers[0] == tickers[1]
        or type(value.duration_seconds) is not int
        or value.duration_seconds < 10
        or value.duration_seconds > 3_600
        or type(value.poll_seconds) is not int
        or value.poll_seconds < 1
        or value.poll_seconds > value.duration_seconds
    ):
        raise _UsageError("invalid command arguments")
    return value.match_id, tickers, value.duration_seconds, value.poll_seconds


def _write(stream: TextIO, value: str) -> None:
    try:
        written = stream.write(value)
        if written != len(value):
            raise _OutputError
        stream.flush()
    except (OSError, UnicodeError, ValueError):
        raise _OutputError from None


def _best_effort_write(stream: TextIO, value: str) -> None:
    try:
        _write(stream, value)
    except _OutputError:
        pass


class _DashboardOutput:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        try:
            self._interactive = bool(stream.isatty())
        except (AttributeError, OSError, ValueError):
            self._interactive = False

    def __call__(self, rendered: str) -> None:
        if type(rendered) is not str or not rendered:
            raise _OutputError
        prefix = "\x1b[2J\x1b[H" if self._interactive else ""
        suffix = "" if rendered.endswith("\n") else "\n"
        _write(self._stream, prefix + rendered + suffix)


class _StopState:
    __slots__ = ("requested",)

    def __init__(self) -> None:
        self.requested = False

    def request(self, *_: object) -> None:
        self.requested = True

    def __call__(self) -> bool:
        return self.requested


@contextmanager
def _signals() -> Iterator[_StopState]:
    state = _StopState()
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
        raise ShadowCollectorError("shadow_signal_handler_unavailable") from None
    try:
        yield state
    finally:
        restore_failed = False
        for number, prior in reversed(previous):
            try:
                signal.signal(number, prior)
            except (OSError, ValueError):
                restore_failed = True
        if restore_failed:
            raise ShadowCollectorError(
                "shadow_signal_handler_unavailable"
            ) from None


def _code(error: BaseException) -> str:
    value = getattr(error, "code", None)
    return (
        value
        if type(value) is str and _SAFE_CODE.fullmatch(value) is not None
        else "shadow_internal_error"
    )


def _planned_provider_calls(duration_seconds: int, poll_seconds: int) -> int:
    return 1 + (duration_seconds - 1) // poll_seconds


def _preflight_quota(ledger: object, planned_calls: int) -> None:
    session = getattr(ledger, "remaining_session_attempts", None)
    access = getattr(ledger, "remaining_access_attempts", None)
    if (
        type(planned_calls) is not int
        or planned_calls <= 0
        or type(session) is not int
        or session < 0
        or type(access) is not int
        or access < 0
    ):
        raise ShadowCollectorError("sportradar_shadow_quota_invalid")
    if planned_calls > session or planned_calls > access:
        raise ShadowCollectorError("sportradar_shadow_quota_insufficient")


def _startup_banner(
    *, planned_calls: int, evidence_root: object
) -> str:
    root = str(evidence_root)
    if not root or "\n" in root or "\r" in root or len(root) > 4_096:
        raise ShadowCollectorError("shadow_evidence_root_invalid")
    return (
        "READ ONLY / UNQUALIFIED / NO ORDERS\n"
        "starting unqualified tennis shadow collector\n"
        "ticker mapping: OPERATOR-SUPPLIED / UNVERIFIED\n"
        f"planned provider calls: {planned_calls}\n"
        f"evidence root: {root}"
    )


async def _run(
    *,
    match_id: str,
    tickers: tuple[str, str],
    duration_seconds: int,
    poll_seconds: int,
    material: object,
    output: _DashboardOutput,
    services: LiveShadowCliDependencies,
    stop: _StopState,
) -> str:
    with services.trial_ledger_factory() as trial_ledger:
        planned_calls = _planned_provider_calls(
            duration_seconds, poll_seconds
        )
        _preflight_quota(trial_ledger, planned_calls)
        output(
            _startup_banner(
                planned_calls=planned_calls,
                evidence_root=services.evidence_root(),
            )
        )
        provider_context = services.sportradar_transport_factory(
            api_key=material.sportradar_api_key,
            ledger=trial_ledger,
        )
        async with provider_context as provider:
            kalshi = services.kalshi_transport_factory(material, tickers)
            projector = services.projector_factory(tickers)
            with services.evidence_store_factory() as evidence:
                collector: object | None = None
                try:
                    collector = services.collector_factory(
                        provider_match_id=match_id,
                        market_tickers=tickers,
                        sportradar_transport=provider,
                        sportradar_ledger=trial_ledger,
                        kalshi_transport=kalshi,
                        market_projector=projector,
                        evidence_store=evidence,
                        wall_ns=services.wall_ns,
                        monotonic_ns=services.monotonic_ns,
                        pause=services.pause,
                        stop_requested=stop,
                        render=output,
                    )
                    return await collector.run(
                        duration_seconds=duration_seconds,
                        poll_seconds=poll_seconds,
                    )
                except BaseException as error:
                    counts = getattr(collector, "evidence_counts", (0, 0))
                    if (
                        type(counts) is not tuple
                        or len(counts) != 2
                        or any(
                            type(value) is not int or value < 0
                            for value in counts
                        )
                    ):
                        counts = (0, 0)
                    ensure_terminal = getattr(
                        evidence, "ensure_halted_terminal", None
                    )
                    if not callable(ensure_terminal):
                        raise ShadowCollectorError(
                            "shadow_evidence_terminal_unavailable"
                        ) from error
                    await asyncio.to_thread(
                        ensure_terminal,
                        code=_code(error),
                        provider_match_id=match_id,
                        market_tickers=tickers,
                        sportradar_captures=counts[0],
                        kalshi_frames=counts[1],
                    )
                    raise


def run_cli(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    dependencies: LiveShadowCliDependencies | None = None,
) -> int:
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr
    services = LiveShadowCliDependencies() if dependencies is None else dependencies
    try:
        match_id, tickers, duration, poll = _arguments(argv)
    except _UsageError:
        _best_effort_write(error_stream, "ERROR: invalid command arguments\n")
        return 2
    try:
        material = services.credential_loader(environ)
        with _signals() as stop:
            result = asyncio.run(
                _run(
                    match_id=match_id,
                    tickers=tickers,
                    duration_seconds=duration,
                    poll_seconds=poll,
                    material=material,
                    output=_DashboardOutput(output_stream),
                    services=services,
                    stop=stop,
                )
            )
        return 130 if result == "operator_interrupt" else 0
    except KeyboardInterrupt:
        _best_effort_write(error_stream, "STOPPED: operator interrupt\n")
        return 130
    except BaseException as error:
        _best_effort_write(error_stream, f"HALTED: {_code(error)}\n")
        return 1


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "LiveShadowCliDependencies",
    "UnqualifiedKalshiProjector",
    "main",
    "run_cli",
)
