from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Final

from .contracts import (
    DecisionReason,
    ExpertContractError,
    PlayerSide,
    _boolean,
    _exact,
    _exact_self,
    _integer,
    _probability,
    _quantity,
    _safe_id,
    _sha256,
    expert_contract_sha256,
)


_DECIMAL_PLACES: Final[Decimal] = Decimal("0.000000000001")
_FEATURE_DEFINITION: Final[dict[str, object]] = {
    "schema": "prematch_feature_definition_v1",
    "eligible_row_rule": (
        "match_start, observed, and revised wall times must be strictly "
        "before scheduled_start_wall_ns"
    ),
    "serve_features": (
        "recency-decayed serve points won and total by player"
    ),
    "return_features": (
        "recency-decayed return points won and total by player"
    ),
    "ranking_rule": (
        "latest ranking with ranking_as_of_wall_ns strictly before "
        "scheduled_start_wall_ns"
    ),
}
PREMATCH_FEATURE_DEFINITION_SHA256: Final[str] = expert_contract_sha256(
    _FEATURE_DEFINITION
)


def _decimal(value: object, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(name)
    if not value.is_finite():
        raise ExpertContractError(name)
    return value


def _optional_integer(value: object, name: str) -> None:
    if value is not None:
        _integer(value, name, positive=True)


def _optional_side(value: object, name: str) -> None:
    if value is not None:
        _exact(value, PlayerSide, name)


def _nonnegative_decimal(value: object, name: str) -> Decimal:
    number = _decimal(value, name)
    if number < Decimal("0"):
        raise ExpertContractError(name)
    return number


def _ordered_probability_interval(
    lower: Decimal,
    center: Decimal,
    upper: Decimal,
    name: str,
) -> None:
    _probability(lower, f"{name}_lower")
    _probability(center, f"{name}_center")
    _probability(upper, f"{name}_upper")
    if lower > center or center > upper:
        raise ExpertContractError(name)


def _quantize(value: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return value.quantize(_DECIMAL_PLACES)


def _clamp_probability(value: Decimal) -> Decimal:
    if value < Decimal("0"):
        return Decimal("0")
    if value > Decimal("1"):
        return Decimal("1")
    return _quantize(value)


def _opposite(side: PlayerSide) -> PlayerSide:
    if side is PlayerSide.HOME:
        return PlayerSide.AWAY
    return PlayerSide.HOME


@dataclass(frozen=True, slots=True)
class HistoricalRow:
    provider_match_id: str
    home_player_id: str
    away_player_id: str
    surface: str
    match_start_wall_ns: int
    observed_wall_ns: int
    revised_wall_ns: int
    source_lineage_sha256: str
    row_sha256: str
    winner_side: PlayerSide | None
    home_serve_points_won: int
    home_serve_points_total: int
    away_serve_points_won: int
    away_serve_points_total: int
    home_return_points_won: int
    home_return_points_total: int
    away_return_points_won: int
    away_return_points_total: int
    home_ranking: int | None = None
    away_ranking: int | None = None
    ranking_as_of_wall_ns: int | None = None

    def __post_init__(self) -> None:
        _exact_self(self, HistoricalRow)
        _safe_id(self.provider_match_id, "provider_match_id")
        _safe_id(self.home_player_id, "home_player_id")
        _safe_id(self.away_player_id, "away_player_id")
        _safe_id(self.surface, "surface")
        _integer(self.match_start_wall_ns, "match_start_wall_ns")
        _integer(self.observed_wall_ns, "observed_wall_ns")
        _integer(self.revised_wall_ns, "revised_wall_ns")
        _sha256(self.source_lineage_sha256, "source_lineage_sha256")
        _sha256(self.row_sha256, "row_sha256")
        _optional_side(self.winner_side, "winner_side")
        for name in (
            "home_serve_points_won",
            "home_serve_points_total",
            "away_serve_points_won",
            "away_serve_points_total",
            "home_return_points_won",
            "home_return_points_total",
            "away_return_points_won",
            "away_return_points_total",
        ):
            _integer(getattr(self, name), name)
        if self.home_serve_points_won > self.home_serve_points_total:
            raise ExpertContractError("home_serve_points")
        if self.away_serve_points_won > self.away_serve_points_total:
            raise ExpertContractError("away_serve_points")
        if self.home_return_points_won > self.home_return_points_total:
            raise ExpertContractError("home_return_points")
        if self.away_return_points_won > self.away_return_points_total:
            raise ExpertContractError("away_return_points")
        _optional_integer(self.home_ranking, "home_ranking")
        _optional_integer(self.away_ranking, "away_ranking")
        if self.ranking_as_of_wall_ns is not None:
            _integer(self.ranking_as_of_wall_ns, "ranking_as_of_wall_ns")
        if (self.home_ranking is None) != (self.away_ranking is None):
            raise ExpertContractError("ranking")
        if (self.home_ranking is None) != (
            self.ranking_as_of_wall_ns is None
        ):
            raise ExpertContractError("ranking_as_of_wall_ns")


@dataclass(frozen=True, slots=True)
class PrematchFeatures:
    player_home_id: str
    player_away_id: str
    surface: str
    scheduled_start_wall_ns: int
    eligible_row_count: int
    discarded_row_count: int
    max_source_wall_ns: int
    home_serve_points_won: Decimal
    home_serve_points_total: Decimal
    away_serve_points_won: Decimal
    away_serve_points_total: Decimal
    home_return_points_won: Decimal
    home_return_points_total: Decimal
    away_return_points_won: Decimal
    away_return_points_total: Decimal
    home_ranking: int | None
    away_ranking: int | None
    source_rows_sha256: str
    feature_definition_sha256: str
    feature_vector_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, PrematchFeatures)
        _safe_id(self.player_home_id, "player_home_id")
        _safe_id(self.player_away_id, "player_away_id")
        _safe_id(self.surface, "surface")
        _integer(self.scheduled_start_wall_ns, "scheduled_start_wall_ns")
        _integer(self.eligible_row_count, "eligible_row_count")
        _integer(self.discarded_row_count, "discarded_row_count")
        _integer(self.max_source_wall_ns, "max_source_wall_ns")
        for name in (
            "home_serve_points_won",
            "home_serve_points_total",
            "away_serve_points_won",
            "away_serve_points_total",
            "home_return_points_won",
            "home_return_points_total",
            "away_return_points_won",
            "away_return_points_total",
        ):
            _nonnegative_decimal(getattr(self, name), name)
        if self.home_serve_points_won > self.home_serve_points_total:
            raise ExpertContractError("home_serve_points")
        if self.away_serve_points_won > self.away_serve_points_total:
            raise ExpertContractError("away_serve_points")
        if self.home_return_points_won > self.home_return_points_total:
            raise ExpertContractError("home_return_points")
        if self.away_return_points_won > self.away_return_points_total:
            raise ExpertContractError("away_return_points")
        _optional_integer(self.home_ranking, "home_ranking")
        _optional_integer(self.away_ranking, "away_ranking")
        _sha256(self.source_rows_sha256, "source_rows_sha256")
        _sha256(
            self.feature_definition_sha256,
            "feature_definition_sha256",
        )
        _sha256(self.feature_vector_sha256, "feature_vector_sha256")


@dataclass(frozen=True, slots=True)
class FrozenPrematchArtifact:
    artifact_id: str
    model_sha256: str
    feature_definition_sha256: str
    training_cutoff_wall_ns: int
    source_dataset_sha256: str
    entitlement_sha256: str
    manifest_sha256: str
    access_decision_sha256: str
    tour_serve_alpha: Decimal
    tour_serve_beta: Decimal
    surface_serve_alpha: Decimal
    surface_serve_beta: Decimal
    return_alpha: Decimal
    return_beta: Decimal
    recency_half_life_ns: int
    opponent_adjustment_weight: Decimal
    minimum_effective_sample_size: Decimal
    model_build_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, FrozenPrematchArtifact)
        _safe_id(self.artifact_id, "artifact_id")
        _sha256(self.model_sha256, "model_sha256")
        _sha256(
            self.feature_definition_sha256,
            "feature_definition_sha256",
        )
        _integer(self.training_cutoff_wall_ns, "training_cutoff_wall_ns")
        _sha256(self.source_dataset_sha256, "source_dataset_sha256")
        _sha256(self.entitlement_sha256, "entitlement_sha256")
        _sha256(self.manifest_sha256, "manifest_sha256")
        _sha256(self.access_decision_sha256, "access_decision_sha256")
        for name in (
            "tour_serve_alpha",
            "tour_serve_beta",
            "surface_serve_alpha",
            "surface_serve_beta",
            "return_alpha",
            "return_beta",
            "minimum_effective_sample_size",
        ):
            _quantity(getattr(self, name), name, positive=True)
        _integer(
            self.recency_half_life_ns,
            "recency_half_life_ns",
            positive=True,
        )
        _quantity(
            self.opponent_adjustment_weight,
            "opponent_adjustment_weight",
        )
        if self.opponent_adjustment_weight > Decimal("1"):
            raise ExpertContractError("opponent_adjustment_weight")
        _sha256(self.model_build_sha256, "model_build_sha256")


@dataclass(frozen=True, slots=True)
class PrematchPrior:
    player_home_id: str
    player_away_id: str
    surface: str
    scheduled_start_wall_ns: int
    home_serve_point_probability: Decimal
    home_serve_point_lower: Decimal
    home_serve_point_upper: Decimal
    away_serve_point_probability: Decimal
    away_serve_point_lower: Decimal
    away_serve_point_upper: Decimal
    home_effective_sample_size: Decimal
    away_effective_sample_size: Decimal
    supported: bool
    support_status: str
    training_cutoff_wall_ns: int
    model_sha256: str
    prematch_artifact_sha256: str
    feature_definition_sha256: str
    feature_vector_sha256: str
    abstention_reason: DecisionReason | None = None

    def __post_init__(self) -> None:
        _exact_self(self, PrematchPrior)
        _safe_id(self.player_home_id, "player_home_id")
        _safe_id(self.player_away_id, "player_away_id")
        _safe_id(self.surface, "surface")
        _integer(self.scheduled_start_wall_ns, "scheduled_start_wall_ns")
        _ordered_probability_interval(
            self.home_serve_point_lower,
            self.home_serve_point_probability,
            self.home_serve_point_upper,
            "home_serve_point_probability",
        )
        _ordered_probability_interval(
            self.away_serve_point_lower,
            self.away_serve_point_probability,
            self.away_serve_point_upper,
            "away_serve_point_probability",
        )
        _quantity(self.home_effective_sample_size, "home_effective_sample_size")
        _quantity(self.away_effective_sample_size, "away_effective_sample_size")
        _boolean(self.supported, "supported")
        _safe_id(self.support_status, "support_status")
        _integer(self.training_cutoff_wall_ns, "training_cutoff_wall_ns")
        _sha256(self.model_sha256, "model_sha256")
        _sha256(
            self.prematch_artifact_sha256,
            "prematch_artifact_sha256",
        )
        _sha256(
            self.feature_definition_sha256,
            "feature_definition_sha256",
        )
        _sha256(self.feature_vector_sha256, "feature_vector_sha256")
        if self.abstention_reason is not None:
            _exact(self.abstention_reason, DecisionReason, "abstention_reason")
        if self.supported and self.abstention_reason is not None:
            raise ExpertContractError("abstention_reason")
        if not self.supported and self.abstention_reason is None:
            raise ExpertContractError("abstention_reason")


def _row_is_point_in_time(
    row: HistoricalRow,
    scheduled_start_wall_ns: int,
) -> bool:
    return (
        row.match_start_wall_ns < scheduled_start_wall_ns
        and row.observed_wall_ns < scheduled_start_wall_ns
        and row.revised_wall_ns < scheduled_start_wall_ns
    )


def _recency_weight(
    row: HistoricalRow,
    *,
    scheduled_start_wall_ns: int,
) -> Decimal:
    age = scheduled_start_wall_ns - row.match_start_wall_ns
    if age <= 0:
        return Decimal("0")
    periods = age // 86_400_000_000_000
    if periods > 24:
        periods = 24
    return Decimal("1") / (Decimal("2") ** periods)


def _add_weighted(
    value: Decimal,
    points: int,
    weight: Decimal,
) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return value + Decimal(points) * weight


def _player_side_in_row(
    row: HistoricalRow,
    player_id: str,
) -> PlayerSide | None:
    if row.home_player_id == player_id:
        return PlayerSide.HOME
    if row.away_player_id == player_id:
        return PlayerSide.AWAY
    return None


def _row_serve_points(
    row: HistoricalRow,
    side: PlayerSide,
) -> tuple[int, int]:
    if side is PlayerSide.HOME:
        return row.home_serve_points_won, row.home_serve_points_total
    return row.away_serve_points_won, row.away_serve_points_total


def _row_return_points(
    row: HistoricalRow,
    side: PlayerSide,
) -> tuple[int, int]:
    if side is PlayerSide.HOME:
        return row.home_return_points_won, row.home_return_points_total
    return row.away_return_points_won, row.away_return_points_total


def _row_ranking(row: HistoricalRow, side: PlayerSide) -> int | None:
    if side is PlayerSide.HOME:
        return row.home_ranking
    return row.away_ranking


def _feature_payload(
    *,
    player_home_id: str,
    player_away_id: str,
    surface: str,
    scheduled_start_wall_ns: int,
    eligible_row_count: int,
    discarded_row_count: int,
    max_source_wall_ns: int,
    home_serve_points_won: Decimal,
    home_serve_points_total: Decimal,
    away_serve_points_won: Decimal,
    away_serve_points_total: Decimal,
    home_return_points_won: Decimal,
    home_return_points_total: Decimal,
    away_return_points_won: Decimal,
    away_return_points_total: Decimal,
    home_ranking: int | None,
    away_ranking: int | None,
    source_rows_sha256: str,
) -> dict[str, object]:
    return {
        "player_home_id": player_home_id,
        "player_away_id": player_away_id,
        "surface": surface,
        "scheduled_start_wall_ns": scheduled_start_wall_ns,
        "eligible_row_count": eligible_row_count,
        "discarded_row_count": discarded_row_count,
        "max_source_wall_ns": max_source_wall_ns,
        "home_serve_points_won": home_serve_points_won,
        "home_serve_points_total": home_serve_points_total,
        "away_serve_points_won": away_serve_points_won,
        "away_serve_points_total": away_serve_points_total,
        "home_return_points_won": home_return_points_won,
        "home_return_points_total": home_return_points_total,
        "away_return_points_won": away_return_points_won,
        "away_return_points_total": away_return_points_total,
        "home_ranking": home_ranking,
        "away_ranking": away_ranking,
        "source_rows_sha256": source_rows_sha256,
        "feature_definition_sha256": PREMATCH_FEATURE_DEFINITION_SHA256,
    }


def _prematch_artifact_payload(
    artifact: FrozenPrematchArtifact,
) -> dict[str, object]:
    return {
        "schema": "frozen_prematch_artifact_v1",
        "artifact_id": artifact.artifact_id,
        "model_sha256": artifact.model_sha256,
        "feature_definition_sha256": artifact.feature_definition_sha256,
        "training_cutoff_wall_ns": artifact.training_cutoff_wall_ns,
        "source_dataset_sha256": artifact.source_dataset_sha256,
        "entitlement_sha256": artifact.entitlement_sha256,
        "manifest_sha256": artifact.manifest_sha256,
        "access_decision_sha256": artifact.access_decision_sha256,
        "tour_serve_alpha": artifact.tour_serve_alpha,
        "tour_serve_beta": artifact.tour_serve_beta,
        "surface_serve_alpha": artifact.surface_serve_alpha,
        "surface_serve_beta": artifact.surface_serve_beta,
        "return_alpha": artifact.return_alpha,
        "return_beta": artifact.return_beta,
        "recency_half_life_ns": artifact.recency_half_life_ns,
        "opponent_adjustment_weight": artifact.opponent_adjustment_weight,
        "minimum_effective_sample_size": (
            artifact.minimum_effective_sample_size
        ),
        "model_build_sha256": artifact.model_build_sha256,
    }


def build_features(
    rows: tuple[HistoricalRow, ...],
    *,
    player_home_id: str,
    player_away_id: str,
    surface: str,
    scheduled_start_wall_ns: int,
) -> PrematchFeatures:
    if type(rows) is not tuple:
        raise TypeError("rows")
    for row in rows:
        if type(row) is not HistoricalRow:
            raise TypeError("rows")
    _safe_id(player_home_id, "player_home_id")
    _safe_id(player_away_id, "player_away_id")
    _safe_id(surface, "surface")
    _integer(scheduled_start_wall_ns, "scheduled_start_wall_ns")

    home_serve_won = Decimal("0")
    home_serve_total = Decimal("0")
    away_serve_won = Decimal("0")
    away_serve_total = Decimal("0")
    home_return_won = Decimal("0")
    home_return_total = Decimal("0")
    away_return_won = Decimal("0")
    away_return_total = Decimal("0")
    eligible = 0
    discarded = 0
    max_source = 0
    source_hashes: list[str] = []
    home_rank: int | None = None
    away_rank: int | None = None
    home_rank_as_of = -1
    away_rank_as_of = -1

    for row in rows:
        if not _row_is_point_in_time(row, scheduled_start_wall_ns):
            discarded += 1
            continue
        eligible += 1
        if row.revised_wall_ns > max_source:
            max_source = row.revised_wall_ns
        source_hashes.append(row.row_sha256)
        weight = _recency_weight(row, scheduled_start_wall_ns=scheduled_start_wall_ns)
        for player_id, target_side in (
            (player_home_id, PlayerSide.HOME),
            (player_away_id, PlayerSide.AWAY),
        ):
            row_side = _player_side_in_row(row, player_id)
            if row_side is None:
                continue
            serve_won, serve_total = _row_serve_points(row, row_side)
            return_won, return_total = _row_return_points(row, row_side)
            if target_side is PlayerSide.HOME:
                home_serve_won = _add_weighted(
                    home_serve_won,
                    serve_won,
                    weight,
                )
                home_serve_total = _add_weighted(
                    home_serve_total,
                    serve_total,
                    weight,
                )
                home_return_won = _add_weighted(
                    home_return_won,
                    return_won,
                    weight,
                )
                home_return_total = _add_weighted(
                    home_return_total,
                    return_total,
                    weight,
                )
            else:
                away_serve_won = _add_weighted(
                    away_serve_won,
                    serve_won,
                    weight,
                )
                away_serve_total = _add_weighted(
                    away_serve_total,
                    serve_total,
                    weight,
                )
                away_return_won = _add_weighted(
                    away_return_won,
                    return_won,
                    weight,
                )
                away_return_total = _add_weighted(
                    away_return_total,
                    return_total,
                    weight,
                )
            if (
                row.ranking_as_of_wall_ns is not None
                and row.ranking_as_of_wall_ns < scheduled_start_wall_ns
            ):
                ranking = _row_ranking(row, row_side)
                if ranking is None:
                    continue
                if (
                    target_side is PlayerSide.HOME
                    and row.ranking_as_of_wall_ns > home_rank_as_of
                ):
                    home_rank = ranking
                    home_rank_as_of = row.ranking_as_of_wall_ns
                elif (
                    target_side is PlayerSide.AWAY
                    and row.ranking_as_of_wall_ns > away_rank_as_of
                ):
                    away_rank = ranking
                    away_rank_as_of = row.ranking_as_of_wall_ns

    source_rows_sha256 = expert_contract_sha256(
        {
            "schema": "prematch_source_rows_v1",
            "row_sha256": tuple(sorted(source_hashes)),
        }
    )
    payload = _feature_payload(
        player_home_id=player_home_id,
        player_away_id=player_away_id,
        surface=surface,
        scheduled_start_wall_ns=scheduled_start_wall_ns,
        eligible_row_count=eligible,
        discarded_row_count=discarded,
        max_source_wall_ns=max_source,
        home_serve_points_won=_quantize(home_serve_won),
        home_serve_points_total=_quantize(home_serve_total),
        away_serve_points_won=_quantize(away_serve_won),
        away_serve_points_total=_quantize(away_serve_total),
        home_return_points_won=_quantize(home_return_won),
        home_return_points_total=_quantize(home_return_total),
        away_return_points_won=_quantize(away_return_won),
        away_return_points_total=_quantize(away_return_total),
        home_ranking=home_rank,
        away_ranking=away_rank,
        source_rows_sha256=source_rows_sha256,
    )
    return PrematchFeatures(
        **payload,
        feature_vector_sha256=expert_contract_sha256(
            {"schema": "prematch_features_v1", **payload}
        ),
    )


def _posterior_probability(
    *,
    wins: Decimal,
    total: Decimal,
    alpha: Decimal,
    beta: Decimal,
) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return _clamp_probability((alpha + wins) / (alpha + beta + total))


def _rate_or_prior(
    *,
    wins: Decimal,
    total: Decimal,
    alpha: Decimal,
    beta: Decimal,
) -> Decimal:
    if total == Decimal("0"):
        return _posterior_probability(
            wins=Decimal("0"),
            total=Decimal("0"),
            alpha=alpha,
            beta=beta,
        )
    with localcontext() as context:
        context.prec = 50
        return _clamp_probability(wins / total)


def _serve_estimate(
    *,
    serve_won: Decimal,
    serve_total: Decimal,
    opponent_return_won: Decimal,
    opponent_return_total: Decimal,
    artifact: FrozenPrematchArtifact,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    alpha = artifact.tour_serve_alpha + artifact.surface_serve_alpha
    beta = artifact.tour_serve_beta + artifact.surface_serve_beta
    prior = _posterior_probability(
        wins=Decimal("0"),
        total=Decimal("0"),
        alpha=alpha,
        beta=beta,
    )
    posterior = _posterior_probability(
        wins=serve_won,
        total=serve_total,
        alpha=alpha,
        beta=beta,
    )
    opponent_return = _rate_or_prior(
        wins=opponent_return_won,
        total=opponent_return_total,
        alpha=artifact.return_alpha,
        beta=artifact.return_beta,
    )
    with localcontext() as context:
        context.prec = 50
        adjustment = Decimal("0")
        if opponent_return_total > Decimal("0"):
            adjustment = (
                artifact.opponent_adjustment_weight
                * (Decimal("1") - opponent_return - prior)
            )
        probability = _clamp_probability(posterior + adjustment)
        ess = alpha + beta + serve_total
        width = Decimal("0.50") / (ess.sqrt() if ess > 0 else Decimal("1"))
    lower = _clamp_probability(probability - width)
    upper = _clamp_probability(probability + width)
    return probability, lower, upper, _quantize(ess)


def estimate_prematch(
    features: PrematchFeatures,
    artifact: FrozenPrematchArtifact,
) -> PrematchPrior:
    if type(features) is not PrematchFeatures:
        raise TypeError("features")
    if type(artifact) is not FrozenPrematchArtifact:
        raise TypeError("artifact")
    if features.feature_definition_sha256 != artifact.feature_definition_sha256:
        raise ExpertContractError("feature_definition_sha256")
    if features.max_source_wall_ns > artifact.training_cutoff_wall_ns:
        raise ExpertContractError("training_cutoff_wall_ns")

    home_probability, home_lower, home_upper, home_ess = _serve_estimate(
        serve_won=features.home_serve_points_won,
        serve_total=features.home_serve_points_total,
        opponent_return_won=features.away_return_points_won,
        opponent_return_total=features.away_return_points_total,
        artifact=artifact,
    )
    away_probability, away_lower, away_upper, away_ess = _serve_estimate(
        serve_won=features.away_serve_points_won,
        serve_total=features.away_serve_points_total,
        opponent_return_won=features.home_return_points_won,
        opponent_return_total=features.home_return_points_total,
        artifact=artifact,
    )
    supported = (
        home_ess >= artifact.minimum_effective_sample_size
        and away_ess >= artifact.minimum_effective_sample_size
    )
    support_status = "supported" if supported else "low_ess"
    return PrematchPrior(
        player_home_id=features.player_home_id,
        player_away_id=features.player_away_id,
        surface=features.surface,
        scheduled_start_wall_ns=features.scheduled_start_wall_ns,
        home_serve_point_probability=home_probability,
        home_serve_point_lower=home_lower,
        home_serve_point_upper=home_upper,
        away_serve_point_probability=away_probability,
        away_serve_point_lower=away_lower,
        away_serve_point_upper=away_upper,
        home_effective_sample_size=home_ess,
        away_effective_sample_size=away_ess,
        supported=supported,
        support_status=support_status,
        training_cutoff_wall_ns=artifact.training_cutoff_wall_ns,
        model_sha256=artifact.model_sha256,
        prematch_artifact_sha256=expert_contract_sha256(
            _prematch_artifact_payload(artifact)
        ),
        feature_definition_sha256=features.feature_definition_sha256,
        feature_vector_sha256=features.feature_vector_sha256,
        abstention_reason=(
            None if supported else DecisionReason.MODEL_UNCERTAIN
        ),
    )


__all__ = (
    "FrozenPrematchArtifact",
    "HistoricalRow",
    "PREMATCH_FEATURE_DEFINITION_SHA256",
    "PrematchFeatures",
    "PrematchPrior",
    "build_features",
    "estimate_prematch",
)
