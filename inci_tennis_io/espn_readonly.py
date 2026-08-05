"""Read-only transport for the public ESPN tennis scoreboard.

The endpoint is unauthenticated, so this module never reads, holds, or sends a
credential. Only GET is issued, redirects are refused, the body is size
capped, and every response is returned with the paired observation clock the
normalizer needs.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Callable, Final

import requests


_ORIGIN: Final[str] = "https://site.api.espn.com"
_PATH_TEMPLATE: Final[str] = "/apis/site/v2/sports/tennis/{tour}/scoreboard"
_TOURS: Final[frozenset[str]] = frozenset({"atp", "wta"})
_MAXIMUM_BODY_BYTES: Final[int] = 8_388_608
_CONNECT_TIMEOUT_SECONDS: Final[int] = 5
_READ_TIMEOUT_SECONDS: Final[int] = 15
_DEFAULT_CLOCK_UNCERTAINTY_NS: Final[int] = 1_000_000_000


class EspnReadOnlyError(RuntimeError):
    """Raised when the public scoreboard cannot be read safely."""


@dataclass(frozen=True, slots=True)
class EspnCapture:
    """One verified scoreboard body with its paired observation clock."""

    tour: str
    document: dict
    source_wall_ns: int
    received_monotonic_ns: int
    clock_uncertainty_ns: int


def _fail(code: str) -> None:
    raise EspnReadOnlyError(code)


def _header(headers: object, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    if value is None:
        return None
    if type(value) is not str:
        _fail("espn_header_invalid")
    return value


class EspnScoreboardTransport:
    """Single-owner read-only reader for the public ESPN scoreboard."""

    def __init__(
        self,
        *,
        wall_ns: Callable[[], int] = time.time_ns,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        clock_uncertainty_ns: int = _DEFAULT_CLOCK_UNCERTAINTY_NS,
    ) -> None:
        if type(clock_uncertainty_ns) is not int or clock_uncertainty_ns < 0:
            _fail("espn_clock_uncertainty_invalid")
        self._wall_ns = wall_ns
        self._monotonic_ns = monotonic_ns
        self._clock_uncertainty_ns = clock_uncertainty_ns
        self._session = requests.Session()
        self._closed = False

    def __repr__(self) -> str:
        return "EspnScoreboardTransport(read_only=True)"

    def close(self) -> None:
        self._closed = True
        try:
            self._session.close()
        except Exception:
            _fail("espn_transport_close_failed")

    def fetch_scoreboard(self, tour: str) -> EspnCapture:
        """GET one tour scoreboard and return its parsed document."""
        if self._closed:
            _fail("espn_transport_closed")
        if type(tour) is not str or tour not in _TOURS:
            _fail("espn_tour_invalid")

        received_monotonic_ns = self._monotonic_ns()
        if type(received_monotonic_ns) is not int or received_monotonic_ns < 0:
            _fail("espn_clock_invalid")

        response = None
        try:
            response = self._session.get(
                _ORIGIN + _PATH_TEMPLATE.format(tour=tour),
                headers={
                    "accept": "application/json",
                    "accept-encoding": "identity",
                },
                allow_redirects=False,
                stream=True,
                timeout=(_CONNECT_TIMEOUT_SECONDS, _READ_TIMEOUT_SECONDS),
            )
            status = response.status_code
            if type(status) is not int or status != 200:
                _fail("espn_http_status_not_ok")
            content_type = _header(response.headers, "Content-Type")
            if (
                content_type is None
                or content_type.split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                _fail("espn_content_type_invalid")
            content_length = _header(response.headers, "Content-Length")
            if content_length is not None:
                if (
                    not content_length.isascii()
                    or not content_length.isdecimal()
                    or int(content_length) > _MAXIMUM_BODY_BYTES
                ):
                    _fail("espn_body_too_large")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=65_536):
                if type(chunk) is not bytes:
                    _fail("espn_response_body_invalid")
                size += len(chunk)
                if size > _MAXIMUM_BODY_BYTES:
                    _fail("espn_body_too_large")
                chunks.append(chunk)
            body = b"".join(chunks)
        except EspnReadOnlyError:
            raise
        except Exception:
            _fail("espn_request_failed")
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

        source_wall_ns = self._wall_ns()
        if type(source_wall_ns) is not int or source_wall_ns <= 0:
            _fail("espn_clock_invalid")
        try:
            document = json.loads(body.decode("utf-8"))
        except Exception:
            _fail("espn_body_not_json")
        if type(document) is not dict:
            _fail("espn_body_not_object")
        return EspnCapture(
            tour=tour,
            document=document,
            source_wall_ns=source_wall_ns,
            received_monotonic_ns=received_monotonic_ns,
            clock_uncertainty_ns=self._clock_uncertainty_ns,
        )


__all__: Final[tuple[str, ...]] = (
    "EspnCapture",
    "EspnReadOnlyError",
    "EspnScoreboardTransport",
)
