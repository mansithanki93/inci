"""Inci's scalp logic. Decisions use EXECUTABLE prices (fix #3):
entries are evaluated at the ask, exits at the bid. Entry is skipped
unless the take-profit clears fees at that entry price (fix #4).
Positions support partial fills (fix #2) and P&L is net of fees.
"""
import time
from dataclasses import dataclass
from decimal import Decimal

from fees import fee_usd, projected_scalp_pnl_usd
from signals import dip_signal_with_reason


@dataclass
class Position:
    ticker: str
    entry_price: Decimal      # actual avg fill (cents)
    contracts: Decimal        # currently held
    opened_at: float
    entry_fee_usd: Decimal


class ScalpStrategy:
    def __init__(self, config, ledger=None, now=None):
        self.cfg = config
        self.ledger = ledger
        self.positions = {}
        self._last_entry_rejection = None
        self._ledger_day = ledger.utc_day(now) if ledger is not None else None
        self.realized_pnl = (ledger.today_total(now) if ledger is not None
                             else Decimal('0'))

    def refresh_daily_pnl(self, now=None):
        if self.ledger is None:
            return self.realized_pnl
        day = self.ledger.utc_day(now)
        if day != self._ledger_day:
            self.realized_pnl = self.ledger.today_total(now)
            self._ledger_day = day
        return self.realized_pnl

    # ---------- Entry: judged at the ASK ----------
    def _evaluate_entry(self, ticker, history, now_ts, mid, bid, ask):
        """Read-only entry evaluation returning (signal, rejection_reason)."""
        self.refresh_daily_pnl(now_ts)
        if mid is None or bid is None or ask is None:
            return None, "missing_quote"
        if ticker in self.positions:
            return None, "position_open"
        if len(self.positions) >= self.cfg.max_open_positions:
            return None, "max_open_positions"
        if not (self.cfg.min_price <= ask <= self.cfg.max_price):
            return None, "entry_price_out_of_range"
        if (ask - bid) > self.cfg.max_spread:
            return None, "spread_too_wide"
        if self.realized_pnl <= -Decimal(str(self.cfg.max_daily_loss_usd)):
            return None, "daily_loss_limit"
        projected = projected_scalp_pnl_usd(
            ask, self.cfg.take_profit, self.cfg.contracts_per_trade,
            self.cfg.sim_slippage_cents,
            self.cfg.balance_precision_usd)
        # Match the aggregate, slipped paper execution rather than a
        # one-contract unslipped approximation.
        if projected <= 0:
            return None, "projected_pnl_nonpositive"
        dip, rejection = dip_signal_with_reason(
            history, now_ts, mid, self.cfg.dip_threshold,
            self.cfg.lookback_seconds)
        if dip is not None:
            return ({"action": "BUY",
                     "reason": f"dip {dip:.1f}c; entry ask {ask}c, "
                               f"projected net ${projected:+.4f}"}, None)
        return None, rejection

    def evaluate_entry(self, ticker, history, now_ts, mid, bid, ask):
        """Expose the built-in decision explanation without dispatch effects."""
        return ScalpStrategy._evaluate_entry(
            self, ticker, history, now_ts, mid, bid, ask)

    def check_entry(self, ticker, history, now_ts, mid, bid, ask):
        """history = (ts, mid) ticks strictly BEFORE this one."""
        decision, rejection = ScalpStrategy._evaluate_entry(
            self, ticker, history, now_ts, mid, bid, ask)
        self._last_entry_rejection = rejection
        return decision

    def last_entry_rejection(self):
        """Return diagnostics captured by the latest public entry check."""
        return self._last_entry_rejection

    # ---------- Exit: judged at the BID ----------
    def check_exit(self, ticker, bid, now=None):
        pos = self.positions.get(ticker)
        if pos is None or bid is None:
            return None
        move = bid - pos.entry_price          # what we'd actually realize
        held = (now if now is not None else time.time()) - pos.opened_at
        if move >= self.cfg.take_profit:
            return {"action": "SELL", "reason": f"take-profit, bid {move:+.0f}c vs entry"}
        if move <= -self.cfg.stop_loss:
            return {"action": "SELL", "reason": f"stop-loss, bid {move:+.0f}c vs entry"}
        if held >= self.cfg.max_hold_seconds:
            return {"action": "SELL", "reason": f"time exit {held:.0f}s ({move:+.0f}c)"}
        return None

    # ---------- Fills (supports partials; P&L net of fees; fix #2, #6) ----------
    def record_fill(self, ticker, side, fill_price, filled, fee, now=None,
                    event_id=None):
        """fill_price = ACTUAL average fill in cents (a gapped stop records
        the real exit, not the configured stop). fee in USD."""
        if filled <= 0:
            return
        self.refresh_daily_pnl(now)
        if side == "BUY":
            pos = self.positions.get(ticker)
            if pos is None:
                self.positions[ticker] = Position(ticker, fill_price, filled,
                                                  now if now is not None else time.time(), fee)
            else:  # average in (shouldn't normally happen, but be safe)
                total = pos.contracts + filled
                pos.entry_price = (pos.entry_price * pos.contracts
                                   + fill_price * filled) / total
                pos.contracts = total
                pos.entry_fee_usd += fee
        else:
            pos = self.positions.get(ticker)
            if pos is None:
                print(f"[warn] SELL fill for {ticker} with no local position")
                return
            closing = min(filled, pos.contracts)
            frac = closing / pos.contracts
            entry_fee_part = pos.entry_fee_usd * frac
            pnl = ((fill_price - pos.entry_price) * closing / Decimal(100)
                   - fee - entry_fee_part)
            self.realized_pnl += pnl
            if self.ledger is not None:
                self.ledger.record(pnl, ts=now, event_id=event_id)
            pos.contracts -= closing
            pos.entry_fee_usd -= entry_fee_part
            state = f"{pos.contracts} still held" if pos.contracts else "flat"
            if pos.contracts == 0:
                del self.positions[ticker]
            print(f"[pnl] {ticker}: closed {closing} @ {fill_price}c "
                  f"net {pnl:+.2f} USD ({state}) | session {self.realized_pnl:+.2f}")
