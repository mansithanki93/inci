from __future__ import annotations

import hashlib
import json
import unittest
from urllib.parse import urlsplit


_ORIGIN = "https://external-api.kalshi.com"
_TICKER = "KXATP-26AUG01-ALCARAZ"


def _market(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "ticker": _TICKER,
        "event_ticker": "KXATP-26AUG01-MATCH",
        "market_type": "binary",
        "status": "finalized",
        "result": "yes",
        "settlement_value_dollars": "1.0000",
        "settlement_ts": "2026-08-01T18:30:00Z",
    }
    value.update(overrides)
    return value


class _Response:
    def __init__(self, value: object, *, status: int = 200, body: bytes | None = None,
                 headers: dict[str, object] | None = None) -> None:
        self.status_code = status
        self.content = body if body is not None else json.dumps(
            value, separators=(",", ":")
        ).encode("utf-8")
        self.headers = {
            "Content-Type": "application/json",
            "Content-Encoding": "identity",
            "Content-Length": str(len(self.content)),
        }
        if headers:
            self.headers.update(headers)


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.trust_env = True
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> _Response:
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected network request")
        return self.responses.pop(0)


def _transport(session: _Session):
    from inci_tennis_io.kalshi_shadow_settlement import KalshiShadowSettlementTransport

    return KalshiShadowSettlementTransport(session=session, sleep=lambda _: None)


class KalshiShadowSettlementTransportTests(unittest.TestCase):
    def test_current_market_response_is_authoritative_and_preserved_exactly(self) -> None:
        """Catches changing the public endpoint, request controls, or raw evidence."""
        session = _Session([_Response({"market": _market()})])

        state = _transport(session).get_market_result(_TICKER)

        self.assertEqual(state.ticker, _TICKER)
        self.assertEqual(state.event_ticker, "KXATP-26AUG01-MATCH")
        self.assertEqual(state.settlement_value_dollars, "1.0000")
        self.assertEqual(state.route_tier, "current")
        self.assertEqual(state.raw_body, b'{"market":{"ticker":"KXATP-26AUG01-ALCARAZ","event_ticker":"KXATP-26AUG01-MATCH","market_type":"binary","status":"finalized","result":"yes","settlement_value_dollars":"1.0000","settlement_ts":"2026-08-01T18:30:00Z"}}')
        self.assertEqual(state.raw_sha256, hashlib.sha256(state.raw_body).hexdigest())
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, _ORIGIN + "/trade-api/v2/markets/" + _TICKER)
        self.assertEqual(kwargs["headers"], {"Accept": "application/json", "Accept-Encoding": "identity"})
        self.assertEqual(kwargs["timeout"], (3, 10))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("params", kwargs)
        self.assertFalse(session.trust_env)

    def test_only_exact_current_404_uses_historical_market_route(self) -> None:
        """Catches fallback after a malformed, redirected, or non-404 current response."""
        session = _Session([_Response({}, status=404), _Response({"market": _market()})])

        state = _transport(session).get_market_result(_TICKER)

        self.assertEqual(state.route_tier, "historical")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(urlsplit(session.calls[1][1]).path,
                         "/trade-api/v2/historical/markets/" + _TICKER)

    def test_rejects_unsafe_ticker_without_any_request(self) -> None:
        """Catches ticker interpolation allowing a path outside the Market endpoint."""
        session = _Session([])

        with self.assertRaisesRegex(ValueError, "kalshi_settlement_query_invalid"):
            _transport(session).get_market_result("../portfolio/orders")

        self.assertEqual(session.calls, [])

    def test_rejects_redirect_or_duplicate_json_without_historical_fallback(self) -> None:
        """Catches treating unsafe current evidence as an absent market."""
        for response in (
            _Response({}, status=302),
            _Response({}, body=b'{"market":{},"market":{}}'),
        ):
            session = _Session([response])
            with self.assertRaises(ValueError):
                _transport(session).get_market_result(_TICKER)
            self.assertEqual(len(session.calls), 1)

    def test_rejects_schema_coercion_and_nonfixed_decimal(self) -> None:
        """Catches accepting float, exponent, or missing critical Market evidence."""
        for market in (
            _market(settlement_value_dollars=1.0),
            _market(settlement_value_dollars="1e0"),
            _market(result="void"),
            _market(settlement_ts="2026-08-01T18:30:00"),
        ):
            with self.assertRaisesRegex(ValueError, "kalshi_settlement_schema_invalid"):
                _transport(_Session([_Response({"market": market})])).get_market_result(_TICKER)


if __name__ == "__main__":
    unittest.main()
