"""Immutable, read-only discovery values for Kalshi-first tennis evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inci_tennis_adapters.sportradar_trial_v3 import (
        SportradarCompetitionProvenance,
    )


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
    provider_match: ProviderMatchRef | None
    reason: str
    selectable: bool
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HybridChooserSnapshot:
    rows: tuple[HybridMatchRow, ...]
    provider_state: ProviderDiscoveryState
    catalog_sha256: str
    provider_snapshot_sha256: str | None
    coverage_registry_sha256: str
    resolver_version: str
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
