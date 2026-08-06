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
import argparse
import sys
import time
import signal
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from config import Config
from kalshi_client import KalshiClient, format_market_skips
from market_data import PriceFeed, MarketUnavailable
from strategy import ScalpStrategy
from espn_prob_gate import EspnProbGate
from executor import Executor, HaltError
from research_log import ResearchLog, write_startup_halt
from order_journal import OrderJournal
from safety import Safety, Reconciler, ExposureError
from schemas import SchemaError, UnknownOrderState
from engine import Context, process_tick, flatten_all
from pnl_ledger import DailyPnlLedger
from process_lock import ProcessLock, ProcessLockError
from sports_discovery import local_day_window, resolve_series


@dataclass(frozen=True)
class CliOptions:
    mode: str
    requested_sports: tuple[str, ...]


class CliUsageError(ValueError):
    """A command-line error that callers can print without a traceback."""


class _CliParser(argparse.ArgumentParser):
    def error(self, message):
        raise CliUsageError(f"{self.format_usage().strip()}\nerror: {message}")


def parse_cli(argv) -> CliOptions:
    """Parse operator input without constructing configuration or clients."""
    parser = _CliParser(prog="bot.py")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_const", dest="mode",
                       const="check")
    modes.add_argument("--list-sports", action="store_const", dest="mode",
                       const="list-sports")
    modes.add_argument("--live", action="store_const", dest="mode",
                       const="live")
    modes.add_argument("--demo", action="store_const", dest="mode",
                       const="demo")
    parser.set_defaults(mode="paper")
    parser.add_argument("--sports")
    parsed = parser.parse_args(list(argv))

    requested_sports = ()
    if parsed.sports is not None:
        if parsed.mode != "paper":
            parser.error("--sports is valid only for normal paper mode")
        requested_sports = tuple(part.strip() for part in
                                 parsed.sports.split(","))
        if not all(requested_sports):
            parser.error("--sports cannot contain a blank comma component")
        normalized = [sport.casefold() for sport in requested_sports]
        if len(set(normalized)) != len(normalized):
            parser.error("--sports cannot contain duplicate Sports")
    return CliOptions(mode=parsed.mode, requested_sports=requested_sports)


def _print_supported_sports(client):
    """Temporary Task 1 adapter for Task 2's public filters client method."""
    filters = client.get_sports_filters()
    ordering = (filters.get("sport_ordering", ())
                if isinstance(filters, dict) else filters)
    print("All sports")
    for sport in ordering:
        if sport != "All sports":
            print(sport)


def _utc_text(timestamp):
    return datetime.fromtimestamp(
        timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _number_text(value):
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _sample_page_suffix(metadata):
    cursor = metadata["cursor"]
    if cursor:
        return "more_pages=true; not followed by --check"
    return "more_pages=false"


def _games_capable_sports(filters):
    """Return canonical Sports that advertise exact Games competitions."""
    sports = filters["sports"]
    return tuple(
        sport for sport in filters["sport_ordering"]
        if sport != "All sports"
        and "Games" in sports[sport]["scopes"]
        and any("Games" in scopes
                for scopes in sports[sport]["competitions"].values()))


def _sample_milestone_event_ticker(milestones):
    """Prefer an explicit main game Event, then a sole primary Event."""
    sports_rows = tuple(
        row for row in milestones if row["category"] == "Sports")
    for row in sports_rows:
        if row["main_game_event_ticker"] is not None:
            return row["main_game_event_ticker"]
    for row in sports_rows:
        primary = row["primary_event_tickers"]
        if len(primary) == 1:
            return primary[0]
    return None


def _preflight_sports_metadata(client):
    """Validate bounded public Sports contracts without doing discovery."""
    filters = client.get_sports_filters()
    capable = _games_capable_sports(filters)
    canonical_count = sum(
        sport != "All sports" for sport in filters["sport_ordering"])
    print(
        f"[check] Sports filters OK: {canonical_count} "
        "canonical Sports; Games-capable: "
        + (", ".join(capable) if capable else "none"))

    series_rows = client.get_sports_series()
    print(f"[check] Sports series schema OK: {len(series_rows)} rows")

    if not capable:
        print("[check] WARNING no Games-capable Sport/competition is "
              "currently available")
        return

    sport = capable[0]
    competitions = tuple(sorted(
        name for name, scopes
        in filters["sports"][sport]["competitions"].items()
        if "Games" in scopes))
    competition = competitions[0]
    window = local_day_window()
    milestones, milestone_metadata = client.get_sports_milestones_page(
        competition=competition,
        minimum_start_date=datetime.fromtimestamp(
            window.session_start_utc, timezone.utc))
    print(
        "[check] Sports milestone page OK: "
        f"competition={competition} rows={len(milestones)} "
        f"{_sample_page_suffix(milestone_metadata)}")

    event_ticker = _sample_milestone_event_ticker(milestones)
    if event_ticker is None:
        print("[check] WARNING no usable Sports milestone")
        return

    official_series = tuple(
        row["series_ticker"] for row in series_rows
        if row["category"] == "Sports")
    series_ticker = resolve_series(event_ticker, official_series)
    if series_ticker is None:
        print("[check] WARNING no resolvable official Sports Series")
        return

    events, event_metadata = client.get_open_events_page(
        series_ticker=series_ticker)
    print(
        "[check] Sports event page OK: "
        f"series={series_ticker} events={len(events)} "
        f"{_sample_page_suffix(event_metadata)}")
    print("[check] " + format_market_skips(
        event_metadata["market_skips"]))


def format_discovery_telemetry(discovery):
    """Return deterministic operator telemetry for one discovery result."""
    stats = discovery.stats
    count_keys = (
        "series_rows", "milestone_pages", "milestone_rows",
        "event_pages", "event_rows", "candidates", "bindable_candidates",
        "selected", "selected_bindable",
    )
    skip_items = sorted(
        (key, value) for key, value in stats.items()
        if key.startswith("skip_") and value)
    skip_text = (", ".join(f"{key}={value}" for key, value in skip_items)
                 if skip_items else "none")
    lines = [
        "[discover] Sports=" + ",".join(discovery.selected_sports),
        f"[discover] day timezone={discovery.local_timezone}",
        f"  local=[{discovery.session_start_local}, "
        f"{discovery.session_end_local})",
        f"  utc=[{_utc_text(discovery.session_start_utc)}, "
        f"{_utc_text(discovery.session_end_utc)})",
        "[discover] " + " ".join(
            f"{key}={stats.get(key, 0)}" for key in count_keys),
        f"  skips={skip_text}",
    ]
    for contract in discovery.contracts:
        provenance = contract.provenance
        lines.append(
            f"[discover] {provenance.sport} | "
            f"{provenance.league or 'unknown'} | {contract.game_title} | "
            f"{contract.ticker} | "
            f"{_utc_text(provenance.scheduled_start_ts)} | "
            f"bid={_number_text(contract.bid)} "
            f"ask={_number_text(contract.ask)} "
            f"spread={_number_text(contract.ask - contract.bid)} "
            f"depth=({_number_text(contract.bid_size)},"
            f"{_number_text(contract.ask_size)})")
    return "\n".join(lines)


def _precanonical_failure(reason, requested_sports, requested_tickers):
    print(f"STARTUP FAILED ({reason})")
    try:
        write_startup_halt(
            reason, requested_sports=requested_sports,
            tickers=requested_tickers)
    except Exception as log_error:
        print("STARTUP FAILED to persist operational halt: "
              f"{type(log_error).__name__}: {log_error}")
    return 1


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
    try:
        _preflight_sports_metadata(client)
    except Exception as e:
        print(f"[check] FAIL Sports metadata contract: {e}")
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
            observed = [name for name, rows in samples.items() if rows]
            if empty:
                print(
                    "[check] WARNING row schemas not observed for empty "
                    f"collections {empty}; no production probes were made")
            print(
                "[check] portfolio row schemas observed live: "
                + (", ".join(observed) if observed else "none"))
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
                        ctx.log.event(
                            ticker, "api_error", ts=ctx.clock(),
                            detail=f"{type(e).__name__}: {e}")
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
                        ctx.log.event(
                            ticker, "quarantined", ts=ctx.clock(),
                            detail=str(e))
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
    if cfg.tickers and cfg.sports:
        print("STARTUP FAILED: both configured tickers and Sports were "
              "provided; select exactly one discovery method.")
        return 1
    if not cfg.tickers and not cfg.sports:
        print("STARTUP FAILED: select at least one Sport or configure "
              "explicit tickers before starting a paper session.")
        return 1
    requested_sports = tuple(cfg.sports)
    requested_tickers = tuple(cfg.tickers)
    print("PAPER mode (latency/spread/depth/slippage/fees simulated).")

    espn_gate = (EspnProbGate(cfg) if cfg.espn_gate_enabled else None)
    try:
        feed = PriceFeed(cfg, client)
        discovery = feed.discover(scoreboard_gate=espn_gate)
    except Exception as error:
        reason = f"market discovery failed: {type(error).__name__}: {error}"
        return _precanonical_failure(
            reason, requested_sports, requested_tickers)

    try:
        cfg.sports = list(discovery.selected_sports)
        cfg.validate()
        print(format_discovery_telemetry(discovery))
        session_start = time.time()
        ledger = DailyPnlLedger(cfg.daily_pnl_path)
        strategy = ScalpStrategy(cfg, ledger=ledger, now=session_start)
        log = ResearchLog(
            starting_pnl=strategy.realized_pnl, config=cfg,
            session_start=session_start,
            provenance_by_ticker=discovery.provenance_by_ticker)
    except Exception as error:
        reason = f"canonical startup failed: {type(error).__name__}: {error}"
        return _precanonical_failure(
            reason, requested_sports, requested_tickers)

    try:
        journal = OrderJournal(cfg.order_journal_path)
        executor = Executor(cfg, client, feed, journal=journal)
        safety = Safety(cfg)
        if espn_gate is not None:
            print("[espn] score+prob entry gate enabled "
                  f"(leagues={','.join(cfg.espn_leagues)}; "
                  f"min_p={cfg.espn_min_model_prob}; "
                  f"min_edge={cfg.espn_min_edge})")
            if cfg.prefer_scoreboard_bind:
                print("[discover] prefer_scoreboard_bind=on "
                      f"(bindable={discovery.stats.get('bindable_candidates', 0)}; "
                      f"selected_bindable="
                      f"{discovery.stats.get('selected_bindable', 0)})")
            if cfg.live_tennis_enabled:
                from live_tennis import resolve_api_key
                if espn_gate.live_tennis_cache is not None:
                    print("[live-tennis] ITF secondary feed enabled "
                          f"(tours={','.join(cfg.live_tennis_tours)}; "
                          f"cache_s={cfg.live_tennis_cache_s}; "
                          f"upcoming={cfg.live_tennis_include_upcoming})")
                elif resolve_api_key(cfg):
                    print("[live-tennis] enabled but cache not attached")
                else:
                    print("[live-tennis] enabled but no API key "
                          "(set LIVETENNISAPI_KEY); ITF stays fail-closed")
        ctx = Context(cfg, feed, strategy, executor, log, safety,
                      espn_gate=espn_gate)

        reconciler = None
        if not cfg.paper_trading:
            reconciler = Reconciler(cfg, client, strategy, journal)
            reconciler.startup()

        tickers = discovery.tickers
        if not tickers:
            reason = "no eligible Games contracts for selected Sports"
            try:
                log.end(clean=True, reason=reason)
            except Exception as log_error:
                print("STARTUP FAILED to persist clean research terminal: "
                      f"{type(log_error).__name__}: {log_error}")
                return 1
            print("No eligible Games contracts for selected Sports.")
            return 0
        feed.subscribe(tickers)
    except Exception as error:
        reason = f"session startup failed: {type(error).__name__}: {error}"
        print(f"STARTUP FAILED ({reason})")
        try:
            log.end(clean=False, reason=reason)
        except Exception as log_error:
            print("STARTUP FAILED to persist research terminal: "
                  f"{type(log_error).__name__}: {log_error}")
        return 1

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

    try:
        options = parse_cli(argv)
    except CliUsageError as error:
        print(error)
        return 2

    if options.mode == "live":
        print("Live trading is disabled in this build. See README: the\n"
              "verification checklist (including positive replay results on\n"
              "unseen matches) must pass first; enabling live is then a\n"
              "deliberate code change reviewed on its own.")
        return 2

    if options.mode == "demo":
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
    if options.requested_sports:
        cfg.sports = list(options.requested_sports)
    if options.mode == "list-sports":
        try:
            client = client_factory(cfg)
            _print_supported_sports(client)
            return 0
        except Exception as error:
            print("LIST SPORTS FAILED: "
                  f"{type(error).__name__}: {error}")
            return 1
    client = client_factory(cfg)
    if options.mode == "check":
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
