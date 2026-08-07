"""Replay logged ticks through the paper decision/pending-fill path used by
the running bot. Logged observations supply an immutable virtual timestamp;
no blocking sleep or parallel fill simulator exists."""
import csv
import json
import math
import os
from copy import deepcopy
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from config import Config
from strategy import ScalpStrategy
from executor import Executor
from engine import Context, process_tick
from safety import Safety
from fees import fee_usd
from research_log import (
    TICK_HEADER, TRADE_HEADER, config_fingerprint, code_fingerprint,
)
from sports_discovery import ContractProvenance
from tennis_win_prob import (
    match_win_probability,
    match_win_probability_from_prematch,
    score_transition_advances,
    set_complete,
)


class VirtualClock:
    def __init__(self, t0=0.0):
        self.t = t0

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


@dataclass(frozen=True)
class LoggedQuote:
    """Validated quote plus replay-only role and entry-decision evidence."""
    ts: float
    ticker: str
    mid: Decimal
    bid: Decimal
    ask: Decimal
    bid_qty: Decimal
    ask_qty: Decimal
    close_ts: float
    can_close_early: bool
    market_role: str
    sweep_id: int
    quote_phase: str
    decision_at: float
    score_gate: dict | None
    siblings: dict | None

    def _legacy(self):
        return (
            self.ts, self.ticker, self.mid, self.bid, self.ask,
            self.bid_qty, self.ask_qty, self.close_ts,
            self.can_close_early,
        )

    def __iter__(self):
        return iter(self._legacy())

    def __len__(self):
        return len(self._legacy())

    def __getitem__(self, index):
        return self._legacy()[index]


@dataclass(frozen=True)
class LoggedTrade:
    """Strictly validated fill row from the sibling trades_v6 file."""
    ts: float
    ticker: str
    side: str
    price: Decimal
    contracts: Decimal
    fee_usd: Decimal
    reason: str


class ReplayFeed:
    """Only exposes books that the replay driver has already applied."""
    def __init__(self, clock, provenance_by_ticker=None,
                 trade_tickers=()):
        self.clock = clock
        self.history = defaultdict(lambda: deque(maxlen=600))
        self.books = {}
        self.close_times = {}
        self.can_close_early = {}
        self.provenance_by_ticker = dict(provenance_by_ticker or {})
        self.trade_tickers = frozenset(trade_tickers)
        self.watch_tickers = frozenset(
            set(self.provenance_by_ticker) - self.trade_tickers)

    def apply(self, ts, ticker, mid, bid, ask, bid_qty, ask_qty,
              close_ts=None, can_close_early=None):
        self.books[ticker] = (bid, bid_qty, ask, ask_qty)
        self.history[ticker].append((ts, mid))
        if close_ts is not None:
            self.close_times[ticker] = close_ts
        if can_close_early is not None:
            self.can_close_early[ticker] = can_close_early

    def top_of_book(self, ticker):
        return self.books.get(ticker, (None, None, None, None))

    def lifecycle(self, ticker):
        return (self.close_times.get(ticker),
                self.can_close_early.get(ticker))

    def entry_allowed(self, ticker, now, required_seconds):
        close_ts = self.close_times.get(ticker)
        return (True if close_ts is None else
                now + float(required_seconds) < close_ts)

    def early_close_risk(self, ticker):
        return self.can_close_early.get(ticker, False)

    def sibling_tickers(self, ticker):
        try:
            event = self.provenance_by_ticker[ticker].event_ticker
        except KeyError:
            return ()
        return tuple(
            other for other, provenance in self.provenance_by_ticker.items()
            if other != ticker and provenance.event_ticker == event)

    def mid_rise_in_lookback(self, ticker, now, lookback_s):
        window = [
            (ts, mid) for ts, mid in self.history.get(ticker, ())
            if now - float(lookback_s) <= float(ts) <= float(now)
        ]
        if len(window) < 2:
            return Decimal(0)
        return max(Decimal(0), window[-1][1] - min(mid for _, mid in window))


def validate_logged_book(mid, bid, ask, bid_qty=None, ask_qty=None):
    """Validate one normalized executable book before research can use it."""
    prices = {"mid": mid, "bid": bid, "ask": ask}
    for name, value in prices.items():
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
            prices[name] = value
        if not value.is_finite() or not Decimal(0) <= value <= Decimal(100):
            raise ValueError(f"invalid {name}: {value}")
    mid, bid, ask = prices["mid"], prices["bid"], prices["ask"]
    if bid > ask:
        raise ValueError(f"crossed book: bid {bid} > ask {ask}")
    if mid != (bid + ask) / Decimal(2):
        raise ValueError(
            f"mid {mid} does not equal arithmetic book midpoint")
    depths = []
    for name, value in (("bid_qty", bid_qty), ("ask_qty", ask_qty)):
        if value is None:
            depths.append(None)
            continue
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
        if not parsed.is_finite() or parsed < 0:
            raise ValueError(f"invalid {name}: {parsed}")
        depths.append(parsed)
    return mid, bid, ask, depths[0], depths[1]


def _utc_day(timestamp):
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError(f"invalid timestamp: {timestamp}") from error


def _legacy_error(header, raw_rows):
    fingerprint = None
    if "code_fingerprint" in header:
        index = header.index("code_fingerprint")
        for row in raw_rows:
            if index < len(row) and row[index]:
                fingerprint = row[index]
                break
    if "event_id" in header or any(row and row[0] == "5" for row in raw_rows):
        suffix = (f"; logged code fingerprint: {fingerprint}"
                  if fingerprint else "")
        return ValueError(
            "strict v6 replay rejects this v5 log; use archived v5 code"
            + suffix)
    return ValueError(
        "strict v6 replay requires the exact v6 tick header; use archived "
        "code for legacy research logs")


def _selected_sports(raw):
    try:
        parsed = json.loads(raw)
    except Exception as error:
        raise ValueError("selected_sports must be compact JSON") from error
    if (not isinstance(parsed, list) or not parsed
            or any(not isinstance(sport, str) or not sport
                   for sport in parsed)
            or len(set(parsed)) != len(parsed)):
        raise ValueError(
            "selected_sports must be a unique nonempty-string JSON array")
    if json.dumps(parsed, separators=(",", ":")) != raw:
        raise ValueError(
            "selected_sports must use exact compact JSON serialization")
    return tuple(parsed)


def _market_scope(raw):
    """Parse the immutable session-level trade/watch ticker manifest."""
    try:
        parsed = json.loads(raw)
    except Exception as error:
        raise ValueError("market_scope must be compact JSON") from error
    if (not isinstance(parsed, dict)
            or set(parsed) not in (
                {"trade", "watch"},
                {"trade", "watch", "score_bindings"})):
        raise ValueError(
            "market_scope must contain trade/watch arrays and optional "
            "score bindings")
    normalized = {}
    for role in ("trade", "watch"):
        values = parsed[role]
        if (not isinstance(values, list)
                or any(not isinstance(value, str) or not value
                       for value in values)
                or len(set(values)) != len(values)
                or values != sorted(values)):
            raise ValueError(
                f"market_scope {role} must be a sorted unique ticker array")
        normalized[role] = frozenset(values)
    if normalized["trade"] & normalized["watch"]:
        raise ValueError("market_scope trade/watch arrays overlap")
    raw_bindings = parsed.get("score_bindings", {})
    if not isinstance(raw_bindings, dict):
        raise ValueError("market_scope score_bindings must be an object")
    bindings = {}
    for ticker, value in raw_bindings.items():
        if (ticker not in normalized["trade"] | normalized["watch"]
                or not isinstance(value, list)
                or len(value) not in (3, 5)
                or any(not isinstance(item, str) or not item
                       for item in value)):
            raise ValueError("invalid market_scope score binding")
        binding = tuple(value)
        competition_id, athlete_id, opponent_id = binding[:3]
        source = "lt:" if competition_id.startswith("lt:") else "espn:"
        athlete_prefix = source + "athlete:"
        if (competition_id == source
                or not competition_id.startswith(source)
                or athlete_id == athlete_prefix
                or opponent_id == athlete_prefix
                or not athlete_id.startswith(athlete_prefix)
                or not opponent_id.startswith(athlete_prefix)
                or athlete_id == opponent_id
                or (len(binding) == 5 and binding[3] == binding[4])):
            raise ValueError("invalid market_scope score binding")
        bindings[ticker] = binding
    normalized["score_bindings"] = bindings
    if json.dumps(parsed, sort_keys=True, separators=(",", ":")) != raw:
        raise ValueError("market_scope must use exact compact serialization")
    return normalized


def _observation_detail(raw):
    """Validate the exact decision envelope written by the live sweep."""
    try:
        payload = json.loads(raw)
    except Exception as error:
        raise ValueError(
            "quote detail lacks replayable decision evidence") from error
    if not isinstance(payload, dict):
        raise ValueError("quote detail must be a decision object")
    role = payload.get("market_role")
    sweep_id = payload.get("sweep_id")
    quote_phase = payload.get("quote_phase")
    decision_at = payload.get("decision_at")
    if role not in ("trade", "watch"):
        raise ValueError("quote market_role must be trade or watch")
    if (isinstance(sweep_id, bool) or not isinstance(sweep_id, int)
            or sweep_id <= 0):
        raise ValueError("quote sweep_id must be a positive integer")
    if quote_phase not in ("evidence", "execution"):
        raise ValueError("quote_phase must be evidence or execution")
    if role == "watch" and quote_phase != "evidence":
        raise ValueError("watch quote cannot use execution phase")
    if (isinstance(decision_at, bool)
            or not isinstance(decision_at, (int, float))
            or not math.isfinite(decision_at) or decision_at < 0):
        raise ValueError("quote decision_at must be finite and nonnegative")
    base_fields = {
        "market_role", "sweep_id", "quote_phase", "decision_at",
    }
    if quote_phase == "evidence":
        if set(payload) != base_fields:
            raise ValueError("evidence quote contains trade decision evidence")
        return role, sweep_id, quote_phase, decision_at, None, None
    if role != "trade":
        raise ValueError("only trade quotes can use execution phase")
    if set(payload) != {
            *base_fields, "score_gate", "siblings"}:
        raise ValueError(
            "trade execution lacks exact entry decision evidence")

    gate = payload["score_gate"]
    if not isinstance(gate, dict):
        raise ValueError("score_gate evidence must be an object")
    if (not isinstance(gate.get("enabled"), bool)
            or not isinstance(gate.get("allow"), bool)
            or not isinstance(gate.get("reason"), str)
            or not gate["reason"]):
        raise ValueError("invalid score_gate decision evidence")
    allowed_gate_fields = {
        "enabled", "allow", "reason", "model_prob", "market_prob",
        "edge", "espn_match_id", "espn_player", "model_1_prob",
        "model_2_prob", "prior_source_sha256", "prior_generated_at",
        "prior_model_1_id", "prior_model_2_id", "score_source",
        "score_match_id", "score_athlete_id", "score_opponent_id",
        "score_player_name", "score_opponent_name",
        "score_timestamp", "score_lifecycle_state", "score_observed",
        "score_best_of", "score_sets_for", "score_sets_against",
        "score_games_for", "score_games_against",
        "prematch_model_1_prob", "prematch_model_2_prob",
        "prior_model_as_of", "prior_match_start", "gate_observed_at",
    }
    if not set(gate) <= allowed_gate_fields:
        raise ValueError("unknown score_gate decision evidence")
    for field in (
            "model_prob", "market_prob", "model_1_prob", "model_2_prob",
            "prematch_model_1_prob", "prematch_model_2_prob"):
        value = gate.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value:
            raise ValueError(f"invalid {field}")
        try:
            parsed = Decimal(value)
        except Exception as error:
            raise ValueError(f"invalid {field}") from error
        if not parsed.is_finite() or not Decimal(0) <= parsed <= Decimal(1):
            raise ValueError(f"invalid {field}")
    for timestamp_field in ("score_timestamp", "gate_observed_at"):
        timestamp_value = gate.get(timestamp_field)
        if timestamp_value is None:
            continue
        if not isinstance(timestamp_value, str) or not timestamp_value:
            raise ValueError(f"invalid {timestamp_field}")
        try:
            parsed_timestamp = Decimal(timestamp_value)
        except Exception as error:
            raise ValueError(f"invalid {timestamp_field}") from error
        if not parsed_timestamp.is_finite() or parsed_timestamp < 0:
            raise ValueError(f"invalid {timestamp_field}")
    for name_field in ("score_player_name", "score_opponent_name"):
        name_value = gate.get(name_field)
        if name_value is not None and (
                not isinstance(name_value, str) or not name_value
                or name_value != name_value.strip()):
            raise ValueError(f"invalid {name_field}")
    edge = gate.get("edge")
    if edge is not None:
        if not isinstance(edge, str) or not edge:
            raise ValueError("invalid edge")
        try:
            parsed_edge = Decimal(edge)
        except Exception as error:
            raise ValueError("invalid edge") from error
        if (not parsed_edge.is_finite()
                or not Decimal(-1) <= parsed_edge <= Decimal(1)):
            raise ValueError("invalid edge")
    for field in (
            "espn_match_id", "espn_player", "prior_model_1_id",
            "prior_model_2_id", "score_match_id", "score_athlete_id",
            "score_opponent_id"):
        value = gate.get(field)
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError(f"invalid {field}")
    source = gate.get("score_source")
    if source is not None and source not in ("espn", "live_tennis"):
        raise ValueError("invalid score_source")
    lifecycle = gate.get("score_lifecycle_state")
    if lifecycle is not None and lifecycle not in ("pre", "in", "post"):
        raise ValueError("invalid score_lifecycle_state")
    observed = gate.get("score_observed")
    if observed is not None and not isinstance(observed, bool):
        raise ValueError("invalid score_observed")
    best_of = gate.get("score_best_of")
    if best_of is not None and (
            isinstance(best_of, bool) or best_of not in (3, 5)):
        raise ValueError("invalid score_best_of")
    for field in (
            "score_sets_for", "score_sets_against",
            "score_games_for", "score_games_against"):
        value = gate.get(field)
        if value is not None and (
                isinstance(value, bool) or not isinstance(value, int)
                or value < 0):
            raise ValueError(f"invalid {field}")
    digest = gate.get("prior_source_sha256")
    if digest is not None and (
            not isinstance(digest, str) or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)):
        raise ValueError("invalid prior_source_sha256")
    for field in (
            "prior_generated_at", "prior_model_as_of",
            "prior_match_start"):
        value = gate.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError(f"invalid {field}")
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"invalid {field}") from error
    provenance_fields = (
        "prior_source_sha256", "prior_generated_at",
        "prior_model_1_id", "prior_model_2_id", "prior_model_as_of",
        "prior_match_start", "prematch_model_1_prob",
        "prematch_model_2_prob",
    )
    present_provenance = [gate.get(field) is not None
                          for field in provenance_fields]
    if any(present_provenance) and not all(present_provenance):
        raise ValueError("incomplete prior provenance")
    if any(present_provenance) and (
            gate.get("model_1_prob") is None
            or gate.get("model_2_prob") is None):
        raise ValueError("prior provenance requires both model probabilities")
    score_core_fields = (
        "score_source", "score_match_id", "score_athlete_id",
        "score_opponent_id", "score_lifecycle_state", "score_observed",
        "score_best_of", "score_sets_for", "score_sets_against",
        "score_games_for", "score_games_against",
    )
    score_core_present = [gate.get(field) is not None
                          for field in score_core_fields]
    if any(score_core_present) and not all(score_core_present):
        raise ValueError("incomplete structured score evidence")
    if all(score_core_present):
        expected_prefix = (
            "lt:" if source == "live_tennis" else "espn:")
        if (not gate["score_match_id"].startswith(expected_prefix)
                or gate["score_match_id"] == expected_prefix):
            raise ValueError("score_match_id lacks provider qualification")
        athlete_prefix = expected_prefix + "athlete:"
        if (not gate["score_athlete_id"].startswith(athlete_prefix)
                or not gate["score_opponent_id"].startswith(athlete_prefix)
                or gate["score_athlete_id"] == athlete_prefix
                or gate["score_opponent_id"] == athlete_prefix
                or gate["score_athlete_id"] == gate["score_opponent_id"]):
            raise ValueError("invalid provider-qualified score athlete ids")
        if (gate.get("espn_match_id") is not None
                and gate["espn_match_id"] != gate["score_match_id"]):
            raise ValueError("score match identity contradicts gate identity")
        if (gate.get("score_player_name") is not None
                and gate.get("espn_player") is not None
                and gate["score_player_name"] != gate["espn_player"]):
            raise ValueError(
                "score orientation drift: player name contradicts gate "
                "identity")
        if lifecycle == "in" and (
                observed is not True
                or gate.get("score_timestamp") is None):
            raise ValueError("live score evidence must be observed and timed")

    siblings = payload["siblings"]
    if not isinstance(siblings, dict):
        raise ValueError("sibling evidence must be an object")
    if not set(siblings) <= {"enabled", "complete", "rises", "error"}:
        raise ValueError("unknown sibling decision evidence")
    if (not isinstance(siblings.get("enabled"), bool)
            or not isinstance(siblings.get("complete"), bool)
            or not isinstance(siblings.get("rises"), list)):
        raise ValueError("invalid sibling decision evidence")
    if not siblings["complete"] and not isinstance(
            siblings.get("error"), str):
        raise ValueError("incomplete sibling evidence requires an error")
    rises = []
    seen = set()
    for item in siblings["rises"]:
        if (not isinstance(item, list) or len(item) != 2
                or not isinstance(item[0], str) or not item[0]
                or item[0] in seen):
            raise ValueError("invalid sibling rise evidence")
        try:
            rise = Decimal(str(item[1]))
        except Exception as error:
            raise ValueError("invalid sibling rise") from error
        if not rise.is_finite() or rise < 0:
            raise ValueError("invalid sibling rise")
        seen.add(item[0])
        rises.append((item[0], str(rise)))
    normalized_siblings = dict(siblings)
    normalized_siblings["rises"] = tuple(rises)
    return (role, sweep_id, quote_phase, decision_at,
            dict(gate), normalized_siblings)


def _utc_z_timestamp(value, field):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ValueError(f"invalid {field}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"invalid {field}")
    return parsed.astimezone(timezone.utc).timestamp()


def _rounded_probability(value):
    return Decimal(str(round(value, 6)))


def _validate_prior_timeline(gate, gate_observed_at, scheduled_start_ts):
    """Validate the immutable prior cutoff chain and return generated_at."""
    if gate.get("prior_source_sha256") is None:
        return None
    if gate_observed_at is None:
        raise ValueError("prematch prior lacks gate_observed_at")
    model_as_of = _utc_z_timestamp(
        gate["prior_model_as_of"], "prior_model_as_of")
    generated_at = _utc_z_timestamp(
        gate["prior_generated_at"], "prior_generated_at")
    match_start = _utc_z_timestamp(
        gate["prior_match_start"], "prior_match_start")
    if (model_as_of > generated_at
            or Decimal(str(generated_at)) > Decimal(str(gate_observed_at))
            or generated_at > match_start
            or match_start != float(scheduled_start_ts)):
        raise ValueError(
            "prematch prior chronology/cutoff contradicts scheduled start")
    return Decimal(str(generated_at))


def _validate_score_model(gate, cfg, row, scheduled_start_ts):
    """Return the one canonical allow/deny derived from durable evidence."""
    score_fields = (
        "score_source", "score_match_id", "score_athlete_id",
        "score_opponent_id", "score_lifecycle_state", "score_observed",
        "score_best_of", "score_sets_for", "score_sets_against",
        "score_games_for", "score_games_against",
    )
    if any(gate.get(field) is None for field in score_fields):
        raise ValueError("score_gate lacks complete structured score evidence")
    if gate.get("gate_observed_at") is None:
        raise ValueError("structured score evidence lacks gate_observed_at")
    gate_observed_at = Decimal(gate["gate_observed_at"])
    execution_at = Decimal(str(row.ts))
    decision_at = Decimal(str(row.decision_at))
    if not gate_observed_at <= execution_at <= decision_at:
        raise ValueError(
            "score gate chronology must satisfy gate_observed_at <= "
            "execution ts <= decision_at")
    generated_at = _validate_prior_timeline(
        gate, gate_observed_at, scheduled_start_ts)

    state = gate["score_lifecycle_state"]
    best_of = gate["score_best_of"]
    sets_for = gate["score_sets_for"]
    sets_against = gate["score_sets_against"]
    games_for = gate["score_games_for"]
    games_against = gate["score_games_against"]
    sets_needed = best_of // 2 + 1
    canonical_allow = state != "post"
    if (sets_for > sets_needed or sets_against > sets_needed
            or (sets_for == sets_needed and sets_against == sets_needed)):
        raise ValueError("invalid structured tennis score state")
    if state != "post" and (
            sets_for >= sets_needed or sets_against >= sets_needed
            or games_for > 7 or games_against > 7
            or ((games_for or games_against)
                and set_complete(games_for, games_against))):
        raise ValueError("invalid structured tennis score state")
    if state == "post" and not (
            sets_for == sets_needed or sets_against == sets_needed):
        raise ValueError("completed lifecycle lacks a match winner")
    if state == "pre":
        if any((sets_for, sets_against, games_for, games_against)):
            raise ValueError("prematch score state must be zero")
        if gate["score_observed"] is not False:
            raise ValueError("prematch score must be explicitly unobserved")
        if gate.get("score_timestamp") is not None:
            raise ValueError("prematch score cannot carry a score timestamp")
        if decision_at >= Decimal(str(scheduled_start_ts)):
            canonical_allow = False

    if state == "in":
        if gate["score_observed"] is not True:
            raise ValueError("live score must be explicitly observed")
        score_timestamp = Decimal(gate["score_timestamp"])
        if score_timestamp > gate_observed_at:
            raise ValueError(
                "live score_timestamp follows gate_observed_at")
        score_age = decision_at - score_timestamp
        maximum_age = Decimal(str(getattr(
            cfg, "score_provider_max_age_s",
            max(45.0, float(cfg.espn_cache_s) * 3.0))))
        if score_age < 0:
            raise ValueError("score timestamp follows decision_at")
        if score_age > maximum_age:
            canonical_allow = False

    if state == "post":
        # A completed score is sufficient to derive a canonical denial.  Some
        # providers stop publishing model fields once the match is terminal.
        return False

    prematch_one = gate.get("prematch_model_1_prob")
    prematch_two = gate.get("prematch_model_2_prob")
    updated_one = gate.get("model_1_prob")
    updated_two = gate.get("model_2_prob")
    if (prematch_one is None) != (prematch_two is None):
        raise ValueError("incomplete raw prematch model probabilities")
    if prematch_one is None:
        if updated_one is not None or updated_two is not None:
            raise ValueError("guard-only score cannot claim updated models")
        expected = _rounded_probability(match_win_probability(
            sets_for, sets_against, games_for, games_against,
            best_of=best_of))
        if Decimal(gate["model_prob"]) != expected:
            raise ValueError("guard-only model_prob contradicts score state")
        if gate.get("edge") is not None:
            raise ValueError("guard-only score cannot claim a market edge")
        canonical_allow = (
            canonical_allow
            and not bool(getattr(cfg, "two_model_prior_path", ""))
            and expected >= Decimal(str(cfg.espn_min_model_prob)))
        return canonical_allow

    raw_prematch = Decimal(prematch_one), Decimal(prematch_two)
    if any(not Decimal(0) < probability < Decimal(1)
           for probability in raw_prematch):
        raise ValueError(
            "raw prematch model probability must be strictly between 0 and 1")

    if updated_one is None or updated_two is None:
        raise ValueError("prematch models require both score revisions")
    expected_models = tuple(_rounded_probability(
        match_win_probability_from_prematch(
            float(Decimal(prior)), sets_for, sets_against,
            games_for, games_against, best_of=best_of))
        for prior in raw_prematch)
    logged_models = Decimal(updated_one), Decimal(updated_two)
    if logged_models != expected_models:
        raise ValueError("score-updated model probabilities do not recompute")
    if Decimal(gate["model_prob"]) != min(expected_models):
        raise ValueError("score_gate model_prob is not conservative model")

    market_prob = Decimal(gate["market_prob"])
    expected_edge = min(expected_models) - market_prob
    if gate.get("edge") is None or Decimal(gate["edge"]) != expected_edge:
        raise ValueError("score_gate edge does not recompute")
    prior_age = decision_at - generated_at
    if prior_age < 0:
        raise ValueError("prematch prior is from the future")
    canonical_allow = (
        canonical_allow
        and prior_age <= Decimal(str(cfg.two_model_prior_max_age_s))
        and min(expected_models) >= Decimal(str(cfg.espn_min_model_prob))
        and expected_edge >= Decimal(str(cfg.espn_min_edge)))
    return canonical_allow


def _validate_decision_evidence(
        rows, cfg, provenance_by_ticker, trade_tickers, watch_tickers,
        discovery_score_bindings):
    """Cross-check recorded decisions against books and immutable scope.

    The envelope is an audit record, not an oracle: replay independently
    derives the market probability and sibling movement from the logged
    quotes before it is allowed to execute a trade row.
    """
    histories = defaultdict(list)
    score_identity_by_ticker = {}
    lifecycle_by_score = {}
    score_state_by_identity = {}
    prior_baseline_by_ticker = {}
    index = 0
    while index < len(rows):
        sweep_id = rows[index].sweep_id
        end = index
        while end < len(rows) and rows[end].sweep_id == sweep_id:
            end += 1
        sweep = rows[index:end]
        executions = [
            row for row in sweep if row.quote_phase == "execution"]
        trade_evidence = [
            row for row in sweep
            if row.market_role == "trade" and row.quote_phase == "evidence"]
        watch_evidence = [
            row for row in sweep
            if row.market_role == "watch" and row.quote_phase == "evidence"]
        if executions:
            if len(executions) != 1 or len(trade_evidence) != 1:
                raise ValueError(
                    f"sweep {sweep_id} is not an exact one-trade runtime "
                    "package")
            execution = executions[0]
            if trade_evidence[0].ticker != execution.ticker:
                raise ValueError(
                    f"sweep {sweep_id} execution lacks its exact trade "
                    "evidence")
            event = provenance_by_ticker[execution.ticker].event_ticker
            expected_watch = {
                ticker for ticker in watch_tickers
                if provenance_by_ticker[ticker].event_ticker == event
            }
            actual_watch = {row.ticker for row in watch_evidence}
            if not actual_watch <= expected_watch:
                raise ValueError(
                    f"sweep {sweep_id} contains watch evidence outside the "
                    "same-event watch manifest")
            if actual_watch != expected_watch:
                siblings = execution.siblings
                if (not siblings.get("enabled")
                        or siblings.get("complete")
                        or siblings.get("rises")
                        or not siblings.get("error")):
                    raise ValueError(
                        f"sweep {sweep_id} missing watch evidence without "
                        "an explicit incomplete sibling denial")
            if len(sweep) != 2 + len(actual_watch):
                raise ValueError(
                    f"sweep {sweep_id} contains extra runtime package rows")
        elif not (
                len(sweep) == 1 and len(watch_evidence) == 1
                and not trade_evidence):
            raise ValueError(
                f"sweep {sweep_id} must be one trade package or one "
                "evidence-only watch quote")

        observed = {row.ticker for row in sweep}
        for row in sweep:
            histories[row.ticker].append((row.ts, row.mid))

        for row in sweep:
            if (row.ticker not in trade_tickers
                    or row.quote_phase != "execution"):
                continue
            decision_at = row.decision_at
            gate = row.score_gate
            expected_gate_enabled = bool(cfg.espn_gate_enabled)
            if gate["enabled"] != expected_gate_enabled:
                raise ValueError(
                    f"score_gate enabled state contradicts config for "
                    f"{row.ticker!r}")
            market_prob = (
                None if gate.get("market_prob") is None
                else Decimal(gate["market_prob"]))
            model_prob = (
                None if gate.get("model_prob") is None
                else Decimal(gate["model_prob"]))
            edge = (None if gate.get("edge") is None
                    else Decimal(gate["edge"]))
            if market_prob is not None and market_prob != row.ask / Decimal(100):
                raise ValueError(
                    f"score_gate market_prob contradicts logged ask for "
                    f"{row.ticker!r}")
            if edge is not None:
                if model_prob is None or market_prob is None:
                    raise ValueError(
                        "score_gate edge requires model_prob and market_prob")
                if edge != model_prob - market_prob:
                    raise ValueError(
                        "score_gate edge contradicts model and market values")
            model_one = gate.get("model_1_prob")
            model_two = gate.get("model_2_prob")
            if (model_one is None) != (model_two is None):
                raise ValueError(
                    "score_gate must include both model probabilities")
            if model_one is not None and model_prob != min(
                    Decimal(model_one), Decimal(model_two)):
                raise ValueError(
                    "score_gate model_prob must be conservative model minimum")
            if not gate["enabled"]:
                if (not gate["allow"]
                        or gate["reason"] != "score_gate_disabled"
                        or set(gate) != {"enabled", "allow", "reason"}):
                    raise ValueError("invalid disabled score_gate evidence")
            else:
                score_fields = (
                    "score_source", "score_match_id", "score_athlete_id",
                    "score_opponent_id", "score_lifecycle_state",
                    "score_observed", "score_best_of", "score_sets_for",
                    "score_sets_against", "score_games_for",
                    "score_games_against",
                )
                score_complete = all(
                    gate.get(field) is not None for field in score_fields)
                gate_observed = gate.get("gate_observed_at")
                if gate_observed is not None and Decimal(
                        gate_observed) > Decimal(str(row.ts)):
                    raise ValueError(
                        "gate_observed_at follows execution quote timestamp")
                _validate_prior_timeline(
                    gate, gate_observed,
                    provenance_by_ticker[
                        row.ticker].scheduled_start_ts)
                if not score_complete:
                    if gate["allow"]:
                        raise ValueError(
                            "incomplete external score denial can never allow")
                else:
                    state = gate["score_lifecycle_state"]
                    if state != "post" and (
                            model_prob is None or market_prob is None):
                        raise ValueError(
                            "structured score gate requires model and market "
                            "probabilities")
                    score_identity = tuple(gate[field] for field in (
                        "score_source", "score_match_id",
                        "score_athlete_id", "score_opponent_id"))
                    expected_binding = discovery_score_bindings.get(
                        row.ticker)
                    actual_binding = (
                        gate["score_match_id"], gate["score_athlete_id"],
                        gate["score_opponent_id"],
                        gate.get("score_player_name"),
                        gate.get("score_opponent_name"))
                    if expected_binding is None:
                        if gate["allow"]:
                            raise ValueError(
                                "allowed score gate lacks immutable discovery "
                                f"binding for {row.ticker!r}")
                    else:
                        comparable = (actual_binding[:3]
                                      if len(expected_binding) == 3
                                      else actual_binding)
                        if tuple(expected_binding) != comparable:
                            raise ValueError(
                                f"score gate contradicts discovery binding "
                                f"for {row.ticker!r}")
                        if (gate.get("prior_source_sha256") is not None
                                and (len(expected_binding) != 5
                                     or any(value is None
                                            for value in actual_binding[3:]))):
                            raise ValueError(
                                "two-model prior lacks exact discovery player "
                                "lookup identity")
                    score_key = (row.ticker, *score_identity)
                    score_state = (
                        None if gate.get("score_timestamp") is None else
                        Decimal(gate["score_timestamp"]),
                        state,
                        gate["score_sets_for"], gate["score_sets_against"],
                        gate["score_games_for"], gate["score_games_against"],
                    )
                    previous_score = score_state_by_identity.get(score_key)
                    if not score_transition_advances(
                            previous_score, score_state):
                        raise ValueError(
                            "score lifecycle/progress rewind for "
                            f"{row.ticker!r}")
                    score_state_by_identity[score_key] = score_state
                    canonical_allow = _validate_score_model(
                        gate, cfg, row,
                        provenance_by_ticker[
                            row.ticker].scheduled_start_ts)
                    if gate["allow"] != canonical_allow:
                        raise ValueError(
                            "logged score_gate allow contradicts canonical "
                            f"{'allow' if canonical_allow else 'deny'}")

                    prior_identity = score_identity + tuple(gate.get(field)
                        for field in (
                            "score_player_name", "score_opponent_name",
                            "prior_source_sha256", "prior_generated_at",
                            "prior_model_1_id", "prior_model_2_id",
                            "prematch_model_1_prob",
                            "prematch_model_2_prob", "prior_model_as_of",
                            "prior_match_start"))
                    prior_score = score_identity_by_ticker.setdefault(
                        row.ticker, score_identity)
                    if prior_score != score_identity:
                        raise ValueError(
                            f"score orientation drift for {row.ticker!r}")
                    prior_baseline = prior_baseline_by_ticker.setdefault(
                        row.ticker, prior_identity)
                    if prior_baseline != prior_identity:
                        raise ValueError(
                            f"prematch prior baseline drift for {row.ticker!r}")
                    lifecycle_key = (row.ticker, *score_identity)
                    lifecycle_rank = {"pre": 0, "in": 1, "post": 2}[state]
                    prior_lifecycle = lifecycle_by_score.setdefault(
                        lifecycle_key, lifecycle_rank)
                    if lifecycle_rank < prior_lifecycle:
                        raise ValueError(
                            f"score lifecycle rewind for {row.ticker!r}")
                    lifecycle_by_score[lifecycle_key] = lifecycle_rank

                    if gate.get("prior_source_sha256") is not None:
                        if gate["prior_model_1_id"] == gate["prior_model_2_id"]:
                            raise ValueError(
                                "two-model prior requires independent model ids")

            siblings = row.siblings
            expected_sibling_enabled = bool(cfg.sibling_spike_enabled)
            if siblings["enabled"] != expected_sibling_enabled:
                raise ValueError(
                    f"sibling evidence enabled state contradicts config for "
                    f"{row.ticker!r}")
            rises = dict(siblings["rises"])
            if not siblings["enabled"]:
                if (not siblings["complete"] or rises
                        or siblings.get("error") is not None):
                    raise ValueError("invalid disabled sibling evidence")
                continue

            event = provenance_by_ticker[row.ticker].event_ticker
            expected = {
                ticker for ticker in watch_tickers
                if provenance_by_ticker[ticker].event_ticker == event
            }
            complete = bool(expected) and expected <= observed
            if siblings["complete"] != complete:
                raise ValueError(
                    "sibling evidence completeness contradicts same-sweep "
                    "watch coverage")
            if not complete:
                if rises or not siblings.get("error"):
                    raise ValueError(
                        "incomplete sibling evidence must contain only error")
                continue
            if set(rises) != expected:
                raise ValueError(
                    "sibling rise names contradict same-event watch manifest")
            lookback = float(cfg.sibling_spike_lookback_s)
            for ticker in expected:
                window = [
                    (ts, mid) for ts, mid in histories[ticker]
                    if decision_at - lookback <= ts <= decision_at
                ]
                expected_rise = Decimal(0)
                if len(window) >= 2:
                    expected_rise = max(
                        Decimal(0), window[-1][1]
                        - min(mid for _ts, mid in window))
                if Decimal(rises[ticker]) != expected_rise:
                    raise ValueError(
                        f"sibling rise for {ticker!r} contradicts logged "
                        "watch quotes")
        index = end


def _decimal_field(row, field, *, minimum=None):
    raw = row[field]
    if raw == "":
        raise ValueError(f"{field} is required")
    try:
        parsed = Decimal(raw)
    except Exception as error:
        raise ValueError(f"invalid {field}: {raw!r}") from error
    if not parsed.is_finite() or (minimum is not None and parsed < minimum):
        raise ValueError(f"invalid {field}: {raw!r}")
    return parsed


def _timestamp_field(row, field):
    raw = row[field]
    try:
        parsed = float(raw)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"invalid {field}: {raw!r}") from error
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"invalid {field}: {raw!r}")
    _utc_day(parsed)
    return parsed


def load_log(path, tickers=None, include_metadata=False, cfg=None,
             expected_code_fingerprint=None, include_watch=False):
    """Strictly validate a complete v6 file before applying ticker filters."""
    selected_filter = None
    if tickers is not None:
        if isinstance(tickers, (str, bytes)):
            raise ValueError("tickers filter must be a collection")
        selected_filter = frozenset(tickers)
        if any(not isinstance(ticker, str) or not ticker
               for ticker in selected_filter):
            raise ValueError(
                "tickers filter must contain nonempty strings")

    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError("strict v6 replay requires a CSV header") from error
        raw_rows = list(reader)
    if header != TICK_HEADER:
        raise _legacy_error(header, raw_rows)
    for number, values in enumerate(raw_rows, start=2):
        if len(values) != len(TICK_HEADER):
            raise ValueError(
                f"v6 row {number} has {len(values)} columns; "
                f"expected {len(TICK_HEADER)}")
    if not raw_rows:
        raise ValueError(
            "strict v6 replay requires session metadata rows")

    normalized_rows = []
    gap_tickers = []
    provenance_by_ticker = {}
    provenance_by_event = {}
    terminal_status = "missing"
    terminal_reason = None
    terminal_seen = False
    session_id = None
    starting_pnl = None
    starting_day = None
    logged_config = None
    logged_code = None
    logged_selected_text = None
    logged_selected_sports = None
    logged_scope_text = None
    logged_scope = None
    last_timestamp = None
    last_sweep_id = None
    sweep_quote_keys = set()
    sweep_decision_at = None
    sweep_seen_execution = False
    sweep_evidence_tickers = set()
    role_by_ticker = {}
    seen_rows = set()

    for number, values in enumerate(raw_rows, start=2):
        row_identity = tuple(values)
        if row_identity in seen_rows:
            raise ValueError(f"duplicate v6 row at line {number}")
        seen_rows.add(row_identity)
        row = dict(zip(TICK_HEADER, values))
        if row["schema_version"] != "6":
            if row["schema_version"] == "5":
                raise _legacy_error(TICK_HEADER, [values])
            raise ValueError(
                f"schema_version mismatch on row {number}: "
                f"{row['schema_version']!r}; strict v6 requires '6'")
        if terminal_seen:
            raise ValueError("row appears after session terminal record")
        if not row["session_id"]:
            raise ValueError("session_id is required")
        parsed_start = _decimal_field(
            row, "starting_daily_pnl_usd")
        timestamp = _timestamp_field(row, "ts")
        if last_timestamp is not None and timestamp < last_timestamp:
            raise ValueError("non-monotonic observation timestamps")
        last_timestamp = timestamp
        if not row["starting_utc_day"]:
            raise ValueError("starting_utc_day is required")
        try:
            datetime.strptime(row["starting_utc_day"], "%Y-%m-%d")
        except ValueError as error:
            raise ValueError(
                f"invalid starting_utc_day "
                f"{row['starting_utc_day']!r}") from error
        actual_day = _utc_day(timestamp)
        if row["utc_day"] != actual_day:
            raise ValueError(
                f"utc_day {row['utc_day']!r} disagrees with timestamp "
                f"day {actual_day!r}")
        if row["utc_day"] < row["starting_utc_day"]:
            raise ValueError("row precedes process-session start day")
        if not row["config_fingerprint"]:
            raise ValueError("config_fingerprint is required")
        if not row["code_fingerprint"]:
            raise ValueError("code_fingerprint is required")
        selected_sports = _selected_sports(row["selected_sports"])
        market_scope = _market_scope(row["market_scope"])
        if session_id is None:
            session_id = row["session_id"]
            starting_pnl = parsed_start
            starting_day = row["starting_utc_day"]
            logged_config = row["config_fingerprint"]
            logged_code = row["code_fingerprint"]
            logged_selected_text = row["selected_sports"]
            logged_selected_sports = selected_sports
            logged_scope_text = row["market_scope"]
            logged_scope = market_scope
        elif (
                row["session_id"] != session_id
                or parsed_start != starting_pnl
                or row["starting_utc_day"] != starting_day
                or row["config_fingerprint"] != logged_config
                or row["code_fingerprint"] != logged_code):
            raise ValueError(
                "replay refuses mixed/inconsistent session metadata")
        elif row["selected_sports"] != logged_selected_text:
            raise ValueError(
                "selected_sports changed within one session")
        elif row["market_scope"] != logged_scope_text:
            raise ValueError("market_scope changed within one session")

        event = row["event"]
        if event in ("session_end", "session_halt"):
            if terminal_seen:
                raise ValueError("multiple session terminal records")
            forbidden = (
                "ticker", "sport", "league", "series_ticker",
                "milestone_id", "event_ticker", "scheduled_start_ts",
                "close_ts", "can_close_early", "mid", "bid", "ask",
                "bid_qty", "ask_qty",
            )
            if any(row[field] != "" for field in forbidden):
                raise ValueError(
                    "session terminal record must be fully unscoped")
            if not row["detail"].strip():
                raise ValueError(
                    "session terminal record lacks a reason")
            if (sweep_decision_at is not None
                    and timestamp < sweep_decision_at):
                raise ValueError(
                    "session terminal timestamp precedes final decision")
            terminal_seen = True
            terminal_status = (
                "clean" if event == "session_end" else "halted")
            terminal_reason = row["detail"]
            continue

        ticker = row["ticker"]
        if not ticker:
            raise ValueError(
                "tickerless nonterminal row is forbidden")
        if not event:
            raise ValueError("event must be a nonempty string")
        for field in (
                "sport", "series_ticker", "milestone_id",
                "event_ticker", "scheduled_start_ts"):
            if row[field] == "":
                raise ValueError(f"{field} is required")
        if row["sport"] not in selected_sports:
            raise ValueError(
                f"provenance Sport {row['sport']!r} is not selected")
        scheduled_start = _timestamp_field(
            row, "scheduled_start_ts")
        try:
            provenance = ContractProvenance(
                sport=row["sport"],
                league=(None if row["league"] == "" else row["league"]),
                series_ticker=row["series_ticker"],
                milestone_id=row["milestone_id"],
                event_ticker=row["event_ticker"],
                scheduled_start_ts=scheduled_start)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"invalid provenance for {ticker!r}: {error}") from error
        prior = provenance_by_ticker.setdefault(ticker, provenance)
        if prior != provenance:
            raise ValueError(
                f"ticker {ticker!r} has drifting provenance")
        game_provenance = (
            provenance.sport, provenance.league,
            provenance.series_ticker, provenance.milestone_id,
            provenance.scheduled_start_ts,
        )
        prior_game = provenance_by_event.setdefault(
            provenance.event_ticker, game_provenance)
        if prior_game != game_provenance:
            raise ValueError(
                f"event {provenance.event_ticker!r} has conflicting "
                "game provenance")

        lifecycle_book_fields = (
            "close_ts", "can_close_early", "mid", "bid", "ask",
            "bid_qty", "ask_qty",
        )
        if event != "quote":
            if any(row[field] != "" for field in lifecycle_book_fields):
                raise ValueError(
                    "ticker-bearing nonquote row must have blank "
                    "lifecycle/book fields")
            gap_tickers.append(ticker)
            continue
        if any(row[field] == "" for field in lifecycle_book_fields):
            raise ValueError(
                "malformed quote row has missing lifecycle/book field")
        close_ts = _timestamp_field(row, "close_ts")
        if row["can_close_early"] not in ("true", "false"):
            raise ValueError(
                "can_close_early must be 'true' or 'false'")
        parsed = validate_logged_book(
            _decimal_field(row, "mid"),
            _decimal_field(row, "bid"),
            _decimal_field(row, "ask"),
            _decimal_field(row, "bid_qty", minimum=Decimal(0)),
            _decimal_field(row, "ask_qty", minimum=Decimal(0)))
        role, sweep_id, quote_phase, decision_at, gate, siblings = \
            _observation_detail(row["detail"])
        expected_role = (
            "trade" if ticker in logged_scope["trade"] else
            "watch" if ticker in logged_scope["watch"] else None)
        if role != expected_role:
            raise ValueError(
                f"market_role for {ticker!r} contradicts session manifest")
        if last_sweep_id is not None and sweep_id < last_sweep_id:
            raise ValueError("non-monotonic quote sweep_id")
        if sweep_id != last_sweep_id:
            if (sweep_decision_at is not None
                    and timestamp < sweep_decision_at):
                raise ValueError(
                    "new quote sweep starts before the prior decision")
            sweep_quote_keys = set()
            sweep_decision_at = None
            sweep_seen_execution = False
            sweep_evidence_tickers = set()
            last_sweep_id = sweep_id
        quote_key = (ticker, quote_phase)
        if quote_key in sweep_quote_keys:
            raise ValueError(
                f"duplicate ticker/phase {quote_key!r} in quote sweep "
                f"{sweep_id}")
        if decision_at < timestamp:
            raise ValueError("decision_at precedes its quote timestamp")
        if sweep_decision_at is None:
            sweep_decision_at = decision_at
        elif sweep_decision_at != decision_at:
            raise ValueError(
                f"decision_at drift within sweep {sweep_id}")
        if quote_phase == "evidence" and sweep_seen_execution:
            raise ValueError(
                f"evidence quote follows execution phase in sweep "
                f"{sweep_id}")
        if quote_phase == "evidence":
            sweep_evidence_tickers.add(ticker)
        else:
            if ticker not in sweep_evidence_tickers:
                raise ValueError(
                    f"execution quote for {ticker!r} lacks same-sweep "
                    "evidence")
            sweep_seen_execution = True
        sweep_quote_keys.add(quote_key)
        prior_role = role_by_ticker.setdefault(ticker, role)
        if prior_role != role:
            raise ValueError(f"market_role drift for {ticker!r}")
        normalized_rows.append(LoggedQuote(
            timestamp, ticker, *parsed, close_ts,
            row["can_close_early"] == "true", role, sweep_id,
            quote_phase, decision_at, gate, siblings))

    base_config = deepcopy(cfg if cfg is not None else Config())
    base_config.sports = list(logged_selected_sports)
    base_config.validate()
    expected_config = config_fingerprint(base_config)
    expected_code = (
        expected_code_fingerprint
        if expected_code_fingerprint is not None
        else code_fingerprint())
    if logged_config != expected_config:
        raise ValueError("research config fingerprint mismatch")
    if logged_code != expected_code:
        raise ValueError("research code fingerprint mismatch")

    manifest_tickers = logged_scope["trade"] | logged_scope["watch"]
    if manifest_tickers != set(provenance_by_ticker):
        raise ValueError(
            "market_scope must exactly match session market provenance")
    _validate_decision_evidence(
        normalized_rows, base_config, provenance_by_ticker,
        logged_scope["trade"], logged_scope["watch"],
        logged_scope["score_bindings"])
    # A risk-reducing execution package may proceed without a required watch,
    # but it is still incomplete research evidence. Count the explicit
    # fail-closed sibling denial as a data gap even if a crafted log omits the
    # provider-error row that the runtime normally records.
    gap_tickers.extend(
        row.ticker for row in normalized_rows
        if (row.quote_phase == "execution"
            and row.siblings.get("enabled")
            and not row.siblings.get("complete")))

    trade_names = set(logged_scope["trade"])
    if selected_filter is None:
        selected_trade = trade_names
    else:
        unknown = selected_filter - trade_names
        if unknown:
            raise ValueError(
                "ticker filter contains watch-only or unknown market(s): "
                + ",".join(sorted(unknown)))
        selected_trade = set(selected_filter)
    selected_events = {
        provenance_by_ticker[ticker].event_ticker
        for ticker in selected_trade
    }
    selected_names = set(selected_trade)
    if selected_filter is None:
        # A ticker can fail before its first successful quote, so it has no
        # durable trade/watch role envelope yet.  It is still an in-scope data
        # gap and must disqualify an otherwise empty replay.
        selected_names.update(gap_tickers)
    if include_watch:
        selected_names.update(
            ticker for ticker, provenance in provenance_by_ticker.items()
            if ticker in logged_scope["watch"]
            and provenance.event_ticker in selected_events)
    rows = [row for row in normalized_rows if row.ticker in selected_names]
    gaps = sum(ticker in selected_names for ticker in gap_tickers)
    selected_provenance = {
        ticker: provenance
        for ticker, provenance in provenance_by_ticker.items()
        if ticker in selected_names
    }
    result = (rows, gaps)
    if include_metadata:
        return result + (
            starting_pnl, starting_day, terminal_status, terminal_reason,
            logged_selected_sports, selected_provenance,
        )
    return result


def load_rows(path):
    return load_log(path)[0]


def _trade_path_for_ticks(path):
    directory, name = os.path.split(os.fspath(path))
    if not name.startswith("ticks_v6_") or not name.endswith(".csv"):
        raise ValueError(
            "strict v6 replay requires a canonical ticks_v6 filename so its "
            "sibling trades_v6 file is unambiguous")
    return os.path.join(directory, "trades_v6_" + name[len("ticks_v6_"):])


def _tick_audit_context(path):
    """Read already-validated tick identity for sibling trade validation."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise ValueError("strict v6 replay requires tick session metadata")
    first = rows[0]
    metadata_fields = (
        "schema_version", "session_id", "starting_daily_pnl_usd",
        "starting_utc_day", "config_fingerprint", "code_fingerprint",
        "selected_sports", "market_scope",
    )
    metadata = {field: first[field] for field in metadata_fields}
    provenance_fields = (
        "sport", "league", "series_ticker", "milestone_id",
        "event_ticker", "scheduled_start_ts",
    )
    provenance = {}
    terminal_ts = None
    for row in rows:
        if row["event"] in ("session_end", "session_halt"):
            terminal_ts = float(row["ts"])
            continue
        ticker = row["ticker"]
        current = tuple(row[field] for field in provenance_fields)
        prior = provenance.setdefault(ticker, current)
        if prior != current:
            # load_log has already rejected this; retaining the assertion keeps
            # the trade validator independently fail closed.
            raise ValueError(f"ticker {ticker!r} has drifting provenance")
    return metadata, provenance, terminal_ts


def _load_trade_log(tick_path):
    """Validate the complete sibling fill ledger before replaying any quote."""
    metadata, provenance, terminal_ts = _tick_audit_context(tick_path)
    trade_path = _trade_path_for_ticks(tick_path)
    try:
        with open(trade_path, newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration as error:
                raise ValueError(
                    "strict v6 replay requires a trade CSV header") from error
            raw_rows = list(reader)
    except FileNotFoundError as error:
        raise ValueError(
            f"strict v6 replay requires sibling trade log {trade_path!r}") \
            from error
    if header != TRADE_HEADER:
        raise ValueError("strict v6 replay requires the exact v6 trade header")

    scope = _market_scope(metadata["market_scope"])
    provenance_fields = (
        "sport", "league", "series_ticker", "milestone_id",
        "event_ticker", "scheduled_start_ts",
    )
    metadata_fields = (
        "schema_version", "session_id", "starting_daily_pnl_usd",
        "starting_utc_day", "config_fingerprint", "code_fingerprint",
        "selected_sports", "market_scope",
    )
    records = []
    seen = set()
    last_ts = None
    for number, values in enumerate(raw_rows, start=2):
        if len(values) != len(TRADE_HEADER):
            raise ValueError(
                f"v6 trade row {number} has {len(values)} columns; "
                f"expected {len(TRADE_HEADER)}")
        identity = tuple(values)
        if identity in seen:
            raise ValueError(f"duplicate v6 trade row at line {number}")
        seen.add(identity)
        row = dict(zip(TRADE_HEADER, values))
        for field in metadata_fields:
            if row[field] != metadata[field]:
                raise ValueError(
                    f"trade row {number} has inconsistent {field}")
        ts = _timestamp_field(row, "ts")
        if last_ts is not None and ts < last_ts:
            raise ValueError("non-monotonic trade timestamps")
        last_ts = ts
        if terminal_ts is not None and ts > terminal_ts:
            raise ValueError("trade timestamp follows session terminal")
        if row["utc_day"] != _utc_day(ts):
            raise ValueError("trade utc_day contradicts timestamp")
        if row["utc_day"] < row["starting_utc_day"]:
            raise ValueError("trade row precedes process-session start day")
        ticker = row["ticker"]
        if not ticker or ticker not in scope["trade"]:
            raise ValueError(
                "trade row ticker is watch-only, unknown, or empty")
        expected_provenance = provenance.get(ticker)
        actual_provenance = tuple(row[field] for field in provenance_fields)
        if expected_provenance is None or actual_provenance != expected_provenance:
            raise ValueError(
                f"trade provenance contradicts ticks for {ticker!r}")
        if row["side"] not in ("BUY", "SELL"):
            raise ValueError("trade side must be BUY or SELL")
        price = _decimal_field(row, "price", minimum=Decimal(0))
        if price > Decimal(100):
            raise ValueError("trade price exceeds 100 cents")
        contracts = _decimal_field(
            row, "contracts", minimum=Decimal(0))
        if contracts <= 0:
            raise ValueError("trade contracts must be positive")
        trade_fee = _decimal_field(row, "fee_usd", minimum=Decimal(0))
        if not row["reason"]:
            raise ValueError("trade reason must be a nonempty string")
        records.append(LoggedTrade(
            ts, ticker, row["side"], price, contracts, trade_fee,
            row["reason"]))
    return records


class _ReplayFillAudit:
    """Minimal ResearchLog-compatible sink for reconstructed paper fills."""
    def __init__(self):
        self.records = []

    def trade(self, ticker, side, price, contracts, reason, *, fee, ts):
        self.records.append(LoggedTrade(
            float(ts), ticker, side, Decimal(str(price)),
            Decimal(str(contracts)), Decimal(str(fee)), reason))


def _compare_trade_logs(expected, actual):
    if len(expected) != len(actual):
        raise ValueError(
            "reconstructed fills do not match trade ledger: "
            f"expected {len(expected)}, reconstructed {len(actual)}")
    for index, (logged, reconstructed) in enumerate(
            zip(expected, actual), start=1):
        if logged != reconstructed:
            raise ValueError(
                "reconstructed fill contradicts trade ledger at row "
                f"{index}: logged={logged!r}, "
                f"reconstructed={reconstructed!r}")


def value_residual(position, bid, bid_qty, cfg):
    """Conservative liquidation value for replay-only residual inventory."""
    contracts = Decimal(str(position.contracts))
    available = (Decimal(0) if bid_qty is None
                 else max(Decimal(0), Decimal(str(bid_qty))))
    executable = min(contracts, available) if bid is not None else Decimal(0)
    exit_price = (max(Decimal(0), Decimal(str(bid))
                      - Decimal(str(cfg.sim_slippage_cents)))
                  if bid is not None else Decimal(0))
    proceeds = exit_price * executable / Decimal(100)
    exit_fee = (fee_usd(
        exit_price, executable, side="SELL",
        balance_precision_usd=cfg.balance_precision_usd)
        if executable else Decimal(0))
    cost = (Decimal(str(position.entry_price)) * contracts / Decimal(100)
            + Decimal(str(position.entry_fee_usd)))
    return {
        "contracts": contracts,
        "executable_contracts": executable,
        "unpriced_contracts": contracts - executable,
        "exit_price": exit_price,
        "marked_pnl": proceeds - exit_fee - cost,
    }


def replay(path, tickers=None, cfg=None, verbose=False):
    """Run the paper bot over a tick log through the exact engine path.
    Returns a dict: realized (Decimal), residual_contracts, residual_marked
    (mark-to-last-bid net of fees for anything flatten could not close —
    counted so reported P&L cannot hide open inventory), trades."""
    cfg = deepcopy(cfg if cfg is not None else Config())
    all_rows, data_gaps, starting_pnl, starting_day, terminal_status, \
        terminal_reason, selected_sports, market_provenance = load_log(
            path, tickers=tickers, include_metadata=True, cfg=cfg,
            expected_code_fingerprint=code_fingerprint(),
            include_watch=True)
    logged_trades = _load_trade_log(path)
    cfg.sports = list(selected_sports)
    cfg.validate()
    cfg.paper_trading = True
    clock = VirtualClock()
    trade_tickers = tuple(dict.fromkeys(
        row.ticker for row in all_rows if row.market_role == "trade"))
    feed = ReplayFeed(
        clock, provenance_by_ticker=market_provenance,
        trade_tickers=trade_tickers)
    strat = ScalpStrategy(cfg)
    ex = Executor(cfg, client=None, feed=feed,
                  clock=clock.time, sleep=clock.sleep)
    safety = Safety(cfg)
    fill_audit = _ReplayFillAudit()
    ctx = Context(cfg, feed, strat, ex, log=fill_audit, safety=safety,
                  clock=clock.time)

    trades = []
    per_ticker_realized = defaultdict(lambda: Decimal(0))
    orig = strat.record_fill

    def counting(ticker, side, price, filled, fee, now=None):
        trades.append((ticker, side, price, filled))
        before = strat.realized_pnl
        orig(ticker, side, price, filled, fee, now=now)
        per_ticker_realized[ticker] += strat.realized_pnl - before
    strat.record_fill = counting

    import io, contextlib
    sink = None if verbose else io.StringIO()
    strat.realized_pnl = starting_pnl
    starting_realized = starting_pnl
    current_day = starting_day
    processed_count = 0
    session_realized = Decimal(0)
    with contextlib.redirect_stdout(sink) if sink else contextlib.nullcontext():
        index = 0
        while index < len(all_rows) and not safety.tripped:
            sweep_id = all_rows[index].sweep_id
            end = index
            while (end < len(all_rows)
                   and all_rows[end].sweep_id == sweep_id):
                end += 1
            sweep = all_rows[index:end]

            # Runtime collects the complete evidence phase and every trade
            # requote before it processes any decision.  Install the complete
            # sweep first so replay exposes the same contemporaneous books to
            # pending-order checks and sibling-history calculations.
            for row in sweep:
                row_day = _utc_day(row.ts)
                if current_day is None:
                    current_day = row_day
                elif row_day != current_day:
                    strat.realized_pnl = Decimal(0)
                    current_day = row_day
                clock.t = max(clock.t, row.ts)
                feed.apply(
                    row.ts, row.ticker, row.mid, row.bid, row.ask,
                    row.bid_qty, row.ask_qty, row.close_ts,
                    row.can_close_early)
                processed_count += 1

            # Evidence rows are audit/history only.  Only a post-evidence
            # trade requote is eligible to drive the paper engine.
            for row in sweep:
                if (row.market_role != "trade"
                        or row.quote_phase != "execution"):
                    continue
                decision_day = _utc_day(row.decision_at)
                if decision_day != current_day:
                    strat.realized_pnl = Decimal(0)
                    current_day = decision_day
                clock.t = max(clock.t, row.decision_at)
                before = strat.realized_pnl
                process_tick(
                    ctx, row.ticker, row.mid, row.bid, row.ask,
                    observed_at=row.ts, decision_at=row.decision_at,
                    gate_snapshot=row.score_gate,
                    sibling_snapshot=row.siblings,
                    log_observation=False, sweep_id=sweep_id)
                session_realized += strat.realized_pnl - before
                if safety.tripped:
                    break
            index = end
        pending_count = len(ex.pending_paper)
        # Never synthesize a post-EOF liquidation from the last cached book.
        # Open inventory is valued conservatively below and makes the replay
        # incomplete.
        # residual inventory: flatten retries may still leave contracts
        # (zero depth). Mark them at the last bid net of fees so the
        # reported result cannot silently exclude open inventory.
        residual_contracts = Decimal(0)
        residual_marked = Decimal(0)
        residuals = {}
        for t, pos in strat.positions.items():
            bid, bid_qty, _, _ = feed.top_of_book(t)
            detail = value_residual(pos, bid, bid_qty, cfg)
            residuals[t] = detail
            residual_contracts += detail["contracts"]
            residual_marked += detail["marked_pnl"]
    total_pnl = session_realized + residual_marked
    per_ticker_total = dict(per_ticker_realized)
    for ticker, detail in residuals.items():
        per_ticker_total[ticker] = (per_ticker_total.get(ticker, Decimal(0))
                                    + detail["marked_pnl"])
    selected_trade_set = set(trade_tickers)
    expected_trades = [
        trade for trade in logged_trades
        if trade.ticker in selected_trade_set]
    _compare_trade_logs(expected_trades, fill_audit.records)
    return {"realized": session_realized,
            "starting_daily_pnl": starting_realized,
            "ending_daily_pnl": strat.realized_pnl,
            "residual_contracts": residual_contracts,
            "residual_marked": residual_marked,
            "residuals": residuals,
            "total_pnl": total_pnl,
            "pending_orders": pending_count,
            "rows_processed": processed_count,
            "rows_available": len(all_rows),
            "data_gaps": data_gaps,
            "terminal_status": terminal_status,
            "terminal_reason": terminal_reason,
            "selected_sports": selected_sports,
            "market_provenance": dict(market_provenance),
            "trade_tickers": trade_tickers,
            "watch_tickers": tuple(sorted(feed.watch_tickers)),
            "halted": safety.tripped,
            "halt_reason": safety.tripped_reason,
            "evaluable": (len(all_rows) > 0 and not residual_contracts
                           and pending_count == 0 and data_gaps == 0
                           and terminal_status == "clean"
                           and not safety.tripped
                           and processed_count == len(all_rows)),
            "per_ticker_realized": dict(per_ticker_realized),
            "per_ticker_total": per_ticker_total,
            "trades": trades}
