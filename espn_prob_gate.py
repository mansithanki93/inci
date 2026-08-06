"""Fail-closed score + win-prob gate for v6 paper entries.

An entry is allowed only when:
1. the Kalshi contract binds to a scoreboard match by player name
   (ESPN ATP/WTA, plus Live Tennis ITF/challenger when configured),
2. the match is live (or pre, with neutral score),
3. score-model P(win) clears ``espn_min_model_prob``,
4. model edge vs market ask clears ``espn_min_edge``.

Unbound markets are blocked — price dips alone cannot authorize a buy.
Live Tennis is polled only after ESPN bind fails for ITF-marked tickers,
so free-tier API quota is not burned on ATP/WTA sessions.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from espn_tennis import EspnMatch, EspnScoreboardCache
from live_tennis import LiveTennisCache, resolve_api_key
from tennis_win_prob import (
    completed_sets_won,
    current_games,
    match_win_probability,
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
    """Last-name forward match with optional first initial/name overlap."""
    a = name_tokens(player)
    b = name_tokens(candidate)
    if not a or not b:
        return False
    if normalize_name(player) == normalize_name(candidate):
        return True
    # Require shared last token (surname) and not conflicting.
    if not (a & b):
        return False
    last_a = normalize_name(player).split()[-1]
    last_b = normalize_name(candidate).split()[-1]
    if last_a != last_b:
        # Allow multi-part surnames sharing any long token (>=4).
        shared = {t for t in (a & b) if len(t) >= 4}
        if not shared:
            return False
    return True


@dataclass(frozen=True)
class GateDecision:
    allow: bool
    reason: str
    model_prob: Decimal | None = None
    market_prob: Decimal | None = None
    edge: Decimal | None = None
    espn_match_id: str | None = None
    espn_player: str | None = None


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
    ):
        self.cfg = config
        self.cache = cache or EspnScoreboardCache(
            leagues=tuple(getattr(config, "espn_leagues", ("atp", "wta"))),
            ttl_s=float(getattr(config, "espn_cache_s", 15.0)),
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
        return bound

    def is_bound(self, *, ticker: str, player_name: str,
                 event_title: str) -> bool:
        """True when a live/pre scoreboard card binds (no ask/edge checks)."""
        return self.find_bind(
            ticker=ticker, player_name=player_name,
            event_title=event_title) is not None

    def model_edge_score(self, *, ticker: str, player_name: str,
                         event_title: str, ask_cents) -> tuple:
        """Sibling-ranking score: ``(bound, edge)`` — higher is better.

        Unbound / unscored contracts get ``(0, 0)`` so a bindable sibling
        with any edge wins. Used only for discovery one-per-event picks.
        """
        bound = self.find_bind(
            ticker=ticker, player_name=player_name,
            event_title=event_title)
        if bound is None:
            return (0, Decimal(0))
        match, me, opp = bound
        try:
            ask = Decimal(str(ask_cents))
        except Exception:
            return (1, Decimal(0))
        if ask <= 0 or ask >= 100:
            return (1, Decimal(0))
        live = match.state == "in"
        sets_me, sets_opp = completed_sets_won(
            me.sets, opp.sets, live=live)
        g_me, g_opp = current_games(me.sets, opp.sets, live=live)
        if match.games is not None and (g_me, g_opp) == (0, 0) and live:
            if match.competitors[0].display_name == me.display_name:
                g_me, g_opp = match.games
            else:
                g_opp, g_me = match.games
        model = match_win_probability(
            sets_me, sets_opp, g_me, g_opp, best_of=match.best_of)
        edge = Decimal(str(round(model, 6))) - (ask / Decimal(100))
        return (1, edge)

    def decide(self, *, ticker: str, player_name: str, event_title: str,
               ask_cents) -> GateDecision:
        if not self.enabled():
            return GateDecision(True, "espn_gate_disabled")
        try:
            ask = Decimal(str(ask_cents))
        except Exception:
            return GateDecision(False, "blocked:invalid_ask")
        if ask <= 0 or ask >= 100:
            return GateDecision(False, "blocked:invalid_ask")
        market_prob = ask / Decimal(100)

        player_name = display_player_name(player_name)
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
        if match.state == "post":
            return GateDecision(
                False, "blocked:match_over", espn_match_id=match.competition_id,
                espn_player=me.display_name)
        live = match.state == "in"
        sets_me, sets_opp = completed_sets_won(
            me.sets, opp.sets, live=live)
        # Prefer paired linescore games; fall back to match.games ordered by
        # competitor order in ESPN payload (me may be either side).
        g_me, g_opp = current_games(me.sets, opp.sets, live=live)
        if match.games is not None and (g_me, g_opp) == (0, 0) and live:
            # match.games follows competitors tuple order.
            if match.competitors[0].display_name == me.display_name:
                g_me, g_opp = match.games
            else:
                g_opp, g_me = match.games

        model = match_win_probability(
            sets_me, sets_opp, g_me, g_opp, best_of=match.best_of)
        model_p = Decimal(str(round(model, 6)))
        min_p = Decimal(str(self.cfg.espn_min_model_prob))
        min_edge = Decimal(str(self.cfg.espn_min_edge))
        edge = model_p - market_prob
        source = ("live_tennis" if str(match.competition_id).startswith("lt:")
                  else "espn")
        if model_p < min_p:
            return GateDecision(
                False,
                f"blocked:model_prob {model_p:.3f}<{min_p} "
                f"score {sets_me}-{sets_opp} games {g_me}-{g_opp} "
                f"({match.detail})",
                model_prob=model_p, market_prob=market_prob, edge=edge,
                espn_match_id=match.competition_id,
                espn_player=me.display_name)
        if edge < min_edge:
            return GateDecision(
                False,
                f"blocked:edge {edge:+.3f}<{min_edge} "
                f"(model {model_p:.3f} vs ask {market_prob:.3f})",
                model_prob=model_p, market_prob=market_prob, edge=edge,
                espn_match_id=match.competition_id,
                espn_player=me.display_name)
        return GateDecision(
            True,
            f"{source}_ok model {model_p:.3f} edge {edge:+.3f} "
            f"score {sets_me}-{sets_opp} ({match.detail})",
            model_prob=model_p, market_prob=market_prob, edge=edge,
            espn_match_id=match.competition_id,
            espn_player=me.display_name)

    def _bind(self, player_name: str, event_title: str,
              matches: tuple[EspnMatch, ...]):
        player_name = player_name or ""
        event_title = event_title or ""
        # Prefer matches where both event names appear when possible.
        event_tokens = name_tokens(event_title)
        candidates = []
        for match in matches:
            if match.state not in ("pre", "in"):
                continue
            for me in match.competitors:
                if not names_match(player_name, me.display_name):
                    continue
                opp = (match.competitors[0]
                       if match.competitors[1].display_name == me.display_name
                       else match.competitors[1])
                # Soft event confirmation: opponent surname in event title.
                opp_last = normalize_name(opp.display_name).split()[-1]
                confirmed = (not event_tokens) or (opp_last in event_tokens) or (
                    normalize_name(me.display_name).split()[-1] in event_tokens)
                score = (2 if confirmed else 1, 1 if match.state == "in" else 0)
                candidates.append((score, match, me, opp))
        if not candidates:
            return None
        candidates.sort(key=lambda row: row[0], reverse=True)
        # Require at least soft confirmation when event title exists.
        best = candidates[0]
        if event_tokens and best[0][0] < 2:
            return None
        _, match, me, opp = best
        return match, me, opp
