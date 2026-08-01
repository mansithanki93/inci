from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from decimal import Decimal
from hashlib import sha256
import importlib
import importlib.util
import gc
import json
from pathlib import Path
from typing import Literal, get_type_hints
import unittest
import weakref

from tennis_v1.canonical import canonical_json_bytes


MODULE = "inci_tennis_adapters.kalshi_candidate"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMAS = (
    Path(__file__).resolve().parents[2]
    / "inci_tennis_adapters"
    / "schemas"
)
CANDIDATE_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "inci_tennis_adapters"
    / "kalshi_candidate.py"
)
PROVENANCE = (
    "Synthetic Task-8 candidate fixture for deterministic offline testing "
    "only; it is not captured from or validated against current Kalshi "
    "wire data."
)
FIXTURE_PINS = {
    "kalshi_orderbook_snapshot_synthetic_candidate_v1.json": (
        "ac880e11d9f243399365ff9d335a51e6fb915797af1a903946c9389bab1952ea",
        "61bd9024b7cd91b43a308123be347f96eaa736abd0a79091e24759c4f5badc28",
    ),
    "kalshi_orderbook_delta_synthetic_candidate_v1.json": (
        "e9e82d5a019745c639a7521d79c7684e3a7b7f771eb14963ba3993feeaa8f3cc",
        "074d4a6d0f0df3207176e4f8f70b24bc8a30076044a0e3922c57c1377f024ece",
    ),
    "kalshi_market_lifecycle_synthetic_candidate_v1.json": (
        "75cd02db8def1d3474c7ccb46ef16e2f0f455a5d8e5eef7fc8686245b1275293",
        "db5f100167e59c0a5e630b638d9125a92e066d3c0063cea7528098e039ac4a9a",
    ),
    "kalshi_public_trade_synthetic_candidate_v1.json": (
        "67f7ec4fcb72e4a750ac74c4f8ee2796bf8267ae9874ffa9315e20d531060287",
        "6cc64b464432925348ec81b48d82c870abdec7d947832581c8f1c136a41191c0",
    ),
}


def _module(testcase: unittest.TestCase):
    if importlib.util.find_spec(MODULE) is None:
        testcase.skipTest("Task-8 candidate module has not been implemented")
    import inci_tennis_adapters.kalshi_candidate as candidate_module

    return candidate_module


def _raw(document: object) -> bytes:
    return canonical_json_bytes(document)


def _classify(testcase: unittest.TestCase, document: object):
    module = _module(testcase)
    payload = _raw(document)
    return module.classify_kalshi_synthetic_frame(
        payload,
        expected_raw_sha256=sha256(payload).hexdigest(),
    )


def _snapshot(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "type": "ORDERBOOK_SNAPSHOT",
        "ticker": "SYNTHETIC-TENNIS-HOME",
        "sequence": "synthetic-seq-1",
        "complete": True,
        "yes_bids": [{"price": "0.4", "quantity": "5"}],
        "no_bids": [{"price": "0.45", "quantity": "2"}],
    }
    value.update(updates)
    return value


def _delta(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "type": "ORDERBOOK_DELTA",
        "ticker": "SYNTHETIC-TENNIS-HOME",
        "sequence": "synthetic-seq-2",
        "side": "YES",
        "price": "0.4",
        "quantity_delta": "-1",
    }
    value.update(updates)
    return value


def _lifecycle(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "type": "MARKET_LIFECYCLE",
        "ticker": "SYNTHETIC-TENNIS-HOME",
        "sequence": "synthetic-seq-3",
        "status": "SUSPENDED",
    }
    value.update(updates)
    return value


def _trade(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "type": "PUBLIC_TRADE",
        "ticker": "SYNTHETIC-TENNIS-HOME",
        "sequence": "synthetic-seq-4",
        "trade_id": "SYNTHETIC-TRADE-1",
        "side": "YES",
        "price": "0.42",
        "quantity": "1.5",
    }
    value.update(updates)
    return value


def _validate_fixture_bytes(content: bytes) -> dict[str, object]:
    try:
        wrapper = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("fixture_invalid") from error
    if (
        type(wrapper) is not dict
        or set(wrapper)
        != {
            "fixture_kind",
            "provenance",
            "raw_sha256",
            "payload",
        }
        or content != canonical_json_bytes(wrapper)
        or wrapper["fixture_kind"] != "kalshi_synthetic_candidate_v1"
        or wrapper["provenance"] != PROVENANCE
        or type(wrapper["payload"]) is not dict
        or type(wrapper["raw_sha256"]) is not str
        or sha256(canonical_json_bytes(wrapper["payload"])).hexdigest()
        != wrapper["raw_sha256"]
    ):
        raise ValueError("fixture_invalid")
    return wrapper


class CandidateModuleRedTests(unittest.TestCase):
    def test_candidate_module_exists_for_semantic_contract(self) -> None:
        self.assertIsNotNone(
            importlib.util.find_spec(MODULE),
            "Task-8 candidate parser/protocol module is absent",
        )


class CandidateValueContractTests(unittest.TestCase):
    def test_candidate_regex_import_shape_is_boundary_safe(self) -> None:
        tree = ast.parse(
            CANDIDATE_SOURCE.read_text(encoding="utf-8"),
            filename=str(CANDIDATE_SOURCE),
        )
        direct_re_imports = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "re"
        }
        from_re_imports = {
            (alias.name, alias.asname)
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module == "re"
            and node.level == 0
            for alias in node.names
        }
        self.assertEqual(direct_re_imports, set())
        self.assertEqual(
            from_re_imports,
            {
                ("Pattern", None),
                ("compile", "pattern_compile"),
            },
        )

    def test_snapshot_complete_annotation_is_literal_true(self) -> None:
        module = _module(self)
        self.assertEqual(
            get_type_hints(
                module.KalshiCandidateOrderbookSnapshotV1
            )["complete"],
            Literal[True],
        )

    def test_exact_enum_members_and_values(self) -> None:
        module = _module(self)
        expected = {
            module.KalshiCandidatePhysicalStateV1: (
                "ABSENT",
                "CONNECTING",
                "CONNECTED",
                "CLOSING",
                "UNCERTAIN",
                "CLOSED",
                "HALTED",
            ),
            module.KalshiCandidateProtocolEventKindV1: (
                "OPEN_REQUESTED",
                "OPEN_SUCCEEDED",
                "OPEN_FAILED",
                "RECEIVE_ARMED",
                "RECEIVE_COMPLETED",
                "RECEIVE_FAILED",
                "CLOSE_REQUESTED",
                "CLOSE_PROVEN",
                "CLOSE_TIMEOUT",
                "CLOSE_FAILED",
                "BARRIER_REQUIRED",
                "BARRIER_WITNESSED",
                "HALT_REQUESTED",
            ),
            module.KalshiCandidateProtocolActionV1: (
                "NONE",
                "CONNECTION_OPENED",
                "CONNECTION_CLOSED",
                "BARRIER_REQUIRED",
                "TRANSITION_REJECTED",
                "HALTED",
            ),
            module.KalshiCandidateProtocolReasonV1: (
                "PROTOCOL_TRANSITION_INVALID",
                "GENERATION_OVERFLOW",
                "OPEN_FAILED",
                "RECEIVE_FAILED",
                "CLOSE_UNCERTAIN",
                "HALT_REQUESTED",
            ),
            module.KalshiCandidateParseStatusV1: ("ACCEPTED", "REJECTED"),
            module.KalshiCandidateFrameKindV1: (
                "ORDERBOOK_SNAPSHOT",
                "ORDERBOOK_DELTA",
                "MARKET_LIFECYCLE",
                "PUBLIC_TRADE",
            ),
            module.KalshiCandidateErrorScopeV1: ("TICKER", "CONNECTION"),
            module.KalshiCandidateRejectReasonV1: (
                "PAYLOAD_INVALID",
                "SCHEMA_UNKNOWN",
                "CAPACITY_EXCEEDED",
                "IDENTITY_INVALID",
                "BOOK_INVALID",
            ),
            module.KalshiCandidateSideV1: ("YES", "NO"),
            module.KalshiCandidateMarketStatusV1: (
                "OPEN",
                "SUSPENDED",
                "CLOSED",
                "SETTLED",
            ),
        }
        for enum_type, names in expected.items():
            with self.subTest(enum=enum_type.__name__):
                self.assertEqual(tuple(enum_type.__members__), names)
                self.assertEqual(
                    tuple(member.value for member in enum_type),
                    names,
                )

    def test_exact_private_frozen_slotted_contract_fields(self) -> None:
        module = _module(self)
        expected = {
            module.KalshiSyntheticBarrierIdentityV1: (
                "schema_version",
                "physical_connection_generation",
                "barrier_nonce",
                "identity_sha256",
            ),
            module.KalshiCandidateProtocolStateV1: (
                "schema_version",
                "physical_state",
                "physical_connection_generation",
                "active_receive",
                "pending_barrier_identity",
                "state_sha256",
            ),
            module.KalshiCandidateProtocolEventV1: (
                "schema_version",
                "kind",
                "barrier_identity",
                "event_sha256",
            ),
            module.KalshiCandidateProtocolTransitionV1: (
                "schema_version",
                "prior_state",
                "event",
                "state",
                "action",
                "reason",
                "transition_sha256",
            ),
            module.KalshiCandidateFrameResultV1: (
                "schema_version",
                "status",
                "kind",
                "scope",
                "reason",
                "raw_sha256",
                "ticker",
                "opaque_sequence",
                "frame",
                "result_sha256",
            ),
            module.KalshiCandidateBookLevelV1: ("price", "quantity"),
            module.KalshiCandidateOrderbookSnapshotV1: (
                "schema_version",
                "ticker",
                "opaque_sequence",
                "complete",
                "yes_bids",
                "no_bids",
            ),
            module.KalshiCandidateOrderbookDeltaV1: (
                "schema_version",
                "ticker",
                "opaque_sequence",
                "side",
                "price",
                "quantity_delta",
            ),
            module.KalshiCandidateMarketLifecycleV1: (
                "schema_version",
                "ticker",
                "opaque_sequence",
                "status",
            ),
            module.KalshiCandidatePublicTradeV1: (
                "schema_version",
                "ticker",
                "opaque_sequence",
                "trade_id",
                "side",
                "price",
                "quantity",
            ),
        }
        for contract, names in expected.items():
            with self.subTest(contract=contract.__name__):
                self.assertEqual(
                    tuple(field.name for field in fields(contract)),
                    names,
                )
                self.assertEqual(contract.__slots__, names)
                with self.assertRaises(TypeError):
                    contract()
                with self.assertRaises(TypeError):
                    type("Forged", (contract,), {})
        state = module.initial_kalshi_candidate_protocol_state()
        with self.assertRaises(FrozenInstanceError):
            state.active_receive = True

    def test_private_factories_reject_caller_schema_or_digest(self) -> None:
        module = _module(self)
        with self.assertRaises(TypeError):
            module.KalshiSyntheticBarrierIdentityV1._create(
                physical_connection_generation=1,
                barrier_nonce="nonce",
                schema_version=1,
            )
        with self.assertRaises(TypeError):
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.OPEN_REQUESTED,
                event_sha256="0" * 64,
            )

    def test_initial_absent_has_one_sentinel_protected_construction_path(
        self,
    ) -> None:
        module = _module(self)
        with self.assertRaisesRegex(
            ValueError,
            "kalshi_candidate_contract_invalid",
        ):
            module.KalshiCandidateProtocolStateV1._create(
                module.KalshiCandidatePhysicalStateV1.ABSENT,
                0,
                False,
                None,
            )
        with self.assertRaises(TypeError):
            module.KalshiCandidateProtocolStateV1._create_initial_absent(
                object()
            )


class CandidateProtocolTests(unittest.TestCase):
    def _connected(self):
        module = _module(self)
        absent = module.initial_kalshi_candidate_protocol_state()
        opening = module.transition_kalshi_candidate_protocol(
            absent,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.OPEN_REQUESTED
            ),
        ).state
        connected_transition = module.transition_kalshi_candidate_protocol(
            opening,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.OPEN_SUCCEEDED
            ),
        )
        return module, connected_transition.state

    def test_initial_state_and_open_chain_are_exact(self) -> None:
        module, connected = self._connected()
        initial = module.initial_kalshi_candidate_protocol_state()
        self.assertEqual(
            (
                initial.physical_state,
                initial.physical_connection_generation,
                initial.active_receive,
                initial.pending_barrier_identity,
            ),
            (
                module.KalshiCandidatePhysicalStateV1.ABSENT,
                0,
                False,
                None,
            ),
        )
        self.assertEqual(
            connected.physical_state,
            module.KalshiCandidatePhysicalStateV1.CONNECTED,
        )
        self.assertEqual(connected.physical_connection_generation, 1)

    def test_receive_and_close_barrier_chain(self) -> None:
        module, connected = self._connected()
        armed = module.transition_kalshi_candidate_protocol(
            connected,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.RECEIVE_ARMED
            ),
        )
        self.assertTrue(armed.state.active_receive)
        completed = module.transition_kalshi_candidate_protocol(
            armed.state,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.RECEIVE_COMPLETED
            ),
        )
        self.assertFalse(completed.state.active_receive)
        barrier = module.make_kalshi_synthetic_barrier_identity(
            completed.state,
            barrier_nonce="close-1",
        )
        closing = module.transition_kalshi_candidate_protocol(
            completed.state,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.CLOSE_REQUESTED,
                barrier_identity=barrier,
            ),
        )
        self.assertIs(
            closing.action,
            module.KalshiCandidateProtocolActionV1.BARRIER_REQUIRED,
        )
        witnessed = module.transition_kalshi_candidate_protocol(
            closing.state,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.BARRIER_WITNESSED,
                barrier_identity=barrier,
            ),
        )
        self.assertIsNone(witnessed.state.pending_barrier_identity)
        closed = module.transition_kalshi_candidate_protocol(
            witnessed.state,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.CLOSE_PROVEN
            ),
        )
        reopened = module.transition_kalshi_candidate_protocol(
            closed.state,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.OPEN_REQUESTED
            ),
        )
        self.assertEqual(
            reopened.state.physical_state,
            module.KalshiCandidatePhysicalStateV1.CONNECTING,
        )
        self.assertEqual(
            reopened.state.physical_connection_generation,
            1,
        )

    def test_receive_failure_requires_and_preserves_exact_barrier(self) -> None:
        module, connected = self._connected()
        armed = module.transition_kalshi_candidate_protocol(
            connected,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.RECEIVE_ARMED
            ),
        ).state
        barrier = module.make_kalshi_synthetic_barrier_identity(
            armed,
            barrier_nonce="receive-failed",
        )
        failed = module.transition_kalshi_candidate_protocol(
            armed,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.RECEIVE_FAILED,
                barrier_identity=barrier,
            ),
        )
        self.assertEqual(
            failed.state.physical_state,
            module.KalshiCandidatePhysicalStateV1.CLOSING,
        )
        self.assertEqual(
            failed.reason,
            module.KalshiCandidateProtocolReasonV1.RECEIVE_FAILED,
        )
        self.assertEqual(failed.state.pending_barrier_identity, barrier)

    def test_invalid_transition_returns_same_prior_state_object(self) -> None:
        module, connected = self._connected()
        transition = module.transition_kalshi_candidate_protocol(
            connected,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.OPEN_SUCCEEDED
            ),
        )
        self.assertIs(transition.state, connected)
        self.assertEqual(
            transition.action,
            module.KalshiCandidateProtocolActionV1.TRANSITION_REJECTED,
        )
        self.assertEqual(
            transition.reason,
            module.KalshiCandidateProtocolReasonV1.PROTOCOL_TRANSITION_INVALID,
        )

    def test_stale_barrier_cannot_set_or_clear_pending_identity(self) -> None:
        module, connected = self._connected()
        current = module.make_kalshi_synthetic_barrier_identity(
            connected,
            barrier_nonce="current",
        )
        stale = module.KalshiSyntheticBarrierIdentityV1._create(
            0,
            "stale",
        )
        for kind, barrier in (
            (module.KalshiCandidateProtocolEventKindV1.BARRIER_REQUIRED, stale),
            (module.KalshiCandidateProtocolEventKindV1.CLOSE_REQUESTED, stale),
        ):
            with self.subTest(kind=kind):
                transition = module.transition_kalshi_candidate_protocol(
                    connected,
                    module.KalshiCandidateProtocolEventV1._create(
                        kind,
                        barrier_identity=barrier,
                    ),
                )
                self.assertIs(transition.state, connected)
        pending = module.transition_kalshi_candidate_protocol(
            connected,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.BARRIER_REQUIRED,
                barrier_identity=current,
            ),
        ).state
        rejected = module.transition_kalshi_candidate_protocol(
            pending,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.BARRIER_WITNESSED,
                barrier_identity=stale,
            ),
        )
        self.assertIs(rejected.state, pending)

    def test_value_equal_separately_built_barriers_match(self) -> None:
        module, connected = self._connected()
        first = module.make_kalshi_synthetic_barrier_identity(
            connected,
            barrier_nonce="equal",
        )
        second = module.make_kalshi_synthetic_barrier_identity(
            connected,
            barrier_nonce="equal",
        )
        self.assertIsNot(first, second)
        pending = module.transition_kalshi_candidate_protocol(
            connected,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.BARRIER_REQUIRED,
                barrier_identity=first,
            ),
        ).state
        witnessed = module.transition_kalshi_candidate_protocol(
            pending,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.BARRIER_WITNESSED,
                barrier_identity=second,
            ),
        )
        self.assertIsNone(witnessed.state.pending_barrier_identity)

    def test_halt_is_terminal_and_close_uncertainty_halts(self) -> None:
        module, connected = self._connected()
        barrier = module.make_kalshi_synthetic_barrier_identity(
            connected,
            barrier_nonce="close-timeout",
        )
        closing = module.transition_kalshi_candidate_protocol(
            connected,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.CLOSE_REQUESTED,
                barrier_identity=barrier,
            ),
        ).state
        halted = module.transition_kalshi_candidate_protocol(
            closing,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.CLOSE_TIMEOUT
            ),
        )
        self.assertEqual(
            halted.reason,
            module.KalshiCandidateProtocolReasonV1.CLOSE_UNCERTAIN,
        )
        terminal = module.transition_kalshi_candidate_protocol(
            halted.state,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.OPEN_REQUESTED
            ),
        )
        self.assertIs(terminal.state, halted.state)
        self.assertEqual(
            terminal.action,
            module.KalshiCandidateProtocolActionV1.TRANSITION_REJECTED,
        )

    def test_generation_overflow_halts(self) -> None:
        module = _module(self)
        connecting = module.KalshiCandidateProtocolStateV1._create(
            module.KalshiCandidatePhysicalStateV1.CONNECTING,
            9_223_372_036_854_775_807,
            False,
            None,
        )
        result = module.transition_kalshi_candidate_protocol(
            connecting,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.OPEN_SUCCEEDED
            ),
        )
        self.assertEqual(
            result.reason,
            module.KalshiCandidateProtocolReasonV1.GENERATION_OVERFLOW,
        )
        self.assertEqual(
            result.state.physical_state,
            module.KalshiCandidatePhysicalStateV1.HALTED,
        )

    def test_protocol_digests_follow_literal_domains_and_projections(self) -> None:
        module, connected = self._connected()
        barrier = module.make_kalshi_synthetic_barrier_identity(
            connected,
            barrier_nonce="digest-1",
        )
        barrier_unsigned = {
            "schema_version": 1,
            "physical_connection_generation": 1,
            "barrier_nonce": "digest-1",
        }
        self.assertEqual(
            barrier.identity_sha256,
            sha256(
                b"INCI-KALSHI-SYNTHETIC-BARRIER-V1\0"
                + canonical_json_bytes(barrier_unsigned)
            ).hexdigest(),
        )
        state_unsigned = {
            "schema_version": 1,
            "physical_state": "CONNECTED",
            "physical_connection_generation": 1,
            "active_receive": False,
            "pending_barrier_identity": None,
        }
        self.assertEqual(
            connected.state_sha256,
            sha256(
                b"INCI-KALSHI-SYNTHETIC-PROTOCOL-STATE-V1\0"
                + canonical_json_bytes(state_unsigned)
            ).hexdigest(),
        )
        event = module.KalshiCandidateProtocolEventV1._create(
            module.KalshiCandidateProtocolEventKindV1.BARRIER_REQUIRED,
            barrier_identity=barrier,
        )
        barrier_full = dict(
            barrier_unsigned,
            identity_sha256=barrier.identity_sha256,
        )
        event_unsigned = {
            "schema_version": 1,
            "kind": "BARRIER_REQUIRED",
            "barrier_identity": barrier_full,
        }
        self.assertEqual(
            event.event_sha256,
            sha256(
                b"INCI-KALSHI-SYNTHETIC-PROTOCOL-EVENT-V1\0"
                + canonical_json_bytes(event_unsigned)
            ).hexdigest(),
        )
        transition = module.transition_kalshi_candidate_protocol(
            connected,
            event,
        )
        state_full = dict(
            state_unsigned,
            state_sha256=connected.state_sha256,
        )
        pending_unsigned = {
            "schema_version": 1,
            "physical_state": "CONNECTED",
            "physical_connection_generation": 1,
            "active_receive": False,
            "pending_barrier_identity": barrier_full,
        }
        pending_full = dict(
            pending_unsigned,
            state_sha256=transition.state.state_sha256,
        )
        event_full = dict(
            event_unsigned,
            event_sha256=event.event_sha256,
        )
        transition_unsigned = {
            "schema_version": 1,
            "prior_state": state_full,
            "event": event_full,
            "state": pending_full,
            "action": "BARRIER_REQUIRED",
            "reason": None,
        }
        self.assertEqual(
            transition.transition_sha256,
            sha256(
                b"INCI-KALSHI-SYNTHETIC-PROTOCOL-TRANSITION-V1\0"
                + canonical_json_bytes(transition_unsigned)
            ).hexdigest(),
        )

    def test_nonce_and_exact_type_guards(self) -> None:
        module, connected = self._connected()
        for invalid in ("", "-bad", "bad space", "x" * 65):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(
                    ValueError,
                    "kalshi_candidate_barrier_invalid",
                ):
                    module.make_kalshi_synthetic_barrier_identity(
                        connected,
                        barrier_nonce=invalid,
                    )
        with self.assertRaisesRegex(TypeError, "state"):
            module.transition_kalshi_candidate_protocol(
                object(),
                module.KalshiCandidateProtocolEventV1._create(
                    module.KalshiCandidateProtocolEventKindV1.OPEN_REQUESTED
                ),
            )
        with self.assertRaisesRegex(TypeError, "event"):
            module.transition_kalshi_candidate_protocol(connected, object())

    def test_event_barrier_presence_rules_are_exhaustive(self) -> None:
        module, connected = self._connected()
        barrier = module.make_kalshi_synthetic_barrier_identity(
            connected,
            barrier_nonce="presence",
        )
        required = {
            module.KalshiCandidateProtocolEventKindV1.RECEIVE_FAILED,
            module.KalshiCandidateProtocolEventKindV1.BARRIER_REQUIRED,
            module.KalshiCandidateProtocolEventKindV1.BARRIER_WITNESSED,
            module.KalshiCandidateProtocolEventKindV1.CLOSE_REQUESTED,
        }
        for kind in module.KalshiCandidateProtocolEventKindV1:
            with self.subTest(kind=kind):
                if kind in required:
                    with self.assertRaisesRegex(
                        ValueError,
                        "kalshi_candidate_contract_invalid",
                    ):
                        module.KalshiCandidateProtocolEventV1._create(kind)
                    self.assertEqual(
                        module.KalshiCandidateProtocolEventV1._create(
                            kind,
                            barrier_identity=barrier,
                        ).barrier_identity,
                        barrier,
                    )
                else:
                    module.KalshiCandidateProtocolEventV1._create(kind)
                    with self.assertRaisesRegex(
                        ValueError,
                        "kalshi_candidate_contract_invalid",
                    ):
                        module.KalshiCandidateProtocolEventV1._create(
                            kind,
                            barrier_identity=barrier,
                        )

    def test_forged_digest_bearing_values_are_rejected_before_parent_hash(
        self,
    ) -> None:
        module, connected = self._connected()
        barrier = module.make_kalshi_synthetic_barrier_identity(
            connected,
            barrier_nonce="forge",
        )
        object.__setattr__(barrier, "barrier_nonce", "changed")
        with self.assertRaisesRegex(
            ValueError,
            "kalshi_candidate_contract_invalid",
        ):
            module.KalshiCandidateProtocolStateV1._create(
                module.KalshiCandidatePhysicalStateV1.CONNECTED,
                1,
                False,
                barrier,
            )

        module, connected = self._connected()
        event = module.KalshiCandidateProtocolEventV1._create(
            module.KalshiCandidateProtocolEventKindV1.RECEIVE_ARMED
        )
        object.__setattr__(event, "kind", "RECEIVE_ARMED")
        with self.assertRaisesRegex(
            ValueError,
            "kalshi_candidate_contract_invalid",
        ):
            module.transition_kalshi_candidate_protocol(connected, event)

        object.__setattr__(connected, "active_receive", True)
        with self.assertRaisesRegex(
            ValueError,
            "kalshi_candidate_contract_invalid",
        ):
            module.make_kalshi_synthetic_barrier_identity(
                connected,
                barrier_nonce="forged-state",
            )

        module, connected = self._connected()
        transition = module.transition_kalshi_candidate_protocol(
            connected,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.RECEIVE_ARMED
            ),
        )
        object.__setattr__(transition, "action", "NONE")
        with self.assertRaisesRegex(
            ValueError,
            "kalshi_candidate_contract_invalid",
        ):
            module._validate_kalshi_candidate_protocol_transition(transition)

        result = _classify(self, _trade())
        object.__setattr__(result.frame, "price", Decimal("0.43"))
        with self.assertRaisesRegex(
            ValueError,
            "kalshi_candidate_contract_invalid",
        ):
            module.KalshiCandidateFrameResultV1._create(
                result.status,
                result.kind,
                result.scope,
                result.reason,
                result.raw_sha256,
                result.ticker,
                result.opaque_sequence,
                result.frame,
            )
        clean = _classify(self, _trade())
        object.__setattr__(clean, "raw_sha256", "0" * 64)
        with self.assertRaisesRegex(
            ValueError,
            "kalshi_candidate_contract_invalid",
        ):
            module.candidate_unsigned_frame_projection_v1(clean)

    def test_correctly_rehashed_but_semantically_wrong_transition_rejects(
        self,
    ) -> None:
        module, connected = self._connected()
        event = module.KalshiCandidateProtocolEventV1._create(
            module.KalshiCandidateProtocolEventKindV1.RECEIVE_ARMED
        )
        wrong_state = module.KalshiCandidateProtocolStateV1._create(
            module.KalshiCandidatePhysicalStateV1.CONNECTED,
            1,
            False,
            None,
        )
        with self.assertRaisesRegex(
            ValueError,
            "kalshi_candidate_contract_invalid",
        ):
            module.KalshiCandidateProtocolTransitionV1._create(
                connected,
                event,
                wrong_state,
                module.KalshiCandidateProtocolActionV1.NONE,
                None,
                module._TRANSITION_SENTINEL,
            )

    def test_remaining_protocol_table_edges(self) -> None:
        module, connected = self._connected()
        absent = module.initial_kalshi_candidate_protocol_state()
        connecting = module.transition_kalshi_candidate_protocol(
            absent,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.OPEN_REQUESTED
            ),
        ).state
        open_failed = module.transition_kalshi_candidate_protocol(
            connecting,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.OPEN_FAILED
            ),
        )
        self.assertEqual(
            (open_failed.action, open_failed.reason),
            (
                module.KalshiCandidateProtocolActionV1.HALTED,
                module.KalshiCandidateProtocolReasonV1.OPEN_FAILED,
            ),
        )

        barrier = module.make_kalshi_synthetic_barrier_identity(
            connected,
            barrier_nonce="table",
        )
        pending = module.transition_kalshi_candidate_protocol(
            connected,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.BARRIER_REQUIRED,
                barrier_identity=barrier,
            ),
        ).state
        for state in (
            module.transition_kalshi_candidate_protocol(
                connected,
                module.KalshiCandidateProtocolEventV1._create(
                    module.KalshiCandidateProtocolEventKindV1.RECEIVE_ARMED
                ),
            ).state,
            pending,
        ):
            with self.subTest(state=state):
                rejected = module.transition_kalshi_candidate_protocol(
                    state,
                    module.KalshiCandidateProtocolEventV1._create(
                        module.KalshiCandidateProtocolEventKindV1.RECEIVE_ARMED
                    ),
                )
                self.assertIs(rejected.state, state)

        active = module.transition_kalshi_candidate_protocol(
            connected,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.RECEIVE_ARMED
            ),
        ).state
        close_while_active = module.transition_kalshi_candidate_protocol(
            active,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.CLOSE_REQUESTED,
                barrier_identity=barrier,
            ),
        )
        self.assertIs(close_while_active.state, active)
        for kind in (
            module.KalshiCandidateProtocolEventKindV1.RECEIVE_COMPLETED,
            module.KalshiCandidateProtocolEventKindV1.RECEIVE_FAILED,
        ):
            event = module.KalshiCandidateProtocolEventV1._create(
                kind,
                barrier_identity=(
                    barrier
                    if kind
                    is module.KalshiCandidateProtocolEventKindV1.RECEIVE_FAILED
                    else None
                ),
            )
            rejected = module.transition_kalshi_candidate_protocol(
                connected,
                event,
            )
            self.assertIs(rejected.state, connected)

        connected_cleared = module.transition_kalshi_candidate_protocol(
            pending,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.BARRIER_WITNESSED,
                barrier_identity=barrier,
            ),
        ).state
        self.assertEqual(
            connected_cleared.physical_state,
            module.KalshiCandidatePhysicalStateV1.CONNECTED,
        )
        self.assertIsNone(connected_cleared.pending_barrier_identity)

        closing = module.transition_kalshi_candidate_protocol(
            pending,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.CLOSE_REQUESTED,
                barrier_identity=barrier,
            ),
        ).state
        closed_pending = module.transition_kalshi_candidate_protocol(
            closing,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.CLOSE_PROVEN
            ),
        ).state
        self.assertEqual(closed_pending.pending_barrier_identity, barrier)
        reopen_rejected = module.transition_kalshi_candidate_protocol(
            closed_pending,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.OPEN_REQUESTED
            ),
        )
        self.assertIs(reopen_rejected.state, closed_pending)
        cleared = module.transition_kalshi_candidate_protocol(
            closed_pending,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.BARRIER_WITNESSED,
                barrier_identity=barrier,
            ),
        ).state
        self.assertIsNone(cleared.pending_barrier_identity)

        uncertain = module.KalshiCandidateProtocolStateV1._create(
            module.KalshiCandidatePhysicalStateV1.UNCERTAIN,
            1,
            False,
            None,
        )
        closed_clear = module.transition_kalshi_candidate_protocol(
            closed_pending,
            module.KalshiCandidateProtocolEventV1._create(
                module.KalshiCandidateProtocolEventKindV1.BARRIER_WITNESSED,
                barrier_identity=barrier,
            ),
        ).state
        for state in (
            absent,
            connecting,
            connected,
            closing,
            closed_pending,
            closed_clear,
            uncertain,
        ):
            with self.subTest(halt_state=state.physical_state):
                halted = module.transition_kalshi_candidate_protocol(
                    state,
                    module.KalshiCandidateProtocolEventV1._create(
                        module.KalshiCandidateProtocolEventKindV1.HALT_REQUESTED
                    ),
                )
                self.assertEqual(
                    halted.state.physical_state,
                    module.KalshiCandidatePhysicalStateV1.HALTED,
                )

    def test_construction_provenance_is_live_only_and_reclaimable(self) -> None:
        module = _module(self)
        before = len(module._CONSTRUCTION_PROVENANCE)
        value = module.KalshiCandidateProtocolEventV1._create(
            module.KalshiCandidateProtocolEventKindV1.OPEN_REQUESTED
        )
        reference = weakref.ref(value)
        self.assertTrue(module._was_factory_built(value))
        self.assertGreaterEqual(
            len(module._CONSTRUCTION_PROVENANCE),
            before + 1,
        )
        del value
        gc.collect()
        self.assertIsNone(reference())
        self.assertLessEqual(
            len(module._CONSTRUCTION_PROVENANCE),
            before,
        )


class CandidateFrameClassifierTests(unittest.TestCase):
    def test_all_four_valid_frames_are_accepted_exactly(self) -> None:
        module = _module(self)
        cases = (
            (
                _snapshot(),
                module.KalshiCandidateFrameKindV1.ORDERBOOK_SNAPSHOT,
                module.KalshiCandidateOrderbookSnapshotV1,
            ),
            (
                _delta(),
                module.KalshiCandidateFrameKindV1.ORDERBOOK_DELTA,
                module.KalshiCandidateOrderbookDeltaV1,
            ),
            (
                _lifecycle(),
                module.KalshiCandidateFrameKindV1.MARKET_LIFECYCLE,
                module.KalshiCandidateMarketLifecycleV1,
            ),
            (
                _trade(),
                module.KalshiCandidateFrameKindV1.PUBLIC_TRADE,
                module.KalshiCandidatePublicTradeV1,
            ),
        )
        for document, kind, frame_type in cases:
            with self.subTest(kind=kind):
                result = _classify(self, document)
                self.assertEqual(
                    result.status,
                    module.KalshiCandidateParseStatusV1.ACCEPTED,
                )
                self.assertEqual(result.kind, kind)
                self.assertIsNone(result.scope)
                self.assertIsNone(result.reason)
                self.assertEqual(result.ticker, document["ticker"])
                self.assertEqual(
                    result.opaque_sequence,
                    document["sequence"],
                )
                self.assertIs(type(result.frame), frame_type)

    def test_hash_identity_precedes_raw_size_and_json_parsing(self) -> None:
        module = _module(self)
        payloads = (b"{" + b"x" * 1_048_576, b"{not-json")
        for payload in payloads:
            with self.subTest(size=len(payload)):
                result = module.classify_kalshi_synthetic_frame(
                    payload,
                    expected_raw_sha256="0" * 64,
                )
                self.assertEqual(
                    (result.scope, result.reason),
                    (
                        module.KalshiCandidateErrorScopeV1.CONNECTION,
                        module.KalshiCandidateRejectReasonV1.IDENTITY_INVALID,
                    ),
                )

    def test_expected_hash_type_and_format_are_identity_rejections(self) -> None:
        module = _module(self)
        payload = _raw(_snapshot())
        with self.assertRaisesRegex(TypeError, "expected_raw_sha256"):
            module.classify_kalshi_synthetic_frame(
                payload,
                expected_raw_sha256=bytes(32),
            )
        for expected in ("A" * 64, "0" * 63, "g" * 64):
            with self.subTest(expected=expected):
                result = module.classify_kalshi_synthetic_frame(
                    payload,
                    expected_raw_sha256=expected,
                )
                self.assertEqual(
                    result.reason,
                    module.KalshiCandidateRejectReasonV1.IDENTITY_INVALID,
                )

    def test_raw_capacity_boundary(self) -> None:
        module = _module(self)
        payload = b"{}" + b" " * (1_048_576 - 2)
        self.assertEqual(len(payload), 1_048_576)
        at_limit = module.classify_kalshi_synthetic_frame(
            payload,
            expected_raw_sha256=sha256(payload).hexdigest(),
        )
        self.assertNotEqual(
            at_limit.reason,
            module.KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
        )
        oversized = payload + b" "
        rejected = module.classify_kalshi_synthetic_frame(
            oversized,
            expected_raw_sha256=sha256(oversized).hexdigest(),
        )
        self.assertEqual(
            (rejected.scope, rejected.reason),
            (
                module.KalshiCandidateErrorScopeV1.CONNECTION,
                module.KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
            ),
        )

    def test_lexical_limits_for_depth_nodes_keys_strings_and_integers(self) -> None:
        module = _module(self)

        def classified(document: object):
            return _classify(self, document)

        nested: object = 0
        for _ in range(16):
            nested = [nested]
        depth_ok = classified(_snapshot(probe=nested))
        self.assertEqual(
            depth_ok.reason,
            module.KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
        )
        nested = [nested]
        self.assertEqual(
            classified(_snapshot(probe=nested)).reason,
            module.KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
        )

        nodes_ok = classified(_snapshot(probe=[0] * 8_167))
        self.assertEqual(
            nodes_ok.reason,
            module.KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
        )
        self.assertEqual(
            classified(_snapshot(probe=[0] * 8_168)).reason,
            module.KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
        )

        key_ok = classified(_snapshot(**{"x" * 128: 0}))
        self.assertEqual(
            key_ok.reason,
            module.KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
        )
        self.assertEqual(
            classified(_snapshot(**{"x" * 129: 0})).reason,
            module.KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
        )

        string_ok = classified(_snapshot(probe="é" * 2_048))
        self.assertEqual(
            string_ok.reason,
            module.KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
        )
        self.assertEqual(
            classified(_snapshot(probe="é" * 2_049)).reason,
            module.KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
        )

        lexical = (
            b'{"type":"ORDERBOOK_SNAPSHOT","ticker":"SAFE",'
            b'"sequence":"s","complete":true,"yes_bids":[],'
            b'"no_bids":[],"probe":10000000000000000000,"later":}'
        )
        result = module.classify_kalshi_synthetic_frame(
            lexical,
            expected_raw_sha256=sha256(lexical).hexdigest(),
        )
        self.assertEqual(
            result.reason,
            module.KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
        )

        for integer in (
            "01",
            "-0",
            "9223372036854775808",
            "-9223372036854775809",
        ):
            payload = (
                b'{"type":"ORDERBOOK_SNAPSHOT","ticker":"SAFE",'
                b'"sequence":"s","complete":true,"yes_bids":[],'
                b'"no_bids":[],"probe":'
                + integer.encode("ascii")
                + b"}"
            )
            with self.subTest(integer=integer):
                result = module.classify_kalshi_synthetic_frame(
                    payload,
                    expected_raw_sha256=sha256(payload).hexdigest(),
                )
                self.assertEqual(
                    result.reason,
                    module.KalshiCandidateRejectReasonV1.PAYLOAD_INVALID,
                )

    def test_malformed_json_duplicate_float_bool_surrogate_and_bom_reject(self) -> None:
        module = _module(self)
        payloads = (
            b'{"type":"ORDERBOOK_SNAPSHOT","type":"ORDERBOOK_SNAPSHOT"}',
            b'{"type":"ORDERBOOK_SNAPSHOT","probe":1.0}',
            b'{"type":"ORDERBOOK_SNAPSHOT","probe":true,"later":}',
            b'{"type":"ORDERBOOK_SNAPSHOT","ticker":"\\ud800"}',
            b"\xef\xbb\xbf{}",
        )
        for payload in payloads:
            with self.subTest(payload=payload[:40]):
                result = module.classify_kalshi_synthetic_frame(
                    payload,
                    expected_raw_sha256=sha256(payload).hexdigest(),
                )
                self.assertEqual(
                    (result.scope, result.reason),
                    (
                        module.KalshiCandidateErrorScopeV1.CONNECTION,
                        module.KalshiCandidateRejectReasonV1.PAYLOAD_INVALID,
                    ),
                )

    def test_lexical_first_fault_wins_before_later_capacity_or_syntax(
        self,
    ) -> None:
        module = _module(self)
        boolean_first = (
            b'{"type":"ORDERBOOK_SNAPSHOT","probe":true,"later":"'
            + b"x" * 4_097
            + b'"}'
        )
        integer_first = (
            b'{"type":"ORDERBOOK_SNAPSHOT","probe":'
            b"10000000000000000000x}"
        )
        invalid_first = (
            b'{"type":"ORDERBOOK_SNAPSHOT","probe":'
            b"x10000000000000000000}"
        )
        capacity_before_utf8 = (
            b'{"type":"ORDERBOOK_SNAPSHOT","probe":'
            b"10000000000000000000,"
            b'"later":"\xff"}'
        )
        first = module.classify_kalshi_synthetic_frame(
            boolean_first,
            expected_raw_sha256=sha256(boolean_first).hexdigest(),
        )
        second = module.classify_kalshi_synthetic_frame(
            integer_first,
            expected_raw_sha256=sha256(integer_first).hexdigest(),
        )
        third = module.classify_kalshi_synthetic_frame(
            invalid_first,
            expected_raw_sha256=sha256(invalid_first).hexdigest(),
        )
        fourth = module.classify_kalshi_synthetic_frame(
            capacity_before_utf8,
            expected_raw_sha256=sha256(capacity_before_utf8).hexdigest(),
        )
        self.assertEqual(
            first.reason,
            module.KalshiCandidateRejectReasonV1.PAYLOAD_INVALID,
        )
        self.assertEqual(
            second.reason,
            module.KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
        )
        self.assertEqual(
            third.reason,
            module.KalshiCandidateRejectReasonV1.PAYLOAD_INVALID,
        )
        self.assertEqual(
            fourth.reason,
            module.KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
        )

    def test_signed_64_integer_endpoints_are_lexically_accepted(self) -> None:
        module = _module(self)
        for integer in (
            "-9223372036854775808",
            "9223372036854775807",
        ):
            payload = (
                b'{"type":"ORDERBOOK_SNAPSHOT","ticker":"SAFE",'
                b'"sequence":"s","complete":true,"yes_bids":[],'
                b'"no_bids":[],"probe":'
                + integer.encode("ascii")
                + b"}"
            )
            with self.subTest(integer=integer):
                result = module.classify_kalshi_synthetic_frame(
                    payload,
                    expected_raw_sha256=sha256(payload).hexdigest(),
                )
                self.assertEqual(
                    (result.scope, result.reason),
                    (
                        module.KalshiCandidateErrorScopeV1.TICKER,
                        module.KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
                    ),
                )

    def test_unknown_type_and_identity_stage_precedence(self) -> None:
        module = _module(self)
        unknown = _classify(
            self,
            {
                "type": "NOT_SUPPORTED",
                "ticker": "SAFE",
                "sequence": "safe",
            },
        )
        self.assertEqual(
            (unknown.scope, unknown.reason, unknown.ticker),
            (
                module.KalshiCandidateErrorScopeV1.CONNECTION,
                module.KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
                None,
            ),
        )
        non_snapshot_complete = _classify(
            self,
            _trade(complete=True),
        )
        self.assertEqual(
            (
                non_snapshot_complete.scope,
                non_snapshot_complete.reason,
                non_snapshot_complete.ticker,
            ),
            (
                module.KalshiCandidateErrorScopeV1.TICKER,
                module.KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
                "SYNTHETIC-TENNIS-HOME",
            ),
        )
        unsafe = _snapshot(
            ticker="unsafe ticker",
            yes_bids=[{} for _ in range(1_025)],
        )
        result = _classify(self, unsafe)
        self.assertEqual(
            (result.scope, result.reason, result.ticker),
            (
                module.KalshiCandidateErrorScopeV1.CONNECTION,
                module.KalshiCandidateRejectReasonV1.IDENTITY_INVALID,
                None,
            ),
        )

    def test_schema_shape_and_ladder_capacity_precede_local_semantics(self) -> None:
        module = _module(self)
        extra = _classify(self, _snapshot(extra="value"))
        self.assertEqual(
            (extra.scope, extra.reason, extra.ticker),
            (
                module.KalshiCandidateErrorScopeV1.TICKER,
                module.KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
                "SYNTHETIC-TENNIS-HOME",
            ),
        )
        huge_bad = _snapshot(
            yes_bids=[
                {"price": "not-price", "quantity": "not-quantity"}
            ]
            * 1_025,
        )
        result = _classify(self, huge_bad)
        self.assertEqual(
            result.reason,
            module.KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
        )
        wrong_level = _classify(
            self,
            _snapshot(yes_bids=[{"price": "0.4", "size": "1"}]),
        )
        self.assertEqual(
            wrong_level.reason,
            module.KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
        )
        for document in (
            _snapshot(yes_bids={}),
            _snapshot(no_bids=None),
            {key: value for key, value in _snapshot().items() if key != "yes_bids"},
        ):
            with self.subTest(document=document):
                self.assertEqual(
                    _classify(self, document).reason,
                    module.KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
                )

        unsafe_extra = _classify(
            self,
            _snapshot(ticker="bad ticker", extra="value"),
        )
        self.assertEqual(
            (unsafe_extra.scope, unsafe_extra.reason),
            (
                module.KalshiCandidateErrorScopeV1.CONNECTION,
                module.KalshiCandidateRejectReasonV1.IDENTITY_INVALID,
            ),
        )

    def test_one_sided_1024_level_snapshot_is_accepted(self) -> None:
        levels = []
        for index in range(1_024):
            units = 10_000 - index
            price = (
                "1"
                if units == 10_000
                else "0." + f"{units:04d}".rstrip("0")
            )
            levels.append({"price": price, "quantity": "1"})
        result = _classify(
            self,
            _snapshot(yes_bids=levels, no_bids=[]),
        )
        self.assertEqual(result.status.value, "ACCEPTED")
        self.assertEqual(len(result.frame.yes_bids), 1_024)

    def test_snapshot_book_semantics(self) -> None:
        module = _module(self)
        cases = (
            _snapshot(complete=False),
            _snapshot(yes_bids=[{"price": "0.40", "quantity": "1"}]),
            _snapshot(yes_bids=[{"price": "0.4", "quantity": "0"}]),
            _snapshot(
                yes_bids=[
                    {"price": "0.4", "quantity": "1"},
                    {"price": "0.5", "quantity": "1"},
                ]
            ),
            _snapshot(
                yes_bids=[
                    {"price": "0.4", "quantity": "1"},
                    {"price": "0.4", "quantity": "2"},
                ]
            ),
            _snapshot(
                yes_bids=[{"price": "0.6", "quantity": "1"}],
                no_bids=[{"price": "0.5", "quantity": "1"}],
            ),
        )
        for document in cases:
            with self.subTest(document=document):
                result = _classify(self, document)
                self.assertEqual(
                    (result.scope, result.reason),
                    (
                        module.KalshiCandidateErrorScopeV1.TICKER,
                        module.KalshiCandidateRejectReasonV1.BOOK_INVALID,
                    ),
                )

    def test_delta_lifecycle_and_trade_semantics(self) -> None:
        module = _module(self)
        cases = (
            (_delta(side="yes"), module.KalshiCandidateRejectReasonV1.BOOK_INVALID),
            (_delta(price="1.001"), module.KalshiCandidateRejectReasonV1.BOOK_INVALID),
            (_delta(quantity_delta="0"), module.KalshiCandidateRejectReasonV1.BOOK_INVALID),
            (_lifecycle(status="PAUSED"), module.KalshiCandidateRejectReasonV1.PAYLOAD_INVALID),
            (_trade(trade_id="bad id"), module.KalshiCandidateRejectReasonV1.IDENTITY_INVALID),
            (_trade(side="yes"), module.KalshiCandidateRejectReasonV1.PAYLOAD_INVALID),
            (_trade(price="0.420"), module.KalshiCandidateRejectReasonV1.PAYLOAD_INVALID),
            (_trade(quantity="0"), module.KalshiCandidateRejectReasonV1.PAYLOAD_INVALID),
        )
        for document, reason in cases:
            with self.subTest(document=document):
                result = _classify(self, document)
                self.assertEqual(result.reason, reason)
                self.assertEqual(
                    result.scope,
                    module.KalshiCandidateErrorScopeV1.TICKER,
                )

    def test_decimal_boundaries_and_no_delete_semantics(self) -> None:
        accepted = (
            _delta(price="0", quantity_delta="0.0001"),
            _delta(price="1", quantity_delta="-1000000"),
            _trade(price="0.0001", quantity="1000000"),
        )
        for document in accepted:
            with self.subTest(document=document):
                self.assertEqual(
                    _classify(self, document).status.value,
                    "ACCEPTED",
                )
        rejected = (
            _delta(quantity_delta="-0"),
            _delta(quantity_delta="1000000.0001"),
            _trade(quantity="1000000.0001"),
        )
        for document in rejected:
            with self.subTest(document=document):
                self.assertEqual(
                    _classify(self, document).status.value,
                    "REJECTED",
                )

    def test_rejections_erase_connection_identity_and_never_echo_payload(self) -> None:
        module = _module(self)
        payload = _raw(
            {
                "type": "UNKNOWN",
                "ticker": "SECRET",
                "sequence": "SECRET",
                "api_key": "do-not-echo",
            }
        )
        result = module.classify_kalshi_synthetic_frame(
            payload,
            expected_raw_sha256=sha256(payload).hexdigest(),
        )
        self.assertIsNone(result.ticker)
        self.assertIsNone(result.opaque_sequence)
        self.assertIsNone(result.frame)
        self.assertNotIn("do-not-echo", repr(result))

    def test_secret_shaped_extra_keys_are_rejected_without_value_heuristics(
        self,
    ) -> None:
        module = _module(self)
        root_extra = _classify(
            self,
            _snapshot(api_key="do-not-retain"),
        )
        nested_extra = _classify(
            self,
            _snapshot(
                yes_bids=[
                    {
                        "price": "0.4",
                        "quantity": "1",
                        "authorization": "do-not-retain",
                    }
                ]
            ),
        )
        for result in (root_extra, nested_extra):
            self.assertEqual(
                result.reason,
                module.KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
            )
            self.assertIsNone(result.frame)
            self.assertNotIn("do-not-retain", repr(result))
        legitimate_value = _classify(
            self,
            _trade(trade_id="TOKEN"),
        )
        self.assertEqual(legitimate_value.status.value, "ACCEPTED")

    def test_frame_projection_and_digest_are_exact(self) -> None:
        module = _module(self)
        result = _classify(self, _trade())
        expected = {
            "schema_version": 1,
            "status": "ACCEPTED",
            "kind": "PUBLIC_TRADE",
            "scope": None,
            "reason": None,
            "raw_sha256": sha256(_raw(_trade())).hexdigest(),
            "ticker": "SYNTHETIC-TENNIS-HOME",
            "opaque_sequence": "synthetic-seq-4",
            "frame": {
                "schema_version": 1,
                "type": "PUBLIC_TRADE",
                "ticker": "SYNTHETIC-TENNIS-HOME",
                "sequence": "synthetic-seq-4",
                "trade_id": "SYNTHETIC-TRADE-1",
                "side": "YES",
                "price": "0.42",
                "quantity": "1.5",
            },
        }
        self.assertEqual(
            module.candidate_unsigned_frame_projection_v1(result),
            expected,
        )
        self.assertEqual(
            result.result_sha256,
            sha256(
                b"INCI-KALSHI-SYNTHETIC-CANDIDATE-FRAME-V1\0"
                + canonical_json_bytes(expected)
            ).hexdigest(),
        )

    def test_derived_projection_capacity_maps_to_connection_rejection(
        self,
    ) -> None:
        module = _module(self)
        document = _trade()
        normal = _classify(self, document)
        unsigned_size = len(
            canonical_json_bytes(
                module.candidate_unsigned_frame_projection_v1(normal)
            )
        )
        prior = module._MAX_CANONICAL_BYTES
        try:
            module._MAX_CANONICAL_BYTES = unsigned_size
            result = _classify(self, document)
        finally:
            module._MAX_CANONICAL_BYTES = prior
        self.assertEqual(
            (result.scope, result.reason),
            (
                module.KalshiCandidateErrorScopeV1.CONNECTION,
                module.KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
            ),
        )

    def test_exact_input_types(self) -> None:
        module = _module(self)
        with self.assertRaisesRegex(TypeError, "payload"):
            module.classify_kalshi_synthetic_frame(
                bytearray(b"{}"),
                expected_raw_sha256="0" * 64,
            )


class CandidateSchemaFixtureTests(unittest.TestCase):
    def test_synthetic_fixture_bytes_and_hashes_are_pinned(self) -> None:
        for filename, (raw_pin, file_pin) in FIXTURE_PINS.items():
            with self.subTest(filename=filename):
                path = FIXTURES / filename
                content = path.read_bytes()
                self.assertEqual(sha256(content).hexdigest(), file_pin)
                wrapper = _validate_fixture_bytes(content)
                self.assertEqual(
                    set(wrapper),
                    {
                        "fixture_kind",
                        "provenance",
                        "raw_sha256",
                        "payload",
                    },
                )
                self.assertEqual(
                    wrapper["fixture_kind"],
                    "kalshi_synthetic_candidate_v1",
                )
                self.assertEqual(wrapper["provenance"], PROVENANCE)
                self.assertEqual(wrapper["raw_sha256"], raw_pin)
                payload = canonical_json_bytes(wrapper["payload"])
                self.assertEqual(sha256(payload).hexdigest(), raw_pin)
                self.assertEqual(content, canonical_json_bytes(wrapper))
                mutated = dict(wrapper["payload"])
                mutated["ticker"] = "MUTATED"
                self.assertNotEqual(
                    sha256(canonical_json_bytes(mutated)).hexdigest(),
                    wrapper["raw_sha256"],
                )
                noncanonical = json.dumps(
                    wrapper,
                    indent=2,
                    sort_keys=False,
                ).encode("utf-8")
                self.assertNotEqual(noncanonical, content)

    def test_fixture_envelope_mutations_are_rejected(self) -> None:
        content = (
            FIXTURES
            / "kalshi_public_trade_synthetic_candidate_v1.json"
        ).read_bytes()
        wrapper = _validate_fixture_bytes(content)
        mutations: list[bytes] = []
        extra = dict(wrapper)
        extra["extra"] = "forbidden"
        mutations.append(canonical_json_bytes(extra))
        changed_provenance = dict(wrapper)
        changed_provenance["provenance"] = "changed"
        mutations.append(canonical_json_bytes(changed_provenance))
        changed_payload = dict(wrapper)
        changed_payload["payload"] = dict(wrapper["payload"])
        changed_payload["payload"]["quantity"] = "2"
        mutations.append(canonical_json_bytes(changed_payload))
        mutations.append(
            json.dumps(wrapper, indent=2, sort_keys=True).encode("utf-8")
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation[:40]):
                with self.assertRaisesRegex(
                    ValueError,
                    "fixture_invalid",
                ):
                    _validate_fixture_bytes(mutation)

    def test_all_fixture_payloads_classify_offline(self) -> None:
        module = _module(self)
        for filename in FIXTURE_PINS:
            with self.subTest(filename=filename):
                wrapper = _validate_fixture_bytes(
                    (FIXTURES / filename).read_bytes()
                )
                payload = canonical_json_bytes(wrapper["payload"])
                result = module.classify_kalshi_synthetic_frame(
                    payload,
                    expected_raw_sha256=wrapper["raw_sha256"],
                )
                self.assertEqual(
                    result.status,
                    module.KalshiCandidateParseStatusV1.ACCEPTED,
                )

    def test_schema_files_are_closed_synthetic_contracts(self) -> None:
        names = (
            "kalshi-orderbook-snapshot-synthetic-candidate-v1.schema.json",
            "kalshi-orderbook-delta-synthetic-candidate-v1.schema.json",
            "kalshi-market-lifecycle-synthetic-candidate-v1.schema.json",
            "kalshi-public-trade-synthetic-candidate-v1.schema.json",
        )
        for name in names:
            with self.subTest(name=name):
                schema = json.loads((SCHEMAS / name).read_bytes())
                self.assertEqual(schema["type"], "object")
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(
                    set(schema["required"]),
                    set(schema["properties"]),
                )
                self.assertIn("Synthetic", schema["description"])
                self.assertIn("not", schema["description"])
                if "yes_bids" in schema["properties"]:
                    for ladder in ("yes_bids", "no_bids"):
                        level = schema["properties"][ladder]["items"]
                        self.assertEqual(level["type"], "object")
                        self.assertFalse(level["additionalProperties"])
                        self.assertEqual(
                            set(level["required"]),
                            set(level["properties"]),
                        )


if __name__ == "__main__":
    unittest.main()
