from __future__ import annotations

import socket
from io import StringIO
import unittest
from unittest.mock import patch

import requests
import websockets


class HybridNoNetworkSentinelTests(unittest.TestCase):
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
