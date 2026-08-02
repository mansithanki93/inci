"""Run the hybrid/settlement tests with all real network access denied."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import sys
import unittest
from unittest.mock import patch


def _forbidden(label: str):
    def reject(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError(f"hybrid_test_network_forbidden:{label}")

    return reject


@contextmanager
def deny_network() -> Iterator[None]:
    with (
        patch(
            "socket.create_connection",
            _forbidden("socket.create_connection"),
        ),
        patch(
            "socket.socket.connect",
            _forbidden("socket.socket.connect"),
        ),
        patch(
            "socket.socket.connect_ex",
            _forbidden("socket.socket.connect_ex"),
        ),
        patch(
            "socket.getaddrinfo",
            _forbidden("socket.getaddrinfo"),
        ),
        patch(
            "requests.sessions.Session.request",
            _forbidden("requests.Session.request"),
        ),
        patch(
            "websockets.connect",
            _forbidden("websockets.connect"),
        ),
    ):
        yield


def run_suite(
    suite: unittest.TestSuite,
    *,
    stream: object = sys.stderr,
) -> unittest.TestResult:
    with deny_network():
        return unittest.TextTestRunner(stream=stream).run(suite)


def main(argv: list[str] | None = None) -> int:
    names = sys.argv[1:] if argv is None else argv
    if not names:
        return 2
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    return 0 if run_suite(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
