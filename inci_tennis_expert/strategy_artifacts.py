from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Final

from .contracts import (
    ArtifactPin,
    ExpertSessionManifestV1,
    MatchFormat,
)
from .fee_schedule import FrozenFeeSchedule


__all__ = (
    "ArtifactPayload",
    "StrategyArtifactError",
    "VerifiedStrategyArtifacts",
    "verify_strategy_artifacts",
)


_MAX_ARTIFACT_BYTES: Final[int] = 4_194_304
_MODEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "artifact_id",
        "artifact_kind",
        "calibrated",
        "calibration_cutoff_wall_ns",
        "frozen",
        "schema_version",
        "supported_match_format",
        "training_cutoff_wall_ns",
    }
)
_FEE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "artifact_id",
        "artifact_kind",
        "balance_precision",
        "effective_from_wall_ns",
        "effective_until_wall_ns",
        "maker_multiplier",
        "maker_rate",
        "schema_version",
        "series_tickers",
        "taker_multiplier",
        "taker_rate",
        "trade_fee_precision",
    }
)


class StrategyArtifactError(ValueError):
    pass


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StrategyArtifactError("artifact_json_duplicate_key")
        result[key] = value
    return result


def _document(payload: ArtifactPayload) -> dict[str, object]:
    try:
        value = json.loads(
            payload.data.decode("ascii"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise StrategyArtifactError("artifact_json") from None
    if type(value) is not dict or _canonical_json(value) != payload.data:
        raise StrategyArtifactError("artifact_canonical_json")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise StrategyArtifactError(name)
    return value


def _integer(value: object, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise StrategyArtifactError(name)
    return value


def _decimal_text(value: object, name: str) -> Decimal:
    if type(value) is not str:
        raise StrategyArtifactError(name)
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise StrategyArtifactError(name) from None
    if not result.is_finite():
        raise StrategyArtifactError(name)
    return result


@dataclass(frozen=True, slots=True, repr=False)
class ArtifactPayload:
    pin: ArtifactPin
    data: bytes

    def __post_init__(self) -> None:
        if type(self) is not ArtifactPayload:
            raise TypeError("artifact_payload")
        if type(self.pin) is not ArtifactPin:
            raise TypeError("pin")
        if (
            type(self.data) is not bytes
            or not self.data
            or len(self.data) > _MAX_ARTIFACT_BYTES
        ):
            raise StrategyArtifactError("artifact_payload")
        if sha256(self.data).hexdigest() != self.pin.artifact_sha256:
            raise StrategyArtifactError("artifact_payload_digest")


class VerifiedStrategyArtifacts:
    """Immutable, self-verifying research artifact bundle.

    This value proves content and session-manifest consistency. It is not an
    execution capability; paper promotion requires a separate trusted runtime
    authority that does not exist in this package slice.
    """

    __slots__ = (
        "_manifest",
        "_outcome",
        "_markout",
        "_fee_payload",
        "_fee_schedule",
    )

    def __init__(
        self,
        *,
        manifest: ExpertSessionManifestV1,
        outcome: ArtifactPayload,
        markout: ArtifactPayload,
        fee_schedule: ArtifactPayload,
    ) -> None:
        schedule = _validate_strategy_artifacts(
            manifest,
            outcome=outcome,
            markout=markout,
            fee_schedule=fee_schedule,
        )
        object.__setattr__(self, "_manifest", manifest)
        object.__setattr__(self, "_outcome", outcome)
        object.__setattr__(self, "_markout", markout)
        object.__setattr__(self, "_fee_payload", fee_schedule)
        object.__setattr__(self, "_fee_schedule", schedule)

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise StrategyArtifactError("authority_immutable")

    @property
    def session_manifest_sha256(self) -> str:
        return self._manifest.manifest_sha256

    @property
    def outcome_pin(self) -> ArtifactPin:
        return self._outcome.pin

    @property
    def markout_pin(self) -> ArtifactPin:
        return self._markout.pin

    @property
    def fee_schedule_pin(self) -> ArtifactPin:
        return self._fee_payload.pin

    @property
    def fee_schedule(self) -> FrozenFeeSchedule:
        return self._fee_schedule

    @property
    def session_artifact_pins(self) -> tuple[ArtifactPin, ...]:
        return self._manifest.artifact_pins

    def validate(self) -> None:
        if type(self) is not VerifiedStrategyArtifacts:
            raise TypeError("authority")
        schedule = _validate_strategy_artifacts(
            self._manifest,
            outcome=self._outcome,
            markout=self._markout,
            fee_schedule=self._fee_payload,
        )
        if schedule != self._fee_schedule:
            raise StrategyArtifactError("fee_schedule_payload_mismatch")

    def models_are_causal_at(self, decision_wall_ns: int) -> bool:
        self.validate()
        decision = _integer(
            decision_wall_ns,
            "decision_wall_ns",
            positive=True,
        )
        cutoffs = (
            _model_cutoffs(
                self._outcome,
                expected_kind="outcome_model",
            ),
            _model_cutoffs(
                self._markout,
                expected_kind="five_minute_markout",
            ),
        )
        return all(
            training < decision and calibration < decision
            for training, calibration in cutoffs
        )


def _verify_model(
    payload: ArtifactPayload,
    *,
    expected_kind: str,
) -> None:
    document = _document(payload)
    if frozenset(document) != _MODEL_KEYS:
        raise StrategyArtifactError("model_artifact_schema")
    if (
        document["schema_version"] != 1
        or document["artifact_id"] != payload.pin.artifact_id
        or document["artifact_kind"] != expected_kind
        or document["frozen"] is not True
        or document["calibrated"] is not True
        or document["supported_match_format"]
        != MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS.value
    ):
        raise StrategyArtifactError("model_artifact_authority")
    training_cutoff = _integer(
        document["training_cutoff_wall_ns"],
        "training_cutoff_wall_ns",
        positive=True,
    )
    calibration_cutoff = _integer(
        document["calibration_cutoff_wall_ns"],
        "calibration_cutoff_wall_ns",
        positive=True,
    )
    if calibration_cutoff < training_cutoff:
        raise StrategyArtifactError("model_artifact_cutoff")


def _model_cutoffs(
    payload: ArtifactPayload,
    *,
    expected_kind: str,
) -> tuple[int, int]:
    _verify_model(payload, expected_kind=expected_kind)
    document = _document(payload)
    return (
        _integer(
            document["training_cutoff_wall_ns"],
            "training_cutoff_wall_ns",
            positive=True,
        ),
        _integer(
            document["calibration_cutoff_wall_ns"],
            "calibration_cutoff_wall_ns",
            positive=True,
        ),
    )


def _fee_schedule(payload: ArtifactPayload) -> FrozenFeeSchedule:
    document = _document(payload)
    if frozenset(document) != _FEE_KEYS:
        raise StrategyArtifactError("fee_artifact_schema")
    if (
        document["schema_version"] != 1
        or document["artifact_id"] != payload.pin.artifact_id
        or document["artifact_kind"] != "fee_schedule"
    ):
        raise StrategyArtifactError("fee_artifact_authority")
    series = document["series_tickers"]
    if (
        type(series) is not list
        or not series
        or any(type(item) is not str for item in series)
    ):
        raise StrategyArtifactError("series_tickers")
    effective_until = document["effective_until_wall_ns"]
    if effective_until is not None:
        effective_until = _integer(
            effective_until,
            "effective_until_wall_ns",
            positive=True,
        )
    try:
        return FrozenFeeSchedule(
            schedule_id=_text(document["artifact_id"], "artifact_id"),
            series_tickers=tuple(series),
            taker_rate=_decimal_text(document["taker_rate"], "taker_rate"),
            maker_rate=_decimal_text(document["maker_rate"], "maker_rate"),
            taker_multiplier=_decimal_text(
                document["taker_multiplier"],
                "taker_multiplier",
            ),
            maker_multiplier=_decimal_text(
                document["maker_multiplier"],
                "maker_multiplier",
            ),
            trade_fee_precision=_decimal_text(
                document["trade_fee_precision"],
                "trade_fee_precision",
            ),
            balance_precision=_decimal_text(
                document["balance_precision"],
                "balance_precision",
            ),
            effective_from_wall_ns=_integer(
                document["effective_from_wall_ns"],
                "effective_from_wall_ns",
                positive=True,
            ),
            effective_until_wall_ns=effective_until,
        )
    except ValueError:
        raise StrategyArtifactError("fee_artifact_values") from None


def _validate_strategy_artifacts(
    manifest: ExpertSessionManifestV1,
    *,
    outcome: ArtifactPayload,
    markout: ArtifactPayload,
    fee_schedule: ArtifactPayload,
) -> FrozenFeeSchedule:
    if type(manifest) is not ExpertSessionManifestV1:
        raise TypeError("manifest")
    ExpertSessionManifestV1.__post_init__(manifest)
    for payload, name in (
        (outcome, "outcome"),
        (markout, "markout"),
        (fee_schedule, "fee_schedule"),
    ):
        if type(payload) is not ArtifactPayload:
            raise TypeError(name)
        ArtifactPayload.__post_init__(payload)
    pins = {pin.artifact_id: pin for pin in manifest.artifact_pins}
    for payload in (outcome, markout, fee_schedule):
        if pins.get(payload.pin.artifact_id) != payload.pin:
            raise StrategyArtifactError("artifact_not_in_session_manifest")
    if len({outcome.pin.artifact_id, markout.pin.artifact_id,
            fee_schedule.pin.artifact_id}) != 3:
        raise StrategyArtifactError("artifact_identity_collision")
    _verify_model(outcome, expected_kind="outcome_model")
    _verify_model(markout, expected_kind="five_minute_markout")
    return _fee_schedule(fee_schedule)


def verify_strategy_artifacts(
    manifest: ExpertSessionManifestV1,
    *,
    outcome: ArtifactPayload,
    markout: ArtifactPayload,
    fee_schedule: ArtifactPayload,
) -> VerifiedStrategyArtifacts:
    return VerifiedStrategyArtifacts(
        manifest=manifest,
        outcome=outcome,
        markout=markout,
        fee_schedule=fee_schedule,
    )
