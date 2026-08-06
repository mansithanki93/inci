"""Raw-capture bridge for the live Models 1+2 paper session.

The bridge accepts provider bytes and Kalshi WebSocket bytes only.  It has no
network or order authority and projects a value only after the reviewed wire
parser/reducer has accepted the raw parent.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable, Final

import inci_tennis_adapters.sportradar_trial_v3 as sportradar_trial_v3
from inci_tennis_adapters.kalshi_v2 import (
    KalshiSubscribed,
    UnqualifiedTwoTickerBookReducer,
    parse_unqualified_book_message,
)
from inci_tennis_adapters.live_score_candidates import (
    LiveScoreCaptureContext,
    parse_live_score,
)
from inci_tennis_expert.contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ScoreValue,
    SetScore,
    TennisState,
    TerminationKind,
)
from inci_tennis_expert.fee_schedule import FrozenFeeSchedule
from inci_tennis_expert.live_paper_contracts import (
    LivePaperMarketBinding,
    LivePaperSourceObservation,
)
from inci_tennis_expert.live_paper_execution import project_paper_l2
from inci_tennis_expert.live_paper_score import observation_from_live_score_facts
from inci_tennis_expert.live_paper_session import (
    LivePaperDurableParentReceipt,
    LivePaperCaptureReceiptInput,
    LivePaperHeartbeatInput,
    LivePaperL2Input,
    LivePaperRecord,
    LivePaperRecordKind,
    LivePaperProviderAuthority,
    LivePaperScoreBatchInput,
    LivePaperSessionConfig,
    LivePaperSessionState,
    open_live_paper_session,
    reduce_live_paper_input,
    compute_live_paper_provider_authority_sha256,
    compute_live_paper_parent_receipt_sha256,
)
from inci_tennis_io.shadow_evidence import PersistedKalshiFrame
from inci_tennis_io.sportradar_trial_transport import (
    TrialCapture,
    TrialObservationRecord,
)
from inci_tennis_expert.live_two_model import (
    LiveArtifactAuthority,
    build_operator_bootstrap_artifacts,
)
from inci_tennis_expert.pilot_contracts import ServeStrengthArtifact
from inci_tennis_expert.pilot_dynamic_model import DynamicPointArtifact


__all__ = (
    "LivePaperBridgeError",
    "LivePaperProviderBinding",
    "LivePaperManifest",
    "manifest_from_document",
    "load_live_paper_manifest",
    "live_paper_provider_authorities",
    "GrowingJsonlCaptureBridge",
    "LivePaperCaptureObserver",
)


_MANIFEST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema", "version", "canonical_match_id", "scheduled_start_wall_ns",
        "match_format", "home_player_id", "away_player_id", "providers",
        "markets", "fee_schedule", "fee_series_ticker",
    }
)
_PROVIDER_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "slot", "source_id", "provider_match_id", "home_player_id",
        "away_player_id", "independent_lineage_id", "source_lineage_sha256",
        "independence_proven", "independence_proof_sha256",
    }
)
_MARKET_FIELDS: Final[frozenset[str]] = frozenset(
    {"ticker", "market_id", "yes_player_side"}
)
_FEE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schedule_id", "series_tickers", "taker_rate", "maker_rate",
        "taker_multiplier", "maker_multiplier", "trade_fee_precision",
        "balance_precision", "effective_from_wall_ns", "effective_until_wall_ns",
    }
)
_SCORE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "kind", "provider_slot", "provider_source_id", "provider_match_id",
        "home_player_id", "away_player_id", "independent_lineage_id",
        "source_lineage_sha256", "independence_proven",
        "independence_proof_sha256", "raw_capture_id", "captured_wall_ns",
        "captured_monotonic_ns", "clock_uncertainty_ns", "payload_base64",
    }
)
_KALSHI_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "kind", "physical_connection_generation", "captured_wall_ns",
        "captured_monotonic_ns", "clock_uncertainty_ns", "payload_base64",
    }
)
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_PAYLOAD_BYTES = 1_048_576


class LivePaperBridgeError(ValueError):
    """Fixed-code rejection for manifest or raw capture bridge input."""


def _fail(code: str) -> None:
    raise LivePaperBridgeError(code)


def _exact(value: object, fields: frozenset[str], code: str) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != fields:
        _fail(code)
    return value


def _text(value: object, code: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        _fail(code)
    return value


def _digest(value: object, code: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _fail(code)
    return value


def _positive(value: object, code: str, *, allow_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if allow_zero else 1):
        _fail(code)
    return value


def _decimal(value: object, code: str) -> Decimal:
    if type(value) is not str:
        _fail(code)
    try:
        result = Decimal(value)
    except InvalidOperation:
        _fail(code)
    if not result.is_finite() or format(result, "f") != value:
        _fail(code)
    return result


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            _fail("duplicate_json_key")
        value[key] = item
    return value


def _document(raw: bytes, code: str) -> object:
    try:
        return json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_pairs,
            parse_float=lambda _: _fail("json_number"),
            parse_constant=lambda _: _fail("json_number"),
        )
    except LivePaperBridgeError:
        raise
    except Exception as error:
        raise LivePaperBridgeError(code) from error


@dataclass(frozen=True, slots=True)
class LivePaperProviderBinding:
    slot: str
    source_id: str
    provider_match_id: str
    home_player_id: str
    away_player_id: str
    independent_lineage_id: str
    source_lineage_sha256: str
    independence_proven: bool | None
    independence_proof_sha256: str | None

    def __post_init__(self) -> None:
        for value in (
            self.slot, self.source_id, self.provider_match_id,
            self.home_player_id, self.away_player_id,
            self.independent_lineage_id,
        ):
            _text(value, "provider_binding")
        _digest(self.source_lineage_sha256, "provider_binding")
        if self.independence_proven is not None and type(self.independence_proven) is not bool:
            _fail("provider_binding")
        if self.independence_proof_sha256 is not None:
            _digest(self.independence_proof_sha256, "provider_binding")
        if (self.independence_proven is True) != (self.independence_proof_sha256 is not None):
            _fail("provider_binding")


@dataclass(frozen=True, slots=True)
class LivePaperManifest:
    canonical_match_id: str
    scheduled_start_wall_ns: int
    match_format: MatchFormat
    home_player_id: str
    away_player_id: str
    providers: tuple[LivePaperProviderBinding, ...]
    binding: LivePaperMarketBinding
    fee_schedule: FrozenFeeSchedule
    fee_series_ticker: str
    manifest_sha256: str

    def provider(self, slot: str, source_id: str) -> LivePaperProviderBinding:
        selected = tuple(
            row for row in self.providers
            if row.slot == slot and row.source_id == source_id
        )
        if len(selected) != 1:
            _fail("provider_not_in_manifest")
        return selected[0]


def live_paper_provider_authorities(
    manifest: LivePaperManifest,
) -> tuple[LivePaperProviderAuthority, ...]:
    if type(manifest) is not LivePaperManifest:
        _fail("manifest_authority")
    return tuple(
        sorted(
            (
                LivePaperProviderAuthority(
                    row.slot,
                    row.source_id,
                    row.provider_match_id,
                    row.home_player_id,
                    row.away_player_id,
                    row.independent_lineage_id,
                    row.source_lineage_sha256,
                    row.independence_proven,
                    row.independence_proof_sha256,
                )
                for row in manifest.providers
            ),
            key=lambda row: (
                row.slot,
                row.source_id,
                row.provider_match_id,
                row.home_player_id,
                row.away_player_id,
                row.independent_lineage_id,
                row.source_lineage_sha256,
                (
                    "true"
                    if row.independence_proven is True
                    else "false"
                    if row.independence_proven is False
                    else "none"
                ),
                row.independence_proof_sha256 or "",
            ),
        )
    )


def _market(document: object, side: PlayerSide) -> tuple[str, str]:
    row = _exact(document, _MARKET_FIELDS, "manifest_market")
    expected = "HOME" if side is PlayerSide.HOME else "AWAY"
    if row["yes_player_side"] != expected:
        _fail("manifest_orientation")
    return _text(row["ticker"], "manifest_ticker"), _digest_uuid(row["market_id"])


def _digest_uuid(value: object) -> str:
    if type(value) is not str or re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        value,
    ) is None:
        _fail("manifest_market_id")
    return value


def manifest_from_document(document: object, *, raw_sha256: str | None = None) -> LivePaperManifest:
    root = _exact(document, _MANIFEST_FIELDS, "manifest_fields")
    if root["schema"] != "inci.live-paper-match-manifest" or root["version"] != 1:
        _fail("manifest_schema")
    canonical_match_id = _text(root["canonical_match_id"], "canonical_match_id")
    scheduled_start = _positive(root["scheduled_start_wall_ns"], "scheduled_start_wall_ns")
    try:
        match_format = MatchFormat[root["match_format"]]  # type: ignore[index]
    except (KeyError, TypeError):
        _fail("match_format")
    if match_format is not MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS:
        _fail("match_format")
    home_player_id = _text(root["home_player_id"], "home_player_id")
    away_player_id = _text(root["away_player_id"], "away_player_id")
    if home_player_id == away_player_id:
        _fail("player_orientation")
    raw_providers = root["providers"]
    if type(raw_providers) is not list or not raw_providers or len(raw_providers) > 8:
        _fail("providers")
    providers: list[LivePaperProviderBinding] = []
    for value in raw_providers:
        row = _exact(value, _PROVIDER_FIELDS, "provider_fields")
        providers.append(
            LivePaperProviderBinding(
                _text(row["slot"], "provider_slot"),
                _text(row["source_id"], "provider_source_id"),
                _text(row["provider_match_id"], "provider_match_id"),
                _text(row["home_player_id"], "provider_home_player_id"),
                _text(row["away_player_id"], "provider_away_player_id"),
                _text(row["independent_lineage_id"], "independent_lineage_id"),
                _digest(row["source_lineage_sha256"], "source_lineage_sha256"),
                row["independence_proven"],  # type: ignore[arg-type]
                row["independence_proof_sha256"],  # type: ignore[arg-type]
            )
        )
    provider_keys = {(row.slot, row.source_id) for row in providers}
    if len(provider_keys) != len(providers):
        _fail("provider_duplicate")
    markets = _exact(root["markets"], frozenset({"home", "away"}), "manifest_markets")
    home_ticker, home_market = _market(markets["home"], PlayerSide.HOME)
    away_ticker, away_market = _market(markets["away"], PlayerSide.AWAY)
    binding = LivePaperMarketBinding(
        canonical_match_id=canonical_match_id,
        scheduled_start_wall_ns=scheduled_start,
        home_player_id=home_player_id,
        away_player_id=away_player_id,
        home_ticker=home_ticker,
        home_market_id=home_market,
        home_yes_player_side=PlayerSide.HOME,
        away_ticker=away_ticker,
        away_market_id=away_market,
        away_yes_player_side=PlayerSide.AWAY,
    )
    fee = _exact(root["fee_schedule"], _FEE_FIELDS, "fee_schedule")
    series = fee["series_tickers"]
    if type(series) is not list or not series or any(type(item) is not str for item in series):
        _fail("fee_schedule")
    schedule = FrozenFeeSchedule(
        schedule_id=_text(fee["schedule_id"], "fee_schedule"),
        series_tickers=tuple(series),
        taker_rate=_decimal(fee["taker_rate"], "fee_schedule"),
        maker_rate=_decimal(fee["maker_rate"], "fee_schedule"),
        taker_multiplier=_decimal(fee["taker_multiplier"], "fee_schedule"),
        maker_multiplier=_decimal(fee["maker_multiplier"], "fee_schedule"),
        trade_fee_precision=_decimal(fee["trade_fee_precision"], "fee_schedule"),
        balance_precision=_decimal(fee["balance_precision"], "fee_schedule"),
        effective_from_wall_ns=_positive(fee["effective_from_wall_ns"], "fee_schedule"),
        effective_until_wall_ns=fee["effective_until_wall_ns"],  # type: ignore[arg-type]
    )
    fee_ticker = _text(root["fee_series_ticker"], "fee_series_ticker")
    if fee_ticker not in schedule.series_tickers:
        _fail("fee_series_ticker")
    digest = raw_sha256 or sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    return LivePaperManifest(
        canonical_match_id, scheduled_start, match_format, home_player_id,
        away_player_id, tuple(providers), binding, schedule, fee_ticker, digest,
    )


def load_live_paper_manifest(path: Path) -> LivePaperManifest:
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            _fail("manifest_path")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            after = os.fstat(descriptor)
            if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                _fail("manifest_path")
            raw = os.read(descriptor, _MAX_MANIFEST_BYTES + 1)
        finally:
            os.close(descriptor)
    except LivePaperBridgeError:
        raise
    except OSError as error:
        raise LivePaperBridgeError("manifest_path") from error
    if not raw or len(raw) > _MAX_MANIFEST_BYTES:
        _fail("manifest_size")
    return manifest_from_document(_document(raw, "manifest_json"), raw_sha256=sha256(raw).hexdigest())


def _payload(envelope: dict[str, object]) -> bytes:
    encoded = envelope["payload_base64"]
    if type(encoded) is not str or len(encoded) > 2 * _MAX_PAYLOAD_BYTES:
        _fail("payload_base64")
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeError, ValueError, binascii.Error) as error:
        raise LivePaperBridgeError("payload_base64") from error
    if not raw or len(raw) > _MAX_PAYLOAD_BYTES:
        _fail("payload_size")
    return raw


def _parent_receipt(
    *,
    source_kind: str,
    capture_id: str,
    raw_reference: str,
    raw_sha256: str,
    durable_receipt_sha256: str,
    captured_wall_ns: int,
    captured_monotonic_ns: int,
    clock_uncertainty_ns: int,
    physical_connection_generation: int | None,
) -> LivePaperDurableParentReceipt:
    digest = compute_live_paper_parent_receipt_sha256(
        source_kind=source_kind,
        capture_id=capture_id,
        raw_reference=raw_reference,
        raw_sha256=raw_sha256,
        durable_receipt_sha256=durable_receipt_sha256,
        captured_wall_ns=captured_wall_ns,
        captured_monotonic_ns=captured_monotonic_ns,
        clock_uncertainty_ns=clock_uncertainty_ns,
        physical_connection_generation=physical_connection_generation,
    )
    return LivePaperDurableParentReceipt(
        source_kind,
        capture_id,
        raw_reference,
        raw_sha256,
        durable_receipt_sha256,
        digest,
        captured_wall_ns,
        captured_monotonic_ns,
        clock_uncertainty_ns,
        physical_connection_generation,
    )


def _durable_contract_sha256(kind: str, projection: object) -> str:
    return sha256(
        b"INCI-LIVE-PAPER-COLLECTOR-RECEIPT-V1\0"
        + kind.encode("ascii")
        + b"\0"
        + json.dumps(
            projection,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()


def _trial_observation_sha256(value: TrialObservationRecord) -> str:
    reservation = value.reservation
    return _durable_contract_sha256(
        "sportradar_trial_observation",
        {
            "command": value.command,
            "reservation": {
                "session_id": reservation.session_id,
                "session_attempt": reservation.session_attempt,
                "access_attempt": reservation.access_attempt,
                "route": reservation.route,
                "started_wall_ns": reservation.started_wall_ns,
            },
            "provider_match_id": value.provider_match_id,
            "generated_wall_ns": value.generated_wall_ns,
            "captured_wall_ns": value.captured_wall_ns,
            "status": value.status,
            "match_status": value.match_status,
            "payload_sha256": value.payload_sha256,
            "raw_path": str(value.raw_path),
            "progression": value.progression,
            "last_event_id": value.last_event_id,
            "terminal_reason": value.terminal_reason,
        },
    )


def _kalshi_receipt_sha256(value: PersistedKalshiFrame) -> str:
    return _durable_contract_sha256(
        "shadow_kalshi_capture",
        {
            "raw_path": value.raw_path,
            "raw_sha256": value.raw_sha256,
            "captured_wall_ns": value.captured_wall_ns,
            "captured_monotonic_ns": value.captured_monotonic_ns,
            "clock_uncertainty_ns": value.clock_uncertainty_ns,
            "physical_connection_generation": (
                value.physical_connection_generation
            ),
        },
    )


def _sportradar_completed_set(value: object) -> SetScore:
    if type(value) is not dict:
        _fail("sportradar_terminal_set_invalid")
    home = value.get("home_score")
    away = value.get("away_score")
    home_tiebreak = value.get("home_tiebreak_score")
    away_tiebreak = value.get("away_tiebreak_score")
    if (
        type(home) is not int
        or type(away) is not int
        or not 0 <= home <= 7
        or not 0 <= away <= 7
    ):
        _fail("sportradar_terminal_set_invalid")
    is_tiebreak_set = (home, away) in {(7, 6), (6, 7)}
    if is_tiebreak_set:
        tiebreak_winner = max(home_tiebreak, away_tiebreak) if (
            type(home_tiebreak) is int
            and type(away_tiebreak) is int
        ) else -1
        tiebreak_loser = min(home_tiebreak, away_tiebreak) if (
            type(home_tiebreak) is int
            and type(away_tiebreak) is int
        ) else -1
        legal_tiebreak = (
            tiebreak_winner == 7 and 0 <= tiebreak_loser <= 5
        ) or (
            tiebreak_winner > 7
            and tiebreak_loser == tiebreak_winner - 2
        )
        if (
            type(home_tiebreak) is not int
            or type(away_tiebreak) is not int
            or min(home_tiebreak, away_tiebreak) < 0
            or not legal_tiebreak
            or (home > away) != (home_tiebreak > away_tiebreak)
        ):
            _fail("sportradar_terminal_set_invalid")
    elif home_tiebreak is not None or away_tiebreak is not None:
        _fail("sportradar_terminal_set_invalid")
    return SetScore(home, away, home_tiebreak, away_tiebreak)


class GrowingJsonlCaptureBridge:
    """Stateful raw-capture adapter over the pure Task 4 session reducer."""

    def __init__(self, manifest: LivePaperManifest, state: LivePaperSessionState) -> None:
        if type(manifest) is not LivePaperManifest or type(state) is not LivePaperSessionState:
            _fail("bridge_configuration")
        if state.config.canonical_match_id != manifest.canonical_match_id:
            _fail("bridge_binding")
        self.manifest = manifest
        self.state = state
        self.records: list[LivePaperRecord] = []
        self._latest_scores: dict[tuple[str, str], object] = {}
        self._revisions: dict[tuple[str, str], int] = {}
        self._capture_ids: set[str] = set()
        self._capture_digests: set[str] = set()
        self._book = UnqualifiedTwoTickerBookReducer(
            (manifest.binding.home_ticker, manifest.binding.away_ticker)
        )
        self._generation: int | None = None
        self.last_book_monotonic_ns: int | None = None
        self._rehydrating = False
        self._rehydrated_input: object | None = None

    @classmethod
    def bootstrap(
        cls,
        manifest: LivePaperManifest,
        *,
        home_serve_probability: Decimal,
        away_serve_probability: Decimal,
        opened_wall_ns: int,
        opened_monotonic_ns: int,
    ) -> "GrowingJsonlCaptureBridge":
        static, dynamic = build_operator_bootstrap_artifacts(
            canonical_match_id=manifest.canonical_match_id,
            scheduled_start_wall_ns=manifest.scheduled_start_wall_ns,
            cutoff_wall_ns=manifest.scheduled_start_wall_ns - 1,
            home_serve_point_probability=home_serve_probability,
            away_serve_point_probability=away_serve_probability,
        )
        return cls.from_artifacts(
            manifest,
            static_artifact=static,
            dynamic_artifact=dynamic,
            artifact_authority=LiveArtifactAuthority.OPERATOR_BOOTSTRAP,
            opened_wall_ns=opened_wall_ns,
            opened_monotonic_ns=opened_monotonic_ns,
        )

    @classmethod
    def from_artifacts(
        cls,
        manifest: LivePaperManifest,
        *,
        static_artifact: ServeStrengthArtifact,
        dynamic_artifact: DynamicPointArtifact,
        artifact_authority: LiveArtifactAuthority,
        opened_wall_ns: int,
        opened_monotonic_ns: int,
    ) -> "GrowingJsonlCaptureBridge":
        provider_authorities = live_paper_provider_authorities(manifest)
        config = LivePaperSessionConfig(
            canonical_match_id=manifest.canonical_match_id,
            manifest_sha256=manifest.manifest_sha256,
            provider_authorities=provider_authorities,
            provider_authority_sha256=(
                compute_live_paper_provider_authority_sha256(
                    provider_authorities
                )
            ),
            static_artifact=static_artifact,
            dynamic_artifact=dynamic_artifact,
            artifact_authority=artifact_authority,
            market_binding=manifest.binding,
            fee_schedule=manifest.fee_schedule,
            fee_series_ticker=manifest.fee_series_ticker,
            opened_wall_ns=opened_wall_ns,
            opened_monotonic_ns=opened_monotonic_ns,
        )
        return cls(manifest, open_live_paper_session(config))

    def restore_records(
        self,
        records: tuple[LivePaperRecord, ...],
        *,
        reconstruct_live_adapter: bool = True,
    ) -> None:
        self.records = list(records)
        if not reconstruct_live_adapter:
            return
        for record in records:
            body = record.payload.body
            if record.kind is LivePaperRecordKind.RAW_SCORE_RECEIPT:
                for observation in body.observations:
                    key = (observation.provider_slot, observation.source_id)
                    self._latest_scores[key] = observation
                    self._revisions[key] = max(
                        self._revisions.get(key, 0), observation.state.revision
                    )
                parents = body.durable_parent_receipts
            elif record.kind is LivePaperRecordKind.RAW_L2_RECEIPT:
                self.last_book_monotonic_ns = body.frame.captured_monotonic_ns
                parents = (
                    ()
                    if body.durable_parent_receipt is None
                    else (body.durable_parent_receipt,)
                )
            elif record.kind is LivePaperRecordKind.RAW_CAPTURE_RECEIPT:
                parents = (body.durable_parent_receipt,)
            else:
                parents = ()
            for parent in parents:
                self._capture_ids.add(parent.capture_id)
                self._capture_digests.add(parent.raw_sha256)

    def _check_capture_reuse(
        self, capture_id: str, digest: str, *, exact_durable_parent: bool = False
    ) -> None:
        if capture_id in self._capture_ids or (
            exact_durable_parent and digest in self._capture_digests
        ):
            _fail("capture_reuse")

    def _commit_capture(self, capture_id: str, digest: str) -> None:
        self._capture_ids.add(capture_id)
        self._capture_digests.add(digest)

    def _reduce(self, item: object) -> tuple[LivePaperRecord, ...]:
        if self._rehydrating:
            if self._rehydrated_input is not None:
                _fail("adapter_rehydration_multiple_inputs")
            self._rehydrated_input = item
            return ()
        try:
            state, records = reduce_live_paper_input(self.state, item)  # type: ignore[arg-type]
        except Exception as error:
            raise LivePaperBridgeError("session_reduce") from error
        self.state = state
        self.records.extend(records)
        return records

    def rehydrate_envelope(self, source: str, value: object) -> object | None:
        """Rebuild raw adapter state without refeeding the restored session."""
        if self._rehydrating or source not in {"score", "kalshi"}:
            _fail("adapter_rehydration")
        self._rehydrating = True
        self._rehydrated_input = None
        try:
            if source == "score":
                self.accept_score_envelope(value)
            else:
                self.accept_kalshi_envelope(value)
            return self._rehydrated_input
        finally:
            self._rehydrated_input = None
            self._rehydrating = False

    def accept_score_envelope(self, value: object) -> tuple[LivePaperRecord, ...]:
        row = _exact(value, _SCORE_FIELDS, "score_envelope_fields")
        if row["kind"] != "score_capture":
            _fail("score_envelope_kind")
        slot = _text(row["provider_slot"], "provider_slot")
        source = _text(row["provider_source_id"], "provider_source_id")
        binding = self.manifest.provider(slot, source)
        expected = (
            binding.provider_match_id, binding.home_player_id,
            binding.away_player_id, binding.independent_lineage_id,
            binding.source_lineage_sha256, binding.independence_proven,
            binding.independence_proof_sha256,
        )
        actual = (
            row["provider_match_id"], row["home_player_id"], row["away_player_id"],
            row["independent_lineage_id"], row["source_lineage_sha256"],
            row["independence_proven"], row["independence_proof_sha256"],
        )
        if actual != expected:
            _fail("provider_manifest_mismatch")
        wall = _positive(row["captured_wall_ns"], "capture_clock")
        monotonic = _positive(row["captured_monotonic_ns"], "capture_clock", allow_zero=True)
        uncertainty = _positive(row["clock_uncertainty_ns"], "capture_clock", allow_zero=True)
        capture_id = _text(row["raw_capture_id"], "raw_capture_id")
        raw = _payload(row)
        raw_digest = sha256(raw).hexdigest()
        self._check_capture_reuse(capture_id, raw_digest)
        context = LiveScoreCaptureContext(
            provider_source_id=source,
            revision_domain_id="paper-local-revisions-v1",
            source_lineage_sha256=binding.source_lineage_sha256,
            provider_match_id=binding.provider_match_id,
            home_player_id=binding.home_player_id,
            away_player_id=binding.away_player_id,
            scheduled_start_wall_ns=self.manifest.scheduled_start_wall_ns,
            match_format=self.manifest.match_format,
            local_capture_wall_ns=wall,
            local_capture_monotonic_ns=monotonic,
            local_clock_uncertainty_ns=uncertainty,
            raw_capture_id=capture_id,
            lineage_independence_proven=binding.independence_proven,
        )
        try:
            normalized = parse_live_score(slot, raw, context)
        except Exception as error:
            raise LivePaperBridgeError("score_parse") from error
        if normalized.facts is None:
            _fail("score_facts_unavailable")
        key = (slot, source)
        revision = self._revisions.get(key, 0) + 1
        try:
            observation = observation_from_live_score_facts(
                canonical_match_id=self.manifest.canonical_match_id,
                context=context,
                normalized=normalized,
                local_revision=revision,
                independence_proof_sha256=binding.independence_proof_sha256,
            )
            observation = replace(
                observation,
                independent_lineage_id=binding.independent_lineage_id,
            )
        except Exception as error:
            raise LivePaperBridgeError("score_projection") from error
        previous = self._latest_scores.get(key)
        self._revisions[key] = revision
        self._latest_scores[key] = observation
        try:
            records = self._reduce_latest_scores(wall, monotonic)
        except BaseException:
            if previous is None:
                self._latest_scores.pop(key, None)
                self._revisions.pop(key, None)
            else:
                self._latest_scores[key] = previous
                self._revisions[key] = revision - 1
            raise
        self._commit_capture(capture_id, raw_digest)
        return records

    def _reduce_latest_scores(
        self,
        wall: int,
        monotonic: int,
        durable_parent_receipts: tuple[
            LivePaperDurableParentReceipt, ...
        ] = (),
    ) -> tuple[LivePaperRecord, ...]:
        observations = tuple(
            sorted(
                self._latest_scores.values(),
                key=lambda item: (item.provider_slot, item.source_id),
            )
        )
        return self._reduce(
            LivePaperScoreBatchInput(
                observations,
                wall,
                monotonic,
                durable_parent_receipts,
            )
        )

    def accept_sportradar_capture(
        self,
        raw: bytes,
        *,
        captured_wall_ns: int,
        captured_monotonic_ns: int,
        clock_uncertainty_ns: int,
        durable_parent_receipt: LivePaperDurableParentReceipt | None = None,
    ) -> tuple[LivePaperRecord, ...]:
        """Project one reviewed Sportradar summary/timeline raw capture."""
        providers = tuple(row for row in self.manifest.providers if row.slot == "sportradar")
        if len(providers) != 1:
            _fail("sportradar_manifest_binding")
        binding = providers[0]
        try:
            try:
                score = sportradar_trial_v3.parse_sport_event_timeline(
                    raw, expected_match_id=binding.provider_match_id
                ).score
            except sportradar_trial_v3.SportradarWireContractError:
                score = sportradar_trial_v3.parse_sport_event_summary(
                    raw, expected_match_id=binding.provider_match_id
                )
        except Exception as error:
            raise LivePaperBridgeError("sportradar_parse") from error
        natural_end = (score.status, score.match_status) == (
            "closed",
            "ended",
        )
        active = score.status == "live" and score.match_status in {
            "live",
            "1st_set",
            "2nd_set",
            "3rd_set",
        }
        if (
            score.home_id != binding.home_player_id
            or score.away_id != binding.away_player_id
            or score.start_wall_ns != self.manifest.scheduled_start_wall_ns
            or score.best_of != 3
            or not (active or natural_end)
        ):
            _fail("sportradar_score_incomplete")
        point_values = {
            "0": ScoreValue.LOVE,
            "15": ScoreValue.FIFTEEN,
            "30": ScoreValue.THIRTY,
            "40": ScoreValue.FORTY,
            "A": ScoreValue.ADVANTAGE,
        }
        if natural_end:
            if (
                score.sets_home is None
                or score.sets_away is None
                or score.sets_home == score.sets_away
            ):
                _fail("sportradar_score_incomplete")
            points_home = ScoreValue.LOVE
            points_away = ScoreValue.LOVE
            tiebreak_home = 0
            tiebreak_away = 0
            tiebreak_first_server = None
            server = None
        elif (
            score.games_home is None
            or score.games_away is None
            or score.serving not in {"home", "away"}
        ):
            _fail("sportradar_score_incomplete")
        elif score.in_tiebreak is True:
            if not score.points_home.isascii() or not score.points_home.isdigit() or not score.points_away.isascii() or not score.points_away.isdigit():
                _fail("sportradar_score_incomplete")
            points_home = ScoreValue.LOVE
            points_away = ScoreValue.LOVE
            tiebreak_home = int(score.points_home)
            tiebreak_away = int(score.points_away)
            server = (
                PlayerSide.HOME if score.serving == "home" else PlayerSide.AWAY
            )
            first_is_current = ((tiebreak_home + tiebreak_away + 1) // 2) % 2 == 0
            tiebreak_first_server = (
                server
                if first_is_current
                else PlayerSide.AWAY
                if server is PlayerSide.HOME
                else PlayerSide.HOME
            )
        else:
            if (
                score.in_tiebreak is not False
                or score.points_home not in point_values
                or score.points_away not in point_values
            ):
                _fail("sportradar_score_incomplete")
            points_home = point_values[score.points_home]
            points_away = point_values[score.points_away]
            tiebreak_home = 0
            tiebreak_away = 0
            tiebreak_first_server = None
            server = (
                PlayerSide.HOME if score.serving == "home" else PlayerSide.AWAY
            )
        try:
            document = json.loads(raw.decode("utf-8"))
            period_rows = document["sport_event_status"].get("period_scores", [])
            completed_raw = period_rows if natural_end else period_rows[:-1]
            completed = tuple(
                _sportradar_completed_set(row) for row in completed_raw
            )
        except Exception as error:
            raise LivePaperBridgeError("sportradar_score_projection") from error
        if natural_end:
            wins = [0, 0]
            for index, item in enumerate(completed):
                games = (item.games_home, item.games_away)
                legal = (
                    (games[0] == 6 and 0 <= games[1] <= 4)
                    or (games[1] == 6 and 0 <= games[0] <= 4)
                    or games in {(7, 5), (5, 7)}
                    or (
                        games in {(7, 6), (6, 7)}
                        and item.tiebreak_points_home is not None
                        and item.tiebreak_points_away is not None
                    )
                )
                if not legal:
                    _fail("sportradar_terminal_set_invalid")
                winner_index = 0 if games[0] > games[1] else 1
                wins[winner_index] += 1
                if wins[winner_index] == 2 and index != len(completed) - 1:
                    _fail("sportradar_terminal_set_after_clinch")
            derived_sets = (wins[0], wins[1])
            if (
                derived_sets != (score.sets_home, score.sets_away)
                or max(derived_sets) != 2
                or min(derived_sets) > 1
            ):
                _fail("sportradar_terminal_score_mismatch")
        raw_digest = sha256(raw).hexdigest()
        if durable_parent_receipt is not None:
            if (
                type(durable_parent_receipt)
                is not LivePaperDurableParentReceipt
                or durable_parent_receipt.source_kind
                != "sportradar_trial_observation"
                or durable_parent_receipt.raw_sha256 != raw_digest
                or durable_parent_receipt.captured_wall_ns != captured_wall_ns
                or durable_parent_receipt.captured_monotonic_ns
                != captured_monotonic_ns
                or durable_parent_receipt.clock_uncertainty_ns
                != clock_uncertainty_ns
            ):
                _fail("sportradar_parent_receipt")
            self._check_capture_reuse(
                durable_parent_receipt.capture_id,
                raw_digest,
                exact_durable_parent=True,
            )
        key = (binding.slot, binding.source_id)
        revision = self._revisions.get(key, 0) + 1
        state = TennisState(
            provider_source_id=binding.source_id,
            revision_domain_id="paper-local-revisions-v1",
            source_lineage_sha256=binding.source_lineage_sha256,
            provider_match_id=binding.provider_match_id,
            home_player_id=binding.home_player_id,
            away_player_id=binding.away_player_id,
            scheduled_start_wall_ns=self.manifest.scheduled_start_wall_ns,
            match_format=self.manifest.match_format,
            status=(
                MatchStatus.ENDED
                if natural_end
                else MatchStatus.LIVE
                if score.status == "live"
                else MatchStatus.SUSPENDED
            ),
            termination_kind=(
                TerminationKind.NATURAL if natural_end else TerminationKind.NONE
            ),
            winner=(
                PlayerSide.HOME
                if natural_end and score.sets_home > score.sets_away
                else PlayerSide.AWAY
                if natural_end
                else None
            ),
            retired_side=None,
            completed_sets=completed,
            games_home=0 if natural_end else score.games_home,
            games_away=0 if natural_end else score.games_away,
            points_home=points_home,
            points_away=points_away,
            in_tiebreak=(not natural_end and score.in_tiebreak is True),
            tiebreak_points_home=tiebreak_home,
            tiebreak_points_away=tiebreak_away,
            tiebreak_first_server=tiebreak_first_server,
            server_for_next_point=server,
            correction_epoch=0,
            revision=revision,
            snapshot_complete=True,
            last_provider_event_id="paper-local-sportradar-" + str(revision),
            last_event_semantic_sha256=raw_digest,
            correction_lineage_sha256=sha256(
                b"INCI-LIVE-PAPER-SPORTRADAR-V1\0" + raw_digest.encode("ascii")
            ).hexdigest(),
            last_source_wall_ns=score.generated_wall_ns,
            last_source_generated_wall_ns=score.generated_wall_ns,
            last_received_monotonic_ns=captured_monotonic_ns,
            last_clock_uncertainty_ns=clock_uncertainty_ns,
            block_reason=None,
            expected_revision=None,
            observed_revision=None,
            blocked_event_semantic_sha256=None,
            blocked_received_monotonic_ns=None,
        )
        observation = LivePaperSourceObservation(
            canonical_match_id=self.manifest.canonical_match_id,
            provider_slot=binding.slot,
            source_id=binding.source_id,
            independent_lineage_id=binding.independent_lineage_id,
            lineage_sha256=binding.source_lineage_sha256,
            independence_proven=binding.independence_proven,
            state=state,
            raw_receipt_sha256=raw_digest,
            captured_wall_ns=captured_wall_ns,
            captured_monotonic_ns=captured_monotonic_ns,
            independence_proof_sha256=binding.independence_proof_sha256,
        )
        previous = self._latest_scores.get(key)
        self._revisions[key] = revision
        self._latest_scores[key] = observation
        try:
            records = self._reduce_latest_scores(
                captured_wall_ns,
                captured_monotonic_ns,
                ()
                if durable_parent_receipt is None
                else (durable_parent_receipt,),
            )
        except BaseException:
            if previous is None:
                self._latest_scores.pop(key, None)
                self._revisions.pop(key, None)
            else:
                self._latest_scores[key] = previous
                self._revisions[key] = revision - 1
            raise
        if durable_parent_receipt is not None:
            self._commit_capture(
                durable_parent_receipt.capture_id, raw_digest
            )
        return records

    def accept_kalshi_envelope(
        self,
        value: object,
        *,
        durable_parent_receipt: LivePaperDurableParentReceipt | None = None,
    ) -> tuple[LivePaperRecord, ...]:
        row = _exact(value, _KALSHI_FIELDS, "kalshi_envelope_fields")
        if row["kind"] != "kalshi_frame":
            _fail("kalshi_envelope_kind")
        generation = _positive(row["physical_connection_generation"], "kalshi_generation")
        raw = _payload(row)
        raw_digest = sha256(raw).hexdigest()
        wall = _positive(row["captured_wall_ns"], "capture_clock")
        monotonic = _positive(row["captured_monotonic_ns"], "capture_clock", allow_zero=True)
        uncertainty = _positive(row["clock_uncertainty_ns"], "capture_clock", allow_zero=True)
        if durable_parent_receipt is not None:
            if (
                type(durable_parent_receipt)
                is not LivePaperDurableParentReceipt
                or durable_parent_receipt.source_kind != "shadow_kalshi_capture"
                or durable_parent_receipt.raw_sha256 != raw_digest
                or durable_parent_receipt.captured_wall_ns
                != row["captured_wall_ns"]
                or durable_parent_receipt.captured_monotonic_ns
                != row["captured_monotonic_ns"]
                or durable_parent_receipt.clock_uncertainty_ns
                != row["clock_uncertainty_ns"]
                or durable_parent_receipt.physical_connection_generation
                != generation
            ):
                _fail("kalshi_parent_receipt")
            self._check_capture_reuse(
                durable_parent_receipt.capture_id,
                raw_digest,
                exact_durable_parent=True,
            )
        try:
            parsed = parse_unqualified_book_message(raw)
            if self._generation is None or generation != self._generation:
                if type(parsed) is not KalshiSubscribed or parsed.request_id is None:
                    _fail("kalshi_subscription_first")
                self._book.begin_subscription(generation, parsed.request_id)
                self._generation = generation
            state = self._book.apply(parsed, generation)
        except LivePaperBridgeError:
            raise
        except Exception as error:
            raise LivePaperBridgeError("kalshi_parse") from error
        if state.status == "terminal":
            _fail("kalshi_terminal")
        full_l2 = self._book.full_l2
        if full_l2 is None:
            if durable_parent_receipt is not None:
                records = self._reduce(
                    LivePaperCaptureReceiptInput(
                        durable_parent_receipt,
                        wall,
                        monotonic,
                    )
                )
                self._commit_capture(
                    durable_parent_receipt.capture_id, raw_digest
                )
                return records
            return ()
        try:
            projected = project_paper_l2(
                full_l2,
                binding=self.manifest.binding,
                raw_parent_receipt_sha256=sha256(raw).hexdigest(),
                captured_wall_ns=wall,
                captured_monotonic_ns=monotonic,
                clock_uncertainty_ns=uncertainty,
                home_ticker=self.manifest.binding.home_ticker,
                away_ticker=self.manifest.binding.away_ticker,
            )
        except Exception as error:
            raise LivePaperBridgeError("kalshi_projection") from error
        self.last_book_monotonic_ns = monotonic
        records = self._reduce(
            LivePaperL2Input(
                projected,
                wall,
                monotonic,
                durable_parent_receipt,
            )
        )
        if durable_parent_receipt is not None:
            self._commit_capture(
                durable_parent_receipt.capture_id, raw_digest
            )
        return records


class LivePaperCaptureObserver:
    """Collector observer that forwards only durable raw parents to a bridge."""

    def __init__(
        self,
        bridge: GrowingJsonlCaptureBridge,
        *,
        record_sink: Callable[[tuple[LivePaperRecord, ...]], None] | None = None,
    ) -> None:
        if type(bridge) is not GrowingJsonlCaptureBridge:
            _fail("observer_configuration")
        self.bridge = bridge
        if record_sink is not None and not callable(record_sink):
            _fail("observer_configuration")
        self._record_sink = record_sink
        prior_generations: list[int] = []
        for record in bridge.records:
            body = record.payload.body
            if record.kind is LivePaperRecordKind.RAW_L2_RECEIPT:
                prior_generations.append(
                    body.frame.physical_connection_generation
                )
            elif record.kind is LivePaperRecordKind.RAW_CAPTURE_RECEIPT:
                parent = body.durable_parent_receipt
                if parent.source_kind == "shadow_kalshi_capture":
                    generation = parent.physical_connection_generation
                    if type(generation) is not int:
                        _fail("observer_generation_history")
                    prior_generations.append(generation)
        self._generation_base = max(prior_generations, default=0)

    def _sink(self, records: tuple[LivePaperRecord, ...]) -> None:
        if records and self._record_sink is not None:
            self._record_sink(records)

    async def after_provider_commit(
        self,
        *,
        capture: object,
        durable_receipt: object,
        captured_wall_ns: int,
        captured_monotonic_ns: int,
        clock_uncertainty_ns: int,
    ) -> None:
        providers = tuple(
            row for row in self.bridge.manifest.providers
            if row.slot == "sportradar"
        )
        if (
            type(capture) is not TrialCapture
            or type(durable_receipt) is not TrialObservationRecord
            or len(providers) != 1
            or durable_receipt.command != "shadow"
            or durable_receipt.provider_match_id
            != providers[0].provider_match_id
            or capture.reservation.route not in {"summary", "timeline"}
            or durable_receipt.reservation != capture.reservation
            or durable_receipt.raw_path != capture.raw_path
            or durable_receipt.captured_wall_ns != capture.captured_wall_ns
            or durable_receipt.captured_wall_ns != captured_wall_ns
            or durable_receipt.payload_sha256
            != sha256(capture.payload).hexdigest()
            or not capture.raw_path.is_absolute()
        ):
            _fail("collector_provider_capture")
        raw = capture.payload
        capture_id = "sportradar:" + ":".join(
            (
                capture.reservation.session_id,
                str(capture.reservation.session_attempt),
                str(capture.reservation.access_attempt),
                capture.reservation.route,
            )
        )
        parent = _parent_receipt(
            source_kind="sportradar_trial_observation",
            capture_id=capture_id,
            raw_reference=str(capture.raw_path),
            raw_sha256=durable_receipt.payload_sha256,
            durable_receipt_sha256=_trial_observation_sha256(
                durable_receipt
            ),
            captured_wall_ns=captured_wall_ns,
            captured_monotonic_ns=captured_monotonic_ns,
            clock_uncertainty_ns=clock_uncertainty_ns,
            physical_connection_generation=None,
        )
        self._sink(
            self.bridge.accept_sportradar_capture(
                raw,
                captured_wall_ns=captured_wall_ns,
                captured_monotonic_ns=captured_monotonic_ns,
                clock_uncertainty_ns=clock_uncertainty_ns,
                durable_parent_receipt=parent,
            )
        )

    async def after_kalshi_commit(
        self,
        *,
        frame: object,
        durable_receipt: object,
        captured_wall_ns: int,
        captured_monotonic_ns: int,
        clock_uncertainty_ns: int,
    ) -> None:
        raw = getattr(frame, "payload", None)
        generation = getattr(frame, "physical_connection_generation", None)
        if (
            type(raw) is not bytes
            or type(generation) is not int
            or generation <= 0
            or type(durable_receipt) is not PersistedKalshiFrame
            or durable_receipt.raw_sha256 != sha256(raw).hexdigest()
            or durable_receipt.captured_wall_ns != captured_wall_ns
            or durable_receipt.captured_monotonic_ns
            != captured_monotonic_ns
            or durable_receipt.clock_uncertainty_ns != clock_uncertainty_ns
            or durable_receipt.physical_connection_generation != generation
            or not os.path.isabs(durable_receipt.raw_path)
        ):
            _fail("collector_kalshi_frame")
        paper_generation = generation + self._generation_base
        parent = _parent_receipt(
            source_kind="shadow_kalshi_capture",
            capture_id=durable_receipt.raw_path,
            raw_reference=durable_receipt.raw_path,
            raw_sha256=durable_receipt.raw_sha256,
            durable_receipt_sha256=_kalshi_receipt_sha256(
                durable_receipt
            ),
            captured_wall_ns=captured_wall_ns,
            captured_monotonic_ns=captured_monotonic_ns,
            clock_uncertainty_ns=clock_uncertainty_ns,
            physical_connection_generation=paper_generation,
        )
        records = self.bridge.accept_kalshi_envelope(
            {
                "kind": "kalshi_frame",
                "physical_connection_generation": paper_generation,
                "captured_wall_ns": captured_wall_ns,
                "captured_monotonic_ns": captured_monotonic_ns,
                "clock_uncertainty_ns": clock_uncertainty_ns,
                "payload_base64": base64.b64encode(raw).decode("ascii"),
            },
            durable_parent_receipt=parent,
        )
        self._sink(records)

    async def after_heartbeat_commit(
        self,
        *,
        captured_wall_ns: int,
        captured_monotonic_ns: int,
    ) -> None:
        self._sink(
            self.bridge._reduce(
                LivePaperHeartbeatInput(
                    captured_wall_ns,
                    captured_monotonic_ns,
                )
            )
        )
