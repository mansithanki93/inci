"""The single per-tick decision path shared by bot.py (real time) and
replay.py (virtual time)."""
import time as _time
from datetime import datetime, timezone
from decimal import Decimal

from executor import HaltError
from schemas import UnknownOrderState
from fees import fee_usd
from research_log import observation_detail
from safety import ExposureError


def _exit_priority(reason):
    """Higher wins. Stop preempts time; time preempts take-profit."""
    if "stop-loss" in reason:
        return 2
    if reason.startswith("time exit"):
        return 1
    return 0


def _utc_epoch_decimal(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    parsed = parsed.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = parsed - epoch
    return (Decimal(delta.days * 86400 + delta.seconds)
            + Decimal(delta.microseconds) / Decimal(1_000_000))


class Context:
    def __init__(self, cfg, feed, strategy, executor, log, safety,
                 clock=_time.time, espn_gate=None):
        self.cfg = cfg
        self.feed = feed
        self.strategy = strategy
        self.executor = executor
        self.log = log
        self.safety = safety
        self.clock = clock
        self.espn_gate = espn_gate
        self.latest_bid = {}
        self.bid_ts = {}          # ticker -> time of last usable bid
        self.entry_status = {}    # ticker -> stable lifecycle gate status
        self.score_identity_by_ticker = {}  # ticker -> provider orientation
        self.sweep_id = 0


def set_entry_status(ctx, ticker, status, message=None):
    """Publish lifecycle gate state without repeating unchanged console text."""
    previous = ctx.entry_status.get(ticker)
    ctx.entry_status[ticker] = status
    if message is not None and previous != status:
        print(message)


def _event_ticker_for(ctx, ticker):
    provenance = getattr(ctx.feed, "provenance_by_ticker", None) or {}
    if not hasattr(provenance, "get"):
        return None
    row = provenance.get(ticker)
    if row is None:
        return None
    return getattr(row, "event_ticker", None) or None


def _contract_identity(ctx, ticker):
    """Return the immutable market names used by the external score gate."""
    player_name = ticker
    event_title = ""
    scheduled_start_ts = None
    contracts = getattr(ctx.feed, "contracts_by_ticker", None) or {}
    contract = contracts.get(ticker) if hasattr(contracts, "get") else None
    if contract is not None:
        player_name = getattr(contract, "title", player_name) or player_name
        event_title = getattr(contract, "game_title", "") or ""
    provenance = getattr(ctx.feed, "provenance_by_ticker", None) or {}
    provenance_row = (provenance.get(ticker)
                      if hasattr(provenance, "get") else None)
    if provenance_row is not None:
        scheduled_start_ts = getattr(
            provenance_row, "scheduled_start_ts", None)
    return player_name, event_title, scheduled_start_ts


def _gate_snapshot(ctx, ticker, ask, supplied=None, *, required=False):
    """Capture one fail-closed score decision for this exact observation."""
    if supplied is not None:
        return supplied
    if not bool(getattr(ctx.cfg, "espn_gate_enabled", True)):
        return {
            "enabled": False, "allow": True,
            "reason": "score_gate_disabled",
        }
    gate = getattr(ctx, "espn_gate", None)
    if gate is None or not gate.enabled():
        if not required:
            # Low-level engine tests/callers without the runtime's external
            # gate keep legacy behavior. The real driver passes required=True
            # and replay always supplies a durable decision.
            return {
                "enabled": False, "allow": True,
                "reason": "score_gate_not_attached",
            }
        return {
            "enabled": True, "allow": False,
            "reason": "blocked:score_gate_unavailable",
        }
    player_name, event_title, scheduled_start_ts = _contract_identity(
        ctx, ticker)
    try:
        decision = gate.decide(
            ticker=ticker, player_name=player_name,
            event_title=event_title, ask_cents=ask,
            scheduled_start_ts=scheduled_start_ts)
    except Exception as error:  # noqa: BLE001 - external gate fails closed
        return {
            "enabled": True, "allow": False,
            "reason": (f"blocked:score_gate_error "
                       f"({type(error).__name__}: {error})"),
            "gate_observed_at": str(Decimal(str(ctx.clock()))),
        }
    snapshot = {
        "enabled": True,
        "allow": bool(getattr(decision, "allow", False)),
        "reason": str(getattr(decision, "reason", "") or
                      "blocked:score_gate_missing_reason"),
        "gate_observed_at": str(Decimal(str(ctx.clock()))),
    }
    for field in (
            "model_prob", "market_prob", "edge", "espn_match_id",
            "espn_player", "model_1_prob", "model_2_prob",
            "prior_source_sha256", "prior_generated_at",
            "prior_model_1_id", "prior_model_2_id", "score_source",
            "score_match_id", "score_athlete_id", "score_opponent_id",
            "score_player_name", "score_opponent_name",
            "score_timestamp", "score_lifecycle_state",
            "prematch_model_1_prob", "prematch_model_2_prob",
            "prior_model_as_of", "prior_match_start"):
        value = getattr(decision, field, None)
        snapshot[field] = None if value is None else str(value)
    for field in (
            "score_observed", "score_best_of", "score_sets_for",
            "score_sets_against", "score_games_for",
            "score_games_against"):
        snapshot[field] = getattr(decision, field, None)
    # Early fail-closed decisions (match over, stale score, invalid score)
    # can know identity/lifecycle before a complete score is available. Keep
    # denied audit rows schema-valid by emitting the structured score group
    # only when it is complete; an allowed row with missing identity is then
    # blocked by the post-evidence reprice below.
    score_core = (
        "score_source", "score_match_id", "score_athlete_id",
        "score_opponent_id", "score_lifecycle_state", "score_observed",
        "score_best_of", "score_sets_for", "score_sets_against",
        "score_games_for", "score_games_against",
    )
    if any(snapshot.get(field) is None for field in score_core):
        for field in (*score_core, "score_timestamp"):
            snapshot[field] = None
    else:
        identity = tuple(snapshot[field] for field in (
            "score_source", "score_match_id", "score_athlete_id",
            "score_opponent_id", "score_player_name",
            "score_opponent_name"))
        pinned = ctx.score_identity_by_ticker.setdefault(ticker, identity)
        identity_drift = pinned != identity
        if identity_drift:
            snapshot.update(
                allow=False, reason="blocked:score_identity_drift")
        if required and not identity_drift:
            bindings = getattr(ctx.feed, "score_bindings_by_ticker", None)
            expected = (bindings.get(ticker)
                        if hasattr(bindings, "get") else None)
            actual = identity[1:]
            if expected is None:
                snapshot.update(
                    allow=False,
                    reason="blocked:missing_discovery_score_binding")
            else:
                expected = tuple(expected)
                comparable = actual[:3] if len(expected) == 3 else actual
                if expected != comparable:
                    snapshot.update(
                        allow=False,
                        reason="blocked:discovery_score_binding_mismatch")
    return snapshot


def _gate_block(snapshot):
    if snapshot.get("enabled") and not snapshot.get("allow"):
        return snapshot.get("reason") or "blocked:score_gate"
    return None


def _reprice_gate_snapshot(ctx, snapshot, ask, *, decision_at=None,
                           ticker=None):
    """Apply immutable score/model evidence to the post-evidence ask."""
    updated = dict(snapshot)
    if not updated.get("enabled"):
        return updated
    score_fields = (
        "score_source", "score_match_id", "score_athlete_id",
        "score_opponent_id", "score_lifecycle_state", "score_observed",
        "score_best_of", "score_sets_for", "score_sets_against",
        "score_games_for", "score_games_against",
    )
    if any(updated.get(field) is None for field in score_fields):
        if updated.get("allow"):
            updated.update(
                allow=False, reason="blocked:incomplete_score_evidence")
        return updated
    # Only an edge denial can legitimately change when the post-evidence ask
    # changes. Identity, lifecycle, score-progress, model-collapse, stale and
    # provider failures are hard denials and must never be repriced to allow.
    reason = str(updated.get("reason") or "")
    if (not updated.get("allow")
            and not reason.startswith("blocked:edge ")):
        updated["allow"] = False
        return updated
    try:
        gate_observed_at = Decimal(str(updated.get("gate_observed_at")))
    except Exception:
        updated.update(
            allow=False, reason="blocked:missing_gate_observation_time")
        return updated
    if not gate_observed_at.is_finite() or gate_observed_at < 0:
        updated.update(
            allow=False, reason="blocked:invalid_gate_observation_time")
        return updated
    try:
        decided = Decimal(str(
            ctx.clock() if decision_at is None else decision_at))
    except Exception:
        updated.update(
            allow=False, reason="blocked:invalid_decision_timestamp")
        return updated
    if (not decided.is_finite() or decided < gate_observed_at):
        updated.update(
            allow=False, reason="blocked:invalid_gate_decision_chronology")
        return updated
    lifecycle = updated["score_lifecycle_state"]
    if lifecycle == "post":
        updated.update(allow=False, reason="blocked:match_over")
        return updated
    if lifecycle == "pre" and ticker is not None:
        provenance = getattr(ctx.feed, "provenance_by_ticker", None) or {}
        row = provenance.get(ticker) if hasattr(provenance, "get") else None
        scheduled = getattr(row, "scheduled_start_ts", None)
        try:
            scheduled = Decimal(str(scheduled))
        except Exception:
            updated.update(
                allow=False, reason="blocked:invalid_scheduled_start")
            return updated
        if (not decided.is_finite() or not scheduled.is_finite()
                or decided >= scheduled):
            updated.update(
                allow=False,
                reason="blocked:prematch_state_at_decision_time")
            return updated
    if lifecycle == "in":
        try:
            score_at = Decimal(str(updated.get("score_timestamp")))
            maximum_age = Decimal(str(getattr(
                ctx.cfg, "score_provider_max_age_s",
                max(45.0, float(ctx.cfg.espn_cache_s) * 3.0))))
        except Exception:
            updated.update(
                allow=False, reason="blocked:invalid_score_timestamp")
            return updated
        age = decided - score_at
        if (updated.get("score_observed") is not True
                or not decided.is_finite() or not score_at.is_finite()
                or age < 0 or age > maximum_age):
            updated.update(
                allow=False, reason="blocked:stale_score_at_decision")
            return updated
    try:
        ask_prob = Decimal(str(ask)) / Decimal(100)
    except Exception:
        updated.update(
            allow=False, reason="blocked:invalid_post_evidence_ask")
        return updated
    if (not ask_prob.is_finite()
            or ask_prob <= Decimal(0) or ask_prob >= Decimal(1)):
        updated.update(
            allow=False, reason="blocked:invalid_post_evidence_ask")
        return updated

    model_raw = updated.get("model_prob")
    if model_raw is None:
        if updated.get("market_prob") is not None:
            updated["market_prob"] = str(ask_prob)
        return updated
    try:
        model = Decimal(str(model_raw))
    except Exception:
        updated.update(
            allow=False, reason="blocked:invalid_post_evidence_model")
        return updated
    if not model.is_finite():
        updated.update(
            allow=False, reason="blocked:invalid_post_evidence_model")
        return updated

    updated["market_prob"] = str(ask_prob)
    minimum_model = Decimal(str(ctx.cfg.espn_min_model_prob))
    if updated.get("edge") is None:
        # Guard-only decisions have no fair-value claim; their allow state is
        # score/model based and therefore unaffected by the market requote.
        if bool(getattr(ctx.cfg, "two_model_prior_path", "")):
            updated.update(
                allow=False, reason="blocked:prematch_prior_required")
        elif model < minimum_model:
            updated.update(
                allow=False,
                reason=(f"blocked:score_collapse {model:.3f}"
                        f"<{minimum_model} after requote"))
        return updated

    prior_fields = (
        "model_1_prob", "model_2_prob", "prior_source_sha256",
        "prior_generated_at", "prior_model_1_id", "prior_model_2_id",
        "prematch_model_1_prob", "prematch_model_2_prob",
        "prior_model_as_of", "prior_match_start",
    )
    if any(updated.get(field) is None for field in prior_fields):
        updated.update(
            allow=False, reason="blocked:incomplete_prematch_provenance")
        return updated

    try:
        model_as_of = _utc_epoch_decimal(updated["prior_model_as_of"])
        generated_at = _utc_epoch_decimal(updated["prior_generated_at"])
        match_start = _utc_epoch_decimal(updated["prior_match_start"])
        maximum_prior_age = Decimal(str(
            ctx.cfg.two_model_prior_max_age_s))
    except Exception:
        updated.update(
            allow=False, reason="blocked:invalid_prematch_prior_timeline")
        return updated
    if (model_as_of > generated_at or generated_at > match_start
            or not maximum_prior_age.is_finite()
            or maximum_prior_age <= 0):
        updated.update(
            allow=False, reason="blocked:invalid_prematch_prior_timeline")
        return updated
    prior_age = decided - generated_at
    if prior_age < 0 or prior_age > maximum_prior_age:
        updated.update(
            allow=False, reason="blocked:stale_prematch_prior_at_decision")
        return updated
    if ticker is not None:
        provenance = getattr(ctx.feed, "provenance_by_ticker", None) or {}
        row = provenance.get(ticker) if hasattr(provenance, "get") else None
        scheduled = getattr(row, "scheduled_start_ts", None)
        try:
            scheduled = Decimal(str(scheduled))
        except Exception:
            scheduled = None
        if (scheduled is None or not scheduled.is_finite()
                or match_start != scheduled):
            updated.update(
                allow=False, reason="blocked:prematch_start_mismatch")
            return updated

    edge = model - ask_prob
    updated["edge"] = str(edge)
    minimum_edge = Decimal(str(ctx.cfg.espn_min_edge))
    if model < minimum_model:
        updated.update(
            allow=False,
            reason=(f"blocked:model_prob {model:.3f}<{minimum_model} "
                    "after requote"))
    elif edge < minimum_edge:
        updated.update(
            allow=False,
            reason=(f"blocked:edge {edge:+.3f}<{minimum_edge} "
                    f"(model {model:.3f} vs post-evidence ask "
                    f"{ask_prob:.3f})"))
    else:
        updated.update(
            allow=True,
            reason=(f"post_evidence_edge {edge:+.3f}>={minimum_edge} "
                    f"(model {model:.3f} vs ask {ask_prob:.3f})"))
    return updated


def _same_event_exposure(ctx, ticker):
    """Return another ticker with open/pending exposure on the same Event."""
    event = _event_ticker_for(ctx, ticker)
    if not event:
        return None
    occupied = set(ctx.strategy.positions)
    for order in list(getattr(ctx.executor, "pending_paper", None) or ()):
        other = getattr(order, "ticker", None)
        if other:
            occupied.add(other)
    for other in occupied:
        if other == ticker:
            continue
        if _event_ticker_for(ctx, other) == event:
            return other
    return None


def _sibling_snapshot(ctx, ticker, now, observed_tickers=None):
    """Capture same-sweep sibling rises used by both runtime and replay."""
    if not bool(getattr(ctx.cfg, "sibling_spike_enabled", True)):
        return {"enabled": False, "complete": True, "rises": ()}
    try:
        siblings = tuple(ctx.feed.sibling_tickers(ticker) or ())
    except Exception as error:  # noqa: BLE001 - fail closed below
        if observed_tickers is None:
            return {"enabled": True, "complete": True, "rises": ()}
        return {
            "enabled": True, "complete": False, "rises": (),
            "error": f"{type(error).__name__}: {error}",
        }
    if not siblings and observed_tickers is None:
        return {"enabled": True, "complete": True, "rises": ()}
    if not siblings:
        return {
            "enabled": True, "complete": False, "rises": (),
            "error": "no same-event sibling provenance",
        }
    if observed_tickers is not None:
        missing = tuple(sorted(set(siblings) - set(observed_tickers)))
        if missing:
            return {
                "enabled": True, "complete": False, "rises": (),
                "error": "missing current-sweep sibling quote: "
                         + ",".join(missing),
            }
    lookback = float(getattr(
        ctx.cfg, "sibling_spike_lookback_s",
        getattr(ctx.cfg, "lookback_seconds", 45.0)))
    rises = []
    try:
        for sibling in siblings:
            rise = Decimal(str(ctx.feed.mid_rise_in_lookback(
                sibling, now, lookback)))
            rises.append((sibling, str(rise)))
    except Exception as error:  # noqa: BLE001 - fail closed below
        return {
            "enabled": True, "complete": False, "rises": (),
            "error": f"{type(error).__name__}: {error}",
        }
    return {"enabled": True, "complete": True, "rises": tuple(rises)}


def _sibling_spike_block(ctx, ticker, now, supplied=None):
    """Block when an opposite-side mid spiked up inside the lookback window."""
    evidence = (_sibling_snapshot(ctx, ticker, now)
                if supplied is None else supplied)
    if not evidence.get("enabled"):
        return None
    if not evidence.get("complete"):
        return "sibling_evidence_incomplete: " + str(
            evidence.get("error") or "unknown")
    threshold = Decimal(str(getattr(ctx.cfg, "sibling_spike_cents", 15)))
    lookback = float(getattr(
        ctx.cfg, "sibling_spike_lookback_s",
        getattr(ctx.cfg, "lookback_seconds", 45.0)))
    worst = None
    for sibling, rise in evidence.get("rises", ()):
        rise = Decimal(str(rise))
        if rise >= threshold and (worst is None or rise > worst[0]):
            worst = (rise, sibling)
    if worst is None:
        return None
    rise, sibling = worst
    return (f"sibling_spike {sibling} mid +{rise}c "
            f">= {threshold}c in {lookback:.0f}s")


def sync_execution_observation(ctx, ticker):
    """Move the executor's fresh requote into the risk mark immediately."""
    observation = getattr(ctx.executor, "last_observation", None)
    if not observation or observation.get("ticker") != ticker:
        return
    bid = observation.get("bid")
    observed_at = observation.get("observed_at")
    if bid is not None and observed_at is not None:
        ctx.latest_bid[ticker] = bid
        ctx.bid_ts[ticker] = observed_at


def process_tick(ctx, ticker, mid, bid, ask, observed_at=None, *,
                 decision_at=None, gate_snapshot=None,
                 sibling_snapshot=None, log_observation=True,
                 sweep_id=None):
    # One timestamp governs the complete causal processing of this quote.
    # In real time it is captured by PriceFeed; replay supplies the CSV row
    # timestamp.  This prevents processing delay from making an old quote
    # eligible for a latency-delayed paper fill.
    quote_ts = ctx.clock() if observed_at is None else observed_at
    now = quote_ts if decision_at is None else decision_at
    current_gate = _gate_snapshot(ctx, ticker, ask, supplied=gate_snapshot)
    current_siblings = (_sibling_snapshot(ctx, ticker, now)
                        if sibling_snapshot is None else sibling_snapshot)
    if ctx.log and log_observation:
        if sweep_id is None:
            ctx.sweep_id += 1
            sweep_id = ctx.sweep_id
        _, bq, _, aq = ctx.feed.top_of_book(ticker)
        close_ts, can_close_early = (
            ctx.feed.lifecycle(ticker)
            if hasattr(ctx.feed, "lifecycle") else (None, None))
        if mid is None:
            ctx.log.event(ticker, "no_quote", ts=quote_ts)
        else:
            ctx.log.tick(
                ticker, mid, bid, ask, bq, aq, ts=quote_ts,
                detail=observation_detail(
                    "trade", sweep_id, quote_phase="execution",
                    decision_at=now, score_gate=current_gate,
                    siblings=current_siblings),
                close_ts=close_ts, can_close_early=can_close_early)
    if bid is not None:
        ctx.latest_bid[ticker] = bid
        ctx.bid_ts[ticker] = quote_ts

    fresh_quote = mid is not None and bid is not None and ask is not None
    if ctx.cfg.paper_trading and fresh_quote:
        # A due exit has not filled until this quote is processed. Re-evaluate
        # it against the same book first so a newly breached hard stop can
        # preempt a lower-priority time/TP exit and use stop execution
        # semantics without paying a second latency window.
        pending_sell = (ctx.executor.get_pending(ticker, side="SELL")
                        if hasattr(ctx.executor, "get_pending") else None)
        if (pending_sell is not None
                and pending_sell.due_at <= quote_ts
                and ticker in ctx.strategy.positions):
            # This branch may consume the current book immediately. Evaluate
            # time-based priority at the book's observation time, not at the
            # later completed-sweep decision time, so a pre-deadline quote
            # cannot fill an exit that only became eligible afterwards.
            current_exit = ctx.strategy.check_exit(
                ticker, bid, now=quote_ts)
            if (current_exit is not None
                    and _exit_priority(current_exit["reason"])
                    > _exit_priority(pending_sell.reason)):
                print(f"[signal] SELL {ticker}: {current_exit['reason']}")
                ctx.executor.upgrade_pending_paper_exit(
                    ticker, current_exit["reason"])
        pending_buy = (ctx.executor.get_pending(ticker, side="BUY")
                       if hasattr(ctx.executor, "get_pending") else None)
        if pending_buy is not None and pending_buy.due_at <= quote_ts:
            block = None
            # A delayed entry cannot add exposure after the portfolio has
            # already crossed its fee-inclusive loss limit. Evaluate before
            # the IOC attempt, while risk-reducing SELLs remain marketable.
            check_loss_limit(ctx)
            if ctx.safety.tripped:
                block = ctx.safety.tripped_reason or "portfolio risk halt"
            elif not ctx.cfg.min_price <= ask <= ctx.cfg.max_price:
                block = f"price {ask} outside entry bounds"
            elif ask - bid > ctx.cfg.max_spread:
                block = (f"spread {ask - bid} exceeds "
                         f"{ctx.cfg.max_spread}")
            if (block is None and hasattr(ctx.feed, "entry_allowed")
                    and not ctx.feed.entry_allowed(
                        ticker, now,
                        ctx.cfg.max_hold_seconds
                        + ctx.cfg.close_buffer_seconds)):
                block = "insufficient close horizon"
            if block is None and bool(getattr(
                    ctx.cfg, "one_contract_per_event", True)):
                conflict = _same_event_exposure(ctx, ticker)
                if conflict is not None:
                    block = f"same-event exposure via {conflict}"
            if block is None:
                block = _gate_block(current_gate)
            if block is None:
                block = _sibling_spike_block(
                    ctx, ticker, now, supplied=current_siblings)
            if block is not None:
                ctx.executor.cancel_pending_paper(ticker=ticker, side="BUY")
                set_entry_status(
                    ctx, ticker, f"canceled:arrival:{block[:40]}",
                    f"[entry] CANCELED {ticker} at IOC arrival: {block}")
        for pending, result in ctx.executor.process_due_paper_orders(
                quote_ts, ticker=ticker):
            if result:
                # The quote timestamp determines whether the delayed IOC had
                # an eligible arrival observation. State and audit records
                # cannot precede completion of the two-phase decision sweep.
                ctx.strategy.record_fill(
                    pending.ticker, pending.side, *result, now=now)
                if ctx.log:
                    ctx.log.trade(pending.ticker, pending.side,
                                  result[0], result[1], pending.reason,
                                  fee=result[2], ts=now)

    # Revalue all exposure after each observation (and any due fill), before
    # another exit/entry can be scheduled.  This gives runtime and replay the
    # same loss-check cadence and stops a breached account in this ticker,
    # rather than after the rest of a multi-market sweep.
    check_loss_limit(ctx)
    if ctx.safety.tripped:
        return
    # A pending delayed-IOC BUY (awaiting entry) blocks further action on this
    # ticker. A working SELL does NOT block the exit re-check, so a higher-
    # priority exit (time over TP, stop over either) can upgrade once.
    if ctx.cfg.paper_trading and ctx.executor.has_pending(ticker, side="BUY"):
        return

    exit_sig = ctx.strategy.check_exit(ticker, bid, now=now)
    if exit_sig:
        pos = ctx.strategy.positions[ticker]
        if ctx.cfg.paper_trading:
            reason = exit_sig["reason"]
            working = ctx.executor.get_pending(ticker, side="SELL")
            if working is not None:
                # Never refresh due_at on an equal/higher-priority pending
                # exit (stop deadline sliding). Only upgrade once.
                if _exit_priority(reason) > _exit_priority(working.reason):
                    print(f"[signal] SELL {ticker}: {reason}")
                    if hasattr(ctx.executor, "upgrade_pending_paper_exit"):
                        ctx.executor.upgrade_pending_paper_exit(
                            ticker, reason)
                    else:
                        # Compatibility for minimal test doubles. Production
                        # Executor always upgrades in place so the already-
                        # paid latency window is preserved.
                        ctx.executor.cancel_pending_paper(
                            ticker=ticker, side="SELL")
                        ctx.executor.submit_paper(
                            ticker, "SELL", pos.contracts, reason, now=now)
                return
            print(f"[signal] SELL {ticker}: {reason}")
            ctx.executor.submit_paper(
                ticker, "SELL", pos.contracts, reason, now=now,
                entry_price=pos.entry_price,
                entry_contracts=pos.contracts,
                entry_fee_usd=pos.entry_fee_usd)
            return
        print(f"[signal] SELL {ticker}: {exit_sig['reason']}")
        result = ctx.executor.execute(ticker, "SELL", pos.contracts,
                                      expected_pre_position=pos.contracts)
        sync_execution_observation(ctx, ticker)
        if result:
            outcome_id = getattr(ctx.executor, "last_outcome_id", None)
            ctx.strategy.record_fill(ticker, "SELL", *result, now=now,
                                     event_id=outcome_id)
            journal = getattr(ctx.executor, "journal", None)
            if journal and outcome_id:
                journal.record("applied", order_id=outcome_id)
            if ctx.log:
                ctx.log.trade(ticker, "SELL", result[0], result[1],
                              exit_sig["reason"], fee=result[2],
                              ts=now)
        check_loss_limit(ctx)
        return

    # Holding a position with a working delayed-IOC exit: nothing to enter.
    if ctx.cfg.paper_trading and ctx.executor.has_pending(ticker, side="SELL"):
        return

    hist = list(ctx.feed.history[ticker])[:-1]
    if (ctx.cfg.paper_trading
            and len(ctx.strategy.positions)
            + ctx.executor.pending_count("BUY")
            >= ctx.cfg.max_open_positions):
        return
    if (hasattr(ctx.feed, "entry_allowed")
            and not ctx.feed.entry_allowed(
                ticker, now,
                ctx.cfg.max_hold_seconds + ctx.cfg.close_buffer_seconds)):
        set_entry_status(
            ctx, ticker, "blocked:close_horizon",
            f"[entry] BLOCKED {ticker}: insufficient close horizon")
        return
    early_close_risk = (
        hasattr(ctx.feed, "early_close_risk")
        and ctx.feed.early_close_risk(ticker))
    if early_close_risk:
        if not ctx.cfg.paper_trading:
            set_entry_status(
                ctx, ticker, "blocked:can_close_early",
                f"[entry] BLOCKED {ticker}: can_close_early=true "
                "outside paper mode")
            return
        set_entry_status(
            ctx, ticker, "paper_allowed:can_close_early",
            f"[entry] PAPER-ONLY {ticker}: can_close_early=true; "
            "entry remains enabled")
    else:
        set_entry_status(ctx, ticker, "eligible")
    _, _, _, ask_qty = ctx.feed.top_of_book(ticker)
    entry_sig = ctx.strategy.check_entry(ticker, hist, now,
                                         mid, bid, ask, ask_qty=ask_qty)
    if entry_sig:
        if bool(getattr(ctx.cfg, "one_contract_per_event", True)):
            conflict = _same_event_exposure(ctx, ticker)
            if conflict is not None:
                set_entry_status(
                    ctx, ticker, "blocked:event_sibling",
                    f"[entry] BLOCKED {ticker}: already exposed on "
                    f"same event via {conflict}")
                return
        spike = _sibling_spike_block(
            ctx, ticker, now, supplied=current_siblings)
        if spike is not None:
            set_entry_status(
                ctx, ticker, "blocked:sibling_spike",
                f"[entry] BLOCKED {ticker}: {spike}")
            return
        gate_block = _gate_block(current_gate)
        if gate_block is not None:
            set_entry_status(
                ctx, ticker, f"blocked:score:{gate_block[:40]}",
                f"[entry] BLOCKED {ticker}: {gate_block}")
            return
        if current_gate.get("enabled"):
            entry_sig = dict(entry_sig)
            entry_sig["reason"] = (
                f"{entry_sig['reason']}; {current_gate['reason']}")
        print(f"[signal] BUY {ticker}: {entry_sig['reason']}")
        size = entry_sig.get("contracts", ctx.cfg.contracts_per_trade)
        if ctx.cfg.paper_trading:
            ctx.executor.submit_paper(
                ticker, "BUY", size, entry_sig["reason"], now=now)
            return
        result = ctx.executor.execute(
            ticker, "BUY", size,
            expected_pre_position=Decimal(0), max_entry_price=ask)
        sync_execution_observation(ctx, ticker)
        if result:
            outcome_id = getattr(ctx.executor, "last_outcome_id", None)
            ctx.strategy.record_fill(ticker, "BUY", *result, now=now,
                                     event_id=outcome_id)
            journal = getattr(ctx.executor, "journal", None)
            if journal and outcome_id:
                journal.record("applied", order_id=outcome_id)
            if ctx.log:
                ctx.log.trade(ticker, "BUY", result[0], result[1],
                              entry_sig["reason"], fee=result[2],
                              ts=now)
        check_loss_limit(ctx)


def open_pnl_usd(ctx):
    """Conservative executable-depth mark, including unpriced inventory."""
    total = Decimal(0)
    for t, pos in ctx.strategy.positions.items():
        bid = ctx.latest_bid.get(t)
        if bid is not None:
            _, bid_qty, _, _ = (ctx.feed.top_of_book(t)
                                if ctx.feed is not None
                                else (bid, None, None, None))
            contracts = Decimal(str(pos.contracts))
            available = (Decimal(0) if bid_qty is None
                         else max(Decimal(0), Decimal(str(bid_qty))))
            executable = min(contracts, available)
            exit_price = max(
                Decimal(0), Decimal(str(bid))
                - Decimal(str(ctx.cfg.sim_slippage_cents)))
            proceeds = exit_price * executable / Decimal(100)
            exit_fee = (fee_usd(
                exit_price, executable, side="SELL",
                balance_precision_usd=ctx.cfg.balance_precision_usd)
                        if executable else Decimal(0))
            cost = (Decimal(str(pos.entry_price)) * contracts / Decimal(100)
                    + Decimal(str(pos.entry_fee_usd)))
            total += proceeds - exit_fee - cost
    return total


def check_loss_limit(ctx):
    """Missing-bid risk: an open position whose market has no FRESH bid
    cannot be valued — that is itself a halt condition, not a free pass."""
    now = ctx.clock()
    ctx.strategy.refresh_daily_pnl(now)
    for t in ctx.strategy.positions:
        ts = ctx.bid_ts.get(t)
        if ts is None or now - ts > ctx.cfg.stale_data_s:
            ctx.safety.trip(f"cannot value open position {t}: "
                            f"no fresh bid (> {ctx.cfg.stale_data_s}s)")
            return
    total = ctx.strategy.realized_pnl + open_pnl_usd(ctx)
    if total <= -Decimal(str(ctx.cfg.max_daily_loss_usd)):
        ctx.safety.trip(f"loss limit incl. open positions "
                        f"({total:+.2f} USD)")


def flatten_all(ctx):
    """Close every open position, with retries for partial fills.
    Live: refuses if the journal holds unresolved (ambiguous) orders —
    flattening on top of an ambiguous SELL risks a duplicate SELL — and
    sizes each close from AUTHORITATIVE exchange positions."""
    ex = ctx.executor
    live = not ctx.cfg.paper_trading
    if live and ex.journal and ex.journal.unresolved():
        raise ExposureError(
            "unresolved orders in journal; auto-flatten could duplicate an "
            "in-flight order")
    if live and ex.journal and ex.journal.unapplied_outcomes():
        raise ExposureError(
            "unapplied filled outcome; local position/P&L state is unsafe")

    if live:
        def authoritative_positions():
            try:
                rows = ex.client.get_positions()
            except Exception as e:
                raise ExposureError(
                    f"authoritative positions unavailable: {e}") from e
            result = {}
            for row in rows:
                if row["ticker"] in result:
                    raise ExposureError(
                        f"duplicate position rows for {row['ticker']}")
                if row["position"]:
                    result[row["ticker"]] = row["position"]
            return result

        for _ in range(ctx.cfg.flatten_retries):
            api_pos = authoritative_positions()
            if not api_pos:
                if ctx.strategy.positions:
                    raise ExposureError(
                        "exchange is flat but local position book is not; "
                        "P&L state requires manual reconciliation")
                return
            local_pos = {
                ticker: Decimal(str(position.contracts))
                for ticker, position in ctx.strategy.positions.items()
            }
            if api_pos != local_pos or any(qty <= 0 for qty in api_pos.values()):
                raise ExposureError(
                    "authoritative exposure lacks an exact local position "
                    "and cost basis; manual reconciliation required")
            for ticker, authoritative in api_pos.items():
                side = "SELL"
                qty = authoritative
                try:
                    result = ex.execute(
                        ticker, side, qty,
                        expected_pre_position=authoritative)
                except (HaltError, UnknownOrderState) as e:
                    raise ExposureError(
                        f"flatten failed for {ticker}: {e}") from e
                if result and ticker in ctx.strategy.positions:
                    outcome_id = getattr(ex, "last_outcome_id", None)
                    ctx.strategy.record_fill(ticker, "SELL", *result,
                                             now=ctx.clock(),
                                             event_id=outcome_id)
                    journal = getattr(ex, "journal", None)
                    if journal and outcome_id:
                        journal.record("applied", order_id=outcome_id)
        residual = authoritative_positions()
        if residual:
            raise ExposureError(
                "account not flat after bounded retries: "
                + ", ".join(f"{t}={q}" for t, q in residual.items()))
        if ctx.strategy.positions:
            raise ExposureError(
                "exchange is flat but local position book is not")
        return

    # A paper position cannot be honestly flattened without a newly observed
    # quote. Reusing the last book would consume the same displayed depth
    # repeatedly and fabricate fills.
    if hasattr(ex, "cancel_pending_paper"):
        ex.cancel_pending_paper()
    if ctx.strategy.positions:
        raise ExposureError(
            "paper positions require future quotes; retained as residual")
