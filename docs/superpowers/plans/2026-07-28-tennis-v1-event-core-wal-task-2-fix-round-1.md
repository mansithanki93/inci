# Tennis v1 Event Core WAL Task 2 Fix Round 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:test-driven-development for every production change and
> superpowers:verification-before-completion before reporting success.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the Task 2 capability-claim ordering, make WAL bootstrap
capacity-safe, bound writer/reader parent state to O(1), and prove real
write/fsync failures poison durability.

**Architecture:** Retention owns all filesystem capacity and complete-write
boundaries. It checks a conservative bootstrap margin before returning a
capability and recognizes the exact sequence-1 `SESSION_START` frame as the
only capacity-exempt EVT1 frame. WAL claims the exact capability/manifest
before interpreting the supplied manifest and retains only the most recent
RAW; both writer and reader enforce latest-RAW derived parenting.

**Tech Stack:** Python 3.14, `unittest`, immutable Tennis v1 event contracts,
opaque retention capabilities, canonical JSON, SHA-256 framed WAL.

## Global Constraints

- Tests must be written and observed RED before production edits.
- Preserve the existing uncommitted baseline and all unrelated user changes.
- Do not stage, commit, or modify protected legacy v6 files.
- Use only capability-owned WAL I/O; `wal.py` may not open, stat, map, seek,
  purge, or unlink WAL paths.
- Actual write/fsync uncertainty permanently poisons the writer; only a
  proven prewrite capacity denial leaves it healthy.
- Finish with the required focused, integration, all-Tennis, legacy,
  compile/static, and whitespace verification commands.

---

### Task 1: Retention-owned bootstrap capacity and real I/O failure boundaries

**Files:**

- Modify: `tennis_v1/retention.py`
- Test: `tests/tennis_v1/test_retention.py`

**Interfaces:**

- Consumes: `RetentionCoordinator.arm_before_wal(...)`,
  `ProviderWalWriteCapability.write_all(frame)`.
- Produces: an arm-time capacity check using
  `MIN_FREE_BYTES + RESERVE_BYTES + MAX_FRAME_BYTES`, and exact recognition of
  the armed manifest's sequence-1 `SESSION_START` frame.

- [ ] **Step 1: Write failing bootstrap-capacity tests**

Add a test that patches `os.fstatvfs` at arm time to the conservative
threshold and asserts `arm_before_wal` raises
`RetentionPrewriteCapacityError` before returning a capability, while the WAL
does not exist or remains zero bytes. Add a second test that arms with
adequate space, then patches later `fstatvfs` to fail if called while writing
the exact sequence-1 `session_start_frame(manifest)` and asserts the frame is
durable.

- [ ] **Step 2: Run the focused tests and record the expected RED**

```bash
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_retention.RetentionTests.test_bootstrap_capacity_fails_during_arm_before_capability_or_wal_start \
  tests.tennis_v1.test_retention.RetentionTests.test_exact_session_start_bypasses_later_frame_capacity_check \
  -v
```

- [ ] **Step 3: Implement the minimum retention change**

Check capacity before WAL creation/capability return with the worst-case frame
margin. During writes, parse and validate only the exact sequence-1
`SESSION_START` bound to the armed manifest as bootstrap; every other
nonterminal EVT1 frame keeps the ordinary exact-length prewrite threshold.

- [ ] **Step 4: Write and observe real write-loop/fsync RED tests**

Exercise short/zero write and `os.fsync` failures through retention's real
complete-write boundary. Assert the capability is revoked/global halt is
latched and no subsequent retry or terminal is accepted.

- [ ] **Step 5: Implement only boundary handling required by those tests**

Retain the existing uncertain-durability path for all actual I/O failures;
do not translate them into capacity denials.

- [ ] **Step 6: Run focused retention tests**

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_retention -v
```

---

### Task 2: Claim-first writer bootstrap and latest-RAW O(1) contract

**Files:**

- Modify: `tennis_v1/wal.py`
- Test: `tests/tennis_v1/test_wal.py`

**Interfaces:**

- Consumes: `_claim_provider_wal_writer(write_capability=...,
  session_manifest=...)`.
- Produces: claim-before-manifest-validation ordering; `_latest_raw` O(1)
  writer state; latest-RAW-only derived parent validation in writer and reader.

- [ ] **Step 1: Write and observe claim-order RED tests**

For each exact armed manifest mutation that causes canonical/control
validation to fail, call `JournalWriter.create`, assert zero WAL bytes,
capability consumption, and process-global retention halt. Preserve the test
that a non-capability fails before crossing retention dependencies.

- [ ] **Step 2: Move the claim before supplied-manifest inspection**

After only the exact capability type check, call the retention claim helper.
Then require/encode the exact manifest. Any post-claim validation failure
already owns a consumed capability and must not write bytes.

- [ ] **Step 3: Write and observe writer-memory/latest-parent RED tests**

Append many RAW records with large payloads and assert writer-owned retained
memory does not grow with record count. Append RAW A, RAW B, then attempt a
derived record for A and assert a no-write `JournalValidationError`; prove
multiple derived records for B remain valid. Forge a WAL with an older raw
parent and assert reader corruption.

- [ ] **Step 4: Replace retained raw history with latest-RAW state**

Store only the most recent immutable RAW record (or its fixed-size identity
witness). `append_derived` accepts only exact equality with that latest RAW.
The reader holds only `last_raw_seq` and rejects any other parent without
rereading earlier frames.

- [ ] **Step 5: Narrow terminal witness validation**

Clean terminals require the latest raw sequence. Halted terminals accept
exact integer `0` or a sequence not greater than the latest raw; final replay
owns the semantic truth of the halted witness.

- [ ] **Step 6: Run focused WAL tests**

```bash
"$INCI_TENNIS_PYTHON" -m unittest tests.tennis_v1.test_wal -v
```

---

### Task 3: Rulings, verification, and report

**Files:**

- Modify:
  `.superpowers/sdd/2026-07-27-tennis-v1-event-core-wal/task-2-controller-rulings.md`
- Create:
  `.superpowers/sdd/2026-07-27-tennis-v1-event-core-wal/task-2-fix-round-1-report.md`

- [ ] **Step 1: Record both controller resolutions**

Document indivisible claim/prefix/`SESSION_START` bootstrap with arm-time
worst-case capacity, and the latest-RAW-only derived-parent narrowing.

- [ ] **Step 2: Run the complete verification matrix**

Run focused WAL, full retention, retention/entitlement/event integration, all
Tennis v1 tests, the protected-legacy guard, `tests.py`, compileall, static
authority/forbidden-API checks, and `git diff --check`.

- [ ] **Step 3: Write the fix-round report**

Record exact RED evidence, implementation summary, every command/result,
residual concerns, staging status, and SHA-256 hashes for all changed files.
