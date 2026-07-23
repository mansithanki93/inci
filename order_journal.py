"""Durable order journal (fsync'd JSONL). Every order is journaled BEFORE
submission and its outcome recorded after, so a crash/restart can never
lose track of what was sent to the exchange."""
import json
import os
import time


class OrderJournal:
    def __init__(self, path):
        if not os.path.isabs(path):
            raise ValueError("order journal path must be absolute")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path

    def record(self, event, **fields):
        entry = {"ts": time.time(), "event": event, **fields}
        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry

    def load(self):
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def unresolved(self):
        """Orders journaled as submitted/acked with no recorded outcome.
        These MUST be resolved against the exchange before trading."""
        submitted, acked, done = {}, {}, set()
        for e in self.load():
            cid = e.get("client_order_id")
            if e["event"] == "submitted":
                submitted[cid] = e
            elif e["event"] == "acked":
                acked[cid] = e
            elif e["event"] in ("outcome", "submit_failed"):
                done.add(cid)
        out = []
        for cid, e in submitted.items():
            if cid not in done:
                e = dict(e)
                # Preserve every acknowledgement fact needed to reconstruct
                # an authoritative OrderIntent after a restart.
                e.update({k: v for k, v in acked.get(cid, {}).items()
                          if k not in ("event", "ts", "client_order_id")})
                out.append(e)
        return out

    def unapplied_outcomes(self):
        """Filled terminal outcomes not durably applied to local state/P&L."""
        outcomes = {}
        applied = set()
        for entry in self.load():
            order_id = entry.get("order_id")
            if entry.get("event") == "outcome" and order_id:
                try:
                    if float(entry.get("filled", 0)) != 0:
                        outcomes[order_id] = entry
                except (TypeError, ValueError):
                    outcomes[order_id] = entry
            elif entry.get("event") == "applied" and order_id:
                applied.add(order_id)
        return [entry for order_id, entry in outcomes.items()
                if order_id not in applied]
