from __future__ import annotations

import ast
from dataclasses import fields
from decimal import (
    Decimal,
    ROUND_DOWN,
    getcontext,
    setcontext,
)
import json
from pathlib import Path
import unittest
from unittest import mock

from inci_tennis_expert.contracts import (
    BookDelta,
    BookEventKind,
    BookLevel,
    BookSnapshot,
    BookState,
    BookTransitionResult,
    ContractSide,
    ExpertContractError,
    MarketLifecycle,
    MarketStatus,
    canonical_expert_bytes,
    expert_contract_sha256,
)
from inci_tennis_expert.market_book import (
    apply_book_delta,
    apply_book_snapshot,
    apply_market_lifecycle,
    book_from_snapshot,
    executable_buy,
    require_book_resnapshot,
)


FIXTURE_DIR = Path(__file__).with_name("fixtures")
ALLOWED_STATUS_TRANSITIONS = {
    MarketStatus.PREOPEN: {
        MarketStatus.PREOPEN,
        MarketStatus.OPEN,
        MarketStatus.SUSPENDED,
        MarketStatus.CLOSED,
        MarketStatus.CANCELLED,
    },
    MarketStatus.OPEN: {
        MarketStatus.OPEN,
        MarketStatus.SUSPENDED,
        MarketStatus.CLOSED,
        MarketStatus.CANCELLED,
    },
    MarketStatus.SUSPENDED: {
        MarketStatus.SUSPENDED,
        MarketStatus.OPEN,
        MarketStatus.CLOSED,
        MarketStatus.CANCELLED,
    },
    MarketStatus.CLOSED: {
        MarketStatus.CLOSED,
        MarketStatus.SETTLED,
        MarketStatus.CANCELLED,
    },
    MarketStatus.SETTLED: {MarketStatus.SETTLED},
    MarketStatus.CANCELLED: {MarketStatus.CANCELLED},
}


def level(price: str, quantity: str = "1") -> BookLevel:
    return BookLevel(Decimal(price), Decimal(quantity))


def snapshot(**changes: object) -> BookSnapshot:
    values: dict[str, object] = {
        "ticker": "MATCH-HOME",
        "connection_epoch": 1,
        "sequence": 10,
        "market_status": MarketStatus.OPEN,
        "scheduled_close_wall_ns": 10_000,
        "source_wall_ns": 100,
        "observed_monotonic_ns": 200,
        "clock_uncertainty_ns": 2,
        "yes_bids": (level("0.40", "5"), level("0.35", "2")),
        "no_bids": (level("0.45", "2"), level("0.44", "4")),
    }
    values.update(changes)
    return BookSnapshot(**values)  # type: ignore[arg-type]


def delta(**changes: object) -> BookDelta:
    values: dict[str, object] = {
        "ticker": "MATCH-HOME",
        "connection_epoch": 1,
        "sequence": 11,
        "source_wall_ns": 101,
        "observed_monotonic_ns": 201,
        "clock_uncertainty_ns": 3,
        "contract_side": ContractSide.YES,
        "price": Decimal("0.40"),
        "quantity": Decimal("3"),
    }
    values.update(changes)
    return BookDelta(**values)  # type: ignore[arg-type]


def lifecycle(**changes: object) -> MarketLifecycle:
    values: dict[str, object] = {
        "ticker": "MATCH-HOME",
        "connection_epoch": 1,
        "market_status": MarketStatus.SUSPENDED,
        "scheduled_close_wall_ns": 10_100,
        "source_wall_ns": 102,
        "observed_monotonic_ns": 202,
        "clock_uncertainty_ns": 4,
    }
    values.update(changes)
    return MarketLifecycle(**values)  # type: ignore[arg-type]


def initial_state(**snapshot_changes: object) -> BookState:
    return book_from_snapshot(snapshot(**snapshot_changes)).state


def changed_state(**changes: object) -> BookState:
    state = initial_state()
    values = {field.name: getattr(state, field.name) for field in fields(state)}
    values.update(changes)
    return BookState(**values)  # type: ignore[arg-type]


def subclass_instance(value: object) -> object:
    subclass = type(
        f"{type(value).__name__}Subclass",
        (type(value),),
        {"__slots__": ()},
    )
    instance = object.__new__(subclass)
    for field in fields(value):
        object.__setattr__(instance, field.name, getattr(value, field.name))
    return instance


def assert_no_event(
    case: unittest.TestCase,
    result: BookTransitionResult,
) -> None:
    case.assertIsNone(result.accepted_event_kind)
    case.assertIsNone(result.accepted_event_sha256)
    case.assertEqual(result.executable_move, Decimal("0"))
    case.assertIsNone(result.move_observed_monotonic_ns)
    case.assertFalse(result.top_of_book_changed)
    case.assertEqual(result.connection_epoch, result.state.connection_epoch)
    case.assertEqual(result.sequence, result.state.sequence)


def assert_gapped_only(
    case: unittest.TestCase,
    before: BookState,
    after: BookState,
) -> None:
    for field in fields(before):
        actual = getattr(after, field.name)
        if field.name == "trusted":
            case.assertIs(actual, False)
        elif field.name == "sequence_gap":
            case.assertIs(actual, True)
        else:
            case.assertEqual(actual, getattr(before, field.name), field.name)


def expected_move(
    before: BookState,
    after: BookState,
) -> Decimal:
    def ask(book: BookState, side: ContractSide) -> Decimal | None:
        bids = book.no_bids if side is ContractSide.YES else book.yes_bids
        return Decimal("1") - bids[0].price if bids else None

    moves: list[Decimal] = []
    for side in ContractSide:
        old = ask(before, side)
        new = ask(after, side)
        if old is None and new is None:
            moves.append(Decimal("0"))
        elif old is None or new is None:
            moves.append(Decimal("1"))
        else:
            moves.append(abs(new - old))
    return max(moves)


class SnapshotAndTrustBarrierTests(unittest.TestCase):
    def test_initial_snapshot_maps_every_field_and_emits_exact_witness(self) -> None:
        incoming = snapshot()
        digest = expert_contract_sha256(incoming)
        result = book_from_snapshot(incoming)
        expected = BookState(
            ticker="MATCH-HOME",
            connection_epoch=1,
            sequence=10,
            market_status=MarketStatus.OPEN,
            scheduled_close_wall_ns=10_000,
            book_source_wall_ns=100,
            book_observed_monotonic_ns=200,
            book_clock_uncertainty_ns=2,
            lifecycle_source_wall_ns=100,
            lifecycle_observed_monotonic_ns=200,
            lifecycle_clock_uncertainty_ns=2,
            yes_bids=(level("0.40", "5"), level("0.35", "2")),
            no_bids=(level("0.45", "2"), level("0.44", "4")),
            trusted=True,
            sequence_gap=False,
            last_executable_move=Decimal("0"),
            last_executable_move_monotonic_ns=200,
            last_snapshot_sha256=digest,
            last_event_sha256=digest,
        )
        self.assertEqual(result.state, expected)
        self.assertIs(result.accepted_event_kind, BookEventKind.SNAPSHOT)
        self.assertEqual(result.accepted_event_sha256, digest)
        self.assertEqual(result.executable_move, Decimal("0"))
        self.assertEqual(result.move_observed_monotonic_ns, 200)
        self.assertEqual(result.connection_epoch, 1)
        self.assertEqual(result.sequence, 10)
        self.assertFalse(result.top_of_book_changed)

    def test_initial_snapshot_accepts_empty_one_sided_and_non_open_books(self) -> None:
        cases = (
            {
                "yes_bids": (),
                "no_bids": (),
                "market_status": MarketStatus.PREOPEN,
            },
            {"yes_bids": (), "no_bids": (level("0.45"),)},
            {"yes_bids": (level("0.40"),), "no_bids": ()},
            {
                "yes_bids": (),
                "no_bids": (),
                "market_status": MarketStatus.SETTLED,
            },
        )
        for changes in cases:
            with self.subTest(changes=changes):
                result = book_from_snapshot(snapshot(**changes))
                self.assertTrue(result.state.trusted)
                self.assertFalse(result.state.sequence_gap)
                self.assertIs(result.state.market_status, changes.get(
                    "market_status",
                    MarketStatus.OPEN,
                ))

    def test_snapshot_and_resnapshot_exact_type_boundaries(self) -> None:
        incoming = snapshot()
        state = initial_state()
        for value in ({}, subclass_instance(incoming)):
            with self.subTest(snapshot=type(value).__name__):
                with self.assertRaisesRegex(TypeError, "^snapshot$"):
                    book_from_snapshot(value)  # type: ignore[arg-type]
        for value in ({}, subclass_instance(state)):
            with self.subTest(state=type(value).__name__):
                with self.assertRaisesRegex(TypeError, "^state$"):
                    require_book_resnapshot(value)  # type: ignore[arg-type]
        gapped = require_book_resnapshot(state)
        with self.assertRaisesRegex(TypeError, "^state$"):
            apply_book_snapshot(
                subclass_instance(gapped),
                incoming,
            )  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "^snapshot$"):
            apply_book_snapshot(
                gapped,
                subclass_instance(incoming),
            )  # type: ignore[arg-type]

    def test_resnapshot_barrier_is_explicit_preserving_and_idempotent(self) -> None:
        state = initial_state()
        gapped = require_book_resnapshot(state)
        self.assertIsNot(gapped, state)
        assert_gapped_only(self, state, gapped)
        self.assertIs(require_book_resnapshot(gapped), gapped)

    def test_replacement_snapshot_precedence_table(self) -> None:
        trusted = initial_state()
        gapped = require_book_resnapshot(trusted)
        cases = (
            (
                object(),
                object(),
                TypeError,
                "state",
            ),
            (
                trusted,
                object(),
                TypeError,
                "snapshot",
            ),
            (
                trusted,
                snapshot(ticker="OTHER", connection_epoch=0),
                ExpertContractError,
                "book_ticker_mismatch",
            ),
            (
                trusted,
                snapshot(connection_epoch=0),
                ExpertContractError,
                "book_snapshot_not_required",
            ),
            (
                gapped,
                snapshot(
                    connection_epoch=0,
                    observed_monotonic_ns=1,
                ),
                ExpertContractError,
                "book_snapshot_epoch_stale",
            ),
            (
                gapped,
                snapshot(
                    connection_epoch=1,
                    observed_monotonic_ns=1,
                ),
                ExpertContractError,
                "book_snapshot_epoch_not_newer",
            ),
            (
                gapped,
                snapshot(
                    connection_epoch=2,
                    observed_monotonic_ns=199,
                    market_status=MarketStatus.SETTLED,
                ),
                ExpertContractError,
                "book_time_regression",
            ),
        )
        for state, incoming, error, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(error, f"^{message}$"):
                    apply_book_snapshot(state, incoming)  # type: ignore[arg-type]

    def test_replacement_time_must_not_regress_against_either_clock(self) -> None:
        bases = (
            changed_state(
                book_observed_monotonic_ns=300,
                lifecycle_observed_monotonic_ns=250,
                last_executable_move_monotonic_ns=200,
                trusted=False,
                sequence_gap=True,
            ),
            changed_state(
                book_observed_monotonic_ns=250,
                lifecycle_observed_monotonic_ns=300,
                last_executable_move_monotonic_ns=200,
                trusted=False,
                sequence_gap=True,
            ),
        )
        for base in bases:
            with self.subTest(
                book=base.book_observed_monotonic_ns,
                lifecycle=base.lifecycle_observed_monotonic_ns,
            ):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "^book_time_regression$",
                ):
                    apply_book_snapshot(
                        base,
                        snapshot(
                            connection_epoch=2,
                            observed_monotonic_ns=299,
                        ),
                    )

    def test_replacement_clock_graph_collisions_and_max_equality_are_exact(
        self,
    ) -> None:
        clock_cases = (
            changed_state(
                market_status=MarketStatus.OPEN,
                book_observed_monotonic_ns=300,
                lifecycle_observed_monotonic_ns=250,
                last_executable_move_monotonic_ns=200,
                trusted=False,
                sequence_gap=True,
            ),
            changed_state(
                market_status=MarketStatus.OPEN,
                book_observed_monotonic_ns=250,
                lifecycle_observed_monotonic_ns=300,
                last_executable_move_monotonic_ns=200,
                trusted=False,
                sequence_gap=True,
            ),
        )
        for base in clock_cases:
            controlling_clock = max(
                base.book_observed_monotonic_ns,
                base.lifecycle_observed_monotonic_ns,
            )
            with self.subTest(
                book=base.book_observed_monotonic_ns,
                lifecycle=base.lifecycle_observed_monotonic_ns,
                boundary="regression_plus_invalid_graph",
            ):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "^book_time_regression$",
                ):
                    apply_book_snapshot(
                        base,
                        snapshot(
                            connection_epoch=2,
                            observed_monotonic_ns=controlling_clock - 1,
                            market_status=MarketStatus.SETTLED,
                        ),
                    )
            with self.subTest(
                book=base.book_observed_monotonic_ns,
                lifecycle=base.lifecycle_observed_monotonic_ns,
                boundary="equality_at_maximum",
            ):
                accepted = apply_book_snapshot(
                    base,
                    snapshot(
                        connection_epoch=2,
                        observed_monotonic_ns=controlling_clock,
                        market_status=MarketStatus.SUSPENDED,
                    ),
                )
                self.assertIs(
                    accepted.accepted_event_kind,
                    BookEventKind.SNAPSHOT,
                )
                self.assertEqual(
                    accepted.state.book_observed_monotonic_ns,
                    controlling_clock,
                )
                self.assertEqual(
                    accepted.state.lifecycle_observed_monotonic_ns,
                    controlling_clock,
                )

    def test_invalid_replacement_graph_precedes_move_and_digest_work(self) -> None:
        base = changed_state(
            market_status=MarketStatus.SETTLED,
            trusted=False,
            sequence_gap=True,
        )
        incoming = snapshot(
            connection_epoch=2,
            observed_monotonic_ns=300,
            market_status=MarketStatus.OPEN,
            yes_bids=(),
            no_bids=(
                BookLevel(
                    Decimal("0.5"),
                    Decimal("1E+1000001"),
                ),
            ),
        )
        result = apply_book_snapshot(base, incoming)
        assert_no_event(self, result)
        assert_gapped_only(self, base, result.state)

    def test_new_epoch_snapshot_restores_trust_and_resets_sequence(self) -> None:
        old = require_book_resnapshot(initial_state())
        incoming = snapshot(
            connection_epoch=2,
            sequence=3,
            source_wall_ns=400,
            observed_monotonic_ns=401,
            clock_uncertainty_ns=5,
            scheduled_close_wall_ns=20_000,
            yes_bids=(level("0.42", "7"),),
            no_bids=(level("0.43", "8"),),
        )
        result = apply_book_snapshot(old, incoming)
        self.assertTrue(result.state.trusted)
        self.assertFalse(result.state.sequence_gap)
        self.assertEqual(result.state.connection_epoch, 2)
        self.assertEqual(result.state.sequence, 3)
        self.assertEqual(result.state.yes_bids, incoming.yes_bids)
        self.assertEqual(result.state.no_bids, incoming.no_bids)
        self.assertEqual(
            result.executable_move,
            expected_move(old, result.state),
        )
        self.assertEqual(result.move_observed_monotonic_ns, 401)
        self.assertEqual(
            result.state.last_executable_move_monotonic_ns,
            401,
        )
        self.assertIs(result.accepted_event_kind, BookEventKind.SNAPSHOT)
        self.assertEqual(
            result.accepted_event_sha256,
            expert_contract_sha256(incoming),
        )
        self.assertEqual(
            result.state.last_snapshot_sha256,
            result.accepted_event_sha256,
        )
        self.assertEqual(
            result.state.last_event_sha256,
            result.accepted_event_sha256,
        )

    def test_all_replacement_lifecycle_pairs_follow_exact_graph(self) -> None:
        for current in MarketStatus:
            for incoming_status in MarketStatus:
                base = changed_state(
                    market_status=current,
                    trusted=False,
                    sequence_gap=True,
                )
                incoming = snapshot(
                    connection_epoch=2,
                    observed_monotonic_ns=300,
                    market_status=incoming_status,
                )
                with self.subTest(current=current, incoming=incoming_status):
                    result = apply_book_snapshot(base, incoming)
                    if incoming_status in ALLOWED_STATUS_TRANSITIONS[current]:
                        self.assertIs(
                            result.accepted_event_kind,
                            BookEventKind.SNAPSHOT,
                        )
                        self.assertTrue(result.state.trusted)
                        self.assertIs(
                            result.state.market_status,
                            incoming_status,
                        )
                    else:
                        assert_no_event(self, result)
                        assert_gapped_only(self, base, result.state)


class DeltaReductionTests(unittest.TestCase):
    def test_absolute_insert_replace_delete_and_sort_both_ladders(self) -> None:
        for side, field_name in (
            (ContractSide.YES, "yes_bids"),
            (ContractSide.NO, "no_bids"),
        ):
            with self.subTest(side=side):
                state = initial_state()
                inserted = apply_book_delta(
                    state,
                    delta(
                        contract_side=side,
                        price=Decimal("0.38"),
                        quantity=Decimal("9"),
                    ),
                )
                ladder = getattr(inserted.state, field_name)
                self.assertEqual(
                    tuple(item.price for item in ladder),
                    tuple(sorted(
                        (item.price for item in ladder),
                        reverse=True,
                    )),
                )
                self.assertIn(level("0.38", "9"), ladder)
                replaced = apply_book_delta(
                    inserted.state,
                    delta(
                        sequence=12,
                        observed_monotonic_ns=202,
                        contract_side=side,
                        price=Decimal("0.38"),
                        quantity=Decimal("7"),
                    ),
                )
                self.assertIn(level("0.38", "7"), getattr(
                    replaced.state,
                    field_name,
                ))
                removed = apply_book_delta(
                    replaced.state,
                    delta(
                        sequence=13,
                        observed_monotonic_ns=203,
                        contract_side=side,
                        price=Decimal("0.38"),
                        quantity=Decimal("0"),
                    ),
                )
                self.assertNotIn(
                    Decimal("0.38"),
                    tuple(
                        item.price
                        for item in getattr(removed.state, field_name)
                    ),
                )

    def test_deleting_best_level_changes_reciprocal_executable_ask(self) -> None:
        state = initial_state()
        result = apply_book_delta(
            state,
            delta(
                contract_side=ContractSide.NO,
                price=Decimal("0.45"),
                quantity=Decimal("0"),
            ),
        )
        self.assertEqual(result.state.no_bids[0].price, Decimal("0.44"))
        self.assertEqual(result.executable_move, Decimal("0.01"))
        self.assertTrue(result.top_of_book_changed)

    def test_nonexistent_delete_and_crossed_result_gap_without_mutation(self) -> None:
        state = initial_state()
        for incoming in (
            delta(price=Decimal("0.39"), quantity=Decimal("0")),
            delta(price=Decimal("0.60"), quantity=Decimal("1")),
        ):
            with self.subTest(incoming=incoming):
                result = apply_book_delta(state, incoming)
                assert_no_event(self, result)
                assert_gapped_only(self, state, result.state)

    def test_delta_epoch_sequence_and_time_fail_closed(self) -> None:
        state = initial_state()
        old_epoch = delta(connection_epoch=0)
        with self.assertRaisesRegex(
            ExpertContractError,
            "^book_epoch_stale$",
        ):
            apply_book_delta(state, old_epoch)
        gap_cases = (
            delta(connection_epoch=2),
            delta(observed_monotonic_ns=199),
            delta(sequence=10),
            delta(sequence=9),
            delta(sequence=12),
        )
        for incoming in gap_cases:
            with self.subTest(incoming=incoming):
                result = apply_book_delta(state, incoming)
                assert_no_event(self, result)
                assert_gapped_only(self, state, result.state)
        gapped = require_book_resnapshot(state)
        with self.assertRaisesRegex(
            ExpertContractError,
            "^book_resnapshot_required$",
        ):
            apply_book_delta(gapped, delta(connection_epoch=0))

    def test_delta_exact_type_and_precedence_table(self) -> None:
        trusted = initial_state()
        gapped = require_book_resnapshot(trusted)
        valid_delta = delta()
        cases = (
            (object(), object(), TypeError, "state"),
            (trusted, object(), TypeError, "delta"),
            (
                trusted,
                delta(ticker="OTHER", connection_epoch=0),
                ExpertContractError,
                "book_ticker_mismatch",
            ),
            (
                gapped,
                delta(connection_epoch=0),
                ExpertContractError,
                "book_resnapshot_required",
            ),
            (
                trusted,
                delta(
                    connection_epoch=0,
                    observed_monotonic_ns=1,
                    sequence=1,
                ),
                ExpertContractError,
                "book_epoch_stale",
            ),
        )
        for state, incoming, error, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(error, f"^{message}$"):
                    apply_book_delta(state, incoming)  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "^state$"):
            apply_book_delta(
                subclass_instance(trusted),
                valid_delta,
            )  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "^delta$"):
            apply_book_delta(
                trusted,
                subclass_instance(valid_delta),
            )  # type: ignore[arg-type]

    def test_delta_precedence_gap_rows_preserve_every_accepted_field(self) -> None:
        state = initial_state()
        cases = (
            delta(
                connection_epoch=2,
                observed_monotonic_ns=1,
                sequence=1,
                price=Decimal("0.39"),
                quantity=Decimal("0"),
            ),
            delta(
                observed_monotonic_ns=199,
                sequence=9,
                price=Decimal("0.39"),
                quantity=Decimal("0"),
            ),
            delta(
                sequence=10,
                price=Decimal("0.39"),
                quantity=Decimal("0"),
            ),
            delta(
                sequence=12,
                price=Decimal("0.60"),
                quantity=Decimal("1"),
            ),
        )
        for incoming in cases:
            with self.subTest(incoming=incoming):
                result = apply_book_delta(state, incoming)
                assert_no_event(self, result)
                assert_gapped_only(self, state, result.state)

    def test_accepted_delta_updates_only_ruled_fields_and_digest(self) -> None:
        before = initial_state()
        incoming = delta(
            contract_side=ContractSide.NO,
            price=Decimal("0.45"),
            quantity=Decimal("1"),
        )
        result = apply_book_delta(before, incoming)
        self.assertEqual(result.state.connection_epoch, 1)
        self.assertEqual(result.state.sequence, 11)
        self.assertEqual(result.state.book_source_wall_ns, 101)
        self.assertEqual(result.state.book_observed_monotonic_ns, 201)
        self.assertEqual(result.state.book_clock_uncertainty_ns, 3)
        for name in (
            "market_status",
            "scheduled_close_wall_ns",
            "lifecycle_source_wall_ns",
            "lifecycle_observed_monotonic_ns",
            "lifecycle_clock_uncertainty_ns",
            "last_snapshot_sha256",
        ):
            self.assertEqual(
                getattr(result.state, name),
                getattr(before, name),
                name,
            )
        digest = expert_contract_sha256(incoming)
        self.assertEqual(result.state.last_event_sha256, digest)
        self.assertEqual(result.accepted_event_sha256, digest)
        self.assertIs(result.accepted_event_kind, BookEventKind.DELTA)
        self.assertEqual(result.executable_move, Decimal("0"))
        self.assertEqual(result.move_observed_monotonic_ns, 201)
        self.assertFalse(result.top_of_book_changed)

    def test_positive_delta_witness_is_event_local_and_gap_preserves_it(self) -> None:
        incoming = delta(
            contract_side=ContractSide.NO,
            price=Decimal("0.45"),
            quantity=Decimal("0"),
        )
        result = apply_book_delta(initial_state(), incoming)
        digest = expert_contract_sha256(incoming)
        self.assertIs(result.accepted_event_kind, BookEventKind.DELTA)
        self.assertEqual(result.accepted_event_sha256, digest)
        self.assertEqual(result.executable_move, Decimal("0.01"))
        self.assertEqual(result.move_observed_monotonic_ns, 201)
        self.assertEqual(result.connection_epoch, 1)
        self.assertEqual(result.sequence, 11)
        self.assertTrue(result.top_of_book_changed)
        self.assertEqual(result.state.last_executable_move, Decimal("0.01"))
        self.assertEqual(
            result.state.last_executable_move_monotonic_ns,
            201,
        )
        gap = apply_book_delta(
            result.state,
            delta(sequence=13, observed_monotonic_ns=202),
        )
        assert_no_event(self, gap)
        self.assertEqual(gap.state.last_executable_move, Decimal("0.01"))
        self.assertEqual(
            gap.state.last_executable_move_monotonic_ns,
            201,
        )
        self.assertEqual(gap.state.last_event_sha256, digest)

    def test_move_metric_covers_both_sides_max_appearance_and_deep_zero(self) -> None:
        cases = (
            (
                snapshot(),
                delta(
                    contract_side=ContractSide.NO,
                    price=Decimal("0.45"),
                    quantity=Decimal("0"),
                ),
                Decimal("0.01"),
            ),
            (
                snapshot(),
                delta(
                    contract_side=ContractSide.YES,
                    price=Decimal("0.40"),
                    quantity=Decimal("0"),
                ),
                Decimal("0.05"),
            ),
            (
                snapshot(),
                delta(
                    contract_side=ContractSide.YES,
                    price=Decimal("0.30"),
                    quantity=Decimal("1"),
                ),
                Decimal("0"),
            ),
            (
                snapshot(yes_bids=()),
                delta(
                    contract_side=ContractSide.YES,
                    price=Decimal("0.40"),
                    quantity=Decimal("1"),
                ),
                Decimal("1"),
            ),
            (
                snapshot(no_bids=(level("0.45"),)),
                delta(
                    contract_side=ContractSide.NO,
                    price=Decimal("0.45"),
                    quantity=Decimal("0"),
                ),
                Decimal("1"),
            ),
        )
        for origin, incoming, want in cases:
            with self.subTest(want=want, incoming=incoming):
                before = book_from_snapshot(origin).state
                result = apply_book_delta(before, incoming)
                self.assertEqual(result.executable_move, want)
                self.assertEqual(result.state.last_executable_move, want)
                self.assertEqual(result.move_observed_monotonic_ns, 201)
                self.assertEqual(
                    result.state.last_executable_move_monotonic_ns,
                    201,
                )
                self.assertEqual(result.top_of_book_changed, want > 0)


class LifecycleReductionTests(unittest.TestCase):
    def test_newer_lifecycle_updates_only_lifecycle_fields_and_digest(self) -> None:
        before = initial_state()
        incoming = lifecycle()
        result = apply_market_lifecycle(before, incoming)
        digest = expert_contract_sha256(incoming)
        self.assertIs(result.accepted_event_kind, BookEventKind.LIFECYCLE)
        self.assertEqual(result.accepted_event_sha256, digest)
        self.assertEqual(result.state.last_event_sha256, digest)
        self.assertIs(result.state.market_status, MarketStatus.SUSPENDED)
        self.assertEqual(result.state.scheduled_close_wall_ns, 10_100)
        self.assertEqual(result.state.lifecycle_source_wall_ns, 102)
        self.assertEqual(result.state.lifecycle_observed_monotonic_ns, 202)
        self.assertEqual(result.state.lifecycle_clock_uncertainty_ns, 4)
        for name in (
            "connection_epoch",
            "sequence",
            "book_source_wall_ns",
            "book_observed_monotonic_ns",
            "book_clock_uncertainty_ns",
            "yes_bids",
            "no_bids",
            "trusted",
            "sequence_gap",
            "last_executable_move",
            "last_executable_move_monotonic_ns",
            "last_snapshot_sha256",
        ):
            self.assertEqual(
                getattr(result.state, name),
                getattr(before, name),
                name,
            )
        self.assertEqual(result.executable_move, Decimal("0"))
        self.assertIsNone(result.move_observed_monotonic_ns)
        self.assertFalse(result.top_of_book_changed)

    def test_lifecycle_idempotence_conflict_epoch_and_time_rules(self) -> None:
        state = initial_state()
        exact = lifecycle(
            market_status=state.market_status,
            scheduled_close_wall_ns=state.scheduled_close_wall_ns,
            source_wall_ns=state.lifecycle_source_wall_ns,
            observed_monotonic_ns=state.lifecycle_observed_monotonic_ns,
            clock_uncertainty_ns=state.lifecycle_clock_uncertainty_ns,
        )
        duplicate = apply_market_lifecycle(state, exact)
        self.assertIs(duplicate.state, state)
        assert_no_event(self, duplicate)
        with mock.patch(
            "inci_tennis_expert.market_book._status_transition_allowed",
            side_effect=AssertionError("later branch executed"),
        ) as status_transition:
            conflict = apply_market_lifecycle(
                state,
                lifecycle(
                    market_status=MarketStatus.SETTLED,
                    observed_monotonic_ns=(
                        state.lifecycle_observed_monotonic_ns
                    ),
                ),
            )
        status_transition.assert_not_called()
        assert_no_event(self, conflict)
        assert_gapped_only(self, state, conflict.state)
        with self.assertRaisesRegex(
            ExpertContractError,
            "^lifecycle_time_regression$",
        ):
            apply_market_lifecycle(
                state,
                lifecycle(observed_monotonic_ns=199),
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^book_epoch_stale$",
        ):
            apply_market_lifecycle(
                state,
                lifecycle(connection_epoch=0, observed_monotonic_ns=1),
            )
        future = apply_market_lifecycle(
            state,
            lifecycle(connection_epoch=2, observed_monotonic_ns=1),
        )
        assert_no_event(self, future)
        assert_gapped_only(self, state, future.state)

    def test_lifecycle_updates_gapped_state_without_repairing_trust(self) -> None:
        gapped = require_book_resnapshot(initial_state())
        result = apply_market_lifecycle(gapped, lifecycle())
        self.assertIs(result.accepted_event_kind, BookEventKind.LIFECYCLE)
        self.assertFalse(result.state.trusted)
        self.assertTrue(result.state.sequence_gap)

    def test_all_lifecycle_pairs_follow_exact_graph(self) -> None:
        for current in MarketStatus:
            for incoming_status in MarketStatus:
                base = changed_state(market_status=current)
                incoming = lifecycle(market_status=incoming_status)
                with self.subTest(current=current, incoming=incoming_status):
                    result = apply_market_lifecycle(base, incoming)
                    if incoming_status in ALLOWED_STATUS_TRANSITIONS[current]:
                        self.assertIs(
                            result.accepted_event_kind,
                            BookEventKind.LIFECYCLE,
                        )
                        self.assertIs(
                            result.state.market_status,
                            incoming_status,
                        )
                    else:
                        assert_no_event(self, result)
                        assert_gapped_only(self, base, result.state)

    def test_lifecycle_exact_type_and_precedence_table(self) -> None:
        state = initial_state()
        incoming = lifecycle()
        cases = (
            (object(), object(), TypeError, "state"),
            (state, object(), TypeError, "lifecycle"),
            (
                state,
                lifecycle(ticker="OTHER", connection_epoch=0),
                ExpertContractError,
                "book_ticker_mismatch",
            ),
            (
                state,
                lifecycle(connection_epoch=0, observed_monotonic_ns=1),
                ExpertContractError,
                "book_epoch_stale",
            ),
            (
                state,
                lifecycle(
                    observed_monotonic_ns=199,
                    market_status=MarketStatus.SETTLED,
                ),
                ExpertContractError,
                "lifecycle_time_regression",
            ),
        )
        for current, event, error, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(error, f"^{message}$"):
                    apply_market_lifecycle(current, event)  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "^state$"):
            apply_market_lifecycle(
                subclass_instance(state),
                incoming,
            )  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "^lifecycle$"):
            apply_market_lifecycle(
                state,
                subclass_instance(incoming),
            )  # type: ignore[arg-type]

    def test_lifecycle_gap_and_duplicate_do_not_relabel_historical_move(self) -> None:
        moved = changed_state(
            last_executable_move=Decimal("0.10"),
            last_executable_move_monotonic_ns=200,
        )
        duplicate = apply_market_lifecycle(
            moved,
            lifecycle(
                market_status=moved.market_status,
                scheduled_close_wall_ns=moved.scheduled_close_wall_ns,
                source_wall_ns=moved.lifecycle_source_wall_ns,
                observed_monotonic_ns=moved.lifecycle_observed_monotonic_ns,
                clock_uncertainty_ns=moved.lifecycle_clock_uncertainty_ns,
            ),
        )
        assert_no_event(self, duplicate)
        self.assertEqual(
            duplicate.state.last_executable_move,
            Decimal("0.10"),
        )
        conflict = apply_market_lifecycle(
            moved,
            lifecycle(
                market_status=MarketStatus.SUSPENDED,
                observed_monotonic_ns=moved.lifecycle_observed_monotonic_ns,
            ),
        )
        assert_no_event(self, conflict)
        self.assertEqual(
            conflict.state.last_executable_move,
            Decimal("0.10"),
        )


class MarketBookExecutionTests(unittest.TestCase):
    def test_buy_yes_consumes_no_bids_as_reciprocal_yes_asks(self) -> None:
        initial = book_from_snapshot(
            BookSnapshot(
                ticker="MATCH-HOME",
                connection_epoch=1,
                sequence=1,
                market_status=MarketStatus.OPEN,
                scheduled_close_wall_ns=10_000,
                source_wall_ns=100,
                observed_monotonic_ns=200,
                clock_uncertainty_ns=1,
                yes_bids=(BookLevel(Decimal("0.40"), Decimal("5")),),
                no_bids=(
                    BookLevel(Decimal("0.45"), Decimal("2")),
                    BookLevel(Decimal("0.44"), Decimal("4")),
                ),
            )
        )
        filled, average, levels = executable_buy(
            initial.state,
            ContractSide.YES,
            Decimal("3"),
            Decimal("0.56"),
        )
        self.assertEqual(filled, Decimal("3"))
        self.assertEqual(
            average,
            Decimal(
                "0.55333333333333333333333333333333333333333333333333333333333333333333333333333333"
            ),
        )
        self.assertEqual(
            tuple(level.quantity for level in levels),
            (Decimal("2"), Decimal("1")),
        )
        self.assertEqual(
            levels,
            (
                BookLevel(Decimal("0.55"), Decimal("2")),
                BookLevel(Decimal("0.56"), Decimal("1")),
            ),
        )

    def test_buy_no_consumes_yes_bids_with_limit_equality_and_partial(self) -> None:
        state = initial_state(
            yes_bids=(level("0.60", "1"), level("0.55", "2")),
            no_bids=(),
        )
        filled, average, levels = executable_buy(
            state,
            ContractSide.NO,
            Decimal("2"),
            Decimal("0.45"),
        )
        self.assertEqual(filled, Decimal("2"))
        self.assertEqual(average, Decimal("0.425"))
        self.assertEqual(
            levels,
            (
                BookLevel(Decimal("0.40"), Decimal("1")),
                BookLevel(Decimal("0.45"), Decimal("1")),
            ),
        )

    def test_execution_stops_before_price_above_limit_and_caps_request(self) -> None:
        state = initial_state(
            yes_bids=(),
            no_bids=(
                level("0.50", "2"),
                level("0.45", "3"),
                level("0.40", "4"),
            ),
        )
        filled, average, levels = executable_buy(
            state,
            ContractSide.YES,
            Decimal("10"),
            Decimal("0.55"),
        )
        self.assertEqual(filled, Decimal("5"))
        self.assertEqual(average, Decimal("0.53"))
        self.assertEqual(
            levels,
            (
                BookLevel(Decimal("0.50"), Decimal("2")),
                BookLevel(Decimal("0.55"), Decimal("3")),
            ),
        )

    def test_zero_request_empty_depth_and_wrong_one_sided_outcome_are_zero(self) -> None:
        empty = initial_state(yes_bids=(), no_bids=())
        yes_only = initial_state(yes_bids=(level("0.40"),), no_bids=())
        cases = (
            (empty, ContractSide.YES, Decimal("1")),
            (yes_only, ContractSide.YES, Decimal("1")),
            (yes_only, ContractSide.NO, Decimal("0")),
        )
        for state, side, quantity in cases:
            with self.subTest(side=side, quantity=quantity):
                self.assertEqual(
                    executable_buy(
                        state,
                        side,
                        quantity,
                        Decimal("1"),
                    ),
                    (Decimal("0"), Decimal("0"), ()),
                )
        self.assertEqual(
            executable_buy(
                yes_only,
                ContractSide.NO,
                Decimal("1"),
                Decimal("1"),
            )[0],
            Decimal("1"),
        )

    def test_execution_type_and_semantic_precedence_table(self) -> None:
        trusted = initial_state()
        gapped = require_book_resnapshot(trusted)
        non_open = initial_state(market_status=MarketStatus.SUSPENDED)
        cases = (
            (
                object(),
                "yes",
                1,
                1,
                TypeError,
                "state",
            ),
            (
                trusted,
                "yes",
                1,
                1,
                TypeError,
                "outcome",
            ),
            (
                trusted,
                ContractSide.YES,
                1,
                1,
                TypeError,
                "contracts",
            ),
            (
                trusted,
                ContractSide.YES,
                Decimal("1"),
                1,
                TypeError,
                "limit_price",
            ),
            (
                gapped,
                ContractSide.YES,
                Decimal("-1"),
                Decimal("2"),
                ExpertContractError,
                "contracts",
            ),
            (
                gapped,
                ContractSide.YES,
                Decimal("1"),
                Decimal("2"),
                ExpertContractError,
                "limit_price",
            ),
            (
                gapped,
                ContractSide.YES,
                Decimal("1"),
                Decimal("1"),
                ExpertContractError,
                "book_untrusted",
            ),
            (
                non_open,
                ContractSide.YES,
                Decimal("1"),
                Decimal("1"),
                ExpertContractError,
                "market_not_open",
            ),
        )
        for (
            state,
            side,
            quantity,
            limit,
            error,
            message,
        ) in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(error, f"^{message}$"):
                    executable_buy(
                        state,  # type: ignore[arg-type]
                        side,  # type: ignore[arg-type]
                        quantity,  # type: ignore[arg-type]
                        limit,  # type: ignore[arg-type]
                    )

    def test_execution_rejects_subclasses_nonfinite_and_float(self) -> None:
        class DecimalSubclass(Decimal):
            pass

        state = initial_state()
        for args, message in (
            (
                (
                    subclass_instance(state),
                    ContractSide.YES,
                    Decimal("1"),
                    Decimal("1"),
                ),
                "state",
            ),
            (
                (state, ContractSide.YES, 1.0, Decimal("1")),
                "contracts",
            ),
            (
                (state, ContractSide.YES, Decimal("1"), 1.0),
                "limit_price",
            ),
            (
                (
                    state,
                    ContractSide.YES,
                    DecimalSubclass("1"),
                    Decimal("1"),
                ),
                "contracts",
            ),
            (
                (
                    state,
                    ContractSide.YES,
                    Decimal("1"),
                    DecimalSubclass("1"),
                ),
                "limit_price",
            ),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(TypeError, f"^{message}$"):
                    executable_buy(*args)  # type: ignore[arg-type]
        for quantity in (
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("-Infinity"),
            Decimal("-0.1"),
        ):
            with self.subTest(quantity=quantity):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "^contracts$",
                ):
                    executable_buy(
                        state,
                        ContractSide.YES,
                        quantity,
                        Decimal("1"),
                    )
        for limit in (
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("-0.1"),
            Decimal("1.1"),
        ):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "^limit_price$",
                ):
                    executable_buy(
                        state,
                        ContractSide.YES,
                        Decimal("1"),
                        limit,
                    )

    def test_every_non_open_status_rejects_execution(self) -> None:
        for status in MarketStatus:
            if status is MarketStatus.OPEN:
                continue
            with self.subTest(status=status):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "^market_not_open$",
                ):
                    executable_buy(
                        initial_state(market_status=status),
                        ContractSide.YES,
                        Decimal("1"),
                        Decimal("1"),
                    )


class MoveDigestFixtureAndDeterminismTests(unittest.TestCase):
    def test_replacement_move_covers_changed_unchanged_appearance_disappearance(self) -> None:
        cases = (
            (
                snapshot(),
                snapshot(
                    connection_epoch=2,
                    sequence=1,
                    observed_monotonic_ns=300,
                ),
                Decimal("0"),
            ),
            (
                snapshot(),
                snapshot(
                    connection_epoch=2,
                    sequence=1,
                    observed_monotonic_ns=300,
                    no_bids=(level("0.43", "2"),),
                ),
                Decimal("0.02"),
            ),
            (
                snapshot(no_bids=()),
                snapshot(
                    connection_epoch=2,
                    sequence=1,
                    observed_monotonic_ns=300,
                    no_bids=(level("0.45", "2"),),
                ),
                Decimal("1"),
            ),
            (
                snapshot(no_bids=(level("0.45", "2"),)),
                snapshot(
                    connection_epoch=2,
                    sequence=1,
                    observed_monotonic_ns=300,
                    no_bids=(),
                ),
                Decimal("1"),
            ),
        )
        for original, replacement, want in cases:
            with self.subTest(want=want):
                old = require_book_resnapshot(book_from_snapshot(original).state)
                result = apply_book_snapshot(old, replacement)
                self.assertEqual(result.executable_move, want)
                self.assertEqual(result.state.last_executable_move, want)
                self.assertEqual(
                    result.state.last_executable_move_monotonic_ns,
                    300,
                )
                self.assertEqual(result.top_of_book_changed, want > 0)

    def test_replacement_move_takes_maximum_across_both_outcomes(self) -> None:
        old = require_book_resnapshot(initial_state())
        result = apply_book_snapshot(
            old,
            snapshot(
                connection_epoch=2,
                sequence=1,
                observed_monotonic_ns=300,
                yes_bids=(level("0.30", "1"),),
                no_bids=(level("0.44", "1"),),
            ),
        )
        self.assertEqual(result.executable_move, Decimal("0.10"))

    def test_known_digest_vectors_cover_all_accepted_event_classes(self) -> None:
        first_event = snapshot()
        first = book_from_snapshot(first_event)
        delta_event = delta(
            contract_side=ContractSide.NO,
            price=Decimal("0.45"),
            quantity=Decimal("0"),
        )
        second = apply_book_delta(first.state, delta_event)
        lifecycle_event = lifecycle(
            observed_monotonic_ns=202,
            market_status=MarketStatus.SUSPENDED,
        )
        third = apply_market_lifecycle(second.state, lifecycle_event)
        replacement_event = snapshot(
            connection_epoch=2,
            sequence=3,
            market_status=MarketStatus.OPEN,
            observed_monotonic_ns=300,
            yes_bids=(level("0.41", "1"),),
            no_bids=(level("0.43", "1"),),
        )
        fourth = apply_book_snapshot(
            require_book_resnapshot(third.state),
            replacement_event,
        )
        values = {
            "initial_event": first_event,
            "initial_state": first.state,
            "initial_result": first,
            "delta_event": delta_event,
            "delta_state": second.state,
            "delta_result": second,
            "lifecycle_event": lifecycle_event,
            "lifecycle_state": third.state,
            "lifecycle_result": third,
            "replacement_event": replacement_event,
            "replacement_state": fourth.state,
            "replacement_result": fourth,
        }
        expected = {
            "initial_event": "8cc3e038573ead3b0828ff5dcb41403600ad9620e6d35269454dd3a97bc5c3e3",
            "initial_state": "6e90a096675ed99403f3b9c14e45ca483fd3a08994306fa5c457a6f75a644dfc",
            "initial_result": "433758be219c4c5f9d8a01158ffbccfed58b3208722196aa2ba271d30596ccc9",
            "delta_event": "0de776faf21e3509cb47f3f31ebc0348ec9e47b3b28c2e12d180b8e1cc9d2fbc",
            "delta_state": "a43859766c713164b91c25294c78908707c1dbd1ddfb905042f40332a14d4b02",
            "delta_result": "6ded5f30c58731a687be8f4ba4cc5f6aa9f737e333b2b6c5c68e107095be53d7",
            "lifecycle_event": "2a9cfb2344d9870ccb2a0955014d1410a34b31317763c0c9f61b38a4b86ccde2",
            "lifecycle_state": "fa95d924b4bcbcb0fdea751dec85b44eb9db1eefcf7eb323a85b419866b9de46",
            "lifecycle_result": "219f08e6c1f922743e6ba77c18d91dd7b1f1684f09a20501091818695637fd3e",
            "replacement_event": "8b45700e059987559ceff1afcf089712398c9d42f51aff73558dc62c971502e4",
            "replacement_state": "69ff367fe42e9c75ffbb502416894e19604d4b9c0cb306ed85a24df7f3df17ff",
            "replacement_result": "71d1a26df7b674b84be74f8d8f18cc85389e38f206be82a830ba5690bc298dd8",
        }
        for name, value in values.items():
            with self.subTest(name=name):
                self.assertEqual(expert_contract_sha256(value), expected[name])

    def test_normalized_fixtures_have_exact_shape_and_no_float_conversion(self) -> None:
        snapshot_document = json.loads(
            (FIXTURE_DIR / "kalshi_orderbook_snapshot_v2.json").read_text(
                encoding="utf-8"
            )
        )
        delta_document = json.loads(
            (FIXTURE_DIR / "kalshi_orderbook_delta_v2.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(snapshot_document),
            {"fixture_domain", "payload"},
        )
        self.assertEqual(
            set(delta_document),
            {"fixture_domain", "payload"},
        )
        self.assertEqual(
            snapshot_document["fixture_domain"],
            "inci-tennis-expert-normalized-book-v1",
        )
        self.assertEqual(
            delta_document["fixture_domain"],
            "inci-tennis-expert-normalized-book-v1",
        )
        snapshot_payload = snapshot_document["payload"]
        delta_payload = delta_document["payload"]
        self.assertEqual(
            tuple(snapshot_payload),
            (
                "ticker",
                "connection_epoch",
                "sequence",
                "market_status",
                "scheduled_close_wall_ns",
                "source_wall_ns",
                "observed_monotonic_ns",
                "clock_uncertainty_ns",
                "yes_bids",
                "no_bids",
            ),
        )
        self.assertEqual(
            tuple(delta_payload),
            (
                "ticker",
                "connection_epoch",
                "sequence",
                "source_wall_ns",
                "observed_monotonic_ns",
                "clock_uncertainty_ns",
                "contract_side",
                "price",
                "quantity",
            ),
        )
        fixture_snapshot = BookSnapshot(
            ticker=snapshot_payload["ticker"],
            connection_epoch=snapshot_payload["connection_epoch"],
            sequence=snapshot_payload["sequence"],
            market_status=MarketStatus(snapshot_payload["market_status"]),
            scheduled_close_wall_ns=snapshot_payload[
                "scheduled_close_wall_ns"
            ],
            source_wall_ns=snapshot_payload["source_wall_ns"],
            observed_monotonic_ns=snapshot_payload[
                "observed_monotonic_ns"
            ],
            clock_uncertainty_ns=snapshot_payload["clock_uncertainty_ns"],
            yes_bids=tuple(
                BookLevel(Decimal(price), Decimal(quantity))
                for price, quantity in snapshot_payload["yes_bids"]
            ),
            no_bids=tuple(
                BookLevel(Decimal(price), Decimal(quantity))
                for price, quantity in snapshot_payload["no_bids"]
            ),
        )
        fixture_delta = BookDelta(
            ticker=delta_payload["ticker"],
            connection_epoch=delta_payload["connection_epoch"],
            sequence=delta_payload["sequence"],
            source_wall_ns=delta_payload["source_wall_ns"],
            observed_monotonic_ns=delta_payload["observed_monotonic_ns"],
            clock_uncertainty_ns=delta_payload["clock_uncertainty_ns"],
            contract_side=ContractSide(delta_payload["contract_side"]),
            price=Decimal(delta_payload["price"]),
            quantity=Decimal(delta_payload["quantity"]),
        )
        self.assertTrue(all(
            type(item.price) is Decimal and type(item.quantity) is Decimal
            for item in fixture_snapshot.yes_bids + fixture_snapshot.no_bids
        ))
        self.assertIs(type(fixture_delta.price), Decimal)
        self.assertIs(type(fixture_delta.quantity), Decimal)
        self.assertEqual(
            apply_book_delta(
                book_from_snapshot(fixture_snapshot).state,
                fixture_delta,
            ).state.sequence,
            11,
        )

    def test_replaying_identical_sequence_is_byte_and_digest_identical(self) -> None:
        def replay() -> tuple[BookTransitionResult, ...]:
            first = book_from_snapshot(snapshot())
            second = apply_book_delta(
                first.state,
                delta(
                    contract_side=ContractSide.NO,
                    price=Decimal("0.45"),
                    quantity=Decimal("1"),
                ),
            )
            third = apply_market_lifecycle(second.state, lifecycle())
            fourth = apply_book_snapshot(
                require_book_resnapshot(third.state),
                snapshot(
                    connection_epoch=2,
                    sequence=1,
                    observed_monotonic_ns=300,
                    market_status=MarketStatus.OPEN,
                ),
            )
            return first, second, third, fourth

        left = replay()
        right = replay()
        self.assertEqual(left, right)
        self.assertEqual(
            tuple(canonical_expert_bytes(item) for item in left),
            tuple(canonical_expert_bytes(item) for item in right),
        )
        self.assertEqual(
            tuple(expert_contract_sha256(item) for item in left),
            tuple(expert_contract_sha256(item) for item in right),
        )

    def test_global_decimal_context_cannot_change_results_bytes_or_digests(self) -> None:
        first_snapshot = snapshot()
        next_delta = delta(
            contract_side=ContractSide.NO,
            price=Decimal("0.45"),
            quantity=Decimal("0"),
        )
        requested_contracts = Decimal("2")
        execution_limit = Decimal("0.65")

        def exercise() -> tuple[object, ...]:
            first = book_from_snapshot(first_snapshot)
            second = apply_book_delta(
                first.state,
                next_delta,
            )
            execution = executable_buy(
                second.state,
                ContractSide.NO,
                requested_contracts,
                execution_limit,
            )
            return (
                first,
                second,
                execution,
                canonical_expert_bytes(second),
                expert_contract_sha256(second),
            )

        before_context = getcontext().copy()
        baseline = exercise()
        try:
            context = getcontext()
            context.prec = 4
            context.rounding = ROUND_DOWN
            for index, signal in enumerate(tuple(context.traps)):
                context.traps[signal] = not context.traps[signal]
                context.flags[signal] = index % 2 == 0
            hostile_context = context.copy()
            self.assertEqual(exercise(), baseline)
            after_exercise = getcontext()
            self.assertEqual(after_exercise.prec, hostile_context.prec)
            self.assertEqual(
                after_exercise.rounding,
                hostile_context.rounding,
            )
            self.assertEqual(after_exercise.traps, hostile_context.traps)
            self.assertEqual(after_exercise.flags, hostile_context.flags)
        finally:
            setcontext(before_context)

    def test_oversized_execution_arithmetic_uses_stable_error(self) -> None:
        huge = Decimal("1E+1000001")
        state = changed_state(
            yes_bids=(),
            no_bids=(BookLevel(Decimal("0.5"), huge),),
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^book_decimal_arithmetic$",
        ):
            executable_buy(
                state,
                ContractSide.YES,
                huge,
                Decimal("1"),
            )

    def test_bounded_valid_ladders_and_deltas_remain_canonical(self) -> None:
        prices = ("0.20", "0.30", "0.40")
        quantities = ("1", "2")
        for best in prices:
            for quantity in quantities:
                origin = snapshot(
                    yes_bids=(level(best, quantity),),
                    no_bids=(level("0.50", "1"),),
                )
                first = book_from_snapshot(origin)
                for new_price in prices:
                    incoming = delta(
                        price=Decimal(new_price),
                        quantity=Decimal(quantity),
                    )
                    with self.subTest(
                        best=best,
                        quantity=quantity,
                        new_price=new_price,
                    ):
                        result = apply_book_delta(first.state, incoming)
                        if result.accepted_event_kind is None:
                            self.assertTrue(result.state.sequence_gap)
                            continue
                        self.assertEqual(
                            result.state.yes_bids,
                            tuple(sorted(
                                result.state.yes_bids,
                                key=lambda item: item.price,
                                reverse=True,
                            )),
                        )
                        self.assertEqual(
                            len({
                                item.price for item in result.state.yes_bids
                            }),
                            len(result.state.yes_bids),
                        )
                        self.assertLessEqual(
                            result.state.yes_bids[0].price
                            + result.state.no_bids[0].price,
                            Decimal("1"),
                        )
                        self.assertEqual(
                            result,
                            apply_book_delta(
                                book_from_snapshot(origin).state,
                                incoming,
                            ),
                        )

    def test_production_module_has_no_authority_or_replace_capability(self) -> None:
        module_path = (
            Path(__file__).parents[2]
            / "inci_tennis_expert"
            / "market_book.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        forbidden_roots = {
            "asyncio",
            "datetime",
            "http",
            "io",
            "os",
            "pathlib",
            "random",
            "requests",
            "secrets",
            "socket",
            "subprocess",
            "time",
            "urllib",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(
                        alias.name.split(".", 1)[0],
                        forbidden_roots,
                    )
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn(
                    (node.module or "").split(".", 1)[0],
                    forbidden_roots,
                )
                self.assertFalse(
                    node.module == "dataclasses"
                    and any(alias.name == "replace" for alias in node.names)
                )
                self.assertIn(
                    node.module,
                    {
                        "__future__",
                        "decimal",
                        "typing",
                        "inci_tennis_expert.contracts",
                    },
                )
            elif isinstance(node, ast.Call):
                self.assertFalse(
                    isinstance(node.func, ast.Name)
                    and node.func.id == "replace"
                )


if __name__ == "__main__":
    unittest.main()
