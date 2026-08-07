"""Strict read-only adapter for Models 1+2 prematch snapshots.

The producer writes one bounded JSON document with this shape::

    {
      "schema_version": "inci-two-model-prematch-v2",
      "generated_at": "2026-08-06T12:00:00Z",
      "provenance": {
        "producer": "inci-two-model-pilot",
        "model_1_id": "inci-static-bo3-v1",
        "model_2_id": "inci-dynamic-bo3-v1"
      },
      "priors": [{
        "competition_id": "espn:181730",
        "athlete_id": "espn:athlete:1",
        "opponent_athlete_id": "espn:athlete:2",
        "player_name": "Ada Ace",
        "opponent_name": "Bea Break",
        "model_as_of": "2026-08-06T11:59:30Z",
        "match_start": "2026-08-06T12:01:00Z",
        "model_1_match_probability": "0.61",
        "model_2_match_probability": "0.64"
      }]
    }

Probability values are JSON strings intentionally.  Rejecting JSON numbers
prevents a binary-float parse from silently changing a model output before it
reaches the Decimal-based trading path.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable


SCHEMA_VERSION = "inci-two-model-prematch-v2"
MAX_FILE_BYTES = 1024 * 1024
MAX_PRIORS = 4096
_MAX_ID_LENGTH = 256
_MAX_NAME_LENGTH = 256
_MAX_PROVENANCE_LENGTH = 256
_UTC_TIMESTAMP = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?Z$")
_DECIMAL_TEXT = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$")
_ROOT_FIELDS = frozenset({
    "schema_version", "generated_at", "provenance", "priors",
})
_PROVENANCE_FIELDS = frozenset({
    "producer", "model_1_id", "model_2_id",
})
_PRIOR_FIELDS = frozenset({
    "competition_id",
    "athlete_id",
    "opponent_athlete_id",
    "player_name",
    "opponent_name",
    "model_as_of",
    "match_start",
    "model_1_match_probability",
    "model_2_match_probability",
})


class PriorDataError(ValueError):
    """The prior snapshot is missing, stale, malformed, or ambiguous."""


@dataclass(frozen=True, slots=True)
class PriorProvenance:
    schema_version: str
    producer: str
    model_1_id: str
    model_2_id: str
    generated_at: datetime
    source_path: str
    source_sha256: str
    source_mtime_ns: int
    source_size: int


@dataclass(frozen=True, slots=True)
class TwoModelPrior:
    competition_id: str
    athlete_id: str
    opponent_athlete_id: str
    player_name: str
    opponent_name: str
    model_as_of: datetime
    match_start: datetime
    model_1_probability: Decimal
    model_2_probability: Decimal
    provenance: PriorProvenance

    @property
    def probabilities(self) -> tuple[Decimal, Decimal]:
        """Return the pair consumed by the live score updater."""
        return self.model_1_probability, self.model_2_probability


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True, slots=True)
class _Snapshot:
    generated_at: datetime
    provenance: PriorProvenance
    priors: dict[tuple[str, str, str, str, str], TwoModelPrior]


def _file_identity(value: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=value.st_dev,
        inode=value.st_ino,
        mode=value.st_mode,
        size=value.st_size,
        mtime_ns=value.st_mtime_ns,
        ctime_ns=value.st_ctime_ns,
    )


def _duplicate_safe_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PriorDataError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_number(value: str):
    raise PriorDataError(f"JSON numbers are forbidden: {value}")


def _reject_nonfinite(value: str):
    raise PriorDataError(f"non-finite JSON number: {value}")


def _strict_text(value: object, field: str, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise PriorDataError(f"invalid {field}")
    return value


def _probability(value: object, field: str) -> Decimal:
    if (
        type(value) is not str
        or len(value) > 64
        or _DECIMAL_TEXT.fullmatch(value) is None
    ):
        raise PriorDataError(f"invalid {field}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise PriorDataError(f"invalid {field}") from error
    if not parsed.is_finite() or not Decimal(0) < parsed < Decimal(1):
        raise PriorDataError(f"invalid {field}")
    return parsed


def _utc_timestamp(value: object, field: str) -> datetime:
    if type(value) is not str:
        raise PriorDataError(f"invalid {field}")
    match = _UTC_TIMESTAMP.fullmatch(value)
    if match is None:
        raise PriorDataError(f"invalid {field}; UTC Z timestamp required")
    fraction = match.group(2) or ""
    try:
        parsed = datetime.strptime(
            match.group(1), "%Y-%m-%dT%H:%M:%S").replace(
                microsecond=int((fraction[:6]).ljust(6, "0") or "0"),
                tzinfo=timezone.utc,
            )
    except ValueError as error:
        raise PriorDataError(f"invalid {field}") from error
    return parsed


def _elapsed_seconds(later: datetime, earlier: datetime) -> Decimal:
    delta = later - earlier
    return (
        Decimal(delta.days * 86400 + delta.seconds)
        + Decimal(delta.microseconds) / Decimal(1_000_000)
    )


class TwoModelPriorStore:
    """Load and exactly bind local prematch priors, failing closed.

    The parsed document is cached only while the file's device, inode, mode,
    size, nanosecond mtime, and ctime are unchanged.  Freshness is evaluated
    on every lookup, including cache hits.
    """

    def __init__(
            self,
            path: str | os.PathLike[str],
            *,
            max_age_s: int | float | Decimal,
            now: Callable[[], datetime] | None = None,
    ) -> None:
        raw_path = os.fspath(path)
        if type(raw_path) is not str or not raw_path or "\x00" in raw_path:
            raise ValueError("prior path must be a non-empty filesystem path")
        self.path = Path(os.path.abspath(os.path.expanduser(raw_path))).resolve()
        try:
            parsed_max_age = Decimal(str(max_age_s))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("max_age_s must be a positive finite number") from error
        if not parsed_max_age.is_finite() or parsed_max_age <= 0:
            raise ValueError("max_age_s must be a positive finite number")
        if now is not None and not callable(now):
            raise TypeError("now must be callable")
        self.max_age_s = parsed_max_age
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._cached_identity: _FileIdentity | None = None
        self._cached_snapshot: _Snapshot | None = None

    @classmethod
    def from_environment(
            cls,
            variable: str = "INCI_TWO_MODEL_PRIOR_PATH",
            *,
            max_age_s: int | float | Decimal,
            now: Callable[[], datetime] | None = None,
    ) -> "TwoModelPriorStore":
        variable = _strict_text(variable, "environment variable", 128)
        value = os.environ.get(variable)
        if value is None or not value.strip():
            raise PriorDataError(f"missing environment path: {variable}")
        return cls(value, max_age_s=max_age_s, now=now)

    def __call__(self, *, competition_id: str, athlete_id: str,
                 opponent_athlete_id: str, player_name: str,
                 opponent_name: str) -> TwoModelPrior | None:
        return self.lookup(
            competition_id=competition_id,
            athlete_id=athlete_id,
            opponent_athlete_id=opponent_athlete_id,
            player_name=player_name,
            opponent_name=opponent_name,
        )

    def lookup(self, *, competition_id: str, athlete_id: str,
               opponent_athlete_id: str, player_name: str,
               opponent_name: str) -> TwoModelPrior | None:
        competition_id = _strict_text(
            competition_id, "competition_id", _MAX_ID_LENGTH)
        athlete_id = _strict_text(athlete_id, "athlete_id", _MAX_ID_LENGTH)
        opponent_athlete_id = _strict_text(
            opponent_athlete_id, "opponent_athlete_id", _MAX_ID_LENGTH)
        player_name = _strict_text(
            player_name, "player_name", _MAX_NAME_LENGTH)
        opponent_name = _strict_text(
            opponent_name, "opponent_name", _MAX_NAME_LENGTH)
        snapshot = self._load_snapshot()
        self._require_fresh(snapshot.generated_at)
        return snapshot.priors.get((
            competition_id, athlete_id, opponent_athlete_id,
            player_name, opponent_name))

    def _require_fresh(self, generated_at: datetime) -> None:
        observed_now = self._now()
        if (
            not isinstance(observed_now, datetime)
            or observed_now.tzinfo is None
            or observed_now.utcoffset() is None
        ):
            raise PriorDataError("now clock must return a timezone-aware datetime")
        observed_now = observed_now.astimezone(timezone.utc)
        age = _elapsed_seconds(observed_now, generated_at)
        if age < 0:
            raise PriorDataError("prior generated_at is in the future")
        if age > self.max_age_s:
            raise PriorDataError(
                f"stale prior snapshot: age={age}s max={self.max_age_s}s")

    def _path_stat(self) -> tuple[os.stat_result, _FileIdentity]:
        try:
            value = os.stat(self.path, follow_symlinks=False)
        except OSError as error:
            raise PriorDataError(f"prior snapshot unavailable: {self.path}") from error
        identity = _file_identity(value)
        if not stat.S_ISREG(value.st_mode):
            raise PriorDataError("prior snapshot must be a regular file")
        if value.st_size > MAX_FILE_BYTES:
            raise PriorDataError("prior snapshot exceeds file-size bound")
        return value, identity

    def _load_snapshot(self) -> _Snapshot:
        _, path_identity = self._path_stat()
        if (
            path_identity == self._cached_identity
            and self._cached_snapshot is not None
        ):
            return self._cached_snapshot

        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except OSError as error:
            raise PriorDataError("prior snapshot could not be opened safely") from error
        try:
            opened_identity = _file_identity(os.fstat(descriptor))
            if opened_identity != path_identity:
                raise PriorDataError("prior snapshot changed while opening")
            chunks = []
            total = 0
            while True:
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, MAX_FILE_BYTES + 1 - total),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise PriorDataError("prior snapshot exceeds file-size bound")
            closed_identity = _file_identity(os.fstat(descriptor))
            if closed_identity != opened_identity or total != opened_identity.size:
                raise PriorDataError("prior snapshot changed while reading")
        finally:
            os.close(descriptor)

        _, final_identity = self._path_stat()
        if final_identity != opened_identity:
            raise PriorDataError("prior snapshot changed while reading")
        raw = b"".join(chunks)
        snapshot = self._parse(raw, final_identity)
        self._cached_identity = final_identity
        self._cached_snapshot = snapshot
        return snapshot

    def _parse(self, raw: bytes, identity: _FileIdentity) -> _Snapshot:
        try:
            text = raw.decode("utf-8")
            document = json.loads(
                text,
                object_pairs_hook=_duplicate_safe_object,
                parse_float=_reject_json_number,
                parse_int=_reject_json_number,
                parse_constant=_reject_nonfinite,
            )
        except PriorDataError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PriorDataError("invalid JSON prior snapshot") from error

        if type(document) is not dict or frozenset(document) != _ROOT_FIELDS:
            raise PriorDataError("invalid root fields")
        if document["schema_version"] != SCHEMA_VERSION:
            raise PriorDataError("unsupported schema_version")
        generated_at = _utc_timestamp(document["generated_at"], "generated_at")

        raw_provenance = document["provenance"]
        if (
            type(raw_provenance) is not dict
            or frozenset(raw_provenance) != _PROVENANCE_FIELDS
        ):
            raise PriorDataError("invalid provenance fields")
        producer = _strict_text(
            raw_provenance["producer"], "producer", _MAX_PROVENANCE_LENGTH)
        model_1_id = _strict_text(
            raw_provenance["model_1_id"], "model_1_id",
            _MAX_PROVENANCE_LENGTH)
        model_2_id = _strict_text(
            raw_provenance["model_2_id"], "model_2_id",
            _MAX_PROVENANCE_LENGTH)
        if model_1_id == model_2_id:
            raise PriorDataError("Models 1+2 provenance must be independent")
        provenance = PriorProvenance(
            schema_version=SCHEMA_VERSION,
            producer=producer,
            model_1_id=model_1_id,
            model_2_id=model_2_id,
            generated_at=generated_at,
            source_path=str(self.path),
            source_sha256=hashlib.sha256(raw).hexdigest(),
            source_mtime_ns=identity.mtime_ns,
            source_size=identity.size,
        )

        raw_priors = document["priors"]
        if type(raw_priors) is not list or len(raw_priors) > MAX_PRIORS:
            raise PriorDataError("invalid or excessive priors")
        priors: dict[tuple[str, str, str, str, str], TwoModelPrior] = {}
        athlete_keys: dict[tuple[str, str], tuple[str, str, str]] = {}
        player_keys: dict[tuple[str, str], tuple[str, str, str]] = {}
        for raw_prior in raw_priors:
            if (
                type(raw_prior) is not dict
                or frozenset(raw_prior) != _PRIOR_FIELDS
            ):
                raise PriorDataError("invalid prior fields")
            competition_id = _strict_text(
                raw_prior["competition_id"], "competition_id", _MAX_ID_LENGTH)
            athlete_id = _strict_text(
                raw_prior["athlete_id"], "athlete_id", _MAX_ID_LENGTH)
            opponent_athlete_id = _strict_text(
                raw_prior["opponent_athlete_id"], "opponent_athlete_id",
                _MAX_ID_LENGTH)
            player_name = _strict_text(
                raw_prior["player_name"], "player_name", _MAX_NAME_LENGTH)
            opponent_name = _strict_text(
                raw_prior["opponent_name"], "opponent_name", _MAX_NAME_LENGTH)
            if (athlete_id == opponent_athlete_id
                    or player_name == opponent_name):
                raise PriorDataError(
                    "selected and opponent identities must differ")
            source = "lt" if competition_id.startswith("lt:") else "espn"
            if (not competition_id.startswith(source + ":")
                    or not athlete_id.startswith(source + ":athlete:")
                    or not opponent_athlete_id.startswith(
                        source + ":athlete:")):
                raise PriorDataError("provider-qualified prior identity required")
            model_as_of = _utc_timestamp(
                raw_prior["model_as_of"], "model_as_of")
            match_start = _utc_timestamp(
                raw_prior["match_start"], "match_start")
            if model_as_of > generated_at:
                raise PriorDataError(
                    "model_as_of cannot be after snapshot generated_at")
            if generated_at > match_start:
                raise PriorDataError(
                    "prematch cutoff violated: snapshot generated after match start")
            key = (competition_id, athlete_id, opponent_athlete_id,
                   player_name, opponent_name)
            if key in priors:
                raise PriorDataError("duplicate prior identity")
            athlete_key = (competition_id, athlete_id)
            player_key = (competition_id, player_name)
            if (
                athlete_key in athlete_keys
                and athlete_keys[athlete_key] != (
                    opponent_athlete_id, player_name, opponent_name)
            ):
                raise PriorDataError("ambiguous athlete identity")
            if (
                player_key in player_keys
                and player_keys[player_key] != (
                    athlete_id, opponent_athlete_id, opponent_name)
            ):
                raise PriorDataError("ambiguous player identity")
            athlete_keys[athlete_key] = (
                opponent_athlete_id, player_name, opponent_name)
            player_keys[player_key] = (
                athlete_id, opponent_athlete_id, opponent_name)
            priors[key] = TwoModelPrior(
                competition_id=competition_id,
                athlete_id=athlete_id,
                opponent_athlete_id=opponent_athlete_id,
                player_name=player_name,
                opponent_name=opponent_name,
                model_as_of=model_as_of,
                match_start=match_start,
                model_1_probability=_probability(
                    raw_prior["model_1_match_probability"],
                    "model_1_match_probability",
                ),
                model_2_probability=_probability(
                    raw_prior["model_2_match_probability"],
                    "model_2_match_probability",
                ),
                provenance=provenance,
            )
        return _Snapshot(
            generated_at=generated_at,
            provenance=provenance,
            priors=priors,
        )
