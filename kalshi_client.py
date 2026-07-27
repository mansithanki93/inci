"""Kalshi V2 REST client. Endpoint split per official V2:
create/cancel on /portfolio/events/orders; poll/list on /portfolio/orders.
All list endpoints paginate via cursor. Every body is schema-validated."""
import base64
import time
from collections import Counter

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

import schemas

MAX_PAGES = 20
GET_429_BACKOFF_SECONDS = (0.25, 0.5, 1.0, 2.0)


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
        self._private_key = None
        self.last_market_skips = {}
        if config.api_key_id and config.private_key_path:
            try:
                with open(config.private_key_path, "rb") as f:
                    self._private_key = serialization.load_pem_private_key(
                        f.read(), password=None)
            except FileNotFoundError:
                pass

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
        if auth and self._private_key is None:
            raise schemas.SchemaError(
                "authenticated request requires a loaded private key")
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

    # ---------- Market data (public) ----------
    def _parse_market_collection(self, rows):
        """Skip only recognized unsupported products in list contexts.

        Direct market reads remain strict. Any malformed binary market or
        unknown product type still aborts the collection as schema drift.
        """
        parsed = []
        skipped = Counter()

        def save_skips():
            self.last_market_skips = dict(sorted(skipped.items()))

        for row in rows:
            try:
                parsed.append(schemas.parse_market(row))
            except schemas.UnsupportedMarketType as error:
                skipped[error.market_type] += 1
                save_skips()
            except schemas.SchemaError as error:
                save_skips()
                raise schemas.SchemaError(
                    f"{error}; {format_market_skips(self.last_market_skips)}"
                ) from error
        save_skips()
        return parsed

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
