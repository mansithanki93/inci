"""Durable, public-Kalshi-only labels for finalized tennis shadow sessions.

This store detects non-racing at-rest corruption and requires cooperative writers
to honor its advisory lock.  Durable reads are finite: epoch and commit files are
limited to 1 KiB, pending descriptors to 32 KiB, each raw body to the transport's
8 MiB maximum, and the ledger to 64 MiB, 10,000 rows, and 64 KiB per row.  One
audit may read at most 1 GiB in aggregate, including recovery revalidation.  It
does not defend against a malicious same-UID process racing checks, bypassing
locks, changing process memory or descriptors, or coherently rolling back or
deleting the state root.  Reverification of the held source lease narrows, but
cannot eliminate, that boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import pwd
from re import compile as pattern_compile
import stat
from typing import Callable, Literal, Protocol, Sequence
from uuid import UUID, uuid4

from inci_tennis_io.kalshi_shadow_settlement import (
    KalshiFinalMarketState,
    KalshiShadowSettlementTransport,
)
from inci_tennis_io.shadow_evidence import (
    AuditedShadowSettlementSource,
    audit_shadow_settlement_source,
)


_EPOCH_SCHEMA = "inci-tennis-shadow-settlement-epoch-v1"
_PENDING_SCHEMA = "inci-tennis-shadow-settlement-pending-v1"
_COMMIT_SCHEMA = "inci-tennis-shadow-settlement-commit-v1"
_ROW_SCHEMA = "inci-tennis-shadow-settlement-row-v1"
_ZERO_DIGEST = "0" * 64
_DIGEST_RE = pattern_compile(r"[0-9a-f]{64}\Z")
_DECIMAL_RE = pattern_compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_TICKER_RE = pattern_compile(r"[A-Z0-9][A-Z0-9._-]{0,127}\Z")
_TOKEN_RE = pattern_compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_RFC3339_RE = pattern_compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z\Z"
)
_RECOGNIZED_STATUSES = frozenset(
    {"initialized", "inactive", "active", "closed", "determined", "disputed", "amended", "finalized"}
)
_ROOT_NAMES = frozenset(
    {
        "raw",
        "settlement.lock",
        "settlement.epoch",
        "settlement.pending",
        "settlement.commit",
        "settlements.jsonl",
    }
)
_MAX_EPOCH_BYTES = 1024
_MAX_COMMIT_BYTES = 1024
_MAX_PENDING_BYTES = 32 * 1024
_MAX_RAW_BODY_BYTES = 8_388_608
_MAX_LEDGER_BYTES = 64 * 1024 * 1024
_MAX_LEDGER_LINE_BYTES = 64 * 1024
_MAX_LEDGER_ROWS = 10_000
_MAX_RAW_FILES = _MAX_LEDGER_ROWS * 2
_MAX_AUDIT_BYTES = 1_073_741_824
_ROW_KEYS = frozenset(
    {
        "schema",
        "transaction_id",
        "row_number",
        "source_path",
        "source_ledger_sha256",
        "source_session_id",
        "source_mode",
        "event_ticker",
        "market_tickers",
        "player_names",
        "source_first_row_sha256",
        "source_terminal_row_sha256",
        "markets",
        "state",
        "winning_market_ticker",
        "winning_player_name",
        "reconciled_wall_ns",
        "reconciled_monotonic_ns",
        "supersedes_row_sha256",
        "previous_row_sha256",
        "row_sha256",
    }
)
_MARKET_KEYS = frozenset(
    {
        "ticker",
        "event_ticker",
        "market_type",
        "status",
        "result",
        "settlement_value_dollars",
        "settlement_ts",
        "route_tier",
        "raw_path",
        "raw_sha256",
    }
)


class ShadowSettlementError(RuntimeError):
    """A durable settlement artifact or operation failed closed."""


@dataclass(frozen=True, slots=True)
class ShadowSettlementResult:
    state: Literal["pending", "final", "conflict"]
    winning_market_ticker: str | None
    winning_player_name: str | None


class ShadowSettlementClocks(Protocol):
    def wall_ns(self) -> int: ...

    def monotonic_ns(self) -> int: ...


def default_shadow_settlement_state_root() -> Path:
    return (
        Path(pwd.getpwuid(os.getuid()).pw_dir)
        / ".local/state/inci/tennis-shadow-settlement"
    )


@dataclass(frozen=True, slots=True)
class _Audit:
    rows: tuple[dict[str, object], ...]
    pending: dict[str, object] | None
    recovery: Literal["advance", "cleanup"] | None
    ledger_bytes: int
    raw_files: int
    audit_bytes: int


@dataclass(slots=True)
class _AuditByteBudget:
    consumed: int = 0

    def read(
        self,
        path: Path,
        *,
        maximum_bytes: int,
        empty: bool = False,
    ) -> bytes:
        info = _safe_regular(path, empty=empty)
        if info.st_size < 0 or self.consumed + info.st_size > _MAX_AUDIT_BYTES:
            raise ShadowSettlementError(
                "shadow_settlement_audit_capacity_invalid"
            )
        payload = _read_safe(
            path, maximum_bytes=maximum_bytes, empty=empty
        )
        if self.consumed + len(payload) > _MAX_AUDIT_BYTES:
            raise ShadowSettlementError(
                "shadow_settlement_audit_capacity_invalid"
            )
        self.consumed += len(payload)
        return payload


@dataclass(frozen=True, slots=True)
class _PreparedCommit:
    transaction_id: str
    reconciled_wall_ns: int
    reconciled_monotonic_ns: int
    raw_paths: tuple[Path, Path]
    row: dict[str, object]
    row_line: bytes
    pending_payload: bytes
    epoch_payload: bytes
    commit_payload: bytes


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ShadowSettlementError("shadow_settlement_json_invalid") from exc


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ShadowSettlementError("shadow_settlement_json_duplicate")
        value[key] = item
    return value


def _parse_canonical(payload: bytes, *, name: str) -> dict[str, object]:
    if not payload.endswith(b"\n") or payload == b"\n" or b"\r" in payload:
        raise ShadowSettlementError(f"shadow_settlement_{name}_encoding_invalid")
    try:
        decoded = payload[:-1].decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            object_pairs_hook=_object_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ShadowSettlementError("shadow_settlement_json_nonfinite")
            ),
        )
    except ShadowSettlementError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShadowSettlementError(f"shadow_settlement_{name}_json_invalid") from exc
    if type(value) is not dict or _canonical_json(value) + b"\n" != payload:
        raise ShadowSettlementError(f"shadow_settlement_{name}_canonical_invalid")
    return value


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_digest(value: object) -> bool:
    return type(value) is str and _DIGEST_RE.fullmatch(value) is not None


def _is_uuid4(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return False
    return str(parsed) == value and parsed.version == 4 and parsed.variant == "specified in RFC 4122"


def _validate_root_configuration(root: Path) -> None:
    if not root.is_absolute() or Path(os.path.abspath(root)) != root:
        raise ShadowSettlementError("shadow_settlement_root_noncanonical")
    if root.is_symlink():
        raise ShadowSettlementError("shadow_settlement_root_symlink")
    if root.exists() and root.resolve(strict=True) != root:
        raise ShadowSettlementError("shadow_settlement_root_noncanonical")
    if not root.exists() and root.parent.resolve(strict=False) / root.name != root:
        raise ShadowSettlementError("shadow_settlement_root_noncanonical")


def _safe_directory(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ShadowSettlementError("shadow_settlement_directory_missing") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.getuid()
    ):
        raise ShadowSettlementError("shadow_settlement_directory_unsafe")
    return info


def _safe_regular(path: Path, *, empty: bool = False) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ShadowSettlementError("shadow_settlement_file_missing") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or (empty and info.st_size != 0)
    ):
        raise ShadowSettlementError("shadow_settlement_file_unsafe")
    return info


def _read_safe(path: Path, *, maximum_bytes: int, empty: bool = False) -> bytes:
    if type(maximum_bytes) is not int or maximum_bytes < 0:
        raise ShadowSettlementError("shadow_settlement_size_policy_invalid")
    expected = _safe_regular(path, empty=empty)
    if expected.st_size > maximum_bytes:
        raise ShadowSettlementError("shadow_settlement_file_size_invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    primary: BaseException | None = None
    try:
        actual = os.fstat(descriptor)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ShadowSettlementError("shadow_settlement_file_replaced")
        if actual.st_size > maximum_bytes:
            raise ShadowSettlementError("shadow_settlement_file_size_invalid")
        payload = bytearray()
        while True:
            remaining = maximum_bytes - len(payload)
            chunk = os.read(descriptor, min(1024 * 1024, remaining + 1))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > maximum_bytes:
                raise ShadowSettlementError("shadow_settlement_file_size_invalid")
        if empty and payload:
            raise ShadowSettlementError("shadow_settlement_lock_nonempty")
        return bytes(payload)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            os.close(descriptor)
        except BaseException:
            if primary is None:
                raise


def _directory_fsync(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    primary: BaseException | None = None
    try:
        os.fsync(descriptor)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            os.close(descriptor)
        except BaseException:
            if primary is None:
                raise


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if type(written) is not int or written <= 0:
            raise ShadowSettlementError("shadow_settlement_write_failed")
        offset += written


def _write_new_file(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    primary: BaseException | None = None
    try:
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            os.close(descriptor)
        except BaseException:
            if primary is None:
                raise


def _publish_new(path: Path, payload: bytes, root: Path) -> None:
    temporary = root / f".{path.name}.{uuid4()}.tmp"
    _write_new_file(temporary, payload)
    primary: BaseException | None = None
    try:
        if path.exists() or path.is_symlink():
            raise ShadowSettlementError("shadow_settlement_publication_exists")
        os.replace(temporary, path)
        _directory_fsync(root)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if temporary.exists() or temporary.is_symlink():
            try:
                temporary.unlink()
            except BaseException:
                if primary is None:
                    raise


def _replace_file(path: Path, payload: bytes, root: Path) -> None:
    temporary = root / f".{path.name}.{uuid4()}.tmp"
    _write_new_file(temporary, payload)
    primary: BaseException | None = None
    try:
        os.replace(temporary, path)
        _directory_fsync(root)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if temporary.exists() or temporary.is_symlink():
            try:
                temporary.unlink()
            except BaseException:
                if primary is None:
                    raise


def _append_row(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    primary: BaseException | None = None
    try:
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            os.close(descriptor)
        except BaseException:
            if primary is None:
                raise


def _unlink_and_sync(path: Path, root: Path) -> None:
    path.unlink()
    _directory_fsync(root)


def _epoch_bytes() -> bytes:
    return _canonical_json(
        {
            "schema": _EPOCH_SCHEMA,
            "row_schema": _ROW_SCHEMA,
            "pending_schema": _PENDING_SCHEMA,
            "commit_schema": _COMMIT_SCHEMA,
        }
    ) + b"\n"


def _commit_bytes(row_number: int, digest: str) -> bytes:
    return _canonical_json(
        {"schema": _COMMIT_SCHEMA, "row_number": row_number, "row_sha256": digest}
    ) + b"\n"


def _validate_epoch(value: dict[str, object]) -> None:
    if value != {
        "schema": _EPOCH_SCHEMA,
        "row_schema": _ROW_SCHEMA,
        "pending_schema": _PENDING_SCHEMA,
        "commit_schema": _COMMIT_SCHEMA,
    }:
        raise ShadowSettlementError("shadow_settlement_epoch_invalid")


def _validate_commit(value: dict[str, object]) -> None:
    if set(value) != {"schema", "row_number", "row_sha256"}:
        raise ShadowSettlementError("shadow_settlement_commit_fields_invalid")
    if (
        value["schema"] != _COMMIT_SCHEMA
        or not _is_nonnegative_int(value["row_number"])
        or value["row_number"] == 0
        or not _is_digest(value["row_sha256"])
    ):
        raise ShadowSettlementError("shadow_settlement_commit_invalid")


def _source_identity(source: AuditedShadowSettlementSource) -> tuple[object, ...]:
    return (
        str(source.session_path),
        source.ledger_sha256,
        source.session_id,
        source.mode,
        source.event_ticker,
        tuple(source.market_tickers),
        tuple(source.player_names),
        source.first_row_sha256,
        source.terminal_row_sha256,
    )


def _row_source_identity(row: dict[str, object]) -> tuple[object, ...]:
    return (
        row["source_path"],
        row["source_ledger_sha256"],
        row["source_session_id"],
        row["source_mode"],
        row["event_ticker"],
        tuple(row["market_tickers"]),  # type: ignore[arg-type]
        tuple(row["player_names"]),  # type: ignore[arg-type]
        row["source_first_row_sha256"],
        row["source_terminal_row_sha256"],
    )


def _validate_row(
    row: dict[str, object],
    *,
    root: Path,
    row_number: int,
    previous_digest: str,
    raw_inventory: set[Path],
    read_safe: Callable[..., bytes] = _read_safe,
) -> ShadowSettlementResult:
    if set(row) != _ROW_KEYS:
        raise ShadowSettlementError("shadow_settlement_row_fields_invalid")
    if (
        row["schema"] != _ROW_SCHEMA
        or not _is_uuid4(row["transaction_id"])
        or row["row_number"] != row_number
        or row["previous_row_sha256"] != previous_digest
        or not _is_digest(row["row_sha256"])
        or not _is_digest(row["source_ledger_sha256"])
        or not _is_digest(row["source_first_row_sha256"])
        or not _is_digest(row["source_terminal_row_sha256"])
        or type(row["source_session_id"]) is not str
        or not row["source_session_id"]
        or row["source_mode"] not in ("VERIFIED", "PRICE_ONLY")
        or type(row["event_ticker"]) is not str
        or type(row["source_path"]) is not str
        or not _is_nonnegative_int(row["reconciled_wall_ns"])
        or not _is_nonnegative_int(row["reconciled_monotonic_ns"])
    ):
        raise ShadowSettlementError("shadow_settlement_row_invalid")
    source_path = Path(row["source_path"])
    if not source_path.is_absolute() or source_path.resolve(strict=False) != source_path:
        raise ShadowSettlementError("shadow_settlement_source_path_invalid")
    tickers = row["market_tickers"]
    players = row["player_names"]
    if (
        type(tickers) is not list
        or len(tickers) != 2
        or any(type(item) is not str or _TICKER_RE.fullmatch(item) is None for item in tickers)
        or len(set(tickers)) != 2
        or type(players) is not list
        or len(players) != 2
        or any(type(item) is not str or not item for item in players)
    ):
        raise ShadowSettlementError("shadow_settlement_source_pair_invalid")
    state = row["state"]
    winner_ticker = row["winning_market_ticker"]
    winner_player = row["winning_player_name"]
    if state == "final":
        if winner_ticker not in tickers or type(winner_player) is not str:
            raise ShadowSettlementError("shadow_settlement_winner_invalid")
        if players[tickers.index(winner_ticker)] != winner_player:
            raise ShadowSettlementError("shadow_settlement_winner_invalid")
    elif state == "conflict":
        if winner_ticker is not None or winner_player is not None:
            raise ShadowSettlementError("shadow_settlement_conflict_winner_invalid")
    else:
        raise ShadowSettlementError("shadow_settlement_state_invalid")
    supersedes = row["supersedes_row_sha256"]
    if supersedes is not None and not _is_digest(supersedes):
        raise ShadowSettlementError("shadow_settlement_supersedes_invalid")
    if row_number == 1 and supersedes is not None:
        raise ShadowSettlementError("shadow_settlement_first_supersedes_invalid")
    markets = row["markets"]
    if type(markets) is not list or len(markets) != 2:
        raise ShadowSettlementError("shadow_settlement_markets_invalid")
    persisted_states: list[KalshiFinalMarketState] = []
    for index, market in enumerate(markets):
        if type(market) is not dict or set(market) != _MARKET_KEYS:
            raise ShadowSettlementError("shadow_settlement_market_fields_invalid")
        raw_path_value = market["raw_path"]
        if type(raw_path_value) is not str or type(market["raw_sha256"]) is not str:
            raise ShadowSettlementError("shadow_settlement_raw_reference_invalid")
        raw_path = Path(raw_path_value)
        expected_name = (
            f"settlement-{row['transaction_id']}-{index:02d}-{tickers[index]}.json"
        )
        if (
            raw_path != root / "raw" / expected_name
            or raw_path.resolve(strict=False) != raw_path
            or not _is_digest(market["raw_sha256"])
        ):
            raise ShadowSettlementError("shadow_settlement_market_binding_invalid")
        if raw_path not in raw_inventory:
            raise ShadowSettlementError("shadow_settlement_raw_missing")
        payload = read_safe(raw_path, maximum_bytes=_MAX_RAW_BODY_BYTES)
        if sha256(payload).hexdigest() != market["raw_sha256"]:
            raise ShadowSettlementError("shadow_settlement_raw_digest_invalid")
        state_value = KalshiFinalMarketState(
            ticker=market["ticker"],
            event_ticker=market["event_ticker"],
            market_type=market["market_type"],
            status=market["status"],
            result=market["result"],
            settlement_value_dollars=market["settlement_value_dollars"],
            settlement_ts=market["settlement_ts"],
            raw_body=payload,
            raw_sha256=market["raw_sha256"],
            route_tier=market["route_tier"],
        )
        _validate_market_state_syntax(state_value)
        persisted_states.append(state_value)
    candidate = dict(row)
    digest = candidate.pop("row_sha256")
    if sha256(_canonical_json(candidate)).hexdigest() != digest:
        raise ShadowSettlementError("shadow_settlement_row_digest_invalid")
    evidence = _classify_values(
        event_ticker=row["event_ticker"],  # type: ignore[arg-type]
        market_tickers=tuple(tickers),  # type: ignore[arg-type]
        player_names=tuple(players),  # type: ignore[arg-type]
        states=persisted_states,
    )
    if state == "final" and evidence != ShadowSettlementResult(
        "final", winner_ticker, winner_player  # type: ignore[arg-type]
    ):
        raise ShadowSettlementError("shadow_settlement_final_semantics_invalid")
    return evidence


def _validate_pending(
    value: dict[str, object],
    *,
    root: Path,
    tail: dict[str, object],
    read_safe: Callable[..., bytes] = _read_safe,
) -> None:
    if set(value) != {
        "schema",
        "transaction_id",
        "row_number",
        "previous_row_sha256",
        "row_sha256",
        "raw_files",
    }:
        raise ShadowSettlementError("shadow_settlement_pending_fields_invalid")
    if (
        value["schema"] != _PENDING_SCHEMA
        or not _is_uuid4(value["transaction_id"])
        or value["transaction_id"] != tail["transaction_id"]
        or value["row_number"] != tail["row_number"]
        or value["previous_row_sha256"] != tail["previous_row_sha256"]
        or value["row_sha256"] != tail["row_sha256"]
    ):
        raise ShadowSettlementError("shadow_settlement_pending_invalid")
    files = value["raw_files"]
    markets = tail["markets"]
    if type(files) is not list or len(files) != 2 or type(markets) is not list:
        raise ShadowSettlementError("shadow_settlement_pending_raw_invalid")
    expected: list[dict[str, object]] = []
    for market in markets:
        if type(market) is not dict:
            raise ShadowSettlementError("shadow_settlement_pending_raw_invalid")
        expected.append({"path": market["raw_path"], "sha256": market["raw_sha256"]})
    if files != expected:
        raise ShadowSettlementError("shadow_settlement_pending_raw_invalid")
    for item in files:
        if type(item) is not dict:
            raise ShadowSettlementError("shadow_settlement_pending_raw_invalid")
        path = Path(item["path"])  # type: ignore[arg-type]
        if (
            path.parent != root / "raw"
            or sha256(
                read_safe(path, maximum_bytes=_MAX_RAW_BODY_BYTES)
            ).hexdigest()
            != item["sha256"]
        ):
            raise ShadowSettlementError("shadow_settlement_pending_raw_invalid")


def _audit_root(root: Path) -> _Audit:
    _validate_root_configuration(root)
    _safe_directory(root)
    budget = _AuditByteBudget()
    names: set[str] = set()
    for entry in root.iterdir():
        if entry.name not in _ROOT_NAMES or len(names) >= len(_ROOT_NAMES):
            raise ShadowSettlementError("shadow_settlement_inventory_invalid")
        names.add(entry.name)
    if "raw" not in names or "settlement.lock" not in names:
        raise ShadowSettlementError("shadow_settlement_inventory_invalid")
    raw_root = root / "raw"
    _safe_directory(raw_root)
    budget.read(root / "settlement.lock", maximum_bytes=0, empty=True)
    raw_inventory: set[Path] = set()
    for path in raw_root.iterdir():
        if len(raw_inventory) >= _MAX_RAW_FILES:
            raise ShadowSettlementError("shadow_settlement_raw_count_invalid")
        _safe_regular(path)
        if path.name.startswith(".") or path.suffix != ".json":
            raise ShadowSettlementError("shadow_settlement_raw_inventory_invalid")
        raw_inventory.add(path)

    epoch_path = root / "settlement.epoch"
    if epoch_path.exists() or epoch_path.is_symlink():
        _validate_epoch(
            _parse_canonical(
                budget.read(epoch_path, maximum_bytes=_MAX_EPOCH_BYTES),
                name="epoch",
            )
        )

    rows: list[dict[str, object]] = []
    ledger_bytes = 0
    ledger_path = root / "settlements.jsonl"
    if ledger_path.exists() or ledger_path.is_symlink():
        payload = budget.read(
            ledger_path, maximum_bytes=_MAX_LEDGER_BYTES
        )
        ledger_bytes = len(payload)
        if not payload:
            raise ShadowSettlementError("shadow_settlement_ledger_empty")
        lines = payload.splitlines(keepends=True)
        if len(lines) > _MAX_LEDGER_ROWS:
            raise ShadowSettlementError("shadow_settlement_ledger_row_count_invalid")
        if any(len(line) > _MAX_LEDGER_LINE_BYTES for line in lines):
            raise ShadowSettlementError("shadow_settlement_ledger_line_size_invalid")
        previous = _ZERO_DIGEST
        consumed_raw: set[Path] = set()
        source_histories: dict[tuple[object, ...], list[dict[str, object]]] = {}
        for index, line in enumerate(lines, start=1):
            row = _parse_canonical(line, name="row")
            evidence = _validate_row(
                row,
                root=root,
                row_number=index,
                previous_digest=previous,
                raw_inventory=raw_inventory,
                read_safe=budget.read,
            )
            identity = _row_source_identity(row)
            history = source_histories.setdefault(identity, [])
            if not history:
                if row["supersedes_row_sha256"] is not None:
                    raise ShadowSettlementError("shadow_settlement_first_supersedes_invalid")
                if row["state"] == "final":
                    if evidence.state != "final":
                        raise ShadowSettlementError("shadow_settlement_first_final_invalid")
                elif evidence.state != "conflict":
                    raise ShadowSettlementError("shadow_settlement_first_conflict_invalid")
            else:
                first = history[0]
                if (
                    len(history) != 1
                    or first["state"] != "final"
                    or row["state"] != "conflict"
                    or row["supersedes_row_sha256"] != first["row_sha256"]
                ):
                    raise ShadowSettlementError("shadow_settlement_history_invalid")
                if _row_normalized_markets(row) == _row_normalized_markets(first):
                    raise ShadowSettlementError("shadow_settlement_conflict_unchanged")
            history.append(row)
            for market in row["markets"]:  # type: ignore[assignment]
                consumed_raw.add(Path(market["raw_path"]))
            rows.append(row)
            previous = row["row_sha256"]  # type: ignore[assignment]
        if consumed_raw != raw_inventory:
            raise ShadowSettlementError("shadow_settlement_raw_orphan")
    elif raw_inventory:
        raise ShadowSettlementError("shadow_settlement_raw_without_ledger")

    if rows and not epoch_path.exists():
        raise ShadowSettlementError("shadow_settlement_epoch_missing")

    commit: dict[str, object] | None = None
    commit_path = root / "settlement.commit"
    if commit_path.exists() or commit_path.is_symlink():
        commit = _parse_canonical(
            budget.read(commit_path, maximum_bytes=_MAX_COMMIT_BYTES),
            name="commit",
        )
        _validate_commit(commit)
    elif rows and not (root / "settlement.pending").exists():
        raise ShadowSettlementError("shadow_settlement_commit_missing")
    if not rows and commit is not None:
        raise ShadowSettlementError("shadow_settlement_commit_without_rows")

    pending: dict[str, object] | None = None
    recovery: Literal["advance", "cleanup"] | None = None
    pending_path = root / "settlement.pending"
    if pending_path.exists() or pending_path.is_symlink():
        if not rows:
            raise ShadowSettlementError("shadow_settlement_pending_without_row")
        pending = _parse_canonical(
            budget.read(pending_path, maximum_bytes=_MAX_PENDING_BYTES),
            name="pending",
        )
        _validate_pending(
            pending,
            root=root,
            tail=rows[-1],
            read_safe=budget.read,
        )
        tail_number = len(rows)
        tail_digest = rows[-1]["row_sha256"]
        if commit is not None and commit == {
            "schema": _COMMIT_SCHEMA,
            "row_number": tail_number,
            "row_sha256": tail_digest,
        }:
            recovery = "cleanup"
        else:
            predecessor_number = tail_number - 1
            predecessor_digest = rows[-2]["row_sha256"] if predecessor_number else None
            predecessor_ok = (
                commit is None
                if predecessor_number == 0
                else commit
                == {
                    "schema": _COMMIT_SCHEMA,
                    "row_number": predecessor_number,
                    "row_sha256": predecessor_digest,
                }
            )
            if not predecessor_ok:
                raise ShadowSettlementError("shadow_settlement_pending_commit_invalid")
            recovery = "advance"
    elif rows:
        expected_commit = {
            "schema": _COMMIT_SCHEMA,
            "row_number": len(rows),
            "row_sha256": rows[-1]["row_sha256"],
        }
        if commit != expected_commit:
            raise ShadowSettlementError("shadow_settlement_commit_tail_invalid")

    if not rows and names - {"raw", "settlement.lock", "settlement.epoch"}:
        raise ShadowSettlementError("shadow_settlement_empty_inventory_invalid")
    return _Audit(
        tuple(rows),
        pending,
        recovery,
        ledger_bytes,
        len(raw_inventory),
        budget.consumed,
    )


def _market_object(state: KalshiFinalMarketState, raw_path: Path) -> dict[str, object]:
    return {
        "ticker": state.ticker,
        "event_ticker": state.event_ticker,
        "market_type": state.market_type,
        "status": state.status,
        "result": state.result,
        "settlement_value_dollars": state.settlement_value_dollars,
        "settlement_ts": state.settlement_ts,
        "route_tier": state.route_tier,
        "raw_path": str(raw_path),
        "raw_sha256": state.raw_sha256,
    }


def _normalized_market(state: KalshiFinalMarketState) -> dict[str, object]:
    return {
        "ticker": state.ticker,
        "event_ticker": state.event_ticker,
        "market_type": state.market_type,
        "status": state.status,
        "result": state.result,
        "settlement_value_dollars": state.settlement_value_dollars,
        "settlement_ts": state.settlement_ts,
        "route_tier": state.route_tier,
        "raw_sha256": state.raw_sha256,
    }


def _row_normalized_markets(row: dict[str, object]) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for market in row["markets"]:  # type: ignore[assignment]
        result.append({key: value for key, value in market.items() if key != "raw_path"})
    return tuple(result)


def _valid_rfc3339(value: object) -> bool:
    if type(value) is not str or _RFC3339_RE.fullmatch(value) is None:
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def _validate_market_state_syntax(state: KalshiFinalMarketState) -> None:
    if (
        type(state.ticker) is not str
        or _TICKER_RE.fullmatch(state.ticker) is None
        or type(state.event_ticker) is not str
        or _TICKER_RE.fullmatch(state.event_ticker) is None
        or type(state.market_type) is not str
        or _TOKEN_RE.fullmatch(state.market_type) is None
        or type(state.status) is not str
        or state.status not in _RECOGNIZED_STATUSES
        or type(state.route_tier) is not str
        or state.route_tier not in ("current", "historical")
    ):
        raise ShadowSettlementError("shadow_settlement_market_syntax_invalid")
    result = state.result
    if (
        result is not None
        and (
            type(result) is not str
            or (result != "" and _TOKEN_RE.fullmatch(result) is None)
        )
    ):
        raise ShadowSettlementError("shadow_settlement_result_syntax_invalid")
    value = state.settlement_value_dollars
    if (
        value is not None
        and (
            type(value) is not str
            or (value != "" and _DECIMAL_RE.fullmatch(value) is None)
        )
    ):
        raise ShadowSettlementError("shadow_settlement_decimal_syntax_invalid")
    timestamp = state.settlement_ts
    if (
        timestamp is not None
        and (
            type(timestamp) is not str
            or (timestamp != "" and not _valid_rfc3339(timestamp))
        )
    ):
        raise ShadowSettlementError("shadow_settlement_timestamp_syntax_invalid")
    if state.status == "finalized" and not (result and value and timestamp):
        raise ShadowSettlementError("shadow_settlement_final_fields_missing")
    if type(state.raw_body) is not bytes or not state.raw_body:
        raise ShadowSettlementError("shadow_settlement_raw_evidence_invalid")
    if len(state.raw_body) > _MAX_RAW_BODY_BYTES:
        raise ShadowSettlementError("shadow_settlement_raw_body_size_invalid")
    if not _is_digest(state.raw_sha256):
        raise ShadowSettlementError("shadow_settlement_raw_evidence_invalid")
    if sha256(state.raw_body).hexdigest() != state.raw_sha256:
        raise ShadowSettlementError("shadow_settlement_raw_digest_invalid")


def _validate_fetched(states: Sequence[KalshiFinalMarketState]) -> None:
    if len(states) != 2:
        raise ShadowSettlementError("shadow_settlement_market_count_invalid")
    for state in states:
        if not isinstance(state, KalshiFinalMarketState):
            raise ShadowSettlementError("shadow_settlement_market_state_invalid")
        if state.status not in _RECOGNIZED_STATUSES:
            raise ValueError("shadow_settlement_status_unknown")
        _validate_market_state_syntax(state)


def _classify_values(
    *,
    event_ticker: str,
    market_tickers: tuple[str, str],
    player_names: tuple[str, str],
    states: Sequence[KalshiFinalMarketState],
) -> ShadowSettlementResult:
    if any(state.status != "finalized" for state in states):
        return ShadowSettlementResult("pending", None, None)
    valid = True
    values: list[Decimal] = []
    for index, state in enumerate(states):
        valid = valid and state.ticker == market_tickers[index]
        valid = valid and state.event_ticker == event_ticker
        valid = valid and state.market_type == "binary"
        valid = valid and _valid_rfc3339(state.settlement_ts)
        text = state.settlement_value_dollars
        if type(text) is not str or _DECIMAL_RE.fullmatch(text) is None:
            valid = False
            values.append(Decimal(-1))
        else:
            try:
                values.append(Decimal(text))
            except InvalidOperation:
                valid = False
                values.append(Decimal(-1))
        if state.result not in ("yes", "no"):
            valid = False
    if len(values) != 2 or values[0] + values[1] != Decimal(1):
        valid = False
    yes = [index for index, state in enumerate(states) if state.result == "yes"]
    no = [index for index, state in enumerate(states) if state.result == "no"]
    if len(yes) != 1 or len(no) != 1:
        valid = False
    if valid:
        winner = yes[0]
        if values[winner] != Decimal(1) or values[no[0]] != Decimal(0):
            valid = False
    if not valid:
        return ShadowSettlementResult("conflict", None, None)
    return ShadowSettlementResult(
        "final", market_tickers[winner], player_names[winner]
    )


def _classify(
    source: AuditedShadowSettlementSource,
    states: Sequence[KalshiFinalMarketState],
) -> ShadowSettlementResult:
    _validate_fetched(states)
    return _classify_values(
        event_ticker=source.event_ticker,
        market_tickers=source.market_tickers,
        player_names=source.player_names,
        states=states,
    )


def _clock_value(value: object) -> int:
    if not _is_nonnegative_int(value):
        raise ShadowSettlementError("shadow_settlement_clock_invalid")
    return value  # type: ignore[return-value]


def _source_from_lease(lease: object) -> AuditedShadowSettlementSource:
    source = AuditedShadowSettlementSource(
        session_path=lease.session_path,  # type: ignore[attr-defined]
        ledger_sha256=lease.ledger_sha256,  # type: ignore[attr-defined]
        session_id=lease.session_id,  # type: ignore[attr-defined]
        mode=lease.mode,  # type: ignore[attr-defined]
        event_ticker=lease.event_ticker,  # type: ignore[attr-defined]
        market_tickers=lease.market_tickers,  # type: ignore[attr-defined]
        player_names=lease.player_names,  # type: ignore[attr-defined]
        first_row_sha256=lease.first_row_sha256,  # type: ignore[attr-defined]
        terminal_row_sha256=lease.terminal_row_sha256,  # type: ignore[attr-defined]
    )
    if (
        source.session_path.resolve(strict=True) != source.session_path
        or source.mode not in ("VERIFIED", "PRICE_ONLY")
        or len(source.market_tickers) != 2
        or len(source.player_names) != 2
    ):
        raise ShadowSettlementError("shadow_settlement_source_invalid")
    return source


class ShadowSettlementLabelStore:
    """Configuration-only handle for one independent settlement state root."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_shadow_settlement_state_root()

    def _reconcile_with_lease(
        self,
        lease: object,
        transport: KalshiShadowSettlementTransport,
        clocks: ShadowSettlementClocks,
    ) -> ShadowSettlementResult:
        source = _source_from_lease(lease)
        verify_source = lease.verify_unchanged  # type: ignore[attr-defined]
        _validate_root_configuration(self.root)
        if self.root.exists() or self.root.is_symlink():
            return self._under_lock(source, transport, clocks, verify_source, None)

        states = tuple(
            transport.get_market_result(ticker) for ticker in source.market_tickers
        )
        initial = _classify(source, states)
        if initial.state == "pending":
            return initial
        prepared = self._prepare_commit(
            _Audit((), None, None, 0, 0, 0),
            source,
            states,
            initial,
            clocks,
            None,
            None,
        )
        self._bootstrap()
        return self._under_lock(
            source, transport, clocks, verify_source, states, prepared
        )

    def _bootstrap(self) -> None:
        root = self.root
        created_root = False
        try:
            os.mkdir(root, 0o700)
            created_root = True
            os.chmod(root, 0o700)
            _directory_fsync(root.parent)
        except FileExistsError:
            pass
        if created_root:
            os.mkdir(root / "raw", 0o700)
            os.chmod(root / "raw", 0o700)
            _directory_fsync(root)
            descriptor = os.open(
                root / "settlement.lock",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            primary: BaseException | None = None
            try:
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
            except BaseException as exc:
                primary = exc
                raise
            finally:
                try:
                    os.close(descriptor)
                except BaseException:
                    if primary is None:
                        raise
            _directory_fsync(root)

    def _under_lock(
        self,
        source: AuditedShadowSettlementSource,
        transport: KalshiShadowSettlementTransport,
        clocks: ShadowSettlementClocks,
        verify_source: Callable[[], None],
        prefetched: tuple[KalshiFinalMarketState, ...] | None,
        prepared_seed: _PreparedCommit | None = None,
    ) -> ShadowSettlementResult:
        lock_path = self.root / "settlement.lock"
        expected = _safe_regular(lock_path, empty=True)
        descriptor = os.open(lock_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        primary: BaseException | None = None
        try:
            actual = os.fstat(descriptor)
            if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise ShadowSettlementError("shadow_settlement_lock_replaced")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            audit = _audit_root(self.root)
            if audit.pending is not None:
                audit = self._recover(audit, source, verify_source)
            prior = self._lookup(audit.rows, source)
            if prior is not None and prior["state"] == "conflict":
                return ShadowSettlementResult("conflict", None, None)
            states = prefetched
            if states is None:
                states = tuple(
                    transport.get_market_result(ticker) for ticker in source.market_tickers
                )
            current = _classify(source, states)
            if prior is None:
                if current.state == "pending":
                    return current
                return self._commit(
                    audit,
                    source,
                    states,
                    current,
                    clocks,
                    verify_source,
                    None,
                    prepared_seed,
                )
            if _row_normalized_markets(prior) == tuple(
                _normalized_market(state) for state in states
            ):
                return ShadowSettlementResult(
                    "final",
                    prior["winning_market_ticker"],  # type: ignore[arg-type]
                    prior["winning_player_name"],  # type: ignore[arg-type]
                )
            conflict = ShadowSettlementResult("conflict", None, None)
            return self._commit(
                audit,
                source,
                states,
                conflict,
                clocks,
                verify_source,
                prior["row_sha256"],  # type: ignore[arg-type]
                prepared_seed,
            )
        except BaseException as exc:
            primary = exc
            raise
        finally:
            cleanup: BaseException | None = None
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except BaseException as exc:
                cleanup = exc
            try:
                os.close(descriptor)
            except BaseException as exc:
                if cleanup is None:
                    cleanup = exc
            if primary is None and cleanup is not None:
                raise cleanup

    def _recover(
        self,
        audit: _Audit,
        source: AuditedShadowSettlementSource,
        verify_source: Callable[[], None],
    ) -> _Audit:
        tail = audit.rows[-1]
        if _row_source_identity(tail) != _source_identity(source):
            raise ShadowSettlementError("shadow_settlement_pending_source_mismatch")
        verify_source()
        if audit.recovery == "advance":
            _replace_file(
                self.root / "settlement.commit",
                _commit_bytes(len(audit.rows), tail["row_sha256"]),  # type: ignore[arg-type]
                self.root,
            )
            verify_source()
        elif audit.recovery != "cleanup":
            raise ShadowSettlementError("shadow_settlement_pending_unrecoverable")
        else:
            verify_source()
        _unlink_and_sync(self.root / "settlement.pending", self.root)
        return _audit_root(self.root)

    @staticmethod
    def _lookup(
        rows: Sequence[dict[str, object]], source: AuditedShadowSettlementSource
    ) -> dict[str, object] | None:
        identity = _source_identity(source)
        matches = [row for row in rows if _row_source_identity(row) == identity]
        return matches[-1] if matches else None

    def _prepare_commit(
        self,
        audit: _Audit,
        source: AuditedShadowSettlementSource,
        states: Sequence[KalshiFinalMarketState],
        result: ShadowSettlementResult,
        clocks: ShadowSettlementClocks,
        supersedes: str | None,
        seed: _PreparedCommit | None,
    ) -> _PreparedCommit:
        _validate_fetched(states)
        next_row_number = len(audit.rows) + 1
        if next_row_number > _MAX_LEDGER_ROWS:
            raise ShadowSettlementError("shadow_settlement_row_capacity_invalid")
        if audit.raw_files + len(states) > _MAX_RAW_FILES:
            raise ShadowSettlementError("shadow_settlement_raw_count_capacity_invalid")
        epoch_payload = _epoch_bytes()
        if len(epoch_payload) > _MAX_EPOCH_BYTES:
            raise ShadowSettlementError("shadow_settlement_epoch_size_invalid")
        if seed is None:
            transaction_id = str(uuid4())
            wall_ns = _clock_value(clocks.wall_ns())
            monotonic_ns = _clock_value(clocks.monotonic_ns())
        else:
            transaction_id = seed.transaction_id
            wall_ns = seed.reconciled_wall_ns
            monotonic_ns = seed.reconciled_monotonic_ns
        raw_paths = tuple(
            self.root
            / "raw"
            / f"settlement-{transaction_id}-{index:02d}-{source.market_tickers[index]}.json"
            for index in range(2)
        )
        if len(raw_paths) != 2:
            raise ShadowSettlementError("shadow_settlement_raw_path_count_invalid")
        previous = audit.rows[-1]["row_sha256"] if audit.rows else _ZERO_DIGEST
        row: dict[str, object] = {
            "schema": _ROW_SCHEMA,
            "transaction_id": transaction_id,
            "row_number": next_row_number,
            "source_path": str(source.session_path),
            "source_ledger_sha256": source.ledger_sha256,
            "source_session_id": source.session_id,
            "source_mode": source.mode,
            "event_ticker": source.event_ticker,
            "market_tickers": list(source.market_tickers),
            "player_names": list(source.player_names),
            "source_first_row_sha256": source.first_row_sha256,
            "source_terminal_row_sha256": source.terminal_row_sha256,
            "markets": [
                _market_object(state, raw_paths[index])
                for index, state in enumerate(states)
            ],
            "state": result.state,
            "winning_market_ticker": result.winning_market_ticker,
            "winning_player_name": result.winning_player_name,
            "reconciled_wall_ns": wall_ns,
            "reconciled_monotonic_ns": monotonic_ns,
            "supersedes_row_sha256": supersedes,
            "previous_row_sha256": previous,
        }
        row["row_sha256"] = sha256(_canonical_json(row)).hexdigest()
        row_line = _canonical_json(row) + b"\n"
        if len(row_line) > _MAX_LEDGER_LINE_BYTES:
            raise ShadowSettlementError("shadow_settlement_row_line_size_invalid")
        if audit.ledger_bytes + len(row_line) > _MAX_LEDGER_BYTES:
            raise ShadowSettlementError("shadow_settlement_ledger_size_capacity_invalid")
        pending = {
            "schema": _PENDING_SCHEMA,
            "transaction_id": transaction_id,
            "row_number": row["row_number"],
            "previous_row_sha256": previous,
            "row_sha256": row["row_sha256"],
            "raw_files": [
                {"path": str(raw_paths[index]), "sha256": states[index].raw_sha256}
                for index in range(2)
            ],
        }
        pending_payload = _canonical_json(pending) + b"\n"
        if len(pending_payload) > _MAX_PENDING_BYTES:
            raise ShadowSettlementError("shadow_settlement_pending_size_invalid")
        commit_payload = _commit_bytes(next_row_number, row["row_sha256"])  # type: ignore[arg-type]
        if len(commit_payload) > _MAX_COMMIT_BYTES:
            raise ShadowSettlementError("shadow_settlement_commit_size_invalid")
        prospective_audit_bytes = (
            audit.audit_bytes
            + 2 * sum(len(state.raw_body) for state in states)
            + len(row_line)
            + len(pending_payload)
            + len(epoch_payload)
            + len(commit_payload)
        )
        if prospective_audit_bytes > _MAX_AUDIT_BYTES:
            raise ShadowSettlementError(
                "shadow_settlement_audit_capacity_invalid"
            )
        return _PreparedCommit(
            transaction_id=transaction_id,
            reconciled_wall_ns=wall_ns,
            reconciled_monotonic_ns=monotonic_ns,
            raw_paths=raw_paths,  # type: ignore[arg-type]
            row=row,
            row_line=row_line,
            pending_payload=pending_payload,
            epoch_payload=epoch_payload,
            commit_payload=commit_payload,
        )

    def _commit(
        self,
        audit: _Audit,
        source: AuditedShadowSettlementSource,
        states: Sequence[KalshiFinalMarketState],
        result: ShadowSettlementResult,
        clocks: ShadowSettlementClocks,
        verify_source: Callable[[], None],
        supersedes: str | None,
        prepared_seed: _PreparedCommit | None,
    ) -> ShadowSettlementResult:
        prepared = self._prepare_commit(
            audit,
            source,
            states,
            result,
            clocks,
            supersedes,
            prepared_seed,
        )
        epoch_path = self.root / "settlement.epoch"
        if not epoch_path.exists():
            _publish_new(epoch_path, prepared.epoch_payload, self.root)
        verify_source()
        _publish_new(
            self.root / "settlement.pending", prepared.pending_payload, self.root
        )
        for path, state in zip(prepared.raw_paths, states, strict=True):
            _write_new_file(path, state.raw_body)
        _directory_fsync(self.root / "raw")
        _append_row(self.root / "settlements.jsonl", prepared.row_line)
        verify_source()
        commit_path = self.root / "settlement.commit"
        if commit_path.exists():
            _replace_file(commit_path, prepared.commit_payload, self.root)
        else:
            _publish_new(commit_path, prepared.commit_payload, self.root)
        verify_source()
        _unlink_and_sync(self.root / "settlement.pending", self.root)
        return result


def reconcile_shadow_settlement(
    session_path: Path,
    transport: KalshiShadowSettlementTransport,
    store: ShadowSettlementLabelStore,
    clocks: ShadowSettlementClocks,
) -> ShadowSettlementResult:
    with audit_shadow_settlement_source(session_path) as lease:
        return store._reconcile_with_lease(lease, transport, clocks)


__all__ = (
    "ShadowSettlementClocks",
    "ShadowSettlementError",
    "ShadowSettlementLabelStore",
    "ShadowSettlementResult",
    "default_shadow_settlement_state_root",
    "reconcile_shadow_settlement",
)
