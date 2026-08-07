from __future__ import annotations

from inci_tennis_expert.facade import (
    ClipBundle,
    ClipJournalRecordV1,
    ClipObservation,
    clip_record_from_observation,
    encode_clip_journal_records,
    make_default_clip_bundle,
    observe_clip_on_transition,
    serialize_clip_journal_document,
    verify_clip_record_matches_observation,
)


class PaperClipSession:
    """In-memory paper clip observer with durable companion journal records.

    Never places orders. Open inventory and journal records update only from
    sealed expert observations returned for each trusted transition.
    """

    def __init__(
        self,
        bundle: ClipBundle,
        *,
        session_id: str = "paper-clip-session",
    ) -> None:
        if type(bundle) is not ClipBundle:
            raise TypeError("bundle")
        if type(session_id) is not str or not session_id:
            raise TypeError("session_id")
        self._bundle = bundle
        self._session_id = session_id
        self._positions: dict = {}
        self._records: list = []

    @classmethod
    def with_default_bundle(
        cls,
        *,
        require_calibration: bool = True,
        max_holding_wall_ns: int = 300_000_000_000,
        session_id: str = "paper-clip-session",
    ) -> PaperClipSession:
        return cls(
            make_default_clip_bundle(
                require_calibration=require_calibration,
                max_holding_wall_ns=max_holding_wall_ns,
            ),
            session_id=session_id,
        )

    @property
    def bundle(self) -> ClipBundle:
        return self._bundle

    @property
    def session_id(self) -> str:
        return self._session_id

    def open_tickers(self) -> tuple[str, ...]:
        return tuple(sorted(self._positions))

    def journal_records(self) -> tuple[ClipJournalRecordV1, ...]:
        return tuple(self._records)

    def journal_bundle_bytes(self) -> bytes:
        return encode_clip_journal_records(self.journal_records())

    def journal_document_bytes(self) -> bytes:
        return serialize_clip_journal_document(self.journal_records())

    def observe(
        self,
        transition: object,
        prior: object,
        *,
        calibration: object | None = None,
        calibration_artifact_sha256: str | None = None,
    ) -> tuple[ClipObservation, ...]:
        observations = observe_clip_on_transition(
            transition,  # type: ignore[arg-type]
            prior,  # type: ignore[arg-type]
            self._bundle,
            self._positions,
            calibration=calibration,  # type: ignore[arg-type]
        )
        for observation in observations:
            record = clip_record_from_observation(
                observation,
                session_id=self._session_id,
                record_sequence=len(self._records) + 1,
                prior=prior,  # type: ignore[arg-type]
                bundle=self._bundle,
                calibration_artifact_sha256=calibration_artifact_sha256,
            )
            verify_clip_record_matches_observation(
                record,
                observation,
                prior=prior,  # type: ignore[arg-type]
                bundle=self._bundle,
                calibration_artifact_sha256=calibration_artifact_sha256,
            )
            self._records.append(record)
        return observations


__all__ = (
    "PaperClipSession",
    "ClipBundle",
    "ClipObservation",
    "ClipJournalRecordV1",
    "make_default_clip_bundle",
)
