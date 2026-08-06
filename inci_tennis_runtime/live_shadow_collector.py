"""Pure composition for a read-only, unqualified tennis shadow collector."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import copy_context
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import partial
from hashlib import sha256
from re import ASCII, compile as pattern_compile
from typing import Protocol
import unicodedata

import inci_tennis_adapters.sportradar_trial_v3 as sportradar_trial_v3
from inci_tennis_io.facade import (
    ShadowEvidenceObservation,
    ShadowMarketCandidate,
    TrialCapture,
    TrialObservationRecord,
)


_MATCH_PATTERN = pattern_compile(r"sr:sport_event:[1-9][0-9]*\Z", flags=ASCII)
_TICKER_PATTERN = pattern_compile(
    r"[A-Z0-9][A-Z0-9._-]{0,127}\Z", flags=ASCII
)
_SAFE_CODE_PATTERN = pattern_compile(
    r"(?:shadow|kalshi|sportradar)_[a-z0-9_]{1,96}\Z", flags=ASCII
)
_REASON_PATTERN = pattern_compile(
    r"(?:candidate|kalshi)_[a-z0-9_]{1,96}\Z", flags=ASCII
)
_DECIMAL_PATTERN = pattern_compile(
    r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z", flags=ASCII
)
_CANDIDATE_STATUSES = frozenset(
    {
        "candidate",
        "gap",
        "duplicate",
        "out_of_order",
        "error",
        "ignored",
        "incomplete",
        "snapshot_required",
    }
)
_TERMINAL_STATUSES = frozenset({"closed", "cancelled", "abandoned"})
_TERMINAL_KALSHI_CODES = frozenset({"kalshi_stream_terminal"})
_MAX_RECOVERY_ATTEMPTS = 3
_HEARTBEAT_SECONDS = 60
_PROVIDER_FAILOVER_TRANSPORT_CODES = frozenset(
    {
        "sportradar_async_dependency_unavailable",
        "sportradar_body_too_large",
        "sportradar_content_encoding_invalid",
        "sportradar_content_length_invalid",
        "sportradar_content_length_mismatch",
        "sportradar_content_type_invalid",
        "sportradar_http_status_invalid",
        "sportradar_response_body_invalid",
        "sportradar_response_body_unavailable",
        "sportradar_response_headers_invalid",
        "sportradar_total_deadline",
        "sportradar_transport_unavailable",
    }
)
_PROVIDER_FAILOVER_PARSER_CODES = frozenset(
    {
        "sportradar_competitors_invalid",
        "sportradar_duplicate_json_key",
        "sportradar_generated_time_mismatch",
        "sportradar_generated_time_missing",
        "sportradar_live_summaries_schema_unknown",
        "sportradar_live_summary_schema_unknown",
        "sportradar_match_format_unknown",
        "sportradar_match_status_unknown",
        "sportradar_nonfinite_number",
        "sportradar_period_scores_invalid",
        "sportradar_point_state_invalid",
        "sportradar_server_invalid",
        "sportradar_source_stale",
        "sportradar_source_time_ahead",
        "sportradar_status_unknown",
        "sportradar_summary_schema_unknown",
        "sportradar_timeline_before_summary",
        "sportradar_timeline_competitor_invalid",
        "sportradar_timeline_event_unknown",
        "sportradar_timeline_gap",
        "sportradar_timeline_missing",
        "sportradar_timeline_order_invalid",
        "sportradar_timeline_period_invalid",
        "sportradar_timeline_reason_unknown",
        "sportradar_timeline_result_unknown",
        "sportradar_timeline_schema_unknown",
        "sportradar_timeline_server_invalid",
        "sportradar_timeline_unmarked_correction",
        "sportradar_timeline_update_invalid",
        "sportradar_timestamp_invalid",
        "sportradar_wire_contract_invalid",
    }
)
_PROVIDER_HTTP_STATUS_PATTERN = pattern_compile(
    r"sportradar_http_status_[1-5][0-9]{2}\Z", flags=ASCII
)


class ShadowCollectorError(RuntimeError):
    """A fixed shadow diagnostic without raw payload or credential text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ShadowCollectorError(code)


def _provider_failure_allows_price_only(code: object) -> bool:
    return type(code) is str and (
        code in _PROVIDER_FAILOVER_TRANSPORT_CODES
        or code in _PROVIDER_FAILOVER_PARSER_CODES
        or _PROVIDER_HTTP_STATUS_PATTERN.fullmatch(code) is not None
    )


_PROVIDER_ATTESTATION_SEAL = object()
_PROVIDER_ATTESTATION_SCHEMA = "clean-provider-failure-v1"
_PROVIDER_FAILURE_SOURCES = frozenset(
    {"sportradar_fetch", "sportradar_parser"}
)


@dataclass(frozen=True, slots=True)
class _CleanProviderFailureAttestation:
    code: str
    source: str
    schema: str
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _PROVIDER_ATTESTATION_SEAL
            or self.schema != _PROVIDER_ATTESTATION_SCHEMA
            or self.source not in _PROVIDER_FAILURE_SOURCES
            or not _provider_failure_allows_price_only(self.code)
        ):
            raise TypeError("clean provider failure attestation is internal")


class _AttestedProviderFailure(ShadowCollectorError):
    def __init__(self, code: str, source: str, *, _seal: object) -> None:
        if _seal is not _PROVIDER_ATTESTATION_SEAL:
            raise TypeError("attested provider failure is internal")
        super().__init__(code)
        self._attestation = _CleanProviderFailureAttestation(
            code,
            source,
            _PROVIDER_ATTESTATION_SCHEMA,
            _PROVIDER_ATTESTATION_SEAL,
        )


class _ProviderOperationFailure(RuntimeError):
    def __init__(self, code: str, source: str, *, _seal: object) -> None:
        if (
            _seal is not _PROVIDER_ATTESTATION_SEAL
            or source not in _PROVIDER_FAILURE_SOURCES
            or not _provider_failure_allows_price_only(code)
        ):
            raise TypeError("provider operation failure is internal")
        self.code = code
        self.source = source
        super().__init__(code)


def _provider_failure_attestation(
    error: object,
) -> _CleanProviderFailureAttestation | None:
    if type(error) is not _AttestedProviderFailure:
        return None
    value = getattr(error, "_attestation", None)
    return value if type(value) is _CleanProviderFailureAttestation else None


def _provider_failure_attestation_is_valid(
    attestation: object,
    code: object,
) -> bool:
    return (
        type(attestation) is _CleanProviderFailureAttestation
        and attestation._seal is _PROVIDER_ATTESTATION_SEAL
        and attestation.schema == _PROVIDER_ATTESTATION_SCHEMA
        and attestation.source in _PROVIDER_FAILURE_SOURCES
        and type(code) is str
        and attestation.code == code
    )


def _provider_operation_failure(
    error: BaseException,
    source: str,
) -> BaseException:
    code = _error_code(error)
    if not _provider_failure_allows_price_only(code):
        return error
    return _ProviderOperationFailure(
        code,
        source,
        _seal=_PROVIDER_ATTESTATION_SEAL,
    )


class _KalshiTransport(Protocol):
    async def open_readonly(self) -> None: ...

    async def subscribe(self) -> object: ...

    async def receive_one(self, timeout_seconds: float) -> object: ...

    async def request_snapshot(self, sid: int) -> object: ...

    async def close(self) -> None: ...


class _SportradarTransport(Protocol):
    @property
    def completed_captures(self) -> int: ...

    async def fetch_summary(self, match_id: str) -> TrialCapture: ...

    async def fetch_timeline(self, match_id: str) -> TrialCapture: ...


class _SportradarLedger(Protocol):
    def record_observation(self, record: TrialObservationRecord) -> None: ...

    def record_parser_failure(
        self, *, command: str, reservation: object, code: str
    ) -> None: ...

    def record_session_terminal(self, **values: object) -> None: ...


class _EvidenceStore(Protocol):
    def persist_kalshi_frame(self, frame: object) -> object: ...

    def append_observation(self, record: ShadowEvidenceObservation) -> None: ...

    def append_terminal(self, **values: object) -> None: ...

    def ensure_halted_terminal(self, **values: object) -> None: ...


class LivePaperCaptureObserver(Protocol):
    """Post-commit raw capture hook for an additive paper-only consumer."""

    async def after_provider_commit(
        self,
        *,
        capture: TrialCapture,
        durable_receipt: object,
        captured_wall_ns: int,
        captured_monotonic_ns: int,
        clock_uncertainty_ns: int,
    ) -> None: ...

    async def after_kalshi_commit(
        self,
        *,
        frame: object,
        durable_receipt: object,
        captured_wall_ns: int,
        captured_monotonic_ns: int,
        clock_uncertainty_ns: int,
    ) -> None: ...

    async def after_heartbeat_commit(
        self,
        *,
        captured_wall_ns: int,
        captured_monotonic_ns: int,
    ) -> None: ...


class _MarketProjector(Protocol):
    def begin_subscription(self, receipt: object) -> None: ...

    def apply(self, frame: object) -> CandidateMarketProjection: ...

    def snapshot_requested(self, receipt: object) -> None: ...

    def disconnect(self, generation: int | None) -> None: ...


@dataclass(frozen=True, slots=True)
class CandidateMarketView:
    ticker: str
    yes_bid: str | None
    yes_ask: str | None
    bid_depth: str | None
    ask_depth: str | None

    def __post_init__(self) -> None:
        values = (self.yes_bid, self.yes_ask, self.bid_depth, self.ask_depth)
        if (
            type(self.ticker) is not str
            or _TICKER_PATTERN.fullmatch(self.ticker) is None
            or any(value is not None and type(value) is not str for value in values)
            or (self.yes_bid is None) != (self.bid_depth is None)
            or (self.yes_ask is None) != (self.ask_depth is None)
        ):
            _fail("shadow_projection_invalid")
        for value, price in zip(values, (True, True, False, False), strict=True):
            if value is not None and not _valid_decimal(value, price=price):
                _fail("shadow_projection_invalid")


@dataclass(frozen=True, slots=True)
class CandidateMarketProjection:
    """One unqualified result from an injected pure Kalshi projector."""

    markets: tuple[CandidateMarketView, CandidateMarketView]
    generation: int | None
    sequence: int | None
    subscription_id: int | None
    status: str
    reason: str
    snapshot_needed: bool

    def __post_init__(self) -> None:
        if (
            self.status not in _CANDIDATE_STATUSES
            or type(self.reason) is not str
            or (
                self.reason != "empty_book"
                and _REASON_PATTERN.fullmatch(self.reason) is None
            )
            or type(self.markets) is not tuple
            or len(self.markets) != 2
            or any(type(value) is not CandidateMarketView for value in self.markets)
            or self.markets[0].ticker == self.markets[1].ticker
            or type(self.snapshot_needed) is not bool
            or any(
                value is not None and (type(value) is not int or value < 0)
                for value in (
                    self.generation,
                    self.sequence,
                    self.subscription_id,
                )
            )
        ):
            _fail("shadow_projection_invalid")
        values = tuple(
            field
            for market in self.markets
            for field in (
                market.yes_bid,
                market.yes_ask,
                market.bid_depth,
                market.ask_depth,
            )
        )
        if self.status == "candidate":
            if any(value is None for value in values) or self.snapshot_needed:
                _fail("shadow_projection_invalid")
        elif any(value is not None for value in values):
            _fail("shadow_projection_invalid")
        if self.snapshot_needed and self.subscription_id is None:
            _fail("shadow_projection_invalid")


def _valid_decimal(value: object, *, price: bool) -> bool:
    if (
        type(value) is not str
        or _DECIMAL_PATTERN.fullmatch(value) is None
    ):
        return False
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return False
    return Decimal("0") <= parsed <= Decimal("1") if price else parsed > 0


@dataclass(frozen=True, slots=True)
class ShadowDashboardView:
    provider_match_id: str
    players: str
    score: str
    server: str
    sportradar_age_seconds: float | None
    market_tickers: tuple[str, str]
    home_book: str
    away_book: str
    kalshi_status: str
    kalshi_generation: int | None
    kalshi_sequence: int | None
    kalshi_age_seconds: float | None
    last_event: str
    reason: str
    sportradar_captures: int
    kalshi_frames: int
    mapping_mode: str = "operator_supplied"
    elapsed_seconds: float = 0.0


def _terminal_text(value: object, maximum: int = 128) -> str:
    raw = str(value)
    safe = "".join(
        " "
        if character in "\r\n\t"
        else "?"
        if unicodedata.category(character).startswith("C")
        else character
        for character in raw
    )
    return " ".join(safe.split())[:maximum]


def render_shadow_dashboard(view: ShadowDashboardView) -> str:
    """Render one dependency-free, explicitly non-trading snapshot."""

    if view.mapping_mode == "auto_matched":
        mode = (
            "READ ONLY / VERIFIED SOURCE LINK / UNQUALIFIED / "
            "NO SIGNALS / NO P&L / NO ORDERS"
        )
        mapping = "VERIFIED SOURCE LINK / UNQUALIFIED"
    elif view.mapping_mode == "operator_supplied":
        mode = "READ ONLY / UNQUALIFIED / NO SIGNALS / NO ORDERS"
        mapping = "OPERATOR-SUPPLIED / UNVERIFIED"
    else:
        _fail("shadow_mapping_mode_invalid")
    age_sr = "--" if view.sportradar_age_seconds is None else f"{view.sportradar_age_seconds:.1f}s"
    age_kalshi = "--" if view.kalshi_age_seconds is None else f"{view.kalshi_age_seconds:.1f}s"
    generation = "--" if view.kalshi_generation is None else str(view.kalshi_generation)
    sequence = "--" if view.kalshi_sequence is None else str(view.kalshi_sequence)
    rows = (
        ("MODE", mode),
        ("ELAPSED", f"{view.elapsed_seconds:.1f}s"),
        ("TICKER MAPPING", mapping),
        ("MATCH", view.provider_match_id),
        ("PLAYERS", view.players),
        ("SCORE", view.score),
        ("SERVER", view.server),
        ("SPORTRADAR AGE", age_sr),
        ("HOME TICKER", view.market_tickers[0]),
        ("HOME CANDIDATE BOOK", view.home_book),
        ("AWAY TICKER", view.market_tickers[1]),
        ("AWAY CANDIDATE BOOK", view.away_book),
        ("KALSHI STATUS", view.kalshi_status),
        ("KALSHI GEN / SEQ / AGE", f"{generation} / {sequence} / {age_kalshi}"),
        ("LAST EVENT", view.last_event),
        ("REASON", view.reason),
        (
            "CAPTURES",
            f"Sportradar {view.sportradar_captures} | Kalshi {view.kalshi_frames}",
        ),
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


def _error_code(error: BaseException) -> str:
    value = getattr(error, "code", None)
    if (
        type(value) is str
        and _SAFE_CODE_PATTERN.fullmatch(value) is not None
    ):
        return value
    return "shadow_internal_error"


def _age(now_ns: int, then_ns: int) -> int | None:
    difference = now_ns - then_ns
    return difference if difference >= 0 else None


async def _durable_to_thread(
    operation: Callable[..., object],
    /,
    *args: object,
    **kwargs: object,
) -> object:
    """Finish one durability operation even if its caller is cancelled."""

    result, cancellation = await _durable_to_thread_result(
        operation,
        *args,
        **kwargs,
    )
    if cancellation is not None:
        raise cancellation
    return result


async def _durable_to_thread_result(
    operation: Callable[..., object],
    /,
    *args: object,
    **kwargs: object,
) -> tuple[object, asyncio.CancelledError | None]:
    """Return a durable result before propagating retained cancellation."""

    loop = asyncio.get_running_loop()
    completed = asyncio.Event()
    outcome: list[tuple[object | None, BaseException | None]] = []
    call = partial(operation, *args, **kwargs)
    worker = loop.run_in_executor(
        None,
        partial(copy_context().run, call),
    )

    def retain_outcome(future: asyncio.Future[object]) -> None:
        try:
            outcome.append((future.result(), None))
        except BaseException as error:
            outcome.append((None, error))
        completed.set()

    worker.add_done_callback(retain_outcome)
    cancellation: asyncio.CancelledError | None = None
    while not completed.is_set():
        try:
            await completed.wait()
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
    result, error = outcome[0]
    if error is not None:
        if cancellation is not None:
            raise cancellation from error
        raise error
    if cancellation is not None:
        return result, cancellation
    return result, None


async def _shielded_task_result(
    task: asyncio.Task[object],
) -> tuple[object | None, asyncio.CancelledError | None, Exception | None]:
    """Await a cleanup task to completion while retaining caller cancellation."""

    completed = asyncio.Event()
    outcome: list[tuple[object | None, BaseException | None]] = []

    def retain_outcome(future: asyncio.Future[object]) -> None:
        try:
            outcome.append((future.result(), None))
        except BaseException as error:
            outcome.append((None, error))
        completed.set()

    task.add_done_callback(retain_outcome)
    cancellation: asyncio.CancelledError | None = None
    while not completed.is_set():
        try:
            await completed.wait()
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
    result, error = outcome[0]
    if isinstance(error, asyncio.CancelledError):
        return None, cancellation or error, None
    if isinstance(error, Exception):
        return None, cancellation, error
    if error is not None:
        raise error
    return result, cancellation, None


def _terminal_status(
    snapshot: sportradar_trial_v3.SportradarScoreSnapshot,
) -> str | None:
    if snapshot.status in _TERMINAL_STATUSES:
        return snapshot.status
    if snapshot.match_status in _TERMINAL_STATUSES:
        return snapshot.match_status
    return None


class LiveShadowCollector:
    """Join read-only raw evidence without creating trusted decisions."""

    def __init__(
        self,
        *,
        provider_match_id: str,
        market_tickers: tuple[str, str],
        sportradar_transport: _SportradarTransport,
        sportradar_ledger: _SportradarLedger,
        kalshi_transport: _KalshiTransport,
        market_projector: _MarketProjector,
        evidence_store: _EvidenceStore,
        wall_ns: Callable[[], int],
        monotonic_ns: Callable[[], int],
        pause: Callable[[float], Awaitable[None]],
        stop_requested: Callable[[], bool],
        render: Callable[[str], None],
        mapping_mode: str = "operator_supplied",
        capture_observer: LivePaperCaptureObserver | None = None,
    ) -> None:
        if (
            type(provider_match_id) is not str
            or _MATCH_PATTERN.fullmatch(provider_match_id) is None
            or type(market_tickers) is not tuple
            or len(market_tickers) != 2
            or market_tickers[0] == market_tickers[1]
            or any(
                type(item) is not str
                or _TICKER_PATTERN.fullmatch(item) is None
                for item in market_tickers
            )
            or not all(
                callable(item)
                for item in (
                    wall_ns,
                    monotonic_ns,
                    pause,
                    stop_requested,
                    render,
                )
            )
            or mapping_mode not in {"operator_supplied", "auto_matched"}
            or (
                capture_observer is not None
                and any(
                    not callable(getattr(capture_observer, name, None))
                    for name in (
                        "after_provider_commit",
                        "after_kalshi_commit",
                    )
                )
            )
            or any(
                not callable(getattr(market_projector, name, None))
                for name in (
                    "begin_subscription",
                    "apply",
                    "snapshot_requested",
                    "disconnect",
                )
            )
        ):
            _fail("shadow_collector_configuration_invalid")
        self._provider_match_id = provider_match_id
        self._tickers = market_tickers
        self._sportradar = sportradar_transport
        self._trial_ledger = sportradar_ledger
        self._kalshi = kalshi_transport
        self._projector = market_projector
        self._evidence = evidence_store
        self._wall_ns = wall_ns
        self._monotonic_ns = monotonic_ns
        self._pause = pause
        self._stop_requested = stop_requested
        self._render = render
        self._mapping_mode = mapping_mode
        self._capture_observer = capture_observer
        self._score: sportradar_trial_v3.SportradarScoreSnapshot | None = None
        self._timeline: (
            sportradar_trial_v3.SportradarTimelineSnapshot | None
        ) = None
        self._provider_capture: TrialCapture | None = None
        self._kalshi_reference: object | None = None
        self._books = {
            ticker: ShadowMarketCandidate(ticker, None, None, None, None)
            for ticker in market_tickers
        }
        self._kalshi_status = "waiting"
        self._kalshi_generation: int | None = None
        self._kalshi_sequence: int | None = None
        self._progression = "initial"
        self._last_event_id: int | None = None
        self._last_event_type: str | None = None
        self._last_event_result: str | None = None
        self._sportradar_captures = 0
        self._kalshi_frames = 0
        self._provider_clock_uncertainty_ns = 0
        self._provider_clock_bracket: tuple[int, int, int, int] | None = None
        self._recovery_attempts = 0
        self._run_started_monotonic_ns: int | None = None
        self._next_heartbeat_monotonic_ns: int | None = None

    @property
    def evidence_counts(self) -> tuple[int, int]:
        """Counts used only to close an emergency evidence terminal."""

        return self._sportradar_captures, self._kalshi_frames

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

    async def _record_trial_observation(
        self,
        capture: TrialCapture,
        score: sportradar_trial_v3.SportradarScoreSnapshot,
        progression: str,
    ) -> object:
        record = TrialObservationRecord(
            command="shadow",
            reservation=capture.reservation,
            provider_match_id=score.provider_match_id,
            generated_wall_ns=score.generated_wall_ns,
            captured_wall_ns=capture.captured_wall_ns,
            status=score.status,
            match_status=score.match_status,
            payload_sha256=sha256(capture.payload).hexdigest(),
            raw_path=capture.raw_path,
            progression=progression,
            last_event_id=self._last_event_id,
            terminal_reason=_terminal_status(score),
        )
        durable_receipt, cancellation = await _durable_to_thread_result(
            self._trial_ledger.record_observation,
            record,
        )
        if cancellation is not None:
            raise cancellation
        return record if durable_receipt is None else durable_receipt

    async def _capture_summary(self) -> None:
        started_wall, started_monotonic = self._clock()
        try:
            capture = await self._sportradar.fetch_summary(
                self._provider_match_id
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as error:
            marked = _provider_operation_failure(
                error, "sportradar_fetch"
            )
            if marked is error:
                raise
            raise marked from error
        self._sportradar_captures += 1
        completed_wall, completed_monotonic = self._clock()
        if completed_monotonic < started_monotonic:
            _fail("shadow_clock_invalid")
        self._provider_clock_uncertainty_ns = (
            completed_monotonic - started_monotonic
        )
        self._provider_clock_bracket = (
            started_wall,
            started_monotonic,
            completed_wall,
            completed_monotonic,
        )
        try:
            score = sportradar_trial_v3.parse_sport_event_summary(
                capture.payload,
                expected_match_id=self._provider_match_id,
            )
        except sportradar_trial_v3.SportradarWireContractError as error:
            await _durable_to_thread(
                self._trial_ledger.record_parser_failure,
                command="shadow",
                reservation=capture.reservation,
                code=error.code,
            )
            marked = _provider_operation_failure(
                error, "sportradar_parser"
            )
            if marked is error:
                raise
            raise marked from error
        self._score = score
        self._provider_capture = capture
        self._progression = "initial"
        durable_receipt = await self._record_trial_observation(
            capture, score, self._progression
        )
        if self._capture_observer is not None:
            await self._capture_observer.after_provider_commit(
                capture=capture,
                durable_receipt=durable_receipt,
                captured_wall_ns=capture.captured_wall_ns,
                captured_monotonic_ns=completed_monotonic,
                clock_uncertainty_ns=self._provider_clock_uncertainty_ns,
            )

    async def _capture_timeline(self) -> None:
        started_wall, started_monotonic = self._clock()
        try:
            capture = await self._sportradar.fetch_timeline(
                self._provider_match_id
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as error:
            marked = _provider_operation_failure(
                error, "sportradar_fetch"
            )
            if marked is error:
                raise
            raise marked from error
        self._sportradar_captures += 1
        completed_wall, completed_monotonic = self._clock()
        if completed_monotonic < started_monotonic:
            _fail("shadow_clock_invalid")
        self._provider_clock_uncertainty_ns = (
            completed_monotonic - started_monotonic
        )
        self._provider_clock_bracket = (
            started_wall,
            started_monotonic,
            completed_wall,
            completed_monotonic,
        )
        try:
            timeline = sportradar_trial_v3.parse_sport_event_timeline(
                capture.payload,
                expected_match_id=self._provider_match_id,
            )
            if self._timeline is None:
                if self._score is None:
                    _fail("shadow_provider_state_invalid")
                sportradar_trial_v3.validate_timeline_after_summary(
                    self._score, timeline
                )
                progression = "initial_timeline"
            else:
                progression = sportradar_trial_v3.validate_timeline_progression(
                    self._timeline,
                    timeline,
                )
        except sportradar_trial_v3.SportradarWireContractError as error:
            await _durable_to_thread(
                self._trial_ledger.record_parser_failure,
                command="shadow",
                reservation=capture.reservation,
                code=error.code,
            )
            marked = _provider_operation_failure(
                error, "sportradar_parser"
            )
            if marked is error:
                raise
            raise marked from error
        self._timeline = timeline
        self._score = timeline.score
        self._provider_capture = capture
        self._progression = progression
        last = timeline.events[-1] if timeline.events else None
        self._last_event_id = None if last is None else last.event_id
        self._last_event_type = None if last is None else last.event_type
        self._last_event_result = None if last is None else last.result
        durable_receipt = await self._record_trial_observation(
            capture, timeline.score, progression
        )
        if self._capture_observer is not None:
            await self._capture_observer.after_provider_commit(
                capture=capture,
                durable_receipt=durable_receipt,
                captured_wall_ns=capture.captured_wall_ns,
                captured_monotonic_ns=completed_monotonic,
                clock_uncertainty_ns=self._provider_clock_uncertainty_ns,
            )

    async def _append_observation(self, reason: str) -> None:
        score = self._score
        capture = self._provider_capture
        if score is None or capture is None:
            _fail("shadow_provider_state_invalid")
        bracket = self._provider_clock_bracket
        if bracket is None:
            _fail("shadow_provider_state_invalid")
        wall, monotonic = self._clock()
        reference = self._kalshi_reference
        kalshi_monotonic = (
            None
            if reference is None
            else reference.captured_monotonic_ns
        )
        uncertainty = max(
            self._provider_clock_uncertainty_ns,
            0 if reference is None else reference.clock_uncertainty_ns,
        )
        await _durable_to_thread(
            self._evidence.append_observation,
            ShadowEvidenceObservation(
                observed_wall_ns=wall,
                observed_monotonic_ns=monotonic,
                clock_uncertainty_ns=uncertainty,
                provider_match_id=self._provider_match_id,
                market_tickers=self._tickers,
                provider_generated_wall_ns=score.generated_wall_ns,
                provider_captured_wall_ns=capture.captured_wall_ns,
                provider_request_started_wall_ns=bracket[0],
                provider_request_started_monotonic_ns=bracket[1],
                provider_request_completed_wall_ns=bracket[2],
                provider_request_completed_monotonic_ns=bracket[3],
                provider_clock_uncertainty_ns=(
                    self._provider_clock_uncertainty_ns
                ),
                provider_raw_path=str(capture.raw_path),
                provider_raw_sha256=sha256(capture.payload).hexdigest(),
                home_player_name=score.home_name,
                away_player_name=score.away_name,
                match_status=score.match_status,
                sets=(score.sets_home, score.sets_away),
                games=(score.games_home, score.games_away),
                points=(score.points_home, score.points_away),
                server=score.serving,
                sportradar_age_ns=_age(wall, score.generated_wall_ns),
                progression=self._progression,
                last_event_id=self._last_event_id,
                last_event_type=self._last_event_type,
                last_event_result=self._last_event_result,
                kalshi_raw_path=None if reference is None else reference.raw_path,
                kalshi_raw_sha256=(
                    None if reference is None else reference.raw_sha256
                ),
                kalshi_captured_wall_ns=(
                    None if reference is None else reference.captured_wall_ns
                ),
                kalshi_captured_monotonic_ns=kalshi_monotonic,
                kalshi_generation=self._kalshi_generation,
                kalshi_sequence=self._kalshi_sequence,
                kalshi_age_ns=(
                    None
                    if kalshi_monotonic is None
                    else _age(monotonic, kalshi_monotonic)
                ),
                kalshi_status=self._kalshi_status,
                home_market=self._books[self._tickers[0]],
                away_market=self._books[self._tickers[1]],
                reason=reason,
                sportradar_captures=self._sportradar_captures,
                kalshi_frames=self._kalshi_frames,
            )
        )
        await asyncio.to_thread(
            self._render,
            self._dashboard(reason, wall, monotonic),
        )

    def _dashboard(self, reason: str, wall: int, monotonic: int) -> str:
        score = self._score
        if score is None:
            _fail("shadow_provider_state_invalid")
        sets = f"{score.sets_home if score.sets_home is not None else '--'}-{score.sets_away if score.sets_away is not None else '--'}"
        games = f"{score.games_home if score.games_home is not None else '--'}-{score.games_away if score.games_away is not None else '--'}"
        points = f"{score.points_home}-{score.points_away}"
        provider_age = _age(wall, score.generated_wall_ns)
        reference = self._kalshi_reference
        kalshi_age = (
            None
            if reference is None
            else _age(monotonic, reference.captured_monotonic_ns)
        )
        last_event = (
            "--"
            if self._last_event_id is None
            else f"{self._last_event_id} {self._last_event_type or '--'}"
            + (
                f" ({self._last_event_result})"
                if self._last_event_result is not None
                else ""
            )
        )
        return render_shadow_dashboard(
            ShadowDashboardView(
                provider_match_id=self._provider_match_id,
                players=f"{score.home_name} vs {score.away_name}",
                score=f"sets {sets} | games {games} | points {points}",
                server=score.serving or "--",
                sportradar_age_seconds=(
                    None if provider_age is None else provider_age / 1_000_000_000
                ),
                market_tickers=self._tickers,
                home_book=self._book_text(self._books[self._tickers[0]]),
                away_book=self._book_text(self._books[self._tickers[1]]),
                kalshi_status=self._kalshi_status,
                kalshi_generation=self._kalshi_generation,
                kalshi_sequence=self._kalshi_sequence,
                kalshi_age_seconds=(
                    None if kalshi_age is None else kalshi_age / 1_000_000_000
                ),
                last_event=last_event,
                reason=reason,
                sportradar_captures=self._sportradar_captures,
                kalshi_frames=self._kalshi_frames,
                mapping_mode=self._mapping_mode,
                elapsed_seconds=(
                    0.0
                    if self._run_started_monotonic_ns is None
                    else (monotonic - self._run_started_monotonic_ns)
                    / 1_000_000_000
                ),
            )
        )

    def _waiting_dashboard(self, reason: str, monotonic: int) -> str:
        reference = self._kalshi_reference
        kalshi_age = (
            None
            if reference is None
            else _age(monotonic, reference.captured_monotonic_ns)
        )
        return render_shadow_dashboard(
            ShadowDashboardView(
                provider_match_id=self._provider_match_id,
                players="--",
                score="--",
                server="--",
                sportradar_age_seconds=None,
                market_tickers=self._tickers,
                home_book=self._book_text(self._books[self._tickers[0]]),
                away_book=self._book_text(self._books[self._tickers[1]]),
                kalshi_status=self._kalshi_status,
                kalshi_generation=self._kalshi_generation,
                kalshi_sequence=self._kalshi_sequence,
                kalshi_age_seconds=(
                    None if kalshi_age is None else kalshi_age / 1_000_000_000
                ),
                last_event="--",
                reason=reason,
                sportradar_captures=self._sportradar_captures,
                kalshi_frames=self._kalshi_frames,
                mapping_mode=self._mapping_mode,
                elapsed_seconds=(
                    0.0
                    if self._run_started_monotonic_ns is None
                    else (monotonic - self._run_started_monotonic_ns)
                    / 1_000_000_000
                ),
            )
        )

    @staticmethod
    def _book_text(value: ShadowMarketCandidate) -> str:
        if value.yes_bid is None and value.yes_ask is None:
            return "--"
        return (
            f"bid {value.yes_bid or '--'} x {value.bid_depth or '--'} | "
            f"ask {value.yes_ask or '--'} x {value.ask_depth or '--'}"
        )

    async def _receive(self, timeout_seconds: float) -> None:
        try:
            frame = await self._kalshi.receive_one(timeout_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = _error_code(error)
            if code == "kalshi_ws_receive_timeout":
                await self._emit_timeout_heartbeat_if_due()
                return
            if code in _TERMINAL_KALSHI_CODES:
                raise ShadowCollectorError(code) from error
            self._books = {
                ticker: ShadowMarketCandidate(ticker, None, None, None, None)
                for ticker in self._tickers
            }
            self._kalshi_status = "disconnected"
            self._projector.disconnect(self._kalshi_generation)
            self._kalshi_generation = None
            self._kalshi_sequence = None
            if self._score is not None:
                await self._append_observation("kalshi_stream_disconnected")
            await self._reconnect()
            return
        reference, persistence_cancellation = await _durable_to_thread_result(
            self._evidence.persist_kalshi_frame,
            frame,
        )
        self._kalshi_reference = reference
        self._kalshi_frames += 1
        if persistence_cancellation is not None:
            raise persistence_cancellation
        if self._capture_observer is not None:
            await self._capture_observer.after_kalshi_commit(
                frame=frame,
                durable_receipt=reference,
                captured_wall_ns=getattr(frame, "captured_wall_ns", 0),
                captured_monotonic_ns=getattr(
                    frame, "captured_monotonic_ns", 0
                ),
                clock_uncertainty_ns=getattr(
                    frame, "clock_uncertainty_ns", 0
                ),
            )
        try:
            projection = self._projector.apply(frame)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = _error_code(error)
            if code in _TERMINAL_KALSHI_CODES:
                raise ShadowCollectorError(code) from error
            self._books = {
                ticker: ShadowMarketCandidate(ticker, None, None, None, None)
                for ticker in self._tickers
            }
            self._kalshi_status = "error"
            self._kalshi_generation = reference.physical_connection_generation
            self._kalshi_sequence = None
            if self._score is not None:
                await self._append_observation("kalshi_parser_error")
            if code == "shadow_internal_error":
                _fail(code)
            self._projector.disconnect(self._kalshi_generation)
            await self._reconnect()
            return
        if type(projection) is not CandidateMarketProjection:
            _fail("shadow_projection_invalid")
        if tuple(value.ticker for value in projection.markets) != self._tickers:
            _fail("shadow_projection_wrong_ticker")
        self._recovery_attempts = 0
        self._kalshi_status = projection.status
        self._kalshi_generation = projection.generation
        self._kalshi_sequence = projection.sequence
        if projection.status == "candidate":
            self._books = {
                value.ticker: ShadowMarketCandidate(
                    value.ticker,
                    value.yes_bid,
                    value.yes_ask,
                    value.bid_depth,
                    value.ask_depth,
                )
                for value in projection.markets
            }
        else:
            self._books = {
                ticker: ShadowMarketCandidate(ticker, None, None, None, None)
                for ticker in self._tickers
            }
        if projection.snapshot_needed:
            assert projection.subscription_id is not None
            receipt = await self._kalshi.request_snapshot(
                projection.subscription_id
            )
            self._projector.snapshot_requested(receipt)
        if self._score is not None:
            await self._append_observation(projection.reason)
        await self._emit_timeout_heartbeat_if_due()

    async def _emit_timeout_heartbeat_if_due(self) -> None:
        next_heartbeat = self._next_heartbeat_monotonic_ns
        if next_heartbeat is None:
            return
        wall, now = self._clock()
        if now < next_heartbeat:
            return
        heartbeat_ns = _HEARTBEAT_SECONDS * 1_000_000_000
        while next_heartbeat <= now:
            next_heartbeat += heartbeat_ns
        self._next_heartbeat_monotonic_ns = next_heartbeat
        if self._score is None:
            await asyncio.to_thread(
                self._render,
                self._waiting_dashboard("kalshi_receive_timeout_heartbeat", now),
            )
            return
        else:
            await self._append_observation("kalshi_receive_timeout_heartbeat")
        callback = (
            None
            if self._capture_observer is None
            else getattr(
                self._capture_observer,
                "after_heartbeat_commit",
                None,
            )
        )
        if callable(callback):
            await callback(
                captured_wall_ns=wall,
                captured_monotonic_ns=now,
            )

    async def _reconnect(self) -> None:
        last_error: Exception | None = None
        while self._recovery_attempts < _MAX_RECOVERY_ATTEMPTS:
            delay = float(2 ** self._recovery_attempts)
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
                if _error_code(error) in _TERMINAL_KALSHI_CODES:
                    raise
                last_error = error
                continue
            self._kalshi_status = "waiting"
            self._kalshi_generation = None
            self._kalshi_sequence = None
            if self._score is not None:
                await self._append_observation("kalshi_reconnected")
            return
        exhausted = ShadowCollectorError("kalshi_recovery_exhausted")
        if last_error is not None:
            raise exhausted from last_error
        raise exhausted

    async def _capture_provider_while_receiving(
        self,
        capture: Awaitable[None],
    ) -> None:
        task = asyncio.create_task(capture)
        try:
            await self._pause(0)
            while not task.done():
                if self._stop_requested():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        if not task.cancelled():
                            raise
                    raise KeyboardInterrupt
                await self._receive(0.25)
                await self._pause(0)
            await task
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    if not task.cancelled():
                        raise
            raise
        except Exception:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    if not task.cancelled():
                        raise
            raise

    async def _finalize(
        self,
        *,
        reason: str,
        failure: str | None,
        opened: bool,
    ) -> tuple[str, str | None, bool]:
        completed_captures = getattr(
            self._sportradar,
            "completed_captures",
            self._sportradar_captures,
        )
        if (
            type(completed_captures) is not int
            or completed_captures < self._sportradar_captures
        ):
            _fail("shadow_provider_state_invalid")
        self._sportradar_captures = completed_captures
        first_error: Exception | None = None
        cleanup_clean = True
        primary_halt = reason == "halted" and failure is not None
        if opened:
            try:
                await self._kalshi.close()
            except Exception as error:
                cleanup_clean = False
                if not primary_halt:
                    first_error = error
                if reason != "cancelled" and not primary_halt:
                    reason = "halted"
                    failure = _error_code(error)
        if reason is None:
            reason = "halted"
            failure = "shadow_internal_error"
        wall, monotonic = self._clock()
        try:
            await _durable_to_thread(
                self._evidence.append_terminal,
                reason=reason,
                code=failure,
                ended_wall_ns=wall,
                ended_monotonic_ns=monotonic,
                provider_match_id=self._provider_match_id,
                market_tickers=self._tickers,
                sportradar_captures=self._sportradar_captures,
                kalshi_frames=self._kalshi_frames,
            )
        except Exception as error:
            if first_error is None:
                first_error = error
        terminal_values: dict[str, object] = {
            "command": "shadow",
            "provider_match_id": self._provider_match_id,
            "reason": reason,
        }
        if reason == "halted":
            terminal_values["code"] = "sportradar_shadow_collector_halted"
        elif reason == "operator_interrupt":
            terminal_values["code"] = "sportradar_operator_interrupt"
        elif reason == "cancelled":
            terminal_values["code"] = "sportradar_shadow_task_cancelled"
        try:
            await _durable_to_thread(
                self._trial_ledger.record_session_terminal,
                **terminal_values,
            )
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error
        return reason, failure, cleanup_clean

    async def run(self, *, duration_seconds: int, poll_seconds: int) -> str:
        if (
            type(duration_seconds) is not int
            or duration_seconds < 10
            or duration_seconds > 3_600
            or type(poll_seconds) is not int
            or poll_seconds < 1
            or poll_seconds > duration_seconds
        ):
            _fail("shadow_duration_invalid")
        _, start = self._clock()
        self._run_started_monotonic_ns = start
        self._next_heartbeat_monotonic_ns = (
            start + _HEARTBEAT_SECONDS * 1_000_000_000
        )
        reason: str | None = None
        failure: str | None = None
        provider_failure_source: str | None = None
        cancellation: asyncio.CancelledError | None = None
        opened = False
        try:
            opened = True
            await self._kalshi.open_readonly()
            receipt = await self._kalshi.subscribe()
            self._projector.begin_subscription(receipt)
            await self._capture_provider_while_receiving(
                self._capture_summary()
            )
            await self._append_observation("provider_summary_captured")
            if self._stop_requested():
                reason = "operator_interrupt"
            terminal = (
                _terminal_status(self._score) if self._score is not None else None
            )
            if reason is None and terminal is not None:
                reason = terminal
            else:
                end = start + duration_seconds * 1_000_000_000
                next_provider = start + poll_seconds * 1_000_000_000
                while reason is None:
                    if self._stop_requested():
                        reason = "operator_interrupt"
                        break
                    _, now = self._clock()
                    if now >= end:
                        reason = "duration_elapsed"
                        break
                    if now >= next_provider:
                        await self._capture_provider_while_receiving(
                            self._capture_timeline()
                        )
                        await self._append_observation(
                            "provider_timeline_captured"
                        )
                        terminal = (
                            _terminal_status(self._score)
                            if self._score is not None
                            else None
                        )
                        if terminal is not None:
                            reason = terminal
                            break
                        next_provider += poll_seconds * 1_000_000_000
                        continue
                    timeout = min(
                        1.0,
                        (end - now) / 1_000_000_000,
                        (next_provider - now) / 1_000_000_000,
                    )
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
        except _ProviderOperationFailure as error:
            failure = error.code
            provider_failure_source = error.source
            reason = "halted"
        except Exception as error:
            failure = _error_code(error)
            reason = "halted"
        if reason is None:
            reason = "halted"
            failure = "shadow_internal_error"
        cleanup = asyncio.create_task(
            self._finalize(reason=reason, failure=failure, opened=opened)
        )
        result, cleanup_cancellation, cleanup_error = (
            await _shielded_task_result(cleanup)
        )
        if cancellation is None:
            cancellation = cleanup_cancellation
        if cancellation is not None:
            if cleanup_error is not None:
                raise cancellation from cleanup_error
            raise cancellation
        if cleanup_error is not None:
            raise ShadowCollectorError(_error_code(cleanup_error)) from cleanup_error
        if (
            type(result) is not tuple
            or len(result) != 3
            or type(result[0]) is not str
            or (result[1] is not None and type(result[1]) is not str)
            or type(result[2]) is not bool
        ):
            _fail("shadow_internal_error")
        reason, failure, cleanup_clean = result
        if reason == "halted":
            code = failure or "shadow_internal_error"
            if (
                cleanup_clean
                and provider_failure_source is not None
                and _provider_failure_allows_price_only(code)
            ):
                raise _AttestedProviderFailure(
                    code,
                    provider_failure_source,
                    _seal=_PROVIDER_ATTESTATION_SEAL,
                )
            raise ShadowCollectorError(code)
        return reason


__all__ = (
    "CandidateMarketProjection",
    "CandidateMarketView",
    "LiveShadowCollector",
    "ShadowCollectorError",
    "ShadowDashboardView",
    "render_shadow_dashboard",
)
