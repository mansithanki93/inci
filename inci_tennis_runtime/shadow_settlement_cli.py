"""Read-only command wiring for finalized shadow-settlement labels."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from re import compile as pattern_compile
import sys
import time
from typing import Callable

from inci_tennis_io.kalshi_shadow_settlement import KalshiShadowSettlementTransport
from inci_tennis_io.shadow_settlement_labels import (
    ShadowSettlementLabelStore,
    reconcile_shadow_settlement,
)


_SAFE_CODE = pattern_compile(r"(?:shadow|kalshi)_[a-z0-9_]{1,96}\Z")
_STATES = frozenset(("pending", "final", "conflict"))


class _UsageError(ValueError):
    pass


class _HelpRequested(Exception):
    pass


class _SystemClocks:
    def wall_ns(self) -> int:
        return time.time_ns()

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


def _write_complete(stream: object, text: str) -> bool:
    try:
        written = stream.write(text)  # type: ignore[attr-defined]
        if type(written) is not int or written != len(text):
            return False
        stream.flush()  # type: ignore[attr-defined]
        return True
    except BaseException:
        return False


class _Parser(argparse.ArgumentParser):
    def __init__(self, output: object) -> None:
        super().__init__(
            prog="python -m inci_tennis_runtime.shadow_settlement_cli",
            allow_abbrev=False,
        )
        self._output = output
        self.add_argument("session_path", type=Path)

    def _print_message(self, message: str | None, file: object | None = None) -> None:
        del file
        if message is not None:
            _write_complete(self._output, message)

    def error(self, message: str) -> None:
        del message
        raise _UsageError

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message is not None:
            self._print_message(message)
        if status == 0:
            raise _HelpRequested
        raise _UsageError


def _arguments(argv: list[str] | None, output: object) -> Path:
    parser = _Parser(output)
    arguments = sys.argv[1:] if argv is None else argv
    parsed = parser.parse_args(arguments)
    path = parsed.session_path
    if not isinstance(path, Path) or not path.is_absolute():
        raise _UsageError
    return path


def _transport() -> object:
    return KalshiShadowSettlementTransport()


def _store() -> object:
    return ShadowSettlementLabelStore()


def _clocks() -> object:
    return _SystemClocks()


@dataclass(frozen=True, slots=True)
class ShadowSettlementCliDependencies:
    transport_factory: Callable[[], object] = _transport
    store_factory: Callable[[], object] = _store
    clocks_factory: Callable[[], object] = _clocks
    reconcile: Callable[[Path, object, object, object], object] = reconcile_shadow_settlement


def _safe_code(error: BaseException) -> str:
    try:
        candidates = (getattr(error, "code", None), str(error))
    except BaseException:
        return "shadow_settlement_unavailable"
    for candidate in candidates:
        if type(candidate) is str and _SAFE_CODE.fullmatch(candidate) is not None:
            return candidate
    return "shadow_settlement_unavailable"


def _halt(error_stream: object, error: BaseException) -> int:
    _write_complete(error_stream, f"HALTED: {_safe_code(error)}\n")
    return 1


def run_cli(
    argv: list[str] | None = None,
    *,
    stdout: object | None = None,
    stderr: object | None = None,
    dependencies: ShadowSettlementCliDependencies | None = None,
) -> int:
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr
    try:
        session_path = _arguments(argv, output_stream)
    except _HelpRequested:
        return 0
    except _UsageError:
        _write_complete(error_stream, "ERROR: invalid command arguments\n")
        return 2
    except KeyboardInterrupt:
        _write_complete(error_stream, "STOPPED: operator interrupt\n")
        return 130
    except BaseException as error:
        return _halt(error_stream, error)
    try:
        services = ShadowSettlementCliDependencies() if dependencies is None else dependencies
        transport = services.transport_factory()
        store = services.store_factory()
        clocks = services.clocks_factory()
        result = services.reconcile(session_path, transport, store, clocks)
        state = result.state  # type: ignore[attr-defined]
        if type(state) is not str or state not in _STATES:
            raise RuntimeError("shadow_settlement_unavailable")
        if not _write_complete(output_stream, state + "\n"):
            raise RuntimeError("shadow_settlement_unavailable")
        return 0
    except KeyboardInterrupt:
        _write_complete(error_stream, "STOPPED: operator interrupt\n")
        return 130
    except BaseException as error:
        return _halt(error_stream, error)


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "ShadowSettlementCliDependencies",
    "main",
    "run_cli",
)
