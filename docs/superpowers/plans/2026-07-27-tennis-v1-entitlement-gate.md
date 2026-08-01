# Tennis v1 Provider Entitlement Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an immutable, machine-enforced gate that prevents Tennis v1
from contacting a sports-data provider unless the exact product tier has
documented research rights, adequate causal capabilities, valid trial dates,
retention coverage, credentials, quotas, and passed qualification evidence.

**Architecture:** A strict local JSON manifest contains permission evidence
references, terms metadata, capability declarations, quotas, and qualification
results but never credentials. A pure evaluator compares that manifest with a
specific requested research session and an injected environment mapping. A
private, fsynced retention marker must be durable before any provider WAL can
exist; one exclusive coordinator authorizes every later byte read and
physically purges due sessions. The only committed provider example is
deliberately ineligible.

**Tech Stack:** Python 3.12+ standard library (`dataclasses`, `datetime`,
`enum`, `hashlib`, `json`, `pathlib`, `typing`, `unittest`).

## Global Constraints

- Work only in the isolated `feature/tennis-v1-foundation` worktree.
- Do not modify existing v6 runtime modules or weaken its 200-test baseline.
- The exact 200-test v6 snapshot is currently uncommitted. Never stage or
  commit those pre-existing user-owned changes from this worktree. Tennis-only
  commits are a patch series until the operator supplies a base commit whose
  21 protected hashes match the baseline manifest; only then may they be
  rebased/cherry-picked for a clean PR.
- Therefore every `git add`/`git commit` checkpoint below is deferred while
  `BASE_SNAPSHOT_UNCOMMITTED` is true. Implement and verify in the isolated
  worktree, but do not create a commit object that passes only because of
  uncommitted legacy bytes.
- Do not add `requests`, provider SDKs, network calls, or order-capable code.
- No paid provider, auto-renewal, or automatic trial upgrade is allowed.
- A granted permission requires a non-secret evidence reference.
- Credentials remain environment values; manifests store names only.
- Actual provider manifests and retained provider data stay outside Git.
- Every unknown field, missing field, invalid date, or unverified mandatory
  permission fails closed.
- Publication permission is recorded but is not required for private research;
  denied publication must still prevent export.
- All timestamps are timezone-aware UTC and serialized as RFC 3339 with `Z`.
- All tests are network-free.
- The implementation order intentionally interleaves the two approved plans:
  complete this plan's Tasks 0 through 3, complete Event Core Task 1 so the
  immutable `SessionManifest` contract exists, return here for Task 4, then
  complete Event Core Tasks 2 through 7 and this plan's Task 5. Task 4 defines
  capability protocols and is tested with strict fakes; the later event-core
  tasks add the writer, reader, runtime, and replay integration tests. This
  ordering prevents a circular import or a placeholder retention boundary.

---

### Task 0: Pin and verify the Python runtime

**Files:**

- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `tools/verify_runtime.py`
- Create: `tests/__init__.py`
- Create: `tests/tennis_v1/__init__.py`
- Create: `tests/tennis_v1/test_legacy_baseline.py`
- Use: `docs/superpowers/specs/2026-07-27-tennis-v1-legacy-baseline.sha256`

**Interfaces:**

- `INCI_TENNIS_PYTHON` is an absolute operator-selected Python executable.
- Supported runtime: Python `>=3.12,<3.15`.
- `requirements.txt` locks the verified legacy environment exactly:
  `requests==2.34.2`, `cryptography==49.0.0`, `certifi==2026.7.22`,
  `cffi==2.1.0`, `charset-normalizer==3.4.9`, `idna==3.18`,
  `pycparser==3.0`, and `urllib3==2.7.0`. Tennis Phase 1 itself remains
  standard-library-only.

- [ ] **Step 1: Add the runtime verifier and immutable legacy guard**

```python
import hashlib
import pathlib
import sys

minimum = (3, 12)
maximum = (3, 15)
if not minimum <= sys.version_info[:2] < maximum:
    raise SystemExit(
        "Inci Tennis v1 requires Python >=3.12,<3.15; got "
        f"{sys.version_info.major}.{sys.version_info.minor}")
executable = pathlib.Path(sys.executable).resolve(strict=True)
digest = hashlib.sha256(executable.read_bytes()).hexdigest()
print(f"{executable} {sys.version.split()[0]} sha256={digest}")
```

`test_legacy_baseline.py` strictly parses the committed SHA file as exactly
21 unique lowercase-digest/relative-path rows, rejects absolute paths,
traversal, symlinks, missing files, and any protected legacy file not listed,
then hashes each file from one descriptor. The expected protected path set is
also a test constant, so deleting a manifest row cannot silently narrow the
guard.

`pyproject.toml` declares:

```toml
[project]
name = "inci-tennis-research"
version = "0.0.0"
requires-python = ">=3.12,<3.15"
dependencies = [
  "requests==2.34.2",
  "cryptography==49.0.0",
]
```

`requirements.txt` contains the eight exact versions listed above, one per
line. The runtime verifier also records the selected executable's resolved
absolute path, major/minor/patch version, and executable SHA-256 in test
output so a later verification can identify the actual interpreter.

- [ ] **Step 2: Verify the selected interpreter before setup**

Run:

```bash
test -n "$INCI_TENNIS_PYTHON"
test -x "$INCI_TENNIS_PYTHON"
"$INCI_TENNIS_PYTHON" tools/verify_runtime.py
```

Expected: prints the absolute selected executable and exits zero. Do not
continue with an interpreter that fails.

- [ ] **Step 3: Create an external virtual environment and install pins**

Run, replacing the example destination with an operator-owned path outside
the repository:

```bash
"$INCI_TENNIS_PYTHON" -m venv /absolute/outside-repo/inci-tennis-venv
/absolute/outside-repo/inci-tennis-venv/bin/pip install -r requirements.txt
export INCI_TENNIS_PYTHON=/absolute/outside-repo/inci-tennis-venv/bin/python
"$INCI_TENNIS_PYTHON" tools/verify_runtime.py
```

- [ ] **Step 4: Establish the copied legacy baseline**

Run:

```bash
"$INCI_TENNIS_PYTHON" tests.py
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_legacy_baseline -v
```

Expected: `ALL TESTS PASS (200 tests)` and every exact byte hash in the
legacy baseline manifest matches. The test rejects a missing, extra,
symlinked, or digest-mismatched entry in the protected legacy set. Rerun it at
every checkpoint; behavior tests alone do not prove that Phase 1 left v6
untouched.
If `git show HEAD:<protected path>` does not match the manifest, record the
integration state as `BASE_SNAPSHOT_UNCOMMITTED`; this is expected locally but
blocks claiming that a clean clone or PR is reproducible. It is not permission
to stage the pre-existing files.

- [ ] **Step 5: Commit only runtime metadata**

```bash
git add pyproject.toml requirements.txt tools/verify_runtime.py \
  tests/__init__.py tests/tennis_v1/__init__.py \
  tests/tennis_v1/test_legacy_baseline.py \
  docs/superpowers/specs/2026-07-27-tennis-v1-legacy-baseline.sha256
git commit -m "build: pin Tennis v1 Python runtime"
```

### Task 1: Add the isolated package and immutable research configuration

**Files:**

- Create: `tennis_v1/__init__.py`
- Create: `tennis_v1/canonical.py`
- Create: `tennis_v1/pinned_file.py`
- Create: `tennis_v1/config.py`
- Create: `tests/tennis_v1/test_config.py`
- Create: `tests/tennis_v1/test_canonical.py`
- Create: `tests/tennis_v1/test_pinned_file.py`

**Interfaces:**

- Consumes: JSON configuration stored outside the package.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class TennisV1Config:
    schema_version: int
    state_root: Path
    provider_manifest_path: Path
    provider_manifest_sha256: str
    trusted_permission_reviewer_ids: tuple[str, ...]
    trusted_qualification_issuer_ids: tuple[str, ...]
    observed_pool_limit: int
    paper_position_limit: int
    source_file_sha256: str
    canonical_sha256: str


def load_config(
    path: str | Path,
    *,
    repo_root: str | Path,
) -> TennisV1Config: ...
def canonical_json_bytes(value: object) -> bytes: ...
def read_pinned_file(
    path: str | Path,
    *,
    expected_sha256: str | None,
    repo_root: str | Path,
    max_bytes: int,
) -> "PinnedBytes": ...
def canonical_config_sha256(config: TennisV1Config) -> str: ...
def session_wal_path(config: TennisV1Config, session_id: str) -> Path: ...
```

- [ ] **Step 1: Write strict configuration tests**

```python
class TennisV1ConfigTests(unittest.TestCase):
    def test_loads_frozen_config_and_normalizes_absolute_paths(self):
        raw = {
            "schema_version": 1,
            "state_root": str(self.root / "state"),
            "provider_manifest_path": str(self.root / "provider.json"),
            "provider_manifest_sha256": "a" * 64,
            "trusted_permission_reviewer_ids": ["reviewer-test"],
            "trusted_qualification_issuer_ids": ["issuer-test"],
            "observed_pool_limit": 10,
            "paper_position_limit": 3,
        }
        path = self.write_json(raw)
        config = load_config(path, repo_root=self.root / "repo")
        self.assertEqual(config.observed_pool_limit, 10)
        self.assertTrue(config.state_root.is_absolute())
        with self.assertRaises(FrozenInstanceError):
            config.observed_pool_limit = 9

    def test_rejects_unknown_order_or_live_controls(self):
        for forbidden in ("live_enabled", "demo_enabled", "order_url",
                          "api_key", "private_key", "provider_url"):
            raw = self.valid_config()
            raw[forbidden] = "unsafe"
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(ConfigError):
                    load_config(
                        self.write_json(raw), repo_root=self.root / "repo")

    def test_rejects_relative_paths_and_limits_outside_policy(self):
        bad = self.valid_config()
        bad["state_root"] = "logs"
        with self.assertRaises(ConfigError):
            load_config(
                self.write_json(bad), repo_root=self.root / "repo")
        for field, value in (("observed_pool_limit", 0),
                             ("observed_pool_limit", 11),
                             ("paper_position_limit", 0),
                             ("paper_position_limit", 4)):
            bad = self.valid_config()
            bad[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(ConfigError):
                    load_config(
                        self.write_json(bad), repo_root=self.root / "repo")

    def test_session_wal_path_is_confined_and_requires_canonical_uuid(self):
        config = load_config(
            self.write_json(self.valid_config()),
            repo_root=self.root / "repo")
        session_id = "1f8b7b52-fdad-4dc1-a7a1-c2b1d4afaa12"
        self.assertEqual(
            session_wal_path(config, session_id),
            config.state_root / "sessions" / f"{session_id}.wal")
        with self.assertRaises(ConfigError):
            session_wal_path(config, "../escape")

    def test_config_rejects_symlink_nonregular_oversize_bom_and_non_utf8(self):
        ...

    def test_config_rejects_duplicate_keys_floats_and_boolean_limits(self):
        ...

    def test_config_opens_once_and_parses_the_same_fd_bytes(self):
        ...

    def test_canonical_json_rejects_floats_nonstring_keys_and_unknown_types(self):
        ...

    def test_config_source_and_canonical_digests_are_distinct_and_bound(self):
        ...

    def test_pinned_loader_rejects_every_symlink_component_and_nonregular_file(self):
        ...

    def test_pinned_loader_detects_dev_inode_mode_link_uid_size_mtime_ctime_drift(self):
        ...
```

- [ ] **Step 2: Run the focused test and observe the missing package**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_canonical \
  tests.tennis_v1.test_pinned_file \
  tests.tennis_v1.test_config -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tennis_v1'`.

- [ ] **Step 3: Implement strict loading and canonical hashing**

`load_config` must use the shared `read_pinned_file` security boundary:

- require a POSIX runtime with `O_NOFOLLOW`, `O_DIRECTORY`, `dir_fd`, and
  `flock`; absence is a hard unsupported-runtime failure;
- traverse every absolute parent component from a trusted root descriptor
  with `O_DIRECTORY | O_NOFOLLOW`, then open the basename once with
  `O_RDONLY | O_CLOEXEC | O_NOFOLLOW`;
- require an `fstat`-verified regular, single-link file and reject a symlink,
  directory, device, FIFO, or metadata drift;
- compare `(st_dev, st_ino, st_mode, st_nlink, st_uid, st_size, st_mtime_ns,
  st_ctime_ns)` before and after the bounded read;
- read at most 64 KiB plus one sentinel byte and reject oversized input;
- reject UTF-8 BOM, non-UTF-8, duplicate JSON keys, floats, NaN, Infinity,
  and booleans in integer fields;
- parse and validate only the exact bytes obtained from that descriptor;
- never echo input fragments or absolute paths in errors.

After strict decoding, `load_config` must enforce:

```python
EXPECTED_KEYS = frozenset({
    "schema_version", "state_root",
    "provider_manifest_path", "provider_manifest_sha256",
    "trusted_permission_reviewer_ids",
    "trusted_qualification_issuer_ids",
    "observed_pool_limit",
    "paper_position_limit",
})

if not isinstance(raw, dict) or set(raw) != EXPECTED_KEYS:
    raise ConfigError("configuration keys do not match schema v1")
if raw["schema_version"] != 1:
    raise ConfigError("unsupported configuration schema_version")
if (isinstance(raw["observed_pool_limit"], bool)
        or not isinstance(raw["observed_pool_limit"], int)
        or not 1 <= raw["observed_pool_limit"] <= 10):
    raise ConfigError("observed_pool_limit must be from 1 through 10")
if (isinstance(raw["paper_position_limit"], bool)
        or not isinstance(raw["paper_position_limit"], int)
        or not 1 <= raw["paper_position_limit"] <= 3):
    raise ConfigError("paper_position_limit must be from 1 through 3")
```

Both paths must already be absolute after `expanduser`; do not silently anchor
relative paths to the current directory. Both paths must resolve outside
`repo_root`, and symlink-based escape/entry ambiguity is rejected.
Each trusted reviewer/issuer tuple is nonempty, unique, sorted, and uses the
bounded safe identifier grammar; these IDs are trust anchors selected in the
immutable config, not accepted from a provider artifact by itself.
`canonical_json_bytes` is the one package-wide canonical serializer: sorted
keys, compact separators, ASCII-safe UTF-8, no floats/NaN/Infinity, exact
string mapping keys, and only JSON scalar/list/mapping types.
The loaded config retains the SHA-256 of its exact source bytes and a separate
canonical semantic SHA; the canonical projection excludes both derived hashes
and is stable across JSON key order. `provider_manifest_sha256` is explicitly
the SHA-256 of the exact external
manifest file bytes (not the canonical semantic hash) and must be
exactly 64 lowercase hexadecimal characters. `canonical_config_sha256` must encode a
canonical sorted-key JSON object and return a lowercase SHA-256 hex string.
Session IDs are generated once per new WAL session and are deliberately not
stored in reusable configuration.
`__init__.py` exports only `TennisV1Config`, `load_config`,
`canonical_config_sha256`, and `session_wal_path`. `session_wal_path` accepts lowercase canonical UUID text and
returns exactly `state_root / "sessions" / f"{session_id}.wal"`; it rejects
all other strings rather than normalizing them.

- [ ] **Step 4: Run the focused tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_canonical \
  tests.tennis_v1.test_pinned_file \
  tests.tennis_v1.test_config -v
```

Expected: PASS.

- [ ] **Step 5: Commit only the new package/configuration files**

```bash
git add tennis_v1/__init__.py tennis_v1/canonical.py tennis_v1/pinned_file.py \
  tennis_v1/config.py tests/tennis_v1/test_canonical.py \
  tests/tennis_v1/test_pinned_file.py \
  tests/tennis_v1/test_config.py
git commit -m "feat: add immutable Tennis v1 configuration"
```

### Task 2: Define and strictly parse the provider manifest

**Files:**

- Create: `tennis_v1/entitlements.py`
- Create: `tennis_v1/adapter_contract.py`
- Create: `tennis_v1/qualification_protocol.py`
- Create: `tennis_v1/schemas/provider-entitlement-v1.schema.json`
- Create: `tennis_v1/schemas/provider-permission-v1.schema.json`
- Create: `tennis_v1/schemas/provider-qualification-v1.schema.json`
- Create: `tennis_v1/schemas/provider-qualification-trace-v1.schema.json`
- Create: `tests/tennis_v1/test_entitlements.py`
- Create: `tests/tennis_v1/test_adapter_contract.py`
- Create: `tests/tennis_v1/fixtures/synthetic_adapter.py`
- Create: `tests/tennis_v1/fixtures/provider_manifest_schema_example.json`
- Create: `tests/tennis_v1/fixtures/provider_permission_schema_example.json`
- Create: `tests/tennis_v1/fixtures/provider_qualification_schema_example.json`
- Create: `tests/tennis_v1/fixtures/provider_qualification_trace_schema_example.json`

**Interfaces:**

- Consumes: one local JSON provider manifest.
- Produces:

```python
class IntendedUse(str, Enum):
    PRIVATE_PAPER_EVALUATION = "private_paper_evaluation"


class PermissionBasis(str, Enum):
    TRIAL_TERMS = "trial_terms"
    WRITTEN_PERMISSION = "written_permission"


class PermissionOperation(str, Enum):
    PROVIDER_INGEST = "provider_ingest"
    RAW_RETENTION = "raw_retention"
    DERIVED_SIGNALS = "derived_signals"
    POST_EXPIRY_ANALYSIS = "post_expiry_analysis"
    PUBLICATION = "publication"


class BillingMode(str, Enum):
    TRIAL = "trial"
    PAID = "paid"


class QualificationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNTESTED = "untested"


@dataclass(frozen=True, slots=True)
class ProviderQuotas:
    requests_per_rolling_60_seconds: int
    requests_per_utc_calendar_day: int
    requests_per_rolling_second: int
    max_connections: int
    max_subscriptions: int
    resync_requests_per_rolling_hour: int


class AuthMode(str, Enum):
    PUBLIC = "public"
    API_KEY = "api_key"
    OAUTH_CLIENT = "oauth_client"


@dataclass(frozen=True, slots=True)
class AuthContract:
    mode: AuthMode
    credential_env_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdapterUsagePlan:
    startup_requests_fixed: int
    startup_requests_per_match: int
    steady_requests_per_minute_fixed: int
    steady_requests_per_minute_per_match: int
    resync_requests_per_match: int
    max_resyncs_per_match_per_hour: int
    max_connections: int
    subscriptions_per_match: int


@dataclass(frozen=True, slots=True)
class AdapterContract:
    provider_id: str
    product_tier: str
    adapter_id: str
    adapter_code_sha256: str
    auth: AuthContract
    usage: AdapterUsagePlan
    formats: tuple[str, ...]
    auth_contract_sha256: str
    quota_contract_sha256: str


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    stable_match_ids: bool
    stable_player_ids: bool
    point_state: bool
    current_server: bool
    match_format: bool
    source_event_time: bool
    provider_generated_time: bool
    monotonic_sequence_or_revision: bool
    correction_semantics: bool
    resync_snapshot: bool
    supported_formats: tuple[str, ...]
    declared_strata: tuple["CoverageStratum", ...]


@dataclass(frozen=True, slots=True)
class CoverageStratum:
    sport: str
    tour: str
    competition_tier: str
    match_format: str
    round_code: str


@dataclass(frozen=True, slots=True)
class QualifiedStratumEvidence:
    stratum: CoverageStratum
    observed_matches: int
    simultaneous_matches_tested: int
    tested_formats: tuple[str, ...]
    tested_capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualificationArtifact:
    schema_version: int
    provider_id: str
    product_tier: str
    source_lineage_id: str
    adapter_code_sha256: str
    auth_contract_sha256: str
    quota_contract_sha256: str
    qualification_protocol_sha256: str
    evidence_trace_sha256: str
    issuer_id: str
    approval_id: str
    issued_at: datetime
    status: QualificationStatus
    qualified_at: datetime | None
    qualified_until: datetime | None
    observed_matches: int
    simultaneous_matches_tested: int
    strata: tuple[QualifiedStratumEvidence, ...]


@dataclass(frozen=True, slots=True)
class PermissionArtifact:
    schema_version: int
    provider_id: str
    product_tier: str
    entitlement_id_sha256: str
    terms_version: str
    basis: PermissionBasis
    intended_use: IntendedUse
    permitted_operations: tuple[PermissionOperation, ...]
    access_starts_at: datetime
    access_expires_at: datetime
    analysis_expires_at: datetime
    raw_retention_until: datetime
    reviewed_at: datetime
    reviewer_id: str
    approval_id: str
    evidence_document_sha256: str


@dataclass(frozen=True, slots=True)
class QualificationTraceMatch:
    match_id_sha256: str
    stratum: CoverageStratum
    tested_format: str
    started_at: datetime
    ended_at: datetime
    tested_capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualificationTrace:
    schema_version: int
    provider_id: str
    product_tier: str
    source_lineage_id: str
    adapter_code_sha256: str
    auth_contract_sha256: str
    quota_contract_sha256: str
    qualification_protocol_sha256: str
    started_at: datetime
    completed_at: datetime
    matches: tuple[QualificationTraceMatch, ...]
    clean_terminal: bool
    source_file_sha256: str


@dataclass(frozen=True, slots=True)
class ProviderManifest:
    schema_version: int
    provider_id: str
    product_tier: str
    entitlement_id: str = field(repr=False)
    source_lineage_id: str
    terms_url: str
    terms_version: str
    permission_artifact_path: Path = field(repr=False)
    permission_artifact_sha256: str
    permission_evidence_path: Path = field(repr=False)
    permission_evidence_sha256: str
    permission: PermissionArtifact
    billing_mode: BillingMode
    auto_renew: bool
    access_starts_at: datetime
    access_expires_at: datetime
    analysis_expires_at: datetime
    raw_retention_until: datetime
    max_raw_retention_seconds: int
    credential_env_names: tuple[str, ...]
    quotas: ProviderQuotas
    capabilities: ProviderCapabilities
    qualification_artifact_path: Path = field(repr=False)
    qualification_artifact_sha256: str
    qualification_trace_path: Path = field(repr=False)
    qualification_trace_sha256: str
    qualification: QualificationArtifact
    source_file_sha256: str
    canonical_sha256: str


def load_provider_manifest(
    path: str | Path,
    *,
    expected_sha256: str,
    repo_root: str | Path,
) -> ProviderManifest: ...
def load_qualification_artifact(
    path: str | Path,
    *,
    expected_sha256: str,
    repo_root: str | Path,
) -> QualificationArtifact: ...
def load_permission_artifact(
    path: str | Path,
    *,
    expected_sha256: str,
    evidence_path: str | Path,
    expected_evidence_sha256: str,
    repo_root: str | Path,
) -> PermissionArtifact: ...
def load_qualification_trace(
    path: str | Path,
    *,
    expected_sha256: str,
    repo_root: str | Path,
) -> QualificationTrace: ...
def canonical_manifest_sha256(manifest: ProviderManifest) -> str: ...
def opaque_id_sha256(value: str) -> str: ...
def load_active_adapter_contract(
    *,
    provider_id: str,
    product_tier: str,
) -> AdapterContract: ...
def derive_quota_demand(
    adapter: AdapterContract,
    request: "ResearchRequest",
) -> ProviderQuotas: ...
def qualification_protocol_sha256() -> str: ...
```

- [ ] **Step 1: Add schema-only fixtures, a typed fixture builder, and parser tests**

Committed JSON examples use synthetic identifiers, contain placeholder paths,
and are deliberately non-runtime-loadable. Tests use a typed fixture builder
that first creates permission evidence, permission artifact, and qualification
trace and artifact under a per-test temporary repo-external directory;
computes their exact digests; renders those actual absolute paths into a fresh
manifest; computes its digest; and only then invokes the runtime loader. Tests
never copy committed path-bearing bytes unchanged or depend on one machine's
path.
Tests must cover:

```python
def test_manifest_round_trips_without_secret_values(): ...
def test_manifest_hash_is_stable_across_json_key_order(): ...
def test_exact_file_digest_and_canonical_digest_are_distinct_and_bound(): ...
def test_unknown_or_missing_top_level_field_fails(): ...
def test_unknown_or_missing_nested_field_fails(): ...
def test_timestamp_requires_utc_z_suffix(): ...
def test_trial_terms_and_written_permission_are_discriminated_and_pinned(): ...
def test_permission_artifact_binds_provider_tier_entitlement_terms_use_and_dates(): ...
def test_permission_reviewer_approval_and_review_time_are_structurally_valid(): ...
def test_trial_terms_cannot_grant_post_expiry_analysis_or_publication(): ...
def test_written_permission_grants_only_its_explicit_operations_and_windows(): ...
def test_unrelated_permission_evidence_document_cannot_authorize_operations(): ...
def test_manifest_itself_requires_expected_digest_and_repo_external_path(): ...
def test_manifest_symlink_nonregular_oversize_and_duplicate_keys_fail(): ...
def test_duplicate_or_invalid_credential_env_name_fails(): ...
def test_nonpositive_quota_and_invalid_sha256_fail(): ...
def test_manifest_repr_contains_no_environment_values(): ...
def test_secret_keys_and_fixture_sentinel_never_reach_repr_errors_or_stdout(): ...
def test_qualification_artifact_is_external_digest_pinned_and_single_open(): ...
def test_qualification_artifact_binds_provider_tier_lineage_and_adapter_code(): ...
def test_qualification_binds_trace_protocol_issuer_approval_and_validity_fields(): ...
def test_qualification_trace_is_external_digest_pinned_strict_and_single_open(): ...
def test_qualification_summary_is_derived_exactly_from_trace_rows(): ...
def test_active_adapter_digest_is_computed_from_registered_code_closure(): ...
def test_auth_and_quota_contract_hashes_bind_manifest_and_qualification(): ...
def test_path_or_caller_supplied_adapter_digest_cannot_override_registry(): ...
def test_passed_artifact_requires_time_counts_capacity_and_capabilities(): ...
def test_per_stratum_evidence_requires_counts_formats_capabilities_and_capacity(): ...
def test_structured_strata_exact_grammars_reject_wildcards_unknowns_duplicates(): ...
def test_documented_json_schemas_match_runtime_required_keys_and_enums(): ...
```

Use these exact permission operations:

```python
{
    "provider_ingest",
    "raw_retention",
    "derived_signals",
    "post_expiry_analysis",
    "publication",
}
```

Use exactly these capability keys:

```python
{
    "stable_match_ids",
    "stable_player_ids",
    "point_state",
    "current_server",
    "match_format",
    "source_event_time",
    "provider_generated_time",
    "monotonic_sequence_or_revision",
    "correction_semantics",
    "resync_snapshot",
    "supported_formats",
    "declared_strata",
}
```

The manifest's raw `permission` object contains only digest-pinned external
references:

```json
{
  "artifact_path": "/absolute/outside-repo/permission-review.json",
  "artifact_sha256": "<64 lowercase hex>",
  "evidence_path": "/absolute/outside-repo/terms-or-letter.pdf",
  "evidence_sha256": "<64 lowercase hex>"
}
```

The permission artifact is the sole legal authority for allowed operations.
Both the artifact and its evidence document are independently loaded through
`read_pinned_file`; a matching digest written inside an untrusted artifact is
not a trust anchor. The artifact binds the evidence-document hash to the
provider, exact product tier, opaque entitlement-ID hash, terms version,
explicit `private_paper_evaluation` use, basis, exact access/analysis/raw
retention windows, review time, reviewer, and approval ID. Every bound value
must exactly equal the corresponding manifest value. The loader requires
`reviewer_id` and `approval_id` to use the bounded safe-ID grammar and
`reviewed_at` to be canonical UTC. It has neither `TennisV1Config` nor a
current request clock, so it does not decide whether the reviewer is trusted
or the review is current; those mandatory authorization checks belong to
`evaluate_provider` in Task 3.

`TRIAL_TERMS` requires `billing_mode == TRIAL`, uses the saved exact terms
snapshot as evidence, and permits exactly
`PROVIDER_INGEST`, `RAW_RETENTION`, and `DERIVED_SIGNALS`. It requires
`analysis_expires_at == access_expires_at`; `POST_EXPIRY_ANALYSIS` and
`PUBLICATION` are structurally forbidden. `WRITTEN_PERMISSION` also uses
`billing_mode == TRIAL` in Phase 1 because all paid access is disabled. It
requires a saved written authorization as evidence and the three base
operations, and may add `POST_EXPIRY_ANALYSIS` or `PUBLICATION` only when the
artifact explicitly grants each operation. `analysis_expires_at >
access_expires_at` is valid iff `POST_EXPIRY_ANALYSIS` is present. Neither
branch may authorize a session beyond its access window or retained bytes
beyond `raw_retention_until`. The manifest cannot self-declare, widen, or
substitute permission status.

The manifest's raw `qualification` object is only a reference:

```json
{
  "artifact_path": "/absolute/outside-repo/qualification.json",
  "artifact_sha256": "<64 lowercase hex>",
  "evidence_trace_path": "/absolute/outside-repo/qualification-trace.wal",
  "evidence_trace_sha256": "<64 lowercase hex>"
}
```

The verified external artifact is the sole runtime authority for
qualification status and validity, but its numeric and categorical summary
is not accepted on assertion. `load_qualification_trace` loads the separate
trace with `read_pinned_file` (64 MiB maximum), verifies the configured exact
SHA-256, rejects duplicate/unknown keys and secret-shaped keys, and accepts
only the documented trace schema. The trace requires a clean terminal record,
unique opaque match hashes, `started_at < ended_at` for every match, and exact
provider/tier/lineage/adapter/auth/quota/protocol bindings.

The loader derives `observed_matches`, per-stratum match counts, tested
formats, tested capabilities, and simultaneous capacity from the verified
trace. Simultaneous capacity is the maximum overlap of half-open match
intervals `[started_at, ended_at)`; an end at timestamp `t` is processed before
a start at `t`. The artifact's global and per-stratum summaries must equal
those derived values exactly. It may not omit an adverse row or claim broader
coverage. A manifest may declare capabilities but cannot promote itself to
`passed`.

The artifact has exact top-level keys matching `QualificationArtifact`. Its
provider ID, product tier, source lineage, and evidence-trace SHA must match
the verified inputs. The loader requires `issuer_id` and `approval_id` to use
the bounded safe-ID grammar and `qualified_at == trace.completed_at`. A passed
artifact requires non-null times, positive counts, nonempty strata, and the
intrinsic ordering `qualified_at <= issued_at < qualified_until`.
`qualified_until` must be no later than both `analysis_expires_at` and
`qualified_at + timedelta(days=30)`. Trial terms intrinsically require
`analysis_expires_at == access_expires_at`, so a trial qualification still
cannot outlive access. The protocol SHA must equal the
code-owned qualification protocol; adapter/auth/quota hashes must equal the
internally computed active adapter contract. Failed and untested artifacts
cannot carry passed-only counts, strata, or validity dates. A caller cannot
supply an adapter, registry root, digest, or trust decision. The loader does
not have config trust anchors or an authoritative current clock; Task 3 must
reject an untrusted issuer, future issue, or expired qualification.

Coverage is never encoded as opaque strings. Each declared or covered stratum
is an exact object with `sport`, `tour`, `competition_tier`, `match_format`,
and `round_code`; values are bounded canonical identifiers and `*`,
`unknown`, empty values, or title-derived prose are rejected.
The exact component grammars are: `sport` is literal lowercase `tennis`;
`tour` and `competition_tier` match `[A-Z][A-Z0-9-]{0,31}`;
`match_format` and `round_code` match `[A-Z][A-Z0-9_]{0,31}`.
Each `QualifiedStratumEvidence` has positive observed/capacity counts and
nonempty unique tested format/capability tuples, all exactly derived from its
trace rows. Global observed counts equal the sum of stratum counts; global
simultaneous capacity is independently derived across all intervals and is
not inferred by summing per-stratum capacities.

`load_active_adapter_contract` does not accept a digest or credential names
from the caller. It resolves `(provider_id, product_tier)` through a
code-owned registry rooted at `Path(__file__).resolve(strict=True).parent`.
The registry entry declares one closed allowlist of adapter modules.
`adapter_code_sha256` is
`sha256(b"INCI-ADAPTER-CLOSURE-V1\0" + canonical_json_bytes(entries))`,
where each sorted entry is
`{"path": <package-relative POSIX path>, "length": <byte length>,
"sha256": <exact-file SHA-256>}`. Every component is opened with the shared
dirfd/`O_NOFOLLOW` boundary; duplicate paths, symlinks, hard links, files
outside the package, a missing allowlisted file, an unexpected Python file in
the adapter directory, and a contract/provider mismatch fail closed. Phase
1's production registry is empty; only a synthetic test adapter exists. A
real provider cannot pass preflight until a separately reviewed read-only
adapter adds its complete closure in Phase 2.
`qualification_protocol_sha256` is
`sha256(b"INCI-QUALIFICATION-PROTOCOL-V1\0" + canonical_json_bytes(
QUALIFICATION_PROTOCOL_V1))`; `QUALIFICATION_PROTOCOL_V1` is a code-owned
constant and not artifact-supplied.

Quota demand is an integer-only conservative contract, not a caller-provided
estimate. Reject booleans and negative `AdapterUsagePlan` values, require
positive `max_connections` and `subscriptions_per_match`, and use:

```python
MICROSECONDS_PER_MINUTE = 60_000_000
MICROSECONDS_PER_HOUR = 3_600_000_000


def ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


matches = request.requested_matches
startup = (
    usage.startup_requests_fixed
    + matches * usage.startup_requests_per_match
)
steady_minute = (
    usage.steady_requests_per_minute_fixed
    + matches * usage.steady_requests_per_minute_per_match
)
resync_hour = (
    matches
    * usage.resync_requests_per_match
    * usage.max_resyncs_per_match_per_hour
)
worst_cluster = startup + steady_minute + resync_hour
```

Compute session and overlap lengths without `total_seconds()` or floats:

```python
def timedelta_microseconds(delta: timedelta) -> int:
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000
        + delta.microseconds
    )
```

The session is the half-open interval `[request.now_utc,
request.session_end_utc)` and must have positive duration. For every UTC
calendar day with positive overlap, let `overlap_us` be the exact integer
microseconds of that overlap and define:

```python
day_demand = (
    (startup if request.now_utc.date() == utc_day else 0)
    + ceil_div(overlap_us, MICROSECONDS_PER_MINUTE) * steady_minute
    + ceil_div(overlap_us, MICROSECONDS_PER_HOUR) * resync_hour
)
```

The returned `ProviderQuotas` demand is exactly:

```python
ProviderQuotas(
    requests_per_rolling_60_seconds=worst_cluster,
    requests_per_utc_calendar_day=max(day_demands),
    requests_per_rolling_second=worst_cluster,
    max_connections=usage.max_connections,
    max_subscriptions=matches * usage.subscriptions_per_match,
    resync_requests_per_rolling_hour=resync_hour,
)
```

`worst_cluster` deliberately assumes startup, one steady-minute batch, and
the whole allowed resync-hour batch can coincide; this is the fail-closed
bound when the provider contract supplies no finer pacing guarantee. Each of
the six derived fields is compared with the identically named provider quota.
No duration, request demand, or quota override appears in the manifest,
permission artifact, qualification artifact, or caller input.

- [ ] **Step 2: Run the focused manifest tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_entitlements.ProviderManifestTests -v
```

Expected: FAIL because `tennis_v1.entitlements` does not exist.

- [ ] **Step 3: Implement exact-schema parsing**

Use one helper for each scalar type. The project accepts only the canonical
RFC 3339 UTC subset `YYYY-MM-DDTHH:MM:SSZ` or
`YYYY-MM-DDTHH:MM:SS.ffffffZ`. It rejects offsets, spaces, week/ordinal dates,
missing seconds, variable-width fractions, and naive timestamps before
calling a parser:

```python
def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str) or FULL_UTC_RE.fullmatch(value) is None:
        raise ManifestError(f"{field}: invalid_utc_timestamp")
    try:
        fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in value else "%Y-%m-%dT%H:%M:%SZ"
        parsed = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
    except ValueError:
        raise ManifestError(f"{field}: invalid_utc_timestamp") from None
    if format_utc(parsed) != value:
        raise ManifestError(f"{field}: noncanonical_utc_timestamp")
    return parsed
```

Requirements:

- use `read_pinned_file` for the manifest, permission artifact/evidence,
  qualification artifact, and qualification trace; JSON artifacts are capped
  at 64 KiB, permission evidence at 4 MiB, and the trace at 64 MiB;
- require the manifest path to resolve outside `repo_root`;
- hash and parse exactly the one descriptor's returned bytes;
- compare that digest with the immutable configuration's expected digest;
- reject JSON duplicate keys with `object_pairs_hook`;
- recursively reject unexpected normalized keys matching authorization,
  cookie, token, secret, password, private-key, signature, credential, or
  API-key patterns before constructing any dataclass; the exact
  schema-approved `credential_env_names` name-only field is the sole
  credential-pattern exception and its values must be environment-variable
  identifiers, never credentials;
- reject booleans where integers are required;
- allow an empty credential list for a genuinely public product; every
  declared `credential_env_names` entry must be unique and match
  `[A-Z][A-Z0-9_]*`;
- require a public `https` terms URL with no user information, query string,
  or fragment;
- allow only `rest_json`, `websocket_json`, and `ndjson` in a unique nonempty
  `supported_formats` tuple;
- require unique structured `declared_strata`; every component uses its exact
  bounded canonical grammar, and wildcard, `unknown`, and title-derived
  coverage values are forbidden;
- require positive nonboolean values for every `ProviderQuotas` field, using
  only the six exact rolling/calendar window names in the dataclass;
- require a 64-character lowercase hex qualification artifact hash;
- require a repo-external regular qualification artifact and verify its exact
  SHA-256 using the same size limit, single-open/fstat/no-symlink,
  duplicate-key, encoding, and secret-key rejection rules;
- independently pin and parse the repo-external qualification trace, derive
  its counts/capacity/formats/capabilities with the exact half-open interval
  algorithm above, and require every summary field in the qualification
  artifact to equal that derivation;
- derive all `QualificationArtifact` fields from verified artifact bytes;
  never copy status, counts, tested capabilities, capacity, validity, or
  strata from the manifest;
- require a passed artifact to have non-null `qualified_at` and
  `qualified_until`, bounded issuer/approval IDs, intrinsically ordered
  timestamps, positive observed/capacity counts, a nonempty exact capability
  set, a 64-character adapter-code SHA, and the exact 30-day/analysis-window
  validity bound above, with the trial analysis-equals-access constraint
  preserving the access ceiling for trial terms;
- bind artifact provider ID, product tier, and source lineage to the manifest;
- verify the permission artifact and evidence document independently, require
  the artifact's `evidence_document_sha256` to match the exact evidence bytes,
  bind all permission fields described above to the manifest, and require the
  reviewer, approval, review-time structure, basis, billing branch, exact
  operations, and exact windows to satisfy the structural rules above;
- never include manifest/evidence paths or input fragments in parser errors;
- require `access_starts_at < access_expires_at <= analysis_expires_at`;
- when `analysis_expires_at > access_expires_at`, require the written
  permission branch and an explicit `POST_EXPIRY_ANALYSIS` operation;
- require `raw_retention_until >= analysis_expires_at` for official research;
- sort and freeze structured declared and covered strata by all five fields;
- retain the exact verified source-file digest on `ProviderManifest` and
  separately hash only validated normalized values as `canonical_sha256`;
  the canonical projection excludes filesystem paths and both derived digest
  fields, so it is nonrecursive and stable across JSON key order; these two
  digest meanings are never conflated;
- compute opaque identifiers with
  `sha256(b"INCI-OPAQUE-ID-V1\0" + value.encode("utf-8"))` after validating a
  nonempty bounded string; never log the clear identifier.

The four committed Draft 2020-12 JSON Schemas are tooling/documentation, while
the stdlib parsers remain runtime authority. Set `additionalProperties:
false` on every object. Tests load every schema with stdlib `json` and compare
its required keys, permission/basis/status enums, capability keys, billing
enums, stratum keys, and timestamp patterns with runtime constants so the
contracts cannot drift.

- [ ] **Step 4: Run manifest tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_entitlements.ProviderManifestTests -v
```

Expected: PASS.

- [ ] **Step 5: Commit the manifest contract**

```bash
git add tennis_v1/entitlements.py \
  tennis_v1/adapter_contract.py tennis_v1/qualification_protocol.py \
  tennis_v1/schemas/provider-entitlement-v1.schema.json \
  tennis_v1/schemas/provider-permission-v1.schema.json \
  tennis_v1/schemas/provider-qualification-v1.schema.json \
  tennis_v1/schemas/provider-qualification-trace-v1.schema.json \
  tests/tennis_v1/test_entitlements.py \
  tests/tennis_v1/test_adapter_contract.py \
  tests/tennis_v1/fixtures/synthetic_adapter.py \
  tests/tennis_v1/fixtures/provider_manifest_schema_example.json \
  tests/tennis_v1/fixtures/provider_permission_schema_example.json \
  tests/tennis_v1/fixtures/provider_qualification_schema_example.json \
  tests/tennis_v1/fixtures/provider_qualification_trace_schema_example.json
git commit -m "feat: define strict provider entitlement manifest"
```

### Task 3: Implement the pure fail-closed qualification decision

**Files:**

- Modify: `tennis_v1/entitlements.py`
- Modify: `tests/tennis_v1/test_entitlements.py`

**Interfaces:**

```python
class QualificationReason(str, Enum):
    ELIGIBLE = "eligible"
    PAID_ACCESS_DISABLED = "paid_access_disabled"
    AUTO_RENEW_FORBIDDEN = "auto_renew_forbidden"
    ACCESS_NOT_STARTED = "access_not_started"
    ACCESS_EXPIRED = "access_expired"
    CLOCK_ROLLBACK = "clock_rollback"
    SESSION_WINDOW_EXCEEDS_ACCESS = "session_window_exceeds_access"
    ANALYSIS_EXPIRED = "analysis_expired"
    ANALYSIS_WINDOW_INADEQUATE = "analysis_window_inadequate"
    RETENTION_TOO_SHORT = "retention_too_short"
    MANDATORY_PERMISSION_MISSING = "mandatory_permission_missing"
    CREDENTIAL_MISSING = "credential_missing"
    QUOTA_INADEQUATE = "quota_inadequate"
    CAPABILITY_MISSING = "capability_missing"
    FORMAT_UNSUPPORTED = "format_unsupported"
    ADAPTER_MISMATCH = "adapter_mismatch"
    QUALIFICATION_EVIDENCE_MISMATCH = "qualification_evidence_mismatch"
    QUALIFICATION_NOT_PASSED = "qualification_not_passed"
    QUALIFICATION_CAPACITY_INADEQUATE = "qualification_capacity_inadequate"
    STRATUM_NOT_QUALIFIED = "stratum_not_qualified"


@dataclass(frozen=True, slots=True)
class RequestedStratum:
    stratum: CoverageStratum
    matches: int


@dataclass(frozen=True, slots=True)
class ResearchRequest:
    intended_use: IntendedUse
    now_utc: datetime
    session_end_utc: datetime
    required_retention_until: datetime
    expiry_safety_margin_seconds: int
    required_raw_retention_seconds: int
    requested_matches: int
    required_strata: tuple[RequestedStratum, ...]


@dataclass(frozen=True, slots=True)
class QualifiedProviderBinding:
    provider_id: str
    product_tier: str
    source_lineage_id: str
    entitlement_id_sha256: str
    manifest_file_sha256: str
    manifest_canonical_sha256: str
    qualification_artifact_sha256: str
    permission_artifact_sha256: str
    qualification_trace_sha256: str
    adapter_code_sha256: str
    auth_contract_sha256: str
    quota_contract_sha256: str
    session_end_utc: datetime
    required_retention_until: datetime
    access_expires_at: datetime
    analysis_expires_at: datetime
    qualified_until: datetime


@dataclass(frozen=True, slots=True)
class QualificationDecision:
    eligible: bool
    reasons: tuple[QualificationReason, ...]
    export_allowed: bool
    manifest_file_sha256: str
    manifest_canonical_sha256: str
    request_sha256: str
    provider_request_binding_sha256: str | None
    binding: QualifiedProviderBinding | None

    def require_eligible(self) -> None: ...


class ProviderGateError(RuntimeError):
    reason: QualificationReason


def evaluate_provider(
    config: TennisV1Config,
    manifest: ProviderManifest,
    request: ResearchRequest,
    *,
    environ: Mapping[str, str],
) -> QualificationDecision: ...
def provider_request_binding_sha256(
    decision: QualificationDecision,
) -> str: ...


class ProviderGate:
    def __init__(
        self,
        config: TennisV1Config,
        manifest: ProviderManifest,
        request: ResearchRequest,
        *,
        environ: Mapping[str, str],
        clock: Callable[[], datetime],
    ) -> None: ...

    def require_start(self) -> QualificationDecision: ...
    def require_ingest(self) -> QualificationDecision: ...
    def require_resync(self) -> QualificationDecision: ...
    def require_transform(self) -> QualificationDecision: ...
    def require_derived_persist(self) -> QualificationDecision: ...
    def require_raw_persist(self) -> int:
        """Return the currently allowed upper delete-by UTC ns or raise."""
    def require_analysis(self) -> QualificationDecision: ...
    def require_export(self) -> QualificationDecision: ...
    def seconds_until_access_expiry(self) -> float: ...
```

- [ ] **Step 1: Add one adversarial test per decision reason**

Tests must independently mutate the valid manifest/request to prove:

```python
def test_paid_and_auto_renew_access_are_never_eligible(): ...
def test_not_started_expired_and_short_retention_fail(): ...
def test_session_end_plus_margin_must_fit_inside_access_window(): ...
def test_required_retention_must_fit_analysis_and_raw_windows(): ...
def test_required_retention_is_exactly_session_end_plus_required_seconds(): ...
def test_every_capture_delete_by_covers_the_session_required_horizon(): ...
def test_access_expiry_blocks_ingest_but_separate_analysis_permission_is_required(): ...
def test_each_mandatory_permission_fails_when_absent_or_unauthorized(): ...
def test_publication_denied_allows_private_research_but_blocks_export(): ...
def test_missing_or_blank_credential_fails_without_echoing_value(): ...
def test_path_or_arbitrary_manifest_env_name_cannot_satisfy_adapter_auth(): ...
def test_requested_and_qualification_capacity_must_cover_pool(): ...
def test_quota_demand_is_derived_from_duration_pool_and_adapter_usage_plan(): ...
def test_each_exact_rolling_calendar_connection_subscription_and_resync_quota_is_enforced(): ...
def test_every_causal_capability_is_mandatory(): ...
def test_qualification_must_pass_and_bind_active_adapter_code(): ...
def test_evaluator_rejects_untrusted_permission_reviewer_and_future_review(): ...
def test_evaluator_rejects_untrusted_issuer_future_issue_and_expired_qualification(): ...
def test_provider_request_binding_hash_covers_every_binding_and_request_field(): ...
def test_structured_strata_and_format_must_be_declared_and_tested(): ...
def test_reasons_are_complete_sorted_and_stable(): ...
def test_require_eligible_raises_one_redacted_summary(): ...
def test_gate_rechecks_clock_for_every_operation_and_never_extends_on_rollback(): ...
def test_require_raw_persist_is_zero_arg_and_uses_only_authoritative_clock(): ...
def test_expiry_between_raw_persist_transform_and_derived_persist_denies(): ...
```

The mandatory operations are provider ingest, raw retention, and derived
signals under the sole `PRIVATE_PAPER_EVALUATION` intended use. Publication is
independently enforced only for export.

- [ ] **Step 2: Run the decision tests and observe the missing evaluator**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_entitlements.ProviderDecisionTests -v
```

Expected: FAIL because `evaluate_provider` is missing.

- [ ] **Step 3: Implement complete reason accumulation**

Do not return after the first failure. Accumulate all applicable reasons,
deduplicate, and sort by enum value. Eligibility is exactly:

```python
eligible = not reasons
if eligible:
    reasons = [QualificationReason.ELIGIBLE]
```

`request_sha256` is a domain-separated hash over exactly the normalized
`ResearchRequest` fields, including structured strata. Derived quota demand
is not a request field: it is recomputed from the internally resolved adapter,
and `quota_contract_sha256` separately binds that derivation contract. The
request hash is present for eligible and ineligible decisions without
exposing environment values.

`binding` and `provider_request_binding_sha256` are non-null exactly when
eligible. The binding is constructed only from the
verified manifest and qualification artifact, hashes the entitlement ID with
the shared domain-separated opaque-ID function, and gives the session builder
one exact provenance object to compare. Ineligible decisions have
both fields set to `None`; callers cannot fabricate a clean session manifest
from them. The binding SHA is:

```python
sha256(
    b"INCI-PROVIDER-REQUEST-BINDING-V1\0"
    + canonical_json_bytes({
        "request_sha256": decision.request_sha256,
        "binding": normalized_every_QualifiedProviderBinding_field,
    })
).hexdigest()
```

The normalized projection includes every dataclass field exactly once,
formats datetimes with the package's canonical UTC formatter, contains no
paths or secrets, and is compared against an independently enumerated
expected-key test. Adding/removing a binding field without updating the
projection fails that test.

Additional rules:

- `request.now_utc`, `session_end_utc`, and
  `required_retention_until` must be aware UTC values satisfying
  `now < session_end <= required_retention_until`;
- `requested_matches` must be from 1 through 10;
- requested strata are unique, each `matches` is positive/nonboolean, and
  their sum equals `requested_matches`;
- `expiry_safety_margin_seconds` must be a positive nonboolean integer and the
  request is eligible only when
  `session_end_utc + margin < access_expires_at`;
- `required_retention_until` must be no later than both
  `analysis_expires_at` and `raw_retention_until`, and
  `required_raw_retention_seconds` must be a positive nonboolean integer no
  greater than the provider's declared maximum;
- `required_retention_until` must equal
  `session_end_utc + timedelta(seconds=required_raw_retention_seconds)`
  exactly, without a float conversion;
- preflight must prove
  `min(raw_retention_until, now_utc + max_raw_retention_seconds)
  >= required_retention_until`; otherwise even the earliest session capture
  cannot satisfy the requested horizon;
- provider subscription capacity and qualification-tested simultaneous
  capacity must cover the requested match pool;
- `derive_quota_demand` uses the exact six integer formulas and exact
  rolling-60-second/rolling-second/UTC-calendar-day windows in Task 2; the
  caller supplies none of these demand numbers and each identically named
  demand must fit its provider quota;
- all required strata must exist in both capability `declared_strata` and
  qualification `covered_strata`;
- for each requested stratum, the qualification row's observed count and
  simultaneous capacity cover that row's requested match count, its tested
  formats intersect the active adapter formats, and it tested every causal
  capability used by Phase 1;
- the verified artifact's adapter/auth/quota SHAs must equal the internally
  computed active `AdapterContract`;
- manifest credential names and authentication mode must exactly equal the
  code-owned `AuthContract`; `PUBLIC` requires an empty tuple, nonpublic modes
  require their exact nonempty slots, and only those exact environment names
  are checked for nonblank values;
- permission reviewer and qualification issuer must appear in the immutable
  config trust-anchor tuples; `reviewed_at <= request.now_utc`,
  `qualified_at <= issued_at <= request.now_utc < qualified_until`, and the
  intrinsic Task 2 ordering/window bounds are required by
  `evaluate_provider`; a trusted-looking string in an artifact is
  insufficient;
- exception messages may contain credential variable names but never values;
- `export_allowed` is true only when the provider is eligible and publication
  permission is granted.

`ProviderGate` must evaluate the clock on every method call. At
`now + margin >= access_expires_at`, it blocks start, ingest, resync,
transformation, prediction, and persistence. `require_analysis` may continue
while access is current under the base research permission; after access
expiry it may continue only when `now < analysis_expires_at` and
`post_expiry_analysis` is granted. It always also requires
`now < raw_retention_until`; retention expiry permits deletion only, never
opening the retained bytes.
`require_export` additionally requires publication permission. If the wall
clock moves backward after a successful call, the gate fails closed instead
of extending access. Every operational gate call rechecks its injected
authoritative clock, including review/issue nonfuture status and
`now < qualified_until`; loader time is never treated as authority.
`require_raw_persist()` is deliberately zero-argument—no caller timestamp,
observation time, or default parameter is accepted—and returns the currently allowed
upper delete-by deadline:
`min(raw_retention_until, clock_now +
timedelta(seconds=max_raw_retention_seconds))`, encoded as integer UTC
nanoseconds. It rejects an equal/past upper bound or one earlier than
`request.required_retention_until`; it never chooses a record deadline and
never derives one from an observation timestamp. The retention runtime
chooses the earlier fixed session deadline
`request.required_retention_until`, proves it is no later than this returned
upper bound on every persistence call, and writes that same deadline to every
raw and derived record. Conversion uses integer epoch
days/seconds/microseconds multiplied by 1,000, never a float.
The gate never mutates or re-hashes the normalized request: its initial
`now_utc` remains stable request provenance, so the request and full-binding
hashes stay session-invariant. Every operational temporal predicate instead
uses the freshly sampled injected clock as a separate authoritative `as_of`
value; the initial timestamp cannot freeze entitlement time.
The injected environment mapping is also checked on every operational call
without copying or retaining credential values; removal/blanking of a required
variable fails closed, while messages expose only its declared variable name.

- [ ] **Step 4: Run every entitlement test**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_entitlements -v
```

Expected: PASS.

- [ ] **Step 5: Commit the gate**

```bash
git add tennis_v1/entitlements.py \
  tests/tennis_v1/test_entitlements.py
git commit -m "feat: enforce provider qualification gate"
```

### Task 4: Enforce physical retention before any provider WAL exists

**Files:**

- Create: `tennis_v1/retention.py`
- Create: `tennis_v1/schemas/retention-marker-v1.schema.json`
- Create: `tests/tennis_v1/test_retention.py`

Event Core Task 1 must already have created `tennis_v1.session`; Task 4 imports
only its immutable `SessionManifest` and digest helper. It does not import the
future sequencer or WAL modules.

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class RetentionMarker:
    schema_version: int
    session_id: str
    wal_basename: str
    reserve_basename: str
    delete_by_ns: int
    session_manifest_sha256: str
    provider_request_binding_sha256: str
    provider_manifest_file_sha256: str
    entitlement_id_sha256: str
    qualification_artifact_sha256: str
    created_at_ns: int


@dataclass(frozen=True, slots=True, init=False)
class ProviderWalWriteCapability:
    def write_all(self, frame: bytes) -> None: ...
    def write_halt_control(self, frame: bytes) -> None: ...
    def fsync(self) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True, init=False)
class ProviderWalReadCapability:
    def pread(self, *, offset: int, length: int) -> bytes: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PurgeReport:
    deleted_sessions: tuple[str, ...]
    recovered_markers: tuple[str, ...]


class RetentionError(RuntimeError): ...
class RetentionDueDeleteError(RetentionError): ...
class RetentionGlobalHalt(RetentionError): ...


class RetentionSessionAuthorizer(Protocol):
    @property
    def coordinator(self) -> "RetentionCoordinator": ...
    @property
    def session_manifest(self) -> SessionManifest: ...
    @property
    def bound_decision(self) -> QualificationDecision: ...
    def authorize_session(self) -> None: ...
    def authorize_raw_persistence(self) -> int: ...
    def authorize_analysis(self) -> QualificationDecision: ...


class RetentionCoordinator:
    @classmethod
    def acquire(
        cls,
        config: TennisV1Config,
        *,
        clock_ns: Callable[[], int],
    ) -> "RetentionCoordinator": ...

    def recover_and_purge(self) -> PurgeReport: ...
    def arm_before_wal(
        self,
        *,
        session_manifest: SessionManifest,
        decision: QualificationDecision,
        persistence_authorizer: RetentionSessionAuthorizer,
    ) -> ProviderWalWriteCapability: ...
    def issue_read_capability(
        self,
        *,
        persistence_authorizer: RetentionSessionAuthorizer,
    ) -> ProviderWalReadCapability: ...
    def require_provider_operation(self) -> None: ...
    def mark_clean_terminal(self, *, session_id: str) -> None: ...
    def close(self) -> None: ...


```

- [ ] **Step 1: Add physical-retention and crash-matrix tests**

```python
def test_private_fsynced_marker_is_durable_before_wal_creation(): ...
def test_marker_binds_exact_session_manifest_and_full_provider_request_sha(): ...
def test_arm_requires_same_prebuilt_manifest_bound_authorizer_and_decision(): ...
def test_authorizer_must_be_bound_to_the_same_coordinator_object(): ...
def test_write_and_read_capability_constructors_copy_pickle_and_reuse_fail(): ...
def test_halt_control_is_one_shot_strictly_typed_and_denied_when_session_due(): ...
def test_marker_binds_reserve_and_recovery_handles_every_reserve_crash_window(): ...
def test_state_marker_lock_and_wal_modes_are_0700_and_0600(): ...
def test_second_process_cannot_acquire_the_exclusive_retention_lock(): ...
def test_symlink_hardlink_wrong_owner_mode_and_nonregular_entries_halt(): ...
def test_recovery_removes_armed_marker_when_wal_was_never_created(): ...
def test_recovery_accepts_matching_not_due_marker_and_wal(): ...
def test_wal_without_marker_or_marker_wal_mismatch_globally_halts(): ...
def test_due_purge_unlinks_wal_then_fsyncs_before_marker_unlink(): ...
def test_due_unlink_or_fsync_failure_globally_halts_and_blocks_clean_terminal(): ...
def test_global_halt_revokes_all_live_read_and_write_capabilities(): ...
def test_only_retention_opens_unlinks_or_purges_a_provider_wal(): ...
```

- [ ] **Step 2: Run the focused test and observe the missing coordinator**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_retention -v
```

Expected: FAIL because `tennis_v1.retention` does not exist.

- [ ] **Step 3: Implement the retention state machine**

`RetentionCoordinator.acquire` opens `state_root`, `sessions`, and
`retention-markers` as private directories (`0700`) with dirfd traversal and
`O_DIRECTORY | O_NOFOLLOW`. It creates/opens one canonical
`retention.lock` (`0600`) and holds `flock(LOCK_EX | LOCK_NB)` for the
coordinator lifetime. Every marker and WAL must be a same-owner, regular,
single-link `0600` file; every lookup/create/unlink uses a held directory fd,
`O_CLOEXEC`, and `O_NOFOLLOW`. Paths, globbing, and re-resolution are not
authorization boundaries.

`arm_before_wal` receives the already-built `SessionManifest`, the exact
eligible `QualificationDecision` used to build it, and the already-bound
`RetentionSessionAuthorizer`. Event Core Task 4 supplies the concrete
`ProviderPersistenceAuthorizer`; Task 4 tests use a strict fake implementing
the same protocol. The coordinator never imports the sequencer. It never
rebuilds a partial binding. It
requires `persistence_authorizer.session_manifest` to be that exact manifest,
requires `persistence_authorizer.bound_decision is decision`, requires
`persistence_authorizer.coordinator is self`, calls
`require_decision_matches_session`, calls the authorizer's authoritative-clock
session check, and calls its zero-argument authoritative-clock raw persistence
check. `authorize_raw_persistence` accepts no timestamp. Swapping any
manifest, decision, request, authorizer, or gate fails before a marker or WAL
is created.

The marker stores
`session_manifest_sha256(session_manifest)` and the decision's
`provider_request_binding_sha256` from Task 3. It also retains the listed
high-value provider digests for direct recovery diagnostics. The full binding
digest is not reconstructed from a subset of `SessionManifest`; it is copied
from and compared with the exact decision, whose canonical projection covers
the complete `QualifiedProviderBinding` plus `request_sha256`.
`delete_by_ns` is the manifest's fixed
`required_retention_until_ns`, proven no later than the authorizer's current
allowed upper deadline.

The coordinator creates the canonical marker with
`O_CREAT | O_EXCL | O_NOFOLLOW`, writes canonical JSON, fsyncs the marker fd,
closes it, then fsyncs `retention-markers/`. Only after that succeeds does the
coordinator create the matching WAL with
`O_CREAT | O_EXCL | O_NOFOLLOW` and mode `0600`, retain ownership of its fd,
create and physically allocate the marker-bound `0600` reserve file, fsync
both files and the sessions directory, and return an opaque one-shot
`ProviderWalWriteCapability`. The marker names both exact basenames, so crash
recovery never treats the reserve as an unrelated entry. The coordinator
never exposes an fd or a path.

Both capability classes have raising public constructors, reject
copy/deepcopy/pickle, carry a coordinator-private unguessable identity, bind
one coordinator generation/session/manifest SHA/full-binding SHA, and redact
their repr. Every operation validates object identity, live generation,
thread/process ownership, global-halt state, and marker/WAL/reserve binding.
Capabilities cannot be reused after close, transferred to another
coordinator, or widened to another session.

Normal `write_all` and `fsync` operations are revoked by a global retention
halt. The sole exception is `write_halt_control`, which is one-shot and
accepts only a fully validated canonical `SESSION_HALT` control frame. It is
permitted only when this exact session's marker/WAL/reserve tuple is still
not due, identity-valid, and known durability-healthy. It cannot append raw or
derived data, cannot bypass expiry, and is permanently consumed whether it
succeeds or fails. A halt caused by another session may therefore leave an
auditable terminal; a halt involving this session's due or ambiguous storage
permits no further byte and leaves the WAL unclean.

`JournalWriter.create` accepts only the coordinator-issued write capability
and the exact bound `SessionManifest`; it accepts no config, path, fd, marker,
or caller-created protocol substitute. It performs writes and fsyncs only
through capability methods. Raw and derived encoders receive no deadline
parameter: they copy the marker's one `delete_by_ns` and reject any decoded
record whose deadline differs. Before every raw or derived write, the
capability invokes the exact bound `ProviderPersistenceAuthorizer`, whose
zero-argument `gate.require_raw_persist()` must still return an upper deadline
covering the marker deadline. Thus a session remains homogeneous across
observation times.

`recover_and_purge` runs while the exclusive lock is held, before any
provider network operation, WAL append, or replay:

- marker plus matching WAL and no reserve at `now_ns < delete_by_ns`: retain;
- marker with no WAL: treat as crash before creation or after WAL unlink;
  if the bound reserve exists, validate and unlink it and fsync the sessions
  directory first, then unlink the marker and fsync the marker directory;
- marker plus matching WAL and reserve after an unclean process exit: validate
  the reserve, unlink it, fsync the sessions directory, and retain the
  not-due WAL for authorized replay only;
- WAL with no marker, malformed/noncanonical marker, basename/session/digest
  mismatch, unbound or duplicate reserve, unexpected entry, symlink, hard
  link, owner/mode mismatch, or duplicate session: set the process-wide
  retention halt latch and raise;
- due marker plus WAL: unlink the WAL and its bound reserve when present by
  sessions dirfd, fsync the sessions directory, unlink the marker by marker
  dirfd, then fsync the marker directory.

Any due-delete unlink or fsync failure sets the process-wide halt latch before
returning. While latched, every provider start/network/regular-write/read
operation and `mark_clean_terminal` raises `RetentionGlobalHalt`; an unrelated
session cannot continue. Only the strictly bounded `write_halt_control`
exception above may run. A crash after WAL unlink leaves the marker as recovery
evidence. Failure to remove that marker on restart also halts. The latch is
not cleared in-process; an operator must repair the storage cause and a fresh
exclusive-lock startup must complete `recover_and_purge`.

Both `arm_before_wal` and `issue_read_capability` first require
`persistence_authorizer.coordinator is self`; coordinator identity is part of
the authority and cannot be satisfied by equal fields from another object.
`issue_read_capability` then calls the supplied session authorizer's
`authorize_analysis()`, requires that returned decision to remain eligible,
requires the authorizer's immutable bound decision and session manifest to
match the marker, and
matches its full provider/request binding SHA to the marker before any
provider byte is read. `JournalReader.open` accepts only that opaque read
capability—never a path, fd, config, or purge callback. Each header, length,
payload, checksum, scan, and iterator read calls
`ProviderWalReadCapability.pread`; the reader may not read ahead, mmap, retain
an fd, or cache bytes across an authorization boundary.

Immediately before every bounded `os.pread`, the capability uses the
coordinator's injected clock, rechecks the marker/WAL/session-manifest/full-
binding tuple, calls the bound gate's `require_analysis()`, and verifies its
current decision binding SHA. At or after `delete_by_ns`, the coordinator
revokes all capabilities for the session, performs the due purge, and returns
no bytes. The read capability never exposes an fd or data buffered before
authorization.

The coordinator alone owns recovery, unlink, directory fsync, due purge, and
the process-wide global-halt latch. `JournalReader` has no
`purge_expired_session` or equivalent API and cannot clear a halt. A static
AST/dependency test rejects provider-WAL `open`, `os.open`, `Path.open`,
`read_bytes`, `mmap`, `unlink`, `remove`, or purge implementation outside
`retention.py`; `wal.py` may access provider bytes only through the two exact
capability types.

Call `recover_and_purge` at process startup and on the runtime's bounded
retention timer before `require_provider_operation`. The coordinator's timer
is mandatory while provider data exists: it wakes at the earliest exact
`delete_by_ns` (and is signaled when an earlier marker is armed), serializes
with operations under an in-process `RLock`, and purges immediately; any timer
exception latches the global halt. Process downtime cannot execute deletion;
therefore the first action after restart is the locked purge, with all
provider bytes and network access blocked until it succeeds.
The marker schema sets `additionalProperties: false`, exact required keys,
canonical UUID/basename/digest patterns, and positive nonboolean nanoseconds.

- [ ] **Step 4: Run retention and entitlement tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_retention tests.tennis_v1.test_entitlements \
  tests.tennis_v1.test_events -v
```

Expected: PASS.

- [ ] **Step 5: Commit the retention boundary**

```bash
git add tennis_v1/retention.py \
  tennis_v1/schemas/retention-marker-v1.schema.json \
  tests/tennis_v1/test_retention.py
git commit -m "feat: enforce provider data retention boundary"
```

### Task 5: Add a deliberately disabled example and local preflight

**Files:**

- Create: `provider_manifest.example.json`
- Create: `tennis_v1/preflight.py`
- Create: `tests/tennis_v1/test_preflight.py`
- Create: `docs/tennis_v1/README.md`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class EntitlementPreflight:
    provider_id: str
    product_tier: str
    entitlement_id_sha256: str
    permission_artifact_sha256: str
    qualification_artifact_sha256: str
    qualification_trace_sha256: str
    access_expires_at: datetime
    analysis_expires_at: datetime
    raw_retention_until: datetime
    qualified_until: datetime
    planned_session_delete_by_ns: int
    requested_matches: int
    eligible: bool
    reasons: tuple[str, ...]
    export_allowed: bool


def run_entitlement_preflight(
    config: TennisV1Config,
    request: ResearchRequest,
    *,
    environ: Mapping[str, str],
) -> EntitlementPreflight: ...
```

- [ ] **Step 1: Add preflight and disabled-example tests**

```python
def test_committed_example_is_synthetic_ineligible_and_not_runtime_loadable(): ...
def test_preflight_reads_only_the_configured_local_manifest(): ...
def test_preflight_makes_no_network_calls_or_state_directories(): ...
def test_preflight_output_never_contains_environment_values(): ...
def test_expired_manifest_cannot_be_overridden_by_environment(): ...
def test_preflight_cannot_accept_a_caller_adapter_or_registry_root(): ...
def test_untrusted_reviewer_or_issuer_and_expired_qualification_fail(): ...
```

The example must use:

```json
{
  "provider_id": "EXAMPLE_DISABLED",
  "product_tier": "UNQUALIFIED_TEMPLATE",
  "entitlement_id": "NO_ENTITLEMENT",
  "billing_mode": "trial",
  "auto_renew": false
}
```

Its permission and qualification references are non-existent placeholder
paths and placeholder digests, all capabilities are `false`, and its dates
are syntactically valid but expired. It contains no legacy flat permission,
quota-demand, qualification-result, or request-override fields. The committed
example therefore cannot pass the pinned loaders. No real provider name,
credential name, reviewer/issuer, permission, or capability is claimed.

- [ ] **Step 2: Run focused tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_preflight -v
```

Expected: FAIL because `tennis_v1.preflight` is missing.

- [ ] **Step 3: Implement the read-only preflight**

`run_entitlement_preflight` loads the configured external manifest using the
pinned configuration digest, independently verifies the permission artifact
and evidence plus qualification artifact and trace, internally resolves the
active adapter closure, evaluates them, and returns redacted metadata. It
accepts no caller adapter, digest, registry root, permission, qualification,
or quota demand. It must not create the state root, import a network library,
or write an artifact. After it succeeds, the locked retention startup/recovery
in Task 4 must also succeed before any provider network or WAL action.
Document in `docs/tennis_v1/README.md`:

- Tennis v1 is not yet runnable;
- actual manifests and credentials remain outside Git;
- the config pins the exact external manifest digest;
- trial-terms snapshots or written permission remain outside Git and are
  independently digest-pinned;
- the committed example is intentionally blocked;
- no provider trial should be started until its terms and exact product tier
  pass review;
- no free trial auto-upgrades or subscribes;
- no provider permission claim is inferred from the example.
- access expiry blocks new data use immediately; post-expiry analysis requires
  separately granted permission and a current analysis deadline.
- qualification expires at `qualified_until` and cannot outlive its verified
  analysis window or the 30-day qualification freshness bound; trial
  qualification remains access-capped because trial analysis cannot extend
  beyond access;
- physical retention uses one session deadline, startup recovery, and a
  process-wide halt on any due-delete failure.

- [ ] **Step 4: Run Phase 1 entitlement and legacy suites**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest discover -s tests/tennis_v1 -p 'test_*.py' -v
"$INCI_TENNIS_PYTHON" tests.py
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_legacy_baseline -v
```

Expected: all Tennis entitlement tests pass and legacy reports
`ALL TESTS PASS (200 tests)`.

- [ ] **Step 5: Commit the preflight and documentation**

```bash
git add provider_manifest.example.json tennis_v1/preflight.py \
  tests/tennis_v1/test_preflight.py docs/tennis_v1/README.md
git commit -m "docs: add blocked provider entitlement preflight"
```

## Plan Verification

Before declaring this plan complete:

```bash
"$INCI_TENNIS_PYTHON" -m unittest discover -s tests/tennis_v1 -p 'test_*.py' -v
"$INCI_TENNIS_PYTHON" tests.py
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_legacy_baseline -v
"$INCI_TENNIS_PYTHON" -m compileall -q tennis_v1 tests/tennis_v1
git diff --check
```

Inspect all tracked Tennis files and confirm:

```bash
rg -n 'requests|httpx|urllib\.request|POST|DELETE|live_enabled|demo_enabled' \
  tennis_v1 tests/tennis_v1
```

Expected: no transport or order-enabling implementation. Words used only in
negative tests or documentation are reviewed manually.
