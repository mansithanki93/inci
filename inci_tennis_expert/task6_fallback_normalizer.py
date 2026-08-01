from __future__ import annotations

from .contracts import (
    ExpertIgnoreReasonV1,
    ExpertIgnoredDraftV1,
)


__all__ = ("normalize_task6_fallback",)


def normalize_task6_fallback(_: object) -> tuple[ExpertIgnoredDraftV1, ...]:
    return (
        ExpertIgnoredDraftV1(
            ExpertIgnoreReasonV1.NORMALIZER_NOT_REGISTERED
        ),
    )
