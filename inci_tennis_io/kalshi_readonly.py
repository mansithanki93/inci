"""Read-only Kalshi orderbook WebSocket transport for shadow evidence.

This IO boundary returns opaque bytes only.  A caller can durably acknowledge
the exact frame before passing it to a separate adapter parser.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from re import compile as pattern_compile
import stat
from typing import Awaitable, Callable, Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


KALSHI_WEBSOCKET_PATH = "/trade-api/ws/v2"
KALSHI_WEBSOCKET_ENDPOINT = (
    "wss://external-api-ws.kalshi.com" + KALSHI_WEBSOCKET_PATH
)
KALSHI_API_KEYS_PATH = "/trade-api/v2/api_keys"
KALSHI_API_KEYS_ENDPOINT = "https://external-api.kalshi.com" + KALSHI_API_KEYS_PATH
KALSHI_UNQUALIFIED_SHADOW = "unqualified_shadow"

_MAX_FRAME_BYTES = 1_048_576
_MAX_SCOPE_BODY_BYTES = 262_144
_MAX_PRIVATE_KEY_BYTES = 65_536
_MAX_SIGNED_64 = 9_223_372_036_854_775_807
_SEND_TIMEOUT_SECONDS = 5.0
_TICKER_RE = pattern_compile(r"[A-Z0-9][A-Z0-9._-]{0,127}\Z")
_API_KEY_ID_RE = pattern_compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


class KalshiReadOnlyError(RuntimeError):
    """Fixed-code transport failure that never interpolates wire or secrets."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise KalshiReadOnlyError(code)


@dataclass(frozen=True, slots=True, repr=False)
class KalshiReadOnlyCredentials:
    """Explicit credential reference; environment lookup is intentionally absent."""

    api_key_id: str
    private_key_path: Path

    def __post_init__(self) -> None:
        if (
            type(self.api_key_id) is not str
            or _API_KEY_ID_RE.fullmatch(self.api_key_id) is None
            or not isinstance(self.private_key_path, Path)
            or not self.private_key_path.is_absolute()
        ):
            _fail("kalshi_ws_credentials_invalid")

    def __repr__(self) -> str:
        return "<KalshiReadOnlyCredentials redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class KalshiClockObservation:
    """One externally-authoritative paired wall/monotonic observation."""

    wall_ns: int
    monotonic_ns: int
    uncertainty_ns: int

    def __post_init__(self) -> None:
        if (
            not _valid_nonnegative_int(self.wall_ns)
            or not _valid_nonnegative_int(self.monotonic_ns)
            or not _valid_nonnegative_int(self.uncertainty_ns)
        ):
            _fail("kalshi_ws_clock_invalid")

    def __repr__(self) -> str:
        return "<KalshiClockObservation redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class KalshiCommandReceipt:
    """Correlation coordinates for one read-only WebSocket command."""

    request_id: int
    physical_connection_generation: int
    command: str

    def __post_init__(self) -> None:
        if (
            type(self.request_id) is not int
            or self.request_id <= 0
            or self.request_id > _MAX_SIGNED_64
            or type(self.physical_connection_generation) is not int
            or self.physical_connection_generation <= 0
            or self.physical_connection_generation > _MAX_SIGNED_64
            or self.command not in {"subscribe", "get_snapshot"}
        ):
            _fail("kalshi_ws_command_receipt_invalid")

    @property
    def qualification(self) -> str:
        return KALSHI_UNQUALIFIED_SHADOW

    def __repr__(self) -> str:
        return "<KalshiCommandReceipt unqualified_shadow>"


@dataclass(frozen=True, slots=True, repr=False)
class KalshiRawFrame:
    payload: bytes
    captured_wall_ns: int
    captured_monotonic_ns: int
    clock_uncertainty_ns: int
    physical_connection_generation: int

    def __post_init__(self) -> None:
        if (
            type(self.payload) is not bytes
            or not self.payload
            or len(self.payload) > _MAX_FRAME_BYTES
            or not _valid_nonnegative_int(self.captured_wall_ns)
            or not _valid_nonnegative_int(self.captured_monotonic_ns)
            or not _valid_nonnegative_int(self.clock_uncertainty_ns)
            or type(self.physical_connection_generation) is not int
            or self.physical_connection_generation <= 0
            or self.physical_connection_generation > _MAX_SIGNED_64
        ):
            _fail("kalshi_ws_raw_frame_invalid")

    @property
    def qualification(self) -> str:
        return KALSHI_UNQUALIFIED_SHADOW

    @property
    def raw_sha256(self) -> str:
        return sha256(self.payload).hexdigest()

    def __repr__(self) -> str:
        return "<KalshiRawFrame unqualified_shadow>"


class _Socket(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self, *, decode: bool) -> object: ...

    async def close(self) -> None: ...


_Connector = Callable[..., Awaitable[_Socket]]


class _ScopeBody(Protocol):
    def iter_chunked(self, chunk_size: int): ...


class _ScopeResponse(Protocol):
    status: int
    headers: object
    content: _ScopeBody


class _ScopeRequestContext(Protocol):
    async def __aenter__(self) -> _ScopeResponse: ...

    async def __aexit__(self, *values: object) -> None: ...


class _ScopeSession(Protocol):
    def get(self, url: str, **kwargs: object) -> _ScopeRequestContext: ...

    async def close(self) -> None: ...


class _ScopeSessionFactory(Protocol):
    def __call__(self, **kwargs: object) -> _ScopeSession: ...


async def _default_connector(uri: str, **kwargs: object) -> _Socket:
    try:
        from websockets.asyncio.client import connect
    except Exception:
        _fail("kalshi_ws_dependency_unavailable")

    class _NoRedirectConnect(connect):
        def process_redirect(self, exc: Exception) -> Exception:
            return exc

    if kwargs.get("proxy") is not None:
        _fail("kalshi_ws_connector_invalid")
    kwargs["proxy"] = None
    return await _NoRedirectConnect(uri, **kwargs)


def _default_scope_session_factory(**kwargs: object) -> _ScopeSession:
    try:
        from aiohttp import ClientSession, ClientTimeout
    except Exception:
        _fail("kalshi_ws_scope_dependency_unavailable")
    expected = {
        "total_timeout_seconds",
        "connect_timeout_seconds",
        "read_timeout_seconds",
        "trust_env",
        "auto_decompress",
    }
    if set(kwargs) != expected:
        _fail("kalshi_ws_scope_session_invalid")
    try:
        timeout = ClientTimeout(
            total=kwargs["total_timeout_seconds"],
            connect=kwargs["connect_timeout_seconds"],
            sock_connect=kwargs["connect_timeout_seconds"],
            sock_read=kwargs["read_timeout_seconds"],
        )
        return ClientSession(
            timeout=timeout,
            trust_env=kwargs["trust_env"],
            auto_decompress=kwargs["auto_decompress"],
            raise_for_status=False,
        )
    except KalshiReadOnlyError:
        raise
    except Exception:
        _fail("kalshi_ws_scope_session_invalid")


def _valid_nonnegative_int(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_SIGNED_64


def _read_private_key(credentials: KalshiReadOnlyCredentials) -> rsa.RSAPrivateKey:
    descriptor: int | None = None
    try:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if type(no_follow) is not int:
            _fail("kalshi_ws_private_key_invalid")
        descriptor = os.open(
            credentials.private_key_path,
            os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_PRIVATE_KEY_BYTES
        ):
            _fail("kalshi_ws_private_key_invalid")
        chunks: list[bytes] = []
        remaining = _MAX_PRIVATE_KEY_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 16_384))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if not raw or len(raw) > _MAX_PRIVATE_KEY_BYTES:
            _fail("kalshi_ws_private_key_invalid")
        value = serialization.load_pem_private_key(raw, password=None)
    except KalshiReadOnlyError:
        raise
    except Exception:
        _fail("kalshi_ws_private_key_invalid")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if not isinstance(value, rsa.RSAPrivateKey):
        _fail("kalshi_ws_private_key_invalid")
    return value


def _auth_headers(
    credentials: KalshiReadOnlyCredentials,
    private_key: rsa.RSAPrivateKey,
    wall_time_ns: int,
    path: str,
) -> dict[str, str]:
    if (
        not _valid_nonnegative_int(wall_time_ns)
        or path not in {KALSHI_API_KEYS_PATH, KALSHI_WEBSOCKET_PATH}
    ):
        _fail("kalshi_ws_clock_invalid")
    timestamp = str(wall_time_ns // 1_000_000)
    message = (timestamp + "GET" + path).encode("ascii")
    try:
        signature = private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256().digest_size,
            ),
            hashes.SHA256(),
        )
    except Exception:
        _fail("kalshi_ws_signing_failed")
    return {
        "KALSHI-ACCESS-KEY": credentials.api_key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }


def _scope_header(headers: object, name: str) -> str | None:
    try:
        value = headers.get(name)  # type: ignore[union-attr]
        if value is None:
            value = headers.get(name.lower())  # type: ignore[union-attr]
    except (AttributeError, TypeError):
        _fail("kalshi_ws_scope_response_invalid")
    if value is None:
        return None
    if type(value) is not str or len(value) > 128:
        _fail("kalshi_ws_scope_response_invalid")
    return value


def _decode_scope_payload(payload: bytes, active_key_id: str) -> None:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        _fail("kalshi_ws_scope_response_invalid")
    if type(value) is not dict or set(value) != {"api_keys"}:
        _fail("kalshi_ws_scope_response_invalid")
    rows = value["api_keys"]
    if type(rows) is not list or len(rows) > 256:
        _fail("kalshi_ws_scope_response_invalid")
    active_scopes: set[str] | None = None
    seen: set[str] = set()
    for row in rows:
        if type(row) is not dict or set(row) != {"api_key_id", "name", "scopes"}:
            _fail("kalshi_ws_scope_response_invalid")
        api_key_id = row["api_key_id"]
        name = row["name"]
        scopes = row["scopes"]
        if (
            type(api_key_id) is not str
            or _API_KEY_ID_RE.fullmatch(api_key_id) is None
            or api_key_id in seen
            or type(name) is not str
            or len(name) > 256
            or type(scopes) is not list
            or not scopes
            or len(scopes) > 8
            or any(
                type(scope) is not str or scope not in {"read", "write"}
                for scope in scopes
            )
            or len(set(scopes)) != len(scopes)
        ):
            _fail("kalshi_ws_scope_response_invalid")
        seen.add(api_key_id)
        if api_key_id == active_key_id:
            active_scopes = set(scopes)
    if active_scopes != {"read"}:
        _fail("kalshi_ws_key_scope_not_read_only")


async def _verify_read_only_scope(
    credentials: KalshiReadOnlyCredentials,
    private_key: rsa.RSAPrivateKey,
    wall_time_ns: int,
    session_factory: _ScopeSessionFactory,
) -> None:
    headers = _auth_headers(
        credentials,
        private_key,
        wall_time_ns,
        KALSHI_API_KEYS_PATH,
    )
    headers["accept"] = "application/json"
    headers["accept-encoding"] = "identity"
    try:
        session = session_factory(
            total_timeout_seconds=5,
            connect_timeout_seconds=2,
            read_timeout_seconds=3,
            trust_env=False,
            auto_decompress=False,
        )
    except KalshiReadOnlyError:
        raise
    except Exception:
        _fail("kalshi_ws_scope_session_invalid")
    try:
        try:
            async with asyncio.timeout(5):
                async with session.get(
                    KALSHI_API_KEYS_ENDPOINT,
                    headers=headers,
                    allow_redirects=False,
                    auto_decompress=False,
                    proxy=None,
                ) as response:
                    if type(response.status) is not int:
                        _fail("kalshi_ws_scope_response_invalid")
                    if 300 <= response.status < 400:
                        _fail("kalshi_ws_scope_redirect_rejected")
                    if response.status != 200:
                        _fail("kalshi_ws_scope_request_failed")
                    encoding = _scope_header(response.headers, "Content-Encoding")
                    if (
                        encoding is not None
                        and encoding.strip().lower() != "identity"
                    ):
                        _fail("kalshi_ws_scope_response_invalid")
                    length = _scope_header(response.headers, "Content-Length")
                    if length is not None and (
                        not length.isascii()
                        or not length.isdecimal()
                        or int(length) > _MAX_SCOPE_BODY_BYTES
                    ):
                        _fail("kalshi_ws_scope_response_invalid")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.content.iter_chunked(65_536):
                        if type(chunk) is not bytes:
                            _fail("kalshi_ws_scope_response_invalid")
                        size += len(chunk)
                        if size > _MAX_SCOPE_BODY_BYTES:
                            _fail("kalshi_ws_scope_response_invalid")
                        chunks.append(chunk)
                    payload = b"".join(chunks)
                    if (
                        not payload
                        or length is not None
                        and len(payload) != int(length)
                    ):
                        _fail("kalshi_ws_scope_response_invalid")
                    _decode_scope_payload(payload, credentials.api_key_id)
        except TimeoutError:
            _fail("kalshi_ws_scope_request_failed")
        except KalshiReadOnlyError:
            raise
        except Exception:
            _fail("kalshi_ws_scope_request_failed")
    finally:
        try:
            await session.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            _fail("kalshi_ws_scope_session_invalid")


def _validate_tickers(value: object) -> tuple[str, str]:
    if (
        type(value) is not tuple
        or len(value) != 2
        or any(
            type(item) is not str or _TICKER_RE.fullmatch(item) is None
            for item in value
        )
        or value[0] == value[1]
    ):
        _fail("kalshi_ws_tickers_invalid")
    return value


class KalshiReadOnlyTransport:
    """One-lifetime, one-socket, exactly-two-market shadow transport."""

    __slots__ = (
        "_clock_observer",
        "_connector",
        "_credentials",
        "_generation",
        "_next_request_id",
        "_scope_session_factory",
        "_socket",
        "_state",
        "_tickers",
    )

    def __init__(
        self,
        *,
        credentials: KalshiReadOnlyCredentials,
        market_tickers: tuple[str, str],
        clock_observer: Callable[[], KalshiClockObservation],
        connector: _Connector | None = None,
        scope_session_factory: _ScopeSessionFactory | None = None,
    ) -> None:
        if type(credentials) is not KalshiReadOnlyCredentials:
            _fail("kalshi_ws_credentials_invalid")
        if not callable(connector) and connector is not None:
            _fail("kalshi_ws_connector_invalid")
        if not callable(clock_observer):
            _fail("kalshi_ws_clock_invalid")
        if scope_session_factory is not None and not callable(scope_session_factory):
            _fail("kalshi_ws_scope_session_invalid")
        self._credentials = credentials
        self._tickers = _validate_tickers(market_tickers)
        self._connector = connector or _default_connector
        self._scope_session_factory = (
            scope_session_factory or _default_scope_session_factory
        )
        self._clock_observer = clock_observer
        self._socket: _Socket | None = None
        self._generation = 0
        self._next_request_id = 1
        self._state = "new"

    def __repr__(self) -> str:
        return "<KalshiReadOnlyTransport unqualified_shadow>"

    async def open_readonly(self) -> None:
        if self._state not in {"new", "closed"} or self._socket is not None:
            _fail("kalshi_ws_state_invalid")
        if self._generation >= _MAX_SIGNED_64:
            _fail("kalshi_ws_generation_exhausted")
        self._state = "opening"
        private_key = _read_private_key(self._credentials)
        try:
            observation = self._clock_observer()
            if type(observation) is not KalshiClockObservation:
                _fail("kalshi_ws_clock_invalid")
            await _verify_read_only_scope(
                self._credentials,
                private_key,
                observation.wall_ns,
                self._scope_session_factory,
            )
            headers = _auth_headers(
                self._credentials,
                private_key,
                observation.wall_ns,
                KALSHI_WEBSOCKET_PATH,
            )
            socket = await self._connector(
                KALSHI_WEBSOCKET_ENDPOINT,
                additional_headers=headers,
                proxy=None,
                max_size=_MAX_FRAME_BYTES,
                open_timeout=10,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
            )
        except KalshiReadOnlyError:
            self._state = "halted"
            raise
        except Exception:
            self._state = "halted"
            _fail("kalshi_ws_open_failed")
        if socket is None:
            self._state = "halted"
            _fail("kalshi_ws_open_failed")
        self._socket = socket
        self._generation += 1
        self._state = "open"

    async def subscribe(self) -> KalshiCommandReceipt:
        if self._state != "open" or self._socket is None:
            _fail("kalshi_ws_state_invalid")
        request_id = self._next_request_id
        message = json.dumps(
            {
                "id": request_id,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_tickers": list(self._tickers),
                    "use_yes_price": True,
                },
            },
            separators=(",", ":"),
        )
        try:
            await asyncio.wait_for(
                self._socket.send(message),
                timeout=_SEND_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            self._state = "halted"
            _fail("kalshi_ws_send_timeout")
        except Exception:
            self._state = "halted"
            _fail("kalshi_ws_send_failed")
        self._next_request_id += 1
        self._state = "receiving"
        return KalshiCommandReceipt(
            request_id=request_id,
            physical_connection_generation=self._generation,
            command="subscribe",
        )

    async def receive_one(self, timeout_seconds: float) -> KalshiRawFrame:
        if self._state != "receiving" or self._socket is None:
            _fail("kalshi_ws_state_invalid")
        if (
            type(timeout_seconds) not in (int, float)
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > 60
        ):
            _fail("kalshi_ws_timeout_invalid")
        try:
            raw = await asyncio.wait_for(
                self._socket.recv(decode=False),
                timeout=float(timeout_seconds),
            )
            observation = self._clock_observer()
            if type(observation) is not KalshiClockObservation:
                _fail("kalshi_ws_clock_invalid")
        except TimeoutError:
            _fail("kalshi_ws_receive_timeout")
        except KalshiReadOnlyError:
            self._state = "halted"
            raise
        except Exception:
            self._state = "halted"
            _fail("kalshi_ws_receive_failed")
        if type(raw) is not bytes:
            self._state = "halted"
            _fail("kalshi_ws_frame_type_invalid")
        if not raw:
            self._state = "halted"
            _fail("kalshi_ws_frame_empty")
        if len(raw) > _MAX_FRAME_BYTES:
            self._state = "halted"
            _fail("kalshi_ws_frame_oversize")
        return KalshiRawFrame(
            payload=raw,
            captured_wall_ns=observation.wall_ns,
            captured_monotonic_ns=observation.monotonic_ns,
            clock_uncertainty_ns=observation.uncertainty_ns,
            physical_connection_generation=self._generation,
        )

    async def request_snapshot(
        self,
        subscription_id: int,
    ) -> KalshiCommandReceipt:
        if self._state != "receiving" or self._socket is None:
            _fail("kalshi_ws_state_invalid")
        if (
            type(subscription_id) is not int
            or subscription_id <= 0
            or subscription_id > _MAX_SIGNED_64
        ):
            _fail("kalshi_ws_subscription_id_invalid")
        request_id = self._next_request_id
        message = json.dumps(
            {
                "id": request_id,
                "cmd": "update_subscription",
                "params": {
                    "sids": [subscription_id],
                    "market_tickers": list(self._tickers),
                    "action": "get_snapshot",
                },
            },
            separators=(",", ":"),
        )
        try:
            await asyncio.wait_for(
                self._socket.send(message),
                timeout=_SEND_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            self._state = "halted"
            _fail("kalshi_ws_send_timeout")
        except Exception:
            self._state = "halted"
            _fail("kalshi_ws_send_failed")
        self._next_request_id += 1
        return KalshiCommandReceipt(
            request_id=request_id,
            physical_connection_generation=self._generation,
            command="get_snapshot",
        )

    async def close(self) -> None:
        socket = self._socket
        if socket is None:
            if self._state == "closed":
                return
            if self._state in {"new", "opening", "halted"}:
                self._state = "closed"
                return
            _fail("kalshi_ws_state_invalid")
        self._state = "closing"
        try:
            await socket.close()
        except Exception:
            self._state = "halted"
            _fail("kalshi_ws_close_failed")
        self._socket = None
        self._state = "closed"
