"""Edge analyzer. Raw mark-outs are non-executable hypothesis diagnostics;
only replay uses the bot's decision and pending-fill path. Results are split
by event, never by sibling contract.

Usage: python analyze.py logs/ticks_<date>.csv
"""
import hashlib
import sys
from collections import defaultdict
from decimal import Decimal

from config import Config
from fees import fee_cents
from signals import dip_signal
from replay import load_log, replay

CFG = Config()
SUPPORTED_LABEL = (
    "SUPPORTED HYPOTHESIS: TEST is evaluable and net P&L > 0"
)
NOT_SUPPORTED_LABEL = (
    "NOT SUPPORTED: TEST <= 0, empty, or not evaluable"
)


def build_horizons(max_hold_seconds):
    return sorted(set([1, 5, 15, 30, 60, 120, max_hold_seconds]))


HORIZONS = build_horizons(CFG.max_hold_seconds)
MARKOUT_TOLERANCE_S = max(1.0, CFG.poll_interval * 2)


def load(path):
    rows, data_gaps, _, _, terminal_status, terminal_reason, \
        selected_sports, provenance_by_ticker = load_log(
            path, include_metadata=True, cfg=CFG)
    if terminal_status != "clean":
        if terminal_status == "halted":
            raise ValueError(
                "analyzer refuses a halted research session: "
                f"{terminal_reason or 'missing reason'}")
        raise ValueError(
            "analyzer requires one durable clean session terminal record")
    if data_gaps:
        raise ValueError(
            f"analyzer refuses {data_gaps} research data gap(s)")
    series = defaultdict(list)
    for timestamp, ticker, mid, bid, ask, _, _, _, _ in rows:
        series[ticker].append((timestamp, mid, bid, ask))
    groups = {
        ticker: provenance.event_ticker
        for ticker, provenance in provenance_by_ticker.items()
    }
    return (
        series, groups, selected_sports, provenance_by_ticker)


def split_bucket(group_id, version="event-v1"):
    digest = hashlib.sha256(f"{version}:{group_id}".encode()).digest()
    return "TRAIN" if digest[0] % 2 == 0 else "TEST"


def split_markets(tickers, groups):
    train = {t for t in tickers if split_bucket(groups[t]) == "TRAIN"}
    return train, set(tickers) - train


def validate_replay_metadata(selected_sports, provenance, result):
    """Require the loader and one full replay to describe one session."""
    try:
        replay_sports = tuple(result["selected_sports"])
        replay_provenance = dict(result["market_provenance"])
    except (KeyError, TypeError) as error:
        raise ValueError(
            "replay result lacks selected-Sports/provenance metadata") \
            from error
    if replay_sports != tuple(selected_sports):
        raise ValueError(
            "load/replay selected-Sports metadata mismatch")
    if replay_provenance != dict(provenance):
        raise ValueError(
            "load/replay market provenance metadata mismatch")


def build_partitions(series, groups, selected_sports, provenance):
    """Build deterministic Event-level partitions without touching rows."""
    selected_sports = tuple(selected_sports)
    if (not selected_sports
            or len(set(selected_sports)) != len(selected_sports)
            or any(not isinstance(sport, str) or not sport
                   for sport in selected_sports)):
        raise ValueError(
            "analysis requires unique nonempty selected Sports")
    tickers = set(series)
    if tickers != set(groups) or tickers != set(provenance):
        raise ValueError(
            "analysis requires complete matching series/group/provenance "
            "ticker sets")

    bucket_by_ticker = {}
    for ticker in tickers:
        item = provenance[ticker]
        event_ticker = getattr(item, "event_ticker", None)
        sport = getattr(item, "sport", None)
        if not event_ticker or groups[ticker] != event_ticker:
            raise ValueError(
                f"inconsistent Event provenance for {ticker!r}")
        if sport not in selected_sports:
            raise ValueError(
                f"missing/unselected Sport provenance for {ticker!r}")
        bucket_by_ticker[ticker] = split_bucket(event_ticker)

    overall = {
        bucket: tuple(sorted(
            ticker for ticker in tickers
            if bucket_by_ticker[ticker] == bucket))
        for bucket in ("TRAIN", "TEST")
    }
    sports = {}
    for sport in selected_sports:
        sport_tickers = {
            ticker for ticker in tickers
            if provenance[ticker].sport == sport
        }
        sports[sport] = {
            bucket: tuple(sorted(
                ticker for ticker in sport_tickers
                if bucket_by_ticker[ticker] == bucket))
            for bucket in ("TRAIN", "TEST")
        }
    return {
        "selected_sports": selected_sports,
        "bucket_by_ticker": bucket_by_ticker,
        "overall": overall,
        "sports": sports,
    }


def summarize_partition(tickers, result):
    """Attribute one partition from the already-computed shared replay."""
    tickers = tuple(sorted(tickers))
    selected = frozenset(tickers)
    exits = sum(
        ticker in selected and side == "SELL"
        for ticker, side, _, _ in result["trades"])
    net_pnl = sum(
        (result["per_ticker_total"].get(ticker, Decimal(0))
         for ticker in tickers),
        Decimal(0))
    residuals = {
        ticker: result["residuals"][ticker]
        for ticker in tickers
        if ticker in result["residuals"]
    }
    residual_contracts = sum(
        (detail["contracts"] for detail in residuals.values()),
        Decimal(0))
    residual_marked = sum(
        (detail["marked_pnl"] for detail in residuals.values()),
        Decimal(0))
    return {
        "tickers": tickers,
        "markets": len(tickers),
        "exits": exits,
        "net_pnl": net_pnl,
        "residuals": residuals,
        "residual_contracts": residual_contracts,
        "residual_marked": residual_marked,
    }


def _replay_is_evaluable(result):
    """Fail closed if the replay flag contradicts concrete incomplete state."""
    try:
        return bool(
            result["evaluable"]
            and not result["residuals"]
            and Decimal(str(result["residual_contracts"])) == 0
            and result["pending_orders"] == 0
            and result["data_gaps"] == 0
            and not result["halted"]
            and result["terminal_status"] == "clean"
            and result["rows_processed"] > 0
            and result["rows_processed"] == result["rows_available"])
    except (KeyError, TypeError, ValueError):
        return False


def _incomplete_reasons(summary, result):
    reasons = []
    if summary["residual_contracts"]:
        reasons.append("partition residual inventory")
    elif result.get("residuals") or result.get("residual_contracts"):
        reasons.append("portfolio residual inventory")
    if result.get("pending_orders"):
        reasons.append("pending orders")
    if result.get("halted"):
        reasons.append(
            f"safety halt: {result.get('halt_reason') or 'missing reason'}")
    if result.get("terminal_status") != "clean":
        reasons.append(
            f"session terminal={result.get('terminal_status', 'missing')}: "
            f"{result.get('terminal_reason') or 'missing reason'}")
    if result.get("data_gaps"):
        reasons.append("data gaps")
    processed = result.get("rows_processed", 0)
    available = result.get("rows_available", 0)
    if processed != available:
        reasons.append(f"unprocessed rows {processed}/{available}")
    if not processed:
        reasons.append("no quote rows")
    if not reasons:
        reasons.append("replay marked non-evaluable")
    return reasons


def _format_partition(bucket, tickers, result):
    summary = summarize_partition(tickers, result)
    if _replay_is_evaluable(result):
        status = (
            f"net P&L {summary['net_pnl']:+.2f} USD "
            "[RESEARCH-EVALUABLE; ESTIMATED FEES]")
    else:
        status = (
            f"P&L NOT REPORTABLE; diagnostic mark "
            f"{summary['net_pnl']:+.2f} USD [INCOMPLETE: "
            f"{', '.join(_incomplete_reasons(summary, result))}]")
    residual = (
        f" (incl. residual {summary['residual_contracts']} contracts "
        f"marked {summary['residual_marked']:+.2f})"
        if summary["residual_contracts"] else "")
    return (
        f"  {bucket}: {summary['markets']} markets, "
        f"{summary['exits']} exits, {status}{residual}")


def format_replay_report(partitions, result):
    """Format deterministic overall/per-Sport shared replay attribution."""
    lines = [
        "FULL REPLAY through one shared portfolio path:",
        "",
        "OVERALL",
        _format_partition("TRAIN", partitions["overall"]["TRAIN"], result),
        _format_partition("TEST", partitions["overall"]["TEST"], result),
    ]
    globally_evaluable = _replay_is_evaluable(result)
    for sport in partitions["selected_sports"]:
        sport_partitions = partitions["sports"][sport]
        test_summary = summarize_partition(
            sport_partitions["TEST"], result)
        supported = bool(
            test_summary["tickers"]
            and globally_evaluable
            and test_summary["net_pnl"] > Decimal(0))
        lines.extend([
            "",
            f"SPORT: {sport}",
            _format_partition("TRAIN", sport_partitions["TRAIN"], result),
            _format_partition("TEST", sport_partitions["TEST"], result),
            "  Held-out: "
            + (SUPPORTED_LABEL if supported else NOT_SUPPORTED_LABEL),
        ])
    return "\n".join(lines)


def find_signals(points, dip):
    out, last = [], -1e9
    for i, (t, mid, bid, ask) in enumerate(points):
        if t - last < CFG.max_hold_seconds:
            continue
        hist = [(tt, m) for (tt, m, _, _) in points[max(0, i - 300):i]]
        if dip_signal(hist, t, mid, dip, CFG.lookback_seconds) is not None:
            if (ask - bid) <= CFG.max_spread and CFG.min_price <= ask <= CFG.max_price:
                out.append(i)
                last = t
    return out


def markouts(points, i):
    ea = points[i][3] + CFG.sim_slippage_cents
    t0 = points[i][0]
    out, j = {}, i + 1
    for h in HORIZONS:
        while j < len(points) and points[j][0] < t0 + h:
            j += 1
        if j >= len(points):
            continue
        if points[j][0] - (t0 + h) > MARKOUT_TOLERANCE_S:
            continue
        eb = points[j][2] - CFG.sim_slippage_cents
        out[h] = ((eb - ea)
                  - fee_cents(ea, side="BUY",
                              balance_precision_usd=CFG.balance_precision_usd)
                  - fee_cents(max(Decimal(0), eb), side="SELL",
                              balance_precision_usd=CFG.balance_precision_usd))
    return out


def report_markouts(name, sigs):
    if not sigs:
        print(f"  {name}: no signals")
        return
    print(f"  {name}: {len(sigs)} signals")
    print("    horizon:  " + "".join(f"{h:>8}s" for h in HORIZONS))
    available = [[m[h] for m in sigs if h in m] for h in HORIZONS]
    avg = [(sum(v) / len(v)) if v else None for v in available]
    win = [(100 * sum(1 for x in v if x > 0) / len(v)) if v else None
           for v in available]
    print("    samples:  " + "".join(f"{len(v):>8}" for v in available))
    print("    diagnostic avg: " + "".join(
        f"{a:>8.2f}c" if a is not None else f"{'n/a':>9}"
        for a in avg))
    print("    win %:    " + "".join(
        f"{w:>8.1f} " if w is not None else f"{'n/a':>9}"
        for w in win))


def main(path):
    series, groups, selected_sports, provenance = load(path)
    partitions = build_partitions(
        series, groups, selected_sports, provenance)
    tickers = sorted(series)
    print(f"Loaded {sum(len(v) for v in series.values())} ticks, "
          f"{len(tickers)} markets")
    print(f"Params (config.py): dip={CFG.dip_threshold}c tp={CFG.take_profit}c "
          f"stop={CFG.stop_loss}c hold={CFG.max_hold_seconds}s "
          f"latency={CFG.sim_latency_s}s slip={CFG.sim_slippage_cents}c "
          f"balance_precision=${CFG.balance_precision_usd}\n")
    train = partitions["overall"]["TRAIN"]
    test = partitions["overall"]["TEST"]

    print("MARK-OUTS (NON-EXECUTABLE hypothesis diagnostics; no latency/depth):")
    for name, group in (("TRAIN", train), ("TEST", test)):
        sigs = []
        for tk in group:
            pts = series[tk]
            if len(pts) >= 20:
                sigs += [markouts(pts, i)
                         for i in find_signals(pts, CFG.dip_threshold)]
        report_markouts(name, sigs)

    r = replay(path, cfg=CFG)
    validate_replay_metadata(selected_sports, provenance, r)
    print("\n" + format_replay_report(partitions, r))
    print("\nJudge only on TEST. Research-evaluable does not prove live "
          "profitability; current series fees and every README checklist "
          "item still require independent verification.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "logs/ticks.csv")
