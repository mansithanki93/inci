from __future__ import annotations

from decimal import Decimal
import sys

from inci_tennis_expert.contracts import (
    MarketStatus,
    MatchFormat,
    MatchStatus,
    PlayerSide,
    SyncReason,
)
from .shadow_runtime import (
    ContractMonitorView,
    MatchMonitorView,
    SyncDisplayState,
    render_monitor,
)


_USAGE = (
    "usage: python -m inci_tennis_runtime.shadow_cli --sample "
    "[--all-stages] [--no-ansi] [--width 60..240]"
)


def terminal_frame(
    text: str,
    *,
    is_tty: bool,
    no_ansi: bool = False,
) -> str:
    if type(text) is not str:
        raise TypeError("text")
    if type(is_tty) is not bool or type(no_ansi) is not bool:
        raise TypeError("terminal")
    value = text.rstrip("\n") + "\n"
    if is_tty and not no_ansi:
        return "\x1b[2J\x1b[H" + value
    return value


def _sample_contract(
    side: PlayerSide,
    *,
    trusted: bool,
) -> ContractMonitorView:
    if side is PlayerSide.HOME:
        player_id = "sample-home-player"
        ticker = "SAMPLETENNISHOME"
        bid = Decimal("0.42")
        ask = Decimal("0.45")
        bid_quantity = Decimal("18")
        ask_quantity = Decimal("12")
        sequence = 44
    else:
        player_id = "sample-away-player"
        ticker = "SAMPLETENNISAWAY"
        bid = Decimal("0.55")
        ask = Decimal("0.58")
        bid_quantity = Decimal("12")
        ask_quantity = Decimal("18")
        sequence = 45
    if not trusted:
        return ContractMonitorView(
            player_side=side,
            player_id=player_id,
            ticker=ticker,
            market_status=None,
            yes_bid=None,
            yes_ask=None,
            bid_quantity=None,
            ask_quantity=None,
            spread=None,
            book_age_ns=None,
            connection_epoch=None,
            sequence=None,
            sync_state=SyncDisplayState.WAITING,
            reason=SyncReason.SNAPSHOT_INCOMPLETE,
            evaluated_monotonic_ns=None,
        )
    return ContractMonitorView(
        player_side=side,
        player_id=player_id,
        ticker=ticker,
        market_status=MarketStatus.OPEN,
        yes_bid=bid,
        yes_ask=ask,
        bid_quantity=bid_quantity,
        ask_quantity=ask_quantity,
        spread=ask - bid,
        book_age_ns=120_000_000,
        connection_epoch=3,
        sequence=sequence,
        sync_state=SyncDisplayState.TRUSTED,
        reason=SyncReason.TRUSTED_SYNCHRONIZED,
        evaluated_monotonic_ns=5_000_000_000,
    )


def sample_monitor_views() -> tuple[MatchMonitorView, ...]:
    home_waiting = _sample_contract(PlayerSide.HOME, trusted=False)
    away_waiting = _sample_contract(PlayerSide.AWAY, trusted=False)
    waiting = MatchMonitorView(
        mode_label="SYNTHETIC DISPLAY SAMPLE / READ-ONLY",
        canonical_match_id="sample-match-001",
        provider_match_id="sample-provider-match-001",
        event_ticker="SAMPLETENNISMATCH",
        match_format=MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        home_player_id="sample-home-player",
        away_player_id="sample-away-player",
        match_status=None,
        completed_sets=(),
        games=None,
        points=None,
        server=None,
        provider_revision=None,
        provider_age_ns=None,
        observed_wall_ns=None,
        observed_monotonic_ns=None,
        decision_sequence=0,
        contracts=(home_waiting, away_waiting),
    )
    home_trusted = _sample_contract(PlayerSide.HOME, trusted=True)
    provider_ready = MatchMonitorView(
        mode_label="SYNTHETIC DISPLAY SAMPLE / READ-ONLY",
        canonical_match_id="sample-match-001",
        provider_match_id="sample-provider-match-001",
        event_ticker="SAMPLETENNISMATCH",
        match_format=MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        home_player_id="sample-home-player",
        away_player_id="sample-away-player",
        match_status=MatchStatus.LIVE,
        completed_sets=(),
        games=(2, 1),
        points=("30", "15"),
        server=PlayerSide.HOME,
        provider_revision=18,
        provider_age_ns=80_000_000,
        observed_wall_ns=2_000_000_000,
        observed_monotonic_ns=5_000_000_000,
        decision_sequence=1,
        contracts=(home_trusted, away_waiting),
    )
    away_trusted = _sample_contract(PlayerSide.AWAY, trusted=True)
    synchronized = MatchMonitorView(
        mode_label="SYNTHETIC DISPLAY SAMPLE / READ-ONLY",
        canonical_match_id="sample-match-001",
        provider_match_id="sample-provider-match-001",
        event_ticker="SAMPLETENNISMATCH",
        match_format=MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        home_player_id="sample-home-player",
        away_player_id="sample-away-player",
        match_status=MatchStatus.LIVE,
        completed_sets=((6, 4),),
        games=(1, 2),
        points=("15", "30"),
        server=PlayerSide.AWAY,
        provider_revision=37,
        provider_age_ns=40_000_000,
        observed_wall_ns=2_100_000_000,
        observed_monotonic_ns=5_100_000_000,
        decision_sequence=2,
        contracts=(home_trusted, away_trusted),
    )
    return waiting, provider_ready, synchronized


def _decode_arguments(
    arguments: tuple[str, ...],
) -> tuple[bool, bool, int] | None:
    if type(arguments) is not tuple or arguments.count("--sample") != 1:
        return None
    all_stages = False
    no_ansi = False
    width = 120
    seen: set[str] = set()
    index = 0
    while index < len(arguments):
        value = arguments[index]
        if value == "--sample":
            if value in seen:
                return None
            seen.add(value)
        elif value in {"--all-stages", "--no-ansi"}:
            if value in seen:
                return None
            seen.add(value)
            if value == "--all-stages":
                all_stages = True
            else:
                no_ansi = True
        elif value == "--width":
            if value in seen or index + 1 >= len(arguments):
                return None
            seen.add(value)
            index += 1
            try:
                width = int(arguments[index])
            except ValueError:
                return None
            if width < 60 or width > 240:
                return None
        else:
            return None
        index += 1
    return all_stages, no_ansi, width


def _safe_render(view: MatchMonitorView, width: int) -> tuple[str, bool]:
    try:
        return render_monitor(view, width=width), False
    except Exception as error:
        return (
            "INCI TENNIS SHADOW | RENDER ERROR | NO ORDERS\n"
            f"reason={type(error).__name__}",
            True,
        )


def main(
    arguments: tuple[str, ...] | None = None,
    *,
    output: object | None = None,
    is_tty: bool | None = None,
) -> int:
    values = tuple(sys.argv[1:]) if arguments is None else arguments
    destination = sys.stdout if output is None else output
    decoded = _decode_arguments(values)
    if decoded is None:
        destination.write(_USAGE + "\n")
        return 2
    all_stages, no_ansi, width = decoded
    if is_tty is None:
        is_tty = bool(destination.isatty())
    views = sample_monitor_views()
    if all_stages:
        for index, view in enumerate(views, start=1):
            rendered, failed = _safe_render(view, width)
            if failed:
                destination.write(rendered + "\n")
                return 1
            if index > 1:
                destination.write("\n")
            destination.write(f"SAMPLE STAGE {index}/{len(views)}\n")
            destination.write(rendered + "\n")
        return 0
    rendered, failed = _safe_render(views[-1], width)
    if failed:
        destination.write(rendered + "\n")
        return 1
    destination.write(
        terminal_frame(
            rendered,
            is_tty=is_tty,
            no_ansi=no_ansi,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "main",
    "sample_monitor_views",
    "terminal_frame",
)
