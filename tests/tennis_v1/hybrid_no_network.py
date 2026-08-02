"""Run the hybrid/settlement tests with all real network access denied."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
import os
import socket
import stat
import sys
import unittest
from unittest.mock import patch


_ORIGINAL_LOW_LEVEL_SOCKET_INITIALIZER = socket.socket.__mro__[1].__init__
_PATCHABLE_SOCKET_CLASS = socket.socket
_NETWORK_DENIAL_DEPTH = 0
_NETWORK_AUDIT_HOOK_INSTALLED = False

_NETWORK_AUDIT_EVENTS = frozenset(
    {
        "socket.bind",
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.gethostbyname_ex",
        "socket.getnameinfo",
        "socket.getservbyname",
        "socket.getservbyport",
        "socket.sendto",
    }
)


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


def _network_audit_hook(event: str, args: tuple[object, ...]) -> None:
    if _NETWORK_DENIAL_DEPTH <= 0:
        return
    if event == "socket.__new__":
        if args and not isinstance(args[0], _PATCHABLE_SOCKET_CLASS):
            raise AssertionError(
                "hybrid_test_network_forbidden:socket.__new__"
            )
        return
    if event in _NETWORK_AUDIT_EVENTS:
        raise AssertionError(f"hybrid_test_network_forbidden:{event}")


def _install_network_audit_hook() -> None:
    global _NETWORK_AUDIT_HOOK_INSTALLED
    if _NETWORK_AUDIT_HOOK_INSTALLED:
        return
    sys.addaudithook(_network_audit_hook)
    _NETWORK_AUDIT_HOOK_INSTALLED = True


def _open_descriptor_names() -> tuple[int, ...]:
    """Return a bounded snapshot of this process's open descriptors."""

    for directory in ("/proc/self/fd", "/dev/fd"):
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        descriptors: list[int] = []
        for name in names:
            if name.isascii() and name.isdecimal():
                descriptors.append(int(name))
        return tuple(descriptors)
    raise AssertionError("hybrid_test_network_sentinel_unavailable:fd_inventory")


def _quarantine_precaptured_socket_fds() -> None:
    """Replace inherited socket descriptors so native aliases cannot transmit.

    Python's immutable ``_socket.socket`` methods cannot be monkey-patched.  A
    test could retain both an instance and its native ``send`` method before
    entering this context.  Replacing every already-open socket descriptor with
    a harmless tombstone closes that escape without risking later descriptor
    reuse: the retained socket object still owns the tombstone and will close it
    normally when the test releases the object.
    """

    for descriptor in _open_descriptor_names():
        try:
            info = os.fstat(descriptor)
        except OSError:
            continue
        if not stat.S_ISSOCK(info.st_mode):
            continue
        try:
            os.close(descriptor)
        except OSError:
            continue
        tombstone = os.open(os.devnull, os.O_RDWR)
        if tombstone == descriptor:
            continue
        try:
            os.dup2(tombstone, descriptor)
        finally:
            os.close(tombstone)


@contextmanager
def deny_network() -> Iterator[None]:
    global _NETWORK_DENIAL_DEPTH
    _install_network_audit_hook()
    if _NETWORK_DENIAL_DEPTH == 0:
        _quarantine_precaptured_socket_fds()
    _NETWORK_DENIAL_DEPTH += 1
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
    try:
        with ExitStack() as stack:
            for target, label in targets:
                stack.enter_context(patch(target, _forbidden(label)))
            # On CPython, SocketType exposes the immutable `_socket.socket`
            # base rather than the patchable `socket.socket` subclass. Route
            # module lookups through denied aliases; the audit hook also blocks
            # immutable references captured before this context.
            stack.enter_context(patch("socket.SocketType", socket.socket))
            stack.enter_context(patch("_socket.socket", low_level_socket_alias))
            yield
    finally:
        _NETWORK_DENIAL_DEPTH -= 1


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
