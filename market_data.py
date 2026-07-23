"""Quotes with depth, from the orderbook endpoint (one call per tick gives
bid, ask, AND top-of-book quantities). Staleness is tracked from subscribe
time, so a market that never delivers a quote counts as stale from t=0."""
import time
import collections

from schemas import SchemaError
from kalshi_client import format_market_skips


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
        self.group_ids = {}
        self.close_times = {}
        self.can_close_early = {}

    def subscribe(self, tickers):
        now = self.clock()
        for t in tickers:
            self.last_good.setdefault(t, now)   # stale clock starts NOW

    def discover_tickers(self):
        if self.cfg.tickers:
            return self.cfg.tickers
        found = []
        markets = self.client.get_markets(
            status="open", limit=200, mve_filter="exclude")
        print("[discover] " + format_market_skips(
            getattr(self.client, "last_market_skips", {})))
        keywords = [k.lower() for k in self.cfg.market_keywords]
        for m in markets:
            if (m["status"] != "active"
                    or m["yes_bid"] is None or m["yes_ask"] is None
                    or m["yes_bid_size"] <= 0 or m["yes_ask_size"] <= 0):
                continue
            text = (m["title"] + " " + m["ticker"]).lower()
            if any(kw in text for kw in keywords):
                found.append(m["ticker"])
                self.group_ids[m["ticker"]] = m["event_ticker"]
                print(f"[discover] {m['ticker']}: {m['title'][:60]}")
        return found

    def get_quote(self, ticker):
        """Return ``(mid, bid, ask, observed_at)``.

        The timestamp is captured exactly once after the response is parsed
        and travels with that observation through logging, signals, and paper
        fills.  One call to the market
        endpoint yields bid, ask, AND top-of-book sizes (live V2 schema).
        Raises on transport/schema errors so safety can count them."""
        m = self.client.get_market(ticker)          # SchemaError propagates
        observed_at = self.clock()
        event_ticker = m.get("event_ticker")
        if not event_ticker:
            raise SchemaError(
                f"market {ticker}: missing event_ticker; research grouping "
                "would be unsafe")
        self.group_ids[ticker] = event_ticker
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

    def group_id(self, ticker):
        return self.group_ids.get(ticker)

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
