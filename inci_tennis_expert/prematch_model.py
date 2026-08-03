from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, DecimalException, localcontext
from enum import Enum
from typing import Final

from .contracts import MatchFormat, PlayerSide


_BO3: Final[MatchFormat] = (
    MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS
)
_CALCULATION_CONTEXT: Final[Context] = Context(prec=50)
_PROBABILITY_QUANTUM: Final[Decimal] = Decimal("0.000000000000000001")
_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")
_TEN: Final[Decimal] = Decimal("10")


class PrematchModelError(ValueError):
    pass


class EvidenceFeature(str, Enum):
    ELO = "elo"
    SERVE_RETURN = "serve_return"
    NEWS = "news"
    EXTERNAL = "external"


EVIDENCE_FEATURE_ORDER: Final[tuple[EvidenceFeature, ...]] = (
    EvidenceFeature.ELO,
    EvidenceFeature.SERVE_RETURN,
    EvidenceFeature.NEWS,
    EvidenceFeature.EXTERNAL,
)


def _nonempty_text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(name)
    if not value or value != value.strip():
        raise PrematchModelError(name)
    return value


def _wall_ns(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(name)
    if value < 0:
        raise PrematchModelError(name)
    return value


def _nonnegative_integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(name)
    if value < 0:
        raise PrematchModelError(name)
    return value


def _finite_decimal(value: object, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(name)
    if not value.is_finite():
        raise PrematchModelError(name)
    return value


def _open_probability(value: object, name: str) -> Decimal:
    probability = _finite_decimal(value, name)
    if probability <= _ZERO or probability >= _ONE:
        raise PrematchModelError(name)
    return probability


def _feature_mask(
    value: object,
    name: str,
) -> tuple[bool, bool, bool, bool]:
    if type(value) is not tuple or len(value) != len(EVIDENCE_FEATURE_ORDER):
        raise PrematchModelError(name)
    if any(type(item) is not bool for item in value):
        raise TypeError(name)
    return value  # type: ignore[return-value]


def _standard_bo3(value: object) -> MatchFormat:
    if type(value) is not MatchFormat:
        raise TypeError("match_format")
    if value is not _BO3:
        raise PrematchModelError("match_format")
    return value


def causal_prematch_cutoff(
    scheduled_start_wall_ns: int,
    first_in_play_received_wall_ns: int | None,
) -> int:
    scheduled = _wall_ns(
        scheduled_start_wall_ns,
        "scheduled_start_wall_ns",
    )
    if first_in_play_received_wall_ns is None:
        return scheduled
    first_in_play = _wall_ns(
        first_in_play_received_wall_ns,
        "first_in_play_received_wall_ns",
    )
    return min(scheduled, first_in_play)


@dataclass(frozen=True, slots=True)
class EloParameters:
    version: str
    baseline: Decimal
    k_factor: Decimal
    scale: Decimal

    def __post_init__(self) -> None:
        _nonempty_text(self.version, "version")
        _finite_decimal(self.baseline, "baseline")
        if _finite_decimal(self.k_factor, "k_factor") <= _ZERO:
            raise PrematchModelError("k_factor")
        if _finite_decimal(self.scale, "scale") <= _ZERO:
            raise PrematchModelError("scale")


@dataclass(frozen=True, slots=True)
class HistoricalMatch:
    match_id: str
    home_player_id: str
    away_player_id: str
    surface: str
    winner: PlayerSide
    completed_wall_ns: int
    received_wall_ns: int
    match_format: MatchFormat

    def __post_init__(self) -> None:
        _nonempty_text(self.match_id, "match_id")
        _nonempty_text(self.home_player_id, "home_player_id")
        _nonempty_text(self.away_player_id, "away_player_id")
        if self.home_player_id == self.away_player_id:
            raise PrematchModelError("player_identity")
        _nonempty_text(self.surface, "surface")
        if type(self.winner) is not PlayerSide:
            raise TypeError("winner")
        _wall_ns(self.completed_wall_ns, "completed_wall_ns")
        _wall_ns(self.received_wall_ns, "received_wall_ns")
        _standard_bo3(self.match_format)


@dataclass(frozen=True, slots=True)
class EloSnapshot:
    parameters_version: str
    cutoff_wall_ns: int
    player_home_id: str
    player_away_id: str
    surface: str
    home_overall_rating: Decimal
    away_overall_rating: Decimal
    home_surface_rating: Decimal
    away_surface_rating: Decimal
    home_overall_support: int
    away_overall_support: int
    home_surface_support: int
    away_surface_support: int
    input_match_count: int
    eligible_match_count: int
    excluded_future_received_count: int
    excluded_not_before_cutoff_count: int
    eligible_match_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _nonempty_text(self.parameters_version, "parameters_version")
        _wall_ns(self.cutoff_wall_ns, "cutoff_wall_ns")
        _nonempty_text(self.player_home_id, "player_home_id")
        _nonempty_text(self.player_away_id, "player_away_id")
        if self.player_home_id == self.player_away_id:
            raise PrematchModelError("player_identity")
        _nonempty_text(self.surface, "surface")
        for name in (
            "home_overall_rating",
            "away_overall_rating",
            "home_surface_rating",
            "away_surface_rating",
        ):
            _finite_decimal(getattr(self, name), name)
        for name in (
            "home_overall_support",
            "away_overall_support",
            "home_surface_support",
            "away_surface_support",
            "input_match_count",
            "eligible_match_count",
            "excluded_future_received_count",
            "excluded_not_before_cutoff_count",
        ):
            _nonnegative_integer(getattr(self, name), name)
        if type(self.eligible_match_ids) is not tuple or any(
            type(item) is not str for item in self.eligible_match_ids
        ):
            raise TypeError("eligible_match_ids")
        if len(self.eligible_match_ids) != self.eligible_match_count:
            raise PrematchModelError("eligible_match_ids")
        if self.input_match_count != (
            self.eligible_match_count
            + self.excluded_future_received_count
            + self.excluded_not_before_cutoff_count
        ):
            raise PrematchModelError("match_counts")


def _rating(
    ratings: dict[str, Decimal],
    player_id: str,
    baseline: Decimal,
) -> Decimal:
    return ratings.get(player_id, baseline)


def _expected_score(
    rating: Decimal,
    opponent_rating: Decimal,
    scale: Decimal,
) -> Decimal:
    with localcontext(_CALCULATION_CONTEXT) as context:
        exponent = (opponent_rating - rating) / scale
        return _ONE / (_ONE + context.power(_TEN, exponent))


def _apply_elo_match(
    match: HistoricalMatch,
    ratings: dict[str, Decimal],
    parameters: EloParameters,
) -> None:
    with localcontext(_CALCULATION_CONTEXT):
        home_rating = _rating(
            ratings,
            match.home_player_id,
            parameters.baseline,
        )
        away_rating = _rating(
            ratings,
            match.away_player_id,
            parameters.baseline,
        )
        home_score = _ONE if match.winner is PlayerSide.HOME else _ZERO
        away_score = _ONE - home_score
        home_expected = _expected_score(
            home_rating,
            away_rating,
            parameters.scale,
        )
        away_expected = _ONE - home_expected
        ratings[match.home_player_id] = (
            home_rating + parameters.k_factor * (home_score - home_expected)
        )
        ratings[match.away_player_id] = (
            away_rating + parameters.k_factor * (away_score - away_expected)
        )


def build_elo_snapshot(
    rows: tuple[HistoricalMatch, ...],
    *,
    player_home_id: str,
    player_away_id: str,
    surface: str,
    scheduled_start_wall_ns: int,
    first_in_play_received_wall_ns: int | None,
    match_format: MatchFormat,
    parameters: EloParameters,
) -> EloSnapshot:
    if type(rows) is not tuple:
        raise TypeError("rows")
    if any(type(row) is not HistoricalMatch for row in rows):
        raise TypeError("rows")
    _nonempty_text(player_home_id, "player_home_id")
    _nonempty_text(player_away_id, "player_away_id")
    if player_home_id == player_away_id:
        raise PrematchModelError("player_identity")
    _nonempty_text(surface, "surface")
    _standard_bo3(match_format)
    if type(parameters) is not EloParameters:
        raise TypeError("parameters")
    cutoff = causal_prematch_cutoff(
        scheduled_start_wall_ns,
        first_in_play_received_wall_ns,
    )
    seen_match_ids: set[str] = set()
    eligible: list[HistoricalMatch] = []
    excluded_future_received_count = 0
    excluded_not_before_cutoff_count = 0
    for row in rows:
        if row.received_wall_ns > cutoff:
            excluded_future_received_count += 1
        elif row.completed_wall_ns >= cutoff:
            excluded_not_before_cutoff_count += 1
        else:
            if row.match_id in seen_match_ids:
                raise PrematchModelError("duplicate_match_id")
            seen_match_ids.add(row.match_id)
            eligible.append(row)
    eligible.sort(
        key=lambda row: (
            row.completed_wall_ns,
            row.received_wall_ns,
            row.match_id,
        )
    )

    overall_ratings: dict[str, Decimal] = {}
    surface_ratings: dict[str, Decimal] = {}
    overall_support: dict[str, int] = {}
    surface_support: dict[str, int] = {}
    for row in eligible:
        _apply_elo_match(row, overall_ratings, parameters)
        overall_support[row.home_player_id] = (
            overall_support.get(row.home_player_id, 0) + 1
        )
        overall_support[row.away_player_id] = (
            overall_support.get(row.away_player_id, 0) + 1
        )
        if row.surface == surface:
            _apply_elo_match(row, surface_ratings, parameters)
            surface_support[row.home_player_id] = (
                surface_support.get(row.home_player_id, 0) + 1
            )
            surface_support[row.away_player_id] = (
                surface_support.get(row.away_player_id, 0) + 1
            )

    return EloSnapshot(
        parameters_version=parameters.version,
        cutoff_wall_ns=cutoff,
        player_home_id=player_home_id,
        player_away_id=player_away_id,
        surface=surface,
        home_overall_rating=_rating(
            overall_ratings,
            player_home_id,
            parameters.baseline,
        ),
        away_overall_rating=_rating(
            overall_ratings,
            player_away_id,
            parameters.baseline,
        ),
        home_surface_rating=_rating(
            surface_ratings,
            player_home_id,
            parameters.baseline,
        ),
        away_surface_rating=_rating(
            surface_ratings,
            player_away_id,
            parameters.baseline,
        ),
        home_overall_support=overall_support.get(player_home_id, 0),
        away_overall_support=overall_support.get(player_away_id, 0),
        home_surface_support=surface_support.get(player_home_id, 0),
        away_surface_support=surface_support.get(player_away_id, 0),
        input_match_count=len(rows),
        eligible_match_count=len(eligible),
        excluded_future_received_count=excluded_future_received_count,
        excluded_not_before_cutoff_count=excluded_not_before_cutoff_count,
        eligible_match_ids=tuple(row.match_id for row in eligible),
    )


@dataclass(frozen=True, slots=True)
class PrematchEvidence:
    evidence_id: str
    feature: EvidenceFeature
    probability: Decimal | None
    event_wall_ns: int
    received_wall_ns: int

    def __post_init__(self) -> None:
        _nonempty_text(self.evidence_id, "evidence_id")
        if type(self.feature) is not EvidenceFeature:
            raise TypeError("feature")
        if self.probability is not None:
            _open_probability(self.probability, "probability")
        _wall_ns(self.event_wall_ns, "event_wall_ns")
        _wall_ns(self.received_wall_ns, "received_wall_ns")


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    cutoff_wall_ns: int
    feature_mask: tuple[bool, bool, bool, bool]
    probabilities: tuple[
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
    ]
    selected_evidence_ids: tuple[
        str | None,
        str | None,
        str | None,
        str | None,
    ]
    input_observation_count: int
    eligible_observation_count: int
    excluded_future_received_count: int
    excluded_not_before_cutoff_count: int

    def __post_init__(self) -> None:
        _wall_ns(self.cutoff_wall_ns, "cutoff_wall_ns")
        mask = _feature_mask(self.feature_mask, "feature_mask")
        if type(self.probabilities) is not tuple or len(
            self.probabilities
        ) != len(EVIDENCE_FEATURE_ORDER):
            raise PrematchModelError("probabilities")
        for index, probability in enumerate(self.probabilities):
            if mask[index]:
                _open_probability(probability, "probabilities")
            elif probability is not None:
                raise PrematchModelError("missing_mask")
        if type(self.selected_evidence_ids) is not tuple or len(
            self.selected_evidence_ids
        ) != len(EVIDENCE_FEATURE_ORDER):
            raise PrematchModelError("selected_evidence_ids")
        if any(
            item is not None and type(item) is not str
            for item in self.selected_evidence_ids
        ):
            raise TypeError("selected_evidence_ids")
        for name in (
            "input_observation_count",
            "eligible_observation_count",
            "excluded_future_received_count",
            "excluded_not_before_cutoff_count",
        ):
            _nonnegative_integer(getattr(self, name), name)
        if self.input_observation_count != (
            self.eligible_observation_count
            + self.excluded_future_received_count
            + self.excluded_not_before_cutoff_count
        ):
            raise PrematchModelError("observation_counts")


def build_evidence_snapshot(
    observations: tuple[PrematchEvidence, ...],
    *,
    scheduled_start_wall_ns: int,
    first_in_play_received_wall_ns: int | None,
    match_format: MatchFormat,
) -> EvidenceSnapshot:
    if type(observations) is not tuple:
        raise TypeError("observations")
    if any(type(item) is not PrematchEvidence for item in observations):
        raise TypeError("observations")
    _standard_bo3(match_format)
    cutoff = causal_prematch_cutoff(
        scheduled_start_wall_ns,
        first_in_play_received_wall_ns,
    )
    eligible: list[PrematchEvidence] = []
    excluded_future_received_count = 0
    excluded_not_before_cutoff_count = 0
    seen_evidence_ids: set[str] = set()
    for observation in observations:
        if observation.received_wall_ns > cutoff:
            excluded_future_received_count += 1
        elif observation.event_wall_ns >= cutoff:
            excluded_not_before_cutoff_count += 1
        else:
            if observation.evidence_id in seen_evidence_ids:
                raise PrematchModelError("duplicate_evidence_id")
            seen_evidence_ids.add(observation.evidence_id)
            eligible.append(observation)
    eligible.sort(
        key=lambda item: (
            item.received_wall_ns,
            item.event_wall_ns,
            item.evidence_id,
        )
    )
    selected: dict[EvidenceFeature, PrematchEvidence] = {}
    for observation in eligible:
        selected[observation.feature] = observation
    probabilities = tuple(
        selected[feature].probability if feature in selected else None
        for feature in EVIDENCE_FEATURE_ORDER
    )
    selected_ids = tuple(
        selected[feature].evidence_id if feature in selected else None
        for feature in EVIDENCE_FEATURE_ORDER
    )
    return EvidenceSnapshot(
        cutoff_wall_ns=cutoff,
        feature_mask=tuple(
            probability is not None for probability in probabilities
        ),  # type: ignore[arg-type]
        probabilities=probabilities,  # type: ignore[arg-type]
        selected_evidence_ids=selected_ids,  # type: ignore[arg-type]
        input_observation_count=len(observations),
        eligible_observation_count=len(eligible),
        excluded_future_received_count=excluded_future_received_count,
        excluded_not_before_cutoff_count=excluded_not_before_cutoff_count,
    )


@dataclass(frozen=True, slots=True)
class FrozenLogOddsArtifact:
    version: str
    feature_mask: tuple[bool, bool, bool, bool]
    intercept: Decimal
    coefficients: tuple[Decimal, Decimal, Decimal, Decimal]

    def __post_init__(self) -> None:
        _nonempty_text(self.version, "version")
        _feature_mask(self.feature_mask, "feature_mask")
        _finite_decimal(self.intercept, "intercept")
        if type(self.coefficients) is not tuple or len(
            self.coefficients
        ) != len(EVIDENCE_FEATURE_ORDER):
            raise PrematchModelError("coefficients")
        for coefficient in self.coefficients:
            _finite_decimal(coefficient, "coefficients")


@dataclass(frozen=True, slots=True)
class CombinedProbability:
    artifact_version: str
    feature_mask: tuple[bool, bool, bool, bool]
    log_odds: Decimal
    probability: Decimal

    def __post_init__(self) -> None:
        _nonempty_text(self.artifact_version, "artifact_version")
        _feature_mask(self.feature_mask, "feature_mask")
        _finite_decimal(self.log_odds, "log_odds")
        _open_probability(self.probability, "probability")


def combine_log_odds(
    evidence: EvidenceSnapshot,
    artifact: FrozenLogOddsArtifact,
) -> CombinedProbability:
    if type(evidence) is not EvidenceSnapshot:
        raise TypeError("evidence")
    if type(artifact) is not FrozenLogOddsArtifact:
        raise TypeError("artifact")
    if evidence.feature_mask != artifact.feature_mask:
        raise PrematchModelError("artifact_feature_mask_mismatch")
    try:
        with localcontext(_CALCULATION_CONTEXT):
            log_odds = artifact.intercept
            for index, is_present in enumerate(evidence.feature_mask):
                probability = evidence.probabilities[index]
                if not is_present:
                    if probability is not None:
                        raise PrematchModelError("missing_mask")
                    continue
                probability = _open_probability(
                    probability,
                    "probabilities",
                )
                logit = (probability / (_ONE - probability)).ln()
                log_odds += artifact.coefficients[index] * logit
            probability = _ONE / (_ONE + (-log_odds).exp())
            probability = probability.quantize(_PROBABILITY_QUANTUM)
    except DecimalException as error:
        raise PrematchModelError("log_odds_arithmetic") from error
    _open_probability(probability, "probability")
    return CombinedProbability(
        artifact_version=artifact.version,
        feature_mask=evidence.feature_mask,
        log_odds=log_odds,
        probability=probability,
    )
