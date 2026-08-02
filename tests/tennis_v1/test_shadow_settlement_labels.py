from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from inci_tennis_io.kalshi_shadow_settlement import KalshiFinalMarketState


_EVENT = "KXTENNIS-MATCH"
_TICKERS = ("KXTENNIS-MATCH-HOME", "KXTENNIS-MATCH-AWAY")
_PLAYERS = ("Player Home", "Player Away")
_WALL_NS = 1_785_607_205_000_000_000


def _private_payload(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def _source(root: Path) -> Path:
    from inci_tennis_io.shadow_evidence import (
        ShadowEvidenceStore,
        ShadowResolutionEvidence,
    )

    root = root.resolve(strict=False)
    provider = root.parent / (root.name + "-provider.json")
    _private_payload(provider, b'{"provider":"discovery"}')
    with ShadowEvidenceStore(root) as store:
        ledger = store.ledger_path
        store.append_resolution(
            ShadowResolutionEvidence(
                selected_wall_ns=_WALL_NS + 2,
                provider_match_id="sr:sport_event:123456",
                provider_start_wall_ns=_WALL_NS - 10_000_000_000,
                event_ticker=_EVENT,
                home_player_name=_PLAYERS[0],
                away_player_name=_PLAYERS[1],
                market_tickers=_TICKERS,
                provider_discovery_raw_path=str(provider),
                provider_discovery_raw_sha256=sha256(provider.read_bytes()).hexdigest(),
                kalshi_catalog_sha256="1" * 64,
                resolver_snapshot_sha256="2" * 64,
                resolver_rule_version="strict-name-start-v1",
            )
        )
        store.append_terminal(
            reason="duration_elapsed",
            code=None,
            ended_wall_ns=_WALL_NS + 30,
            ended_monotonic_ns=30,
            provider_match_id="sr:sport_event:123456",
            market_tickers=_TICKERS,
            sportradar_captures=0,
            kalshi_frames=0,
        )
    return ledger.resolve(strict=True)


def _market(index: int, **overrides: object) -> KalshiFinalMarketState:
    body = (b'{"market":"home"}' if index == 0 else b'{"market":"away"}')
    values: dict[str, object] = {
        "ticker": _TICKERS[index],
        "event_ticker": _EVENT,
        "market_type": "binary",
        "status": "finalized",
        "result": "yes" if index == 0 else "no",
        "settlement_value_dollars": "1.0000" if index == 0 else "0.0000",
        "settlement_ts": "2026-08-01T18:30:00Z",
        "raw_body": body,
        "raw_sha256": sha256(body).hexdigest(),
        "route_tier": "current",
    }
    values.update(overrides)
    return KalshiFinalMarketState(**values)  # type: ignore[arg-type]


class _Transport:
    def __init__(self, states: tuple[KalshiFinalMarketState, KalshiFinalMarketState],
                 source_root: Path | None = None) -> None:
        self.states = states
        self.calls: list[str] = []
        self.source_root = source_root

    def get_market_result(self, ticker: str) -> KalshiFinalMarketState:
        if self.source_root is not None:
            descriptor = os.open(self.source_root / "shadow.lock", os.O_RDONLY)
            try:
                with self_test_case().assertRaises(BlockingIOError):
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)
        self.calls.append(ticker)
        return self.states[len(self.calls) - 1]


class _Clocks:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def wall_ns(self) -> int:
        self.calls.append("wall")
        return _WALL_NS + 100

    def monotonic_ns(self) -> int:
        self.calls.append("monotonic")
        return 100


def self_test_case() -> unittest.TestCase:
    return unittest.TestCase()


def _store(root: Path):
    from inci_tennis_io.shadow_settlement_labels import ShadowSettlementLabelStore

    configured = root if root.is_symlink() else root.resolve(strict=False)
    return ShadowSettlementLabelStore(configured)


def _reconcile(source: Path, root: Path, transport: _Transport, clocks: _Clocks):
    from inci_tennis_io.shadow_settlement_labels import reconcile_shadow_settlement

    return reconcile_shadow_settlement(source, transport, _store(root), clocks)


def _rows(root: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (root / "settlements.jsonl").read_text().splitlines()]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _rewrite_rows(root: Path, rows: list[dict[str, object]]) -> None:
    previous = "0" * 64
    for index, row in enumerate(rows, start=1):
        row["row_number"] = index
        row["previous_row_sha256"] = previous
        without_digest = {key: value for key, value in row.items() if key != "row_sha256"}
        row["row_sha256"] = sha256(_canonical(without_digest)).hexdigest()
        previous = row["row_sha256"]  # type: ignore[assignment]
    ledger = b"".join(_canonical(row) + b"\n" for row in rows)
    (root / "settlements.jsonl").write_bytes(ledger)
    (root / "settlements.jsonl").chmod(0o600)
    commit = {
        "schema": "inci-tennis-shadow-settlement-commit-v1",
        "row_number": len(rows),
        "row_sha256": rows[-1]["row_sha256"],
    }
    (root / "settlement.commit").write_bytes(_canonical(commit) + b"\n")
    (root / "settlement.commit").chmod(0o600)


def _append_forged_row(
    root: Path,
    mutate: object,
) -> list[dict[str, object]]:
    rows = _rows(root)
    row = json.loads(json.dumps(rows[-1]))
    transaction_id = str(uuid4())
    row["transaction_id"] = transaction_id
    for index, market in enumerate(row["markets"]):
        old_path = Path(market["raw_path"])
        new_path = root / "raw" / (
            f"settlement-{transaction_id}-{index:02d}-{row['market_tickers'][index]}.json"
        )
        new_path.write_bytes(old_path.read_bytes())
        new_path.chmod(0o600)
        market["raw_path"] = str(new_path)
    mutate(row)
    rows.append(row)
    _rewrite_rows(root, rows)
    return rows


def _snapshot(root: Path) -> dict[str, tuple[bytes, int, int, int, int]]:
    return {
        str(path.relative_to(root)): (
            path.read_bytes(),
            stat.S_IMODE(path.stat().st_mode),
            path.stat().st_nlink,
            path.stat().st_mtime_ns,
            path.stat().st_ctime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class ShadowSettlementLabelContractTests(unittest.TestCase):
    def test_store_has_no_public_reconciliation_bypass(self) -> None:
        """Catches callers persisting a fabricated source with a no-op verifier."""
        from inci_tennis_io.shadow_settlement_labels import ShadowSettlementLabelStore

        store = ShadowSettlementLabelStore(Path("/canonical/configuration-only"))
        self.assertFalse(hasattr(store, "reconcile"))
        public_methods = {
            name for name in dir(ShadowSettlementLabelStore)
            if not name.startswith("_")
        }
        self.assertEqual(public_methods, set())

    def test_result_is_frozen_and_default_root_uses_os_account_only(self) -> None:
        """Catches mutable results or HOME/Path.home controlling durable authority."""
        from inci_tennis_io.shadow_settlement_labels import (
            ShadowSettlementResult,
            default_shadow_settlement_state_root,
        )

        result = ShadowSettlementResult("pending", None, None)
        with self.assertRaises(FrozenInstanceError):
            result.state = "final"  # type: ignore[misc]
        account = SimpleNamespace(pw_dir="/private/var/settlement-account")
        with patch.dict(os.environ, {"HOME": "/attacker"}), patch(
            "pwd.getpwuid", return_value=account
        ), patch.object(Path, "home", side_effect=AssertionError("Path.home forbidden")):
            self.assertEqual(
                default_shadow_settlement_state_root(),
                Path("/private/var/settlement-account/.local/state/inci/tennis-shadow-settlement"),
            )

    def test_pending_fetches_exact_order_under_source_lease_without_any_store_write(self) -> None:
        """Catches pending creating state, calling clocks, reordering, or dropping the lease."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source_root = base / "source"
            source = _source(source_root)
            root = base / "labels"
            transport = _Transport(
                (_market(0, status="determined"), _market(1)), source_root.resolve()
            )
            clocks = _Clocks()
            result = _reconcile(source, root, transport, clocks)
            self.assertEqual((result.state, result.winning_market_ticker,
                              result.winning_player_name), ("pending", None, None))
            self.assertEqual(transport.calls, list(_TICKERS))
            self.assertEqual(clocks.calls, [])
            self.assertFalse(root.exists())

    def test_final_winner_in_each_direction_has_exact_durable_grammar_and_raw_bytes(self) -> None:
        """Catches wrong winner mapping, source binding, canonical row, or raw rewrite."""
        for winner in (0, 1):
            with self.subTest(winner=winner), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                source = _source(base / "source")
                source_rows = [json.loads(line) for line in source.read_text().splitlines()]
                root = (base / "labels").resolve()
                states = (
                    _market(0, result="yes" if winner == 0 else "no",
                            settlement_value_dollars="1.0000" if winner == 0 else "0.0000"),
                    _market(1, result="yes" if winner == 1 else "no",
                            settlement_value_dollars="1" if winner == 1 else "0"),
                )
                clocks = _Clocks()
                result = _reconcile(source, root, _Transport(states), clocks)
                self.assertEqual(result.state, "final")
                self.assertEqual(result.winning_market_ticker, _TICKERS[winner])
                self.assertEqual(result.winning_player_name, _PLAYERS[winner])
                self.assertEqual(clocks.calls, ["wall", "monotonic"])

                self.assertEqual(
                    set(path.name for path in root.iterdir()),
                    {"raw", "settlement.lock", "settlement.epoch", "settlement.commit", "settlements.jsonl"},
                )
                self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((root / "raw").stat().st_mode), 0o700)
                self.assertEqual((root / "settlement.lock").read_bytes(), b"")
                row = _rows(root)[0]
                self.assertEqual(set(row), {
                    "schema", "transaction_id", "row_number", "source_path",
                    "source_ledger_sha256", "source_session_id", "source_mode",
                    "event_ticker", "market_tickers", "player_names",
                    "source_first_row_sha256", "source_terminal_row_sha256",
                    "markets", "state", "winning_market_ticker", "winning_player_name",
                    "reconciled_wall_ns", "reconciled_monotonic_ns",
                    "supersedes_row_sha256", "previous_row_sha256", "row_sha256",
                })
                self.assertEqual(row["source_path"], str(source))
                self.assertEqual(row["source_ledger_sha256"], sha256(source.read_bytes()).hexdigest())
                self.assertEqual(row["source_mode"], "VERIFIED")
                self.assertEqual(row["source_session_id"], source_rows[0]["session_id"])
                self.assertEqual(row["source_first_row_sha256"], source_rows[0]["row_sha256"])
                self.assertEqual(row["source_terminal_row_sha256"], source_rows[-1]["row_sha256"])
                self.assertEqual(row["market_tickers"], list(_TICKERS))
                self.assertEqual(row["player_names"], list(_PLAYERS))
                self.assertEqual(row["previous_row_sha256"], "0" * 64)
                self.assertIsNone(row["supersedes_row_sha256"])
                self.assertEqual(row["reconciled_wall_ns"], _WALL_NS + 100)
                self.assertEqual(row["reconciled_monotonic_ns"], 100)
                for index, market in enumerate(row["markets"]):
                    raw_path = Path(market["raw_path"])
                    self.assertEqual(raw_path.read_bytes(), states[index].raw_body)
                    self.assertEqual(market["raw_sha256"], states[index].raw_sha256)
                    self.assertIn(f"-{index:02d}-{_TICKERS[index]}.json", raw_path.name)
                    self.assertEqual(stat.S_IMODE(raw_path.stat().st_mode), 0o600)
                encoded_without_digest = json.dumps(
                    {key: value for key, value in row.items() if key != "row_sha256"},
                    sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                    allow_nan=False,
                ).encode()
                self.assertEqual(row["row_sha256"], sha256(encoded_without_digest).hexdigest())
                self.assertTrue((root / "settlements.jsonl").read_bytes().endswith(b"\n"))

    def test_all_finalized_invalid_families_become_durable_conflicts(self) -> None:
        """Catches guessing a final winner from malformed or noncomplementary evidence."""
        cases = {
            "both_yes": (_market(0), _market(1, result="yes", settlement_value_dollars="1")),
            "both_no": (_market(0, result="no", settlement_value_dollars="0"), _market(1)),
            "ticker": (_market(0, ticker="OTHER"), _market(1)),
            "event": (_market(0, event_ticker="OTHER"), _market(1)),
            "scalar": (_market(0, market_type="scalar"), _market(1)),
            "void": (_market(0, result="void"), _market(1)),
            "wrong_yes_value": (_market(0, settlement_value_dollars="0"), _market(1)),
            "noncomplementary": (_market(0, settlement_value_dollars="1.1"), _market(1)),
        }
        for name, states in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                source = _source(base / "source")
                root = base / "labels"
                result = _reconcile(source, root, _Transport(states), _Clocks())
                self.assertEqual((result.state, result.winning_market_ticker,
                                  result.winning_player_name), ("conflict", None, None))
                self.assertEqual(_rows(root)[0]["state"], "conflict")
                no_network = _Transport((_market(0), _market(1)))
                self.assertEqual(
                    _reconcile(source, root, no_network, _Clocks()).state,
                    "conflict",
                )
                self.assertEqual(no_network.calls, [])

    def test_transport_impossible_finalized_fields_fail_closed_without_writes(self) -> None:
        """Catches hand-built schema-invalid states being persisted as conflicts."""
        cases = {
            "missing_result": (_market(0, result=None), _market(1)),
            "missing_value": (_market(0, settlement_value_dollars=None), _market(1)),
            "missing_timestamp": (_market(0, settlement_ts=None), _market(1)),
            "bad_timestamp": (_market(0, settlement_ts="2026-02-30T18:30:00Z"), _market(1)),
            "exponent": (_market(0, settlement_value_dollars="1e0"), _market(1)),
            "sign": (_market(0, settlement_value_dollars="+1"), _market(1)),
            "leading_zero": (_market(0, settlement_value_dollars="01.0"), _market(1)),
        }
        for name, states in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                source = _source(base / "source")
                root = base / "labels"
                with self.assertRaises(RuntimeError):
                    _reconcile(source, root, _Transport(states), _Clocks())
                self.assertFalse(root.exists())

    def test_unknown_status_is_error_and_never_pending_or_durable(self) -> None:
        """Catches unknown lifecycle values being mislabeled as an ordinary pending state."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = _source(base / "source")
            root = base / "labels"
            with self.assertRaises(ValueError):
                _reconcile(source, root, _Transport((_market(0, status="mystery"), _market(1))), _Clocks())
            self.assertFalse(root.exists())

    def test_identical_final_is_read_only_changed_pair_supersedes_once_and_conflict_is_terminal(self) -> None:
        """Catches duplicate rows, reversible labels, or network use after conflict."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = _source(base / "source")
            root = base / "labels"
            initial_states = (_market(0), _market(1))
            first = _reconcile(source, root, _Transport(initial_states), _Clocks())
            before = _snapshot(root)
            no_op_clocks = _Clocks()
            second = _reconcile(source, root, _Transport(initial_states), no_op_clocks)
            self.assertEqual(second, first)
            self.assertEqual(no_op_clocks.calls, [])
            self.assertEqual(_snapshot(root), before)

            changed = (_market(0, status="determined"), _market(1))
            conflict = _reconcile(source, root, _Transport(changed), _Clocks())
            self.assertEqual(conflict.state, "conflict")
            rows = _rows(root)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["supersedes_row_sha256"], rows[0]["row_sha256"])
            self.assertEqual(rows[1]["previous_row_sha256"], rows[0]["row_sha256"])

            terminal_transport = _Transport(initial_states)
            terminal_clocks = _Clocks()
            terminal = _reconcile(source, root, terminal_transport, terminal_clocks)
            self.assertEqual(terminal.state, "conflict")
            self.assertEqual(terminal_transport.calls, [])
            self.assertEqual(terminal_clocks.calls, [])
            self.assertEqual(len(_rows(root)), 2)

    def test_nonfinal_after_prior_final_is_conflict_but_initial_nonfinal_is_pending(self) -> None:
        """Catches treating changed official evidence as a reversible pending label."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = _source(base / "source")
            root = base / "labels"
            _reconcile(source, root, _Transport((_market(0), _market(1))), _Clocks())
            result = _reconcile(
                source, root,
                _Transport((_market(0), _market(1, status="determined"))),
                _Clocks(),
            )
            self.assertEqual(result.state, "conflict")

    def test_tamper_or_unknown_inventory_fails_closed_before_network(self) -> None:
        """Catches partial/root-local tampering being ignored during source lookup."""
        mutators = {
            "unknown": lambda root: _private_payload(root / "surprise", b"x"),
            "lock_nonzero": lambda root: _private_payload(root / "settlement.lock", b"x"),
            "ledger_mode": lambda root: (root / "settlements.jsonl").chmod(0o644),
            "raw_bytes": lambda root: next((root / "raw").iterdir()).write_bytes(b"changed"),
            "commit": lambda root: (root / "settlement.commit").write_bytes(b"{}\n"),
        }
        for name, mutate in mutators.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                source = _source(base / "source")
                root = base / "labels"
                states = (_market(0), _market(1))
                _reconcile(source, root, _Transport(states), _Clocks())
                mutate(root)
                transport = _Transport(states)
                with self.assertRaises((ValueError, RuntimeError, OSError)):
                    _reconcile(source, root, transport, _Clocks())
                self.assertEqual(transport.calls, [])

    def test_symlink_hardlink_and_noncanonical_roots_are_rejected(self) -> None:
        """Catches alternate names or links bypassing owner-controlled state authority."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = _source(base / "source")
            real = (base / "real").resolve()
            real.mkdir(mode=0o700)
            alias = base / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaises((ValueError, RuntimeError)):
                _reconcile(source, alias, _Transport((_market(0), _market(1))), _Clocks())

            root = base / "labels"
            _reconcile(source, root, _Transport((_market(0), _market(1))), _Clocks())
            hardlink = base / "hardlink"
            os.link(root / "settlements.jsonl", hardlink)
            with self.assertRaises((ValueError, RuntimeError)):
                _reconcile(source, root, _Transport((_market(0), _market(1))), _Clocks())

    def test_active_writer_fails_nonblocking_before_network(self) -> None:
        """Catches waiting on or racing an existing settlement writer."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = _source(base / "source")
            root = base / "labels"
            states = (_market(0), _market(1))
            _reconcile(source, root, _Transport(states), _Clocks())
            fd = os.open(root / "settlement.lock", os.O_RDONLY)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            transport = _Transport(states)
            try:
                with self.assertRaises((BlockingIOError, RuntimeError)):
                    _reconcile(source, root, transport, _Clocks())
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            self.assertEqual(transport.calls, [])

    def test_bad_clock_or_raw_digest_fails_before_row_append(self) -> None:
        """Catches untrusted injected metadata entering durable history."""
        for name, states, clocks in (
            ("raw", (_market(0, raw_sha256="0" * 64), _market(1)), _Clocks()),
            ("bool-clock", (_market(0), _market(1)), SimpleNamespace(wall_ns=lambda: True, monotonic_ns=lambda: 1)),
            ("negative-clock", (_market(0), _market(1)), SimpleNamespace(wall_ns=lambda: 1, monotonic_ns=lambda: -1)),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                source = _source(base / "source")
                root = base / "labels"
                with self.assertRaises((ValueError, RuntimeError)):
                    _reconcile(source, root, _Transport(states), clocks)  # type: ignore[arg-type]
                if root.exists() and (root / "settlements.jsonl").exists():
                    self.assertEqual((root / "settlements.jsonl").read_bytes(), b"")

    def test_source_change_before_publication_prevents_commit(self) -> None:
        """Catches committing a label after the audited source bytes are replaced."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source_root = base / "source"
            source = _source(source_root)
            root = base / "labels"

            class MutatingTransport(_Transport):
                def get_market_result(self, ticker: str) -> KalshiFinalMarketState:
                    result = super().get_market_result(ticker)
                    if len(self.calls) == 2:
                        source.write_bytes(source.read_bytes().replace(b"Player Home", b"Player H0me", 1))
                        source.chmod(0o600)
                    return result

            with self.assertRaises(RuntimeError):
                _reconcile(source, root, MutatingTransport((_market(0), _market(1))), _Clocks())
            self.assertFalse((root / "settlements.jsonl").exists())

    def test_exact_predecessor_watermark_recovery_advances_once_and_is_terminal(self) -> None:
        """Catches retry appending a duplicate instead of advancing the exact durable tail."""
        from inci_tennis_io import shadow_settlement_labels as labels

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source = _source(base / "source")
            root = base / "labels"
            _reconcile(source, root, _Transport((_market(0), _market(1))), _Clocks())
            changed = (_market(0, status="determined"), _market(1))
            real_replace = labels.os.replace

            def fail_commit_replace(source_path: object, target_path: object) -> None:
                if Path(target_path).name == "settlement.commit":
                    raise OSError("injected commit replace failure")
                real_replace(source_path, target_path)

            with patch.object(labels.os, "replace", side_effect=fail_commit_replace):
                with self.assertRaisesRegex(OSError, "commit replace"):
                    _reconcile(source, root, _Transport(changed), _Clocks())
            self.assertTrue((root / "settlement.pending").exists())
            self.assertEqual(len(_rows(root)), 2)
            predecessor = json.loads((root / "settlement.commit").read_text())
            self.assertEqual(predecessor["row_number"], 1)

            no_network = _Transport((_market(0), _market(1)))
            recovered = _reconcile(source, root, no_network, _Clocks())
            self.assertEqual(recovered.state, "conflict")
            self.assertEqual(no_network.calls, [])
            self.assertEqual(len(_rows(root)), 2)
            self.assertFalse((root / "settlement.pending").exists())
            watermark = json.loads((root / "settlement.commit").read_text())
            self.assertEqual(watermark["row_number"], 2)
            self.assertEqual(watermark["row_sha256"], _rows(root)[-1]["row_sha256"])

    def test_exact_committed_tail_recovery_only_cleans_and_is_source_bound(self) -> None:
        """Catches cleanup recovery for another source or duplicate terminal rows."""
        from inci_tennis_io import shadow_settlement_labels as labels

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source = _source(base / "source-one")
            other_source = _source(base / "source-two")
            root = base / "labels"
            _reconcile(source, root, _Transport((_market(0), _market(1))), _Clocks())
            real_unlink = labels.os.unlink

            def fail_pending_unlink(path: object, *args: object, **kwargs: object) -> None:
                if Path(path).name == "settlement.pending":
                    raise OSError("injected pending unlink failure")
                real_unlink(path, *args, **kwargs)

            changed = (_market(0, status="determined"), _market(1))
            with patch.object(labels.os, "unlink", side_effect=fail_pending_unlink):
                with self.assertRaisesRegex(OSError, "pending unlink"):
                    _reconcile(source, root, _Transport(changed), _Clocks())
            self.assertTrue((root / "settlement.pending").exists())
            self.assertEqual(json.loads((root / "settlement.commit").read_text())["row_number"], 2)

            foreign_transport = _Transport((_market(0), _market(1)))
            with self.assertRaises(RuntimeError):
                _reconcile(other_source, root, foreign_transport, _Clocks())
            self.assertEqual(foreign_transport.calls, [])
            self.assertTrue((root / "settlement.pending").exists())

            no_network = _Transport((_market(0), _market(1)))
            recovered = _reconcile(source, root, no_network, _Clocks())
            self.assertEqual(recovered.state, "conflict")
            self.assertEqual(no_network.calls, [])
            self.assertEqual(len(_rows(root)), 2)
            self.assertFalse((root / "settlement.pending").exists())

    def test_any_other_pending_raw_row_watermark_combination_fails_closed(self) -> None:
        """Catches broad crash recovery accepting a missing or changed raw artifact."""
        from inci_tennis_io import shadow_settlement_labels as labels

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source = _source(base / "source")
            root = base / "labels"
            real_publish = labels._publish_new

            def fail_commit(path: Path, payload: bytes, state_root: Path) -> None:
                if path.name == "settlement.commit":
                    raise OSError("injected commit publication failure")
                real_publish(path, payload, state_root)

            with patch.object(labels, "_publish_new", side_effect=fail_commit):
                with self.assertRaisesRegex(OSError, "commit publication"):
                    _reconcile(source, root, _Transport((_market(0), _market(1))), _Clocks())
            raw = next((root / "raw").iterdir())
            raw.write_bytes(b"not-the-recorded-evidence")
            raw.chmod(0o600)
            transport = _Transport((_market(0), _market(1)))
            with self.assertRaises(RuntimeError):
                _reconcile(source, root, transport, _Clocks())
            self.assertEqual(transport.calls, [])
            self.assertEqual(len(_rows(root)), 1)

    def test_full_audit_rejects_forbidden_per_source_histories(self) -> None:
        """Catches second finals, missing/wrong supersession, and post-conflict rows."""
        def second_final(row: dict[str, object]) -> None:
            row["supersedes_row_sha256"] = None

        def missing_supersedes(row: dict[str, object]) -> None:
            row["state"] = "conflict"
            row["winning_market_ticker"] = None
            row["winning_player_name"] = None
            row["markets"][0]["status"] = "determined"  # type: ignore[index]
            row["supersedes_row_sha256"] = None

        def wrong_supersedes(row: dict[str, object]) -> None:
            missing_supersedes(row)
            row["supersedes_row_sha256"] = "f" * 64

        for name, mutate in (
            ("second_final", second_final),
            ("missing_supersedes", missing_supersedes),
            ("wrong_supersedes", wrong_supersedes),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                source = _source(base / "source")
                root = base / "labels"
                states = (_market(0), _market(1))
                _reconcile(source, root, _Transport(states), _Clocks())
                _append_forged_row(root, mutate)
                transport = _Transport(states)
                with self.assertRaises(RuntimeError):
                    _reconcile(source, root, transport, _Clocks())
                self.assertEqual(transport.calls, [])

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source = _source(base / "source")
            root = base / "labels"
            states = (_market(0), _market(1))
            _reconcile(source, root, _Transport(states), _Clocks())

            def valid_second_conflict(row: dict[str, object]) -> None:
                row["state"] = "conflict"
                row["winning_market_ticker"] = None
                row["winning_player_name"] = None
                row["markets"][0]["status"] = "determined"  # type: ignore[index]
                row["supersedes_row_sha256"] = _rows(root)[0]["row_sha256"]

            second = _append_forged_row(root, valid_second_conflict)[-1]

            def third_after_conflict(row: dict[str, object]) -> None:
                row["state"] = "conflict"
                row["winning_market_ticker"] = None
                row["winning_player_name"] = None
                row["supersedes_row_sha256"] = second["row_sha256"]

            _append_forged_row(root, third_after_conflict)
            transport = _Transport(states)
            with self.assertRaises(RuntimeError):
                _reconcile(source, root, transport, _Clocks())
            self.assertEqual(transport.calls, [])

    def test_full_audit_revalidates_normalized_market_and_row_semantics(self) -> None:
        """Catches digest-consistent malformed Markets or unsupported row meanings."""
        def route(row: dict[str, object]) -> None:
            row["markets"][0]["route_tier"] = "archive"  # type: ignore[index]

        def token(row: dict[str, object]) -> None:
            row["markets"][0]["market_type"] = "binary/type"  # type: ignore[index]

        def status(row: dict[str, object]) -> None:
            row["markets"][0]["status"] = "mystery"  # type: ignore[index]

        def result_type(row: dict[str, object]) -> None:
            row["markets"][0]["result"] = 1  # type: ignore[index]

        def decimal(row: dict[str, object]) -> None:
            row["markets"][0]["settlement_value_dollars"] = "1e0"  # type: ignore[index]

        def timestamp(row: dict[str, object]) -> None:
            row["markets"][0]["settlement_ts"] = "2026-08-01T18:30:00+00:00"  # type: ignore[index]

        def both_yes_final(row: dict[str, object]) -> None:
            row["markets"][1]["result"] = "yes"  # type: ignore[index]
            row["markets"][1]["settlement_value_dollars"] = "1"  # type: ignore[index]

        def first_conflict_valid_pair(row: dict[str, object]) -> None:
            row["state"] = "conflict"
            row["winning_market_ticker"] = None
            row["winning_player_name"] = None

        def first_conflict_nonfinal(row: dict[str, object]) -> None:
            first_conflict_valid_pair(row)
            row["markets"][0]["status"] = "determined"  # type: ignore[index]
            row["markets"][0]["result"] = None  # type: ignore[index]
            row["markets"][0]["settlement_value_dollars"] = None  # type: ignore[index]
            row["markets"][0]["settlement_ts"] = None  # type: ignore[index]

        mutations = (
            ("route", route), ("token", token), ("status", status),
            ("result_type", result_type), ("decimal", decimal),
            ("timestamp", timestamp), ("both_yes_final", both_yes_final),
            ("first_conflict_valid_pair", first_conflict_valid_pair),
            ("first_conflict_nonfinal", first_conflict_nonfinal),
        )
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                source = _source(base / "source")
                root = base / "labels"
                states = (_market(0), _market(1))
                _reconcile(source, root, _Transport(states), _Clocks())
                rows = _rows(root)
                mutate(rows[0])
                _rewrite_rows(root, rows)
                transport = _Transport(states)
                with self.assertRaises(RuntimeError):
                    _reconcile(source, root, transport, _Clocks())
                self.assertEqual(transport.calls, [])

    def test_semantically_valid_conflict_history_boundaries_are_accepted(self) -> None:
        """Catches an overstrict audit rejecting allowed finalized/nonfinal changes."""
        cases = (
            (_market(0, result="no", settlement_value_dollars="0"),
             _market(1, result="yes", settlement_value_dollars="1")),
            (_market(0), _market(1, result="yes", settlement_value_dollars="1")),
            (_market(0, status="determined", result=None,
                     settlement_value_dollars=None, settlement_ts=None), _market(1)),
        )
        for states in cases:
            with self.subTest(states=states), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                source = _source(base / "source")
                root = base / "labels"
                _reconcile(source, root, _Transport((_market(0), _market(1))), _Clocks())
                result = _reconcile(source, root, _Transport(states), _Clocks())
                self.assertEqual(result.state, "conflict")
                no_network = _Transport((_market(0), _market(1)))
                self.assertEqual(_reconcile(source, root, no_network, _Clocks()).state, "conflict")
                self.assertEqual(no_network.calls, [])

    def test_all_durable_reads_enforce_finite_caps_with_exact_boundaries(self) -> None:
        """Catches control, raw, ledger, or line reads allocating without a bound."""
        from inci_tennis_io import shadow_settlement_labels as labels

        self.assertEqual(labels._MAX_RAW_BODY_BYTES, 8_388_608)
        self.assertLessEqual(labels._MAX_EPOCH_BYTES, 4096)
        self.assertLessEqual(labels._MAX_COMMIT_BYTES, 4096)
        self.assertLessEqual(labels._MAX_PENDING_BYTES, 65_536)
        self.assertGreater(labels._MAX_LEDGER_BYTES, labels._MAX_LEDGER_LINE_BYTES)
        self.assertGreater(labels._MAX_LEDGER_ROWS, 1)

        cap_cases = (
            ("_MAX_EPOCH_BYTES", "settlement.epoch"),
            ("_MAX_COMMIT_BYTES", "settlement.commit"),
            ("_MAX_RAW_BODY_BYTES", "raw"),
            ("_MAX_LEDGER_BYTES", "settlements.jsonl"),
        )
        for constant, artifact in cap_cases:
            with self.subTest(constant=constant), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                source = _source(base / "source")
                root = base / "labels"
                states = (_market(0), _market(1))
                _reconcile(source, root, _Transport(states), _Clocks())
                path = next((root / "raw").iterdir()) if artifact == "raw" else root / artifact
                exact = path.stat().st_size
                with patch.object(labels, constant, exact):
                    self.assertEqual(_reconcile(source, root, _Transport(states), _Clocks()).state, "final")
                transport = _Transport(states)
                with patch.object(labels, constant, exact - 1):
                    with self.assertRaisesRegex(RuntimeError, "size"):
                        _reconcile(source, root, transport, _Clocks())
                self.assertEqual(transport.calls, [])

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source = _source(base / "source")
            root = base / "labels"
            states = (_market(0), _market(1))
            _reconcile(source, root, _Transport(states), _Clocks())
            ledger = root / "settlements.jsonl"
            line_size = len(ledger.read_bytes())
            with patch.object(labels, "_MAX_LEDGER_LINE_BYTES", line_size):
                self.assertEqual(_reconcile(source, root, _Transport(states), _Clocks()).state, "final")
            with patch.object(labels, "_MAX_LEDGER_LINE_BYTES", line_size - 1):
                with self.assertRaisesRegex(RuntimeError, "line"):
                    _reconcile(source, root, _Transport(states), _Clocks())
            with patch.object(labels, "_MAX_LEDGER_ROWS", 1):
                self.assertEqual(_reconcile(source, root, _Transport(states), _Clocks()).state, "final")
            with patch.object(labels, "_MAX_LEDGER_ROWS", 0):
                with self.assertRaisesRegex(RuntimeError, "row"):
                    _reconcile(source, root, _Transport(states), _Clocks())

        for cap_delta, should_pass in ((0, True), (-1, False)):
            with self.subTest(pending_cap_delta=cap_delta), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                source = _source(base / "source")
                root = base / "labels"
                states = (_market(0), _market(1))
                real_unlink = labels.os.unlink

                def retain_pending(path: object, *args: object, **kwargs: object) -> None:
                    if Path(path).name == "settlement.pending":
                        raise OSError("retain pending")
                    real_unlink(path, *args, **kwargs)

                with patch.object(labels.os, "unlink", side_effect=retain_pending):
                    with self.assertRaisesRegex(OSError, "retain pending"):
                        _reconcile(source, root, _Transport(states), _Clocks())
                exact = (root / "settlement.pending").stat().st_size
                transport = _Transport(states)
                with patch.object(labels, "_MAX_PENDING_BYTES", exact + cap_delta):
                    if should_pass:
                        self.assertEqual(_reconcile(source, root, transport, _Clocks()).state, "final")
                    else:
                        with self.assertRaisesRegex(RuntimeError, "size"):
                            _reconcile(source, root, transport, _Clocks())
                        self.assertEqual(transport.calls, [])

    def test_raw_body_accepts_transport_maximum_and_rejects_one_byte_over(self) -> None:
        """Catches a settlement-specific raw cap drifting from the reviewed transport."""
        from inci_tennis_io import shadow_settlement_labels as labels

        for size, should_pass in ((8_388_608, True), (8_388_609, False)):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                source = _source(base / "source")
                root = base / "labels"
                body = b"x" * size
                states = (
                    replace(_market(0), raw_body=body, raw_sha256=sha256(body).hexdigest()),
                    _market(1),
                )
                if should_pass:
                    self.assertEqual(_reconcile(source, root, _Transport(states), _Clocks()).state, "final")
                    self.assertEqual(next(path for path in (root / "raw").iterdir() if "-00-" in path.name).stat().st_size,
                                     labels._MAX_RAW_BODY_BYTES)
                else:
                    with self.assertRaisesRegex(RuntimeError, "size"):
                        _reconcile(source, root, _Transport(states), _Clocks())
                    self.assertFalse(root.exists())

    def test_generated_artifacts_are_preflighted_at_every_exact_cap(self) -> None:
        """Catches a successful commit poisoning its own next bounded audit."""
        from inci_tennis_io import shadow_settlement_labels as labels

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            states = (_market(0), _market(1))
            probe_source = _source(base / "s99-source")
            probe_root = base / "r99-labels"
            real_unlink = labels.os.unlink

            def retain_pending(path: object, *args: object, **kwargs: object) -> None:
                if Path(path).name == "settlement.pending":
                    raise OSError("retain pending probe")
                real_unlink(path, *args, **kwargs)

            with patch.object(labels.os, "unlink", side_effect=retain_pending):
                with self.assertRaisesRegex(OSError, "retain pending probe"):
                    _reconcile(probe_source, probe_root, _Transport(states), _Clocks())
            row_line = (probe_root / "settlements.jsonl").read_bytes()
            raw_size = max(path.stat().st_size for path in (probe_root / "raw").iterdir())
            exact_caps = {
                "_MAX_LEDGER_ROWS": 1,
                "_MAX_RAW_FILES": 2,
                "_MAX_EPOCH_BYTES": (probe_root / "settlement.epoch").stat().st_size,
                "_MAX_COMMIT_BYTES": (probe_root / "settlement.commit").stat().st_size,
                "_MAX_PENDING_BYTES": (probe_root / "settlement.pending").stat().st_size,
                "_MAX_RAW_BODY_BYTES": raw_size,
                "_MAX_LEDGER_LINE_BYTES": len(row_line),
                "_MAX_LEDGER_BYTES": len(row_line),
            }

            exact_source = _source(base / "s98-source")
            exact_root = base / "r98-labels"
            exact_clocks = _Clocks()
            with patch.multiple(labels, **exact_caps):
                result = _reconcile(
                    exact_source, exact_root, _Transport(states), exact_clocks
                )
                self.assertEqual(result.state, "final")
                self.assertEqual(exact_clocks.calls, ["wall", "monotonic"])
                no_op_transport = _Transport(states)
                no_op_clocks = _Clocks()
                self.assertEqual(
                    _reconcile(
                        exact_source, exact_root, no_op_transport, no_op_clocks
                    ).state,
                    "final",
                )
                self.assertEqual(no_op_clocks.calls, [])
                self.assertEqual(len(_rows(exact_root)), 1)

            for index, (constant, exact) in enumerate(exact_caps.items()):
                with self.subTest(constant=constant):
                    source = _source(base / f"s{index:02d}-source")
                    root = base / f"r{index:02d}-labels"
                    clocks = _Clocks()
                    with patch.object(labels, constant, exact - 1):
                        with self.assertRaisesRegex(RuntimeError, "capacity|size|count"):
                            _reconcile(
                                source, root, _Transport(states), clocks
                            )
                    self.assertFalse(root.exists())

    def test_existing_store_count_overflow_is_clockless_and_metadata_read_only(self) -> None:
        """Catches the original full-store overrun mutating a valid capped root."""
        from inci_tennis_io import shadow_settlement_labels as labels

        changed = (
            _market(0, result="no", settlement_value_dollars="0"),
            _market(1, result="yes", settlement_value_dollars="1"),
        )
        for constant, cap in (("_MAX_LEDGER_ROWS", 1), ("_MAX_RAW_FILES", 2)):
            with self.subTest(constant=constant), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                source = _source(base / "source")
                root = base / "labels"
                _reconcile(
                    source, root, _Transport((_market(0), _market(1))), _Clocks()
                )

                def durable_snapshot() -> dict[str, tuple[object, ...]]:
                    snapshot: dict[str, tuple[object, ...]] = {}
                    for path in (root, *sorted(root.rglob("*"))):
                        info = path.lstat()
                        snapshot[str(path.relative_to(root))] = (
                            stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode),
                            info.st_uid, info.st_nlink, info.st_mtime_ns,
                            info.st_ctime_ns,
                            path.read_bytes() if path.is_file() else None,
                        )
                    return snapshot

                before = durable_snapshot()
                clocks = _Clocks()
                with patch.object(labels, constant, cap):
                    with self.assertRaisesRegex(RuntimeError, "capacity"):
                        _reconcile(source, root, _Transport(changed), clocks)
                self.assertEqual(clocks.calls, [])
                self.assertEqual(durable_snapshot(), before)


    def test_transaction_phase_fault_matrix_never_returns_false_or_duplicates(self) -> None:
        """Catches durability boundary failures returning a label or duplicating a row."""
        from inci_tennis_io import shadow_settlement_labels as labels

        phases = (
            "root_bootstrap", "raw_bootstrap", "lock_create", "lock_close",
            "epoch_write", "epoch_publish", "epoch_root_fsync",
            "pending_write", "pending_publish", "pending_root_fsync",
            "raw_0_write", "raw_1_write", "raw_directory_fsync",
            "ledger_open", "ledger_append", "ledger_fsync", "ledger_close",
            "commit_write", "commit_publish", "commit_root_fsync",
            "pending_unlink", "cleanup_root_fsync",
        )
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                source = _source(base / "source")
                root = base / "labels"
                fired = [False]
                real_mkdir = labels.os.mkdir
                real_open = labels.os.open
                real_close = labels.os.close
                real_write_new = labels._write_new_file
                real_publish = labels._publish_new
                real_dir_fsync = labels._directory_fsync
                real_append = labels._append_row
                real_replace = labels.os.replace
                real_unlink = labels.os.unlink

                def inject() -> None:
                    fired[0] = True
                    raise OSError("injected " + phase)

                lock_fds: set[int] = set()

                def mkdir(path: object, mode: int = 0o777, *args: object, **kwargs: object) -> None:
                    target = Path(path)
                    if phase == "root_bootstrap" and target == root:
                        inject()
                    if phase == "raw_bootstrap" and target == root / "raw":
                        inject()
                    real_mkdir(path, mode, *args, **kwargs)

                def open_file(path: object, flags: int, mode: int = 0o777, *args: object, **kwargs: object) -> int:
                    target = Path(path) if isinstance(path, (str, os.PathLike)) else None
                    if phase == "lock_create" and target == root / "settlement.lock":
                        inject()
                    if phase == "ledger_open" and target == root / "settlements.jsonl":
                        inject()
                    descriptor = real_open(path, flags, mode, *args, **kwargs)
                    if target == root / "settlement.lock":
                        lock_fds.add(descriptor)
                    return descriptor

                def close_file(descriptor: int) -> None:
                    if phase == "lock_close" and descriptor in lock_fds:
                        real_close(descriptor)
                        lock_fds.discard(descriptor)
                        inject()
                    real_close(descriptor)

                def write_new(path: Path, payload: bytes) -> None:
                    name = path.name
                    if phase == "epoch_write" and ".settlement.epoch." in name:
                        inject()
                    if phase == "pending_write" and ".settlement.pending." in name:
                        inject()
                    if phase == "raw_0_write" and "-00-" in name:
                        inject()
                    if phase == "raw_1_write" and "-01-" in name:
                        inject()
                    if phase == "commit_write" and ".settlement.commit." in name:
                        inject()
                    real_write_new(path, payload)

                def publish(path: Path, payload: bytes, state_root: Path) -> None:
                    if phase == "epoch_publish" and path.name == "settlement.epoch":
                        inject()
                    if phase == "pending_publish" and path.name == "settlement.pending":
                        inject()
                    if phase == "commit_publish" and path.name == "settlement.commit":
                        inject()
                    real_publish(path, payload, state_root)

                root_sync_counts = [0]

                def dir_fsync(path: Path) -> None:
                    if path == root:
                        root_sync_counts[0] += 1
                    if phase == "raw_directory_fsync" and path == root / "raw" and (root / "settlement.pending").exists():
                        inject()
                    if phase == "epoch_root_fsync" and path == root and (root / "settlement.epoch").exists() and not (root / "settlement.pending").exists():
                        inject()
                    if phase == "pending_root_fsync" and path == root and (root / "settlement.pending").exists() and not any((root / "raw").iterdir()):
                        inject()
                    if phase == "commit_root_fsync" and path == root and (root / "settlement.commit").exists() and (root / "settlement.pending").exists():
                        inject()
                    if phase == "cleanup_root_fsync" and path == root and not (root / "settlement.pending").exists() and (root / "settlement.commit").exists():
                        inject()
                    real_dir_fsync(path)

                def append(path: Path, payload: bytes) -> None:
                    if phase == "ledger_append":
                        inject()
                    if phase in ("ledger_fsync", "ledger_close"):
                        # The wrapper boundary is injected after the append bytes exist;
                        # exact-tail recovery must decide whether they may commit.
                        real_append(path, payload)
                        inject()
                    real_append(path, payload)

                def replace_file(source_path: object, target_path: object) -> None:
                    if phase == "commit_publish" and Path(target_path).name == "settlement.commit":
                        inject()
                    real_replace(source_path, target_path)

                def unlink(path: object, *args: object, **kwargs: object) -> None:
                    if phase == "pending_unlink" and Path(path).name == "settlement.pending":
                        inject()
                    real_unlink(path, *args, **kwargs)

                patches = (
                    patch.object(labels.os, "mkdir", side_effect=mkdir),
                    patch.object(labels.os, "open", side_effect=open_file),
                    patch.object(labels.os, "close", side_effect=close_file),
                    patch.object(labels, "_write_new_file", side_effect=write_new),
                    patch.object(labels, "_publish_new", side_effect=publish),
                    patch.object(labels, "_directory_fsync", side_effect=dir_fsync),
                    patch.object(labels, "_append_row", side_effect=append),
                    patch.object(labels.os, "replace", side_effect=replace_file),
                    patch.object(labels.os, "unlink", side_effect=unlink),
                )
                for active in patches:
                    active.start()
                try:
                    with self.assertRaisesRegex(OSError, "injected"):
                        _reconcile(source, root, _Transport((_market(0), _market(1))), _Clocks())
                finally:
                    for active in reversed(patches):
                        active.stop()
                self.assertTrue(fired[0])
                before_retry = len(_rows(root)) if (root / "settlements.jsonl").exists() and (root / "settlements.jsonl").stat().st_size else 0
                self.assertLessEqual(before_retry, 1)
                try:
                    retry = _reconcile(source, root, _Transport((_market(0), _market(1))), _Clocks())
                except (OSError, RuntimeError, ValueError):
                    pass  # Poisoned pre-tail states intentionally stay failed closed.
                else:
                    self.assertEqual(retry.state, "final")
                    self.assertEqual(len(_rows(root)), 1)

    def test_unlock_failure_still_closes_descriptor_and_primary_error_wins(self) -> None:
        """Catches cleanup failure leaking the root lock or masking a transport error."""
        from inci_tennis_io import shadow_settlement_labels as labels

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source = _source(base / "source")
            root = base / "labels"
            _reconcile(source, root, _Transport((_market(0), _market(1))), _Clocks())
            settlement_inode = (root / "settlement.lock").stat().st_ino
            real_flock = labels.fcntl.flock

            def fail_unlock(descriptor: int, operation: int) -> None:
                if os.fstat(descriptor).st_ino == settlement_inode and operation == fcntl.LOCK_UN:
                    raise OSError("injected unlock")
                real_flock(descriptor, operation)

            class FailingTransport(_Transport):
                def get_market_result(self, ticker: str) -> KalshiFinalMarketState:
                    raise RuntimeError("transport primary")

            descriptors_before = len(os.listdir("/dev/fd"))
            with patch.object(labels.fcntl, "flock", side_effect=fail_unlock):
                with self.assertRaisesRegex(RuntimeError, "transport primary"):
                    _reconcile(source, root, FailingTransport((_market(0), _market(1))), _Clocks())
            self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)

    def test_authority_ast_is_get_only_and_uses_no_private_shadow_evidence_helpers(self) -> None:
        """Catches importing hidden audit internals or gaining trading/provider authority."""
        source_path = Path(__file__).parents[2] / "inci_tennis_io" / "shadow_settlement_labels.py"
        tree = ast.parse(source_path.read_text())
        forbidden = {
            "portfolio", "order", "trade", "account", "credential", "provider",
            "strategy", "signal", "executor", "position", "fill", "fee", "pnl",
        }
        modules: set[str] = set()
        transport_calls: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    modules.add(node.module)
                if node.module == "inci_tennis_io.shadow_evidence":
                    self.assertFalse(any(alias.name.startswith("_") for alias in node.names))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "transport":
                    transport_calls.append(node.func.attr)
        self.assertFalse(any(term in module.casefold() for term in forbidden for module in modules))
        self.assertEqual(set(transport_calls), {"get_market_result"})


if __name__ == "__main__":
    unittest.main()
