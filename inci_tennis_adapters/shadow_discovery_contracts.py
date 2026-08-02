"""Immutable, read-only discovery values for Kalshi-first tennis evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from re import compile as pattern_compile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inci_tennis_adapters.sportradar_trial_v3 import (
        SportradarCompetitionProvenance,
    )


_MAX_SIGNED_64 = 9_223_372_036_854_775_807
_DIGEST = pattern_compile(r"[0-9a-f]{64}\Z")
_TICKER = pattern_compile(r"[A-Z0-9][A-Z0-9._-]{0,127}\Z")


class HybridStatus(str, Enum):
    VERIFIED = "VERIFIED"
    PRICE_ONLY = "PRICE_ONLY"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class KalshiCompetitionProvenance:
    sport: str
    scope: str
    queried_competitions: tuple[str, ...]
    series_ticker: str
    milestone_id: str
    milestone_league: str | None

    def __post_init__(self) -> None:
        keys = self.queried_competitions
        if (
            type(keys) is not tuple
            or not keys
            or any(type(key) is not str or not key for key in keys)
            or keys != tuple(sorted(set(keys)))
        ):
            raise ValueError("kalshi_competition_provenance_invalid")


@dataclass(frozen=True, slots=True)
class KalshiShadowMarket:
    ticker: str
    yes_player_name: str
    initial_yes_bid: str | None = None
    initial_yes_ask: str | None = None
    initial_yes_bid_depth: str | None = None
    initial_yes_ask_depth: str | None = None


@dataclass(frozen=True, slots=True)
class KalshiShadowGame:
    provenance: KalshiCompetitionProvenance
    event_ticker: str
    scheduled_start_wall_ns: int
    game_title: str
    markets: tuple[KalshiShadowMarket, KalshiShadowMarket]
    initial_book_state: str


@dataclass(frozen=True, slots=True)
class KalshiCatalogExclusion:
    event_ticker: str
    reason: str
    provenance: KalshiCompetitionProvenance | None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KalshiShadowCatalogSnapshot:
    games: tuple[KalshiShadowGame, ...]
    excluded: tuple[KalshiCatalogExclusion, ...]
    catalog_sha256: str


@dataclass(frozen=True, slots=True)
class ProviderDiscoveryState:
    state: str
    reason: str
    provider_payload_sha256: str | None = None
    captured_wall_ns: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.state) is not str
            or not self.state
            or type(self.reason) is not str
            or not self.reason
        ):
            raise ValueError("provider_discovery_state_invalid")
        if self.state == "available" and (
            self.provider_payload_sha256 is None or self.captured_wall_ns is None
        ):
            raise ValueError("provider_discovery_state_invalid")
        if (
            self.provider_payload_sha256 is not None
            and (
                type(self.provider_payload_sha256) is not str
                or _DIGEST.fullmatch(self.provider_payload_sha256) is None
            )
        ):
            raise ValueError("provider_discovery_state_invalid")
        if self.captured_wall_ns is not None and (
            type(self.captured_wall_ns) is not int
            or not 0 < self.captured_wall_ns <= _MAX_SIGNED_64
        ):
            raise ValueError("provider_discovery_state_invalid")


@dataclass(frozen=True, slots=True)
class ProviderMatchRef:
    provider_match_id: str
    provider_start_wall_ns: int
    home_player_name: str
    away_player_name: str
    status: str
    competition: SportradarCompetitionProvenance


@dataclass(frozen=True, slots=True)
class HybridMatchRow:
    status: HybridStatus
    game: KalshiShadowGame
    market_tickers: tuple[str, str]
    provider_match: ProviderMatchRef | None
    reason: str
    selectable: bool
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        tickers = self.market_tickers
        if (
            type(tickers) is not tuple
            or len(tickers) != 2
            or any(
                type(ticker) is not str or _TICKER.fullmatch(ticker) is None
                for ticker in tickers
            )
            or tickers[0] == tickers[1]
        ):
            raise ValueError("hybrid_match_row_market_tickers_invalid")


@dataclass(frozen=True, slots=True)
class HybridChooserSnapshot:
    rows: tuple[HybridMatchRow, ...]
    provider_state: ProviderDiscoveryState
    catalog_sha256: str
    provider_snapshot_sha256: str | None
    coverage_registry_sha256: str
    resolver_version: str
    provider_diagnostics: tuple[str, ...]
    resolver_snapshot_sha256: str


__all__ = (
    "HybridChooserSnapshot",
    "HybridMatchRow",
    "HybridStatus",
    "KalshiCatalogExclusion",
    "KalshiCompetitionProvenance",
    "KalshiShadowCatalogSnapshot",
    "KalshiShadowGame",
    "KalshiShadowMarket",
    "ProviderDiscoveryState",
    "ProviderMatchRef",
)
