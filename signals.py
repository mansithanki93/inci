"""The single, shared entry rule. The live bot and the analyzer both call
this so they cannot drift apart (fix #5). Strictly causal: `history` must
contain only ticks BEFORE the current one."""


def dip_signal_with_reason(history, now_ts, current_mid, dip_threshold,
                           lookback_seconds):
    """Return the dip signal and the rejection reason when it is absent."""
    window = [m for t, m in history
              if 0 < now_ts - t <= lookback_seconds]
    if not window:
        return None, "missing_history"
    dip = max(window) - current_mid
    if dip < dip_threshold:
        return None, "dip_below_threshold"
    return dip, None


def dip_signal(history, now_ts, current_mid, dip_threshold, lookback_seconds):
    """history: iterable of (ts, mid) from ticks strictly before now.
    Returns dip size in cents if triggered, else None."""
    return dip_signal_with_reason(
        history, now_ts, current_mid, dip_threshold, lookback_seconds)[0]
