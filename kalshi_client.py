"""Kalshi V2 REST client. Endpoint split per official V2:
create/cancel on /portfolio/events/orders; poll/list on /portfolio/orders.
All list endpoints paginate via cursor. Every body is schema-validated."""
import base64
import time
from collections import Counter
from datetime import datetime, timezone

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

import schemas

MAX_PAGES = 20
GET_429_BACKOFF_SECONDS = (0.25, 0.5, 1.0, 2.0)
PUBLIC_METADATA_MIN_INTERVAL_SECONDS = 0.05


def _diagnostic(value, limit=240):
    rendered = repr(value)
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit - 3] + "..."


def format_market_skips(skips):
    """Stable, loud summary for discovery and preflight output."""
    counts = {str(name): int(count) for name, count in (skips or {}).items()
              if int(count) > 0}
    total = sum(counts.values())
    types = (", ".join(f"{name}={counts[name]}" for name in sorted(counts))
             if counts else "none")
    return f"unsupported skipped: total={total} types={types}"


class KalshiClient:
    def __init__(self, config):
        self.cfg = config
        self.base = config.api_base
        self.session = requests.Session()
        self._sleep = time.sleep
        self._monotonic = time.monotonic
        self._last_public_metadata_at = None
        self._private_key = None
        self.last_market_skips = {}
        self.last_market_scan = None
        self.last_sports_market_skips = {}

    def _ensure_private_key(self):
        """Load credentials only when an authenticated request needs them."""
        if self._private_key is not None:
            return
        if not self.cfg.api_key_id:
            raise schemas.SchemaError(
                "authenticated request requires KALSHI_API_KEY_ID")
        if not self.cfg.private_key_path:
            raise schemas.SchemaError(
                "authenticated request requires KALSHI_PRIVATE_KEY_PATH")
        try:
            with open(self.cfg.private_key_path, "rb") as handle:
                self._private_key = serialization.load_pem_private_key(
                    handle.read(), password=None)
        except Exception as error:
            raise schemas.SchemaError(
                "authenticated request could not load private key from "
                f"{self.cfg.private_key_path!r}: "
                f"{type(error).__name__}: {error}") from error

    def _sign(self, method, path):
        ts = str(int(time.time() * 1000))
        msg = (ts + method + path.split("?")[0]).encode()
        sig = self._private_key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=hashes.SHA256().digest_size),
            hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.cfg.api_key_id,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
                "KALSHI-ACCESS-TIMESTAMP": ts}

    def _request(self, method, endpoint, params=None, body=None, auth=False):
        path = "/trade-api/v2" + endpoint
        if auth:
            self._ensure_private_key()
        response = None
        for attempt in range(len(GET_429_BACKOFF_SECONDS) + 1):
            # Authentication timestamps/signatures must be fresh after a
            # backoff; recompute them for every attempt.
            headers = self._sign(method, path) if auth else {}
            response = self.session.request(
                method, self.base + endpoint, params=params,
                json=body, headers=headers, timeout=10)
            if (method == "GET"
                    and getattr(response, "status_code", None) == 429
                    and attempt < len(GET_429_BACKOFF_SECONDS)):
                delay = GET_429_BACKOFF_SECONDS[attempt]
                print(f"[rate-limit] GET {endpoint} throttled; "
                      f"retrying in {delay:.2f}s")
                self._sleep(delay)
                continue
            break
        r = response
        r.raise_for_status()
        try:
            payload = r.json()
        except Exception as e:
            raise schemas.SchemaError(
                f"{endpoint}: response was not valid JSON, raw="
                f"{_diagnostic(getattr(r, 'text', None))}") from e
        if not isinstance(payload, dict):
            raise schemas.SchemaError(
                f"{endpoint}: expected response object, got "
                f"{_diagnostic(payload)}")
        return payload

    def _paginate(self, endpoint, list_key, params=None, auth=False,
                  cursor_required=True, required_lists=()):
        """Follow cursors and fail closed on malformed or truncated data."""
        params = dict(params or {})
        items = []
        seen_cursors = set()
        for _ in range(MAX_PAGES):
            resp = self._request("GET", endpoint, params=params, auth=auth)
            if not isinstance(resp, dict):
                raise schemas.SchemaError(
                    f"{endpoint}: expected response object, got "
                    f"{_diagnostic(resp)}")
            if list_key not in resp or not isinstance(resp[list_key], list):
                raise schemas.SchemaError(
                    f"{endpoint}: missing/invalid collection '{list_key}', "
                    f"got {_diagnostic(resp.get(list_key, '<missing>'))}")
            for required in required_lists:
                if (required not in resp
                        or not isinstance(resp[required], list)):
                    raise schemas.SchemaError(
                        f"{endpoint}: missing/invalid collection "
                        f"'{required}', got "
                        f"{_diagnostic(resp.get(required, '<missing>'))}")
            items.extend(resp[list_key])
            if "cursor" not in resp:
                if cursor_required:
                    raise schemas.SchemaError(
                        f"{endpoint}: missing required pagination cursor")
                return items
            cursor = resp["cursor"]
            if not isinstance(cursor, str):
                raise schemas.SchemaError(
                    f"{endpoint}: cursor must be a string, got "
                    f"{_diagnostic(cursor)}")
            if cursor == "":
                return items
            if cursor in seen_cursors:
                raise schemas.SchemaError(
                    f"{endpoint}: repeated pagination cursor {cursor!r}")
            seen_cursors.add(cursor)
            params["cursor"] = cursor
        raise schemas.SchemaError(
            f"{endpoint}: pagination exceeded {MAX_PAGES} pages")

    def _paginate_public_inventory(self, endpoint, params, parse_page):
        """Exhaust a public metadata inventory; never return partial rows."""
        query = dict(params)
        rows = []
        skips = Counter()
        seen_cursors = set()
        raw_row_count = 0
        for page_number in range(1, MAX_PAGES + 1):
            response = self._request_public_metadata(
                endpoint, params=query)
            page_rows, cursor = parse_page(response)
            rows.extend(page_rows)
            raw_row_count += len(page_rows)
            for row in page_rows:
                for market_type, count in row.get("market_skips", {}).items():
                    skips[market_type] += count
            if cursor == "":
                metadata = {"pages": page_number, "rows": raw_row_count,
                            "raw_rows": raw_row_count,
                            "market_skips": dict(sorted(skips.items()))}
                return tuple(rows), metadata
            if cursor in seen_cursors:
                raise schemas.SchemaError(
                    f"{endpoint}: repeated pagination cursor {cursor!r}")
            seen_cursors.add(cursor)
            query["cursor"] = cursor
        raise schemas.SchemaError(
            f"{endpoint}: pagination exceeded {MAX_PAGES} pages")

    def _request_public_metadata(self, endpoint, *, params=None):
        """Pace successful public discovery GETs before they reach 429s."""
        clock = getattr(self, "_monotonic", time.monotonic)
        sleeper = getattr(self, "_sleep", time.sleep)
        last_request = getattr(self, "_last_public_metadata_at", None)
        if last_request is not None:
            remaining = (
                PUBLIC_METADATA_MIN_INTERVAL_SECONDS
                - (clock() - last_request))
            if remaining > 0:
                sleeper(remaining)
        response = self._request("GET", endpoint, params=params)
        self._last_public_metadata_at = clock()
        return response

    @staticmethod
    def _rfc3339_utc(value):
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise schemas.SchemaError(
                    "minimum_start_date: invalid RFC3339 timestamp "
                    f"{value!r}") from error
        else:
            raise schemas.SchemaError(
                "minimum_start_date: expected RFC3339 string or datetime, got "
                f"{_diagnostic(value)}")
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise schemas.SchemaError(
                "minimum_start_date: timestamp must include timezone, got "
                f"{_diagnostic(value)}")
        return parsed.astimezone(timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")

    # ---------- Market data (public) ----------
    def get_sports_filters(self):
        return schemas.parse_sports_filters_response(
            self._request_public_metadata("/search/filters_by_sport"))

    def get_sports_series(self):
        return schemas.parse_series_list_response(
            self._request_public_metadata(
                "/series", params={"category": "Sports"}))

    def get_sports_milestones_page(
            self, *, competition, minimum_start_date):
        """Validate exactly one public Sports milestone page.

        This bounded method exists for ``--check``.  Discovery uses the
        exhaustive sibling below and must never substitute this sample for a
        complete inventory.
        """
        if not isinstance(competition, str) or not competition:
            raise schemas.SchemaError(
                "sports milestones competition: expected nonempty string, "
                f"got {_diagnostic(competition)}")
        response = self._request_public_metadata("/milestones", params={
            "category": "Sports",
            "minimum_start_date": KalshiClient._rfc3339_utc(
                minimum_start_date),
            "competition": competition,
            "limit": 500,
        })
        rows, cursor = schemas.parse_milestones_page(response)
        return rows, {
            "pages": 1,
            "rows": len(rows),
            "raw_rows": len(rows),
            "cursor": cursor,
        }

    def get_open_events_page(self, *, series_ticker):
        """Validate exactly one public nested-Event page for ``--check``."""
        if not isinstance(series_ticker, str) or not series_ticker:
            raise schemas.SchemaError(
                "series_ticker: expected nonempty string, got "
                f"{_diagnostic(series_ticker)}")
        response = self._request_public_metadata("/events", params={
            "series_ticker": series_ticker,
            "status": "open",
            "with_nested_markets": "true",
            "limit": 200,
        })
        rows, cursor = schemas.parse_events_page(response)
        skips = Counter()
        for row in rows:
            for market_type, count in row["market_skips"].items():
                skips[market_type] += count
        market_skips = dict(sorted(skips.items()))
        self.last_sports_market_skips = market_skips
        return rows, {
            "pages": 1,
            "rows": len(rows),
            "raw_rows": len(rows),
            "cursor": cursor,
            "market_skips": market_skips,
        }

    def get_sports_milestones(self, *, minimum_start_date, competition=None,
                               related_event_ticker=None):
        has_competition = competition is not None
        has_related_ticker = related_event_ticker is not None
        if has_competition == has_related_ticker:
            raise schemas.SchemaError(
                "sports milestones require exactly one of competition or "
                "related_event_ticker")
        selection_name, selection_value = (
            ("competition", competition) if has_competition else
            ("related_event_ticker", related_event_ticker))
        if not isinstance(selection_value, str) or not selection_value:
            raise schemas.SchemaError(
                f"sports milestones {selection_name}: expected nonempty string, "
                f"got {_diagnostic(selection_value)}")
        rows, metadata = self._paginate_public_inventory(
            "/milestones", {
                "category": "Sports",
                "minimum_start_date": self._rfc3339_utc(minimum_start_date),
                selection_name: selection_value,
                "limit": 500,
            }, schemas.parse_milestones_page)
        return rows, metadata

    def get_open_events(self, *, series_ticker):
        if not isinstance(series_ticker, str) or not series_ticker:
            raise schemas.SchemaError("series_ticker: expected nonempty string, "
                                     f"got {_diagnostic(series_ticker)}")
        rows, metadata = self._paginate_public_inventory(
            "/events", {
                "series_ticker": series_ticker,
                "status": "open",
                "with_nested_markets": "true",
                "limit": 200,
            }, schemas.parse_events_page)
        self.last_sports_market_skips = metadata["market_skips"]
        return rows, metadata

    def get_event(self, event_ticker, *, with_nested_markets=True):
        if not isinstance(event_ticker, str) or not event_ticker:
            raise schemas.SchemaError("event_ticker: expected nonempty string, "
                                     f"got {_diagnostic(event_ticker)}")
        if not isinstance(with_nested_markets, bool):
            raise schemas.SchemaError(
                "with_nested_markets: expected boolean, got "
                f"{_diagnostic(with_nested_markets)}")
        event = schemas.parse_event_response(self._request_public_metadata(
            f"/events/{event_ticker}", params={
                "with_nested_markets": "true" if with_nested_markets else "false"}))
        self.last_sports_market_skips = event["market_skips"]
        return event

    def _parse_market_rows(self, rows, skipped):
        """Skip only recognized unsupported products in list contexts.

        Direct market reads remain strict. Any malformed binary market or
        unknown product type still aborts the collection as schema drift.
        """
        parsed = []

        for row in rows:
            try:
                parsed.append(schemas.parse_market(row))
            except schemas.UnsupportedMarketType as error:
                skipped[error.market_type] += 1
                self.last_market_skips = dict(sorted(skipped.items()))
            except schemas.SchemaError as error:
                self.last_market_skips = dict(sorted(skipped.items()))
                raise schemas.SchemaError(
                    f"{error}; {format_market_skips(self.last_market_skips)}"
                ) from error
        return parsed

    def _parse_market_collection(self, rows):
        skipped = Counter()
        parsed = self._parse_market_rows(rows, skipped)
        self.last_market_skips = dict(sorted(skipped.items()))
        return parsed

    def _save_market_scan(self, *, pages, rows, selected, truncated,
                          complete, stop_reason):
        metadata = {
            "pages": pages,
            "rows": rows,
            "selected": selected,
            "truncated": truncated,
            "complete": complete,
            "stop_reason": stop_reason,
        }
        self.last_market_scan = metadata
        return metadata

    def scan_markets(self, predicate, max_results, **params):
        """Bounded market discovery, distinct from exhaustive pagination.

        Discovery is allowed to stop once it has the requested number of
        usable markets. It remains strict about every envelope, page row, and
        cursor it actually observes. A nonempty cursor after ``MAX_PAGES`` is
        reported as an incomplete scan rather than treated as a portfolio
        pagination failure.
        """
        if (isinstance(max_results, bool) or not isinstance(max_results, int)
                or max_results <= 0):
            raise schemas.SchemaError("market discovery max_results must be positive")
        if not callable(predicate):
            raise schemas.SchemaError("market discovery predicate must be callable")

        query = dict(params)
        selected = []
        skipped = Counter()
        seen_cursors = set()
        row_count = 0
        self.last_market_skips = {}
        self.last_market_scan = None

        for page_number in range(1, MAX_PAGES + 1):
            response = self._request("GET", "/markets", params=query)
            if not isinstance(response, dict):
                raise schemas.SchemaError(
                    "/markets: expected response object, got "
                    f"{_diagnostic(response)}")
            if "markets" not in response or not isinstance(
                    response["markets"], list):
                raise schemas.SchemaError(
                    "/markets: missing/invalid collection 'markets', got "
                    f"{_diagnostic(response.get('markets', '<missing>'))}")
            if "cursor" not in response:
                raise schemas.SchemaError(
                    "/markets: missing required pagination cursor")
            cursor = response["cursor"]
            if not isinstance(cursor, str):
                raise schemas.SchemaError(
                    "/markets: cursor must be a string, got "
                    f"{_diagnostic(cursor)}")
            if cursor and cursor in seen_cursors:
                raise schemas.SchemaError(
                    f"/markets: repeated pagination cursor {cursor!r}")
            if cursor:
                seen_cursors.add(cursor)

            parsed = self._parse_market_rows(response["markets"], skipped)
            self.last_market_skips = dict(sorted(skipped.items()))
            row_count += len(response["markets"])
            for market in parsed:
                if predicate(market):
                    selected.append(market)
                    if len(selected) == max_results:
                        complete = cursor == ""
                        return selected, self._save_market_scan(
                            pages=page_number, rows=row_count,
                            selected=len(selected), truncated=not complete,
                            complete=complete,
                            stop_reason=("end" if complete else "selected_cap"))

            if cursor == "":
                return selected, self._save_market_scan(
                    pages=page_number, rows=row_count, selected=len(selected),
                    truncated=False, complete=True, stop_reason="end")
            query["cursor"] = cursor

        # Discovery intentionally returns a useful partial candidate set here.
        # Generic portfolio pagination stays fail-closed in ``_paginate``.
        return selected, self._save_market_scan(
            pages=MAX_PAGES, rows=row_count, selected=len(selected),
            truncated=True, complete=False, stop_reason="page_cap")

    def get_markets(self, **params):
        return self._parse_market_collection(
            self._paginate("/markets", "markets", params))

    def get_markets_sample(self, limit=25, **params):
        """Fetch exactly one bounded page for diagnostics, not inventory."""
        if isinstance(limit, bool) or not isinstance(limit, int) \
                or not 1 <= limit <= 1000:
            raise schemas.SchemaError("market sample limit must be 1..1000")
        query = dict(params)
        query["limit"] = limit
        response = self._request("GET", "/markets", params=query)
        if ("markets" not in response
                or not isinstance(response["markets"], list)):
            raise schemas.SchemaError(
                "/markets: missing/invalid collection 'markets', got "
                f"{_diagnostic(response.get('markets', '<missing>'))}")
        if "cursor" not in response or not isinstance(response["cursor"], str):
            raise schemas.SchemaError(
                "/markets: missing/invalid cursor, got "
                f"{_diagnostic(response.get('cursor', '<missing>'))}")
        return self._parse_market_collection(response["markets"][:limit])

    def get_market(self, ticker):
        resp = self._request("GET", f"/markets/{ticker}")
        return schemas.parse_market_response(resp)

    def get_market_for_discovery(self, ticker):
        """Direct Market proof with discovery pacing, not quote-loop pacing."""
        resp = self._request_public_metadata(f"/markets/{ticker}")
        return schemas.parse_market_response(resp)

    def get_orderbook(self, ticker):
        resp = self._request("GET", f"/markets/{ticker}/orderbook")
        return schemas.parse_orderbook_response(resp)

    def get_exchange_status(self):
        return schemas.parse_exchange_status(
            self._request("GET", "/exchange/status"))

    # ---------- Orders: create/cancel on EVENTS endpoint only ----------
    def create_order(self, body):
        resp = self._request("POST", schemas.CREATE_CANCEL_ENDPOINT,
                             body=body, auth=True)
        return schemas.parse_create_ack(resp)      # ack has NO status

    def cancel_order(self, order_id):
        response = self._request(
            "DELETE", f"{schemas.CREATE_CANCEL_ENDPOINT}/{order_id}",
            params={"subaccount": self.cfg.subaccount}, auth=True)
        ack = schemas.parse_cancel_ack(response)
        if ack["order_id"] != order_id:
            raise schemas.SchemaError(
                f"cancel ack order mismatch: {ack['order_id']} != {order_id}")
        return ack

    # ---------- Orders: poll/list on ORDERS endpoint only ----------
    def get_order(self, order_id):
        resp = self._request("GET", f"{schemas.ORDERS_ENDPOINT}/{order_id}",
                             auth=True)
        order = schemas.parse_order_response(resp)
        if (order["subaccount_number"] is not None
                and order["subaccount_number"] != self.cfg.subaccount):
            raise schemas.SchemaError(
                f"order {order_id}: wrong subaccount "
                f"{order['subaccount_number']}")
        return order

    def get_open_orders(self):
        # Current V2 has exactly one nonterminal state: resting. Querying it
        # avoids paginating terminal history and scopes the result explicitly.
        params = {"status": "resting", "subaccount": self.cfg.subaccount}
        raw = self._paginate(schemas.ORDERS_ENDPOINT, "orders", params,
                             auth=True)
        parsed = [schemas.parse_order(o) for o in raw]
        for order in parsed:
            if (order["status"] != "resting"
                    or (order["subaccount_number"] is not None
                        and order["subaccount_number"]
                        != self.cfg.subaccount)):
                raise schemas.SchemaError(
                    f"resting-order query returned wrong scope/state: {order}")
        return parsed

    # ---------- Portfolio ----------
    def get_balance(self):
        return schemas.parse_balance(self._request(
            "GET", "/portfolio/balance",
            params={"subaccount": self.cfg.subaccount}, auth=True))

    def get_fills(self, order_id=None):
        params = {"subaccount": self.cfg.subaccount}
        if order_id:
            params["order_id"] = order_id
        raw = self._paginate("/portfolio/fills", "fills", params, auth=True)
        parsed = [schemas.parse_fill(f) for f in raw]
        for fill in parsed:
            if (fill["subaccount_number"] is not None
                    and fill["subaccount_number"] != self.cfg.subaccount):
                raise schemas.SchemaError(
                    f"fill {fill['fill_id']}: wrong subaccount")
        return parsed

    def get_positions(self):
        raw = self._paginate("/portfolio/positions", "market_positions",
                             {"subaccount": self.cfg.subaccount}, auth=True,
                             cursor_required=False,
                             required_lists=("event_positions",))
        parsed = [schemas.parse_position(p) for p in raw]
        return [p for p in parsed if p["position"] != 0]
