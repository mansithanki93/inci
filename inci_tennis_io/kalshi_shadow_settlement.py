"""GET-only public Kalshi Market evidence for shadow settlement.

The transport validates and preserves a single public Market response.  It has
no account, order, portfolio, trade, provider, or outcome-classification
authority; settlement-pair semantics belong to the reconciliation layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from re import compile as pattern_compile
import time

import requests


_ORIGIN = "https://external-api.kalshi.com"
_CURRENT_PATH = "/trade-api/v2/markets/"
_HISTORICAL_PATH = "/trade-api/v2/historical/markets/"
_MAXIMUM_BODY_BYTES = 8_388_608
_STREAM_CHUNK_BYTES = 65_536
_GET_429_DELAYS = (0.25, 0.5, 1.0, 2.0)
_TICKER = pattern_compile(r"[A-Z0-9][A-Z0-9._-]{0,127}\Z")
_TOKEN = pattern_compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_FIXED_POINT = pattern_compile(r"(?:0|[1-9]\d*)(?:\.\d+)?\Z")
_RFC3339_UTC = pattern_compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\Z")
_STATUSES = frozenset({
    "initialized", "inactive", "active", "closed", "determined", "disputed",
    "amended", "finalized",
})


class KalshiShadowSettlementError(ValueError):
    """Sanitized fail-closed public settlement transport error."""


def _fail(code: str) -> None:
    raise KalshiShadowSettlementError(code)


def _duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            _fail("kalshi_settlement_json_invalid")
        value[key] = item
    return value


def _nonfinite(_: str) -> object:
    _fail("kalshi_settlement_json_invalid")


def _safe_ticker(value: object) -> bool:
    return type(value) is str and _TICKER.fullmatch(value) is not None


def _required_text(value: object, field: str) -> str:
    if type(value) is not dict or field not in value:
        raise ValueError("kalshi_settlement_schema_invalid")
    result = value[field]
    if type(result) is not str or not result:
        raise ValueError("kalshi_settlement_schema_invalid")
    return result


def _nullable_text(value: object, field: str) -> str | None:
    if type(value) is not dict or field not in value:
        raise ValueError("kalshi_settlement_schema_invalid")
    result = value[field]
    if result is None or result == "":
        return None
    if type(result) is not str:
        raise ValueError("kalshi_settlement_schema_invalid")
    return result


def _settlement_decimal(value: str) -> None:
    if _FIXED_POINT.fullmatch(value) is None:
        raise ValueError("kalshi_settlement_schema_invalid")
    try:
        if not Decimal(value).is_finite():
            raise ValueError("kalshi_settlement_schema_invalid")
    except InvalidOperation:
        raise ValueError("kalshi_settlement_schema_invalid") from None


def _settlement_timestamp(value: str) -> None:
    if _RFC3339_UTC.fullmatch(value) is None:
        raise ValueError("kalshi_settlement_schema_invalid")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ValueError("kalshi_settlement_schema_invalid") from None


@dataclass(frozen=True, slots=True)
class KalshiFinalMarketState:
    """Immutable validated Market evidence; this type makes no finality claim."""

    ticker: str
    event_ticker: str
    market_type: str
    status: str
    result: str | None
    settlement_value_dollars: str | None
    settlement_ts: str | None
    raw_body: bytes
    raw_sha256: str
    route_tier: str


class KalshiShadowSettlementTransport:
    """Fixed-origin, public, GET-only Market transport."""

    __slots__ = ("_session", "_sleep")

    def __init__(self, *, session: object | None = None, sleep: object | None = None) -> None:
        try:
            chosen_session = requests.Session() if session is None else session
            chosen_session.trust_env = False  # type: ignore[attr-defined]
        except Exception:
            _fail("kalshi_settlement_transport_invalid")
        self._session = chosen_session
        self._sleep = time.sleep if sleep is None else sleep

    @staticmethod
    def _close_after(response: object, operation: object) -> object:
        primary_error: BaseException | None = None
        try:
            return operation()  # type: ignore[operator]
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                response.close()  # type: ignore[attr-defined]
            except Exception:
                if primary_error is None:
                    _fail("kalshi_settlement_close_invalid")

    def _request(self, path: str) -> tuple[int, bytes | None]:
        for attempt in range(len(_GET_429_DELAYS) + 1):
            try:
                response = self._session.request(  # type: ignore[union-attr]
                    "GET", _ORIGIN + path,
                    headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                    allow_redirects=False, stream=True, timeout=(3, 10),
                )
            except Exception:
                _fail("kalshi_settlement_transport_invalid")

            def disposition() -> tuple[int, bytes | None]:
                status = getattr(response, "status_code", None)
                if type(status) is not int:
                    _fail("kalshi_settlement_response_invalid")
                if 300 <= status < 400:
                    _fail("kalshi_settlement_redirect_invalid")
                if status == 404 or status == 429:
                    return status, None
                if status != 200:
                    _fail("kalshi_settlement_status_invalid")
                return status, self._stream_body(response)

            status, body = self._close_after(response, disposition)  # type: ignore[misc]
            if status != 429:
                return status, body
            if attempt == len(_GET_429_DELAYS):
                _fail("kalshi_settlement_rate_limited")
            try:
                self._sleep(_GET_429_DELAYS[attempt])  # type: ignore[operator]
            except Exception:
                _fail("kalshi_settlement_transport_invalid")
        _fail("kalshi_settlement_rate_limited")

    @staticmethod
    def _stream_body(response: object) -> bytes:
        try:
            headers = response.headers  # type: ignore[attr-defined]
            content_type = headers.get("Content-Type")
            content_encoding = headers.get("Content-Encoding")
            content_length = headers.get("Content-Length")
        except Exception:
            _fail("kalshi_settlement_headers_invalid")
        if (type(content_type) is not str or
                content_type.split(";", 1)[0].strip().casefold() != "application/json"):
            _fail("kalshi_settlement_content_type_invalid")
        if content_encoding is not None and (
            type(content_encoding) is not str or content_encoding.strip().casefold() != "identity"
        ):
            _fail("kalshi_settlement_content_encoding_invalid")
        expected_size: int | None = None
        if content_length is not None:
            if type(content_length) is not str or not content_length.isascii() or not content_length.isdigit():
                _fail("kalshi_settlement_headers_invalid")
            expected_size = int(content_length)
            if expected_size > _MAXIMUM_BODY_BYTES:
                _fail("kalshi_settlement_body_too_large")
        chunks: list[bytes] = []
        size = 0
        try:
            iterator = response.iter_content(chunk_size=_STREAM_CHUNK_BYTES)  # type: ignore[attr-defined]
            for chunk in iterator:
                if type(chunk) is not bytes or not chunk:
                    _fail("kalshi_settlement_body_invalid")
                size += len(chunk)
                if size > _MAXIMUM_BODY_BYTES:
                    _fail("kalshi_settlement_body_too_large")
                chunks.append(chunk)
        except KalshiShadowSettlementError:
            raise
        except Exception:
            _fail("kalshi_settlement_body_invalid")
        if size == 0 or (expected_size is not None and size != expected_size):
            _fail("kalshi_settlement_body_invalid")
        return b"".join(chunks)

    @staticmethod
    def _state(ticker: str, body: bytes, route_tier: str) -> KalshiFinalMarketState:
        try:
            value = json.loads(body.decode("utf-8"), object_pairs_hook=_duplicate_keys,
                               parse_constant=_nonfinite)
        except KalshiShadowSettlementError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            _fail("kalshi_settlement_json_invalid")
        if type(value) is not dict or set(value) != {"market"} or type(value["market"]) is not dict:
            _fail("kalshi_settlement_schema_invalid")
        market = value["market"]
        try:
            returned_ticker = _required_text(market, "ticker")
            event_ticker = _required_text(market, "event_ticker")
            market_type = _required_text(market, "market_type")
            status = _required_text(market, "status")
            result = _nullable_text(market, "result")
            settlement_value = _nullable_text(market, "settlement_value_dollars")
            settlement_ts = _nullable_text(market, "settlement_ts")
            if (returned_ticker != ticker or not _safe_ticker(returned_ticker) or
                    not _safe_ticker(event_ticker) or _TOKEN.fullmatch(market_type) is None or
                    status not in _STATUSES):
                raise ValueError("kalshi_settlement_schema_invalid")
            if result is not None and _TOKEN.fullmatch(result) is None:
                raise ValueError("kalshi_settlement_schema_invalid")
            if settlement_value is not None:
                _settlement_decimal(settlement_value)
            if settlement_ts is not None:
                _settlement_timestamp(settlement_ts)
            if status == "finalized" and (
                result is None or settlement_value is None or settlement_ts is None
            ):
                raise ValueError("kalshi_settlement_schema_invalid")
        except KalshiShadowSettlementError:
            raise
        except Exception:
            _fail("kalshi_settlement_schema_invalid")
        return KalshiFinalMarketState(
            ticker=returned_ticker, event_ticker=event_ticker, market_type=market_type,
            status=status, result=result, settlement_value_dollars=settlement_value,
            settlement_ts=settlement_ts, raw_body=body,
            raw_sha256=sha256(body).hexdigest(), route_tier=route_tier,
        )

    def get_market_result(self, ticker: str) -> KalshiFinalMarketState:
        if not _safe_ticker(ticker):
            _fail("kalshi_settlement_query_invalid")
        status, body = self._request(_CURRENT_PATH + ticker)
        if status == 200 and body is not None:
            return self._state(ticker, body, "current")
        # Historical retrieval is permitted only after a fully closed exact 404.
        status, body = self._request(_HISTORICAL_PATH + ticker)
        if status != 200 or body is None:
            _fail("kalshi_settlement_status_invalid")
        return self._state(ticker, body, "historical")
