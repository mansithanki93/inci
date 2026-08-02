"""Pure, strict resolver for unqualified live tennis match choices."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from re import compile as pattern_compile
from unicodedata import category, normalize

from inci_tennis_adapters.sportradar_trial_v3 import (
    SportradarLiveSummariesSnapshot,
    SportradarScoreSnapshot,
)


_MAX_NAME_BYTES = 256
_MAX_TITLE_BYTES = 512
_MAX_SIGNED_64 = 9_223_372_036_854_775_807
_TICKER = pattern_compile(r"[A-Z0-9][A-Z0-9._-]{0,127}\Z")
_DIGEST = pattern_compile(r"[0-9a-f]{64}\Z")
_PLACEHOLDERS = frozenset(
    {
        "tbd",
        "tba",
        "to be determined",
        "to be announced",
        "unknown",
        "n/a",
        "na",
        "none",
        "null",
        "-",
        "?",
    }
)


@dataclass(frozen=True, slots=True)
class KalshiShadowMarket:
    ticker: str
    yes_player_name: str


@dataclass(frozen=True, slots=True)
class KalshiShadowGame:
    event_ticker: str
    scheduled_start_wall_ns: int
    game_title: str
    markets: tuple[KalshiShadowMarket, KalshiShadowMarket]


@dataclass(frozen=True, slots=True)
class ShadowMatchChoice:
    provider_match_id: str
    provider_start_wall_ns: int
    event_ticker: str
    home_player_name: str
    away_player_name: str
    market_tickers: tuple[str, str]


@dataclass(frozen=True, slots=True)
class ShadowUnavailableMatch:
    source: str
    identity: str
    display_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class ShadowChooserSnapshot:
    ready: tuple[ShadowMatchChoice, ...]
    unavailable: tuple[ShadowUnavailableMatch, ...]
    provider_payload_sha256: str
    kalshi_catalog_sha256: str
    resolver_snapshot_sha256: str


def normalize_player_name(value: str) -> str:
    """Return the one allowed player-name identity form, or reject it."""
    if type(value) is not str:
        raise ValueError("shadow_player_name_invalid")
    value = normalize("NFKC", value)
    if any(category(character) == "Cc" for character in value):
        raise ValueError("shadow_player_name_invalid")
    value = " ".join(value.split()).casefold()
    if (
        not value
        or value in _PLACEHOLDERS
        or len(value.encode("utf-8")) > _MAX_NAME_BYTES
    ):
        raise ValueError("shadow_player_name_invalid")
    return value


def _safe_ticker(value: object) -> bool:
    return type(value) is str and _TICKER.fullmatch(value) is not None


def _safe_title(value: object) -> bool:
    if type(value) is not str or not value.strip():
        return False
    if any(category(character) == "Cc" for character in value):
        return False
    return len(value.encode("utf-8")) <= _MAX_TITLE_BYTES


def _positive_wall_ns(value: object) -> bool:
    return (
        type(value) is int
        and 0 < value <= _MAX_SIGNED_64
    )


def _valid_digest(value: object) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _provider_identity(value: object) -> str:
    if type(value) is str and value and not any(
        category(character) == "Cc" for character in value
    ):
        return value
    return "provider_identity_invalid"


def _provider_display(row: SportradarScoreSnapshot) -> str:
    if type(row.home_name) is str and type(row.away_name) is str:
        if not any(category(character) == "Cc" for character in row.home_name + row.away_name):
            return f"{row.home_name} v {row.away_name}"
    return "provider_players_invalid"


def _unavailable_provider(
    row: SportradarScoreSnapshot,
    reason: str,
) -> ShadowUnavailableMatch:
    return ShadowUnavailableMatch(
        source="provider",
        identity=_provider_identity(row.provider_match_id),
        display_name=_provider_display(row),
        reason=reason,
    )


def _unavailable_game(
    game: object,
    reason: str,
) -> ShadowUnavailableMatch:
    if type(game) is KalshiShadowGame:
        identity = game.event_ticker if _safe_ticker(game.event_ticker) else "kalshi_ticker_invalid"
        title = game.game_title if _safe_title(game.game_title) else "kalshi_title_invalid"
    else:
        identity = "kalshi_game_invalid"
        title = "kalshi_game_invalid"
    return ShadowUnavailableMatch(
        source="kalshi",
        identity=identity,
        display_name=title,
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class _ProviderCandidate:
    row: SportradarScoreSnapshot
    identity: str
    pair: frozenset[str]


@dataclass(frozen=True, slots=True)
class _GameCandidate:
    game: KalshiShadowGame
    pair: frozenset[str]
    ticker_by_player: tuple[tuple[str, str], tuple[str, str]]

    def ticker_for(self, player: str) -> str:
        for known_player, ticker in self.ticker_by_player:
            if known_player == player:
                return ticker
        raise ValueError("shadow_bijection_invalid")


def _provider_candidate(row: object) -> _ProviderCandidate | None:
    if type(row) is not SportradarScoreSnapshot:
        return None
    if not _positive_wall_ns(row.start_wall_ns):
        return None
    if type(row.status) is not str:
        return None
    identity = _provider_identity(row.provider_match_id)
    if identity == "provider_identity_invalid":
        return None
    try:
        home = normalize_player_name(row.home_name)
        away = normalize_player_name(row.away_name)
    except ValueError:
        return None
    if home == away:
        return None
    return _ProviderCandidate(row=row, identity=identity, pair=frozenset((home, away)))


def _game_candidate(game: object) -> _GameCandidate | None:
    if type(game) is not KalshiShadowGame:
        return None
    if (
        not _safe_ticker(game.event_ticker)
        or not _positive_wall_ns(game.scheduled_start_wall_ns)
        or not _safe_title(game.game_title)
        or type(game.markets) is not tuple
        or len(game.markets) != 2
    ):
        return None
    first, second = game.markets
    if type(first) is not KalshiShadowMarket or type(second) is not KalshiShadowMarket:
        return None
    if not _safe_ticker(first.ticker) or not _safe_ticker(second.ticker):
        return None
    if first.ticker == second.ticker:
        return None
    try:
        first_name = normalize_player_name(first.yes_player_name)
        second_name = normalize_player_name(second.yes_player_name)
    except ValueError:
        return None
    if first_name == second_name:
        return None
    return _GameCandidate(
        game=game,
        pair=frozenset((first_name, second_name)),
        ticker_by_player=((first_name, first.ticker), (second_name, second.ticker)),
    )


def _projection(
    ready: tuple[ShadowMatchChoice, ...],
    unavailable: tuple[ShadowUnavailableMatch, ...],
    provider_payload_sha256: str,
    kalshi_catalog_sha256: str,
) -> bytes:
    return json.dumps(
        {
            "kalshi_catalog_sha256": kalshi_catalog_sha256,
            "provider_payload_sha256": provider_payload_sha256,
            "ready": [
                {
                    "away_player_name": row.away_player_name,
                    "event_ticker": row.event_ticker,
                    "home_player_name": row.home_player_name,
                    "market_tickers": list(row.market_tickers),
                    "provider_match_id": row.provider_match_id,
                    "provider_start_wall_ns": row.provider_start_wall_ns,
                }
                for row in ready
            ],
            "unavailable": [
                {
                    "display_name": row.display_name,
                    "identity": row.identity,
                    "reason": row.reason,
                    "source": row.source,
                }
                for row in unavailable
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def resolve_shadow_matches(
    provider: SportradarLiveSummariesSnapshot,
    kalshi_games: tuple[KalshiShadowGame, ...],
    *,
    kalshi_catalog_sha256: str,
) -> ShadowChooserSnapshot:
    """Resolve only exact, unique live provider-to-Kalshi identities."""
    if (
        type(provider) is not SportradarLiveSummariesSnapshot
        or type(provider.snapshots) is not tuple
        or type(kalshi_games) is not tuple
        or not _valid_digest(provider.payload_sha256)
        or not _valid_digest(kalshi_catalog_sha256)
    ):
        raise ValueError("shadow_resolver_input_invalid")

    provider_entries: list[tuple[SportradarScoreSnapshot, _ProviderCandidate | None]] = [
        (row, _provider_candidate(row)) for row in provider.snapshots
    ]
    game_entries: list[tuple[object, _GameCandidate | None]] = [
        (game, _game_candidate(game)) for game in kalshi_games
    ]

    provider_reasons: dict[int, str] = {}
    game_reasons: dict[int, str] = {}
    provider_ids: dict[str, list[int]] = {}
    provider_pairs: dict[frozenset[str], list[int]] = {}
    game_ids: dict[str, list[int]] = {}
    game_pairs: dict[frozenset[str], list[int]] = {}

    for index, (_, candidate) in enumerate(provider_entries):
        if candidate is None:
            provider_reasons[index] = "provider_invalid"
        elif candidate.row.status != "live":
            provider_reasons[index] = "provider_not_live"
        else:
            provider_ids.setdefault(candidate.identity, []).append(index)
            provider_pairs.setdefault(candidate.pair, []).append(index)
    for index, (_, candidate) in enumerate(game_entries):
        if candidate is None:
            game_reasons[index] = "kalshi_invalid"
        else:
            game_ids.setdefault(candidate.game.event_ticker, []).append(index)
            game_pairs.setdefault(candidate.pair, []).append(index)

    for indexes in provider_ids.values():
        if len(indexes) > 1:
            for index in indexes:
                provider_reasons[index] = "provider_duplicate_id"
    for indexes in provider_pairs.values():
        if len(indexes) > 1:
            for index in indexes:
                provider_reasons.setdefault(index, "provider_duplicate_pair")
    for indexes in game_ids.values():
        if len(indexes) > 1:
            for index in indexes:
                game_reasons[index] = "kalshi_duplicate_ticker"
    for indexes in game_pairs.values():
        if len(indexes) > 1:
            for index in indexes:
                game_reasons.setdefault(index, "kalshi_duplicate_pair")

    provider_edges: dict[int, list[int]] = {}
    game_edges: dict[int, list[int]] = {}
    for provider_index, (_, provider_candidate) in enumerate(provider_entries):
        if provider_candidate is None or provider_candidate.row.status != "live":
            continue
        for game_index, (_, game_candidate) in enumerate(game_entries):
            if game_candidate is None or provider_candidate.pair != game_candidate.pair:
                continue
            if abs(
                provider_candidate.row.start_wall_ns
                - game_candidate.game.scheduled_start_wall_ns
            ) <= 900_000_000_000:
                provider_edges.setdefault(provider_index, []).append(game_index)
                game_edges.setdefault(game_index, []).append(provider_index)

    ready_rows: list[ShadowMatchChoice] = []
    for provider_index, links in provider_edges.items():
        if len(links) != 1 or provider_index in provider_reasons:
            continue
        game_index = links[0]
        if len(game_edges[game_index]) != 1 or game_index in game_reasons:
            continue
        provider_candidate = provider_entries[provider_index][1]
        game_candidate = game_entries[game_index][1]
        if provider_candidate is None or game_candidate is None:
            continue
        home = normalize_player_name(provider_candidate.row.home_name)
        away = normalize_player_name(provider_candidate.row.away_name)
        ready_rows.append(
            ShadowMatchChoice(
                provider_match_id=provider_candidate.identity,
                provider_start_wall_ns=provider_candidate.row.start_wall_ns,
                event_ticker=game_candidate.game.event_ticker,
                home_player_name=provider_candidate.row.home_name,
                away_player_name=provider_candidate.row.away_name,
                market_tickers=(
                    game_candidate.ticker_for(home),
                    game_candidate.ticker_for(away),
                ),
            )
        )

    selected_provider_ids = {row.provider_match_id for row in ready_rows}
    selected_game_ids = {row.event_ticker for row in ready_rows}
    unavailable_rows: list[ShadowUnavailableMatch] = []
    for index, (row, _) in enumerate(provider_entries):
        if _provider_identity(row.provider_match_id) in selected_provider_ids:
            continue
        reason = provider_reasons.get(index)
        if reason is None:
            reason = "provider_ambiguous" if len(provider_edges.get(index, ())) > 1 else "provider_unmatched"
        unavailable_rows.append(_unavailable_provider(row, reason))
    for index, (game, _) in enumerate(game_entries):
        identity = game.event_ticker if type(game) is KalshiShadowGame else "kalshi_game_invalid"
        if identity in selected_game_ids:
            continue
        reason = game_reasons.get(index)
        if reason is None:
            reason = "kalshi_ambiguous" if len(game_edges.get(index, ())) > 1 else "kalshi_unmatched"
        unavailable_rows.append(_unavailable_game(game, reason))

    ready = tuple(
        sorted(
            ready_rows,
            key=lambda row: (
                row.provider_start_wall_ns,
                row.provider_match_id,
                row.event_ticker,
            ),
        )
    )
    unavailable = tuple(
        sorted(
            unavailable_rows,
            key=lambda row: (row.source, row.identity, row.display_name, row.reason),
        )
    )
    resolver_snapshot_sha256 = sha256(
        _projection(
            ready,
            unavailable,
            provider.payload_sha256,
            kalshi_catalog_sha256,
        )
    ).hexdigest()
    return ShadowChooserSnapshot(
        ready=ready,
        unavailable=unavailable,
        provider_payload_sha256=provider.payload_sha256,
        kalshi_catalog_sha256=kalshi_catalog_sha256,
        resolver_snapshot_sha256=resolver_snapshot_sha256,
    )


__all__ = [
    "KalshiShadowGame",
    "KalshiShadowMarket",
    "ShadowChooserSnapshot",
    "ShadowMatchChoice",
    "ShadowUnavailableMatch",
    "normalize_player_name",
    "resolve_shadow_matches",
]
