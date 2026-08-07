"""Inci configuration — Kalshi edition. All prices in CENTS (1-99).

LIVE TRADING IS DISABLED IN THIS BUILD (no flag or env var enables it).
"""
import os
import pwd
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


def _default_state_root():
    # Safety state for any future order-enabled build must not be movable by
    # an environment variable: two launches with different environments
    # would otherwise acquire different locks and reset the daily loss view.
    account_home = pwd.getpwuid(os.getuid()).pw_dir
    return os.path.abspath(os.path.join(
        account_home, ".local", "state", "inci"))


@dataclass
class Config:
    # --- Mode ---
    paper_trading: bool = True
    use_demo_env: bool = False
    # NOTE: there is intentionally no live-enable flag. See bot.py docstring.

    # --- API credentials ---
    api_key_id: str = os.getenv("KALSHI_API_KEY_ID", "")
    private_key_path: str = os.getenv("KALSHI_PRIVATE_KEY_PATH", "kalshi_key.pem")
    subaccount: int = 0

    @property
    def api_base(self):
        if self.use_demo_env:
            return "https://external-api.demo.kalshi.co/trade-api/v2"
        return "https://external-api.kalshi.com/trade-api/v2"

    # --- Markets ---
    tickers: list = field(default_factory=list)
    sports: list = field(default_factory=list)
    max_monitored_markets: int = 10

    # --- Strategy (cents). Defaults sized so TP clears fees near 50c (fix #4):
    # net_take_profit(50, 5) ~= +1.2c; a 2c TP would be net-negative there.
    # take_profit is the ARM floor (minimum favorable move). With
    # tp_trail_cents > 0, exit waits for a pullback from the post-arm peak so
    # a set-driven spike can run past the floor before we sell.
    dip_threshold: int = 7
    lookback_seconds: int = 45
    take_profit: int = 5
    tp_trail_cents: int = 2           # 0 = fixed TP at arm; >0 = trail giveback
    stop_loss: int = 6
    max_hold_seconds: int = 300       # also the analyzer's signal cooldown
    close_buffer_seconds: int = 60    # no new entry this long before close

    # --- Risk limits (P&L is fee-inclusive everywhere) ---
    contracts_per_trade: int = 20
    max_open_positions: int = 3
    max_daily_loss_usd: float = 30.0
    min_price: int = 10
    max_price: int = 90
    max_spread: int = 3

    # --- Paper-mode realism (fix #7) ---
    # Paper is delayed IOC / taker-at-touch (not maker GTC). Latency delays
    # the single fill attempt; stop-loss applies adverse slippage.
    sim_latency_s: float = 1.0        # delay between signal and simulated fill
    sim_slippage_cents: int = 1       # adverse adjustment on stop-loss fills
    # Kalshi balance target: ordinary/non-direct accounts use $0.01; direct
    # members use $0.0001. Paper fees include the corresponding rounding fee.
    balance_precision_usd: str = "0.01"

    # --- Execution & safety (fixes #8, #9) ---
    poll_interval: float = 1.5
    # Also bounds how late the first eligible paper quote may arrive after a
    # delayed IOC's due_at; later observations cancel instead of back-filling.
    fill_timeout_s: float = 4.0       # cancel unfilled/stale IOC after this
    cancel_timeout_s: float = 5.0     # poll-until-terminal after a cancel
    reconcile_timeout_s: float = 5.0  # wait for order/fill/position agreement
    flatten_retries: int = 3          # attempts per position when flattening
    # --- ESPN score + win-prob entry gate (research) ---
    # When the runtime attaches EspnProbGate, entries require a live ESPN
    # ATP/WTA bind and a score-model edge. ITF can bind via Live Tennis when
    # enabled + keyed; otherwise unbound markets fail closed.
    espn_gate_enabled: bool = True
    espn_leagues: tuple = ("atp", "wta")
    espn_cache_s: float = 15.0
    espn_min_model_prob: float = 0.35   # reject collapsing sides
    espn_min_edge: float = 0.03         # model_prob - ask/100
    # Optional read-only bridge from the separate Models 1+2 pilot.  With no
    # path, the live score transform is a guard only and cannot claim edge.
    two_model_prior_path: str = os.getenv("INCI_TWO_MODEL_PRIOR_PATH", "")
    two_model_prior_max_age_s: float = 86400.0
    # When True (default), discovery ranks scoreboard-bindable contracts
    # ahead of unbound ones, then applies the usual depth/spread ranking
    # within each tier. Still capped by max_monitored_markets.
    prefer_scoreboard_bind: bool = True
    # When True (default), monitor/enter at most one YES contract per Event
    # (no both-sides of the same match). Among siblings, discovery prefers
    # the better scoreboard model edge when a gate score is available.
    one_contract_per_event: bool = True
    # Block entry when the opposite YES (sibling) mid has spiked up within
    # the lookback — e.g. underdog 5c→40c while we buy the favorite.
    # Requires quoting watch_contracts (siblings) alongside traded markets.
    sibling_spike_enabled: bool = True
    sibling_spike_cents: int = 15
    sibling_spike_lookback_s: float = 45.0
    # Live Tennis API secondary feed (ITF / optional challenger). Key from
    # live_tennis_api_key or env LIVETENNISAPI_KEY / LIVETENNIS_API_KEY.
    live_tennis_enabled: bool = True
    live_tennis_api_key: str = ""
    live_tennis_tours: tuple = ("itf",)
    live_tennis_cache_s: float = 120.0
    live_tennis_include_upcoming: bool = False
    live_tennis_ticker_substrings: tuple = ("ITF",)

    # Official Create V2 enums. Paper matches this: one delayed attempt, then
    # cancel remainder — never retain a stale working order.
    time_in_force: str = "immediate_or_cancel"
    self_trade_prevention_type: str = "taker_at_cross"
    stale_data_s: float = 30.0        # halt if a market's quotes go stale
    max_consec_errors: int = 5        # halt after this many API errors
    reconcile_every_s: float = 60.0   # position reconciliation cadence (live)
    # Durable safety state is absolute and environment/subaccount scoped so
    # two launches from different working directories cannot select separate
    # locks or loss ledgers.  Account identity is deliberately not a
    # user-controlled namespace: until the API provides a stable account ID,
    # different accounts sharing this state root collide safely instead of
    # silently splitting risk state.
    state_root: str = field(default_factory=_default_state_root)
    process_lock_path: str = field(init=False, default="")
    order_journal_path: str = field(init=False, default="")
    daily_pnl_path: str = field(init=False, default="")
    _state_identity: tuple = field(init=False, repr=False, default=())

    def __post_init__(self):
        self.validate()
        self.state_root = os.path.abspath(os.path.expanduser(self.state_root))
        environment = "demo" if self.use_demo_env else "production"
        directory = os.path.join(
            self.state_root, environment, f"subaccount-{self.subaccount}")
        defaults = {
            "process_lock_path": "inci.lock",
            "order_journal_path": "orders.jsonl",
            "daily_pnl_path": "daily_pnl.jsonl",
        }
        for name, filename in defaults.items():
            setattr(self, name, os.path.join(directory, filename))
        self._state_identity = self._expected_state_identity()

    def _expected_state_identity(self):
        root = os.path.abspath(os.path.expanduser(self.state_root))
        environment = "demo" if self.use_demo_env else "production"
        directory = os.path.join(
            root, environment, f"subaccount-{self.subaccount}")
        return (root, environment, self.subaccount,
                os.path.join(directory, "inci.lock"),
                os.path.join(directory, "orders.jsonl"),
                os.path.join(directory, "daily_pnl.jsonl"))

    def validate(self):
        """Fail fast before research with impossible/favorable parameters."""
        def number(name, *, positive=False, nonnegative=False):
            raw = getattr(self, name)
            if (isinstance(raw, bool)
                    or not isinstance(raw, (int, float, Decimal))):
                raise ValueError(f"{name} must be a real numeric value")
            try:
                value = Decimal(str(raw))
            except (InvalidOperation, ValueError, TypeError) as error:
                raise ValueError(f"{name} must be numeric") from error
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")
            if positive and value <= 0:
                raise ValueError(f"{name} must be positive")
            if nonnegative and value < 0:
                raise ValueError(f"{name} must be nonnegative")
            return value

        def positive_int(name):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

        if not isinstance(self.paper_trading, bool):
            raise ValueError("paper_trading must be boolean")
        if not isinstance(self.use_demo_env, bool):
            raise ValueError("use_demo_env must be boolean")
        if not isinstance(self.state_root, str) or not self.state_root:
            raise ValueError("state_root must be a nonempty path")
        if (not self.paper_trading
                and os.path.abspath(os.path.expanduser(self.state_root))
                != _default_state_root()):
            raise ValueError(
                "order-enabled safety state must use the canonical root")
        if (isinstance(self.subaccount, bool)
                or not isinstance(self.subaccount, int)
                or not 0 <= self.subaccount <= 32):
            raise ValueError("subaccount must be an integer from 0 through 32")
        for name in ("dip_threshold", "lookback_seconds", "take_profit",
                     "stop_loss", "max_hold_seconds", "contracts_per_trade",
                     "max_daily_loss_usd", "poll_interval", "fill_timeout_s",
                     "cancel_timeout_s", "reconcile_timeout_s",
                     "stale_data_s", "reconcile_every_s",
                     "close_buffer_seconds", "two_model_prior_max_age_s"):
            number(name, positive=True)
        for name in ("sim_latency_s", "sim_slippage_cents", "max_spread",
                     "tp_trail_cents", "espn_cache_s", "espn_min_model_prob",
                     "espn_min_edge", "live_tennis_cache_s",
                     "sibling_spike_cents", "sibling_spike_lookback_s"):
            number(name, nonnegative=True)
        if number("espn_min_model_prob", nonnegative=True) > Decimal(1):
            raise ValueError("espn_min_model_prob must be <= 1")
        if number("espn_min_edge", nonnegative=True) > Decimal(1):
            raise ValueError("espn_min_edge must be <= 1")
        leagues = self.espn_leagues
        if (not isinstance(leagues, tuple)
                or not leagues
                or any(not isinstance(x, str) or not x for x in leagues)):
            raise ValueError("espn_leagues must be a nonempty tuple of strings")
        if not isinstance(self.espn_gate_enabled, bool):
            raise ValueError("espn_gate_enabled must be a bool")
        if (not isinstance(self.two_model_prior_path, str)
                or "\x00" in self.two_model_prior_path
                or (self.two_model_prior_path
                    and self.two_model_prior_path !=
                    self.two_model_prior_path.strip())):
            raise ValueError(
                "two_model_prior_path must be a trimmed filesystem path")
        if not isinstance(self.prefer_scoreboard_bind, bool):
            raise ValueError("prefer_scoreboard_bind must be a bool")
        if not isinstance(self.one_contract_per_event, bool):
            raise ValueError("one_contract_per_event must be a bool")
        if not isinstance(self.sibling_spike_enabled, bool):
            raise ValueError("sibling_spike_enabled must be a bool")
        if self.sibling_spike_enabled and not self.one_contract_per_event:
            raise ValueError(
                "sibling_spike_enabled requires one_contract_per_event")
        if not isinstance(self.live_tennis_enabled, bool):
            raise ValueError("live_tennis_enabled must be a bool")
        if not isinstance(self.live_tennis_include_upcoming, bool):
            raise ValueError("live_tennis_include_upcoming must be a bool")
        if not isinstance(self.live_tennis_api_key, str):
            raise ValueError("live_tennis_api_key must be a string")
        for name in ("live_tennis_tours", "live_tennis_ticker_substrings"):
            values = getattr(self, name)
            if (not isinstance(values, tuple)
                    or not values
                    or any(not isinstance(x, str) or not x for x in values)):
                raise ValueError(
                    f"{name} must be a nonempty tuple of strings")
        for name in ("max_open_positions", "max_monitored_markets",
                     "flatten_retries", "max_consec_errors"):
            positive_int(name)
        if self.max_monitored_markets > 10:
            raise ValueError("max_monitored_markets cannot exceed 10")
        minimum = number("min_price", nonnegative=True)
        maximum = number("max_price", positive=True)
        if not (Decimal(0) < minimum < maximum < Decimal(100)):
            raise ValueError("price bounds must satisfy 0 < min < max < 100")
        slippage = number("sim_slippage_cents", nonnegative=True)
        if slippage >= Decimal(100) or maximum + slippage >= Decimal(100):
            raise ValueError(
                "max_price + sim_slippage_cents must remain below 100")
        take_profit = number("take_profit", positive=True)
        if maximum + take_profit > Decimal(100):
            raise ValueError("max_price + take_profit cannot exceed 100")
        for name in ("dip_threshold", "take_profit", "stop_loss",
                     "max_spread", "tp_trail_cents", "sibling_spike_cents"):
            if number(name, nonnegative=True) > Decimal(100):
                raise ValueError(f"{name} cannot exceed 100 cents")
        contracts = number("contracts_per_trade", positive=True)
        if max(0, -contracts.as_tuple().exponent) > 2:
            raise ValueError(
                "contracts_per_trade supports at most 2 decimal places")
        try:
            precision = Decimal(str(self.balance_precision_usd))
        except (InvalidOperation, ValueError, TypeError) as error:
            raise ValueError("balance_precision_usd must be decimal") from error
        if precision not in (Decimal("0.01"), Decimal("0.0001")):
            raise ValueError(
                "balance_precision_usd must be 0.01 or 0.0001")
        if self.time_in_force != "immediate_or_cancel":
            raise ValueError("paper/live parity requires immediate_or_cancel")
        if self.self_trade_prevention_type not in ("taker_at_cross", "maker"):
            raise ValueError("invalid self_trade_prevention_type")
        def selection_list(name):
            values = getattr(self, name)
            if (not isinstance(values, list)
                    or any(not isinstance(value, str) or not value.strip()
                           for value in values)
                    or len(set(values)) != len(values)):
                raise ValueError(
                    f"{name} must be a list of unique nonempty strings")

        selection_list("tickers")
        selection_list("sports")
        if self._state_identity:
            current = self._expected_state_identity()
            actual = (current[0], current[1], current[2],
                      self.process_lock_path, self.order_journal_path,
                      self.daily_pnl_path)
            if current != self._state_identity or actual != self._state_identity:
                raise ValueError(
                    "state identity or derived safety paths changed after "
                    "construction; create a new Config")
        for name in ("dip_threshold", "take_profit", "stop_loss",
                     "tp_trail_cents",
                     "contracts_per_trade", "max_daily_loss_usd",
                     "min_price", "max_price", "max_spread",
                     "sim_slippage_cents", "sibling_spike_cents"):
            setattr(self, name, Decimal(str(getattr(self, name))))
        for name in ("lookback_seconds", "max_hold_seconds",
                     "sim_latency_s", "poll_interval", "fill_timeout_s",
                     "cancel_timeout_s", "reconcile_timeout_s",
                     "stale_data_s", "reconcile_every_s",
                     "close_buffer_seconds", "espn_cache_s",
                     "espn_min_model_prob", "espn_min_edge",
                     "two_model_prior_max_age_s",
                     "live_tennis_cache_s", "sibling_spike_lookback_s"):
            setattr(self, name, float(getattr(self, name)))
        self.espn_leagues = tuple(self.espn_leagues)
        self.live_tennis_tours = tuple(self.live_tennis_tours)
        self.live_tennis_ticker_substrings = tuple(
            self.live_tennis_ticker_substrings)
        self.live_tennis_api_key = str(self.live_tennis_api_key or "")
        return self
