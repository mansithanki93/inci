from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True

from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives import hashes, serialization

from inci_tennis_expert.contracts import (
    BookDelta,
    BookSnapshot,
    ContractSide,
    MarketLifecycle,
    MarketStatus,
)
from inci_tennis_io.kalshi_readonly import (
    KALSHI_WS_PATH,
    KalshiBookDecoder,
    KalshiMarketMetadata,
    KalshiObservationClock,
    KalshiReadOnlyError,
    KalshiReadOnlyFeed,
    KalshiResnapshotRequired,
    READ_ONLY_CHANNELS,
    decode_kalshi_text_frame,
    handshake_signature_payload,
    kalshi_handshake_headers,
    subscribe_command,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"
TICKER = "KXATPMATCH-26AUG04-HOME"
CLOSE_NS = 1_785_900_000_000_000_000
CLOCK = KalshiObservationClock(
    source_wall_ns=1_785_889_587_000_000_000,
    observed_monotonic_ns=5_000_000_000,
    clock_uncertainty_ns=1_000_000,
)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def snapshot_frame() -> dict:
    return fixture("kalshi_ws_orderbook_snapshot_v2.json")


def delta_frame() -> dict:
    return fixture("kalshi_ws_orderbook_delta_v2.json")


def lifecycle_frame() -> dict:
    return fixture("kalshi_ws_lifecycle_v2.json")


def decoder() -> KalshiBookDecoder:
    return KalshiBookDecoder(
        {
            TICKER: KalshiMarketMetadata(
                market_status=MarketStatus.OPEN,
                scheduled_close_wall_ns=CLOSE_NS,
            )
        }
    )


class FakeSocket:
    """Minimal stand-in for one physical websockets connection."""

    def __init__(self, inbound: tuple[str, ...] = ()) -> None:
        self.sent: list[str] = []
        self.closed = False
        self._inbound = list(inbound)

    def send(self, text: str) -> None:
        self.sent.append(text)

    def close(self) -> None:
        self.closed = True

    def __iter__(self):
        return iter(self._inbound)


class SigningTests(unittest.TestCase):
    def test_only_the_websocket_get_path_is_signed(self) -> None:
        payload = handshake_signature_payload(1_700_000_000_000)
        self.assertEqual(payload, b"1700000000000GET/trade-api/ws/v2")
        self.assertIn(KALSHI_WS_PATH.encode("ascii"), payload)
        self.assertNotIn(b"POST", payload)
        self.assertNotIn(b"portfolio", payload)

    def test_headers_verify_against_the_public_key(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        headers = kalshi_handshake_headers(
            key_id="key-abc",
            private_key_pem=pem,
            timestamp_ms=1_700_000_000_000,
        )
        self.assertEqual(headers["KALSHI-ACCESS-KEY"], "key-abc")
        self.assertEqual(
            headers["KALSHI-ACCESS-TIMESTAMP"],
            "1700000000000",
        )
        from base64 import b64decode

        key.public_key().verify(
            b64decode(headers["KALSHI-ACCESS-SIGNATURE"]),
            handshake_signature_payload(1_700_000_000_000),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

    def test_credentials_never_appear_in_repr(self) -> None:
        feed = KalshiReadOnlyFeed(
            decoder(),
            connect=lambda url, additional_headers: FakeSocket(),
            key_id="super-secret-key-id",
            private_key_pem="-----BEGIN PRIVATE KEY-----secret",
        )
        self.assertNotIn("super-secret-key-id", repr(feed))
        self.assertNotIn("secret", repr(feed))

    def test_bad_timestamp_and_key_fail_closed(self) -> None:
        with self.assertRaises(KalshiReadOnlyError):
            handshake_signature_payload(0)
        with self.assertRaises(KalshiReadOnlyError):
            kalshi_handshake_headers(
                key_id="",
                private_key_pem="x",
                timestamp_ms=1,
            )
        with self.assertRaises(KalshiReadOnlyError):
            kalshi_handshake_headers(
                key_id="k",
                private_key_pem="not-a-pem",
                timestamp_ms=1,
            )


class SubscriptionTests(unittest.TestCase):
    def test_only_read_only_channels_are_permitted(self) -> None:
        self.assertEqual(
            READ_ONLY_CHANNELS,
            frozenset({"orderbook_delta", "market_lifecycle_v2", "trade"}),
        )
        for forbidden in ("user_orders", "user_fills", "market_positions"):
            with self.subTest(channel=forbidden):
                with self.assertRaises(KalshiReadOnlyError):
                    subscribe_command(
                        command_id=1,
                        channel=forbidden,
                        market_tickers=(TICKER,),
                    )

    def test_orderbook_subscription_shape(self) -> None:
        command = subscribe_command(
            command_id=1,
            channel="orderbook_delta",
            market_tickers=(TICKER,),
        )
        self.assertEqual(
            command,
            {
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_tickers": [TICKER],
                },
            },
        )

    def test_lifecycle_channel_rejects_ticker_filters(self) -> None:
        command = subscribe_command(
            command_id=2,
            channel="market_lifecycle_v2",
            market_tickers=(),
        )
        self.assertNotIn("market_tickers", command["params"])
        with self.assertRaises(KalshiReadOnlyError):
            subscribe_command(
                command_id=3,
                channel="market_lifecycle_v2",
                market_tickers=(TICKER,),
            )


class SnapshotDecodeTests(unittest.TestCase):
    def test_snapshot_becomes_a_descending_normalized_book(self) -> None:
        book = decoder().decode(snapshot_frame(), CLOCK)
        self.assertIsInstance(book, BookSnapshot)
        assert isinstance(book, BookSnapshot)
        self.assertEqual(book.ticker, TICKER)
        self.assertEqual(book.sequence, 2)
        self.assertIs(book.market_status, MarketStatus.OPEN)
        self.assertEqual(book.scheduled_close_wall_ns, CLOSE_NS)
        self.assertEqual(
            [(level.price, level.quantity) for level in book.yes_bids],
            [(Decimal("0.4000"), Decimal("5.00")),
             (Decimal("0.3500"), Decimal("2.00"))],
        )
        self.assertEqual(
            [(level.price, level.quantity) for level in book.no_bids],
            [(Decimal("0.4500"), Decimal("2.00")),
             (Decimal("0.4400"), Decimal("4.00"))],
        )

    def test_absent_side_is_an_empty_ladder(self) -> None:
        frame = snapshot_frame()
        del frame["msg"]["no_dollars_fp"]
        book = decoder().decode(frame, CLOCK)
        assert isinstance(book, BookSnapshot)
        self.assertEqual(book.no_bids, ())
        self.assertEqual(len(book.yes_bids), 2)

    def test_out_of_range_and_malformed_levels_fail_closed(self) -> None:
        for level in ([["1.0000", "5.00"]], [["0.0000", "5.00"]],
                      [["0.4000"]], [["0.4000", "-5.00"]], "levels"):
            with self.subTest(level=level):
                frame = snapshot_frame()
                frame["msg"]["yes_dollars_fp"] = level
                with self.assertRaises(KalshiReadOnlyError):
                    decoder().decode(frame, CLOCK)

    def test_unknown_ticker_is_rejected(self) -> None:
        frame = snapshot_frame()
        frame["msg"]["market_ticker"] = "KXOTHER-MARKET"
        with self.assertRaises(KalshiReadOnlyError):
            decoder().decode(frame, CLOCK)


class DeltaDecodeTests(unittest.TestCase):
    def test_additive_delta_becomes_absolute_quantity(self) -> None:
        book = decoder()
        book.decode(snapshot_frame(), CLOCK)
        delta = book.decode(delta_frame(), CLOCK)
        self.assertIsInstance(delta, BookDelta)
        assert isinstance(delta, BookDelta)
        # NO 0.45 held 2.00 and the wire delta is -1.00.
        self.assertIs(delta.contract_side, ContractSide.NO)
        self.assertEqual(delta.price, Decimal("0.4500"))
        self.assertEqual(delta.quantity, Decimal("1.00"))
        self.assertEqual(delta.sequence, 3)

    def test_delta_emptying_a_level_reports_zero(self) -> None:
        book = decoder()
        book.decode(snapshot_frame(), CLOCK)
        frame = delta_frame()
        frame["msg"]["delta_fp"] = "-2.00"
        delta = book.decode(frame, CLOCK)
        assert isinstance(delta, BookDelta)
        self.assertEqual(delta.quantity, Decimal("0"))

    def test_delta_before_snapshot_demands_one(self) -> None:
        with self.assertRaises(KalshiResnapshotRequired):
            decoder().decode(delta_frame(), CLOCK)

    def test_sequence_gap_forces_resnapshot(self) -> None:
        book = decoder()
        book.decode(snapshot_frame(), CLOCK)
        frame = delta_frame()
        frame["seq"] = 9
        with self.assertRaises(KalshiResnapshotRequired):
            book.decode(frame, CLOCK)
        self.assertIn(TICKER, book.awaiting_snapshot())
        # No further delta may advance the book until a snapshot arrives.
        with self.assertRaises(KalshiResnapshotRequired):
            book.decode(delta_frame(), CLOCK)

    def test_delta_below_zero_forces_resnapshot(self) -> None:
        book = decoder()
        book.decode(snapshot_frame(), CLOCK)
        frame = delta_frame()
        frame["msg"]["delta_fp"] = "-99.00"
        with self.assertRaises(KalshiResnapshotRequired):
            book.decode(frame, CLOCK)
        self.assertIn(TICKER, book.awaiting_snapshot())

    def test_replayed_sequence_forces_resnapshot(self) -> None:
        book = decoder()
        book.decode(snapshot_frame(), CLOCK)
        book.decode(delta_frame(), CLOCK)
        with self.assertRaises(KalshiResnapshotRequired):
            book.decode(delta_frame(), CLOCK)

    def test_snapshot_after_gap_restores_the_book(self) -> None:
        book = decoder()
        book.decode(snapshot_frame(), CLOCK)
        gapped = delta_frame()
        gapped["seq"] = 40
        with self.assertRaises(KalshiResnapshotRequired):
            book.decode(gapped, CLOCK)
        recovery = snapshot_frame()
        recovery["seq"] = 41
        restored = book.decode(recovery, CLOCK)
        self.assertIsInstance(restored, BookSnapshot)
        self.assertEqual(book.awaiting_snapshot(), ())
        following = delta_frame()
        following["seq"] = 42
        self.assertIsInstance(book.decode(following, CLOCK), BookDelta)

    def test_unknown_side_is_rejected(self) -> None:
        book = decoder()
        book.decode(snapshot_frame(), CLOCK)
        frame = delta_frame()
        frame["msg"]["side"] = "maybe"
        with self.assertRaises(KalshiReadOnlyError):
            book.decode(frame, CLOCK)


class TrustEpochTests(unittest.TestCase):
    def test_new_epoch_is_strictly_newer_and_drops_every_book(self) -> None:
        book = decoder()
        book.decode(snapshot_frame(), CLOCK)
        first = book.connection_epoch
        second = book.begin_trust_epoch()
        self.assertGreater(second, first)
        self.assertEqual(book.awaiting_snapshot(), (TICKER,))
        with self.assertRaises(KalshiResnapshotRequired):
            book.decode(delta_frame(), CLOCK)

    def test_snapshots_carry_the_current_epoch(self) -> None:
        book = decoder()
        before = book.decode(snapshot_frame(), CLOCK)
        assert isinstance(before, BookSnapshot)
        book.begin_trust_epoch()
        after = book.decode(snapshot_frame(), CLOCK)
        assert isinstance(after, BookSnapshot)
        self.assertGreater(after.connection_epoch, before.connection_epoch)


class LifecycleTests(unittest.TestCase):
    def test_lifecycle_maps_to_market_status(self) -> None:
        book = decoder()
        event = book.decode(lifecycle_frame(), CLOCK)
        self.assertIsInstance(event, MarketLifecycle)
        assert isinstance(event, MarketLifecycle)
        self.assertIs(event.market_status, MarketStatus.SUSPENDED)
        self.assertEqual(event.scheduled_close_wall_ns, CLOSE_NS)

    def test_lifecycle_status_is_remembered_for_later_snapshots(self) -> None:
        book = decoder()
        book.decode(lifecycle_frame(), CLOCK)
        snapshot = book.decode(snapshot_frame(), CLOCK)
        assert isinstance(snapshot, BookSnapshot)
        self.assertIs(snapshot.market_status, MarketStatus.SUSPENDED)

    def test_other_markets_and_events_are_ignored(self) -> None:
        book = decoder()
        other = lifecycle_frame()
        other["msg"]["market_ticker"] = "KXSOMETHING-ELSE"
        self.assertIsNone(book.decode(other, CLOCK))
        unknown = lifecycle_frame()
        unknown["msg"]["event_type"] = "metadata_updated"
        self.assertIsNone(book.decode(unknown, CLOCK))


class FrameTests(unittest.TestCase):
    def test_control_frames_are_ignored_and_errors_fail(self) -> None:
        book = decoder()
        self.assertIsNone(book.decode({"type": "subscribed", "id": 1}, CLOCK))
        with self.assertRaises(KalshiReadOnlyError):
            book.decode({"type": "error", "msg": {}}, CLOCK)
        with self.assertRaises(KalshiReadOnlyError):
            book.decode({"type": "user_fill", "msg": {}}, CLOCK)

    def test_text_frames_are_size_capped_and_must_be_json_objects(
        self,
    ) -> None:
        self.assertEqual(decode_kalshi_text_frame('{"type":"ok"}'),
                         {"type": "ok"})
        with self.assertRaises(KalshiReadOnlyError):
            decode_kalshi_text_frame("[1,2,3]")
        with self.assertRaises(KalshiReadOnlyError):
            decode_kalshi_text_frame("not json")
        with self.assertRaises(KalshiReadOnlyError):
            decode_kalshi_text_frame("x" * 5_000_000)


class FeedTests(unittest.TestCase):
    def build(self, inbound: tuple[str, ...] = ()):
        sockets: list[FakeSocket] = []

        def connect(url, additional_headers):
            self.assertTrue(url.startswith("wss://"))
            self.assertIn("KALSHI-ACCESS-SIGNATURE", additional_headers)
            socket = FakeSocket(inbound)
            sockets.append(socket)
            return socket

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        feed = KalshiReadOnlyFeed(
            decoder(),
            connect=connect,
            key_id="key-abc",
            private_key_pem=pem,
        )
        return feed, sockets

    def test_connect_subscribes_read_only_channels(self) -> None:
        feed, sockets = self.build()
        feed.connect()
        self.assertEqual(len(sockets), 1)
        commands = [json.loads(text) for text in sockets[0].sent]
        self.assertEqual(len(commands), 2)
        channels = [c["params"]["channels"][0] for c in commands]
        self.assertEqual(channels, ["orderbook_delta", "market_lifecycle_v2"])
        for command in commands:
            self.assertEqual(command["cmd"], "subscribe")
        feed.close()

    def test_reconnect_never_overlaps_sockets(self) -> None:
        feed, sockets = self.build()
        first_epoch = feed.connect()
        second_epoch = feed.connect()
        self.assertEqual(len(sockets), 2)
        self.assertTrue(sockets[0].closed)
        self.assertFalse(sockets[1].closed)
        self.assertGreater(second_epoch, first_epoch)
        feed.close()
        self.assertTrue(sockets[1].closed)
        self.assertFalse(feed.socket_open)

    def test_events_stream_snapshot_then_delta(self) -> None:
        inbound = (
            json.dumps({"type": "subscribed", "id": 1}),
            json.dumps(snapshot_frame()),
            json.dumps(delta_frame()),
        )
        feed, _ = self.build(inbound)
        feed.connect()
        events = list(feed.events())
        self.assertEqual(len(events), 2)
        self.assertIsInstance(events[0], BookSnapshot)
        self.assertIsInstance(events[1], BookDelta)
        feed.close()

    def test_desynchronized_ticker_is_skipped_not_fatal(self) -> None:
        gapped = delta_frame()
        gapped["seq"] = 77
        inbound = (
            json.dumps(snapshot_frame()),
            json.dumps(gapped),
            json.dumps(lifecycle_frame()),
        )
        feed, _ = self.build(inbound)
        feed.connect()
        events = list(feed.events())
        self.assertEqual(len(events), 2)
        self.assertIsInstance(events[0], BookSnapshot)
        self.assertIsInstance(events[1], MarketLifecycle)
        feed.close()

    def test_events_require_an_open_socket(self) -> None:
        feed, _ = self.build()
        with self.assertRaises(KalshiReadOnlyError):
            list(feed.events())


class ExpertBookIntegrationTests(unittest.TestCase):
    """Decoded frames must drive the expert book state machine unchanged."""

    def test_snapshot_and_delta_advance_a_trusted_book(self) -> None:
        from inci_tennis_expert.market_book import (
            apply_book_delta,
            book_from_snapshot,
        )

        book = decoder()
        snapshot = book.decode(snapshot_frame(), CLOCK)
        assert isinstance(snapshot, BookSnapshot)
        state = book_from_snapshot(snapshot).state
        self.assertTrue(state.trusted)
        self.assertFalse(state.sequence_gap)
        self.assertEqual(state.no_bids[0].quantity, Decimal("2.00"))

        delta = book.decode(delta_frame(), CLOCK)
        assert isinstance(delta, BookDelta)
        advanced = apply_book_delta(state, delta).state
        self.assertTrue(advanced.trusted)
        self.assertEqual(advanced.sequence, 3)
        # The NO 0.45 level went from 2.00 to 1.00 via a -1.00 wire delta.
        self.assertEqual(advanced.no_bids[0].price, Decimal("0.4500"))
        self.assertEqual(advanced.no_bids[0].quantity, Decimal("1.00"))

    def test_executable_ask_is_derivable_from_the_decoded_book(self) -> None:
        from inci_tennis_expert.market_book import book_from_snapshot

        book = decoder()
        snapshot = book.decode(snapshot_frame(), CLOCK)
        assert isinstance(snapshot, BookSnapshot)
        state = book_from_snapshot(snapshot).state
        # A YES buy lifts the complementary NO bid: ask = 1 - best NO bid.
        best_no_bid = state.no_bids[0].price
        self.assertEqual(Decimal("1") - best_no_bid, Decimal("0.5500"))


class MutationBoundaryTests(unittest.TestCase):
    def test_module_contains_no_order_or_mutation_surface(self) -> None:
        source = (
            Path(__file__).resolve().parents[2]
            / "inci_tennis_io"
            / "kalshi_readonly.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            '"POST"', "'POST'", '"PUT"', '"DELETE"', '"PATCH"',
            "/portfolio/orders", "create_order", "cancel_order",
            "batch_orders", "decrease_order",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
