"""Edge analyzer. Raw mark-outs are non-executable hypothesis diagnostics;
only replay uses the bot's decision and pending-fill path. Results are split
by event, never by sibling contract.

Usage: python analyze.py logs/ticks_<date>.csv
"""
import csv
import hashlib
import math
import sys
from collections import defaultdict
from decimal import Decimal

from config import Config
from fees import fee_cents
from signals import dip_signal
from replay import replay, validate_logged_book, _utc_day
from research_log import config_fingerprint, code_fingerprint

CFG = Config()


def build_horizons(max_hold_seconds):
    return sorted(set([1, 5, 15, 30, 60, 120, max_hold_seconds]))


HORIZONS = build_horizons(CFG.max_hold_seconds)
MARKOUT_TOLERANCE_S = max(1.0, CFG.poll_interval * 2)


def load(path):
    series = defaultdict(list)
    groups = {}
    session_id = None
    starting_pnl = None
    starting_day = None
    config_id = None
    code_id = None
    terminal_seen = False
    terminal_clean = False
    last_timestamp = None
    expected_config = config_fingerprint(CFG)
    expected_code = code_fingerprint()
    with open(path) as f:
        for row in csv.DictReader(f):
            if (row.get("schema_version") != "5"
                    or not row.get("session_id")
                    or row.get("starting_daily_pnl_usd") in (None, "")
                    or not row.get("starting_utc_day")
                    or not row.get("utc_day")
                    or not row.get("config_fingerprint")
                    or not row.get("code_fingerprint")):
                raise ValueError(
                    "analyzer requires v5 single-session logs with provenance, "
                    "session_id "
                    "and event_id on quotes; legacy/mixed logs are not valid TEST "
                    "evidence")
            try:
                timestamp = float(row["ts"])
                row_start = Decimal(row["starting_daily_pnl_usd"])
            except Exception as error:
                raise ValueError("invalid v5 timestamp/session P&L") from error
            if (not row_start.is_finite() or not math.isfinite(timestamp)
                    or timestamp < 0):
                raise ValueError("non-finite/negative timestamp or starting P&L")
            if row["utc_day"] != _utc_day(timestamp):
                raise ValueError("utc_day disagrees with quote timestamp")
            if row["utc_day"] < row["starting_utc_day"]:
                raise ValueError("quote precedes process-session start day")
            if last_timestamp is not None and timestamp < last_timestamp:
                raise ValueError("non-monotonic observation timestamps")
            last_timestamp = timestamp
            if terminal_seen:
                raise ValueError("row appears after session terminal record")
            if session_id is None:
                session_id = row["session_id"]
                starting_pnl = row_start
                starting_day = row["starting_utc_day"]
                config_id = row["config_fingerprint"]
                code_id = row["code_fingerprint"]
            elif row["session_id"] != session_id:
                raise ValueError("analyzer refuses mixed process sessions")
            elif row_start != starting_pnl:
                raise ValueError("session starting P&L changed within one log")
            elif row["starting_utc_day"] != starting_day:
                raise ValueError("session starting UTC day changed within log")
            elif (row["config_fingerprint"] != config_id
                  or row["code_fingerprint"] != code_id):
                raise ValueError("session provenance changed within log")
            if config_id != expected_config or code_id != expected_code:
                raise ValueError("research config/code fingerprint mismatch")
            event = row.get("event") or "quote"
            if event in ("session_end", "session_halt"):
                if row.get("ticker") or row.get("event_id"):
                    raise ValueError(
                        "session terminal record must not name a market")
                if not row.get("detail"):
                    raise ValueError("session terminal record lacks a reason")
                terminal_seen = True
                terminal_clean = event == "session_end"
                continue
            if event != "quote":
                continue
            if not row.get("event_id"):
                raise ValueError(
                    "quote row lacks verified event_id; ticker-level grouping "
                    "is not valid TEST evidence")
            if not row.get("ticker"):
                raise ValueError("quote row lacks ticker")
            if any(row.get(k) in (None, "")
                   for k in ("close_ts", "can_close_early", "mid", "bid",
                             "ask", "bid_qty", "ask_qty")):
                raise ValueError(
                    "malformed quote row has missing price/depth field")
            try:
                close_ts = float(row["close_ts"])
                if (not math.isfinite(close_ts) or close_ts < 0
                        or row["can_close_early"] not in ("true", "false")):
                    raise ValueError("invalid market lifecycle fields")
                mid, bid, ask, _, _ = validate_logged_book(
                    Decimal(row["mid"]), Decimal(row["bid"]),
                    Decimal(row["ask"]), Decimal(row["bid_qty"]),
                    Decimal(row["ask_qty"]))
            except Exception as error:
                raise ValueError(f"malformed quote row: {error}") from error
            ticker = row["ticker"]
            group = row.get("event_id") or row.get("group_id") or ticker
            if ticker in groups and groups[ticker] != group:
                raise ValueError(f"ticker {ticker} maps to multiple events")
            groups[ticker] = group
            series[ticker].append((timestamp, mid, bid, ask))
    if not terminal_seen:
        raise ValueError(
            "analyzer requires one durable clean session terminal record")
    if not terminal_clean:
        raise ValueError("analyzer refuses a halted research session")
    return series, groups


def split_bucket(group_id, version="event-v1"):
    digest = hashlib.sha256(f"{version}:{group_id}".encode()).digest()
    return "TRAIN" if digest[0] % 2 == 0 else "TEST"


def split_markets(tickers, groups):
    train = {t for t in tickers if split_bucket(groups[t]) == "TRAIN"}
    return train, set(tickers) - train


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
    series, groups = load(path)
    tickers = sorted(series)
    print(f"Loaded {sum(len(v) for v in series.values())} ticks, "
          f"{len(tickers)} markets")
    print(f"Params (config.py): dip={CFG.dip_threshold}c tp={CFG.take_profit}c "
          f"stop={CFG.stop_loss}c hold={CFG.max_hold_seconds}s "
          f"latency={CFG.sim_latency_s}s slip={CFG.sim_slippage_cents}c "
          f"balance_precision=${CFG.balance_precision_usd}\n")
    train, test = split_markets(set(tickers), groups)

    print("MARK-OUTS (NON-EXECUTABLE hypothesis diagnostics; no latency/depth):")
    for name, group in (("TRAIN", train), ("TEST", test)):
        sigs = []
        for tk in group:
            pts = series[tk]
            if len(pts) >= 20:
                sigs += [markouts(pts, i)
                         for i in find_signals(pts, CFG.dip_threshold)]
        report_markouts(name, sigs)

    print("\nFULL REPLAY through one shared portfolio path (P&L attributed by "
          "partition):")
    r = replay(path, cfg=CFG)
    for name, group in (("TRAIN", train), ("TEST", test)):
        sells = [t for t in r["trades"]
                 if t[0] in group and t[1] == "SELL"]
        total = sum((r["per_ticker_total"].get(t, Decimal(0))
                     for t in group), Decimal(0))
        group_residuals = {t: detail for t, detail in r["residuals"].items()
                           if t in group}
        residual_contracts = sum(
            (d["contracts"] for d in group_residuals.values()), Decimal(0))
        residual_marked = sum(
            (d["marked_pnl"] for d in group_residuals.values()), Decimal(0))
        resid = (f" (incl. residual {residual_contracts} contracts "
                 f"marked {residual_marked:+.2f})"
                 if residual_contracts else "")
        if r["evaluable"]:
            result_text = (f"net P&L {total:+.2f} USD "
                           "[RESEARCH-EVALUABLE; ESTIMATED FEES]")
        else:
            reasons = []
            if residual_contracts:
                reasons.append("partition residual inventory")
            elif r["residual_contracts"]:
                reasons.append("portfolio residual inventory")
            if r["pending_orders"]:
                reasons.append("pending orders")
            if r["data_gaps"]:
                reasons.append("data gaps")
            if r["halted"]:
                reasons.append(f"safety halt: {r['halt_reason']}")
            if r["terminal_status"] != "clean":
                reasons.append(
                    f"session terminal={r['terminal_status']}: "
                    f"{r['terminal_reason'] or 'missing reason'}")
            if not r["rows_processed"]:
                reasons.append("no quote rows")
            result_text = (f"P&L NOT REPORTABLE; diagnostic mark "
                           f"{total:+.2f} USD [INCOMPLETE: "
                           f"{', '.join(reasons) or 'unknown'}]")
        print(f"  {name}: {len(sells)} exits across {len(group)} markets, "
              f"{result_text}{resid}")
    print("\nJudge only on TEST. Research-evaluable does not prove live "
          "profitability; current series fees and every README checklist "
          "item still require independent verification.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "logs/ticks.csv")
