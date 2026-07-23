"""Durable UTC-day realized P&L ledger for restart-safe loss limits."""
import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal


class DailyPnlLedger:
    def __init__(self, path, clock=time.time):
        if not os.path.isabs(path):
            raise ValueError("daily P&L ledger path must be absolute")
        self.path = path
        self.clock = clock
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def _day(self, ts=None):
        value = self.clock() if ts is None else ts
        return datetime.fromtimestamp(value, timezone.utc).date().isoformat()

    def utc_day(self, ts=None):
        return self._day(ts)

    def _entries(self):
        if not os.path.exists(self.path):
            return []
        entries = []
        seen = {}
        with open(self.path) as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    event_id = entry["event_id"]
                    if not isinstance(event_id, str) or not event_id:
                        raise ValueError("invalid event_id")
                    value = Decimal(entry["pnl_usd"])
                    if not value.is_finite():
                        raise ValueError("non-finite P&L")
                    timestamp = float(entry["effective_ts"])
                    if not math.isfinite(timestamp) or timestamp < 0:
                        raise ValueError("invalid effective_ts")
                    utc_day = entry["utc_day"]
                    if (not isinstance(utc_day, str)
                            or utc_day != self._day(timestamp)):
                        raise ValueError(
                            "utc_day does not match effective_ts")
                    payload = (utc_day, value, timestamp)
                    if event_id in seen and seen[event_id] != payload:
                        raise ValueError(
                            f"conflicting duplicate event_id {event_id}")
                    if event_id in seen:
                        continue
                    seen[event_id] = payload
                    entries.append((event_id, *payload))
                except Exception as e:
                    raise ValueError(
                        f"invalid P&L ledger line {line_number}: {e}") from e
        return entries

    def record_once(self, event_id, pnl, ts=None):
        timestamp = self.clock() if ts is None else ts
        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("effective_ts must be finite and nonnegative") \
                from error
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("effective_ts must be finite and nonnegative")
        value = Decimal(str(pnl))
        if not value.is_finite():
            raise ValueError("P&L must be finite")
        payload = (self._day(timestamp), value, timestamp)
        for seen_id, day, seen_value, seen_ts in self._entries():
            if seen_id != event_id:
                continue
            if (day, seen_value, seen_ts) != payload:
                raise ValueError(
                    f"conflicting duplicate P&L event_id {event_id}")
            return False
        entry = {"event_id": event_id, "effective_ts": timestamp,
                 "utc_day": payload[0], "pnl_usd": str(value)}
        with open(self.path, "a") as handle:
            handle.write(json.dumps(entry) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True

    def record(self, pnl, ts=None, event_id=None):
        return self.record_once(event_id or uuid.uuid4().hex, pnl, ts=ts)

    def today_total(self, ts=None):
        day = self._day(ts)
        total = Decimal(0)
        for _, entry_day, value, _ in self._entries():
            if entry_day == day:
                total += value
        return total
