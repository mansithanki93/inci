"""Focused fail-closed tests for durable replay decision evidence."""
import csv
from decimal import Decimal
import os
import tempfile
import unittest

from config import Config
from replay import LoggedTrade, _compare_trade_logs, load_log, replay
from research_log import (
    ResearchLog,
    config_fingerprint,
    observation_detail,
)
from sports_discovery import ContractProvenance
from tennis_win_prob import match_win_probability


def _provenance():
    return {
        ticker: ContractProvenance(
            sport="Tennis", league="ATP", series_ticker="KXATP",
            milestone_id="M", event_ticker="EVENT",
            scheduled_start_ts=10_000,
        )
        for ticker in ("TRADE", "WATCH")
    }


def _score_bindings(*tickers):
    out = {}
    for ticker in tickers:
        if ticker == "WATCH":
            out[ticker] = (
                "espn:M", "espn:athlete:two", "espn:athlete:one",
                "Player Two", "Player One")
        else:
            out[ticker] = (
                "espn:M", "espn:athlete:one", "espn:athlete:two",
                "Player One", "Player Two")
    return out


def _disabled_gate():
    return {
        "enabled": False, "allow": True,
        "reason": "score_gate_disabled",
    }


def _siblings(rise="0"):
    return {
        "enabled": True, "complete": True,
        "rises": (("WATCH", rise),),
    }


def _prematch_score_evidence():
    return {
        "score_source": "espn",
        "score_match_id": "espn:M",
        "score_athlete_id": "espn:athlete:one",
        "score_opponent_id": "espn:athlete:two",
        "score_player_name": "Player One",
        "score_opponent_name": "Player Two",
        "score_timestamp": None,
        "score_lifecycle_state": "pre",
        "score_observed": False,
        "score_best_of": 3,
        "score_sets_for": 0,
        "score_sets_against": 0,
        "score_games_for": 0,
        "score_games_against": 0,
        "prematch_model_1_prob": "0.82",
        "prematch_model_2_prob": "0.80",
        "prior_model_as_of": "1970-01-01T00:00:01Z",
        "prior_match_start": "1970-01-01T02:46:40Z",
        "gate_observed_at": "1.9",
    }


def _guard_gate(*, state="pre", gate_observed_at="1", allow=True,
                score_timestamp=None, athlete="one", opponent="two"):
    if state == "in" and score_timestamp is None:
        score_timestamp = gate_observed_at
    return {
        "enabled": True, "allow": allow,
        "reason": ("score_guard_only" if allow else "blocked:score_guard"),
        "model_prob": "0.5", "market_prob": "0.51", "edge": None,
        "espn_match_id": "espn:M", "espn_player": "Player One",
        "score_source": "espn", "score_match_id": "espn:M",
        "score_athlete_id": f"espn:athlete:{athlete}",
        "score_opponent_id": f"espn:athlete:{opponent}",
        "score_player_name": (
            "Player One" if athlete == "one" else "Player Two"),
        "score_opponent_name": (
            "Player Two" if opponent == "two" else "Player One"),
        "score_timestamp": score_timestamp,
        "score_lifecycle_state": state,
        "score_observed": state != "pre", "score_best_of": 3,
        "score_sets_for": 0, "score_sets_against": 0,
        "score_games_for": 0, "score_games_against": 0,
        "gate_observed_at": gate_observed_at,
    }


def _two_model_gate(*, allow=True, gate_observed_at="1.9", digest=None):
    return {
        "enabled": True, "allow": allow,
        "reason": "models_ok" if allow else "blocked:models",
        "model_prob": "0.80", "market_prob": "0.53", "edge": "0.27",
        "espn_match_id": "espn:M", "espn_player": "Player One",
        **_prematch_score_evidence(),
        "gate_observed_at": gate_observed_at,
        "model_1_prob": "0.82", "model_2_prob": "0.80",
        "prior_source_sha256": digest or "a" * 64,
        "prior_generated_at": "1970-01-01T00:00:01Z",
        "prior_model_1_id": "MODEL-A", "prior_model_2_id": "MODEL-B",
    }


class ReplayEvidenceIntegrityTests(unittest.TestCase):
    def _log(self, cfg, session):
        return ResearchLog(
            tempfile.mkdtemp(), session_id=session, session_start=0,
            config=cfg, provenance_by_ticker=_provenance(),
            trade_tickers=("TRADE",), watch_tickers=("WATCH",),
            score_bindings_by_ticker=_score_bindings("TRADE", "WATCH"),
        )

    @staticmethod
    def _quote(log, ticker, ts, mid, detail):
        log.tick(
            ticker, Decimal(mid), Decimal(mid) - 1, Decimal(mid) + 1,
            Decimal(100), Decimal(100), ts=ts, detail=detail,
            close_ts=10_000, can_close_early=False,
        )

    @staticmethod
    def _single_log(cfg, session, *, scheduled_start=10_000):
        provenance = ContractProvenance(
            sport="Tennis", league="ATP", series_ticker="KXATP",
            milestone_id="M", event_ticker="EVENT",
            scheduled_start_ts=scheduled_start)
        return ResearchLog(
            tempfile.mkdtemp(), session_id=session, session_start=0,
            config=cfg, provenance_by_ticker={"TRADE": provenance},
            trade_tickers=("TRADE",), watch_tickers=(),
            score_bindings_by_ticker=_score_bindings("TRADE"))

    def _single_package(self, log, gate, *, sweep=1, evidence_ts=1.8,
                        execution_ts=2, decision_at=2, mid=52):
        self._quote(
            log, "TRADE", evidence_ts, mid,
            observation_detail(
                "trade", sweep, quote_phase="evidence",
                decision_at=decision_at))
        self._quote(
            log, "TRADE", execution_ts, mid,
            observation_detail(
                "trade", sweep, quote_phase="execution",
                decision_at=decision_at, score_gate=gate,
                siblings={
                    "enabled": False, "complete": True, "rises": (),
                }))

    def test_session_manifest_rejects_role_relabel(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=True,
        )
        log = self._log(cfg, "role-relabel")
        self._quote(
            log, "TRADE", 1, 50,
            observation_detail(
                "watch", 1, quote_phase="evidence", decision_at=1),
        )
        self._quote(
            log, "WATCH", 1.1, 50,
            observation_detail(
                "watch", 1, quote_phase="evidence", decision_at=1.1),
        )
        log.end(clean=True, reason="operator interrupt", ts=2)

        with self.assertRaisesRegex(ValueError, "manifest|market_role"):
            replay(log.tick_path, cfg=cfg)

    def test_incomplete_watch_package_replays_as_gap_not_entry(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=True)
        log = self._log(cfg, "watch-gap-exit-package")
        self._quote(
            log, "TRADE", 1, 50,
            observation_detail(
                "trade", 1, quote_phase="evidence", decision_at=3))
        log.event(
            "WATCH", "api_error", ts=2,
            detail="MarketUnavailable: watch book unavailable")
        self._quote(
            log, "TRADE", 3, 50,
            observation_detail(
                "trade", 1, quote_phase="execution", decision_at=3,
                score_gate=_disabled_gate(), siblings={
                    "enabled": True, "complete": False, "rises": (),
                    "error": "missing current-sweep sibling quote: WATCH",
                }))
        log.end(clean=True, reason="operator interrupt", ts=4)

        result = replay(log.tick_path, cfg=cfg)

        self.assertFalse(result["evaluable"])
        self.assertGreaterEqual(result["data_gaps"], 1)
        self.assertEqual(result["trades"], [])

    def test_replay_rejects_execution_before_same_sweep_evidence(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=True,
        )
        log = self._log(cfg, "phase-order")
        self._quote(
            log, "TRADE", .9, 50,
            observation_detail(
                "trade", 1, quote_phase="evidence", decision_at=2),
        )
        self._quote(
            log, "TRADE", 1, 50,
            observation_detail(
                "trade", 1, quote_phase="execution", decision_at=2,
                score_gate=_disabled_gate(), siblings=_siblings("0")),
        )
        self._quote(
            log, "WATCH", 2, 50,
            observation_detail(
                "watch", 1, quote_phase="evidence", decision_at=2),
        )
        log.end(clean=True, reason="operator interrupt", ts=3)

        with self.assertRaisesRegex(ValueError, "phase|evidence"):
            replay(log.tick_path, cfg=cfg)

    def test_replay_rejects_execution_without_same_ticker_evidence(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False,
        )
        log = ResearchLog(
            tempfile.mkdtemp(), session_id="missing-trade-evidence",
            session_start=0, config=cfg,
            provenance_by_ticker={"TRADE": _provenance()["TRADE"]},
            trade_tickers=("TRADE",), watch_tickers=(),
        )
        self._quote(
            log, "TRADE", 1, 50,
            observation_detail(
                "trade", 1, quote_phase="execution", decision_at=2,
                score_gate=_disabled_gate(),
                siblings={
                    "enabled": False, "complete": True, "rises": (),
                }),
        )
        log.end(clean=True, reason="operator interrupt", ts=3)

        with self.assertRaisesRegex(ValueError, "evidence"):
            replay(log.tick_path, cfg=cfg)

    def test_replay_rejects_decision_time_rewind_between_sweeps(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False,
        )
        log = ResearchLog(
            tempfile.mkdtemp(), session_id="decision-rewind",
            session_start=0, config=cfg,
            provenance_by_ticker={"TRADE": _provenance()["TRADE"]},
            trade_tickers=("TRADE",), watch_tickers=(),
        )
        for sweep, ts, decision_at in ((1, 1, 100), (2, 2, 3)):
            self._quote(
                log, "TRADE", ts, 50,
                observation_detail(
                    "trade", sweep, quote_phase="evidence",
                    decision_at=decision_at),
            )
            self._quote(
                log, "TRADE", ts + .1, 50,
                observation_detail(
                    "trade", sweep, quote_phase="execution",
                    decision_at=decision_at, score_gate=_disabled_gate(),
                    siblings={
                        "enabled": False, "complete": True, "rises": (),
                    }),
            )
        log.end(clean=True, reason="operator interrupt", ts=101)

        with self.assertRaisesRegex(ValueError, "decision|sweep|rewind"):
            replay(log.tick_path, cfg=cfg)

    def test_replay_rejects_terminal_before_final_decision(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False,
        )
        log = ResearchLog(
            tempfile.mkdtemp(), session_id="terminal-rewind",
            session_start=0, config=cfg,
            provenance_by_ticker={"TRADE": _provenance()["TRADE"]},
            trade_tickers=("TRADE",), watch_tickers=(),
        )
        self._quote(
            log, "TRADE", 1, 50,
            observation_detail(
                "trade", 1, quote_phase="evidence", decision_at=100),
        )
        self._quote(
            log, "TRADE", 1.1, 50,
            observation_detail(
                "trade", 1, quote_phase="execution", decision_at=100,
                score_gate=_disabled_gate(),
                siblings={
                    "enabled": False, "complete": True, "rises": (),
                }),
        )
        log.end(clean=True, reason="operator interrupt", ts=2)

        with self.assertRaisesRegex(ValueError, "terminal|decision"):
            replay(log.tick_path, cfg=cfg)

    def test_replay_recomputes_same_sweep_sibling_rise(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=True, sibling_spike_cents=15,
            sibling_spike_lookback_s=45,
        )
        log = self._log(cfg, "fake-sibling-rise")
        for sweep, trade_mid, watch_mid, ts in (
                (1, 60, 5, 1), (2, 52, 40, 2)):
            self._quote(
                log, "WATCH", ts, watch_mid,
                observation_detail(
                    "watch", sweep, quote_phase="evidence",
                    decision_at=ts + .1),
            )
            self._quote(
                log, "TRADE", ts + .05, trade_mid,
                observation_detail(
                    "trade", sweep, quote_phase="evidence",
                    decision_at=ts + .1),
            )
            trade_detail = observation_detail(
                "trade", sweep, quote_phase="execution",
                decision_at=ts + .1, score_gate=_disabled_gate(),
                siblings=_siblings("0"),
            )
            self._quote(log, "TRADE", ts + .1, trade_mid, trade_detail)
        log.end(clean=True, reason="operator interrupt", ts=3)

        with self.assertRaisesRegex(ValueError, "sibling.*rise"):
            replay(log.tick_path, cfg=cfg)

    def test_replay_rejects_impossible_allowed_score_gate(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False,
        )
        log = ResearchLog(
            tempfile.mkdtemp(), session_id="impossible-gate",
            session_start=0, config=cfg,
            provenance_by_ticker={"TRADE": _provenance()["TRADE"]},
            trade_tickers=("TRADE",), watch_tickers=(),
        )
        gate = {
            "enabled": True, "allow": True, "reason": "fake_ok",
            "model_prob": "0.34", "market_prob": "0.53",
            "edge": "-0.19", "espn_match_id": "M",
            "espn_player": "Player One",
        }
        detail = observation_detail(
            "trade", 1, quote_phase="execution", decision_at=1,
            score_gate=gate,
            siblings={"enabled": False, "complete": True, "rises": ()},
        )
        self._quote(
            log, "TRADE", .9, 52,
            observation_detail(
                "trade", 1, quote_phase="evidence", decision_at=1),
        )
        self._quote(log, "TRADE", 1, 52, detail)
        log.end(clean=True, reason="operator interrupt", ts=2)

        with self.assertRaisesRegex(
                ValueError, "score_gate|model_prob|edge|incomplete"):
            replay(log.tick_path, cfg=cfg)

    def test_edge_allow_requires_fresh_independent_two_model_provenance(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False,
        )

        def write(gate, session):
            log = ResearchLog(
                tempfile.mkdtemp(), session_id=session, session_start=0,
                config=cfg,
                provenance_by_ticker={"TRADE": _provenance()["TRADE"]},
                trade_tickers=("TRADE",), watch_tickers=(),
                score_bindings_by_ticker=_score_bindings("TRADE"),
            )
            detail = observation_detail(
                "trade", 1, quote_phase="execution", decision_at=2,
                score_gate=gate,
                siblings={
                    "enabled": False, "complete": True, "rises": (),
                },
            )
            self._quote(
                log, "TRADE", 1.9, 52,
                observation_detail(
                    "trade", 1, quote_phase="evidence", decision_at=2),
            )
            self._quote(log, "TRADE", 2, 52, detail)
            log.end(clean=True, reason="operator interrupt", ts=3)
            return log.tick_path

        base = {
            "enabled": True, "allow": True, "reason": "models_ok",
            "model_prob": "0.80", "market_prob": "0.53",
            "edge": "0.27", "espn_match_id": "espn:M",
            "espn_player": "Player One",
            **_prematch_score_evidence(),
        }
        invalid = [(dict(base), "missing-prior")]
        complete = dict(base, model_1_prob="0.82", model_2_prob="0.80",
                        prior_source_sha256="a" * 64,
                        prior_generated_at="1970-01-01T00:00:01Z",
                        prior_model_1_id="MODEL-A",
                        prior_model_2_id="MODEL-B")
        invalid.append((dict(complete, prior_model_2_id="MODEL-A"),
                        "same-model-id"))
        invalid.append((dict(complete, model_1_prob="0.90"),
                        "fake-score-revision"))
        invalid.append((dict(
            complete, prior_generated_at="1970-01-01T00:00:04Z"),
            "future-prior"))
        for gate, session in invalid:
            with self.subTest(session=session):
                with self.assertRaisesRegex(
                        ValueError,
                        "prior|model.*provenance|model.*id|recompute"):
                    replay(write(gate, session), cfg=cfg)

    def test_allowed_live_gate_requires_fresh_score_at_decision(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False,
        )
        log = ResearchLog(
            tempfile.mkdtemp(), session_id="stale-live-score",
            session_start=0, config=cfg,
            provenance_by_ticker={"TRADE": _provenance()["TRADE"]},
            trade_tickers=("TRADE",), watch_tickers=(),
            score_bindings_by_ticker=_score_bindings("TRADE"),
        )
        decision_at = 100
        self._quote(
            log, "TRADE", 1, 50,
            observation_detail(
                "trade", 1, quote_phase="evidence",
                decision_at=decision_at),
        )
        gate = {
            "enabled": True, "allow": True,
            "reason": "espn_score_guard_only",
            "model_prob": "0.5", "market_prob": "0.51", "edge": None,
            "espn_match_id": "espn:M", "espn_player": "Player One",
            "score_source": "espn", "score_match_id": "espn:M",
            "score_athlete_id": "espn:athlete:one",
            "score_opponent_id": "espn:athlete:two",
            "score_player_name": "Player One",
            "score_opponent_name": "Player Two",
            "score_timestamp": "1", "score_lifecycle_state": "in",
            "gate_observed_at": "2",
            "score_observed": True, "score_best_of": 3,
            "score_sets_for": 0, "score_sets_against": 0,
            "score_games_for": 0, "score_games_against": 0,
            "prematch_model_1_prob": None,
            "prematch_model_2_prob": None,
            "prior_model_as_of": None, "prior_match_start": None,
        }
        self._quote(
            log, "TRADE", 2, 50,
            observation_detail(
                "trade", 1, quote_phase="execution",
                decision_at=decision_at, score_gate=gate,
                siblings={
                    "enabled": False, "complete": True, "rises": (),
                }),
        )
        log.end(clean=True, reason="operator interrupt", ts=101)

        with self.assertRaisesRegex(ValueError, "stale|score"):
            replay(log.tick_path, cfg=cfg)

    def test_complete_score_gate_rejects_forged_deny(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False)
        log = self._single_log(cfg, "forged-deny")
        self._single_package(log, _two_model_gate(allow=False))
        log.end(clean=True, reason="operator interrupt", ts=3)

        with self.assertRaisesRegex(ValueError, "canonical allow"):
            replay(log.tick_path, cfg=cfg)

    def test_incomplete_external_gate_may_only_deny(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False)
        for allow in (False, True):
            with self.subTest(allow=allow):
                log = self._single_log(cfg, f"incomplete-{allow}")
                gate = {
                    "enabled": True, "allow": allow,
                    "reason": "blocked:external_binding",
                    "gate_observed_at": "1.9",
                }
                self._single_package(log, gate)
                log.end(clean=True, reason="operator interrupt", ts=3)
                if allow:
                    with self.assertRaisesRegex(
                            ValueError, "incomplete.*never allow"):
                        replay(log.tick_path, cfg=cfg)
                else:
                    result = replay(log.tick_path, cfg=cfg)
                    self.assertEqual(result["trades"], [])

    def test_prematch_lifecycle_at_scheduled_start_is_rejected(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False, espn_min_model_prob=0.4)
        log = self._single_log(
            cfg, "late-prematch", scheduled_start=10_000)
        gate = _guard_gate(gate_observed_at="10000", allow=False)
        self._single_package(
            log, gate, evidence_ts=9_999, execution_ts=10_000,
            decision_at=10_000, mid=50)
        log.end(clean=True, reason="operator interrupt", ts=10_001)

        result = replay(log.tick_path, cfg=cfg)
        self.assertEqual(result["trades"], [])

    def test_prematch_decision_cannot_cross_scheduled_start(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False, espn_min_model_prob=0.4)
        log = self._single_log(
            cfg, "decision-crosses-start", scheduled_start=10_000)
        gate = _guard_gate(
            gate_observed_at="9999.8", allow=False)
        self._single_package(
            log, gate, evidence_ts=9_999.7, execution_ts=9_999.9,
            decision_at=10_000, mid=50)
        log.end(clean=True, reason="operator interrupt", ts=10_001)

        result = replay(log.tick_path, cfg=cfg)
        self.assertEqual(result["trades"], [])

    def test_replay_rejects_impossible_prior_probability_endpoints(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False)
        log = self._single_log(cfg, "endpoint-prior")
        gate = dict(
            _two_model_gate(),
            prematch_model_1_prob="1",
            prematch_model_2_prob="1",
            model_1_prob="1", model_2_prob="1",
            model_prob="1", edge="0.47")
        self._single_package(log, gate)
        log.end(clean=True, reason="operator interrupt", ts=3)

        with self.assertRaisesRegex(
                ValueError, "prematch.*probability|probability.*prematch"):
            replay(log.tick_path, cfg=cfg)

    def test_replay_rejects_empty_provider_identity_suffix(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False, espn_min_model_prob=0.4)
        log = self._single_log(cfg, "empty-provider-id")
        gate = _guard_gate(gate_observed_at="1.9")
        gate["score_match_id"] = "espn:"
        gate["espn_match_id"] = "espn:"
        self._single_package(log, gate, mid=50)
        log.end(clean=True, reason="operator interrupt", ts=3)

        with self.assertRaisesRegex(ValueError, "provider qualification"):
            replay(log.tick_path, cfg=cfg)

    def test_score_lifecycle_orientation_and_prior_are_pinned(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False, espn_min_model_prob=0.4)

        cases = []
        lifecycle = self._single_log(cfg, "lifecycle-rewind")
        self._single_package(
            lifecycle, _guard_gate(
                state="in", gate_observed_at="1.9",
                score_timestamp="1.8"), mid=50)
        self._single_package(
            lifecycle, _guard_gate(
                state="pre", gate_observed_at="2.9"), sweep=2,
            evidence_ts=2.8, execution_ts=3, decision_at=3, mid=50)
        lifecycle.end(clean=True, reason="operator interrupt", ts=4)
        cases.append((lifecycle.tick_path, "lifecycle.*rewind"))

        orientation = self._single_log(cfg, "orientation-drift")
        self._single_package(
            orientation, _guard_gate(gate_observed_at="1.9"), mid=50)
        self._single_package(
            orientation, _guard_gate(
                gate_observed_at="2.9", athlete="two", opponent="one"),
            sweep=2, evidence_ts=2.8, execution_ts=3, decision_at=3,
            mid=50)
        orientation.end(clean=True, reason="operator interrupt", ts=4)
        cases.append((orientation.tick_path, "orientation.*drift"))

        prior = self._single_log(cfg, "prior-drift")
        self._single_package(prior, _two_model_gate())
        self._single_package(
            prior, _two_model_gate(
                gate_observed_at="2.9", digest="b" * 64), sweep=2,
            evidence_ts=2.8, execution_ts=3, decision_at=3)
        prior.end(clean=True, reason="operator interrupt", ts=4)
        cases.append((prior.tick_path, "prior baseline.*drift"))

        for path, pattern in cases:
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(ValueError, pattern):
                    replay(path, cfg=cfg)

    def test_first_score_identity_must_match_discovery_binding(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False, espn_min_model_prob=0.4)
        log = self._single_log(cfg, "foreign-first-score")
        gate = _guard_gate(gate_observed_at="1.9")
        gate.update(
            espn_match_id="espn:FOREIGN",
            score_match_id="espn:FOREIGN",
            score_athlete_id="espn:athlete:foreign-one",
            score_opponent_id="espn:athlete:foreign-two",
            score_player_name="Foreign One",
            score_opponent_name="Foreign Two",
            espn_player="Foreign One")
        self._single_package(log, gate, mid=50)
        log.end(clean=True, reason="operator interrupt", ts=3)

        with self.assertRaisesRegex(ValueError, "discovery binding"):
            replay(log.tick_path, cfg=cfg)

    def test_live_score_progress_cannot_rewind(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False, espn_min_model_prob=0)
        log = self._single_log(cfg, "live-score-rewind")
        first = _guard_gate(
            state="in", gate_observed_at="1.9", score_timestamp="1.8")
        first.update(
            score_sets_for=1, score_games_for=5,
            model_prob=str(Decimal(str(round(match_win_probability(
                1, 0, 5, 0, best_of=3), 6)))))
        self._single_package(log, first, mid=50)

        second = _guard_gate(
            state="in", gate_observed_at="2.9", score_timestamp="2.8")
        self._single_package(
            log, second, sweep=2, evidence_ts=2.8,
            execution_ts=3, decision_at=3, mid=50)
        log.end(clean=True, reason="operator interrupt", ts=4)

        with self.assertRaisesRegex(ValueError, "score.*rewind|progress"):
            replay(log.tick_path, cfg=cfg)

    def test_gate_and_prior_chronology_is_strict(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False)
        mutations = (
            ("model-after-generated", {
                "prior_model_as_of": "1970-01-01T00:00:02Z"}),
            ("generated-after-gate", {
                "prior_generated_at": "1970-01-01T00:00:02Z"}),
            ("gate-after-execution", {"gate_observed_at": "2.1"}),
        )
        for session, updates in mutations:
            with self.subTest(session=session):
                log = self._single_log(cfg, session)
                gate = dict(_two_model_gate(), **updates)
                self._single_package(log, gate)
                log.end(clean=True, reason="operator interrupt", ts=3)
                with self.assertRaisesRegex(
                        ValueError, "chronology|cutoff|gate_observed"):
                    replay(log.tick_path, cfg=cfg)

        live = self._single_log(cfg, "score-after-gate")
        self._single_package(
            live, _guard_gate(
                state="in", gate_observed_at="1.9",
                score_timestamp="1.95", allow=False), mid=50)
        live.end(clean=True, reason="operator interrupt", ts=3)
        with self.assertRaisesRegex(
                ValueError, "score_timestamp.*gate_observed"):
            replay(live.tick_path, cfg=cfg)

    def test_runtime_package_requires_exact_same_event_watch_set(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False)
        provenance = {
            "TRADE": ContractProvenance(
                "Tennis", "ATP", "KXATP", "M", "EVENT", 10_000),
            "SAME": ContractProvenance(
                "Tennis", "ATP", "KXATP", "M", "EVENT", 10_000),
            "OTHER": ContractProvenance(
                "Tennis", "ATP", "KXATP", "M2", "OTHER-EVENT", 10_000),
        }
        for include_same, include_other, session in (
                (False, False, "missing-watch"),
                (True, True, "extra-watch")):
            with self.subTest(session=session):
                log = ResearchLog(
                    tempfile.mkdtemp(), session_id=session, session_start=0,
                    config=cfg, provenance_by_ticker=provenance,
                    trade_tickers=("TRADE",),
                    watch_tickers=("SAME", "OTHER"))
                self._quote(
                    log, "TRADE", 1, 50,
                    observation_detail(
                        "trade", 1, quote_phase="evidence",
                        decision_at=2))
                if include_same:
                    self._quote(
                        log, "SAME", 1.1, 50,
                        observation_detail(
                            "watch", 1, quote_phase="evidence",
                            decision_at=2))
                if include_other:
                    self._quote(
                        log, "OTHER", 1.2, 50,
                        observation_detail(
                            "watch", 1, quote_phase="evidence",
                            decision_at=2))
                self._quote(
                    log, "TRADE", 2, 50,
                    observation_detail(
                        "trade", 1, quote_phase="execution",
                        decision_at=2, score_gate=_disabled_gate(),
                        siblings={
                            "enabled": False, "complete": True,
                            "rises": (),
                        }))
                # Ensure both watch tickers have durable provenance even when
                # one was deliberately omitted from the trade package.
                next_sweep = 2
                for offset, ticker in enumerate(("SAME", "OTHER"), start=1):
                    if ticker in ({"SAME"} if include_same else set()) | (
                            {"OTHER"} if include_other else set()):
                        continue
                    self._quote(
                        log, ticker, 2 + offset / 10, 50,
                        observation_detail(
                            "watch", next_sweep,
                            quote_phase="evidence",
                            decision_at=2 + offset / 10))
                    next_sweep += 1
                log.end(clean=True, reason="operator interrupt", ts=4)
                with self.assertRaisesRegex(
                        ValueError,
                        "watch evidence|watch manifest|incomplete sibling"):
                    replay(log.tick_path, cfg=cfg)

    def test_evidence_only_single_watch_sweep_is_valid(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False)
        provenance = {"WATCH": ContractProvenance(
            "Tennis", "ATP", "KXATP", "M", "EVENT", 10_000)}
        log = ResearchLog(
            tempfile.mkdtemp(), session_id="watch-only", session_start=0,
            config=cfg, provenance_by_ticker=provenance,
            trade_tickers=(), watch_tickers=("WATCH",))
        self._quote(
            log, "WATCH", 1, 50,
            observation_detail(
                "watch", 1, quote_phase="evidence", decision_at=1))
        log.end(clean=True, reason="operator interrupt", ts=2)

        rows, gaps = load_log(log.tick_path, cfg=cfg, include_watch=True)
        self.assertEqual(rows, [])
        self.assertEqual(gaps, 0)

    def test_sibling_trade_ledger_is_mandatory_and_exact(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False)

        missing = self._single_log(cfg, "missing-ledger")
        self._single_package(missing, _disabled_gate())
        missing.end(clean=True, reason="operator interrupt", ts=3)
        os.unlink(missing.trade_path)
        with self.assertRaisesRegex(ValueError, "requires sibling trade log"):
            replay(missing.tick_path, cfg=cfg)

        forged = self._single_log(cfg, "forged-ledger")
        self._single_package(forged, _disabled_gate())
        forged.trade(
            "TRADE", "BUY", Decimal(51), Decimal(1), "forged fill",
            fee=Decimal("0.0200"), ts=2)
        forged.end(clean=True, reason="operator interrupt", ts=3)
        with self.assertRaisesRegex(ValueError, "reconstructed fills"):
            replay(forged.tick_path, cfg=cfg)

        malformed = self._single_log(cfg, "bad-ledger-metadata")
        self._single_package(malformed, _disabled_gate())
        malformed.trade(
            "TRADE", "BUY", Decimal(51), Decimal(1), "forged fill",
            fee=Decimal("0.0200"), ts=2)
        malformed.end(clean=True, reason="operator interrupt", ts=3)
        with open(malformed.trade_path, newline="") as handle:
            rows = list(csv.reader(handle))
        rows[1][1] = "OTHER-SESSION"
        with open(malformed.trade_path, "w", newline="") as handle:
            csv.writer(handle).writerows(rows)
        with self.assertRaisesRegex(ValueError, "inconsistent session_id"):
            replay(malformed.tick_path, cfg=cfg)

    def test_fill_comparison_covers_every_audit_field(self):
        baseline = LoggedTrade(
            2.0, "TRADE", "BUY", Decimal(51), Decimal(2),
            Decimal("0.03"), "dip")
        alternatives = (
            LoggedTrade(2.1, "TRADE", "BUY", Decimal(51), Decimal(2),
                        Decimal("0.03"), "dip"),
            LoggedTrade(2.0, "OTHER", "BUY", Decimal(51), Decimal(2),
                        Decimal("0.03"), "dip"),
            LoggedTrade(2.0, "TRADE", "SELL", Decimal(51), Decimal(2),
                        Decimal("0.03"), "dip"),
            LoggedTrade(2.0, "TRADE", "BUY", Decimal(52), Decimal(2),
                        Decimal("0.03"), "dip"),
            LoggedTrade(2.0, "TRADE", "BUY", Decimal(51), Decimal(1),
                        Decimal("0.03"), "dip"),
            LoggedTrade(2.0, "TRADE", "BUY", Decimal(51), Decimal(2),
                        Decimal("0.04"), "dip"),
            LoggedTrade(2.0, "TRADE", "BUY", Decimal(51), Decimal(2),
                        Decimal("0.03"), "other reason"),
        )
        for changed in alternatives:
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(ValueError, "contradicts"):
                    _compare_trade_logs([baseline], [changed])

    def test_fill_timeout_changes_research_config_fingerprint(self):
        short = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False, fill_timeout_s=4,
        )
        long = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False, fill_timeout_s=100,
        )

        self.assertNotEqual(
            config_fingerprint(short), config_fingerprint(long))

    def test_prior_requirement_changes_research_config_fingerprint(self):
        guard_only = Config(
            sports=["Tennis"], two_model_prior_path="",
            sibling_spike_enabled=False)
        prior_required = Config(
            sports=["Tennis"], two_model_prior_path="/tmp/priors.json",
            sibling_spike_enabled=False)

        self.assertNotEqual(
            config_fingerprint(guard_only),
            config_fingerprint(prior_required))

    def test_prior_required_config_rejects_guard_only_allow(self):
        cfg = Config(
            sports=["Tennis"],
            two_model_prior_path="/tmp/priors.json",
            sibling_spike_enabled=False,
            espn_min_model_prob=0.4)
        log = self._single_log(cfg, "required-prior-guard-only")
        self._single_package(
            log, _guard_gate(gate_observed_at="1.9"), mid=50)
        log.end(clean=True, reason="operator interrupt", ts=3)

        with self.assertRaisesRegex(ValueError, "canonical deny|prior"):
            replay(log.tick_path, cfg=cfg)


if __name__ == "__main__":
    unittest.main()
