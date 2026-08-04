from __future__ import annotations

from inci_tennis_expert.engine import (
    ClipBundle,
    ClipObservation,
    make_default_clip_bundle,
    observe_clip_on_transition,
)


class PaperClipSession:
    """In-memory paper clip observer over trusted sync transitions.

    Never places orders. Open inventory is updated only by sealed expert
    engine observations returned from each transition.
    """

    def __init__(self, bundle: ClipBundle) -> None:
        if type(bundle) is not ClipBundle:
            raise TypeError("bundle")
        self._bundle = bundle
        self._positions: dict = {}

    @classmethod
    def with_default_bundle(
        cls,
        *,
        require_calibration: bool = True,
        max_holding_wall_ns: int = 300_000_000_000,
    ) -> PaperClipSession:
        return cls(
            make_default_clip_bundle(
                require_calibration=require_calibration,
                max_holding_wall_ns=max_holding_wall_ns,
            )
        )

    @property
    def bundle(self) -> ClipBundle:
        return self._bundle

    def open_tickers(self) -> tuple[str, ...]:
        return tuple(sorted(self._positions))

    def observe(
        self,
        transition: object,
        prior: object,
        *,
        calibration: object | None = None,
    ) -> tuple[ClipObservation, ...]:
        return observe_clip_on_transition(
            transition,  # type: ignore[arg-type]
            prior,  # type: ignore[arg-type]
            self._bundle,
            self._positions,
            calibration=calibration,  # type: ignore[arg-type]
        )


__all__ = (
    "PaperClipSession",
    "ClipBundle",
    "ClipObservation",
    "make_default_clip_bundle",
)
