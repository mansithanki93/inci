"""Native-async, read-only Sportradar transport for shadow collection.

The transport owns no parsing or decision authority.  It returns the same
durably-accounted ``TrialCapture`` used by the trial observer while keeping
network waits and the ledger's blocking pacing/fsync work off the event loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
import json
from pathlib import Path
from re import compile as pattern_compile
import time
from typing import Protocol

from inci_tennis_io.sportradar_trial_transport import (
    SportradarTrialObserverError,
    TrialAttemptReservation,
    TrialCapture,
    TrialUsageLedger,
)


_ORIGIN = "https://api.sportradar.com"
_MATCH_ID = pattern_compile(r"sr:sport_event:[1-9][0-9]*\Z")
_MAXIMUM_BODY_BYTES = 8_388_608
_TOTAL_TIMEOUT_SECONDS: float = 15
_TOTAL_TIMEOUT_NS = 15_000_000_000
_CONNECT_TIMEOUT_SECONDS: float = 3
_READ_TIMEOUT_SECONDS: float = 10


def _fail(code: str) -> None:
    raise SportradarTrialObserverError(code)


class _AsyncBody(Protocol):
    def iter_chunked(self, chunk_size: int) -> AsyncIterator[object]: ...


class _AsyncResponse(Protocol):
    status: int
    headers: object
    content: _AsyncBody


class _AsyncRequestContext(Protocol):
    async def __aenter__(self) -> _AsyncResponse: ...

    async def __aexit__(self, *values: object) -> None: ...


class _AsyncSession(Protocol):
    def get(self, url: str, **kwargs: object) -> _AsyncRequestContext: ...

    async def close(self) -> None: ...


class _SessionFactory(Protocol):
    def __call__(
        self,
        *,
        total_timeout_seconds: float,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        trust_env: bool,
        auto_decompress: bool,
    ) -> _AsyncSession: ...


def _default_session_factory(
    *,
    total_timeout_seconds: float,
    connect_timeout_seconds: float,
    read_timeout_seconds: float,
    trust_env: bool,
    auto_decompress: bool,
) -> _AsyncSession:
    """Import aiohttp only when the default transport is first exercised."""

    try:
        from aiohttp import ClientSession, ClientTimeout
    except Exception:
        _fail("sportradar_async_dependency_unavailable")
    try:
        timeout = ClientTimeout(
            total=total_timeout_seconds,
            connect=connect_timeout_seconds,
            sock_connect=connect_timeout_seconds,
            sock_read=read_timeout_seconds,
        )
        return ClientSession(
            timeout=timeout,
            trust_env=trust_env,
            auto_decompress=auto_decompress,
            raise_for_status=False,
        )
    except SportradarTrialObserverError:
        raise
    except Exception:
        _fail("sportradar_transport_unavailable")


def _header(headers: object, name: str) -> str | None:
    try:
        value = headers.get(name)  # type: ignore[union-attr]
        if value is None:
            value = headers.get(name.lower())  # type: ignore[union-attr]
    except (AttributeError, TypeError):
        _fail("sportradar_response_headers_invalid")
    if value is None:
        return None
    if type(value) is not str or len(value) > 512:
        _fail("sportradar_response_headers_invalid")
    return value


def _decoded_contains_secret(payload: bytes, secret: str) -> bool:
    try:
        value = json.loads(payload, object_pairs_hook=lambda pairs: pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return False
    pending = [value]
    visited = 0
    while pending:
        current = pending.pop()
        visited += 1
        if visited > 200_000:
            _fail("sportradar_response_body_invalid")
        if type(current) is str:
            if secret in current:
                return True
        elif type(current) in {list, tuple}:
            pending.extend(current)
    return False


def _escaped_contains_secret(payload: bytes, secret: str) -> bool:
    text = payload.decode("latin-1")
    escapes = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    escape = pattern_compile(r'\\(u[0-9a-fA-F]{4}|["\\/bfnrt])')

    def decode(match: object) -> str:
        token = match.group(1)  # type: ignore[union-attr]
        if token.startswith("u"):
            return chr(int(token[1:], 16))
        return escapes[token]

    return secret in escape.sub(decode, text)


def _validate_api_key(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or value != value.strip()
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        _fail("sportradar_api_key_invalid")
    return value


def _validate_match_id(value: object) -> str:
    if type(value) is not str or _MATCH_ID.fullmatch(value) is None:
        _fail("sportradar_match_identifier_invalid")
    return value


def _persist_success(
    ledger: TrialUsageLedger,
    reservation: TrialAttemptReservation,
    payload: bytes,
    captured_wall_ns: int,
) -> Path:
    raw_path = ledger.persist_raw(
        reservation,
        payload,
        captured_wall_ns=captured_wall_ns,
    )
    ledger.record_attempt_outcome(
        reservation,
        outcome="captured",
        code="sportradar_capture_persisted",
    )
    return raw_path


class SportradarShadowAsyncTransport:
    """GET-only async transport with durable trial-attempt disposition."""

    __slots__ = (
        "_api_key",
        "_closed",
        "_completed_captures",
        "_ledger",
        "_lock",
        "_monotonic_ns",
        "_session",
        "_session_factory",
        "_wall_ns",
    )

    def __init__(
        self,
        *,
        api_key: str,
        ledger: TrialUsageLedger,
        session: _AsyncSession | None = None,
        session_factory: _SessionFactory | None = None,
        wall_ns: Callable[[], int] = time.time_ns,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if type(ledger) is not TrialUsageLedger:
            _fail("sportradar_usage_ledger_invalid")
        if session is not None and session_factory is not None:
            _fail("sportradar_async_session_invalid")
        if session_factory is not None and not callable(session_factory):
            _fail("sportradar_async_session_invalid")
        if not callable(wall_ns) or not callable(monotonic_ns):
            _fail("sportradar_clock_invalid")
        self._api_key = _validate_api_key(api_key)
        self._ledger = ledger
        self._session = session
        self._session_factory = session_factory or _default_session_factory
        self._wall_ns = wall_ns
        self._monotonic_ns = monotonic_ns
        self._lock = asyncio.Lock()
        self._closed = False
        self._completed_captures = 0

    @property
    def completed_captures(self) -> int:
        """Durable captures completed, including cancellation races."""

        return self._completed_captures

    def __repr__(self) -> str:
        return (
            "SportradarShadowAsyncTransport("
            f"ledger={self._ledger!r}, api_key=<redacted>)"
        )

    async def fetch_summary(self, match_id: str) -> TrialCapture:
        match = _validate_match_id(match_id)
        return await self._get(
            "summary",
            f"/tennis/trial/v3/en/sport_events/{match}/summary.json",
        )

    async def fetch_timeline(self, match_id: str) -> TrialCapture:
        match = _validate_match_id(match_id)
        return await self._get(
            "timeline",
            f"/tennis/trial/v3/en/sport_events/{match}/timeline.json",
        )

    async def fetch_live_summaries(self) -> TrialCapture:
        return await self._get(
            "live_summaries",
            "/tennis/trial/v3/en/schedules/live/summaries.json",
        )

    def _get_session(self) -> _AsyncSession:
        if self._session is None:
            self._session = self._session_factory(
                total_timeout_seconds=15,
                connect_timeout_seconds=3,
                read_timeout_seconds=10,
                trust_env=False,
                auto_decompress=False,
            )
        return self._session

    async def _get(self, route: str, path: str) -> TrialCapture:
        async with self._lock:
            if self._closed:
                _fail("sportradar_transport_closed")
            reservation = await self._reserve(route)
            disposition_recorded = False
            try:
                started_monotonic_ns = self._monotonic_ns()
                if (
                    type(started_monotonic_ns) is not int
                    or started_monotonic_ns < 0
                ):
                    _fail("sportradar_clock_invalid")
                try:
                    async with asyncio.timeout(_TOTAL_TIMEOUT_SECONDS):
                        payload = await self._request_payload(path)
                except TimeoutError:
                    _fail("sportradar_total_deadline")
                self._enforce_deadline(started_monotonic_ns)

                if (
                    self._api_key.encode("utf-8") in payload
                    or _decoded_contains_secret(payload, self._api_key)
                    or _escaped_contains_secret(payload, self._api_key)
                ):
                    _fail("sportradar_credential_reflected")
                self._enforce_deadline(started_monotonic_ns)
                captured_wall_ns = self._wall_ns()
                if type(captured_wall_ns) is not int or captured_wall_ns <= 0:
                    _fail("sportradar_clock_invalid")
                raw_path = await self._persist(
                    reservation,
                    payload,
                    captured_wall_ns,
                )
                disposition_recorded = True
                self._enforce_deadline(started_monotonic_ns)
                return TrialCapture(
                    reservation=reservation,
                    captured_wall_ns=captured_wall_ns,
                    raw_path=raw_path,
                    payload=payload,
                )
            except asyncio.CancelledError:
                if not disposition_recorded:
                    await self._record_interrupted(reservation)
                raise
            except SportradarTrialObserverError as error:
                if not disposition_recorded:
                    await self._record_failed(reservation, error.code)
                raise
            except Exception:
                if not disposition_recorded:
                    await self._record_failed(
                        reservation,
                        "sportradar_transport_unavailable",
                    )
                _fail("sportradar_transport_unavailable")

    def _enforce_deadline(self, started_monotonic_ns: int) -> None:
        current = self._monotonic_ns()
        if (
            type(current) is not int
            or current < started_monotonic_ns
            or current - started_monotonic_ns > _TOTAL_TIMEOUT_NS
        ):
            if type(current) is int and current >= started_monotonic_ns:
                _fail("sportradar_total_deadline")
            _fail("sportradar_clock_invalid")

    async def _request_payload(self, path: str) -> bytes:
        session = self._get_session()
        try:
            request = session.get(
                _ORIGIN + path,
                headers={
                    "accept": "application/json",
                    "accept-encoding": "identity",
                    "x-api-key": self._api_key,
                },
                allow_redirects=False,
                auto_decompress=False,
            )
            async with request as response:
                status = response.status
                if type(status) is not int or status != 200:
                    if type(status) is int and 100 <= status <= 599:
                        _fail(f"sportradar_http_status_{status}")
                    _fail("sportradar_http_status_invalid")
                content_type = _header(response.headers, "Content-Type")
                if (
                    content_type is None
                    or content_type.split(";", 1)[0].strip().lower()
                    != "application/json"
                ):
                    _fail("sportradar_content_type_invalid")
                content_encoding = _header(
                    response.headers,
                    "Content-Encoding",
                )
                if (
                    content_encoding is not None
                    and content_encoding.strip().lower() not in {"", "identity"}
                ):
                    _fail("sportradar_content_encoding_invalid")
                content_length = _header(response.headers, "Content-Length")
                if content_length is not None:
                    if (
                        not content_length.isascii()
                        or not content_length.isdecimal()
                    ):
                        _fail("sportradar_content_length_invalid")
                    if int(content_length) > _MAXIMUM_BODY_BYTES:
                        _fail("sportradar_body_too_large")
                chunks: list[bytes] = []
                size = 0
                try:
                    async for chunk in response.content.iter_chunked(65_536):
                        if type(chunk) is not bytes:
                            _fail("sportradar_response_body_invalid")
                        size += len(chunk)
                        if size > _MAXIMUM_BODY_BYTES:
                            _fail("sportradar_body_too_large")
                        chunks.append(chunk)
                except SportradarTrialObserverError:
                    raise
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _fail("sportradar_response_body_unavailable")
                payload = b"".join(chunks)
                if content_length is not None and len(payload) != int(
                    content_length
                ):
                    _fail("sportradar_content_length_mismatch")
                return payload
        except (SportradarTrialObserverError, asyncio.CancelledError):
            raise
        except TimeoutError:
            raise
        except Exception:
            _fail("sportradar_transport_unavailable")

    async def _reserve(self, route: str) -> TrialAttemptReservation:
        task = asyncio.create_task(asyncio.to_thread(self._ledger.reserve, route))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                reservation = await asyncio.shield(task)
            except Exception:
                raise
            await self._record_interrupted(reservation)
            raise

    async def _persist(
        self,
        reservation: TrialAttemptReservation,
        payload: bytes,
        captured_wall_ns: int,
    ) -> Path:
        task = asyncio.create_task(
            asyncio.to_thread(
                _persist_success,
                self._ledger,
                reservation,
                payload,
                captured_wall_ns,
            )
        )
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                raw_path = await asyncio.shield(task)
                break
            except asyncio.CancelledError as error:
                if cancellation is None:
                    cancellation = error
            except Exception as error:
                if cancellation is not None:
                    await self._record_failed(
                        reservation,
                        "sportradar_transport_unavailable",
                    )
                    raise cancellation from error
                raise
        self._completed_captures += 1
        if cancellation is not None:
            raise cancellation
        return raw_path

    async def _record_interrupted(
        self,
        reservation: TrialAttemptReservation,
    ) -> None:
        await asyncio.shield(
            asyncio.to_thread(
                self._ledger.record_interrupted_outcome,
                reservation,
            )
        )

    async def _record_failed(
        self,
        reservation: TrialAttemptReservation,
        code: str,
    ) -> None:
        await asyncio.shield(
            asyncio.to_thread(
                self._ledger.record_failed_or_uncertain_outcome,
                reservation,
                code=code,
            )
        )

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            session = self._session
            self._session = None
            if session is not None:
                try:
                    await session.close()
                except Exception:
                    pass

    async def __aenter__(self) -> SportradarShadowAsyncTransport:
        if self._closed:
            _fail("sportradar_transport_closed")
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


__all__ = ("SportradarShadowAsyncTransport",)
