"""Pure strict parser/reducer for current Kalshi orderbook shadow frames.

Callers must durably acknowledge the opaque raw frame before invoking this
module.  Parsed values remain explicitly unqualified research evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from re import Pattern
from re import compile as pattern_compile


KALSHI_UNQUALIFIED_SHADOW = "unqualified_shadow"

_FULL_L2_STATE_DOMAIN = (
    b"inci-tennis-kalshi-unqualified-two-ticker-full-l2-v1\x00"
)

_MAX_FRAME_BYTES = 1_048_576
_MAX_LADDER_LEVELS = 1_024
_MAX_JSON_DEPTH = 16
_MAX_JSON_NODES = 8_192
_MAX_SIGNED_64 = 9_223_372_036_854_775_807
_MAX_QUANTITY = Decimal("1000000000000")
_TICKER_RE = pattern_compile(r"[A-Z0-9][A-Z0-9._-]{0,127}\Z")
_UUID_RE = pattern_compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
_CLIENT_ID_RE = pattern_compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_PRICE_RE = pattern_compile(r"(?:0|1|0\.[0-9]{1,4}|1\.0{1,4})\Z")
_QUANTITY_RE = pattern_compile(
    r"(?:0|[1-9][0-9]{0,11})\.[0-9]{2}\Z"
)
_DELTA_RE = pattern_compile(
    r"-?(?:0|[1-9][0-9]{0,11})\.[0-9]{2}\Z"
)
_RFC3339_UTC_RE = pattern_compile(
    r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})T"
    r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,9}))?Z\Z"
)


class KalshiWireContractError(ValueError):
    """Fixed-code rejection that never renders the raw frame."""

    def __init__(self) -> None:
        super().__init__("kalshi_ws_contract_invalid")


def _fail() -> None:
    raise KalshiWireContractError()


class _ParsedValue:
    @property
    def qualification(self) -> str:
        return KALSHI_UNQUALIFIED_SHADOW

    @property
    def research_only(self) -> bool:
        return True

    @property
    def execution_authorized(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"<{type(self).__name__} unqualified_shadow>"


@dataclass(frozen=True, slots=True, repr=False)
class KalshiSubscribed(_ParsedValue):
    request_id: int | None
    sid: int
    raw_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class KalshiCommandOk(_ParsedValue):
    request_id: int | None
    sid: int | None
    sequence: int | None
    market_tickers: tuple[str, ...] | None
    raw_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class KalshiCommandError(_ParsedValue):
    request_id: int | None
    error_code: int
    message_sha256: str
    raw_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class KalshiOrderbookSnapshot(_ParsedValue):
    sid: int
    sequence: int
    market_ticker: str
    market_id: str
    yes_levels: tuple[tuple[Decimal, Decimal], ...]
    no_levels: tuple[tuple[Decimal, Decimal], ...]
    raw_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class KalshiOrderbookDelta(_ParsedValue):
    sid: int
    sequence: int
    market_ticker: str
    market_id: str
    price_dollars: Decimal
    delta: Decimal
    side: str
    source_ts: str | None
    source_ts_ms: int | None
    client_order_id_sha256: str | None
    subaccount: int | None
    raw_sha256: str

    @property
    def client_order_id(self) -> None:
        """The raw client identifier is deliberately never exposed."""

        return None


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _json_integer(token: str) -> int:
    if len(token.removeprefix("-")) > 19:
        _fail()
    try:
        value = int(token)
    except (ValueError, OverflowError):
        _fail()
    if value < -_MAX_SIGNED_64 or value > _MAX_SIGNED_64:
        _fail()
    return value


def _reject_json_number(_: str) -> object:
    _fail()


def _validate_tree(value: object, depth: int = 0) -> int:
    if depth > _MAX_JSON_DEPTH:
        _fail()
    if value is None or type(value) in (bool, int):
        return 1
    if type(value) is str:
        if len(value.encode("utf-8")) > 4_096:
            _fail()
        return 1
    if type(value) is list:
        nodes = 1
        for item in value:
            nodes += _validate_tree(item, depth + 1)
            if nodes > _MAX_JSON_NODES:
                _fail()
        return nodes
    if type(value) is dict:
        nodes = 1
        for key, item in value.items():
            if type(key) is not str or len(key.encode("utf-8")) > 128:
                _fail()
            nodes += 1 + _validate_tree(item, depth + 1)
            if nodes > _MAX_JSON_NODES:
                _fail()
        return nodes
    _fail()


def _exact(value: object, keys: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != keys:
        _fail()
    return value


def _positive_id(value: object) -> int:
    if type(value) is not int or value <= 0 or value > _MAX_SIGNED_64:
        _fail()
    return value


def _command_id(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0 or value > _MAX_SIGNED_64:
        _fail()
    return value


def _sequence(value: object) -> int:
    if type(value) is not int or value < 1 or value > _MAX_SIGNED_64:
        _fail()
    return value


def _safe_string(value: object, pattern: Pattern[str]) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail()
    return value


def _decimal(
    value: object,
    pattern: Pattern[str],
    *,
    positive: bool,
) -> Decimal:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail()
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        _fail()
    if (
        not parsed.is_finite()
        or abs(parsed) > _MAX_QUANTITY
        or (positive and parsed <= 0)
    ):
        _fail()
    return parsed


def _price(value: object) -> Decimal:
    value_decimal = _decimal(value, _PRICE_RE, positive=False)
    if value_decimal < 0 or value_decimal > 1:
        _fail()
    return value_decimal


def _ticker(value: object) -> str:
    return _safe_string(value, _TICKER_RE)


def _ticker_list(value: object) -> tuple[str, ...]:
    if type(value) is not list or not value or len(value) > 1_024:
        _fail()
    result = tuple(_ticker(item) for item in value)
    if len(set(result)) != len(result):
        _fail()
    return result


def _uuid(value: object) -> str:
    return _safe_string(value, _UUID_RE)


def _ladders(value: object) -> tuple[tuple[Decimal, Decimal], ...]:
    if type(value) is not list or len(value) > _MAX_LADDER_LEVELS:
        _fail()
    result: list[tuple[Decimal, Decimal]] = []
    previous_price: Decimal | None = None
    for level in value:
        if type(level) is not list or len(level) != 2:
            _fail()
        price = _price(level[0])
        quantity = _decimal(level[1], _QUANTITY_RE, positive=True)
        if previous_price is not None and price <= previous_price:
            _fail()
        previous_price = price
        result.append((price, quantity))
    return tuple(result)


def _timestamp_ms(value: object) -> int:
    if type(value) is not int or value < 0 or value > _MAX_SIGNED_64:
        _fail()
    return value


def _rfc3339_utc_to_ms(value: object) -> int:
    if type(value) is not str:
        _fail()
    match = _RFC3339_UTC_RE.fullmatch(value)
    if match is None:
        _fail()
    groups = match.groupdict()
    fraction = groups["fraction"] or ""
    try:
        observed = datetime(
            int(groups["year"]),
            int(groups["month"]),
            int(groups["day"]),
            int(groups["hour"]),
            int(groups["minute"]),
            int(groups["second"]),
            int((fraction + "000000")[:6]),
            tzinfo=UTC,
        )
    except (TypeError, ValueError, OverflowError):
        _fail()
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    if observed < epoch:
        _fail()
    elapsed = observed - epoch
    milliseconds = (
        elapsed.days * 86_400_000
        + elapsed.seconds * 1_000
        + elapsed.microseconds // 1_000
    )
    if milliseconds > _MAX_SIGNED_64:
        _fail()
    return milliseconds


def _parse_subscribed(
    document: dict[str, object], raw_hash: str
) -> KalshiSubscribed:
    if (
        not frozenset({"type", "msg"}).issubset(document)
        or not frozenset(document).issubset({"id", "type", "msg"})
    ):
        _fail()
    message = _exact(document["msg"], frozenset({"channel", "sid"}))
    if message["channel"] != "orderbook_delta":
        _fail()
    return KalshiSubscribed(
        request_id=_command_id(document.get("id")),
        sid=_positive_id(message["sid"]),
        raw_sha256=raw_hash,
    )


def _parse_ok(
    document: dict[str, object], raw_hash: str
) -> KalshiCommandOk:
    allowed = frozenset({"id", "sid", "seq", "type", "msg"})
    if frozenset(document) - allowed:
        _fail()
    message = document.get("msg")
    market_tickers: tuple[str, ...] | None = None
    if message is not None:
        if type(message) is not dict or frozenset(message) - {
            "market_tickers",
            "market_ids",
        }:
            _fail()
        if "market_tickers" in message:
            market_tickers = _ticker_list(message["market_tickers"])
        if "market_ids" in message:
            market_ids = message["market_ids"]
            if type(market_ids) is not list or not market_ids:
                _fail()
            tuple(_uuid(item) for item in market_ids)
    return KalshiCommandOk(
        request_id=_command_id(document.get("id")),
        sid=(
            _positive_id(document["sid"])
            if "sid" in document
            else None
        ),
        sequence=(
            _sequence(document["seq"])
            if "seq" in document
            else None
        ),
        market_tickers=market_tickers,
        raw_sha256=raw_hash,
    )


def _parse_error(
    document: dict[str, object], raw_hash: str
) -> KalshiCommandError:
    if (
        not frozenset({"type", "msg"}).issubset(document)
        or not frozenset(document).issubset({"id", "type", "msg"})
    ):
        _fail()
    message = _exact(document["msg"], frozenset({"code", "msg"}))
    text = message["msg"]
    code = message["code"]
    if (
        type(text) is not str
        or not text
        or len(text.encode("utf-8")) > 512
        or type(code) is not int
        or code < 1
        or code > 28
    ):
        _fail()
    return KalshiCommandError(
        request_id=_command_id(document.get("id")),
        error_code=code,
        message_sha256=sha256(text.encode("utf-8")).hexdigest(),
        raw_sha256=raw_hash,
    )


def _parse_snapshot(
    document: dict[str, object], raw_hash: str
) -> KalshiOrderbookSnapshot:
    _exact(document, frozenset({"type", "sid", "seq", "msg"}))
    message = document["msg"]
    required = frozenset({"market_ticker", "market_id"})
    optional = frozenset({"yes_dollars_fp", "no_dollars_fp"})
    if (
        type(message) is not dict
        or not required.issubset(message)
        or not frozenset(message).issubset(required | optional)
    ):
        _fail()
    return KalshiOrderbookSnapshot(
        sid=_positive_id(document["sid"]),
        sequence=_sequence(document["seq"]),
        market_ticker=_ticker(message["market_ticker"]),
        market_id=_uuid(message["market_id"]),
        yes_levels=_ladders(message.get("yes_dollars_fp", [])),
        no_levels=_ladders(message.get("no_dollars_fp", [])),
        raw_sha256=raw_hash,
    )


def _parse_delta(
    document: dict[str, object], raw_hash: str
) -> KalshiOrderbookDelta:
    _exact(document, frozenset({"type", "sid", "seq", "msg"}))
    message = document["msg"]
    required = frozenset(
        {
            "market_ticker",
            "market_id",
            "price_dollars",
            "delta_fp",
            "side",
        }
    )
    optional = frozenset({"ts", "ts_ms", "client_order_id", "subaccount"})
    if (
        type(message) is not dict
        or not required.issubset(message)
        or not frozenset(message).issubset(required | optional)
    ):
        _fail()
    side = message["side"]
    source_ts = message.get("ts")
    source_ts_ms = message.get("ts_ms")
    client_order_id = message.get("client_order_id")
    subaccount = message.get("subaccount")
    if (
        side not in {"yes", "no"}
        or (
            client_order_id is not None
            and (
                type(client_order_id) is not str
                or _CLIENT_ID_RE.fullmatch(client_order_id) is None
            )
        )
        or (
            subaccount is not None
            and (
                type(subaccount) is not int
                or subaccount < 0
                or subaccount > 63
            )
        )
    ):
        _fail()
    parsed_source_ms = (
        _rfc3339_utc_to_ms(source_ts) if source_ts is not None else None
    )
    source_ts_ms = (
        _timestamp_ms(source_ts_ms) if source_ts_ms is not None else None
    )
    if (
        parsed_source_ms is not None
        and source_ts_ms is not None
        and parsed_source_ms != source_ts_ms
    ):
        _fail()
    delta = _decimal(message["delta_fp"], _DELTA_RE, positive=False)
    if delta == 0:
        _fail()
    return KalshiOrderbookDelta(
        sid=_positive_id(document["sid"]),
        sequence=_sequence(document["seq"]),
        market_ticker=_ticker(message["market_ticker"]),
        market_id=_uuid(message["market_id"]),
        price_dollars=_price(message["price_dollars"]),
        delta=delta,
        side=side,
        source_ts=source_ts,
        source_ts_ms=source_ts_ms,
        client_order_id_sha256=(
            sha256(client_order_id.encode("utf-8")).hexdigest()
            if type(client_order_id) is str
            else None
        ),
        subaccount=subaccount,
        raw_sha256=raw_hash,
    )


def parse_unqualified_book_message(raw: bytes) -> object:
    """Parse only control/orderbook frames used by the book-only transport."""

    if type(raw) is not bytes or not raw or len(raw) > _MAX_FRAME_BYTES:
        _fail()
    try:
        document = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_int=_json_integer,
            parse_float=_reject_json_number,
            parse_constant=_reject_json_number,
        )
    except KalshiWireContractError:
        raise
    except Exception:
        _fail()
    _validate_tree(document)
    if type(document) is not dict or type(document.get("type")) is not str:
        _fail()
    raw_hash = sha256(raw).hexdigest()
    parsers = {
        "subscribed": _parse_subscribed,
        "ok": _parse_ok,
        "error": _parse_error,
        "orderbook_snapshot": _parse_snapshot,
        "orderbook_delta": _parse_delta,
    }
    parser = parsers.get(document["type"])
    if parser is None:
        _fail()
    return parser(document, raw_hash)


def parse_unqualified_shadow_message(raw: bytes) -> object:
    """Compatibility name for the explicitly book-only shadow parser."""

    return parse_unqualified_book_message(raw)


@dataclass(frozen=True, slots=True, repr=False)
class UnqualifiedCandidateBookView(_ParsedValue):
    """Non-executable top-of-book research projection for one market."""

    ticker: str
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    no_bid: Decimal | None
    no_ask: Decimal | None
    yes_bid_depth: Decimal | None
    yes_ask_depth: Decimal | None
    no_bid_depth: Decimal | None
    no_ask_depth: Decimal | None
    generation: int | None
    sid: int | None
    sequence: int | None
    status: str
    reason: str


@dataclass(frozen=True, slots=True, repr=False)
class UnqualifiedTwoTickerCandidateState(_ParsedValue):
    """Immutable aggregate result from one strict two-market stream."""

    views: tuple[UnqualifiedCandidateBookView, UnqualifiedCandidateBookView]
    generation: int | None
    sid: int | None
    sequence: int | None
    status: str
    reason: str
    snapshot_needed: bool

    def view(self, ticker: str) -> UnqualifiedCandidateBookView:
        for candidate in self.views:
            if candidate.ticker == ticker:
                return candidate
        raise KeyError("kalshi_candidate_ticker_unknown")


@dataclass(frozen=True, slots=True, repr=False)
class UnqualifiedCandidateL2Market(_ParsedValue):
    """Immutable exact-depth research copy for one candidate market."""

    ticker: str
    market_id: str
    yes_levels: tuple[tuple[Decimal, Decimal], ...]
    no_levels: tuple[tuple[Decimal, Decimal], ...]


@dataclass(frozen=True, slots=True, repr=False)
class UnqualifiedTwoTickerL2State(_ParsedValue):
    """Immutable unqualified full depth from one ready reducer state."""

    markets: tuple[UnqualifiedCandidateL2Market, UnqualifiedCandidateL2Market]
    physical_connection_generation: int
    subscription_id: int
    global_sequence: int
    state_sha256: str

    def market(self, ticker: str) -> UnqualifiedCandidateL2Market:
        for candidate in self.markets:
            if candidate.ticker == ticker:
                return candidate
        raise KeyError("kalshi_candidate_ticker_unknown")


def _canonical_decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if value.is_zero() else rendered


def _full_l2_state_sha256(
    markets: tuple[UnqualifiedCandidateL2Market, UnqualifiedCandidateL2Market],
    *,
    physical_connection_generation: int,
    subscription_id: int,
    global_sequence: int,
) -> str:
    projection = {
        "schema_version": 1,
        "physical_connection_generation": physical_connection_generation,
        "subscription_id": subscription_id,
        "global_sequence": global_sequence,
        "markets": [
            {
                "ticker": market.ticker,
                "market_id": market.market_id,
                "yes_levels": [
                    [
                        _canonical_decimal_text(price),
                        _canonical_decimal_text(quantity),
                    ]
                    for price, quantity in market.yes_levels
                ],
                "no_levels": [
                    [
                        _canonical_decimal_text(price),
                        _canonical_decimal_text(quantity),
                    ]
                    for price, quantity in market.no_levels
                ],
            }
            for market in markets
        ],
    }
    encoded = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return sha256(_FULL_L2_STATE_DOMAIN + encoded).hexdigest()


class UnqualifiedTwoTickerBookReducer:
    """Fail-closed reducer for one two-market, one-SID candidate book stream.

    The reducer never constructs the trusted expert book.  It keeps prices
    unavailable until the correlated subscription acknowledgement and both
    market snapshots have arrived on one physical connection generation.
    Sequence numbers are global to that shared SID.
    """

    __slots__ = (
        "_books",
        "_expected_request_id",
        "_generation",
        "_last_sequence",
        "_market_ids",
        "_reason",
        "_seen_snapshots",
        "_sid",
        "_snapshot_needed",
        "_status",
        "_tickers",
    )

    def __init__(self, market_tickers: tuple[str, str]) -> None:
        if (
            type(market_tickers) is not tuple
            or len(market_tickers) != 2
            or market_tickers[0] == market_tickers[1]
        ):
            _fail()
        self._tickers = (
            _ticker(market_tickers[0]),
            _ticker(market_tickers[1]),
        )
        self._generation: int | None = None
        self._sid: int | None = None
        self._last_sequence: int | None = None
        self._expected_request_id: int | None = None
        self._status = "uninitialized"
        self._reason = "subscription_not_started"
        self._snapshot_needed = False
        self._market_ids: dict[str, str | None] = {}
        self._books: dict[
            str,
            dict[str, dict[Decimal, Decimal]],
        ] = {}
        self._seen_snapshots: set[str] = set()
        self._clear_books()

    def __repr__(self) -> str:
        return "<UnqualifiedTwoTickerBookReducer unqualified_shadow>"

    @property
    def state(self) -> UnqualifiedTwoTickerCandidateState:
        return self._state()

    @property
    def full_l2(self) -> UnqualifiedTwoTickerL2State | None:
        if self._status != "ready":
            return None
        generation = self._generation
        sid = self._sid
        sequence = self._last_sequence
        if generation is None or sid is None or sequence is None:
            _fail()
        copied: list[UnqualifiedCandidateL2Market] = []
        for ticker in self._tickers:
            market_id = self._market_ids[ticker]
            if market_id is None:
                _fail()
            copied.append(
                UnqualifiedCandidateL2Market(
                    ticker=ticker,
                    market_id=market_id,
                    yes_levels=tuple(sorted(self._books[ticker]["yes"].items())),
                    no_levels=tuple(sorted(self._books[ticker]["no"].items())),
                )
            )
        markets = (copied[0], copied[1])
        return UnqualifiedTwoTickerL2State(
            markets=markets,
            physical_connection_generation=generation,
            subscription_id=sid,
            global_sequence=sequence,
            state_sha256=_full_l2_state_sha256(
                markets,
                physical_connection_generation=generation,
                subscription_id=sid,
                global_sequence=sequence,
            ),
        )

    def begin_subscription(
        self,
        physical_connection_generation: int,
        request_id: int,
    ) -> UnqualifiedTwoTickerCandidateState:
        generation = _positive_id(physical_connection_generation)
        command_id = _positive_id(request_id)
        if self._generation is not None and generation <= self._generation:
            _fail()
        self._generation = generation
        self._sid = None
        self._last_sequence = None
        self._expected_request_id = command_id
        self._status = "awaiting_subscription_ack"
        self._reason = "subscription_ack_pending"
        self._snapshot_needed = False
        self._clear_books()
        return self._state()

    def expect_snapshot(
        self,
        physical_connection_generation: int,
        subscription_id: int,
        request_id: int,
    ) -> UnqualifiedTwoTickerCandidateState:
        if (
            self._generation != _positive_id(physical_connection_generation)
            or self._sid != _positive_id(subscription_id)
            or self._status != "invalidated"
            or not self._snapshot_needed
        ):
            _fail()
        self._expected_request_id = _positive_id(request_id)
        self._last_sequence = None
        self._status = "awaiting_snapshot_ack"
        self._reason = "snapshot_ack_pending"
        self._snapshot_needed = False
        self._clear_books()
        return self._state()

    def disconnect(
        self,
        physical_connection_generation: int,
    ) -> UnqualifiedTwoTickerCandidateState:
        if self._generation != _positive_id(physical_connection_generation):
            _fail()
        self._expected_request_id = None
        self._last_sequence = None
        self._sid = None
        self._status = "disconnected"
        self._reason = "physical_connection_closed"
        self._snapshot_needed = False
        self._clear_books()
        return self._state()

    def apply(
        self,
        message: object,
        physical_connection_generation: int,
    ) -> UnqualifiedTwoTickerCandidateState:
        generation = _positive_id(physical_connection_generation)
        if self._generation != generation:
            return self._invalidate("physical_generation_mismatch", terminal=True)
        if type(message) is KalshiCommandError:
            return self._command_error(message)
        if self._status in {"terminal", "disconnected", "uninitialized"}:
            return self._invalidate("stream_not_active", terminal=True)
        if type(message) is KalshiSubscribed:
            return self._subscription_ack(message)
        if type(message) is KalshiCommandOk:
            return self._snapshot_ack(message)
        if type(message) is KalshiOrderbookSnapshot:
            return self._snapshot(message)
        if type(message) is KalshiOrderbookDelta:
            return self._delta(message)
        return self._invalidate("message_type_unexpected")

    def _clear_books(self) -> None:
        self._books = {
            ticker: {"yes": {}, "no": {}} for ticker in self._tickers
        }
        self._market_ids = {ticker: None for ticker in self._tickers}
        self._seen_snapshots = set()

    def _invalidate(
        self,
        reason: str,
        *,
        terminal: bool = False,
    ) -> UnqualifiedTwoTickerCandidateState:
        self._status = "terminal" if terminal else "invalidated"
        self._reason = reason
        self._snapshot_needed = not terminal and self._sid is not None
        self._expected_request_id = None
        self._clear_books()
        return self._state()

    def _command_error(
        self,
        message: KalshiCommandError,
    ) -> UnqualifiedTwoTickerCandidateState:
        expected = self._expected_request_id
        if self._status == "awaiting_subscription_ack":
            if message.request_id != expected:
                return self._invalidate(
                    "command_error_correlation_mismatch",
                    terminal=True,
                )
            return self._invalidate(
                "subscription_command_error",
                terminal=True,
            )
        if self._status == "awaiting_snapshot_ack":
            if message.request_id != expected:
                return self._invalidate(
                    "command_error_correlation_mismatch",
                    terminal=True,
                )
            return self._invalidate(
                "snapshot_command_error",
                terminal=True,
            )
        reason = (
            "terminal_channel_error"
            if message.error_code in {10, 17, 25}
            else "command_error_unexpected"
        )
        return self._invalidate(reason, terminal=True)

    def _subscription_ack(
        self,
        message: KalshiSubscribed,
    ) -> UnqualifiedTwoTickerCandidateState:
        if (
            self._status != "awaiting_subscription_ack"
            or message.request_id is None
            or message.request_id <= 0
            or message.request_id != self._expected_request_id
        ):
            return self._invalidate("subscription_ack_mismatch", terminal=True)
        self._sid = message.sid
        self._expected_request_id = None
        self._last_sequence = None
        self._status = "awaiting_snapshots"
        self._reason = "snapshot_barrier_incomplete"
        self._snapshot_needed = False
        self._clear_books()
        return self._state()

    def _snapshot_ack(
        self,
        message: KalshiCommandOk,
    ) -> UnqualifiedTwoTickerCandidateState:
        if (
            self._status != "awaiting_snapshot_ack"
            or message.request_id is None
            or message.request_id <= 0
            or message.request_id != self._expected_request_id
            or message.sid != self._sid
            or message.sequence is None
            or message.market_tickers is None
            or frozenset(message.market_tickers) != frozenset(self._tickers)
            or len(message.market_tickers) != 2
        ):
            return self._invalidate("snapshot_ack_mismatch")
        self._expected_request_id = None
        self._last_sequence = message.sequence
        self._status = "awaiting_snapshots"
        self._reason = "snapshot_barrier_incomplete"
        self._snapshot_needed = False
        self._clear_books()
        return self._state()

    def _sequence_reason(self, incoming: int) -> str | None:
        if self._last_sequence is None:
            return None
        expected = self._last_sequence + 1
        if incoming == expected:
            return None
        if incoming == self._last_sequence:
            return "sequence_duplicate"
        if incoming < expected:
            return "sequence_out_of_order"
        return "sequence_gap"

    def _identity_reason(
        self,
        *,
        sid: int,
        ticker: str,
        market_id: str,
    ) -> str | None:
        if sid != self._sid:
            return "subscription_id_mismatch"
        if ticker not in self._books:
            return "market_ticker_mismatch"
        expected_market_id = self._market_ids[ticker]
        if expected_market_id is not None and market_id != expected_market_id:
            return "market_id_mismatch"
        return None

    def _snapshot(
        self,
        message: KalshiOrderbookSnapshot,
    ) -> UnqualifiedTwoTickerCandidateState:
        if self._status != "awaiting_snapshots":
            return self._invalidate("snapshot_unexpected")
        identity_reason = self._identity_reason(
            sid=message.sid,
            ticker=message.market_ticker,
            market_id=message.market_id,
        )
        if identity_reason is not None:
            return self._invalidate(identity_reason)
        sequence_reason = self._sequence_reason(message.sequence)
        if sequence_reason is not None:
            return self._invalidate(sequence_reason)
        if message.market_ticker in self._seen_snapshots:
            return self._invalidate("snapshot_duplicate")
        self._last_sequence = message.sequence
        self._market_ids[message.market_ticker] = message.market_id
        self._books[message.market_ticker] = {
            "yes": dict(message.yes_levels),
            "no": dict(message.no_levels),
        }
        self._seen_snapshots.add(message.market_ticker)
        crossing = self._crossing_reason(message.market_ticker)
        if crossing is not None:
            return self._invalidate(crossing)
        if self._seen_snapshots != set(self._tickers):
            return self._state()
        self._update_availability()
        return self._state()

    def _delta(
        self,
        message: KalshiOrderbookDelta,
    ) -> UnqualifiedTwoTickerCandidateState:
        if self._status == "awaiting_snapshot_ack":
            return self._state()
        if self._status not in {"awaiting_snapshots", "ready", "empty_book"}:
            return self._invalidate("delta_before_snapshot")
        identity_reason = self._identity_reason(
            sid=message.sid,
            ticker=message.market_ticker,
            market_id=message.market_id,
        )
        if identity_reason is not None:
            return self._invalidate(identity_reason)
        sequence_reason = self._sequence_reason(message.sequence)
        if sequence_reason is not None:
            return self._invalidate(sequence_reason)
        if message.market_ticker not in self._seen_snapshots:
            self._last_sequence = message.sequence
            return self._state()
        selected = self._books[message.market_ticker][message.side]
        before = selected.get(message.price_dollars)
        if before is None:
            if message.delta < 0:
                return self._invalidate("delta_level_missing")
            after = message.delta
        else:
            after = before + message.delta
        if after < 0:
            return self._invalidate("delta_quantity_negative")
        if after == 0:
            selected.pop(message.price_dollars, None)
        else:
            selected[message.price_dollars] = after
        self._last_sequence = message.sequence
        crossing = self._crossing_reason(message.market_ticker)
        if crossing is not None:
            return self._invalidate(crossing)
        if self._seen_snapshots == set(self._tickers):
            self._update_availability()
        return self._state()

    def _crossing_reason(self, ticker: str) -> str | None:
        yes = self._books[ticker]["yes"]
        no = self._books[ticker]["no"]
        if yes and no and max(yes) > min(no):
            return "candidate_book_crossed"
        return None

    def _update_availability(self) -> None:
        if all(
            self._books[ticker]["yes"] and self._books[ticker]["no"]
            for ticker in self._tickers
        ):
            self._status = "ready"
            self._reason = "candidate_book_ready"
        else:
            self._status = "empty_book"
            self._reason = "empty_executable_book"
        self._snapshot_needed = False

    def _view(self, ticker: str) -> UnqualifiedCandidateBookView:
        yes_bid: Decimal | None = None
        yes_ask: Decimal | None = None
        no_bid: Decimal | None = None
        no_ask: Decimal | None = None
        yes_bid_depth: Decimal | None = None
        yes_ask_depth: Decimal | None = None
        no_bid_depth: Decimal | None = None
        no_ask_depth: Decimal | None = None
        if self._status == "ready":
            yes = self._books[ticker]["yes"]
            no = self._books[ticker]["no"]
            yes_bid = max(yes)
            yes_ask = min(no)
            no_bid = Decimal("1") - yes_ask
            no_ask = Decimal("1") - yes_bid
            yes_bid_depth = yes[yes_bid]
            yes_ask_depth = no[yes_ask]
            no_bid_depth = yes_ask_depth
            no_ask_depth = yes_bid_depth
        return UnqualifiedCandidateBookView(
            ticker=ticker,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            yes_bid_depth=yes_bid_depth,
            yes_ask_depth=yes_ask_depth,
            no_bid_depth=no_bid_depth,
            no_ask_depth=no_ask_depth,
            generation=self._generation,
            sid=self._sid,
            sequence=self._last_sequence,
            status=self._status,
            reason=self._reason,
        )

    def _state(self) -> UnqualifiedTwoTickerCandidateState:
        return UnqualifiedTwoTickerCandidateState(
            views=(self._view(self._tickers[0]), self._view(self._tickers[1])),
            generation=self._generation,
            sid=self._sid,
            sequence=self._last_sequence,
            status=self._status,
            reason=self._reason,
            snapshot_needed=self._snapshot_needed,
        )
