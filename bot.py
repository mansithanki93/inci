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
from research_log import ResearchLog, observation_detail, write_startup_halt
from order_journal import OrderJournal
from safety import Safety, Reconciler, ExposureError
from schemas import SchemaError, UnknownOrderState
from engine import (
    Context,
    _gate_snapshot,
    _reprice_gate_snapshot,
    _sibling_snapshot,
    flatten_all,
    process_tick,
)
from pnl_ledger import DailyPnlLedger
from process_lock import ProcessLock, ProcessLockError
from sports_discovery import local_day_window, resolve_series
from two_model_prior import TwoModelPriorStore


@dataclass(frozen=True)
class CliOptions:
    mode: str
    requested_sports: tuple[str, ...]


class CliUsageError(ValueError):
    """A command-line error that callers can print without a traceback."""


class _CliParser(argparse.ArgumentParser):
    def error(self, message):
        raise CliUsageError(f"{self.format_usage().strip()}\nerror: {message}")


def build_score_gate(cfg, *, prior_now=None):
    """Construct the score gate and its optional read-only Models 1+2 bridge."""
    if not cfg.espn_gate_enabled:
        return None
    provider = None
    if cfg.two_model_prior_path:
        provider = TwoModelPriorStore(
            cfg.two_model_prior_path,
            max_age_s=cfg.two_model_prior_max_age_s,
            now=prior_now,
        )
    return EspnProbGate(cfg, prematch_prior_provider=provider)


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
        "skipped_event_siblings", "skipped_unverified_opponent",
        "watch_siblings", "selected",
        "selected_bindable",
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


def run_loop(ctx, reconciler, tickers, sleep=time.sleep, quote_tickers=None):
    """Run monitoring and route every stop reason through safe shutdown.

    ``tickers`` are tradeable markets. ``quote_tickers`` may also include
    watch-only siblings (quoted for spike checks, never process_tick'd).
    """
    safety = ctx.safety
    trade_tickers = tuple(tickers)
    trade_set = set(trade_tickers)
    if quote_tickers is None:
        quote_tickers = trade_tickers
    else:
        quote_tickers = tuple(quote_tickers)

    def critical_tickers():
        critical = set(ctx.strategy.positions)
        if hasattr(ctx.executor, "has_pending"):
            critical.update(t for t in trade_tickers
                            if ctx.executor.has_pending(t))
        return critical

    watch_tickers = tuple(t for t in quote_tickers if t not in trade_set)
    watch_set = set(watch_tickers)

    def related_watch_tickers(ticker):
        if not watch_tickers:
            return (), None
        if not hasattr(ctx.feed, "sibling_tickers"):
            return (), RuntimeError(
                "same-event sibling provenance is unavailable")
        try:
            siblings = set(ctx.feed.sibling_tickers(ticker) or ())
        except Exception as error:
            return (), error
        return tuple(t for t in watch_tickers if t in siblings), None

    def observe(ticker, phase, log_records, *, requote=False):
        if ticker in safety.quarantined:
            return None, False
        try:
            mid, bid, ask, observed_at = ctx.feed.get_quote(ticker)
            safety.ok(ticker)
        except Exception as error:
            if ctx.log and hasattr(ctx.log, "event"):
                prefix = "post-evidence requote: " if requote else ""
                log_records.append((
                    "event", ticker, "api_error", ctx.clock(),
                    f"{prefix}{type(error).__name__}: {error}"))
            was_quarantined = ticker in safety.quarantined
            if ticker in critical_tickers():
                if isinstance(error, MarketUnavailable):
                    kind = ("post-evidence market unavailable" if requote
                            else "market unavailable")
                else:
                    kind = ("post-evidence quote failure" if requote
                            else "quote failure")
                safety.trip(
                    f"{kind} for exposed/pending market {ticker}: {error}")
            elif isinstance(error, MarketUnavailable):
                safety.quarantined.add(ticker)
                print(f"[safety] QUARANTINED {ticker}: {error}")
            else:
                safety.handle_exception(error, ticker)
            if (ctx.log and hasattr(ctx.log, "event")
                    and not was_quarantined
                    and ticker in safety.quarantined):
                log_records.append((
                    "event", ticker, "quarantined", ctx.clock(),
                    str(error)))
            safety.check_staleness(
                ctx.feed, quote_tickers, critical_tickers())
            return None, True

        # Check between network requests; a successful slow request can make
        # another exposed market stale before this package decides anything.
        safety.check_staleness(
            ctx.feed, quote_tickers, critical_tickers())
        if safety.tripped:
            return None, False
        _, bid_qty, _, ask_qty = ctx.feed.top_of_book(ticker)
        close_ts, can_close_early = (
            ctx.feed.lifecycle(ticker)
            if hasattr(ctx.feed, "lifecycle") else (None, None))
        return {
            "ticker": ticker, "mid": mid, "bid": bid, "ask": ask,
            "observed_at": observed_at, "bid_qty": bid_qty,
            "ask_qty": ask_qty, "close_ts": close_ts,
            "can_close_early": can_close_early, "quote_phase": phase,
        }, False

    def persist_package(rows, log_records, sweep_id, decision_at,
                        execution_evidence=None):
        if not ctx.log:
            return
        durable_rows = [
            (record[3], "event", record) for record in log_records]
        durable_rows.extend(
            (row["observed_at"], "quote", row) for row in rows)
        durable_rows.sort(key=lambda item: item[0])
        for _timestamp, record_type, record in durable_rows:
            if record_type == "event":
                _, ticker, event, event_ts, event_detail = record
                ctx.log.event(
                    ticker, event, ts=event_ts, detail=event_detail)
                continue
            row = record
            ticker = row["ticker"]
            role = "trade" if ticker in trade_set else "watch"
            phase = row["quote_phase"]
            if phase == "execution":
                gate, siblings = execution_evidence
                detail = observation_detail(
                    role, sweep_id, quote_phase=phase,
                    decision_at=decision_at, score_gate=gate,
                    siblings=siblings)
            else:
                detail = observation_detail(
                    role, sweep_id, quote_phase=phase,
                    decision_at=decision_at)
            ctx.log.tick(
                ticker, row["mid"], row["bid"], row["ask"],
                row["bid_qty"], row["ask_qty"],
                ts=row["observed_at"], detail=detail,
                close_ts=row["close_ts"],
                can_close_early=row["can_close_early"])

    try:
        while not safety.tripped:
            sweep_had_error = False
            observed_watch = set()
            for ticker in trade_tickers:
                if safety.tripped:
                    break
                if ticker in safety.quarantined:
                    continue
                ctx.sweep_id += 1
                sweep_id = ctx.sweep_id
                log_records = []
                rows = []

                # A decision package contains the trade's evidence quote and
                # all same-event watch evidence, followed by exactly one fresh
                # trade execution quote. It is processed before any unrelated
                # ticker can delay or contaminate the decision.
                related, relation_error = related_watch_tickers(ticker)
                evidence_names = (ticker,) + related
                if relation_error is not None:
                    sweep_had_error = True
                    log_records.append((
                        "event", ticker, "api_error", ctx.clock(),
                        "sibling provenance failure: "
                        f"{type(relation_error).__name__}: "
                        f"{relation_error}"))
                    # Sibling evidence controls entry only. Never quarantine
                    # or halt an exposed ticker before its own fresh quote can
                    # schedule/fill a risk-reducing exit. Flat markets still
                    # use the normal repeated-error quarantine policy.
                    if ticker not in critical_tickers():
                        safety.handle_exception(relation_error, ticker)
                for evidence_ticker in evidence_names:
                    row, had_error = observe(
                        evidence_ticker, "evidence", log_records)
                    sweep_had_error |= had_error
                    if row is not None:
                        rows.append(row)
                        if evidence_ticker in watch_set:
                            observed_watch.add(evidence_ticker)
                    if safety.tripped:
                        break

                trade_evidence = next((
                    row for row in rows
                    if row["ticker"] == ticker
                    and row["quote_phase"] == "evidence"), None)
                raw_gate = None
                execution_row = None
                if trade_evidence is not None and not safety.tripped:
                    raw_gate = _gate_snapshot(
                        ctx, ticker, trade_evidence["ask"], required=True)
                    execution_row, had_error = observe(
                        ticker, "execution", log_records, requote=True)
                    sweep_had_error |= had_error
                    if execution_row is not None:
                        rows.append(execution_row)

                decision_at = ctx.clock()
                if (rows and decision_at < max(
                        row["observed_at"] for row in rows)):
                    safety.trip(
                        "decision clock precedes a package quote timestamp")
                if (execution_row is not None and rows
                        and execution_row["observed_at"] < max(
                            row["observed_at"] for row in rows
                            if row["quote_phase"] == "evidence")):
                    safety.trip("execution quote precedes its evidence phase")

                execution_evidence = None
                # The trade's evidence + requote form the executable package.
                # Missing watch evidence is represented explicitly by an
                # incomplete sibling snapshot: it blocks entries inside the
                # engine but still permits pending/required SELL reductions.
                package_complete = (
                    trade_evidence is not None and execution_row is not None)
                if package_complete:
                    observed_tickers = {row["ticker"] for row in rows}
                    execution_evidence = (
                        _reprice_gate_snapshot(
                            ctx, raw_gate, execution_row["ask"],
                            decision_at=decision_at, ticker=ticker),
                        _sibling_snapshot(
                            ctx, ticker, decision_at,
                            observed_tickers=observed_tickers),
                    )
                # Strict replay packages are atomic. A failed required
                # evidence quote or execution requote leaves only durable gap
                # events and cannot drive the paper engine.
                if not package_complete:
                    history = getattr(ctx.feed, "history", None)
                    if hasattr(history, "get"):
                        for row in reversed(rows):
                            bucket = history.get(row["ticker"])
                            expected = (
                                row["observed_at"], row["mid"])
                            if bucket and bucket[-1] == expected:
                                bucket.pop()
                            else:
                                safety.trip(
                                    "cannot discard incomplete package from "
                                    f"quote history for {row['ticker']}")
                    gap_tickers = {record[1] for record in log_records}
                    for row in rows:
                        observed_ticker = row["ticker"]
                        if observed_ticker in gap_tickers:
                            continue
                        log_records.append((
                            "event", observed_ticker, "package_aborted",
                            decision_at,
                            "successful quote discarded because causal "
                            "decision package was incomplete"))
                        gap_tickers.add(observed_ticker)
                persist_package(
                    rows if package_complete else (), log_records,
                    sweep_id, decision_at,
                    execution_evidence=execution_evidence)

                if package_complete and not safety.tripped:
                    try:
                        gate, siblings = execution_evidence
                        process_tick(
                            ctx, ticker, execution_row["mid"],
                            execution_row["bid"], execution_row["ask"],
                            observed_at=execution_row["observed_at"],
                            decision_at=decision_at, gate_snapshot=gate,
                            sibling_snapshot=siblings,
                            log_observation=False, sweep_id=sweep_id)
                    except (HaltError, UnknownOrderState) as error:
                        safety.trip(str(error))

            # Quote any unassigned watch contract for freshness/telemetry.
            # It cannot influence a trade because no same-event provenance
            # connected it to a decision package.
            for ticker in watch_tickers:
                if safety.tripped:
                    break
                if ticker in observed_watch or ticker in safety.quarantined:
                    continue
                ctx.sweep_id += 1
                sweep_id = ctx.sweep_id
                log_records = []
                row, had_error = observe(
                    ticker, "evidence", log_records)
                sweep_had_error |= had_error
                rows = [] if row is None else [row]
                decision_at = ctx.clock()
                if row is not None and decision_at < row["observed_at"]:
                    safety.trip(
                        "decision clock precedes a watch quote timestamp")
                persist_package(
                    rows, log_records, sweep_id, decision_at)
            if safety.all_quarantined(trade_tickers):
                safety.trip("every monitored market quarantined")
            safety.check_staleness(
                ctx.feed, quote_tickers, critical_tickers())
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

    espn_gate = build_score_gate(cfg)
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
            provenance_by_ticker=discovery.provenance_by_ticker,
            trade_tickers=getattr(
                feed, "trade_tickers",
                tuple(contract.ticker for contract in discovery.contracts)),
            watch_tickers=getattr(
                feed, "watch_tickers",
                tuple(contract.ticker for contract in getattr(
                    discovery, "watch_contracts", ()))),
            score_bindings_by_ticker=getattr(
                feed, "score_bindings_by_ticker", {}))
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
            if espn_gate.prematch_prior_provider is not None:
                print("[models] Models 1+2 prematch bridge enabled "
                      f"(max_age={cfg.two_model_prior_max_age_s:.0f}s)")
            else:
                print("[models] no Models 1+2 prior configured; score "
                      "updates are guard-only and do not claim market edge")
            if cfg.prefer_scoreboard_bind:
                print("[discover] prefer_scoreboard_bind=on "
                      f"(bindable={discovery.stats.get('bindable_candidates', 0)}; "
                      f"selected_bindable="
                      f"{discovery.stats.get('selected_bindable', 0)})")
            if cfg.one_contract_per_event:
                print("[discover] one_contract_per_event=on "
                      f"(skipped_siblings="
                      f"{discovery.stats.get('skipped_event_siblings', 0)})")
            if cfg.sibling_spike_enabled:
                print("[discover] sibling_spike=on "
                      f"(threshold={cfg.sibling_spike_cents}c / "
                      f"{cfg.sibling_spike_lookback_s:.0f}s; "
                      f"watching={discovery.stats.get('watch_siblings', 0)})")
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
        watch_tickers = tuple(getattr(discovery, "watch_tickers", ()) or ())
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
        quote_tickers = tuple(dict.fromkeys(tickers + watch_tickers))
        feed.subscribe(quote_tickers)
    except Exception as error:
        reason = f"session startup failed: {type(error).__name__}: {error}"
        print(f"STARTUP FAILED ({reason})")
        try:
            log.end(clean=False, reason=reason)
        except Exception as log_error:
            print("STARTUP FAILED to persist research terminal: "
                  f"{type(log_error).__name__}: {log_error}")
        return 1

    if watch_tickers:
        print(f"Monitoring {len(tickers)} markets "
              f"(+{len(watch_tickers)} sibling watches). Ctrl-C to stop.\n")
    else:
        print(f"Monitoring {len(tickers)} markets. Ctrl-C to stop.\n")

    with termination_signals_as_interrupt():
        shutdown_ok = run_loop(
            ctx, reconciler, tickers, quote_tickers=quote_tickers)
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
