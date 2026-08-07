from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from inci_tennis_expert.contracts import (
    ExpertContractError,
    _boolean,
    _exact,
    _exact_self,
    _integer,
    _safe_id,
    _sha256,
    expert_contract_sha256,
)
from inci_tennis_expert.prematch_model import HistoricalRow


_ENTITLEMENT_SCHEMA_SHA256: Final[str] = expert_contract_sha256(
    {"schema": "historical-entitlement-v1"}
)
_MANIFEST_SCHEMA_SHA256: Final[str] = expert_contract_sha256(
    {"schema": "historical-dataset-manifest-v1"}
)


@dataclass(frozen=True, slots=True)
class HistoricalEntitlementArtifact:
    entitlement_id: str
    provider_id: str
    product_id: str
    source_lineage_sha256: str
    authorized_dataset_sha256: str
    issued_wall_ns: int
    not_before_wall_ns: int
    not_after_wall_ns: int
    retention_delete_after_wall_ns: int
    publication_not_before_wall_ns: int
    active: bool
    analysis_use_granted: bool
    derivative_use_granted: bool
    artifact_sha256: str
    schema_sha256: str = _ENTITLEMENT_SCHEMA_SHA256

    def __post_init__(self) -> None:
        _exact_self(self, HistoricalEntitlementArtifact)
        _safe_id(self.entitlement_id, "entitlement_id")
        _safe_id(self.provider_id, "provider_id")
        _safe_id(self.product_id, "product_id")
        _sha256(self.source_lineage_sha256, "source_lineage_sha256")
        _sha256(
            self.authorized_dataset_sha256,
            "authorized_dataset_sha256",
        )
        _integer(self.issued_wall_ns, "issued_wall_ns")
        _integer(self.not_before_wall_ns, "not_before_wall_ns")
        _integer(self.not_after_wall_ns, "not_after_wall_ns")
        _integer(
            self.retention_delete_after_wall_ns,
            "retention_delete_after_wall_ns",
        )
        _integer(
            self.publication_not_before_wall_ns,
            "publication_not_before_wall_ns",
        )
        _boolean(self.active, "active")
        _boolean(self.analysis_use_granted, "analysis_use_granted")
        _boolean(self.derivative_use_granted, "derivative_use_granted")
        _sha256(self.artifact_sha256, "artifact_sha256")
        _sha256(self.schema_sha256, "schema_sha256")
        if self.not_before_wall_ns > self.not_after_wall_ns:
            raise ExpertContractError("authorized_period")


@dataclass(frozen=True, slots=True)
class HistoricalDatasetManifest:
    dataset_id: str
    provider_id: str
    product_id: str
    source_lineage_sha256: str
    declared_dataset_sha256: str
    observed_dataset_sha256: str
    row_count: int
    min_match_start_wall_ns: int
    max_match_start_wall_ns: int
    frozen_at_wall_ns: int
    manifest_sha256: str
    schema_sha256: str = _MANIFEST_SCHEMA_SHA256

    def __post_init__(self) -> None:
        _exact_self(self, HistoricalDatasetManifest)
        _safe_id(self.dataset_id, "dataset_id")
        _safe_id(self.provider_id, "provider_id")
        _safe_id(self.product_id, "product_id")
        _sha256(self.source_lineage_sha256, "source_lineage_sha256")
        _sha256(self.declared_dataset_sha256, "declared_dataset_sha256")
        _sha256(self.observed_dataset_sha256, "observed_dataset_sha256")
        _integer(self.row_count, "row_count")
        _integer(self.min_match_start_wall_ns, "min_match_start_wall_ns")
        _integer(self.max_match_start_wall_ns, "max_match_start_wall_ns")
        _integer(self.frozen_at_wall_ns, "frozen_at_wall_ns")
        _sha256(self.manifest_sha256, "manifest_sha256")
        _sha256(self.schema_sha256, "schema_sha256")
        if self.min_match_start_wall_ns > self.max_match_start_wall_ns:
            raise ExpertContractError("match_start_window")


@dataclass(frozen=True, slots=True)
class HistoricalAccessDecision:
    authorized: bool
    reason: str
    provider_id: str
    product_id: str
    source_lineage_sha256: str
    dataset_sha256: str
    official_window_start_wall_ns: int
    official_window_end_wall_ns: int
    entitlement_sha256: str
    manifest_sha256: str
    decision_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, HistoricalAccessDecision)
        _boolean(self.authorized, "authorized")
        _safe_id(self.reason, "reason")
        _safe_id(self.provider_id, "provider_id")
        _safe_id(self.product_id, "product_id")
        _sha256(self.source_lineage_sha256, "source_lineage_sha256")
        _sha256(self.dataset_sha256, "dataset_sha256")
        _integer(
            self.official_window_start_wall_ns,
            "official_window_start_wall_ns",
        )
        _integer(
            self.official_window_end_wall_ns,
            "official_window_end_wall_ns",
        )
        _sha256(self.entitlement_sha256, "entitlement_sha256")
        _sha256(self.manifest_sha256, "manifest_sha256")
        _sha256(self.decision_sha256, "decision_sha256")
        if (
            self.official_window_start_wall_ns
            > self.official_window_end_wall_ns
        ):
            raise ExpertContractError("official_window")
        if self.authorized != (self.reason == "authorized"):
            raise ExpertContractError("reason")


def _access_payload(
    *,
    authorized: bool,
    reason: str,
    manifest: HistoricalDatasetManifest,
    official_window_start_wall_ns: int,
    official_window_end_wall_ns: int,
) -> dict[str, object]:
    return {
        "authorized": authorized,
        "reason": reason,
        "provider_id": manifest.provider_id,
        "product_id": manifest.product_id,
        "source_lineage_sha256": manifest.source_lineage_sha256,
        "dataset_sha256": manifest.observed_dataset_sha256,
        "official_window_start_wall_ns": official_window_start_wall_ns,
        "official_window_end_wall_ns": official_window_end_wall_ns,
    }


def _entitlement_payload(
    entitlement: HistoricalEntitlementArtifact,
) -> dict[str, object]:
    return {
        "schema": "historical_entitlement_v1",
        "entitlement_id": entitlement.entitlement_id,
        "provider_id": entitlement.provider_id,
        "product_id": entitlement.product_id,
        "source_lineage_sha256": entitlement.source_lineage_sha256,
        "authorized_dataset_sha256": entitlement.authorized_dataset_sha256,
        "issued_wall_ns": entitlement.issued_wall_ns,
        "not_before_wall_ns": entitlement.not_before_wall_ns,
        "not_after_wall_ns": entitlement.not_after_wall_ns,
        "retention_delete_after_wall_ns": (
            entitlement.retention_delete_after_wall_ns
        ),
        "publication_not_before_wall_ns": (
            entitlement.publication_not_before_wall_ns
        ),
        "active": entitlement.active,
        "analysis_use_granted": entitlement.analysis_use_granted,
        "derivative_use_granted": entitlement.derivative_use_granted,
        "artifact_sha256": entitlement.artifact_sha256,
        "schema_sha256": entitlement.schema_sha256,
    }


def _manifest_payload(manifest: HistoricalDatasetManifest) -> dict[str, object]:
    return {
        "schema": "historical_dataset_manifest_v1",
        "dataset_id": manifest.dataset_id,
        "provider_id": manifest.provider_id,
        "product_id": manifest.product_id,
        "source_lineage_sha256": manifest.source_lineage_sha256,
        "declared_dataset_sha256": manifest.declared_dataset_sha256,
        "observed_dataset_sha256": manifest.observed_dataset_sha256,
        "row_count": manifest.row_count,
        "min_match_start_wall_ns": manifest.min_match_start_wall_ns,
        "max_match_start_wall_ns": manifest.max_match_start_wall_ns,
        "frozen_at_wall_ns": manifest.frozen_at_wall_ns,
        "manifest_sha256": manifest.manifest_sha256,
        "schema_sha256": manifest.schema_sha256,
    }


def _decision(
    *,
    reason: str,
    entitlement: HistoricalEntitlementArtifact,
    manifest: HistoricalDatasetManifest,
    official_window_start_wall_ns: int,
    official_window_end_wall_ns: int,
) -> HistoricalAccessDecision:
    authorized = reason == "authorized"
    entitlement_sha256 = expert_contract_sha256(
        _entitlement_payload(entitlement)
    )
    manifest_sha256 = expert_contract_sha256(_manifest_payload(manifest))
    payload = _access_payload(
        authorized=authorized,
        reason=reason,
        manifest=manifest,
        official_window_start_wall_ns=official_window_start_wall_ns,
        official_window_end_wall_ns=official_window_end_wall_ns,
    )
    payload["entitlement_sha256"] = entitlement_sha256
    payload["manifest_sha256"] = manifest_sha256
    return HistoricalAccessDecision(
        **payload,
        decision_sha256=expert_contract_sha256(
            {"schema": "historical_access_decision_v1", **payload}
        ),
    )


def authorize_historical_dataset(
    entitlement: HistoricalEntitlementArtifact,
    manifest: HistoricalDatasetManifest,
    *,
    official_window_start_wall_ns: int,
    official_window_end_wall_ns: int,
) -> HistoricalAccessDecision:
    if type(entitlement) is not HistoricalEntitlementArtifact:
        raise TypeError("entitlement")
    if type(manifest) is not HistoricalDatasetManifest:
        raise TypeError("manifest")
    _integer(
        official_window_start_wall_ns,
        "official_window_start_wall_ns",
    )
    _integer(official_window_end_wall_ns, "official_window_end_wall_ns")
    if official_window_start_wall_ns > official_window_end_wall_ns:
        raise ExpertContractError("official_window")
    reason = "authorized"
    if not entitlement.active:
        reason = "entitlement_inactive"
    elif (
        not entitlement.analysis_use_granted
        or not entitlement.derivative_use_granted
    ):
        reason = "use_not_granted"
    elif entitlement.provider_id != manifest.provider_id:
        reason = "provider_mismatch"
    elif entitlement.product_id != manifest.product_id:
        reason = "product_mismatch"
    elif entitlement.source_lineage_sha256 != manifest.source_lineage_sha256:
        reason = "lineage_mismatch"
    elif manifest.declared_dataset_sha256 != manifest.observed_dataset_sha256:
        reason = "dataset_digest_drift"
    elif (
        entitlement.authorized_dataset_sha256
        != manifest.observed_dataset_sha256
    ):
        reason = "dataset_not_entitled"
    elif (
        official_window_start_wall_ns < entitlement.not_before_wall_ns
        or official_window_end_wall_ns > entitlement.not_after_wall_ns
    ):
        reason = "official_window_unauthorized"
    elif entitlement.not_after_wall_ns < manifest.frozen_at_wall_ns:
        reason = "entitlement_expired"
    elif (
        entitlement.publication_not_before_wall_ns
        > entitlement.retention_delete_after_wall_ns
    ):
        reason = "retention_publication_conflict"
    elif (
        official_window_end_wall_ns
        > entitlement.retention_delete_after_wall_ns
    ):
        reason = "retention_expired"
    return _decision(
        reason=reason,
        entitlement=entitlement,
        manifest=manifest,
        official_window_start_wall_ns=official_window_start_wall_ns,
        official_window_end_wall_ns=official_window_end_wall_ns,
    )


def freeze_historical_rows(
    rows: tuple[HistoricalRow, ...],
    decision: HistoricalAccessDecision,
) -> tuple[HistoricalRow, ...]:
    if type(rows) is not tuple:
        raise TypeError("rows")
    if type(decision) is not HistoricalAccessDecision:
        raise TypeError("decision")
    if not decision.authorized:
        raise ExpertContractError("historical_access_denied")
    for row in rows:
        if type(row) is not HistoricalRow:
            raise TypeError("rows")
        if row.source_lineage_sha256 != decision.source_lineage_sha256:
            raise ExpertContractError("source_lineage_sha256")
        if (
            row.match_start_wall_ns
            < decision.official_window_start_wall_ns
            or row.match_start_wall_ns
            > decision.official_window_end_wall_ns
        ):
            raise ExpertContractError("official_window")
    return rows


__all__ = (
    "HistoricalAccessDecision",
    "HistoricalDatasetManifest",
    "HistoricalEntitlementArtifact",
    "authorize_historical_dataset",
    "freeze_historical_rows",
)
