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
    market_keywords: list = field(default_factory=lambda: [
        "tennis", "ATP", "WTA", "US Open", "Wimbledon",
        "Roland Garros", "Australian Open",
    ])

    # --- Strategy (cents). Defaults sized so TP clears fees near 50c (fix #4):
    # net_take_profit(50, 5) ~= +1.2c; a 2c TP would be net-negative there.
    dip_threshold: int = 7
    lookback_seconds: int = 45
    take_profit: int = 5
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
    sim_latency_s: float = 1.0        # delay between signal and simulated fill
    sim_slippage_cents: int = 1       # adverse price adjustment on every fill
    # Kalshi balance target: ordinary/non-direct accounts use $0.01; direct
    # members use $0.0001. Paper fees include the corresponding rounding fee.
    balance_precision_usd: str = "0.01"

    # --- Execution & safety (fixes #8, #9) ---
    poll_interval: float = 1.5
    fill_timeout_s: float = 4.0       # cancel unfilled remainder after this
    cancel_timeout_s: float = 5.0     # poll-until-terminal after a cancel
    reconcile_timeout_s: float = 5.0  # wait for order/fill/position agreement
    flatten_retries: int = 3          # attempts per position when flattening
    # Official Create V2 enums. IOC prevents a scalp order from becoming a
    # stale resting order; an unfilled remainder is canceled by the exchange.
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
                     "close_buffer_seconds"):
            number(name, positive=True)
        for name in ("sim_latency_s", "sim_slippage_cents", "max_spread"):
            number(name, nonnegative=True)
        for name in ("max_open_positions", "flatten_retries",
                     "max_consec_errors"):
            positive_int(name)
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
                     "max_spread"):
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
        if (not isinstance(self.tickers, list)
                or any(not isinstance(t, str) or not t for t in self.tickers)):
            raise ValueError("tickers must be a list of nonempty strings")
        if (not isinstance(self.market_keywords, list)
                or any(not isinstance(k, str) or not k
                       for k in self.market_keywords)):
            raise ValueError(
                "market_keywords must be a list of nonempty strings")
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
                     "contracts_per_trade", "max_daily_loss_usd",
                     "min_price", "max_price", "max_spread",
                     "sim_slippage_cents"):
            setattr(self, name, Decimal(str(getattr(self, name))))
        for name in ("lookback_seconds", "max_hold_seconds",
                     "sim_latency_s", "poll_interval", "fill_timeout_s",
                     "cancel_timeout_s", "reconcile_timeout_s",
                     "stale_data_s", "reconcile_every_s",
                     "close_buffer_seconds"):
            setattr(self, name, float(getattr(self, name)))
        return self
