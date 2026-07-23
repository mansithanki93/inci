"""Safety halts (per-market quarantine + global) and STRICT reconciliation."""
import time
from collections import defaultdict
from decimal import Decimal

from order_resolution import OrderIntent, OrderResolver, ResolutionError


class ExposureError(Exception):
    pass


class Safety:
    def __init__(self, config):
        self.cfg = config
        self.consec_errors = 0
        self.per_ticker = defaultdict(int)
        self.quarantined = set()
        self.tripped_reason = None

    def ok(self, ticker=None):
        if ticker:
            self.per_ticker[ticker] = 0

    def global_ok(self):
        """Reset global failures only after a complete healthy sweep."""
        self.consec_errors = 0

    def handle_exception(self, error, ticker=None):
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status in (401, 403):
            self.trip(f"authentication/authorization failure (HTTP {status})")
            return
        if status == 429:
            self.trip("exchange rate limit reached (HTTP 429)")
            return
        if type(error).__name__ in ("SchemaError", "UnknownOrderState"):
            self.trip(f"exchange contract failure: {error}")
            return
        self.error(error, ticker=ticker)

    def error(self, what, ticker=None):
        """Per-market failures quarantine that market instead of killing
        the whole bot; global/unattributed failures still trip the halt."""
        if ticker is not None:
            self.per_ticker[ticker] += 1
            print(f"[safety] {ticker} error "
                  f"({self.per_ticker[ticker]}/{self.cfg.max_consec_errors}):"
                  f" {what}")
            if (self.per_ticker[ticker] >= self.cfg.max_consec_errors
                    and ticker not in self.quarantined):
                self.quarantined.add(ticker)
                print(f"[safety] QUARANTINED {ticker} "
                      f"(persistent errors); other markets continue")
            return
        self.consec_errors += 1
        print(f"[safety] API error ({self.consec_errors}/"
              f"{self.cfg.max_consec_errors}): {what}")
        if self.consec_errors >= self.cfg.max_consec_errors:
            self.trip(f"{self.consec_errors} consecutive API errors")

    def all_quarantined(self, tickers):
        return bool(tickers) and set(tickers) <= self.quarantined

    def check_staleness(self, feed, tickers, critical_tickers=()):
        critical = set(critical_tickers)
        hidden = critical & self.quarantined
        if hidden:
            self.trip(
                f"exposed/pending markets are quarantined: {sorted(hidden)}")
            return
        active = [t for t in tickers if t not in self.quarantined]
        stale = feed.stale_tickers(active)
        exposed = [ticker for ticker in stale if ticker in critical]
        if exposed:
            self.trip(f"stale quotes for exposed/pending markets "
                      f"(> {self.cfg.stale_data_s}s): {exposed}")
            return
        for ticker in stale:
            if ticker not in self.quarantined:
                self.quarantined.add(ticker)
                print(f"[safety] QUARANTINED {ticker} (stale/unquoted)")
        if self.all_quarantined(tickers):
            self.trip("every monitored market unavailable/stale")

    def trip(self, reason):
        if not self.tripped_reason:
            self.tripped_reason = reason
            print(f"\n[safety] HALT: {reason}")

    @property
    def tripped(self):
        return self.tripped_reason is not None


class Reconciler:
    def __init__(self, config, client, strategy, journal,
                 clock=time.time, sleep=time.sleep):
        self.cfg = config
        self.client = client
        self.strategy = strategy
        self.journal = journal
        self.clock = clock
        self.sleep = sleep
        self.resolver = OrderResolver(client, config, clock, sleep)
        self.last = 0.0

    def _intent(self, entry):
        required = ("order_id", "client_order_id", "ticker", "side",
                    "count", "pre_position", "ack_fill", "ack_remaining")
        missing = [k for k in required if entry.get(k) is None]
        if missing:
            raise ExposureError(
                f"journal entry {entry.get('client_order_id')} lacks "
                f"restart evidence {missing}; resolve manually")
        try:
            return OrderIntent(
                entry["order_id"], entry["client_order_id"],
                entry["ticker"], entry["side"], Decimal(str(entry["count"])),
                Decimal(str(entry["pre_position"])),
                Decimal(str(entry["ack_fill"])),
                Decimal(str(entry["ack_remaining"])))
        except Exception as e:
            raise ExposureError(
                f"invalid journal evidence for "
                f"{entry.get('client_order_id')}: {e}") from e

    def _resolve_journaled(self, entry):
        intent = self._intent(entry)
        try:
            order = self.resolver.order(intent.order_id,
                                         intent.client_order_id)
            order = self.resolver.cancel_to_terminal(
                intent.order_id, self.cfg.cancel_timeout_s,
                intent.client_order_id, initial=order)
            result = self.resolver.resolve(intent, initial=order)
        except ResolutionError as e:
            raise ExposureError(str(e)) from e
        self.journal.record(
            "outcome", client_order_id=intent.client_order_id,
            order_id=intent.order_id, ticker=intent.ticker, side=intent.side,
            status=result.order["status"],
            filled=str(result.filled), avg_price=str(result.avg_price),
            remaining=str(result.order["remaining_count"]),
            canceled=str(result.order["initial_count"] - result.filled
                         - result.order["remaining_count"]),
            fee=str(result.fee), api_position=str(result.post_position),
            effective_ts=self.clock(),
            recovered_on_startup=True)
        if result.filled:
            raise ExposureError(
                f"journaled order {intent.order_id} filled {result.filled} "
                f"while Inci was down; account must be verified and flattened")
        print(f"[reconcile] resolved stale order "
              f"{intent.order_id[:8]} (verified zero fill)")

    def _cancel_stray(self, order):
        oid = order["order_id"]
        try:
            terminal = self.resolver.cancel_to_terminal(
                oid, self.cfg.cancel_timeout_s, initial=order)
            first = self.resolver.fills(oid)[1]
            self.sleep(0.25)
            second = self.resolver.fills(oid)[1]
        except ResolutionError as e:
            raise ExposureError(str(e)) from e
        if first or second or terminal["fill_count"]:
            raise ExposureError(
                f"stray order {oid} filled during startup/shutdown "
                f"(order={terminal['fill_count']}, fills={first}/{second})")
        print(f"[reconcile] canceled stray nonterminal order {oid[:8]}")

    def _drain_orders(self):
        """Attempt every safely identifiable cleanup before reporting errors."""
        errors = []
        for entry in list(self.journal.unresolved()):
            try:
                self._resolve_journaled(entry)
            except ExposureError as e:
                errors.append(f"journal {entry.get('client_order_id')}: {e}")
        try:
            orders = self.client.get_open_orders()
        except Exception as e:
            errors.append(f"order listing failed: {e}")
            orders = []
        for order in orders:
            try:
                self._cancel_stray(order)
            except ExposureError as e:
                errors.append(f"order {order.get('order_id')}: {e}")
        try:
            remaining = self.client.get_open_orders()
            if remaining:
                errors.append("still open: "
                              + ",".join(o["order_id"] for o in remaining))
        except Exception as e:
            errors.append(f"final order listing failed: {e}")
        unresolved = self.journal.unresolved()
        if unresolved:
            errors.append("unresolved journal: " + ",".join(
                str(e.get("client_order_id")) for e in unresolved))
        unapplied = self.journal.unapplied_outcomes()
        if unapplied:
            errors.append("filled outcome not durably applied: " + ",".join(
                str(e.get("order_id")) for e in unapplied))
        return errors

    def _positions(self):
        try:
            rows = self.client.get_positions()
        except Exception as e:
            raise ExposureError(f"position reconciliation failed: {e}") from e
        out = {}
        for row in rows:
            if row["ticker"] in out:
                raise ExposureError(
                    f"duplicate position rows for {row['ticker']}")
            out[row["ticker"]] = row["position"]
        return out

    def _require_flat_stable(self):
        first = self._positions()
        self.sleep(0.25)
        second = self._positions()
        if first or second:
            positions = second or first
            raise ExposureError(
                "account holds positions Inci cannot safely assume: "
                + ", ".join(f"{t}={q}" for t, q in positions.items()))

    def startup(self):
        print("[reconcile] strict startup check...")
        errors = self._drain_orders()
        try:
            self._require_flat_stable()
        except ExposureError as e:
            errors.append(str(e))
        if errors:
            raise ExposureError("startup reconciliation failed: "
                                + "; ".join(errors))
        print("[reconcile] startup OK: no unresolved orders, flat account")

    def periodic(self, safety):
        if self.clock() - self.last < self.cfg.reconcile_every_s:
            return
        self.last = self.clock()
        try:
            errors = self._drain_orders()
            if errors:
                safety.trip("reconciliation failed: " + "; ".join(errors))
                return
            api = self._positions()
        except Exception as e:
            safety.trip(f"reconciliation fetch failed: {e}")
            return
        local = {t: p.contracts for t, p in self.strategy.positions.items()}
        for t in set(api) | set(local):
            if api.get(t, 0) != local.get(t, 0):
                safety.trip(f"position mismatch {t}: "
                            f"Kalshi={api.get(t, 0)} local={local.get(t, 0)}")
                return

    def shutdown(self):
        errors = self._drain_orders()
        if errors:
            raise ExposureError("shutdown reconciliation failed: "
                                + "; ".join(errors))

    def verify_flat(self):
        if self.journal.unresolved():
            raise ExposureError("cannot verify flat: unresolved journal")
        if self.journal.unapplied_outcomes():
            raise ExposureError("cannot verify flat: unapplied filled outcome")
        if self.client.get_open_orders():
            raise ExposureError("cannot verify flat: nonterminal orders remain")
        self._require_flat_stable()
