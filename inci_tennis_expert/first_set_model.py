from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from enum import Enum
from hashlib import sha256
import json
from typing import Final

from .contracts import MatchFormat, PlayerSide, SetScore


_BO3: Final[MatchFormat] = (
    MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS
)
_DECIMAL_CONTEXT: Final[Context] = Context(prec=50)
_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")


class FirstSetModelError(ValueError):
    pass


class FirstSetAbstentionReason(str, Enum):
    INCOMPLETE_HISTORY = "incomplete_history"
    ILLEGAL_HISTORY = "illegal_history"
    AMBIGUOUS_SERVER = "ambiguous_server"
    AMBIGUOUS_WINNER = "ambiguous_winner"
    MISSING_PROVENANCE = "missing_provenance"
    INVALID_PROVENANCE = "invalid_provenance"
    MIXED_CONSENSUS_EPOCHS = "mixed_consensus_epochs"
    REPLAYED_TRANSITION = "replayed_transition"


def _nonempty_text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(name)
    if not value or value != value.strip():
        raise FirstSetModelError(name)
    return value


def _positive_integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(name)
    if value <= 0:
        raise FirstSetModelError(name)
    return value


def _nonnegative_integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(name)
    if value < 0:
        raise FirstSetModelError(name)
    return value


def _finite_decimal(value: object, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(name)
    if not value.is_finite():
        raise FirstSetModelError(name)
    return value


def _sha256(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(name)
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise FirstSetModelError(name)
    return value


def _optional_sha256(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _sha256(value, name)


def _lineage_sha256s(
    value: object,
    name: str,
) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise TypeError(name)
    for lineage_sha256 in value:
        _sha256(lineage_sha256, name)
    return value  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class BetaDistribution:
    alpha: Decimal
    beta: Decimal

    def __post_init__(self) -> None:
        if _finite_decimal(self.alpha, "alpha") <= _ZERO:
            raise FirstSetModelError("alpha")
        if _finite_decimal(self.beta, "beta") <= _ZERO:
            raise FirstSetModelError("beta")

    @property
    def mean(self) -> Decimal:
        with localcontext(_DECIMAL_CONTEXT):
            return self.alpha / (self.alpha + self.beta)


@dataclass(frozen=True, slots=True)
class FirstSetParameters:
    version: str
    evidence_weight: Decimal

    def __post_init__(self) -> None:
        _nonempty_text(self.version, "version")
        weight = _finite_decimal(self.evidence_weight, "evidence_weight")
        if weight <= _ZERO or weight > _ONE:
            raise FirstSetModelError("evidence_weight")


@dataclass(frozen=True, slots=True)
class FirstSetPoint:
    point_id: str
    sequence_number: int
    set_number: int
    server: PlayerSide | None
    winner: PlayerSide | None
    consensus_epoch: int | None
    consensus_transition_sha256: str | None
    supporting_source_lineage_sha256s: tuple[str, ...]

    def __post_init__(self) -> None:
        _nonempty_text(self.point_id, "point_id")
        _positive_integer(self.sequence_number, "sequence_number")
        _positive_integer(self.set_number, "set_number")
        if self.server is not None and type(self.server) is not PlayerSide:
            raise TypeError("server")
        if self.winner is not None and type(self.winner) is not PlayerSide:
            raise TypeError("winner")
        if self.consensus_epoch is not None:
            _nonnegative_integer(self.consensus_epoch, "consensus_epoch")
        _optional_sha256(
            self.consensus_transition_sha256,
            "consensus_transition_sha256",
        )
        _lineage_sha256s(
            self.supporting_source_lineage_sha256s,
            "supporting_source_lineage_sha256s",
        )


def _canonical_decimal(value: Decimal) -> str:
    _finite_decimal(value, "value")
    if value.is_zero():
        return "0e0"
    sign, digits, exponent = value.as_tuple()
    normalized_digits = list(digits)
    normalized_exponent = exponent
    while normalized_digits[-1] == 0:
        normalized_digits.pop()
        normalized_exponent += 1
    coefficient = "".join(str(digit) for digit in normalized_digits)
    prefix = "-" if sign else ""
    return f"{prefix}{coefficient}e{normalized_exponent}"


def _domain_sha256(domain: bytes, value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return sha256(domain + encoded).hexdigest()


def _point_history_sha256(points: tuple[FirstSetPoint, ...]) -> str:
    return _domain_sha256(
        b"INCI-FIRST-SET-POINT-HISTORY-SHA256-V1\0",
        {
            "canonical_version": 1,
            "points": [
                {
                    "consensus_epoch": point.consensus_epoch,
                    "consensus_transition_sha256": (
                        point.consensus_transition_sha256
                    ),
                    "point_id": point.point_id,
                    "sequence_number": point.sequence_number,
                    "server": (
                        None if point.server is None else point.server.value
                    ),
                    "set_number": point.set_number,
                    "supporting_source_lineage_sha256s": sorted(
                        point.supporting_source_lineage_sha256s
                    ),
                    "winner": (
                        None if point.winner is None else point.winner.value
                    ),
                }
                for point in points
            ],
        },
    )


def _terminal_set_sha256(terminal_set: SetScore) -> str:
    return _domain_sha256(
        b"INCI-FIRST-SET-TERMINAL-SHA256-V1\0",
        {
            "canonical_version": 1,
            "terminal_set": {
                "games_away": terminal_set.games_away,
                "games_home": terminal_set.games_home,
                "tiebreak_points_away": terminal_set.tiebreak_points_away,
                "tiebreak_points_home": terminal_set.tiebreak_points_home,
            },
        },
    )


def _point_provenance(
    points: tuple[FirstSetPoint, ...],
) -> tuple[
    int | None,
    tuple[int | None, ...],
    tuple[str | None, ...],
    tuple[str, ...],
]:
    consensus_epochs = tuple(point.consensus_epoch for point in points)
    present_epochs = {
        epoch for epoch in consensus_epochs if epoch is not None
    }
    consensus_epoch = (
        next(iter(present_epochs))
        if len(present_epochs) == 1
        and all(epoch is not None for epoch in consensus_epochs)
        else None
    )
    transition_sha256s = tuple(
        point.consensus_transition_sha256 for point in points
    )
    lineage_sha256s = tuple(
        sorted(
            {
                lineage_sha256
                for point in points
                for lineage_sha256
                in point.supporting_source_lineage_sha256s
            }
        )
    )
    return (
        consensus_epoch,
        consensus_epochs,
        transition_sha256s,
        lineage_sha256s,
    )


@dataclass(frozen=True, slots=True)
class ServicePointSupport:
    service_points_won: int
    service_points_lost: int

    def __post_init__(self) -> None:
        _nonnegative_integer(
            self.service_points_won,
            "service_points_won",
        )
        _nonnegative_integer(
            self.service_points_lost,
            "service_points_lost",
        )

    @property
    def service_points(self) -> int:
        return self.service_points_won + self.service_points_lost


_NO_SUPPORT: Final[ServicePointSupport] = ServicePointSupport(0, 0)


def _opposite(side: PlayerSide) -> PlayerSide:
    if side is PlayerSide.HOME:
        return PlayerSide.AWAY
    return PlayerSide.HOME


def _tiebreak_server(
    first_server: PlayerSide,
    completed_points: int,
) -> PlayerSide:
    if completed_points % 4 in {0, 3}:
        return first_server
    return _opposite(first_server)


def _replay_first_set(
    points: tuple[FirstSetPoint, ...],
) -> SetScore | None:
    if not points or points[0].server is None:
        return None
    game_server = points[0].server
    games_home = 0
    games_away = 0
    points_home = 0
    points_away = 0
    in_tiebreak = False
    tiebreak_first_server: PlayerSide | None = None
    tiebreak_home = 0
    tiebreak_away = 0

    for index, point in enumerate(points):
        assert point.server is not None
        assert point.winner is not None
        if in_tiebreak:
            assert tiebreak_first_server is not None
            expected_server = _tiebreak_server(
                tiebreak_first_server,
                tiebreak_home + tiebreak_away,
            )
            if point.server is not expected_server:
                return None
            if point.winner is PlayerSide.HOME:
                tiebreak_home += 1
            else:
                tiebreak_away += 1
            if (
                max(tiebreak_home, tiebreak_away) >= 7
                and abs(tiebreak_home - tiebreak_away) >= 2
            ):
                if index != len(points) - 1:
                    return None
                return SetScore(
                    games_home=7 if tiebreak_home > tiebreak_away else 6,
                    games_away=7 if tiebreak_away > tiebreak_home else 6,
                    tiebreak_points_home=tiebreak_home,
                    tiebreak_points_away=tiebreak_away,
                )
            continue

        if point.server is not game_server:
            return None
        game_winner: PlayerSide | None = None
        if points_home == 4:
            if point.winner is PlayerSide.HOME:
                game_winner = PlayerSide.HOME
            else:
                points_home = points_away = 3
        elif points_away == 4:
            if point.winner is PlayerSide.AWAY:
                game_winner = PlayerSide.AWAY
            else:
                points_home = points_away = 3
        elif points_home == points_away == 3:
            if point.winner is PlayerSide.HOME:
                points_home = 4
            else:
                points_away = 4
        elif point.winner is PlayerSide.HOME:
            if points_home == 3:
                game_winner = PlayerSide.HOME
            else:
                points_home += 1
        elif points_away == 3:
            game_winner = PlayerSide.AWAY
        else:
            points_away += 1

        if game_winner is None:
            continue
        if game_winner is PlayerSide.HOME:
            games_home += 1
        else:
            games_away += 1
        points_home = points_away = 0
        game_server = _opposite(game_server)
        if (
            max(games_home, games_away) >= 6
            and abs(games_home - games_away) >= 2
        ):
            if index != len(points) - 1:
                return None
            return SetScore(games_home, games_away, None, None)
        if games_home == games_away == 6:
            in_tiebreak = True
            tiebreak_first_server = game_server
    return None


@dataclass(frozen=True, slots=True)
class FirstSetReview:
    supported: bool
    abstention_reason: FirstSetAbstentionReason | None
    home_posterior: BetaDistribution | None
    away_posterior: BetaDistribution | None
    home_support: ServicePointSupport
    away_support: ServicePointSupport
    accepted_point_count: int
    consensus_epoch: int | None
    consensus_epochs: tuple[int | None, ...]
    consensus_transition_sha256s: tuple[str | None, ...]
    supporting_source_lineage_sha256s: tuple[str, ...]
    point_history: tuple[FirstSetPoint, ...]
    terminal_set: SetScore
    point_history_sha256: str
    terminal_set_sha256: str
    parameters_version: str
    evidence_weight: Decimal

    def __post_init__(self) -> None:
        if type(self.supported) is not bool:
            raise TypeError("supported")
        if (
            self.abstention_reason is not None
            and type(self.abstention_reason) is not FirstSetAbstentionReason
        ):
            raise TypeError("abstention_reason")
        if self.home_posterior is not None and type(
            self.home_posterior
        ) is not BetaDistribution:
            raise TypeError("home_posterior")
        if self.away_posterior is not None and type(
            self.away_posterior
        ) is not BetaDistribution:
            raise TypeError("away_posterior")
        if type(self.home_support) is not ServicePointSupport:
            raise TypeError("home_support")
        if type(self.away_support) is not ServicePointSupport:
            raise TypeError("away_support")
        _nonnegative_integer(self.accepted_point_count, "accepted_point_count")
        if type(self.point_history) is not tuple or any(
            type(point) is not FirstSetPoint for point in self.point_history
        ):
            raise TypeError("point_history")
        if len(self.point_history) != self.accepted_point_count:
            raise FirstSetModelError("point_history")
        if type(self.terminal_set) is not SetScore:
            raise TypeError("terminal_set")
        if self.consensus_epoch is not None:
            _nonnegative_integer(self.consensus_epoch, "consensus_epoch")
        if type(self.consensus_epochs) is not tuple:
            raise TypeError("consensus_epochs")
        for consensus_epoch in self.consensus_epochs:
            if consensus_epoch is not None:
                _nonnegative_integer(consensus_epoch, "consensus_epochs")
        if len(self.consensus_epochs) != self.accepted_point_count:
            raise FirstSetModelError("consensus_epochs")
        if type(self.consensus_transition_sha256s) is not tuple:
            raise TypeError("consensus_transition_sha256s")
        for transition_sha256 in self.consensus_transition_sha256s:
            _optional_sha256(
                transition_sha256,
                "consensus_transition_sha256s",
            )
        if (
            len(self.consensus_transition_sha256s)
            != self.accepted_point_count
        ):
            raise FirstSetModelError("consensus_transition_sha256s")
        lineages = _lineage_sha256s(
            self.supporting_source_lineage_sha256s,
            "supporting_source_lineage_sha256s",
        )
        if lineages != tuple(sorted(set(lineages))):
            raise FirstSetModelError("supporting_source_lineage_sha256s")
        _sha256(self.point_history_sha256, "point_history_sha256")
        _sha256(self.terminal_set_sha256, "terminal_set_sha256")
        if self.point_history_sha256 != _point_history_sha256(
            self.point_history
        ):
            raise FirstSetModelError("point_history_sha256")
        if self.terminal_set_sha256 != _terminal_set_sha256(
            self.terminal_set
        ):
            raise FirstSetModelError("terminal_set_sha256")
        if _point_provenance(self.point_history) != (
            self.consensus_epoch,
            self.consensus_epochs,
            self.consensus_transition_sha256s,
            self.supporting_source_lineage_sha256s,
        ):
            raise FirstSetModelError("point_provenance")
        _nonempty_text(self.parameters_version, "parameters_version")
        weight = _finite_decimal(self.evidence_weight, "evidence_weight")
        if weight <= _ZERO or weight > _ONE:
            raise FirstSetModelError("evidence_weight")
        if self.supported:
            if (
                self.abstention_reason is not None
                or self.home_posterior is None
                or self.away_posterior is None
                or self.accepted_point_count == 0
                or self.consensus_epoch is None
                or any(
                    epoch != self.consensus_epoch
                    for epoch in self.consensus_epochs
                )
                or any(
                    transition_sha256 is None
                    for transition_sha256
                    in self.consensus_transition_sha256s
                )
                or len(set(self.consensus_transition_sha256s))
                != self.accepted_point_count
                or len(self.supporting_source_lineage_sha256s) < 2
            ):
                raise FirstSetModelError("supported_review")
            if (
                self.home_support.service_points
                + self.away_support.service_points
                != self.accepted_point_count
            ):
                raise FirstSetModelError("support_count")
        elif (
            self.abstention_reason is None
            or self.home_posterior is not None
            or self.away_posterior is not None
            or self.home_support.service_points != 0
            or self.away_support.service_points != 0
        ):
            raise FirstSetModelError("abstention_review")


def first_set_review_sha256(review: FirstSetReview) -> str:
    if type(review) is not FirstSetReview:
        raise TypeError("review")

    def posterior_value(
        posterior: BetaDistribution | None,
    ) -> dict[str, str] | None:
        if posterior is None:
            return None
        return {
            "alpha": _canonical_decimal(posterior.alpha),
            "beta": _canonical_decimal(posterior.beta),
        }

    return _domain_sha256(
        b"INCI-FIRST-SET-REVIEW-SHA256-V1\0",
        {
            "abstention_reason": (
                None
                if review.abstention_reason is None
                else review.abstention_reason.value
            ),
            "accepted_point_count": review.accepted_point_count,
            "away_posterior": posterior_value(review.away_posterior),
            "away_support": {
                "service_points_lost": (
                    review.away_support.service_points_lost
                ),
                "service_points_won": (
                    review.away_support.service_points_won
                ),
            },
            "canonical_version": 1,
            "consensus_epoch": review.consensus_epoch,
            "consensus_epochs": list(review.consensus_epochs),
            "consensus_transition_sha256s": list(
                review.consensus_transition_sha256s
            ),
            "evidence_weight": _canonical_decimal(review.evidence_weight),
            "home_posterior": posterior_value(review.home_posterior),
            "home_support": {
                "service_points_lost": (
                    review.home_support.service_points_lost
                ),
                "service_points_won": (
                    review.home_support.service_points_won
                ),
            },
            "parameters_version": review.parameters_version,
            "point_history_sha256": review.point_history_sha256,
            "supported": review.supported,
            "supporting_source_lineage_sha256s": list(
                review.supporting_source_lineage_sha256s
            ),
            "terminal_set_sha256": review.terminal_set_sha256,
        },
    )


class FirstSetBayesianModel:
    __slots__ = (
        "_away_prior",
        "_frozen_review",
        "_home_prior",
        "_parameters",
        "_points",
        "_seen_point_ids",
    )

    def __init__(
        self,
        *,
        home_prior: BetaDistribution,
        away_prior: BetaDistribution,
        parameters: FirstSetParameters,
        match_format: MatchFormat,
    ) -> None:
        if type(home_prior) is not BetaDistribution:
            raise TypeError("home_prior")
        if type(away_prior) is not BetaDistribution:
            raise TypeError("away_prior")
        if type(parameters) is not FirstSetParameters:
            raise TypeError("parameters")
        if type(match_format) is not MatchFormat:
            raise TypeError("match_format")
        if match_format is not _BO3:
            raise FirstSetModelError("match_format")
        self._home_prior = home_prior
        self._away_prior = away_prior
        self._parameters = parameters
        self._points: list[FirstSetPoint] = []
        self._seen_point_ids: set[str] = set()
        self._frozen_review: FirstSetReview | None = None

    @property
    def is_frozen(self) -> bool:
        return self._frozen_review is not None

    @property
    def review(self) -> FirstSetReview | None:
        return self._frozen_review

    def observe_point(self, point: FirstSetPoint) -> bool:
        if type(point) is not FirstSetPoint:
            raise TypeError("point")
        if point.point_id in self._seen_point_ids:
            raise FirstSetModelError("duplicate_point_id")
        if self.is_frozen:
            if point.set_number == 1:
                raise FirstSetModelError("first_set_frozen")
            self._seen_point_ids.add(point.point_id)
            return False
        if point.set_number != 1:
            raise FirstSetModelError("first_set_only")
        self._seen_point_ids.add(point.point_id)
        self._points.append(point)
        return True

    def _abstain(
        self,
        reason: FirstSetAbstentionReason,
        terminal_set: SetScore,
    ) -> FirstSetReview:
        (
            consensus_epoch,
            consensus_epochs,
            transition_sha256s,
            lineage_sha256s,
            point_history_sha256,
            terminal_set_sha256,
        ) = self._provenance(terminal_set)
        return FirstSetReview(
            supported=False,
            abstention_reason=reason,
            home_posterior=None,
            away_posterior=None,
            home_support=_NO_SUPPORT,
            away_support=_NO_SUPPORT,
            accepted_point_count=len(self._points),
            consensus_epoch=consensus_epoch,
            consensus_epochs=consensus_epochs,
            consensus_transition_sha256s=transition_sha256s,
            supporting_source_lineage_sha256s=lineage_sha256s,
            point_history=tuple(self._points),
            terminal_set=terminal_set,
            point_history_sha256=point_history_sha256,
            terminal_set_sha256=terminal_set_sha256,
            parameters_version=self._parameters.version,
            evidence_weight=self._parameters.evidence_weight,
        )

    def _provenance(
        self,
        terminal_set: SetScore,
    ) -> tuple[
        int | None,
        tuple[int | None, ...],
        tuple[str | None, ...],
        tuple[str, ...],
        str,
        str,
    ]:
        points = tuple(self._points)
        (
            consensus_epoch,
            consensus_epochs,
            transition_sha256s,
            lineage_sha256s,
        ) = _point_provenance(points)
        return (
            consensus_epoch,
            consensus_epochs,
            transition_sha256s,
            lineage_sha256s,
            _point_history_sha256(points),
            _terminal_set_sha256(terminal_set),
        )

    def complete_set_one(
        self,
        *,
        terminal_set: SetScore,
    ) -> FirstSetReview:
        if type(terminal_set) is not SetScore:
            raise TypeError("terminal_set")
        if self._frozen_review is not None:
            return self._frozen_review
        if not self._points:
            self._frozen_review = self._abstain(
                FirstSetAbstentionReason.INCOMPLETE_HISTORY,
                terminal_set,
            )
            return self._frozen_review
        expected_sequence = tuple(range(1, len(self._points) + 1))
        observed_sequence = tuple(
            point.sequence_number for point in self._points
        )
        if observed_sequence != expected_sequence:
            self._frozen_review = self._abstain(
                FirstSetAbstentionReason.INCOMPLETE_HISTORY,
                terminal_set,
            )
            return self._frozen_review
        if any(
            point.consensus_epoch is None
            or point.consensus_transition_sha256 is None
            or not point.supporting_source_lineage_sha256s
            for point in self._points
        ):
            self._frozen_review = self._abstain(
                FirstSetAbstentionReason.MISSING_PROVENANCE,
                terminal_set,
            )
            return self._frozen_review
        if any(
            len(point.supporting_source_lineage_sha256s) < 2
            or len(set(point.supporting_source_lineage_sha256s))
            != len(point.supporting_source_lineage_sha256s)
            for point in self._points
        ):
            self._frozen_review = self._abstain(
                FirstSetAbstentionReason.INVALID_PROVENANCE,
                terminal_set,
            )
            return self._frozen_review
        consensus_epochs = {
            point.consensus_epoch for point in self._points
        }
        if len(consensus_epochs) != 1:
            self._frozen_review = self._abstain(
                FirstSetAbstentionReason.MIXED_CONSENSUS_EPOCHS,
                terminal_set,
            )
            return self._frozen_review
        transition_sha256s = tuple(
            point.consensus_transition_sha256 for point in self._points
        )
        if len(set(transition_sha256s)) != len(transition_sha256s):
            self._frozen_review = self._abstain(
                FirstSetAbstentionReason.REPLAYED_TRANSITION,
                terminal_set,
            )
            return self._frozen_review
        if any(point.server is None for point in self._points):
            self._frozen_review = self._abstain(
                FirstSetAbstentionReason.AMBIGUOUS_SERVER,
                terminal_set,
            )
            return self._frozen_review
        if any(point.winner is None for point in self._points):
            self._frozen_review = self._abstain(
                FirstSetAbstentionReason.AMBIGUOUS_WINNER,
                terminal_set,
            )
            return self._frozen_review
        if _replay_first_set(tuple(self._points)) != terminal_set:
            self._frozen_review = self._abstain(
                FirstSetAbstentionReason.ILLEGAL_HISTORY,
                terminal_set,
            )
            return self._frozen_review

        home_won = 0
        home_lost = 0
        away_won = 0
        away_lost = 0
        for point in self._points:
            assert point.server is not None
            assert point.winner is not None
            server_won = point.winner is point.server
            if point.server is PlayerSide.HOME:
                if server_won:
                    home_won += 1
                else:
                    home_lost += 1
            elif server_won:
                away_won += 1
            else:
                away_lost += 1
        home_support = ServicePointSupport(home_won, home_lost)
        away_support = ServicePointSupport(away_won, away_lost)
        weight = self._parameters.evidence_weight
        with localcontext(_DECIMAL_CONTEXT):
            home_posterior = BetaDistribution(
                alpha=self._home_prior.alpha + weight * Decimal(home_won),
                beta=self._home_prior.beta + weight * Decimal(home_lost),
            )
            away_posterior = BetaDistribution(
                alpha=self._away_prior.alpha + weight * Decimal(away_won),
                beta=self._away_prior.beta + weight * Decimal(away_lost),
            )
        (
            consensus_epoch,
            consensus_epochs,
            transition_sha256s,
            lineage_sha256s,
            point_history_sha256,
            terminal_set_sha256,
        ) = self._provenance(terminal_set)
        assert consensus_epoch is not None
        self._frozen_review = FirstSetReview(
            supported=True,
            abstention_reason=None,
            home_posterior=home_posterior,
            away_posterior=away_posterior,
            home_support=home_support,
            away_support=away_support,
            accepted_point_count=len(self._points),
            consensus_epoch=consensus_epoch,
            consensus_epochs=consensus_epochs,
            consensus_transition_sha256s=transition_sha256s,
            supporting_source_lineage_sha256s=lineage_sha256s,
            point_history=tuple(self._points),
            terminal_set=terminal_set,
            point_history_sha256=point_history_sha256,
            terminal_set_sha256=terminal_set_sha256,
            parameters_version=self._parameters.version,
            evidence_weight=weight,
        )
        return self._frozen_review
