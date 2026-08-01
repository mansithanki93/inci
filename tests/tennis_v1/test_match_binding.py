from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from inci_tennis_expert.contracts import (
    ArtifactPin,
    BindingMarketMetadata,
    BindingMetadata,
    BindingReviewDecision,
    BindingRoute,
    BindingUniverse,
    ContractSide,
    ExpertContractError,
    MatchBinding,
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ScoreValue,
    SettlementSemantics,
    TennisState,
    TennisTransitionReason,
    TerminationKind,
    canonical_binding_review_artifact_bytes,
    compute_binding_review_artifact_sha256,
    compute_binding_review_evidence_sha256,
    compute_binding_universe_sha256,
    compute_membership_projection_sha256,
    compute_settlement_projection_sha256,
    expert_contract_sha256,
    player_side_for_contract,
)
from inci_tennis_expert.match_binding import (
    binding_metadata_for,
    binding_universe_sha256,
    decode_binding_universe,
    require_authorized_route,
    resolve_binding,
)
import inci_tennis_expert.match_binding as match_binding_module
from inci_tennis_io.pinned_artifacts import (
    BINDING_REVIEW_ARTIFACT_MAX_BYTES,
    MATCH_BINDING_ARTIFACT_MAX_BYTES,
    PinnedArtifactBytes,
    PinnedArtifactError,
    PinnedArtifactReadRequest,
    read_pinned_artifact,
)
from tennis_v1.pinned_file import PinnedBytes, PinnedFileError


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "tennis_v1" / "fixtures"
SCHEMAS = ROOT / "inci_tennis_expert" / "schemas"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def raw_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def settlement_document(*, rule_sha: str) -> dict[str, object]:
    values: dict[str, object] = {
        "result_authority": "kalshi_finalized_market_result",
        "natural_completion": "yes_if_named_player_final_winner",
        "retirement_after_point": "yes_if_named_player_final_winner",
        "walkover_before_point": "void",
        "default_after_point": "yes_if_named_player_final_winner",
        "disqualification_after_point": "void",
        "cancellation": "void",
        "postponement": "defer",
        "abandonment": "await_latest_finalized_result",
        "amendment": "await_latest_finalized_result",
        "void_treatment": "no_directional_settlement",
        "raw_rules_sha256": rule_sha,
    }
    values["projection_sha256"] = compute_settlement_projection_sha256(
        **values  # type: ignore[arg-type]
    )
    return values


def one_binding_document(index: int = 0) -> dict[str, object]:
    suffix = f"{index:03d}"
    canonical_match_id = f"canonical-match-{suffix}"
    provider_match_id = f"provider-match-{suffix}"
    event_ticker = f"TENNISMATCH{suffix}"
    event_id = f"event-{suffix}"
    series_ticker = "TENNISSERIES"
    event_catalog_sha = hashlib.sha256(
        f"catalog-{suffix}".encode("ascii")
    ).hexdigest()
    home_provider = f"provider-home-{suffix}"
    away_provider = f"provider-away-{suffix}"
    home_canonical = f"canonical-home-{suffix}"
    away_canonical = f"canonical-away-{suffix}"
    markets: list[dict[str, object]] = []
    for side, provider_id, canonical_id, label in (
        ("home", home_provider, home_canonical, "HOME"),
        ("away", away_provider, away_canonical, "AWAY"),
    ):
        market_ticker = f"TENNIS{suffix}{label}"
        market_id = f"market-{suffix}-{label.lower()}"
        membership_evidence = hashlib.sha256(
            f"membership-{suffix}-{label}".encode("ascii")
        ).hexdigest()
        membership: dict[str, object] = {
            "series_ticker": series_ticker,
            "event_ticker": event_ticker,
            "event_id": event_id,
            "market_ticker": market_ticker,
            "market_id": market_id,
            "product": "match_winner",
            "event_catalog_sha256": event_catalog_sha,
            "source_id": "kalshi-event-catalog",
            "source_version": "v2",
            "captured_wall_ns": 800_000_000 + index,
            "membership_evidence_sha256": membership_evidence,
        }
        membership["membership_projection_sha256"] = (
            compute_membership_projection_sha256(
                series_ticker=series_ticker,
                event_ticker=event_ticker,
                event_id=event_id,
                market_ticker=market_ticker,
                market_id=market_id,
                product="match_winner",
                event_catalog_sha256=event_catalog_sha,
                membership_source_id="kalshi-event-catalog",
                membership_source_version="v2",
                membership_captured_wall_ns=800_000_000 + index,
                membership_evidence_sha256=membership_evidence,
            )
        )
        market_text = f"Synthetic {label} player wins match"
        rule_text = f"Synthetic settlement rules {suffix} {label}"
        rule_sha = raw_sha(rule_text.encode("utf-8"))
        markets.append(
            {
                "market_ticker": market_ticker,
                "market_id": market_id,
                "yes_player_side": side,
                "yes_provider_player_id": provider_id,
                "yes_canonical_player_id": canonical_id,
                "yes_outcome": "wins_match",
                "membership": membership,
                "settlement": settlement_document(rule_sha=rule_sha),
                "market_evidence": {
                    "source_id": "kalshi-market-api",
                    "source_version": "v2",
                    "captured_wall_ns": 850_000_000 + index,
                    "market_text": market_text,
                    "market_text_sha256": raw_sha(
                        market_text.encode("utf-8")
                    ),
                    "settlement_rule_text": rule_text,
                    "settlement_rule_text_sha256": rule_sha,
                },
            }
        )
    return {
        "canonical_match_id": canonical_match_id,
        "provider": {
            "sport": "tennis",
            "competition_kind": "real",
            "competitor_count": 2,
            "source_id": "provider-a",
            "revision_domain_id": "revision-a",
            "source_lineage_sha256": SHA_A,
            "match_id": provider_match_id,
            "home_player": {
                "provider_player_id": home_provider,
                "canonical_player_id": home_canonical,
                "display_name": f"Synthetic Home {suffix}",
                "participant_type": "player",
                "participant_status": "confirmed",
            },
            "away_player": {
                "provider_player_id": away_provider,
                "canonical_player_id": away_canonical,
                "display_name": f"Synthetic Away {suffix}",
                "participant_type": "player",
                "participant_status": "confirmed",
            },
            "scheduled_start_wall_ns": 2_000_000_000 + index,
            "snapshot_sha256": SHA_B,
            "snapshot_captured_wall_ns": 900_000_000 + index,
        },
        "competition": {
            "tournament_id": "tournament-1",
            "season_id": "season-2026",
            "draw_id": "draw-main",
            "round_id": f"round-{suffix}",
            "tour_id": "tour-atp",
            "tier_id": "tier-250",
            "surface": "hard",
            "tournament_name": "Synthetic Open",
        },
        "match": {
            "match_type": "singles",
            "product": "match_winner",
            "format": "standard_advantage_bo3_tb7_all_sets",
            "start_tolerance_ns": 900_000_000_000,
        },
        "kalshi": {
            "series_ticker": series_ticker,
            "event_ticker": event_ticker,
            "event_id": event_id,
            "scheduled_start_wall_ns": 2_000_000_100 + index,
            "event_sha256": SHA_C,
            "event_captured_wall_ns": 900_000_000 + index,
            "event_catalog_sha256": event_catalog_sha,
            "route_authority": "direct_yes_only",
            "markets": markets,
            "authorized_routes": [
                {
                    "player_side": "home",
                    "market_ticker": markets[0]["market_ticker"],
                    "contract_side": "yes",
                },
                {
                    "player_side": "away",
                    "market_ticker": markets[1]["market_ticker"],
                    "contract_side": "yes",
                },
            ],
        },
    }


def manifest_document(count: int = 1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact_id": "binding-manifest-1",
        "artifact_created_wall_ns": 1_000_000_000,
        "bindings": [one_binding_document(index) for index in range(count)],
    }


def projections_for(
    document: dict[str, object],
    manifest_pin: ArtifactPin,
) -> tuple[tuple[MatchBinding, ...], tuple[BindingMetadata, ...]]:
    bindings: list[MatchBinding] = []
    metadata: list[BindingMetadata] = []
    for raw_binding in document["bindings"]:  # type: ignore[union-attr]
        provider = raw_binding["provider"]
        competition = raw_binding["competition"]
        match = raw_binding["match"]
        kalshi = raw_binding["kalshi"]
        raw_markets = kalshi["markets"]
        projected_markets: list[BindingMarketMetadata] = []
        for raw_market in raw_markets:
            membership = raw_market["membership"]
            evidence = raw_market["market_evidence"]
            projected_settlement = SettlementSemantics(
                **raw_market["settlement"]
            )
            projected_markets.append(
                BindingMarketMetadata(
                    series_ticker=membership["series_ticker"],
                    event_ticker=membership["event_ticker"],
                    event_id=membership["event_id"],
                    market_ticker=membership["market_ticker"],
                    market_id=membership["market_id"],
                    yes_player_side=PlayerSide(
                        raw_market["yes_player_side"]
                    ),
                    yes_provider_player_id=(
                        raw_market["yes_provider_player_id"]
                    ),
                    yes_canonical_player_id=(
                        raw_market["yes_canonical_player_id"]
                    ),
                    product=membership["product"],
                    event_catalog_sha256=membership[
                        "event_catalog_sha256"
                    ],
                    membership_source_id=membership["source_id"],
                    membership_source_version=membership["source_version"],
                    membership_captured_wall_ns=membership[
                        "captured_wall_ns"
                    ],
                    membership_evidence_sha256=membership[
                        "membership_evidence_sha256"
                    ],
                    membership_projection_sha256=membership[
                        "membership_projection_sha256"
                    ],
                    market_text_sha256=evidence["market_text_sha256"],
                    settlement_rule_text_sha256=evidence[
                        "settlement_rule_text_sha256"
                    ],
                    settlement=projected_settlement,
                )
            )
        routes = tuple(
            BindingRoute(
                player_side=PlayerSide(route["player_side"]),
                market_ticker=route["market_ticker"],
                contract_side=ContractSide(route["contract_side"]),
            )
            for route in kalshi["authorized_routes"]
        )
        binding = MatchBinding(
            provider_match_id=provider["match_id"],
            canonical_match_id=raw_binding["canonical_match_id"],
            provider_source_id=provider["source_id"],
            revision_domain_id=provider["revision_domain_id"],
            source_lineage_sha256=provider["source_lineage_sha256"],
            provider_home_player_id=provider["home_player"][
                "provider_player_id"
            ],
            provider_away_player_id=provider["away_player"][
                "provider_player_id"
            ],
            kalshi_event_ticker=kalshi["event_ticker"],
            home_market_ticker=raw_markets[0]["market_ticker"],
            away_market_ticker=raw_markets[1]["market_ticker"],
            match_format=MatchFormat(match["format"]),
            scheduled_start_wall_ns=provider["scheduled_start_wall_ns"],
            start_tolerance_ns=match["start_tolerance_ns"],
            artifact_created_wall_ns=document[
                "artifact_created_wall_ns"
            ],
            binding_artifact_sha256=manifest_pin.artifact_sha256,
        )
        item = BindingMetadata(
            canonical_match_id=raw_binding["canonical_match_id"],
            canonical_home_player_id=provider["home_player"][
                "canonical_player_id"
            ],
            canonical_away_player_id=provider["away_player"][
                "canonical_player_id"
            ],
            tournament_id=competition["tournament_id"],
            season_id=competition["season_id"],
            draw_id=competition["draw_id"],
            round_id=competition["round_id"],
            tour_id=competition["tour_id"],
            tier_id=competition["tier_id"],
            surface=competition["surface"],
            provider_snapshot_sha256=provider["snapshot_sha256"],
            kalshi_event_sha256=kalshi["event_sha256"],
            markets=tuple(projected_markets),
            authorized_routes=routes,
        )
        bindings.append(binding)
        metadata.append(item)
    return tuple(bindings), tuple(metadata)


def valid_payloads(
    count: int = 1,
) -> tuple[bytes, bytes, ArtifactPin, ArtifactPin, BindingUniverse]:
    document = manifest_document(count)
    manifest_payload = json_bytes(document)
    manifest_pin = ArtifactPin(
        artifact_id=document["artifact_id"],  # type: ignore[arg-type]
        artifact_sha256=raw_sha(manifest_payload),
    )
    bindings, metadata = projections_for(document, manifest_pin)
    evidence = compute_binding_review_evidence_sha256(
        manifest_pin,
        bindings,
        metadata,
    )
    review_values: dict[str, object] = {
        "review_artifact_id": "binding-review-1",
        "review_artifact_created_wall_ns": 1_200_000_000,
        "binding_artifact_id": manifest_pin.artifact_id,
        "binding_artifact_sha256": manifest_pin.artifact_sha256,
        "decision": "approved",
        "reviewer_id": "independent-reviewer-1",
        "reviewed_wall_ns": 1_100_000_000,
        "review_evidence_sha256": evidence,
    }
    review_payload = canonical_binding_review_artifact_bytes(
        **review_values  # type: ignore[arg-type]
    )
    review_pin = ArtifactPin(
        artifact_id=review_values["review_artifact_id"],  # type: ignore[arg-type]
        artifact_sha256=raw_sha(review_payload),
    )
    review = BindingReviewDecision(
        review_artifact_sha256=review_pin.artifact_sha256,
        **review_values,  # type: ignore[arg-type]
    )
    expected = BindingUniverse(
        raw_artifact_id=manifest_pin.artifact_id,
        raw_artifact_sha256=manifest_pin.artifact_sha256,
        review=review,
        bindings=bindings,
        metadata=metadata,
        universe_sha256=compute_binding_universe_sha256(
            manifest_pin,
            review,
            bindings,
            metadata,
        ),
    )
    return (
        manifest_payload,
        review_payload,
        manifest_pin,
        review_pin,
        expected,
    )


def manifest_error(
    document: dict[str, object],
    expected_error: str,
) -> None:
    payload = json_bytes(document)
    pin = ArtifactPin(
        str(document.get("artifact_id", "binding-manifest-1")),
        raw_sha(payload),
    )
    review_payload = b"{"
    review_pin = ArtifactPin("binding-review-1", raw_sha(review_payload))
    with unittest.TestCase().assertRaisesRegex(
        ExpertContractError,
        f"^{expected_error}$",
    ):
        decode_binding_universe(
            payload,
            review_payload,
            manifest_pin=pin,
            review_pin=review_pin,
        )


def live_state(binding: MatchBinding, **changes: object) -> TennisState:
    values: dict[str, object] = {
        "provider_source_id": binding.provider_source_id,
        "revision_domain_id": binding.revision_domain_id,
        "source_lineage_sha256": binding.source_lineage_sha256,
        "provider_match_id": binding.provider_match_id,
        "home_player_id": binding.provider_home_player_id,
        "away_player_id": binding.provider_away_player_id,
        "scheduled_start_wall_ns": binding.scheduled_start_wall_ns,
        "match_format": binding.match_format,
        "status": MatchStatus.LIVE,
        "termination_kind": TerminationKind.NONE,
        "winner": None,
        "retired_side": None,
        "completed_sets": (),
        "games_home": 0,
        "games_away": 0,
        "points_home": ScoreValue.LOVE,
        "points_away": ScoreValue.LOVE,
        "in_tiebreak": False,
        "tiebreak_points_home": 0,
        "tiebreak_points_away": 0,
        "tiebreak_first_server": None,
        "server_for_next_point": PlayerSide.HOME,
        "correction_epoch": 0,
        "revision": 1,
        "snapshot_complete": True,
        "last_provider_event_id": "provider-event-1",
        "last_event_semantic_sha256": SHA_B,
        "correction_lineage_sha256": SHA_C,
        "last_source_wall_ns": 1,
        "last_source_generated_wall_ns": 1,
        "last_received_monotonic_ns": 1,
        "last_clock_uncertainty_ns": 0,
        "block_reason": None,
        "expected_revision": None,
        "observed_revision": None,
        "blocked_event_semantic_sha256": None,
        "blocked_received_monotonic_ns": None,
    }
    values.update(changes)
    return TennisState(**values)  # type: ignore[arg-type]


class ContractAndDigestTests(unittest.TestCase):
    def test_known_digest_vectors_are_independent_and_raw_differs(self) -> None:
        (
            _manifest_payload,
            review_payload,
            manifest_pin,
            _review_pin,
            universe,
        ) = valid_payloads()
        self.assertEqual(
            universe.metadata[0].markets[0].settlement.projection_sha256,
            "00f24b40555cad09165601ecc6302b879932bbfda68e9361b73764c5b6d83bbc",
        )
        self.assertEqual(
            universe.metadata[0].markets[0].membership_projection_sha256,
            "ed89d257a4d252bac0883775f6ba9a9fddc5d3f8adbf1ae78aa5b22404e6421e",
        )
        self.assertEqual(
            universe.review.review_evidence_sha256,
            "a7d5080e7ebf6a80ea6caba97326a6df3679517bc1675fabed24df29f0fc17ee",
        )
        self.assertEqual(
            review_payload,
            (
                b'{"schema_version":1,"artifact_id":"binding-review-1",'
                b'"artifact_created_wall_ns":1200000000,'
                b'"binding_artifact_id":"binding-manifest-1",'
                b'"binding_artifact_sha256":"'
                + manifest_pin.artifact_sha256.encode("ascii")
                + b'","decision":"approved",'
                b'"reviewer_id":"independent-reviewer-1",'
                b'"reviewed_wall_ns":1100000000,'
                b'"review_evidence_sha256":"'
                + universe.review.review_evidence_sha256.encode("ascii")
                + b'"}'
            ),
        )
        self.assertEqual(
            universe.review.review_artifact_sha256,
            raw_sha(review_payload),
        )
        self.assertEqual(
            universe.universe_sha256,
            "37dd30e3f99d228fd89b162827e81487d23dcf3d2339a7d5434509063124d02f",
        )
        self.assertNotEqual(
            universe.raw_artifact_sha256,
            universe.universe_sha256,
        )

    def test_contracts_are_frozen_slotted_and_repr_is_safe(self) -> None:
        universe = valid_payloads()[-1]
        values = (
            universe.metadata[0].authorized_routes[0],
            universe.metadata[0].markets[0].settlement,
            universe.metadata[0].markets[0],
            universe.metadata[0],
            universe.review,
            universe,
        )
        for value in values:
            with self.subTest(contract=type(value).__name__):
                self.assertIn("__slots__", type(value).__dict__)
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, fields(value)[0].name, object())
                self.assertNotIn("payload", repr(value).lower())

    def test_closed_settlement_route_and_membership_invariants(self) -> None:
        universe = valid_payloads()[-1]
        settlement = universe.metadata[0].markets[0].settlement
        with self.assertRaisesRegex(ExpertContractError, "^contract_side$"):
            BindingRoute(PlayerSide.HOME, "TENNIS000HOME", ContractSide.NO)
        with self.assertRaisesRegex(ExpertContractError, "^cancellation$"):
            replace(settlement, cancellation="refund")
        with self.assertRaisesRegex(
            ExpertContractError,
            "^projection_sha256$",
        ):
            replace(settlement, projection_sha256=SHA_A)
        market = universe.metadata[0].markets[0]
        with self.assertRaisesRegex(
            ExpertContractError,
            "^membership_projection_sha256$",
        ):
            replace(market, membership_projection_sha256=SHA_A)
        with self.assertRaisesRegex(
            ExpertContractError,
            "^settlement_rule_text_sha256$",
        ):
            replace(market, settlement_rule_text_sha256=SHA_E)

    def test_subset_reorder_fabrication_and_review_reuse_fail(self) -> None:
        universe = valid_payloads(2)[-1]
        with self.assertRaises(ExpertContractError):
            replace(
                universe,
                bindings=universe.bindings[:1],
                metadata=universe.metadata[:1],
            )
        with self.assertRaises(ExpertContractError):
            replace(
                universe,
                bindings=tuple(reversed(universe.bindings)),
                metadata=tuple(reversed(universe.metadata)),
            )
        fabricated_metadata = replace(
            universe.metadata[0],
            surface="clay",
        )
        with self.assertRaises(ExpertContractError):
            replace(
                universe,
                metadata=(fabricated_metadata,) + universe.metadata[1:],
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^review_artifact_sha256$",
        ):
            replace(
                universe.review,
                review_evidence_sha256=SHA_A,
            )

    def test_player_bijection_collision_precedence_and_domain_split(self) -> None:
        _, _, _, _, universe = valid_payloads(2)
        second = universe.metadata[1]
        home_market = replace(
            second.markets[0],
            yes_provider_player_id=universe.bindings[0].provider_home_player_id,
            yes_canonical_player_id="different-canonical",
        )
        changed_second = replace(
            second,
            canonical_home_player_id="different-canonical",
            markets=(home_market, second.markets[1]),
        )
        changed_binding = replace(
            universe.bindings[1],
            provider_home_player_id=universe.bindings[0].provider_home_player_id,
        )
        pin = ArtifactPin(
            universe.raw_artifact_id,
            universe.raw_artifact_sha256,
        )
        changed_bindings = (universe.bindings[0], changed_binding)
        changed_metadata = (universe.metadata[0], changed_second)
        evidence = compute_binding_review_evidence_sha256(
            pin,
            changed_bindings,
            changed_metadata,
        )
        review_values = {
            "review_artifact_id": "collision-review",
            "review_artifact_created_wall_ns": 1_200_000_000,
            "binding_artifact_id": pin.artifact_id,
            "binding_artifact_sha256": pin.artifact_sha256,
            "decision": "approved",
            "reviewer_id": "reviewer-1",
            "reviewed_wall_ns": 1_100_000_000,
            "review_evidence_sha256": evidence,
        }
        review = BindingReviewDecision(
            review_artifact_sha256=compute_binding_review_artifact_sha256(
                **review_values
            ),
            **review_values,
        )
        digest = compute_binding_universe_sha256(
            pin,
            review,
            changed_bindings,
            changed_metadata,
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^binding_manifest_provider_player_collision$",
        ):
            BindingUniverse(
                pin.artifact_id,
                pin.artifact_sha256,
                review,
                changed_bindings,
                changed_metadata,
                digest,
            )
        split_binding = replace(changed_binding, provider_source_id="provider-b")
        split_bindings = (universe.bindings[0], split_binding)
        split_evidence = compute_binding_review_evidence_sha256(
            pin,
            split_bindings,
            changed_metadata,
        )
        split_values = dict(
            review_values,
            review_evidence_sha256=split_evidence,
        )
        split_review = BindingReviewDecision(
            review_artifact_sha256=compute_binding_review_artifact_sha256(
                **split_values
            ),
            **split_values,
        )
        split_digest = compute_binding_universe_sha256(
            pin,
            split_review,
            split_bindings,
            changed_metadata,
        )
        constructed = BindingUniverse(
            pin.artifact_id,
            pin.artifact_sha256,
            split_review,
            split_bindings,
            changed_metadata,
            split_digest,
        )
        self.assertEqual(len(constructed.bindings), 2)

    def test_review_bytes_bind_every_field(self) -> None:
        universe = valid_payloads()[-1]
        review = universe.review
        original = review.review_artifact_sha256
        fields_without_digest = (
            "review_artifact_id",
            "review_artifact_created_wall_ns",
            "binding_artifact_id",
            "binding_artifact_sha256",
            "decision",
            "reviewer_id",
            "reviewed_wall_ns",
            "review_evidence_sha256",
        )
        for field_name in fields_without_digest:
            values = {
                name: getattr(review, name)
                for name in fields_without_digest
            }
            current = values[field_name]
            if type(current) is int:
                values[field_name] = current + 1
            elif field_name in {
                "binding_artifact_sha256",
                "review_evidence_sha256",
            }:
                values[field_name] = SHA_E
            elif field_name == "decision":
                values[field_name] = "rejected"
            else:
                values[field_name] = f"{current}-changed"
            with self.subTest(field=field_name):
                self.assertNotEqual(
                    compute_binding_review_artifact_sha256(**values),
                    original,
                )


class DecoderTests(unittest.TestCase):
    def test_valid_decode_returns_complete_expected_universe(self) -> None:
        payload, review, manifest_pin, review_pin, expected = valid_payloads()
        actual = decode_binding_universe(
            payload,
            review,
            manifest_pin=manifest_pin,
            review_pin=review_pin,
        )
        self.assertEqual(actual, expected)
        self.assertEqual(binding_universe_sha256(actual), expected.universe_sha256)
        self.assertEqual(len(actual.bindings), 1)
        self.assertEqual(actual.metadata[0].surface, "hard")
        self.assertEqual(actual.metadata[0].tournament_id, "tournament-1")
        self.assertEqual(
            actual.metadata[0].markets[0].membership_source_version,
            "v2",
        )

    def test_exact_type_and_outer_precedence(self) -> None:
        payload, review, manifest_pin, review_pin, _ = valid_payloads()
        class BytesSubclass(bytes):
            pass
        class PinSubclass(ArtifactPin):
            pass
        rows = (
            (
                BytesSubclass(payload),
                object(),
                object(),
                object(),
                TypeError,
                "manifest_payload",
            ),
            (
                payload,
                BytesSubclass(review),
                object(),
                object(),
                TypeError,
                "review_payload",
            ),
            (
                payload,
                review,
                object(),
                object(),
                TypeError,
                "manifest_pin",
            ),
            (
                payload,
                review,
                manifest_pin,
                object(),
                TypeError,
                "review_pin",
            ),
            (
                b"",
                b"",
                manifest_pin,
                review_pin,
                ExpertContractError,
                "binding_manifest_payload_size",
            ),
            (
                payload,
                b"",
                manifest_pin,
                review_pin,
                ExpertContractError,
                "binding_review_payload_size",
            ),
            (
                b"{",
                b"{",
                manifest_pin,
                review_pin,
                ExpertContractError,
                "binding_manifest_payload_sha256",
            ),
        )
        for manifest, review_value, pin, rpin, error, message in rows:
            with self.subTest(message=message):
                with self.assertRaisesRegex(error, f"^{message}$"):
                    decode_binding_universe(
                        manifest,
                        review_value,
                        manifest_pin=pin,
                        review_pin=rpin,
                    )
        forged_review_pin = ArtifactPin(review_pin.artifact_id, SHA_E)
        with self.assertRaisesRegex(
            ExpertContractError,
            "^binding_review_payload_sha256$",
        ):
            decode_binding_universe(
                payload,
                b"{",
                manifest_pin=manifest_pin,
                review_pin=forged_review_pin,
            )

    def test_manifest_and_review_json_failures_are_static(self) -> None:
        manifest_bad_values = (
            (b"\xef\xbb\xbf{}", "bom"),
            (b"\xff", "utf8"),
            (b'{"x":1,"x":2}', "duplicate_key"),
            (b'{"x":1.2}', "number"),
            (b'{"x":NaN}', "number"),
            (b'{"x":12345678901234567890}', "number"),
            (b"{", "syntax"),
        )
        review_good = b"{"
        review_pin = ArtifactPin("binding-review-1", raw_sha(review_good))
        for malformed, suffix in manifest_bad_values:
            with self.subTest(domain="manifest", suffix=suffix):
                pin = ArtifactPin("binding-manifest-1", raw_sha(malformed))
                with self.assertRaises(ExpertContractError) as caught:
                    decode_binding_universe(
                        malformed,
                        review_good,
                        manifest_pin=pin,
                        review_pin=review_pin,
                    )
                self.assertEqual(
                    str(caught.exception),
                    f"binding_manifest_json_{suffix}",
                )
                self.assertIsNone(caught.exception.__cause__)
        manifest_payload, _, manifest_pin, _, _ = valid_payloads()
        for malformed, suffix in manifest_bad_values:
            with self.subTest(domain="review", suffix=suffix):
                pin = ArtifactPin("binding-review-1", raw_sha(malformed))
                with self.assertRaisesRegex(
                    ExpertContractError,
                    f"^binding_review_json_{suffix}$",
                ):
                    decode_binding_universe(
                        manifest_payload,
                        malformed,
                        manifest_pin=manifest_pin,
                        review_pin=pin,
                    )
        with mock.patch(
            "inci_tennis_expert.match_binding.json.loads",
            side_effect=RecursionError,
        ):
            with self.assertRaisesRegex(
                ExpertContractError,
                "^binding_manifest_json_recursion$",
            ):
                decode_binding_universe(
                    manifest_payload,
                    review_good,
                    manifest_pin=manifest_pin,
                    review_pin=review_pin,
                )
        review_payload = valid_payloads()[1]
        valid_review_pin = valid_payloads()[3]
        with mock.patch(
            "inci_tennis_expert.match_binding.json.loads",
            side_effect=[manifest_document(), RecursionError()],
        ):
            with self.assertRaisesRegex(
                ExpertContractError,
                "^binding_review_json_recursion$",
            ):
                decode_binding_universe(
                    manifest_payload,
                    review_payload,
                    manifest_pin=manifest_pin,
                    review_pin=valid_review_pin,
                )

    def test_display_surrogates_fail_with_static_manifest_value_error(
        self,
    ) -> None:
        mutations = (
            (
                (
                    "bindings",
                    0,
                    "provider",
                    "home_player",
                    "display_name",
                ),
                "Synthetic \ud800 Player",
            ),
            (
                (
                    "bindings",
                    0,
                    "kalshi",
                    "markets",
                    0,
                    "market_evidence",
                    "market_text",
                ),
                "Synthetic market\n\udfff",
            ),
            (
                (
                    "bindings",
                    0,
                    "kalshi",
                    "markets",
                    0,
                    "market_evidence",
                    "settlement_rule_text",
                ),
                "Synthetic rules\n\ud800",
            ),
        )
        review_payload = valid_payloads()[1]
        review_pin = valid_payloads()[3]
        for path, hostile_text in mutations:
            document = manifest_document()
            target: object = document
            for part in path[:-1]:
                target = target[part]  # type: ignore[index]
            target[path[-1]] = hostile_text  # type: ignore[index]
            payload = json.dumps(
                document,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
            pin = ArtifactPin(
                str(document["artifact_id"]),
                raw_sha(payload),
            )
            with self.subTest(path=path):
                with self.assertRaises(ExpertContractError) as caught:
                    decode_binding_universe(
                        payload,
                        review_payload,
                        manifest_pin=pin,
                        review_pin=review_pin,
                    )
                self.assertEqual(
                    str(caught.exception),
                    "binding_manifest_value",
                )
                self.assertIsNone(caught.exception.__cause__)

    def test_payload_byte_limits_precede_hash_and_json(self) -> None:
        _, _, manifest_pin, review_pin, _ = valid_payloads()
        with self.assertRaisesRegex(
            ExpertContractError,
            "^binding_manifest_payload_size$",
        ):
            decode_binding_universe(
                b"{" + b" " * MATCH_BINDING_ARTIFACT_MAX_BYTES,
                b"{",
                manifest_pin=manifest_pin,
                review_pin=review_pin,
            )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^binding_review_payload_size$",
        ):
            decode_binding_universe(
                b"{",
                b"{" + b" " * BINDING_REVIEW_ARTIFACT_MAX_BYTES,
                manifest_pin=ArtifactPin("binding-manifest-1", raw_sha(b"{")),
                review_pin=review_pin,
            )

    def test_wrong_scalar_types_are_shape_before_value_validation(self) -> None:
        mutations = (
            ("artifact_created_wall_ns", True),
            ("bindings.0.canonical_match_id", 7),
            ("bindings.0.provider.sport", []),
            ("bindings.0.provider.competitor_count", "2"),
            ("bindings.0.competition.surface", 7),
            ("bindings.0.match.format", 7),
            ("bindings.0.kalshi.route_authority", None),
            (
                "bindings.0.kalshi.markets.0.membership.source_id",
                7,
            ),
            (
                "bindings.0.kalshi.markets.0.settlement.cancellation",
                7,
            ),
            (
                "bindings.0.kalshi.authorized_routes.0.contract_side",
                7,
            ),
        )
        for dotted, value in mutations:
            document = manifest_document()
            target: object = document
            parts = dotted.split(".")
            for part in parts[:-1]:
                target = (
                    target[int(part)]  # type: ignore[index]
                    if part.isdigit()
                    else target[part]  # type: ignore[index]
                )
            target[parts[-1]] = value  # type: ignore[index]
            with self.subTest(field=dotted):
                manifest_error(document, "binding_manifest_shape")

    def test_manifest_structural_gates_placeholders_routes_and_times(self) -> None:
        mutations: list[tuple[str, object, str]] = (
            ("provider.sport", "soccer", "binding_manifest_value"),
            (
                "provider.competition_kind",
                "virtual",
                "binding_manifest_value",
            ),
            ("provider.competitor_count", 4, "binding_manifest_value"),
            (
                "provider.home_player.participant_type",
                "team",
                "binding_manifest_value",
            ),
            (
                "provider.home_player.participant_status",
                "unconfirmed",
                "binding_manifest_value",
            ),
            (
                "provider.home_player.display_name",
                "Winner of Match 7",
                "binding_manifest_placeholder",
            ),
            (
                "provider.home_player.provider_player_id",
                "TBD-123",
                "binding_manifest_placeholder",
            ),
            (
                "match.match_type",
                "doubles",
                "binding_manifest_value",
            ),
            (
                "match.product",
                "set_winner",
                "binding_manifest_value",
            ),
            (
                "match.format",
                "match_tiebreak",
                "binding_manifest_value",
            ),
            (
                "match.start_tolerance_ns",
                900_000_000_001,
                "binding_manifest_value",
            ),
            (
                "kalshi.route_authority",
                "cross_market_complement",
                "binding_manifest_route",
            ),
            (
                "kalshi.authorized_routes.0.contract_side",
                "no",
                "binding_manifest_route",
            ),
        )
        for dotted, value, error in mutations:
            document = manifest_document()
            target: object = document["bindings"][0]  # type: ignore[index]
            parts = dotted.split(".")
            for part in parts[:-1]:
                if part.isdigit():
                    target = target[int(part)]  # type: ignore[index]
                else:
                    target = target[part]  # type: ignore[index]
            target[parts[-1]] = value  # type: ignore[index]
            with self.subTest(field=dotted):
                manifest_error(document, error)
        document = manifest_document()
        document["bindings"][0]["kalshi"]["scheduled_start_wall_ns"] = (  # type: ignore[index]
            902_000_000_001
        )
        manifest_error(document, "binding_manifest_start_time")

    def test_membership_evidence_and_chronology_fail_without_text_fallback(
        self,
    ) -> None:
        document = manifest_document()
        membership = document["bindings"][0]["kalshi"]["markets"][0][  # type: ignore[index]
            "membership"
        ]
        membership["membership_projection_sha256"] = SHA_A
        manifest_error(document, "binding_manifest_evidence")
        document = manifest_document()
        membership = document["bindings"][0]["kalshi"]["markets"][0][  # type: ignore[index]
            "membership"
        ]
        membership["captured_wall_ns"] = 1_000_000_001
        manifest_error(document, "binding_manifest_evidence")
        document = manifest_document()
        evidence = document["bindings"][0]["kalshi"]["markets"][0][  # type: ignore[index]
            "market_evidence"
        ]
        evidence["market_text_sha256"] = SHA_A
        manifest_error(document, "binding_manifest_evidence")

    def test_membership_projection_changes_for_each_provenance_field(
        self,
    ) -> None:
        universe = valid_payloads()[-1]
        market = universe.metadata[0].markets[0]
        base = {
            "series_ticker": market.series_ticker,
            "event_ticker": market.event_ticker,
            "event_id": market.event_id,
            "market_ticker": market.market_ticker,
            "market_id": market.market_id,
            "product": market.product,
            "event_catalog_sha256": market.event_catalog_sha256,
            "membership_source_id": market.membership_source_id,
            "membership_source_version": market.membership_source_version,
            "membership_captured_wall_ns": (
                market.membership_captured_wall_ns
            ),
            "membership_evidence_sha256": (
                market.membership_evidence_sha256
            ),
        }
        original = compute_membership_projection_sha256(**base)
        changes = {
            "membership_source_id": "different-source",
            "membership_source_version": "v3",
            "membership_captured_wall_ns": (
                market.membership_captured_wall_ns + 1
            ),
            "event_catalog_sha256": SHA_E,
            "membership_evidence_sha256": SHA_D,
        }
        for name, value in changes.items():
            mutated = dict(base)
            mutated[name] = value
            with self.subTest(field=name):
                self.assertNotEqual(
                    compute_membership_projection_sha256(**mutated),
                    original,
                )

    def test_all_settlement_choice_combinations_construct(self) -> None:
        binary = ("yes_if_named_player_final_winner", "void")
        abandonment = ("await_latest_finalized_result", "void")
        rule_sha = SHA_A
        for retirement in binary:
            for walkover in binary:
                for default in binary:
                    for disqualification in binary:
                        for abandoned in abandonment:
                            values = {
                                "result_authority": (
                                    "kalshi_finalized_market_result"
                                ),
                                "natural_completion": (
                                    "yes_if_named_player_final_winner"
                                ),
                                "retirement_after_point": retirement,
                                "walkover_before_point": walkover,
                                "default_after_point": default,
                                "disqualification_after_point": (
                                    disqualification
                                ),
                                "cancellation": "void",
                                "postponement": "defer",
                                "abandonment": abandoned,
                                "amendment": (
                                    "await_latest_finalized_result"
                                ),
                                "void_treatment": (
                                    "no_directional_settlement"
                                ),
                                "raw_rules_sha256": rule_sha,
                            }
                            projection = (
                                compute_settlement_projection_sha256(
                                    **values
                                )
                            )
                            self.assertEqual(
                                SettlementSemantics(
                                    **values,
                                    projection_sha256=projection,
                                ).projection_sha256,
                                projection,
                            )

    def test_manifest_count_limits_and_start_tolerance_boundary(self) -> None:
        payload, review, manifest_pin, review_pin, universe = valid_payloads(
            128
        )
        self.assertLess(len(payload), MATCH_BINDING_ARTIFACT_MAX_BYTES)
        decoded = decode_binding_universe(
            payload,
            review,
            manifest_pin=manifest_pin,
            review_pin=review_pin,
        )
        self.assertEqual(decoded, universe)
        document = manifest_document(129)
        self.assertLess(len(json_bytes(document)), MATCH_BINDING_ARTIFACT_MAX_BYTES)
        manifest_error(document, "binding_manifest_value")
        document = manifest_document()
        document["bindings"][0]["match"]["start_tolerance_ns"] = (  # type: ignore[index]
            900_000_000_000
        )
        document["bindings"][0]["kalshi"]["scheduled_start_wall_ns"] = (  # type: ignore[index]
            document["bindings"][0]["provider"][  # type: ignore[index]
                "scheduled_start_wall_ns"
            ]
            + 900_000_000_000
        )
        payload, review, pin, rpin, _ = _valid_payloads_from_document(document)
        decode_binding_universe(
            payload,
            review,
            manifest_pin=pin,
            review_pin=rpin,
        )

    def test_review_requires_canonical_bytes_independent_pin_and_chronology(
        self,
    ) -> None:
        payload, review, manifest_pin, _review_pin, expected = valid_payloads()
        parsed = json.loads(review)
        alternate = json.dumps(parsed, indent=2).encode("ascii")
        alternate_pin = ArtifactPin(parsed["artifact_id"], raw_sha(alternate))
        with self.assertRaisesRegex(
            ExpertContractError,
            "^binding_review_artifact$",
        ):
            decode_binding_universe(
                payload,
                alternate,
                manifest_pin=manifest_pin,
                review_pin=alternate_pin,
            )
        for suffix in (b"\n", b" "):
            changed = review + suffix
            changed_pin = ArtifactPin(
                expected.review.review_artifact_id,
                raw_sha(changed),
            )
            with self.assertRaisesRegex(
                ExpertContractError,
                "^binding_review_artifact$",
            ):
                decode_binding_universe(
                    payload,
                    changed,
                    manifest_pin=manifest_pin,
                    review_pin=changed_pin,
                )
        changed = canonical_binding_review_artifact_bytes(
            review_artifact_id=parsed["artifact_id"],
            review_artifact_created_wall_ns=parsed[
                "artifact_created_wall_ns"
            ],
            binding_artifact_id=parsed["binding_artifact_id"],
            binding_artifact_sha256=parsed["binding_artifact_sha256"],
            decision=parsed["decision"],
            reviewer_id=parsed["reviewer_id"],
            reviewed_wall_ns=999_999_999,
            review_evidence_sha256=parsed["review_evidence_sha256"],
        )
        changed_pin = ArtifactPin(parsed["artifact_id"], raw_sha(changed))
        with self.assertRaisesRegex(
            ExpertContractError,
            "^binding_review_time$",
        ):
            decode_binding_universe(
                payload,
                changed,
                manifest_pin=manifest_pin,
                review_pin=changed_pin,
            )

    def test_review_shape_value_artifact_and_evidence_branches(self) -> None:
        payload, review, manifest_pin, _review_pin, _ = valid_payloads()
        parsed = json.loads(review)
        cases = (
            (
                dict(parsed, reviewer_id=7),
                "binding_review_shape",
            ),
            (
                dict(parsed, decision="rejected"),
                "binding_review_value",
            ),
            (
                dict(parsed, binding_artifact_sha256=SHA_E),
                "binding_review_artifact",
            ),
            (
                dict(parsed, review_evidence_sha256=SHA_E),
                "binding_review_evidence",
            ),
        )
        for changed_value, expected_error in cases:
            changed = json_bytes(changed_value)
            pin = ArtifactPin(parsed["artifact_id"], raw_sha(changed))
            with self.subTest(error=expected_error):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    f"^{expected_error}$",
                ):
                    decode_binding_universe(
                        payload,
                        changed,
                        manifest_pin=manifest_pin,
                        review_pin=pin,
                    )

    def test_global_collision_order_and_player_collision_precedence(self) -> None:
        document = manifest_document(2)
        document["bindings"].reverse()  # type: ignore[union-attr]
        manifest_error(document, "binding_manifest_order")
        document = manifest_document(2)
        document["bindings"][1]["canonical_match_id"] = (  # type: ignore[index]
            document["bindings"][0]["canonical_match_id"]  # type: ignore[index]
        )
        document["bindings"][1]["provider"]["home_player"][  # type: ignore[index]
            "provider_player_id"
        ] = document["bindings"][0]["provider"]["home_player"][  # type: ignore[index]
            "provider_player_id"
        ]
        document["bindings"][1]["kalshi"]["markets"][0][  # type: ignore[index]
            "yes_provider_player_id"
        ] = document["bindings"][1]["provider"]["home_player"][  # type: ignore[index]
            "provider_player_id"
        ]
        manifest_error(document, "binding_manifest_collision")
        document = manifest_document(2)
        document["bindings"][1]["provider"]["home_player"][  # type: ignore[index]
            "provider_player_id"
        ] = document["bindings"][0]["provider"]["home_player"][  # type: ignore[index]
            "provider_player_id"
        ]
        document["bindings"][1]["kalshi"]["markets"][0][  # type: ignore[index]
            "yes_provider_player_id"
        ] = document["bindings"][1]["provider"]["home_player"][  # type: ignore[index]
            "provider_player_id"
        ]
        manifest_error(
            document,
            "binding_manifest_provider_player_collision",
        )
        document = manifest_document(2)
        first = document["bindings"][0]["provider"]["home_player"]  # type: ignore[index]
        second = document["bindings"][1]["provider"]["home_player"]  # type: ignore[index]
        second["canonical_player_id"] = first["canonical_player_id"]
        document["bindings"][1]["kalshi"]["markets"][0][  # type: ignore[index]
            "yes_canonical_player_id"
        ] = second["canonical_player_id"]
        manifest_error(
            document,
            "binding_manifest_canonical_player_collision",
        )
        document = manifest_document(2)
        first_provider = document["bindings"][0]["provider"]  # type: ignore[index]
        second_provider = document["bindings"][1]["provider"]  # type: ignore[index]
        second_provider["source_lineage_sha256"] = SHA_E
        for market_index, player_key in enumerate(
            ("home_player", "away_player")
        ):
            second_provider[player_key]["provider_player_id"] = (  # type: ignore[index]
                first_provider[player_key]["provider_player_id"]  # type: ignore[index]
            )
            second_provider[player_key]["canonical_player_id"] = (  # type: ignore[index]
                first_provider[player_key]["canonical_player_id"]  # type: ignore[index]
            )
            second_market = document["bindings"][1]["kalshi"]["markets"][  # type: ignore[index]
                market_index
            ]
            second_market["yes_provider_player_id"] = second_provider[  # type: ignore[index]
                player_key
            ]["provider_player_id"]
            second_market["yes_canonical_player_id"] = second_provider[  # type: ignore[index]
                player_key
            ]["canonical_player_id"]
        payload, review, pin, review_pin, expected = (
            _valid_payloads_from_document(document)
        )
        self.assertEqual(
            decode_binding_universe(
                payload,
                review,
                manifest_pin=pin,
                review_pin=review_pin,
            ),
            expected,
        )

    def test_route_order_side_swap_missing_extra_and_no_all_fail(self) -> None:
        mutators = (
            lambda routes: routes.reverse(),
            lambda routes: routes.pop(),
            lambda routes: routes.append(dict(routes[0])),
            lambda routes: routes[0].__setitem__("contract_side", "no"),
            lambda routes: routes[0].__setitem__("player_side", "away"),
            lambda routes: routes[0].__setitem__(
                "market_ticker",
                routes[1]["market_ticker"],
            ),
        )
        for index, mutate in enumerate(mutators):
            document = manifest_document()
            routes = document["bindings"][0]["kalshi"][  # type: ignore[index]
                "authorized_routes"
            ]
            mutate(routes)
            with self.subTest(case=index):
                manifest_error(document, "binding_manifest_route")


def _valid_payloads_from_document(
    document: dict[str, object],
) -> tuple[bytes, bytes, ArtifactPin, ArtifactPin, BindingUniverse]:
    manifest_payload = json_bytes(document)
    manifest_pin = ArtifactPin(
        document["artifact_id"],  # type: ignore[arg-type]
        raw_sha(manifest_payload),
    )
    bindings, metadata = projections_for(document, manifest_pin)
    evidence = compute_binding_review_evidence_sha256(
        manifest_pin,
        bindings,
        metadata,
    )
    values = {
        "review_artifact_id": "binding-review-custom",
        "review_artifact_created_wall_ns": 1_200_000_000,
        "binding_artifact_id": manifest_pin.artifact_id,
        "binding_artifact_sha256": manifest_pin.artifact_sha256,
        "decision": "approved",
        "reviewer_id": "reviewer-custom",
        "reviewed_wall_ns": 1_100_000_000,
        "review_evidence_sha256": evidence,
    }
    review_payload = canonical_binding_review_artifact_bytes(**values)
    review_pin = ArtifactPin(values["review_artifact_id"], raw_sha(review_payload))
    review = BindingReviewDecision(
        review_artifact_sha256=review_pin.artifact_sha256,
        **values,
    )
    universe = BindingUniverse(
        manifest_pin.artifact_id,
        manifest_pin.artifact_sha256,
        review,
        bindings,
        metadata,
        compute_binding_universe_sha256(
            manifest_pin,
            review,
            bindings,
            metadata,
        ),
    )
    return manifest_payload, review_payload, manifest_pin, review_pin, universe


class ResolutionTests(unittest.TestCase):
    def test_resolve_metadata_and_direct_yes_route_are_exact(self) -> None:
        universe = valid_payloads()[-1]
        binding = universe.bindings[0]
        state = live_state(binding)
        resolved = resolve_binding(
            state,
            binding.kalshi_event_ticker,
            universe,
        )
        self.assertIs(resolved, binding)
        equal_binding = replace(binding)
        self.assertEqual(
            binding_metadata_for(universe, equal_binding),
            universe.metadata[0],
        )
        home = require_authorized_route(
            universe,
            equal_binding,
            binding.home_market_ticker,
            ContractSide.YES,
        )
        self.assertEqual(home.player_side, PlayerSide.HOME)
        self.assertIs(
            match_binding_module.player_side_for_contract,
            player_side_for_contract,
        )
        self.assertEqual(
            player_side_for_contract(
                binding,
                binding.home_market_ticker,
                ContractSide.NO,
            ),
            PlayerSide.AWAY,
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^binding_route_unsupported$",
        ):
            require_authorized_route(
                universe,
                binding,
                binding.home_market_ticker,
                ContractSide.NO,
            )

    def test_resolution_rejects_identity_tolerance_and_ticker_shortcuts(self) -> None:
        universe = valid_payloads()[-1]
        binding = universe.bindings[0]
        state = live_state(binding)
        mutations = (
            {"provider_match_id": "another-match"},
            {
                "home_player_id": binding.provider_away_player_id,
                "away_player_id": binding.provider_home_player_id,
            },
            {"scheduled_start_wall_ns": binding.scheduled_start_wall_ns + 1},
            {"source_lineage_sha256": SHA_E},
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(
                    ExpertContractError,
                    "^binding_not_found$",
                ):
                    resolve_binding(
                        live_state(binding, **changes),
                        binding.kalshi_event_ticker,
                        universe,
                    )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^binding_not_found$",
        ):
            resolve_binding(state, "TENNISMATCH", universe)
        with self.assertRaisesRegex(
            ExpertContractError,
            "^binding_not_in_universe$",
        ):
            binding_metadata_for(
                universe,
                replace(binding, provider_match_id="field-different"),
            )

    def test_public_api_type_precedence_is_left_to_right(self) -> None:
        universe = valid_payloads()[-1]
        binding = universe.bindings[0]
        state = live_state(binding)
        rows = (
            (
                binding_universe_sha256,
                (object(),),
                "universe",
            ),
            (
                binding_metadata_for,
                (object(), object()),
                "universe",
            ),
            (
                binding_metadata_for,
                (universe, object()),
                "binding",
            ),
            (
                resolve_binding,
                (object(), object(), object()),
                "provider_state",
            ),
            (
                resolve_binding,
                (state, object(), object()),
                "kalshi_event_ticker",
            ),
            (
                resolve_binding,
                (state, binding.kalshi_event_ticker, object()),
                "universe",
            ),
            (
                require_authorized_route,
                (object(), object(), object(), object()),
                "universe",
            ),
            (
                require_authorized_route,
                (universe, object(), object(), object()),
                "binding",
            ),
            (
                require_authorized_route,
                (universe, binding, object(), object()),
                "market_ticker",
            ),
            (
                require_authorized_route,
                (
                    universe,
                    binding,
                    binding.home_market_ticker,
                    object(),
                ),
                "contract_side",
            ),
        )
        for function, args, message in rows:
            with self.subTest(function=function.__name__, message=message):
                with self.assertRaisesRegex(TypeError, f"^{message}$"):
                    function(*args)


class PinnedArtifactTests(unittest.TestCase):
    def request(self, **changes: object) -> PinnedArtifactReadRequest:
        values: dict[str, object] = {
            "artifact_pin": ArtifactPin("artifact-1", SHA_A),
            "path": Path("/tmp/external/binding.json"),
            "repo_root": Path("/tmp/repository"),
            "forbidden_root": Path("/tmp/state"),
            "max_bytes": 100,
        }
        values.update(changes)
        return PinnedArtifactReadRequest(**values)  # type: ignore[arg-type]

    def test_request_and_result_are_exact_safe_and_bounded(self) -> None:
        request = self.request()
        self.assertNotIn(str(request.path), repr(request))
        data = b"binding"
        result = PinnedArtifactBytes(
            ArtifactPin("artifact-1", raw_sha(data)),
            data,
        )
        self.assertNotIn("binding", repr(result))
        with self.assertRaisesRegex(
            PinnedArtifactError,
            "^pinned_artifact_path$",
        ):
            self.request(path=Path("relative"))
        with self.assertRaisesRegex(
            PinnedArtifactError,
            "^pinned_artifact_path$",
        ):
            self.request(path=Path("/tmp/a/../b"))
        with self.assertRaisesRegex(
            PinnedArtifactError,
            "^pinned_artifact_max_bytes$",
        ):
            self.request(max_bytes=True)
        with self.assertRaisesRegex(
            PinnedArtifactError,
            "^pinned_artifact_max_bytes$",
        ):
            self.request(max_bytes=MATCH_BINDING_ARTIFACT_MAX_BYTES + 1)
        with self.assertRaisesRegex(
            PinnedArtifactError,
            "^pinned_artifact_digest$",
        ):
            PinnedArtifactBytes(ArtifactPin("artifact-1", SHA_A), data)

    def test_reader_delegates_exactly_once_and_maps_failures(self) -> None:
        data = b"binding"
        pin = ArtifactPin("artifact-1", raw_sha(data))
        request = self.request(artifact_pin=pin)
        with mock.patch(
            "inci_tennis_io.pinned_artifacts.read_pinned_file",
            return_value=PinnedBytes(data=data, sha256=pin.artifact_sha256),
        ) as read:
            result = read_pinned_artifact(request)
        self.assertEqual(result, PinnedArtifactBytes(pin, data))
        read.assert_called_once_with(
            request.path,
            expected_sha256=pin.artifact_sha256,
            repo_root=request.repo_root,
            max_bytes=request.max_bytes,
            forbidden_root=request.forbidden_root,
        )
        with mock.patch(
            "inci_tennis_io.pinned_artifacts.read_pinned_file",
            side_effect=PinnedFileError("dynamic secret path"),
        ):
            with self.assertRaises(PinnedArtifactError) as caught:
                read_pinned_artifact(request)
            self.assertEqual(str(caught.exception), "pinned_artifact_read")
            self.assertIsNone(caught.exception.__cause__)
        with mock.patch(
            "inci_tennis_io.pinned_artifacts.read_pinned_file",
            return_value=object(),
        ):
            with self.assertRaisesRegex(
                PinnedArtifactError,
                "^pinned_artifact_result$",
            ):
                read_pinned_artifact(request)

    def test_reader_enforces_request_limit_against_hostile_result(
        self,
    ) -> None:
        exact_data = b"1234567"
        exact_pin = ArtifactPin("artifact-1", raw_sha(exact_data))
        exact_request = self.request(
            artifact_pin=exact_pin,
            max_bytes=len(exact_data),
        )
        with mock.patch(
            "inci_tennis_io.pinned_artifacts.read_pinned_file",
            return_value=PinnedBytes(
                data=exact_data,
                sha256=exact_pin.artifact_sha256,
            ),
        ):
            self.assertEqual(
                read_pinned_artifact(exact_request),
                PinnedArtifactBytes(exact_pin, exact_data),
            )

        oversized_data = exact_data + b"8"
        oversized_pin = ArtifactPin(
            "artifact-1",
            raw_sha(oversized_data),
        )
        oversized_request = self.request(
            artifact_pin=oversized_pin,
            max_bytes=len(exact_data),
        )
        with mock.patch(
            "inci_tennis_io.pinned_artifacts.read_pinned_file",
            return_value=PinnedBytes(
                data=oversized_data,
                sha256=oversized_pin.artifact_sha256,
            ),
        ):
            with self.assertRaises(PinnedArtifactError) as caught:
                read_pinned_artifact(oversized_request)
        self.assertEqual(
            str(caught.exception),
            "pinned_artifact_result",
        )
        self.assertIsNone(caught.exception.__cause__)

    def test_reader_rejects_hostile_result_fields_without_operator_leak(
        self,
    ) -> None:
        class BadStr(str):
            def __ne__(self, other: object) -> bool:
                raise RuntimeError("secret operator leak")

        data = b"binding"
        pin = ArtifactPin("artifact-1", raw_sha(data))
        request = self.request(
            artifact_pin=pin,
            max_bytes=len(data),
        )
        hostile_results = (
            PinnedBytes(
                data=bytearray(data),  # type: ignore[arg-type]
                sha256=pin.artifact_sha256,
            ),
            PinnedBytes(
                data=data,
                sha256=BadStr(pin.artifact_sha256),
            ),
            PinnedBytes(data=data, sha256="A" * 64),
            PinnedBytes(
                data=b"forged",
                sha256=pin.artifact_sha256,
            ),
        )
        for result in hostile_results:
            with self.subTest(
                data_type=type(result.data).__name__,
                digest_type=type(result.sha256).__name__,
            ):
                with mock.patch(
                    "inci_tennis_io.pinned_artifacts.read_pinned_file",
                    return_value=result,
                ):
                    with self.assertRaises(PinnedArtifactError) as caught:
                        read_pinned_artifact(request)
                self.assertEqual(
                    str(caught.exception),
                    "pinned_artifact_result",
                )
                self.assertIsNone(caught.exception.__cause__)

    def test_review_limit_is_distinct_and_within_wrapper_ceiling(self) -> None:
        self.assertEqual(MATCH_BINDING_ARTIFACT_MAX_BYTES, 1_048_576)
        self.assertEqual(BINDING_REVIEW_ARTIFACT_MAX_BYTES, 16_384)
        self.assertLess(
            BINDING_REVIEW_ARTIFACT_MAX_BYTES,
            MATCH_BINDING_ARTIFACT_MAX_BYTES,
        )


class SchemaAndFixtureTests(unittest.TestCase):
    def test_schemas_are_closed_local_draft_2020_12_documents(self) -> None:
        expected_ids = {
            "match-binding-v1.schema.json": (
                "urn:inci-tennis-expert:schema:match-binding-v1"
            ),
            "binding-review-v1.schema.json": (
                "urn:inci-tennis-expert:schema:binding-review-v1"
            ),
        }
        for name, expected_id in expected_ids.items():
            schema = json.loads((SCHEMAS / name).read_bytes())
            with self.subTest(schema=name):
                self.assertEqual(
                    schema["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )
                self.assertEqual(schema["$id"], expected_id)
                self.assertNotIn("http://", json.dumps(schema))
                for node in _object_schema_nodes(schema):
                    self.assertIs(node.get("additionalProperties"), False)
        manifest_schema = json.loads(
            (SCHEMAS / "match-binding-v1.schema.json").read_bytes()
        )
        bindings = manifest_schema["properties"]["bindings"]
        self.assertEqual(bindings["minItems"], 1)
        self.assertEqual(bindings["maxItems"], 128)

    def test_synthetic_fixtures_decode_to_expected_universe(self) -> None:
        manifest_payload = (
            FIXTURES / "match_binding_schema_example.json"
        ).read_bytes()
        review_payload = (
            FIXTURES / "binding_review_schema_example.json"
        ).read_bytes()
        manifest_value = json.loads(manifest_payload)
        review_value = json.loads(review_payload)
        universe = decode_binding_universe(
            manifest_payload,
            review_payload,
            manifest_pin=ArtifactPin(
                manifest_value["artifact_id"],
                raw_sha(manifest_payload),
            ),
            review_pin=ArtifactPin(
                review_value["artifact_id"],
                raw_sha(review_payload),
            ),
        )
        self.assertEqual(len(universe.bindings), 1)
        self.assertTrue(
            universe.bindings[0].canonical_match_id.startswith("synthetic-")
        )


def _object_schema_nodes(value: object) -> tuple[dict[str, object], ...]:
    found: list[dict[str, object]] = []
    if type(value) is dict:
        if value.get("type") == "object":
            found.append(value)
        for nested in value.values():
            found.extend(_object_schema_nodes(nested))
    elif type(value) is list:
        for nested in value:
            found.extend(_object_schema_nodes(nested))
    return tuple(found)


if __name__ == "__main__":
    unittest.main()
