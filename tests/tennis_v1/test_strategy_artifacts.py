from __future__ import annotations

from dataclasses import fields, replace
from decimal import Decimal
from hashlib import sha256
import unittest

from inci_tennis_expert.contracts import (
    ArtifactPin,
    ExpertSessionManifestV1,
    compute_expert_session_manifest_sha256,
)
from inci_tennis_expert.strategy_artifacts import (
    ArtifactPayload,
    StrategyArtifactError,
    VerifiedStrategyArtifacts,
    verify_strategy_artifacts,
)
from tennis_v1.canonical import canonical_json_bytes
from tests.tennis_v1.test_expert_journal_codec import _manifest_fixture


def _model_document(
    artifact_id: str,
    artifact_kind: str,
    *,
    training_cutoff_wall_ns: int = 1,
    calibration_cutoff_wall_ns: int = 1,
) -> bytes:
    return canonical_json_bytes(
        {
            "artifact_id": artifact_id,
            "artifact_kind": artifact_kind,
            "calibrated": True,
            "calibration_cutoff_wall_ns": calibration_cutoff_wall_ns,
            "frozen": True,
            "schema_version": 1,
            "supported_match_format": (
                "standard_advantage_bo3_tb7_all_sets"
            ),
            "training_cutoff_wall_ns": training_cutoff_wall_ns,
        }
    )


def _fee_document() -> bytes:
    return canonical_json_bytes(
        {
            "artifact_id": "fees-v1",
            "artifact_kind": "fee_schedule",
            "balance_precision": "0.01",
            "effective_from_wall_ns": 1,
            "effective_until_wall_ns": None,
            "maker_multiplier": "1",
            "maker_rate": "0.0175",
            "schema_version": 1,
            "series_tickers": ["KXATPMATCH", "KXWTAMATCH"],
            "taker_multiplier": "1",
            "taker_rate": "0.07",
            "trade_fee_precision": "0.0001",
        }
    )


def _payload(artifact_id: str, data: bytes) -> ArtifactPayload:
    return ArtifactPayload(
        pin=ArtifactPin(artifact_id, sha256(data).hexdigest()),
        data=data,
    )


def _manifest_with(*pins: ArtifactPin) -> ExpertSessionManifestV1:
    base = _manifest_fixture()
    values = {
        item.name: getattr(base, item.name)
        for item in fields(ExpertSessionManifestV1)
        if item.name != "manifest_sha256"
    }
    values["artifact_pins"] = tuple(
        sorted(pins, key=lambda pin: pin.artifact_id)
    )
    values["manifest_sha256"] = compute_expert_session_manifest_sha256(
        **values
    )
    return ExpertSessionManifestV1(**values)  # type: ignore[arg-type]


class StrategyArtifactAuthorityTests(unittest.TestCase):
    def payloads(self) -> tuple[ArtifactPayload, ...]:
        return (
            _payload(
                "outcome-v1",
                _model_document("outcome-v1", "outcome_model"),
            ),
            _payload(
                "markout-v1",
                _model_document("markout-v1", "five_minute_markout"),
            ),
            _payload("fees-v1", _fee_document()),
        )

    def test_verified_authority_requires_manifest_membership_and_content(
        self,
    ) -> None:
        outcome, markout, fee = self.payloads()
        manifest = _manifest_with(outcome.pin, markout.pin, fee.pin)

        authority = verify_strategy_artifacts(
            manifest,
            outcome=outcome,
            markout=markout,
            fee_schedule=fee,
        )

        self.assertIs(type(authority), VerifiedStrategyArtifacts)
        self.assertEqual(
            authority.session_manifest_sha256,
            manifest.manifest_sha256,
        )
        self.assertEqual(authority.outcome_pin, outcome.pin)
        self.assertEqual(authority.markout_pin, markout.pin)
        self.assertEqual(authority.fee_schedule_pin, fee.pin)
        self.assertEqual(authority.fee_schedule.schedule_id, "fees-v1")
        self.assertEqual(
            authority.fee_schedule.series_tickers,
            ("KXATPMATCH", "KXWTAMATCH"),
        )
        self.assertTrue(authority.models_are_causal_at(2))

    def test_model_cutoffs_must_precede_the_decision(self) -> None:
        outcome = _payload(
            "outcome-v1",
            _model_document(
                "outcome-v1",
                "outcome_model",
                training_cutoff_wall_ns=1,
                calibration_cutoff_wall_ns=2,
            ),
        )
        markout = _payload(
            "markout-v1",
            _model_document(
                "markout-v1",
                "five_minute_markout",
            ),
        )
        fee = _payload("fees-v1", _fee_document())
        authority = verify_strategy_artifacts(
            _manifest_with(outcome.pin, markout.pin, fee.pin),
            outcome=outcome,
            markout=markout,
            fee_schedule=fee,
        )

        self.assertFalse(authority.models_are_causal_at(2))

    def test_missing_manifest_pin_or_changed_bytes_fail_closed(self) -> None:
        outcome, markout, fee = self.payloads()
        missing = _manifest_with(outcome.pin, fee.pin)
        with self.assertRaisesRegex(
            StrategyArtifactError,
            "^artifact_not_in_session_manifest$",
        ):
            verify_strategy_artifacts(
                missing,
                outcome=outcome,
                markout=markout,
                fee_schedule=fee,
            )

        with self.assertRaisesRegex(
            StrategyArtifactError,
            "^artifact_payload_digest$",
        ):
            ArtifactPayload(
                pin=outcome.pin,
                data=outcome.data + b" ",
            )

    def test_noncanonical_or_unfrozen_model_artifact_fails_closed(self) -> None:
        outcome, markout, fee = self.payloads()
        noncanonical_data = b'{"artifact_kind":"outcome_model", "x":1}'
        noncanonical = _payload("outcome-v1", noncanonical_data)
        manifest = _manifest_with(noncanonical.pin, markout.pin, fee.pin)
        with self.assertRaises(StrategyArtifactError):
            verify_strategy_artifacts(
                manifest,
                outcome=noncanonical,
                markout=markout,
                fee_schedule=fee,
            )

        unfrozen_data = canonical_json_bytes(
            {
                "artifact_id": "outcome-v1",
                "artifact_kind": "outcome_model",
                "calibrated": True,
                "calibration_cutoff_wall_ns": 200,
                "frozen": False,
                "schema_version": 1,
                "supported_match_format": (
                    "standard_advantage_bo3_tb7_all_sets"
                ),
                "training_cutoff_wall_ns": 100,
            }
        )
        unfrozen = _payload("outcome-v1", unfrozen_data)
        manifest = _manifest_with(unfrozen.pin, markout.pin, fee.pin)
        with self.assertRaisesRegex(
            StrategyArtifactError,
            "^model_artifact_authority$",
        ):
            verify_strategy_artifacts(
                manifest,
                outcome=unfrozen,
                markout=markout,
                fee_schedule=fee,
            )

    def test_authority_constructor_performs_full_verification(self) -> None:
        outcome, markout, fee = self.payloads()
        with self.assertRaisesRegex(
            StrategyArtifactError,
            "^artifact_not_in_session_manifest$",
        ):
            VerifiedStrategyArtifacts(
                manifest=_manifest_with(outcome.pin, fee.pin),
                outcome=outcome,
                markout=markout,
                fee_schedule=fee,
            )

    def test_verified_authority_cannot_be_replaced_with_zero_fees(
        self,
    ) -> None:
        outcome, markout, fee = self.payloads()
        authority = verify_strategy_artifacts(
            _manifest_with(outcome.pin, markout.pin, fee.pin),
            outcome=outcome,
            markout=markout,
            fee_schedule=fee,
        )
        zero_fee_schedule = replace(
            authority.fee_schedule,
            taker_rate=Decimal("0"),
            maker_rate=Decimal("0"),
        )

        with self.assertRaises((TypeError, ValueError)):
            replace(authority, fee_schedule=zero_fee_schedule)

        with self.assertRaisesRegex(
            StrategyArtifactError,
            "^authority_immutable$",
        ):
            authority.fee_schedule = zero_fee_schedule  # type: ignore[misc]

        bad_manifest = _manifest_with(outcome.pin, markout.pin, fee.pin)
        object.__setattr__(bad_manifest, "manifest_sha256", "e" * 64)
        object.__setattr__(authority, "_manifest", bad_manifest)
        with self.assertRaisesRegex(
            ValueError,
            "manifest_sha256",
        ):
            authority.validate()


if __name__ == "__main__":
    unittest.main()
