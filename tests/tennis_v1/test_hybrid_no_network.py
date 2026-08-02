from __future__ import annotations

import importlib
import socket
from io import StringIO
import unittest
from unittest.mock import patch

import requests
import websockets


class HybridNoNetworkSentinelTests(unittest.TestCase):
    def test_runtime_sentinel_is_reentrant(self) -> None:
        """Catches nested sentinels recursing through a patched socket alias."""

        from tests.tennis_v1.hybrid_no_network import deny_network

        with deny_network(), deny_network():
            candidate = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                with self.assertRaisesRegex(
                    AssertionError,
                    "hybrid_test_network_forbidden:socket.socket.bind",
                ):
                    candidate.bind(("127.0.0.1", 0))
            finally:
                candidate.close()

    def test_runtime_sentinel_blocks_socket_connection_attempts(self) -> None:
        from tests.tennis_v1.hybrid_no_network import deny_network

        with deny_network():
            with self.assertRaisesRegex(
                AssertionError,
                "hybrid_test_network_forbidden:socket.create_connection",
            ):
                socket.create_connection(("127.0.0.1", 9))

            candidate = socket.socket()
            try:
                with self.assertRaisesRegex(
                    AssertionError,
                    "hybrid_test_network_forbidden:socket.socket.connect",
                ):
                    candidate.connect(("127.0.0.1", 9))
            finally:
                candidate.close()

    def test_runtime_sentinel_blocks_http_and_websocket_attempts(self) -> None:
        from tests.tennis_v1.hybrid_no_network import deny_network

        with deny_network():
            with self.assertRaisesRegex(
                AssertionError,
                "hybrid_test_network_forbidden:requests.Session.request",
            ):
                requests.Session().request("GET", "https://example.invalid")
            with self.assertRaisesRegex(
                AssertionError,
                "hybrid_test_network_forbidden:websockets.connect",
            ):
                websockets.connect("wss://example.invalid")

    def test_runtime_sentinel_blocks_raw_datagram_server_and_dns_paths(self) -> None:
        """Catches raw socket operations bypassing the high-level patches."""

        from tests.tennis_v1.hybrid_no_network import deny_network

        with deny_network():
            candidate = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                for operation, label in (
                    (
                        lambda: candidate.bind(("127.0.0.1", 0)),
                        "socket.socket.bind",
                    ),
                    (
                        lambda: candidate.sendto(b"x", ("127.0.0.1", 9)),
                        "socket.socket.sendto",
                    ),
                ):
                    with self.subTest(label=label), self.assertRaisesRegex(
                        AssertionError,
                        f"hybrid_test_network_forbidden:{label}",
                    ):
                        operation()
            finally:
                candidate.close()

            with self.assertRaisesRegex(
                AssertionError,
                "hybrid_test_network_forbidden:socket.gethostbyname",
            ):
                socket.gethostbyname("example.invalid")

    def test_runtime_sentinel_blocks_public_sockettype_alias(self) -> None:
        """Catches `_socket.socket` access through the public SocketType alias."""

        from tests.tennis_v1.hybrid_no_network import deny_network

        with deny_network():
            candidate = socket.SocketType(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                for operation, label in (
                    (
                        lambda: candidate.bind(("127.0.0.1", 0)),
                        "socket.socket.bind",
                    ),
                    (
                        lambda: candidate.sendto(b"x", ("127.0.0.1", 9)),
                        "socket.socket.sendto",
                    ),
                ):
                    with self.subTest(label=label), self.assertRaisesRegex(
                        AssertionError,
                        f"hybrid_test_network_forbidden:{label}",
                    ):
                        operation()
            finally:
                candidate.close()

    def test_runtime_sentinel_blocks_low_level_socket_module_alias(self) -> None:
        """Catches direct `_socket.socket` construction under the sentinel."""

        from tests.tennis_v1.hybrid_no_network import deny_network

        with deny_network():
            low_level_socket = importlib.import_module("_socket")
            with self.assertRaisesRegex(
                AssertionError,
                "hybrid_test_network_forbidden:_socket.socket",
            ):
                low_level_socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def test_runtime_sentinel_blocks_precaptured_native_socket_types(self) -> None:
        """Catches immutable native constructors retained before denial."""

        from tests.tennis_v1.hybrid_no_network import deny_network

        low_level_socket = importlib.import_module("_socket")
        constructors = (
            socket.SocketType,
            socket.socket.__mro__[1],
            low_level_socket.socket,
        )
        with deny_network():
            for constructor in constructors:
                with self.subTest(
                    constructor=repr(constructor)
                ), self.assertRaisesRegex(
                    AssertionError,
                    "hybrid_test_network_forbidden:socket.__new__",
                ):
                    constructor(socket.AF_INET, socket.SOCK_DGRAM)

    def test_runtime_sentinel_blocks_socket_opened_before_context(self) -> None:
        """Catches a native socket object surviving into the denial window."""

        from tests.tennis_v1.hybrid_no_network import deny_network

        candidate = socket.SocketType(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with deny_network(), self.assertRaisesRegex(
                AssertionError,
                "hybrid_test_network_forbidden:socket.bind",
            ):
                candidate.bind(("127.0.0.1", 0))
        finally:
            candidate.close()

    def test_runtime_sentinel_quarantines_precaptured_native_socket_fds(self) -> None:
        """Catches native send/sendmsg methods bypassing Python-level patches."""

        from tests.tennis_v1.hybrid_no_network import deny_network

        left, right = socket.socketpair()
        native = socket.SocketType(fileno=left.detach())
        try:
            with deny_network():
                operations = [lambda: native.send(b"x")]
                if hasattr(native, "sendmsg"):
                    operations.append(lambda: native.sendmsg([b"y"]))
                for operation in operations:
                    with self.subTest(operation=repr(operation)), self.assertRaises(
                        OSError
                    ):
                        operation()
        finally:
            native.close()
            right.close()

    def test_main_installs_sentinel_before_loading_test_modules(self) -> None:
        """Catches import-time network access escaping before suite execution."""

        from tests.tennis_v1.hybrid_no_network import main

        escaped: list[bool] = []

        def would_escape(*args: object, **kwargs: object) -> None:
            del args, kwargs
            escaped.append(True)

        def load(_: object) -> unittest.TestSuite:
            socket.create_connection(("127.0.0.1", 9))
            return unittest.TestSuite()

        with patch("socket.create_connection", would_escape), patch.object(
            unittest.defaultTestLoader,
            "loadTestsFromNames",
            side_effect=load,
        ), self.assertRaisesRegex(
            AssertionError,
            "hybrid_test_network_forbidden:socket.create_connection",
        ):
            main(["synthetic"])

        self.assertEqual(escaped, [])

    def test_suite_runner_keeps_the_sentinel_active_for_loaded_tests(self) -> None:
        from tests.tennis_v1.hybrid_no_network import run_suite

        suite = unittest.TestSuite(
            (
                unittest.FunctionTestCase(
                    lambda: socket.create_connection(("127.0.0.1", 9))
                ),
            )
        )
        output = StringIO()
        result = run_suite(suite, stream=output)

        self.assertFalse(result.wasSuccessful())
        self.assertIn(
            "hybrid_test_network_forbidden:socket.create_connection",
            output.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
