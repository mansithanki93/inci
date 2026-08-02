"""Independent read-only Kalshi price evidence collection and display."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from re import ASCII, compile as pattern_compile
from typing import Protocol

from inci_tennis_io.shadow_evidence import (
    PriceOnlyEvidenceObservation,
    PriceOnlySessionEvidence,
    ShadowMarketCandidate,
)
from inci_tennis_runtime.live_shadow_collector import (
    CandidateMarketProjection,
    CandidateMarketView,
    ShadowCollectorError,
    _age,
    _durable_to_thread,
    _durable_to_thread_result,
    _error_code,
    _shielded_task_result,
    _terminal_text,
)


_TICKER_PATTERN = pattern_compile(
    r"[A-Z0-9][A-Z0-9._-]{0,127}\Z", flags=ASCII
)
_TERMINAL_KALSHI_CODES = frozenset({"kalshi_stream_terminal"})
_MAX_RECOVERY_ATTEMPTS = 3
_MODE_LITERAL = (
    "READ ONLY / PRICE ONLY / NO SCORE FEED / NO SIGNALS / NO P&L / NO ORDERS"
)


def _fail(code: str) -> None:
    raise ShadowCollectorError(code)


class _KalshiTransport(Protocol):
    async def open_readonly(self) -> None: ...

    async def subscribe(self) -> object: ...

    async def receive_one(self, timeout_seconds: float) -> object: ...

    async def request_snapshot(self, sid: int) -> object: ...

    async def close(self) -> None: ...


class _MarketProjector(Protocol):
    def begin_subscription(self, receipt: object) -> None: ...

    def apply(self, frame: object) -> CandidateMarketProjection: ...

    def snapshot_requested(self, receipt: object) -> None: ...

    def disconnect(self, generation: int | None) -> None: ...


class _EvidenceStore(Protocol):
    def append_price_only_session(
        self, record: PriceOnlySessionEvidence
    ) -> None: ...

    def persist_kalshi_frame(self, frame: object) -> object: ...

    def append_price_only_observation(
        self, record: PriceOnlyEvidenceObservation
    ) -> None: ...

    def append_price_only_terminal(self, **values: object) -> None: ...


@dataclass(frozen=True, slots=True)
class PriceOnlyDashboardView:
    event_ticker: str
    player_a_name: str
    player_b_name: str
    market_tickers: tuple[str, str]
    market_a_book: str
    market_b_book: str
    kalshi_status: str
    kalshi_generation: int | None
    kalshi_sequence: int | None
    kalshi_age_seconds: float | None
    reason: str
    kalshi_frames: int


def render_price_only_dashboard(view: PriceOnlyDashboardView) -> str:
    """Render one neutral price-only snapshot without trading semantics."""

    if (
        type(view) is not PriceOnlyDashboardView
        or type(view.market_tickers) is not tuple
        or len(view.market_tickers) != 2
    ):
        _fail("shadow_price_only_dashboard_invalid")
    age = (
        "--"
        if view.kalshi_age_seconds is None
        else f"{view.kalshi_age_seconds:.1f}s"
    )
    generation = (
        "--" if view.kalshi_generation is None else str(view.kalshi_generation)
    )
    sequence = "--" if view.kalshi_sequence is None else str(view.kalshi_sequence)
    rows = (
        ("MODE", _MODE_LITERAL),
        ("EVENT", view.event_ticker),
        ("PLAYER A", view.player_a_name),
        ("PLAYER B", view.player_b_name),
        ("TICKER A", view.market_tickers[0]),
        ("MARKET A BOOK", view.market_a_book),
        ("TICKER B", view.market_tickers[1]),
        ("MARKET B BOOK", view.market_b_book),
        ("KALSHI STATUS", view.kalshi_status),
        ("KALSHI GEN / SEQ / AGE", f"{generation} / {sequence} / {age}"),
        ("REASON", view.reason),
        ("FRAMES", str(view.kalshi_frames)),
    )
    width = max(len(label) for label, _ in rows)
    border = "+" + "-" * (width + 2) + "+" + "-" * 82 + "+\n"
    lines = [border]
    for label, value in rows:
        lines.append(
            f"| {_terminal_text(label, 32):<{width}} | "
            f"{_terminal_text(value, 80):<80} |\n"
        )
    lines.append(border)
    return "".join(lines)


class PriceOnlyShadowCollector:
    """Collect exact Kalshi bytes without constructing a score-feed path."""

    __slots__ = (
        "_session",
        "_event_ticker",
        "_tickers",
        "_kalshi",
        "_projector",
        "_evidence",
        "_wall_ns",
        "_monotonic_ns",
        "_pause",
        "_stop_requested",
        "_render",
        "_books",
        "_kalshi_reference",
        "_kalshi_status",
        "_kalshi_generation",
        "_kalshi_sequence",
        "_kalshi_frames",
        "_recovery_attempts",
        "_terminal_reason",
    )

    def __init__(
        self,
        *,
        session_evidence: PriceOnlySessionEvidence,
        kalshi_transport: _KalshiTransport,
        market_projector: _MarketProjector,
        evidence_store: _EvidenceStore,
        wall_ns: Callable[[], int],
        monotonic_ns: Callable[[], int],
        pause: Callable[[float], Awaitable[None]],
        stop_requested: Callable[[], bool],
        render: Callable[[str], None],
    ) -> None:
        session = session_evidence
        transport_methods = (
            "open_readonly",
            "subscribe",
            "receive_one",
            "request_snapshot",
            "close",
        )
        projector_methods = (
            "begin_subscription",
            "apply",
            "snapshot_requested",
            "disconnect",
        )
        evidence_methods = (
            "append_price_only_session",
            "persist_kalshi_frame",
            "append_price_only_observation",
            "append_price_only_terminal",
        )
        if (
            type(session) is not PriceOnlySessionEvidence
            or type(session.event_ticker) is not str
            or _TICKER_PATTERN.fullmatch(session.event_ticker) is None
            or type(session.market_tickers) is not tuple
            or len(session.market_tickers) != 2
            or session.market_tickers[0] == session.market_tickers[1]
            or any(
                type(ticker) is not str
                or _TICKER_PATTERN.fullmatch(ticker) is None
                for ticker in session.market_tickers
            )
            or any(
                type(name) is not str or not name.strip()
                for name in (session.player_a_name, session.player_b_name)
            )
            or any(
                not callable(getattr(kalshi_transport, name, None))
                for name in transport_methods
            )
            or any(
                not callable(getattr(market_projector, name, None))
                for name in projector_methods
            )
            or any(
                not callable(getattr(evidence_store, name, None))
                for name in evidence_methods
            )
            or not all(
                callable(value)
                for value in (
                    wall_ns,
                    monotonic_ns,
                    pause,
                    stop_requested,
                    render,
                )
            )
        ):
            _fail("shadow_price_only_collector_configuration_invalid")
        self._session = session
        self._event_ticker = session.event_ticker
        self._tickers = session.market_tickers
        self._kalshi = kalshi_transport
        self._projector = market_projector
        self._evidence = evidence_store
        self._wall_ns = wall_ns
        self._monotonic_ns = monotonic_ns
        self._pause = pause
        self._stop_requested = stop_requested
        self._render = render
        self._books = self._empty_books()
        self._kalshi_reference: object | None = None
        self._kalshi_status = "waiting"
        self._kalshi_generation: int | None = None
        self._kalshi_sequence: int | None = None
        self._kalshi_frames = 0
        self._recovery_attempts = 0
        self._terminal_reason: str | None = None

    def _empty_books(self) -> dict[str, ShadowMarketCandidate]:
        return {
            ticker: ShadowMarketCandidate(ticker, None, None, None, None)
            for ticker in self._tickers
        }

    def _clock(self) -> tuple[int, int]:
        wall = self._wall_ns()
        monotonic = self._monotonic_ns()
        if (
            type(wall) is not int
            or wall <= 0
            or type(monotonic) is not int
            or monotonic < 0
        ):
            _fail("shadow_clock_invalid")
        return wall, monotonic

    @staticmethod
    def _book_text(value: ShadowMarketCandidate) -> str:
        if value.yes_bid is None and value.yes_ask is None:
            return "--"
        return (
            f"bid {value.yes_bid or '--'} x {value.bid_depth or '--'} | "
            f"ask {value.yes_ask or '--'} x {value.ask_depth or '--'}"
        )

    def _dashboard(self, reason: str, monotonic: int) -> str:
        reference = self._kalshi_reference
        age = (
            None
            if reference is None
            else _age(monotonic, reference.captured_monotonic_ns)
        )
        return render_price_only_dashboard(
            PriceOnlyDashboardView(
                event_ticker=self._event_ticker,
                player_a_name=self._session.player_a_name,
                player_b_name=self._session.player_b_name,
                market_tickers=self._tickers,
                market_a_book=self._book_text(self._books[self._tickers[0]]),
                market_b_book=self._book_text(self._books[self._tickers[1]]),
                kalshi_status=self._kalshi_status,
                kalshi_generation=self._kalshi_generation,
                kalshi_sequence=self._kalshi_sequence,
                kalshi_age_seconds=None if age is None else age / 1_000_000_000,
                reason=reason,
                kalshi_frames=self._kalshi_frames,
            )
        )

    async def _append_observation(self, reason: str) -> None:
        wall, monotonic = self._clock()
        reference = self._kalshi_reference
        generation = (
            None
            if reference is None
            else reference.physical_connection_generation
        )
        sequence = (
            None
            if reference is None
            else self._kalshi_sequence
            if self._kalshi_sequence is not None
            else 0
        )
        await _durable_to_thread(
            self._evidence.append_price_only_observation,
            PriceOnlyEvidenceObservation(
                observed_wall_ns=wall,
                observed_monotonic_ns=monotonic,
                clock_uncertainty_ns=(
                    0 if reference is None else reference.clock_uncertainty_ns
                ),
                event_ticker=self._event_ticker,
                market_tickers=self._tickers,
                kalshi_raw_path=None if reference is None else reference.raw_path,
                kalshi_raw_sha256=(
                    None if reference is None else reference.raw_sha256
                ),
                kalshi_captured_wall_ns=(
                    None if reference is None else reference.captured_wall_ns
                ),
                kalshi_captured_monotonic_ns=(
                    None
                    if reference is None
                    else reference.captured_monotonic_ns
                ),
                kalshi_generation=generation,
                kalshi_sequence=sequence,
                kalshi_age_ns=(
                    None
                    if reference is None
                    else _age(monotonic, reference.captured_monotonic_ns)
                ),
                kalshi_status=self._kalshi_status,
                market_a=self._books[self._tickers[0]],
                market_b=self._books[self._tickers[1]],
                reason=reason,
                kalshi_frames=self._kalshi_frames,
            ),
        )
        await asyncio.to_thread(
            self._render,
            self._dashboard(reason, monotonic),
        )

    async def _reconnect(self) -> None:
        last_error: Exception | None = None
        while self._recovery_attempts < _MAX_RECOVERY_ATTEMPTS:
            delay = float(2**self._recovery_attempts)
            self._recovery_attempts += 1
            await self._pause(delay)
            try:
                await self._kalshi.close()
                await self._kalshi.open_readonly()
                receipt = await self._kalshi.subscribe()
                self._projector.begin_subscription(receipt)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                code = _error_code(error)
                if code in _TERMINAL_KALSHI_CODES:
                    raise ShadowCollectorError(code) from error
                last_error = error
                continue
            self._books = self._empty_books()
            self._kalshi_status = "waiting"
            self._kalshi_generation = None
            self._kalshi_sequence = None
            await self._append_observation("kalshi_reconnected")
            return
        exhausted = ShadowCollectorError("kalshi_recovery_exhausted")
        if last_error is not None:
            raise exhausted from last_error
        raise exhausted

    async def _disconnect_and_reconnect(self) -> None:
        self._books = self._empty_books()
        self._kalshi_status = "disconnected"
        self._projector.disconnect(self._kalshi_generation)
        self._kalshi_generation = None
        self._kalshi_sequence = None
        await self._append_observation("kalshi_stream_disconnected")
        await self._reconnect()

    async def _receive(self, timeout_seconds: float) -> None:
        try:
            frame = await self._kalshi.receive_one(timeout_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = _error_code(error)
            if code == "kalshi_ws_receive_timeout":
                return
            if code in _TERMINAL_KALSHI_CODES:
                raise ShadowCollectorError(code) from error
            await self._disconnect_and_reconnect()
            return

        reference, persistence_cancellation = await _durable_to_thread_result(
            self._evidence.persist_kalshi_frame,
            frame,
        )
        self._kalshi_reference = reference
        self._kalshi_frames += 1
        if persistence_cancellation is not None:
            raise persistence_cancellation
        try:
            projection = self._projector.apply(frame)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = _error_code(error)
            if code in _TERMINAL_KALSHI_CODES:
                raise ShadowCollectorError(code) from error
            self._books = self._empty_books()
            self._kalshi_status = "error"
            self._kalshi_generation = reference.physical_connection_generation
            self._kalshi_sequence = None
            await self._append_observation("kalshi_parser_error")
            if code == "shadow_internal_error":
                _fail(code)
            self._projector.disconnect(self._kalshi_generation)
            self._kalshi_generation = None
            self._kalshi_sequence = None
            await self._reconnect()
            return
        if type(projection) is not CandidateMarketProjection:
            _fail("shadow_projection_invalid")
        if tuple(market.ticker for market in projection.markets) != self._tickers:
            _fail("shadow_projection_wrong_ticker")
        if (
            projection.generation
            != reference.physical_connection_generation
        ):
            _fail("shadow_projection_generation_mismatch")
        self._recovery_attempts = 0
        self._kalshi_status = projection.status
        self._kalshi_generation = reference.physical_connection_generation
        self._kalshi_sequence = projection.sequence
        if projection.status == "candidate":
            self._books = {
                market.ticker: ShadowMarketCandidate(
                    market.ticker,
                    market.yes_bid,
                    market.yes_ask,
                    market.bid_depth,
                    market.ask_depth,
                )
                for market in projection.markets
            }
        else:
            self._books = self._empty_books()
        await self._append_observation(projection.reason)
        if projection.snapshot_needed:
            assert projection.subscription_id is not None
            try:
                receipt = await self._kalshi.request_snapshot(
                    projection.subscription_id
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                code = _error_code(error)
                if code in _TERMINAL_KALSHI_CODES:
                    raise ShadowCollectorError(code) from error
                await self._disconnect_and_reconnect()
                return
            self._projector.snapshot_requested(receipt)

    async def _finalize(
        self,
        *,
        reason: str,
        failure: str | None,
        opened: bool,
    ) -> tuple[str, str | None, Exception | None, Exception | None]:
        close_error: Exception | None = None
        terminal_error: Exception | None = None
        if opened:
            try:
                await self._kalshi.close()
            except Exception as error:
                close_error = error
                if reason not in {"halted", "cancelled"}:
                    reason = "halted"
                    failure = _error_code(error)
        try:
            wall, monotonic = self._clock()
            await _durable_to_thread(
                self._evidence.append_price_only_terminal,
                reason=reason,
                code=failure,
                ended_wall_ns=wall,
                ended_monotonic_ns=monotonic,
                event_ticker=self._event_ticker,
                market_tickers=self._tickers,
                kalshi_frames=self._kalshi_frames,
            )
        except Exception as error:
            terminal_error = error
        self._terminal_reason = reason
        return reason, failure, close_error, terminal_error

    async def run(self, *, duration_seconds: int) -> str:
        if (
            type(duration_seconds) is not int
            or duration_seconds < 10
            or duration_seconds > 3_600
        ):
            _fail("shadow_duration_invalid")
        _, start = self._clock()
        reason: str | None = None
        failure: str | None = None
        processing_error: Exception | None = None
        cancellation: asyncio.CancelledError | None = None
        opened = False
        try:
            _, session_cancellation = await _durable_to_thread_result(
                self._evidence.append_price_only_session,
                self._session,
            )
            if session_cancellation is not None:
                raise session_cancellation
            opened = True
            await self._kalshi.open_readonly()
            receipt = await self._kalshi.subscribe()
            self._projector.begin_subscription(receipt)
            end = start + duration_seconds * 1_000_000_000
            while reason is None:
                if self._stop_requested():
                    reason = "operator_interrupt"
                    break
                _, now = self._clock()
                if now >= end:
                    reason = "duration_elapsed"
                    break
                timeout = min(1.0, (end - now) / 1_000_000_000)
                if timeout <= 0:
                    await self._pause(0)
                    continue
                await self._receive(timeout)
        except asyncio.CancelledError as error:
            cancellation = error
            reason = "cancelled"
            failure = None
        except KeyboardInterrupt:
            reason = "operator_interrupt"
        except Exception as error:
            processing_error = error
            reason = "halted"
            failure = _error_code(error)
        if reason is None:
            reason = "halted"
            failure = "shadow_internal_error"
        cleanup = asyncio.create_task(
            self._finalize(reason=reason, failure=failure, opened=opened)
        )
        result, cleanup_cancellation, cleanup_task_error = (
            await _shielded_task_result(cleanup)
        )
        if cancellation is None:
            cancellation = cleanup_cancellation
        if cleanup_task_error is not None:
            if processing_error is None:
                processing_error = cleanup_task_error
            if failure is None:
                failure = _error_code(cleanup_task_error)
        if (
            type(result) is not tuple
            or len(result) != 4
            or type(result[0]) is not str
            or (result[1] is not None and type(result[1]) is not str)
        ):
            if cancellation is not None:
                raise cancellation
            _fail("shadow_internal_error")
        reason, failure, close_error, terminal_error = result
        cleanup_error = terminal_error or close_error or cleanup_task_error
        if cancellation is not None:
            if cleanup_error is not None:
                raise cancellation from cleanup_error
            raise cancellation
        if processing_error is not None:
            diagnostic = ShadowCollectorError(failure or _error_code(processing_error))
            if cleanup_error is not None:
                raise diagnostic from cleanup_error
            raise diagnostic from processing_error
        if close_error is not None:
            diagnostic = ShadowCollectorError(failure or _error_code(close_error))
            if terminal_error is not None:
                raise diagnostic from terminal_error
            raise diagnostic from close_error
        if terminal_error is not None:
            raise ShadowCollectorError(_error_code(terminal_error)) from terminal_error
        if reason == "halted":
            _fail(failure or "shadow_internal_error")
        return reason


__all__ = (
    "CandidateMarketProjection",
    "CandidateMarketView",
    "PriceOnlyDashboardView",
    "PriceOnlyShadowCollector",
    "ShadowCollectorError",
    "render_price_only_dashboard",
)
