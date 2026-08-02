from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import unittest


_ORIGIN = "https://external-api.kalshi.com"
_TICKER = "KXATP-26AUG01-ALCARAZ"


def _market(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "ticker": _TICKER, "event_ticker": "KXATP-26AUG01-MATCH",
        "market_type": "binary", "status": "finalized", "result": "yes",
        "settlement_value_dollars": "1.0000",
        "settlement_ts": "2026-08-01T18:30:00Z",
    }
    value.update(overrides)
    return value


class _Response:
    def __init__(self, value: object = None, *, status: object = 200,
                 body: bytes | None = None, chunks: list[object] | None = None,
                 headers: dict[str, object] | None = None,
                 close_error: BaseException | None = None) -> None:
        encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.status_code = status
        self._chunks = list(chunks) if chunks is not None else [body if body is not None else encoded]
        self.headers = {"Content-Type": "application/json", "Content-Encoding": "identity",
                        "Content-Length": str(sum(len(c) for c in self._chunks if type(c) is bytes))}
        if headers:
            self.headers.update(headers)
        self.close_error = close_error
        self.close_count = 0

    @property
    def content(self) -> bytes:
        raise AssertionError("transport must stream rather than read response.content")

    def iter_content(self, chunk_size: int) -> object:
        self.chunk_size = chunk_size
        for chunk in self._chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def close(self) -> None:
        self.close_count += 1
        if self.close_error:
            raise self.close_error


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


def _transport(session: _Session, sleeps: list[float] | None = None):
    from inci_tennis_io.kalshi_shadow_settlement import KalshiShadowSettlementTransport
    return KalshiShadowSettlementTransport(session=session, sleep=(sleeps.append if sleeps is not None else lambda _: None))


_FORBIDDEN_IMPORT_TERMS = frozenset({
    "portfolio", "order", "trade", "account", "credential", "provider",
    "strategy", "signal", "executor", "position", "fill", "fee", "pnl",
})
_REVIEWED_TRANSPORT_AST_SHA256 = "00128b2f077dae03c0a62a4c28fa60cd2f704899c50852b13a895c4c6dd7b954"


def _assert_authority_free_ast(tree: ast.AST) -> None:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    assert not any(term in module.casefold() for module in modules for term in _FORBIDDEN_IMPORT_TERMS)


def _canonical_ast_sha256(source: str) -> str:
    canonical = ast.dump(ast.parse(source), include_attributes=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _assert_reviewed_transport_ast(source: str) -> None:
    assert _canonical_ast_sha256(source) == _REVIEWED_TRANSPORT_AST_SHA256


class KalshiShadowSettlementTransportTests(unittest.TestCase):
    def test_current_200_is_authoritative_streamed_and_preserved_exactly(self) -> None:
        """Catches changing the endpoint, request controls, or raw evidence bytes."""
        response = _Response({"market": _market()})
        session = _Session([response])
        state = _transport(session).get_market_result(_TICKER)
        self.assertEqual(state.ticker, _TICKER)
        self.assertEqual(state.route_tier, "current")
        self.assertEqual(state.raw_sha256, hashlib.sha256(state.raw_body).hexdigest())
        self.assertEqual(response.close_count, 1)
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("GET", _ORIGIN + "/trade-api/v2/markets/" + _TICKER))
        self.assertEqual(kwargs, {
            "headers": {"Accept": "application/json", "Accept-Encoding": "identity"},
            "allow_redirects": False, "stream": True, "timeout": (3, 10),
        })
        self.assertFalse(session.trust_env)

    def test_only_closed_exact_current_404_uses_historical_route(self) -> None:
        """Catches fallback after anything other than an exact current-route 404."""
        current, historical = _Response(status=404), _Response({"market": _market()})
        session = _Session([current, historical])
        state = _transport(session).get_market_result(_TICKER)
        self.assertEqual(state.route_tier, "historical")
        self.assertEqual(current.close_count, 1)
        self.assertEqual(historical.close_count, 1)
        self.assertEqual(session.calls[1], (
            "GET", _ORIGIN + "/trade-api/v2/historical/markets/" + _TICKER,
            {"headers": {"Accept": "application/json", "Accept-Encoding": "identity"},
             "allow_redirects": False, "stream": True, "timeout": (3, 10)},
        ))

    def test_429_retries_with_exact_bounded_schedule_and_closes_every_attempt(self) -> None:
        """Catches unbounded or mistimed rate-limit retry behavior."""
        responses = [_Response(status=429), _Response(status=429), _Response({"market": _market()})]
        sleeps: list[float] = []
        state = _transport(_Session(responses), sleeps).get_market_result(_TICKER)
        self.assertEqual(state.route_tier, "current")
        self.assertEqual(sleeps, [0.25, 0.5])
        self.assertEqual([response.close_count for response in responses], [1, 1, 1])

    def test_429_exhaustion_closes_each_response(self) -> None:
        """Catches a final 429 leaking a response or retrying without a bound."""
        responses = [_Response(status=429) for _ in range(5)]
        sleeps: list[float] = []
        with self.assertRaisesRegex(ValueError, "kalshi_settlement_rate_limited"):
            _transport(_Session(responses), sleeps).get_market_result(_TICKER)
        self.assertEqual(sleeps, [0.25, 0.5, 1.0, 2.0])
        self.assertEqual([response.close_count for response in responses], [1] * 5)

    def test_primary_error_wins_over_close_error_and_successful_close_error_fails(self) -> None:
        """Catches close failures masking primary evidence errors or being ignored."""
        invalid = _Response({"market": _market()}, headers={"Content-Type": "text/plain"}, close_error=OSError())
        with self.assertRaisesRegex(ValueError, "kalshi_settlement_content_type_invalid"):
            _transport(_Session([invalid])).get_market_result(_TICKER)
        self.assertEqual(invalid.close_count, 1)
        valid = _Response({"market": _market()}, close_error=OSError())
        with self.assertRaisesRegex(ValueError, "kalshi_settlement_close_invalid"):
            _transport(_Session([valid])).get_market_result(_TICKER)
        self.assertEqual(valid.close_count, 1)

    def test_close_keyboard_interrupt_never_masks_primary_or_becomes_a_value_error(self) -> None:
        """Catches cleanup swallowing an interrupt or replacing a schema error with it."""
        invalid = _Response({"market": _market()}, headers={"Content-Type": "text/plain"}, close_error=KeyboardInterrupt())
        with self.assertRaisesRegex(ValueError, "kalshi_settlement_content_type_invalid"):
            _transport(_Session([invalid])).get_market_result(_TICKER)
        valid = _Response({"market": _market()}, close_error=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            _transport(_Session([valid])).get_market_result(_TICKER)

    def test_nonfinal_null_or_empty_settlement_fields_are_admitted_without_final_claim(self) -> None:
        """Catches transport declaring a non-finalized Market invalid or final."""
        for fields, expected in (
            ({"result": None, "settlement_value_dollars": None, "settlement_ts": None}, (None, None, None)),
            ({"result": "", "settlement_value_dollars": "", "settlement_ts": ""}, ("", "", "")),
            ({"result": "yes", "settlement_value_dollars": "0.0000", "settlement_ts": "2026-08-01T18:30:00Z"}, ("yes", "0.0000", "2026-08-01T18:30:00Z")),
        ):
            state = _transport(_Session([_Response({"market": _market(status="determined", **fields)})])).get_market_result(_TICKER)
            self.assertEqual(state.status, "determined")
            self.assertEqual((state.result, state.settlement_value_dollars, state.settlement_ts), expected)

    def test_scalar_and_unknown_safe_final_literals_are_evidence_not_transport_rejections(self) -> None:
        """Catches 7A classifying scalar/void evidence instead of preserving it for 7B."""
        state = _transport(_Session([_Response({"market": _market(market_type="scalar", result="void-cancel", settlement_value_dollars="0", settlement_ts="2026-08-01T18:30:00Z")})])).get_market_result(_TICKER)
        self.assertEqual((state.market_type, state.result), ("scalar", "void-cancel"))

    def test_rejects_status_http_header_and_stream_failures_without_fallback(self) -> None:
        """Catches malformed current responses being treated as historical absence."""
        bad_responses = (
            _Response(status=True), _Response(status=302), _Response(status=400), _Response(status=500),
            _Response({"market": _market()}, headers={"Content-Encoding": "gzip"}),
            _Response({"market": _market()}, headers={"Content-Length": "8388609"}),
            _Response({"market": _market()}, chunks=[b"x" * 8_388_609], headers={"Content-Length": None}),
            _Response({"market": _market()}, chunks=["not-bytes"]),
            _Response({"market": _market()}, chunks=[OSError("read")]),
        )
        for response in bad_responses:
            session = _Session([response])
            with self.assertRaises(ValueError):
                _transport(session).get_market_result(_TICKER)
            self.assertEqual(len(session.calls), 1)
            self.assertEqual(response.close_count, 1)

    def test_rejects_utf8_nonfinite_nested_duplicates_bad_wrapper_and_identity_drift(self) -> None:
        """Catches JSON ambiguity or an unrelated Market being accepted as requested evidence."""
        bodies = (
            b"\xff", b'{"market":{"x":NaN}}', b'{"market":{"a":{"x":1,"x":2}}}',
            b'{"market":{},"extra":1}',
            json.dumps({"market": _market(ticker="OTHER")}).encode(),
        )
        for body in bodies:
            response = _Response(body=body)
            with self.assertRaises(ValueError):
                _transport(_Session([response])).get_market_result(_TICKER)
            self.assertEqual(response.close_count, 1)

    def test_rejects_unsafe_ticker_without_request(self) -> None:
        """Catches ticker interpolation allowing an authority-bearing path."""
        session = _Session([])
        with self.assertRaisesRegex(ValueError, "kalshi_settlement_query_invalid"):
            _transport(session).get_market_result("../portfolio/orders")
        self.assertEqual(session.calls, [])

    def test_rejects_bad_timestamps_fixed_point_and_wrong_nullable_types(self) -> None:
        """Catches coercion, relaxed UTC grammar, and bad leading-zero numeric text."""
        bad = (
            _market(settlement_ts="20260801T183000Z"), _market(settlement_ts="2026-08-01T18:30:00+00:00"),
            _market(settlement_ts="2026-02-30T18:30:00Z"), _market(settlement_value_dollars="01.0"),
            _market(settlement_value_dollars="1e0"), _market(result=1),
            _market(status="determined", result=None, settlement_value_dollars=0, settlement_ts=None),
        )
        for market in bad:
            with self.assertRaisesRegex(ValueError, "kalshi_settlement_schema_invalid"):
                _transport(_Session([_Response({"market": market})])).get_market_result(_TICKER)

    def test_transport_ast_matches_exact_reviewed_program_and_forbids_authority_imports(self) -> None:
        """Catches any executable change or an imported authority-bearing capability."""
        source_path = Path(__file__).parents[2] / "inci_tennis_io" / "kalshi_shadow_settlement.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        _assert_authority_free_ast(tree)
        _assert_reviewed_transport_ast(source)

    def test_transport_ast_seal_ignores_comments_and_formatting(self) -> None:
        """Catches hashing source bytes instead of the location-free canonical AST."""
        source_path = Path(__file__).parents[2] / "inci_tennis_io" / "kalshi_shadow_settlement.py"
        source = source_path.read_text(encoding="utf-8")
        _assert_reviewed_transport_ast("# review-only comment\n\n" + source)
        _assert_reviewed_transport_ast(ast.unparse(ast.parse(source)))

    def test_ast_policy_rejects_all_reviewed_executable_mutations(self) -> None:
        """Catches executable authority changes outside handcrafted request checks."""
        source_path = Path(__file__).parents[2] / "inci_tennis_io" / "kalshi_shadow_settlement.py"
        source = source_path.read_text(encoding="utf-8")
        mutations = (
            ("auth assignment", "    def _request(self, path: str) -> tuple[int, bytes | None]:\n",
             "    def _request(self, path: str) -> tuple[int, bytes | None]:\n        self.s.auth = credentials\n"),
            ("post call", "    def _request(self, path: str) -> tuple[int, bytes | None]:\n",
             "    def _request(self, path: str) -> tuple[int, bytes | None]:\n        self.s.post(path)\n"),
            ("portfolio traversal", "_CURRENT_PATH + ticker", "_CURRENT_PATH + \"../portfolio/orders\""),
            ("post method", "\"GET\", _ORIGIN + path,", "\"POST\", _ORIGIN + path,"),
            ("auth header", "{\"Accept\": \"application/json\", \"Accept-Encoding\": \"identity\"}",
             "{\"Accept\": \"application/json\", \"Accept-Encoding\": \"identity\", \"Authorization\": credentials}"),
            ("params", "allow_redirects=False, stream=True, timeout=(3, 10),",
             "params={}, allow_redirects=False, stream=True, timeout=(3, 10),"),
            ("third positional", "\"GET\", _ORIGIN + path,", "\"GET\", _ORIGIN + path, body,"),
        )
        for label, old, new in mutations:
            with self.subTest(label=label):
                self.assertEqual(source.count(old), 1)
                mutated = source.replace(old, new)
                self.assertNotEqual(_canonical_ast_sha256(mutated), _REVIEWED_TRANSPORT_AST_SHA256)
                with self.assertRaises(AssertionError):
                    _assert_reviewed_transport_ast(mutated)

    def test_forbidden_import_semantic_check_rejects_authority_module(self) -> None:
        """Catches a forbidden capability import independently of the exact AST seal."""
        with self.assertRaises(AssertionError):
            _assert_authority_free_ast(ast.parse("from product.provider import Client"))


if __name__ == "__main__":
    unittest.main()
