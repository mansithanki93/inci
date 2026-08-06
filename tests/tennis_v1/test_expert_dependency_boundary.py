from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import textwrap
import tomllib
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PHASE_ONE_ROOT = REPOSITORY_ROOT / "tennis_v1"

PACKAGE_ROOTS = {
    "inci_tennis_expert": REPOSITORY_ROOT / "inci_tennis_expert",
    "inci_tennis_io": REPOSITORY_ROOT / "inci_tennis_io",
    "inci_tennis_adapters": REPOSITORY_ROOT / "inci_tennis_adapters",
    "inci_tennis_runtime": REPOSITORY_ROOT / "inci_tennis_runtime",
}

EMPTY_MODULE_AST_SHA256 = (
    "ad2e13b69c4fc1fda46413f740f426fc1793cfc6d6da8d226ba619f1aef48be7"
)
EXPECTED_PACKAGE_AST_SHA256 = {
    "inci_tennis_expert": {
        "__init__.py": EMPTY_MODULE_AST_SHA256,
        "contracts.py": (
            "574860701427c3bce00af9f17d3bb58bcc94086678cf45d58643fc98f55426ce"
        ),
        "fee_schedule.py": (
            "4b338cd9f276ef25a3fc7991acae4f4e6e3bde7ae36c9b398ae864381cd73192"
        ),
        "facade.py": (
            "cf6fc820a31443e85254fa9d3bc3534c7e935b025af48a21948478e5fb277e4d"
        ),
        "consensus_l2_research.py": (
            "9971f3407fdb56a6fcb6f833293b589ee4ecf9f5192a07024eb753ab936ae5d3"
        ),
        "first_set_model.py": (
            "997d7d9e9e108e8dcda861f2b5dea332a982a85c52735d074e01c4982323e768"
        ),
        "five_minute_path.py": (
            "d137e7cbb02ff649e18cd83829c1f5fa5e1a9e831e1310989290495d2cbad250"
        ),
        "journal_codec.py": (
            "516fe36207012faf4a3853fe69d42c68ea6d775fd7e06624b273e181199c7102"
        ),
        "market_book.py": (
            "68ccc3d0fbda2a69c87b64bdc348e791f9f318fc0dfa7685e97afac0657682fd"
        ),
        "match_binding.py": (
            "1240813fe2d8809fc9fd9c546ac168f09524e677d5a0732d944e2dcd9e28b87c"
        ),
        "observation.py": (
            "e1b821d8b385220b5516831a221fd1b5df311035bc8fcde395bcc8db8ddee2b3"
        ),
        "paper_policy.py": (
            "c4aca5fefb3f41d0b8994525d56b37c3775c917e552ae8b5ea280d2e78c227b7"
        ),
        "prematch_model.py": (
            "e9ab14e6f99650ede2e0832d4a3252244fd77e9a3a01ac026392d77b55759b5f"
        ),
        "reducer.py": (
            "a604a502f315c189736f7e77ba7ee1560de38b5b6a42f5f1ae75664db644f0c7"
        ),
        "replay.py": (
            "689c39076f95e80d3992b8fa01594416898c524ce41c85e73eb65874e14c8bef"
        ),
        "risk.py": (
            "e96fa93873227400ebd175c75d4707012507678c207026446e94245b59bd5cee"
        ),
        "score_consensus.py": (
            "3494588d794dfa37798b4149aea198860cd799a76dd1eb9405f701e5d651a686"
        ),
        "state.py": (
            "004651dcd24eb2c116715393aeba5844e23f80997cf20ce96f2cdde5a6e38dea"
        ),
        "strategy_artifacts.py": (
            "4146c098cdde369a844d1883b9c356c1f42947aa1a7568286e8c07fef42ab9ab"
        ),
        "tennis_score.py": (
            "40d24bee509fae0560a5b3e815f2ace84de7a06facfa23469eb885a13a7e9f70"
        ),
        "synchronizer.py": (
            "51251b6e751309d71759b9eb7aa2c2617b1a1acc6d3199326a0a4c02fb725b33"
        ),
        "task6_fallback_normalizer.py": (
            "2629e1752c789478201da6b3dd291dfdb6dc46614998e2ab8115d47f676b3b41"
        ),
        "win_probability.py": (
            "3c665bc5b025a375e28ffa72116d940547e34f34e9d5531445a73b5951c2985f"
        ),
    },
    "inci_tennis_io": {
        "__init__.py": EMPTY_MODULE_AST_SHA256,
        "account_lock.py": (
            "6752e3b873f0e18b6226c0c664aaa49263c30b4c925019ebcebb87ce893d6e5c"
        ),
        "expert_journal_store.py": (
            "37b31d9a4fe593b6f8cbb8e092f52593ba34504c911a22f83eb71baad4479faa"
        ),
        "facade.py": (
            "696c8410e7ac164b86d819edb4277bdf4468d4b93a1703e494e4130faf80dcc0"
        ),
        "kalshi_readonly.py": (
            "dcfad924e5a8a89c2e666eb048284ee57e5258b7c903b17483480f994954a532"
        ),
        "kalshi_shadow_catalog.py": (
            "d8a938f19b20ed403c5a663b06bb27341568fdd9fcd45bee27c9bbd9582a25c5"
        ),
        "kalshi_shadow_settlement.py": (
            "d08a3c297aa901a98b47f57c15f29259475bb1395b9879f07b2d7d80f33462e3"
        ),
        "pinned_artifacts.py": (
            "43feef97b2d9a37f00143f00f6272562a64881da5cf143c060eb77c8c058ca0f"
        ),
        "ports.py": (
            "654d49b2fe90447435ca99b0a6a93a9205853299e93f29ff474b4910623ec23b"
        ),
        "provider_readonly.py": (
            "6dde0bf459b9106182f94ca3bab6340d7aae7a3b1ad8f48b644e7739e6d28487"
        ),
        "shadow_evidence.py": (
            "a40ffa9112b9915650c5f69045dc097d86714142c744611d61633eeffb122c3c"
        ),
        "shadow_settlement_labels.py": (
            "01a0342cb6498826b1f1f70f43b8f1b010e6ef5f14ae3403f716c684ca164ba2"
        ),
        "sportradar_shadow_async.py": (
            "44a9dc48a58fd2efdb3990c0a3a4c933f7bf20b3ecea78115f47f8f49daa3b53"
        ),
        "sportradar_trial_transport.py": (
            "75497ff641e9e718f8ac965e43d9760c825f2ee4ca7001bdc5b09a3130c6b044"
        ),
    },
    "inci_tennis_adapters": {
        "__init__.py": EMPTY_MODULE_AST_SHA256,
        "candidate_contracts.py": (
            "cbf31a3fa855c5bf08db539b8c1052a715de236d3bad3d818b44b50230403ac0"
        ),
        "kalshi_candidate.py": (
            "17747eaa104dc1cfb18e8f57848372624d8b16ba04e6070bf684a7443b410b54"
        ),
        "kalshi_v2.py": (
            "7aed340884f6e3b64df20051fc2c52de8a3fb2ed290749889b2d9979a16bccf4"
        ),
        "live_score_candidates.py": (
            "ebe36f50a07189a918769c48057415b5cefcbc5df2cf8d77d59462c290bfb76d"
        ),
        "registry.py": (
            "14e534fd3762d5d6198b1de8762568497bcf1c807fd56177942dab8f4de59229"
        ),
        "shadow_discovery_contracts.py": (
            "6df84030c62fc1d7b9336cb7430f1f84c0ee4f3fe8eae25d898c040f55f6eff9"
        ),
        "shadow_match_chooser.py": (
            "ec75919e93698402ad57b057e4f85fc47ec5ce5d5902860fc4a84a6c53f7b665"
        ),
        "shadow_provider_coverage.py": (
            "0d56b4759aec2660949fc4a63666059b77686e9131b063b6eba9bc53604ec111"
        ),
        "sportradar_tennis_v3.py": (
            "7a0757de48b66bd28f0547f2574216a2408a88b70fdf2e259776e067b6aa261f"
        ),
        "sportradar_trial_v3.py": (
            "f8977e0577f5ac2406b67944ec81e3d0b0a8fc0623fee5b1dd430736db206b39"
        ),
    },
    "inci_tennis_runtime": {
        "__init__.py": EMPTY_MODULE_AST_SHA256,
        "expert_controller.py": (
            "c8f094d2381b04015edffbb1248b727319694497fc1672195e5bb843ac96cf42"
        ),
        "live_price_only_collector.py": (
            "84685fa90fcbf79c7c1c845c33f57636a08d7f7c516297747f9ec3dc5b50e7c2"
        ),
        "live_shadow_cli.py": (
            "dc1be937bf96a2b13ac25dfda091faedd3a28e73d9e30e7923ca6a2d0bd497c7"
        ),
        "live_shadow_collector.py": (
            "55410dcc6262d46191a3df52785a076e654cda72f72637655953a9dbb1ea659b"
        ),
        "provider_qualification_controller.py": (
            "94be825905f29c252c1d4d32f4544e6e1084661b099255b4d0cfbee9e02d6109"
        ),
        "replay_service.py": (
            "fd37e740069fc1b79946a2f7026acdc8c7a7f99983fa721772aec06e27c35a97"
        ),
        "shadow_cli.py": (
            "a46e9d8a8dc69aca6b255b3fbbb9ccb321a7226093f8a979a7e3354010890503"
        ),
        "shadow_runtime.py": (
            "611cfb436eedf88fd6ea364983b66ec2073247d449272fb7d0726fa96053cd1e"
        ),
        "shadow_settlement_cli.py": (
            "05823c93da9f70426230fcea0eb7cd98b969539c79c135190e6e7e3069a60059"
        ),
        "sportradar_trial_cli.py": (
            "afbc1d87c7e42295b942cd7737b7d70eeef5a909c8e9d41e29f3563a55c0e619"
        ),
    },
}

LIVE_PAPER_MODULE_AST_SHA256 = {
    "live_paper_contracts.py": (
        "d53991f2ec7134dc0de6294ebdffe1758d376d3495ad09aafe1f216cb739ac8f"
    ),
    "live_paper_execution.py": (
        "e0ab250383818b2d243e2d86e495fb96a88b969b2c1d4167f17c1542485702a9"
    ),
    "live_paper_score.py": (
        "6fefcc8627ae6fa64cf50865699b31077c221bb8f95e105cf52300b010f346b0"
    ),
    "live_paper_session.py": (
        "b76fd5b094e143e262ac588c0f2aad16092ef3313324a7a7214bcc1322bbe9aa"
    ),
    "live_two_model.py": (
        "f4728f2b2aa03bcf955cfe061ea840da45eb8bd3fc20b569d881e053e3afef92"
    ),
}

EXPECTED_PACKAGE_RESOURCE_SHA256 = {
    "inci_tennis_expert": {
        "schemas/binding-review-v1.schema.json": (
            "1ed2ea66f5ef2b9814676860f1afe5e8fa6411ba8ef91bc9e728b4aa2ebda453"
        ),
        "schemas/expert-journal-group-v1.schema.json": (
            "004280f5ba8225ed514eb8a336407b2238e7840656923b523ee2d440794547c8"
        ),
        "schemas/expert-journal-record-v1.schema.json": (
            "56b9f95e346bda2ee78fe6df4a57382dad1f948d1bf97acf21e65ba8d6aaa63e"
        ),
        "schemas/match-binding-v1.schema.json": (
            "73dcc7c55623449c9b64582a84cbe0baf7817137649f5e3eb7a9adfb6bbd5f70"
        ),
        "schemas/expert-observation-ignored-v1.schema.json": (
            "033edd37c85bb05c8defcd5a2af649246572b8fd48682f80207854da7b2f1f8a"
        ),
        "schemas/expert-observation-rejected-v1.schema.json": (
            "8343568ac80eb5072404c2eac50bfe15412490a089c116bcc526ad23058a7137"
        ),
        "schemas/expert-session-manifest-v1.schema.json": (
            "78630398df4167ac89b12f96c247646b273717e1bfceec78085ad3026f47d17c"
        ),
        "schemas/expert-session-terminal-v1.schema.json": (
            "395a1226de161b78e59e79a3ecc04d4de7f2958b71761f7319de0a477b180bc3"
        ),
        "schemas/expert-synchronization-applied-v1.schema.json": (
            "da8edb60c720eccc69afe94be49a8a4350770671f2375b30b0304753eacd574f"
        ),
        "schemas/task6-fallback-no-payload-v1.schema.json": (
            "2ed27c1421e6928dbe13dbfdb5c59e1045b30341fe7ebe05700006bc5ac572c0"
        ),
    },
    "inci_tennis_io": {},
    "inci_tennis_adapters": {
        (
            "schemas/"
            "kalshi-market-lifecycle-synthetic-candidate-v1.schema.json"
        ): (
            "d6860722bb4712f65ca030bc6fef7fb7c950469dee8c07302b141e77d9890450"
        ),
        (
            "schemas/"
            "kalshi-orderbook-delta-synthetic-candidate-v1.schema.json"
        ): (
            "66ce507d63cc7d99327e90ab08bb7ecb602c0f8f3a2a265400b5862e8b00d136"
        ),
        (
            "schemas/"
            "kalshi-orderbook-snapshot-synthetic-candidate-v1.schema.json"
        ): (
            "7a5ae1731f2a0160d6fc3cc4abddbd8c796325732272b5bd8b1bc132936e6ee0"
        ),
        (
            "schemas/"
            "kalshi-public-trade-synthetic-candidate-v1.schema.json"
        ): (
            "b346a6c981f9b3b81d144f43ab6a19d9782fd0d102dfac147c0b18575c18c0db"
        ),
        (
            "schemas/"
            "sportradar-tennis-candidate-authorization-v1.schema.json"
        ): (
            "65da19a583a4d952d9408b66394f7e0a329c0850a10b20ac8e542b8cedc4352a"
        ),
        "schemas/sportradar-tennis-candidate-manifest-v1.schema.json": (
            "c6c76de6f317b8e247b92cd4dafb36e4b51a58b41cf57deb9779d86138124241"
        ),
        "schemas/sportradar-tennis-qualification-output-v1.schema.json": (
            "f029b2424a3d067fca261565e366a0c974412c3d4c5d226716f5f054816000f6"
        ),
        (
            "schemas/"
            "sportradar-tennis-summary-v3-candidate-v1.schema.json"
        ): (
            "a8c88191b02c64b5505eb906838cf75e541b5d86653b9e5a447263f057432b89"
        ),
        (
            "schemas/"
            "sportradar-tennis-timeline-v3-candidate-v1.schema.json"
        ): (
            "54f9d2ebbc0c0e31998abe7ca0928cd6ea30f5b4b410019763f778acb9a4918f"
        ),
        "schemas/sportradar-tennis-transport-error-v1.schema.json": (
            "af8854340f2b9021572134f6211c68d9bcf590ba45b09c20c797fcc479f4a002"
        ),
    },
    "inci_tennis_runtime": {},
}

NEW_PACKAGE_ROOTS = frozenset(PACKAGE_ROOTS)
ROOT_V6_PYTHON_PATHS = (
    "analyze.py",
    "bot.py",
    "config.py",
    "engine.py",
    "executor.py",
    "fees.py",
    "kalshi_client.py",
    "market_data.py",
    "order_journal.py",
    "order_resolution.py",
    "pnl_ledger.py",
    "process_lock.py",
    "replay.py",
    "research_log.py",
    "safety.py",
    "schemas.py",
    "signals.py",
    "sports_discovery.py",
    "strategy.py",
    "tests.py",
)

EXPECTED_REQUIREMENTS_SHA256 = (
    "3183d3dbb70c4404620ec2be38bc39275f94ebcbaac2e3eeab8daff0d4875f53"
)

EXPERT_PHASE_ONE_IMPORTS = frozenset(
    {
        "tennis_v1.events.CapturedInput",
        "tennis_v1.events.PersistedEvent",
        "tennis_v1.replay_core.ReplayResult",
    }
)
SEALED_EXPERT_PHASE_ONE_IMPORTS = frozenset(
    {
        "tennis_v1.canonical.canonical_json_bytes",
        "tennis_v1.codec.canonical_record_sha256",
        "tennis_v1.events.PersistedEvent",
        "tennis_v1.events.ProvenanceState",
        "tennis_v1.events.RecordKind",
        "tennis_v1.events.SessionManifest",
        "tennis_v1.events.SourceKind",
        "tennis_v1.replay_core.ReplayMismatch",
        "tennis_v1.replay_core.ReplayResult",
        "tennis_v1.session.canonical_session_manifest_bytes",
        "tennis_v1.session.session_manifest_sha256",
        "tennis_v1.state.FoundationState",
        "tennis_v1.state.canonical_state_bytes",
        "tennis_v1.wal.ScanIssue",
    }
)
RUNTIME_PHASE_ONE_IMPORTS = frozenset(
    {
        *EXPERT_PHASE_ONE_IMPORTS,
        "tennis_v1.replay_core.replay_exact",
        "tennis_v1.sequencer.EventRuntime",
    }
)
SEALED_RUNTIME_PHASE_ONE_IMPORTS = frozenset(
    {
        "tennis_v1.codec.canonical_record_sha256",
        "tennis_v1.events.PersistedEvent",
        "tennis_v1.events.ProvenanceState",
        "tennis_v1.events.RecordKind",
        "tennis_v1.events.SessionManifest",
        "tennis_v1.events.SourceKind",
        "tennis_v1.ingress.BoundedIngress",
        "tennis_v1.ingress.DeferredEmergencyCommitSubjectV1",
        "tennis_v1.ingress.DurableCausalPrecedesProofV1",
        "tennis_v1.ingress.DurableEvidenceTerminalV1",
        "tennis_v1.ingress.DurableIngressParentV1",
        (
            "tennis_v1.ingress."
            "_abort_deferred_emergency_commit_subject_v1"
        ),
        "tennis_v1.ingress._bind_durable_ingress_consumer_v1",
        (
            "tennis_v1.ingress."
            "_close_durable_causal_precedes_proof_after_deferred_append_failure_v1"
        ),
        (
            "tennis_v1.ingress."
            "_commit_prepared_durable_causal_precedes_proof_v1"
        ),
        "tennis_v1.ingress._consume_durable_evidence_terminal_v1",
        "tennis_v1.ingress._consume_durable_ingress_parent_v1",
        (
            "tennis_v1.ingress."
            "_converge_durable_causal_precedes_proof_after_deferred_append_failure_v1"
        ),
        (
            "tennis_v1.ingress."
            "_issue_deferred_emergency_commit_subject_v1"
        ),
        (
            "tennis_v1.ingress."
            "_lookup_prepared_deferred_emergency_causal_proof_commit_for_proof_v1"
        ),
        (
            "tennis_v1.ingress."
            "_prepare_durable_causal_precedes_proof_commit_v1"
        ),
        (
            "tennis_v1.ingress."
            "_validate_durable_evidence_terminal_for_consumer_v1"
        ),
        (
            "tennis_v1.ingress."
            "_validate_durable_ingress_parent_for_consumer_v1"
        ),
        "tennis_v1.retention.RetentionCoordinator",
        "tennis_v1.sequencer.EventRuntime",
        "tennis_v1.sequencer.ProviderPersistenceAuthorizer",
        "tennis_v1.sequencer.WrongOwnerThread",
        "tennis_v1.session.canonical_session_manifest_bytes",
        "tennis_v1.session.session_manifest_sha256",
        "tennis_v1.state.initial_state",
    }
)
SEALED_RUNTIME_STDLIB_AUTHORITY_IMPORTS = frozenset(
    {
        "hashlib.sha256",
        "os.getpid",
        "threading.RLock",
        "threading.current_thread",
    }
)
SEALED_RUNTIME_FILE_STDLIB_AUTHORITY_IMPORTS = {
    "sportradar_trial_cli.py": frozenset({"signal"}),
    "live_shadow_cli.py": frozenset({"os", "signal"}),
    "shadow_settlement_cli.py": frozenset({"pathlib.Path", "time"}),
}
SEALED_RUNTIME_FILE_CLOCK_CALLS = {
    "live_shadow_cli.py": frozenset({"monotonic_ns", "wall_ns"}),
    "shadow_settlement_cli.py": frozenset({"monotonic_ns", "time_ns"}),
}
IO_PHASE_ONE_IMPORTS = frozenset(
    {
        "tennis_v1.adapter_contract.load_active_adapter_contract",
        "tennis_v1.capture.validate_captured_input",
        "tennis_v1.codec.canonical_json_bytes",
        "tennis_v1.codec.canonical_record_sha256",
        "tennis_v1.codec.decode_record",
        "tennis_v1.events.PersistedEvent",
        "tennis_v1.events.RecordKind",
        "tennis_v1.events.SessionManifest",
        "tennis_v1.fingerprints.CODE_FINGERPRINT_DOMAIN",
        "tennis_v1.pinned_file.PinnedBytes",
        "tennis_v1.pinned_file.PinnedFileError",
        "tennis_v1.pinned_file.read_pinned_file",
        "tennis_v1.replay_core.replay_exact",
        "tennis_v1.retention.ExpertStateRootAccountLockRequestV1",
        "tennis_v1.retention.RetentionCoordinator",
        "tennis_v1.retention.RetentionDueDeleteError",
        "tennis_v1.retention.RetentionError",
        "tennis_v1.retention.RetentionMarker",
        "tennis_v1.retention._consume_expert_state_root_account_lock_request",
        "tennis_v1.retention._revoke_expert_state_root_account_lock_grant",
        "tennis_v1.retention.sample_expert_retention_wall_ns",
        "tennis_v1.sequencer.ProviderPersistenceAuthorizer",
        "tennis_v1.session.session_manifest_sha256",
        "tennis_v1.wal.JournalReader",
    }
)
IO_EXPERT_IMPLEMENTATION_IMPORTS = frozenset(
    {
        "inci_tennis_expert.facade.begin_expert_replay",
        "inci_tennis_expert.facade.finish_expert_replay",
        "inci_tennis_expert.facade.replay_expert_parent_group",
        "inci_tennis_expert.journal_codec.EXPERT_EMERGENCY_RESERVE_BYTES",
        "inci_tennis_expert.journal_codec.EXPERT_FILE_HEADER_BYTES",
        "inci_tennis_expert.journal_codec.EXPERT_FRAME_DIGEST_DOMAIN",
        "inci_tennis_expert.journal_codec.EXPERT_FRAME_KIND_PARENT_GROUP",
        "inci_tennis_expert.journal_codec.EXPERT_FRAME_KIND_TERMINAL",
        "inci_tennis_expert.journal_codec.EXPERT_FRAME_PREFIX_BYTES",
        "inci_tennis_expert.journal_codec.EXPERT_FRAME_TRAILER_BYTES",
        "inci_tennis_expert.journal_codec.EXPERT_MIN_FREE_BYTES",
        "inci_tennis_expert.journal_codec.MAX_EXPERT_EVENT_PAYLOAD_BYTES",
        "inci_tennis_expert.journal_codec.MAX_EXPERT_FRAME_BYTES",
        "inci_tennis_expert.journal_codec.MAX_EXPERT_OUTCOMES_PER_PARENT",
        "inci_tennis_expert.journal_codec.MAX_EXPERT_TERMINAL_FRAME_BYTES",
        "inci_tennis_expert.journal_codec.decode_expert_complete_frame",
        "inci_tennis_expert.journal_codec.decode_expert_file_header",
        "inci_tennis_expert.journal_codec.decode_expert_frame_prefix",
        "inci_tennis_expert.journal_codec.decode_expert_group_frame_structural",
        "inci_tennis_expert.journal_codec.decode_expert_manifest_frame",
        "inci_tennis_expert.journal_codec.decode_expert_terminal_frame_replay_material",
        "inci_tennis_expert.journal_codec.decode_expert_terminal_frame_structural",
        "inci_tennis_expert.journal_codec.encode_expert_file_header",
        "inci_tennis_expert.journal_codec.encode_expert_group_frame",
        "inci_tennis_expert.journal_codec.encode_expert_manifest_frame",
        "inci_tennis_expert.journal_codec.encode_expert_terminal_frame",
        "inci_tennis_expert.journal_codec.validate_expert_frame_parts",
        "inci_tennis_expert.journal_codec.validate_expert_group_against_cursor",
        "inci_tennis_expert.journal_codec.validate_expert_group_metadata_diagnostic",
        "inci_tennis_expert.journal_codec.validate_expert_streamed_frame_trailer",
        "inci_tennis_expert.journal_codec.validate_expert_terminal_against_cursor",
    }
)

TASK7_TOOL_RELATIVE_PATH = "tools/qualify_sportradar_tennis_v3.py"
EXPECTED_TASK7_TOOL_AST_SHA256 = (
    "0953d95ade1109d52b6fd0f691fa7cb5b9e5f23905221b2af9293e7452dcd289"
)
TASK7_TOOL_REPOSITORY_IMPORTS = frozenset(
    {
        (
            "inci_tennis_io.provider_readonly."
            "CandidateOfflineValidationError"
        ),
        (
            "inci_tennis_io.provider_readonly."
            "sportradar_candidate_offline_is_eligible"
        ),
        (
            "inci_tennis_io.provider_readonly."
            "validate_sportradar_candidate_offline_artifacts"
        ),
    }
)
TASK7_IO_PHASE_ONE_IMPORTS = {
    "account_lock.py": frozenset(
        {"tennis_v1.canonical.canonical_json_bytes"}
    ),
    "expert_journal_store.py": frozenset(
        {"tennis_v1.events.CapturedInput"}
    ),
    "provider_readonly.py": frozenset(
        {
            "tennis_v1.adapter_contract.AdapterUsagePlan",
            "tennis_v1.adapter_contract.ProviderQuotas",
            "tennis_v1.canonical.canonical_json_bytes",
            "tennis_v1.capture.issue_capture_authority",
            "tennis_v1.capture.safe_provenance",
            "tennis_v1.entitlements.CoverageStratum",
            "tennis_v1.entitlements.QualificationReason",
            (
                "tennis_v1.entitlements."
                "REQUIRED_QUALIFICATION_CAPABILITIES"
            ),
            "tennis_v1.entitlements.RequestedStratum",
            "tennis_v1.events.CaptureAuthority",
            "tennis_v1.events.CapturedInput",
            "tennis_v1.events.SessionCaptureAuthorizer",
            "tennis_v1.events.SessionManifest",
            "tennis_v1.events.SourceKind",
            "tennis_v1.session.session_manifest_sha256",
        }
    ),
}
TASK7_IO_ADAPTER_IMPORTS = {
    "expert_journal_store.py": frozenset({"inci_tennis_adapters"}),
    "kalshi_shadow_catalog.py": frozenset(
        {
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "KalshiCatalogExclusion"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "KalshiCompetitionProvenance"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "KalshiShadowCatalogSnapshot"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "KalshiShadowGame"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "KalshiShadowMarket"
            ),
            (
                "inci_tennis_adapters.shadow_match_chooser."
                "KalshiShadowGame"
            ),
            (
                "inci_tennis_adapters.shadow_match_chooser."
                "KalshiShadowMarket"
            ),
            (
                "inci_tennis_adapters.shadow_match_chooser."
                "normalize_player_name"
            ),
        }
    ),
    "provider_readonly.py": frozenset(
        {
            (
                "inci_tennis_adapters.candidate_contracts."
                "CandidateProviderBindingV1"
            ),
            (
                "inci_tennis_adapters.candidate_contracts."
                "CandidateQualificationDecisionV1"
            ),
            (
                "inci_tennis_adapters.candidate_contracts."
                "CandidateQuotaClosureV1"
            ),
        }
    )
}
TASK7_IO_EXPERT_IMPLEMENTATION_IMPORTS = {
    "provider_readonly.py": frozenset(
        {"inci_tennis_expert.match_binding.decode_binding_universe"}
    )
}
TASK7_ADAPTER_PHASE_ONE_IMPORTS = {
    "candidate_contracts.py": frozenset(
        {
            "tennis_v1.adapter_contract.AdapterUsagePlan",
            "tennis_v1.adapter_contract.ProviderQuotas",
            "tennis_v1.entitlements.QualificationReason",
        }
    ),
    "kalshi_candidate.py": frozenset(
        {"tennis_v1.canonical.canonical_json_bytes"}
    ),
    "registry.py": frozenset(
        {
            "tennis_v1.entitlements.QualifiedProviderBinding",
            "tennis_v1.events.CapturedInput",
            "tennis_v1.events.PersistedEvent",
            "tennis_v1.events.SourceKind",
        }
    ),
    "sportradar_tennis_v3.py": frozenset(
        {
            "tennis_v1.canonical.canonical_json_bytes",
            "tennis_v1.codec.canonical_record_sha256",
            "tennis_v1.entitlements.QualifiedProviderBinding",
            "tennis_v1.events.CapturedInput",
            "tennis_v1.events.PersistedEvent",
            "tennis_v1.events.ProvenanceState",
            "tennis_v1.events.RecordKind",
            "tennis_v1.events.SourceKind",
        }
    ),
}
TASK7_ADAPTER_PEER_IMPORTS = {
    "registry.py": frozenset(
        {
            (
                "inci_tennis_adapters.sportradar_tennis_v3."
                "SportradarTennisV3CandidateError"
            ),
            (
                "inci_tennis_adapters.sportradar_tennis_v3."
                "bind_sportradar_tennis_v3_event"
            ),
            (
                "inci_tennis_adapters.sportradar_tennis_v3."
                "validate_sportradar_tennis_v3_prior"
            ),
            (
                "inci_tennis_adapters.sportradar_tennis_v3."
                "validate_sportradar_tennis_v3_transport_error"
            ),
        }
    ),
    "shadow_discovery_contracts.py": frozenset(
        {
            (
                "inci_tennis_adapters.sportradar_trial_v3."
                "SportradarCompetitionProvenance"
            ),
        }
    ),
    "shadow_match_chooser.py": frozenset(
        {
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "HybridChooserSnapshot"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "HybridMatchRow"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "HybridStatus"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "KalshiCompetitionProvenance"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "KalshiShadowCatalogSnapshot"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "KalshiShadowGame"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "KalshiShadowMarket"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "ProviderDiscoveryState"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "ProviderMatchRef"
            ),
            (
                "inci_tennis_adapters.shadow_provider_coverage."
                "assess_provider_route"
            ),
            (
                "inci_tennis_adapters.shadow_provider_coverage."
                "coverage_registry_sha256"
            ),
            (
                "inci_tennis_adapters.sportradar_trial_v3."
                "SportradarCompetitionProvenance"
            ),
            (
                "inci_tennis_adapters.sportradar_trial_v3."
                "SportradarHybridDiagnostic"
            ),
            (
                "inci_tennis_adapters.sportradar_trial_v3."
                "SportradarHybridDiscoverySnapshot"
            ),
            (
                "inci_tennis_adapters.sportradar_trial_v3."
                "SportradarHybridMatch"
            ),
            (
                "inci_tennis_adapters.sportradar_trial_v3."
                "SportradarLiveSummariesSnapshot"
            ),
            (
                "inci_tennis_adapters.sportradar_trial_v3."
                "SportradarScoreSnapshot"
            ),
        }
    ),
    "shadow_provider_coverage.py": frozenset(
        {
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "KalshiShadowGame"
            ),
            (
                "inci_tennis_adapters.sportradar_trial_v3."
                "SportradarCompetitionProvenance"
            ),
        }
    ),
    "sportradar_tennis_v3.py": frozenset(
        {
            (
                "inci_tennis_adapters.candidate_contracts."
                "CandidateParserEvidenceV1"
            ),
            (
                "inci_tennis_adapters.candidate_contracts."
                "CandidateProviderBindingV1"
            ),
            (
                "inci_tennis_adapters.candidate_contracts."
                "REQUIRED_CANDIDATE_CAPABILITIES"
            ),
            (
                "inci_tennis_adapters.candidate_contracts."
                "candidate_binding_projection"
            ),
        }
    ),
}
TASK7_RUNTIME_PHASE_ONE_IMPORTS = {
    "provider_qualification_controller.py": frozenset(
        {
            "tennis_v1.entitlements.QualifiedProviderBinding",
            "tennis_v1.events.CapturedInput",
            "tennis_v1.events.PersistedEvent",
            "tennis_v1.events.RecordKind",
            "tennis_v1.sequencer.EventRuntime",
        }
    )
}
TASK7_RUNTIME_ADAPTER_IMPORTS = {
    "live_shadow_cli.py": frozenset(
        {
            "inci_tennis_adapters.kalshi_v2",
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "HybridChooserSnapshot"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "HybridMatchRow"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "HybridStatus"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "KalshiCatalogExclusion"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "KalshiShadowCatalogSnapshot"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "ProviderDiscoveryState"
            ),
            (
                "inci_tennis_adapters.shadow_discovery_contracts."
                "ProviderMatchRef"
            ),
            (
                "inci_tennis_adapters.shadow_match_chooser."
                "resolve_hybrid_shadow_matches"
            ),
            "inci_tennis_adapters.sportradar_trial_v3",
        }
    ),
    "live_shadow_collector.py": frozenset(
        {"inci_tennis_adapters.sportradar_trial_v3"}
    ),
    "sportradar_trial_cli.py": frozenset(
        {"inci_tennis_adapters.sportradar_trial_v3"}
    ),
    "provider_qualification_controller.py": frozenset(
        {
            (
                "inci_tennis_adapters.candidate_contracts."
                "CandidateParserEvidenceV1"
            ),
            (
                "inci_tennis_adapters.candidate_contracts."
                "CandidateProviderBindingV1"
            ),
            (
                "inci_tennis_adapters.candidate_contracts."
                "CandidateQualificationDecisionV1"
            ),
            (
                "inci_tennis_adapters.candidate_contracts."
                "CandidateQuotaClosureV1"
            ),
            (
                "inci_tennis_adapters.candidate_contracts."
                "candidate_binding_projection"
            ),
            (
                "inci_tennis_adapters.candidate_contracts."
                "candidate_decision_projection"
            ),
            (
                "inci_tennis_adapters.candidate_contracts."
                "candidate_quota_projection"
            ),
            (
                "inci_tennis_adapters.candidate_contracts."
                "candidate_quotas_projection"
            ),
            (
                "inci_tennis_adapters.candidate_contracts."
                "candidate_usage_projection"
            ),
            (
                "inci_tennis_adapters.registry."
                "normalize_sportradar_candidate_raw"
            ),
            (
                "inci_tennis_adapters.sportradar_tennis_v3."
                "inspect_sportradar_candidate_capture"
            ),
        }
    )
}
TASK7_RUNTIME_IO_IMPORTS = {
    "live_price_only_collector.py": frozenset(
        {
            (
                "inci_tennis_io.shadow_evidence."
                "PriceOnlyEvidenceObservation"
            ),
            "inci_tennis_io.shadow_evidence.PriceOnlySessionEvidence",
            "inci_tennis_io.shadow_evidence.ShadowMarketCandidate",
        }
    ),
    "live_shadow_cli.py": frozenset(
        {
            (
                "inci_tennis_io.kalshi_shadow_catalog."
                "KalshiShadowCatalogTransport"
            ),
        }
    ),
    "provider_qualification_controller.py": frozenset(
        {
            "inci_tennis_io.facade",
            (
                "inci_tennis_io.ports."
                "CandidateQualificationAppendReceiptV1"
            ),
            (
                "inci_tennis_io.ports."
                "CandidateQualificationOutputWriterV1"
            ),
            "inci_tennis_io.ports.CandidateSourceSealsV1",
            (
                "inci_tennis_io.provider_readonly."
                "SPORTRADAR_CANDIDATE_USAGE"
            ),
            (
                "inci_tennis_io.provider_readonly."
                "ValidatedCandidateOfflineArtifactsV1"
            ),
            (
                "inci_tennis_io.provider_readonly."
                "make_sportradar_candidate_evidence_mismatch_decision"
            ),
            (
                "inci_tennis_io.provider_readonly."
                "make_sportradar_candidate_eligible_decision"
            ),
            (
                "inci_tennis_io.provider_readonly."
                "make_sportradar_candidate_offline_denial"
            ),
        }
    ),
    "shadow_settlement_cli.py": frozenset(
        {
            (
                "inci_tennis_io.kalshi_shadow_settlement."
                "KalshiShadowSettlementTransport"
            ),
            (
                "inci_tennis_io.shadow_settlement_labels."
                "ShadowSettlementLabelStore"
            ),
            (
                "inci_tennis_io.shadow_settlement_labels."
                "reconcile_shadow_settlement"
            ),
        }
    ),
}
TASK7_RUNTIME_EXPERT_IMPORTS = {
    "provider_qualification_controller.py": frozenset(
        {
            "inci_tennis_expert.contracts.BindingUniverse",
            "inci_tennis_expert.contracts.ExpertObservationDraftV1",
            "inci_tennis_expert.contracts.TennisState",
            "inci_tennis_expert.contracts.canonical_expert_bytes",
        }
    ),
    "shadow_cli.py": frozenset(
        {
            "inci_tennis_expert.contracts.MarketStatus",
            "inci_tennis_expert.contracts.MatchFormat",
            "inci_tennis_expert.contracts.MatchStatus",
            "inci_tennis_expert.contracts.PlayerSide",
            "inci_tennis_expert.contracts.SyncReason",
        }
    ),
    "shadow_runtime.py": frozenset(
        {
            "inci_tennis_expert.contracts.MarketStatus",
            "inci_tennis_expert.contracts.MatchFormat",
            "inci_tennis_expert.contracts.MatchStatus",
            "inci_tennis_expert.contracts.PlayerSide",
            "inci_tennis_expert.contracts.ScoreValue",
            "inci_tennis_expert.contracts.SyncReason",
            (
                "inci_tennis_expert.contracts."
                "SynchronizationSessionState"
            ),
            (
                "inci_tennis_expert.contracts."
                "SynchronizationTransitionResult"
            ),
            (
                "inci_tennis_expert.synchronizer."
                "validate_synchronization_transition"
            ),
        }
    ),
}
TASK8_OBSERVATION_ONLY_RUNTIME_FILES = frozenset(
    {
        "live_price_only_collector.py",
        "live_shadow_cli.py",
        "live_shadow_collector.py",
        "shadow_settlement_cli.py",
    }
)
TASK8_RUNTIME_PEER_IMPORTS = {
    "live_price_only_collector.py": frozenset(
        {
            (
                "inci_tennis_runtime.live_shadow_collector."
                "CandidateMarketProjection"
            ),
            "inci_tennis_runtime.live_shadow_collector.CandidateMarketView",
            "inci_tennis_runtime.live_shadow_collector.ShadowCollectorError",
            "inci_tennis_runtime.live_shadow_collector._age",
            "inci_tennis_runtime.live_shadow_collector._durable_to_thread",
            (
                "inci_tennis_runtime.live_shadow_collector."
                "_durable_to_thread_result"
            ),
            "inci_tennis_runtime.live_shadow_collector._error_code",
            (
                "inci_tennis_runtime.live_shadow_collector."
                "_shielded_task_result"
            ),
            "inci_tennis_runtime.live_shadow_collector._terminal_text",
        }
    ),
    "live_shadow_cli.py": frozenset(
        {
            (
                "inci_tennis_runtime.live_shadow_collector."
                "CandidateMarketProjection"
            ),
            "inci_tennis_runtime.live_shadow_collector.CandidateMarketView",
            "inci_tennis_runtime.live_shadow_collector.LiveShadowCollector",
            "inci_tennis_runtime.live_shadow_collector.ShadowCollectorError",
            "inci_tennis_runtime.live_shadow_collector._durable_to_thread",
            (
                "inci_tennis_runtime.live_shadow_collector."
                "_durable_to_thread_result"
            ),
            (
                "inci_tennis_runtime.live_shadow_collector."
                "_provider_failure_allows_price_only"
            ),
            (
                "inci_tennis_runtime.live_shadow_collector."
                "_provider_failure_attestation"
            ),
            (
                "inci_tennis_runtime.live_shadow_collector."
                "_provider_failure_attestation_is_valid"
            ),
            (
                "inci_tennis_runtime.live_shadow_collector."
                "_shielded_task_result"
            ),
            (
                "inci_tennis_runtime.live_price_only_collector."
                "PriceOnlyShadowCollector"
            ),
        }
    ),
    "live_shadow_collector.py": frozenset(),
    "shadow_settlement_cli.py": frozenset(),
}
TASK8_RUNTIME_IO_IMPORTS = {
    "live_price_only_collector.py": frozenset(
        {
            "inci_tennis_io.shadow_evidence.PriceOnlyEvidenceObservation",
            "inci_tennis_io.shadow_evidence.PriceOnlySessionEvidence",
            "inci_tennis_io.shadow_evidence.ShadowMarketCandidate",
        }
    ),
    "live_shadow_cli.py": frozenset(
        {
            "inci_tennis_io.facade.KalshiReadOnlyCredentials",
            "inci_tennis_io.facade.KalshiReadOnlyTransport",
            "inci_tennis_io.facade.PriceOnlySessionEvidence",
            "inci_tennis_io.facade.ShadowEvidenceStore",
            "inci_tennis_io.facade.ShadowMarketCandidate",
            "inci_tennis_io.facade.ShadowResolutionEvidence",
            "inci_tennis_io.facade.SportradarShadowAsyncTransport",
            "inci_tennis_io.facade.TrialObservationRecord",
            "inci_tennis_io.facade.TrialUsageLedger",
            "inci_tennis_io.facade.default_shadow_state_root",
            (
                "inci_tennis_io.facade."
                "load_kalshi_only_credential_material"
            ),
            "inci_tennis_io.facade.load_shadow_credential_material",
            "inci_tennis_io.facade.shadow_kalshi_clock_observation",
            "inci_tennis_io.facade.shadow_monotonic_ns",
            "inci_tennis_io.facade.shadow_pause",
            "inci_tennis_io.facade.shadow_wall_ns",
            "inci_tennis_io.facade.validate_price_only_session_evidence",
            (
                "inci_tennis_io.kalshi_shadow_catalog."
                "KalshiShadowCatalogTransport"
            ),
        }
    ),
    "live_shadow_collector.py": frozenset(
        {
            "inci_tennis_io.facade.ShadowEvidenceObservation",
            "inci_tennis_io.facade.ShadowMarketCandidate",
            "inci_tennis_io.facade.TrialCapture",
            "inci_tennis_io.facade.TrialObservationRecord",
        }
    ),
    "shadow_settlement_cli.py": frozenset(
        {
            (
                "inci_tennis_io.kalshi_shadow_settlement."
                "KalshiShadowSettlementTransport"
            ),
            (
                "inci_tennis_io.shadow_settlement_labels."
                "ShadowSettlementLabelStore"
            ),
            (
                "inci_tennis_io.shadow_settlement_labels."
                "reconcile_shadow_settlement"
            ),
        }
    ),
}

FROZEN_ROOT_V6_IMPORT_ROOTS = frozenset(
    Path(relative_path).stem
    for relative_path in ROOT_V6_PYTHON_PATHS
)
REPOSITORY_IMPORT_ROOTS = frozenset(
    {
        *NEW_PACKAGE_ROOTS,
        "tennis_v1",
        *FROZEN_ROOT_V6_IMPORT_ROOTS,
    }
)
IO_EXTERNAL_IMPORT_ROOTS = frozenset(
    {"cryptography", "requests", "websockets"}
)
SEALED_IO_FILE_EXTERNAL_IMPORT_ROOTS = {
    "kalshi_readonly.py": frozenset({"aiohttp"}),
    "sportradar_shadow_async.py": frozenset({"aiohttp"}),
}
RAW_NETWORK_STDLIB_ROOTS = frozenset(
    {
        "ftplib",
        "http",
        "imaplib",
        "poplib",
        "smtplib",
        "socket",
        "socketserver",
        "ssl",
        "telnetlib",
        "urllib",
        "xmlrpc",
    }
)
DYNAMIC_IMPORT_ROOTS = frozenset(
    {"builtins", "importlib", "pkgutil", "pydoc", "runpy", "zipimport"}
)
FORBIDDEN_DYNAMIC_BUILTINS = frozenset(
    {"__import__", "compile", "eval", "exec"}
)
MAX_FOLDED_STATIC_STRING_NODES = 32
MAX_FOLDED_STATIC_STRING_LENGTH = 256
DYNAMIC_NAMESPACE_NAMES = frozenset(
    {"__builtins__", "builtins", "globals"}
)
FORBIDDEN_EXECUTION_STRINGS = frozenset({"--demo", "--live"})
ASYNCIO_MODULE_NETWORK_CALLS = frozenset(
    {
        "open_connection",
        "open_unix_connection",
        "start_server",
        "start_unix_server",
    }
)
ASYNCIO_LOOP_NETWORK_CALLS = frozenset(
    {
        "create_connection",
        "create_server",
        "create_unix_connection",
        "create_unix_server",
    }
)
ASYNCIO_LOOP_FACTORIES = frozenset(
    {"get_event_loop", "get_running_loop", "new_event_loop"}
)
TYPING_REQUESTS_ANNOTATION_FORMS = frozenset(
    {"Annotated", "Optional", "Union"}
)

NETWORK_IMPORT_ROOTS = frozenset(
    {
        *RAW_NETWORK_STDLIB_ROOTS,
        "aiohttp",
        "httpx",
        "requests",
        "urllib3",
        "websocket",
        "websockets",
    }
)
FILESYSTEM_IMPORT_ROOTS = frozenset(
    {
        "fileinput",
        "glob",
        "io",
        "os",
        "pathlib",
        "shutil",
        "tempfile",
    }
)
PROCESS_IMPORT_ROOTS = frozenset(
    {
        "asyncio.subprocess",
        "multiprocessing",
        "signal",
        "subprocess",
    }
)
CREDENTIAL_IMPORT_ROOTS = frozenset(
    {
        "boto3",
        "cryptography",
        "keyring",
        "oci",
        "paramiko",
        "secrets",
    }
)
NONDETERMINISTIC_IMPORT_ROOTS = frozenset({"random", "secrets", "time"})
LEGACY_EXECUTION_IMPORT_ROOTS = frozenset({"executor"})

FORBIDDEN_AUTHORITY_IDENTIFIERS = frozenset(
    {
        "amend_order",
        "cancel_order",
        "create_order",
        "demo",
        "demo_enabled",
        "demo_mode",
        "is_demo",
        "is_live",
        "live",
        "live_enabled",
        "live_mode",
    }
)
FORBIDDEN_MUTATION_VERBS = frozenset({"delete", "patch", "post", "put"})
HTTP_NON_GET_METHODS = frozenset(
    {
        *FORBIDDEN_MUTATION_VERBS,
        "head",
        "options",
    }
)
HTTP_SESSION_NON_GET_METHODS = frozenset(
    {
        *HTTP_NON_GET_METHODS,
        "send",
    }
)
HTTP_CALL_NAMES = frozenset(
    {
        *HTTP_SESSION_NON_GET_METHODS,
        "get",
        "request",
    }
)
FORBIDDEN_NETWORK_CALLS = frozenset(
    {
        "connect",
        "create_connection",
        "request",
        "send",
        "sendall",
        "socket",
        "urlopen",
    }
)
FORBIDDEN_FILESYSTEM_CALLS = frozenset(
    {
        "chmod",
        "mkdir",
        "open",
        "read_bytes",
        "read_text",
        "rename",
        "replace",
        "rmdir",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    }
)
FORBIDDEN_CLOCK_CALLS = frozenset(
    {
        "monotonic",
        "monotonic_ns",
        "now",
        "perf_counter",
        "perf_counter_ns",
        "sleep",
        "time",
        "time_ns",
        "today",
        "utcnow",
    }
)
FORBIDDEN_PROCESS_CALLS = frozenset(
    {
        "exec",
        "execv",
        "execve",
        "fork",
        "kill",
        "popen",
        "run",
        "spawn",
        "system",
    }
)
FORBIDDEN_ADAPTER_SEGMENTS = frozenset(
    {
        "account",
        "execution",
        "executor",
        "policy",
        "risk",
        "scorecard",
        "simulation",
    }
)
FORBIDDEN_RUNTIME_SEGMENTS = frozenset(
    {
        "account",
        "adapter",
        "model",
        "parse",
        "parser",
        "policy",
        "risk",
        "scorecard",
        "simulation",
        "transport",
    }
)


class ExpertBoundaryViolation(ValueError):
    pass


def canonical_ast_sha256(source: str, filename: str) -> str:
    try:
        tree = ast.parse(source, filename=filename, type_comments=False)
    except (SyntaxError, TypeError, ValueError):
        raise ExpertBoundaryViolation(
            f"{filename}:canonical_ast_parse_forbidden"
        ) from None
    canonical = ast.dump(tree, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _package_inventory(
    package_root: Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not package_root.exists():
        return (), ()
    if package_root.is_symlink():
        raise ExpertBoundaryViolation("package_symlink_forbidden:.")

    pending = [package_root]
    python_paths: list[str] = []
    resource_paths: list[str] = []
    while pending:
        directory = pending.pop()
        try:
            entries = tuple(os.scandir(directory))
        except OSError as error:
            raise ExpertBoundaryViolation(
                "package_inventory_scan_failed"
            ) from error
        for entry in entries:
            entry_path = Path(entry.path)
            relative_path = entry_path.relative_to(package_root).as_posix()
            if entry.is_symlink():
                raise ExpertBoundaryViolation(
                    f"package_symlink_forbidden:{relative_path}"
                )
            if entry.is_dir(follow_symlinks=False):
                if entry.name == "__pycache__":
                    raise ExpertBoundaryViolation(
                        f"package_bytecode_forbidden:{relative_path}"
                    )
                pending.append(entry_path)
                continue
            if not entry.is_file(follow_symlinks=False):
                raise ExpertBoundaryViolation(
                    f"package_entry_type_forbidden:{relative_path}"
                )
            if entry.name.endswith(".pyc"):
                raise ExpertBoundaryViolation(
                    f"package_bytecode_forbidden:{relative_path}"
                )
            if entry.name.endswith(".py"):
                python_paths.append(relative_path)
            else:
                resource_paths.append(relative_path)
    return tuple(sorted(python_paths)), tuple(sorted(resource_paths))


def _package_python_paths(package_root: Path) -> tuple[str, ...]:
    python_paths, _ = _package_inventory(package_root)
    return python_paths


def _imported_modules(tree: ast.AST) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                imported.append("." * node.level + (node.module or ""))
            elif node.module is not None:
                imported.append(node.module)
    return tuple(imported)


def _imported_bindings(tree: ast.AST) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif (
            isinstance(node, ast.ImportFrom)
            and not node.level
            and node.module is not None
        ):
            imported.extend(
                f"{node.module}.{alias.name}" for alias in node.names
            )
    return tuple(imported)


def _root_name(module_name: str) -> str:
    return module_name.lstrip(".").partition(".")[0]


def _segments(value: str) -> frozenset[str]:
    return frozenset(
        segment
        for segment in (
            value.lower().replace("-", "_").replace(".", "_").split("_")
        )
        if segment
    )


def _identifier_names(tree: ast.AST) -> tuple[str, ...]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.alias):
            names.append(node.asname or node.name.rpartition(".")[2])
    return tuple(names)


def _call_leaf_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id.lower()
    if isinstance(call.func, ast.Attribute):
        return call.func.attr.lower()
    return None


def _fold_static_string(expression: ast.AST) -> str | None:
    remaining_nodes = MAX_FOLDED_STATIC_STRING_NODES

    def fold(node: ast.AST) -> str | None:
        nonlocal remaining_nodes
        remaining_nodes -= 1
        if remaining_nodes < 0:
            return None
        if (
            isinstance(node, ast.Constant)
            and type(node.value) is str
        ):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = fold(node.left)
            if left is None:
                return None
            right = fold(node.right)
            if right is None:
                return None
            if len(left) + len(right) <= MAX_FOLDED_STATIC_STRING_LENGTH:
                return left + right
        return None

    return fold(expression)


def _folded_static_strings(tree: ast.AST) -> tuple[str, ...]:
    return tuple(
        value
        for node in ast.walk(tree)
        if (value := _fold_static_string(node)) is not None
    )


def _sys_import_aliases(tree: ast.AST) -> frozenset[str]:
    return frozenset(
        alias.asname or alias.name.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if _root_name(alias.name) == "sys"
    )


def _is_sys_modules_reference(
    expression: ast.AST,
    sys_aliases: frozenset[str],
) -> bool:
    return (
        isinstance(expression, ast.Attribute)
        and expression.attr == "modules"
        and isinstance(expression.value, ast.Name)
        and expression.value.id in sys_aliases
    )


def _reject_unapproved_phase_one_imports(
    tree: ast.AST,
    *,
    allowed_bindings: frozenset[str],
    filename: str,
) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _root_name(alias.name) == "tennis_v1":
                    raise ExpertBoundaryViolation(
                        f"{filename}:phase_one_module_import_forbidden:"
                        f"{alias.name}"
                    )
        elif (
            isinstance(node, ast.ImportFrom)
            and not node.level
            and node.module is not None
            and _root_name(node.module) == "tennis_v1"
        ):
            for alias in node.names:
                binding = f"{node.module}.{alias.name}"
                if alias.name == "*" or binding not in allowed_bindings:
                    raise ExpertBoundaryViolation(
                        f"{filename}:phase_one_binding_forbidden:{binding}"
                    )


def _reject_dynamic_import_authority(
    tree: ast.AST,
    filename: str,
    *,
    allow_sealed_source_inventory_loader: bool = False,
) -> None:
    if allow_sealed_source_inventory_loader:
        return
    sys_aliases = _sys_import_aliases(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                _root_name(alias.name) in DYNAMIC_IMPORT_ROOTS
                for alias in node.names
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:dynamic_import_module_forbidden"
                )
        if isinstance(node, ast.ImportFrom):
            if (
                node.module is not None
                and _root_name(node.module) in DYNAMIC_IMPORT_ROOTS
                and not (
                    allow_sealed_source_inventory_loader
                    and node.module == "importlib.machinery"
                    and tuple(alias.name for alias in node.names)
                    == ("ModuleSpec", "SourceFileLoader")
                )
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:dynamic_import_module_forbidden"
                )
            if (
                node.module == "sys"
                and any(alias.name == "modules" for alias in node.names)
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:sys_modules_import_forbidden"
                )
        if isinstance(node, ast.Name):
            if (
                node.id in FORBIDDEN_DYNAMIC_BUILTINS
                and not (
                    allow_sealed_source_inventory_loader
                    and node.id == "__import__"
                )
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:dynamic_import_builtin_name_forbidden"
                )
            if node.id in DYNAMIC_NAMESPACE_NAMES:
                raise ExpertBoundaryViolation(
                    f"{filename}:dynamic_namespace_forbidden:{node.id}"
                )
        if isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_DYNAMIC_BUILTINS:
                raise ExpertBoundaryViolation(
                    f"{filename}:dynamic_import_builtin_attribute_forbidden"
                )
            if node.attr in DYNAMIC_NAMESPACE_NAMES:
                raise ExpertBoundaryViolation(
                    f"{filename}:dynamic_namespace_attribute_forbidden"
                )
            if _is_sys_modules_reference(node, sys_aliases):
                raise ExpertBoundaryViolation(
                    f"{filename}:sys_modules_authority_forbidden"
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
        ):
            attribute_name = _fold_static_string(node.args[1])
            if attribute_name in FORBIDDEN_DYNAMIC_BUILTINS:
                raise ExpertBoundaryViolation(
                    f"{filename}:dynamic_import_builtin_getattr_forbidden"
                )
            if attribute_name in DYNAMIC_NAMESPACE_NAMES:
                raise ExpertBoundaryViolation(
                    f"{filename}:dynamic_namespace_getattr_forbidden"
                )
            if (
                attribute_name == "modules"
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in sys_aliases
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:sys_modules_getattr_forbidden"
                )
        if isinstance(node, ast.Subscript):
            key = _fold_static_string(node.slice)
            if key in FORBIDDEN_DYNAMIC_BUILTINS:
                raise ExpertBoundaryViolation(
                    f"{filename}:dynamic_import_builtin_subscript_forbidden"
                )
            if key in DYNAMIC_NAMESPACE_NAMES:
                raise ExpertBoundaryViolation(
                    f"{filename}:dynamic_namespace_subscript_forbidden"
                )
            if _is_sys_modules_reference(node.value, sys_aliases):
                raise ExpertBoundaryViolation(
                    f"{filename}:sys_modules_subscript_forbidden"
                )
        if _fold_static_string(node) == "__import__":
            raise ExpertBoundaryViolation(
                f"{filename}:dynamic_import_builtin_string_forbidden"
            )


def _dotted_expression_name(expression: ast.AST) -> str | None:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        prefix = _dotted_expression_name(expression.value)
        if prefix is not None:
            return f"{prefix}.{expression.attr}"
    return None


class _HttpProvenance:
    def __init__(self, tree: ast.AST) -> None:
        self._scope_for: dict[int, int] = {}
        self._scope_parent: dict[int, int | None] = {}
        self._scope_class: dict[int, int | None] = {}
        self._scope_kind: dict[int, str] = {}
        self._bindings: dict[int, set[str]] = {}
        self._kinds: dict[tuple[int, str], str] = {}
        self._class_scopes: dict[tuple[int, str], int] = {}
        self._typing_annotation_forms: dict[
            tuple[int, str],
            str,
        ] = {}

        module_scope = id(tree)
        self._new_scope(
            module_scope,
            parent=None,
            class_scope=None,
            kind="module",
        )
        self._visit(tree, module_scope, None)
        self._seed_requests_imports(tree)
        self._seed_asyncio_imports(tree)
        self._seed_typing_annotation_imports(tree)
        self._seed_requests_annotations(tree)
        self._propagate_assignments(tree)

    def _new_scope(
        self,
        scope: int,
        *,
        parent: int | None,
        class_scope: int | None,
        kind: str,
    ) -> None:
        self._scope_parent[scope] = parent
        self._scope_class[scope] = class_scope
        self._scope_kind[scope] = kind
        self._bindings[scope] = set()

    def _bind(self, scope: int, name: str) -> None:
        self._bindings[scope].add(name)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
        scope: int,
        class_scope: int | None,
    ) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._bind(scope, node.name)
            for decorator in node.decorator_list:
                self._visit(decorator, scope, class_scope)
            if node.returns is not None:
                self._visit(node.returns, scope, class_scope)
            for type_parameter in getattr(node, "type_params", ()):
                self._visit(type_parameter, scope, class_scope)

        for default in node.args.defaults:
            self._visit(default, scope, class_scope)
        for default in node.args.kw_defaults:
            if default is not None:
                self._visit(default, scope, class_scope)

        function_scope = id(node)
        parent_scope = (
            self._scope_parent[scope]
            if self._scope_kind[scope] == "class"
            else scope
        )
        self._new_scope(
            function_scope,
            parent=parent_scope,
            class_scope=class_scope,
            kind="function",
        )
        arguments = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
        if node.args.vararg is not None:
            arguments += (node.args.vararg,)
        if node.args.kwarg is not None:
            arguments += (node.args.kwarg,)
        for argument in arguments:
            self._scope_for[id(argument)] = function_scope
            self._bind(function_scope, argument.arg)
            if argument.annotation is not None:
                self._visit(argument.annotation, scope, class_scope)

        if isinstance(node, ast.Lambda):
            self._visit(node.body, function_scope, class_scope)
        else:
            for statement in node.body:
                self._visit(statement, function_scope, class_scope)

    def _visit(
        self,
        node: ast.AST,
        scope: int,
        class_scope: int | None,
    ) -> None:
        self._scope_for[id(node)] = scope
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda),
        ):
            self._visit_function(node, scope, class_scope)
            return
        if isinstance(node, ast.ClassDef):
            self._bind(scope, node.name)
            for expression in (
                *node.bases,
                *(keyword.value for keyword in node.keywords),
                *node.decorator_list,
                *getattr(node, "type_params", ()),
            ):
                self._visit(expression, scope, class_scope)
            nested_class_scope = id(node)
            self._new_scope(
                nested_class_scope,
                parent=scope,
                class_scope=nested_class_scope,
                kind="class",
            )
            self._class_scopes[
                self._bound_name_reference(scope, node.name)
            ] = nested_class_scope
            for statement in node.body:
                self._visit(
                    statement,
                    nested_class_scope,
                    nested_class_scope,
                )
            return
        if isinstance(node, ast.Import):
            for alias in node.names:
                self._bind(
                    scope,
                    alias.asname or alias.name.partition(".")[0],
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    self._bind(scope, alias.asname or alias.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            self._bind(scope, node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name is not None:
            self._bind(scope, node.name)

        for child in ast.iter_child_nodes(node):
            self._visit(child, scope, class_scope)

    def _binding_scope(self, scope: int, name: str) -> int | None:
        current: int | None = scope
        while current is not None:
            if name in self._bindings[current]:
                return current
            current = self._scope_parent[current]
        return None

    def _bound_name_reference(
        self,
        scope: int,
        name: str,
    ) -> tuple[int, str]:
        if self._scope_kind[scope] == "class":
            return scope, f"@member.{name}"
        return scope, name

    def _class_member_reference(
        self,
        scope: int,
        dotted_name: str,
    ) -> tuple[int, str] | None:
        root, separator, remainder = dotted_name.partition(".")
        class_scope = self._scope_class[scope]
        if (
            class_scope is not None
            and root in {"cls", "self"}
            and separator
        ):
            return class_scope, f"@member.{remainder}"
        if (
            class_scope is not None
            and self._scope_kind[scope] == "class"
            and not separator
            and root in self._bindings[scope]
        ):
            return class_scope, f"@member.{root}"
        if separator:
            binding_scope = self._binding_scope(scope, root)
            if binding_scope is not None:
                declared_class_scope = self._class_scopes.get(
                    self._bound_name_reference(binding_scope, root)
                )
                if declared_class_scope is not None:
                    return (
                        declared_class_scope,
                        f"@member.{remainder}",
                    )
        return None

    def _reference(
        self,
        expression: ast.AST,
    ) -> tuple[int, str] | None:
        if isinstance(expression, ast.Attribute):
            class_scope = self._class_scope(expression.value)
            if class_scope is not None:
                return class_scope, f"@member.{expression.attr}"
        dotted_name = _dotted_expression_name(expression)
        if dotted_name is None:
            return None
        scope = self._scope_for[id(expression)]
        class_member = self._class_member_reference(scope, dotted_name)
        if class_member is not None:
            return class_member
        root = dotted_name.partition(".")[0]
        binding_scope = self._binding_scope(scope, root)
        if binding_scope is None:
            return None
        return binding_scope, dotted_name

    def _target_reference(
        self,
        target: ast.AST,
        scope: int,
    ) -> tuple[int, str] | None:
        if isinstance(target, ast.Name):
            return self._bound_name_reference(scope, target.id)
        if not isinstance(target, ast.Attribute):
            return None
        dotted_name = _dotted_expression_name(target)
        if dotted_name is None:
            return None
        class_member = self._class_member_reference(scope, dotted_name)
        if class_member is not None:
            return class_member
        root = dotted_name.partition(".")[0]
        binding_scope = self._binding_scope(scope, root)
        return (binding_scope or scope), dotted_name

    def _register(
        self,
        reference: tuple[int, str] | None,
        kind: str,
    ) -> bool:
        if reference is None or reference in self._kinds:
            return False
        self._kinds[reference] = kind
        return True

    def _class_scope(self, expression: ast.AST) -> int | None:
        if isinstance(expression, ast.Call):
            return self._class_scope(expression.func)
        if not isinstance(expression, ast.Name):
            return None
        scope = self._scope_for[id(expression)]
        binding_scope = self._binding_scope(scope, expression.id)
        if binding_scope is None:
            return None
        return self._class_scopes.get(
            self._bound_name_reference(binding_scope, expression.id)
        )

    def _register_class(
        self,
        reference: tuple[int, str] | None,
        class_scope: int | None,
    ) -> bool:
        if (
            reference is None
            or class_scope is None
            or reference in self._class_scopes
        ):
            return False
        self._class_scopes[reference] = class_scope
        return True

    def _seed_requests_imports(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                scope = self._scope_for[id(node)]
                for alias in node.names:
                    if _root_name(alias.name) != "requests":
                        continue
                    binding = (
                        alias.asname or alias.name.partition(".")[0]
                    )
                    self._register(
                        self._bound_name_reference(scope, binding),
                        "receiver",
                    )
            elif (
                isinstance(node, ast.ImportFrom)
                and not node.level
                and node.module is not None
                and _root_name(node.module) == "requests"
            ):
                scope = self._scope_for[id(node)]
                for alias in node.names:
                    binding = alias.asname or alias.name
                    method = alias.name.lower()
                    if method in HTTP_CALL_NAMES:
                        kind = f"callable:{method}"
                    elif method in {"session"}:
                        kind = "factory"
                    elif method in {"api", "sessions"}:
                        kind = "receiver"
                    else:
                        continue
                    self._register(
                        self._bound_name_reference(scope, binding),
                        kind,
                    )

    def _seed_asyncio_imports(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                scope = self._scope_for[id(node)]
                for alias in node.names:
                    if _root_name(alias.name) != "asyncio":
                        continue
                    binding = (
                        alias.asname or alias.name.partition(".")[0]
                    )
                    self._register(
                        self._bound_name_reference(scope, binding),
                        "asyncio_module",
                    )
            elif (
                isinstance(node, ast.ImportFrom)
                and not node.level
                and node.module is not None
                and _root_name(node.module) == "asyncio"
            ):
                scope = self._scope_for[id(node)]
                for alias in node.names:
                    binding = alias.asname or alias.name
                    method = alias.name.lower()
                    if method in ASYNCIO_MODULE_NETWORK_CALLS:
                        kind = f"asyncio_forbidden:{method}"
                    elif method in ASYNCIO_LOOP_FACTORIES:
                        kind = "asyncio_loop_factory"
                    else:
                        continue
                    self._register(
                        self._bound_name_reference(scope, binding),
                        kind,
                    )

    def _seed_typing_annotation_imports(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                scope = self._scope_for[id(node)]
                for alias in node.names:
                    if alias.name != "typing":
                        continue
                    binding = alias.asname or alias.name
                    self._typing_annotation_forms[
                        self._bound_name_reference(scope, binding)
                    ] = "module"
            elif (
                isinstance(node, ast.ImportFrom)
                and not node.level
                and node.module == "typing"
            ):
                scope = self._scope_for[id(node)]
                for alias in node.names:
                    if alias.name not in TYPING_REQUESTS_ANNOTATION_FORMS:
                        continue
                    binding = alias.asname or alias.name
                    self._typing_annotation_forms[
                        self._bound_name_reference(scope, binding)
                    ] = alias.name

    def _typing_annotation_form(
        self,
        expression: ast.AST,
    ) -> str | None:
        if isinstance(expression, ast.Name):
            reference = self._reference(expression)
            if reference is not None:
                return self._typing_annotation_forms.get(reference)
        elif (
            isinstance(expression, ast.Attribute)
            and expression.attr in TYPING_REQUESTS_ANNOTATION_FORMS
        ):
            reference = self._reference(expression.value)
            if (
                reference is not None
                and self._typing_annotation_forms.get(reference) == "module"
            ):
                return expression.attr
        return None

    @staticmethod
    def _is_none_annotation(expression: ast.AST) -> bool:
        return (
            isinstance(expression, ast.Constant)
            and expression.value is None
        )

    def _is_requests_receiver_annotation(
        self,
        expression: ast.AST,
    ) -> bool:
        if self.kind(expression) == "factory":
            return True
        if isinstance(expression, ast.BinOp) and isinstance(
            expression.op,
            ast.BitOr,
        ):
            return (
                self._is_requests_receiver_annotation(expression.left)
                and self._is_none_annotation(expression.right)
            ) or (
                self._is_none_annotation(expression.left)
                and self._is_requests_receiver_annotation(expression.right)
            )
        if not isinstance(expression, ast.Subscript):
            return False
        form = self._typing_annotation_form(expression.value)
        elements = (
            expression.slice.elts
            if isinstance(expression.slice, ast.Tuple)
            else (expression.slice,)
        )
        if form == "Optional":
            return (
                len(elements) == 1
                and self._is_requests_receiver_annotation(elements[0])
            )
        if form == "Union":
            return (
                len(elements) == 2
                and (
                    self._is_requests_receiver_annotation(elements[0])
                    and self._is_none_annotation(elements[1])
                    or self._is_none_annotation(elements[0])
                    and self._is_requests_receiver_annotation(elements[1])
                )
            )
        if form == "Annotated":
            return (
                len(elements) >= 2
                and self._is_requests_receiver_annotation(elements[0])
            )
        return False

    def _seed_requests_annotations(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.arg)
                and node.annotation is not None
                and self._is_requests_receiver_annotation(node.annotation)
            ):
                scope = self._scope_for[id(node)]
                self._register(
                    self._bound_name_reference(scope, node.arg),
                    "typed_receiver",
                )

    def _propagate_assignments(self, tree: ast.AST) -> None:
        assignments: list[tuple[int, ast.AST, ast.AST]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                scope = self._scope_for[id(node)]
                assignments.extend(
                    (scope, target, node.value) for target in node.targets
                )
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                scope = self._scope_for[id(node)]
                assignments.append((scope, node.target, node.value))

        changed = True
        while changed:
            changed = False
            for scope, target, value in assignments:
                kind = self.kind(value)
                if kind is not None and self._register(
                    self._target_reference(target, scope),
                    kind,
                ):
                    changed = True
                if self._register_class(
                    self._target_reference(target, scope),
                    self._class_scope(value),
                ):
                    changed = True

    def kind(self, expression: ast.AST) -> str | None:
        reference = self._reference(expression)
        if reference is not None and reference in self._kinds:
            return self._kinds[reference]
        if isinstance(expression, ast.Attribute):
            receiver_kind = self.kind(expression.value)
            if receiver_kind in {"receiver", "typed_receiver"}:
                method = expression.attr.lower()
                if method in HTTP_CALL_NAMES:
                    return f"callable:{method}"
                if (
                    receiver_kind == "receiver"
                    and method in {"api", "sessions"}
                ):
                    return "receiver"
                if receiver_kind == "receiver" and method == "session":
                    return "factory"
            if receiver_kind == "asyncio_module":
                method = expression.attr.lower()
                if method in ASYNCIO_MODULE_NETWORK_CALLS:
                    return f"asyncio_forbidden:{method}"
                if method in ASYNCIO_LOOP_FACTORIES:
                    return "asyncio_loop_factory"
            if receiver_kind == "asyncio_loop":
                method = expression.attr.lower()
                if method in ASYNCIO_LOOP_NETWORK_CALLS:
                    return f"asyncio_forbidden:{method}"
        elif isinstance(expression, ast.Call):
            function_kind = self.kind(expression.func)
            if function_kind == "factory":
                return "receiver"
            if function_kind == "asyncio_loop_factory":
                return "asyncio_loop"
        return None


def _is_direct_provenance_assignment(
    parent: ast.AST | None,
    node: ast.AST,
) -> bool:
    return (
        isinstance(parent, ast.Assign)
        and parent.value is node
        and all(
            isinstance(target, (ast.Name, ast.Attribute))
            for target in parent.targets
        )
    ) or (
        isinstance(parent, ast.AnnAssign)
        and parent.value is node
        and isinstance(parent.target, (ast.Name, ast.Attribute))
    )


def _is_http_provenance_component(
    parent: ast.AST | None,
    node: ast.AST,
    provenance: _HttpProvenance,
) -> bool:
    if (
        isinstance(parent, ast.Attribute)
        and parent.value is node
        and provenance.kind(parent) is not None
    ):
        return True
    return (
        isinstance(parent, ast.Call)
        and parent.func is node
        and provenance.kind(node) == "factory"
        and provenance.kind(parent) == "receiver"
    )


def _is_safe_http_receiver_configuration(
    parent: ast.AST | None,
    node: ast.AST,
    parents: dict[int, ast.AST],
) -> bool:
    """Allow only the explicit opt-out from ambient requests credentials."""

    if not (
        isinstance(parent, ast.Attribute)
        and parent.value is node
        and parent.attr == "trust_env"
        and isinstance(parent.ctx, ast.Store)
    ):
        return False
    assignment = parents.get(id(parent))
    return (
        isinstance(assignment, ast.Assign)
        and len(assignment.targets) == 1
        and assignment.targets[0] is parent
        and isinstance(assignment.value, ast.Constant)
        and assignment.value.value is False
    )


def _is_safe_http_receiver_cleanup(
    parent: ast.AST | None,
    node: ast.AST,
    parents: dict[int, ast.AST],
) -> bool:
    """Allow an owned receiver to be closed without leaking its authority."""

    if not (
        isinstance(parent, ast.Attribute)
        and parent.value is node
        and parent.attr == "close"
    ):
        return False
    call = parents.get(id(parent))
    return (
        isinstance(call, ast.Call)
        and call.func is parent
        and not call.args
        and not call.keywords
    )


def _call_uses_exact_get_method(node: ast.Call) -> bool:
    method_nodes: list[ast.AST] = []
    if node.args:
        method_nodes.append(node.args[0])
    method_nodes.extend(
        keyword.value for keyword in node.keywords if keyword.arg == "method"
    )
    return (
        len(method_nodes) == 1
        and isinstance(method_nodes[0], ast.Constant)
        and type(method_nodes[0].value) is str
        and method_nodes[0].value == "GET"
    )


def _annotation_node_ids(tree: ast.AST) -> frozenset[int]:
    annotation_ids: set[int] = set()

    def include(annotation: ast.AST | None) -> None:
        if annotation is not None:
            annotation_ids.update(id(node) for node in ast.walk(annotation))

    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            include(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            include(node.returns)
        elif isinstance(node, ast.AnnAssign):
            include(node.annotation)

        for type_parameter in getattr(node, "type_params", ()):
            include(getattr(type_parameter, "bound", None))
            include(getattr(type_parameter, "default_value", None))

    return frozenset(annotation_ids)


def _is_asyncio_provenance_component(
    parent: ast.AST | None,
    node: ast.AST,
    kind: str,
) -> bool:
    if isinstance(parent, ast.Attribute) and parent.value is node:
        return True
    return (
        kind == "asyncio_loop_factory"
        and isinstance(parent, ast.Call)
        and parent.func is node
    )


def _require_io_remote_get_only(
    tree: ast.AST,
    filename: str,
    *,
    allow_exact_reviewed_receiver_escape: bool = False,
) -> None:
    provenance = _HttpProvenance(tree)
    annotation_ids = _annotation_node_ids(tree)
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    for node in ast.walk(tree):
        if id(node) in annotation_ids:
            continue
        if (
            isinstance(node, ast.ImportFrom)
            and not node.level
            and node.module is not None
            and _root_name(node.module) == "requests"
        ):
            for alias in node.names:
                if alias.name.lower() in HTTP_SESSION_NON_GET_METHODS:
                    raise ExpertBoundaryViolation(
                        f"{filename}:remote_non_get_import_forbidden:"
                        f"{alias.name}"
                    )

        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and provenance.kind(node.args[0]) == "receiver"
        ):
            if not (
                isinstance(node.args[1], ast.Constant)
                and type(node.args[1].value) is str
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:dynamic_http_method_forbidden"
                )
            if node.args[1].value.lower() in HTTP_CALL_NAMES:
                raise ExpertBoundaryViolation(
                    f"{filename}:http_callable_getattr_forbidden:"
                    f"{node.args[1].value}"
                )

        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
        ):
            receiver_kind = provenance.kind(node.args[0])
            asyncio_methods: frozenset[str] | None = None
            if receiver_kind == "asyncio_module":
                asyncio_methods = ASYNCIO_MODULE_NETWORK_CALLS
            elif receiver_kind == "asyncio_loop":
                asyncio_methods = ASYNCIO_LOOP_NETWORK_CALLS
            if asyncio_methods is not None:
                if not (
                    isinstance(node.args[1], ast.Constant)
                    and type(node.args[1].value) is str
                ):
                    raise ExpertBoundaryViolation(
                        f"{filename}:dynamic_asyncio_method_forbidden"
                    )
                if node.args[1].value.lower() in asyncio_methods:
                    raise ExpertBoundaryViolation(
                        f"{filename}:raw_asyncio_network_getattr_forbidden:"
                        f"{node.args[1].value}"
                    )

        if isinstance(node, (ast.Name, ast.Attribute)) and not isinstance(
            node.ctx,
            ast.Load,
        ):
            continue
        kind = provenance.kind(node)
        if kind is None:
            continue
        parent = parents.get(id(node))
        if kind.startswith("asyncio_forbidden:"):
            raise ExpertBoundaryViolation(
                f"{filename}:raw_asyncio_network_call_forbidden:"
                f"{kind.partition(':')[2]}"
            )
        if kind.startswith("asyncio_"):
            if not (
                _is_asyncio_provenance_component(parent, node, kind)
                or _is_direct_provenance_assignment(parent, node)
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:asyncio_authority_escape_forbidden:{kind}"
                )
            continue
        if kind == "typed_receiver":
            continue
        if kind.startswith("callable:"):
            method = kind.partition(":")[2]
            if method in HTTP_SESSION_NON_GET_METHODS:
                raise ExpertBoundaryViolation(
                    f"{filename}:remote_non_get_callable_forbidden:{method}"
                )
            is_direct_call = (
                isinstance(parent, ast.Call) and parent.func is node
            )
            if not (
                is_direct_call
                or _is_direct_provenance_assignment(parent, node)
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:http_callable_escape_forbidden:{method}"
                )
        elif not (
            _is_http_provenance_component(parent, node, provenance)
            or _is_direct_provenance_assignment(parent, node)
            or _is_safe_http_receiver_configuration(parent, node, parents)
            or _is_safe_http_receiver_cleanup(parent, node, parents)
            or (
                allow_exact_reviewed_receiver_escape
                and kind == "receiver"
            )
        ):
            raise ExpertBoundaryViolation(
                f"{filename}:http_receiver_escape_forbidden:{kind}"
            )

    for node in ast.walk(tree):
        if id(node) in annotation_ids or not isinstance(node, ast.Call):
            continue
        call_kind = provenance.kind(node.func)
        if call_kind != "callable:request":
            continue
        if not _call_uses_exact_get_method(node):
            raise ExpertBoundaryViolation(
                f"{filename}:generic_request_requires_exact_get"
            )


def _is_domain_live_member(
    node: ast.AST,
    parents: dict[int, ast.AST],
) -> bool:
    if isinstance(node, ast.Attribute):
        base_name = _dotted_expression_name(node.value)
        return (
            node.attr == "LIVE"
            and base_name is not None
            and base_name.rpartition(".")[2].endswith("Status")
        )
    if not (
        isinstance(node, ast.Name)
        and node.id == "LIVE"
        and isinstance(node.ctx, ast.Store)
    ):
        return False

    assignment = parents.get(id(node))
    if isinstance(assignment, ast.Assign):
        value = assignment.value
    elif isinstance(assignment, ast.AnnAssign):
        value = assignment.value
    else:
        return False
    class_node = parents.get(id(assignment))
    if not (
        isinstance(class_node, ast.ClassDef)
        and assignment in class_node.body
        and class_node.name.endswith("Status")
        and any(
            (
                (_dotted_expression_name(base) or "").rpartition(".")[2]
                in {"Enum", "StrEnum"}
            )
            for base in class_node.bases
        )
    ):
        return False
    return (
        isinstance(value, ast.Constant)
        and type(value.value) is str
        and value.value == "live"
    )


def _reject_forbidden_authority(tree: ast.AST, filename: str) -> None:
    identifiers = {name.lower() for name in _identifier_names(tree)}
    forbidden = (
        identifiers & FORBIDDEN_AUTHORITY_IDENTIFIERS
    ) - {"live"}
    if forbidden:
        raise ExpertBoundaryViolation(
            f"{filename}:forbidden_authority_identifier:{sorted(forbidden)!r}"
        )

    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(tree):
        identifier: str | None = None
        if isinstance(node, ast.Name):
            identifier = node.id
        elif isinstance(node, ast.Attribute):
            identifier = node.attr
        elif isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            identifier = node.name
        elif isinstance(node, ast.arg):
            identifier = node.arg
        elif isinstance(node, ast.alias):
            identifier = node.asname or node.name.rpartition(".")[2]
        if (
            identifier is not None
            and identifier.lower() == "live"
            and not _is_domain_live_member(node, parents)
        ):
            raise ExpertBoundaryViolation(
                f"{filename}:forbidden_authority_identifier:['live']"
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and (
                attribute_name := _fold_static_string(node.args[1])
            ) is not None
            and attribute_name.lower()
            in FORBIDDEN_AUTHORITY_IDENTIFIERS
        ):
            raise ExpertBoundaryViolation(
                f"{filename}:forbidden_dynamic_authority_identifier:"
                f"{attribute_name}"
            )

    strings = tuple(
        value.lower() for value in _folded_static_strings(tree)
    )
    execution_strings = set(strings) & FORBIDDEN_EXECUTION_STRINGS
    if execution_strings:
        raise ExpertBoundaryViolation(
            f"{filename}:execution_string_forbidden:"
            f"{sorted(execution_strings)!r}"
        )
    if any(
        "/portfolio/orders" in value or "/portfolio/positions" in value
        for value in strings
    ):
        raise ExpertBoundaryViolation(
            f"{filename}:portfolio_order_route_forbidden"
        )
    if (
        any("/portfolio/" in value for value in strings)
        and any(value in FORBIDDEN_MUTATION_VERBS for value in strings)
    ):
        raise ExpertBoundaryViolation(
            f"{filename}:portfolio_mutation_route_forbidden"
        )


def check_source(source: str, *, package_name: str, filename: str) -> None:
    if package_name not in PACKAGE_ROOTS:
        raise ExpertBoundaryViolation(f"unknown_package:{package_name}")
    try:
        tree = ast.parse(
            textwrap.dedent(source),
            filename=filename,
            type_comments=False,
        )
    except (SyntaxError, TypeError, ValueError):
        raise ExpertBoundaryViolation(
            f"{filename}:source_parse_forbidden"
        ) from None

    expected_digest = EXPECTED_PACKAGE_AST_SHA256[package_name].get(filename)
    is_task7_tool = (
        package_name == "inci_tennis_io"
        and filename == TASK7_TOOL_RELATIVE_PATH
    )
    source_digest = (
        canonical_ast_sha256(source, filename)
        if expected_digest is not None or is_task7_tool
        else None
    )
    if (
        is_task7_tool
        and source_digest != EXPECTED_TASK7_TOOL_AST_SHA256
    ):
        raise ExpertBoundaryViolation(
            f"{filename}:task7_tool_ast_seal_mismatch:{source_digest}"
        )
    is_exactly_sealed_source = (
        expected_digest is not None
        and source_digest == expected_digest
    ) or is_task7_tool
    allow_sealed_source_inventory_loader = (
        package_name == "inci_tennis_io"
        and filename == "expert_journal_store.py"
        and is_exactly_sealed_source
    )
    if filename in TASK8_OBSERVATION_ONLY_RUNTIME_FILES and any(
        isinstance(node, ast.ImportFrom) and bool(node.level)
        for node in ast.walk(tree)
    ):
        raise ExpertBoundaryViolation(
            f"{filename}:runtime_relative_import_forbidden"
        )
    _reject_forbidden_authority(tree, filename)
    _reject_dynamic_import_authority(
        tree,
        filename,
        allow_sealed_source_inventory_loader=(
            allow_sealed_source_inventory_loader
        ),
    )
    if package_name == "inci_tennis_io":
        _require_io_remote_get_only(
            tree,
            filename,
            allow_exact_reviewed_receiver_escape=(
                filename == "kalshi_shadow_settlement.py"
                and is_exactly_sealed_source
            ),
        )
    imported_modules = _imported_modules(tree)
    imported_bindings = _imported_bindings(tree)
    if is_task7_tool:
        repository_imports = frozenset(
            binding
            for binding in imported_bindings
            if _root_name(binding) in REPOSITORY_IMPORT_ROOTS
        )
        if repository_imports != TASK7_TOOL_REPOSITORY_IMPORTS:
            raise ExpertBoundaryViolation(
                f"{filename}:task7_tool_imports_forbidden:"
                f"{sorted(repository_imports)!r}"
            )
    imported_roots = {_root_name(module) for module in imported_modules}
    imported_full_roots = {
        module.lstrip(".").partition(".")[0]
        if not module.startswith("asyncio.subprocess")
        else "asyncio.subprocess"
        for module in imported_modules
    }
    absolute_imported_roots = {
        _root_name(module)
        for module in imported_modules
        if not module.startswith(".")
    }
    frozen_root_v6_imports = (
        absolute_imported_roots & FROZEN_ROOT_V6_IMPORT_ROOTS
    )
    if frozen_root_v6_imports:
        raise ExpertBoundaryViolation(
            f"{filename}:frozen_root_v6_import_forbidden:"
            f"{sorted(frozen_root_v6_imports)!r}"
        )
    allowed_external_roots = (
        IO_EXTERNAL_IMPORT_ROOTS
        | (
            SEALED_IO_FILE_EXTERNAL_IMPORT_ROOTS.get(
                filename, frozenset()
            )
            if is_exactly_sealed_source
            else frozenset()
        )
        if package_name == "inci_tennis_io"
        else frozenset()
    )
    unapproved_external_roots = {
        root
        for root in absolute_imported_roots
        if root not in sys.stdlib_module_names
        and root not in REPOSITORY_IMPORT_ROOTS
        and root not in allowed_external_roots
    }
    if unapproved_external_roots:
        raise ExpertBoundaryViolation(
            f"{filename}:external_import_forbidden:"
            f"{sorted(unapproved_external_roots)!r}"
        )

    if imported_roots & LEGACY_EXECUTION_IMPORT_ROOTS:
        raise ExpertBoundaryViolation(f"{filename}:legacy_executor_forbidden")

    if package_name == "inci_tennis_expert":
        forbidden_imports = (
            NETWORK_IMPORT_ROOTS
            | FILESYSTEM_IMPORT_ROOTS
            | PROCESS_IMPORT_ROOTS
            | CREDENTIAL_IMPORT_ROOTS
            | NONDETERMINISTIC_IMPORT_ROOTS
            | {
                "inci_tennis_adapters",
                "inci_tennis_io",
                "inci_tennis_runtime",
            }
        )
        _reject_unapproved_phase_one_imports(
            tree,
            allowed_bindings=(
                SEALED_EXPERT_PHASE_ONE_IMPORTS
                if is_exactly_sealed_source
                else EXPERT_PHASE_ONE_IMPORTS
            ),
            filename=filename,
        )
    elif package_name == "inci_tennis_io":
        scoped_phase_one_imports = (
            TASK7_IO_PHASE_ONE_IMPORTS.get(filename, frozenset())
            if is_exactly_sealed_source
            else frozenset()
        )
        scoped_adapter_imports = (
            TASK7_IO_ADAPTER_IMPORTS.get(filename, frozenset())
            if is_exactly_sealed_source
            else frozenset()
        )
        for binding in imported_bindings:
            if (
                _root_name(binding) == "inci_tennis_adapters"
                and binding not in scoped_adapter_imports
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:io_adapter_import_forbidden:{binding}"
                )
        forbidden_imports = {
            "inci_tennis_runtime",
            *(
                ()
                if scoped_adapter_imports
                else ("inci_tennis_adapters",)
            ),
        }
        rejected_raw_network_imports = (
            imported_roots & RAW_NETWORK_STDLIB_ROOTS
        )
        if rejected_raw_network_imports:
            raise ExpertBoundaryViolation(
                f"{filename}:io_raw_network_import_forbidden:"
                f"{sorted(rejected_raw_network_imports)!r}"
            )
        rejected_network_imports = imported_roots & (
            NETWORK_IMPORT_ROOTS
            - allowed_external_roots
            - RAW_NETWORK_STDLIB_ROOTS
        )
        if rejected_network_imports:
            raise ExpertBoundaryViolation(
                f"{filename}:io_network_import_forbidden:"
                f"{sorted(rejected_network_imports)!r}"
            )
        _reject_unapproved_phase_one_imports(
            tree,
            allowed_bindings=(
                IO_PHASE_ONE_IMPORTS | scoped_phase_one_imports
            ),
            filename=filename,
        )
        scoped_expert_imports = (
            TASK7_IO_EXPERT_IMPLEMENTATION_IMPORTS.get(
                filename,
                frozenset(),
            )
            if is_exactly_sealed_source
            else frozenset()
        )
        for module in imported_bindings:
            if (
                module.startswith("inci_tennis_expert.")
                and not any(
                    module.startswith(prefix)
                    for prefix in (
                        "inci_tennis_expert.contracts.",
                        "inci_tennis_expert.wire.",
                    )
                )
                and module not in IO_EXPERT_IMPLEMENTATION_IMPORTS
                and module not in scoped_expert_imports
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:io_expert_import_forbidden:{module}"
                )
    elif package_name == "inci_tennis_adapters":
        scoped_peer_imports = (
            TASK7_ADAPTER_PEER_IMPORTS.get(filename, frozenset())
            if is_exactly_sealed_source
            else frozenset()
        )
        for binding in imported_bindings:
            if (
                _root_name(binding) == "inci_tennis_adapters"
                and binding not in scoped_peer_imports
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:adapter_peer_import_forbidden:{binding}"
                )
        forbidden_imports = (
            NETWORK_IMPORT_ROOTS
            | FILESYSTEM_IMPORT_ROOTS
            | PROCESS_IMPORT_ROOTS
            | CREDENTIAL_IMPORT_ROOTS
            | NONDETERMINISTIC_IMPORT_ROOTS
            | {
                "inci_tennis_io",
                "inci_tennis_runtime",
            }
        )
        _reject_unapproved_phase_one_imports(
            tree,
            allowed_bindings=(
                TASK7_ADAPTER_PHASE_ONE_IMPORTS.get(
                    filename,
                    frozenset(),
                )
                if is_exactly_sealed_source
                else frozenset()
            ),
            filename=filename,
        )
        for module in imported_bindings:
            if _segments(module) & FORBIDDEN_ADAPTER_SEGMENTS:
                raise ExpertBoundaryViolation(
                    f"{filename}:adapter_authority_import_forbidden:{module}"
                )
    else:
        scoped_phase_one_imports = (
            TASK7_RUNTIME_PHASE_ONE_IMPORTS.get(filename, frozenset())
            if is_exactly_sealed_source
            else frozenset()
        )
        scoped_adapter_imports = (
            TASK7_RUNTIME_ADAPTER_IMPORTS.get(filename, frozenset())
            if is_exactly_sealed_source
            else frozenset()
        )
        scoped_stdlib_authority_imports = (
            SEALED_RUNTIME_STDLIB_AUTHORITY_IMPORTS
            | SEALED_RUNTIME_FILE_STDLIB_AUTHORITY_IMPORTS.get(
                filename,
                frozenset(),
            )
            if is_exactly_sealed_source
            else frozenset()
        )
        for binding in imported_bindings:
            if (
                _root_name(binding) == "inci_tennis_adapters"
                and binding not in scoped_adapter_imports
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:runtime_adapter_import_forbidden:{binding}"
                )
        if is_exactly_sealed_source:
            rejected_sealed_stdlib = {
                binding
                for binding in imported_bindings
                if _root_name(binding)
                in (
                    PROCESS_IMPORT_ROOTS
                    | NONDETERMINISTIC_IMPORT_ROOTS
                    | FILESYSTEM_IMPORT_ROOTS
                )
                and binding not in scoped_stdlib_authority_imports
            }
            if rejected_sealed_stdlib:
                raise ExpertBoundaryViolation(
                    f"{filename}:sealed_runtime_import_forbidden:"
                    f"{sorted(rejected_sealed_stdlib)!r}"
                )
        forbidden_imports = (
            (
                NETWORK_IMPORT_ROOTS
                | FILESYSTEM_IMPORT_ROOTS
                | PROCESS_IMPORT_ROOTS
                | CREDENTIAL_IMPORT_ROOTS
                | NONDETERMINISTIC_IMPORT_ROOTS
                | (
                    set()
                    if scoped_adapter_imports
                    else {"inci_tennis_adapters"}
                )
            )
            - {
                _root_name(binding)
                for binding in scoped_stdlib_authority_imports
            }
            if is_exactly_sealed_source
            else (
                NETWORK_IMPORT_ROOTS
                | FILESYSTEM_IMPORT_ROOTS
                | PROCESS_IMPORT_ROOTS
                | CREDENTIAL_IMPORT_ROOTS
                | NONDETERMINISTIC_IMPORT_ROOTS
                | {
                    "inci_tennis_adapters",
                }
            )
        )
        _reject_unapproved_phase_one_imports(
            tree,
            allowed_bindings=(
                SEALED_RUNTIME_PHASE_ONE_IMPORTS
                | scoped_phase_one_imports
                if is_exactly_sealed_source
                else RUNTIME_PHASE_ONE_IMPORTS
            ),
            filename=filename,
        )
        scoped_io_imports = (
            TASK8_RUNTIME_IO_IMPORTS[filename]
            if filename in TASK8_OBSERVATION_ONLY_RUNTIME_FILES
            else (
                TASK7_RUNTIME_IO_IMPORTS.get(filename, frozenset())
                if is_exactly_sealed_source
                else frozenset()
            )
        )
        scoped_expert_imports = (
            TASK7_RUNTIME_EXPERT_IMPORTS.get(filename, frozenset())
            if is_exactly_sealed_source
            else frozenset()
        )
        for module in imported_bindings:
            is_expert_module = (
                module == "inci_tennis_expert"
                or module.startswith("inci_tennis_expert.")
            )
            if (
                filename in TASK8_OBSERVATION_ONLY_RUNTIME_FILES
                and _root_name(module) == "inci_tennis_runtime"
                and module not in TASK8_RUNTIME_PEER_IMPORTS[filename]
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:runtime_peer_import_forbidden:{module}"
                )
            if _segments(module) & FORBIDDEN_RUNTIME_SEGMENTS:
                raise ExpertBoundaryViolation(
                    f"{filename}:runtime_implementation_import_forbidden:{module}"
                )
            unscoped_io_binding = (
                _root_name(module) == "inci_tennis_io"
                and module not in scoped_io_imports
            )
            if (
                unscoped_io_binding
                and (
                    filename in TASK8_OBSERVATION_ONLY_RUNTIME_FILES
                    or not any(
                        module.startswith(prefix)
                        for prefix in (
                            "inci_tennis_io.facade.",
                            "inci_tennis_io.ports.",
                        )
                    )
                )
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:runtime_io_import_forbidden:{module}"
                )
            if (
                filename in TASK8_OBSERVATION_ONLY_RUNTIME_FILES
                and is_expert_module
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:runtime_expert_import_forbidden:{module}"
                )
            if (
                filename in TASK7_RUNTIME_EXPERT_IMPORTS
                and is_expert_module
                and module not in scoped_expert_imports
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:runtime_expert_import_forbidden:{module}"
                )
            if (
                is_expert_module
                and not is_exactly_sealed_source
                and not any(
                    module.startswith(prefix)
                    for prefix in (
                        "inci_tennis_expert.engine.",
                        "inci_tennis_expert.facade.",
                    )
                )
            ):
                raise ExpertBoundaryViolation(
                    f"{filename}:runtime_expert_import_forbidden:{module}"
                )

    rejected_imports = imported_full_roots & forbidden_imports
    if rejected_imports:
        raise ExpertBoundaryViolation(
            f"{filename}:forbidden_import:{sorted(rejected_imports)!r}"
        )

    identifier_segments = set().union(
        *(_segments(name) for name in _identifier_names(tree)),
        frozenset(),
    )
    if package_name == "inci_tennis_adapters":
        forbidden_segments = identifier_segments & FORBIDDEN_ADAPTER_SEGMENTS
        if forbidden_segments and not is_exactly_sealed_source:
            raise ExpertBoundaryViolation(
                f"{filename}:adapter_authority_forbidden:"
                f"{sorted(forbidden_segments)!r}"
            )
    elif package_name == "inci_tennis_runtime":
        forbidden_segments = identifier_segments & FORBIDDEN_RUNTIME_SEGMENTS
        if forbidden_segments and not is_exactly_sealed_source:
            raise ExpertBoundaryViolation(
                f"{filename}:runtime_implementation_forbidden:"
                f"{sorted(forbidden_segments)!r}"
            )

    call_names = {
        name
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        if (name := _call_leaf_name(call)) is not None
    }
    if (
        call_names & FORBIDDEN_DYNAMIC_BUILTINS
        and not allow_sealed_source_inventory_loader
    ):
        raise ExpertBoundaryViolation(f"{filename}:dynamic_code_forbidden")

    if package_name != "inci_tennis_io":
        if call_names & FORBIDDEN_NETWORK_CALLS:
            raise ExpertBoundaryViolation(
                f"{filename}:concrete_network_call_forbidden"
            )
        if call_names & FORBIDDEN_FILESYSTEM_CALLS:
            raise ExpertBoundaryViolation(
                f"{filename}:filesystem_call_forbidden"
            )
    if package_name in {
        "inci_tennis_expert",
        "inci_tennis_adapters",
        "inci_tennis_runtime",
    }:
        rejected_clock_calls = call_names & FORBIDDEN_CLOCK_CALLS
        if package_name == "inci_tennis_runtime" and is_exactly_sealed_source:
            rejected_clock_calls -= SEALED_RUNTIME_FILE_CLOCK_CALLS.get(
                filename,
                frozenset(),
            )
        if rejected_clock_calls:
            raise ExpertBoundaryViolation(f"{filename}:wall_clock_forbidden")
        if (
            call_names & FORBIDDEN_PROCESS_CALLS
            and not (
                package_name == "inci_tennis_runtime"
                and is_exactly_sealed_source
            )
        ):
            raise ExpertBoundaryViolation(f"{filename}:process_call_forbidden")


def verify_sealed_package(package_name: str) -> None:
    package_root = PACKAGE_ROOTS[package_name]
    expected = EXPECTED_PACKAGE_AST_SHA256[package_name]
    expected_resources = EXPECTED_PACKAGE_RESOURCE_SHA256[package_name]
    actual_paths, actual_resources = _package_inventory(package_root)
    expected_paths = tuple(sorted(expected))
    require_exact_inventory(package_name, actual_paths)
    require_exact_resource_inventory(package_name, actual_resources)

    for relative_path in actual_paths:
        path = package_root / relative_path
        source = path.read_text(encoding="utf-8")
        actual_digest = canonical_ast_sha256(source, relative_path)
        if actual_digest != expected[relative_path]:
            raise ExpertBoundaryViolation(
                f"{package_name}/{relative_path}:ast_seal_mismatch:"
                f"{actual_digest}"
            )
        check_source(
            source,
            package_name=package_name,
            filename=relative_path,
        )
    for relative_path in actual_resources:
        actual_digest = hashlib.sha256(
            (package_root / relative_path).read_bytes()
        ).hexdigest()
        if actual_digest != expected_resources[relative_path]:
            raise ExpertBoundaryViolation(
                f"{package_name}/{relative_path}:resource_seal_mismatch:"
                f"{actual_digest}"
            )


def require_exact_inventory(
    package_name: str,
    actual_paths: tuple[str, ...],
) -> None:
    expected_paths = tuple(sorted(EXPECTED_PACKAGE_AST_SHA256[package_name]))
    if actual_paths != expected_paths:
        raise ExpertBoundaryViolation(
            f"{package_name}:package_inventory_mismatch:"
            f"actual={actual_paths!r}:expected={expected_paths!r}"
        )


def require_exact_resource_inventory(
    package_name: str,
    actual_paths: tuple[str, ...],
) -> None:
    expected_paths = tuple(
        sorted(EXPECTED_PACKAGE_RESOURCE_SHA256[package_name])
    )
    if actual_paths != expected_paths:
        raise ExpertBoundaryViolation(
            f"{package_name}:package_resource_inventory_mismatch:"
            f"actual={actual_paths!r}:expected={expected_paths!r}"
        )


def check_legacy_source_for_new_package_imports(
    source: str,
    *,
    filename: str,
) -> None:
    tree = ast.parse(source, filename=filename, type_comments=False)
    imported = tuple(
        module
        for module in _imported_modules(tree)
        if _root_name(module) in NEW_PACKAGE_ROOTS
    )
    if imported:
        raise ExpertBoundaryViolation(
            f"{filename}:new_package_import_forbidden:{imported!r}"
        )


class ExpertDependencyBoundaryTests(unittest.TestCase):
    def assert_source_allowed(
        self,
        source: str,
        *,
        package_name: str,
        filename: str = "fixture.py",
    ) -> None:
        try:
            check_source(
                source,
                package_name=package_name,
                filename=filename,
            )
        except ExpertBoundaryViolation as error:
            self.fail(f"allowed source rejected: {error}")

    def assert_source_rejected(
        self,
        source: str,
        *,
        package_name: str,
        filename: str = "fixture.py",
    ) -> None:
        with self.assertRaises(ExpertBoundaryViolation):
            check_source(
                source,
                package_name=package_name,
                filename=filename,
            )

    def test_live_paper_modules_are_ast_sealed_and_capability_free(self) -> None:
        """Catches a paper bridge gaining a client, route, or mutation verb."""
        forbidden_text = {
            "kalshiclient",
            "executor",
            "create_order",
            "cancel_order",
            "/portfolio/",
            "--live",
            "--demo",
        }
        for filename, expected_digest in LIVE_PAPER_MODULE_AST_SHA256.items():
            with self.subTest(filename=filename):
                source = (PACKAGE_ROOTS["inci_tennis_expert"] / filename).read_text(
                    encoding="utf-8"
                )
                self.assertEqual(canonical_ast_sha256(source, filename), expected_digest)
                tree = ast.parse(source, filename=filename, type_comments=False)
                bindings = _imported_bindings(tree)
                self.assertFalse(
                    {
                        binding
                        for binding in bindings
                        if binding.endswith(".KalshiClient")
                        or binding.endswith(".Executor")
                        or binding in {"KalshiClient", "Executor", "executor"}
                    }
                )
                strings = {value.lower() for value in _folded_static_strings(tree)}
                self.assertFalse(strings & forbidden_text)

    def test_four_packages_have_independent_ast_seals(self) -> None:
        self.assertEqual(set(EXPECTED_PACKAGE_AST_SHA256), set(PACKAGE_ROOTS))
        self.assertEqual(
            set(EXPECTED_PACKAGE_RESOURCE_SHA256),
            set(PACKAGE_ROOTS),
        )
        for package_name in PACKAGE_ROOTS:
            with self.subTest(package_name=package_name):
                self.assertTrue(
                    (PACKAGE_ROOTS[package_name] / "__init__.py").is_file()
                )
                verify_sealed_package(package_name)
                self.assertIsNot(
                    EXPECTED_PACKAGE_AST_SHA256[package_name],
                    EXPECTED_PACKAGE_AST_SHA256[
                        next(
                            other
                            for other in PACKAGE_ROOTS
                            if other != package_name
                        )
                    ],
                )

    def test_task8_environment_inventory_closes_candidate_sources(self) -> None:
        from inci_tennis_io import expert_journal_store

        expected_io = (
            "inci_tennis_io/__init__.py",
            "inci_tennis_io/pinned_artifacts.py",
            "inci_tennis_io/ports.py",
            "inci_tennis_io/expert_journal_store.py",
            "inci_tennis_io/facade.py",
            "inci_tennis_io/kalshi_readonly.py",
            "inci_tennis_io/kalshi_shadow_catalog.py",
            "inci_tennis_io/kalshi_shadow_settlement.py",
            "inci_tennis_io/provider_readonly.py",
            "inci_tennis_io/shadow_evidence.py",
            "inci_tennis_io/shadow_settlement_labels.py",
            "inci_tennis_io/sportradar_shadow_async.py",
            "inci_tennis_io/sportradar_trial_transport.py",
            "inci_tennis_io/account_lock.py",
        )
        expected_task7_adapters = (
            "inci_tennis_adapters/__init__.py",
            "inci_tennis_adapters/candidate_contracts.py",
            "inci_tennis_adapters/kalshi_v2.py",
            "inci_tennis_adapters/live_score_candidates.py",
            "inci_tennis_adapters/registry.py",
            "inci_tennis_adapters/shadow_discovery_contracts.py",
            "inci_tennis_adapters/shadow_match_chooser.py",
            "inci_tennis_adapters/shadow_provider_coverage.py",
            "inci_tennis_adapters/sportradar_tennis_v3.py",
            "inci_tennis_adapters/sportradar_trial_v3.py",
        )
        expected_task7_resources = (
            (
                "inci_tennis_adapters/schemas/"
                "sportradar-tennis-summary-v3-candidate-v1.schema.json"
            ),
            (
                "inci_tennis_adapters/schemas/"
                "sportradar-tennis-timeline-v3-candidate-v1.schema.json"
            ),
            (
                "inci_tennis_adapters/schemas/"
                "sportradar-tennis-transport-error-v1.schema.json"
            ),
            (
                "inci_tennis_adapters/schemas/"
                "sportradar-tennis-candidate-manifest-v1.schema.json"
            ),
            (
                "inci_tennis_adapters/schemas/"
                "sportradar-tennis-candidate-authorization-v1.schema.json"
            ),
            (
                "inci_tennis_adapters/schemas/"
                "sportradar-tennis-qualification-output-v1.schema.json"
            ),
        )
        expected_kalshi = (
            "inci_tennis_adapters/kalshi_candidate.py",
            (
                "inci_tennis_adapters/schemas/"
                "kalshi-orderbook-snapshot-synthetic-candidate-v1.schema.json"
            ),
            (
                "inci_tennis_adapters/schemas/"
                "kalshi-orderbook-delta-synthetic-candidate-v1.schema.json"
            ),
            (
                "inci_tennis_adapters/schemas/"
                "kalshi-market-lifecycle-synthetic-candidate-v1.schema.json"
            ),
            (
                "inci_tennis_adapters/schemas/"
                "kalshi-public-trade-synthetic-candidate-v1.schema.json"
            ),
        )
        expected_adapters = (
            *expected_task7_adapters,
            *expected_kalshi,
        )
        expected_runtime = (
            "inci_tennis_runtime/__init__.py",
            "inci_tennis_runtime/expert_controller.py",
            "inci_tennis_runtime/live_price_only_collector.py",
            "inci_tennis_runtime/live_shadow_cli.py",
            "inci_tennis_runtime/live_shadow_collector.py",
            "inci_tennis_runtime/replay_service.py",
            (
                "inci_tennis_runtime/"
                "provider_qualification_controller.py"
            ),
            "inci_tennis_runtime/shadow_runtime.py",
            "inci_tennis_runtime/shadow_cli.py",
            "inci_tennis_runtime/shadow_settlement_cli.py",
            "inci_tennis_runtime/sportradar_trial_cli.py",
        )
        self.assertEqual(expert_journal_store._IO_INVENTORY, expected_io)
        self.assertEqual(
            expert_journal_store._ADAPTER_INVENTORY,
            expected_adapters,
        )
        self.assertEqual(
            expert_journal_store._KALSHI_CANDIDATE_ADAPTER_INVENTORY,
            expected_kalshi,
        )
        self.assertEqual(
            expert_journal_store._KALSHI_CANDIDATE_SCHEMA_INVENTORY,
            expected_kalshi[1:],
        )
        self.assertEqual(
            expert_journal_store._CANDIDATE_SCHEMA_INVENTORY,
            expected_task7_resources,
        )
        self.assertEqual(
            expert_journal_store._RUNTIME_INVENTORY,
            expected_runtime,
        )
        self.assertEqual(
            expert_journal_store._SOURCE_PACKAGE_INVENTORIES[
                "inci_tennis_adapters"
            ],
            tuple(sorted((*expected_adapters, *expected_task7_resources))),
        )
        self.assertEqual(
            len(expert_journal_store._ADAPTER_INVENTORY),
            len(set(expert_journal_store._ADAPTER_INVENTORY)),
        )
        source_inventory = (
            expert_journal_store._SOURCE_PACKAGE_INVENTORIES[
                "inci_tennis_adapters"
            ]
        )
        self.assertEqual(len(source_inventory), len(set(source_inventory)))

    def test_task8_package_maps_pin_exact_sources_and_resources(self) -> None:
        expected_python = {
            "inci_tennis_io": {
                "__init__.py",
                "account_lock.py",
                "expert_journal_store.py",
                "facade.py",
                "kalshi_readonly.py",
                "kalshi_shadow_catalog.py",
                "kalshi_shadow_settlement.py",
                "pinned_artifacts.py",
                "ports.py",
                "provider_readonly.py",
                "shadow_evidence.py",
                "shadow_settlement_labels.py",
                "sportradar_shadow_async.py",
                "sportradar_trial_transport.py",
            },
            "inci_tennis_adapters": {
                "__init__.py",
                "candidate_contracts.py",
                "kalshi_candidate.py",
                "kalshi_v2.py",
                "live_score_candidates.py",
                "registry.py",
                "shadow_discovery_contracts.py",
                "shadow_match_chooser.py",
                "shadow_provider_coverage.py",
                "sportradar_tennis_v3.py",
                "sportradar_trial_v3.py",
            },
            "inci_tennis_runtime": {
                "__init__.py",
                "expert_controller.py",
                "live_price_only_collector.py",
                "live_shadow_cli.py",
                "live_shadow_collector.py",
                "provider_qualification_controller.py",
                "replay_service.py",
                "shadow_cli.py",
                "shadow_runtime.py",
                "shadow_settlement_cli.py",
                "sportradar_trial_cli.py",
            },
        }
        expected_adapter_resources = {
            (
                "schemas/"
                "kalshi-market-lifecycle-synthetic-candidate-v1.schema.json"
            ),
            (
                "schemas/"
                "kalshi-orderbook-delta-synthetic-candidate-v1.schema.json"
            ),
            (
                "schemas/"
                "kalshi-orderbook-snapshot-synthetic-candidate-v1.schema.json"
            ),
            (
                "schemas/"
                "kalshi-public-trade-synthetic-candidate-v1.schema.json"
            ),
            (
                "schemas/"
                "sportradar-tennis-summary-v3-candidate-v1.schema.json"
            ),
            (
                "schemas/"
                "sportradar-tennis-timeline-v3-candidate-v1.schema.json"
            ),
            "schemas/sportradar-tennis-transport-error-v1.schema.json",
            "schemas/sportradar-tennis-candidate-manifest-v1.schema.json",
            (
                "schemas/"
                "sportradar-tennis-candidate-authorization-v1.schema.json"
            ),
            (
                "schemas/"
                "sportradar-tennis-qualification-output-v1.schema.json"
            ),
        }
        for package_name, paths in expected_python.items():
            with self.subTest(package_name=package_name):
                actual_python, actual_resources = _package_inventory(
                    PACKAGE_ROOTS[package_name]
                )
                self.assertEqual(set(actual_python), paths)
                self.assertEqual(
                    set(EXPECTED_PACKAGE_AST_SHA256[package_name]),
                    paths,
                )
                self.assertEqual(
                    set(actual_resources),
                    (
                        expected_adapter_resources
                        if package_name == "inci_tennis_adapters"
                        else set()
                    ),
                )
        self.assertEqual(
            set(EXPECTED_PACKAGE_RESOURCE_SHA256["inci_tennis_adapters"]),
            expected_adapter_resources,
        )

    def test_task8_special_imports_require_exact_reviewed_source(self) -> None:
        reviewed = (
            ("inci_tennis_io", "expert_journal_store.py"),
            ("inci_tennis_io", "kalshi_shadow_catalog.py"),
            ("inci_tennis_io", "kalshi_shadow_settlement.py"),
            ("inci_tennis_io", "provider_readonly.py"),
            ("inci_tennis_io", "sportradar_shadow_async.py"),
            ("inci_tennis_adapters", "candidate_contracts.py"),
            ("inci_tennis_adapters", "kalshi_candidate.py"),
            ("inci_tennis_adapters", "registry.py"),
            ("inci_tennis_adapters", "shadow_discovery_contracts.py"),
            ("inci_tennis_adapters", "shadow_match_chooser.py"),
            ("inci_tennis_adapters", "shadow_provider_coverage.py"),
            ("inci_tennis_adapters", "sportradar_tennis_v3.py"),
            (
                "inci_tennis_runtime",
                "provider_qualification_controller.py",
            ),
            ("inci_tennis_runtime", "live_price_only_collector.py"),
            ("inci_tennis_runtime", "live_shadow_cli.py"),
            ("inci_tennis_runtime", "live_shadow_collector.py"),
            ("inci_tennis_runtime", "shadow_cli.py"),
            ("inci_tennis_runtime", "shadow_runtime.py"),
            ("inci_tennis_runtime", "shadow_settlement_cli.py"),
        )
        for package_name, filename in reviewed:
            source = (PACKAGE_ROOTS[package_name] / filename).read_text(
                encoding="utf-8"
            )
            with self.subTest(package_name=package_name, filename=filename):
                self.assert_source_allowed(
                    source,
                    package_name=package_name,
                    filename=filename,
                )
                self.assert_source_rejected(
                    source,
                    package_name=package_name,
                    filename=f"copied-{filename}",
                )
                self.assert_source_rejected(
                    source + "\nTASK7_UNREVIEWED_CHANGE = 1\n",
                    package_name=package_name,
                    filename=filename,
                )

    def test_task8_observation_runtime_rejects_expert_authority_after_reseal(
        self,
    ) -> None:
        """Catches a seal refresh approving synchronization authority."""

        source = """
            from inci_tennis_expert.synchronizer import (
                validate_synchronization_transition,
            )

            def collect(*args, **kwargs):
                return validate_synchronization_transition(*args, **kwargs)
        """
        normalized = textwrap.dedent(source)
        expected = EXPECTED_PACKAGE_AST_SHA256["inci_tennis_runtime"]
        for filename in sorted(TASK8_OBSERVATION_ONLY_RUNTIME_FILES):
            with self.subTest(filename=filename):
                original = expected[filename]
                expected[filename] = canonical_ast_sha256(normalized, filename)
                try:
                    with self.assertRaisesRegex(
                        ExpertBoundaryViolation,
                        "runtime_expert_import_forbidden",
                    ):
                        check_source(
                            normalized,
                            package_name="inci_tennis_runtime",
                            filename=filename,
                        )
                finally:
                    expected[filename] = original

    def test_task8_observation_runtime_rejects_expert_root_alias_after_reseal(
        self,
    ) -> None:
        """Catches a package-root alias bypassing expert import rejection."""

        source = """
            import inci_tennis_expert as expert

            def collect(*args, **kwargs):
                return expert.synchronizer.validate_synchronization_transition(
                    *args,
                    **kwargs,
                )
        """
        normalized = textwrap.dedent(source)
        expected = EXPECTED_PACKAGE_AST_SHA256["inci_tennis_runtime"]
        for filename in sorted(TASK8_OBSERVATION_ONLY_RUNTIME_FILES):
            with self.subTest(filename=filename):
                original = expected[filename]
                expected[filename] = canonical_ast_sha256(normalized, filename)
                try:
                    with self.assertRaisesRegex(
                        ExpertBoundaryViolation,
                        "runtime_expert_import_forbidden",
                    ):
                        check_source(
                            normalized,
                            package_name="inci_tennis_runtime",
                            filename=filename,
                        )
                finally:
                    expected[filename] = original

    def test_task8_observation_runtime_rejects_sync_peer_after_reseal(
        self,
    ) -> None:
        """Catches synchronization authority re-exported by a runtime peer."""

        source = """
            from inci_tennis_runtime.shadow_runtime import (
                validate_synchronization_transition,
            )

            def collect(*args, **kwargs):
                return validate_synchronization_transition(*args, **kwargs)
        """
        normalized = textwrap.dedent(source)
        expected = EXPECTED_PACKAGE_AST_SHA256["inci_tennis_runtime"]
        for filename in sorted(TASK8_OBSERVATION_ONLY_RUNTIME_FILES):
            with self.subTest(filename=filename):
                original = expected[filename]
                expected[filename] = canonical_ast_sha256(normalized, filename)
                try:
                    with self.assertRaisesRegex(
                        ExpertBoundaryViolation,
                        "runtime_peer_import_forbidden",
                    ):
                        check_source(
                            normalized,
                            package_name="inci_tennis_runtime",
                            filename=filename,
                        )
                finally:
                    expected[filename] = original

    def test_task8_observation_runtime_rejects_io_root_alias_after_reseal(
        self,
    ) -> None:
        """Catches a package-root alias bypassing the exact IO allowlist."""

        source = """
            import inci_tennis_io as evidence_io

            def collect(*args, **kwargs):
                return evidence_io.expert_journal_store.append(*args, **kwargs)
        """
        normalized = textwrap.dedent(source)
        expected = EXPECTED_PACKAGE_AST_SHA256["inci_tennis_runtime"]
        for filename in sorted(TASK8_OBSERVATION_ONLY_RUNTIME_FILES):
            with self.subTest(filename=filename):
                original = expected[filename]
                expected[filename] = canonical_ast_sha256(normalized, filename)
                try:
                    with self.assertRaisesRegex(
                        ExpertBoundaryViolation,
                        "runtime_io_import_forbidden",
                    ):
                        check_source(
                            normalized,
                            package_name="inci_tennis_runtime",
                            filename=filename,
                        )
                finally:
                    expected[filename] = original

    def test_task8_observation_runtime_rejects_relative_imports_after_reseal(
        self,
    ) -> None:
        """Catches relative imports escaping exact runtime-peer review."""

        sources = (
            """
                from .shadow_runtime import validate_synchronization_transition

                def collect(*args, **kwargs):
                    return validate_synchronization_transition(*args, **kwargs)
            """,
            """
                from . import shadow_runtime

                def collect(*args, **kwargs):
                    return shadow_runtime.validate_synchronization_transition(
                        *args,
                        **kwargs,
                    )
            """,
            """
                from .shadow_runtime import *

                def collect(*args, **kwargs):
                    return validate_synchronization_transition(*args, **kwargs)
            """,
        )
        expected = EXPECTED_PACKAGE_AST_SHA256["inci_tennis_runtime"]
        for index, source in enumerate(sources):
            normalized = textwrap.dedent(source)
            for filename in sorted(TASK8_OBSERVATION_ONLY_RUNTIME_FILES):
                with self.subTest(index=index, filename=filename):
                    original = expected[filename]
                    expected[filename] = canonical_ast_sha256(
                        normalized,
                        filename,
                    )
                    try:
                        with self.assertRaisesRegex(
                            ExpertBoundaryViolation,
                            "runtime_relative_import_forbidden",
                        ):
                            check_source(
                                normalized,
                                package_name="inci_tennis_runtime",
                                filename=filename,
                            )
                    finally:
                        expected[filename] = original

    def test_task8_observation_runtime_rejects_privileged_io_reexports(
        self,
    ) -> None:
        """Catches journal authority hidden behind facade/ports re-exports."""

        sources = (
            "from inci_tennis_io.facade import append_expert_group\n",
            "from inci_tennis_io.facade import issue_expert_append_permit\n",
            "from inci_tennis_io.ports import ExpertJournalAppendPermitV1\n",
        )
        expected = EXPECTED_PACKAGE_AST_SHA256["inci_tennis_runtime"]
        for index, source in enumerate(sources):
            for filename in sorted(TASK8_OBSERVATION_ONLY_RUNTIME_FILES):
                with self.subTest(index=index, filename=filename):
                    original = expected[filename]
                    expected[filename] = canonical_ast_sha256(source, filename)
                    try:
                        with self.assertRaisesRegex(
                            ExpertBoundaryViolation,
                            "runtime_io_import_forbidden",
                        ):
                            check_source(
                                source,
                                package_name="inci_tennis_runtime",
                                filename=filename,
                            )
                    finally:
                        expected[filename] = original

    def test_task7_adapters_reject_unreviewed_phase_one_bindings(self) -> None:
        added_import = (
            "\nfrom tennis_v1.capture import issue_capture_authority\n"
        )
        for filename in (
            "candidate_contracts.py",
            "registry.py",
            "sportradar_tennis_v3.py",
        ):
            source = (
                PACKAGE_ROOTS["inci_tennis_adapters"] / filename
            ).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assert_source_rejected(
                    source + added_import,
                    package_name="inci_tennis_adapters",
                    filename=filename,
                )

    def test_task7_tool_requires_exact_ast_and_io_policy(self) -> None:
        relative_path = "tools/qualify_sportradar_tennis_v3.py"
        source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        self.assert_source_allowed(
            source,
            package_name="inci_tennis_io",
            filename=relative_path,
        )
        self.assert_source_rejected(
            source + "\nTASK7_UNREVIEWED_CHANGE = 1\n",
            package_name="inci_tennis_io",
            filename=relative_path,
        )
        self.assert_source_rejected(
            "import socket\n",
            package_name="inci_tennis_io",
            filename=relative_path,
        )

    def test_package_seals_reject_added_removed_and_changed_modules(self) -> None:
        for package_name in PACKAGE_ROOTS:
            with self.subTest(package_name=package_name):
                expected = EXPECTED_PACKAGE_AST_SHA256[package_name]
                self.assertIn("__init__.py", expected)
                for relative_path, expected_digest in expected.items():
                    self.assertNotEqual(
                        canonical_ast_sha256(
                            "VALUE = 1\n",
                            relative_path,
                        ),
                        expected_digest,
                    )
                with self.assertRaises(ExpertBoundaryViolation):
                    require_exact_inventory(
                        package_name,
                        tuple(sorted((*expected, "unreviewed.py"))),
                    )
                with self.assertRaises(ExpertBoundaryViolation):
                    require_exact_inventory(package_name, ())

    def test_package_resources_are_raw_sealed_and_exactly_inventoried(
        self,
    ) -> None:
        for package_name, expected in (
            EXPECTED_PACKAGE_RESOURCE_SHA256.items()
        ):
            with self.subTest(package_name=package_name):
                package_root = PACKAGE_ROOTS[package_name]
                _, actual_paths = _package_inventory(package_root)
                require_exact_resource_inventory(
                    package_name,
                    actual_paths,
                )
                for relative_path, expected_digest in expected.items():
                    actual_digest = hashlib.sha256(
                        (package_root / relative_path).read_bytes()
                    ).hexdigest()
                    self.assertEqual(actual_digest, expected_digest)
                    self.assertNotEqual(
                        hashlib.sha256(b"changed").hexdigest(),
                        expected_digest,
                    )
                with self.assertRaises(ExpertBoundaryViolation):
                    require_exact_resource_inventory(
                        package_name,
                        tuple(sorted((*expected, "unreviewed.resource"))),
                    )

    def test_package_inventory_rejects_every_cache_and_bytecode_entry(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            package_root = Path(temporary) / "sealed"
            package_root.mkdir()
            (package_root / "__init__.py").write_text(
                "\n",
                encoding="utf-8",
            )
            cache_root = package_root / "__pycache__"
            cache_root.mkdir()
            (cache_root / "module.cpython-314.pyc").write_bytes(b"hostile")
            with self.assertRaisesRegex(
                ExpertBoundaryViolation,
                "package_bytecode_forbidden",
            ):
                _package_inventory(package_root)

        with TemporaryDirectory() as temporary:
            package_root = Path(temporary) / "sealed"
            package_root.mkdir()
            (package_root / "__init__.py").write_text(
                "\n",
                encoding="utf-8",
            )
            (package_root / "module.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            (package_root / "module.pyc").write_bytes(b"hostile")
            with self.assertRaisesRegex(
                ExpertBoundaryViolation,
                "package_bytecode_forbidden",
            ):
                _package_inventory(package_root)

    def test_new_package_roots_export_no_production_registry(self) -> None:
        for package_name, package_root in PACKAGE_ROOTS.items():
            with self.subTest(package_name=package_name):
                init_path = package_root / "__init__.py"
                self.assertTrue(init_path.is_file())
                tree = ast.parse(
                    init_path.read_text(encoding="utf-8"),
                    filename=f"{package_name}/__init__.py",
                )
                assigned_names = {
                    target.id
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.Assign, ast.AnnAssign))
                    for target in (
                        node.targets if isinstance(node, ast.Assign) else (node.target,)
                    )
                    if isinstance(target, ast.Name)
                }
                self.assertEqual(assigned_names, set())

    def test_phase_one_and_root_v6_import_none_of_the_new_packages(self) -> None:
        for path in sorted(PHASE_ONE_ROOT.rglob("*.py")):
            check_legacy_source_for_new_package_imports(
                path.read_text(encoding="utf-8"),
                filename=path.relative_to(REPOSITORY_ROOT).as_posix(),
            )
        for relative_path in ROOT_V6_PYTHON_PATHS:
            path = REPOSITORY_ROOT / relative_path
            check_legacy_source_for_new_package_imports(
                path.read_text(encoding="utf-8"),
                filename=relative_path,
            )

    def test_phase_one_import_of_expert_package_fails_boundary(self) -> None:
        with self.assertRaisesRegex(
            ExpertBoundaryViolation,
            r"\Atennis_v1/capture.py:new_package_import_forbidden:",
        ):
            check_legacy_source_for_new_package_imports(
                "from inci_tennis_expert import engine",
                filename="tennis_v1/capture.py",
            )

    def test_forbidden_capability_imports_are_rejected_by_package(self) -> None:
        cases = (
            ("inci_tennis_expert", "import requests"),
            ("inci_tennis_expert", "from pathlib import Path"),
            ("inci_tennis_expert", "import subprocess"),
            ("inci_tennis_expert", "import time"),
            ("inci_tennis_expert", "import random"),
            ("inci_tennis_expert", "import keyring"),
            ("inci_tennis_adapters", "import socket"),
            ("inci_tennis_adapters", "from inci_tennis_io import facade"),
            ("inci_tennis_adapters", "from inci_tennis_expert import policy"),
            ("inci_tennis_adapters", "from inci_tennis_expert import risk"),
            ("inci_tennis_adapters", "from inci_tennis_expert import scorecard"),
            ("inci_tennis_runtime", "import requests"),
            ("inci_tennis_runtime", "from inci_tennis_adapters import parser"),
            ("inci_tennis_runtime", "from inci_tennis_expert import policy"),
            ("inci_tennis_runtime", "from inci_tennis_io import journal"),
        )
        for package_name, source in cases:
            with self.subTest(package_name=package_name, source=source):
                self.assert_source_rejected(
                    source,
                    package_name=package_name,
                )

    def test_frozen_root_v6_imports_are_rejected_everywhere(self) -> None:
        frozen_stems = tuple(
            Path(relative_path).stem
            for relative_path in ROOT_V6_PYTHON_PATHS
        )
        for package_name in PACKAGE_ROOTS:
            for module_name in frozen_stems:
                for source in (
                    f"import {module_name}",
                    f"from {module_name} import FrozenV6Symbol",
                ):
                    with self.subTest(
                        package_name=package_name,
                        module_name=module_name,
                        source=source,
                    ):
                        self.assert_source_rejected(
                            source,
                            package_name=package_name,
                        )

    def test_io_import_surface_is_exact_and_read_only(self) -> None:
        allowed = (
            "import requests",
            "import websockets",
            "import cryptography",
            "from tennis_v1.capture import validate_captured_input",
            (
                "from tennis_v1.capture "
                "import validate_captured_input as validate"
            ),
            "from inci_tennis_expert.contracts import WireEvent",
            (
                "from inci_tennis_expert.facade "
                "import begin_expert_replay"
            ),
            (
                "from inci_tennis_expert.facade "
                "import finish_expert_replay"
            ),
            (
                "from inci_tennis_expert.facade "
                "import replay_expert_parent_group"
            ),
        )
        for source in allowed:
            with self.subTest(source=source):
                check_source(
                    source,
                    package_name="inci_tennis_io",
                    filename="fixture.py",
                )

        rejected = (
            "import tennis_v1",
            "import tennis_v1.wal",
            "from tennis_v1 import capture",
            "from tennis_v1.capture import *",
            "from tennis_v1.capture import CapturedInput",
            "from tennis_v1.wal import JournalWriter",
            "from inci_tennis_expert.policy import PaperPolicy",
            "from inci_tennis_expert.risk import RiskGate",
            "from inci_tennis_expert.facade import ExpertEngine",
            "from inci_tennis_expert.facade import normalize_expert_parent",
            "from inci_tennis_runtime import paper_runtime",
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_rejects_named_http_mutation_calls(self) -> None:
        cases = (
            "import requests\nrequests.post('/markets')",
            "import requests\nrequests.put('/markets/one')",
            "import requests\nrequests.patch('/markets/one')",
            "import requests\nrequests.delete('/markets/one')",
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.post('/markets')"
            ),
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.put('/markets/one')"
            ),
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.patch('/markets/one')"
            ),
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.delete('/markets/one')"
            ),
            "from requests import post as retrieve\nretrieve('/markets')",
            "import requests\nverb = requests.post\nverb('/markets')",
            "import requests\nrequests.head('/markets')",
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.options('/markets')"
            ),
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_generic_request_requires_static_exact_get(self) -> None:
        allowed = (
            "import requests\nrequests.get('/markets')",
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.get('/markets')"
            ),
            "import requests\nrequests.request('GET', '/markets')",
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.request(method='GET', url='/markets')"
            ),
            "from requests import get as retrieve\nretrieve('/markets')",
            (
                "import requests\n"
                "retrieve = requests.get\n"
                "retrieve('/markets')"
            ),
            (
                "from requests import request as retrieve\n"
                "retrieve('GET', '/markets')"
            ),
        )
        for source in allowed:
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_io",
                )

        rejected = (
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.request(method, '/markets')"
            ),
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.request('POST', '/markets')"
            ),
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.request('get', '/markets')"
            ),
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.request(url='/markets')"
            ),
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.request(method=f'{verb}', url='/markets')"
            ),
            (
                "from requests import request as retrieve\n"
                "retrieve('POST', '/markets')"
            ),
            (
                "import requests\n"
                "retrieve = requests.request\n"
                "retrieve(method, '/markets')"
            ),
            (
                "import requests\n"
                "retrieve = getattr(requests, method)\n"
                "retrieve('/markets')"
            ),
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_tracks_requests_receiver_provenance_without_name_guesses(
        self,
    ) -> None:
        allowed = (
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.get('/markets')"
            ),
            (
                "from requests import api\n"
                "api.get('/markets')"
            ),
            (
                "from requests import sessions\n"
                "sessions.Session().get('/markets')"
            ),
            (
                "import requests\n"
                "Factory = requests.Session\n"
                "Factory().get('/markets')"
            ),
            """
                import requests

                class Client:
                    def __init__(self):
                        self.session = requests.Session()
                        self.session.trust_env = False

                    def retrieve(self):
                        try:
                            return self.session.get('/markets')
                        finally:
                            self.session.close()
            """,
        )
        for source in allowed:
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_io",
                )

        rejected = (
            """
                import requests

                class Client:
                    def __init__(self):
                        self.session = requests.Session()
                        self.session.trust_env = True

                    def retrieve(self):
                        return self.session.get('/markets')
            """,
            (
                "from requests import api\n"
                "api.post('/markets')"
            ),
            (
                "from requests import sessions\n"
                "sessions.Session().post('/markets')"
            ),
            (
                "import requests\n"
                "Factory = requests.Session\n"
                "Factory().post('/markets')"
            ),
            """
                import requests

                class Client:
                    def __init__(self):
                        self.session = requests.Session()

                    def submit(self):
                        return self.session.post('/markets')
            """,
            """
                import requests

                class Client:
                    def __init__(self):
                        self.session = requests.Session()

                    def submit(self):
                        return self.session.request('POST', '/markets')
            """,
            (
                "import requests\n"
                "def invoke(client=requests):\n"
                "    client.post('/markets')"
            ),
            "import requests\n[requests][0].post('/markets')",
            "import requests\nstored = [requests]",
            (
                "import requests\n"
                "def invoke(client=requests):\n"
                "    return client"
            ),
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_class_members_share_one_requests_session_provenance(
        self,
    ) -> None:
        allowed = (
            """
                import requests

                class Client:
                    session = requests.Session()

                    def retrieve(self):
                        return self.session.get('/markets')
            """,
            """
                import requests

                class Client:
                    session = requests.Session()

                    @classmethod
                    def retrieve(cls):
                        return cls.session.request('GET', '/markets')
            """,
        )
        for source in allowed:
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_io",
                )

        rejected = (
            """
                import requests

                class Client:
                    session = requests.Session()

                    def mutate(self):
                        return self.session.post('/markets')
            """,
            """
                import requests

                class Client:
                    session = requests.Session()

                    def mutate(self):
                        return self.session.request('POST', '/markets')
            """,
            """
                import requests

                class Client:
                    session = requests.Session()

                    @classmethod
                    def mutate(cls):
                        return cls.session.post('/markets')
            """,
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_class_name_and_alias_share_requests_member_provenance(
        self,
    ) -> None:
        allowed = (
            """
                import requests

                class Client:
                    transport = requests.Session()

                Client.transport.get('/markets')
            """,
            """
                import requests

                class Client:
                    transport = requests.Session()

                Alias = Client
                Alias.transport.request('GET', '/markets')
            """,
        )
        for source in allowed:
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_io",
                )

        rejected = (
            """
                import requests

                class Client:
                    transport = requests.Session()

                Client.transport.post('/markets')
            """,
            """
                import requests

                class Client:
                    transport = requests.Session()

                Client.transport.request('POST', '/markets')
            """,
            """
                import requests

                class Client:
                    transport = requests.Session()

                Alias = Client
                Alias.transport.post('/markets')
            """,
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_class_name_and_alias_share_asyncio_member_provenance(
        self,
    ) -> None:
        rejected = (
            """
                import asyncio

                class Client:
                    loop = asyncio.get_event_loop()

                Client.loop.create_connection(factory, host, port)
            """,
            """
                import asyncio

                class Client:
                    loop = asyncio.get_event_loop()

                Alias = Client
                Alias.loop.create_server(factory, host, port)
            """,
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_constructed_classes_share_requests_member_provenance(
        self,
    ) -> None:
        allowed = (
            """
                import requests

                class Client:
                    transport = requests.Session()

                Client().transport.get('/markets')
            """,
            """
                import requests

                class Client:
                    transport = requests.Session()

                Alias = Client
                Alias().transport.request('GET', '/markets')
            """,
            """
                import requests

                class Client:
                    transport = requests.Session()

                client = Client()
                client.transport.get('/markets')
            """,
            """
                class Transport:
                    def post(self, route):
                        return route

                    def request(self, method, route):
                        return method, route

                class Client:
                    transport = Transport()

                Alias = Client
                client = Client()
                Client().transport.post('/markets')
                Alias().transport.request('POST', '/markets')
                client.transport.post('/markets')
            """,
        )
        for source in allowed:
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_io",
                )

        rejected = (
            """
                import requests

                class Client:
                    transport = requests.Session()

                Client().transport.post('/markets')
            """,
            """
                import requests

                class Client:
                    transport = requests.Session()

                Client().transport.request('POST', '/markets')
            """,
            """
                import requests

                class Client:
                    transport = requests.Session()

                Alias = Client
                Alias().transport.post('/markets')
            """,
            """
                import requests

                class Client:
                    transport = requests.Session()

                client = Client()
                client.transport.request('POST', '/markets')
            """,
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_constructed_classes_share_asyncio_member_provenance(
        self,
    ) -> None:
        allowed = (
            """
                import asyncio

                class Client:
                    loop = asyncio.get_event_loop()

                Alias = Client
                client = Client()
                Client().loop.call_soon(callback)
                Alias().loop.create_task(coro)
                client.loop.call_later(1, callback)
            """,
            """
                class Loop:
                    def create_connection(self, *args):
                        return args

                    def create_server(self, *args):
                        return args

                class Client:
                    loop = Loop()

                Alias = Client
                client = Client()
                Client().loop.create_connection(factory, host, port)
                Alias().loop.create_server(factory, host, port)
                client.loop.create_connection(factory, host, port)
            """,
        )
        for source in allowed:
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_io",
                )

        rejected = (
            """
                import asyncio

                class Client:
                    loop = asyncio.get_event_loop()

                Client().loop.create_connection(factory, host, port)
            """,
            """
                import asyncio

                class Client:
                    loop = asyncio.get_event_loop()

                Alias = Client
                Alias().loop.create_server(factory, host, port)
            """,
            """
                import asyncio

                class Client:
                    loop = asyncio.get_event_loop()

                client = Client()
                client.loop.create_unix_connection(factory, path)
            """,
            """
                import asyncio

                class Client:
                    loop = asyncio.get_event_loop()

                Alias = Client
                client = Alias()
                client.loop.create_unix_server(factory, path)
            """,
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_requests_annotations_are_not_runtime_authority(self) -> None:
        allowed = (
            """
                import requests

                session: requests.Session

                def identity(
                    value: requests.Session,
                ) -> requests.Session:
                    return value
            """,
            """
                from __future__ import annotations

                import requests

                class Client:
                    session: requests.Session

                    def identity(
                        self,
                        value: requests.Session,
                    ) -> requests.Session:
                        return value
            """,
            """
                import requests

                def identity[T: requests.Session](value: T) -> T:
                    return value
            """,
            """
                import requests

                session: requests.Session = requests.Session()
                session.get('/markets')
            """,
        )
        for source in allowed:
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_io",
                )

        rejected = (
            """
                import requests

                session: requests.Session = requests.Session()
                session.post('/markets')
            """,
            """
                from __future__ import annotations

                import requests

                session: requests.Session = requests.Session()
                session.request('POST', '/markets')
            """,
            """
                import requests

                def mutate(value: requests.Session):
                    return requests.post('/markets')
            """,
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_annotated_parameters_enforce_requests_calls_only(
        self,
    ) -> None:
        allowed = (
            """
                import requests

                def identity(
                    session: requests.Session,
                ) -> requests.Session:
                    return session
            """,
            """
                import requests

                def retrieve(session: requests.Session):
                    return session.get('/markets')
            """,
        )
        for source in allowed:
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_io",
                )

        rejected = (
            """
                import requests

                def mutate(session: requests.Session):
                    return session.post('/markets')
            """,
            """
                import requests

                def mutate(session: requests.Session):
                    return session.request('POST', '/markets')
            """,
            """
                from requests import Session

                def mutate(session: Session):
                    return session.post('/markets')
            """,
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_wrapped_requests_annotations_enforce_calls_only(
        self,
    ) -> None:
        allowed = (
            """
                import requests

                def identity(
                    session: requests.Session | None,
                ) -> requests.Session | None:
                    return session
            """,
            """
                import requests

                def identity(
                    session: None | requests.Session,
                ) -> None | requests.Session:
                    return session
            """,
            """
                from requests import Session
                from typing import Optional

                def retrieve(session: Optional[Session]):
                    return session.get('/markets')
            """,
            """
                import typing as types
                from requests import Session as HttpSession

                def retrieve(
                    session: types.Union[HttpSession, None],
                ):
                    return session.request('GET', '/markets')
            """,
            """
                from requests import Session as HttpSession
                from typing import Annotated as Metadata

                def retrieve(
                    session: Metadata[HttpSession, 'remote'],
                ):
                    return session.get('/markets')
            """,
            """
                from requests import Session
                from typing import Annotated, Optional

                def retrieve(
                    session: Annotated[
                        Optional[Session],
                        'remote',
                    ],
                ):
                    return session.get('/markets')
            """,
            """
                class Optional:
                    pass

                class Session:
                    def post(self, route):
                        return route

                def mutate(session: Optional[Session]):
                    return session.post('/markets')
            """,
        )
        for source in allowed:
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_io",
                )

        rejected = (
            """
                import requests

                def mutate(session: requests.Session | None):
                    return session.post('/markets')
            """,
            """
                import requests

                def mutate(session: None | requests.Session):
                    return session.request('POST', '/markets')
            """,
            """
                import requests
                import typing

                def mutate(
                    session: typing.Optional[requests.Session],
                ):
                    return session.post('/markets')
            """,
            """
                from requests import Session as HttpSession
                from typing import Optional as Maybe

                def mutate(session: Maybe[HttpSession]):
                    return session.request('POST', '/markets')
            """,
            """
                import requests
                import typing

                def mutate(
                    session: typing.Union[
                        requests.Session,
                        None,
                    ],
                ):
                    return session.post('/markets')
            """,
            """
                from requests import Session as HttpSession
                from typing import Union as Choice

                def mutate(
                    session: Choice[HttpSession, None],
                ):
                    return session.request('POST', '/markets')
            """,
            """
                import requests
                import typing

                def mutate(
                    session: typing.Annotated[
                        requests.Session,
                        'remote',
                    ],
                ):
                    return session.post('/markets')
            """,
            """
                from requests import Session as HttpSession
                from typing import Annotated as Metadata

                def mutate(
                    session: Metadata[HttpSession, 'remote'],
                ):
                    return session.request('POST', '/markets')
            """,
            """
                from requests import Session
                from typing import Annotated, Optional

                def mutate(
                    session: Annotated[
                        Optional[Session],
                        'remote',
                    ],
                ):
                    return session.post('/markets')
            """,
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_reviewer_http_callable_escape_catalog_is_rejected(self) -> None:
        cases = (
            (
                "import requests\n"
                "getattr(requests, 'post')('/markets')"
            ),
            "import requests\n[requests.post][0]('/markets')",
            (
                "import requests\n"
                "def invoke(call=requests.post):\n"
                "    call('/markets')"
            ),
            (
                "import requests\n"
                "{'write': requests.post}['write']('/markets')"
            ),
            (
                "import functools\n"
                "import requests\n"
                "functools.partial(requests.request, 'POST')('/markets')"
            ),
            (
                "import requests\n"
                "getattr(requests, 'request')('POST', '/markets')"
            ),
            (
                "import urllib.request\n"
                "urllib.request.urlopen('/markets', data=b'body')"
            ),
            (
                "import socket\n"
                "socket.socket().sendall(b'POST /markets HTTP/1.1')"
            ),
            (
                "import requests\n"
                "session = requests.Session()\n"
                "session.send(prepared)"
            ),
            (
                "import requests\n"
                "[requests.request][0]('GET', '/markets')"
            ),
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_source_network_imports_are_exact(self) -> None:
        for source in (
            "import requests",
            "import websockets",
            "import cryptography",
            "import queue",
        ):
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_io",
                )

        rejected = (
            "import aiohttp",
            "import http.client",
            "import httpx\nhttpx.post('/markets')",
            "import socket",
            "import socketserver",
            "import ssl",
            "import urllib.request",
            "import urllib3",
            "import websocket",
            "import certifi",
            "import unknown_vendor_sdk",
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_rejects_raw_asyncio_network_primitives(self) -> None:
        module_primitives = (
            "open_connection",
            "open_unix_connection",
            "start_server",
            "start_unix_server",
        )
        for primitive in module_primitives:
            source = f"""
                import asyncio

                async def probe():
                    return await asyncio.{primitive}(target)
            """
            with self.subTest(primitive=primitive, source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

        loop_primitives = (
            "create_connection",
            "create_unix_connection",
            "create_server",
            "create_unix_server",
        )
        for primitive in loop_primitives:
            source = f"""
                import asyncio

                async def probe():
                    loop = asyncio.get_running_loop()
                    return await loop.{primitive}(factory, target)
            """
            with self.subTest(primitive=primitive, source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

        alias_escapes = (
            """
                from asyncio import open_connection as dial

                async def probe():
                    return await dial(host, port)
            """,
            """
                import asyncio

                async def probe():
                    loop = asyncio.get_event_loop()
                    dial = loop.create_connection
                    return await dial(factory, host, port)
            """,
        )
        for source in alias_escapes:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_allows_non_network_asyncio_and_local_lookalikes(self) -> None:
        allowed = (
            """
                import asyncio

                async def coordinate():
                    queue = asyncio.Queue()
                    await queue.put(item)
                    task = asyncio.create_task(worker())
                    loop = asyncio.get_running_loop()
                    loop.call_soon(callback)
                    return task
            """,
            """
                async def open_connection():
                    return None

                class Builder:
                    def create_connection(self):
                        return None

                Builder().create_connection()
            """,
        )
        for source in allowed:
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_io_allows_local_queue_and_websocket_operations(self) -> None:
        allowed = (
            "import queue\nqueue.Queue().put(item)",
            (
                "import queue\n"
                "session = queue.Queue()\n"
                "session.put(item)"
            ),
            """
                class Session:
                    def send(self, item):
                        return item

                Session().send(item)
            """,
            """
                import websockets

                session = websockets.connect(uri)
                session.send(subscribe_message)
                session.send(update_subscription_message)
                session.send(unsubscribe_message)
                session.recv()
                session.close()
            """,
            """
                import queue
                import requests

                def retrieve():
                    session = requests.Session()
                    return session.get('/markets')

                def enqueue():
                    session = queue.Queue()
                    session.put(item)
            """,
        )
        for source in allowed:
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_io",
                )

    def test_dynamic_import_authority_is_rejected_everywhere(self) -> None:
        cases = (
            (
                "import importlib\n"
                "importlib.import_module('tennis_v1.events')"
            ),
            (
                "from importlib import import_module as load\n"
                "load('tennis_v1.sequencer')"
            ),
            (
                "from builtins import __import__ as load\n"
                "load('tennis_v1.events')"
            ),
            (
                "load = __import__\n"
                "load('tennis_v1.events')"
            ),
            (
                "import builtins\n"
                "load = getattr(builtins, '__import__')\n"
                "load('tennis_v1.events')"
            ),
            (
                "import runpy\n"
                "runpy.run_module('tennis_v1.events')"
            ),
            "loader_name = '__import__'",
            (
                "fn = eval\n"
                "fn('1 + 1')"
            ),
            (
                "fn = exec\n"
                "fn('value = 1')"
            ),
            (
                "fn = compile\n"
                "fn('value = 1', 'fixture.py', 'exec')"
            ),
            (
                "fn = tools.eval\n"
                "fn('1 + 1')"
            ),
            (
                "import pkgutil\n"
                "pkgutil.resolve_name('tennis_v1.events:CapturedInput')"
            ),
            (
                "from pydoc import locate as load\n"
                "load('tennis_v1.events.CapturedInput')"
            ),
            (
                "import zipimport\n"
                "zipimport.zipimporter(path).load_module(module_name)"
            ),
        )
        for package_name in PACKAGE_ROOTS:
            for source in cases:
                with self.subTest(
                    package_name=package_name,
                    source=source,
                ):
                    self.assert_source_rejected(
                        source,
                        package_name=package_name,
                    )

    def test_reflective_dynamic_authority_is_rejected_everywhere(
        self,
    ) -> None:
        cases = (
            """
                module = globals()['__builtins__']['__' + 'import__'](
                    'requests'
                )
                getattr(module, 'po' + 'st')('/markets')
            """,
            """
                import sys

                module = sys.modules['exec' + 'utor']
                getattr(module, 'create_' + 'order')()
            """,
            """
                import sys

                module = sys.modules['safe_' + 'plugin']
                module.describe()
            """,
            """
                loader = tools['__' + 'import__']
                loader('requests')
            """,
            """
                namespace = tools['__built' + 'ins__']
                namespace.describe()
            """,
            """
                fn = getattr(tools, 'ev' + 'al')
                fn('1 + 1')
            """,
        )
        for package_name in PACKAGE_ROOTS:
            for source in cases:
                with self.subTest(
                    package_name=package_name,
                    source=source,
                ):
                    self.assert_source_rejected(
                        source,
                        package_name=package_name,
                    )

    def test_expert_phase_one_import_allowlist_is_exact(self) -> None:
        allowed = (
            "from tennis_v1.events import PersistedEvent",
            "from tennis_v1.events import CapturedInput",
            "from tennis_v1.replay_core import ReplayResult",
            "from tennis_v1.events import PersistedEvent as EvidenceEvent",
        )
        for source in allowed:
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_expert",
                )

        rejected = (
            "import tennis_v1",
            "import tennis_v1.events",
            "from tennis_v1 import events",
            "from tennis_v1.events import *",
            "from tennis_v1.events import PersistedEventFactory",
            "from tennis_v1.events import SessionManifest as PersistedEvent",
            "from tennis_v1.replay_core import replay_exact",
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_expert",
                )

    def test_runtime_phase_one_import_allowlist_is_exact(self) -> None:
        allowed = (
            "from tennis_v1.sequencer import EventRuntime",
            "from tennis_v1.replay_core import replay_exact",
            "from tennis_v1.events import PersistedEvent",
            "from tennis_v1.events import CapturedInput",
            "from tennis_v1.replay_core import ReplayResult",
            "from tennis_v1.sequencer import EventRuntime as EvidenceRuntime",
        )
        for source in allowed:
            with self.subTest(source=source):
                self.assert_source_allowed(
                    source,
                    package_name="inci_tennis_runtime",
                )

        rejected = (
            "import tennis_v1",
            "import tennis_v1.sequencer",
            "from tennis_v1 import sequencer",
            "from tennis_v1.sequencer import *",
            "from tennis_v1.sequencer import ProviderPersistenceAuthorizer",
            "from tennis_v1.replay_core import replay_exactly",
            "from tennis_v1.events import SessionManifest as PersistedEvent",
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_runtime",
                )

    def test_concrete_socket_call_is_rejected_outside_io(self) -> None:
        source = """
            import socket
            connection = socket.socket()
        """
        for package_name in (
            "inci_tennis_expert",
            "inci_tennis_adapters",
            "inci_tennis_runtime",
        ):
            with self.subTest(package_name=package_name):
                self.assert_source_rejected(
                    source,
                    package_name=package_name,
                )

    def test_order_verbs_paths_modes_and_executor_are_rejected_everywhere(self) -> None:
        cases = (
            "def create_order():\n    return None",
            "def cancel_order():\n    return None",
            "def amend_order():\n    return None",
            'ORDER_PATH = "/trade-api/v2/portfolio/orders"',
            "DEMO_MODE = True",
            "LIVE_MODE = True",
            "import executor",
        )
        for package_name in PACKAGE_ROOTS:
            for source in cases:
                with self.subTest(package_name=package_name, source=source):
                    self.assert_source_rejected(
                        source,
                        package_name=package_name,
                    )

    def test_domain_live_enum_is_not_execution_authority(
        self,
    ) -> None:
        self.assert_source_allowed(
            """
                from enum import Enum

                class MatchStatus(str, Enum):
                    SCHEDULED = "scheduled"
                    LIVE = "live"
                    SUSPENDED = "suspended"
                    ENDED = "ended"
                    CANCELLED = "cancelled"

                CURRENT = MatchStatus.LIVE
            """,
            package_name="inci_tennis_expert",
        )

    def test_live_execution_authority_remains_forbidden(self) -> None:
        rejected = (
            "LIVE = True",
            "LIVE_MODE = True",
            "DEMO_MODE = True",
            "live_enabled = True",
            "demo_enabled = True",
            "def create_order():\n    pass",
        )
        for source in rejected:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_expert",
                )

    def test_execution_cli_flags_and_routes_are_rejected_everywhere(
        self,
    ) -> None:
        cases = (
            "FLAG = '--live'",
            "FLAG = '--' + 'demo'",
            "parser.add_argument('--live')",
            "parser.add_argument('--' + 'demo')",
            "handlers = {'--live': handler}",
            "handlers = {'--' + 'demo': handler}",
            "ROUTE = '/portfolio/' + 'orders'",
            "ROUTE = '/portfolio/' + 'positions'",
        )
        for package_name in PACKAGE_ROOTS:
            for source in cases:
                with self.subTest(
                    package_name=package_name,
                    source=source,
                ):
                    self.assert_source_rejected(
                        source,
                        package_name=package_name,
                    )

    def test_runtime_is_composition_only(self) -> None:
        allowed = """
            from inci_tennis_io.ports import ReadOnlyFeed
            from inci_tennis_expert.facade import ExpertEngine

            def sequence(feed: ReadOnlyFeed, engine: ExpertEngine):
                return engine.consume(feed.receive())
        """
        check_source(
            allowed,
            package_name="inci_tennis_runtime",
            filename="fixture.py",
        )

        cases = (
            "def parse_event(value):\n    return value",
            "def risk_gate(value):\n    return value",
            "def simulation(value):\n    return value",
            "def transport(value):\n    return value",
            "open('journal', 'wb')",
            "request('GET', '/markets')",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_source_rejected(
                    source,
                    package_name="inci_tennis_runtime",
                )

    def test_python_is_pinned_exactly_and_dependency_lock_is_unchanged(self) -> None:
        pyproject = tomllib.loads(
            (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(pyproject["project"]["requires-python"], "==3.14.5")
        requirements_digest = hashlib.sha256(
            (REPOSITORY_ROOT / "requirements.txt").read_bytes()
        ).hexdigest()
        self.assertEqual(requirements_digest, EXPECTED_REQUIREMENTS_SHA256)


if __name__ == "__main__":
    unittest.main()
