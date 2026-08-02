"""Run the hybrid/settlement tests with all real network access denied."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
import socket
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
    targets = [
        ("socket.create_connection", "socket.create_connection"),
        ("socket.create_server", "socket.create_server"),
        ("socket.socket.connect", "socket.socket.connect"),
        ("socket.socket.connect_ex", "socket.socket.connect_ex"),
        ("socket.socket.bind", "socket.socket.bind"),
        ("socket.socket.listen", "socket.socket.listen"),
        ("socket.socket.accept", "socket.socket.accept"),
        ("socket.socket.sendto", "socket.socket.sendto"),
        ("socket.getaddrinfo", "socket.getaddrinfo"),
        ("socket.gethostbyname", "socket.gethostbyname"),
        ("socket.gethostbyname_ex", "socket.gethostbyname_ex"),
        ("socket.gethostbyaddr", "socket.gethostbyaddr"),
        ("socket.getnameinfo", "socket.getnameinfo"),
        ("requests.sessions.Session.request", "requests.Session.request"),
        ("websockets.connect", "websockets.connect"),
    ]
    optional_socket_methods = (
        "sendall",
        "sendfile",
        "sendmsg",
        "sendmsg_afalg",
    )
    targets.extend(
        (f"socket.socket.{name}", f"socket.socket.{name}")
        for name in optional_socket_methods
        if hasattr(socket.socket, name)
    )
    with ExitStack() as stack:
        for target, label in targets:
            stack.enter_context(patch(target, _forbidden(label)))
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
    with deny_network():
        suite = unittest.defaultTestLoader.loadTestsFromNames(names)
        result = unittest.TextTestRunner(stream=sys.stderr).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
