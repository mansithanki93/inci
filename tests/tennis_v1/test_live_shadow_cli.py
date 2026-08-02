from __future__ import annotations

import io
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


MATCH_ID = "sr:sport_event:123456"
TICKERS = ("KXTENNIS-MATCH-HOME", "KXTENNIS-MATCH-AWAY")
SECOND_MATCH_ID = "sr:sport_event:654321"
GENERATED_NS = 1_785_607_202_000_000_000


def _live_payload(*, include_second: bool = True) -> bytes:
    def row(
        match_id: str,
        home_id: str,
        home: str,
        away_id: str,
        away: str,
        minute: int,
    ) -> dict[str, object]:
        return {
            "sport_event": {
                "id": match_id,
                "start_time": f"2026-08-01T18:{minute:02d}:00+00:00",
                "start_time_confirmed": True,
                "competitors": [
                    {"id": home_id, "name": home, "qualifier": "home"},
                    {"id": away_id, "name": away, "qualifier": "away"},
                ],
                "sport_event_context": {"mode": {"best_of": 3}},
            },
            "sport_event_status": {
                "status": "live",
                "match_status": "1st_set",
                "home_score": 0,
                "away_score": 0,
                "period_scores": [],
                "game_state": {
                    "home_score": 0,
                    "away_score": 0,
                    "serving": "home",
                    "tie_break": False,
                },
            },
        }

    rows = [
        row(
            MATCH_ID,
            "sr:competitor:101",
            "Player Home",
            "sr:competitor:202",
            "Player Away",
            0,
        )
    ]
    if include_second:
        rows.append(
            row(
                SECOND_MATCH_ID,
                "sr:competitor:303",
                "Second Home",
                "sr:competitor:404",
                "Second Away",
                5,
            )
        )
    return json.dumps(
        {
            "generated_at": "2026-08-01T18:00:02+00:00",
            "summaries": rows,
        },
        separators=(",", ":"),
    ).encode()


def _games(*, include_second: bool = True) -> tuple[object, ...]:
    from inci_tennis_adapters.shadow_match_chooser import (
        KalshiShadowGame,
        KalshiShadowMarket,
    )

    values = [
        KalshiShadowGame(
            event_ticker="KXTENNIS-MATCH",
            scheduled_start_wall_ns=1_785_607_200_000_000_000,
            game_title="Player Home v Player Away",
            markets=(
                KalshiShadowMarket(TICKERS[1], "Player Away"),
                KalshiShadowMarket(TICKERS[0], "Player Home"),
            ),
        )
    ]
    if include_second:
        values.append(
            KalshiShadowGame(
                event_ticker="KXTENNIS-SECOND",
                scheduled_start_wall_ns=1_785_607_500_000_000_000,
                game_title="Second Home v Second Away",
                markets=(
                    KalshiShadowMarket(
                        "KXTENNIS-SECOND-AWAY", "Second Away"
                    ),
                    KalshiShadowMarket(
                        "KXTENNIS-SECOND-HOME", "Second Home"
                    ),
                ),
            )
        )
    return tuple(values)


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


class _ChooserLedger(_Context):
    def __init__(self, **values: object) -> None:
        super().__init__(**values)
        self.observations: list[object] = []
        self.parser_failures: list[dict[str, object]] = []
        self.terminals: list[dict[str, object]] = []

    def record_observation(self, value: object) -> None:
        self.observations.append(value)

    def record_parser_failure(self, **values: object) -> None:
        self.parser_failures.append(values)

    def record_session_terminal(self, **values: object) -> None:
        self.terminals.append(values)


class _ChooserProvider(_Context):
    def __init__(
        self,
        payload: bytes | None = None,
        *,
        captured_wall_ns: int = GENERATED_NS + 5_000_000_000,
    ) -> None:
        super().__init__()
        self.payload = _live_payload() if payload is None else payload
        self.captured_wall_ns = captured_wall_ns
        self.discovery_calls = 0
        self.completed_captures = 1

    async def fetch_live_summaries(self) -> object:
        self.discovery_calls += 1
        return SimpleNamespace(
            reservation=SimpleNamespace(route="live_summaries"),
            captured_wall_ns=self.captured_wall_ns,
            raw_path=Path("/private/tmp/live-summaries.json"),
            payload=self.payload,
        )


class _ChooserCatalog:
    def __init__(self, games: tuple[object, ...] | None = None) -> None:
        self.games = _games() if games is None else games
        self.calls = 0

    def discover_tennis_games(self) -> tuple[tuple[object, ...], str]:
        self.calls += 1
        return self.games, "b" * 64


class _ChooserEvidence(_Context):
    def __init__(self, events: list[str] | None = None) -> None:
        super().__init__()
        self.resolutions: list[object] = []
        self.events = events

    def append_resolution(self, value: object) -> None:
        self.resolutions.append(value)
        if self.events is not None:
            self.events.append("resolution")


class _Collector:
    def __init__(self, **values: object) -> None:
        self.values = values

    async def run(self, *, duration_seconds: int, poll_seconds: int) -> str:
        self.values["run_duration_seconds"] = duration_seconds
        self.values["run_poll_seconds"] = poll_seconds
        self.values["render"](
            "READ ONLY / UNQUALIFIED / NO ORDERS\n"
            f"duration={duration_seconds} poll={poll_seconds}"
        )
        return "duration_elapsed"


class LiveShadowCliTests(unittest.TestCase):
    def test_choose_lists_once_reprompts_locally_and_runs_second_match(self) -> None:
        """Catches rediscovery or selecting a row other than the displayed number."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        material = SimpleNamespace(
            sportradar_api_key="secret",
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        ledger = _ChooserLedger()
        provider = _ChooserProvider()
        catalog = _ChooserCatalog()
        evidence = _ChooserEvidence(events := [])
        collectors: list[_Collector] = []

        def kalshi_factory(*_: object) -> object:
            events.append("kalshi")
            return object()

        def collector_factory(**values: object) -> _Collector:
            value = _Collector(**values)
            collectors.append(value)
            return value

        output = io.StringIO()
        errors = io.StringIO()
        code = run_cli(
            ["--choose"],
            environ={},
            stdin=io.StringIO("x\n2\n"),
            stdout=output,
            stderr=errors,
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: material,
                trial_ledger_factory=lambda: ledger,
                sportradar_transport_factory=lambda **_: provider,
                catalog_transport_factory=lambda: catalog,
                evidence_store_factory=lambda: evidence,
                evidence_root=lambda: evidence.state_root,
                kalshi_transport_factory=kalshi_factory,
                projector_factory=lambda _: object(),
                collector_factory=collector_factory,
            ),
        )

        self.assertEqual(code, 0, errors.getvalue())
        self.assertEqual(provider.discovery_calls, 1)
        self.assertEqual(catalog.calls, 1)
        self.assertEqual(len(ledger.observations), 1)
        self.assertEqual(
            ledger.observations[0].provider_match_id, None
        )
        self.assertEqual(ledger.observations[0].progression, "discovery")
        self.assertEqual(collectors[0].values["provider_match_id"], SECOND_MATCH_ID)
        self.assertEqual(
            collectors[0].values["market_tickers"],
            ("KXTENNIS-SECOND-HOME", "KXTENNIS-SECOND-AWAY"),
        )
        self.assertEqual(collectors[0].values["mapping_mode"], "auto_matched")
        self.assertEqual(collectors[0].values["run_duration_seconds"], 600)
        self.assertEqual(collectors[0].values["run_poll_seconds"], 10)
        self.assertEqual(len(evidence.resolutions), 1)
        self.assertEqual(evidence.resolutions[0].provider_match_id, SECOND_MATCH_ID)
        self.assertEqual(events, ["resolution", "kalshi"])
        self.assertIn("READY TO COLLECT", output.getvalue())
        self.assertIn("[2] Second Home vs Second Away", output.getvalue())
        self.assertIn("Invalid selection", output.getvalue())
        self.assertIn(
            "READ ONLY / AUTO-MATCHED / UNQUALIFIED / NO ORDERS",
            output.getvalue(),
        )
        self.assertIn("planned provider calls: 61", output.getvalue())

    def test_quit_eof_and_zero_ready_never_create_evidence_or_websocket(self) -> None:
        """Catches opening market IO before an immutable choice exists."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        material = SimpleNamespace(
            sportradar_api_key="secret",
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        for label, stdin, games in (
            ("quit", io.StringIO("q\n"), _games()),
            ("eof", io.StringIO(""), _games()),
            ("zero", io.StringIO("1\n"), ()),
        ):
            with self.subTest(label=label):
                ledger = _ChooserLedger()
                provider = _ChooserProvider()
                catalog = _ChooserCatalog(games)
                forbidden: list[str] = []
                dependencies = LiveShadowCliDependencies(
                    credential_loader=lambda _: material,
                    trial_ledger_factory=lambda: ledger,
                    sportradar_transport_factory=lambda **_: provider,
                    catalog_transport_factory=lambda: catalog,
                    evidence_store_factory=lambda: forbidden.append("evidence"),
                    kalshi_transport_factory=lambda *_: forbidden.append("kalshi"),
                    projector_factory=lambda _: forbidden.append("projector"),
                    collector_factory=lambda **_: forbidden.append("collector"),
                )
                code = run_cli(
                    ["--choose", "--duration-seconds", "10"],
                    environ={},
                    stdin=stdin,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                    dependencies=dependencies,
                )

                self.assertEqual(code, 0)
                self.assertEqual(provider.discovery_calls, 1)
                self.assertEqual(catalog.calls, 1)
                self.assertEqual(forbidden, [])
                self.assertEqual(
                    ledger.terminals,
                    [
                        {
                            "command": "shadow",
                            "provider_match_id": None,
                            "reason": "list_complete",
                        }
                    ],
                )

    def test_choose_quota_preflight_counts_discovery_before_network(self) -> None:
        """Catches spending the extra discovery call outside the trial budget."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        material = SimpleNamespace(
            sportradar_api_key="secret",
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        ledger = _ChooserLedger(
            remaining_session_attempts=60,
            remaining_access_attempts=100,
        )
        calls: list[str] = []
        code = run_cli(
            ["--choose"],
            environ={},
            stdin=io.StringIO("q\n"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: material,
                trial_ledger_factory=lambda: ledger,
                sportradar_transport_factory=lambda **_: calls.append("provider"),
                catalog_transport_factory=lambda: calls.append("catalog"),
            ),
        )

        self.assertEqual(code, 1)
        self.assertEqual(calls, [])

    def test_choose_rejects_future_stale_and_malformed_discovery(self) -> None:
        """Catches resolving names from an untrustworthy provider capture."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        material = SimpleNamespace(
            sportradar_api_key="secret",
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        cases = (
            (
                "future",
                _ChooserProvider(captured_wall_ns=GENERATED_NS - 6_000_000_000),
                "sportradar_source_time_ahead",
            ),
            (
                "stale",
                _ChooserProvider(captured_wall_ns=GENERATED_NS + 61_000_000_000),
                "sportradar_source_stale",
            ),
            (
                "malformed",
                _ChooserProvider(payload=b"{}"),
                "sportradar_live_summaries_schema_unknown",
            ),
        )
        for label, provider, code_value in cases:
            with self.subTest(label=label):
                ledger = _ChooserLedger()
                errors = io.StringIO()
                code = run_cli(
                    ["--choose", "--duration-seconds", "10"],
                    environ={},
                    stdin=io.StringIO("q\n"),
                    stdout=io.StringIO(),
                    stderr=errors,
                    dependencies=LiveShadowCliDependencies(
                        credential_loader=lambda _: material,
                        trial_ledger_factory=lambda: ledger,
                        sportradar_transport_factory=lambda **_: provider,
                        catalog_transport_factory=lambda: _ChooserCatalog(),
                    ),
                )
                self.assertEqual(code, 1)
                self.assertIn(code_value, errors.getvalue())
                self.assertEqual(
                    ledger.terminals[-1],
                    {
                        "command": "shadow",
                        "provider_match_id": None,
                        "reason": "halted",
                        "code": code_value,
                    },
                )
                if label in {"future", "malformed"}:
                    self.assertEqual(ledger.parser_failures[-1]["code"], code_value)

    def test_choose_and_explicit_identifiers_are_mutually_exclusive(self) -> None:
        """Catches silently preferring one identity source over another."""

        from inci_tennis_runtime.live_shadow_cli import run_cli

        self.assertEqual(
            run_cli(
                [
                    "--choose",
                    "--match-id",
                    MATCH_ID,
                    "--home-ticker",
                    TICKERS[0],
                    "--away-ticker",
                    TICKERS[1],
                ],
                environ={},
                stdin=io.StringIO(),
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            ),
            2,
        )

    def test_preselection_cancellation_writes_restartable_cancel_terminal(self) -> None:
        """Catches recording task cancellation as an unexplained halt."""

        import asyncio
        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        class CancelledProvider(_ChooserProvider):
            async def fetch_live_summaries(self) -> object:
                raise asyncio.CancelledError

        material = SimpleNamespace(
            sportradar_api_key="secret",
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        ledger = _ChooserLedger()
        code = run_cli(
            ["--choose", "--duration-seconds", "10"],
            environ={},
            stdin=io.StringIO(),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: material,
                trial_ledger_factory=lambda: ledger,
                sportradar_transport_factory=lambda **_: CancelledProvider(),
            ),
        )

        self.assertEqual(code, 1)
        self.assertEqual(
            ledger.terminals,
            [
                {
                    "command": "shadow",
                    "provider_match_id": None,
                    "reason": "cancelled",
                    "code": "sportradar_shadow_task_cancelled",
                }
            ],
        )

    def test_selected_startup_failure_binds_terminal_and_counts_discovery(self) -> None:
        """Catches losing the chosen identity or discovery count before WS open."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        class TerminalEvidence(_ChooserEvidence):
            def __init__(self) -> None:
                super().__init__()
                self.halted: list[dict[str, object]] = []

            def ensure_halted_terminal(self, **values: object) -> None:
                self.halted.append(values)

        material = SimpleNamespace(
            sportradar_api_key="secret",
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        ledger = _ChooserLedger()
        provider = _ChooserProvider()
        evidence = TerminalEvidence()

        def fail_kalshi(*_: object) -> object:
            raise ShadowCollectorError("kalshi_transport_unavailable")

        code = run_cli(
            ["--choose", "--duration-seconds", "10"],
            environ={},
            stdin=io.StringIO("1\n"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: material,
                trial_ledger_factory=lambda: ledger,
                sportradar_transport_factory=lambda **_: provider,
                catalog_transport_factory=lambda: _ChooserCatalog(),
                evidence_store_factory=lambda: evidence,
                kalshi_transport_factory=fail_kalshi,
            ),
        )

        self.assertEqual(code, 1)
        self.assertEqual(evidence.halted[0]["sportradar_captures"], 1)
        self.assertEqual(evidence.halted[0]["provider_match_id"], MATCH_ID)
        self.assertEqual(
            ledger.terminals[-1],
            {
                "command": "shadow",
                "provider_match_id": MATCH_ID,
                "reason": "halted",
                "code": "sportradar_shadow_discovery_halted",
            },
        )

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
