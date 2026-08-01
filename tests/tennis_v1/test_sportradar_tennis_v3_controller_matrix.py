from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import unittest
from unittest import mock

from inci_tennis_expert.contracts import canonical_expert_bytes
from inci_tennis_io import facade
from tennis_v1.session import session_manifest_sha256
from tests.tennis_v1 import test_sportradar_tennis_v3 as _existing
from tests.tennis_v1.sportradar_candidate_fixture_support import (
    capture_public_candidate_fixture,
    capture_redacted_candidate_fixture,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class SportradarCandidateControllerMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        _existing.CandidateOutputWriterTests.setUp(self)

    def tearDown(self) -> None:
        _existing.CandidateOutputWriterTests.tearDown(self)

    def _eligible_candidate_inputs(
        self,
        source_seals: object,
        *,
        required_capabilities: tuple[str, ...] | None = None,
    ) -> object:
        return (
            _existing.CandidateOutputWriterTests
            ._eligible_candidate_inputs(
                self,
                source_seals,
                required_capabilities=required_capabilities,
            )
        )

    def test_q9_capture_identity_is_retained_across_fsync_before_parse(
        self,
    ) -> None:
        import inci_tennis_runtime.provider_qualification_controller as controller
        from inci_tennis_io.provider_readonly import (
            build_sportradar_candidate_session_manifest,
        )

        seal_authority = (
            facade.issue_sportradar_candidate_source_seal_collection_authority(
                self.root_authority
            )
        )
        source_seals = facade.collect_sportradar_candidate_source_seals(
            seal_authority
        )
        artifacts = self._eligible_candidate_inputs(source_seals)
        decision = controller.evaluate_sportradar_candidate_offline(
            artifacts=artifacts,
            source_seals=source_seals,
        )
        self.assertTrue(decision.eligible_for_candidate_observation)
        self.assertIsNotNone(decision.binding)
        self.assertIsNotNone(decision.quota)
        binding = decision.binding
        quota = decision.quota
        manifest = build_sportradar_candidate_session_manifest(
            artifacts=artifacts,
            source_seals=source_seals,
            decision=decision,
        )
        payload = (
            FIXTURES / "sportradar_tennis_summary_v3.json"
        ).read_bytes()
        captured = capture_public_candidate_fixture(
            payload,
            manifest=manifest,
            source_entity_id="synthetic-match-1",
            session_start_wall_ns=artifacts.session_start_wall_ns,
            local_wall_ns=(
                artifacts.session_start_wall_ns + 1_000_000_000
            ),
            local_monotonic_ns=700,
            clock_uncertainty_ns=2,
            event_type="sportradar_tennis_summary_v3",
            source_wall_ns=1_894_726_800_000_000_000,
            source_generated_ns=1_894_726_800_250_000_000,
            provider_sequence="c0.r0",
        )
        self.clock.now_ns = artifacts.session_start_wall_ns + 1
        writer = facade.create_sportradar_candidate_output_writer(
            self.root_authority,
            output_parent=str(self.output_parent),
            session_manifest=manifest,
            session_manifest_sha256=session_manifest_sha256(manifest),
            candidate_manifest_sha256=(
                artifacts.candidate_manifest_sha256
            ),
            manifest_core_sha256=artifacts.manifest_core_sha256,
            candidate_authorization_sha256=(
                artifacts.candidate_authorization_sha256
            ),
            candidate_decision_sha256=decision.decision_sha256,
            candidate_binding_sha256=binding.binding_sha256,
            quota_closure_sha256=quota.quota_closure_sha256,
            candidate_source_seals_sha256=(
                source_seals.candidate_source_seals_sha256
            ),
            match_binding_universe_sha256=(
                artifacts.universe.universe_sha256
            ),
            requested_provider_match_ids=(
                artifacts.requested_provider_match_ids
            ),
            session_start_wall_ns=artifacts.session_start_wall_ns,
            maximum_candidate_trace_bytes=(
                artifacts.maximum_candidate_trace_bytes
            ),
        )
        quota_coordinates = (
            ("rolling_second_attempts", 0),
            ("rolling_60_seconds_attempts", 0),
            ("utc_day_attempts", 0),
            ("active_connections", 0),
            ("active_subscriptions", 1),
            ("rolling_hour_resync_attempts", 0),
        )
        permit_sha256 = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-READ-PERMIT-V1\0"
            + canonical_expert_bytes(
                (
                    ("session_id", manifest.session_id),
                    ("route", "summary"),
                    ("provider_match_id", "synthetic-match-1"),
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
        permit = facade.append_sportradar_candidate_permit(
            writer,
            prior_receipt=None,
            route="summary",
            provider_match_id="synthetic-match-1",
            resync=False,
            connection_epoch=1,
            permit_sha256=permit_sha256,
            quota_coordinates=quota_coordinates,
        )

        original_append = facade.append_sportradar_candidate_capture
        original_inspect = controller.inspect_sportradar_candidate_capture
        order: list[str] = []

        def append_checked(*args: object, **kwargs: object):
            self.assertIs(kwargs["captured"], captured)
            receipt = original_append(*args, **kwargs)
            self.assertTrue(receipt.fsynced)
            order.append("capture_fsynced")
            return receipt

        def inspect_checked(*args: object, **kwargs: object):
            self.assertEqual(order, ["capture_fsynced"])
            self.assertIs(kwargs["captured"], captured)
            order.append("same_object_inspected")
            return original_inspect(*args, **kwargs)

        with (
            mock.patch.object(
                controller.facade,
                "append_sportradar_candidate_capture",
                side_effect=append_checked,
            ),
            mock.patch.object(
                controller,
                "inspect_sportradar_candidate_capture",
                side_effect=inspect_checked,
            ),
        ):
            evidence, parser_receipt = (
                controller
                .record_sportradar_candidate_capture_for_qualification(
                    writer=writer,
                    permit_receipt=permit,
                    binding=binding,
                    universe=artifacts.universe,
                    captured=captured,
                    prior=None,
                )
            )

        self.assertEqual(
            order,
            ["capture_fsynced", "same_object_inspected"],
        )
        self.assertEqual(evidence.parser_outcome, "accepted")
        self.assertEqual(parser_receipt.record_type, "parser_result")
        facade.finalize_sportradar_candidate_output(
            writer,
            prior_receipt=parser_receipt,
            terminal_reason="completed",
        )

    def test_c2_c3_secret_input_can_persist_only_as_canonical_redaction(
        self,
    ) -> None:
        from tennis_v1.capture import CaptureValidationError
        from tennis_v1.canonical import canonical_json_bytes

        manifest, values = _existing.CandidateOutputWriterTests._artifacts(
            self
        )
        secret = "DO_NOT_PERSIST_CANDIDATE_SECRET"
        raw = (
            '{"event_id":"synthetic-event","api_key":"'
            + secret
            + '"}'
        ).encode("ascii")
        common = {
            "manifest": manifest,
            "source_entity_id": "synthetic-match",
            "session_start_wall_ns": 100,
            "local_wall_ns": 101,
            "local_monotonic_ns": 700,
            "clock_uncertainty_ns": 2,
            "event_type": "sportradar_tennis_summary_v3",
            "source_wall_ns": 100,
            "source_generated_ns": 100,
            "provider_sequence": "c0.r0",
        }
        with self.assertRaises(CaptureValidationError):
            capture_public_candidate_fixture(raw, **common)
        captured = capture_redacted_candidate_fixture(raw, **common)
        expected = canonical_json_bytes(
            {
                "api_key": "<redacted>",
                "event_id": "synthetic-event",
            }
        )
        self.assertEqual(captured.payload, expected)
        self.assertEqual(captured.payload_encoding, "canonical-json-v1")
        self.assertEqual(
            captured.payload_transform,
            "json-secret-redaction-v1",
        )
        self.assertNotIn(secret, repr(captured))

        writer = facade.create_sportradar_candidate_output_writer(
            self.root_authority,
            output_parent=str(self.output_parent),
            session_manifest=manifest,
            session_manifest_sha256=session_manifest_sha256(manifest),
            **values,
        )
        quota_coordinates = (
            ("rolling_second_attempts", 0),
            ("rolling_60_seconds_attempts", 0),
            ("utc_day_attempts", 0),
            ("active_connections", 0),
            ("active_subscriptions", 1),
            ("rolling_hour_resync_attempts", 0),
        )
        permit_sha256 = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-READ-PERMIT-V1\0"
            + canonical_expert_bytes(
                (
                    ("session_id", manifest.session_id),
                    ("route", "summary"),
                    ("provider_match_id", "synthetic-match"),
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
        permit = facade.append_sportradar_candidate_permit(
            writer,
            prior_receipt=None,
            route="summary",
            provider_match_id="synthetic-match",
            resync=False,
            connection_epoch=1,
            permit_sha256=permit_sha256,
            quota_coordinates=quota_coordinates,
        )
        capture_receipt = facade.append_sportradar_candidate_capture(
            writer,
            prior_receipt=permit,
            captured=captured,
        )
        failure_receipt = facade.append_sportradar_candidate_failure(
            writer,
            prior_receipt=capture_receipt,
            stage="parser",
            failure_code="parser_rejected",
            permit_receipt=None,
            capture_receipt=capture_receipt,
        )
        commit = facade.finalize_sportradar_candidate_output(
            writer,
            prior_receipt=failure_receipt,
            terminal_reason="parser_rejected",
        )
        trace = (
            self.output_parent
            / commit.final_basename
            / "qualification-captures-v1.jsonl"
        ).read_bytes()
        self.assertNotIn(secret.encode("ascii"), trace)
        self.assertNotIn(secret.encode("ascii").hex().encode("ascii"), trace)
        self.assertIn(captured.payload.hex().encode("ascii"), trace)


if __name__ == "__main__":
    unittest.main()
