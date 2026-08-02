"""Run the hybrid/settlement tests with all real network access denied."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
import socket
import sys
import unittest
from unittest.mock import patch


_ORIGINAL_LOW_LEVEL_SOCKET_INITIALIZER = socket.socket.__mro__[1].__init__


def _forbidden(label: str):
    def reject(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError(f"hybrid_test_network_forbidden:{label}")

    return reject


class _DeniedLowLevelSocketAlias:
    """Deny `_socket.socket()` while preserving `socket.socket.__init__`."""

    def __init__(self, initializer: object) -> None:
        # socket.socket.__init__ calls `_socket.socket.__init__` explicitly.
        self.__dict__["__init__"] = initializer

    def __call__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("hybrid_test_network_forbidden:_socket.socket")


@contextmanager
def deny_network() -> Iterator[None]:
    low_level_socket_alias = _DeniedLowLevelSocketAlias(
        _ORIGINAL_LOW_LEVEL_SOCKET_INITIALIZER
    )
    socket_methods = (
        "connect",
        "connect_ex",
        "bind",
        "listen",
        "accept",
        "sendto",
    )
    optional_socket_methods = (
        "sendall",
        "sendfile",
        "sendmsg",
        "sendmsg_afalg",
    )
    targets = [
        ("socket.create_connection", "socket.create_connection"),
        ("socket.create_server", "socket.create_server"),
        ("socket.getaddrinfo", "socket.getaddrinfo"),
        ("socket.gethostbyname", "socket.gethostbyname"),
        ("socket.gethostbyname_ex", "socket.gethostbyname_ex"),
        ("socket.gethostbyaddr", "socket.gethostbyaddr"),
        ("socket.getnameinfo", "socket.getnameinfo"),
        ("requests.sessions.Session.request", "requests.Session.request"),
        ("websockets.connect", "websockets.connect"),
    ]
    targets.extend(
        (f"socket.socket.{name}", f"socket.socket.{name}")
        for name in (*socket_methods, *optional_socket_methods)
        if hasattr(socket.socket, name)
    )
    with ExitStack() as stack:
        for target, label in targets:
            stack.enter_context(patch(target, _forbidden(label)))
        # On CPython, SocketType exposes the immutable `_socket.socket` base
        # rather than the patchable `socket.socket` subclass. Route the public
        # alias through the already-denied subclass for the sentinel lifetime.
        stack.enter_context(patch("socket.SocketType", socket.socket))
        stack.enter_context(patch("_socket.socket", low_level_socket_alias))
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
