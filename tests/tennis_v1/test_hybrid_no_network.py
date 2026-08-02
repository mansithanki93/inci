from __future__ import annotations

import socket
from io import StringIO
import unittest

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
