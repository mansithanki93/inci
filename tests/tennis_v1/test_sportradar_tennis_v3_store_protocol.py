from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import unittest

from inci_tennis_expert.contracts import canonical_expert_bytes
from inci_tennis_io import facade
from tennis_v1.session import session_manifest_sha256
from tests.tennis_v1 import test_sportradar_tennis_v3 as _existing
from tests.tennis_v1.sportradar_candidate_fixture_support import (
    capture_public_candidate_fixture,
    capture_transport_candidate_fixture,
)


class SportradarCandidateStoreProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        _existing.CandidateOutputWriterTests.setUp(self)

    def tearDown(self) -> None:
        _existing.CandidateOutputWriterTests.tearDown(self)

    def _new_writer(
        self,
        *,
        output_parent: Path | None = None,
        requested_provider_match_ids: tuple[str, ...] = (
            "synthetic-match",
        ),
    ) -> tuple[object, object, dict[str, object]]:
        manifest, values = (
            _existing.CandidateOutputWriterTests._artifacts(
                self,
                requested_provider_match_ids=requested_provider_match_ids,
            )
        )
        writer = facade.create_sportradar_candidate_output_writer(
            self.root_authority,
            output_parent=str(
                self.output_parent if output_parent is None else output_parent
            ),
            session_manifest=manifest,
            session_manifest_sha256=session_manifest_sha256(manifest),
            **values,
        )
        return writer, manifest, values

    @staticmethod
    def _quota_coordinates(
        active_subscriptions: int = 1,
    ) -> tuple[tuple[str, int], ...]:
        return (
            ("rolling_second_attempts", 0),
            ("rolling_60_seconds_attempts", 0),
            ("utc_day_attempts", 0),
            ("active_connections", 1),
            ("active_subscriptions", active_subscriptions),
            ("rolling_hour_resync_attempts", 0),
        )

    def _append_first_permit(
        self,
        writer: object,
        manifest: object,
        *,
        route: str,
        provider_match_id: str = "synthetic-match",
        active_subscriptions: int = 1,
    ) -> object:
        quota_coordinates = self._quota_coordinates(active_subscriptions)
        permit_sha256 = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-READ-PERMIT-V1\0"
            + canonical_expert_bytes(
                (
                    ("session_id", manifest.session_id),
                    ("route", route),
                    ("provider_match_id", provider_match_id),
                    ("resync", False),
                    ("connection_epoch", 1),
                    ("quota_coordinates", quota_coordinates),
                    (
                        "previous_record_sha256",
                        manifest.qualification_trace_sha256,
                    ),
                )
            )
        ).hexdigest()
        return facade.append_sportradar_candidate_permit(
            writer,
            prior_receipt=None,
            route=route,
            provider_match_id=provider_match_id,
            resync=False,
            connection_epoch=1,
            permit_sha256=permit_sha256,
            quota_coordinates=quota_coordinates,
        )

    @staticmethod
    def _captured(
        manifest: object,
        *,
        event_type: str,
        provider_match_id: str = "synthetic-match",
    ) -> object:
        return capture_public_candidate_fixture(
            b"{}",
            manifest=manifest,
            source_entity_id=provider_match_id,
            session_start_wall_ns=100,
            local_wall_ns=101,
            local_monotonic_ns=700,
            clock_uncertainty_ns=2,
            event_type=event_type,
            source_wall_ns=100,
            source_generated_ns=100,
            provider_sequence="c0.r1",
        )

    @staticmethod
    def _capabilities() -> tuple[tuple[str, bool], ...]:
        return (
            ("correction_semantics", True),
            ("current_server", True),
            ("match_format", True),
            ("monotonic_sequence_or_revision", True),
            ("point_state", True),
            ("provider_generated_time", True),
            ("resync_snapshot", True),
            ("source_event_time", True),
            ("stable_match_ids", True),
            ("stable_player_ids", True),
        )

    def test_output_parent_rejects_state_root_descendants_and_aliases(
        self,
    ) -> None:
        manifest, values = (
            _existing.CandidateOutputWriterTests._artifacts(self)
        )
        state_root = self.root_path / "state"
        direct = state_root / "candidate-output"
        direct.mkdir(mode=0o700)
        alias_target = state_root / "candidate-output-alias-target"
        alias_target.mkdir(mode=0o700)
        alias = self.root_path / "state-alias"
        os.symlink(state_root, alias)

        for output_parent in (direct, alias / alias_target.name):
            with self.subTest(output_parent=output_parent):
                with self.assertRaisesRegex(
                    ValueError,
                    "^candidate_output_contract_invalid$",
                ):
                    facade.create_sportradar_candidate_output_writer(
                        self.root_authority,
                        output_parent=str(output_parent),
                        session_manifest=manifest,
                        session_manifest_sha256=(
                            session_manifest_sha256(manifest)
                        ),
                        **values,
                    )
                self.assertEqual(tuple(output_parent.iterdir()), ())

    def test_capture_must_match_preceding_permit_route(self) -> None:
        writer, manifest, _ = self._new_writer()
        try:
            permit = self._append_first_permit(
                writer,
                manifest,
                route="summary",
            )
            timeline = self._captured(
                manifest,
                event_type="sportradar_tennis_timeline_v3",
            )
            with self.assertRaisesRegex(
                ValueError,
                "^candidate_output_contract_invalid$",
            ):
                facade.append_sportradar_candidate_capture(
                    writer,
                    prior_receipt=permit,
                    captured=timeline,
                )
            facade.append_sportradar_candidate_capture(
                writer,
                prior_receipt=permit,
                captured=self._captured(
                    manifest,
                    event_type="sportradar_tennis_summary_v3",
                ),
            )
        finally:
            facade.abort_sportradar_candidate_output(writer)

        writer, manifest, _ = self._new_writer(
            requested_provider_match_ids=(
                "synthetic-match-a",
                "synthetic-match-b",
            )
        )
        try:
            permit = self._append_first_permit(
                writer,
                manifest,
                route="summary",
                provider_match_id="synthetic-match-a",
                active_subscriptions=2,
            )
            with self.assertRaisesRegex(
                ValueError,
                "^candidate_output_contract_invalid$",
            ):
                facade.append_sportradar_candidate_capture(
                    writer,
                    prior_receipt=permit,
                    captured=self._captured(
                        manifest,
                        event_type="sportradar_tennis_summary_v3",
                        provider_match_id="synthetic-match-b",
                    ),
                )
            facade.append_sportradar_candidate_capture(
                writer,
                prior_receipt=permit,
                captured=self._captured(
                    manifest,
                    event_type="sportradar_tennis_summary_v3",
                    provider_match_id="synthetic-match-a",
                ),
            )
        finally:
            facade.abort_sportradar_candidate_output(writer)

    def test_transport_capture_is_lawful_after_either_permit_route(
        self,
    ) -> None:
        for route in ("summary", "timeline"):
            with self.subTest(route=route):
                writer, manifest, _ = self._new_writer()
                try:
                    permit = self._append_first_permit(
                        writer,
                        manifest,
                        route=route,
                    )
                    captured = capture_transport_candidate_fixture(
                        manifest=manifest,
                        source_entity_id="synthetic-match",
                        session_start_wall_ns=100,
                        local_wall_ns=101,
                        local_monotonic_ns=700,
                        clock_uncertainty_ns=2,
                        exception_type="timeout_error",
                        status_code=None,
                        error_code="timeout",
                    )
                    capture = facade.append_sportradar_candidate_capture(
                        writer,
                        prior_receipt=permit,
                        captured=captured,
                    )
                    capabilities = tuple(
                        (name, False)
                        for name, _ in self._capabilities()
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        "^candidate_output_contract_invalid$",
                    ):
                        facade.append_sportradar_candidate_parser_result(
                            writer,
                            prior_receipt=capture,
                            capture_receipt=capture,
                            evidence_sha256="a" * 64,
                            parser_outcome="ignored",
                            reason="event_not_relevant",
                            output_contract_sha256s=(),
                            capabilities=capabilities,
                            first_correction_epoch=None,
                            first_revision=None,
                            last_correction_epoch=None,
                            last_revision=None,
                        )
                    facade.append_sportradar_candidate_parser_result(
                        writer,
                        prior_receipt=capture,
                        capture_receipt=capture,
                        evidence_sha256=(
                            _existing._candidate_parser_evidence_sha256(
                                captured,
                                parser_outcome="ignored",
                                reason="event_not_relevant",
                                output_contract_sha256s=(),
                                capabilities=capabilities,
                                first_correction_epoch=None,
                                first_revision=None,
                                last_correction_epoch=None,
                                last_revision=None,
                            )
                        ),
                        parser_outcome="ignored",
                        reason="event_not_relevant",
                        output_contract_sha256s=(),
                        capabilities=capabilities,
                        first_correction_epoch=None,
                        first_revision=None,
                        last_correction_epoch=None,
                        last_revision=None,
                    )
                finally:
                    facade.abort_sportradar_candidate_output(writer)

    def test_parser_result_closes_reason_and_coordinate_contract(
        self,
    ) -> None:
        writer, manifest, _ = self._new_writer()
        try:
            permit = self._append_first_permit(
                writer,
                manifest,
                route="summary",
            )
            capture = facade.append_sportradar_candidate_capture(
                writer,
                prior_receipt=permit,
                captured=self._captured(
                    manifest,
                    event_type="sportradar_tennis_summary_v3",
                ),
            )
            for parser_outcome, reason, coordinates in (
                (
                    "ignored",
                    "attacker_reason",
                    (None, None, None, None),
                ),
                (
                    "rejected",
                    "attacker_reason",
                    (None, None, None, None),
                ),
                (
                    "rejected",
                    "parent_contract_invalid",
                    (None, None, None, None),
                ),
                (
                    "ignored",
                    "event_not_relevant",
                    (None, None, None, None),
                ),
                (
                    "accepted",
                    None,
                    (1, 2, 0, 9),
                ),
                (
                    "accepted",
                    None,
                    (0, 0, 9_223_372_036_854_775_808, 0),
                ),
            ):
                with self.subTest(
                    parser_outcome=parser_outcome,
                    reason=reason,
                    coordinates=coordinates,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "^candidate_output_contract_invalid$",
                    ):
                        facade.append_sportradar_candidate_parser_result(
                            writer,
                            prior_receipt=capture,
                            capture_receipt=capture,
                            evidence_sha256="a" * 64,
                            parser_outcome=parser_outcome,
                            reason=reason,
                            output_contract_sha256s=(
                                ("b" * 64,)
                                if parser_outcome == "accepted"
                                else ()
                            ),
                            capabilities=self._capabilities(),
                            first_correction_epoch=coordinates[0],
                            first_revision=coordinates[1],
                            last_correction_epoch=coordinates[2],
                            last_revision=coordinates[3],
                        )
            with self.assertRaisesRegex(
                ValueError,
                "^candidate_output_contract_invalid$",
            ):
                facade.append_sportradar_candidate_parser_result(
                    writer,
                    prior_receipt=capture,
                    capture_receipt=capture,
                    evidence_sha256="a" * 64,
                    parser_outcome="accepted",
                    reason=None,
                    output_contract_sha256s=("b" * 64, "c" * 64),
                    capabilities=self._capabilities(),
                    first_correction_epoch=0,
                    first_revision=0,
                    last_correction_epoch=0,
                    last_revision=0,
                )
        finally:
            facade.abort_sportradar_candidate_output(writer)

    def test_candidate_parser_value_contract_uses_output_reason_vocabulary(
        self,
    ) -> None:
        from inci_tennis_adapters.candidate_contracts import (
            CandidateParserEvidenceV1,
        )

        common = {
            "schema_version": 1,
            "event_type": "sportradar_tennis_summary_v3",
            "event_version": 1,
            "payload_sha256": "a" * 64,
            "capture_envelope_sha256": "b" * 64,
            "parser_outcome": "rejected",
            "output_contract_sha256s": (),
            "capabilities": self._capabilities(),
            "first_correction_epoch": None,
            "first_revision": None,
            "last_correction_epoch": None,
            "last_revision": None,
        }
        with self.assertRaisesRegex(
            ValueError,
            "^candidate_contract_invalid$",
        ):
            CandidateParserEvidenceV1._create(
                **common,
                reason="parent_contract_invalid",
            )
        evidence = CandidateParserEvidenceV1._create(
            **common,
            reason="normalizer_exception",
        )
        self.assertEqual(evidence.reason, "normalizer_exception")

    def test_failure_stage_must_match_protocol_position(self) -> None:
        writer, manifest, _ = self._new_writer()
        try:
            with self.assertRaisesRegex(
                ValueError,
                "^candidate_output_contract_invalid$",
            ):
                facade.append_sportradar_candidate_failure(
                    writer,
                    prior_receipt=None,
                    stage="parser",
                    failure_code="parser_rejected",
                    permit_receipt=None,
                    capture_receipt=None,
                )
            permit = self._append_first_permit(
                writer,
                manifest,
                route="summary",
            )
            with self.assertRaisesRegex(
                ValueError,
                "^candidate_output_contract_invalid$",
            ):
                facade.append_sportradar_candidate_failure(
                    writer,
                    prior_receipt=permit,
                    stage="parser",
                    failure_code="parser_rejected",
                    permit_receipt=permit,
                    capture_receipt=None,
                )
            capture = facade.append_sportradar_candidate_capture(
                writer,
                prior_receipt=permit,
                captured=self._captured(
                    manifest,
                    event_type="sportradar_tennis_summary_v3",
                ),
            )
            with self.assertRaisesRegex(
                ValueError,
                "^candidate_output_contract_invalid$",
            ):
                facade.append_sportradar_candidate_failure(
                    writer,
                    prior_receipt=capture,
                    stage="permit",
                    failure_code="quota_denied",
                    permit_receipt=None,
                    capture_receipt=capture,
                )
            failure = facade.append_sportradar_candidate_failure(
                writer,
                prior_receipt=capture,
                stage="parser",
                failure_code="parser_rejected",
                permit_receipt=None,
                capture_receipt=capture,
            )
            with self.assertRaisesRegex(
                ValueError,
                "^candidate_output_contract_invalid$",
            ):
                facade.append_sportradar_candidate_failure(
                    writer,
                    prior_receipt=failure,
                    stage="permit",
                    failure_code="quota_denied",
                    permit_receipt=None,
                    capture_receipt=None,
                )
            commit = facade.finalize_sportradar_candidate_output(
                writer,
                prior_receipt=failure,
                terminal_reason="parser_rejected",
            )
            self.assertEqual(commit.terminal_reason, "parser_rejected")
            writer = None
        finally:
            if writer is not None:
                facade.abort_sportradar_candidate_output(writer)

    def test_every_terminal_rejects_unmatched_protocol_records(self) -> None:
        writer, manifest, _ = self._new_writer()
        permit = self._append_first_permit(
            writer,
            manifest,
            route="summary",
        )
        with self.assertRaisesRegex(
            ValueError,
            "^candidate_output_contract_invalid$",
        ):
            facade.finalize_sportradar_candidate_output(
                writer,
                prior_receipt=permit,
                terminal_reason="operator_stop",
            )

    def test_failure_terminal_reason_is_exactly_correlated(self) -> None:
        writer, _, _ = self._new_writer()
        failure = facade.append_sportradar_candidate_failure(
            writer,
            prior_receipt=None,
            stage="permit",
            failure_code="quota_denied",
            permit_receipt=None,
            capture_receipt=None,
        )
        with self.assertRaisesRegex(
            ValueError,
            "^candidate_output_contract_invalid$",
        ):
            facade.finalize_sportradar_candidate_output(
                writer,
                prior_receipt=failure,
                terminal_reason="operator_stop",
            )


if __name__ == "__main__":
    unittest.main()
