"""GET-only public Kalshi Market evidence for shadow settlement.

This module deliberately has no account, portfolio, order, trade, provider, or
outcome-classification capability.  It validates one public Market response and
returns immutable evidence for the reconciliation layer to interpret.
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
_GET_429_DELAYS = (0.25, 0.5, 1.0, 2.0)
_TICKER = pattern_compile(r"[A-Z0-9][A-Z0-9._-]{0,127}\Z")
_FIXED_POINT = pattern_compile(r"\d+(?:\.\d+)?\Z")
_STATUSES = frozenset({
    "initialized", "inactive", "active", "closed", "determined", "disputed",
    "amended", "finalized",
})
_RESULTS = frozenset({"yes", "no"})


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


def _settlement_decimal(value: str) -> None:
    if _FIXED_POINT.fullmatch(value) is None:
        raise ValueError("kalshi_settlement_schema_invalid")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError("kalshi_settlement_schema_invalid") from None
    if not parsed.is_finite():
        raise ValueError("kalshi_settlement_schema_invalid")


def _settlement_timestamp(value: str) -> None:
    if not value.endswith("Z"):
        raise ValueError("kalshi_settlement_schema_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ValueError("kalshi_settlement_schema_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("kalshi_settlement_schema_invalid")


@dataclass(frozen=True, slots=True)
class KalshiFinalMarketState:
    """Validated immutable Market evidence, without a winner interpretation."""

    ticker: str
    event_ticker: str
    market_type: str
    status: str
    result: str
    settlement_value_dollars: str
    settlement_ts: str
    raw_body: bytes
    raw_sha256: str
    route_tier: str


class KalshiShadowSettlementTransport:
    """Public GET-only Market client with tightly bounded retry behavior."""

    __slots__ = ("_session", "_sleep")

    def __init__(self, *, session: object | None = None, sleep: object | None = None) -> None:
        try:
            chosen_session = requests.Session() if session is None else session
            chosen_session.trust_env = False  # type: ignore[attr-defined]
        except Exception:
            _fail("kalshi_settlement_transport_invalid")
        self._session = chosen_session
        self._sleep = time.sleep if sleep is None else sleep

    def _request(self, path: str) -> tuple[int, bytes | None]:
        response = None
        for attempt in range(len(_GET_429_DELAYS) + 1):
            try:
                response = self._session.request(  # type: ignore[union-attr]
                    "GET", _ORIGIN + path,
                    headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                    allow_redirects=False,
                    timeout=(3, 10),
                )
            except Exception:
                _fail("kalshi_settlement_transport_invalid")
            status = getattr(response, "status_code", None)
            if type(status) is not int:
                _fail("kalshi_settlement_response_invalid")
            if status == 429:
                if attempt == len(_GET_429_DELAYS):
                    _fail("kalshi_settlement_rate_limited")
                try:
                    self._sleep(_GET_429_DELAYS[attempt])  # type: ignore[operator]
                except Exception:
                    _fail("kalshi_settlement_transport_invalid")
                continue
            if 300 <= status < 400:
                _fail("kalshi_settlement_redirect_invalid")
            if status == 404:
                return status, None
            if status != 200:
                _fail("kalshi_settlement_status_invalid")
            return status, self._validated_body(response)
        _fail("kalshi_settlement_rate_limited")

    @staticmethod
    def _validated_body(response: object) -> bytes:
        try:
            headers = response.headers  # type: ignore[attr-defined]
            content_type = headers.get("Content-Type")
            content_encoding = headers.get("Content-Encoding")
            content_length = headers.get("Content-Length")
            body = response.content  # type: ignore[attr-defined]
        except Exception:
            _fail("kalshi_settlement_headers_invalid")
        if (type(content_type) is not str or
                content_type.split(";", 1)[0].strip().casefold() != "application/json"):
            _fail("kalshi_settlement_content_type_invalid")
        if content_encoding is not None and (
            type(content_encoding) is not str or content_encoding.strip().casefold() != "identity"
        ):
            _fail("kalshi_settlement_content_encoding_invalid")
        if type(body) is not bytes or not body:
            _fail("kalshi_settlement_body_invalid")
        if len(body) > _MAXIMUM_BODY_BYTES:
            _fail("kalshi_settlement_body_too_large")
        if content_length is not None:
            if type(content_length) is not str or not content_length.isascii() or not content_length.isdigit():
                _fail("kalshi_settlement_headers_invalid")
            if int(content_length) != len(body):
                _fail("kalshi_settlement_body_invalid")
        return body

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
            result = _required_text(market, "result")
            settlement_value = _required_text(market, "settlement_value_dollars")
            settlement_ts = _required_text(market, "settlement_ts")
            if (returned_ticker != ticker or not _safe_ticker(returned_ticker) or
                    not _safe_ticker(event_ticker) or market_type != "binary" or
                    status not in _STATUSES or result not in _RESULTS):
                raise ValueError("kalshi_settlement_schema_invalid")
            _settlement_decimal(settlement_value)
            _settlement_timestamp(settlement_ts)
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
        # Only a fully received current-route 404 reaches this fallback.
        status, body = self._request(_HISTORICAL_PATH + ticker)
        if status != 200 or body is None:
            _fail("kalshi_settlement_status_invalid")
        return self._state(ticker, body, "historical")
