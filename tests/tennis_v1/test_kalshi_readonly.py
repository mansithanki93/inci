from __future__ import annotations

import asyncio
import base64
from dataclasses import fields
from decimal import Decimal
import inspect
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


TICKERS = ("KXTENNIS-MATCH-HOME", "KXTENNIS-MATCH-AWAY")
MARKET_ID = "9b0f6b43-5b68-4f9f-9f02-9a2d1b8ac1a1"


def _wire(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _clock_observer(
    wall_ns: int = 1_000,
    monotonic_ns: int = 100,
    uncertainty_ns: int = 3,
):
    from inci_tennis_io.kalshi_readonly import KalshiClockObservation

    return lambda: KalshiClockObservation(
        wall_ns,
        monotonic_ns,
        uncertainty_ns,
    )


def _snapshot(*, levels: int = 2) -> bytes:
    yes = [[f"0.{8 + index:02d}00", "300.00"] for index in range(levels)]
    no = [[f"0.{54 + index:02d}00", "20.00"] for index in range(levels)]
    return _wire(
        {
            "type": "orderbook_snapshot",
            "sid": 2,
            "seq": 2,
            "msg": {
                "market_ticker": TICKERS[0],
                "market_id": MARKET_ID,
                "yes_dollars_fp": yes,
                "no_dollars_fp": no,
            },
        }
    )


def _delta() -> bytes:
    return _wire(
        {
            "type": "orderbook_delta",
            "sid": 2,
            "seq": 3,
            "msg": {
                "market_ticker": TICKERS[0],
                "market_id": MARKET_ID,
                "price_dollars": "0.960",
                "delta_fp": "-54.00",
                "side": "yes",
                "ts": "2022-11-22T20:44:01Z",
                "ts_ms": 1669149841000,
            },
        }
    )


def _trade() -> bytes:
    return _wire(
        {
            "type": "trade",
            "sid": 11,
            "msg": {
                "trade_id": "d91bc706-ee49-470d-82d8-11418bda6fed",
                "market_ticker": TICKERS[0],
                "yes_price_dollars": "0.360",
                "no_price_dollars": "0.640",
                "count_fp": "136.00",
                "taker_side": "no",
                "ts": 1669149841,
                "ts_ms": 1669149841000,
            },
        }
    )


def _book_snapshot(
    ticker: str,
    sequence: int,
    *,
    sid: int = 2,
    yes: list[list[str]] | None = None,
    no: list[list[str]] | None = None,
) -> bytes:
    message: dict[str, object] = {
        "market_ticker": ticker,
        "market_id": MARKET_ID,
    }
    if yes is not None:
        message["yes_dollars_fp"] = yes
    if no is not None:
        message["no_dollars_fp"] = no
    return _wire(
        {
            "type": "orderbook_snapshot",
            "sid": sid,
            "seq": sequence,
            "msg": message,
        }
    )


def _book_delta(
    ticker: str,
    sequence: int,
    *,
    side: str,
    price: str,
    delta: str,
    sid: int = 2,
) -> bytes:
    return _wire(
        {
            "type": "orderbook_delta",
            "sid": sid,
            "seq": sequence,
            "msg": {
                "market_ticker": ticker,
                "market_id": MARKET_ID,
                "price_dollars": price,
                "delta_fp": delta,
                "side": side,
                "ts": "2026-08-01T12:34:56.789Z",
                "ts_ms": 1785587696789,
            },
        }
    )


class _FakeSocket:
    def __init__(self, frames: list[bytes] | None = None) -> None:
        self.frames = list(frames or [])
        self.sent: list[str] = []
        self.receive_decode_values: list[bool] = []
        self.close_calls = 0

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self, *, decode: bool) -> bytes:
        self.receive_decode_values.append(decode)
        if not self.frames:
            await asyncio.Future()
        return self.frames.pop(0)

    async def close(self) -> None:
        self.close_calls += 1


class _NeverResolvingSendSocket(_FakeSocket):
    def __init__(self, *, hang_on_send: int) -> None:
        super().__init__()
        self.hang_on_send = hang_on_send
        self.send_calls = 0

    async def send(self, message: str) -> None:
        self.sent.append(message)
        self.send_calls += 1
        if self.send_calls == self.hang_on_send:
            await asyncio.Future()


class _Connector:
    def __init__(self, socket: _FakeSocket) -> None:
        self.socket = socket
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def __call__(self, uri: str, **kwargs: object) -> _FakeSocket:
        self.calls.append((uri, kwargs))
        return self.socket


class _ScopeContent:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def iter_chunked(self, chunk_size: int):
        if chunk_size != 65_536:
            raise AssertionError("unexpected chunk size")
        yield self.payload


class _ScopeResponse:
    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        self.status = status
        self.headers = {
            "Content-Encoding": "identity",
            "Content-Length": str(len(payload)),
        }
        self.content = _ScopeContent(payload)


class _ScopeRequestContext:
    def __init__(self, response: _ScopeResponse) -> None:
        self.response = response
        self.exited = False

    async def __aenter__(self) -> _ScopeResponse:
        return self.response

    async def __aexit__(self, *_: object) -> None:
        self.exited = True


class _ScopeSession:
    def __init__(self, response: _ScopeResponse) -> None:
        self.request = _ScopeRequestContext(response)
        self.calls: list[dict[str, object]] = []
        self.close_calls = 0

    def get(self, url: str, **kwargs: object) -> _ScopeRequestContext:
        self.calls.append({"url": url, **kwargs})
        return self.request

    async def close(self) -> None:
        self.close_calls += 1


class _ScopeSessionFactory:
    def __init__(self, response: _ScopeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []
        self.sessions: list[_ScopeSession] = []

    def __call__(self, **kwargs: object) -> _ScopeSession:
        self.calls.append(kwargs)
        session = _ScopeSession(self.response)
        self.sessions.append(session)
        return session


def _scope_payload(api_key_id: str, scopes: list[str]) -> bytes:
    return _wire(
        {
            "api_keys": [
                {
                    "api_key_id": api_key_id,
                    "name": "Inci read-only shadow",
                    "scopes": scopes,
                }
            ]
        }
    )


class _KeyFixture:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        self.path = Path(self.directory.name) / "kalshi-private.pem"
        self.path.write_bytes(
            self.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        os.chmod(self.path, 0o600)

    def close(self) -> None:
        self.directory.cleanup()


class KalshiReadOnlyTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from inci_tennis_io.kalshi_readonly import KalshiReadOnlyCredentials

        self.key = _KeyFixture()
        self.credentials = KalshiReadOnlyCredentials(
            api_key_id="11111111-2222-3333-4444-555555555555",
            private_key_path=self.key.path,
        )
        self.scope_session_factory = _ScopeSessionFactory(
            _ScopeResponse(_scope_payload(self.credentials.api_key_id, ["read"]))
        )

    def tearDown(self) -> None:
        self.key.close()

    async def test_open_signs_only_the_fixed_get_path_and_opens_one_socket(self) -> None:
        from inci_tennis_io.kalshi_readonly import (
            KALSHI_API_KEYS_ENDPOINT,
            KALSHI_API_KEYS_PATH,
            KALSHI_WEBSOCKET_ENDPOINT,
            KALSHI_WEBSOCKET_PATH,
            KalshiReadOnlyError,
            KalshiReadOnlyTransport,
        )

        socket = _FakeSocket()
        connector = _Connector(socket)
        transport = KalshiReadOnlyTransport(
            credentials=self.credentials,
            market_tickers=TICKERS,
            connector=connector,
            scope_session_factory=self.scope_session_factory,
            clock_observer=_clock_observer(
                1_785_650_400_123_456_789,
                10,
                7,
            ),
        )

        await transport.open_readonly()
        with self.assertRaisesRegex(KalshiReadOnlyError, "kalshi_ws_state_invalid"):
            await transport.open_readonly()

        self.assertEqual(
            self.scope_session_factory.calls,
            [
                {
                    "total_timeout_seconds": 5,
                    "connect_timeout_seconds": 2,
                    "read_timeout_seconds": 3,
                    "trust_env": False,
                    "auto_decompress": False,
                }
            ],
        )
        scope_session = self.scope_session_factory.sessions[0]
        self.assertEqual(len(scope_session.calls), 1)
        scope_call = scope_session.calls[0]
        self.assertEqual(scope_call["url"], KALSHI_API_KEYS_ENDPOINT)
        self.assertEqual(scope_call["allow_redirects"], False)
        self.assertEqual(scope_call["auto_decompress"], False)
        self.assertIsNone(scope_call["proxy"])
        scope_headers = scope_call["headers"]
        self.assertEqual(scope_headers["accept"], "application/json")
        self.assertEqual(scope_headers["accept-encoding"], "identity")
        scope_timestamp = scope_headers["KALSHI-ACCESS-TIMESTAMP"]
        scope_signature = base64.b64decode(
            scope_headers["KALSHI-ACCESS-SIGNATURE"]
        )
        self.key.private_key.public_key().verify(
            scope_signature,
            (scope_timestamp + "GET" + KALSHI_API_KEYS_PATH).encode("ascii"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256().digest_size,
            ),
            hashes.SHA256(),
        )
        self.assertTrue(scope_session.request.exited)
        self.assertEqual(scope_session.close_calls, 1)
        self.assertEqual(len(connector.calls), 1)
        uri, kwargs = connector.calls[0]
        self.assertEqual(uri, KALSHI_WEBSOCKET_ENDPOINT)
        self.assertEqual(KALSHI_WEBSOCKET_PATH, "/trade-api/ws/v2")
        headers = kwargs["additional_headers"]
        self.assertEqual(
            headers["KALSHI-ACCESS-KEY"],
            self.credentials.api_key_id,
        )
        timestamp = headers["KALSHI-ACCESS-TIMESTAMP"]
        self.assertEqual(timestamp, "1785650400123")
        signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
        self.key.private_key.public_key().verify(
            signature,
            (timestamp + "GET" + KALSHI_WEBSOCKET_PATH).encode("ascii"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256().digest_size,
            ),
            hashes.SHA256(),
        )
        self.assertEqual(kwargs["max_size"], 1_048_576)

    async def test_scope_preflight_failures_never_reach_websocket(self) -> None:
        from inci_tennis_io.kalshi_readonly import (
            KalshiReadOnlyError,
            KalshiReadOnlyTransport,
        )

        other_key = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        cases = (
            (
                "write",
                _ScopeResponse(_scope_payload(self.credentials.api_key_id, ["write"])),
                "kalshi_ws_key_scope_not_read_only",
            ),
            (
                "full",
                _ScopeResponse(
                    _scope_payload(self.credentials.api_key_id, ["read", "write"])
                ),
                "kalshi_ws_key_scope_not_read_only",
            ),
            (
                "missing",
                _ScopeResponse(_scope_payload(other_key, ["read"])),
                "kalshi_ws_key_scope_not_read_only",
            ),
            (
                "duplicate-json-key",
                _ScopeResponse(b'{"api_keys":[],"api_keys":[]}'),
                "kalshi_ws_scope_response_invalid",
            ),
            (
                "redirect",
                _ScopeResponse(b"{}", status=302),
                "kalshi_ws_scope_redirect_rejected",
            ),
        )
        for label, response, code in cases:
            with self.subTest(label=label):
                connector = _Connector(_FakeSocket())
                transport = KalshiReadOnlyTransport(
                    credentials=self.credentials,
                    market_tickers=TICKERS,
                    connector=connector,
                    scope_session_factory=_ScopeSessionFactory(response),
                    clock_observer=_clock_observer(),
                )
                with self.assertRaisesRegex(KalshiReadOnlyError, code):
                    await transport.open_readonly()
                self.assertEqual(connector.calls, [])

    async def test_reconnect_is_serial_and_increments_physical_generation(self) -> None:
        from inci_tennis_io.kalshi_readonly import KalshiReadOnlyTransport

        socket = _FakeSocket([b"first", b"second"])
        connector = _Connector(socket)
        transport = KalshiReadOnlyTransport(
            credentials=self.credentials,
            market_tickers=TICKERS,
            connector=connector,
            scope_session_factory=self.scope_session_factory,
            clock_observer=_clock_observer(),
        )

        await transport.open_readonly()
        await transport.subscribe()
        first = await transport.receive_one(0.5)
        await transport.close()
        await transport.open_readonly()
        await transport.subscribe()
        second = await transport.receive_one(0.5)

        self.assertEqual(len(connector.calls), 2)
        self.assertEqual(socket.close_calls, 1)
        self.assertEqual(first.physical_connection_generation, 1)
        self.assertEqual(second.physical_connection_generation, 2)

    async def test_subscription_and_snapshot_commands_are_exact_and_read_only(self) -> None:
        from inci_tennis_io.kalshi_readonly import KalshiReadOnlyTransport

        socket = _FakeSocket()
        transport = KalshiReadOnlyTransport(
            credentials=self.credentials,
            market_tickers=TICKERS,
            connector=_Connector(socket),
            scope_session_factory=self.scope_session_factory,
            clock_observer=_clock_observer(),
        )
        await transport.open_readonly()
        await transport.subscribe()
        await transport.request_snapshot(27)
        await transport.close()

        self.assertEqual(
            [json.loads(item) for item in socket.sent],
            [
                {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta"],
                        "market_tickers": list(TICKERS),
                        "use_yes_price": True,
                    },
                },
                {
                    "id": 2,
                    "cmd": "update_subscription",
                    "params": {
                        "sids": [27],
                        "market_tickers": list(TICKERS),
                        "action": "get_snapshot",
                    },
                },
            ],
        )
        self.assertEqual(socket.close_calls, 1)
        self.assertFalse(hasattr(transport, "send"))
        self.assertFalse(hasattr(transport, "request"))

    async def test_subscribe_send_timeout_is_sanitized_and_cleanup_stays_bounded(
        self,
    ) -> None:
        """Catches a stalled subscribe send blocking terminal cleanup forever."""

        import inci_tennis_io.kalshi_readonly as readonly
        from inci_tennis_io.kalshi_readonly import (
            KalshiReadOnlyError,
            KalshiReadOnlyTransport,
        )

        socket = _NeverResolvingSendSocket(hang_on_send=1)
        transport = KalshiReadOnlyTransport(
            credentials=self.credentials,
            market_tickers=TICKERS,
            connector=_Connector(socket),
            scope_session_factory=self.scope_session_factory,
            clock_observer=_clock_observer(),
        )
        await transport.open_readonly()
        try:
            with patch.object(
                readonly,
                "_SEND_TIMEOUT_SECONDS",
                0.001,
            ):
                with self.assertRaisesRegex(
                    KalshiReadOnlyError,
                    "^kalshi_ws_send_timeout$",
                ):
                    try:
                        await asyncio.wait_for(transport.subscribe(), timeout=0.1)
                    except TimeoutError:
                        self.fail("subscribe send remained unbounded")
        finally:
            await asyncio.wait_for(transport.close(), timeout=0.1)

        self.assertEqual(socket.send_calls, 1)
        self.assertEqual(socket.close_calls, 1)

    async def test_snapshot_send_timeout_is_sanitized_and_cleanup_stays_bounded(
        self,
    ) -> None:
        """Catches a stalled snapshot send blocking terminal cleanup forever."""

        import inci_tennis_io.kalshi_readonly as readonly
        from inci_tennis_io.kalshi_readonly import (
            KalshiReadOnlyError,
            KalshiReadOnlyTransport,
        )

        socket = _NeverResolvingSendSocket(hang_on_send=2)
        transport = KalshiReadOnlyTransport(
            credentials=self.credentials,
            market_tickers=TICKERS,
            connector=_Connector(socket),
            scope_session_factory=self.scope_session_factory,
            clock_observer=_clock_observer(),
        )
        await transport.open_readonly()
        await transport.subscribe()
        try:
            with patch.object(
                readonly,
                "_SEND_TIMEOUT_SECONDS",
                0.001,
            ):
                with self.assertRaisesRegex(
                    KalshiReadOnlyError,
                    "^kalshi_ws_send_timeout$",
                ):
                    try:
                        await asyncio.wait_for(
                            transport.request_snapshot(27),
                            timeout=0.1,
                        )
                    except TimeoutError:
                        self.fail("snapshot send remained unbounded")
        finally:
            await asyncio.wait_for(transport.close(), timeout=0.1)

        self.assertEqual(socket.send_calls, 2)
        self.assertEqual(socket.close_calls, 1)

    async def test_receive_is_opaque_exact_bounded_and_clocked(self) -> None:
        from inci_tennis_io.kalshi_readonly import (
            KalshiRawFrame,
            KalshiReadOnlyTransport,
        )

        payload = b'{ "opaque" : [1, 2, 3] }'
        socket = _FakeSocket([payload])
        from inci_tennis_io.kalshi_readonly import KalshiClockObservation

        observations = iter(
            (
                KalshiClockObservation(1_000, 100, 3),
                KalshiClockObservation(5_000, 109, 11),
            )
        )
        transport = KalshiReadOnlyTransport(
            credentials=self.credentials,
            market_tickers=TICKERS,
            connector=_Connector(socket),
            scope_session_factory=self.scope_session_factory,
            clock_observer=lambda: next(observations),
        )
        await transport.open_readonly()
        await transport.subscribe()

        frame = await transport.receive_one(0.5)

        self.assertIs(type(frame), KalshiRawFrame)
        self.assertEqual(
            tuple(item.name for item in fields(frame)),
            (
                "payload",
                "captured_wall_ns",
                "captured_monotonic_ns",
                "clock_uncertainty_ns",
                "physical_connection_generation",
            ),
        )
        self.assertEqual(frame.payload, payload)
        self.assertEqual(frame.captured_wall_ns, 5_000)
        self.assertEqual(frame.captured_monotonic_ns, 109)
        self.assertEqual(frame.clock_uncertainty_ns, 11)
        self.assertEqual(frame.physical_connection_generation, 1)
        self.assertEqual(socket.receive_decode_values, [False])
        self.assertEqual(frame.qualification, "unqualified_shadow")
        self.assertEqual(len(frame.raw_sha256), 64)
        with self.assertRaises(Exception):
            frame.payload = b"changed"  # type: ignore[misc]

    async def test_receive_rejects_text_oversize_timeout_and_before_subscribe(self) -> None:
        from inci_tennis_io.kalshi_readonly import (
            KalshiReadOnlyError,
            KalshiReadOnlyTransport,
        )

        cases = (
            (["not-bytes"], "kalshi_ws_frame_type_invalid"),
            ([b"x" * 1_048_577], "kalshi_ws_frame_oversize"),
            ([], "kalshi_ws_receive_timeout"),
        )
        for frames, code in cases:
            with self.subTest(code=code):
                socket = _FakeSocket(frames)  # type: ignore[arg-type]
                transport = KalshiReadOnlyTransport(
                    credentials=self.credentials,
                    market_tickers=TICKERS,
                    connector=_Connector(socket),
                    scope_session_factory=self.scope_session_factory,
                    clock_observer=_clock_observer(),
                )
                await transport.open_readonly()
                if code == "kalshi_ws_frame_type_invalid":
                    with self.assertRaisesRegex(
                        KalshiReadOnlyError,
                        "kalshi_ws_state_invalid",
                    ):
                        await transport.receive_one(0.01)
                await transport.subscribe()
                with self.assertRaisesRegex(KalshiReadOnlyError, code):
                    await transport.receive_one(0.001)

        valid = KalshiReadOnlyTransport(
            credentials=self.credentials,
            market_tickers=TICKERS,
            connector=_Connector(_FakeSocket([b"valid"])),
            scope_session_factory=self.scope_session_factory,
            clock_observer=_clock_observer(),
        )
        await valid.open_readonly()
        await valid.subscribe()
        with self.assertRaisesRegex(
            KalshiReadOnlyError,
            "kalshi_ws_timeout_invalid",
        ):
            await valid.receive_one(float("nan"))

    async def test_cancelled_open_can_be_closed_without_masking_cancellation(self) -> None:
        """Catches cleanup replacing an interrupted open with a state error."""

        from inci_tennis_io.kalshi_readonly import KalshiReadOnlyTransport

        async def cancelled_connector(*_: object, **__: object) -> object:
            raise asyncio.CancelledError("cancelled-open")

        transport = KalshiReadOnlyTransport(
            credentials=self.credentials,
            market_tickers=TICKERS,
            connector=cancelled_connector,
            scope_session_factory=self.scope_session_factory,
            clock_observer=_clock_observer(),
        )
        with self.assertRaises(asyncio.CancelledError):
            await transport.open_readonly()
        await transport.close()
        await transport.close()

    async def test_cancelled_close_retains_socket_for_idempotent_retry(self) -> None:
        """Catches cancellation orphaning a socket before close completes."""

        from inci_tennis_io.kalshi_readonly import KalshiReadOnlyTransport

        class CancelOnceSocket(_FakeSocket):
            def __init__(self) -> None:
                super().__init__()
                self.close_started = asyncio.Event()

            async def close(self) -> None:
                self.close_calls += 1
                if self.close_calls == 1:
                    self.close_started.set()
                    await asyncio.Future()

        socket = CancelOnceSocket()
        transport = KalshiReadOnlyTransport(
            credentials=self.credentials,
            market_tickers=TICKERS,
            connector=_Connector(socket),
            scope_session_factory=self.scope_session_factory,
            clock_observer=_clock_observer(),
        )
        await transport.open_readonly()

        first_close = asyncio.create_task(transport.close())
        await socket.close_started.wait()
        first_close.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first_close

        await transport.close()
        await transport.close()
        self.assertEqual(socket.close_calls, 2)

    def test_constructor_requires_exactly_two_distinct_safe_tickers(self) -> None:
        from inci_tennis_io.kalshi_readonly import (
            KalshiReadOnlyError,
            KalshiReadOnlyTransport,
        )

        invalid = (
            (TICKERS[0],),
            (TICKERS[0], TICKERS[0]),
            (TICKERS[0].lower(), TICKERS[1]),
            (TICKERS[0], "BAD/TICKER"),
            [TICKERS[0], TICKERS[1]],
        )
        for tickers in invalid:
            with self.subTest(tickers=tickers):
                with self.assertRaisesRegex(
                    KalshiReadOnlyError,
                    "kalshi_ws_tickers_invalid",
                ):
                    KalshiReadOnlyTransport(
                        credentials=self.credentials,
                        market_tickers=tickers,  # type: ignore[arg-type]
                        connector=_Connector(_FakeSocket()),
                        scope_session_factory=self.scope_session_factory,
                        clock_observer=_clock_observer(),
                    )

    def test_credentials_and_failures_are_redacted_and_environment_free(self) -> None:
        from inci_tennis_io.kalshi_readonly import (
            KalshiReadOnlyCredentials,
            KalshiReadOnlyError,
            KalshiReadOnlyTransport,
        )

        secret_id = "SECRET-KEY-ID"
        secret_path = Path(self.key.directory.name) / "secret-name.pem"
        credential = KalshiReadOnlyCredentials(secret_id, secret_path)
        self.assertEqual(repr(credential), "<KalshiReadOnlyCredentials redacted>")
        transport = KalshiReadOnlyTransport(
            credentials=credential,
            market_tickers=TICKERS,
            connector=_Connector(_FakeSocket()),
            scope_session_factory=self.scope_session_factory,
            clock_observer=_clock_observer(),
        )
        with self.assertRaises(KalshiReadOnlyError) as caught:
            asyncio.run(transport.open_readonly())
        diagnostic = repr(caught.exception) + str(caught.exception) + repr(transport)
        self.assertNotIn(secret_id, diagnostic)
        self.assertNotIn(str(secret_path), diagnostic)
        source = inspect.getsource(inspect.getmodule(KalshiReadOnlyTransport))
        self.assertNotIn("os.environ", source)
        self.assertNotIn("getenv", source)

    def test_static_surface_contains_no_order_or_rest_mutation_capability(self) -> None:
        import inci_tennis_io.kalshi_readonly as module

        source = Path(module.__file__).read_text(encoding="utf-8").casefold()
        for forbidden in (
            '"post"',
            '"put"',
            '"patch"',
            '"delete"',
            "/portfolio/",
            "legacy executor",
            "kalshiclient",
            "requests.",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class KalshiCurrentWireParserTests(unittest.TestCase):
    def test_parses_subscribed_ok_and_redacted_error(self) -> None:
        from inci_tennis_adapters.kalshi_v2 import (
            KalshiCommandError,
            KalshiCommandOk,
            KalshiSubscribed,
            parse_unqualified_shadow_message,
        )

        subscribed = parse_unqualified_shadow_message(
            _wire(
                {
                    "id": 1,
                    "type": "subscribed",
                    "msg": {"channel": "orderbook_delta", "sid": 2},
                }
            )
        )
        ok = parse_unqualified_shadow_message(
            _wire(
                {
                    "id": 2,
                    "sid": 2,
                    "seq": 4,
                    "type": "ok",
                    "msg": {"market_tickers": list(TICKERS)},
                }
            )
        )
        error = parse_unqualified_shadow_message(
            _wire(
                {
                    "id": 7,
                    "type": "error",
                    "msg": {"code": 6, "msg": "Already subscribed"},
                }
            )
        )

        self.assertIs(type(subscribed), KalshiSubscribed)
        self.assertEqual((subscribed.request_id, subscribed.sid), (1, 2))
        self.assertIs(type(ok), KalshiCommandOk)
        self.assertEqual(ok.market_tickers, TICKERS)
        self.assertIs(type(error), KalshiCommandError)
        self.assertEqual(error.error_code, 6)
        self.assertNotIn("Already subscribed", repr(error))
        for value in (subscribed, ok, error):
            self.assertEqual(value.qualification, "unqualified_shadow")
            self.assertEqual(len(value.raw_sha256), 64)

    def test_parses_snapshot_decimal_ladders_exactly(self) -> None:
        from inci_tennis_adapters.kalshi_v2 import (
            KalshiOrderbookSnapshot,
            parse_unqualified_shadow_message,
        )

        result = parse_unqualified_shadow_message(_snapshot())

        self.assertIs(type(result), KalshiOrderbookSnapshot)
        self.assertEqual(result.market_ticker, TICKERS[0])
        self.assertEqual(result.market_id, MARKET_ID)
        self.assertEqual(result.sid, 2)
        self.assertEqual(result.sequence, 2)
        self.assertEqual(
            result.yes_levels,
            (
                (Decimal("0.0800"), Decimal("300.00")),
                (Decimal("0.0900"), Decimal("300.00")),
            ),
        )
        self.assertEqual(
            result.no_levels,
            (
                (Decimal("0.5400"), Decimal("20.00")),
                (Decimal("0.5500"), Decimal("20.00")),
            ),
        )

    def test_parses_delta_decimal_fields_exactly(self) -> None:
        from inci_tennis_adapters.kalshi_v2 import (
            KalshiOrderbookDelta,
            parse_unqualified_shadow_message,
        )

        delta = parse_unqualified_shadow_message(_delta())

        self.assertIs(type(delta), KalshiOrderbookDelta)
        self.assertEqual(delta.price_dollars, Decimal("0.960"))
        self.assertEqual(delta.delta, Decimal("-54.00"))
        self.assertEqual(delta.side, "yes")
        self.assertEqual(delta.source_ts, "2022-11-22T20:44:01Z")
        self.assertEqual(delta.source_ts_ms, 1669149841000)

    def test_delta_accepts_each_documented_optional_timestamp_as_absent(self) -> None:
        from inci_tennis_adapters.kalshi_v2 import (
            KalshiOrderbookDelta,
            parse_unqualified_shadow_message,
        )

        without_ts = json.loads(_delta())
        del without_ts["msg"]["ts"]
        result_without_ts = parse_unqualified_shadow_message(_wire(without_ts))
        self.assertIs(type(result_without_ts), KalshiOrderbookDelta)
        self.assertIsNone(result_without_ts.source_ts)
        self.assertEqual(result_without_ts.source_ts_ms, 1669149841000)

        without_ts_ms = json.loads(_delta())
        del without_ts_ms["msg"]["ts_ms"]
        result_without_ts_ms = parse_unqualified_shadow_message(
            _wire(without_ts_ms)
        )
        self.assertEqual(
            result_without_ts_ms.source_ts,
            "2022-11-22T20:44:01Z",
        )
        self.assertIsNone(result_without_ts_ms.source_ts_ms)

    def test_rejects_unknown_duplicate_malformed_and_oversize_inputs(self) -> None:
        from inci_tennis_adapters.kalshi_v2 import (
            KalshiWireContractError,
            parse_unqualified_shadow_message,
        )

        malformed = (
            b"not-json",
            b'{"type":"trade","type":"trade","sid":1,"msg":{}}',
            _wire({"type": "unknown", "sid": 1, "seq": 1, "msg": {}}),
            _wire(
                {
                    "type": "orderbook_delta",
                    "sid": 2,
                    "seq": 3,
                    "msg": {
                        "market_ticker": TICKERS[0],
                        "market_id": MARKET_ID,
                        "price_dollars": "NaN",
                        "delta_fp": "1.00",
                        "side": "yes",
                        "ts": "2022-11-22T20:44:01Z",
                        "ts_ms": 1669149841000,
                    },
                }
            ),
            b"x" * 1_048_577,
        )
        for raw in malformed:
            with self.subTest(size=len(raw)):
                with self.assertRaisesRegex(
                    KalshiWireContractError,
                    "kalshi_ws_contract_invalid",
                ):
                    parse_unqualified_shadow_message(raw)


class KalshiReviewedWireContractTests(unittest.TestCase):
    def test_official_empty_snapshot_sides_and_delta_metadata_are_valid(self) -> None:
        """Catches requiring optional snapshot sides or rejecting documented metadata."""

        from inci_tennis_adapters.kalshi_v2 import (
            KalshiOrderbookDelta,
            KalshiOrderbookSnapshot,
            parse_unqualified_book_message,
        )

        empty = parse_unqualified_book_message(
            _book_snapshot(TICKERS[0], 1, yes=None, no=None)
        )
        self.assertIs(type(empty), KalshiOrderbookSnapshot)
        self.assertEqual((empty.yes_levels, empty.no_levels), ((), ()))

        documented = json.loads(_book_delta(
            TICKERS[0], 2, side="yes", price="0.5600", delta="1.00"
        ))
        documented["msg"]["client_order_id"] = "client-order-123"
        documented["msg"]["subaccount"] = 7
        parsed = parse_unqualified_book_message(_wire(documented))
        self.assertIs(type(parsed), KalshiOrderbookDelta)
        self.assertEqual(parsed.subaccount, 7)
        self.assertIsNone(parsed.client_order_id)
        self.assertEqual(len(parsed.client_order_id_sha256), 64)

    def test_book_parser_rejects_unsubscribed_trade_surface(self) -> None:
        """Catches exposing a trade shape that this book-only transport never receives."""

        from inci_tennis_adapters.kalshi_v2 import (
            KalshiWireContractError,
            parse_unqualified_book_message,
        )

        canonical = json.loads(_trade())
        canonical["msg"].update(
            taker_outcome_side="no",
            taker_book_side="ask",
        )
        with self.assertRaises(KalshiWireContractError):
            parse_unqualified_book_message(_wire(canonical))

    def test_strict_sequence_numeric_identity_and_timestamp_contract(self) -> None:
        """Catches accepting seq zero, loose fixed point, non-UUID IDs, or bad clocks."""

        from inci_tennis_adapters.kalshi_v2 import (
            KalshiWireContractError,
            parse_unqualified_book_message,
        )

        invalid: list[bytes] = []
        seq_zero = json.loads(_book_snapshot(TICKERS[0], 1, yes=[], no=[]))
        seq_zero["seq"] = 0
        invalid.append(_wire(seq_zero))
        six_place_price = json.loads(_book_delta(
            TICKERS[0], 2, side="yes", price="0.560000", delta="1.00"
        ))
        invalid.append(_wire(six_place_price))
        six_place_delta = json.loads(_book_delta(
            TICKERS[0], 2, side="yes", price="0.5600", delta="1.000000"
        ))
        invalid.append(_wire(six_place_delta))
        bad_uuid = json.loads(_book_snapshot(TICKERS[0], 1, yes=[], no=[]))
        bad_uuid["msg"]["market_id"] = "not-a-uuid"
        invalid.append(_wire(bad_uuid))
        impossible_date = json.loads(_book_delta(
            TICKERS[0], 2, side="yes", price="0.5600", delta="1.00"
        ))
        impossible_date["msg"]["ts"] = "2026-99-99T99:99:99Z"
        invalid.append(_wire(impossible_date))
        contradictory_clock = json.loads(_book_delta(
            TICKERS[0], 2, side="yes", price="0.5600", delta="1.00"
        ))
        contradictory_clock["msg"]["ts_ms"] += 1
        invalid.append(_wire(contradictory_clock))

        for raw in invalid:
            with self.subTest(raw=raw[:100]):
                with self.assertRaises(KalshiWireContractError):
                    parse_unqualified_book_message(raw)

    def test_snapshot_ladders_are_strictly_ascending_on_the_wire(self) -> None:
        """Catches silently accepting malformed ordering that obscures best levels."""

        from inci_tennis_adapters.kalshi_v2 import (
            KalshiWireContractError,
            parse_unqualified_book_message,
        )

        descending = _book_snapshot(
            TICKERS[0],
            1,
            yes=[["0.5600", "1.00"], ["0.5500", "2.00"]],
            no=[],
        )
        with self.assertRaises(KalshiWireContractError):
            parse_unqualified_book_message(descending)

    def test_rejects_excessive_ladders_nonpositive_size_and_unknown_fields(self) -> None:
        from inci_tennis_adapters.kalshi_v2 import (
            KalshiWireContractError,
            parse_unqualified_shadow_message,
        )

        too_many = _snapshot(levels=1025)
        zero_quantity = json.loads(_snapshot())
        zero_quantity["msg"]["yes_dollars_fp"][0][1] = "0.00"
        unknown_field = json.loads(_delta())
        unknown_field["msg"]["unexpected"] = True
        for raw in (
            too_many,
            _wire(zero_quantity),
            _wire(unknown_field),
        ):
            with self.subTest(size=len(raw)):
                with self.assertRaises(KalshiWireContractError):
                    parse_unqualified_shadow_message(raw)


class KalshiReviewedTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from inci_tennis_io.kalshi_readonly import KalshiReadOnlyCredentials

        self.key = _KeyFixture()
        os.chmod(self.key.path, stat.S_IRUSR | stat.S_IWUSR)
        self.credentials = KalshiReadOnlyCredentials(
            api_key_id="11111111-2222-3333-4444-555555555555",
            private_key_path=self.key.path,
        )
        self.scope_session_factory = _ScopeSessionFactory(
            _ScopeResponse(_scope_payload(self.credentials.api_key_id, ["read"]))
        )

    def tearDown(self) -> None:
        self.key.close()

    def test_pinned_websockets_surface_matches_default_connector(self) -> None:
        """Catches a dependency upgrade that removes the exact no-network API used."""

        import cryptography
        import websockets
        from websockets.asyncio.client import ClientConnection, connect

        self.assertEqual(websockets.__version__, "16.1.1")
        self.assertEqual(cryptography.__version__, "49.0.0")
        self.assertIn("additional_headers", inspect.signature(connect).parameters)
        self.assertIn("decode", inspect.signature(ClientConnection.recv).parameters)

    async def test_default_connector_disables_proxy_and_refuses_redirects(self) -> None:
        """Catches forwarding signed Kalshi headers through a proxy or redirect."""

        from unittest.mock import patch

        from inci_tennis_io.kalshi_readonly import _default_connector

        redirect_error = RuntimeError("redirect response")
        socket = _FakeSocket()
        observed: dict[str, object] = {}

        class RedirectFollowingConnect:
            def __init__(self, uri: str, **kwargs: object) -> None:
                observed["uri"] = uri
                observed["kwargs"] = kwargs

            def process_redirect(self, _: Exception) -> object:
                return "wss://attacker.invalid/collect"

            def __await__(self):
                async def resolve() -> object:
                    observed["redirect_result"] = self.process_redirect(
                        redirect_error
                    )
                    return socket

                return resolve().__await__()

        with patch(
            "websockets.asyncio.client.connect",
            RedirectFollowingConnect,
        ):
            result = await _default_connector(
                "wss://external-api-ws.kalshi.com/trade-api/ws/v2",
                additional_headers={"KALSHI-ACCESS-KEY": "sensitive"},
            )

        self.assertIs(result, socket)
        kwargs = observed["kwargs"]
        self.assertIs(type(kwargs), dict)
        self.assertIn("proxy", kwargs)
        self.assertIsNone(kwargs["proxy"])
        self.assertIs(observed["redirect_result"], redirect_error)

    async def test_required_paired_clock_is_copied_after_receive(self) -> None:
        """Catches measuring idle receive duration or making an unmeasured clock claim."""

        from inci_tennis_io.kalshi_readonly import (
            KalshiClockObservation,
            KalshiReadOnlyTransport,
        )

        observations = iter(
            (
                KalshiClockObservation(1000, 100, 3),
                KalshiClockObservation(5000, 500, 7),
            )
        )
        socket = _FakeSocket([b"opaque"])
        transport = KalshiReadOnlyTransport(
            credentials=self.credentials,
            market_tickers=TICKERS,
            connector=_Connector(socket),
            scope_session_factory=self.scope_session_factory,
            clock_observer=lambda: next(observations),
        )
        await transport.open_readonly()
        receipt = await transport.subscribe()
        frame = await transport.receive_one(0.5)

        self.assertEqual(
            (
                receipt.request_id,
                receipt.physical_connection_generation,
                receipt.command,
            ),
            (1, 1, "subscribe"),
        )
        self.assertEqual(
            (
                frame.captured_wall_ns,
                frame.captured_monotonic_ns,
                frame.clock_uncertainty_ns,
            ),
            (5000, 500, 7),
        )

    async def test_snapshot_command_returns_correlatable_typed_receipt(self) -> None:
        """Catches losing the request ID/generation needed to bind an OK response."""

        from inci_tennis_io.kalshi_readonly import (
            KalshiClockObservation,
            KalshiCommandReceipt,
            KalshiReadOnlyTransport,
        )

        transport = KalshiReadOnlyTransport(
            credentials=self.credentials,
            market_tickers=TICKERS,
            connector=_Connector(_FakeSocket()),
            scope_session_factory=self.scope_session_factory,
            clock_observer=lambda: KalshiClockObservation(1000, 100, 3),
        )
        await transport.open_readonly()
        subscribe_receipt = await transport.subscribe()
        snapshot_receipt = await transport.request_snapshot(27)

        self.assertIs(type(subscribe_receipt), KalshiCommandReceipt)
        self.assertIs(type(snapshot_receipt), KalshiCommandReceipt)
        self.assertEqual(snapshot_receipt.request_id, 2)
        self.assertEqual(snapshot_receipt.physical_connection_generation, 1)
        self.assertEqual(snapshot_receipt.command, "get_snapshot")

    async def test_invalid_clock_authority_halts_before_any_later_frame(self) -> None:
        """Catches continuing after the session clock authority violates its contract."""

        from inci_tennis_io.kalshi_readonly import (
            KalshiClockObservation,
            KalshiReadOnlyError,
            KalshiReadOnlyTransport,
        )

        observations = iter(
            (
                KalshiClockObservation(1000, 100, 3),
                object(),
                KalshiClockObservation(2000, 200, 3),
            )
        )
        transport = KalshiReadOnlyTransport(
            credentials=self.credentials,
            market_tickers=TICKERS,
            connector=_Connector(_FakeSocket([b"first", b"second"])),
            scope_session_factory=self.scope_session_factory,
            clock_observer=lambda: next(observations),  # type: ignore[return-value]
        )
        await transport.open_readonly()
        await transport.subscribe()
        with self.assertRaisesRegex(KalshiReadOnlyError, "kalshi_ws_clock_invalid"):
            await transport.receive_one(0.5)
        with self.assertRaisesRegex(KalshiReadOnlyError, "kalshi_ws_state_invalid"):
            await transport.receive_one(0.5)

    async def test_private_key_open_rejects_symlink_and_permissive_mode(self) -> None:
        """Catches following a replaced key path or accepting group/world-readable PEMs."""

        from inci_tennis_io.kalshi_readonly import (
            KalshiClockObservation,
            KalshiReadOnlyCredentials,
            KalshiReadOnlyError,
            KalshiReadOnlyTransport,
        )

        link = Path(self.key.directory.name) / "linked.pem"
        link.symlink_to(self.key.path)
        bad_mode = Path(self.key.directory.name) / "bad-mode.pem"
        bad_mode.write_bytes(self.key.path.read_bytes())
        os.chmod(bad_mode, 0o644)
        for path in (link, bad_mode):
            with self.subTest(path=path.name):
                transport = KalshiReadOnlyTransport(
                    credentials=KalshiReadOnlyCredentials(
                        self.credentials.api_key_id,
                        path,
                    ),
                    market_tickers=TICKERS,
                    connector=_Connector(_FakeSocket()),
                    scope_session_factory=self.scope_session_factory,
                    clock_observer=lambda: KalshiClockObservation(1000, 100, 3),
                )
                with self.assertRaisesRegex(
                    KalshiReadOnlyError,
                    "kalshi_ws_private_key_invalid",
                ):
                    await transport.open_readonly()


class UnqualifiedTwoTickerBookReducerTests(unittest.TestCase):
    @staticmethod
    def _parse(raw: bytes) -> object:
        from inci_tennis_adapters.kalshi_v2 import parse_unqualified_book_message

        return parse_unqualified_book_message(raw)

    @staticmethod
    def _subscribed(request_id: int = 1, sid: int = 2) -> object:
        return UnqualifiedTwoTickerBookReducerTests._parse(
            _wire(
                {
                    "id": request_id,
                    "type": "subscribed",
                    "msg": {"channel": "orderbook_delta", "sid": sid},
                }
            )
        )

    def _ready_reducer(self):
        from inci_tennis_adapters.kalshi_v2 import UnqualifiedTwoTickerBookReducer

        reducer = UnqualifiedTwoTickerBookReducer(TICKERS)
        reducer.begin_subscription(1, 1)
        reducer.apply(self._subscribed(), 1)
        reducer.apply(
            self._parse(
                _book_snapshot(
                    TICKERS[0],
                    1,
                    yes=[["0.2000", "4.00"], ["0.3000", "5.00"]],
                    no=[["0.7000", "6.00"], ["0.8000", "7.00"]],
                )
            ),
            1,
        )
        state = reducer.apply(
            self._parse(
                _book_snapshot(
                    TICKERS[1],
                    2,
                    yes=[["0.4000", "8.00"]],
                    no=[["0.6000", "9.00"]],
                )
            ),
            1,
        )
        return reducer, state

    def test_ack_two_snapshot_barrier_normalizes_yes_scale_and_depth(self) -> None:
        """Catches publishing before both snapshots or treating 0.70 as a no bid."""

        from inci_tennis_adapters.kalshi_v2 import UnqualifiedTwoTickerBookReducer

        reducer = UnqualifiedTwoTickerBookReducer(TICKERS)
        initial = reducer.begin_subscription(1, 1)
        self.assertEqual(initial.status, "awaiting_subscription_ack")
        self.assertTrue(all(view.yes_bid is None for view in initial.views))
        acknowledged = reducer.apply(self._subscribed(), 1)
        self.assertEqual(acknowledged.status, "awaiting_snapshots")

        first = reducer.apply(
            self._parse(
                _book_snapshot(
                    TICKERS[0],
                    1,
                    yes=[["0.2000", "4.00"], ["0.3000", "5.00"]],
                    no=[["0.7000", "6.00"], ["0.8000", "7.00"]],
                )
            ),
            1,
        )
        self.assertEqual(first.status, "awaiting_snapshots")
        self.assertTrue(all(view.yes_bid is None for view in first.views))

        ready = reducer.apply(
            self._parse(
                _book_snapshot(
                    TICKERS[1],
                    2,
                    yes=[["0.4000", "8.00"]],
                    no=[["0.6000", "9.00"]],
                )
            ),
            1,
        )
        self.assertEqual((ready.status, ready.sequence), ("ready", 2))
        first_view = ready.view(TICKERS[0])
        self.assertEqual(
            (
                first_view.yes_bid,
                first_view.yes_ask,
                first_view.no_bid,
                first_view.no_ask,
            ),
            (
                Decimal("0.3000"),
                Decimal("0.7000"),
                Decimal("0.3000"),
                Decimal("0.7000"),
            ),
        )
        self.assertEqual(
            (first_view.yes_bid_depth, first_view.yes_ask_depth),
            (Decimal("5.00"), Decimal("6.00")),
        )
        self.assertFalse(ready.snapshot_needed)
        self.assertEqual(ready.qualification, "unqualified_shadow")

    def test_interleaved_global_sequence_and_signed_delta_update(self) -> None:
        """Catches per-ticker sequence tracking or treating signed delta as replacement."""

        from inci_tennis_adapters.kalshi_v2 import UnqualifiedTwoTickerBookReducer

        reducer = UnqualifiedTwoTickerBookReducer(TICKERS)
        reducer.begin_subscription(1, 1)
        reducer.apply(self._subscribed(), 1)
        reducer.apply(
            self._parse(_book_snapshot(
                TICKERS[0], 1, yes=[["0.3000", "5.00"]], no=[["0.7000", "6.00"]]
            )),
            1,
        )
        reducer.apply(
            self._parse(_book_delta(
                TICKERS[0], 2, side="yes", price="0.3000", delta="2.00"
            )),
            1,
        )
        ready = reducer.apply(
            self._parse(_book_snapshot(
                TICKERS[1], 3, yes=[["0.4000", "8.00"]], no=[["0.6000", "9.00"]]
            )),
            1,
        )
        self.assertEqual(ready.sequence, 3)
        self.assertEqual(
            ready.view(TICKERS[0]).yes_bid_depth,
            Decimal("7.00"),
        )
        removed = reducer.apply(
            self._parse(_book_delta(
                TICKERS[1], 4, side="no", price="0.6000", delta="-9.00"
            )),
            1,
        )
        self.assertEqual(removed.status, "empty_book")
        self.assertFalse(removed.snapshot_needed)
        self.assertTrue(all(view.yes_bid is None for view in removed.views))

    def test_valid_omitted_side_is_empty_not_a_snapshot_gap(self) -> None:
        """Catches looping resnapshot requests for a valid one-sided orderbook."""

        from inci_tennis_adapters.kalshi_v2 import UnqualifiedTwoTickerBookReducer

        reducer = UnqualifiedTwoTickerBookReducer(TICKERS)
        reducer.begin_subscription(1, 1)
        reducer.apply(self._subscribed(), 1)
        reducer.apply(
            self._parse(_book_snapshot(
                TICKERS[0], 1, yes=[["0.3000", "5.00"]], no=None
            )),
            1,
        )
        empty = reducer.apply(
            self._parse(_book_snapshot(
                TICKERS[1], 2, yes=[["0.4000", "8.00"]], no=[["0.6000", "9.00"]]
            )),
            1,
        )
        self.assertEqual(empty.status, "empty_book")
        self.assertFalse(empty.snapshot_needed)
        self.assertTrue(all(view.yes_bid is None for view in empty.views))

        executable = reducer.apply(
            self._parse(_book_delta(
                TICKERS[0], 3, side="no", price="0.7000", delta="2.00"
            )),
            1,
        )
        self.assertEqual(executable.status, "ready")
        self.assertEqual(executable.view(TICKERS[0]).no_bid, Decimal("0.3000"))
        self.assertFalse(executable.snapshot_needed)

    def test_gap_duplicate_and_out_of_order_invalidate_both_books(self) -> None:
        """Catches retaining either candidate view after global sequence discontinuity."""

        for incoming, reason in ((4, "sequence_gap"), (2, "sequence_duplicate"), (1, "sequence_out_of_order")):
            with self.subTest(sequence=incoming):
                reducer, _ = self._ready_reducer()
                invalidated = reducer.apply(
                    self._parse(_book_delta(
                        TICKERS[0], incoming, side="yes", price="0.3000", delta="1.00"
                    )),
                    1,
                )
                self.assertEqual(invalidated.status, "invalidated")
                self.assertEqual(invalidated.reason, reason)
                self.assertTrue(invalidated.snapshot_needed)
                self.assertTrue(all(view.yes_bid is None for view in invalidated.views))

    def test_correlated_ack_and_fresh_two_snapshot_barrier_recover_gap(self) -> None:
        """Catches recovery from an uncorrelated OK or only one fresh snapshot."""

        reducer, _ = self._ready_reducer()
        reducer.apply(
            self._parse(_book_delta(
                TICKERS[0], 9, side="yes", price="0.3000", delta="1.00"
            )),
            1,
        )
        waiting = reducer.expect_snapshot(1, 2, 9)
        self.assertEqual(waiting.status, "awaiting_snapshot_ack")
        ok = self._parse(
            _wire(
                {
                    "id": 9,
                    "sid": 2,
                    "seq": 20,
                    "type": "ok",
                    "msg": {"market_tickers": list(TICKERS)},
                }
            )
        )
        reducer.apply(ok, 1)
        one = reducer.apply(
            self._parse(_book_snapshot(
                TICKERS[0], 21, yes=[["0.3100", "2.00"]], no=[["0.6900", "3.00"]]
            )),
            1,
        )
        self.assertEqual(one.status, "awaiting_snapshots")
        recovered = reducer.apply(
            self._parse(_book_snapshot(
                TICKERS[1], 22, yes=[["0.4100", "4.00"]], no=[["0.5900", "5.00"]]
            )),
            1,
        )
        self.assertEqual((recovered.status, recovered.sequence), ("ready", 22))
        self.assertFalse(recovered.snapshot_needed)

    def test_recovery_pending_deltas_do_not_request_another_snapshot(self) -> None:
        """Catches a get_snapshot loop while the first correlated recovery is pending."""

        reducer, _ = self._ready_reducer()
        reducer.apply(
            self._parse(_book_delta(
                TICKERS[0], 9, side="yes", price="0.3000", delta="1.00"
            )),
            1,
        )
        state = reducer.expect_snapshot(1, 2, 9)
        self.assertFalse(state.snapshot_needed)
        for sequence, ticker in ((10, TICKERS[0]), (11, TICKERS[1])):
            state = reducer.apply(
                self._parse(_book_delta(
                    ticker,
                    sequence,
                    side="yes",
                    price="0.3000",
                    delta="1.00",
                )),
                1,
            )
            self.assertEqual(state.status, "awaiting_snapshot_ack")
            self.assertFalse(state.snapshot_needed)
            self.assertTrue(all(view.yes_bid is None for view in state.views))

    def test_disconnect_reconnect_and_terminal_error_never_reuse_old_book(self) -> None:
        """Catches carrying prices across a physical generation or channel death."""

        reducer, _ = self._ready_reducer()
        disconnected = reducer.disconnect(1)
        self.assertEqual(disconnected.status, "disconnected")
        self.assertTrue(all(view.yes_bid is None for view in disconnected.views))
        next_generation = reducer.begin_subscription(2, 1)
        self.assertEqual(next_generation.generation, 2)
        reducer.apply(self._subscribed(request_id=1, sid=8), 2)
        terminal = reducer.apply(
            self._parse(
                _wire(
                    {
                        "id": 3,
                        "type": "error",
                        "msg": {"code": 25, "msg": "buffer overflow"},
                    }
                )
            ),
            2,
        )
        self.assertEqual(terminal.status, "terminal")
        self.assertEqual(terminal.reason, "terminal_channel_error")
        self.assertFalse(terminal.snapshot_needed)
        self.assertTrue(all(view.yes_bid is None for view in terminal.views))

    def test_subscription_command_errors_are_correlated_and_terminal(self) -> None:
        """Catches treating a failed subscribe as a recoverable snapshot gap."""

        from inci_tennis_adapters.kalshi_v2 import (
            UnqualifiedTwoTickerBookReducer,
        )

        for code in (7, 9, 27):
            with self.subTest(code=code):
                reducer = UnqualifiedTwoTickerBookReducer(TICKERS)
                reducer.begin_subscription(1, 41)
                state = reducer.apply(
                    self._parse(
                        _wire(
                            {
                                "id": 41,
                                "type": "error",
                                "msg": {"code": code, "msg": "rejected"},
                            }
                        )
                    ),
                    1,
                )

                self.assertEqual(state.status, "terminal")
                self.assertEqual(state.reason, "subscription_command_error")
                self.assertFalse(state.snapshot_needed)
                self.assertTrue(
                    all(view.yes_bid is None for view in state.views)
                )

    def test_snapshot_command_errors_are_correlated_terminal_and_never_loop(self) -> None:
        """Catches retry loops after a correlated get_snapshot rejection."""

        for code in (7, 9, 27):
            with self.subTest(code=code):
                reducer, _ = self._ready_reducer()
                reducer.apply(
                    self._parse(
                        _book_delta(
                            TICKERS[0],
                            9,
                            side="yes",
                            price="0.3000",
                            delta="1.00",
                        )
                    ),
                    1,
                )
                reducer.expect_snapshot(1, 2, 51)
                state = reducer.apply(
                    self._parse(
                        _wire(
                            {
                                "id": 51,
                                "type": "error",
                                "msg": {"code": code, "msg": "rejected"},
                            }
                        )
                    ),
                    1,
                )

                self.assertEqual(state.status, "terminal")
                self.assertEqual(state.reason, "snapshot_command_error")
                self.assertFalse(state.snapshot_needed)
                self.assertTrue(
                    all(view.yes_bid is None for view in state.views)
                )

    def test_command_error_request_mismatch_is_terminal_without_attribution(self) -> None:
        """Catches attributing an unrelated command failure to get_snapshot."""

        reducer, _ = self._ready_reducer()
        reducer.apply(
            self._parse(
                _book_delta(
                    TICKERS[0],
                    9,
                    side="yes",
                    price="0.3000",
                    delta="1.00",
                )
            ),
            1,
        )
        reducer.expect_snapshot(1, 2, 51)
        state = reducer.apply(
            self._parse(
                _wire(
                    {
                        "id": 50,
                        "type": "error",
                        "msg": {"code": 9, "msg": "rejected"},
                    }
                )
            ),
            1,
        )

        self.assertEqual(state.status, "terminal")
        self.assertEqual(state.reason, "command_error_correlation_mismatch")
        self.assertFalse(state.snapshot_needed)

if __name__ == "__main__":
    unittest.main()
