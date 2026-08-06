"""Quotes with depth, from the orderbook endpoint (one call per tick gives
bid, ask, AND top-of-book quantities). Staleness is tracked from subscribe
time, so a market that never delivers a quote counts as stale from t=0."""
import time
import collections
from types import MappingProxyType

from schemas import SchemaError
from sports_discovery import (
    DiscoveryResult,
    SelectedContract,
    discover_game_contracts,
)


class MarketUnavailable(Exception):
    """Documented lifecycle/liquidity state, not an API contract failure."""


class PriceFeed:
    def __init__(self, config, client, clock=time.time):
        self.cfg = config
        self.client = client
        self.clock = clock
        self.history = collections.defaultdict(
            lambda: collections.deque(maxlen=600))
        self.last_good = {}
        self.last_book = {}     # ticker -> (bid, bid_qty, ask, ask_qty)
        self.contracts_by_ticker = MappingProxyType({})
        self.provenance_by_ticker = MappingProxyType({})
        self.trade_tickers = frozenset()
        self.watch_tickers = frozenset()
        self._discovery_installed = False
        self.close_times = {}
        self.can_close_early = {}

    def install_discovery(self, discovery):
        """Install one complete, immutable discovery result atomically."""
        if self._discovery_installed:
            raise ValueError("discovery is already installed")
        if not isinstance(discovery, DiscoveryResult):
            raise ValueError("discovery must be a DiscoveryResult")

        selected_sports = frozenset(discovery.selected_sports)
        contracts = {}
        provenance = {}
        trade = set()
        watch = set()
        for contract in discovery.contracts:
            if not isinstance(contract, SelectedContract):
                raise ValueError(
                    "discovery contracts must contain SelectedContract values")
            ticker = contract.ticker
            if ticker in contracts:
                raise ValueError(f"duplicate discovery ticker {ticker!r}")
            if contract.provenance.sport not in selected_sports:
                raise ValueError(
                    f"contract {ticker!r} Sport "
                    f"{contract.provenance.sport!r} is not selected")
            contracts[ticker] = contract
            provenance[ticker] = contract.provenance
            trade.add(ticker)
        for contract in getattr(discovery, "watch_contracts", ()) or ():
            if not isinstance(contract, SelectedContract):
                raise ValueError(
                    "watch contracts must contain SelectedContract values")
            ticker = contract.ticker
            if ticker in provenance:
                raise ValueError(f"duplicate discovery ticker {ticker!r}")
            if contract.provenance.sport not in selected_sports:
                raise ValueError(
                    f"watch contract {ticker!r} Sport "
                    f"{contract.provenance.sport!r} is not selected")
            provenance[ticker] = contract.provenance
            watch.add(ticker)

        self.contracts_by_ticker = MappingProxyType(contracts)
        self.provenance_by_ticker = MappingProxyType(provenance)
        self.trade_tickers = frozenset(trade)
        self.watch_tickers = frozenset(watch)
        self._discovery_installed = True

    def discover(self, *, now=None, scoreboard_gate=None):
        bind_predicate = None
        sibling_score = None
        gate_on = (
            scoreboard_gate is not None
            and getattr(scoreboard_gate, "enabled", lambda: False)())
        prefer = bool(getattr(self.cfg, "prefer_scoreboard_bind", True))
        if prefer and gate_on:
            def bind_predicate(contract, gate=scoreboard_gate):
                return gate.is_bound(
                    ticker=contract.ticker,
                    player_name=contract.title,
                    event_title=contract.game_title)
        if (bool(getattr(self.cfg, "one_contract_per_event", True))
                and gate_on
                and hasattr(scoreboard_gate, "model_edge_score")):
            def sibling_score(contract, gate=scoreboard_gate):
                return gate.model_edge_score(
                    ticker=contract.ticker,
                    player_name=contract.title,
                    event_title=contract.game_title,
                    ask_cents=contract.ask)
        result = discover_game_contracts(
            self.cfg, self.client, now=now,
            bind_predicate=bind_predicate,
            sibling_score=sibling_score)
        self.install_discovery(result)
        return result

    def discover_tickers(self):
        """Compatibility adapter; production startup uses ``discover``."""
        return list(self.discover().tickers)

    def subscribe(self, tickers):
        if not self._discovery_installed:
            raise SchemaError("discovery provenance is not installed")
        if isinstance(tickers, (str, bytes)):
            raise ValueError("subscription tickers must be a sequence")
        try:
            tickers = tuple(tickers)
        except TypeError as error:
            raise ValueError(
                "subscription tickers must be an iterable") from error

        seen = set()
        for ticker in tickers:
            if not isinstance(ticker, str) or not ticker:
                raise ValueError(
                    "subscription tickers must be nonempty strings")
            if ticker in seen:
                raise ValueError(
                    f"duplicate subscription ticker {ticker!r}")
            if ticker not in self.provenance_by_ticker:
                raise SchemaError(
                    f"subscription ticker {ticker!r} was not discovered")
            seen.add(ticker)

        now = self.clock()
        for ticker in tickers:
            self.last_good.setdefault(ticker, now)   # stale clock starts NOW

    def get_quote(self, ticker):
        """Return ``(mid, bid, ask, observed_at)``.

        The timestamp is captured exactly once after the response is parsed
        and travels with that observation through logging, signals, and paper
        fills.  One call to the market
        endpoint yields bid, ask, AND top-of-book sizes (live V2 schema).
        Raises on transport/schema errors so safety can count them."""
        expected = self.provenance(ticker)
        m = self.client.get_market(ticker)          # SchemaError propagates
        if m.get("ticker") != ticker:
            raise SchemaError(
                f"market identity mismatch for {ticker!r}: "
                f"got ticker={m.get('ticker')!r}")
        if m.get("event_ticker") != expected.event_ticker:
            raise SchemaError(
                f"market event mismatch for {ticker!r}: expected "
                f"{expected.event_ticker!r}, got "
                f"{m.get('event_ticker')!r}")
        observed_at = self.clock()
        if m.get("close_ts") is not None:
            self.close_times[ticker] = m["close_ts"]
        self.can_close_early[ticker] = bool(m.get("can_close_early"))
        if m.get("status") != "active":
            raise MarketUnavailable(
                f"market {ticker} unavailable: status={m.get('status')!r}")
        bid, ask = m["yes_bid"], m["yes_ask"]
        bid_qty, ask_qty = m["yes_bid_size"], m["yes_ask_size"]
        if (bid is None or ask is None
                or bid_qty is None or ask_qty is None
                or bid_qty <= 0 or ask_qty <= 0):
            raise MarketUnavailable(
                f"market {ticker} unavailable: empty executable book")
        mid = (bid + ask) / 2   # Decimal
        self.history[ticker].append((observed_at, mid))
        self.last_good[ticker] = observed_at
        self.last_book[ticker] = (bid, bid_qty, ask, ask_qty)
        return mid, bid, ask, observed_at

    def top_of_book(self, ticker):
        return self.last_book.get(ticker, (None, None, None, None))

    def sibling_tickers(self, ticker):
        """Other YES contracts on the same Event (watch or trade)."""
        event = self.group_id(ticker)
        out = []
        for other, provenance in self.provenance_by_ticker.items():
            if other == ticker:
                continue
            if provenance.event_ticker == event:
                out.append(other)
        return tuple(out)

    def mid_rise_in_lookback(self, ticker, now, lookback_s):
        """Latest mid minus minimum mid in ``[now-lookback, now]``.

        Returns 0 when fewer than two samples are available.
        """
        from decimal import Decimal
        history = list(self.history.get(ticker) or ())
        if not history:
            return Decimal(0)
        lookback_s = float(lookback_s)
        window = [(ts, mid) for ts, mid in history
                  if now - lookback_s <= float(ts) <= float(now)]
        if len(window) < 2:
            return Decimal(0)
        latest = window[-1][1]
        floor = min(mid for _, mid in window)
        rise = latest - floor
        if rise < 0:
            return Decimal(0)
        return rise

    def provenance(self, ticker):
        if not isinstance(ticker, str) or not ticker:
            raise SchemaError("ticker must be a nonempty string")
        if not self._discovery_installed:
            raise SchemaError("discovery provenance is not installed")
        try:
            return self.provenance_by_ticker[ticker]
        except KeyError as error:
            raise SchemaError(
                f"ticker {ticker!r} has no installed discovery provenance") \
                from error

    def group_id(self, ticker):
        return self.provenance(ticker).event_ticker

    def lifecycle(self, ticker):
        """Return the lifecycle facts attached to the latest observation."""
        return (self.close_times.get(ticker),
                self.can_close_early.get(ticker))

    def entry_allowed(self, ticker, now, required_seconds):
        close_ts = self.close_times.get(ticker)
        return (close_ts is not None
                and now + float(required_seconds) < close_ts)

    def early_close_risk(self, ticker):
        return self.can_close_early.get(ticker, True)

    def stale_tickers(self, tickers):
        now = self.clock()
        return [t for t in tickers
                if now - self.last_good.get(t, 0) > self.cfg.stale_data_s]
