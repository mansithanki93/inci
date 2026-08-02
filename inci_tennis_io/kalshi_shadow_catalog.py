"""Narrow GET-only Kalshi metadata transport for shadow match discovery."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
from re import compile as pattern_compile
import time
from unicodedata import category

import requests

import schemas
from inci_tennis_adapters.shadow_match_chooser import (
    KalshiShadowGame,
    KalshiShadowMarket,
    normalize_player_name,
)
from sports_discovery import SelectedContract, discover_game_inventory


_ORIGIN = "https://external-api.kalshi.com"
_API_PREFIX = "/trade-api/v2"
_MAXIMUM_BODY_BYTES = 8_388_608
_MAXIMUM_PAGES = 20
_MAXIMUM_SIGNED_64 = 9_223_372_036_854_775_807
_PUBLIC_METADATA_MIN_INTERVAL_SECONDS = 0.05
_GET_429_DELAYS = (0.25, 0.5, 1.0, 2.0)
_TICKER = pattern_compile(r"[A-Z0-9][A-Z0-9._-]{0,127}\Z")
_CURSOR = pattern_compile(r"[A-Za-z0-9._~+/=-]{1,2048}\Z")


class KalshiShadowCatalogError(ValueError):
    """Sanitized fail-closed catalog boundary error."""


def _fail(code: str) -> None:
    raise KalshiShadowCatalogError(code)


@dataclass(frozen=True, slots=True)
class _CatalogConfig:
    sports: tuple[str, ...] = ("Tennis",)
    tickers: tuple[str, ...] = ()
    max_spread: int = 100
    contracts_per_trade: int = 1
    max_monitored_markets: int = _MAXIMUM_SIGNED_64


def _duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("kalshi_catalog_json_invalid")
        result[key] = value
    return result


def _seconds_to_wall_ns(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("kalshi_catalog_timestamp_invalid")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("kalshi_catalog_timestamp_invalid")
    try:
        seconds = Decimal(str(value))
        nanoseconds = seconds * Decimal(1_000_000_000)
    except (InvalidOperation, ValueError):
        raise ValueError("kalshi_catalog_timestamp_invalid") from None
    if (
        not seconds.is_finite()
        or seconds <= 0
        or nanoseconds != nanoseconds.to_integral_value()
    ):
        raise ValueError("kalshi_catalog_timestamp_invalid")
    result = int(nanoseconds)
    if result > _MAXIMUM_SIGNED_64:
        raise ValueError("kalshi_catalog_timestamp_invalid")
    return result


def _safe_ticker(value: object) -> bool:
    return type(value) is str and _TICKER.fullmatch(value) is not None


def _safe_title(value: object) -> bool:
    if type(value) is not str or not value.strip():
        return False
    if any(category(character).startswith("C") for character in value):
        return False
    try:
        return len(value.encode("utf-8")) <= 512
    except UnicodeEncodeError:
        return False


def _catalog_digest(games: tuple[KalshiShadowGame, ...]) -> str:
    projection = [
        {
            "event_ticker": game.event_ticker,
            "game_title": game.game_title,
            "markets": [
                {
                    "ticker": market.ticker,
                    "yes_player_name": market.yes_player_name,
                }
                for market in game.markets
            ],
            "scheduled_start_wall_ns": game.scheduled_start_wall_ns,
        }
        for game in games
    ]
    payload = json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


class KalshiShadowCatalogTransport:
    """Public Sports metadata client with no account or mutation authority."""

    __slots__ = (
        "_event_sibling_counts",
        "_game_milestone_ids",
        "_last_public_metadata_at",
        "_monotonic",
        "_session",
        "_sleep",
    )

    def __init__(self) -> None:
        try:
            session = requests.Session()
            session.trust_env = False
        except Exception:
            _fail("kalshi_catalog_transport_invalid")
        self._session = session
        self._sleep = time.sleep
        self._monotonic = time.monotonic
        self._last_public_metadata_at: float | None = None
        self._event_sibling_counts: dict[str, int] = {}
        self._game_milestone_ids: set[str] = set()

    def _pace(self) -> None:
        if self._last_public_metadata_at is None:
            return
        try:
            remaining = _PUBLIC_METADATA_MIN_INTERVAL_SECONDS - (
                self._monotonic() - self._last_public_metadata_at
            )
            if remaining > 0:
                self._sleep(remaining)
        except Exception:
            _fail("kalshi_catalog_transport_invalid")

    def _read(self, path: str, params: dict[str, object]) -> dict[str, object]:
        self._pace()
        response = None
        for attempt in range(len(_GET_429_DELAYS) + 1):
            try:
                response = self._session.request(
                    "GET",
                    _ORIGIN + path,
                    params=dict(params),
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                    allow_redirects=False,
                    timeout=(3, 10),
                )
            except Exception:
                _fail("kalshi_catalog_transport_invalid")
            if getattr(response, "status_code", None) == 429:
                if attempt == len(_GET_429_DELAYS):
                    _fail("kalshi_catalog_rate_limited")
                try:
                    self._sleep(_GET_429_DELAYS[attempt])
                except Exception:
                    _fail("kalshi_catalog_transport_invalid")
                continue
            break
        self._last_public_metadata_at = self._monotonic()
        status = getattr(response, "status_code", None)
        if isinstance(status, bool) or not isinstance(status, int):
            _fail("kalshi_catalog_response_invalid")
        if 300 <= status < 400:
            _fail("kalshi_catalog_redirect_invalid")
        if status != 200:
            _fail("kalshi_catalog_status_invalid")
        try:
            headers = response.headers
            content_type = headers.get("Content-Type")
            content_encoding = headers.get("Content-Encoding")
            content_length = headers.get("Content-Length")
        except Exception:
            _fail("kalshi_catalog_headers_invalid")
        if type(content_type) is not str or content_type.split(";", 1)[0].strip().casefold() != "application/json":
            _fail("kalshi_catalog_content_type_invalid")
        if content_encoding is not None and (
            type(content_encoding) is not str
            or content_encoding.strip().casefold() != "identity"
        ):
            _fail("kalshi_catalog_content_encoding_invalid")
        try:
            body = response.content
        except Exception:
            _fail("kalshi_catalog_body_invalid")
        if type(body) is not bytes or not body:
            _fail("kalshi_catalog_body_invalid")
        if len(body) > _MAXIMUM_BODY_BYTES:
            _fail("kalshi_catalog_body_too_large")
        if content_length is not None:
            if (
                type(content_length) is not str
                or not content_length.isascii()
                or not content_length.isdigit()
            ):
                _fail("kalshi_catalog_headers_invalid")
            if int(content_length) != len(body):
                _fail("kalshi_catalog_body_invalid")
        try:
            value = json.loads(body, object_pairs_hook=_duplicate_keys)
        except KalshiShadowCatalogError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            _fail("kalshi_catalog_json_invalid")
        if type(value) is not dict:
            _fail("kalshi_catalog_json_invalid")
        return value

    @staticmethod
    def _rfc3339_utc(value: object) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            _fail("kalshi_catalog_timestamp_invalid")
        return value.astimezone(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")

    def get_sports_filters(self) -> dict[str, object]:
        try:
            return schemas.parse_sports_filters_response(
                self._read(_API_PREFIX + "/search/filters_by_sport", {})
            )
        except KalshiShadowCatalogError:
            raise
        except Exception:
            _fail("kalshi_catalog_schema_invalid")

    def get_sports_series(self) -> tuple[dict[str, object], ...]:
        try:
            return schemas.parse_series_list_response(
                self._read(_API_PREFIX + "/series", {"category": "Sports"})
            )
        except KalshiShadowCatalogError:
            raise
        except Exception:
            _fail("kalshi_catalog_schema_invalid")

    def get_sports_milestones(
        self,
        *,
        competition: str,
        minimum_start_date: datetime,
    ) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
        if type(competition) is not str or not competition:
            _fail("kalshi_catalog_query_invalid")
        query: dict[str, object] = {
            "category": "Sports",
            "minimum_start_date": self._rfc3339_utc(minimum_start_date),
            "competition": competition,
            "limit": 500,
        }
        rows, metadata = self._pages(
            _API_PREFIX + "/milestones",
            query,
            schemas.parse_milestones_page,
        )
        self._game_milestone_ids.update(
            row["milestone_id"] for row in rows if row["type"] == "game"
        )
        return rows, metadata

    def get_open_events(
        self, *, series_ticker: str
    ) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
        if type(series_ticker) is not str or not series_ticker:
            _fail("kalshi_catalog_query_invalid")
        rows, metadata = self._pages(
            _API_PREFIX + "/events",
            {
                "series_ticker": series_ticker,
                "status": "open",
                "with_nested_markets": "true",
                "limit": 200,
            },
            schemas.parse_events_page,
        )
        skips = Counter()
        for row in rows:
            event_ticker = row["event_ticker"]
            sibling_count = len(row["markets"]) + sum(
                row.get("market_skips", {}).values()
            )
            previous_count = self._event_sibling_counts.get(event_ticker)
            if previous_count is not None and previous_count != sibling_count:
                _fail("kalshi_catalog_schema_invalid")
            self._event_sibling_counts[event_ticker] = sibling_count
            for product, count in row.get("market_skips", {}).items():
                skips[product] += count
        metadata["market_skips"] = dict(sorted(skips.items()))
        return rows, metadata

    def _pages(
        self,
        path: str,
        params: dict[str, object],
        parser: object,
    ) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
        query = dict(params)
        rows: list[dict[str, object]] = []
        cursors: set[str] = set()
        for page in range(1, _MAXIMUM_PAGES + 1):
            try:
                page_rows, cursor = parser(self._read(path, query))  # type: ignore[operator]
            except KalshiShadowCatalogError:
                raise
            except Exception:
                _fail("kalshi_catalog_schema_invalid")
            rows.extend(page_rows)
            if cursor == "":
                return tuple(rows), {
                    "pages": page,
                    "rows": len(rows),
                    "raw_rows": len(rows),
                    "market_skips": {},
                }
            if _CURSOR.fullmatch(cursor) is None or cursor in cursors:
                _fail("kalshi_catalog_pagination_invalid")
            cursors.add(cursor)
            query["cursor"] = cursor
        _fail("kalshi_catalog_pagination_invalid")

    def discover_tennis_games(
        self, *, now: datetime | None = None
    ) -> tuple[tuple[KalshiShadowGame, ...], str]:
        try:
            self._event_sibling_counts = {}
            self._game_milestone_ids = set()
            inventory = discover_game_inventory(_CatalogConfig(), self, now=now)
            grouped: dict[str, list[SelectedContract]] = {}
            for contract in inventory.contracts:
                grouped.setdefault(contract.provenance.event_ticker, []).append(contract)
            games: list[KalshiShadowGame] = []
            for event_ticker, contracts in grouped.items():
                if (
                    len(contracts) != 2
                    or self._event_sibling_counts.get(event_ticker) != 2
                    or any(
                        contract.provenance.milestone_id
                        not in self._game_milestone_ids
                        for contract in contracts
                    )
                    or not _safe_ticker(event_ticker)
                ):
                    continue
                first, second = sorted(contracts, key=lambda row: row.ticker)
                if (
                    first.provenance != second.provenance
                    or first.game_title != second.game_title
                    or not _safe_title(first.game_title)
                    or not _safe_ticker(first.ticker)
                    or not _safe_ticker(second.ticker)
                    or first.ticker == second.ticker
                    or type(first.yes_player_name) is not str
                    or type(second.yes_player_name) is not str
                ):
                    continue
                try:
                    first_name = normalize_player_name(first.yes_player_name)
                    second_name = normalize_player_name(second.yes_player_name)
                except ValueError:
                    continue
                try:
                    start_ns = _seconds_to_wall_ns(
                        first.provenance.scheduled_start_ts
                    )
                except ValueError:
                    _fail("kalshi_catalog_timestamp_invalid")
                if first_name == second_name:
                    continue
                games.append(
                    KalshiShadowGame(
                        event_ticker=event_ticker,
                        scheduled_start_wall_ns=start_ns,
                        game_title=first.game_title,
                        markets=(
                            KalshiShadowMarket(first.ticker, first.yes_player_name),
                            KalshiShadowMarket(second.ticker, second.yes_player_name),
                        ),
                    )
                )
            result = tuple(
                sorted(
                    games,
                    key=lambda game: (
                        game.scheduled_start_wall_ns,
                        game.event_ticker,
                    ),
                )
            )
            return result, _catalog_digest(result)
        except KalshiShadowCatalogError:
            raise
        except Exception:
            _fail("kalshi_catalog_schema_invalid")


__all__ = ["KalshiShadowCatalogError", "KalshiShadowCatalogTransport"]
