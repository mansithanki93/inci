"""Pure Sports discovery domain values and metadata helpers.

Network collection deliberately lives above this module.  These helpers only
consume the normalized Task 2 response shapes, so selection and ranking remain
deterministic and independently testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import math
from types import MappingProxyType
from typing import Mapping, NamedTuple


def _identity(value, field):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _finite_decimal(value, field):
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")
    return value


def _timestamp(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite nonnegative timestamp")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite nonnegative timestamp")
    return value


@dataclass(frozen=True)
class ContractProvenance:
    sport: str
    league: str | None
    series_ticker: str
    milestone_id: str
    event_ticker: str
    scheduled_start_ts: float

    def __post_init__(self):
        _identity(self.sport, "sport")
        if self.league is not None:
            _identity(self.league, "league")
        _identity(self.series_ticker, "series_ticker")
        _identity(self.milestone_id, "milestone_id")
        _identity(self.event_ticker, "event_ticker")
        object.__setattr__(self, "scheduled_start_ts", _timestamp(
            self.scheduled_start_ts, "scheduled_start_ts"))


@dataclass(frozen=True)
class SelectedContract:
    ticker: str
    title: str
    game_title: str
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    provenance: ContractProvenance

    def __post_init__(self):
        _identity(self.ticker, "ticker")
        _identity(self.title, "title")
        _identity(self.game_title, "game_title")
        _finite_decimal(self.bid, "bid")
        _finite_decimal(self.ask, "ask")
        _finite_decimal(self.bid_size, "bid_size")
        _finite_decimal(self.ask_size, "ask_size")
        if not isinstance(self.provenance, ContractProvenance):
            raise ValueError("provenance must be ContractProvenance")


class LocalDayWindow(NamedTuple):
    """One local calendar day, represented for durable output and API bounds."""

    local_timezone: str
    session_start_local: str
    session_end_local: str
    session_start_utc: float
    session_end_utc: float


class SeriesIndex(NamedTuple):
    """All official Sports prefixes plus the unambiguous selected-Sport map."""

    official_series_tickers: tuple[str, ...]
    sport_by_series: Mapping[str, str]


@dataclass(frozen=True)
class DiscoveryResult:
    contracts: tuple[SelectedContract, ...]
    selected_sports: tuple[str, ...]
    local_timezone: str
    session_start_local: str
    session_end_local: str
    session_start_utc: float
    session_end_utc: float
    stats: Mapping[str, int]

    def __post_init__(self):
        if not isinstance(self.contracts, tuple):
            raise ValueError("contracts must be a tuple")
        tickers = set()
        for contract in self.contracts:
            if not isinstance(contract, SelectedContract):
                raise ValueError("contracts must contain SelectedContract values")
            if contract.ticker in tickers:
                raise ValueError(f"duplicate contract ticker {contract.ticker!r}")
            tickers.add(contract.ticker)
        if not isinstance(self.selected_sports, tuple):
            raise ValueError("selected_sports must be a tuple")
        sport_names = set()
        for sport in self.selected_sports:
            _identity(sport, "selected_sport")
            if sport in sport_names:
                raise ValueError(f"duplicate selected Sport {sport!r}")
            sport_names.add(sport)
        _identity(self.local_timezone, "local_timezone")
        _identity(self.session_start_local, "session_start_local")
        _identity(self.session_end_local, "session_end_local")
        start = _timestamp(self.session_start_utc, "session_start_utc")
        end = _timestamp(self.session_end_utc, "session_end_utc")
        if end <= start:
            raise ValueError("session end must be after session start")
        if not isinstance(self.stats, Mapping):
            raise ValueError("stats must be a mapping")
        copied_stats = {}
        for key, value in self.stats.items():
            _identity(key, "stats key")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("stats values must be nonnegative integers")
            copied_stats[key] = value
        object.__setattr__(self, "session_start_utc", start)
        object.__setattr__(self, "session_end_utc", end)
        object.__setattr__(self, "stats", MappingProxyType(copied_stats))

    @property
    def tickers(self):
        return tuple(contract.ticker for contract in self.contracts)

    @property
    def provenance_by_ticker(self):
        return {contract.ticker: contract.provenance for contract in self.contracts}


def _choices(filters):
    if not isinstance(filters, Mapping):
        raise ValueError("Sports filters must be a mapping")
    ordering = filters.get("sport_ordering")
    sports = filters.get("sports")
    if not isinstance(ordering, (tuple, list)) or not isinstance(sports, Mapping):
        raise ValueError("Sports filters must have sport_ordering and sports")
    ordered = tuple(_identity(sport, "sport_ordering item") for sport in ordering)
    if len(set(ordered)) != len(ordered) or set(ordered) != set(sports):
        raise ValueError("Sports filters have inconsistent canonical choices")
    folded = [sport.casefold() for sport in ordered]
    if len(set(folded)) != len(folded):
        raise ValueError("Sports filters have ambiguous case-insensitive choices")
    return ordered, sports


def list_supported_sports(client) -> tuple[str, ...]:
    """Return the exact public API ordering, including its All sports entry."""
    if not hasattr(client, "get_sports_filters"):
        raise ValueError("client must provide get_sports_filters")
    ordering, _ = _choices(client.get_sports_filters())
    return ordering


def canonicalize_sports(requested, filters) -> tuple[str, ...]:
    """Validate explicit requested Sports and return their stable API ordering."""
    ordering, sports = _choices(filters)
    choices = ", ".join(ordering)
    if isinstance(requested, str):
        raise ValueError("requested Sports must be a sequence; choices: " + choices)
    try:
        requested = tuple(requested)
    except TypeError as error:
        raise ValueError("requested Sports must be a sequence; choices: " + choices) \
            from error
    by_folded_name = {sport.casefold(): sport for sport in ordering}
    selected = set()
    for raw_name in requested:
        if not isinstance(raw_name, str) or not raw_name:
            raise ValueError("requested Sport must be nonempty; choices: " + choices)
        canonical = by_folded_name.get(raw_name.casefold())
        if canonical is None:
            raise ValueError(f"unknown Sport {raw_name!r}; choices: {choices}")
        if canonical == "All sports":
            raise ValueError("All sports cannot be selected; choices: " + choices)
        if canonical in selected:
            raise ValueError(f"duplicate Sport {canonical!r}; choices: {choices}")
        details = sports[canonical]
        if not isinstance(details, Mapping):
            raise ValueError(
                f"Sport {canonical!r} has invalid filters; choices: {choices}")
        scopes = details.get("scopes")
        competitions = details.get("competitions")
        if not isinstance(scopes, (tuple, list, frozenset, set)) or "Games" not in scopes:
            raise ValueError(f"Sport {canonical!r} lacks exact Games scope; choices: {choices}")
        if not isinstance(competitions, Mapping) or not any(
                isinstance(comp_scopes, (tuple, list, frozenset, set))
                and "Games" in comp_scopes
                for comp_scopes in competitions.values()):
            raise ValueError(
                f"Sport {canonical!r} has no competition with exact Games scope; "
                f"choices: {choices}")
        selected.add(canonical)
    if not selected:
        raise ValueError("at least one Sport is required; choices: " + choices)
    return tuple(sport for sport in ordering if sport in selected)


def local_day_window(now=None):
    """Return the current/supplied timezone's half-open local calendar day."""
    if now is None:
        # ``datetime.now().astimezone().tzinfo`` is commonly a fixed-offset
        # object (for example PDT), not the system's full timezone rules.
        # Resolve each naive local midnight separately so a DST transition
        # produces the correct 23- or 25-hour UTC window.
        local_date = datetime.now().date()
        start = datetime.combine(local_date, time.min).astimezone()
        end = datetime.combine(
            local_date + timedelta(days=1), time.min).astimezone()
        label = start.tzname() or str(start.tzinfo)
    else:
        if (not isinstance(now, datetime)
                or now.tzinfo is None or now.utcoffset() is None):
            raise ValueError("now must be a timezone-aware datetime")
        start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        end = datetime.combine(now.date() + timedelta(days=1), time.min,
                               tzinfo=now.tzinfo)
        label = (getattr(now.tzinfo, "key", None)
                 or now.tzname() or str(now.tzinfo))
    return LocalDayWindow(
        label, start.isoformat(), end.isoformat(),
        start.astimezone(timezone.utc).timestamp(),
        end.astimezone(timezone.utc).timestamp())


def rank_contracts(candidates, contracts_per_trade):
    """Rank contract candidates by executable depth, price, time, then ticker."""
    cap = _finite_decimal(contracts_per_trade, "contracts_per_trade")
    if cap <= 0:
        raise ValueError("contracts_per_trade must be positive")
    candidates = tuple(candidates)
    if not all(isinstance(candidate, SelectedContract) for candidate in candidates):
        raise ValueError("candidates must contain SelectedContract values")
    return tuple(sorted(candidates, key=lambda contract: (
        -min(contract.bid_size, contract.ask_size, cap),
        contract.ask - contract.bid,
        -min(contract.bid_size, contract.ask_size),
        contract.provenance.scheduled_start_ts,
        contract.ticker,
    )))


def _selected_sports(filters):
    if isinstance(filters, Mapping):
        if "sports" in filters:
            values = filters["sports"]
            if not isinstance(values, Mapping):
                raise ValueError("filters.sports must be a mapping")
            values = values.keys()
        else:
            values = filters.keys()
    else:
        values = filters
    if isinstance(values, str):
        raise ValueError("canonical Sports must be a sequence")
    try:
        values = tuple(values)
    except TypeError as error:
        raise ValueError("canonical Sports must be a sequence") from error
    for sport in values:
        _identity(sport, "canonical Sport")
    if len(set(values)) != len(values):
        raise ValueError("canonical Sports must be unique")
    return frozenset(values)


def _increment(stats, key):
    if not isinstance(stats, Mapping) or not hasattr(stats, "__setitem__"):
        raise ValueError("stats must be a mutable mapping")
    current = stats.get(key, 0)
    if isinstance(current, bool) or not isinstance(current, int) or current < 0:
        raise ValueError(f"stats[{key!r}] must be a nonnegative integer")
    stats[key] = current + 1


def build_series_index(series_rows, filters, stats):
    """Index all official Sports prefixes and selected-Sport tag assignments."""
    selected_sports = _selected_sports(filters)
    try:
        rows = tuple(series_rows)
    except TypeError as error:
        raise ValueError("series_rows must be a sequence") from error
    official = []
    by_series = {}
    seen = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("series row must be a mapping")
        ticker = row.get("series_ticker")
        _identity(ticker, "series_ticker")
        category = _identity(row.get("category"), "series.category")
        tags = row.get("tags")
        if not isinstance(tags, (tuple, list)) or any(
                not isinstance(tag, str) or not tag for tag in tags):
            raise ValueError("series.tags must be a sequence of nonempty strings")
        if category != "Sports":
            _increment(stats, "skip_series_off_category")
            continue
        if ticker in seen:
            raise ValueError(f"duplicate official Sports series ticker {ticker!r}")
        seen.add(ticker)
        official.append(ticker)
        matching = tuple(tag for tag in tags if tag in selected_sports)
        if not matching:
            _increment(stats, "skip_series_no_sport_tag")
        elif len(matching) > 1:
            _increment(stats, "skip_series_ambiguous_sport")
        else:
            by_series[ticker] = matching[0]
    return SeriesIndex(tuple(official), MappingProxyType(dict(by_series)))


def resolve_series(event_ticker, official_series_tickers):
    """Resolve an event only through the unique longest official delimiter prefix."""
    _identity(event_ticker, "event_ticker")
    if isinstance(official_series_tickers, str):
        raise ValueError("official series tickers must be a sequence")
    try:
        tickers = tuple(official_series_tickers)
    except TypeError as error:
        raise ValueError("official series tickers must be a sequence") from error
    for ticker in tickers:
        _identity(ticker, "official series ticker")
    matches = [ticker for ticker in tickers
               if event_ticker.startswith(ticker + "-")]
    if not matches:
        return None
    longest = max(len(ticker) for ticker in matches)
    winners = [ticker for ticker in matches if len(ticker) == longest]
    if len(winners) != 1:
        raise ValueError(f"ambiguous Series resolution for {event_ticker!r}")
    return winners[0]


def _metadata_count(metadata, field, operation):
    if not isinstance(metadata, Mapping):
        raise ValueError(f"{operation} metadata must be a mapping")
    value = metadata.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{operation} metadata.{field} must be a nonnegative integer")
    return value


def _collect_metadata(stats, metadata, operation):
    for field in ("pages", "rows"):
        key = operation + "_" + field
        stats[key] = stats.get(key, 0) + _metadata_count(metadata, field, operation)
    if operation != "event":
        return
    skips = metadata.get("market_skips")
    if not isinstance(skips, Mapping):
        raise ValueError("event metadata.market_skips must be a mapping")
    for market_type, count in skips.items():
        _identity(market_type, "event metadata market skip type")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("event metadata market skip count must be a nonnegative integer")
        key = "skip_unsupported_market_" + market_type
        stats[key] = stats.get(key, 0) + count


def _check_normalized_milestone(row):
    if not isinstance(row, Mapping):
        raise ValueError("milestone row must be a mapping")
    _identity(row.get("milestone_id"), "milestone_id")
    _identity(row.get("category"), "milestone.category")
    _timestamp(row.get("start_ts"), "milestone.start_ts")
    _identity(row.get("title"), "milestone.title")
    league = row.get("league")
    if league is not None:
        _identity(league, "milestone.league")
    main = row.get("main_game_event_ticker")
    if main is not None:
        _identity(main, "milestone.main_game_event_ticker")
    primary = row.get("primary_event_tickers")
    if not isinstance(primary, (tuple, list)):
        raise ValueError("milestone.primary_event_tickers must be a sequence")
    for ticker in primary:
        _identity(ticker, "milestone.primary_event_ticker")
    related = row.get("related_event_tickers")
    if not isinstance(related, (tuple, list)):
        raise ValueError("milestone.related_event_tickers must be a sequence")
    for ticker in related:
        _identity(ticker, "milestone.related_event_ticker")
    if len(set(related)) != len(related):
        raise ValueError("milestone.related_event_tickers must be unique")


def _check_normalized_event(row):
    if not isinstance(row, Mapping):
        raise ValueError("event row must be a mapping")
    _identity(row.get("event_ticker"), "event_ticker")
    _identity(row.get("series_ticker"), "event.series_ticker")
    _identity(row.get("category"), "event.category")
    _identity(row.get("title"), "event.title")
    markets = row.get("markets")
    if not isinstance(markets, (tuple, list)):
        raise ValueError("event.markets must be a sequence")
    market_skips = row.get("market_skips")
    if not isinstance(market_skips, Mapping):
        raise ValueError("event.market_skips must be a mapping")
    return tuple(markets)


def _add_market_skip(stats, key):
    _increment(stats, key)


def _select_event_markets(event, expected, cfg, stats):
    markets = _check_normalized_event(event)
    if event["series_ticker"] != expected["series_ticker"]:
        raise ValueError(
            f"event {event['event_ticker']!r} has unexpected Series "
            f"{event['series_ticker']!r}")
    if event["category"] != "Sports":
        _add_market_skip(stats, "skip_event_off_category")
        return ()
    selected = []
    max_spread = Decimal(str(cfg.max_spread))
    for market in markets:
        if not isinstance(market, Mapping):
            raise ValueError("parsed Market must be a mapping")
        ticker = _identity(market.get("ticker"), "market.ticker")
        if market.get("event_ticker") != event["event_ticker"]:
            raise ValueError(
                f"Market {ticker!r} does not match event "
                f"{event['event_ticker']!r}")
        status = _identity(market.get("status"), "market.status")
        if status != "active":
            _add_market_skip(stats, "skip_market_inactive")
            continue
        bid = market.get("yes_bid")
        ask = market.get("yes_ask")
        if bid is None or ask is None:
            _add_market_skip(stats, "skip_market_missing_quote")
            continue
        bid_size = market.get("yes_bid_size")
        ask_size = market.get("yes_ask_size")
        if not all(isinstance(value, Decimal) and value.is_finite()
                   for value in (bid, ask)):
            raise ValueError(f"Market {ticker!r} has invalid parsed book")
        if any(value is not None
               and (not isinstance(value, Decimal) or not value.is_finite())
               for value in (bid_size, ask_size)):
            raise ValueError(f"Market {ticker!r} has invalid parsed book")
        if bid_size is None or ask_size is None:
            _add_market_skip(stats, "skip_market_no_depth")
            continue
        if bid_size <= 0 or ask_size <= 0:
            _add_market_skip(stats, "skip_market_no_depth")
            continue
        if ask - bid > max_spread:
            _add_market_skip(stats, "skip_market_wide_spread")
            continue
        title = market.get("title") or market.get("yes_sub_title") or ticker
        _identity(title, "market display title")
        selected.append(SelectedContract(
            ticker=ticker, title=title, game_title=event["title"], bid=bid,
            ask=ask, bid_size=bid_size, ask_size=ask_size,
            provenance=ContractProvenance(
                sport=expected["sport"], league=expected["league"],
                series_ticker=expected["series_ticker"],
                milestone_id=expected["milestone_id"],
                event_ticker=event["event_ticker"],
                scheduled_start_ts=expected["scheduled_start_ts"])))
    return tuple(selected)


def _validated_explicit_tickers(cfg):
    raw_tickers = getattr(cfg, "tickers", None)
    if not isinstance(raw_tickers, (list, tuple)):
        raise ValueError(
            "explicit tickers must be an ordered list or tuple")
    tickers = tuple(raw_tickers)
    for ticker in tickers:
        _identity(ticker, "explicit ticker")
    if len(set(tickers)) != len(tickers):
        raise ValueError("explicit ticker list contains a duplicate")
    max_markets = getattr(cfg, "max_monitored_markets", None)
    if (isinstance(max_markets, bool) or not isinstance(max_markets, int)
            or max_markets <= 0):
        raise ValueError("cfg.max_monitored_markets must be a positive integer")
    if len(tickers) > max_markets:
        raise ValueError(
            f"explicit ticker list exceeds monitoring cap {max_markets}")
    return tickers


def _stable_market_identity(market, context):
    if not isinstance(market, Mapping):
        raise ValueError(f"{context} must be a parsed Market mapping")
    ticker = _identity(market.get("ticker"), f"{context}.ticker")
    event_ticker = _identity(
        market.get("event_ticker"), f"{context}.event_ticker")
    market_type = _identity(
        market.get("market_type"), f"{context}.market_type")
    if market_type != "binary":
        raise ValueError(
            f"{context} {ticker!r} must be a supported binary Market")
    notional = _finite_decimal(
        market.get("notional_value"), f"{context}.notional_value")
    if notional != Decimal(1):
        raise ValueError(
            f"{context} {ticker!r} must have exactly $1 notional")
    return ticker, event_ticker, market_type, notional


def _add_direct_event_market_skips(stats, event):
    skips = event["market_skips"]
    for market_type, count in skips.items():
        _identity(market_type, "event.market_skips type")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(
                "event.market_skips count must be a nonnegative integer")
        key = "skip_unsupported_market_" + market_type
        stats[key] = stats.get(key, 0) + count


def _explicit_selected_contract(market, event, expected, cfg):
    ticker = _identity(market.get("ticker"), "market.ticker")
    status = _identity(market.get("status"), "market.status")
    if status != "active":
        raise ValueError(f"explicit Market {ticker!r} is not active")
    bid = market.get("yes_bid")
    ask = market.get("yes_ask")
    if bid is None or ask is None:
        raise ValueError(
            f"explicit Market {ticker!r} lacks a two-sided quote")
    if not all(isinstance(value, Decimal) and value.is_finite()
               for value in (bid, ask)):
        raise ValueError(f"explicit Market {ticker!r} has an invalid quote")
    bid_size = market.get("yes_bid_size")
    ask_size = market.get("yes_ask_size")
    if not all(isinstance(value, Decimal) and value.is_finite()
               for value in (bid_size, ask_size)):
        raise ValueError(f"explicit Market {ticker!r} lacks valid depth")
    if bid_size <= 0 or ask_size <= 0:
        raise ValueError(
            f"explicit Market {ticker!r} lacks positive two-sided depth")
    if bid > ask:
        raise ValueError(f"explicit Market {ticker!r} has a crossed book")
    max_spread = Decimal(str(cfg.max_spread))
    if not max_spread.is_finite() or max_spread < 0:
        raise ValueError("cfg.max_spread must be finite and nonnegative")
    if ask - bid > max_spread:
        raise ValueError(
            f"explicit Market {ticker!r} exceeds max spread")
    title = market.get("title") or market.get("yes_sub_title") or ticker
    _identity(title, "market display title")
    return SelectedContract(
        ticker=ticker, title=title, game_title=event["title"],
        bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size,
        provenance=ContractProvenance(
            sport=expected["sport"], league=expected["league"],
            series_ticker=expected["series_ticker"],
            milestone_id=expected["milestone_id"],
            event_ticker=event["event_ticker"],
            scheduled_start_ts=expected["scheduled_start_ts"]))


def _discover_explicit_tickers(cfg, client, *, now=None):
    tickers = _validated_explicit_tickers(cfg)
    for method in ("get_sports_filters", "get_sports_series", "get_market",
                   "get_event", "get_sports_milestones"):
        if not hasattr(client, method):
            raise ValueError(f"client must provide {method}")

    stats = {}
    filters = client.get_sports_filters()
    sport_ordering, sports = _choices(filters)
    window = local_day_window(now)
    minimum_start_date = datetime.fromtimestamp(
        window.session_start_utc, tz=timezone.utc)

    series_rows = client.get_sports_series()
    try:
        series_rows = tuple(series_rows)
    except TypeError as error:
        raise ValueError("Sports Series response must be a sequence") from error
    stats["series_rows"] = len(series_rows)
    canonical_sports = tuple(
        sport for sport in sport_ordering if sport != "All sports")
    index = build_series_index(series_rows, canonical_sports, stats)

    direct_markets = {}
    requested_by_event = {}
    event_resolution = {}
    get_market = getattr(
        client, "get_market_for_discovery", client.get_market)
    for requested_ticker in tickers:
        direct = get_market(requested_ticker)
        identity = _stable_market_identity(
            direct, f"direct Market {requested_ticker!r}")
        if identity[0] != requested_ticker:
            raise ValueError(
                f"requested ticker {requested_ticker!r} returned "
                f"Market {identity[0]!r}")
        event_ticker = identity[1]
        series_ticker = resolve_series(
            event_ticker, index.official_series_tickers)
        if series_ticker is None:
            raise ValueError(
                f"explicit Market {requested_ticker!r} has unresolved "
                f"Series for event {event_ticker!r}")
        sport = index.sport_by_series.get(series_ticker)
        if sport is None:
            raise ValueError(
                f"explicit Market {requested_ticker!r} Series "
                f"{series_ticker!r} has no unique canonical Sport")
        canonicalize_sports((sport,), filters)
        resolution = (series_ticker, sport)
        previous = event_resolution.get(event_ticker)
        if previous is not None and previous != resolution:
            raise ValueError(
                f"event {event_ticker!r} has conflicting Series/Sport "
                "resolution")
        event_resolution[event_ticker] = resolution
        direct_markets[requested_ticker] = direct
        requested_by_event.setdefault(event_ticker, []).append(
            requested_ticker)

    contracts_by_ticker = {}
    seen_milestones = {}
    for event_ticker, requested_tickers in requested_by_event.items():
        series_ticker, sport = event_resolution[event_ticker]
        event = client.get_event(
            event_ticker, with_nested_markets=True)
        markets = _check_normalized_event(event)
        stats["event_pages"] = stats.get("event_pages", 0) + 1
        stats["event_rows"] = stats.get("event_rows", 0) + 1
        _add_direct_event_market_skips(stats, event)
        if event["event_ticker"] != event_ticker:
            raise ValueError(
                f"requested event {event_ticker!r} returned "
                f"{event['event_ticker']!r}")
        if event["category"] != "Sports":
            raise ValueError(
                f"explicit event {event_ticker!r} is not Sports")
        if event["series_ticker"] != series_ticker:
            raise ValueError(
                f"explicit event {event_ticker!r} Series "
                f"{event['series_ticker']!r} disagrees with official "
                f"{series_ticker!r}")

        nested_by_ticker = {}
        requested_set = set(requested_tickers)
        for nested in markets:
            identity = _stable_market_identity(
                nested, f"nested Market in {event_ticker!r}")
            if identity[0] not in requested_set:
                continue
            if identity[0] in nested_by_ticker:
                raise ValueError(
                    f"explicit Market {identity[0]!r} occurs more than once "
                    f"in event {event_ticker!r}")
            nested_by_ticker[identity[0]] = nested
        for requested_ticker in requested_tickers:
            nested = nested_by_ticker.get(requested_ticker)
            if nested is None:
                raise ValueError(
                    f"explicit Market {requested_ticker!r} is missing from "
                    f"event {event_ticker!r}")
            direct_identity = _stable_market_identity(
                direct_markets[requested_ticker],
                f"direct Market {requested_ticker!r}")
            nested_identity = _stable_market_identity(
                nested, f"nested Market {requested_ticker!r}")
            if direct_identity != nested_identity:
                raise ValueError(
                    f"explicit Market {requested_ticker!r} direct/nested "
                    "stable identity mismatch")

        milestone_rows, metadata = client.get_sports_milestones(
            related_event_ticker=event_ticker,
            minimum_start_date=minimum_start_date)
        _collect_metadata(stats, metadata, "milestone")
        try:
            milestone_rows = tuple(milestone_rows)
        except TypeError as error:
            raise ValueError(
                "Sports milestones response must be a sequence") from error
        seen_event_milestones = set()
        proofs = []
        for milestone in milestone_rows:
            _check_normalized_milestone(milestone)
            milestone_id = milestone["milestone_id"]
            previous = seen_milestones.get(milestone_id)
            if previous is not None:
                if previous != milestone:
                    raise ValueError(
                        f"duplicate milestone {milestone_id!r} has "
                        "conflicting metadata")
            else:
                seen_milestones[milestone_id] = milestone
            if event_ticker not in milestone["related_event_tickers"]:
                raise ValueError(
                    f"milestone {milestone_id!r} returned for "
                    f"{event_ticker!r} does not list that related event")
            if milestone_id in seen_event_milestones:
                continue
            seen_event_milestones.add(milestone_id)
            if milestone["category"] != "Sports":
                continue
            start = milestone["start_ts"]
            if not window.session_start_utc <= start < window.session_end_utc:
                continue
            selected_event = milestone["main_game_event_ticker"]
            if not selected_event and len(milestone["primary_event_tickers"]) == 1:
                selected_event = milestone["primary_event_tickers"][0]
            if selected_event == event_ticker:
                proofs.append(milestone)
        if len(proofs) != 1:
            raise ValueError(
                f"explicit event {event_ticker!r} requires exactly one "
                f"current-day Games milestone proof, got {len(proofs)}")
        proof = proofs[0]
        expected = {
            "sport": sport,
            "league": proof["league"],
            "series_ticker": series_ticker,
            "milestone_id": proof["milestone_id"],
            "scheduled_start_ts": proof["start_ts"],
        }
        for requested_ticker in requested_tickers:
            contracts_by_ticker[requested_ticker] = \
                _explicit_selected_contract(
                    nested_by_ticker[requested_ticker], event, expected, cfg)

    contracts = tuple(contracts_by_ticker[ticker] for ticker in tickers)
    selected = {contract.provenance.sport for contract in contracts}
    selected_sports = tuple(
        sport for sport in sport_ordering if sport in selected)
    stats["candidates"] = len(contracts)
    stats["selected"] = len(contracts)
    return DiscoveryResult(
        contracts=contracts, selected_sports=selected_sports,
        local_timezone=window.local_timezone,
        session_start_local=window.session_start_local,
        session_end_local=window.session_end_local,
        session_start_utc=window.session_start_utc,
        session_end_utc=window.session_end_utc,
        stats=dict(sorted(stats.items())))


def discover_game_contracts(cfg, client, *, now=None):
    """Prove and select today's Games from one explicit discovery source."""
    has_sports = bool(getattr(cfg, "sports", None))
    has_tickers = bool(getattr(cfg, "tickers", None))
    if has_sports == has_tickers:
        raise ValueError(
            "discover_game_contracts requires exactly one of cfg.sports "
            "or cfg.tickers")
    if has_tickers:
        return _discover_explicit_tickers(cfg, client, now=now)
    for method in ("get_sports_filters", "get_sports_series",
                   "get_sports_milestones", "get_open_events"):
        if not hasattr(client, method):
            raise ValueError(f"client must provide {method}")

    stats = {}
    filters = client.get_sports_filters()
    selected_sports = canonicalize_sports(cfg.sports, filters)
    window = local_day_window(now)
    minimum_start_date = datetime.fromtimestamp(
        window.session_start_utc, tz=timezone.utc)

    series_rows = client.get_sports_series()
    try:
        series_rows = tuple(series_rows)
    except TypeError as error:
        raise ValueError("Sports Series response must be a sequence") from error
    stats["series_rows"] = len(series_rows)
    sport_ordering, _ = _choices(filters)
    canonical_sports = tuple(
        sport for sport in sport_ordering if sport != "All sports")
    index = build_series_index(series_rows, canonical_sports, stats)

    seen_milestones = {}
    expected_events = {}
    for sport in selected_sports:
        competitions = filters["sports"][sport]["competitions"]
        names = sorted(name for name, scopes in competitions.items()
                       if "Games" in scopes)
        for competition in names:
            rows, metadata = client.get_sports_milestones(
                competition=competition, minimum_start_date=minimum_start_date)
            _collect_metadata(stats, metadata, "milestone")
            try:
                rows = tuple(rows)
            except TypeError as error:
                raise ValueError("Sports milestones response must be a sequence") from error
            for milestone in rows:
                _check_normalized_milestone(milestone)
                milestone_id = milestone["milestone_id"]
                previous = seen_milestones.get(milestone_id)
                if previous is not None:
                    if previous != milestone:
                        raise ValueError(
                            f"duplicate milestone {milestone_id!r} has conflicting metadata")
                else:
                    seen_milestones[milestone_id] = milestone
                if milestone["category"] != "Sports":
                    _add_market_skip(stats, "skip_milestone_off_category")
                    continue
                start = milestone["start_ts"]
                if not window.session_start_utc <= start < window.session_end_utc:
                    _add_market_skip(stats, "skip_milestone_outside_day")
                    continue
                event_ticker = milestone.get("main_game_event_ticker")
                if not event_ticker:
                    primary = milestone["primary_event_tickers"]
                    if len(primary) != 1:
                        _add_market_skip(stats, "skip_games_ambiguous")
                        continue
                    event_ticker = primary[0]
                resolved_series = resolve_series(
                    event_ticker, index.official_series_tickers)
                if resolved_series is None:
                    _add_market_skip(stats, "skip_event_unresolved_series")
                    continue
                resolved_sport = index.sport_by_series.get(resolved_series)
                if resolved_sport is None:
                    _add_market_skip(stats, "skip_event_unmapped_sport")
                    continue
                if resolved_sport != sport:
                    _add_market_skip(stats, "skip_competition_sport_mismatch")
                    continue
                expected = {
                    "sport": sport,
                    # League is optional provenance and must remain absent
                    # when Kalshi does not supply it.  A competition query
                    # name is filtering metadata, not authoritative league
                    # identity, and the same Milestone may appear under more
                    # than one competition.
                    "league": milestone["league"],
                    "series_ticker": resolved_series,
                    "milestone_id": milestone_id,
                    "scheduled_start_ts": start,
                    "game_title": milestone["title"],
                }
                previous = expected_events.get(event_ticker)
                if previous is not None:
                    if previous != expected:
                        raise ValueError(
                            f"Games event {event_ticker!r} has conflicting provenance")
                    continue
                expected_events[event_ticker] = expected

    expected_by_series = {}
    for event_ticker, expected in expected_events.items():
        expected_by_series.setdefault(expected["series_ticker"], {})[event_ticker] = expected
    candidates = []
    candidate_tickers = set()
    for series_ticker in sorted(expected_by_series):
        rows, metadata = client.get_open_events(series_ticker=series_ticker)
        _collect_metadata(stats, metadata, "event")
        try:
            rows = tuple(rows)
        except TypeError as error:
            raise ValueError("open events response must be a sequence") from error
        seen_events = {}
        expected = expected_by_series[series_ticker]
        for event in rows:
            _check_normalized_event(event)
            event_ticker = event["event_ticker"]
            if event_ticker not in expected:
                continue
            previous = seen_events.get(event_ticker)
            if previous is not None:
                if previous != event:
                    raise ValueError(
                        f"duplicate event {event_ticker!r} has conflicting metadata")
                continue
            seen_events[event_ticker] = event
            for contract in _select_event_markets(event, expected[event_ticker], cfg, stats):
                if contract.ticker in candidate_tickers:
                    raise ValueError(f"duplicate selected Market {contract.ticker!r}")
                candidate_tickers.add(contract.ticker)
                candidates.append(contract)
        for event_ticker in expected:
            if event_ticker not in seen_events:
                _add_market_skip(stats, "skip_event_not_open")

    stats["candidates"] = len(candidates)
    ranked = rank_contracts(candidates, Decimal(str(cfg.contracts_per_trade)))
    max_markets = getattr(cfg, "max_monitored_markets", None)
    if isinstance(max_markets, bool) or not isinstance(max_markets, int) or max_markets <= 0:
        raise ValueError("cfg.max_monitored_markets must be a positive integer")
    contracts = ranked[:max_markets]
    stats["selected"] = len(contracts)
    return DiscoveryResult(
        contracts=contracts, selected_sports=selected_sports,
        local_timezone=window.local_timezone,
        session_start_local=window.session_start_local,
        session_end_local=window.session_end_local,
        session_start_utc=window.session_start_utc,
        session_end_utc=window.session_end_utc,
        stats=dict(sorted(stats.items())))
