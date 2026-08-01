"""Offline deterministic Task-9 transition-evidence contracts."""

from __future__ import annotations

import hashlib
import json
import errno
import grp as _grp
import os as _os
import pwd as _pwd
import stat as _stat
import sys as _sys
import threading as _threading
import weakref as _weakref
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from types import MappingProxyType as _MappingProxyType
from typing import Final, TypeAlias


Task9DecoderRowV2: TypeAlias = tuple[
    str,
    str,
    int,
    int,
    str,
    str | None,
    str,
]


class Task9TransitionEvidenceError(ValueError):
    """Fixed redacted failure for malformed transition evidence."""


class Task9EvidenceStageIdV1(Enum):
    PREDECESSOR_TRANSITION_MANIFEST = "PREDECESSOR_TRANSITION_MANIFEST"
    PREDECESSOR_TRANSITION_REVIEW = "PREDECESSOR_TRANSITION_REVIEW"
    POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW = (
        "POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW"
    )
    FUNCTIONAL_WAVE_REVIEW_A = "FUNCTIONAL_WAVE_REVIEW_A"
    FUNCTIONAL_WAVE_REVIEW_B = "FUNCTIONAL_WAVE_REVIEW_B"
    FUNCTIONAL_WAVE_REVIEW_C = "FUNCTIONAL_WAVE_REVIEW_C"
    FUNCTIONAL_WAVE_REVIEW_D = "FUNCTIONAL_WAVE_REVIEW_D"
    FUNCTIONAL_WAVE_REVIEW_E = "FUNCTIONAL_WAVE_REVIEW_E"
    FUNCTIONAL_WAVE_REVIEW_R = "FUNCTIONAL_WAVE_REVIEW_R"
    FINAL_RESEAL_TRANSITION = "FINAL_RESEAL_TRANSITION"
    FINAL_RESEAL_REVIEW = "FINAL_RESEAL_REVIEW"
    RELEASE_EVIDENCE = "RELEASE_EVIDENCE"


class Task9StageOutputKindV1(Enum):
    ARTIFACT = "ARTIFACT"
    PROCEDURAL_ASSIGNMENT_RECEIPT = "PROCEDURAL_ASSIGNMENT_RECEIPT"
    CHAIN_ACCEPTANCE_RECEIPT = "CHAIN_ACCEPTANCE_RECEIPT"


class Task9FunctionalWaveIdV1(Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    R = "R"


class Task9EvidenceBundleIdV1(Enum):
    PREDECESSOR = "PREDECESSOR"
    FUNCTIONAL_A = "FUNCTIONAL_A"
    FUNCTIONAL_B = "FUNCTIONAL_B"
    FUNCTIONAL_C = "FUNCTIONAL_C"
    FUNCTIONAL_D = "FUNCTIONAL_D"
    FUNCTIONAL_E = "FUNCTIONAL_E"
    FUNCTIONAL_R = "FUNCTIONAL_R"
    FINAL_RESEAL = "FINAL_RESEAL"
    RELEASE_SUPPORT = "RELEASE_SUPPORT"


class Task9SealIdV1(Enum):
    PREDECESSOR_SOURCE = "PREDECESSOR_SOURCE"
    PREDECESSOR_RESOURCE = "PREDECESSOR_RESOURCE"
    FUNCTIONAL_A_SOURCE = "FUNCTIONAL_A_SOURCE"
    FUNCTIONAL_A_RESOURCE = "FUNCTIONAL_A_RESOURCE"
    FUNCTIONAL_B_SOURCE = "FUNCTIONAL_B_SOURCE"
    FUNCTIONAL_B_RESOURCE = "FUNCTIONAL_B_RESOURCE"
    FUNCTIONAL_C_SOURCE = "FUNCTIONAL_C_SOURCE"
    FUNCTIONAL_C_RESOURCE = "FUNCTIONAL_C_RESOURCE"
    FUNCTIONAL_D_SOURCE = "FUNCTIONAL_D_SOURCE"
    FUNCTIONAL_D_RESOURCE = "FUNCTIONAL_D_RESOURCE"
    FUNCTIONAL_E_SOURCE = "FUNCTIONAL_E_SOURCE"
    FUNCTIONAL_E_RESOURCE = "FUNCTIONAL_E_RESOURCE"
    FUNCTIONAL_R_SOURCE = "FUNCTIONAL_R_SOURCE"
    FUNCTIONAL_R_RESOURCE = "FUNCTIONAL_R_RESOURCE"
    FINAL_SOURCE = "FINAL_SOURCE"
    FINAL_RESOURCE = "FINAL_RESOURCE"


class Task9ProceduralAssignmentScopeV1(Enum):
    PREDECESSOR_TRANSITION_MANIFEST = "PREDECESSOR_TRANSITION_MANIFEST"
    PREDECESSOR_TRANSITION_REVIEW = "PREDECESSOR_TRANSITION_REVIEW"
    POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW = "POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW"
    FUNCTIONAL_WAVE_REVIEW_A = "FUNCTIONAL_WAVE_REVIEW_A"
    FUNCTIONAL_WAVE_REVIEW_B = "FUNCTIONAL_WAVE_REVIEW_B"
    FUNCTIONAL_WAVE_REVIEW_C = "FUNCTIONAL_WAVE_REVIEW_C"
    FUNCTIONAL_WAVE_REVIEW_D = "FUNCTIONAL_WAVE_REVIEW_D"
    FUNCTIONAL_WAVE_REVIEW_E = "FUNCTIONAL_WAVE_REVIEW_E"
    FUNCTIONAL_WAVE_REVIEW_R = "FUNCTIONAL_WAVE_REVIEW_R"
    FINAL_RESEAL_TRANSITION = "FINAL_RESEAL_TRANSITION"
    FINAL_RESEAL_REVIEW = "FINAL_RESEAL_REVIEW"
    RELEASE_EVIDENCE = "RELEASE_EVIDENCE"


Task9EvidenceStageRowV1: TypeAlias = tuple[
    str, str, str, str, str, str, str, str, str
]


TASK9_EVIDENCE_STAGE_ROWS_V1: Final[
    tuple[Task9EvidenceStageRowV1, ...]
] = (
    (
        "PREDECESSOR_TRANSITION_MANIFEST",
        "PREDECESSOR_TRANSITION_MANIFEST",
        "manifest_sha256",
        "task-9-predecessor-transition-manifest-v1.json",
        "task-9-predecessor-transition-manifest-procedural-assignment-write-receipt-v1.json",
        "task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json",
        "task-9-predecessor-transition-manifest-v1.json.tmp-v1",
        "task-9-predecessor-transition-manifest-procedural-assignment-write-receipt-v1.json.tmp-v1",
        "task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json.tmp-v1",
    ),
    (
        "PREDECESSOR_TRANSITION_REVIEW",
        "PREDECESSOR_TRANSITION_REVIEW",
        "review_sha256",
        "task-9-predecessor-transition-review-v1.json",
        "task-9-predecessor-transition-review-procedural-assignment-write-receipt-v1.json",
        "task-9-predecessor-transition-review-chain-acceptance-receipt-v1.json",
        "task-9-predecessor-transition-review-v1.json.tmp-v1",
        "task-9-predecessor-transition-review-procedural-assignment-write-receipt-v1.json.tmp-v1",
        "task-9-predecessor-transition-review-chain-acceptance-receipt-v1.json.tmp-v1",
    ),
    (
        "POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW",
        "POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW",
        "review_sha256",
        "task-9-post-predecessor-amended-package-rereview-v1.json",
        "task-9-post-predecessor-amended-package-rereview-procedural-assignment-write-receipt-v1.json",
        "task-9-post-predecessor-amended-package-rereview-chain-acceptance-receipt-v1.json",
        "task-9-post-predecessor-amended-package-rereview-v1.json.tmp-v1",
        "task-9-post-predecessor-amended-package-rereview-procedural-assignment-write-receipt-v1.json.tmp-v1",
        "task-9-post-predecessor-amended-package-rereview-chain-acceptance-receipt-v1.json.tmp-v1",
    ),
    (
        "FUNCTIONAL_WAVE_REVIEW_A",
        "FUNCTIONAL_WAVE_REVIEW",
        "review_sha256",
        "task-9-functional-wave-review-a-v1.json",
        "task-9-functional-wave-review-a-procedural-assignment-write-receipt-v1.json",
        "task-9-functional-wave-review-a-chain-acceptance-receipt-v1.json",
        "task-9-functional-wave-review-a-v1.json.tmp-v1",
        "task-9-functional-wave-review-a-procedural-assignment-write-receipt-v1.json.tmp-v1",
        "task-9-functional-wave-review-a-chain-acceptance-receipt-v1.json.tmp-v1",
    ),
    (
        "FUNCTIONAL_WAVE_REVIEW_B",
        "FUNCTIONAL_WAVE_REVIEW",
        "review_sha256",
        "task-9-functional-wave-review-b-v1.json",
        "task-9-functional-wave-review-b-procedural-assignment-write-receipt-v1.json",
        "task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json",
        "task-9-functional-wave-review-b-v1.json.tmp-v1",
        "task-9-functional-wave-review-b-procedural-assignment-write-receipt-v1.json.tmp-v1",
        "task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json.tmp-v1",
    ),
    (
        "FUNCTIONAL_WAVE_REVIEW_C",
        "FUNCTIONAL_WAVE_REVIEW",
        "review_sha256",
        "task-9-functional-wave-review-c-v1.json",
        "task-9-functional-wave-review-c-procedural-assignment-write-receipt-v1.json",
        "task-9-functional-wave-review-c-chain-acceptance-receipt-v1.json",
        "task-9-functional-wave-review-c-v1.json.tmp-v1",
        "task-9-functional-wave-review-c-procedural-assignment-write-receipt-v1.json.tmp-v1",
        "task-9-functional-wave-review-c-chain-acceptance-receipt-v1.json.tmp-v1",
    ),
    (
        "FUNCTIONAL_WAVE_REVIEW_D",
        "FUNCTIONAL_WAVE_REVIEW",
        "review_sha256",
        "task-9-functional-wave-review-d-v1.json",
        "task-9-functional-wave-review-d-procedural-assignment-write-receipt-v1.json",
        "task-9-functional-wave-review-d-chain-acceptance-receipt-v1.json",
        "task-9-functional-wave-review-d-v1.json.tmp-v1",
        "task-9-functional-wave-review-d-procedural-assignment-write-receipt-v1.json.tmp-v1",
        "task-9-functional-wave-review-d-chain-acceptance-receipt-v1.json.tmp-v1",
    ),
    (
        "FUNCTIONAL_WAVE_REVIEW_E",
        "FUNCTIONAL_WAVE_REVIEW",
        "review_sha256",
        "task-9-functional-wave-review-e-v1.json",
        "task-9-functional-wave-review-e-procedural-assignment-write-receipt-v1.json",
        "task-9-functional-wave-review-e-chain-acceptance-receipt-v1.json",
        "task-9-functional-wave-review-e-v1.json.tmp-v1",
        "task-9-functional-wave-review-e-procedural-assignment-write-receipt-v1.json.tmp-v1",
        "task-9-functional-wave-review-e-chain-acceptance-receipt-v1.json.tmp-v1",
    ),
    (
        "FUNCTIONAL_WAVE_REVIEW_R",
        "FUNCTIONAL_WAVE_REVIEW",
        "review_sha256",
        "task-9-functional-wave-review-r-v1.json",
        "task-9-functional-wave-review-r-procedural-assignment-write-receipt-v1.json",
        "task-9-functional-wave-review-r-chain-acceptance-receipt-v1.json",
        "task-9-functional-wave-review-r-v1.json.tmp-v1",
        "task-9-functional-wave-review-r-procedural-assignment-write-receipt-v1.json.tmp-v1",
        "task-9-functional-wave-review-r-chain-acceptance-receipt-v1.json.tmp-v1",
    ),
    (
        "FINAL_RESEAL_TRANSITION",
        "FINAL_RESEAL_TRANSITION",
        "manifest_sha256",
        "task-9-final-reseal-transition-v1.json",
        "task-9-final-reseal-transition-procedural-assignment-write-receipt-v1.json",
        "task-9-final-reseal-transition-chain-acceptance-receipt-v1.json",
        "task-9-final-reseal-transition-v1.json.tmp-v1",
        "task-9-final-reseal-transition-procedural-assignment-write-receipt-v1.json.tmp-v1",
        "task-9-final-reseal-transition-chain-acceptance-receipt-v1.json.tmp-v1",
    ),
    (
        "FINAL_RESEAL_REVIEW",
        "FINAL_RESEAL_REVIEW",
        "review_sha256",
        "task-9-final-reseal-review-v1.json",
        "task-9-final-reseal-review-procedural-assignment-write-receipt-v1.json",
        "task-9-final-reseal-review-chain-acceptance-receipt-v1.json",
        "task-9-final-reseal-review-v1.json.tmp-v1",
        "task-9-final-reseal-review-procedural-assignment-write-receipt-v1.json.tmp-v1",
        "task-9-final-reseal-review-chain-acceptance-receipt-v1.json.tmp-v1",
    ),
    (
        "RELEASE_EVIDENCE",
        "RELEASE_EVIDENCE",
        "record_sha256",
        "task-9-release-evidence-v1.json",
        "task-9-release-evidence-procedural-assignment-write-receipt-v1.json",
        "task-9-release-evidence-chain-acceptance-receipt-v1.json",
        "task-9-release-evidence-v1.json.tmp-v1",
        "task-9-release-evidence-procedural-assignment-write-receipt-v1.json.tmp-v1",
        "task-9-release-evidence-chain-acceptance-receipt-v1.json.tmp-v1",
    ),
)

TASK9_TRANSIENT_WRITE_PATHS_V1: Final[tuple[str, ...]] = tuple(
    path for row in TASK9_EVIDENCE_STAGE_ROWS_V1 for path in row[6:9]
)
TASK9_STAGE_OWNED_PATHS_V1: Final[tuple[str, ...]] = tuple(
    path for row in TASK9_EVIDENCE_STAGE_ROWS_V1 for path in row[3:6]
) + TASK9_TRANSIENT_WRITE_PATHS_V1


class _Task9LinkCallOutcomeV1(Enum):
    LINK_CREATED = "LINK_CREATED"
    FINAL_EXISTS = "FINAL_EXISTS"
    CALL_UNCERTAIN = "CALL_UNCERTAIN"


_TASK9_OS_LINK_CALL_V1 = _os.link


def _call_task9_link_noreplace_v1(
    *, root_fd: int, temp_relative_path: str, final_relative_path: str
) -> _Task9LinkCallOutcomeV1:
    """Normalize the sole ruled no-replace publication primitive."""
    try:
        _TASK9_OS_LINK_CALL_V1(
            temp_relative_path,
            final_relative_path,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return _Task9LinkCallOutcomeV1.FINAL_EXISTS
        return _Task9LinkCallOutcomeV1.CALL_UNCERTAIN
    except Exception:
        return _Task9LinkCallOutcomeV1.CALL_UNCERTAIN
    return _Task9LinkCallOutcomeV1.LINK_CREATED


def _task9_platform_gate_v1() -> None:
    """Fail before evidence authority issuance on unsupported stdlib surfaces."""
    if (
        _sys.platform not in ("linux", "darwin")
        or _os.link not in _os.supports_dir_fd
        or _os.link not in _os.supports_follow_symlinks
        or _os.unlink not in _os.supports_dir_fd
        or _os.open not in _os.supports_dir_fd
    ):
        raise Task9TransitionEvidenceError(
            "task9_evidence_promotion_unsupported"
        )


TASK9_EVIDENCE_DECODER_TABLE_V2: Final[
    tuple[Task9DecoderRowV2, ...]
] = (('.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-8-report.md',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-controller-rulings.md',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-implementation-adjudications.md',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-parallel-execution-brief.md',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-preflight-map.md',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('docs/tennis_v1/README.md',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_expert/digest_registry.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_expert/mailbox.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_io/account_lock.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_io/expert_journal_store.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_io/research_runtime_config.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/bootstrap.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/config.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/expert_controller.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/schemas/research-runtime-config-v1.schema.json',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/shadow_activation.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/shadow_cli.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/shadow_mailbox.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/shadow_runtime.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/shadow_sources.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('pyproject.toml',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('task-9-documentation-evidence-v1.json',
  'TASK9_DOCUMENTATION_EVIDENCE_V1',
  1,
  1048576,
  'Task9DocumentationEvidenceV1',
  'evidence_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-final-reseal-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  16777216,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-final-reseal-review-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-final-reseal-review-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-final-reseal-review-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-final-reseal-review-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-final-reseal-review-v1.json',
  'TASK9_FINAL_RESEAL_REVIEW_V1',
  1,
  1048576,
  'Task9FinalResealReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-final-reseal-review-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-final-reseal-transition-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-final-reseal-transition-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-final-reseal-transition-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-final-reseal-transition-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-final-reseal-transition-v1.json',
  'TASK9_FINAL_RESEAL_TRANSITION_V1',
  1,
  1048576,
  'Task9FinalResealTransitionV1',
  'manifest_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-final-reseal-transition-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-final-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-final-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-a-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  16777216,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-functional-wave-a-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-a-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-b-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  16777216,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-functional-wave-b-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-b-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-c-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  16777216,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-functional-wave-c-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-c-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-d-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  16777216,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-functional-wave-d-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-d-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-e-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  16777216,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-functional-wave-e-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-e-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-r-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  16777216,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-functional-wave-r-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-r-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-review-a-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-functional-wave-review-a-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-a-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-functional-wave-review-a-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-a-v1.json',
  'TASK9_FUNCTIONAL_WAVE_REVIEW_V1',
  1,
  1048576,
  'Task9FunctionalWaveReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-functional-wave-review-a-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-b-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-functional-wave-review-b-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-b-v1.json',
  'TASK9_FUNCTIONAL_WAVE_REVIEW_V1',
  1,
  1048576,
  'Task9FunctionalWaveReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-functional-wave-review-b-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-c-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-functional-wave-review-c-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-c-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-functional-wave-review-c-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-c-v1.json',
  'TASK9_FUNCTIONAL_WAVE_REVIEW_V1',
  1,
  1048576,
  'Task9FunctionalWaveReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-functional-wave-review-c-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-d-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-functional-wave-review-d-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-d-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-functional-wave-review-d-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-d-v1.json',
  'TASK9_FUNCTIONAL_WAVE_REVIEW_V1',
  1,
  1048576,
  'Task9FunctionalWaveReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-functional-wave-review-d-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-e-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-functional-wave-review-e-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-e-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-functional-wave-review-e-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-e-v1.json',
  'TASK9_FUNCTIONAL_WAVE_REVIEW_V1',
  1,
  1048576,
  'Task9FunctionalWaveReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-functional-wave-review-e-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-r-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-functional-wave-review-r-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-r-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-functional-wave-review-r-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-r-v1.json',
  'TASK9_FUNCTIONAL_WAVE_REVIEW_V1',
  1,
  1048576,
  'Task9FunctionalWaveReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-functional-wave-review-r-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-post-predecessor-amended-package-rereview-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-post-predecessor-amended-package-rereview-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-post-predecessor-amended-package-rereview-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-post-predecessor-amended-package-rereview-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-post-predecessor-amended-package-rereview-v1.json',
  'TASK9_POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW_V1',
  1,
  1048576,
  'Task9PostPredecessorAmendedPackageRereviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-post-predecessor-amended-package-rereview-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-predecessor-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  16777216,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-predecessor-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-predecessor-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-predecessor-transition-manifest-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-predecessor-transition-manifest-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-predecessor-transition-manifest-v1.json',
  'TASK9_PREDECESSOR_TRANSITION_MANIFEST_V1',
  1,
  1048576,
  'Task9PredecessorTransitionManifestV1',
  'manifest_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-predecessor-transition-manifest-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-predecessor-transition-review-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-predecessor-transition-review-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-predecessor-transition-review-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-predecessor-transition-review-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-predecessor-transition-review-v1.json',
  'TASK9_PREDECESSOR_TRANSITION_REVIEW_V1',
  1,
  1048576,
  'Task9PredecessorTransitionReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-predecessor-transition-review-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-release-evidence-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-release-evidence-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-release-evidence-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-release-evidence-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-release-evidence-v1.json',
  'TASK9_RELEASE_EVIDENCE_V1',
  1,
  1048576,
  'Task9ReleaseEvidenceV1',
  'record_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-release-evidence-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-release-support-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  16777216,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('tennis_v1/entitlements.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tennis_v1/ingress.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/shadow_fixture_support.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/support/shadow_cleanup_oracle_support.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_account_lock.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_durable_parent_bridge.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_entitlements.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_expert_controller.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_expert_dependency_boundary.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_expert_journal_store.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_expert_runtime_config.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_ingress.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_preflight.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_production_account_lock.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_research_runtime_config_io.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_activation.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_bootstrap.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_capacity.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_cli.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_digest_registry.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_mailbox.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_mailbox_contracts.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_precredential_entitlement.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_recorded_fixtures.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_runtime.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_sources.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_task9_transition_evidence.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tools/task9_transition_evidence.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
 'content_sha256',
 'RAW_PATH_SNAPSHOT_ONLY'))

TASK9_EVIDENCE_DECODER_TABLE_V3: Final[
    tuple[Task9DecoderRowV2, ...]
] = (('.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-8-report.md',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-controller-rulings.md',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-implementation-adjudications.md',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-parallel-execution-brief.md',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-preflight-map.md',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('docs/tennis_v1/README.md',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_expert/digest_registry.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_expert/mailbox.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_io/account_lock.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_io/expert_journal_store.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_io/research_runtime_config.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/bootstrap.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/config.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/expert_controller.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/schemas/research-runtime-config-v1.schema.json',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/shadow_activation.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/shadow_cli.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/shadow_mailbox.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/shadow_runtime.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('inci_tennis_runtime/shadow_sources.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('pyproject.toml',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('task-9-documentation-evidence-v1.json',
  'TASK9_DOCUMENTATION_EVIDENCE_V1',
  1,
  1048576,
  'Task9DocumentationEvidenceV1',
  'evidence_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-final-reseal-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  150994944,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-final-reseal-review-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-final-reseal-review-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-final-reseal-review-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-final-reseal-review-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-final-reseal-review-v1.json',
  'TASK9_FINAL_RESEAL_REVIEW_V1',
  1,
  1048576,
  'Task9FinalResealReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-final-reseal-review-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-final-reseal-transition-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-final-reseal-transition-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-final-reseal-transition-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-final-reseal-transition-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-final-reseal-transition-v1.json',
  'TASK9_FINAL_RESEAL_TRANSITION_V1',
  1,
  1048576,
  'Task9FinalResealTransitionV1',
  'manifest_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-final-reseal-transition-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-final-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-final-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-a-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  150994944,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-functional-wave-a-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-a-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-b-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  150994944,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-functional-wave-b-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-b-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-c-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  150994944,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-functional-wave-c-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-c-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-d-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  150994944,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-functional-wave-d-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-d-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-e-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  150994944,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-functional-wave-e-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-e-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-r-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  150994944,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-functional-wave-r-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-r-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-functional-wave-review-a-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-functional-wave-review-a-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-a-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-functional-wave-review-a-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-a-v1.json',
  'TASK9_FUNCTIONAL_WAVE_REVIEW_V1',
  1,
  1048576,
  'Task9FunctionalWaveReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-functional-wave-review-a-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-b-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-functional-wave-review-b-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-b-v1.json',
  'TASK9_FUNCTIONAL_WAVE_REVIEW_V1',
  1,
  1048576,
  'Task9FunctionalWaveReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-functional-wave-review-b-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-c-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-functional-wave-review-c-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-c-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-functional-wave-review-c-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-c-v1.json',
  'TASK9_FUNCTIONAL_WAVE_REVIEW_V1',
  1,
  1048576,
  'Task9FunctionalWaveReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-functional-wave-review-c-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-d-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-functional-wave-review-d-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-d-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-functional-wave-review-d-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-d-v1.json',
  'TASK9_FUNCTIONAL_WAVE_REVIEW_V1',
  1,
  1048576,
  'Task9FunctionalWaveReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-functional-wave-review-d-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-e-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-functional-wave-review-e-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-e-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-functional-wave-review-e-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-e-v1.json',
  'TASK9_FUNCTIONAL_WAVE_REVIEW_V1',
  1,
  1048576,
  'Task9FunctionalWaveReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-functional-wave-review-e-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-r-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-functional-wave-review-r-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-r-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-functional-wave-review-r-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-functional-wave-review-r-v1.json',
  'TASK9_FUNCTIONAL_WAVE_REVIEW_V1',
  1,
  1048576,
  'Task9FunctionalWaveReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-functional-wave-review-r-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-post-predecessor-amended-package-rereview-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-post-predecessor-amended-package-rereview-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-post-predecessor-amended-package-rereview-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-post-predecessor-amended-package-rereview-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-post-predecessor-amended-package-rereview-v1.json',
  'TASK9_POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW_V1',
  1,
  1048576,
  'Task9PostPredecessorAmendedPackageRereviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-post-predecessor-amended-package-rereview-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-predecessor-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  150994944,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('task-9-predecessor-resource-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-predecessor-source-seal-v1.json',
  'TASK9_SOURCE_OR_RESOURCE_SEAL_V1',
  1,
  4194304,
  'Task9SealV1',
  'seal_sha256',
  'SEAL'),
 ('task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-predecessor-transition-manifest-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-predecessor-transition-manifest-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-predecessor-transition-manifest-v1.json',
  'TASK9_PREDECESSOR_TRANSITION_MANIFEST_V1',
  1,
  1048576,
  'Task9PredecessorTransitionManifestV1',
  'manifest_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-predecessor-transition-manifest-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-predecessor-transition-review-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-predecessor-transition-review-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-predecessor-transition-review-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-predecessor-transition-review-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-predecessor-transition-review-v1.json',
  'TASK9_PREDECESSOR_TRANSITION_REVIEW_V1',
  1,
  1048576,
  'Task9PredecessorTransitionReviewV1',
  'review_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-predecessor-transition-review-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-release-evidence-chain-acceptance-receipt-v1.json',
  'TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1',
  1,
  262144,
  'Task9ChainAcceptanceReceiptV1',
  'receipt_sha256',
  'CHAIN_RECEIPT_CURRENT_OR_ANTECEDENT'),
 ('task-9-release-evidence-chain-acceptance-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  262144,
  'bytes',
  None,
  'NEVER'),
 ('task-9-release-evidence-procedural-assignment-write-receipt-v1.json',
  'TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1',
  1,
  131072,
  'Task9ProceduralAssignmentWriteReceiptV1',
  'receipt_sha256',
  'ASSIGNMENT_RECEIPT_ONLY'),
 ('task-9-release-evidence-procedural-assignment-write-receipt-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  131072,
  'bytes',
  None,
  'NEVER'),
 ('task-9-release-evidence-v1.json',
  'TASK9_RELEASE_EVIDENCE_V1',
  1,
  1048576,
  'Task9ReleaseEvidenceV1',
  'record_sha256',
  'ARTIFACT_CURRENT_OR_TYPED_SEMANTIC'),
 ('task-9-release-evidence-v1.json.tmp-v1',
  'TASK9_TRANSIENT_WRITE_BYTES_V1',
  1,
  1048576,
  'bytes',
  None,
  'NEVER'),
 ('task-9-release-support-evidence-bundle-v1.json',
  'TASK9_EVIDENCE_BUNDLE_V1',
  1,
  150994944,
  'Task9EvidenceBundleV1',
  'bundle_sha256',
  'TYPED_SEMANTIC'),
 ('tennis_v1/entitlements.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tennis_v1/ingress.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/shadow_fixture_support.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/support/shadow_cleanup_oracle_support.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_account_lock.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_durable_parent_bridge.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_entitlements.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_expert_controller.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_expert_dependency_boundary.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_expert_journal_store.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_expert_runtime_config.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_ingress.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_preflight.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_production_account_lock.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_research_runtime_config_io.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_activation.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_bootstrap.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_capacity.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_cli.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_digest_registry.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_mailbox.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_mailbox_contracts.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_precredential_entitlement.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_recorded_fixtures.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_runtime.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_shadow_sources.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tests/tennis_v1/test_task9_transition_evidence.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'),
 ('tools/task9_transition_evidence.py',
  'TASK9_RAW_GOVERNED_BYTES_V1',
  1,
  8388608,
  'Task9EvidencePathSnapshotV1',
  'content_sha256',
  'RAW_PATH_SNAPSHOT_ONLY'))


_ROWS_BYTE_COUNT_V2 = 23_492
_ROWS_SHA256_V2 = (
    "ac31842447a9e0e029cd77065121d2bc38c7a1ef18a5c2f1327b2a120b0c1903"
)
_PREIMAGE_BYTE_COUNT_V2 = 23_520
_PREIMAGE_SHA256_V2 = (
    "92b4c561070364fd6313d0fc0cfe53da2d8aab2f4c38841cf617e6ebca50fd9a"
)
_DECODER_TABLE_SHA256_V2 = (
    "2c30b4492eaf322127a9a53024b3dd6232f2b2ffc3292c943db4be2f7074be40"
)
_DECODER_TABLE_DOMAIN_V1 = b"INCI-TASK-9-EVIDENCE-DECODER-TABLE-V1\0"


def _invalid() -> Task9TransitionEvidenceError:
    return Task9TransitionEvidenceError("task9_evidence_structure_invalid")


def _canonical_json_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return encoded.encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise _invalid() from None


def _rows_json_bytes_v2(
    value: tuple[Task9DecoderRowV2, ...],
) -> bytes:
    return _canonical_json_bytes(value)


def validate_task9_evidence_decoder_table_v2(
    value: object,
) -> tuple[Task9DecoderRowV2, ...]:
    """Validate the complete literal Round-17 decoder-table projection."""
    if type(value) is not tuple or len(value) != 147:
        raise _invalid()

    paths: list[str] = []
    for row in value:
        if (
            type(row) is not tuple
            or len(row) != 7
            or type(row[0]) is not str
            or type(row[1]) is not str
            or type(row[2]) is not int
            or type(row[3]) is not int
            or type(row[4]) is not str
            or row[5] is not None
            and type(row[5]) is not str
            or type(row[6]) is not str
            or not row[0]
            or not row[0].isascii()
            or row[0].startswith("/")
            or ".." in row[0].split("/")
            or row[2] != 1
            or row[3] < 1
        ):
            raise _invalid()
        paths.append(row[0])

    if paths != sorted(paths) or len(set(paths)) != 147:
        raise _invalid()

    rows_bytes = _rows_json_bytes_v2(value)
    if (
        len(rows_bytes) != _ROWS_BYTE_COUNT_V2
        or hashlib.sha256(rows_bytes).hexdigest() != _ROWS_SHA256_V2
    ):
        raise _invalid()

    preimage = _canonical_json_bytes(
        {"schema_version": 2, "rows": value}
    )
    if (
        len(preimage) != _PREIMAGE_BYTE_COUNT_V2
        or hashlib.sha256(preimage).hexdigest() != _PREIMAGE_SHA256_V2
        or hashlib.sha256(
            _DECODER_TABLE_DOMAIN_V1 + preimage
        ).hexdigest()
        != _DECODER_TABLE_SHA256_V2
    ):
        raise _invalid()
    return value


def task9_evidence_decoder_table_rows_json_bytes_v2() -> bytes:
    """Return the canonical row-array bytes for the frozen literal table."""
    rows = validate_task9_evidence_decoder_table_v2(
        TASK9_EVIDENCE_DECODER_TABLE_V2
    )
    return _rows_json_bytes_v2(rows)


def task9_evidence_decoder_table_preimage_bytes_v2() -> bytes:
    """Return the canonical decoder-table object preimage."""
    rows = validate_task9_evidence_decoder_table_v2(
        TASK9_EVIDENCE_DECODER_TABLE_V2
    )
    return _canonical_json_bytes({"schema_version": 2, "rows": rows})


def task9_evidence_decoder_table_sha256_v2() -> str:
    """Return the governed domain-separated decoder-table digest."""
    preimage = task9_evidence_decoder_table_preimage_bytes_v2()
    digest = hashlib.sha256(_DECODER_TABLE_DOMAIN_V1 + preimage).hexdigest()
    if digest != _DECODER_TABLE_SHA256_V2:
        raise _invalid()
    return digest


_ROWS_BYTE_COUNT_V3 = 23_501
_ROWS_SHA256_V3 = (
    "6c0165d33def319485c1af9b3e5de3cd475e9d82128ff72fde9dcb73a0389c68"
)
_PREIMAGE_BYTE_COUNT_V3 = 23_529
_PREIMAGE_SHA256_V3 = (
    "65974ea88043b725230531e47679e0b357eed85d4ca110b7b4b2bfaf81990855"
)
_DECODER_TABLE_SHA256_V3 = (
    "8e229254772ad9af77c97ed256f54d5e5bd1dfe5097909f97d2e3976f1c5572e"
)
def validate_task9_evidence_decoder_table_v3(
    value: object,
) -> tuple[Task9DecoderRowV2, ...]:
    if type(value) is not tuple or len(value) != 147:
        raise _invalid()
    paths: list[str] = []
    for row in value:
        if (
            type(row) is not tuple or len(row) != 7
            or type(row[0]) is not str or type(row[1]) is not str
            or type(row[2]) is not int or type(row[3]) is not int
            or type(row[4]) is not str
            or (row[5] is not None and type(row[5]) is not str)
            or type(row[6]) is not str or not row[0] or not row[0].isascii()
            or row[0].startswith("/") or ".." in row[0].split("/")
            or row[2] != 1 or row[3] < 1
        ):
            raise _invalid()
        paths.append(row[0])
    if paths != sorted(paths) or len(set(paths)) != 147:
        raise _invalid()
    rows_bytes = _canonical_json_bytes(value)
    if (
        len(rows_bytes) != _ROWS_BYTE_COUNT_V3
        or hashlib.sha256(rows_bytes).hexdigest() != _ROWS_SHA256_V3
    ):
        raise _invalid()
    preimage = _canonical_json_bytes({"schema_version": 3, "rows": value})
    if (
        len(preimage) != _PREIMAGE_BYTE_COUNT_V3
        or hashlib.sha256(preimage).hexdigest() != _PREIMAGE_SHA256_V3
        or hashlib.sha256(_DECODER_TABLE_DOMAIN_V1 + preimage).hexdigest()
        != _DECODER_TABLE_SHA256_V3
    ):
        raise _invalid()
    return value


def task9_evidence_decoder_table_rows_json_bytes_v3() -> bytes:
    return _canonical_json_bytes(
        validate_task9_evidence_decoder_table_v3(TASK9_EVIDENCE_DECODER_TABLE_V3)
    )


def task9_evidence_decoder_table_preimage_bytes_v3() -> bytes:
    rows = validate_task9_evidence_decoder_table_v3(
        TASK9_EVIDENCE_DECODER_TABLE_V3
    )
    return _canonical_json_bytes({"schema_version": 3, "rows": rows})


def task9_evidence_decoder_table_sha256_v3() -> str:
    digest = hashlib.sha256(
        _DECODER_TABLE_DOMAIN_V1
        + task9_evidence_decoder_table_preimage_bytes_v3()
    ).hexdigest()
    if digest != _DECODER_TABLE_SHA256_V3:
        raise _invalid()
    return digest


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9NoReplacePromotionPolicyV2:
    schema_version: int
    policy_id: str
    supported_platforms: tuple[str, ...]
    platform_gate_ids: tuple[str, ...]
    temporary_create: str
    temporary_checks: tuple[str, ...]
    temporary_steps: tuple[str, ...]
    publication_primitive: str
    publication_call: tuple[str, ...]
    publication_success_checks: tuple[str, ...]
    publication_conflict_errno: str
    temporary_unlink: str
    final_checks: tuple[str, ...]
    commit: str
    forbidden: tuple[str, ...]
    policy_sha256: str

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("task9_evidence_structure_invalid")

    def __copy__(self) -> object:
        raise TypeError("task9_evidence_structure_invalid")

    def __deepcopy__(self, memo: object) -> object:
        raise TypeError("task9_evidence_structure_invalid")

    def __reduce__(self) -> object:
        raise TypeError("task9_evidence_structure_invalid")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("task9_evidence_structure_invalid")


_TASK9_NO_REPLACE_PROMOTION_POLICY_V2_PROJECTION: Final[dict[str, object]] = {
    "schema_version": 2,
    "policy_id": "TASK9_NO_REPLACE_PROMOTION_POLICY_V2",
    "supported_platforms": ("linux", "darwin"),
    "platform_gate_ids": (
        "PYTHON_OS_LINK_DIR_FD",
        "PYTHON_OS_LINK_FOLLOW_SYMLINKS",
        "PYTHON_OS_UNLINK_DIR_FD",
        "PYTHON_OS_OPEN_DIR_FD",
    ),
    "temporary_create": (
        "OPENAT_O_WRONLY_O_CREAT_O_EXCL_O_NOFOLLOW_O_CLOEXEC_0600"
    ),
    "temporary_checks": (
        "REGULAR",
        "EUID_OWNER",
        "MODE_0600",
        "NLINK_1",
        "SAME_ROOT_DEVICE",
        "STABLE_IDENTITY",
        "EXACT_SIZE",
        "EXACT_CONTENT_SHA256",
    ),
    "temporary_steps": ("WRITE_ALL", "FSTAT", "FSYNC", "FSTAT"),
    "publication_primitive": "PYTHON_OS_LINK_SAME_DIRFD_NOFOLLOW",
    "publication_call": (
        "TEMP_RELATIVE_PATH",
        "FINAL_RELATIVE_PATH",
        "SRC_DIR_FD_ROOT",
        "DST_DIR_FD_ROOT",
        "FOLLOW_SYMLINKS_FALSE",
    ),
    "publication_success_checks": (
        "SAME_DEVICE",
        "SAME_INODE",
        "NLINK_2",
        "EXACT_CONTENT",
    ),
    "publication_conflict_errno": "EEXIST",
    "temporary_unlink": "PYTHON_OS_UNLINK_TEMP_DIR_FD_ROOT",
    "final_checks": (
        "REGULAR",
        "EUID_OWNER",
        "MODE_0600",
        "NLINK_1",
        "SAME_ROOT_DEVICE",
        "EXACT_SIZE",
        "EXACT_CONTENT_SHA256",
    ),
    "commit": "FSYNC_ROOT_DIRECTORY",
    "forbidden": (
        "RENAMEAT2",
        "RENAMEATX_NP",
        "RENAMEAT",
        "OS_RENAME",
        "OS_REPLACE",
        "COPY",
        "FINAL_UNLINK",
        "CALLER_PATH",
        "CROSS_DEVICE_PROMOTION",
        "CTYPES",
        "CFFI",
        "COMPILED_EXTENSION",
        "RAW_SYSCALL",
        "CALLER_ADAPTER",
    ),
}


def _task9_policy_v2() -> Task9NoReplacePromotionPolicyV2:
    projection = _TASK9_NO_REPLACE_PROMOTION_POLICY_V2_PROJECTION
    policy_sha256 = hashlib.sha256(
        b"INCI-TASK-9-NO-REPLACE-PROMOTION-POLICY-V2\0"
        + _canonical_json_bytes(projection)
    ).hexdigest()
    return Task9NoReplacePromotionPolicyV2(
        **projection,
        policy_sha256=policy_sha256,
    )


TASK9_NO_REPLACE_PROMOTION_POLICY_V2: Final[
    Task9NoReplacePromotionPolicyV2
] = _task9_policy_v2()


class _Task9ExactValue:
    __slots__ = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if any(base is not _Task9ExactValue for base in cls.__bases__):
            raise TypeError("task9_evidence_structure_invalid")

    def __copy__(self) -> object:
        raise TypeError("task9_evidence_structure_invalid")

    def __deepcopy__(self, memo: object) -> object:
        raise TypeError("task9_evidence_structure_invalid")

    def __reduce__(self) -> object:
        raise TypeError("task9_evidence_structure_invalid")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("task9_evidence_structure_invalid")


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9TransitionPathV1(_Task9ExactValue):
    path: str
    before_state: str
    before_sha256: str | None
    after_state: str
    after_sha256: str | None


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9DocumentPinV1(_Task9ExactValue):
    path: str
    sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9Task8FinalReportV1(_Task9ExactValue):
    status: str
    sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9ChangedSliceOwnerV1(_Task9ExactValue):
    slice_id: str
    owner_id: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9PredecessorSliceV1(_Task9ExactValue):
    slice_id: str
    ordered_paths: tuple[Task9TransitionPathV1, ...]
    canonical_red_sha256: str
    semantic_red_sha256: str
    independent_review_sha256: str
    compatibility_evidence_sha256: str
    review_disposition: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9FunctionalOwnerV1(_Task9ExactValue):
    owner_scope: str
    owner_id: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9FunctionalWaveReviewReferenceV1(_Task9ExactValue):
    wave_id: str
    review_sha256: str
    disposition: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9ProceduralRoleBindingV1(_Task9ExactValue):
    schema_version: int
    role_id: str
    local_label: str
    binding_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9ProceduralWorkflowAssignmentEvidenceV1(_Task9ExactValue):
    schema_version: int
    workflow_id: str
    assignment_scope: str
    controller_operator_label: str
    creator_controller_label: str | None
    role_bindings: tuple[Task9ProceduralRoleBindingV1, ...]
    role_binding_sha256s: tuple[str, ...]
    reviewer_label: str | None
    identity_assurance: str
    controller_operator_attested: bool
    assignment_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9PredecessorTransitionManifestV1(_Task9ExactValue):
    schema_version: int
    transition_id: str
    manifest_creator_controller_id: str
    task8_final_report: Task9Task8FinalReportV1
    baseline_document_pins: tuple[Task9DocumentPinV1, ...]
    baseline_seam_pins: tuple[Task9DocumentPinV1, ...]
    changed_slice_owner_ids: tuple[Task9ChangedSliceOwnerV1, ...]
    predecessor_slices: tuple[Task9PredecessorSliceV1, ...]
    post_predecessor_seam_pins: tuple[Task9DocumentPinV1, ...]
    source_seal_sha256: str
    resource_seal_sha256: str
    procedural_assignment_evidence: Task9ProceduralWorkflowAssignmentEvidenceV1
    procedural_assignment_evidence_sha256: str
    identity_assurance: str
    controller_operator_attested: bool
    manifest_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9PredecessorTransitionReviewV1(_Task9ExactValue):
    schema_version: int
    review_id: str
    manifest_sha256: str
    reviewer_id: str
    reviewed_post_predecessor_tree_sha256: str
    verified_slice_ids: tuple[str, ...]
    verified_post_predecessor_seam_pins: tuple[Task9DocumentPinV1, ...]
    verified_source_seal_sha256: str
    verified_resource_seal_sha256: str
    disposition: str
    procedural_assignment_evidence: Task9ProceduralWorkflowAssignmentEvidenceV1
    procedural_assignment_evidence_sha256: str
    identity_assurance: str
    controller_operator_attested: bool
    review_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9PostPredecessorAmendedPackageRereviewV1(_Task9ExactValue):
    schema_version: int
    review_id: str
    predecessor_manifest_sha256: str
    predecessor_transition_review_sha256: str
    reviewed_post_predecessor_tree_sha256: str
    amended_document_pins: tuple[Task9DocumentPinV1, ...]
    reviewer_id: str
    disposition: str
    procedural_assignment_evidence: Task9ProceduralWorkflowAssignmentEvidenceV1
    procedural_assignment_evidence_sha256: str
    identity_assurance: str
    controller_operator_attested: bool
    review_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9FunctionalWaveReviewV1(_Task9ExactValue):
    schema_version: int
    wave_id: str
    reviewer_id: str
    reviewed_tree_sha256: str
    disposition: str
    procedural_assignment_evidence: Task9ProceduralWorkflowAssignmentEvidenceV1
    procedural_assignment_evidence_sha256: str
    identity_assurance: str
    controller_operator_attested: bool
    review_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9FinalResealTransitionV1(_Task9ExactValue):
    schema_version: int
    transition_id: str
    manifest_creator_controller_id: str
    predecessor_manifest_sha256: str
    task9_pre_wave_f_tree_sha256: str
    post_predecessor_amended_package_rereview_sha256: str
    allowed_paths: tuple[Task9TransitionPathV1, ...]
    functional_owner_ids: tuple[Task9FunctionalOwnerV1, ...]
    functional_wave_reviews: tuple[Task9FunctionalWaveReviewReferenceV1, ...]
    focused_test_evidence_sha256: str
    compatibility_evidence_sha256: str
    source_seal_before_sha256: str
    source_seal_after_sha256: str
    resource_seal_before_sha256: str
    resource_seal_after_sha256: str
    post_reseal_tree_sha256: str
    procedural_assignment_evidence: Task9ProceduralWorkflowAssignmentEvidenceV1
    procedural_assignment_evidence_sha256: str
    identity_assurance: str
    controller_operator_attested: bool
    manifest_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9FinalResealReviewV1(_Task9ExactValue):
    schema_version: int
    manifest_sha256: str
    reviewer_id: str
    reviewed_post_reseal_tree_sha256: str
    final_document_pins: tuple[Task9DocumentPinV1, ...]
    disposition: str
    procedural_assignment_evidence: Task9ProceduralWorkflowAssignmentEvidenceV1
    procedural_assignment_evidence_sha256: str
    identity_assurance: str
    controller_operator_attested: bool
    review_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9ReleaseEvidenceV1(_Task9ExactValue):
    schema_version: int
    release_id: str
    predecessor_manifest_sha256: str
    predecessor_transition_review_sha256: str
    post_predecessor_amended_package_rereview_sha256: str
    final_reseal_manifest_sha256: str
    final_reseal_review_sha256: str
    final_document_pins: tuple[Task9DocumentPinV1, ...]
    functional_wave_reviews: tuple[Task9FunctionalWaveReviewReferenceV1, ...]
    focused_test_evidence_sha256: str
    compatibility_evidence_sha256: str
    source_seal_sha256: str
    resource_seal_sha256: str
    documentation_evidence_sha256: str
    release_status: str
    procedural_assignment_evidence: Task9ProceduralWorkflowAssignmentEvidenceV1
    procedural_assignment_evidence_sha256: str
    identity_assurance: str
    controller_operator_attested: bool
    record_sha256: str


Task9CanonicalArtifactV1: TypeAlias = (
    Task9PredecessorTransitionManifestV1
    | Task9PredecessorTransitionReviewV1
    | Task9PostPredecessorAmendedPackageRereviewV1
    | Task9FunctionalWaveReviewV1
    | Task9FinalResealTransitionV1
    | Task9FinalResealReviewV1
    | Task9ReleaseEvidenceV1
)


_TASK9_SLICE_IDS: Final[tuple[str, ...]] = (
    "PA",
    "PE",
    "PD_INGRESS",
    "PD_CONTROLLER",
    "PD_INTEGRATION",
)
_TASK9_WAVE_IDS: Final[tuple[str, ...]] = ("A", "B", "C", "D", "E", "R")
_TASK9_FUNCTIONAL_OWNER_SCOPES: Final[tuple[str, ...]] = (
    "A1",
    "A2",
    "A3",
    "A4",
    "A5",
    "B",
    "C",
    "D",
    "E",
    "R",
)
_TASK9_FINAL_DOCUMENT_PATHS: Final[tuple[str, ...]] = (
    ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-controller-rulings.md",
    ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-implementation-adjudications.md",
    ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-parallel-execution-brief.md",
    ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-preflight-map.md",
)
_TASK9_WAVE_F_PATHS: Final[tuple[str, ...]] = (
    "inci_tennis_io/expert_journal_store.py",
    "tests/tennis_v1/test_expert_dependency_boundary.py",
    "tests/tennis_v1/test_expert_journal_store.py",
)


_TASK9_ROLE_IDS_BY_SCOPE: Final[dict[str, tuple[str, ...]]] = {
    "PREDECESSOR_TRANSITION_MANIFEST": (
        "CONTROLLER_OPERATOR",
        "PREDECESSOR_TRANSITION_CREATOR",
        "CHANGED_OWNER_PA",
        "CHANGED_OWNER_PE",
        "CHANGED_OWNER_PD_INGRESS",
        "CHANGED_OWNER_PD_CONTROLLER",
        "CHANGED_OWNER_PD_INTEGRATION",
    ),
    "PREDECESSOR_TRANSITION_REVIEW": (
        "CONTROLLER_OPERATOR",
        "PREDECESSOR_TRANSITION_CREATOR",
        "CHANGED_OWNER_PA",
        "CHANGED_OWNER_PE",
        "CHANGED_OWNER_PD_INGRESS",
        "CHANGED_OWNER_PD_CONTROLLER",
        "CHANGED_OWNER_PD_INTEGRATION",
        "PREDECESSOR_TRANSITION_REVIEWER",
    ),
    "POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW": (
        "CONTROLLER_OPERATOR",
        "PREDECESSOR_TRANSITION_CREATOR",
        "CHANGED_OWNER_PA",
        "CHANGED_OWNER_PE",
        "CHANGED_OWNER_PD_INGRESS",
        "CHANGED_OWNER_PD_CONTROLLER",
        "CHANGED_OWNER_PD_INTEGRATION",
        "PREDECESSOR_TRANSITION_REVIEWER",
        "AMENDED_PACKAGE_REREVIEWER",
    ),
    "FUNCTIONAL_WAVE_REVIEW_A": (
        "CONTROLLER_OPERATOR",
        "FUNCTIONAL_OWNER_A1",
        "FUNCTIONAL_OWNER_A2",
        "FUNCTIONAL_OWNER_A3",
        "FUNCTIONAL_OWNER_A4",
        "FUNCTIONAL_OWNER_A5",
        "FUNCTIONAL_REVIEWER_A",
    ),
    "FUNCTIONAL_WAVE_REVIEW_B": (
        "CONTROLLER_OPERATOR",
        "FUNCTIONAL_OWNER_B",
        "FUNCTIONAL_REVIEWER_B",
    ),
    "FUNCTIONAL_WAVE_REVIEW_C": (
        "CONTROLLER_OPERATOR",
        "FUNCTIONAL_OWNER_C",
        "FUNCTIONAL_REVIEWER_C",
    ),
    "FUNCTIONAL_WAVE_REVIEW_D": (
        "CONTROLLER_OPERATOR",
        "FUNCTIONAL_OWNER_D",
        "FUNCTIONAL_REVIEWER_D",
    ),
    "FUNCTIONAL_WAVE_REVIEW_E": (
        "CONTROLLER_OPERATOR",
        "FUNCTIONAL_OWNER_E",
        "FUNCTIONAL_REVIEWER_E",
    ),
    "FUNCTIONAL_WAVE_REVIEW_R": (
        "CONTROLLER_OPERATOR",
        "FUNCTIONAL_OWNER_R",
        "FUNCTIONAL_REVIEWER_R",
    ),
    "FINAL_RESEAL_TRANSITION": (
        "CONTROLLER_OPERATOR",
        "FINAL_RESEAL_CREATOR",
        "FUNCTIONAL_OWNER_A1",
        "FUNCTIONAL_OWNER_A2",
        "FUNCTIONAL_OWNER_A3",
        "FUNCTIONAL_OWNER_A4",
        "FUNCTIONAL_OWNER_A5",
        "FUNCTIONAL_OWNER_B",
        "FUNCTIONAL_OWNER_C",
        "FUNCTIONAL_OWNER_D",
        "FUNCTIONAL_OWNER_E",
        "FUNCTIONAL_OWNER_R",
        "FUNCTIONAL_REVIEWER_A",
        "FUNCTIONAL_REVIEWER_B",
        "FUNCTIONAL_REVIEWER_C",
        "FUNCTIONAL_REVIEWER_D",
        "FUNCTIONAL_REVIEWER_E",
        "FUNCTIONAL_REVIEWER_R",
    ),
    "FINAL_RESEAL_REVIEW": (
        "CONTROLLER_OPERATOR",
        "FINAL_RESEAL_CREATOR",
        "FINAL_RESEAL_REVIEWER",
        "FUNCTIONAL_OWNER_A1",
        "FUNCTIONAL_OWNER_A2",
        "FUNCTIONAL_OWNER_A3",
        "FUNCTIONAL_OWNER_A4",
        "FUNCTIONAL_OWNER_A5",
        "FUNCTIONAL_OWNER_B",
        "FUNCTIONAL_OWNER_C",
        "FUNCTIONAL_OWNER_D",
        "FUNCTIONAL_OWNER_E",
        "FUNCTIONAL_OWNER_R",
    ),
    "RELEASE_EVIDENCE": (
        "CONTROLLER_OPERATOR",
        "FINAL_RESEAL_CREATOR",
        "FINAL_RESEAL_REVIEWER",
        "RELEASE_RECORDER",
        "FUNCTIONAL_OWNER_A1",
        "FUNCTIONAL_OWNER_A2",
        "FUNCTIONAL_OWNER_A3",
        "FUNCTIONAL_OWNER_A4",
        "FUNCTIONAL_OWNER_A5",
        "FUNCTIONAL_OWNER_B",
        "FUNCTIONAL_OWNER_C",
        "FUNCTIONAL_OWNER_D",
        "FUNCTIONAL_OWNER_E",
        "FUNCTIONAL_OWNER_R",
        "FUNCTIONAL_REVIEWER_A",
        "FUNCTIONAL_REVIEWER_B",
        "FUNCTIONAL_REVIEWER_C",
        "FUNCTIONAL_REVIEWER_D",
        "FUNCTIONAL_REVIEWER_E",
        "FUNCTIONAL_REVIEWER_R",
    ),
}


def _task9_public_projection(value: object, *, exclude: tuple[str, ...] = ()) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _task9_public_projection(getattr(value, field.name))
            for field in fields(value)
            if field.name not in exclude
        }
    if type(value) is tuple:
        return [_task9_public_projection(item) for item in value]
    if type(value) is list:
        return [_task9_public_projection(item) for item in value]
    if type(value) is dict:
        return {
            key: _task9_public_projection(item)
            for key, item in value.items()
            if key not in exclude
        }
    if isinstance(value, Enum):
        return value.value
    if value is None or type(value) in (str, int, bool):
        return value
    raise _invalid()


def _task9_domain_sha256_v1(domain: str, projection: object) -> str:
    if type(domain) is not str or not domain.isascii() or not domain:
        raise _invalid()
    return hashlib.sha256(
        domain.encode("ascii") + b"\0" + _canonical_json_bytes(projection)
    ).hexdigest()


def _task9_is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _task9_is_safe_id(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 64
        and value.isascii()
        and value[0].isalnum()
        and all(character.isalnum() or character in "._-" for character in value)
    )


def _task9_is_safe_relative_path(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value.encode("ascii", "ignore")) <= 4096
        and value.isascii()
        and not value.startswith("/")
        and "" not in value.split("/")
        and "." not in value.split("/")
        and ".." not in value.split("/")
    )


def _task9_validate_self(value: object, self_field: str, domain: str) -> None:
    if not _task9_is_sha256(getattr(value, self_field, None)):
        raise _invalid()
    projection = _task9_public_projection(value, exclude=(self_field,))
    if getattr(value, self_field) != _task9_domain_sha256_v1(domain, projection):
        raise _invalid()


def _task9_validate_pin_tuple(
    value: object, *, exact_paths: tuple[str, ...] | None = None, cardinality: int | None = None
) -> tuple[Task9DocumentPinV1, ...]:
    if type(value) is not tuple or (cardinality is not None and len(value) != cardinality):
        raise _invalid()
    paths: list[str] = []
    for pin in value:
        if type(pin) is not Task9DocumentPinV1:
            raise _invalid()
        validate_task9_document_pin_structure_v1(pin)
        paths.append(pin.path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise _invalid()
    if exact_paths is not None and tuple(paths) != exact_paths:
        raise _invalid()
    return value


def validate_task9_transition_path_structure_v1(
    value: object,
) -> Task9TransitionPathV1:
    if type(value) is not Task9TransitionPathV1 or not _task9_is_safe_relative_path(value.path):
        raise _invalid()
    for state, digest in (
        (value.before_state, value.before_sha256),
        (value.after_state, value.after_sha256),
    ):
        if state == "ABSENT":
            if digest is not None:
                raise _invalid()
        elif state == "PRESENT":
            if not _task9_is_sha256(digest):
                raise _invalid()
        else:
            raise _invalid()
    return value


def validate_task9_document_pin_structure_v1(value: object) -> Task9DocumentPinV1:
    if (
        type(value) is not Task9DocumentPinV1
        or not _task9_is_safe_relative_path(value.path)
        or not _task9_is_sha256(value.sha256)
    ):
        raise _invalid()
    return value


def validate_task9_procedural_role_binding_structure_v1(
    value: object,
) -> Task9ProceduralRoleBindingV1:
    if (
        type(value) is not Task9ProceduralRoleBindingV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or not _task9_is_safe_id(value.role_id)
        or not _task9_is_safe_id(value.local_label)
    ):
        raise _invalid()
    _task9_validate_self(
        value,
        "binding_sha256",
        "INCI-TASK-9-PROCEDURAL-ROLE-BINDING-V1",
    )
    return value


def validate_task9_procedural_workflow_assignment_evidence_structure_v1(
    value: object,
) -> Task9ProceduralWorkflowAssignmentEvidenceV1:
    if (
        type(value) is not Task9ProceduralWorkflowAssignmentEvidenceV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.workflow_id != "TASK9"
        or value.assignment_scope not in _TASK9_ROLE_IDS_BY_SCOPE
        or not _task9_is_safe_id(value.controller_operator_label)
        or value.creator_controller_label is not None
        and not _task9_is_safe_id(value.creator_controller_label)
        or value.reviewer_label is not None
        and not _task9_is_safe_id(value.reviewer_label)
        or type(value.role_bindings) is not tuple
        or type(value.role_binding_sha256s) is not tuple
        or value.identity_assurance != "PROCEDURAL_LOCAL_ATTESTATION"
        or type(value.controller_operator_attested) is not bool
        or value.controller_operator_attested is not True
    ):
        raise _invalid()
    expected_role_ids = _TASK9_ROLE_IDS_BY_SCOPE[value.assignment_scope]
    if (
        len(value.role_bindings) != len(expected_role_ids)
        or len(value.role_binding_sha256s) != len(expected_role_ids)
        or tuple(binding.role_id for binding in value.role_bindings)
        != expected_role_ids
        or len({id(binding) for binding in value.role_bindings})
        != len(value.role_bindings)
    ):
        raise _invalid()
    for index, binding in enumerate(value.role_bindings):
        if type(binding) is not Task9ProceduralRoleBindingV1:
            raise _invalid()
        validate_task9_procedural_role_binding_structure_v1(binding)
        if value.role_binding_sha256s[index] != binding.binding_sha256:
            raise _invalid()
    if len(set(value.role_binding_sha256s)) != len(value.role_binding_sha256s):
        raise _invalid()
    labels = {binding.role_id: binding.local_label for binding in value.role_bindings}
    if labels["CONTROLLER_OPERATOR"] != value.controller_operator_label:
        raise _invalid()

    creator_role = None
    if "PREDECESSOR_TRANSITION_CREATOR" in labels:
        creator_role = "PREDECESSOR_TRANSITION_CREATOR"
    elif "FINAL_RESEAL_CREATOR" in labels:
        creator_role = "FINAL_RESEAL_CREATOR"
    if creator_role is None:
        if value.creator_controller_label is not None:
            raise _invalid()
    elif value.creator_controller_label != labels[creator_role]:
        raise _invalid()

    if value.assignment_scope in (
        "PREDECESSOR_TRANSITION_MANIFEST",
        "FINAL_RESEAL_TRANSITION",
    ):
        if value.reviewer_label is not None:
            raise _invalid()
    else:
        reviewer_role = {
            "PREDECESSOR_TRANSITION_REVIEW": "PREDECESSOR_TRANSITION_REVIEWER",
            "POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW": "AMENDED_PACKAGE_REREVIEWER",
            "FUNCTIONAL_WAVE_REVIEW_A": "FUNCTIONAL_REVIEWER_A",
            "FUNCTIONAL_WAVE_REVIEW_B": "FUNCTIONAL_REVIEWER_B",
            "FUNCTIONAL_WAVE_REVIEW_C": "FUNCTIONAL_REVIEWER_C",
            "FUNCTIONAL_WAVE_REVIEW_D": "FUNCTIONAL_REVIEWER_D",
            "FUNCTIONAL_WAVE_REVIEW_E": "FUNCTIONAL_REVIEWER_E",
            "FUNCTIONAL_WAVE_REVIEW_R": "FUNCTIONAL_REVIEWER_R",
            "FINAL_RESEAL_REVIEW": "FINAL_RESEAL_REVIEWER",
            "RELEASE_EVIDENCE": "FINAL_RESEAL_REVIEWER",
        }[value.assignment_scope]
        if value.reviewer_label != labels[reviewer_role]:
            raise _invalid()
    if (
        value.assignment_scope == "RELEASE_EVIDENCE"
        and labels["RELEASE_RECORDER"] != value.controller_operator_label
    ):
        raise _invalid()
    _task9_validate_self(
        value,
        "assignment_sha256",
        "INCI-TASK-9-PROCEDURAL-WORKFLOW-ASSIGNMENT-EVIDENCE-V1",
    )
    return value


def _task9_validate_artifact_assignment(
    value: Task9CanonicalArtifactV1,
    *,
    expected_scope: str,
    creator_label: str | None,
    reviewer_label: str | None,
) -> None:
    assignment = value.procedural_assignment_evidence
    if type(assignment) is not Task9ProceduralWorkflowAssignmentEvidenceV1:
        raise _invalid()
    validate_task9_procedural_workflow_assignment_evidence_structure_v1(assignment)
    if (
        assignment.assignment_scope != expected_scope
        or value.procedural_assignment_evidence_sha256
        != assignment.assignment_sha256
        or value.identity_assurance != "PROCEDURAL_LOCAL_ATTESTATION"
        or type(value.controller_operator_attested) is not bool
        or value.controller_operator_attested is not True
        or assignment.identity_assurance != value.identity_assurance
        or assignment.controller_operator_attested
        is not value.controller_operator_attested
        or assignment.creator_controller_label != creator_label
        or assignment.reviewer_label != reviewer_label
    ):
        raise _invalid()


def _task9_validate_sha_fields(value: object, names: tuple[str, ...]) -> None:
    if any(not _task9_is_sha256(getattr(value, name, None)) for name in names):
        raise _invalid()


def _task9_validate_functional_review_refs(value: object) -> None:
    if type(value) is not tuple or len(value) != 6:
        raise _invalid()
    if tuple(item.wave_id for item in value) != _TASK9_WAVE_IDS:
        raise _invalid()
    for item in value:
        if (
            type(item) is not Task9FunctionalWaveReviewReferenceV1
            or not _task9_is_sha256(item.review_sha256)
            or item.disposition != "CLEAN"
        ):
            raise _invalid()


def validate_task9_predecessor_transition_manifest_structure_v1(
    value: object,
) -> Task9PredecessorTransitionManifestV1:
    if (
        type(value) is not Task9PredecessorTransitionManifestV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.transition_id != "task-9-predecessor-transition-v1"
        or not _task9_is_safe_id(value.manifest_creator_controller_id)
        or type(value.task8_final_report) is not Task9Task8FinalReportV1
        or value.task8_final_report.status != "TASK_8_IMPLEMENTATION_FINAL"
        or not _task9_is_sha256(value.task8_final_report.sha256)
    ):
        raise _invalid()
    _task9_validate_pin_tuple(value.baseline_document_pins)
    baseline_seams = _task9_validate_pin_tuple(value.baseline_seam_pins, cardinality=7)
    post_seams = _task9_validate_pin_tuple(
        value.post_predecessor_seam_pins, cardinality=7
    )
    if tuple(pin.path for pin in baseline_seams) != tuple(pin.path for pin in post_seams):
        raise _invalid()
    if (
        type(value.changed_slice_owner_ids) is not tuple
        or tuple(owner.slice_id for owner in value.changed_slice_owner_ids)
        != _TASK9_SLICE_IDS
        or type(value.predecessor_slices) is not tuple
        or tuple(item.slice_id for item in value.predecessor_slices)
        != _TASK9_SLICE_IDS
    ):
        raise _invalid()
    for owner in value.changed_slice_owner_ids:
        if type(owner) is not Task9ChangedSliceOwnerV1 or not _task9_is_safe_id(owner.owner_id):
            raise _invalid()
    for item in value.predecessor_slices:
        if type(item) is not Task9PredecessorSliceV1 or type(item.ordered_paths) is not tuple:
            raise _invalid()
        paths = []
        for path_value in item.ordered_paths:
            validate_task9_transition_path_structure_v1(path_value)
            paths.append(path_value.path)
        if len(paths) != len(set(paths)):
            raise _invalid()
        _task9_validate_sha_fields(
            item,
            (
                "canonical_red_sha256",
                "semantic_red_sha256",
                "independent_review_sha256",
                "compatibility_evidence_sha256",
            ),
        )
        if item.review_disposition != "CLEAN":
            raise _invalid()
    _task9_validate_sha_fields(value, ("source_seal_sha256", "resource_seal_sha256"))
    _task9_validate_artifact_assignment(
        value,
        expected_scope="PREDECESSOR_TRANSITION_MANIFEST",
        creator_label=value.manifest_creator_controller_id,
        reviewer_label=None,
    )
    _task9_validate_self(
        value,
        "manifest_sha256",
        "INCI-TASK-9-PREDECESSOR-TRANSITION-MANIFEST-V1",
    )
    return value


def validate_task9_predecessor_transition_review_structure_v1(
    value: object,
) -> Task9PredecessorTransitionReviewV1:
    if (
        type(value) is not Task9PredecessorTransitionReviewV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.review_id != "task-9-predecessor-transition-review-v1"
        or not _task9_is_safe_id(value.reviewer_id)
        or tuple(value.verified_slice_ids) != _TASK9_SLICE_IDS
        or value.disposition not in ("CLEAN", "NOT_CLEAN")
    ):
        raise _invalid()
    _task9_validate_pin_tuple(value.verified_post_predecessor_seam_pins, cardinality=7)
    _task9_validate_sha_fields(
        value,
        (
            "manifest_sha256",
            "reviewed_post_predecessor_tree_sha256",
            "verified_source_seal_sha256",
            "verified_resource_seal_sha256",
        ),
    )
    _task9_validate_artifact_assignment(
        value,
        expected_scope="PREDECESSOR_TRANSITION_REVIEW",
        creator_label=value.procedural_assignment_evidence.creator_controller_label,
        reviewer_label=value.reviewer_id,
    )
    _task9_validate_self(
        value,
        "review_sha256",
        "INCI-TASK-9-PREDECESSOR-TRANSITION-REVIEW-V1",
    )
    return value


def validate_task9_post_predecessor_amended_package_rereview_structure_v1(
    value: object,
) -> Task9PostPredecessorAmendedPackageRereviewV1:
    if (
        type(value) is not Task9PostPredecessorAmendedPackageRereviewV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.review_id
        != "task-9-post-predecessor-amended-package-rereview-v1"
        or not _task9_is_safe_id(value.reviewer_id)
        or value.disposition not in ("CLEAN", "NOT_CLEAN")
    ):
        raise _invalid()
    _task9_validate_pin_tuple(
        value.amended_document_pins,
        exact_paths=_TASK9_FINAL_DOCUMENT_PATHS,
        cardinality=4,
    )
    _task9_validate_sha_fields(
        value,
        (
            "predecessor_manifest_sha256",
            "predecessor_transition_review_sha256",
            "reviewed_post_predecessor_tree_sha256",
        ),
    )
    _task9_validate_artifact_assignment(
        value,
        expected_scope="POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW",
        creator_label=value.procedural_assignment_evidence.creator_controller_label,
        reviewer_label=value.reviewer_id,
    )
    _task9_validate_self(
        value,
        "review_sha256",
        "INCI-TASK-9-POST-PREDECESSOR-AMENDED-PACKAGE-REREVIEW-V1",
    )
    return value


def validate_task9_functional_wave_review_structure_v1(
    value: object,
) -> Task9FunctionalWaveReviewV1:
    if (
        type(value) is not Task9FunctionalWaveReviewV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.wave_id not in _TASK9_WAVE_IDS
        or not _task9_is_safe_id(value.reviewer_id)
        or not _task9_is_sha256(value.reviewed_tree_sha256)
        or value.disposition not in ("CLEAN", "NOT_CLEAN")
    ):
        raise _invalid()
    expected_scope = {
        "A": "FUNCTIONAL_WAVE_REVIEW_A",
        "B": "FUNCTIONAL_WAVE_REVIEW_B",
        "C": "FUNCTIONAL_WAVE_REVIEW_C",
        "D": "FUNCTIONAL_WAVE_REVIEW_D",
        "E": "FUNCTIONAL_WAVE_REVIEW_E",
        "R": "FUNCTIONAL_WAVE_REVIEW_R",
    }[value.wave_id]
    _task9_validate_artifact_assignment(
        value,
        expected_scope=expected_scope,
        creator_label=None,
        reviewer_label=value.reviewer_id,
    )
    _task9_validate_self(
        value,
        "review_sha256",
        "INCI-TASK-9-FUNCTIONAL-WAVE-REVIEW-V1",
    )
    return value


def validate_task9_final_reseal_transition_structure_v1(
    value: object,
) -> Task9FinalResealTransitionV1:
    if (
        type(value) is not Task9FinalResealTransitionV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.transition_id != "task-9-final-reseal-transition-v1"
        or not _task9_is_safe_id(value.manifest_creator_controller_id)
        or type(value.allowed_paths) is not tuple
        or tuple(path.path for path in value.allowed_paths) != _TASK9_WAVE_F_PATHS
        or type(value.functional_owner_ids) is not tuple
        or tuple(owner.owner_scope for owner in value.functional_owner_ids)
        != _TASK9_FUNCTIONAL_OWNER_SCOPES
    ):
        raise _invalid()
    for path_value in value.allowed_paths:
        validate_task9_transition_path_structure_v1(path_value)
    for owner in value.functional_owner_ids:
        if type(owner) is not Task9FunctionalOwnerV1 or not _task9_is_safe_id(owner.owner_id):
            raise _invalid()
    _task9_validate_functional_review_refs(value.functional_wave_reviews)
    _task9_validate_sha_fields(
        value,
        (
            "predecessor_manifest_sha256",
            "task9_pre_wave_f_tree_sha256",
            "post_predecessor_amended_package_rereview_sha256",
            "focused_test_evidence_sha256",
            "compatibility_evidence_sha256",
            "source_seal_before_sha256",
            "source_seal_after_sha256",
            "resource_seal_before_sha256",
            "resource_seal_after_sha256",
            "post_reseal_tree_sha256",
        ),
    )
    _task9_validate_artifact_assignment(
        value,
        expected_scope="FINAL_RESEAL_TRANSITION",
        creator_label=value.manifest_creator_controller_id,
        reviewer_label=None,
    )
    _task9_validate_self(
        value,
        "manifest_sha256",
        "INCI-TASK-9-FINAL-RESEAL-TRANSITION-V1",
    )
    return value


def validate_task9_final_reseal_review_structure_v1(
    value: object,
) -> Task9FinalResealReviewV1:
    if (
        type(value) is not Task9FinalResealReviewV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or not _task9_is_safe_id(value.reviewer_id)
        or value.disposition not in ("CLEAN", "NOT_CLEAN")
    ):
        raise _invalid()
    _task9_validate_pin_tuple(
        value.final_document_pins,
        exact_paths=_TASK9_FINAL_DOCUMENT_PATHS,
        cardinality=4,
    )
    _task9_validate_sha_fields(
        value, ("manifest_sha256", "reviewed_post_reseal_tree_sha256")
    )
    _task9_validate_artifact_assignment(
        value,
        expected_scope="FINAL_RESEAL_REVIEW",
        creator_label=value.procedural_assignment_evidence.creator_controller_label,
        reviewer_label=value.reviewer_id,
    )
    _task9_validate_self(
        value,
        "review_sha256",
        "INCI-TASK-9-FINAL-RESEAL-REVIEW-V1",
    )
    return value


def validate_task9_release_evidence_structure_v1(
    value: object,
) -> Task9ReleaseEvidenceV1:
    if (
        type(value) is not Task9ReleaseEvidenceV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.release_id != "task-9-release-evidence-v1"
        or value.release_status != "RELEASED"
    ):
        raise _invalid()
    _task9_validate_pin_tuple(
        value.final_document_pins,
        exact_paths=_TASK9_FINAL_DOCUMENT_PATHS,
        cardinality=4,
    )
    _task9_validate_functional_review_refs(value.functional_wave_reviews)
    _task9_validate_sha_fields(
        value,
        (
            "predecessor_manifest_sha256",
            "predecessor_transition_review_sha256",
            "post_predecessor_amended_package_rereview_sha256",
            "final_reseal_manifest_sha256",
            "final_reseal_review_sha256",
            "focused_test_evidence_sha256",
            "compatibility_evidence_sha256",
            "source_seal_sha256",
            "resource_seal_sha256",
            "documentation_evidence_sha256",
        ),
    )
    _task9_validate_artifact_assignment(
        value,
        expected_scope="RELEASE_EVIDENCE",
        creator_label=value.procedural_assignment_evidence.creator_controller_label,
        reviewer_label=value.procedural_assignment_evidence.reviewer_label,
    )
    _task9_validate_self(
        value,
        "record_sha256",
        "INCI-TASK-9-RELEASE-EVIDENCE-V1",
    )
    return value


validate_task9_predecessor_transition_manifest_v1 = (
    validate_task9_predecessor_transition_manifest_structure_v1
)
validate_task9_predecessor_transition_review_v1 = (
    validate_task9_predecessor_transition_review_structure_v1
)
validate_task9_post_predecessor_amended_package_rereview_v1 = (
    validate_task9_post_predecessor_amended_package_rereview_structure_v1
)
validate_task9_functional_wave_review_v1 = (
    validate_task9_functional_wave_review_structure_v1
)
validate_task9_final_reseal_transition_v1 = (
    validate_task9_final_reseal_transition_structure_v1
)
validate_task9_final_reseal_review_v1 = (
    validate_task9_final_reseal_review_structure_v1
)
validate_task9_release_evidence_v1 = validate_task9_release_evidence_structure_v1


class _Task9DuplicateJsonKey(ValueError):
    pass


def _task9_unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in pairs:
        if key in result:
            raise _Task9DuplicateJsonKey
        result[key] = item
    return result


def _task9_decode_canonical_json_object(payload: bytes, *, cap: int) -> dict[str, object]:
    if type(payload) is not bytes or len(payload) > cap:
        raise _invalid()
    try:
        decoded = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_task9_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        raise _invalid() from None
    if type(decoded) is not dict or _canonical_json_bytes(decoded) != payload:
        raise _invalid()
    return decoded


def _task9_exact_keys(value: object, expected: tuple[str, ...]) -> dict[str, object]:
    if type(value) is not dict or set(value) != set(expected):
        raise _invalid()
    return value


def _task9_tuple_from_list(value: object, parser: object) -> tuple[object, ...]:
    if type(value) is not list or not callable(parser):
        raise _invalid()
    return tuple(parser(item) for item in value)


def _task9_parse_role_binding(value: object) -> Task9ProceduralRoleBindingV1:
    obj = _task9_exact_keys(
        value, ("schema_version", "role_id", "local_label", "binding_sha256")
    )
    result = Task9ProceduralRoleBindingV1(**obj)
    return validate_task9_procedural_role_binding_structure_v1(result)


def _task9_parse_assignment(
    value: object,
) -> Task9ProceduralWorkflowAssignmentEvidenceV1:
    obj = _task9_exact_keys(
        value,
        (
            "schema_version",
            "workflow_id",
            "assignment_scope",
            "controller_operator_label",
            "creator_controller_label",
            "role_bindings",
            "role_binding_sha256s",
            "reviewer_label",
            "identity_assurance",
            "controller_operator_attested",
            "assignment_sha256",
        ),
    )
    result = Task9ProceduralWorkflowAssignmentEvidenceV1(
        schema_version=obj["schema_version"],
        workflow_id=obj["workflow_id"],
        assignment_scope=obj["assignment_scope"],
        controller_operator_label=obj["controller_operator_label"],
        creator_controller_label=obj["creator_controller_label"],
        role_bindings=_task9_tuple_from_list(
            obj["role_bindings"], _task9_parse_role_binding
        ),
        role_binding_sha256s=_task9_tuple_from_list(
            obj["role_binding_sha256s"], lambda item: item
        ),
        reviewer_label=obj["reviewer_label"],
        identity_assurance=obj["identity_assurance"],
        controller_operator_attested=obj["controller_operator_attested"],
        assignment_sha256=obj["assignment_sha256"],
    )
    return validate_task9_procedural_workflow_assignment_evidence_structure_v1(
        result
    )


def _task9_parse_pin(value: object) -> Task9DocumentPinV1:
    obj = _task9_exact_keys(value, ("path", "sha256"))
    return validate_task9_document_pin_structure_v1(Task9DocumentPinV1(**obj))


def _task9_parse_transition_path(value: object) -> Task9TransitionPathV1:
    obj = _task9_exact_keys(
        value,
        ("path", "before_state", "before_sha256", "after_state", "after_sha256"),
    )
    return validate_task9_transition_path_structure_v1(Task9TransitionPathV1(**obj))


def _task9_parse_task8_final_report(value: object) -> Task9Task8FinalReportV1:
    obj = _task9_exact_keys(value, ("status", "sha256"))
    return Task9Task8FinalReportV1(**obj)


def _task9_parse_changed_owner(value: object) -> Task9ChangedSliceOwnerV1:
    obj = _task9_exact_keys(value, ("slice_id", "owner_id"))
    return Task9ChangedSliceOwnerV1(**obj)


def _task9_parse_predecessor_slice(value: object) -> Task9PredecessorSliceV1:
    obj = _task9_exact_keys(
        value,
        (
            "slice_id",
            "ordered_paths",
            "canonical_red_sha256",
            "semantic_red_sha256",
            "independent_review_sha256",
            "compatibility_evidence_sha256",
            "review_disposition",
        ),
    )
    return Task9PredecessorSliceV1(
        slice_id=obj["slice_id"],
        ordered_paths=_task9_tuple_from_list(
            obj["ordered_paths"], _task9_parse_transition_path
        ),
        canonical_red_sha256=obj["canonical_red_sha256"],
        semantic_red_sha256=obj["semantic_red_sha256"],
        independent_review_sha256=obj["independent_review_sha256"],
        compatibility_evidence_sha256=obj["compatibility_evidence_sha256"],
        review_disposition=obj["review_disposition"],
    )


def _task9_parse_functional_owner(value: object) -> Task9FunctionalOwnerV1:
    obj = _task9_exact_keys(value, ("owner_scope", "owner_id"))
    return Task9FunctionalOwnerV1(**obj)


def _task9_parse_functional_review_ref(
    value: object,
) -> Task9FunctionalWaveReviewReferenceV1:
    obj = _task9_exact_keys(value, ("wave_id", "review_sha256", "disposition"))
    return Task9FunctionalWaveReviewReferenceV1(**obj)


def _task9_artifact_common(obj: dict[str, object]) -> dict[str, object]:
    return {
        "procedural_assignment_evidence": _task9_parse_assignment(
            obj["procedural_assignment_evidence"]
        ),
        "procedural_assignment_evidence_sha256": obj[
            "procedural_assignment_evidence_sha256"
        ],
        "identity_assurance": obj["identity_assurance"],
        "controller_operator_attested": obj["controller_operator_attested"],
    }


def parse_task9_predecessor_transition_manifest_v1(
    payload: bytes,
) -> Task9PredecessorTransitionManifestV1:
    obj = _task9_decode_canonical_json_object(payload, cap=1_048_576)
    _task9_exact_keys(
        obj,
        tuple(field.name for field in fields(Task9PredecessorTransitionManifestV1)),
    )
    result = Task9PredecessorTransitionManifestV1(
        schema_version=obj["schema_version"],
        transition_id=obj["transition_id"],
        manifest_creator_controller_id=obj["manifest_creator_controller_id"],
        task8_final_report=_task9_parse_task8_final_report(obj["task8_final_report"]),
        baseline_document_pins=_task9_tuple_from_list(
            obj["baseline_document_pins"], _task9_parse_pin
        ),
        baseline_seam_pins=_task9_tuple_from_list(
            obj["baseline_seam_pins"], _task9_parse_pin
        ),
        changed_slice_owner_ids=_task9_tuple_from_list(
            obj["changed_slice_owner_ids"], _task9_parse_changed_owner
        ),
        predecessor_slices=_task9_tuple_from_list(
            obj["predecessor_slices"], _task9_parse_predecessor_slice
        ),
        post_predecessor_seam_pins=_task9_tuple_from_list(
            obj["post_predecessor_seam_pins"], _task9_parse_pin
        ),
        source_seal_sha256=obj["source_seal_sha256"],
        resource_seal_sha256=obj["resource_seal_sha256"],
        **_task9_artifact_common(obj),
        manifest_sha256=obj["manifest_sha256"],
    )
    return validate_task9_predecessor_transition_manifest_structure_v1(result)


def parse_task9_predecessor_transition_review_v1(
    payload: bytes,
) -> Task9PredecessorTransitionReviewV1:
    obj = _task9_decode_canonical_json_object(payload, cap=1_048_576)
    _task9_exact_keys(
        obj, tuple(field.name for field in fields(Task9PredecessorTransitionReviewV1))
    )
    result = Task9PredecessorTransitionReviewV1(
        schema_version=obj["schema_version"],
        review_id=obj["review_id"],
        manifest_sha256=obj["manifest_sha256"],
        reviewer_id=obj["reviewer_id"],
        reviewed_post_predecessor_tree_sha256=obj[
            "reviewed_post_predecessor_tree_sha256"
        ],
        verified_slice_ids=_task9_tuple_from_list(
            obj["verified_slice_ids"], lambda item: item
        ),
        verified_post_predecessor_seam_pins=_task9_tuple_from_list(
            obj["verified_post_predecessor_seam_pins"], _task9_parse_pin
        ),
        verified_source_seal_sha256=obj["verified_source_seal_sha256"],
        verified_resource_seal_sha256=obj["verified_resource_seal_sha256"],
        disposition=obj["disposition"],
        **_task9_artifact_common(obj),
        review_sha256=obj["review_sha256"],
    )
    return validate_task9_predecessor_transition_review_structure_v1(result)


def parse_task9_post_predecessor_amended_package_rereview_v1(
    payload: bytes,
) -> Task9PostPredecessorAmendedPackageRereviewV1:
    obj = _task9_decode_canonical_json_object(payload, cap=1_048_576)
    _task9_exact_keys(
        obj,
        tuple(
            field.name
            for field in fields(Task9PostPredecessorAmendedPackageRereviewV1)
        ),
    )
    result = Task9PostPredecessorAmendedPackageRereviewV1(
        schema_version=obj["schema_version"],
        review_id=obj["review_id"],
        predecessor_manifest_sha256=obj["predecessor_manifest_sha256"],
        predecessor_transition_review_sha256=obj[
            "predecessor_transition_review_sha256"
        ],
        reviewed_post_predecessor_tree_sha256=obj[
            "reviewed_post_predecessor_tree_sha256"
        ],
        amended_document_pins=_task9_tuple_from_list(
            obj["amended_document_pins"], _task9_parse_pin
        ),
        reviewer_id=obj["reviewer_id"],
        disposition=obj["disposition"],
        **_task9_artifact_common(obj),
        review_sha256=obj["review_sha256"],
    )
    return validate_task9_post_predecessor_amended_package_rereview_structure_v1(
        result
    )


def parse_task9_functional_wave_review_v1(
    payload: bytes,
) -> Task9FunctionalWaveReviewV1:
    obj = _task9_decode_canonical_json_object(payload, cap=1_048_576)
    _task9_exact_keys(
        obj, tuple(field.name for field in fields(Task9FunctionalWaveReviewV1))
    )
    result = Task9FunctionalWaveReviewV1(
        schema_version=obj["schema_version"],
        wave_id=obj["wave_id"],
        reviewer_id=obj["reviewer_id"],
        reviewed_tree_sha256=obj["reviewed_tree_sha256"],
        disposition=obj["disposition"],
        **_task9_artifact_common(obj),
        review_sha256=obj["review_sha256"],
    )
    return validate_task9_functional_wave_review_structure_v1(result)


def parse_task9_final_reseal_transition_v1(
    payload: bytes,
) -> Task9FinalResealTransitionV1:
    obj = _task9_decode_canonical_json_object(payload, cap=1_048_576)
    _task9_exact_keys(
        obj, tuple(field.name for field in fields(Task9FinalResealTransitionV1))
    )
    result = Task9FinalResealTransitionV1(
        schema_version=obj["schema_version"],
        transition_id=obj["transition_id"],
        manifest_creator_controller_id=obj["manifest_creator_controller_id"],
        predecessor_manifest_sha256=obj["predecessor_manifest_sha256"],
        task9_pre_wave_f_tree_sha256=obj["task9_pre_wave_f_tree_sha256"],
        post_predecessor_amended_package_rereview_sha256=obj[
            "post_predecessor_amended_package_rereview_sha256"
        ],
        allowed_paths=_task9_tuple_from_list(
            obj["allowed_paths"], _task9_parse_transition_path
        ),
        functional_owner_ids=_task9_tuple_from_list(
            obj["functional_owner_ids"], _task9_parse_functional_owner
        ),
        functional_wave_reviews=_task9_tuple_from_list(
            obj["functional_wave_reviews"], _task9_parse_functional_review_ref
        ),
        focused_test_evidence_sha256=obj["focused_test_evidence_sha256"],
        compatibility_evidence_sha256=obj["compatibility_evidence_sha256"],
        source_seal_before_sha256=obj["source_seal_before_sha256"],
        source_seal_after_sha256=obj["source_seal_after_sha256"],
        resource_seal_before_sha256=obj["resource_seal_before_sha256"],
        resource_seal_after_sha256=obj["resource_seal_after_sha256"],
        post_reseal_tree_sha256=obj["post_reseal_tree_sha256"],
        **_task9_artifact_common(obj),
        manifest_sha256=obj["manifest_sha256"],
    )
    return validate_task9_final_reseal_transition_structure_v1(result)


def parse_task9_final_reseal_review_v1(
    payload: bytes,
) -> Task9FinalResealReviewV1:
    obj = _task9_decode_canonical_json_object(payload, cap=1_048_576)
    _task9_exact_keys(
        obj, tuple(field.name for field in fields(Task9FinalResealReviewV1))
    )
    result = Task9FinalResealReviewV1(
        schema_version=obj["schema_version"],
        manifest_sha256=obj["manifest_sha256"],
        reviewer_id=obj["reviewer_id"],
        reviewed_post_reseal_tree_sha256=obj["reviewed_post_reseal_tree_sha256"],
        final_document_pins=_task9_tuple_from_list(
            obj["final_document_pins"], _task9_parse_pin
        ),
        disposition=obj["disposition"],
        **_task9_artifact_common(obj),
        review_sha256=obj["review_sha256"],
    )
    return validate_task9_final_reseal_review_structure_v1(result)


def parse_task9_release_evidence_v1(payload: bytes) -> Task9ReleaseEvidenceV1:
    obj = _task9_decode_canonical_json_object(payload, cap=1_048_576)
    _task9_exact_keys(
        obj, tuple(field.name for field in fields(Task9ReleaseEvidenceV1))
    )
    result = Task9ReleaseEvidenceV1(
        schema_version=obj["schema_version"],
        release_id=obj["release_id"],
        predecessor_manifest_sha256=obj["predecessor_manifest_sha256"],
        predecessor_transition_review_sha256=obj[
            "predecessor_transition_review_sha256"
        ],
        post_predecessor_amended_package_rereview_sha256=obj[
            "post_predecessor_amended_package_rereview_sha256"
        ],
        final_reseal_manifest_sha256=obj["final_reseal_manifest_sha256"],
        final_reseal_review_sha256=obj["final_reseal_review_sha256"],
        final_document_pins=_task9_tuple_from_list(
            obj["final_document_pins"], _task9_parse_pin
        ),
        functional_wave_reviews=_task9_tuple_from_list(
            obj["functional_wave_reviews"], _task9_parse_functional_review_ref
        ),
        focused_test_evidence_sha256=obj["focused_test_evidence_sha256"],
        compatibility_evidence_sha256=obj["compatibility_evidence_sha256"],
        source_seal_sha256=obj["source_seal_sha256"],
        resource_seal_sha256=obj["resource_seal_sha256"],
        documentation_evidence_sha256=obj["documentation_evidence_sha256"],
        release_status=obj["release_status"],
        **_task9_artifact_common(obj),
        record_sha256=obj["record_sha256"],
    )
    return validate_task9_release_evidence_structure_v1(result)


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9EvidencePathSnapshotV1(_Task9ExactValue):
    schema_version: int
    relative_path: str
    state: str
    device: int | None
    inode: int | None
    mode: int | None
    owner: int | None
    links: int | None
    size: int | None
    mtime_ns: int | None
    ctime_ns: int | None
    content_sha256: str | None
    path_snapshot_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9EvidenceTreeSnapshotV1(_Task9ExactValue):
    schema_version: int
    tree_id: str
    path_snapshot_sha256s: tuple[str, ...]
    tree_sha256: str
    snapshot_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class EvidenceRootSnapshotV1(_Task9ExactValue):
    schema_version: int
    snapshot_allocation_coordinate: int
    evidence_root_identity_sha256: str
    closed_path_count: int
    present_path_count: int
    captured_bytes_total: int
    capture_policy_sha256: str
    decoder_table_sha256: str
    closed_temp_path_count: int
    present_temp_path_count: int
    transient_write_tree_snapshot_sha256: str
    transient_state: str
    path_snapshot_sha256s: tuple[str, ...]
    tree_snapshot_sha256s: tuple[str, ...]
    snapshot_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9ProceduralAssignmentWriteReceiptV1(_Task9ExactValue):
    schema_version: int
    receipt_id: str
    stage_id: str
    artifact_family: str
    artifact_relative_path: str
    artifact_temp_relative_path: str
    receipt_relative_path: str
    receipt_temp_relative_path: str
    assignment_scope: str
    assignment_sha256: str
    artifact_self_field: str
    artifact_self_sha256: str
    artifact_content_sha256: str
    promotion_policy_sha256: str
    writer_projection_sha256: str
    write_mode: str
    receipt_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9ChainAcceptanceReceiptV1(_Task9ExactValue):
    schema_version: int
    receipt_id: str
    stage_id: str
    artifact_family: str
    artifact_relative_path: str
    receipt_relative_path: str
    receipt_temp_relative_path: str
    artifact_self_field: str
    artifact_self_sha256: str
    artifact_content_sha256: str
    evidence_root_snapshot_sha256: str
    procedural_assignment_write_receipt_sha256: str
    semantic_evidence_sha256s: tuple[str, ...]
    raw_evidence_content_sha256s: tuple[str, ...]
    seal_sha256s: tuple[str, ...]
    antecedent_chain_receipt_sha256s: tuple[str, ...]
    stage_contract_sha256: str
    capture_policy_sha256: str
    decoder_table_sha256: str
    promotion_policy_sha256: str
    acceptance: str
    validator_projection_sha256: str
    receipt_sha256: str


def validate_task9_evidence_path_snapshot_structure_v1(
    value: object,
) -> Task9EvidencePathSnapshotV1:
    if (
        type(value) is not Task9EvidencePathSnapshotV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or not _task9_is_safe_relative_path(value.relative_path)
    ):
        raise _invalid()
    identity_fields = (
        value.device,
        value.inode,
        value.mode,
        value.owner,
        value.links,
        value.size,
        value.mtime_ns,
        value.ctime_ns,
    )
    if value.state == "ABSENT":
        if any(item is not None for item in identity_fields) or value.content_sha256 is not None:
            raise _invalid()
    elif value.state == "PRESENT":
        if (
            any(type(item) is not int or item < 0 for item in identity_fields)
            or not _stat.S_ISREG(value.mode)
            or value.links != 1
            or value.size > TASK9_EVIDENCE_BUNDLE_DECODER_CAP_V1
            or not _task9_is_sha256(value.content_sha256)
        ):
            raise _invalid()
    else:
        raise _invalid()
    _task9_validate_self(
        value,
        "path_snapshot_sha256",
        "INCI-TASK-9-EVIDENCE-PATH-SNAPSHOT-V1",
    )
    return value


def validate_task9_evidence_tree_snapshot_structure_v1(
    value: object,
) -> Task9EvidenceTreeSnapshotV1:
    if (
        type(value) is not Task9EvidenceTreeSnapshotV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or not _task9_is_safe_id(value.tree_id)
        or type(value.path_snapshot_sha256s) is not tuple
        or any(not _task9_is_sha256(item) for item in value.path_snapshot_sha256s)
        or len(set(value.path_snapshot_sha256s)) != len(value.path_snapshot_sha256s)
        or not _task9_is_sha256(value.tree_sha256)
    ):
        raise _invalid()
    _task9_validate_self(
        value,
        "snapshot_sha256",
        "INCI-TASK-9-EVIDENCE-TREE-SNAPSHOT-V1",
    )
    return value


def validate_task9_evidence_root_snapshot_structure_v1(
    value: object,
) -> EvidenceRootSnapshotV1:
    if (
        type(value) is not EvidenceRootSnapshotV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or type(value.snapshot_allocation_coordinate) is not int
        or not 1 <= value.snapshot_allocation_coordinate <= 9_223_372_036_854_775_807
        or not _task9_is_sha256(value.evidence_root_identity_sha256)
        or type(value.closed_path_count) is not int
        or value.closed_path_count != 147
        or type(value.present_path_count) is not int
        or not 0 <= value.present_path_count <= min(2_048, value.closed_path_count)
        or type(value.captured_bytes_total) is not int
        or not 0 <= value.captured_bytes_total
        <= TASK9_EVIDENCE_AGGREGATE_RETAINED_BYTE_CAP_V3
        or value.capture_policy_sha256
        != task9_evidence_capture_policy_sha256_v3()
        or value.decoder_table_sha256
        != task9_evidence_decoder_table_sha256_v3()
        or type(value.closed_temp_path_count) is not int
        or value.closed_temp_path_count != 36
        or type(value.present_temp_path_count) is not int
        or not 0 <= value.present_temp_path_count <= 36
        or value.transient_state
        != ("CLEAN" if value.present_temp_path_count == 0 else "DIRTY")
        or not _task9_is_sha256(value.transient_write_tree_snapshot_sha256)
        or type(value.path_snapshot_sha256s) is not tuple
        or len(value.path_snapshot_sha256s) != 147
        or len(set(value.path_snapshot_sha256s)) != 147
        or any(not _task9_is_sha256(item) for item in value.path_snapshot_sha256s)
        or type(value.tree_snapshot_sha256s) is not tuple
        or any(not _task9_is_sha256(item) for item in value.tree_snapshot_sha256s)
        or len(set(value.tree_snapshot_sha256s)) != len(value.tree_snapshot_sha256s)
    ):
        raise _invalid()
    _task9_validate_self(
        value,
        "snapshot_sha256",
        "INCI-TASK-9-EVIDENCE-ROOT-SNAPSHOT-V1",
    )
    return value


def _task9_stage_row(stage_id: object) -> Task9EvidenceStageRowV1:
    if type(stage_id) is not str:
        raise _invalid()
    for row in TASK9_EVIDENCE_STAGE_ROWS_V1:
        if row[0] == stage_id:
            return row
    raise _invalid()


def validate_task9_procedural_assignment_write_receipt_structure_v1(
    value: object,
) -> Task9ProceduralAssignmentWriteReceiptV1:
    if (
        type(value) is not Task9ProceduralAssignmentWriteReceiptV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.receipt_id != "task-9-procedural-assignment-write-receipt-v1"
        or value.write_mode not in ("INITIAL", "RECOVERY")
    ):
        raise _invalid()
    row = _task9_stage_row(value.stage_id)
    if (
        value.artifact_family != row[1]
        or value.artifact_self_field != row[2]
        or value.artifact_relative_path != row[3]
        or value.receipt_relative_path != row[4]
        or value.artifact_temp_relative_path != row[6]
        or value.receipt_temp_relative_path != row[7]
        or value.assignment_scope != row[0]
        or value.promotion_policy_sha256
        != TASK9_NO_REPLACE_PROMOTION_POLICY_V2.policy_sha256
        or value.writer_projection_sha256
        != _task9_assignment_writer_projection_sha256_v2(value.stage_id)
    ):
        raise _invalid()
    _task9_validate_sha_fields(
        value,
        (
            "assignment_sha256",
            "artifact_self_sha256",
            "artifact_content_sha256",
            "writer_projection_sha256",
        ),
    )
    _task9_validate_self(
        value,
        "receipt_sha256",
        "INCI-TASK-9-PROCEDURAL-ASSIGNMENT-WRITE-RECEIPT-V1",
    )
    return value


_TASK9_CHAIN_CARDINALITIES: Final[
    tuple[tuple[int, int, int, int], ...]
] = (
    (2, 0, 2, 0),
    (2, 0, 2, 1),
    (2, 4, 2, 2),
    (2, 0, 2, 1),
    (2, 0, 2, 1),
    (2, 0, 2, 1),
    (2, 0, 2, 1),
    (2, 0, 2, 1),
    (2, 0, 2, 1),
    (8, 0, 2, 9),
    (3, 4, 2, 1),
    (11, 5, 2, 11),
)


def validate_task9_chain_acceptance_receipt_structure_v1(
    value: object,
) -> Task9ChainAcceptanceReceiptV1:
    if (
        type(value) is not Task9ChainAcceptanceReceiptV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.receipt_id != "task-9-chain-acceptance-receipt-v1"
        or value.acceptance != "ACCEPTED"
    ):
        raise _invalid()
    row = _task9_stage_row(value.stage_id)
    stage_index = tuple(item[0] for item in TASK9_EVIDENCE_STAGE_ROWS_V1).index(
        value.stage_id
    )
    typed_count, raw_count, seal_count, antecedent_count = _TASK9_CHAIN_CARDINALITIES[
        stage_index
    ]
    sequences = (
        (value.semantic_evidence_sha256s, typed_count),
        (value.raw_evidence_content_sha256s, raw_count),
        (value.seal_sha256s, seal_count),
        (value.antecedent_chain_receipt_sha256s, antecedent_count),
    )
    if (
        value.artifact_family != row[1]
        or value.artifact_self_field != row[2]
        or value.artifact_relative_path != row[3]
        or value.receipt_relative_path != row[5]
        or value.receipt_temp_relative_path != row[8]
        or value.decoder_table_sha256 != task9_evidence_decoder_table_sha256_v3()
        or value.promotion_policy_sha256
        != TASK9_NO_REPLACE_PROMOTION_POLICY_V2.policy_sha256
        or value.stage_contract_sha256
        != _task9_contract_for_stage(value.stage_id).stage_contract_sha256
        or value.capture_policy_sha256
        != task9_evidence_capture_policy_sha256_v3()
        or value.validator_projection_sha256
        != _task9_chain_validator_projection_sha256_v2(value.stage_id)
    ):
        raise _invalid()
    for sequence, cardinality in sequences:
        if (
            type(sequence) is not tuple
            or len(sequence) != cardinality
            or any(not _task9_is_sha256(item) for item in sequence)
            or len(set(sequence)) != len(sequence)
        ):
            raise _invalid()
    _task9_validate_sha_fields(
        value,
        (
            "artifact_self_sha256",
            "artifact_content_sha256",
            "evidence_root_snapshot_sha256",
            "procedural_assignment_write_receipt_sha256",
            "stage_contract_sha256",
            "capture_policy_sha256",
            "validator_projection_sha256",
        ),
    )
    _task9_validate_self(
        value,
        "receipt_sha256",
        "INCI-TASK-9-CHAIN-ACCEPTANCE-RECEIPT-V1",
    )
    return value


def parse_task9_procedural_assignment_write_receipt_v1(
    payload: bytes,
) -> Task9ProceduralAssignmentWriteReceiptV1:
    obj = _task9_decode_canonical_json_object(payload, cap=131_072)
    _task9_exact_keys(
        obj,
        tuple(field.name for field in fields(Task9ProceduralAssignmentWriteReceiptV1)),
    )
    return validate_task9_procedural_assignment_write_receipt_structure_v1(
        Task9ProceduralAssignmentWriteReceiptV1(**obj)
    )


def parse_task9_chain_acceptance_receipt_v1(
    payload: bytes,
) -> Task9ChainAcceptanceReceiptV1:
    obj = _task9_decode_canonical_json_object(payload, cap=262_144)
    _task9_exact_keys(
        obj, tuple(field.name for field in fields(Task9ChainAcceptanceReceiptV1))
    )
    for name in (
        "semantic_evidence_sha256s",
        "raw_evidence_content_sha256s",
        "seal_sha256s",
        "antecedent_chain_receipt_sha256s",
    ):
        obj[name] = _task9_tuple_from_list(obj[name], lambda item: item)
    return validate_task9_chain_acceptance_receipt_structure_v1(
        Task9ChainAcceptanceReceiptV1(**obj)
    )


Task9ArtifactStageContractRowV1: TypeAlias = tuple[str, str, str, int, str, str]
Task9WriteReceiptStageContractRowV1: TypeAlias = tuple[
    str, str, str, int, str, str
]
Task9SemanticStageContractRowV1: TypeAlias = tuple[str, str, int, str, str]
Task9TreeStageContractRowV1: TypeAlias = tuple[str, str, str]
Task9RawStageContractRowV1: TypeAlias = tuple[str, str, int, str, str, str]
Task9SealStageContractRowV1: TypeAlias = tuple[str, str, int, str, str]
Task9AntecedentStageContractRowV1: TypeAlias = tuple[
    str, str, str, int, str, str
]
Task9ChainOutputStageContractRowV1: TypeAlias = tuple[
    str, str, str, int, str, str
]


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9ChainStageContractV1(_Task9ExactValue):
    schema_version: int
    stage_id: str
    A: Task9ArtifactStageContractRowV1
    W: Task9WriteReceiptStageContractRowV1
    O_rows: tuple[Task9SemanticStageContractRowV1, ...]
    T_rows: tuple[Task9TreeStageContractRowV1, ...]
    B_rows: tuple[Task9RawStageContractRowV1, ...]
    S_rows: tuple[Task9SealStageContractRowV1, ...]
    P_rows: tuple[Task9AntecedentStageContractRowV1, ...]
    C: Task9ChainOutputStageContractRowV1
    stage_contract_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9ChainStageContractTableV1(_Task9ExactValue):
    schema_version: int
    table_id: str
    stage_contract_sha256s: tuple[str, ...]
    table_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9RawRefSequencePositionV1(_Task9ExactValue):
    relative_path: str
    reference_owner_type: str
    reference_field: str
    raw_source: str


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class Task9RawRefSequenceStagePositionsV1(_Task9ExactValue):
    stage_id: str
    cardinality: int
    positions: tuple[Task9RawRefSequencePositionV1, ...]


@dataclass(frozen=True, slots=True, eq=False, repr=False, weakref_slot=True)
class RawSha256SequenceContractRowV1(_Task9ExactValue):
    schema_version: int
    owner_type: str
    field_name: str
    mode: str
    domain: None
    projection_fields: tuple[()]
    self_exclusions: tuple[()]
    stage_id_field: str
    position_rows_by_stage: tuple[Task9RawRefSequenceStagePositionsV1, ...]
    sequence_order_rule: str
    sequence_duplicate_rule: str


TASK9_TRANSIENT_WRITE_TREE_V1: Final[tuple[str, ...]] = tuple(
    sorted(TASK9_TRANSIENT_WRITE_PATHS_V1)
)
TASK9_TERMINAL_TOMBSTONE_CAP_PER_FAMILY_V1: Final[int] = 4_096


_TASK9_GOVERNING_RAW_ROWS: Final[tuple[Task9RawStageContractRowV1, ...]] = (
    (
        ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-controller-rulings.md",
        "TASK9_RAW_GOVERNED_BYTES_V1",
        1,
        "Task9EvidencePathSnapshotV1",
        "content_sha256",
        "INCI-TASK-9-GOVERNING-DOCUMENT-RAW-BYTES-V1",
    ),
    (
        ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-implementation-adjudications.md",
        "TASK9_RAW_GOVERNED_BYTES_V1",
        1,
        "Task9EvidencePathSnapshotV1",
        "content_sha256",
        "INCI-TASK-9-GOVERNING-DOCUMENT-RAW-BYTES-V1",
    ),
    (
        ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-preflight-map.md",
        "TASK9_RAW_GOVERNED_BYTES_V1",
        1,
        "Task9EvidencePathSnapshotV1",
        "content_sha256",
        "INCI-TASK-9-GOVERNING-DOCUMENT-RAW-BYTES-V1",
    ),
    (
        ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-parallel-execution-brief.md",
        "TASK9_RAW_GOVERNED_BYTES_V1",
        1,
        "Task9EvidencePathSnapshotV1",
        "content_sha256",
        "INCI-TASK-9-GOVERNING-DOCUMENT-RAW-BYTES-V1",
    ),
)
_TASK9_README_RAW_ROW: Final[Task9RawStageContractRowV1] = (
    "docs/tennis_v1/README.md",
    "TASK9_RAW_GOVERNED_BYTES_V1",
    1,
    "Task9EvidencePathSnapshotV1",
    "content_sha256",
    "INCI-TASK-9-DOCUMENTATION-RAW-BYTES-V1",
)


_TASK9_RAW_GOVERNING_POSITIONS_V1: Final[
    tuple[Task9RawRefSequencePositionV1, ...]
] = (
    Task9RawRefSequencePositionV1(
        relative_path=".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-controller-rulings.md",
        reference_owner_type="tools.task9_transition_evidence.Task9EvidencePathSnapshotV1",
        reference_field="content_sha256",
        raw_source="INCI-TASK-9-GOVERNING-DOCUMENT-RAW-BYTES-V1",
    ),
    Task9RawRefSequencePositionV1(
        relative_path=".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-implementation-adjudications.md",
        reference_owner_type="tools.task9_transition_evidence.Task9EvidencePathSnapshotV1",
        reference_field="content_sha256",
        raw_source="INCI-TASK-9-GOVERNING-DOCUMENT-RAW-BYTES-V1",
    ),
    Task9RawRefSequencePositionV1(
        relative_path=".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-preflight-map.md",
        reference_owner_type="tools.task9_transition_evidence.Task9EvidencePathSnapshotV1",
        reference_field="content_sha256",
        raw_source="INCI-TASK-9-GOVERNING-DOCUMENT-RAW-BYTES-V1",
    ),
    Task9RawRefSequencePositionV1(
        relative_path=".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-parallel-execution-brief.md",
        reference_owner_type="tools.task9_transition_evidence.Task9EvidencePathSnapshotV1",
        reference_field="content_sha256",
        raw_source="INCI-TASK-9-GOVERNING-DOCUMENT-RAW-BYTES-V1",
    ),
)

_TASK9_RAW_RELEASE_POSITIONS_V1: Final[
    tuple[Task9RawRefSequencePositionV1, ...]
] = (
    Task9RawRefSequencePositionV1(
        relative_path=".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-controller-rulings.md",
        reference_owner_type="tools.task9_transition_evidence.Task9EvidencePathSnapshotV1",
        reference_field="content_sha256",
        raw_source="INCI-TASK-9-GOVERNING-DOCUMENT-RAW-BYTES-V1",
    ),
    Task9RawRefSequencePositionV1(
        relative_path=".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-implementation-adjudications.md",
        reference_owner_type="tools.task9_transition_evidence.Task9EvidencePathSnapshotV1",
        reference_field="content_sha256",
        raw_source="INCI-TASK-9-GOVERNING-DOCUMENT-RAW-BYTES-V1",
    ),
    Task9RawRefSequencePositionV1(
        relative_path=".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-preflight-map.md",
        reference_owner_type="tools.task9_transition_evidence.Task9EvidencePathSnapshotV1",
        reference_field="content_sha256",
        raw_source="INCI-TASK-9-GOVERNING-DOCUMENT-RAW-BYTES-V1",
    ),
    Task9RawRefSequencePositionV1(
        relative_path=".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-9-parallel-execution-brief.md",
        reference_owner_type="tools.task9_transition_evidence.Task9EvidencePathSnapshotV1",
        reference_field="content_sha256",
        raw_source="INCI-TASK-9-GOVERNING-DOCUMENT-RAW-BYTES-V1",
    ),
    Task9RawRefSequencePositionV1(
        relative_path="docs/tennis_v1/README.md",
        reference_owner_type="tools.task9_transition_evidence.Task9EvidencePathSnapshotV1",
        reference_field="content_sha256",
        raw_source="INCI-TASK-9-DOCUMENTATION-RAW-BYTES-V1",
    ),
)

TASK9_RAW_SHA256_SEQUENCE_CONTRACT_ROW_V1: Final[
    RawSha256SequenceContractRowV1
] = RawSha256SequenceContractRowV1(
    schema_version=1,
    owner_type="tools.task9_transition_evidence.Task9ChainAcceptanceReceiptV1",
    field_name="raw_evidence_content_sha256s",
    mode="RAW_REF_SEQUENCE",
    domain=None,
    projection_fields=(),
    self_exclusions=(),
    stage_id_field="stage_id",
    position_rows_by_stage=(
        Task9RawRefSequenceStagePositionsV1(
            stage_id="PREDECESSOR_TRANSITION_MANIFEST", cardinality=0, positions=()
        ),
        Task9RawRefSequenceStagePositionsV1(
            stage_id="PREDECESSOR_TRANSITION_REVIEW", cardinality=0, positions=()
        ),
        Task9RawRefSequenceStagePositionsV1(
            stage_id="POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW",
            cardinality=4,
            positions=_TASK9_RAW_GOVERNING_POSITIONS_V1,
        ),
        Task9RawRefSequenceStagePositionsV1(
            stage_id="FUNCTIONAL_WAVE_REVIEW_A", cardinality=0, positions=()
        ),
        Task9RawRefSequenceStagePositionsV1(
            stage_id="FUNCTIONAL_WAVE_REVIEW_B", cardinality=0, positions=()
        ),
        Task9RawRefSequenceStagePositionsV1(
            stage_id="FUNCTIONAL_WAVE_REVIEW_C", cardinality=0, positions=()
        ),
        Task9RawRefSequenceStagePositionsV1(
            stage_id="FUNCTIONAL_WAVE_REVIEW_D", cardinality=0, positions=()
        ),
        Task9RawRefSequenceStagePositionsV1(
            stage_id="FUNCTIONAL_WAVE_REVIEW_E", cardinality=0, positions=()
        ),
        Task9RawRefSequenceStagePositionsV1(
            stage_id="FUNCTIONAL_WAVE_REVIEW_R", cardinality=0, positions=()
        ),
        Task9RawRefSequenceStagePositionsV1(
            stage_id="FINAL_RESEAL_TRANSITION", cardinality=0, positions=()
        ),
        Task9RawRefSequenceStagePositionsV1(
            stage_id="FINAL_RESEAL_REVIEW",
            cardinality=4,
            positions=_TASK9_RAW_GOVERNING_POSITIONS_V1,
        ),
        Task9RawRefSequenceStagePositionsV1(
            stage_id="RELEASE_EVIDENCE",
            cardinality=5,
            positions=_TASK9_RAW_RELEASE_POSITIONS_V1,
        ),
    ),
    sequence_order_rule="TASK9_STAGE_CONTRACT_B_ROW_ORDER_V1",
    sequence_duplicate_rule="FORBID_EXACT_OBJECT_PATH_AND_DIGEST_DUPLICATES",
)


def _task9_make_chain_stage_contract(
    *,
    stage_id: str,
    A: Task9ArtifactStageContractRowV1,
    W: Task9WriteReceiptStageContractRowV1,
    O_rows: tuple[Task9SemanticStageContractRowV1, ...],
    T_rows: tuple[Task9TreeStageContractRowV1, ...],
    B_rows: tuple[Task9RawStageContractRowV1, ...],
    S_rows: tuple[Task9SealStageContractRowV1, ...],
    P_rows: tuple[Task9AntecedentStageContractRowV1, ...],
    C: Task9ChainOutputStageContractRowV1,
) -> Task9ChainStageContractV1:
    projection = {
        "schema_version": 1,
        "stage_id": stage_id,
        "A": A,
        "W": W,
        "O_rows": O_rows,
        "T_rows": T_rows,
        "B_rows": B_rows,
        "S_rows": S_rows,
        "P_rows": P_rows,
        "C": C,
    }
    return Task9ChainStageContractV1(
        **projection,
        stage_contract_sha256=_task9_domain_sha256_v1(
            "INCI-TASK-9-CHAIN-STAGE-CONTRACT-V1", projection
        ),
    )


_TASK9_STAGE_CONTRACT_01 = _task9_make_chain_stage_contract(
    stage_id="PREDECESSOR_TRANSITION_MANIFEST",
    A=("task-9-predecessor-transition-manifest-v1.json", "task-9-predecessor-transition-manifest-v1.json.tmp-v1", "TASK9_PREDECESSOR_TRANSITION_MANIFEST_V1", 1, "Task9PredecessorTransitionManifestV1", "manifest_sha256"),
    W=("task-9-predecessor-transition-manifest-procedural-assignment-write-receipt-v1.json", "task-9-predecessor-transition-manifest-procedural-assignment-write-receipt-v1.json.tmp-v1", "TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1", 1, "Task9ProceduralAssignmentWriteReceiptV1", "receipt_sha256"),
    O_rows=(("task-9-predecessor-evidence-bundle-v1.json", "TASK9_EVIDENCE_BUNDLE_V1", 1, "Task9EvidenceBundleV1", "bundle_sha256"),),
    T_rows=(("TASK9_PREDECESSOR_TRANSITION_TREE_V1", "Task9EvidenceTreeSnapshotV1", "snapshot_sha256"),),
    B_rows=(),
    S_rows=(("task-9-predecessor-source-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256"), ("task-9-predecessor-resource-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256")),
    P_rows=(),
    C=("task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json", "task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json.tmp-v1", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
)

_TASK9_STAGE_CONTRACT_02 = _task9_make_chain_stage_contract(
    stage_id="PREDECESSOR_TRANSITION_REVIEW",
    A=("task-9-predecessor-transition-review-v1.json", "task-9-predecessor-transition-review-v1.json.tmp-v1", "TASK9_PREDECESSOR_TRANSITION_REVIEW_V1", 1, "Task9PredecessorTransitionReviewV1", "review_sha256"),
    W=("task-9-predecessor-transition-review-procedural-assignment-write-receipt-v1.json", "task-9-predecessor-transition-review-procedural-assignment-write-receipt-v1.json.tmp-v1", "TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1", 1, "Task9ProceduralAssignmentWriteReceiptV1", "receipt_sha256"),
    O_rows=(("task-9-predecessor-evidence-bundle-v1.json", "TASK9_EVIDENCE_BUNDLE_V1", 1, "Task9EvidenceBundleV1", "bundle_sha256"),),
    T_rows=(("TASK9_PREDECESSOR_TRANSITION_TREE_V1", "Task9EvidenceTreeSnapshotV1", "snapshot_sha256"),),
    B_rows=(),
    S_rows=(("task-9-predecessor-source-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256"), ("task-9-predecessor-resource-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256")),
    P_rows=(("PREDECESSOR_TRANSITION_MANIFEST", "task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),),
    C=("task-9-predecessor-transition-review-chain-acceptance-receipt-v1.json", "task-9-predecessor-transition-review-chain-acceptance-receipt-v1.json.tmp-v1", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
)

_TASK9_STAGE_CONTRACT_03 = _task9_make_chain_stage_contract(
    stage_id="POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW",
    A=("task-9-post-predecessor-amended-package-rereview-v1.json", "task-9-post-predecessor-amended-package-rereview-v1.json.tmp-v1", "TASK9_POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW_V1", 1, "Task9PostPredecessorAmendedPackageRereviewV1", "review_sha256"),
    W=("task-9-post-predecessor-amended-package-rereview-procedural-assignment-write-receipt-v1.json", "task-9-post-predecessor-amended-package-rereview-procedural-assignment-write-receipt-v1.json.tmp-v1", "TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1", 1, "Task9ProceduralAssignmentWriteReceiptV1", "receipt_sha256"),
    O_rows=(("task-9-predecessor-evidence-bundle-v1.json", "TASK9_EVIDENCE_BUNDLE_V1", 1, "Task9EvidenceBundleV1", "bundle_sha256"),),
    T_rows=(("TASK9_POST_PREDECESSOR_AMENDED_PACKAGE_TREE_V1", "Task9EvidenceTreeSnapshotV1", "snapshot_sha256"),),
    B_rows=_TASK9_GOVERNING_RAW_ROWS,
    S_rows=(("task-9-predecessor-source-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256"), ("task-9-predecessor-resource-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256")),
    P_rows=(("PREDECESSOR_TRANSITION_MANIFEST", "task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"), ("PREDECESSOR_TRANSITION_REVIEW", "task-9-predecessor-transition-review-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256")),
    C=("task-9-post-predecessor-amended-package-rereview-chain-acceptance-receipt-v1.json", "task-9-post-predecessor-amended-package-rereview-chain-acceptance-receipt-v1.json.tmp-v1", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
)

_TASK9_STAGE_CONTRACT_04 = _task9_make_chain_stage_contract(
    stage_id="FUNCTIONAL_WAVE_REVIEW_A",
    A=("task-9-functional-wave-review-a-v1.json", "task-9-functional-wave-review-a-v1.json.tmp-v1", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
    W=("task-9-functional-wave-review-a-procedural-assignment-write-receipt-v1.json", "task-9-functional-wave-review-a-procedural-assignment-write-receipt-v1.json.tmp-v1", "TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1", 1, "Task9ProceduralAssignmentWriteReceiptV1", "receipt_sha256"),
    O_rows=(("task-9-functional-wave-a-evidence-bundle-v1.json", "TASK9_EVIDENCE_BUNDLE_V1", 1, "Task9EvidenceBundleV1", "bundle_sha256"),),
    T_rows=(("TASK9_FUNCTIONAL_WAVE_A_TREE_V1", "Task9EvidenceTreeSnapshotV1", "snapshot_sha256"),),
    B_rows=(),
    S_rows=(("task-9-functional-wave-a-source-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256"), ("task-9-functional-wave-a-resource-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256")),
    P_rows=(("POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW", "task-9-post-predecessor-amended-package-rereview-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),),
    C=("task-9-functional-wave-review-a-chain-acceptance-receipt-v1.json", "task-9-functional-wave-review-a-chain-acceptance-receipt-v1.json.tmp-v1", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
)

_TASK9_STAGE_CONTRACT_05 = _task9_make_chain_stage_contract(
    stage_id="FUNCTIONAL_WAVE_REVIEW_B",
    A=("task-9-functional-wave-review-b-v1.json", "task-9-functional-wave-review-b-v1.json.tmp-v1", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
    W=("task-9-functional-wave-review-b-procedural-assignment-write-receipt-v1.json", "task-9-functional-wave-review-b-procedural-assignment-write-receipt-v1.json.tmp-v1", "TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1", 1, "Task9ProceduralAssignmentWriteReceiptV1", "receipt_sha256"),
    O_rows=(("task-9-functional-wave-b-evidence-bundle-v1.json", "TASK9_EVIDENCE_BUNDLE_V1", 1, "Task9EvidenceBundleV1", "bundle_sha256"),),
    T_rows=(("TASK9_FUNCTIONAL_WAVE_B_TREE_V1", "Task9EvidenceTreeSnapshotV1", "snapshot_sha256"),),
    B_rows=(),
    S_rows=(("task-9-functional-wave-b-source-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256"), ("task-9-functional-wave-b-resource-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256")),
    P_rows=(("FUNCTIONAL_WAVE_REVIEW_A", "task-9-functional-wave-review-a-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),),
    C=("task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json", "task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json.tmp-v1", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
)

_TASK9_STAGE_CONTRACT_06 = _task9_make_chain_stage_contract(
    stage_id="FUNCTIONAL_WAVE_REVIEW_C",
    A=("task-9-functional-wave-review-c-v1.json", "task-9-functional-wave-review-c-v1.json.tmp-v1", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
    W=("task-9-functional-wave-review-c-procedural-assignment-write-receipt-v1.json", "task-9-functional-wave-review-c-procedural-assignment-write-receipt-v1.json.tmp-v1", "TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1", 1, "Task9ProceduralAssignmentWriteReceiptV1", "receipt_sha256"),
    O_rows=(("task-9-functional-wave-c-evidence-bundle-v1.json", "TASK9_EVIDENCE_BUNDLE_V1", 1, "Task9EvidenceBundleV1", "bundle_sha256"),),
    T_rows=(("TASK9_FUNCTIONAL_WAVE_C_TREE_V1", "Task9EvidenceTreeSnapshotV1", "snapshot_sha256"),),
    B_rows=(),
    S_rows=(("task-9-functional-wave-c-source-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256"), ("task-9-functional-wave-c-resource-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256")),
    P_rows=(("FUNCTIONAL_WAVE_REVIEW_B", "task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),),
    C=("task-9-functional-wave-review-c-chain-acceptance-receipt-v1.json", "task-9-functional-wave-review-c-chain-acceptance-receipt-v1.json.tmp-v1", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
)

_TASK9_STAGE_CONTRACT_07 = _task9_make_chain_stage_contract(
    stage_id="FUNCTIONAL_WAVE_REVIEW_D",
    A=("task-9-functional-wave-review-d-v1.json", "task-9-functional-wave-review-d-v1.json.tmp-v1", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
    W=("task-9-functional-wave-review-d-procedural-assignment-write-receipt-v1.json", "task-9-functional-wave-review-d-procedural-assignment-write-receipt-v1.json.tmp-v1", "TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1", 1, "Task9ProceduralAssignmentWriteReceiptV1", "receipt_sha256"),
    O_rows=(("task-9-functional-wave-d-evidence-bundle-v1.json", "TASK9_EVIDENCE_BUNDLE_V1", 1, "Task9EvidenceBundleV1", "bundle_sha256"),),
    T_rows=(("TASK9_FUNCTIONAL_WAVE_D_TREE_V1", "Task9EvidenceTreeSnapshotV1", "snapshot_sha256"),),
    B_rows=(),
    S_rows=(("task-9-functional-wave-d-source-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256"), ("task-9-functional-wave-d-resource-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256")),
    P_rows=(("FUNCTIONAL_WAVE_REVIEW_C", "task-9-functional-wave-review-c-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),),
    C=("task-9-functional-wave-review-d-chain-acceptance-receipt-v1.json", "task-9-functional-wave-review-d-chain-acceptance-receipt-v1.json.tmp-v1", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
)

_TASK9_STAGE_CONTRACT_08 = _task9_make_chain_stage_contract(
    stage_id="FUNCTIONAL_WAVE_REVIEW_E",
    A=("task-9-functional-wave-review-e-v1.json", "task-9-functional-wave-review-e-v1.json.tmp-v1", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
    W=("task-9-functional-wave-review-e-procedural-assignment-write-receipt-v1.json", "task-9-functional-wave-review-e-procedural-assignment-write-receipt-v1.json.tmp-v1", "TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1", 1, "Task9ProceduralAssignmentWriteReceiptV1", "receipt_sha256"),
    O_rows=(("task-9-functional-wave-e-evidence-bundle-v1.json", "TASK9_EVIDENCE_BUNDLE_V1", 1, "Task9EvidenceBundleV1", "bundle_sha256"),),
    T_rows=(("TASK9_FUNCTIONAL_WAVE_E_TREE_V1", "Task9EvidenceTreeSnapshotV1", "snapshot_sha256"),),
    B_rows=(),
    S_rows=(("task-9-functional-wave-e-source-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256"), ("task-9-functional-wave-e-resource-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256")),
    P_rows=(("FUNCTIONAL_WAVE_REVIEW_D", "task-9-functional-wave-review-d-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),),
    C=("task-9-functional-wave-review-e-chain-acceptance-receipt-v1.json", "task-9-functional-wave-review-e-chain-acceptance-receipt-v1.json.tmp-v1", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
)

_TASK9_STAGE_CONTRACT_09 = _task9_make_chain_stage_contract(
    stage_id="FUNCTIONAL_WAVE_REVIEW_R",
    A=("task-9-functional-wave-review-r-v1.json", "task-9-functional-wave-review-r-v1.json.tmp-v1", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
    W=("task-9-functional-wave-review-r-procedural-assignment-write-receipt-v1.json", "task-9-functional-wave-review-r-procedural-assignment-write-receipt-v1.json.tmp-v1", "TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1", 1, "Task9ProceduralAssignmentWriteReceiptV1", "receipt_sha256"),
    O_rows=(("task-9-functional-wave-r-evidence-bundle-v1.json", "TASK9_EVIDENCE_BUNDLE_V1", 1, "Task9EvidenceBundleV1", "bundle_sha256"),),
    T_rows=(("TASK9_FUNCTIONAL_WAVE_R_TREE_V1", "Task9EvidenceTreeSnapshotV1", "snapshot_sha256"),),
    B_rows=(),
    S_rows=(("task-9-functional-wave-r-source-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256"), ("task-9-functional-wave-r-resource-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256")),
    P_rows=(("FUNCTIONAL_WAVE_REVIEW_E", "task-9-functional-wave-review-e-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),),
    C=("task-9-functional-wave-review-r-chain-acceptance-receipt-v1.json", "task-9-functional-wave-review-r-chain-acceptance-receipt-v1.json.tmp-v1", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
)

_TASK9_STAGE_CONTRACT_10 = _task9_make_chain_stage_contract(
    stage_id="FINAL_RESEAL_TRANSITION",
    A=("task-9-final-reseal-transition-v1.json", "task-9-final-reseal-transition-v1.json.tmp-v1", "TASK9_FINAL_RESEAL_TRANSITION_V1", 1, "Task9FinalResealTransitionV1", "manifest_sha256"),
    W=("task-9-final-reseal-transition-procedural-assignment-write-receipt-v1.json", "task-9-final-reseal-transition-procedural-assignment-write-receipt-v1.json.tmp-v1", "TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1", 1, "Task9ProceduralAssignmentWriteReceiptV1", "receipt_sha256"),
    O_rows=(
        ("task-9-final-reseal-evidence-bundle-v1.json", "TASK9_EVIDENCE_BUNDLE_V1", 1, "Task9EvidenceBundleV1", "bundle_sha256"),
        ("task-9-functional-wave-review-a-v1.json", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
        ("task-9-functional-wave-review-b-v1.json", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
        ("task-9-functional-wave-review-c-v1.json", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
        ("task-9-functional-wave-review-d-v1.json", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
        ("task-9-functional-wave-review-e-v1.json", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
        ("task-9-functional-wave-review-r-v1.json", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
    ),
    T_rows=(("TASK9_FINAL_RESEAL_TREE_V1", "Task9EvidenceTreeSnapshotV1", "snapshot_sha256"),),
    B_rows=(),
    S_rows=(("task-9-final-source-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256"), ("task-9-final-resource-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256")),
    P_rows=(
        ("PREDECESSOR_TRANSITION_MANIFEST", "task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("PREDECESSOR_TRANSITION_REVIEW", "task-9-predecessor-transition-review-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW", "task-9-post-predecessor-amended-package-rereview-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FUNCTIONAL_WAVE_REVIEW_A", "task-9-functional-wave-review-a-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FUNCTIONAL_WAVE_REVIEW_B", "task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FUNCTIONAL_WAVE_REVIEW_C", "task-9-functional-wave-review-c-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FUNCTIONAL_WAVE_REVIEW_D", "task-9-functional-wave-review-d-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FUNCTIONAL_WAVE_REVIEW_E", "task-9-functional-wave-review-e-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FUNCTIONAL_WAVE_REVIEW_R", "task-9-functional-wave-review-r-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
    ),
    C=("task-9-final-reseal-transition-chain-acceptance-receipt-v1.json", "task-9-final-reseal-transition-chain-acceptance-receipt-v1.json.tmp-v1", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
)

_TASK9_STAGE_CONTRACT_11 = _task9_make_chain_stage_contract(
    stage_id="FINAL_RESEAL_REVIEW",
    A=("task-9-final-reseal-review-v1.json", "task-9-final-reseal-review-v1.json.tmp-v1", "TASK9_FINAL_RESEAL_REVIEW_V1", 1, "Task9FinalResealReviewV1", "review_sha256"),
    W=("task-9-final-reseal-review-procedural-assignment-write-receipt-v1.json", "task-9-final-reseal-review-procedural-assignment-write-receipt-v1.json.tmp-v1", "TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1", 1, "Task9ProceduralAssignmentWriteReceiptV1", "receipt_sha256"),
    O_rows=(
        ("task-9-final-reseal-evidence-bundle-v1.json", "TASK9_EVIDENCE_BUNDLE_V1", 1, "Task9EvidenceBundleV1", "bundle_sha256"),
        ("task-9-final-reseal-transition-v1.json", "TASK9_FINAL_RESEAL_TRANSITION_V1", 1, "Task9FinalResealTransitionV1", "manifest_sha256"),
    ),
    T_rows=(("TASK9_FINAL_RESEAL_TREE_V1", "Task9EvidenceTreeSnapshotV1", "snapshot_sha256"),),
    B_rows=_TASK9_GOVERNING_RAW_ROWS,
    S_rows=(("task-9-final-source-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256"), ("task-9-final-resource-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256")),
    P_rows=(("FINAL_RESEAL_TRANSITION", "task-9-final-reseal-transition-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),),
    C=("task-9-final-reseal-review-chain-acceptance-receipt-v1.json", "task-9-final-reseal-review-chain-acceptance-receipt-v1.json.tmp-v1", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
)

_TASK9_STAGE_CONTRACT_12 = _task9_make_chain_stage_contract(
    stage_id="RELEASE_EVIDENCE",
    A=("task-9-release-evidence-v1.json", "task-9-release-evidence-v1.json.tmp-v1", "TASK9_RELEASE_EVIDENCE_V1", 1, "Task9ReleaseEvidenceV1", "record_sha256"),
    W=("task-9-release-evidence-procedural-assignment-write-receipt-v1.json", "task-9-release-evidence-procedural-assignment-write-receipt-v1.json.tmp-v1", "TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1", 1, "Task9ProceduralAssignmentWriteReceiptV1", "receipt_sha256"),
    O_rows=(
        ("task-9-release-support-evidence-bundle-v1.json", "TASK9_EVIDENCE_BUNDLE_V1", 1, "Task9EvidenceBundleV1", "bundle_sha256"),
        ("task-9-documentation-evidence-v1.json", "TASK9_DOCUMENTATION_EVIDENCE_V1", 1, "Task9DocumentationEvidenceV1", "evidence_sha256"),
        ("task-9-functional-wave-review-a-v1.json", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
        ("task-9-functional-wave-review-b-v1.json", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
        ("task-9-functional-wave-review-c-v1.json", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
        ("task-9-functional-wave-review-d-v1.json", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
        ("task-9-functional-wave-review-e-v1.json", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
        ("task-9-functional-wave-review-r-v1.json", "TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1, "Task9FunctionalWaveReviewV1", "review_sha256"),
        ("task-9-final-reseal-transition-v1.json", "TASK9_FINAL_RESEAL_TRANSITION_V1", 1, "Task9FinalResealTransitionV1", "manifest_sha256"),
        ("task-9-final-reseal-review-v1.json", "TASK9_FINAL_RESEAL_REVIEW_V1", 1, "Task9FinalResealReviewV1", "review_sha256"),
    ),
    T_rows=(("TASK9_RELEASE_SUPPORT_TREE_V1", "Task9EvidenceTreeSnapshotV1", "snapshot_sha256"),),
    B_rows=_TASK9_GOVERNING_RAW_ROWS + (_TASK9_README_RAW_ROW,),
    S_rows=(("task-9-final-source-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256"), ("task-9-final-resource-seal-v1.json", "TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 1, "Task9SealV1", "seal_sha256")),
    P_rows=(
        ("PREDECESSOR_TRANSITION_MANIFEST", "task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("PREDECESSOR_TRANSITION_REVIEW", "task-9-predecessor-transition-review-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW", "task-9-post-predecessor-amended-package-rereview-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FUNCTIONAL_WAVE_REVIEW_A", "task-9-functional-wave-review-a-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FUNCTIONAL_WAVE_REVIEW_B", "task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FUNCTIONAL_WAVE_REVIEW_C", "task-9-functional-wave-review-c-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FUNCTIONAL_WAVE_REVIEW_D", "task-9-functional-wave-review-d-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FUNCTIONAL_WAVE_REVIEW_E", "task-9-functional-wave-review-e-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FUNCTIONAL_WAVE_REVIEW_R", "task-9-functional-wave-review-r-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FINAL_RESEAL_TRANSITION", "task-9-final-reseal-transition-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
        ("FINAL_RESEAL_REVIEW", "task-9-final-reseal-review-chain-acceptance-receipt-v1.json", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
    ),
    C=("task-9-release-evidence-chain-acceptance-receipt-v1.json", "task-9-release-evidence-chain-acceptance-receipt-v1.json.tmp-v1", "TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 1, "Task9ChainAcceptanceReceiptV1", "receipt_sha256"),
)

TASK9_CHAIN_STAGE_CONTRACTS_V1: Final[
    tuple[Task9ChainStageContractV1, ...]
] = (
    _TASK9_STAGE_CONTRACT_01,
    _TASK9_STAGE_CONTRACT_02,
    _TASK9_STAGE_CONTRACT_03,
    _TASK9_STAGE_CONTRACT_04,
    _TASK9_STAGE_CONTRACT_05,
    _TASK9_STAGE_CONTRACT_06,
    _TASK9_STAGE_CONTRACT_07,
    _TASK9_STAGE_CONTRACT_08,
    _TASK9_STAGE_CONTRACT_09,
    _TASK9_STAGE_CONTRACT_10,
    _TASK9_STAGE_CONTRACT_11,
    _TASK9_STAGE_CONTRACT_12,
)

_TASK9_CHAIN_STAGE_TABLE_PROJECTION: Final[dict[str, object]] = {
    "schema_version": 1,
    "table_id": "TASK9_CHAIN_STAGE_CONTRACT_TABLE_V1",
    "stage_contract_sha256s": tuple(
        contract.stage_contract_sha256 for contract in TASK9_CHAIN_STAGE_CONTRACTS_V1
    ),
}
TASK9_CHAIN_STAGE_CONTRACT_TABLE_V1: Final[Task9ChainStageContractTableV1] = (
    Task9ChainStageContractTableV1(
        **_TASK9_CHAIN_STAGE_TABLE_PROJECTION,
        table_sha256=_task9_domain_sha256_v1(
            "INCI-TASK-9-CHAIN-STAGE-CONTRACT-TABLE-V1",
            _TASK9_CHAIN_STAGE_TABLE_PROJECTION,
        ),
    )
)


_TASK9_CAPTURE_POLICY_PROJECTION_V2: Final[dict[str, object]] = {
    "aggregate_retained_bytes_cap": 268435456,
    "capture_policy_id": "TASK9_EVIDENCE_CAPTURE_POLICY_V2",
    "closed_path_count_cap": 4096,
    "closed_temp_path_count": 36,
    "close_cleanup_step_ids": (
        "SNAPSHOT_ACTIVE_TO_CLOSING",
        "DROP_DECODE_CACHE",
        "DROP_DECODE_FAILURE_CACHE",
        "DROP_RETAINED_BYTES",
        "DROP_PATH_AND_TREE_OBJECTS",
        "REMOVE_SNAPSHOT_LIVE_CELL",
        "INSTALL_WEAK_TERMINAL_TOMBSTONE",
        "SNAPSHOT_CLOSING_TO_CLOSED",
    ),
    "decoder_caps": (
        ("TASK9_PREDECESSOR_TRANSITION_MANIFEST_V1", 1048576),
        ("TASK9_PREDECESSOR_TRANSITION_REVIEW_V1", 1048576),
        ("TASK9_POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW_V1", 1048576),
        ("TASK9_FUNCTIONAL_WAVE_REVIEW_V1", 1048576),
        ("TASK9_FINAL_RESEAL_TRANSITION_V1", 1048576),
        ("TASK9_FINAL_RESEAL_REVIEW_V1", 1048576),
        ("TASK9_RELEASE_EVIDENCE_V1", 1048576),
        ("TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1", 131072),
        ("TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1", 262144),
        ("TASK9_EVIDENCE_BUNDLE_V1", 16777216),
        ("TASK9_SOURCE_OR_RESOURCE_SEAL_V1", 4194304),
        ("TASK9_DOCUMENTATION_EVIDENCE_V1", 1048576),
        ("TASK9_RAW_GOVERNED_BYTES_V1", 8388608),
    ),
    "descriptor_size_check_step_ids": (
        "OPEN_ROOT_NOFOLLOW",
        "OPEN_PATH_NOFOLLOW",
        "FSTAT_BEFORE_SIZE",
        "CHECK_ROW_CAP",
        "CHECK_SINGLE_FILE_CAP",
        "CHECK_AGGREGATE_REMAINING",
        "READ_EXACT_SIZE",
        "FSTAT_AFTER_READ",
        "VERIFY_STABLE_IDENTITY",
        "CLOSE_PATH_DESCRIPTOR",
    ),
    "issuance_cleanup_step_ids": (
        "CLOSE_ALL_OPEN_PATH_DESCRIPTORS",
        "DROP_PARTIAL_RETAINED_BYTES",
        "DROP_PARTIAL_PATH_OBJECTS",
        "DROP_PARTIAL_TREE_OBJECTS",
        "DROP_PARTIAL_ROOT_OBJECT",
        "REMOVE_PARTIAL_SNAPSHOT_LIVE_CELL",
    ),
    "namespace_reconciliation_rows": (
        ("FINAL_ABSENT", "TEMP_ABSENT", "NO_DURABLE_OUTPUT"),
        ("FINAL_ABSENT", "TEMP_EXACT_SAFE_SINGLE_LINK", "UNLINK_SAFE_ORPHAN_AND_FSYNC_ROOT"),
        ("FINAL_ABSENT", "TEMP_UNSAFE_OR_CONFLICTING", "QUARANTINED_DIRTY"),
        ("FINAL_EXACT_SAFE", "TEMP_ABSENT", "VALIDATE_FINAL_AND_FSYNC_ROOT"),
        ("FINAL_EXACT_SAFE", "TEMP_SAME_INODE_LINK_COUNT_TWO", "UNLINK_TEMP_VERIFY_FINAL_AND_FSYNC_ROOT"),
        ("FINAL_PRESENT", "TEMP_DIFFERENT_INODE_OR_UNSAFE", "QUARANTINED_DIRTY"),
        ("FINAL_CONFLICTING_OR_UNSAFE", "TEMP_ANY", "QUARANTINED_CONFLICT"),
        ("OBSERVATION_UNCERTAIN", "TEMP_ANY", "DURABILITY_UNCERTAIN"),
    ),
    "present_path_count_cap": 2048,
    "schema_version": 2,
    "single_file_hard_cap": 16777216,
    "stage_owned_final_path_count": 36,
    "stage_owned_temp_path_count": 36,
    "terminal_tombstone_cap_per_family": 4096,
    "transient_caps_by_output_kind": (
        ("ARTIFACT", 1048576),
        ("PROCEDURAL_ASSIGNMENT_RECEIPT", 131072),
        ("CHAIN_ACCEPTANCE_RECEIPT", 262144),
    ),
    "uncertainty_precedence": (
        "DESCRIPTOR_CLOSE_UNCERTAIN",
        "ROOT_DIRECTORY_FSYNC_UNCERTAIN",
        "NAMESPACE_IDENTITY_UNCERTAIN",
        "PAYLOAD_DURABILITY_UNCERTAIN",
        "SEMANTIC_INVALID",
    ),
}

TASK9_EVIDENCE_CAPTURE_POLICY_V3: Final[object] = _MappingProxyType({'aggregate_retained_bytes_cap': 843055104,
 'capture_policy_id': 'TASK9_EVIDENCE_CAPTURE_POLICY_V3',
 'closed_path_count_cap': 4096,
 'closed_temp_path_count': 36,
 'close_cleanup_step_ids': ('SNAPSHOT_ACTIVE_TO_CLOSING',
                            'DROP_DECODE_CACHE',
                            'DROP_DECODE_FAILURE_CACHE',
                            'DROP_RETAINED_BYTES',
                            'DROP_PATH_AND_TREE_OBJECTS',
                            'REMOVE_SNAPSHOT_LIVE_CELL',
                            'INSTALL_WEAK_TERMINAL_TOMBSTONE',
                            'SNAPSHOT_CLOSING_TO_CLOSED'),
 'decoder_caps': (('TASK9_PREDECESSOR_TRANSITION_MANIFEST_V1', 1048576),
                  ('TASK9_PREDECESSOR_TRANSITION_REVIEW_V1', 1048576),
                  ('TASK9_POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW_V1', 1048576),
                  ('TASK9_FUNCTIONAL_WAVE_REVIEW_V1', 1048576),
                  ('TASK9_FINAL_RESEAL_TRANSITION_V1', 1048576),
                  ('TASK9_FINAL_RESEAL_REVIEW_V1', 1048576),
                  ('TASK9_RELEASE_EVIDENCE_V1', 1048576),
                  ('TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1', 131072),
                  ('TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1', 262144),
                  ('TASK9_EVIDENCE_BUNDLE_V1', 150994944),
                  ('TASK9_SOURCE_OR_RESOURCE_SEAL_V1', 4194304),
                  ('TASK9_DOCUMENTATION_EVIDENCE_V1', 1048576),
                  ('TASK9_RAW_GOVERNED_BYTES_V1', 8388608)),
 'descriptor_size_check_step_ids': ('OPEN_ROOT_NOFOLLOW',
                                    'OPEN_PATH_NOFOLLOW',
                                    'FSTAT_BEFORE_SIZE',
                                    'CHECK_ROW_CAP',
                                    'CHECK_SINGLE_FILE_CAP',
                                    'CHECK_AGGREGATE_REMAINING',
                                    'READ_EXACT_SIZE',
                                    'FSTAT_AFTER_READ',
                                    'VERIFY_STABLE_IDENTITY',
                                    'CLOSE_PATH_DESCRIPTOR'),
 'issuance_cleanup_step_ids': ('CLOSE_ALL_OPEN_PATH_DESCRIPTORS',
                               'DROP_PARTIAL_RETAINED_BYTES',
                               'DROP_PARTIAL_PATH_OBJECTS',
                               'DROP_PARTIAL_TREE_OBJECTS',
                               'DROP_PARTIAL_ROOT_OBJECT',
                               'REMOVE_PARTIAL_SNAPSHOT_LIVE_CELL'),
 'namespace_reconciliation_rows': (('FINAL_ABSENT', 'TEMP_ABSENT', 'NO_DURABLE_OUTPUT'),
                                   ('FINAL_ABSENT',
                                    'TEMP_EXACT_SAFE_SINGLE_LINK',
                                    'UNLINK_SAFE_ORPHAN_AND_FSYNC_ROOT'),
                                   ('FINAL_ABSENT',
                                    'TEMP_UNSAFE_OR_CONFLICTING',
                                    'QUARANTINED_DIRTY'),
                                   ('FINAL_EXACT_SAFE',
                                    'TEMP_ABSENT',
                                    'VALIDATE_FINAL_AND_FSYNC_ROOT'),
                                   ('FINAL_EXACT_SAFE',
                                    'TEMP_SAME_INODE_LINK_COUNT_TWO',
                                    'UNLINK_TEMP_VERIFY_FINAL_AND_FSYNC_ROOT'),
                                   ('FINAL_PRESENT',
                                    'TEMP_DIFFERENT_INODE_OR_UNSAFE',
                                    'QUARANTINED_DIRTY'),
                                   ('FINAL_CONFLICTING_OR_UNSAFE',
                                    'TEMP_ANY',
                                    'QUARANTINED_CONFLICT'),
                                   ('OBSERVATION_UNCERTAIN', 'TEMP_ANY', 'DURABILITY_UNCERTAIN')),
 'present_path_count_cap': 2048,
 'schema_version': 3,
 'single_file_hard_cap': 150994944,
 'stage_owned_final_path_count': 36,
 'stage_owned_temp_path_count': 36,
 'terminal_tombstone_cap_per_family': 4096,
 'transient_caps_by_output_kind': (('ARTIFACT', 1048576),
                                   ('PROCEDURAL_ASSIGNMENT_RECEIPT', 131072),
                                   ('CHAIN_ACCEPTANCE_RECEIPT', 262144)),
 'uncertainty_precedence': ('DESCRIPTOR_CLOSE_UNCERTAIN',
                            'ROOT_DIRECTORY_FSYNC_UNCERTAIN',
                            'NAMESPACE_IDENTITY_UNCERTAIN',
                            'PAYLOAD_DURABILITY_UNCERTAIN',
                            'SEMANTIC_INVALID')})



def task9_evidence_capture_policy_sha256_v2() -> str:
    return _task9_domain_sha256_v1(
        "INCI-TASK-9-EVIDENCE-CAPTURE-POLICY-V1",
        _TASK9_CAPTURE_POLICY_PROJECTION_V2,
    )


_TASK9_CAPTURE_POLICY_CANONICAL_BYTES_V3 = 2_571
_TASK9_CAPTURE_POLICY_RAW_SHA256_V3 = (
    "c65bd68e6288956e4103ed0a304afd11b11eeb86fa87523b506001a41efaa40e"
)
_TASK9_CAPTURE_POLICY_SHA256_V3 = (
    "7f5b1dd6828ee429435507c7321fa49c20a432692e8fa1394dbb8c4ea358f356"
)


def task9_evidence_capture_policy_sha256_v3() -> str:
    canonical = _canonical_json_bytes(dict(TASK9_EVIDENCE_CAPTURE_POLICY_V3))
    if (
        len(canonical) != _TASK9_CAPTURE_POLICY_CANONICAL_BYTES_V3
        or hashlib.sha256(canonical).hexdigest()
        != _TASK9_CAPTURE_POLICY_RAW_SHA256_V3
    ):
        raise _invalid()
    digest = hashlib.sha256(
        b"INCI-TASK-9-EVIDENCE-CAPTURE-POLICY-V1\0" + canonical
    ).hexdigest()
    if digest != _TASK9_CAPTURE_POLICY_SHA256_V3:
        raise _invalid()
    return digest


TASK9_COMMAND_CANONICAL_BYTE_CAP_V1: Final[int] = 4_194_304
TASK9_BUNDLE_NONCOMMAND_CANONICAL_BYTE_CAP_V1: Final[int] = 8_388_608
TASK9_EVIDENCE_BUNDLE_DECODER_CAP_V1: Final[int] = 150_994_944
TASK9_EVIDENCE_AGGREGATE_RETAINED_BYTE_CAP_V3: Final[int] = 843_055_104
TASK9_BUNDLE_COMMAND_OCCURRENCE_CARDINALITY_V1: Final[
    tuple[tuple[Task9EvidenceBundleIdV1, int], ...]
] = (
    (Task9EvidenceBundleIdV1.PREDECESSOR, 34),
    (Task9EvidenceBundleIdV1.FUNCTIONAL_A, 26),
    (Task9EvidenceBundleIdV1.FUNCTIONAL_B, 8),
    (Task9EvidenceBundleIdV1.FUNCTIONAL_C, 8),
    (Task9EvidenceBundleIdV1.FUNCTIONAL_D, 8),
    (Task9EvidenceBundleIdV1.FUNCTIONAL_E, 8),
    (Task9EvidenceBundleIdV1.FUNCTIONAL_R, 8),
    (Task9EvidenceBundleIdV1.FINAL_RESEAL, 10),
    (Task9EvidenceBundleIdV1.RELEASE_SUPPORT, 9),
)
TASK9_BUNDLE_SEMANTIC_MAXIMA_V1: Final[
    tuple[tuple[Task9EvidenceBundleIdV1, int], ...]
] = (
    (Task9EvidenceBundleIdV1.PREDECESSOR, 150_994_944),
    (Task9EvidenceBundleIdV1.FUNCTIONAL_A, 117_440_512),
    (Task9EvidenceBundleIdV1.FUNCTIONAL_B, 41_943_040),
    (Task9EvidenceBundleIdV1.FUNCTIONAL_C, 41_943_040),
    (Task9EvidenceBundleIdV1.FUNCTIONAL_D, 41_943_040),
    (Task9EvidenceBundleIdV1.FUNCTIONAL_E, 41_943_040),
    (Task9EvidenceBundleIdV1.FUNCTIONAL_R, 41_943_040),
    (Task9EvidenceBundleIdV1.FINAL_RESEAL, 50_331_648),
    (Task9EvidenceBundleIdV1.RELEASE_SUPPORT, 46_137_344),
)


def _task9_require_active_capture_contract_v3(
    capture_policy_sha256: object, decoder_table_sha256: object,
) -> None:
    if (
        capture_policy_sha256 != task9_evidence_capture_policy_sha256_v3()
        or decoder_table_sha256 != task9_evidence_decoder_table_sha256_v3()
    ):
        raise Task9TransitionEvidenceError("task9_evidence_structure_invalid")


def _task9_enforce_command_canonical_byte_count_v1(byte_count: object) -> None:
    if (
        type(byte_count) is not int or byte_count < 0
        or byte_count > TASK9_COMMAND_CANONICAL_BYTE_CAP_V1
    ):
        raise Task9TransitionEvidenceError(
            "task9_evidence_canonical_size_invalid"
        )


def _task9_count_command_serialization_positions_v1(value: object) -> int:
    command_type = globals().get("Task9CommandEvidenceV1")
    if isinstance(command_type, type) and type(value) is command_type:
        return 1
    if is_dataclass(value) and not isinstance(value, type):
        return sum(
            _task9_count_command_serialization_positions_v1(
                getattr(value, field.name)
            )
            for field in fields(value)
        )
    if type(value) in (tuple, list):
        return sum(
            _task9_count_command_serialization_positions_v1(item)
            for item in value
        )
    if type(value) is dict:
        return sum(
            _task9_count_command_serialization_positions_v1(item)
            for item in value.values()
        )
    return 0


def _task9_capacity_row_v1(
    rows: tuple[tuple[Task9EvidenceBundleIdV1, int], ...],
    evidence_id: object,
) -> int:
    if type(evidence_id) is Task9EvidenceBundleIdV1:
        normalized = evidence_id
    elif type(evidence_id) is str:
        try:
            normalized = Task9EvidenceBundleIdV1(evidence_id)
        except ValueError:
            raise Task9TransitionEvidenceError(
                "task9_evidence_canonical_size_invalid"
            ) from None
    else:
        raise Task9TransitionEvidenceError(
            "task9_evidence_canonical_size_invalid"
        )
    for candidate, value in rows:
        if candidate is normalized:
            return value
    raise Task9TransitionEvidenceError("task9_evidence_canonical_size_invalid")


def _task9_enforce_evidence_bundle_capacity_v1(
    *, evidence_id: Task9EvidenceBundleIdV1, occurrence_count: int,
    command_byte_counts: tuple[int, ...], command_elided_byte_count: int,
    bundle_byte_count: int,
) -> None:
    expected_occurrences = _task9_capacity_row_v1(
        TASK9_BUNDLE_COMMAND_OCCURRENCE_CARDINALITY_V1, evidence_id
    )
    semantic_maximum = _task9_capacity_row_v1(
        TASK9_BUNDLE_SEMANTIC_MAXIMA_V1, evidence_id
    )
    if (
        type(occurrence_count) is not int
        or type(command_byte_counts) is not tuple
        or len(command_byte_counts) != expected_occurrences
        or occurrence_count != expected_occurrences
        or any(type(value) is not int for value in command_byte_counts)
        or type(command_elided_byte_count) is not int
        or type(bundle_byte_count) is not int
    ):
        raise Task9TransitionEvidenceError(
            "task9_evidence_canonical_size_invalid"
        )
    for value in command_byte_counts:
        _task9_enforce_command_canonical_byte_count_v1(value)
    if (
        command_elided_byte_count < 0
        or command_elided_byte_count
        > TASK9_BUNDLE_NONCOMMAND_CANONICAL_BYTE_CAP_V1
        or bundle_byte_count < 0
        or bundle_byte_count > semantic_maximum
        or bundle_byte_count > TASK9_EVIDENCE_BUNDLE_DECODER_CAP_V1
    ):
        raise Task9TransitionEvidenceError(
            "task9_evidence_canonical_size_invalid"
        )


def _task9_enforce_prospective_capture_aggregate_v3(
    retained_bytes: object, new_bytes: object,
) -> None:
    if (
        type(retained_bytes) is not int or retained_bytes < 0
        or type(new_bytes) is not int or new_bytes < 0
        or retained_bytes + new_bytes
        > TASK9_EVIDENCE_AGGREGATE_RETAINED_BYTE_CAP_V3
    ):
        raise Task9TransitionEvidenceError("task9_evidence_capture_cap_exceeded")


def _task9_capture_preallocation_guard_v3(
    *, row: Task9DecoderRowV2, observed_size: int, retained_bytes: int,
) -> None:
    if (
        not any(row is candidate for candidate in TASK9_EVIDENCE_DECODER_TABLE_V3)
        or type(observed_size) is not int or observed_size < 0
        or observed_size > row[3]
        or observed_size > 150_994_944
    ):
        raise Task9TransitionEvidenceError("task9_evidence_capture_cap_exceeded")
    _task9_enforce_prospective_capture_aggregate_v3(
        retained_bytes, observed_size
    )


def _task9_enforce_bundle_write_capacity_v3(
    *, evidence_id: Task9EvidenceBundleIdV1, canonical_bytes: int,
    retained_bytes: int,
) -> None:
    maximum = _task9_capacity_row_v1(
        TASK9_BUNDLE_SEMANTIC_MAXIMA_V1, evidence_id
    )
    if (
        type(canonical_bytes) is not int or canonical_bytes < 0
        or canonical_bytes > maximum
    ):
        raise Task9TransitionEvidenceError(
            "task9_evidence_canonical_size_invalid"
        )
    _task9_enforce_prospective_capture_aggregate_v3(
        retained_bytes, canonical_bytes
    )


def _task9_contract_for_stage(stage_id: str) -> Task9ChainStageContractV1:
    for contract in TASK9_CHAIN_STAGE_CONTRACTS_V1:
        if contract.stage_id == stage_id:
            return contract
    raise _invalid()


def _task9_assignment_writer_projection_sha256_v2(stage_id: str) -> str:
    contract = _task9_contract_for_stage(stage_id)
    projection = {
        "schema_version": 2,
        "writer_id": "TASK9_CANONICAL_ARTIFACT_PAIR_WRITER_V2",
        "writer_version": 2,
        "stage_contract_sha256": contract.stage_contract_sha256,
        "artifact_final_path": contract.A[0],
        "artifact_temp_path": contract.A[1],
        "assignment_receipt_final_path": contract.W[0],
        "assignment_receipt_temp_path": contract.W[1],
        "canonical_json_algorithm": "UTF8_SORTED_KEYS_NO_INSIGNIFICANT_WHITESPACE_V1",
        "artifact_cap": 1048576,
        "assignment_receipt_cap": 131072,
        "promotion_policy_sha256": TASK9_NO_REPLACE_PROMOTION_POLICY_V2.policy_sha256,
        "initial_step_ids": (
            "VALIDATE_EXACT_ARTIFACT_ASSIGNMENT_RESERVATION_AUTHORITY",
            "WRITE_PROMOTE_ARTIFACT",
            "TERMINALIZE_ARTIFACT_BOUNDARY",
            "BUILD_ASSIGNMENT_RECEIPT",
            "WRITE_PROMOTE_ASSIGNMENT_RECEIPT",
            "TERMINALIZE_PAIR",
        ),
        "recovery_step_ids": (
            "RECONCILE_NAMESPACE",
            "READ_VALIDATE_EXISTING_ARTIFACT",
            "CONSUME_NEW_RECOVERY_ATTESTATION",
            "ABSENT_WRITE_OR_IDENTICAL_RETURN_OR_CONFLICT",
            "TERMINALIZE_PAIR",
        ),
    }
    return _task9_domain_sha256_v1(
        "INCI-TASK-9-PROCEDURAL-ASSIGNMENT-WRITER-PROJECTION-V2",
        projection,
    )


def _task9_chain_validator_projection_sha256_v2(stage_id: str) -> str:
    contract = _task9_contract_for_stage(stage_id)
    projection = {
        "schema_version": 2,
        "validator_id": "TASK9_SNAPSHOT_CHAIN_VALIDATOR_V2",
        "validator_version": 2,
        "stage_contract_sha256": contract.stage_contract_sha256,
        "stage_contract_table_sha256": TASK9_CHAIN_STAGE_CONTRACT_TABLE_V1.table_sha256,
        "capture_policy_sha256": task9_evidence_capture_policy_sha256_v3(),
        "decoder_table_sha256": task9_evidence_decoder_table_sha256_v3(),
        "promotion_policy_sha256": TASK9_NO_REPLACE_PROMOTION_POLICY_V2.policy_sha256,
        "validation_step_ids": (
            "EXACT_INPUT_TYPES", "SNAPSHOT_ACTIVE_OWNER", "TRANSIENT_TREE_CLEAN",
            "STAGE_CONTRACT_SELF", "ARTIFACT_PARSE", "ASSIGNMENT_RECEIPT_PARSE",
            "ARTIFACT_ASSIGNMENT_PARITY", "TYPED_SEMANTIC_ROWS", "RAW_CONTENT_ROWS",
            "SEAL_ROWS", "ANTECEDENT_ROWS", "OWNER_REVIEWER_SEPARATION",
            "DISPOSITION_RELEASE_RULES", "PENDING_RECEIPT_PROJECTION",
            "SNAPSHOT_CLOSE_CERTAIN", "NO_REPLACE_RECEIPT_WRITE",
        ),
        "receipt_write_step_ids": (
            "OPEN_TEMP_EXCLUSIVE", "WRITE_ALL", "VERIFY_TEMP", "FSYNC_TEMP",
            "NATIVE_NOREPLACE_OR_PROVEN_LINKAT_FALLBACK", "VERIFY_FINAL_TEMP_STATE",
            "FSYNC_ROOT_DIRECTORY", "TERMINALIZE_AUTHORITY",
        ),
    }
    return _task9_domain_sha256_v1(
        "INCI-TASK-9-CHAIN-VALIDATOR-PROJECTION-V2", projection
    )


_TASK9_AUTHORITY_TOKEN = object()
_TASK9_EVIDENCE_LOCK = _threading.RLock()
_TASK9_ROOT_AUTHORITY_LEDGER: dict[int, dict[str, object]] = {}
_TASK9_ROOT_SNAPSHOT_LEDGER: dict[int, dict[str, object]] = {}
_TASK9_CLOSED_SNAPSHOT_TOMBSTONES: dict[
    int, _weakref.ReferenceType[object]
] = {}
_TASK9_SNAPSHOT_COORDINATE = 0


def _classify_task9_weak_identity_v1(
    entries: dict[int, _weakref.ReferenceType[object]],
    *,
    numeric_key: int,
    candidate: object,
) -> str:
    if type(entries) is not dict or type(numeric_key) is not int:
        raise Task9TransitionEvidenceError("task9_evidence_weak_identity_collision")
    stored_ref = entries.get(numeric_key)
    if stored_ref is None:
        return "NEW"
    if not isinstance(stored_ref, _weakref.ReferenceType):
        raise Task9TransitionEvidenceError("task9_evidence_weak_identity_collision")
    stored = stored_ref()
    if stored is None:
        entries.pop(numeric_key, None)
        return "NEW"
    if stored is candidate:
        return "TERMINAL_REPEAT"
    raise Task9TransitionEvidenceError("task9_evidence_weak_identity_collision")


def _task9_get_live_record_v1(
    ledger: dict[int, dict[str, object]], candidate: object
) -> dict[str, object] | None:
    record = ledger.get(id(candidate))
    if record is None:
        return None
    stored_ref = record.get("ref")
    if not isinstance(stored_ref, _weakref.ReferenceType):
        raise Task9TransitionEvidenceError("task9_evidence_weak_identity_collision")
    stored = stored_ref()
    if stored is None:
        ledger.pop(id(candidate), None)
        return None
    if stored is not candidate:
        raise Task9TransitionEvidenceError("task9_evidence_weak_identity_collision")
    return record


def _task9_admit_live_record_v1(
    ledger: dict[int, dict[str, object]],
    candidate: object,
    record: dict[str, object],
) -> None:
    dead_keys = tuple(
        key
        for key, existing in ledger.items()
        if isinstance(existing.get("ref"), _weakref.ReferenceType)
        and existing["ref"]() is None
    )
    for key in dead_keys:
        ledger.pop(key, None)
    existing = _task9_get_live_record_v1(ledger, candidate)
    if existing is not None:
        raise Task9TransitionEvidenceError("task9_evidence_weak_identity_collision")
    if len(ledger) >= 4_096:
        raise Task9TransitionEvidenceError(
            "task9_evidence_live_capability_cap_exceeded"
        )
    ledger[id(candidate)] = record


class Task9EvidenceRootAuthorityV1(_Task9ExactValue):
    __slots__ = ("__weakref__",)

    def __new__(cls, token: object = None) -> Task9EvidenceRootAuthorityV1:
        if token is not _TASK9_AUTHORITY_TOKEN:
            raise TypeError("task9_evidence_snapshot_invalid")
        return super().__new__(cls)


def _task9_root_identity(stat_result: _os.stat_result) -> tuple[int, ...]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_mode,
        stat_result.st_uid,
        stat_result.st_nlink,
    )


def _task9_open_relative_nofollow(root_fd: int, relative_path: str) -> int:
    components = relative_path.split("/")
    current_fd = _os.dup(root_fd)
    try:
        for component in components[:-1]:
            next_fd = _os.open(
                component,
                _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
                dir_fd=current_fd,
            )
            _os.close(current_fd)
            current_fd = next_fd
        result_fd = _os.open(
            components[-1],
            _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
            dir_fd=current_fd,
        )
        return result_fd
    finally:
        _os.close(current_fd)


def _task9_identity_fields(stat_result: _os.stat_result) -> tuple[int, ...]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_mode,
        stat_result.st_uid,
        stat_result.st_nlink,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def _task9_absent_path_snapshot(relative_path: str) -> Task9EvidencePathSnapshotV1:
    projection = {
        "schema_version": 1,
        "relative_path": relative_path,
        "state": "ABSENT",
        "device": None,
        "inode": None,
        "mode": None,
        "owner": None,
        "links": None,
        "size": None,
        "mtime_ns": None,
        "ctime_ns": None,
        "content_sha256": None,
    }
    return Task9EvidencePathSnapshotV1(
        **projection,
        path_snapshot_sha256=_task9_domain_sha256_v1(
            "INCI-TASK-9-EVIDENCE-PATH-SNAPSHOT-V1", projection
        ),
    )


def _task9_present_path_snapshot(
    relative_path: str, stat_result: _os.stat_result, content: bytes
) -> Task9EvidencePathSnapshotV1:
    projection = {
        "schema_version": 1,
        "relative_path": relative_path,
        "state": "PRESENT",
        "device": stat_result.st_dev,
        "inode": stat_result.st_ino,
        "mode": stat_result.st_mode,
        "owner": stat_result.st_uid,
        "links": stat_result.st_nlink,
        "size": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
        "ctime_ns": stat_result.st_ctime_ns,
        "content_sha256": hashlib.sha256(content).hexdigest(),
    }
    return Task9EvidencePathSnapshotV1(
        **projection,
        path_snapshot_sha256=_task9_domain_sha256_v1(
            "INCI-TASK-9-EVIDENCE-PATH-SNAPSHOT-V1", projection
        ),
    )


def _task9_capture_path(
    root_fd: int, row: Task9DecoderRowV2, remaining: int
) -> tuple[Task9EvidencePathSnapshotV1, bytes | None]:
    relative_path, _, _, row_cap, _, _, _ = row
    try:
        path_fd = _task9_open_relative_nofollow(root_fd, relative_path)
    except FileNotFoundError:
        return _task9_absent_path_snapshot(relative_path), None
    except OSError:
        raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid") from None
    try:
        before = _os.fstat(path_fd)
        if (
            not _stat.S_ISREG(before.st_mode)
            or before.st_uid != _os.geteuid()
            or before.st_nlink != 1
        ):
            raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid")
        if before.st_size > remaining:
            raise Task9TransitionEvidenceError("task9_evidence_capture_cap_exceeded")
        _task9_capture_preallocation_guard_v3(
            row=row,
            observed_size=before.st_size,
            retained_bytes=(
                TASK9_EVIDENCE_AGGREGATE_RETAINED_BYTE_CAP_V3 - remaining
            ),
        )
        chunks: list[bytes] = []
        bytes_left = before.st_size
        while bytes_left:
            chunk = _os.read(path_fd, min(bytes_left, 1_048_576))
            if not chunk:
                raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid")
            chunks.append(chunk)
            bytes_left -= len(chunk)
        content = b"".join(chunks)
        after = _os.fstat(path_fd)
        if len(content) != before.st_size or _task9_identity_fields(before) != _task9_identity_fields(after):
            raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid")
        return _task9_present_path_snapshot(relative_path, after, content), content
    finally:
        _os.close(path_fd)


def _task9_make_tree_snapshot(
    tree_id: str,
    paths: tuple[Task9EvidencePathSnapshotV1, ...],
) -> Task9EvidenceTreeSnapshotV1:
    facts = tuple(
        (item.relative_path, item.state, item.content_sha256) for item in paths
    )
    tree_sha256 = _task9_domain_sha256_v1(
        "INCI-TASK-9-TRANSIENT-WRITE-TREE-V1", facts
    )
    projection = {
        "schema_version": 1,
        "tree_id": tree_id,
        "path_snapshot_sha256s": tuple(
            item.path_snapshot_sha256 for item in paths
        ),
        "tree_sha256": tree_sha256,
    }
    return Task9EvidenceTreeSnapshotV1(
        **projection,
        snapshot_sha256=_task9_domain_sha256_v1(
            "INCI-TASK-9-EVIDENCE-TREE-SNAPSHOT-V1", projection
        ),
    )


def issue_task9_evidence_root_snapshot_v1(
    authority: Task9EvidenceRootAuthorityV1,
) -> EvidenceRootSnapshotV1:
    _task9_platform_gate_v1()
    if type(authority) is not Task9EvidenceRootAuthorityV1:
        raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid")
    with _TASK9_EVIDENCE_LOCK:
        record = _task9_get_live_record_v1(
            _TASK9_ROOT_AUTHORITY_LEDGER, authority
        )
        if (
            record is None
            or record["state"] != "FRESH"
            or record["pid"] != _os.getpid()
            or record["thread"] != _threading.get_ident()
        ):
            raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid")
        record["state"] = "CONSUMING"
    root_fd = -1
    try:
        root_fd = _os.open(
            record["root_path"],
            _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
        )
        root_stat = _os.fstat(root_fd)
        if _task9_root_identity(root_stat) != record["root_identity"]:
            raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid")
        path_snapshots: list[Task9EvidencePathSnapshotV1] = []
        retained_bytes: dict[str, bytes] = {}
        captured_total = 0
        for row in TASK9_EVIDENCE_DECODER_TABLE_V3:
            path_snapshot, content = _task9_capture_path(
                root_fd,
                row,
                TASK9_EVIDENCE_AGGREGATE_RETAINED_BYTE_CAP_V3 - captured_total,
            )
            validate_task9_evidence_path_snapshot_structure_v1(path_snapshot)
            path_snapshots.append(path_snapshot)
            if content is not None:
                retained_bytes[path_snapshot.relative_path] = content
                captured_total += len(content)
        by_path = {item.relative_path: item for item in path_snapshots}
        transient_paths = tuple(
            by_path[path] for path in TASK9_TRANSIENT_WRITE_TREE_V1
        )
        transient_tree = _task9_make_tree_snapshot(
            "TASK9_TRANSIENT_WRITE_TREE_V1", transient_paths
        )
        validate_task9_evidence_tree_snapshot_structure_v1(transient_tree)
        present_temp_count = sum(item.state == "PRESENT" for item in transient_paths)
        root_identity_projection = {
            "device": root_stat.st_dev,
            "inode": root_stat.st_ino,
            "mode": root_stat.st_mode,
            "owner": root_stat.st_uid,
            "links": root_stat.st_nlink,
        }
        root_identity_sha256 = _task9_domain_sha256_v1(
            "INCI-TASK-9-EVIDENCE-ROOT-IDENTITY-V1",
            root_identity_projection,
        )
        global _TASK9_SNAPSHOT_COORDINATE
        with _TASK9_EVIDENCE_LOCK:
            if _TASK9_SNAPSHOT_COORDINATE >= 9_223_372_036_854_775_807:
                raise Task9TransitionEvidenceError(
                    "task9_evidence_live_capability_cap_exceeded"
                )
            _TASK9_SNAPSHOT_COORDINATE += 1
            coordinate = _TASK9_SNAPSHOT_COORDINATE
        projection = {
            "schema_version": 1,
            "snapshot_allocation_coordinate": coordinate,
            "evidence_root_identity_sha256": root_identity_sha256,
            "closed_path_count": 147,
            "present_path_count": len(retained_bytes),
            "captured_bytes_total": captured_total,
            "capture_policy_sha256": task9_evidence_capture_policy_sha256_v3(),
            "decoder_table_sha256": task9_evidence_decoder_table_sha256_v3(),
            "closed_temp_path_count": 36,
            "present_temp_path_count": present_temp_count,
            "transient_write_tree_snapshot_sha256": transient_tree.snapshot_sha256,
            "transient_state": "CLEAN" if present_temp_count == 0 else "DIRTY",
            "path_snapshot_sha256s": tuple(
                item.path_snapshot_sha256 for item in path_snapshots
            ),
            "tree_snapshot_sha256s": (transient_tree.snapshot_sha256,),
        }
        snapshot = EvidenceRootSnapshotV1(
            **projection,
            snapshot_sha256=_task9_domain_sha256_v1(
                "INCI-TASK-9-EVIDENCE-ROOT-SNAPSHOT-V1", projection
            ),
        )
        validate_task9_evidence_root_snapshot_structure_v1(snapshot)
        snapshot_record = {
            "ref": _weakref.ref(snapshot),
            "pid": _os.getpid(),
            "thread": _threading.get_ident(),
            "state": "ACTIVE",
            "retained_bytes": retained_bytes,
            "path_by_path": by_path,
            "trees": {transient_tree.tree_id: transient_tree},
            "decode_cache": {},
            "decode_failure": None,
        }
        with _TASK9_EVIDENCE_LOCK:
            _task9_admit_live_record_v1(
                _TASK9_ROOT_SNAPSHOT_LEDGER, snapshot, snapshot_record
            )
            record["state"] = "CONSUMED"
            _TASK9_ROOT_AUTHORITY_LEDGER.pop(id(authority), None)
        return snapshot
    except Task9TransitionEvidenceError:
        with _TASK9_EVIDENCE_LOCK:
            record["state"] = "CONSUMED_FAILED"
            _TASK9_ROOT_AUTHORITY_LEDGER.pop(id(authority), None)
        raise
    except Exception:
        with _TASK9_EVIDENCE_LOCK:
            record["state"] = "CONSUMED_FAILED"
            _TASK9_ROOT_AUTHORITY_LEDGER.pop(id(authority), None)
        raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid") from None
    finally:
        if root_fd >= 0:
            _os.close(root_fd)


def _task9_resolve_snapshot(snapshot: object) -> dict[str, object]:
    if type(snapshot) is not EvidenceRootSnapshotV1:
        raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid")
    with _TASK9_EVIDENCE_LOCK:
        record = _task9_get_live_record_v1(
            _TASK9_ROOT_SNAPSHOT_LEDGER, snapshot
        )
        if record is None:
            disposition = _classify_task9_weak_identity_v1(
                _TASK9_CLOSED_SNAPSHOT_TOMBSTONES,
                numeric_key=id(snapshot),
                candidate=snapshot,
            )
            if disposition == "TERMINAL_REPEAT":
                raise Task9TransitionEvidenceError("task9_evidence_snapshot_closed")
            raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid")
        if (
            record["pid"] != _os.getpid()
            or record["thread"] != _threading.get_ident()
            or record["state"] not in ("ACTIVE", "DECODE_FAILED")
        ):
            raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid")
        return record


def close_task9_evidence_root_snapshot_v1(snapshot: EvidenceRootSnapshotV1) -> None:
    if type(snapshot) is not EvidenceRootSnapshotV1:
        raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid")
    with _TASK9_EVIDENCE_LOCK:
        record = _task9_get_live_record_v1(
            _TASK9_ROOT_SNAPSHOT_LEDGER, snapshot
        )
        if record is None:
            disposition = _classify_task9_weak_identity_v1(
                _TASK9_CLOSED_SNAPSHOT_TOMBSTONES,
                numeric_key=id(snapshot),
                candidate=snapshot,
            )
            if disposition == "TERMINAL_REPEAT":
                return None
            raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid")
        if record["pid"] != _os.getpid() or record["thread"] != _threading.get_ident():
            raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid")
        if record["state"] == "CLOSE_UNCERTAIN":
            raise Task9TransitionEvidenceError(
                "task9_evidence_snapshot_cleanup_uncertain"
            )
        if record["state"] not in ("ACTIVE", "DECODE_FAILED"):
            raise Task9TransitionEvidenceError("task9_evidence_snapshot_invalid")
        dead_keys = tuple(
            key
            for key, stored_ref in _TASK9_CLOSED_SNAPSHOT_TOMBSTONES.items()
            if stored_ref() is None
        )
        for key in dead_keys:
            _TASK9_CLOSED_SNAPSHOT_TOMBSTONES.pop(key, None)
        if len(_TASK9_CLOSED_SNAPSHOT_TOMBSTONES) >= TASK9_TERMINAL_TOMBSTONE_CAP_PER_FAMILY_V1:
            record["state"] = "CLOSE_UNCERTAIN"
            raise Task9TransitionEvidenceError(
                "task9_evidence_snapshot_cleanup_uncertain"
            )
        record["state"] = "CLOSING"
        record["decode_cache"].clear()
        record["retained_bytes"].clear()
        record["path_by_path"].clear()
        record["trees"].clear()
        record["decode_failure"] = None
        _TASK9_ROOT_SNAPSHOT_LEDGER.pop(id(snapshot), None)
        _TASK9_CLOSED_SNAPSHOT_TOMBSTONES[id(snapshot)] = _weakref.ref(snapshot)
    return None


def _task9_read_snapshot_decoded(
    snapshot: EvidenceRootSnapshotV1,
    *,
    relative_path: str,
    decoder_id: str,
    parser: object,
) -> object:
    record = _task9_resolve_snapshot(snapshot)
    if record["state"] == "DECODE_FAILED":
        raise Task9TransitionEvidenceError("task9_evidence_decode_invalid")
    path_snapshot = record["path_by_path"].get(relative_path)
    content = record["retained_bytes"].get(relative_path)
    if (
        type(path_snapshot) is not Task9EvidencePathSnapshotV1
        or path_snapshot.state != "PRESENT"
        or type(content) is not bytes
        or hashlib.sha256(content).hexdigest() != path_snapshot.content_sha256
    ):
        record["state"] = "DECODE_FAILED"
        record["decode_failure"] = "task9_evidence_decode_invalid"
        raise Task9TransitionEvidenceError("task9_evidence_decode_invalid")
    key = (relative_path, decoder_id, path_snapshot.content_sha256)
    cached = record["decode_cache"].get(key)
    if cached is not None:
        return cached
    try:
        decoded = parser(content)
    except Exception:
        record["state"] = "DECODE_FAILED"
        record["decode_failure"] = "task9_evidence_decode_invalid"
        raise Task9TransitionEvidenceError("task9_evidence_decode_invalid") from None
    record["decode_cache"][key] = decoded
    return decoded


def read_task9_predecessor_transition_manifest_from_snapshot_v1(
    snapshot: EvidenceRootSnapshotV1,
) -> Task9PredecessorTransitionManifestV1:
    return _task9_read_snapshot_decoded(
        snapshot,
        relative_path="task-9-predecessor-transition-manifest-v1.json",
        decoder_id="TASK9_PREDECESSOR_TRANSITION_MANIFEST_V1",
        parser=parse_task9_predecessor_transition_manifest_v1,
    )


def read_task9_predecessor_transition_review_from_snapshot_v1(
    snapshot: EvidenceRootSnapshotV1,
) -> Task9PredecessorTransitionReviewV1:
    return _task9_read_snapshot_decoded(
        snapshot,
        relative_path="task-9-predecessor-transition-review-v1.json",
        decoder_id="TASK9_PREDECESSOR_TRANSITION_REVIEW_V1",
        parser=parse_task9_predecessor_transition_review_v1,
    )


def read_task9_post_predecessor_amended_package_rereview_from_snapshot_v1(
    snapshot: EvidenceRootSnapshotV1,
) -> Task9PostPredecessorAmendedPackageRereviewV1:
    return _task9_read_snapshot_decoded(
        snapshot,
        relative_path="task-9-post-predecessor-amended-package-rereview-v1.json",
        decoder_id="TASK9_POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW_V1",
        parser=parse_task9_post_predecessor_amended_package_rereview_v1,
    )


_TASK9_FUNCTIONAL_REVIEW_PATHS: Final[dict[Task9FunctionalWaveIdV1, str]] = {
    Task9FunctionalWaveIdV1.A: "task-9-functional-wave-review-a-v1.json",
    Task9FunctionalWaveIdV1.B: "task-9-functional-wave-review-b-v1.json",
    Task9FunctionalWaveIdV1.C: "task-9-functional-wave-review-c-v1.json",
    Task9FunctionalWaveIdV1.D: "task-9-functional-wave-review-d-v1.json",
    Task9FunctionalWaveIdV1.E: "task-9-functional-wave-review-e-v1.json",
    Task9FunctionalWaveIdV1.R: "task-9-functional-wave-review-r-v1.json",
}


def read_task9_functional_wave_review_from_snapshot_v1(
    snapshot: EvidenceRootSnapshotV1, *, wave_id: Task9FunctionalWaveIdV1
) -> Task9FunctionalWaveReviewV1:
    if type(wave_id) is not Task9FunctionalWaveIdV1:
        raise Task9TransitionEvidenceError("task9_evidence_decode_invalid")
    result = _task9_read_snapshot_decoded(
        snapshot,
        relative_path=_TASK9_FUNCTIONAL_REVIEW_PATHS[wave_id],
        decoder_id="TASK9_FUNCTIONAL_WAVE_REVIEW_V1",
        parser=parse_task9_functional_wave_review_v1,
    )
    if result.wave_id != wave_id.value:
        raise Task9TransitionEvidenceError("task9_evidence_decode_invalid")
    return result


def read_task9_final_reseal_transition_from_snapshot_v1(
    snapshot: EvidenceRootSnapshotV1,
) -> Task9FinalResealTransitionV1:
    return _task9_read_snapshot_decoded(
        snapshot,
        relative_path="task-9-final-reseal-transition-v1.json",
        decoder_id="TASK9_FINAL_RESEAL_TRANSITION_V1",
        parser=parse_task9_final_reseal_transition_v1,
    )


def read_task9_final_reseal_review_from_snapshot_v1(
    snapshot: EvidenceRootSnapshotV1,
) -> Task9FinalResealReviewV1:
    return _task9_read_snapshot_decoded(
        snapshot,
        relative_path="task-9-final-reseal-review-v1.json",
        decoder_id="TASK9_FINAL_RESEAL_REVIEW_V1",
        parser=parse_task9_final_reseal_review_v1,
    )


def read_task9_release_evidence_from_snapshot_v1(
    snapshot: EvidenceRootSnapshotV1,
) -> Task9ReleaseEvidenceV1:
    return _task9_read_snapshot_decoded(
        snapshot,
        relative_path="task-9-release-evidence-v1.json",
        decoder_id="TASK9_RELEASE_EVIDENCE_V1",
        parser=parse_task9_release_evidence_v1,
    )


def read_task9_procedural_assignment_write_receipt_from_snapshot_v1(
    snapshot: EvidenceRootSnapshotV1, *, stage_id: Task9EvidenceStageIdV1
) -> Task9ProceduralAssignmentWriteReceiptV1:
    if type(stage_id) is not Task9EvidenceStageIdV1:
        raise Task9TransitionEvidenceError("task9_evidence_decode_invalid")
    row = _task9_stage_row(stage_id.value)
    return _task9_read_snapshot_decoded(
        snapshot,
        relative_path=row[4],
        decoder_id="TASK9_PROCEDURAL_ASSIGNMENT_WRITE_RECEIPT_V1",
        parser=parse_task9_procedural_assignment_write_receipt_v1,
    )


def read_task9_chain_acceptance_receipt_from_snapshot_v1(
    snapshot: EvidenceRootSnapshotV1, *, stage_id: Task9EvidenceStageIdV1
) -> Task9ChainAcceptanceReceiptV1:
    if type(stage_id) is not Task9EvidenceStageIdV1:
        raise Task9TransitionEvidenceError("task9_evidence_decode_invalid")
    row = _task9_stage_row(stage_id.value)
    return _task9_read_snapshot_decoded(
        snapshot,
        relative_path=row[5],
        decoder_id="TASK9_CHAIN_ACCEPTANCE_RECEIPT_V1",
        parser=parse_task9_chain_acceptance_receipt_v1,
    )


class Task9ProceduralAttestationAuthorityV1(_Task9ExactValue):
    __slots__ = ("__weakref__",)

    def __new__(cls, token: object = None) -> Task9ProceduralAttestationAuthorityV1:
        if token is not _TASK9_AUTHORITY_TOKEN:
            raise TypeError("task9_procedural_assignment_invalid")
        return super().__new__(cls)


class Task9EvidencePairWriteAuthorityV1(_Task9ExactValue):
    __slots__ = ("__weakref__",)

    def __new__(cls, token: object = None) -> Task9EvidencePairWriteAuthorityV1:
        if token is not _TASK9_AUTHORITY_TOKEN:
            raise TypeError("task9_procedural_assignment_reservation_invalid")
        return super().__new__(cls)


class Task9ProceduralAssignmentReservationV1(_Task9ExactValue):
    __slots__ = ("__weakref__",)

    def __new__(cls, token: object = None) -> Task9ProceduralAssignmentReservationV1:
        if token is not _TASK9_AUTHORITY_TOKEN:
            raise TypeError("task9_procedural_assignment_reservation_invalid")
        return super().__new__(cls)


_TASK9_ATTESTATION_LEDGER: dict[int, dict[str, object]] = {}
_TASK9_ASSIGNMENT_ISSUANCE_LEDGER: dict[int, dict[str, object]] = {}
_TASK9_PAIR_AUTHORITY_LEDGER: dict[int, dict[str, object]] = {}
_TASK9_RESERVATION_LEDGER: dict[int, dict[str, object]] = {}
_TASK9_CONSUMED_RESERVATION_TOMBSTONES: dict[
    int, _weakref.ReferenceType[object]
] = {}


def _task9_terminalize_consumed_reservation_v1(
    reservation: Task9ProceduralAssignmentReservationV1,
) -> None:
    dead_keys = tuple(
        key
        for key, stored_ref in _TASK9_CONSUMED_RESERVATION_TOMBSTONES.items()
        if stored_ref() is None
    )
    for key in dead_keys:
        _TASK9_CONSUMED_RESERVATION_TOMBSTONES.pop(key, None)
    if len(_TASK9_CONSUMED_RESERVATION_TOMBSTONES) >= TASK9_TERMINAL_TOMBSTONE_CAP_PER_FAMILY_V1:
        raise Task9TransitionEvidenceError(
            "task9_evidence_live_capability_cap_exceeded"
        )
    _TASK9_CONSUMED_RESERVATION_TOMBSTONES[id(reservation)] = _weakref.ref(
        reservation
    )


def _issue_task9_procedural_attestation_authority_v1(
    *,
    assignment_scope: str,
    controller_operator_label: str,
    creator_controller_label: str | None,
    role_bindings: tuple[Task9ProceduralRoleBindingV1, ...],
    reviewer_label: str | None,
) -> Task9ProceduralAttestationAuthorityV1:
    if (
        assignment_scope not in _TASK9_ROLE_IDS_BY_SCOPE
        or not _task9_is_safe_id(controller_operator_label)
        or creator_controller_label is not None
        and not _task9_is_safe_id(creator_controller_label)
        or reviewer_label is not None
        and not _task9_is_safe_id(reviewer_label)
        or type(role_bindings) is not tuple
    ):
        raise Task9TransitionEvidenceError("task9_procedural_assignment_invalid")
    for binding in role_bindings:
        validate_task9_procedural_role_binding_structure_v1(binding)
    authority = Task9ProceduralAttestationAuthorityV1(_TASK9_AUTHORITY_TOKEN)
    authority_record = {
        "ref": _weakref.ref(authority),
        "pid": _os.getpid(),
        "thread": _threading.get_ident(),
        "state": "FRESH",
        "assignment_scope": assignment_scope,
        "controller_operator_label": controller_operator_label,
        "creator_controller_label": creator_controller_label,
        "role_bindings": role_bindings,
        "reviewer_label": reviewer_label,
    }
    with _TASK9_EVIDENCE_LOCK:
        _task9_admit_live_record_v1(
            _TASK9_ATTESTATION_LEDGER, authority, authority_record
        )
    return authority


def issue_task9_procedural_workflow_assignment_evidence_v1(
    authority: Task9ProceduralAttestationAuthorityV1,
    *,
    assignment_scope: Task9ProceduralAssignmentScopeV1,
    controller_operator_label: str,
    creator_controller_label: str | None,
    role_bindings: tuple[Task9ProceduralRoleBindingV1, ...],
    reviewer_label: str | None,
) -> Task9ProceduralWorkflowAssignmentEvidenceV1:
    if (
        type(authority) is not Task9ProceduralAttestationAuthorityV1
        or type(assignment_scope) is not Task9ProceduralAssignmentScopeV1
    ):
        raise Task9TransitionEvidenceError("task9_procedural_assignment_invalid")
    with _TASK9_EVIDENCE_LOCK:
        record = _task9_get_live_record_v1(_TASK9_ATTESTATION_LEDGER, authority)
        if (
            record is None
            or record["state"] != "FRESH"
            or record["pid"] != _os.getpid()
            or record["thread"] != _threading.get_ident()
        ):
            raise Task9TransitionEvidenceError("task9_procedural_assignment_invalid")
        record["state"] = "CONSUMING"
    try:
        if (
            record["assignment_scope"] != assignment_scope.value
            or record["controller_operator_label"] != controller_operator_label
            or record["creator_controller_label"] != creator_controller_label
            or record["role_bindings"] is not role_bindings
            or record["reviewer_label"] != reviewer_label
        ):
            raise Task9TransitionEvidenceError("task9_procedural_assignment_invalid")
        projection = {
            "schema_version": 1,
            "workflow_id": "TASK9",
            "assignment_scope": assignment_scope.value,
            "controller_operator_label": controller_operator_label,
            "creator_controller_label": creator_controller_label,
            "role_bindings": role_bindings,
            "role_binding_sha256s": tuple(
                binding.binding_sha256 for binding in role_bindings
            ),
            "reviewer_label": reviewer_label,
            "identity_assurance": "PROCEDURAL_LOCAL_ATTESTATION",
            "controller_operator_attested": True,
        }
        assignment = Task9ProceduralWorkflowAssignmentEvidenceV1(
            **projection,
            assignment_sha256=_task9_domain_sha256_v1(
                "INCI-TASK-9-PROCEDURAL-WORKFLOW-ASSIGNMENT-EVIDENCE-V1",
                _task9_public_projection(projection),
            ),
        )
        validate_task9_procedural_workflow_assignment_evidence_structure_v1(
            assignment
        )
        with _TASK9_EVIDENCE_LOCK:
            assignment_record = {
                "ref": _weakref.ref(assignment),
                "authority_id": id(authority),
                "pid": _os.getpid(),
                "thread": _threading.get_ident(),
                "state": "RESERVATION_FRESH",
            }
            _task9_admit_live_record_v1(
                _TASK9_ASSIGNMENT_ISSUANCE_LEDGER,
                assignment,
                assignment_record,
            )
            record["state"] = "CONSUMED"
            _TASK9_ATTESTATION_LEDGER.pop(id(authority), None)
        return assignment
    except Task9TransitionEvidenceError:
        with _TASK9_EVIDENCE_LOCK:
            record["state"] = "CONSUMED_FAILED"
            _TASK9_ATTESTATION_LEDGER.pop(id(authority), None)
        raise


def _issue_task9_evidence_pair_write_authority_v1(
    root_path: str,
    *,
    stage_id: Task9EvidenceStageIdV1,
    write_mode: str,
) -> Task9EvidencePairWriteAuthorityV1:
    _task9_platform_gate_v1()
    if (
        type(root_path) is not str
        or not _os.path.isabs(root_path)
        or type(stage_id) is not Task9EvidenceStageIdV1
        or write_mode not in ("INITIAL", "RECOVERY")
    ):
        raise Task9TransitionEvidenceError(
            "task9_procedural_assignment_reservation_invalid"
        )
    root_fd = -1
    try:
        root_fd = _os.open(
            root_path,
            _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
        )
        root_stat = _os.fstat(root_fd)
        if not _stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid != _os.geteuid():
            raise OSError
        root_identity = _task9_root_identity(root_stat)
    except Exception:
        raise Task9TransitionEvidenceError(
            "task9_procedural_assignment_reservation_invalid"
        ) from None
    finally:
        if root_fd >= 0:
            _os.close(root_fd)
    authority = Task9EvidencePairWriteAuthorityV1(_TASK9_AUTHORITY_TOKEN)
    authority_record = {
        "ref": _weakref.ref(authority),
        "root_path": root_path,
        "root_identity": root_identity,
        "stage_id": stage_id,
        "write_mode": write_mode,
        "pid": _os.getpid(),
        "thread": _threading.get_ident(),
        "state": "FRESH",
        "pending_payload": None,
    }
    with _TASK9_EVIDENCE_LOCK:
        _task9_admit_live_record_v1(
            _TASK9_PAIR_AUTHORITY_LEDGER, authority, authority_record
        )
    return authority


def _task9_stage_for_artifact(
    artifact: Task9CanonicalArtifactV1,
) -> Task9EvidenceStageIdV1:
    if type(artifact) is Task9PredecessorTransitionManifestV1:
        return Task9EvidenceStageIdV1.PREDECESSOR_TRANSITION_MANIFEST
    if type(artifact) is Task9PredecessorTransitionReviewV1:
        return Task9EvidenceStageIdV1.PREDECESSOR_TRANSITION_REVIEW
    if type(artifact) is Task9PostPredecessorAmendedPackageRereviewV1:
        return Task9EvidenceStageIdV1.POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW
    if type(artifact) is Task9FunctionalWaveReviewV1:
        stage_id = {
            "A": Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_A,
            "B": Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_B,
            "C": Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_C,
            "D": Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_D,
            "E": Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_E,
            "R": Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_R,
        }.get(artifact.wave_id)
        if stage_id is None:
            raise _invalid()
        return stage_id
    if type(artifact) is Task9FinalResealTransitionV1:
        return Task9EvidenceStageIdV1.FINAL_RESEAL_TRANSITION
    if type(artifact) is Task9FinalResealReviewV1:
        return Task9EvidenceStageIdV1.FINAL_RESEAL_REVIEW
    if type(artifact) is Task9ReleaseEvidenceV1:
        return Task9EvidenceStageIdV1.RELEASE_EVIDENCE
    raise _invalid()


def _task9_validate_artifact(value: object) -> Task9CanonicalArtifactV1:
    validators = {
        Task9PredecessorTransitionManifestV1: validate_task9_predecessor_transition_manifest_structure_v1,
        Task9PredecessorTransitionReviewV1: validate_task9_predecessor_transition_review_structure_v1,
        Task9PostPredecessorAmendedPackageRereviewV1: validate_task9_post_predecessor_amended_package_rereview_structure_v1,
        Task9FunctionalWaveReviewV1: validate_task9_functional_wave_review_structure_v1,
        Task9FinalResealTransitionV1: validate_task9_final_reseal_transition_structure_v1,
        Task9FinalResealReviewV1: validate_task9_final_reseal_review_structure_v1,
        Task9ReleaseEvidenceV1: validate_task9_release_evidence_structure_v1,
    }
    validator = validators.get(type(value))
    if validator is None:
        raise _invalid()
    return validator(value)


def issue_task9_procedural_assignment_reservation_v1(
    assignment_evidence: Task9ProceduralWorkflowAssignmentEvidenceV1,
    artifact: Task9CanonicalArtifactV1,
    *,
    write_authority: Task9EvidencePairWriteAuthorityV1,
) -> Task9ProceduralAssignmentReservationV1:
    if (
        type(write_authority) is not Task9EvidencePairWriteAuthorityV1
        or type(assignment_evidence) is not Task9ProceduralWorkflowAssignmentEvidenceV1
    ):
        raise Task9TransitionEvidenceError(
            "task9_procedural_assignment_reservation_invalid"
        )
    with _TASK9_EVIDENCE_LOCK:
        pair_record = _task9_get_live_record_v1(
            _TASK9_PAIR_AUTHORITY_LEDGER, write_authority
        )
        assignment_record = _task9_get_live_record_v1(
            _TASK9_ASSIGNMENT_ISSUANCE_LEDGER, assignment_evidence
        )
        if (
            pair_record is None
            or pair_record["state"] != "FRESH"
            or pair_record["write_mode"] != "INITIAL"
        ):
            raise Task9TransitionEvidenceError(
                "task9_procedural_assignment_reservation_invalid"
            )
        pair_record["state"] = "ISSUING"
        if (
            assignment_record is None
            or assignment_record["state"] != "RESERVATION_FRESH"
        ):
            pair_record["state"] = "CONSUMED_FAILED"
            _TASK9_PAIR_AUTHORITY_LEDGER.pop(id(write_authority), None)
            raise Task9TransitionEvidenceError(
                "task9_procedural_assignment_reservation_invalid"
            )
        assignment_record["state"] = "RESERVATION_CONSUMING"
    try:
        validated_artifact = _task9_validate_artifact(artifact)
        stage_id = _task9_stage_for_artifact(validated_artifact)
        if (
            validated_artifact.procedural_assignment_evidence
            is not assignment_evidence
            or pair_record["stage_id"] is not stage_id
            or assignment_evidence.assignment_scope != stage_id.value
            or pair_record["pid"] != _os.getpid()
            or pair_record["thread"] != _threading.get_ident()
        ):
            raise Task9TransitionEvidenceError(
                "task9_procedural_assignment_reservation_invalid"
            )
        artifact_bytes = _canonical_json_bytes(
            _task9_public_projection(validated_artifact)
        )
        if len(artifact_bytes) > 1_048_576:
            raise Task9TransitionEvidenceError(
                "task9_procedural_assignment_artifact_drift"
            )
        reservation = Task9ProceduralAssignmentReservationV1(
            _TASK9_AUTHORITY_TOKEN
        )
        reservation_record = {
            "ref": _weakref.ref(reservation),
            "assignment": assignment_evidence,
            "artifact": validated_artifact,
            "artifact_bytes": artifact_bytes,
            "pair": write_authority,
            "stage_id": stage_id,
            "write_mode": "INITIAL",
            "pid": _os.getpid(),
            "thread": _threading.get_ident(),
            "state": "FRESH",
        }
        with _TASK9_EVIDENCE_LOCK:
            _task9_admit_live_record_v1(
                _TASK9_RESERVATION_LEDGER, reservation, reservation_record
            )
            assignment_record["state"] = "RESERVATION_CONSUMED"
            pair_record["state"] = "RESERVED"
        return reservation
    except Task9TransitionEvidenceError:
        with _TASK9_EVIDENCE_LOCK:
            assignment_record["state"] = "RESERVATION_CONSUMED_FAILED"
            pair_record["state"] = "CONSUMED_FAILED"
            _TASK9_PAIR_AUTHORITY_LEDGER.pop(id(write_authority), None)
        raise


def _task9_output_paths(
    stage_id: Task9EvidenceStageIdV1,
    output_kind: Task9StageOutputKindV1,
) -> tuple[str, str, int]:
    row = _task9_stage_row(stage_id.value)
    if output_kind is Task9StageOutputKindV1.ARTIFACT:
        return row[3], row[6], 1_048_576
    if output_kind is Task9StageOutputKindV1.PROCEDURAL_ASSIGNMENT_RECEIPT:
        return row[4], row[7], 131_072
    if output_kind is Task9StageOutputKindV1.CHAIN_ACCEPTANCE_RECEIPT:
        return row[5], row[8], 262_144
    raise Task9TransitionEvidenceError("task9_evidence_temp_path_invalid")


def _task9_write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = _os.write(fd, payload[offset:])
        if written <= 0:
            raise OSError
        offset += written


def _task9_verify_promoted_payload_v1(
    root_fd: int,
    relative_path: str,
    payload: bytes,
    *,
    expected_device: int,
    expected_inode: int,
    expected_links: int,
    failure: str,
) -> _os.stat_result:
    verify_fd = -1
    try:
        verify_fd = _os.open(
            relative_path,
            _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
            dir_fd=root_fd,
        )
        before = _os.fstat(verify_fd)
        if (
            not _stat.S_ISREG(before.st_mode)
            or before.st_uid != _os.geteuid()
            or _stat.S_IMODE(before.st_mode) != 0o600
            or before.st_dev != expected_device
            or before.st_ino != expected_inode
            or before.st_nlink != expected_links
            or before.st_size != len(payload)
        ):
            raise Task9TransitionEvidenceError(failure)
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = _os.read(verify_fd, min(remaining, 1_048_576))
            if not chunk:
                raise Task9TransitionEvidenceError(failure)
            chunks.append(chunk)
            remaining -= len(chunk)
        if _os.read(verify_fd, 1) != b"":
            raise Task9TransitionEvidenceError(failure)
        after = _os.fstat(verify_fd)
        content = b"".join(chunks)
        if (
            _task9_identity_fields(before) != _task9_identity_fields(after)
            or hashlib.sha256(content).digest() != hashlib.sha256(payload).digest()
        ):
            raise Task9TransitionEvidenceError(failure)
        return after
    except Task9TransitionEvidenceError:
        raise
    except Exception:
        raise Task9TransitionEvidenceError(failure) from None
    finally:
        if verify_fd >= 0:
            try:
                _os.close(verify_fd)
            except Exception:
                raise Task9TransitionEvidenceError(failure) from None


def _write_and_promote_task9_stage_output_v1(
    authority: Task9EvidencePairWriteAuthorityV1,
    *,
    output_kind: Task9StageOutputKindV1,
    payload: bytes,
) -> None:
    if (
        type(authority) is not Task9EvidencePairWriteAuthorityV1
        or type(output_kind) is not Task9StageOutputKindV1
        or type(payload) is not bytes
    ):
        raise Task9TransitionEvidenceError("task9_evidence_promotion_uncertain")
    with _TASK9_EVIDENCE_LOCK:
        record = _task9_get_live_record_v1(
            _TASK9_PAIR_AUTHORITY_LEDGER, authority
        )
    if (
        record is None
        or record["pending_payload"] is not payload
        or record["pid"] != _os.getpid()
        or record["thread"] != _threading.get_ident()
    ):
        raise Task9TransitionEvidenceError("task9_evidence_promotion_uncertain")
    final_path, temp_path, cap = _task9_output_paths(record["stage_id"], output_kind)
    if len(payload) > cap:
        raise Task9TransitionEvidenceError("task9_evidence_capture_cap_exceeded")
    root_fd = -1
    temp_fd = -1
    temp_created = False
    try:
        root_fd = _os.open(
            record["root_path"],
            _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
        )
        if _task9_root_identity(_os.fstat(root_fd))[:4] != record["root_identity"][:4]:
            raise Task9TransitionEvidenceError("task9_evidence_filesystem_fact_mismatch")
        temp_fd = _os.open(
            temp_path,
            _os.O_WRONLY
            | _os.O_CREAT
            | _os.O_EXCL
            | _os.O_NOFOLLOW
            | _os.O_CLOEXEC,
            0o600,
            dir_fd=root_fd,
        )
        temp_created = True
        _task9_write_all(temp_fd, payload)
        before = _os.fstat(temp_fd)
        _os.fsync(temp_fd)
        after = _os.fstat(temp_fd)
        if (
            _task9_identity_fields(before) != _task9_identity_fields(after)
            or not _stat.S_ISREG(after.st_mode)
            or after.st_uid != _os.geteuid()
            or _stat.S_IMODE(after.st_mode) != 0o600
            or after.st_nlink != 1
            or after.st_size != len(payload)
        ):
            raise Task9TransitionEvidenceError("task9_evidence_filesystem_fact_mismatch")
        _task9_verify_promoted_payload_v1(
            root_fd,
            temp_path,
            payload,
            expected_device=after.st_dev,
            expected_inode=after.st_ino,
            expected_links=1,
            failure="task9_evidence_filesystem_fact_mismatch",
        )
        outcome = _call_task9_link_noreplace_v1(
            root_fd=root_fd,
            temp_relative_path=temp_path,
            final_relative_path=final_path,
        )
        if outcome is _Task9LinkCallOutcomeV1.FINAL_EXISTS:
            raise Task9TransitionEvidenceError("task9_evidence_promotion_conflict")
        if outcome is _Task9LinkCallOutcomeV1.CALL_UNCERTAIN:
            raise Task9TransitionEvidenceError("task9_evidence_promotion_uncertain")
        temp_stat = _os.stat(temp_path, dir_fd=root_fd, follow_symlinks=False)
        final_stat = _os.stat(final_path, dir_fd=root_fd, follow_symlinks=False)
        if (
            temp_stat.st_dev != final_stat.st_dev
            or temp_stat.st_ino != final_stat.st_ino
            or temp_stat.st_nlink != 2
            or final_stat.st_nlink != 2
            or final_stat.st_size != len(payload)
        ):
            raise Task9TransitionEvidenceError("task9_evidence_promotion_uncertain")
        _os.unlink(temp_path, dir_fd=root_fd)
        temp_created = False
        final_stat = _os.stat(final_path, dir_fd=root_fd, follow_symlinks=False)
        if final_stat.st_nlink != 1 or final_stat.st_size != len(payload):
            raise Task9TransitionEvidenceError("task9_evidence_promotion_uncertain")
        _task9_verify_promoted_payload_v1(
            root_fd,
            final_path,
            payload,
            expected_device=final_stat.st_dev,
            expected_inode=final_stat.st_ino,
            expected_links=1,
            failure="task9_evidence_promotion_uncertain",
        )
        _os.fsync(root_fd)
    except FileExistsError:
        raise Task9TransitionEvidenceError("task9_evidence_promotion_conflict") from None
    except Task9TransitionEvidenceError as exc:
        if str(exc) != "task9_evidence_promotion_uncertain" and temp_created and root_fd >= 0:
            try:
                if temp_fd >= 0:
                    _os.close(temp_fd)
                    temp_fd = -1
                _os.unlink(temp_path, dir_fd=root_fd)
                _os.fsync(root_fd)
            except Exception:
                raise Task9TransitionEvidenceError(
                    "task9_evidence_promotion_uncertain"
                ) from None
        raise
    except Exception:
        raise Task9TransitionEvidenceError("task9_evidence_promotion_uncertain") from None
    finally:
        if temp_fd >= 0:
            _os.close(temp_fd)
        if root_fd >= 0:
            _os.close(root_fd)


def _task9_build_assignment_receipt(
    artifact: Task9CanonicalArtifactV1,
    *,
    stage_id: Task9EvidenceStageIdV1,
    artifact_bytes: bytes,
    write_mode: str,
) -> Task9ProceduralAssignmentWriteReceiptV1:
    row = _task9_stage_row(stage_id.value)
    projection = {
        "schema_version": 1,
        "receipt_id": "task-9-procedural-assignment-write-receipt-v1",
        "stage_id": stage_id.value,
        "artifact_family": row[1],
        "artifact_relative_path": row[3],
        "artifact_temp_relative_path": row[6],
        "receipt_relative_path": row[4],
        "receipt_temp_relative_path": row[7],
        "assignment_scope": stage_id.value,
        "assignment_sha256": artifact.procedural_assignment_evidence.assignment_sha256,
        "artifact_self_field": row[2],
        "artifact_self_sha256": getattr(artifact, row[2]),
        "artifact_content_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "promotion_policy_sha256": TASK9_NO_REPLACE_PROMOTION_POLICY_V2.policy_sha256,
        "writer_projection_sha256": _task9_assignment_writer_projection_sha256_v2(
            stage_id.value
        ),
        "write_mode": write_mode,
    }
    return Task9ProceduralAssignmentWriteReceiptV1(
        **projection,
        receipt_sha256=_task9_domain_sha256_v1(
            "INCI-TASK-9-PROCEDURAL-ASSIGNMENT-WRITE-RECEIPT-V1",
            projection,
        ),
    )


def _task9_write_artifact_pair(
    artifact: Task9CanonicalArtifactV1,
    *,
    reservation: Task9ProceduralAssignmentReservationV1,
    write_authority: Task9EvidencePairWriteAuthorityV1,
    expected_stage: Task9EvidenceStageIdV1,
) -> Task9ProceduralAssignmentWriteReceiptV1:
    if (
        type(reservation) is not Task9ProceduralAssignmentReservationV1
        or type(write_authority) is not Task9EvidencePairWriteAuthorityV1
    ):
        raise Task9TransitionEvidenceError(
            "task9_procedural_assignment_reservation_invalid"
        )
    with _TASK9_EVIDENCE_LOCK:
        reservation_record = _task9_get_live_record_v1(
            _TASK9_RESERVATION_LEDGER, reservation
        )
        pair_record = _task9_get_live_record_v1(
            _TASK9_PAIR_AUTHORITY_LEDGER, write_authority
        )
        if reservation_record is None:
            disposition = _classify_task9_weak_identity_v1(
                _TASK9_CONSUMED_RESERVATION_TOMBSTONES,
                numeric_key=id(reservation),
                candidate=reservation,
            )
            if disposition == "TERMINAL_REPEAT":
                raise Task9TransitionEvidenceError(
                    "task9_procedural_assignment_reservation_consumed"
                )
            raise Task9TransitionEvidenceError(
                "task9_procedural_assignment_reservation_invalid"
            )
        if (
            reservation_record["state"] != "FRESH"
            or reservation_record["artifact"] is not artifact
            or reservation_record["pair"] is not write_authority
            or reservation_record["stage_id"] is not expected_stage
            or pair_record is None
            or pair_record["state"] != "RESERVED"
            or pair_record["stage_id"] is not expected_stage
        ):
            raise Task9TransitionEvidenceError(
                "task9_procedural_assignment_reservation_invalid"
            )
        reservation_record["state"] = "CONSUMING"
    try:
        _task9_validate_artifact(artifact)
        artifact_bytes = reservation_record["artifact_bytes"]
        if artifact_bytes != _canonical_json_bytes(_task9_public_projection(artifact)):
            raise Task9TransitionEvidenceError(
                "task9_procedural_assignment_artifact_drift"
            )
        pair_record["pending_payload"] = artifact_bytes
        _write_and_promote_task9_stage_output_v1(
            write_authority,
            output_kind=Task9StageOutputKindV1.ARTIFACT,
            payload=artifact_bytes,
        )
        pair_record["pending_payload"] = None
        reservation_record["state"] = "ARTIFACT_DURABLE_RECEIPT_PENDING"
        pair_record["state"] = "ARTIFACT_DURABLE"
        receipt = _task9_build_assignment_receipt(
            artifact,
            stage_id=expected_stage,
            artifact_bytes=artifact_bytes,
            write_mode="INITIAL",
        )
        validate_task9_procedural_assignment_write_receipt_structure_v1(receipt)
        receipt_bytes = _canonical_json_bytes(_task9_public_projection(receipt))
        reservation_record["state"] = "RECEIPT_WRITING"
        pair_record["state"] = "RECEIPT_WRITING"
        pair_record["pending_payload"] = receipt_bytes
        _write_and_promote_task9_stage_output_v1(
            write_authority,
            output_kind=Task9StageOutputKindV1.PROCEDURAL_ASSIGNMENT_RECEIPT,
            payload=receipt_bytes,
        )
        pair_record["pending_payload"] = None
        with _TASK9_EVIDENCE_LOCK:
            reservation_record["state"] = "CONSUMED"
            pair_record["state"] = "CONSUMED"
            _TASK9_RESERVATION_LEDGER.pop(id(reservation), None)
            _TASK9_PAIR_AUTHORITY_LEDGER.pop(id(write_authority), None)
            _task9_terminalize_consumed_reservation_v1(reservation)
        return receipt
    except Task9TransitionEvidenceError as exc:
        with _TASK9_EVIDENCE_LOCK:
            reservation_record["state"] = (
                "DURABILITY_UNCERTAIN"
                if str(exc) in (
                    "task9_evidence_promotion_uncertain",
                    "task9_procedural_assignment_write_uncertain",
                )
                else "CONSUMED_FAILED"
            )
            pair_record["state"] = reservation_record["state"]
            pair_record["pending_payload"] = None
            _TASK9_RESERVATION_LEDGER.pop(id(reservation), None)
            _TASK9_PAIR_AUTHORITY_LEDGER.pop(id(write_authority), None)
            _task9_terminalize_consumed_reservation_v1(reservation)
        raise


def write_task9_predecessor_transition_manifest_v1(
    value: Task9PredecessorTransitionManifestV1,
    *,
    reservation: Task9ProceduralAssignmentReservationV1,
    write_authority: Task9EvidencePairWriteAuthorityV1,
) -> Task9ProceduralAssignmentWriteReceiptV1:
    return _task9_write_artifact_pair(
        value,
        reservation=reservation,
        write_authority=write_authority,
        expected_stage=Task9EvidenceStageIdV1.PREDECESSOR_TRANSITION_MANIFEST,
    )


def write_task9_predecessor_transition_review_v1(
    value: Task9PredecessorTransitionReviewV1,
    *,
    reservation: Task9ProceduralAssignmentReservationV1,
    write_authority: Task9EvidencePairWriteAuthorityV1,
) -> Task9ProceduralAssignmentWriteReceiptV1:
    return _task9_write_artifact_pair(
        value,
        reservation=reservation,
        write_authority=write_authority,
        expected_stage=Task9EvidenceStageIdV1.PREDECESSOR_TRANSITION_REVIEW,
    )


def write_task9_post_predecessor_amended_package_rereview_v1(
    value: Task9PostPredecessorAmendedPackageRereviewV1,
    *,
    reservation: Task9ProceduralAssignmentReservationV1,
    write_authority: Task9EvidencePairWriteAuthorityV1,
) -> Task9ProceduralAssignmentWriteReceiptV1:
    return _task9_write_artifact_pair(
        value,
        reservation=reservation,
        write_authority=write_authority,
        expected_stage=Task9EvidenceStageIdV1.POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW,
    )


def write_task9_functional_wave_review_v1(
    value: Task9FunctionalWaveReviewV1,
    *,
    wave_id: Task9FunctionalWaveIdV1,
    reservation: Task9ProceduralAssignmentReservationV1,
    write_authority: Task9EvidencePairWriteAuthorityV1,
) -> Task9ProceduralAssignmentWriteReceiptV1:
    if type(wave_id) is not Task9FunctionalWaveIdV1 or value.wave_id != wave_id.value:
        raise Task9TransitionEvidenceError(
            "task9_procedural_assignment_reservation_invalid"
        )
    expected_stage = {
        Task9FunctionalWaveIdV1.A: Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_A,
        Task9FunctionalWaveIdV1.B: Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_B,
        Task9FunctionalWaveIdV1.C: Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_C,
        Task9FunctionalWaveIdV1.D: Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_D,
        Task9FunctionalWaveIdV1.E: Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_E,
        Task9FunctionalWaveIdV1.R: Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_R,
    }[wave_id]
    return _task9_write_artifact_pair(
        value,
        reservation=reservation,
        write_authority=write_authority,
        expected_stage=expected_stage,
    )


def write_task9_final_reseal_transition_v1(
    value: Task9FinalResealTransitionV1,
    *,
    reservation: Task9ProceduralAssignmentReservationV1,
    write_authority: Task9EvidencePairWriteAuthorityV1,
) -> Task9ProceduralAssignmentWriteReceiptV1:
    return _task9_write_artifact_pair(
        value,
        reservation=reservation,
        write_authority=write_authority,
        expected_stage=Task9EvidenceStageIdV1.FINAL_RESEAL_TRANSITION,
    )


def write_task9_final_reseal_review_v1(
    value: Task9FinalResealReviewV1,
    *,
    reservation: Task9ProceduralAssignmentReservationV1,
    write_authority: Task9EvidencePairWriteAuthorityV1,
) -> Task9ProceduralAssignmentWriteReceiptV1:
    return _task9_write_artifact_pair(
        value,
        reservation=reservation,
        write_authority=write_authority,
        expected_stage=Task9EvidenceStageIdV1.FINAL_RESEAL_REVIEW,
    )


def write_task9_release_evidence_v1(
    value: Task9ReleaseEvidenceV1,
    *,
    reservation: Task9ProceduralAssignmentReservationV1,
    write_authority: Task9EvidencePairWriteAuthorityV1,
) -> Task9ProceduralAssignmentWriteReceiptV1:
    return _task9_write_artifact_pair(
        value,
        reservation=reservation,
        write_authority=write_authority,
        expected_stage=Task9EvidenceStageIdV1.RELEASE_EVIDENCE,
    )


def _task9_parse_artifact_for_stage(
    stage_id: Task9EvidenceStageIdV1, payload: bytes
) -> Task9CanonicalArtifactV1:
    parsers = {
        Task9EvidenceStageIdV1.PREDECESSOR_TRANSITION_MANIFEST: parse_task9_predecessor_transition_manifest_v1,
        Task9EvidenceStageIdV1.PREDECESSOR_TRANSITION_REVIEW: parse_task9_predecessor_transition_review_v1,
        Task9EvidenceStageIdV1.POST_PREDECESSOR_AMENDED_PACKAGE_REREVIEW: parse_task9_post_predecessor_amended_package_rereview_v1,
        Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_A: parse_task9_functional_wave_review_v1,
        Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_B: parse_task9_functional_wave_review_v1,
        Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_C: parse_task9_functional_wave_review_v1,
        Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_D: parse_task9_functional_wave_review_v1,
        Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_E: parse_task9_functional_wave_review_v1,
        Task9EvidenceStageIdV1.FUNCTIONAL_WAVE_REVIEW_R: parse_task9_functional_wave_review_v1,
        Task9EvidenceStageIdV1.FINAL_RESEAL_TRANSITION: parse_task9_final_reseal_transition_v1,
        Task9EvidenceStageIdV1.FINAL_RESEAL_REVIEW: parse_task9_final_reseal_review_v1,
        Task9EvidenceStageIdV1.RELEASE_EVIDENCE: parse_task9_release_evidence_v1,
    }
    result = parsers[stage_id](payload)
    if _task9_stage_for_artifact(result) is not stage_id:
        raise Task9TransitionEvidenceError(
            "task9_procedural_assignment_recovery_invalid"
        )
    return result


def issue_task9_procedural_assignment_recovery_reservation_v1(
    authority: Task9ProceduralAttestationAuthorityV1,
    *,
    stage_id: Task9EvidenceStageIdV1,
    write_authority: Task9EvidencePairWriteAuthorityV1,
) -> Task9ProceduralAssignmentReservationV1:
    if (
        type(authority) is not Task9ProceduralAttestationAuthorityV1
        or type(stage_id) is not Task9EvidenceStageIdV1
        or type(write_authority) is not Task9EvidencePairWriteAuthorityV1
    ):
        raise Task9TransitionEvidenceError(
            "task9_procedural_assignment_recovery_invalid"
        )
    with _TASK9_EVIDENCE_LOCK:
        attestation_record = _task9_get_live_record_v1(
            _TASK9_ATTESTATION_LEDGER, authority
        )
        pair_record = _task9_get_live_record_v1(
            _TASK9_PAIR_AUTHORITY_LEDGER, write_authority
        )
        if (
            attestation_record is None
            or attestation_record["state"] != "FRESH"
            or pair_record is None
            or pair_record["state"] != "FRESH"
            or pair_record["write_mode"] != "RECOVERY"
            or pair_record["stage_id"] is not stage_id
        ):
            raise Task9TransitionEvidenceError(
                "task9_procedural_assignment_recovery_invalid"
            )
        attestation_record["state"] = "CONSUMING"
        pair_record["state"] = "ISSUING"
    root_fd = -1
    try:
        root_fd = _os.open(
            pair_record["root_path"],
            _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
        )
        if _task9_root_identity(_os.fstat(root_fd))[:4] != pair_record[
            "root_identity"
        ][:4]:
            raise Task9TransitionEvidenceError(
                "task9_procedural_assignment_recovery_invalid"
            )
        artifact_path = _task9_stage_row(stage_id.value)[3]
        decoder_row = next(
            row for row in TASK9_EVIDENCE_DECODER_TABLE_V3 if row[0] == artifact_path
        )
        path_snapshot, artifact_bytes = _task9_capture_path(
            root_fd, decoder_row, 1_048_576
        )
        if path_snapshot.state != "PRESENT" or type(artifact_bytes) is not bytes:
            raise Task9TransitionEvidenceError(
                "task9_procedural_assignment_recovery_invalid"
            )
        artifact = _task9_parse_artifact_for_stage(stage_id, artifact_bytes)
        assignment = artifact.procedural_assignment_evidence
        expected_assignment_projection = {
            "assignment_scope": attestation_record["assignment_scope"],
            "controller_operator_label": attestation_record[
                "controller_operator_label"
            ],
            "creator_controller_label": attestation_record[
                "creator_controller_label"
            ],
            "role_bindings": _task9_public_projection(
                attestation_record["role_bindings"]
            ),
            "reviewer_label": attestation_record["reviewer_label"],
        }
        actual_assignment_projection = {
            "assignment_scope": assignment.assignment_scope,
            "controller_operator_label": assignment.controller_operator_label,
            "creator_controller_label": assignment.creator_controller_label,
            "role_bindings": _task9_public_projection(assignment.role_bindings),
            "reviewer_label": assignment.reviewer_label,
        }
        if expected_assignment_projection != actual_assignment_projection:
            raise Task9TransitionEvidenceError(
                "task9_procedural_assignment_recovery_invalid"
            )
        reservation = Task9ProceduralAssignmentReservationV1(
            _TASK9_AUTHORITY_TOKEN
        )
        reservation_record = {
            "ref": _weakref.ref(reservation),
            "assignment": assignment,
            "artifact": artifact,
            "artifact_bytes": artifact_bytes,
            "pair": write_authority,
            "stage_id": stage_id,
            "write_mode": "RECOVERY",
            "pid": _os.getpid(),
            "thread": _threading.get_ident(),
            "state": "FRESH",
        }
        with _TASK9_EVIDENCE_LOCK:
            _task9_admit_live_record_v1(
                _TASK9_RESERVATION_LEDGER, reservation, reservation_record
            )
            attestation_record["state"] = "CONSUMED"
            pair_record["state"] = "RESERVED"
            _TASK9_ATTESTATION_LEDGER.pop(id(authority), None)
        return reservation
    except (Task9TransitionEvidenceError, StopIteration):
        with _TASK9_EVIDENCE_LOCK:
            attestation_record["state"] = "CONSUMED_FAILED"
            pair_record["state"] = "CONSUMED_FAILED"
            _TASK9_ATTESTATION_LEDGER.pop(id(authority), None)
            _TASK9_PAIR_AUTHORITY_LEDGER.pop(id(write_authority), None)
        raise Task9TransitionEvidenceError(
            "task9_procedural_assignment_recovery_invalid"
        ) from None
    finally:
        if root_fd >= 0:
            _os.close(root_fd)


def recover_task9_procedural_assignment_write_receipt_v1(
    reservation: Task9ProceduralAssignmentReservationV1,
    *,
    write_authority: Task9EvidencePairWriteAuthorityV1,
) -> Task9ProceduralAssignmentWriteReceiptV1:
    if (
        type(reservation) is not Task9ProceduralAssignmentReservationV1
        or type(write_authority) is not Task9EvidencePairWriteAuthorityV1
    ):
        raise Task9TransitionEvidenceError(
            "task9_procedural_assignment_recovery_invalid"
        )
    with _TASK9_EVIDENCE_LOCK:
        reservation_record = _task9_get_live_record_v1(
            _TASK9_RESERVATION_LEDGER, reservation
        )
        pair_record = _task9_get_live_record_v1(
            _TASK9_PAIR_AUTHORITY_LEDGER, write_authority
        )
        if reservation_record is None:
            disposition = _classify_task9_weak_identity_v1(
                _TASK9_CONSUMED_RESERVATION_TOMBSTONES,
                numeric_key=id(reservation),
                candidate=reservation,
            )
            if disposition == "TERMINAL_REPEAT":
                raise Task9TransitionEvidenceError(
                    "task9_procedural_assignment_reservation_consumed"
                )
            raise Task9TransitionEvidenceError(
                "task9_procedural_assignment_recovery_invalid"
            )
        if (
            reservation_record["state"] != "FRESH"
            or reservation_record["write_mode"] != "RECOVERY"
            or reservation_record["pair"] is not write_authority
            or pair_record is None
            or pair_record["state"] != "RESERVED"
            or pair_record["write_mode"] != "RECOVERY"
        ):
            raise Task9TransitionEvidenceError(
                "task9_procedural_assignment_recovery_invalid"
            )
        reservation_record["state"] = "CONSUMING"
    stage_id = reservation_record["stage_id"]
    artifact = reservation_record["artifact"]
    artifact_bytes = reservation_record["artifact_bytes"]
    expected = _task9_build_assignment_receipt(
        artifact,
        stage_id=stage_id,
        artifact_bytes=artifact_bytes,
        write_mode="RECOVERY",
    )
    root_fd = -1
    try:
        root_fd = _os.open(
            pair_record["root_path"],
            _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
        )
        receipt_path = _task9_stage_row(stage_id.value)[4]
        decoder_row = next(
            row for row in TASK9_EVIDENCE_DECODER_TABLE_V3 if row[0] == receipt_path
        )
        receipt_snapshot, receipt_bytes = _task9_capture_path(
            root_fd, decoder_row, 131_072
        )
    except StopIteration:
        raise Task9TransitionEvidenceError(
            "task9_procedural_assignment_recovery_invalid"
        ) from None
    finally:
        if root_fd >= 0:
            _os.close(root_fd)
    try:
        if receipt_snapshot.state == "PRESENT":
            if type(receipt_bytes) is not bytes:
                raise Task9TransitionEvidenceError(
                    "task9_procedural_assignment_receipt_conflict"
                )
            existing = parse_task9_procedural_assignment_write_receipt_v1(
                receipt_bytes
            )
            existing_projection = _task9_public_projection(
                existing, exclude=("write_mode", "receipt_sha256")
            )
            expected_projection = _task9_public_projection(
                expected, exclude=("write_mode", "receipt_sha256")
            )
            if existing_projection != expected_projection:
                raise Task9TransitionEvidenceError(
                    "task9_procedural_assignment_receipt_conflict"
                )
            result = existing
        else:
            receipt_bytes = _canonical_json_bytes(_task9_public_projection(expected))
            pair_record["pending_payload"] = receipt_bytes
            pair_record["state"] = "RECEIPT_WRITING"
            reservation_record["state"] = "RECEIPT_WRITING"
            _write_and_promote_task9_stage_output_v1(
                write_authority,
                output_kind=Task9StageOutputKindV1.PROCEDURAL_ASSIGNMENT_RECEIPT,
                payload=receipt_bytes,
            )
            pair_record["pending_payload"] = None
            result = expected
        with _TASK9_EVIDENCE_LOCK:
            reservation_record["state"] = "CONSUMED"
            pair_record["state"] = "CONSUMED"
            _TASK9_RESERVATION_LEDGER.pop(id(reservation), None)
            _TASK9_PAIR_AUTHORITY_LEDGER.pop(id(write_authority), None)
            _task9_terminalize_consumed_reservation_v1(reservation)
        return result
    except Task9TransitionEvidenceError as exc:
        with _TASK9_EVIDENCE_LOCK:
            state = (
                "DURABILITY_UNCERTAIN"
                if str(exc) == "task9_evidence_promotion_uncertain"
                else "CONSUMED_FAILED"
            )
            reservation_record["state"] = state
            pair_record["state"] = state
            pair_record["pending_payload"] = None
            _TASK9_RESERVATION_LEDGER.pop(id(reservation), None)
            _TASK9_PAIR_AUTHORITY_LEDGER.pop(id(write_authority), None)
            _task9_terminalize_consumed_reservation_v1(reservation)
        raise


# TASK9_ROUND19_COMMAND_BOOTSTRAP_BEGIN_V1
import fcntl as _fcntl
import ast as _ast
import base64 as _base64
import csv as _csv
import importlib.metadata as _importlib_metadata
import io as _io
import re as _re
import selectors as _selectors
import signal as _signal
import subprocess as _subprocess
import sysconfig as _sysconfig
import time as _time


_TASK9_BOOTSTRAP_LOADED_ORIGIN_V1: Final[str] = __file__


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9ModuleOriginRootBindingPolicyV1(_Task9ExactValue):
    schema_version: int
    policy_id: str
    module_relative_path: str
    module_suffix_components: tuple[str, str]
    directory_open_flags: tuple[str, ...]
    file_open_flags: tuple[str, ...]
    origin_identity_checks: tuple[str, ...]
    root_identity_checks: tuple[str, ...]
    forbidden_sources: tuple[str, ...]
    policy_sha256: str


_TASK9_BOOTSTRAP_ROOT_POLICY_PROJECTION_V1: Final[dict[str, object]] = {
    "schema_version": 1,
    "policy_id": "TASK9_MODULE_ORIGIN_ROOT_BINDING_V1",
    "module_relative_path": "tools/task9_transition_evidence.py",
    "module_suffix_components": ("tools", "task9_transition_evidence.py"),
    "directory_open_flags": ("O_RDONLY", "O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"),
    "file_open_flags": ("O_RDONLY", "O_NOFOLLOW", "O_CLOEXEC"),
    "origin_identity_checks": (
        "ABSOLUTE", "EXACT_FILE_EQUALS_SPEC_ORIGIN", "REGULAR", "EUID_OWNER",
        "NOT_GROUP_OR_OTHER_WRITABLE", "NLINK_1", "STABLE_IDENTITY", "STABLE_BYTES",
    ),
    "root_identity_checks": (
        "DIRECTORY", "EUID_OWNER", "NOT_GROUP_OR_OTHER_WRITABLE", "STABLE_IDENTITY",
        "REOPENED_MODULE_SAME_DEVICE_INODE_BYTES",
    ),
    "forbidden_sources": (
        "CALLER_PATH", "CWD", "ENVIRONMENT", "REALPATH", "SYMLINK", "DEFAULT_ROOT",
    ),
}
TASK9_MODULE_ORIGIN_ROOT_BINDING_POLICY_V1: Final[Task9ModuleOriginRootBindingPolicyV1] = (
    Task9ModuleOriginRootBindingPolicyV1(
        **_TASK9_BOOTSTRAP_ROOT_POLICY_PROJECTION_V1,
        policy_sha256=_task9_domain_sha256_v1(
            "INCI-TASK-9-MODULE-ORIGIN-ROOT-BINDING-POLICY-V1",
            _TASK9_BOOTSTRAP_ROOT_POLICY_PROJECTION_V1,
        ),
    )
)


# Round-19 path-closure bootstrap substrate.

def _task9_bootstrap_projection_v1(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        projection: dict[str, object] = {}
        for field in fields(value):
            cell = getattr(value, field.name)
            if field.name == "raw_bytes":
                if type(cell) is not bytes:
                    raise _invalid()
                projection["raw_bytes_hex"] = cell.hex()
            else:
                projection[field.name] = _task9_bootstrap_projection_v1(cell)
        return projection
    if type(value) is tuple:
        return [_task9_bootstrap_projection_v1(item) for item in value]
    if type(value) is list:
        return [_task9_bootstrap_projection_v1(item) for item in value]
    if type(value) is dict:
        return {
            key: _task9_bootstrap_projection_v1(item)
            for key, item in value.items()
        }
    if isinstance(value, Enum):
        return value.value
    if value is None or type(value) in (str, int, bool):
        return value
    raise _invalid()


def _task9_bootstrap_domain_sha256_v1(domain: str, projection: object) -> str:
    return _task9_domain_sha256_v1(
        domain, _task9_bootstrap_projection_v1(projection)
    )


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9TreeInventoryRowV1(_Task9ExactValue):
    relative_path: str
    state: str
    content_sha256: str | None


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9RuntimeInventoryRowV1(_Task9ExactValue):
    relative_path: str
    file_kind: str
    size: int
    stat_identity: tuple[int, ...]
    content_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9ExternalDistributionFileRowV1(_Task9ExactValue):
    relative_path: str
    size: int
    stat_identity: tuple[int, ...]
    content_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9ExternalDistributionInventoryRowV1(_Task9ExactValue):
    normalized_name: str
    version: str
    metadata_sha256: str
    record_sha256: str
    file_rows: tuple[Task9ExternalDistributionFileRowV1, ...]


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9InterpreterPathHopRowV1(_Task9ExactValue):
    scope: str
    hop_index: int
    source_path: str
    link_target: str
    link_stat_identity: tuple[int, ...]
    resolved_after_hop_path: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9RuntimeSymlinkInventoryRowV1(_Task9ExactValue):
    relative_path: str
    link_target: str
    link_stat_identity: tuple[int, ...]
    target_kind: str
    target_role: str
    runtime_file_kind: str | None
    target_size: int | None
    target_stat_identity: tuple[int, ...]
    target_content_sha256: str | None


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9RuntimeRegularTargetRowV1(_Task9ExactValue):
    target_role: str
    resolved_target_path: str
    target_size: int
    target_stat_identity: tuple[int, ...]
    target_content_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9ExcludedBasePurelibFileRowV1(_Task9ExactValue):
    relative_path: str
    file_kind: str
    size: int
    stat_identity: tuple[int, ...]
    content_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9ExcludedBasePurelibDirectoryIdentityRowV1(_Task9ExactValue):
    relative_path: str
    stat_identity: tuple[int, ...]
    entries_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9ExcludedBasePurelibDirectoryRowV1(_Task9ExactValue):
    schema_version: int
    target_role: str
    resolved_target_path: str
    relation_to_venv_purelib: str
    active_search_path_index: None
    target_stat_identity: tuple[int, ...]
    target_entries_sha256: str
    exact_file_count: int
    exact_directory_count: int
    exact_file_bytes: int
    file_rows: tuple[Task9ExcludedBasePurelibFileRowV1, ...]
    directory_identity_rows: tuple[
        Task9ExcludedBasePurelibDirectoryIdentityRowV1, ...
    ]
    excluded_inventory_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9RuntimeSymlinkInventoryEvidenceV1(_Task9ExactValue):
    schema_version: int
    runtime_symlink_inventory_rows: tuple[
        Task9RuntimeSymlinkInventoryRowV1, ...
    ]
    regular_target_rows: tuple[Task9RuntimeRegularTargetRowV1, ...]
    excluded_base_purelib_directory: Task9ExcludedBasePurelibDirectoryRowV1
    evidence_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9PathComponentDirectoryIdentityRowV1(_Task9ExactValue):
    endpoint_key: str
    component_index: int
    absolute_path: str
    stat_identity: tuple[int, ...]
    entries_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9PathEndpointParentIdentityRowV1(_Task9ExactValue):
    endpoint_key: str
    endpoint_role: str
    endpoint_path: str
    parent_path: str
    parent_stat_identity: tuple[int, ...]
    parent_entries_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9TrustedHomebrewComponentModePolicyV1(_Task9ExactValue):
    schema_version: int
    policy_id: str
    default_directory_rule: str
    effective_group_membership_source: str
    group_id_normalization: str
    exact_group_member_count: int
    exception_rows: tuple[tuple[str, str, int, str], ...]
    required_group_member_role_uids: tuple[str, str]
    bind_resolved_member_names: bool
    require_before_after_equality: bool
    require_complete_passwd_universe: bool
    require_distinct_role_uids: bool
    require_empty_primary_gid_member_rows: bool
    require_zero_membership_query_errors: bool
    policy_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9InstalledHostPasswdGroupAccessWitnessV1(_Task9ExactValue):
    schema_version: int
    witness_id: str
    passwd_raw_row_count: int
    passwd_raw_canonical_bytes: int
    passwd_raw_rows_sha256: str
    passwd_unique_row_count: int
    passwd_unique_canonical_bytes: int
    passwd_unique_rows_sha256: str
    effective_group_access_row_count: int
    effective_group_access_canonical_bytes: int
    effective_group_access_rows_sha256: str
    witness_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9TrustedHomebrewComponentRowV1(_Task9ExactValue):
    path: str
    owner_role: str
    stat_identity: tuple[int, ...]
    entries_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9TrustedHomebrewComponentModeEvidenceV1(_Task9ExactValue):
    schema_version: int
    policy_sha256: str
    installed_host_witness: Task9InstalledHostPasswdGroupAccessWitnessV1
    passwd_raw_row_count: int
    passwd_raw_canonical_bytes: int
    passwd_raw_rows_sha256: str
    passwd_raw_rows: tuple[tuple[object, ...], ...]
    passwd_unique_row_count: int
    passwd_unique_canonical_bytes: int
    passwd_unique_rows_sha256: str
    passwd_unique_rows: tuple[tuple[object, ...], ...]
    passwd_name_conflict_rows: tuple[tuple[object, ...], ...]
    passwd_uid_conflict_rows: tuple[tuple[object, ...], ...]
    root_role_passwd_row: tuple[object, ...]
    effective_uid_role_passwd_row: tuple[object, ...]
    gid80_group_row: tuple[object, ...]
    gid80_member_resolution_rows: tuple[tuple[object, ...], ...]
    primary_gid_member_rows: tuple[tuple[object, ...], ...]
    effective_group_access_row_count: int
    effective_group_access_canonical_bytes: int
    effective_group_access_rows_sha256: str
    effective_group_access_rows: tuple[tuple[object, ...], ...]
    membership_query_error_rows: tuple[tuple[object, ...], ...]
    effective_gid80_member_rows: tuple[tuple[object, ...], ...]
    builtin_identity_rows: tuple[tuple[str, str], ...]
    component_rows: tuple[Task9TrustedHomebrewComponentRowV1, ...]
    evidence_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9PyvenvConfigEvidenceV1(_Task9ExactValue):
    schema_version: int
    path: str
    raw_bytes: bytes
    size: int
    stat_identity: tuple[int, ...]
    content_sha256: str
    parsed_rows: tuple[tuple[str, str], ...]
    policy_sha256: str
    evidence_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9ImportSearchPathRowV1(_Task9ExactValue):
    index: int
    absolute_path: str
    role: str
    state: str
    path_stat_identity: tuple[int, ...] | None
    absent_parent_path: str | None
    absent_parent_stat_identity: tuple[int, ...] | None
    absent_parent_entries_sha256: str | None


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9ImportSearchRowProjectionRowV1(_Task9ExactValue):
    index: int
    absolute_path: str
    role: str
    state: str
    path_stat_identity: tuple[int, ...] | None


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9ImportSearchRowProjectionV1(_Task9ExactValue):
    schema_version: int
    policy_sha256: str
    rows: tuple[Task9ImportSearchRowProjectionRowV1, ...]
    projection_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9ImportSearchPathEvidenceV1(_Task9ExactValue):
    schema_version: int
    policy_sha256: str
    rows: tuple[Task9ImportSearchPathRowV1, ...]
    row_projection_sha256: str
    excluded_base_purelib_directory: Task9ExcludedBasePurelibDirectoryRowV1
    evidence_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9InterpreterPathClosureEvidenceV1(_Task9ExactValue):
    schema_version: int
    allowance_sha256: str
    launcher_hop_rows: tuple[Task9InterpreterPathHopRowV1, ...]
    stdlib_root_hop_rows: tuple[Task9InterpreterPathHopRowV1, ...]
    purelib_root_hop_rows: tuple[()]
    component_directory_identity_rows: tuple[
        Task9PathComponentDirectoryIdentityRowV1, ...
    ]
    endpoint_parent_identity_rows: tuple[
        Task9PathEndpointParentIdentityRowV1, ...
    ]
    regular_target_rows: tuple[Task9RuntimeRegularTargetRowV1, ...]
    component_allowance_sha256: str
    endpoint_parent_allowance_sha256: str
    pyvenv_config_evidence: Task9PyvenvConfigEvidenceV1
    import_search_path_evidence: Task9ImportSearchPathEvidenceV1
    runtime_symlink_inventory_sha256: str
    trusted_homebrew_component_mode_evidence: (
        Task9TrustedHomebrewComponentModeEvidenceV1
    )
    evidence_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9InterpreterPathClosureAllowanceV1(_Task9ExactValue):
    schema_version: int
    allowance_id: str
    max_symlink_hops: int
    path_hop_rows: tuple[tuple[object, ...], ...]
    zero_hop_scopes: tuple[tuple[str, str], ...]
    runtime_symlink_rows: tuple[tuple[object, ...], ...]
    regular_target_rows: tuple[tuple[str, str], ...]
    excluded_directory_target_rows: tuple[tuple[str, str], ...]
    allowance_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9PyvenvConfigPolicyV1(_Task9ExactValue):
    schema_version: int
    policy_id: str
    path: str
    content_size: int
    content_sha256: str
    encoding: str
    line_ending: str
    terminal_lf: bool
    parsed_rows: tuple[tuple[str, str], ...]
    include_system_site_packages: bool
    policy_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9ImportSearchPathPolicyV1(_Task9ExactValue):
    schema_version: int
    policy_id: str
    exact_row_count: int
    rows: tuple[tuple[object, ...], ...]
    excluded_base_purelib_path: str
    excluded_base_purelib_relation: str
    excluded_base_purelib_search_state: str
    policy_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9PathEndpointParentAllowanceV1(_Task9ExactValue):
    schema_version: int
    policy_id: str
    exact_row_count: int
    rows: tuple[tuple[object, ...], ...]
    policy_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9PathComponentAllowanceV1(_Task9ExactValue):
    schema_version: int
    policy_id: str
    exact_row_count: int
    rows: tuple[tuple[object, ...], ...]
    policy_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9BootstrapProbeEnvironmentPolicyV1(_Task9ExactValue):
    schema_version: int
    policy_id: str
    inherit_parent_environment: bool
    probe_kinds: tuple[str, str]
    probe_row_name: str
    fixed_rows: tuple[tuple[str, str], ...]
    dynamic_rows: tuple[tuple[str, str], ...]
    policy_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9InterpreterEvidenceV1(_Task9ExactValue):
    schema_version: int
    interpreter_id: str
    launcher_path: str
    implementation_name: str
    version_info: tuple[int, int, int, str, int]
    cache_tag: str
    resolved_executable_path: str
    resolved_executable_stat_identity: tuple[int, ...]
    resolved_executable_sha256: str
    runtime_inventory_rows: tuple[Task9RuntimeInventoryRowV1, ...]
    runtime_symlink_inventory_evidence: Task9RuntimeSymlinkInventoryEvidenceV1
    runtime_directory_identity_rows: tuple[tuple[str, tuple[int, ...]], ...]
    runtime_inventory_sha256: str
    external_distribution_inventory_rows: tuple[Task9ExternalDistributionInventoryRowV1, ...]
    site_packages_directory_identity_rows: tuple[tuple[str, tuple[int, ...]], ...]
    external_distribution_inventory_sha256: str
    path_closure_evidence: Task9InterpreterPathClosureEvidenceV1
    evidence_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9CommandDependencyInventoryV1(_Task9ExactValue):
    schema_version: int
    inventory_id: str
    inventory_rows: tuple[Task9TreeInventoryRowV1, ...]
    file_identity_rows: tuple[tuple[str, tuple[int, ...]], ...]
    directory_identity_rows: tuple[tuple[str, tuple[int, ...]], ...]
    inventory_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9CommandDependencyGenesisV1(_Task9ExactValue):
    schema_version: int
    genesis_id: str
    dependency_inventory: Task9CommandDependencyInventoryV1
    interpreter_evidence: Task9InterpreterEvidenceV1
    root_binding_policy_sha256: str
    captured_monotonic_ns: int
    genesis_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9CommandDependencyFreezeReservationV1(_Task9ExactValue):
    schema_version: int
    command_id: str
    stage_id: str
    baseline_kind: str
    dependency_predecessor_stage_id: None
    dependency_genesis_sha256: str
    baseline_dependency_inventory_sha256: str
    dependency_inventory_id: str
    inventory_sha256: str
    antecedent_chain_receipt_sha256s: tuple[()]
    reservation_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9FrozenCommandDependencySealV1(_Task9ExactValue):
    schema_version: int
    command_id: str
    dependency_inventory: Task9CommandDependencyInventoryV1
    freeze_reservation: Task9CommandDependencyFreezeReservationV1
    seal_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9CommandRuntimeAllowanceV1(_Task9ExactValue):
    schema_version: int
    allowance_id: str
    allowed_stdlib_inventory_sha256: str
    allowed_external_distribution_names: tuple[str, ...]
    allowance_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9CommandExecutionSnapshotV1(_Task9ExactValue):
    schema_version: int
    command_id: str
    capture_phase: str
    root_identity_sha256: str
    root_binding_policy_sha256: str
    inventory_id: str
    inventory_rows: tuple[Task9TreeInventoryRowV1, ...]
    dependency_inventory_id: str
    dependency_inventory_rows: tuple[Task9TreeInventoryRowV1, ...]
    dependency_file_identity_rows: tuple[tuple[str, tuple[int, ...]], ...]
    dependency_directory_identity_rows: tuple[tuple[str, tuple[int, ...]], ...]
    execution_directory_stat_identity_rows: tuple[tuple[str, tuple[int, ...]], ...]
    interpreter_evidence_sha256: str
    interpreter_path_closure_evidence_sha256: str
    execution_runtime_identity_rows: tuple[tuple[str, tuple[int, ...]], ...]
    execution_runtime_identity_sha256: str
    frozen_dependency_seal_sha256: str
    captured_monotonic_ns: int
    snapshot_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9CommandCwdPolicyV1(_Task9ExactValue):
    schema_version: int
    policy_id: str
    root_binding_policy_sha256: str
    cwd_source: str
    shell_allowed: bool
    policy_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9CommandEnvironmentPolicyV1(_Task9ExactValue):
    schema_version: int
    policy_id: str
    inherit_parent_environment: bool
    fixed_rows: tuple[tuple[str, str], ...]
    dynamic_row_names: tuple[str, str]
    pycache_policy: str
    forbidden_capabilities: tuple[str, ...]
    policy_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9CommandStreamPolicyV1(_Task9ExactValue):
    schema_version: int
    policy_id: str
    stderr_mode: str
    output_cap_bytes: int
    decoding: str
    newline_policy: str
    nul_allowed: bool
    policy_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9CommandEvidenceV1(_Task9ExactValue):
    schema_version: int
    command_id: str
    runner_kind: str
    argv: tuple[str, ...]
    interpreter_evidence: Task9InterpreterEvidenceV1
    cwd_policy_sha256: str
    environment_policy_sha256: str
    stream_policy_sha256: str
    environment_rows: tuple[tuple[str, str], ...]
    environment_sha256: str
    stdin_mode: str
    timeout_ns: int
    output_cap_bytes: int
    expected_outcome_kind: str
    output_parser_id: str
    dependency_inventory: Task9CommandDependencyInventoryV1
    frozen_dependency_seal: Task9FrozenCommandDependencySealV1
    runtime_allowance: Task9CommandRuntimeAllowanceV1
    before_execution_snapshot: Task9CommandExecutionSnapshotV1
    started_monotonic_ns: int
    child_pid: int
    exit_code: int
    stdout_utf8: str
    stdout_byte_count: int
    stdout_sha256: str
    test_count: int
    failure_count: int
    error_count: int
    skipped_count: int
    reported_duration_milliseconds: int | None
    completed_monotonic_ns: int
    wall_duration_ns: int
    after_execution_snapshot: Task9CommandExecutionSnapshotV1
    evidence_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9BootstrapPathClosureSnapshotV1(_Task9ExactValue):
    schema_version: int
    snapshot_kind: str
    root_identity_sha256: str
    root_binding_policy_sha256: str
    dependency_inventory_sha256: str
    interpreter_evidence: Task9InterpreterEvidenceV1
    interpreter_path_closure_evidence_sha256: str
    bootstrap_probe_environment_policy_sha256: str
    captured_monotonic_ns: int
    snapshot_sha256: str


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Task9BootstrapRunObservationV1(_Task9ExactValue):
    schema_version: int
    observation_kind: str
    fixed_test_target: str
    before_snapshot: Task9BootstrapPathClosureSnapshotV1
    after_snapshot: Task9BootstrapPathClosureSnapshotV1
    unittest_environment_rows: tuple[tuple[str, str], ...]
    unittest_environment_rows_sha256: str
    frozen_v6_environment_rows: tuple[tuple[str, str], ...]
    frozen_v6_environment_rows_sha256: str
    unittest_stdout_utf8: str
    unittest_stdout_size: int
    unittest_stdout_sha256: str
    frozen_v6_stdout_utf8: str
    frozen_v6_stdout_size: int
    frozen_v6_stdout_sha256: str
    unittest_search_row_projection_sha256: str
    frozen_v6_search_row_projection_sha256: str
    unittest_sentinel_sha256: str
    frozen_v6_sentinel_sha256: str
    started_monotonic_ns: int
    unittest_child_pid: int
    unittest_completed_monotonic_ns: int
    frozen_v6_started_monotonic_ns: int
    frozen_v6_child_pid: int
    frozen_v6_completed_monotonic_ns: int
    completed_monotonic_ns: int
    wall_duration_ns: int
    unittest_returncode: int
    frozen_v6_returncode: int
    tests_run: int
    failures: int
    errors: int
    skipped: int
    semantic_outcome: str
    observation_sha256: str


_TASK9_PATH_ENDPOINT_ROWS_V1: Final[tuple[tuple[object, ...], ...]] = (('ABSENT_STDLIB_ZIP',
  'ABSENT_PARENT',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python314.zip',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib'),
 ('COMMAND_CWD',
  'DIRECTORY_PARENT',
  '/Users/mthanki/Downloads/inci-tennis-v1',
  '/Users/mthanki/Downloads'),
 ('LAUNCHER_0_LINK',
  'LINK_PARENT',
  '/Users/mthanki/.venvs/inci-expert-py314/bin/python',
  '/Users/mthanki/.venvs/inci-expert-py314/bin'),
 ('LAUNCHER_0_TARGET',
  'TARGET_PARENT',
  '/Users/mthanki/.venvs/inci-expert-py314/bin/python3.14',
  '/Users/mthanki/.venvs/inci-expert-py314/bin'),
 ('LAUNCHER_1_LINK',
  'LINK_PARENT',
  '/Users/mthanki/.venvs/inci-expert-py314/bin/python3.14',
  '/Users/mthanki/.venvs/inci-expert-py314/bin'),
 ('LAUNCHER_1_TARGET',
  'TARGET_PARENT',
  '/opt/homebrew/opt/python@3.14/bin/python3.14',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/bin'),
 ('LAUNCHER_2_LINK', 'LINK_PARENT', '/opt/homebrew/opt/python@3.14', '/opt/homebrew/opt'),
 ('LAUNCHER_2_TARGET',
  'TARGET_PARENT',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/bin/python3.14',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/bin'),
 ('LAUNCHER_3_LINK',
  'LINK_PARENT',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/bin/python3.14',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/bin'),
 ('LAUNCHER_3_TARGET',
  'TARGET_PARENT',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/bin/python3.14',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/bin'),
 ('PURELIB_ROOT',
  'DIRECTORY_PARENT',
  '/Users/mthanki/.venvs/inci-expert-py314/lib/python3.14/site-packages',
  '/Users/mthanki/.venvs/inci-expert-py314/lib/python3.14'),
 ('PYVENV_CONFIG',
  'FILE_PARENT',
  '/Users/mthanki/.venvs/inci-expert-py314/pyvenv.cfg',
  '/Users/mthanki/.venvs/inci-expert-py314'),
 ('RESOLVED_STDLIB',
  'DIRECTORY_PARENT',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib'),
 ('RUNTIME_A_LINK',
  'LINK_PARENT',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14/config-3.14-darwin/libpython3.14.a',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14/config-3.14-darwin'),
 ('RUNTIME_A_TARGET',
  'TARGET_PARENT',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Python',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14'),
 ('RUNTIME_DYLIB_LINK',
  'LINK_PARENT',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14/config-3.14-darwin/libpython3.14.dylib',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14/config-3.14-darwin'),
 ('RUNTIME_DYLIB_TARGET',
  'TARGET_PARENT',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Python',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14'),
 ('RUNTIME_SITE_LINK',
  'LINK_PARENT',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14/site-packages',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14'),
 ('RUNTIME_SITE_TARGET',
  'TARGET_PARENT',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/lib/python3.14/site-packages',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/lib/python3.14'),
 ('STDLIB_DYNLOAD',
  'DIRECTORY_PARENT',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14/lib-dynload',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14'),
 ('STDLIB_ROOT_0_LINK', 'LINK_PARENT', '/opt/homebrew/opt/python@3.14', '/opt/homebrew/opt'),
 ('STDLIB_ROOT_0_TARGET',
  'TARGET_PARENT',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14',
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib'),
 ('VENV_PURELIB',
  'DIRECTORY_PARENT',
  '/Users/mthanki/.venvs/inci-expert-py314/lib/python3.14/site-packages',
  '/Users/mthanki/.venvs/inci-expert-py314/lib/python3.14'))

_TASK9_PATH_COMPONENT_ROWS_V1: Final[tuple[tuple[object, ...], ...]] = (('ABSENT_STDLIB_ZIP', 0, '/'),
 ('ABSENT_STDLIB_ZIP', 1, '/opt'),
 ('ABSENT_STDLIB_ZIP', 2, '/opt/homebrew'),
 ('ABSENT_STDLIB_ZIP', 3, '/opt/homebrew/Cellar'),
 ('ABSENT_STDLIB_ZIP', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('ABSENT_STDLIB_ZIP', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('ABSENT_STDLIB_ZIP', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks'),
 ('ABSENT_STDLIB_ZIP', 7, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework'),
 ('ABSENT_STDLIB_ZIP',
  8,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions'),
 ('ABSENT_STDLIB_ZIP',
  9,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14'),
 ('ABSENT_STDLIB_ZIP',
  10,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib'),
 ('COMMAND_CWD', 0, '/'),
 ('COMMAND_CWD', 1, '/Users'),
 ('COMMAND_CWD', 2, '/Users/mthanki'),
 ('COMMAND_CWD', 3, '/Users/mthanki/Downloads'),
 ('LAUNCHER_0_LINK', 0, '/'),
 ('LAUNCHER_0_LINK', 1, '/Users'),
 ('LAUNCHER_0_LINK', 2, '/Users/mthanki'),
 ('LAUNCHER_0_LINK', 3, '/Users/mthanki/.venvs'),
 ('LAUNCHER_0_LINK', 4, '/Users/mthanki/.venvs/inci-expert-py314'),
 ('LAUNCHER_0_LINK', 5, '/Users/mthanki/.venvs/inci-expert-py314/bin'),
 ('LAUNCHER_0_TARGET', 0, '/'),
 ('LAUNCHER_0_TARGET', 1, '/Users'),
 ('LAUNCHER_0_TARGET', 2, '/Users/mthanki'),
 ('LAUNCHER_0_TARGET', 3, '/Users/mthanki/.venvs'),
 ('LAUNCHER_0_TARGET', 4, '/Users/mthanki/.venvs/inci-expert-py314'),
 ('LAUNCHER_0_TARGET', 5, '/Users/mthanki/.venvs/inci-expert-py314/bin'),
 ('LAUNCHER_1_LINK', 0, '/'),
 ('LAUNCHER_1_LINK', 1, '/Users'),
 ('LAUNCHER_1_LINK', 2, '/Users/mthanki'),
 ('LAUNCHER_1_LINK', 3, '/Users/mthanki/.venvs'),
 ('LAUNCHER_1_LINK', 4, '/Users/mthanki/.venvs/inci-expert-py314'),
 ('LAUNCHER_1_LINK', 5, '/Users/mthanki/.venvs/inci-expert-py314/bin'),
 ('LAUNCHER_1_TARGET', 0, '/'),
 ('LAUNCHER_1_TARGET', 1, '/opt'),
 ('LAUNCHER_1_TARGET', 2, '/opt/homebrew'),
 ('LAUNCHER_1_TARGET', 3, '/opt/homebrew/Cellar'),
 ('LAUNCHER_1_TARGET', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('LAUNCHER_1_TARGET', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('LAUNCHER_1_TARGET', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/bin'),
 ('LAUNCHER_2_LINK', 0, '/'),
 ('LAUNCHER_2_LINK', 1, '/opt'),
 ('LAUNCHER_2_LINK', 2, '/opt/homebrew'),
 ('LAUNCHER_2_LINK', 3, '/opt/homebrew/opt'),
 ('LAUNCHER_2_TARGET', 0, '/'),
 ('LAUNCHER_2_TARGET', 1, '/opt'),
 ('LAUNCHER_2_TARGET', 2, '/opt/homebrew'),
 ('LAUNCHER_2_TARGET', 3, '/opt/homebrew/Cellar'),
 ('LAUNCHER_2_TARGET', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('LAUNCHER_2_TARGET', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('LAUNCHER_2_TARGET', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/bin'),
 ('LAUNCHER_3_LINK', 0, '/'),
 ('LAUNCHER_3_LINK', 1, '/opt'),
 ('LAUNCHER_3_LINK', 2, '/opt/homebrew'),
 ('LAUNCHER_3_LINK', 3, '/opt/homebrew/Cellar'),
 ('LAUNCHER_3_LINK', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('LAUNCHER_3_LINK', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('LAUNCHER_3_LINK', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/bin'),
 ('LAUNCHER_3_TARGET', 0, '/'),
 ('LAUNCHER_3_TARGET', 1, '/opt'),
 ('LAUNCHER_3_TARGET', 2, '/opt/homebrew'),
 ('LAUNCHER_3_TARGET', 3, '/opt/homebrew/Cellar'),
 ('LAUNCHER_3_TARGET', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('LAUNCHER_3_TARGET', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('LAUNCHER_3_TARGET', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks'),
 ('LAUNCHER_3_TARGET', 7, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework'),
 ('LAUNCHER_3_TARGET',
  8,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions'),
 ('LAUNCHER_3_TARGET',
  9,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14'),
 ('LAUNCHER_3_TARGET',
  10,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/bin'),
 ('PURELIB_ROOT', 0, '/'),
 ('PURELIB_ROOT', 1, '/Users'),
 ('PURELIB_ROOT', 2, '/Users/mthanki'),
 ('PURELIB_ROOT', 3, '/Users/mthanki/.venvs'),
 ('PURELIB_ROOT', 4, '/Users/mthanki/.venvs/inci-expert-py314'),
 ('PURELIB_ROOT', 5, '/Users/mthanki/.venvs/inci-expert-py314/lib'),
 ('PURELIB_ROOT', 6, '/Users/mthanki/.venvs/inci-expert-py314/lib/python3.14'),
 ('PYVENV_CONFIG', 0, '/'),
 ('PYVENV_CONFIG', 1, '/Users'),
 ('PYVENV_CONFIG', 2, '/Users/mthanki'),
 ('PYVENV_CONFIG', 3, '/Users/mthanki/.venvs'),
 ('PYVENV_CONFIG', 4, '/Users/mthanki/.venvs/inci-expert-py314'),
 ('RESOLVED_STDLIB', 0, '/'),
 ('RESOLVED_STDLIB', 1, '/opt'),
 ('RESOLVED_STDLIB', 2, '/opt/homebrew'),
 ('RESOLVED_STDLIB', 3, '/opt/homebrew/Cellar'),
 ('RESOLVED_STDLIB', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('RESOLVED_STDLIB', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('RESOLVED_STDLIB', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks'),
 ('RESOLVED_STDLIB', 7, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework'),
 ('RESOLVED_STDLIB',
  8,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions'),
 ('RESOLVED_STDLIB',
  9,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14'),
 ('RESOLVED_STDLIB',
  10,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib'),
 ('RUNTIME_A_LINK', 0, '/'),
 ('RUNTIME_A_LINK', 1, '/opt'),
 ('RUNTIME_A_LINK', 2, '/opt/homebrew'),
 ('RUNTIME_A_LINK', 3, '/opt/homebrew/Cellar'),
 ('RUNTIME_A_LINK', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('RUNTIME_A_LINK', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('RUNTIME_A_LINK', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks'),
 ('RUNTIME_A_LINK', 7, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework'),
 ('RUNTIME_A_LINK',
  8,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions'),
 ('RUNTIME_A_LINK',
  9,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14'),
 ('RUNTIME_A_LINK',
  10,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib'),
 ('RUNTIME_A_LINK',
  11,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14'),
 ('RUNTIME_A_LINK',
  12,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14/config-3.14-darwin'),
 ('RUNTIME_A_TARGET', 0, '/'),
 ('RUNTIME_A_TARGET', 1, '/opt'),
 ('RUNTIME_A_TARGET', 2, '/opt/homebrew'),
 ('RUNTIME_A_TARGET', 3, '/opt/homebrew/Cellar'),
 ('RUNTIME_A_TARGET', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('RUNTIME_A_TARGET', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('RUNTIME_A_TARGET', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks'),
 ('RUNTIME_A_TARGET', 7, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework'),
 ('RUNTIME_A_TARGET',
  8,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions'),
 ('RUNTIME_A_TARGET',
  9,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14'),
 ('RUNTIME_DYLIB_LINK', 0, '/'),
 ('RUNTIME_DYLIB_LINK', 1, '/opt'),
 ('RUNTIME_DYLIB_LINK', 2, '/opt/homebrew'),
 ('RUNTIME_DYLIB_LINK', 3, '/opt/homebrew/Cellar'),
 ('RUNTIME_DYLIB_LINK', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('RUNTIME_DYLIB_LINK', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('RUNTIME_DYLIB_LINK', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks'),
 ('RUNTIME_DYLIB_LINK', 7, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework'),
 ('RUNTIME_DYLIB_LINK',
  8,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions'),
 ('RUNTIME_DYLIB_LINK',
  9,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14'),
 ('RUNTIME_DYLIB_LINK',
  10,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib'),
 ('RUNTIME_DYLIB_LINK',
  11,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14'),
 ('RUNTIME_DYLIB_LINK',
  12,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14/config-3.14-darwin'),
 ('RUNTIME_DYLIB_TARGET', 0, '/'),
 ('RUNTIME_DYLIB_TARGET', 1, '/opt'),
 ('RUNTIME_DYLIB_TARGET', 2, '/opt/homebrew'),
 ('RUNTIME_DYLIB_TARGET', 3, '/opt/homebrew/Cellar'),
 ('RUNTIME_DYLIB_TARGET', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('RUNTIME_DYLIB_TARGET', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('RUNTIME_DYLIB_TARGET', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks'),
 ('RUNTIME_DYLIB_TARGET', 7, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework'),
 ('RUNTIME_DYLIB_TARGET',
  8,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions'),
 ('RUNTIME_DYLIB_TARGET',
  9,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14'),
 ('RUNTIME_SITE_LINK', 0, '/'),
 ('RUNTIME_SITE_LINK', 1, '/opt'),
 ('RUNTIME_SITE_LINK', 2, '/opt/homebrew'),
 ('RUNTIME_SITE_LINK', 3, '/opt/homebrew/Cellar'),
 ('RUNTIME_SITE_LINK', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('RUNTIME_SITE_LINK', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('RUNTIME_SITE_LINK', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks'),
 ('RUNTIME_SITE_LINK', 7, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework'),
 ('RUNTIME_SITE_LINK',
  8,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions'),
 ('RUNTIME_SITE_LINK',
  9,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14'),
 ('RUNTIME_SITE_LINK',
  10,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib'),
 ('RUNTIME_SITE_LINK',
  11,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14'),
 ('RUNTIME_SITE_TARGET', 0, '/'),
 ('RUNTIME_SITE_TARGET', 1, '/opt'),
 ('RUNTIME_SITE_TARGET', 2, '/opt/homebrew'),
 ('RUNTIME_SITE_TARGET', 3, '/opt/homebrew/Cellar'),
 ('RUNTIME_SITE_TARGET', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('RUNTIME_SITE_TARGET', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('RUNTIME_SITE_TARGET', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/lib'),
 ('RUNTIME_SITE_TARGET', 7, '/opt/homebrew/Cellar/python@3.14/3.14.5/lib/python3.14'),
 ('STDLIB_DYNLOAD', 0, '/'),
 ('STDLIB_DYNLOAD', 1, '/opt'),
 ('STDLIB_DYNLOAD', 2, '/opt/homebrew'),
 ('STDLIB_DYNLOAD', 3, '/opt/homebrew/Cellar'),
 ('STDLIB_DYNLOAD', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('STDLIB_DYNLOAD', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('STDLIB_DYNLOAD', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks'),
 ('STDLIB_DYNLOAD', 7, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework'),
 ('STDLIB_DYNLOAD',
  8,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions'),
 ('STDLIB_DYNLOAD',
  9,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14'),
 ('STDLIB_DYNLOAD',
  10,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib'),
 ('STDLIB_DYNLOAD',
  11,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14'),
 ('STDLIB_ROOT_0_LINK', 0, '/'),
 ('STDLIB_ROOT_0_LINK', 1, '/opt'),
 ('STDLIB_ROOT_0_LINK', 2, '/opt/homebrew'),
 ('STDLIB_ROOT_0_LINK', 3, '/opt/homebrew/opt'),
 ('STDLIB_ROOT_0_TARGET', 0, '/'),
 ('STDLIB_ROOT_0_TARGET', 1, '/opt'),
 ('STDLIB_ROOT_0_TARGET', 2, '/opt/homebrew'),
 ('STDLIB_ROOT_0_TARGET', 3, '/opt/homebrew/Cellar'),
 ('STDLIB_ROOT_0_TARGET', 4, '/opt/homebrew/Cellar/python@3.14'),
 ('STDLIB_ROOT_0_TARGET', 5, '/opt/homebrew/Cellar/python@3.14/3.14.5'),
 ('STDLIB_ROOT_0_TARGET', 6, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks'),
 ('STDLIB_ROOT_0_TARGET', 7, '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework'),
 ('STDLIB_ROOT_0_TARGET',
  8,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions'),
 ('STDLIB_ROOT_0_TARGET',
  9,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14'),
 ('STDLIB_ROOT_0_TARGET',
  10,
  '/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib'),
 ('VENV_PURELIB', 0, '/'),
 ('VENV_PURELIB', 1, '/Users'),
 ('VENV_PURELIB', 2, '/Users/mthanki'),
 ('VENV_PURELIB', 3, '/Users/mthanki/.venvs'),
 ('VENV_PURELIB', 4, '/Users/mthanki/.venvs/inci-expert-py314'),
 ('VENV_PURELIB', 5, '/Users/mthanki/.venvs/inci-expert-py314/lib'),
 ('VENV_PURELIB', 6, '/Users/mthanki/.venvs/inci-expert-py314/lib/python3.14'))


_TASK9_FIXED_ENVIRONMENT_ROWS_V1: Final[tuple[tuple[str, str], ...]] = (
    ("INCI_DEMO_DISABLED", "1"),
    ("INCI_LIVE_DISABLED", "1"),
    ("INCI_NETWORK_DISABLED", "1"),
    ("INCI_ORDER_EXECUTION_DISABLED", "1"),
    ("LANG", "C"),
    ("LC_ALL", "C"),
    ("PYTHONDONTWRITEBYTECODE", "1"),
    ("PYTHONHASHSEED", "0"),
    ("PYTHONIOENCODING", "utf-8:strict"),
    ("PYTHONNOUSERSITE", "1"),
    ("PYTHONUNBUFFERED", "1"),
    ("PYTHONUTF8", "1"),
    ("TMPDIR", "/tmp"),
    ("TZ", "UTC"),
)


_TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_PROJECTION_V1: Final[
    dict[str, object]
] = {
    "schema_version": 1,
    "policy_id": "TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_V1",
    "default_directory_rule": (
        "OWNER_UID_ZERO_OR_EFFECTIVE_UID_AND_NO_GROUP_OR_OTHER_WRITE"
    ),
    "effective_group_membership_source": "MACOS_OPENDIRECTORY_GETGROUPLIST",
    "group_id_normalization": "SIGNED_INT32_INPUT_UINT32_OUTPUT",
    "exact_group_member_count": 2,
    "exception_rows": (
        ("/opt/homebrew/Cellar", "EFFECTIVE_UID", 80, "0775"),
        ("/opt/homebrew/opt", "EFFECTIVE_UID", 80, "0775"),
    ),
    "required_group_member_role_uids": ("ROOT_UID", "EFFECTIVE_UID"),
    "bind_resolved_member_names": True,
    "require_before_after_equality": True,
    "require_complete_passwd_universe": True,
    "require_distinct_role_uids": True,
    "require_empty_primary_gid_member_rows": True,
    "require_zero_membership_query_errors": True,
}
_TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_BYTES_V1: Final[bytes] = (
    _canonical_json_bytes(
        _TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_PROJECTION_V1
    )
)
if (
    len(_TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_BYTES_V1) != 744
    or hashlib.sha256(
        _TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_BYTES_V1
    ).hexdigest()
    != "8731428ad93bab8c9c190c4e61f5a41b003cec33fc472030145d7733ef9f1962"
):
    raise Task9TransitionEvidenceError("task9_bootstrap_homebrew_policy_invalid")
TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_V1: Final[
    Task9TrustedHomebrewComponentModePolicyV1
] = Task9TrustedHomebrewComponentModePolicyV1(
    **_TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_PROJECTION_V1,
    policy_sha256=_task9_bootstrap_domain_sha256_v1(
        "INCI-TASK-9-TRUSTED-HOMEBREW-COMPONENT-MODE-POLICY-V1",
        _TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_PROJECTION_V1,
    ),
)
if (
    TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_V1.policy_sha256
    != "a0d56a544d01190f251b6253c83ca25aeb8c5765871435d2a1ca363ea0ea51d6"
):
    raise Task9TransitionEvidenceError("task9_bootstrap_homebrew_policy_invalid")


_TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_PROJECTION_V1: Final[
    dict[str, object]
] = {
    "schema_version": 1,
    "witness_id": "TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_V1",
    "passwd_raw_row_count": 267,
    "passwd_raw_canonical_bytes": 21_762,
    "passwd_raw_rows_sha256": (
        "f6fe0a487223c6f2744b996f82aafc3cec775321285ad13e07388086e728977e"
    ),
    "passwd_unique_row_count": 135,
    "passwd_unique_canonical_bytes": 10_994,
    "passwd_unique_rows_sha256": (
        "1473637d6258b4652e1a1fcecf9e779c9d5f3ab34de83c0f41a31407edac99d1"
    ),
    "effective_group_access_row_count": 135,
    "effective_group_access_canonical_bytes": 14_217,
    "effective_group_access_rows_sha256": (
        "30f8ea202ae4dc31a5e96a2e8cdbb1d25dd455db7629e5259168671c28861381"
    ),
}
_TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_BYTES_V1: Final[bytes] = (
    _canonical_json_bytes(
        _TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_PROJECTION_V1
    )
)
if (
    len(_TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_BYTES_V1) != 594
    or hashlib.sha256(
        _TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_BYTES_V1
    ).hexdigest()
    != "a080ddcb439edf1c6c52209ff42751160e94614f31fb9cc41d20e87a3be97945"
):
    raise Task9TransitionEvidenceError("task9_bootstrap_homebrew_witness_invalid")
TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_V1: Final[
    Task9InstalledHostPasswdGroupAccessWitnessV1
] = Task9InstalledHostPasswdGroupAccessWitnessV1(
    **_TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_PROJECTION_V1,
    witness_sha256=_task9_bootstrap_domain_sha256_v1(
        "INCI-TASK-9-INSTALLED-HOST-PASSWD-GROUP-ACCESS-WITNESS-V1",
        _TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_PROJECTION_V1,
    ),
)
if (
    TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_V1.witness_sha256
    != "8312e51848e7c7ebd477fe1d79f62c8a0c28f4a18031c77a60769b35f87fa519"
):
    raise Task9TransitionEvidenceError("task9_bootstrap_homebrew_witness_invalid")
_TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_SINGLETON_V1 = (
    TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_V1
)


TASK9_INTERPRETER_PATH_CLOSURE_ALLOWANCE_V1: Final[
    Task9InterpreterPathClosureAllowanceV1
] = Task9InterpreterPathClosureAllowanceV1(
    schema_version=1,
    allowance_id="TASK9_INTERPRETER_PATH_CLOSURE_ALLOWANCE_V1",
    max_symlink_hops=8,
    path_hop_rows=(
        ("LAUNCHER", 0, "/Users/mthanki/.venvs/inci-expert-py314/bin/python", "python3.14", "/Users/mthanki/.venvs/inci-expert-py314/bin/python3.14"),
        ("LAUNCHER", 1, "/Users/mthanki/.venvs/inci-expert-py314/bin/python3.14", "/opt/homebrew/opt/python@3.14/bin/python3.14", "/opt/homebrew/opt/python@3.14/bin/python3.14"),
        ("LAUNCHER", 2, "/opt/homebrew/opt/python@3.14", "../Cellar/python@3.14/3.14.5", "/opt/homebrew/Cellar/python@3.14/3.14.5/bin/python3.14"),
        ("LAUNCHER", 3, "/opt/homebrew/Cellar/python@3.14/3.14.5/bin/python3.14", "../Frameworks/Python.framework/Versions/3.14/bin/python3.14", "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/bin/python3.14"),
        ("STDLIB_ROOT", 0, "/opt/homebrew/opt/python@3.14", "../Cellar/python@3.14/3.14.5", "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14"),
    ),
    zero_hop_scopes=(("PURELIB_ROOT", "/Users/mthanki/.venvs/inci-expert-py314/lib/python3.14/site-packages"),),
    runtime_symlink_rows=(
        ("config-3.14-darwin/libpython3.14.a", "../../../Python", "REGULAR", "CPYTHON_FRAMEWORK_BINARY", "DATA"),
        ("config-3.14-darwin/libpython3.14.dylib", "../../../Python", "REGULAR", "CPYTHON_FRAMEWORK_BINARY", "EXTENSION"),
        ("site-packages", "../../../../../../lib/python3.14/site-packages", "DIRECTORY", "BASE_PURELIB_ROOT", None),
    ),
    regular_target_rows=(("CPYTHON_FRAMEWORK_BINARY", "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Python"),),
    excluded_directory_target_rows=(("BASE_PURELIB_ROOT", "/opt/homebrew/Cellar/python@3.14/3.14.5/lib/python3.14/site-packages"),),
    allowance_sha256="137d6b7d31f5e992b307a2cc4e6f32c5f2e4ccbf3052434006ddb62a6b6be6d2",
)

TASK9_PYVENV_CONFIG_POLICY_V1: Final[Task9PyvenvConfigPolicyV1] = (
    Task9PyvenvConfigPolicyV1(
        schema_version=1,
        policy_id="TASK9_PYVENV_CONFIG_POLICY_V1",
        path="/Users/mthanki/.venvs/inci-expert-py314/pyvenv.cfg",
        content_size=308,
        content_sha256="5b4e9e15d664eaf4b663b4849b61242aecb485beb3091b2595b545f933d88095",
        encoding="STRICT_UTF8",
        line_ending="LF_ONLY",
        terminal_lf=True,
        parsed_rows=(
            ("home", "/opt/homebrew/opt/python@3.14/bin"),
            ("include-system-site-packages", "false"),
            ("version", "3.14.5"),
            ("executable", "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/bin/python3.14"),
            ("command", "/opt/homebrew/opt/python@3.14/bin/python3.14 -m venv /Users/mthanki/.venvs/inci-expert-py314"),
        ),
        include_system_site_packages=False,
        policy_sha256="024b655a2538071f6ad7d351724e3282a5e64716bc2fb176c2eb12cbbd7f7b60",
    )
)

TASK9_SANITIZED_IMPORT_SEARCH_PATH_POLICY_V1: Final[
    Task9ImportSearchPathPolicyV1
] = Task9ImportSearchPathPolicyV1(
    schema_version=1,
    policy_id="TASK9_SANITIZED_IMPORT_SEARCH_PATH_POLICY_V1",
    exact_row_count=5,
    rows=(
        (0, "/Users/mthanki/Downloads/inci-tennis-v1", "COMMAND_CWD", "PRESENT"),
        (1, "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python314.zip", "ABSENT_STDLIB_ZIP", "ABSENT"),
        (2, "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14", "RESOLVED_STDLIB", "PRESENT"),
        (3, "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/lib/python3.14/lib-dynload", "STDLIB_DYNLOAD", "PRESENT"),
        (4, "/Users/mthanki/.venvs/inci-expert-py314/lib/python3.14/site-packages", "VENV_PURELIB", "PRESENT"),
    ),
    excluded_base_purelib_path="/opt/homebrew/Cellar/python@3.14/3.14.5/lib/python3.14/site-packages",
    excluded_base_purelib_relation="DISTINCT_FROM_VENV_PURELIB",
    excluded_base_purelib_search_state="ABSENT",
    policy_sha256="4fc73c4632f1af17183e3d164bdefad21955eabd4b7c248e797d28242f466b79",
)

TASK9_PATH_ENDPOINT_PARENT_ALLOWANCE_V1: Final[
    Task9PathEndpointParentAllowanceV1
] = Task9PathEndpointParentAllowanceV1(
    schema_version=1,
    policy_id="TASK9_PATH_ENDPOINT_PARENT_ALLOWANCE_V1",
    exact_row_count=23,
    rows=_TASK9_PATH_ENDPOINT_ROWS_V1,
    policy_sha256="d3606e079f9b28a376b686cf7cb9bcc0fac20953ca26ab4f05ab00fcb1abf452",
)

TASK9_PATH_COMPONENT_ALLOWANCE_V1: Final[
    Task9PathComponentAllowanceV1
] = Task9PathComponentAllowanceV1(
    schema_version=1,
    policy_id="TASK9_PATH_COMPONENT_ALLOWANCE_V1",
    exact_row_count=192,
    rows=_TASK9_PATH_COMPONENT_ROWS_V1,
    policy_sha256="40398598a215c9a31fbda2bf12e23814c4eefdc8fe60100860aebd0a8763b6af",
)

TASK9_BOOTSTRAP_PROBE_ENVIRONMENT_POLICY_V1: Final[
    Task9BootstrapProbeEnvironmentPolicyV1
] = Task9BootstrapProbeEnvironmentPolicyV1(
    schema_version=1,
    policy_id="TASK9_BOOTSTRAP_PROBE_ENVIRONMENT_POLICY_V1",
    inherit_parent_environment=False,
    probe_kinds=("UNITTEST_MODULE", "FROZEN_V6_SCRIPT"),
    probe_row_name="INCI_TASK9_BOOTSTRAP_PATH_PROBE",
    fixed_rows=_TASK9_FIXED_ENVIRONMENT_ROWS_V1,
    dynamic_rows=(
        ("HOME", "/tmp/inci-task9-home-bootstrap-probe-<probe-kind-lower>-<positive-allocation-coordinate>"),
        ("PYTHONPYCACHEPREFIX", "/tmp/inci-task9-pycache-bootstrap-probe-<probe-kind-lower>-<positive-allocation-coordinate>"),
    ),
    policy_sha256="97ca402820fcef9cd516b9b1814757f19cee110ee8d362bedcdc242f2f4bfdbb",
)

TASK9_BOOTSTRAP_PRODUCTION_REGISTRIES_V1: Final[tuple[()]] = ()
TASK9_BOOTSTRAP_NETWORK_CAPABILITIES_V1: Final[tuple[()]] = ()
TASK9_BOOTSTRAP_NETWORK_CALL_PATHS_V1: Final[tuple[()]] = ()


def _task9_bootstrap_policy_v1(cls: type, domain: str, projection: dict[str, object]) -> object:
    return cls(**projection, policy_sha256=_task9_bootstrap_domain_sha256_v1(domain, projection))


TASK9_COMMAND_CWD_POLICY_V1: Final[Task9CommandCwdPolicyV1] = _task9_bootstrap_policy_v1(
    Task9CommandCwdPolicyV1,
    "INCI-TASK-9-COMMAND-CWD-POLICY-V1",
    {"schema_version": 1, "policy_id": "TASK9_MODULE_ORIGIN_COMMAND_CWD_V1", "root_binding_policy_sha256": TASK9_MODULE_ORIGIN_ROOT_BINDING_POLICY_V1.policy_sha256, "cwd_source": "MODULE_ORIGIN_EVIDENCE_ROOT", "shell_allowed": False},
)
TASK9_COMMAND_ENVIRONMENT_POLICY_V1: Final[Task9CommandEnvironmentPolicyV1] = _task9_bootstrap_policy_v1(
    Task9CommandEnvironmentPolicyV1,
    "INCI-TASK-9-COMMAND-ENVIRONMENT-POLICY-V1",
    {"schema_version": 1, "policy_id": "TASK9_OFFLINE_TEST_ENVIRONMENT_V1", "inherit_parent_environment": False, "fixed_rows": _TASK9_FIXED_ENVIRONMENT_ROWS_V1, "dynamic_row_names": ("HOME", "PYTHONPYCACHEPREFIX"), "pycache_policy": "COMMAND_UNIQUE_CODE_OWNED_DIRECTORY_UNDER_/tmp", "forbidden_capabilities": ("CREDENTIAL", "NETWORK", "PROVIDER_ACCOUNT", "PORTFOLIO", "DEMO", "LIVE", "EXECUTION", "ORDER")},
)
TASK9_COMMAND_STREAM_POLICY_V1: Final[Task9CommandStreamPolicyV1] = _task9_bootstrap_policy_v1(
    Task9CommandStreamPolicyV1,
    "INCI-TASK-9-COMMAND-STREAM-POLICY-V1",
    {"schema_version": 1, "policy_id": "TASK9_MERGED_BOUNDED_UTF8_STREAM_V1", "stderr_mode": "MERGED_INTO_STDOUT", "output_cap_bytes": 1_048_576, "decoding": "STRICT_UTF8", "newline_policy": "LF_ONLY", "nul_allowed": False},
)


COMMAND_EXECUTION_44: Final[tuple[str, ...]] = (
    "inci_tennis_adapters/registry.py",
    "inci_tennis_expert/digest_registry.py", "inci_tennis_expert/mailbox.py",
    "inci_tennis_io/account_lock.py", "inci_tennis_io/expert_journal_store.py",
    "inci_tennis_io/research_runtime_config.py", "inci_tennis_runtime/bootstrap.py",
    "inci_tennis_runtime/config.py", "inci_tennis_runtime/expert_controller.py",
    "inci_tennis_runtime/schemas/research-runtime-config-v1.schema.json",
    "inci_tennis_runtime/shadow_activation.py", "inci_tennis_runtime/shadow_cli.py",
    "inci_tennis_runtime/shadow_mailbox.py", "inci_tennis_runtime/shadow_runtime.py",
    "inci_tennis_runtime/shadow_sources.py", "pyproject.toml", "tennis_v1/entitlements.py",
    "tennis_v1/ingress.py", "tests/tennis_v1/shadow_fixture_support.py",
    "tests/tennis_v1/support/shadow_cleanup_oracle_support.py",
    "tests/tennis_v1/test_account_lock.py", "tests/tennis_v1/test_durable_parent_bridge.py",
    "tests/tennis_v1/test_entitlements.py", "tests/tennis_v1/test_expert_controller.py",
    "tests/tennis_v1/test_expert_dependency_boundary.py",
    "tests/tennis_v1/test_expert_journal_store.py",
    "tests/tennis_v1/test_expert_runtime_config.py", "tests/tennis_v1/test_ingress.py",
    "tests/tennis_v1/test_preflight.py", "tests/tennis_v1/test_production_account_lock.py",
    "tests/tennis_v1/test_research_runtime_config_io.py",
    "tests/tennis_v1/test_shadow_activation.py", "tests/tennis_v1/test_shadow_bootstrap.py",
    "tests/tennis_v1/test_shadow_capacity.py", "tests/tennis_v1/test_shadow_cli.py",
    "tests/tennis_v1/test_shadow_digest_registry.py", "tests/tennis_v1/test_shadow_mailbox.py",
    "tests/tennis_v1/test_shadow_mailbox_contracts.py",
    "tests/tennis_v1/test_shadow_precredential_entitlement.py",
    "tests/tennis_v1/test_shadow_recorded_fixtures.py",
    "tests/tennis_v1/test_shadow_runtime.py", "tests/tennis_v1/test_shadow_sources.py",
    "tests/tennis_v1/test_task9_transition_evidence.py", "tools/task9_transition_evidence.py",
)


TASK9_COMPLETE_REPOSITORY_DEPENDENCY_SUPERSET_V1: Final[tuple[str, ...]] = (
    ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/review-baselines/task-0-fix1-test_expert_dependency_boundary.py",
    ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/review-baselines/task-0-fix2-test_expert_dependency_boundary.py",
    ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/review-baselines/task-0-fix3-test_expert_dependency_boundary.py",
    ".superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/review-baselines/task-0-review1-test_expert_dependency_boundary.py",
    "analyze.py", "bot.py", "config.py", "engine.py", "executor.py", "fees.py",
    "inci_tennis_adapters/__init__.py", "inci_tennis_adapters/candidate_contracts.py",
    "inci_tennis_adapters/kalshi_candidate.py", "inci_tennis_adapters/registry.py",
    "inci_tennis_adapters/schemas/kalshi-market-lifecycle-synthetic-candidate-v1.schema.json",
    "inci_tennis_adapters/schemas/kalshi-orderbook-delta-synthetic-candidate-v1.schema.json",
    "inci_tennis_adapters/schemas/kalshi-orderbook-snapshot-synthetic-candidate-v1.schema.json",
    "inci_tennis_adapters/schemas/kalshi-public-trade-synthetic-candidate-v1.schema.json",
    "inci_tennis_adapters/schemas/sportradar-tennis-candidate-authorization-v1.schema.json",
    "inci_tennis_adapters/schemas/sportradar-tennis-candidate-manifest-v1.schema.json",
    "inci_tennis_adapters/schemas/sportradar-tennis-qualification-output-v1.schema.json",
    "inci_tennis_adapters/schemas/sportradar-tennis-summary-v3-candidate-v1.schema.json",
    "inci_tennis_adapters/schemas/sportradar-tennis-timeline-v3-candidate-v1.schema.json",
    "inci_tennis_adapters/schemas/sportradar-tennis-transport-error-v1.schema.json",
    "inci_tennis_adapters/sportradar_tennis_v3.py",
    "inci_tennis_expert/__init__.py", "inci_tennis_expert/contracts.py",
    "inci_tennis_expert/digest_registry.py", "inci_tennis_expert/facade.py",
    "inci_tennis_expert/journal_codec.py", "inci_tennis_expert/mailbox.py",
    "inci_tennis_expert/market_book.py", "inci_tennis_expert/match_binding.py",
    "inci_tennis_expert/observation.py", "inci_tennis_expert/reducer.py",
    "inci_tennis_expert/replay.py",
    "inci_tennis_expert/schemas/binding-review-v1.schema.json",
    "inci_tennis_expert/schemas/expert-journal-group-v1.schema.json",
    "inci_tennis_expert/schemas/expert-journal-record-v1.schema.json",
    "inci_tennis_expert/schemas/expert-observation-ignored-v1.schema.json",
    "inci_tennis_expert/schemas/expert-observation-rejected-v1.schema.json",
    "inci_tennis_expert/schemas/expert-session-manifest-v1.schema.json",
    "inci_tennis_expert/schemas/expert-session-terminal-v1.schema.json",
    "inci_tennis_expert/schemas/expert-synchronization-applied-v1.schema.json",
    "inci_tennis_expert/schemas/match-binding-v1.schema.json",
    "inci_tennis_expert/schemas/task6-fallback-no-payload-v1.schema.json",
    "inci_tennis_expert/state.py", "inci_tennis_expert/synchronizer.py",
    "inci_tennis_expert/task6_fallback_normalizer.py", "inci_tennis_expert/tennis_score.py",
    "inci_tennis_io/__init__.py", "inci_tennis_io/account_lock.py",
    "inci_tennis_io/expert_journal_store.py", "inci_tennis_io/facade.py",
    "inci_tennis_io/pinned_artifacts.py", "inci_tennis_io/ports.py",
    "inci_tennis_io/provider_readonly.py", "inci_tennis_io/research_runtime_config.py",
    "inci_tennis_runtime/__init__.py", "inci_tennis_runtime/bootstrap.py",
    "inci_tennis_runtime/config.py", "inci_tennis_runtime/expert_controller.py",
    "inci_tennis_runtime/provider_qualification_controller.py",
    "inci_tennis_runtime/replay_service.py",
    "inci_tennis_runtime/schemas/research-runtime-config-v1.schema.json",
    "inci_tennis_runtime/shadow_activation.py", "inci_tennis_runtime/shadow_cli.py",
    "inci_tennis_runtime/shadow_mailbox.py", "inci_tennis_runtime/shadow_runtime.py",
    "inci_tennis_runtime/shadow_sources.py", "kalshi_client.py", "market_data.py",
    "order_journal.py", "order_resolution.py", "pnl_ledger.py", "process_lock.py",
    "provider_manifest.example.json", "pyproject.toml", "replay.py", "research_log.py",
    "safety.py", "schemas.py", "signals.py", "sports_discovery.py", "strategy.py",
    "tennis_v1/__init__.py", "tennis_v1/adapter_contract.py", "tennis_v1/canonical.py",
    "tennis_v1/capture.py", "tennis_v1/codec.py", "tennis_v1/config.py",
    "tennis_v1/entitlements.py", "tennis_v1/events.py", "tennis_v1/fingerprints.py",
    "tennis_v1/ingress.py", "tennis_v1/mailbox.py", "tennis_v1/pinned_file.py",
    "tennis_v1/preflight.py", "tennis_v1/qualification_protocol.py",
    "tennis_v1/reducer.py", "tennis_v1/replay_core.py", "tennis_v1/retention.py",
    "tennis_v1/schemas/provider-entitlement-v1.schema.json",
    "tennis_v1/schemas/provider-permission-v1.schema.json",
    "tennis_v1/schemas/provider-qualification-trace-v1.schema.json",
    "tennis_v1/schemas/provider-qualification-v1.schema.json",
    "tennis_v1/schemas/retention-marker-v1.schema.json",
    "tennis_v1/sequencer.py", "tennis_v1/session.py", "tennis_v1/state.py",
    "tennis_v1/wal.py", "tests.py", "tests/__init__.py",
    "tests/tennis_v1/__init__.py",
    "tests/tennis_v1/fixtures/binding_review_schema_example.json",
    "tests/tennis_v1/fixtures/kalshi_market_lifecycle_synthetic_candidate_v1.json",
    "tests/tennis_v1/fixtures/kalshi_orderbook_delta_synthetic_candidate_v1.json",
    "tests/tennis_v1/fixtures/kalshi_orderbook_delta_v2.json",
    "tests/tennis_v1/fixtures/kalshi_orderbook_snapshot_synthetic_candidate_v1.json",
    "tests/tennis_v1/fixtures/kalshi_orderbook_snapshot_v2.json",
    "tests/tennis_v1/fixtures/kalshi_public_trade_synthetic_candidate_v1.json",
    "tests/tennis_v1/fixtures/match_binding_schema_example.json",
    "tests/tennis_v1/fixtures/provider_manifest_schema_example.json",
    "tests/tennis_v1/fixtures/provider_permission_schema_example.json",
    "tests/tennis_v1/fixtures/provider_qualification_schema_example.json",
    "tests/tennis_v1/fixtures/provider_qualification_trace_schema_example.json",
    "tests/tennis_v1/fixtures/sportradar_tennis_summary_v3.json",
    "tests/tennis_v1/fixtures/sportradar_tennis_timeline_v3.json",
    "tests/tennis_v1/fixtures/synthetic_adapter.py",
    "tests/tennis_v1/shadow_fixture_support.py",
    "tests/tennis_v1/sportradar_candidate_fixture_support.py",
    "tests/tennis_v1/support/shadow_cleanup_oracle_support.py",
    "tests/tennis_v1/test_account_lock.py", "tests/tennis_v1/test_adapter_contract.py",
    "tests/tennis_v1/test_canonical.py", "tests/tennis_v1/test_capture.py",
    "tests/tennis_v1/test_codec.py", "tests/tennis_v1/test_config.py",
    "tests/tennis_v1/test_dependency_boundary.py",
    "tests/tennis_v1/test_durable_parent_bridge.py",
    "tests/tennis_v1/test_entitlements.py", "tests/tennis_v1/test_events.py",
    "tests/tennis_v1/test_expert_contracts.py",
    "tests/tennis_v1/test_expert_controller.py",
    "tests/tennis_v1/test_expert_dependency_boundary.py",
    "tests/tennis_v1/test_expert_journal_codec.py",
    "tests/tennis_v1/test_expert_journal_store.py",
    "tests/tennis_v1/test_expert_observation.py",
    "tests/tennis_v1/test_expert_reducer.py", "tests/tennis_v1/test_expert_replay.py",
    "tests/tennis_v1/test_expert_runtime_config.py",
    "tests/tennis_v1/test_fingerprints.py", "tests/tennis_v1/test_ingress.py",
    "tests/tennis_v1/test_kalshi_candidate.py",
    "tests/tennis_v1/test_legacy_baseline.py", "tests/tennis_v1/test_mailbox.py",
    "tests/tennis_v1/test_market_book.py", "tests/tennis_v1/test_match_binding.py",
    "tests/tennis_v1/test_pinned_file.py", "tests/tennis_v1/test_preflight.py",
    "tests/tennis_v1/test_production_account_lock.py",
    "tests/tennis_v1/test_reducer.py", "tests/tennis_v1/test_replay_core.py",
    "tests/tennis_v1/test_research_runtime_config_io.py",
    "tests/tennis_v1/test_retention.py", "tests/tennis_v1/test_sequencer.py",
    "tests/tennis_v1/test_shadow_activation.py",
    "tests/tennis_v1/test_shadow_bootstrap.py", "tests/tennis_v1/test_shadow_capacity.py",
    "tests/tennis_v1/test_shadow_cli.py", "tests/tennis_v1/test_shadow_digest_registry.py",
    "tests/tennis_v1/test_shadow_mailbox.py",
    "tests/tennis_v1/test_shadow_mailbox_contracts.py",
    "tests/tennis_v1/test_shadow_precredential_entitlement.py",
    "tests/tennis_v1/test_shadow_recorded_fixtures.py",
    "tests/tennis_v1/test_shadow_runtime.py", "tests/tennis_v1/test_shadow_sources.py",
    "tests/tennis_v1/test_sportradar_tennis_v3.py",
    "tests/tennis_v1/test_sportradar_tennis_v3_acceptance_matrix.py",
    "tests/tennis_v1/test_sportradar_tennis_v3_controller_matrix.py",
    "tests/tennis_v1/test_sportradar_tennis_v3_output_schema.py",
    "tests/tennis_v1/test_sportradar_tennis_v3_parser_matrix.py",
    "tests/tennis_v1/test_sportradar_tennis_v3_qualification_matrix.py",
    "tests/tennis_v1/test_sportradar_tennis_v3_store_protocol.py",
    "tests/tennis_v1/test_synchronizer.py",
    "tests/tennis_v1/test_task9_transition_evidence.py",
    "tests/tennis_v1/test_tennis_score.py", "tests/tennis_v1/test_wal.py",
    "tools/qualify_sportradar_tennis_v3.py", "tools/task9_transition_evidence.py",
    "tools/verify_runtime.py",
)


_TASK9_BOOTSTRAP_AUTHORITY_COORDINATE = 0
_TASK9_BOOTSTRAP_ACTIVE_LEASE_ID: int | None = None
_TASK9_BOOTSTRAP_ROOT_LEDGER: dict[int, dict[str, object]] = {}
_TASK9_BOOTSTRAP_LEASE_LEDGER: dict[int, dict[str, object]] = {}
_TASK9_BOOTSTRAP_FIXED_TEST_TARGET_V1 = (
    "tests.tennis_v1.test_task9_transition_evidence."
    "Round19CommandEvidenceBootstrapTests."
    "test_bootstrap_dependency_genesis_and_empty_first_antecedent_are_exact"
)
_TASK9_BOOTSTRAP_UNITTEST_ARGV_V1 = (
    "/Users/mthanki/.venvs/inci-expert-py314/bin/python", "-B", "-m",
    "unittest", _TASK9_BOOTSTRAP_FIXED_TEST_TARGET_V1,
)
_TASK9_BOOTSTRAP_FROZEN_V6_ARGV_V1 = (
    "/Users/mthanki/.venvs/inci-expert-py314/bin/python", "-B", "tests.py",
)
_TASK9_BOOTSTRAP_UNITTEST_PROBE_V1: Final[object] = object()
_TASK9_BOOTSTRAP_FROZEN_V6_PROBE_V1: Final[object] = object()
_TASK9_PATH_CLOSURE_OS_V1: Final[object] = _os
_TASK9_IMPORT_SEARCH_SYS_V1 = _sys
_TASK9_HOMEBREW_OS_V1 = _os
_TASK9_HOMEBREW_PWD_V1 = _pwd
_TASK9_HOMEBREW_GRP_V1 = _grp
_TASK9_HOMEBREW_GETEUID_V1 = _os.geteuid
_TASK9_HOMEBREW_GETGROUPLIST_V1 = _os.getgrouplist
_TASK9_HOMEBREW_GETPWALL_V1 = _pwd.getpwall
_TASK9_HOMEBREW_GETPWNAM_V1 = _pwd.getpwnam
_TASK9_HOMEBREW_GETGRGID_V1 = _grp.getgrgid


def _task9_bootstrap_full_stat_identity_v1(value: _os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
        value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns,
    )


class _Task9ObservedDescriptorV1:
    __slots__ = ("_fd", "_path")

    def __init__(self, fd: int, path: str) -> None:
        if type(fd) is not int or fd < 0 or type(path) is not str:
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_filesystem_invalid"
            )
        self._fd = fd
        self._path = path

    def __index__(self) -> int:
        return self._fd

    def __fspath__(self) -> str:
        return self._path


def _task9_bootstrap_descriptor_path_v1(fd: int) -> str:
    try:
        raw = _fcntl.fcntl(fd, _fcntl.F_GETPATH, b"\0" * 1024)
        if type(raw) is not bytes or b"\0" not in raw:
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_filesystem_invalid"
            )
        path_bytes, padding = raw.split(b"\0", 1)
        if not path_bytes or any(padding):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_filesystem_invalid"
            )
        path = path_bytes.decode("utf-8", "strict")
    except Task9TransitionEvidenceError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_filesystem_invalid"
        ) from None
    if not path.startswith("/") or "\0" in path:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_filesystem_invalid"
        )
    return path


def _task9_bootstrap_observed_descriptor_v1(
    fd: int,
) -> _Task9ObservedDescriptorV1:
    return _Task9ObservedDescriptorV1(
        fd, _task9_bootstrap_descriptor_path_v1(fd)
    )


def _task9_gid_u32_to_i32_v1(value: object) -> int:
    if type(value) is not int or not 0 <= value < 4_294_967_296:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    return value if value <= 2_147_483_647 else value - 4_294_967_296


def _task9_normalize_getgrouplist_gid_v1(value: object) -> int:
    if type(value) is not int:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    native_u = 2 * (_sys.maxsize + 1)
    if (
        type(_sys.maxsize) is not int
        or _sys.maxsize <= 0
        or native_u not in (4_294_967_296, 18_446_744_073_709_551_616)
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    if 0 <= value < 4_294_967_296:
        return value
    if -2_147_483_648 <= value < 0:
        return value + 4_294_967_296
    if (
        native_u > 4_294_967_296
        and native_u - 2_147_483_648 <= value < native_u
    ):
        return value - native_u + 4_294_967_296
    raise Task9TransitionEvidenceError(
        "task9_bootstrap_homebrew_membership_invalid"
    )


def _task9_homebrew_passwd_row_v1(value: object) -> tuple[object, ...]:
    if type(value) is not _pwd.struct_passwd:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    row = (
        value.pw_name,
        value.pw_passwd,
        value.pw_uid,
        value.pw_gid,
        value.pw_gecos,
        value.pw_dir,
        value.pw_shell,
    )
    for cell in (row[0], row[1], row[4], row[5], row[6]):
        if (
            type(cell) is not str
            or "\0" in cell
            or not cell.isascii()
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_homebrew_membership_invalid"
            )
    for cell in (row[2], row[3]):
        if type(cell) is not int or not 0 <= cell < 4_294_967_296:
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_homebrew_membership_invalid"
            )
    return row


def _task9_homebrew_group_row_v1(value: object) -> tuple[object, ...]:
    if type(value) is not _grp.struct_group or type(value.gr_mem) is not list:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    if (
        type(value.gr_name) is not str
        or type(value.gr_passwd) is not str
        or "\0" in value.gr_name
        or "\0" in value.gr_passwd
        or not value.gr_name.isascii()
        or not value.gr_passwd.isascii()
        or type(value.gr_gid) is not int
        or value.gr_gid != 80
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    members = tuple(value.gr_mem)
    if any(
        type(name) is not str or not name or "\0" in name or not name.isascii()
        for name in members
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    sorted_members = tuple(sorted(members, key=lambda name: name.encode("ascii")))
    if len(set(sorted_members)) != len(sorted_members):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    return (value.gr_name, value.gr_passwd, value.gr_gid, sorted_members)


def _task9_homebrew_rows_coordinates_v1(
    rows: tuple[object, ...],
) -> tuple[int, int, str, bytes]:
    encoded = _canonical_json_bytes(rows)
    return len(rows), len(encoded), hashlib.sha256(encoded).hexdigest(), encoded


def _task9_homebrew_require_witness_match_v1(
    raw_rows: tuple[tuple[object, ...], ...],
    unique_rows: tuple[tuple[object, ...], ...],
    access_rows: tuple[tuple[object, ...], ...],
) -> tuple[
    tuple[int, int, str, bytes],
    tuple[int, int, str, bytes],
    tuple[int, int, str, bytes],
]:
    raw = _task9_homebrew_rows_coordinates_v1(raw_rows)
    unique = _task9_homebrew_rows_coordinates_v1(unique_rows)
    access = _task9_homebrew_rows_coordinates_v1(access_rows)
    witness = TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_V1
    if (
        raw[:3]
        != (
            witness.passwd_raw_row_count,
            witness.passwd_raw_canonical_bytes,
            witness.passwd_raw_rows_sha256,
        )
        or unique[:3]
        != (
            witness.passwd_unique_row_count,
            witness.passwd_unique_canonical_bytes,
            witness.passwd_unique_rows_sha256,
        )
        or access[:3]
        != (
            witness.effective_group_access_row_count,
            witness.effective_group_access_canonical_bytes,
            witness.effective_group_access_rows_sha256,
        )
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    return raw, unique, access


def _task9_bootstrap_capture_trusted_homebrew_component_mode_evidence_v1(
) -> Task9TrustedHomebrewComponentModeEvidenceV1:
    os_api = _TASK9_HOMEBREW_OS_V1
    pwd_api = _TASK9_HOMEBREW_PWD_V1
    grp_api = _TASK9_HOMEBREW_GRP_V1
    try:
        identity_exact = (
            os_api is _os
            and pwd_api is _pwd
            and grp_api is _grp
            and os_api.geteuid is _TASK9_HOMEBREW_GETEUID_V1
            and os_api.getgrouplist is _TASK9_HOMEBREW_GETGROUPLIST_V1
            and pwd_api.getpwall is _TASK9_HOMEBREW_GETPWALL_V1
            and pwd_api.getpwnam is _TASK9_HOMEBREW_GETPWNAM_V1
            and grp_api.getgrgid is _TASK9_HOMEBREW_GETGRGID_V1
        )
    except Exception:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        ) from None
    if (
        TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_V1
        is not _TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_SINGLETON_V1
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    try:
        raw_source = pwd_api.getpwall()
        if type(raw_source) is not list or len(raw_source) > 1024:
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_homebrew_membership_invalid"
            )
        projected = tuple(_task9_homebrew_passwd_row_v1(row) for row in raw_source)
        raw_rows = tuple(
            row
            for _encoded, row in sorted(
                ((_canonical_json_bytes(row), row) for row in projected),
                key=lambda item: item[0],
            )
        )
        unique_by_bytes: dict[bytes, tuple[object, ...]] = {}
        for row in raw_rows:
            unique_by_bytes.setdefault(_canonical_json_bytes(row), row)
        unique_rows = tuple(unique_by_bytes[key] for key in sorted(unique_by_bytes))

        names: dict[str, list[tuple[object, ...]]] = {}
        uids: dict[int, list[tuple[object, ...]]] = {}
        for row in unique_rows:
            names.setdefault(row[0], []).append(row)
            uids.setdefault(row[2], []).append(row)
        name_conflicts = tuple(
            (name, tuple(rows))
            for name, rows in sorted(names.items())
            if len(rows) != 1
        )
        uid_conflicts = tuple(
            (uid, tuple(rows))
            for uid, rows in sorted(uids.items())
            if len(rows) != 1
        )
        euid = os_api.geteuid()
        if (
            type(euid) is not int
            or euid <= 0
            or name_conflicts
            or uid_conflicts
            or len(uids.get(0, ())) != 1
            or len(uids.get(euid, ())) != 1
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_homebrew_membership_invalid"
            )
        root_row = uids[0][0]
        effective_row = uids[euid][0]
        if root_row == effective_row or root_row[0] == effective_row[0]:
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_homebrew_membership_invalid"
            )
        primary_gid_rows = tuple(row for row in unique_rows if row[3] == 80)
        if primary_gid_rows:
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_homebrew_membership_invalid"
            )

        group_row = _task9_homebrew_group_row_v1(grp_api.getgrgid(80))
        required_names = tuple(
            sorted((root_row[0], effective_row[0]), key=lambda name: name.encode("ascii"))
        )
        if group_row[3] != required_names:
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_homebrew_membership_invalid"
            )
        member_resolutions: list[tuple[object, ...]] = []
        role_by_name = {
            root_row[0]: ("ROOT_UID", root_row),
            effective_row[0]: ("EFFECTIVE_UID", effective_row),
        }
        for name in required_names:
            resolved = _task9_homebrew_passwd_row_v1(pwd_api.getpwnam(name))
            role, expected = role_by_name[name]
            if resolved != expected:
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_homebrew_membership_invalid"
                )
            member_resolutions.append((name, role, resolved))

        access_rows_list: list[tuple[object, ...]] = []
        for row in unique_rows:
            returned = os_api.getgrouplist(row[0], _task9_gid_u32_to_i32_v1(row[3]))
            if type(returned) is not list or not returned or len(returned) > 4096:
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_homebrew_membership_invalid"
                )
            gids = tuple(sorted(_task9_normalize_getgrouplist_gid_v1(cell) for cell in returned))
            if len(set(gids)) != len(gids) or row[3] not in gids:
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_homebrew_membership_invalid"
                )
            access_rows_list.append((row, gids))
        access_rows = tuple(access_rows_list)
        gid80_access_rows = tuple(item for item in access_rows if 80 in item[1])
        gid80_rows = tuple(item[0] for item in gid80_access_rows)
        if (
            len(gid80_rows) != 2
            or set(gid80_rows) != {root_row, effective_row}
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_homebrew_membership_invalid"
            )

        raw_coordinates, unique_coordinates, access_coordinates = (
            _task9_homebrew_require_witness_match_v1(
                raw_rows, unique_rows, access_rows
            )
        )
        component_rows: list[Task9TrustedHomebrewComponentRowV1] = []
        for path in ("/opt/homebrew/Cellar", "/opt/homebrew/opt"):
            identity, entries_sha = _task9_bootstrap_directory_entries_v1(path)
            if (
                identity[3] != euid
                or identity[4] != 80
                or _stat.S_IMODE(identity[2]) != 0o775
            ):
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_homebrew_membership_invalid"
                )
            component_rows.append(
                Task9TrustedHomebrewComponentRowV1(
                    path=path,
                    owner_role="EFFECTIVE_UID",
                    stat_identity=identity,
                    entries_sha256=entries_sha,
                )
            )
        projection = {
            "schema_version": 1,
            "policy_sha256": (
                TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_V1.policy_sha256
            ),
            "installed_host_witness": (
                TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_V1
            ),
            "passwd_raw_row_count": raw_coordinates[0],
            "passwd_raw_canonical_bytes": raw_coordinates[1],
            "passwd_raw_rows_sha256": raw_coordinates[2],
            "passwd_raw_rows": raw_rows,
            "passwd_unique_row_count": unique_coordinates[0],
            "passwd_unique_canonical_bytes": unique_coordinates[1],
            "passwd_unique_rows_sha256": unique_coordinates[2],
            "passwd_unique_rows": unique_rows,
            "passwd_name_conflict_rows": name_conflicts,
            "passwd_uid_conflict_rows": uid_conflicts,
            "root_role_passwd_row": root_row,
            "effective_uid_role_passwd_row": effective_row,
            "gid80_group_row": group_row,
            "gid80_member_resolution_rows": tuple(member_resolutions),
            "primary_gid_member_rows": primary_gid_rows,
            "effective_group_access_row_count": access_coordinates[0],
            "effective_group_access_canonical_bytes": access_coordinates[1],
            "effective_group_access_rows_sha256": access_coordinates[2],
            "effective_group_access_rows": access_rows,
            "membership_query_error_rows": (),
            "effective_gid80_member_rows": gid80_rows,
            "builtin_identity_rows": (
                ("grp.getgrgid", "CODE_OWNED_STDLIB_BUILTIN"),
                ("os.geteuid", "CODE_OWNED_STDLIB_BUILTIN"),
                ("os.getgrouplist", "CODE_OWNED_STDLIB_BUILTIN"),
                ("pwd.getpwall", "CODE_OWNED_STDLIB_BUILTIN"),
                ("pwd.getpwnam", "CODE_OWNED_STDLIB_BUILTIN"),
            ),
            "component_rows": tuple(component_rows),
        }
        if len(_canonical_json_bytes(_task9_bootstrap_projection_v1(projection))) > 150_994_944:
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_homebrew_membership_invalid"
            )
        if not identity_exact:
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_homebrew_membership_invalid"
            )
        return Task9TrustedHomebrewComponentModeEvidenceV1(
            **projection,
            evidence_sha256=_task9_bootstrap_domain_sha256_v1(
                "INCI-TASK-9-TRUSTED-HOMEBREW-COMPONENT-MODE-EVIDENCE-V1",
                projection,
            ),
        )
    except Task9TransitionEvidenceError:
        raise
    except (KeyError, OSError, OverflowError, TypeError, ValueError, UnicodeError):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        ) from None


def _task9_bootstrap_directory_entries_v1(
    path: str,
) -> tuple[tuple[int, ...], str]:
    os_api = _TASK9_PATH_CLOSURE_OS_V1
    fd = -1
    try:
        fd = _task9_bootstrap_open_absolute_v1(path, directory=True)
        observed_fd = _task9_bootstrap_observed_descriptor_v1(fd)
        before = os_api.fstat(observed_fd)
        homebrew_exception = (
            path in ("/opt/homebrew/Cellar", "/opt/homebrew/opt")
            and before.st_uid == _os.geteuid()
            and before.st_gid == 80
            and _stat.S_IMODE(before.st_mode) == 0o775
        )
        if (
            not _stat.S_ISDIR(before.st_mode)
            or before.st_uid not in (_os.geteuid(), 0)
            or (before.st_mode & 0o022 and not homebrew_exception)
            or before.st_nlink < 1
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_path_closure_invalid"
            )
        captures: list[tuple[tuple[str, str], ...]] = []
        for _capture_index in range(2):
            rows: list[tuple[str, str]] = []
            names = tuple(sorted(os_api.listdir(observed_fd)))
            if any(type(name) is not str or "\0" in name for name in names):
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_path_closure_invalid"
            )
            for name in names:
                child_path = (
                    f"/{name}" if path == "/" else f"{path}/{name}"
                )
                value = os_api.stat(child_path, follow_symlinks=False)
                if _stat.S_ISDIR(value.st_mode):
                    kind = "DIRECTORY"
                elif _stat.S_ISREG(value.st_mode):
                    kind = "REGULAR"
                elif _stat.S_ISLNK(value.st_mode):
                    kind = "SYMLINK"
                else:
                    kind = "SPECIAL"
                rows.append((name, kind))
            captures.append(tuple(rows))
        after = os_api.fstat(observed_fd)
        identity = _task9_bootstrap_full_stat_identity_v1(before)
        if (
            identity != _task9_bootstrap_full_stat_identity_v1(after)
            or captures[0] != captures[1]
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_path_closure_drift"
            )
        return identity, _task9_bootstrap_domain_sha256_v1(
            "INCI-TASK-9-DIRECTORY-ENTRIES-V1", captures[0]
        )
    except Task9TransitionEvidenceError:
        raise
    except OSError:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_path_closure_invalid"
        ) from None
    finally:
        if fd >= 0:
            os_api.close(fd)


def _task9_bootstrap_directory_structural_identity_v1(
    value: _os.stat_result, entries: tuple[tuple[str, str], ...]
) -> tuple[object, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
        value.st_nlink,
        _task9_bootstrap_domain_sha256_v1("INCI-TASK-9-DIRECTORY-ENTRIES-V1", entries),
    )


def _task9_bootstrap_read_descriptor_v1(fd: int, *, cap: int = 64 * 1024 * 1024) -> tuple[_os.stat_result, bytes]:
    os_api = _TASK9_PATH_CLOSURE_OS_V1
    observed_fd = _task9_bootstrap_observed_descriptor_v1(fd)
    before = os_api.fstat(observed_fd)
    if (
        not _stat.S_ISREG(before.st_mode)
        or before.st_size < 0
        or before.st_size > cap
        or before.st_mode & 0o022
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_filesystem_invalid")
    captures: list[bytes] = []
    identities = [_task9_bootstrap_full_stat_identity_v1(before)]
    for _capture_index in range(2):
        os_api.lseek(observed_fd, 0, _os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os_api.read(observed_fd, min(remaining, 1_048_576))
            if not chunk:
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_filesystem_invalid"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        captures.append(b"".join(chunks))
        identities.append(
            _task9_bootstrap_full_stat_identity_v1(
                os_api.fstat(observed_fd)
            )
        )
    if len(set(identities)) != 1 or captures[0] != captures[1]:
        raise Task9TransitionEvidenceError("task9_bootstrap_filesystem_drift")
    return os_api.fstat(observed_fd), captures[0]


def _task9_bootstrap_open_absolute_v1(path: str, *, directory: bool) -> int:
    if type(path) is not str or not path.startswith("/"):
        raise Task9TransitionEvidenceError("task9_bootstrap_filesystem_invalid")
    os_api = _TASK9_PATH_CLOSURE_OS_V1
    current = os_api.open(
        "/", _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC
    )
    try:
        components = tuple(item for item in path.split("/") if item)
        absolute = ""
        for index, component in enumerate(components):
            final = index == len(components) - 1
            absolute = f"{absolute}/{component}"
            flags = _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_CLOEXEC
            if not final or directory:
                flags |= _os.O_DIRECTORY
            next_fd = os_api.open(absolute, flags)
            os_api.close(current)
            current = next_fd
        return current
    except Exception:
        os_api.close(current)
        raise


def _task9_bootstrap_origin_authority_record_v1() -> tuple[int, tuple[int, ...], tuple[int, ...], str]:
    origin = getattr(globals().get("__spec__"), "origin", None)
    if (
        type(__file__) is not str
        or type(origin) is not str
        or __file__ != origin
        or __file__ != _TASK9_BOOTSTRAP_LOADED_ORIGIN_V1
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_root_invalid")
    if not __file__.startswith("/") or tuple(__file__.split("/")[-2:]) != ("tools", "task9_transition_evidence.py"):
        raise Task9TransitionEvidenceError("task9_bootstrap_root_invalid")
    slash_fd = current_fd = tools_fd = module_fd = root_fd = reopened_fd = -1
    try:
        slash_fd = _os.open("/", _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC)
        current_fd = slash_fd
        components = tuple(item for item in __file__.split("/") if item)
        for component in components[:-1]:
            next_fd = _os.open(component, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC, dir_fd=current_fd)
            if current_fd != slash_fd:
                _os.close(current_fd)
            current_fd = next_fd
        tools_fd = current_fd
        current_fd = -1
        module_fd = _os.open(components[-1], _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_CLOEXEC, dir_fd=tools_fd)
        module_stat, module_bytes = _task9_bootstrap_read_descriptor_v1(module_fd, cap=16_777_216)
        if module_stat.st_uid != _os.geteuid() or module_stat.st_nlink != 1:
            raise Task9TransitionEvidenceError("task9_bootstrap_root_invalid")
        root_fd = _os.open("..", _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC, dir_fd=tools_fd)
        root_before = _os.fstat(root_fd)
        if not _stat.S_ISDIR(root_before.st_mode) or root_before.st_uid != _os.geteuid() or root_before.st_mode & 0o022:
            raise Task9TransitionEvidenceError("task9_bootstrap_root_invalid")
        reopened_fd = _task9_open_relative_nofollow(root_fd, "tools/task9_transition_evidence.py")
        reopened_stat, reopened_bytes = _task9_bootstrap_read_descriptor_v1(reopened_fd, cap=16_777_216)
        root_after = _os.fstat(root_fd)
        if (
            _task9_bootstrap_full_stat_identity_v1(module_stat) != _task9_bootstrap_full_stat_identity_v1(reopened_stat)
            or module_bytes != reopened_bytes
            or _task9_bootstrap_full_stat_identity_v1(root_before) != _task9_bootstrap_full_stat_identity_v1(root_after)
        ):
            raise Task9TransitionEvidenceError("task9_bootstrap_root_invalid")
        retained = root_fd
        root_fd = -1
        return retained, _task9_bootstrap_full_stat_identity_v1(root_after), _task9_bootstrap_full_stat_identity_v1(module_stat), hashlib.sha256(module_bytes).hexdigest()
    except Exception:
        raise Task9TransitionEvidenceError("task9_bootstrap_root_invalid") from None
    finally:
        closed: set[int] = set()
        for fd in (reopened_fd, root_fd, module_fd, tools_fd, current_fd, slash_fd):
            if fd >= 0 and fd not in closed:
                closed.add(fd)
                _os.close(fd)


def _issue_task9_evidence_root_authority_v1() -> Task9EvidenceRootAuthorityV1:
    root_fd = -1
    try:
        root_fd, root_identity, module_identity, module_sha256 = _task9_bootstrap_origin_authority_record_v1()
        authority = Task9EvidenceRootAuthorityV1(_TASK9_AUTHORITY_TOKEN)
        global _TASK9_BOOTSTRAP_AUTHORITY_COORDINATE
        with _TASK9_EVIDENCE_LOCK:
            _TASK9_BOOTSTRAP_AUTHORITY_COORDINATE += 1
            coordinate = _TASK9_BOOTSTRAP_AUTHORITY_COORDINATE
            _task9_admit_live_record_v1(_TASK9_BOOTSTRAP_ROOT_LEDGER, authority, {
                "ref": _weakref.ref(authority), "root_fd": root_fd,
                "root_identity": root_identity, "module_identity": module_identity,
                "module_sha256": module_sha256,
                "policy": TASK9_MODULE_ORIGIN_ROOT_BINDING_POLICY_V1,
                "policy_sha256": TASK9_MODULE_ORIGIN_ROOT_BINDING_POLICY_V1.policy_sha256,
                "allocation_coordinate": coordinate, "euid": _os.geteuid(),
                "pid": _os.getpid(), "thread": _threading.get_ident(), "state": "FRESH",
            })
        root_fd = -1
        return authority
    finally:
        if root_fd >= 0:
            _os.close(root_fd)


def _task9_bootstrap_live_authority_v1(authority: object, *, state: str = "FRESH") -> dict[str, object]:
    if type(authority) is not Task9EvidenceRootAuthorityV1:
        raise Task9TransitionEvidenceError("task9_bootstrap_authority_invalid")
    with _TASK9_EVIDENCE_LOCK:
        record = _task9_get_live_record_v1(_TASK9_BOOTSTRAP_ROOT_LEDGER, authority)
        if (
            record is None or record["state"] != state or record["pid"] != _os.getpid()
            or record["thread"] != _threading.get_ident() or record["euid"] != _os.geteuid()
            or record["policy"] is not TASK9_MODULE_ORIGIN_ROOT_BINDING_POLICY_V1
        ):
            raise Task9TransitionEvidenceError("task9_bootstrap_authority_invalid")
        return record


def _revoke_task9_evidence_root_authority_v1(authority: Task9EvidenceRootAuthorityV1) -> None:
    record = _task9_bootstrap_live_authority_v1(authority)
    with _TASK9_EVIDENCE_LOCK:
        record["state"] = "REVOKED"
        _TASK9_BOOTSTRAP_ROOT_LEDGER.pop(id(authority), None)
    try:
        _os.close(record["root_fd"])
    except OSError:
        raise Task9TransitionEvidenceError("task9_bootstrap_root_release_uncertain") from None


class _Task9BootstrapMutationLeaseV1:
    __slots__ = ("__weakref__",)
    def __new__(cls, token: object = None):
        if token is not _TASK9_AUTHORITY_TOKEN:
            raise TypeError("task9_bootstrap_lease_invalid")
        return super().__new__(cls)


def _acquire_task9_bootstrap_mutation_lease_v1(
    authority: Task9EvidenceRootAuthorityV1,
) -> _Task9BootstrapMutationLeaseV1:
    record = _task9_bootstrap_live_authority_v1(authority)
    _task9_bootstrap_validate_authority_root_v1(record)
    global _TASK9_BOOTSTRAP_ACTIVE_LEASE_ID
    with _TASK9_EVIDENCE_LOCK:
        if _TASK9_BOOTSTRAP_ACTIVE_LEASE_ID is not None:
            raise Task9TransitionEvidenceError("task9_bootstrap_lease_busy")
        try:
            _fcntl.flock(record["root_fd"], _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError:
            raise Task9TransitionEvidenceError("task9_bootstrap_lease_busy") from None
        lease = _Task9BootstrapMutationLeaseV1(_TASK9_AUTHORITY_TOKEN)
        record["state"] = "LEASED"
        lease_record = {
            "ref": _weakref.ref(lease),
            "authority": authority,
            "authority_record": record,
            "pid": _os.getpid(),
            "thread": _threading.get_ident(),
            "state": "ACTIVE",
        }
        _TASK9_BOOTSTRAP_LEASE_LEDGER[id(lease)] = lease_record
        _TASK9_BOOTSTRAP_ACTIVE_LEASE_ID = id(lease)
        return lease


def _task9_bootstrap_live_lease_v1(lease: object) -> dict[str, object]:
    if type(lease) is not _Task9BootstrapMutationLeaseV1:
        raise Task9TransitionEvidenceError("task9_bootstrap_lease_invalid")
    record = _TASK9_BOOTSTRAP_LEASE_LEDGER.get(id(lease))
    if (
        record is None or record["ref"]() is not lease or record["state"] != "ACTIVE"
        or record["pid"] != _os.getpid() or record["thread"] != _threading.get_ident()
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_lease_invalid")
    return record


def _release_task9_bootstrap_mutation_lease_v1(
    lease: _Task9BootstrapMutationLeaseV1,
) -> None:
    record = _task9_bootstrap_live_lease_v1(lease)
    authority = record["authority"]
    authority_record = record["authority_record"]
    global _TASK9_BOOTSTRAP_ACTIVE_LEASE_ID
    try:
        _fcntl.flock(authority_record["root_fd"], _fcntl.LOCK_UN)
    except OSError:
        record["state"] = "RELEASE_UNCERTAIN"
        try:
            _os.close(authority_record["root_fd"])
        finally:
            _TASK9_BOOTSTRAP_ACTIVE_LEASE_ID = None
            _TASK9_BOOTSTRAP_LEASE_LEDGER.pop(id(lease), None)
            _TASK9_BOOTSTRAP_ROOT_LEDGER.pop(id(authority), None)
        raise Task9TransitionEvidenceError("task9_bootstrap_lease_release_uncertain") from None
    record["state"] = "RELEASED"
    _TASK9_BOOTSTRAP_ACTIVE_LEASE_ID = None
    _TASK9_BOOTSTRAP_LEASE_LEDGER.pop(id(lease), None)
    _TASK9_BOOTSTRAP_ROOT_LEDGER.pop(id(authority), None)
    try:
        _os.close(authority_record["root_fd"])
    except OSError:
        raise Task9TransitionEvidenceError("task9_bootstrap_lease_release_uncertain") from None


TASK9_NONSTAGE_EVIDENCE_OUTPUT_PATHS_V1: Final[tuple[str, ...]] = (
    "task-9-documentation-evidence-v1.json",
    "task-9-final-reseal-evidence-bundle-v1.json",
    "task-9-final-resource-seal-v1.json", "task-9-final-source-seal-v1.json",
    "task-9-functional-wave-a-evidence-bundle-v1.json",
    "task-9-functional-wave-a-resource-seal-v1.json",
    "task-9-functional-wave-a-source-seal-v1.json",
    "task-9-functional-wave-b-evidence-bundle-v1.json",
    "task-9-functional-wave-b-resource-seal-v1.json",
    "task-9-functional-wave-b-source-seal-v1.json",
    "task-9-functional-wave-c-evidence-bundle-v1.json",
    "task-9-functional-wave-c-resource-seal-v1.json",
    "task-9-functional-wave-c-source-seal-v1.json",
    "task-9-functional-wave-d-evidence-bundle-v1.json",
    "task-9-functional-wave-d-resource-seal-v1.json",
    "task-9-functional-wave-d-source-seal-v1.json",
    "task-9-functional-wave-e-evidence-bundle-v1.json",
    "task-9-functional-wave-e-resource-seal-v1.json",
    "task-9-functional-wave-e-source-seal-v1.json",
    "task-9-functional-wave-r-evidence-bundle-v1.json",
    "task-9-functional-wave-r-resource-seal-v1.json",
    "task-9-functional-wave-r-source-seal-v1.json",
    "task-9-predecessor-evidence-bundle-v1.json",
    "task-9-predecessor-resource-seal-v1.json",
    "task-9-predecessor-source-seal-v1.json",
    "task-9-release-support-evidence-bundle-v1.json",
)
_TASK9_BOOTSTRAP_OUTPUT_EXCLUSION_PATHS_V1: Final[tuple[str, ...]] = (
    "task-9-documentation-evidence-v1.json",
    "task-9-final-reseal-evidence-bundle-v1.json",
    "task-9-final-reseal-review-chain-acceptance-receipt-v1.json",
    "task-9-final-reseal-review-chain-acceptance-receipt-v1.json.tmp-v1",
    "task-9-final-reseal-review-procedural-assignment-write-receipt-v1.json",
    "task-9-final-reseal-review-procedural-assignment-write-receipt-v1.json.tmp-v1",
    "task-9-final-reseal-review-v1.json", "task-9-final-reseal-review-v1.json.tmp-v1",
    "task-9-final-reseal-transition-chain-acceptance-receipt-v1.json",
    "task-9-final-reseal-transition-chain-acceptance-receipt-v1.json.tmp-v1",
    "task-9-final-reseal-transition-procedural-assignment-write-receipt-v1.json",
    "task-9-final-reseal-transition-procedural-assignment-write-receipt-v1.json.tmp-v1",
    "task-9-final-reseal-transition-v1.json", "task-9-final-reseal-transition-v1.json.tmp-v1",
    "task-9-final-resource-seal-v1.json", "task-9-final-source-seal-v1.json",
    "task-9-functional-wave-a-evidence-bundle-v1.json",
    "task-9-functional-wave-a-resource-seal-v1.json",
    "task-9-functional-wave-a-source-seal-v1.json",
    "task-9-functional-wave-b-evidence-bundle-v1.json",
    "task-9-functional-wave-b-resource-seal-v1.json",
    "task-9-functional-wave-b-source-seal-v1.json",
    "task-9-functional-wave-c-evidence-bundle-v1.json",
    "task-9-functional-wave-c-resource-seal-v1.json",
    "task-9-functional-wave-c-source-seal-v1.json",
    "task-9-functional-wave-d-evidence-bundle-v1.json",
    "task-9-functional-wave-d-resource-seal-v1.json",
    "task-9-functional-wave-d-source-seal-v1.json",
    "task-9-functional-wave-e-evidence-bundle-v1.json",
    "task-9-functional-wave-e-resource-seal-v1.json",
    "task-9-functional-wave-e-source-seal-v1.json",
    "task-9-functional-wave-r-evidence-bundle-v1.json",
    "task-9-functional-wave-r-resource-seal-v1.json",
    "task-9-functional-wave-r-source-seal-v1.json",
    "task-9-functional-wave-review-a-chain-acceptance-receipt-v1.json",
    "task-9-functional-wave-review-a-chain-acceptance-receipt-v1.json.tmp-v1",
    "task-9-functional-wave-review-a-procedural-assignment-write-receipt-v1.json",
    "task-9-functional-wave-review-a-procedural-assignment-write-receipt-v1.json.tmp-v1",
    "task-9-functional-wave-review-a-v1.json", "task-9-functional-wave-review-a-v1.json.tmp-v1",
    "task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json",
    "task-9-functional-wave-review-b-chain-acceptance-receipt-v1.json.tmp-v1",
    "task-9-functional-wave-review-b-procedural-assignment-write-receipt-v1.json",
    "task-9-functional-wave-review-b-procedural-assignment-write-receipt-v1.json.tmp-v1",
    "task-9-functional-wave-review-b-v1.json", "task-9-functional-wave-review-b-v1.json.tmp-v1",
    "task-9-functional-wave-review-c-chain-acceptance-receipt-v1.json",
    "task-9-functional-wave-review-c-chain-acceptance-receipt-v1.json.tmp-v1",
    "task-9-functional-wave-review-c-procedural-assignment-write-receipt-v1.json",
    "task-9-functional-wave-review-c-procedural-assignment-write-receipt-v1.json.tmp-v1",
    "task-9-functional-wave-review-c-v1.json", "task-9-functional-wave-review-c-v1.json.tmp-v1",
    "task-9-functional-wave-review-d-chain-acceptance-receipt-v1.json",
    "task-9-functional-wave-review-d-chain-acceptance-receipt-v1.json.tmp-v1",
    "task-9-functional-wave-review-d-procedural-assignment-write-receipt-v1.json",
    "task-9-functional-wave-review-d-procedural-assignment-write-receipt-v1.json.tmp-v1",
    "task-9-functional-wave-review-d-v1.json", "task-9-functional-wave-review-d-v1.json.tmp-v1",
    "task-9-functional-wave-review-e-chain-acceptance-receipt-v1.json",
    "task-9-functional-wave-review-e-chain-acceptance-receipt-v1.json.tmp-v1",
    "task-9-functional-wave-review-e-procedural-assignment-write-receipt-v1.json",
    "task-9-functional-wave-review-e-procedural-assignment-write-receipt-v1.json.tmp-v1",
    "task-9-functional-wave-review-e-v1.json", "task-9-functional-wave-review-e-v1.json.tmp-v1",
    "task-9-functional-wave-review-r-chain-acceptance-receipt-v1.json",
    "task-9-functional-wave-review-r-chain-acceptance-receipt-v1.json.tmp-v1",
    "task-9-functional-wave-review-r-procedural-assignment-write-receipt-v1.json",
    "task-9-functional-wave-review-r-procedural-assignment-write-receipt-v1.json.tmp-v1",
    "task-9-functional-wave-review-r-v1.json", "task-9-functional-wave-review-r-v1.json.tmp-v1",
    "task-9-post-predecessor-amended-package-rereview-chain-acceptance-receipt-v1.json",
    "task-9-post-predecessor-amended-package-rereview-chain-acceptance-receipt-v1.json.tmp-v1",
    "task-9-post-predecessor-amended-package-rereview-procedural-assignment-write-receipt-v1.json",
    "task-9-post-predecessor-amended-package-rereview-procedural-assignment-write-receipt-v1.json.tmp-v1",
    "task-9-post-predecessor-amended-package-rereview-v1.json",
    "task-9-post-predecessor-amended-package-rereview-v1.json.tmp-v1",
    "task-9-predecessor-evidence-bundle-v1.json",
    "task-9-predecessor-resource-seal-v1.json", "task-9-predecessor-source-seal-v1.json",
    "task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json",
    "task-9-predecessor-transition-manifest-chain-acceptance-receipt-v1.json.tmp-v1",
    "task-9-predecessor-transition-manifest-procedural-assignment-write-receipt-v1.json",
    "task-9-predecessor-transition-manifest-procedural-assignment-write-receipt-v1.json.tmp-v1",
    "task-9-predecessor-transition-manifest-v1.json",
    "task-9-predecessor-transition-manifest-v1.json.tmp-v1",
    "task-9-predecessor-transition-review-chain-acceptance-receipt-v1.json",
    "task-9-predecessor-transition-review-chain-acceptance-receipt-v1.json.tmp-v1",
    "task-9-predecessor-transition-review-procedural-assignment-write-receipt-v1.json",
    "task-9-predecessor-transition-review-procedural-assignment-write-receipt-v1.json.tmp-v1",
    "task-9-predecessor-transition-review-v1.json",
    "task-9-predecessor-transition-review-v1.json.tmp-v1",
    "task-9-release-evidence-chain-acceptance-receipt-v1.json",
    "task-9-release-evidence-chain-acceptance-receipt-v1.json.tmp-v1",
    "task-9-release-evidence-procedural-assignment-write-receipt-v1.json",
    "task-9-release-evidence-procedural-assignment-write-receipt-v1.json.tmp-v1",
    "task-9-release-evidence-v1.json", "task-9-release-evidence-v1.json.tmp-v1",
    "task-9-release-support-evidence-bundle-v1.json",
)
_TASK9_BOOTSTRAP_OUTPUT_EXCLUSION_SET_V1 = frozenset(
    _TASK9_BOOTSTRAP_OUTPUT_EXCLUSION_PATHS_V1
)
_TASK9_BOOTSTRAP_EXCLUDED_DIRECTORY_NAMES_V1 = frozenset(
    (".git", ".venv", "Logs", "logs", "__pycache__")
)


def _task9_bootstrap_capture_regular_at_v1(
    directory_fd: int, name: str, relative_path: str
) -> tuple[Task9TreeInventoryRowV1, tuple[str, tuple[int, ...]], bytes]:
    fd = -1
    try:
        fd = _os.open(name, _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_CLOEXEC, dir_fd=directory_fd)
        value, content = _task9_bootstrap_read_descriptor_v1(fd)
        if value.st_uid not in (_os.geteuid(), 0) or value.st_nlink != 1:
            raise Task9TransitionEvidenceError("task9_bootstrap_dependency_invalid")
        digest = hashlib.sha256(content).hexdigest()
        return (
            Task9TreeInventoryRowV1(relative_path, "PRESENT", digest),
            (relative_path, _task9_bootstrap_full_stat_identity_v1(value)),
            content,
        )
    finally:
        if fd >= 0:
            _os.close(fd)


def _task9_bootstrap_walk_repository_v1(root_fd: int) -> tuple[
    dict[str, Task9TreeInventoryRowV1],
    dict[str, tuple[str, tuple[int, ...]]],
    tuple[tuple[str, tuple[object, ...]], ...],
    tuple[tuple[str, tuple[int, ...]], ...],
    dict[str, bytes],
]:
    discovered: dict[str, Task9TreeInventoryRowV1] = {}
    file_identities: dict[str, tuple[str, tuple[int, ...]]] = {}
    structural_directories: list[tuple[str, tuple[object, ...]]] = []
    execution_directories: list[tuple[str, tuple[int, ...]]] = []
    retained: dict[str, bytes] = {}

    def walk(directory_fd: int, relative_directory: str) -> None:
        before = _os.fstat(directory_fd)
        if not _stat.S_ISDIR(before.st_mode) or before.st_uid != _os.geteuid() or before.st_mode & 0o022:
            raise Task9TransitionEvidenceError("task9_bootstrap_dependency_invalid")
        names = tuple(sorted(_os.listdir(directory_fd)))
        filtered_entries: list[tuple[str, str]] = []
        for name in names:
            relative_path = name if not relative_directory else f"{relative_directory}/{name}"
            if name in _TASK9_BOOTSTRAP_EXCLUDED_DIRECTORY_NAMES_V1:
                value = _os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if _stat.S_ISDIR(value.st_mode):
                    continue
            if relative_path in _TASK9_BOOTSTRAP_OUTPUT_EXCLUSION_SET_V1:
                continue
            value = _os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if _stat.S_ISLNK(value.st_mode):
                raise Task9TransitionEvidenceError("task9_bootstrap_dependency_invalid")
            elif _stat.S_ISDIR(value.st_mode):
                filtered_entries.append((name, "DIRECTORY"))
                child_fd = _os.open(name, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC, dir_fd=directory_fd)
                try:
                    walk(child_fd, relative_path)
                finally:
                    _os.close(child_fd)
            elif _stat.S_ISREG(value.st_mode):
                filtered_entries.append((name, "REGULAR"))
                if relative_path.endswith((".py", ".json", ".toml")):
                    row, identity, content = _task9_bootstrap_capture_regular_at_v1(directory_fd, name, relative_path)
                    if relative_path in discovered:
                        raise Task9TransitionEvidenceError("task9_bootstrap_dependency_invalid")
                    discovered[relative_path] = row
                    file_identities[relative_path] = identity
                    retained[relative_path] = content
            else:
                raise Task9TransitionEvidenceError("task9_bootstrap_dependency_invalid")
        after = _os.fstat(directory_fd)
        if _task9_bootstrap_full_stat_identity_v1(before) != _task9_bootstrap_full_stat_identity_v1(after):
            raise Task9TransitionEvidenceError("task9_bootstrap_dependency_drift")
        key = "." if not relative_directory else relative_directory
        structural_directories.append((key, _task9_bootstrap_directory_structural_identity_v1(after, tuple(filtered_entries))))
        execution_directories.append((key, _task9_bootstrap_full_stat_identity_v1(after)))

    duplicate = _os.dup(root_fd)
    try:
        walk(duplicate, "")
    finally:
        _os.close(duplicate)
    return discovered, file_identities, tuple(sorted(structural_directories)), tuple(sorted(execution_directories)), retained


def _task9_bootstrap_validate_repository_ast_v1(retained: dict[str, bytes], root_fd: int) -> None:
    admitted_external_roots: set[str] = set()
    import_search_bindings: list[tuple[str, int]] = []
    import_search_loads: list[tuple[str, int]] = []
    stdlib = frozenset(_sys.stdlib_module_names)
    repository_roots = frozenset(
        path.split("/", 1)[0].removesuffix(".py")
        for path in TASK9_COMPLETE_REPOSITORY_DEPENDENCY_SUPERSET_V1
    )
    external_mapping = {
        "certifi": "certifi", "cffi": "cffi", "charset_normalizer": "charset-normalizer",
        "cryptography": "cryptography", "idna": "idna", "pycparser": "pycparser",
        "requests": "requests", "urllib3": "urllib3",
    }
    for relative_path, content in retained.items():
        if not relative_path.endswith(".py"):
            continue
        try:
            tree = _ast.parse(content, filename=relative_path)
        except (SyntaxError, ValueError):
            raise Task9TransitionEvidenceError("task9_bootstrap_dependency_ast_invalid") from None
        sys_aliases = {"sys", "_sys"}
        sys_path_aliases: set[str] = set()
        site_aliases = {"site", "_site"}
        for top_level in tree.body:
            if isinstance(top_level, _ast.Import):
                for alias in top_level.names:
                    if alias.name == "sys":
                        sys_aliases.add(alias.asname or "sys")
                    elif alias.name == "site":
                        site_aliases.add(alias.asname or "site")
            elif isinstance(top_level, _ast.ImportFrom):
                if top_level.module == "sys":
                    for alias in top_level.names:
                        if alias.name == "path":
                            sys_path_aliases.add(alias.asname or "path")
                elif top_level.module == "site":
                    for alias in top_level.names:
                        if alias.name == "addsitedir":
                            site_aliases.add(alias.asname or "addsitedir")

        def is_sys_path(value: object) -> bool:
            return (
                isinstance(value, _ast.Attribute)
                and value.attr == "path"
                and isinstance(value.value, _ast.Name)
                and value.value.id in sys_aliases
            ) or (
                isinstance(value, _ast.Name)
                and value.id in sys_path_aliases
            )

        def mutates_sys_path_target(target: object) -> bool:
            return is_sys_path(target) or (
                isinstance(target, _ast.Subscript)
                and is_sys_path(target.value)
            )

        for node in _ast.walk(tree):
            if (
                relative_path == "tools/task9_transition_evidence.py"
                and isinstance(node, (_ast.Assign, _ast.AnnAssign))
            ):
                targets = (
                    node.targets if isinstance(node, _ast.Assign) else (node.target,)
                )
                if any(
                    isinstance(target, _ast.Name)
                    and target.id == "_TASK9_IMPORT_SEARCH_SYS_V1"
                    for target in targets
                ):
                    if not isinstance(node.value, _ast.Name) or node.value.id != "_sys":
                        raise Task9TransitionEvidenceError(
                            "task9_bootstrap_dependency_ast_invalid"
                        )
                    import_search_bindings.append((relative_path, node.lineno))
            if (
                relative_path == "tools/task9_transition_evidence.py"
                and isinstance(node, _ast.Attribute)
                and isinstance(node.ctx, _ast.Load)
                and node.attr == "path"
                and isinstance(node.value, _ast.Name)
                and node.value.id == "_TASK9_IMPORT_SEARCH_SYS_V1"
            ):
                import_search_loads.append((relative_path, node.lineno))
            if isinstance(node, (_ast.Assign, _ast.AnnAssign, _ast.AugAssign)):
                targets = (
                    node.targets if isinstance(node, _ast.Assign) else (node.target,)
                )
                if any(mutates_sys_path_target(target) for target in targets):
                    raise Task9TransitionEvidenceError(
                        "task9_bootstrap_dependency_ast_invalid"
                    )
            if isinstance(node, _ast.Delete) and any(
                mutates_sys_path_target(target) for target in node.targets
            ):
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_dependency_ast_invalid"
                )
            if isinstance(node, _ast.Call):
                dynamic = (
                    isinstance(node.func, _ast.Name) and node.func.id == "__import__"
                ) or (
                    isinstance(node.func, _ast.Attribute)
                    and node.func.attr == "import_module"
                )
                if dynamic and (
                    not node.args or not isinstance(node.args[0], _ast.Constant)
                    or type(node.args[0].value) is not str
                ):
                    raise Task9TransitionEvidenceError("task9_bootstrap_dynamic_import_invalid")
                if (
                    isinstance(node.func, _ast.Attribute)
                    and node.func.attr
                    in ("append", "extend", "insert", "remove", "pop", "clear")
                    and is_sys_path(node.func.value)
                ):
                    raise Task9TransitionEvidenceError(
                        "task9_bootstrap_dependency_ast_invalid"
                    )
                if (
                    isinstance(node.func, _ast.Attribute)
                    and node.func.attr == "addsitedir"
                    and isinstance(node.func.value, _ast.Name)
                    and node.func.value.id in site_aliases
                ) or (
                    isinstance(node.func, _ast.Name)
                    and node.func.id in site_aliases
                    and node.func.id.endswith("addsitedir")
                ):
                    raise Task9TransitionEvidenceError(
                        "task9_bootstrap_dependency_ast_invalid"
                    )
            names: tuple[str, ...] = ()
            if isinstance(node, _ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, _ast.ImportFrom) and node.module is not None and node.level == 0:
                names = (node.module,)
            for imported in names:
                root = imported.split(".", 1)[0]
                if root in stdlib or root in repository_roots:
                    continue
                if root not in external_mapping:
                    raise Task9TransitionEvidenceError("task9_bootstrap_external_import_invalid")
                admitted_external_roots.add(root)
    if (
        len(import_search_bindings) != 1
        or len(import_search_loads) != 1
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_dependency_ast_invalid"
        )
    for root in admitted_external_roots:
        for candidate in (f"{root}.py", root):
            try:
                value = _os.stat(candidate, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if _stat.S_ISREG(value.st_mode) or _stat.S_ISDIR(value.st_mode):
                raise Task9TransitionEvidenceError("task9_bootstrap_import_shadow_invalid")


def _task9_bootstrap_capture_dependency_inventory_v1(root_fd: int) -> tuple[Task9CommandDependencyInventoryV1, tuple[tuple[str, tuple[int, ...]], ...], dict[str, bytes]]:
    discovered, identities, directories, execution_directories, retained = _task9_bootstrap_walk_repository_v1(root_fd)
    universe = TASK9_COMPLETE_REPOSITORY_DEPENDENCY_SUPERSET_V1
    if tuple(sorted(universe)) != universe or len(set(universe)) != len(universe):
        raise Task9TransitionEvidenceError("task9_bootstrap_dependency_universe_invalid")
    extras = tuple(sorted(set(discovered).difference(universe)))
    if extras:
        raise Task9TransitionEvidenceError("task9_bootstrap_dependency_universe_invalid")
    rows = tuple(
        discovered.get(path, Task9TreeInventoryRowV1(path, "ABSENT", None))
        for path in universe
    )
    file_rows = tuple(identities[path] for path in universe if path in identities)
    _task9_bootstrap_validate_repository_ast_v1(retained, root_fd)
    projection = {"schema_version": 1, "inventory_id": "TASK9_COMPLETE_REPOSITORY_DEPENDENCY_SUPERSET_V1", "inventory_rows": rows, "file_identity_rows": file_rows, "directory_identity_rows": directories}
    inventory = Task9CommandDependencyInventoryV1(
        **projection,
        inventory_sha256=_task9_bootstrap_domain_sha256_v1("INCI-TASK-9-COMMAND-DEPENDENCY-INVENTORY-V1", projection),
    )
    return inventory, execution_directories, retained


def _task9_bootstrap_walk_code_owned_tree_v1(
    root_path: str, *, excluded_directories: frozenset[str]
) -> tuple[tuple[Task9RuntimeInventoryRowV1, ...], tuple[tuple[str, tuple[int, ...]], ...]]:
    if root_path != "/Users/mthanki/.venvs/inci-expert-py314/lib/python3.14/site-packages":
        raise Task9TransitionEvidenceError("task9_bootstrap_interpreter_invalid")
    root_fd = _task9_bootstrap_open_absolute_v1(root_path, directory=True)
    rows: list[Task9RuntimeInventoryRowV1] = []
    directories: list[tuple[str, tuple[int, ...]]] = []

    def walk(directory_fd: int, relative_directory: str) -> None:
        before = _os.fstat(directory_fd)
        if not _stat.S_ISDIR(before.st_mode) or before.st_uid not in (_os.geteuid(), 0) or before.st_mode & 0o022:
            raise Task9TransitionEvidenceError("task9_bootstrap_interpreter_invalid")
        for name in sorted(_os.listdir(directory_fd)):
            if name in excluded_directories:
                value = _os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if _stat.S_ISDIR(value.st_mode) or _stat.S_ISLNK(value.st_mode):
                    continue
                raise Task9TransitionEvidenceError("task9_bootstrap_interpreter_invalid")
            relative_path = name if not relative_directory else f"{relative_directory}/{name}"
            value = _os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if _stat.S_ISLNK(value.st_mode):
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_interpreter_invalid"
                )
            elif _stat.S_ISDIR(value.st_mode):
                child_fd = _os.open(name, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC, dir_fd=directory_fd)
                try:
                    walk(child_fd, relative_path)
                finally:
                    _os.close(child_fd)
            elif _stat.S_ISREG(value.st_mode):
                file_fd = _os.open(name, _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_CLOEXEC, dir_fd=directory_fd)
                try:
                    captured, content = _task9_bootstrap_read_descriptor_v1(file_fd)
                finally:
                    _os.close(file_fd)
                if captured.st_uid not in (_os.geteuid(), 0):
                    raise Task9TransitionEvidenceError("task9_bootstrap_interpreter_invalid")
                suffix = relative_path.rsplit(".", 1)[-1].lower() if "." in relative_path else ""
                kind = "PYTHON" if suffix in ("py", "pyi") else "EXTENSION" if suffix in ("so", "dylib", "pyd") else "DATA"
                rows.append(Task9RuntimeInventoryRowV1(relative_path, kind, len(content), _task9_bootstrap_full_stat_identity_v1(captured), hashlib.sha256(content).hexdigest()))
            else:
                raise Task9TransitionEvidenceError("task9_bootstrap_interpreter_invalid")
        after = _os.fstat(directory_fd)
        if _task9_bootstrap_full_stat_identity_v1(before) != _task9_bootstrap_full_stat_identity_v1(after):
            raise Task9TransitionEvidenceError("task9_bootstrap_interpreter_drift")
        directories.append(("." if not relative_directory else relative_directory, _task9_bootstrap_full_stat_identity_v1(after)))

    try:
        walk(root_fd, "")
    finally:
        _os.close(root_fd)
    return tuple(sorted(rows, key=lambda row: row.relative_path)), tuple(sorted(directories))


def _task9_bootstrap_normalize_distribution_name_v1(value: str) -> str:
    return _re.sub(r"[-_.]+", "-", value).lower()


def _task9_bootstrap_capture_distribution_file_v1(
    site_root: str, record_path: str
) -> tuple[Task9ExternalDistributionFileRowV1, str | None]:
    if type(record_path) is not str or not record_path or record_path.startswith("/"):
        raise Task9TransitionEvidenceError("task9_bootstrap_distribution_invalid")
    normalized = _os.path.normpath(record_path)
    absolute = _os.path.normpath(_os.path.join(site_root, normalized))
    site_prefix = site_root.rstrip("/") + "/"
    venv_prefix = "/Users/mthanki/.venvs/inci-expert-py314/"
    if not (absolute.startswith(site_prefix) or absolute.startswith(venv_prefix)):
        raise Task9TransitionEvidenceError("task9_bootstrap_distribution_invalid")
    fd = _task9_bootstrap_open_absolute_v1(absolute, directory=False)
    try:
        value, content = _task9_bootstrap_read_descriptor_v1(fd)
    finally:
        _os.close(fd)
    if value.st_uid not in (_os.geteuid(), 0):
        raise Task9TransitionEvidenceError("task9_bootstrap_distribution_invalid")
    inside = absolute[len(site_prefix):] if absolute.startswith(site_prefix) else None
    return Task9ExternalDistributionFileRowV1(record_path, len(content), _task9_bootstrap_full_stat_identity_v1(value), hashlib.sha256(content).hexdigest()), inside


def _task9_bootstrap_capture_external_distributions_v1(
    site_root: str,
) -> tuple[tuple[Task9ExternalDistributionInventoryRowV1, ...], tuple[tuple[str, tuple[int, ...]], ...]]:
    expected = ("certifi", "cffi", "charset-normalizer", "cryptography", "idna", "pip", "pycparser", "requests", "urllib3")
    distributions_by_name: dict[str, object] = {}
    for distribution in _importlib_metadata.distributions(path=[site_root]):
        name = _task9_bootstrap_normalize_distribution_name_v1(distribution.metadata["Name"])
        if name in distributions_by_name:
            raise Task9TransitionEvidenceError("task9_bootstrap_distribution_invalid")
        distributions_by_name[name] = distribution
    if tuple(sorted(distributions_by_name)) != expected:
        raise Task9TransitionEvidenceError("task9_bootstrap_distribution_invalid")
    inventory: list[Task9ExternalDistributionInventoryRowV1] = []
    owned_importable: dict[str, tuple[int, str]] = {}
    for name in expected:
        distribution = distributions_by_name[name]
        dist_info_name = distribution._path.name
        metadata_relative = f"{dist_info_name}/METADATA"
        record_relative = f"{dist_info_name}/RECORD"
        metadata_row, _ = _task9_bootstrap_capture_distribution_file_v1(site_root, metadata_relative)
        record_row, _ = _task9_bootstrap_capture_distribution_file_v1(site_root, record_relative)
        record_fd = _task9_bootstrap_open_absolute_v1(f"{site_root}/{record_relative}", directory=False)
        try:
            _, record_bytes = _task9_bootstrap_read_descriptor_v1(record_fd)
        finally:
            _os.close(record_fd)
        try:
            record_text = record_bytes.decode("utf-8", "strict")
            record_entries = tuple(_csv.reader(_io.StringIO(record_text, newline="")))
        except (UnicodeDecodeError, _csv.Error):
            raise Task9TransitionEvidenceError("task9_bootstrap_distribution_invalid") from None
        file_rows: list[Task9ExternalDistributionFileRowV1] = []
        seen_paths: set[str] = set()
        for entry in record_entries:
            if len(entry) != 3 or entry[0] in seen_paths:
                raise Task9TransitionEvidenceError("task9_bootstrap_distribution_invalid")
            seen_paths.add(entry[0])
            file_row, importable_path = _task9_bootstrap_capture_distribution_file_v1(site_root, entry[0])
            if entry[1]:
                try:
                    algorithm, encoded = entry[1].split("=", 1)
                except ValueError:
                    raise Task9TransitionEvidenceError("task9_bootstrap_distribution_invalid") from None
                actual = _base64.urlsafe_b64encode(bytes.fromhex(file_row.content_sha256)).decode("ascii").rstrip("=")
                if algorithm != "sha256" or encoded != actual:
                    raise Task9TransitionEvidenceError("task9_bootstrap_distribution_invalid")
            if entry[2] and (not entry[2].isdigit() or int(entry[2]) != file_row.size):
                raise Task9TransitionEvidenceError("task9_bootstrap_distribution_invalid")
            file_rows.append(file_row)
            if importable_path is not None and "__pycache__" not in importable_path.split("/"):
                previous = owned_importable.get(importable_path)
                value = (file_row.size, file_row.content_sha256)
                if previous is not None and previous != value:
                    raise Task9TransitionEvidenceError("task9_bootstrap_distribution_invalid")
                owned_importable[importable_path] = value
        inventory.append(Task9ExternalDistributionInventoryRowV1(name, distribution.version, metadata_row.content_sha256, record_row.content_sha256, tuple(file_rows)))
    site_rows, site_directories = _task9_bootstrap_walk_code_owned_tree_v1(site_root, excluded_directories=frozenset(("__pycache__",)))
    actual_importable = {row.relative_path: (row.size, row.content_sha256) for row in site_rows}
    if actual_importable != owned_importable:
        raise Task9TransitionEvidenceError("task9_bootstrap_distribution_closure_invalid")
    forbidden = ("sitecustomize.py", "usercustomize.py")
    if any(path.endswith(".pth") for path in actual_importable) or any(name in actual_importable for name in forbidden):
        raise Task9TransitionEvidenceError("task9_bootstrap_distribution_closure_invalid")
    return tuple(inventory), site_directories


def _task9_bootstrap_capture_exact_link_v1(
    source_path: str, expected_target: str,
) -> tuple[int, ...]:
    os_api = _TASK9_PATH_CLOSURE_OS_V1
    try:
        before = os_api.lstat(source_path)
        first_target = os_api.readlink(source_path)
        second_target = os_api.readlink(source_path)
        after = os_api.lstat(source_path)
    except OSError:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_path_closure_invalid"
        ) from None
    identity = _task9_bootstrap_full_stat_identity_v1(before)
    if (
        not _stat.S_ISLNK(before.st_mode)
        or before.st_uid not in (_os.geteuid(), 0)
        or before.st_mode & 0o022
        or before.st_nlink != 1
        or identity != _task9_bootstrap_full_stat_identity_v1(after)
        or first_target != expected_target
        or second_target != expected_target
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_path_closure_invalid"
        )
    return identity


def _task9_bootstrap_capture_regular_absolute_v1(
    path: str, *, cap: int = 64 * 1024 * 1024, require_single_link: bool,
) -> tuple[tuple[int, ...], bytes]:
    os_api = _TASK9_PATH_CLOSURE_OS_V1
    fd = -1
    try:
        fd = _task9_bootstrap_open_absolute_v1(path, directory=False)
        value, content = _task9_bootstrap_read_descriptor_v1(fd, cap=cap)
        if (
            value.st_uid not in (_os.geteuid(), 0)
            or value.st_mode & 0o022
            or (require_single_link and value.st_nlink != 1)
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_path_closure_invalid"
            )
        return _task9_bootstrap_full_stat_identity_v1(value), content
    except Task9TransitionEvidenceError:
        raise
    except OSError:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_path_closure_invalid"
        ) from None
    finally:
        if fd >= 0:
            os_api.close(fd)


def _task9_bootstrap_capture_path_hops_v1(
) -> tuple[
    tuple[Task9InterpreterPathHopRowV1, ...],
    tuple[Task9InterpreterPathHopRowV1, ...],
]:
    rows: list[Task9InterpreterPathHopRowV1] = []
    for scope, hop_index, source_path, link_target, resolved_path in (
        TASK9_INTERPRETER_PATH_CLOSURE_ALLOWANCE_V1.path_hop_rows
    ):
        rows.append(
            Task9InterpreterPathHopRowV1(
                scope=scope,
                hop_index=hop_index,
                source_path=source_path,
                link_target=link_target,
                link_stat_identity=_task9_bootstrap_capture_exact_link_v1(
                    source_path, link_target
                ),
                resolved_after_hop_path=resolved_path,
            )
        )
    launcher = tuple(row for row in rows if row.scope == "LAUNCHER")
    stdlib = tuple(row for row in rows if row.scope == "STDLIB_ROOT")
    if (
        len(launcher) != 4
        or tuple(row.hop_index for row in launcher) != (0, 1, 2, 3)
        or len(stdlib) != 1
        or stdlib[0].hop_index != 0
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_path_closure_invalid"
        )
    return launcher, stdlib


def _task9_bootstrap_capture_policy_namespaces_v1(
) -> tuple[
    tuple[Task9PathComponentDirectoryIdentityRowV1, ...],
    tuple[Task9PathEndpointParentIdentityRowV1, ...],
]:
    cache: dict[str, tuple[tuple[int, ...], str]] = {}

    def capture(path: str) -> tuple[tuple[int, ...], str]:
        result = cache.get(path)
        if result is None:
            result = _task9_bootstrap_directory_entries_v1(path)
            cache[path] = result
        return result

    components = tuple(
        Task9PathComponentDirectoryIdentityRowV1(
            endpoint_key=endpoint_key,
            component_index=component_index,
            absolute_path=absolute_path,
            stat_identity=capture(absolute_path)[0],
            entries_sha256=capture(absolute_path)[1],
        )
        for endpoint_key, component_index, absolute_path
        in TASK9_PATH_COMPONENT_ALLOWANCE_V1.rows
    )
    endpoints = tuple(
        Task9PathEndpointParentIdentityRowV1(
            endpoint_key=endpoint_key,
            endpoint_role=endpoint_role,
            endpoint_path=endpoint_path,
            parent_path=parent_path,
            parent_stat_identity=capture(parent_path)[0],
            parent_entries_sha256=capture(parent_path)[1],
        )
        for endpoint_key, endpoint_role, endpoint_path, parent_path
        in TASK9_PATH_ENDPOINT_PARENT_ALLOWANCE_V1.rows
    )
    if len(components) != 192 or len(endpoints) != 23:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_path_closure_invalid"
        )
    return components, endpoints


def _task9_bootstrap_capture_pyvenv_config_v1(
) -> Task9PyvenvConfigEvidenceV1:
    policy = TASK9_PYVENV_CONFIG_POLICY_V1
    identity, content = _task9_bootstrap_capture_regular_absolute_v1(
        policy.path, cap=4_096, require_single_link=True
    )
    if (
        len(content) != policy.content_size
        or hashlib.sha256(content).hexdigest() != policy.content_sha256
        or not content.endswith(b"\n")
        or b"\r" in content
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_pyvenv_invalid")
    try:
        text = content.decode("utf-8", "strict")
        parsed_rows = tuple(
            tuple(cell.strip() for cell in line.split("=", 1))
            for line in text.removesuffix("\n").split("\n")
        )
    except (UnicodeDecodeError, ValueError):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_pyvenv_invalid"
        ) from None
    if parsed_rows != policy.parsed_rows:
        raise Task9TransitionEvidenceError("task9_bootstrap_pyvenv_invalid")
    projection = {
        "schema_version": 1,
        "path": policy.path,
        "raw_bytes_hex": content.hex(),
        "size": len(content),
        "stat_identity": identity,
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "parsed_rows": parsed_rows,
        "policy_sha256": policy.policy_sha256,
    }
    evidence = Task9PyvenvConfigEvidenceV1(
        schema_version=1,
        path=policy.path,
        raw_bytes=content,
        size=len(content),
        stat_identity=identity,
        content_sha256=hashlib.sha256(content).hexdigest(),
        parsed_rows=parsed_rows,
        policy_sha256=policy.policy_sha256,
        evidence_sha256=_task9_domain_sha256_v1(
            "INCI-TASK-9-PYVENV-CONFIG-EVIDENCE-V1", projection
        ),
    )
    return evidence


def _task9_bootstrap_capture_excluded_base_purelib_v1(
) -> Task9ExcludedBasePurelibDirectoryRowV1:
    os_api = _TASK9_PATH_CLOSURE_OS_V1
    root_path = (
        TASK9_SANITIZED_IMPORT_SEARCH_PATH_POLICY_V1
        .excluded_base_purelib_path
    )
    venv_path = TASK9_SANITIZED_IMPORT_SEARCH_PATH_POLICY_V1.rows[4][1]
    root_identity, root_entries_sha = _task9_bootstrap_directory_entries_v1(
        root_path
    )
    venv_identity, _ = _task9_bootstrap_directory_entries_v1(venv_path)
    if root_identity[:2] == venv_identity[:2]:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_excluded_base_invalid"
        )
    files: list[Task9ExcludedBasePurelibFileRowV1] = []
    directories: list[Task9ExcludedBasePurelibDirectoryIdentityRowV1] = []

    def walk(absolute: str, relative: str) -> None:
        directory_identity, entries_sha = (
            _task9_bootstrap_directory_entries_v1(absolute)
        )
        directories.append(
            Task9ExcludedBasePurelibDirectoryIdentityRowV1(
                relative_path="." if not relative else relative,
                stat_identity=directory_identity,
                entries_sha256=entries_sha,
            )
        )
        directory_fd = -1
        try:
            directory_fd = _task9_bootstrap_open_absolute_v1(
                absolute, directory=True
            )
            observed_directory_fd = _Task9ObservedDescriptorV1(
                directory_fd, absolute
            )
            for name in sorted(os_api.listdir(observed_directory_fd)):
                child_relative = name if not relative else f"{relative}/{name}"
                child_absolute = f"{absolute}/{name}"
                value = os_api.stat(child_absolute, follow_symlinks=False)
                if _stat.S_ISDIR(value.st_mode):
                    if name == "__pycache__":
                        raise Task9TransitionEvidenceError(
                            "task9_bootstrap_excluded_base_invalid"
                        )
                    walk(child_absolute, child_relative)
                elif _stat.S_ISREG(value.st_mode):
                    identity, content = (
                        _task9_bootstrap_capture_regular_absolute_v1(
                            child_absolute,
                            cap=64 * 1024 * 1024,
                            require_single_link=False,
                        )
                    )
                    suffix = child_relative.rsplit(".", 1)[-1].lower()
                    files.append(
                        Task9ExcludedBasePurelibFileRowV1(
                            relative_path=child_relative,
                            file_kind=(
                                "PYTHON" if suffix in ("py", "pyi") else "DATA"
                            ),
                            size=len(content),
                            stat_identity=identity,
                            content_sha256=hashlib.sha256(content).hexdigest(),
                        )
                    )
                else:
                    raise Task9TransitionEvidenceError(
                        "task9_bootstrap_excluded_base_invalid"
                    )
        finally:
            if directory_fd >= 0:
                os_api.close(directory_fd)

    walk(root_path, "")
    file_rows = tuple(sorted(files, key=lambda row: row.relative_path))
    directory_rows = tuple(
        sorted(directories, key=lambda row: row.relative_path)
    )
    total_bytes = sum(row.size for row in file_rows)
    if (len(file_rows), len(directory_rows), total_bytes) != (
        487, 79, 5_657_777
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_excluded_base_invalid"
        )
    projection = {
        "schema_version": 1,
        "target_role": "BASE_PURELIB_ROOT",
        "resolved_target_path": root_path,
        "relation_to_venv_purelib": "DISTINCT_FROM_VENV_PURELIB",
        "active_search_path_index": None,
        "target_stat_identity": root_identity,
        "target_entries_sha256": root_entries_sha,
        "exact_file_count": 487,
        "exact_directory_count": 79,
        "exact_file_bytes": 5_657_777,
        "file_rows": file_rows,
        "directory_identity_rows": directory_rows,
    }
    return Task9ExcludedBasePurelibDirectoryRowV1(
        **projection,
        excluded_inventory_sha256=_task9_bootstrap_domain_sha256_v1(
            "INCI-TASK-9-EXCLUDED-BASE-PURELIB-INVENTORY-V1",
            projection,
        ),
    )


def _task9_bootstrap_capture_runtime_inventory_v1(
    excluded: Task9ExcludedBasePurelibDirectoryRowV1,
) -> tuple[
    tuple[Task9RuntimeInventoryRowV1, ...],
    tuple[tuple[str, tuple[int, ...]], ...],
    Task9RuntimeSymlinkInventoryEvidenceV1,
    tuple[Task9RuntimeRegularTargetRowV1, ...],
]:
    os_api = _TASK9_PATH_CLOSURE_OS_V1
    runtime_root = TASK9_SANITIZED_IMPORT_SEARCH_PATH_POLICY_V1.rows[2][1]
    framework_path = (
        TASK9_INTERPRETER_PATH_CLOSURE_ALLOWANCE_V1.regular_target_rows[0][1]
    )
    target_identity, target_content = (
        _task9_bootstrap_capture_regular_absolute_v1(
            framework_path, cap=64 * 1024 * 1024, require_single_link=True
        )
    )
    regular_target = Task9RuntimeRegularTargetRowV1(
        target_role="CPYTHON_FRAMEWORK_BINARY",
        resolved_target_path=framework_path,
        target_size=len(target_content),
        target_stat_identity=target_identity,
        target_content_sha256=hashlib.sha256(target_content).hexdigest(),
    )
    allowed_links = {
        row[0]: row[1:]
        for row in TASK9_INTERPRETER_PATH_CLOSURE_ALLOWANCE_V1
        .runtime_symlink_rows
    }
    link_identities: dict[str, tuple[int, ...]] = {}
    file_rows: list[Task9RuntimeInventoryRowV1] = []
    directory_rows: list[tuple[str, tuple[int, ...]]] = []

    def walk(absolute: str, relative: str) -> None:
        directory_identity, _entries_sha = (
            _task9_bootstrap_directory_entries_v1(absolute)
        )
        directory_rows.append(("." if not relative else relative, directory_identity))
        fd = -1
        try:
            fd = _task9_bootstrap_open_absolute_v1(absolute, directory=True)
            observed_fd = _Task9ObservedDescriptorV1(fd, absolute)
            before = os_api.fstat(observed_fd)
            names = tuple(sorted(os_api.listdir(observed_fd)))
            for name in names:
                child_relative = name if not relative else f"{relative}/{name}"
                child_absolute = f"{absolute}/{name}"
                value = os_api.stat(child_absolute, follow_symlinks=False)
                if _stat.S_ISLNK(value.st_mode):
                    policy = allowed_links.get(child_relative)
                    if policy is None:
                        raise Task9TransitionEvidenceError(
                            "task9_bootstrap_runtime_link_invalid"
                        )
                    link_identities[child_relative] = (
                        _task9_bootstrap_capture_exact_link_v1(
                            child_absolute, policy[0]
                        )
                    )
                elif _stat.S_ISDIR(value.st_mode):
                    if name == "__pycache__":
                        continue
                    walk(child_absolute, child_relative)
                elif _stat.S_ISREG(value.st_mode):
                    identity, content = (
                        _task9_bootstrap_capture_regular_absolute_v1(
                            child_absolute,
                            cap=64 * 1024 * 1024,
                            require_single_link=False,
                        )
                    )
                    suffix = child_relative.rsplit(".", 1)[-1].lower()
                    file_rows.append(
                        Task9RuntimeInventoryRowV1(
                            relative_path=child_relative,
                            file_kind=(
                                "PYTHON" if suffix in ("py", "pyi")
                                else "EXTENSION" if suffix in ("so", "dylib", "pyd")
                                else "DATA"
                            ),
                            size=len(content),
                            stat_identity=identity,
                            content_sha256=hashlib.sha256(content).hexdigest(),
                        )
                    )
                else:
                    raise Task9TransitionEvidenceError(
                        "task9_bootstrap_interpreter_invalid"
                    )
            after = os_api.fstat(observed_fd)
            if (
                _task9_bootstrap_full_stat_identity_v1(before)
                != _task9_bootstrap_full_stat_identity_v1(after)
            ):
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_interpreter_drift"
                )
        finally:
            if fd >= 0:
                os_api.close(fd)

    walk(runtime_root, "")
    if tuple(sorted(link_identities)) != tuple(sorted(allowed_links)):
        raise Task9TransitionEvidenceError("task9_bootstrap_runtime_link_invalid")
    symlink_rows: list[Task9RuntimeSymlinkInventoryRowV1] = []
    for relative_path, link_target, target_kind, target_role, runtime_kind in (
        TASK9_INTERPRETER_PATH_CLOSURE_ALLOWANCE_V1.runtime_symlink_rows
    ):
        if target_role == "CPYTHON_FRAMEWORK_BINARY":
            target_size = regular_target.target_size
            target_stat_identity = regular_target.target_stat_identity
            target_sha = regular_target.target_content_sha256
        else:
            target_size = None
            target_stat_identity = excluded.target_stat_identity
            target_sha = None
        symlink_rows.append(
            Task9RuntimeSymlinkInventoryRowV1(
                relative_path=relative_path,
                link_target=link_target,
                link_stat_identity=link_identities[relative_path],
                target_kind=target_kind,
                target_role=target_role,
                runtime_file_kind=runtime_kind,
                target_size=target_size,
                target_stat_identity=target_stat_identity,
                target_content_sha256=target_sha,
            )
        )
    regular_targets = (regular_target,)
    symlink_projection = {
        "schema_version": 1,
        "runtime_symlink_inventory_rows": tuple(symlink_rows),
        "regular_target_rows": regular_targets,
        "excluded_base_purelib_directory": excluded,
    }
    symlink_evidence = Task9RuntimeSymlinkInventoryEvidenceV1(
        **symlink_projection,
        evidence_sha256=_task9_bootstrap_domain_sha256_v1(
            "INCI-TASK-9-RUNTIME-SYMLINK-INVENTORY-V1",
            symlink_projection,
        ),
    )
    return (
        tuple(sorted(file_rows, key=lambda row: row.relative_path)),
        tuple(sorted(directory_rows)),
        symlink_evidence,
        regular_targets,
    )


def _task9_bootstrap_capture_import_search_path_v1(
    excluded: Task9ExcludedBasePurelibDirectoryRowV1,
) -> Task9ImportSearchPathEvidenceV1:
    os_api = _TASK9_PATH_CLOSURE_OS_V1
    policy = TASK9_SANITIZED_IMPORT_SEARCH_PATH_POLICY_V1
    observed_import_search_path = tuple(_TASK9_IMPORT_SEARCH_SYS_V1.path)
    normalized_observed: list[str] = []
    for value in observed_import_search_path:
        if value == "":
            normalized_observed.append(policy.rows[0][1])
        elif type(value) is str and value.startswith("/"):
            normalized_observed.append(_os.path.normpath(value))
        else:
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_import_search_invalid"
            )
    expected_paths = tuple(row[1] for row in policy.rows)
    if tuple(normalized_observed) != expected_paths:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_import_search_invalid"
        )
    rows: list[Task9ImportSearchPathRowV1] = []
    for index, absolute_path, role, state in policy.rows:
        if state == "PRESENT":
            path_identity, _ = _task9_bootstrap_directory_entries_v1(
                absolute_path
            )
            rows.append(
                Task9ImportSearchPathRowV1(
                    index=index,
                    absolute_path=absolute_path,
                    role=role,
                    state=state,
                    path_stat_identity=path_identity,
                    absent_parent_path=None,
                    absent_parent_stat_identity=None,
                    absent_parent_entries_sha256=None,
                )
            )
        else:
            try:
                os_api.lstat(absolute_path)
            except FileNotFoundError:
                pass
            except OSError:
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_import_search_invalid"
                ) from None
            else:
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_import_search_invalid"
                )
            parent = _os.path.dirname(absolute_path)
            parent_identity, parent_entries = (
                _task9_bootstrap_directory_entries_v1(parent)
            )
            try:
                os_api.lstat(absolute_path)
            except FileNotFoundError:
                pass
            except OSError:
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_import_search_invalid"
                ) from None
            else:
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_import_search_invalid"
                )
            rows.append(
                Task9ImportSearchPathRowV1(
                    index=index,
                    absolute_path=absolute_path,
                    role=role,
                    state=state,
                    path_stat_identity=None,
                    absent_parent_path=parent,
                    absent_parent_stat_identity=parent_identity,
                    absent_parent_entries_sha256=parent_entries,
                )
            )
    path_rows = tuple(rows)
    projection_rows = tuple(
        Task9ImportSearchRowProjectionRowV1(
            index=row.index,
            absolute_path=row.absolute_path,
            role=row.role,
            state=row.state,
            path_stat_identity=row.path_stat_identity,
        )
        for row in path_rows
    )
    row_projection = {
        "schema_version": 1,
        "policy_sha256": policy.policy_sha256,
        "rows": projection_rows,
    }
    row_projection_sha = _task9_bootstrap_domain_sha256_v1(
        "INCI-TASK-9-IMPORT-SEARCH-ROW-PROJECTION-V1",
        row_projection,
    )
    projection = {
        "schema_version": 1,
        "policy_sha256": policy.policy_sha256,
        "rows": path_rows,
        "row_projection_sha256": row_projection_sha,
        "excluded_base_purelib_directory": excluded,
    }
    return Task9ImportSearchPathEvidenceV1(
        **projection,
        evidence_sha256=_task9_bootstrap_domain_sha256_v1(
            "INCI-TASK-9-IMPORT-SEARCH-PATH-EVIDENCE-V1", projection
        ),
    )


def _task9_bootstrap_capture_interpreter_evidence_v1(
) -> tuple[Task9InterpreterEvidenceV1, tuple[tuple[str, tuple[int, ...]], ...]]:
    launcher = "/Users/mthanki/.venvs/inci-expert-py314/bin/python"
    stdlib_root = TASK9_SANITIZED_IMPORT_SEARCH_PATH_POLICY_V1.rows[2][1]
    site_root = TASK9_SANITIZED_IMPORT_SEARCH_PATH_POLICY_V1.rows[4][1]
    if (
        _sys.executable != launcher
        or _sys.implementation.name != "cpython"
        or tuple(_sys.version_info) != (3, 14, 5, "final", 0)
        or _sys.implementation.cache_tag != "cpython-314"
        or _sysconfig.get_path("stdlib")
        != (
            "/opt/homebrew/opt/python@3.14/Frameworks/Python.framework/"
            "Versions/3.14/lib/python3.14"
        )
        or _sysconfig.get_path("purelib") != site_root
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_interpreter_invalid")
    homebrew_before = (
        _task9_bootstrap_capture_trusted_homebrew_component_mode_evidence_v1()
    )
    launcher_hops, stdlib_hops = _task9_bootstrap_capture_path_hops_v1()
    resolved_executable = launcher_hops[-1].resolved_after_hop_path
    executable_identity, executable_bytes = (
        _task9_bootstrap_capture_regular_absolute_v1(
            resolved_executable,
            cap=64 * 1024 * 1024,
            require_single_link=True,
        )
    )
    components, endpoints = _task9_bootstrap_capture_policy_namespaces_v1()
    config = _task9_bootstrap_capture_pyvenv_config_v1()
    excluded = _task9_bootstrap_capture_excluded_base_purelib_v1()
    runtime_rows, runtime_directories, symlink_evidence, regular_targets = (
        _task9_bootstrap_capture_runtime_inventory_v1(excluded)
    )
    distributions, site_directories = (
        _task9_bootstrap_capture_external_distributions_v1(site_root)
    )
    search_evidence = _task9_bootstrap_capture_import_search_path_v1(excluded)
    homebrew_after = (
        _task9_bootstrap_capture_trusted_homebrew_component_mode_evidence_v1()
    )
    _validate_task9_trusted_homebrew_component_mode_evidence_pair_v1(
        homebrew_before, homebrew_after
    )
    closure_projection = {
        "schema_version": 1,
        "allowance_sha256": (
            TASK9_INTERPRETER_PATH_CLOSURE_ALLOWANCE_V1.allowance_sha256
        ),
        "launcher_hop_rows": launcher_hops,
        "stdlib_root_hop_rows": stdlib_hops,
        "purelib_root_hop_rows": (),
        "component_directory_identity_rows": components,
        "endpoint_parent_identity_rows": endpoints,
        "regular_target_rows": regular_targets,
        "component_allowance_sha256": (
            TASK9_PATH_COMPONENT_ALLOWANCE_V1.policy_sha256
        ),
        "endpoint_parent_allowance_sha256": (
            TASK9_PATH_ENDPOINT_PARENT_ALLOWANCE_V1.policy_sha256
        ),
        "pyvenv_config_evidence": config,
        "import_search_path_evidence": search_evidence,
        "runtime_symlink_inventory_sha256": symlink_evidence.evidence_sha256,
        "trusted_homebrew_component_mode_evidence": homebrew_before,
    }
    closure = Task9InterpreterPathClosureEvidenceV1(
        **closure_projection,
        evidence_sha256=_task9_bootstrap_domain_sha256_v1(
            "INCI-TASK-9-INTERPRETER-PATH-CLOSURE-EVIDENCE-V1",
            closure_projection,
        ),
    )
    runtime_projection = {
        "version_info": tuple(_sys.version_info),
        "implementation_name": _sys.implementation.name,
        "cache_tag": _sys.implementation.cache_tag,
        "hexversion": _sys.hexversion,
        "byteorder": _sys.byteorder,
        "file_rows": runtime_rows,
        "runtime_symlink_rows": symlink_evidence.runtime_symlink_inventory_rows,
        "directory_rows": runtime_directories,
        "regular_target_rows": regular_targets,
        "excluded_base_purelib_directory": excluded,
    }
    runtime_sha = _task9_bootstrap_domain_sha256_v1(
        "INCI-TASK-9-CPYTHON-RUNTIME-INVENTORY-V1", runtime_projection
    )
    external_projection = {
        "distribution_rows": distributions,
        "site_packages_directory_identity_rows": site_directories,
    }
    external_sha = _task9_bootstrap_domain_sha256_v1(
        "INCI-TASK-9-EXTERNAL-DISTRIBUTION-INVENTORY-V1",
        external_projection,
    )
    projection = {
        "schema_version": 1,
        "interpreter_id": "CPYTHON_3_14_5_INCI_EXPERT_PY314",
        "launcher_path": launcher,
        "implementation_name": "cpython",
        "version_info": (3, 14, 5, "final", 0),
        "cache_tag": "cpython-314",
        "resolved_executable_path": resolved_executable,
        "resolved_executable_stat_identity": executable_identity,
        "resolved_executable_sha256": hashlib.sha256(executable_bytes).hexdigest(),
        "runtime_inventory_rows": runtime_rows,
        "runtime_symlink_inventory_evidence": symlink_evidence,
        "runtime_directory_identity_rows": runtime_directories,
        "runtime_inventory_sha256": runtime_sha,
        "external_distribution_inventory_rows": distributions,
        "site_packages_directory_identity_rows": site_directories,
        "external_distribution_inventory_sha256": external_sha,
        "path_closure_evidence": closure,
    }
    evidence = Task9InterpreterEvidenceV1(
        **projection,
        evidence_sha256=_task9_bootstrap_domain_sha256_v1(
            "INCI-TASK-9-INTERPRETER-EVIDENCE-V1", projection
        ),
    )
    runtime_identities: list[tuple[str, tuple[int, ...]]] = [
        ("EXECUTABLE", evidence.resolved_executable_stat_identity)
    ]
    runtime_identities.extend(
        (f"RUNTIME:{row.relative_path}", row.stat_identity)
        for row in runtime_rows
    )
    runtime_identities.extend(
        (f"RUNTIME_DIRECTORY:{path}", identity)
        for path, identity in runtime_directories
    )
    runtime_identities.extend(
        (f"RUNTIME_LINK:{row.relative_path}", row.link_stat_identity)
        for row in symlink_evidence.runtime_symlink_inventory_rows
    )
    for distribution in distributions:
        runtime_identities.extend(
            (
                f"DISTRIBUTION:{distribution.normalized_name}:{row.relative_path}",
                row.stat_identity,
            )
            for row in distribution.file_rows
        )
    runtime_identities.extend(
        (f"SITE_PACKAGES_DIRECTORY:{path}", identity)
        for path, identity in site_directories
    )
    return evidence, tuple(sorted(runtime_identities))


def _task9_bootstrap_is_stat9_v1(value: object) -> bool:
    return (
        type(value) is tuple
        and len(value) == 9
        and all(type(cell) is int and cell >= 0 for cell in value)
    )


def _task9_bootstrap_self_projection_v1(
    value: object, self_field: str,
) -> dict[str, object]:
    if not is_dataclass(value) or isinstance(value, type):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    projection: dict[str, object] = {}
    for field in fields(value):
        if field.name == self_field:
            continue
        cell = getattr(value, field.name)
        if field.name == "raw_bytes":
            if type(cell) is not bytes:
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_structure_invalid"
                )
            projection["raw_bytes_hex"] = cell.hex()
        else:
            projection[field.name] = cell
    return projection


def _task9_bootstrap_validate_self_v1(
    value: object, self_field: str, domain: str,
) -> None:
    digest = getattr(value, self_field, None)
    if (
        not _task9_is_sha256(digest)
        or digest
        != _task9_bootstrap_domain_sha256_v1(
            domain, _task9_bootstrap_self_projection_v1(value, self_field)
        )
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")


def _validate_task9_trusted_homebrew_component_mode_evidence_v1(
    value: object,
) -> Task9TrustedHomebrewComponentModeEvidenceV1:
    witness = TASK9_INSTALLED_HOST_PASSWD_GROUP_ACCESS_WITNESS_V1
    policy = TASK9_TRUSTED_HOMEBREW_COMPONENT_MODE_POLICY_V1
    if (
        type(value) is not Task9TrustedHomebrewComponentModeEvidenceV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.policy_sha256 != policy.policy_sha256
        or value.installed_host_witness is not witness
        or type(value.passwd_raw_rows) is not tuple
        or type(value.passwd_unique_rows) is not tuple
        or type(value.effective_group_access_rows) is not tuple
        or value.passwd_name_conflict_rows != ()
        or value.passwd_uid_conflict_rows != ()
        or value.primary_gid_member_rows != ()
        or value.membership_query_error_rows != ()
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    raw, unique, access = _task9_homebrew_require_witness_match_v1(
        value.passwd_raw_rows,
        value.passwd_unique_rows,
        value.effective_group_access_rows,
    )
    if (
        raw[:3]
        != (
            value.passwd_raw_row_count,
            value.passwd_raw_canonical_bytes,
            value.passwd_raw_rows_sha256,
        )
        or unique[:3]
        != (
            value.passwd_unique_row_count,
            value.passwd_unique_canonical_bytes,
            value.passwd_unique_rows_sha256,
        )
        or access[:3]
        != (
            value.effective_group_access_row_count,
            value.effective_group_access_canonical_bytes,
            value.effective_group_access_rows_sha256,
        )
        or tuple(
            sorted(value.passwd_raw_rows, key=_canonical_json_bytes)
        )
        != value.passwd_raw_rows
        or tuple(
            sorted(value.passwd_unique_rows, key=_canonical_json_bytes)
        )
        != value.passwd_unique_rows
        or len(set(value.passwd_unique_rows)) != len(value.passwd_unique_rows)
        or value.root_role_passwd_row not in value.passwd_unique_rows
        or value.effective_uid_role_passwd_row not in value.passwd_unique_rows
        or value.root_role_passwd_row[2] != 0
        or value.effective_uid_role_passwd_row[2] != _os.geteuid()
        or value.root_role_passwd_row[0]
        == value.effective_uid_role_passwd_row[0]
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    expected_names = tuple(
        sorted(
            (
                value.root_role_passwd_row[0],
                value.effective_uid_role_passwd_row[0],
            ),
            key=lambda name: name.encode("ascii"),
        )
    )
    expected_gid80_rows = tuple(
        row[0] for row in value.effective_group_access_rows if 80 in row[1]
    )
    if (
        type(value.gid80_group_row) is not tuple
        or len(value.gid80_group_row) != 4
        or value.gid80_group_row[2:] != (80, expected_names)
        or type(value.gid80_member_resolution_rows) is not tuple
        or tuple(row[0] for row in value.gid80_member_resolution_rows)
        != expected_names
        or value.effective_gid80_member_rows != expected_gid80_rows
        or len(expected_gid80_rows) != 2
        or set(expected_gid80_rows)
        != {
            value.root_role_passwd_row,
            value.effective_uid_role_passwd_row,
        }
        or value.builtin_identity_rows
        != (
            ("grp.getgrgid", "CODE_OWNED_STDLIB_BUILTIN"),
            ("os.geteuid", "CODE_OWNED_STDLIB_BUILTIN"),
            ("os.getgrouplist", "CODE_OWNED_STDLIB_BUILTIN"),
            ("pwd.getpwall", "CODE_OWNED_STDLIB_BUILTIN"),
            ("pwd.getpwnam", "CODE_OWNED_STDLIB_BUILTIN"),
        )
        or type(value.component_rows) is not tuple
        or tuple(row.path for row in value.component_rows)
        != ("/opt/homebrew/Cellar", "/opt/homebrew/opt")
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    for row in value.component_rows:
        if (
            type(row) is not Task9TrustedHomebrewComponentRowV1
            or row.owner_role != "EFFECTIVE_UID"
            or not _task9_bootstrap_is_stat9_v1(row.stat_identity)
            or row.stat_identity[3] != _os.geteuid()
            or row.stat_identity[4] != 80
            or _stat.S_IMODE(row.stat_identity[2]) != 0o775
            or not _task9_is_sha256(row.entries_sha256)
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_homebrew_membership_invalid"
            )
    _task9_bootstrap_validate_self_v1(
        value,
        "evidence_sha256",
        "INCI-TASK-9-TRUSTED-HOMEBREW-COMPONENT-MODE-EVIDENCE-V1",
    )
    return value


def _validate_task9_trusted_homebrew_component_mode_evidence_pair_v1(
    before: object,
    after: object,
) -> None:
    if (
        type(before) is not Task9TrustedHomebrewComponentModeEvidenceV1
        or type(after) is not Task9TrustedHomebrewComponentModeEvidenceV1
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_invalid"
        )
    if (
        before.installed_host_witness is not after.installed_host_witness
        or
        _task9_bootstrap_projection_v1(before)
        != _task9_bootstrap_projection_v1(after)
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_homebrew_membership_drift"
        )
    _validate_task9_trusted_homebrew_component_mode_evidence_v1(before)
    _validate_task9_trusted_homebrew_component_mode_evidence_v1(after)


def _validate_task9_pyvenv_config_evidence_v1(
    value: object,
) -> Task9PyvenvConfigEvidenceV1:
    policy = TASK9_PYVENV_CONFIG_POLICY_V1
    if (
        type(value) is not Task9PyvenvConfigEvidenceV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.path != policy.path
        or type(value.raw_bytes) is not bytes
        or type(value.size) is not int
        or value.size != len(value.raw_bytes)
        or value.size != policy.content_size
        or not _task9_bootstrap_is_stat9_v1(value.stat_identity)
        or value.content_sha256 != hashlib.sha256(value.raw_bytes).hexdigest()
        or value.content_sha256 != policy.content_sha256
        or value.parsed_rows != policy.parsed_rows
        or value.policy_sha256 != policy.policy_sha256
        or not value.raw_bytes.endswith(b"\n")
        or b"\r" in value.raw_bytes
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    try:
        decoded = value.raw_bytes.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_structure_invalid"
        ) from None
    reparsed = tuple(
        tuple(cell.strip() for cell in line.split("=", 1))
        for line in decoded.removesuffix("\n").split("\n")
    )
    if reparsed != value.parsed_rows:
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    _task9_bootstrap_validate_self_v1(
        value,
        "evidence_sha256",
        "INCI-TASK-9-PYVENV-CONFIG-EVIDENCE-V1",
    )
    return value


def _validate_task9_excluded_base_purelib_directory_v1(
    value: object,
) -> Task9ExcludedBasePurelibDirectoryRowV1:
    if (
        type(value) is not Task9ExcludedBasePurelibDirectoryRowV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.target_role != "BASE_PURELIB_ROOT"
        or value.resolved_target_path
        != TASK9_SANITIZED_IMPORT_SEARCH_PATH_POLICY_V1.excluded_base_purelib_path
        or value.relation_to_venv_purelib != "DISTINCT_FROM_VENV_PURELIB"
        or value.active_search_path_index is not None
        or not _task9_bootstrap_is_stat9_v1(value.target_stat_identity)
        or not _task9_is_sha256(value.target_entries_sha256)
        or type(value.exact_file_count) is not int
        or type(value.exact_directory_count) is not int
        or type(value.exact_file_bytes) is not int
        or (value.exact_file_count, value.exact_directory_count,
            value.exact_file_bytes) != (487, 79, 5_657_777)
        or type(value.file_rows) is not tuple
        or len(value.file_rows) != 487
        or type(value.directory_identity_rows) is not tuple
        or len(value.directory_identity_rows) != 79
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    file_paths: list[str] = []
    total = 0
    for row in value.file_rows:
        if (
            type(row) is not Task9ExcludedBasePurelibFileRowV1
            or type(row.relative_path) is not str
            or not row.relative_path
            or not row.relative_path.isascii()
            or row.relative_path.startswith("/")
            or ".." in row.relative_path.split("/")
            or row.file_kind not in ("PYTHON", "DATA")
            or type(row.size) is not int
            or row.size < 0
            or not _task9_bootstrap_is_stat9_v1(row.stat_identity)
            or row.stat_identity[6] != row.size
            or not _task9_is_sha256(row.content_sha256)
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_structure_invalid"
            )
        file_paths.append(row.relative_path)
        total += row.size
    directory_paths: list[str] = []
    for row in value.directory_identity_rows:
        if (
            type(row) is not Task9ExcludedBasePurelibDirectoryIdentityRowV1
            or type(row.relative_path) is not str
            or not row.relative_path
            or not row.relative_path.isascii()
            or not _task9_bootstrap_is_stat9_v1(row.stat_identity)
            or not _stat.S_ISDIR(row.stat_identity[2])
            or not _task9_is_sha256(row.entries_sha256)
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_structure_invalid"
            )
        directory_paths.append(row.relative_path)
    if (
        file_paths != sorted(file_paths)
        or len(set(file_paths)) != 487
        or directory_paths != sorted(directory_paths)
        or len(set(directory_paths)) != 79
        or directory_paths[0] != "."
        or total != 5_657_777
        or value.directory_identity_rows[0].stat_identity
        != value.target_stat_identity
        or value.directory_identity_rows[0].entries_sha256
        != value.target_entries_sha256
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    _task9_bootstrap_validate_self_v1(
        value,
        "excluded_inventory_sha256",
        "INCI-TASK-9-EXCLUDED-BASE-PURELIB-INVENTORY-V1",
    )
    return value


def _validate_task9_runtime_symlink_inventory_evidence_v1(
    value: object,
) -> Task9RuntimeSymlinkInventoryEvidenceV1:
    if (
        type(value) is not Task9RuntimeSymlinkInventoryEvidenceV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or type(value.runtime_symlink_inventory_rows) is not tuple
        or len(value.runtime_symlink_inventory_rows) != 3
        or type(value.regular_target_rows) is not tuple
        or len(value.regular_target_rows) != 1
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    excluded = _validate_task9_excluded_base_purelib_directory_v1(
        value.excluded_base_purelib_directory
    )
    target = value.regular_target_rows[0]
    expected_target = (
        TASK9_INTERPRETER_PATH_CLOSURE_ALLOWANCE_V1.regular_target_rows[0]
    )
    if (
        type(target) is not Task9RuntimeRegularTargetRowV1
        or (target.target_role, target.resolved_target_path) != expected_target
        or type(target.target_size) is not int
        or target.target_size < 0
        or not _task9_bootstrap_is_stat9_v1(target.target_stat_identity)
        or target.target_stat_identity[6] != target.target_size
        or not _task9_is_sha256(target.target_content_sha256)
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    for row, expected in zip(
        value.runtime_symlink_inventory_rows,
        TASK9_INTERPRETER_PATH_CLOSURE_ALLOWANCE_V1.runtime_symlink_rows,
    ):
        if (
            type(row) is not Task9RuntimeSymlinkInventoryRowV1
            or (
                row.relative_path, row.link_target, row.target_kind,
                row.target_role, row.runtime_file_kind,
            ) != expected
            or not _task9_bootstrap_is_stat9_v1(row.link_stat_identity)
            or not _stat.S_ISLNK(row.link_stat_identity[2])
            or not _task9_bootstrap_is_stat9_v1(row.target_stat_identity)
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_structure_invalid"
            )
        if row.target_kind == "REGULAR":
            if (
                type(row.target_size) is not int
                or row.target_size < 0
                or row.runtime_file_kind not in ("DATA", "EXTENSION")
                or not _task9_is_sha256(row.target_content_sha256)
                or (
                    row.target_size, row.target_stat_identity,
                    row.target_content_sha256,
                ) != (
                    target.target_size, target.target_stat_identity,
                    target.target_content_sha256,
                )
            ):
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_structure_invalid"
                )
        elif (
            row.target_kind != "DIRECTORY"
            or row.runtime_file_kind is not None
            or row.target_size is not None
            or row.target_content_sha256 is not None
            or row.target_stat_identity != excluded.target_stat_identity
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_structure_invalid"
            )
    _task9_bootstrap_validate_self_v1(
        value,
        "evidence_sha256",
        "INCI-TASK-9-RUNTIME-SYMLINK-INVENTORY-V1",
    )
    return value


def _validate_task9_import_search_path_evidence_v1(
    value: object,
) -> Task9ImportSearchPathEvidenceV1:
    policy = TASK9_SANITIZED_IMPORT_SEARCH_PATH_POLICY_V1
    if (
        type(value) is not Task9ImportSearchPathEvidenceV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.policy_sha256 != policy.policy_sha256
        or type(value.rows) is not tuple
        or len(value.rows) != 5
        or not _task9_is_sha256(value.row_projection_sha256)
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    projection_rows: list[Task9ImportSearchRowProjectionRowV1] = []
    for row, expected in zip(value.rows, policy.rows):
        if (
            type(row) is not Task9ImportSearchPathRowV1
            or (row.index, row.absolute_path, row.role, row.state) != expected
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_structure_invalid"
            )
        if row.state == "PRESENT":
            if (
                not _task9_bootstrap_is_stat9_v1(row.path_stat_identity)
                or row.absent_parent_path is not None
                or row.absent_parent_stat_identity is not None
                or row.absent_parent_entries_sha256 is not None
            ):
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_structure_invalid"
                )
        elif (
            row.path_stat_identity is not None
            or type(row.absent_parent_path) is not str
            or row.absent_parent_path != _os.path.dirname(row.absolute_path)
            or not _task9_bootstrap_is_stat9_v1(
                row.absent_parent_stat_identity
            )
            or not _task9_is_sha256(row.absent_parent_entries_sha256)
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_structure_invalid"
            )
        projection_rows.append(
            Task9ImportSearchRowProjectionRowV1(
                row.index,
                row.absolute_path,
                row.role,
                row.state,
                row.path_stat_identity,
            )
        )
    projection = {
        "schema_version": 1,
        "policy_sha256": policy.policy_sha256,
        "rows": tuple(projection_rows),
    }
    if value.row_projection_sha256 != _task9_bootstrap_domain_sha256_v1(
        "INCI-TASK-9-IMPORT-SEARCH-ROW-PROJECTION-V1", projection
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    _validate_task9_excluded_base_purelib_directory_v1(
        value.excluded_base_purelib_directory
    )
    _task9_bootstrap_validate_self_v1(
        value,
        "evidence_sha256",
        "INCI-TASK-9-IMPORT-SEARCH-PATH-EVIDENCE-V1",
    )
    return value


def _validate_task9_interpreter_path_closure_evidence_v1(
    value: object,
) -> Task9InterpreterPathClosureEvidenceV1:
    if (
        type(value) is not Task9InterpreterPathClosureEvidenceV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.allowance_sha256
        != TASK9_INTERPRETER_PATH_CLOSURE_ALLOWANCE_V1.allowance_sha256
        or value.component_allowance_sha256
        != TASK9_PATH_COMPONENT_ALLOWANCE_V1.policy_sha256
        or value.endpoint_parent_allowance_sha256
        != TASK9_PATH_ENDPOINT_PARENT_ALLOWANCE_V1.policy_sha256
        or type(value.launcher_hop_rows) is not tuple
        or len(value.launcher_hop_rows) != 4
        or type(value.stdlib_root_hop_rows) is not tuple
        or len(value.stdlib_root_hop_rows) != 1
        or value.purelib_root_hop_rows != ()
        or type(value.component_directory_identity_rows) is not tuple
        or len(value.component_directory_identity_rows) != 192
        or type(value.endpoint_parent_identity_rows) is not tuple
        or len(value.endpoint_parent_identity_rows) != 23
        or type(value.regular_target_rows) is not tuple
        or len(value.regular_target_rows) != 1
        or not _task9_is_sha256(value.runtime_symlink_inventory_sha256)
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    _validate_task9_pyvenv_config_evidence_v1(value.pyvenv_config_evidence)
    _validate_task9_import_search_path_evidence_v1(
        value.import_search_path_evidence
    )
    _validate_task9_trusted_homebrew_component_mode_evidence_v1(
        value.trusted_homebrew_component_mode_evidence
    )
    _task9_bootstrap_validate_self_v1(
        value,
        "evidence_sha256",
        "INCI-TASK-9-INTERPRETER-PATH-CLOSURE-EVIDENCE-V1",
    )
    return value


def _task9_bootstrap_validate_authority_root_v1(record: dict[str, object]) -> None:
    current_root = _os.fstat(record["root_fd"])
    if _task9_bootstrap_full_stat_identity_v1(current_root) != record["root_identity"]:
        raise Task9TransitionEvidenceError("task9_bootstrap_root_drift")
    module_fd = -1
    try:
        module_fd = _task9_open_relative_nofollow(record["root_fd"], "tools/task9_transition_evidence.py")
        module_stat, module_bytes = _task9_bootstrap_read_descriptor_v1(module_fd, cap=16_777_216)
        if (
            _task9_bootstrap_full_stat_identity_v1(module_stat) != record["module_identity"]
            or hashlib.sha256(module_bytes).hexdigest() != record["module_sha256"]
        ):
            raise Task9TransitionEvidenceError("task9_bootstrap_root_drift")
    finally:
        if module_fd >= 0:
            _os.close(module_fd)


def _validate_task9_command_dependency_inventory_v1(
    value: object,
) -> Task9CommandDependencyInventoryV1:
    if (
        type(value) is not Task9CommandDependencyInventoryV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.inventory_id
        != "TASK9_COMPLETE_REPOSITORY_DEPENDENCY_SUPERSET_V1"
        or type(value.inventory_rows) is not tuple
        or tuple(row.relative_path for row in value.inventory_rows)
        != TASK9_COMPLETE_REPOSITORY_DEPENDENCY_SUPERSET_V1
        or type(value.file_identity_rows) is not tuple
        or type(value.directory_identity_rows) is not tuple
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    file_identities = dict(value.file_identity_rows)
    if (
        len(file_identities) != len(value.file_identity_rows)
        or any(
            type(path) is not str
            or not _task9_bootstrap_is_stat9_v1(identity)
            for path, identity in value.file_identity_rows
        )
        or any(
            type(path) is not str
            or type(identity) is not tuple
            or len(identity) != 7
            or any(type(cell) is not int or cell < 0 for cell in identity[:6])
            or not _stat.S_ISDIR(identity[2])
            or not _task9_is_sha256(identity[6])
            for path, identity in value.directory_identity_rows
        )
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    for row in value.inventory_rows:
        if (
            type(row) is not Task9TreeInventoryRowV1
            or row.state not in ("PRESENT", "ABSENT")
            or (
                row.state == "PRESENT"
                and (
                    not _task9_is_sha256(row.content_sha256)
                    or row.relative_path not in file_identities
                )
            )
            or (
                row.state == "ABSENT"
                and (
                    row.content_sha256 is not None
                    or row.relative_path in file_identities
                )
            )
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_structure_invalid"
            )
    _task9_bootstrap_validate_self_v1(
        value,
        "inventory_sha256",
        "INCI-TASK-9-COMMAND-DEPENDENCY-INVENTORY-V1",
    )
    return value


def _validate_task9_interpreter_evidence_v1(
    value: object,
) -> Task9InterpreterEvidenceV1:
    if (
        type(value) is not Task9InterpreterEvidenceV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.interpreter_id != "CPYTHON_3_14_5_INCI_EXPERT_PY314"
        or value.launcher_path
        != "/Users/mthanki/.venvs/inci-expert-py314/bin/python"
        or value.implementation_name != "cpython"
        or value.version_info != (3, 14, 5, "final", 0)
        or value.cache_tag != "cpython-314"
        or not _task9_bootstrap_is_stat9_v1(
            value.resolved_executable_stat_identity
        )
        or not _task9_is_sha256(value.resolved_executable_sha256)
        or type(value.runtime_inventory_rows) is not tuple
        or type(value.runtime_directory_identity_rows) is not tuple
        or type(value.external_distribution_inventory_rows) is not tuple
        or type(value.site_packages_directory_identity_rows) is not tuple
        or not _task9_is_sha256(value.runtime_inventory_sha256)
        or not _task9_is_sha256(value.external_distribution_inventory_sha256)
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    symlinks = _validate_task9_runtime_symlink_inventory_evidence_v1(
        value.runtime_symlink_inventory_evidence
    )
    closure = _validate_task9_interpreter_path_closure_evidence_v1(
        value.path_closure_evidence
    )
    if (
        closure.runtime_symlink_inventory_sha256 != symlinks.evidence_sha256
        or symlinks.excluded_base_purelib_directory
        is not closure.import_search_path_evidence.excluded_base_purelib_directory
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_structure_invalid")
    _task9_bootstrap_validate_self_v1(
        value,
        "evidence_sha256",
        "INCI-TASK-9-INTERPRETER-EVIDENCE-V1",
    )
    return value


def _task9_future_genesis_projection_v1(
    dependency_inventory: Task9CommandDependencyInventoryV1,
    interpreter_evidence: Task9InterpreterEvidenceV1,
) -> dict[str, object]:
    dependency = _validate_task9_command_dependency_inventory_v1(
        dependency_inventory
    )
    interpreter = _validate_task9_interpreter_evidence_v1(
        interpreter_evidence
    )
    return {
        "schema_version": 1,
        "genesis_id": "TASK9_PREDECESSOR_COMMAND_DEPENDENCY_GENESIS_V1",
        "dependency_inventory_sha256": dependency.inventory_sha256,
        "interpreter_evidence_sha256": interpreter.evidence_sha256,
        "root_binding_policy_sha256": (
            TASK9_MODULE_ORIGIN_ROOT_BINDING_POLICY_V1.policy_sha256
        ),
        "antecedent_chain_receipt_sha256s": (),
    }


def _task9_capture_bootstrap_path_closure_from_lease_v1(
    lease: _Task9BootstrapMutationLeaseV1,
    *,
    baseline_dependency: Task9CommandDependencyInventoryV1 | None = None,
    baseline_interpreter: Task9InterpreterEvidenceV1 | None = None,
) -> tuple[
    Task9BootstrapPathClosureSnapshotV1,
    Task9CommandDependencyInventoryV1,
    Task9InterpreterEvidenceV1,
]:
    lease_record = _task9_bootstrap_live_lease_v1(lease)
    authority_record = lease_record["authority_record"]
    _task9_bootstrap_validate_authority_root_v1(authority_record)
    dependency, _execution_directories, _retained = (
        _task9_bootstrap_capture_dependency_inventory_v1(
            authority_record["root_fd"]
        )
    )
    interpreter, _runtime_identities = (
        _task9_bootstrap_capture_interpreter_evidence_v1()
    )
    _validate_task9_command_dependency_inventory_v1(dependency)
    _validate_task9_interpreter_evidence_v1(interpreter)
    if (baseline_dependency is None) != (baseline_interpreter is None):
        raise Task9TransitionEvidenceError("task9_bootstrap_snapshot_invalid")
    if baseline_dependency is not None and baseline_interpreter is not None:
        _validate_task9_command_dependency_inventory_v1(baseline_dependency)
        _validate_task9_interpreter_evidence_v1(baseline_interpreter)
        if (
            _task9_bootstrap_projection_v1(dependency)
            != _task9_bootstrap_projection_v1(baseline_dependency)
            or _task9_bootstrap_projection_v1(interpreter)
            != _task9_bootstrap_projection_v1(baseline_interpreter)
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_execution_drift"
            )
        dependency = baseline_dependency
        interpreter = baseline_interpreter
    _task9_bootstrap_validate_authority_root_v1(authority_record)
    closure = interpreter.path_closure_evidence
    captured = _time.monotonic_ns()
    projection = {
        "schema_version": 1,
        "snapshot_kind": (
            "PD_INTEGRATION_BOOTSTRAP_PATH_CLOSURE_SNAPSHOT_V1"
        ),
        "root_identity_sha256": _task9_bootstrap_domain_sha256_v1(
            "INCI-TASK-9-EVIDENCE-ROOT-IDENTITY-V1",
            authority_record["root_identity"],
        ),
        "root_binding_policy_sha256": (
            TASK9_MODULE_ORIGIN_ROOT_BINDING_POLICY_V1.policy_sha256
        ),
        "dependency_inventory_sha256": dependency.inventory_sha256,
        "interpreter_evidence": interpreter,
        "interpreter_path_closure_evidence_sha256": closure.evidence_sha256,
        "bootstrap_probe_environment_policy_sha256": (
            TASK9_BOOTSTRAP_PROBE_ENVIRONMENT_POLICY_V1.policy_sha256
        ),
        "captured_monotonic_ns": captured,
    }
    snapshot = Task9BootstrapPathClosureSnapshotV1(
        **projection,
        snapshot_sha256=_task9_bootstrap_domain_sha256_v1(
            "INCI-TASK-9-PD-INTEGRATION-BOOTSTRAP-PATH-CLOSURE-SNAPSHOT-V1",
            projection,
        ),
    )
    _validate_task9_bootstrap_path_closure_snapshot_v1(snapshot)
    return snapshot, dependency, interpreter


def _validate_task9_bootstrap_path_closure_snapshot_v1(
    value: object,
) -> Task9BootstrapPathClosureSnapshotV1:
    if (
        type(value) is not Task9BootstrapPathClosureSnapshotV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.snapshot_kind
        != "PD_INTEGRATION_BOOTSTRAP_PATH_CLOSURE_SNAPSHOT_V1"
        or not _task9_is_sha256(value.root_identity_sha256)
        or value.root_binding_policy_sha256
        != TASK9_MODULE_ORIGIN_ROOT_BINDING_POLICY_V1.policy_sha256
        or not _task9_is_sha256(value.dependency_inventory_sha256)
        or value.interpreter_path_closure_evidence_sha256
        != value.interpreter_evidence.path_closure_evidence.evidence_sha256
        or value.bootstrap_probe_environment_policy_sha256
        != TASK9_BOOTSTRAP_PROBE_ENVIRONMENT_POLICY_V1.policy_sha256
        or type(value.captured_monotonic_ns) is not int
        or value.captured_monotonic_ns <= 0
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_snapshot_invalid")
    _validate_task9_interpreter_evidence_v1(value.interpreter_evidence)
    _task9_bootstrap_validate_self_v1(
        value,
        "snapshot_sha256",
        "INCI-TASK-9-PD-INTEGRATION-BOOTSTRAP-PATH-CLOSURE-SNAPSHOT-V1",
    )
    return value


def _capture_task9_bootstrap_dependency_observation_v1(
) -> Task9CommandDependencyInventoryV1:
    authority = _issue_task9_evidence_root_authority_v1()
    lease: _Task9BootstrapMutationLeaseV1 | None = None
    try:
        lease = _acquire_task9_bootstrap_mutation_lease_v1(authority)
        record = _task9_bootstrap_live_lease_v1(lease)["authority_record"]
        _task9_bootstrap_validate_authority_root_v1(record)
        dependency, _execution_directories, _retained = (
            _task9_bootstrap_capture_dependency_inventory_v1(
                record["root_fd"]
            )
        )
        _task9_bootstrap_validate_authority_root_v1(record)
        return _validate_task9_command_dependency_inventory_v1(dependency)
    finally:
        if lease is not None:
            _release_task9_bootstrap_mutation_lease_v1(lease)
        else:
            _revoke_task9_evidence_root_authority_v1(authority)


def _capture_task9_bootstrap_path_closure_snapshot_v1(
) -> Task9BootstrapPathClosureSnapshotV1:
    authority = _issue_task9_evidence_root_authority_v1()
    lease: _Task9BootstrapMutationLeaseV1 | None = None
    try:
        lease = _acquire_task9_bootstrap_mutation_lease_v1(authority)
        snapshot, _dependency, _interpreter = (
            _task9_capture_bootstrap_path_closure_from_lease_v1(lease)
        )
        return snapshot
    finally:
        if lease is not None:
            _release_task9_bootstrap_mutation_lease_v1(lease)
        else:
            _revoke_task9_evidence_root_authority_v1(authority)


def _validate_task9_bootstrap_snapshot_pair_v1(
    before: object,
    after: object,
) -> None:
    first = _validate_task9_bootstrap_path_closure_snapshot_v1(before)
    second = _validate_task9_bootstrap_path_closure_snapshot_v1(after)
    if second.captured_monotonic_ns <= first.captured_monotonic_ns:
        raise Task9TransitionEvidenceError("task9_bootstrap_snapshot_drift")
    excluded = {"captured_monotonic_ns", "snapshot_sha256"}
    for field in fields(Task9BootstrapPathClosureSnapshotV1):
        if field.name in excluded:
            continue
        left = getattr(first, field.name)
        right = getattr(second, field.name)
        if field.name == "interpreter_evidence":
            left = _task9_bootstrap_projection_v1(left)
            right = _task9_bootstrap_projection_v1(right)
        if left != right:
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_snapshot_drift"
            )
    _validate_task9_trusted_homebrew_component_mode_evidence_pair_v1(
        first.interpreter_evidence.path_closure_evidence
        .trusted_homebrew_component_mode_evidence,
        second.interpreter_evidence.path_closure_evidence
        .trusted_homebrew_component_mode_evidence,
    )


def _task9_bootstrap_probe_kind_and_argv_v1(
    probe: object,
) -> tuple[str, tuple[str, ...]]:
    if probe is _TASK9_BOOTSTRAP_UNITTEST_PROBE_V1:
        return "UNITTEST_MODULE", _TASK9_BOOTSTRAP_UNITTEST_ARGV_V1
    if probe is _TASK9_BOOTSTRAP_FROZEN_V6_PROBE_V1:
        return "FROZEN_V6_SCRIPT", _TASK9_BOOTSTRAP_FROZEN_V6_ARGV_V1
    raise Task9TransitionEvidenceError("task9_bootstrap_probe_invalid")


def _task9_bootstrap_validate_probe_environment_rows_v1(
    rows: object,
    probe: object,
) -> tuple[str, str]:
    kind, _argv = _task9_bootstrap_probe_kind_and_argv_v1(probe)
    if (
        type(rows) is not tuple
        or len(rows) != 17
        or any(
            type(row) is not tuple
            or len(row) != 2
            or type(row[0]) is not str
            or type(row[1]) is not str
            or not row[0].isascii()
            or not row[1].isascii()
            or "\0" in row[0]
            or "\0" in row[1]
            for row in rows
        )
        or tuple(sorted(rows)) != rows
        or len(dict(rows)) != 17
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_environment_invalid"
        )
    values = dict(rows)
    if (
        tuple(
            row for row in rows
            if row[0] not in (
                "HOME", "INCI_TASK9_BOOTSTRAP_PATH_PROBE",
                "PYTHONPYCACHEPREFIX",
            )
        )
        != _TASK9_FIXED_ENVIRONMENT_ROWS_V1
        or values.get("INCI_TASK9_BOOTSTRAP_PATH_PROBE") != kind
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_environment_invalid"
        )
    home_prefix = f"/tmp/inci-task9-home-bootstrap-probe-{kind.lower()}-"
    pycache_prefix = (
        f"/tmp/inci-task9-pycache-bootstrap-probe-{kind.lower()}-"
    )
    home = values.get("HOME")
    pycache = values.get("PYTHONPYCACHEPREFIX")
    if (
        type(home) is not str
        or type(pycache) is not str
        or not home.startswith(home_prefix)
        or not pycache.startswith(pycache_prefix)
        or not home[len(home_prefix):].isdigit()
        or not pycache[len(pycache_prefix):].isdigit()
        or home[len(home_prefix):].startswith("0")
        or pycache[len(pycache_prefix):].startswith("0")
        or home[len(home_prefix):] != pycache[len(pycache_prefix):]
        or "/" in home[len("/tmp/"):]
        or "/" in pycache[len("/tmp/"):]
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_environment_invalid"
        )
    return home, pycache


def _task9_bootstrap_make_probe_environment_rows_v1(
    authority_record: dict[str, object],
    probe: object,
) -> tuple[tuple[str, str], ...]:
    kind, _argv = _task9_bootstrap_probe_kind_and_argv_v1(probe)
    coordinate = authority_record.get("allocation_coordinate")
    if type(coordinate) is not int or coordinate <= 0:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_environment_invalid"
        )
    home = (
        f"/tmp/inci-task9-home-bootstrap-probe-{kind.lower()}-{coordinate}"
    )
    pycache = (
        f"/tmp/inci-task9-pycache-bootstrap-probe-{kind.lower()}-"
        f"{coordinate}"
    )
    rows = tuple(sorted((
        *_TASK9_FIXED_ENVIRONMENT_ROWS_V1,
        ("HOME", home),
        ("INCI_TASK9_BOOTSTRAP_PATH_PROBE", kind),
        ("PYTHONPYCACHEPREFIX", pycache),
    )))
    _task9_bootstrap_validate_probe_environment_rows_v1(rows, probe)
    created: list[str] = []
    try:
        for path in (home, pycache):
            _os.mkdir(path, 0o700)
            value = _os.stat(path, follow_symlinks=False)
            if (
                not _stat.S_ISDIR(value.st_mode)
                or value.st_uid != _os.geteuid()
                or _stat.S_IMODE(value.st_mode) != 0o700
            ):
                raise Task9TransitionEvidenceError(
                    "task9_bootstrap_environment_invalid"
                )
            created.append(path)
    except Exception:
        for path in reversed(created):
            try:
                _os.rmdir(path)
            except OSError:
                pass
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_environment_invalid"
        ) from None
    return rows


def _task9_bootstrap_remove_directory_contents_v1(directory_fd: int) -> None:
    for name in _os.listdir(directory_fd):
        value = _os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if _stat.S_ISDIR(value.st_mode):
            child_fd = _os.open(name, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC, dir_fd=directory_fd)
            try:
                _task9_bootstrap_remove_directory_contents_v1(child_fd)
            finally:
                _os.close(child_fd)
            _os.rmdir(name, dir_fd=directory_fd)
        elif _stat.S_ISREG(value.st_mode):
            _os.unlink(name, dir_fd=directory_fd)
        else:
            raise Task9TransitionEvidenceError("task9_bootstrap_environment_cleanup_uncertain")


def _task9_bootstrap_cleanup_probe_environment_v1(
    rows: tuple[tuple[str, str], ...],
    probe: object,
) -> None:
    paths = _task9_bootstrap_validate_probe_environment_rows_v1(rows, probe)
    tmp_fd = -1
    try:
        tmp_fd = _os.open(
            "/private/tmp",
            _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
        )
        for path in paths:
            if not path.startswith("/tmp/inci-task9-") or "/" in path[len("/tmp/"):]:
                raise Task9TransitionEvidenceError("task9_bootstrap_environment_cleanup_uncertain")
            name = path[len("/tmp/"):]
            directory_fd = _os.open(name, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC, dir_fd=tmp_fd)
            try:
                value = _os.fstat(directory_fd)
                if value.st_uid != _os.geteuid() or _stat.S_IMODE(value.st_mode) != 0o700:
                    raise Task9TransitionEvidenceError("task9_bootstrap_environment_cleanup_uncertain")
                _task9_bootstrap_remove_directory_contents_v1(directory_fd)
            finally:
                _os.close(directory_fd)
            _os.rmdir(name, dir_fd=tmp_fd)
    except Task9TransitionEvidenceError:
        raise
    except OSError:
        raise Task9TransitionEvidenceError("task9_bootstrap_environment_cleanup_uncertain") from None
    finally:
        if tmp_fd >= 0:
            _os.close(tmp_fd)


def _task9_bootstrap_authority_for_child_v1(authority: object) -> dict[str, object]:
    if type(authority) is not Task9EvidenceRootAuthorityV1:
        raise Task9TransitionEvidenceError("task9_bootstrap_authority_invalid")
    record = _task9_get_live_record_v1(_TASK9_BOOTSTRAP_ROOT_LEDGER, authority)
    if (
        record is None or record["state"] not in ("FRESH", "LEASED")
        or record["pid"] != _os.getpid() or record["thread"] != _threading.get_ident()
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_authority_invalid")
    return record


def _task9_bootstrap_descriptor_cwd_v1(
    authority_record: dict[str, object],
) -> str:
    root_fd = authority_record.get("root_fd")
    if type(root_fd) is not int or root_fd < 0:
        raise Task9TransitionEvidenceError("task9_bootstrap_root_invalid")
    try:
        raw = _fcntl.fcntl(root_fd, _fcntl.F_GETPATH, b"\0" * 1024)
        if type(raw) is not bytes or b"\0" not in raw:
            raise Task9TransitionEvidenceError("task9_bootstrap_root_invalid")
        path_bytes, padding = raw.split(b"\0", 1)
        if not path_bytes or any(padding):
            raise Task9TransitionEvidenceError("task9_bootstrap_root_invalid")
        path = path_bytes.decode("utf-8", "strict")
        if not path.startswith("/") or "\0" in path:
            raise Task9TransitionEvidenceError("task9_bootstrap_root_invalid")
        reopened_fd = _task9_bootstrap_open_absolute_v1(path, directory=True)
        try:
            identity = _task9_bootstrap_full_stat_identity_v1(
                _os.fstat(reopened_fd)
            )
        finally:
            _os.close(reopened_fd)
    except Task9TransitionEvidenceError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_root_invalid"
        ) from None
    if identity != authority_record.get("root_identity"):
        raise Task9TransitionEvidenceError("task9_bootstrap_root_drift")
    return path


def _task9_bootstrap_terminate_process_group_v1(
    process: _subprocess.Popen, *, termination_grace_ns: int
) -> None:
    if process.poll() is not None:
        process.wait()
        return
    try:
        _os.killpg(process.pid, _signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            _os.killpg(process.pid, _signal.SIGKILL)
        except OSError:
            pass
        process.wait()
        raise Task9TransitionEvidenceError("task9_bootstrap_child_cleanup_uncertain") from None
    try:
        process.wait(timeout=termination_grace_ns / 1_000_000_000)
        return
    except _subprocess.TimeoutExpired:
        pass
    try:
        _os.killpg(process.pid, _signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        process.wait()
        raise Task9TransitionEvidenceError("task9_bootstrap_child_cleanup_uncertain") from None
    process.wait()


def _run_task9_fixed_bootstrap_probe_v1(
    root_authority: Task9EvidenceRootAuthorityV1,
    probe: object,
) -> tuple[tuple[tuple[str, str], ...], int, int, bytes, int, int]:
    kind, argv = _task9_bootstrap_probe_kind_and_argv_v1(probe)
    authority_record = _task9_bootstrap_authority_for_child_v1(root_authority)
    _task9_bootstrap_validate_authority_root_v1(authority_record)
    descriptor_cwd = _task9_bootstrap_descriptor_cwd_v1(authority_record)
    environment_rows = _task9_bootstrap_make_probe_environment_rows_v1(
        authority_record, probe
    )
    environment = dict(environment_rows)
    process = None
    selector = _selectors.DefaultSelector()
    started = _time.monotonic_ns()
    timeout_ns = 300_000_000_000
    termination_grace_ns = 5_000_000_000
    try:
        process = _subprocess.Popen(
            argv, cwd=descriptor_cwd, env=environment,
            stdin=_subprocess.DEVNULL, stdout=_subprocess.PIPE,
            stderr=_subprocess.STDOUT, shell=False, start_new_session=True,
            close_fds=True,
        )
        if process.stdout is None:
            raise Task9TransitionEvidenceError("task9_bootstrap_command_invalid")
        _os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, _selectors.EVENT_READ)
        output = bytearray()
        deadline = started + timeout_ns
        eof = False
        while not eof or process.poll() is None:
            now = _time.monotonic_ns()
            if now >= deadline:
                _task9_bootstrap_terminate_process_group_v1(process, termination_grace_ns=termination_grace_ns)
                raise Task9TransitionEvidenceError("task9_bootstrap_command_timeout")
            events = selector.select(min((deadline - now) / 1_000_000_000, 0.1))
            for key, _ in events:
                try:
                    chunk = _os.read(key.fileobj.fileno(), 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    eof = True
                    selector.unregister(key.fileobj)
                    break
                output.extend(chunk)
                if len(output) > 1_048_576:
                    _task9_bootstrap_terminate_process_group_v1(process, termination_grace_ns=termination_grace_ns)
                    raise Task9TransitionEvidenceError("task9_bootstrap_output_cap_exceeded")
        exit_code = process.wait()
        completed = _time.monotonic_ns()
        if (
            type(process.pid) is not int
            or process.pid <= 0
            or completed <= started
            or _task9_bootstrap_descriptor_cwd_v1(authority_record)
            != descriptor_cwd
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_command_invalid"
            )
        return (
            environment_rows, process.pid, exit_code, bytes(output),
            started, completed,
        )
    except Task9TransitionEvidenceError:
        if process is not None and process.poll() is None:
            _task9_bootstrap_terminate_process_group_v1(process, termination_grace_ns=termination_grace_ns)
        raise
    except Exception:
        if process is not None and process.poll() is None:
            _task9_bootstrap_terminate_process_group_v1(process, termination_grace_ns=termination_grace_ns)
        raise Task9TransitionEvidenceError("task9_bootstrap_command_invalid") from None
    finally:
        selector.close()
        if process is not None and process.stdout is not None:
            process.stdout.close()
        _task9_bootstrap_cleanup_probe_environment_v1(
            environment_rows, probe
        )


_TASK9_UNITTEST_RAN_RE_V1 = _re.compile(r"^Ran ([0-9]+) tests? in ([0-9]+)\.([0-9]{3})s$")
_TASK9_UNITTEST_OK_RE_V1 = _re.compile(r"^OK(?: \(skipped=([0-9]+)\))?$")
_TASK9_UNITTEST_FAILED_RE_V1 = _re.compile(r"^FAILED \((.+)\)$")


def _parse_task9_unittest_v314_v1(
    output: bytes, *, exit_code: int, expected_outcome_kind: str
) -> tuple[int, int, int, int, int]:
    if type(output) is not bytes or len(output) > 1_048_576 or b"\r" in output or b"\0" in output:
        raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid")
    try:
        text = output.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid") from None
    if text.encode("utf-8") != output:
        raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid")
    lines = text.split("\n")
    summaries = tuple(match for line in lines if (match := _TASK9_UNITTEST_RAN_RE_V1.fullmatch(line)) is not None)
    if len(summaries) != 1:
        raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid")
    test_count = int(summaries[0].group(1))
    milliseconds = 1000 * int(summaries[0].group(2)) + int(summaries[0].group(3))
    terminals = tuple(line for line in lines if _TASK9_UNITTEST_OK_RE_V1.fullmatch(line) or _TASK9_UNITTEST_FAILED_RE_V1.fullmatch(line))
    if len(terminals) != 1:
        raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid")
    failures = errors = skipped = 0
    ok_match = _TASK9_UNITTEST_OK_RE_V1.fullmatch(terminals[0])
    if ok_match:
        skipped = int(ok_match.group(1) or 0)
    else:
        entries = {}
        for item in _TASK9_UNITTEST_FAILED_RE_V1.fullmatch(terminals[0]).group(1).split(", "):
            try:
                name, value = item.split("=", 1)
            except ValueError:
                raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid") from None
            if name not in ("failures", "errors", "skipped") or not value.isdigit() or name in entries:
                raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid")
            entries[name] = int(value)
        failures, errors, skipped = entries.get("failures", 0), entries.get("errors", 0), entries.get("skipped", 0)
    if expected_outcome_kind == "GREEN":
        if exit_code != 0 or ok_match is None or test_count < 1 or failures or errors:
            raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid")
    elif expected_outcome_kind == "SEMANTIC_RED":
        if exit_code != 1 or test_count < 1 or failures < 1 or errors or "_FailedTest" in text or "ModuleNotFoundError" in text:
            raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid")
    else:
        raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid")
    return test_count, failures, errors, skipped, milliseconds


def _task9_bootstrap_probe_environment_rows_sha256_v1(
    probe: object,
    rows: tuple[tuple[str, str], ...],
) -> str:
    kind, _argv = _task9_bootstrap_probe_kind_and_argv_v1(probe)
    _task9_bootstrap_validate_probe_environment_rows_v1(rows, probe)
    return _task9_bootstrap_domain_sha256_v1(
        "INCI-TASK-9-BOOTSTRAP-PROBE-ENVIRONMENT-ROWS-V1",
        {"schema_version": 1, "probe_kind": kind, "rows": rows},
    )


def _task9_bootstrap_validate_probe_stdout_v1(
    probe: object,
    output: bytes,
    expected_search_row_projection_sha256: str,
) -> tuple[str, int, str, str]:
    kind, _argv = _task9_bootstrap_probe_kind_and_argv_v1(probe)
    if (
        type(output) is not bytes
        or len(output) > 1_048_576
        or b"\r" in output
        or b"\0" in output
        or not _task9_is_sha256(expected_search_row_projection_sha256)
    ):
        raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid")
    try:
        text = output.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_output_invalid"
        ) from None
    if text.encode("utf-8", "strict") != output:
        raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid")
    pattern = _re.compile(
        rf"^INCI_TASK9_CHILD_PATH_V1 {kind} ([0-9a-f]{{64}})$",
        _re.MULTILINE,
    )
    matches = pattern.findall(text)
    if matches != [expected_search_row_projection_sha256]:
        raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid")
    sentinel = (
        f"INCI_TASK9_CHILD_PATH_V1 {kind} "
        f"{expected_search_row_projection_sha256}\n"
    ).encode("ascii")
    if probe is _TASK9_BOOTSTRAP_FROZEN_V6_PROBE_V1 and output != sentinel:
        raise Task9TransitionEvidenceError("task9_bootstrap_output_invalid")
    return (
        text,
        len(output),
        hashlib.sha256(output).hexdigest(),
        hashlib.sha256(
            b"INCI-TASK-9-CHILD-PATH-PROBE-SENTINEL-V1\0" + sentinel
        ).hexdigest(),
    )


def _validate_task9_bootstrap_run_observation_v1(
    value: object,
) -> Task9BootstrapRunObservationV1:
    if (
        type(value) is not Task9BootstrapRunObservationV1
        or type(value.schema_version) is not int
        or value.schema_version != 1
        or value.observation_kind
        != "PD_INTEGRATION_BOOTSTRAP_RUN_OBSERVATION_V1"
        or value.fixed_test_target != _TASK9_BOOTSTRAP_FIXED_TEST_TARGET_V1
        or value.unittest_returncode != 0
        or value.frozen_v6_returncode != 0
        or (value.tests_run, value.failures, value.errors, value.skipped)
        != (1, 0, 0, 0)
        or value.semantic_outcome != "GREEN"
        or type(value.unittest_child_pid) is not int
        or type(value.frozen_v6_child_pid) is not int
        or value.unittest_child_pid <= 0
        or value.frozen_v6_child_pid <= 0
        or value.unittest_child_pid == value.frozen_v6_child_pid
        or not (
            value.started_monotonic_ns
            < value.unittest_completed_monotonic_ns
            <= value.frozen_v6_started_monotonic_ns
            < value.frozen_v6_completed_monotonic_ns
            <= value.completed_monotonic_ns
        )
        or value.wall_duration_ns
        != value.completed_monotonic_ns - value.started_monotonic_ns
    ):
        raise Task9TransitionEvidenceError(
            "task9_bootstrap_observation_invalid"
        )
    _task9_bootstrap_validate_probe_environment_rows_v1(
        value.unittest_environment_rows,
        _TASK9_BOOTSTRAP_UNITTEST_PROBE_V1,
    )
    _task9_bootstrap_validate_probe_environment_rows_v1(
        value.frozen_v6_environment_rows,
        _TASK9_BOOTSTRAP_FROZEN_V6_PROBE_V1,
    )
    _validate_task9_bootstrap_snapshot_pair_v1(
        value.before_snapshot, value.after_snapshot
    )
    _task9_bootstrap_validate_self_v1(
        value,
        "observation_sha256",
        "INCI-TASK-9-PD-INTEGRATION-BOOTSTRAP-RUN-OBSERVATION-V1",
    )
    return value


def _run_task9_command_bootstrap_exercise_v1(
) -> Task9BootstrapRunObservationV1:
    authority = _issue_task9_evidence_root_authority_v1()
    lease: _Task9BootstrapMutationLeaseV1 | None = None
    try:
        lease = _acquire_task9_bootstrap_mutation_lease_v1(authority)
        started = _time.monotonic_ns()
        before, dependency, interpreter = (
            _task9_capture_bootstrap_path_closure_from_lease_v1(lease)
        )
        expected_search_sha = (
            before.interpreter_evidence.path_closure_evidence
            .import_search_path_evidence.row_projection_sha256
        )
        (
            unittest_rows,
            unittest_pid,
            unittest_returncode,
            unittest_output,
            _unittest_started,
            unittest_completed,
        ) = _run_task9_fixed_bootstrap_probe_v1(
            authority, _TASK9_BOOTSTRAP_UNITTEST_PROBE_V1
        )
        tests_run, failures, errors, skipped, _duration_ms = (
            _parse_task9_unittest_v314_v1(
                unittest_output,
                exit_code=unittest_returncode,
                expected_outcome_kind="GREEN",
            )
        )
        if (tests_run, failures, errors, skipped) != (1, 0, 0, 0):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_output_invalid"
            )
        unittest_text, unittest_size, unittest_sha, unittest_sentinel_sha = (
            _task9_bootstrap_validate_probe_stdout_v1(
                _TASK9_BOOTSTRAP_UNITTEST_PROBE_V1,
                unittest_output,
                expected_search_sha,
            )
        )
        (
            frozen_rows,
            frozen_pid,
            frozen_returncode,
            frozen_output,
            frozen_started,
            frozen_completed,
        ) = _run_task9_fixed_bootstrap_probe_v1(
            authority, _TASK9_BOOTSTRAP_FROZEN_V6_PROBE_V1
        )
        if frozen_returncode != 0:
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_output_invalid"
            )
        frozen_text, frozen_size, frozen_sha, frozen_sentinel_sha = (
            _task9_bootstrap_validate_probe_stdout_v1(
                _TASK9_BOOTSTRAP_FROZEN_V6_PROBE_V1,
                frozen_output,
                expected_search_sha,
            )
        )
        after, _after_dependency, _after_interpreter = (
            _task9_capture_bootstrap_path_closure_from_lease_v1(
                lease,
                baseline_dependency=dependency,
                baseline_interpreter=interpreter,
            )
        )
        _validate_task9_bootstrap_snapshot_pair_v1(before, after)
        completed = _time.monotonic_ns()
        if not (
            started < unittest_completed <= frozen_started
            < frozen_completed <= completed
        ):
            raise Task9TransitionEvidenceError(
                "task9_bootstrap_observation_invalid"
            )
        projection = {
            "schema_version": 1,
            "observation_kind": (
                "PD_INTEGRATION_BOOTSTRAP_RUN_OBSERVATION_V1"
            ),
            "fixed_test_target": _TASK9_BOOTSTRAP_FIXED_TEST_TARGET_V1,
            "before_snapshot": before,
            "after_snapshot": after,
            "unittest_environment_rows": unittest_rows,
            "unittest_environment_rows_sha256": (
                _task9_bootstrap_probe_environment_rows_sha256_v1(
                    _TASK9_BOOTSTRAP_UNITTEST_PROBE_V1, unittest_rows
                )
            ),
            "frozen_v6_environment_rows": frozen_rows,
            "frozen_v6_environment_rows_sha256": (
                _task9_bootstrap_probe_environment_rows_sha256_v1(
                    _TASK9_BOOTSTRAP_FROZEN_V6_PROBE_V1, frozen_rows
                )
            ),
            "unittest_stdout_utf8": unittest_text,
            "unittest_stdout_size": unittest_size,
            "unittest_stdout_sha256": unittest_sha,
            "frozen_v6_stdout_utf8": frozen_text,
            "frozen_v6_stdout_size": frozen_size,
            "frozen_v6_stdout_sha256": frozen_sha,
            "unittest_search_row_projection_sha256": expected_search_sha,
            "frozen_v6_search_row_projection_sha256": expected_search_sha,
            "unittest_sentinel_sha256": unittest_sentinel_sha,
            "frozen_v6_sentinel_sha256": frozen_sentinel_sha,
            "started_monotonic_ns": started,
            "unittest_child_pid": unittest_pid,
            "unittest_completed_monotonic_ns": unittest_completed,
            "frozen_v6_started_monotonic_ns": frozen_started,
            "frozen_v6_child_pid": frozen_pid,
            "frozen_v6_completed_monotonic_ns": frozen_completed,
            "completed_monotonic_ns": completed,
            "wall_duration_ns": completed - started,
            "unittest_returncode": unittest_returncode,
            "frozen_v6_returncode": frozen_returncode,
            "tests_run": tests_run,
            "failures": failures,
            "errors": errors,
            "skipped": skipped,
            "semantic_outcome": "GREEN",
        }
        observation = Task9BootstrapRunObservationV1(
            **projection,
            observation_sha256=_task9_bootstrap_domain_sha256_v1(
                "INCI-TASK-9-PD-INTEGRATION-BOOTSTRAP-RUN-OBSERVATION-V1",
                projection,
            ),
        )
        return _validate_task9_bootstrap_run_observation_v1(observation)
    finally:
        if lease is not None:
            _release_task9_bootstrap_mutation_lease_v1(lease)
        else:
            _revoke_task9_evidence_root_authority_v1(authority)


def _task9_bootstrap_runtime_allowance_v1(interpreter: Task9InterpreterEvidenceV1) -> Task9CommandRuntimeAllowanceV1:
    projection = {"schema_version": 1, "allowance_id": "TASK9_OFFLINE_RUNTIME_ALLOWANCE_V1", "allowed_stdlib_inventory_sha256": interpreter.runtime_inventory_sha256, "allowed_external_distribution_names": ("certifi", "cffi", "charset-normalizer", "cryptography", "idna", "pycparser", "requests", "urllib3")}
    return Task9CommandRuntimeAllowanceV1(**projection, allowance_sha256=_task9_bootstrap_domain_sha256_v1("INCI-TASK-9-COMMAND-RUNTIME-ALLOWANCE-V1", projection))


# TASK9_ROUND19_COMMAND_BOOTSTRAP_END_V1
