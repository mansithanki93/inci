from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import inci_tennis_runtime.shadow_settlement_cli as settlement_cli
from inci_tennis_runtime.shadow_settlement_cli import (
    ShadowSettlementCliDependencies,
    main,
    run_cli,
)


_SESSION = Path("/private/tmp/session-00000000-0000-4000-8000-000000000000.jsonl")
_REVIEWED_CLI_AST_SHA256 = "11030cf9651e60b4364310d8685b51093531a521daa231eaf7bb639f119d94a6"
_ALLOWED_IMPORTS = (
    ("from", "__future__", ("annotations",)),
    ("import", ("argparse",)),
    ("from", "dataclasses", ("dataclass",)),
    ("from", "pathlib", ("Path",)),
    ("from", "re", ("compile",)),
    ("import", ("sys",)),
    ("import", ("time",)),
    ("from", "typing", ("Callable",)),
    ("from", "inci_tennis_io.kalshi_shadow_settlement", ("KalshiShadowSettlementTransport",)),
    ("from", "inci_tennis_io.shadow_settlement_labels", (
        "ShadowSettlementLabelStore", "reconcile_shadow_settlement",
    )),
)


class _Factory:
    def __init__(self, name: str, calls: list[str], value: object = None,
                 error: BaseException | None = None) -> None:
        self.name = name
        self.calls = calls
        self.value = object() if value is None else value
        self.error = error

    def __call__(self) -> object:
        self.calls.append(self.name)
        if self.error is not None:
            raise self.error
        return self.value


class _TextStream:
    def __init__(self, *, short: bool = False, write_error: BaseException | None = None,
                 flush_error: BaseException | None = None) -> None:
        self.value = ""
        self.short = short
        self.write_error = write_error
        self.flush_error = flush_error

    def write(self, text: str) -> int:
        if self.write_error is not None:
            raise self.write_error
        accepted = len(text) - 1 if self.short and text else len(text)
        self.value += text[:accepted]
        return accepted

    def flush(self) -> None:
        if self.flush_error is not None:
            raise self.flush_error


def _dependencies(
    calls: list[str],
    *,
    result: object = SimpleNamespace(state="pending"),
    error: BaseException | None = None,
) -> tuple[ShadowSettlementCliDependencies, tuple[object, object, object]]:
    transport, store, clocks = object(), object(), object()

    def reconcile(path: Path, actual_transport: object, actual_store: object,
                  actual_clocks: object) -> object:
        calls.append("reconcile")
        if (path, actual_transport, actual_store, actual_clocks) != (
            _SESSION, transport, store, clocks,
        ):
            raise AssertionError("CLI forwarded a changed path or dependency")
        if error is not None:
            raise error
        return result

    return (
        ShadowSettlementCliDependencies(
            transport_factory=_Factory("transport", calls, transport),
            store_factory=_Factory("store", calls, store),
            clocks_factory=_Factory("clocks", calls, clocks),
            reconcile=reconcile,
        ),
        (transport, store, clocks),
    )


def _canonical_ast_sha256(source: str) -> str:
    return hashlib.sha256(
        ast.dump(ast.parse(source), include_attributes=False).encode("utf-8")
    ).hexdigest()


def _imports(tree: ast.AST) -> tuple[object, ...]:
    values: list[object] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            values.append(("import", tuple(alias.name for alias in node.names)))
        elif isinstance(node, ast.ImportFrom):
            values.append(("from", node.module, tuple(alias.name for alias in node.names)))
    return tuple(values)


def _assert_reviewed_cli_ast(source: str) -> None:
    tree = ast.parse(source)
    assert _imports(tree) == _ALLOWED_IMPORTS
    assert _canonical_ast_sha256(source) == _REVIEWED_CLI_AST_SHA256


class ShadowSettlementCliContractTests(unittest.TestCase):
    def test_each_valid_result_prints_only_its_state_and_forwards_exact_dependencies(self) -> None:
        """Catches emitting private reconciliation fields or reordering composition."""
        for state in ("pending", "final", "conflict"):
            with self.subTest(state=state):
                calls: list[str] = []
                dependencies, _ = _dependencies(
                    calls, result=SimpleNamespace(
                        state=state, winning_market_ticker="SECRET", winning_player_name="SECRET",
                    ),
                )
                stdout, stderr = _TextStream(), _TextStream()
                status = run_cli([str(_SESSION)], stdout=stdout, stderr=stderr,
                                 dependencies=dependencies)
                self.assertEqual(status, 0)
                self.assertEqual(stdout.value, state + "\n")
                self.assertEqual(stderr.value, "")
                self.assertEqual(calls, ["transport", "store", "clocks", "reconcile"])

    def test_help_writes_argparse_help_without_construction(self) -> None:
        """Catches help initializing a transport, store, or clocks."""
        calls: list[str] = []
        dependencies, _ = _dependencies(calls)
        stdout, stderr = _TextStream(), _TextStream()
        status = run_cli(["--help"], stdout=stdout, stderr=stderr,
                         dependencies=dependencies)
        self.assertEqual(status, 0)
        self.assertIn("usage:", stdout.value)
        self.assertEqual(stderr.value, "")
        self.assertEqual(calls, [])

    def test_help_output_failure_is_still_successful_and_silent_on_stderr(self) -> None:
        """Catches failed help output being converted into a normal command halt."""
        for stdout in (
            _TextStream(short=True), _TextStream(write_error=OSError()),
            _TextStream(flush_error=OSError()),
            _TextStream(write_error=KeyboardInterrupt()),
            _TextStream(flush_error=SystemExit()),
        ):
            with self.subTest(stdout=stdout):
                calls: list[str] = []
                dependencies, _ = _dependencies(calls)
                stderr = _TextStream()
                self.assertEqual(
                    run_cli(["--help"], stdout=stdout, stderr=stderr,
                            dependencies=dependencies),
                    0,
                )
                self.assertEqual(stderr.value, "")
                self.assertEqual(calls, [])

    def test_missing_extra_and_relative_arguments_are_silent_usage_failures(self) -> None:
        """Catches native argparse output or dependency work before validation."""
        for argv in ([], [str(_SESSION), "extra"], ["relative.jsonl"]):
            with self.subTest(argv=argv):
                calls: list[str] = []
                dependencies, _ = _dependencies(calls)
                stdout, stderr = _TextStream(), _TextStream()
                status = run_cli(argv, stdout=stdout, stderr=stderr,
                                 dependencies=dependencies)
                self.assertEqual(status, 2)
                self.assertEqual(stdout.value, "")
                self.assertEqual(stderr.value, "ERROR: invalid command arguments\n")
                self.assertEqual(calls, [])

    def test_malformed_reconciler_result_halts_without_partial_output(self) -> None:
        """Catches treating absent, coerced, or unknown state as a success."""
        for result in (
            object(), SimpleNamespace(state=None), SimpleNamespace(state=b"final"),
            SimpleNamespace(state="wrong"),
        ):
            with self.subTest(result=type(result).__name__):
                calls: list[str] = []
                dependencies, _ = _dependencies(calls, result=result)
                stdout, stderr = _TextStream(), _TextStream()
                self.assertEqual(
                    run_cli([str(_SESSION)], stdout=stdout, stderr=stderr,
                            dependencies=dependencies),
                    1,
                )
                self.assertEqual(stdout.value, "")
                self.assertEqual(stderr.value, "HALTED: shadow_settlement_unavailable\n")

    def test_known_and_unknown_failure_codes_are_sanitized(self) -> None:
        """Catches paths, credentials, reprs, or unsafe text leaking to stderr."""
        class _KnownError(RuntimeError):
            code = "kalshi_settlement_rate_limited"

        cases = (
            (_KnownError("/private/token"), "kalshi_settlement_rate_limited"),
            (RuntimeError("shadow_settlement_source_invalid"), "shadow_settlement_source_invalid"),
            (RuntimeError("ticker=SECRET /private/key.pem"), "shadow_settlement_unavailable"),
            (RuntimeError("shadow_bad-code"), "shadow_settlement_unavailable"),
        )
        for error, expected in cases:
            with self.subTest(error=str(error)):
                calls: list[str] = []
                dependencies, _ = _dependencies(calls, error=error)
                stdout, stderr = _TextStream(), _TextStream()
                self.assertEqual(run_cli([str(_SESSION)], stdout=stdout, stderr=stderr,
                                         dependencies=dependencies), 1)
                self.assertEqual(stdout.value, "")
                self.assertEqual(stderr.value, f"HALTED: {expected}\n")

    def test_keyboard_interrupt_has_its_own_exit_and_message(self) -> None:
        """Catches an operator interrupt being reported as an ordinary fault."""
        calls: list[str] = []
        dependencies, _ = _dependencies(calls, error=KeyboardInterrupt())
        stdout, stderr = _TextStream(), _TextStream()
        self.assertEqual(run_cli([str(_SESSION)], stdout=stdout, stderr=stderr,
                                 dependencies=dependencies), 130)
        self.assertEqual(stdout.value, "")
        self.assertEqual(stderr.value, "STOPPED: operator interrupt\n")

    def test_short_or_failed_success_output_halts_without_changing_to_a_usage_error(self) -> None:
        """Catches accepting a partial stdout state as a completed reconciliation."""
        cases = (
            (_TextStream(short=True), "final"),
            (_TextStream(write_error=OSError()), ""),
            (_TextStream(flush_error=OSError()), "final\n"),
            (_TextStream(write_error=KeyboardInterrupt()), ""),
            (_TextStream(flush_error=SystemExit()), "final\n"),
        )
        for stdout, expected_stdout in cases:
            with self.subTest(stdout=stdout):
                calls: list[str] = []
                dependencies, _ = _dependencies(calls, result=SimpleNamespace(state="final"))
                stderr = _TextStream()
                self.assertEqual(run_cli([str(_SESSION)], stdout=stdout, stderr=stderr,
                                         dependencies=dependencies), 1)
                # A stream can retain bytes it has already accepted; a CLI cannot retract them.
                self.assertEqual(stdout.value, expected_stdout)
                self.assertEqual(stderr.value, "HALTED: shadow_settlement_unavailable\n")

    def test_failed_stderr_is_best_effort_and_preserves_chosen_exit(self) -> None:
        """Catches error-reporting failures replacing usage, interrupt, or halt status."""
        scenarios = (
            ([], None, 2),
            ([str(_SESSION)], KeyboardInterrupt(), 130),
            ([str(_SESSION)], RuntimeError("bad"), 1),
        )
        for argv, error, expected in scenarios:
            with self.subTest(expected=expected):
                calls: list[str] = []
                dependencies, _ = _dependencies(calls, error=error)
                for stderr in (
                    _TextStream(short=True), _TextStream(write_error=OSError()),
                    _TextStream(flush_error=OSError()),
                    _TextStream(write_error=KeyboardInterrupt()),
                    _TextStream(flush_error=SystemExit()),
                ):
                    with self.subTest(stderr=stderr):
                        self.assertEqual(
                            run_cli(argv, stdout=_TextStream(), stderr=stderr,
                                    dependencies=dependencies),
                            expected,
                        )

    def test_default_factories_are_frozen_lazy_and_use_reviewed_constructors(self) -> None:
        """Catches default composition acquiring a boundary early or with changed inputs."""
        calls: list[str] = []
        transport, store = object(), object()
        clock_type = settlement_cli._SystemClocks
        with patch.object(settlement_cli, "KalshiShadowSettlementTransport",
                          side_effect=lambda: calls.append("transport") or transport) as transport_ctor, patch.object(
                settlement_cli, "ShadowSettlementLabelStore",
                side_effect=lambda: calls.append("store") or store,
        ) as store_ctor, patch.object(
                settlement_cli, "_SystemClocks",
                side_effect=lambda: calls.append("clock") or clock_type(),
        ) as clock_ctor, patch.object(settlement_cli.time, "time_ns", return_value=11) as wall, patch.object(
                settlement_cli.time, "monotonic_ns", return_value=12,
        ) as monotonic:
            dependencies = ShadowSettlementCliDependencies()
            with self.assertRaises(FrozenInstanceError):
                dependencies.transport_factory = lambda: object()  # type: ignore[misc]
            self.assertEqual(calls, [])
            self.assertIs(dependencies.transport_factory(), transport)
            self.assertIs(dependencies.store_factory(), store)
            clocks = dependencies.clocks_factory()
            self.assertEqual(calls, ["transport", "store", "clock"])
            self.assertEqual(clocks.wall_ns(), 11)
            self.assertEqual(clocks.monotonic_ns(), 12)
        transport_ctor.assert_called_once_with()
        store_ctor.assert_called_once_with()
        clock_ctor.assert_called_once_with()
        wall.assert_called_once_with()
        monotonic.assert_called_once_with()

    def test_main_delegates_to_run_cli(self) -> None:
        """Catches the module entry point bypassing the injected command boundary."""
        with patch("inci_tennis_runtime.shadow_settlement_cli.run_cli", return_value=17) as run:
            self.assertEqual(main(), 17)
        run.assert_called_once_with()

    def test_static_policy_seals_the_exact_module_and_rejects_dynamic_authority_mutations(self) -> None:
        """Catches imports or indirect calls that add environment or external authority."""
        source_path = Path(__file__).parents[2] / "inci_tennis_runtime" / "shadow_settlement_cli.py"
        source = source_path.read_text(encoding="utf-8")
        _assert_reviewed_cli_ast(source)
        for mutation in (
            "\n__import__(\"external_\" + \"provider\")\n",
            "\nimport importlib\nimportlib.import_module(\"network\")\n",
            "\ngetattr(object(), \"execute_\" + \"order\")()\n",
            "\nfrom os import getenv as read_setting\n",
            "\nfrom configparser import ConfigParser as Settings\n",
            "\nfrom secrets import token_urlsafe as secret_text\n",
            "\nfrom requests import Session as PublicSession\n",
        ):
            with self.subTest(mutation=mutation):
                self.assertNotEqual(_canonical_ast_sha256(source + mutation),
                                    _REVIEWED_CLI_AST_SHA256)
                with self.assertRaises(AssertionError):
                    _assert_reviewed_cli_ast(source + mutation)


if __name__ == "__main__":
    unittest.main()
