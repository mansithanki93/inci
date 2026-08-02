"""Narrow GET-only Kalshi metadata transport for shadow match discovery."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, time as day_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
from re import compile as pattern_compile
import time
from unicodedata import category

import requests

from inci_tennis_adapters.shadow_match_chooser import (
    KalshiShadowGame,
    KalshiShadowMarket,
    normalize_player_name,
)


_ORIGIN = "https://external-api.kalshi.com"
_API_PREFIX = "/trade-api/v2"
_MAXIMUM_BODY_BYTES = 8_388_608
_MAXIMUM_PAGES = 20
_MAXIMUM_SIGNED_64 = 9_223_372_036_854_775_807
_PUBLIC_METADATA_MIN_INTERVAL_SECONDS = 0.05
_GET_429_DELAYS = (0.25, 0.5, 1.0, 2.0)
_TICKER = pattern_compile(r"[A-Z0-9][A-Z0-9._-]{0,127}\Z")
_CURSOR = pattern_compile(r"[A-Za-z0-9._~+/=-]{1,2048}\Z")
_FIXED_POINT = pattern_compile(r"-?\d+(?:\.\d+)?\Z")
_QUANTITY = pattern_compile(r"\d+\.\d{2}\Z")
_MARKET_STATUSES = frozenset(
    {
        "initialized",
        "inactive",
        "active",
        "closed",
        "determined",
        "disputed",
        "amended",
        "finalized",
    }
)


class KalshiShadowCatalogError(ValueError):
    """Sanitized fail-closed catalog boundary error."""


def _fail(code: str) -> None:
    raise KalshiShadowCatalogError(code)


def _required(value: object, field: str) -> object:
    if type(value) is not dict or field not in value or value[field] is None:
        raise ValueError("kalshi_catalog_schema_invalid")
    return value[field]


def _text(value: object, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value):
        raise ValueError("kalshi_catalog_schema_invalid")
    return value


def _string_tuple(
    value: object, *, require_nonempty: bool = False
) -> tuple[str, ...]:
    if type(value) is not list or (require_nonempty and not value):
        raise ValueError("kalshi_catalog_schema_invalid")
    result = tuple(_text(item) for item in value)
    if len(set(result)) != len(result):
        raise ValueError("kalshi_catalog_schema_invalid")
    return result


def _decimal(value: object, *, quantity: bool = False) -> Decimal:
    expression = _QUANTITY if quantity else _FIXED_POINT
    if type(value) is not str or expression.fullmatch(value) is None:
        raise ValueError("kalshi_catalog_schema_invalid")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError("kalshi_catalog_schema_invalid") from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("kalshi_catalog_schema_invalid")
    return parsed


def _dollars_to_cents(value: object) -> Decimal:
    parsed = _decimal(value)
    if max(0, -parsed.as_tuple().exponent) > 4:
        raise ValueError("kalshi_catalog_schema_invalid")
    cents = parsed * Decimal(100)
    if cents > Decimal(100):
        raise ValueError("kalshi_catalog_schema_invalid")
    return cents


def _timestamp(value: object) -> float:
    text = _text(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("kalshi_catalog_schema_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("kalshi_catalog_schema_invalid")
    result = parsed.astimezone(timezone.utc).timestamp()
    if not math.isfinite(result) or result < 0:
        raise ValueError("kalshi_catalog_schema_invalid")
    return result


def _parse_filters(value: object) -> dict[str, object]:
    raw_sports = _required(value, "filters_by_sports")
    ordering = _string_tuple(
        _required(value, "sport_ordering"), require_nonempty=True
    )
    if type(raw_sports) is not dict:
        raise ValueError("kalshi_catalog_schema_invalid")
    names = tuple(_text(name) for name in raw_sports)
    if (
        set(names) != set(ordering)
        or len({name.casefold() for name in names}) != len(names)
    ):
        raise ValueError("kalshi_catalog_schema_invalid")
    sports: dict[str, object] = {}
    for sport in ordering:
        details = raw_sports[sport]
        scopes = frozenset(_string_tuple(_required(details, "scopes")))
        raw_competitions = _required(details, "competitions")
        if type(raw_competitions) is not dict:
            raise ValueError("kalshi_catalog_schema_invalid")
        competitions: dict[str, frozenset[str]] = {}
        for name, competition in raw_competitions.items():
            canonical_name = _text(name)
            competitions[canonical_name] = frozenset(
                _string_tuple(_required(competition, "scopes"))
            )
        sports[sport] = {
            "scopes": scopes,
            "competitions": competitions,
        }
    return {"sport_ordering": ordering, "sports": sports}


def _parse_series(value: object) -> tuple[dict[str, object], ...]:
    rows = _required(value, "series")
    if type(rows) is not list:
        raise ValueError("kalshi_catalog_schema_invalid")
    if "cursor" in value:
        cursor = value["cursor"]
        if type(cursor) is not str or cursor:
            raise ValueError("kalshi_catalog_schema_invalid")
    result: list[dict[str, object]] = []
    tickers: set[str] = set()
    for row in rows:
        ticker = _text(_required(row, "ticker"))
        if ticker in tickers:
            raise ValueError("kalshi_catalog_schema_invalid")
        tickers.add(ticker)
        raw_tags = row.get("tags")
        tags = () if raw_tags is None else _string_tuple(raw_tags)
        result.append(
            {
                "series_ticker": ticker,
                "category": _text(_required(row, "category")),
                "tags": tags,
            }
        )
    return tuple(result)


def _parse_milestone(row: object) -> dict[str, object]:
    details = _required(row, "details")
    if type(details) is not dict:
        raise ValueError("kalshi_catalog_schema_invalid")

    def optional_detail(field: str) -> str | None:
        raw = details.get(field)
        return None if raw is None else _text(raw)

    return {
        "milestone_id": _text(_required(row, "id")),
        "category": _text(_required(row, "category")),
        "type": _text(_required(row, "type")),
        "start_ts": _timestamp(_required(row, "start_date")),
        "title": _text(_required(row, "title")),
        "league": optional_detail("league"),
        "main_game_event_ticker": optional_detail("main_game_event_ticker"),
        "primary_event_tickers": _string_tuple(
            _required(row, "primary_event_tickers")
        ),
        "related_event_tickers": _string_tuple(
            _required(row, "related_event_tickers")
        ),
    }


def _parse_market(row: object) -> tuple[dict[str, object] | None, str | None]:
    ticker = _text(_required(row, "ticker"))
    event_ticker = _text(_required(row, "event_ticker"))
    market_type = _text(_required(row, "market_type"))
    if market_type not in {"binary", "scalar"}:
        raise ValueError("kalshi_catalog_schema_invalid")
    if market_type == "scalar":
        return None, market_type
    raw_collection = row.get("mve_collection_ticker")
    if raw_collection is not None and type(raw_collection) is not str:
        raise ValueError("kalshi_catalog_schema_invalid")
    if (
        raw_collection
        or ticker.startswith("KXMVE")
        or event_ticker.startswith("KXMVE")
    ):
        return None, "mve"
    yes_name = _text(_required(row, "yes_sub_title"), allow_empty=True)
    no_name = _text(_required(row, "no_sub_title"), allow_empty=True)
    raw_title = row.get("title")
    title = (
        yes_name
        if raw_title is None
        else _text(raw_title, allow_empty=True)
    )
    status = _text(_required(row, "status"))
    if status not in _MARKET_STATUSES:
        raise ValueError("kalshi_catalog_schema_invalid")
    close_ts = _timestamp(_required(row, "close_time"))
    can_close_early = _required(row, "can_close_early")
    if type(can_close_early) is not bool:
        raise ValueError("kalshi_catalog_schema_invalid")
    notional = _decimal(_required(row, "notional_value_dollars"))
    if notional != Decimal(1):
        raise ValueError("kalshi_catalog_schema_invalid")
    bid = _dollars_to_cents(_required(row, "yes_bid_dollars"))
    ask = _dollars_to_cents(_required(row, "yes_ask_dollars"))
    bid_size = _decimal(_required(row, "yes_bid_size_fp"), quantity=True)
    ask_size = _decimal(_required(row, "yes_ask_size_fp"), quantity=True)
    executable_bid = None if bid_size == 0 else bid
    executable_ask = None if ask_size == 0 else ask
    if (
        executable_bid is not None
        and executable_ask is not None
        and executable_bid > executable_ask
    ):
        raise ValueError("kalshi_catalog_schema_invalid")
    return (
        {
            "ticker": ticker,
            "event_ticker": event_ticker,
            "title": title,
            "market_type": market_type,
            "yes_sub_title": yes_name,
            "no_sub_title": no_name,
            "notional_value": notional,
            "close_ts": close_ts,
            "can_close_early": can_close_early,
            "yes_bid": executable_bid,
            "yes_ask": executable_ask,
            "yes_bid_size": bid_size,
            "yes_ask_size": ask_size,
            "status": status,
        },
        None,
    )


def _parse_event(row: object) -> dict[str, object]:
    event_ticker = _text(_required(row, "event_ticker"))
    raw_markets = _required(row, "markets")
    if type(raw_markets) is not list:
        raise ValueError("kalshi_catalog_schema_invalid")
    markets: list[dict[str, object]] = []
    skips: Counter[str] = Counter()
    for raw_market in raw_markets:
        market, skip_type = _parse_market(raw_market)
        if market is None:
            raw_event_ticker = _text(
                _required(raw_market, "event_ticker")
            )
            if raw_event_ticker != event_ticker or skip_type is None:
                raise ValueError("kalshi_catalog_schema_invalid")
            skips[skip_type] += 1
            continue
        if market["event_ticker"] != event_ticker:
            raise ValueError("kalshi_catalog_schema_invalid")
        markets.append(market)
    return {
        "event_ticker": event_ticker,
        "series_ticker": _text(_required(row, "series_ticker")),
        "category": _text(_required(row, "category")),
        "title": _text(_required(row, "title")),
        "markets": tuple(markets),
        "market_skips": dict(sorted(skips.items())),
    }


def _parse_page(
    value: object, key: str, row_parser: object
) -> tuple[tuple[dict[str, object], ...], str]:
    rows = _required(value, key)
    cursor = _required(value, "cursor")
    if type(rows) is not list or type(cursor) is not str:
        raise ValueError("kalshi_catalog_schema_invalid")
    return tuple(row_parser(row) for row in rows), cursor  # type: ignore[operator]


def _parse_milestones_page(
    value: object,
) -> tuple[tuple[dict[str, object], ...], str]:
    return _parse_page(value, "milestones", _parse_milestone)


def _parse_events_page(
    value: object,
) -> tuple[tuple[dict[str, object], ...], str]:
    return _parse_page(value, "events", _parse_event)


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


def _local_day_bounds(now: datetime | None) -> tuple[float, float]:
    if now is None:
        local_date = datetime.now().date()
        start = datetime.combine(local_date, day_time.min).astimezone()
        end = datetime.combine(
            local_date + timedelta(days=1), day_time.min
        ).astimezone()
    else:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("kalshi_catalog_timestamp_invalid")
        start = datetime.combine(now.date(), day_time.min, tzinfo=now.tzinfo)
        end = datetime.combine(
            now.date() + timedelta(days=1),
            day_time.min,
            tzinfo=now.tzinfo,
        )
    return (
        start.astimezone(timezone.utc).timestamp(),
        end.astimezone(timezone.utc).timestamp(),
    )


def _tennis_scope(filters: dict[str, object]) -> tuple[str, tuple[str, ...]]:
    ordering = filters["sport_ordering"]
    sports = filters["sports"]
    if type(ordering) is not tuple or type(sports) is not dict:
        raise ValueError("kalshi_catalog_schema_invalid")
    by_name = {name.casefold(): name for name in ordering}
    tennis = by_name.get("tennis")
    if tennis is None:
        raise ValueError("kalshi_catalog_schema_invalid")
    details = sports[tennis]
    if type(details) is not dict or "Games" not in details["scopes"]:
        raise ValueError("kalshi_catalog_schema_invalid")
    competitions = details["competitions"]
    if type(competitions) is not dict:
        raise ValueError("kalshi_catalog_schema_invalid")
    names = tuple(
        sorted(
            name
            for name, scopes in competitions.items()
            if "Games" in scopes
        )
    )
    if not names:
        raise ValueError("kalshi_catalog_schema_invalid")
    return tennis, names


def _series_maps(
    rows: tuple[dict[str, object], ...],
    filters: dict[str, object],
) -> tuple[tuple[str, ...], dict[str, str]]:
    ordering = filters["sport_ordering"]
    if type(ordering) is not tuple:
        raise ValueError("kalshi_catalog_schema_invalid")
    canonical_sports = frozenset(
        name for name in ordering if name != "All sports"
    )
    official: list[str] = []
    sport_by_series: dict[str, str] = {}
    seen: set[str] = set()
    for row in rows:
        if row["category"] != "Sports":
            continue
        ticker = row["series_ticker"]
        tags = row["tags"]
        if type(ticker) is not str or type(tags) is not tuple or ticker in seen:
            raise ValueError("kalshi_catalog_schema_invalid")
        seen.add(ticker)
        official.append(ticker)
        matches = tuple(tag for tag in tags if tag in canonical_sports)
        if len(matches) == 1:
            sport_by_series[ticker] = matches[0]
    return tuple(official), sport_by_series


def _resolve_series(event_ticker: str, official: tuple[str, ...]) -> str | None:
    matches = tuple(
        ticker
        for ticker in official
        if event_ticker.startswith(ticker + "-")
    )
    if not matches:
        return None
    longest = max(len(ticker) for ticker in matches)
    winners = tuple(ticker for ticker in matches if len(ticker) == longest)
    if len(winners) != 1:
        raise ValueError("kalshi_catalog_schema_invalid")
    return winners[0]


def _eligible_markets(event: dict[str, object]) -> tuple[dict[str, object], ...]:
    raw_markets = event["markets"]
    if type(raw_markets) is not tuple:
        raise ValueError("kalshi_catalog_schema_invalid")
    result: list[dict[str, object]] = []
    for market in raw_markets:
        if market["event_ticker"] != event["event_ticker"]:
            raise ValueError("kalshi_catalog_schema_invalid")
        if market["status"] != "active":
            continue
        bid = market["yes_bid"]
        ask = market["yes_ask"]
        bid_size = market["yes_bid_size"]
        ask_size = market["yes_ask_size"]
        if bid is None or ask is None:
            continue
        if (
            type(bid) is not Decimal
            or type(ask) is not Decimal
            or type(bid_size) is not Decimal
            or type(ask_size) is not Decimal
            or bid_size <= 0
            or ask_size <= 0
            or ask - bid > Decimal(100)
        ):
            raise ValueError("kalshi_catalog_schema_invalid")
        result.append(market)
    return tuple(result)


class KalshiShadowCatalogTransport:
    """Public Sports metadata client with no account or mutation authority."""

    __slots__ = (
        "_event_sibling_counts",
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
            return _parse_filters(
                self._read(_API_PREFIX + "/search/filters_by_sport", {})
            )
        except KalshiShadowCatalogError:
            raise
        except Exception:
            _fail("kalshi_catalog_schema_invalid")

    def get_sports_series(self) -> tuple[dict[str, object], ...]:
        try:
            return _parse_series(
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
            _parse_milestones_page,
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
            _parse_events_page,
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
            filters = self.get_sports_filters()
            tennis, competitions = _tennis_scope(filters)
            day_start, day_end = _local_day_bounds(now)
            minimum_start_date = datetime.fromtimestamp(
                day_start, tz=timezone.utc
            )
            official, sport_by_series = _series_maps(
                self.get_sports_series(), filters
            )

            seen_milestones: dict[str, dict[str, object]] = {}
            game_milestone_ids: set[str] = set()
            expected_events: dict[str, dict[str, object]] = {}
            for competition in competitions:
                rows, _ = self.get_sports_milestones(
                    competition=competition,
                    minimum_start_date=minimum_start_date,
                )
                for milestone in rows:
                    milestone_id = milestone["milestone_id"]
                    if type(milestone_id) is not str:
                        raise ValueError("kalshi_catalog_schema_invalid")
                    previous_milestone = seen_milestones.get(milestone_id)
                    if (
                        previous_milestone is not None
                        and previous_milestone != milestone
                    ):
                        raise ValueError("kalshi_catalog_schema_invalid")
                    seen_milestones[milestone_id] = milestone
                    if milestone["type"] == "game":
                        game_milestone_ids.add(milestone_id)
                    if milestone["category"] != "Sports":
                        continue
                    start_ts = milestone["start_ts"]
                    if (
                        type(start_ts) is not float
                        or not day_start <= start_ts < day_end
                    ):
                        continue
                    event_ticker = milestone["main_game_event_ticker"]
                    if event_ticker is None:
                        primary = milestone["primary_event_tickers"]
                        if type(primary) is not tuple or len(primary) != 1:
                            continue
                        event_ticker = primary[0]
                    if type(event_ticker) is not str:
                        raise ValueError("kalshi_catalog_schema_invalid")
                    series_ticker = _resolve_series(event_ticker, official)
                    if (
                        series_ticker is None
                        or sport_by_series.get(series_ticker) != tennis
                    ):
                        continue
                    expected = {
                        "series_ticker": series_ticker,
                        "milestone_id": milestone_id,
                        "scheduled_start_ts": start_ts,
                    }
                    previous_event = expected_events.get(event_ticker)
                    if previous_event is not None and previous_event != expected:
                        raise ValueError("kalshi_catalog_schema_invalid")
                    expected_events[event_ticker] = expected

            expected_by_series: dict[str, dict[str, dict[str, object]]] = {}
            for event_ticker, expected in expected_events.items():
                series_ticker = expected["series_ticker"]
                if type(series_ticker) is not str:
                    raise ValueError("kalshi_catalog_schema_invalid")
                expected_by_series.setdefault(series_ticker, {})[
                    event_ticker
                ] = expected

            games: list[KalshiShadowGame] = []
            market_tickers: set[str] = set()
            for series_ticker in sorted(expected_by_series):
                rows, _ = self.get_open_events(series_ticker=series_ticker)
                expected = expected_by_series[series_ticker]
                seen_events: dict[str, dict[str, object]] = {}
                for event in rows:
                    event_ticker = event["event_ticker"]
                    if type(event_ticker) is not str or event_ticker not in expected:
                        continue
                    previous_event = seen_events.get(event_ticker)
                    if previous_event is not None:
                        if previous_event != event:
                            raise ValueError("kalshi_catalog_schema_invalid")
                        continue
                    seen_events[event_ticker] = event
                    if event["series_ticker"] != series_ticker:
                        raise ValueError("kalshi_catalog_schema_invalid")
                    if event["category"] != "Sports":
                        continue
                    raw_markets = event["markets"]
                    market_skips = event["market_skips"]
                    if type(raw_markets) is not tuple or type(market_skips) is not dict:
                        raise ValueError("kalshi_catalog_schema_invalid")
                    sibling_count = len(raw_markets) + sum(market_skips.values())
                    markets = _eligible_markets(event)
                    if len(markets) != 2 or sibling_count != 2:
                        continue
                    first, second = sorted(
                        markets, key=lambda market: market["ticker"]
                    )
                    first_ticker = first["ticker"]
                    second_ticker = second["ticker"]
                    game_title = event["title"]
                    first_player = first["yes_sub_title"]
                    second_player = second["yes_sub_title"]
                    if (
                        first_ticker == second_ticker
                        or first_ticker in market_tickers
                        or second_ticker in market_tickers
                    ):
                        raise ValueError("kalshi_catalog_schema_invalid")
                    market_tickers.update((first_ticker, second_ticker))
                    if (
                        expected[event_ticker]["milestone_id"]
                        not in game_milestone_ids
                    ):
                        continue
                    if (
                        not _safe_ticker(event_ticker)
                        or not _safe_title(game_title)
                        or not _safe_ticker(first_ticker)
                        or not _safe_ticker(second_ticker)
                        or type(first_player) is not str
                        or type(second_player) is not str
                    ):
                        continue
                    try:
                        first_name = normalize_player_name(first_player)
                        second_name = normalize_player_name(second_player)
                    except ValueError:
                        continue
                    if first_name == second_name:
                        continue
                    try:
                        start_ns = _seconds_to_wall_ns(
                            expected[event_ticker]["scheduled_start_ts"]
                        )
                    except ValueError:
                        _fail("kalshi_catalog_timestamp_invalid")
                    games.append(
                        KalshiShadowGame(
                            event_ticker=event_ticker,
                            scheduled_start_wall_ns=start_ns,
                            game_title=game_title,
                            markets=(
                                KalshiShadowMarket(first_ticker, first_player),
                                KalshiShadowMarket(second_ticker, second_player),
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
