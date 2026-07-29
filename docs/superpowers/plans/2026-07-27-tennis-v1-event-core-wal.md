# Tennis v1 Deterministic Event Core and WAL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic Tennis v1 event runtime in which every raw
input is durably recorded before reduction, replay recomputes and verifies
every derived output, and a crash, corrupted tail, slow dashboard, or secret
payload can never become valid research evidence.

**Architecture:** Producers submit immutable captured-input drafts to one
thread-owned sequencer. A session-exclusive binary WAL assigns the only
`ingest_seq`, writes and `fsync`s the raw frame, and only then permits the
pure reducer to act. Derived frames are audit witnesses; exact replay ignores
them as inputs, recomputes them from raw frames, and byte-compares the trace.

**Tech Stack:** Python 3.12+ standard library (`dataclasses`,
`datetime`, `enum`, `fcntl`, `hashlib`, `json`, `os`, `pathlib`, `queue`,
`struct`, `threading`, `time`, `typing`, `unittest`).

## Global Constraints

- Follow the explicit cross-plan order: complete Provider Entitlement Tasks
  0 through 3; complete this plan's Task 1; return to Provider Entitlement
  Task 4 for the retention coordinator; then complete this plan's Tasks 2
  through 7 and Provider Entitlement Task 5. This plan consumes only
  interfaces that exist at each checkpoint; no placeholder retention API is
  permitted.
- Every command uses the absolute Python `>=3.12,<3.15` executable in
  `INCI_TENNIS_PYTHON`; bare `python` is forbidden.
- Work only in the isolated `feature/tennis-v1-foundation` worktree.
- Existing v6 files and its 200-test behavior remain unchanged.
- Honor `BASE_SNAPSHOT_UNCOMMITTED` from the entitlement plan: never stage the
  user-owned legacy changes, and defer every commit checkpoint until a clean
  base commit matches the protected legacy hash manifest.
- Do not import `bot`, `engine`, `executor`, `kalshi_client`,
  `market_data`, `order_journal`, `order_resolution`, `replay`, `safety`, or
  `research_log` anywhere under `tennis_v1/`.
- Do not import `requests` or expose any HTTP mutation interface.
- `ingest_seq` is the durable arrival order. Never sort by provider, exchange,
  event, generated, or wall-clock timestamp.
- All canonical times are integer nanoseconds; canonical JSON contains no
  floats.
- One WAL represents one process session. Phase 1 never resumes a crashed
  session.
- A raw frame is `fsync`ed before reducer invocation.
- A missing, halted, torn, corrupt, or trace-mismatched terminal cannot pass
  exact replay. Mechanical WAL validity and exact replay are distinct from
  research evaluability.
- Phase 1 always reports `research_evaluable=False`; Gates C through F of the
  complete design do not exist yet.
- The WAL is never silently repaired, truncated, or marked clean.
- Credentials and unsafe headers never enter a captured payload.
- The dashboard is observational and cannot block the writer or reducer.

---

### Task 1: Define immutable events and canonical record encoding

**Files:**

- Create: `tennis_v1/events.py`
- Create: `tennis_v1/capture.py`
- Create: `tennis_v1/codec.py`
- Create: `tennis_v1/fingerprints.py`
- Create: `tennis_v1/session.py`
- Create: `tests/tennis_v1/test_events.py`
- Create: `tests/tennis_v1/test_capture.py`
- Create: `tests/tennis_v1/test_codec.py`
- Create: `tests/tennis_v1/test_fingerprints.py`

**Interfaces:**

```python
class SourceKind(str, Enum):
    PROVIDER = "provider"
    KALSHI = "kalshi"
    TIMER = "timer"
    SYSTEM = "system"


class RecordKind(str, Enum):
    RAW = "raw"
    DERIVED = "derived"
    CONTROL = "control"


class ProvenanceState(str, Enum):
    ABSENT = "absent"
    SAFE_ORIGINAL = "safe_original"
    REDACTED = "redacted"


@dataclass(frozen=True, slots=True, init=False)
class ProvenanceEvidence:
    value: str | None
    state: ProvenanceState

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("use a safe provenance factory")


@dataclass(frozen=True, slots=True)
class SessionManifest:
    schema_version: int
    session_id: str
    created_wall_ns: int
    config_file_sha256: str
    config_canonical_sha256: str
    code_sha256: str
    research_request_sha256: str
    provider_id: str
    product_tier: str
    source_lineage_id: str
    provider_manifest_file_sha256: str
    provider_manifest_canonical_sha256: str
    entitlement_id_sha256: str
    terms_version: str
    permission_artifact_sha256: str
    qualification_artifact_sha256: str
    qualification_trace_sha256: str
    adapter_code_sha256: str
    auth_contract_sha256: str
    quota_contract_sha256: str
    session_end_ns: int
    required_retention_until_ns: int
    access_expires_at_ns: int
    analysis_expires_at_ns: int
    research_evaluable: Literal[False]


class SessionCaptureAuthorizer(Protocol):
    @property
    def session_manifest(self) -> SessionManifest: ...
    def authorize_capture(
        self, authority: "CaptureAuthority", captured: "CapturedInput"
    ) -> None: ...


@dataclass(frozen=True, slots=True, init=False)
class CapturedInput:
    session_id: str
    event_type: str
    event_version: int
    source_kind: SourceKind
    source_id: str
    source_entity_id: str
    endpoint_id: str | None
    endpoint_state: ProvenanceState
    channel_id: str | None
    channel_state: ProvenanceState
    request_id: str | None
    request_id_state: ProvenanceState
    source_wall_ns: int | None
    source_generated_ns: int | None
    local_wall_ns: int
    local_monotonic_ns: int
    clock_uncertainty_ns: int
    connection_epoch: int
    provider_sequence: str | None
    content_type: str
    payload_encoding: str
    payload_transform: str
    retention_delete_by_ns: int | None
    payload: bytes = field(repr=False)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("CapturedInput must be created by a safe capture factory")


@dataclass(frozen=True, slots=True, init=False)
class CaptureAuthority:
    session_id: str
    source_kind: SourceKind
    source_id: str
    source_entity_id: str
    endpoint_id: str | None
    endpoint_state: ProvenanceState
    channel_id: str | None
    channel_state: ProvenanceState
    connection_epoch: int
    _session_authorizer: SessionCaptureAuthorizer = field(
        repr=False, compare=False)
    _wall_clock_ns: Callable[[], int] = field(repr=False, compare=False)
    _monotonic_clock_ns: Callable[[], int] = field(repr=False, compare=False)
    _clock_uncertainty_ns: Callable[[], int] = field(
        repr=False, compare=False)
    _allowed_content_types: tuple[str, ...] = field(repr=False)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("CaptureAuthority must be issued by the session runtime")


@dataclass(frozen=True, slots=True)
class DerivedDraft:
    event_type: str
    event_version: int
    payload_encoding: str
    payload: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class PersistedEvent:
    journal_version: int
    record_kind: RecordKind
    ingest_seq: int
    session_id: str
    event_type: str
    event_version: int
    source_kind: SourceKind
    source_id: str
    source_entity_id: str
    endpoint_id: str | None
    endpoint_state: ProvenanceState
    channel_id: str | None
    channel_state: ProvenanceState
    request_id: str | None
    request_id_state: ProvenanceState
    source_wall_ns: int | None
    source_generated_ns: int | None
    local_wall_ns: int
    local_monotonic_ns: int
    clock_uncertainty_ns: int
    connection_epoch: int
    provider_sequence: str | None
    parent_ingest_seq: int | None
    content_type: str
    payload_encoding: str
    payload_transform: str
    retention_delete_by_ns: int | None
    payload_sha256: str
    payload: bytes = field(repr=False)


# imported from tennis_v1.canonical
def canonical_metadata(event: PersistedEvent) -> bytes: ...
def canonical_record_sha256(event: PersistedEvent) -> str: ...
def encode_record(event: PersistedEvent) -> tuple[bytes, bytes]: ...
def decode_record(metadata: bytes, payload: bytes) -> PersistedEvent: ...
def capture_public_json(
    raw_json: bytes,
    *,
    authority: CaptureAuthority,
    content_type: str,
    request_id: ProvenanceEvidence,
    event_type: str,
    event_version: int,
    source_wall_ns: int | None,
    source_generated_ns: int | None,
    provider_sequence: str | None,
) -> CapturedInput: ...
def capture_redacted_json(
    raw_json: bytes,
    *,
    authority: CaptureAuthority,
    content_type: str,
    request_id: ProvenanceEvidence,
    event_type: str,
    event_version: int,
    source_wall_ns: int | None,
    source_generated_ns: int | None,
    provider_sequence: str | None,
) -> CapturedInput: ...
def capture_transport_error(
    *,
    exception_type: str,
    status_code: int | None,
    error_code: str | None,
    request_id: ProvenanceEvidence,
    authority: CaptureAuthority,
    event_type: str,
    event_version: int,
) -> CapturedInput: ...
def issue_capture_authority(
    *,
    session_authorizer: SessionCaptureAuthorizer,
    source_kind: SourceKind,
    source_id: str,
    source_entity_id: str,
    endpoint: ProvenanceEvidence,
    channel: ProvenanceEvidence,
    connection_epoch: int,
    allowed_content_types: tuple[str, ...],
    wall_clock_ns: Callable[[], int],
    monotonic_clock_ns: Callable[[], int],
    clock_uncertainty_ns: Callable[[], int],
) -> CaptureAuthority: ...
def absent_provenance() -> ProvenanceEvidence: ...
def safe_provenance(value: str) -> ProvenanceEvidence: ...
def redacted_provenance() -> ProvenanceEvidence: ...
def build_session_manifest(
    *,
    config: TennisV1Config,
    provider_manifest: ProviderManifest,
    qualification: QualificationDecision,
    session_id: str,
    created_wall_ns: int,
    code_sha256: str,
) -> SessionManifest: ...
def session_manifest_sha256(manifest: SessionManifest) -> str: ...
def require_decision_matches_session(
    decision: QualificationDecision,
    manifest: SessionManifest,
) -> None: ...
def code_sha256(package_root: str | Path) -> str: ...
def new_session_id() -> str: ...
```

- [ ] **Step 1: Write immutable event-validation tests**

```python
def test_captured_input_requires_nonnegative_integer_times_and_epoch(): ...
def test_source_and_event_identifiers_are_nonempty_safe_strings(): ...
def test_provider_sequence_is_an_opaque_string_not_an_integer_clock(): ...
def test_payload_is_bytes_and_sha256_matches_exact_durable_bytes(): ...
def test_derived_parent_must_precede_derived_ingest_sequence(): ...
def test_canonical_records_reject_floats_unknown_keys_and_unknown_enums(): ...
def test_provider_raw_requires_future_retention_deadline(): ...
def test_nonprovider_raw_rejects_provider_retention_deadline(): ...
def test_code_fingerprint_covers_sorted_source_and_schema_bytes(): ...
def test_code_fingerprint_rejects_symlinked_or_unknown_artifacts(): ...
def test_new_session_id_is_unique_canonical_uuid_and_not_config_driven(): ...
def test_session_manifest_requires_verified_eligible_matching_inputs(): ...
def test_session_manifest_copies_every_current_qualification_binding_field(): ...
def test_session_manifest_fixes_one_provider_delete_by_for_whole_session(): ...
def test_session_manifest_is_phase_one_nonresearch_evaluable(): ...
def test_decoder_requires_research_evaluable_is_literal_false(): ...
def test_direct_capture_and_authority_construction_raise_explicitly(): ...
def test_only_capture_module_may_use_private_sentinel_constructor(): ...
def test_authority_requires_exact_session_bound_provider_authorizer(): ...
def test_authority_fixes_source_entity_endpoint_channel_epoch_and_clock_fields(): ...
def test_endpoint_and_channel_are_distinct_bounded_provenance_fields(): ...
def test_endpoint_channel_and_request_each_preserve_absent_safe_or_redacted_state(): ...
def test_provenance_objects_require_safe_factories_and_never_store_redacted_input(): ...
def test_content_type_and_typed_endpoint_channel_request_are_captured(): ...
def test_public_json_rejects_duplicate_keys_secrets_headers_and_url_queries(): ...
def test_redacted_json_removes_nested_case_variant_secrets_deterministically(): ...
def test_transport_error_accepts_only_allowlisted_typed_fields(): ...
def test_capture_rejects_oversize_or_overdeep_json_before_decode(): ...
def test_session_authorizer_revalidates_every_envelope_field_and_payload_byte(): ...
def test_capture_checks_coordinator_halt_before_returning_an_envelope(): ...
def test_secret_sentinel_absent_from_capture_repr_errors_and_encoded_bytes(): ...
```

Include round trips containing UTF-8, newline, NUL, and arbitrary bytes:

```python
payload = b'{"line":"one\\ntwo","nul":"\\u0000"}\x00\xff'
metadata, durable_payload = encode_record(event_with(payload))
decoded = decode_record(metadata, durable_payload)
self.assertEqual(decoded, event_with(payload))
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_events \
  tests.tennis_v1.test_capture tests.tennis_v1.test_codec \
  tests.tennis_v1.test_fingerprints -v
```

Expected: FAIL because the modules do not exist.

- [ ] **Step 3: Implement strict canonical serialization**

Canonical metadata uses:

```python
json.dumps(
    value,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=True,
    allow_nan=False,
).encode("utf-8")
```

`canonical_json_bytes` from `tennis_v1.canonical` is the single canonical JSON implementation used by
config hashes, manifests, session manifests, records, derived payloads,
terminal payloads, state, and trace entries. It recursively rejects floats,
non-string mapping keys, and unsupported values before serialization.
The metadata schema has an exact key set and stores
`payload_sha256=sha256(payload).hexdigest()`. `decode_record` rejects a
mismatch.

`CapturedInput`, `CaptureAuthority`, and `ProvenanceEvidence` have explicit
raising public
constructors. `capture.py` owns a module-private sentinel and private
`object.__new__`/`object.__setattr__` builders; only `issue_capture_authority`
and the safe provenance/capture factories may call them. The dependency AST
test rejects direct construction or private-builder references from any other
package file.

`issue_capture_authority` accepts only a `SessionCaptureAuthorizer` already
bound to the exact `SessionManifest`; the production implementation is the
concrete `ProviderPersistenceAuthorizer` introduced in Task 4. It fixes the session, source
kind/ID, source entity, separate endpoint and channel fields, connection epoch,
allowed content types, injected local clocks, clock-uncertainty sampler, and session retention
policy. A provider authority requires
`source_id == session_manifest.provider_id`; Kalshi/system/timer authorities
use separately allowlisted fixed IDs. Only the session bootstrap/runtime may
call the issuer; the dependency AST test rejects calls from adapters and other
package files. Factories sample local wall, monotonic, and measured uncertainty
values from this authority. Endpoint and channel enter authority issuance only
as `ProvenanceEvidence`; request ID enters each capture factory through the
same type. For every field, `ABSENT` requires `value=None`, `SAFE_ORIGINAL`
requires a nonempty bounded safe-identifier value, and `REDACTED` requires the
literal `"<redacted>"`. `redacted_provenance()` accepts no source value, so no
unsafe identifier or hash is retained; `safe_provenance(value)` validates
before constructing. Factories copy the authority's epoch/entity/typed
endpoint/channel and fixed marker-backed manifest retention deadline.
Adapters may supply only permitted source timestamps, source
sequence/revision, body bytes, content type, and typed request-ID evidence;
they cannot choose, backdate, or relabel runtime-owned facts.

After privately constructing a candidate, every factory calls
`session_authorizer.authorize_capture(authority, candidate)` before returning
it. The concrete authorizer first checks the coordinator's global halt, then
revalidates the complete envelope against the exact authority/manifest:
session/source/entity, endpoint, channel, request-ID value/state, source/local
times, uncertainty, epoch, sequence, content type, encoding, transformation,
homogeneous marker deadline, payload digest, payload length, and the full
content/secret policy. The writer performs the same shared validation before
frame construction. Failure inside a factory returns no envelope and is
retryable; an uninitialized, forged, or altered `CapturedInput` that reaches
the runtime is instead a fatal `capture_contract_violation`, never a retryable
adapter error.

Use these hard bounds:

```python
MAX_CAPTURE_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 250_000
```

Each JSON factory rejects `len(raw_json) > MAX_CAPTURE_BYTES` before decoding,
performs a string-aware byte prescan that rejects nesting deeper than
`MAX_JSON_DEPTH` before `json.loads`, performs strict UTF-8 decoding with BOM
rejection,
rejects duplicate keys with `object_pairs_hook`, and recursively accepts only
dictionaries with string keys, lists, strings, integers, booleans, and null.
The recursive validator counts all containers, keys, and scalar values and
rejects more than `MAX_JSON_NODES`. It is iterative rather than recursively
walking attacker-controlled depth. The supplied media type must be one exact
normalized member of the authority's bounded allowlist; the transport-error
factory assigns
`application/vnd.inci.transport-error+json` itself. Unknown, parameter-smuggled,
blank, or control-containing content types fail before payload parsing or
journaling.
`capture_public_json` rejects secret-shaped content. `capture_redacted_json`
replaces secret values with `"<redacted>"` before the durable payload exists.
Keys are normalized by removing punctuation and folding case, then matched
against:

```python
SECRET_KEYS = frozenset({
    "authorization", "cookie", "setcookie", "apikey", "accesstoken",
    "refreshtoken", "bearer", "token", "secret", "password", "passwd",
    "privatekey", "signature", "credential", "clientsecret",
    "kalshiaccesskey", "kalshiaccesssignature",
})
```

Both factories reject header collections, request objects, PEM markers,
credential-like environment maps, and any HTTP(S) URL containing user
information, a query, or a fragment. `capture_transport_error` constructs its
own canonical JSON solely from the four allowlisted typed arguments; it never
accepts an exception object, URL, headers, arbitrary mapping, or
`str(exception)`. No pre-redaction value or hash is stored. Every payload
field uses `repr=False`; errors contain stable reason codes only.
Factory-supplied event/source identifiers use a bounded allowlist grammar that
excludes whitespace, `/`, `?`, `#`, `@`, `=`, and control characters, so they
cannot smuggle a URL, header, or query string outside the payload checks.

Allowed payload transformations are assigned by the factories and exactly
`identity-public-market-v1`, `json-secret-redaction-v1`, and
`sanitized-transport-error-v1`. Unknown content types or transformations fail
before journaling. All provider raw and derived records in one session use
exactly `session_manifest.required_retention_until_ns` as their homogeneous
`retention_delete_by_ns`; the session builder has already proved that this
deadline is permitted for the earliest capture. Every later gate call must
still authorize a deadline at least that late. Kalshi, timer, system, and
control records require `retention_delete_by_ns=None`. Because provider and
derived bytes are mixed in one append-only file, expiry deletes the entire
session WAL rather than trying to redact individual frames.

`code_sha256` hashes the sorted relative paths and exact bytes of every `.py`
file and JSON schema beneath `tennis_v1/`, rejecting symlinks and unexpected
file types. It excludes `__pycache__` and compiled files. `opaque_id_sha256`
is imported from the entitlement module, which already owns that
domain-separated contract. `new_session_id` returns lowercase canonical UUID4
text. It is called once when creating a new WAL and is never regenerated or
read from reusable configuration.

`build_session_manifest` is the only session-manifest constructor used by the
runtime. It consumes the frozen config, the already digest-verified provider
manifest, and an eligible `QualificationDecision`; verifies that their
provider, product tier, lineage, manifest file/canonical hashes, request,
entitlement, permission artifact, qualification artifact/trace, adapter,
auth/quota contracts, and time-window bindings match exactly; hashes the
opaque entitlement ID; fixes the homogeneous provider delete-by to
`required_retention_until_ns`; and fixes `research_evaluable=False`.
`require_decision_matches_session` is the one exhaustive comparison used by
bootstrap, every concrete authorizer boundary, and replay. It calls
`decision.require_eligible()`, requires a non-null binding, and compares every
decision/binding field represented in the manifest—never a provider ID or one
digest alone. The builder rejects a caller-supplied mapping, an ineligible or
mismatched decision, or an expired session window. The later retention
coordinator computes the confined WAL and reserve basenames, arms the marker,
and performs exclusive creation; neither the writer nor this task accepts or
computes a WAL path. The manifest's canonical bytes are persisted as the
`SESSION_START` authority.
`SessionManifest.__post_init__`, capability-only `JournalWriter.create`, the
`SESSION_START` decoder, and replay independently require
`research_evaluable is False` (identity, not falsey equality). A manually
constructed manifest carrying `0`, `None`, or `True` is rejected before file
creation or trust.

- [ ] **Step 4: Run codec tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_events \
  tests.tennis_v1.test_capture tests.tennis_v1.test_codec \
  tests.tennis_v1.test_fingerprints -v
```

Expected: PASS.

- [ ] **Step 5: Commit the event contract**

```bash
git add tennis_v1/events.py tennis_v1/capture.py tennis_v1/codec.py \
  tennis_v1/fingerprints.py tennis_v1/session.py \
  tests/tennis_v1/test_events.py tests/tennis_v1/test_capture.py \
  tests/tennis_v1/test_codec.py \
  tests/tennis_v1/test_fingerprints.py
git commit -m "feat: define canonical Tennis event records"
```

### Task 2: Implement the framed, checksummed, exclusive WAL

**Files:**

- Create: `tennis_v1/wal.py`
- Create: `tests/tennis_v1/test_wal.py`

**Interfaces:**

```python
# Opaque ProviderWalWriteCapability, ProviderWalReadCapability, and
# RetentionCoordinator are imported from tennis_v1.retention. Only the
# coordinator can construct the capabilities.

class ScanIssue(str, Enum):
    MISSING_TERMINAL = "missing_terminal"
    HALTED_TERMINAL = "halted_terminal"
    TORN_TAIL = "torn_tail"
    CORRUPT_TAIL = "corrupt_tail"


class JournalValidationError(ValueError): ...
class JournalDurabilityError(OSError): ...
class JournalCorruptionError(RuntimeError): ...
class DiskLowError(RuntimeError): ...


@dataclass(frozen=True, slots=True)
class ScanSummary:
    file_size: int
    last_good_offset: int
    last_good_ingest_seq: int
    record_count: int
    raw_count: int
    derived_count: int
    terminal_clean: bool
    issue: ScanIssue | None
    wal_valid: bool


class JournalWriter:
    @classmethod
    def create(
        cls,
        *,
        write_capability: ProviderWalWriteCapability,
        session_manifest: SessionManifest,
    ) -> "JournalWriter": ...

    @property
    def session_manifest(self) -> SessionManifest: ...
    @property
    def session_start(self) -> PersistedEvent: ...
    @property
    def poisoned(self) -> bool: ...

    def append_raw(self, captured: CapturedInput) -> PersistedEvent: ...
    def append_derived(
        self,
        parent: PersistedEvent,
        draft: DerivedDraft,
    ) -> PersistedEvent: ...
    def close_clean(
        self,
        *,
        reason: str,
        trace_sha256: str,
        final_state_sha256: str,
        last_applied_raw_seq: int,
    ) -> PersistedEvent: ...
    def close_halted(
        self,
        *,
        reason: str,
        trace_sha256: str,
        final_state_sha256: str,
        last_applied_raw_seq: int,
    ) -> PersistedEvent: ...


class JournalReader:
    @classmethod
    def open(
        cls,
        *,
        read_capability: ProviderWalReadCapability,
    ) -> "JournalReader": ...
    def __enter__(self) -> "JournalReader": ...
    def __exit__(self, *exc_info: object) -> None: ...
    def close(self) -> None: ...
    def read_session_manifest(self) -> SessionManifest: ...
    def scan(self, *, require_clean: bool = False) -> ScanSummary: ...
    def iter_records(
        self, *, diagnostic_prefix: bool = False
    ) -> Iterator[PersistedEvent]: ...
```

- [ ] **Step 1: Write binary framing and durability tests**

Required tests:

```python
def test_writer_requires_coordinator_issued_capability_and_exact_armed_marker(): ...
def test_writer_create_accepts_only_opaque_capability_and_exact_manifest(): ...
def test_writer_requires_research_evaluable_is_literal_false(): ...
def test_capability_manifest_marker_session_and_binding_mismatch_fail_prewrite(): ...
def test_session_start_is_sequence_one_durable_and_matches_manifest(): ...
def test_every_append_completes_write_loop_and_fsync_before_return(): ...
def test_frame_round_trip_preserves_arbitrary_payload_bytes(): ...
def test_unknown_versions_flags_kinds_lengths_and_oversize_fail(): ...
def test_metadata_payload_digest_and_trailer_corruption_fail(): ...
def test_sequence_duplicate_regression_gap_and_forward_parent_fail(): ...
def test_duplicate_or_nonfinal_terminal_fails(): ...
def test_terminal_payload_counts_hashes_and_reason_are_exact(): ...
def test_writer_never_appends_after_terminal(): ...
def test_forged_capture_validation_error_writes_no_raw_and_leaves_writer_healthy_for_halt(): ...
def test_zero_byte_partial_write_and_fsync_failures_poison_writer(): ...
def test_poisoned_writer_cannot_append_or_write_any_terminal(): ...
def test_every_healthy_writer_halt_requires_state_trace_and_last_applied_witnesses(): ...
def test_disk_low_halts_with_reserved_terminal_space_before_next_raw(): ...
def test_halted_terminal_uses_only_one_shot_halt_control_capability(): ...
def test_global_halt_for_other_session_can_write_control_but_due_session_cannot(): ...
def test_reserve_is_marker_bound_and_recovered_or_purged_after_crash(): ...
def test_scan_summary_is_bounded_and_iter_records_streams_large_session(): ...
def test_secret_sentinel_is_absent_from_every_wal_byte_repr_and_error(): ...
def test_reader_requires_coordinator_issued_read_capability(): ...
def test_reader_open_accepts_only_opaque_capability_not_path_fd_or_callback(): ...
def test_every_header_metadata_payload_digest_and_trailer_read_uses_coordinator(): ...
def test_reader_rechecks_capability_before_each_bounded_range_and_never_read_aheads(): ...
```

For every byte boundary in the last frame:

```python
for cut in range(frame_start + 1, file_size):
    damaged = original[:cut]
    summary = scan_bytes(damaged)
    self.assertEqual(summary.issue, ScanIssue.TORN_TAIL)
    self.assertEqual(summary.last_good_ingest_seq, prior_seq)
```

- [ ] **Step 2: Run the focused WAL tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_wal -v
```

Expected: FAIL because `tennis_v1.wal` does not exist.

- [ ] **Step 3: Implement the exact binary container**

Use:

```python
FILE_PREFIX = struct.Struct(">8sHHI")
FILE_MAGIC = b"INCIWAL\x00"
FILE_VERSION = 1
FILE_FLAGS = 0

FRAME_PREFIX = struct.Struct(">4sBBHQQII")
FRAME_MAGIC = b"EVT1"
FRAME_VERSION = 1
FRAME_FLAGS = 0
FRAME_KIND = {
    RecordKind.RAW: 1,
    RecordKind.DERIVED: 2,
    RecordKind.CONTROL: 3,
}
FRAME_TRAILER = struct.Struct(">Q4s")
TRAILER_MAGIC = b"1TVE"
MAX_FRAME_BYTES = 16 * 1024 * 1024
MIN_FREE_BYTES = 64 * 1024 * 1024
DISK_HALT_RESERVE_BYTES = 1024 * 1024
```

The 16-byte file prefix fields, in order, are `magic`, unsigned 16-bit
`version`, unsigned 16-bit `flags`, and unsigned 32-bit `header_length`.
Version 1 requires `flags == FILE_FLAGS` and
`header_length == FILE_PREFIX.size`; every other value fails closed.

The 32-byte frame prefix fields, in order, are `magic`, unsigned 8-bit
`version`, unsigned 8-bit numeric record kind, unsigned 16-bit `flags`,
unsigned 64-bit `ingest_seq`, unsigned 64-bit `total_frame_length`, unsigned
32-bit `metadata_length`, and unsigned 32-bit `payload_length`. The numeric
kind must be the exact inverse of `FRAME_KIND`, must agree with the canonical
metadata's string `record_kind`, and version 1 requires
`flags == FRAME_FLAGS`. There are no ignored or reserved nonzero bits.

Each frame is:

```text
32-byte prefix
canonical metadata
exact payload
32-byte SHA-256 frame digest
12-byte repeated-length trailer
```

`total_frame_length` includes the 32-byte prefix, metadata, payload, 32-byte
digest, and 12-byte trailer. It must equal
`76 + metadata_length + payload_length`, be at most `MAX_FRAME_BYTES`, and
match the repeated trailer value before any variable-length allocation.

The digest is:

```python
hashlib.sha256(
    b"INCI-FRAME-V1\0"
    + prefix_bytes
    + metadata_bytes
    + payload_bytes
).digest()
```

The coordinator-issued write capability internally owns a marker-bound,
private, physically allocated, fsynced sibling reserve of
`DISK_HALT_RESERVE_BYTES`; sparse allocation does not qualify. The retention
marker names the reserve, and startup recovery validates, removes, or purges
it using the crash matrix from Provider Entitlement Task 4. The writer has no
directory/path API. Its
`write_all` implementation verifies, before every nonterminal frame, free
bytes greater than
`MIN_FREE_BYTES + DISK_HALT_RESERVE_BYTES + encoded_frame_length`. A failed
prewrite check consumes no sequence and raises `DiskLowError`. The runtime
globally closes inputs and appends one sanitized `disk_low` halted terminal
while the writer remains healthy; `JournalWriter.close_halted` uses the
capability's strict one-shot `write_halt_control`, which internally
releases/`fsync`s the reserve first.
Capability `close` removes any remaining reserve after a clean/halted terminal
is durable. If an actual
write/`fsync` fails despite the reserve,
normal uncertain-durability poison rules win and no terminal is fabricated.

Session bootstrap uses only this sequence:

```python
write_capability = coordinator.arm_before_wal(
    session_manifest=session_manifest,
    decision=persistence_authorizer.bound_decision,
    persistence_authorizer=persistence_authorizer,
)
writer = JournalWriter.create(
    write_capability=write_capability,
    session_manifest=session_manifest,
)
```

`ProviderWalWriteCapability` is opaque, single-consumer, and constructible only
by the exclusively locked coordinator after the canonical marker is durable.
It binds the exact marker SHA, session ID, WAL basename, homogeneous
delete-by, provider binding, and expected session-manifest SHA. The writer
requires exact equality among the capability's marker binding and supplied manifest
before taking ownership; mismatch consumes the capability, writes no byte, and
latches the coordinator's global halt. No writer constructor accepts a path,
fd, config, directory descriptor, or unarmed manifest, and `wal.py` never
creates or reopens a provider WAL.

The capability supplies the already-created exclusive append descriptor only
to this one writer and exposes only `write_all`, `write_halt_control`,
`fsync`, and `close`, not a reusable raw fd. Regular frames use
`write_all(frame)`; a halted terminal uses only `write_halt_control(frame)`.
The capability owns the explicit complete-write loop followed by `fsync`.
Immediately append and `fsync` `SESSION_START` as
control sequence 1; its payload is the canonical `SessionManifest`. The writer
owns sequence allocation across raw, derived, and control records.
Control metadata is not adapter-supplied: `SESSION_START` and the unique
terminal use `source_kind=SYSTEM`, `source_id="tennis-v1"`,
`source_entity_id=session_id`, `endpoint_id=None`,
`endpoint_state=ABSENT`, `channel_id="session-control"`,
`channel_state=SAFE_ORIGINAL`, `request_id=None`,
`request_id_state=ABSENT`, source/generated timestamps `None`, local wall/monotonic
times plus epoch/uncertainty integer sentinel zero,
`provider_sequence=None`, and `retention_delete_by_ns=None`.
`SESSION_START` uses
`content_type="application/vnd.inci.session-manifest+json"`; the terminal uses
`content_type="application/vnd.inci.session-terminal+json"`. The decoder
requires those exact event-type-specific values, so control records cannot
masquerade as captured input.
Phase 1 deliberately `fsync`s every derived witness as the simplest auditable
contract. Throughput is benchmarked before Phase 2; any future batching policy
requires a new format/terminal contract and crash tests rather than a silent
optimization.

Canonical validation and frame construction finish before sequence
reservation or the first write. A validation failure therefore leaves the
writer known healthy and consumes no raw sequence, allowing `EventRuntime` to
write the mandatory fatal halted terminal. It is not permission to retry a
forged returned capture. Only a safe factory rejection before any
`CapturedInput` is returned/admitted is retryable. Once any capability write is attempted,
any zero-byte write, partial-write exception, close error, or `os.fsync`
failure creates an uncertain durable state: the writer permanently marks
itself poisoned, closes its descriptor best-effort, never reuses that
sequence, and rejects every later append and both terminal methods. Creation
or parent-directory `fsync` failure is treated the same way. No caller may
infer from an exception that zero bytes reached storage.

The unique final terminal control payload has this exact schema:

```json
{
  "terminal_version": 1,
  "clean": true,
  "reason": "operator_stop",
  "trace_sha256": "<64 lowercase hex>",
  "final_state_sha256": "<64 lowercase hex>",
  "record_count_before_terminal": 42,
  "raw_count": 14,
  "derived_count": 14,
  "last_applied_raw_seq": 40,
  "config_file_sha256": "<64 lowercase hex>",
  "config_canonical_sha256": "<64 lowercase hex>",
  "code_sha256": "<64 lowercase hex>",
  "session_manifest_sha256": "<64 lowercase hex>",
  "provider_manifest_file_sha256": "<64 lowercase hex>",
  "provider_manifest_canonical_sha256": "<64 lowercase hex>",
  "entitlement_id_sha256": "<64 lowercase hex>",
  "permission_artifact_sha256": "<64 lowercase hex>",
  "qualification_artifact_sha256": "<64 lowercase hex>",
  "qualification_trace_sha256": "<64 lowercase hex>",
  "adapter_code_sha256": "<64 lowercase hex>",
  "auth_contract_sha256": "<64 lowercase hex>",
  "quota_contract_sha256": "<64 lowercase hex>",
  "required_retention_until_ns": 1800000000000000000,
  "research_evaluable": false
}
```

`reason` is nonempty. `research_evaluable` is the literal JSON `false` in
Phase 1 and cannot be supplied by a caller. A clean terminal requires every
digest, `final_state_sha256`, and `last_applied_raw_seq`. Every halted terminal
also requires the runtime's current trace, final-state SHA, and last-applied
raw sequence and cannot pass exact replay. `EventRuntime`, not its caller,
computes these witnesses. If they are unavailable because a write may have
partially reached storage, the writer is durability-uncertain and no terminal
attempt is allowed; `null` is never encoded as a substitute.

Scanner behavior:

- EOF between complete frames with no terminal:
  `MISSING_TERMINAL`, verified prefix retained, cannot pass exact replay.
- EOF inside the final frame: `TORN_TAIL`, verified prefix retained, not
  eligible for exact replay.
- A final structurally complete frame with a bad digest:
  `CORRUPT_TAIL`, verified prefix retained, cannot pass exact replay.
- Corruption before the physical tail, sequence errors, invalid parents, or
  bytes after terminal: raise `JournalCorruptionError`.
- Never modify the source WAL.

`ScanSummary` contains counts and offsets only; it never retains record
objects or payloads. `iter_records` validates and yields one frame at a time.
Exact replay consumes that iterator in one pass. A memory-bound regression
creates enough maximum-practical frames to prove peak memory is independent
of record count within a small fixed buffer allowance.
`wal_valid=True` means every byte in the file belongs to a structurally valid
frame sequence; a valid prefix with no terminal or a valid halted terminal can
therefore be WAL-valid but cannot pass exact replay. Torn/corrupt bytes set it
false. Interior corruption raises and never yields a misleading summary.

Capability-only `JournalReader.open` accepts an opaque
coordinator-issued `ProviderWalReadCapability` already bound to the exact
not-due marker, session, provider, manifest digest, and authorizer binding. It
stores no fd or path. For the file prefix and every frame prefix, metadata
chunk, payload chunk, digest, and trailer, it calls only
`read_capability.pread(offset=..., length=...)`. The capability uses its
coordinator's trusted clock and rechecks global halt plus marker/WAL identity and deadline
immediately before every bounded range and returns only that range. The reader
never reads ahead, buffers an unauthorized later range, calls `open`,
`os.open`, `pread`, `mmap`, `seek`, `read_bytes`, or performs purge/unlink.
`close` returns the read capability to the coordinator. Purge, descriptor
ownership, locks, inode/mode/link checks, and parent-directory `fsync` remain
exclusively in `retention.py`.

- [ ] **Step 4: Run WAL tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_wal -v
```

Expected: PASS.

- [ ] **Step 5: Commit the durable store**

```bash
git add tennis_v1/wal.py tests/tennis_v1/test_wal.py
git commit -m "feat: add crash-detecting Tennis event WAL"
```

### Task 3: Add the pure reducer and deterministic trace chain

**Files:**

- Create: `tennis_v1/state.py`
- Create: `tennis_v1/reducer.py`
- Create: `tests/tennis_v1/test_reducer.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class FoundationState:
    session_id: str
    last_applied_raw_seq: int
    raw_count: int
    derived_count: int
    source_epochs: tuple[tuple[SourceKind, str, int], ...]


@dataclass(frozen=True, slots=True)
class Reduction:
    state: FoundationState
    outputs: tuple[DerivedDraft, ...]


def initial_state(session_id: str) -> FoundationState: ...
def reduce_event(
    state: FoundationState,
    event: PersistedEvent,
) -> Reduction: ...
def canonical_state_bytes(state: FoundationState) -> bytes: ...
def initial_trace(session_start: PersistedEvent) -> bytes: ...
def next_trace(
    prior_trace: bytes,
    raw: PersistedEvent,
    derived: tuple[PersistedEvent, ...],
    state: FoundationState,
) -> bytes: ...
```

- [ ] **Step 1: Write reducer and trace tests**

```python
def test_reducer_accepts_only_raw_records_for_its_session(): ...
def test_reducer_rejects_duplicate_or_regressed_raw_application(): ...
def test_source_epoch_never_regresses_within_a_source(): ...
def test_reducer_has_no_clock_random_filesystem_or_network_dependency(): ...
def test_same_state_and_event_produce_byte_identical_reduction(): ...
def test_reduction_has_one_state_authority_and_no_independent_state_bytes(): ...
def test_trace_changes_for_payload_output_order_parent_or_state_change(): ...
def test_trace_changes_when_any_raw_or_derived_metadata_field_changes(): ...
def test_trace_seed_binds_complete_session_start_record_metadata_and_payload(): ...
def test_sorted_frozen_state_is_independent_of_mapping_insertion_order(): ...
```

For Phase 1, the reducer emits one `raw_accepted` derived draft whose canonical
payload contains only:

```json
{
  "input_event_type": "provider_frame",
  "input_payload_sha256": "<64 lowercase hex>",
  "parent_ingest_seq": 2,
  "source_id": "provider-a"
}
```

This deliberately proves durability/replay mechanics without pretending to
understand tennis or Kalshi payloads before Phase 2.

- [ ] **Step 2: Run the focused reducer tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_reducer -v
```

Expected: FAIL because reducer modules do not exist.

- [ ] **Step 3: Implement the pure reducer and trace**

`reduce_event` must have no imports of `time`, `random`, `os`, `pathlib`,
network libraries, or legacy Inci modules. It validates session identity,
record kind, and nonregressing source epochs, then returns a new frozen state.
There is no separately supplied canonical-state byte field:
`canonical_state_bytes(reduction.state)` is the only state serialization used
by runtime, trace, and replay.

Trace seed binds the complete checksummed-canonical `SESSION_START` record,
not only its payload:

```python
hashlib.sha256(
    b"INCI-TRACE-V1\0"
    + bytes.fromhex(canonical_record_sha256(session_start))
).digest()
```

`append_derived` deterministically copies the parent raw record's session,
source kind/ID/entity, endpoint, channel, typed request-ID value/state,
source/generated/local times, uncertainty, connection epoch, provider
sequence, and retention deadline. It sets the new sequence and parent
sequence, takes type/version/encoding/payload only from `DerivedDraft`, and
fixes `content_type="application/vnd.inci.derived+json"` plus
`payload_transform="derived-canonical-v1"`. No derived path reads a clock or
invents source metadata.

Trace step:

```python
entry = canonical_json_bytes({
    "v": 1,
    "raw_record_sha256": canonical_record_sha256(raw),
    "outputs": [
        {
            "record_sha256": canonical_record_sha256(item),
        }
        for item in derived
    ],
    "state_sha256": hashlib.sha256(
        canonical_state_bytes(state)
    ).hexdigest(),
})
trace = hashlib.sha256(
    b"INCI-TRACE-STEP-V1\0"
    + prior_trace
    + len(entry).to_bytes(8, "big")
    + entry
).digest()
```

`canonical_record_sha256` is exactly:

```python
hashlib.sha256(
    b"INCI-CANONICAL-RECORD-V1\0"
    + len(metadata).to_bytes(8, "big")
    + metadata
    + len(payload).to_bytes(8, "big")
    + payload
).hexdigest()
```

Because canonical metadata includes every record field, the trace binds source
kind/ID/entity, endpoint, channel, typed request-ID value/state, content type, all
timestamps, uncertainty, epoch, provider sequence, parent, encoding,
transform, retention, payload digest, and payload. Tests independently mutate
every metadata field and every derived-record field and require a trace
change.

- [ ] **Step 4: Run reducer tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_reducer -v
```

Expected: PASS.

- [ ] **Step 5: Commit reducer and trace**

```bash
git add tennis_v1/state.py tennis_v1/reducer.py \
  tests/tennis_v1/test_reducer.py
git commit -m "feat: add deterministic Tennis foundation reducer"
```

### Task 4: Enforce durable-before-reduce sequencing

**Files:**

- Create: `tennis_v1/sequencer.py`
- Create: `tests/tennis_v1/test_sequencer.py`

**Interfaces:**

```python
class RuntimePoisoned(RuntimeError): ...
class WrongOwnerThread(RuntimeError): ...


class PersistenceAuthorizer(SessionCaptureAuthorizer, Protocol):
    @property
    def coordinator(self) -> RetentionCoordinator: ...
    def authorize_session(self) -> None: ...
    def authorize_ingest(self, captured: CapturedInput) -> None: ...
    def authorize_raw_persistence(self) -> int:
        """Return the current allowed upper delete-by UTC ns or raise."""
    def authorize_persist(self, captured: CapturedInput) -> int | None:
        """Return provider delete-by nanoseconds; nonprovider returns None."""
    def authorize_transform(self, raw: PersistedEvent) -> None: ...
    def authorize_derived_persist(
        self, raw: PersistedEvent, draft: DerivedDraft
    ) -> None: ...
    def authorize_analysis(self) -> QualificationDecision: ...
    def authorize_close(self) -> None: ...


@dataclass(frozen=True, slots=True, init=False)
class ProviderPersistenceAuthorizer:
    gate: ProviderGate = field(repr=False)
    coordinator: RetentionCoordinator = field(repr=False, compare=False)
    session_manifest: SessionManifest
    bound_decision: QualificationDecision

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("use bind_provider_persistence_authorizer")

    def authorize_session(self) -> None:
        require_decision_matches_session(
            self.gate.require_start(), self.session_manifest)
    def authorize_capture(
        self, authority: CaptureAuthority, captured: CapturedInput
    ) -> None:
        self.coordinator.require_provider_operation()
        require_decision_matches_session(
            self.gate.require_ingest(), self.session_manifest)
        if captured.source_kind is SourceKind.PROVIDER:
            allowed_delete_by = self.authorize_raw_persistence()
            if allowed_delete_by < self.session_manifest.required_retention_until_ns:
                raise ProviderGateError(QualificationReason.RETENTION_TOO_SHORT)
        validate_capture_against_authority(
            authority, captured, self.session_manifest)
        validate_captured_input(captured, self.session_manifest)
    def authorize_ingest(self, captured: CapturedInput) -> None:
        require_decision_matches_session(
            self.gate.require_ingest(), self.session_manifest)
        validate_captured_input(captured, self.session_manifest)
    def authorize_raw_persistence(self) -> int:
        return self.gate.require_raw_persist()
    def authorize_persist(self, captured: CapturedInput) -> int | None:
        if captured.source_kind is SourceKind.PROVIDER:
            allowed_delete_by = self.authorize_raw_persistence()
            if allowed_delete_by < self.session_manifest.required_retention_until_ns:
                raise ProviderGateError(QualificationReason.RETENTION_TOO_SHORT)
            return self.session_manifest.required_retention_until_ns
        return None
    def authorize_transform(self, raw: PersistedEvent) -> None:
        require_decision_matches_session(
            self.gate.require_transform(), self.session_manifest)
    def authorize_derived_persist(
        self, raw: PersistedEvent, draft: DerivedDraft
    ) -> None:
        require_decision_matches_session(
            self.gate.require_derived_persist(), self.session_manifest)
    def authorize_analysis(self) -> QualificationDecision:
        decision = self.gate.require_analysis()
        require_decision_matches_session(decision, self.session_manifest)
        return decision
    def authorize_close(self) -> None:
        require_decision_matches_session(
            self.gate.require_start(), self.session_manifest)


def bind_provider_persistence_authorizer(
    *,
    gate: ProviderGate,
    coordinator: RetentionCoordinator,
    session_manifest: SessionManifest,
) -> ProviderPersistenceAuthorizer: ...


class EventRuntime:
    def __init__(
        self,
        *,
        writer: JournalWriter,
        state: FoundationState,
        persistence_authorizer: PersistenceAuthorizer,
        coordinator: RetentionCoordinator,
    ) -> None: ...

    @property
    def state(self) -> FoundationState: ...
    @property
    def trace_sha256(self) -> str: ...

    def ingest(self, captured: CapturedInput) -> PersistedEvent: ...
    def poll_entitlement(self) -> None: ...
    def close_clean(self, reason: str) -> PersistedEvent: ...
    def close_halted(self, reason: str) -> PersistedEvent: ...
```

- [ ] **Step 1: Write ordering and failure-injection tests**

```python
def test_raw_fsync_finishes_before_reducer_is_called(): ...
def test_write_or_fsync_failure_causes_zero_reducer_calls(): ...
def test_zero_byte_partial_write_and_fsync_failure_poison_runtime(): ...
def test_reducer_failure_after_raw_fsync_writes_sanitized_halt_when_writer_healthy(): ...
def test_derived_validation_failure_halts_but_derived_durability_failure_poisons(): ...
def test_safe_factory_rejection_is_retryable_before_envelope_admission(): ...
def test_forged_returned_capture_is_fatal_halt_not_retryable_validation(): ...
def test_durability_failure_never_reuses_uncertain_sequence(): ...
def test_nonowner_thread_cannot_ingest_or_close(): ...
def test_clean_terminal_is_unique_fsynced_and_final(): ...
def test_halted_terminal_never_passes_exact_replay(): ...
def test_provider_gate_runs_before_append_and_sets_fixed_session_delete_by(): ...
def test_provider_denial_causes_zero_raw_writes_zero_reducer_calls_and_one_halt(): ...
def test_expiry_on_ingest_persist_or_clean_close_forces_halted_terminal(): ...
def test_expiry_after_raw_before_transform_or_derived_write_forces_halt(): ...
def test_idle_expiry_poll_forces_halted_terminal_before_more_work(): ...
def test_runtime_uses_writer_manifest_as_single_session_authority(): ...
def test_provider_authorizer_maps_every_runtime_boundary_to_exact_gate_method(): ...
def test_authorizer_rejects_gate_for_different_session_provider_or_request(): ...
def test_runtime_rejects_coordinator_different_from_authorizer_and_writer_capability(): ...
def test_direct_provider_authorizer_construction_is_unavailable(): ...
def test_every_gate_decision_is_rebound_to_complete_session_manifest(): ...
def test_capture_and_persist_return_same_homogeneous_session_deadline(): ...
def test_require_raw_persist_is_called_with_no_observation_argument(): ...
def test_global_halt_checked_at_capture_append_transform_each_derived_close_and_idle(): ...
def test_coordinator_halt_at_every_boundary_writes_one_witnessed_halt_if_writer_healthy(): ...
def test_current_session_due_or_ambiguous_halt_leaves_unclean_without_further_byte(): ...
def test_healthy_reducer_trace_or_derived_validation_failure_writes_one_halt(): ...
def test_disk_low_prewrite_check_halts_before_raw_or_reducer(): ...
```

The fake writer exposes an `is_durable(seq)` assertion. The fake reducer must
assert that the raw sequence is durable at entry, proving the ordering rather
than checking call lists alone.

`bind_provider_persistence_authorizer` is the only constructor for the
concrete authorizer. It calls `gate.require_start()`, exhaustively validates
that decision against the supplied manifest, stores that exact immutable
object as `bound_decision`, binds the one exclusively locked
coordinator by object identity, then uses a module-private sentinel to
construct the frozen object. The dependency AST test rejects
direct construction/private-sentinel references elsewhere. Every later gate
method returning a decision is revalidated with the same exhaustive helper,
so swapping in a gate for another provider, product, request, entitlement,
manifest, artifact, adapter, or time window cannot authorize this WAL.

- [ ] **Step 2: Run focused sequencer tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_sequencer -v
```

Expected: FAIL because `tennis_v1.sequencer` does not exist.

- [ ] **Step 3: Implement the transaction order exactly**

`ingest` performs:

```python
self._require_owner_and_healthy()
assert self._state.session_id == self._writer.session_manifest.session_id
try:
    self._coordinator.require_provider_operation()
    self._persistence_authorizer.authorize_ingest(captured)
    delete_by_ns = self._persistence_authorizer.authorize_persist(captured)
except ProviderGateError as error:
    self._close_halted_for_gate(error.reason)
    raise
except RetentionGlobalHalt:
    self._close_halted_for_runtime("retention_global_halt")
    raise
if captured.retention_delete_by_ns != delete_by_ns:
    self._close_halted_for_runtime("capture_contract_violation")
    raise CaptureValidationError("retention_deadline_mismatch")
try:
    self._coordinator.require_provider_operation()
    raw = self._writer.append_raw(captured)   # includes fsync
except JournalValidationError:
    self._close_halted_for_runtime("capture_contract_violation")
    raise
except RetentionGlobalHalt:
    self._close_halted_for_runtime("retention_global_halt")
    raise
except DiskLowError:
    self._close_halted_for_runtime("disk_low")
    raise
except JournalDurabilityError:
    self._poisoned = True
    raise
failure_reason = "reducer_exception"
try:
    self._coordinator.require_provider_operation()
    self._persistence_authorizer.authorize_transform(raw)
    reduction = reduce_event(self._state, raw)
    stored_outputs = []
    for draft in reduction.outputs:
        self._coordinator.require_provider_operation()
        self._persistence_authorizer.authorize_derived_persist(raw, draft)
        failure_reason = "derived_validation_failure"
        stored_outputs.append(self._writer.append_derived(raw, draft))
    stored_outputs = tuple(stored_outputs)
    failure_reason = "trace_exception"
    new_trace = next_trace(
        self._trace, raw, stored_outputs, reduction.state)
except ProviderGateError as error:
    self._close_halted_for_gate(error.reason)
    raise
except RetentionGlobalHalt:
    self._close_halted_for_runtime("retention_global_halt")
    raise
except DiskLowError:
    self._close_halted_for_runtime("disk_low")
    raise
except JournalDurabilityError:
    self._poisoned = True
    raise
except BaseException:
    self._close_halted_for_runtime(failure_reason)
    raise
self._trace = new_trace
self._state = reduction.state
return raw
```

Envelope validation may occur before append, but `reduce_event` may not.
Provider authorization occurs before capture and is rechecked before append,
before transformation/reduction, and immediately before every derived write.
The safe capture factory copies the homogeneous session delete-by from the
same bound manifest; the runtime does not mutate an immutable capture. The
concrete provider authorizer calls
`ProviderGate.require_raw_persist()` with no observation argument, proves that
the currently permitted deadline still covers
`session_manifest.required_retention_until_ns`, and returns that fixed session
deadline. The runtime requires exact equality; nonprovider sources require
`None`.
Any writer error after the first possible write, including a zero-byte write,
partial-write exception, or capability `fsync` failure, poisons both writer and runtime
and leaves the uncertain sequence permanently unusable. Only a safe-factory
rejection before a returned/admitted capture is retryable. Any writer
validation failure for a purported safe capture is a fatal contract violation
that writes a witnessed halt while the writer is healthy. After a persisted raw event, a
`JournalDurabilityError` or `writer.poisoned` state forbids every terminal
attempt and leaves the damaged session for diagnostic replay. In contrast, a
reducer exception, trace exception, derived prewrite/validation exception, or
other `BaseException` while the writer is still healthy closes inputs and
appends/`fsync`s exactly one halted terminal with the fixed sanitized stage
reason shown above; no exception type, message, payload, or representation is
journaled. The runtime then remains permanently closed and re-raises the
original exception. Failure while writing that halt converts to normal
uncertain-durability poison and never fabricates success.

`close_clean` calls `authorize_close` immediately before terminal creation.
If the provider entitlement or analysis window is no longer valid while the
writer remains healthy, the runtime writes a halted terminal with reason
`provider_entitlement_expired` and returns/raises a non-clean outcome; it
never writes a clean terminal. Every ingest calls both gate methods, so no
long-running session relies only on startup authorization. The concrete
authorizer maps the two post-raw checks to
`ProviderGate.require_transform()` and
`ProviderGate.require_derived_persist()`.
`EventRuntime.__init__` first requires complete equality among the state
session, writer manifest, authorizer manifest, coordinator, write capability,
and armed marker—not only session ID—and
calls `authorize_session`, whose returned gate decision is exhaustively rebound
to that manifest. A denial writes at most a halted terminal after the already
durable `SESSION_START` and never exposes a usable runtime.
It initializes the trace only with
`initial_trace(writer.session_start)`; callers cannot inject a seed.
`close_clean` computes and passes
`sha256(canonical_state_bytes(self._state)).hexdigest()` as the final-state
witness; callers cannot inject it.
The owner loop calls `poll_entitlement` at least once per bounded ingress
poll interval, including while idle. Capture, the final instant before raw
append, transformation, every derived append, clean/halted close, and each
idle poll first call `coordinator.require_provider_operation` with a fresh
wall time. Clean close additionally calls `coordinator.mark_clean_terminal`
only after the terminal is durable. On a global-halt result, a known-healthy,
not-due, identity-valid current session attempts exactly one strict
`write_halt_control` carrying non-null current
trace/final-state/last-applied witnesses, then closes. If the current session
is itself due, storage-ambiguous, or the halt-control write has any uncertain
outcome, no further byte is attempted and the WAL remains unclean. Thus the
control-only exception never bypasses retention expiry or fabricates a
terminal after durability uncertainty.

- [ ] **Step 4: Run sequencer tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_sequencer -v
```

Expected: PASS.

- [ ] **Step 5: Commit the runtime**

```bash
git add tennis_v1/sequencer.py tests/tennis_v1/test_sequencer.py
git commit -m "feat: sequence durable Tennis inputs before reduction"
```

### Task 5: Add exact replay and crash semantics

**Files:**

- Create: `tennis_v1/replay.py`
- Create: `tests/tennis_v1/test_replay.py`
- Create: `tests/tennis_v1/test_crash_recovery.py`

**Interfaces:**

```python
class ReplayMismatch(str, Enum):
    SESSION_MANIFEST = "session_manifest_mismatch"
    DERIVED_MISSING = "derived_missing"
    DERIVED_EXTRA = "derived_extra"
    DERIVED_ORDER = "derived_order_mismatch"
    DERIVED_RECORD = "derived_record_mismatch"
    STATE = "state_mismatch"
    TRACE = "trace_mismatch"
    TERMINAL_COUNTS = "terminal_counts_mismatch"
    TERMINAL_PROVENANCE = "terminal_provenance_mismatch"


@dataclass(frozen=True, slots=True)
class ReplayResult:
    state: FoundationState
    trace_sha256: str
    raw_count: int
    derived_count: int
    terminal_clean: bool
    wal_valid: bool
    exact_replay: bool
    research_evaluable: Literal[False]
    issue: ScanIssue | ReplayMismatch | None


def replay_exact(
    *,
    expected_session_manifest_sha256: str,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> ReplayResult: ...
def scan_diagnostic_prefix(
    *,
    expected_session_manifest_sha256: str,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> ReplayResult: ...
```

- [ ] **Step 1: Write replay and subprocess-crash tests**

```python
def test_replay_uses_raw_records_only_and_matches_every_derived_witness(): ...
def test_replay_twice_produces_identical_state_outputs_and_trace(): ...
def test_mutated_derived_payload_or_order_fails_at_parent_sequence(): ...
def test_each_manifest_derived_state_trace_and_terminal_mismatch_is_typed(): ...
def test_missing_halted_torn_or_corrupt_terminal_never_passes_exact_replay(): ...
def test_record_after_terminal_and_duplicate_terminal_fail(): ...
def test_raw_arrival_order_wins_over_reversed_source_timestamps(): ...
def test_crash_after_raw_fsync_before_reduce_is_applied_by_diagnostic_replay(): ...
def test_crash_during_frame_write_preserves_only_verified_prefix(): ...
def test_phase_one_never_appends_to_or_resumes_crashed_session(): ...
def test_analysis_denial_opens_and_reads_zero_wal_bytes(): ...
def test_post_expiry_analysis_requires_separate_current_permission(): ...
def test_clean_exact_phase_one_replay_is_still_not_research_evaluable(): ...
def test_session_start_manifest_drives_authorization_and_full_replay_identity(): ...
def test_replay_has_no_path_config_fd_or_arbitrary_authorization_callback(): ...
def test_unrelated_authorizer_or_coordinator_cannot_authorize_session(): ...
def test_expected_manifest_sha_marker_and_binding_checked_before_wal_open(): ...
def test_due_purge_or_purge_failure_happens_before_any_wal_open_or_read(): ...
def test_first_session_start_digest_must_equal_external_expected_digest(): ...
def test_every_replay_byte_range_uses_the_same_coordinator_read_capability(): ...
def test_final_state_digest_mismatch_is_typed_separately_from_trace(): ...
def test_large_replay_streams_without_retaining_all_records(): ...
```

Use a subprocess with synchronization pipes rather than `sleep`: the child
signals immediately after raw `fsync`, the parent terminates it before reducer
entry, and diagnostic replay must see that raw frame.

- [ ] **Step 2: Run focused replay tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_replay \
  tests.tennis_v1.test_crash_recovery -v
```

Expected: FAIL because exact replay is missing.

- [ ] **Step 3: Implement exact comparison**

`replay_exact`:

1. requires the authorizer's coordinator to be the exact supplied coordinator,
   validates the external expected manifest SHA grammar, and requires it to
   equal `session_manifest_sha256(persistence_authorizer.session_manifest)`;
   mismatch sets global halt before any WAL open/read;
2. calls `coordinator.recover_and_purge()`; the coordinator owns the trusted
   clock.
   The coordinator parses the separately durable marker, verifies its exact
   expected-manifest SHA/session/provider/entitlement/artifact/deadline binding,
   and completes due purge before opening or reading any WAL byte. Due deletion,
   overdue state, mismatch, or purge ambiguity/failure returns no read
   capability and globally halts;
3. obtains `analysis_decision =
   persistence_authorizer.authorize_analysis()`, which uses
   `ProviderGate.require_analysis()` and rebinds the decision to the complete
   expected manifest. Denial opens and reads zero WAL bytes;
4. calls `coordinator.issue_read_capability` with the exact bound
   `persistence_authorizer` to obtain an opaque
   `ProviderWalReadCapability`.
   Capability issuance rechecks the armed, not-due marker and exact authorizer
   binding before the coordinator opens the WAL;
5. constructs `JournalReader` only from that capability. All bounded reads go
   back through the coordinator. It decodes the checksummed `SESSION_START`
   first record and requires its exact canonical manifest digest to equal
   `expected_session_manifest_sha256` before requesting any later byte;
6. initializes state and trace from the complete persisted `SESSION_START`
   record;
7. visits records in physical/`ingest_seq` order;
8. applies only raw records;
9. recomputes each raw record's derived drafts;
10. reconstructs each complete expected `PersistedEvent`, including its
    expected physical sequence, and byte-compares exact
    `encode_record(expected)` metadata and payload with the stored derived
    record; any field mismatch is `DERIVED_RECORD`;
11. recomputes the canonical state and trace;
12. compares counts, last-applied raw sequence, every provenance fingerprint,
    homogeneous retention deadline, final-state SHA, and final trace with the
    unique final clean terminal. State and trace mismatches retain their
    separate typed statuses.

`scan_diagnostic_prefix` may return the verified prefix but always sets
`exact_replay=False` for any scan issue. It uses the identical pre-open
purge/marker/authorizer/capability sequence because structural scanning reads
provider bytes. `wal_valid` means the
scanned bytes satisfy the container contract; `exact_replay` means a clean
terminal and all witnesses/state/trace matched; `research_evaluable` is
always false in Phase 1 regardless of those two results. Replay owns no path,
fd, purge operation, or authorization callback; the coordinator is the sole
WAL-byte and retention authority.

- [ ] **Step 4: Run replay and crash tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_replay \
  tests.tennis_v1.test_crash_recovery -v
```

Expected: PASS.

- [ ] **Step 5: Commit replay**

```bash
git add tennis_v1/replay.py tests/tennis_v1/test_replay.py \
  tests/tennis_v1/test_crash_recovery.py
git commit -m "feat: verify exact Tennis WAL replay"
```

### Task 6: Serialize bounded concurrent ingress without dropping inputs

**Files:**

- Create: `tennis_v1/ingress.py`
- Create: `tests/tennis_v1/test_ingress.py`

**Interfaces:**

```python
class IngressClosed(RuntimeError): ...
class IngressBackpressureHalt(RuntimeError): ...
class IngressOwnerUnresponsive(RuntimeError): ...


@dataclass(frozen=True, slots=True)
class IngressItem:
    producer_id: str
    producer_sequence: int
    captured: CapturedInput


@dataclass(frozen=True, slots=True)
class DurableIngressReceipt:
    producer_id: str
    producer_sequence: int
    raw_ingest_seq: int
    raw_record_sha256: str


class BoundedIngress:
    def __init__(
        self,
        *,
        capacity: int,
        producer_timeout_seconds: float,
        receipt_timeout_seconds: float,
    ) -> None: ...

    def enqueue(self, item: IngressItem) -> DurableIngressReceipt:
        """Return only after raw fsync, or raise without acknowledging it."""

    def drain_one(
        self,
        runtime: EventRuntime,
        *,
        timeout_seconds: float,
    ) -> PersistedEvent | None:
        """Owner-thread-only queue drain into the sole sequencer."""

    def close_inputs(self) -> None: ...
    @property
    def halt_reason(self) -> str | None: ...
```

- [ ] **Step 1: Write concurrent no-drop and halt tests**

```python
def test_many_barrier_released_producers_create_one_gap_free_durable_order(): ...
def test_each_enqueued_producer_item_is_persisted_exactly_once(): ...
def test_enqueue_returns_only_after_matching_raw_fsync_receipt(): ...
def test_crash_before_drain_returns_no_durable_receipt(): ...
def test_crash_after_raw_fsync_before_receipt_is_explicit_ambiguous_nonexact_session(): ...
def test_queue_full_blocks_only_to_bounded_timeout_then_requests_global_halt(): ...
def test_receipt_wait_is_bounded_and_owner_stall_requests_global_halt(): ...
def test_blocked_put_cannot_linearize_after_close_or_first_halt(): ...
def test_backpressure_timeout_never_drops_and_continues_nothing(): ...
def test_backpressure_counts_provisional_durable_rejected_and_terminal_exactly(): ...
def test_only_owner_thread_may_drain_or_close_runtime(): ...
def test_close_inputs_rejects_late_enqueue_and_drains_existing_items(): ...
```

Use a `threading.Barrier` to release multiple producers together. Assert every
`(producer_id, producer_sequence)` appears exactly once, every physical WAL
sequence including derived records remains contiguous, and replay follows
queue arrival order rather than provider timestamps.

- [ ] **Step 2: Run focused ingress tests**

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_ingress -v
```

Expected: FAIL because `tennis_v1.ingress` does not exist.

- [ ] **Step 3: Implement one bounded multi-producer queue**

Use `queue.Queue(maxsize=capacity)`. A queue insertion is **provisional**, not
accepted. Each item owns an internal completion event/result slot.
One admission lock linearizes the closed/halt check and the sole bounded
`Queue.put`: a producer holds it across that put, and a timeout records the
first halt/closed state before releasing it. Therefore no already-blocked put
can succeed after close/halt. `enqueue` then waits only
`receipt_timeout_seconds` for the owner to return the matching fsynced raw
receipt or terminal error. It returns—and the producer may
acknowledge/checkpoint upstream—only after `EventRuntime.ingest` has returned
the durable raw sequence. A receipt timeout atomically records
`owner_unresponsive`, closes new admission, wakes the owner, and raises
`IngressOwnerUnresponsive`; the item remains merely provisional and the
session cannot close clean. There is no drop, overwrite, second processing of
one queue node, or direct producer access to the writer. The contract does not
claim cross-process exactly-once delivery: a crash after raw `fsync` but before
receipt publication is intentionally an ambiguous, non-exact prior session
and upstream retry belongs to a new session.

A queue-slot timeout atomically records the first immutable backpressure halt,
rejects that item, closes new insertions, and wakes the owner. For a pure
backpressure halt, the owner drains every already provisional item to a
durable receipt in FIFO order, then writes exactly one halted terminal; no
provisional item remains. If entitlement denial, process crash, or writer
poisoning prevents that drain, affected waiters receive/observe failure, no
durable receipt is issued, and the session remains unclean when a trustworthy
terminal cannot be written. Such items were never acknowledged as accepted.
The terminal/count tests distinguish provisional, durable/accepted, rejected,
and terminal counts exactly.

The owner is the only caller of `EventRuntime.ingest`; it never writes a clean
terminal after any backpressure or owner-liveness failure. On
`owner_unresponsive`, a live owner drains already provisional items and writes
one halted terminal; if the owner has actually died, waiters fail within their
deadline and the WAL remains unclean rather than pretending that a terminal
was durable.
`drain_one` calls `runtime.poll_entitlement()` before waiting and again after
every queue timeout, so a quiet queue cannot postpone provider expiry.

- [ ] **Step 4: Run ingress and replay tests**

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_ingress \
  tests.tennis_v1.test_replay -v
```

Expected: PASS.

- [ ] **Step 5: Commit ingress**

```bash
git add tennis_v1/ingress.py tests/tennis_v1/test_ingress.py
git commit -m "feat: serialize bounded concurrent Tennis ingress"
```

### Task 7: Isolate the dashboard and enforce the dependency boundary

**Files:**

- Create: `tennis_v1/mailbox.py`
- Create: `tests/tennis_v1/test_mailbox.py`
- Create: `tests/tennis_v1/test_dependency_boundary.py`
- Modify: `docs/tennis_v1/README.md`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    session_id: str
    last_applied_raw_seq: int
    raw_count: int
    derived_count: int
    trace_sha256: str


class LatestSnapshotMailbox:
    def publish(self, snapshot: DashboardSnapshot) -> None: ...
    def take(self, *, timeout: float | None = None) -> DashboardSnapshot: ...
```

- [ ] **Step 1: Add overwrite and boundary tests**

```python
def test_publish_never_waits_for_slow_consumer_and_keeps_latest(): ...
def test_consumer_failure_does_not_touch_writer_runtime_or_trace(): ...
def test_snapshot_is_frozen_and_nested_mutability_is_unrepresentable(): ...
def test_single_publisher_is_enforced_and_replacement_is_race_safe(): ...
def test_tennis_package_imports_no_legacy_runtime_or_network_library(): ...
def test_tennis_package_contains_no_order_mutation_transport(): ...
def test_existing_v6_modules_do_not_import_tennis_v1(): ...
def test_adversarial_indirect_network_and_order_ast_fixtures_are_rejected(): ...
def test_os_process_dynamic_import_eval_and_exec_escape_hatches_are_rejected(): ...
```

The dependency test parses every Python file under `tennis_v1/` with `ast`.
Package imports are fail-closed: only relative `tennis_v1` imports and this
reviewed standard-library root allowlist are accepted:

```python
ALLOWED_STDLIB_IMPORTS = {
    "__future__", "collections", "dataclasses", "datetime", "enum", "fcntl",
    "hashlib", "json", "os", "pathlib", "queue", "re", "stat", "struct",
    "threading", "time", "typing", "uuid",
}
```

Everything else is denied, including legacy modules, provider/exchange SDKs,
`socket`, `http.client`, `urllib`, `requests`, `httpx`, `aiohttp`, and
`websockets`. A second AST/string policy rejects transport or mutation
definitions/calls named `post`, `put`, `patch`, `delete`, `request`,
`submit`, `submit_order`, `place_order`, `create_order`, `cancel_order`, or
`amend_order`; a `request` call with a nonliteral method; literal HTTP methods
other than `GET`; and endpoint strings containing `/orders`,
`/portfolio/orders`, or mutation URLs. Adversarial temporary fixtures prove
each indirect form is caught, including aliases and attribute calls.
Because `os` is needed by the WAL but could otherwise escape the import
allowlist, the policy also rejects `os.system`, `os.popen`, every
`os.spawn*`/`os.exec*` call, dynamic `__import__`, `eval`, `exec`, and
compile-and-execute patterns. Adversarial fixtures cover aliases, `getattr`,
constant concatenation, and environment-supplied command names; static
inspection must not claim a no-network/no-mutation boundary while arbitrary
process execution remains reachable.
The sole `.put` call exception is the exact
`self._queue.put(item, timeout=...)` queue operation inside
`tennis_v1/ingress.py`; the AST test matches its file, receiver, positional
shape, and keyword set. `put_nowait` is allowed only for the size-one mailbox.
Definitions named `put` and all other `.put` receivers remain denied.

- [ ] **Step 2: Run focused tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_mailbox \
  tests.tennis_v1.test_dependency_boundary -v
```

Expected: FAIL because `tennis_v1.mailbox` does not exist.

- [ ] **Step 3: Implement a size-one nonblocking mailbox**

Use `queue.Queue(maxsize=1)` plus a short mailbox-only lock around the
replace-latest operation. The first publisher thread becomes the sole
publisher; another publisher is rejected. The exact frozen
`DashboardSnapshot` has primitives only, so a mutable snapshot is
unrepresentable. `publish` replaces one stale value atomically and never calls
the dashboard or holds a WAL/reducer lock.

Update `docs/tennis_v1/README.md` with:

- the Tennis v1 research-only boundary;
- the new WAL as canonical Tennis input evidence;
- why v6 CSV/replay code is intentionally not imported;
- diagnostic scanning versus exact replay versus later research evaluability;
- no live/demo/provider-network runtime exists in Phase 1.

- [ ] **Step 4: Run all new and legacy tests**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest discover -s tests/tennis_v1 -p 'test_*.py' -v
"$INCI_TENNIS_PYTHON" tests.py
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_legacy_baseline -v
```

Expected: all Tennis foundation tests pass and legacy reports
`ALL TESTS PASS (200 tests)`.

- [ ] **Step 5: Commit the isolation boundary**

```bash
git add tennis_v1/mailbox.py tests/tennis_v1/test_mailbox.py \
  tests/tennis_v1/test_dependency_boundary.py docs/tennis_v1/README.md
git commit -m "test: enforce Tennis research-only dependency boundary"
```

## Plan Verification

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest discover -s tests/tennis_v1 -p 'test_*.py' -v
"$INCI_TENNIS_PYTHON" tests.py
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_legacy_baseline -v
"$INCI_TENNIS_PYTHON" -m compileall -q tennis_v1 tests/tennis_v1
git diff --check
```

Then run the crash tests three times to detect timing assumptions:

```bash
for attempt in 1 2 3; do
  "$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_crash_recovery -v
done
```

Finally inspect the package boundary:

```bash
rg -n 'requests|httpx|create_order|cancel_order|amend_order|/orders' tennis_v1
```

Expected: no network or order mutation implementation. Mentions in enum values,
negative checks, or documentation are reviewed manually.
