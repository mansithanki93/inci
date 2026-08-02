"""Fail-closed CLI composition for the unqualified live tennis shadow."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import os
from re import compile as pattern_compile
import signal
import sys
from typing import Iterator, TextIO

from inci_tennis_io.facade import (
    KalshiReadOnlyCredentials,
    KalshiReadOnlyTransport,
    PriceOnlySessionEvidence,
    ShadowEvidenceStore,
    ShadowMarketCandidate,
    ShadowResolutionEvidence,
    SportradarShadowAsyncTransport,
    TrialObservationRecord,
    TrialUsageLedger,
    default_shadow_state_root,
    load_kalshi_only_credential_material,
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
    _durable_to_thread,
    _provider_failure_allows_price_only,
)
from inci_tennis_runtime.live_price_only_collector import (
    PriceOnlyShadowCollector,
)
from inci_tennis_adapters.shadow_match_chooser import (
    resolve_hybrid_shadow_matches,
)
from inci_tennis_adapters.shadow_discovery_contracts import (
    HybridChooserSnapshot,
    HybridMatchRow,
    HybridStatus,
    KalshiCatalogExclusion,
    KalshiShadowCatalogSnapshot,
    ProviderDiscoveryState,
    ProviderMatchRef,
)
import inci_tennis_adapters.sportradar_trial_v3 as trial_wire


_MATCH = pattern_compile(r"sr:sport_event:[1-9][0-9]*\Z")
_TICKER = pattern_compile(r"[A-Z0-9][A-Z0-9._-]{0,127}\Z")
_SAFE_CODE = pattern_compile(
    r"(?:shadow|kalshi|sportradar)_[a-z0-9_]{1,96}\Z"
)
_MAXIMUM_SOURCE_FUTURE_NS = 5_000_000_000
_MAXIMUM_SOURCE_AGE_NS = 60_000_000_000


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


def _catalog_transport() -> object:
    from inci_tennis_io.kalshi_shadow_catalog import (
        KalshiShadowCatalogTransport,
    )

    return KalshiShadowCatalogTransport()


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
    kalshi_only_credential_loader: Callable[..., object] = (
        load_kalshi_only_credential_material
    )
    trial_ledger_factory: Callable[[], object] = TrialUsageLedger
    sportradar_transport_factory: Callable[..., object] = (
        _async_provider
    )
    catalog_transport_factory: Callable[[], object] = _catalog_transport
    evidence_store_factory: Callable[[], object] = ShadowEvidenceStore
    evidence_root: Callable[[], object] = default_shadow_state_root
    kalshi_transport_factory: Callable[[object, tuple[str, str]], object] = (
        _kalshi_transport
    )
    projector_factory: Callable[[tuple[str, str]], object] = (
        UnqualifiedKalshiProjector
    )
    collector_factory: Callable[..., object] = LiveShadowCollector
    price_only_collector_factory: Callable[..., object] = (
        PriceOnlyShadowCollector
    )
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
    parser.add_argument("--choose", action="store_true")
    parser.add_argument("--match-id")
    parser.add_argument("--home-ticker")
    parser.add_argument("--away-ticker")
    parser.add_argument("--duration-seconds", type=int, default=600)
    parser.add_argument("--poll-seconds", type=int, default=10)
    return parser


@dataclass(frozen=True, slots=True)
class _CliArguments:
    choose: bool
    match_id: str | None
    tickers: tuple[str, str] | None
    duration_seconds: int
    poll_seconds: int


def _arguments(argv: list[str] | None) -> _CliArguments:
    value = _parser().parse_args(argv)
    manual_values = (
        value.match_id,
        value.home_ticker,
        value.away_ticker,
    )
    manual_complete = all(item is not None for item in manual_values)
    manual_empty = all(item is None for item in manual_values)
    tickers = (
        None
        if not manual_complete
        else (value.home_ticker, value.away_ticker)
    )
    if (
        type(value.choose) is not bool
        or (value.choose and not manual_empty)
        or (not value.choose and not manual_complete)
        or manual_complete
        and (
            type(value.match_id) is not str
            or _MATCH.fullmatch(value.match_id) is None
            or tickers is None
            or any(
                type(item) is not str or _TICKER.fullmatch(item) is None
                for item in tickers
            )
            or tickers[0] == tickers[1]
        )
        or type(value.duration_seconds) is not int
        or value.duration_seconds < 10
        or value.duration_seconds > 3_600
        or type(value.poll_seconds) is not int
        or value.poll_seconds < 1
        or value.poll_seconds > value.duration_seconds
    ):
        raise _UsageError("invalid command arguments")
    return _CliArguments(
        choose=value.choose,
        match_id=None if value.choose else value.match_id,
        tickers=None if value.choose else tickers,
        duration_seconds=value.duration_seconds,
        poll_seconds=value.poll_seconds,
    )


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
    __slots__ = ("prompting", "requested")

    def __init__(self) -> None:
        self.requested = False
        self.prompting = False

    def request(self, *_: object) -> None:
        self.requested = True
        if self.prompting:
            raise KeyboardInterrupt

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
    if (
        value is None
        and len(getattr(error, "args", ())) == 1
        and type(error.args[0]) is str
    ):
        value = error.args[0]
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
    *, planned_calls: int, evidence_root: object, mapping_mode: str
) -> str:
    root = str(evidence_root)
    if not root or "\n" in root or "\r" in root or len(root) > 4_096:
        raise ShadowCollectorError("shadow_evidence_root_invalid")
    if mapping_mode == "auto_matched":
        mode = (
            "READ ONLY / VERIFIED SOURCE LINK / UNQUALIFIED / "
            "NO SIGNALS / NO P&L / NO ORDERS"
        )
        mapping = "VERIFIED SOURCE LINK / UNQUALIFIED"
    elif mapping_mode == "operator_supplied":
        mode = "READ ONLY / UNQUALIFIED / NO ORDERS"
        mapping = "OPERATOR-SUPPLIED / UNVERIFIED"
    else:
        raise ShadowCollectorError("shadow_mapping_mode_invalid")
    return (
        f"{mode}\n"
        "starting unqualified tennis shadow collector\n"
        f"ticker mapping: {mapping}\n"
        f"planned provider calls: {planned_calls}\n"
        f"evidence root: {root}"
    )


async def _record_trial_terminal(
    ledger: object,
    *,
    provider_match_id: str | None,
    reason: str,
    code: str | None = None,
) -> None:
    operation = getattr(ledger, "record_session_terminal", None)
    if not callable(operation):
        return
    values: dict[str, object] = {
        "command": "shadow",
        "provider_match_id": provider_match_id,
        "reason": reason,
    }
    if code is not None:
        values["code"] = code
    await _durable_to_thread(operation, **values)


def _trial_halt_code(error: BaseException) -> str:
    code = _code(error)
    return (
        code
        if code.startswith("sportradar_")
        else "sportradar_shadow_discovery_halted"
    )


@dataclass(frozen=True, slots=True)
class _ProviderDiscovery:
    snapshot: object | None
    state: ProviderDiscoveryState
    raw_path: str | None = None
    raw_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class _VerifiedCollectionHalt:
    code: str
    predecessor_session_id: str
    predecessor_terminal_row_sha256: str


def _optional_provider_key(
    environ: Mapping[str, str] | None,
) -> tuple[str | None, str]:
    source = os.environ if environ is None else environ
    if not isinstance(source, Mapping):
        raise ShadowCollectorError("shadow_credentials_invalid")
    value = source.get("SPORTRADAR_API_KEY")
    if value is None:
        return None, "provider_credentials_missing"
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > 4_096
        or any(ord(character) < 32 for character in value)
    ):
        return None, "provider_credentials_invalid"
    return value, "provider_credentials_available"


def _discovery_state(
    state: str,
    reason: str,
    *,
    raw_sha256: str | None = None,
    captured_wall_ns: int | None = None,
) -> ProviderDiscoveryState:
    try:
        return ProviderDiscoveryState(
            state,
            reason,
            raw_sha256,
            captured_wall_ns,
        )
    except ValueError:
        raise ShadowCollectorError("shadow_provider_discovery_invalid") from None


async def _capture_optional_provider(
    *,
    provider_key: str | None,
    absent_reason: str,
    services: LiveShadowCliDependencies,
) -> _ProviderDiscovery:
    if provider_key is None:
        return _ProviderDiscovery(
            None,
            _discovery_state("unavailable", absent_reason),
        )
    with services.trial_ledger_factory() as trial_ledger:
        try:
            _preflight_quota(trial_ledger, 1)
        except ShadowCollectorError as error:
            if error.code == "sportradar_shadow_quota_insufficient":
                return _ProviderDiscovery(
                    None,
                    _discovery_state(
                        "unavailable", "provider_quota_unavailable"
                    ),
                )
            raise
        capture: object | None = None
        provider_snapshot: object | None = None
        terminal_attempted = False
        provider_context = services.sportradar_transport_factory(
            api_key=provider_key,
            ledger=trial_ledger,
        )
        try:
            async with provider_context as provider:
                try:
                    capture = await provider.fetch_live_summaries()
                    payload = getattr(capture, "payload", None)
                    if type(payload) is not bytes:
                        raise ShadowCollectorError(
                            "sportradar_response_body_invalid"
                        )
                    raw_digest = sha256(payload).hexdigest()
                    try:
                        provider_snapshot = (
                            trial_wire.parse_live_summaries_for_hybrid(payload)
                        )
                    except trial_wire.SportradarWireContractError as error:
                        await _durable_to_thread(
                            trial_ledger.record_parser_failure,
                            command="shadow",
                            reservation=capture.reservation,
                            code=error.code,
                        )
                        raise
                    captured_wall_ns = getattr(
                        capture, "captured_wall_ns", None
                    )
                    difference = (
                        captured_wall_ns - provider_snapshot.generated_wall_ns
                        if type(captured_wall_ns) is int
                        else None
                    )
                    if difference is None:
                        raise ShadowCollectorError("sportradar_clock_invalid")
                    if difference < -_MAXIMUM_SOURCE_FUTURE_NS:
                        error = trial_wire.SportradarWireContractError(
                            "sportradar_source_time_ahead"
                        )
                        await _durable_to_thread(
                            trial_ledger.record_parser_failure,
                            command="shadow",
                            reservation=capture.reservation,
                            code=error.code,
                        )
                        raise error
                    await _durable_to_thread(
                        trial_ledger.record_observation,
                        TrialObservationRecord(
                            command="shadow",
                            reservation=capture.reservation,
                            provider_match_id=None,
                            generated_wall_ns=(
                                provider_snapshot.generated_wall_ns
                            ),
                            captured_wall_ns=captured_wall_ns,
                            status="listed",
                            match_status=None,
                            payload_sha256=raw_digest,
                            raw_path=capture.raw_path,
                            progression="discovery",
                            last_event_id=None,
                            terminal_reason=(
                                "empty"
                                if not provider_snapshot.matches
                                else None
                            ),
                        ),
                    )
                    if difference > _MAXIMUM_SOURCE_AGE_NS:
                        raise trial_wire.SportradarWireContractError(
                            "sportradar_source_stale"
                        )
                    if not provider_snapshot.matches:
                        terminal_attempted = True
                        await _record_trial_terminal(
                            trial_ledger,
                            provider_match_id=None,
                            reason="list_complete",
                        )
                        return _ProviderDiscovery(
                            provider_snapshot,
                            _discovery_state(
                                "unavailable",
                                "provider_empty",
                                raw_sha256=raw_digest,
                                captured_wall_ns=captured_wall_ns,
                            ),
                            str(capture.raw_path),
                            raw_digest,
                        )
                    terminal_attempted = True
                    await _record_trial_terminal(
                        trial_ledger,
                        provider_match_id=None,
                        reason="list_complete",
                    )
                    return _ProviderDiscovery(
                        provider_snapshot,
                        _discovery_state(
                            "available",
                            "provider_discovery_available",
                            raw_sha256=raw_digest,
                            captured_wall_ns=captured_wall_ns,
                        ),
                        str(capture.raw_path),
                        raw_digest,
                    )
                except asyncio.CancelledError:
                    if terminal_attempted:
                        raise
                    terminal_attempted = True
                    await _record_trial_terminal(
                        trial_ledger,
                        provider_match_id=None,
                        reason="cancelled",
                        code="sportradar_shadow_task_cancelled",
                    )
                    raise
                except KeyboardInterrupt:
                    if terminal_attempted:
                        raise
                    terminal_attempted = True
                    await _record_trial_terminal(
                        trial_ledger,
                        provider_match_id=None,
                        reason="operator_interrupt",
                        code="sportradar_operator_interrupt",
                    )
                    raise
                except BaseException as error:
                    if terminal_attempted:
                        raise
                    code = _code(error)
                    terminal_attempted = True
                    await _record_trial_terminal(
                        trial_ledger,
                        provider_match_id=None,
                        reason="halted",
                        code=_trial_halt_code(error),
                    )
                    if not _provider_failure_allows_price_only(code):
                        raise
                    raw_path = (
                        None
                        if capture is None
                        else str(getattr(capture, "raw_path", "")) or None
                    )
                    raw_digest = (
                        None
                        if capture is None
                        else sha256(getattr(capture, "payload", b"")).hexdigest()
                    )
                    captured_wall_ns = (
                        None
                        if capture is None
                        else getattr(capture, "captured_wall_ns", None)
                    )
                    return _ProviderDiscovery(
                        provider_snapshot,
                        _discovery_state(
                            "stale" if code == "sportradar_source_stale" else "error",
                            code,
                            raw_sha256=raw_digest,
                            captured_wall_ns=captured_wall_ns,
                        ),
                        raw_path,
                        raw_digest,
                    )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise


def _clock_pair(services: LiveShadowCliDependencies) -> tuple[int, int]:
    wall = services.wall_ns()
    monotonic = services.monotonic_ns()
    if (
        type(wall) is not int
        or wall <= 0
        or type(monotonic) is not int
        or monotonic < 0
    ):
        raise ShadowCollectorError("shadow_clock_invalid")
    return wall, monotonic


def _price_session(
    *,
    row: HybridMatchRow,
    snapshot: HybridChooserSnapshot,
    discovery: _ProviderDiscovery,
    selected_wall_ns: int,
    selected_monotonic_ns: int,
    predecessor_session_id: str | None = None,
    predecessor_terminal_row_sha256: str | None = None,
) -> PriceOnlySessionEvidence:
    _row_identity(row)
    game = row.game
    markets = game.markets
    candidates = tuple(
        ShadowMarketCandidate(
            market.ticker,
            market.initial_yes_bid,
            market.initial_yes_ask,
            market.initial_yes_bid_depth,
            market.initial_yes_ask_depth,
        )
        for market in markets
    )
    provenance = game.provenance
    return PriceOnlySessionEvidence(
        selected_wall_ns=selected_wall_ns,
        selected_monotonic_ns=selected_monotonic_ns,
        event_ticker=game.event_ticker,
        player_a_name=markets[0].yes_player_name,
        player_b_name=markets[1].yes_player_name,
        market_tickers=(markets[0].ticker, markets[1].ticker),
        scheduled_start_wall_ns=game.scheduled_start_wall_ns,
        catalog_sport=provenance.sport,
        catalog_scope=provenance.scope,
        catalog_queried_competitions=provenance.queried_competitions,
        catalog_series_ticker=provenance.series_ticker,
        catalog_milestone_id=provenance.milestone_id,
        catalog_milestone_league=provenance.milestone_league,
        initial_book_state=game.initial_book_state,
        initial_market_a=candidates[0],
        initial_market_b=candidates[1],
        provider_discovery_state=discovery.state.state,
        provider_discovery_reason=discovery.state.reason,
        provider_discovery_raw_path=discovery.raw_path,
        provider_discovery_raw_sha256=discovery.raw_sha256,
        kalshi_catalog_sha256=snapshot.catalog_sha256,
        resolver_snapshot_sha256=snapshot.resolver_snapshot_sha256,
        resolver_version=snapshot.resolver_version,
        registry_digest=snapshot.coverage_registry_sha256,
        predecessor_session_id=predecessor_session_id,
        predecessor_terminal_row_sha256=(
            predecessor_terminal_row_sha256
        ),
    )


def _safe_display(value: object, maximum: int = 160) -> str:
    text = str(value)
    safe = "".join(
        " " if character in "\r\n\t" or ord(character) < 32 else character
        for character in text
    )
    return " ".join(safe.split())[:maximum]


def _selectable_rows(
    snapshot: HybridChooserSnapshot,
) -> tuple[HybridMatchRow, ...]:
    return tuple(
        row
        for status in (HybridStatus.VERIFIED, HybridStatus.PRICE_ONLY)
        for row in snapshot.rows
        if row.status is status and row.selectable is True
    )


def _row_names(row: HybridMatchRow) -> tuple[str, str]:
    provider = row.provider_match
    if row.status is HybridStatus.VERIFIED and type(provider) is ProviderMatchRef:
        return provider.home_player_name, provider.away_player_name
    markets = row.game.markets
    if type(markets) is tuple and len(markets) == 2:
        return markets[0].yes_player_name, markets[1].yes_player_name
    raise ShadowCollectorError("shadow_selection_identity_changed")


def _row_identity(row: object) -> tuple[object, ...]:
    if type(row) is not HybridMatchRow:
        raise ShadowCollectorError("shadow_selection_identity_changed")
    game = row.game
    markets = getattr(game, "markets", None)
    provenance = getattr(game, "provenance", None)
    if (
        type(markets) is not tuple
        or len(markets) != 2
        or type(row.market_tickers) is not tuple
        or len(row.market_tickers) != 2
        or any(
            type(ticker) is not str or _TICKER.fullmatch(ticker) is None
            for ticker in row.market_tickers
        )
        or row.market_tickers[0] == row.market_tickers[1]
        or type(getattr(game, "event_ticker", None)) is not str
        or _TICKER.fullmatch(game.event_ticker) is None
    ):
        raise ShadowCollectorError("shadow_selection_identity_changed")
    market_identity = tuple(
        (
            market.ticker,
            market.yes_player_name,
            market.initial_yes_bid,
            market.initial_yes_ask,
            market.initial_yes_bid_depth,
            market.initial_yes_ask_depth,
        )
        for market in markets
    )
    provider = row.provider_match
    if row.status is HybridStatus.VERIFIED:
        if (
            row.selectable is not True
            or type(provider) is not ProviderMatchRef
            or type(provider.provider_match_id) is not str
            or _MATCH.fullmatch(provider.provider_match_id) is None
            or type(provider.provider_start_wall_ns) is not int
            or provider.provider_start_wall_ns <= 0
            or provider.status != "live"
            or set(row.market_tickers)
            != {markets[0].ticker, markets[1].ticker}
        ):
            raise ShadowCollectorError("shadow_selection_identity_changed")
        provider_identity: object = (
            provider.provider_match_id,
            provider.provider_start_wall_ns,
            provider.home_player_name,
            provider.away_player_name,
            provider.status,
            provider.competition,
        )
    elif row.status is HybridStatus.PRICE_ONLY:
        if (
            row.selectable is not True
            or provider is not None
            or row.market_tickers
            != (markets[0].ticker, markets[1].ticker)
        ):
            raise ShadowCollectorError("shadow_selection_identity_changed")
        provider_identity = None
    else:
        raise ShadowCollectorError("shadow_selection_identity_changed")
    return (
        row.status,
        game.event_ticker,
        game.scheduled_start_wall_ns,
        game.game_title,
        market_identity,
        provenance,
        game.initial_book_state,
        row.market_tickers,
        provider_identity,
        row.reason,
        row.selectable,
        row.diagnostics,
    )


def _chooser_text(
    snapshot: HybridChooserSnapshot,
    exclusions: tuple[KalshiCatalogExclusion, ...] = (),
) -> str:
    selectable = _selectable_rows(snapshot)
    numbers = {id(row): number for number, row in enumerate(selectable, start=1)}
    lines = [
        "READ ONLY / HYBRID TENNIS EVIDENCE / NO SIGNALS / NO P&L / NO ORDERS\n",
        "VERIFIED\n",
        "--------\n",
    ]
    verified = tuple(
        row for row in snapshot.rows if row.status is HybridStatus.VERIFIED
    )
    if verified:
        for row in verified:
            first, second = _row_names(row)
            lines.append(
                f"[{numbers[id(row)]}] {_safe_display(first)} vs "
                f"{_safe_display(second)} | {_safe_display(row.game.event_ticker)}\n"
            )
    else:
        lines.append("None\n")
    lines.extend(("\nPRICE ONLY\n", "----------\n"))
    price_only = tuple(
        row for row in snapshot.rows if row.status is HybridStatus.PRICE_ONLY
    )
    if price_only:
        for row in price_only:
            first, second = _row_names(row)
            lines.append(
                f"[{numbers[id(row)]}] {_safe_display(first)} vs "
                f"{_safe_display(second)} | {_safe_display(row.game.event_ticker)} | "
                f"{_safe_display(row.reason)} | book={_safe_display(row.game.initial_book_state)}\n"
            )
    else:
        lines.append("None\n")
    lines.extend(("\nCONFLICT / EXCLUDED\n", "-------------------\n"))
    conflicts = tuple(
        row for row in snapshot.rows if row.status is HybridStatus.CONFLICT
    )
    if conflicts or exclusions or snapshot.provider_diagnostics:
        for row in conflicts:
            lines.append(
                f"- {_safe_display(row.game.event_ticker)} | "
                f"{_safe_display(row.reason)} | "
                f"{_safe_display(','.join(row.diagnostics))}\n"
            )
        for row in exclusions:
            lines.append(
                f"- {_safe_display(row.event_ticker)} | {_safe_display(row.reason)}\n"
            )
        for diagnostic in snapshot.provider_diagnostics:
            lines.append(f"- provider | {_safe_display(diagnostic)}\n")
    else:
        lines.append("None\n")
    return "".join(lines)


def _prompt_choice(
    snapshot: HybridChooserSnapshot,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: _StopState,
) -> HybridMatchRow | None:
    choices = _selectable_rows(snapshot)
    if not choices:
        return None
    while True:
        if stop():
            raise KeyboardInterrupt
        _write(stdout, f"Select [1-{len(choices)}] or Q: ")
        try:
            stop.prompting = True
            if stop():
                raise KeyboardInterrupt
            value = stdin.readline()
            if stop():
                raise KeyboardInterrupt
        finally:
            stop.prompting = False
        if type(value) is not str:
            raise ShadowCollectorError("shadow_selection_input_invalid")
        if value == "":
            return None
        selected = value.strip()
        if selected.casefold() == "q":
            return None
        if selected.isascii() and selected.isdigit():
            number = int(selected)
            if 1 <= number <= len(choices):
                return choices[number - 1]
        _write(stdout, "Invalid selection; enter a displayed number or Q.\n")


async def _run_collection(
    *,
    match_id: str,
    tickers: tuple[str, str],
    duration_seconds: int,
    poll_seconds: int,
    material: object,
    output: _DashboardOutput,
    services: LiveShadowCliDependencies,
    stop: _StopState,
    trial_ledger: object,
    provider: object,
    mapping_mode: str,
    resolution: ShadowResolutionEvidence | None,
) -> str | _VerifiedCollectionHalt:
    collector: object | None = None
    evidence: object | None = None
    try:
        with services.evidence_store_factory() as evidence:
            if resolution is not None:
                append_resolution = getattr(
                    evidence, "append_resolution", None
                )
                if not callable(append_resolution):
                    raise ShadowCollectorError(
                        "shadow_evidence_resolution_unavailable"
                    )
                await asyncio.to_thread(append_resolution, resolution)
            kalshi = services.kalshi_transport_factory(material, tickers)
            projector = services.projector_factory(tickers)
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
                mapping_mode=mapping_mode,
            )
            return await collector.run(
                duration_seconds=duration_seconds,
                poll_seconds=poll_seconds,
            )
    except BaseException as error:
        counts = getattr(collector, "evidence_counts", (0, 0))
        if collector is None:
            provider_captures = getattr(provider, "completed_captures", 0)
            if type(provider_captures) is int and provider_captures >= 0:
                counts = (provider_captures, 0)
        if (
            type(counts) is not tuple
            or len(counts) != 2
            or any(type(value) is not int or value < 0 for value in counts)
        ):
            counts = (0, 0)
        if evidence is not None:
            ensure_terminal = getattr(evidence, "ensure_halted_terminal", None)
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
        if collector is None:
            await _record_trial_terminal(
                trial_ledger,
                provider_match_id=match_id,
                reason="halted",
                code="sportradar_shadow_discovery_halted",
            )
        if (
            mapping_mode == "auto_matched"
            and getattr(error, "failover_eligible", False) is True
        ):
            session_id = getattr(evidence, "session_id", None)
            terminal_digest = getattr(
                evidence, "terminal_row_sha256", None
            )
            if (
                type(session_id) is not str
                or not session_id
                or type(terminal_digest) is not str
                or pattern_compile(r"[0-9a-f]{64}\Z").fullmatch(
                    terminal_digest
                )
                is None
            ):
                raise ShadowCollectorError(
                    "shadow_evidence_reference_invalid"
                ) from error
            return _VerifiedCollectionHalt(
                _code(error), session_id, terminal_digest
            )
        raise


async def _run_manual(
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
        planned_calls = _planned_provider_calls(duration_seconds, poll_seconds)
        _preflight_quota(trial_ledger, planned_calls)
        output(
            _startup_banner(
                planned_calls=planned_calls,
                evidence_root=services.evidence_root(),
                mapping_mode="operator_supplied",
            )
        )
        provider_context = services.sportradar_transport_factory(
            api_key=material.sportradar_api_key,
            ledger=trial_ledger,
        )
        async with provider_context as provider:
            return await _run_collection(
                match_id=match_id,
                tickers=tickers,
                duration_seconds=duration_seconds,
                poll_seconds=poll_seconds,
                material=material,
                output=output,
                services=services,
                stop=stop,
                trial_ledger=trial_ledger,
                provider=provider,
                mapping_mode="operator_supplied",
                resolution=None,
            )


async def _run_price_choice(
    *,
    row: HybridMatchRow,
    snapshot: HybridChooserSnapshot,
    discovery: _ProviderDiscovery,
    duration_seconds: int,
    material: object,
    output: _DashboardOutput,
    services: LiveShadowCliDependencies,
    stop: _StopState,
    selected_wall_ns: int,
    selected_monotonic_ns: int,
    predecessor: _VerifiedCollectionHalt | None = None,
) -> str:
    session = _price_session(
        row=row,
        snapshot=snapshot,
        discovery=discovery,
        selected_wall_ns=selected_wall_ns,
        selected_monotonic_ns=selected_monotonic_ns,
        predecessor_session_id=(
            None if predecessor is None else predecessor.predecessor_session_id
        ),
        predecessor_terminal_row_sha256=(
            None
            if predecessor is None
            else predecessor.predecessor_terminal_row_sha256
        ),
    )
    with services.evidence_store_factory() as evidence:
        kalshi = services.kalshi_transport_factory(
            material, session.market_tickers
        )
        projector = services.projector_factory(session.market_tickers)
        collector = services.price_only_collector_factory(
            session_evidence=session,
            kalshi_transport=kalshi,
            market_projector=projector,
            evidence_store=evidence,
            wall_ns=services.wall_ns,
            monotonic_ns=services.monotonic_ns,
            pause=services.pause,
            stop_requested=stop,
            render=output,
        )
        return await collector.run(duration_seconds=duration_seconds)


async def _run_verified_choice(
    *,
    row: HybridMatchRow,
    snapshot: HybridChooserSnapshot,
    discovery: _ProviderDiscovery,
    provider_key: str,
    duration_seconds: int,
    poll_seconds: int,
    selected_wall_ns: int,
    material: object,
    output: _DashboardOutput,
    services: LiveShadowCliDependencies,
    stop: _StopState,
) -> str | _VerifiedCollectionHalt:
    identity = _row_identity(row)
    provider_match = row.provider_match
    if (
        type(provider_match) is not ProviderMatchRef
        or discovery.raw_path is None
        or discovery.raw_sha256 is None
        or discovery.state.state != "available"
    ):
        raise ShadowCollectorError("shadow_selection_identity_changed")
    planned_calls = _planned_provider_calls(
        duration_seconds, poll_seconds
    )
    with services.trial_ledger_factory() as trial_ledger:
        _preflight_quota(trial_ledger, planned_calls)
        if _row_identity(row) != identity:
            raise ShadowCollectorError("shadow_selection_identity_changed")
        output(
            _startup_banner(
                planned_calls=planned_calls,
                evidence_root=services.evidence_root(),
                mapping_mode="auto_matched",
            )
        )
        provider_context = services.sportradar_transport_factory(
            api_key=provider_key,
            ledger=trial_ledger,
        )
        async with provider_context as provider:
            resolution = ShadowResolutionEvidence(
                selected_wall_ns=selected_wall_ns,
                provider_match_id=provider_match.provider_match_id,
                provider_start_wall_ns=(
                    provider_match.provider_start_wall_ns
                ),
                event_ticker=row.game.event_ticker,
                home_player_name=provider_match.home_player_name,
                away_player_name=provider_match.away_player_name,
                market_tickers=row.market_tickers,
                provider_discovery_raw_path=discovery.raw_path,
                provider_discovery_raw_sha256=discovery.raw_sha256,
                kalshi_catalog_sha256=snapshot.catalog_sha256,
                resolver_snapshot_sha256=(
                    snapshot.resolver_snapshot_sha256
                ),
                resolver_rule_version="strict-name-start-v1",
            )
            return await _run_collection(
                match_id=provider_match.provider_match_id,
                tickers=row.market_tickers,
                duration_seconds=duration_seconds,
                poll_seconds=poll_seconds,
                material=material,
                output=output,
                services=services,
                stop=stop,
                trial_ledger=trial_ledger,
                provider=provider,
                mapping_mode="auto_matched",
                resolution=resolution,
            )


async def _run_choose(
    *,
    duration_seconds: int,
    poll_seconds: int,
    material: object,
    environ: Mapping[str, str] | None,
    stdin: TextIO,
    stdout: TextIO,
    output: _DashboardOutput,
    services: LiveShadowCliDependencies,
    stop: _StopState,
) -> str:
    catalog_transport = services.catalog_transport_factory()
    discover = getattr(catalog_transport, "discover_tennis_catalog", None)
    if not callable(discover):
        raise ShadowCollectorError("kalshi_catalog_contract_invalid")
    catalog = await asyncio.to_thread(discover)
    if type(catalog) is not KalshiShadowCatalogSnapshot:
        raise ShadowCollectorError("kalshi_catalog_contract_invalid")
    provider_key, absent_reason = _optional_provider_key(environ)
    discovery = await _capture_optional_provider(
        provider_key=provider_key,
        absent_reason=absent_reason,
        services=services,
    )
    try:
        snapshot = resolve_hybrid_shadow_matches(
            catalog,
            discovery.snapshot,
            provider_state=discovery.state,
        )
    except ValueError:
        raise ShadowCollectorError("shadow_resolution_invalid") from None
    identities = tuple(
        _row_identity(row) for row in _selectable_rows(snapshot)
    )
    output(_chooser_text(snapshot, catalog.excluded))
    choice = _prompt_choice(
        snapshot,
        stdin=stdin,
        stdout=stdout,
        stop=stop,
    )
    if choice is None:
        return "list_complete"
    choices = _selectable_rows(snapshot)
    try:
        index = next(
            position
            for position, candidate in enumerate(choices)
            if candidate is choice
        )
    except StopIteration:
        raise ShadowCollectorError("shadow_selection_identity_changed") from None
    if _row_identity(choice) != identities[index]:
        raise ShadowCollectorError("shadow_selection_identity_changed")
    selected_wall_ns, selected_monotonic_ns = _clock_pair(services)
    if choice.status is HybridStatus.PRICE_ONLY:
        return await _run_price_choice(
            row=choice,
            snapshot=snapshot,
            discovery=discovery,
            duration_seconds=duration_seconds,
            material=material,
            output=output,
            services=services,
            stop=stop,
            selected_wall_ns=selected_wall_ns,
            selected_monotonic_ns=selected_monotonic_ns,
        )
    if choice.status is not HybridStatus.VERIFIED or provider_key is None:
        raise ShadowCollectorError("shadow_selection_identity_changed")
    provider_snapshot = discovery.snapshot
    generated_wall_ns = getattr(
        provider_snapshot, "generated_wall_ns", None
    )
    if type(generated_wall_ns) is not int:
        raise ShadowCollectorError("shadow_selection_identity_changed")
    selected_source_age = selected_wall_ns - generated_wall_ns
    if selected_source_age < -_MAXIMUM_SOURCE_FUTURE_NS:
        raise ShadowCollectorError("sportradar_source_time_ahead")
    if selected_source_age > _MAXIMUM_SOURCE_AGE_NS:
        raise ShadowCollectorError("sportradar_source_stale")
    deadline_ns = selected_monotonic_ns + duration_seconds * 1_000_000_000
    result = await _run_verified_choice(
        row=choice,
        snapshot=snapshot,
        discovery=discovery,
        provider_key=provider_key,
        duration_seconds=duration_seconds,
        poll_seconds=poll_seconds,
        selected_wall_ns=selected_wall_ns,
        material=material,
        output=output,
        services=services,
        stop=stop,
    )
    if type(result) is str:
        return result
    if type(result) is not _VerifiedCollectionHalt:
        raise ShadowCollectorError("shadow_internal_error")
    failover_wall_ns, failover_monotonic_ns = _clock_pair(services)
    if failover_monotonic_ns < selected_monotonic_ns:
        raise ShadowCollectorError("shadow_clock_invalid")
    remaining_ns = deadline_ns - failover_monotonic_ns
    remaining_seconds = remaining_ns // 1_000_000_000
    if remaining_seconds < 10:
        raise ShadowCollectorError(result.code)
    return await _run_price_choice(
        row=choice,
        snapshot=snapshot,
        discovery=discovery,
        duration_seconds=int(remaining_seconds),
        material=material,
        output=output,
        services=services,
        stop=stop,
        selected_wall_ns=failover_wall_ns,
        selected_monotonic_ns=failover_monotonic_ns,
        predecessor=result,
    )


def run_cli(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    dependencies: LiveShadowCliDependencies | None = None,
) -> int:
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr
    services = LiveShadowCliDependencies() if dependencies is None else dependencies
    try:
        arguments = _arguments(argv)
    except _UsageError:
        _best_effort_write(error_stream, "ERROR: invalid command arguments\n")
        return 2
    try:
        material = (
            services.kalshi_only_credential_loader(environ)
            if arguments.choose
            else services.credential_loader(environ)
        )
        with _signals() as stop:
            if arguments.choose:
                result = asyncio.run(
                    _run_choose(
                        duration_seconds=arguments.duration_seconds,
                        poll_seconds=arguments.poll_seconds,
                        material=material,
                        environ=environ,
                        stdin=input_stream,
                        stdout=output_stream,
                        output=_DashboardOutput(output_stream),
                        services=services,
                        stop=stop,
                    )
                )
            else:
                if arguments.match_id is None or arguments.tickers is None:
                    raise _UsageError("invalid command arguments")
                result = asyncio.run(
                    _run_manual(
                        match_id=arguments.match_id,
                        tickers=arguments.tickers,
                        duration_seconds=arguments.duration_seconds,
                        poll_seconds=arguments.poll_seconds,
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
