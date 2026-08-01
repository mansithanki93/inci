from __future__ import annotations

import ast
from collections import Counter
import hashlib
import os
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "tennis_v1"

EXPECTED_PRODUCTION_AST_SHA256 = {
    "__init__.py": "e0a5bfe8f91850d9f32ada6cb9c23d137a5aaecae6dacf994be11914242fa174",
    "adapter_contract.py": "a6173a6e57daaef73158ed832064ee60117e30c49f5587db28d26d99e8f33233",
    "canonical.py": "86fef75db429de6cdcc31bcea177f68abdbf7e1ff95764ab1f4a169f05e3bd12",
    "capture.py": "4130604d14ef6ad386713dbc0fcd421b70ac8afadabe21db934c91f89969ac53",
    "codec.py": "a950b3429b12cb385458877ca61d71bc256e6b9d2e5f2a0e8b9f3f1b7fe168f6",
    "config.py": "89300cd54c1c91abc5903f7e622d7898c4b1fc63e890482e3233de4a1dc24129",
    "entitlements.py": "220c15ee8f37d9645c551905ae3f8d52007e6a36d19a549f25756f4f96f9a25d",
    "events.py": "6fd257d629d43536a078889738259c4a9296194ae5b915c9c518b64f11d83db2",
    "fingerprints.py": "89ec41022c491edb74a759ce544cb23eb19291a69ae7487173cd7516c4814a74",
    "ingress.py": "556540dddc47a96e5a8335f18d53daeca997c15884f596e168c8ccc93e558ad8",
    "mailbox.py": "7e3260f0046f966f60ba728509a2e10126b43ea553c1a202589b294a883902aa",
    "pinned_file.py": "9a014f9e4bef544f9f0fd1617b0dfa38d12cb5200cbf9d0e90be1951ce9483b5",
    "preflight.py": "af6906c62937ed57bc329340f4ae8bc709e535c3b56cc75605fd93da028c0623",
    "qualification_protocol.py": "5ef03ec162c858120cfb83dc87c4bf9e86dc969b4ec389b96d6b8d1a8a0d75de",
    "reducer.py": "2a17d3b76b49b7f202a8447e3c9f0c806976c3fba2435821a2119ef398c88916",
    "replay_core.py": "6df8de8ae1ce026157e21fe2f7549ccba2c861aad0bf7351bae7a49949ae79e0",
    "retention.py": "3e2131aacbc7b69d1460e9db0a6cb0e2c7aec7d5dc1341ccbf5a6316b1acc292",
    "sequencer.py": "da55de5d3e611197401b90a5f6dd4871cd1d6e0794e79d6e75ee335f72a32638",
    "session.py": "9839b3b10ecc6bba9a440165fbd0766105f0b31603be5c8297339e338863f8c3",
    "state.py": "92fbf5ac81481d0e107bc575c597f79afb9ac4f6e07cbc2cbd59da30ccaf8ac2",
    "wal.py": "019ef29f8f7aec08a89a98962d57ea58b7e74e701b5f97763e7279cfdaeb32a5",
}

EXPECTED_SAFE_FIXTURE_AST_SHA256 = frozenset(
    {
        (
            "adapter_contract.py",
            "47d3c6c445c60a52b0077011745ef7b341a51645871f67d4837d711605d4c8f6",
        ),
        (
            "capture.py",
            "1b66f7472ca0a4219742c79a6c092df9838dfc45ddf167e169052bc9add346db",
        ),
        (
            "capture.py",
            "237b06f11cb82a4b6f252ae1a5c4f2538ef46f09ee8f6d512f62782b50cb422a",
        ),
        (
            "capture.py",
            "e7d5bd310c0a762fd68e53e14b2836288600c2ba0dbf16a6afc6078a69bb07f8",
        ),
        (
            "entitlements.py",
            "3b04e12c019d0ad1c8bfaba1a03ccbe9e9ffed8906614ba24127163d107d1500",
        ),
        (
            "entitlements.py",
            "59a142e97fbcf8ee3793c6c1070a6506b79902a8c58988cc08eb28d66af55c8f",
        ),
        (
            "events.py",
            "6f186608a64ecf51e94336a3250a5008824e864531ff10ff83b251d7a5375c2e",
        ),
        (
            "events.py",
            "ab2370b74baf38fe0bfa4c2cb9e13b9e51be77bd1e6b5d8ffc4c3b1e11404a27",
        ),
        (
            "fingerprints.py",
            "988ba1629c7e2ace2500418e710143aefd84ce6f88d71eeaaac5200e1f5b667d",
        ),
        (
            "ingress.py",
            "17aa0e7366ea483896be5101da95212f950c866597480b79e1c9a8881447f778",
        ),
        (
            "mailbox.py",
            "6553230b8624d64bb1ebe7cbda5b10b56bfed3c0048f487c6cb42efee56653ed",
        ),
        (
            "mailbox.py",
            "d783f9cfb6ff2bf9d332708a045d883a293380e1d1f4ac33d70840f930038dfc",
        ),
        (
            "preflight.py",
            "0e6c9c15cce7ab3553dea2da546d0d2de6bf5999e51c008c7487a016fc63cc74",
        ),
        (
            "reducer.py",
            "bf7b4593e01f1f592844feb45f9e3b4e3b4b556bb62e0b7fb92e9f5831405c5d",
        ),
        (
            "replay_core.py",
            "703018421e9a16726bd6a3901c5796c3abc466a59922f1532f86b7f38a099f84",
        ),
        (
            "retention.py",
            "1ea2f343db5104488e87b349c266922f869ee866306e0c8446a85025b7b1463a",
        ),
        (
            "retention.py",
            "991f2e660fb36b580c1734cdd94a8614d1a5936510109c88bbe9e540c7f7a519",
        ),
        (
            "retention.py",
            "c97232a5e046999d62e7753e2f827679e30c4a8d1bf6fa4aec67bdcfe7e8c14c",
        ),
    }
)

ALLOWED_STDLIB_IMPORTS = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "errno",
    "fcntl",
    "hashlib",
    "hmac",
    "json",
    "math",
    "os",
    "pathlib",
    "posixpath",
    "queue",
    "re",
    "stat",
    "struct",
    "sys",
    "threading",
    "time",
    "types",
    "typing",
    "unicodedata",
    "uuid",
    "weakref",
}

ALLOWED_FROM_MEMBERS = {
    "__future__": {"annotations"},
    "collections": {"Counter", "defaultdict"},
    "collections.abc": {"Callable", "Mapping"},
    "dataclasses": {"dataclass", "field", "fields", "replace"},
    "datetime": {"datetime", "time", "timedelta", "timezone"},
    "enum": {"Enum"},
    "os": {"getpid"},
    "pathlib": {"Path", "PurePosixPath"},
    "types": {"MappingProxyType"},
    "typing": {
        "Callable",
        "Generator",
        "Iterator",
        "Literal",
        "Protocol",
    },
}

ALLOWED_RELATIVE_MEMBERS = {
    "adapter_contract": {
        "AdapterContract",
        "AdapterContractError",
        "AdapterUsagePlan",
        "AuthContract",
        "AuthMode",
        "ProviderQuotas",
        "derive_quota_demand",
        "load_active_adapter_contract",
    },
    "canonical": {"CanonicalJsonError", "canonical_json_bytes"},
    "capture": {
        "MAX_CAPTURE_BYTES",
        "CaptureValidationError",
        "validate_capture_against_authority",
        "validate_captured_input",
    },
    "codec": {
        "RecordCodecError",
        "canonical_record_sha256",
        "decode_record",
        "encode_record",
    },
    "config": {
        "TennisV1Config",
        "canonical_config_sha256",
        "load_config",
        "session_wal_path",
    },
    "entitlements": {
        "CoverageStratum",
        "IntendedUse",
        "PermissionArtifact",
        "PermissionOperation",
        "ProviderGate",
        "ProviderGateError",
        "ProviderManifest",
        "ProviderSessionPoll",
        "QualificationDecision",
        "QualificationReason",
        "QualificationStatus",
        "QualifiedProviderBinding",
        "ResearchRequest",
        "RequestedStratum",
        "_evaluate_provider_as_of",
        "_load_provider_manifest_restricted",
        "_request_sha256",
        "_snapshot_environment",
        "canonical_manifest_sha256",
        "opaque_id_sha256",
        "provider_request_binding_sha256",
    },
    "events": {
        "CaptureAuthority",
        "CapturedInput",
        "DerivedDraft",
        "PersistedEvent",
        "ProvenanceEvidence",
        "ProvenanceState",
        "RecordKind",
        "SessionCaptureAuthorizer",
        "SessionManifest",
        "SourceKind",
        "_exact_nonnegative_integer",
        "_safe_identifier",
        "_sha256",
        "_validate_content_type",
        "_validate_provenance",
        "_validate_session_id",
    },
    "pinned_file": {"PinnedFileError", "read_pinned_file"},
    "qualification_protocol": {
        "QUALIFICATION_PROTOCOL_V1",
        "qualification_protocol_sha256",
    },
    "reducer": {"initial_trace", "next_trace", "reduce_event"},
    "retention": {
        "ProviderWalReadCapability",
        "ProviderWalWriteCapability",
        "RESERVE_BYTES",
        "RetentionCoordinator",
        "RetentionDueDeleteError",
        "RetentionError",
        "RetentionGlobalHalt",
        "RetentionPrewriteCapacityError",
        "_ack_provider_wal_clean_terminal",
        "_claim_provider_wal_reader",
        "_claim_provider_wal_runtime",
        "_claim_provider_wal_writer",
        "_reject_expected_replay_manifest",
        "_reject_replay_manifest",
    },
    "sequencer": {
        "EventRuntime",
        "ProviderPersistenceAuthorizer",
        "WrongOwnerThread",
    },
    "session": {
        "canonical_session_manifest_bytes",
        "require_decision_matches_session",
        "session_manifest_sha256",
    },
    "state": {
        "FoundationState",
        "canonical_state_bytes",
        "initial_state",
    },
    "wal": {
        "DiskLowError",
        "JournalCorruptionError",
        "JournalDurabilityError",
        "JournalReader",
        "JournalValidationError",
        "JournalWriter",
        "ScanIssue",
        "ScanSummary",
    },
}

PROTECTED_GUARD_NAMES = {
    "AdapterUsagePlan",
    "CaptureAuthority",
    "EventRuntime",
    "ExpertRetentionClockSampleCapabilityV1",
    "ExpertStateRootAccountLockRequestV1",
    "PersistedEvent",
    "ProviderCapabilities",
    "ProviderQuotas",
    "ProviderWalReadCapability",
    "ProviderWalWriteCapability",
    "RetentionCoordinator",
    "RetentionMarker",
    "SessionManifest",
    "_ExpertRootGrantAuthority",
    "_ExpertRootRequestAuthority",
    "_ExpertStateRootAccountLockGrantV1",
    "dict",
    "type",
}

FORBIDDEN_IMPORT_ROOTS = {
    "aiohttp",
    "bot",
    "engine",
    "executor",
    "http",
    "httpx",
    "importlib",
    "kalshi_client",
    "market_data",
    "multiprocessing",
    "order_journal",
    "order_resolution",
    "replay",
    "requests",
    "research_log",
    "runpy",
    "safety",
    "socket",
    "subprocess",
    "urllib",
    "websockets",
}

FORBIDDEN_TRANSPORT_NAMES = {
    "client",
    "connect",
    "fetch",
    "http_client",
    "http_session",
    "send",
    "socket",
    "transport",
    "urlopen",
}
FORBIDDEN_CALL_NAMES = {
    "amend_order",
    "cancel_order",
    "connect",
    "create_order",
    "delete",
    "fetch",
    "patch",
    "place_order",
    "post",
    "put",
    "request",
    "send",
    "submit",
    "submit_order",
    "urlopen",
}
HTTP_METHODS = {
    "CONNECT",
    "DELETE",
    "GET",
    "HEAD",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
    "TRACE",
}
FORBIDDEN_BUILTINS = {
    "__import__",
    "breakpoint",
    "compile",
    "eval",
    "exec",
}

PREFLIGHT_ALLOWED_OS_CALLS = {
    "os.path.normpath",
}

PREFLIGHT_FORBIDDEN_PATH_FS_METHODS = {
    "absolute",
    "chmod",
    "exists",
    "expanduser",
    "glob",
    "group",
    "hardlink_to",
    "is_block_device",
    "is_char_device",
    "is_dir",
    "is_fifo",
    "is_file",
    "is_mount",
    "is_socket",
    "is_symlink",
    "iterdir",
    "lchmod",
    "lstat",
    "mkdir",
    "open",
    "owner",
    "read_bytes",
    "read_text",
    "readlink",
    "rename",
    "replace",
    "resolve",
    "rglob",
    "rmdir",
    "samefile",
    "stat",
    "symlink_to",
    "touch",
    "unlink",
    "walk",
    "write_bytes",
    "write_text",
}

PREFLIGHT_FORBIDDEN_OUTPUT_METHODS = {
    "close",
    "fileno",
    "flush",
    "read",
    "read1",
    "readinto",
    "readline",
    "readlines",
    "seek",
    "truncate",
    "write",
    "writelines",
}

EXPECTED_PREFLIGHT_FILESYSTEM_CALLS = Counter(
    {
        (
            "preflight.py",
            "_capture_repository_root",
            "Path(__file__).resolve(strict=True)",
        ): 1,
    }
)

EXPECTED_UNICODE_IMPORTS = Counter(
    {
        ("pinned_file.py", "import unicodedata"): 1,
        ("preflight.py", "import unicodedata"): 1,
    }
)

EXPECTED_UNICODE_CALLS = Counter(
    {
        (
            "pinned_file.py",
            "_reject_forbidden_root_overlap",
            "unicodedata.normalize('NFC', component)",
        ): 2,
        (
            "preflight.py",
            "_paths_overlap",
            "unicodedata.normalize('NFC', component)",
        ): 2,
    }
)

EXPECTED_PREFLIGHT_SIGNATURES = frozenset(
    {
        ("", "_capture_repository_root", (), (), None, (), None, 0, ()),
        (
            "EntitlementPreflightError",
            "__init__",
            (),
            ("self",),
            None,
            (),
            None,
            0,
            (),
        ),
        ("", "_safe_identifier", (), ("value",), None, (), None, 0, ()),
        ("", "_safe_sha256", (), ("value",), None, (), None, 0, ()),
        ("", "_exact_utc", (), ("value",), None, (), None, 0, ()),
        (
            "EntitlementPreflight",
            "__post_init__",
            (),
            ("self",),
            None,
            (),
            None,
            0,
            (),
        ),
        (
            "",
            "_lexically_normal_absolute_path",
            (),
            ("value",),
            None,
            (),
            None,
            0,
            (),
        ),
        (
            "",
            "_paths_overlap",
            (),
            ("left", "right"),
            None,
            (),
            None,
            0,
            (),
        ),
        (
            "",
            "_validate_config",
            (),
            ("config", "repository_root"),
            None,
            (),
            None,
            0,
            (),
        ),
        ("", "_validate_request", (), ("request",), None, (), None, 0, ()),
        (
            "",
            "_validate_binding_fields",
            (),
            ("binding",),
            None,
            (),
            None,
            0,
            (),
        ),
        (
            "",
            "_expected_binding",
            (),
            ("manifest", "request"),
            None,
            (),
            None,
            0,
            (),
        ),
        (
            "",
            "_validate_decision",
            (),
            ("config", "manifest", "request", "decision"),
            None,
            (),
            None,
            0,
            (),
        ),
        (
            "",
            "_planned_delete_by_ns",
            (),
            ("value",),
            None,
            (),
            None,
            0,
            (),
        ),
        (
            "",
            "_run_preflight",
            (),
            ("config", "request", "environ"),
            None,
            (),
            None,
            0,
            (),
        ),
        (
            "",
            "run_entitlement_preflight",
            (),
            ("config", "request"),
            None,
            ("environ",),
            None,
            0,
            (False,),
        ),
    }
)

SAFE_GET_RECEIVERS = {
    "adapter_contract.py": {"registry"},
    "entitlements.py": {"environ", "evidence_by_stratum"},
    "events.py": {"CONTROL_RECORD_CONTRACTS"},
    "reducer.py": {"epochs", "state_epochs"},
    "retention.py": {
        "self._expert_clock_capabilities",
        "self._expert_root_grants",
        "self._session_states",
        "self._write_capabilities",
        "self._write_tombstones",
        "self._read_capabilities",
        "self._read_tombstones",
        "value",
    },
}

EXPECTED_MODULE_CONTAINER_ASSIGNMENTS = {
    (
        "retention.py",
        "_validate_expert_root_binding",
        ("original_values",),
        (
            "(os.fstat(self._state_fd), os.fstat(self._sessions_fd), "
            "os.fstat(self._markers_fd), os.fstat(self._lock_fd))"
        ),
    ),
    (
        "retention.py",
        "_validate_expert_root_binding",
        ("duplicate_values",),
        (
            "(os.fstat(authority.state_fd), "
            "os.fstat(authority.sessions_fd), "
            "os.fstat(authority.markers_fd), "
            "os.fstat(authority.lock_fd))"
        ),
    ),
}

EXPECTED_QUEUE_CALLS = Counter(
    {
        ("ingress.py", "__init__", "queue.Queue(maxsize=self._capacity)"): 1,
        ("ingress.py", "_next_action_locked", "self._queue.empty()"): 1,
        ("ingress.py", "_runtime_failure", "self._queue.get_nowait()"): 1,
        (
            "ingress.py",
            "close_external_halt",
            "self._queue.get_nowait()",
        ): 1,
        ("ingress.py", "drain_one", "self._queue.get_nowait()"): 2,
        ("ingress.py", "enqueue", "self._queue.put_nowait(node)"): 1,
        ("mailbox.py", "__init__", "queue.Queue(maxsize=1)"): 1,
        ("mailbox.py", "publish", "self._queue.get_nowait()"): 1,
        ("mailbox.py", "publish", "self._queue.put_nowait(snapshot)"): 1,
        ("mailbox.py", "take", "self._queue.get(timeout=timeout)"): 1,
    }
)

EXPECTED_PARAMETER_CALLS = Counter(
    {
        ("adapter_contract.py", "_utc_datetime", "value.utcoffset()"): 1,
        ("canonical.py", "_validate", "value.items()"): 1,
        ("capture.py", "_build_capture_authority", "values.items()"): 1,
        ("capture.py", "_build_captured_input", "values.items()"): 1,
        (
            "capture.py",
            "_capture_common",
            "authority._clock_uncertainty_ns()",
        ): 1,
        (
            "capture.py",
            "_capture_common",
            "authority._monotonic_clock_ns()",
        ): 1,
        ("capture.py", "_capture_common", "authority._wall_clock_ns()"): 1,
        (
            "capture.py",
            "_capture_common",
            "performing_authorizer.authorize_capture(authority, candidate)",
        ): 1,
        ("capture.py", "_normalized_key", "value.casefold()"): 1,
        (
            "capture.py",
            "_validate_and_redact_json",
            "current.items()",
        ): 1,
        ("capture.py", "_parse_json", "content.decode('utf-8')"): 1,
        (
            "capture.py",
            "_parse_json",
            "content.startswith(b'\\xef\\xbb\\xbf')",
        ): 1,
        ("capture.py", "_unsafe_string", "value.upper()"): 1,
        ("codec.py", "_decode_enum", "enum_type(value)"): 1,
        ("codec.py", "_strict_object", "content.decode('utf-8')"): 1,
        (
            "codec.py",
            "_strict_object",
            "content.startswith(b'\\xef\\xbb\\xbf')",
        ): 1,
        ("config.py", "_parse_config_bytes", "content.decode('utf-8')"): 1,
        (
            "config.py",
            "_parse_config_bytes",
            "content.startswith(b'\\xef\\xbb\\xbf')",
        ): 1,
        ("entitlements.py", "_enum", "enum_type(value)"): 1,
        (
            "entitlements.py",
            "_evaluate_provider_as_of",
            "environ.get(name)",
        ): 1,
        (
            "entitlements.py",
            "_evaluate_provider_as_of",
            "value.strip()",
        ): 1,
        ("entitlements.py", "_is_aware_utc", "value.utcoffset()"): 1,
        ("entitlements.py", "_normalized_key", "value.lower()"): 1,
        (
            "entitlements.py",
            "_parse_json",
            "content.decode('utf-8')",
        ): 1,
        (
            "entitlements.py",
            "_parse_json",
            "content.startswith(b'\\xef\\xbb\\xbf')",
        ): 1,
        (
            "entitlements.py",
            "_reject_secret_shaped_keys",
            "value.items()",
        ): 1,
        (
            "entitlements.py",
            "format_utc",
            "value.strftime('%Y-%m-%dT%H:%M:%S.%fZ')",
        ): 1,
        (
            "entitlements.py",
            "format_utc",
            "value.strftime('%Y-%m-%dT%H:%M:%SZ')",
        ): 1,
        (
            "preflight.py",
            "_lexically_normal_absolute_path",
            "value.is_absolute()",
        ): 1,
        (
            "fingerprints.py",
            "_reject_symlinked_route",
            "candidate.expanduser()",
        ): 1,
        (
            "fingerprints.py",
            "_reject_symlinked_route",
            "expanded.is_absolute()",
        ): 1,
        (
            "ingress.py",
            "_finalize",
            "runtime.close_clean('operator_stop')",
        ): 1,
        (
            "ingress.py",
            "_finalize",
            "runtime.close_ingress_backpressure()",
        ): 1,
        (
            "ingress.py",
            "_finalize",
            "runtime.close_ingress_owner_unresponsive()",
        ): 1,
        (
            "ingress.py",
            "_finalize",
            "runtime.close_ingress_session_end()",
        ): 1,
        (
            "ingress.py",
            "close_external_halt",
            "runtime.close_halted('operator_halt')",
        ): 1,
        (
            "ingress.py",
            "close_external_halt",
            "runtime.close_ingress_backpressure()",
        ): 1,
        (
            "ingress.py",
            "close_external_halt",
            "runtime.close_ingress_owner_unresponsive()",
        ): 1,
        (
            "ingress.py",
            "close_external_halt",
            "runtime.require_owner()",
        ): 1,
        ("ingress.py", "_process_node", "node.completion.set()"): 1,
        (
            "ingress.py",
            "_process_node",
            "runtime.ingest(node.item.captured)",
        ): 1,
        ("ingress.py", "_settle_failed_node", "node.completion.set()"): 1,
        (
            "ingress.py",
            "drain_one",
            "runtime.check_ingress_session_end()",
        ): 1,
        ("ingress.py", "drain_one", "runtime.require_owner()"): 1,
        (
            "replay_core.py",
            "_manifest_from_start",
            "event.payload.decode('ascii')",
        ): 1,
        (
            "replay_core.py",
            "_replay",
            "coordinator.issue_read_capability("
            "persistence_authorizer=persistence_authorizer)",
        ): 1,
        (
            "replay_core.py",
            "_replay",
            "coordinator.recover_and_purge()",
        ): 1,
        (
            "replay_core.py",
            "_replay",
            "persistence_authorizer.authorize_analysis()",
        ): 1,
        (
            "replay_core.py",
            "_terminal_payload",
            "event.payload.decode('ascii')",
        ): 1,
        (
            "retention.py",
            "_claim_provider_wal_runtime",
            "coordinator._claim_provider_wal_runtime("
            "write_capability=write_capability, "
            "persistence_authorizer=persistence_authorizer, "
            "session_manifest=session_manifest)",
        ): 1,
        ("retention.py", "_open_state_root", "path.is_absolute()"): 1,
        (
            "retention.py",
            "_reject_expected_replay_manifest",
            "coordinator._reject_expected_replay_manifest("
            "expected_session_manifest_sha256="
            "expected_session_manifest_sha256, "
            "persistence_authorizer=persistence_authorizer)",
        ): 1,
        (
            "retention.py",
            "_reject_replay_manifest",
            "coordinator._reject_replay_manifest("
            "read_capability=read_capability, "
            "persistence_authorizer=persistence_authorizer, "
            "session_id=session_id)",
        ): 1,
        ("retention.py", "_strict_json", "content.decode('utf-8')"): 1,
        (
            "retention.py",
            "_strict_json",
            "content.startswith(b'\\xef\\xbb\\xbf')",
        ): 1,
        (
            "retention.py",
            "arm_before_wal",
            "persistence_authorizer.authorize_raw_persistence()",
        ): 1,
        (
            "retention.py",
            "arm_before_wal",
            "persistence_authorizer.authorize_session()",
        ): 1,
        (
            "retention.py",
            "_authorize_clean_close",
            "authorizer.authorize_close()",
        ): 1,
        (
            "retention.py",
            "_authorize_write",
            "authorizer.authorize_raw_persistence()",
        ): 1,
        (
            "retention.py",
            "issue_read_capability",
            "persistence_authorizer.authorize_analysis()",
        ): 1,
        (
            "sequencer.py",
            "__init__",
            "persistence_authorizer.authorize_session()",
        ): 1,
        (
            "sequencer.py",
            "__init__",
            "writer.claim_runtime("
            "persistence_authorizer=persistence_authorizer, "
            "coordinator=coordinator)",
        ): 1,
        (
            "sequencer.py",
            "bind_provider_persistence_authorizer",
            "gate.require_start()",
        ): 1,
        ("session.py", "_datetime_ns", "value.utcoffset()"): 2,
        (
            "session.py",
            "build_session_manifest",
            "qualification.require_eligible()",
        ): 1,
        (
            "session.py",
            "require_decision_matches_session",
            "decision.require_eligible()",
        ): 1,
        (
            "wal.py",
            "_strict_json_object",
            "content.decode('utf-8')",
        ): 1,
        (
            "wal.py",
            "_strict_json_object",
            "content.startswith(b'\\xef\\xbb\\xbf')",
        ): 1,
        ("wal.py", "create", "read_capability.close()"): 1,
        (
            "wal.py",
            "create",
            "read_capability.pread(offset=0, length=0)",
        ): 1,
        ("wal.py", "create", "write_capability.close()"): 1,
        ("wal.py", "create", "write_capability.write_all(prefix)"): 1,
    }
)

EXPECTED_FIELDS_CALLS = Counter(
    {
        ("codec.py", "<module>", "fields(PersistedEvent)"): 1,
        ("codec.py", "<module>", "fields(SessionManifest)"): 1,
        ("reducer.py", "initial_trace", "fields(SessionManifest)"): 1,
        ("replay_core.py", "_derived_signature", "fields(PersistedEvent)"): 1,
        ("retention.py", "_marker_projection", "fields(RetentionMarker)"): 1,
        ("retention.py", "<module>", "fields(RetentionMarker)"): 1,
        ("session.py", "_projection", "fields(SessionManifest)"): 1,
        ("wal.py", "<module>", "fields(SessionManifest)"): 1,
    }
)

EXPECTED_DATACLASS_FIELDS = Counter(
    {
        (
            "adapter_contract.py",
            "_usage_projection",
            "AdapterUsagePlan.__dataclass_fields__",
        ): 1,
        (
            "entitlements.py",
            "<module>",
            "ProviderQuotas.__dataclass_fields__",
        ): 1,
        (
            "entitlements.py",
            "_canonical_projection",
            "ProviderQuotas.__dataclass_fields__",
        ): 1,
        (
            "entitlements.py",
            "_parse_quotas",
            "ProviderQuotas.__dataclass_fields__",
        ): 1,
        (
            "entitlements.py",
            "_evaluate_provider_as_of",
            "ProviderQuotas.__dataclass_fields__",
        ): 1,
    }
)

EXPECTED_REFLECTION_CALLS = Counter(
    {
        (
            "adapter_contract.py",
            "_open_root",
            "hasattr",
            "hasattr(os, name)",
        ): 1,
        (
            "adapter_contract.py",
            "_usage_projection",
            "getattr",
            "getattr(usage, name)",
        ): 1,
        (
            "adapter_contract.py",
            "_validate_usage",
            "getattr",
            "getattr(usage, name)",
        ): 4,
        (
            "adapter_contract.py",
            "derive_quota_demand",
            "getattr",
            "getattr(request, 'now_utc', None)",
        ): 1,
        (
            "adapter_contract.py",
            "derive_quota_demand",
            "getattr",
            "getattr(request, 'requested_matches', None)",
        ): 1,
        (
            "adapter_contract.py",
            "derive_quota_demand",
            "getattr",
            "getattr(request, 'session_end_utc', None)",
        ): 1,
        (
            "capture.py",
            "issue_capture_authority",
            "getattr",
            "getattr(session_authorizer, 'authorize_capture', None)",
        ): 1,
        (
            "capture.py",
            "validate_capture_against_authority",
            "getattr",
            "getattr(owner, 'authorize_capture', None)",
        ): 1,
        (
            "capture.py",
            "validate_capture_against_authority",
            "object.__getattribute__",
            "object.__getattribute__(authority, '_session_authorizer')",
        ): 1,
        (
            "entitlements.py",
            "_bind_qualification",
            "getattr",
            "getattr(capabilities, name)",
        ): 1,
        (
            "entitlements.py",
            "_canonical_projection",
            "getattr",
            "getattr(manifest.capabilities, name)",
        ): 1,
        (
            "entitlements.py",
            "_canonical_projection",
            "getattr",
            "getattr(manifest.quotas, name)",
        ): 1,
        (
            "entitlements.py",
            "_evaluate_provider_as_of",
            "getattr",
            "getattr(demand, name)",
        ): 1,
        (
            "entitlements.py",
            "_evaluate_provider_as_of",
            "getattr",
            "getattr(manifest.capabilities, name)",
        ): 1,
        (
            "entitlements.py",
            "_evaluate_provider_as_of",
            "getattr",
            "getattr(manifest.quotas, name)",
        ): 1,
        (
            "events.py",
            "__post_init__",
            "getattr",
            "getattr(self, field_name)",
        ): 5,
        (
            "fingerprints.py",
            "code_sha256",
            "getattr",
            "getattr(os, 'O_CLOEXEC', 0)",
        ): 1,
        (
            "fingerprints.py",
            "code_sha256",
            "getattr",
            "getattr(os, 'O_NOFOLLOW', 0)",
        ): 1,
        (
            "pinned_file.py",
            "<module>",
            "hasattr",
            "hasattr(os, 'supports_dir_fd')",
        ): 1,
        (
            "pinned_file.py",
            "_require_posix_features",
            "hasattr",
            "hasattr(fcntl, 'flock')",
        ): 1,
        (
            "pinned_file.py",
            "_require_posix_features",
            "hasattr",
            "hasattr(os, item)",
        ): 1,
        (
            "reducer.py",
            "initial_trace",
            "getattr",
            "getattr(session_manifest, item.name)",
        ): 1,
        (
            "replay_core.py",
            "_derived_signature",
            "getattr",
            "getattr(event, item.name)",
        ): 1,
        (
            "retention.py",
            "__post_init__",
            "getattr",
            "getattr(self, name)",
        ): 2,
        (
            "retention.py",
            "_ack_provider_wal_clean_terminal",
            "object.__getattribute__",
            "object.__getattribute__(write_capability, '_dispatch')",
        ): 1,
        (
            "retention.py",
            "_allocate_reserve",
            "hasattr",
            "hasattr(os, 'posix_fallocate')",
        ): 1,
        (
            "retention.py",
            "_claim_provider_wal_reader",
            "object.__getattribute__",
            "object.__getattribute__(read_capability, '_dispatch')",
        ): 1,
        (
            "retention.py",
            "_claim_provider_wal_runtime",
            "object.__getattribute__",
            "object.__getattribute__(write_capability, '_dispatch')",
        ): 1,
        (
            "retention.py",
            "_claim_provider_wal_writer",
            "object.__getattribute__",
            "object.__getattribute__(write_capability, '_dispatch')",
        ): 1,
        (
            "retention.py",
            "_consume_expert_state_root_account_lock_request",
            "object.__getattribute__",
            "object.__getattribute__(request, '_dispatch')",
        ): 1,
        (
            "retention.py",
            "_close_write_locked",
            "getattr",
            "getattr(state, name)",
        ): 1,
        (
            "retention.py",
            "_close_write_locked",
            "setattr",
            "setattr(state, name, -1)",
        ): 1,
        (
            "retention.py",
            "_inventory",
            "locals",
            "locals()",
        ): 1,
        (
            "retention.py",
            "_marker_projection",
            "getattr",
            "getattr(marker, item.name)",
        ): 1,
        (
            "retention.py",
            "_open_existing_file",
            "hasattr",
            "hasattr(os, 'O_NONBLOCK')",
        ): 1,
        (
            "retention.py",
            "_open_existing_file",
            "locals",
            "locals()",
        ): 3,
        (
            "retention.py",
            "_open_lock",
            "hasattr",
            "hasattr(os, 'O_NONBLOCK')",
        ): 1,
        (
            "retention.py",
            "_reject_replay_manifest",
            "object.__getattribute__",
            "object.__getattribute__(read_capability, '_dispatch')",
        ): 1,
        (
            "retention.py",
            "_revoke_expert_state_root_account_lock_grant",
            "object.__getattribute__",
            "object.__getattribute__(grant, '_dispatch')",
        ): 1,
        (
            "retention.py",
            "_require_posix_features",
            "hasattr",
            "hasattr(fcntl, 'flock')",
        ): 1,
        (
            "retention.py",
            "_require_posix_features",
            "hasattr",
            "hasattr(os, 'geteuid')",
        ): 1,
        (
            "retention.py",
            "_require_posix_features",
            "hasattr",
            "hasattr(os, 'pread')",
        ): 1,
        (
            "retention.py",
            "_require_posix_features",
            "hasattr",
            "hasattr(os, name)",
        ): 1,
        (
            "retention.py",
            "_revoke_reads_for_global_halt",
            "hasattr",
            "hasattr(self, '_lock')",
        ): 1,
        (
            "retention.py",
            "_validate_expert_root_authority_locked",
            "object.__getattribute__",
            "object.__getattribute__(capability, '_dispatch')",
        ): 1,
        (
            "retention.py",
            "_validate_expert_root_binding",
            "object.__getattribute__",
            "object.__getattribute__(authority.grant, '_clock_capability')",
        ): 1,
        (
            "retention.py",
            "_validate_expert_root_binding",
            "object.__getattribute__",
            "object.__getattribute__(authority.grant, '_dispatch')",
        ): 1,
        (
            "retention.py",
            "_validate_expert_root_binding",
            "object.__getattribute__",
            "object.__getattribute__(authority.grant, '_lock_fd')",
        ): 1,
        (
            "retention.py",
            "_validate_expert_root_binding",
            "object.__getattribute__",
            "object.__getattribute__(authority.grant, '_markers_fd')",
        ): 1,
        (
            "retention.py",
            "_validate_expert_root_binding",
            "object.__getattribute__",
            "object.__getattribute__(authority.grant, '_sessions_fd')",
        ): 1,
        (
            "retention.py",
            "_validate_expert_root_binding",
            "object.__getattribute__",
            "object.__getattribute__(authority.grant, '_state_fd')",
        ): 1,
        (
            "retention.py",
            "_validate_file_stat",
            "hasattr",
            "hasattr(value, 'st_blocks')",
        ): 1,
        (
            "retention.py",
            "close",
            "getattr",
            "getattr(self, fd_name)",
        ): 1,
        (
            "retention.py",
            "close",
            "hasattr",
            "hasattr(self, '_lock')",
        ): 1,
        (
            "retention.py",
            "close",
            "setattr",
            "setattr(self, fd_name, -1)",
        ): 1,
        (
            "retention.py",
            "issue_read_capability",
            "locals",
            "locals()",
        ): 1,
        (
            "retention.py",
            "sample_expert_retention_wall_ns",
            "object.__getattribute__",
            "object.__getattribute__(capability, '_dispatch')",
        ): 1,
        (
            "session.py",
            "_projection",
            "getattr",
            "getattr(manifest, field.name)",
        ): 1,
    }
)


class DependencyPolicyViolation(ValueError):
    pass


def _static_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left)
        right = _static_string(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and type(value.value) is str:
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                rendered = _static_string(value.value)
                if rendered is None:
                    return None
                parts.append(rendered)
            else:
                return None
        return "".join(parts)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and not node.keywords
    ):
        template = _static_string(node.func.value)
        arguments = [_static_string(item) for item in node.args]
        if template is None or any(item is None for item in arguments):
            return None
        try:
            return template.format(*arguments)
        except (IndexError, KeyError, ValueError):
            return None
    return None


def _target_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(
            name for item in node.elts for name in _target_names(item)
        )
    return ()


class DependencyPolicy:
    def __init__(self, source: str, filename: str):
        self.source = source
        self.relative_filename = Path(filename).as_posix()
        self.filename = self.relative_filename
        self.tree = ast.parse(source, filename=filename)
        self.parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent
        self.aliases: dict[str, str] = {}
        self.imported_names: set[str] = set()
        self.get_result_names: set[str] = set()
        self.get_result_targets: set[str] = set()
        self.violations: list[str] = []
        self.reflection_calls: Counter[tuple[str, str, str, str]] = Counter()
        self.fields_calls: Counter[tuple[str, str, str]] = Counter()
        self.dataclass_fields: Counter[tuple[str, str, str]] = Counter()
        self.queue_calls: Counter[tuple[str, str, str]] = Counter()
        self.parameter_calls: Counter[tuple[str, str, str]] = Counter()
        self.preflight_filesystem_calls: Counter[
            tuple[str, str, str]
        ] = Counter()
        self.unicode_imports: Counter[tuple[str, str]] = Counter()
        self.unicode_calls: Counter[tuple[str, str, str]] = Counter()
        self._caller_taints: dict[
            ast.FunctionDef | ast.AsyncFunctionDef,
            set[str],
        ] = {}
        self._caller_containers: dict[
            ast.FunctionDef | ast.AsyncFunctionDef,
            set[str],
        ] = {}

    def _function_name(self, node: ast.AST) -> str:
        current = node
        while current in self.parents:
            current = self.parents[current]
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return current.name
        return "<module>"

    def _enclosing_function(
        self,
        node: ast.AST,
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        current = node
        while current in self.parents:
            current = self.parents[current]
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return current
        return None

    def _enclosing_class(self, node: ast.AST) -> ast.ClassDef | None:
        current = node
        while current in self.parents:
            current = self.parents[current]
            if isinstance(current, ast.ClassDef):
                return current
        return None

    def _has_exact_type_guard(
        self,
        node: ast.AST,
        receiver: str,
        exact_type: str,
    ) -> bool:
        if self._guard_name_is_shadowed(node, "type"):
            return False
        if exact_type == "_PATH_TYPE":
            if not self._has_reviewed_concrete_path_type_binding(node):
                return False
        elif self._guard_name_is_shadowed(node, exact_type):
            return False
        statements, contexts = self._dominating_context(node)
        for context, test, _body in contexts:
            if (
                context == "body"
                and self._is_exact_compare(
                    test,
                    receiver,
                    exact_type,
                    positive=True,
                )
            ) or (
                context == "orelse"
                and self._is_exact_compare(
                    test,
                    receiver,
                    exact_type,
                    positive=False,
                )
            ):
                if self._receiver_rebound_before_node(
                    node,
                    _body,
                    receiver,
                ):
                    return False
                return True

        current = node
        while current in self.parents:
            parent = self.parents[current]
            if (
                isinstance(parent, ast.BoolOp)
                and isinstance(parent.op, (ast.And, ast.Or))
            ):
                guarded = False
                for value in parent.values:
                    if self._contains(value, node):
                        if guarded:
                            return True
                        break
                    if guarded and self._statement_assigns(
                        value,
                        receiver,
                    ):
                        return False
                    positive = isinstance(parent.op, ast.And)
                    if self._is_exact_compare(
                        value, receiver, exact_type, positive=positive
                    ):
                        guarded = True
            current = parent

        for statement in reversed(statements):
            if self._statement_assigns(statement, receiver):
                return False
            if (
                isinstance(statement, ast.If)
                and self._reject_guard_matches(
                    statement.test,
                    receiver,
                    exact_type,
                )
                and self._body_terminates(statement.body)
            ):
                return True
        return False

    def _receiver_rebound_before_node(
        self,
        node: ast.AST,
        body: list[ast.stmt],
        receiver: str,
    ) -> bool:
        for statement in body:
            if not self._contains(statement, node):
                if self._statement_assigns(statement, receiver):
                    return True
                continue
            if isinstance(statement, ast.Match):
                for case in statement.cases:
                    if not any(
                        self._contains(item, node)
                        for item in case.body
                    ):
                        continue
                    if self._statement_assigns(case.pattern, receiver):
                        return True
                    return self._receiver_rebound_before_node(
                        node,
                        case.body,
                        receiver,
                    )
            entry_targets = self._assignment_targets(statement)
            if any(
                receiver == ast.unparse(target)
                or receiver.split(".", 1)[0] in _target_names(target)
                for target in entry_targets
            ):
                return True
            entry_expressions: tuple[ast.AST, ...] = ()
            if isinstance(statement, (ast.If, ast.While)):
                entry_expressions = (statement.test,)
            elif isinstance(statement, (ast.For, ast.AsyncFor)):
                entry_expressions = (statement.iter,)
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                entry_expressions = tuple(
                    item.context_expr for item in statement.items
                )
            elif isinstance(statement, ast.Match):
                entry_expressions = (statement.subject,)
            if any(
                self._statement_assigns(expression, receiver)
                for expression in entry_expressions
            ):
                return True
            for nested in self._nested_statement_bodies(statement):
                if any(self._contains(item, node) for item in nested):
                    return self._receiver_rebound_before_node(
                        node,
                        nested,
                        receiver,
                    )
            return False
        return False

    def _has_reviewed_concrete_path_type_binding(
        self,
        node: ast.AST,
    ) -> bool:
        if self._guard_name_is_shadowed(node, "Path"):
            return False
        function = self._enclosing_function(node)
        if function is not None:
            arguments = (
                tuple(function.args.posonlyargs)
                + tuple(function.args.args)
                + tuple(function.args.kwonlyargs)
            )
            if function.args.vararg is not None:
                arguments += (function.args.vararg,)
            if function.args.kwarg is not None:
                arguments += (function.args.kwarg,)
            if any(
                argument.arg == "_PATH_TYPE"
                for argument in arguments
            ):
                return False
            if any(
                self._statement_assigns(statement, "_PATH_TYPE")
                for statement in function.body
            ):
                return False

        path_imports = 0
        path_type_bindings = 0
        for statement in self.tree.body:
            if (
                isinstance(statement, ast.ImportFrom)
                and statement.level == 0
                and statement.module == "pathlib"
            ):
                path_imports += sum(
                    alias.name == "Path" and alias.asname is None
                    for alias in statement.names
                )
            if not self._statement_assigns(statement, "_PATH_TYPE"):
                continue
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and statement.targets[0].id == "_PATH_TYPE"
                and ast.unparse(statement.value) == "type(Path())"
            ):
                path_type_bindings += 1
                continue
            return False
        return path_imports == 1 and path_type_bindings == 1

    def _guard_name_is_shadowed(self, node: ast.AST, name: str) -> bool:
        function = self._enclosing_function(node)
        if function is not None:
            arguments = (
                tuple(function.args.posonlyargs)
                + tuple(function.args.args)
                + tuple(function.args.kwonlyargs)
            )
            if function.args.vararg is not None:
                arguments += (function.args.vararg,)
            if function.args.kwarg is not None:
                arguments += (function.args.kwarg,)
            if any(argument.arg == name for argument in arguments):
                return True
            for statement in function.body:
                if self._statement_assigns(statement, name):
                    return True

        for statement in self.tree.body:
            if isinstance(statement, ast.ClassDef) and statement.name == name:
                continue
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                continue
            if self._statement_assigns(statement, name):
                return True
        return False

    @staticmethod
    def _contains(container: ast.AST, needle: ast.AST) -> bool:
        return container is needle or any(
            candidate is needle for candidate in ast.walk(container)
        )

    @staticmethod
    def _body_terminates(body: list[ast.stmt]) -> bool:
        return bool(body) and isinstance(body[0], (ast.Raise, ast.Return))

    @staticmethod
    def _is_exact_compare(
        node: ast.AST,
        receiver: str,
        exact_type: str,
        *,
        positive: bool,
    ) -> bool:
        return (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.Is if positive else ast.IsNot)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Name)
            and node.comparators[0].id == exact_type
            and isinstance(node.left, ast.Call)
            and isinstance(node.left.func, ast.Name)
            and node.left.func.id == "type"
            and len(node.left.args) == 1
            and not node.left.keywords
            and ast.unparse(node.left.args[0]) == receiver
        )

    @staticmethod
    def _is_exact_identity_compare(
        node: ast.AST,
        left: str,
        right: str,
        *,
        positive: bool,
    ) -> bool:
        return (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.Is if positive else ast.IsNot)
            and len(node.comparators) == 1
            and ast.unparse(node.left) == left
            and ast.unparse(node.comparators[0]) == right
        )

    def _has_exact_identity_reject_guard(
        self,
        node: ast.AST,
        left: str,
        right: str,
    ) -> bool:
        current = node
        while current in self.parents:
            parent = self.parents[current]
            if isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.Or):
                guarded = False
                for value in parent.values:
                    if self._contains(value, node):
                        return guarded
                    if guarded and (
                        self._statement_assigns(value, left)
                        or self._statement_assigns(value, right)
                    ):
                        return False
                    if self._is_exact_identity_compare(
                        value,
                        left,
                        right,
                        positive=False,
                    ):
                        guarded = True
            current = parent

        statements, _ = self._dominating_context(node)
        for statement in reversed(statements):
            if (
                self._statement_assigns(statement, left)
                or self._statement_assigns(statement, right)
            ):
                return False
            if (
                isinstance(statement, ast.If)
                and self._is_exact_identity_compare(
                    statement.test,
                    left,
                    right,
                    positive=False,
                )
                and self._body_terminates(statement.body)
            ):
                return True
        return False

    def _reject_guard_matches(
        self,
        node: ast.AST,
        receiver: str,
        exact_type: str,
    ) -> bool:
        if self._is_exact_compare(
            node,
            receiver,
            exact_type,
            positive=False,
        ):
            return True
        return (
            isinstance(node, ast.BoolOp)
            and isinstance(node.op, ast.Or)
            and any(
                self._is_exact_compare(
                    value,
                    receiver,
                    exact_type,
                    positive=False,
                )
                for value in node.values
            )
        )

    @staticmethod
    def _assignment_targets(statement: ast.AST) -> tuple[ast.AST, ...]:
        if isinstance(statement, ast.Assign):
            return tuple(statement.targets)
        if isinstance(statement, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            return (statement.target,)
        if isinstance(statement, (ast.For, ast.AsyncFor)):
            return (statement.target,)
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            return tuple(
                item.optional_vars
                for item in statement.items
                if item.optional_vars is not None
            )
        if isinstance(statement, ast.Delete):
            return tuple(statement.targets)
        if isinstance(statement, ast.MatchAs) and statement.name is not None:
            return (ast.Name(id=statement.name),)
        if isinstance(statement, ast.MatchStar) and statement.name is not None:
            return (ast.Name(id=statement.name),)
        if (
            isinstance(statement, ast.MatchMapping)
            and statement.rest is not None
        ):
            return (ast.Name(id=statement.rest),)
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return (ast.Name(id=statement.name),)
        if isinstance(statement, ast.ExceptHandler) and statement.name is not None:
            return (ast.Name(id=statement.name),)
        return ()

    @classmethod
    def _statement_assigns(cls, statement: ast.stmt, receiver: str) -> bool:
        root = receiver.split(".", 1)[0]
        def assigns(candidate: ast.AST, *, nested: bool) -> bool:
            for target in cls._assignment_targets(candidate):
                rendered = ast.unparse(target)
                if (
                    root in _target_names(target)
                    or rendered == receiver
                    or rendered == root
                ):
                    return True
            if nested and isinstance(
                candidate,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                return False
            return any(
                assigns(child, nested=True)
                for child in ast.iter_child_nodes(candidate)
            )

        return assigns(statement, nested=False)

    def _dominating_context(
        self,
        node: ast.AST,
    ) -> tuple[list[ast.stmt], list[tuple[str, ast.AST, list[ast.stmt]]]]:
        function = self._enclosing_function(node)
        if function is None:
            root_body = self.tree.body
        else:
            root_body = function.body
        prior: list[ast.stmt] = []
        contexts: list[tuple[str, ast.AST, list[ast.stmt]]] = []

        def descend(body: list[ast.stmt]) -> bool:
            for index, statement in enumerate(body):
                if not self._contains(statement, node):
                    continue
                prior.extend(body[:index])
                if isinstance(statement, ast.If):
                    if self._contains(statement.test, node):
                        return True
                    if any(self._contains(item, node) for item in statement.body):
                        contexts.append(("body", statement.test, statement.body))
                        return descend(statement.body)
                    if any(
                        self._contains(item, node)
                        for item in statement.orelse
                    ):
                        contexts.append(
                            ("orelse", statement.test, statement.orelse)
                        )
                        return descend(statement.orelse)
                for nested in self._nested_statement_bodies(statement):
                    if any(self._contains(item, node) for item in nested):
                        return descend(nested)
                return True
            return False

        descend(root_body)
        return prior, contexts

    @staticmethod
    def _nested_statement_bodies(statement: ast.stmt) -> tuple[list[ast.stmt], ...]:
        names = ("body", "orelse", "finalbody")
        bodies = tuple(
            value
            for name in names
            if isinstance((value := getattr(statement, name, None)), list)
        )
        if isinstance(statement, ast.Try):
            bodies += tuple(handler.body for handler in statement.handlers)
        return bodies

    def _has_assignment_from(
        self,
        node: ast.AST,
        target_name: str,
        value_shape: str,
    ) -> bool:
        value = self._last_reaching_value(node, target_name)
        return value is not None and ast.unparse(value) == value_shape

    def _last_reaching_value(
        self,
        node: ast.AST,
        target_name: str,
    ) -> ast.AST | None:
        statements, _ = self._dominating_context(node)
        for statement in reversed(statements):
            direct = self._direct_assignment_value(statement, target_name)
            if direct is not None:
                return direct
            if isinstance(statement, ast.Try):
                try_value = self._try_success_value(statement, target_name)
                if try_value is not None:
                    return try_value
            if self._statement_assigns(statement, target_name):
                return None
        return None

    @staticmethod
    def _direct_assignment_value(
        statement: ast.stmt,
        target_name: str,
    ) -> ast.AST | None:
        if isinstance(statement, ast.Assign) and any(
            ast.unparse(target) == target_name for target in statement.targets
        ):
            return statement.value
        if (
            isinstance(statement, ast.AnnAssign)
            and ast.unparse(statement.target) == target_name
        ):
            return statement.value
        return None

    def _try_success_value(
        self,
        statement: ast.Try,
        target_name: str,
    ) -> ast.AST | None:
        if statement.finalbody or statement.orelse:
            return None
        if not statement.handlers or not all(
            self._body_terminates(handler.body)
            for handler in statement.handlers
        ):
            return None
        for candidate in reversed(statement.body):
            value = self._direct_assignment_value(candidate, target_name)
            if value is not None:
                return value
            if self._statement_assigns(candidate, target_name):
                return None
        return None

    def _has_prior_call(
        self,
        node: ast.AST,
        rendered: str,
        *,
        validated_receiver: str,
    ) -> bool:
        statements, _ = self._dominating_context(node)
        for statement in reversed(statements):
            if self._statement_assigns(statement, validated_receiver):
                return False
            if (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and ast.unparse(statement.value) == rendered
            ):
                return True
        return False

    def _add(self, node: ast.AST, reason: str) -> None:
        line = getattr(node, "lineno", 0)
        self.violations.append(f"{self.filename}:{line}:{reason}")

    def _resolved_path(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self._resolved_path(node.value)
            return None if base is None else f"{base}.{node.attr}"
        return None

    @staticmethod
    def _dangerous_path(path: str) -> bool:
        if path in {
            "types.CodeType",
            "types.FunctionType",
            "sys.modules",
            "sys.meta_path",
            "sys.path_hooks",
            "sys.path_importer_cache",
            "sys.breakpointhook",
        }:
            return True
        if path in {
            "os.system",
            "os.popen",
            "os.fork",
            "os.forkpty",
            "os.startfile",
        }:
            return True
        if path.startswith(("os.spawn", "os.posix_spawn", "os.exec")):
            return True
        return False

    def _check_import(self, node: ast.Import | ast.ImportFrom) -> None:
        if any(
            (
                isinstance(node, ast.Import)
                and alias.name.split(".", 1)[0] == "unicodedata"
            )
            or (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".", 1)[0] == "unicodedata"
            )
            for alias in node.names
        ):
            key = (self.filename, ast.unparse(node))
            self.unicode_imports[key] += 1
            if key not in EXPECTED_UNICODE_IMPORTS:
                self._add(node, "unicode_import_forbidden")
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if (
                    root not in ALLOWED_STDLIB_IMPORTS
                    or root in FORBIDDEN_IMPORT_ROOTS
                    or "." in alias.name
                ):
                    self._add(node, f"import_forbidden:{alias.name}")
                local_name = alias.asname or root
                if (
                    local_name in PROTECTED_GUARD_NAMES
                    and local_name != alias.name
                ):
                    self._add(
                        node,
                        f"guard_binding_shadow_forbidden:{local_name}",
                    )
                self.aliases[local_name] = alias.name
                self.imported_names.add(local_name)
            return

        if node.level:
            if (
                node.level != 1
                or not node.module
                or any(alias.name == "*" for alias in node.names)
            ):
                self._add(node, "relative_import_shape_forbidden")
                return
            allowed = ALLOWED_RELATIVE_MEMBERS.get(node.module)
            if allowed is None:
                self._add(node, f"relative_module_forbidden:{node.module}")
                return
            for alias in node.names:
                local_name = alias.asname or alias.name
                if (
                    local_name in PROTECTED_GUARD_NAMES
                    and local_name != alias.name
                ):
                    self._add(
                        node,
                        f"guard_binding_shadow_forbidden:{local_name}",
                    )
                if alias.name not in allowed:
                    self._add(
                        node,
                        f"relative_member_forbidden:"
                        f"{node.module}.{alias.name}",
                    )
                self.aliases[local_name] = (
                    f"tennis_v1.{node.module}.{alias.name}"
                )
                self.imported_names.add(local_name)
            return

        module = node.module or ""
        root = module.split(".", 1)[0]
        if module == "urllib.parse":
            if (
                self.filename not in {"capture.py", "entitlements.py"}
                or len(node.names) != 1
                or node.names[0].name != "urlsplit"
                or node.names[0].asname is not None
            ):
                self._add(node, "urllib_import_forbidden")
            else:
                self.aliases["urlsplit"] = "urllib.parse.urlsplit"
                self.imported_names.add("urlsplit")
            return
        if (
            root not in ALLOWED_STDLIB_IMPORTS
            or root in FORBIDDEN_IMPORT_ROOTS
            or module not in ALLOWED_FROM_MEMBERS
        ):
            self._add(node, f"from_import_forbidden:{module}")
            return
        allowed = ALLOWED_FROM_MEMBERS[module]
        for alias in node.names:
            local_name = alias.asname or alias.name
            if (
                local_name in PROTECTED_GUARD_NAMES
                and local_name != alias.name
            ):
                self._add(
                    node,
                    f"guard_binding_shadow_forbidden:{local_name}",
                )
            if alias.name == "*" or alias.name not in allowed:
                self._add(node, f"from_member_forbidden:{module}.{alias.name}")
            self.aliases[local_name] = (
                f"{module}.{alias.name}"
            )
            self.imported_names.add(local_name)

    def _check_definition(self, node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if self.filename == "preflight.py":
                enclosing_class = self._enclosing_class(node)
                signature = (
                    "" if enclosing_class is None else enclosing_class.name,
                    node.name,
                    tuple(item.arg for item in node.args.posonlyargs),
                    tuple(item.arg for item in node.args.args),
                    (
                        None
                        if node.args.vararg is None
                        else node.args.vararg.arg
                    ),
                    tuple(item.arg for item in node.args.kwonlyargs),
                    (
                        None
                        if node.args.kwarg is None
                        else node.args.kwarg.arg
                    ),
                    len(node.args.defaults),
                    tuple(
                        item is not None for item in node.args.kw_defaults
                    ),
                )
                if signature not in EXPECTED_PREFLIGHT_SIGNATURES:
                    self._add(
                        node,
                        "preflight_signature_forbidden:"
                        f"{signature!r}",
                    )
            if node.name in self.imported_names:
                self._add(node, f"import_shadow_forbidden:{node.name}")
            if node.name in FORBIDDEN_TRANSPORT_NAMES | FORBIDDEN_CALL_NAMES:
                self._add(node, f"definition_forbidden:{node.name}")
            arguments = (
                list(node.args.posonlyargs)
                + list(node.args.args)
                + list(node.args.kwonlyargs)
            )
            if node.args.vararg is not None:
                arguments.append(node.args.vararg)
            if node.args.kwarg is not None:
                arguments.append(node.args.kwarg)
            for argument in arguments:
                if argument.arg in self.imported_names:
                    self._add(
                        argument,
                        f"import_shadow_forbidden:{argument.arg}",
                    )
                if argument.arg in PROTECTED_GUARD_NAMES:
                    self._add(
                        argument,
                        f"guard_binding_shadow_forbidden:{argument.arg}",
                    )
                if argument.arg in FORBIDDEN_TRANSPORT_NAMES:
                    self._add(
                        argument,
                        f"transport_parameter_forbidden:{argument.arg}",
                    )
                if argument.annotation is not None:
                    self._check_transport_annotation(argument.annotation)
            if node.returns is not None:
                self._check_transport_annotation(node.returns)
            for expression in (
                tuple(node.decorator_list)
                + tuple(node.args.defaults)
                + tuple(
                    item
                    for item in node.args.kw_defaults
                    if item is not None
                )
            ):
                self._check_escape_expression(expression, "callable_metadata")
        elif isinstance(node, ast.ClassDef):
            if node.name in self.imported_names:
                self._add(node, f"import_shadow_forbidden:{node.name}")
            for expression in node.decorator_list:
                self._check_escape_expression(expression, "class_decorator")
            for keyword in node.keywords:
                if keyword.arg == "metaclass":
                    self._check_escape_expression(
                        keyword.value,
                        "metaclass",
                    )
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets: tuple[str, ...]
            if isinstance(node, ast.Assign):
                targets = tuple(
                    name
                    for target in node.targets
                    for name in _target_names(target)
                )
                value = node.value
            else:
                targets = _target_names(node.target)
                value = node.value
            class_field = isinstance(
                self.parents.get(node),
                ast.ClassDef,
            )
            for name in targets:
                if name in self.imported_names and not class_field:
                    self._add(node, f"import_shadow_forbidden:{name}")
                if name in PROTECTED_GUARD_NAMES:
                    self._add(
                        node,
                        f"guard_binding_shadow_forbidden:{name}",
                    )
                if name in FORBIDDEN_TRANSPORT_NAMES:
                    self._add(node, f"transport_field_forbidden:{name}")
            if isinstance(node, ast.AnnAssign):
                self._check_transport_annotation(node.annotation)
            if value is not None:
                resolved = self._resolved_path(value)
                if resolved in {
                    "getattr",
                    "hasattr",
                    "locals",
                    "setattr",
                    "urllib.parse.urlsplit",
                    "vars",
                    "globals",
                }:
                    self._add(node, "reflected_callable_alias_forbidden")
                if resolved is not None:
                    for name in targets:
                        self.aliases[name] = resolved
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and value.func.attr == "get"
                ):
                    self.get_result_names.update(targets)
                if any(
                    isinstance(item, ast.Name)
                    and self.aliases.get(item.id, "").split(".", 1)[0]
                    in ALLOWED_STDLIB_IMPORTS
                    for item in ast.walk(value)
                ) and isinstance(
                    value,
                    (ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Lambda),
                ) and (
                    (
                        self.filename,
                        self._function_name(node),
                        targets,
                        ast.unparse(value),
                    )
                    not in EXPECTED_MODULE_CONTAINER_ASSIGNMENTS
                ):
                    self._add(node, "module_container_escape_forbidden")
        elif isinstance(node, ast.Return) and node.value is not None:
            resolved = self._resolved_path(node.value)
            if (
                resolved is not None
                and resolved.split(".", 1)[0] in ALLOWED_STDLIB_IMPORTS
            ):
                self._add(node, "module_or_callable_return_forbidden")

    def _check_transport_annotation(self, node: ast.AST) -> None:
        tokens = {
            item.id.lower()
            for item in ast.walk(node)
            if isinstance(item, ast.Name)
        } | {
            item.attr.lower()
            for item in ast.walk(node)
            if isinstance(item, ast.Attribute)
        }
        if tokens & FORBIDDEN_TRANSPORT_NAMES:
            self._add(node, "transport_annotation_forbidden")

    def _check_escape_expression(self, node: ast.AST, context: str) -> None:
        for item in ast.walk(node):
            path = self._resolved_path(item)
            if path is not None and (
                self._dangerous_path(path)
                or path in FORBIDDEN_BUILTINS
            ):
                self._add(node, f"{context}_escape_forbidden")
                return

    def _check_string(self, node: ast.AST) -> None:
        value = _static_string(node)
        if value is None:
            return
        upper = value.upper()
        lowered = value.lower()
        if upper in HTTP_METHODS:
            self._add(node, f"http_method_forbidden:{upper}")
        if "/orders" in lowered or "/portfolio/orders" in lowered:
            self._add(node, "mutation_endpoint_forbidden")

    def _check_attribute(self, node: ast.Attribute) -> None:
        path = self._resolved_path(node)
        if (
            path is not None
            and path.startswith("self._queue.")
            and node.attr
            not in {"empty", "get", "get_nowait", "put", "put_nowait"}
        ):
            self._add(
                node,
                f"private_queue_member_forbidden:{node.attr}",
            )
        if path is not None and self._dangerous_path(path):
            self._add(node, f"process_or_reflection_escape:{path}")
        if node.attr in {
            "__builtins__",
            "__dict__",
            "__getattr__",
            "__getattribute__",
            "__globals__",
            "__subclasses__",
            "f_globals",
            "load_module",
            "loader",
        }:
            approved_object_getattribute = (
                node.attr == "__getattribute__"
                and isinstance(node.value, ast.Name)
                and node.value.id == "object"
                and isinstance(self.parents.get(node), ast.Call)
                and self.parents[node].func is node  # type: ignore[union-attr]
                and (
                    self.filename,
                    self._function_name(self.parents[node]),
                    "object.__getattribute__",
                    ast.unparse(self.parents[node]),
                )
                in EXPECTED_REFLECTION_CALLS
            )
            if not approved_object_getattribute:
                self._add(node, f"reflective_attribute_forbidden:{node.attr}")
        if node.attr in {
            "system",
            "popen",
            "fork",
            "forkpty",
            "startfile",
            "_getframe",
        } or node.attr.startswith(("spawn", "posix_spawn", "exec")):
            self._add(node, f"execution_attribute_forbidden:{node.attr}")
        approved_retention_request_field = (
            self.filename == "retention.py"
            and self._function_name(node)
            == "_consume_expert_state_root_account_lock_request"
            and ast.unparse(node) == "request_authority.request"
            and self._has_exact_type_guard(
                node,
                "request_authority",
                "_ExpertRootRequestAuthority",
            )
        )
        if (
            node.attr in FORBIDDEN_CALL_NAMES | FORBIDDEN_TRANSPORT_NAMES
            and not approved_retention_request_field
        ):
            self._add(node, f"transport_or_mutation_attribute:{node.attr}")
        if node.attr == "get":
            parent = self.parents.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node):
                self._add(node, "mapping_get_alias_forbidden")
        if node.attr in {"put_nowait", "get_nowait"}:
            parent = self.parents.get(node)
            if not (
                isinstance(parent, ast.Call)
                and parent.func is node
                and ast.unparse(node.value) == "self._queue"
            ):
                self._add(node, f"queue_method_alias_forbidden:{node.attr}")
        if path in {"os.environ", "os.getenv", "os.environb"}:
            self._add(node, "environment_lookup_forbidden")
        if path is not None and path.startswith("math.") and path != "math.isfinite":
            self._add(node, f"math_member_forbidden:{path}")

    def _check_reflection_call(self, node: ast.Call) -> bool:
        kind: str | None = None
        if isinstance(node.func, ast.Name) and node.func.id in {
            "getattr",
            "hasattr",
            "locals",
            "setattr",
        }:
            kind = node.func.id
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "object"
            and node.func.attr == "__getattribute__"
        ):
            kind = "object.__getattribute__"
        if kind is None:
            return False

        key = (
            self.filename,
            self._function_name(node),
            kind,
            ast.unparse(node),
        )
        self.reflection_calls[key] += 1
        if key not in EXPECTED_REFLECTION_CALLS:
            self._add(node, f"reflection_shape_forbidden:{ast.unparse(node)}")
            return True
        if self.filename == "capture.py" and kind == "getattr":
            parent = self.parents.get(node)
            if not (
                isinstance(parent, ast.Call)
                and isinstance(parent.func, ast.Name)
                and parent.func.id == "callable"
                and parent.args == [node]
            ):
                self._add(node, "capture_reflection_use_forbidden")
        if not self._reflection_has_dominance(node, kind):
            self._add(node, "reflection_type_dominance_forbidden")
        if kind == "locals":
            parent = self.parents.get(node)
            if not isinstance(parent, ast.Compare):
                self._add(node, "locals_use_forbidden")
        return True

    def _reflection_has_dominance(self, node: ast.Call, kind: str) -> bool:
        function = self._function_name(node)
        rendered = ast.unparse(node)
        if self.filename == "capture.py":
            if function == "issue_capture_authority":
                return self._has_exact_type_guard(
                    node,
                    "session_manifest",
                    "SessionManifest",
                )
            if function == "validate_capture_against_authority":
                return self._has_exact_type_guard(
                    node,
                    "authority",
                    "CaptureAuthority",
                )
        if self.filename == "events.py" and kind == "getattr":
            owner = self._enclosing_class(node)
            return owner is not None and self._has_exact_type_guard(
                node,
                "self",
                owner.name,
            )
        if self.filename == "adapter_contract.py":
            if function in {"_validate_usage", "_usage_projection"}:
                return self._has_exact_type_guard(
                    node,
                    "usage",
                    "AdapterUsagePlan",
                )
            return True
        if self.filename == "entitlements.py":
            if function == "_canonical_projection":
                if "manifest.quotas" in rendered:
                    return self._has_exact_type_guard(
                        node,
                        "manifest.quotas",
                        "ProviderQuotas",
                    )
                if "manifest.capabilities" in rendered:
                    return self._has_exact_type_guard(
                        node,
                        "manifest.capabilities",
                        "ProviderCapabilities",
                    )
            if function == "_bind_qualification":
                return self._has_exact_type_guard(
                    node,
                    "capabilities",
                    "ProviderCapabilities",
                )
            if function == "_evaluate_provider_as_of":
                if "demand" in rendered and "manifest." not in rendered:
                    return self._has_exact_type_guard(
                        node,
                        "demand",
                        "ProviderQuotas",
                    )
                return self._has_prior_call(
                    node,
                    "_canonical_projection(manifest)",
                    validated_receiver="manifest",
                )
        if self.filename == "retention.py":
            if function == "__post_init__":
                return self._has_exact_type_guard(
                    node,
                    "self",
                    "RetentionMarker",
                )
            if function == "_marker_projection":
                return self._has_exact_type_guard(
                    node,
                    "marker",
                    "RetentionMarker",
                )
            if kind == "object.__getattribute__":
                expert_reflection_guards = {
                    "_consume_expert_state_root_account_lock_request": (
                        "request",
                        "ExpertStateRootAccountLockRequestV1",
                    ),
                    "_revoke_expert_state_root_account_lock_grant": (
                        "grant",
                        "_ExpertStateRootAccountLockGrantV1",
                    ),
                    "sample_expert_retention_wall_ns": (
                        "capability",
                        "ExpertRetentionClockSampleCapabilityV1",
                    ),
                    "_validate_expert_root_binding": (
                        "authority",
                        "_ExpertRootGrantAuthority",
                    ),
                    "_validate_expert_root_authority_locked": (
                        "authority",
                        "_ExpertRootGrantAuthority",
                    ),
                }
                expert_guard = expert_reflection_guards.get(function)
                if expert_guard is not None:
                    exact_type_dominates = self._has_exact_type_guard(
                        node,
                        expert_guard[0],
                        expert_guard[1],
                    )
                    if (
                        function
                        == "_validate_expert_root_authority_locked"
                    ):
                        return (
                            exact_type_dominates
                            and self._has_exact_identity_reject_guard(
                                node,
                                "authority.clock_capability",
                                "capability",
                            )
                        )
                    return exact_type_dominates
                if "write_capability" in rendered:
                    return self._has_exact_type_guard(
                        node,
                        "write_capability",
                        "ProviderWalWriteCapability",
                    )
                return self._has_exact_type_guard(
                    node,
                    "read_capability",
                    "ProviderWalReadCapability",
                )
            return True
        if self.filename == "session.py" and function == "_projection":
            return self._has_exact_type_guard(
                node,
                "manifest",
                "SessionManifest",
            )
        if self.filename == "replay_core.py" and function == "_derived_signature":
            return self._has_exact_type_guard(
                node,
                "event",
                "PersistedEvent",
            )
        if self.filename == "reducer.py" and function == "initial_trace":
            return self._has_assignment_from(
                node,
                "session_manifest",
                "SessionManifest(**raw_manifest)",
            )
        return True

    def _check_fields_call(self, node: ast.Call) -> bool:
        if not (isinstance(node.func, ast.Name) and node.func.id == "fields"):
            return False
        key = (
            self.filename,
            self._function_name(node),
            ast.unparse(node),
        )
        self.fields_calls[key] += 1
        if key not in EXPECTED_FIELDS_CALLS:
            self._add(node, f"dataclass_fields_call_forbidden:{ast.unparse(node)}")
        return True

    def _check_get_call(self, node: ast.Call) -> bool:
        if not (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
        ):
            return False
        receiver = ast.unparse(node.func.value)
        if receiver == "self._queue":
            return False
        resolved_receiver = self._resolved_path(node.func.value)
        parent = self.parents.get(node)
        if (
            receiver not in SAFE_GET_RECEIVERS.get(self.filename, set())
            or resolved_receiver != receiver
            or len(node.args) not in {1, 2}
            or node.keywords
            or any(
                isinstance(item, ast.Starred)
                for item in node.args
            )
        ):
            self._add(node, f"mapping_get_forbidden:{receiver}")
            return True
        if isinstance(parent, ast.Call) and parent.func is node:
            self._add(node, "mapping_get_result_invoked")
        current: ast.AST = node
        while current in self.parents:
            current = self.parents[current]
            if isinstance(current, ast.stmt):
                break
            if isinstance(
                current,
                (
                    ast.Call,
                    ast.Dict,
                    ast.DictComp,
                    ast.GeneratorExp,
                    ast.Lambda,
                    ast.List,
                    ast.ListComp,
                    ast.Set,
                    ast.SetComp,
                    ast.Subscript,
                    ast.Tuple,
                ),
            ):
                self._add(node, "mapping_get_result_escape_forbidden")
                break
        if not self._safe_get_provenance(node, receiver):
            self._add(node, f"mapping_get_provenance_forbidden:{receiver}")
        return True

    def _safe_get_provenance(self, node: ast.Call, receiver: str) -> bool:
        function_name = self._function_name(node)
        if self.filename == "adapter_contract.py" and receiver == "registry":
            return self._has_assignment_from(
                node,
                "registry",
                "_validated_registry_snapshot(_ADAPTER_REGISTRY)",
            )
        if self.filename == "entitlements.py" and receiver == "environ":
            return self._has_exact_type_guard(node, "environ", "dict")
        if (
            self.filename == "entitlements.py"
            and receiver == "evidence_by_stratum"
        ):
            return isinstance(
                self._last_reaching_value(node, "evidence_by_stratum"),
                ast.DictComp,
            )
        if self.filename == "events.py":
            return receiver == "CONTROL_RECORD_CONTRACTS"
        if self.filename == "reducer.py":
            return isinstance(
                self._last_reaching_value(node, receiver),
                (ast.Dict, ast.DictComp),
            )
        if self.filename == "retention.py" and receiver.startswith("self._"):
            if function_name == "<module>":
                return False
            statements, _ = self._dominating_context(node)
            if any(
                self._statement_assigns(statement, receiver)
                for statement in statements
            ):
                return False
            return not any(
                isinstance(candidate, ast.Call)
                and self._resolved_path(candidate.func)
                in {"delattr", "object.__setattr__", "setattr"}
                and len(candidate.args) >= 2
                and isinstance(candidate.args[1], ast.Constant)
                and candidate.args[1].value == receiver.rsplit(".", 1)[1]
                for statement in statements
                for candidate in ast.walk(statement)
            )
        if self.filename == "retention.py" and receiver == "value":
            value = self._last_reaching_value(node, "value")
            assigned_json = (
                isinstance(value, ast.Call)
                and ast.unparse(value.func) == "json.loads"
            )
            return assigned_json and self._has_exact_type_guard(
                node,
                "value",
                "dict",
            )
        return False

    def _check_queue_call(self, node: ast.Call) -> bool:
        rendered = ast.unparse(node)
        function = self._function_name(node)
        resolved_function = self._resolved_path(node.func)
        is_constructor = (
            isinstance(node.func, ast.Attribute)
            and resolved_function in {"queue.Queue", "queue.SimpleQueue"}
        )
        is_private_method = (
            isinstance(node.func, ast.Attribute)
            and ast.unparse(node.func.value) == "self._queue"
            and node.func.attr
            in {"empty", "get", "get_nowait", "put", "put_nowait"}
        )
        if not is_constructor and not is_private_method:
            return False
        key = (self.filename, function, rendered)
        self.queue_calls[key] += 1
        if (
            key not in EXPECTED_QUEUE_CALLS
            or (
                is_constructor
                and resolved_function != "queue.Queue"
            )
        ):
            self._add(node, f"queue_shape_forbidden:{rendered}")
        return True

    def _check_queue_receiver_integrity(self) -> None:
        queue_calls = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and (
                self._resolved_path(node.func)
                in {"queue.Queue", "queue.SimpleQueue"}
                or (
                    isinstance(node.func, ast.Attribute)
                    and ast.unparse(node.func.value) == "self._queue"
                    and node.func.attr
                    in {"empty", "get", "get_nowait", "put", "put_nowait"}
                )
            )
        ]
        if not queue_calls:
            return
        if self.filename not in {"ingress.py", "mailbox.py"}:
            self._add(queue_calls[0], "queue_file_forbidden")
            return

        assignments: list[ast.Assign | ast.AnnAssign] = []
        mutations: list[ast.AST] = []
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    tuple(node.targets)
                    if isinstance(node, ast.Assign)
                    else (node.target,)
                )
                if any(
                    ast.unparse(target) == "self._queue"
                    for target in targets
                ):
                    assignments.append(node)
            elif isinstance(node, (ast.AugAssign, ast.Delete)):
                targets = (
                    (node.target,)
                    if isinstance(node, ast.AugAssign)
                    else tuple(node.targets)
                )
                if any(
                    ast.unparse(target) == "self._queue"
                    for target in targets
                ):
                    mutations.append(node)

        expected_maxsize = (
            "1" if self.filename == "mailbox.py" else "self._capacity"
        )
        valid_assignment = False
        if len(assignments) == 1:
            assignment = assignments[0]
            value = assignment.value
            parent = self.parents.get(assignment)
            valid_assignment = (
                isinstance(value, ast.Call)
                and self._resolved_path(value.func) == "queue.Queue"
                and not value.args
                and len(value.keywords) == 1
                and value.keywords[0].arg == "maxsize"
                and ast.unparse(value.keywords[0].value) == expected_maxsize
                and isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
                and parent.name == "__init__"
                and assignment in parent.body
            )
        reflective_mutations = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and self._resolved_path(node.func)
            in {"delattr", "object.__setattr__", "setattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "_queue"
        ]
        if (
            len(assignments) != 1
            or mutations
            or reflective_mutations
            or not valid_assignment
        ):
            self._add(
                queue_calls[0],
                "private_queue_provenance_forbidden",
            )

    @staticmethod
    def _function_parameter_names(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> set[str]:
        arguments = (
            tuple(function.args.posonlyargs)
            + tuple(function.args.args)
            + tuple(function.args.kwonlyargs)
        )
        names = {argument.arg for argument in arguments}
        if function.args.vararg is not None:
            names.add(function.args.vararg.arg)
        if function.args.kwarg is not None:
            names.add(function.args.kwarg.arg)
        return names - {"self", "cls"}

    def _visible_caller_taints(
        self,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> set[str]:
        result: set[str] = set()
        current: ast.AST | None = function
        while isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.update(self._caller_taints.get(current, set()))
            current = self._enclosing_function(current)
        return result

    def _visible_caller_containers(
        self,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> set[str]:
        result: set[str] = set()
        current: ast.AST | None = function
        while isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.update(self._caller_containers.get(current, set()))
            current = self._enclosing_function(current)
        return result

    def _expression_has_caller_taint(
        self,
        expression: ast.AST,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> bool:
        taints = self._visible_caller_taints(function)
        if not taints:
            return False
        return any(
            (
                isinstance(candidate, ast.Name)
                and candidate.id in taints
            )
            or (
                isinstance(candidate, (ast.Attribute, ast.Subscript))
                and ast.unparse(candidate) in taints
            )
            for candidate in ast.walk(expression)
        )

    def _value_carries_caller_taint(
        self,
        expression: ast.AST,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        extra_taints: frozenset[str] = frozenset(),
    ) -> bool:
        taints = self._visible_caller_taints(function) | set(extra_taints)
        if isinstance(expression, ast.Name):
            return expression.id in taints
        if isinstance(expression, ast.Attribute):
            return self._value_carries_caller_taint(
                expression.value,
                function,
                extra_taints=frozenset(taints),
            )
        if isinstance(expression, ast.Subscript):
            return (
                ast.unparse(expression.value)
                in self._visible_caller_containers(function)
                or self._container_contains_caller_taint(
                    expression.value,
                    function,
                    extra_taints=frozenset(taints),
                )
            )
        if isinstance(expression, (ast.NamedExpr, ast.Starred)):
            return self._value_carries_caller_taint(
                expression.value,
                function,
                extra_taints=frozenset(taints),
            )
        if isinstance(expression, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
            return False
        if isinstance(expression, ast.IfExp):
            return any(
                self._value_carries_caller_taint(
                    item,
                    function,
                    extra_taints=frozenset(taints),
                )
                for item in (expression.body, expression.orelse)
            )
        if isinstance(expression, ast.BoolOp):
            return any(
                self._value_carries_caller_taint(
                    item,
                    function,
                    extra_taints=frozenset(taints),
                )
                for item in expression.values
            )
        if isinstance(expression, ast.Lambda):
            return self._expression_has_caller_taint(
                expression.body,
                function,
            )
        if isinstance(expression, ast.Call):
            if (
                isinstance(expression.func, ast.Attribute)
                and expression.func.attr
                in {"pop", "popleft", "popitem"}
                and ast.unparse(expression.func.value)
                in self._visible_caller_containers(function)
            ):
                return True
            return self._value_carries_caller_taint(
                expression.func,
                function,
                extra_taints=frozenset(taints),
            )
        if isinstance(
            expression,
            (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp),
        ):
            return False
        return False

    def _container_contains_caller_taint(
        self,
        expression: ast.AST,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        extra_taints: frozenset[str] = frozenset(),
    ) -> bool:
        if (
            isinstance(expression, (ast.Name, ast.Attribute))
            and ast.unparse(expression)
            in self._visible_caller_containers(function)
        ):
            return True
        if isinstance(expression, (ast.Tuple, ast.List, ast.Set)):
            return any(
                self._value_carries_caller_taint(
                    item,
                    function,
                    extra_taints=extra_taints,
                )
                or self._container_contains_caller_taint(
                    item,
                    function,
                    extra_taints=extra_taints,
                )
                for item in expression.elts
            )
        if isinstance(expression, ast.Dict):
            return any(
                item is not None
                and (
                    self._value_carries_caller_taint(
                        item,
                        function,
                        extra_taints=extra_taints,
                    )
                    or self._container_contains_caller_taint(
                        item,
                        function,
                        extra_taints=extra_taints,
                    )
                )
                for item in tuple(expression.keys) + tuple(expression.values)
            )
        if isinstance(
            expression,
            (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp),
        ):
            return self._expression_has_caller_taint(expression, function)
        return False

    def _build_caller_taint(self) -> None:
        functions = tuple(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        self._caller_taints = {
            function: self._function_parameter_names(function)
            for function in functions
        }
        self._caller_containers = {
            function: set()
            for function in functions
        }
        assignments = tuple(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        )
        returns = tuple(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom))
            and node.value is not None
        )
        flow_bindings = tuple(
            node
            for node in ast.walk(self.tree)
            if isinstance(
                node,
                (
                    ast.For,
                    ast.AsyncFor,
                    ast.With,
                    ast.AsyncWith,
                    ast.Match,
                    ast.comprehension,
                ),
            )
        )
        container_mutations = tuple(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr
            in {"add", "append", "extend", "insert", "update"}
        )

        changed = True
        while changed:
            changed = False
            for assignment in assignments:
                function = self._enclosing_function(assignment)
                if function is None or assignment.value is None:
                    continue
                targets = self._taint_assignment_targets(assignment)
                if self._container_contains_caller_taint(
                    assignment.value,
                    function,
                ):
                    destructures = any(
                        isinstance(target, (ast.Tuple, ast.List))
                        for target in (
                            tuple(assignment.targets)
                            if isinstance(assignment, ast.Assign)
                            else (assignment.target,)
                        )
                    )
                    target_map = (
                        self._caller_taints
                        if destructures
                        else self._caller_containers
                    )
                    before = len(target_map[function])
                    target_map[function].update(targets)
                    changed = (
                        changed
                        or len(target_map[function]) != before
                    )
                if self._value_carries_caller_taint(
                    assignment.value,
                    function,
                ):
                    before = len(self._caller_taints[function])
                    self._caller_taints[function].update(targets)
                    changed = (
                        changed
                        or len(self._caller_taints[function]) != before
                    )

            for binding in flow_bindings:
                function = self._enclosing_function(binding)
                if function is None:
                    continue
                source: ast.AST | None = None
                targets: tuple[ast.AST, ...] = ()
                if isinstance(binding, (ast.For, ast.AsyncFor)):
                    source = binding.iter
                    targets = (binding.target,)
                elif isinstance(binding, (ast.With, ast.AsyncWith)):
                    for item in binding.items:
                        if (
                            item.optional_vars is None
                            or not (
                                self._value_carries_caller_taint(
                                    item.context_expr,
                                    function,
                                )
                                or self._container_contains_caller_taint(
                                    item.context_expr,
                                    function,
                                )
                            )
                        ):
                            continue
                        before = len(self._caller_taints[function])
                        self._caller_taints[function].update(
                            _target_names(item.optional_vars)
                        )
                        changed = (
                            changed
                            or len(self._caller_taints[function]) != before
                        )
                    continue
                elif isinstance(binding, ast.Match):
                    source = binding.subject
                    targets = tuple(
                        target
                        for case in binding.cases
                        for pattern in ast.walk(case.pattern)
                        for target in self._assignment_targets(pattern)
                    )
                else:
                    source = binding.iter
                    targets = (binding.target,)
                if not self._container_contains_caller_taint(
                    source,
                    function,
                ):
                    continue
                before = len(self._caller_taints[function])
                self._caller_taints[function].update(
                    name for target in targets for name in _target_names(target)
                )
                changed = (
                    changed
                    or len(self._caller_taints[function]) != before
                )

            for mutation in container_mutations:
                function = self._enclosing_function(mutation)
                if function is None:
                    continue
                values = tuple(mutation.args) + tuple(
                    keyword.value for keyword in mutation.keywords
                )
                if not any(
                    self._expression_has_caller_taint(value, function)
                    or self._container_contains_caller_taint(
                        value,
                        function,
                    )
                    for value in values
                ):
                    continue
                receiver = ast.unparse(mutation.func.value)
                before = len(self._caller_containers[function])
                self._caller_containers[function].add(receiver)
                changed = (
                    changed
                    or len(self._caller_containers[function]) != before
                )

            for return_node in returns:
                function = self._enclosing_function(return_node)
                if (
                    function is None
                    or not self._value_carries_caller_taint(
                        return_node.value,
                        function,
                    )
                ):
                    continue
                outer = self._enclosing_function(function)
                if outer is None:
                    continue
                before = len(self._caller_taints[outer])
                self._caller_taints[outer].add(function.name)
                changed = (
                    changed
                    or len(self._caller_taints[outer]) != before
                )

    def _check_caller_metadata(self) -> None:
        for node in ast.walk(self.tree):
            outer = self._enclosing_function(node)
            if outer is None:
                continue
            expressions: tuple[ast.AST, ...] = ()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                expressions = (
                    tuple(node.decorator_list)
                    + tuple(node.args.defaults)
                    + tuple(
                        value
                        for value in node.args.kw_defaults
                        if value is not None
                    )
                )
                for candidate in ast.walk(node):
                    if (
                        isinstance(
                            candidate,
                            (ast.Return, ast.Yield, ast.YieldFrom),
                        )
                        and candidate.value is not None
                        and self._expression_has_caller_taint(
                            candidate.value,
                            outer,
                        )
                    ):
                        self._add(
                            candidate.value,
                            "caller_callable_return_forbidden",
                        )
            elif isinstance(node, ast.ClassDef):
                expressions = (
                    tuple(node.decorator_list)
                    + tuple(node.bases)
                    + tuple(keyword.value for keyword in node.keywords)
                    + tuple(
                        candidate.value
                        for candidate in node.body
                        if isinstance(
                            candidate,
                            (ast.Assign, ast.AnnAssign),
                        )
                        and candidate.value is not None
                    )
                )
            elif isinstance(node, ast.Lambda):
                expressions = tuple(node.args.defaults) + tuple(
                    value
                    for value in node.args.kw_defaults
                    if value is not None
                )
            for expression in expressions:
                if (
                    self._value_carries_caller_taint(expression, outer)
                    or self._container_contains_caller_taint(
                        expression,
                        outer,
                    )
                ):
                    self._add(
                        expression,
                        "caller_callable_metadata_forbidden",
                    )

    def _check_caller_assignment_escape(self, node: ast.AST) -> None:
        value: ast.AST | None = None
        targets: tuple[ast.AST, ...] = ()
        if isinstance(node, ast.Assign):
            value = node.value
            targets = tuple(node.targets)
        elif isinstance(node, ast.AnnAssign):
            value = node.value
            targets = (node.target,)
        if value is None:
            return
        function = self._enclosing_function(node)
        if (
            function is None
            or not self._expression_has_caller_taint(value, function)
        ):
            return
        for target in targets:
            path = self._resolved_path(target)
            if (
                path is not None
                and "." in path
                and path.split(".", 1)[0] in self.aliases.values()
            ):
                self._add(
                    target,
                    "caller_module_assignment_forbidden",
                )

    def _check_caller_callback_arguments(self, node: ast.Call) -> None:
        function = self._enclosing_function(node)
        if function is None:
            return
        callback_keywords = {
            "action",
            "callback",
            "default_factory",
            "factory",
            "func",
            "function",
            "handler",
            "key",
            "predicate",
            "target",
        }
        for keyword in node.keywords:
            if (
                keyword.arg in callback_keywords
                and self._expression_has_caller_taint(
                    keyword.value,
                    function,
                )
            ):
                self._add(
                    keyword.value,
                    "caller_callback_argument_forbidden",
                )

        path = self._resolved_path(node.func)
        if path == "threading.Thread":
            for value in tuple(node.args) + tuple(
                keyword.value for keyword in node.keywords
            ):
                if (
                    self._expression_has_caller_taint(value, function)
                    or self._container_contains_caller_taint(
                        value,
                        function,
                    )
                ):
                    self._add(
                        value,
                        "caller_callback_argument_forbidden",
                    )
        positional_callback_indexes = {
            "atexit.register": (0,),
            "filter": (0,),
            "map": (0,),
            "threading.Thread": tuple(range(len(node.args))),
            "threading.Timer": (1,),
            "threading.Barrier": (1,),
            "threading.settrace": (0,),
            "threading.settrace_all_threads": (0,),
            "weakref.ref": (1,),
            "weakref.finalize": (1,),
        }
        for index in positional_callback_indexes.get(path or "", ()):
            if (
                index < len(node.args)
                and self._expression_has_caller_taint(
                    node.args[index],
                    function,
                )
            ):
                self._add(
                    node.args[index],
                    "caller_callback_argument_forbidden",
                )

    def _check_caller_call_target(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Call):
            self._add(node, "call_result_target_forbidden")
            return
        function = self._enclosing_function(node)
        if (
            function is not None
            and isinstance(node.func, ast.Name)
            and node.func.id in {"self", "cls"}
            and not isinstance(
                self.parents.get(function),
                ast.ClassDef,
            )
        ):
            self._add(node, "module_receiver_dispatch_forbidden")
            return
        if (
            function is not None
            and isinstance(node.func, ast.Lambda)
            and any(
                self._expression_has_caller_taint(argument, function)
                or self._container_contains_caller_taint(
                    argument,
                    function,
                )
                for argument in tuple(node.args)
                + tuple(
                    keyword.value for keyword in node.keywords
                )
            )
        ):
            self._add(node, "caller_lambda_dispatch_forbidden")
            return
        if (
            function is None
            or not self._value_carries_caller_taint(node.func, function)
        ):
            return
        key = (
            self.filename,
            self._function_name(node),
            ast.unparse(node),
        )
        self.parameter_calls[key] += 1
        if (
            self.filename == "ingress.py"
            and self._function_name(node) == "close_external_halt"
            and key in EXPECTED_PARAMETER_CALLS
            and not self._has_exact_type_guard(
                node,
                "runtime",
                "EventRuntime",
            )
        ):
            self._add(node, "caller_type_dominance_forbidden")
        if (
            key
            == (
                "preflight.py",
                "_lexically_normal_absolute_path",
                "value.is_absolute()",
            )
            and not self._has_exact_type_guard(
                node,
                "value",
                "_PATH_TYPE",
            )
        ):
            self._add(node, "concrete_path_type_dominance_forbidden")
        if (
            key
            == (
                "capture.py",
                "_validate_and_redact_json",
                "current.items()",
            )
            and not self._has_exact_type_guard(
                node,
                "current",
                "dict",
            )
        ):
            self._add(node, "concrete_dict_type_dominance_forbidden")
        if key not in EXPECTED_PARAMETER_CALLS:
            self._add(
                node,
                f"caller_callable_forbidden:{ast.unparse(node)}",
            )

    def _check_call(self, node: ast.Call) -> None:
        self._check_caller_call_target(node)
        self._check_caller_callback_arguments(node)
        if self._check_reflection_call(node):
            return
        if self._check_fields_call(node):
            return
        if self._check_queue_call(node):
            return
        if self._check_get_call(node):
            return

        path = self._resolved_path(node.func)
        if path is not None and path.startswith("unicodedata."):
            unicode_key = (
                self.filename,
                self._function_name(node),
                ast.unparse(node),
            )
            self.unicode_calls[unicode_key] += 1
            if unicode_key not in EXPECTED_UNICODE_CALLS:
                self._add(node, "unicode_call_forbidden")
        if self.filename == "preflight.py":
            filesystem_key = (
                self.filename,
                self._function_name(node),
                ast.unparse(node),
            )
            if filesystem_key in EXPECTED_PREFLIGHT_FILESYSTEM_CALLS:
                self.preflight_filesystem_calls[filesystem_key] += 1
            else:
                if path == "open":
                    self._add(node, "preflight_direct_open_forbidden")
                if (
                    path is not None
                    and path.startswith("os.")
                    and path not in PREFLIGHT_ALLOWED_OS_CALLS
                ):
                    self._add(
                        node,
                        f"preflight_filesystem_access_forbidden:{path}",
                    )
                if isinstance(node.func, ast.Attribute) and (
                    node.func.attr in PREFLIGHT_FORBIDDEN_PATH_FS_METHODS
                    or node.func.attr in PREFLIGHT_FORBIDDEN_OUTPUT_METHODS
                ):
                    self._add(
                        node,
                        "preflight_filesystem_access_forbidden:"
                        f"{node.func.attr}",
                    )
        if ast.unparse(node.func) in self.get_result_targets:
            self._add(node, "mapping_get_result_invoked")
        if isinstance(node.func, ast.Subscript):
            self._add(node, "dynamic_subscript_call_forbidden")
        if path is not None and path.startswith("queue."):
            self._add(node, f"queue_call_forbidden:{path}")
        if path in {
            "getattr",
            "globals",
            "hasattr",
            "locals",
            "setattr",
            "vars",
        }:
            self._add(node, f"dynamic_reflection_call_forbidden:{path}")
        if path is not None and self._dangerous_path(path):
            self._add(node, f"dangerous_call:{path}")
        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_BUILTINS:
                self._add(node, f"dynamic_execution_forbidden:{node.func.id}")
            if node.func.id in FORBIDDEN_CALL_NAMES:
                self._add(node, f"transport_or_mutation_call:{node.func.id}")
            if node.func.id == "urlsplit":
                if (
                    self.filename not in {"capture.py", "entitlements.py"}
                    or path != "urllib.parse.urlsplit"
                    or len(node.args) != 1
                    or node.keywords
                ):
                    self._add(node, "urlsplit_use_forbidden")
            if node.func.id in self.get_result_names:
                self._add(node, "mapping_get_result_invoked")
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in FORBIDDEN_CALL_NAMES | FORBIDDEN_TRANSPORT_NAMES:
                self._add(
                    node,
                    f"transport_or_mutation_call:{node.func.attr}",
                )
            if path in {"types.CodeType", "types.FunctionType"}:
                self._add(node, f"dynamic_callable_construction:{path}")
            if path is not None and path.startswith("os.") and (
                path == "os.system"
                or path == "os.popen"
                or path in {"os.fork", "os.forkpty", "os.startfile"}
                or path.startswith(("os.spawn", "os.posix_spawn", "os.exec"))
            ):
                self._add(node, f"process_call_forbidden:{path}")
            if path == "math.isfinite":
                if (
                    (self.filename, self._function_name(node), ast.unparse(node))
                    not in {
                        (
                            "ingress.py",
                            "_positive_timeout",
                            "math.isfinite(value)",
                        ),
                        ("mailbox.py", "take", "math.isfinite(timeout)"),
                    }
                ):
                    self._add(node, "math_use_forbidden")
            elif path is not None and path.startswith("math."):
                self._add(node, f"math_use_forbidden:{path}")

        for argument in tuple(node.args) + tuple(
            keyword.value for keyword in node.keywords
        ):
            argument_path = self._resolved_path(argument)
            if argument_path is not None and self._dangerous_path(argument_path):
                self._add(node, f"dangerous_callback_argument:{argument_path}")

    @staticmethod
    def _taint_assignment_targets(node: ast.AST) -> tuple[str, ...]:
        targets: tuple[ast.AST, ...] = ()
        if isinstance(node, ast.Assign):
            targets = tuple(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            targets = (node.target,)

        def render(target: ast.AST) -> tuple[str, ...]:
            if isinstance(target, (ast.Tuple, ast.List)):
                return tuple(
                    value for item in target.elts for value in render(item)
                )
            return (ast.unparse(target),)

        return tuple(value for target in targets for value in render(target))

    def _build_get_result_taint(self) -> None:
        assignments = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        ]
        changed = True
        while changed:
            changed = False
            for assignment in assignments:
                value = assignment.value
                if value is None:
                    continue
                tainted = any(
                    (
                        isinstance(candidate, ast.Call)
                        and isinstance(candidate.func, ast.Attribute)
                        and candidate.func.attr == "get"
                        and ast.unparse(candidate.func.value) != "self._queue"
                    )
                    or (
                        isinstance(candidate, (ast.Name, ast.Attribute))
                        and ast.unparse(candidate) in self.get_result_targets
                    )
                    for candidate in ast.walk(value)
                )
                if not tainted:
                    continue
                before = len(self.get_result_targets)
                self.get_result_targets.update(
                    self._taint_assignment_targets(assignment)
                )
                changed = changed or len(self.get_result_targets) != before

    def run(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self._check_import(node)

        self._build_get_result_taint()
        self._build_caller_taint()
        self._check_caller_metadata()

        for node in ast.walk(self.tree):
            self._check_definition(node)
            self._check_string(node)
            self._check_caller_assignment_escape(node)
            if isinstance(node, ast.Attribute):
                self._check_attribute(node)
                if node.attr == "__dataclass_fields__":
                    key = (
                        self.filename,
                        self._function_name(node),
                        ast.unparse(node),
                    )
                    self.dataclass_fields[key] += 1
                    if key not in EXPECTED_DATACLASS_FIELDS:
                        self._add(node, "dataclass_field_source_forbidden")
            if isinstance(node, ast.Name) and node.id in {
                "__builtins__",
                "__import__",
                "__loader__",
                "__spec__",
                "globals",
                "vars",
                "eval",
                "exec",
                "breakpoint",
                "getattr",
                "hasattr",
                "locals",
                "setattr",
            }:
                parent = self.parents.get(node)
                approved_reflection_name = (
                    isinstance(parent, ast.Call)
                    and parent.func is node
                    and (
                        self.filename,
                        self._function_name(parent),
                        node.id,
                        ast.unparse(parent),
                    )
                    in EXPECTED_REFLECTION_CALLS
                )
                if not approved_reflection_name:
                    self._add(node, f"recovered_builtin_forbidden:{node.id}")
            if isinstance(node, ast.Subscript):
                base = self._resolved_path(node.value)
                if base in {"sys.modules", "__builtins__"}:
                    self._add(node, f"reflective_subscript_forbidden:{base}")
            if isinstance(node, ast.Call):
                self._check_call(node)

        self._check_queue_receiver_integrity()

        for actual, expected, label in (
            (self.reflection_calls, EXPECTED_REFLECTION_CALLS, "reflection"),
            (self.fields_calls, EXPECTED_FIELDS_CALLS, "fields"),
            (
                self.dataclass_fields,
                EXPECTED_DATACLASS_FIELDS,
                "dataclass_fields",
            ),
            (self.queue_calls, EXPECTED_QUEUE_CALLS, "queue"),
            (self.parameter_calls, EXPECTED_PARAMETER_CALLS, "parameter"),
            (
                self.preflight_filesystem_calls,
                EXPECTED_PREFLIGHT_FILESYSTEM_CALLS,
                "preflight_filesystem",
            ),
            (self.unicode_imports, EXPECTED_UNICODE_IMPORTS, "unicode_import"),
            (self.unicode_calls, EXPECTED_UNICODE_CALLS, "unicode_call"),
        ):
            for key, count in actual.items():
                if count > expected.get(key, 0):
                    self.violations.append(
                        f"{self.filename}:0:{label}_count_exceeded:{key}:{count}"
                    )

        if self.violations:
            raise DependencyPolicyViolation("\n".join(sorted(set(self.violations))))


def canonical_ast_sha256(source: str, filename: str) -> str:
    try:
        tree = ast.parse(
            textwrap.dedent(source),
            filename=filename,
            type_comments=False,
        )
    except (SyntaxError, TypeError, ValueError):
        raise DependencyPolicyViolation(
            f"{filename}:canonical_ast_parse_forbidden"
        ) from None
    canonical = ast.dump(
        tree,
        annotate_fields=True,
        include_attributes=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_exact_package_inventory(paths: tuple[str, ...]) -> None:
    expected = tuple(sorted(EXPECTED_PRODUCTION_AST_SHA256))
    if paths != expected:
        raise DependencyPolicyViolation(
            "package_ast_inventory_mismatch:"
            f"actual={paths!r}:expected={expected!r}"
        )


def _package_python_paths(package_root: Path) -> tuple[str, ...]:
    if package_root.is_symlink():
        raise DependencyPolicyViolation("package_symlink_forbidden:.")
    pending = [package_root]
    python_paths: list[str] = []
    while pending:
        directory = pending.pop()
        try:
            entries = tuple(os.scandir(directory))
        except OSError as error:
            raise DependencyPolicyViolation(
                "package_inventory_scan_failed"
            ) from error
        for entry in entries:
            entry_path = Path(entry.path)
            relative_path = entry_path.relative_to(package_root).as_posix()
            if entry.is_symlink():
                raise DependencyPolicyViolation(
                    f"package_symlink_forbidden:{relative_path}"
                )
            if entry.is_dir(follow_symlinks=False):
                pending.append(entry_path)
            elif (
                entry.is_file(follow_symlinks=False)
                and entry.name.endswith(".py")
            ):
                python_paths.append(relative_path)
    return tuple(sorted(python_paths))


def _require_production_ast(source: str, filename: str) -> None:
    expected = EXPECTED_PRODUCTION_AST_SHA256.get(filename)
    actual = canonical_ast_sha256(source, filename)
    if expected is None or actual != expected:
        raise DependencyPolicyViolation(
            f"{filename}:production_ast_closure_mismatch:{actual}"
        )


def check_source(source: str, filename: str) -> DependencyPolicy:
    if type(filename) is not str:
        raise DependencyPolicyViolation("canonical_ast_filename_forbidden")
    digest = canonical_ast_sha256(source, filename)
    approved = (
        EXPECTED_PRODUCTION_AST_SHA256.get(filename) == digest
        or (filename, digest) in EXPECTED_SAFE_FIXTURE_AST_SHA256
    )
    if not approved:
        raise DependencyPolicyViolation(
            f"{filename}:unreviewed_canonical_ast:{digest}"
        )
    policy = DependencyPolicy(textwrap.dedent(source), filename)
    policy.run()
    return policy


def package_policies() -> tuple[DependencyPolicy, ...]:
    package_paths = _package_python_paths(PACKAGE_ROOT)
    _require_exact_package_inventory(package_paths)
    policies: list[DependencyPolicy] = []
    for relative_path in package_paths:
        path = PACKAGE_ROOT / relative_path
        source = path.read_text(encoding="utf-8")
        _require_production_ast(source, relative_path)
        policies.append(
            check_source(
                source,
                relative_path,
            )
        )
    result = tuple(policies)
    for actual, expected, label in (
        (
            sum((policy.reflection_calls for policy in result), Counter()),
            EXPECTED_REFLECTION_CALLS,
            "reflection",
        ),
        (
            sum((policy.fields_calls for policy in result), Counter()),
            EXPECTED_FIELDS_CALLS,
            "fields",
        ),
        (
            sum((policy.dataclass_fields for policy in result), Counter()),
            EXPECTED_DATACLASS_FIELDS,
            "dataclass_fields",
        ),
        (
            sum((policy.queue_calls for policy in result), Counter()),
            EXPECTED_QUEUE_CALLS,
            "queue",
        ),
        (
            sum((policy.parameter_calls for policy in result), Counter()),
            EXPECTED_PARAMETER_CALLS,
            "parameter",
        ),
        (
            sum(
                (
                    policy.preflight_filesystem_calls
                    for policy in result
                ),
                Counter(),
            ),
            EXPECTED_PREFLIGHT_FILESYSTEM_CALLS,
            "preflight_filesystem",
        ),
        (
            sum((policy.unicode_imports for policy in result), Counter()),
            EXPECTED_UNICODE_IMPORTS,
            "unicode_import",
        ),
        (
            sum((policy.unicode_calls for policy in result), Counter()),
            EXPECTED_UNICODE_CALLS,
            "unicode_call",
        ),
    ):
        if actual != expected:
            raise DependencyPolicyViolation(
                f"package_exact_{label}_count_mismatch:"
                f"actual={actual!r}:expected={expected!r}"
            )
    return result


class DependencyBoundaryTests(unittest.TestCase):
    def assert_rejected(
        self,
        source: str,
        *,
        filename: str = "fixture.py",
    ) -> None:
        with self.assertRaises(DependencyPolicyViolation):
            DependencyPolicy(
                textwrap.dedent(source),
                filename,
            ).run()

    def assert_closure_rejected(
        self,
        source: str,
        *,
        filename: str,
    ) -> None:
        with self.assertRaises(DependencyPolicyViolation):
            check_source(source, filename)

    def test_semantic_rejection_helper_executes_dependency_policy(self) -> None:
        marker = RuntimeError("semantic runner executed")
        with (
            mock.patch.object(
                DependencyPolicy,
                "run",
                side_effect=marker,
            ) as run_call,
            self.assertRaisesRegex(
                RuntimeError,
                r"\Asemantic runner executed\Z",
            ),
        ):
            self.assert_rejected("pass")
        run_call.assert_called_once_with()

    def test_tennis_package_passes_deny_by_default_policy(self) -> None:
        policies = package_policies()
        self.assertEqual(
            tuple(policy.filename for policy in policies),
            _package_python_paths(PACKAGE_ROOT),
        )

    def test_exact_reflection_dataclass_and_queue_counts_are_frozen(self) -> None:
        policies = package_policies()
        reflection = sum(
            (policy.reflection_calls for policy in policies),
            Counter(),
        )
        fields_calls = sum(
            (policy.fields_calls for policy in policies),
            Counter(),
        )
        dataclass_fields = sum(
            (policy.dataclass_fields for policy in policies),
            Counter(),
        )
        queue_calls = sum(
            (policy.queue_calls for policy in policies),
            Counter(),
        )
        parameter_calls = sum(
            (policy.parameter_calls for policy in policies),
            Counter(),
        )
        preflight_filesystem_calls = sum(
            (
                policy.preflight_filesystem_calls
                for policy in policies
            ),
            Counter(),
        )
        unicode_imports = sum(
            (policy.unicode_imports for policy in policies),
            Counter(),
        )
        unicode_calls = sum(
            (policy.unicode_calls for policy in policies),
            Counter(),
        )
        self.assertEqual(reflection, EXPECTED_REFLECTION_CALLS)
        self.assertEqual(fields_calls, EXPECTED_FIELDS_CALLS)
        self.assertEqual(dataclass_fields, EXPECTED_DATACLASS_FIELDS)
        self.assertEqual(queue_calls, EXPECTED_QUEUE_CALLS)
        self.assertEqual(parameter_calls, EXPECTED_PARAMETER_CALLS)
        self.assertEqual(
            preflight_filesystem_calls,
            EXPECTED_PREFLIGHT_FILESYSTEM_CALLS,
        )
        self.assertEqual(unicode_imports, EXPECTED_UNICODE_IMPORTS)
        self.assertEqual(unicode_calls, EXPECTED_UNICODE_CALLS)

    def test_existing_v6_modules_do_not_import_tennis_v1(self) -> None:
        imported_by: list[str] = []
        for path in sorted(REPO_ROOT.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(
                    alias.name == "tennis_v1"
                    or alias.name.startswith("tennis_v1.")
                    for alias in node.names
                ):
                    imported_by.append(path.name)
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module is not None
                    and (
                        node.module == "tennis_v1"
                        or node.module.startswith("tennis_v1.")
                    )
                ):
                    imported_by.append(path.name)
        self.assertEqual(imported_by, [])

    def test_direct_from_star_and_relative_import_escapes_are_rejected(self) -> None:
        cases = (
            "import requests",
            "from requests import Session",
            "from http.client import HTTPConnection",
            "import socket",
            "import subprocess",
            "import multiprocessing",
            "import importlib",
            "import runpy",
            "from .events import *",
            "from ..tennis_v1 import events",
            "from os import system",
            "from types import FunctionType",
            "from urllib.parse import urlsplit as split",
            "import urllib",
            "import urllib.parse",
            "from urllib.request import urlopen",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_rejected(source)

    def test_os_process_execution_and_dynamic_import_escapes_are_rejected(self) -> None:
        cases = (
            "import os\nos.system('id')",
            "import os\nos.popen('id')",
            "import os\nos.fork()",
            "import os\nos.forkpty()",
            "import os\nos.startfile('x')",
            "import os\nos.spawnv(0, 'x', ())",
            "import os\nos.posix_spawn('x', (), {})",
            "import os\nos.execve('x', (), {})",
            "module = __import__('os')",
            "eval('1 + 1')",
            "exec('x = 1')",
            "code = compile('1', 'x', 'eval')\neval(code)",
            "breakpoint()",
            "import types\ntypes.FunctionType(None, {})",
            "import types\ntypes.CodeType()",
            "import sys\nsys.modules['os'].system('id')",
            "__builtins__['__import__']('os')",
            "def f():\n    return 1\nf.__globals__['os'].system('id')",
            "object.__subclasses__()",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_rejected(source)

    def test_alias_container_return_lambda_and_nested_scope_escapes_are_rejected(self) -> None:
        cases = (
            "import os as operating\noperating.system('id')",
            "import os\na = b = os.system",
            "import os\n(a, b) = (os.system, len)",
            "import os\ncallbacks = [os.system]",
            "import os\ncallbacks = {'run': os.system}",
            "import os\ncallback = (os.system,)[0]\ncallback('id')",
            "import os\ndef recover():\n    return os.system",
            "import os\ncallback = lambda: os.system('id')",
            "import os\n@os.system\ndef decorated():\n    pass",
            "import os\ndef configured(callback=os.system):\n    pass",
            "import os\nclass Meta(type):\n    pass\nclass Value(metaclass=os.system):\n    pass",
            "import os\ncallbacks = [os.system for _ in (0,)]",
            "import os\n(callback := os.system)",
            "import os\ndef outer():\n    def inner():\n        return os.system\n    return inner",
            (
                "import os\nimport threading\n"
                "threading.Thread(target=os.system, args=('id',))"
            ),
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_rejected(source)

    def test_reflected_environment_and_assembled_method_escapes_are_rejected(self) -> None:
        cases = (
            "import os\ngetattr(os, 'system')('id')",
            "import os\nname = 'sys' + 'tem'\ngetattr(os, name)('id')",
            "import os\nname = f\"{'sys'}tem\"\ngetattr(os, name)('id')",
            "import os\nname = '{}{}'.format('sys', 'tem')\ngetattr(os, name)('id')",
            (
                "import os\nname = os.environ['CALLABLE']\n"
                "getattr(os, name)('id')"
            ),
            "import os\nreflect = getattr\nreflect(os, 'system')('id')",
            "method = 'P' + 'OST'",
            "method = f\"{'PO'}ST\"",
            "method = '{}{}'.format('PO', 'ST')",
            "endpoint = '/portfolio/' + 'orders'",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_rejected(source)

    def test_reviewer_reproduced_scope_provenance_and_reflection_bypasses_are_rejected(self) -> None:
        cases = (
            (
                "fixture.py",
                "def run(provider, url):\n"
                "    runner = provider.get\n"
                "    return runner(url)",
            ),
            ("fixture.py", "import os\nvars(os)['system']('id')"),
            (
                "fixture.py",
                "import os\nreflect = getattr\nreflect(os, 'system')('id')",
            ),
            (
                "fixture.py",
                "import os\nos.__getattribute__('system')('id')",
            ),
            (
                "adapter_contract.py",
                "def load_active_adapter_contract(attacker):\n"
                "    registry = attacker\n"
                "    return registry.get('runner')()",
            ),
            (
                "capture.py",
                "from urllib.parse import urlsplit\n"
                "def _unsafe_string(urlsplit, value):\n"
                "    return urlsplit(value)",
            ),
            (
                "mailbox.py",
                "import queue\n"
                "def __init__(self, attacker):\n"
                "    queue = attacker\n"
                "    self._queue = queue.Queue(maxsize=1)",
            ),
            (
                "capture.py",
                "def issue_capture_authority(session_authorizer):\n"
                "    return callable(getattr("
                "session_authorizer, 'authorize_capture', None))",
            ),
            (
                "fixture.py",
                "import os\n"
                "globals()['__builtins__']['__import__']('os')",
            ),
            (
                "fixture.py",
                "import sys\n"
                "sys._getframe().f_globals['__builtins__']"
                "['__import__']('os')",
            ),
            (
                "fixture.py",
                "def run(module):\n    return module.system('id')",
            ),
            (
                "fixture.py",
                "import os\n"
                "def recover(module):\n    return module.system\n"
                "recover(os)('id')",
            ),
            (
                "fixture.py",
                "import os\nimport threading\n"
                "threading.Thread(target=vars(os)['system'])",
            ),
            (
                "fixture.py",
                "import os\n"
                "def run(callback=vars(os)['system']):\n    return callback()",
            ),
            ("fixture.py", "import math\nmath.sqrt(4.0)"),
            (
                "fixture.py",
                "def run(attacker, value):\n"
                "    attacker.put_nowait(value)",
            ),
            (
                "adapter_contract.py",
                "def load_active_adapter_contract(registry):\n"
                "    lookup = registry.get\n"
                "    return lookup('key')",
            ),
        )
        for filename, source in cases:
            with self.subTest(filename=filename, source=source):
                self.assert_rejected(source, filename=filename)

    def test_dominating_guards_require_lexical_builtins_and_last_safe_definition(self) -> None:
        cases = (
            (
                "events.py",
                """
                class PersistedEvent:
                    def __post_init__(self, type):
                        if type(self) is not PersistedEvent:
                            raise ValueError
                        return getattr(self, field_name)
                """,
            ),
            (
                "events.py",
                """
                class PersistedEvent:
                    def __post_init__(self, PersistedEvent):
                        if type(self) is not PersistedEvent:
                            raise ValueError
                        return getattr(self, field_name)
                """,
            ),
            (
                "adapter_contract.py",
                """
                def load_active_adapter_contract(attacker, condition):
                    if condition:
                        registry = _validated_registry_snapshot(
                            _ADAPTER_REGISTRY
                        )
                    registry = attacker
                    return registry.get(("provider", "tier"))
                """,
            ),
            (
                "entitlements.py",
                """
                def _evaluate_provider_as_of(environ, name):
                    type(environ) is dict
                    return environ.get(name)
                """,
            ),
            (
                "entitlements.py",
                """
                def _evaluate_provider_as_of(environ, name):
                    if type(environ) is not dict:
                        pass
                    return environ.get(name)
                """,
            ),
            (
                "entitlements.py",
                """
                def _evaluate_provider_as_of(environ, name):
                    if False:
                        if type(environ) is not dict:
                            raise ValueError
                    return environ.get(name)
                """,
            ),
            (
                "entitlements.py",
                """
                def _evaluate_provider_as_of(environ, name, type):
                    if type(environ) is not dict:
                        raise ValueError
                    return environ.get(name)
                """,
            ),
        )
        for filename, source in cases:
            with self.subTest(filename=filename, source=source):
                self.assert_rejected(source, filename=filename)

    def test_preflight_path_dispatch_requires_dominating_concrete_path_guard(
        self,
    ) -> None:
        reviewed = """
            from pathlib import Path
            _PATH_TYPE = type(Path())
            def _lexically_normal_absolute_path(value):
                if type(value) is not _PATH_TYPE:
                    return False
                return value.is_absolute()
        """
        check_source(reviewed, "preflight.py")

        no_guard = """
            from pathlib import Path
            _PATH_TYPE = type(Path())
            def _lexically_normal_absolute_path(value):
                return value.is_absolute()
        """
        subclass_guard = """
            from pathlib import Path
            _PATH_TYPE = type(Path())
            def _lexically_normal_absolute_path(value):
                if not isinstance(value, _PATH_TYPE):
                    return False
                return value.is_absolute()
        """
        rebound_in_positive_branch = """
            from pathlib import Path
            _PATH_TYPE = type(Path())
            attacker = object()
            def _lexically_normal_absolute_path(value):
                if type(value) is _PATH_TYPE:
                    value = attacker
                    return value.is_absolute()
                return False
        """
        rebound_in_negative_else = """
            from pathlib import Path
            _PATH_TYPE = type(Path())
            attacker = object()
            def _lexically_normal_absolute_path(value):
                if type(value) is not _PATH_TYPE:
                    return False
                else:
                    value = attacker
                    return value.is_absolute()
        """
        rebound_by_match = """
            from pathlib import Path
            _PATH_TYPE = type(Path())
            attacker = object()
            def _lexically_normal_absolute_path(value):
                if type(value) is _PATH_TYPE:
                    match attacker:
                        case value:
                            return value.is_absolute()
                return False
        """
        rebound_by_with = """
            from pathlib import Path
            _PATH_TYPE = type(Path())
            attacker = object()
            def _lexically_normal_absolute_path(value):
                if type(value) is _PATH_TYPE:
                    with attacker as value:
                        return value.is_absolute()
                return False
        """
        rebound_by_walrus = """
            from pathlib import Path
            _PATH_TYPE = type(Path())
            attacker = object()
            def _lexically_normal_absolute_path(value):
                if type(value) is _PATH_TYPE:
                    if (value := attacker):
                        return value.is_absolute()
                return False
        """
        rebound_inside_boolean_guard = """
            from pathlib import Path
            _PATH_TYPE = type(Path())
            attacker = object()
            def _lexically_normal_absolute_path(value):
                return (
                    type(value) is _PATH_TYPE
                    and (value := attacker)
                    and value.is_absolute()
                )
        """
        for source in (
            no_guard,
            subclass_guard,
            rebound_in_positive_branch,
            rebound_in_negative_else,
            rebound_by_match,
            rebound_by_with,
            rebound_by_walrus,
            rebound_inside_boolean_guard,
        ):
            with self.subTest(source=source), self.assertRaises(
                DependencyPolicyViolation
            ):
                DependencyPolicy(
                    textwrap.dedent(source),
                    "preflight.py",
                ).run()

    def test_preflight_direct_filesystem_catalog_is_rejected(self) -> None:
        open_modes = (
            "r",
            "rb",
            "w",
            "a",
            "x",
            "r+",
            "w+b",
        )
        os_calls = (
            "open",
            "read",
            "stat",
            "lstat",
            "listdir",
            "scandir",
            "walk",
            "access",
            "readlink",
            "chmod",
            "chown",
            "fchmod",
            "fchown",
            "ftruncate",
            "link",
            "makedirs",
            "mkdir",
            "mknod",
            "remove",
            "removedirs",
            "rename",
            "renames",
            "replace",
            "rmdir",
            "symlink",
            "truncate",
            "unlink",
            "write",
        )
        path_methods = tuple(sorted(PREFLIGHT_FORBIDDEN_PATH_FS_METHODS))
        output_methods = tuple(sorted(PREFLIGHT_FORBIDDEN_OUTPUT_METHODS))
        cases = [
            (
                f"builtin_open_{mode}",
                (
                    "def _capture_repository_root():\n"
                    f"    return open('/tmp/input', {mode!r})\n"
                ),
            )
            for mode in open_modes
        ]
        cases.extend(
            (
                f"os_{name}",
                (
                    "import os\n"
                    "def _capture_repository_root():\n"
                    f"    return os.{name}('/tmp/input')\n"
                ),
            )
            for name in os_calls
        )
        cases.extend(
            (
                f"path_{name}",
                (
                    "from pathlib import Path\n"
                    "def _capture_repository_root():\n"
                    f"    return Path('/tmp/input').{name}()\n"
                ),
            )
            for name in path_methods
        )
        cases.extend(
            (
                f"file_{name}",
                (
                    "def _capture_repository_root():\n"
                    f"    return open('/tmp/input', 'rb').{name}()\n"
                ),
            )
            for name in output_methods
        )

        for label, source in cases:
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(
                    DependencyPolicyViolation,
                    "preflight_.*filesystem|preflight_direct_open",
                ),
            ):
                DependencyPolicy(
                    source,
                    "preflight.py",
                ).run()

    def test_preflight_only_allows_the_frozen_repository_root_probe(self) -> None:
        source = """
            from pathlib import Path
            def _capture_repository_root():
                return Path(__file__).resolve(strict=True)
        """
        policy = DependencyPolicy(
            textwrap.dedent(source),
            "preflight.py",
        )
        policy.run()
        self.assertEqual(
            policy.preflight_filesystem_calls,
            EXPECTED_PREFLIGHT_FILESYSTEM_CALLS,
        )

    def test_preflight_function_signatures_are_exact_and_closed(self) -> None:
        seam_names = (
            "repo_root",
            "loader",
            "adapter",
            "registry",
            "digest",
            "permission",
            "qualification",
            "quota",
            "clock",
            "callback",
        )
        cases = [
            (
                name,
                f"def run({name}):\n    return None\n",
            )
            for name in seam_names
        ]
        cases.extend(
            (
                ("varargs", "def run(*args):\n    return None\n"),
                ("kwargs", "def run(**kwargs):\n    return None\n"),
                (
                    "public_extra_keyword",
                    (
                        "def run_entitlement_preflight("
                        "config, request, *, environ, loader=None"
                        "):\n"
                        "    return None\n"
                    ),
                ),
                (
                    "internal_extra_keyword",
                    (
                        "def _run_preflight("
                        "config, request, environ, *, callback=None"
                        "):\n"
                        "    return None\n"
                    ),
                ),
            )
        )

        for label, source in cases:
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(
                    DependencyPolicyViolation,
                    "preflight_signature_forbidden",
                ),
            ):
                DependencyPolicy(
                    source,
                    "preflight.py",
                ).run()

    def test_unicode_normalization_authority_is_exactly_scoped(self) -> None:
        cases = (
            (
                "capture.py",
                "import unicodedata\n",
            ),
            (
                "preflight.py",
                "import unicodedata as unicode_tools\n",
            ),
            (
                "preflight.py",
                """
                import unicodedata
                def _safe_identifier(value):
                    return unicodedata.normalize("NFD", value)
                """,
            ),
            (
                "pinned_file.py",
                """
                import unicodedata
                value = unicodedata.category("x")
                """,
            ),
        )
        for filename, source in cases:
            with (
                self.subTest(filename=filename, source=source),
                self.assertRaisesRegex(
                    DependencyPolicyViolation,
                    "unicode_(?:import|call)_forbidden",
                ),
            ):
                DependencyPolicy(
                    textwrap.dedent(source),
                    filename,
                ).run()

    def test_projection_prevalidation_must_be_unconditional_and_dominating(self) -> None:
        cases = (
            """
            def _evaluate_provider_as_of(manifest, name):
                if False:
                    _canonical_projection(manifest)
                return getattr(manifest.capabilities, name)
            """,
            """
            def _evaluate_provider_as_of(manifest, name):
                ignored = lambda: _canonical_projection(manifest)
                return getattr(manifest.capabilities, name)
            """,
            """
            def _evaluate_provider_as_of(manifest, name, condition):
                if condition:
                    _canonical_projection(manifest)
                return getattr(manifest.capabilities, name)
            """,
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_rejected(source, filename="entitlements.py")

    def test_relative_reexports_loaders_and_container_carried_get_results_are_rejected(self) -> None:
        cases = (
            (
                "fixture.py",
                """
                from .events import __builtins__ as safe
                safe["__import__"]("socket").create_connection(("x", 1))
                """,
            ),
            (
                "fixture.py",
                """
                from .retention import sys as runtime
                runtime.modules["socket"].create_connection(("x", 1))
                """,
            ),
            (
                "fixture.py",
                """
                __loader__.load_module("socket").create_connection(("x", 1))
                """,
            ),
            (
                "fixture.py",
                """
                __spec__.loader.load_module("socket").create_connection(
                    ("x", 1)
                )
                """,
            ),
            (
                "adapter_contract.py",
                """
                def load_active_adapter_contract(provider, tier):
                    registry = _validated_registry_snapshot(
                        _ADAPTER_REGISTRY
                    )
                    [registry.get((provider, tier))][0]()
                """,
            ),
            (
                "adapter_contract.py",
                """
                def load_active_adapter_contract(provider, tier):
                    registry = _validated_registry_snapshot(
                        _ADAPTER_REGISTRY
                    )
                    runner = (registry.get((provider, tier)),)[0]
                    runner()
                """,
            ),
        )
        for filename, source in cases:
            with self.subTest(filename=filename, source=source):
                self.assert_rejected(source, filename=filename)

    def test_queue_aliases_and_private_queue_reassignment_are_rejected(self) -> None:
        cases = (
            (
                "mailbox.py",
                "import queue as runtime\nvalue = runtime.SimpleQueue()",
            ),
            (
                "mailbox.py",
                "import queue as runtime\nvalue = runtime.Queue()",
            ),
            (
                "mailbox.py",
                """
                import queue
                def __init__(self, attacker):
                    self._queue = queue.Queue(maxsize=1)
                    self._queue = attacker
                def publish(self, snapshot):
                    self._queue.get_nowait()
                    self._queue.put_nowait(snapshot)
                def take(self, timeout):
                    return self._queue.get(timeout=timeout)
                """,
            ),
            (
                "ingress.py",
                """
                import queue
                def __init__(self, attacker):
                    self._queue = queue.Queue(maxsize=self._capacity)
                    self._queue = attacker
                def enqueue(self, node):
                    self._queue.put_nowait(node)
                def _runtime_failure(self):
                    return self._queue.get_nowait()
                def drain_one(self):
                    first = self._queue.get_nowait()
                    second = self._queue.get_nowait()
                    return first, second
                """,
            ),
        )
        for filename, source in cases:
            with self.subTest(filename=filename, source=source):
                self.assert_rejected(source, filename=filename)

    def test_queue_constructor_dominance_and_closed_receiver_surface_are_enforced(self) -> None:
        cases = (
            (
                "mailbox.py",
                """
                import queue
                def __init__(self, condition):
                    if condition:
                        self._queue = queue.Queue(maxsize=1)
                def publish(self, snapshot):
                    self._queue.get_nowait()
                    self._queue.put_nowait(snapshot)
                def take(self, timeout):
                    return self._queue.get(timeout=timeout)
                """,
            ),
            (
                "mailbox.py",
                """
                import queue
                def __init__(self, attacker):
                    self._queue = queue.Queue(maxsize=1)
                    object.__setattr__(self, "_queue", attacker)
                def publish(self, snapshot):
                    self._queue.get_nowait()
                    self._queue.put_nowait(snapshot)
                def take(self, timeout):
                    return self._queue.get(timeout=timeout)
                """,
            ),
            (
                "mailbox.py",
                "import queue\nvalue = queue.LifoQueue(maxsize=1)",
            ),
            (
                "mailbox.py",
                """
                import queue
                def __init__(self):
                    self._queue = queue.Queue(maxsize=1)
                def publish(self, snapshot):
                    self._queue.queue.clear()
                    self._queue.get_nowait()
                    self._queue.put_nowait(snapshot)
                def take(self, timeout):
                    return self._queue.get(timeout=timeout)
                """,
            ),
        )
        for filename, source in cases:
            with self.subTest(filename=filename, source=source):
                self.assert_rejected(source, filename=filename)

    def test_safe_get_results_and_private_mappings_cannot_be_rebound_into_dispatch(self) -> None:
        cases = (
            (
                "adapter_contract.py",
                """
                def load_active_adapter_contract(provider, tier):
                    registry = _validated_registry_snapshot(
                        _ADAPTER_REGISTRY
                    )
                    first = registry.get((provider, tier))
                    second = first
                    second()
                """,
            ),
            (
                "adapter_contract.py",
                """
                def load_active_adapter_contract(provider, tier):
                    registry = _validated_registry_snapshot(
                        _ADAPTER_REGISTRY
                    )
                    first = registry.get((provider, tier))
                    second = (first,)[0]
                    second()
                """,
            ),
            (
                "retention.py",
                """
                def read(self, attacker, key):
                    self._session_states = attacker
                    return self._session_states.get(key)
                """,
            ),
        )
        for filename, source in cases:
            with self.subTest(filename=filename, source=source):
                self.assert_rejected(source, filename=filename)

    def test_guard_builtins_and_exact_types_cannot_be_import_aliases(self) -> None:
        cases = (
            (
                "events.py",
                """
                import math as type
                class SessionManifest:
                    def __post_init__(self):
                        if type(self) is not SessionManifest:
                            raise TypeError
                        return getattr(self, field_name)
                """,
            ),
            (
                "capture.py",
                """
                import math as SessionManifest
                def issue_capture_authority(session_authorizer):
                    session_manifest = session_authorizer.session_manifest
                    if (
                        type(session_manifest) is not SessionManifest
                        or not callable(
                            getattr(
                                session_authorizer,
                                "authorize_capture",
                                None,
                            )
                        )
                    ):
                        raise TypeError
                """,
            ),
            (
                "ingress.py",
                """
                import math as EventRuntime
                def close_external_halt(self, runtime):
                    if type(runtime) is not EventRuntime:
                        raise TypeError
                    return runtime.close_halted('operator_halt')
                """,
            ),
        )
        for filename, source in cases:
            with self.subTest(filename=filename, source=source):
                self.assert_rejected(source, filename=filename)

    def test_nested_paths_cannot_borrow_root_filename_exceptions(self) -> None:
        cases = (
            (
                "subpkg/capture.py",
                """
                from urllib.parse import urlsplit
                def _unsafe_string(value):
                    return urlsplit(value)
                """,
            ),
            (
                "subpkg/mailbox.py",
                """
                import queue
                def __init__(self):
                    self._queue = queue.Queue(maxsize=1)
                """,
            ),
        )
        for filename, source in cases:
            with self.subTest(filename=filename):
                self.assert_rejected(source, filename=filename)

    def test_dynamic_attribute_and_subscript_transport_dispatch_is_rejected(self) -> None:
        cases = (
            """
            def run(tool, url):
                name = "".join(("g", "et"))
                return tool.__getattr__(name)(url)
            """,
            """
            def run(tool, name, url):
                return tool[name](url)
            """,
            """
            def run(tool):
                return tool["create_order"]()
            """,
            """
            def run(tool):
                name = "create_" + "order"
                return tool[name]()
            """,
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_rejected(source)

    def test_arbitrary_caller_callable_and_bound_method_flow_is_rejected(self) -> None:
        cases = (
            """
            def run(callback):
                return callback("https://example.invalid")
            """,
            """
            def run(runner):
                return runner("https://example.invalid")
            """,
            """
            def run(service, url):
                return service.do(url)
            """,
            """
            def run(service, url):
                return service.__call__(url)
            """,
            """
            def run(callback, url):
                target = callback
                return target(url)
            """,
            """
            def run(callback, url):
                target = (callback,)[0]
                return target(url)
            """,
            """
            def identity(value):
                return value
            def run(callback, url):
                return identity(callback)(url)
            """,
            """
            def run(callback, url):
                def recover():
                    return callback
                return recover()(url)
            """,
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_rejected(source)

    def test_caller_callable_metadata_container_and_callback_flow_is_rejected(self) -> None:
        cases = (
            """
            def run(callback, url):
                return (target := callback)(url)
            """,
            """
            def run(callback, url):
                target = lambda: callback(url)
                return target()
            """,
            """
            def run(callback, url):
                def nested():
                    return callback(url)
                return nested()
            """,
            """
            def run(callback):
                def nested(value=callback):
                    return value
                return nested
            """,
            """
            def run(callback):
                decorator = callback
                @decorator
                def nested():
                    return None
                return nested
            """,
            """
            def run(callback):
                class Value(metaclass=callback):
                    pass
                return Value
            """,
            """
            def run(callback, url):
                targets = [item for item in (callback,)]
                return targets[0](url)
            """,
            """
            import threading
            def run(callback):
                return threading.Thread(target=callback)
            """,
            """
            import threading
            def run(callback):
                return threading.Thread(None, callback)
            """,
            """
            import threading
            def run(callback):
                return threading.Thread(target=lambda: callback())
            """,
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_rejected(source)

    def test_reviewer_caller_flow_catalog_is_rejected(self) -> None:
        cases = (
            "def run(self):\n    return self('x')",
            "def run(cls):\n    return cls('x')",
            """
            def run(callback):
                for target in (callback,):
                    return target("x")
            """,
            """
            def run(callback):
                match (callback,):
                    case (target,):
                        return target("x")
            """,
            """
            def run(callback):
                with callback as target:
                    return target("x")
            """,
            """
            def run(callback):
                targets = []
                targets.append(callback)
                target = targets.pop()
                return target("x")
            """,
            """
            def run(callback):
                targets = {}
                targets.update({"run": callback})
                target = targets.pop("run")
                return target("x")
            """,
            """
            def run(callback):
                class Holder:
                    target = callback
                    def invoke(self):
                        return self.target("x")
                holder = Holder()
                return holder.invoke()
            """,
            """
            def run(callback):
                return (lambda value: value("x"))(callback)
            """,
            """
            def run(callback):
                return (lambda value=callback: value("x"))()
            """,
            """
            def run(callback):
                class Value(**{"metaclass": callback}):
                    pass
                return Value
            """,
            """
            def run(callback):
                options = {"metaclass": callback}
                class Value(**options):
                    pass
                return Value
            """,
            """
            def run(callback):
                class Value(callback):
                    pass
                return Value
            """,
            """
            def run(callback):
                return [target("x") for target in (callback,)]
            """,
            """
            def run(callback):
                targets = (callback,)
                return [target("x") for target in targets]
            """,
            """
            def run(callback):
                class Holder:
                    def recover(self):
                        return callback
                holder = Holder()
                target = holder.recover()
                return target("x")
            """,
            """
            def run(callback):
                def recover():
                    yield callback
                for target in recover():
                    return target("x")
            """,
            """
            import threading
            def run(callback):
                return threading.Thread(
                    target=lambda value: value(),
                    args=(callback,),
                )
            """,
            """
            import threading
            def run(callback):
                return threading.Thread(
                    target=lambda *, value: value(),
                    kwargs={"value": callback},
                )
            """,
            """
            import threading
            def run(callback):
                return threading.Timer(1, callback)
            """,
            """
            import threading
            def run(callback):
                return threading.Barrier(2, callback)
            """,
            """
            import threading
            def run(callback):
                threading.settrace(callback)
            """,
            """
            import threading
            def run(callback):
                threading.excepthook = callback
            """,
            """
            import weakref
            def run(value, callback):
                return weakref.ref(value, callback)
            """,
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_rejected(source)

    def test_canonical_ast_closure_ignores_layout_but_rejects_behavior_change(self) -> None:
        reviewed = """
            import threading
            def owner():
                return threading.current_thread()
        """
        layout_only = """
            # The reviewed behavior is unchanged.

            import threading

            def owner( ):
                return threading.current_thread( )
        """
        changed = """
            import threading
            def owner():
                return threading.main_thread()
        """
        check_source(reviewed, "mailbox.py")
        check_source(layout_only, "mailbox.py")
        self.assert_closure_rejected(changed, filename="mailbox.py")

    def test_canonical_ast_closure_rejects_path_spoof_and_module_change(self) -> None:
        safe_fixture = """
            import threading
            def owner():
                return threading.current_thread()
        """
        self.assert_closure_rejected(
            safe_fixture,
            filename="nested/mailbox.py",
        )
        changed_init = (
            (PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")
            + "\nAST_CLOSURE_BYPASS = 1\n"
        )
        self.assert_closure_rejected(changed_init, filename="__init__.py")

    def test_canonical_ast_digest_tables_and_recursive_inventory_are_frozen(self) -> None:
        actual_paths = _package_python_paths(PACKAGE_ROOT)
        self.assertEqual(
            actual_paths,
            tuple(sorted(EXPECTED_PRODUCTION_AST_SHA256)),
        )
        self.assertEqual(len(EXPECTED_PRODUCTION_AST_SHA256), 21)
        self.assertEqual(len(EXPECTED_SAFE_FIXTURE_AST_SHA256), 18)
        for changed_paths in (
            actual_paths[:-1],
            actual_paths + ("unexpected.py",),
            tuple(
                "renamed.py" if item == "mailbox.py" else item
                for item in actual_paths
            ),
        ):
            with self.subTest(changed_paths=changed_paths):
                with self.assertRaises(DependencyPolicyViolation):
                    _require_exact_package_inventory(changed_paths)
        for relative_path in actual_paths:
            source = (PACKAGE_ROOT / relative_path).read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                canonical_ast_sha256(source, relative_path),
                EXPECTED_PRODUCTION_AST_SHA256[relative_path],
            )
        safe_mailbox_fixture = """
            import threading
            def owner():
                return threading.current_thread()
        """
        check_source(safe_mailbox_fixture, "mailbox.py")
        with self.assertRaises(DependencyPolicyViolation):
            _require_production_ast(
                safe_mailbox_fixture,
                "mailbox.py",
            )

    def test_package_inventory_rejects_symlink_files_and_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            package_root = Path(temporary_directory) / "package"
            nested = package_root / "nested"
            nested.mkdir(parents=True)
            (package_root / "__init__.py").write_text(
                "",
                encoding="utf-8",
            )
            (nested / "module.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            self.assertEqual(
                _package_python_paths(package_root),
                ("__init__.py", "nested/module.py"),
            )

            external_file = Path(temporary_directory) / "external.py"
            external_file.write_text("VALUE = 2\n", encoding="utf-8")
            file_alias = package_root / "alias.py"
            file_alias.symlink_to(external_file)
            with self.assertRaisesRegex(
                DependencyPolicyViolation,
                r"package_symlink_forbidden:alias\.py",
            ):
                _package_python_paths(package_root)
            file_alias.unlink()

            external_directory = (
                Path(temporary_directory) / "external-package"
            )
            external_directory.mkdir()
            (external_directory / "__init__.py").write_text(
                "",
                encoding="utf-8",
            )
            directory_alias = package_root / "evilpkg"
            directory_alias.symlink_to(
                external_directory,
                target_is_directory=True,
            )
            with self.assertRaisesRegex(
                DependencyPolicyViolation,
                r"package_symlink_forbidden:evilpkg",
            ):
                _package_python_paths(package_root)

    def test_caller_injected_transport_and_order_shapes_are_rejected(self) -> None:
        cases = (
            "def run(client):\n    return client.get('https://example.invalid')",
            (
                "def run(request):\n"
                "    return request('GET', 'https://example.invalid')"
            ),
            "def fetch(value):\n    return value",
            "def run(http_client):\n    return 1",
            "class Value:\n    transport: object",
            "def run(tool):\n    operation = tool.request\n    return operation('GET', '/')",
            (
                "def run(tool):\n"
                "    operations = [tool.get]\n"
                "    return operations[0]('/')"
            ),
            (
                "def run(tool):\n"
                "    method = getattr(tool, 'request')\n"
                "    return method('GET', '/')"
            ),
            "def submit_order(value):\n    return value",
            "class Provider:\n    def cancel_order(self):\n        pass",
            "endpoint = '/orders'",
            "method = 'DELETE'",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_rejected(source)

    def test_exact_safe_data_queue_url_and_local_reader_fixtures_pass(self) -> None:
        positive = (
            (
                "adapter_contract.py",
                """
                def load_active_adapter_contract(provider, tier):
                    registry = _validated_registry_snapshot(
                        _ADAPTER_REGISTRY
                    )
                    return registry.get((provider, tier))
                """,
            ),
            (
                "entitlements.py",
                """
                def _evaluate_provider_as_of(
                    environ, qualification, name, stratum
                ):
                    if type(environ) is not dict:
                        raise TypeError
                    credential = environ.get(name)
                    evidence_by_stratum = {
                        item.stratum: item
                        for item in qualification.strata
                    }
                    evidence = evidence_by_stratum.get(stratum)
                    return credential, evidence
                """,
            ),
            (
                "events.py",
                """
                def __post_init__(self):
                    return CONTROL_RECORD_CONTRACTS.get(self.event_type)
                """,
            ),
            (
                "reducer.py",
                """
                def reduce_event(state, source_key):
                    epochs = {
                        key: value for key, value in state.source_epochs
                    }
                    return epochs.get(source_key)
                def next_trace(state, key):
                    state_epochs = {
                        item: value for item, value in state.source_epochs
                    }
                    return state_epochs.get(key, -1)
                """,
            ),
            (
                "retention.py",
                """
                import json
                def read(self, metadata, session_id):
                    try:
                        value = json.loads(metadata)
                    except ValueError:
                        return None
                    if type(value) is not dict:
                        raise TypeError
                    event_type = value.get("event_type")
                    state = self._session_states.get(session_id)
                    return event_type, state
                """,
            ),
            (
                "capture.py",
                """
                from urllib.parse import urlsplit
                def _unsafe_string(value):
                    return urlsplit(value)
                """,
            ),
            (
                "entitlements.py",
                """
                from urllib.parse import urlsplit
                def _terms_url(value):
                    return urlsplit(value)
                """,
            ),
            (
                "replay_core.py",
                """
                from .wal import JournalReader
                def replay(read_capability):
                    return JournalReader.create(read_capability=read_capability)
                """,
            ),
            (
                "ingress.py",
                """
                import queue
                def __init__(self):
                    self._queue = queue.Queue(maxsize=self._capacity)
                def enqueue(self, node):
                    self._queue.put_nowait(node)
                def _runtime_failure(self):
                    return self._queue.get_nowait()
                def drain_one(self):
                    first = self._queue.get_nowait()
                    second = self._queue.get_nowait()
                    return first, second
                """,
            ),
            (
                "mailbox.py",
                """
                import queue
                def __init__(self):
                    self._queue = queue.Queue(maxsize=1)
                def publish(self, snapshot):
                    self._queue.get_nowait()
                    self._queue.put_nowait(snapshot)
                def take(self, timeout):
                    return self._queue.get(timeout=timeout)
                """,
            ),
            (
                "mailbox.py",
                """
                import threading
                def owner():
                    return threading.current_thread()
                """,
            ),
        )
        for filename, source in positive:
            with self.subTest(filename=filename, source=source):
                check_source(source, filename)

    def test_exact_reflection_positive_fixtures_and_near_neighbors(self) -> None:
        positive = (
            (
                "fingerprints.py",
                """
                import os
                def code_sha256():
                    return (
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                    )
                """,
            ),
            (
                "capture.py",
                """
                def issue_capture_authority(session_authorizer):
                    session_manifest = session_authorizer.session_manifest
                    if (
                        type(session_manifest) is not SessionManifest
                        or not callable(
                            getattr(
                                session_authorizer,
                                "authorize_capture",
                                None,
                            )
                        )
                    ):
                        raise TypeError
                """,
            ),
            (
                "capture.py",
                """
                def validate_capture_against_authority(authority):
                    if type(authority) is not CaptureAuthority:
                        raise TypeError
                    owner = object.__getattribute__(
                        authority, "_session_authorizer"
                    )
                    return callable(getattr(owner, "authorize_capture", None))
                """,
            ),
            (
                "events.py",
                """
                class SessionManifest:
                    def __post_init__(self):
                        if type(self) is not SessionManifest:
                            raise TypeError
                        for field_name in ("raw_count",):
                            getattr(self, field_name)
                """,
            ),
            (
                "retention.py",
                """
                from dataclasses import fields
                def _marker_projection(marker):
                    if type(marker) is not RetentionMarker:
                        raise TypeError
                    return {
                        item.name: getattr(marker, item.name)
                        for item in fields(RetentionMarker)
                    }
                """,
            ),
            (
                "retention.py",
                """
                def _claim_provider_wal_writer(write_capability):
                    if type(write_capability) is not ProviderWalWriteCapability:
                        raise TypeError
                    return object.__getattribute__(
                        write_capability, "_dispatch"
                    )
                """,
            ),
        )
        for filename, source in positive:
            with self.subTest(filename=filename):
                check_source(source, filename)

        malicious = (
            (
                "capture.py",
                """
                def issue_capture_authority(session_authorizer):
                    reflected = getattr(
                        session_authorizer, "authorize_capture", None
                    )
                    return callable(reflected)
                """,
            ),
            (
                "fingerprints.py",
                """
                import os
                def code_sha256():
                    return getattr(os, "system", None)
                """,
            ),
            (
                "events.py",
                """
                def __post_init__(self, attribute):
                    return getattr(self, attribute)
                """,
            ),
            (
                "retention.py",
                """
                from dataclasses import fields
                def _marker_projection(marker):
                    return fields(marker)
                """,
            ),
            (
                "retention.py",
                """
                def _claim_provider_wal_writer(write_capability):
                    return object.__getattribute__(
                        write_capability, "_other"
                    )
                """,
            ),
            (
                "entitlements.py",
                "value = ProviderQuotas.__dict__",
            ),
            (
                "capture.py",
                """
                def issue_capture_authority(session_authorizer):
                    first = callable(
                        getattr(session_authorizer, "authorize_capture", None)
                    )
                    second = callable(
                        getattr(session_authorizer, "authorize_capture", None)
                    )
                    return first and second
                """,
            ),
        )
        for filename, source in malicious:
            with self.subTest(filename=filename, source=source):
                self.assert_rejected(source, filename=filename)

    def test_queue_and_url_near_neighbor_fixtures_are_rejected(self) -> None:
        cases = (
            ("mailbox.py", "import queue\nvalue = queue.SimpleQueue()"),
            ("mailbox.py", "import queue\nvalue = queue.Queue(maxsize=0)"),
            (
                "mailbox.py",
                "def publish(self, snapshot):\n    self._queue.put(snapshot)",
            ),
            (
                "mailbox.py",
                "def publish(self, snapshot):\n    queue.Queue.put(self._queue, snapshot)",
            ),
            (
                "mailbox.py",
                "def publish(self, snapshot):\n    self._queue.put_nowait(*snapshot)",
            ),
            (
                "mailbox.py",
                "def put(self, value):\n    return value",
            ),
            (
                "ingress.py",
                "def enqueue(self, node):\n    self._queue.put_nowait(other)",
            ),
            (
                "capture.py",
                "from urllib.parse import urlparse",
            ),
            (
                "capture.py",
                "from urllib.parse import urlsplit\nparser = urlsplit",
            ),
            (
                "capture.py",
                "from urllib.parse import urlsplit\nurlsplit = other",
            ),
        )
        for filename, source in cases:
            with self.subTest(filename=filename, source=source):
                self.assert_rejected(source, filename=filename)

    def test_task6_retention_bridge_exceptions_remain_exact(self) -> None:
        cases = (
            """
                def _consume_expert_state_root_account_lock_request(request):
                    if type(request) is not object:
                        raise TypeError
                    return object.__getattribute__(request, '_dispatch')
            """,
            """
                import os
                def _validate_expert_root_binding(self, authority):
                    original_values = (
                        os.fstat(self._state_fd),
                        os.fstat(self._sessions_fd),
                        os.fstat(self._markers_fd),
                    )
                    return original_values
            """,
            """
                def _consume_expert_state_root_account_lock_request(
                    self,
                    request_authority,
                    request,
                ):
                    if type(request_authority) is not object:
                        raise TypeError
                    return request_authority.request
            """,
            """
                def _validate_expert_root_binding(self, authority):
                    if type(authority) is not object:
                        raise TypeError
                    return object.__getattribute__(
                        authority.grant,
                        '_state_fd',
                    )
            """,
            """
                def _revoke_expert_state_root_account_lock_grant(grant):
                    if type(grant) is not object:
                        raise TypeError
                    return object.__getattribute__(grant, '_dispatch')
            """,
            """
                def sample_expert_retention_wall_ns(capability):
                    if type(capability) is not object:
                        raise TypeError
                    return object.__getattribute__(
                        capability,
                        '_dispatch',
                    )
            """,
            """
                def _validate_expert_root_authority_locked(
                    self,
                    authority,
                    capability,
                ):
                    if type(authority) is not _ExpertRootGrantAuthority:
                        raise TypeError
                    return object.__getattribute__(
                        capability,
                        '_dispatch',
                    )
            """,
            """
                import os
                def wrong_function(self, authority):
                    original_values = (
                        os.fstat(self._state_fd),
                        os.fstat(self._sessions_fd),
                        os.fstat(self._markers_fd),
                        os.fstat(self._lock_fd),
                    )
                    return original_values
            """,
            """
                import os
                def _validate_expert_root_binding(self, authority):
                    wrong_values = (
                        os.fstat(self._state_fd),
                        os.fstat(self._sessions_fd),
                        os.fstat(self._markers_fd),
                        os.fstat(self._lock_fd),
                    )
                    return wrong_values
            """,
            """
                def wrong_function(request_authority):
                    if type(request_authority) is not _ExpertRootRequestAuthority:
                        raise TypeError
                    return request_authority.request
            """,
            """
                def _validate_expert_root_authority_locked(
                    self,
                    authority,
                    capability,
                    attacker,
                ):
                    self._expert_root_grants = attacker
                    return self._expert_root_grants.get(authority.grant)
            """,
            """
                def _sample_expert_retention_wall_ns(
                    self,
                    capability,
                    attacker,
                ):
                    self._expert_clock_capabilities = attacker
                    return self._expert_clock_capabilities.get(capability)
            """,
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_rejected(source, filename="retention.py")

        ingress_cases = (
            """
                def close_external_halt(self, runtime):
                    return runtime.close_halted('operator_halt')
            """,
            """
                def close_external_halt(self, runtime):
                    if type(runtime) is not EventRuntime:
                        raise TypeError
                    return runtime.close_halted('different_reason')
            """,
            """
                def wrong_function(self):
                    return self._queue.get_nowait()
            """,
        )
        for source in ingress_cases:
            with self.subTest(source=source):
                self.assert_rejected(source, filename="ingress.py")

    def test_task7_documentation_owned_sections_are_present_and_explicit(self) -> None:
        path = REPO_ROOT / "docs" / "tennis_v1" / "README.md"
        text = path.read_text(encoding="utf-8")
        required_headings = (
            "## Tennis v1 Phase 1 Research-Only Boundary",
            "## WAL Is Canonical Tennis Input Evidence",
            "## Why v6 CSV and Replay Stay Separate",
            "## Diagnostic Scan, Exact Replay, and Research Evaluability",
            "## No Live, Demo, or Provider-Network Runtime",
            "## Reviewed Canonical AST Closure",
        )
        for heading in required_headings:
            with self.subTest(heading=heading):
                self.assertIn(heading, text)
        lowered = text.lower()
        self.assertIn("wal", lowered)
        self.assertIn("canonical", lowered)
        self.assertIn("diagnostic", lowered)
        self.assertIn("exact replay", lowered)
        self.assertIn("research_evaluable=false", lowered)
        self.assertIn("no provider network", lowered)
        self.assertIn("no live", lowered)
        self.assertIn("no demo", lowered)
        self.assertIn("cpython 3.14.5", lowered)
        self.assertIn("task 5", lowered)
        self.assertIn("explicit re-review", lowered)


if __name__ == "__main__":
    unittest.main()
