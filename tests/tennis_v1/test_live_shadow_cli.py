from __future__ import annotations

import asyncio
import io
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import threading
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
                "sport_event_context": {
                    "sport": {"id": "sr:sport:5", "name": "Tennis"},
                    "category": {"id": "sr:category:3", "name": "ATP"},
                    "competition": {
                        "id": "sr:competition:1",
                        "name": "Washington",
                        "type": "singles",
                        "gender": "men",
                        "level": "professional",
                    },
                    "mode": {"best_of": 3},
                },
            },
            "sport_event_status": {
                "status": "live",
                "match_status": "live",
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


def _hybrid_game(
    event_ticker: str = "KXTENNIS-MATCH",
    *,
    home: str = "Player Home",
    away: str = "Player Away",
    start_wall_ns: int = 1_785_607_200_000_000_000,
    initial_book_state: str = "two_sided",
) -> object:
    from inci_tennis_adapters.shadow_discovery_contracts import (
        KalshiCompetitionProvenance,
        KalshiShadowGame,
        KalshiShadowMarket,
    )

    provenance = KalshiCompetitionProvenance(
        sport="Tennis",
        scope="Games",
        queried_competitions=("ATP",),
        series_ticker="KXATP",
        milestone_id="milestone-1",
        milestone_league="ATP",
    )
    prefix = event_ticker
    tickers = TICKERS if event_ticker == "KXTENNIS-MATCH" else (
        f"{prefix}-HOME",
        f"{prefix}-AWAY",
    )
    return KalshiShadowGame(
        provenance=provenance,
        event_ticker=event_ticker,
        scheduled_start_wall_ns=start_wall_ns,
        game_title=f"{home} v {away}",
        markets=(
            KalshiShadowMarket(
                tickers[0], home, "0.40", "0.42", "2", "3"
            ),
            KalshiShadowMarket(
                tickers[1], away, "0.58", "0.60", "4", "5"
            ),
        ),
        initial_book_state=initial_book_state,
    )


def _hybrid_catalog(
    *games: object,
    exclusions: tuple[object, ...] = (),
) -> object:
    from inci_tennis_adapters.shadow_discovery_contracts import (
        KalshiShadowCatalogSnapshot,
    )

    return KalshiShadowCatalogSnapshot(
        games=(_hybrid_game(),) if not games else games,
        excluded=exclusions,
        catalog_sha256="b" * 64,
    )


class _Context:
    def __init__(
        self,
        value: object | None = None,
        *,
        remaining_session_attempts: int = 100,
        remaining_access_attempts: int = 100,
        state_root: Path = Path("/private/tmp/inci-shadow-evidence"),
        events: list[str] | None = None,
        label: str = "context",
        exit_error: BaseException | None = None,
    ) -> None:
        self.value = self if value is None else value
        self.entered = False
        self.exited = False
        self.remaining_session_attempts = remaining_session_attempts
        self.remaining_access_attempts = remaining_access_attempts
        self.state_root = state_root
        self.events = events
        self.label = label
        self.exit_error = exit_error

    def __enter__(self) -> object:
        self.entered = True
        if self.events is not None:
            self.events.append(f"{self.label}_enter")
        return self.value

    def __exit__(self, *_: object) -> None:
        self.exited = True
        if self.events is not None:
            self.events.append(f"{self.label}_exit")
        if self.exit_error is not None:
            raise self.exit_error

    async def __aenter__(self) -> object:
        self.entered = True
        if self.events is not None:
            self.events.append(f"{self.label}_enter")
        return self.value

    async def __aexit__(self, *_: object) -> None:
        self.exited = True
        if self.events is not None:
            self.events.append(f"{self.label}_exit")
        if self.exit_error is not None:
            raise self.exit_error


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
        error: BaseException | None = None,
        events: list[str] | None = None,
        label: str = "provider",
        exit_error: BaseException | None = None,
    ) -> None:
        super().__init__(events=events, label=label, exit_error=exit_error)
        self.payload = _live_payload() if payload is None else payload
        self.captured_wall_ns = captured_wall_ns
        self.discovery_calls = 0
        self.completed_captures = 1
        self.error = error

    async def fetch_live_summaries(self) -> object:
        self.discovery_calls += 1
        if self.error is not None:
            raise self.error
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

    def discover_tennis_catalog(self) -> object:
        from inci_tennis_adapters.shadow_discovery_contracts import (
            KalshiShadowCatalogSnapshot,
            KalshiShadowGame,
            KalshiShadowMarket,
        )

        self.calls += 1
        converted = []
        for game in self.games:
            template = _hybrid_game(game.event_ticker)
            converted.append(
                KalshiShadowGame(
                    provenance=template.provenance,
                    event_ticker=game.event_ticker,
                    scheduled_start_wall_ns=game.scheduled_start_wall_ns,
                    game_title=game.game_title,
                    markets=tuple(
                        KalshiShadowMarket(
                            market.ticker, market.yes_player_name
                        )
                        for market in game.markets
                    ),
                    initial_book_state="empty",
                )
            )
        return KalshiShadowCatalogSnapshot(
            games=tuple(converted),
            excluded=(),
            catalog_sha256="b" * 64,
        )


class _HybridCatalog:
    def __init__(
        self,
        snapshot: object | None = None,
        *,
        events: list[str] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.snapshot = _hybrid_catalog() if snapshot is None else snapshot
        self.calls = 0
        self.events = events
        self.error = error

    def discover_tennis_catalog(self) -> object:
        self.calls += 1
        if self.events is not None:
            self.events.append("catalog")
        if self.error is not None:
            raise self.error
        return self.snapshot


class _ChooserEvidence(_Context):
    def __init__(
        self,
        events: list[str] | None = None,
        *,
        session_id: str = "11111111-1111-4111-8111-111111111111",
        terminal_row_sha256: str | None = None,
    ) -> None:
        super().__init__(events=events, label="evidence")
        self.resolutions: list[object] = []
        self.halted: list[dict[str, object]] = []
        self.session_id = session_id
        self.terminal_row_sha256 = terminal_row_sha256

    def append_resolution(self, value: object) -> None:
        self.resolutions.append(value)
        if self.events is not None:
            self.events.append("resolution")

    def ensure_halted_terminal(self, **values: object) -> None:
        self.halted.append(values)
        if self.terminal_row_sha256 is None:
            self.terminal_row_sha256 = "d" * 64

    ensure_setup_terminal = ensure_halted_terminal


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


class _PriceCollector:
    def __init__(self, **values: object) -> None:
        self.values = values

    async def run(self, *, duration_seconds: int) -> str:
        self.values["run_duration_seconds"] = duration_seconds
        return "duration_elapsed"


class LiveShadowCliTests(unittest.TestCase):
    def test_no_sportradar_key_still_lists_and_collects_price_only(self) -> None:
        """Catches chooser startup requiring or constructing score-feed state."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        material = SimpleNamespace(
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        catalog = _HybridCatalog()
        price_collectors: list[_PriceCollector] = []
        forbidden: list[str] = []

        def price_factory(**values: object) -> _PriceCollector:
            collector = _PriceCollector(**values)
            price_collectors.append(collector)
            return collector

        errors = io.StringIO()
        code = run_cli(
            ["--choose", "--duration-seconds", "10"],
            environ={},
            stdin=io.StringIO("1\n"),
            stdout=io.StringIO(),
            stderr=errors,
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: forbidden.append("manual_credentials"),
                kalshi_only_credential_loader=lambda _: material,
                trial_ledger_factory=lambda: forbidden.append("trial_ledger"),
                sportradar_transport_factory=lambda **_: forbidden.append("provider"),
                catalog_transport_factory=lambda: catalog,
                evidence_store_factory=_Context,
                evidence_root=lambda: Path("/private/tmp/inci-shadow"),
                kalshi_transport_factory=lambda *_: object(),
                projector_factory=lambda _: object(),
                collector_factory=lambda **_: forbidden.append("verified_collector"),
                price_only_collector_factory=price_factory,
                wall_ns=lambda: GENERATED_NS + 6_000_000_000,
                monotonic_ns=lambda: 10_000_000_000,
            ),
        )

        self.assertEqual(code, 0, errors.getvalue())
        self.assertEqual(catalog.calls, 1)
        self.assertEqual(forbidden, [])
        self.assertEqual(len(price_collectors), 1)
        self.assertEqual(
            price_collectors[0].values["run_duration_seconds"], 10
        )

    def test_catalog_failure_precedes_provider_key_ledger_and_transport(self) -> None:
        """Catches optional provider inspection weakening the primary census."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        events: list[str] = []

        class Environment(dict[str, str]):
            def get(self, key: str, default: object = None) -> object:
                if key == "SPORTRADAR_API_KEY":
                    events.append("provider_key")
                return super().get(key, default)

        material = SimpleNamespace(
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        errors = io.StringIO()
        code = run_cli(
            ["--choose", "--duration-seconds", "10"],
            environ=Environment(SPORTRADAR_API_KEY="secret"),
            stdin=io.StringIO("q\n"),
            stdout=io.StringIO(),
            stderr=errors,
            dependencies=LiveShadowCliDependencies(
                kalshi_only_credential_loader=lambda _: material,
                catalog_transport_factory=lambda: _HybridCatalog(
                    events=events,
                    error=ShadowCollectorError("kalshi_catalog_unavailable"),
                ),
                trial_ledger_factory=lambda: events.append("ledger"),
                sportradar_transport_factory=lambda **_: events.append("provider"),
            ),
        )

        self.assertEqual(code, 1)
        self.assertEqual(events, ["catalog"])
        self.assertEqual(errors.getvalue(), "HALTED: kalshi_catalog_unavailable\n")

    def test_catalog_transport_is_closed_and_primary_failure_wins(self) -> None:
        """Catches leaking discovery HTTP state or cleanup masking catalog evidence."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        class ClosableCatalog(_HybridCatalog):
            def __init__(
                self,
                *,
                body_error: BaseException | None = None,
                close_error: BaseException | None = None,
            ) -> None:
                super().__init__(error=body_error)
                self.close_error = close_error
                self.close_count = 0

            def close(self) -> None:
                self.close_count += 1
                if self.close_error is not None:
                    raise self.close_error

        material = SimpleNamespace(
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        scenarios = (
            (None, None, 0, ""),
            (
                ShadowCollectorError("kalshi_catalog_status_invalid"),
                RuntimeError("kalshi_catalog_session_close_invalid"),
                1,
                "HALTED: kalshi_catalog_status_invalid\n",
            ),
            (
                None,
                RuntimeError("kalshi_catalog_session_close_invalid"),
                1,
                "HALTED: kalshi_catalog_session_close_invalid\n",
            ),
        )
        for body_error, close_error, expected, expected_stderr in scenarios:
            with self.subTest(body_error=body_error, close_error=close_error):
                catalog = ClosableCatalog(
                    body_error=body_error, close_error=close_error
                )
                stderr = io.StringIO()
                code = run_cli(
                    ["--choose"],
                    environ={},
                    stdin=io.StringIO("q\n"),
                    stdout=io.StringIO(),
                    stderr=stderr,
                    dependencies=LiveShadowCliDependencies(
                        kalshi_only_credential_loader=lambda _: material,
                        catalog_transport_factory=lambda: catalog,
                    ),
                )
                self.assertEqual(code, expected)
                self.assertEqual(stderr.getvalue(), expected_stderr)
                self.assertEqual(catalog.close_count, 1)

    def test_stop_during_catalog_prevents_new_provider_work(self) -> None:
        """Catches a stop request still spending optional provider quota after discovery."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            _DashboardOutput,
            _StopState,
            _run_choose,
        )

        stop = _StopState()
        events: list[str] = []

        class StoppingCatalog(_HybridCatalog):
            def discover_tennis_catalog(self) -> object:
                result = super().discover_tennis_catalog()
                stop.request()
                return result

        def ledger_factory() -> object:
            events.append("ledger")
            return _ChooserLedger()

        def provider_factory(**_: object) -> object:
            events.append("provider")
            return _ChooserProvider()

        services = LiveShadowCliDependencies(
            trial_ledger_factory=ledger_factory,
            sportradar_transport_factory=provider_factory,
            catalog_transport_factory=StoppingCatalog,
        )
        material = SimpleNamespace(
            sportradar_api_key="secret",
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )

        with self.assertRaises(KeyboardInterrupt):
            asyncio.run(
                _run_choose(
                    duration_seconds=10,
                    poll_seconds=1,
                    material=material,
                    environ={"SPORTRADAR_API_KEY": "secret"},
                    stdin=io.StringIO("q\n"),
                    stdout=io.StringIO(),
                    output=_DashboardOutput(io.StringIO()),
                    services=services,
                    stop=stop,
                )
            )
        self.assertEqual(events, [])

    def test_discovery_absence_quota_transport_parser_stale_and_empty_downgrade(self) -> None:
        """Catches optional discovery failures removing the Kalshi choice."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        material = SimpleNamespace(
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        cases = (
            ("missing", {}, None, None, "provider_credentials_missing"),
            (
                "quota",
                {"SPORTRADAR_API_KEY": "secret"},
                _ChooserLedger(
                    remaining_session_attempts=0,
                    remaining_access_attempts=1,
                ),
                None,
                "provider_quota_unavailable",
            ),
            (
                "network",
                {"SPORTRADAR_API_KEY": "secret"},
                _ChooserLedger(),
                _ChooserProvider(
                    error=ShadowCollectorError("sportradar_transport_unavailable")
                ),
                "sportradar_transport_unavailable",
            ),
            (
                "parser",
                {"SPORTRADAR_API_KEY": "secret"},
                _ChooserLedger(),
                _ChooserProvider(payload=b"{}"),
                "sportradar_live_summaries_schema_unknown",
            ),
            (
                "stale",
                {"SPORTRADAR_API_KEY": "secret"},
                _ChooserLedger(),
                _ChooserProvider(
                    captured_wall_ns=GENERATED_NS + 61_000_000_000
                ),
                "sportradar_source_stale",
            ),
            (
                "empty",
                {"SPORTRADAR_API_KEY": "secret"},
                _ChooserLedger(),
                _ChooserProvider(
                    payload=json.dumps(
                        {
                            "generated_at": "2026-08-01T18:00:02+00:00",
                            "summaries": [],
                        },
                        separators=(",", ":"),
                    ).encode()
                ),
                "provider_empty",
            ),
        )
        for label, environ, ledger, provider, reason in cases:
            with self.subTest(label=label):
                output = io.StringIO()
                errors = io.StringIO()
                provider_calls: list[str] = []
                selected_ledger = ledger
                selected_provider = provider
                code = run_cli(
                    ["--choose", "--duration-seconds", "10"],
                    environ=environ,
                    stdin=io.StringIO("q\n"),
                    stdout=output,
                    stderr=errors,
                    dependencies=LiveShadowCliDependencies(
                        kalshi_only_credential_loader=lambda _: material,
                        catalog_transport_factory=_HybridCatalog,
                        trial_ledger_factory=lambda: selected_ledger,
                        sportradar_transport_factory=lambda **_: (
                            provider_calls.append("provider") or selected_provider
                        ),
                    ),
                )

                self.assertEqual(code, 0, errors.getvalue())
                self.assertIn("PRICE ONLY", output.getvalue())
                self.assertIn(reason, output.getvalue())
                if label in {"missing", "quota"}:
                    self.assertEqual(provider_calls, [])

    def test_discovery_factory_and_context_entry_failures_downgrade_after_terminal(self) -> None:
        """Catches optional provider startup failures aborting Kalshi-only use."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        class EntryFailure(_ChooserProvider):
            async def __aenter__(self) -> object:
                self.entered = True
                raise ShadowCollectorError("sportradar_transport_unavailable")

        def factory_failure(**_: object) -> object:
            raise ShadowCollectorError("sportradar_transport_unavailable")

        material = SimpleNamespace(
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        for label, provider_factory in (
            ("factory", factory_failure),
            ("context_entry", lambda **_: EntryFailure()),
        ):
            with self.subTest(label=label):
                ledger = _ChooserLedger()
                price_collectors: list[_PriceCollector] = []
                errors = io.StringIO()
                code = run_cli(
                    ["--choose", "--duration-seconds", "10"],
                    environ={"SPORTRADAR_API_KEY": "secret"},
                    stdin=io.StringIO("1\n"),
                    stdout=io.StringIO(),
                    stderr=errors,
                    dependencies=LiveShadowCliDependencies(
                        kalshi_only_credential_loader=lambda _: material,
                        catalog_transport_factory=_HybridCatalog,
                        trial_ledger_factory=lambda: ledger,
                        sportradar_transport_factory=provider_factory,
                        evidence_store_factory=_Context,
                        kalshi_transport_factory=lambda *_: object(),
                        projector_factory=lambda _: object(),
                        price_only_collector_factory=lambda **values: (
                            price_collectors.append(_PriceCollector(**values))
                            or price_collectors[-1]
                        ),
                        wall_ns=lambda: GENERATED_NS + 6_000_000_000,
                        monotonic_ns=lambda: 10_000_000_000,
                    ),
                )

                self.assertEqual(code, 0, errors.getvalue())
                self.assertEqual(len(price_collectors), 1)
                self.assertEqual(
                    ledger.terminals,
                    [
                        {
                            "command": "shadow",
                            "provider_match_id": None,
                            "reason": "halted",
                            "code": "sportradar_transport_unavailable",
                        }
                    ],
                )

    def test_non_bytes_discovery_payload_downgrades_without_raw_binding(self) -> None:
        """Catches hashing malformed provider payloads or retaining partial raw proof."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        material = SimpleNamespace(
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        ledger = _ChooserLedger()
        provider = _ChooserProvider()
        provider.payload = "not-bytes"
        price_collectors: list[_PriceCollector] = []
        errors = io.StringIO()
        code = run_cli(
            ["--choose", "--duration-seconds", "10"],
            environ={"SPORTRADAR_API_KEY": "secret"},
            stdin=io.StringIO("1\n"),
            stdout=io.StringIO(),
            stderr=errors,
            dependencies=LiveShadowCliDependencies(
                kalshi_only_credential_loader=lambda _: material,
                catalog_transport_factory=_HybridCatalog,
                trial_ledger_factory=lambda: ledger,
                sportradar_transport_factory=lambda **_: provider,
                evidence_store_factory=_Context,
                kalshi_transport_factory=lambda *_: object(),
                projector_factory=lambda _: object(),
                price_only_collector_factory=lambda **values: (
                    price_collectors.append(_PriceCollector(**values))
                    or price_collectors[-1]
                ),
                wall_ns=lambda: GENERATED_NS + 6_000_000_000,
                monotonic_ns=lambda: 10_000_000_000,
            ),
        )

        self.assertEqual(code, 0, errors.getvalue())
        self.assertEqual(ledger.terminals[-1]["code"], "sportradar_response_body_invalid")
        session = price_collectors[0].values["session_evidence"]
        self.assertEqual(session.provider_discovery_reason, "sportradar_response_body_invalid")
        self.assertIsNone(session.provider_discovery_raw_path)
        self.assertIsNone(session.provider_discovery_raw_sha256)

    def test_three_sections_share_selectable_numbering_and_conflicts_reprompt(self) -> None:
        """Catches conflict numbering or selection-triggered rediscovery."""

        from inci_tennis_adapters.shadow_discovery_contracts import (
            KalshiCatalogExclusion,
        )
        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        verified = _hybrid_game()
        price = _hybrid_game(
            "KXATP-PRICE",
            home="Neutral One",
            away="Neutral Two",
            start_wall_ns=1_785_607_500_000_000_000,
        )
        conflict_a = _hybrid_game(
            "KXATP-CONFLICT-A", home="Same One", away="Same Two"
        )
        conflict_b = _hybrid_game(
            "KXATP-CONFLICT-B", home="Same One", away="Same Two"
        )
        excluded = KalshiCatalogExclusion(
            "KXATP-EXCLUDED", "active_binary_sibling_count_invalid", None
        )
        catalog = _HybridCatalog(
            _hybrid_catalog(
                verified,
                price,
                conflict_a,
                conflict_b,
                exclusions=(excluded,),
            )
        )
        material = SimpleNamespace(
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        price_collectors: list[_PriceCollector] = []
        output = io.StringIO()
        errors = io.StringIO()
        code = run_cli(
            ["--choose", "--duration-seconds", "10"],
            environ={"SPORTRADAR_API_KEY": "secret"},
            stdin=io.StringIO("3\n2\n"),
            stdout=output,
            stderr=errors,
            dependencies=LiveShadowCliDependencies(
                kalshi_only_credential_loader=lambda _: material,
                catalog_transport_factory=lambda: catalog,
                trial_ledger_factory=_ChooserLedger,
                sportradar_transport_factory=lambda **_: _ChooserProvider(
                    payload=_live_payload(include_second=False)
                ),
                evidence_store_factory=_Context,
                kalshi_transport_factory=lambda *_: object(),
                projector_factory=lambda _: object(),
                price_only_collector_factory=lambda **values: (
                    price_collectors.append(_PriceCollector(**values))
                    or price_collectors[-1]
                ),
                wall_ns=lambda: GENERATED_NS + 6_000_000_000,
                monotonic_ns=lambda: 10_000_000_000,
            ),
        )

        text = output.getvalue()
        self.assertEqual(code, 0, errors.getvalue())
        self.assertEqual(catalog.calls, 1)
        self.assertIn("VERIFIED", text)
        self.assertIn("[1] Player Home vs Player Away", text)
        self.assertIn("PRICE ONLY", text)
        self.assertIn("[2] Neutral One vs Neutral Two", text)
        self.assertIn("CONFLICT / EXCLUDED", text)
        self.assertIn("KXATP-CONFLICT-A", text)
        self.assertNotIn("[3]", text)
        self.assertIn("Invalid selection", text)
        self.assertEqual(len(price_collectors), 1)

    def test_verified_second_preflight_happens_after_choice_before_io(self) -> None:
        """Catches discovery quota being mistaken for collection authority."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        material = SimpleNamespace(
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        ledgers = iter(
            (
                _ChooserLedger(),
                _ChooserLedger(
                    remaining_session_attempts=0,
                    remaining_access_attempts=100,
                ),
            )
        )
        forbidden: list[str] = []
        errors = io.StringIO()
        code = run_cli(
            ["--choose", "--duration-seconds", "10"],
            environ={"SPORTRADAR_API_KEY": "secret"},
            stdin=io.StringIO("1\n"),
            stdout=io.StringIO(),
            stderr=errors,
            dependencies=LiveShadowCliDependencies(
                kalshi_only_credential_loader=lambda _: material,
                catalog_transport_factory=_HybridCatalog,
                trial_ledger_factory=lambda: next(ledgers),
                sportradar_transport_factory=lambda **_: _ChooserProvider(
                    payload=_live_payload(include_second=False)
                ),
                evidence_store_factory=lambda: forbidden.append("evidence"),
                kalshi_transport_factory=lambda *_: forbidden.append("kalshi"),
                collector_factory=lambda **_: forbidden.append("collector"),
                wall_ns=lambda: GENERATED_NS + 6_000_000_000,
                monotonic_ns=lambda: 1_000_000_000,
            ),
        )

        self.assertEqual(code, 1)
        self.assertEqual(forbidden, [])
        self.assertIn("sportradar_shadow_quota_insufficient", errors.getvalue())

    def test_selection_identity_recheck_is_snapshot_only_and_precedes_io(self) -> None:
        """Catches mutated displayed identity starting a different socket."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        game = _hybrid_game()
        catalog = _HybridCatalog(_hybrid_catalog(game))
        forbidden: list[str] = []

        class MutatingInput(io.StringIO):
            def readline(self, *values: object) -> str:
                result = super().readline(*values)
                object.__setattr__(game, "event_ticker", "KXATP-MUTATED")
                return result

        material = SimpleNamespace(
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        errors = io.StringIO()
        code = run_cli(
            ["--choose", "--duration-seconds", "10"],
            environ={"SPORTRADAR_API_KEY": "secret"},
            stdin=MutatingInput("1\n"),
            stdout=io.StringIO(),
            stderr=errors,
            dependencies=LiveShadowCliDependencies(
                kalshi_only_credential_loader=lambda _: material,
                catalog_transport_factory=lambda: catalog,
                evidence_store_factory=lambda: forbidden.append("evidence"),
                kalshi_transport_factory=lambda *_: forbidden.append("kalshi"),
                price_only_collector_factory=lambda **_: forbidden.append("price"),
                wall_ns=lambda: GENERATED_NS + 6_000_000_000,
                monotonic_ns=lambda: 10_000_000_000,
            ),
        )

        self.assertEqual(code, 1)
        self.assertEqual(catalog.calls, 1)
        self.assertEqual(forbidden, [])
        self.assertIn("shadow_selection_identity_changed", errors.getvalue())

    def test_verified_provider_halt_links_fresh_price_session_for_remainder(self) -> None:
        """Catches failover reusing score state or an unbound verified terminal."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )
        from inci_tennis_runtime.live_shadow_collector import (
            LiveShadowCollector,
            ShadowCollectorError,
        )

        class VerifiedFailingProvider(_ChooserProvider):
            def __init__(self, events: list[str]) -> None:
                super().__init__(events=events, label="verified_provider")
                self.completed_captures = 0

            async def fetch_summary(self, match_id: str) -> object:
                del match_id
                raise ShadowCollectorError(
                    "sportradar_transport_unavailable"
                )

            async def fetch_timeline(self, match_id: str) -> object:
                raise AssertionError(f"unexpected timeline: {match_id}")

        class VerifiedEvidence(_ChooserEvidence):
            def append_terminal(self, **values: object) -> None:
                self.halted.append(values)
                self.terminal_row_sha256 = "d" * 64

        class ReadOnlyKalshi:
            async def open_readonly(self) -> None:
                events.append("kalshi_open")

            async def subscribe(self) -> object:
                events.append("kalshi_subscribe")
                return SimpleNamespace(
                    request_id=1,
                    physical_connection_generation=1,
                )

            async def receive_one(self, timeout_seconds: float) -> object:
                del timeout_seconds
                raise ShadowCollectorError("kalshi_ws_receive_timeout")

            async def request_snapshot(self, sid: int) -> object:
                raise AssertionError(f"unexpected snapshot: {sid}")

            async def close(self) -> None:
                events.append("kalshi_close")

        class Projector:
            def begin_subscription(self, receipt: object) -> None:
                del receipt

            def apply(self, frame: object) -> object:
                raise AssertionError(f"unexpected frame: {frame!r}")

            def snapshot_requested(self, receipt: object) -> None:
                raise AssertionError(f"unexpected snapshot: {receipt!r}")

            def disconnect(self, generation: int | None) -> None:
                del generation

        events: list[str] = []
        discovery_ledger = _ChooserLedger(events=events, label="discovery_ledger")
        verified_ledger = _ChooserLedger(events=events, label="verified_ledger")
        ledgers = iter((discovery_ledger, verified_ledger))
        discovery_provider = _ChooserProvider(
            payload=_live_payload(include_second=False),
            events=events,
            label="discovery_provider",
        )
        verified_provider = VerifiedFailingProvider(events)
        providers = iter((discovery_provider, verified_provider))
        verified_evidence = VerifiedEvidence(
            events,
            session_id="11111111-1111-4111-8111-111111111111",
        )
        price_evidence = _Context(events=events, label="price_evidence")
        evidence = iter((verified_evidence, price_evidence))
        price_collectors: list[_PriceCollector] = []
        clocks = iter(
            (
                1_000_000_000,
                2_000_000_000,
                3_000_000_000,
                12_200_000_000,
                12_200_000_000,
                12_200_000_000,
                12_200_000_000,
            )
        )
        kalshi_transports = iter((ReadOnlyKalshi(), object()))
        projectors = iter((Projector(), object()))

        def price_factory(**values: object) -> _PriceCollector:
            events.append("price_collector")
            collector = _PriceCollector(**values)
            price_collectors.append(collector)
            return collector

        material = SimpleNamespace(
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        errors = io.StringIO()
        code = run_cli(
            ["--choose", "--duration-seconds", "30"],
            environ={"SPORTRADAR_API_KEY": "secret"},
            stdin=io.StringIO("1\n"),
            stdout=io.StringIO(),
            stderr=errors,
            dependencies=LiveShadowCliDependencies(
                kalshi_only_credential_loader=lambda _: material,
                catalog_transport_factory=_HybridCatalog,
                trial_ledger_factory=lambda: next(ledgers),
                sportradar_transport_factory=lambda **_: next(providers),
                evidence_store_factory=lambda: next(evidence),
                kalshi_transport_factory=lambda *_: next(kalshi_transports),
                projector_factory=lambda _: next(projectors),
                collector_factory=LiveShadowCollector,
                price_only_collector_factory=price_factory,
                wall_ns=lambda: GENERATED_NS + 6_000_000_000,
                monotonic_ns=lambda: next(clocks),
            ),
        )

        self.assertEqual(code, 0, errors.getvalue())
        self.assertEqual(len(price_collectors), 1)
        session = price_collectors[0].values["session_evidence"]
        self.assertEqual(
            session.predecessor_session_id,
            "11111111-1111-4111-8111-111111111111",
        )
        self.assertEqual(session.predecessor_terminal_row_sha256, "d" * 64)
        self.assertEqual(price_collectors[0].values["run_duration_seconds"], 18)
        self.assertLess(
            events.index("verified_provider_exit"),
            events.index("price_collector"),
        )
        self.assertLess(
            events.index("verified_ledger_exit"),
            events.index("price_collector"),
        )
        forbidden_names = {
            "score",
            "server",
            "provider_age",
            "sportradar_transport",
            "sportradar_ledger",
        }
        self.assertTrue(forbidden_names.isdisjoint(price_collectors[0].values))

    def test_failover_rechecks_absolute_deadline_after_evidence_audit(self) -> None:
        """Catches setup time restarting a stale failover duration below ten seconds."""

        from inci_tennis_adapters.shadow_match_chooser import (
            resolve_hybrid_shadow_matches,
        )
        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            _DashboardOutput,
            _ProviderDiscovery,
            _StopState,
            _VerifiedCollectionHalt,
            _discovery_state,
            _price_session,
            _run_price_choice,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError
        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceStore,
            ShadowResolutionEvidence,
        )

        snapshot = resolve_hybrid_shadow_matches(_hybrid_catalog())
        row = snapshot.rows[0]
        discovery = _ProviderDiscovery(
            None,
            _discovery_state(
                "unavailable", "provider_credentials_missing"
            ),
        )
        clock = {"monotonic": 12_200_000_000}

        class PriceCollector:
            def __init__(self) -> None:
                self.run_calls = 0

            async def run(self, *, duration_seconds: int) -> str:
                del duration_seconds
                self.run_calls += 1
                return "duration_elapsed"

        with tempfile.TemporaryDirectory() as directory:
            root = (Path(directory) / "shadow").resolve()
            provider_capture = (Path(directory) / "provider.json").resolve()
            provider_payload = b'{"provider":"discovery"}'
            provider_capture.write_bytes(provider_payload)
            provider_capture.chmod(0o600)
            with ShadowEvidenceStore(root) as prior:
                prior_ledger = prior.ledger_path
                prior.append_resolution(
                    ShadowResolutionEvidence(
                        selected_wall_ns=GENERATED_NS + 1_000_000_000,
                        provider_match_id=MATCH_ID,
                        provider_start_wall_ns=GENERATED_NS,
                        event_ticker=row.game.event_ticker,
                        home_player_name="Player Home",
                        away_player_name="Player Away",
                        market_tickers=row.market_tickers,
                        provider_discovery_raw_path=str(provider_capture),
                        provider_discovery_raw_sha256=sha256(
                            provider_payload
                        ).hexdigest(),
                        kalshi_catalog_sha256=snapshot.catalog_sha256,
                        resolver_snapshot_sha256=(
                            snapshot.resolver_snapshot_sha256
                        ),
                        resolver_rule_version="strict-name-start-v1",
                    )
                )
                prior.append_terminal(
                    reason="halted",
                    code="sportradar_transport_unavailable",
                    ended_wall_ns=GENERATED_NS + 5_000_000_000,
                    ended_monotonic_ns=12_000_000_000,
                    provider_match_id=MATCH_ID,
                    market_tickers=row.market_tickers,
                    sportradar_captures=0,
                    kalshi_frames=0,
                )
                predecessor = _VerifiedCollectionHalt(
                    "sportradar_transport_unavailable",
                    prior.session_id,
                    prior.terminal_row_sha256 or "",
                )
            collector = PriceCollector()

            def slow_evidence() -> ShadowEvidenceStore:
                store = ShadowEvidenceStore(root)
                clock["monotonic"] = 22_000_000_000
                return store

            services = LiveShadowCliDependencies(
                evidence_store_factory=slow_evidence,
                kalshi_transport_factory=lambda *_: object(),
                projector_factory=lambda _: object(),
                price_only_collector_factory=lambda **_: collector,
                wall_ns=lambda: GENERATED_NS + 6_000_000_000,
                monotonic_ns=lambda: clock["monotonic"],
            )
            with self.assertRaisesRegex(
                ShadowCollectorError, "^sportradar_transport_unavailable$"
            ):
                asyncio.run(
                    _run_price_choice(
                        row=row,
                        snapshot=snapshot,
                        discovery=discovery,
                        duration_seconds=18,
                        material=object(),
                        output=_DashboardOutput(io.StringIO()),
                        services=services,
                        stop=_StopState(),
                        selected_wall_ns=GENERATED_NS + 6_000_000_000,
                        selected_monotonic_ns=12_200_000_000,
                        predecessor=predecessor,
                        deadline_monotonic_ns=31_000_000_000,
                    )
                )
            self.assertEqual(collector.run_calls, 0)
            first_ledger = next(
                path
                for path in root.glob("session-*.jsonl")
                if path != prior_ledger
            )
            first_rows = [
                json.loads(line)
                for line in first_ledger.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [row["kind"] for row in first_rows],
                ["price_only_session", "price_only_terminal"],
            )
            self.assertEqual(
                first_rows[-1]["code"],
                "sportradar_transport_unavailable",
            )
            durable_session = _price_session(
                row=row,
                snapshot=snapshot,
                discovery=discovery,
                selected_wall_ns=GENERATED_NS + 6_000_000_000,
                selected_monotonic_ns=12_200_000_000,
                predecessor_session_id=(
                    predecessor.predecessor_session_id
                ),
                predecessor_terminal_row_sha256=(
                    predecessor.predecessor_terminal_row_sha256
                ),
            )
            with ShadowEvidenceStore(root) as reopened:
                reopened.append_price_only_session(durable_session)
                reopened.ensure_price_only_halted_terminal(
                    code="shadow_internal_error"
                )

    def test_price_setup_failures_never_leave_an_empty_ledger(self) -> None:
        """Catches pre-run constructor errors permanently poisoning the root."""

        from inci_tennis_adapters.shadow_match_chooser import (
            resolve_hybrid_shadow_matches,
        )
        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            _DashboardOutput,
            _ProviderDiscovery,
            _StopState,
            _discovery_state,
            _price_session,
            _run_price_choice,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        snapshot = resolve_hybrid_shadow_matches(_hybrid_catalog())
        row = snapshot.rows[0]
        discovery = _ProviderDiscovery(
            None,
            _discovery_state(
                "unavailable", "provider_credentials_missing"
            ),
        )
        durable_session = _price_session(
            row=row,
            snapshot=snapshot,
            discovery=discovery,
            selected_wall_ns=GENERATED_NS + 6_000_000_000,
            selected_monotonic_ns=12_200_000_000,
            predecessor_session_id=None,
            predecessor_terminal_row_sha256=None,
        )

        def failure(code: str):
            def fail(*_: object, **__: object) -> object:
                raise ShadowCollectorError(code)

            return fail

        cases = (
            (
                "kalshi",
                failure("kalshi_transport_unavailable"),
                lambda _: object(),
                lambda **_: object(),
                "kalshi_transport_unavailable",
            ),
            (
                "projector",
                lambda *_: object(),
                failure("shadow_projector_invalid"),
                lambda **_: object(),
                "shadow_projector_invalid",
            ),
            (
                "collector",
                lambda *_: object(),
                lambda _: object(),
                failure("shadow_price_collector_invalid"),
                "shadow_price_collector_invalid",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            for (
                label,
                kalshi_factory,
                projector_factory,
                collector_factory,
                expected_code,
            ) in cases:
                with self.subTest(label=label):
                    root = (parent / label).resolve()
                    services = LiveShadowCliDependencies(
                        evidence_store_factory=lambda root=root: (
                            ShadowEvidenceStore(root)
                        ),
                        kalshi_transport_factory=kalshi_factory,
                        projector_factory=projector_factory,
                        price_only_collector_factory=collector_factory,
                        wall_ns=lambda: GENERATED_NS + 6_000_000_000,
                        monotonic_ns=lambda: 12_200_000_000,
                    )
                    with self.assertRaisesRegex(
                        ShadowCollectorError, f"^{expected_code}$"
                    ):
                        asyncio.run(
                            _run_price_choice(
                                row=row,
                                snapshot=snapshot,
                                discovery=discovery,
                                duration_seconds=18,
                                material=object(),
                                output=_DashboardOutput(io.StringIO()),
                                services=services,
                                stop=_StopState(),
                                selected_wall_ns=(
                                    GENERATED_NS + 6_000_000_000
                                ),
                                selected_monotonic_ns=12_200_000_000,
                            )
                        )
                    existing = tuple(root.glob("session-*.jsonl"))
                    if label == "collector":
                        self.assertEqual(len(existing), 1)
                        rows = [
                            json.loads(line)
                            for line in existing[0]
                            .read_text(encoding="utf-8")
                            .splitlines()
                        ]
                        self.assertEqual(
                            [item["kind"] for item in rows],
                            ["price_only_session", "price_only_terminal"],
                        )
                    else:
                        self.assertEqual(existing, ())
                    with ShadowEvidenceStore(root) as reopened:
                        reopened.append_price_only_session(durable_session)
                        reopened.ensure_price_only_halted_terminal(
                            code="shadow_internal_error"
                        )

    def test_every_selectable_row_passes_durable_session_admission_first(self) -> None:
        """Catches resolver values reaching IO before exact ledger validation."""

        from inci_tennis_adapters.shadow_discovery_contracts import (
            ProviderDiscoveryState,
        )
        from inci_tennis_adapters.shadow_match_chooser import (
            resolve_hybrid_shadow_matches,
        )
        from inci_tennis_io.shadow_evidence import ShadowEvidenceError
        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            _DashboardOutput,
            _ProviderDiscovery,
            _StopState,
            _run_price_choice,
        )

        invalid_book = _hybrid_game(initial_book_state="empty")
        invalid_decimal = _hybrid_game("KXTENNIS-DECIMAL")
        object.__setattr__(
            invalid_decimal.markets[0], "initial_yes_bid", "00.40"
        )
        cases = (
            (
                "book_state",
                resolve_hybrid_shadow_matches(
                    _hybrid_catalog(invalid_book)
                ),
                ProviderDiscoveryState(
                    "unavailable", "provider_credentials_missing"
                ),
            ),
            (
                "decimal_grammar",
                resolve_hybrid_shadow_matches(
                    _hybrid_catalog(invalid_decimal)
                ),
                ProviderDiscoveryState(
                    "unavailable", "provider_credentials_missing"
                ),
            ),
            (
                "discovery_state",
                resolve_hybrid_shadow_matches(_hybrid_catalog()),
                ProviderDiscoveryState("mystery", "synthetic"),
            ),
        )
        for label, snapshot, state in cases:
            with self.subTest(label=label):
                row = snapshot.rows[0]
                self.assertTrue(row.selectable)
                calls: list[str] = []
                services = LiveShadowCliDependencies(
                    evidence_store_factory=lambda: calls.append("evidence"),
                    kalshi_transport_factory=lambda *_: calls.append("kalshi"),
                    projector_factory=lambda _: calls.append("projector"),
                    price_only_collector_factory=lambda **_: calls.append(
                        "collector"
                    ),
                )
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "^shadow_evidence_row_invalid$"
                ):
                    asyncio.run(
                        _run_price_choice(
                            row=row,
                            snapshot=snapshot,
                            discovery=_ProviderDiscovery(None, state),
                            duration_seconds=18,
                            material=object(),
                            output=_DashboardOutput(io.StringIO()),
                            services=services,
                            stop=_StopState(),
                            selected_wall_ns=(
                                GENERATED_NS + 6_000_000_000
                            ),
                            selected_monotonic_ns=12_200_000_000,
                        )
                    )
                self.assertEqual(calls, [])

    def test_price_setup_interrupts_write_their_exact_terminal_reason(self) -> None:
        """Catches setup cancellation becoming an unshielded generic halt."""

        from inci_tennis_adapters.shadow_match_chooser import (
            resolve_hybrid_shadow_matches,
        )
        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            _DashboardOutput,
            _ProviderDiscovery,
            _StopState,
            _discovery_state,
            _run_price_choice,
        )

        snapshot = resolve_hybrid_shadow_matches(_hybrid_catalog())
        row = snapshot.rows[0]
        discovery = _ProviderDiscovery(
            None,
            _discovery_state(
                "unavailable", "provider_credentials_missing"
            ),
        )

        def raising(error: BaseException):
            def fail(**_: object) -> object:
                raise error

            return fail

        cases = (
            ("cancelled", asyncio.CancelledError(), asyncio.CancelledError),
            ("operator_interrupt", KeyboardInterrupt(), KeyboardInterrupt),
        )
        with tempfile.TemporaryDirectory() as directory:
            for label, error, expected in cases:
                with self.subTest(label=label):
                    root = (Path(directory) / label).resolve()
                    services = LiveShadowCliDependencies(
                        evidence_store_factory=lambda root=root: (
                            ShadowEvidenceStore(root)
                        ),
                        kalshi_transport_factory=lambda *_: object(),
                        projector_factory=lambda _: object(),
                        price_only_collector_factory=raising(error),
                    )
                    with self.assertRaises(expected):
                        asyncio.run(
                            _run_price_choice(
                                row=row,
                                snapshot=snapshot,
                                discovery=discovery,
                                duration_seconds=18,
                                material=object(),
                                output=_DashboardOutput(io.StringIO()),
                                services=services,
                                stop=_StopState(),
                                selected_wall_ns=(
                                    GENERATED_NS + 6_000_000_000
                                ),
                                selected_monotonic_ns=12_200_000_000,
                            )
                        )
                    ledger = next(root.glob("session-*.jsonl"))
                    rows = [
                        json.loads(line)
                        for line in ledger.read_text(encoding="utf-8").splitlines()
                    ]
                    self.assertEqual(rows[-1]["kind"], "price_only_terminal")
                    self.assertEqual(rows[-1]["reason"], label)
                    self.assertIsNone(rows[-1]["code"])

    def test_price_setup_durability_failure_preserves_retained_cancellation(
        self,
    ) -> None:
        """Catches a failed durable append wrapping retained cancellation."""

        from inci_tennis_adapters.shadow_match_chooser import (
            resolve_hybrid_shadow_matches,
        )
        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            _DashboardOutput,
            _ProviderDiscovery,
            _StopState,
            _discovery_state,
            _run_price_choice,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        started = threading.Event()
        release = threading.Event()

        class Evidence(_Context):
            def append_price_only_session(self, _: object) -> None:
                started.set()
                if not release.wait(5):
                    raise AssertionError("test release timed out")
                raise OSError("injected append failure")

            def ensure_price_only_setup_terminal(self, **_: object) -> None:
                raise AssertionError("terminal must not replace cancellation")

        class Transport:
            def __init__(self) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        snapshot = resolve_hybrid_shadow_matches(_hybrid_catalog())
        discovery = _ProviderDiscovery(
            None,
            _discovery_state("unavailable", "provider_credentials_missing"),
        )
        transport = Transport()

        async def scenario() -> None:
            exception_contexts: list[dict[str, object]] = []
            loop = asyncio.get_running_loop()
            loop.set_debug(True)
            loop.set_exception_handler(
                lambda _loop, context: exception_contexts.append(context)
            )
            task = asyncio.create_task(
                _run_price_choice(
                    row=snapshot.rows[0],
                    snapshot=snapshot,
                    discovery=discovery,
                    duration_seconds=18,
                    material=object(),
                    output=_DashboardOutput(io.StringIO()),
                    services=LiveShadowCliDependencies(
                        evidence_store_factory=Evidence,
                        kalshi_transport_factory=lambda *_: transport,
                        projector_factory=lambda _: object(),
                        price_only_collector_factory=lambda **_: (
                            (_ for _ in ()).throw(
                                ShadowCollectorError(
                                    "shadow_price_collector_invalid"
                                )
                            )
                        ),
                    ),
                    stop=_StopState(),
                    selected_wall_ns=GENERATED_NS + 6_000_000_000,
                    selected_monotonic_ns=12_200_000_000,
                )
            )
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            task.cancel()
            await asyncio.sleep(0)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
            self.assertEqual(exception_contexts, [])

        asyncio.run(scenario())
        self.assertTrue(transport.closed)

    def test_stale_provider_choice_forms_a_durable_price_session(self) -> None:
        """Catches the required stale downgrade being rejected at admission."""

        from inci_tennis_adapters.shadow_discovery_contracts import (
            ProviderDiscoveryState,
        )
        from inci_tennis_adapters.shadow_match_chooser import (
            resolve_hybrid_shadow_matches,
        )
        from inci_tennis_runtime.live_shadow_cli import (
            _ProviderDiscovery,
            _price_session,
        )

        snapshot = resolve_hybrid_shadow_matches(
            _hybrid_catalog(),
            provider_state=ProviderDiscoveryState(
                "stale",
                "sportradar_source_stale",
                "a" * 64,
                GENERATED_NS + 5_000_000_000,
            ),
        )
        row = snapshot.rows[0]
        self.assertTrue(row.selectable)
        self.assertEqual(row.status.value, "PRICE_ONLY")
        session = _price_session(
            row=row,
            snapshot=snapshot,
            discovery=_ProviderDiscovery(
                None,
                ProviderDiscoveryState(
                    "stale",
                    "sportradar_source_stale",
                    "a" * 64,
                    GENERATED_NS + 5_000_000_000,
                ),
                "/private/tmp/provider-stale.json",
                "a" * 64,
            ),
            selected_wall_ns=GENERATED_NS + 6_000_000_000,
            selected_monotonic_ns=12_200_000_000,
        )
        self.assertEqual(session.provider_discovery_state, "stale")

    def test_external_cancellation_during_setup_fsync_is_retained(self) -> None:
        """Catches shielded durability swallowing a later task cancellation."""

        from inci_tennis_adapters.shadow_match_chooser import (
            resolve_hybrid_shadow_matches,
        )
        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            _DashboardOutput,
            _ProviderDiscovery,
            _StopState,
            _discovery_state,
            _run_price_choice,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        snapshot = resolve_hybrid_shadow_matches(_hybrid_catalog())
        row = snapshot.rows[0]
        discovery = _ProviderDiscovery(
            None,
            _discovery_state(
                "unavailable", "provider_credentials_missing"
            ),
        )
        started = threading.Event()
        release = threading.Event()

        with tempfile.TemporaryDirectory() as directory:
            root = (Path(directory) / "shadow").resolve()

            class BlockingEvidence(ShadowEvidenceStore):
                def append_price_only_session(self, record: object) -> None:
                    started.set()
                    if not release.wait(timeout=2):
                        raise AssertionError("test release timed out")
                    super().append_price_only_session(record)

            def fail_collector(**_: object) -> object:
                raise ShadowCollectorError("shadow_price_collector_invalid")

            services = LiveShadowCliDependencies(
                evidence_store_factory=lambda: BlockingEvidence(root),
                kalshi_transport_factory=lambda *_: object(),
                projector_factory=lambda _: object(),
                price_only_collector_factory=fail_collector,
            )

            async def scenario() -> None:
                task = asyncio.create_task(
                    _run_price_choice(
                        row=row,
                        snapshot=snapshot,
                        discovery=discovery,
                        duration_seconds=18,
                        material=object(),
                        output=_DashboardOutput(io.StringIO()),
                        services=services,
                        stop=_StopState(),
                        selected_wall_ns=GENERATED_NS + 6_000_000_000,
                        selected_monotonic_ns=12_200_000_000,
                    )
                )
                ready = await asyncio.to_thread(started.wait, 2)
                self.assertTrue(ready)
                task.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            asyncio.run(scenario())
            ledger = next(root.glob("session-*.jsonl"))
            rows = [
                json.loads(line)
                for line in ledger.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [item["kind"] for item in rows],
                ["price_only_session", "price_only_terminal"],
            )

    def test_nonprovider_and_short_remainder_never_start_failover(self) -> None:
        """Catches generic prefix matching or fabricated minimum-duration runs."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        async def real_provider_failure() -> ShadowCollectorError:
            from inci_tennis_runtime.live_shadow_collector import (
                LiveShadowCollector,
            )

            class Provider:
                completed_captures = 0

                async def fetch_summary(self, match_id: str) -> object:
                    del match_id
                    raise ShadowCollectorError(
                        "sportradar_transport_unavailable"
                    )

                async def fetch_timeline(self, match_id: str) -> object:
                    raise AssertionError(f"unexpected timeline: {match_id}")

            class Kalshi:
                async def open_readonly(self) -> None:
                    pass

                async def subscribe(self) -> object:
                    return SimpleNamespace(
                        request_id=1,
                        physical_connection_generation=1,
                    )

                async def receive_one(self, timeout_seconds: float) -> object:
                    del timeout_seconds
                    raise ShadowCollectorError("kalshi_ws_receive_timeout")

                async def request_snapshot(self, sid: int) -> object:
                    raise AssertionError(f"unexpected snapshot: {sid}")

                async def close(self) -> None:
                    pass

            class Ledger:
                def record_session_terminal(self, **values: object) -> None:
                    del values

            class Evidence:
                def append_terminal(self, **values: object) -> None:
                    del values

            class Projector:
                def begin_subscription(self, receipt: object) -> None:
                    del receipt

                def apply(self, frame: object) -> object:
                    raise AssertionError(f"unexpected frame: {frame!r}")

                def snapshot_requested(self, receipt: object) -> None:
                    raise AssertionError(f"unexpected snapshot: {receipt!r}")

                def disconnect(self, generation: int | None) -> None:
                    del generation

            async def pause(seconds: float) -> None:
                del seconds
                await asyncio.sleep(0)

            collector = LiveShadowCollector(
                provider_match_id=MATCH_ID,
                market_tickers=TICKERS,
                sportradar_transport=Provider(),
                sportradar_ledger=Ledger(),
                kalshi_transport=Kalshi(),
                market_projector=Projector(),
                evidence_store=Evidence(),
                wall_ns=lambda: GENERATED_NS + 6_000_000_000,
                monotonic_ns=lambda: 1_000_000_000,
                pause=pause,
                stop_requested=lambda: False,
                render=lambda _: None,
            )
            try:
                await collector.run(duration_seconds=10, poll_seconds=10)
            except ShadowCollectorError as error:
                return error
            raise AssertionError("provider failure was not raised")

        short_attested_error = asyncio.run(real_provider_failure())

        cases = (
            (
                "kalshi",
                ShadowCollectorError("kalshi_transport_unavailable"),
                (1, 2),
            ),
            (
                "evidence",
                ShadowCollectorError("shadow_evidence_write_failed"),
                (1, 2),
            ),
            (
                "unknown",
                ShadowCollectorError("sportradar_new_unknown_failure"),
                (1, 2),
            ),
            (
                "forged",
                ShadowCollectorError("sportradar_transport_unavailable"),
                (1, 2),
            ),
            (
                "short",
                short_attested_error,
                (1_000_000_000, 21_500_000_000),
            ),
        )
        for label, selected_error, clock_values in cases:
            with self.subTest(label=label):
                ledgers = iter((_ChooserLedger(), _ChooserLedger()))
                providers = iter(
                    (
                        _ChooserProvider(payload=_live_payload(include_second=False)),
                        _ChooserProvider(),
                    )
                )
                evidence = _ChooserEvidence()
                price_calls: list[str] = []
                clocks = iter(clock_values)

                class FailedCollector(_Collector):
                    async def run(
                        self, *, duration_seconds: int, poll_seconds: int
                    ) -> str:
                        del duration_seconds, poll_seconds
                        raise selected_error

                material = SimpleNamespace(
                    kalshi_api_key_id="identifier",
                    kalshi_private_key_path=Path("/private/tmp/key.pem"),
                )
                errors = io.StringIO()
                code = run_cli(
                    ["--choose", "--duration-seconds", "30"],
                    environ={"SPORTRADAR_API_KEY": "secret"},
                    stdin=io.StringIO("1\n"),
                    stdout=io.StringIO(),
                    stderr=errors,
                    dependencies=LiveShadowCliDependencies(
                        kalshi_only_credential_loader=lambda _: material,
                        catalog_transport_factory=_HybridCatalog,
                        trial_ledger_factory=lambda: next(ledgers),
                        sportradar_transport_factory=lambda **_: next(providers),
                        evidence_store_factory=lambda: evidence,
                        kalshi_transport_factory=lambda *_: object(),
                        projector_factory=lambda _: object(),
                        collector_factory=FailedCollector,
                        price_only_collector_factory=lambda **_: price_calls.append(
                            "price"
                        ),
                        wall_ns=lambda: GENERATED_NS + 6_000_000_000,
                        monotonic_ns=lambda: next(clocks),
                    ),
                )

                self.assertEqual(code, 1)
                self.assertEqual(price_calls, [])
                self.assertIn(selected_error.code, errors.getvalue())

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
            environ={"SPORTRADAR_API_KEY": "secret"},
            stdin=io.StringIO("x\n2\n"),
            stdout=output,
            stderr=errors,
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: material,
                kalshi_only_credential_loader=lambda _: material,
                trial_ledger_factory=lambda: ledger,
                sportradar_transport_factory=lambda **_: provider,
                catalog_transport_factory=lambda: catalog,
                evidence_store_factory=lambda: evidence,
                evidence_root=lambda: evidence.state_root,
                kalshi_transport_factory=kalshi_factory,
                projector_factory=lambda _: object(),
                collector_factory=collector_factory,
                wall_ns=lambda: GENERATED_NS + 6_000_000_000,
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
        self.assertEqual(
            events,
            ["evidence_enter", "resolution", "kalshi", "evidence_exit"],
        )
        self.assertIn("VERIFIED", output.getvalue())
        self.assertIn("[2] Second Home vs Second Away", output.getvalue())
        self.assertIn("Invalid selection", output.getvalue())
        self.assertIn(
            "READ ONLY / VERIFIED SOURCE LINK / UNQUALIFIED / NO SIGNALS / NO P&L / NO ORDERS",
            output.getvalue(),
        )
        self.assertIn("planned provider calls: 60", output.getvalue())

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
                    kalshi_only_credential_loader=lambda _: material,
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
                    environ={"SPORTRADAR_API_KEY": "secret"},
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

    def test_choose_quota_preflight_is_exchange_first_and_bounds_discovery(self) -> None:
        """Catches spending provider quota before the catalog or at zero budget."""

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
            remaining_session_attempts=0,
            remaining_access_attempts=100,
        )
        calls: list[str] = []
        code = run_cli(
            ["--choose"],
            environ={"SPORTRADAR_API_KEY": "secret"},
            stdin=io.StringIO("q\n"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: material,
                kalshi_only_credential_loader=lambda _: material,
                trial_ledger_factory=lambda: ledger,
                sportradar_transport_factory=lambda **_: calls.append("provider"),
                catalog_transport_factory=lambda: (
                    calls.append("catalog") or _HybridCatalog()
                ),
            ),
        )

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["catalog"])

    def test_choose_downgrades_future_stale_and_malformed_discovery(self) -> None:
        """Catches untrustworthy discovery verifying or removing catalog rows."""

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
                output = io.StringIO()
                errors = io.StringIO()
                code = run_cli(
                    ["--choose", "--duration-seconds", "10"],
                    environ={"SPORTRADAR_API_KEY": "secret"},
                    stdin=io.StringIO("q\n"),
                    stdout=output,
                    stderr=errors,
                    dependencies=LiveShadowCliDependencies(
                        credential_loader=lambda _: material,
                        kalshi_only_credential_loader=lambda _: material,
                        trial_ledger_factory=lambda: ledger,
                        sportradar_transport_factory=lambda **_: provider,
                        catalog_transport_factory=lambda: _ChooserCatalog(),
                    ),
                )
                self.assertEqual(code, 0, errors.getvalue())
                self.assertIn(code_value, output.getvalue())
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
            environ={"SPORTRADAR_API_KEY": "secret"},
            stdin=io.StringIO(),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: material,
                kalshi_only_credential_loader=lambda _: material,
                trial_ledger_factory=lambda: ledger,
                sportradar_transport_factory=lambda **_: CancelledProvider(),
                catalog_transport_factory=_HybridCatalog,
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
        discovery_ledger = _ChooserLedger()
        collection_ledger = _ChooserLedger()
        ledgers = iter((discovery_ledger, collection_ledger))
        providers = iter(
            (
                _ChooserProvider(payload=_live_payload(include_second=False)),
                _ChooserProvider(),
            )
        )
        evidence = TerminalEvidence()

        def fail_kalshi(*_: object) -> object:
            raise ShadowCollectorError("kalshi_transport_unavailable")

        code = run_cli(
            ["--choose", "--duration-seconds", "10"],
            environ={"SPORTRADAR_API_KEY": "secret"},
            stdin=io.StringIO("1\n"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: material,
                kalshi_only_credential_loader=lambda _: material,
                trial_ledger_factory=lambda: next(ledgers),
                sportradar_transport_factory=lambda **_: next(providers),
                catalog_transport_factory=lambda: _ChooserCatalog(),
                evidence_store_factory=lambda: evidence,
                kalshi_transport_factory=fail_kalshi,
                wall_ns=lambda: GENERATED_NS + 6_000_000_000,
            ),
        )

        self.assertEqual(code, 1)
        self.assertEqual(evidence.halted[0]["sportradar_captures"], 1)
        self.assertEqual(evidence.halted[0]["provider_match_id"], MATCH_ID)
        self.assertEqual(
            collection_ledger.terminals[-1],
            {
                "command": "shadow",
                "provider_match_id": MATCH_ID,
                "reason": "halted",
                "code": "sportradar_shadow_discovery_halted",
            },
        )

    def test_prompt_rechecks_stop_before_read_and_before_accepting_choice(self) -> None:
        """Catches a signal race accepting a choice after stop was requested."""

        from inci_tennis_adapters.shadow_match_chooser import (
            resolve_hybrid_shadow_matches,
        )
        from inci_tennis_runtime.live_shadow_cli import (
            _StopState,
            _prompt_choice,
        )

        snapshot = resolve_hybrid_shadow_matches(_hybrid_catalog())

        for phase in ("after_prompt_write", "after_read"):
            with self.subTest(phase=phase):
                stop = _StopState()

                class Output(io.StringIO):
                    def write(self, value: str) -> int:
                        written = super().write(value)
                        if phase == "after_prompt_write":
                            stop.requested = True
                        return written

                class Input(io.StringIO):
                    def readline(self, *values: object) -> str:
                        result = super().readline(*values)
                        if phase == "after_read":
                            stop.requested = True
                        return result

                with self.assertRaises(KeyboardInterrupt):
                    _prompt_choice(
                        snapshot,
                        stdin=Input("1\n"),
                        stdout=Output(),
                        stop=stop,
                    )

    def test_overlong_numeric_choice_is_reprompted_without_integer_conversion(self) -> None:
        """Catches hostile digit input escaping the local invalid-choice loop."""

        from inci_tennis_adapters.shadow_match_chooser import (
            resolve_hybrid_shadow_matches,
        )
        from inci_tennis_runtime.live_shadow_cli import (
            _StopState,
            _prompt_choice,
        )

        snapshot = resolve_hybrid_shadow_matches(_hybrid_catalog())
        output = io.StringIO()
        result = _prompt_choice(
            snapshot,
            stdin=io.StringIO("9" * 5_000 + "\nQ\n"),
            stdout=output,
            stop=_StopState(),
        )

        self.assertIsNone(result)
        self.assertEqual(output.getvalue().count("Invalid selection"), 1)

    def test_tty_ready_screen_retains_safety_claim_and_unavailable_rows(self) -> None:
        """Catches the in-place refresh erasing the chooser's safety label."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            run_cli,
        )

        class Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        material = SimpleNamespace(
            sportradar_api_key="secret",
            kalshi_api_key_id="identifier",
            kalshi_private_key_path=Path("/private/tmp/key.pem"),
        )
        output = Tty()
        code = run_cli(
            ["--choose", "--duration-seconds", "10"],
            environ={},
            stdin=io.StringIO("q\n"),
            stdout=output,
            stderr=io.StringIO(),
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: material,
                kalshi_only_credential_loader=lambda _: material,
                trial_ledger_factory=_ChooserLedger,
                sportradar_transport_factory=lambda **_: _ChooserProvider(),
                catalog_transport_factory=lambda: _ChooserCatalog(
                    _games(include_second=False)
                ),
            ),
        )

        self.assertEqual(code, 0)
        visible = output.getvalue().split("\x1b[2J\x1b[H")[-1]
        self.assertIn(
            "READ ONLY / HYBRID TENNIS EVIDENCE / NO SIGNALS / NO P&L / NO ORDERS",
            visible,
        )
        self.assertIn("PRICE ONLY", visible)
        self.assertIn("CONFLICT / EXCLUDED", visible)
        self.assertNotIn("[2]", visible)

    def test_selection_rechecks_discovery_freshness_after_operator_delay(self) -> None:
        """Catches starting collection from a snapshot that aged at the prompt."""

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
        forbidden: list[str] = []
        code = run_cli(
            ["--choose", "--duration-seconds", "10"],
            environ={"SPORTRADAR_API_KEY": "secret"},
            stdin=io.StringIO("1\n"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: material,
                kalshi_only_credential_loader=lambda _: material,
                trial_ledger_factory=lambda: ledger,
                sportradar_transport_factory=lambda **_: _ChooserProvider(),
                catalog_transport_factory=lambda: _ChooserCatalog(),
                evidence_store_factory=lambda: forbidden.append("evidence"),
                kalshi_transport_factory=lambda *_: forbidden.append("kalshi"),
                wall_ns=lambda: GENERATED_NS + 67_000_000_000,
            ),
        )

        self.assertEqual(code, 1)
        self.assertEqual(forbidden, [])
        self.assertEqual(
            ledger.terminals[-1],
            {
                "command": "shadow",
                "provider_match_id": None,
                "reason": "list_complete",
            },
        )

        def failed_clock() -> int:
            raise ShadowCollectorError("shadow_clock_invalid")

        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        failed_ledger = _ChooserLedger()
        failed_code = run_cli(
            ["--choose", "--duration-seconds", "10"],
            environ={"SPORTRADAR_API_KEY": "secret"},
            stdin=io.StringIO("1\n"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            dependencies=LiveShadowCliDependencies(
                credential_loader=lambda _: material,
                kalshi_only_credential_loader=lambda _: material,
                trial_ledger_factory=lambda: failed_ledger,
                sportradar_transport_factory=lambda **_: _ChooserProvider(),
                catalog_transport_factory=lambda: _ChooserCatalog(),
                evidence_store_factory=lambda: forbidden.append("evidence"),
                wall_ns=failed_clock,
            ),
        )
        self.assertEqual(failed_code, 1)
        self.assertEqual(forbidden, [])
        self.assertEqual(
            failed_ledger.terminals[-1],
            {
                "command": "shadow",
                "provider_match_id": None,
                "reason": "list_complete",
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

    def test_verified_blocked_resolution_append_retains_repeated_cancellation(
        self,
    ) -> None:
        """Catches cancellation abandoning resolution or terminal durability."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            _DashboardOutput,
            _StopState,
            _run_collection,
        )

        started = threading.Event()
        release = threading.Event()
        evidence = _ChooserEvidence()
        ledger = _ChooserLedger()
        kalshi_calls: list[str] = []

        def append_resolution(value: object) -> None:
            started.set()
            if not release.wait(5):
                raise AssertionError("test release timed out")
            evidence.resolutions.append(value)

        evidence.append_resolution = append_resolution  # type: ignore[method-assign]

        async def scenario() -> None:
            task = asyncio.create_task(
                _run_collection(
                    match_id=MATCH_ID,
                    tickers=TICKERS,
                    duration_seconds=10,
                    poll_seconds=1,
                    material=object(),
                    output=_DashboardOutput(io.StringIO()),
                    services=LiveShadowCliDependencies(
                        evidence_store_factory=lambda: evidence,
                        kalshi_transport_factory=lambda *_: kalshi_calls.append(
                            "kalshi"
                        ),
                    ),
                    stop=_StopState(),
                    trial_ledger=ledger,
                    provider=_Context(),
                    mapping_mode="auto_matched",
                    resolution=object(),
                )
            )
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())
        self.assertEqual(len(evidence.resolutions), 1)
        self.assertEqual(
            evidence.halted,
            [
                {
                    "reason": "cancelled",
                    "code": None,
                    "provider_match_id": MATCH_ID,
                    "market_tickers": TICKERS,
                    "sportradar_captures": 0,
                    "kalshi_frames": 0,
                }
            ],
        )
        self.assertEqual(kalshi_calls, [])
        self.assertEqual(ledger.terminals[-1]["reason"], "cancelled")
        self.assertEqual(
            ledger.terminals[-1]["code"],
            "sportradar_shadow_task_cancelled",
        )

    def test_verified_setup_interrupt_and_factory_failures_close_transport(
        self,
    ) -> None:
        """Catches false halt reasons and orphaned pre-collector transports."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            _DashboardOutput,
            _StopState,
            _run_collection,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        class Transport:
            def __init__(self, *, close_error: BaseException | None = None) -> None:
                self.close_error = close_error
                self.closed = 0

            async def close(self) -> None:
                self.closed += 1
                if self.close_error is not None:
                    raise self.close_error

        def run_case(
            error: BaseException,
            *,
            collector_failure: bool,
            close_error: BaseException | None = None,
        ) -> tuple[_ChooserEvidence, _ChooserLedger, Transport]:
            evidence = _ChooserEvidence()
            ledger = _ChooserLedger()
            transport = Transport(close_error=close_error)

            def raise_error(*_: object, **__: object) -> object:
                raise error

            services = LiveShadowCliDependencies(
                evidence_store_factory=lambda: evidence,
                kalshi_transport_factory=lambda *_: transport,
                projector_factory=(
                    (lambda _: object())
                    if collector_failure
                    else raise_error
                ),
                collector_factory=raise_error if collector_failure else _Collector,
            )
            with self.assertRaises(type(error)) as raised:
                asyncio.run(
                    _run_collection(
                        match_id=MATCH_ID,
                        tickers=TICKERS,
                        duration_seconds=10,
                        poll_seconds=1,
                        material=object(),
                        output=_DashboardOutput(io.StringIO()),
                        services=services,
                        stop=_StopState(),
                        trial_ledger=ledger,
                        provider=_Context(),
                        mapping_mode="auto_matched",
                        resolution=object(),
                    )
                )
            self.assertIs(raised.exception, error)
            return evidence, ledger, transport

        for label, collector_failure in (("projector", False), ("collector", True)):
            with self.subTest(label=label):
                original = ShadowCollectorError(f"shadow_{label}_invalid")
                evidence, ledger, transport = run_case(
                    original,
                    collector_failure=collector_failure,
                    close_error=OSError("injected close failure"),
                )
                self.assertEqual(transport.closed, 1)
                self.assertEqual(evidence.halted[-1]["reason"], "halted")
                self.assertEqual(evidence.halted[-1]["code"], original.code)
                self.assertEqual(ledger.terminals[-1]["reason"], "halted")

        evidence, ledger, transport = run_case(
            KeyboardInterrupt(), collector_failure=False
        )
        self.assertEqual(transport.closed, 1)
        self.assertEqual(evidence.halted[-1]["reason"], "operator_interrupt")
        self.assertIsNone(evidence.halted[-1]["code"])
        self.assertEqual(ledger.terminals[-1]["reason"], "operator_interrupt")
        self.assertEqual(
            ledger.terminals[-1]["code"], "sportradar_operator_interrupt"
        )

    def test_verified_real_store_setup_terminals_remain_auditable(self) -> None:
        """Catches writing setup terminals after the real store has closed."""

        from inci_tennis_adapters.shadow_match_chooser import (
            resolve_hybrid_shadow_matches,
        )
        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceStore,
            ShadowResolutionEvidence,
        )
        from inci_tennis_io.sportradar_trial_transport import TrialUsageLedger
        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            _DashboardOutput,
            _StopState,
            _run_collection,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        snapshot = resolve_hybrid_shadow_matches(_hybrid_catalog())
        row = snapshot.rows[0]

        class Transport:
            def __init__(self) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            capture = (parent / "provider.json").resolve()
            payload = b'{"provider":"discovery"}'
            capture.write_bytes(payload)
            capture.chmod(0o600)
            resolution = ShadowResolutionEvidence(
                selected_wall_ns=GENERATED_NS + 1_000_000_000,
                provider_match_id=MATCH_ID,
                provider_start_wall_ns=GENERATED_NS,
                event_ticker=row.game.event_ticker,
                home_player_name="Player Home",
                away_player_name="Player Away",
                market_tickers=row.market_tickers,
                provider_discovery_raw_path=str(capture),
                provider_discovery_raw_sha256=sha256(payload).hexdigest(),
                kalshi_catalog_sha256=snapshot.catalog_sha256,
                resolver_snapshot_sha256=snapshot.resolver_snapshot_sha256,
                resolver_rule_version="strict-name-start-v1",
            )
            cases = (
                (
                    "halted",
                    ShadowCollectorError("shadow_projector_invalid"),
                    "shadow_projector_invalid",
                ),
                ("operator_interrupt", KeyboardInterrupt(), None),
            )
            for reason, error, expected_code in cases:
                with self.subTest(reason=reason):
                    root = (parent / reason).resolve()
                    transport = Transport()

                    def fail_projector(
                        _: object, error: BaseException = error
                    ) -> object:
                        raise error

                    services = LiveShadowCliDependencies(
                        evidence_store_factory=lambda root=root: (
                            ShadowEvidenceStore(root)
                        ),
                        kalshi_transport_factory=lambda *_: transport,
                        projector_factory=fail_projector,
                    )
                    trial_root = (parent / f"{reason}-trial").resolve()
                    with TrialUsageLedger(trial_root) as trial_ledger:
                        with self.assertRaises(type(error)):
                            asyncio.run(
                                _run_collection(
                                    match_id=MATCH_ID,
                                    tickers=row.market_tickers,
                                    duration_seconds=10,
                                    poll_seconds=1,
                                    material=object(),
                                    output=_DashboardOutput(io.StringIO()),
                                    services=services,
                                    stop=_StopState(),
                                    trial_ledger=trial_ledger,
                                    provider=_Context(),
                                    mapping_mode="auto_matched",
                                    resolution=resolution,
                                )
                            )
                    with TrialUsageLedger(trial_root) as reopened_trial:
                        self.assertEqual(
                            reopened_trial.recovered_unclean_sessions, 0
                        )
                        reopened_trial.record_session_terminal(
                            command="shadow",
                            provider_match_id=None,
                            reason="list_complete",
                        )
                    ledger = next(root.glob("session-*.jsonl"))
                    rows = [
                        json.loads(line)
                        for line in ledger.read_text(encoding="utf-8").splitlines()
                    ]
                    self.assertEqual(rows[-1]["kind"], "auto_terminal")
                    self.assertEqual(rows[-1]["reason"], reason)
                    self.assertEqual(rows[-1]["code"], expected_code)
                    self.assertTrue(transport.closed)

                    # Construction audits the prior ledger before opening the
                    # next session; completing that session proves restartability.
                    with ShadowEvidenceStore(root) as reopened:
                        reopened.append_resolution(resolution)
                        reopened.ensure_halted_terminal(
                            code="shadow_internal_error",
                            provider_match_id=MATCH_ID,
                            market_tickers=row.market_tickers,
                            sportradar_captures=0,
                            kalshi_frames=0,
                        )

    def test_verified_setup_close_retains_repeated_cancellation(self) -> None:
        """Catches cancellation abandoning an unstarted transport close."""

        from inci_tennis_runtime.live_shadow_cli import (
            LiveShadowCliDependencies,
            _DashboardOutput,
            _StopState,
            _run_collection,
        )
        from inci_tennis_runtime.live_shadow_collector import ShadowCollectorError

        evidence = _ChooserEvidence()
        ledger = _ChooserLedger()

        async def scenario() -> None:
            close_started = asyncio.Event()
            close_release = asyncio.Event()

            class Transport:
                def __init__(self) -> None:
                    self.closed = False

                async def close(self) -> None:
                    close_started.set()
                    await close_release.wait()
                    self.closed = True

            transport = Transport()

            def fail_projector(_: object) -> object:
                raise ShadowCollectorError("shadow_projector_invalid")

            task = asyncio.create_task(
                _run_collection(
                    match_id=MATCH_ID,
                    tickers=TICKERS,
                    duration_seconds=10,
                    poll_seconds=1,
                    material=object(),
                    output=_DashboardOutput(io.StringIO()),
                    services=LiveShadowCliDependencies(
                        evidence_store_factory=lambda: evidence,
                        kalshi_transport_factory=lambda *_: transport,
                        projector_factory=fail_projector,
                    ),
                    stop=_StopState(),
                    trial_ledger=ledger,
                    provider=_Context(),
                    mapping_mode="auto_matched",
                    resolution=object(),
                )
            )
            await asyncio.wait_for(close_started.wait(), timeout=5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            close_release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(transport.closed)

        asyncio.run(scenario())
        self.assertEqual(len(evidence.halted), 1)
        self.assertEqual(evidence.halted[0]["reason"], "halted")
        self.assertEqual(evidence.halted[0]["code"], "shadow_projector_invalid")
        self.assertEqual(len(ledger.terminals), 1)

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
