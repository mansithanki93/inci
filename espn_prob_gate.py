"""Fail-closed score + win-prob gate for v6 paper entries.

An entry is evaluated only when:
1. the Kalshi contract binds to a scoreboard match by player name
   (ESPN ATP/WTA, plus Live Tennis ITF/challenger when configured),
2. the match is live (or pre, with neutral score),
3. the neutral score-collapse guard clears ``espn_min_model_prob``, and
4. when a Models 1+2 provider is configured, both score-updated priors are
   available and their conservative edge clears ``espn_min_edge``.

Without a configured prematch provider, the neutral transform is explicitly a
collapse guard only; it never claims a fair-value edge against the market.

Unbound markets are blocked — price dips alone cannot authorize a buy.
Live Tennis is polled only after ESPN bind fails for ITF-marked tickers,
so free-tier API quota is not burned on ATP/WTA sessions.
"""
from __future__ import annotations

import math
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from espn_tennis import EspnMatch, EspnScoreboardCache
from live_tennis import LiveTennisCache, resolve_api_key
from tennis_win_prob import (
    completed_sets_won,
    current_games,
    match_win_probability,
    match_win_probability_from_prematch,
    score_transition_advances,
    set_complete,
)


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def name_tokens(value: str) -> frozenset[str]:
    return frozenset(tok for tok in normalize_name(value).split() if tok)


def names_match(player: str, candidate: str) -> bool:
    """Match a player identity by surname plus compatible first identity.

    Scoreboards frequently abbreviate the given name (``A. Oetzbach``), so
    matching initials are accepted.  A shared surname alone is deliberately
    insufficient: it would bind Venus to Serena Williams.
    """
    a = normalize_name(player).split()
    b = normalize_name(candidate).split()
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) < 2 or len(b) < 2 or a[-1] != b[-1]:
        return False
    first_a, first_b = a[0], b[0]
    return first_a == first_b or (
        bool(first_a) and len(first_b) == 1 and first_a[0] == first_b
    )


def _identity_mentioned(candidate: str, text: str) -> bool:
    """Require a contiguous first+surname identity in free-form event text."""
    identity = normalize_name(candidate).split()
    words = normalize_name(text).split()
    if len(identity) < 2 or len(words) < len(identity):
        return False
    for offset in range(len(words) - len(identity) + 1):
        mention = words[offset:offset + len(identity)]
        if mention == identity:
            return True
        # Scoreboards and exchanges may use a given-name initial, but the
        # remainder of the identity must still match contiguously.
        if (mention[1:] == identity[1:] and len(mention[0]) == 1
                and mention[0] == identity[0][0]):
            return True
    return False


@dataclass(frozen=True)
class GateDecision:
    allow: bool
    reason: str
    model_prob: Decimal | None = None
    market_prob: Decimal | None = None
    edge: Decimal | None = None
    espn_match_id: str | None = None
    espn_player: str | None = None
    model_1_prob: Decimal | None = None
    model_2_prob: Decimal | None = None
    prior_source_sha256: str | None = None
    prior_generated_at: str | None = None
    prior_model_1_id: str | None = None
    prior_model_2_id: str | None = None
    score_source: str | None = None
    score_match_id: str | None = None
    score_athlete_id: str | None = None
    score_opponent_id: str | None = None
    score_player_name: str | None = None
    score_opponent_name: str | None = None
    score_timestamp: Decimal | None = None
    score_lifecycle_state: str | None = None
    score_observed: bool | None = None
    score_best_of: int | None = None
    score_sets_for: int | None = None
    score_sets_against: int | None = None
    score_games_for: int | None = None
    score_games_against: int | None = None
    prematch_model_1_prob: Decimal | None = None
    prematch_model_2_prob: Decimal | None = None
    prior_model_as_of: str | None = None
    prior_match_start: str | None = None


@dataclass(frozen=True)
class _PrematchModels:
    model_1: Decimal
    model_2: Decimal
    source_sha256: str
    generated_at: str
    model_1_id: str
    model_2_id: str
    model_as_of: str
    match_start: str

    @property
    def probabilities(self) -> tuple[Decimal, Decimal]:
        return self.model_1, self.model_2


_NON_MATCH_OUTCOME = re.compile(
    r"\b(?:first|second|third|fourth|fifth|opening|final|a|one|any|each)\s+"
    r"(?:set|game|point|tiebreak|tie\s+break)\b|"
    r"\b(?:set|game|point|tiebreak)s?\s+\d+\b|"
    r"\b(?:serve|serves|double\s+fault)\b")


def match_winner_player_name(value: str) -> str | None:
    """Extract a player only from a strict Kalshi match-winner question."""
    text = str(value or "").strip()
    match = re.fullmatch(r"(?is)will\s+(.+?)\s+win\s+(.+?)\s*\?", text)
    if match is None:
        return None
    player = match.group(1).strip()
    outcome = normalize_name(match.group(2))
    if (not player or not outcome.endswith(" match")
            or _NON_MATCH_OUTCOME.search(outcome)):
        return None
    return player


def ticker_wants_live_tennis(ticker: str, event_title: str = "",
                             substrings=("ITF",)) -> bool:
    """True when the contract should consult the Live Tennis secondary feed."""
    hay = f"{ticker or ''} {event_title or ''}".upper()
    return any(str(part).upper() in hay for part in substrings if part)


def display_player_name(player_name: str) -> str:
    """Extract the player from a Kalshi 'Will X win the …' market title."""
    text = str(player_name or "").strip()
    lower = text.lower()
    if lower.startswith("will ") and " win " in lower[5:]:
        mid = text[5:]
        idx = mid.lower().find(" win ")
        if idx > 0:
            return mid[:idx].strip()
    return text


class EspnProbGate:
    def __init__(
            self,
            config,
            cache: EspnScoreboardCache | None = None,
            live_tennis_cache: LiveTennisCache | None = None,
            prematch_prior_provider=None,
            clock=time.time,
            max_score_age_s: float | None = None,
    ):
        self.cfg = config
        self.clock = clock
        self.prematch_prior_provider = prematch_prior_provider
        self._pinned_priors: dict[tuple[str, str, str], _PrematchModels] = {}
        self._lifecycle_by_match: dict[str, int] = {}
        self._score_by_orientation: dict[tuple[str, str, str], tuple] = {}
        espn_ttl = float(getattr(config, "espn_cache_s", 15.0))
        self.max_score_age_s = float(
            max_score_age_s if max_score_age_s is not None else getattr(
                config, "score_provider_max_age_s",
                max(45.0, espn_ttl * 3.0)))
        self.cache = cache or EspnScoreboardCache(
            leagues=tuple(getattr(config, "espn_leagues", ("atp", "wta"))),
            ttl_s=espn_ttl,
            max_stale_s=float(getattr(
                config, "espn_max_stale_s", max(45.0, espn_ttl * 3.0))),
        )
        self.live_tennis_cache = live_tennis_cache
        if (self.live_tennis_cache is None
                and bool(getattr(config, "live_tennis_enabled", False))):
            api_key = resolve_api_key(config)
            if api_key:
                self.live_tennis_cache = LiveTennisCache(
                    api_key=api_key,
                    tours=tuple(getattr(
                        config, "live_tennis_tours", ("itf",))),
                    ttl_s=float(getattr(config, "live_tennis_cache_s", 120.0)),
                    max_stale_s=float(getattr(
                        config, "live_tennis_max_stale_s",
                        max(360.0, float(getattr(
                            config, "live_tennis_cache_s", 120.0)) * 3.0))),
                    include_upcoming=bool(getattr(
                        config, "live_tennis_include_upcoming", False)),
                )

    def enabled(self) -> bool:
        return bool(getattr(self.cfg, "espn_gate_enabled", True))

    def find_bind(self, *, ticker: str, player_name: str, event_title: str):
        """Return ``(match, me, opp)`` for a live/pre scoreboard card, or None.

        Used by entry gating and by discovery ranking. Scoreboard fetch errors
        are swallowed so discovery can fail-open to depth ranking.
        """
        player_name = display_player_name(player_name)
        event_title = event_title or ""
        try:
            matches = self.cache.matches()
        except Exception:  # noqa: BLE001
            matches = ()
        bound = self._bind(player_name, event_title, matches)
        if bound is None and self.live_tennis_cache is not None:
            needles = tuple(getattr(
                self.cfg, "live_tennis_ticker_substrings", ("ITF",)))
            if ticker_wants_live_tennis(ticker, event_title, needles):
                try:
                    lt_matches = self.live_tennis_cache.matches()
                except Exception:  # noqa: BLE001
                    lt_matches = ()
                if lt_matches:
                    bound = self._bind(player_name, event_title, lt_matches)
        if bound is None:
            return None
        match, me, opp = bound
        if match.state == "post":
            return None
        if not self._score_fresh(match):
            return None
        return bound

    def is_bound(self, *, ticker: str, player_name: str,
                 event_title: str) -> bool:
        """True when a live/pre scoreboard card binds (no ask/edge checks)."""
        return self.find_bind(
            ticker=ticker, player_name=player_name,
            event_title=event_title) is not None

    def binding_identity(self, *, ticker: str, player_name: str,
                         event_title: str) -> tuple[str, str, str] | None:
        """Return the provider-qualified scoreboard orientation, or ``None``.

        Discovery uses this stronger result to prove that two Kalshi Markets
        represent opposite players in the same match. A boolean bind cannot
        distinguish that relationship from two same-player props.
        """
        binding = self.binding_provenance(
            ticker=ticker, player_name=player_name,
            event_title=event_title)
        return None if binding is None else binding[:3]

    def binding_provenance(self, *, ticker: str, player_name: str,
                           event_title: str) -> tuple | None:
        """Return the exact five-part prior lookup identity at discovery."""
        match_winner = match_winner_player_name(player_name)
        if match_winner is None:
            return None
        bound = self.find_bind(
            ticker=ticker, player_name=match_winner,
            event_title=event_title)
        if bound is None:
            return None
        match, me, opp = bound
        competition_id = str(match.competition_id or "")
        source = "lt" if competition_id.startswith("lt:") else "espn"
        if not competition_id:
            return None
        if not competition_id.startswith(source + ":"):
            competition_id = source + ":" + competition_id

        def athlete_identity(competitor):
            athlete_id = str(competitor.athlete_id or "")
            if not athlete_id:
                return None
            prefix = source + ":athlete:"
            if not athlete_id.startswith(prefix):
                athlete_id = prefix + athlete_id
            return athlete_id

        selected_id = athlete_identity(me)
        opponent_id = athlete_identity(opp)
        if (selected_id is None or opponent_id is None
                or selected_id == opponent_id):
            return None
        return (competition_id, selected_id, opponent_id,
                me.display_name, opp.display_name)

    def model_edge_score(self, *, ticker: str, player_name: str,
                         event_title: str, ask_cents,
                         scheduled_start_ts=None) -> tuple:
        """Sibling-ranking score: ``(entry_eligible, edge)`` — higher wins.

        Eligibility applies the same model-probability and edge floors as
        ``decide`` before raw edge can choose between same-event siblings.
        Neutral guard-only cards use zero edge and only their collapse floor.
        """
        original_name = str(player_name or "").strip()
        if original_name.lower().startswith("will "):
            player_name = match_winner_player_name(original_name)
            if player_name is None:
                return (0, Decimal(0))
        bound = self.find_bind(
            ticker=ticker, player_name=player_name,
            event_title=event_title)
        if bound is None:
            return (0, Decimal(0))
        match, me, opp = bound
        try:
            ask = Decimal(str(ask_cents))
        except Exception:
            return (0, Decimal(0))
        if ask <= 0 or ask >= 100:
            return (0, Decimal(0))
        try:
            state = self._score_state(match, me, opp)
        except (TypeError, ValueError):
            return (0, Decimal(0))
        if (self._prematch_after_start(match, scheduled_start_ts)
                or not self._lifecycle_advances(match)):
            return (0, Decimal(0))
        priors = self._prematch_priors(
            match, me, opp, scheduled_start_ts=scheduled_start_ts)
        if priors is None:
            if self.prematch_prior_provider is not None:
                return (0, Decimal(0))
            guard = Decimal(str(round(match_win_probability(
                *state, best_of=match.best_of), 6)))
            minimum = Decimal(str(self.cfg.espn_min_model_prob))
            return (1 if guard >= minimum else 0, Decimal(0))
        models = tuple(match_win_probability_from_prematch(
            prior, *state, best_of=match.best_of)
                       for prior in priors.probabilities)
        model = Decimal(str(round(min(models), 6)))
        edge = model - (ask / Decimal(100))
        eligible = (
            model >= Decimal(str(self.cfg.espn_min_model_prob))
            and edge >= Decimal(str(self.cfg.espn_min_edge))
        )
        return (1 if eligible else 0, edge)

    def decide(self, *, ticker: str, player_name: str, event_title: str,
               ask_cents, scheduled_start_ts=None) -> GateDecision:
        if not self.enabled():
            return GateDecision(True, "espn_gate_disabled")
        try:
            ask = Decimal(str(ask_cents))
        except Exception:
            return GateDecision(False, "blocked:invalid_ask")
        if ask <= 0 or ask >= 100:
            return GateDecision(False, "blocked:invalid_ask")
        market_prob = ask / Decimal(100)

        original_name = str(player_name or "").strip()
        if original_name.lower().startswith("will "):
            player_name = match_winner_player_name(original_name)
            if player_name is None:
                return GateDecision(
                    False, "blocked:non_match_winner_contract",
                    market_prob=market_prob)
        else:
            player_name = display_player_name(original_name)
        lt_error = None
        try:
            matches = self.cache.matches()
        except Exception as error:  # noqa: BLE001
            return GateDecision(
                False, f"blocked:espn_unavailable ({error})")

        bound = self._bind(player_name, event_title, matches)
        if bound is None and self.live_tennis_cache is not None:
            needles = tuple(getattr(
                self.cfg, "live_tennis_ticker_substrings", ("ITF",)))
            if ticker_wants_live_tennis(ticker, event_title, needles):
                try:
                    lt_matches = self.live_tennis_cache.matches()
                except Exception as error:  # noqa: BLE001
                    lt_error = str(error)
                    lt_matches = ()
                if lt_matches:
                    bound = self._bind(player_name, event_title, lt_matches)
        if bound is None:
            extra = ""
            if lt_error:
                extra = f"; live_tennis_unavailable ({lt_error})"
            return GateDecision(
                False,
                "blocked:no_espn_bind "
                f"(ITF/unlisted matches cannot trade on dips alone{extra})")
        match, me, opp = bound
        identity_fields = self._identity_fields(match, me, opp)
        if (identity_fields["score_match_id"] in ("espn:", "lt:")
                or identity_fields["score_athlete_id"] is None
                or identity_fields["score_opponent_id"] is None):
            return GateDecision(
                False, "blocked:missing_provider_athlete_identity",
                market_prob=market_prob,
                espn_match_id=identity_fields["score_match_id"],
                espn_player=me.display_name, **identity_fields)
        if not self._lifecycle_advances(match):
            return GateDecision(
                False, "blocked:score_lifecycle_rewind",
                market_prob=market_prob,
                espn_match_id=identity_fields["score_match_id"],
                espn_player=me.display_name, **identity_fields)
        if match.state == "post":
            try:
                sets_me, sets_opp, g_me, g_opp = self._score_state(
                    match, me, opp)
            except (TypeError, ValueError):
                return GateDecision(
                    False, "blocked:match_over",
                    market_prob=market_prob,
                    espn_match_id=identity_fields["score_match_id"],
                    espn_player=me.display_name, **identity_fields)
            score_fields = {
                **identity_fields,
                "score_sets_for": sets_me,
                "score_sets_against": sets_opp,
                "score_games_for": g_me,
                "score_games_against": g_opp,
            }
            if not self._score_advances(score_fields):
                return GateDecision(
                    False, "blocked:score_progress_rewind",
                    market_prob=market_prob,
                    espn_match_id=identity_fields["score_match_id"],
                    espn_player=me.display_name, **score_fields)
            return GateDecision(
                False, "blocked:match_over",
                market_prob=market_prob,
                espn_match_id=identity_fields["score_match_id"],
                espn_player=me.display_name, **score_fields)
        if not self._score_fresh(match):
            return GateDecision(
                False, "blocked:stale_score",
                market_prob=market_prob,
                espn_match_id=identity_fields["score_match_id"],
                espn_player=me.display_name, **identity_fields)
        try:
            sets_me, sets_opp, g_me, g_opp = self._score_state(
                match, me, opp)
        except (TypeError, ValueError):
            return GateDecision(
                False, "blocked:invalid_score",
                market_prob=market_prob,
                espn_match_id=identity_fields["score_match_id"],
                espn_player=me.display_name, **identity_fields)
        score_fields = {
            **identity_fields,
            "score_sets_for": sets_me,
            "score_sets_against": sets_opp,
            "score_games_for": g_me,
            "score_games_against": g_opp,
        }
        if not self._score_advances(score_fields):
            return GateDecision(
                False, "blocked:score_progress_rewind",
                market_prob=market_prob,
                espn_match_id=identity_fields["score_match_id"],
                espn_player=me.display_name, **score_fields)
        if self._prematch_after_start(match, scheduled_start_ts):
            return GateDecision(
                False, "blocked:prematch_state_after_scheduled_start",
                market_prob=market_prob,
                espn_match_id=identity_fields["score_match_id"],
                espn_player=me.display_name, **score_fields)
        min_p = Decimal(str(self.cfg.espn_min_model_prob))
        min_edge = Decimal(str(self.cfg.espn_min_edge))
        source = ("live_tennis" if str(match.competition_id).startswith("lt:")
                  else "espn")
        priors = self._prematch_priors(
            match, me, opp, scheduled_start_ts=scheduled_start_ts)
        if priors is None:
            if self.prematch_prior_provider is not None:
                return GateDecision(
                    False, "blocked:prematch_prior_unavailable",
                    market_prob=market_prob,
                    espn_match_id=identity_fields["score_match_id"],
                    espn_player=me.display_name, **score_fields)
            # A neutral score transform has no player-skill information. It is
            # useful only as a collapse guard and must never be called edge.
            guard = match_win_probability(
                sets_me, sets_opp, g_me, g_opp, best_of=match.best_of)
            guard_p = Decimal(str(round(guard, 6)))
            if guard_p < min_p:
                return GateDecision(
                    False,
                    f"blocked:score_collapse {guard_p:.3f}<{min_p} "
                    f"score {sets_me}-{sets_opp} games {g_me}-{g_opp} "
                    f"({match.detail})",
                    model_prob=guard_p, market_prob=market_prob, edge=None,
                    espn_match_id=identity_fields["score_match_id"],
                    espn_player=me.display_name, **score_fields)
            return GateDecision(
                True,
                f"{source}_score_guard_only {guard_p:.3f} "
                f"score {sets_me}-{sets_opp} games {g_me}-{g_opp}; "
                "no_prematch_prior",
                model_prob=guard_p, market_prob=market_prob, edge=None,
                espn_match_id=identity_fields["score_match_id"],
                espn_player=me.display_name, **score_fields)

        updated = tuple(match_win_probability_from_prematch(
            prior, sets_me, sets_opp, g_me, g_opp,
            best_of=match.best_of) for prior in priors.probabilities)
        model_1_p, model_2_p = tuple(
            Decimal(str(round(value, 6))) for value in updated)
        model_p = min(model_1_p, model_2_p)
        edge = model_p - market_prob
        model_fields = {
            "model_1_prob": model_1_p,
            "model_2_prob": model_2_p,
            "prior_source_sha256": priors.source_sha256,
            "prior_generated_at": priors.generated_at,
            "prior_model_1_id": priors.model_1_id,
            "prior_model_2_id": priors.model_2_id,
            "prematch_model_1_prob": priors.model_1,
            "prematch_model_2_prob": priors.model_2,
            "prior_model_as_of": priors.model_as_of,
            "prior_match_start": priors.match_start,
        }
        if model_p < min_p:
            return GateDecision(
                False,
                f"blocked:model_prob {model_p:.3f}<{min_p} "
                f"score {sets_me}-{sets_opp} games {g_me}-{g_opp} "
                f"({match.detail})",
                model_prob=model_p, market_prob=market_prob, edge=edge,
                espn_match_id=identity_fields["score_match_id"],
                espn_player=me.display_name, **score_fields, **model_fields)
        if edge < min_edge:
            return GateDecision(
                False,
                f"blocked:edge {edge:+.3f}<{min_edge} "
                f"(model {model_p:.3f} vs ask {market_prob:.3f})",
                model_prob=model_p, market_prob=market_prob, edge=edge,
                espn_match_id=identity_fields["score_match_id"],
                espn_player=me.display_name, **score_fields, **model_fields)
        return GateDecision(
            True,
            f"{source}_ok model {model_p:.3f} edge {edge:+.3f} "
            f"score {sets_me}-{sets_opp} ({match.detail})",
            model_prob=model_p, market_prob=market_prob, edge=edge,
            espn_match_id=identity_fields["score_match_id"],
            espn_player=me.display_name, **score_fields, **model_fields)

    @staticmethod
    def _qualified_identity(match: EspnMatch, competitor) -> tuple[str, str | None]:
        raw_match = str(match.competition_id or "")
        prefix = "lt" if raw_match.startswith("lt:") else "espn"
        match_id = raw_match if raw_match.startswith(prefix + ":") \
            else prefix + ":" + raw_match
        raw_athlete = str(competitor.athlete_id or "")
        if not raw_athlete:
            athlete_id = None
        else:
            athlete_prefix = prefix + ":athlete:"
            athlete_id = raw_athlete if raw_athlete.startswith(athlete_prefix) \
                else athlete_prefix + raw_athlete
        return match_id, athlete_id

    def _identity_fields(self, match: EspnMatch, me, opp) -> dict:
        match_id, athlete_id = self._qualified_identity(match, me)
        opponent_match_id, opponent_id = self._qualified_identity(match, opp)
        if opponent_match_id != match_id:
            raise ValueError("score competitor match identity mismatch")
        timestamp = getattr(match, "score_timestamp", None)
        timestamp_value = None
        if timestamp is not None:
            try:
                timestamp_value = Decimal(str(timestamp))
            except Exception:
                timestamp_value = None
            if timestamp_value is not None and not timestamp_value.is_finite():
                timestamp_value = None
        return {
            "score_source": (
                "live_tennis" if match_id.startswith("lt:") else "espn"),
            "score_match_id": match_id,
            "score_athlete_id": athlete_id,
            "score_opponent_id": opponent_id,
            "score_player_name": me.display_name,
            "score_opponent_name": opp.display_name,
            "score_timestamp": timestamp_value,
            "score_lifecycle_state": match.state,
            "score_observed": bool(getattr(match, "score_observed", False)),
            "score_best_of": int(match.best_of),
        }

    def _score_fresh(self, match: EspnMatch) -> bool:
        timestamp = getattr(match, "score_timestamp", None)
        if match.state != "in":
            return True
        if timestamp is None:
            return False
        try:
            now = float(self.clock())
            score_time = float(timestamp)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(now) or not math.isfinite(score_time):
            return False
        age = now - score_time
        return 0.0 <= age <= self.max_score_age_s

    def _prematch_after_start(self, match: EspnMatch,
                              scheduled_start_ts) -> bool:
        if match.state != "pre" or scheduled_start_ts is None:
            return False
        try:
            now = Decimal(str(self.clock()))
            scheduled = Decimal(str(scheduled_start_ts))
        except Exception:
            return True
        return (not now.is_finite() or not scheduled.is_finite()
                or now >= scheduled)

    def _lifecycle_advances(self, match: EspnMatch) -> bool:
        """Reject provider lifecycle rewinds within one gate/session."""
        match_id, _athlete_id = self._qualified_identity(
            match, match.competitors[0])
        rank = {"pre": 0, "in": 1, "post": 2}.get(match.state)
        if rank is None:
            return False
        previous = self._lifecycle_by_match.get(match_id)
        if previous is not None and rank < previous:
            return False
        self._lifecycle_by_match[match_id] = rank
        return True

    def _score_advances(self, score_fields: dict) -> bool:
        """Apply the replay-shared score transition rule and pin on success."""
        key = tuple(score_fields[field] for field in (
            "score_match_id", "score_athlete_id", "score_opponent_id"))
        current = (
            score_fields.get("score_timestamp"),
            score_fields["score_lifecycle_state"],
            score_fields["score_sets_for"],
            score_fields["score_sets_against"],
            score_fields["score_games_for"],
            score_fields["score_games_against"],
        )
        previous = self._score_by_orientation.get(key)
        if not score_transition_advances(previous, current):
            return False
        self._score_by_orientation[key] = current
        return True

    @staticmethod
    def _score_state(match: EspnMatch, me, opp) -> tuple[int, int, int, int]:
        live = match.state == "in"
        if live and not bool(getattr(match, "score_observed", False)):
            raise ValueError("live score was not observed")
        sets_me, sets_opp = completed_sets_won(
            me.sets, opp.sets, live=live)
        authoritative = int(me.sets_won), int(opp.sets_won)
        sets_needed = int(match.best_of) // 2 + 1
        if (any(value < 0 or value > sets_needed for value in authoritative)
                or sum(authoritative) > int(match.best_of)
                or authoritative == (sets_needed, sets_needed)):
            raise ValueError("invalid authoritative set score")
        if (sum(authoritative) == sets_me + sets_opp
                and sum(authoritative) > 0
                and authoritative != (sets_me, sets_opp)):
            raise ValueError("inconsistent authoritative set orientation")
        # A provider may publish the match set score before its per-set game
        # arrays. Prefer that authoritative progress when it contains more
        # completed sets than can be reconstructed from the sparse arrays.
        if sum(authoritative) > sets_me + sets_opp:
            if authoritative[0] < sets_me or authoritative[1] < sets_opp:
                raise ValueError("inconsistent authoritative set score")
            sets_me, sets_opp = authoritative
        if (live and max(sets_me, sets_opp) >= sets_needed) or (
                match.state == "pre" and (sets_me or sets_opp)):
            raise ValueError("set score conflicts with match lifecycle")
        g_me, g_opp = current_games(me.sets, opp.sets, live=live)
        if (match.games is not None and (g_me, g_opp) == (0, 0) and live
                and not set_complete(*match.games)):
            if match.competitors[0] is me:
                g_me, g_opp = match.games
            elif match.competitors[1] is me:
                g_opp, g_me = match.games
            else:
                # Never infer orientation from optional IDs: two missing IDs
                # compare equal and silently reverse player-two scores.
                raise ValueError("score competitor orientation is ambiguous")
        return sets_me, sets_opp, g_me, g_opp

    @staticmethod
    def _utc_text(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z")

    @staticmethod
    def _aware_datetime(value) -> datetime | None:
        if (not isinstance(value, datetime) or value.tzinfo is None
                or value.utcoffset() is None):
            return None
        return value.astimezone(timezone.utc)

    @staticmethod
    def _epoch_decimal(value: datetime) -> Decimal:
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = value - epoch
        return (Decimal(delta.days * 86400 + delta.seconds)
                + Decimal(delta.microseconds) / Decimal(1_000_000))

    def _schedule_matches(self, prior: _PrematchModels,
                          scheduled_start_ts) -> bool:
        if scheduled_start_ts is None:
            return True
        try:
            expected = Decimal(str(scheduled_start_ts))
            parsed = datetime.fromisoformat(
                prior.match_start.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return False
        if not expected.is_finite():
            return False
        parsed = self._aware_datetime(parsed)
        return parsed is not None and self._epoch_decimal(parsed) == expected

    def _prior_is_fresh(self, prior: _PrematchModels) -> bool:
        """Pinned baselines remain immutable but still age out exactly."""
        try:
            observed = Decimal(str(self.clock()))
            generated = datetime.fromisoformat(
                prior.generated_at.replace("Z", "+00:00"))
            generated = self._aware_datetime(generated)
            maximum = Decimal(str(self.cfg.two_model_prior_max_age_s))
        except (AttributeError, TypeError, ValueError):
            return False
        if (generated is None or not observed.is_finite()
                or not maximum.is_finite() or maximum <= 0):
            return False
        age = observed - self._epoch_decimal(generated)
        return Decimal(0) <= age <= maximum

    def _prematch_priors(self, match: EspnMatch, me, opp,
                         *, scheduled_start_ts=None) -> _PrematchModels | None:
        provider = self.prematch_prior_provider
        if provider is None:
            return None
        competition_id, athlete_id = self._qualified_identity(match, me)
        opponent_competition_id, opponent_id = self._qualified_identity(
            match, opp)
        if (competition_id != opponent_competition_id or athlete_id is None
                or opponent_id is None or athlete_id == opponent_id):
            return None
        key = competition_id, athlete_id, opponent_id
        pinned = self._pinned_priors.get(key)
        if pinned is not None:
            return pinned if (
                self._schedule_matches(pinned, scheduled_start_ts)
                and self._prior_is_fresh(pinned)) else None
        try:
            value = provider(
                competition_id=competition_id,
                athlete_id=athlete_id,
                opponent_athlete_id=opponent_id,
                player_name=me.display_name,
                opponent_name=opp.display_name,
            )
        except Exception:  # A missing/broken prior cannot authorize edge.
            return None
        if value is None:
            return None
        expected_identity = {
            "competition_id": competition_id,
            "athlete_id": athlete_id,
            "opponent_athlete_id": opponent_id,
            "player_name": me.display_name,
            "opponent_name": opp.display_name,
        }
        if any(getattr(value, field, None) != expected
               for field, expected in expected_identity.items()):
            return None
        provenance = getattr(value, "provenance", None)
        raw = (getattr(value, "model_1_probability", None),
               getattr(value, "model_2_probability", None))
        try:
            probabilities = tuple(Decimal(str(item)) for item in raw)
        except Exception:
            return None
        if (len(probabilities) != 2
                or any(not item.is_finite() or not Decimal(0) < item < Decimal(1)
                       for item in probabilities)):
            return None
        generated_at = self._aware_datetime(
            getattr(provenance, "generated_at", None))
        model_as_of = self._aware_datetime(
            getattr(value, "model_as_of", None))
        match_start = self._aware_datetime(
            getattr(value, "match_start", None))
        if (generated_at is None or model_as_of is None or match_start is None
                or not model_as_of <= generated_at <= match_start):
            return None
        source_sha256 = getattr(provenance, "source_sha256", None)
        model_1_id = getattr(provenance, "model_1_id", None)
        model_2_id = getattr(provenance, "model_2_id", None)
        if (not isinstance(source_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
                or not isinstance(model_1_id, str) or not model_1_id
                or not isinstance(model_2_id, str) or not model_2_id
                or model_1_id == model_2_id):
            return None
        parsed = _PrematchModels(
            model_1=probabilities[0],
            model_2=probabilities[1],
            source_sha256=source_sha256,
            generated_at=self._utc_text(generated_at),
            model_1_id=model_1_id,
            model_2_id=model_2_id,
            model_as_of=self._utc_text(model_as_of),
            match_start=self._utc_text(match_start),
        )
        if not self._schedule_matches(parsed, scheduled_start_ts):
            return None
        if not self._prior_is_fresh(parsed):
            return None
        self._pinned_priors[key] = parsed
        return parsed

    def _bind(self, player_name: str, event_title: str,
              matches: tuple[EspnMatch, ...]):
        player_name = player_name or ""
        event_title = event_title or ""
        event_tokens = name_tokens(event_title)
        candidates = []
        seen = set()
        for match in matches:
            if match.state not in ("pre", "in", "post"):
                continue
            for index, me in enumerate(match.competitors):
                if not names_match(player_name, me.display_name):
                    continue
                opp = match.competitors[1 - index]
                me_last = normalize_name(me.display_name).split()[-1]
                opp_last = normalize_name(opp.display_name).split()[-1]
                # Both sides must be confirmed. The old OR allowed the target's
                # own surname alone to "confirm" an unrelated same-name card.
                if not event_tokens or not {
                        me_last, opp_last}.issubset(event_tokens):
                    continue
                if not _identity_mentioned(opp.display_name, event_title):
                    continue
                identity = (
                    match.competition_id,
                    me.athlete_id or normalize_name(me.display_name),
                    opp.athlete_id or normalize_name(opp.display_name),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                candidates.append((match, me, opp))
        if not candidates:
            return None
        # Never guess between two plausible scoreboard cards.
        if len(candidates) != 1:
            return None
        return candidates[0]
