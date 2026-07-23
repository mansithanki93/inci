"""Inci (pronounced "in-see") — Kalshi in-game scalping bot.

Usage:
    python bot.py            # paper mode (default)
    python bot.py --check    # preflight: validates live V2 responses
                             # against schemas.py contract validators
    python bot.py --demo     # refuses: demo order flow is disabled

LIVE TRADING IS DISABLED IN THIS BUILD. There is no flag, config field,
or environment variable that enables it. It stays disabled until the
README verification checklist — including positive net replay results
on unseen matches from your own logs — is satisfied, at which point
enabling it is a deliberate code change, not a switch.
"""
import sys
import time
import signal
from contextlib import contextmanager

from config import Config
from kalshi_client import KalshiClient, format_market_skips
from market_data import PriceFeed, MarketUnavailable
from strategy import ScalpStrategy
from executor import Executor, HaltError
from research_log import ResearchLog
from order_journal import OrderJournal
from safety import Safety, Reconciler, ExposureError
from schemas import SchemaError, UnknownOrderState
from engine import Context, process_tick, flatten_all
from pnl_ledger import DailyPnlLedger
from process_lock import ProcessLock, ProcessLockError


@contextmanager
def termination_signals_as_interrupt():
    """Route process-manager termination through run_loop's safe shutdown.

    The first SIGTERM/SIGHUP becomes KeyboardInterrupt; later termination
    signals are ignored until reconciliation/terminal logging completes.
    """
    previous = {}
    handled = [False]

    def handler(_signum, _frame):
        if handled[0]:
            return
        handled[0] = True
        raise KeyboardInterrupt()

    try:
        for name in ("SIGTERM", "SIGHUP"):
            number = getattr(signal, name, None)
            if number is None:
                continue
            previous[number] = signal.getsignal(number)
            signal.signal(number, handler)
        yield
    finally:
        for number, old_handler in previous.items():
            signal.signal(number, old_handler)


def preflight(cfg, client):
    """Hit the real API; every response passes through the SAME schema
    validators the bot uses, so contract drift fails here, not mid-trade."""
    print(f"[check] target: {cfg.api_base}")
    ok = True
    try:
        print(f"[check] exchange status: {client.get_exchange_status()}")
    except Exception as e:
        print(f"[check] FAIL /exchange/status: {e}")
        ok = False
    try:
        mkts = client.get_markets_sample(
            status="open", limit=25, mve_filter="exclude")
        print("[check] " + format_market_skips(
            getattr(client, "last_market_skips", {})))
        if not mkts:
            raise SchemaError("no supported markets returned")
        quoted = [m for m in mkts
                  if (m["yes_bid"] is not None
                      and m["yes_ask"] is not None
                      and m["yes_bid_size"] > 0
                      and m["yes_ask_size"] > 0)]
        print(f"[check] market schema OK "
              f"({len(quoted)}/{len(mkts)} sampled markets have quotes)")
        if not quoted:
            raise SchemaError(
                "no quoted markets in a 25-market sample — schema may have "
                "drifted again; inspect raw fields")
        q = quoted[0]
        if not (0 < q["yes_bid"] <= q["yes_ask"] <= 100):
            raise SchemaError(
                f"quote sanity failed: bid={q['yes_bid']} "
                f"ask={q['yes_ask']}")
        print(f"[check] quote sanity OK: {q['ticker']} "
              f"bid={q['yes_bid']:.1f}c ask={q['yes_ask']:.1f}c "
              f"depth=({q['yes_bid_size']},{q['yes_ask_size']})")
    except (SchemaError, Exception) as e:
        print(f"[check] FAIL market data contract: {e}")
        ok = False
    if cfg.api_key_id:
        try:
            print(f"[check] auth OK, balance: {client.get_balance()}")
            samples = {
                "orders": client.get_open_orders(),
                "fills": client.get_fills(),
                "positions": client.get_positions(),
            }
            empty = [name for name, rows in samples.items() if not rows]
            print("[check] authenticated portfolio envelopes OK; row samples: "
                  + ", ".join(f"{name}={len(rows)}"
                              for name, rows in samples.items()))
            if empty:
                raise SchemaError(
                    "portfolio row schemas not exercised for empty "
                    f"collections {empty}; preflight coverage is incomplete")
            print("[check] portfolio row schemas OK (orders/fills/positions)")
        except (SchemaError, Exception) as e:
            print(f"[check] FAIL portfolio contract: {e}")
            ok = False
    else:
        print("[check] FAIL no API key — authenticated portfolio contracts "
              "were not checked")
        ok = False
    print("[check] " + ("ALL PASSED" if ok else "FAILURES — do not trade"))
    return ok


def safe_shutdown(ctx, reconciler):
    """Cancel/verify orders, then flatten only from an unambiguous state."""
    if ctx.cfg.paper_trading:
        canceled = (ctx.executor.cancel_pending_paper()
                    if hasattr(ctx.executor, "cancel_pending_paper") else [])
        if ctx.strategy.positions:
            print("[shutdown] paper positions retained as residual; no stale "
                  "book was reused to fabricate exits")
        if canceled:
            print(f"[shutdown] canceled {len(canceled)} pending paper order(s)")
        return True
    errors = []
    if reconciler:
        try:
            reconciler.shutdown()
        except Exception as e:
            errors.append(f"order shutdown: {e}")
    should_flatten = (not ctx.cfg.paper_trading) or bool(ctx.strategy.positions)
    if should_flatten and not errors:
        try:
            flatten_all(ctx)
        except Exception as e:
            errors.append(f"flatten: {e}")
    if reconciler and not errors:
        try:
            reconciler.verify_flat()
        except Exception as e:
            errors.append(f"final flat verification: {e}")
    if errors:
        print("[shutdown] FAILED: " + "; ".join(errors))
        return False
    print("[shutdown] verified: no ambiguous orders or exposure")
    return True


def run_loop(ctx, reconciler, tickers, sleep=time.sleep):
    """Run monitoring and route every stop reason through safe shutdown."""
    safety = ctx.safety

    def critical_tickers():
        critical = set(ctx.strategy.positions)
        if hasattr(ctx.executor, "has_pending"):
            critical.update(t for t in tickers
                            if ctx.executor.has_pending(t))
        return critical

    try:
        while not safety.tripped:
            sweep_had_error = False
            for ticker in tickers:
                if ticker in safety.quarantined:
                    continue
                try:
                    mid, bid, ask, observed_at = ctx.feed.get_quote(ticker)
                    safety.ok(ticker)
                except Exception as e:
                    sweep_had_error = True
                    if ctx.log and hasattr(ctx.log, "event"):
                        group = (ctx.feed.group_id(ticker)
                                 if hasattr(ctx.feed, "group_id") else ticker)
                        ctx.log.event(ticker, "api_error", ts=ctx.clock(),
                                      group_id=group)
                    was_quarantined = ticker in safety.quarantined
                    if isinstance(e, MarketUnavailable):
                        if ticker in critical_tickers():
                            safety.trip(
                                f"market unavailable with exposure/pending "
                                f"order: {ticker}: {e}")
                        else:
                            safety.quarantined.add(ticker)
                            print(f"[safety] QUARANTINED {ticker}: {e}")
                    else:
                        if ticker in critical_tickers():
                            safety.trip(
                                f"quote failure for exposed/pending market "
                                f"{ticker}: {e}")
                        else:
                            safety.handle_exception(e, ticker)
                    if (ctx.log and hasattr(ctx.log, "event")
                            and not was_quarantined
                            and ticker in safety.quarantined):
                        ctx.log.event(ticker, "quarantined", ts=ctx.clock(),
                                      group_id=group)
                    safety.check_staleness(
                        ctx.feed, tickers, critical_tickers())
                    if safety.tripped:
                        break
                    continue
                # A slow request for this market may have made another
                # market stale. Check before this quote can trigger an order
                # or the sweep can block on another request.
                safety.check_staleness(ctx.feed, tickers, critical_tickers())
                if safety.tripped:
                    break
                try:
                    process_tick(ctx, ticker, mid, bid, ask,
                                 observed_at=observed_at)
                    if safety.tripped:
                        break
                except (HaltError, UnknownOrderState) as e:
                    safety.trip(str(e))
                    break
            if safety.all_quarantined(tickers):
                safety.trip("every monitored market quarantined")
            safety.check_staleness(ctx.feed, tickers, critical_tickers())
            if reconciler and not safety.tripped:
                reconciler.periodic(safety)
            if not sweep_had_error and not safety.tripped:
                safety.global_ok()
            if not safety.tripped:
                sleep(ctx.cfg.poll_interval)
    except KeyboardInterrupt:
        safety.trip("operator interrupt")
    except Exception as e:
        safety.trip(f"unhandled runtime error: {type(e).__name__}: {e}")
    shutdown_ok = safe_shutdown(ctx, reconciler)
    clean_operator_stop = safety.tripped_reason == "operator interrupt"
    if ctx.log and hasattr(ctx.log, "end"):
        try:
            ctx.log.end(
                clean=bool(shutdown_ok and clean_operator_stop),
                reason=safety.tripped_reason or "runtime ended")
        except Exception as error:
            print(f"[shutdown] FAILED to persist research terminal: {error}")
            shutdown_ok = False
    return shutdown_ok and clean_operator_stop


def run_session(cfg, client):
    try:
        cfg.validate()
    except ValueError as error:
        print(f"STARTUP FAILED (invalid configuration): {error}")
        return 1
    if not cfg.paper_trading:
        print("REAL ORDER SESSION DISABLED: configuration cannot unlock it.")
        return 1
    print("PAPER mode (latency/spread/depth/slippage/fees simulated).")
    session_start = time.time()
    feed = PriceFeed(cfg, client)
    ledger = DailyPnlLedger(cfg.daily_pnl_path)
    strategy = ScalpStrategy(cfg, ledger=ledger, now=session_start)
    journal = OrderJournal(cfg.order_journal_path)
    executor = Executor(cfg, client, feed, journal=journal)
    log = ResearchLog(starting_pnl=strategy.realized_pnl, config=cfg,
                      session_start=session_start)
    safety = Safety(cfg)
    ctx = Context(cfg, feed, strategy, executor, log, safety)

    reconciler = None
    if not cfg.paper_trading:
        reconciler = Reconciler(cfg, client, strategy, journal)
        try:
            reconciler.startup()
        except Exception as e:
            print(f"\nSTARTUP FAILED (refusing to trade): {e}")
            return 1

    tickers = feed.discover_tickers()
    if not tickers:
        print(f"No open markets matched {cfg.market_keywords}.")
        log.end(clean=True, reason="no matching markets")
        return 0
    feed.subscribe(tickers)
    print(f"Monitoring {len(tickers)} markets. Ctrl-C to stop.\n")

    with termination_signals_as_interrupt():
        shutdown_ok = run_loop(ctx, reconciler, tickers)
    print(f"\nUTC-day P&L (net of fees): {strategy.realized_pnl:+.2f} USD "
          f"| open positions: {len(strategy.positions)}")
    for t, p in strategy.positions.items():
        print(f"  OPEN: {t} x{p.contracts} @ {p.entry_price}c — "
              "close manually if on demo!")
    return 0 if shutdown_ok else 2


def main(argv=None, client_factory=KalshiClient, config_factory=Config):
    argv = sys.argv[1:] if argv is None else list(argv)
    print("=== Inci v6.0 — Kalshi paper-research scalper ===")

    if "--live" in argv:
        print("Live trading is disabled in this build. See README: the\n"
              "verification checklist (including positive replay results on\n"
              "unseen matches) must pass first; enabling live is then a\n"
              "deliberate code change reviewed on its own.")
        return 2

    if "--demo" in argv:
        print("Demo order flow is disabled in this build, alongside live.\n"
              "Unlock order (demo) mode only after: (1) authenticated\n"
              "`--check` passes against production portfolio schemas, and\n"
              "(2) the order-contract tests are reviewed against Kalshi's\n"
              "official docs. Enabling it is then a deliberate code change.\n"
              "Schemas are never verified by probing with real orders.")
        return 2
    cfg = config_factory()
    try:
        cfg.validate()
    except ValueError as error:
        print(f"STARTUP FAILED (invalid configuration): {error}")
        return 1
    client = client_factory(cfg)
    if "--check" in argv:
        return 0 if preflight(cfg, client) else 1
    try:
        with ProcessLock(cfg.process_lock_path):
            exit_code = run_session(cfg, client)
    except ProcessLockError as e:
        print(f"STARTUP FAILED (refusing to run): {e}")
        exit_code = 1
    if exit_code:
        return exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
