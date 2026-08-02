"""Closed, observation-only provider coverage for hybrid discovery."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from inci_tennis_adapters.shadow_discovery_contracts import KalshiShadowGame
from inci_tennis_adapters.sportradar_trial_v3 import (
    SportradarCompetitionProvenance,
)


_REGISTRY_VERSION = "kalshi-first-provider-coverage-v2"
_KALSHI_SPORT = "Tennis"
_KALSHI_SCOPE = "Games"
_KALSHI_MILESTONE_TYPE = "tennis_tournament_singles"
_PROVIDER_SPORT_ID = "sr:sport:5"
_PROVIDER_SPORT_NAME = "Tennis"
_SUPPORTED_RULES = (
    ("KXATP", "sr:category:3", "singles", "ATP"),
    ("KXCHALLENGER", "sr:category:72", "singles", "Challenger"),
    ("KXWTA", "sr:category:6", "singles", "WTA"),
    ("KXWTA125", "sr:category:871", "singles", "WTA 125K"),
)
_UNSUPPORTED_RULES = (
    ("KXEXHIBITION", "sr:category:79", "singles", "Exhibition"),
    ("KXITFMATCH", "sr:category:785", "singles", "ITF Men"),
    ("KXITFWMATCH", "sr:category:213", "singles", "ITF Women"),
)


@dataclass(frozen=True, slots=True)
class ProviderCoverageAssessment:
    state: str
    reason: str
    canonical_tour: str | None
    authority_scope: str = "observation_only"
    execution_authorized: bool = False


def coverage_registry_sha256_for_tables(
    supported: tuple[tuple[str, str, str, str], ...],
    unsupported: tuple[tuple[str, str, str, str], ...],
) -> str:
    """Hash a canonical closed rule table; table input order has no meaning."""

    projection = {
        "authority_scope": "observation_only",
        "provider_id": "sportradar",
        "supported": [list(rule) for rule in sorted(supported)],
        "unsupported": [list(rule) for rule in sorted(unsupported)],
        "verification_gates": {
            "kalshi_milestone_type": _KALSHI_MILESTONE_TYPE,
            "kalshi_scope": _KALSHI_SCOPE,
            "kalshi_sport": _KALSHI_SPORT,
            "provider_sport_id": _PROVIDER_SPORT_ID,
            "provider_sport_name": _PROVIDER_SPORT_NAME,
        },
        "version": _REGISTRY_VERSION,
    }
    return sha256(
        json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def coverage_registry_sha256() -> str:
    return coverage_registry_sha256_for_tables(_SUPPORTED_RULES, _UNSUPPORTED_RULES)


def assess_provider_route(
    game: KalshiShadowGame,
    provider: SportradarCompetitionProvenance,
) -> ProviderCoverageAssessment:
    """Assess exact structured metadata with default deny and no execution use."""

    if (
        type(game) is not KalshiShadowGame
        or type(provider) is not SportradarCompetitionProvenance
    ):
        return ProviderCoverageAssessment(
            "unclassified", "coverage_input_invalid", None
        )
    provenance = game.provenance
    if provenance.sport != _KALSHI_SPORT or provenance.scope != _KALSHI_SCOPE:
        return ProviderCoverageAssessment(
            "unclassified", "kalshi_provenance_unclassified", None
        )
    if provenance.milestone_type != _KALSHI_MILESTONE_TYPE:
        return ProviderCoverageAssessment(
            "unclassified", "kalshi_milestone_type_price_only", None
        )
    if (
        provider.sport_id != _PROVIDER_SPORT_ID
        or provider.sport_name != _PROVIDER_SPORT_NAME
    ):
        return ProviderCoverageAssessment(
            "unclassified", "provider_sport_unclassified", None
        )
    key = (
        provenance.series_ticker,
        provider.category_id,
        provider.competition_type,
    )
    for series, category, competition_type, tour in _SUPPORTED_RULES:
        if key == (series, category, competition_type):
            return ProviderCoverageAssessment(
                "supported", "coverage_route_supported", tour
            )
    for series, category, competition_type, tour in _UNSUPPORTED_RULES:
        if key == (series, category, competition_type):
            return ProviderCoverageAssessment(
                "unsupported", "coverage_route_unsupported", tour
            )
    return ProviderCoverageAssessment(
        "unclassified", "coverage_route_unclassified", None
    )


__all__ = (
    "ProviderCoverageAssessment",
    "assess_provider_route",
    "coverage_registry_sha256",
    "coverage_registry_sha256_for_tables",
)
