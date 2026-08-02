from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


MATCH_ID = "sr:sport_event:123456"
TICKERS = ("KXTENNIS-MATCH-HOME", "KXTENNIS-MATCH-AWAY")


class _Context:
    def __init__(
        self,
        value: object | None = None,
        *,
        remaining_session_attempts: int = 100,
        remaining_access_attempts: int = 100,
        state_root: Path = Path("/private/tmp/inci-shadow-evidence"),
    ) -> None:
        self.value = self if value is None else value
        self.entered = False
        self.exited = False
        self.remaining_session_attempts = remaining_session_attempts
        self.remaining_access_attempts = remaining_access_attempts
        self.state_root = state_root

    def __enter__(self) -> object:
        self.entered = True
        return self.value

    def __exit__(self, *_: object) -> None:
        self.exited = True

    async def __aenter__(self) -> object:
        self.entered = True
        return self.value

    async def __aexit__(self, *_: object) -> None:
        self.exited = True


class _Collector:
    def __init__(self, **values: object) -> None:
        self.values = values

    async def run(self, *, duration_seconds: int, poll_seconds: int) -> str:
        self.values["render"](
            "READ ONLY / UNQUALIFIED / NO ORDERS\n"
            f"duration={duration_seconds} poll={poll_seconds}"
        )
        return "duration_elapsed"


class LiveShadowCliTests(unittest.TestCase):
    def test_operator_interrupt_returns_shell_interrupt_status(self) -> None:
        """Catches a handled Ctrl-C being reported as normal completion."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        class InterruptedCollector(_Collector):
            async def run(
                self, *, duration_seconds: int, poll_seconds: int
            ) -> str:
                del duration_seconds, poll_seconds
                return "operator_interrupt"

        material = SimpleNamespace(
            sportradar_api_key="secret",
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        code = run_cli(
            [
                "--match-id",
                MATCH_ID,
                "--home-ticker",
                TICKERS[0],
                "--away-ticker",
                TICKERS[1],
                "--duration-seconds",
                "10",
            ],
            environ={},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: material,
                trial_ledger_factory=_Context,
                sportradar_transport_factory=lambda **_: _Context(),
                evidence_store_factory=_Context,
                evidence_root=lambda: Path("/private/tmp/inci-shadow"),
                kalshi_transport_factory=lambda *_: object(),
                projector_factory=lambda _: object(),
                collector_factory=InterruptedCollector,
            ),
        )

        self.assertEqual(code, 130)

    def test_signal_scope_does_not_mislabel_collector_body_errors(self) -> None:
        """Catches converting a collector OSError into a signal setup error."""

        from unittest.mock import patch

        from inci_tennis_runtime.live_shadow_cli import _signals

        original = OSError("collector body failed")
        installed: dict[int, object] = {}

        def install(number: int, handler: object) -> object:
            installed[number] = handler
            return None

        with patch("signal.getsignal", return_value=object()), patch(
            "signal.signal", side_effect=install
        ):
            with self.assertRaises(OSError) as raised:
                with _signals():
                    raise original

        self.assertIs(raised.exception, original)

    def test_post_store_startup_failure_writes_halted_terminal(self) -> None:
        """Catches leaving the evidence store permanently unclean on startup."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        class Evidence(_Context):
            def __init__(self) -> None:
                super().__init__()
                self.terminals: list[dict[str, object]] = []

            def ensure_halted_terminal(self, **values: object) -> None:
                self.terminals.append(values)

        material = SimpleNamespace(
            sportradar_api_key="secret",
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        ledger = _Context()
        provider = _Context()
        evidence = Evidence()

        def fail_collector(**_: object) -> object:
            raise ShadowCollectorError(
                "shadow_collector_configuration_invalid"
            )

        code = run_cli(
            [
                "--match-id",
                MATCH_ID,
                "--home-ticker",
                TICKERS[0],
                "--away-ticker",
                TICKERS[1],
                "--duration-seconds",
                "10",
            ],
            environ={},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: material,
                trial_ledger_factory=lambda: ledger,
                sportradar_transport_factory=lambda **_: provider,
                evidence_store_factory=lambda: evidence,
                evidence_root=lambda: evidence.state_root,
                kalshi_transport_factory=lambda *_: object(),
                projector_factory=lambda _: object(),
                collector_factory=fail_collector,
            ),
        )

        self.assertEqual(code, 1)
        self.assertEqual(
            evidence.terminals,
            [
                {
                    "code": "shadow_collector_configuration_invalid",
                    "provider_match_id": MATCH_ID,
                    "market_tickers": TICKERS,
                    "sportradar_captures": 0,
                    "kalshi_frames": 0,
                }
            ],
        )

    def test_quota_preflight_refuses_before_any_network_capable_factory(self) -> None:
        """Catches starting a partial run that cannot fit the trial budget."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        material = SimpleNamespace(
            sportradar_api_key="secret",
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        ledger = _Context(
            remaining_session_attempts=2,
            remaining_access_attempts=99,
        )
        network_capable_calls: list[str] = []
        dependencies = LiveShadowCliDependencies(
            credential_loader=lambda _: material,
            trial_ledger_factory=lambda: ledger,
            sportradar_transport_factory=lambda **_: network_capable_calls.append(
                "sportradar"
            ),
            evidence_store_factory=lambda: network_capable_calls.append(
                "evidence"
            ),
            kalshi_transport_factory=lambda *_: network_capable_calls.append(
                "kalshi"
            ),
            projector_factory=lambda _: network_capable_calls.append(
                "projector"
            ),
            collector_factory=lambda **_: network_capable_calls.append(
                "collector"
            ),
        )
        output = io.StringIO()
        errors = io.StringIO()
        code = run_cli(
            [
                "--match-id",
                MATCH_ID,
                "--home-ticker",
                TICKERS[0],
                "--away-ticker",
                TICKERS[1],
                "--duration-seconds",
                "21",
                "--poll-seconds",
                "10",
            ],
            environ={},
            stdout=output,
            stderr=errors,
            dependencies=dependencies,
        )

        self.assertEqual(code, 1)
        self.assertEqual(network_capable_calls, [])
        self.assertTrue(ledger.entered and ledger.exited)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(
            errors.getvalue(),
            "HALTED: sportradar_shadow_quota_insufficient\n",
        )

    def test_reducer_projector_exposes_both_books_only_after_barrier(self) -> None:
        """Catches publishing one ticker before the aggregate barrier is ready."""

        from inci_tennis_io.kalshi_readonly import KalshiCommandReceipt
        from inci_tennis_runtime.live_shadow_cli import UnqualifiedKalshiProjector

        projector = UnqualifiedKalshiProjector(TICKERS)
        projector.begin_subscription(
            KalshiCommandReceipt(1, 1, "subscribe")
        )

        def frame(value: object) -> object:
            return SimpleNamespace(
                payload=json.dumps(value, separators=(",", ":")).encode(),
                physical_connection_generation=1,
            )

        subscribed = projector.apply(
            frame(
                {
                    "id": 1,
                    "type": "subscribed",
                    "msg": {"channel": "orderbook_delta", "sid": 27},
                }
            )
        )
        first = projector.apply(
            frame(
                {
                    "type": "orderbook_snapshot",
                    "sid": 27,
                    "seq": 1,
                    "msg": {
                        "market_ticker": TICKERS[0],
                        "market_id": "11111111-2222-3333-4444-555555555555",
                        "yes_dollars_fp": [["0.3100", "12.00"]],
                        "no_dollars_fp": [["0.3400", "8.00"]],
                    },
                }
            )
        )
        second = projector.apply(
            frame(
                {
                    "type": "orderbook_snapshot",
                    "sid": 27,
                    "seq": 2,
                    "msg": {
                        "market_ticker": TICKERS[1],
                        "market_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        "yes_dollars_fp": [["0.6600", "6.00"]],
                        "no_dollars_fp": [["0.6900", "9.00"]],
                    },
                }
            )
        )

        for value in (subscribed, first):
            self.assertEqual(value.status, "incomplete")
            self.assertFalse(value.snapshot_needed)
            self.assertTrue(
                all(market.yes_bid is None for market in value.markets)
            )
        self.assertEqual(second.status, "candidate")
        self.assertEqual(
            tuple(market.ticker for market in second.markets), TICKERS
        )
        self.assertEqual(second.markets[0].yes_bid, "0.3100")
        self.assertEqual(second.markets[0].yes_ask, "0.3400")
        self.assertEqual(second.markets[1].yes_bid, "0.6600")
        self.assertEqual(second.markets[1].yes_ask, "0.6900")

    def test_reducer_projector_treats_empty_book_as_incomplete_not_gap(self) -> None:
        """Catches resnapshot looping on a valid empty executable book."""

        from inci_tennis_io.kalshi_readonly import KalshiCommandReceipt
        from inci_tennis_runtime.live_shadow_cli import UnqualifiedKalshiProjector

        projector = UnqualifiedKalshiProjector(TICKERS)
        projector.begin_subscription(KalshiCommandReceipt(1, 1, "subscribe"))

        def frame(value: object) -> object:
            return SimpleNamespace(
                payload=json.dumps(value, separators=(",", ":")).encode(),
                physical_connection_generation=1,
            )

        projector.apply(
            frame(
                {
                    "id": 1,
                    "type": "subscribed",
                    "msg": {"channel": "orderbook_delta", "sid": 27},
                }
            )
        )
        projector.apply(
            frame(
                {
                    "type": "orderbook_snapshot",
                    "sid": 27,
                    "seq": 1,
                    "msg": {
                        "market_ticker": TICKERS[0],
                        "market_id": "11111111-2222-3333-4444-555555555555",
                    },
                }
            )
        )
        result = projector.apply(
            frame(
                {
                    "type": "orderbook_snapshot",
                    "sid": 27,
                    "seq": 2,
                    "msg": {
                        "market_ticker": TICKERS[1],
                        "market_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        "yes_dollars_fp": [["0.6600", "6.00"]],
                        "no_dollars_fp": [["0.6900", "9.00"]],
                    },
                }
            )
        )

        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.reason, "empty_book")
        self.assertFalse(result.snapshot_needed)
        self.assertTrue(
            all(market.yes_bid is None for market in result.markets)
        )

    def test_rejects_missing_duplicate_and_unsafe_arguments_before_credentials(self) -> None:
        """Catches ambiguous match/market selection or accidental extra options."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        credential_calls: list[object] = []
        dependencies = LiveShadowCliDependencies(
            credential_loader=lambda value=None: credential_calls.append(value),
        )
        invalid = (
            [],
            [
                "--match-id",
                MATCH_ID,
                "--home-ticker",
                TICKERS[0],
                "--away-ticker",
                TICKERS[0],
                "--duration-seconds",
                "10",
            ],
            [
                "--match-id",
                "bad/match",
                "--home-ticker",
                TICKERS[0],
                "--away-ticker",
                TICKERS[1],
                "--duration-seconds",
                "10",
            ],
            [
                "--match-id",
                MATCH_ID,
                "--home-ticker",
                TICKERS[0],
                "--away-ticker",
                TICKERS[1],
                "--duration-seconds",
                "9",
            ],
            [
                "--match-id",
                MATCH_ID,
                "--home-ticker",
                TICKERS[0],
                "--away-ticker",
                TICKERS[1],
                "--duration-seconds",
                "10",
                "--live",
            ],
        )
        for argv in invalid:
            with self.subTest(argv=argv):
                self.assertEqual(
                    run_cli(
                        argv,
                        environ={},
                        stdout=io.StringIO(),
                        stderr=io.StringIO(),
                        dependencies=dependencies,
                    ),
                    2,
                )
        self.assertEqual(credential_calls, [])

    def test_injected_composition_is_read_only_and_never_prints_credentials(self) -> None:
        """Catches leaking credential values or bypassing the collector boundary."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        secret_sr = "SPORTRADAR-SECRET"
        secret_id = "KALSHI-SECRET-ID"
        secret_path = Path("/private/tmp/kalshi-secret.pem")
        material = SimpleNamespace(
            sportradar_api_key=secret_sr,
            kalshi_api_key_id=secret_id,
            kalshi_private_key_path=secret_path,
        )
        ledger = _Context()
        sportradar = _Context()
        evidence = _Context()
        transports: list[tuple[object, tuple[str, str]]] = []
        collectors: list[_Collector] = []

        def collector_factory(**values: object) -> _Collector:
            collector = _Collector(**values)
            collectors.append(collector)
            return collector

        dependencies = LiveShadowCliDependencies(
            credential_loader=lambda _: material,
            trial_ledger_factory=lambda: ledger,
            sportradar_transport_factory=lambda **values: sportradar,
            evidence_store_factory=lambda: evidence,
            evidence_root=lambda: evidence.state_root,
            kalshi_transport_factory=lambda credential, tickers: (
                transports.append((credential, tickers)) or object()
            ),
            projector_factory=lambda tickers: (lambda _: None),
            collector_factory=collector_factory,
            wall_ns=lambda: 1,
            monotonic_ns=lambda: 1,
            pause=lambda _: None,
        )
        output = io.StringIO()
        errors = io.StringIO()
        code = run_cli(
            [
                "--match-id",
                MATCH_ID,
                "--home-ticker",
                TICKERS[0],
                "--away-ticker",
                TICKERS[1],
                "--duration-seconds",
                "10",
            ],
            environ={"ignored": "by injected loader"},
            stdout=output,
            stderr=errors,
            dependencies=dependencies,
        )

        self.assertEqual(code, 0)
        self.assertTrue(ledger.entered and ledger.exited)
        self.assertTrue(sportradar.entered and sportradar.exited)
        self.assertTrue(evidence.entered and evidence.exited)
        self.assertEqual(transports[0][1], TICKERS)
        self.assertEqual(collectors[0].values["provider_match_id"], MATCH_ID)
        self.assertEqual(collectors[0].values["market_tickers"], TICKERS)
        self.assertIn("READ ONLY / UNQUALIFIED / NO ORDERS", output.getvalue())
        self.assertIn("planned provider calls: 1", output.getvalue())
        self.assertIn(
            "ticker mapping: OPERATOR-SUPPLIED / UNVERIFIED",
            output.getvalue(),
        )
        self.assertIn(
            "evidence root: /private/tmp/inci-shadow-evidence",
            output.getvalue(),
        )
        combined = output.getvalue() + errors.getvalue()
        for secret in (secret_sr, secret_id, str(secret_path)):
            self.assertNotIn(secret, combined)
        self.assertNotIn("P&L", combined)
        self.assertNotIn("ORDER", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
