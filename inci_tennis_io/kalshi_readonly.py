"""Read-only Kalshi Trade API v2 WebSocket capture.

This module reads market data and nothing else. It signs only the WebSocket
handshake for ``GET /trade-api/ws/v2``, subscribes only to read-only channels,
and exposes no order, position, or portfolio operation of any kind.

Two concerns live here:

``KalshiBookDecoder``
    A pure state machine that converts Kalshi wire frames into the normalized
    ``BookSnapshot`` / ``BookDelta`` / ``MarketLifecycle`` contracts. Kalshi
    publishes *additive* order-book deltas while the normalized contract
    carries the *absolute* resulting quantity for a price level, so the
    decoder tracks the wire book per ticker purely to perform that
    translation. Any desynchronization fails closed into a resnapshot demand
    rather than emitting a book that was never observed.

``KalshiReadOnlyFeed``
    Owns at most one physical WebSocket at a time and allocates a strictly
    newer local trust epoch before any reconnect or forced resnapshot, so a
    delta can never create trust in a book that was not snapshotted first.
"""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
from decimal import Decimal, DecimalException
import json
import os
import time
from typing import Callable, Final, Iterator

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from inci_tennis_expert.contracts import (
    BookDelta,
    BookLevel,
    BookSnapshot,
    ContractSide,
    MarketLifecycle,
    MarketStatus,
)


KALSHI_WS_PATH: Final[str] = "/trade-api/ws/v2"
KALSHI_WS_URL: Final[str] = (
    "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
)
KALSHI_KEY_ID_ENV: Final[str] = "KALSHI_API_KEY_ID"
KALSHI_PRIVATE_KEY_ENV: Final[str] = "KALSHI_PRIVATE_KEY_PEM"

ORDERBOOK_CHANNEL: Final[str] = "orderbook_delta"
LIFECYCLE_CHANNEL: Final[str] = "market_lifecycle_v2"
READ_ONLY_CHANNELS: Final[frozenset[str]] = frozenset(
    {ORDERBOOK_CHANNEL, LIFECYCLE_CHANNEL, "trade"}
)

_SNAPSHOT_TYPE: Final[str] = "orderbook_snapshot"
_DELTA_TYPE: Final[str] = "orderbook_delta"
_LIFECYCLE_TYPE: Final[str] = "market_lifecycle_v2"
_IGNORED_TYPES: Final[frozenset[str]] = frozenset(
    {"subscribed", "unsubscribed", "ok", "trade", "ticker", "pong"}
)

_MAX_LEVELS: Final[int] = 512
_MAX_FRAME_BYTES: Final[int] = 4_194_304
_MAX_TICKERS: Final[int] = 64
_ONE: Final[Decimal] = Decimal("1")
_ZERO: Final[Decimal] = Decimal("0")

_LIFECYCLE_STATUS_BY_EVENT: Final[dict[str, MarketStatus]] = {
    "created": MarketStatus.PREOPEN,
    "activated": MarketStatus.OPEN,
    "deactivated": MarketStatus.SUSPENDED,
    "paused": MarketStatus.SUSPENDED,
    "closed": MarketStatus.CLOSED,
    "determined": MarketStatus.CLOSED,
    "settled": MarketStatus.SETTLED,
}


class KalshiReadOnlyError(RuntimeError):
    """Raised when Kalshi market data cannot be read or trusted."""


class KalshiResnapshotRequired(KalshiReadOnlyError):
    """Raised when a ticker's book must be discarded and re-snapshotted."""


def _fail(code: str) -> None:
    raise KalshiReadOnlyError(code)


def load_kalshi_key_id() -> str | None:
    """Read the API key id at the IO boundary without copying it to config."""
    return os.environ.get(KALSHI_KEY_ID_ENV)


def load_kalshi_private_key_pem() -> str | None:
    """Read the signing key at the IO boundary without copying it to config."""
    return os.environ.get(KALSHI_PRIVATE_KEY_ENV)


def handshake_signature_payload(timestamp_ms: int) -> bytes:
    """Return the exact bytes Kalshi signs for the WebSocket handshake."""
    if type(timestamp_ms) is not int or timestamp_ms <= 0:
        _fail("kalshi_timestamp_invalid")
    return f"{timestamp_ms}GET{KALSHI_WS_PATH}".encode("ascii")


def kalshi_handshake_headers(
    *,
    key_id: str,
    private_key_pem: str,
    timestamp_ms: int,
) -> dict[str, str]:
    """Sign only ``GET /trade-api/ws/v2`` and return the handshake headers."""
    if type(key_id) is not str or not key_id or len(key_id) > 256:
        _fail("kalshi_key_id_invalid")
    if type(private_key_pem) is not str or not private_key_pem:
        _fail("kalshi_private_key_invalid")
    payload = handshake_signature_payload(timestamp_ms)
    try:
        private_key = load_pem_private_key(
            private_key_pem.encode("utf-8"),
            password=None,
        )
        signature = private_key.sign(
            payload,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
    except KalshiReadOnlyError:
        raise
    except Exception:
        _fail("kalshi_signature_failed")
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
        "KALSHI-ACCESS-SIGNATURE": b64encode(signature).decode("ascii"),
    }


def subscribe_command(
    *,
    command_id: int,
    channel: str,
    market_tickers: tuple[str, ...],
) -> dict:
    """Build one read-only subscribe command."""
    if type(command_id) is not int or command_id <= 0:
        _fail("kalshi_command_id_invalid")
    if type(channel) is not str or channel not in READ_ONLY_CHANNELS:
        _fail("kalshi_channel_forbidden")
    if type(market_tickers) is not tuple:
        _fail("kalshi_market_tickers_invalid")
    if channel == LIFECYCLE_CHANNEL:
        if market_tickers:
            _fail("kalshi_lifecycle_filter_unsupported")
        return {
            "id": command_id,
            "cmd": "subscribe",
            "params": {"channels": [channel]},
        }
    if not market_tickers or len(market_tickers) > _MAX_TICKERS:
        _fail("kalshi_market_tickers_invalid")
    for ticker in market_tickers:
        if type(ticker) is not str or not ticker:
            _fail("kalshi_market_tickers_invalid")
    return {
        "id": command_id,
        "cmd": "subscribe",
        "params": {
            "channels": [channel],
            "market_tickers": list(market_tickers),
        },
    }


def _decimal(value: object, code: str) -> Decimal:
    if type(value) is not str or not value:
        _fail(code)
    try:
        number = Decimal(value)
    except (ArithmeticError, DecimalException, ValueError):
        _fail(code)
    if not number.is_finite():
        _fail(code)
    return number


def _mapping(value: object, code: str) -> dict:
    if type(value) is not dict:
        _fail(code)
    return value


def _positive_int(value: object, code: str) -> int:
    if type(value) is not int or type(value) is bool or value <= 0:
        _fail(code)
    return value


@dataclass(frozen=True, slots=True)
class KalshiMarketMetadata:
    """Status and close time the order-book channel does not carry."""

    market_status: MarketStatus
    scheduled_close_wall_ns: int


@dataclass(frozen=True, slots=True)
class KalshiObservationClock:
    source_wall_ns: int
    observed_monotonic_ns: int
    clock_uncertainty_ns: int


class KalshiBookDecoder:
    """Translate Kalshi wire frames into normalized book contracts."""

    def __init__(
        self,
        metadata: dict[str, KalshiMarketMetadata],
        *,
        connection_epoch: int = 1,
    ) -> None:
        if type(metadata) is not dict or not metadata:
            _fail("kalshi_metadata_invalid")
        for ticker, entry in metadata.items():
            if type(ticker) is not str or not ticker:
                _fail("kalshi_metadata_invalid")
            if type(entry) is not KalshiMarketMetadata:
                _fail("kalshi_metadata_invalid")
        if type(connection_epoch) is not int or connection_epoch <= 0:
            _fail("kalshi_epoch_invalid")
        self._metadata = dict(metadata)
        self._connection_epoch = connection_epoch
        self._levels: dict[str, dict[ContractSide, dict[Decimal, Decimal]]] = {}
        self._sequence: dict[str, int] = {}
        self._awaiting_snapshot: set[str] = set(metadata)

    @property
    def connection_epoch(self) -> int:
        return self._connection_epoch

    def tickers(self) -> tuple[str, ...]:
        return tuple(sorted(self._metadata))

    def awaiting_snapshot(self) -> tuple[str, ...]:
        return tuple(sorted(self._awaiting_snapshot))

    def set_market_metadata(
        self,
        ticker: str,
        metadata: KalshiMarketMetadata,
    ) -> None:
        if type(ticker) is not str or not ticker:
            _fail("kalshi_metadata_invalid")
        if type(metadata) is not KalshiMarketMetadata:
            _fail("kalshi_metadata_invalid")
        self._metadata[ticker] = metadata

    def begin_trust_epoch(self) -> int:
        """Allocate a strictly newer epoch and demand a fresh snapshot.

        Called before any reconnect, subscription change, or forced
        resnapshot. Every tracked book is discarded so no delta can advance a
        book that was not snapshotted inside the new epoch.
        """
        self._connection_epoch += 1
        self._levels.clear()
        self._sequence.clear()
        self._awaiting_snapshot = set(self._metadata)
        return self._connection_epoch

    def require_resnapshot(self, ticker: str) -> None:
        """Discard one ticker's book without disturbing the others."""
        if type(ticker) is not str or not ticker:
            _fail("kalshi_ticker_invalid")
        self._levels.pop(ticker, None)
        self._sequence.pop(ticker, None)
        self._awaiting_snapshot.add(ticker)

    def _metadata_for(self, ticker: str) -> KalshiMarketMetadata:
        entry = self._metadata.get(ticker)
        if entry is None:
            _fail("kalshi_ticker_unknown")
        return entry  # type: ignore[return-value]

    def _ladder(
        self,
        ticker: str,
        side: ContractSide,
    ) -> tuple[BookLevel, ...]:
        prices = self._levels[ticker][side]
        return tuple(
            BookLevel(price, prices[price])
            for price in sorted(prices, reverse=True)
            if prices[price] > _ZERO
        )

    def decode_snapshot(
        self,
        frame: dict,
        clock: KalshiObservationClock,
    ) -> BookSnapshot:
        message = _mapping(frame.get("msg"), "kalshi_snapshot_invalid")
        sequence = _positive_int(frame.get("seq"), "kalshi_sequence_invalid")
        ticker = message.get("market_ticker")
        if type(ticker) is not str or not ticker:
            _fail("kalshi_ticker_invalid")
        metadata = self._metadata_for(ticker)

        sides: dict[ContractSide, dict[Decimal, Decimal]] = {
            ContractSide.YES: {},
            ContractSide.NO: {},
        }
        for key, side in (
            ("yes_dollars_fp", ContractSide.YES),
            ("no_dollars_fp", ContractSide.NO),
        ):
            raw_levels = message.get(key)
            if raw_levels is None:
                continue
            if type(raw_levels) is not list or len(raw_levels) > _MAX_LEVELS:
                _fail("kalshi_levels_invalid")
            for raw_level in raw_levels:
                if type(raw_level) is not list or len(raw_level) != 2:
                    _fail("kalshi_level_invalid")
                price = _decimal(raw_level[0], "kalshi_price_invalid")
                quantity = _decimal(raw_level[1], "kalshi_quantity_invalid")
                if price <= _ZERO or price >= _ONE:
                    _fail("kalshi_price_range")
                if quantity < _ZERO:
                    _fail("kalshi_quantity_range")
                if price in sides[side]:
                    _fail("kalshi_duplicate_price")
                if quantity > _ZERO:
                    sides[side][price] = quantity

        self._levels[ticker] = sides
        self._sequence[ticker] = sequence
        self._awaiting_snapshot.discard(ticker)
        return BookSnapshot(
            ticker=ticker,
            connection_epoch=self._connection_epoch,
            sequence=sequence,
            market_status=metadata.market_status,
            scheduled_close_wall_ns=metadata.scheduled_close_wall_ns,
            source_wall_ns=clock.source_wall_ns,
            observed_monotonic_ns=clock.observed_monotonic_ns,
            clock_uncertainty_ns=clock.clock_uncertainty_ns,
            yes_bids=self._ladder(ticker, ContractSide.YES),
            no_bids=self._ladder(ticker, ContractSide.NO),
        )

    def decode_delta(
        self,
        frame: dict,
        clock: KalshiObservationClock,
    ) -> BookDelta:
        message = _mapping(frame.get("msg"), "kalshi_delta_invalid")
        sequence = _positive_int(frame.get("seq"), "kalshi_sequence_invalid")
        ticker = message.get("market_ticker")
        if type(ticker) is not str or not ticker:
            _fail("kalshi_ticker_invalid")
        self._metadata_for(ticker)
        if ticker in self._awaiting_snapshot or ticker not in self._levels:
            raise KalshiResnapshotRequired("kalshi_snapshot_required")

        expected = self._sequence[ticker] + 1
        if sequence != expected:
            self.require_resnapshot(ticker)
            raise KalshiResnapshotRequired("kalshi_sequence_gap")

        raw_side = message.get("side")
        if raw_side == "yes":
            side = ContractSide.YES
        elif raw_side == "no":
            side = ContractSide.NO
        else:
            _fail("kalshi_side_invalid")
        price = _decimal(message.get("price_dollars"), "kalshi_price_invalid")
        change = _decimal(message.get("delta_fp"), "kalshi_delta_invalid")
        if price <= _ZERO or price >= _ONE:
            _fail("kalshi_price_range")

        prices = self._levels[ticker][side]
        resulting = prices.get(price, _ZERO) + change
        if resulting < _ZERO:
            # The wire book disagrees with ours; never invent a level.
            self.require_resnapshot(ticker)
            raise KalshiResnapshotRequired("kalshi_negative_quantity")
        if resulting == _ZERO:
            prices.pop(price, None)
        else:
            prices[price] = resulting
        self._sequence[ticker] = sequence

        return BookDelta(
            ticker=ticker,
            connection_epoch=self._connection_epoch,
            sequence=sequence,
            source_wall_ns=clock.source_wall_ns,
            observed_monotonic_ns=clock.observed_monotonic_ns,
            clock_uncertainty_ns=clock.clock_uncertainty_ns,
            contract_side=side,
            price=price,
            quantity=resulting,
        )

    def decode_lifecycle(
        self,
        frame: dict,
        clock: KalshiObservationClock,
    ) -> MarketLifecycle | None:
        message = _mapping(frame.get("msg"), "kalshi_lifecycle_invalid")
        ticker = message.get("market_ticker")
        if type(ticker) is not str or not ticker:
            return None
        if ticker not in self._metadata:
            return None
        event_type = message.get("event_type")
        if type(event_type) is not str:
            _fail("kalshi_lifecycle_event_invalid")
        status = _LIFECYCLE_STATUS_BY_EVENT.get(event_type)
        if status is None:
            return None
        previous = self._metadata[ticker]
        updated = KalshiMarketMetadata(
            market_status=status,
            scheduled_close_wall_ns=previous.scheduled_close_wall_ns,
        )
        self._metadata[ticker] = updated
        return MarketLifecycle(
            ticker=ticker,
            connection_epoch=self._connection_epoch,
            market_status=status,
            scheduled_close_wall_ns=updated.scheduled_close_wall_ns,
            source_wall_ns=clock.source_wall_ns,
            observed_monotonic_ns=clock.observed_monotonic_ns,
            clock_uncertainty_ns=clock.clock_uncertainty_ns,
        )

    def decode(
        self,
        frame: object,
        clock: KalshiObservationClock,
    ) -> BookSnapshot | BookDelta | MarketLifecycle | None:
        """Decode one wire frame, returning None for ignorable control types."""
        document = _mapping(frame, "kalshi_frame_invalid")
        if type(clock) is not KalshiObservationClock:
            _fail("kalshi_clock_invalid")
        frame_type = document.get("type")
        if type(frame_type) is not str:
            _fail("kalshi_frame_type_invalid")
        if frame_type == _SNAPSHOT_TYPE:
            return self.decode_snapshot(document, clock)
        if frame_type == _DELTA_TYPE:
            return self.decode_delta(document, clock)
        if frame_type == _LIFECYCLE_TYPE:
            return self.decode_lifecycle(document, clock)
        if frame_type == "error":
            _fail("kalshi_server_error")
        if frame_type in _IGNORED_TYPES:
            return None
        _fail("kalshi_frame_type_unknown")
        return None


def decode_kalshi_text_frame(text: str) -> dict:
    """Parse one size-capped JSON text frame."""
    if type(text) is not str:
        _fail("kalshi_frame_not_text")
    if len(text) > _MAX_FRAME_BYTES:
        _fail("kalshi_frame_too_large")
    try:
        document = json.loads(text)
    except ValueError:
        _fail("kalshi_frame_not_json")
    return _mapping(document, "kalshi_frame_invalid")


class KalshiReadOnlyFeed:
    """Own at most one physical read-only Kalshi WebSocket at a time."""

    def __init__(
        self,
        decoder: KalshiBookDecoder,
        *,
        connect: Callable[..., object],
        key_id: str,
        private_key_pem: str,
        url: str = KALSHI_WS_URL,
        wall_ns: Callable[[], int] = time.time_ns,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        clock_uncertainty_ns: int = 1_000_000_000,
    ) -> None:
        if type(decoder) is not KalshiBookDecoder:
            _fail("kalshi_decoder_invalid")
        if not callable(connect):
            _fail("kalshi_connect_invalid")
        if type(url) is not str or not url.startswith("wss://"):
            _fail("kalshi_url_invalid")
        if type(clock_uncertainty_ns) is not int or clock_uncertainty_ns < 0:
            _fail("kalshi_clock_uncertainty_invalid")
        self._decoder = decoder
        self._connect = connect
        self._key_id = key_id
        self._private_key_pem = private_key_pem
        self._url = url
        self._wall_ns = wall_ns
        self._monotonic_ns = monotonic_ns
        self._clock_uncertainty_ns = clock_uncertainty_ns
        self._socket: object | None = None
        self._command_id = 0

    def __repr__(self) -> str:
        return "KalshiReadOnlyFeed(read_only=True, credentials=<redacted>)"

    @property
    def socket_open(self) -> bool:
        return self._socket is not None

    def _clock(self) -> KalshiObservationClock:
        return KalshiObservationClock(
            source_wall_ns=self._wall_ns(),
            observed_monotonic_ns=self._monotonic_ns(),
            clock_uncertainty_ns=self._clock_uncertainty_ns,
        )

    def close(self) -> None:
        socket = self._socket
        self._socket = None
        if socket is None:
            return
        closer = getattr(socket, "close", None)
        if closer is not None:
            try:
                closer()
            except Exception:
                pass

    def connect(self) -> int:
        """Open the single physical socket and subscribe read-only.

        Any existing socket is closed first and a strictly newer trust epoch
        is allocated, so sockets never overlap and no pre-reconnect frame can
        advance a book in the new epoch.
        """
        self.close()
        epoch = self._decoder.begin_trust_epoch()
        headers = kalshi_handshake_headers(
            key_id=self._key_id,
            private_key_pem=self._private_key_pem,
            timestamp_ms=self._wall_ns() // 1_000_000,
        )
        try:
            socket = self._connect(self._url, additional_headers=headers)
        except Exception:
            _fail("kalshi_connect_failed")
        self._socket = socket
        self._subscribe()
        return epoch

    def _send(self, command: dict) -> None:
        socket = self._socket
        if socket is None:
            _fail("kalshi_socket_closed")
        sender = getattr(socket, "send", None)
        if sender is None:
            _fail("kalshi_socket_invalid")
        try:
            sender(json.dumps(command, separators=(",", ":")))
        except Exception:
            _fail("kalshi_send_failed")

    def _subscribe(self) -> None:
        self._command_id += 1
        self._send(
            subscribe_command(
                command_id=self._command_id,
                channel=ORDERBOOK_CHANNEL,
                market_tickers=self._decoder.tickers(),
            )
        )
        self._command_id += 1
        self._send(
            subscribe_command(
                command_id=self._command_id,
                channel=LIFECYCLE_CHANNEL,
                market_tickers=(),
            )
        )

    def events(
        self,
    ) -> Iterator[BookSnapshot | BookDelta | MarketLifecycle]:
        """Yield normalized events until the socket ends.

        A ticker that desynchronizes is dropped to awaiting-snapshot and the
        stream continues; it produces no further event until Kalshi sends a
        new snapshot for it.
        """
        socket = self._socket
        if socket is None:
            _fail("kalshi_socket_closed")
        for raw in socket:  # type: ignore[union-attr]
            document = decode_kalshi_text_frame(raw)
            try:
                event = self._decoder.decode(document, self._clock())
            except KalshiResnapshotRequired:
                continue
            if event is not None:
                yield event


__all__: Final[tuple[str, ...]] = (
    "KALSHI_KEY_ID_ENV",
    "KALSHI_PRIVATE_KEY_ENV",
    "KALSHI_WS_PATH",
    "KALSHI_WS_URL",
    "KalshiBookDecoder",
    "KalshiMarketMetadata",
    "KalshiObservationClock",
    "KalshiReadOnlyError",
    "KalshiReadOnlyFeed",
    "KalshiResnapshotRequired",
    "READ_ONLY_CHANNELS",
    "decode_kalshi_text_frame",
    "handshake_signature_payload",
    "kalshi_handshake_headers",
    "load_kalshi_key_id",
    "load_kalshi_private_key_pem",
    "subscribe_command",
)
